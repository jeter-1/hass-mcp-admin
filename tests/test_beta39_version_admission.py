"""B39-136-R3b: runtime Home Assistant version admission fails closed.

The reviewed template semantics are asserted valid for a fixed set of Home
Assistant releases. Nothing previously read the connected instance's running
version, so obligation-ledger evidence could gate approval, lock projection,
and dispatch on an instance the semantics were never reviewed against.

The gate is an additional necessary condition layered on the R2 source-read
fence and the R5 compatibility/execution-authority split. It fails closed on
every negative case: unsupported release, unreachable instance, malformed
configuration response, and a missing or empty version field.
"""

from __future__ import annotations

from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

import asyncio  # noqa: E402

from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.models import (  # noqa: E402
    OBLIGATION_LEDGER_MODEL,
    DependencyIndexSnapshot,
    DependencyScanResult,
    SourceCoverageItem,
)
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DependencySourceProvider,
    ProviderCapability,
)
from ha_mcp_engineering.dependency.semantic_registry import (  # noqa: E402
    supported_home_assistant_versions,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    VERSION_UNAVAILABLE_REASON,
    VERSION_UNREADABLE_REASON,
    VERSION_UNSUPPORTED_REASON,
    HelperDependencyRiskService,
    build_helper_dependency_risk_binding,
    helper_dependency_risk_assessment,
    home_assistant_version_admission,
)

ENTITY_ID = "input_boolean.beta39_version_admission"
SUPPORTED = supported_home_assistant_versions()


def _coverage():
    return (
        SourceCoverageItem(
            "automation",
            "direct_ha_api",
            "automation_config",
            "complete",
            obligation_ledger_completeness="complete",
        ),
        SourceCoverageItem(
            "blueprint",
            "direct_ha_api",
            "blueprint_source",
            "complete",
            obligation_ledger_completeness="complete",
        ),
    )


def _snapshot(version, status) -> DependencyIndexSnapshot:
    return DependencyIndexSnapshot(
        fingerprint="a" * 64,
        generation=1,
        built_at_monotonic=time.monotonic(),
        built_at="2026-08-19T12:00:00+00:00",
        findings=(),
        dynamic_references=(),
        target_metadata={},
        coverage=_coverage(),
        obligations=(),
        obligation_ledger_model=OBLIGATION_LEDGER_MODEL,
        home_assistant_version=version,
        home_assistant_version_status=status,
    )


_FRESH = {
    "freshness": "current",
    "evidence_stale": False,
    "invalidated": False,
}


def _binding(version, status):
    return build_helper_dependency_risk_binding(
        _snapshot(version, status),
        entity_id=ENTITY_ID,
        index_metadata=_FRESH,
    )


class VersionAdmissionDecisionTests(unittest.TestCase):
    def test_registry_is_the_only_source_of_supported_versions(self):
        self.assertEqual(
            ("2026.7.2", "2026.8.0", "2026.8.1"), SUPPORTED
        )

    def test_every_supported_release_is_admitted(self):
        for version in SUPPORTED:
            with self.subTest(version=version):
                decision = home_assistant_version_admission(
                    _snapshot(version, "observed")
                )
                self.assertTrue(decision["admitted"])
                self.assertIsNone(decision["reason_code"])
                self.assertEqual(version, decision["observed_version"])

    def test_negative_cases_are_distinguishable(self):
        cases = (
            ("2026.9.0", "observed", VERSION_UNSUPPORTED_REASON),
            ("2025.1.0", "observed", VERSION_UNSUPPORTED_REASON),
            (None, "unavailable", VERSION_UNAVAILABLE_REASON),
            (None, "unreadable", VERSION_UNREADABLE_REASON),
            ("", "observed", VERSION_UNREADABLE_REASON),
        )
        seen = set()
        for version, status, expected in cases:
            with self.subTest(version=version, status=status):
                decision = home_assistant_version_admission(
                    _snapshot(version, status)
                )
                self.assertFalse(decision["admitted"])
                self.assertEqual(expected, decision["reason_code"])
                seen.add(expected)
        # Mismatch, connectivity, and malformed must not collapse into one
        # generic failure.
        self.assertEqual(3, len(seen))

    def test_a_snapshot_without_version_evidence_fails_closed(self):
        # A snapshot predating the gate carries no version, and defaults must
        # refuse rather than admit.
        legacy = DependencyIndexSnapshot(
            fingerprint="a" * 64,
            generation=1,
            built_at_monotonic=time.monotonic(),
            built_at="2026-08-19T12:00:00+00:00",
            findings=(),
            dynamic_references=(),
            target_metadata={},
            coverage=_coverage(),
        )
        decision = home_assistant_version_admission(legacy)
        self.assertFalse(decision["admitted"])
        self.assertEqual(
            VERSION_UNAVAILABLE_REASON, decision["reason_code"]
        )


