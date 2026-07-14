import asyncio
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.errors import (  # noqa: E402
    ErrorCode,
    GovernanceError,
    HomeAssistantUnavailableError,
    InvalidRequestError,
)
from ha_mcp_engineering.handoff.models import (  # noqa: E402
    HandoffEvidenceBundle,
    HandoffEvidenceReference,
    HandoffItem,
)
from ha_mcp_engineering.handoff.service import HandoffGenerationService  # noqa: E402
from ha_mcp_engineering.dependency.service import EntityDependencyAnalysisService  # noqa: E402
from ha_mcp_engineering.incident.models import IncidentSourceCoverage  # noqa: E402
from ha_mcp_engineering.incident.service import IncidentCorrelationService  # noqa: E402
from ha_mcp_engineering.impact.service import ChangeImpactAnalysisService  # noqa: E402
from ha_mcp_engineering.integrity.service import (  # noqa: E402
    ConfigurationIntegrityAnalysisService,
)
from ha_mcp_engineering.observability import METRICS, RuntimeMetrics  # noqa: E402
from ha_mcp_engineering.providers import (  # noqa: E402
    EvidenceRequest,
    EvidenceRouter,
    ProviderCapability,
    ProviderCompleteness,
    ProviderCoverage,
    ProviderError,
    ProviderFailureCategory,
    ProviderResult,
)
from ha_mcp_engineering.providers.dispatch import CanonicalProviderDispatcher  # noqa: E402
from ha_mcp_engineering.reliability.service import (  # noqa: E402
    AutomationReliabilityAnalysisService,
)


def request(**overrides):
    values = {
        "handoff_type": "system_status",
        "title": "",
        "focus_entity_ids": [],
        "automation_ids": [],
        "change_plan_ids": [],
        "lookback_hours": 168,
        "include_runtime_health": True,
        "include_governance_context": True,
        "include_dependency_context": True,
        "include_integrity_context": True,
        "include_reliability_context": True,
        "include_incident_context": True,
        "include_recommendations": True,
        "detail_level": "standard",
        "output_format": "structured",
        "limit": 20,
        "cursor": "",
        "refresh_index": False,
    }
    values.update(overrides)
    return values


class Provider:
    provider_id = "engineering"

    def __init__(self, *, failed=False, timeout=False, items=1, partial=False):
        self.failed = failed
        self.timeout = timeout
        self.items = items
        self.partial = partial
        self.calls = 0
        self.index = type(
            "Index",
            (),
            {"health": lambda self: {"valid": True, "generation": 1, "fingerprint": "index-1"}},
        )()

    async def fetch(self, request_value):
        self.calls += 1
        if self.timeout:
            raise asyncio.TimeoutError
        if self.failed:
            return ProviderResult(
                "engineering",
                ProviderCapability.HANDOFF_GENERATION,
                ProviderCompleteness.FAILED,
                failure=ProviderError(
                    ProviderFailureCategory.UPSTREAM_ERROR,
                    "The provider failed.",
                ),
            )
        evidence = {}
        items = []
        for index in range(self.items):
            reference_id = f"ev-{index}"
            evidence[reference_id] = HandoffEvidenceReference(
                reference_id, "test", f"source-{index}", "Bounded evidence."
            )
            items.append(
                HandoffItem(
                    f"item-{index}",
                    "current_state",
                    "fact",
                    f"Item {index}",
                    "Bounded fact.",
                    "current",
                    "info",
                    "confirmed",
                    supporting_evidence_reference_ids=(reference_id,),
                )
            )
        coverage = [
            IncidentSourceCoverage(
                "test",
                "engineering",
                "handoff_generation",
                "partial" if self.partial else "complete",
                True,
                True,
                len(items),
                0,
                ["Bounded coverage limitation."] if self.partial else [],
                1.0,
                False,
                None,
                False,
                ["bounded_test_limitation"] if self.partial else [],
            )
        ]
        return ProviderResult(
            "engineering",
            ProviderCapability.HANDOFF_GENERATION,
            ProviderCompleteness.PARTIAL if self.partial else ProviderCompleteness.COMPLETE,
            coverage=ProviderCoverage(1, 0 if self.partial else 1),
            data=HandoffEvidenceBundle(
                scope={
                    "focus_entity_ids": [],
                    "automation_ids": [],
                    "automation_entity_ids": [],
                    "change_plan_ids": [],
                    "lookback_hours": 168,
                    "contexts_requested": [],
                },
                items=items,
                evidence=evidence,
                coverage=coverage,
                index={
                    "requested": True,
                    "generation": 1,
                    "fingerprint": "index-1",
                    "cache_hit": True,
                },
            ),
        )

    def active_index_identity(self):
        return self.index.health()


