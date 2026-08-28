"""Beta 47 helper-risk provenance and proportional effect semantics."""

from __future__ import annotations

from pathlib import Path
import hashlib
import inspect
import json
import sys
import unittest

from jinja2.filters import FILTERS


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.extraction import (  # noqa: E402
    extract_document_with_obligations,
    resolve_literal_label_obligations,
)
from ha_mcp_engineering.f3_configuration.locks import (  # noqa: E402
    helper_dependency_lock_key,
    operation_lock_requests,
    unconstrained_helper_dependency_lock_key,
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
from tests.f3_configuration_fixtures import (  # noqa: E402
    SyntheticConfigurationGateway,
    adapter_for,
    proposal_for,
    valid_config,
)


TARGET = "input_boolean.beta46_target"

UNKNOWN_MEMBER_ATTRIBUTE_FILTERS = {
    "map": "{{ [caller_supplied] | map(attribute='state') | list }}",
    "selectattr": "{{ [caller_supplied] | selectattr('state') | list }}",
    "rejectattr": "{{ [caller_supplied] | rejectattr('state') | list }}",
    "sort": "{{ [caller_supplied] | sort(attribute='state') | list }}",
    "join": "{{ [caller_supplied] | join(',', attribute='state') }}",
    "sum": "{{ [caller_supplied] | sum(attribute='state') }}",
    "unique": "{{ [caller_supplied] | unique(attribute='state') | list }}",
    "min": "{{ [caller_supplied] | min(attribute='state') }}",
    "max": "{{ [caller_supplied] | max(attribute='state') }}",
    "groupby": "{{ [caller_supplied] | groupby('state') | list }}",
}


def _consequential_config() -> dict:
    return {
        "action": [
            {
                "service": "cover.open_cover",
                "target": {"entity_id": "cover.synthetic_garage"},
            }
        ]
    }


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

    def test_exact_state_member_inclusion_and_exclusion_survive_filters(self):
        for filter_name, template in UNKNOWN_MEMBER_ATTRIBUTE_FILTERS.items():
            target_template = template.replace(
                "caller_supplied", "states.input_boolean.beta46_target"
            )
            unrelated_template = template.replace(
                "caller_supplied", "states.sensor.alpha"
            )
            with self.subTest(filter=filter_name, receiver="target"):
                target = _binding(
                    target_template,
                    _consequential_config(),
                    source_id=f"target_member_{filter_name}",
                )
                target_risk = helper_dependency_risk_assessment(
                    {"binding": target, "provenance": {"generation": 47}}
                )
                self.assertGreater(
                    target["exact_dependency_obligation_count"], 0
                )
                self.assertEqual(0, target["opaque_obligation_count"])
                self.assertTrue(target["evidence_complete"])
                self.assertTrue(target["execution_eligible"])
                self.assertEqual(
                    "safety_critical", target["physical_consequence"]
                )
                self.assertEqual("high", target_risk.level.value)
                self.assertTrue(target_risk.apply_allowed)
            with self.subTest(filter=filter_name, receiver="unrelated"):
                unrelated = _binding(
                    unrelated_template,
                    _consequential_config(),
                    source_id=f"unrelated_member_{filter_name}",
                )
                unrelated_risk = helper_dependency_risk_assessment(
                    {
                        "binding": unrelated,
                        "provenance": {"generation": 47},
                    }
                )
                self.assertEqual(0, unrelated["opaque_obligation_count"])
                self.assertTrue(unrelated["evidence_complete"])
                self.assertTrue(unrelated["execution_eligible"])
                self.assertEqual(
                    [], unrelated["relevant_downstream_object_ids"]
                )
                self.assertEqual("none", unrelated["physical_consequence"])
                self.assertEqual("low", unrelated_risk.level.value)
                self.assertTrue(unrelated_risk.apply_allowed)

    def test_complete_state_domains_preserve_target_polarity(self):
        for domain in ("sensor", "binary_sensor"):
            with self.subTest(domain=domain):
                excluded = _binding(
                    "{{ states."
                    + domain
                    + " | map(attribute='state') | list }}",
                    _consequential_config(),
                    source_id=f"excluded_domain_{domain}",
                )
                self.assertTrue(excluded["evidence_complete"])
                self.assertEqual(0, excluded["opaque_obligation_count"])
                self.assertEqual(
                    [], excluded["relevant_downstream_object_ids"]
                )
        target_capable = _binding(
            "{{ states.input_boolean | map(attribute='state') | list }}",
            _consequential_config(),
            source_id="target_capable_domain",
        )
        self.assertFalse(target_capable["evidence_complete"])
        self.assertGreater(target_capable["opaque_obligation_count"], 0)

    def test_ordinary_member_receivers_remain_low_friction(self):
        ordinary_templates = {
            "map": "{{ [{'state': 'ok'}] | map(attribute='state') | list }}",
            "selectattr": (
                "{{ [{'state': 'ok'}] | selectattr('state') | list }}"
            ),
            "rejectattr": (
                "{{ [{'state': 'ok'}] | rejectattr('state') | list }}"
            ),
            "sort": "{{ [{'state': 'ok'}] | sort(attribute='state') | list }}",
            "join": "{{ [{'state': 'ok'}] | join(',', attribute='state') }}",
            "sum": "{{ [{'state': 1}] | sum(attribute='state') }}",
            "unique": (
                "{{ [{'state': 'ok'}] | unique(attribute='state') | list }}"
            ),
            "min": "{{ [{'state': 'ok'}] | min(attribute='state') }}",
            "max": "{{ [{'state': 'ok'}] | max(attribute='state') }}",
            "groupby": "{{ [{'state': 'ok'}] | groupby('state') | list }}",
            "missing_mapping_field": (
                "{{ [{'name': 'ok'}] | map(attribute='state') | list }}"
            ),
            "ordinary_scalars": (
                "{{ ['value', 7] | map(attribute='state') | list }}"
            ),
            "mapping_values": (
                "{% set values = {'a': {'state': 'ok'}} %}"
                "{{ values.values() | map(attribute='state') | list }}"
            ),
            "mapping_items": (
                "{% set values = {'a': {'state': 'ok'}} %}"
                "{{ values.items() | map(attribute='1.state') | list }}"
            ),
        }
        for name, template in ordinary_templates.items():
            with self.subTest(case=name):
                binding = _binding(
                    template,
                    _consequential_config(),
                    source_id=f"ordinary_member_{name}",
                )
                risk = helper_dependency_risk_assessment(
                    {"binding": binding, "provenance": {"generation": 47}}
                )
                self.assertTrue(binding["evidence_complete"])
                self.assertTrue(binding["execution_eligible"])
                self.assertEqual(0, binding["opaque_obligation_count"])
                self.assertEqual([], binding["relevant_downstream_object_ids"])
                self.assertEqual("none", binding["physical_consequence"])
                self.assertEqual("low", risk.level.value)
                self.assertTrue(risk.apply_allowed)

    def test_local_macro_preserves_exact_member_provenance(self):
        macro = (
            "{% macro project(values) %}"
            "{{ values | map(attribute='state') | list }}"
            "{% endmacro %}"
        )
        exact = _binding(
            macro + "{{ project([states.input_boolean.beta46_target]) }}",
            _consequential_config(),
            source_id="macro_exact_member",
        )
        excluded = _binding(
            macro + "{{ project([states.sensor.alpha]) }}",
            _consequential_config(),
            source_id="macro_excluded_member",
        )
        self.assertGreater(exact["exact_dependency_obligation_count"], 0)
        self.assertEqual(0, exact["opaque_obligation_count"])
        self.assertEqual("safety_critical", exact["physical_consequence"])
        self.assertTrue(exact["execution_eligible"])
        self.assertEqual(0, excluded["opaque_obligation_count"])
        self.assertEqual([], excluded["relevant_downstream_object_ids"])
        self.assertTrue(excluded["execution_eligible"])


class Beta47ConservativeProvenanceControls(unittest.TestCase):
    def test_pinned_jinja_attribute_filter_inventory_is_covered(self):
        signature_filters = {
            name
            for name, function in FILTERS.items()
            if "attribute" in inspect.signature(function).parameters
        }
        self.assertEqual(
            {
                "groupby",
                "join",
                "max",
                "min",
                "sort",
                "sum",
                "unique",
            },
            signature_filters,
        )
        self.assertEqual(
            set(UNKNOWN_MEMBER_ATTRIBUTE_FILTERS),
            signature_filters | {"map", "selectattr", "rejectattr"},
        )

    def test_unknown_collection_member_attribute_filters_fail_closed(self):
        for filter_name, template in UNKNOWN_MEMBER_ATTRIBUTE_FILTERS.items():
            with self.subTest(filter=filter_name):
                source_id = f"unknown_member_{filter_name}"
                binding = _binding(
                    template,
                    _consequential_config(),
                    source_id=source_id,
                )
                risk = helper_dependency_risk_assessment(
                    {"binding": binding, "provenance": {"generation": 47}}
                )

                self.assertGreater(binding["opaque_obligation_count"], 0)
                self.assertFalse(binding["evidence_complete"])
                self.assertFalse(binding["execution_eligible"])
                self.assertIn(
                    f"automation.{source_id}",
                    binding["relevant_downstream_object_ids"],
                )
                self.assertEqual(
                    "safety_critical", binding["physical_consequence"]
                )
                self.assertEqual("high", risk.level.value)
                self.assertFalse(risk.apply_allowed)
                self.assertIn(
                    source_id,
                    binding["dependency_lock_projection"][
                        "automation_resource_ids"
                    ],
                )

    def test_direct_and_collection_member_unknown_controls_agree(self):
        direct = _binding(
            "{{ caller_supplied.state }}",
            _consequential_config(),
            source_id="direct_unknown_member",
        )
        projected = _binding(
            UNKNOWN_MEMBER_ATTRIBUTE_FILTERS["map"],
            _consequential_config(),
            source_id="projected_unknown_member",
        )
        for binding in (direct, projected):
            self.assertGreater(binding["opaque_obligation_count"], 0)
            self.assertFalse(binding["evidence_complete"])
            self.assertFalse(binding["execution_eligible"])
            self.assertEqual(
                "safety_critical", binding["physical_consequence"]
            )

    def test_positional_attribute_arguments_fail_closed(self):
        templates = {
            "join": "{{ [caller_supplied] | join(',', 'state') }}",
            "sum": "{{ [caller_supplied] | sum('state') }}",
            "unique": (
                "{{ [caller_supplied] | unique(false, 'state') | list }}"
            ),
            "min": "{{ [caller_supplied] | min(false, 'state') }}",
            "max": "{{ [caller_supplied] | max(false, 'state') }}",
            "sort": (
                "{{ [caller_supplied] | sort(false, false, 'state') | list }}"
            ),
        }
        for filter_name, template in templates.items():
            with self.subTest(filter=filter_name):
                binding = _binding(
                    template,
                    _consequential_config(),
                    source_id=f"positional_member_{filter_name}",
                )
                self.assertGreater(binding["opaque_obligation_count"], 0)
                self.assertFalse(binding["evidence_complete"])
                self.assertFalse(binding["execution_eligible"])

    def test_unknown_member_receiver_boundaries_fail_closed(self):
        long_path = ".".join("state" for _ in range(9))
        overflow = ", ".join("caller_supplied" for _ in range(129))
        templates = {
            "dynamic_attribute": (
                "{{ [caller_supplied] | map(attribute=attribute_name) | list }}"
            ),
            "dynamic_attribute_test": (
                "{{ [caller_supplied] "
                "| selectattr('state', test_name) | list }}"
            ),
            "unknown_with_default": (
                "{{ [caller_supplied] "
                "| map(attribute='state', default='off') | list }}"
            ),
            "nested_attribute": (
                "{{ [caller_supplied] "
                "| map(attribute='context.user_id') | list }}"
            ),
            "malformed_attribute": (
                "{{ [caller_supplied] "
                "| map(attribute='context..user_id') | list }}"
            ),
            "deep_attribute": (
                "{{ [caller_supplied] "
                f"| map(attribute='{long_path}') | list }}}}"
            ),
            "candidate_overflow": (
                "{{ [" + overflow + "] | map(attribute='state') | list }}"
            ),
            "external_template": (
                "{% import 'external_helpers.jinja' as external %}"
                "{{ external.values() | map(attribute='state') | list }}"
            ),
            "unknown_callable": (
                "{{ [unknown_factory()] | map(attribute='state') | list }}"
            ),
            "mixed_receivers": (
                "{{ [{'state': 'ok'}, caller_supplied] "
                "| map(attribute='state') | list }}"
            ),
        }
        for name, template in templates.items():
            with self.subTest(case=name):
                binding = _binding(
                    template,
                    _consequential_config(),
                    source_id=f"member_boundary_{name}",
                )
                self.assertFalse(binding["evidence_complete"])
                self.assertFalse(binding["execution_eligible"])
                self.assertGreater(
                    binding["opaque_obligation_count"]
                    + binding["coverage_failure_count"],
                    0,
                )
                self.assertIn(
                    binding["physical_consequence"],
                    {"safety_critical", "unknown"},
                )

    def test_member_attribute_material_changes_are_fingerprint_bound(self):
        templates = (
            "{{ [caller_supplied] | map(attribute='state') | list }}",
            "{{ [caller_supplied] | map(attribute='name') | list }}",
            "{{ [caller_supplied] | sort(attribute='state') | list }}",
            "{{ [{'state': 'ok'}] | map(attribute='state') | list }}",
            "{{ [{'state': 'ok'}] | map(attribute='name') | list }}",
            "{{ states.sensor | map(attribute='state') | list }}",
            "{{ states.binary_sensor | map(attribute='state') | list }}",
        )
        fingerprints = {
            _binding(
                template,
                _consequential_config(),
                source_id="member_attribute_drift",
            )["evidence_fingerprint"]
            for template in templates
        }
        self.assertEqual(len(templates), len(fingerprints))

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
    def test_v9_is_current_and_v3_through_v8_are_read_only(self):
        self.assertEqual(
            "helper-dependency-risk-v9", HELPER_DEPENDENCY_RISK_MODEL
        )
        self.assertEqual(
            frozenset({"helper-dependency-risk-v9"}),
            HELPER_DEPENDENCY_RISK_EXECUTION_MODELS,
        )
        for model in (
            "helper-dependency-risk-v3",
            "helper-dependency-risk-v4",
            "helper-dependency-risk-v5",
            "helper-dependency-risk-v6",
            "helper-dependency-risk-v7",
            "helper-dependency-risk-v8",
        ):
            self.assertIn(model, HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS)
            self.assertNotIn(model, HELPER_DEPENDENCY_RISK_EXECUTION_MODELS)


class Beta47CollectionMemberConfigurationLockTests(
    unittest.IsolatedAsyncioTestCase
):
    async def _lock_keys(self, template: str, *, action: str) -> set[str]:
        proposed = valid_config("automation")
        proposed["condition"] = [
            {"condition": "template", "value_template": template}
        ]
        current = valid_config("automation") if action == "update" else None
        gateway = SyntheticConfigurationGateway(
            ({("automation", "porch_light"): current} if current else {})
        )
        prepared = await adapter_for(
            "automation", action, gateway
        ).prepare(
            proposal_for(
                "automation",
                action,
                current_config=current,
                proposed_config=proposed,
            )
        )
        return {item.key for item in operation_lock_requests(prepared)}

    async def test_unknown_member_filters_use_conservative_configuration_lock(
        self,
    ):
        expected = unconstrained_helper_dependency_lock_key()
        for action in ("create", "update"):
            for filter_name, template in UNKNOWN_MEMBER_ATTRIBUTE_FILTERS.items():
                with self.subTest(action=action, filter=filter_name):
                    self.assertIn(
                        expected,
                        await self._lock_keys(template, action=action),
                    )

    async def test_exact_and_ordinary_member_filters_preserve_lock_polarity(
        self,
    ):
        exact = valid_config("automation")
        exact["condition"] = [
            {
                "condition": "template",
                "value_template": (
                    "{{ [states.input_boolean.beta46_target] "
                    "| map(attribute='state') | list }}"
                ),
            }
        ]
        ordinary = valid_config("automation")
        ordinary["condition"] = [
            {
                "condition": "template",
                "value_template": (
                    "{{ [{'state': 'ok'}] "
                    "| map(attribute='state') | list }}"
                ),
            }
        ]
        for action in ("create", "update"):
            with self.subTest(action=action, receiver="exact"):
                current = valid_config("automation") if action == "update" else None
                prepared = await adapter_for(
                    "automation",
                    action,
                    SyntheticConfigurationGateway(
                        ({("automation", "porch_light"): current} if current else {})
                    ),
                ).prepare(
                    proposal_for(
                        "automation",
                        action,
                        current_config=current,
                        proposed_config=exact,
                    )
                )
                keys = {item.key for item in operation_lock_requests(prepared)}
                self.assertIn(helper_dependency_lock_key(TARGET), keys)
                self.assertNotIn(
                    unconstrained_helper_dependency_lock_key(), keys
                )
            with self.subTest(action=action, receiver="ordinary"):
                current = valid_config("automation") if action == "update" else None
                prepared = await adapter_for(
                    "automation",
                    action,
                    SyntheticConfigurationGateway(
                        ({("automation", "porch_light"): current} if current else {})
                    ),
                ).prepare(
                    proposal_for(
                        "automation",
                        action,
                        current_config=current,
                        proposed_config=ordinary,
                    )
                )
                keys = {item.key for item in operation_lock_requests(prepared)}
                self.assertFalse(
                    any(key.startswith("helper_dependency:") for key in keys)
                )

    async def test_removing_opaque_member_filter_keeps_conservative_guard(self):
        opaque = valid_config("automation")
        opaque["condition"] = [
            {
                "condition": "template",
                "value_template": UNKNOWN_MEMBER_ATTRIBUTE_FILTERS["map"],
            }
        ]
        ordinary = valid_config("automation")
        prepared = await adapter_for(
            "automation",
            "update",
            SyntheticConfigurationGateway(
                {("automation", "porch_light"): opaque}
            ),
        ).prepare(
            proposal_for(
                "automation",
                "update",
                current_config=opaque,
                proposed_config=ordinary,
            )
        )
        self.assertIn(
            unconstrained_helper_dependency_lock_key(),
            {item.key for item in operation_lock_requests(prepared)},
        )


if __name__ == "__main__":
    unittest.main()
