import asyncio
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.facilitation import (  # noqa: E402
    DetailLevel,
    EvidenceReference,
    SourceCoverage,
    bounded_result,
)
from ha_mcp_engineering.observability import METRICS  # noqa: E402
from ha_mcp_engineering.providers import (  # noqa: E402
    CapabilityRoute,
    DirectHaApiProvider,
    EngineeringEvidenceProvider,
    EvidenceRequest,
    EvidenceRouter,
    ProviderCapability,
    ProviderCompleteness,
    ProviderCoverage,
    ProviderError,
    ProviderFailureCategory,
    ProviderResult,
    RoutingPolicy,
    StandardHaMcpGateway,
    TOOL_CAPABILITY_POLICY,
    routing_for_tool,
)
from ha_mcp_engineering.capabilities import BETA_NATIVE_CAPABILITIES, CAPABILITIES  # noqa: E402


class FakeProvider(EngineeringEvidenceProvider):
    def __init__(self, provider_id, result):
        self.provider_id = provider_id
        self.capabilities = frozenset(ProviderCapability)
        self.result = result

    @property
    def available(self):
        return self.result.completeness != ProviderCompleteness.UNAVAILABLE

    async def fetch(self, request):
        self.result.capability = request.capability
        return self.result


def result(provider, capability, completeness=ProviderCompleteness.COMPLETE, **kwargs):
    coverage = kwargs.pop(
        "coverage",
        ProviderCoverage(1, 1 if completeness == ProviderCompleteness.COMPLETE else 0),
    )
    return ProviderResult(
        provider_id=provider,
        capability=capability,
        completeness=completeness,
        coverage=coverage,
        **kwargs,
    )


