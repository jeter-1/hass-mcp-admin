"""HAMCP-319 section 7 ledger rows 1-11: 9.1.0 parity with the shipped 8.5 surface.

Every call is an 8.5-shaped public call through the real upstream read gateway
with a fake transport. 9.1 responses are class S fixtures transcribed from the
pinned 9.1.0 source (tests/fixtures/ha_mcp_9_1_0/config/provenance.json).

Evidence class of the 8.5-side rows (architect ruling 2026-11-07, item 1): **S
(8.6 replay), never C.** No 8.5.0 capture exists for rows 1-11, so the 8.5.0
policy is exercised with the decoded 8.6.0 standalone records
(tests/fixtures/ha_mcp_8_6_0/read-results.json). Those records prove the 8.5.0
gateway's pass-through handling of that shape, not 8.5.0 upstream output; the
live 8.5.0 proof is the 8.5 exact-image lanes. No live Home Assistant, no
network.
"""
from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from mcp.server.fastmcp import FastMCP  # noqa: E402

from ha_mcp_engineering.ha_mcp_readmission import ha_mcp as readmission  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_1  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_1_config as config  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import load_reviewed_upstream_release_registry  # noqa: E402
from test_ha_mcp_production_readmission import _GatewayTransport, _settings  # noqa: E402
from test_upstream_8_5_compatibility import capture  # noqa: E402

FIXTURES_91 = json.loads(
    (ROOT / "tests/fixtures/ha_mcp_9_1_0/config/responses.json").read_text("utf-8")
)
_READS_86 = json.loads(
    (ROOT / "tests/fixtures/ha_mcp_8_6_0/read-results.json").read_text("utf-8")
)
# S (8.6 replay): 8.6.0 records fed to the 8.5.0 policy; not 8.5.0 captures.
FIXTURES_85 = {entry["upstream_name"]: entry for entry in _READS_86["standalone"][:11]}
CANARY = "SYNTHETIC-CANARY-319-CFG"
ADAPTER_91 = "ha_mcp_error_contract_9_1_0_v1"
ADAPTER_85 = "ha_mcp_error_contract_8_5_0_v1"

AUTOMATION = "ha_config_get_automation"
CALENDAR = "ha_config_get_calendar_events"
CATEGORY = "ha_config_get_category"
LABEL = "ha_config_get_label"
SCENE = "ha_config_get_scene"
SCRIPT = "ha_config_get_script"
RESOURCES = "ha_config_list_dashboard_resources"
GROUPS = "ha_config_list_groups"
HELPERS = "ha_config_list_helpers"
TEMPLATE = "ha_eval_template"
TRACES = "ha_get_automation_traces"
ROW_TOOLS = (
    AUTOMATION, CALENDAR, CATEGORY, LABEL, SCENE, SCRIPT, RESOURCES, GROUPS,
    HELPERS, TEMPLATE, TRACES,
)


def ok(payload):
    return {
        "isError": False,
        "structuredContent": deepcopy(payload),
        "content": [{"type": "text", "text": json.dumps(payload)}],
    }


def error(payload):
    return {"isError": True, "content": [{"type": "text", "text": json.dumps(payload)}]}


def fixture(key):
    entry = FIXTURES_91["success"][key]
    return deepcopy(entry["arguments"]), deepcopy(entry["structuredContent"])


class Transport(_GatewayTransport):
    def __init__(self, tools, version):
        super().__init__(tools, version=version)
        self.sent = []
        self.response = None

    async def execute_read(self, tool, arguments, **kwargs):
        exchange = await super().execute_read(tool, arguments, **kwargs)
        self.sent.append((tool, deepcopy(arguments)))
        return replace(exchange, call_result=deepcopy(self.response))


