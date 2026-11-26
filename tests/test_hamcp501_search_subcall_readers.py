"""HAMCP-501 W4: the gateway's native search sub-call page readers.

``UpstreamReadGateway.search_subcall_reader`` serves the W3 scan
(``search.subcalls``) one fixed ``ha_search`` page at a time over the admitted
9.2.0 Row 25 route (HAMCP-481 design sections 2.4 to 2.6; decisions D1 to D3
as relayed by the architect). Everything is synthetic and offline: the
committed 9.2.0 catalog, the receipt transport of ``support.ha_mcp_captures``
and the synthetic Core of ``tests.test_hamcp481_core_reads``. W6 wires the
readers to native ``ha_search`` through the application's no-I/O composition
and keeps the route internal-only (G1); these tests drive the readers
directly and through ``search.engine``
(``tests.test_hamcp501_native_search_cutover`` drives the registered tool).
"""

from __future__ import annotations

import ast
import asyncio
import contextvars
import dataclasses
import json
from pathlib import Path
import sys
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]

from jsonschema import Draft202012Validator  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from tests import test_hamcp481_native_search_engine as w3  # noqa: E402
from tests.test_hamcp481_native_search_engine import MARKER, World, reasons_of  # noqa: E402
from support import frozen_foreign_releases as foreign  # noqa: E402
from support.ha_mcp_920_receipts import result as receipt_result, with_content  # noqa: E402
from support.ha_mcp_captures import capture, gateway_920  # noqa: E402
from ha_mcp_engineering.clients.mcp import DashboardTransportError  # noqa: E402
from ha_mcp_engineering.models import responses  # noqa: E402
from ha_mcp_engineering.providers import upstream_read_gateway as gw  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2 as reads_9_2  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2_keys as k  # noqa: E402
from ha_mcp_engineering.request_context import current_telemetry  # noqa: E402
from ha_mcp_engineering.search import reply, subcalls  # noqa: E402

PACKAGE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering"
LADDER = subcalls.PAGE_SIZE_LADDER
# HAMCP-501 W6 S1: every type pages from the ladder's first size, and steps
# down only for a page the reply size bound alone refused.
PAGE = LADDER[0]
Failure = gw.SearchSubcallPageFailure
# A synthetic endpoint whose parts occur in no result: the default test URL's
# host would mask Engineering's own ``upstream_*`` keys and trip D2.
HOST, SEGMENT = "qzsynthhost", "qzsynthsegmentnotreal"
URL = f"http://{HOST}:9583/{SEGMENT}/mcp"
# 9.2.0 ``_new_search_response`` adds this warning whenever search_types is
# explicit, which a sub-call page always is.
SKIP = ("entity search skipped: explicit search_types selects "
        "configuration-only search; omit search_types to search entities.")
AUDIT_KEYS = {"event", "request_id", "parent_request_id", "provider", "upstream_tool", "search_type",
              "offset", "limit", "upstream_version", "contract_fingerprint", "result_status", "failure_category",
              "provider_dispatch_count", "fallback_occurred"}


def body(search_type, rows, *, offset=0, total=None, **changes):
    """One 9.2.0 ``ha_search`` page as ``_apply_search_outcome`` builds it."""

    total = offset + len(rows) if total is None else total
    more = offset + len(rows) < total
    following = offset + len(rows) if more else None
    value = {"success": True, "query": "kitchen", "entities": [], "entity_total_matches": 0,
             "automations": [], "scripts": [], "scenes": [], "helpers": [],
             "search_types": [search_type], "config_total_matches": total, "partial": False,
             "errors": [], "warnings": [SKIP], "count": len(rows), "offset": offset,
             "limit": PAGE, "config_has_more": more, "config_next_offset": following,
             "has_more": more, "next_offset": following, subcalls.BUCKETS[search_type]: list(rows)}
    value.update(changes)
    return value


def reply_of(content):
    """The upstream call result carrying ``content`` (the committed envelope)."""

    return with_content(receipt_result("r25-search"), content)


def pages_of(search_type, rows, *, total=None, **changes):
    total = len(rows) if total is None else total
    size = PAGE
    return [reply_of(body(search_type, rows[start:start + size], offset=start, total=total, **changes))
            for start in range(0, max(len(rows), 1), size)]


def board(index, score=80):
    return {"url_path": f"dash-{index}", "title": f"Kitchen board {index}", "score": score,
            "match_in_config": True, "match_in_references": False}


def flow(index, score=90, **changes):
    return {"entity_id": f"sensor.kitchen_flow_{index}", "helper_type": "template",
            "name": f"Kitchen flow {index}", "score": score, "match_in_name": True,
            "match_in_config": False, "match_in_references": False, **changes}


def arguments(search_type="helper", page=0, query="kitchen", exact_match=True, size=PAGE):
    return subcalls.page_arguments(search_type, query, exact_match, page, size)


def refused(error):
    """A 9.2.0 ``raise_tool_error`` reply: an MCP error whose one text block is
    the ``create_error_response`` JSON (``tools/helpers.py``, 51e3aac7)."""

    return {"isError": True, "content": [{"type": "text", "text": json.dumps(error)}]}


# ``visibility/enforcement.py`` ``_raise_enforced`` (51e3aac7): enforce mode
# refuses a call it cannot scan or that embeds a hidden id.
ENFORCED = {"success": False, "error": {
    "code": "ENTITY_VISIBILITY_ENFORCED",
    "message": ("Entity visibility enforce mode is active on this Home Assistant MCP server. "
                f"{MARKER} No data was returned."),
    "suggestion": ("The entity is restricted by the entity visibility filter's enforce mode; "
                   "do not retry the same request."),
    "suggestions": [
        ("The entity is restricted by the entity visibility filter's enforce mode; "
         "do not retry the same request."),
        ("If the user wants this data, they must adjust or turn off enforce mode in the "
         "ha-mcp settings UI (Entity Visibility tab)."),
    ]}, "tool_name": "ha_search", "visibility_enforced": True}
# ``_conceal_as_not_found``: an exact hidden id is concealed as absent
# (``create_entity_not_found_error``).
CONCEALED = {"success": False, "error": {
    "code": "ENTITY_NOT_FOUND", "message": f"Entity '{MARKER}' not found",
    "details": f"No entity with ID '{MARKER}' exists in Home Assistant",
    "suggestion": "Use ha_search() to find correct entity ID",
    "suggestions": ["Use ha_search() to find correct entity ID", "Verify the entity exists in Home Assistant",
                    "Check for typos in the entity ID"]}, "entity_id": MARKER}
# ``smart_search/_deep.py`` ``_apply_per_type_partial_flag`` (51e3aac7), which
# ``search/response.py`` ``_mirror_partial_to_warnings`` also warns.
UNSEARCHED = ("1 custom helper integration entry not scanned (a search opens no options flow for a "
              "helper flow that is not Home Assistant's own, so their config was not searched) — their "
              "match status is unknown; this result is not exhaustive.")


PAIR_FILE = ROOT / "docs/evidence/hamcp371/sr/d6-search-pair-9.2.0.json"


def unified_catalog():
    """The committed 9.2.0 catalog with D6's trimmed ``unified`` ha_search
    descriptor, the variant a component with unified search advertises."""

    unified = json.loads(PAIR_FILE.read_text(encoding="utf-8"))["variants"]["unified"]["descriptor"]
    return [unified if tool["name"] == "ha_search" else tool for tool in capture("9.2.0")["tools"]]


