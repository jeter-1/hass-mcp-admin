"""Focused RC2dev5 regressions for live-acceptance corrections."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_automation_reliability_analysis import (
    FakeProvider as ReliabilityProvider,
    bundle as reliability_bundle,
)
from tests.test_beta_observability import settings as application_settings
from tests.test_entity_dependency_analysis import FakeProvider, TARGET, finding, scan
from tests.test_governance import GovernanceTestCase
from tests.test_rc3a_reviewed_contract import ReviewedFakeTransport, settings as dashboard_settings

from ha_mcp_engineering.dependency.index import DependencyIndex
from ha_mcp_engineering.dependency.runtime import DependencyAnalysisRuntime
from ha_mcp_engineering.dependency.service import EntityDependencyAnalysisService
from ha_mcp_engineering.errors import (
    AutomationNotFoundError,
    DashboardProviderError,
    EntityNotFoundError,
    ErrorCode,
    GovernanceError,
)
from ha_mcp_engineering.application import validate_settings
from ha_mcp_engineering.configuration import load_settings
from ha_mcp_engineering.errors import ConfigurationError
from ha_mcp_engineering.observability import METRICS
from ha_mcp_engineering.providers.upstream_dashboard import UpstreamDashboardProvider
from ha_mcp_engineering.providers.dispatch import CanonicalProviderDispatcher
from ha_mcp_engineering.reliability.service import AutomationReliabilityAnalysisService
from ha_mcp_engineering.sanitization import sanitize_untrusted_data


FIXTURES = Path(__file__).parent / "fixtures"


class DependencyConfigurationTests(unittest.TestCase):
    def test_safe_defaults_and_legacy_settings_remain_valid(self):
        configured = application_settings("/tmp/rc2dev5-audit.jsonl")
        self.assertTrue(configured.prewarm_enabled)
        self.assertEqual(configured.prewarm_startup_delay_seconds, 45)
        self.assertEqual(configured.prewarm_retry_delay_seconds, 300)
        self.assertEqual(configured.dependency_index_soft_ttl_seconds, 600)
        self.assertEqual(configured.dependency_index_hard_ttl_seconds, 3600)
        validate_settings(configured)

    def test_invalid_ttl_and_retry_values_fail_clearly(self):
        configured = application_settings("/tmp/rc2dev5-audit.jsonl")
        for changed in (
            replace(configured, dependency_index_soft_ttl_seconds=0),
            replace(
                configured,
                dependency_index_soft_ttl_seconds=600,
                dependency_index_hard_ttl_seconds=600,
            ),
            replace(configured, prewarm_retry_delay_seconds=299),
            replace(configured, prewarm_startup_delay_seconds=-1),
        ):
            with self.subTest(changed=changed), self.assertRaises(ConfigurationError):
                validate_settings(changed)

    def test_rc2dev4_options_without_new_keys_receive_rc2dev5_defaults(self):
        with (
            patch(
                "ha_mcp_engineering.configuration._read_options",
                return_value={
                    "dependency_index_prewarm": False,
                    "access_secret": "fixture-access-secret-1234567890",
                },
            ),
            patch.dict("os.environ", {"SUPERVISOR_TOKEN": "fixture-ha-token"}),
        ):
            configured = load_settings()
        self.assertFalse(configured.dependency_index_prewarm)
        self.assertTrue(configured.prewarm_enabled)
        self.assertEqual(configured.prewarm_startup_delay_seconds, 45)
        self.assertEqual(configured.prewarm_retry_delay_seconds, 300)
        self.assertEqual(configured.dependency_index_soft_ttl_seconds, 600)
        self.assertEqual(configured.dependency_index_hard_ttl_seconds, 3600)
        validate_settings(configured)


class GovernanceCompatibilityTests(GovernanceTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        METRICS.reset()

    async def test_approval_lifecycle_is_authoritative_and_legacy_status_is_marked(self):
        plan = await self.update_plan()
        self.assertEqual(plan["approval_lifecycle"], "approval_not_requested")
        self.assertTrue(plan["status_is_legacy"])
        self.assertEqual(
            plan["authoritative_lifecycle_field"], "approval_lifecycle"
        )

    async def test_missing_plan_is_a_domain_outcome(self):
        with self.assertRaises(GovernanceError) as caught:
            self.service.get_plan("missing-plan")
        self.assertEqual(caught.exception.code, ErrorCode.CHANGE_PLAN_NOT_FOUND)
        metrics = METRICS.snapshot()
        self.assertEqual(metrics["domain_outcome_counts"]["change_plan_not_found"], 1)


class ControlledProvider(FakeProvider):
    def __init__(self):
        super().__init__(scan([
            finding(),
            finding(path="$.condition[1].entity_id", source_id="auto-2"),
        ]))
        self.gate: asyncio.Event | None = None
        self.failure: Exception | None = None

    async def scan(self):
        self.scan_count += 1
        gate = self.gate
        if gate is not None:
            await gate.wait()
        if self.failure is not None:
            raise self.failure
        return copy.deepcopy(self.scan_result)


class DependencyFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        METRICS.reset()
        self.provider = ControlledProvider()
        self.index = DependencyIndex(
            self.provider, soft_ttl_seconds=10, hard_ttl_seconds=60
        )
        self.service = EntityDependencyAnalysisService(self.index)

    async def asyncTearDown(self):
        await self.index.shutdown()

    async def build(self):
        return await self.service.analyze(entity_id=TARGET, limit=1)

    async def test_initial_and_concurrent_callers_share_one_generation(self):
        gate = asyncio.Event()
        self.provider.gate = gate
        callers = [asyncio.create_task(self.build()) for _ in range(3)]
        for _ in range(10):
            await asyncio.sleep(0)
            if self.provider.scan_count:
                break
        self.assertEqual(self.provider.scan_count, 1)
        gate.set()
        results = await asyncio.gather(*callers)
        identities = {
            (item.data["index"]["generation"], item.data["index"]["fingerprint"])
            for item in results
        }
        self.assertEqual(len(identities), 1)
        self.assertEqual(self.index.generation, 1)

    async def test_valid_cache_is_zero_io_and_starts_no_refresh(self):
        await self.build()
        count = self.provider.scan_count
        result = await self.build()
        self.assertEqual(self.provider.scan_count, count)
        self.assertTrue(result.data["index"]["cache_hit"])
        self.assertEqual(result.data["index"]["freshness"], "current")
        self.assertFalse(result.data["index"]["background_refresh_active"])

    async def test_soft_expiration_serves_immediately_and_refreshes_once(self):
        await self.build()
        generation = self.index.generation
        old_fingerprint = self.index.snapshot.fingerprint
        self.index.snapshot.built_at_monotonic -= 11
        gate = asyncio.Event()
        self.provider.gate = gate
        started = time.perf_counter()
        values = await asyncio.gather(*(self.build() for _ in range(5)))
        foreground_ms = (time.perf_counter() - started) * 1000
        self.assertLess(foreground_ms, 1000)
        self.assertEqual(self.provider.scan_count, 2)
        self.assertTrue(all(item.data["index"]["evidence_stale"] for item in values))
        self.assertEqual(self.index.generation, generation)
        self.assertEqual(self.index.snapshot.fingerprint, old_fingerprint)
        self.assertEqual(self.index.health()["build_state"], "stale_refreshing")
        gate.set()
        await asyncio.shield(self.index._build_task)
        self.assertEqual(self.index.generation, generation + 1)
        old_cursor = values[0].data["pagination"]["next_cursor"]
        self.assertTrue(old_cursor)
        with self.assertRaises(GovernanceError) as stale:
            await self.service.analyze(
                entity_id=TARGET,
                cursor=old_cursor,
            )
        self.assertEqual(stale.exception.code, ErrorCode.STALE_CURSOR)

    async def test_failed_background_refresh_preserves_last_good_until_hard_ttl(self):
        await self.build()
        generation = self.index.generation
        fingerprint = self.index.snapshot.fingerprint
        self.index.snapshot.built_at_monotonic -= 11
        self.provider.failure = RuntimeError("synthetic refresh failure")
        result = await self.build()
        self.assertTrue(result.data["index"]["evidence_stale"])
        await asyncio.sleep(0)
        await asyncio.gather(self.index._build_task, return_exceptions=True)
        health = self.index.health()
        self.assertEqual(health["build_state"], "refresh_failed_stale_available")
        self.assertEqual(self.index.generation, generation)
        self.assertEqual(self.index.snapshot.fingerprint, fingerprint)
        self.index.snapshot.built_at_monotonic -= 60
        with self.assertRaises(RuntimeError):
            await self.index.get()
        self.assertEqual(self.index.health()["freshness"], "hard_expired")

    async def test_explicit_refresh_and_invalidation_are_single_flight(self):
        await self.build()
        gate = asyncio.Event()
        self.provider.gate = gate
        first = asyncio.create_task(self.index.get(refresh=True))
        second = asyncio.create_task(self.index.get(refresh=True))
        for _ in range(10):
            await asyncio.sleep(0)
            if self.provider.scan_count == 2:
                break
        self.assertEqual(self.provider.scan_count, 2)
        gate.set()
        await asyncio.gather(first, second)
        self.assertEqual(self.index.generation, 2)
        self.index.invalidate("configuration_changed")
        self.provider.gate = None
        await self.index.get()
        self.assertEqual(self.index.generation, 3)
        self.assertFalse(self.index.invalidated)

    async def test_waiter_cancellation_does_not_cancel_shared_build_and_shutdown_cleans(self):
        gate = asyncio.Event()
        self.provider.gate = gate
        cancelled = asyncio.create_task(self.index.get())
        surviving = asyncio.create_task(self.index.get())
        await asyncio.sleep(0)
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        self.assertFalse(self.index._build_task.cancelled())
        gate.set()
        snapshot, _, _ = await surviving
        self.assertEqual(snapshot.generation, 1)

    async def test_cancelled_manager_build_wakes_waiters_and_can_be_retried(self):
        gate = asyncio.Event()
        self.provider.gate = gate
        first = asyncio.create_task(self.index.get())
        second = asyncio.create_task(self.index.get())
        for _ in range(10):
            await asyncio.sleep(0)
            if self.provider.scan_count:
                break
        self.index._build_task.cancel()
        results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertTrue(all(isinstance(item, asyncio.CancelledError) for item in results))
        self.assertNotEqual(self.index.health()["build_state"], "building")

        self.provider.gate = None
        snapshot, rebuilt, _ = await self.index.get()
        self.assertTrue(rebuilt)
        self.assertEqual(snapshot.generation, 1)


class PrewarmRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_on_demand_build_during_delay_prevents_duplicate_prewarm(self):
        provider = ControlledProvider()
        provider.rest_client = SimpleNamespace(request=AsyncMock(return_value={}))
        index = DependencyIndex(provider, soft_ttl_seconds=10, hard_ttl_seconds=60)
        runtime = DependencyAnalysisRuntime(
            service=EntityDependencyAnalysisService(index)
        )
        runtime.start_prewarm(
            startup_delay_seconds=0.04,
            retry_delay_seconds=300,
        )
        await index.get()
        await asyncio.sleep(0.08)
        self.assertEqual(provider.scan_count, 1)
        self.assertEqual(runtime.health()["prewarm_state"], "complete")
        provider.rest_client.request.assert_not_awaited()
        await runtime.shutdown()

    async def test_prewarm_is_delayed_nonblocking_and_does_not_retry_storm(self):
        provider = ControlledProvider()
        provider.rest_client = SimpleNamespace(
            request=AsyncMock(side_effect=RuntimeError("not ready"))
        )
        runtime = DependencyAnalysisRuntime(
            service=EntityDependencyAnalysisService(
                DependencyIndex(provider, soft_ttl_seconds=10, hard_ttl_seconds=60)
            )
        )
        task = runtime.start_prewarm(
            startup_delay_seconds=0.02, retry_delay_seconds=300
        )
        self.assertFalse(task.done())
        self.assertEqual(runtime.health()["prewarm_state"], "scheduled")
        await asyncio.sleep(0.06)
        health = runtime.health()
        self.assertEqual(health["prewarm_attempt_count"], 1)
        self.assertEqual(health["prewarm_failure_category"], "connectivity_not_ready")
        self.assertIsNotNone(health["next_prewarm_retry_at"])
        await runtime.shutdown()
        self.assertTrue(task.done())


class DashboardDomainOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        METRICS.reset()
        self.transport = ReviewedFakeTransport()
        self.provider = UpstreamDashboardProvider()
        self.provider.configure(dashboard_settings(), transport=self.transport)
        await self.provider.refresh_capabilities()

    async def test_exact_713_envelope_is_not_found_without_health_degradation(self):
        fixture = json.loads(
            (FIXTURES / "ha_mcp_7_13_dashboard_not_found.json").read_text(encoding="utf-8")
        )
        self.transport.payload = fixture
        before = METRICS.snapshot()["provider_routing"]["provider_operational_failures"]
        with self.assertRaises(DashboardProviderError) as caught:
            await self.provider.get_dashboard_config(
                url_path="missing-dashboard", force_reload=True, response_limit=60_000
            )
        self.assertEqual(caught.exception.code, ErrorCode.DASHBOARD_NOT_FOUND)
        self.assertFalse(caught.exception.retryable)
        health = self.provider.health_snapshot()
        self.assertEqual(health["session_state"], "idle")
        self.assertEqual(health["operational_status"], "available")
        self.assertEqual(health["contract_status"], "valid")
        self.assertEqual(health["failure_counts"]["dashboard_not_found"], 1)
        self.assertEqual(health["failure_counts"]["upstream_error"], 0)
        after = METRICS.snapshot()["provider_routing"]["provider_operational_failures"]
        self.assertEqual(after, before)
        self.transport.payload = {"dashboards": []}
        valid = await self.provider.list_dashboards(limit=10, response_limit=60_000)
        self.assertEqual(valid.data["count"], 0)

    async def test_similar_generic_error_is_not_misclassified(self):
        self.transport.payload = {
            "success": False,
            "error": {
                "code": "SERVICE_CALL_FAILED",
                "message": "Wrapper mentions Unknown config specified: missing-dashboard",
            },
            "action": "get",
            "url_path": "missing-dashboard",
        }
        with self.assertRaises(DashboardProviderError) as caught:
            await self.provider.get_dashboard_config(
                url_path="missing-dashboard", force_reload=True, response_limit=60_000
            )
        self.assertEqual(caught.exception.code, ErrorCode.UPSTREAM_DASHBOARD_UPSTREAM_ERROR)
        self.assertEqual(self.provider.health_snapshot()["session_state"], "failed")


class DirectDomainOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        METRICS.reset()
        self.dispatcher = CanonicalProviderDispatcher()

    async def test_expected_not_found_source_coverage_is_domain_classified(self):
        for tool_name, error, expected in (
            ("get_entity", EntityNotFoundError(), "domain_outcome_entity_not_found"),
            (
                "get_automation_config",
                AutomationNotFoundError(),
                "domain_outcome_automation_not_found",
            ),
        ):
            async def action(value=error):
                raise value

            payload = json.loads(await self.dispatcher.execute(
                tool_name,
                action,
                arguments={},
                response_limit=60_000,
            ))
            self.assertFalse(payload["success"])
            self.assertFalse(payload["retryable"])
            coverage = payload["metadata"]["source_coverage"][0]
            self.assertEqual(coverage["failure_category"], expected)
            self.assertFalse(coverage["fallback_occurred"])
        routing = METRICS.snapshot()["provider_routing"]
        self.assertEqual(
            routing["provider_operational_failures"].get("direct_ha_api", 0), 0
        )


class ReliabilitySummaryTests(unittest.IsolatedAsyncioTestCase):
    def make_bundle(self):
        references = [
            {
                "entity_id": "sensor.shared_unavailable",
                "status": "unavailable",
                "relation": "condition",
                "config_path": f"$.condition[{index}].entity_id",
            }
            for index in range(9)
        ]
        references.append({
            "entity_id": "sensor.recovery_target",
            "status": "unavailable",
            "relation": "trigger",
            "config_path": "$.trigger[0].entity_id",
        })
        config = {
            "alias": "Summary",
            "mode": "single",
            "trigger": [{
                "platform": "state", "entity_id": "sensor.recovery_target", "to": "unavailable"
            }],
            "condition": [],
            "action": [],
        }
        return reliability_bundle(references=references, config=config)

    async def test_summary_returns_one_group_and_bounded_intentional_note(self):
        instant = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
        summary_service = AutomationReliabilityAnalysisService(
            ReliabilityProvider(self.make_bundle()), clock=lambda: instant
        )
        summary = await summary_service.analyze(
            automation_id="reliability_test", detail_level="summary", limit=20
        )
        self.assertEqual(summary.data["unique_root_cause_count"], 1)
        self.assertEqual(len(summary.data["root_cause_groups"]), 1)
        group = summary.data["root_cause_groups"][0]
        self.assertEqual(group["reference_count"], 9)
        self.assertLessEqual(len(group["representative_configuration_paths"]), 3)
        self.assertEqual(len(summary.data["findings"]), 1)
        self.assertEqual(summary.data["findings"][0]["status"], "intentional")

        standard_service = AutomationReliabilityAnalysisService(
            ReliabilityProvider(self.make_bundle()), clock=lambda: instant
        )
        standard = await standard_service.analyze(
            automation_id="reliability_test", detail_level="standard", limit=20
        )
        self.assertLess(
            len(json.dumps(summary.data, sort_keys=True)),
            len(json.dumps(standard.data, sort_keys=True)),
        )


class WebhookSanitizationTests(unittest.TestCase):
    def test_webhook_identifiers_are_redacted_without_hiding_git_sha(self):
        identifier = "webhookIdentifier_0123456789abcdef"
        git_sha = "4c4b673f2234924e3dbaeae46bcd224e179603d2"
        value = (
            f"Received remote request for local webhook {identifier}; "
            f"path=/api/webhook/{identifier}; revision={git_sha}"
        )
        result = sanitize_untrusted_data(value)
        self.assertNotIn(identifier, result.value)
        self.assertIn(git_sha, result.value)
        self.assertTrue(result.redaction_applied)
        self.assertIn("webhook_identifier", result.redaction_categories)
        self.assertGreaterEqual(result.redacted_field_count, 1)


if __name__ == "__main__":
    unittest.main()