class _Base(unittest.IsolatedAsyncioTestCase):
    version = "9.1.0"

    async def asyncSetUp(self):
        self.transport = Transport(capture(self.version)["tools"], self.version)
        self.gateway = UpstreamReadGateway()
        self.gateway.configure(
            replace(_settings("synthetic-no-key"), ha_mcp_release_registry_enabled=False),
            transport=self.transport,
        )
        await self.gateway.initialize(FastMCP(f"synthetic-hamcp319-cfg-{self.version}"))

    async def call(self, name, arguments, response=None):
        self.transport.response = response
        _, token = begin_request("synthetic-hamcp319-cfg")
        try:
            return json.loads(
                await self.gateway._registered_tool_registry.snapshot()[name].run(arguments)
            )
        finally:
            end_request(token)

    def succeeded(self, result, *, complete=True):
        self.assertTrue(result.get("success"), result)
        metadata = result["metadata"]
        self.assertEqual(metadata["provider"], "upstream_read_gateway")
        self.assertEqual(metadata["upstream_version"], self.version)
        self.assertFalse(metadata["fallback_occurred"])
        self.assertEqual(metadata["completeness"], "complete" if complete else "partial", result)
        if complete:
            self.assertEqual(result["warnings"], [], result)
        return result["data"]

    def refused(self, result, *, dispatched, category):
        self.assertFalse(result.get("success"), result)
        self.assertNotIn("data", result)
        self.assertEqual(result["metadata"]["upstream_dispatch_occurred"], dispatched, result)
        self.assertFalse(result["metadata"]["fallback_occurred"])
        self.assertEqual(result["details"]["failure_category"], category, result)
        self.assertNotIn(CANARY, json.dumps(result))

    async def assert_refused_unsent(self, name, arguments, category="argument_validation"):
        before = len(self.transport.sent)
        result = await self.call(name, arguments, ok({"success": True}))
        self.refused(result, dispatched=False, category=category)
        self.assertEqual(len(self.transport.sent), before, "refusal reached the peer")

    async def assert_invalid_response(self, name, arguments, payload):
        before = len(self.transport.sent)
        result = await self.call(name, arguments, ok(payload))
        self.refused(result, dispatched=True, category="invalid_response")
        self.assertEqual(len(self.transport.sent), before + 1, "no retry or fallback")

    def last_sent(self, name):
        tool, arguments = self.transport.sent[-1]
        self.assertEqual(tool, name)
        return arguments


