"""HAMCP-519: read-only storage helper settings (``get_helper_settings``).

Release 2.11.0 Lane C GO, 2026-11-27 (Josh's decisions, as relayed by the
architect): an ``input_text`` initial value is reported as presence and
length only; the other initial values are returned; the reply pages with the
PC-1 cursor. Fixtures model the Home Assistant Core 2026.11.3 storage
collection rows (``input_*/list``) and ``config/entity_registry/list``.
Everything is synthetic; no live Home Assistant.
"""

from __future__ import annotations

import ast
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from ha_mcp_engineering import capabilities, paging  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.routes import STATIC_TOOL_CORE_REQUIREMENTS  # noqa: E402
from ha_mcp_engineering.helper_settings import contracts, service  # noqa: E402
from ha_mcp_engineering.incident_logs.cursor import CursorCodec  # noqa: E402
from ha_mcp_engineering.providers import routing  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.search import core_contracts as core  # noqa: E402
from ha_mcp_engineering.search.core_reads import CoreReadSession  # noqa: E402
from ha_mcp_engineering.tools import helper_settings as tool_module  # noqa: E402
from ha_mcp_engineering.tools import registry as tool_registry  # noqa: E402

PACKAGE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering"
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
SECRET = "synthetic-known-secret-519-token"
TEXT_INITIAL = "synthetic-initial-text-needle"
ALL_DOMAINS = ("input_boolean", "input_number", "input_select", "input_text", "input_datetime",
               "input_button")
BUDGET = 60_000 - paging.ENVELOPE_RESERVE_CHARS


def registry_row(entity_id, platform, unique_id, **values):
    return {"area_id": None, "categories": {}, "config_entry_id": None, "config_subentry_id": None,
            "created_at": 1.0, "device_id": None, "disabled_by": None, "entity_category": None,
            "entity_id": entity_id, "hidden_by": None, "icon": None, "id": "01JSYNTHETIC" + entity_id,
            "labels": [], "modified_at": 1.0, "name": None, "next_name_part": None, "options": {},
            "original_name": None, "platform": platform, "translation_key": None,
            "unique_id": unique_id, **values}


# Core 2026.11.3 storage fields of each collection (the CREATE/STORAGE_FIELDS
# of components/input_*/__init__.py), with ``id`` added by the collection.
def helpers():
    return {
        "input_boolean": [{"id": "synthetic_guest", "name": "Synthetic guest", "icon": "mdi:account",
                           "initial": True}],
        "input_number": [{"id": "synthetic_level", "name": "Synthetic level", "icon": None, "initial": 20.5,
                          "min": 0, "max": 100.0, "step": 0.5, "mode": "slider",
                          "unit_of_measurement": "%", "device_class": None}],
        "input_select": [{"id": "synthetic_mode", "name": "Synthetic mode", "icon": None, "initial": "b",
                          "options": ["a", "b", "c"]}],
        "input_text": [{"id": "synthetic_note", "name": "Synthetic note", "icon": None,
                        "initial": TEXT_INITIAL, "min": 0, "max": 100, "mode": "text", "pattern": None,
                        "unit_of_measurement": None}],
        "input_datetime": [{"id": "synthetic_alarm", "name": "Synthetic alarm", "icon": None,
                            "has_date": False, "has_time": True, "initial": "07:30:00"}],
        "input_button": [{"id": "synthetic_press", "name": "Synthetic press", "icon": "mdi:gesture-tap"}],
    }


def values_for(collections):
    registry = [registry_row(f"{domain}.{row['id']}", domain, row["id"])
                for domain, rows in collections.items() for row in rows if type(row) is dict and "id" in row]
    return {"config/entity_registry/list": registry,
            **{f"{domain}/list": rows for domain, rows in collections.items()}}


class SyntheticCore:
    def __init__(self):
        self.unavailable: set[str] = set()
        self.generation = 7
        self.current_observation = SimpleNamespace(version="2026.11.3")
        self.route_calls: list[tuple[str, ...]] = []

    def route_status(self, requirements):
        self.route_calls.append(requirements)
        return {"available": bool(requirements) and not set(requirements) & self.unavailable,
                "disposition": "admitted", "reason_codes": [], "generation": self.generation}


