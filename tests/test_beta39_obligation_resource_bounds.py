"""Adversarial resource bounds for the Beta 39 obligation ledger."""

from __future__ import annotations

from statistics import median
import time
import unittest

from ha_mcp_engineering.dependency.extraction import (
    MAX_CONFIGURATION_NODES,
    MAX_EVENT_SELECTOR_VALUES,
    extract_document_with_obligations,
)
from ha_mcp_engineering.dependency.models import (
    AutomationActionRiskProfile,
    DependencyIndexSnapshot,
    SourceCoverageItem,
)
from ha_mcp_engineering.dependency.obligation_ledger import (
    MAX_TEMPLATE_BINDINGS,
    MAX_TEMPLATE_CANDIDATES,
    MAX_TEMPLATE_OBLIGATIONS,
    MAX_TEMPLATE_SOURCE_CHARS,
    analyze_template_obligations,
)
from ha_mcp_engineering.f3_configuration.locks import (
    operation_lock_requests,
    unconstrained_helper_dependency_lock_key,
)
from ha_mcp_engineering.governance.helper_dependency import (
    build_helper_dependency_risk_binding,
    helper_dependency_risk_assessment,
)
from ha_mcp_engineering.governance.risk import (
    automation_action_consequence_profile,
)

from tests.f3_configuration_fixtures import (
    SyntheticConfigurationGateway,
    adapter_for,
    proposal_for,
    valid_config,
)


TARGET = "input_boolean.beta39_resource_fixture"


def _valid_entity_id(value: str) -> bool:
    domain, separator, object_id = value.partition(".")
    return bool(separator and domain and object_id and " " not in value)


def _analyze(template: str):
    return analyze_template_obligations(
        template,
        source_type="automation",
        source_id="porch_light",
        config_path="$.condition[0].value_template",
        relation="condition",
        source_entity_id="automation.porch_light",
        source_name="Beta 39 resource fixture",
        source_state="on",
        configuration_fingerprint="resource-fixture-v1",
        entity_id_validator=_valid_entity_id,
    )


def _mapping_template(size: int, *, dynamic_key: bool = False) -> str:
    fields = ",".join(
        f"'ordinary_{index:04d}':'value_{index:04d}'"
        for index in range(size)
    )
    lookup = "values[selector]" if dynamic_key else "values.get('lookup')"
    return (
        "{% set values={"
        + fields
        + ",'lookup':is_state} %}{{ "
        + lookup
        + "('"
        + TARGET
        + "','on') }}"
    )


def _binding_overflow_template() -> str:
    statements = ["{% set alias_0000 = is_state %}"]
    statements.extend(
        "{% set alias_"
        + f"{index:04d}"
        + " = alias_"
        + f"{index - 1:04d}"
        + " %}"
        for index in range(1, MAX_TEMPLATE_BINDINGS + 2)
    )
    statements.append(
        "{{ alias_"
        + f"{MAX_TEMPLATE_BINDINGS + 1:04d}"
        + "('"
        + TARGET
        + "','on') }}"
    )
    return "".join(statements)


def _profile(configuration: dict) -> AutomationActionRiskProfile:
    projected = automation_action_consequence_profile(configuration)
    return AutomationActionRiskProfile(
        source_id="porch_light",
        source_entity_id="automation.porch_light",
        risk_level=projected["risk_level"],
        physical_consequence=projected["physical_consequence"],
        complete=projected["complete"],
        truncated=projected["truncated"],
        action_domains=tuple(projected["action_domains"]),
        services=tuple(projected["services"]),
        reason_codes=tuple(projected["reason_codes"]),
        effect_projection_model=projected["effect_projection_model"],
        effect_targets=tuple(projected["effect_targets"]),
        effect_data=tuple(projected["effect_data"]),
        effect_structure_fingerprint=projected[
            "effect_structure_fingerprint"
        ],
        effect_projection_fingerprint=projected[
            "effect_projection_fingerprint"
        ],
        effect_projection_clipped=projected["effect_projection_clipped"],
        evidence_fingerprint=projected["evidence_fingerprint"],
    )