class Parity91Tests(_Base):
    """9.1.0 adapter rows 1-11 against class S 9.1 fixtures."""

    version = "9.1.0"

    async def test_91_parity_row_1_automation(self):
        arguments, payload = fixture("row_1_automation")
        data = self.succeeded(await self.call(AUTOMATION, arguments, ok(payload)))
        self.assertEqual(data, payload)
        self.assertEqual(self.last_sent(AUTOMATION), {"entity_id": "automation.gateway_fixture"})
        # Upstream warnings stay untrusted data; they never become gateway warnings.
        warned = payload | {"warnings": ["Deprecated syntax detected."]}
        result = await self.call(AUTOMATION, arguments, ok(warned))
        self.assertEqual(self.succeeded(result), warned)
        self.assertNotIn("Deprecated syntax detected.", result["warnings"])
        await self.assert_refused_unsent(AUTOMATION, {"entity_id": "automation.x"})
        await self.assert_refused_unsent(AUTOMATION, {"identifier": "   "})
        for broken in (
            {key: value for key, value in payload.items() if key != "references"},
            payload | {"entity": "automation.gateway_fixture"},
            payload | {"action": "list"},
            payload | {"config": "alias: x"},
            payload | {"success": False},
            payload | {"warnings": [1]},
            [payload],
        ):
            await self.assert_invalid_response(AUTOMATION, arguments, broken)

    async def test_91_parity_row_2_calendar(self):
        arguments, payload = fixture("row_2_calendar")
        data = self.succeeded(await self.call(CALENDAR, arguments, ok(payload)))
        self.assertEqual(data, payload)
        self.assertEqual(self.last_sent(CALENDAR), arguments)
        await self.assert_refused_unsent(CALENDAR, arguments | {"calendar_id": "x"})
        events = payload["events"] * 6
        for broken in (
            payload | {"entity_id": "calendar.other"},
            payload | {"count": 2},
            payload | {"events": events, "count": 6, "total_available": 6},
            payload | {"total_available": 0},
            payload | {"time_range": {"start": None, "end": "2026-07-22T00:00:00+00:00"}},
            payload | {"count": True, "events": [payload["events"][0]]},
        ):
            await self.assert_invalid_response(CALENDAR, arguments, broken)

    async def test_91_parity_row_3_category(self):
        for key in ("row_3_category_list", "row_3_category_get"):
            arguments, payload = fixture(key)
            data = self.succeeded(await self.call(CATEGORY, arguments, ok(payload)))
            self.assertEqual(data, payload)
            self.assertEqual(self.last_sent(CATEGORY), arguments)
        await self.assert_refused_unsent(CATEGORY, {})
        await self.assert_refused_unsent(CATEGORY, {"scope": "automation", "name": "x"})
        list_arguments, listed = fixture("row_3_category_list")
        get_arguments, got = fixture("row_3_category_get")
        for arguments, broken in (
            (list_arguments, listed | {"scope": "script"}),
            (list_arguments, listed | {"count": 0}),
            (list_arguments, got),
            (get_arguments, got | {"category_id": "other"}),
            (get_arguments, listed),
            (get_arguments, got | {"category": None}),
        ):
            await self.assert_invalid_response(CATEGORY, arguments, broken)

    async def test_91_parity_row_4_label(self):
        for key in ("row_4_label_list", "row_4_label_get"):
            arguments, payload = fixture(key)
            data = self.succeeded(await self.call(LABEL, arguments, ok(payload)))
            self.assertEqual(data, payload)
            self.assertEqual(self.last_sent(LABEL), arguments)
        await self.assert_refused_unsent(LABEL, {"name": "x"})
        list_arguments, listed = fixture("row_4_label_list")
        get_arguments, got = fixture("row_4_label_get")
        for arguments, broken in (
            (list_arguments, listed | {"count": 3}),
            (list_arguments, got),
            (get_arguments, got | {"label_id": "other"}),
            (get_arguments, listed),
            (get_arguments, {key: value for key, value in got.items() if key != "message"}),
        ):
            await self.assert_invalid_response(LABEL, arguments, broken)

    async def test_91_parity_row_5_scene(self):
        arguments, payload = fixture("row_5_scene")
        data = self.succeeded(await self.call(SCENE, arguments, ok(payload)))
        self.assertEqual(data, payload)
        self.assertEqual(self.last_sent(SCENE), {"scene_id": "gateway_fixture"})
        # Discovery inputs are never sent: they are refused before dispatch.
        for extra in ({"query": "x"}, {"search_in_config": True}, {"limit": 5}, {"offset": 1}):
            await self.assert_refused_unsent(SCENE, arguments | extra)
        await self.assert_refused_unsent(SCENE, {})
        await self.assert_refused_unsent(SCENE, {"scene_id": "  "})
        for broken in (
            {"success": True, "scenes": [], "total_count": 0, "has_more": False},
            payload | {"scenes": []},
            payload | {"category": 7},
            {key: value for key, value in payload.items() if key != "config_hash"},
        ):
            await self.assert_invalid_response(SCENE, arguments, broken)

    async def test_91_parity_row_6_script(self):
        arguments, payload = fixture("row_6_script")
        data = self.succeeded(await self.call(SCRIPT, arguments, ok(payload)))
        # The dependency consumer needs complete, success, script_id and config.
        self.assertEqual(data, payload)
        self.assertEqual(data["script_id"], "gateway_fixture")
        self.assertIsInstance(data["config"], dict)
        self.assertEqual(self.last_sent(SCRIPT), arguments)
        await self.assert_refused_unsent(SCRIPT, {})
        await self.assert_refused_unsent(SCRIPT, arguments | {"raw": True})
        for broken in (
            payload | {"config": None},
            payload | {"action": "list"},
            {key: value for key, value in payload.items() if key != "script_id"},
            payload | {"warnings": "single"},
        ):
            await self.assert_invalid_response(SCRIPT, arguments, broken)

    async def test_91_parity_row_7_dashboard_resources(self):
        arguments, payload = fixture("row_7_resources")
        data = self.succeeded(await self.call(RESOURCES, arguments, ok(payload)))
        # The shipped 8.5 css/js/module keys are zero-filled, never invented.
        self.assertEqual(data, payload | {"by_type": {"css": 1, "js": 0, "module": 1}})
        sent = self.last_sent(RESOURCES)
        self.assertEqual(sent, arguments)
        self.assertNotIn("include_hash", sent)
        yaml_mode = payload | {"resource_mode": "yaml", "note": "synthetic yaml note"}
        data = self.succeeded(await self.call(RESOURCES, arguments, ok(yaml_mode)))
        self.assertEqual((data["resource_mode"], data["note"]), ("yaml", "synthetic yaml note"))
        await self.assert_refused_unsent(RESOURCES, arguments | {"include_hash": True})
        row = payload["resources"][0]
        for broken in (
            payload | {"resources": [row | {"_config_hash": "abc"}]},
            payload | {"has_more": False},
            payload | {"next_offset": None},
            payload | {"limit": 100},
            payload | {"count": 2},
            payload | {"by_type": {"module": 3}},
            payload | {"by_type": {"module": -1}},
            payload | {"inline_count": 3},
            payload | {"action": "get"},
        ):
            await self.assert_invalid_response(RESOURCES, arguments, broken)

    async def test_91_parity_row_8_groups(self):
        arguments, payload = fixture("row_8_groups")
        result = await self.call(GROUPS, arguments, ok(payload))
        data = self.succeeded(result, complete=False)
        self.assertEqual(result["warnings"], [config.GROUP_FIELDS_UNAVAILABLE_WARNING])
        first, second = data["groups"]
        self.assertEqual(first["unavailable_fields"], ["all"])
        self.assertNotIn("all", first)
        self.assertNotIn("unavailable_fields", second)
        self.assertEqual(second["all"], True)
        expected = deepcopy(payload)
        expected["groups"][0]["unavailable_fields"] = ["all"]
        self.assertEqual(data, expected)
        self.assertEqual(self.last_sent(GROUPS), arguments)
        # A record that reports both fields stays complete and undisclosed.
        full = deepcopy(payload)
        full["groups"][0]["all"] = False
        result = await self.call(GROUPS, arguments, ok(full))
        self.assertEqual(self.succeeded(result), full)
        await self.assert_refused_unsent(GROUPS, arguments | {"group_id": "x"})
        record = payload["groups"][0]
        for broken in (
            payload | {"groups": [record | {"unavailable_fields": []}, payload["groups"][1]]},
            payload | {"groups": [record | {"entity_id": "light.x"}, payload["groups"][1]]},
            payload | {"groups": [record | {"object_id": "other"}, payload["groups"][1]]},
            payload | {"groups": [{k: v for k, v in record.items() if k != "entity_ids"}, payload["groups"][1]]},
            payload | {"count": 1},
            payload | {"total_count": 9},
        ):
            await self.assert_invalid_response(GROUPS, arguments, broken)

    async def test_91_parity_row_9_helpers(self):
        arguments, payload = fixture("row_9_helpers")
        data = self.succeeded(await self.call(HELPERS, arguments, ok(payload)))
        self.assertEqual(data, payload)
        sent = self.last_sent(HELPERS)
        self.assertEqual(sent, arguments | {"describe": False})
        self.assertNotIn("menu_choice", sent)
        self.assertNotIn("helper_id", sent)
        for extra in ({"describe": True}, {"menu_choice": "x"}, {"helper_id": "x"}):
            await self.assert_refused_unsent(HELPERS, arguments | extra)
        await self.assert_refused_unsent(HELPERS, {"helper_type": "unknown_type"})
        unpaginated = {
            "success": True, "helper_type": "input_boolean", "count": 1,
            "helpers": {"storage": []}, "message": "Found 1 input_boolean helper(s)",
            "warnings": ["Listing could not be paginated"],
        }
        for broken in (
            unpaginated,
            payload | {"helper_type": "counter"},
            payload | {"has_more": True},
            {"success": True, "helper_type": "input_boolean", "flows": []},
        ):
            await self.assert_invalid_response(HELPERS, arguments, broken)

    async def test_91_parity_row_10_eval_template(self):
        arguments, payload = fixture("row_10_template")
        result = await self.call(TEMPLATE, arguments, ok(payload))
        data = self.succeeded(result, complete=False)
        self.assertEqual(result["warnings"], [config.TEMPLATE_FIELDS_UNAVAILABLE_WARNING])
        self.assertEqual(data, payload | {"unavailable_fields": ["evaluation_time", "request_id"]})
        self.assertNotIn("request_id", data)
        self.assertNotIn("evaluation_time", data)
        self.assertEqual(self.last_sent(TEMPLATE), {
            "template": "{{ 1 + 1 }}", "timeout": 3, "report_errors": True,
            "condition": None, "variables": None, "strict": False,
        })
        for timeout in (1, 60):
            self.succeeded(await self.call(TEMPLATE, arguments | {"timeout": timeout}, ok(payload)), complete=False)
            self.assertEqual(self.last_sent(TEMPLATE)["timeout"], timeout)
        for bad in ({"timeout": 0}, {"timeout": 61}, {"timeout": 2.5}, {"timeout": True},
                    {"condition": "x"}, {"variables": {}}, {"strict": True}):
            await self.assert_refused_unsent(TEMPLATE, arguments | bad)
        for broken in (
            payload | {"template": "{{ 2 }}"},
            payload | {"request_id": "synthetic"},
            payload | {"evaluation_time": 0.1},
            payload | {"unavailable_fields": []},
            payload | {"condition": True},
            payload | {"listeners": []},
            {key: value for key, value in payload.items() if key != "result"},
        ):
            await self.assert_invalid_response(TEMPLATE, arguments, broken)

    async def test_91_parity_row_11_automation_traces(self):
        for key in ("row_11_traces_list", "row_11_traces_detail"):
            arguments, payload = fixture(key)
            data = self.succeeded(await self.call(TRACES, arguments, ok(payload)))
            self.assertEqual(data, payload)
            self.assertEqual(self.last_sent(TRACES), arguments)
        await self.assert_refused_unsent(TRACES, {"automation_id": "automation.x", "order": "random"})
        await self.assert_refused_unsent(TRACES, {"automation_id": "automation.x", "limit": 51})
        list_arguments, listed = fixture("row_11_traces_list")
        detail_arguments, detail = fixture("row_11_traces_detail")
        for arguments, broken in (
            (list_arguments, listed | {"automation_id": "automation.other"}),
            (list_arguments, listed | {"order": "oldest"}),
            (list_arguments, listed | {"offset": 1}),
            (list_arguments, listed | {"trace_count": 0}),
            (list_arguments, listed | {"has_more": True}),
            (list_arguments | {"limit": 1}, listed | {"traces": listed["traces"] * 2, "trace_count": 2, "total_available": 2}),
            (list_arguments, detail),
            (detail_arguments, detail | {"run_id": "other"}),
            (detail_arguments, listed),
            (detail_arguments, {key: value for key, value in detail.items() if key != "state"}),
        ):
            await self.assert_invalid_response(TRACES, arguments, broken)

    async def test_91_parity_rows_1_11_bounded_output(self):
        arguments, payload = fixture("row_9_helpers")
        # 20 records of 4000 characters exceed the 60000-byte policy limit for
        # this row while each string stays below the sanitizer's string cap.
        huge = payload | {"helpers": [{"id": f"h{i}", "name": "x" * 4000} for i in range(20)],
                          "count": 20, "total_count": 20}
        result = await self.call(HELPERS, arguments | {"limit": 20}, ok(huge | {"limit": 20}))
        self.refused(result, dispatched=True, category="response_too_large")
        self.assertLess(len(json.dumps(result)), 20000)

    async def test_91_parity_rows_1_11_upstream_errors_fail_closed(self):
        arguments, _ = fixture("row_5_scene")
        # CONFIG_NOT_FOUND has no domain mapping: generic upstream_error, no widening.
        result = await self.call(SCENE, arguments, error({
            "success": False, "error": {"code": "CONFIG_NOT_FOUND", "message": f"{CANARY}"},
            "scene_id": "gateway_fixture"}))
        self.refused(result, dispatched=True, category="upstream_error")
        result = await self.call(SCENE, arguments, error({
            "success": False, "error": {"code": "ENTITY_NOT_FOUND", "message": CANARY}}))
        self.refused(result, dispatched=True, category="entity_not_found")
        for tool, key in ((CALENDAR, "row_2_calendar"), (GROUPS, "row_8_groups"), (TRACES, "row_11_traces_list")):
            arguments, _ = fixture(key)
            result = await self.call(tool, arguments, error({
                "success": False, "error": {"code": "INTERNAL_ERROR", "message": CANARY}}))
            self.refused(result, dispatched=True, category="upstream_error")

    async def test_91_error_adapter_positive(self):
        release = load_reviewed_upstream_release_registry().by_version["9.1.0"]
        adapter = readmission._error_adapter(
            release.error_contract_fingerprint, upstream_version="9.1.0"
        )
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.adapter_id, ADAPTER_91)
        self.assertEqual(adapter.upstream_versions, frozenset({"9.1.0"}))
        self.assertEqual(adapter.evidence_class, "source_derived")
        # Never attached by analogy: 8.5.0 with the equal aggregate is not served by it.
        other = readmission._error_adapter(
            release.error_contract_fingerprint, upstream_version="8.5.0"
        )
        self.assertNotEqual(getattr(other, "adapter_id", None), ADAPTER_91)
        self.assertTrue(readmission._error_contract_matches_capability(
            capability_id=AUTOMATION, selected_adapter=adapter, observed_adapter=adapter,
            selected_entity_status="", observed_entity_status="",
        ))
        for key, entry in FIXTURES_91["errors"].items():
            with self.subTest(key=key):
                result = await self.call(entry["tool"], entry["arguments"], error(entry["error"]))
                self.refused(result, dispatched=True,
                             category=entry.get("failure_category", entry["public_outcome"]))
                self.assertEqual(result["error_code"], entry["public_outcome"])
                self.assertNotIn("available_script_ids", json.dumps(result))
                self.assertNotIn("missing_fixture", json.dumps(result))


