"""HAMCP-501 W6: the native ``ha_search`` cutover, end to end.

The registered native tool runs over the synthetic Core of
``tests.test_hamcp481_core_reads`` and the 9.2.0 gateway's sub-call page
readers of ``tests.test_hamcp501_search_subcall_readers``, directly and
through the authenticated gateway. The delegated 9.2.0 ``ha_search`` is an
internal-only route (G1 = A, as relayed by the architect): never listed, never
aliased, reached only by the page readers. Its pages keep the route's own
Core requirement (G2). The end-to-end tests run under both 9.2.0 descriptors;
``unified`` is the live variant (fingerprint c1ca76bd, per the HA Admin
baseline of 2026-11-25). Everything is synthetic and offline.
"""

from __future__ import annotations

import ast
import asyncio
import contextvars
from copy import deepcopy
from functools import partial
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]

import httpx  # noqa: E402
from jsonschema import Draft202012Validator  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from tests import test_hamcp481_native_search_engine as w3  # noqa: E402
from tests import test_hamcp501_search_subcall_readers as w4  # noqa: E402
from tests.test_hamcp481_native_search_engine import MARKER, reasons_of  # noqa: E402
from tests.test_hamcp476_automation_run_counts import settings as gateway_settings  # noqa: E402
from support import ha_mcp_captures as captures  # noqa: E402
import test_hamcp162_yaml_admission as yaml_admission  # noqa: E402
from ha_mcp_engineering import capabilities  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, HomeAssistantApiError  # noqa: E402
from ha_mcp_engineering.ha_core_readmission import routes  # noqa: E402
from ha_mcp_engineering.mcp_sdk_compatibility import McpSdkToolRegistry  # noqa: E402
from ha_mcp_engineering.providers import upstream_read_gateway as gw  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2 as reads_9_2  # noqa: E402
from ha_mcp_engineering.providers import upstream_yaml_reader as yaml_reader  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, current_telemetry, end_request  # noqa: E402
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.search import core_contracts as c, reply as reply_module, scoring  # noqa: E402
from ha_mcp_engineering.tools import search as search_module  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import schema_fingerprint  # noqa: E402

PACKAGE = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering"
SUBCALL_REQUIREMENT = "core.delegated_device_effective_area"
REPLY_BUCKETS = ("entities", "automations", "scripts", "scenes", "helpers", "dashboards")


def rpc_result(response):
    """The JSON-RPC result: the app's JSON reply, or the gateway's own SSE
    reply (a refusal the gateway renders before the SDK)."""

    if response.headers.get("content-type", "").startswith("text/event-stream"):
        (line,) = [row for row in response.text.replace("\r", "").splitlines()
                   if row.startswith("data: ")]
        return json.loads(line.removeprefix("data: "))["result"]
    return response.json()["result"]


def ranked(data):
    """Each bucket's (id or name, score, match fields) in reply order."""

    return {bucket: [(row.get("entity_id") or row.get("name"), row["score"], row.get("match_type"),
                      row.get("match_in_name"), row.get("match_in_config"), row.get("match_in_references"))
                     for row in data.get(bucket, ())]
            for bucket in REPLY_BUCKETS}


