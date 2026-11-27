"""HAMCP-425 (PC-1): the signed continuation cursor on the five list tools.

search_entities, list_automations, list_devices and list_entity_registry
page their whole sorted match set; list_execution_tasks pages newest first.
Every page is re-read, re-filtered and re-sorted, and the keyed set digest
binds the walk. All rows and identifiers are synthetic.
"""

from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import replace
import inspect
import json
from pathlib import Path
import random
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering import paging  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError, HomeAssistantApiError  # noqa: E402
from ha_mcp_engineering.governance import service as governance_service  # noqa: E402
from ha_mcp_engineering.governance.service import ChangeGovernanceService  # noqa: E402
from ha_mcp_engineering.incident_logs.cursor import CURSOR_TTL_SECONDS, CursorCodec  # noqa: E402
from ha_mcp_engineering.providers.dispatch import CanonicalProviderDispatcher  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.tools import compatibility  # noqa: E402
from ha_mcp_engineering.tools import governance as governance_tools  # noqa: E402


def compact(value):
    return json.dumps(value, separators=(",", ":"), default=str)


def automations(count, pad=0):
    return [{"entity_id": f"automation.a{index:04d}", "state": "on",
             "attributes": {"friendly_name": f"A {index}" + "p" * pad, "id": str(index),
                            "last_triggered": None}} for index in range(count)]


def entity_states(count, pad=0):
    return [{"entity_id": f"{('sensor', 'light')[index % 2]}.e{index:04d}", "state": "on",
             "attributes": {"friendly_name": f"E {index}" + "p" * pad}} for index in range(count)]


def devices(count, pad=0):
    return [{"id": f"d{index:04d}", "name": f"Device {index}" + "p" * pad, "manufacturer": "Acme",
             "model": "M"} for index in range(count)]


def registry(count, pad=0):
    return [{"entity_id": f"sensor.s{index:04d}", "name": f"S {index}" + "p" * pad,
             "platform": "test"} for index in range(count)]


# tool -> (source call, row key, identity, fixture, codec, filter fingerprint input)
TOOLS = {
    "list_automations": ("rest", "automations", "entity_id", automations, "LIST_AUTOMATIONS_CURSORS",
                         lambda query="", **_: {"query": query.lower()}),
    "search_entities": ("rest", "results", "entity_id", entity_states, "SEARCH_ENTITIES_CURSORS",
                        lambda query="", domain="", **_: {"query": query.strip().casefold(),
                                                         "domain": domain.strip().lower()}),
    "list_devices": ("ws_command", "devices", "id", devices, "LIST_DEVICES_CURSORS",
                     lambda query="", **_: {"query": query.lower()}),
    "list_entity_registry": ("ws_command", "entities", "entity_id", registry, "LIST_ENTITY_REGISTRY_CURSORS",
                             lambda query="", **_: {"query": query.lower()}),
}
NAME_FIELD = {"list_automations": ("attributes", "friendly_name"), "search_entities": ("attributes", "friendly_name"),
              "list_devices": (None, "name"), "list_entity_registry": (None, "name")}


def raw(tool):
    # The handler itself, below the registry's routed wrapper.
    return inspect.unwrap(getattr(compatibility, tool))


def codec_of(tool):
    return getattr(compatibility, TOOLS[tool][4])


def source_id(tool, row):
    return row[TOOLS[tool][2]]


def rename(tool, row, value):
    parent, key = NAME_FIELD[tool]
    (row[parent] if parent else row)[key] = value


