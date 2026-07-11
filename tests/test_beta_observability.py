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
from unittest.mock import patch

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
    ConfigurationError,
    ErrorCode,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
    error_definition,
    map_exception,
)
from ha_mcp_engineering.logging_config import JsonFormatter, log_event, redact_data  # noqa: E402
from ha_mcp_engineering.models import FailureResponse, SuccessResponse, Timing  # noqa: E402
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    current_request_id,
    end_request,
    normalize_request_id,
)
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.tool_framework import run_structured  # noqa: E402
from ha_mcp_engineering.tools import compatibility  # noqa: E402


SECRET = "observability-test-access-secret"


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


class ErrorTaxonomyTests(unittest.TestCase):
    def test_stable_error_code_catalog_and_mappings(self):
        expected = {
            "authentication_failure", "authorization_failure", "invalid_request",
            "validation_failure", "home_assistant_unavailable",
            "home_assistant_api_error", "home_assistant_timeout", "entity_not_found",
            "automation_not_found", "unsupported_operation", "configuration_conflict",
            "rate_limit_exceeded", "internal_server_error",
        }
        self.assertEqual({code.value for code in ERROR_CATALOG}, expected)
        for definition in ERROR_CATALOG.values():
            self.assertTrue(definition.message)
            self.assertIsInstance(definition.http_status, int)
            self.assertTrue(definition.mcp_mapping)
            self.assertIn("endpoint_category", definition.safe_detail_fields)

    def test_retryable_classification(self):
        for code in (ErrorCode.HA_TIMEOUT, ErrorCode.HA_UNAVAILABLE, ErrorCode.RATE_LIMIT_EXCEEDED):
            self.assertTrue(error_definition(code).retryable)
        for code in (ErrorCode.INVALID_REQUEST, ErrorCode.AUTHENTICATION_FAILURE):
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
        self.assertIn("<redacted>", encoded)
        self.assertIn("<access_secret>", encoded)

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


class GatewayAndHealthTests(unittest.TestCase):
    def test_rate_limit_response_uses_stable_error_mapping(self):
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

    def test_get_server_health_returns_safe_data(self):
        with tempfile.TemporaryDirectory() as directory:
            create_application(settings(str(Path(directory) / "audit.jsonl")))
            payload = json.loads(asyncio.run(compatibility.get_server_health(check_ha=False)))
        self.assertTrue(payload["success"])
        health = payload["data"]
        self.assertEqual(health["server"]["version"], "2.0.0-beta.1")
        self.assertEqual(health["registered_tool_count"], 26)
        self.assertTrue(health["redaction"]["enabled"])
        encoded = json.dumps(payload)
        self.assertNotIn(SECRET, encoded)
        self.assertNotIn("test-ha-token", encoded)
        self.assertNotIn("/mcp", encoded)

    def test_get_server_health_is_beta_only(self):
        beta_names = {
            tool.name for tool in compatibility.mcp._tool_manager.list_tools()
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