class Parity85Tests(_Base):
    """The 8.5.0 policy passes the replayed records through byte-identically.

    Evidence class S (8.6 replay): the inputs are 8.6.0 records, so this proves
    the 8.5.0 gateway path, not 8.5.0 upstream output (8.5 lanes carry that).
    """

    version = "8.5.0"

    async def _pass_through(self, tool):
        entry = FIXTURES_85[tool]
        result = await self.call(tool, deepcopy(entry["arguments"]), deepcopy(entry["result"]))
        data = self.succeeded(result)
        self.assertEqual(data, entry["decoded"])
        self.assertNotIn("unavailable_fields", json.dumps(data))
        self.assertEqual(self.last_sent(tool), entry["arguments"])
        return entry

    async def _shape_refusals(self, tool, refusals):
        for arguments in refusals:
            await self.assert_refused_unsent(tool, arguments)

    async def test_85_parity_row_1_automation(self):
        await self._pass_through(AUTOMATION)
        await self._shape_refusals(AUTOMATION, [{}, {"entity_id": "automation.x"}])

    async def test_85_parity_row_2_calendar(self):
        await self._pass_through(CALENDAR)
        await self._shape_refusals(CALENDAR, [{}, {"entity_id": "calendar.x", "calendar_id": "x"}])

    async def test_85_parity_row_3_category(self):
        await self._pass_through(CATEGORY)
        await self._shape_refusals(CATEGORY, [{}, {"scope": "automation", "name": "x"}])

    async def test_85_parity_row_4_label(self):
        await self._pass_through(LABEL)
        await self._shape_refusals(LABEL, [{"name": "x"}])

    async def test_85_parity_row_5_scene(self):
        await self._pass_through(SCENE)
        await self._shape_refusals(SCENE, [{}, {"scene_id": "x", "query": "x"}, {"scene_id": "x", "limit": 1}])

    async def test_85_parity_row_6_script(self):
        entry = await self._pass_through(SCRIPT)
        self.assertEqual(entry["decoded"]["script_id"], "gateway_fixture")
        await self._shape_refusals(SCRIPT, [{}, {"script_id": "x", "raw": True}])

    async def test_85_parity_row_7_dashboard_resources(self):
        entry = await self._pass_through(RESOURCES)
        self.assertEqual(entry["decoded"]["by_type"], {"css": 0, "js": 0, "module": 0})
        await self._shape_refusals(RESOURCES, [{"include_hash": True}])

    async def test_85_parity_row_8_groups(self):
        await self._pass_through(GROUPS)
        await self._shape_refusals(GROUPS, [{"group_id": "x"}])

    async def test_85_parity_row_9_helpers(self):
        await self._pass_through(HELPERS)
        await self._shape_refusals(HELPERS, [
            {"helper_type": "input_boolean", "describe": True},
            {"helper_type": "input_boolean", "menu_choice": "x"},
            {"helper_type": "input_boolean", "helper_id": "x"},
        ])

    async def test_85_parity_row_10_eval_template(self):
        await self._pass_through(TEMPLATE)
        await self._shape_refusals(TEMPLATE, [
            {"template": "x", "condition": "x"}, {"template": "x", "variables": {}},
            {"template": "x", "strict": True},
        ])

    async def test_85_parity_row_11_automation_traces(self):
        await self._pass_through(TRACES)
        await self._shape_refusals(TRACES, [{"automation_id": "automation.x", "order": "random"}])

    async def test_85_error_adapter_positive(self):
        release = load_reviewed_upstream_release_registry().by_version["8.5.0"]
        adapter = readmission._error_adapter(
            release.error_contract_fingerprint, upstream_version="8.5.0"
        )
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.adapter_id, ADAPTER_85)
        self.assertIsNone(adapter.upstream_versions)
        self.assertIsNone(adapter.evidence_class)
        self.assertTrue(readmission._error_contract_matches_capability(
            capability_id=AUTOMATION, selected_adapter=adapter, observed_adapter=adapter,
            selected_entity_status="", observed_entity_status="",
        ))
        for key, entry in FIXTURES_91["errors"].items():
            with self.subTest(key=key):
                result = await self.call(entry["tool"], entry["arguments"], error(entry["error"]))
                self.refused(result, dispatched=True,
                             category=entry.get("failure_category", entry["public_outcome"]))


class ProjectionTableTests(unittest.TestCase):
    def test_91_parity_rows_1_11_projection_table_is_exact(self):
        self.assertEqual(set(config.PROJECTIONS), set(ROW_TOOLS))
        self.assertTrue(set(ROW_TOOLS) <= set(upstream_reads_9_1.INPUT_FINGERPRINTS))
        for name, (project, completeness) in config.PROJECTIONS.items():
            with self.subTest(name=name):
                for payload in (None, [], "x", {"success": False}, {}):
                    with self.assertRaises(ValueError):
                        project(payload, {})
                partial, warnings = completeness({})
                self.assertIsInstance(partial, bool)
                self.assertTrue(all(isinstance(item, str) for item in warnings))
        # Completeness never echoes upstream text.
        self.assertEqual(config.PROJECTIONS[GROUPS][1]({"groups": [{"unavailable_fields": [CANARY]}]}),
                         (True, [config.GROUP_FIELDS_UNAVAILABLE_WARNING]))
        self.assertEqual(config.PROJECTIONS[TEMPLATE][1]({"warnings": [CANARY]}),
                         (True, [config.TEMPLATE_FIELDS_UNAVAILABLE_WARNING]))


if __name__ == "__main__":
    unittest.main()
