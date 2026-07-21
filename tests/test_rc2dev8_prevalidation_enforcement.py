import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.capabilities import (  # noqa: E402
    build_capability_catalog,
    capability_for_tool,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.observability import METRICS  # noqa: E402
from ha_mcp_engineering.governance.runtime import GOVERNANCE  # noqa: E402
from ha_mcp_engineering.providers.routing import (  # noqa: E402
    PREVALIDATION_ENFORCEMENT_TOOLS,
)
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    UPSTREAM_DASHBOARD,
)
from ha_mcp_engineering.request_context import current_telemetry  # noqa: E402
from ha_mcp_engineering.routing import (  # noqa: E402
    AuthenticatedMcpGateway,
    _jsonrpc_response_from_body,
    _mcp_error_code,
    _structured_tool_failure_code,
)
from ha_mcp_engineering.tools.registry import get_registered_server  # noqa: E402
from ha_mcp_engineering.tools import compatibility  # noqa: E402
from ha_mcp_engineering.version import SERVER_VERSION  # noqa: E402

PROMOTION_SPEC = importlib.util.spec_from_file_location(
    "rc2dev12_promote_next_release",
    ROOT / "scripts" / "promote_next_release.py",
)
PROMOTION_MODULE = importlib.util.module_from_spec(PROMOTION_SPEC)
assert PROMOTION_SPEC.loader is not None
PROMOTION_SPEC.loader.exec_module(PROMOTION_MODULE)


SECRET = "synthetic-rc2dev8-gateway-secret"


def _settings(audit_path: Path) -> Settings:
    return Settings(
        ha_url="http://synthetic-ha.invalid",
        ha_token="synthetic-token",
        access_secret=SECRET,
        port=8100,
        audit_path=str(audit_path),
        rate_limit_per_minute=10_000,
        rate_limit_burst=1_000,
        destructive_services=frozenset(),
        governance_path=str(audit_path.parent / "governance"),
        prewarm_enabled=False,
    )


def _parse_sse(response) -> dict:
    line = next(
        row
        for row in response.text.replace("\r", "").splitlines()
        if row.startswith("data: ")
    )
    return json.loads(line.removeprefix("data: "))