class SyntheticRest:
    def __init__(self, calls):
        self.calls = calls

    async def request(self, method, path, **kwargs):
        self.calls.append(("rest", method, str(path)))
        raise AssertionError("helper settings dispatch no REST read")


class SyntheticWebSocket:
    def __init__(self, values, calls):
        self.values, self.calls, self.hook = values, calls, None

    async def command(self, payload):
        self.calls.append(("command", copy.deepcopy(payload)))
        if self.hook:
            self.hook(payload["type"])
        value = self.values[payload["type"]]
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)

    async def command_batch(self, payloads):
        self.calls.append(("batch", copy.deepcopy(payloads)))
        raise AssertionError("helper settings dispatch no batch read")


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.collections = helpers()
        self.values = values_for(self.collections)
        self.calls: list = []
        self.rest = SyntheticRest(self.calls)
        self.socket = SyntheticWebSocket(self.values, self.calls)
        self.core = SyntheticCore()
        self.telemetry, token = begin_request()
        self.addCleanup(end_request, token)
        self.telemetry.caller_id = "synthetic_authenticated_caller"
        self.authorized = True
        self.telemetry.core_dispatch_authorizer = lambda: self.authorized
        self.codec = CursorCodec(contracts.NAME)

    def factory(self, reads):
        return CoreReadSession(self.rest, self.socket, self.core, reads, clock=lambda: NOW)

    def set_rows(self, domain, rows):
        self.values[f"{domain}/list"] = rows

    async def report(self, budget=BUDGET, **arguments):
        request = contracts.validate_arguments(arguments)
        return await service.report(request, session_factory=self.factory, known_secrets=(SECRET,),
                                    codec=self.codec, budget=budget)

    def commands(self):
        return [call[1]["type"] for call in self.calls if call[0] == "command"]

    def by_key(self, reply):
        return {row["helper_key"]: row for row in reply["helpers"]}

    def assertRefused(self, reason, error):
        self.assertIsInstance(error.exception, contracts.HelperSettingsError)
        self.assertEqual(error.exception.details, {"reason": reason})


