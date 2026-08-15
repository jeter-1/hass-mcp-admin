"""Beta 39 selector-aware specialized helper-risk regression coverage."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    build_helper_dependency_risk_binding,
    helper_dependency_risk_assessment,
)
from ha_mcp_engineering.governance.models import RiskLevel  # noqa: E402
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