class Readers(World):
    """The 9.2.0 gateway's page readers over the receipt transport."""

    # HAMCP-501 W6: the subclasses below rerun these readers under the
    # ``unified`` descriptor.
    UNIFIED = False

    async def asyncSetUp(self):
        self.gateway, self.transport = await gateway_920(
            tools=unified_catalog() if self.UNIFIED else None, url=URL)
        self.audit = Mock()
        self.served: dict = {}
        # HAMCP-501 W6 S1: rows each type's pages are sliced from, by their
        # own offset and limit, as 9.2.0 pages.
        self.table: dict = {}
        self.children: list = []
        self.before = self.after = None
        original = self.transport.execute_read

        async def respond(tool, arguments, **kwargs):
            self.children.append(current_telemetry())
            if tool == "ha_search":
                search_type, offset = arguments["search_types"][0], arguments["offset"]
                limit = arguments["limit"]
                self.transport.override = self.served.get(
                    (search_type, offset, limit), self.served.get(
                        (search_type, offset), self.table_page(search_type, offset, limit)))
            else:
                self.transport.override = None
            if self.before is not None:
                self.before()
            try:
                return await original(tool, arguments, **kwargs)
            finally:
                if self.after is not None:
                    self.after()

        self.transport.execute_read = respond
        self.reader = self.gateway.search_subcall_reader(audit=self.audit)
        self.readers = self.reader.readers

    def table_page(self, search_type, offset, limit):
        if search_type not in self.table:
            return reply_of(body(search_type, [], offset=offset))
        rows = self.table[search_type]
        return reply_of(body(search_type, rows[offset:offset + limit], offset=offset,
                             total=len(rows), limit=limit))

    def serve(self, search_type, pages):
        for index, raw in enumerate(pages):
            self.served[(search_type, index * PAGE)] = raw

    def sent(self):
        return [(name, value) for name, value in self.transport.sent]

    def audits(self):
        return [record.args[0] for record in self.audit.write.call_args_list]

    async def failed(self, category, search_type="helper", page=0, size=PAGE):
        with self.assertRaises(Failure) as raised:
            await self.readers[search_type](arguments(search_type, page, size=size))
        self.assertEqual(raised.exception.category, category)
        self.assertEqual(raised.exception.args, (category,))
        return raised.exception


class ReaderShapeTests(Readers):
    async def test_one_fixed_reader_per_sub_call(self):
        reader = self.reader
        self.assertEqual(set(reader.readers), set(subcalls.SUBCALL_TYPES))
        self.assertEqual(set(reader.readers), set(subcalls.BUCKETS))
        with self.assertRaises(TypeError):
            reader.readers["automation"] = reader.readers["helper"]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            reader.current = lambda: True
        self.assertTrue(reader.current())
        # HAMCP-501 W6 (G1): the Row 25 route is internal-only, never listed.
        self.assertEqual([route for route in self.gateway._exposed.values()
                          if route.entry.upstream_name == "ha_search"], [])
        (route,) = [route for route in self.gateway._internal.values()
                    if route.entry.upstream_name == "ha_search"]
        self.assertEqual((reader.version, reader.contract), (route.server_version, route.contract_fingerprint))
        self.assertEqual(reader.version, "9.2.0")
        self.assertTrue(reader.provenance.endswith("Row 25 projection; no fallback"))
        self.assertEqual(self.transport.calls, 0)

    async def test_a_page_is_the_projected_row_25_page(self):
        self.serve("helper", [reply_of(body("helper", [flow(0), flow(1, entry_id="01JSYNTHETIC")]))])
        page = await self.readers["helper"](arguments())
        self.assertIsInstance(page, subcalls.PageResult)
        self.assertEqual(page.rows, (flow(0), flow(1)))
        self.assertEqual((page.partial, page.has_more, page.config_total_matches), (False, False, 2))
        # D1: the names Row 25 withholds, held in memory; never their values.
        self.assertEqual(page.withheld_names, frozenset({"entry_id"}))
        self.assertNotIn("01JSYNTHETIC", repr(page))
        self.assertTrue(subcalls._page_valid("helper", page, PAGE))
        self.serve("dashboard", [reply_of(body("dashboard", [board(0), board(1)]))])
        page = await self.readers["dashboard"](arguments("dashboard"))
        # Row 25 withholds a 9.2.0 dashboard's url_path and title (disclosed
        # for W6): a row keeps its score and match flags only.
        self.assertEqual(page.rows, tuple({"score": 80, "match_in_config": True,
                                           "match_in_references": False} for _ in range(2)))
        self.assertEqual(page.withheld_names, frozenset({"url_path", "title"}))
        self.assertNotIn("dash-0", repr(page))
        self.assertTrue(subcalls._page_valid("dashboard", page, PAGE))
        self.assertEqual(self.sent(), [("ha_search", arguments()), ("ha_search", arguments("dashboard"))])

    async def test_withheld_names_are_bucket_record_names_only(self):
        # A top-level name Row 25 does not admit is dropped by the projection,
        # not counted: only record names of the requested bucket count.
        self.serve("helper", [reply_of(body("helper", [flow(0)], partial_reason="synthetic bounded",
                                            total_matches=1))])
        page = await self.readers["helper"](arguments())
        self.assertEqual(page.withheld_names, frozenset())
        self.assertEqual(page.rows, (flow(0),))
        for payload, bucket in (({"helpers": [flow(0)]}, "dashboards"), ({}, "helpers"),
                                ({"helpers": {}}, "helpers"), ([], "helpers"), ({"helpers": []}, "entities"),
                                ({"entities": []}, "entities")):
            with self.subTest(payload=payload, bucket=bucket), self.assertRaises(ValueError):
                k.bucket_withheld_names(payload, bucket)
        self.assertEqual(k.bucket_withheld_names({"helpers": [{**flow(0), "entry_id": "x"}]}, "helpers"),
                         frozenset({"entry_id"}))
        # HAMCP-501 W6 S1 b+ (approved by Josh as relayed by the architect):
        # the automation, script and scene buckets count the same way.
        for bucket in ("automations", "scripts", "scenes"):
            with self.subTest(bucket=bucket):
                self.assertEqual(k.bucket_withheld_names({bucket: []}, bucket), frozenset())
                self.assertEqual(k.bucket_withheld_names({bucket: [{"entity_id": "x", "config": None}],
                                                          "helpers": [{"entry_id": "y"}]}, bucket),
                                 frozenset({"config"}))

    async def test_an_entity_type_page_is_the_projected_row_25_page(self):
        # HAMCP-501 W6 S1 b+: the automation, script and scene sub-calls. An
        # explicit search_types makes a 9.2.0 page config-only
        # (``tools/tools_search.py`` line 363, sha256 95a418bb...), so registry
        # search is not eligible (``search/response.py`` line 337, sha256
        # fe776978...). Both the component (``search/component.py`` lines 577
        # and 580, sha256 bf14215b...) and legacy (``search/legacy.py`` lines
        # 87 and 112, sha256 d202c015...) paths then keep the empty entities
        # bucket ``_new_search_response`` builds (``response.py`` line 617), so
        # the gateway's check that every other bucket is empty accepts a live
        # page. The rows are ``_normalize_component_config_record``'s keys.
        rows = {
            "automation": {"entity_id": "automation.kitchen_lights", "friendly_name": "Kitchen lights",
                           "score": 100, "match_in_name": True, "match_in_config": False,
                           "match_in_references": False},
            "script": {"entity_id": "script.kitchen_reset", "script_id": "kitchen_reset",
                       "friendly_name": "Kitchen reset", "score": 80, "match_in_name": False,
                       "match_in_config": True, "match_in_references": False},
            "scene": {"entity_id": "scene.kitchen_dim", "scene_id": None, "friendly_name": "Kitchen dim",
                      "score": 100, "match_in_name": True, "match_in_config": False,
                      "match_in_references": False},
        }
        for search_type, row in rows.items():
            with self.subTest(search_type=search_type):
                self.serve(search_type, [reply_of(body(search_type, [row]))])
                page = await self.readers[search_type](arguments(search_type))
                self.assertEqual(page.rows, (row,))
                self.assertEqual((page.partial, page.has_more, page.config_total_matches), (False, False, 1))
                self.assertEqual(page.withheld_names, frozenset())
                self.assertTrue(subcalls._page_valid(search_type, page, PAGE))
                # A key Row 25 does not admit is withheld and counted, never kept.
                extra = {**row, "config": {"alias": MARKER}}
                self.serve(search_type, [reply_of(body(search_type, [extra]))])
                page = await self.readers[search_type](arguments(search_type))
                self.assertEqual((page.rows, page.withheld_names), ((row,), frozenset({"config"})))
                self.assertNotIn(MARKER, repr(page))
                # A page with a non-empty entities bucket is not a sub-call page.
                entity = {"entity_id": "light.kitchen", "friendly_name": "Kitchen", "score": 100}
                self.serve(search_type, [reply_of(body(search_type, [row], entities=[entity],
                                                       entity_total_matches=1))])
                await self.failed("invalid_response", search_type)
                # So is a page that fills another sub-call's bucket.
                self.serve(search_type, [reply_of(body(search_type, [row], helpers=[flow(0)]))])
                await self.failed("invalid_response", search_type)
        self.assertEqual(self.sent(), [("ha_search", arguments(search_type))
                                       for search_type in rows for _ in range(4)])


