from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock

from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
CAPTURE = (
    ROOT
    / "docs"
    / "evidence"
    / "upstream-read-compatibility"
    / "ha-mcp-8.1.1.json"
)
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
    _compare_held_tool_contract,
)
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.capabilities import (  # noqa: E402
    capability_for_tool,
    dynamic_upstream_capabilities,
    replace_dynamic_upstream_capabilities,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    current_telemetry,
    end_request,
)
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.tools import (  # noqa: E402
    get_registered_server,
    registered_tools,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    load_reviewed_upstream_release_registry,
)
from tests.test_readonly_upstream_gateway import (  # noqa: E402
    FakeTransport,
    settings,
)


ENTRY_ID = "ha-mcp-v8.1.1-e1d76a6e"


def captured_tools() -> list[dict]:
    return deepcopy(json.loads(CAPTURE.read_text(encoding="utf-8"))["tools"])


def decoded(value: str) -> dict:
    return json.loads(value)


class HeldReadCanaryTests(unittest.IsolatedAsyncioTestCase):
    def admission_surface_snapshot(
        self,
        gateway: UpstreamReadGateway,
        server: FastMCP,
    ) -> dict:
        health = gateway.health_snapshot()
        held_classifications = tuple(
            sorted(
                (entry.upstream_name, entry.classification)
                for entry in gateway._policy.tools
                if entry.classification == "held_for_canary"
            )
        )
        registered_dynamic_tools = tuple(sorted(registered_tools(server)))
        dynamic_capabilities = dynamic_upstream_capabilities()
        snapshot = {
            "held_classifications": held_classifications,
            "held_canary_routes": tuple(sorted(gateway._held_canaries)),
            "registered_dynamic_tools": registered_dynamic_tools,
            "dynamic_capabilities": dynamic_capabilities,
            "admission_generation": gateway._admission_generation,
            "admission_state": {
                field: deepcopy(health[field])
                for field in (
                    "selected_compatibility_entry_id",
                    "admission_status",
                    "compatibility_status",
                    "admission_complete",
                    "generic_delegation_available",
                    "dynamically_exposed_count",
                    "exact_matched_automatic_read_count",
                    "accounted_automatic_read_count",
                    "automatic_read_accounting_valid",
                    "held_read_count",
                    "held_tools",
                    "live_canary_required_tools",
                    "policy_classifications",
                    "blocked_classification_counts",
                    "catalog_fingerprint",
                    "strict_full_contract_fingerprint",
                    "fallback_count",
                )
            },
        }
        self.assertEqual(
            held_classifications,
            (("ha_get_operation_status", "held_for_canary"),),
        )
        self.assertEqual(len(registered_dynamic_tools), 25)
        self.assertIn("ha_search", registered_dynamic_tools)
        self.assertNotIn("ha_get_operation_status", registered_dynamic_tools)
        self.assertEqual(len(dynamic_capabilities), 25)
        self.assertTrue(
            all(
                item["operation_class"] == "automatic_read"
                for item in dynamic_capabilities
            )
        )
        return snapshot

    def test_public_tool_is_read_only_engineering_native(self):
        tool = registered_tools(get_registered_server()).get(
            "run_held_read_canary"
        )
        self.assertIsNotNone(tool)
        self.assertTrue(tool.annotations.readOnlyHint)
        self.assertFalse(tool.annotations.destructiveHint)
        self.assertFalse(tool.annotations.idempotentHint)
        self.assertEqual(
            tool.parameters["properties"]["upstream_tool_name"]["maxLength"],
            128,
        )
        self.assertEqual(
            tool.parameters["properties"][
                "expected_compatibility_entry_id"
            ]["maxLength"],
            160,
        )
        capability = capability_for_tool("run_held_read_canary")
        self.assertEqual(capability["operation_class"], "held_read_canary")
        self.assertEqual(capability["provider"], "upstream_read_gateway")
        self.assertEqual(capability["fallback"], "none")
        self.assertFalse(capability["promotion_performed"])

    async def gateway(
        self,
        *,
        tools: list[dict] | None = None,
        result: dict | None = None,
    ) -> tuple[UpstreamReadGateway, FakeTransport, FastMCP]:
        self.addCleanup(replace_dynamic_upstream_capabilities, (), {})
        transport = FakeTransport(
            tools or captured_tools(),
            version="8.1.1",
            result=result,
        )
        gateway = UpstreamReadGateway()
        gateway.configure(
            settings(),
            transport=transport,
            release_registry=load_reviewed_upstream_release_registry(),
            admission_validator=lambda _catalog: None,
        )
        server = FastMCP("held-read-canary-test")
        await gateway.initialize(server)
        self.assertEqual(
            gateway.health_snapshot()["admission_status"],
            "admitted_exact",
        )
        return gateway, transport, server

    async def test_promoted_search_is_rejected_by_held_canary(self):
        gateway, transport, server = await self.gateway()
        gateway._ha_rest_client = AsyncMock()
        gateway._ha_websocket_client = AsyncMock()
        before = self.admission_surface_snapshot(gateway, server)

        telemetry, token = begin_request("held-canary-promoted-search")
        try:
            result = decoded(
                await gateway.run_held_read_canary(
                    upstream_tool_name="ha_search",
                    expected_compatibility_entry_id=ENTRY_ID,
                    arguments={"query": "porch", "limit": 1},
                )
            )
        finally:
            end_request(token)

        self.assertFalse(result["success"])
        self.assertEqual(
            result["details"]["reason"], "tool_not_held_for_canary"
        )
        self.assertFalse(
            result["details"]["canary_evidence"]["dispatch_occurred"]
        )
        self.assertEqual(transport.calls, [])
        self.assertEqual(transport.attempts, [])
        self.assertIn("ha_search", registered_tools(server))
        self.assertNotIn("ha_get_operation_status", registered_tools(server))
        self.assertEqual(
            self.admission_surface_snapshot(gateway, server),
            before,
        )
        self.assertEqual(gateway._ha_rest_client.mock_calls, [])
        self.assertEqual(gateway._ha_websocket_client.mock_calls, [])
        self.assertEqual(telemetry.provider_dispatch_count, 0)

    async def test_upstream_error_is_truthful_and_preserves_admission(self):
        gateway, transport, server = await self.gateway(
            result={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "success": False,
                                "error": {
                                    "code": "RESOURCE_NOT_FOUND",
                                    "message": "synthetic secret-free error",
                                },
                            }
                        ),
                    }
                ],
                "isError": True,
            }
        )
        before = self.admission_surface_snapshot(gateway, server)

        telemetry, token = begin_request("held-canary-not-found")
        try:
            result = decoded(
                await gateway.run_held_read_canary(
                    upstream_tool_name="ha_get_operation_status",
                    expected_compatibility_entry_id=ENTRY_ID,
                    arguments={
                        "operation_id": "missing-synthetic-operation"
                    },
                )
            )
        finally:
            end_request(token)

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "provider_error")
        evidence = result["details"]["canary_evidence"]
        self.assertEqual(evidence["failure_category"], "upstream_error")
        self.assertEqual(
            evidence["error_contract"]["structured_code"],
            "RESOURCE_NOT_FOUND",
        )
        self.assertRegex(
            evidence["error_contract"]["shape_fingerprint"],
            r"^[0-9a-f]{64}$",
        )
        self.assertTrue(evidence["dispatch_occurred"])
        self.assertFalse(evidence["promotion_performed"])
        self.assertEqual(transport.calls[0][0], "ha_get_operation_status")
        self.assertEqual(telemetry.provider_dispatch_count, 1)
        self.assertEqual(telemetry.provider_success_count, 0)
        self.assertEqual(telemetry.provider_failure_count, 1)
        self.assertEqual(
            self.admission_surface_snapshot(gateway, server),
            before,
        )

    async def test_binding_schema_and_nonheld_rejections_precede_dispatch(self):
        gateway, transport, _server = await self.gateway()
        cases = (
            (
                "ha_get_operation_status",
                "wrong-entry",
                {"operation_id": "synthetic-operation"},
                "compatibility_entry_mismatch",
            ),
            (
                "ha_get_operation_status",
                ENTRY_ID,
                {},
                None,
            ),
            (
                "ha_search",
                ENTRY_ID,
                {"query": "porch"},
                "tool_not_held_for_canary",
            ),
            (
                "ha_config_get_automation",
                ENTRY_ID,
                {},
                "tool_not_held_for_canary",
            ),
            (
                "ha_call_service",
                ENTRY_ID,
                {},
                "tool_not_held_for_canary",
            ),
            (
                "ha_bulk_control",
                ENTRY_ID,
                {},
                "tool_not_held_for_canary",
            ),
            (
                "ha_config_delete_dashboard",
                ENTRY_ID,
                {},
                "tool_not_held_for_canary",
            ),
            (
                "ha_get_camera_image",
                ENTRY_ID,
                {},
                "tool_not_held_for_canary",
            ),
            (
                "ha_report_issue",
                ENTRY_ID,
                {},
                "tool_not_held_for_canary",
            ),
            (
                "ha_unreviewed_synthetic",
                ENTRY_ID,
                {},
                "tool_not_held_for_canary",
            ),
        )
        for name, entry_id, arguments, reason in cases:
            with self.subTest(name=name, reason=reason):
                result = decoded(
                    await gateway.run_held_read_canary(
                        upstream_tool_name=name,
                        expected_compatibility_entry_id=entry_id,
                        arguments=arguments,
                    )
                )
                self.assertFalse(result["success"])
                self.assertFalse(
                    result["details"]["canary_evidence"][
                        "dispatch_occurred"
                    ]
                )
                if reason is not None:
                    self.assertEqual(result["details"]["reason"], reason)
        self.assertEqual(transport.attempts, [])
        self.assertEqual(transport.calls, [])

    async def test_known_held_contract_mismatches_fail_before_dispatch(self):
        mutations = {
            "schema": lambda tool: tool["inputSchema"].update(
                {"additionalProperties": True}
            ),
            "annotation": lambda tool: tool["annotations"].update(
                {"destructiveHint": True}
            ),
            "output": lambda tool: tool.update(
                {"outputSchema": {"type": "string"}}
            ),
            "runtime": lambda tool: tool.update(
                {"title": "Unreviewed runtime title"}
            ),
        }
        for component, mutation in mutations.items():
            with self.subTest(component=component):
                tools = captured_tools()
                target = next(
                    item
                    for item in tools
                    if item["name"] == "ha_get_operation_status"
                )
                mutation(target)
                gateway, transport, server = await self.gateway(tools=tools)
                result = decoded(
                    await gateway.run_held_read_canary(
                        upstream_tool_name="ha_get_operation_status",
                        expected_compatibility_entry_id=ENTRY_ID,
                        arguments={"operation_id": "synthetic-operation"},
                    )
                )
                self.assertFalse(result["success"])
                self.assertEqual(
                    result["details"]["failure_category"],
                    "schema_mismatch",
                )
                self.assertFalse(
                    result["details"]["canary_evidence"][
                        "dispatch_occurred"
                    ]
                )
                self.assertEqual(transport.attempts, [])
                self.assertNotIn(
                    "ha_get_operation_status", registered_tools(server)
                )

    def test_held_security_and_nonheld_quarantine_contracts_are_rejected(self):
        release = load_reviewed_upstream_release_registry().by_version["8.1.1"]
        entry = release.policy.by_name["ha_get_operation_status"]
        contract = release.tool_contracts_by_name["ha_get_operation_status"]
        observed = next(
            item
            for item in captured_tools()
            if item["name"] == "ha_get_operation_status"
        )
        insecure_annotations = replace(
            entry.reviewed_annotations,
            read_only=False,
        )
        insecure_entry = replace(
            entry,
            reviewed_annotations=insecure_annotations,
        )
        quarantined_contract = replace(
            contract,
            quarantine_reason="policy:prohibited",
        )

        insecure = _compare_held_tool_contract(
            insecure_entry,
            observed,
            protocol_version="2025-03-26",
            reviewed_contract=contract,
            runtime_contract_fingerprint_model=(
                release.runtime_contract_fingerprint_model
            ),
        )
        quarantined = _compare_held_tool_contract(
            entry,
            observed,
            protocol_version="2025-03-26",
            reviewed_contract=quarantined_contract,
            runtime_contract_fingerprint_model=(
                release.runtime_contract_fingerprint_model
            ),
        )

        self.assertFalse(insecure.accepted)
        self.assertEqual(insecure.reason, "security_classification_mismatch")
        self.assertFalse(quarantined.accepted)
        self.assertEqual(
            quarantined.reason,
            "security_classification_mismatch",
        )

    async def test_failed_canary_preserves_admission_and_is_not_success(self):
        gateway, transport, server = await self.gateway(
            result={
                "content": [{"type": "text", "text": '"not-an-object"'}],
                "isError": False,
            }
        )
        before = self.admission_surface_snapshot(gateway, server)
        result = decoded(
            await gateway.run_held_read_canary(
                upstream_tool_name="ha_get_operation_status",
                expected_compatibility_entry_id=ENTRY_ID,
                arguments={"operation_id": "synthetic-operation"},
            )
        )
        self.assertFalse(result["success"])
        self.assertEqual(
            result["details"]["reason"],
            "output_contract_validation_failed",
        )
        self.assertTrue(
            result["details"]["canary_evidence"]["dispatch_occurred"]
        )
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(
            self.admission_surface_snapshot(gateway, server),
            before,
        )

    async def test_untrusted_success_is_bounded_and_reports_partial(self):
        gateway, transport, _server = await self.gateway(
            result={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "success": True,
                                "query": "porch",
                                "untrusted": "x" * 100_000,
                            }
                        ),
                    }
                ],
                "isError": False,
            }
        )

        serialized = await gateway.run_held_read_canary(
            upstream_tool_name="ha_get_operation_status",
            expected_compatibility_entry_id=ENTRY_ID,
            arguments={"operation_id": "synthetic-operation"},
        )
        result = decoded(serialized)

        self.assertLessEqual(len(serialized.encode("utf-8")), 60_000)
        self.assertTrue(result["success"])
        evidence = result["data"]["canary_evidence"]
        self.assertEqual(evidence["outcome"], "partial")
        self.assertEqual(evidence["completeness"], "partial")
        self.assertTrue(evidence["truncated"])
        self.assertFalse(evidence["promotion_performed"])
        self.assertEqual(len(transport.calls), 1)

    async def test_audit_excludes_arguments_and_records_bounded_evidence(self):
        secret_argument = "synthetic-canary-payload-secret"

        async def app(_scope, _receive, send):
            telemetry = current_telemetry()
            self.assertIsNotNone(telemetry)
            telemetry.audit_context.update(
                {
                    "upstream_tool": "ha_get_operation_status",
                    "expected_compatibility_entry_id": ENTRY_ID,
                    "active_compatibility_entry_id": ENTRY_ID,
                    "observed_upstream_server": "ha-mcp",
                    "observed_upstream_version": "8.1.1",
                    "observed_upstream_protocol": "2025-03-26",
                    "reviewed_classification_before": "held_for_canary",
                    "reviewed_classification_after": "held_for_canary",
                    "dispatch_occurred": True,
                    "provider": "upstream_read_gateway",
                    "fallback_occurred": False,
                    "outcome": "success",
                    "failure_category": None,
                    "completeness": "complete",
                    "truncated": False,
                    "promotion_performed": False,
                }
            )
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "held-canary-audit",
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps({"success": True}),
                            }
                        ],
                        "isError": False,
                    },
                }
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": body,
                    "more_body": False,
                }
            )

        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            configured = replace(settings(), audit_path=str(audit_path))
            routed = AuthenticatedMcpGateway(
                app,
                configured,
                AuditLogger(str(audit_path), "synthetic-engineering-access-secret"),
            )
            request = {
                "jsonrpc": "2.0",
                "id": "held-canary-audit",
                "method": "tools/call",
                "params": {
                    "name": "run_held_read_canary",
                    "arguments": {
                        "upstream_tool_name": "ha_get_operation_status",
                        "expected_compatibility_entry_id": ENTRY_ID,
                        "arguments": {
                            "operation_id": secret_argument,
                            "nested": {"token": secret_argument},
                        },
                    },
                },
            }
            delivered = False

            async def receive():
                nonlocal delivered
                if delivered:
                    return {"type": "http.disconnect"}
                delivered = True
                return {
                    "type": "http.request",
                    "body": json.dumps(request).encode(),
                    "more_body": False,
                }

            async def send(_message):
                return None

            await routed(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/synthetic-engineering-access-secret/mcp",
                    "raw_path": b"/synthetic-engineering-access-secret/mcp",
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"x-request-id", b"held-canary-audit"),
                    ],
                    "client": ("127.0.0.1", 1),
                },
                receive,
                send,
            )
            serialized = audit_path.read_text(encoding="utf-8")
            record = json.loads(serialized.splitlines()[-1])

        self.assertNotIn(secret_argument, serialized)
        self.assertEqual(
            record["parameters"]["argument_fields"],
            ["nested", "operation_id"],
        )
        self.assertEqual(record["access"], "read")
        self.assertEqual(record["result_status"], "success")
        self.assertEqual(
            record["analysis_summary"]["upstream_tool"],
            "ha_get_operation_status",
        )
        self.assertFalse(
            record["analysis_summary"]["promotion_performed"]
        )


if __name__ == "__main__":
    unittest.main()
