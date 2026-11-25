"""HAMCP-501 W4: the gateway's native search sub-call page readers.

``UpstreamReadGateway.search_subcall_reader`` serves the W3 scan
(``search.subcalls``) one fixed ``ha_search`` page at a time over the admitted
9.2.0 Row 25 route (HAMCP-481 design sections 2.4 to 2.6; decisions D1 to D3
as relayed by the architect). Everything is synthetic and offline: the
committed 9.2.0 catalog, the receipt transport of ``support.ha_mcp_captures``
and the synthetic Core of ``tests.test_hamcp481_core_reads``. Nothing
registers or routes the readers yet (W6), so these tests drive them directly
and through ``search.engine``.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
from pathlib import Path
import sys
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]

from mcp.server.fastmcp import FastMCP  # noqa: E402
from tests import test_hamcp481_native_search_engine as w3  # noqa: E402
from tests.test_hamcp481_native_search_engine import MARKER, World, reasons_of  # noqa: E402
from support import frozen_foreign_releases as foreign  # noqa: E402
from support.ha_mcp_920_receipts import result as receipt_result, with_content  # noqa: E402
from support.ha_mcp_captures import capture, gateway_920  # noqa: E402
from ha_mcp_engineering.clients.mcp import DashboardTransportError  # noqa: E402
from ha_mcp_engineering.providers import upstream_read_gateway as gw  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2 as reads_9_2  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2_keys as k  # noqa: E402
from ha_mcp_engineering.request_context import current_telemetry  # noqa: E402
from ha_mcp_engineering.search import reply, subcalls  # noqa: E402

PACKAGE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering"
PAGE = subcalls.PAGE_SIZE
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
              "offset", "upstream_version", "contract_fingerprint", "result_status", "failure_category",
              "provider_dispatch_count", "fallback_occurred"}


def body(search_type, rows, *, offset=0, total=None, **changes):
    """One 9.2.0 ``ha_search`` page as ``_apply_search_outcome`` builds it."""

    total = offset + len(rows) if total is None else total
    more = offset + len(rows) < total
    following = offset + len(rows) if more else None
    value = {"success": True, "query": "kitchen", "entities": [], "entity_total_matches": 0,
             "automations": [], "scripts": [], "scenes": [], "helpers": [],
             "search_types": [search_type], "config_total_matches": total, "partial": False,
             "errors": [], "warnings": [SKIP], "count": len(rows), "offset": offset, "limit": PAGE,
             "config_has_more": more, "config_next_offset": following, "has_more": more,
             "next_offset": following, subcalls.BUCKETS[search_type]: list(rows)}
    value.update(changes)
    return value


def reply_of(content):
    """The upstream call result carrying ``content`` (the committed envelope)."""

    return with_content(receipt_result("r25-search"), content)


def pages_of(search_type, rows, *, total=None, **changes):
    total = len(rows) if total is None else total
    return [reply_of(body(search_type, rows[start:start + PAGE], offset=start, total=total, **changes))
            for start in range(0, max(len(rows), 1), PAGE)]


def board(index, score=80):
    return {"url_path": f"dash-{index}", "title": f"Kitchen board {index}", "score": score,
            "match_in_config": True, "match_in_references": False}


def flow(index, score=90, **changes):
    return {"entity_id": f"sensor.kitchen_flow_{index}", "helper_type": "template",
            "name": f"Kitchen flow {index}", "score": score, "match_in_name": True,
            "match_in_config": False, "match_in_references": False, **changes}


def arguments(search_type="helper", page=0, query="kitchen", exact_match=True):
    return subcalls.page_arguments(search_type, query, exact_match, page)


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


class Readers(World):
    """The 9.2.0 gateway's page readers over the receipt transport."""

    async def asyncSetUp(self):
        self.gateway, self.transport = await gateway_920(url=URL)
        self.audit = Mock()
        self.served: dict = {}
        self.children: list = []
        self.before = self.after = None
        original = self.transport.execute_read

        async def respond(tool, arguments, **kwargs):
            self.children.append(current_telemetry())
            if tool == "ha_search":
                search_type, offset = arguments["search_types"][0], arguments["offset"]
                self.transport.override = self.served.get(
                    (search_type, offset), reply_of(body(search_type, [], offset=offset)))
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

    def serve(self, search_type, pages):
        for index, raw in enumerate(pages):
            self.served[(search_type, index * PAGE)] = raw

    def sent(self):
        return [(name, value) for name, value in self.transport.sent]

    def audits(self):
        return [record.args[0] for record in self.audit.write.call_args_list]

    async def failed(self, category, search_type="helper", page=0):
        with self.assertRaises(Failure) as raised:
            await self.readers[search_type](arguments(search_type, page))
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
        (route,) = [route for route in self.gateway._exposed.values()
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
        self.assertTrue(subcalls._page_valid("helper", page))
        self.serve("dashboard", [reply_of(body("dashboard", [board(0), board(1)]))])
        page = await self.readers["dashboard"](arguments("dashboard"))
        # Row 25 withholds a 9.2.0 dashboard's url_path and title (disclosed
        # for W6): a row keeps its score and match flags only.
        self.assertEqual(page.rows, tuple({"score": 80, "match_in_config": True,
                                           "match_in_references": False} for _ in range(2)))
        self.assertEqual(page.withheld_names, frozenset({"url_path", "title"}))
        self.assertNotIn("dash-0", repr(page))
        self.assertTrue(subcalls._page_valid("dashboard", page))
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
                                ({"helpers": {}}, "helpers"), ([], "helpers"), ({"helpers": []}, "scripts"),
                                ({"scripts": []}, "scripts")):
            with self.subTest(payload=payload, bucket=bucket), self.assertRaises(ValueError):
                k.bucket_withheld_names(payload, bucket)
        self.assertEqual(k.bucket_withheld_names({"helpers": [{**flow(0), "entry_id": "x"}]}, "helpers"),
                         frozenset({"entry_id"}))