class VersionAdmissionBindingTests(unittest.TestCase):
    def test_supported_release_stays_execution_eligible(self):
        binding = _binding(SUPPORTED[-1], "observed")
        self.assertTrue(binding["execution_eligible"])
        self.assertTrue(binding["coverage_complete"])
        self.assertTrue(binding["home_assistant_version_admitted"])
        self.assertEqual(
            SUPPORTED[-1], binding["home_assistant_version_observed"]
        )
        self.assertEqual(
            list(SUPPORTED), binding["home_assistant_supported_versions"]
        )
        self.assertNotIn(
            VERSION_UNSUPPORTED_REASON,
            binding["coverage_failure_reason_codes"],
        )

    def test_unsupported_release_is_not_execution_eligible(self):
        binding = _binding("2026.9.0", "observed")
        self.assertFalse(binding["execution_eligible"])
        self.assertFalse(binding["evidence_complete"])
        self.assertEqual("coverage_failure", binding["semantic_precision"])
        self.assertIn(
            VERSION_UNSUPPORTED_REASON,
            binding["coverage_failure_reason_codes"],
        )
        self.assertFalse(binding["home_assistant_version_admitted"])

    def test_connectivity_failure_is_not_execution_eligible(self):
        binding = _binding(None, "unavailable")
        self.assertFalse(binding["execution_eligible"])
        self.assertIn(
            VERSION_UNAVAILABLE_REASON,
            binding["coverage_failure_reason_codes"],
        )

    def test_malformed_configuration_is_not_execution_eligible(self):
        binding = _binding(None, "unreadable")
        self.assertFalse(binding["execution_eligible"])
        self.assertIn(
            VERSION_UNREADABLE_REASON,
            binding["coverage_failure_reason_codes"],
        )

    def test_version_participates_in_the_evidence_fingerprint(self):
        # Approval binds this fingerprint, so evidence produced against a
        # different release must not hash the same.
        first = _binding(SUPPORTED[0], "observed")
        second = _binding(SUPPORTED[-1], "observed")
        self.assertNotEqual(
            first["evidence_fingerprint"], second["evidence_fingerprint"]
        )

    def test_admission_is_deterministic(self):
        self.assertEqual(
            _binding(SUPPORTED[-1], "observed")["evidence_fingerprint"],
            _binding(SUPPORTED[-1], "observed")["evidence_fingerprint"],
        )


class VersionAdmissionDisclosureTests(unittest.TestCase):
    """A refusal must say which release is running and which are reviewed."""

    def _assessment(self, version, status):
        binding = _binding(version, status)
        return helper_dependency_risk_assessment(
            {
                "binding": binding,
                "provenance": {
                    "provider": "dependency_index",
                    "completeness": binding["completeness"],
                },
            }
        )

    def test_unsupported_release_states_both_sides(self):
        risk = self._assessment("2026.9.0", "observed")
        self.assertFalse(risk.apply_allowed)
        text = " ".join(risk.reasons) + " " + " ".join(risk.warnings)
        self.assertIn("2026.9.0", text)
        for version in SUPPORTED:
            self.assertIn(version, text)

    def test_connectivity_failure_is_worded_as_a_read_failure(self):
        risk = self._assessment(None, "unavailable")
        self.assertFalse(risk.apply_allowed)
        text = " ".join(risk.reasons) + " " + " ".join(risk.warnings)
        self.assertIn("could not be read", text)

    def test_refusal_never_implies_partial_execution(self):
        for version, status in (
            ("2026.9.0", "observed"),
            (None, "unavailable"),
            (None, "unreadable"),
        ):
            with self.subTest(version=version, status=status):
                risk = self._assessment(version, status)
                text = (
                    " ".join(risk.reasons)
                    + " "
                    + " ".join(risk.warnings)
                ).lower()
                # A gate rejection is a pre-dispatch refusal, never a write.
                for forbidden in (
                    "changed but unverified",
                    "partially applied",
                    "partial write",
                    "may have been applied",
                ):
                    self.assertNotIn(forbidden, text)

    def test_risk_evidence_carries_the_observed_version(self):
        risk = self._assessment("2026.9.0", "observed")
        entry = next(
            item
            for item in risk.evidence
            if item.get("field") == "home_assistant_version"
        )
        self.assertEqual("2026.9.0", entry["observed_version"])
        self.assertFalse(entry["admitted"])
        self.assertEqual(list(SUPPORTED), entry["supported_versions"])


class _VersionProvider(DependencySourceProvider):
    provider_id = "direct_ha_api"
    capabilities = frozenset({ProviderCapability.DEPENDENCY_ANALYSIS})

    def __init__(self, version, status):
        self.version = version
        self.status = status

    @property
    def available(self):
        return True

    async def scan(self):
        return DependencyScanResult(
            findings=[],
            dynamic_references=[],
            target_metadata={},
            coverage=list(_coverage()),
            obligations=[],
            obligation_ledger_model=OBLIGATION_LEDGER_MODEL,
            home_assistant_version=self.version,
            home_assistant_version_status=self.status,
        )

    async def fetch(self, request):
        raise NotImplementedError


