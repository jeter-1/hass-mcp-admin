"""B39-136-R1/R6: differential tests against the real pinned Jinja 3.1.6.

The oracle for every ordering-sensitive construct is the pinned Jinja
environment itself, not an expectation table.  Each case renders the selector
expression with real Jinja to learn which element is genuinely selected, then
analyzes the same expression inside ``states[...]`` and requires the ledger to
either name exactly that element or classify conservatively.  Naming a
different element is the fail-open defect this file exists to prevent.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

import unittest

from jinja2.defaults import DEFAULT_FILTERS, DEFAULT_TESTS
from jinja2.sandbox import ImmutableSandboxedEnvironment

from ha_mcp_engineering.dependency.extraction import valid_entity_id
from ha_mcp_engineering.dependency.obligation_ledger import (
    analyze_template_obligations,
)
from ha_mcp_engineering.dependency.semantic_registry import semantic_category


# Insertion order deliberately disagrees with alphabetical order, so any
# canonical sorting inside the value model selects the wrong element.
MAPPING_LITERAL = (
    "{'sensor.zulu': 1, 'input_boolean.beta39_ledger': 2, 'light.alpha': 3}"
)
MAPPING_VALUE = {
    "sensor.zulu": 1,
    "input_boolean.beta39_ledger": 2,
    "light.alpha": 3,
}
ENVIRONMENT = ImmutableSandboxedEnvironment(
    extensions=("jinja2.ext.loopcontrols", "jinja2.ext.do")
)

# Constructs whose selected position Jinja determines exactly.  The ledger
# must reproduce the oracle rather than fall back to a candidate union,
# otherwise ordinary configuration is needlessly opaque.
EXACT_SELECTORS = (
    "(d.keys()|list)|first",
    "(d.keys()|list)|last",
    "(d.items()|list)|first|first",
    "(d.items()|list)|last|first",
    "(d.keys()|list)[0]",
    "(d.keys()|list)[1]",
    "(d.keys()|list)[-1]",
    "d|first",
    "d|last",
    "(d|list)|first",
    "(d|list)|last",
    "(d|items|list)|first|first",
    "(d|items|list)|last|first",
    "([(d.keys()|list)|last])|first",
)

# Constructs that are dependency-neutral scalar transformations of a selected
# key.  Jinja determines the position, but the transformation result is not a
# literal the ledger may claim, so these must classify conservatively.
NEUTRALISED_SELECTORS = (
    "(d.keys()|list)|last|lower",
    "(d.keys()|list)|last|trim",
)

# Constructs whose order Jinja may change or whose position is not determined.
# The ledger must not claim an exact singleton for these.
ORDER_UNCERTAIN_SELECTORS = (
    "(d.keys()|list|sort)|first",
    "(d.keys()|list|reverse|list)|first",
    "(d.keys()|list|unique|list)|last",
    "(d|dictsort|list)|first|first",
    "(d.keys()|list)|max",
    "(d.keys()|list)|min",
    "(d.keys()|list)|random",
    "(d.keys()|list)[1:]|first",
)


def _render(expression: str) -> str:
    return ENVIRONMENT.from_string(
        "{% set d = " + MAPPING_LITERAL + " %}{{ " + expression + " }}"
    ).render()


def _analyze(expression: str):
    source = (
        "{% set d = "
        + MAPPING_LITERAL
        + " %}{{ states["
        + expression
        + "] }}"
    )
    return analyze_template_obligations(
        source,
        source_type="automation",
        source_id="beta39_differential",
        config_path="$.condition[0].value_template",
        relation="template_reference",
        source_entity_id=None,
        source_name=None,
        source_state=None,
        configuration_fingerprint="beta39-differential",
        entity_id_validator=valid_entity_id,
    )


def _state_access_obligations(result):
    return [
        item
        for item in result.obligations
        if item.obligation_kind
        in {"states_item_access", "state_value", "template_analysis"}
        or item.reason_code.startswith("states_item")
    ]


class JinjaOrderingDifferentialTests(unittest.TestCase):
    def test_pinned_environment_is_the_reviewed_one(self):
        # The oracle is only meaningful if it is the same parser the ledger
        # claims to model.
        import jinja2

        self.assertEqual("3.1.6", jinja2.__version__)

    def test_exact_selectors_reproduce_the_real_jinja_selection(self):
        for expression in EXACT_SELECTORS:
            with self.subTest(expression=expression):
                oracle = _render(expression)
                self.assertTrue(
                    valid_entity_id(oracle),
                    f"oracle {oracle!r} is not an entity id",
                )
                result = _analyze(expression)
                self.assertFalse(result.coverage_failed)
                exact = {
                    entity_id
                    for item in result.obligations
                    if item.outcome == "exact_dependency"
                    for entity_id in item.exact_entity_ids
                }
                self.assertEqual(
                    {oracle},
                    exact,
                    f"{expression} selected {sorted(exact)} but real "
                    f"Jinja selects {oracle}",
                )

    def test_order_uncertain_selectors_never_claim_a_wrong_singleton(self):
        for expression in ORDER_UNCERTAIN_SELECTORS:
            with self.subTest(expression=expression):
                oracle = _render(expression)
                result = _analyze(expression)
                exact_items = [
                    item
                    for item in result.obligations
                    if item.outcome == "exact_dependency"
                    and item.exact_entity_ids
                ]
                for item in exact_items:
                    # An exact claim is allowed only when it still contains
                    # the element real Jinja selects.
                    self.assertIn(
                        oracle,
                        set(item.exact_entity_ids),
                        f"{expression} claimed {item.exact_entity_ids} "
                        f"without the real selection {oracle}",
                    )
                    self.assertGreater(
                        len(item.exact_entity_ids),
                        1,
                        f"{expression} claimed an exact singleton over an "
                        "order-uncertain sequence",
                    )

    def test_neutralised_selectors_do_not_claim_a_transformed_literal(self):
        for expression in NEUTRALISED_SELECTORS:
            with self.subTest(expression=expression):
                result = _analyze(expression)
                exact = {
                    entity_id
                    for item in result.obligations
                    if item.outcome == "exact_dependency"
                    for entity_id in item.exact_entity_ids
                }
                self.assertEqual(set(), exact)
                self.assertTrue(
                    any(
                        item.outcome == "bounded_semantic_opaque"
                        and item.lock_projection == "conservative"
                        for item in result.obligations
                    ),
                    f"{expression} lost the selector without saying so",
                )

    def test_incompatible_branch_orders_are_not_resolved_to_one_order(self):
        source = (
            "{% if states('sensor.mode') == 'a' %}"
            "{% set d = {'sensor.zulu': 1, 'light.alpha': 2} %}"
            "{% else %}"
            "{% set d = {'light.alpha': 2, 'sensor.zulu': 1} %}"
            "{% endif %}"
            "{{ states[(d.keys()|list)|last] }}"
        )
        result = analyze_template_obligations(
            source,
            source_type="automation",
            source_id="beta39_differential",
            config_path="$.condition[0].value_template",
            relation="template_reference",
            source_entity_id=None,
            source_name=None,
            source_state=None,
            configuration_fingerprint="beta39-differential",
            entity_id_validator=valid_entity_id,
        )
        selected = {
            entity_id
            for item in result.obligations
            if item.outcome == "exact_dependency"
            for entity_id in item.exact_entity_ids
        }
        # Both branch orders are possible, so the union is retained and no
        # artificial order is chosen.
        self.assertIn("sensor.zulu", selected)
        self.assertIn("light.alpha", selected)

    def test_iteration_covers_every_key_in_insertion_order(self):
        source = (
            "{% set d = "
            + MAPPING_LITERAL
            + " %}{% for key in d %}{{ states[key] }}{% endfor %}"
        )
        result = analyze_template_obligations(
            source,
            source_type="automation",
            source_id="beta39_differential",
            config_path="$.condition[0].value_template",
            relation="template_reference",
            source_entity_id=None,
            source_name=None,
            source_state=None,
            configuration_fingerprint="beta39-differential",
            entity_id_validator=valid_entity_id,
        )
        selected = {
            entity_id
            for item in result.obligations
            if item.outcome == "exact_dependency"
            for entity_id in item.exact_entity_ids
        }
        self.assertEqual(set(MAPPING_VALUE), selected)


class JinjaAttrFilterDifferentialTests(unittest.TestCase):
    """``attr`` is real attribute access and never falls back to items."""

    def test_attr_on_a_mapping_matches_real_jinja_undefined(self):
        rendered = ENVIRONMENT.from_string(
            "{{ (m|attr('is_state')) is defined }}"
        ).render(m={"is_state": "sensor.zulu"})
        self.assertEqual("False", rendered)

        result = analyze_template_obligations(
            "{% set m = {'is_state': 'sensor.zulu'} %}"
            "{{ states[m|attr('is_state')] }}",
            source_type="automation",
            source_id="beta39_differential",
            config_path="$.condition[0].value_template",
            relation="template_reference",
            source_entity_id=None,
            source_name=None,
            source_state=None,
            configuration_fingerprint="beta39-differential",
            entity_id_validator=valid_entity_id,
        )
        exact = {
            entity_id
            for item in result.obligations
            for entity_id in item.exact_entity_ids
        }
        # Real Jinja yields undefined here, so the mapping item must not be
        # read back as an entity selector.
        self.assertNotIn("sensor.zulu", exact)

    def test_dot_access_still_reads_the_mapping_item(self):
        rendered = ENVIRONMENT.from_string("{{ m.other }}").render(
            m={"other": "sensor.zulu"}
        )
        self.assertEqual("sensor.zulu", rendered)

        result = analyze_template_obligations(
            "{% set m = {'other': 'sensor.zulu'} %}{{ states[m.other] }}",
            source_type="automation",
            source_id="beta39_differential",
            config_path="$.condition[0].value_template",
            relation="template_reference",
            source_entity_id=None,
            source_name=None,
            source_state=None,
            configuration_fingerprint="beta39-differential",
            entity_id_validator=valid_entity_id,
        )
        exact = {
            entity_id
            for item in result.obligations
            if item.outcome == "exact_dependency"
            for entity_id in item.exact_entity_ids
        }
        self.assertEqual({"sensor.zulu"}, exact)


class StandardJinjaVocabularyTests(unittest.TestCase):
    """The vocabulary is derived from the pinned package, aliases included."""

    def test_every_standard_filter_and_test_name_is_classified(self):
        for surface, table in (
            ("filters", DEFAULT_FILTERS),
            ("tests", DEFAULT_TESTS),
        ):
            for name in table:
                with self.subTest(surface=surface, name=name):
                    self.assertNotEqual(
                        "unknown",
                        semantic_category(surface, name),
                        f"{surface}.{name} is unclassified",
                    )

    def test_aliases_inherit_their_implementation_category(self):
        for surface, table in (
            ("filters", DEFAULT_FILTERS),
            ("tests", DEFAULT_TESTS),
        ):
            groups: dict[str, set[str]] = {}
            for name, function in table.items():
                key = getattr(function, "__name__", repr(function))
                groups.setdefault(key, set()).add(name)
            for key, names in groups.items():
                if len(names) < 2:
                    continue
                categories = {
                    semantic_category(surface, name) for name in names
                }
                with self.subTest(surface=surface, implementation=key):
                    self.assertEqual(
                        1,
                        len(categories),
                        f"{sorted(names)} disagree: {sorted(categories)}",
                    )

    def test_documented_alias_pairs_are_present(self):
        self.assertEqual(
            semantic_category("filters", "default"),
            semantic_category("filters", "d"),
        )
        self.assertEqual(
            semantic_category("filters", "escape"),
            semantic_category("filters", "e"),
        )
        self.assertEqual(
            semantic_category("filters", "length"),
            semantic_category("filters", "count"),
        )
        self.assertNotEqual("unknown", semantic_category("filters", "items"))


class LowFrictionRegressionTests(unittest.TestCase):
    """Ordinary configuration must stay exact, lock-free, and executable.

    This is a required pass with the same weight as the safety cases: a
    ledger that is safe because every helper flip elevates delivers no
    operational benefit.
    """

    ORDINARY_TEMPLATES = (
        "{{ trigger.payload_json.get('message') }}",
        "{{ trigger.payload_json.get('message', 'none') }}",
        "{{ states('sensor.zulu')|d(0) }}",
        "{{ states('sensor.zulu')|default(0) }}",
        "{{ states('sensor.zulu')|float(0)|round(1) }}",
        "{{ [1, 2, 3]|sum }}",
        "{{ [1, 2, 3]|max }}",
        "{{ (states('sensor.zulu')|float(0) * 2)|abs }}",
        "{{ now().hour }}",
        "{{ now().strftime('%H:%M') }}",
        "{% set m = {'a': 1} %}{% for k, v in m|items %}{{ k }}{{ v }}"
        "{% endfor %}",
        "{{ {'a': 1}|tojson }}",
        "{{ 'text'|truncate(2) }}",
        "{{ 'text'|e }}",
        "{{ ['a', 'b']|count }}",
        "{{ 1 if states('sensor.zulu') == 'on' else 0 }}",
    )

    def _analyze(self, source: str):
        return analyze_template_obligations(
            source,
            source_type="automation",
            source_id="beta39_low_friction",
            config_path="$.condition[0].value_template",
            relation="template_reference",
            source_entity_id=None,
            source_name=None,
            source_state=None,
            configuration_fingerprint="beta39-low-friction",
            entity_id_validator=valid_entity_id,
        )

    def test_ordinary_templates_stay_complete_and_lock_free(self):
        for source in self.ORDINARY_TEMPLATES:
            with self.subTest(source=source):
                result = self._analyze(source)
                self.assertFalse(result.coverage_failed, source)
                for item in result.obligations:
                    self.assertNotEqual(
                        "bounded_semantic_opaque",
                        item.outcome,
                        f"{source} became opaque: {item.reason_code}",
                    )
                    self.assertNotEqual(
                        "coverage_failure", item.outcome, source
                    )
                    self.assertIn(
                        item.lock_projection,
                        {"none", "exact"},
                        f"{source} requires a conservative lock",
                    )
                    self.assertFalse(item.limit_exceeded, source)

    def test_ordinary_templates_render_under_real_jinja(self):
        # Every low-friction template must be a template Home Assistant could
        # actually evaluate, so the regression cannot drift into fiction.
        for source in self.ORDINARY_TEMPLATES:
            with self.subTest(source=source):
                ENVIRONMENT.parse(source)


if __name__ == "__main__":
    unittest.main()