class ObligationLedgerResourceBoundTests(unittest.TestCase):
    def test_structural_event_scan_and_selector_lists_are_bounded(self):
        duplicate_exact = {
            "trigger": [
                {
                    "platform": "event",
                    "event_type": ["state_changed", "state_changed"],
                    "event_data": {"entity_id": TARGET},
                }
            ]
        }
        _findings, _dynamic, obligations = (
            extract_document_with_obligations(
                source_type="automation",
                source_id="duplicate_event_selector",
                source_entity_id="automation.duplicate_event_selector",
                source_name=None,
                source_state="on",
                config=duplicate_exact,
            )
        )
        self.assertTrue(
            any(
                item.obligation_kind == "state_changed_event_trigger"
                and item.outcome == "exact_dependency"
                and TARGET in item.exact_entity_ids
                for item in obligations
            ),
            obligations,
        )

        overflow = {
            "trigger": [
                {
                    "platform": "event",
                    "event_type": [
                        "state_changed"
                    ]
                    * (MAX_EVENT_SELECTOR_VALUES + 1),
                    "event_data": {"entity_id": TARGET},
                }
            ]
        }
        _findings, _dynamic, obligations = (
            extract_document_with_obligations(
                source_type="automation",
                source_id="overflow_event_selector",
                source_entity_id="automation.overflow_event_selector",
                source_name=None,
                source_state="on",
                config=overflow,
            )
        )
        self.assertTrue(
            any(
                item.outcome == "coverage_failure"
                and item.reason_code == "event_type_selector_limit_exceeded"
                and item.limit_exceeded
                for item in obligations
            ),
            obligations,
        )

        oversized = {
            "unrelated": [
                {"value": index}
                for index in range(MAX_CONFIGURATION_NODES * 5)
            ]
        }
        started = time.perf_counter()
        _findings, _dynamic, obligations = (
            extract_document_with_obligations(
                source_type="automation",
                source_id="oversized_structure",
                source_entity_id="automation.oversized_structure",
                source_name=None,
                source_state="on",
                config=oversized,
            )
        )
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.0)
        self.assertTrue(
            any(
                item.outcome == "coverage_failure"
                and item.reason_code
                == "configuration_structure_limit_exceeded"
                for item in obligations
            ),
            obligations,
        )

    def test_tiny_namespace_self_snapshot_is_bounded_and_exact(self):
        template = (
            "{% set ns=namespace(lookup=is_state) %}"
            "{% set ns.snapshot = ns %}"
            "{{ ns.snapshot.lookup('"
            + TARGET
            + "','on') }}"
        )

        started = time.perf_counter()
        first = _analyze(template)
        elapsed = time.perf_counter() - started
        second = _analyze(template)

        self.assertEqual(first, second)
        self.assertLess(elapsed, 1.0)
        self.assertLessEqual(len(first.obligations), MAX_TEMPLATE_OBLIGATIONS)
        self.assertFalse(first.coverage_failed)
        self.assertTrue(
            any(
                item.outcome == "exact_dependency"
                and TARGET in item.exact_entity_ids
                for item in first.obligations
            ),
            first.obligations,
        )

    def test_repeated_namespace_self_snapshots_fail_boundedly(self):
        assignments = "".join(
            f"{{% set ns.snapshot_{index:02d} = ns %}}"
            for index in range(12)
        )
        template = (
            "{% set ns=namespace(lookup=is_state) %}"
            + assignments
            + "{{ ns.snapshot_11.lookup('"
            + TARGET
            + "','on') }}"
        )

        started = time.perf_counter()
        first = _analyze(template)
        elapsed = time.perf_counter() - started
        second = _analyze(template)

        self.assertEqual(first, second)
        self.assertLess(elapsed, 1.0)
        self.assertLessEqual(len(first.obligations), MAX_TEMPLATE_OBLIGATIONS)
        self.assertTrue(first.coverage_failed)
        self.assertTrue(
            any(
                item.outcome == "coverage_failure"
                and item.limit_exceeded
                and item.reason_code == "template_abstract_value_limit_exceeded"
                for item in first.obligations
            ),
            first.obligations,
        )

    def test_large_finite_exact_mapping_remains_linear_and_exact(self):
        small_template = _mapping_template(32)
        large_template = _mapping_template(96)

        # Warm parser/import caches before measuring the representative ratio.
        _analyze(small_template)
        small_samples = []
        large_samples = []
        small_result = None
        large_result = None
        for _ in range(3):
            started = time.perf_counter()
            small_result = _analyze(small_template)
            small_samples.append(time.perf_counter() - started)
            started = time.perf_counter()
            large_result = _analyze(large_template)
            large_samples.append(time.perf_counter() - started)

        assert small_result is not None
        assert large_result is not None
        self.assertFalse(small_result.coverage_failed)
        self.assertFalse(large_result.coverage_failed)
        self.assertTrue(
            any(
                item.outcome == "exact_dependency"
                and TARGET in item.exact_entity_ids
                for item in large_result.obligations
            ),
            large_result.obligations,
        )
        self.assertLess(median(large_samples), 1.0)
        self.assertLessEqual(
            median(large_samples),
            max(0.25, median(small_samples) * 10),
        )
        self.assertLessEqual(
            large_result.work_units,
            small_result.work_units * 5,
        )

    def test_overflow_mapping_scaling_remains_bounded_and_near_linear(self):
        small_template = _mapping_template(128, dynamic_key=True)
        large_template = _mapping_template(512, dynamic_key=True)
        _analyze(small_template)
        started = time.perf_counter()
        small_result = _analyze(small_template)
        small_elapsed = time.perf_counter() - started
        started = time.perf_counter()
        large_result = _analyze(large_template)
        large_elapsed = time.perf_counter() - started

        self.assertTrue(small_result.coverage_failed)
        self.assertTrue(large_result.coverage_failed)
        self.assertLess(large_elapsed, 1.0)
        self.assertLessEqual(
            large_elapsed,
            max(0.25, small_elapsed * 10),
        )
        self.assertLessEqual(
            large_result.work_units,
            small_result.work_units * 5,
        )

    def test_dynamic_mapping_candidate_overflow_is_explicit_failure(self):
        result = _analyze(
            _mapping_template(
                MAX_TEMPLATE_CANDIDATES + 1,
                dynamic_key=True,
            )
        )

        failures = [
            item
            for item in result.obligations
            if item.outcome == "coverage_failure"
        ]
        self.assertTrue(result.coverage_failed)
        self.assertTrue(failures, result.obligations)
        self.assertTrue(all(item.limit_exceeded for item in failures))
        self.assertTrue(
            any(item.lock_projection == "coverage_failure" for item in failures)
        )
        self.assertLessEqual(len(result.obligations), MAX_TEMPLATE_OBLIGATIONS)

    def test_branch_union_limit_cannot_be_erased_by_last_projection(self):
        first = ",".join(f"'sensor.a_{index:03d}'" for index in range(80))
        second_values = [
            *(f"'sensor.b_{index:03d}'" for index in range(79)),
            f"'{TARGET}'",
        ]
        template = (
            "{% set first=["
            + first
            + "] %}{% set second=["
            + ",".join(second_values)
            + "] %}{{ states((first if enabled else second) | last) }}"
        )
        result = _analyze(template)

        self.assertTrue(result.coverage_failed)
        self.assertTrue(
            any(
                item.outcome == "coverage_failure"
                and item.limit_exceeded
                for item in result.obligations
            ),
            result.obligations,
        )

    def test_constructor_keyword_limit_survives_values_projection(self):
        values = ",".join(
            f"k_{index:03d}='sensor.value_{index:03d}'"
            for index in range(MAX_TEMPLATE_CANDIDATES)
        )
        template = (
            "{{ dict("
            + values
            + ",target='"
            + TARGET
            + "').values() | last | states }}"
        )
        result = _analyze(template)

        self.assertTrue(result.coverage_failed)
        self.assertTrue(
            any(
                item.reason_code == "template_value_container_limit_exceeded"
                for item in result.obligations
            ),
            result.obligations,
        )

    def test_binding_and_source_limits_are_explicit_and_bounded(self):
        binding_result = _analyze(_binding_overflow_template())
        source_result = _analyze("x" * (MAX_TEMPLATE_SOURCE_CHARS + 1))

        for result, reason in (
            (binding_result, "template_binding_limit_exceeded"),
            (source_result, "template_source_limit_exceeded"),
        ):
            with self.subTest(reason=reason):
                self.assertTrue(result.coverage_failed)
                self.assertLessEqual(
                    len(result.obligations), MAX_TEMPLATE_OBLIGATIONS
                )
                self.assertTrue(
                    any(
                        item.outcome == "coverage_failure"
                        and item.reason_code == reason
                        and item.limit_exceeded
                        for item in result.obligations
                    ),
                    result.obligations,
                )


class ObligationResourceGovernanceAndLockTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_coverage_failure_is_nonactionable_and_conservatively_locked(self):
        template = _binding_overflow_template()
        configuration = valid_config("automation")
        configuration["condition"] = [
            {"condition": "template", "value_template": template}
        ]
        configuration["action"] = [
            {
                "service": "cover.open_cover",
                "target": {"entity_id": "cover.synthetic_resource_fixture"},
            }
        ]
        findings, dynamic, obligations = extract_document_with_obligations(
            source_type="automation",
            source_id="porch_light",
            source_entity_id="automation.porch_light",
            source_name="Beta 39 resource fixture",
            source_state="on",
            config=configuration,
        )
        snapshot = DependencyIndexSnapshot(
            fingerprint="a" * 64,
            generation=39,
            built_at_monotonic=time.monotonic(),
            built_at="2026-08-15T12:00:00+00:00",
            findings=tuple(findings),
            dynamic_references=tuple(dynamic),
            target_metadata={},
            coverage=(
                SourceCoverageItem(
                    "automation",
                    "direct_ha_api",
                    "automation_config",
                    "complete",
                ),
                SourceCoverageItem(
                    "blueprint",
                    "direct_ha_api",
                    "blueprint_source",
                    "complete",
                ),
            ),
            automation_action_profiles=(_profile(configuration),),
            obligations=tuple(obligations),
        )
        binding = build_helper_dependency_risk_binding(
            snapshot,
            entity_id=TARGET,
            index_metadata={
                "freshness": "current",
                "evidence_stale": False,
                "invalidated": False,
            },
        )
        risk = helper_dependency_risk_assessment(
            {"binding": binding, "provenance": {"generation": 39}}
        )

        self.assertFalse(binding["coverage_complete"])
        self.assertEqual("coverage_failure", binding["semantic_precision"])
        self.assertFalse(binding["execution_eligible"])
        self.assertFalse(risk.apply_allowed)

        base = valid_config("automation")
        gateway = SyntheticConfigurationGateway()
        proposal = proposal_for(
            "automation",
            "update",
            current_config=base,
            proposed_config=configuration,
        )
        gateway.states[("automation", proposal.target_id)] = base
        prepared = await adapter_for(
            "automation", "update", gateway
        ).prepare(proposal)
        locks = {item.key for item in operation_lock_requests(prepared)}
        self.assertIn(unconstrained_helper_dependency_lock_key(), locks)


if __name__ == "__main__":
    unittest.main()
