import asyncio
from collections import Counter
from contextlib import redirect_stderr
import importlib.util
import io
import json
import logging
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient
import yaml


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DIR = ROOT / "hass_mcp_admin"
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(PRODUCTION_DIR))
sys.path.insert(0, str(BETA_DIR))

production_spec = importlib.util.spec_from_file_location(
    "v1_server", PRODUCTION_DIR / "server.py"
)
production_server = importlib.util.module_from_spec(production_spec)
assert production_spec.loader is not None
production_spec.loader.exec_module(production_server)

from ha_mcp_engineering.application import create_application  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.logging_config import JsonFormatter  # noqa: E402
from ha_mcp_engineering.observability import METRICS  # noqa: E402
from ha_mcp_engineering.capabilities import (  # noqa: E402
    CAPABILITIES,
    PLANNED_CAPABILITIES,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.governance import GOVERNANCE  # noqa: E402
from ha_mcp_engineering.dependency import DEPENDENCY_ANALYSIS  # noqa: E402
from ha_mcp_engineering.dependency.service import AnalysisOutput  # noqa: E402
from ha_mcp_engineering.reliability import RELIABILITY_ANALYSIS  # noqa: E402
from ha_mcp_engineering.reliability.models import ReliabilityAnalysisOutput  # noqa: E402
from ha_mcp_engineering.impact import CHANGE_IMPACT_ANALYSIS  # noqa: E402
from ha_mcp_engineering.impact.models import ImpactAnalysisOutput  # noqa: E402
from ha_mcp_engineering.tools import compatibility  # noqa: E402
from ha_mcp_engineering.tools.registry import get_registered_server  # noqa: E402


SECRET = "beta-regression-access-secret"
INITIALIZE_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "beta-regression", "version": "1"},
    },
}


class FakeResponse:
    def __init__(self, status, body='{"message":"safe fake upstream response"}'):
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self.body


class FakeSession:
    def __init__(self, status, body='{"message":"safe fake upstream response"}'):
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def request(self, *args, **kwargs):
        return FakeResponse(self.status, self.body)


def beta_settings(audit_path: str) -> Settings:
    return Settings(
        ha_url="http://supervisor/core",
        ha_token="test-token",
        access_secret=SECRET,
        port=8100,
        audit_path=audit_path,
        rate_limit_per_minute=120,
        rate_limit_burst=100,
        destructive_services=frozenset(),
        governance_path=str(Path(audit_path).parent / "governance"),
    )


class FakeGovernanceGateway:
    def __init__(self):
        self.configs = {
            "mcp_governance_test": {
                "alias": "Governance fixture",
                "trigger": [{"platform": "state", "entity_id": "binary_sensor.example"}],
                "condition": [],
                "action": [{"service": "notify.example", "data": {"message": "before"}}],
                "mode": "single",
            }
        }

    async def get(self, automation_id):
        import copy
        return copy.deepcopy(self.configs.get(automation_id))

    async def write(self, automation_id, config):
        import copy
        self.configs[automation_id] = copy.deepcopy(config)
        return {"result": "ok"}

    async def validate(self):
        return {"result": "valid", "errors": None}


class FakeDependencyService:
    async def analyze(self, **kwargs):
        entity_id = kwargs["entity_id"].strip().lower()
        return AnalysisOutput(
            data={
                "target": {"entity_id": entity_id, "entity_exists": False, "registry_entry_exists": False, "domain": entity_id.split(".", 1)[0]},
                "overview": {"dependency_status": "not_detected", "direct_reference_count": 0},
                "assessment": {"rename_or_removal_status": "unknown_due_to_incomplete_coverage", "reason": "Coverage is incomplete."},
                "findings": [],
                "source_coverage": [{"source_type": "dashboard", "completeness": "unavailable"}],
                "pagination": {"requested_limit": 50, "effective_limit": 50, "maximum_limit": 100, "clamped": False, "clamp_reason": None, "returned": 0, "total": 0, "has_more": False, "next_cursor": None},
                "index": {"fingerprint": "safe-fingerprint", "generation": 1, "cache_hit": True, "lookup_duration_ms": 0.1, "original_build_duration_ms": 1.0, "current_request_duration_ms": 0.2},
            },
            warnings=["Dashboard configuration is unavailable."],
            metadata={"detail_level": kwargs.get("detail_level", "summary"), "partial": True},
            partial=True,
        )


