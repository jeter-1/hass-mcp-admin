"""Beta 39 selector-aware specialized helper-risk regression coverage."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    build_helper_dependency_risk_binding,
    helper_dependency_risk_assessment,
)
from ha_mcp_engineering.governance.models import RiskLevel  # noqa: E402
from ha_mcp_engineering.dependency.extraction import (  # noqa: E402
    MAX_TEMPLATE_SEGMENT_CHARS,
)
from ha_mcp_engineering.dependency.models import (  # noqa: E402
    SourceCoverageItem,
)
from tests.test_beta38_helper_dependency_resolution import (  # noqa: E402
    TARGET,
    _binding,
    _dynamic,
    _profile,
    _snapshot,
)


ORDINARY_DYNAMIC_TEMPLATES = (
    "{% set states = ['ready'] %}{{ states | join(', ') }}",
    "{% set states = ['message'] %}{{ states | count }}",
    "{% for states in ['ok'] %}{{ states }}{% endfor %}",
    "{% set states = namespace(value='ok') %}{{ states }}",
    "{% set states = signature_parts %}{{ states | join(':') }}",
    "{% set states = messages %}{{ states | length }}",
    "{% set states = ['a'] %}{{ states[0] }}",
    "{% set states = {'signature':'ok'} %}{{ states['signature'] }}",
    "{% for states in messages %}{{ states }}{% endfor %}",
)

FINITE_SENSOR_TEMPLATES = (
    "{% set temperature_entity = 'sensor.a' if enabled else 'sensor.b' %}"
    "{{ states(temperature_entity) }}",
    "{% for c in [{'id':'sensor.c'},{'id':'sensor.d'}] %}"
    "{{ states(c.id) }}{% endfor %}",
)


def _dynamic_items(templates: tuple[str, ...], prefix: str):
    items = []
    for index, template in enumerate(templates):
        findings, dynamic = _dynamic(
            template, source_id=f"{prefix}_{index}"
        )
        if findings:
            raise AssertionError("dynamic fixture unexpectedly became static")
        items.extend(dynamic)
    return items


class SpecializedHelperRiskSelectorTests(unittest.TestCase):
    def test_beta38_live_shape_excludes_non_selector_template_dataflow(self):
        ordinary = _dynamic_items(
            ORDINARY_DYNAMIC_TEMPLATES, "ordinary_formatting"
        )
        finite_sensors = _dynamic_items(
            FINITE_SENSOR_TEMPLATES, "finite_geolocation"
        )
        observed = _binding(
            _snapshot(dynamic=(*ordinary, *finite_sensors))
        )
        risk = helper_dependency_risk_assessment(
            {
                "binding": observed,
                "provenance": {
                    "provider": "dependency_index",
                    "completeness": observed["completeness"],
                    "generation": 39,
                    "fingerprint": "b" * 64,
                    "fallback": "none",
                    "fallback_occurred": False,
                },
            }
        )

        self.assertEqual(len(ordinary), 9)
        self.assertTrue(
            all(not item.entity_selector_present for item in ordinary)
        )
        self.assertEqual(
            {item.reference_kind for item in ordinary},
            {"ordinary_dynamic_template"},
        )
        self.assertEqual(
            {item.warning for item in ordinary},
            {"Dynamic template content contains no entity selector."},
        )
        self.assertEqual(len(finite_sensors), 2)
        self.assertTrue(
            all(item.entity_selector_present for item in finite_sensors)
        )
        self.assertTrue(observed["evidence_complete"])
        self.assertTrue(observed["execution_eligible"])
        self.assertEqual(observed["completeness"], "complete")
        self.assertEqual(observed["physical_consequence"], "none")
        self.assertEqual(observed["relevant_downstream_object_ids"], [])
        self.assertEqual(
            observed["target_relevant_dynamic_reference_count"], 0
        )
        self.assertEqual(observed["unrelated_dynamic_reference_count"], 2)
        self.assertEqual(observed["non_entity_dynamic_reference_count"], 9)
        self.assertEqual(risk.level, RiskLevel.LOW)
        self.assertTrue(risk.apply_allowed)

    def test_non_selector_evidence_is_bounded_without_poisoning_completeness(self):
        templates = tuple(
            f"{{% set states = ['message_{index}'] %}}"
            "{{ states | join(',') }}"
            for index in range(70)
        )
        ordinary = _dynamic_items(templates, "bounded_formatting")
        observed = _binding(_snapshot(dynamic=ordinary))

        self.assertTrue(observed["evidence_complete"])
        self.assertTrue(observed["execution_eligible"])
        self.assertEqual(observed["non_entity_dynamic_reference_count"], 70)
        self.assertEqual(
            observed["non_entity_dynamic_evaluation_overflow_count"], 6
        )
        self.assertIsInstance(
            observed[
                "non_entity_dynamic_evaluation_overflow_fingerprint"
            ],
            str,
        )
        self.assertEqual(
            len(observed["resolved_dynamic_reference_evidence"]), 64
        )

    def test_finite_candidates_include_or_exclude_the_exact_target(self):
        _findings, excluded = _dynamic(
            "{% set entity = 'sensor.a' if enabled else 'sensor.b' %}"
            "{{ states(entity) }}",
            source_id="finite_excluded",
        )
        excluded_binding = _binding(_snapshot(dynamic=excluded))
        self.assertTrue(excluded_binding["evidence_complete"])
        self.assertEqual(
            excluded_binding["unrelated_dynamic_reference_count"], 1
        )

        source_id = "finite_included"
        _findings, included = _dynamic(
            f"{{% set entity = '{TARGET}' if enabled else 'sensor.b' %}}"
            "{{ states(entity) }}",
            source_id=source_id,
        )
        included_binding = _binding(
            _snapshot(
                dynamic=included,
                profiles=(_profile(source_id),),
            )
        )
        self.assertTrue(included_binding["evidence_complete"])
        self.assertEqual(
            included_binding["resolved_target_dynamic_reference_count"], 1
        )
        self.assertEqual(
            included_binding["relevant_downstream_object_ids"],
            [f"automation.{source_id}"],
        )

    def test_unbounded_lookup_macro_and_dynamic_label_remain_incomplete(self):
        templates = (
            "{{ states(variable) }}",
            "{{ states(entity_macro()) }}",
            "{{ label_entities(label_name) | map('states') | list }}",
            "{% set states = states(variable) %}{{ states }}",
            "{% if enabled %}{% set states = ['message'] %}"
            "{% endif %}{{ states(variable) }}",
            "{% macro signature() %}{% set states = ['message'] %}"
            "{% endmacro %}{{ states(variable) }}",
            "{% for states in messages %}{{ states }}{% else %}"
            "{{ states(variable) }}{% endfor %}",
            "{% for states in messages %}{{ states }}",
        )
        for index, template in enumerate(templates):
            with self.subTest(template=template):
                _findings, dynamic = _dynamic(
                    template, source_id=f"unresolved_selector_{index}"
                )
                observed = _binding(_snapshot(dynamic=dynamic))

                self.assertEqual(len(dynamic), 1)
                self.assertTrue(dynamic[0].entity_selector_present)
                self.assertFalse(observed["evidence_complete"])
                self.assertFalse(observed["execution_eligible"])
                self.assertEqual(
                    observed["target_relevant_dynamic_reference_count"],
                    1,
                )

    def test_static_and_consequential_dependencies_remain_visible(self):
        source_id = "static_consequential"
        findings, dynamic = _dynamic(
            f"{{{{ is_state('{TARGET}', 'on') }}}}",
            source_id=source_id,
        )
        observed = _binding(
            _snapshot(
                findings=findings,
                dynamic=dynamic,
                profiles=(_profile(source_id, "cover.open_cover"),),
            )
        )
        risk = helper_dependency_risk_assessment(
            {
                "binding": observed,
                "provenance": {
                    "provider": "dependency_index",
                    "completeness": "complete",
                },
            }
        )

        self.assertTrue(observed["evidence_complete"])
        self.assertEqual(observed["physical_consequence"], "direct")
        self.assertEqual(risk.level, RiskLevel.HIGH)
        self.assertTrue(risk.apply_allowed)

    def test_known_entity_helper_callable_aliases_retain_exact_dependency(self):
        templates = (
            "{% set states = is_state %}"
            f"{{{{ states('{TARGET}', 'on') }}}}",
            "{% for states in [is_state] %}"
            f"{{{{ states('{TARGET}', 'on') }}}}"
            "{% endfor %}",
            "{% set lookup = states %}"
            f"{{{{ lookup('{TARGET}') }}}}",
            "{% set lookup = state_attr %}"
            f"{{{{ lookup('{TARGET}', 'friendly_name') }}}}",
            "{% set lookup = is_state_attr %}"
            f"{{{{ lookup('{TARGET}', 'mode', 'on') }}}}",
            "{% set lookup = has_value %}"
            f"{{{{ lookup('{TARGET}') }}}}",
            "{% set lookup = expand %}"
            f"{{{{ lookup('{TARGET}') }}}}",
            "{% set first = is_state %}{% set lookup = first %}"
            f"{{{{ lookup('{TARGET}', 'on') }}}}",
        )
        for index, template in enumerate(templates):
            with self.subTest(template=template):
                source_id = f"callable_alias_{index}"
                findings, dynamic = _dynamic(
                    template, source_id=source_id
                )
                observed = _binding(
                    _snapshot(
                        findings=findings,
                        dynamic=dynamic,
                        profiles=(
                            _profile(source_id, "cover.open_cover"),
                        ),
                    )
                )
                risk = helper_dependency_risk_assessment(
                    {
                        "binding": observed,
                        "provenance": {
                            "provider": "dependency_index",
                            "completeness": observed["completeness"],
                        },
                    }
                )

                self.assertEqual(
                    {item.target_entity_id for item in findings},
                    {TARGET},
                )
                self.assertEqual(dynamic, [])
                self.assertTrue(observed["evidence_complete"])
                self.assertEqual(
                    observed["relevant_downstream_object_ids"],
                    [f"automation.{source_id}"],
                )
                self.assertEqual(observed["physical_consequence"], "direct")
                self.assertEqual(risk.level, RiskLevel.HIGH)
                self.assertTrue(risk.apply_allowed)

    def test_unknown_callable_alias_remains_incomplete(self):
        templates = (
            "{% set states = unknown_callable %}"
            f"{{{{ states('{TARGET}') }}}}",
            "{% set lookup = unknown_callable %}"
            f"{{{{ lookup('{TARGET}') }}}}",
            "{% if enabled %}{% set lookup = is_state %}{% endif %}"
            f"{{{{ lookup('{TARGET}', 'on') }}}}",
        )
        for index, template in enumerate(templates):
            with self.subTest(template=template):
                findings, dynamic = _dynamic(
                    template,
                    source_id=f"unknown_callable_alias_{index}",
                )
                observed = _binding(_snapshot(dynamic=dynamic))

                self.assertEqual(findings, [])
                self.assertEqual(len(dynamic), 1)
                self.assertTrue(dynamic[0].entity_selector_present)
                self.assertFalse(
                    dynamic[0].candidate_resolution_complete
                )
                self.assertFalse(observed["evidence_complete"])
                self.assertFalse(observed["execution_eligible"])
                self.assertEqual(
                    observed[
                        "target_relevant_dynamic_reference_count"
                    ],
                    1,
                )

    def test_states_collection_aliases_retain_exact_dependency(self):
        templates = (
            "{% set lookup = states %}"
            f"{{{{ lookup['{TARGET}'] }}}}",
            "{% set first = states %}{% set lookup = first %}"
            f"{{{{ lookup['{TARGET}'] }}}}",
            "{% set helpers = {'lookup': states} %}"
            "{% set lookup = helpers.lookup %}"
            f"{{{{ lookup['{TARGET}'] }}}}",
            "{% set helpers = {'lookup': states} %}"
            "{% set lookup = helpers['lookup'] %}"
            f"{{{{ lookup['{TARGET}'] }}}}",
            "{% for lookup in [states] %}"
            f"{{{{ lookup['{TARGET}'] }}}}"
            "{% endfor %}",
            "{% set lookup = states %}"
            "{{ lookup.input_boolean."
            f"{TARGET.split('.', 1)[1]} }}}}",
        )
        for index, template in enumerate(templates):
            with self.subTest(template=template):
                source_id = f"states_collection_alias_{index}"
                findings, dynamic = _dynamic(
                    template, source_id=source_id
                )
                observed = _binding(
                    _snapshot(
                        findings=findings,
                        dynamic=dynamic,
                        profiles=(
                            _profile(source_id, "cover.open_cover"),
                        ),
                    )
                )
                risk = helper_dependency_risk_assessment(
                    {
                        "binding": observed,
                        "provenance": {
                            "provider": "dependency_index",
                            "completeness": observed["completeness"],
                        },
                    }
                )

                self.assertEqual(
                    {item.target_entity_id for item in findings},
                    {TARGET},
                )
                self.assertEqual(dynamic, [])
                self.assertTrue(observed["evidence_complete"])
                self.assertEqual(
                    observed["relevant_downstream_object_ids"],
                    [f"automation.{source_id}"],
                )
                self.assertEqual(
                    observed["physical_consequence"], "direct"
                )
                self.assertEqual(risk.level, RiskLevel.HIGH)
                self.assertTrue(risk.apply_allowed)

    def test_direct_mapping_member_helpers_retain_exact_dependency(self):
        templates = (
            "{% set helpers = {'lookup': states} %}"
            f"{{{{ helpers.lookup['{TARGET}'] }}}}",
            "{% set helpers = {'lookup': states} %}"
            f"{{{{ helpers['lookup']['{TARGET}'] }}}}",
            "{% set helpers = {'lookup': is_state} %}"
            f"{{{{ helpers.lookup('{TARGET}', 'on') }}}}",
            "{% set helpers = {'lookup': is_state} %}"
            f"{{{{ helpers['lookup']('{TARGET}', 'on') }}}}",
            "{% set helpers = {'nested': {'lookup': states}} %}"
            f"{{{{ helpers.nested.lookup['{TARGET}'] }}}}",
            "{% set helpers = {'nested': {'lookup': is_state}} %}"
            f"{{{{ helpers['nested']['lookup']('{TARGET}', 'on') }}}}",
        )
        for index, template in enumerate(templates):
            with self.subTest(template=template):
                source_id = f"direct_mapping_member_{index}"
                findings, dynamic = _dynamic(
                    template, source_id=source_id
                )
                observed = _binding(
                    _snapshot(
                        findings=findings,
                        dynamic=dynamic,
                        profiles=(
                            _profile(source_id, "cover.open_cover"),
                        ),
                    )
                )
                risk = helper_dependency_risk_assessment(
                    {
                        "binding": observed,
                        "provenance": {
                            "provider": "dependency_index",
                            "completeness": observed["completeness"],
                        },
                    }
                )

                self.assertEqual(
                    {item.target_entity_id for item in findings},
                    {TARGET},
                )
                self.assertEqual(dynamic, [])
                self.assertTrue(observed["evidence_complete"])
                self.assertEqual(
                    observed["relevant_downstream_object_ids"],
                    [f"automation.{source_id}"],
                )
                self.assertEqual(
                    observed["physical_consequence"], "direct"
                )
                self.assertEqual(risk.level, RiskLevel.HIGH)
                self.assertTrue(risk.apply_allowed)

    def test_uncertain_direct_mapping_members_remain_incomplete(self):
        templates = (
            "{% set helpers = {'lookup': unknown_callable} %}"
            f"{{{{ helpers.lookup('{TARGET}') }}}}",
            "{% set helpers = "
            "{'lookup': states if enabled else unknown_collection} %}"
            f"{{{{ helpers.lookup['{TARGET}'] }}}}",
            "{% set helpers = {'lookup': states} %}"
            f"{{{{ helpers[dynamic_key]['{TARGET}'] }}}}",
            "{% set helpers = {'lookup': states} %}"
            f"{{{{ helpers[dynamic_key]('{TARGET}') }}}}",
            "{% set helpers = {'nested': {'lookup': unknown_callable}} %}"
            f"{{{{ helpers.nested.lookup('{TARGET}') }}}}",
        )
        for index, template in enumerate(templates):
            with self.subTest(template=template):
                source_id = f"uncertain_mapping_member_{index}"
                findings, dynamic = _dynamic(
                    template, source_id=source_id
                )
                observed = _binding(
                    _snapshot(
                        findings=findings,
                        dynamic=dynamic,
                        profiles=(
                            _profile(source_id, "cover.open_cover"),
                        ),
                    )
                )
                risk = helper_dependency_risk_assessment(
                    {
                        "binding": observed,
                        "provenance": {
                            "provider": "dependency_index",
                            "completeness": observed["completeness"],
                        },
                    }
                )

                self.assertEqual(findings, [])
                self.assertTrue(dynamic)
                self.assertTrue(
                    all(item.entity_selector_present for item in dynamic)
                )
                self.assertFalse(observed["evidence_complete"])
                self.assertFalse(observed["execution_eligible"])
                self.assertEqual(risk.level, RiskLevel.HIGH)
                self.assertFalse(risk.apply_allowed)

    def test_direct_mapping_member_target_exclusion_and_ordinary_value(self):
        findings, dynamic = _dynamic(
            "{% set helpers = {'lookup': states} %}"
            "{{ helpers.lookup['sensor.unrelated'] }}",
            source_id="mapping_member_unrelated",
        )
        observed = _binding(
            _snapshot(
                findings=findings,
                dynamic=dynamic,
                profiles=(
                    _profile(
                        "mapping_member_unrelated", "cover.open_cover"
                    ),
                ),
            )
        )
        risk = helper_dependency_risk_assessment(
            {
                "binding": observed,
                "provenance": {
                    "provider": "dependency_index",
                    "completeness": observed["completeness"],
                },
            }
        )

        self.assertEqual(
            {item.target_entity_id for item in findings},
            {"sensor.unrelated"},
        )
        self.assertEqual(dynamic, [])
        self.assertTrue(observed["evidence_complete"])
        self.assertEqual(observed["relevant_downstream_object_ids"], [])
        self.assertEqual(risk.level, RiskLevel.LOW)
        self.assertTrue(risk.apply_allowed)

        ordinary_findings, ordinary_dynamic = _dynamic(
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.message }}",
            source_id="mapping_member_ordinary",
        )
        ordinary = _binding(
            _snapshot(
                findings=ordinary_findings,
                dynamic=ordinary_dynamic,
            )
        )
        self.assertEqual(ordinary_findings, [])
        self.assertEqual(ordinary_dynamic, [])
        self.assertTrue(ordinary["evidence_complete"])
        self.assertTrue(ordinary["execution_eligible"])

    def test_mapping_methods_follow_jinja_attribute_and_item_semantics(self):
        exact_templates = (
            "{% set helpers = {'get': 'ordinary', "
            f"'{TARGET}': states}} %}}"
            f"{{{{ helpers.get('{TARGET}')('{TARGET}') }}}}",
            "{% set helpers = {'get': 'ordinary', "
            f"'{TARGET}': states}} %}}"
            f"{{{{ helpers.get('{TARGET}')['{TARGET}'] }}}}",
            "{% set helpers = {'get': is_state} %}"
            f"{{{{ helpers['get']('{TARGET}', 'on') }}}}",
            "{% set helpers = {'values': states} %}"
            f"{{{{ helpers['values']['{TARGET}'] }}}}",
            "{% set helpers = {'items': is_state} %}"
            f"{{{{ helpers['items']('{TARGET}', 'on') }}}}",
            "{% set helpers = {'keys': states} %}"
            f"{{{{ helpers['keys']['{TARGET}'] }}}}",
            "{% set helpers = {'nested': {'lookup': is_state}} %}"
            f"{{{{ helpers.get('nested').get('lookup')('{TARGET}', 'on') }}}}",
            "{% set helpers = {} %}"
            f"{{{{ helpers.get('missing', is_state)('{TARGET}', 'on') }}}}",
            "{% set helpers = {'message': 'ready'} %}"
            f"{{{{ helpers.get('message', states('{TARGET}')) }}}}",
            "{% set helpers = {'lookup': is_state} %}"
            "{% for lookup in helpers.values() %}"
            f"{{{{ lookup('{TARGET}', 'on') }}}}"
            "{% endfor %}",
            f"{{% set helpers = {{'{TARGET}': 'ordinary'}} %}}"
            "{% for entity in helpers.keys() %}"
            "{{ states(entity) }}{% endfor %}",
            f"{{% set helpers = {{'{TARGET}': states}} %}}"
            "{% set getter = helpers.get %}"
            f"{{{{ getter('{TARGET}')('{TARGET}') }}}}",
            f"{{% set helpers = {{'{TARGET}': states}} %}}"
            "{% set getter = helpers.get %}"
            f"{{{{ getter('{TARGET}')['{TARGET}'] }}}}",
            "{% set helpers = {'lookup': is_state} %}"
            "{% set values = helpers.values %}"
            "{% for lookup in values() %}"
            f"{{{{ lookup('{TARGET}', 'on') }}}}"
            "{% endfor %}",
            f"{{% set helpers = {{'{TARGET}': 'ordinary'}} %}}"
            "{% set keys = helpers.keys %}"
            "{% for entity in keys() %}"
            "{{ states(entity) }}{% endfor %}",
            f"{{% set source = {{'{TARGET}': states}} %}}"
            "{% set methods = {'getter': source.get} %}"
            f"{{{{ methods.getter('{TARGET}')('{TARGET}') }}}}",
            f"{{% set source = {{'{TARGET}': states}} %}}"
            "{% set methods = {'getter': source.get} %}"
            f"{{{{ methods['getter']('{TARGET}')['{TARGET}'] }}}}",
            f"{{% set source = {{'{TARGET}': states}} %}}"
            "{% set methods = {'getter': source.get} %}"
            f"{{{{ methods.get('getter')('{TARGET}')['{TARGET}'] }}}}",
            "{% set helpers = {} %}"
            f"{{{{ helpers.get('missing', states)('{TARGET}') }}}}",
            "{% set helpers = {} %}"
            f"{{{{ helpers.get('missing', states)['{TARGET}'] }}}}",
            f"{{% set helpers = {{'{TARGET}': 'ordinary'}} %}}"
            "{% for entity in helpers['keys']() %}"
            "{{ states(entity) }}{% endfor %}",
            "{% set helpers = {'lookup': is_state} %}"
            "{% for lookup in helpers['values']() %}"
            f"{{{{ lookup('{TARGET}', 'on') }}}}"
            "{% endfor %}",
            f"{{% set with_value = {{'{TARGET}': 'ordinary'}} %}}"
            "{% set without_value = {} %}"
            "{% set getter = "
            "with_value.get if enabled else without_value.get %}"
            f"{{{{ getter('{TARGET}', states)['{TARGET}'] }}}}",
            f"{{% set helpers = {{'{TARGET}': 'ordinary'}} "
            "if enabled else {} %}"
            f"{{{{ helpers.get('{TARGET}', states)['{TARGET}'] }}}}",
            f"{{% set source = {{'{TARGET}': states}} %}}"
            "{% set level1 = {'next': source.get} %}"
            "{% set level2 = {'next': level1.get} %}"
            "{{ level2.get('next')('next')"
            f"('{TARGET}')('{TARGET}') }}}}",
        )
        for index, template in enumerate(exact_templates):
            with self.subTest(kind="exact", template=template):
                source_id = f"mapping_method_exact_{index}"
                findings, dynamic = _dynamic(template, source_id=source_id)
                observed = _binding(
                    _snapshot(
                        findings=findings,
                        dynamic=dynamic,
                        profiles=(
                            _profile(source_id, "cover.open_cover"),
                        ),
                    )
                )
                risk = helper_dependency_risk_assessment(
                    {
                        "binding": observed,
                        "provenance": {
                            "provider": "dependency_index",
                            "completeness": observed["completeness"],
                        },
                    }
                )

                exact_candidates = {
                    item.target_entity_id for item in findings
                }.union(
                    entity_id
                    for item in dynamic
                    for entity_id in item.possible_entity_ids
                )
                self.assertEqual(exact_candidates, {TARGET})
                self.assertTrue(
                    all(
                        item.candidate_resolution_complete
                        for item in dynamic
                    )
                )
                self.assertTrue(observed["evidence_complete"])
                self.assertEqual(observed["physical_consequence"], "direct")
                self.assertEqual(risk.level, RiskLevel.HIGH)
                self.assertTrue(risk.apply_allowed)

        uncertain_templates = (
            "{% set helpers = {'message': 'ready', 'lookup': states} %}"
            f"{{{{ helpers.get(dynamic_key)('{TARGET}') }}}}",
            "{% set helpers = {'lookup': states} %}"
            "{{ helpers.items() | list }}",
            "{% set helpers = {'lookup': states} %}"
            "{{ helpers.values() | list }}",
            "{% set helpers = {} %}"
            f"{{{{ helpers.get('missing', unknown_callable)('{TARGET}') }}}}",
            "{% set helpers = {'nested': {'lookup': unknown_callable}} %}"
            f"{{{{ helpers.get('nested').get('lookup')('{TARGET}') }}}}",
            "{% set helpers = {'message': 'ready', 'lookup': states} %}"
            "{% set getter = helpers.get %}"
            f"{{{{ getter(dynamic_key)('{TARGET}') }}}}",
            "{% set helpers = {'lookup': states} %}"
            "{% set items = helpers.items %}"
            "{{ items() | list }}",
            "{% set helpers = {'lookup': states} %}"
            "{% set values = helpers.values if enabled else unknown_callable %}"
            "{{ values() | list }}",
            "{% set source = {'lookup': states} %}"
            "{% set methods = "
            "{'getter': source.get if enabled else unknown_callable} %}"
            f"{{{{ methods.getter('lookup')('{TARGET}') }}}}",
            "{% set source = {'lookup': states} %}"
            "{% set methods = {'getter': source.get} %}"
            f"{{{{ methods[dynamic_key]('lookup')('{TARGET}') }}}}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.get('message', states | list) }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.get(states | list, 'fallback') }}",
            f"{{% set helpers = {{'{TARGET}': states}} %}}"
            f"{{{{ (helpers | attr('get'))('{TARGET}')('{TARGET}') }}}}",
            f"{{% set helpers = {{'{TARGET}': states}} %}}"
            f"{{{{ ((helpers) | attr('get'))('{TARGET}')('{TARGET}') }}}}",
            f"{{% set source = {{'{TARGET}': states}} %}}"
            "{% set outer = {'source': source} %}"
            "{{ (outer['source'] | attr('get'))"
            f"('{TARGET}')('{TARGET}') }}}}",
            f"{{% set source = {{'{TARGET}': states}} %}}"
            "{% set outer = {'source': source} %}"
            "{{ (outer.get('source') | attr('get'))"
            f"('{TARGET}')('{TARGET}') }}}}",
            f"{{% set source = {{'{TARGET}': states}} %}}"
            "{% set containers = [source] %}"
            "{{ (containers | map(attribute='get') | first)"
            f"('{TARGET}')('{TARGET}') }}}}",
            f"{{% set source = {{'{TARGET}': states}} %}}"
            "{% set containers = [source] %}"
            "{{ (containers | map('attr', 'get') | first)"
            f"('{TARGET}')('{TARGET}') }}}}",
            f"{{% set source = {{'{TARGET}': states}} %}}"
            "{% set containers = [source] %}"
            "{{ (containers | map(attribute=method_name) | first)"
            f"('{TARGET}')('{TARGET}') }}}}",
            f"{{% set source = {{'lookup': states}} %}}"
            "{% set containers = [source] %}"
            "{{ (containers | map(attribute='lookup') | first)"
            f"('{TARGET}') }}}}",
            f"{{% set source = {{'nested': {{'lookup': is_state}}}} %}}"
            "{% set containers = [source] %}"
            "{{ (containers | map(attribute='nested.lookup') | first)"
            f"('{TARGET}', 'on') }}}}",
            f"{{% set containers = [{{'{TARGET}': states}}] %}}"
            "{{ (containers | first)"
            f".get('{TARGET}')['{TARGET}'] }}}}",
            f"{{% set containers = [{{'{TARGET}': states}}] %}}"
            "{{ (containers | select | first)"
            f".get('{TARGET}')['{TARGET}'] }}}}",
            f"{{% set containers = [{{'{TARGET}': states}}] %}}"
            "{{ (containers | reject | first)"
            f".get('{TARGET}')['{TARGET}'] }}}}",
            f"{{% set containers = [{{'{TARGET}': states}}] %}}"
            "{{ (containers | selectattr('message', 'defined') | first)"
            f".get('{TARGET}')['{TARGET}'] }}}}",
            f"{{% set containers = [{{'{TARGET}': states}}] %}}"
            "{{ (containers | rejectattr('message', 'defined') | first)"
            f".get('{TARGET}')['{TARGET}'] }}}}",
            f"{{% set containers = [{{'{TARGET}': states}}] %}}"
            "{{ ((containers | first) if enabled else {})"
            f".get('{TARGET}')['{TARGET}'] }}}}",
            f"{{% set containers = [{{'{TARGET}': states}}] %}}"
            "{{ [(containers | first)][0]"
            f".get('{TARGET}')['{TARGET}'] }}}}",
            f"{{% set containers = [{{'{TARGET}': states}}] %}}"
            "{{ {'selected': (containers | first)}['selected']"
            f".get('{TARGET}')['{TARGET}'] }}}}",
            f"{{% set containers = [{{'{TARGET}': states}}] %}}"
            "{% set fallback = containers[0] %}"
            "{{ (containers | map(attribute='missing', default=fallback)"
            " | first)"
            f".get('{TARGET}')['{TARGET}'] }}}}",
            f"{{% set source = {{'{TARGET}': states}} %}}"
            "{% set fallback = source.get %}{% set containers = [{}] %}"
            "{{ (containers | map(attribute='missing', default=fallback)"
            f" | first)('{TARGET}')['{TARGET}'] }}}}",
            "{% set containers = [{}] %}"
            "{{ (containers | map(attribute='missing', default=unknown)"
            f" | first)('{TARGET}') }}}}",
            "{% set containers = [{'message': 'ready'}] %}"
            "{{ (containers | unknown_filter | first)"
            f"('{TARGET}') }}}}",
            "{% set containers = [{'lookup': states}] %}"
            f"{{{{ (containers | first].lookup['{TARGET}'] }}}}",
            "{% set containers = [{'lookup': states}] %}"
            f"{{{{ [containers | first).lookup['{TARGET}'] }}}}",
            "{% set containers = [{'lookup': states}] %}"
            f"{{{{ {{containers | first].lookup['{TARGET}'] }}}}",
            "{% set containers = [{}] %}"
            f"{{% set fallback = {{'{TARGET}': states}} %}}"
            "{{ ((containers | groupby('missing', default=fallback)"
            f" | first)[0]).get('{TARGET}')['{TARGET}'] }}}}",
            "{% set messages = ['ready'] %}"
            f"{{{{ (messages | batch(2, states) | first)[1]('{TARGET}') }}}}",
            "{% set messages = ['ready'] %}"
            f"{{{{ (messages | slice(2, states) | list | first)[1]('{TARGET}') }}}}",
            "{% set value = '' %}"
            "{{ (value | default(default_value=states, boolean=true))"
            f"('{TARGET}') }}}}",
            "{% set value = 'ready' %}"
            "{{ (value | default('fallback', true, boolean=false))"
            ".upper() }}",
            "{% set helpers = {'lookup': 'ordinary'} "
            "if enabled else unknown_mapping %}"
            "{% set getter = helpers.get %}"
            f"{{{{ getter('lookup')('{TARGET}') }}}}",
        )
        for index, template in enumerate(uncertain_templates):
            with self.subTest(kind="uncertain", template=template):
                findings, dynamic = _dynamic(
                    template,
                    source_id=f"mapping_method_uncertain_{index}",
                )
                observed = _binding(_snapshot(dynamic=dynamic))

                self.assertEqual(findings, [])
                self.assertTrue(dynamic)
                self.assertTrue(
                    all(item.entity_selector_present for item in dynamic)
                )
                self.assertFalse(observed["evidence_complete"])
                self.assertFalse(observed["execution_eligible"])

        ordinary_templates = (
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.get('message') }}",
            "{% set helpers = {'get': states, 'message': 'ready'} %}"
            "{{ helpers.get('message') }}",
            "{% set helpers = {'get': 'ready'} %}"
            "{{ helpers['get'] }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.get(dynamic_key, 'fallback') }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.items() | list }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.values() | list }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.keys() | list }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{% set getter = helpers.get %}"
            "{{ getter('message') }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{% set items = helpers.items %}"
            "{{ items() | list }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{% set values = helpers.values %}"
            "{{ values() | list }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{% set keys = helpers.keys %}"
            "{{ keys() | list }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.get('message', states) }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers['get']('message') }}",
            "{% set source = {'message': 'ready'} %}"
            "{% set methods = {'getter': source.get} %}"
            "{{ methods.getter('message') }}",
            "{% set source = {'message': 'ready'} %}"
            "{% set methods = {'getter': source.get} %}"
            "{{ methods['getter']('message') }}",
            "{% set with_value = {'message': 'ready'} %}"
            "{% set without_value = {} %}"
            "{% set getter = "
            "with_value.get if enabled else without_value.get %}"
            "{{ getter('message', 'fallback') }}",
            f"{{% set first = {{'{TARGET}': 'ordinary'}} %}}"
            f"{{% set second = {{'{TARGET}': 'ordinary'}} %}}"
            "{% set getter = first.get if enabled else second.get %}"
            f"{{{{ getter('{TARGET}', states) }}}}",
            "{{ \"example | attr('get')\" }}",
            "{{ 'documentation: | attr(' }}",
            "{% set containers = [{'message': 'ready'}] %}"
            "{{ (containers | first).get('message') }}",
            "{% set containers = [{'message': 'ready'}] %}"
            "{{ (containers | selectattr('message', 'defined') | first)"
            ".get('message') }}",
            "{% set containers = [{'message': 'ready'}] %}"
            "{{ containers | map(attribute='message') | list }}",
            "{% set containers = [{'message': 'ready'}] %}"
            "{{ [((containers | first) if enabled else {})][0]"
            ".get('message', 'fallback') }}",
            "{% set containers = [{}] %}"
            "{{ (containers | map(attribute='message', default='ready')"
            " | first) }}",
            "{% set containers = "
            "[{'priority': 1, 'message': 'ready'}] %}"
            "{{ (containers | selectattr('priority', 'eq', 1) | first)"
            ".message }}",
            "{% set containers = [{'message': 'ready'}] %}"
            "{{ (containers | sort(reverse=true) | first).message }}",
            "{% set messages = ['ready', 'done'] %}"
            "{{ (messages | batch(2) | first)[0] }}",
            "{% set value = 'ready' %}"
            "{{ (value | default('fallback', use_boolean)).upper() }}",
            "{% set value = 'ready' %}"
            "{{ (value | default(default_value='fallback')).upper() }}",
            "{% set value = 'ready' %}"
            "{{ (value | default(boolean=true)).upper() }}",
            "{% set value = 'ready' %}"
            "{{ (value | default(default_value='fallback', "
            "boolean=use_boolean)).upper() }}",
            "{% set containers = [{}] %}"
            "{{ (containers | groupby('missing', default='ordinary')"
            " | first)[0] }}",
            "{% set messages = ['ready'] %}"
            "{{ (messages | batch(2, 'ordinary') | first)[1] }}",
            "{% set messages = ['ready'] %}"
            "{{ (messages | slice(2, 'ordinary') | list | first)[1] }}",
        )
        for index, template in enumerate(ordinary_templates):
            with self.subTest(kind="ordinary", template=template):
                findings, dynamic = _dynamic(
                    template,
                    source_id=f"mapping_method_ordinary_{index}",
                )
                observed = _binding(_snapshot(dynamic=dynamic))

                self.assertEqual(findings, [])
                self.assertEqual(dynamic, [])
                self.assertTrue(observed["evidence_complete"])
                self.assertTrue(observed["execution_eligible"])

    def test_dynamic_bracket_method_fallback_is_conservative(self):
        templates = (
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers[dynamic_key]("
            "'missing', is_state)("
            f"'{TARGET}', 'on') }}}}",
            "{% set helpers = {'get': 'ordinary', "
            "'items': 'ordinary', 'keys': 'ordinary', "
            "'values': 'ordinary', 'message': 'ready'} %}"
            "{{ helpers[dynamic_key]().get("
            "'missing', is_state)("
            f"'{TARGET}', 'on') }}}}",
            "{% set helpers = {'message': 'ready'} %}"
            f"{{{{ helpers[dynamic_key](*['missing', is_state])"
            f"('{TARGET}', 'on') }}}}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.get(*('missing', is_state))("
            f"'{TARGET}', 'on') }}}}",
        )
        for index, template in enumerate(templates):
            with self.subTest(template=template):
                source_id = f"dynamic_bracket_method_fallback_{index}"
                findings, dynamic = _dynamic(
                    template,
                    source_id=source_id,
                )
                observed = _binding(
                    _snapshot(
                        findings=findings,
                        dynamic=dynamic,
                        profiles=(
                            _profile(source_id, "cover.open_cover"),
                        ),
                    )
                )
                risk = helper_dependency_risk_assessment(
                    {
                        "binding": observed,
                        "provenance": {
                            "provider": "dependency_index",
                            "completeness": observed["completeness"],
                        },
                    }
                )

                self.assertEqual(findings, [])
                self.assertTrue(dynamic)
                self.assertTrue(
                    any(
                        item.entity_selector_present
                        and not item.candidate_resolution_complete
                        for item in dynamic
                    )
                )
                self.assertFalse(observed["evidence_complete"])
                self.assertFalse(observed["execution_eligible"])
                self.assertGreater(
                    observed[
                        "target_relevant_dynamic_reference_count"
                    ],
                    0,
                )
                self.assertEqual(
                    observed["physical_consequence"], "unknown"
                )
                self.assertEqual(risk.level, RiskLevel.HIGH)
                self.assertFalse(risk.apply_allowed)

    def test_dynamic_bracket_option_analysis_is_bounded(self):
        repeats = 1_024
        template = (
            "{% set helpers = {'message': 'ready'} %}"
            + "{% set helpers = {'x': helpers[dynamic_key]} %}"
            * repeats
            + "{{ helpers[dynamic_key]('missing', is_state)("
            f"'{TARGET}', 'on') }}}}"
        )
        self.assertLess(len(template), MAX_TEMPLATE_SEGMENT_CHARS)

        started = time.perf_counter()
        findings, dynamic = _dynamic(
            template,
            source_id="dynamic_bracket_option_bound",
        )
        elapsed = time.perf_counter() - started
        observed = _binding(_snapshot(dynamic=dynamic))

        self.assertEqual(findings, [])
        self.assertEqual(len(dynamic), 1)
        self.assertTrue(dynamic[0].entity_selector_present)
        self.assertFalse(dynamic[0].candidate_resolution_complete)
        self.assertFalse(observed["evidence_complete"])
        self.assertFalse(observed["execution_eligible"])
        self.assertLess(elapsed, 1.0)

    def test_bounded_get_key_retains_exact_helper_dependency(self):
        source_id = "bounded_dynamic_get_key"
        findings, dynamic = _dynamic(
            "{% set helpers = {'message': 'ready'} %}"
            "{% set dynamic_key = 'get' %}"
            "{{ helpers[dynamic_key]("
            "'missing', is_state)("
            f"'{TARGET}', 'on') }}}}",
            source_id=source_id,
        )
        observed = _binding(
            _snapshot(
                findings=findings,
                dynamic=dynamic,
                profiles=(_profile(source_id, "cover.open_cover"),),
            )
        )
        risk = helper_dependency_risk_assessment(
            {
                "binding": observed,
                "provenance": {
                    "provider": "dependency_index",
                    "completeness": observed["completeness"],
                },
            }
        )
        candidates = {
            item.target_entity_id for item in findings
        }.union(
            entity_id
            for item in dynamic
            for entity_id in item.possible_entity_ids
        )

        self.assertEqual(candidates, {TARGET})
        self.assertTrue(
            all(item.candidate_resolution_complete for item in dynamic)
        )
        self.assertTrue(observed["evidence_complete"])
        self.assertTrue(observed["execution_eligible"])
        self.assertEqual(
            observed["relevant_downstream_object_ids"],
            [f"automation.{source_id}"],
        )
        self.assertEqual(observed["physical_consequence"], "direct")
        self.assertEqual(risk.level, RiskLevel.HIGH)
        self.assertTrue(risk.apply_allowed)

    def test_dynamic_bracket_preserves_proven_ordinary_paths(self):
        templates = (
            "{% set helpers = {'message': 'ready', 'title': 'done'} %}"
            "{% set dynamic_key = 'message' if enabled else 'title' %}"
            "{{ helpers[dynamic_key] }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{% set dynamic_key = 'get' %}"
            "{{ helpers[dynamic_key]('missing', 'fallback') }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{% set dynamic_key = 'keys' if enabled else 'values' %}"
            "{{ helpers[dynamic_key]() | list }}",
            "{% set helpers = {'get': 'ordinary'} %}"
            "{{ helpers['get'] }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.get('message') }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.items() | list }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.values() | list }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.keys() | list }}",
        )

        for index, template in enumerate(templates):
            with self.subTest(template=template):
                findings, dynamic = _dynamic(
                    template,
                    source_id=f"dynamic_bracket_ordinary_{index}",
                )
                observed = _binding(_snapshot(dynamic=dynamic))

                self.assertEqual(findings, [])
                self.assertEqual(dynamic, [])
                self.assertTrue(observed["evidence_complete"])
                self.assertTrue(observed["execution_eligible"])

    def test_bracket_key_entity_reads_are_never_skipped(self):
        exact_templates = (
            "{% set helpers = {'message': 'ready'} %}"
            f"{{{{ helpers[states('{TARGET}')] }}}}",
            "{% set helpers = {'message': 'ready'} %}"
            f"{{{{ helpers[is_state('{TARGET}', 'on')] }}}}",
            "{% set helpers = {'message': 'ready'} %}"
            "{% set lookup = is_state %}"
            f"{{{{ helpers[lookup('{TARGET}', 'on')] }}}}",
        )
        for index, template in enumerate(exact_templates):
            with self.subTest(kind="exact", template=template):
                findings, dynamic = _dynamic(
                    template,
                    source_id=f"bracket_key_exact_{index}",
                )
                candidates = {
                    item.target_entity_id for item in findings
                }.union(
                    entity_id
                    for item in dynamic
                    for entity_id in item.possible_entity_ids
                )
                self.assertEqual(candidates, {TARGET})

        findings, dynamic = _dynamic(
            "{% set helpers = {'message': 'ready'} %}"
            "{% set lookup = unknown_callable %}"
            f"{{{{ helpers[lookup('{TARGET}', 'on')] }}}}",
            source_id="bracket_key_unknown_alias",
        )
        observed = _binding(_snapshot(dynamic=dynamic))

        self.assertEqual(findings, [])
        self.assertTrue(dynamic)
        self.assertTrue(
            all(item.entity_selector_present for item in dynamic)
        )
        self.assertFalse(observed["evidence_complete"])
        self.assertFalse(observed["execution_eligible"])

    def test_dynamic_bracket_fallback_changes_approval_binding(self):
        source_id = "dynamic_bracket_fallback_drift"
        before_findings, before_dynamic = _dynamic(
            "{% set helpers = {'message': 'ready', 'title': 'done'} %}"
            "{% set dynamic_key = 'message' if enabled else 'title' %}"
            "{{ helpers[dynamic_key] }}",
            source_id=source_id,
        )
        after_findings, after_dynamic = _dynamic(
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers[dynamic_key]("
            "'missing', is_state)("
            f"'{TARGET}', 'on') }}}}",
            source_id=source_id,
        )
        before = _binding(
            _snapshot(
                findings=before_findings,
                dynamic=before_dynamic,
                profiles=(_profile(source_id),),
            )
        )
        after = _binding(
            _snapshot(
                findings=after_findings,
                dynamic=after_dynamic,
                profiles=(_profile(source_id),),
            )
        )

        self.assertTrue(before["evidence_complete"])
        self.assertFalse(after["evidence_complete"])
        self.assertNotEqual(
            before["evidence_fingerprint"],
            after["evidence_fingerprint"],
        )

    def test_grouped_and_finite_callable_transport_preserves_provenance(self):
        method_forms = (
            "(helpers[dynamic_key])",
            "((helpers[dynamic_key]))",
            "[helpers[dynamic_key]][0]",
            "(helpers[dynamic_key],)[0]",
            "{'x': helpers[dynamic_key]}['x']",
            "(helpers[dynamic_key] if enabled else helpers[dynamic_key])",
        )
        exact_prefix = (
            "{% set helpers = {'message': 'ready'} %}"
            "{% set dynamic_key = 'get' %}"
        )
        uncertain_prefix = "{% set helpers = {'message': 'ready'} %}"
        for index, form in enumerate(method_forms):
            with self.subTest(kind="exact", form=form):
                source_id = f"transport_exact_{index}"
                findings, dynamic = _dynamic(
                    exact_prefix
                    + "{{ "
                    + form
                    + "('missing', is_state)('"
                    + TARGET
                    + "', 'on') }}",
                    source_id=source_id,
                )
                observed = _binding(
                    _snapshot(
                        findings=findings,
                        dynamic=dynamic,
                        profiles=(
                            _profile(source_id, "cover.open_cover"),
                        ),
                    )
                )
                risk = helper_dependency_risk_assessment(
                    {
                        "binding": observed,
                        "provenance": {
                            "provider": "dependency_index",
                            "completeness": observed["completeness"],
                        },
                    }
                )
                self.assertEqual(
                    {item.target_entity_id for item in findings},
                    {TARGET},
                )
                self.assertEqual(dynamic, [])
                self.assertTrue(observed["evidence_complete"])
                self.assertEqual(observed["physical_consequence"], "direct")
                self.assertEqual(risk.level, RiskLevel.HIGH)
                self.assertTrue(risk.apply_allowed)

            with self.subTest(kind="uncertain", form=form):
                source_id = f"transport_uncertain_{index}"
                findings, dynamic = _dynamic(
                    uncertain_prefix
                    + "{{ "
                    + form
                    + "('missing', is_state)('"
                    + TARGET
                    + "', 'on') }}",
                    source_id=source_id,
                )
                observed = _binding(
                    _snapshot(
                        findings=findings,
                        dynamic=dynamic,
                        profiles=(
                            _profile(source_id, "cover.open_cover"),
                        ),
                    )
                )
                risk = helper_dependency_risk_assessment(
                    {
                        "binding": observed,
                        "provenance": {
                            "provider": "dependency_index",
                            "completeness": observed["completeness"],
                        },
                    }
                )
                self.assertEqual(findings, [])
                self.assertTrue(dynamic)
                self.assertTrue(
                    all(item.entity_selector_present for item in dynamic)
                )
                self.assertTrue(
                    all(
                        not item.candidate_resolution_complete
                        for item in dynamic
                    )
                )
                self.assertFalse(observed["evidence_complete"])
                self.assertFalse(observed["execution_eligible"])
                self.assertEqual(risk.level, RiskLevel.HIGH)
                self.assertFalse(risk.apply_allowed)

        nested_operator_forms = (
            "((helpers.get)('missing', is_state)("
            f"'{TARGET}', 'on')) | bool",
            "'prefix' ~ (helpers.get)('missing', is_state)("
            f"'{TARGET}', 'on')",
        )
        for index, expression in enumerate(nested_operator_forms):
            findings, dynamic = _dynamic(
                uncertain_prefix + "{{ " + expression + " }}",
                source_id=f"transport_operator_exact_{index}",
            )
            self.assertEqual(
                {item.target_entity_id for item in findings}, {TARGET}
            )
            self.assertEqual(dynamic, [])

        unresolved_nested = (
            "(helpers[dynamic_key])('missing', is_state)("
            f"'{TARGET}', 'on') if enabled else ('ready' | upper)",
            "unknown(((helpers.get)('missing', is_state)("
            f"'{TARGET}', 'on')) | bool)",
            "unknown('x' ~ (helpers.get)('missing', is_state)("
            f"'{TARGET}', 'on'))",
        )
        for index, expression in enumerate(unresolved_nested):
            findings, dynamic = _dynamic(
                uncertain_prefix + "{{ " + expression + " }}",
                source_id=f"transport_operator_uncertain_{index}",
            )
            observed = _binding(_snapshot(dynamic=dynamic))
            self.assertEqual(findings, [])
            self.assertTrue(dynamic)
            self.assertFalse(observed["evidence_complete"])
            self.assertFalse(observed["execution_eligible"])

    def test_canonical_helpers_and_ordinary_values_survive_finite_transport(self):
        exact_forms = (
            f"(is_state)('{TARGET}', 'on')",
            f"[is_state][0]('{TARGET}', 'on')",
            f"(is_state,)[0]('{TARGET}', 'on')",
            f"{{'x': is_state}}['x']('{TARGET}', 'on')",
            f"(is_state if enabled else is_state)('{TARGET}', 'on')",
            f"[states][0]['{TARGET}']",
            f"(states,)[0]['{TARGET}']",
            f"{{'x': states}}['x']['{TARGET}']",
            f"(states if enabled else states)['{TARGET}']",
            "['ordinary', helpers.get][-1]('missing', is_state)"
            f"('{TARGET}', 'on')",
            "(helpers.get)('missing', is_state)"
            f"('{TARGET}', 'on')",
            "(helpers['get'])('missing', is_state)"
            f"('{TARGET}', 'on')",
        )
        prefix = "{% set helpers = {'message': 'ready'} %}"
        for index, expression in enumerate(exact_forms):
            with self.subTest(kind="exact", expression=expression):
                findings, dynamic = _dynamic(
                    prefix + "{{ " + expression + " }}",
                    source_id=f"canonical_transport_{index}",
                )
                self.assertEqual(
                    {item.target_entity_id for item in findings},
                    {TARGET},
                )
                self.assertEqual(dynamic, [])

        ordinary_forms = (
            "[helpers.get('message')][0]",
            "(helpers.get('message'),)[0]",
            "{'x': helpers.get('message')}['x']",
            "(helpers.get('message') if enabled else 'ready')",
            "helpers.get('message').upper()",
            "(helpers.get)('message').split(',')",
        )
        for index, expression in enumerate(ordinary_forms):
            with self.subTest(kind="ordinary", expression=expression):
                findings, dynamic = _dynamic(
                    prefix + "{{ " + expression + " }}",
                    source_id=f"ordinary_transport_{index}",
                )
                observed = _binding(_snapshot(dynamic=dynamic))
                self.assertEqual(findings, [])
                self.assertEqual(dynamic, [])
                self.assertTrue(observed["evidence_complete"])
                self.assertTrue(observed["execution_eligible"])

    def test_transported_states_suppression_is_token_scoped(self):
        source_id = "transported_and_bare_states"
        findings, dynamic = _dynamic(
            "{{ ([states][0]['"
            + TARGET
            + "'], states) }}",
            source_id=source_id,
        )
        observed = _binding(
            _snapshot(
                findings=findings,
                dynamic=dynamic,
                profiles=(
                    _profile(source_id, "cover.open_cover"),
                ),
            )
        )

        self.assertEqual(
            {item.target_entity_id for item in findings},
            {TARGET},
        )
        self.assertEqual(len(dynamic), 1)
        self.assertEqual(
            dynamic[0].reference_kind,
            "dynamic_entity_selector",
        )
        self.assertTrue(dynamic[0].entity_selector_present)
        self.assertFalse(observed["evidence_complete"])
        self.assertFalse(observed["execution_eligible"])
        self.assertEqual(observed["physical_consequence"], "direct")

        finite_findings, finite_dynamic = _dynamic(
            "{% set selected = 'sensor.a' %}"
            "{{ [states][0][selected] }}",
            source_id="transported_finite_states",
        )
        self.assertEqual(finite_findings, [])
        self.assertEqual(len(finite_dynamic), 1)
        self.assertTrue(finite_dynamic[0].candidate_resolution_complete)
        self.assertEqual(
            finite_dynamic[0].possible_entity_ids,
            ("sensor.a",),
        )

        conditional_source = "transported_states_control_operand"
        conditional_findings, conditional_dynamic = _dynamic(
            "{{ (states if '"
            + TARGET
            + "' in states else states)['sensor.a'] }}",
            source_id=conditional_source,
        )
        conditional = _binding(
            _snapshot(
                findings=conditional_findings,
                dynamic=conditional_dynamic,
                profiles=(
                    _profile(
                        conditional_source,
                        "cover.open_cover",
                    ),
                ),
            )
        )
        self.assertEqual(
            {item.target_entity_id for item in conditional_findings},
            {"sensor.a"},
        )
        self.assertEqual(len(conditional_dynamic), 1)
        self.assertFalse(conditional["evidence_complete"])
        self.assertFalse(conditional["execution_eligible"])
        self.assertEqual(conditional["physical_consequence"], "unknown")

        mixed_findings, mixed_dynamic = _dynamic(
            "{{ (states if enabled else 'ordinary')['sensor.a'] }}",
            source_id="transported_states_mixed_value",
        )
        mixed = _binding(
            _snapshot(
                findings=mixed_findings,
                dynamic=mixed_dynamic,
            )
        )
        self.assertTrue(mixed_dynamic)
        self.assertFalse(mixed["evidence_complete"])
        self.assertFalse(mixed["execution_eligible"])

        key_findings, key_dynamic = _dynamic(
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.get(states, 'ordinary') }}",
            source_id="transported_states_lookup_key",
        )
        key_binding = _binding(
            _snapshot(
                findings=key_findings,
                dynamic=key_dynamic,
            )
        )
        self.assertEqual(key_findings, [])
        self.assertTrue(key_dynamic)
        self.assertFalse(key_binding["evidence_complete"])
        self.assertFalse(key_binding["execution_eligible"])

    def test_method_returned_helpers_transport_remains_exact(self):
        prefix = "{% set helpers = {'message': 'ready'} %}"
        expressions = (
            "helpers.get('missing', states)('" + TARGET + "')",
            "[helpers.get('missing', states)][0]('" + TARGET + "')",
            "(helpers.get('missing', states),)[0]('" + TARGET + "')",
            "{'x': helpers.get('missing', states)}['x']('"
            + TARGET
            + "')",
            "(helpers.get('missing', states) if enabled else states)('"
            + TARGET
            + "')",
            "helpers.get('missing', helpers.get('missing2', states))('"
            + TARGET
            + "')",
            "(helpers.get('missing', is_state))('"
            + TARGET
            + "', 'on')",
            "[helpers.get('missing', is_state)][0]('"
            + TARGET
            + "', 'on')",
            "(helpers.get('missing', is_state),)[0]('"
            + TARGET
            + "', 'on')",
            "{'x': helpers.get('missing', is_state)}['x']('"
            + TARGET
            + "', 'on')",
            "(helpers.get('missing', is_state) if enabled else is_state)('"
            + TARGET
            + "', 'on')",
        )
        for index, expression in enumerate(expressions):
            with self.subTest(expression=expression):
                findings, dynamic = _dynamic(
                    prefix + "{{ " + expression + " }}",
                    source_id=f"returned_helper_transport_{index}",
                )
                self.assertEqual(
                    {item.target_entity_id for item in findings},
                    {TARGET},
                )
                self.assertEqual(dynamic, [])

    def test_transport_drift_and_bounds_are_explicit(self):
        source_id = "transport_drift"
        before_findings, before_dynamic = _dynamic(
            "{% set helpers = {'message': 'ready'} %}"
            "{{ [helpers.get('message')][0] }}",
            source_id=source_id,
        )
        after_findings, after_dynamic = _dynamic(
            "{% set helpers = {'message': 'ready'} %}"
            "{{ [helpers[dynamic_key]][0]('missing', is_state)("
            f"'{TARGET}', 'on') }}}}",
            source_id=source_id,
        )
        before = _binding(
            _snapshot(
                findings=before_findings,
                dynamic=before_dynamic,
                profiles=(_profile(source_id),),
            )
        )
        after = _binding(
            _snapshot(
                findings=after_findings,
                dynamic=after_dynamic,
                profiles=(_profile(source_id),),
            )
        )
        self.assertTrue(before["evidence_complete"])
        self.assertFalse(after["evidence_complete"])
        self.assertNotEqual(
            before["evidence_fingerprint"],
            after["evidence_fingerprint"],
        )

        deep = (
            "{% set helpers = {'message': 'ready'} %}{{ "
            + "(" * 64
            + "helpers.get"
            + ")" * 64
            + "('missing', is_state)('"
            + TARGET
            + "', 'on') }}"
        )
        malformed = (
            "{% set helpers = {'message': 'ready'} %}"
            "{{ [helpers.get][0]('missing', is_state)("
            f"'{TARGET}', 'on') + ( }}}}"
        )
        for index, template in enumerate((deep, malformed)):
            with self.subTest(kind="bounded", index=index):
                started = time.perf_counter()
                findings, dynamic = _dynamic(
                    template, source_id=f"transport_bound_{index}"
                )
                elapsed = time.perf_counter() - started
                observed = _binding(_snapshot(dynamic=dynamic))
                self.assertEqual(findings, [])
                self.assertEqual(len(dynamic), 1)
                self.assertTrue(
                    dynamic[0].candidate_resolution_limit_exceeded
                )
                self.assertFalse(observed["evidence_complete"])
                self.assertFalse(observed["execution_eligible"])
                self.assertLess(elapsed, 1.0)

    def test_malformed_mapping_projection_scan_is_bounded_and_explicit(self):
        fragment = "| map("
        repeats = MAX_TEMPLATE_SEGMENT_CHARS // len(fragment)
        template = "{{ " + fragment * repeats + " }}"

        started = time.perf_counter()
        findings, dynamic = _dynamic(
            template,
            source_id="malformed_mapping_projection",
        )
        elapsed = time.perf_counter() - started
        observed = _binding(_snapshot(dynamic=dynamic))

        self.assertEqual(findings, [])
        self.assertEqual(len(dynamic), 1)
        self.assertTrue(dynamic[0].candidate_resolution_limit_exceeded)
        self.assertFalse(observed["evidence_complete"])
        self.assertFalse(observed["execution_eligible"])
        self.assertLess(elapsed, 1.0)

    def test_deep_consumed_pipeline_scan_is_bounded_and_explicit(self):
        wrapper_count = 5_000
        expression = (
            "(" * wrapper_count
            + "containers | first"
            + ").message" * wrapper_count
        )
        self.assertLess(len(expression), MAX_TEMPLATE_SEGMENT_CHARS)
        template = (
            "{% set containers = [{'message': 'ready'}] %}"
            "{{ " + expression + " }}"
        )

        started = time.perf_counter()
        findings, dynamic = _dynamic(
            template,
            source_id="deep_consumed_pipeline",
        )
        elapsed = time.perf_counter() - started
        observed = _binding(_snapshot(dynamic=dynamic))

        self.assertEqual(findings, [])
        self.assertEqual(len(dynamic), 1)
        self.assertTrue(dynamic[0].candidate_resolution_limit_exceeded)
        self.assertFalse(observed["evidence_complete"])
        self.assertFalse(observed["execution_eligible"])
        self.assertLess(elapsed, 1.0)

    def test_mapping_method_drift_changes_approval_binding(self):
        source_id = "mapping_method_drift"
        before_findings, before_dynamic = _dynamic(
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.get('message') }}",
            source_id=source_id,
        )
        after_findings, after_dynamic = _dynamic(
            f"{{% set helpers = {{'{TARGET}': states}} %}}"
            f"{{{{ helpers.get('{TARGET}')('{TARGET}') }}}}",
            source_id=source_id,
        )
        before = _binding(
            _snapshot(
                findings=before_findings,
                dynamic=before_dynamic,
                profiles=(_profile(source_id),),
            )
        )
        after = _binding(
            _snapshot(
                findings=after_findings,
                dynamic=after_dynamic,
                profiles=(_profile(source_id),),
            )
        )

        self.assertTrue(before["evidence_complete"])
        self.assertEqual(before["relevant_downstream_object_ids"], [])
        self.assertTrue(after["evidence_complete"])
        self.assertEqual(
            after["relevant_downstream_object_ids"],
            [f"automation.{source_id}"],
        )
        self.assertNotEqual(
            before["evidence_fingerprint"],
            after["evidence_fingerprint"],
        )

    def test_mapping_method_alias_drift_changes_approval_binding(self):
        source_id = "mapping_method_alias_drift"
        before_findings, before_dynamic = _dynamic(
            "{% set helpers = {'message': 'ready'} %}"
            "{% set getter = helpers.get %}"
            "{{ getter('message') }}",
            source_id=source_id,
        )
        after_findings, after_dynamic = _dynamic(
            f"{{% set helpers = {{'{TARGET}': states}} %}}"
            "{% set getter = helpers.get %}"
            f"{{{{ getter('{TARGET}')('{TARGET}') }}}}",
            source_id=source_id,
        )
        before = _binding(
            _snapshot(
                findings=before_findings,
                dynamic=before_dynamic,
                profiles=(_profile(source_id),),
            )
        )
        after = _binding(
            _snapshot(
                findings=after_findings,
                dynamic=after_dynamic,
                profiles=(_profile(source_id),),
            )
        )

        self.assertTrue(before["evidence_complete"])
        self.assertEqual(before["relevant_downstream_object_ids"], [])
        self.assertTrue(after["evidence_complete"])
        self.assertEqual(
            after["relevant_downstream_object_ids"],
            [f"automation.{source_id}"],
        )
        self.assertNotEqual(
            before["evidence_fingerprint"],
            after["evidence_fingerprint"],
        )

    def test_nested_mapping_method_argument_limit_is_conservative(self):
        expression = f"lookup('{TARGET}', 'on')"
        for _index in range(10):
            expression = f"helpers.get('message', {expression})"
        template = (
            "{% set lookup = is_state %}"
            "{% set helpers = {'message': 'ready'} %}"
            f"{{{{ {expression} }}}}"
        )
        findings, dynamic = _dynamic(
            template, source_id="mapping_method_argument_depth"
        )
        observed = _binding(_snapshot(dynamic=dynamic))

        self.assertEqual(findings, [])
        self.assertTrue(dynamic)
        self.assertTrue(
            any(
                item.candidate_resolution_limit_exceeded
                for item in dynamic
            )
        )
        self.assertFalse(observed["evidence_complete"])
        self.assertFalse(observed["execution_eligible"])

    def test_returned_mapping_method_chain_limit_is_conservative(self):
        statements = [
            f"{{% set level0 = {{'{TARGET}': states}} %}}"
        ]
        for index in range(1, 11):
            statements.append(
                "{% set level"
                f"{index} = {{'next': level{index - 1}.get}} %}}"
            )
        expression = "level10.get('next')" + "('next')" * 9
        expression += f"('{TARGET}')('{TARGET}')"
        template = "".join(statements) + f"{{{{ {expression} }}}}"

        findings, dynamic = _dynamic(
            template, source_id="returned_mapping_method_depth"
        )
        observed = _binding(_snapshot(dynamic=dynamic))

        self.assertEqual(findings, [])
        self.assertTrue(dynamic)
        self.assertTrue(
            any(
                item.candidate_resolution_limit_exceeded
                for item in dynamic
            )
        )
        self.assertFalse(observed["evidence_complete"])
        self.assertFalse(observed["execution_eligible"])

    def test_shared_alias_graph_provenance_walk_is_bounded(self):
        statements = [
            "{% set helpers = {'message': 'ready'} %}",
            "{% set level0 = helpers.get %}",
        ]
        for level in range(1, 9):
            fields = ", ".join(
                f"'{index}': level{level - 1}" for index in range(6)
            )
            statements.append(
                f"{{% set level{level} = {{{fields}}} %}}"
            )
        template = "".join(statements) + "{{ (level8 | list)[0] }}"

        started = time.monotonic()
        findings, dynamic = _dynamic(
            template,
            source_id="shared_alias_graph_bound",
        )
        elapsed = time.monotonic() - started
        observed = _binding(_snapshot(dynamic=dynamic))

        self.assertEqual(findings, [])
        self.assertEqual(len(dynamic), 1)
        self.assertTrue(dynamic[0].candidate_resolution_limit_exceeded)
        self.assertFalse(observed["evidence_complete"])
        self.assertFalse(observed["execution_eligible"])
        self.assertLess(elapsed, 1.0)

    def test_direct_mapping_member_drift_changes_binding(self):
        before_findings, before_dynamic = _dynamic(
            "{% set helpers = {'lookup': states} %}"
            "{{ helpers.lookup['sensor.unrelated'] }}",
            source_id="mapping_member_drift",
        )
        after_findings, after_dynamic = _dynamic(
            "{% set helpers = {'lookup': states} %}"
            f"{{{{ helpers.lookup['{TARGET}'] }}}}",
            source_id="mapping_member_drift",
        )
        before = _binding(
            _snapshot(
                findings=before_findings,
                dynamic=before_dynamic,
                profiles=(_profile("mapping_member_drift"),),
            )
        )
        after = _binding(
            _snapshot(
                findings=after_findings,
                dynamic=after_dynamic,
                profiles=(_profile("mapping_member_drift"),),
            )
        )

        self.assertTrue(before["evidence_complete"])
        self.assertEqual(before["relevant_downstream_object_ids"], [])
        self.assertTrue(after["evidence_complete"])
        self.assertEqual(
            after["relevant_downstream_object_ids"],
            ["automation.mapping_member_drift"],
        )
        self.assertNotEqual(
            before["evidence_fingerprint"],
            after["evidence_fingerprint"],
        )

    def test_uncertain_collection_aliases_remain_incomplete(self):
        templates = (
            "{% set original = states %}"
            "{% set lookup = original if enabled else unknown_collection %}"
            f"{{{{ lookup['{TARGET}'] }}}}",
            "{% set lookup = states if enabled else is_state %}"
            f"{{{{ lookup['{TARGET}'] }}}}",
            "{% set states = states if enabled else is_state %}"
            f"{{{{ states['{TARGET}'] }}}}",
            "{% set lookup = unknown_collection %}"
            f"{{{{ lookup['{TARGET}'] }}}}",
            "{% set states = unknown_collection %}"
            f"{{{{ states['{TARGET}'] }}}}",
            "{% set lookup = unknown_collection %}"
            "{{ lookup.input_boolean."
            f"{TARGET.split('.', 1)[1]} }}}}",
            "{% set lookup = unknown_collection %}"
            "{% for item in lookup %}{{ item }}{% endfor %}",
        )
        for index, template in enumerate(templates):
            with self.subTest(template=template):
                source_id = f"uncertain_collection_alias_{index}"
                findings, dynamic = _dynamic(
                    template, source_id=source_id
                )
                observed = _binding(
                    _snapshot(
                        findings=findings,
                        dynamic=dynamic,
                        profiles=(
                            _profile(source_id, "cover.open_cover"),
                        ),
                    )
                )
                risk = helper_dependency_risk_assessment(
                    {
                        "binding": observed,
                        "provenance": {
                            "provider": "dependency_index",
                            "completeness": observed["completeness"],
                        },
                    }
                )

                self.assertEqual(findings, [])
                self.assertTrue(dynamic)
                self.assertTrue(
                    all(item.entity_selector_present for item in dynamic)
                )
                self.assertFalse(observed["evidence_complete"])
                self.assertFalse(observed["execution_eligible"])
                self.assertEqual(risk.level, RiskLevel.HIGH)
                self.assertFalse(risk.apply_allowed)

    def test_bare_and_iterated_states_aliases_remain_incomplete(self):
        templates = (
            "{% set lookup = states %}{{ lookup }}",
            "{% set lookup = states %}"
            "{% for item in lookup %}{{ item }}{% endfor %}",
        )
        for index, template in enumerate(templates):
            with self.subTest(template=template):
                _findings, dynamic = _dynamic(
                    template,
                    source_id=f"states_collection_use_{index}",
                )
                observed = _binding(_snapshot(dynamic=dynamic))

                self.assertTrue(dynamic)
                self.assertTrue(
                    all(item.entity_selector_present for item in dynamic)
                )
                self.assertFalse(observed["evidence_complete"])
                self.assertFalse(observed["execution_eligible"])

    def test_unreviewed_scope_aliases_remain_incomplete(self):
        templates = (
            "{% macro check() %}"
            "{% set lookup = is_state %}"
            f"{{{{ lookup('{TARGET}', 'on') }}}}"
            "{% endmacro %}{{ check() }}",
            "{% with lookup = is_state %}"
            f"{{{{ lookup('{TARGET}', 'on') }}}}"
            "{% endwith %}",
            "{% set lookup = states %}{% macro check() %}"
            f"{{{{ lookup['{TARGET}'] }}}}"
            "{% endmacro %}{{ check() }}",
            "{% macro check(lookup) %}"
            f"{{{{ lookup('{TARGET}') }}}}"
            "{% endmacro %}{{ check(unknown_callable) }}",
            "{% call(lookup) supply(is_state) %}"
            f"{{{{ lookup('{TARGET}', 'on') }}}}"
            "{% endcall %}",
        )
        for index, template in enumerate(templates):
            with self.subTest(template=template):
                source_id = f"unreviewed_scope_alias_{index}"
                findings, dynamic = _dynamic(
                    template, source_id=source_id
                )
                observed = _binding(
                    _snapshot(
                        findings=findings,
                        dynamic=dynamic,
                        profiles=(
                            _profile(source_id, "cover.open_cover"),
                        ),
                    )
                )
                risk = helper_dependency_risk_assessment(
                    {
                        "binding": observed,
                        "provenance": {
                            "provider": "dependency_index",
                            "completeness": observed["completeness"],
                        },
                    }
                )

                self.assertEqual(findings, [])
                self.assertTrue(dynamic)
                self.assertTrue(
                    any(item.entity_selector_present for item in dynamic)
                )
                self.assertFalse(observed["evidence_complete"])
                self.assertFalse(observed["execution_eligible"])
                self.assertEqual(risk.level, RiskLevel.HIGH)
                self.assertFalse(risk.apply_allowed)

    def test_collection_alias_classification_drift_changes_binding(self):
        before = _binding(
            _snapshot(
                dynamic=_dynamic_items(
                    ORDINARY_DYNAMIC_TEMPLATES[:1],
                    "collection_alias_drift",
                )
            )
        )
        _findings, after_dynamic = _dynamic(
            "{% set original = states %}"
            "{% set lookup = original if enabled else unknown_collection %}"
            f"{{{{ lookup['{TARGET}'] }}}}",
            source_id="collection_alias_drift_0",
        )
        after = _binding(_snapshot(dynamic=after_dynamic))

        self.assertTrue(before["evidence_complete"])
        self.assertFalse(after["evidence_complete"])
        self.assertNotEqual(
            before["evidence_fingerprint"],
            after["evidence_fingerprint"],
        )

    def test_callable_alias_classification_drift_changes_binding(self):
        before_dynamic = _dynamic_items(
            ORDINARY_DYNAMIC_TEMPLATES[:1], "callable_alias_drift"
        )
        after_findings, after_dynamic = _dynamic(
            "{% set states = is_state %}"
            f"{{{{ states('{TARGET}', 'on') }}}}",
            source_id="callable_alias_drift_0",
        )
        before = _binding(_snapshot(dynamic=before_dynamic))
        after = _binding(
            _snapshot(
                findings=after_findings,
                dynamic=after_dynamic,
                profiles=(_profile("callable_alias_drift_0"),),
            )
        )

        self.assertTrue(before["evidence_complete"])
        self.assertEqual(before["relevant_downstream_object_ids"], [])
        self.assertTrue(after["evidence_complete"])
        self.assertEqual(
            after["relevant_downstream_object_ids"],
            ["automation.callable_alias_drift_0"],
        )
        self.assertNotEqual(
            before["evidence_fingerprint"],
            after["evidence_fingerprint"],
        )

    def test_incomplete_source_coverage_remains_conservative(self):
        snapshot = _snapshot(
            dynamic=_dynamic_items(
                ORDINARY_DYNAMIC_TEMPLATES[:1], "ordinary_partial"
            )
        )
        snapshot = replace(
            snapshot,
            coverage=(
                SourceCoverageItem(
                    "automation",
                    "direct_ha_api",
                    "automation_config",
                    "partial",
                    failed_item_count=1,
                ),
                snapshot.coverage[1],
            ),
        )
        observed = build_helper_dependency_risk_binding(
            snapshot,
            entity_id=TARGET,
            index_metadata={
                "freshness": "current",
                "evidence_stale": False,
                "invalidated": False,
            },
        )

        self.assertFalse(observed["evidence_complete"])
        self.assertFalse(observed["execution_eligible"])
        self.assertEqual(observed["physical_consequence"], "unknown")

    def test_selector_classification_drift_changes_approval_binding(self):
        ordinary = _dynamic_items(
            ORDINARY_DYNAMIC_TEMPLATES[:1], "classification_drift"
        )
        _findings, unresolved = _dynamic(
            "{{ states(variable) }}", source_id="classification_drift_0"
        )
        before = _binding(_snapshot(dynamic=ordinary))
        after = _binding(_snapshot(dynamic=unresolved))

        self.assertTrue(before["evidence_complete"])
        self.assertFalse(after["evidence_complete"])
        self.assertNotEqual(
            before["evidence_fingerprint"],
            after["evidence_fingerprint"],
        )


if __name__ == "__main__":
    unittest.main()