class EngineTests(Readers):
    """The W3 engine over the gateway's readers: the scan pages, merges and
    discloses exactly as with injected readers."""

    async def test_the_helper_scan_pages_through_the_gateway(self):
        rows = [flow(index, score=90 - index % 50) for index in range(320)]
        self.serve("helper", pages_of("helper", rows))
        result = await self.go(query="kitchen", search_types=["helper"])
        self.assertEqual(self.sent(), [("ha_search", arguments(page=page)) for page in range(3)])
        self.assertEqual(result["config_total_matches"], 320)
        self.assertEqual(len(result["helpers"]), 10)
        self.assertEqual([row["score"] for row in result["helpers"]], [90] * 7 + [89] * 3)
        self.assertEqual(reasons_of(result), {("paged", "config", "more_matches"): 310})
        audits = self.audits()
        self.assertEqual([record["offset"] for record in audits], [0, 150, 300])
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
        self.assertEqual(len({record["request_id"] for record in audits} | {self.telemetry.request_id}), 4)
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
        await self.go(query="kitchen", search_types=["automation"])
        self.assertEqual(self.transport.calls, 0)
        self.audit.write.assert_not_called()
        # The default types include helper but not dashboard.
        await self.go(query="kitchen")
        self.assertEqual(self.sent(), [("ha_search", arguments())])

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

    async def test_an_oversized_page_fails_and_the_earlier_rows_are_kept(self):
        first = [flow(index) for index in range(PAGE)]
        # 150 rows of 400-character names exceed the gateway's 52,000 bytes.
        large = [flow(PAGE + index, name="N" * 400) for index in range(PAGE)]
        self.serve("helper", [reply_of(body("helper", first, total=2 * PAGE)),
                              reply_of(body("helper", large, offset=PAGE, total=2 * PAGE))])
        result = await self.go(query="kitchen", search_types=["helper"])
        self.assertEqual(reasons_of(result),
                         {("source_unavailable", "helper", "helper_subcall_incomplete"): None,
                          ("paged", "config", "more_matches"): PAGE - 10})
        self.assertEqual(result["config_total_matches"], PAGE)
        self.assertNotIn("N" * 400, json.dumps(result))
        self.assertEqual([record["failure_category"] for record in self.audits()], [None, "response_too_large"])
        self.assertEqual(self.transport.calls, 2)

    async def test_a_full_page_of_the_largest_helper_rows_passes_the_gateway(self):
        # Design section 2.6 (P): ``PageSizeTests`` sizes this envelope; here
        # the gateway's own size check accepts it.
        row = {"entity_id": "input_datetime." + "x" * 49, "helper_type": "input_datetime",
               "name": "N" * 64, "score": 100, "match_in_name": True, "match_in_config": True,
               "match_in_references": True}
        query, page = "q" * reply.QUERY_ECHO_MAX, 132
        offset = page * PAGE
        self.served[("helper", offset)] = reply_of(body("helper", [row] * PAGE, offset=offset,
                                                        total=offset + 2 * PAGE, query=query))
        result = await self.readers["helper"](arguments(page=page, query=query))
        self.assertEqual((len(result.rows), result.has_more), (PAGE, True))

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
            # Row 25 keeps about 60 bytes of a dashboard row: only a row count
            # far past the page size can exceed the gateway's size check.
            ("response_too_large", None, None, reply_of(body("dashboard", [board(i) for i in range(2_000)])), 1),
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
        rows = [flow(index) for index in range(PAGE - 2)]
        # 9.2.0 enforce mode omits a record that references a hidden entity.
        # The legacy path omits before paging (``smart_search/_deep.py``
        # ``_scrub_results_for_enforce``, 51e3aac7): the page is consistent and
        # served without a reason, which is the parity the design defines.
        self.serve("helper", [reply_of(body("helper", rows))])
        result = await self.go(query="kitchen", search_types=["helper"])
        self.assertEqual(reasons_of(result), {("paged", "config", "more_matches"): PAGE - 12})
        # The component path omits after paging (``search/component.py``
        # ``_scrub_component_config_buckets``): it lowers the totals but keeps
        # has_more and next_offset, so a page that is not the last is short
        # and the sub-call fails closed. Whether the component serves this
        # install is unknown (design section 1).
        self.serve("helper", [reply_of(body("helper", rows, total=2 * PAGE - 2, config_has_more=True,
                                            has_more=True, config_next_offset=PAGE, next_offset=PAGE))])
        result = await self.go(query="kitchen", search_types=["helper"])
        self.assertEqual(reasons_of(result), {("source_unavailable", "helper", "helper_subcall_incomplete"): None})
        self.assertEqual(self.audits()[-1]["failure_category"], "invalid_response")
        # Its last page stays consistent and is served.
        self.served[("helper", PAGE)] = reply_of(body("helper", rows, offset=PAGE, total=2 * PAGE - 2))
        page = await self.readers["helper"](arguments(page=1))
        self.assertEqual((len(page.rows), page.has_more, page.config_total_matches), (PAGE - 2, False, 2 * PAGE - 2))
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
        query, page = "q" * reply.QUERY_ECHO_MAX, 132
        offset = page * PAGE
        self.served[("dashboard", offset)] = reply_of(body("dashboard", [row] * PAGE, offset=offset,
                                                           total=offset + 2 * PAGE, query=query))
        result = await self.readers["dashboard"](arguments("dashboard", page=page, query=query))
        self.assertEqual((len(result.rows), result.has_more), (PAGE, True))
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
            {**good, "limit": PAGE + 1}, {**good, "limit": True},
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
        self.assertEqual(self.transport.calls, 0)
        self.audit.write.assert_not_called()
        self.assertEqual(self.children, [])

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
        # takes the upstream ``partial`` flag, not that metadata.
        for partial in (False, True):
            with self.subTest(partial=partial):
                self.served[("helper", 0)] = reply_of(body("helper", [flow(0)], partial=partial, warnings=[]))
                result = json.loads(await self.gateway.execute(**self.public_call()))
                self.assertEqual((result["metadata"]["completeness"], result["data"]["partial"]),
                                 ("partial", partial))
                page = await self.readers["helper"](arguments())
                self.assertIs(page.partial, partial)

    def public_call(self):
        (name, route) = [(name, route) for name, route in self.gateway._exposed.items()
                         if route.entry.upstream_name == "ha_search"][0]
        return {"exposed_name": name, "arguments": arguments(),
                "reviewed_schema": route.observed_tool["inputSchema"], "policy_entry": route.entry,
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
            (name,) = [name for name, route in self.gateway._exposed.items()
                       if route.entry.upstream_name == "ha_search"]
            self.gateway._exposed[name] = dataclasses.replace(self.gateway._exposed[name], entry=entry)

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
        self.assertEqual([route for route in gateway._exposed.values() if route.entry.upstream_name == "ha_search"],
                         [])
        self.assertIsNone(gateway.search_subcall_reader())
        self.readers = {}
        result = await self.go(query="kitchen", search_types=["helper"])
        self.assertEqual(reasons_of(result), {("source_unavailable", "helper", "subcall_not_admitted"): None})
        self.assertEqual(transport.calls, 0)

    async def test_the_reader_requires_the_compiled_adapter(self):
        with patch.object(reads_9_2, "is_adapter", return_value=False):
            self.assertIsNone(self.gateway.search_subcall_reader())
        with self.gateway._lock:
            duplicate = dict(self.gateway._exposed)
        (name, route) = [(name, route) for name, route in duplicate.items()
                         if route.entry.upstream_name == "ha_search"][0]
        with self.gateway._lock:
            self.gateway._exposed[name + "_copy"] = route
        self.assertIsNone(self.gateway.search_subcall_reader())
        self.assertEqual(self.transport.calls, 0)

    async def execute(self, upstream_name, search_page, arguments, entry=None):
        (name, route) = [(name, route) for name, route in self.gateway._exposed.items()
                         if route.entry.upstream_name == upstream_name][0]
        return json.loads(await self.gateway.execute(
            exposed_name=name, arguments=arguments, reviewed_schema=route.observed_tool["inputSchema"],
            policy_entry=entry or route.entry, admission_generation=route.generation,
            contract_fingerprint=route.contract_fingerprint, search_page=search_page))

    async def test_execute_refuses_a_page_capture_outside_the_route(self):
        from support.ha_mcp_captures import arguments_920

        capture_ = gw._SearchPageCapture("helpers")
        refusals = [
            ("ha_get_state", capture_, arguments_920("ha_get_state"), None),
            ("ha_search", object(), arguments(), None),
            ("ha_search", {"bucket": "helpers", "withheld_names": None}, arguments(), None),
            ("ha_search", capture_, arguments(),
             foreign.registry().by_version["9.1.2"].policy.by_name["ha_search"]),
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
        # Without a capture the public route is unchanged.
        self.served[("helper", 0)] = reply_of(body("helper", [flow(0)]))
        result = await self.execute("ha_search", None, arguments())
        self.assertIs(result["success"], True)
        self.assertEqual(self.transport.calls, 1)


class ReachabilityTests(Readers):
    """W4 adds no route: nothing outside the gateway reaches the readers."""

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
                         {"providers/upstream_read_gateway.py", "providers/upstream_reads_9_2_keys.py"})
        self.assertEqual({label for path, label in found if path.endswith("_keys.py")},
                         {"bucket_withheld_names"})
        # The factory is defined once and never called inside the package.
        calls = [node for node in ast.walk(ast.parse(
                 (PACKAGE / "providers/upstream_read_gateway.py").read_text(encoding="utf-8")))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "search_subcall_reader"]
        self.assertEqual(calls, [])

    def test_the_audit_record_holds_no_query_or_row(self):
        source = (PACKAGE / "providers/upstream_read_gateway.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        (factory,) = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                      and node.name == "search_subcall_reader"]
        records = [node for node in ast.walk(factory) if isinstance(node, ast.Dict)
                   and any(isinstance(key, ast.Constant) and key.value == "event" for key in node.keys)]
        self.assertEqual(len(records), 1)
        self.assertEqual({key.value for key in records[0].keys}, AUDIT_KEYS)
