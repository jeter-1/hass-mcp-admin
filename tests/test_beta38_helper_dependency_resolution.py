"""Beta 38 bounded helper-dependency resolution and health attribution."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.extraction import (  # noqa: E402
    MAX_TEMPLATE_SEGMENT_CHARS,
    extract_document,
)
from ha_mcp_engineering.dependency.models import (  # noqa: E402
    AutomationActionRiskProfile,
    DependencyFinding,
    DependencyIndexSnapshot,
    SourceCoverageItem,
)
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DirectHaDependencyProvider,
    MAX_LABEL_MEMBERSHIP,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    build_helper_dependency_risk_binding,
    helper_dependency_risk_assessment,
)
from ha_mcp_engineering.governance.helper_state import (  # noqa: E402
    HELPER_STATE_PROVIDER,
    HELPER_STATE_PROVIDER_CONTRACT,
    helper_state_provider_evidence,
)
from ha_mcp_engineering.governance.risk import (  # noqa: E402
    automation_action_consequence_profile,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)


TARGET = "input_boolean.mcp_f2_standard_admin_test_flag"


def _profile(source_id: str, service: str = "notify.notify"):
    value = automation_action_consequence_profile(
        {
            "action": [
                {
                    "service": service,
                    "data": {"message": "bounded beta 38 fixture"},
                    **(
                        {"target": {"entity_id": "cover.disposable"}}
                        if service == "cover.open_cover"
                        else {}
                    ),
                }
            ]
        }
    )
    return AutomationActionRiskProfile(
        source_id=source_id,
        source_entity_id=f"automation.{source_id}",
        risk_level=value["risk_level"],
        physical_consequence=value["physical_consequence"],
        complete=value["complete"],
        truncated=value["truncated"],
        action_domains=tuple(value["action_domains"]),
        services=tuple(value["services"]),
        reason_codes=tuple(value["reason_codes"]),
        effect_projection_model=value["effect_projection_model"],
        effect_targets=tuple(value["effect_targets"]),
        effect_data=tuple(value["effect_data"]),
        effect_structure_fingerprint=value[
            "effect_structure_fingerprint"
        ],
        effect_projection_fingerprint=value[
            "effect_projection_fingerprint"
        ],
        effect_projection_clipped=value["effect_projection_clipped"],
        evidence_fingerprint=value["evidence_fingerprint"],
    )


def _snapshot(
    *,
    dynamic=(),
    findings=(),
    profiles=(),
    label_memberships=None,
    label_fingerprints=None,
    label_complete=False,
    label_truncated=(),
) -> DependencyIndexSnapshot:
    return DependencyIndexSnapshot(
        fingerprint="a" * 64,
        generation=38,
        built_at_monotonic=time.monotonic(),
        built_at="2026-08-14T12:00:00+00:00",
        findings=tuple(findings),
        dynamic_references=tuple(dynamic),
        target_metadata={},
        coverage=(
            SourceCoverageItem(
                "automation", "direct_ha_api", "automation_config", "complete"
            ),
            SourceCoverageItem(
                "blueprint", "direct_ha_api", "blueprint_source", "complete"
            ),
        ),
        automation_action_profiles=tuple(profiles),
        label_memberships=label_memberships or {},
        label_membership_fingerprints=label_fingerprints or {},
        label_membership_truncated=tuple(label_truncated),
        label_registry_complete=label_complete,
    )


def _binding(snapshot: DependencyIndexSnapshot, target: str = TARGET):
    return build_helper_dependency_risk_binding(
        snapshot,
        entity_id=target,
        index_metadata={
            "freshness": "current",
            "evidence_stale": False,
            "invalidated": False,
        },
    )


def _dynamic(template: str, source_id: str = "bounded_dynamic"):
    findings, dynamic = extract_document(
        source_type="automation",
        source_id=source_id,
        source_entity_id=f"automation.{source_id}",
        config={
            "condition": [
                {
                    "condition": "template",
                    "value_template": template,
                }
            ]
        },
    )
    return findings, dynamic


class BoundedDynamicResolutionTests(unittest.TestCase):
    def test_home_assistant_filter_and_test_forms_retain_exact_helpers(self):
        forms = (
            f'{{{{ "{TARGET}" | states }}}}',
            f'{{{{ "{TARGET}" | state_attr("friendly_name") }}}}',
            f'{{{{ "{TARGET}" | has_value }}}}',
            f'{{{{ "{TARGET}" is is_state("on") }}}}',
            f'{{{{ "{TARGET}" is is_state_attr("mode", "on") }}}}',
            f'{{{{ "{TARGET}" is has_value }}}}',
            f'{{{{ "{TARGET}" is not is_state("off") }}}}',
            f'{{{{ "{TARGET}" is not is_state_attr("mode", "off") }}}}',
            f'{{{{ "{TARGET}" is not has_value }}}}',
            f'{{{{ has_value("{TARGET}") }}}}',
        )

        for index, template in enumerate(forms):
            with self.subTest(template=template):
                findings, dynamic = _dynamic(
                    template, source_id=f"exact_operator_{index}"
                )
                self.assertEqual(dynamic, [])
                self.assertEqual(
                    {item.target_entity_id for item in findings},
                    {TARGET},
                )

    def test_collection_filters_and_maps_retain_finite_exact_candidates(self):
        forms = (
            f'{{{{ ["{TARGET}"] | select("is_state", "on") | list }}}}',
            f'{{{{ ["{TARGET}"] '
            '| select("is_state_attr", "mode", "on") | list }}',
            f'{{{{ ["{TARGET}"] | select("has_value") | list }}}}',
            f'{{{{ ["{TARGET}"] | reject("is_state", "off") | list }}}}',
            f'{{{{ ["{TARGET}"] | map("states") | list }}}}',
            f'{{{{ ["{TARGET}"] | map("state_attr", "friendly_name") | list }}}}',
            f'{{{{ ["{TARGET}"] | map("has_value") | list }}}}',
        )

        for index, template in enumerate(forms):
            with self.subTest(template=template):
                findings, dynamic = _dynamic(
                    template, source_id=f"collection_operator_{index}"
                )
                self.assertEqual(findings, [])
                self.assertEqual(len(dynamic), 1)
                self.assertTrue(
                    dynamic[0].candidate_resolution_complete
                )
                self.assertEqual(
                    dynamic[0].possible_entity_ids, (TARGET,)
                )

    def test_parenthesized_and_nested_collection_forms_retain_candidates(self):
        forms = (
            f'{{{{ (["{TARGET}"] '
            '| select("is_state", "on") | list) | count }}}}',
            f'{{{{ (["{TARGET}"] | map("states") | list) '
            '| select("defined") | list }}}}',
            f'{{{{ (["{TARGET}"] '
            '| select("is_state", "on") | list) '
            '| map("states") | list }}}}',
            f'{{{{ (["{TARGET}"] '
            '| reject("has_value") | list) if enabled else [] }}}}',
            f'{{{{ ((["{TARGET}"] | map("state_attr", '
            '"friendly_name") | list) | list) | count }}}}',
        )

        for index, template in enumerate(forms):
            with self.subTest(template=template):
                findings, dynamic = _dynamic(
                    template, source_id=f"nested_collection_{index}"
                )
                self.assertEqual(findings, [])
                self.assertEqual(len(dynamic), 1)
                self.assertTrue(
                    dynamic[0].candidate_resolution_complete
                )
                self.assertEqual(
                    dynamic[0].possible_entity_ids, (TARGET,)
                )

    def test_inline_conditional_collection_branches_preserve_exact_candidates(self):
        cases = (
            (
                f'{{{{ ["{TARGET}"] | select("is_state", "on") '
                "if enabled else [] }}",
                (TARGET,),
            ),
            (
                '{{ ["sensor.a"] | select("is_state", "on") '
                "if enabled else [] }}",
                ("sensor.a",),
            ),
            (
                f'{{{{ (["{TARGET}"] | select("is_state", "on") '
                '| list) if enabled else (["sensor.b"] '
                '| map("states") | list) }}',
                (TARGET, "sensor.b"),
            ),
        )

        for index, (template, expected) in enumerate(cases):
            with self.subTest(template=template):
                findings, dynamic = _dynamic(
                    template, source_id=f"conditional_exact_{index}"
                )
                self.assertEqual(findings, [])
                self.assertEqual(len(dynamic), 1)
                self.assertTrue(
                    dynamic[0].candidate_resolution_complete
                )
                self.assertEqual(
                    dynamic[0].possible_entity_ids, expected
                )

    def test_inline_conditional_dynamic_branch_remains_incomplete(self):
        findings, dynamic = _dynamic(
            f'{{{{ (["{TARGET}"] | select("is_state", "on") '
            "| list) if enabled else (helper_entities "
            '| map("states") | list) }}',
            source_id="conditional_dynamic_branch",
        )
        observed = _binding(_snapshot(dynamic=dynamic))

        self.assertEqual(findings, [])
        self.assertEqual(len(dynamic), 1)
        self.assertEqual(dynamic[0].possible_entity_ids, (TARGET,))
        self.assertFalse(dynamic[0].candidate_resolution_complete)
        self.assertFalse(observed["evidence_complete"])
        self.assertFalse(observed["execution_eligible"])

    def test_inline_conditional_dependency_retains_consequential_profile(self):
        source_id = "conditional_collection_consequence"
        findings, dynamic = _dynamic(
            f'{{{{ ["{TARGET}"] | select("is_state", "on") '
            "if enabled else [] }}",
            source_id=source_id,
        )
        observed = _binding(
            _snapshot(
                dynamic=dynamic,
                profiles=(_profile(source_id, "cover.open_cover"),),
            )
        )

        self.assertEqual(findings, [])
        self.assertTrue(observed["evidence_complete"])
        self.assertEqual(observed["physical_consequence"], "direct")
        self.assertEqual(
            observed["relevant_downstream_object_ids"],
            [f"automation.{source_id}"],
        )

    def test_inline_conditional_candidate_and_operator_drift_changes_binding(self):
        source_id = "conditional_collection_drift"
        templates = (
            f'{{{{ ["{TARGET}"] | select("is_state", "on") '
            "if enabled else [] }}",
            f'{{{{ ["{TARGET}"] | map("states") '
            "if enabled else [] }}",
            '{{ ["sensor.a"] | select("is_state", "on") '
            "if enabled else [] }}",
        )
        bindings = []
        for template in templates:
            _findings, dynamic = _dynamic(template, source_id=source_id)
            bindings.append(
                _binding(
                    _snapshot(
                        dynamic=dynamic,
                        profiles=(_profile(source_id),),
                    )
                )
            )

        self.assertNotEqual(
            bindings[0]["evidence_fingerprint"],
            bindings[1]["evidence_fingerprint"],
        )
        self.assertNotEqual(
            bindings[0]["evidence_fingerprint"],
            bindings[2]["evidence_fingerprint"],
        )

    def test_malformed_nested_collection_scan_is_bounded_and_explicit(self):
        operator = (
            f'["{TARGET}"] | select("is_state", "on") | list'
        )
        elapsed_by_size = {}
        for opener_count in (8_000, 32_000):
            template = "{{ " + "(" * opener_count + operator + " }}"
            started = time.perf_counter()
            findings, dynamic = _dynamic(
                template, source_id=f"malformed_{opener_count}"
            )
            elapsed_by_size[opener_count] = time.perf_counter() - started
            self.assertEqual(findings, [])
            self.assertEqual(len(dynamic), 1)
            self.assertFalse(dynamic[0].candidate_resolution_complete)
            self.assertTrue(
                dynamic[0].candidate_resolution_limit_exceeded
            )

        max_prefix = MAX_TEMPLATE_SEGMENT_CHARS - len(operator) - 1
        for pattern_index, pattern in enumerate(("(", "[", "{", "([{")):
            with self.subTest(maximum_pattern=pattern):
                malformed_prefix = (
                    pattern * ((max_prefix // len(pattern)) + 1)
                )[:max_prefix]
                maximum = "{{ " + malformed_prefix + operator + " }}"
                started = time.perf_counter()
                findings, dynamic = _dynamic(
                    maximum,
                    source_id=f"malformed_maximum_{pattern_index}",
                )
                maximum_elapsed = time.perf_counter() - started

                self.assertEqual(findings, [])
                self.assertEqual(len(dynamic), 1)
                self.assertTrue(
                    dynamic[0].candidate_resolution_limit_exceeded
                )
                self.assertFalse(
                    _binding(_snapshot(dynamic=dynamic))[
                        "evidence_complete"
                    ]
                )
                self.assertLess(maximum_elapsed, 1.0)
        self.assertLess(
            elapsed_by_size[32_000],
            max(0.20, elapsed_by_size[8_000] * 8),
        )

    def test_malformed_delimiters_after_collection_remain_incomplete(self):
        operator = '["sensor.a"] | select("is_state", "on") | list'
        cases = []
        for delimiter in ("(", "[", "{"):
            cases.extend(
                (
                    (delimiter + operator, ()),
                    (operator + " + " + delimiter, ("sensor.a",)),
                )
            )
        cases.append(
            (
                operator + " if enabled else (",
                ("sensor.a",),
            )
        )

        for index, (expression, expected_candidates) in enumerate(cases):
            with self.subTest(expression=expression):
                findings, dynamic = _dynamic(
                    "{{ " + expression + " }}",
                    source_id=f"malformed_position_{index}",
                )
                observed = _binding(_snapshot(dynamic=dynamic))

                self.assertEqual(findings, [])
                self.assertEqual(len(dynamic), 1)
                self.assertEqual(
                    dynamic[0].possible_entity_ids,
                    expected_candidates,
                )
                self.assertFalse(
                    dynamic[0].candidate_resolution_complete
                )
                self.assertTrue(
                    dynamic[0].candidate_resolution_limit_exceeded
                )
                self.assertFalse(observed["evidence_complete"])
                self.assertFalse(observed["execution_eligible"])

    def test_nested_collection_dependency_retains_consequential_profile(self):
        source_id = "nested_collection_consequence"
        findings, dynamic = _dynamic(
            f'{{{{ (["{TARGET}"] '
            '| select("is_state", "on") | list) | count }}}}',
            source_id=source_id,
        )
        observed = _binding(
            _snapshot(
                dynamic=dynamic,
                profiles=(_profile(source_id, "cover.open_cover"),),
            )
        )

        self.assertEqual(findings, [])
        self.assertTrue(observed["evidence_complete"])
        self.assertTrue(observed["execution_eligible"])
        self.assertEqual(observed["physical_consequence"], "direct")
        self.assertEqual(
            observed["relevant_downstream_object_ids"],
            [f"automation.{source_id}"],
        )

    def test_nested_collections_are_target_specific_or_incomplete(self):
        _findings, unrelated = _dynamic(
            '{{ (["sensor.a", "sensor.b"] '
            '| select("is_state", "on") | list) | count }}',
            source_id="nested_unrelated_collection",
        )
        unrelated_binding = _binding(_snapshot(dynamic=unrelated))
        self.assertTrue(unrelated_binding["evidence_complete"])
        self.assertTrue(unrelated_binding["execution_eligible"])
        self.assertEqual(
            unrelated_binding["physical_consequence"], "none"
        )

        forms = (
            '{{ (helper_entities '
            '| select("is_state", "on") | list) | count }}',
            f'{{{{ (["{TARGET}"] '
            '| select(test_name, "on") | list) | count }}}}',
            '{{ (helper_entities | map("states") | list) '
            'if enabled else [] }}',
        )
        for index, template in enumerate(forms):
            with self.subTest(template=template):
                findings, dynamic = _dynamic(
                    template,
                    source_id=f"nested_incomplete_collection_{index}",
                )
                observed = _binding(_snapshot(dynamic=dynamic))
                self.assertEqual(findings, [])
                self.assertEqual(len(dynamic), 1)
                self.assertFalse(
                    dynamic[0].candidate_resolution_complete
                )
                self.assertFalse(observed["evidence_complete"])
                self.assertFalse(observed["execution_eligible"])

        over_nested = (
            "{{ "
            + "(" * 10
            + f'["{TARGET}"] | select("is_state", "on") | list'
            + ")" * 10
            + " }}"
        )
        _findings, over_nested_dynamic = _dynamic(
            over_nested, source_id="nested_collection_limit"
        )
        self.assertEqual(len(over_nested_dynamic), 1)
        self.assertFalse(
            over_nested_dynamic[0].candidate_resolution_complete
        )
        self.assertTrue(
            over_nested_dynamic[0].candidate_resolution_limit_exceeded
        )

    def test_collection_dependency_retains_consequential_profile(self):
        source_id = "collection_filter_consequence"
        findings, dynamic = _dynamic(
            f'{{{{ ["{TARGET}"] | select("is_state", "on") | list }}}}',
            source_id=source_id,
        )
        observed = _binding(
            _snapshot(
                dynamic=dynamic,
                profiles=(_profile(source_id, "cover.open_cover"),),
            )
        )

        self.assertEqual(findings, [])
        self.assertTrue(observed["evidence_complete"])
        self.assertTrue(observed["execution_eligible"])
        self.assertEqual(observed["physical_consequence"], "direct")
        self.assertEqual(
            observed["relevant_downstream_object_ids"],
            [f"automation.{source_id}"],
        )

    def test_collection_candidates_are_target_specific_or_incomplete(self):
        _findings, unrelated = _dynamic(
            '{{ ["sensor.a", "sensor.b"] '
            '| select("is_state", "on") | list }}',
            source_id="unrelated_collection",
        )
        unrelated_binding = _binding(_snapshot(dynamic=unrelated))
        self.assertTrue(unrelated_binding["evidence_complete"])
        self.assertTrue(unrelated_binding["execution_eligible"])
        self.assertEqual(
            unrelated_binding["physical_consequence"], "none"
        )

        _findings, mapped_domain = _dynamic(
            "{{ states.sensor | map(attribute='entity_id') "
            '| select("is_state", "on") | list }}',
            source_id="mapped_sensor_collection",
        )
        mapped_binding = _binding(_snapshot(dynamic=mapped_domain))
        self.assertTrue(mapped_binding["evidence_complete"])
        self.assertTrue(mapped_binding["execution_eligible"])

        _findings, attribute_domain = _dynamic(
            "{{ states.sensor "
            "| selectattr('entity_id', 'has_value') | list }}",
            source_id="attribute_sensor_collection",
        )
        attribute_binding = _binding(
            _snapshot(dynamic=attribute_domain)
        )
        self.assertTrue(attribute_binding["evidence_complete"])
        self.assertTrue(attribute_binding["execution_eligible"])

        forms = (
            '{{ helper_entities | select("is_state", "on") | list }}',
            '{{ [helper_entity] | map("states") | list }}',
            f'{{{{ ["{TARGET}"] | select(test_name, "on") | list }}}}',
            f'{{{{ [{{"entity_id": "{TARGET}"}}] '
            '| selectattr("entity_id", "has_value") | list }}',
            f'{{{{ ["{TARGET}"] '
            '| selectattr(attribute_name, "has_value") | list }}',
            f'{{{{ ["{TARGET}"] | map( }}}}',
        )
        for index, template in enumerate(forms):
            with self.subTest(template=template):
                findings, dynamic = _dynamic(
                    template, source_id=f"incomplete_collection_{index}"
                )
                observed = _binding(_snapshot(dynamic=dynamic))
                self.assertEqual(findings, [])
                self.assertEqual(len(dynamic), 1)
                self.assertFalse(
                    dynamic[0].candidate_resolution_complete
                )
                self.assertFalse(observed["evidence_complete"])
                self.assertFalse(observed["execution_eligible"])

    def test_collection_candidate_and_operator_drift_changes_binding(self):
        source_id = "collection_operator_drift"
        _findings, before_dynamic = _dynamic(
            f'{{{{ ["{TARGET}"] | map("states") | list }}}}',
            source_id=source_id,
        )
        _findings, operator_dynamic = _dynamic(
            f'{{{{ ["{TARGET}"] '
            '| map("state_attr", "friendly_name") | list }}',
            source_id=source_id,
        )
        _findings, candidate_dynamic = _dynamic(
            '{{ ["sensor.a"] | map("states") | list }}',
            source_id=source_id,
        )
        profile = (_profile(source_id),)
        before = _binding(
            _snapshot(dynamic=before_dynamic, profiles=profile)
        )
        operator_after = _binding(
            _snapshot(dynamic=operator_dynamic, profiles=profile)
        )
        candidate_after = _binding(
            _snapshot(dynamic=candidate_dynamic, profiles=profile)
        )

        self.assertNotEqual(
            before["evidence_fingerprint"],
            operator_after["evidence_fingerprint"],
        )
        self.assertNotEqual(
            before["evidence_fingerprint"],
            candidate_after["evidence_fingerprint"],
        )

        _findings, nested_before_dynamic = _dynamic(
            f'{{{{ (["{TARGET}"] '
            '| select("is_state", "on") | list) | count }}}}',
            source_id=source_id,
        )
        _findings, nested_operator_dynamic = _dynamic(
            f'{{{{ (["{TARGET}"] | map("states") | list) '
            '| count }}}}',
            source_id=source_id,
        )
        _findings, nested_candidate_dynamic = _dynamic(
            '{{ (["sensor.a"] '
            '| select("is_state", "on") | list) | count }}',
            source_id=source_id,
        )
        nested_before = _binding(
            _snapshot(dynamic=nested_before_dynamic, profiles=profile)
        )
        nested_operator_after = _binding(
            _snapshot(
                dynamic=nested_operator_dynamic, profiles=profile
            )
        )
        nested_candidate_after = _binding(
            _snapshot(
                dynamic=nested_candidate_dynamic, profiles=profile
            )
        )
        self.assertNotEqual(
            nested_before["evidence_fingerprint"],
            nested_operator_after["evidence_fingerprint"],
        )
        self.assertNotEqual(
            nested_before["evidence_fingerprint"],
            nested_candidate_after["evidence_fingerprint"],
        )

    def test_exact_filter_dependency_retains_consequential_profile(self):
        source_id = "exact_filter_consequence"
        findings, dynamic = _dynamic(
            f'{{{{ "{TARGET}" | states }}}}', source_id=source_id
        )
        observed = _binding(
            _snapshot(
                findings=findings,
                profiles=(_profile(source_id, "cover.open_cover"),),
            )
        )

        self.assertEqual(dynamic, [])
        self.assertTrue(observed["evidence_complete"])
        self.assertEqual(observed["physical_consequence"], "direct")
        self.assertEqual(
            observed["relevant_downstream_object_ids"],
            [f"automation.{source_id}"],
        )

    def test_dynamic_filter_and_test_operands_remain_non_conclusive(self):
        forms = (
            "{{ helper_entity | states }}",
            "{{ helper_entity | state_attr('friendly_name') }}",
            "{{ helper_entity | has_value }}",
            "{{ helper_entity is is_state('on') }}",
            "{{ helper_entity is is_state_attr('mode', 'on') }}",
            "{{ helper_entity is has_value }}",
        )

        for index, template in enumerate(forms):
            with self.subTest(template=template):
                findings, dynamic = _dynamic(
                    template, source_id=f"dynamic_operator_{index}"
                )
                observed = _binding(
                    _snapshot(dynamic=dynamic)
                )
                self.assertEqual(findings, [])
                self.assertEqual(len(dynamic), 1)
                self.assertFalse(
                    dynamic[0].candidate_resolution_complete
                )
                self.assertFalse(observed["evidence_complete"])
                self.assertFalse(observed["execution_eligible"])

    def test_domain_state_collection_is_target_specific(self):
        _input_findings, input_dynamic = _dynamic(
            "{{ states.input_boolean "
            "| selectattr('state', 'eq', 'on') | list }}",
            source_id="input_boolean_collection",
        )
        input_binding = _binding(_snapshot(dynamic=input_dynamic))

        _sensor_findings, sensor_dynamic = _dynamic(
            "{{ states.sensor "
            "| selectattr('state', 'eq', 'on') | list }}",
            source_id="sensor_collection",
        )
        sensor_binding = _binding(_snapshot(dynamic=sensor_dynamic))

        self.assertEqual(
            input_dynamic[0].possible_entity_domains,
            ("input_boolean",),
        )
        self.assertFalse(input_binding["evidence_complete"])
        self.assertFalse(input_binding["execution_eligible"])
        self.assertEqual(
            sensor_dynamic[0].possible_entity_domains, ("sensor",)
        )
        self.assertTrue(sensor_binding["evidence_complete"])
        self.assertTrue(sensor_binding["execution_eligible"])

    def test_unrestricted_or_malformed_state_access_never_disappears(self):
        forms = (
            "{{ states | selectattr('state', 'eq', 'on') | list }}",
            "{% for entity in states %}{{ entity.entity_id }}{% endfor %}",
            "{{ states('not-an-entity') }}",
            "{{ states['input_boolean']['target'] }}",
        )

        for index, template in enumerate(forms):
            with self.subTest(template=template):
                findings, dynamic = _dynamic(
                    template, source_id=f"unbounded_state_{index}"
                )
                observed = _binding(_snapshot(dynamic=dynamic))
                self.assertEqual(findings, [])
                self.assertGreaterEqual(len(dynamic), 1)
                self.assertFalse(observed["evidence_complete"])
                self.assertFalse(observed["execution_eligible"])

    def test_literal_list_dictionary_field_excludes_exact_helper(self):
        findings, dynamic = _dynamic(
            "{% for c in [{'id': 'sensor.a'}, {'id': 'sensor.b'}] %}"
            "{{ states[c.id] }}{% endfor %}"
        )

        self.assertEqual(findings, [])
        self.assertEqual(dynamic[0].possible_entity_ids, ("sensor.a", "sensor.b"))
        self.assertTrue(dynamic[0].candidate_resolution_complete)
        observed = _binding(_snapshot(dynamic=dynamic))
        self.assertTrue(observed["evidence_complete"])
        self.assertEqual(observed["physical_consequence"], "none")
        self.assertEqual(observed["unrelated_dynamic_reference_count"], 1)

    def test_literal_mapping_and_dynamic_key_resolve_all_exact_values(self):
        _findings, dynamic = _dynamic(
            "{% set choices = {'primary': 'sensor.a', 'backup': 'sensor.b'} %}"
            "{{ states(choices[selected_key]) }}"
        )

        self.assertEqual(dynamic[0].possible_entity_ids, ("sensor.a", "sensor.b"))
        self.assertEqual(
            dynamic[0].candidate_resolution_kind, "finite_mapping"
        )
        self.assertTrue(_binding(_snapshot(dynamic=dynamic))["evidence_complete"])

    def test_finite_conditional_resolves_union(self):
        _findings, dynamic = _dynamic(
            "{% set temperature_entity = 'sensor.a' if use_a else 'sensor.b' %}"
            "{{ states(temperature_entity) }}"
        )

        self.assertEqual(dynamic[0].possible_entity_ids, ("sensor.a", "sensor.b"))
        self.assertEqual(
            dynamic[0].candidate_resolution_kind, "finite_conditional"
        )

    def test_literal_label_and_explicit_list_are_deterministic(self):
        _findings, dynamic = _dynamic(
            "{% set entities = label_entities('climate_watch') + "
            "['sensor.outdoor', 'sensor.outdoor'] %}"
            "{% for entity in entities %}{{ states(entity) }}{% endfor %}"
        )

        item = dynamic[0]
        self.assertEqual(item.possible_entity_ids, ("sensor.outdoor",))
        self.assertEqual(
            item.literal_label_selectors, ("climate_watch",)
        )
        excluded = _binding(
            _snapshot(
                dynamic=dynamic,
                label_memberships={
                    "climate_watch": ("climate.hall", "sensor.inside")
                },
                label_fingerprints={"climate_watch": "b" * 64},
                label_complete=True,
            )
        )
        self.assertTrue(excluded["evidence_complete"])
        evidence = excluded["resolved_dynamic_reference_evidence"][0]
        self.assertEqual(evidence["target_membership"], "excluded")
        self.assertEqual(
            evidence["candidate_entity_ids"],
            ["climate.hall", "sensor.inside", "sensor.outdoor"],
        )

    def test_target_in_finite_candidates_preserves_consequential_dependency(self):
        _findings, dynamic = _dynamic(
            "{% set entities = ['sensor.a', 'input_boolean.mcp_f2_standard_admin_test_flag'] %}"
            "{% for entity in entities %}{{ is_state(entity, 'on') }}{% endfor %}",
            source_id="opens_cover",
        )
        observed = _binding(
            _snapshot(
                dynamic=dynamic,
                profiles=(_profile("opens_cover", "cover.open_cover"),),
            )
        )

        self.assertTrue(observed["evidence_complete"])
        self.assertEqual(observed["physical_consequence"], "direct")
        self.assertEqual(
            observed["resolved_target_dynamic_reference_count"], 1
        )
        self.assertEqual(
            helper_dependency_risk_assessment(
                {"binding": observed, "provenance": {"generation": 38}}
            ).level.value,
            "high",
        )

    def test_label_membership_drift_changes_approval_binding(self):
        _findings, dynamic = _dynamic(
            "{% for entity in label_entities('climate_watch') %}"
            "{{ states(entity) }}{% endfor %}",
            source_id="label_guard",
        )
        before = _binding(
            _snapshot(
                dynamic=dynamic,
                label_memberships={"climate_watch": ("sensor.a",)},
                label_fingerprints={"climate_watch": "1" * 64},
                label_complete=True,
            )
        )
        after = _binding(
            _snapshot(
                dynamic=dynamic,
                profiles=(_profile("label_guard"),),
                label_memberships={"climate_watch": ("sensor.a", TARGET)},
                label_fingerprints={"climate_watch": "2" * 64},
                label_complete=True,
            )
        )

        self.assertTrue(before["evidence_complete"])
        self.assertTrue(after["evidence_complete"])
        self.assertNotEqual(
            before["evidence_fingerprint"], after["evidence_fingerprint"]
        )
        self.assertEqual(
            after["resolved_dynamic_reference_evidence"][0][
                "target_membership"
            ],
            "included",
        )

    def test_literal_candidate_change_invalidates_material_evidence(self):
        _findings, before_dynamic = _dynamic(
            "{% for entity in ['sensor.a', 'sensor.b'] %}"
            "{{ states(entity) }}{% endfor %}"
        )
        _findings, after_dynamic = _dynamic(
            "{% for entity in ['sensor.a', 'sensor.c'] %}"
            "{{ states(entity) }}{% endfor %}"
        )

        before = _binding(_snapshot(dynamic=before_dynamic))
        after = _binding(_snapshot(dynamic=after_dynamic))

        self.assertTrue(before["evidence_complete"])
        self.assertTrue(after["evidence_complete"])
        self.assertNotEqual(
            before["evidence_fingerprint"], after["evidence_fingerprint"]
        )

    def test_dynamic_label_and_failed_label_evidence_remain_incomplete(self):
        _findings, dynamic_name = _dynamic(
            "{% for entity in label_entities(selected_label) %}"
            "{{ states(entity) }}{% endfor %}"
        )
        self.assertFalse(
            dynamic_name[0].candidate_resolution_complete
        )
        self.assertFalse(
            _binding(_snapshot(dynamic=dynamic_name))[
                "execution_eligible"
            ]
        )

        _findings, literal_label = _dynamic(
            "{% for entity in label_entities('exact_label') %}"
            "{{ states(entity) }}{% endfor %}"
        )
        failed = _binding(_snapshot(dynamic=literal_label))
        self.assertFalse(failed["evidence_complete"])
        self.assertIn(
            "label_registry_evidence_incomplete",
            failed["resolved_dynamic_reference_evidence"][0][
                "reason_codes"
            ],
        )

    def test_unreviewed_expression_families_and_sensitive_label_fail_closed(self):
        templates = (
            "{{ states(all_states | selectattr('entity_id')) }}",
            "{{ states(custom_entity_function()) }}",
            "{{ states(macro_result) }}",
            "{% for entity in states %}{{ states(entity.entity_id) }}{% endfor %}",
        )
        for index, template in enumerate(templates):
            with self.subTest(template=template):
                _findings, dynamic = _dynamic(
                    template, source_id=f"unknown_{index}"
                )
                self.assertFalse(
                    dynamic[0].candidate_resolution_complete
                )
                self.assertFalse(
                    _binding(_snapshot(dynamic=dynamic))[
                        "evidence_complete"
                    ]
                )

        secret = "synthetic-sensitive-label"
        _findings, dynamic = extract_document(
            source_type="automation",
            source_id="sensitive_label",
            config={
                "condition": [
                    {
                        "condition": "template",
                        "value_template": (
                            "{% for entity in label_entities('"
                            + secret
                            + "') %}{{ states(entity) }}{% endfor %}"
                        ),
                    }
                ]
            },
            secret=secret,
        )
        self.assertNotIn(secret, str(dynamic))
        self.assertFalse(dynamic[0].candidate_resolution_complete)

    def test_candidate_overflow_and_malformed_value_fail_closed(self):
        values = ", ".join(
            f"'sensor.item_{index:03d}'" for index in range(140)
        )
        _findings, overflow = _dynamic(
            "{% set entities = [" + values + "] %}"
            "{% for entity in entities %}{{ states(entity) }}{% endfor %}"
        )
        overflow_binding = _binding(_snapshot(dynamic=overflow))
        self.assertFalse(overflow_binding["evidence_complete"])
        self.assertIn(
            "dynamic_reference_resolution_limit_exceeded",
            overflow_binding["dynamic_resolution_reason_codes"],
        )

        _findings, malformed = _dynamic(
            "{% set entities = ['sensor.a', 'not-an-entity'] %}"
            "{% for entity in entities %}{{ states(entity) }}{% endfor %}"
        )
        self.assertFalse(malformed[0].candidate_resolution_complete)
        self.assertFalse(
            _binding(_snapshot(dynamic=malformed))["evidence_complete"]
        )

    def test_direct_static_dependency_is_not_suppressed(self):
        finding = DependencyFinding(
            evidence_id="ev_" + "3" * 24,
            target_entity_id="input_boolean.mcp_f2_live_test_flag",
            source_type="automation",
            source_id="retained_static",
            source_entity_id="automation.retained_static",
            source_name=None,
            relation="trigger",
            config_path="$.trigger[0].entity_id",
        )
        observed = _binding(
            _snapshot(
                findings=(finding,),
                profiles=(_profile("retained_static"),),
            ),
            target="input_boolean.mcp_f2_live_test_flag",
        )

        self.assertTrue(observed["evidence_complete"])
        self.assertEqual(
            observed["relevant_downstream_object_ids"],
            ["automation.retained_static"],
        )

    def test_beta38_canary_fixture_is_low_only_from_complete_evidence(self):
        _findings, dynamic = _dynamic(
            "{% set entities = ['sensor.a', 'climate.hall'] %}"
            "{% for entity in entities %}{{ states(entity) }}{% endfor %}"
        )
        observed = _binding(_snapshot(dynamic=dynamic))
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 38}}
        )

        self.assertEqual(observed["entity_id"], TARGET)
        self.assertTrue(observed["evidence_complete"])
        self.assertEqual(observed["physical_consequence"], "none")
        self.assertEqual(risk.level.value, "low")
        self.assertTrue(risk.apply_allowed)

    def test_stale_index_is_not_certified(self):
        _findings, dynamic = _dynamic(
            "{% for entity in ['sensor.a'] %}{{ states(entity) }}{% endfor %}"
        )
        observed = build_helper_dependency_risk_binding(
            _snapshot(dynamic=dynamic),
            entity_id=TARGET,
            index_metadata={
                "freshness": "stale",
                "evidence_stale": True,
                "invalidated": False,
            },
        )
        self.assertEqual(observed["completeness"], "stale")
        self.assertFalse(observed["execution_eligible"])


class _DependencyRest:
    def __init__(self, config):
        self.config = config

    async def request(self, method, path):
        if path == "/states":
            return [
                {
                    "entity_id": "automation.label_guard",
                    "state": "on",
                    "attributes": {
                        "id": "label_guard",
                        "friendly_name": "Synthetic label guard",
                    },
                }
            ]
        if path == "/config/automation/config/label_guard":
            return self.config
        raise AssertionError(f"unexpected REST path: {path}")


class _DependencyWebSocket:
    def __init__(self, registry, labels, *, fail_labels=False):
        self.registry = registry
        self.labels = labels
        self.fail_labels = fail_labels

    async def command(self, payload):
        if payload == {"type": "config/entity_registry/list"}:
            return self.registry
        if payload == {"type": "config/label_registry/list"}:
            if self.fail_labels:
                raise RuntimeError("synthetic label registry failure")
            return self.labels
        raise AssertionError(f"unexpected websocket command: {payload}")


class LabelRegistryEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def _config(self):
        return {
            "condition": [
                {
                    "condition": "template",
                    "value_template": (
                        "{% for entity in label_entities('climate_watch') %}"
                        "{{ states(entity) }}{% endfor %}"
                    ),
                }
            ],
            "action": [
                {
                    "service": "notify.notify",
                    "data": {"message": "bounded"},
                }
            ],
        }

    async def test_provider_binds_exact_label_name_and_membership(self):
        registry = [
            {"entity_id": "sensor.a", "labels": ["climate_watch_id"]},
            {"entity_id": "sensor.b", "labels": []},
        ]
        provider = DirectHaDependencyProvider(
            _DependencyRest(self._config()),
            _DependencyWebSocket(
                registry,
                [
                    {
                        "label_id": "climate_watch_id",
                        "name": "climate_watch",
                    }
                ],
            ),
        )

        result = await provider.scan()

        self.assertTrue(result.label_registry_complete)
        self.assertEqual(
            result.label_memberships["climate_watch"], ("sensor.a",)
        )
        self.assertEqual(
            len(result.label_membership_fingerprints["climate_watch"]),
            64,
        )

    async def test_duplicate_entity_labels_preserve_complete_evidence(self):
        provider = DirectHaDependencyProvider(
            _DependencyRest(self._config()),
            _DependencyWebSocket(
                [
                    {
                        "entity_id": "sensor.a",
                        "labels": [
                            "climate_watch_id",
                            "climate_watch_id",
                        ],
                    }
                ],
                [
                    {
                        "label_id": "climate_watch_id",
                        "name": "climate_watch",
                    }
                ],
            ),
        )

        result = await provider.scan()

        self.assertTrue(result.label_registry_complete)
        self.assertEqual(
            result.label_memberships["climate_watch"], ("sensor.a",)
        )

    async def test_failed_or_oversized_label_evidence_is_non_conclusive(self):
        failed = DirectHaDependencyProvider(
            _DependencyRest(self._config()),
            _DependencyWebSocket([], [], fail_labels=True),
        )
        failed_result = await failed.scan()
        self.assertFalse(failed_result.label_registry_complete)

        registry = [
            {
                "entity_id": f"sensor.member_{index:03d}",
                "labels": ["climate_watch_id"],
            }
            for index in range(MAX_LABEL_MEMBERSHIP + 1)
        ]
        oversized = DirectHaDependencyProvider(
            _DependencyRest(self._config()),
            _DependencyWebSocket(
                registry,
                [
                    {
                        "label_id": "climate_watch_id",
                        "name": "climate_watch",
                    }
                ],
            ),
        )
        oversized_result = await oversized.scan()
        observed = _binding(
            _snapshot(
                dynamic=oversized_result.dynamic_references,
                label_memberships=oversized_result.label_memberships,
                label_fingerprints=(
                    oversized_result.label_membership_fingerprints
                ),
                label_complete=oversized_result.label_registry_complete,
                label_truncated=(
                    oversized_result.label_membership_truncated
                ),
            )
        )

        self.assertEqual(
            len(oversized_result.label_memberships["climate_watch"]),
            MAX_LABEL_MEMBERSHIP,
        )
        self.assertFalse(observed["evidence_complete"])
        self.assertIn(
            "label_membership_evidence_incomplete",
            observed["resolved_dynamic_reference_evidence"][0][
                "reason_codes"
            ],
        )


class _HealthHelperGateway:
    def health_snapshot(self):
        return {
            "provider": HELPER_STATE_PROVIDER,
            "provider_contract": HELPER_STATE_PROVIDER_CONTRACT,
            "configured": True,
            "operational_status": "configured_unprobed",
            "health": "unknown",
            "fallback": "none",
            "fallback_count": 0,
            "fallback_policy": "none",
            "last_failure_category": None,
        }


class _FailingHealthHelperGateway:
    def health_snapshot(self):
        raise RuntimeError("synthetic helper health failure")


class HelperStateHealthAttributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = ChangeGovernanceService(
            ChangePlanRepository(root),
            object(),
            helper_state_gateway=_HealthHelperGateway(),
        )

    def tearDown(self):
        self.temp.cleanup()

    def _provider(
        self, rest_status: str, websocket_status: str | None = None
    ):
        summary = self.service.health_summary(
            home_assistant_status=rest_status,
            home_assistant_websocket_status=websocket_status,
        )
        operational = summary["operational_administration"]
        return (
            operational["helper_state_provider"],
            operational["operations"]["set_input_boolean_state"],
            operational["lifecycle_provider"],
        )

    def test_connected_health_uses_exact_native_provider(self):
        provider, operation, _lifecycle = self._provider(
            "connected", "connected"
        )

        self.assertEqual(provider["provider"], HELPER_STATE_PROVIDER)
        self.assertEqual(
            provider["provider_contract"], HELPER_STATE_PROVIDER_CONTRACT
        )
        self.assertEqual(provider["operational_status"], "available")
        self.assertEqual(provider["health"], "healthy")
        self.assertEqual(provider["fallback"], "none")
        self.assertEqual(operation["provider_identity"], HELPER_STATE_PROVIDER)
        self.assertEqual(
            operation["provider_contract"], HELPER_STATE_PROVIDER_CONTRACT
        )
        self.assertEqual(operation["provider_contract_status"], "code_owned_exact")
        self.assertNotEqual(
            operation["provider_identity"], "upstream_operational_lifecycle"
        )

    def test_unavailable_ha_is_truthful_without_provider_substitution(self):
        provider, operation, lifecycle_before = self._provider(
            "unavailable", "connected"
        )
        _healthy, _healthy_operation, lifecycle_after = self._provider(
            "connected", "connected"
        )

        self.assertEqual(provider["provider"], HELPER_STATE_PROVIDER)
        self.assertEqual(provider["operational_status"], "unavailable")
        self.assertEqual(provider["health"], "degraded")
        self.assertEqual(
            provider["last_failure_category"],
            "home_assistant_rest_unavailable",
        )
        self.assertEqual(operation["provider_identity"], HELPER_STATE_PROVIDER)
        self.assertEqual(operation["fallback"], "none")
        self.assertEqual(lifecycle_before, lifecycle_after)

    def test_snapshot_failure_cannot_report_provider_available(self):
        service = ChangeGovernanceService(
            ChangePlanRepository(Path(self.temp.name) / "snapshot_failure"),
            object(),
            helper_state_gateway=_FailingHealthHelperGateway(),
        )

        operational = service.health_summary(
            home_assistant_status="connected",
            home_assistant_websocket_status="connected",
        )["operational_administration"]
        provider = operational["helper_state_provider"]
        operation = operational["operations"]["set_input_boolean_state"]

        self.assertEqual(provider["operational_status"], "unavailable")
        self.assertEqual(provider["health"], "degraded")
        self.assertEqual(
            provider["last_failure_category"],
            "helper_state_health_snapshot_failed:RuntimeError",
        )
        self.assertEqual(operation["provider_availability"], "unavailable")

    def test_capability_and_health_share_canonical_attribution(self):
        provider, operation, _lifecycle = self._provider(
            "connected", "connected"
        )
        capability = helper_state_provider_evidence()

        self.assertEqual(capability["provider"], provider["provider"])
        self.assertEqual(
            capability["provider_contract_model"],
            provider["provider_contract"],
        )
        self.assertEqual(operation["provider_identity"], capability["provider"])
        self.assertEqual(capability["fallback"], "none")

    def test_rest_only_or_skipped_probe_never_claims_available(self):
        rest_only, operation, _lifecycle = self._provider(
            "connected", "unavailable"
        )
        unprobed, _unprobed_operation, _ = self._provider(
            "not_checked", "not_checked"
        )

        self.assertEqual(rest_only["operational_status"], "unavailable")
        self.assertEqual(rest_only["health"], "degraded")
        self.assertEqual(
            rest_only["last_failure_category"],
            "home_assistant_websocket_unavailable",
        )
        self.assertEqual(
            rest_only["transport_health"],
            {"rest": "connected", "websocket": "unavailable"},
        )
        self.assertEqual(operation["provider_availability"], "unavailable")
        self.assertEqual(operation["fallback"], "none")
        self.assertEqual(
            unprobed["operational_status"], "configured_unprobed"
        )
        self.assertEqual(unprobed["health"], "unknown")

    def test_unconfigured_helper_provider_is_not_reported_available(self):
        service = ChangeGovernanceService(
            ChangePlanRepository(Path(self.temp.name) / "unconfigured"),
            object(),
            helper_state_gateway=None,
        )

        operational = service.health_summary(
            home_assistant_status="connected"
        )["operational_administration"]
        provider = operational["helper_state_provider"]
        operation = operational["operations"]["set_input_boolean_state"]

        self.assertEqual(provider["provider"], HELPER_STATE_PROVIDER)
        self.assertEqual(
            provider["provider_contract"], HELPER_STATE_PROVIDER_CONTRACT
        )
        self.assertFalse(provider["configured"])
        self.assertEqual(provider["operational_status"], "unavailable")
        self.assertEqual(provider["health"], "unavailable")
        self.assertEqual(
            provider["last_failure_category"], "provider_unconfigured"
        )
        self.assertEqual(provider["fallback"], "none")
        self.assertEqual(operation["provider_identity"], HELPER_STATE_PROVIDER)
        self.assertEqual(operation["provider_availability"], "unavailable")


if __name__ == "__main__":
    unittest.main()