class NativeBase(w4.Readers):
    """The registered native tool: W3's synthetic Core sessions and the 9.2.0
    gateway's page readers, composed as ``application.py`` composes them."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        runtime = patch.multiple(search_module.SEARCH, session_factory=self.factory,
                                 reader_factory=self.reader_of, known_secrets=self.secrets)
        runtime.start()
        self.addCleanup(runtime.stop)
        self.metadata = None

    def reader_of(self):
        return self.gateway.search_subcall_reader(audit=self.audit)

    async def call(self, **arguments):
        return json.loads(await search_module.registered_tool().run(arguments))

    def check_metadata(self, metadata):
        self.assertEqual({key: metadata[key] for key in
                          ("provider", "data_providers", "fallback", "fallback_occurred")},
                         {"provider": "engineering", "data_providers": list(search_module.DATA_PROVIDERS),
                          "fallback": "none", "fallback_occurred": False})

    async def ok(self, **arguments):
        result = await self.call(**arguments)
        self.assertIs(result["success"], True, result)
        data = result["data"]
        self.check_calls()
        self.check_reply(data)
        self.metadata = result["metadata"]
        self.check_metadata(self.metadata)
        self.assertEqual(self.metadata["completeness"], "partial" if data["partial"] else "complete")
        # HAMCP-523: every reply names its warnings' closed codes, in order.
        self.assertEqual(self.metadata["warning_codes"],
                         [reply_module.WARNING_CODES[text] for text in data.get("warnings", [])])
        self.assertNotIn("visibility_filter_active", json.dumps(result))
        return data

    async def refused_call(self, code, **arguments):
        result = await self.call(**arguments)
        self.assertIs(result["success"], False, result)
        self.assertEqual(result["error_code"], code.value)
        self.assertEqual(result["metadata"]["fallback"], "none")
        self.assertIs(result["metadata"]["fallback_occurred"], False)
        return result

    def registry_row(self, entity_id):
        (row,) = [row for row in self.values["config/entity_registry/list"] if row["entity_id"] == entity_id]
        return row

    def state_row(self, entity_id):
        (row,) = [row for row in self.values["states"] if row["entity_id"] == entity_id]
        return row


class NativeTests(NativeBase):
    """Through the registered tool, under the legacy 9.2.0 descriptor."""

    async def test_the_helper_scan_pages_under_the_native_request(self):
        rows = [w4.flow(index, score=90 - index % 5) for index in range(w4.PAGE + 32)]
        self.serve("helper", w4.pages_of("helper", rows))
        pages = -(-len(rows) // w4.PAGE)
        self.assertGreater(pages, 1)
        data = await self.ok(query="kitchen", search_types=["helper"])
        self.assertEqual(self.sent(), [("ha_search", w4.arguments(page=page)) for page in range(pages)])
        self.assertEqual((data["config_total_matches"], len(data["helpers"])), (w4.PAGE + 32, 10))
        self.assertEqual(reasons_of(data), {("paged", "config", "more_matches"): w4.PAGE + 22})
        self.assertEqual(self.metadata["subcall_route"], "admitted")
        # D5 (a): every page carries the native request's authorizer.
        self.assertEqual(len(self.children), pages)
        for child in self.children:
            self.assertIsNot(child, self.telemetry)
            self.assertEqual(child.tool_name, "search_subcall_page_read")
            self.assertIs(child.core_dispatch_authorizer, self.telemetry.core_dispatch_authorizer)
        self.assertEqual([(record["result_status"], record["failure_category"]) for record in self.audits()],
                         [("success", None)] * pages)

    async def test_dashboards_are_searched_natively_through_the_tool(self):
        # HAMCP-447: the registered tool searches dashboards from Core, with no
        # sub-call page; a record is its url_path, title, score and flags.
        w4.native_dashboards(self.values)
        data = await self.ok(query="kitchen", search_types=["dashboard"])
        self.assertEqual(self.sent(), [])
        self.assertEqual(data["dashboards"], [{"url_path": "synthetic-board", "title": "Synthetic", "score": 100,
                                               "match_in_config": True, "match_in_references": False}])
        self.assertEqual((data["config_total_matches"], data["fields_withheld"]), (1, 0))
        self.assertNotIn("synthview", json.dumps(data))
        self.assertEqual(self.transport.calls, 0)

    async def test_a_failed_page_is_disclosed_and_native_dashboards_are_served(self):
        w4.native_dashboards(self.values)
        self.served[("helper", 0)] = {"isError": True, "content": [{"type": "text", "text": MARKER}]}
        data = await self.ok(query="kitchen", search_types=["dashboard", "helper"])
        self.assertEqual(reasons_of(data),
                         {("source_unavailable", "helper", "helper_subcall_incomplete"): None})
        self.assertEqual([row["url_path"] for row in data["dashboards"]], ["synthetic-board"])
        self.assertEqual(data["helpers"], [])
        self.assertEqual(self.metadata["completeness"], "partial")
        self.assertNotIn(MARKER, json.dumps(data))
        self.assertNotIn(MARKER, json.dumps(self.audits()))

    async def test_only_searched_sub_call_types_send_a_page(self):
        # HAMCP-501 W6 S1 b+ (approved by Josh as relayed by the architect):
        # every configuration type pages through the gateway. X1: a domain
        # intent skips configuration search without an explicit search_types.
        await self.ok(query="kitchen", domain_filter="light")
        self.assertEqual(self.transport.calls, 0)
        await self.ok(query="kitchen", search_types=["helper"])
        self.assertEqual(self.sent(), [("ha_search", w4.arguments())])
        self.transport.sent.clear()
        # The default types are automation, script, scene and helper; not dashboard.
        await self.ok(query="kitchen")
        # W6 F1: a helper probe brackets the scans.
        self.assertEqual(self.sent(), [("ha_search", w4.probe_arguments()), *(
            ("ha_search", w4.arguments(search_type))
            for search_type in ("automation", "script", "scene", "helper")), ("ha_search", w4.probe_arguments())])

    async def test_an_unadmitted_sub_call_route_is_disclosed_with_zero_dispatch(self):
        # HAMCP-447: dashboards are native, so Core serves them.
        w4.native_dashboards(self.values)
        with patch.object(search_module.SEARCH, "reader_factory", lambda: None):
            data = await self.ok(query="kitchen", search_types=["helper", "dashboard"])
        self.assertEqual(self.metadata["subcall_route"], "not_admitted")
        self.assertEqual(reasons_of(data),
                         {("source_unavailable", "helper", "subcall_not_admitted"): None})
        self.assertEqual([row["url_path"] for row in data["dashboards"]], ["synthetic-board"])
        self.assertEqual(self.transport.calls, 0)

    async def test_each_warning_carries_its_closed_code(self):
        # HAMCP-523: the texts stay in data.warnings; the codes are in
        # metadata.warning_codes, and partial does not change.
        self.serve("helper", [w4.reply_of(w4.body("helper", [w4.flow(0)]))])
        data = await self.ok(query="kitchen", search_types=["helper"])
        self.assertEqual(data["warnings"], [reply_module.WARNING_ENTITY_SKIPPED])
        self.assertEqual(self.metadata["warning_codes"], ["entity_search_skipped"])
        self.assertEqual((data["partial"], data["partial_reasons"]), (False, []))
        data = await self.ok(query="kitchen", domain_filter="light")
        self.assertEqual(data["warnings"], [reply_module.WARNING_CONFIG_SKIPPED])
        self.assertEqual(self.metadata["warning_codes"], ["config_search_skipped"])
        self.assertEqual((data["partial"], data["partial_reasons"]), (False, []))
        # Both searches run: no warning, and an empty list of codes.
        data = await self.ok(query="kitchen")
        self.assertNotIn("warnings", data)
        self.assertEqual(self.metadata["warning_codes"], [])

    async def test_a_refusal_carries_no_warning_codes(self):
        result = await self.refused_call(ErrorCode.VALIDATION_FAILURE, query="kitchen", limit=0)
        self.assertNotIn("warning_codes", result["metadata"])

    async def test_an_unconfigured_runtime_refuses_before_any_read(self):
        with patch.multiple(search_module.SEARCH, session_factory=None, reader_factory=None):
            result = await self.refused_call(ErrorCode.PROVIDER_UNAVAILABLE, query="kitchen")
        self.assertEqual(result["details"]["source"], "core_authority")
        self.assertEqual((self.calls, self.reads, self.transport.calls), ([], [], 0))

    async def test_the_policy_gate_refuses_before_any_read(self):
        with patch.object(search_module, "native_search_policy_allows_read", return_value=False):
            await self.refused_call(ErrorCode.PROVIDER_PROHIBITED, query="kitchen")
        self.assertEqual((self.calls, self.reads, self.transport.calls), ([], [], 0))

    async def test_invalid_arguments_refuse_before_any_read(self):
        for arguments in ({"query": "kitchen", "synthetic_unreviewed": True}, {"query": "kitchen", "limit": 0},
                          {"query": "kitchen", "include_config": "false"}, {}, {"query": "   "}):
            with self.subTest(arguments=sorted(arguments)):
                await self.refused_call(ErrorCode.VALIDATION_FAILURE, **arguments)
        self.assertEqual((self.calls, self.reads, self.transport.calls), ([], [], 0))

    async def test_a_core_failure_refuses_with_no_delegated_fallback(self):
        # Design section 8.13: a Core source that cannot be read refuses the
        # call; nothing falls back to the delegated search.
        self.values["states"] = HomeAssistantApiError("synthetic states failure")
        result = await self.refused_call(ErrorCode.PROVIDER_UNAVAILABLE, query="kitchen")
        self.check_metadata(result["metadata"])
        self.assertEqual(self.transport.calls, 0)
        self.assertEqual(self.sent(), [])

    async def test_only_the_fixed_read_only_core_commands_are_sent(self):
        self.serve("helper", [w4.reply_of(w4.body("helper", [w4.flow(0)]))])
        # HAMCP-447: the native dashboard reads are pinned too.
        w4.native_dashboards(self.values)
        await self.ok(query="kitchen", search_types=["automation", "script", "scene", "helper", "dashboard"])
        self.assertEqual(len(self.batches("lovelace/config")), 1)
        await self.ok(query="light.kitchen")
        await self.ok(query="kitchen", exact_match=False,
                      result_fields=["entity_id", "area", "floor", "labels", "aliases"])
        # ``check_calls`` pinned each Core command; the only upstream tool is
        # the internal-only Row 25 search page.
        self.assertEqual({name for name, _ in self.sent()}, {"ha_search"})
        for _, value in self.sent():
            self.assertEqual(set(value), set(w4.arguments()))


class ScorerTests(NativeBase):
    """Design section 3.5 (W1) and Cursor 506 notes, through the registered tool."""

    async def same(self, padded, plain, **arguments):
        first = await self.ok(query=padded, **arguments)
        second = await self.ok(query=plain, **arguments)
        self.assertEqual(ranked(first), ranked(second))
        self.assertEqual(reasons_of(first), reasons_of(second))
        return second

    async def test_case_and_padding_score_as_the_lowered_stripped_query(self):
        data = await self.same("  HaLL LiGHT  ", "hall light")
        self.assertEqual([(row["entity_id"], row["score"]) for row in data["entities"]], [("light.hall", 80)])
        data = await self.same("  VeRanda ", "veranda", exact_match=False)
        self.assertIn("light.porch", [row["entity_id"] for row in data["entities"]])
        # HAMCP-501 W6 S1 b+: automation rows are the route's. A page carries
        # the stripped query, as 9.2.0 strips it (``tools/tools_search.py``
        # line 359, sha256 95a418bb...), and the route lowers it itself (legacy
        # ``smart_search/_deep.py`` line 180, sha256 10c57f9f...).
        morning = {"entity_id": "automation.morning", "friendly_name": "Morning routine", "score": 100,
                   "match_in_name": True, "match_in_config": False, "match_in_references": False}
        self.serve("automation", [w4.reply_of(w4.body("automation", [morning]))])
        self.transport.sent.clear()
        data = await self.same("  MORNING Routine ", "morning routine", search_types=["automation"])
        self.assertEqual([row["entity_id"] for row in data["automations"]], ["automation.morning"])
        # W6 F1: each search's page is bracketed by a helper probe.
        self.assertEqual([value["query"] for _, value in self.sent()],
                         ["MORNING Routine"] * 3 + ["morning routine"] * 3)
        self.assertEqual([(value["search_types"], value["limit"]) for _, value in self.sent()],
                         [(["helper"], 1), (["automation"], w4.PAGE), (["helper"], 1)] * 2)
        # The deep path: an entity-id-shaped query reads Core's search graph
        # once, lowered and stripped (design section 8 step 5). No page
        # carries the automation, so its row is the graph's.
        self.served.clear()
        self.calls.clear()
        data = await self.same("  LIGHT.KITCHEN ", "light.kitchen")
        related = self.batches("search/related")
        self.assertEqual(len(related), 2)
        self.assertEqual([[payload["item_id"] for payload in batch] for batch in related],
                         [["light.kitchen"], ["light.kitchen"]])
        self.assertIs({row["entity_id"]: row for row in data["automations"]}
                      ["automation.morning"]["match_in_references"], True)

    async def test_an_empty_hidden_by_is_hidden_and_penalized(self):
        visible = await self.ok(query="porch light")
        (open_score,) = [row["score"] for row in visible["entities"] if row["entity_id"] == "light.porch"]
        self.registry_row("light.porch")["hidden_by"] = ""
        hidden = await self.ok(query="porch light")
        (empty_score,) = [row["score"] for row in hidden["entities"] if row["entity_id"] == "light.porch"]
        self.registry_row("light.porch")["hidden_by"] = "user"
        user = await self.ok(query="porch light")
        (user_score,) = [row["score"] for row in user["entities"] if row["entity_id"] == "light.porch"]
        self.assertLess(empty_score, open_score)
        self.assertEqual(empty_score, user_score)

    async def test_include_hidden_false_excludes_before_scoring(self):
        self.registry_row("light.porch")["hidden_by"] = ""
        data = await self.ok(query="porch", exact_match=False, include_hidden=False)
        self.assertNotIn("light.porch", [row["entity_id"] for row in data["entities"]])
        # A hidden entity is not even a fuzzy candidate: its aliases are not read.
        for batch in self.batches("config/entity_registry/get_entries"):
            for payload in batch:
                self.assertNotIn("light.porch", payload["entity_ids"])
        self.assertNotIn("light.hall", json.dumps(data["entities"]))

    async def test_domain_filter_narrows_before_scoring(self):
        data = await self.ok(query="kitchen", exact_match=False, domain_filter="light")
        self.assertTrue(data["entities"])
        self.assertEqual({row["domain"] for row in data["entities"]}, {"light"})
        for batch in self.batches("config/entity_registry/get_entries"):
            for payload in batch:
                self.assertTrue(all(entity_id.startswith("light.") for entity_id in payload["entity_ids"]))
        # X1: a domain intent skips configuration search unless search_types is explicit.
        self.assertEqual(self.transport.calls, 0)
        self.assertEqual(data["config_total_matches"], 0)

    async def test_a_null_alias_is_dropped_and_a_non_string_alias_is_disclosed(self):
        with patch.dict(w3.ALIASES, {"light.porch": [None, "Veranda"]}):
            data = await self.ok(query="veranda", exact_match=False)
        self.assertIn("light.porch", [row["entity_id"] for row in data["entities"]])
        with patch.dict(w3.ALIASES, {"light.porch": [7, "Veranda"]}):
            data = await self.ok(query="veranda", exact_match=False)
        self.assertNotIn("light.porch", [row["entity_id"] for row in data["entities"]])
        self.assertEqual(reasons_of(data).get(("source_unavailable", "entity", "entity_aliases")), 1)

    async def test_the_friendly_name_falls_back_to_the_entity_id(self):
        del self.state_row("switch.fan")["attributes"]["friendly_name"]
        data = await self.ok(query="fan")
        self.assertEqual({row["entity_id"]: row["friendly_name"] for row in data["entities"]}["switch.fan"],
                         "switch.fan")

    async def test_an_empty_query_with_a_domain_lists_the_domain(self):
        for query in ("", "   "):
            with self.subTest(query=query):
                data = await self.ok(query=query, domain_filter="light")
                self.assertEqual(data["search_type"], "domain_listing")
                self.assertEqual({row["domain"] for row in data["entities"]}, {"light"})
        self.calls.clear()
        self.reads.clear()
        await self.refused_call(ErrorCode.VALIDATION_FAILURE, query="")
        self.assertEqual((self.calls, self.reads), ([], []))

    def test_the_callers_use_the_scoring_constants(self):
        uses = {}
        for name in ("entities.py", "configs.py"):
            tree = ast.parse((PACKAGE / "search" / name).read_text(encoding="utf-8"))
            uses[name] = {node.attr for node in ast.walk(tree)
                          if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                          and node.value.id == "scoring"}
        self.assertIn("AREA_QUERY_FUZZY_THRESHOLD", uses["entities.py"])
        self.assertIn("GRAPH_HIT_SCORE", uses["configs.py"])
        self.assertEqual((scoring.AREA_QUERY_FUZZY_THRESHOLD, scoring.GRAPH_HIT_SCORE), (80, 100))

    async def test_no_visibility_filter_is_read_or_reported(self):
        # HAMCP-499 is not ported: no ha-mcp settings or visibility read, and
        # no visibility caveat in the reply.
        self.serve("helper", [w4.reply_of(w4.body("helper", [w4.flow(0)]))])
        result = await self.call(query="kitchen")
        encoded = json.dumps(result)
        for absent in ("visibility_filter_active", "Coverage is limited to entities visible",
                       "VISIBLE_SCOPE"):
            self.assertNotIn(absent, encoded)
        self.assertEqual({name for name, _ in self.sent()}, {"ha_search"})


class SubcallFailureCategoryTests(NativeBase):
    """F2: a page failure's category is one of a fixed set."""

    def test_the_failure_type_keeps_only_a_fixed_category(self):
        for category in sorted(gw._SEARCH_PAGE_FAILURE_CATEGORIES):
            self.assertEqual(gw.SearchSubcallPageFailure(category).category, category)
        self.assertIn("provider_authority_changed", gw._SEARCH_PAGE_FAILURE_CATEGORIES)
        for forged in ("forged_category", "x" * 500, 7, None, ["timeout"], {"timeout": 1}):
            with self.subTest(forged=repr(forged)[:40]):
                self.assertEqual(gw.SearchSubcallPageFailure(forged).category, "invalid_response")

    async def test_a_forged_category_audits_as_invalid_response(self):
        for forged in ("forged_category", 7, "x" * 500, None, ["timeout"]):
            with self.subTest(forged=repr(forged)[:40]):
                self.audit.reset_mock()
                execute = AsyncMock(return_value=json.dumps(
                    {"success": False, "details": {"failure_category": forged}}))
                with patch.object(self.gateway, "execute", execute):
                    data = await self.ok(query="kitchen", search_types=["helper"])
                self.assertEqual(reasons_of(data),
                                 {("source_unavailable", "helper", "helper_subcall_incomplete"): None})
                self.assertEqual([record["failure_category"] for record in self.audits()], ["invalid_response"])
                encoded = json.dumps(data)
                self.assertNotIn("forged_category", encoded)
                self.assertNotIn("x" * 500, encoded)
        self.audit.reset_mock()
        execute = AsyncMock(return_value=json.dumps({"success": False, "details": {"failure_category": "timeout"}}))
        with patch.object(self.gateway, "execute", execute):
            await self.ok(query="kitchen", search_types=["helper"])
        self.assertEqual([record["failure_category"] for record in self.audits()], ["timeout"])
        self.assertEqual(self.transport.calls, 0)


