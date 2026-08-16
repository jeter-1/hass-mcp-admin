from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.models import (  # noqa: E402
    OBLIGATION_OUTCOMES,
)
from ha_mcp_engineering.dependency.obligation_ledger import (  # noqa: E402
    MAX_TEMPLATE_CANDIDATES,
    MAX_TEMPLATE_OBLIGATIONS,
    MAX_TEMPLATE_SOURCE_CHARS,
    TemplateContextEvidence,
    analyze_template_obligations,
)


TARGET = "input_boolean.mcp_f2_standard_admin_test_flag"


def _valid_entity_id(value: str) -> bool:
    domain, separator, object_id = value.partition(".")
    return bool(separator and domain and object_id and " " not in value)


class WholeTemplateObligationLedgerTests(unittest.TestCase):
    def _analyze(
        self,
        template: str,
        *,
        context: TemplateContextEvidence | None = None,
        entity_output_role: bool = False,
        config_path: str = "action.0.data.message",
        configuration_fingerprint: str = "fixture-configuration-v1",
    ):
        return analyze_template_obligations(
            template,
            source_type="automation",
            source_id="automation.beta39_fixture",
            config_path=config_path,
            relation="template_reference",
            source_entity_id="automation.beta39_fixture",
            source_name="Beta 39 fixture",
            source_state="on",
            configuration_fingerprint=configuration_fingerprint,
            entity_id_validator=_valid_entity_id,
            context=context,
            entity_output_role=entity_output_role,
        )

    def _outcomes(self, template: str, **kwargs):
        return self._analyze(template, **kwargs).obligations

    def _assert_exact_target(self, template: str) -> None:
        obligations = self._outcomes(template)
        self.assertTrue(
            any(
                item.outcome == "exact_dependency"
                and TARGET in item.exact_entity_ids
                for item in obligations
            ),
            obligations,
        )

    def _assert_opaque(self, template: str, **kwargs) -> None:
        obligations = self._outcomes(template, **kwargs)
        self.assertTrue(
            any(item.outcome == "bounded_semantic_opaque" for item in obligations),
            obligations,
        )
        self.assertTrue(
            any(item.lock_projection == "conservative" for item in obligations),
            obligations,
        )

    def test_historical_transport_compositions_never_erase_exact_dependency(self):
        cases = {
            "direct": "{{ is_state('" + TARGET + "', 'on') }}",
            "parentheses": "{{ (is_state)('" + TARGET + "', 'on') }}",
            "cross_segment_alias": (
                "{% set lookup = is_state %}\n"
                "{{ lookup('" + TARGET + "', 'on') }}"
            ),
            "shadowed_neutral_global": (
                "{% set now = is_state %}"
                "{{ now('" + TARGET + "', 'on') }}"
            ),
            "list_transport": (
                "{% set values = [is_state] %}"
                "{{ values[0]('" + TARGET + "', 'on') }}"
            ),
            "tuple_transport": (
                "{% set values = (is_state,) %}"
                "{{ values[0]('" + TARGET + "', 'on') }}"
            ),
            "mapping_dot": (
                "{% set values = {'lookup': is_state} %}"
                "{{ values.lookup('" + TARGET + "', 'on') }}"
            ),
            "mapping_bracket": (
                "{% set values = {'lookup': is_state} %}"
                "{{ values['lookup']('" + TARGET + "', 'on') }}"
            ),
            "mapping_get": (
                "{% set values = {'lookup': is_state} %}"
                "{{ (values.get)('lookup')('" + TARGET + "', 'on') }}"
            ),
            "mapping_method_fallback": (
                "{% set values = {'message': 'ready'} %}"
                "{{ values['get']('missing', is_state)('"
                + TARGET
                + "', 'on') }}"
            ),
            "finite_dynamic_mapping_method_fallback": (
                "{% set values = {'message': 'ready'} %}"
                "{% set method_name = 'get' %}"
                "{{ [values[method_name]][0]('missing', is_state)('"
                + TARGET
                + "', 'on') }}"
            ),
            "conditional": (
                "{% set lookup = is_state if enabled else is_state %}"
                "{{ lookup('" + TARGET + "', 'on') }}"
            ),
            "loop": (
                "{% for lookup in [is_state] %}"
                "{{ lookup('" + TARGET + "', 'on') }}"
                "{% endfor %}"
            ),
            "local_macro": (
                "{% macro check(entity_id) %}"
                "{{ is_state(entity_id, 'on') }}"
                "{% endmacro %}"
                "{{ check('" + TARGET + "') }}"
            ),
            "dict_constructor": (
                "{% set values = dict(lookup=is_state) %}"
                "{{ values.lookup('" + TARGET + "', 'on') }}"
            ),
        }
        for label, template in cases.items():
            with self.subTest(label=label):
                self._assert_exact_target(template)

    def test_if_elif_else_branches_union_without_order_loss(self):
        branch_values = (
            (TARGET, "sensor.second", "sensor.last"),
            ("sensor.first", TARGET, "sensor.last"),
            ("sensor.first", "sensor.second", TARGET),
            ("sensor.last", TARGET, "sensor.first"),
        )
        for first, second, final in branch_values:
            with self.subTest(
                first=first,
                second=second,
                final=final,
            ):
                template = (
                    "{% set selected='sensor.base' %}"
                    "{% if mode == 'first' %}{% set selected='"
                    + first
                    + "' %}{% elif mode == 'second' %}"
                    "{% set selected='"
                    + second
                    + "' %}{% else %}{% set selected='"
                    + final
                    + "' %}{% endif %}"
                    "{{ states(selected) }}"
                )
                self._assert_exact_target(template)

        unrelated = self._outcomes(
            "{% set selected='sensor.base' %}"
            "{% if mode == 'first' %}{% set selected='sensor.first' %}"
            "{% elif mode == 'second' %}{% set selected='sensor.second' %}"
            "{% else %}{% set selected='sensor.last' %}{% endif %}"
            "{{ states(selected) }}"
        )
        self.assertFalse(
            any(TARGET in item.exact_entity_ids for item in unrelated),
            unrelated,
        )
        self.assertFalse(
            any(item.outcome == "bounded_semantic_opaque" for item in unrelated),
            unrelated,
        )

    def test_macro_definition_identity_survives_aliases_and_branches(self):
        cases = (
            (
                "{% macro f(e) %}{{ states(e) }}{% endmacro %}"
                "{% set old=f %}"
                "{% macro f(e) %}ready{% endmacro %}"
                "{{ old('" + TARGET + "') }}"
            ),
            (
                "{% macro f(e) %}ready{% endmacro %}"
                "{% set neutral=f %}"
                "{% macro f(e) %}{{ states(e) }}{% endmacro %}"
                "{{ f('" + TARGET + "') }}{{ neutral('sensor.a') }}"
            ),
            (
                "{% if enabled %}"
                "{% macro f(e) %}{{ states(e) }}{% endmacro %}"
                "{% else %}{% macro f(e) %}ready{% endmacro %}{% endif %}"
                "{{ f('" + TARGET + "') }}"
            ),
            (
                "{% with marker='x' %}"
                "{% macro f(e) %}{{ states(e) }}{% endmacro %}"
                "{{ f('" + TARGET + "') }}{% endwith %}"
            ),
            (
                "{% for marker in ['x'] %}"
                "{% macro f(e) %}{{ states(e) }}{% endmacro %}"
                "{{ f('" + TARGET + "') }}{% endfor %}"
            ),
        )
        for template in cases:
            with self.subTest(template=template):
                self._assert_exact_target(template)

        neutral = self._outcomes(
            "{% macro f(e) %}ready{% endmacro %}{{ f('sensor.a') }}"
        )
        self.assertTrue(neutral)
        self.assertFalse(
            any(item.outcome == "bounded_semantic_opaque" for item in neutral),
            neutral,
        )

    def test_macro_calls_resolve_definition_frame_not_caller_shadows(self):
        lexical_exact = (
            "{% set lookup=is_state %}"
            "{% macro f(e) %}{{ lookup(e, 'on') }}{% endmacro %}"
            "{% with lookup=now %}{{ f('" + TARGET + "') }}{% endwith %}"
        )
        self._assert_exact_target(lexical_exact)

        caller_only_selector = (
            "{% set lookup=now %}"
            "{% macro f(e) %}{{ lookup() }}{% endmacro %}"
            "{% with lookup=is_state %}{{ f('" + TARGET + "') }}{% endwith %}"
        )
        obligations = self._outcomes(caller_only_selector)
        self.assertFalse(
            any(TARGET in item.exact_entity_ids for item in obligations),
            obligations,
        )

        later_same_frame_rebind = (
            "{% set lookup=now %}"
            "{% macro f(e) %}{{ lookup(e, 'on') }}{% endmacro %}"
            "{% set lookup=is_state %}{{ f('" + TARGET + "') }}"
        )
        self._assert_exact_target(later_same_frame_rebind)

        lexical_default = (
            "{% set selected='" + TARGET + "' %}"
            "{% macro f(e=selected) %}{{ states(e) }}{% endmacro %}"
            "{% with selected='sensor.unrelated' %}{{ f() }}{% endwith %}"
        )
        self._assert_exact_target(lexical_default)

        parameter_default = (
            "{% set e='sensor.unrelated' %}"
            "{% macro f(e, other=e) %}{{ states(other) }}{% endmacro %}"
            "{{ f('" + TARGET + "') }}"
        )
        self._assert_exact_target(parameter_default)

        special_bindings = (
            (
                "{% set varargs=['sensor.unrelated'] %}"
                "{% macro f() %}{{ states(varargs[0]) }}{% endmacro %}"
                "{{ f('" + TARGET + "') }}"
            ),
            (
                "{% set kwargs={'entity':'sensor.unrelated'} %}"
                "{% macro f() %}{{ states(kwargs.entity) }}{% endmacro %}"
                "{{ f(entity='" + TARGET + "') }}"
            ),
        )
        for template in special_bindings:
            with self.subTest(template=template):
                self._assert_exact_target(template)

        for nested_call in (
            "{% with marker='x' %}{{ f('" + TARGET + "') }}{% endwith %}",
            "{% for marker in ['x'] %}{{ f('" + TARGET + "') }}{% endfor %}",
        ):
            with self.subTest(nested_call=nested_call):
                self._assert_exact_target(
                    "{% set lookup=now %}{% if enabled %}"
                    "{% set lookup=is_state %}"
                    "{% macro f(e) %}{{ lookup(e, 'on') }}{% endmacro %}"
                    + nested_call
                    + "{% endif %}"
                )

        path_default = (
            "{% set selected='sensor.unrelated' %}{% if enabled %}"
            "{% set selected='" + TARGET + "' %}"
            "{% macro f(e=selected) %}{{ states(e) }}{% endmacro %}"
            "{% with marker='x' %}{{ f() }}{% endwith %}{% endif %}"
        )
        self._assert_exact_target(path_default)

        later_definition_path_rebind = (
            "{% set lookup=now %}{% if enabled %}"
            "{% macro f(e) %}{{ lookup(e, 'on') }}{% endmacro %}"
            "{% set lookup=is_state %}"
            "{% with marker='x' %}{{ f('" + TARGET + "') }}{% endwith %}"
            "{% endif %}"
        )
        self._assert_exact_target(later_definition_path_rebind)

        later_path_default_rebind = (
            "{% set selected='sensor.unrelated' %}{% if enabled %}"
            "{% macro f(e=selected) %}{{ states(e) }}{% endmacro %}"
            "{% set selected='" + TARGET + "' %}"
            "{% with marker='x' %}{{ f() }}{% endwith %}{% endif %}"
        )
        self._assert_exact_target(later_path_default_rebind)

        later_path_rebind = (
            "{% set lookup=now %}{% if enabled %}"
            "{% set lookup=is_state %}"
            "{% macro f(e) %}{{ lookup() }}{% endmacro %}{% endif %}"
            "{% set lookup=now %}"
            "{% with marker='x' %}{{ f('" + TARGET + "') }}{% endwith %}"
        )
        later_obligations = self._outcomes(later_path_rebind)
        self.assertFalse(
            any(TARGET in item.exact_entity_ids for item in later_obligations),
            later_obligations,
        )

    def test_state_vocabulary_and_computed_dispatch_retain_dependency(self):
        cases = (
            "{{ states('" + TARGET + "') }}",
            "{{ state_attr('" + TARGET + "', 'friendly_name') }}",
            "{{ state_translated('" + TARGET + "') }}",
            "{{ state_attr_translated('" + TARGET + "', 'device_class') }}",
            "{{ is_state('" + TARGET + "', 'on') }}",
            "{{ is_state_attr('" + TARGET + "', 'armed', true) }}",
            "{{ has_value('" + TARGET + "') }}",
            "{{ expand(['" + TARGET + "']) | list }}",
            "{{ closest(['" + TARGET + "']) }}",
            "{{ distance('" + TARGET + "', 'sensor.other') }}",
            "{{ ['" + TARGET + "'] | map('states') | list }}",
            "{{ ['" + TARGET + "'] | map('sta' ~ 'tes') | list }}",
            "{{ ['" + TARGET + "'] | select('is_state', 'on') | list }}",
            "{{ ['" + TARGET + "'] | reject('has_value') | list }}",
            "{{ '" + TARGET + "' | states }}",
            "{{ '" + TARGET + "' is is_state('on') }}",
        )
        for template in cases:
            with self.subTest(template=template):
                self._assert_exact_target(template)

    def test_canonical_state_collections_preserve_exact_and_domain_evidence(self):
        for template in (
            "{{ states.input_boolean.mcp_f2_standard_admin_test_flag.state }}",
            "{{ states['input_boolean.mcp_f2_standard_admin_test_flag'].state }}",
            "{{ states.input_boolean['mcp_f2_standard_admin_test_flag'].state }}",
        ):
            with self.subTest(template=template):
                self._assert_exact_target(template)

        domain = self._outcomes("{{ states.input_boolean | list }}")
        self.assertTrue(
            any(
                item.possible_entity_domains == ("input_boolean",)
                and item.lock_projection == "conservative"
                for item in domain
            ),
            domain,
        )

    def test_mixed_state_collection_provenance_never_becomes_exact_exclusion(self):
        cases = (
            (
                "{% set lookup = states.sensor if enabled else unknown_collection %}"
                "{{ lookup.foo }}"
            ),
            (
                "{% set lookup = states.sensor if enabled else unknown_collection %}"
                "{{ lookup['foo'] }}"
            ),
            (
                "{% set lookup = states if enabled else unknown_collection %}"
                "{{ lookup.input_boolean.mcp_f2_standard_admin_test_flag }}"
            ),
            (
                "{% set lookup = states if enabled else unknown_collection %}"
                "{{ lookup['" + TARGET + "'] }}"
            ),
        )
        for template in cases:
            with self.subTest(template=template):
                obligations = self._outcomes(template)
                self.assertTrue(
                    any(
                        item.outcome == "bounded_semantic_opaque"
                        and item.lock_projection == "conservative"
                        for item in obligations
                    ),
                    obligations,
                )

    def test_structural_narrowing_preserves_parent_uncertainty(self):
        cases = (
            (
                "{% set mapping = {'x': states.sensor} if enabled else unknown_mapping %}"
                "{{ mapping.x.foo }}"
            ),
            (
                "{% set mapping = {'x': states.sensor} if enabled else unknown_mapping %}"
                "{{ mapping.get('x').foo }}"
            ),
            (
                "{% set values = ['sensor.a'] if enabled else unknown_list %}"
                "{{ states(values | first) }}"
            ),
            (
                "{% set values = ['sensor.a'] if enabled else unknown_list %}"
                "{{ states(values | last) }}"
            ),
            (
                "{% set values = ['sensor.a'] if enabled else unknown_list %}"
                "{{ states(values[0]) }}"
            ),
            (
                "{% set values = ['sensor.a'] if enabled else ['" + TARGET + "'] %}"
                "{{ states(values | first) }}"
            ),
        )
        for template in cases:
            with self.subTest(template=template):
                obligations = self._outcomes(template)
                self.assertTrue(
                    any(
                        item.outcome == "bounded_semantic_opaque"
                        or TARGET in item.exact_entity_ids
                        for item in obligations
                    ),
                    obligations,
                )

    def test_iteration_preserves_each_positional_alternative(self):
        mapping_orders = (
            "{'a':'" + TARGET + "','m':'sensor.m','z':'sensor.z'}",
            "{'a':'sensor.a','m':'" + TARGET + "','z':'sensor.z'}",
            "{'a':'sensor.a','m':'sensor.m','z':'" + TARGET + "'}",
        )
        cases = [
            (
                "{% set values="
                + mapping
                + " %}{% for key,value in values.items() %}"
                "{{ states(value) }}{% endfor %}"
            )
            for mapping in mapping_orders
        ]
        cases.extend(
            (
                "{% for pair in [('a','sensor.a'),('z','"
                + TARGET
                + "')] %}{{ states(pair[1]) }}{% endfor %}",
                "{% for key,value in [('a','sensor.a'),('z','"
                + TARGET
                + "')] %}{{ states(value) }}{% endfor %}",
            )
        )
        for template in cases:
            with self.subTest(template=template):
                self._assert_exact_target(template)

    def test_reordering_and_reshaping_filters_cannot_erase_candidates(self):
        cases = (
            "{{ states(['sensor.a', '" + TARGET + "'] | reverse | first) }}",
            "{{ states(['sensor.a', '" + TARGET + "'] | sort | first) }}",
            (
                "{{ states(['sensor.a', '"
                + TARGET
                + "', 'sensor.a'] | unique | last) }}"
            ),
            (
                "{{ states(['sensor.a', '"
                + TARGET
                + "'] | batch(2) | first | last) }}"
            ),
            (
                "{{ states(['sensor.a', '"
                + TARGET
                + "'] | slice(1) | first | last) }}"
            ),
        )
        for template in cases:
            with self.subTest(template=template):
                obligations = self._outcomes(template)
                self.assertTrue(
                    any(
                        TARGET in item.exact_entity_ids
                        or item.outcome
                        in {"bounded_semantic_opaque", "coverage_failure"}
                        for item in obligations
                    ),
                    obligations,
                )

    def test_binding_aware_neutral_contracts_never_erase_state_operands(self):
        cases = {
            "dict_constructor": "{{ dict(states) }}",
            "namespace_constructor": "{{ namespace(states) }}",
            "string_join": "{{ ','.join(states) }}",
            "membership": "{{ 'sensor.fixture' in states }}",
            "destructuring": "{% set first, second = states %}{{ 'ready' }}",
            "mapping_values_transport": (
                "{% set values = {'lookup': states} %}"
                "{{ values.values() | first | list }}"
            ),
            "mapping_items_transport": (
                "{% set values = {'lookup': states} %}"
                "{{ values.items() | first | last | list }}"
            ),
        }
        for label, template in cases.items():
            with self.subTest(label=label):
                obligations = self._outcomes(template)
                self.assertTrue(
                    any(
                        item.outcome
                        in {
                            "exact_dependency",
                            "bounded_semantic_opaque",
                            "coverage_failure",
                        }
                        for item in obligations
                    ),
                    obligations,
                )
                self.assertFalse(
                    all(
                        item.outcome == "proven_dependency_neutral"
                        for item in obligations
                    ),
                    obligations,
                )

    def test_binding_aware_neutral_contracts_preserve_ordinary_values(self):
        for template in (
            "{{ dict({'message': 'ready'}).get('message') }}",
            "{{ namespace(message='ready').message }}",
            "{{ ','.join(['ready', 'set']) }}",
            "{{ 'ready' in ['ready'] }}",
            "{% set first, second = ['ready', 'set'] %}{{ first ~ second }}",
        ):
            with self.subTest(template=template):
                obligations = self._outcomes(template)
                self.assertTrue(obligations)
                self.assertTrue(
                    all(
                        item.outcome == "proven_dependency_neutral"
                        for item in obligations
                    ),
                    obligations,
                )

    def test_namespace_method_named_fields_remain_namespace_attributes(self):
        cases = (
            "{{ states(namespace(get='" + TARGET + "').get or 'sensor.a') }}",
            (
                "{% set selected = namespace(items='" + TARGET + "') %}"
                "{{ states(selected.items or 'sensor.a') }}"
            ),
            (
                "{% set selected = namespace(keys='" + TARGET + "') %}"
                "{{ states(selected.keys or 'sensor.a') }}"
            ),
            (
                "{% set selected = namespace(values='" + TARGET + "') %}"
                "{{ states(selected.values or 'sensor.a') }}"
            ),
        )
        for template in cases:
            with self.subTest(template=template):
                self._assert_exact_target(template)

        ordinary = self._outcomes(
            "{{ namespace(get='ready').get or 'fallback' }}"
        )
        self.assertTrue(ordinary)
        self.assertTrue(
            all(item.outcome == "proven_dependency_neutral" for item in ordinary),
            ordinary,
        )

    def test_mapping_iteration_projects_keys_before_narrowing(self):
        cases = (
            (
                "{{ states(({'"
                + TARGET
                + "':'ready'} | first) or 'sensor.a') }}"
            ),
            (
                "{{ states(({'"
                + TARGET
                + "':'ready'} | list | first) or 'sensor.a') }}"
            ),
            (
                "{% set values = {'sensor.a':'ready','"
                + TARGET
                + "':'set'} %}"
                "{{ states(values | reverse | first) }}"
            ),
        )
        for template in cases:
            with self.subTest(template=template):
                self._assert_exact_target(template)

        unrelated = self._outcomes(
            "{{ states(({'sensor.a':'ready'} | list | first) or 'sensor.b') }}"
        )
        self.assertFalse(
            any(TARGET in item.exact_entity_ids for item in unrelated),
            unrelated,
        )

    def test_dict_and_namespace_positional_construction_preserves_fields(self):
        cases = (
            (
                "{% set selected=dict((('x','"
                + TARGET
                + "'),)) %}"
                "{{ states(selected.get('x') or 'sensor.a') }}"
            ),
            (
                "{{ states((dict((('"
                + TARGET
                + "','ready'),)) | list | first) or 'sensor.a') }}"
            ),
            (
                "{% set selected=namespace({'x':'"
                + TARGET
                + "'}) %}"
                "{{ states(selected.x or 'sensor.a') }}"
            ),
            (
                "{% set selected=namespace((('x','"
                + TARGET
                + "'),)) %}"
                "{{ states(selected.x or 'sensor.a') }}"
            ),
        )
        for template in cases:
            with self.subTest(template=template):
                self._assert_exact_target(template)

        for template in (
            "{{ states(dict(unknown_pairs).get('x') or 'sensor.a') }}",
            "{{ states(namespace(unknown_mapping).x or 'sensor.a') }}",
            "{{ states(dict(('malformed',)).get('x') or 'sensor.a') }}",
        ):
            with self.subTest(template=template):
                self._assert_opaque(template)

    def test_dynamic_context_scalars_taint_later_entity_selection_only(self):
        selectors = (
            "{{ states(trigger.id or 'sensor.a') }}",
            "{{ states(trigger.alias or 'sensor.a') }}",
            "{{ states(trigger.description or 'sensor.a') }}",
            "{{ states(trigger.platform or 'sensor.a') }}",
            "{{ states(trigger.event.event_type or 'sensor.a') }}",
            "{{ states(wait.trigger.id or 'sensor.a') }}",
            "{{ states(wait.trigger.event.event_type or 'sensor.a') }}",
        )
        for template in selectors:
            with self.subTest(template=template):
                self._assert_opaque(template)

        for template in (
            "{{ trigger.id }}",
            "{{ trigger.event.event_type }}",
            "{{ wait.trigger.id }}",
            "{{ wait.completed }}",
            "{{ wait.remaining }}",
        ):
            with self.subTest(template=template):
                obligations = self._outcomes(template)
                self.assertTrue(obligations)
                self.assertTrue(
                    all(
                        item.outcome == "proven_dependency_neutral"
                        for item in obligations
                    ),
                    obligations,
                )

        for template in (
            "{{ states(wait.completed or 'sensor.a') }}",
            "{{ states(wait.remaining or 'sensor.a') }}",
        ):
            with self.subTest(template=template):
                self._assert_opaque(template)

    def test_typed_event_and_datetime_values_are_neutral_until_selection(self):
        ordinary = (
            "{{ trigger.event.origin.name }}",
            "{{ trigger.event.context.id }}",
            "{{ trigger.event.time_fired.isoformat() }}",
            "{{ now().year }}",
            "{{ now().strftime('%Y-%m-%d') }}",
            "{{ utcnow().date().isoformat() }}",
            "{{ today_at('07:30').hour }}",
        )
        for template in ordinary:
            with self.subTest(template=template):
                obligations = self._outcomes(template)
                self.assertTrue(obligations)
                self.assertTrue(
                    all(
                        item.outcome == "proven_dependency_neutral"
                        and item.lock_projection == "none"
                        for item in obligations
                    ),
                    obligations,
                )

        for template in (
            "{{ states(trigger.event.origin.name) }}",
            "{{ states(trigger.event.context.id) }}",
            "{{ states(trigger.event.time_fired.isoformat()) }}",
            "{{ states(now().strftime('%Y')) }}",
            "{{ now().future_method() }}",
            "{{ (now() if enabled else unknown_value).hour }}",
            "{{ (trigger.event if enabled else unknown_value).event_type }}",
        ):
            with self.subTest(template=template):
                self._assert_opaque(template)

    def test_context_data_mapping_reads_are_neutral_until_selection(self):
        ordinary = (
            "{{ trigger.event.data.get('message') }}",
            "{{ trigger.event.data[dynamic_key] }}",
            "{{ trigger.event.data.items() | list }}",
            "{{ trigger.event.data.keys() | list }}",
            "{{ trigger.event.data.values() | list }}",
        )
        for template in ordinary:
            with self.subTest(template=template):
                obligations = self._outcomes(template)
                self.assertTrue(obligations)
                self.assertTrue(
                    all(
                        item.outcome == "proven_dependency_neutral"
                        and item.lock_projection == "none"
                        for item in obligations
                    ),
                    obligations,
                )

        for template in (
            "{{ states(trigger.event.data.get('entity')) }}",
            "{{ states(trigger.event.data[dynamic_key]) }}",
            "{{ trigger.event.data.values() | map('states') | list }}",
        ):
            with self.subTest(template=template):
                self._assert_opaque(template)

        exact_default = self._outcomes(
            "{{ trigger.event.data.get('missing', is_state)(\""
            + TARGET
            + "\", 'on') }}"
        )
        self.assertTrue(
            any(TARGET in item.exact_entity_ids for item in exact_default),
            exact_default,
        )

    def test_jinja_loop_metadata_preserves_item_provenance(self):
        ordinary = self._outcomes(
            "{% for item in ['a', 'b'] %}"
            "{{ loop.index }}:{{ loop.first }}:{{ loop.last }}"
            "{% endfor %}"
        )
        self.assertTrue(ordinary)
        self.assertTrue(
            all(item.outcome == "proven_dependency_neutral" for item in ordinary),
            ordinary,
        )

        self._assert_exact_target(
            "{% for item in ['sensor.a', '" + TARGET + "'] %}"
            "{{ states(loop.nextitem) }}{% endfor %}"
        )
        self._assert_exact_target(
            "{% for item in ['sensor.a'] %}"
            "{{ states(loop.cycle('sensor.a', '" + TARGET + "')) }}"
            "{% endfor %}"
        )
        self._assert_opaque(
            "{% for item in ['a'] %}{{ states(loop.index) }}{% endfor %}"
        )
        self._assert_opaque(
            "{% for item in ['a'] %}"
            "{{ (loop if enabled else unknown_value).index }}"
            "{% endfor %}"
        )
        self._assert_opaque("{{ loop.index }}")

    def test_constructor_keyword_values_preserve_scalar_provenance(self):
        for constructor in ("dict", "namespace"):
            forms = (
                constructor + "(message=trigger.platform)",
                constructor + "({'message':trigger.platform})",
                constructor + "([('message',trigger.platform)])",
            )
            for form in forms:
                with self.subTest(
                    constructor=constructor,
                    form=form,
                    use="display",
                ):
                    template = (
                        "{% set value=" + form + " %}{{ value.message }}"
                    )
                    obligations = self._outcomes(template)
                    self.assertTrue(obligations)
                    self.assertTrue(
                        all(
                            item.outcome == "proven_dependency_neutral"
                            for item in obligations
                        ),
                        obligations,
                    )

                with self.subTest(
                    constructor=constructor,
                    form=form,
                    use="selector",
                ):
                    self._assert_opaque(
                        "{% set value="
                        + form
                        + " %}{{ states(value.message) }}"
                    )

    def test_namespace_branch_assignment_is_order_independent(self):
        branches = (
            (
                "{% if enabled %}{% set ns.x='"
                + TARGET
                + "' %}{% else %}{% set ns.x='sensor.b' %}{% endif %}"
            ),
            (
                "{% if enabled %}{% set ns.x='sensor.b' %}{% else %}"
                "{% set ns.x='"
                + TARGET
                + "' %}{% endif %}"
            ),
        )
        for branch in branches:
            template = (
                "{% set ns=namespace(x='sensor.a') %}"
                + branch
                + "{{ states(ns.x or 'sensor.c') }}"
            )
            with self.subTest(template=template):
                self._assert_exact_target(template)

    def test_namespace_alias_mutations_survive_executing_scopes(self):
        cases = (
            (
                "{% set ns=namespace(x='sensor.a') %}{% set alias=ns %}"
                "{% set alias.x='"
                + TARGET
                + "' %}{{ states(ns.x or 'sensor.c') }}"
            ),
            (
                "{% set ns=namespace(x='sensor.a') %}{% set alias=[ns][0] %}"
                "{% set alias.x='"
                + TARGET
                + "' %}{{ states(ns.x or 'sensor.c') }}"
            ),
            (
                "{% set ns=namespace(x='sensor.a') %}"
                "{% for value in ['ready'] %}{% set ns.x='"
                + TARGET
                + "' %}{% endfor %}{{ states(ns.x or 'sensor.c') }}"
            ),
            (
                "{% set ns=namespace(x='sensor.a') %}"
                "{% for value in values %}{% set ns.x='"
                + TARGET
                + "' %}{% else %}{% set ns.x='sensor.b' %}{% endfor %}"
                "{{ states(ns.x or 'sensor.c') }}"
            ),
            (
                "{% set ns=namespace(x='sensor.a') %}{% with ready=true %}"
                "{% set ns.x='"
                + TARGET
                + "' %}{% endwith %}{{ states(ns.x or 'sensor.c') }}"
            ),
            (
                "{% set ns=namespace(x='sensor.a') %}"
                "{% macro mutate(value) %}{% set value.x='"
                + TARGET
                + "' %}{% endmacro %}{{ mutate(ns) }}"
                "{{ states(ns.x or 'sensor.c') }}"
            ),
            (
                "{% set ns=namespace(x='sensor.a') %}{% set captured %}"
                "{% set ns.x='"
                + TARGET
                + "' %}ready{% endset %}{{ states(ns.x or 'sensor.c') }}"
            ),
            (
                "{% set ns=namespace(x='sensor.a') %}{% block body %}"
                "{% set ns.x='"
                + TARGET
                + "' %}{% endblock %}{{ states(ns.x or 'sensor.c') }}"
            ),
            (
                "{% set ns=namespace(x='sensor.a') %}{% autoescape true %}"
                "{% set ns.x='"
                + TARGET
                + "' %}{% endautoescape %}{{ states(ns.x or 'sensor.c') }}"
            ),
        )
        for template in cases:
            with self.subTest(template=template):
                self._assert_exact_target(template)

    def test_sequence_addition_and_mapping_attribute_iteration_preserve_runtime_values(self):
        cases = (
            (
                "{{ states(((['"
                + TARGET
                + "'] + ['sensor.a']) | first) or 'sensor.b') }}"
            ),
            (
                "{{ states(((('"
                + TARGET
                + "',) + ('sensor.a',)) | first) or 'sensor.b') }}"
            ),
            (
                "{{ states(({'x':'sensor.a'} "
                "| map(attribute='x', default='"
                + TARGET
                + "') | first) or 'sensor.b') }}"
            ),
        )
        for template in cases:
            with self.subTest(template=template):
                self._assert_exact_target(template)

        unrelated = self._outcomes(
            "{{ states(({'x':'sensor.a'} "
            "| map(attribute='x', default='sensor.c') | first) "
            "or 'sensor.b') }}"
        )
        self.assertFalse(
            any(TARGET in item.exact_entity_ids for item in unrelated),
            unrelated,
        )

    def test_selection_filters_cannot_erase_later_runtime_survivors(self):
        cases = (
            (
                "{{ states(['sensor.a','"
                + TARGET
                + "'] | select('equalto','"
                + TARGET
                + "') | first) }}"
            ),
            (
                "{{ states(['sensor.a','"
                + TARGET
                + "'] | reject('equalto','sensor.a') | first) }}"
            ),
            (
                "{{ states([{'id':'sensor.a','ok':false},"
                "{'id':'"
                + TARGET
                + "','ok':true}] | selectattr('ok') "
                "| map(attribute='id') | first) }}"
            ),
            (
                "{{ states([{'id':'sensor.a','ok':false},"
                "{'id':'"
                + TARGET
                + "','ok':true}] | rejectattr('ok','equalto',false) "
                "| map(attribute='id') | first) }}"
            ),
        )
        for template in cases:
            with self.subTest(template=template):
                obligations = self._outcomes(template)
                self.assertTrue(
                    any(
                        TARGET in item.exact_entity_ids
                        or item.outcome == "bounded_semantic_opaque"
                        for item in obligations
                    ),
                    obligations,
                )

    def test_format_filter_result_remains_tainted_for_entity_selection(self):
        cases = (
            (
                "{{ states(('%s.%s' | format('input_boolean',"
                "'mcp_f2_standard_admin_test_flag')) or 'sensor.a') }}"
            ),
            (
                "{{ states(('%(domain)s.%(object)s' | format("
                "domain='input_boolean',"
                "object='mcp_f2_standard_admin_test_flag')) "
                "or 'sensor.a') }}"
            ),
        )
        for template in cases:
            with self.subTest(template=template):
                self._assert_opaque(template)

        ordinary = self._outcomes(
            "{{ '%s: %s' | format('status', 'ready') }}"
        )
        self.assertTrue(ordinary)
        self.assertTrue(
            all(item.outcome == "proven_dependency_neutral" for item in ordinary),
            ordinary,
        )

    def test_slice_projection_cannot_erase_entity_candidates(self):
        cases = (
            (
                "{{ states((['"
                + TARGET
                + "','sensor.a'][:1] | first) or 'sensor.b') }}"
            ),
            (
                "{{ states((['sensor.a','"
                + TARGET
                + "'][1:] | first) or 'sensor.b') }}"
            ),
        )
        for template in cases:
            with self.subTest(template=template):
                obligations = self._outcomes(template)
                self.assertTrue(
                    any(
                        TARGET in item.exact_entity_ids
                        or item.outcome == "bounded_semantic_opaque"
                        for item in obligations
                    ),
                    obligations,
                )

    def test_reviewed_statement_operands_and_defaults_are_accounted(self):
        cases = (
            "{% autoescape states('" + TARGET + "') %}ready{% endautoescape %}",
            "{% macro check(entity=states('" + TARGET + "')) %}{{ entity }}{% endmacro %}{{ check() }}",
            "{% set observed | default(states('" + TARGET + "')) %}ready{% endset %}{{ observed }}",
        )
        for template in cases:
            with self.subTest(template=template):
                self._assert_exact_target(template)

    def test_distance_only_certifies_pure_numeric_coordinates(self):
        ordinary = self._outcomes("{{ distance(0, 1) }}")
        self.assertTrue(
            all(item.outcome == "proven_dependency_neutral" for item in ordinary),
            ordinary,
        )
        mixed = self._outcomes(
            "{{ distance(0, 1 if enabled else '" + TARGET + "') }}"
        )
        self.assertTrue(
            any(TARGET in item.exact_entity_ids for item in mixed),
            mixed,
        )

    def test_unknown_calls_dispatch_and_external_templates_are_explicitly_opaque(self):
        cases = (
            "{{ unknown_callable('" + TARGET + "') }}",
            "{{ unknown_object.lookup('" + TARGET + "') }}",
            "{{ ['" + TARGET + "'] | map(filter_name) | list }}",
            "{{ ['" + TARGET + "'] | select(test_name) | list }}",
            "{% set values = {'message': 'ready'} %}"
            "{{ values[method_name]('missing', is_state)('"
            + TARGET
            + "', 'on') }}",
            "{% import 'custom/source.jinja' as custom %}"
            "{{ custom.check('" + TARGET + "') }}",
            "{% from 'custom/source.jinja' import check %}"
            "{{ check('" + TARGET + "') }}",
            "{% include template_name %}",
            "{% extends 'custom/base.jinja' %}",
            "{% from 'custom/source.jinja' import check %}"
            "{{ check | as_function }}",
        )
        for template in cases:
            with self.subTest(template=template):
                self._assert_opaque(template)

    def test_attribute_dispatch_projects_finite_members_and_defaults(self):
        exact_cases = (
            "{{ ['sensor.a'] | map(attribute='entity_id', default='"
            + TARGET
            + "') | map('states') | list }}",
            "{{ [{'entity_id':'"
            + TARGET
            + "'}, 'sensor.a'] | map(attribute='entity_id', default='sensor.b') | map('states') | list }}",
            "{{ [{'entity_id':'"
            + TARGET
            + "'}, 'sensor.a'] | selectattr('entity_id','is_state','on') | list }}",
            "{{ [{'entity_id':'"
            + TARGET
            + "'}, 'sensor.a'] | rejectattr('entity_id','has_value') | list }}",
        )
        for template in exact_cases:
            with self.subTest(template=template):
                self._assert_exact_target(template)

        dynamic_default = self._outcomes(
            "{{ ['sensor.a'] | map(attribute='entity_id', default=target) | map('states') | list }}"
        )
        self.assertTrue(
            any(
                item.outcome == "bounded_semantic_opaque"
                and item.lock_projection == "conservative"
                for item in dynamic_default
            ),
            dynamic_default,
        )

        sensor_only = self._outcomes(
            "{{ [{'entity_id':'sensor.a'}, 'ready'] | map(attribute='entity_id', default='sensor.b') | map('states') | list }}"
        )
        self.assertTrue(
            any(
                item.outcome == "exact_dependency"
                and set(item.exact_entity_ids) == {"sensor.a", "sensor.b"}
                for item in sensor_only
            ),
            sensor_only,
        )
        self.assertFalse(
            any(TARGET in item.exact_entity_ids for item in sensor_only)
        )

        nested = self._outcomes(
            "{{ [{'foo': {'bar': '"
            + TARGET
            + "'}}] | map(attribute='foo.bar', default='sensor.a') | map('states') | list }}"
        )
        self.assertTrue(
            any(TARGET in item.exact_entity_ids for item in nested),
            nested,
        )
        for template in (
            "{{ [states.sensor.selector] | map(attribute='state', default='sensor.a') | map('states') | list }}",
            "{{ states.sensor | map(attribute='state', default='sensor.a') | map('states') | list }}",
        ):
            with self.subTest(template=template):
                obligations = self._outcomes(template)
                self.assertTrue(
                    any(
                        item.outcome == "bounded_semantic_opaque"
                        and item.lock_projection == "conservative"
                        for item in obligations
                    ),
                    obligations,
                )

    def test_value_returning_state_and_scalar_transforms_taint_later_selectors(self):
        selector_cases = (
            "{{ states(state_attr('sensor.selector','target') or 'sensor.a') }}",
            "{{ states(states('sensor.selector') or 'sensor.a') }}",
            "{{ states(states.sensor.selector.attributes.target or 'sensor.a') }}",
            "{{ states((state_attr('sensor.selector','target') | lower) or 'sensor.a') }}",
            "{{ states(('INPUT_BOOLEAN.MCP_F2_STANDARD_ADMIN_TEST_FLAG' | lower) or 'sensor.a') }}",
            "{{ states(('xinput_boolean.mcp_f2_standard_admin_test_flag' | replace('x','')) | default('sensor.a')) }}",
            "{{ states((['input_boolean','mcp_f2_standard_admin_test_flag'] | join('.')) | default('sensor.a')) }}",
            "{{ states(('INPUT_BOOLEAN.MCP_F2_STANDARD_ADMIN_TEST_FLAG'.lower()) | default('sensor.a')) }}",
            "{{ states((('input_boolean.%s' % 'mcp_f2_standard_admin_test_flag') | default('sensor.a'))) }}",
            "{{ states((['"
            + TARGET
            + "','sensor.a'][0:1] | first) | default('sensor.b')) }}",
        )
        for template in selector_cases:
            with self.subTest(template=template):
                obligations = self._outcomes(template)
                self.assertTrue(
                    any(
                        TARGET in item.exact_entity_ids
                        or item.outcome
                        in {
                            "bounded_semantic_opaque",
                            "coverage_failure",
                        }
                        for item in obligations
                    ),
                    obligations,
                )
                self.assertFalse(
                    all(
                        item.outcome
                        in {
                            "proven_dependency_neutral",
                            "proven_target_exclusion",
                        }
                        or (
                            item.outcome == "exact_dependency"
                            and TARGET not in item.exact_entity_ids
                        )
                        for item in obligations
                    ),
                    obligations,
                )

        rendered = self._outcomes(
            "{{ state_attr('sensor.selector','target') | lower }}"
        )
        self.assertTrue(
            any(
                item.outcome == "exact_dependency"
                and "sensor.selector" in item.exact_entity_ids
                for item in rendered
            ),
            rendered,
        )
        self.assertFalse(
            any(item.outcome == "bounded_semantic_opaque" for item in rendered),
            rendered,
        )

    def test_external_template_evidence_is_bounded_and_fingerprinted(self):
        obligations = self._outcomes(
            "{% import 'custom/source.jinja' as custom %}"
            "{{ custom.check('" + TARGET + "') }}"
        )
        external = [
            item for item in obligations if item.external_template_name is not None
        ]
        self.assertEqual(["custom/source.jinja"], [item.external_template_name for item in external])
        self.assertTrue(all(len(item.expression_fingerprint) == 64 for item in external))
        self.assertTrue(all(item.configuration_fingerprint == "fixture-configuration-v1" for item in external))

    def test_state_bearing_context_is_exact_when_configuration_proves_identity(self):
        context = TemplateContextEvidence(
            trigger_entity_ids=(TARGET,),
            trigger_to_state_entity_ids=(TARGET,),
            trigger_from_state_entity_ids=(TARGET,),
            trigger_zone_entity_ids=("zone.home",),
            wait_trigger_entity_ids=(TARGET,),
            wait_trigger_to_state_entity_ids=(TARGET,),
            this_entity_id="automation.beta39_fixture",
            provenance=("trigger.0.entity_id", "wait_template.0.entity_id"),
        )
        for template, expected in (
            ("{{ trigger }}", TARGET),
            ("{{ trigger.to_state.state }}", TARGET),
            ("{{ trigger.from_state.state }}", TARGET),
            ("{{ trigger.zone.name }}", "zone.home"),
            ("{{ wait.trigger.to_state.state }}", TARGET),
            ("{{ wait.trigger }}", TARGET),
            ("{{ this.entity_id }}", "automation.beta39_fixture"),
        ):
            with self.subTest(template=template):
                obligations = self._outcomes(template, context=context)
                self.assertTrue(
                    any(
                        item.outcome == "exact_dependency"
                        and expected in item.exact_entity_ids
                        for item in obligations
                    ),
                    obligations,
                )

    def test_unconstrained_event_wait_and_trigger_context_remain_opaque(self):
        for template in (
            "{{ trigger }}",
            "{{ trigger.to_state.state }}",
            "{{ wait.trigger.to_state.state }}",
            "{{ wait.trigger }}",
            "{{ states }}",
            "{{ states | list }}",
            "{{ states | count }}",
        ):
            with self.subTest(template=template):
                self._assert_opaque(template)

        displayed_event_value = self._outcomes(
            "{{ trigger.event.data.entity_id }}"
        )
        self.assertTrue(displayed_event_value)
        self.assertTrue(
            all(
                item.outcome == "proven_dependency_neutral"
                for item in displayed_event_value
            ),
            displayed_event_value,
        )
        self._assert_opaque(
            "{{ states(trigger.event.data.entity_id) }}"
        )

    def test_entity_bearing_configuration_output_requires_provenance(self):
        exact = self._outcomes(
            "{{ '" + TARGET + "' if enabled else 'sensor.other' }}",
            entity_output_role=True,
            config_path="action.0.target.entity_id",
        )
        self.assertTrue(
            any(TARGET in item.exact_entity_ids for item in exact), exact
        )
        self._assert_opaque(
            "{{ caller_supplied_entity }}",
            entity_output_role=True,
            config_path="action.0.target.entity_id",
        )

    def test_entity_set_producers_are_explicit_and_never_assumed_inspected(self):
        for template in (
            "{{ label_entities('reviewed_label') }}",
            "{{ area_entities('kitchen') }}",
            "{{ device_entities(device_id_value) }}",
            "{{ floor_entities('ground_floor') }}",
            "{{ integration_entities('mobile_app') }}",
        ):
            with self.subTest(template=template):
                obligations = self._outcomes(template)
                self.assertTrue(
                    any(
                        item.outcome == "bounded_semantic_opaque"
                        and item.semantic_category == "entity_set_producer"
                        for item in obligations
                    ),
                    obligations,
                )

    def test_domain_collection_evidence_preserves_target_specific_precision(self):
        obligations = self._outcomes(
            "{{ states.sensor | selectattr('state', 'eq', 'on') | list }}"
        )
        self.assertTrue(obligations)
        self.assertTrue(
            all(
                item.possible_entity_domains == ("sensor",)
                for item in obligations
                if item.outcome
                in {"exact_dependency", "bounded_semantic_opaque"}
            ),
            obligations,
        )
        mapped = self._outcomes(
            "{{ states.sensor | map(attribute='entity_id') | list }}"
        )
        self.assertTrue(
            any(item.possible_entity_domains == ("sensor",) for item in mapped),
            mapped,
        )
        prefixed = self._outcomes("{{ states('sensor.' ~ room) }}")
        self.assertTrue(
            any(
                item.outcome == "exact_dependency"
                and item.possible_entity_domains == ("sensor",)
                for item in prefixed
            ),
            prefixed,
        )
        for expression in (
            "'sensor.' ~ room if use_sensor else helper_entity",
            "'sensor.' ~ room and helper_entity",
            "'sensor.' ~ room or helper_entity",
        ):
            with self.subTest(expression=expression):
                ambiguous = self._outcomes(
                    "{{ states(" + expression + ") }}"
                )
                self.assertTrue(
                    any(
                        item.outcome == "bounded_semantic_opaque"
                        and item.possible_entity_domains is None
                        for item in ambiguous
                    ),
                    ambiguous,
                )

    def test_ordinary_templates_remain_dependency_neutral_and_lock_free(self):
        cases = (
            "{{ 'states' }}",
            "{{ {'states': 'ready'} }}",
            "{% set states = 'ready' %}{{ states }}",
            "{{ 2 + 3 * 4 }}",
            "{{ now() }}",
            "{{ today_at('07:30') }}",
            "{{ 'a,b'.split(',') }}",
            "{% set data = {'message': 'ready'} %}{{ data.get('message') }}",
            "{% set data = {'message': 'ready'} %}{{ data.items() | list }}",
            "{% set data = {'message': 'ready'} %}{{ data.keys() | list }}",
            "{% set data = {'message': 'ready'} %}{{ data.values() | list }}",
            "Door {{ owner }}: {{ status | upper }}",
            "{% raw %}{{ states('input_boolean.not_executed') }}{% endraw %}",
            "{# states('input_boolean.not_executed') #}",
        )
        for template in cases:
            with self.subTest(template=template):
                obligations = self._outcomes(template)
                self.assertTrue(obligations)
                self.assertTrue(
                    all(
                        item.outcome == "proven_dependency_neutral"
                        and item.lock_projection == "none"
                        for item in obligations
                    ),
                    obligations,
                )

    def test_malformed_and_unsupported_semantics_never_become_absence(self):
        malformed = self._outcomes("{{ states('" + TARGET + "') + ( }}")
        self.assertTrue(
            any(item.reason_code == "template_parse_error" for item in malformed),
            malformed,
        )
        self.assertTrue(
            any(item.outcome == "bounded_semantic_opaque" for item in malformed),
            malformed,
        )
        self._assert_opaque("{{ 'value' | future_home_assistant_filter }}")

    def test_candidate_and_source_limits_are_truthful_coverage_failures(self):
        values = ", ".join(
            f"'sensor.fixture_{index}'"
            for index in range(MAX_TEMPLATE_CANDIDATES + 1)
        )
        candidate_result = self._outcomes("{{ states([" + values + "]) }}")
        self.assertTrue(
            any(
                item.outcome == "coverage_failure"
                and item.limit_exceeded
                and item.lock_projection == "coverage_failure"
                for item in candidate_result
            ),
            candidate_result,
        )
        source_result = self._outcomes("x" * (MAX_TEMPLATE_SOURCE_CHARS + 1))
        self.assertEqual(1, len(source_result))
        self.assertEqual("coverage_failure", source_result[0].outcome)
        self.assertTrue(source_result[0].limit_exceeded)

    def test_obligation_limit_is_bounded_and_reports_failure(self):
        template = " ".join("{{ now() }}" for _ in range(MAX_TEMPLATE_OBLIGATIONS + 20))
        result = self._outcomes(template)
        self.assertLessEqual(len(result), MAX_TEMPLATE_OBLIGATIONS)
        self.assertTrue(
            any(
                item.reason_code == "template_obligation_limit_exceeded"
                and item.outcome == "coverage_failure"
                for item in result
            ),
            result,
        )

    def test_whole_template_results_are_deterministic_and_bound_registry(self):
        template = (
            "{% set lookup = is_state %}"
            "{{ lookup('" + TARGET + "', 'on') }}"
        )
        first = self._analyze(template)
        second = self._analyze(template)
        self.assertEqual(first, second)
        self.assertEqual(64, len(first.semantic_registry_sha256))
        self.assertTrue(
            all(
                item.semantic_registry_fingerprint
                == first.semantic_registry_sha256
                for item in first.obligations
            )
        )
        self.assertTrue(
            all(item.outcome in OBLIGATION_OUTCOMES for item in first.obligations)
        )

    def test_configuration_drift_changes_obligation_identity(self):
        template = "{{ states('" + TARGET + "') }}"
        first = self._analyze(template, configuration_fingerprint="config-a")
        second = self._analyze(template, configuration_fingerprint="config-b")
        self.assertNotEqual(
            first.obligations[0].configuration_fingerprint,
            second.obligations[0].configuration_fingerprint,
        )


if __name__ == "__main__":
    unittest.main()