class ProjectionTests(Base):
    async def test_every_domain_is_projected_with_its_entity_id(self):
        reply = await self.report()
        self.assertEqual((reply["schema"], reply["completeness"], reply["partial_reasons"]),
                         ("helper-settings-v1", "complete", []))
        self.assertEqual(reply["domains"], list(ALL_DOMAINS))
        self.assertEqual((reply["unavailable_domains"], reply["coverage"]), ([], "storage_collections_only"))
        self.assertEqual((reply["total"], reply["returned"], reply["has_more"], reply["next_cursor"]),
                         (6, 6, False, None))
        rows = self.by_key(reply)
        self.assertEqual(list(rows), sorted(rows))
        for domain, [stored] in self.collections.items():
            row = rows[f"{domain}/{stored['id']}"]
            self.assertEqual((row["domain"], row["id"], row["entity_id"], row["entity_id_status"],
                              row["name"], row["icon"], row["invalid_fields"]),
                             (domain, stored["id"], f"{domain}.{stored['id']}", "resolved",
                              stored["name"], stored["icon"], []))
        self.assertEqual(rows["input_boolean/synthetic_guest"]["initial"], {"present": True, "value": True})
        self.assertEqual(rows["input_boolean/synthetic_guest"]["settings"], {})
        self.assertEqual(rows["input_number/synthetic_level"]["initial"], {"present": True, "value": 20.5})
        self.assertEqual(rows["input_number/synthetic_level"]["settings"], {
            "min": 0, "max": 100.0, "step": 0.5, "mode": "slider", "unit_of_measurement": "%",
            "device_class": None})
        self.assertEqual(rows["input_select/synthetic_mode"]["initial"], {"present": True, "value": "b"})
        self.assertEqual(rows["input_select/synthetic_mode"]["settings"], {
            "options_total": 3, "options_truncated": False, "options": ["a", "b", "c"]})
        self.assertEqual(rows["input_text/synthetic_note"]["settings"], {
            "min": 0, "max": 100, "mode": "text", "pattern": None, "unit_of_measurement": None})
        self.assertEqual(rows["input_datetime/synthetic_alarm"]["initial"],
                         {"present": True, "value": "07:30:00"})
        self.assertEqual(rows["input_datetime/synthetic_alarm"]["settings"],
                         {"has_date": False, "has_time": True})
        self.assertIsNone(rows["input_button/synthetic_press"]["initial"])
        self.assertEqual(rows["input_button/synthetic_press"]["settings"], {})
        self.assertEqual([source["source"] for source in reply["sources"]],
                         ["entity_registry", *(f"{domain}_list" for domain in ALL_DOMAINS)])
        self.assertTrue(all(source["status"] == "complete" for source in reply["sources"]))

    async def test_text_initial_is_presence_and_length_only(self):
        reply = await self.report()
        row = self.by_key(reply)["input_text/synthetic_note"]
        self.assertEqual(row["initial"], {"present": True, "length": len(TEXT_INITIAL)})
        self.assertNotIn(TEXT_INITIAL, json.dumps(reply))

    async def test_absent_or_null_initial_is_not_present_and_not_drift(self):
        self.collections["input_text"][0].pop("initial")
        self.collections["input_select"][0]["initial"] = None
        self.set_rows("input_text", self.collections["input_text"])
        self.set_rows("input_select", self.collections["input_select"])
        rows = self.by_key(await self.report())
        self.assertEqual(rows["input_text/synthetic_note"]["initial"], {"present": False, "length": None})
        self.assertEqual(rows["input_select/synthetic_mode"]["initial"], {"present": False, "value": None})
        self.assertEqual(rows["input_text/synthetic_note"]["invalid_fields"], [])

    async def test_unknown_stored_keys_are_never_returned(self):
        self.collections["input_boolean"][0]["synthetic_extra_key"] = "synthetic-extra-needle"
        self.set_rows("input_boolean", self.collections["input_boolean"])
        reply = await self.report()
        self.assertNotIn("synthetic-extra-needle", json.dumps(reply))
        self.assertEqual(reply["completeness"], "complete")

    async def test_field_drift_is_partial_and_withholds_the_value(self):
        number = self.collections["input_number"][0]
        number.update(min="0", step=float("inf"))
        self.collections["input_select"][0]["options"] = ["a", 1]
        self.collections["input_text"][0]["initial"] = 12
        self.collections["input_boolean"][0]["initial"] = "on"
        self.collections["input_datetime"][0]["has_time"] = "yes"
        self.collections["input_button"][0]["icon"] = 5
        for domain, rows in self.collections.items():
            self.set_rows(domain, rows)
        reply = await self.report()
        self.assertEqual((reply["completeness"], reply["partial_reasons"]), ("partial", ["field_drift"]))
        rows = self.by_key(reply)
        level = rows["input_number/synthetic_level"]
        self.assertEqual(level["invalid_fields"], ["min", "step"])
        self.assertEqual((level["settings"]["min"], level["settings"]["step"], level["settings"]["max"]),
                         (None, None, 100.0))
        self.assertEqual(rows["input_select/synthetic_mode"]["settings"]["options"], None)
        self.assertEqual(rows["input_select/synthetic_mode"]["invalid_fields"], ["options"])
        self.assertEqual(rows["input_text/synthetic_note"]["initial"], {"present": True, "length": None})
        self.assertEqual(rows["input_text/synthetic_note"]["invalid_fields"], ["initial"])
        self.assertEqual(rows["input_boolean/synthetic_guest"]["initial"], {"present": True, "value": None})
        self.assertEqual(rows["input_datetime/synthetic_alarm"]["invalid_fields"], ["has_time"])
        self.assertEqual((rows["input_button/synthetic_press"]["icon"],
                          rows["input_button/synthetic_press"]["invalid_fields"]), (None, ["icon"]))

    async def test_bool_is_not_a_number_and_name_drift_is_reported(self):
        self.collections["input_number"][0]["max"] = True
        self.collections["input_boolean"][0]["name"] = 7
        for domain in ("input_number", "input_boolean"):
            self.set_rows(domain, self.collections[domain])
        reply = await self.report()
        rows = self.by_key(reply)
        self.assertEqual(rows["input_number/synthetic_level"]["invalid_fields"], ["max"])
        self.assertEqual((rows["input_boolean/synthetic_guest"]["name"],
                          rows["input_boolean/synthetic_guest"]["invalid_fields"]), (None, ["name"]))
        self.assertIn("field_drift", reply["partial_reasons"])

    async def test_options_are_capped_with_their_total(self):
        options = [f"option_{index:03d}" for index in range(150)]
        self.collections["input_select"][0]["options"] = options
        self.set_rows("input_select", self.collections["input_select"])
        reply = await self.report()
        settings = self.by_key(reply)["input_select/synthetic_mode"]["settings"]
        self.assertEqual(settings["options"], options[:contracts.MAX_OPTIONS])
        self.assertEqual((settings["options_total"], settings["options_truncated"]), (150, True))
        self.assertEqual(reply["partial_reasons"], ["options_truncated"])

    async def test_malformed_and_duplicate_rows_are_reported(self):
        # The shared Core read keeps the first row of a duplicated id and
        # drops the rest; the result is partial with duplicate_rows.
        good = self.collections["input_boolean"][0]
        self.set_rows("input_boolean", [good, {"name": "no id"}, "not a row",
                                        {"id": "synthetic_twin", "name": "One"},
                                        {"id": "synthetic_twin", "name": "Two"}])
        reply = await self.report(domains=["input_boolean"])
        self.assertEqual([(row["helper_key"], row["name"]) for row in reply["helpers"]],
                         [("input_boolean/synthetic_guest", "Synthetic guest"),
                          ("input_boolean/synthetic_twin", "One")])
        self.assertEqual(set(reply["partial_reasons"]), {"malformed_rows", "duplicate_rows"})
        self.assertNotIn("Two", json.dumps(reply["helpers"]))
        self.assertEqual(reply["completeness"], "partial")


