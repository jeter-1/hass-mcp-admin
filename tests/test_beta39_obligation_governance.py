"""Target-specific governance and lock contracts for the obligation ledger."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.models import (  # noqa: E402
    AutomationActionRiskProfile,
    DependencyIndexSnapshot,
    DependencyObligation,
    OBLIGATION_LEDGER_MODEL,
    SourceCoverageItem,
)
from ha_mcp_engineering.dependency.extraction import (  # noqa: E402
    extract_document_with_obligations,
)
from ha_mcp_engineering.f3.operational_locks import (  # noqa: E402
    OperationalLockSetCalculator,
)
from ha_mcp_engineering.f3_configuration.locks import (  # noqa: E402
    helper_dependency_lock_key,
    unconstrained_helper_dependency_lock_key,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    HELPER_DEPENDENCY_RISK_MODEL,
    build_helper_dependency_risk_binding,
    helper_dependency_risk_assessment,
)
from ha_mcp_engineering.governance.normalize import stable_hash  # noqa: E402
from ha_mcp_engineering.governance.risk import (  # noqa: E402
    automation_action_consequence_profile,
)
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    evaluate_change_policy,
)
from tests.f3_operational_fixtures import (  # noqa: E402
    make_plan,
)


TARGET = "input_boolean.beta39_ledger"
REGISTRY = "registry-sha256"
LIVE_ORDINARY_DYNAMIC_TEMPLATES = (
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
LIVE_FINITE_SENSOR_TEMPLATES = (
    "{% set temperature_entity = 'sensor.a' if enabled else 'sensor.b' %}"
    "{{ states(temperature_entity) }}",
    "{% for c in [{'id':'sensor.c'},{'id':'sensor.d'}] %}"
    "{{ states(c.id) }}{% endfor %}",
)


def obligation(
    outcome: str,
    *,
    source_type: str = "automation",
    source_id: str = "porch_light",
    exact: tuple[str, ...] = (),
    domains: tuple[str, ...] | None = None,
    reason: str = "synthetic_obligation",
    external: str | None = None,
    category: str = "state_entity_access",
    kind: str = "state_entity_access",
    relation: str = "template_entity_reference",
) -> DependencyObligation:
    return DependencyObligation(
        evidence_id="ev_" + stable_hash((source_id, outcome, exact, reason))[:24],
        source_type=source_type,
        source_id=source_id,
        source_entity_id=f"automation.{source_id}",
        source_name=None,
        source_state="on",
        config_path="$.condition[0].value_template",
        relation=relation,
        outcome=outcome,
        obligation_kind=kind,
        reason_code=reason,
        semantic_category=category,
        semantic_registry_version="ha-template-semantics-v1",
        semantic_registry_fingerprint=REGISTRY,
        expression_fingerprint=stable_hash((outcome, exact, reason)),
        configuration_fingerprint=stable_hash((source_id, "configuration")),
        exact_entity_ids=exact,
        possible_entity_domains=domains,
        external_template_name=external,
        lock_projection=(
            "coverage_failure"
            if outcome == "coverage_failure"
            else "conservative"
            if outcome == "bounded_semantic_opaque"
            else "exact"
            if outcome == "exact_dependency"
            else "none"
        ),
    )


def profile(source_id: str, service: str) -> AutomationActionRiskProfile:
    action = {"service": service}
    if service.startswith("notify."):
        action["data"] = {"message": "Synthetic ledger fixture"}
    if service == "cover.open_cover":
        action["target"] = {"entity_id": "cover.synthetic_garage"}
    projected = automation_action_consequence_profile(
        {"action": [action]}
    )
    return AutomationActionRiskProfile(
        source_id=source_id,
        source_entity_id=f"automation.{source_id}",
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
        effect_projection_clipped=projected[
            "effect_projection_clipped"
        ],
        evidence_fingerprint=projected["evidence_fingerprint"],
    )


def snapshot(
    obligations: tuple[DependencyObligation, ...],
    *,
    profiles: tuple[AutomationActionRiskProfile, ...] = (),
    automation_completeness: str = "complete",
    overflow: int = 0,
) -> DependencyIndexSnapshot:
    return DependencyIndexSnapshot(
        fingerprint="a" * 64,
        generation=39,
        built_at_monotonic=time.monotonic(),
        built_at="2026-08-15T12:00:00+00:00",
        findings=(),
        dynamic_references=(),
        target_metadata={},
        coverage=(
            SourceCoverageItem(
                "automation",
                "direct_ha_api",
                "automation_config",
                automation_completeness,
            ),
            SourceCoverageItem(
                "blueprint",
                "direct_ha_api",
                "blueprint_source",
                "complete",
            ),
        ),
        automation_action_profiles=profiles,
        obligations=obligations,
        obligation_ledger_model=OBLIGATION_LEDGER_MODEL,
        obligation_overflow_count=overflow,
        obligation_overflow_fingerprint=(
            stable_hash({"overflow": overflow}) if overflow else None
        ),
    )


def bind(value: DependencyIndexSnapshot) -> dict:
    return build_helper_dependency_risk_binding(
        value,
        entity_id=TARGET,
        index_metadata={
            "freshness": "current",
            "evidence_stale": False,
            "invalidated": False,
        },
    )


class ObligationGovernanceTests(unittest.TestCase):
    def test_deployed_beta38_smoke_shape_is_complete_low_and_actionable(self):
        obligations: list[DependencyObligation] = []
        for index, template in enumerate(LIVE_ORDINARY_DYNAMIC_TEMPLATES):
            _findings, _dynamic, extracted = (
                extract_document_with_obligations(
                    source_type="automation",
                    source_id=f"ordinary_{index}",
                    source_entity_id=f"automation.ordinary_{index}",
                    source_name="Live-shape ordinary fixture",
                    source_state="on",
                    config={
                        "action": [
                            {
                                "service": "notify.notify",
                                "data": {"message": template},
                            }
                        ]
                    },
                )
            )
            obligations.extend(extracted)
        for index, template in enumerate(LIVE_FINITE_SENSOR_TEMPLATES):
            _findings, _dynamic, extracted = (
                extract_document_with_obligations(
                    source_type="automation",
                    source_id=f"finite_sensor_{index}",
                    source_entity_id=f"automation.finite_sensor_{index}",
                    source_name="Live-shape finite sensor fixture",
                    source_state="on",
                    config={
                        "condition": [
                            {
                                "condition": "template",
                                "value_template": template,
                            }
                        ],
                        "action": [
                            {
                                "service": "notify.notify",
                                "data": {"message": "Sensor summary"},
                            }
                        ],
                    },
                )
            )
            obligations.extend(extracted)

        observed = bind(snapshot(tuple(obligations)))
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 39}}
        )
        plan = make_plan("set_input_boolean_state", target_id=TARGET)
        plan.operational.baseline["dependency_risk"] = observed
        plan.risk = risk
        policy = evaluate_change_policy(plan)

        self.assertTrue(observed["coverage_complete"])
        self.assertTrue(observed["evidence_complete"])
        self.assertTrue(observed["execution_eligible"])
        self.assertEqual("none", observed["physical_consequence"])
        self.assertEqual([], observed["relevant_downstream_object_ids"])
        self.assertEqual(0, observed["opaque_obligation_count"])
        self.assertGreaterEqual(
            observed["proven_target_exclusion_obligation_count"], 2
        )
        self.assertEqual("low", risk.level.value)
        self.assertTrue(risk.apply_allowed)
        self.assertEqual("standard_admin", policy.policy_class.value)

    def test_exact_exclusion_and_neutral_evidence_remain_low_friction(self):
        observed = bind(
            snapshot(
                (
                    obligation(
                        "exact_dependency",
                        exact=("sensor.temperature",),
                        domains=("sensor",),
                    ),
                    obligation("proven_dependency_neutral"),
                )
            )
        )
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 39}}
        )

        self.assertTrue(observed["coverage_complete"])
        self.assertTrue(observed["evidence_complete"])
        self.assertEqual(observed["semantic_precision"], "exact")
        self.assertEqual(observed["physical_consequence"], "none")
        self.assertEqual(observed["relevant_downstream_object_ids"], [])
        self.assertTrue(risk.apply_allowed)
        self.assertEqual(risk.level.value, "low")

    def test_bounded_opaque_benign_effect_is_transparent_and_actionable(self):
        observed = bind(
            snapshot(
                (obligation("bounded_semantic_opaque"),),
                profiles=(profile("porch_light", "notify.notify"),),
            )
        )
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 39}}
        )

        self.assertTrue(observed["coverage_complete"])
        self.assertFalse(observed["evidence_complete"])
        self.assertTrue(observed["execution_eligible"])
        self.assertEqual(observed["semantic_precision"], "bounded_opaque")
        self.assertEqual(observed["opaque_obligation_count"], 1)
        self.assertEqual(observed["physical_consequence"], "none")
        self.assertTrue(risk.apply_allowed)
        self.assertEqual(risk.level.value, "low")
        self.assertTrue(risk.warnings)
        plan = make_plan("set_input_boolean_state", target_id=TARGET)
        plan.operational.baseline["dependency_risk"] = observed
        plan.risk = risk
        policy = evaluate_change_policy(plan)
        self.assertEqual(policy.policy_class.value, "standard_admin")
        self.assertIn(
            "helper_dependency_bounded_semantic_opacity",
            policy.reason_codes,
        )

    def test_bounded_opaque_consequential_effect_is_elevated_and_actionable(self):
        observed = bind(
            snapshot(
                (obligation("bounded_semantic_opaque"),),
                profiles=(profile("porch_light", "cover.open_cover"),),
            )
        )
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 39}}
        )

        self.assertTrue(observed["execution_eligible"])
        self.assertEqual(observed["physical_consequence"], "safety_critical")
        self.assertEqual(risk.level.value, "high")
        self.assertTrue(risk.apply_allowed)
        plan = make_plan("set_input_boolean_state", target_id=TARGET)
        plan.operational.baseline["dependency_risk"] = observed
        plan.risk = risk
        policy = evaluate_change_policy(plan)
        self.assertEqual(policy.policy_class.value, "elevated_admin")
        self.assertEqual(
            policy.physical_consequence.value, "safety_critical"
        )

    def test_dynamic_dispatch_domain_hint_cannot_discharge_opacity(self):
        observed = bind(
            snapshot(
                (
                    obligation(
                        "bounded_semantic_opaque",
                        domains=("sensor",),
                        category="dynamic_filter_test_dispatch",
                        reason="dynamic_test_name",
                    ),
                ),
                profiles=(profile("porch_light", "cover.open_cover"),),
            )
        )
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 39}}
        )

        self.assertTrue(observed["coverage_complete"])
        self.assertEqual("bounded_opaque", observed["semantic_precision"])
        self.assertEqual(1, observed["opaque_obligation_count"])
        self.assertEqual(
            "safety_critical", observed["physical_consequence"]
        )
        self.assertEqual("high", risk.level.value)
        self.assertTrue(risk.apply_allowed)

    def test_transformed_state_result_remains_consequentially_opaque(self):
        config = {
            "condition": [
                {
                    "condition": "template",
                    "value_template": (
                        "{{ states(state_attr('sensor.selector','target') "
                        "or 'sensor.a') }}"
                    ),
                }
            ],
            "action": [
                {
                    "service": "cover.open_cover",
                    "target": {"entity_id": "cover.synthetic_garage"},
                }
            ],
        }
        _findings, _dynamic, obligations = (
            extract_document_with_obligations(
                source_type="automation",
                source_id="transformed_selector",
                source_entity_id="automation.transformed_selector",
                source_name=None,
                source_state="on",
                config=config,
            )
        )
        observed = bind(
            snapshot(
                tuple(obligations),
                profiles=(
                    profile("transformed_selector", "cover.open_cover"),
                ),
            )
        )
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 39}}
        )

        self.assertEqual("bounded_opaque", observed["semantic_precision"])
        self.assertEqual(
            ["automation.transformed_selector"],
            observed["relevant_downstream_object_ids"],
        )
        self.assertEqual("safety_critical", observed["physical_consequence"])
        self.assertEqual("high", risk.level.value)
        self.assertTrue(risk.apply_allowed)

    def test_wait_scalar_metadata_does_not_create_helper_consequence(self):
        config = {
            "action": [
                {
                    "wait_template": "{{ is_state('sensor.ready', 'on') }}",
                    "timeout": 30,
                },
                {
                    "condition": "template",
                    "value_template": (
                        "{{ wait.completed and wait.remaining is not none }}"
                    ),
                },
                {
                    "service": "cover.open_cover",
                    "target": {"entity_id": "cover.synthetic_garage"},
                },
            ]
        }
        _findings, _dynamic, obligations = (
            extract_document_with_obligations(
                source_type="automation",
                source_id="wait_scalar_cover",
                source_entity_id="automation.wait_scalar_cover",
                source_name=None,
                source_state="on",
                config=config,
            )
        )
        observed = bind(
            snapshot(
                tuple(obligations),
                profiles=(profile("wait_scalar_cover", "cover.open_cover"),),
            )
        )
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 39}}
        )

        self.assertTrue(observed["evidence_complete"])
        self.assertEqual([], observed["relevant_downstream_object_ids"])
        self.assertEqual("none", observed["physical_consequence"])
        self.assertEqual("low", risk.level.value)

        selector_config = {
            **config,
            "condition": [
                {
                    "condition": "template",
                    "value_template": "{{ states(wait.completed) }}",
                }
            ],
        }
        _findings, _dynamic, selector_obligations = (
            extract_document_with_obligations(
                source_type="automation",
                source_id="wait_scalar_selector",
                source_entity_id="automation.wait_scalar_selector",
                source_name=None,
                source_state="on",
                config=selector_config,
            )
        )
        selector = bind(
            snapshot(
                tuple(selector_obligations),
                profiles=(
                    profile("wait_scalar_selector", "cover.open_cover"),
                ),
            )
        )
        self.assertEqual("bounded_opaque", selector["semantic_precision"])
        self.assertEqual(
            ["automation.wait_scalar_selector"],
            selector["relevant_downstream_object_ids"],
        )

    def test_namespace_and_mapping_key_transport_retain_consequential_dependency(self):
        templates = (
            "{{ states(namespace(get='" + TARGET + "').get or 'sensor.a') }}",
            (
                "{{ states(({'"
                + TARGET
                + "':'ready'} | list | first) or 'sensor.a') }}"
            ),
            (
                "{% set selected=dict((('x','"
                + TARGET
                + "'),)) %}"
                "{{ states(selected.get('x') or 'sensor.a') }}"
            ),
            (
                "{% set selected=namespace({'x':'"
                + TARGET
                + "'}) %}"
                "{{ states(selected.x or 'sensor.a') }}"
            ),
            (
                "{% set selected=namespace(x='sensor.a') %}"
                "{% if enabled %}{% set selected.x='"
                + TARGET
                + "' %}{% else %}{% set selected.x='sensor.b' %}{% endif %}"
                "{{ states(selected.x or 'sensor.c') }}"
            ),
            (
                "{% set selected=namespace(x='sensor.a') %}"
                "{% macro mutate(value) %}{% set value.x='"
                + TARGET
                + "' %}{% endmacro %}{{ mutate(selected) }}"
                "{{ states(selected.x or 'sensor.c') }}"
            ),
            (
                "{{ states(((['"
                + TARGET
                + "'] + ['sensor.a']) | first) or 'sensor.b') }}"
            ),
            (
                "{{ states(({'x':'sensor.a'} "
                "| map(attribute='x', default='"
                + TARGET
                + "') | first) or 'sensor.b') }}"
            ),
            (
                "{{ states(['sensor.a','"
                + TARGET
                + "'] | select('equalto','"
                + TARGET
                + "') | first) }}"
            ),
            (
                "{{ states(('%s.%s' | format('input_boolean',"
                "'mcp_f2_standard_admin_test_flag')) or 'sensor.a') }}"
            ),
        )
        for index, template in enumerate(templates):
            with self.subTest(template=template):
                config = {
                    "condition": [
                        {
                            "condition": "template",
                            "value_template": template,
                        }
                    ],
                    "action": [
                        {
                            "service": "cover.open_cover",
                            "target": {
                                "entity_id": "cover.synthetic_garage"
                            },
                        }
                    ],
                }
                source_id = f"container_provenance_{index}"
                _findings, _dynamic, obligations = (
                    extract_document_with_obligations(
                        source_type="automation",
                        source_id=source_id,
                        source_entity_id=f"automation.{source_id}",
                        source_name=None,
                        source_state="on",
                        config=config,
                    )
                )
                observed = bind(
                    snapshot(
                        tuple(obligations),
                        profiles=(
                            profile(source_id, "cover.open_cover"),
                        ),
                    )
                )
                risk = helper_dependency_risk_assessment(
                    {
                        "binding": observed,
                        "provenance": {"generation": 39},
                    }
                )

                self.assertEqual(
                    [f"automation.{source_id}"],
                    observed["relevant_downstream_object_ids"],
                )
                self.assertEqual(
                    "safety_critical",
                    observed["physical_consequence"],
                )
                self.assertEqual("high", risk.level.value)
                self.assertTrue(risk.apply_allowed)

    def test_dynamic_trigger_metadata_selector_is_consequentially_opaque(self):
        config = {
            "trigger": [
                {
                    "platform": "state",
                    "entity_id": "sensor.synthetic_source",
                    "id": TARGET,
                }
            ],
            "condition": [
                {
                    "condition": "template",
                    "value_template": (
                        "{{ states(trigger.id or 'sensor.a') }}"
                    ),
                }
            ],
            "action": [
                {
                    "service": "cover.open_cover",
                    "target": {"entity_id": "cover.synthetic_garage"},
                }
            ],
        }
        _findings, _dynamic, obligations = extract_document_with_obligations(
            source_type="automation",
            source_id="trigger_metadata_selector",
            source_entity_id="automation.trigger_metadata_selector",
            source_name=None,
            source_state="on",
            config=config,
        )
        observed = bind(
            snapshot(
                tuple(obligations),
                profiles=(
                    profile("trigger_metadata_selector", "cover.open_cover"),
                ),
            )
        )
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 39}}
        )

        self.assertEqual("bounded_opaque", observed["semantic_precision"])
        self.assertEqual(
            ["automation.trigger_metadata_selector"],
            observed["relevant_downstream_object_ids"],
        )
        self.assertEqual("safety_critical", observed["physical_consequence"])
        self.assertEqual("high", risk.level.value)
        self.assertTrue(risk.apply_allowed)

    def test_broad_state_changed_trigger_retains_consequential_effect(self):
        config = {
            "trigger": [
                {
                    "platform": "event",
                    "event_type": "state_changed",
                }
            ],
            "action": [
                {
                    "service": "cover.open_cover",
                    "target": {"entity_id": "cover.synthetic_garage"},
                }
            ],
        }
        _findings, _dynamic, obligations = (
            extract_document_with_obligations(
                source_type="automation",
                source_id="state_changed_cover",
                source_entity_id="automation.state_changed_cover",
                source_name="State changed cover fixture",
                source_state="on",
                config=config,
            )
        )
        observed = bind(
            snapshot(
                tuple(obligations),
                profiles=(
                    profile("state_changed_cover", "cover.open_cover"),
                ),
            )
        )
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 39}}
        )

        self.assertTrue(observed["coverage_complete"])
        self.assertEqual("bounded_opaque", observed["semantic_precision"])
        self.assertEqual(
            ["automation.state_changed_cover"],
            observed["relevant_downstream_object_ids"],
        )
        self.assertEqual(
            "safety_critical", observed["physical_consequence"]
        )
        self.assertEqual("high", risk.level.value)
        self.assertTrue(risk.apply_allowed)

    def test_broad_call_service_trigger_retains_consequential_effect(self):
        config = {
            "trigger": [
                {
                    "platform": "event",
                    "event_type": "call_service",
                    "event_data": {
                        "domain": "input_boolean",
                        "service": "turn_on",
                    },
                }
            ],
            "action": [
                {
                    "service": "cover.open_cover",
                    "target": {"entity_id": "cover.synthetic_garage"},
                }
            ],
        }
        _findings, _dynamic, obligations = (
            extract_document_with_obligations(
                source_type="automation",
                source_id="call_service_cover",
                source_entity_id="automation.call_service_cover",
                source_name="Call-service cover fixture",
                source_state="on",
                config=config,
            )
        )
        observed = bind(
            snapshot(
                tuple(obligations),
                profiles=(
                    profile("call_service_cover", "cover.open_cover"),
                ),
            )
        )
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 39}}
        )

        self.assertEqual("bounded_opaque", observed["semantic_precision"])
        self.assertEqual(
            "safety_critical", observed["physical_consequence"]
        )
        self.assertEqual("high", risk.level.value)
        self.assertTrue(risk.apply_allowed)

    def test_literal_action_target_is_noncausal_but_template_action_data_is_causal(self):
        literal = obligation(
            "exact_dependency",
            exact=(TARGET,),
            kind="structured_entity_reference",
            relation="action_target",
            reason="structured_exact_entity_reference",
        )
        low = bind(snapshot((literal,)))
        self.assertEqual([], low["relevant_downstream_object_ids"])
        self.assertEqual("none", low["physical_consequence"])

        template = replace(
            literal,
            obligation_kind="global_is_state",
            relation="action_data",
            semantic_category="state_entity_access",
            reason_code="is_state_entity_access",
        )
        elevated = bind(
            snapshot(
                (template,),
                profiles=(profile("porch_light", "cover.open_cover"),),
            )
        )
        self.assertEqual(
            ["automation.porch_light"],
            elevated["relevant_downstream_object_ids"],
        )
        self.assertEqual(
            "safety_critical", elevated["physical_consequence"]
        )

    def test_large_proven_non_relevant_inventory_is_compacted_not_failed(self):
        obligations = tuple(
            obligation(
                "proven_dependency_neutral",
                source_id=f"neutral_{index:03d}",
                reason=f"neutral_{index:03d}",
            )
            for index in range(300)
        )
        observed = bind(snapshot(obligations))
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 39}}
        )

        self.assertTrue(observed["coverage_complete"])
        self.assertTrue(observed["execution_eligible"])
        self.assertEqual("exact", observed["semantic_precision"])
        self.assertEqual(300, observed["non_relevant_obligation_count"])
        self.assertTrue(observed["non_relevant_obligations_compacted"])
        self.assertFalse(observed["truncated"])
        self.assertEqual([], observed["obligation_evidence"])
        self.assertTrue(risk.apply_allowed)
        self.assertEqual("low", risk.level.value)

        changed = bind(
            snapshot(
                obligations[:-1]
                + (
                    replace(
                        obligations[-1],
                        reason_code="neutral_semantics_changed",
                    ),
                )
            )
        )
        self.assertNotEqual(
            observed["non_relevant_obligation_fingerprint"],
            changed["non_relevant_obligation_fingerprint"],
        )
        self.assertNotEqual(
            observed["evidence_fingerprint"],
            changed["evidence_fingerprint"],
        )

    def test_relevant_obligation_overflow_remains_nonactionable(self):
        obligations = tuple(
            obligation(
                "exact_dependency",
                source_id="porch_light",
                exact=(TARGET,),
                reason=f"exact_{index:03d}",
            )
            for index in range(257)
        )
        observed = bind(
            snapshot(
                obligations,
                profiles=(profile("porch_light", "notify.notify"),),
            )
        )
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 39}}
        )

        self.assertFalse(observed["coverage_complete"])
        self.assertFalse(observed["execution_eligible"])
        self.assertIn(
            "obligation_projection_limit_exceeded",
            observed["coverage_failure_reason_codes"],
        )
        self.assertTrue(observed["truncated"])
        self.assertFalse(risk.apply_allowed)

    def test_target_domain_is_bounded_opacity_not_inventory_failure(self):
        observed = bind(
            snapshot(
                (
                    obligation(
                        "exact_dependency",
                        domains=("input_boolean",),
                        reason="states_input_boolean_domain_collection",
                    ),
                ),
                profiles=(profile("porch_light", "notify.notify"),),
            )
        )
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 39}}
        )
        fake = SimpleNamespace(
            validate=lambda: None,
            operation="set_input_boolean_state",
            target=SimpleNamespace(target_id=TARGET),
            authoritative_provider_slug="direct_home_assistant_state",
            baseline={"dependency_risk": observed},
        )
        keys = {
            item.key for item in OperationalLockSetCalculator().calculate(fake)
        }

        self.assertTrue(observed["coverage_complete"])
        self.assertEqual("bounded_opaque", observed["semantic_precision"])
        self.assertTrue(observed["execution_eligible"])
        self.assertTrue(risk.apply_allowed)
        self.assertIn(unconstrained_helper_dependency_lock_key(), keys)
        self.assertIn("automation:porch_light", keys)

    def test_resolved_blueprint_obligation_uses_automation_effect_and_lock(self):
        observed = bind(
            snapshot(
                (
                    obligation(
                        "exact_dependency",
                        source_type="blueprint",
                        exact=(TARGET,),
                    ),
                ),
                profiles=(profile("porch_light", "cover.open_cover"),),
            )
        )

        self.assertTrue(observed["execution_eligible"])
        self.assertEqual(observed["physical_consequence"], "safety_critical")
        self.assertEqual(
            observed["downstream_automation_resource_ids"], ["porch_light"]
        )

    def test_coverage_failure_and_overflow_are_not_actionable(self):
        for value in (
            snapshot((obligation("coverage_failure"),)),
            snapshot((obligation("proven_dependency_neutral"),), overflow=1),
            snapshot(
                (obligation("proven_dependency_neutral"),),
                automation_completeness="partial",
            ),
        ):
            with self.subTest(value=value):
                observed = bind(value)
                risk = helper_dependency_risk_assessment(
                    {
                        "binding": observed,
                        "provenance": {"generation": 39},
                    }
                )
                self.assertFalse(observed["coverage_complete"])
                self.assertFalse(observed["execution_eligible"])
                self.assertEqual(
                    observed["semantic_precision"], "coverage_failure"
                )
                self.assertFalse(risk.apply_allowed)
                self.assertEqual(risk.level.value, "high")

    def test_registry_obligation_effect_and_lock_projection_bind_fingerprint(self):
        base = obligation("bounded_semantic_opaque")
        before = bind(
            snapshot(
                (base,),
                profiles=(profile("porch_light", "notify.notify"),),
            )
        )
        after = bind(
            snapshot(
                (
                    replace(
                        base,
                        semantic_registry_fingerprint="changed-registry",
                    ),
                ),
                profiles=(profile("porch_light", "notify.notify"),),
            )
        )

        self.assertNotEqual(
            before["evidence_fingerprint"], after["evidence_fingerprint"]
        )

    def test_display_metadata_does_not_create_helper_approval_drift(self):
        base = obligation(
            "exact_dependency",
            exact=("sensor.unrelated",),
            domains=("sensor",),
        )
        before = bind(snapshot((replace(base, source_name="First name"),)))
        after = bind(snapshot((replace(base, source_name="Second name"),)))

        self.assertEqual(
            before["evidence_fingerprint"], after["evidence_fingerprint"]
        )

        first_config = bind(
            snapshot(
                (
                    replace(
                        base,
                        configuration_fingerprint="first-display-message",
                    ),
                )
            )
        )
        second_config = bind(
            snapshot(
                (
                    replace(
                        base,
                        configuration_fingerprint="second-display-message",
                    ),
                )
            )
        )
        self.assertEqual(
            first_config["non_relevant_obligation_fingerprint"],
            second_config["non_relevant_obligation_fingerprint"],
        )
        self.assertEqual(
            first_config["evidence_fingerprint"],
            second_config["evidence_fingerprint"],
        )

    def test_blueprint_coverage_must_be_explicitly_complete(self):
        base_snapshot = snapshot((obligation("proven_dependency_neutral"),))
        automation_only = replace(
            base_snapshot,
            coverage=tuple(
                item
                for item in base_snapshot.coverage
                if item.source_type == "automation"
            ),
        )
        not_requested = replace(
            base_snapshot,
            coverage=tuple(
                replace(item, completeness="not_requested")
                if item.source_type == "blueprint"
                else item
                for item in base_snapshot.coverage
            ),
        )
        for value in (automation_only, not_requested):
            with self.subTest(coverage=value.coverage):
                observed = bind(value)
                self.assertFalse(observed["coverage_complete"])
                self.assertFalse(observed["execution_eligible"])
                self.assertIn(
                    "blueprint_inventory_incomplete",
                    observed["coverage_failure_reason_codes"],
                )

        complete = bind(base_snapshot)
        self.assertTrue(complete["coverage_complete"])

    def test_external_opacity_binds_custom_template_and_conservative_locks(self):
        observed = bind(
            snapshot(
                (
                    obligation(
                        "bounded_semantic_opaque",
                        external="custom/helper.jinja",
                    ),
                ),
                profiles=(profile("porch_light", "notify.notify"),),
            )
        )
        fake = SimpleNamespace(
            validate=lambda: None,
            operation="set_input_boolean_state",
            target=SimpleNamespace(target_id=TARGET),
            authoritative_provider_slug="direct_home_assistant_state",
            baseline={"dependency_risk": observed},
        )
        locks = OperationalLockSetCalculator().calculate(fake)
        keys = {item.key for item in locks}

        self.assertIn(helper_dependency_lock_key(TARGET), keys)
        self.assertIn(unconstrained_helper_dependency_lock_key(), keys)
        self.assertIn("automation:porch_light", keys)
        self.assertIn("reload:custom_templates", keys)

        dynamic = bind(
            snapshot(
                (
                    obligation(
                        "bounded_semantic_opaque",
                        kind="external_template_include",
                        reason="dynamic_external_template_name",
                    ),
                ),
                profiles=(profile("porch_light", "notify.notify"),),
            )
        )
        dynamic_plan = SimpleNamespace(
            validate=lambda: None,
            operation="set_input_boolean_state",
            target=SimpleNamespace(target_id=TARGET),
            authoritative_provider_slug="direct_home_assistant_state",
            baseline={"dependency_risk": dynamic},
        )
        dynamic_keys = {
            item.key
            for item in OperationalLockSetCalculator().calculate(dynamic_plan)
        }
        self.assertIn("reload:custom_templates", dynamic_keys)
        self.assertEqual(
            observed["dependency_lock_projection"][
                "automation_resource_ids"
            ],
            ["porch_light"],
        )
        self.assertEqual(observed["model"], HELPER_DEPENDENCY_RISK_MODEL)


if __name__ == "__main__":
    unittest.main()
