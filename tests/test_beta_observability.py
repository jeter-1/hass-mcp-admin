import asyncio
from dataclasses import replace
import io
import json
import logging
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
PRODUCTION_DIR = ROOT / "hass_mcp_admin"
sys.path.insert(0, str(BETA_DIR))
sys.path.insert(0, str(PRODUCTION_DIR))

from ha_mcp_engineering.application import create_application, validate_settings  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger, AuditRecord  # noqa: E402
from ha_mcp_engineering.clients.rest import HomeAssistantRestClient  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    ERROR_CATALOG,
    AutomationNotFoundError,
    ConfigurationError,
    ErrorCode,
    GovernanceError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
    error_definition,
    map_exception,
)
from ha_mcp_engineering.governance.helper_state import (  # noqa: E402
    HelperStateGateway,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.health import HEALTH  # noqa: E402
from ha_mcp_engineering.logging_config import JsonFormatter, log_event, redact_data  # noqa: E402
from ha_mcp_engineering.models import FailureResponse, SuccessResponse, Timing  # noqa: E402
from ha_mcp_engineering.observability import METRICS, RuntimeMetrics  # noqa: E402
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    current_request_id,
    end_request,
    normalize_request_id,
)
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.tool_framework import run_structured  # noqa: E402
from ha_mcp_engineering.tools import compatibility, registered_tools  # noqa: E402
from ha_mcp_engineering.version import SERVER_VERSION  # noqa: E402


SECRET = "observability-test-access-secret"


class _RestResponse:
    def __init__(self, status, text):
        self.status = status
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def text(self):
        return self._text


class _RestSession:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def request(self, *_args, **_kwargs):
        return self.response


def settings(audit_path: str, **overrides) -> Settings:
    base = Settings(
        ha_url="http://supervisor/core",
        ha_token="test-ha-token",
        access_secret=SECRET,
        port=8100,
        audit_path=audit_path,
        rate_limit_per_minute=120,
        rate_limit_burst=25,
        destructive_services=frozenset(),
        audit_enabled=True,
        audit_max_payload_chars=8192,
        log_level="INFO",
        ha_timeout_seconds=60,
        response_size_limit=60_000,
        redaction_enabled=True,
    )
    return replace(base, **overrides)


class ResponseContractTests(unittest.TestCase):
    def test_structured_success_response_serialization(self):
        response = SuccessResponse(
            operation="unit_test",
            summary="Completed.",
            data={"value": 1},
            warnings=["safe warning"],
            metadata={"source": "test"},
            timing=Timing(total_ms=2.5, tool_ms=2.0),
            request_id="request-success-123",
        )
        payload = json.loads(response.to_json())
        self.assertTrue(payload["success"])
        self.assertEqual(payload["operation"], "unit_test")
        self.assertEqual(payload["request_id"], "request-success-123")
        self.assertEqual(payload["timing"]["tool_ms"], 2.0)

    def test_structured_failure_response_serialization(self):
        response = FailureResponse(
            operation="unit_test",
            error="InvalidRequestError",
            error_code=ErrorCode.INVALID_REQUEST.value,
            message="The request is invalid.",
            details={"field": "query"},
            retryable=False,
            timing=Timing(total_ms=1.0),
            request_id="request-failure-123",
        )
        payload = json.loads(response.to_json())
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "invalid_request")
        self.assertFalse(payload["retryable"])

    def test_request_id_generation_acceptance_and_propagation(self):
        self.assertEqual(normalize_request_id("caller-request-123"), "caller-request-123")
        self.assertNotEqual(normalize_request_id("bad"), "bad")
        telemetry, token = begin_request("caller-request-456")
        try:
            self.assertEqual(current_request_id(), telemetry.request_id)
            payload = json.loads(asyncio.run(run_structured(
                "correlation_test", "Completed.", lambda: {"ok": True}
            )))
            self.assertEqual(payload["request_id"], "caller-request-456")
        finally:
            end_request(token)

    def test_timing_metadata_captures_duration(self):
        async def action():
            await asyncio.sleep(0.002)
            return {"ok": True}

        telemetry, token = begin_request("timing-request-123")
        try:
            payload = json.loads(asyncio.run(run_structured(
                "timing_test", "Completed.", action
            )))
        finally:
            end_request(token)
        self.assertGreater(payload["timing"]["tool_ms"], 0)
        self.assertGreaterEqual(payload["timing"]["total_ms"], payload["timing"]["tool_ms"])

    def test_addon_not_found_uses_observed_domain_outcome_coverage(self):
        telemetry, token = begin_request("addon-not-found-request-123")
        started = time.perf_counter()
        telemetry.begin_upstream_attempt(started)
        telemetry.finish_upstream_attempt(
            started + 0.001,
            1.0,
        )

        def missing_addon():
            raise GovernanceError(ErrorCode.ADDON_NOT_FOUND)

        try:
            payload = json.loads(
                asyncio.run(
                    run_structured(
                        "create_addon_restart_plan",
                        "Created the add-on restart plan.",
                        missing_addon,
                    )
                )
            )
        finally:
            end_request(token)

        coverage = payload["metadata"]["source_coverage"][0]
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "addon_not_found")
        self.assertFalse(payload["retryable"])
        self.assertEqual(
            payload["metadata"]["classification"], "domain_outcome"
        )
        self.assertEqual(
            payload["metadata"]["completeness"], "not_found"
        )
        self.assertEqual(
            coverage,
            {
                "provider": "upstream_operational_lifecycle",
                "completeness": "not_found",
                "failure_category": (
                    "domain_outcome_addon_not_found"
                ),
                "upstream_attempted": True,
                "fallback_occurred": False,
                "source_type": "installed_addon_inventory",
            },
        )