class RoutingPolicyTests(unittest.TestCase):
    def setUp(self):
        METRICS.reset()
        self.policy = RoutingPolicy()

    def test_capabilities_map_to_expected_policy(self):
        expected = {
            ProviderCapability.GOVERNANCE_PERSISTENCE: CapabilityRoute.ENGINEERING_NATIVE,
            ProviderCapability.RISK_ASSESSMENT: CapabilityRoute.ENGINEERING_NATIVE,
            ProviderCapability.DEPENDENCY_ANALYSIS: CapabilityRoute.ENGINEERING_NATIVE,
            ProviderCapability.RELIABILITY_ANALYSIS: CapabilityRoute.ENGINEERING_NATIVE,
            ProviderCapability.IMPACT_ANALYSIS: CapabilityRoute.ENGINEERING_NATIVE,
            ProviderCapability.AUDIT: CapabilityRoute.ENGINEERING_NATIVE,
            ProviderCapability.HANDOFF_GENERATION: CapabilityRoute.ENGINEERING_NATIVE,
            ProviderCapability.DASHBOARD_INVENTORY: CapabilityRoute.UPSTREAM_DASHBOARD,
            ProviderCapability.DASHBOARD_CONFIGURATION_EVIDENCE: CapabilityRoute.UPSTREAM_DASHBOARD,
            ProviderCapability.CURRENT_ENTITY_STATE: CapabilityRoute.TRANSITIONAL_DIRECT,
            ProviderCapability.BROAD_ENTITY_SEARCH: CapabilityRoute.TRANSITIONAL_DIRECT,
            ProviderCapability.AREA_LOOKUP: CapabilityRoute.TRANSITIONAL_DIRECT,
            ProviderCapability.SERVICE_DISCOVERY: CapabilityRoute.TRANSITIONAL_DIRECT,
            ProviderCapability.ORDINARY_SERVICE_EXECUTION: CapabilityRoute.STANDARD_MCP_PREFERRED,
            ProviderCapability.AUTOMATION_CONFIG: CapabilityRoute.DIRECT_HA_REQUIRED,
            ProviderCapability.AUTOMATION_TRACE: CapabilityRoute.DIRECT_HA_REQUIRED,
            ProviderCapability.BLUEPRINT_SOURCE: CapabilityRoute.DIRECT_HA_REQUIRED,
            ProviderCapability.CONFIG_VALIDATION: CapabilityRoute.DIRECT_HA_REQUIRED,
            ProviderCapability.GOVERNED_APPLY: CapabilityRoute.DIRECT_HA_REQUIRED,
            ProviderCapability.EXACT_VERIFICATION: CapabilityRoute.DIRECT_HA_REQUIRED,
            ProviderCapability.GOVERNED_ROLLBACK: CapabilityRoute.DIRECT_HA_REQUIRED,
            ProviderCapability.HISTORY_READ: CapabilityRoute.TRANSITIONAL_DIRECT,
            ProviderCapability.LEGACY_AUTOMATION_WRITE: CapabilityRoute.PROHIBITED,
            ProviderCapability.UNGOVERNED_PHYSICAL_ACTION: CapabilityRoute.PROHIBITED,
            ProviderCapability.SECRET_BEARING_DIAGNOSTICS: CapabilityRoute.PROHIBITED,
            ProviderCapability.UNSUPPORTED_EXPERIMENTAL: CapabilityRoute.UNSUPPORTED,
        }
        for capability, route in expected.items():
            with self.subTest(capability=capability):
                self.assertEqual(self.policy.resolve(capability).route, route)

    def test_all_41_tools_have_a_deterministic_routing_policy(self):
        names = {item["tool"] for item in (*CAPABILITIES, *BETA_NATIVE_CAPABILITIES)}
        self.assertEqual(len(names), 41)
        self.assertEqual(set(TOOL_CAPABILITY_POLICY), names)
        self.assertNotIn(
            CapabilityRoute.UNSUPPORTED,
            {routing_for_tool(name).route for name in names},
        )

    def test_dashboard_provider_is_separate_from_standard_mcp(self):
        for capability in (
            ProviderCapability.DASHBOARD_INVENTORY,
            ProviderCapability.DASHBOARD_CONFIGURATION_EVIDENCE,
        ):
            decision = self.policy.resolve(capability)
            self.assertEqual(decision.route, CapabilityRoute.UPSTREAM_DASHBOARD)
            self.assertEqual(decision.preferred_provider, "upstream_dashboard")
            self.assertEqual(decision.fallback_providers, ())
        self.assertFalse(StandardHaMcpGateway().available)

    def test_direct_required_never_routes_through_service_execution(self):
        decision = self.policy.resolve(ProviderCapability.GOVERNED_APPLY)
        self.assertEqual(decision.preferred_provider, "direct_ha_api")
        self.assertEqual(decision.fallback_providers, ())

    def test_unavailable_standard_mcp_is_honest(self):
        gateway = StandardHaMcpGateway()
        self.assertFalse(gateway.available)
        response = asyncio.run(gateway.fetch(EvidenceRequest(ProviderCapability.SERVICE_DISCOVERY)))
        self.assertEqual(response.completeness, ProviderCompleteness.UNAVAILABLE)
        self.assertEqual(response.failure.category, ProviderFailureCategory.UNAVAILABLE)
        self.assertEqual(response.evidence_count, 0)

    def test_prohibited_capability_cannot_fallback(self):
        router = EvidenceRouter([])
        response = asyncio.run(router.fetch(EvidenceRequest(
            ProviderCapability.UNGOVERNED_PHYSICAL_ACTION,
            allow_direct_fallback=True,
        )))
        self.assertEqual(response.failure.category, ProviderFailureCategory.PROHIBITED)
        self.assertFalse(response.fallback_occurred)
        self.assertEqual(METRICS.snapshot()["provider_routing"]["prohibited_fallback_attempts"], 1)

    def test_failed_standard_write_does_not_fallback_to_direct(self):
        unavailable = result(
            "standard_ha_mcp",
            ProviderCapability.ORDINARY_SERVICE_EXECUTION,
            ProviderCompleteness.UNAVAILABLE,
            failure=ProviderError(ProviderFailureCategory.UNAVAILABLE, "Unavailable"),
        )
        direct = result("direct_ha_api", ProviderCapability.ORDINARY_SERVICE_EXECUTION)
        router = EvidenceRouter([FakeProvider("standard_ha_mcp", unavailable), FakeProvider("direct_ha_api", direct)])
        response = asyncio.run(router.fetch(EvidenceRequest(
            ProviderCapability.ORDINARY_SERVICE_EXECUTION,
            allow_direct_fallback=True,
        )))
        self.assertEqual(response.provider_id, "standard_ha_mcp")
        self.assertFalse(response.fallback_occurred)

    def test_exact_entity_read_selects_direct_without_fallback(self):
        direct = result("direct_ha_api", ProviderCapability.CURRENT_ENTITY_STATE)
        router = EvidenceRouter([FakeProvider("direct_ha_api", direct)])
        response = asyncio.run(router.fetch(EvidenceRequest(
            ProviderCapability.CURRENT_ENTITY_STATE,
        )))
        self.assertEqual(response.provider_id, "direct_ha_api")
        self.assertFalse(response.fallback_occurred)
        metrics = METRICS.snapshot()["provider_routing"]
        self.assertEqual(metrics["fallback_attempts"], 0)
        self.assertEqual(metrics["fallback_successes"], 0)

    def test_broad_entity_search_selects_direct_without_fallback(self):
        decision = self.policy.resolve(ProviderCapability.BROAD_ENTITY_SEARCH)
        self.assertEqual(decision.route, CapabilityRoute.TRANSITIONAL_DIRECT)
        self.assertEqual(decision.preferred_provider, "direct_ha_api")
        self.assertEqual(decision.fallback_providers, ())
        self.assertFalse(decision.explicit_direct_fallback_allowed)

    def test_provider_evidence_is_bounded_and_truncation_counted(self):
        refs = [EvidenceReference(f"ref:{index}", "standard_ha_mcp", "state", "Finding") for index in range(5)]
        complete = result(
            "standard_ha_mcp",
            ProviderCapability.ORDINARY_SERVICE_EXECUTION,
            evidence=refs,
        )
        router = EvidenceRouter([FakeProvider("standard_ha_mcp", complete)])
        response = asyncio.run(router.fetch(EvidenceRequest(
            ProviderCapability.ORDINARY_SERVICE_EXECUTION,
            max_evidence=2,
        )))
        self.assertEqual(response.completeness, ProviderCompleteness.PARTIAL)
        self.assertEqual(response.evidence_count, 2)
        self.assertEqual(METRICS.snapshot()["provider_routing"]["evidence_truncation_count"], 1)