class VersionAdmissionProvenanceTests(unittest.IsolatedAsyncioTestCase):
    """The gate's decision travels with the R2/R5 provenance trail."""

    async def test_provenance_records_the_observed_version(self):
        index = DependencyIndex(_VersionProvider(SUPPORTED[-1], "observed"))
        evidence = await HelperDependencyRiskService(index).assess(
            ENTITY_ID, refresh=True
        )
        provenance = evidence["provenance"]
        self.assertEqual(
            SUPPORTED[-1], provenance["home_assistant_version"]
        )
        self.assertEqual(
            "observed", provenance["home_assistant_version_status"]
        )
        self.assertTrue(provenance["home_assistant_version_admitted"])

    async def test_provenance_records_a_refusal(self):
        index = DependencyIndex(_VersionProvider("2026.9.0", "observed"))
        evidence = await HelperDependencyRiskService(index).assess(
            ENTITY_ID, refresh=True
        )
        self.assertFalse(
            evidence["provenance"]["home_assistant_version_admitted"]
        )
        self.assertFalse(evidence["binding"]["execution_eligible"])

    async def test_the_fenced_preflight_read_carries_the_gate(self):
        # R2's fence governs freshness of the version too, because the
        # version is read as part of the scan the fence admits.
        index = DependencyIndex(_VersionProvider("2026.9.0", "observed"))
        evidence = await HelperDependencyRiskService(index).assess(
            ENTITY_ID, refresh=True, fenced=True
        )
        self.assertTrue(evidence["provenance"]["fenced"])
        self.assertFalse(evidence["binding"]["execution_eligible"])
        self.assertIn(
            VERSION_UNSUPPORTED_REASON,
            evidence["binding"]["coverage_failure_reason_codes"],
        )

    async def test_snapshot_fingerprint_separates_releases(self):
        first = DependencyIndex(_VersionProvider(SUPPORTED[0], "observed"))
        second = DependencyIndex(_VersionProvider(SUPPORTED[-1], "observed"))
        one, _, _ = await first.get()
        two, _, _ = await second.get()
        self.assertNotEqual(one.fingerprint, two.fingerprint)


class _ConfigRest:
    """Fake REST client whose /config behaviour is the case under test."""

    def __init__(self, config_behaviour):
        self.config_behaviour = config_behaviour
        self.config_reads = 0

    async def request(self, method, path, **kwargs):
        if path == "/config":
            self.config_reads += 1
            return self.config_behaviour()
        if path == "/states":
            return []
        raise AssertionError(f"unexpected read: {method} {path}")


class _RegistryWebSocket:
    async def command(self, payload):
        if payload == {"type": "config/entity_registry/list"}:
            return []
        raise AssertionError(payload)


class ProviderVersionReadTests(unittest.IsolatedAsyncioTestCase):
    """The provider read classifies each failure mode distinctly."""

    async def _scan(self, behaviour):
        from ha_mcp_engineering.dependency.provider import (
            DirectHaDependencyProvider,
        )

        rest = _ConfigRest(behaviour)
        result = await DirectHaDependencyProvider(
            rest, _RegistryWebSocket(), concurrency=2
        ).scan()
        return result, rest

    async def test_supported_version_is_observed(self):
        result, rest = await self._scan(
            lambda: {"version": SUPPORTED[-1], "location_name": "Home"}
        )
        self.assertEqual(1, rest.config_reads)
        self.assertEqual(SUPPORTED[-1], result.home_assistant_version)
        self.assertEqual(
            "observed", result.home_assistant_version_status
        )

    async def test_connectivity_failure_is_unavailable(self):
        def boom():
            raise ConnectionError("home assistant is unreachable")

        result, _ = await self._scan(boom)
        self.assertIsNone(result.home_assistant_version)
        self.assertEqual(
            "unavailable", result.home_assistant_version_status
        )

    async def test_timeout_is_unavailable(self):
        def timeout():
            raise asyncio.TimeoutError()

        result, _ = await self._scan(timeout)
        self.assertEqual(
            "unavailable", result.home_assistant_version_status
        )

    async def test_malformed_response_is_unreadable(self):
        result, _ = await self._scan(lambda: ["not", "a", "mapping"])
        self.assertIsNone(result.home_assistant_version)
        self.assertEqual(
            "unreadable", result.home_assistant_version_status
        )

    async def test_missing_version_field_is_unreadable(self):
        result, _ = await self._scan(lambda: {"location_name": "Home"})
        self.assertEqual(
            "unreadable", result.home_assistant_version_status
        )

    async def test_empty_version_field_is_unreadable(self):
        result, _ = await self._scan(lambda: {"version": "   "})
        self.assertEqual(
            "unreadable", result.home_assistant_version_status
        )

    async def test_non_string_version_field_does_not_crash(self):
        result, _ = await self._scan(lambda: {"version": 20260801})
        self.assertEqual(
            "unreadable", result.home_assistant_version_status
        )

    async def test_a_failed_version_read_does_not_abort_the_scan(self):
        # The scan still completes; the refusal happens at the gate, not by
        # losing the rest of the evidence.
        result, _ = await self._scan(
            lambda: (_ for _ in ()).throw(ConnectionError("down"))
        )
        self.assertEqual(
            "unavailable", result.home_assistant_version_status
        )
        self.assertTrue(result.coverage)


if __name__ == "__main__":
    unittest.main()