class EngineTests(Readers):
    """The W3 engine over the gateway's readers: the scan pages, merges and
    discloses exactly as with injected readers."""

    async def test_the_helper_scan_pages_through_the_gateway(self):
        # HAMCP-501 W6 S1: a normal install is one call per 150 rows; the
        # second page's rows rank first.
        rows = [flow(index, score=95 if index >= PAGE else 90 - index % 5)
                for index in range(PAGE + 32)]
        self.serve("helper", pages_of("helper", rows))
        pages = -(-len(rows) // PAGE)
        self.assertGreater(pages, 1)
        result = await self.go(query="kitchen", search_types=["helper"])
        self.assertEqual(self.sent(), [("ha_search", arguments(page=page)) for page in range(pages)])
        self.assertEqual(result["config_total_matches"], PAGE + 32)
        self.assertEqual(len(result["helpers"]), 10)
        self.assertEqual([row["score"] for row in result["helpers"]], [95] * 10)
        self.assertEqual(reasons_of(result), {("paged", "config", "more_matches"): PAGE + 22})
        audits = self.audits()
        self.assertEqual([record["offset"] for record in audits], [page * PAGE for page in range(pages)])
        self.assertEqual([record["limit"] for record in audits], [PAGE] * pages)
        for record in audits:
            self.assertEqual(set(record), AUDIT_KEYS)
            self.assertEqual({key: record[key] for key in ("event", "parent_request_id", "provider",
                                                           "upstream_tool", "search_type", "result_status",
                                                           "failure_category", "provider_dispatch_count",
                                                           "fallback_occurred", "upstream_version")},
                             {"event": "search_subcall_page_read",
                              "parent_request_id": self.telemetry.request_id, "provider": gw.PROVIDER_ID,
                              "upstream_tool": "ha_search", "search_type": "helper",
                              "result_status": "success", "failure_category": None,
                              "provider_dispatch_count": 1, "fallback_occurred": False,
                              "upstream_version": "9.2.0"})
        self.assertEqual(len({record["request_id"] for record in audits} | {self.telemetry.request_id}),
                         pages + 1)
        for child in self.children:
            self.assertIsNot(child, self.telemetry)
            self.assertEqual(child.tool_name, "search_subcall_page_read")
            self.assertEqual(child.caller_id, "synthetic_authenticated_caller")
            self.assertEqual(child.audit_context["parent_request_id"], self.telemetry.request_id)
            self.assertEqual(child.upstream_active_requests, 0)
        self.assertIs(current_telemetry(), self.telemetry)

    async def test_storage_rows_are_dropped_and_withheld_names_counted(self):
        storage = flow(0, entity_id="input_boolean.kitchen_mode", helper_type="input_boolean",
                       name="Kitchen mode")
        self.serve("helper", [reply_of(body("helper", [storage, flow(1, entry_id="01JSYNTHETIC")]))])
        result = await self.go(query="kitchen", search_types=["helper"])
        self.assertEqual([row["entity_id"] for row in result["helpers"]], ["sensor.kitchen_flow_1"])
        self.assertEqual(result["fields_withheld"], 1)
        self.assertNotIn("01JSYNTHETIC", json.dumps(result))

    async def test_the_dashboard_scan_stops_once_it_holds_the_page(self):
        rows = [board(index, score=99 - index % 90) for index in range(400)]
        self.serve("dashboard", pages_of("dashboard", rows))
        result = await self.go(query="kitchen", search_types=["dashboard"])
        self.assertEqual(self.sent(), [("ha_search", arguments("dashboard"))])
        self.assertEqual(len(result["dashboards"]), 10)
        self.assertEqual(result["config_total_matches"], 400)
        self.assertEqual(result["fields_withheld"], 2)
        encoded = json.dumps(result)
        for withheld in ("dash-", "Kitchen board", "url_path", "title"):
            self.assertNotIn(withheld, encoded)
        self.transport.sent.clear()
        await self.go(query="kitchen", search_types=["dashboard"], offset=200, limit=10)
        self.assertEqual(self.sent(), [("ha_search", arguments("dashboard", page)) for page in range(2)])

    async def test_a_type_not_searched_sends_nothing(self):
        # HAMCP-501 W6 S1 b+: every configuration type pages through the
        # gateway, so a page is sent only for a type the call searches. X1: a
        # domain intent skips configuration search without an explicit
        # search_types.
        await self.go(query="kitchen", domain_filter="light")
        self.assertEqual(self.transport.calls, 0)
        self.audit.write.assert_not_called()
        await self.go(query="kitchen", search_types=["helper"])
        self.assertEqual(self.sent(), [("ha_search", arguments())])
        self.transport.sent.clear()
        # The default types are automation, script, scene and helper, in that
        # order; not dashboard.
        await self.go(query="kitchen")
        self.assertEqual(self.sent(), [("ha_search", arguments(search_type))
                                       for search_type in ("automation", "script", "scene", "helper")])

    async def test_a_failed_page_is_incomplete_and_the_other_sub_call_is_served(self):
        self.served[("dashboard", 0)] = {"isError": True, "content": [{"type": "text", "text": MARKER}]}
        self.serve("helper", [reply_of(body("helper", [flow(0)]))])
        result = await self.go(query="kitchen", search_types=["dashboard", "helper"])
        self.assertEqual(reasons_of(result),
                         {("source_unavailable", "dashboard", "dashboard_subcall_incomplete"): None})
        self.assertEqual([row["entity_id"] for row in result["helpers"]], ["sensor.kitchen_flow_0"])
        self.assertEqual(result["dashboards"], [])
        self.assertNotIn(MARKER, json.dumps(result))
        self.assertNotIn(MARKER, json.dumps(self.audits()))
        statuses = {record["search_type"]: (record["result_status"], record["failure_category"])
                    for record in self.audits()}
        self.assertEqual(statuses, {"dashboard": ("failed_or_cancelled", "upstream_error"),
                                    "helper": ("success", None)})

    async def test_a_page_too_large_is_read_again_in_smaller_pages(self):
        # HAMCP-501 W6 S1 (an architect decision): six rows too large together
        # for one page. Their span is read again at each ladder size down to
        # one, that span only, and the scan climbs back to 150 after it.
        rows = [flow(index) for index in range(2 * PAGE + 40)]
        for index in range(PAGE + 6, PAGE + 12):
            rows[index] = flow(index, name="N" * 9_000, score=10)
        self.table["helper"] = rows
        outcome = await subcalls.scan("helper", self.readers["helper"], query="kitchen",
                                      exact_match=True, needed=1)
        spans = [(0, PAGE), (PAGE, PAGE), (PAGE, 30), (PAGE, 6), (PAGE + 6, 6),
                 *[(PAGE + 6 + index, 1) for index in range(6)],
                 (PAGE + 12, 6), (PAGE + 18, 6), (PAGE + 24, 6),
                 (PAGE + 30, 30), (PAGE + 60, 30), (PAGE + 90, 30), (PAGE + 120, 30), (2 * PAGE, PAGE)]
        audits = self.audits()
        self.assertEqual([(record["offset"], record["limit"]) for record in audits], spans)
        self.assertEqual([record["failure_category"] for record in audits],
                         [None, "page_too_large", "page_too_large", None, "page_too_large"] + [None] * 14)
        self.assertEqual(self.sent(), [("ha_search", arguments(page=offset // size, size=size))
                                       for offset, size in spans])
        self.assertEqual((outcome.status, outcome.pages, self.transport.calls),
                         (subcalls.COMPLETE, len(spans), len(spans)))
        # Mixed sizes land on the same rows as a single-size scan: none
        # skipped, none repeated.
        self.assertEqual([row["entity_id"] for row in outcome.rows], [row["entity_id"] for row in rows])
        self.table["helper"] = [flow(index) for index in range(2 * PAGE + 40)]
        single = await subcalls.scan("helper", self.readers["helper"], query="kitchen",
                                     exact_match=True, needed=1)
        self.assertEqual((single.status, single.pages), (subcalls.COMPLETE, 3))
        self.assertEqual([row["entity_id"] for row in single.rows],
                         [row["entity_id"] for row in outcome.rows])
        self.assertEqual(single.total, outcome.total)

    async def test_a_row_too_large_for_a_page_of_one_is_incomplete(self):
        first = [flow(index) for index in range(PAGE)]
        # One row whose name alone, UTF-8 encoded, exceeds the gateway's 52,000
        # bytes: read once at each ladder size, then incomplete; the rows
        # before it are kept.
        large = flow(PAGE, name=w3.PageSizeTests.WIDE * 13_500, score=10)
        self.table["helper"] = first + [large] + [flow(PAGE + 1 + index) for index in range(10)]
        result = await self.go(query="kitchen", search_types=["helper"], limit=1)
        self.assertEqual(reasons_of(result),
                         {("source_unavailable", "helper", "helper_subcall_incomplete"): None,
                          ("paged", "config", "more_matches"): PAGE - 1})
        self.assertEqual(result["config_total_matches"], PAGE)
        self.assertNotIn(w3.PageSizeTests.WIDE * 10, json.dumps(result))
        self.assertEqual([(record["offset"], record["limit"], record["failure_category"])
                          for record in self.audits()],
                         [(0, PAGE, None)] + [(PAGE, size, "page_too_large") for size in LADDER])
        self.assertEqual(self.transport.calls, 1 + len(LADDER))

    async def test_a_page_too_large_at_every_size_is_incomplete(self):
        # Row 25 keeps about 60 bytes of a dashboard row: only a row count far
        # past the page size exceeds the gateway's size check. Served at every
        # size, the page is read once at each ladder size; the helper sub-call
        # is served.
        self.served[("dashboard", 0)] = reply_of(body("dashboard", [board(i) for i in range(2_000)]))
        self.serve("helper", [reply_of(body("helper", [flow(0)]))])
        result = await self.go(query="kitchen", search_types=["dashboard", "helper"])
        self.assertEqual(reasons_of(result),
                         {("source_unavailable", "dashboard", "dashboard_subcall_incomplete"): None})
        self.assertEqual([row["entity_id"] for row in result["helpers"]], ["sensor.kitchen_flow_0"])
        records = [record for record in self.audits() if record["search_type"] == "dashboard"]
        self.assertEqual([(record["offset"], record["limit"], record["failure_category"]) for record in records],
                         [(0, size, "page_too_large") for size in LADDER])
        self.assertNotIn("dash-", json.dumps(result) + json.dumps(self.audits()))

    async def test_the_escaped_reply_bound_is_a_page_too_large(self):
        # A row inside the UTF-8 check whose ASCII-escaped reply exceeds the
        # 60,000-character render bound fails as too large, instead of
        # reaching its reader cut; a row the UTF-8 check refuses fails before
        # the render; a smaller row is served whole.
        flags = []
        original = gw.dump_json_with_truncation

        def recorded(data, limit):
            rendered, truncated = original(data, limit)
            flags.append(truncated)
            return rendered, truncated

        for characters, outcome, rendered in ((4_900, "page_too_large", [True]),
                                              (13_500, "page_too_large", []),
                                              (4_000, None, [False])):
            with self.subTest(characters=characters):
                row = flow(0, name=w3.PageSizeTests.WIDE * characters)
                self.served[("helper", 0, 1)] = reply_of(body("helper", [row], limit=1))
                flags.clear()
                with patch.object(gw, "dump_json_with_truncation", recorded):
                    if outcome is None:
                        page = await self.readers["helper"](arguments(size=1))
                        self.assertEqual(page.rows, (row,))
                    else:
                        failure = await self.failed(outcome, size=1)
                        self.assertIsInstance(failure, subcalls.PageTooLarge)
                self.assertEqual(flags, rendered)

    async def test_a_page_of_each_types_largest_rows_passes_at_its_ladder_size(self):
        # HAMCP-501 W6 S1: ``PageSizeTests`` pins each type's worst-case row
        # at the name bounds. A page of them at the largest ladder size the
        # budget holds passes the gateway's size check, sanitizer, projection
        # and bounded reply, at the last aligned offset and with the longest
        # echoed query; and a page at the first ladder size whose rows alone
        # exceed the 60,000-character reply is too large, so a scan steps
        # down from it. (The budget is conservative: a size between the two
        # may pass.)
        query = w3.PageSizeTests.WIDE * reply.QUERY_ECHO_MAX
        escaped = w3.PageSizeTests.escaped
        replies = []
        original = gw.dump_json_with_truncation

        def recorded(data, limit):
            rendered, truncated = original(data, limit)
            replies.append((data, limit, truncated))
            return rendered, truncated

        sizes = {}
        for search_type in subcalls.SUBCALL_TYPES:
            with self.subTest(search_type=search_type):
                fits = subcalls.PAGE_BUDGET_BYTES // subcalls.ROW_BYTES[search_type]
                size = sizes[search_type] = max(size for size in LADDER if size <= fits)
                page = (subcalls.MAX_SCAN_ROWS - 1) // size
                offset = page * size
                row = w3.PageSizeTests.largest(search_type)
                self.served[(search_type, offset, size)] = reply_of(body(
                    search_type, [row] * size, offset=offset, total=offset + 2 * size, query=query,
                    limit=size))
                replies.clear()
                with patch.object(gw, "dump_json_with_truncation", recorded):
                    result = await self.readers[search_type](
                        arguments(search_type, page=page, query=query, size=size))
                self.assertEqual((len(result.rows), result.has_more), (size, True))
                self.assertEqual(result.rows[0], row)
                self.assertEqual(result.withheld_names, frozenset())
                # The reply is whole: its data within the gateway's margin,
                # and the reply around the data within that margin.
                ((whole, limit, truncated),) = replies
                self.assertEqual((limit, truncated), (60_000, False))
                self.assertLessEqual(escaped(whole["data"]) + 8_000, limit)
                self.assertLessEqual(escaped(whole) - escaped(whole["data"]), 8_000)
                # One more row than the page's size is refused.
                self.served[(search_type, offset, size)] = reply_of(body(
                    search_type, [row] * (size + 1), offset=offset, query=query, limit=size))
                with self.assertRaises(Failure) as raised:
                    await self.readers[search_type](
                        arguments(search_type, page=page, query=query, size=size))
                self.assertEqual(raised.exception.category, "invalid_response")
                larger = [size_ for size_ in LADDER if size_ * subcalls.ROW_BYTES[search_type] > 60_000]
                if not larger:
                    continue
                larger = larger[-1]
                page = (subcalls.MAX_SCAN_ROWS - 1) // larger
                offset = page * larger
                self.served[(search_type, offset, larger)] = reply_of(body(
                    search_type, [row] * larger, offset=offset, total=offset + 2 * larger, query=query,
                    limit=larger))
                with self.assertRaises(subcalls.PageTooLarge) as raised:
                    await self.readers[search_type](
                        arguments(search_type, page=page, query=query, size=larger))
                self.assertEqual(raised.exception.category, "page_too_large")
        self.assertEqual(sizes, {"automation": 6, "script": 1, "scene": 1, "dashboard": PAGE, "helper": 1})

    async def test_upstream_partial_is_disclosed(self):
        self.serve("helper", [reply_of(body("helper", [flow(0)], partial=True))])
        page = await self.readers["helper"](arguments())
        self.assertIs(page.partial, True)
        result = await self.go(query="kitchen", search_types=["helper"])
        self.assertEqual(reasons_of(result), {("not_searched", "helper", "upstream_partial"): None})
        self.assertEqual([row["entity_id"] for row in result["helpers"]], ["sensor.kitchen_flow_0"])

    async def test_an_unsearched_custom_helper_is_upstream_partial(self):
        # 9.2.0 marks a page partial when it skipped a custom helper flow.
        self.serve("helper", [reply_of(body("helper", [flow(0)], partial=True, partial_reason=UNSEARCHED,
                                            warnings=[SKIP, f"incomplete results: {UNSEARCHED}"]))])
        page = await self.readers["helper"](arguments())
        self.assertEqual((page.partial, page.withheld_names), (True, frozenset()))
        result = await self.go(query="kitchen", search_types=["helper"])
        self.assertEqual(reasons_of(result), {("not_searched", "helper", "upstream_partial"): None})
        self.assertEqual([row["entity_id"] for row in result["helpers"]], ["sensor.kitchen_flow_0"])
        self.assertNotIn("custom helper integration", json.dumps(result))

    async def test_every_dashboard_page_failure_is_incomplete_and_never_retried(self):
        # Design section 8, items 9 and 14: each way the gateway fails a dashboard page
        # leaves that sub-call incomplete; the helper sub-call is served.
        state = {}
        inner = self.transport.execute_read

        async def failing(tool, value, **kwargs):
            dashboard = value.get("search_types") == ["dashboard"]
            if dashboard and state["before"]:
                raise DashboardTransportError(state["before"])
            exchange = await inner(tool, value, **kwargs)
            if dashboard and state["after"]:
                raise DashboardTransportError(state["after"])
            return exchange

        self.transport.execute_read = failing
        # (category, raised before dispatch, raised after dispatch, reply, dispatched)
        cases = [
            ("connection_failed", "connection_failed", None, None, 0),
            ("connection_failed", None, "connection_failed", None, 1),
            ("timeout", None, "timeout", None, 1),
            # HAMCP-501 W6 S1: the transport's raw content bound is not the reply
            # size bound: the page fails with no step-down.
            ("response_too_large", None, "response_too_large", None, 1),
            ("invalid_response", None, None,
             reply_of({**body("dashboard", [board(0)]), "has_more": True, "next_offset": 1}), 1),
            ("upstream_error", None, None, {"isError": True, "content": [{"type": "text", "text": MARKER}]}, 1),
            # Enforce mode refuses, or conceals a hidden id as not found: no
            # reviewed domain outcome maps either code, so both are
            # ``upstream_error``.
            ("upstream_error", None, None, refused(ENFORCED), 1),
            ("upstream_error", None, None, refused(CONCEALED), 1),
        ]
        self.serve("helper", [reply_of(body("helper", [flow(0)]))])
        for category, before, after, raw, dispatched in cases:
            with self.subTest(category=category, before=before, after=after):
                state.update(before=before, after=after)
                self.served[("dashboard", 0)] = raw or reply_of(body("dashboard", [board(0)]))
                self.audit.reset_mock()
                result = await self.go(query="kitchen", search_types=["dashboard", "helper"])
                self.assertEqual(reasons_of(result),
                                 {("source_unavailable", "dashboard", "dashboard_subcall_incomplete"): None})
                self.assertEqual([row["entity_id"] for row in result["helpers"]], ["sensor.kitchen_flow_0"])
                self.assertEqual(result["dashboards"], [])
                encoded = json.dumps(result) + json.dumps(self.audits())
                for withheld in (MARKER, "dash-", "Kitchen board", "ENTITY_"):
                    self.assertNotIn(withheld, encoded)
                records = {record["search_type"]: record for record in self.audits()}
                self.assertEqual(set(records), {"dashboard", "helper"})
                self.assertEqual({key: records["dashboard"][key] for key in
                                  ("result_status", "failure_category", "provider_dispatch_count")},
                                 {"result_status": "failed_or_cancelled", "failure_category": category,
                                  "provider_dispatch_count": dispatched})
                self.assertEqual((records["helper"]["result_status"], records["helper"]["failure_category"]),
                                 ("success", None))
        # One attempt per case: a failed page is never retried.
        dashboards = [value for _, value in self.sent() if value["search_types"] == ["dashboard"]]
        self.assertEqual(dashboards, [arguments("dashboard")] * (len(cases) - 1))
        for child in self.children:
            self.assertEqual(child.upstream_active_requests, 0)

    async def test_enforce_mode_pages(self):
        rows = [flow(index) for index in range(8)]
        # 9.2.0 enforce mode omits a record that references a hidden entity.
        # The legacy path omits before paging (``smart_search/_deep.py``
        # ``_scrub_results_for_enforce``, 51e3aac7): the page is consistent and
        # served without a reason, which is the parity the design defines.
        self.serve("helper", [reply_of(body("helper", rows))])
        result = await self.go(query="kitchen", search_types=["helper"], limit=8)
        self.assertEqual(reasons_of(result), {})
        self.assertEqual(len(result["helpers"]), 8)
        # The component path omits after paging (``search/component.py``
        # ``_scrub_component_config_buckets``): it lowers the totals but keeps
        # has_more and next_offset, so a page that is not the last is short
        # and the sub-call fails closed. Whether the component serves this
        # install is unknown (design section 1).
        self.serve("helper", [reply_of(body("helper", rows, total=PAGE + 8, config_has_more=True,
                                            has_more=True, config_next_offset=PAGE, next_offset=PAGE))])
        result = await self.go(query="kitchen", search_types=["helper"])
        self.assertEqual(reasons_of(result), {("source_unavailable", "helper", "helper_subcall_incomplete"): None})
        self.assertEqual(self.audits()[-1]["failure_category"], "invalid_response")
        # Its last page stays consistent and is served.
        self.served[("helper", PAGE)] = reply_of(body("helper", rows, offset=PAGE, total=PAGE + 8))
        page = await self.readers["helper"](arguments(page=1))
        self.assertEqual((len(page.rows), page.has_more, page.config_total_matches), (8, False, PAGE + 8))
        # A refused or concealed helper page fails that sub-call.
        for error in (ENFORCED, CONCEALED):
            with self.subTest(code=error["error"]["code"]):
                self.served[("helper", 0)] = refused(error)
                result = await self.go(query="kitchen", search_types=["helper"])
                self.assertEqual(reasons_of(result),
                                 {("source_unavailable", "helper", "helper_subcall_incomplete"): None})
                self.assertEqual(result["helpers"], [])
                self.assertEqual(self.audits()[-1]["failure_category"], "upstream_error")
                self.assertNotIn(MARKER, json.dumps(result) + json.dumps(self.audits()))
        self.assertEqual(self.transport.calls, 5)

    async def test_a_full_page_of_the_largest_dashboard_rows_passes_the_gateway(self):
        # Design section 2.6 (P): the policy limit W3 sized its page by.
        self.assertEqual(w3.PageSizeTests.gateway_limit(), 60_000)
        # The 9.2.0 dashboard record (``smart_search/_deep.py``) with long
        # names; Row 25 withholds both, so the projected page is small.
        row = {"url_path": "d" * 255, "title": "T" * 255, "score": 100, "match_in_config": True,
               "match_in_references": False}
        query, page, size = "q" * reply.QUERY_ECHO_MAX, 132, PAGE
        offset = page * size
        self.served[("dashboard", offset)] = reply_of(body("dashboard", [row] * size, offset=offset,
                                                           total=offset + 2 * size, query=query))
        result = await self.readers["dashboard"](arguments("dashboard", page=page, query=query))
        self.assertEqual((len(result.rows), result.has_more), (size, True))
        self.assertEqual(result.withheld_names, frozenset({"url_path", "title"}))

    async def test_a_moved_total_is_incomplete(self):
        rows = [flow(index) for index in range(2 * PAGE)]
        self.serve("helper", [reply_of(body("helper", rows[:PAGE], total=3 * PAGE)),
                              reply_of(body("helper", rows[PAGE:], offset=PAGE, total=3 * PAGE + 1))])
        result = await self.go(query="kitchen", search_types=["helper"])
        self.assertIn(("source_unavailable", "helper", "helper_subcall_incomplete"), reasons_of(result))
        self.assertEqual(result["config_total_matches"], PAGE)
        self.assertEqual(self.transport.calls, 2)

    async def test_cancellation_propagates_and_settles(self):
        self.transport.cancel = True
        with self.assertRaises(asyncio.CancelledError):
            await self.readers["helper"](arguments())
        self.assertEqual(self.children[-1].upstream_active_requests, 0)
        self.assertEqual(self.audits()[-1]["result_status"], "failed_or_cancelled")
        with self.assertRaises(asyncio.CancelledError):
            await self.search({"query": "kitchen", "search_types": ["helper"]})
        self.assertEqual(self.children[-1].upstream_active_requests, 0)
        self.assertIs(current_telemetry(), self.telemetry)
        self.transport.cancel = False
        self.serve("helper", [reply_of(body("helper", [flow(0)]))])
        page = await self.readers["helper"](arguments())
        self.assertEqual(page.rows, (flow(0),))
        self.assertEqual(self.transport.calls, 3)
        self.assertEqual(self.children[-1].upstream_active_requests, 0)


class RefusalTests(Readers):
    """A page that cannot be established fails closed with a fixed code."""

    async def test_bad_arguments_are_refused_before_dispatch(self):
        good = arguments()
        bad = [
            None, [], "query", {}, {**good, "extra": 1}, {key: good[key] for key in good if key != "limit"},
            {**good, "offset": 1}, {**good, "offset": -PAGE}, {**good, "offset": subcalls.MAX_SCAN_ROWS},
            {**good, "offset": True}, {**good, "offset": float(PAGE)}, {**good, "limit": PAGE - 1},
            {**good, "limit": PAGE + 1}, {**good, "limit": True}, {**good, "limit": 0},
            {**good, "limit": -1}, {**good, "limit": float(PAGE)}, {**good, "limit": None},
            # HAMCP-501 W6 S1: the limit is a ladder size and the offset a
            # multiple of it.
            {**good, "limit": 10}, {**good, "limit": 2}, {**good, "limit": 2 * PAGE},
            {**good, "offset": 30}, {**good, "offset": 6, "limit": 30},
            {**good, "offset": 1, "limit": 6}, {**good, "offset": True, "limit": 1},
            {**good, "offset": subcalls.MAX_SCAN_ROWS, "limit": 1},
            {**good, "offset": 30.0, "limit": 30}, {**good, "limit": True, "offset": 1},
            {**good, "query": ""}, {**good, "query": "   "}, {**good, "query": 7}, {**good, "query": None},
            {**good, "exact_match": 1}, {**good, "exact_match": None}, {**good, "include_config": True},
            {**good, "include_config": 0}, {**good, "search_types": ["dashboard"]},
            {**good, "search_types": "helper"}, {**good, "search_types": ("helper",)},
            {**good, "search_types": ["helper", "helper"]}, {**good, "search_types": []},
            arguments("dashboard"),
        ]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(Failure) as raised:
                    await self.readers["helper"](value)
                self.assertEqual(raised.exception.category, "argument_validation")
        with self.assertRaises(Failure):
            await self.readers["dashboard"](good)
        dashboard = arguments("dashboard")
        for value in ({**dashboard, "offset": 6, "limit": 30}, {**dashboard, "offset": 30},
                      {**dashboard, "limit": 7}, {**dashboard, "limit": False}):
            with self.subTest(value=value):
                with self.assertRaises(Failure) as raised:
                    await self.readers["dashboard"](value)
                self.assertEqual(raised.exception.category, "argument_validation")
        self.assertEqual(self.transport.calls, 0)
        self.audit.write.assert_not_called()
        self.assertEqual(self.children, [])

    def test_every_ladder_size_is_accepted_at_its_aligned_offsets(self):
        last = subcalls.MAX_SCAN_ROWS - 1
        for size in LADDER:
            for page in (0, 1, 7, last // size):
                with self.subTest(size=size, page=page):
                    value = arguments(page=page, size=size)
                    self.assertEqual((value["offset"], value["limit"]), (page * size, size))
                    self.assertEqual(gw._search_page_arguments("helper", value), value)
            # The next aligned offset is past the row bound.
            value = {**arguments(size=size), "offset": (last // size + 1) * size}
            self.assertIsNone(gw._search_page_arguments("helper", value))

    def test_only_the_size_flag_makes_a_page_too_large(self):
        # HAMCP-501 W6 S1: the step-down signal is the capture's structural
        # flag, which only the reply size branches set, never a category.
        for flag, category, expected in (
                (True, "response_too_large", "page_too_large"),
                (False, "response_too_large", "response_too_large"),
                (1, "response_too_large", "response_too_large"),
                (True, "invalid_response", "invalid_response"),
                (True, "timeout", "timeout"),
                (True, None, "invalid_response")):
            with self.subTest(flag=flag, category=category):
                capture_ = gw._SearchPageCapture("helpers")
                capture_.too_large = flag
                with self.assertRaises(Failure) as raised:
                    gw._search_page_result({"success": False, "details": {"failure_category": category}},
                                           capture_, 0, PAGE)
                self.assertEqual(raised.exception.category, expected)
                self.assertIs(isinstance(raised.exception, subcalls.PageTooLarge),
                              expected == "page_too_large")
        # A successful page whose capture carries any flag is refused.
        data = body("helper", [flow(0)])
        for flag in (True, None, 0, "no"):
            with self.subTest(flag=flag):
                capture_ = gw._SearchPageCapture("helpers")
                capture_.withheld_names, capture_.too_large = frozenset(), flag
                with self.assertRaises(Failure) as raised:
                    gw._search_page_result({"success": True, "data": data}, capture_, 0, PAGE)
                self.assertEqual(raised.exception.category, "invalid_response")
                self.assertNotIsInstance(raised.exception, subcalls.PageTooLarge)
        capture_ = gw._SearchPageCapture("helpers")
        capture_.withheld_names = frozenset()
        page = gw._search_page_result({"success": True, "data": data}, capture_, 0, PAGE)
        self.assertEqual(page.rows, (flow(0),))

    async def test_error_and_invalid_replies_fail_the_page(self):
        valid = body("helper", [flow(0)])
        without = {key: value for key, value in valid.items() if key not in ("config_has_more",
                                                                             "config_next_offset")}
        cases = {
            "upstream_error": [{"isError": True, "content": [{"type": "text", "text": MARKER}]}],
            "invalid_response": [
                reply_of({"success": False, "error": MARKER}),
                # 9.2.0 omits config_has_more when its configs branch raised.
                reply_of(without),
                # D3: config_has_more must equal the flat has_more.
                reply_of({**valid, "has_more": True, "next_offset": 1}),
                reply_of({**valid, "config_has_more": True}),
                reply_of({**valid, "partial": None}),
                reply_of({key: value for key, value in valid.items() if key != "partial"}),
                reply_of({**valid, "config_total_matches": -1}),
                reply_of({**valid, "config_total_matches": "1"}),
                reply_of({**valid, "helpers": {}}),
                # A page that claims more rows must be full and end before the total.
                reply_of(body("helper", [flow(0)], total=5, config_has_more=True, has_more=True)),
                reply_of(body("helper", [flow(index) for index in range(PAGE)], total=PAGE,
                              config_has_more=True, has_more=True)),
                # A last page must end at the total.
                reply_of(body("helper", [flow(0)], total=2, config_has_more=False, has_more=False)),
                reply_of(body("helper", [flow(index) for index in range(PAGE + 1)])),
                # Every other bucket is absent or empty.
                reply_of({**valid, "scripts": [{"entity_id": "script.kitchen", "name": "Kitchen", "score": 1}]}),
                reply_of({**valid, "dashboards": [board(0)]}),
                reply_of({**valid, "entities": [{"entity_id": "light.kitchen", "score": 1}]}),
            ],
        }
        for category, raws in cases.items():
            for raw in raws:
                with self.subTest(category=category, raw=json.dumps(raw)[:120]):
                    self.served[("helper", 0)] = raw
                    failure = await self.failed(category)
                    self.assertNotIn(MARKER, repr(failure))
        self.assertEqual(self.transport.calls, sum(len(raws) for raws in cases.values()))
        self.assertEqual({record["failure_category"] for record in self.audits()}, set(cases))
        self.assertNotIn(MARKER, json.dumps(self.audits()))

    async def test_a_last_page_past_the_total_is_empty(self):
        self.served[("helper", PAGE)] = reply_of(body("helper", [], offset=PAGE, total=3))
        page = await self.readers["helper"](arguments(page=1))
        self.assertEqual((page.rows, page.has_more, page.config_total_matches), ((), False, 3))

    async def test_a_page_is_partial_only_when_the_upstream_says_so(self):
        # The gateway's own completeness reads partial for every 9.2.0
        # ha_search result (its fixed visibility-coverage warning); a page
        # takes the upstream ``partial`` flag, not that metadata. W6 (G1): the
        # internal route is executed only with a page capture.
        for partial in (False, True):
            with self.subTest(partial=partial):
                self.served[("helper", 0)] = reply_of(body("helper", [flow(0)], partial=partial, warnings=[]))
                result = json.loads(await self.gateway.execute(
                    **self.internal_call(), search_page=gw._SearchPageCapture("helpers")))
                self.assertEqual((result["metadata"]["completeness"], result["data"]["partial"]),
                                 ("partial", partial))
                page = await self.readers["helper"](arguments())
                self.assertIs(page.partial, partial)

    def internal_call(self):
        (name, route) = [(name, route) for name, route in self.gateway._internal.items()
                         if route.entry.upstream_name == "ha_search"][0]
        # The schema the page reader validates with (W6).
        return {"exposed_name": name, "arguments": arguments(),
                "reviewed_schema": gw._public_read_schema(route.entry, route.observed_tool["inputSchema"]),
                "policy_entry": route.entry,
                "admission_generation": route.generation, "contract_fingerprint": route.contract_fingerprint}

    async def test_the_reader_checks_the_page_behind_the_gateway(self):
        # The gateway's Row 25 projection refuses these shapes first (above);
        # this proves the reader's own check of what execute returns.
        valid = body("helper", [flow(0)])
        state = {}

        async def execute(**kwargs):
            if state["filled"]:
                kwargs["search_page"].withheld_names = frozenset()
            return json.dumps({"success": True, "data": state["data"]})

        cases = [
            (valid, False),
            ({**valid, "partial": None}, True), ({**valid, "partial": "false"}, True),
            ({**valid, "partial": 0}, True),
            ({key: value for key, value in valid.items() if key != "partial"}, True),
            ({**valid, "config_total_matches": -1}, True), ({**valid, "config_total_matches": "1"}, True),
            ({**valid, "config_total_matches": True}, True), ({**valid, "config_total_matches": None}, True),
            ({key: value for key, value in valid.items() if key != "config_total_matches"}, True),
        ]
        with patch.object(self.gateway, "execute", execute):
            for data, filled in cases:
                with self.subTest(filled=filled, data=json.dumps(data)[-80:]):
                    state.update(data=data, filled=filled)
                    await self.failed("invalid_response")
            state.update(data=valid, filled=True)
            page = await self.readers["helper"](arguments())
        self.assertEqual((page.rows, page.partial, page.config_total_matches), ((flow(0),), False, 1))
        self.assertEqual(self.transport.calls, 0)
        self.assertEqual([record["failure_category"] for record in self.audits()],
                         ["invalid_response"] * len(cases) + [None])

    async def test_d2_a_redacted_or_bounded_page_fails(self):
        for name in (f"Kitchen {SEGMENT} flow", f"Kitchen {HOST} flow", "N" * 20_001):
            with self.subTest(name=name[:40]):
                self.served[("helper", 0)] = reply_of(body("helper", [flow(0, name=name)]))
                await self.failed("sanitization_failed")
        self.assertEqual(self.transport.calls, 3)
        # The same rows unchanged by the sanitizer pass.
        self.served[("helper", 0)] = reply_of(body("helper", [flow(0, name="N" * 20_000)]))
        page = await self.readers["helper"](arguments())
        self.assertEqual(page.rows[0]["name"], "N" * 20_000)

    async def test_parent_authority_is_checked_before_and_after_dispatch(self):
        self.serve("helper", [reply_of(body("helper", [flow(0)]))])
        self.authorized = False
        await self.failed("provider_authority_changed")
        # Refused by the reader itself: nothing reached the transport.
        self.assertEqual((self.transport.calls, self.children), (0, []))
        self.authorized = True

        def lose():
            self.authorized = False

        def regain():
            self.authorized = True

        # Lost after the reader's check, before the dispatch commit: the
        # transport's commit refuses (restored afterwards so the reader's own
        # recheck passes and the commit's code is what surfaces).
        self.before, self.after = lose, regain
        await self.failed("prohibited_delegation")
        self.assertEqual(self.transport.calls, 0)
        # Lost and kept: the reader's recheck refuses too.
        self.after = None
        await self.failed("provider_authority_changed")
        self.assertEqual(self.transport.calls, 0)
        self.authorized, self.before = True, None
        # Lost after dispatch: the page is refused and never retried.
        self.after = lose
        await self.failed("provider_authority_changed")
        self.assertEqual(self.transport.calls, 1)
        self.authorized, self.after = True, None
        page = await self.readers["helper"](arguments())
        self.assertEqual(page.rows, (flow(0),))
        self.assertEqual([record["failure_category"] for record in self.audits()],
                         ["provider_authority_changed", "prohibited_delegation", "provider_authority_changed",
                          "provider_authority_changed", None])
        self.assertEqual([record["provider_dispatch_count"] for record in self.audits()], [0, 0, 0, 1, 1])
        for child in self.children:
            self.assertEqual(child.upstream_active_requests, 0)

    async def test_a_page_without_parent_authority_refuses_without_dispatch(self):
        self.serve("helper", [reply_of(body("helper", [flow(0)]))])
        # No parent request: the page is read in an empty context.
        orphan = asyncio.create_task(self.readers["helper"](arguments()), context=contextvars.Context())
        with self.assertRaises(Failure) as raised:
            await orphan
        self.assertEqual(raised.exception.args, ("provider_authority_changed",))
        # A parent request with no authorizer is refused the same way.
        self.telemetry.core_dispatch_authorizer = None
        await self.failed("provider_authority_changed")
        # Refused before a request starts: no child request, no audit, nothing sent.
        self.assertEqual((self.transport.calls, self.children, self.sent()), (0, [], []))
        self.audit.write.assert_not_called()
        self.assertIs(current_telemetry(), self.telemetry)
        # With the authorizer back, the same page is served.
        self.telemetry.core_dispatch_authorizer = lambda: self.authorized
        page = await self.readers["helper"](arguments())
        self.assertEqual(page.rows, (flow(0),))
        self.assertEqual(self.transport.calls, 1)

    async def test_a_stale_route_refuses_without_dispatch(self):
        tools = [tool for tool in capture("9.2.0")["tools"] if tool["name"] != "ha_search"]
        self.transport.catalog = dataclasses.replace(self.transport.catalog, tools=tuple(tools))
        await self.gateway.initialize(FastMCP("synthetic-hamcp501-reinitialize"))
        self.assertFalse(self.reader.current())
        await self.failed("provider_authority_changed")
        # Refused before any request starts: no child request, no audit.
        self.assertEqual(self.children, [])
        self.audit.write.assert_not_called()
        result = await self.go(query="kitchen", search_types=["helper"])
        self.assertEqual(reasons_of(result),
                         {("source_unavailable", "helper", "helper_subcall_incomplete"): None})
        self.assertEqual(self.transport.calls, 0)
        self.assertIsNone(self.gateway.search_subcall_reader())


class AdmissionTests(Readers):
    """Only the admitted 9.2.0 Row 25 route supplies or serves a page."""

    async def test_no_route_supplies_no_reader(self):
        self.assertIsNone(gw.UpstreamReadGateway().search_subcall_reader())
        tools = [tool for tool in capture("9.2.0")["tools"] if tool["name"] != "ha_search"]
        gateway, transport = await gateway_920(tools=tools, url=URL)
        self.assertIsNone(gateway.search_subcall_reader())
        self.readers = {}
        result = await self.go(query="kitchen", search_types=["helper", "dashboard"])
        self.assertEqual(reasons_of(result),
                         {("source_unavailable", "helper", "subcall_not_admitted"): None,
                          ("source_unavailable", "dashboard", "subcall_not_admitted"): None})
        self.assertEqual(transport.calls, 0)

    def swap_entry(self, entry):
        with self.gateway._lock:
            (name,) = [name for name, route in self.gateway._internal.items()
                       if route.entry.upstream_name == "ha_search"]
            self.gateway._internal[name] = dataclasses.replace(self.gateway._internal[name], entry=entry)

    async def test_a_foreign_release_route_supplies_no_reader(self):
        for version in ("9.1.2", "8.6.0"):
            with self.subTest(version=version):
                entry = foreign.registry().by_version[version].policy.by_name["ha_search"]
                self.assertIsNot(gw._reads(entry), reads_9_2)
                self.swap_entry(entry)
                self.assertIsNone(self.gateway.search_subcall_reader())
        self.assertEqual(self.transport.calls, 0)

    async def test_a_quarantined_descriptor_supplies_no_reader(self):
        # An ha_search descriptor that matches neither reviewed 9.2.0 variant
        # is quarantined, so no route and no reader exist.
        tools = capture("9.2.0")["tools"]
        for tool in tools:
            if tool["name"] == "ha_search":
                tool["inputSchema"]["properties"]["forged"] = {"type": "boolean"}
        gateway, transport = await gateway_920(tools=tools, url=URL)
        self.assertIn("ha_search", {item.get("upstream_name")
                                    for item in gateway.health_snapshot().get("quarantined_tools", [])})
        self.assertEqual([route for routes in (gateway._exposed, gateway._internal)
                          for route in routes.values() if route.entry.upstream_name == "ha_search"], [])
        self.assertIsNone(gateway.search_subcall_reader())
        self.readers = {}
        result = await self.go(query="kitchen", search_types=["helper"])
        self.assertEqual(reasons_of(result), {("source_unavailable", "helper", "subcall_not_admitted"): None})
        self.assertEqual(transport.calls, 0)

    async def test_the_reader_requires_the_compiled_adapter(self):
        with patch.object(reads_9_2, "is_adapter", return_value=False):
            self.assertIsNone(self.gateway.search_subcall_reader())
        with self.gateway._lock:
            duplicate = dict(self.gateway._internal)
        (name, route) = [(name, route) for name, route in duplicate.items()
                         if route.entry.upstream_name == "ha_search"][0]
        with self.gateway._lock:
            self.gateway._internal[name + "_copy"] = route
        self.assertIsNone(self.gateway.search_subcall_reader())
        # W6 (G1): a listed ha_search route is never a reader's route.
        with self.gateway._lock:
            del self.gateway._internal[name + "_copy"]
            self.gateway._exposed[name] = self.gateway._internal.pop(name)
        self.assertIsNone(self.gateway.search_subcall_reader())
        self.assertEqual(self.transport.calls, 0)

    async def execute(self, upstream_name, search_page, arguments, entry=None):
        with self.gateway._lock:
            routes = {**self.gateway._exposed, **self.gateway._internal}
        (name, route) = [(name, route) for name, route in routes.items()
                         if route.entry.upstream_name == upstream_name][0]
        return json.loads(await self.gateway.execute(
            exposed_name=name, arguments=arguments,
            reviewed_schema=gw._public_read_schema(route.entry, route.observed_tool["inputSchema"]),
            policy_entry=entry or route.entry, admission_generation=route.generation,
            contract_fingerprint=route.contract_fingerprint, search_page=search_page))

    async def test_execute_refuses_a_page_capture_outside_the_route(self):
        from support.ha_mcp_captures import arguments_920

        capture_ = gw._SearchPageCapture("helpers")
        # HAMCP-501 W6 S1: a capture arrives with its size flag unset.
        flagged, unset = gw._SearchPageCapture("helpers"), gw._SearchPageCapture("helpers")
        flagged.too_large, unset.too_large = True, None
        refusals = [
            ("ha_get_state", capture_, arguments_920("ha_get_state"), None),
            ("ha_search", object(), arguments(), None),
            ("ha_search", {"bucket": "helpers", "withheld_names": None}, arguments(), None),
            ("ha_search", capture_, arguments(),
             foreign.registry().by_version["9.1.2"].policy.by_name["ha_search"]),
            # W6: a page capture carries exactly its bucket's fixed page
            # arguments; anything else is not a sub-call page.
            ("ha_search", capture_, {**arguments(), "synthetic_unreviewed": 1}, None),
            ("ha_search", capture_, {"query": "kitchen"}, None),
            ("ha_search", capture_, {**arguments(), "offset": 5}, None),
            ("ha_search", capture_, {**arguments(), "limit": 10}, None),
            ("ha_search", capture_, {**arguments(), "offset": 6, "limit": 30}, None),
            ("ha_search", flagged, arguments(), None),
            ("ha_search", unset, arguments(), None),
            ("ha_search", capture_, arguments(search_type="dashboard"), None),
            ("ha_search", gw._SearchPageCapture("dashboards"), arguments(), None),
            ("ha_search", gw._SearchPageCapture("entities"), arguments(), None),
            ("ha_search", gw._SearchPageCapture(["helpers"]), arguments(), None),
        ]
        for upstream_name, page, value, entry in refusals:
            with self.subTest(upstream_name=upstream_name, page=type(page).__name__, entry=bool(entry)):
                result = await self.execute(upstream_name, page, value, entry)
                self.assertIs(result["success"], False)
                self.assertEqual(result["details"]["failure_category"], "prohibited_delegation")
        with patch.object(reads_9_2, "is_adapter", return_value=False):
            result = await self.execute("ha_search", capture_, arguments())
        self.assertEqual(result["details"]["failure_category"], "prohibited_delegation")
        self.assertEqual(self.transport.calls, 0)
        self.assertIsNone(capture_.withheld_names)
        # W6 (G1): without a capture the internal route refuses, zero calls;
        # with one it serves the page.
        self.served[("helper", 0)] = reply_of(body("helper", [flow(0)]))
        result = await self.execute("ha_search", None, arguments())
        self.assertEqual(result["details"]["failure_category"], "prohibited_delegation")
        self.assertEqual(self.transport.calls, 0)
        result = await self.execute("ha_search", gw._SearchPageCapture("helpers"), arguments())
        self.assertIs(result["success"], True)
        self.assertEqual(self.transport.calls, 1)
        result = await self.execute("ha_search", gw._SearchPageCapture("helpers"),
                                    arguments(page=7, size=1))
        self.assertIs(result["success"], True)
        self.assertEqual(self.transport.calls, 2)


class ReachabilityTests(Readers):
    """Nothing outside the gateway reaches the readers except the W6
    application composition, which builds the native tool's reader factory."""

    NAMES = {"search_subcall_reader", "SearchSubcallReader", "SearchSubcallPageFailure",
             "_SearchPageCapture", "bucket_withheld_names", "search_page"}

    def test_only_the_gateway_names_the_readers(self):
        found = []
        for path in sorted(PACKAGE.rglob("*.py")):
            relative = str(path.relative_to(PACKAGE))
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                label = (node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name)
                         else node.arg if isinstance(node, ast.keyword) else node.name
                         if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
                         else None)
                if label in self.NAMES:
                    found.append((relative, label))
        self.assertEqual({path for path, _ in found},
                         {"providers/upstream_read_gateway.py", "providers/upstream_reads_9_2_keys.py",
                          "application.py"})
        self.assertEqual({label for path, label in found if path.endswith("_keys.py")},
                         {"bucket_withheld_names"})
        self.assertEqual([label for path, label in found if path == "application.py"],
                         ["search_subcall_reader"])
        # The factory is defined once, in the gateway, and called once: the
        # application's reader factory for native ha_search (W6).
        calls = []
        for path in sorted(PACKAGE.rglob("*.py")):
            calls += [str(path.relative_to(PACKAGE)) for node in ast.walk(ast.parse(
                path.read_text(encoding="utf-8")))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "search_subcall_reader"]
        self.assertEqual(calls, ["application.py"])

    def test_the_audit_record_holds_no_query_or_row(self):
        source = (PACKAGE / "providers/upstream_read_gateway.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        (factory,) = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                      and node.name == "search_subcall_reader"]
        records = [node for node in ast.walk(factory) if isinstance(node, ast.Dict)
                   and any(isinstance(key, ast.Constant) and key.value == "event" for key in node.keys)]
        self.assertEqual(len(records), 1)
        self.assertEqual({key.value for key in records[0].keys}, AUDIT_KEYS)


class UnifiedDescriptorTests(Readers):
    """HAMCP-501 W6: the readers under the trimmed ``unified`` descriptor.

    Its raw input schema omits ``search_types`` and ``include_config``, which
    the same 9.2.0 callable accepts. The W4 reader validated a page against
    that raw schema, so under ``unified`` every page was refused before
    dispatch. A page is validated with the published read's public schema.
    """

    UNIFIED = True

    def route(self):
        with self.gateway._lock:
            return self.gateway._internal["ha_search"]

    async def test_the_route_is_unified_and_a_page_passes_the_public_schema_only(self):
        route = self.route()
        self.assertEqual(reads_9_2.search_variant_name(route.entry, route.runtime_contract_fingerprint),
                         "unified")
        raw = route.observed_tool["inputSchema"]
        for search_type in subcalls.SUBCALL_TYPES:
            with self.subTest(search_type=search_type):
                page = arguments(search_type)
                self.assertFalse(Draft202012Validator(raw).is_valid(page))
                self.assertTrue(Draft202012Validator(gw._public_read_schema(route.entry, raw)).is_valid(page))

    async def test_a_unified_page_dispatches_the_fixed_arguments_once(self):
        self.serve("helper", [reply_of(body("helper", [flow(0)]))])
        page = await self.readers["helper"](arguments())
        self.assertEqual(page.rows, (flow(0),))
        self.assertEqual(self.sent(), [("ha_search", arguments())])
        self.assertEqual([record["result_status"] for record in self.audits()], ["success"])

    async def test_the_raw_schema_still_bounds_nothing_else(self):
        # The public schema still refuses a key no 9.2.0 variant accepts,
        # before dispatch.
        route = self.route()
        schema = gw._public_read_schema(route.entry, route.observed_tool["inputSchema"])
        self.assertFalse(Draft202012Validator(schema).is_valid({**arguments(), "synthetic_extra": 1}))
        self.assertFalse(Draft202012Validator(schema).is_valid({**arguments(), "limit": 0}))


class UnifiedReaderShapeTests(ReaderShapeTests):
    UNIFIED = True


class UnifiedEngineTests(EngineTests):
    UNIFIED = True


class UnifiedRefusalTests(RefusalTests):
    UNIFIED = True
