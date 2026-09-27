"""Positive privacy controls and rejected-input audit/SDK non-disclosure."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from test_integration_inspection_contract import TARGET, fixture, setup_service
from ha_mcp_engineering.audit import AuditLogger
from ha_mcp_engineering.configuration import Settings
from ha_mcp_engineering.integration_inspection.models import InspectionError
from ha_mcp_engineering.integration_inspection.runtime import INTEGRATION_INSPECTION
from ha_mcp_engineering.request_context import end_request
from ha_mcp_engineering.routing import AuthenticatedMcpGateway
from ha_mcp_engineering.tools import get_registered_server, registered_tools
from ha_mcp_engineering.tools import integration_inspection as public


def settings(**values):
    return Settings(**{**dict(ha_url="http://synthetic.invalid", ha_token="SYNTHETIC_HA_TOKEN",
        access_secret="SYNTHETIC_INSPECTION_ACCESS_SECRET", port=8100, audit_path="unused",
        rate_limit_per_minute=1000, rate_limit_burst=1000, destructive_services=frozenset()), **values})


class SecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_allowlist_before_snapshot_hash_and_export_even_redaction_disabled(self):
        base, _, _, _, base_token = setup_service()
        first = await base.inspect(alarm_entity_id=TARGET)
        end_request(base_token)
        service, client, _, telemetry, token = setup_service(fixture("alarmo_secret_canaries.json"))
        self.addCleanup(end_request, token)
        with patch.object(INTEGRATION_INSPECTION, "service", service), patch.object(public, "SETTINGS", settings(redaction_enabled=False)):
            response = json.loads(await public.get_integration_inspection(alarm_entity_id=TARGET))
        self.assertTrue(response["success"])
        output = json.dumps(response) + repr(tuple(service.snapshots.values())) + json.dumps(telemetry.audit_context)
        for secret in ("SYNTHETIC_PRIVATE_MQTT", "SYNTHETIC_PRIVATE_WEBHOOK", "SYNTHETIC_PRIVATE_USER", "SYNTHETIC_PRIVATE_PIN",
                       "SYNTHETIC_PRIVATE_HASH", "SYNTHETIC_INJECTION_IGNORE_RULES", "SYNTHETIC_PRIVATE_REGISTRY"):
            self.assertNotIn(secret, output)
        clean_hashes = {s["source_id"]: s["projection_sha256"] for s in first["sources"]}
        dirty_hashes = {s["source_id"]: s["projection_sha256"] for s in response["data"]["sources"]}
        self.assertEqual(clean_hashes, dirty_hashes)
        self.assertNotIn(TARGET, json.dumps(telemetry.audit_context))
        self.assertLessEqual(len(json.dumps(response).encode()), 60_000)

    async def test_known_secret_in_identifier_not_reconstructed_from_another_source(self):
        service, _, _, _, token = setup_service(known_secrets=("synthetic_door",))
        self.addCleanup(end_request, token)
        report = await service.inspect(alarm_entity_id=TARGET, limit=50)
        self.assertNotIn("synthetic_door", json.dumps(report))
        self.assertEqual(report["membership"]["outcome"], "partial")
        self.assertIn("redacted_by_policy", [g["reason"] for g in report["gaps"]])

    async def test_served_tool_raw_argument_failures_never_echo_secret_keys_values(self):
        tool = registered_tools(get_registered_server())["get_integration_inspection"]
        for arguments in (None, [], {"alarm_entity_id": TARGET, "limit": True},
                          {"alarm_entity_id": "SYNTHETIC_PRIVATE_TARGET"},
                          {"alarm_entity_id": TARGET, "SYNTHETIC_PRIVATE_KEY": "SYNTHETIC_PRIVATE_VALUE"}):
            output = await tool.run(arguments)
            result = json.loads(output)
            self.assertFalse(result["success"])
            self.assertEqual(result["error_code"], "invalid_request")
            self.assertNotIn("SYNTHETIC_PRIVATE", output)

    async def test_gateway_rejects_before_sdk_and_audit_excludes_invalid_material(self):
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            config = settings(audit_path=str(audit_path))
            app = Mock(side_effect=AssertionError("SDK must not be reached"))
            core = SimpleNamespace(acquire=Mock(return_value=None))
            gateway = AuthenticatedMcpGateway(app, config, AuditLogger(str(audit_path), config.access_secret), core_runtime=core)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url="http://127.0.0.1:8100") as client:
                for bad in ({"alarm_entity_id": "SYNTHETIC_PRIVATE_TARGET"},
                            {"alarm_entity_id": TARGET, "SYNTHETIC_PRIVATE_KEY": "SYNTHETIC_PRIVATE_VALUE"},
                            {"alarm_entity_id": TARGET, "limit": "SYNTHETIC_PRIVATE_LIMIT", "cursor": "SYNTHETIC_PRIVATE_CURSOR"}):
                    response = await client.post(f"/{config.access_secret}/mcp", json={"jsonrpc": "2.0", "id": "synthetic-request",
                        "method": "tools/call", "params": {"name": "get_integration_inspection", "arguments": bad}})
                    self.assertNotIn("SYNTHETIC_PRIVATE", response.text)
                    self.assertIn("invalid_request", response.text)
            self.assertNotIn("SYNTHETIC_PRIVATE", audit_path.read_text())
            self.assertNotIn(TARGET, audit_path.read_text())
            app.assert_not_called()
            core.acquire.assert_not_called()

    async def test_gateway_positive_output_audit_and_lease_settlement(self):
        service, _, core, _, token = setup_service()
        end_request(token)
        core.acquire = Mock(return_value=object())
        core.consume = Mock(return_value=())
        core.revalidate = Mock(return_value=True)
        core.finish = Mock(return_value=True)
        core.release = Mock(return_value=True)
        async def app(scope, receive, send):
            rpc = json.loads((await receive())["body"])
            tool = registered_tools(get_registered_server())["get_integration_inspection"]
            rendered = await tool.run(rpc["params"]["arguments"])
            result = {"jsonrpc": "2.0", "id": rpc["id"], "result": {"content": [{"type": "text", "text": rendered}], "isError": False}}
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": json.dumps(result).encode()})
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            config = settings(audit_path=str(audit_path))
            gateway = AuthenticatedMcpGateway(app, config, AuditLogger(str(audit_path), config.access_secret), core_runtime=core)
            with patch.object(INTEGRATION_INSPECTION, "service", service), patch.object(public, "SETTINGS", config):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url="http://127.0.0.1:8100") as client:
                    response = await client.post(f"/{config.access_secret}/mcp", json={"jsonrpc": "2.0", "id": "synthetic-request",
                        "method": "tools/call", "params": {"name": "get_integration_inspection", "arguments": {"alarm_entity_id": TARGET}}})
            data = json.loads(response.json()["result"]["content"][0]["text"])
            self.assertTrue(data["success"], data)
            self.assertEqual(data["data"]["membership"]["total_in_scope"], 4)
            text = audit_path.read_text()
            self.assertNotIn(TARGET, text)
            self.assertNotIn("binary_sensor.synthetic", text)
            rows = [json.loads(row) for row in text.splitlines()]
            record = next(r for r in rows if r.get("tool_name") == "get_integration_inspection")
            self.assertEqual(record["analysis_summary"]["upstream_calls"], 0)
            self.assertEqual(record["resource_ids"], {})
            core.finish.assert_called_once()
            # Consumed authority is finished; only an unconsumed lease is released.
            core.release.assert_not_called()

    async def test_provider_exception_text_not_in_public_failure(self):
        values = fixture()
        values["manifest"] = ValueError("SYNTHETIC_PRIVATE_PROVIDER_EXCEPTION")
        service, _, _, _, token = setup_service(values)
        self.addCleanup(end_request, token)
        with patch.object(INTEGRATION_INSPECTION, "service", service):
            rendered = await public.get_integration_inspection(alarm_entity_id=TARGET)
        self.assertFalse(json.loads(rendered)["success"])
        self.assertNotIn("SYNTHETIC_PRIVATE", rendered)