class ListHarness(unittest.IsolatedAsyncioTestCase):
    async def page(self, tool, source, *, size_limit=None, **arguments):
        mock = AsyncMock(return_value=deepcopy(source))
        settings = compatibility.SETTINGS
        if size_limit is not None:
            settings = replace(settings, response_size_limit=size_limit)
        with patch.object(compatibility, TOOLS[tool][0], new=mock), \
                patch.object(compatibility, "SETTINGS", settings):
            text = await raw(tool)(**arguments)
        self.assertLessEqual(len(text), settings.response_size_limit)
        return json.loads(text), mock

    async def refused(self, tool, source, code, reason, **arguments):
        mock = AsyncMock(return_value=deepcopy(source))
        with patch.object(compatibility, TOOLS[tool][0], new=mock):
            with self.assertRaises(GovernanceError) as caught:
                await raw(tool)(**arguments)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.details, {"feature": tool, "reason": reason})
        return mock

    async def walk(self, tool, source, *, limits=(100,), size_limit=None, **arguments):
        pages, cursor = [], ""
        while True:
            limit = limits[len(pages) % len(limits)]
            page, mock = await self.page(tool, source, size_limit=size_limit, limit=limit, cursor=cursor,
                                         **arguments)
            self.assertEqual(mock.await_count, 1)
            pages.append(page)
            if not page["has_more"]:
                return pages
            cursor = page["next_cursor"]
            self.assertLess(len(pages), 1000)

    def ids(self, tool, pages):
        _, row_key, identity, *_ = TOOLS[tool]
        return [row[identity] for page in pages for row in page[row_key]]

    async def first_cursor(self, tool, source, **arguments):
        page, _ = await self.page(tool, source, limit=3, **arguments)
        self.assertTrue(page["has_more"])
        return page["next_cursor"]