class ErrorTaxonomyTests(unittest.TestCase):
    def test_stable_error_code_catalog_and_mappings(self):
        expected = {
            "authentication_failure", "authorization_failure", "invalid_request",
            "validation_failure", "home_assistant_unavailable",
            "home_assistant_api_error", "home_assistant_timeout", "entity_not_found",
            "automation_not_found", "resource_not_found", "addon_not_found",
            "unsupported_operation", "configuration_conflict",
            "rate_limit_exceeded", "internal_server_error",
            "change_plan_not_found", "change_plan_expired",
            "dashboard_not_found",
            "change_plan_not_approved", "approval_hash_mismatch",
            "approval_already_consumed", "stale_target_state",
            "external_approval_required", "approval_authority_mismatch",
            "external_approval_invalid", "external_approval_expired",
            "prohibited_change", "policy_snapshot_required",
            "policy_snapshot_mismatch",
            "elevated_risk_acknowledgement_required",
            "approval_principal_mismatch", "approval_sequence_failure",
            "change_plan_rejected",
            "change_in_progress", "unsupported_change_operation",
            "high_risk_change_rejected", "automation_validation_failed",
            "automation_apply_failed", "automation_verification_failed",
            "configuration_validation_failed",
            "configuration_projection_unreviewable",
            "configuration_apply_failed",
            "configuration_verification_failed", "configuration_partial_failure",
            "rollback_not_available", "rollback_approval_required",
            "rollback_failed", "change_plan_storage_error",
            "backup_provider_unavailable", "backup_permission_failure",
            "backup_creation_rejected", "backup_creation_failed",
            "backup_operation_timeout", "backup_verification_timeout",
            "backup_verification_failed", "backup_dispatch_indeterminate",
            "operational_provider_unavailable",
            "self_addon_identity_unavailable",
            "operational_contract_mismatch",
            "operational_validation_failed",
            "operational_action_rejected",
            "operational_verification_pending",
            "operational_verification_failed",
            "operational_dispatch_indeterminate",
            "execution_task_not_found",
            "execution_task_storage_error",
            "execution_task_invalid_state",
            "cancellation_not_permitted_after_dispatch",
            "duplicate_apply_attempt", "internal_invariant_violation",
            "invalid_cursor", "stale_cursor", "analysis_unavailable",
            "provider_unavailable", "provider_timeout", "provider_error",
            "provider_prohibited",
            "upstream_dashboard_not_configured",
            "upstream_dashboard_authentication_failed",
            "upstream_dashboard_endpoint_rejected",
            "upstream_dashboard_connection_failed",
            "upstream_dashboard_timeout",
            "upstream_dashboard_protocol_error",
            "upstream_dashboard_invalid_response",
            "upstream_dashboard_required_tool_missing",
            "upstream_dashboard_schema_incompatible",
            "upstream_dashboard_server_identity_mismatch",
            "upstream_dashboard_version_mismatch",
            "upstream_dashboard_reviewed_contract_mismatch",
            "upstream_dashboard_reviewed_annotation_mismatch",
            "upstream_dashboard_unsupported_trust_profile",
            "upstream_dashboard_prohibited_argument",
            "upstream_dashboard_hash_contract_mismatch",
            "upstream_dashboard_upstream_error",
            "upstream_dashboard_response_too_large",
            "upstream_dashboard_internal_error",
        }
        self.assertEqual({code.value for code in ERROR_CATALOG}, expected)
        for definition in ERROR_CATALOG.values():
            self.assertTrue(definition.message)
            self.assertIsInstance(definition.http_status, int)
            self.assertTrue(definition.mcp_mapping)
            self.assertIn("endpoint_category", definition.safe_detail_fields)

    def test_retryable_classification(self):
        for code in (
            ErrorCode.HA_TIMEOUT,
            ErrorCode.HA_UNAVAILABLE,
            ErrorCode.RATE_LIMIT_EXCEEDED,
            ErrorCode.ANALYSIS_UNAVAILABLE,
            ErrorCode.PROVIDER_TIMEOUT,
            ErrorCode.PROVIDER_ERROR,
            ErrorCode.UPSTREAM_DASHBOARD_CONNECTION_FAILED,
            ErrorCode.UPSTREAM_DASHBOARD_TIMEOUT,
            ErrorCode.UPSTREAM_DASHBOARD_UPSTREAM_ERROR,
            ErrorCode.BACKUP_PROVIDER_UNAVAILABLE,
            ErrorCode.SELF_ADDON_IDENTITY_UNAVAILABLE,
        ):
            self.assertTrue(error_definition(code).retryable)
        for code in (
            ErrorCode.INVALID_REQUEST,
            ErrorCode.AUTHENTICATION_FAILURE,
            ErrorCode.PROVIDER_UNAVAILABLE,
            ErrorCode.PROVIDER_PROHIBITED,
            ErrorCode.UPSTREAM_DASHBOARD_NOT_CONFIGURED,
            ErrorCode.UPSTREAM_DASHBOARD_AUTHENTICATION_FAILED,
            ErrorCode.UPSTREAM_DASHBOARD_ENDPOINT_REJECTED,
            ErrorCode.UPSTREAM_DASHBOARD_REQUIRED_TOOL_MISSING,
            ErrorCode.UPSTREAM_DASHBOARD_SCHEMA_INCOMPATIBLE,
            ErrorCode.ADDON_NOT_FOUND,
        ):
            self.assertFalse(error_definition(code).retryable)

    def test_invalid_request_mapping(self):
        code, _, retryable, details = map_exception(ValueError("unsafe details"))
        self.assertEqual(code, ErrorCode.INVALID_REQUEST)
        self.assertFalse(retryable)
        self.assertEqual(details["exception_type"], "ValueError")
        self.assertNotIn("unsafe details", json.dumps(details))

    def test_home_assistant_timeout_mapping(self):
        client = HomeAssistantRestClient(settings("unused"))
        with patch(
            "ha_mcp_engineering.clients.rest.aiohttp.ClientSession",
            side_effect=asyncio.TimeoutError(),
        ):
            with self.assertRaises(HomeAssistantTimeoutError) as caught:
                asyncio.run(client.request("GET", "/config"))
        self.assertEqual(caught.exception.code, ErrorCode.HA_TIMEOUT)
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn(
            "provider_response_received", caught.exception.details
        )

    def test_home_assistant_unavailable_mapping(self):
        client = HomeAssistantRestClient(settings("unused"))
        with patch(
            "ha_mcp_engineering.clients.rest.aiohttp.ClientSession",
            side_effect=aiohttp.ClientConnectionError("private endpoint detail"),
        ):
            with self.assertRaises(HomeAssistantUnavailableError) as caught:
                asyncio.run(client.request("GET", "/config"))
        self.assertEqual(caught.exception.code, ErrorCode.HA_UNAVAILABLE)
        self.assertNotIn("private endpoint detail", json.dumps(caught.exception.details))
        self.assertNotIn(
            "provider_response_received", caught.exception.details
        )

    def test_rest_success_with_body_returns_after_received_response(self):
        client = HomeAssistantRestClient(settings("unused"))
        response = _RestResponse(200, '{"result":"ok"}')
        with patch(
            "ha_mcp_engineering.clients.rest.aiohttp.ClientSession",
            return_value=_RestSession(response),
        ):
            result = asyncio.run(
                client.request(
                    "POST", "/config/automation/config/fixture"
                )
            )
        self.assertEqual(result, {"result": "ok"})

    def test_rest_empty_success_returns_normally_without_body_evidence(self):
        client = HomeAssistantRestClient(settings("unused"))
        response = _RestResponse(204, "")
        with patch(
            "ha_mcp_engineering.clients.rest.aiohttp.ClientSession",
            return_value=_RestSession(response),
        ):
            result = asyncio.run(
                client.request(
                    "POST", "/config/automation/config/fixture"
                )
            )
        self.assertIsNone(result)

    def test_rest_rejection_records_received_response_without_body(self):
        client = HomeAssistantRestClient(settings("unused"))
        response = _RestResponse(404, "SECRET_RESPONSE_BODY")
        with patch(
            "ha_mcp_engineering.clients.rest.aiohttp.ClientSession",
            return_value=_RestSession(response),
        ):
            with self.assertRaises(AutomationNotFoundError) as caught:
                asyncio.run(
                    client.request(
                        "POST", "/config/automation/config/fixture"
                    )
                )
        self.assertIs(
            caught.exception.details.get("provider_response_received"),
            True,
        )
        self.assertEqual(caught.exception.details.get("status"), 404)
        self.assertNotIn(
            "SECRET_RESPONSE_BODY", json.dumps(caught.exception.details)
        )