class Rc2dev8RawPrevalidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.audit_path = Path(cls.directory.name) / "audit.jsonl"
        fresh_server = FastMCP(
            "rc2dev8-raw-routing-test",
            host="127.0.0.1",
            port=8100,
            streamable_http_path="/mcp",
            stateless_http=True,
        )
        for tool in get_registered_server()._tool_manager.list_tools():
            fresh_server.tool(
                name=tool.name,
                description=tool.description,
                annotations=tool.annotations,
            )(tool.fn)

        @fresh_server.tool(name="rc2dev8_internal_error_fixture")
        async def internal_error_fixture() -> str:
            raise RuntimeError("synthetic bounded handler failure")

        gateway = AuthenticatedMcpGateway(
            fresh_server.streamable_http_app(),
            _settings(cls.audit_path),
            AuditLogger(str(cls.audit_path), SECRET),
        )
        cls.client_context = TestClient(gateway)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        cls.directory.cleanup()

    def rpc(self, tool_name: str, arguments, request_id: str):
        response = self.client.post(
            f"/{SECRET}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
                "x-request-id": request_id,
            },
        )
        return response, _parse_sse(response)

    def audit_record(self, request_id: str) -> dict:
        records = [
            json.loads(row)
            for row in self.audit_path.read_text(encoding="utf-8").splitlines()
        ]
        return next(
            record
            for record in reversed(records)
            if record.get("request_id") == request_id
        )

    def assert_enforcement(self, tool_name, arguments, expected_code, request_id):
        response, mcp = self.rpc(tool_name, arguments, request_id)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("validation error", response.text.lower())
        self.assertFalse(mcp["result"]["isError"])
        rendered = json.loads(mcp["result"]["content"][0]["text"])
        self.assertFalse(rendered["success"])
        self.assertEqual(rendered["operation"], tool_name)
        self.assertEqual(rendered["error_code"], expected_code)
        self.assertEqual(rendered["request_id"], request_id)
        self.assertFalse(rendered["metadata"]["routing"]["fallback_occurred"])
        self.assertFalse(
            rendered["metadata"]["source_coverage"][0]["upstream_attempted"]
        )
        self.assertEqual(rendered["timing"]["home_assistant_request_count"], 0)
        self.assertEqual(rendered["timing"]["upstream_request_count"], 0)
        audit = self.audit_record(request_id)
        self.assertEqual(audit["result_status"], "failure")
        self.assertEqual(audit["error_code"], expected_code)
        self.assertEqual(audit["ha_endpoint_categories"], [])
        self.assertEqual(audit["request_id"], rendered["request_id"])
        return rendered, audit

    def test_raw_json_strings_receive_canonical_enforcement_before_fastmcp(self):
        call_service, _ = self.assert_enforcement(
            "call_service",
            {
                "domain": "rc2dev8_nonexistent_domain",
                "service": "rc2dev8_nonexistent_service",
                "data_json": "{}",
                "confirm": False,
            },
            "provider_unavailable",
            "rc2dev8-call-service-json-string",
        )
        self.assertEqual(
            call_service["metadata"]["routing"]["provider"],
            "standard_ha_mcp",
        )
        upsert, _ = self.assert_enforcement(
            "upsert_automation",
            {
                "automation_id": "rc2dev8_raw_enforcement_probe",
                "config_json": json.dumps(
                    {
                        "alias": "RC2dev8 raw enforcement probe",
                        "description": "must not persist",
                        "mode": "single",
                        "triggers": "invalid",
                        "conditions": [],
                        "actions": "invalid",
                    }
                ),
            },
            "provider_prohibited",
            "rc2dev8-upsert-json-string",
        )
        self.assertEqual(upsert["details"]["replacement"], "create_change_plan")

    def test_reload_and_delete_return_canonical_enforcement(self):
        self.assert_enforcement(
            "reload_domain",
            {"domain": "automation"},
            "provider_unavailable",
            "rc2dev8-reload-domain",
        )
        self.assert_enforcement(
            "delete_automation",
            {"automation_id": "rc2dev8_nonexistent_delete_probe", "confirm": False},
            "provider_prohibited",
            "rc2dev8-delete-automation",
        )

    def test_argument_shape_matrix_is_policy_first_and_does_not_echo_values(self):
        expected = {
            "call_service": "provider_unavailable",
            "reload_domain": "provider_unavailable",
            "upsert_automation": "provider_prohibited",
            "delete_automation": "provider_prohibited",
        }
        marker = "caller-controlled-marker-must-not-echo"
        variants = (
            {"domain": "safe", "service": "safe", "data_json": "not-json"},
            {"missing": marker},
            {},
            {"value": {"nested": marker}},
            {"value": [marker]},
            {"value": None},
            {"unexpected": marker},
            {"nested": {"one": {"two": {"three": marker}}}},
        )
        for tool_name, error_code in expected.items():
            for index, arguments in enumerate(variants):
                request_id = f"rc2dev8-matrix-{tool_name}-{index}"
                rendered, audit = self.assert_enforcement(
                    tool_name, arguments, error_code, request_id
                )
                self.assertNotIn(marker, json.dumps(rendered))
                self.assertNotIn(marker, json.dumps(audit))

    def test_non_object_arguments_are_still_policy_first(self):
        for index, arguments in enumerate((None, [], ["unsafe"], "unsafe")):
            for tool_name in PREVALIDATION_ENFORCEMENT_TOOLS:
                error_code = (
                    "provider_unavailable"
                    if tool_name in {"call_service", "reload_domain"}
                    else "provider_prohibited"
                )
                self.assert_enforcement(
                    tool_name,
                    arguments,
                    error_code,
                    f"rc2dev8-non-object-{tool_name}-{index}",
                )

    def test_provider_and_fallback_counters_do_not_move(self):
        before_requests = METRICS.provider_requests.copy()
        before_failures = METRICS.provider_failures.copy()
        before_fallback = METRICS.fallback_attempts
        before_prohibited_fallback = METRICS.prohibited_fallback_attempts
        for tool_name in PREVALIDATION_ENFORCEMENT_TOOLS:
            error_code = (
                "provider_unavailable"
                if tool_name in {"call_service", "reload_domain"}
                else "provider_prohibited"
            )
            self.assert_enforcement(
                tool_name,
                {"untrusted": {"payload": "not-forwarded"}},
                error_code,
                f"rc2dev8-counter-{tool_name}",
            )
        self.assertEqual(METRICS.provider_requests, before_requests)
        self.assertEqual(METRICS.provider_failures, before_failures)
        self.assertEqual(METRICS.fallback_attempts, before_fallback)
        self.assertEqual(
            METRICS.prohibited_fallback_attempts,
            before_prohibited_fallback,
        )

    def test_normal_tool_validation_remains_active_and_audit_is_truthful(self):
        marker = "normal-validation-payload-must-not-enter-audit"
        response, mcp = self.rpc(
            "get_entity",
            {"entity_id": {"nested": marker}},
            "rc2dev8-normal-validation",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(mcp["result"]["isError"])
        self.assertIn("validation error", response.text.lower())
        audit = self.audit_record("rc2dev8-normal-validation")
        self.assertEqual(audit["result_status"], "failure")
        self.assertEqual(audit["error_code"], "invalid_request")
        self.assertEqual(
            audit["parameters"],
            {"validation": "rejected", "argument_fields": ["entity_id"]},
        )
        self.assertNotIn(marker, json.dumps(audit))
        self.assertEqual(audit["ha_endpoint_categories"], [])

    def test_other_normal_tool_validation_remains_active(self):
        for tool_name, arguments in (
            (
                "entity_dependency_analysis",
                {
                    "entity_id": "input_boolean.synthetic",
                    "detail_level": "summary",
                    "limit": {"not": "an integer"},
                },
            ),
            ("list_dashboards", {"limit": {"not": "an integer"}}),
        ):
            provider_requests = METRICS.provider_requests.copy()
            request_id = f"rc2dev8-normal-validation-{tool_name}"
            response, mcp = self.rpc(tool_name, arguments, request_id)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(mcp["result"]["isError"])
            audit = self.audit_record(request_id)
            self.assertEqual(audit["result_status"], "failure")
            self.assertEqual(audit["error_code"], "invalid_request")
            self.assertEqual(audit["ha_endpoint_categories"], [])
            self.assertEqual(METRICS.provider_requests, provider_requests)

    def test_successful_ha_backed_read_is_audited_as_success(self):
        async def fake_request(method, path, body=None, raw=False):
            self.assertEqual((method, path), ("GET", "/states/sensor.synthetic"))
            telemetry = current_telemetry()
            started = time.perf_counter()
            telemetry.begin_ha_attempt(started)
            telemetry.endpoint_categories.add("states")
            telemetry.finish_ha_attempt(time.perf_counter())
            return {
                "entity_id": "sensor.synthetic",
                "state": "ready",
                "attributes": {"friendly_name": "Synthetic"},
            }

        request_id = "rc2dev8-ha-read-success"
        with patch.object(
            compatibility.REST_CLIENT,
            "request",
            new=AsyncMock(side_effect=fake_request),
        ) as request_spy:
            response, mcp = self.rpc(
                "get_entity",
                {"entity_id": "sensor.synthetic"},
                request_id,
            )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(mcp["result"]["isError"])
        rendered = json.loads(mcp["result"]["content"][0]["text"])
        self.assertEqual(rendered["timing"]["home_assistant_request_count"], 1)
        request_spy.assert_awaited_once()
        audit = self.audit_record(request_id)
        self.assertEqual(audit["result_status"], "success")
        self.assertIsNone(audit["error_code"])
        self.assertEqual(audit["ha_endpoint_categories"], ["states"])

    def test_handler_internal_error_is_audited_as_failure(self):
        request_id = "rc2dev8-handler-internal-error"
        response, mcp = self.rpc(
            "rc2dev8_internal_error_fixture",
            {},
            request_id,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(mcp["result"]["isError"])
        audit = self.audit_record(request_id)
        self.assertEqual(audit["result_status"], "failure")
        self.assertEqual(audit["error_code"], "internal_server_error")
        self.assertEqual(audit["ha_endpoint_categories"], [])
        self.assertNotIn("synthetic bounded handler failure", json.dumps(audit))

    def test_legacy_interception_never_reaches_side_effect_boundaries(self):
        with (
            patch.object(
                compatibility.REST_CLIENT,
                "request",
                new_callable=AsyncMock,
            ) as rest_spy,
            patch.object(
                compatibility.WEBSOCKET_CLIENT,
                "command",
                new_callable=AsyncMock,
            ) as websocket_spy,
            patch.object(
                UPSTREAM_DASHBOARD,
                "list_dashboards",
                new_callable=AsyncMock,
            ) as upstream_spy,
            patch.object(GOVERNANCE, "require") as governance_spy,
        ):
            for tool_name in PREVALIDATION_ENFORCEMENT_TOOLS:
                expected = (
                    "provider_unavailable"
                    if tool_name in {"call_service", "reload_domain"}
                    else "provider_prohibited"
                )
                self.assert_enforcement(
                    tool_name,
                    {"caller": {"arguments": "must not dispatch"}},
                    expected,
                    f"rc2dev8-side-effect-spy-{tool_name}",
                )
        rest_spy.assert_not_awaited()
        websocket_spy.assert_not_awaited()
        upstream_spy.assert_not_awaited()
        governance_spy.assert_not_called()

    def test_unknown_tool_is_audited_as_invalid_request(self):
        response, mcp = self.rpc(
            "rc2dev8_unknown_tool",
            {"payload": "not-retained"},
            "rc2dev8-unknown-tool",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(mcp["result"]["isError"])
        audit = self.audit_record("rc2dev8-unknown-tool")
        self.assertEqual(audit["result_status"], "failure")
        self.assertEqual(audit["error_code"], "invalid_request")
        self.assertEqual(audit["ha_endpoint_categories"], [])

    def test_successful_local_tool_remains_successful_in_audit(self):
        _, mcp = self.rpc(
            "server_info",
            {"check_ha": False},
            "rc2dev8-local-success",
        )
        self.assertFalse(mcp["result"]["isError"])
        rendered = json.loads(mcp["result"]["content"][0]["text"])
        self.assertTrue(rendered["success"])
        audit = self.audit_record("rc2dev8-local-success")
        self.assertEqual(audit["result_status"], "success")
        self.assertIsNone(audit["error_code"])

    def test_malformed_jsonrpc_is_audited_without_payload(self):
        request_id = "rc2dev8-malformed-jsonrpc"
        response = self.client.post(
            f"/{SECRET}/mcp",
            content=b'{"jsonrpc":"2.0","id":"broken"',
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
                "x-request-id": request_id,
            },
        )
        self.assertIn(response.status_code, {200, 400})
        audit = self.audit_record(request_id)
        self.assertEqual(audit["event"], "mcp_request")
        self.assertEqual(audit["result_status"], "failure")
        self.assertEqual(audit["error_code"], "invalid_request")
        self.assertNotIn("broken", json.dumps(audit))

    def test_transport_size_limit_wins_before_policy(self):
        request_id = "rc2dev8-oversized-policy-request"
        response = self.client.post(
            f"/{SECRET}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {
                    "name": "call_service",
                    "arguments": {"oversized": "x" * 2_000_100},
                },
            },
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
                "x-request-id": request_id,
            },
        )
        self.assertEqual(response.status_code, 413)
        rendered = response.json()
        self.assertEqual(rendered["error_code"], "invalid_request")
        audit = self.audit_record(request_id)
        self.assertEqual(audit["result_status"], "failure")
        self.assertEqual(audit["error_code"], "invalid_request")
        self.assertNotIn("x" * 128, json.dumps(audit))