class EntityIdTests(Base):
    async def test_not_registered_when_the_registry_is_complete(self):
        self.values["config/entity_registry/list"] = []
        reply = await self.report(domains=["input_boolean"])
        self.assertEqual((reply["helpers"][0]["entity_id"], reply["helpers"][0]["entity_id_status"]),
                         (None, "not_registered"))
        self.assertEqual(reply["completeness"], "complete")

    async def test_unresolved_when_the_registry_is_partial(self):
        self.values["config/entity_registry/list"] = [
            {"platform": "input_boolean"},
            registry_row("input_number.synthetic_level", "input_number", "synthetic_level")]
        reply = await self.report(domains=["input_boolean"])
        self.assertEqual(reply["helpers"][0]["entity_id_status"], "unresolved")
        self.assertIn("malformed_rows", reply["partial_reasons"])

    async def test_unresolved_when_the_registry_is_unavailable(self):
        self.values["config/entity_registry/list"] = RuntimeError("synthetic-registry-needle")
        reply = await self.report()
        self.assertEqual({row["entity_id_status"] for row in reply["helpers"]}, {"unresolved"})
        self.assertEqual({row["entity_id"] for row in reply["helpers"]}, {None})
        self.assertEqual(reply["partial_reasons"], ["entity_registry_unavailable"])
        self.assertNotIn("synthetic-registry-needle", json.dumps(reply))

    async def test_two_registry_entries_for_one_helper_are_ambiguous(self):
        self.values["config/entity_registry/list"] = [
            registry_row("input_boolean.synthetic_guest", "input_boolean", "synthetic_guest"),
            registry_row("input_boolean.synthetic_guest_2", "input_boolean", "synthetic_guest")]
        reply = await self.report(domains=["input_boolean"])
        self.assertEqual((reply["helpers"][0]["entity_id"], reply["helpers"][0]["entity_id_status"]),
                         (None, "ambiguous"))

    async def test_other_platforms_and_mismatched_domains_never_match(self):
        self.values["config/entity_registry/list"] = [
            registry_row("switch.synthetic_guest", "input_boolean", "synthetic_guest"),
            registry_row("input_boolean.synthetic_other", "template", "synthetic_guest")]
        reply = await self.report(domains=["input_boolean"])
        self.assertEqual(reply["helpers"][0]["entity_id_status"], "not_registered")


