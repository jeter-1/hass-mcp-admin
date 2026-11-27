"""HAMCP-447 (2.11.0 Lane B2): native dashboard search for ``ha_search``.

Approved by Josh as relayed by the architect, 2026-11-27: dashboards are
searched natively, YAML dashboards are skipped with a closed code, and the
default dashboard is read and matched (d1, disclosed). The websocket client
admits ``lovelace/config`` in batches in exactly two shapes, ``{type}`` and
``{type, url_path}`` (w1), and Row 25 admits a dashboard record's
``url_path`` and ``title`` and a flow helper's ``entry_id``.

Fixtures are synthetic and model Home Assistant Core 2026.11.3
``lovelace/dashboards/list`` rows and batched ``lovelace/config`` outcomes.
No test reaches Home Assistant.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from types import MappingProxyType
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

# Imported as modules, so their test cases are not collected twice.
from tests import test_hamcp481_core_reads as cr  # noqa: E402
from tests import test_hamcp481_native_search_engine as e3  # noqa: E402
from ha_mcp_engineering.clients import websocket as ws  # noqa: E402
from ha_mcp_engineering.errors import HomeAssistantApiError, HomeAssistantTimeoutError  # noqa: E402
from ha_mcp_engineering.governance.dashboard_metadata import URL_PATH_PATTERN  # noqa: E402
from ha_mcp_engineering.ha_core_readmission import routes  # noqa: E402
from ha_mcp_engineering.providers import upstream_read_gateway as gateway  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2_keys as k  # noqa: E402
from ha_mcp_engineering.sanitization import TRUNCATION_SUFFIX  # noqa: E402
from ha_mcp_engineering.search import core_contracts as c  # noqa: E402
from ha_mcp_engineering.search import dashboards, reply, scoring, subcalls  # noqa: E402
from ha_mcp_engineering.search.core_reads import INVALID, ReadResult  # noqa: E402
from ha_mcp_engineering.search.reply import Reasons  # noqa: E402

LIST = "lovelace/dashboards/list"
CONFIG = "lovelace/config"
BAD_PATHS = ("Synthetic-Board", "", "a/b", "a b", "a" * 257, "synthetic\n", "../x", 7, None, True, ["x"])
FLAGS = {"score": 100, "match_in_config": True, "match_in_references": False}
SERVE = object()


def board(mode="storage", title="Synthetic"):
    """A projected ``lovelace/dashboards/list`` row."""

    return {"mode": mode, "title": title}


def listing(rows, *, status=c.COMPLETE, dropped=0):
    return ReadResult("dashboards", status, None, MappingProxyType(dict(rows)),
                      ("malformed_rows",) if dropped else (), dropped)


def counts(reasons):
    """The dashboard reasons by code; every one is ``not_searched``."""

    entries = reasons.entries()
    assert all((entry["reason"], entry["search_type"]) == ("not_searched", "dashboard")
               for entry in entries), entries
    return {entry["code"]: entry["count"] for entry in entries}


class ClientShapeTests(unittest.TestCase):
    def test_lovelace_config_batches_in_exactly_two_shapes(self):
        self.assertEqual(ws._BATCH_COMMAND_KEYS[CONFIG],
                         (frozenset({"type"}), frozenset({"type", "url_path"})))
        for payload in ({"type": CONFIG}, {"type": CONFIG, "url_path": "synthetic-board"},
                        {"type": CONFIG, "url_path": "a" * 256}, {"type": CONFIG, "url_path": "default"}):
            with self.subTest(payload=payload):
                self.assertTrue(ws.batch_command_allowed(payload))

    def test_force_other_keys_bad_paths_and_other_commands_are_refused(self):
        refused = [
            {"type": CONFIG, "force": True}, {"type": CONFIG, "force": False},
            {"type": CONFIG, "url_path": "synthetic-board", "force": False},
            {"type": CONFIG, "id": 1}, {"type": CONFIG, "config": {}},
            {"type": CONFIG, "dashboard": "synthetic-board"}, {"url_path": "synthetic-board"},
            *({"type": CONFIG, "url_path": bad} for bad in BAD_PATHS),
            {"type": LIST}, {"type": "lovelace/config/save", "config": {}},
            {"type": "lovelace/config/delete"}, {"type": "lovelace/info"},
            None, CONFIG, [CONFIG], ({"type": CONFIG},),
        ]
        for payload in refused:
            with self.subTest(payload=repr(payload)[:80]):
                self.assertFalse(ws.batch_command_allowed(payload))

    def test_the_contract_pattern_is_the_clients(self):
        for sample in ("synthetic-board", "a" * 256, "a" * 257, "Synthetic", "", "x\n", "a_b-c",
                       "default", "lovelace", "a.b", "é", "0"):
            with self.subTest(sample=sample[:20]):
                expected = URL_PATH_PATTERN.fullmatch(sample) is not None
                self.assertEqual(c.URL_PATH.fullmatch(sample) is not None, expected)
                self.assertEqual(ws.batch_command_allowed({"type": CONFIG, "url_path": sample}), expected)


class ConfigReadTests(cr.Base):
    def batches(self):
        return [call[1] for call in self.calls if call[0] == "batch"]

    def serve(self, outcomes):
        """``lovelace/config`` outcomes by ``url_path`` (``None``: the default)."""

        self.values[CONFIG] = lambda payloads: [copy.deepcopy(outcomes[payload.get("url_path")])
                                                for payload in payloads]

    async def test_configurations_are_read_in_chunks_scored_and_dropped(self):
        targets = [None, *(f"board-{index}" for index in range(9))]
        self.serve({target: (True, {"views": [{"title": f"view {n}"}] * (n + 1)})
                    for n, target in enumerate(targets)})
        measured = []

        def measure(body):
            measured.append(body)
            return len(body["views"])

        result = await self.session("dashboard_config").dashboard_configs(targets, measure)
        self.assertEqual((result.read, result.status, result.partial), ("dashboard_config", c.COMPLETE, ()))
        self.assertEqual(dict(result.value), {target: n + 1 for n, target in enumerate(targets)})
        self.assertEqual(dict(result.items), dict.fromkeys(targets, "read"))
        batches = self.batches()
        self.assertEqual([len(batch) for batch in batches], [c.CONFIG_CHUNK, 2])
        self.assertEqual(batches[0][:2], [{"type": CONFIG}, {"type": CONFIG, "url_path": "board-0"}])
        self.assertEqual(len(measured), len(targets))
        # Only the scores leave the read.
        self.assertNotIn("view", repr(result))

    async def test_item_outcomes(self):
        outcomes = {None: (True, {"views": []}), "found": (True, {"views": [{}]}),
                    "missing": (False, "config_not_found"), "broken": (False, "unknown_error"),
                    "listed": (True, ["not", "a", "mapping"]), "short": (True,), "bare": "x",
                    "unsure": (None, "config_not_found")}
        self.serve(outcomes)
        result = await self.session("dashboard_config").dashboard_configs(
            list(outcomes), lambda body: len(body["views"]))
        self.assertEqual((result.status, result.partial), (c.PARTIAL, ("item_failures",)))
        self.assertEqual(dict(result.items), {
            None: "read", "found": "read", "missing": "not_found", "broken": "failed",
            "listed": "failed", "short": "failed", "bare": "failed", "unsure": "failed"})
        self.assertEqual(dict(result.value), {None: 0, "found": 1})

    async def test_a_score_that_is_not_an_int_fails_the_item(self):
        session = self.session("dashboard_config")
        self.serve({"synthetic-board": (True, {"name": "a"}), "other-board": (True, {"name": "b"})})
        for score in ("100", 1.0, None, True):
            with self.subTest(score=score):
                result = await session.dashboard_configs(
                    ["synthetic-board", "other-board"],
                    lambda body, score=score: score if body["name"] == "a" else 5)
                self.assertEqual((result.status, result.partial), (c.PARTIAL, ("item_failures",)))
                self.assertEqual(dict(result.items), {"synthetic-board": "failed", "other-board": "read"})
                self.assertEqual(dict(result.value), {"other-board": 5})

    async def test_every_item_failing_is_unavailable(self):
        session = self.session("dashboard_config")
        self.values[CONFIG] = lambda payloads: [(False, "unknown_error") for _ in payloads]
        result = await session.dashboard_configs([None, "synthetic-board"], len)
        self.assertEqual((result.status, result.reason), (c.UNAVAILABLE, "command_failed"))
        self.values[CONFIG] = lambda payloads: [(True, {})]
        result = await session.dashboard_configs([None, "synthetic-board"], len)
        self.assertEqual((result.status, result.reason), (c.UNAVAILABLE, "invalid_response"))
        for error, reason in ((HomeAssistantTimeoutError(), "timeout"),
                              (HomeAssistantApiError(), "command_failed")):
            self.values[CONFIG] = error
            result = await session.dashboard_configs([None], len)
            self.assertEqual((result.status, result.reason), (c.UNAVAILABLE, reason))

    async def test_a_failed_chunk_fails_only_its_targets(self):
        session = self.session("dashboard_config")
        replies = iter([HomeAssistantTimeoutError(), None])

        def serve(payloads):
            outcome = next(replies)
            if outcome is not None:
                raise outcome
            return [(True, {}) for _ in payloads]

        self.values[CONFIG] = serve
        targets = [None, *(f"board-{index}" for index in range(c.CONFIG_CHUNK))]
        result = await session.dashboard_configs(targets, len)
        self.assertEqual((result.status, result.partial), (c.PARTIAL, ("item_failures",)))
        self.assertEqual(dict(result.items), {**dict.fromkeys(targets[:c.CONFIG_CHUNK], "failed"),
                                              targets[-1]: "read"})

    async def test_no_chunk_starts_at_the_deadline(self):
        session = self.session("dashboard_config")
        targets = [None, *(f"board-{index}" for index in range(2 * c.CONFIG_CHUNK))]
        clock = iter([0.0, 5.0, 10.0])
        result = await session.dashboard_configs(targets, len, deadline=10.0, monotonic=lambda: next(clock))
        self.assertEqual([len(batch) for batch in self.batches()], [c.CONFIG_CHUNK, c.CONFIG_CHUNK])
        self.assertEqual((result.status, result.partial), (c.PARTIAL, ("item_failures",)))
        self.assertEqual(dict(result.items), {**dict.fromkeys(targets[:-1], "read"), targets[-1]: "failed"})
        self.calls.clear()
        result = await session.dashboard_configs([None, "synthetic-board"], len, deadline=1.0,
                                                 monotonic=lambda: 2.0)
        self.assertEqual((result.status, result.reason), (c.UNAVAILABLE, "timeout"))
        self.assertEqual(self.batches(), [])

    async def test_bad_arguments_are_refused_before_dispatch(self):
        session = self.session("dashboard_config")
        many = [f"board-{index}" for index in range(c.MAX_DASHBOARDS + 1)]
        clock = lambda: 0.0  # noqa: E731
        cases = (
            ("synthetic-board", len, {}), ({"synthetic-board"}, len, {}), (None, len, {}),
            (["synthetic-board", "synthetic-board"], len, {}), ([None, None], len, {}),
            ([None, *many], len, {}), (["Synthetic-Board"], len, {}), ([7], len, {}),
            ([["synthetic-board"]], len, {}), ([True], len, {}), ([{"url_path": "x"}], len, {}),
            (["synthetic-board"], None, {}), (["synthetic-board"], "len", {}),
            (["synthetic-board"], len, {"deadline": 1.0}),
            (["synthetic-board"], len, {"deadline": 1.0, "monotonic": 0.0}),
            (["synthetic-board"], len, {"deadline": "1", "monotonic": clock}),
            (["synthetic-board"], len, {"deadline": True, "monotonic": clock}),
        )
        for targets, measure, options in cases:
            with self.subTest(targets=repr(targets)[:40], options=sorted(options)):
                with self.assertRaises(c.CoreReadError) as error:
                    await session.dashboard_configs(targets, measure, **options)
                self.assertRefused("invalid_arguments", error)
        self.assertEqual(self.calls, [])

    async def test_the_bound_and_an_empty_list(self):
        session = self.session("dashboard_config")
        targets = [None, *(f"board-{index}" for index in range(c.MAX_DASHBOARDS))]
        result = await session.dashboard_configs(targets, len)
        self.assertEqual(result.status, c.COMPLETE)
        self.assertEqual(len(self.batches()), -(-len(targets) // c.CONFIG_CHUNK))
        self.calls.clear()
        result = await session.dashboard_configs([], len)
        self.assertEqual((result.status, dict(result.value), dict(result.items)), (c.COMPLETE, {}, {}))
        self.assertEqual(self.calls, [])

    async def test_the_read_must_be_declared(self):
        with self.assertRaises(c.CoreReadError) as error:
            await self.session("states").dashboard_configs([None], len)
        self.assertRefused("invalid_arguments", error)
        self.assertEqual(self.calls, [])

    async def test_without_the_dashboard_profile_no_dashboard_read_is_held(self):
        self.core.unavailable = {c.DASHBOARD}
        for reads in (("dashboards",), ("dashboard_config",), ("states", "dashboards", "dashboard_config")):
            with self.subTest(reads=reads), self.assertRaises(c.CoreReadError) as error:
                self.session(*reads)
            self.assertRefused("authority_unavailable", error)
        self.session("states")
        self.assertNotIn(c.DASHBOARD, self.core.route_calls[-1])
        self.assertEqual(self.calls, [])

    async def test_a_profile_lost_after_the_session_starts_refuses_the_read(self):
        session = self.session("dashboard_config")
        self.core.unavailable = {c.DASHBOARD}
        with self.assertRaises(c.CoreReadError) as error:
            await session.dashboard_configs([None, "synthetic-board"], len)
        self.assertRefused("authority_unavailable", error)
        self.assertEqual(self.calls, [])


class PlanTests(unittest.TestCase):
    def plan(self, rows, **options):
        reasons = Reasons()
        return dashboards.plan(listing(rows, **options), reasons), counts(reasons)

    def test_read_order_and_titles(self):
        targets, reasons = self.plan({"synthetic-board": board(title="Synthetic"),
                                      "untitled-board": board(title=""),
                                      "yaml-board": board(mode="yaml", title="Yaml")})
        self.assertEqual(targets, [(None, "Default Dashboard"), ("synthetic-board", "Synthetic"),
                                   ("untitled-board", "untitled-board")])
        self.assertEqual(reasons, {"yaml_dashboards_skipped": 1})
        self.assertEqual(self.plan({}), ([(None, "Default Dashboard")], {}))

    def test_yaml_dashboards_are_never_read(self):
        targets, reasons = self.plan({f"yaml-{index}": board(mode="yaml") for index in range(3)})
        self.assertEqual(targets, [(None, "Default Dashboard")])
        self.assertEqual(reasons, {"yaml_dashboards_skipped": 3})

    def test_a_listed_lovelace_dashboard_takes_the_defaults_place(self):
        # Core serves a listed ``lovelace`` dashboard for the default read.
        targets, reasons = self.plan({"lovelace": board(mode="yaml"), "synthetic-board": board()})
        self.assertEqual(targets, [("synthetic-board", "Synthetic")])
        self.assertEqual(reasons, {"yaml_dashboards_skipped": 1})
        targets, reasons = self.plan({"lovelace": board(title="Home"), "default": board(title="Other")})
        self.assertEqual(targets, [("lovelace", "Home"), ("default", "Other")])
        self.assertEqual(reasons, {})

    def test_a_listed_default_path_is_not_read_beside_the_default(self):
        targets, reasons = self.plan({"default": board(title="Clash"), "synthetic-board": board()})
        self.assertEqual(targets, [(None, "Default Dashboard"), ("synthetic-board", "Synthetic")])
        self.assertEqual(reasons, {"read_failed": 1})

    def test_a_dropped_row_leaves_the_default_unread_and_the_count_unknown(self):
        for rows in ({"synthetic-board": board()}, {"synthetic-board": board(), "auto-board": board(mode="auto")}):
            with self.subTest(rows=sorted(rows)):
                targets, reasons = self.plan(rows, status=c.PARTIAL, dropped=1)
                self.assertEqual(targets, [("synthetic-board", "Synthetic")])
                self.assertEqual(reasons, {"read_failed": None})

    def test_unusable_rows_are_counted(self):
        rows = {"auto-board": board(mode="auto"), "none-board": board(mode=None),
                "drift-board": board(mode=INVALID), "empty-row": {}, "invalid-title": board(title=INVALID),
                "none-title": board(title=None), "int-title": board(title=7), "Bad-Path": board(),
                "a/b": board(), "a" * 257: board(), 7: board(), "good-board": board()}
        targets, reasons = self.plan(rows)
        self.assertEqual(targets, [(None, "Default Dashboard"), ("good-board", "Synthetic")])
        self.assertEqual(reasons, {"read_failed": len(rows) - 1})

    def test_the_dashboard_bound(self):
        rows = {f"board-{index:03d}": board() for index in range(c.MAX_DASHBOARDS + 3)}
        targets, reasons = self.plan(rows)
        self.assertEqual(len(targets), c.MAX_DASHBOARDS + 1)
        self.assertEqual(targets[-1], (f"board-{c.MAX_DASHBOARDS - 1:03d}", "Synthetic"))
        self.assertEqual(reasons, {"read_failed": 3})

    def test_an_unreadable_list_reads_nothing(self):
        for result in (ReadResult("dashboards", c.UNAVAILABLE, "timeout"),
                       ReadResult("dashboards", c.COMPLETE, None, None),
                       ReadResult("dashboards", c.COMPLETE, None, [("synthetic-board", board())]),
                       None, {"synthetic-board": board()}):
            with self.subTest(result=repr(result)[:60]):
                reasons = Reasons()
                self.assertEqual(dashboards.plan(result, reasons), [])
                self.assertEqual(counts(reasons), {"read_failed": None})


class FakeReader:
    """A reader whose configurations are bodies, ``"not_found"`` or ``"failed"``."""

    def __init__(self, bodies, result=SERVE):
        self.bodies, self.result, self.calls = bodies, result, []

    async def dashboard_configs(self, targets, measure, *, deadline=None, monotonic=None):
        self.calls.append((list(targets), deadline))
        if self.result is not SERVE:
            return self.result
        scores, items = {}, {}
        for target in targets:
            outcome = self.bodies.get(target, "not_found")
            if isinstance(outcome, dict):
                items[target], scores[target] = "read", measure(copy.deepcopy(outcome))
            else:
                items[target] = outcome
        failed = "failed" in items.values()
        return ReadResult("dashboard_config", c.PARTIAL if failed else c.COMPLETE, None,
                          MappingProxyType(scores), ("item_failures",) if failed else (), 0,
                          MappingProxyType(items))


class SearchTests(unittest.IsolatedAsyncioTestCase):
    ROWS = {"synthetic-board": board(title="Synthetic"), "other-board": board(title="Other"),
            "missing-board": board(title="Missing"), "broken-board": board(title="Broken"),
            "yaml-board": board(mode="yaml", title="Yaml")}
    BODIES = {None: {"views": [{"title": "Kitchen"}]},
              "synthetic-board": {"views": [{"cards": [{"entity": "light.kitchen"}]}]},
              "other-board": {"views": [{"title": "Garden"}]}, "broken-board": "failed"}
    READ = [None, "synthetic-board", "other-board", "missing-board", "broken-board"]

    async def search(self, query="kitchen", exact=True, rows=None, reader=None):
        reasons = Reasons()
        reader = reader or FakeReader(self.BODIES)
        records = await dashboards.search_dashboards(query, exact, listing(rows or self.ROWS), reader,
                                                     reasons, monotonic=lambda: 100.0)
        return records, counts(reasons), reader

    async def test_matches_are_records_without_bodies(self):
        records, reasons, reader = await self.search()
        self.assertEqual(records, [({"url_path": "default", "title": "Default Dashboard", **FLAGS}, True),
                                   ({"url_path": "synthetic-board", "title": "Synthetic", **FLAGS}, True)])
        self.assertEqual(reasons, {"yaml_dashboards_skipped": 1, "read_failed": 1})
        self.assertEqual(reader.calls, [(self.READ, 100.0 + subcalls.SCAN_DEADLINE_SECONDS)])
        text = json.dumps([record for record, _ in records])
        for body_text in ("Garden", "Kitchen", "light.kitchen", "views"):
            self.assertNotIn(body_text, text)

    async def test_exact_and_fuzzy_thresholds(self):
        self.assertEqual((scoring.EXACT_MATCH_THRESHOLD, scoring.FUZZY_THRESHOLD), (100, 70))
        for exact, score, matched in ((True, 100, 3), (True, 99, 0), (False, 70, 3), (False, 69, 0)):
            with self.subTest(exact=exact, score=score), \
                    patch.object(scoring, "config_match_score", return_value=score) as scorer:
                records, _, _ = await self.search(exact=exact)
                self.assertEqual([record["score"] for record, _ in records], [score] * matched)
                self.assertEqual({call.args[1:] for call in scorer.call_args_list}, {("kitchen", exact)})

    async def test_an_unusable_read_result_counts_every_target(self):
        for result in (ReadResult("dashboard_config", c.UNAVAILABLE, "timeout"), None,
                       ReadResult("dashboard_config", c.COMPLETE, None, None)):
            with self.subTest(result=repr(result)[:60]):
                records, reasons, _ = await self.search(reader=FakeReader({}, result=result))
                self.assertEqual(records, [])
                self.assertEqual(reasons, {"yaml_dashboards_skipped": 1, "read_failed": len(self.READ)})
        # A target the reader did not report is a failed read.
        result = ReadResult("dashboard_config", c.COMPLETE, None, MappingProxyType({None: 100}), (), 0,
                            MappingProxyType({None: "read"}))
        records, reasons, _ = await self.search(reader=FakeReader({}, result=result))
        self.assertEqual([record["url_path"] for record, _ in records], ["default"])
        self.assertEqual(reasons, {"yaml_dashboards_skipped": 1, "read_failed": len(self.READ) - 1})

    async def test_nothing_to_read_makes_no_read(self):
        records, reasons, reader = await self.search(rows={"lovelace": board(mode="yaml")})
        self.assertEqual((records, reader.calls), ([], []))
        self.assertEqual(reasons, {"yaml_dashboards_skipped": 1})

    async def test_bad_arguments_raise_before_any_read(self):
        reader = FakeReader(self.BODIES)
        for query, exact in (("", True), (None, True), (7, True), ("kitchen", None), ("kitchen", 1)):
            with self.subTest(query=query, exact=exact), self.assertRaises(ValueError):
                await dashboards.search_dashboards(query, exact, listing(self.ROWS), reader, Reasons())
        self.assertEqual(reader.calls, [])


class EngineTests(e3.World):
    def boards(self, *rows, bodies):
        common = {"icon": None, "require_admin": False, "show_in_sidebar": True}
        self.values[LIST] = [{**common, "id": url_path.replace("-", "_"), "url_path": url_path,
                              "mode": mode, "title": title} for url_path, mode, title in rows]
        self.values[CONFIG] = lambda payloads: [
            (True, copy.deepcopy(bodies[payload.get("url_path")]))
            if payload.get("url_path") in bodies else (False, "config_not_found")
            for payload in payloads]

    async def test_include_config_discloses_each_matched_body_as_withheld(self):
        self.boards(("synthetic-board", "storage", "Synthetic"),
                    bodies={None: {"views": [{"title": f"Kitchen {e3.MARKER}"}]},
                            "synthetic-board": {"views": [{"cards": [{"entity": "light.kitchen"}]}]}})
        result = await self.go(query="kitchen", search_types=["dashboard"], include_config=True)
        self.assertEqual(result["dashboards"], [
            {"url_path": "default", "title": "Default Dashboard", **FLAGS},
            {"url_path": "synthetic-board", "title": "Synthetic", **FLAGS}])
        self.assertEqual(e3.reasons_of(result)[("filtered", "config", "config_bodies_withheld")], 2)
        self.assertGreaterEqual(result["fields_withheld"], 1)
        text = json.dumps(result)
        self.assertNotIn(e3.MARKER, text)
        self.assertNotIn("light.kitchen", text)
        plain = await self.go(query="kitchen", search_types=["dashboard"])
        self.assertNotIn(("filtered", "config", "config_bodies_withheld"), e3.reasons_of(plain))

    async def test_a_long_title_is_bounded(self):
        title = "Synthetic " + "x" * 400
        self.boards(("synthetic-board", "storage", title),
                    bodies={"synthetic-board": {"views": [{"title": "kitchen"}]}})
        result = await self.go(query="kitchen", search_types=["dashboard"])
        [record] = result["dashboards"]
        self.assertEqual(record["url_path"], "synthetic-board")
        # The reply sanitizer's cap on every record string, with its suffix.
        self.assertEqual(record["title"], title[:reply.RECORD_STRING_MAX] + TRUNCATION_SUFFIX)
        self.assertEqual(e3.reasons_of(result)[("truncated", "config", "record_strings")], 1)

    async def test_no_default_read_when_lovelace_is_listed(self):
        self.boards(("lovelace", "storage", "Home"), ("synthetic-board", "storage", "Synthetic"),
                    bodies={"lovelace": {"views": [{"title": "kitchen"}]}, "synthetic-board": {}})
        result = await self.go(query="kitchen", search_types=["dashboard"])
        self.assertEqual(self.batches(CONFIG), [[{"type": CONFIG, "url_path": "lovelace"},
                                                 {"type": CONFIG, "url_path": "synthetic-board"}]])
        self.assertEqual([(record["url_path"], record["title"]) for record in result["dashboards"]],
                         [("lovelace", "Home")])
        self.calls.clear()
        self.boards(("lovelace", "yaml", "Home"), bodies={})
        result = await self.go(query="kitchen", search_types=["dashboard"])
        self.assertEqual((self.batches(CONFIG), result["dashboards"]), ([], []))
        self.assertEqual(e3.reasons_of(result), {("not_searched", "dashboard", "yaml_dashboards_skipped"): 1})

    async def test_an_unreadable_list_reads_no_configuration(self):
        self.values[LIST] = HomeAssistantApiError()
        self.values[CONFIG] = AssertionError("no configuration read")
        result = await self.go(query="kitchen", search_types=["dashboard"])
        self.assertEqual((self.batches(CONFIG), result["dashboards"]), ([], []))
        self.assertIsNone(e3.reasons_of(result)[("not_searched", "dashboard", "read_failed")])

    async def test_without_the_dashboard_profile_a_dashboard_search_is_refused(self):
        self.core.unavailable = {c.DASHBOARD}
        error = await self.refused("source_unavailable", {"query": "kitchen", "search_types": ["dashboard"]})
        self.assertEqual(error.details, {"reason": "source_unavailable", "source": "core_authority"})
        self.assertEqual((self.calls, self.pages), ([], []))
        result = await self.go(query="kitchen", search_types=["automation"])
        self.assertNotIn("dashboards", result)

    async def test_dashboards_are_not_searched_by_default(self):
        result = await self.go(query="kitchen")
        self.assertNotIn("dashboards", result)
        self.assertFalse([call for call in self.calls if call[0] == "command" and call[1]["type"] == LIST])
        self.assertEqual(self.batches(CONFIG), [])
        for reads in self.reads:
            self.assertFalse({"dashboards", "dashboard_config"} & set(reads), reads)


class RequirementTests(unittest.TestCase):
    def requirements(self, arguments):
        return routes.static_tool_requirements("ha_search", arguments)

    def test_only_a_dashboard_search_holds_the_dashboard_profile(self):
        base = self.requirements({"query": "synthetic"})
        self.assertEqual(base, tuple(sorted(routes.STATIC_TOOL_CORE_REQUIREMENTS["ha_search"])))
        self.assertNotIn(c.DASHBOARD, base)
        for search_types in (["dashboard"], '["dashboard"]', ["automation", "dashboard"]):
            with self.subTest(search_types=search_types):
                self.assertEqual(self.requirements({"query": "synthetic", "search_types": search_types}),
                                 tuple(sorted({*base, c.DASHBOARD})))
        for search_types in (["entity"], ["automation", "helper"]):
            with self.subTest(search_types=search_types):
                self.assertEqual(self.requirements({"query": "synthetic", "search_types": search_types}), base)

    def test_a_call_the_engine_refuses_holds_nothing_more(self):
        base = self.requirements({"query": "synthetic"})
        for arguments in (None, "synthetic", 7, [], {"query": ""},
                          {"query": "synthetic", "search_types": ["bogus"]},
                          {"query": "synthetic", "search_types": []},
                          {"query": "synthetic", "search_types": "dashboard"},
                          {"query": "synthetic", "limit": -1},
                          {"query": "synthetic", "search_types": ["dashboard"], "unknown": 1}):
            with self.subTest(arguments=repr(arguments)[:60]):
                self.assertEqual(self.requirements(arguments), base)


class Row25Tests(unittest.TestCase):
    HELPER = {"entity_id": "sensor.synthetic_template", "helper_type": "template", "name": "Synthetic",
              "score": 100, "match_in_name": True, "match_in_config": False, "match_in_references": False}
    ENTITY = {"friendly_name": "Synthetic", "score": 100, "match_in_name": True, "match_in_config": False,
              "match_in_references": False}

    def test_row_25_admits_the_three_names_and_not_the_bodies(self):
        admitted = k.ROWS["ha_search"].admitted
        self.assertLessEqual({"entry_id", "url_path", "title"}, admitted)
        self.assertFalse({"config", "options_withheld", "filename", "icon", "require_admin",
                          "show_in_sidebar", "mode", "views", "id"} & admitted)

    def test_a_flow_helper_row_carries_a_string_entry_id(self):
        self.assertTrue(subcalls._row_valid("helper", self.HELPER))
        self.assertTrue(subcalls._row_valid("helper", {**self.HELPER, "entry_id": "01JSYNTHETIC"}))
        for bad in (7, None, ["01J"], True):
            with self.subTest(entry_id=bad):
                self.assertFalse(subcalls._row_valid("helper", {**self.HELPER, "entry_id": bad}))

    def test_a_sub_call_row_with_a_dashboard_name_is_drift(self):
        rows = {"automation": {**self.ENTITY, "entity_id": "automation.synthetic"},
                "script": {**self.ENTITY, "entity_id": "script.synthetic", "script_id": "synthetic"},
                "scene": {**self.ENTITY, "entity_id": "scene.synthetic", "scene_id": "synthetic"},
                "helper": self.HELPER}
        for search_type, row in rows.items():
            with self.subTest(search_type=search_type):
                self.assertTrue(subcalls._row_valid(search_type, row))
                for name in ("url_path", "title"):
                    self.assertFalse(subcalls._row_valid(search_type, {**row, name: "synthetic-board"}))
                if search_type != "helper":
                    self.assertFalse(subcalls._row_valid(search_type, {**row, "entry_id": "01J"}))

    def test_dashboards_are_not_a_sub_call(self):
        for table in (subcalls.SUBCALL_TYPES, subcalls.BUCKETS, subcalls.ROW_BYTES, subcalls._ROW_KEYS,
                      subcalls.BODY_TYPES):
            self.assertNotIn("dashboard", table)
        with self.assertRaises(ValueError):
            subcalls.page_arguments("dashboard", "synthetic", True, 0, subcalls.PAGE_SIZE_LADDER[0])
        with self.assertRaises(ValueError):
            k.bucket_withheld_names({"dashboards": []}, "dashboards")
        # A delegated page that carries dashboard rows is still refused.
        self.assertIn("dashboards", gateway._SEARCH_PAGE_BUCKETS)


if __name__ == "__main__":
    unittest.main()