class WalkTests(ListHarness):
    async def test_every_match_appears_once_in_identity_order(self):
        for tool, (_, row_key, identity, fixture, *_rest) in TOOLS.items():
            with self.subTest(tool=tool):
                source = fixture(53)
                random.Random(425).shuffle(source)
                pages = await self.walk(tool, source, limits=(1, 7, 3, 20))
                expected = sorted(source_id(tool, row) for row in source)
                self.assertEqual(self.ids(tool, pages), expected)
                for page in pages:
                    rows = page[row_key]
                    self.assertEqual(page["total"], 53)
                    self.assertEqual(page["count"], len(rows))
                    self.assertEqual(page["returned"], len(rows))
                    self.assertIs(page["truncated"], page["has_more"])
                    self.assertNotIn("omitted_ids", page)
                for page in pages[:-1]:
                    self.assertEqual(page["page_limit_reason"], "limit")
                    self.assertIsInstance(page["next_cursor"], str)
                last = pages[-1]
                self.assertEqual((last["has_more"], last["next_cursor"], last["page_limit_reason"]),
                                 (False, None, None))

    async def test_filters_hold_across_the_walk(self):
        source = entity_states(60)
        pages = await self.walk("search_entities", source, limits=(4,), query=" E 1", domain="Sensor")
        expected = sorted(row["entity_id"] for row in source if row["entity_id"].startswith("sensor.")
                          and "e 1" in row["attributes"]["friendly_name"].casefold())
        self.assertEqual(self.ids("search_entities", pages), expected)
        self.assertEqual({page["total"] for page in pages}, {len(expected)})

    async def test_an_empty_match_set_is_one_final_page(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                page, _ = await self.page(tool, TOOLS[tool][3](5), query="no-such-synthetic-row")
                self.assertEqual(page[TOOLS[tool][1]], [])
                self.assertEqual((page["total"], page["has_more"], page["next_cursor"]), (0, False, None))


class FillTests(ListHarness):
    async def test_a_page_is_filled_to_the_budget_and_no_further(self):
        size_limit = 8192
        budget = size_limit - paging.ENVELOPE_RESERVE_CHARS
        for tool, (_, row_key, identity, fixture, *_rest) in TOOLS.items():
            with self.subTest(tool=tool):
                source = fixture(90, pad=300)
                pages = await self.walk(tool, source, size_limit=size_limit)
                self.assertEqual(self.ids(tool, pages), sorted(source_id(tool, row) for row in source))
                for page, following in zip(pages, pages[1:]):
                    self.assertEqual(page["page_limit_reason"], "response_size_limit")
                    size = len(compact(page))
                    self.assertLessEqual(size, budget)
                    # Maximal: the next row would not have fitted (cursor digits are the slack).
                    self.assertGreater(size + len(compact(following[row_key][0])) + 1, budget - 16)

    async def test_an_oversize_row_is_named_and_the_walk_continues(self):
        size_limit = 8192
        for tool, (_, row_key, identity, fixture, *_rest) in TOOLS.items():
            with self.subTest(tool=tool):
                source = fixture(40)
                rename(tool, source[17], "z" * 5000)
                pages = await self.walk(tool, source, size_limit=size_limit, limits=(10,))
                omitted = [ident for page in pages for ident in page.get("omitted_ids", [])]
                self.assertEqual(omitted, [source_id(tool, source[17])])
                carrier = next(page for page in pages if "omitted_ids" in page)
                self.assertEqual(carrier["omitted_oversized"], 1)
                seen = self.ids(tool, pages) + omitted
                self.assertEqual(sorted(seen), sorted(source_id(tool, row) for row in source))
                self.assertEqual(len(seen), len(set(seen)))

    async def test_a_limit_too_small_for_any_page_refuses(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                settings = replace(compatibility.SETTINGS,
                                   response_size_limit=paging.ENVELOPE_RESERVE_CHARS + 100)
                with patch.object(compatibility, TOOLS[tool][0], new=AsyncMock(return_value=TOOLS[tool][3](5))), \
                        patch.object(compatibility, "SETTINGS", settings):
                    with self.assertRaises(GovernanceError) as caught:
                        await raw(tool)(limit=3)
                self.assertEqual(caught.exception.code, ErrorCode.INTERNAL_SERVER_ERROR)
                self.assertEqual(caught.exception.details,
                                 {"feature": tool, "reason": "page_exceeds_response_limit"})


class BindingTests(ListHarness):
    async def test_a_case_only_filter_change_continues(self):
        cases = {
            "list_automations": ({"query": "A 1"}, {"query": "a 1"}),
            "search_entities": ({"query": " E 1 ", "domain": "SENSOR"}, {"query": "e 1", "domain": "sensor"}),
            "list_devices": ({"query": "DEVICE 1"}, {"query": "device 1"}),
            "list_entity_registry": ({"query": "S 1"}, {"query": "s 1"}),
        }
        for tool, (first, second) in cases.items():
            with self.subTest(tool=tool):
                source = TOOLS[tool][3](40)
                cursor = await self.first_cursor(tool, source, **first)
                page, _ = await self.page(tool, source, cursor=cursor, limit=3, **second)
                self.assertGreater(len(page[TOOLS[tool][1]]), 0)

    async def test_a_changed_filter_refuses_before_the_read(self):
        cases = {
            "list_automations": ({"query": "a 1"}, {"query": "a 2"}),
            "search_entities": ({"query": "e 1"}, {"query": "e 1", "domain": "light"}),
            "list_devices": ({"query": "device 1"}, {"query": ""}),
            "list_entity_registry": ({"query": "s 1"}, {"query": "s 10"}),
        }
        for tool, (first, second) in cases.items():
            with self.subTest(tool=tool):
                source = TOOLS[tool][3](40)
                cursor = await self.first_cursor(tool, source, **first)
                mock = await self.refused(tool, source, ErrorCode.INVALID_CURSOR, "query_mismatch",
                                          cursor=cursor, **second)
                mock.assert_not_awaited()

    async def test_a_reordered_source_continues(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                source = TOOLS[tool][3](20)
                cursor = await self.first_cursor(tool, source)
                page, _ = await self.page(tool, list(reversed(source)), cursor=cursor, limit=3)
                self.assertEqual([row[TOOLS[tool][2]] for row in page[TOOLS[tool][1]]],
                                 sorted(source_id(tool, row) for row in source)[3:6])

    async def test_the_payload_holds_positions_and_keyed_digests_only(self):
        filters = {"list_automations": {"query": "automation.a00"}, "search_entities": {"query": "sensor.e00"},
                   "list_devices": {"query": "device 1"}, "list_entity_registry": {"query": "sensor.s00"}}
        for tool, arguments in filters.items():
            with self.subTest(tool=tool):
                source = TOOLS[tool][3](40)
                cursor = await self.first_cursor(tool, source, **arguments)
                codec = codec_of(tool)
                payload = codec.decode(cursor)
                self.assertEqual(set(payload), paging.PAYLOAD_FIELDS)
                self.assertEqual(payload["v"], paging.CURSOR_VERSION)
                self.assertEqual(payload["o"], 3)
                self.assertEqual(payload["q"], codec.fingerprint(TOOLS[tool][5](**arguments)))
                body = base64.urlsafe_b64decode(cursor.split(".")[0] + "==").decode("ascii")
                self.assertNotIn(arguments["query"], body)
                for row in source:
                    self.assertNotIn(source_id(tool, row), body)


class RefusalTests(ListHarness):
    async def test_a_damaged_or_foreign_cursor_refuses_before_the_read(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                source = TOOLS[tool][3](20)
                cursor = await self.first_cursor(tool, source)
                body, signature = cursor.split(".")
                other = next(name for name in TOOLS if name != tool)
                foreign = await self.first_cursor(other, TOOLS[other][3](20))
                same_key_other_feature = CursorCodec("list_execution_tasks", key=codec_of(tool)._key).encode(
                    codec_of(tool).decode(cursor))
                for damaged in (
                    body + "." + signature[:-2] + ("AA" if signature[-2:] != "AA" else "BB"),
                    body[:-2] + "." + signature,
                    body,
                    foreign,
                    same_key_other_feature,
                    "x" * (paging.MAX_CURSOR_CHARS + 1),
                    5,
                    None,
                ):
                    mock = await self.refused(tool, source, ErrorCode.INVALID_CURSOR, "integrity_or_format_invalid",
                                              cursor=damaged)
                    mock.assert_not_awaited()

    async def test_an_expired_cursor_is_stale_before_the_filter_check(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                source = TOOLS[tool][3](20)
                cursor = await self.first_cursor(tool, source)
                later = time.time() + CURSOR_TTL_SECONDS + 5
                with patch.object(codec_of(tool), "_clock", lambda: later):
                    mock = await self.refused(tool, source, ErrorCode.STALE_CURSOR, "cursor_expired",
                                              cursor=cursor, query="a different filter")
                mock.assert_not_awaited()

    async def test_a_forged_payload_signed_with_the_real_key_refuses(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                codec = codec_of(tool)
                good = {"v": 1, "q": codec.fingerprint(TOOLS[tool][5]()), "o": 1, "s": "0" * 32}
                for forged in (
                    {key: value for key, value in good.items() if key != "s"},
                    {key: value for key, value in good.items() if key != "o"},
                    {**good, "z": 1},
                    {**good, "v": 2},
                    {**good, "v": True},
                    {**good, "v": "1"},
                    {**good, "o": -1},
                    {**good, "o": "3"},
                    {**good, "o": True},
                    {**good, "o": 1.0},
                    {**good, "q": "g" * 32},
                    {**good, "q": "ABCDEF" * 5 + "AB"},
                    {**good, "q": 5},
                    {**good, "s": "0" * 31},
                    {**good, "s": "0" * 33},
                ):
                    mock = await self.refused(tool, TOOLS[tool][3](5), ErrorCode.INVALID_CURSOR,
                                              "integrity_or_format_invalid", cursor=codec.encode(forged))
                    mock.assert_not_awaited()

    async def test_an_offset_at_or_past_the_end_refuses_after_the_read(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                codec = codec_of(tool)
                source = TOOLS[tool][3](6)
                digest = paging.set_digest(codec, sorted(source_id(tool, row) for row in source))
                for offset in (6, 7):
                    forged = codec.encode({"v": 1, "q": codec.fingerprint(TOOLS[tool][5]()), "o": offset,
                                           "s": digest})
                    mock = await self.refused(tool, source, ErrorCode.INVALID_CURSOR,
                                              "integrity_or_format_invalid", cursor=forged)
                    self.assertEqual(mock.await_count, 1)

    async def test_a_restart_key_refuses(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                source = TOOLS[tool][3](20)
                cursor = await self.first_cursor(tool, source)
                with patch.object(compatibility, TOOLS[tool][4], CursorCodec(tool)):
                    mock = await self.refused(tool, source, ErrorCode.INVALID_CURSOR,
                                              "integrity_or_format_invalid", cursor=cursor)
                mock.assert_not_awaited()


class ContinuityTests(ListHarness):
    async def test_a_membership_change_breaks_the_walk(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                source = TOOLS[tool][3](20)
                cursor = await self.first_cursor(tool, source)
                added = source + TOOLS[tool][3](21)[20:]
                removed = source[:10] + source[11:]
                renamed = deepcopy(source)
                identity = TOOLS[tool][2]
                renamed[15][identity] = renamed[15][identity] + "x"
                for changed in (added, removed, renamed):
                    mock = await self.refused(tool, changed, ErrorCode.STALE_CURSOR, "list_continuity_broken",
                                              cursor=cursor)
                    self.assertEqual(mock.await_count, 1)

    async def test_a_value_only_change_continues_with_the_new_value(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                source = TOOLS[tool][3](20)
                cursor = await self.first_cursor(tool, source)
                changed = deepcopy(source)
                target = sorted(changed, key=lambda row: source_id(tool, row))[4]
                rename(tool, target, "A 4 renamed")
                page, _ = await self.page(tool, changed, cursor=cursor, limit=3)
                rows = {row[TOOLS[tool][2]]: row for row in page[TOOLS[tool][1]]}
                shown = rows[source_id(tool, target)]
                self.assertEqual(shown.get("friendly_name", shown.get("name")), "A 4 renamed")

    def test_the_set_digest_is_keyed_canonical_and_order_bound(self):
        first, second = CursorCodec("synthetic"), CursorCodec("synthetic")
        self.assertEqual(paging.set_digest(first, ["a", "b"]), paging.set_digest(first, ["a", "b"]))
        self.assertNotEqual(paging.set_digest(first, ["a", "b"]), paging.set_digest(second, ["a", "b"]))
        self.assertNotEqual(paging.set_digest(first, ["a,b"]), paging.set_digest(first, ["a", "b"]))
        self.assertNotEqual(paging.set_digest(first, ["b", "a"]), paging.set_digest(first, ["a", "b"]))
        self.assertRegex(paging.set_digest(first, []), r"^[0-9a-f]{32}$")


class SourceTests(ListHarness):
    async def test_each_page_is_one_read_with_the_original_arguments(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                source = TOOLS[tool][3](10)
                calls, cursor = [], ""
                while True:
                    page, mock = await self.page(tool, source, limit=4, cursor=cursor)
                    self.assertEqual(mock.await_count, 1)
                    calls.append(mock.await_args)
                    if not page["has_more"]:
                        break
                    cursor = page["next_cursor"]
                self.assertEqual(len(calls), 3)
                self.assertTrue(all(call == calls[0] for call in calls))
        expected = {"list_devices": {"type": "config/device_registry/list"},
                    "list_entity_registry": {"type": "config/entity_registry/list"}}
        for tool, payload in expected.items():
            _, mock = await self.page(tool, TOOLS[tool][3](2))
            mock.assert_awaited_once_with(payload)

    async def test_a_malformed_source_fails_the_read(self):
        for tool in TOOLS:
            for value in ({"entity_id": "x"}, None, "synthetic"):
                with self.subTest(tool=tool, value=value):
                    with patch.object(compatibility, TOOLS[tool][0], new=AsyncMock(return_value=value)):
                        with self.assertRaises(HomeAssistantApiError):
                            await raw(tool)()

    async def test_a_row_without_its_identity_fails_the_read(self):
        bad_rows = {
            "list_automations": [{"state": "on"}, {"entity_id": 5}, "row", 7],
            "list_devices": [{"name": "Device"}, {"id": 5}, {"id": None}, "row"],
            "list_entity_registry": [{"platform": "test"}, {"entity_id": None}, "row"],
        }
        for tool, rows in bad_rows.items():
            for row in rows:
                with self.subTest(tool=tool, row=row):
                    source = TOOLS[tool][3](3) + [row]
                    with patch.object(compatibility, TOOLS[tool][0], new=AsyncMock(return_value=source)):
                        with self.assertRaises(HomeAssistantApiError) as caught:
                            await raw(tool)()
                    self.assertEqual(caught.exception.details["status"], "malformed_response")

    async def test_search_entities_still_skips_unreadable_rows(self):
        source = entity_states(3) + [{"state": "on"}, {"entity_id": "Not.Canonical"}, "row", 7]
        page, _ = await self.page("search_entities", source)
        self.assertEqual(self.ids("search_entities", [page]), sorted(row["entity_id"] for row in source[:3]))
        self.assertEqual(page["total"], 3)


def task(index, created_at, plan_id="a" * 32, note=""):
    return SimpleNamespace(task_id=f"{index:032x}", created_at=created_at, state=SimpleNamespace(value="created"),
                           terminal_outcome="", plan_id=plan_id, note=note)


class FakeTaskService:
    list_execution_tasks = ChangeGovernanceService.list_execution_tasks

    def __init__(self, tasks):
        self.tasks = list(tasks)
        self.reads = 0
        self.projected = []
        self.task_repository = SimpleNamespace(list=self._list)

    def _list(self):
        self.reads += 1
        return list(self.tasks)

    def _dispatch_facts(self, task):
        return SimpleNamespace(public_fields=lambda: {})

    def _public_task(self, task, *, include_events):
        assert include_events is False
        self.projected.append(task.task_id)
        return {"task_id": task.task_id, "created_at": task.created_at, "plan_id": task.plan_id,
                "note": task.note}


class ExecutionTaskTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(governance_service, "dispatch_qualify_counters", lambda summary, facts: summary)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tasks(self):
        stamps = ["2026-01-01T00:00:05Z", "2026-01-01T00:00:01Z", "2026-01-01T00:00:05Z",
                  "2026-01-01T00:00:03Z", "2026-01-01T00:00:01Z", "2026-01-01T00:00:09Z", "2026-01-01T00:00:03Z"]
        return [task(index, stamp) for index, stamp in enumerate(stamps)]

    def walk(self, service, **arguments):
        pages, cursor = [], ""
        while True:
            page = service.list_execution_tasks(cursor=cursor, **arguments)
            pages.append(page)
            if not page["has_more"]:
                return pages
            cursor = page["next_cursor"]

    def test_tasks_page_newest_first_with_task_id_breaking_ties(self):
        tasks = self.tasks()
        service = FakeTaskService(reversed(tasks))
        pages = self.walk(service, limit=2)
        order = [row["task_id"] for page in pages for row in page["tasks"]]
        expected = [item.task_id for item in sorted(tasks, key=lambda item: (item.created_at, -int(item.task_id, 16)),
                                                     reverse=True)]
        self.assertEqual(order, expected)
        self.assertEqual(len(pages), 4)
        for page in pages:
            self.assertEqual(set(page), {"count", "tasks", "returned", "total", "has_more", "next_cursor",
                                         "page_limit_reason"})
            self.assertEqual(page["count"], len(page["tasks"]))
            self.assertEqual(page["total"], len(tasks))

    def test_only_the_page_slice_is_projected(self):
        service = FakeTaskService(self.tasks())
        page = service.list_execution_tasks(limit=3)
        self.assertEqual(service.projected, [row["task_id"] for row in page["tasks"]])
        self.assertEqual(len(service.projected), 3)

    def test_a_changed_filter_refuses_before_the_repository_read(self):
        tasks = [task(index, f"2026-01-01T00:00:{index:02d}Z", plan_id="b" * 32) for index in range(5)]
        service = FakeTaskService(tasks)
        cursor = service.list_execution_tasks(limit=2, plan_id="b" * 32)["next_cursor"]
        reads = service.reads
        with self.assertRaises(GovernanceError) as caught:
            service.list_execution_tasks(limit=2, plan_id="c" * 32, cursor=cursor)
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_CURSOR)
        self.assertEqual(caught.exception.details, {"feature": "list_execution_tasks", "reason": "query_mismatch"})
        self.assertEqual(service.reads, reads)

    def test_a_new_task_breaks_the_walk(self):
        service = FakeTaskService(self.tasks())
        cursor = service.list_execution_tasks(limit=2)["next_cursor"]
        service.tasks.append(task(99, "2026-01-01T00:00:10Z"))
        with self.assertRaises(GovernanceError) as caught:
            service.list_execution_tasks(limit=2, cursor=cursor)
        self.assertEqual(caught.exception.code, ErrorCode.STALE_CURSOR)
        self.assertEqual(caught.exception.details["reason"], "list_continuity_broken")

    def test_no_budget_means_no_size_bound(self):
        tasks = [task(index, f"2026-01-01T00:00:{index:02d}Z", note="n" * 20_000) for index in range(5)]
        page = FakeTaskService(tasks).list_execution_tasks(limit=5)
        self.assertEqual(len(page["tasks"]), 5)
        self.assertEqual((page["has_more"], page["page_limit_reason"]), (False, None))

    def test_a_budget_bounds_the_page(self):
        tasks = [task(index, f"2026-01-01T00:00:{index:02d}Z", note="n" * 900) for index in range(10)]
        service = FakeTaskService(tasks)
        pages = self.walk(service, limit=10, page_budget_chars=4000)
        self.assertGreater(len(pages), 2)
        for page in pages[:-1]:
            self.assertLessEqual(len(compact(page)), 4000)
            self.assertEqual(page["page_limit_reason"], "response_size_limit")
        self.assertEqual(sum(len(page["tasks"]) for page in pages), 10)


class EnvelopeReserveTests(unittest.IsolatedAsyncioTestCase):
    """The real envelopes stay well inside ENVELOPE_RESERVE_CHARS on a full page."""

    # The longest request id the pattern admits.
    REQUEST_ID = "req-" + "a" * 124

    async def asyncSetUp(self):
        telemetry, self.token = begin_request(self.REQUEST_ID)
        self.assertEqual(telemetry.request_id, self.REQUEST_ID)
        patcher = patch.object(governance_service, "dispatch_qualify_counters", lambda summary, facts: summary)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        end_request(self.token)

    def assert_reserved(self, text, row_key, size_limit):
        self.assertLessEqual(len(text), size_limit)
        envelope = json.loads(text)
        self.assertIs(envelope["success"], True)
        self.assertEqual(envelope["request_id"], self.REQUEST_ID)
        self.assertNotIn("response_completeness", envelope)
        data = envelope["data"]
        self.assertNotIn("response_completeness", data)
        self.assertEqual(data["page_limit_reason"], "response_size_limit")
        self.assertLessEqual(len(compact(data)), size_limit - paging.ENVELOPE_RESERVE_CHARS)
        overhead = len(compact(envelope)) - len(compact(data))
        self.assertLess(overhead, paging.ENVELOPE_RESERVE_CHARS // 2)
        self.assertFalse(any("exceeded the size limit" in warning for warning in envelope["warnings"]))
        return data

    async def test_the_dispatcher_envelope_never_trims_a_full_page(self):
        size_limit = compatibility.SETTINGS.response_size_limit
        for tool, (call, row_key, identity, fixture, *_rest) in TOOLS.items():
            with self.subTest(tool=tool):
                source = fixture(150, pad=700)
                with patch.object(compatibility, call, new=AsyncMock(return_value=source)):
                    text = await CanonicalProviderDispatcher().execute(
                        tool, lambda: raw(tool)(limit=100), arguments={"limit": 100}, response_limit=size_limit)
                data = self.assert_reserved(text, row_key, size_limit)
                self.assertEqual([row[identity] for row in data[row_key]],
                                 sorted(source_id(tool, row) for row in source)[: len(data[row_key])])

    async def test_a_dispatcher_refusal_names_the_reason_not_the_cursor(self):
        bogus = "SyntheticBogusCursorText.SyntheticSignature"
        for tool in TOOLS:
            with self.subTest(tool=tool):
                mock = AsyncMock(return_value=TOOLS[tool][3](3))
                with patch.object(compatibility, TOOLS[tool][0], new=mock):
                    text = await CanonicalProviderDispatcher().execute(
                        tool, lambda: raw(tool)(cursor=bogus), arguments={"cursor": bogus}, response_limit=60_000)
                envelope = json.loads(text)
                self.assertIs(envelope["success"], False)
                self.assertEqual(envelope["error_code"], "invalid_cursor")
                self.assertEqual(envelope["details"]["reason"], "integrity_or_format_invalid")
                self.assertNotIn("SyntheticBogus", text)
                mock.assert_not_awaited()

    async def test_run_structured_never_trims_a_full_task_page(self):
        size_limit = compatibility.SETTINGS.response_size_limit
        tasks = [task(index, f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}Z", note="n" * 2000)
                 for index in range(120)]
        service = FakeTaskService(tasks)
        with patch.object(governance_tools, "GOVERNANCE", SimpleNamespace(require=lambda: service)):
            text = await governance_tools.list_execution_tasks(limit=100)
        data = self.assert_reserved(text, "tasks", size_limit)
        self.assertGreater(len(data["tasks"]), 0)

    async def test_run_structured_refusal_names_the_reason_not_the_cursor(self):
        service = FakeTaskService([task(1, "2026-01-01T00:00:01Z")])
        with patch.object(governance_tools, "GOVERNANCE", SimpleNamespace(require=lambda: service)):
            text = await governance_tools.list_execution_tasks(cursor="SyntheticBogusCursorText.Sig")
        envelope = json.loads(text)
        self.assertEqual(envelope["error_code"], "invalid_cursor")
        self.assertEqual(envelope["details"]["reason"], "integrity_or_format_invalid")
        self.assertNotIn("SyntheticBogus", text)
        self.assertEqual(service.reads, 0)


if __name__ == "__main__":
    unittest.main()