class Beta23ProviderAccountingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fresh = RuntimeMetrics()
        METRICS.__dict__.clear()
        METRICS.__dict__.update(fresh.__dict__)

    def provider_metrics(self):
        return METRICS.snapshot()["provider_routing"]

    def test_selected_provider_without_dispatch_is_fail_closed(self):
        METRICS.record_provider_result(
            "engineering", "failed", dispatched=False
        )
        self.assertEqual(self.provider_metrics()["requests_by_provider"], {})
        self.assertEqual(self.provider_metrics()["failures_by_provider"], {})

    async def test_handoff_validation_is_not_a_provider_attempt(self):
        provider = Provider()
        service = HandoffGenerationService(provider)
        before = self.provider_metrics()
        with self.assertRaises(InvalidRequestError):
            await service.generate(**request(handoff_type="focused_review"))
        after = self.provider_metrics()
        self.assertEqual(provider.calls, 0)
        self.assertEqual(before["requests_by_provider"], after["requests_by_provider"])
        self.assertEqual(before["successful_requests_by_provider"], after["successful_requests_by_provider"])
        self.assertEqual(before["failures_by_provider"], after["failures_by_provider"])
        self.assertEqual(METRICS.snapshot()["handoff_generation"]["source_failures"], 0)

    async def test_analysis_validation_paths_do_not_dispatch_selected_provider(self):
        provider = Provider()

        class Index:
            async def get(self, refresh=False):
                raise AssertionError("invalid dependency input must not read index")

        operations = (
            IncidentCorrelationService(provider).analyze(
                focus_entity_id="", automation_id="", related_entity_ids=[],
                lookback_hours=24, correlation_window_minutes=10, trace_limit=10,
                include_dependency_context=True, include_integrity_context=True,
                include_reliability_context=True, detail_level="standard", limit=20,
                cursor="", refresh_index=False,
            ),
            ConfigurationIntegrityAnalysisService(provider).analyze(
                cursor="opaque", refresh_index=True
            ),
            ChangeImpactAnalysisService(provider).analyze(
                entity_id="sensor.example", operation="rename_entity",
                replacement_entity_id=None,
            ),
            AutomationReliabilityAnalysisService(provider).analyze(automation_id=""),
            EntityDependencyAnalysisService(Index()).analyze(entity_id="../config"),
        )
        for operation in operations:
            with self.assertRaises((InvalidRequestError, GovernanceError)):
                await operation
        self.assertEqual(provider.calls, 0)
        self.assertEqual(self.provider_metrics()["requests_by_provider"], {})
        self.assertEqual(self.provider_metrics()["failures_by_provider"], {})

    async def test_handoff_cursor_validation_and_snapshot_continuation_do_no_provider_work(self):
        provider = Provider(items=3)
        service = HandoffGenerationService(provider, cursor_key=b"b" * 32)
        first = await service.generate(**request(limit=1))
        baseline = self.provider_metrics()
        second = await service.generate(
            **request(limit=1, cursor=first.data["pagination"]["next_cursor"])
        )
        self.assertEqual(provider.calls, 1)
        self.assertEqual(baseline, self.provider_metrics())
        with self.assertRaises(GovernanceError) as error:
            await service.generate(
                **request(
                    limit=1,
                    cursor=second.data["pagination"]["next_cursor"] + "tampered",
                )
            )
        self.assertEqual(error.exception.code, ErrorCode.INVALID_CURSOR)
        self.assertEqual(baseline, self.provider_metrics())

    async def test_actual_engineering_failure_and_timeout_are_counted_once(self):
        for provider, expected_error in (
            (Provider(failed=True), ErrorCode.ANALYSIS_UNAVAILABLE),
            (Provider(timeout=True), ErrorCode.PROVIDER_TIMEOUT),
        ):
            fresh = RuntimeMetrics()
            METRICS.__dict__.clear()
            METRICS.__dict__.update(fresh.__dict__)
            with self.assertRaises(GovernanceError) as error:
                await HandoffGenerationService(provider).generate(**request())
            self.assertEqual(error.exception.code, expected_error)
            metrics = self.provider_metrics()
            self.assertEqual(metrics["requests_by_provider"]["engineering"], 1)
            self.assertEqual(metrics["failures_by_provider"]["engineering"], 1)
            self.assertEqual(metrics["successful_requests_by_provider"].get("engineering", 0), 0)

    async def test_success_and_partial_are_dispatched_nonfailing_operations(self):
        for partial in (False, True):
            fresh = RuntimeMetrics()
            METRICS.__dict__.clear()
            METRICS.__dict__.update(fresh.__dict__)
            output = await HandoffGenerationService(Provider(partial=partial)).generate(
                **request()
            )
            self.assertEqual(output.partial, partial)
            metrics = self.provider_metrics()
            self.assertEqual(metrics["requests_by_provider"]["engineering"], 1)
            self.assertEqual(metrics["successful_requests_by_provider"]["engineering"], 1)
            self.assertEqual(metrics["failures_by_provider"].get("engineering", 0), 0)
            self.assertEqual(metrics["partial_results"], int(partial))

    async def test_direct_validation_is_not_a_provider_attempt(self):
        dispatcher = CanonicalProviderDispatcher()

        async def invalid():
            raise InvalidRequestError(details={"field": "entity_id"})

        payload = await dispatcher.execute(
            "get_entity", invalid, arguments={"entity_id": "../config"}, response_limit=8192
        )
        self.assertEqual(json.loads(payload)["error_code"], "invalid_request")
        self.assertEqual(self.provider_metrics()["requests_by_provider"], {})
        self.assertEqual(self.provider_metrics()["failures_by_provider"], {})

    async def test_actual_direct_failure_is_counted_once(self):
        dispatcher = CanonicalProviderDispatcher()

        async def unavailable():
            raise HomeAssistantUnavailableError()

        payload = await dispatcher.execute(
            "get_entity", unavailable, arguments={"entity_id": "sensor.example"}, response_limit=8192
        )
        self.assertEqual(
            json.loads(payload)["error_code"], "home_assistant_unavailable"
        )
        metrics = self.provider_metrics()
        self.assertEqual(metrics["requests_by_provider"]["direct_ha_api"], 1)
        self.assertEqual(metrics["failures_by_provider"]["direct_ha_api"], 1)
        self.assertEqual(metrics["failures_by_provider"].get("engineering", 0), 0)

    async def test_unavailable_selected_provider_without_dispatch_is_not_counted(self):
        router = EvidenceRouter([])
        result = await router.fetch(
            EvidenceRequest(ProviderCapability.CURRENT_ENTITY_STATE)
        )
        self.assertFalse(result.succeeded)
        self.assertEqual(self.provider_metrics()["requests_by_provider"], {})
        self.assertEqual(self.provider_metrics()["failures_by_provider"], {})

    async def test_router_attributes_real_failure_and_timeout_once(self):
        class FailingProvider:
            provider_id = "engineering"

            def __init__(self, error):
                self.error = error

            async def fetch(self, request_value):
                raise self.error

        for error, category in (
            (RuntimeError("failed"), ProviderFailureCategory.UPSTREAM_ERROR),
            (asyncio.TimeoutError(), ProviderFailureCategory.TIMEOUT),
        ):
            fresh = RuntimeMetrics()
            METRICS.__dict__.clear()
            METRICS.__dict__.update(fresh.__dict__)
            result = await EvidenceRouter([FailingProvider(error)]).fetch(
                EvidenceRequest(ProviderCapability.HANDOFF_GENERATION)
            )
            self.assertFalse(result.succeeded)
            self.assertEqual(result.failure.category, category)
            metrics = self.provider_metrics()
            self.assertEqual(metrics["requests_by_provider"]["engineering"], 1)
            self.assertEqual(metrics["failures_by_provider"]["engineering"], 1)


if __name__ == "__main__":
    unittest.main()