class SourceTests(Base):
    async def test_some_collections_unavailable_is_partial(self):
        self.values["input_select/list"] = RuntimeError("synthetic-source-needle")
        reply = await self.report()
        self.assertEqual((reply["completeness"], reply["partial_reasons"], reply["unavailable_domains"]),
                         ("partial", ["source_unavailable"], ["input_select"]))
        self.assertNotIn("input_select/synthetic_mode", self.by_key(reply))
        self.assertEqual(reply["total"], 5)
        self.assertNotIn("synthetic-source-needle", json.dumps(reply))

    async def test_every_selected_collection_unavailable_refuses(self):
        for domain in ("input_text", "input_number"):
            self.values[f"{domain}/list"] = RuntimeError("synthetic")
        with self.assertRaises(contracts.HelperSettingsError) as error:
            await self.report(domains=["input_text", "input_number"])
        self.assertRefused("sources_unavailable", error)

    async def test_oversized_collection_is_unavailable(self):
        self.set_rows("input_button", [{"id": f"b{index}", "name": "B"} for index in range(core.MAX_ITEMS + 1)])
        reply = await self.report()
        self.assertEqual(reply["unavailable_domains"], ["input_button"])
        source = next(item for item in reply["sources"] if item["source"] == "input_button_list")
        self.assertEqual((source["status"], source["reason"]), ("unavailable", "inventory_bound_exceeded"))

    async def test_only_the_declared_reads_are_dispatched(self):
        await self.report()
        self.assertEqual(self.commands(), ["config/entity_registry/list",
                                           *(f"{domain}/list" for domain in ALL_DOMAINS)])
        self.assertTrue(all(call[0] == "command" for call in self.calls))
        self.assertTrue(all(set(call[1]) == {"type"} for call in self.calls))
        self.calls.clear()
        await self.report(domains=["input_datetime", "input_boolean"])
        self.assertEqual(self.commands(), ["config/entity_registry/list", "input_boolean/list",
                                           "input_datetime/list"])
        self.assertEqual(self.core.route_calls[-1], ("core.basic_websocket_read", "core.non_device_registry_read"))

    def test_reads_are_declared_core_reads_with_the_routed_requirements(self):
        request = contracts.validate_arguments({})
        reads = contracts.reads_for(request)
        self.assertEqual(reads, ("entity_registry", *(f"{domain}_list" for domain in ALL_DOMAINS)))
        self.assertTrue(all(name in core.READS and core.READS[name].shape == "rows" for name in reads))
        self.assertEqual(core.requirements_for(reads), contracts.CORE_REQUIREMENTS)
        self.assertEqual(STATIC_TOOL_CORE_REQUIREMENTS["get_helper_settings"], contracts.CORE_REQUIREMENTS)


class AuthorityTests(Base):
    async def assertRefusedBeforeDispatch(self, reason="authority_unavailable", **arguments):
        with self.assertRaises(contracts.HelperSettingsError) as error:
            await self.report(**arguments)
        self.assertRefused(reason, error)
        self.assertEqual(self.calls, [])

    async def test_anonymous_caller_is_refused(self):
        self.telemetry.caller_id = "anonymous"
        await self.assertRefusedBeforeDispatch()

    async def test_degraded_core_gate_is_refused(self):
        self.core.unavailable = {"core.non_device_registry_read"}
        await self.assertRefusedBeforeDispatch()
        self.core.unavailable = {"core.basic_websocket_read"}
        await self.assertRefusedBeforeDispatch()

    async def test_denied_dispatch_authorizer_is_refused(self):
        self.authorized = False
        await self.assertRefusedBeforeDispatch()

    async def test_lease_change_mid_read_is_refused(self):
        def bump(command):
            if command == "input_number/list":
                self.core.generation += 1
        self.socket.hook = bump
        with self.assertRaises(contracts.HelperSettingsError) as error:
            await self.report()
        self.assertRefused("authority_unavailable", error)
        self.assertNotIn("input_select/list", self.commands())


