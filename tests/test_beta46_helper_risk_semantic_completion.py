"""Beta 46 typed helper-risk semantics and presentation completeness."""

from __future__ import annotations

from pathlib import Path
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.extraction import (  # noqa: E402
    extract_document_with_obligations,
)
from ha_mcp_engineering.dependency.models import (  # noqa: E402
    AutomationActionRiskProfile,
    DependencyIndexSnapshot,
    OBLIGATION_LEDGER_MODEL,
    SourceCoverageItem,
)
from ha_mcp_engineering.dependency.semantic_registry import (  # noqa: E402
    supported_home_assistant_versions,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    build_helper_dependency_risk_binding,
    helper_dependency_risk_assessment,
)
from ha_mcp_engineering.governance.risk import (  # noqa: E402
    automation_action_consequence_profile,
)


TARGET = "input_boolean.beta46_target"
SUPPORTED_HA_VERSION = supported_home_assistant_versions()[-1]


def _profile(source_id: str, config: dict) -> AutomationActionRiskProfile:
    projected = automation_action_consequence_profile(config)
    return AutomationActionRiskProfile(
        source_id=source_id,
        source_entity_id=f"automation.{source_id}",
        risk_level=str(projected["risk_level"]),
        physical_consequence=str(projected["physical_consequence"]),
        complete=bool(projected["complete"]),
        truncated=bool(projected["truncated"]),
        action_domains=tuple(projected["action_domains"]),
        services=tuple(projected["services"]),
        reason_codes=tuple(projected["reason_codes"]),
        effect_projection_model=str(projected["effect_projection_model"]),
        effect_targets=tuple(projected["effect_targets"]),
        effect_data=tuple(projected["effect_data"]),
        effect_structure_fingerprint=str(
            projected["effect_structure_fingerprint"]
        ),
        effect_projection_fingerprint=str(
            projected["effect_projection_fingerprint"]
        ),
        effect_projection_clipped=bool(
            projected["effect_projection_clipped"]
        ),
        evidence_fingerprint=str(projected["evidence_fingerprint"]),
        analysis_complete=bool(projected["analysis_complete"]),
        semantic_complete=bool(projected["semantic_complete"]),
        presentation_truncated=bool(projected["presentation_truncated"]),
        processing_limit_exceeded=bool(
            projected["processing_limit_exceeded"]
        ),
        processing_limit_reason=projected["processing_limit_reason"],
        processing_observed_action_step_count=int(
            projected["processing_observed_action_step_count"]
        ),
        processing_action_step_limit=int(
            projected["processing_action_step_limit"]
        ),
        processing_action_depth_limit=int(
            projected["processing_action_depth_limit"]
        ),
        processing_overflow_fingerprint=projected[
            "processing_overflow_fingerprint"
        ],
        action_domain_count=int(projected["action_domain_count"]),
        action_domains_fingerprint=str(
            projected["action_domains_fingerprint"]
        ),
        service_count=int(projected["service_count"]),
        services_fingerprint=str(projected["services_fingerprint"]),
        reason_code_count=int(projected["reason_code_count"]),
        reason_codes_fingerprint=str(
            projected["reason_codes_fingerprint"]
        ),
        effect_target_count=int(projected["effect_target_count"]),
        effect_targets_fingerprint=str(
            projected["effect_targets_fingerprint"]
        ),
        effect_data_count=int(projected["effect_data_count"]),
        effect_data_fingerprint=str(
            projected["effect_data_fingerprint"]
        ),
    )


def _binding(template: str, config: dict, *, source_id: str) -> dict:
    document = {
        "alias": "Synthetic Beta 46 helper-risk fixture",
        **config,
        "condition": [
            {"condition": "template", "value_template": template}
        ],
    }
    obligations = extract_document_with_obligations(
        source_type="automation",
        source_id=source_id,
        source_entity_id=f"automation.{source_id}",
        source_name="Synthetic Beta 46 helper-risk fixture",
        source_state="on",
        config=document,
    )[2]
    snapshot = DependencyIndexSnapshot(
        fingerprint="b" * 64,
        generation=46,
        built_at_monotonic=time.monotonic(),
        built_at="2026-08-25T12:00:00+00:00",
        findings=(),
        dynamic_references=(),
        target_metadata={},
        coverage=(
            SourceCoverageItem(
                "automation", "direct_ha_api", "automation_config", "complete"
            ),
            SourceCoverageItem(
                "blueprint", "direct_ha_api", "blueprint_source", "complete"
            ),
        ),
        automation_action_profiles=(_profile(source_id, document),),
        obligations=tuple(obligations),
        obligation_ledger_model=OBLIGATION_LEDGER_MODEL,
        home_assistant_version=SUPPORTED_HA_VERSION,
        home_assistant_version_status="observed",
    )
    return build_helper_dependency_risk_binding(
        snapshot,
        entity_id=TARGET,
        index_metadata={
            "freshness": "current",
            "evidence_stale": False,
            "invalidated": False,
        },
    )