class ProviderContractTests(unittest.TestCase):
    def setUp(self):
        METRICS.reset()

    def test_successful_provider_result_is_bounded_and_timed(self):
        async def handler(request):
            return result(
                "placeholder",
                request.capability,
                evidence=[EvidenceReference("entity:1", "direct_ha_api", "state", "One relevant state")],
            )
        provider = DirectHaApiProvider({ProviderCapability.AUTOMATION_CONFIG: handler})
        response = asyncio.run(provider.fetch(EvidenceRequest(ProviderCapability.AUTOMATION_CONFIG)))
        self.assertTrue(response.succeeded)
        self.assertEqual(response.provider_id, "direct_ha_api")
        self.assertGreaterEqual(response.timing_ms, 0)
        self.assertEqual(response.as_dict()["evidence_count"], 1)

    def test_partial_result_remains_visible(self):
        partial = result(
            "direct_ha_api",
            ProviderCapability.DEPENDENCY_ANALYSIS,
            ProviderCompleteness.PARTIAL,
            warnings=["One source unavailable"],
            coverage=ProviderCoverage(2, 1, ("registry",)),
        )
        self.assertEqual(partial.as_dict()["completeness"], "partial")
        self.assertFalse(partial.as_dict()["coverage"]["complete"])

    def test_provider_timeout_is_structured(self):
        def timeout(_request):
            raise TimeoutError
        provider = DirectHaApiProvider({ProviderCapability.AUTOMATION_TRACE: timeout})
        response = asyncio.run(provider.fetch(EvidenceRequest(ProviderCapability.AUTOMATION_TRACE)))
        self.assertEqual(response.failure.category, ProviderFailureCategory.TIMEOUT)
        self.assertTrue(response.failure.retryable)

    def test_invalid_provider_response_is_structured_failure(self):
        provider = DirectHaApiProvider({ProviderCapability.CONFIG_VALIDATION: lambda _: {"raw": "invalid"}})
        response = asyncio.run(provider.fetch(EvidenceRequest(ProviderCapability.CONFIG_VALIDATION)))
        self.assertEqual(response.failure.category, ProviderFailureCategory.UPSTREAM_ERROR)

    def test_provider_metadata_redacts_secrets_urls_and_bounds_payloads(self):
        response = result(
            "direct_ha_api",
            ProviderCapability.AUTOMATION_CONFIG,
            metadata={
                "access_secret": "not-a-real-secret",
                "token": "not-a-real-token",
                "authenticated_url": "http://example/secret/mcp",
                "safe": "x" * 400,
            },
        ).as_dict()
        encoded = str(response)
        self.assertNotIn("not-a-real-secret", encoded)
        self.assertNotIn("not-a-real-token", encoded)
        self.assertNotIn("/secret/mcp", encoded)
        self.assertIn("<bounded>", encoded)


class TokenEfficiencyTests(unittest.TestCase):
    def test_bounded_results_deduplicate_and_paginate(self):
        response = bounded_result([1, 1, 2, 3, 4], summary="Four findings", limit=2, offset=1)
        self.assertEqual(response.items, [2, 3])
        self.assertEqual(response.pagination.total, 4)
        self.assertTrue(response.pagination.has_more)
        self.assertTrue(response.truncated)

    def test_summary_mode_excludes_raw_bulk_configuration(self):
        response = bounded_result(
            [{"raw_configuration": "large"}],
            summary="One anomaly",
            detail_level=DetailLevel.SUMMARY,
        ).as_dict()
        self.assertEqual(response["items"], [])
        self.assertNotIn("large", str(response))

    def test_bounded_results_redact_secret_bearing_fields(self):
        response = bounded_result(
            [{"access_secret": "not-a-real-secret", "token": "not-a-real-token"}],
            summary="Safe finding",
        ).as_dict()
        encoded = str(response)
        self.assertNotIn("not-a-real-secret", encoded)
        self.assertNotIn("not-a-real-token", encoded)

    def test_evidence_mode_is_bounded_and_deduplicated(self):
        refs = [
            EvidenceReference("ref:1", "provider", "trace", "Finding 1"),
            EvidenceReference("ref:1", "provider", "trace", "Finding 1 duplicate"),
            EvidenceReference("ref:2", "provider", "trace", "Finding 2"),
        ]
        response = bounded_result(
            ["finding"],
            summary="Trace findings",
            detail_level=DetailLevel.EVIDENCE,
            evidence=refs,
            evidence_limit=1,
            coverage=SourceCoverage(("trace", "registry"), ("trace",), ("registry",)),
        )
        payload = response.as_dict()
        self.assertEqual(len(payload["evidence"]), 1)
        self.assertTrue(payload["truncated"])
        self.assertFalse(payload["coverage"]["complete"])


if __name__ == "__main__":
    unittest.main()