class ExecuteHardeningTests(NativeBase):
    """Design section 3.3: ``execute`` serves a page only inside a request that
    carries a Core dispatch authorizer; the reader is its only caller."""

    async def page(self):
        with self.gateway._lock:
            route = self.gateway._internal["ha_search"]
        return json.loads(await self.gateway.execute(
            exposed_name="ha_search", arguments=w4.arguments(),
            reviewed_schema=gw._public_read_schema(route.entry, route.observed_tool["inputSchema"]),
            policy_entry=route.entry, admission_generation=route.generation,
            contract_fingerprint=route.contract_fingerprint,
            search_page=gw._SearchPageCapture("helpers")))

    async def test_a_page_outside_a_request_is_refused_with_zero_dispatch(self):
        self.serve("helper", [w4.reply_of(w4.body("helper", [w4.flow(0)]))])
        result = await asyncio.create_task(self.page(), context=contextvars.Context())
        self.assertIs(result["success"], False)
        self.assertEqual(result["details"]["failure_category"], "prohibited_delegation")
        self.assertEqual(self.transport.calls, 0)

    async def test_a_page_without_an_authorizer_is_refused_with_zero_dispatch(self):
        self.serve("helper", [w4.reply_of(w4.body("helper", [w4.flow(0)]))])
        self.telemetry.core_dispatch_authorizer = None
        result = await self.page()
        self.assertIs(result["success"], False)
        self.assertEqual(result["details"]["failure_category"], "prohibited_delegation")
        self.assertEqual(self.transport.calls, 0)

    async def test_a_page_inside_an_authorized_request_is_served(self):
        self.serve("helper", [w4.reply_of(w4.body("helper", [w4.flow(0)]))])
        result = await self.page()
        self.assertIs(result["success"], True, result)
        self.assertEqual(self.transport.calls, 1)
        self.authorized = False
        result = await self.page()
        self.assertIs(result["success"], False)
        self.assertEqual(self.transport.calls, 1)

    async def test_a_direct_read_of_the_internal_route_is_refused_with_zero_dispatch(self):
        # G1: without a page capture the internal-only route is never a read,
        # inside an authorized request, with the fixed page arguments or any
        # other, under the reviewed or the raw schema.
        self.serve("helper", [w4.reply_of(w4.body("helper", [w4.flow(0)]))])
        with self.gateway._lock:
            route = self.gateway._internal["ha_search"]
        for arguments in (w4.arguments(), {"query": "synthetic"}):
            for schema in (gw._public_read_schema(route.entry, route.observed_tool["inputSchema"]),
                           route.observed_tool["inputSchema"]):
                result = json.loads(await self.gateway.execute(
                    exposed_name="ha_search", arguments=deepcopy(arguments), reviewed_schema=schema,
                    policy_entry=route.entry, admission_generation=route.generation,
                    contract_fingerprint=route.contract_fingerprint))
                self.assertIs(result["success"], False)
                self.assertEqual(result["details"]["failure_category"], "prohibited_delegation")
        self.assertEqual(self.transport.calls, 0)


