"""Beta 47 helper-risk provenance and proportional effect semantics."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.extraction import (  # noqa: E402
    extract_document_with_obligations,
    resolve_literal_label_obligations,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS,
    HELPER_DEPENDENCY_RISK_EXECUTION_MODELS,
    HELPER_DEPENDENCY_RISK_MODEL,
    build_helper_dependency_risk_binding,
    helper_dependency_risk_assessment,
)
from ha_mcp_engineering.governance.risk import (  # noqa: E402
    automation_action_consequence_profile,
)
from tests.test_beta45_helper_risk_exclusion_provenance import (  # noqa: E402
    _snapshot,
)
from tests.test_beta46_helper_risk_semantic_completion import (  # noqa: E402
    _binding,
)


TARGET = "input_boolean.beta46_target"


class Beta47BaselineProvenanceReproductions(unittest.TestCase):
    def test_unrelated_standard_helper_fixture_is_conclusive(self):
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
            "{% set observed = [trigger.from_state, trigger.to_state] %}"
            "{{ observed | map(attribute='state') | list }}"
            "{{ observed | map(attribute='name') | list }}"
            "{{ observed | map(attribute='last_changed') "
            "| map('as_timestamp') | list }}"
            "{{ states.sensor | map(attribute='state') | list }}"
            "{{ states.binary_sensor | map(attribute='name') | list }}",
            config,
            source_id="unrelated_standard_helper",
        )
        risk = helper_dependency_risk_assessment(
            {"binding": binding, "provenance": {"generation": 47}}
        )

        self.assertEqual(0, binding["exact_dependency_obligation_count"])
        self.assertEqual(0, binding["opaque_obligation_count"])
        self.assertEqual([], binding["relevant_downstream_object_ids"])
        self.assertTrue(binding["coverage_complete"])
        self.assertTrue(binding["evidence_complete"])
        self.assertTrue(binding["execution_eligible"])
        self.assertEqual("none", binding["physical_consequence"])
        self.assertEqual("low", risk.level.value)
        self.assertTrue(risk.apply_allowed)

    def test_fixed_trigger_scalar_projection_through_map_is_excluded(self):
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
        templates = (
            "{{ [trigger.from_state, trigger.to_state] "
            "| map(attribute='state') | list }}",
            "{{ [trigger.from_state, trigger.to_state] "
            "| map(attribute='name') | list }}",
            "{{ [trigger.from_state, trigger.to_state] "
            "| map(attribute='last_changed') | map('as_timestamp') | list }}",
            "{{ [trigger.from_state, trigger.to_state] "
            "| map(attribute='context.user_id') | list }}",
        )
        for index, template in enumerate(templates):
            with self.subTest(template=template):
                binding = _binding(
                    template,
                    config,
                    source_id=f"fixed_trigger_map_{index}",
                )
                self.assertTrue(binding["evidence_complete"])
                self.assertTrue(binding["execution_eligible"])
                self.assertEqual(0, binding["opaque_obligation_count"])
                self.assertEqual([], binding["relevant_downstream_object_ids"])

    def test_non_helper_domain_scalar_projection_remains_excluded(self):
        config = {
            "action": [
                {
                    "service": "cover.open_cover",
                    "target": {"entity_id": "cover.synthetic_garage"},
                }
            ]
        }
        for index, template in enumerate(
            (
                "{{ states.sensor | map(attribute='state') | list }}",
                "{{ states.binary_sensor | map(attribute='name') | list }}",
            )
        ):
            with self.subTest(template=template):
                binding = _binding(
                    template,
                    config,
                    source_id=f"domain_map_{index}",
                )
                self.assertTrue(binding["evidence_complete"])
                self.assertEqual(0, binding["opaque_obligation_count"])
                self.assertEqual([], binding["relevant_downstream_object_ids"])

    def test_finite_candidates_keep_completeness_through_reviewed_transport(self):
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
        templates = (
            "{{ states([trigger.from_state, trigger.to_state] "
            "| map(attribute='entity_id') | list) }}",
            "{% set candidates = {'a': 'sensor.alpha', 'b': 'sensor.bravo'} %}"
            "{{ states(candidates.values() | select('string') | list) }}",
        )
        for index, template in enumerate(templates):
            with self.subTest(template=template):
                binding = _binding(
                    template,
                    config,
                    source_id=f"finite_transport_{index}",
                )
                self.assertTrue(binding["evidence_complete"])
                self.assertEqual(0, binding["opaque_obligation_count"])
                self.assertEqual([], binding["relevant_downstream_object_ids"])

    def test_complete_label_candidates_survive_string_conversion(self):
        template = (
            "{{ states(label_entities('reviewed_label') "
            "| map('string') | list) }}"
        )
        document = {
            "alias": "Synthetic Beta 47 label transport",
            "condition": [
                {"condition": "template", "value_template": template}
            ],
            "action": [
                {
                    "service": "cover.open_cover",
                    "target": {"entity_id": "cover.synthetic_garage"},
                }
            ],
        }
        obligations = extract_document_with_obligations(
            source_type="automation",
            source_id="label_string_transport",
            source_entity_id="automation.label_string_transport",
            source_name="Synthetic Beta 47 label transport",
            source_state="on",
            config=document,
        )[2]
        members = ("sensor.alpha", "sensor.bravo")
        membership_fingerprint = hashlib.sha256(
            json.dumps(list(members), separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        resolved = resolve_literal_label_obligations(
            obligations,
            label_memberships={"reviewed_label": members},
            label_membership_fingerprints={
                "reviewed_label": membership_fingerprint
            },
            label_membership_truncated=(),
            label_lookup_resolutions={
                "reviewed_label": ("label_id", "reviewed_label")
            },
            label_registry_complete=True,
        )
        binding = build_helper_dependency_risk_binding(
            _snapshot(resolved, source_id="label_string_transport"),
            entity_id=TARGET,
            index_metadata={
                "freshness": "current",
                "evidence_stale": False,
                "invalidated": False,
            },
        )
        self.assertTrue(binding["evidence_complete"])
        self.assertEqual(0, binding["opaque_obligation_count"])
        self.assertEqual([], binding["relevant_downstream_object_ids"])

    def test_material_trigger_and_domain_changes_change_the_binding(self):
        base_config = {
            "trigger": [{"platform": "state", "entity_id": "person.alpha"}],
            "action": [{"service": "notify.notify", "data": {"message": "ok"}}],
        }
        changed_trigger = {
            **base_config,
            "trigger": [{"platform": "state", "entity_id": "person.bravo"}],
        }
        trigger_first = _binding(
            "{{ trigger.to_state.name }}",
            base_config,
            source_id="trigger_drift",
        )
        trigger_second = _binding(
            "{{ trigger.to_state.name }}",
            changed_trigger,
            source_id="trigger_drift",
        )
        sensor_domain = _binding(
            "{{ states.sensor | map(attribute='state') | list }}",
            base_config,
            source_id="domain_drift",
        )
        helper_domain = _binding(
            "{{ states.input_boolean | map(attribute='state') | list }}",
            base_config,
            source_id="domain_drift",
        )

        self.assertNotEqual(
            trigger_first["evidence_fingerprint"],
            trigger_second["evidence_fingerprint"],
        )
        self.assertNotEqual(
            sensor_domain["evidence_fingerprint"],
            helper_domain["evidence_fingerprint"],
        )
        self.assertTrue(sensor_domain["evidence_complete"])
        self.assertFalse(helper_domain["evidence_complete"])
        self.assertGreater(helper_domain["opaque_obligation_count"], 0)


class Beta47ConservativeProvenanceControls(unittest.TestCase):
    def test_target_capable_unknowns_remain_nonactionable(self):
        config = {
            "action": [
                {
                    "service": "cover.open_cover",
                    "target": {"entity_id": "cover.synthetic_garage"},
                }
            ]
        }
        templates = (
            "{{ states(caller_supplied_entity) }}",
            "{{ states(unknown_entity_factory()) }}",
            "{{ states(label_entities(caller_supplied_label)) }}",
            "{{ states.input_boolean | map(attribute='state') | list }}",
            "{% import 'external_helpers.jinja' as external %}"
            "{{ states(external.entity_selector()) }}",
        )
        for index, template in enumerate(templates):
            with self.subTest(template=template):
                binding = _binding(
                    template,
                    config,
                    source_id=f"opaque_control_{index}",
                )
                risk = helper_dependency_risk_assessment(
                    {"binding": binding, "provenance": {"generation": 47}}
                )
                self.assertFalse(binding["evidence_complete"])
                self.assertFalse(binding["execution_eligible"])
                self.assertGreater(binding["opaque_obligation_count"], 0)
                self.assertEqual(
                    "safety_critical", binding["physical_consequence"]
                )
                self.assertEqual("high", risk.level.value)
                self.assertFalse(risk.apply_allowed)

    def test_malformed_template_remains_nonactionable_and_conservative(self):
        binding = _binding(
            "{{ states(",
            {
                "action": [
                    {
                        "service": "cover.open_cover",
                        "target": {"entity_id": "cover.synthetic_garage"},
                    }
                ]
            },
            source_id="malformed_template",
        )
        self.assertFalse(binding["evidence_complete"])
        self.assertFalse(binding["execution_eligible"])
        self.assertGreater(binding["opaque_obligation_count"], 0)
        self.assertEqual("bounded_opaque", binding["semantic_precision"])


class Beta47ActionEffectReproductions(unittest.TestCase):
    @staticmethod
    def _safety_critical_actions(message: str) -> dict:
        return {
            "action": [
                {
                    "service": "lock.unlock",
                    "target": {"entity_id": "lock.synthetic_front"},
                },
                {
                    "service": "notify.mobile_app_synthetic",
                    "data": {"message": message},
                },
            ]
        }

    def test_reviewed_targetless_notification_does_not_poison_exact_action(self):
        profile = automation_action_consequence_profile(
            self._safety_critical_actions("Guest mode changed")
        )
        self.assertTrue(profile["analysis_complete"])
        self.assertTrue(profile["semantic_complete"])
        self.assertTrue(profile["complete"])
        self.assertEqual("safety_critical", profile["physical_consequence"])

    def test_bounded_display_template_is_actionable_but_dynamic_control_is_not(self):
        safe = automation_action_consequence_profile(
            self._safety_critical_actions(
                "Guest mode changed: {{ states('input_boolean.beta46_target') }}"
            )
        )
        unsafe = automation_action_consequence_profile(
            self._safety_critical_actions("{{ caller_supplied_message }}")
        )
        self.assertTrue(safe["semantic_complete"])
        self.assertTrue(safe["complete"])
        self.assertEqual("safety_critical", safe["physical_consequence"])
        self.assertFalse(unsafe["semantic_complete"])
        self.assertFalse(unsafe["complete"])

    def test_notification_controls_and_unreviewed_effects_stay_conservative(self):
        messages = (
            "{{ caller_supplied_message }}",
            "command_{{ caller_supplied_command }}",
            "kiosk_{{ caller_supplied_command }}",
            "request_location_update",
            "remove_channel",
            "TTS",
        )
        for message in messages:
            with self.subTest(message=message):
                profile = automation_action_consequence_profile(
                    self._safety_critical_actions(message)
                )
                self.assertFalse(profile["semantic_complete"])
                self.assertFalse(profile["complete"])

        for action in (
            {"service": "custom_domain.synthetic_effect"},
            {"service": "{{ caller_supplied_service }}"},
            {"service": "script.synthetic_transitive"},
            {"service": "scene.turn_on", "target": {"entity_id": "scene.synthetic"}},
            {"service": "cover.open_cover"},
        ):
            with self.subTest(action=action):
                profile = automation_action_consequence_profile({"action": [action]})
                self.assertFalse(profile["semantic_complete"])
                self.assertFalse(profile["complete"])

    def test_safe_template_proof_is_prefix_sensitive(self):
        safe = self._safety_critical_actions(
            "Status: {{ caller_supplied_message }}"
        )
        dynamic_first = self._safety_critical_actions(
            "{{ caller_supplied_prefix }} status"
        )
        split_command = self._safety_critical_actions(
            "comm{{ 'and_flashlight' }}"
        )
        safe_profile = automation_action_consequence_profile(safe)
        dynamic_profile = automation_action_consequence_profile(dynamic_first)
        command_profile = automation_action_consequence_profile(split_command)
        self.assertTrue(safe_profile["complete"])
        self.assertFalse(dynamic_profile["complete"])
        self.assertFalse(command_profile["complete"])

    def test_executable_statement_before_literal_prefix_stays_conservative(self):
        profile = automation_action_consequence_profile(
            self._safety_critical_actions(
                "{% if caller_supplied_condition %}"
                "{{ caller_supplied_message }}"
                "{% endif %}Status"
            )
        )
        self.assertFalse(profile["semantic_complete"])
        self.assertFalse(profile["complete"])

    def test_notification_template_proof_is_bounded(self):
        oversized = "Status: {{ value }}" + "x" * 4_096
        node_heavy = "Status: " + "".join("{{ value }}" for _ in range(600))
        for message in (oversized, node_heavy, "Status: {{"):
            with self.subTest(length=len(message)):
                profile = automation_action_consequence_profile(
                    self._safety_critical_actions(message)
                )
                self.assertFalse(profile["semantic_complete"])
                self.assertFalse(profile["complete"])

    def test_exact_safety_critical_binding_is_elevated_and_actionable(self):
        config = self._safety_critical_actions("Guest mode changed")
        binding = _binding(
            "{{ states('input_boolean.beta46_target') }}",
            config,
            source_id="exact_safety_critical",
        )
        risk = helper_dependency_risk_assessment(
            {"binding": binding, "provenance": {"generation": 47}}
        )
        self.assertTrue(binding["evidence_complete"])
        self.assertTrue(binding["execution_eligible"])
        self.assertEqual("safety_critical", binding["physical_consequence"])
        self.assertEqual("high", risk.level.value)
        self.assertTrue(risk.apply_allowed)

    def test_exact_dependency_does_not_override_unknown_effect(self):
        binding = _binding(
            "{{ states('input_boolean.beta46_target') }}",
            {"action": [{"service": "custom_domain.synthetic_effect"}]},
            source_id="exact_with_unknown_effect",
        )
        risk = helper_dependency_risk_assessment(
            {"binding": binding, "provenance": {"generation": 47}}
        )
        self.assertGreater(binding["exact_dependency_obligation_count"], 0)
        self.assertFalse(binding["evidence_complete"])
        self.assertFalse(binding["execution_eligible"])
        self.assertEqual("unknown", binding["physical_consequence"])
        self.assertEqual("high", risk.level.value)
        self.assertFalse(risk.apply_allowed)


class Beta47RiskModelCompatibilityTests(unittest.TestCase):
    def test_v6_is_current_and_v3_through_v5_are_read_only(self):
        self.assertEqual("helper-dependency-risk-v6", HELPER_DEPENDENCY_RISK_MODEL)
        self.assertEqual(
            frozenset({"helper-dependency-risk-v6"}),
            HELPER_DEPENDENCY_RISK_EXECUTION_MODELS,
        )
        for model in (
            "helper-dependency-risk-v3",
            "helper-dependency-risk-v4",
            "helper-dependency-risk-v5",
        ):
            self.assertIn(model, HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS)
            self.assertNotIn(model, HELPER_DEPENDENCY_RISK_EXECUTION_MODELS)


if __name__ == "__main__":
    unittest.main()