class SanitizationTests(Base):
    async def test_known_secret_is_redacted_and_counted(self):
        self.collections["input_boolean"][0]["name"] = f"Guest {SECRET}"
        self.set_rows("input_boolean", self.collections["input_boolean"])
        reply = await self.report()
        rendered = json.dumps(reply)
        self.assertNotIn(SECRET, rendered)
        self.assertIn("redacted_by_policy", reply["partial_reasons"])
        self.assertGreaterEqual(reply["redaction"]["redacted_field_count"], 1)

    async def test_secret_in_an_id_never_reaches_the_identity(self):
        self.set_rows("input_button", [{"id": SECRET, "name": "Synthetic"}])
        reply = await self.report(domains=["input_button"])
        self.assertNotIn(SECRET, json.dumps(reply))
        self.assertEqual(reply["helpers"][0]["helper_key"], f"input_button/{reply['helpers'][0]['id']}")

    async def test_two_ids_the_sanitizer_makes_equal_are_dropped(self):
        self.set_rows("input_button", [{"id": f"{SECRET}a", "name": "A"}, {"id": f"{SECRET}b", "name": "B"},
                                       {"id": "plain", "name": "C"}])
        original = service.sanitize_untrusted_data

        # The id pass maps both secret-bearing ids to one sanitized id.
        def collapsing(value, **kwargs):
            if type(value) is list:
                value = ["same" if SECRET in item else item for item in value]
            return original(value, **kwargs)
        with patch.object(service, "sanitize_untrusted_data", collapsing):
            reply = await self.report(domains=["input_button"])
        self.assertEqual([row["helper_key"] for row in reply["helpers"]], ["input_button/plain"])
        self.assertIn("duplicate_rows", reply["partial_reasons"])

    async def test_long_text_is_truncated_and_counted(self):
        self.collections["input_text"][0]["pattern"] = "p" * 2000
        self.set_rows("input_text", self.collections["input_text"])
        reply = await self.report()
        pattern = self.by_key(reply)["input_text/synthetic_note"]["settings"]["pattern"]
        self.assertLess(len(pattern), 2000)
        self.assertIn("truncated_text", reply["partial_reasons"])
        self.assertGreaterEqual(reply["redaction"]["truncated_field_count"], 1)

    async def test_failed_sanitization_refuses(self):
        failed = SimpleNamespace(failed_closed=True, value=None, redacted_field_count=0,
                                 truncated_field_count=0, redaction_categories=())
        with patch.object(service, "sanitize_untrusted_data", return_value=failed), \
                self.assertRaises(contracts.HelperSettingsError) as error:
            await self.report()
        self.assertRefused("sanitization_failed", error)


class PagingTests(Base):
    def setUp(self):
        super().setUp()
        self.set_rows("input_button", [{"id": f"synthetic_{index:02d}", "name": f"B{index}"}
                                       for index in range(25)])

    async def test_cursor_walk_returns_every_helper_once(self):
        keys, cursor, pages = [], "", 0
        while True:
            reply = await self.report(domains=["input_button"], limit=10, cursor=cursor)
            pages += 1
            keys += [row["helper_key"] for row in reply["helpers"]]
            self.assertEqual(reply["total"], 25)
            if not reply["has_more"]:
                self.assertIsNone(reply["next_cursor"])
                break
            self.assertEqual(reply["page_limit_reason"], "limit")
            cursor = reply["next_cursor"]
        self.assertEqual(pages, 3)
        self.assertEqual(keys, sorted(f"input_button/synthetic_{index:02d}" for index in range(25)))

    async def test_changed_helper_set_refuses_the_cursor(self):
        first = await self.report(domains=["input_button"], limit=10)
        self.values["input_button/list"].append({"id": "synthetic_new", "name": "New"})
        with self.assertRaises(GovernanceError) as error:
            await self.report(domains=["input_button"], limit=10, cursor=first["next_cursor"])
        self.assertEqual((error.exception.code, error.exception.details["reason"]),
                         (ErrorCode.STALE_CURSOR, "list_continuity_broken"))

    async def test_cursor_for_other_domains_is_refused_before_any_read(self):
        first = await self.report(domains=["input_button"], limit=10)
        self.calls.clear()
        with self.assertRaises(GovernanceError) as error:
            await self.report(limit=10, cursor=first["next_cursor"])
        self.assertEqual((error.exception.code, error.exception.details["reason"]),
                         (ErrorCode.INVALID_CURSOR, "query_mismatch"))
        self.assertEqual(self.calls, [])

    async def test_tampered_cursor_is_refused_before_any_read(self):
        first = await self.report(domains=["input_button"], limit=10)
        self.calls.clear()
        with self.assertRaises(GovernanceError) as error:
            await self.report(domains=["input_button"], cursor=first["next_cursor"][:-2] + "AA")
        self.assertEqual(error.exception.code, ErrorCode.INVALID_CURSOR)
        self.assertEqual(self.calls, [])

    async def test_small_budget_pages_by_size_and_too_small_refuses(self):
        reply = await self.report(domains=["input_button"], limit=25, budget=3_000)
        self.assertTrue(reply["has_more"])
        self.assertEqual(reply["page_limit_reason"], "response_size_limit")
        self.assertLessEqual(len(json.dumps(reply, separators=(",", ":"))), 3_000)
        with self.assertRaises(GovernanceError) as error:
            await self.report(domains=["input_button"], budget=800)
        self.assertEqual(error.exception.details["reason"], "page_exceeds_response_limit")