class RoutedTests(NativeBase):
    """Through ``AuthenticatedMcpGateway``: the static-tool Core lease is the
    native request's ``core_dispatch_authorizer`` (D5)."""

    def lease(self, **changes):
        values = {"acquire": Mock(return_value=object()), "consume": Mock(return_value=("synthetic-commit",)),
                  "revalidate": Mock(return_value=True), "finish": Mock(return_value=True),
                  "release": Mock(return_value=True), **changes}
        return SimpleNamespace(**values)

    async def post(self, arguments, *, core=None, name="ha_search"):
        self.parents = []
        core = core or self.lease()

        async def app(scope, receive, send):
            rpc = json.loads((await receive())["body"])
            telemetry = current_telemetry()
            self.parents.append((telemetry, telemetry.core_dispatch_authorizer))
            rendered = await search_module.registered_tool().run(rpc["params"]["arguments"])
            result = {"jsonrpc": "2.0", "id": rpc["id"],
                      "result": {"content": [{"type": "text", "text": rendered}], "isError": False}}
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": json.dumps(result).encode()})

        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        audit_path = directory / "audit.jsonl"
        config = gateway_settings(audit_path=str(audit_path))
        front = AuthenticatedMcpGateway(app, config, AuditLogger(str(audit_path), config.access_secret),
                                        core_runtime=core)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=front), base_url=f"http://127.0.0.1:{config.port}") as http:
            response = await http.post(f"/{config.access_secret}/mcp", json={
                "jsonrpc": "2.0", "id": "synthetic-request", "method": "tools/call",
                "params": {"name": name, "arguments": arguments}})
        audit = audit_path.read_text() if audit_path.exists() else ""
        records = [json.loads(row) for row in audit.splitlines()]
        (record,) = [record for record in records if record.get("tool_name") == name]
        return json.loads(rpc_result(response)["content"][0]["text"]), core, record, audit

    async def test_the_lease_is_the_static_route_and_every_page_carries_it(self):
        rows = [w4.flow(index) for index in range(w4.PAGE + 32)]
        self.serve("helper", w4.pages_of("helper", rows))
        pages = -(-len(rows) // w4.PAGE)
        self.assertGreater(pages, 1)
        result, core, record, _ = await self.post({"query": "kitchen", "search_types": ["helper"]})
        self.assertIs(result["success"], True, result)
        self.check_reply(result["data"])
        core.acquire.assert_called_once()
        # The gateway route normalizes (routes.py: sorted, de-duplicated); the
        # route table holds exactly the engine's declared requirements.
        acquired = tuple(core.acquire.call_args.args[0])
        self.assertEqual(acquired, tuple(sorted(set(routes.STATIC_TOOL_CORE_REQUIREMENTS["ha_search"]))))
        self.assertEqual(acquired, tuple(sorted(set(c.CORE_REQUIREMENTS))))
        lease = core.acquire.return_value
        core.consume.assert_called_once_with(lease)
        for call in core.revalidate.call_args_list:
            self.assertEqual(call.args, (lease, ("synthetic-commit",)))
        core.finish.assert_called_once()
        core.release.assert_not_called()
        ((parent, authorizer),) = self.parents
        self.assertEqual(authorizer.__qualname__.split(".")[0], "AuthenticatedMcpGateway")
        self.assertEqual(authorizer.__name__, "authorize_core_dispatch")
        self.assertEqual(len(self.children), pages)
        for child in self.children:
            self.assertIs(child.core_dispatch_authorizer, authorizer)
            self.assertEqual(child.caller_id, parent.caller_id)
            self.assertEqual(child.audit_context["parent_request_id"], parent.request_id)
        # The lease closes with the request.
        self.assertIs(parent.authorize_core_dispatch(), False)
        self.assertEqual(record["resource_ids"], {})

    async def test_a_lease_revoked_mid_scan_fails_the_page_with_a_fixed_reason(self):
        rows = [w4.flow(index) for index in range(320)]
        self.serve("helper", w4.pages_of("helper", rows))
        revoked = {"value": False}
        core = self.lease(revalidate=Mock(side_effect=lambda *args, **kwargs: not revoked["value"]))
        self.after = lambda: revoked.update(value=True)
        result, core, _, _ = await self.post({"query": "kitchen", "search_types": ["helper"]}, core=core)
        self.assertIs(result["success"], True, result)
        data = result["data"]
        self.assertEqual(self.sent(), [("ha_search", w4.arguments())])
        self.assertEqual(reasons_of(data), {("source_unavailable", "helper", "helper_subcall_incomplete"): None})
        self.assertEqual(data["helpers"], [])
        self.assertEqual([(record["result_status"], record["failure_category"]) for record in self.audits()],
                         [("failed_or_cancelled", "provider_authority_changed")])

    async def test_the_audit_is_bounded_and_carries_no_text(self):
        arguments = {"query": "synthetic-needle-query", "area_filter": "synthetic-needle-area",
                     "exact_match": False, "limit": 25, "offset": 0, "result_fields": ["entity_id", "area"]}
        result, _, record, audit = await self.post(arguments)
        self.assertIs(result["success"], True, result)
        self.assertEqual(record["parameters"], search_module.audit_parameters(arguments))
        self.assertEqual(set(record["parameters"]), set(search_module.audit_parameters({})))
        self.assertEqual((record["parameters"]["provider"], record["parameters"]["fallback"]),
                         ("engineering", "none"))
        self.assertEqual(record["resource_ids"], {})
        for needle in ("synthetic-needle-query", "synthetic-needle-area"):
            self.assertNotIn(needle, audit)

    async def test_a_complete_reply_is_audited_success(self):
        self.serve("helper", w4.pages_of("helper", [w4.flow(index) for index in range(8)]))
        result, _, record, _ = await self.post({"query": "kitchen", "search_types": ["helper"]})
        self.assertIs(result["success"], True, result)
        self.assertIs(result["data"]["partial"], False)
        self.assertEqual(result["metadata"]["completeness"], "complete")
        self.assertEqual(result["metadata"]["warning_codes"], ["entity_search_skipped"])
        self.assertEqual((record["result_status"], record.get("error_code")), ("success", None))

    async def test_a_partial_reply_is_audited_partial(self):
        # The delegated route audited a partial search "partial"; the
        # native tool keeps that status (and its public success).
        self.serve("helper", w4.pages_of("helper", [w4.flow(index) for index in range(320)]))
        revoked = {"value": False}
        core = self.lease(revalidate=Mock(side_effect=lambda *args, **kwargs: not revoked["value"]))
        self.after = lambda: revoked.update(value=True)
        result, _, record, _ = await self.post({"query": "kitchen", "search_types": ["helper"]},
                                               core=core)
        self.assertIs(result["success"], True, result)
        self.assertIs(result["data"]["partial"], True)
        self.assertEqual(result["metadata"]["completeness"], "partial")
        self.assertEqual((record["result_status"], record.get("error_code")), ("partial", None))
        # The page's own record keeps its own status.
        self.assertEqual([record["result_status"] for record in self.audits()], ["failed_or_cancelled"])

    async def test_invalid_arguments_are_refused_before_the_lease(self):
        for arguments in ({"query": "synthetic-needle-query", "synthetic_unreviewed": True},
                          {"query": "synthetic-needle-query", "limit": 0}, {}):
            with self.subTest(arguments=sorted(arguments)):
                result, core, record, audit = await self.post(arguments)
                self.assertIs(result["success"], False)
                self.assertEqual(result["error_code"], ErrorCode.VALIDATION_FAILURE.value)
                core.acquire.assert_not_called()
                self.assertEqual(self.parents, [])
                self.assertEqual(record["parameters"]["validation"], "rejected")
                self.assertEqual((record["result_status"], record.get("error_code")),
                                 ("failure", ErrorCode.VALIDATION_FAILURE.value))
                self.assertNotIn("synthetic-needle-query", audit)
                self.assertNotIn("synthetic-needle-query", json.dumps(result))
        self.assertEqual((self.calls, self.reads, self.transport.calls), ([], [], 0))

    async def test_an_unavailable_lease_refuses_before_the_tool(self):
        result, core, _, _ = await self.post({"query": "kitchen"}, core=self.lease(acquire=Mock(return_value=None)))
        self.assertIs(result["success"], False)
        self.assertEqual(result["error_code"], ErrorCode.PROVIDER_UNAVAILABLE.value)
        self.assertEqual(result["error"], "CoreCapabilityUnavailable")
        self.assertEqual(self.parents, [])
        core.consume.assert_not_called()
        self.assertEqual((self.calls, self.reads, self.transport.calls), ([], [], 0))

    async def test_the_delegated_alias_is_refused_truthfully(self):
        # HAMCP-408: native ha_search has a native capability and is served;
        # the delegated alias is never published and is refused unreached.
        self.assertTrue(capabilities.capability_for_tool("ha_search"))
        self.assertIn("ha_mcp__ha_search", capabilities.reviewed_delegated_read_names())
        result, core, _, _ = await self.post({"query": "kitchen"}, name="ha_mcp__ha_search")
        self.assertIs(result["success"], False)
        self.assertEqual(result["error_code"], ErrorCode.PROVIDER_UNAVAILABLE.value)
        self.assertIn(result["error"], ("DelegatedToolNotAdmitted", "UpstreamNotAdmitted"))
        core.acquire.assert_not_called()
        self.assertEqual(self.parents, [])
        self.assertEqual((self.calls, self.transport.calls), ([], 0))


class UnifiedNativeTests(NativeTests):
    """The same end-to-end tests under the ``unified`` 9.2.0 descriptor, the
    live variant (fingerprint c1ca76bd, per the HA Admin baseline)."""

    UNIFIED = True


class UnifiedRoutedTests(RoutedTests):
    """D5 under the live ``unified`` descriptor."""

    UNIFIED = True


class GateCore:
    """A Core runtime whose one switch withdraws the sub-call requirement."""

    def __init__(self, unavailable=()):
        self.unavailable = set(unavailable)
        self.acquired: list = []
        self.withhold_lease = False
        self.current_observation = None

    def route_status(self, requirements, **kwargs):
        available = not set(requirements) & self.unavailable
        return {"available": available, "disposition": "admitted" if available else "core_unavailable",
                "reason_codes": [] if available else ["synthetic_core_unavailable"], "generation": 1}

    def acquire(self, requirements, **kwargs):
        self.acquired.append((tuple(requirements), kwargs.get("delegated_tool")))
        if self.withhold_lease or set(requirements) & self.unavailable:
            return None
        return object()

    def consume(self, *args, **kwargs):
        return ()

    def revalidate(self, *args, **kwargs):
        return True

    def finish(self, *args, **kwargs):
        return True

    def release(self, *args, **kwargs):
        return True

    def record_catalog_change(self, **kwargs):
        return None


class SubcallCoreGateTests(w4.Readers):
    """G2: the sub-call route keeps its own Core requirement, fail-closed."""

    async def asyncSetUp(self):
        self.gate = GateCore()
        with patch.object(w4, "gateway_920", partial(captures.gateway_920, core_runtime=self.gate)):
            await super().asyncSetUp()

    async def test_each_page_acquires_the_route_requirement(self):
        self.assertEqual(routes.DELEGATED_CORE_REQUIREMENTS["ha_search"], (SUBCALL_REQUIREMENT,))
        self.serve("helper", [w4.reply_of(w4.body("helper", [w4.flow(0)]))])
        page = await self.readers["helper"](w4.arguments())
        self.assertEqual(page.rows, (w4.flow(0),))
        self.assertIn(((SUBCALL_REQUIREMENT,), "ha_search"), self.gate.acquired)
        self.assertEqual(self.transport.calls, 1)

    async def test_a_requirement_withdrawn_after_admission_refuses_the_page(self):
        self.gate.unavailable.add(SUBCALL_REQUIREMENT)
        await self.failed("provider_authority_changed")
        self.assertEqual(self.transport.calls, 0)

    async def test_a_withheld_lease_refuses_the_page(self):
        self.gate.withhold_lease = True
        self.serve("helper", [w4.reply_of(w4.body("helper", [w4.flow(0)]))])
        with self.assertRaises(w4.Failure):
            await self.readers["helper"](w4.arguments())
        self.assertEqual(self.transport.calls, 0)
        result = await self.go(query="kitchen", search_types=["helper"])
        self.assertEqual(reasons_of(result), {("source_unavailable", "helper", "helper_subcall_incomplete"): None})
        self.assertEqual(self.transport.calls, 0)

    async def test_a_requirement_unavailable_at_admission_admits_no_reader(self):
        gate = GateCore(unavailable={SUBCALL_REQUIREMENT})
        gateway, transport = await captures.gateway_920(core_runtime=gate, url=w4.URL)
        self.assertIsNone(gateway.search_subcall_reader())
        with gateway._lock:
            self.assertNotIn("ha_search", gateway._internal)
            self.assertNotIn("ha_search", [route.entry.upstream_name for route in gateway._exposed.values()])
        self.assertEqual(transport.calls, 0)


class CatalogVisibilityTests(unittest.IsolatedAsyncioTestCase):
    """G1: whenever native ``ha_search`` is registered, the delegated one is
    never listed or aliased, on every version; only 9.2.0 Row 25 reads it."""

    async def served(self, build, **kwargs):
        servers = []

        def preloaded(name):
            server = FastMCP(name)
            registry = McpSdkToolRegistry(server)
            registry.replace({**registry.snapshot(), "ha_search": search_module.registered_tool()})
            servers.append(server)
            return server

        with patch.object(captures, "FastMCP", preloaded):
            gateway, transport = await build(**kwargs)
        (server,) = servers
        return gateway, transport, await server.list_tools()

    async def test_one_native_ha_search_is_listed_on_every_version(self):
        cases = (("9.2.0 legacy", captures.gateway_920, {"url": w4.URL}, True),
                 ("9.2.0 unified", captures.gateway_920, {"tools": w4.unified_catalog(), "url": w4.URL}, True),
                 ("8.6.0", captures.gateway_860, {}, False))
        for label, build, kwargs, reads in cases:
            with self.subTest(catalog=label):
                gateway, transport, listed = await self.served(build, **kwargs)
                names = [tool.name for tool in listed]
                self.assertEqual(names.count("ha_search"), 1)
                self.assertEqual([name for name in names if name.endswith("ha_search") and name != "ha_search"], [])
                (tool,) = [tool for tool in listed if tool.name == "ha_search"]
                self.assertEqual(tool.inputSchema, search_module.NATIVE_SCHEMA)
                self.assertEqual(tool.description, search_module.DESCRIPTION)
                with gateway._lock:
                    exposed = dict(gateway._exposed)
                    internal = dict(gateway._internal)
                self.assertEqual([route for route in exposed.values() if route.entry.upstream_name == "ha_search"],
                                 [])
                # 9.2.0 admits the replaced read internal-only; 8.6.0 admits
                # no delegated read in this tree, so nothing is internal.
                self.assertEqual(set(internal), {"ha_search"} if reads else set())
                self.assertEqual(set(names), {"ha_search"} | set(exposed))
                self.assertEqual(gateway.search_subcall_reader() is not None, reads)
                self.assertEqual(transport.calls, 0)


class ContractTests(unittest.TestCase):
    """C1, R1 and the native registration."""

    def test_the_native_schema_differs_only_in_the_search_types_sentence(self):
        upstream = reads_9_2.PUBLIC_SCHEMAS["ha_search"]
        expected = deepcopy(upstream)
        description = expected["properties"]["search_types"]["description"]
        expected["properties"]["search_types"]["description"] = (
            f"{description} {search_module.SEARCH_TYPES_SENTENCE}")
        self.assertEqual(search_module.NATIVE_SCHEMA, expected)
        self.assertNotEqual(search_module.NATIVE_SCHEMA, upstream)
        self.assertEqual(schema_fingerprint(search_module.NATIVE_SCHEMA),
                         search_module.NATIVE_SEARCH_SCHEMA_FINGERPRINT)
        self.assertEqual(search_module.NATIVE_SEARCH_SCHEMA_FINGERPRINT,
                         "47b15a3ef3bbf63bb3485362ea00dfe43c8093b07698a26fb6bf15606286ec4c")
        # Design 4.1 as answered: the 9.2.0 schema and its fingerprint, which
        # bind the sub-call route, are unchanged.
        self.assertEqual(schema_fingerprint(upstream), reads_9_2.SEARCH_PUBLIC_SCHEMA_FINGERPRINT)
        self.assertEqual(reads_9_2.SEARCH_PUBLIC_SCHEMA_FINGERPRINT,
                         "c6bd01c71eea48b7fbe75c8d86f762b2336ab685e99b3e16acd91bed73e3d5bf")

    def test_the_warning_codes_are_closed_and_one_per_text(self):
        codes = reply_module.WARNING_CODES
        self.assertEqual(tuple(codes), reply_module.WARNINGS)
        self.assertEqual(len(set(codes.values())), len(codes))
        for code in codes.values():
            self.assertRegex(code, r"^[a-z]+(_[a-z]+)*$")
        self.assertEqual(reply_module.warning_codes({"warnings": [reply_module.WARNING_AMBIGUOUS_FLOOR]}),
                         ["ambiguous_floor_match"])
        self.assertEqual(reply_module.warning_codes({}), [])
        with self.assertRaises(KeyError):
            reply_module.warning_codes({"warnings": ["synthetic unlisted text"]})

    def test_the_registered_tool_is_the_native_contract(self):
        tool = search_module.registered_tool()
        self.assertEqual((tool.name, tool.description, tool.parameters),
                         ("ha_search", search_module.DESCRIPTION, search_module.NATIVE_SCHEMA))
        self.assertIsNot(tool.parameters, search_module.NATIVE_SCHEMA)
        annotations = tool.annotations
        self.assertEqual((annotations.readOnlyHint, annotations.destructiveHint, annotations.idempotentHint,
                          annotations.openWorldHint), (True, False, True, False))
        with patch.object(search_module, "NATIVE_SEARCH_SCHEMA_FINGERPRINT", "0" * 64), \
                self.assertRaisesRegex(RuntimeError, "native_search_schema_fingerprint_mismatch"):
            search_module.registered_tool()

    def test_the_routes_bind_the_native_reads_and_keep_the_sub_call_gate(self):
        self.assertEqual(routes.STATIC_TOOL_CORE_REQUIREMENTS["ha_search"], c.CORE_REQUIREMENTS)
        # HAMCP-501 W6 S1 b+: search reads no configuration body, so it needs
        # no automation configuration authority.
        self.assertEqual(len(c.CORE_REQUIREMENTS), 4)
        self.assertNotIn("core.automation_configuration_read", c.CORE_REQUIREMENTS)
        self.assertEqual(routes.INTERNAL_ONLY_DELEGATED_READS, frozenset({"ha_search"}))
        self.assertEqual(routes.DELEGATED_CORE_REQUIREMENTS["ha_search"], (SUBCALL_REQUIREMENT,))
        self.assertIn("ha_search", routes.DEVICE_DEPENDENT_DELEGATED_TOOLS)
        self.assertIsNone(routes.static_tool_target("ha_search", {"query": "kitchen"}))


class ReachabilityTests(unittest.TestCase):
    """Design section 3.3: no direct ``execute(search_page=)``."""

    @staticmethod
    def functions_calling_with(keyword):
        found = set()
        for path in PACKAGE.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))

            def visit(node, chain):
                for child in ast.iter_child_nodes(node):
                    inner = chain
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        inner = (*chain, child.name)
                    if isinstance(child, ast.Call) and any(item.arg == keyword for item in child.keywords):
                        found.add((path.relative_to(PACKAGE).as_posix(), chain))
                    visit(child, inner)

            visit(tree, ())
        return found

    def test_the_page_reader_is_the_only_search_page_caller(self):
        self.assertEqual(self.functions_calling_with("search_page"),
                         {("providers/upstream_read_gateway.py", ("search_subcall_reader", "page_reader", "read"))})

    def test_the_tool_and_engine_never_reach_the_gateway_directly(self):
        paths = [PACKAGE / "tools/search.py", *sorted((PACKAGE / "search").glob("*.py"))]
        for path in paths:
            with self.subTest(path=path.relative_to(PACKAGE).as_posix()):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                modules = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
                modules |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                            for alias in node.names}
                imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                            for alias in node.names}
                self.assertEqual([module for module in modules if "upstream_read_gateway" in module], [])
                self.assertEqual([name for name in imported
                                  if name in {"upstream_read_gateway", "_SearchPageCapture", "UpstreamReadGateway"}],
                                 [])
                attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
                self.assertNotIn("execute", attributes)
                self.assertNotIn("_SearchPageCapture", attributes)
                names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
                self.assertNotIn("_SearchPageCapture", names)



