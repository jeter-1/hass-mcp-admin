"""Dependency-aware risk contracts for the Beta 37 helper action."""

from __future__ import annotations

from dataclasses import replace
import time
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.models import (  # noqa: E402
    AutomationActionRiskProfile,
    DependencyFinding,
    DependencyIndexSnapshot,
    SourceCoverageItem,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    HelperDependencyRiskService,
    build_helper_dependency_risk_binding,
    helper_dependency_risk_assessment,
)
from ha_mcp_engineering.governance.risk import (  # noqa: E402
    automation_action_consequence_profile,
)


ENTITY_ID = "input_boolean.beta37_exact_action"


def action_profile(source: str, config: dict) -> AutomationActionRiskProfile:
    value = automation_action_consequence_profile(config)
    return AutomationActionRiskProfile(
        source_id=source,
        source_entity_id=f"automation.{source}",
        risk_level=value["risk_level"],
        physical_consequence=value["physical_consequence"],
        complete=value["complete"],
        truncated=value["truncated"],
        action_domains=tuple(value["action_domains"]),
        services=tuple(value["services"]),
        reason_codes=tuple(value["reason_codes"]),
        evidence_fingerprint=value["evidence_fingerprint"],
    )


def snapshot(
    profiles=(),
    *,
    automation_completeness: str = "complete",
    dynamic=(),
) -> DependencyIndexSnapshot:
    findings = tuple(
        DependencyFinding(
            evidence_id=f"ev_{index:024x}",
            target_entity_id=ENTITY_ID,
            source_type="automation",
            source_id=profile.source_id,
            source_entity_id=profile.source_entity_id,
            source_name=None,
            relation="trigger",
            config_path="$.trigger[0].entity_id",
        )
        for index, profile in enumerate(profiles, start=1)
    )
    return DependencyIndexSnapshot(
        fingerprint="a" * 64,
        generation=7,
        built_at_monotonic=time.monotonic(),
        built_at="2026-08-13T12:00:00+00:00",
        findings=findings,
        dynamic_references=tuple(dynamic),
        target_metadata={},
        coverage=(
            SourceCoverageItem(
                "automation",
                "direct_ha_api",
                "automation_config",
                automation_completeness,
            ),
            SourceCoverageItem(
                "blueprint",
                "direct_ha_api",
                "blueprint_source",
                "complete",
            ),
        ),
        automation_action_profiles=tuple(profiles),
    )


def binding(value: DependencyIndexSnapshot):
    return build_helper_dependency_risk_binding(
        value,
        entity_id=ENTITY_ID,
        index_metadata={
            "freshness": "current",
            "evidence_stale": False,
            "invalidated": False,
        },
    )


class HelperDependencyRiskTests(unittest.IsolatedAsyncioTestCase):
    def test_complete_no_dependency_is_low_and_standard_eligible(self):
        observed = binding(snapshot())
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 7}}
        )

        self.assertEqual(observed["physical_consequence"], "none")
        self.assertEqual(observed["completeness"], "complete")
        self.assertTrue(observed["execution_eligible"])
        self.assertEqual(risk.level.value, "low")
        self.assertTrue(risk.apply_allowed)

    def test_benign_automation_is_complete_without_elevation(self):
        benign = action_profile(
            "benign",
            {
                "action": [
                    {
                        "service": "notify.mobile_app_disposable",
                        "data": {"message": "bounded"},
                    }
                ]
            },
        )

        observed = binding(snapshot((benign,)))

        self.assertEqual(observed["physical_consequence"], "none")
        self.assertEqual(
            observed["relevant_downstream_object_ids"],
            ["automation.benign"],
        )
        self.assertEqual(
            observed["consequential_downstream_object_ids"], []
        )
        self.assertTrue(observed["evidence_complete"])

    def test_cover_action_is_consequential(self):
        cover = action_profile(
            "cover_path",
            {
                "action": [
                    {
                        "service": "cover.open_cover",
                        "target": {"entity_id": "cover.patio"},
                    }
                ]
            },
        )

        observed = binding(snapshot((cover,)))
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 7}}
        )

        self.assertEqual(observed["physical_consequence"], "direct")
        self.assertEqual(risk.level.value, "high")
        self.assertTrue(risk.apply_allowed)

    def test_multiple_automations_retain_worst_consequence(self):
        benign = action_profile(
            "benign",
            {"action": [{"service": "notify.notify"}]},
        )
        climate = action_profile(
            "climate",
            {
                "action": [
                    {
                        "service": "climate.set_temperature",
                        "target": {"entity_id": "climate.disposable"},
                    }
                ]
            },
        )
        security = action_profile(
            "security",
            {
                "action": [
                    {
                        "service": "lock.lock",
                        "target": {"entity_id": "lock.disposable"},
                    }
                ]
            },
        )

        observed = binding(snapshot((benign, climate, security)))

        self.assertEqual(
            observed["physical_consequence"], "safety_critical"
        )
        self.assertEqual(
            observed["consequential_downstream_object_ids"],
            ["automation.climate", "automation.security"],
        )

    def test_incomplete_coverage_never_claims_conclusive_low(self):
        observed = binding(
            snapshot(automation_completeness="partial")
        )
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 7}}
        )

        self.assertFalse(observed["evidence_complete"])
        self.assertNotEqual(observed["completeness"], "complete")
        self.assertEqual(risk.level.value, "high")
        self.assertFalse(risk.apply_allowed)

    def test_truncated_profile_never_claims_conclusive_low(self):
        benign = action_profile(
            "benign", {"action": [{"service": "notify.notify"}]}
        )
        truncated = replace(benign, complete=False, truncated=True)

        observed = binding(snapshot((truncated,)))

        self.assertEqual(observed["completeness"], "truncated")
        self.assertFalse(observed["execution_eligible"])
        self.assertNotEqual(observed["physical_consequence"], "none")

    async def test_dependency_source_failure_is_bounded_and_conservative(self):
        class FailingIndex:
            async def get(self, *, refresh):
                self.refresh = refresh
                raise RuntimeError("untrusted provider response")

        index = FailingIndex()
        observed = await HelperDependencyRiskService(index).assess(
            ENTITY_ID, refresh=True
        )
        risk = helper_dependency_risk_assessment(observed)

        self.assertTrue(index.refresh)
        self.assertEqual(observed["binding"]["completeness"], "failed")
        self.assertEqual(observed["binding"]["physical_consequence"], "unknown")
        self.assertEqual(risk.level.value, "high")
        self.assertFalse(risk.apply_allowed)
        self.assertNotIn(
            "untrusted provider response", str(observed)
        )

    def test_harmless_helper_is_not_blanket_high_risk(self):
        observed = binding(snapshot())
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 7}}
        )

        self.assertEqual(risk.level.value, "low")
        self.assertEqual(risk.warnings, [])


if __name__ == "__main__":
    unittest.main()