class ArgumentTests(unittest.TestCase):
    def test_defaults_and_canonical_domain_order(self):
        request = contracts.validate_arguments({})
        self.assertEqual((request.domains, request.limit, request.cursor), (ALL_DOMAINS, 100, ""))
        request = contracts.validate_arguments({"domains": ["input_text", "input_boolean"]})
        self.assertEqual(request.domains, ("input_boolean", "input_text"))

    def test_invalid_arguments_are_refused(self):
        for arguments in (
            [], {"entity_id": "x"}, {"domains": []}, {"domains": ["input_text", "input_text"]},
            {"domains": ["switch"]}, {"domains": "input_text"}, {"domains": list(ALL_DOMAINS) + ["x"]},
            {"limit": 0}, {"limit": 201}, {"limit": True}, {"limit": "10"}, {"limit": 1.0},
            {"cursor": 5}, {"cursor": "c" * (paging.MAX_CURSOR_CHARS + 1)},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(contracts.HelperSettingsError) as error:
                contracts.validate_arguments(arguments)
            self.assertEqual(error.exception.details, {"reason": "invalid_arguments"})
            self.assertEqual(error.exception.code, ErrorCode.INVALID_REQUEST)

    def test_closed_error_reasons(self):
        self.assertEqual(contracts.HelperSettingsError("not a reason").details,
                         {"reason": "sources_unavailable"})
        self.assertEqual(contracts.HelperSettingsError("authority_unavailable").code,
                         ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(contracts.HelperSettingsError("sources_unavailable").code,
                         ErrorCode.ANALYSIS_UNAVAILABLE)


class ToolTests(Base):
    async def asyncSetUp(self):
        runtime = patch.multiple(tool_module.SEARCH, session_factory=self.factory, known_secrets=(SECRET,))
        runtime.start()
        self.addCleanup(runtime.stop)

    async def call(self, **arguments):
        return json.loads(await tool_module.registered_tool().run(arguments))

    async def test_success_envelope(self):
        response = await self.call(domains=["input_text"])
        data = response["data"]
        self.assertEqual(data["helpers"][0]["initial"], {"present": True, "length": len(TEXT_INITIAL)})
        self.assertEqual({key: response["metadata"][key] for key in
                          ("provider", "data_providers", "fallback", "fallback_occurred", "completeness")},
                         {"provider": "engineering", "data_providers": ["direct_ha_api"], "fallback": "none",
                          "fallback_occurred": False, "completeness": "complete"})
        self.assertEqual(response["warnings"], [])
        self.assertEqual(self.telemetry.result_status, "success")

    async def test_partial_result_warns(self):
        self.values["input_select/list"] = RuntimeError("synthetic")
        response = await self.call()
        self.assertEqual(response["metadata"]["completeness"], "partial")
        self.assertEqual(response["warnings"], [tool_module._PARTIAL])
        self.assertEqual(self.telemetry.result_status, "partial")

    async def test_invalid_arguments_are_not_reflected_and_dispatch_nothing(self):
        needle = "synthetic-argument-needle"
        for arguments in ({"domains": [needle]}, {needle: 1}, {"limit": 0}, {"cursor": needle * 200},
                          {"limit": True}):
            with self.subTest(arguments=list(arguments)):
                rendered = await tool_module.registered_tool().run(arguments)
                response = json.loads(rendered)
                self.assertEqual(response["error_code"], ErrorCode.INVALID_REQUEST.value)
                self.assertEqual(response["details"], {"reason": "invalid_arguments"})
                self.assertNotIn(needle, rendered)
        self.assertEqual(self.calls, [])

    async def test_policy_refusal_dispatches_nothing(self):
        with patch.object(tool_module, "helper_settings_policy_allows_read", return_value=False):
            response = await self.call()
        self.assertEqual(response["error_code"], ErrorCode.PROVIDER_PROHIBITED.value)
        self.assertEqual(self.calls, [])

    async def test_unconfigured_runtime_is_unavailable(self):
        with patch.object(tool_module.SEARCH, "session_factory", None):
            response = await self.call()
        self.assertEqual(response["details"], {"reason": "authority_unavailable"})
        self.assertEqual(self.calls, [])

    async def test_cursor_walk_through_the_tool(self):
        self.set_rows("input_button", [{"id": f"synthetic_{index:02d}", "name": "B"} for index in range(5)])
        first = (await self.call(domains=["input_button"], limit=3))["data"]
        second = (await self.call(domains=["input_button"], limit=3, cursor=first["next_cursor"]))["data"]
        self.assertEqual(len(first["helpers"]) + len(second["helpers"]), 5)
        self.assertFalse(second["has_more"])


class RegistrationTests(unittest.TestCase):
    def test_schema_and_annotations(self):
        tool = tool_module.registered_tool()
        self.assertEqual(tool.name, "get_helper_settings")
        self.assertFalse(tool.parameters["additionalProperties"])
        self.assertEqual(set(tool.parameters["properties"]), {"domains", "limit", "cursor"})
        self.assertEqual(tool.parameters.get("required", []), [])
        annotations = tool.annotations
        self.assertEqual((annotations.readOnlyHint, annotations.destructiveHint, annotations.openWorldHint),
                         (True, False, False))
        self.assertIn("presence and length only", tool.description)
        self.assertIn("YAML", tool.description)

    def test_registered_routed_and_cataloged(self):
        self.assertIn("get_helper_settings", tool_registry.ENGINEERING_STATIC_TOOL_NAMES)
        self.assertEqual(routing.TOOL_CAPABILITY_POLICY["get_helper_settings"],
                         routing.ProviderCapability.CONFIGURATION_DEFINITIONS)
        self.assertTrue(routing.helper_settings_policy_allows_read())
        policy = routing.ANALYTICAL_PROVIDER_POLICIES["get_helper_settings"]
        self.assertEqual((policy["policy_id"], policy["access"], policy["writes_allowed"], policy["fallback_policy"]),
                         ("helper_settings_read_v1", "read", "none", "none"))
        entry = capabilities.capability_for_tool("get_helper_settings")
        self.assertEqual((entry["risk"], entry["operation_class"], entry["fallback"], entry["direct_write_allowed"],
                          entry["data_providers"], entry["policy"]),
                         ("read", "read", "none", False, ["direct_ha_api"], "helper_settings_read_v1"))
        [matrix] = [row for row in capabilities.CAPABILITY_PROVIDER_MATRIX if row["tool"] == "get_helper_settings"]
        self.assertEqual((matrix["fallback_policy"], matrix["trust_profile"]),
                         ("none", "helper-settings-read-v1"))

    def test_no_write_or_delegated_reachability(self):
        # Only CoreReadSession.inventory reaches Home Assistant.
        forbidden = {"call_service", "command", "command_batch", "request", "batch", "execute", "dispatch"}
        for path in (PACKAGE / "helper_settings/service.py", PACKAGE / "helper_settings/contracts.py",
                     PACKAGE / "tools/helper_settings.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            called = {node.func.attr for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
            imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                        for alias in node.names} | {node.module or "" for node in ast.walk(tree)
                                                     if isinstance(node, ast.ImportFrom)}
            with self.subTest(path=path.name):
                self.assertFalse(called & forbidden, called & forbidden)
                self.assertFalse({name for name in imported if "upstream" in name or "gateway" in name})


if __name__ == "__main__":
    unittest.main()