class RedactionAndAuditTests(unittest.TestCase):
    def test_nested_header_cookie_and_secret_redaction(self):
        data = {
            "access_secret": SECRET,
            "headers": {"Authorization": "Bearer raw-token", "Cookie": "session=raw"},
            "nested": [{"token": "raw-token"}, {"value": f"prefix-{SECRET}-suffix"}],
        }
        safe = redact_data(data, secret=SECRET)
        encoded = json.dumps(safe)
        for forbidden in (SECRET, "Bearer raw-token", "session=raw", '"raw-token"'):
            self.assertNotIn(forbidden, encoded)
        self.assertIn("[REDACTED:token]", encoded)
        self.assertIn("[REDACTED:auth_cookie]", encoded)

    def test_request_id_is_present_in_structured_logs(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger = logging.getLogger("ha_mcp_engineering.test-correlation")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        _, token = begin_request("log-request-123")
        try:
            log_event(logger, logging.INFO, "test_event", "Safe message.")
        finally:
            end_request(token)
        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["request_id"], "log-request-123")

    def test_structured_logs_redact_secrets_headers_and_cookies(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger = logging.getLogger("ha_mcp_engineering.test-redaction")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        log_event(
            logger,
            logging.INFO,
            "redaction_test",
            "Safe message.",
            context={
                "access_secret": SECRET,
                "authorization": "Bearer raw-token",
                "cookie": "sid=raw-cookie",
                "nested": {"value": SECRET},
            },
            secret=SECRET,
        )
        output = stream.getvalue()
        for forbidden in (SECRET, "Bearer raw-token", "sid=raw-cookie"):
            self.assertNotIn(forbidden, output)

    def test_audit_record_redaction_and_request_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            audit = AuditLogger(str(path), SECRET)
            audit.write(AuditRecord(
                request_id="audit-request-123",
                tool_name="server_info",
                capability_classification="native",
                operation_category="foundation",
                access="read",
                authenticated=True,
                caller_id="caller-safe",
                parameters={
                    "access_secret": SECRET,
                    "headers": {"authorization": "Bearer raw", "cookie": "sid=raw"},
                    "nested": {"value": SECRET},
                },
                result_status="success",
            ))
            record = path.read_text()
            self.assertIn("audit-request-123", record)
            for forbidden in (SECRET, "Bearer raw", "sid=raw"):
                self.assertNotIn(forbidden, record)

    def test_audit_payload_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            audit = AuditLogger(str(path), SECRET, max_payload_chars=512)
            audit.write({
                "event": "tool_call",
                "request_id": "bounded-request-123",
                "parameters": {"large": "x" * 10_000},
            })
            record = path.read_text()
            self.assertLess(len(record), 512)
            self.assertIn("payload_truncated", record)

    def test_audit_output_failure_does_not_crash_read_only_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = AuditLogger(directory, SECRET)
            self.assertFalse(audit.write({"event": "tool_call"}))
            payload = json.loads(asyncio.run(compatibility.server_info(check_ha=False)))
            self.assertTrue(payload["success"])
            self.assertEqual(audit.state()["write_failures"], 1)


class OperationLatencyMetricTests(unittest.TestCase):
    def test_long_lived_transport_does_not_inflate_operation_latency(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = settings(str(Path(directory) / "audit.jsonl"))

            async def app(scope, receive, send):
                if scope["method"] == "GET":
                    await asyncio.sleep(0.15)
                else:
                    await asyncio.sleep(0.002)
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"{}"})

            gateway = AuthenticatedMcpGateway(
                app, configured, AuditLogger(configured.audit_path, SECRET)
            )

            async def invoke(method, rpc=None):
                body = json.dumps(rpc).encode() if rpc else b""
                delivered = False

                async def receive():
                    nonlocal delivered
                    if delivered:
                        return {"type": "http.disconnect"}
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}

                async def send(message):
                    return None

                scope = {
                    "type": "http",
                    "method": method,
                    "path": f"/{SECRET}/mcp",
                    "raw_path": f"/{SECRET}/mcp".encode(),
                    "headers": [],
                    "client": ("127.0.0.1", 1),
                }
                await gateway(scope, receive, send)

            METRICS.reset()
            try:
                asyncio.run(invoke("GET"))
                after_transport = METRICS.snapshot()
                self.assertEqual(after_transport["transport_request_count"], 1)
                self.assertEqual(after_transport["mcp_operation_count"], 0)
                self.assertEqual(after_transport["mcp_operation_latency"]["count"], 0)

                asyncio.run(invoke("POST", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))
                asyncio.run(invoke("POST", {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}))
                asyncio.run(invoke("POST", {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "safe_fake_tool", "arguments": {}},
                }))
                final = METRICS.snapshot()
                self.assertEqual(final["transport_request_count"], 4)
                self.assertEqual(final["mcp_operation_count"], 3)
                self.assertEqual(
                    final["mcp_operation_methods"],
                    {"initialize": 1, "tools/list": 1, "tools/call": 1},
                )
                self.assertEqual(final["tool_latency"]["count"], 1)
                self.assertLess(final["mcp_operation_latency"]["maximum_ms"], 100)
            finally:
                METRICS.reset()

    def test_tool_and_home_assistant_latency_are_independent(self):
        metrics = RuntimeMetrics()
        metrics.record_mcp_operation(12.0, "tools/call")
        metrics.record_tool_call()
        metrics.record_tool_completion(7.0)
        metrics.record_ha(3.0)
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["mcp_operation_latency"]["average_ms"], 12.0)
        self.assertEqual(snapshot["tool_latency"]["average_ms"], 7.0)
        self.assertEqual(snapshot["home_assistant_latency"]["average_ms"], 3.0)

    def test_addon_not_found_is_a_fresh_global_domain_outcome(self):
        metrics = RuntimeMetrics()
        self.assertNotIn(
            "addon_not_found",
            metrics.snapshot()["domain_outcome_counts"],
        )
        self.assertTrue(
            metrics.record_classified_outcome("addon_not_found")
        )
        snapshot = metrics.snapshot()
        self.assertEqual(
            snapshot["domain_outcome_counts"]["addon_not_found"],
            1,
        )
        self.assertEqual(
            snapshot["provider_routing"][
                "provider_operational_failures"
            ],
            {},
        )

    def test_metric_reset_is_deterministic(self):
        metrics = RuntimeMetrics()
        metrics.record_transport_completion()
        metrics.record_mcp_operation(10.0, "initialize")
        metrics.record_tool_call()
        metrics.record_tool_completion(5.0)
        metrics.record_ha(2.0)
        metrics.record_error("safe_test_error")
        metrics.reset()
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["transport_request_count"], 0)
        self.assertEqual(snapshot["mcp_operation_count"], 0)
        self.assertEqual(snapshot["tool_call_count"], 0)
        self.assertEqual(snapshot["mcp_operation_latency"]["count"], 0)
        self.assertEqual(snapshot["tool_latency"]["count"], 0)
        self.assertEqual(snapshot["home_assistant_latency"]["count"], 0)
        self.assertEqual(snapshot["recent_error_counts"], {})


