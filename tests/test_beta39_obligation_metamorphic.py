"""Metamorphic coverage for the Beta 39 dependency-obligation invariant."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

from jinja2 import nodes
from jinja2.sandbox import ImmutableSandboxedEnvironment


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.extraction import valid_entity_id  # noqa: E402
from ha_mcp_engineering.dependency.obligation_ledger import (  # noqa: E402
    analyze_template_obligations,
)


TARGET = "input_boolean.beta39_metamorphic"
_DEPENDENCY_OUTCOMES = frozenset(
    {
        "exact_dependency",
        "bounded_semantic_opaque",
        "coverage_failure",
    }
)
_ENVIRONMENT = ImmutableSandboxedEnvironment(
    extensions=("jinja2.ext.loopcontrols", "jinja2.ext.do")
)


class ObligationMetamorphicTests(unittest.TestCase):
    def _analyze(self, template: str):
        return analyze_template_obligations(
            template,
            source_type="automation",
            source_id="beta39_metamorphic",
            source_entity_id="automation.beta39_metamorphic",
            source_name="Synthetic metamorphic fixture",
            source_state="on",
            config_path="$.condition[0].value_template",
            relation="template_reference",
            configuration_fingerprint="beta39-metamorphic-config-v1",
            entity_id_validator=valid_entity_id,
        ).obligations

    def _assert_dependency_not_absent(self, template: str) -> None:
        obligations = self._analyze(template)
        self.assertTrue(obligations, template)
        self.assertTrue(
            any(item.outcome in _DEPENDENCY_OUTCOMES for item in obligations),
            (template, obligations),
        )
        self.assertFalse(
            all(
                item.outcome == "proven_dependency_neutral"
                for item in obligations
            ),
            (template, obligations),
        )

    def _assert_proven_ordinary(self, template: str) -> None:
        obligations = self._analyze(template)
        self.assertTrue(obligations, template)
        self.assertTrue(
            all(
                item.outcome == "proven_dependency_neutral"
                and item.lock_projection == "none"
                for item in obligations
            ),
            (template, obligations),
        )

    def test_exact_state_read_survives_each_reviewed_composition(self):
        exact_call = f"is_state('{TARGET}', 'on')"
        cases = {
            "direct": "{{ " + exact_call + " }}",
            "parentheses": "{{ ((" + exact_call + ")) }}",
            "list": "{{ [(" + exact_call + ")] | first }}",
            "tuple": "{{ ((" + exact_call + ",),)[0][0] }}",
            "mapping_subscript": (
                "{{ {'outer': {'inner': ("
                + exact_call
                + ",)}}['outer']['inner'][0] }}"
            ),
            "conditional": (
                "{{ (" + exact_call + ") if enabled else false }}"
            ),
            "assignment": (
                "{% set observed = (" + exact_call + ") %}{{ observed }}"
            ),
            "loop": (
                "{% for entity_id in ['"
                + TARGET
                + "'] %}{{ is_state(entity_id, 'on') }}{% endfor %}"
            ),
            "filter_pipeline": (
                "{{ ['" + TARGET + "'] | map('states') | list }}"
            ),
            "later_invocation": (
                "{% set lookup = is_state %}"
                "{% set transported = {'checks': [lookup]} %}"
                "{{ transported['checks'][0]('"
                + TARGET
                + "', 'on') }}"
            ),
            "nested_transport": (
                "{% set lookup = is_state %}"
                "{% set transported = "
                "({'checks': ([lookup] if enabled else [lookup])},) %}"
                "{% for candidate in transported %}"
                "{{ candidate['checks'][0]('"
                + TARGET
                + "', 'on') | string }}"
                "{% endfor %}"
            ),
        }
        for label, template in cases.items():
            with self.subTest(label=label):
                self._assert_dependency_not_absent(template)

    def test_opaque_state_read_survives_each_reviewed_composition(self):
        opaque_call = "states(unbounded_entity)"
        cases = {
            "direct": "{{ " + opaque_call + " }}",
            "parentheses": "{{ ((" + opaque_call + ")) }}",
            "list": "{{ [(" + opaque_call + ")] | first }}",
            "tuple": "{{ ((" + opaque_call + ",),)[0][0] }}",
            "mapping_subscript": (
                "{{ {'outer': {'inner': ("
                + opaque_call
                + ",)}}['outer']['inner'][0] }}"
            ),
            "conditional": (
                "{{ (" + opaque_call + ") if enabled else 'idle' }}"
            ),
            "assignment": (
                "{% set observed = (" + opaque_call + ") %}{{ observed }}"
            ),
            "loop": (
                "{% for entity_id in [unbounded_entity] %}"
                "{{ states(entity_id) }}{% endfor %}"
            ),
            "filter_pipeline": (
                "{{ [unbounded_entity] | map('states') | list }}"
            ),
            "later_invocation": (
                "{% set lookup = states %}"
                "{% set transported = {'checks': [lookup]} %}"
                "{{ transported['checks'][0](unbounded_entity) }}"
            ),
            "nested_transport": (
                "{% set lookup = states %}"
                "{% set transported = "
                "({'checks': ([lookup] if enabled else [lookup])},) %}"
                "{% for candidate in transported %}"
                "{{ candidate['checks'][0](unbounded_entity) | string }}"
                "{% endfor %}"
            ),
        }
        for label, template in cases.items():
            with self.subTest(label=label):
                self._assert_dependency_not_absent(template)

    def test_proven_ordinary_counterparts_remain_lock_free(self):
        cases = {
            "direct": "{{ 'ready' }}",
            "parentheses": "{{ (('ready')) }}",
            "list": "{{ [('ready')] | first }}",
            "tuple": "{{ (('ready',),)[0][0] }}",
            "mapping_subscript": (
                "{{ {'outer': {'inner': ('ready',)}}"
                "['outer']['inner'][0] }}"
            ),
            "conditional": "{{ 'ready' if enabled else 'idle' }}",
            "assignment": "{% set observed = 'ready' %}{{ observed }}",
            "loop": (
                "{% for value in ['ready'] %}{{ value }}{% endfor %}"
            ),
            "filter_pipeline": "{{ ' ready ' | trim | upper | lower }}",
            "later_invocation": (
                "{% set transform = 'ready'.upper %}{{ transform() }}"
            ),
            "nested_transport": (
                "{% set transform = 'ready'.upper %}"
                "{% set transported = "
                "({'checks': ([transform] if enabled else [transform])},) %}"
                "{% for candidate in transported %}"
                "{{ candidate['checks'][0]() | lower }}"
                "{% endfor %}"
            ),
        }
        for label, template in cases.items():
            with self.subTest(label=label):
                self._assert_proven_ordinary(template)

    def test_candidate_provenance_survives_runtime_narrowing_compositions(self):
        cases = {
            "sequence_addition": (
                "{{ states(((['"
                + TARGET
                + "'] + ['sensor.a']) | first) or 'sensor.b') }}"
            ),
            "selection": (
                "{{ states(['sensor.a','"
                + TARGET
                + "'] | select('equalto','"
                + TARGET
                + "') | first) }}"
            ),
            "attribute_selection": (
                "{{ states([{'id':'sensor.a','ok':false},"
                "{'id':'"
                + TARGET
                + "','ok':true}] | selectattr('ok') "
                "| map(attribute='id') | first) }}"
            ),
            "mapping_key_iteration": (
                "{{ states(({'"
                + TARGET
                + "':'ready'} | list | first) or 'sensor.a') }}"
            ),
            "namespace_alias_scope": (
                "{% set ns=namespace(x='sensor.a') %}"
                "{% macro mutate(value) %}{% set value.x='"
                + TARGET
                + "' %}{% endmacro %}{{ mutate([ns][0]) }}"
                "{{ states(ns.x or 'sensor.b') }}"
            ),
            "constructed_format_result": (
                "{{ states(('%s.%s' | format('input_boolean',"
                "'beta39_metamorphic')) or 'sensor.a') }}"
            ),
        }
        for label, template in cases.items():
            with self.subTest(label=label):
                self._assert_dependency_not_absent(template)

    def test_reviewed_ast_transfer_and_fallback_matrix(self):
        exact_call = f"is_state('{TARGET}', 'on')"
        cases = (
            (
                "Assign",
                "{% set lookup = is_state %}{{ lookup('"
                + TARGET
                + "', 'on') }}",
                nodes.Assign,
                _DEPENDENCY_OUTCOMES,
            ),
            (
                "AssignBlock",
                "{% set observed %}{{ "
                + exact_call
                + " }}{% endset %}{{ observed }}",
                nodes.AssignBlock,
                _DEPENDENCY_OUTCOMES,
            ),
            (
                "For",
                "{% for entity_id in ['"
                + TARGET
                + "'] %}{{ states(entity_id) }}{% endfor %}",
                nodes.For,
                _DEPENDENCY_OUTCOMES,
            ),
            (
                "If",
                "{% if enabled %}{{ "
                + exact_call
                + " }}{% else %}ready{% endif %}",
                nodes.If,
                _DEPENDENCY_OUTCOMES,
            ),
            (
                "Macro",
                "{% macro check(entity_id) %}"
                "{{ is_state(entity_id, 'on') }}{% endmacro %}"
                "{{ check('"
                + TARGET
                + "') }}",
                nodes.Macro,
                _DEPENDENCY_OUTCOMES,
            ),
            (
                "CallBlock",
                "{% macro wrapper() %}{{ caller() }}{% endmacro %}"
                "{% call wrapper() %}{{ "
                + exact_call
                + " }}{% endcall %}",
                nodes.CallBlock,
                _DEPENDENCY_OUTCOMES,
            ),
            (
                "With",
                "{% with lookup = is_state %}{{ lookup('"
                + TARGET
                + "', 'on') }}{% endwith %}",
                nodes.With,
                _DEPENDENCY_OUTCOMES,
            ),
            (
                "Getattr",
                "{% set helpers = {'lookup': is_state} %}"
                "{{ helpers.lookup('"
                + TARGET
                + "', 'on') }}",
                nodes.Getattr,
                _DEPENDENCY_OUTCOMES,
            ),
            (
                "Getitem",
                "{% set helpers = {'lookup': is_state} %}"
                "{{ helpers['lookup']('"
                + TARGET
                + "', 'on') }}",
                nodes.Getitem,
                _DEPENDENCY_OUTCOMES,
            ),
            (
                "CondExpr",
                "{{ " + exact_call + " if enabled else false }}",
                nodes.CondExpr,
                _DEPENDENCY_OUTCOMES,
            ),
            (
                "Filter",
                "{{ ['" + TARGET + "'] | map('states') | list }}",
                nodes.Filter,
                _DEPENDENCY_OUTCOMES,
            ),
            (
                "Test",
                "{{ '" + TARGET + "' is is_state('on') }}",
                nodes.Test,
                _DEPENDENCY_OUTCOMES,
            ),
            (
                "Concat",
                "{{ states('input_boolean.' ~ 'beta39_metamorphic') }}",
                nodes.Concat,
                _DEPENDENCY_OUTCOMES,
            ),
            (
                "FilterBlock fallback",
                "{% filter future_filter %}ready{% endfilter %}",
                nodes.FilterBlock,
                frozenset({"bounded_semantic_opaque", "coverage_failure"}),
            ),
            (
                "Import fallback",
                "{% import 'custom/helper.jinja' as helper %}",
                nodes.Import,
                frozenset({"bounded_semantic_opaque", "coverage_failure"}),
            ),
            (
                "Include fallback",
                "{% include template_name %}",
                nodes.Include,
                frozenset({"bounded_semantic_opaque", "coverage_failure"}),
            ),
            (
                "Extends fallback",
                "{% extends 'custom/base.jinja' %}",
                nodes.Extends,
                frozenset({"bounded_semantic_opaque", "coverage_failure"}),
            ),
            (
                "Unknown callable fallback",
                "{{ unknown_callable('" + TARGET + "') }}",
                nodes.Call,
                frozenset({"bounded_semantic_opaque", "coverage_failure"}),
            ),
        )

        covered_node_types: set[type[nodes.Node]] = set()
        for label, template, expected_node, expected_outcomes in cases:
            with self.subTest(label=label):
                tree = _ENVIRONMENT.parse(template)
                encountered = {
                    type(item) for item in tree.find_all(nodes.Node)
                }
                self.assertIn(expected_node, encountered)
                covered_node_types.add(expected_node)
                obligations = self._analyze(template)
                self.assertTrue(obligations)
                self.assertTrue(
                    any(
                        item.outcome in expected_outcomes
                        for item in obligations
                    ),
                    (label, obligations),
                )

        self.assertEqual(
            {
                nodes.Assign,
                nodes.AssignBlock,
                nodes.Call,
                nodes.CallBlock,
                nodes.Concat,
                nodes.CondExpr,
                nodes.Extends,
                nodes.Filter,
                nodes.FilterBlock,
                nodes.For,
                nodes.Getattr,
                nodes.Getitem,
                nodes.If,
                nodes.Import,
                nodes.Include,
                nodes.Macro,
                nodes.Test,
                nodes.With,
            },
            covered_node_types,
        )


if __name__ == "__main__":
    unittest.main()