class Beta46TypedTriggerProvenanceTests(unittest.TestCase):
    def test_fixed_person_trigger_time_and_context_are_target_exclusions(self):
        config = {
            "trigger": [
                {
                    "platform": "state",
                    "entity_id": ["person.alpha", "person.bravo"],
                }
            ],
            "action": [
                {
                    "service": "cover.open_cover",
                    "target": {"entity_id": "cover.synthetic_garage"},
                }
            ],
        }
        binding = _binding(
            "{{ as_timestamp(now()) - "
            "as_timestamp(trigger.from_state.last_changed) }} "
            "{{ (now() - trigger.to_state.last_updated).total_seconds() }} "
            "{{ trigger.to_state.context.user_id or 'system' }}",
            config,
            source_id="fixed_person_trigger",
        )

        self.assertTrue(binding["coverage_complete"])
        self.assertTrue(binding["evidence_complete"])
        self.assertTrue(binding["execution_eligible"])
        self.assertEqual(0, binding["exact_dependency_obligation_count"])
        self.assertEqual(0, binding["opaque_obligation_count"])
        self.assertEqual([], binding["relevant_downstream_object_ids"])
        self.assertEqual("none", binding["physical_consequence"])
        risk = helper_dependency_risk_assessment(
            {"binding": binding, "provenance": {"generation": 46}}
        )
        self.assertEqual("low", risk.level.value)
        self.assertTrue(risk.apply_allowed)

    def test_fixed_trigger_state_provenance_survives_finite_transport(self):
        config = {
            "trigger": [
                {"platform": "state", "entity_id": "person.alpha"},
                {"platform": "zone", "entity_id": "person.bravo", "zone": "zone.home"},
            ],
            "action": [{"service": "notify.notify", "data": {"message": "ok"}}],
        }
        templates = (
            "{% set prior = trigger.from_state %}"
            "{{ prior.last_changed | as_timestamp }}",
            "{% set observed = trigger.to_state if enabled else trigger.from_state %}"
            "{{ (now() - observed.last_updated).total_seconds() }}",
            "{% for observed in [trigger.from_state, trigger.to_state] %}"
            "{{ observed.context.user_id or 'system' }}{% endfor %}",
        )
        for index, template in enumerate(templates):
            with self.subTest(template=template):
                binding = _binding(
                    template,
                    config,
                    source_id=f"fixed_transport_{index}",
                )
                self.assertTrue(binding["evidence_complete"])
                self.assertEqual(0, binding["opaque_obligation_count"])
                self.assertEqual([], binding["relevant_downstream_object_ids"])

    def test_typed_values_still_fail_closed_when_used_as_entity_selectors(self):
        config = {
            "trigger": [
                {"platform": "state", "entity_id": "person.alpha"}
            ],
            "action": [
                {
                    "service": "cover.open_cover",
                    "target": {"entity_id": "cover.synthetic_garage"},
                }
            ],
        }
        for index, template in enumerate(
            (
                "{{ states(as_timestamp(trigger.to_state.last_changed)) }}",
                "{{ states(trigger.to_state.context.user_id) }}",
            )
        ):
            with self.subTest(template=template):
                binding = _binding(
                    template,
                    config,
                    source_id=f"typed_selector_{index}",
                )
                self.assertFalse(binding["evidence_complete"])
                self.assertFalse(binding["execution_eligible"])
                self.assertGreater(binding["opaque_obligation_count"], 0)
                self.assertEqual(
                    "safety_critical", binding["physical_consequence"]
                )


if __name__ == "__main__":
    unittest.main()