class GatewayAndHealthTests(unittest.TestCase):
    def test_successful_direct_entity_search_reconciles_provider_counters(self):
        METRICS.reset()
        before = METRICS.snapshot()["provider_routing"]
        states = [
            {
                "entity_id": "sensor.example",
                "state": "on",
                "attributes": {"friendly_name": "Example"},
            }
        ]
        with patch.object(
            compatibility, "rest", new=AsyncMock(return_value=states)
        ) as direct:
            payload = json.loads(asyncio.run(compatibility.search_entities("example")))
        after = METRICS.snapshot()["provider_routing"]
        self.assertTrue(payload["success"])
        direct.assert_awaited_once_with("GET", "/states")
        self.assertEqual(
            after["requests_by_provider"].get("direct_ha_api", 0)
            - before["requests_by_provider"].get("direct_ha_api", 0),
            1,
        )
        self.assertEqual(
            after["successful_requests_by_provider"].get("direct_ha_api", 0)
            - before["successful_requests_by_provider"].get("direct_ha_api", 0),
            1,
        )
        self.assertEqual(
            after["failures_by_provider"].get("direct_ha_api", 0)
            - before["failures_by_provider"].get("direct_ha_api", 0),
            0,
        )
        self.assertEqual(after["fallback_attempts"], before["fallback_attempts"])
        self.assertEqual(
            after["prohibited_fallback_attempts"],
            before["prohibited_fallback_attempts"],
        )

    def test_rate_limit_response_uses_stable_error_mapping(self):
        provider_before = METRICS.snapshot()["provider_routing"]
        class RecordingApp:
            async def __call__(self, scope, receive, send):
                raise AssertionError("rate-limited request must not reach app")

        with tempfile.TemporaryDirectory() as directory:
            configured = settings(
                str(Path(directory) / "audit.jsonl"), rate_limit_burst=0
            )
            gateway = AuthenticatedMcpGateway(
                RecordingApp(), configured, AuditLogger(configured.audit_path, SECRET)
            )
            messages = []

            async def receive():
                return {"type": "http.request", "body": b"{}", "more_body": False}

            async def send(message):
                messages.append(message)

            scope = {
                "type": "http", "method": "POST", "path": f"/{SECRET}/mcp",
                "raw_path": f"/{SECRET}/mcp".encode(),
                "headers": [(b"x-request-id", b"rate-request-123")],
                "client": ("127.0.0.1", 1),
            }
            asyncio.run(gateway(scope, receive, send))
            start = next(item for item in messages if item["type"] == "http.response.start")
            body = next(item["body"] for item in messages if item["type"] == "http.response.body")
            payload = json.loads(body)
            self.assertEqual(start["status"], 429)
            self.assertEqual(payload["error_code"], ErrorCode.RATE_LIMIT_EXCEEDED.value)
            self.assertEqual(payload["request_id"], "rate-request-123")
            provider_after = METRICS.snapshot()["provider_routing"]
            self.assertEqual(
                provider_before["requests_by_provider"],
                provider_after["requests_by_provider"],
            )
            self.assertEqual(
                provider_before["failures_by_provider"],
                provider_after["failures_by_provider"],
            )

    def test_get_server_health_returns_safe_data(self):
        with tempfile.TemporaryDirectory() as directory:
            create_application(settings(str(Path(directory) / "audit.jsonl")))
            payload = json.loads(asyncio.run(compatibility.get_server_health(check_ha=False)))
        self.assertTrue(payload["success"])
        health = payload["data"]
        self.assertEqual(health["server"]["version"], SERVER_VERSION)
        self.assertEqual(health["registered_tool_count"], 51)
        helper_provider = health["governance"][
            "operational_administration"
        ]["helper_state_provider"]
        helper_operation = health["governance"][
            "operational_administration"
        ]["operations"]["set_input_boolean_state"]
        self.assertEqual(
            helper_provider["provider"], "direct_home_assistant_state"
        )
        self.assertEqual(
            helper_provider["provider_contract"],
            "direct-ha-exact-input-boolean-v1",
        )
        self.assertEqual(helper_provider["fallback"], "none")
        self.assertEqual(
            helper_operation["provider_identity"],
            "direct_home_assistant_state",
        )
        self.assertEqual(
            helper_operation["provider_contract"],
            "direct-ha-exact-input-boolean-v1",
        )
        self.assertIn("handoff_generation", health)
        self.assertIn("automation_reliability_analysis", health)
        self.assertIn("governance", health)
        self.assertIn("storage_corruption_count", health["governance"])
        self.assertTrue(health["redaction"]["enabled"])
        self.assertIn("mcp_operations", health["latency"])
        self.assertIn("tools", health["latency"])
        self.assertIn("home_assistant", health["latency"])
        self.assertFalse(health["transport"]["session_lifetime_in_latency"])
        self.assertEqual(
            health["provider_routing"]["standard_ha_mcp_delegation"],
            "unavailable",
        )
        self.assertTrue(
            health["provider_routing"]["direct_fallback_requires_explicit_policy"]
        )
        self.assertEqual(
            set(health["provider_routing"]["approved_direct_read_tools"]),
            {
                "search_entities",
                "get_entity",
                "list_areas",
                "search_services",
                "list_services",
            },
        )
        self.assertEqual(health["provider_routing"]["standard_ha_mcp_exact_mapping_count"], 0)
        self.assertEqual(
            health["upstream_dashboard"],
            {
                "configured": False,
                "credential_present": False,
                "reachable": None,
                "operational_status": "unavailable",
                "contract_status": "unknown",
                "reachability_source": "cached",
                "reachability_checked_at": None,
                "reachability_age_seconds": None,
                "reachability_freshness_seconds": 120.0,
                "last_successful_handshake_at": None,
                "last_successful_call_at": None,
                "last_failed_call_at": None,
                "last_failure_category": None,
                "capability_status": "unconfigured",
                "upstream_server_name": None,
                "upstream_server_version": None,
                "mcp_protocol_version": None,
                "upstream_tool_count": 0,
                "required_tool_present": False,
                "required_schema_compatible": False,
                "required_schema_fingerprint": None,
                "required_contract_fingerprint": None,
                "expected_input_schema_fingerprint": None,
                "observed_input_schema_fingerprint": None,
                "input_schema_match": False,
                "expected_reviewed_security_contract_fingerprint": None,
                "observed_reviewed_security_contract_fingerprint": None,
                "reviewed_security_contract_match": False,
                "expected_fixture_runtime_descriptor_fingerprint": None,
                "expected_published_runtime_descriptor_fingerprint": None,
                "observed_runtime_descriptor_fingerprint": None,
                "runtime_descriptor_match": False,
                "published_runtime_descriptor_match": False,
                "runtime_descriptor_drift": "not_observed",
                "catalog_fingerprint": None,
                "trust_mode": None,
                "trust_profile": None,
                "pinned_server_name": "ha-mcp",
                "pinned_server_version": None,
                "reviewed_contract_match": False,
                "admission_status": "not_evaluated",
                "admission_source": None,
                "contract_family": None,
                "compiled_contract_family_count": 2,
                "enabled_upstream_capabilities": [
                    "list_dashboards",
                    "get_dashboard_config",
                ],
                "ordinary_dashboard_read_route": {
                    "route_class": "ordinary_read_provider",
                    "required_tool": "ha_config_get_dashboard",
                    "enabled_capabilities": [
                        "list_dashboards",
                        "get_dashboard_config",
                    ],
                    "publicly_registered": True,
                    "writes_allowed": False,
                    "fallback": "none",
                },
                "governed_dashboard_write_route": {
                    "route_class": "f3_external_approval_only",
                    "required_tools": [
                        "ha_get_skill_guide",
                        "ha_config_set_dashboard",
                    ],
                    "publicly_registered": False,
                    "admission_evaluated_per_operation": True,
                    "exactly_one_dispatch": True,
                    "blind_redispatch_allowed": False,
                    "direct_home_assistant_fallback": False,
                    "fallback": "none",
                },
                "attestation_entry_id": None,
                "attested_upstream_version": None,
                "observed_upstream_version": None,
                "attested_source_commit": None,
                "attested_image_index_digest": None,
                "input_contract_fingerprint": None,
                "security_contract_fingerprint": None,
                "output_contract_fingerprint": None,
                "normalized_runtime_contract_fingerprint": None,
                "release_runtime_contract_fingerprint_model": None,
                "expected_release_runtime_contract_fingerprint": None,
                "observed_release_runtime_contract_fingerprint": None,
                "release_runtime_contract_match": False,
                "runtime_contract_diff_fields": [],
                "runtime_policy_fingerprint_model": None,
                "runtime_policy_state_normalized": None,
                "input_contract_match": False,
                "security_contract_match": False,
                "output_contract_match": False,
                "runtime_contract_match": False,
                "revocation_status": "not_evaluated",
                "validation_reason": None,
                "argument_constraints_active": True,
                "screenshots_allowed": False,
                "preference_writes_allowed": False,
                "last_successful_handshake_timestamp": None,
                "last_successful_dashboard_call_timestamp": None,
                "connection_latency": {
                    "count": 0,
                    "average_ms": None,
                    "maximum_ms": None,
                },
                "tool_call_latency": {
                    "count": 0,
                    "average_ms": None,
                    "maximum_ms": None,
                },
                "request_count": 0,
                "success_count": 0,
                "failure_counts": {
                    "not_configured": 0,
                    "authentication_failed": 0,
                    "endpoint_rejected": 0,
                    "connection_failed": 0,
                    "timeout": 0,
                    "protocol_error": 0,
                    "invalid_response": 0,
                    "required_tool_missing": 0,
                    "schema_incompatible": 0,
                    "server_identity_mismatch": 0,
                    "upstream_version_mismatch": 0,
                    "upstream_attestation_missing": 0,
                    "upstream_attestation_revoked": 0,
                    "upstream_registry_unavailable": 0,
                    "upstream_registry_invalid_signature": 0,
                    "upstream_registry_expired": 0,
                    "upstream_registry_rollback": 0,
                    "upstream_registry_replay_conflict": 0,
                    "upstream_contract_family_unknown": 0,
                    "upstream_input_contract_mismatch": 0,
                    "upstream_security_contract_mismatch": 0,
                    "upstream_output_contract_mismatch": 0,
                    "upstream_runtime_contract_mismatch": 0,
                    "unsupported_protocol_version": 0,
                    "reviewed_contract_mismatch": 0,
                    "reviewed_annotation_mismatch": 0,
                    "input_schema_mismatch": 0,
                    "security_contract_mismatch": 0,
                    "runtime_descriptor_semantic_drift": 0,
                    "annotation_mismatch": 0,
                    "output_contract_mismatch": 0,
                    "unsupported_trust_profile": 0,
                    "prohibited_argument": 0,
                    "hash_contract_mismatch": 0,
                    "upstream_error": 0,
                    "response_too_large": 0,
                    "dashboard_not_found": 0,
                    "internal_error": 0,
                },
                "timeout_count": 0,
                "reconnect_count": 0,
                "session_state": "unconfigured",
                "required_tool": "ha_config_get_dashboard",
                "allowlisted_tool_count": 1,
                "writes_allowed": False,
                "registry_enabled": False,
                "registry_sequence": None,
                "registry_generated_at": None,
                "registry_age_seconds": None,
                "registry_refresh_status": "disabled",
                "last_successful_registry_refresh": None,
                "last_registry_failure_category": None,
                "signature_valid": None,
                "cache_status": "not_loaded",
                "cache_age_seconds": None,
                "registry_refresh_interval_seconds": 21600.0,
                "registry_cache_hard_age_seconds": 604800.0,
                "registry_location": "fixed_repository_https",
            },
        )
        self.assertIn("dependency_analysis", health)
        dependency = health["dependency_analysis"]
        self.assertIn("findings_truncation_event_count", dependency)
        self.assertIn(
            "current_index_unresolved_dynamic_reference_count",
            dependency,
        )
        self.assertEqual(
            dependency["counter_semantics"][
                "findings_truncation_event_count"
            ],
            "cumulative_process_events",
        )
        encoded = json.dumps(payload)
        self.assertNotIn(SECRET, encoded)
        self.assertNotIn("test-ha-token", encoded)
        self.assertNotIn("/mcp", encoded)

    def test_helper_provider_health_tracks_exact_ha_probe_without_substitution(self):
        with tempfile.TemporaryDirectory() as directory:
            create_application(settings(str(Path(directory) / "audit.jsonl")))
            previous_governance = HEALTH.governance
            HEALTH.governance = ChangeGovernanceService(
                ChangePlanRepository(Path(directory) / "governance-health"),
                object(),
                helper_state_gateway=HelperStateGateway(object(), object()),
            )
            try:
                with patch.object(
                    compatibility,
                    "rest",
                    AsyncMock(return_value={"version": "2026.8.1"}),
                ):
                    healthy = json.loads(
                        asyncio.run(
                            compatibility.get_server_health(check_ha=True)
                        )
                    )["data"]
                with patch.object(
                    compatibility,
                    "rest",
                    AsyncMock(
                        side_effect=RuntimeError("synthetic outage")
                    ),
                ):
                    unavailable = json.loads(
                        asyncio.run(
                            compatibility.get_server_health(check_ha=True)
                        )
                    )["data"]
            finally:
                HEALTH.governance = previous_governance

        for health, expected in (
            (healthy, "available"),
            (unavailable, "unavailable"),
        ):
            provider = health["governance"][
                "operational_administration"
            ]["helper_state_provider"]
            operation = health["governance"][
                "operational_administration"
            ]["operations"]["set_input_boolean_state"]
            self.assertEqual(
                provider["provider"], "direct_home_assistant_state"
            )
            self.assertEqual(
                provider["provider_contract"],
                "direct-ha-exact-input-boolean-v1",
            )
            self.assertEqual(provider["operational_status"], expected)
            self.assertEqual(provider["fallback"], "none")
            self.assertEqual(
                operation["provider_identity"],
                "direct_home_assistant_state",
            )
            self.assertNotEqual(
                operation["provider_identity"],
                "upstream_operational_lifecycle",
            )

    def test_get_server_health_is_beta_only(self):
        beta_names = {
            tool.name for tool in registered_tools(compatibility.mcp).values()
        }
        production_source = (PRODUCTION_DIR / "server.py").read_text()
        self.assertIn("get_server_health", beta_names)
        self.assertNotIn("def get_server_health", production_source)

    def test_configuration_validation_is_explicit_and_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid = settings(
                str(Path(directory) / "audit.jsonl"),
                access_secret="short",
                port=70000,
                log_level="VERBOSE",
                ha_timeout_seconds=0,
                response_size_limit=10,
                redaction_enabled=False,
            )
            with self.assertRaises(ConfigurationError) as caught:
                validate_settings(invalid)
        details = json.dumps(caught.exception.details)
        self.assertIn("issues", details)
        self.assertNotIn("test-ha-token", details)
        self.assertNotIn(SECRET, details)


if __name__ == "__main__":
    unittest.main()
