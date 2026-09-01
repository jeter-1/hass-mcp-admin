"""Beta 46 analytical completeness separated from presentation bounds."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS,
    HELPER_DEPENDENCY_RISK_EXECUTION_MODELS,
    helper_dependency_risk_assessment,
)
from ha_mcp_engineering.governance.risk import (  # noqa: E402
    _HELPER_EFFECT_ANALYSIS_NODE_LIMIT,
    _HELPER_EFFECT_PROJECTION_VALUE_LIMIT,
    _HELPER_PROFILE_LIMIT,
    automation_action_consequence_profile,
)
from tests.test_beta46_helper_risk_semantic_completion import (  # noqa: E402
    TARGET,
    _binding,
)


class Beta46ActionProfileCompletenessTests(unittest.TestCase):
    def test_display_service_compaction_is_not_analysis_failure(self):
        config = {
            "trigger": [],
            "action": [
                {
                    "service": f"cover.synthetic_{index:03d}",
                    "target": {"entity_id": "cover.synthetic_garage"},
                }
                for index in range(_HELPER_PROFILE_LIMIT + 8)
            ],
        }
        profile = automation_action_consequence_profile(config)
        self.assertTrue(profile["complete"])
        self.assertTrue(profile["presentation_truncated"])
        self.assertFalse(profile["processing_limit_exceeded"])
        self.assertEqual(
            _HELPER_PROFILE_LIMIT + 8, profile["service_count"]
        )
        self.assertEqual(_HELPER_PROFILE_LIMIT, len(profile["services"]))

        binding = _binding(
            "{{ states('" + TARGET + "') }}",
            config,
            source_id="many_services",
        )
        self.assertTrue(binding["coverage_complete"])
        self.assertTrue(binding["evidence_complete"])
        self.assertTrue(binding["execution_eligible"])
        self.assertEqual("safety_critical", binding["physical_consequence"])
        risk = helper_dependency_risk_assessment(
            {"binding": binding, "provenance": {"generation": 46}}
        )
        self.assertEqual("high", risk.level.value)
        self.assertTrue(risk.apply_allowed)

    def test_effect_compaction_is_hash_bound_not_analysis_failure(self):
        config = {
            "trigger": [],
            "action": [
                {
                    "service": "cover.open_cover",
                    "target": {"entity_id": "cover.synthetic_garage"},
                    "data": {
                        f"field_{index:03d}": f"value_{index:03d}"
                        for index in range(
                            _HELPER_EFFECT_PROJECTION_VALUE_LIMIT + 8
                        )
                    },
                }
            ],
        }
        profile = automation_action_consequence_profile(config)
        self.assertTrue(profile["effect_projection_clipped"])
        self.assertTrue(profile["complete"])
        self.assertTrue(profile["presentation_truncated"])
        self.assertFalse(profile["processing_limit_exceeded"])
        self.assertEqual(
            _HELPER_EFFECT_PROJECTION_VALUE_LIMIT + 8,
            profile["effect_data_count"],
        )

        binding = _binding(
            "{{ states('" + TARGET + "') }}",
            config,
            source_id="compacted_effect",
        )
        self.assertTrue(binding["coverage_complete"])
        self.assertTrue(binding["execution_eligible"])
        self.assertNotIn(
            "action_profile_truncated",
            binding["coverage_failure_reason_codes"],
        )

    def test_material_beyond_visible_prefix_changes_full_set_fingerprint(self):
        def config(last_service: str) -> dict:
            services = [
                f"cover.synthetic_{index:03d}"
                for index in range(_HELPER_PROFILE_LIMIT + 7)
            ] + [last_service]
            return {
                "action": [
                    {
                        "service": service,
                        "target": {"entity_id": "cover.synthetic_garage"},
                    }
                    for service in services
                ]
            }

        first = automation_action_consequence_profile(
            config("cover.zzz_first")
        )
        second = automation_action_consequence_profile(
            config("cover.zzz_second")
        )
        self.assertEqual(first["services"], second["services"])
        self.assertNotEqual(
            first["services_fingerprint"],
            second["services_fingerprint"],
        )
        self.assertNotEqual(
            first["evidence_fingerprint"], second["evidence_fingerprint"]
        )

        first_binding = _binding(
            "{{ states('" + TARGET + "') }}",
            config("cover.zzz_first"),
            source_id="tail_service",
        )
        second_binding = _binding(
            "{{ states('" + TARGET + "') }}",
            config("cover.zzz_second"),
            source_id="tail_service",
        )
        self.assertNotEqual(
            first_binding["evidence_fingerprint"],
            second_binding["evidence_fingerprint"],
        )

    def test_actual_action_step_bound_is_processing_failure(self):
        config = {
            "action": [
                {
                    "service": "cover.open_cover",
                    "target": {"entity_id": "cover.synthetic_garage"},
                }
                for _ in range(513)
            ]
        }
        profile = automation_action_consequence_profile(config)
        self.assertFalse(profile["analysis_complete"])
        self.assertTrue(profile["processing_limit_exceeded"])
        self.assertEqual(
            "action_analysis_step_limit_exceeded",
            profile["processing_limit_reason"],
        )
        self.assertTrue(profile["processing_overflow_fingerprint"])
        self.assertFalse(profile["complete"])

        binding = _binding(
            "{{ states('" + TARGET + "') }}",
            config,
            source_id="action_step_overflow",
        )
        self.assertFalse(binding["coverage_complete"])
        self.assertFalse(binding["consequence_evidence_complete"])
        self.assertTrue(binding["execution_contract_complete"])
        self.assertTrue(binding["execution_eligible"])
        self.assertIn(
            "action_profile_processing_limit_exceeded",
            binding["coverage_failure_reason_codes"],
        )

    def test_actual_action_depth_bound_is_processing_failure(self):
        actions: list[dict] = [
            {
                "service": "cover.open_cover",
                "target": {"entity_id": "cover.synthetic_garage"},
            }
        ]
        for _ in range(18):
            actions = [{"if": [], "then": actions}]

        profile = automation_action_consequence_profile({"action": actions})
        self.assertFalse(profile["analysis_complete"])
        self.assertTrue(profile["processing_limit_exceeded"])
        self.assertEqual(
            "action_analysis_depth_limit_exceeded",
            profile["processing_limit_reason"],
        )
        self.assertTrue(profile["processing_overflow_fingerprint"])
        self.assertFalse(profile["complete"])

    def test_extreme_action_depth_fails_closed_without_recursing(self):
        actions: list[dict] = [
            {
                "service": "cover.open_cover",
                "target": {"entity_id": "cover.synthetic_garage"},
            }
        ]
        for _ in range(1200):
            actions = [{"repeat": {"sequence": actions}}]

        profile = automation_action_consequence_profile({"action": actions})
        self.assertFalse(profile["analysis_complete"])
        self.assertFalse(profile["semantic_complete"])
        self.assertTrue(profile["processing_limit_exceeded"])
        self.assertEqual(
            "action_analysis_depth_limit_exceeded",
            profile["processing_limit_reason"],
        )
        self.assertEqual("unknown", profile["physical_consequence"])
        self.assertEqual("high", profile["risk_level"])
        self.assertTrue(profile["processing_overflow_fingerprint"])
        self.assertFalse(profile["complete"])

    def test_effect_node_bound_is_processing_failure(self):
        config = {
            "action": [
                {
                    "service": "cover.open_cover",
                    "target": {"entity_id": "cover.synthetic_garage"},
                    "data": {
                        f"field_{index:04d}": index
                        for index in range(
                            _HELPER_EFFECT_ANALYSIS_NODE_LIMIT + 1
                        )
                    },
                }
            ]
        }

        profile = automation_action_consequence_profile(config)
        self.assertFalse(profile["analysis_complete"])
        self.assertTrue(profile["processing_limit_exceeded"])
        self.assertEqual(
            "effect_data_node_limit_exceeded",
            profile["processing_limit_reason"],
        )
        self.assertEqual(
            _HELPER_EFFECT_ANALYSIS_NODE_LIMIT,
            profile["processing_observed_effect_node_count"],
        )
        self.assertEqual(
            _HELPER_EFFECT_ANALYSIS_NODE_LIMIT,
            profile["processing_effect_node_limit"],
        )
        self.assertTrue(profile["processing_overflow_fingerprint"])
        self.assertFalse(profile["complete"])

    def test_effect_data_depth_bound_is_processing_failure(self):
        data: dict = {"value": "bounded"}
        for index in range(18):
            data = {f"level_{index}": data}
        config = {
            "action": [
                {
                    "service": "cover.open_cover",
                    "target": {"entity_id": "cover.synthetic_garage"},
                    "data": data,
                }
            ]
        }

        profile = automation_action_consequence_profile(config)
        self.assertTrue(profile["processing_limit_exceeded"])
        self.assertEqual(
            "effect_data_depth_limit_exceeded",
            profile["processing_limit_reason"],
        )
        self.assertTrue(profile["processing_overflow_fingerprint"])
        self.assertFalse(profile["analysis_complete"])
        self.assertFalse(profile["complete"])

    def test_unresolved_action_semantics_are_not_presentation_compaction(self):
        config = {
            "action": [
                {
                    "service": "{{ caller_supplied_service }}",
                    "target": {"entity_id": "cover.synthetic_garage"},
                }
            ]
        }
        profile = automation_action_consequence_profile(config)
        self.assertTrue(profile["analysis_complete"])
        self.assertFalse(profile["semantic_complete"])
        self.assertFalse(profile["processing_limit_exceeded"])
        self.assertFalse(profile["complete"])

    def test_display_metadata_does_not_change_effect_evidence(self):
        base = {
            "action": [
                {
                    "alias": "First display label",
                    "service": "cover.open_cover",
                    "target": {"entity_id": "cover.synthetic_garage"},
                }
            ]
        }
        changed = {
            "action": [
                {
                    **base["action"][0],
                    "alias": "Second display label",
                }
            ]
        }
        first = automation_action_consequence_profile(base)
        second = automation_action_consequence_profile(changed)
        self.assertEqual(
            first["effect_structure_fingerprint"],
            second["effect_structure_fingerprint"],
        )
        self.assertEqual(
            first["evidence_fingerprint"], second["evidence_fingerprint"]
        )


class Beta46RiskModelCompatibilityTests(unittest.TestCase):
    def test_v4_is_readable_but_cannot_authorize_current_execution(self):
        self.assertIn(
            "helper-dependency-risk-v4",
            HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS,
        )
        self.assertNotIn(
            "helper-dependency-risk-v4",
            HELPER_DEPENDENCY_RISK_EXECUTION_MODELS,
        )


if __name__ == "__main__":
    unittest.main()
