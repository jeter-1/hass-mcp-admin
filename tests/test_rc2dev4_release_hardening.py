"""Focused RC2dev4 bake and release-hardening acceptance contracts."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from tests.support import loop_gap
from tests.test_beta_observability import SECRET, settings
from tests.test_entity_dependency_analysis import FakeProvider, coverage, finding, scan, TARGET
from tests.test_governance import GovernanceTestCase
from tests.test_automation_reliability_analysis import bundle as reliability_bundle
from tests.test_rc3a_reviewed_contract import (
    ReviewedFakeTransport,
    reviewed_handshake,
    settings as dashboard_settings,
)

from ha_mcp_engineering.audit import AuditLogger
from ha_mcp_engineering.clients.mcp import DashboardTransportError
from ha_mcp_engineering.dependency.index import DependencyIndex
from ha_mcp_engineering.dependency.provider import DirectHaDependencyProvider
from ha_mcp_engineering.dependency.service import EntityDependencyAnalysisService
from ha_mcp_engineering.errors import DashboardProviderError, ErrorCode, GovernanceError
from ha_mcp_engineering.logging_config import JsonFormatter
from ha_mcp_engineering.observability import METRICS
from ha_mcp_engineering.providers.upstream_dashboard import UpstreamDashboardProvider
from ha_mcp_engineering.reliability.rules import build_root_cause_groups, evaluate_rules
from ha_mcp_engineering.routing import AuthenticatedMcpGateway
from ha_mcp_engineering.sanitization import sanitize_untrusted_data
from ha_mcp_engineering.tools import compatibility


async def _asgi_request(gateway, path: str, *, client="127.0.0.1", body=b"{}"):
    messages = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await gateway(
        {
            "type": "http", "method": "POST", "path": path,
            "raw_path": path.encode(),
            "headers": [(b"host", b"127.0.0.1:8100")], "client": (client, 1),
        },
        receive,
        send,
    )
    status = next(item["status"] for item in messages if item["type"] == "http.response.start")
    response = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
    return status, response


class TransportBakeHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_failure_throttles_without_disabling_valid_client(self):
        calls = 0

        class SafeReadApp:
            async def __call__(inner, scope, receive, send):
                nonlocal calls
                calls += 1
                await send({"type": "http.response.start", "status": 204, "headers": []})
                await send({"type": "http.response.body", "body": b""})

        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            configured = settings(str(audit_path))
            gateway = AuthenticatedMcpGateway(
                SafeReadApp(), configured, AuditLogger(str(audit_path), SECRET)
            )
            attempts = [
                await _asgi_request(gateway, "/missing/mcp", client="127.0.0.9")
                for _ in range(6)
            ]
            statuses = [item[0] for item in attempts]
            valid_status, valid_body = await _asgi_request(gateway, f"/{SECRET}/mcp")
            self.assertEqual(statuses[:5], [404] * 5)
            self.assertEqual(statuses[5], 429)
            self.assertEqual(valid_status, 204)
            self.assertEqual(calls, 1)
            encoded = audit_path.read_text(encoding="utf-8")
            records = [json.loads(line) for line in encoded.splitlines()]
            self.assertEqual(
                [record["event"] for record in records[:5]],
                ["auth_failure"] * 5,
            )
            self.assertEqual(records[5]["event"], "auth_failure_throttled")
            self.assertEqual(records[5]["error_code"], "rate_limit_exceeded")
            self.assertNotIn(SECRET, encoded)
            self.assertNotIn(SECRET.encode(), b"".join(item[1] for item in attempts))
            self.assertNotIn(SECRET.encode(), valid_body)

    async def test_rate_limit_is_structured_audited_and_refills(self):
        class SafeReadApp:
            async def __call__(inner, scope, receive, send):
                await send({"type": "http.response.start", "status": 204, "headers": []})
                await send({"type": "http.response.body", "body": b""})

        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            configured = settings(
                str(audit_path), rate_limit_per_minute=120, rate_limit_burst=1
            )
            gateway = AuthenticatedMcpGateway(
                SafeReadApp(), configured, AuditLogger(str(audit_path), SECRET)
            )
            path = f"/{SECRET}/mcp"
            burst = await asyncio.gather(*(
                _asgi_request(gateway, path) for _ in range(8)
            ))
            statuses = [item[0] for item in burst]
            self.assertEqual(statuses.count(204), 1)
            self.assertEqual(statuses.count(429), 7)
            for status, body in burst:
                if status == 429:
                    self.assertEqual(
                        json.loads(body)["error_code"], "rate_limit_exceeded"
                    )
            gateway.clients["127.0.0.1"].last -= 1.0
            gateway.global_bucket.last -= 1.0
            after_refill, _ = await _asgi_request(gateway, path)
            self.assertEqual(after_refill, 204)
            audit = audit_path.read_text(encoding="utf-8")
            self.assertIn("rate_limited", audit)
            self.assertNotIn(SECRET, audit)


class DependencyBakeAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        METRICS.reset()

    async def test_concurrent_cold_callers_share_one_build_and_warm_is_zero_io(self):
        provider = FakeProvider(scan([finding()]))
        original_scan = provider.scan

        async def delayed_scan():
            await asyncio.sleep(0.03)
            return await original_scan()

        provider.scan = delayed_scan
        service = EntityDependencyAnalysisService(DependencyIndex(provider, ttl_seconds=60))
        first, second = await asyncio.wait_for(
            asyncio.gather(
                service.analyze(entity_id=TARGET),
                service.analyze(entity_id=TARGET),
            ),
            timeout=2,
        )
        self.assertEqual(provider.scan_count, 1)
        self.assertEqual(first.data["index"]["fingerprint"], second.data["index"]["fingerprint"])
        warm_started = time.perf_counter()
        warm = await service.analyze(entity_id=TARGET)
        self.assertLess(time.perf_counter() - warm_started, 1.0)
        self.assertTrue(warm.data["index"]["cache_hit"])
        self.assertEqual(provider.scan_count, 1)

    @staticmethod
    def _cold_build_fixture():
        states = [
            {
                "entity_id": f"automation.fixture_{index}",
                "state": "on",
                "attributes": {"id": f"fixture-{index}", "friendly_name": f"Fixture {index}"},
            }
            for index in range(3)
        ]

        class Rest:
            async def request(inner, method, path):
                if path == "/states":
                    return states
                return {"alias": path.rsplit("/", 1)[-1], "trigger": [], "action": []}

        class WebSocketWithoutBatch:
            async def command(inner, payload):
                return []

        class WebSocket(WebSocketWithoutBatch):
            async def command_batch(inner, payloads):
                outcomes = []
                for payload in payloads:
                    if payload["type"] == "automation/config":
                        internal_id = payload["entity_id"].replace("automation.fixture_", "fixture-")
                        outcomes.append(
                            (True, {"config": {"id": internal_id, "trigger": [], "action": []}})
                        )
                    else:
                        outcomes.append((True, {}))
                return outcomes

        return Rest(), WebSocket(), WebSocketWithoutBatch()

    async def test_cold_build_profile_has_bounded_request_breakdown(self):
        rest, websocket, _ = self._cold_build_fixture()
        provider = DirectHaDependencyProvider(rest, websocket, concurrency=2)
        started = time.perf_counter()
        result = await provider.scan()
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 2.0)  # Fixture regression threshold, not the Pi gate.
        # B39-136-R3b added one bounded read: the running Home Assistant
        # version, which the reviewed template semantics must be admitted
        # against. HAMCP-209 replaced the per-automation stored-config reads
        # with one loaded-configuration batch, and added one service-registry
        # read that admits effect values for display. This pinned breakdown
        # exists to make exactly that kind of change visible.
        self.assertEqual(result.profile["request_count"], 5)
        self.assertEqual(
            result.profile["request_count_by_operation"],
            {
                "effect_display_service_registry": 1,
                "entity_registry_inventory": 1,
                "home_assistant_config": 1,
                "loaded_config_batch": 1,
                "states_inventory": 1,
            },
        )
        self.assertEqual(result.profile["automation_config_source"], "loaded_websocket")
        self.assertEqual(result.profile["loaded_config_command_count"], 6)
        self.assertLessEqual(result.profile["observed_max_concurrency"], 2)
        self.assertFalse(result.profile["inventory_calls_duplicated"])

    async def test_cold_build_without_loaded_source_reports_stored_breakdown(self):
        rest, _, websocket = self._cold_build_fixture()
        provider = DirectHaDependencyProvider(rest, websocket, concurrency=2)
        result = await provider.scan()
        # A client with no batch read has no loaded source: no batch is
        # attempted, and the breakdown is the pre-HAMCP-209 one, with every
        # automation read from stored configuration and the reason reported.
        self.assertEqual(result.profile["request_count"], 7)
        self.assertEqual(
            result.profile["request_count_by_operation"],
            {
                "automation_config": 3,
                "effect_display_service_registry": 1,
                "entity_registry_inventory": 1,
                "home_assistant_config": 1,
                "states_inventory": 1,
            },
        )
        self.assertEqual(result.profile["automation_config_source"], "stored_rest")
        self.assertEqual(
            result.profile["stored_config_fallback_reasons"],
            {"loaded_source_unsupported": 3},
        )
        automation = next(item for item in result.coverage if item.source_type == "automation")
        self.assertEqual(automation.completeness, "complete")
        self.assertLessEqual(result.profile["observed_max_concurrency"], 2)

    async def test_cursor_continuation_is_under_100ms_and_never_rebuilds(self):
        import gc

        provider = FakeProvider(scan([finding(path=f"$.trigger[{i}].entity_id", source_id=str(i)) for i in range(3)]))
        index = DependencyIndex(provider, ttl_seconds=60)
        service = EntityDependencyAnalysisService(index)
        first = await service.analyze(entity_id=TARGET, detail_level="standard", limit=1)
        before = METRICS.snapshot()["dependency_analysis"]
        gaps, delays = [], []
        async def heartbeat():
            last, last_delay = time.perf_counter(), loop_gap.runner_delay()
            while True:
                await asyncio.sleep(.005)
                now, delay = time.perf_counter(), loop_gap.runner_delay()
                gaps.append(now - last)
                delays.append(max(delay - last_delay, 0))
                last, last_delay = now, delay
        # The continuation is served from the built index: prove no build is
        # even scheduled, rather than inferring it from elapsed wall time, and
        # bound the CPU it spends (collection held off for the timed window).
        # CPU time cannot see a blocking wait (sleep, lock, I/O) on the loop
        # thread, so a separately scheduled heartbeat also bounds the longest
        # the continuation leaves the event loop without a turn: by 100 ms
        # plus the unstalled gap and the runner's scheduling delay during that
        # gap, capped well below a 400 ms stall (support/loop_gap.py).
        with patch.object(index, "_ensure_build", wraps=index._ensure_build) as build:
            gc.collect()
            gc.disable()
            try:
                unstalled = await loop_gap.unstalled_gap(.005)
                ticker = asyncio.create_task(heartbeat())
                await asyncio.sleep(0)
                try:
                    started_cpu = time.thread_time()
                    continued = await service.analyze(
                        entity_id=TARGET, detail_level="standard", limit=1,
                        cursor=first.data["pagination"]["next_cursor"],
                    )
                    cpu_seconds = time.thread_time() - started_cpu
                    await asyncio.sleep(.01)
                finally:
                    ticker.cancel()
                    await asyncio.gather(ticker, return_exceptions=True)
            finally:
                gc.enable()
        self.assertTrue(gaps)
        wall = loop_gap.gap_report(gaps, [unstalled + delay for delay in delays], .1)
        print(json.dumps({"fixture": "dependency_cursor_continuation",
                          "maximum_loop_gap_seconds": max(gaps), "unstalled_gap_seconds": unstalled,
                          "maximum_runner_delay_seconds": max(delays), "wall_gap": wall,
                          "thread_cpu_seconds": cpu_seconds, "thread_cpu_bound_seconds": .1}), flush=True)
        build.assert_not_called()
        self.assertEqual(provider.scan_count, 1)
        after = METRICS.snapshot()["dependency_analysis"]
        self.assertEqual(after["index_build_count"], before["index_build_count"])
        self.assertEqual(after["index_cache_hits"], before["index_cache_hits"] + 1)
        self.assertEqual(after["index_cache_misses"], before["index_cache_misses"])
        self.assertTrue(continued.data["index"]["cache_hit"])
        self.assertFalse(continued.data["index"]["refreshed"])
        self.assertEqual(continued.data["index"]["generation"], first.data["index"]["generation"])
        self.assertEqual(continued.data["index"]["built_at"], first.data["index"]["built_at"])
        self.assertLess(cpu_seconds, 0.1)
        self.assertLess(wall["gap_seconds"], wall["bound_seconds"],
                        "Continuation left the event loop without a turn: " + loop_gap.gap_message("Wall", wall))

    async def test_optional_prewarm_requires_connectivity_and_never_raises(self):
        unavailable_provider = FakeProvider(scan([finding()]))
        unavailable_index = DependencyIndex(unavailable_provider, ttl_seconds=60)

        async def unavailable():
            raise ConnectionError("synthetic test fixture")

        self.assertFalse(await unavailable_index.prewarm(unavailable))
        self.assertEqual(unavailable_provider.scan_count, 0)
        failed_health = unavailable_index.health()
        self.assertEqual(failed_health["build_state"], "unbuilt")
        self.assertEqual(failed_health["prewarm_state"], "failed")
        self.assertEqual(
            failed_health["prewarm_failure_category"], "connectivity_not_ready"
        )

        provider = FakeProvider(scan([finding()]))
        index = DependencyIndex(provider, ttl_seconds=60)
        probe_calls = 0

        async def available():
            nonlocal probe_calls
            probe_calls += 1

        self.assertTrue(await index.prewarm(available))
        self.assertEqual(probe_calls, 1)
        self.assertEqual(provider.scan_count, 1)
        self.assertEqual(index.health()["prewarm_state"], "complete")
        self.assertTrue(index.health()["valid"])


class GovernanceLifecycleAcceptanceTests(GovernanceTestCase):
    async def test_new_plan_and_external_challenge_states_are_unambiguous(self):
        created = await self.update_plan()
        self.assertEqual(created["approval_lifecycle"], "approval_not_requested")
        self.assertFalse(created["approval_challenge_created"])
        self.assertEqual(created["next_required_operation"], "approve_change_plan")
        self.assertIsNone(created["approval"]["principal_separation_enforced"])
        self.assertEqual(
            created["approval"]["principal_separation_status"]["reason"],
            "no_decision_recorded",
        )
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertEqual(pending["approval_lifecycle"], "approval_pending_external")
        self.assertTrue(pending["approval_challenge_created"])
        health = self.service.health_summary()
        self.assertEqual(health["plans_with_pending_external_challenge"], 1)
        self.assertEqual(health["pending_challenge_count"], 1)

    async def test_wrong_hash_precedes_approval_and_no_home_assistant_mutation(self):
        created = await self.update_plan()
        with self.assertRaises(GovernanceError) as caught:
            await self.service.apply(created["plan_id"], "0" * 64)
        self.assertEqual(caught.exception.code, ErrorCode.APPROVAL_HASH_MISMATCH)
        self.assertEqual(self.gateway.write_calls, 0)

    async def test_plan_list_is_summary_and_full_plan_remains_retrievable(self):
        created = await self.update_plan()
        summary = self.service.list_plans()["plans"][0]
        self.assertNotIn("proposed_config", summary)
        self.assertNotIn("validation_results", summary)
        self.assertNotIn("events", summary)
        full = self.service.get_plan(created["plan_id"])
        self.assertIn("proposed_config", full)


class SanitizationAcceptanceTests(unittest.TestCase):
    def test_recursive_system_log_secret_families_and_structured_logs(self):
        secrets = {
            "setup_code": "123-45-678",
            "setup_payload": "MT:ABCDEF123",
            "login_flow": "flow-private",
            "access_token": "access-private",
            "refresh_token": "refresh-private",
            "nested": [
                "Authorization: Bearer bearer-private",
                "https://example.invalid/api/webhook/webhook-private?token=query-private",
                "signed https://example.invalid/file?X-Amz-Signature=signed-private",
                "Traceback\nrequest failed at https://user:pass@example.invalid/path?sig=private",
            ],
        }
        result = sanitize_untrusted_data(secrets)
        encoded = json.dumps(result.value)
        for value in (
            "123-45-678", "MT:ABCDEF123", "flow-private", "access-private",
            "refresh-private", "bearer-private", "webhook-private", "query-private",
            "signed-private", "user:pass", "?sig=private",
        ):
            self.assertNotIn(value, encoded)
        self.assertTrue(result.redaction_applied)
        self.assertGreaterEqual(result.redacted_field_count, 8)

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger = logging.getLogger("rc2dev4.sanitization")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        logger.info("Authorization: Bearer bearer-private")
        self.assertNotIn("bearer-private", stream.getvalue())


class DashboardOutcomeAndFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        METRICS.reset()

    async def test_dashboard_not_found_is_domain_outcome_and_health_remains_available(self):
        transport = ReviewedFakeTransport()
        transport.payload = {
            "success": False,
            "error": {"code": "RESOURCE_NOT_FOUND", "message": "bounded"},
        }
        provider = UpstreamDashboardProvider()
        provider.configure(dashboard_settings(), transport=transport)
        with self.assertRaises(DashboardProviderError) as caught:
            await provider.get_dashboard_config(
                url_path="missing-dashboard", force_reload=True, response_limit=60_000
            )
        self.assertEqual(caught.exception.code, ErrorCode.DASHBOARD_NOT_FOUND)
        health = provider.health_snapshot()
        self.assertEqual(health["operational_status"], "available")
        self.assertNotEqual(health["session_state"], "failed")
        self.assertEqual(health["failure_counts"]["connection_failed"], 0)

    async def test_cached_reachability_ages_to_unknown(self):
        provider = UpstreamDashboardProvider()
        provider.configure(dashboard_settings(), transport=ReviewedFakeTransport())
        await provider.refresh_capabilities()
        self.assertEqual(provider.health_snapshot()["operational_status"], "available")
        provider._state.reachability_checked_at = "2000-01-01T00:00:00Z"
        health = provider.health_snapshot()
        self.assertEqual(health["operational_status"], "unknown")
        self.assertIsNone(health["reachable"])

    async def test_upstream_interruption_is_isolated_and_reconnects_without_fallback(self):
        provider = UpstreamDashboardProvider()
        provider.configure(dashboard_settings(), transport=ReviewedFakeTransport())
        await provider.refresh_capabilities()
        expected_fingerprint = provider.health_snapshot()["required_schema_fingerprint"]

        class InterruptedTransport:
            async def discover(inner):
                raise DashboardTransportError("connection_failed")

            async def execute_dashboard_read(inner, arguments, validator):
                raise DashboardTransportError("connection_failed")

        provider._transport = InterruptedTransport()
        with self.assertRaises(DashboardProviderError) as caught:
            await provider.list_dashboards(limit=10, response_limit=60_000)
        self.assertEqual(
            caught.exception.code,
            ErrorCode.UPSTREAM_DASHBOARD_CONNECTION_FAILED,
        )
        self.assertEqual(provider.health_snapshot()["operational_status"], "unavailable")

        provider._transport = ReviewedFakeTransport()
        result = await provider.list_dashboards(limit=10, response_limit=60_000)
        self.assertEqual(result.data["count"], 0)
        restored = provider.health_snapshot()
        self.assertEqual(restored["operational_status"], "available")
        self.assertEqual(restored["required_schema_fingerprint"], expected_fingerprint)
        self.assertEqual(restored["reconnect_count"], 1)
        routing = METRICS.snapshot()["provider_routing"]
        self.assertEqual(routing["fallback_attempts"], 0)
        self.assertEqual(routing["prohibited_fallback_attempts"], 0)


class LegacyDispatchBarrierTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_legacy_write_schemas_reject_before_any_provider_action(self):
        METRICS.reset()
        with patch.object(compatibility, "rest", new=AsyncMock()) as direct:
            payloads = [
                json.loads(await compatibility.upsert_automation("a", "{}")),
                json.loads(await compatibility.delete_automation("a", True)),
                json.loads(await compatibility.call_service("light", "turn_on", "{}", True)),
                json.loads(await compatibility.reload_domain("automation")),
            ]
        direct.assert_not_awaited()
        self.assertEqual(
            [item["error_code"] for item in payloads],
            ["provider_prohibited", "provider_prohibited", "provider_unavailable", "provider_unavailable"],
        )
        routing = METRICS.snapshot()["provider_routing"]
        self.assertEqual(routing["fallback_attempts"], 0)
        self.assertEqual(routing["prohibited_fallback_attempts"], 0)


class ReliabilityDeduplicationAcceptanceTests(unittest.TestCase):
    def test_one_entity_state_cause_groups_all_configuration_references(self):
        references = [
            {
                "entity_id": "sensor.shared_dependency",
                "status": "unavailable",
                "relation": "condition",
                "config_path": "$.condition[0].entity_id",
            },
            {
                "entity_id": "sensor.shared_dependency",
                "status": "unavailable",
                "relation": "action",
                "config_path": "$.action[0].target.entity_id",
            },
        ]
        findings = evaluate_rules(reliability_bundle(references=references))
        matching = [
            item for item in findings
            if item.rule_id == "unavailable_referenced_entity"
        ]
        groups = build_root_cause_groups(matching)
        self.assertEqual(len(matching), 2)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].affected_dependency, "sensor.shared_dependency")
        self.assertEqual(groups[0].affected_reference_count, 2)

    def test_button_unknown_state_is_not_automatically_degraded(self):
        findings = evaluate_rules(reliability_bundle(references=[{
            "entity_id": "button.refresh_device",
            "status": "unknown",
            "relation": "action",
            "config_path": "$.action[0].target.entity_id",
        }]))
        self.assertNotIn("unknown_referenced_entity", {
            item.rule_id for item in findings
        })

    def test_intentional_unavailable_trigger_is_information_not_duplicate_defect(self):
        config = {
            "alias": "Recovery Trigger",
            "mode": "single",
            "trigger": [{
                "platform": "state",
                "entity_id": "sensor.recovery_target",
                "to": "unavailable",
            }],
            "condition": [],
            "action": [],
        }
        findings = evaluate_rules(reliability_bundle(
            config=config,
            references=[{
                "entity_id": "sensor.recovery_target",
                "status": "unavailable",
                "relation": "trigger",
                "config_path": "$.trigger[0].entity_id",
            }],
        ))
        intentional = [
            item for item in findings
            if item.rule_id == "intentional_unavailable_state_trigger"
        ]
        self.assertEqual(len(intentional), 1)
        self.assertEqual(intentional[0].severity, "info")
        self.assertIn("remains unavailable", intentional[0].explanation)
        self.assertNotIn("unavailable_referenced_entity", {
            item.rule_id for item in findings
        })


if __name__ == "__main__":
    unittest.main()