class FakeReliabilityService:
    async def analyze(self, **kwargs):
        automation_id = kwargs["automation_id"]
        return ReliabilityAnalysisOutput(
            data={
                "target": {
                    "automation_id": automation_id,
                    "entity_id": "automation.reliability_fixture",
                    "friendly_name": "Reliability fixture",
                    "enabled": True,
                },
                "analysis_timestamp": "2026-07-12T12:00:00+00:00",
                "requested_lookback_hours": kwargs.get("lookback_hours", 168),
                "overall_assessment": "partial_evidence",
                "result_status": "partial",
                "finding_counts_by_severity": {"info": 1, "low": 0, "medium": 0, "high": 0, "critical": 0},
                "findings": [{"finding_id": "finding-safe", "rule_id": "no_recent_execution_evidence", "severity": "info"}],
                "evidence_references": [{"reference_id": "ev-safe", "source_type": "trace_coverage", "summary": "No trace evidence."}],
                "configuration_fingerprint": "safe-configuration-fingerprint",
                "evidence_source_coverage": [{"source_type": "automation_traces", "provider": "direct_ha_api", "completeness": "unavailable"}],
                "pagination": {"requested_limit": kwargs.get("limit", 20), "effective_limit": kwargs.get("limit", 20), "maximum_limit": 100, "returned": 1, "total": 1, "has_more": False, "next_cursor": None},
            },
            warnings=["Trace evidence is unavailable."],
            metadata={
                "routing": {"classification": "engineering_native", "provider": "engineering", "fallback_occurred": False},
                "source_coverage": [{"source_type": "automation_traces", "provider": "direct_ha_api", "completeness": "unavailable"}],
            },
            partial=True,
        )


class FakeImpactService:
    async def analyze(self, **kwargs):
        entity_id = kwargs["entity_id"]
        return ImpactAnalysisOutput(
            data={
                "target_entity_summary": {
                    "entity_id": entity_id,
                    "state_status": "available",
                },
                "requested_operation": kwargs["operation"],
                "analysis_timestamp": "2026-07-14T12:00:00Z",
                "final_assessment": "review_required",
                "result_status": "partial",
                "finding_count": 1,
                "findings": [
                    {
                        "finding_id": "impact-safe",
                        "rule_id": "direct_automation_reference",
                        "severity": "medium",
                    }
                ],
                "affected_object_groups": [],
                "evidence_references": [
                    {
                        "reference_id": "impact-evidence-safe",
                        "source_type": "automation",
                        "summary": "Bounded evidence.",
                    }
                ],
                "source_coverage_matrix": [
                    {
                        "source_type": "automation",
                        "completeness": "partial",
                    }
                ],
                "pagination": {
                    "requested_limit": 20,
                    "effective_limit": 20,
                    "maximum_limit": 100,
                    "returned": 1,
                    "total": 1,
                    "has_more": False,
                    "next_cursor": None,
                },
            },
            warnings=["Synthetic bounded impact warning."],
            metadata={
                "routing": {
                    "classification": "engineering_native",
                    "provider": "engineering",
                    "policy": "single_entity_change_impact_read",
                    "fallback_occurred": False,
                },
                "source_coverage": [
                    {"source_type": "automation", "completeness": "partial"}
                ],
            },
            partial=True,
        )


class AddonIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.production = yaml.safe_load((PRODUCTION_DIR / "config.yaml").read_text())
        cls.beta = yaml.safe_load((BETA_DIR / "config.yaml").read_text())

    def test_production_metadata_remains_v1_1_2(self):
        self.assertEqual(self.production["name"], "HA MCP Engineering Server")
        self.assertEqual(self.production["slug"], "hass_mcp_admin")
        self.assertEqual(self.production["version"], "1.1.2")
        self.assertEqual(self.production["ports"], {"8099/tcp": 8099})

    def test_beta_metadata_is_distinct_and_valid(self):
        self.assertEqual(self.beta["name"], "HA MCP Engineering Server Beta")
        self.assertEqual(self.beta["slug"], "hass_mcp_engineering_beta")
        self.assertEqual(self.beta["version"], "2.0.0-beta.15")
        self.assertEqual(self.beta["ports"], {"8100/tcp": 8100})
        self.assertNotEqual(self.beta["slug"], self.production["slug"])
        self.assertNotEqual(set(self.beta["ports"]), set(self.production["ports"]))
        self.assertEqual(
            self.beta["slug"].replace("_", "-"), "hass-mcp-engineering-beta"
        )

    def test_dependencies_are_exactly_pinned(self):
        production = (PRODUCTION_DIR / "requirements.txt").read_text().splitlines()
        beta = (BETA_DIR / "requirements.txt").read_text().splitlines()
        self.assertTrue(set(production).issubset(beta))
        self.assertIn("PyYAML==6.0.2", beta)
        self.assertTrue(all("==" in requirement for requirement in beta))

    def test_required_v2_boundaries_and_documentation_exist(self):
        package = BETA_DIR / "ha_mcp_engineering"
        for relative_path in (
            "application.py",
            "mcp_server.py",
            "tools/registry.py",
            "routing.py",
            "clients/rest.py",
            "clients/websocket.py",
            "configuration.py",
            "models/responses.py",
            "models/failures.py",
            "audit.py",
            "sanitization.py",
            "capabilities.py",
            "version.py",
            "governance/models.py",
            "governance/normalize.py",
            "governance/risk.py",
            "governance/storage.py",
            "governance/service.py",
            "tools/governance.py",
            "facilitation/models.py",
            "providers/base.py",
            "providers/models.py",
            "providers/routing.py",
            "providers/standard_mcp.py",
            "providers/direct_ha.py",
            "dependency/models.py",
            "dependency/extraction.py",
            "dependency/provider.py",
            "dependency/index.py",
            "dependency/service.py",
            "tools/analysis.py",
        ):
            self.assertTrue((package / relative_path).is_file(), relative_path)
        self.assertTrue((BETA_DIR / "README.md").is_file())
        self.assertTrue((BETA_DIR / "OBSERVABILITY.md").is_file())
        self.assertTrue((ROOT / "V2_BETA_ARCHITECTURE.md").is_file())
        self.assertTrue((ROOT / "docs" / "CHANGE_GOVERNANCE.md").is_file())
        self.assertTrue((ROOT / "docs" / "SECURITY.md").is_file())
        self.assertTrue((ROOT / "docs" / "TOKEN_EFFICIENCY.md").is_file())
        self.assertTrue(
            (ROOT / "docs" / "architecture" / "ADR-002-ENGINEERING-MCP-FACILITATOR.md").is_file()
        )


class ToolParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.production_tools = {
            tool.name: tool for tool in production_server.mcp._tool_manager.list_tools()
        }
        cls.beta_tools = {
            tool.name: tool for tool in get_registered_server()._tool_manager.list_tools()
        }

    def test_all_25_tools_are_registered(self):
        self.assertEqual(len(self.production_tools), 25)
        self.assertEqual(len(self.beta_tools), 35)
        self.assertEqual(
            set(self.production_tools),
            set(self.beta_tools)
            - {
                "get_server_health",
                "create_change_plan",
                "get_change_plan",
                "list_change_plans",
                "approve_change_plan",
                "apply_change_plan",
                "rollback_change",
                "entity_dependency_analysis",
                "automation_reliability_analysis",
                "change_impact_analysis",
            },
        )

    def test_tool_names_and_argument_schemas_match_v1_1_2(self):
        production_schemas = {
            name: tool.parameters for name, tool in self.production_tools.items()
        }
        beta_schemas = {
            name: self.beta_tools[name].parameters for name in self.production_tools
        }
        self.assertEqual(beta_schemas, production_schemas)

    def test_capability_catalog_preserves_phase3c_provider_truth(self):
        production_catalog = {
            item["tool"]: item for item in production_server.build_capability_catalog()["tools"]
        }
        beta_catalog = {item["tool"]: item for item in CAPABILITIES}
        changed = {name for name in beta_catalog if beta_catalog[name] != production_catalog[name]}
        self.assertEqual(changed, {"get_entity", "list_areas", "search_services", "list_services"})
        for name in changed:
            self.assertEqual(beta_catalog[name]["status"], "transitional")
            self.assertEqual(beta_catalog[name]["routing"], "transitional_direct")
            self.assertEqual(beta_catalog[name]["provider"], "direct_ha_api")
            self.assertEqual(beta_catalog[name]["risk"], "read")
        counts = Counter(item["status"] for item in CAPABILITIES)
        self.assertEqual(
            counts,
            {"native": 8, "transitional": 14, "deprecated": 3},
        )
        self.assertEqual(len(PLANNED_CAPABILITIES), 2)

    def test_server_info_reports_beta_identity(self):
        result = json.loads(asyncio.run(compatibility.server_info(check_ha=False)))
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["server"]["id"], "hass-mcp-engineering-beta")
        self.assertEqual(result["data"]["server"]["name"], "HA MCP Engineering Server Beta")
        self.assertEqual(result["data"]["server"]["version"], "2.0.0-beta.15")
        self.assertEqual(result["data"]["tool_count"], 35)
        self.assertEqual(result["data"]["canonical_tool_count"], 25)

    def test_list_capabilities_reports_expected_catalog(self):
        result = json.loads(asyncio.run(compatibility.list_capabilities()))
        self.assertTrue(result["success"])
        catalog = result["data"]
        self.assertEqual(catalog["count"], 25)
        self.assertEqual(catalog["registered_count"], 35)
        self.assertEqual(len(catalog["planned"]), 2)
        self.assertEqual(
            [item["tool"] for item in catalog["beta_native"]],
            [
                "get_server_health",
                "create_change_plan",
                "get_change_plan",
                "list_change_plans",
                "approve_change_plan",
                "apply_change_plan",
                "rollback_change",
                "entity_dependency_analysis",
                "automation_reliability_analysis",
                "change_impact_analysis",
            ],
        )
        self.assertEqual(
            Counter(item["status"] for item in catalog["tools"]),
            {"native": 8, "transitional": 14, "deprecated": 3},
        )
        self.assertEqual(len(catalog["provider_matrix"]), 5)
        self.assertEqual(
            {item["selected_provider"] for item in catalog["provider_matrix"]},
            {"direct_ha_api", "engineering"},
        )


class BetaApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        settings = beta_settings(str(Path(cls.tempdir.name) / "audit.jsonl"))
        cls.client_context = TestClient(
            create_application(settings), follow_redirects=False
        )
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        cls.tempdir.cleanup()

    def initialize(self, path: str):
        return self.client.post(
            path,
            content=json.dumps(INITIALIZE_REQUEST),
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
            },
        )

    def rpc(self, method, params, *, request_id):
        response = self.client.post(
            f"/{SECRET}/mcp",
            json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
                "x-request-id": request_id,
            },
        )
        data_line = next(
            line
            for line in response.text.replace("\r", "").splitlines()
            if line.startswith("data: ")
        )
        return response, json.loads(data_line.removeprefix("data: "))

    def audit_record(self, request_id):
        path = Path(self.tempdir.name) / "audit.jsonl"
        records = [json.loads(line) for line in path.read_text().splitlines()]
        return next(
            record
            for record in reversed(records)
            if record.get("request_id") == request_id
        )

    def test_beta_application_starts_and_health_check_succeeds(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "ok")

    def test_authenticated_mcp_forms_initialize_without_redirects(self):
        for path in (f"/{SECRET}/mcp", f"/{SECRET}/mcp/"):
            with self.subTest(path=path):
                response = self.initialize(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("location", response.headers)
                self.assertIn("protocolVersion", response.text)

    def test_tools_list_exposes_all_beta_native_tools(self):
        initialized = self.initialize(f"/{SECRET}/mcp")
        self.assertEqual(initialized.status_code, 200)
        response, listing = self.rpc(
            "tools/list", {}, request_id="tools-list-request-123"
        )
        names = [tool["name"] for tool in listing["result"]["tools"]]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(names), 35)
        expected_beta_native = {
            "get_server_health",
            "create_change_plan",
            "get_change_plan",
            "list_change_plans",
            "approve_change_plan",
            "apply_change_plan",
            "rollback_change",
            "entity_dependency_analysis",
            "automation_reliability_analysis",
            "change_impact_analysis",
        }
        self.assertTrue(expected_beta_native.issubset(names))
        dependency_schema = next(
            tool["inputSchema"]
            for tool in listing["result"]["tools"]
            if tool["name"] == "entity_dependency_analysis"
        )
        self.assertEqual(dependency_schema["type"], "object")
        for tool in listing["result"]["tools"]:
            json.dumps(tool["inputSchema"])

        response, call = self.rpc(
            "tools/call",
            {"name": "get_server_health", "arguments": {"check_ha": False}},
            request_id="health-call-request-123",
        )
        tool_payload = json.loads(call["result"]["content"][0]["text"])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(call["result"]["isError"])
        self.assertTrue(tool_payload["success"])
        self.assertEqual(tool_payload["operation"], "get_server_health")
        self.assertEqual(tool_payload["request_id"], "health-call-request-123")

    def test_entity_dependency_analysis_calls_through_real_mcp(self):
        initialized = self.initialize(f"/{SECRET}/mcp")
        self.assertEqual(initialized.status_code, 200)
        previous = DEPENDENCY_ANALYSIS.service
        DEPENDENCY_ANALYSIS.service = FakeDependencyService()
        request_id = "dependency-analysis-request-123"
        try:
            response, call = self.rpc(
                "tools/call",
                {
                    "name": "entity_dependency_analysis",
                    "arguments": {"entity_id": "sensor.removed_sensor"},
                },
                request_id=request_id,
            )
        finally:
            DEPENDENCY_ANALYSIS.service = previous
        payload = json.loads(call["result"]["content"][0]["text"])
        audit = self.audit_record(request_id)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertFalse(payload["data"]["target"]["entity_exists"])
        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(audit["tool_name"], "entity_dependency_analysis")
        self.assertEqual(audit["operation_category"], "analysis")
        self.assertEqual(audit["access"], "read")
        self.assertEqual(audit["result_status"], "partial")
        self.assertEqual(audit["resource_ids"]["entity_id"], "sensor.removed_sensor")
        self.assertNotIn("findings", json.dumps(audit))

    def test_automation_reliability_analysis_calls_through_real_mcp_without_auditing_evidence(self):
        previous = RELIABILITY_ANALYSIS.service
        RELIABILITY_ANALYSIS.service = FakeReliabilityService()
        request_id = "reliability-analysis-request-123"
        try:
            response, call = self.rpc(
                "tools/call",
                {
                    "name": "automation_reliability_analysis",
                    "arguments": {
                        "automation_id": "reliability_fixture",
                        "lookback_hours": 24,
                        "trace_limit": 5,
                        "detail_level": "standard",
                        "limit": 10,
                    },
                },
                request_id=request_id,
            )
        finally:
            RELIABILITY_ANALYSIS.service = previous
        payload = json.loads(call["result"]["content"][0]["text"])
        audit = self.audit_record(request_id)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(payload["metadata"]["routing"]["classification"], "engineering_native")
        self.assertEqual(payload["metadata"]["routing"]["provider"], "engineering")
        self.assertEqual(audit["tool_name"], "automation_reliability_analysis")
        self.assertEqual(audit["operation_category"], "analysis")
        self.assertEqual(audit["access"], "read")
        self.assertEqual(audit["result_status"], "partial")
        self.assertEqual(audit["resource_ids"], {"automation_id": "reliability_fixture"})
        audit_text = json.dumps(audit)
        self.assertNotIn("findings", audit_text)
        self.assertNotIn("evidence", audit_text)
        self.assertNotIn("configuration_fingerprint", audit_text)

    def test_change_impact_analysis_calls_through_real_mcp_without_auditing_evidence(self):
        previous = CHANGE_IMPACT_ANALYSIS.service
        CHANGE_IMPACT_ANALYSIS.service = FakeImpactService()
        request_id = "change-impact-analysis-request-123"
        try:
            response, call = self.rpc(
                "tools/call",
                {
                    "name": "change_impact_analysis",
                    "arguments": {
                        "entity_id": "sensor.impact_fixture",
                        "operation": "remove_entity",
                        "source_types": ["automation", "blueprint"],
                        "detail_level": "standard",
                        "limit": 20,
                    },
                },
                request_id=request_id,
            )
        finally:
            CHANGE_IMPACT_ANALYSIS.service = previous
        payload = json.loads(call["result"]["content"][0]["text"])
        audit = self.audit_record(request_id)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(
            payload["metadata"]["routing"]["classification"],
            "engineering_native",
        )
        self.assertEqual(payload["metadata"]["routing"]["provider"], "engineering")
        self.assertEqual(audit["tool_name"], "change_impact_analysis")
        self.assertEqual(audit["operation_category"], "analysis")
        self.assertEqual(audit["access"], "read")
        self.assertEqual(audit["result_status"], "partial")
        self.assertEqual(
            audit["resource_ids"], {"entity_id": "sensor.impact_fixture"}
        )
        self.assertNotIn("cursor", audit["parameters"])
        audit_text = json.dumps(audit)
        self.assertNotIn("impact-safe", audit_text)
        self.assertNotIn("impact-evidence-safe", audit_text)
        self.assertNotIn("Bounded evidence", audit_text)

    def test_exact_entity_direct_provider_routes_via_real_mcp(self):
        request_id = "direct-provider-integration-123"
        entity = {"entity_id": "sensor.facilitated", "state": "ready", "attributes": {}}
        with patch.object(compatibility, "rest", new=AsyncMock(return_value=entity)) as direct:
            response, call = self.rpc(
                "tools/call",
                {"name": "get_entity", "arguments": {"entity_id": "sensor.facilitated"}},
                request_id=request_id,
            )
        payload = json.loads(call["result"]["content"][0]["text"])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["state"], "ready")
        self.assertEqual(payload["metadata"]["routing"]["provider"], "direct_ha_api")
        self.assertEqual(payload["metadata"]["routing"]["classification"], "transitional_direct")
        self.assertEqual(payload["request_id"], request_id)
        direct.assert_awaited_once_with("GET", "/states/sensor.facilitated")

    def test_system_log_payload_is_sanitized_before_response_logging_and_audit(self):
        request_id = "beta11-system-log-audit-123"
        synthetic_secret = "synthetic-beta11-log-secret-value"
        system_log = [
            {
                "timestamp": 1_789_000_000.0,
                "name": "homeassistant.components.synthetic",
                "level": "ERROR",
                "message": [f"Authorization: Bearer {synthetic_secret}"],
                "exception": f"/api/webhook/{synthetic_secret}",
                "count": 1,
                "source": ["components/synthetic/__init__.py", 42],
            }
        ]
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger = logging.getLogger("ha_mcp_engineering.gateway")
        previous = (logger.handlers, logger.level, logger.propagate)
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        try:
            with patch.object(
                compatibility, "ws_command", new=AsyncMock(return_value=system_log)
            ):
                _, call = self.rpc(
                    "tools/call",
                    {"name": "get_error_log", "arguments": {"tail_lines": 50}},
                    request_id=request_id,
                )
        finally:
            logger.handlers, logger.level, logger.propagate = previous
        payload = json.loads(call["result"]["content"][0]["text"])
        audit = self.audit_record(request_id)
        response_text = json.dumps(payload)
        audit_text = json.dumps(audit)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["data"]["redaction_applied"])
        self.assertNotIn(synthetic_secret, response_text)
        self.assertNotIn(synthetic_secret, stream.getvalue())
        self.assertNotIn(synthetic_secret, audit_text)
        self.assertNotIn("message", audit_text)
        self.assertEqual(audit["parameters"], {"tail_lines": 50})
        self.assertEqual(audit["request_id"], request_id)

    def test_governance_tools_call_end_to_end_through_real_mcp(self):
        initialized = self.initialize(f"/{SECRET}/mcp")
        self.assertEqual(initialized.status_code, 200)
        service = GOVERNANCE.require()
        previous_gateway = service.gateway
        service.gateway = FakeGovernanceGateway()
        try:
            proposed = {
                "alias": "Governance fixture",
                "description": "MCP-governed update",
                "trigger": [{"platform": "state", "entity_id": "binary_sensor.example"}],
                "condition": [],
                "action": [{"service": "notify.example", "data": {"message": "before"}}],
                "mode": "single",
            }
            _, create_call = self.rpc(
                "tools/call",
                {
                    "name": "create_change_plan",
                    "arguments": {
                        "title": "MCP governance integration",
                        "description": "Safe fixture",
                        "operation": "update_automation",
                        "automation_id": "mcp_governance_test",
                        "proposed_config": proposed,
                    },
                },
                request_id="governance-create-123",
            )
            created = json.loads(create_call["result"]["content"][0]["text"])
            self.assertTrue(created["success"])
            plan_id = created["data"]["plan_id"]
            plan_hash = created["data"]["plan_hash"]
            create_audit = self.audit_record("governance-create-123")
            self.assertEqual(
                set(create_audit["parameters"]),
                {"automation_id", "operation"},
            )
            self.assertNotIn("MCP-governed update", json.dumps(create_audit))
            self.assertNotIn(SECRET, json.dumps(create_audit))

            calls = [
                ("get_change_plan", {"plan_id": plan_id}),
                ("list_change_plans", {"status": "awaiting_approval", "limit": 10}),
                ("approve_change_plan", {"plan_id": plan_id, "expected_plan_hash": plan_hash}),
                ("apply_change_plan", {"plan_id": plan_id, "expected_plan_hash": plan_hash}),
            ]
            for index, (name, arguments) in enumerate(calls):
                _, call = self.rpc(
                    "tools/call",
                    {"name": name, "arguments": arguments},
                    request_id=f"governance-call-{index}-123",
                )
                payload = json.loads(call["result"]["content"][0]["text"])
                self.assertTrue(payload["success"], (name, payload))

            _, rollback_request_call = self.rpc(
                "tools/call",
                {"name": "rollback_change", "arguments": {"plan_id": plan_id}},
                request_id="governance-rollback-request-123",
            )
            rollback_request = json.loads(rollback_request_call["result"]["content"][0]["text"])
            self.assertTrue(rollback_request["success"])
            rollback_hash = rollback_request["data"]["plan_hash"]

            _, approval_call = self.rpc(
                "tools/call",
                {"name": "approve_change_plan", "arguments": {"plan_id": plan_id, "expected_plan_hash": rollback_hash}},
                request_id="governance-rollback-approve-123",
            )
            self.assertTrue(json.loads(approval_call["result"]["content"][0]["text"])["success"])
            _, rollback_call = self.rpc(
                "tools/call",
                {"name": "rollback_change", "arguments": {"plan_id": plan_id, "expected_plan_hash": rollback_hash}},
                request_id="governance-rollback-apply-123",
            )
            self.assertTrue(json.loads(rollback_call["result"]["content"][0]["text"])["success"])
        finally:
            service.gateway = previous_gateway

    def test_governance_input_schemas_are_intentional(self):
        tools = {tool.name: tool.parameters for tool in get_registered_server()._tool_manager.list_tools()}
        expected_properties = {
            "create_change_plan": {"title", "description", "operation", "automation_id", "proposed_config", "expiration_minutes", "caller_context"},
            "get_change_plan": {"plan_id"},
            "list_change_plans": {"status", "limit"},
            "approve_change_plan": {"plan_id", "expected_plan_hash", "approval_note"},
            "apply_change_plan": {"plan_id", "expected_plan_hash"},
            "rollback_change": {"plan_id", "expected_plan_hash"},
        }
        for name, properties in expected_properties.items():
            self.assertEqual(set(tools[name]["properties"]), properties)

    def test_unknown_plan_ids_map_to_not_found_across_governance_tools(self):
        plan_id = "0" * 32
        service = GOVERNANCE.require()
        storage_before = service.repository.health()
        errors_before = Counter(METRICS.snapshot()["recent_error_counts"])
        cases = (
            ("get_change_plan", {"plan_id": plan_id}),
            ("approve_change_plan", {"plan_id": plan_id, "expected_plan_hash": "1" * 64}),
            ("apply_change_plan", {"plan_id": plan_id}),
            ("rollback_change", {"plan_id": plan_id}),
        )
        for index, (tool_name, arguments) in enumerate(cases):
            request_id = f"missing-plan-{index}-123"
            _, call = self.rpc(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
                request_id=request_id,
            )
            payload = json.loads(call["result"]["content"][0]["text"])
            audit = self.audit_record(request_id)
            self.assertFalse(payload["success"])
            self.assertEqual(payload["error_code"], "change_plan_not_found")
            self.assertFalse(payload["retryable"])
            self.assertEqual(payload["request_id"], request_id)
            self.assertEqual(audit["result_status"], "failure")
            self.assertEqual(audit["error_code"], "change_plan_not_found")
        storage_after = service.repository.health()
        self.assertEqual(storage_after["write_failures"], storage_before["write_failures"])
        self.assertEqual(storage_after["corruption_count"], storage_before["corruption_count"])
        errors_after = Counter(METRICS.snapshot()["recent_error_counts"])
        self.assertEqual(
            errors_after["change_plan_storage_error"],
            errors_before["change_plan_storage_error"],
        )
        self.assertEqual(
            errors_after["change_plan_not_found"] - errors_before["change_plan_not_found"],
            len(cases),
        )
        _, health_call = self.rpc(
            "tools/call",
            {"name": "get_server_health", "arguments": {"check_ha": False}},
            request_id="missing-plan-health-123",
        )
        health = json.loads(health_call["result"]["content"][0]["text"])["data"]
        self.assertEqual(health["governance"]["storage_status"], "healthy")
        self.assertEqual(
            health["governance"]["storage"]["write_failures"],
            storage_before["write_failures"],
        )

    def _create_plan_through_mcp(self, automation_id, request_id, session):
        proposed = {
            "alias": "Beta 5 create probe",
            "trigger": [{"platform": "state", "entity_id": "binary_sensor.example"}],
            "condition": [],
            "action": [{"service": "notify.example", "data": {"message": "safe fixture"}}],
            "mode": "single",
        }
        with patch(
            "ha_mcp_engineering.clients.rest.aiohttp.ClientSession",
            return_value=session,
        ):
            return self.rpc(
                "tools/call",
                {
                    "name": "create_change_plan",
                    "arguments": {
                        "title": "Beta 5 create probe",
                        "description": "Safe regression fixture",
                        "operation": "create_automation",
                        "automation_id": automation_id,
                        "proposed_config": proposed,
                    },
                },
                request_id=request_id,
            )

    def test_absent_create_id_is_success_and_audit_is_success(self):
        request_id = "create-id-absent-request-123"
        errors_before = Counter(METRICS.snapshot()["recent_error_counts"])
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        loggers = [
            logging.getLogger("ha_mcp_engineering.gateway"),
            logging.getLogger("ha_mcp_engineering.governance"),
        ]
        previous = [(logger.handlers, logger.level, logger.propagate) for logger in loggers]
        for logger in loggers:
            logger.handlers = [handler]
            logger.setLevel(logging.INFO)
            logger.propagate = False
        try:
            _, call = self._create_plan_through_mcp(
                "beta5_absent_create", request_id, FakeSession(404)
            )
        finally:
            for logger, state in zip(loggers, previous):
                logger.handlers, logger.level, logger.propagate = state
        payload = json.loads(call["result"]["content"][0]["text"])
        audit = self.audit_record(request_id)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(audit["result_status"], "success")
        self.assertIsNone(audit["error_code"])
        plan = GOVERNANCE.require().repository.get(payload["data"]["plan_id"])
        self.assertEqual(plan.status.value, "awaiting_approval")
        self.assertEqual(plan.events[-1].event, "change_plan_created")
        self.assertEqual(plan.events[-1].result_status, "success")
        self.assertEqual(plan.events[-1].request_id, request_id)
        self.assertIn(request_id, stream.getvalue())
        errors_after = Counter(METRICS.snapshot()["recent_error_counts"])
        self.assertEqual(
            errors_after["automation_not_found"],
            errors_before["automation_not_found"],
        )

    def test_existing_create_id_is_configuration_conflict(self):
        request_id = "create-id-collision-request-123"
        _, call = self._create_plan_through_mcp(
            "beta5_existing_create", request_id, FakeSession(200, '{"alias":"Already exists"}')
        )
        payload = json.loads(call["result"]["content"][0]["text"])
        audit = self.audit_record(request_id)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "configuration_conflict")
        self.assertEqual(audit["error_code"], "configuration_conflict")
        self.assertEqual(audit["result_status"], "failure")

    def test_create_probe_upstream_500_is_real_api_failure(self):
        request_id = "create-id-upstream-500-request-123"
        _, call = self._create_plan_through_mcp(
            "beta5_upstream_failure", request_id, FakeSession(500)
        )
        payload = json.loads(call["result"]["content"][0]["text"])
        audit = self.audit_record(request_id)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "home_assistant_api_error")
        self.assertEqual(audit["error_code"], "home_assistant_api_error")

    def test_create_probe_malformed_success_response_is_failure(self):
        request_id = "create-id-malformed-request-123"
        _, call = self._create_plan_through_mcp(
            "beta5_malformed_response", request_id, FakeSession(200, "not-json")
        )
        payload = json.loads(call["result"]["content"][0]["text"])
        audit = self.audit_record(request_id)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "home_assistant_api_error")
        self.assertEqual(audit["error_code"], "home_assistant_api_error")

    def test_exact_entity_404_is_correlated_and_audited(self):
        request_id = "entity-404-request-123"
        errors_before = Counter(METRICS.snapshot()["recent_error_counts"])
        provider_before = Counter(
            METRICS.snapshot()["provider_routing"]["failures_by_provider"]
        )
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger = logging.getLogger("ha_mcp_engineering.gateway")
        old_handlers = logger.handlers
        old_level = logger.level
        old_propagate = logger.propagate
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        try:
            with patch(
                "ha_mcp_engineering.clients.rest.aiohttp.ClientSession",
                return_value=FakeSession(404),
            ):
                response, call = self.rpc(
                    "tools/call",
                    {
                        "name": "get_entity",
                        "arguments": {
                            "entity_id": "sensor.beta_smoke_test_nonexistent"
                        },
                    },
                    request_id=request_id,
                )
        finally:
            logger.handlers = old_handlers
            logger.setLevel(old_level)
            logger.propagate = old_propagate
        tool_payload = json.loads(call["result"]["content"][0]["text"])
        audit = self.audit_record(request_id)
        self.assertEqual(response.headers["x-request-id"], request_id)
        self.assertFalse(tool_payload["success"])
        self.assertEqual(tool_payload["error_code"], "entity_not_found")
        self.assertEqual(tool_payload["request_id"], request_id)
        self.assertEqual(audit["result_status"], "failure")
        self.assertEqual(audit["error_code"], "entity_not_found")
        self.assertEqual(audit["request_id"], request_id)
        self.assertIn(request_id, stream.getvalue())
        self.assertNotIn(SECRET, stream.getvalue())
        errors_after = Counter(METRICS.snapshot()["recent_error_counts"])
        provider_after = Counter(
            METRICS.snapshot()["provider_routing"]["failures_by_provider"]
        )
        self.assertEqual(
            errors_after["entity_not_found"] - errors_before["entity_not_found"],
            1,
        )
        self.assertEqual(
            provider_after["direct_ha_api"] - provider_before["direct_ha_api"],
            1,
        )

    def test_invalid_entity_is_counted_once_without_upstream_access(self):
        request_id = "entity-invalid-request-123"
        errors_before = Counter(METRICS.snapshot()["recent_error_counts"])
        provider_before = Counter(
            METRICS.snapshot()["provider_routing"]["failures_by_provider"]
        )
        with patch.object(compatibility, "rest", new=AsyncMock()) as direct:
            _, call = self.rpc(
                "tools/call",
                {"name": "get_entity", "arguments": {"entity_id": "../config"}},
                request_id=request_id,
            )
        payload = json.loads(call["result"]["content"][0]["text"])
        audit = self.audit_record(request_id)
        coverage = payload["metadata"]["source_coverage"][0]
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "invalid_request")
        self.assertEqual(payload["timing"]["home_assistant_ms"], 0.0)
        self.assertEqual(coverage["failure_category"], "request_validation")
        self.assertFalse(coverage["upstream_attempted"])
        self.assertEqual(audit["error_code"], "invalid_request")
        direct.assert_not_awaited()
        errors_after = Counter(METRICS.snapshot()["recent_error_counts"])
        provider_after = Counter(
            METRICS.snapshot()["provider_routing"]["failures_by_provider"]
        )
        self.assertEqual(
            errors_after["invalid_request"] - errors_before["invalid_request"],
            1,
        )
        self.assertEqual(
            provider_after["direct_ha_api"] - provider_before["direct_ha_api"],
            1,
        )

    def test_exact_entity_500_is_audited_as_upstream_failure(self):
        request_id = "entity-500-request-123"
        with patch(
            "ha_mcp_engineering.clients.rest.aiohttp.ClientSession",
            return_value=FakeSession(500),
        ):
            _, call = self.rpc(
                "tools/call",
                {"name": "get_entity", "arguments": {"entity_id": "sensor.fake"}},
                request_id=request_id,
            )
        tool_payload = json.loads(call["result"]["content"][0]["text"])
        audit = self.audit_record(request_id)
        self.assertFalse(tool_payload["success"])
        self.assertEqual(tool_payload["error_code"], "home_assistant_api_error")
        self.assertEqual(audit["result_status"], "failure")
        self.assertEqual(audit["error_code"], "home_assistant_api_error")

    def test_unauthenticated_root_paths_are_rejected(self):
        for path in ("/mcp", "/mcp/"):
            with self.subTest(path=path):
                self.assertEqual(self.initialize(path).status_code, 404)

    def test_secret_is_redacted_from_audit_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            audit = AuditLogger(str(path), SECRET)
            audit.write({"event": "auth_failure", "path": f"/{SECRET}x/mcp"})
            contents = path.read_text()
            self.assertNotIn(SECRET, contents)
            self.assertIn("[REDACTED:token]", contents)

    def test_startup_log_does_not_contain_secret(self):
        settings = beta_settings(str(Path(self.tempdir.name) / "startup-audit.jsonl"))
        output = io.StringIO()
        with patch("ha_mcp_engineering.application.load_settings", return_value=settings), patch(
            "ha_mcp_engineering.application.uvicorn.run"
        ), redirect_stderr(output):
            from ha_mcp_engineering.application import main

            main()
        self.assertNotIn(SECRET, output.getvalue())
        self.assertIn('"event": "server_starting"', output.getvalue())


if __name__ == "__main__":
    unittest.main()