class InternalReaderSchemaSweepTests(unittest.IsolatedAsyncioTestCase):
    """The W4 defect class, swept: an internal reader that validates with a
    schema other than the reviewed one. The three other internal readers pass
    the raw observed schema. On the admitted 9.2.0 routes it is the reviewed
    schema (script) or a superset whose extra keys the reader never sends and
    the dispatch shape pins (history: ``upstream_history_9_1.dispatch_arguments``;
    YAML: ``yaml_reader.validate_arguments`` and ``upstream_arguments``). The
    arguments each reader really sends validate the same under both."""

    RAW_ONLY = {"ha_config_get_script": set(),
                "ha_get_history": {"core_options", "include_schema", "summary"},
                yaml_reader.TOOL_NAME: {"include_content", "include_parsed"}}
    TIME = "2026-01-01T00:00:00.000000+00:00"

    async def test_each_internal_reader_validates_the_same_under_both_schemas(self):
        gateway, _ = await captures.gateway_920(tools=yaml_admission.captured("9.2.0"))
        with gateway._lock:
            routes = {route.entry.upstream_name: route for route in gateway._exposed.values()
                      if route.entry.upstream_name in self.RAW_ONLY}
        self.assertEqual(set(routes), set(self.RAW_ONLY))
        readers = {"ha_config_get_script": (gateway.script_dependency_reader(), [("script.synthetic",)]),
                   "ha_get_history": (gateway.history_reader(),
                                      [("sensor.synthetic", self.TIME, self.TIME, 0, 10)])}
        for purpose, (paths, _event) in gw.YAML_READER_PURPOSES.items():
            readers[f"{yaml_reader.TOOL_NAME}:{purpose}"] = (
                gateway.yaml_config_reader(purpose=purpose), [(path,) for path in sorted(paths)])
        sent = []

        async def execute(**kwargs):
            sent.append(kwargs)
            return json.dumps({"success": True})

        telemetry, token = begin_request("synthetic-w6-sweep")
        telemetry.core_dispatch_authorizer = lambda: True
        try:
            with patch.object(gateway, "execute", execute):
                for key, (reader, calls) in readers.items():
                    with self.subTest(reader=key):
                        self.assertIsNotNone(reader)
                        for call in calls:
                            sent.clear()
                            self.assertIs((await reader.read(*call))["success"], True)
                            (kwargs,) = sent
                            route = routes[key.split(":")[0]]
                            raw = route.observed_tool["inputSchema"]
                            public = gw._public_read_schema(route.entry, raw)
                            self.assertIs(kwargs["reviewed_schema"], raw)
                            extra = self.RAW_ONLY[route.entry.upstream_name]
                            self.assertEqual(set(raw["properties"]) - set(public["properties"]), extra)
                            self.assertEqual(set(public["properties"]) - set(raw["properties"]), set())
                            self.assertIs(raw["additionalProperties"], False)
                            self.assertIs(public["additionalProperties"], False)
                            if not extra:
                                self.assertEqual(schema_fingerprint(raw), schema_fingerprint(public))
                            self.assertFalse(set(kwargs["arguments"]) & extra)
                            for schema in (raw, public):
                                self.assertEqual(list(Draft202012Validator(schema).iter_errors(kwargs["arguments"])), [])
        finally:
            end_request(token)


if __name__ == "__main__":
    unittest.main()
