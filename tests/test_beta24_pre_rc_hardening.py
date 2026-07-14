import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.application import validate_settings  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import ConfigurationError  # noqa: E402
from ha_mcp_engineering.observability import METRICS  # noqa: E402
from ha_mcp_engineering.providers import (  # noqa: E402
    EvidenceRequest,
    EvidenceRouter,
    ProviderCapability,
    StandardHaMcpGateway,
)
from ha_mcp_engineering.request_context import current_telemetry  # noqa: E402
from ha_mcp_engineering.routing import (  # noqa: E402
    MAX_BUCKET_STORE_SIZE,
    UNKNOWN_CLIENT_IDENTITY,
    AuthenticatedMcpGateway,
)
from ha_mcp_engineering.tools import compatibility  # noqa: E402


SECRET = "beta24-test-access-secret-value"


def settings(audit_path: str, **overrides) -> Settings:
    values = {
        "ha_url": "http://supervisor/core",
        "ha_token": "test-token",
        "access_secret": SECRET,
        "port": 8100,
        "audit_path": audit_path,
        "rate_limit_per_minute": 120,
        "rate_limit_burst": 25,
        "destructive_services": frozenset(),
    }
    values.update(overrides)
    return Settings(**values)


class RecordingApp:
    def __init__(self, *, refusal=False):
        self.calls = 0
        self.refusal = refusal

    async def __call__(self, scope, receive, send):
        self.calls += 1
        if self.refusal:
            telemetry = current_telemetry()
            telemetry.error_code = "provider_prohibited"
            telemetry.result_status = "failure"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})


def scope(*, peer=("10.0.0.5", 1234), forwarded=None):
    headers = []
    if forwarded is not None:
        headers.append((b"cf-connecting-ip", forwarded.encode("ascii")))
    return {"type": "http", "headers": headers, "client": peer}


class TrustedProxyIdentityTests(unittest.TestCase):
    def gateway(self, **overrides):
        configured = settings("unused", **overrides)
        return AuthenticatedMcpGateway(RecordingApp(), configured, AuditLogger("unused", SECRET, enabled=False))

    def test_forwarded_header_is_ignored_by_default(self):
        gateway = self.gateway()
        self.assertEqual(
            gateway._client_ip(scope(forwarded="203.0.113.77")), "10.0.0.5"
        )

    def test_valid_forwarded_address_is_used_only_for_trusted_peer(self):
        gateway = self.gateway(
            trust_cf_connecting_ip=True,
            trusted_proxy_cidrs=("10.0.0.0/8",),
        )
        self.assertEqual(
            gateway._client_ip(scope(forwarded="203.0.113.77")), "203.0.113.77"
        )
        self.assertEqual(
            gateway._client_ip(
                scope(peer=("192.0.2.9", 1234), forwarded="203.0.113.88")
            ),
            "192.0.2.9",
        )

    def test_malformed_forwarded_address_falls_back_to_direct_peer(self):
        gateway = self.gateway(
            trust_cf_connecting_ip=True,
            trusted_proxy_cidrs=("10.0.0.0/8",),
        )
        self.assertEqual(
            gateway._client_ip(scope(forwarded="not-an-ip,203.0.113.9")),
            "10.0.0.5",
        )

    def test_ipv6_is_canonicalized_and_trusted(self):
        gateway = self.gateway(
            trust_cf_connecting_ip=True,
            trusted_proxy_cidrs=("2001:db8::/32",),
        )
        self.assertEqual(
            gateway._client_ip(
                scope(peer=("2001:0DB8:0:0::5", 1), forwarded="2001:db8:ffff::7")
            ),
            "2001:db8:ffff::7",
        )

    def test_missing_direct_peer_never_trusts_arbitrary_header(self):
        gateway = self.gateway(
            trust_cf_connecting_ip=True,
            trusted_proxy_cidrs=("0.0.0.0/0",),
        )
        self.assertEqual(
            gateway._client_ip(scope(peer=None, forwarded="203.0.113.77")),
            UNKNOWN_CLIENT_IDENTITY,
        )

    def test_changing_untrusted_forwarded_values_does_not_mint_identity(self):
        gateway = self.gateway()
        identities = {
            gateway._client_ip(scope(forwarded=value))
            for value in ("203.0.113.1", "203.0.113.2", "198.51.100.9")
        }
        self.assertEqual(identities, {"10.0.0.5"})

    def test_invalid_or_excessive_trusted_proxy_configuration_fails_startup(self):
        with self.assertRaises(ConfigurationError):
            validate_settings(
                settings("unused", trusted_proxy_cidrs=("not-a-network",))
            )
        with self.assertRaises(ConfigurationError):
            validate_settings(
                settings(
                    "unused",
                    trusted_proxy_cidrs=tuple(
                        f"192.0.2.{index % 255}/32" for index in range(65)
                    ),
                )
            )


class BoundedBucketStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gateway = AuthenticatedMcpGateway(
            RecordingApp(), settings("unused"), AuditLogger("unused", SECRET, enabled=False)
        )

    def fill(self, store, prefix):
        for index in range(MAX_BUCKET_STORE_SIZE):
            self.gateway._bucket(store, f"{prefix}-{index}", 60, 10)

    def test_client_store_evicts_only_oldest_entry(self):
        self.fill(self.gateway.clients, "client")
        preserved = self.gateway.clients["client-2"]
        preserved.tokens = 3.5
        self.gateway._bucket(self.gateway.clients, "client-0", 60, 10)
        self.gateway._bucket(self.gateway.clients, "client-new", 60, 10)
        self.assertEqual(len(self.gateway.clients), MAX_BUCKET_STORE_SIZE)
        self.assertNotIn("client-1", self.gateway.clients)
        self.assertIn("client-0", self.gateway.clients)
        self.assertIs(self.gateway.clients["client-2"], preserved)
        self.assertEqual(self.gateway.clients["client-2"].tokens, 3.5)
        self.assertIn("client-new", self.gateway.clients)

    def test_auth_failure_store_is_bounded_independently(self):
        self.gateway._bucket(self.gateway.clients, "stable-client", 60, 10).tokens = 2
        self.fill(self.gateway.auth_failures, "auth")
        self.gateway._bucket(self.gateway.auth_failures, "auth-new", 0.5, 5)
        self.assertEqual(len(self.gateway.auth_failures), MAX_BUCKET_STORE_SIZE)
        self.assertNotIn("auth-0", self.gateway.auth_failures)
        self.assertIn("stable-client", self.gateway.clients)
        self.assertEqual(self.gateway.clients["stable-client"].tokens, 2)

    async def test_concurrent_async_bucket_creation_remains_bounded(self):
        async def create(index):
            self.gateway._bucket(self.gateway.clients, f"async-{index}", 60, 10)
            await asyncio.sleep(0)

        await asyncio.gather(
            *(create(index) for index in range(MAX_BUCKET_STORE_SIZE + 50))
        )
        self.assertEqual(len(self.gateway.clients), MAX_BUCKET_STORE_SIZE)


class ProviderUnavailableAccountingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        METRICS.reset()

    async def test_registered_but_known_unavailable_provider_is_not_dispatched(self):
        provider = StandardHaMcpGateway()
        provider.fetch = AsyncMock(side_effect=AssertionError("unavailable provider must not be invoked"))
        result = await EvidenceRouter([provider]).fetch(
            EvidenceRequest(ProviderCapability.BROAD_ENTITY_SEARCH)
        )
        self.assertEqual(result.completeness.value, "unavailable")
        provider.fetch.assert_not_awaited()
        metrics = METRICS.snapshot()["provider_routing"]
        self.assertEqual(metrics["requests_by_provider"], {})
        self.assertEqual(metrics["successful_requests_by_provider"], {})
        self.assertEqual(metrics["failures_by_provider"], {})


class AuditLogBoundTests(unittest.IsolatedAsyncioTestCase):
    async def test_line_requests_are_always_clamped_to_one_through_500(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            path.write_text(
                "".join(json.dumps({"event": "tool_call", "index": i}) + "\n" for i in range(700)),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"AUDIT_PATH": str(path)}):
                expectations = {None: 50, 0: 1, -1: 1, 1: 1, 500: 500, 501: 500, 10**9: 500}
                for requested, expected in expectations.items():
                    with self.subTest(lines=requested):
                        rendered = (
                            await compatibility.get_audit_log()
                            if requested is None
                            else await compatibility.get_audit_log(lines=requested)
                        )
                        self.assertEqual(len(rendered.splitlines()), expected)

    async def test_event_filter_remains_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            rows = [
                json.dumps({"event": "tool_call" if i % 2 else "auth_failure", "index": i})
                for i in range(20)
            ]
            path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            with patch.dict(os.environ, {"AUDIT_PATH": str(path)}):
                rendered = await compatibility.get_audit_log(lines=3, event="auth_failure")
            self.assertEqual(len(rendered.splitlines()), 3)
            self.assertNotIn('"event": "tool_call"', rendered)


class UpsertAuditBoundsTests(unittest.IsolatedAsyncioTestCase):
    async def test_refused_upsert_audit_excludes_configuration_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            audit_path = str(Path(directory) / "audit.jsonl")
            configured = settings(audit_path)
            app = RecordingApp(refusal=True)
            gateway = AuthenticatedMcpGateway(
                app, configured, AuditLogger(audit_path, SECRET)
            )
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "upsert_automation",
                        "arguments": {
                            "automation_id": "safe_fixture",
                            "config_json": json.dumps(
                                {"trigger": [], "action": [], "description": "must-not-be-audited"}
                            ),
                        },
                    },
                }
            ).encode()
            delivered = False

            async def receive():
                nonlocal delivered
                if delivered:
                    return {"type": "http.disconnect"}
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}

            async def send(message):
                return None

            request_scope = {
                "type": "http",
                "method": "POST",
                "path": f"/{SECRET}/mcp",
                "raw_path": f"/{SECRET}/mcp".encode(),
                "headers": [],
                "client": ("127.0.0.1", 1),
            }
            await gateway(request_scope, receive, send)
            record = json.loads(Path(audit_path).read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(record["access"], "write")
            self.assertEqual(record["error_code"], "provider_prohibited")
            self.assertEqual(
                record["parameters"],
                {"automation_id": "safe_fixture", "refusal_reason": "governance_required"},
            )
            self.assertEqual(record["ha_endpoint_categories"], [])
            self.assertNotIn("must-not-be-audited", json.dumps(record))


if __name__ == "__main__":
    unittest.main()