class McpOutcomeClassificationTests(unittest.TestCase):
    def test_release_version_catalog_schemas_and_documents_are_consistent(self):
        authoritative_versions = PROMOTION_MODULE.authoritative_versions(ROOT)
        self.assertEqual(
            set(authoritative_versions.values()),
            {SERVER_VERSION},
        )
        tools = {
            tool.name: tool
            for tool in get_registered_server()._tool_manager.list_tools()
        }
        self.assertEqual(len(tools), 40)
        catalog = build_capability_catalog()
        self.assertEqual(catalog["registered_count"], 40)
        self.assertEqual(catalog["count"], 25)
        self.assertEqual(catalog["planned"], [])
        self.assertEqual(
            PREVALIDATION_ENFORCEMENT_TOOLS,
            {
                "call_service",
                "delete_automation",
                "reload_domain",
                "upsert_automation",
            },
        )
        expected = {
            "call_service": {
                "domain": "string",
                "service": "string",
                "data_json": "string",
                "confirm": "boolean",
            },
            "upsert_automation": {
                "automation_id": "string",
                "config_json": "string",
            },
            "reload_domain": {"domain": "string"},
            "delete_automation": {
                "automation_id": "string",
                "confirm": "boolean",
            },
        }
        for tool_name, field_types in expected.items():
            properties = tools[tool_name].parameters["properties"]
            self.assertEqual(
                {name: properties[name]["type"] for name in field_types},
                field_types,
            )
        expected_capabilities = {
            "call_service": {
                "enforcement": "provider_unavailable",
                "routing": "standard_mcp_preferred",
                "provider": "standard_ha_mcp",
                "fallback": "none",
                "direct_write_allowed": False,
            },
            "reload_domain": {
                "enforcement": "provider_unavailable",
                "routing": "standard_mcp_preferred",
                "provider": "standard_ha_mcp",
                "fallback": "none",
                "direct_write_allowed": False,
            },
            "upsert_automation": {
                "enforcement": "governed_redirect",
                "routing": "prohibited",
                "provider": None,
                "replacement": "create_change_plan",
                "fallback": "none",
                "direct_write_allowed": False,
            },
            "delete_automation": {
                "enforcement": "prohibited",
                "routing": "prohibited",
                "provider": None,
                "fallback": "none",
                "direct_write_allowed": False,
            },
        }
        for tool_name, fields in expected_capabilities.items():
            capability = capability_for_tool(tool_name)
            self.assertEqual(
                {field: capability.get(field) for field in fields},
                fields,
            )
        self.assertTrue((ROOT / "docs" / "RC2DEV8_RELEASE_NOTES.md").is_file())
        self.assertTrue((ROOT / "docs" / "RC2DEV8_ACCEPTANCE.md").is_file())

    def test_sse_structured_failure_uses_exact_engineering_code(self):
        rendered = json.dumps(
            {"success": False, "error_code": "provider_unavailable"}
        )
        body = (
            "event: message\r\n"
            + "data: "
            + json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "content": [{"type": "text", "text": rendered}],
                        "isError": False,
                    },
                }
            )
            + "\r\n\r\n"
        ).encode()
        payload = _jsonrpc_response_from_body(body)
        self.assertIsNotNone(payload)
        self.assertEqual(
            _structured_tool_failure_code(payload),
            "provider_unavailable",
        )
        self.assertIsNone(_mcp_error_code(payload))

    def test_validation_unknown_internal_and_jsonrpc_failures_are_distinct(self):
        validation = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": "Error executing tool safe: 1 validation error for safeArguments",
                    }
                ],
                "isError": True,
            },
        }
        unknown = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [{"type": "text", "text": "Error: Unknown tool"}],
                "isError": True,
            },
        }
        internal = {
            "jsonrpc": "2.0",
            "id": 3,
            "result": {
                "content": [{"type": "text", "text": "Tool execution failed"}],
                "isError": True,
            },
        }
        invalid_params = {
            "jsonrpc": "2.0",
            "id": 4,
            "error": {"code": -32602, "message": "Invalid params"},
        }
        self.assertEqual(_mcp_error_code(validation), "invalid_request")
        self.assertEqual(_mcp_error_code(unknown), "invalid_request")
        self.assertEqual(_mcp_error_code(internal), "internal_server_error")
        self.assertEqual(_mcp_error_code(invalid_params), "invalid_request")


if __name__ == "__main__":
    unittest.main()
