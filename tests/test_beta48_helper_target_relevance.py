"""Beta 48 target-scoped helper dependency evidence."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.models import (  # noqa: E402
    AutomationActionRiskProfile,
    DependencyObligation,
    DependencyIndexSnapshot,
    OBLIGATION_LEDGER_MODEL,
    SourceCoverageItem,
    obligation_fingerprint,
)
from ha_mcp_engineering.dependency.obligation_ledger import (  # noqa: E402
    MAX_TEMPLATE_CANDIDATES,
    TemplateObligationAnalyzer,
)
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.dependency.semantic_registry import (  # noqa: E402
    supported_home_assistant_versions,
)
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ChangeOperation,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS,
    HELPER_DEPENDENCY_RISK_EXECUTION_MODELS,
    HELPER_DEPENDENCY_RISK_MODEL,
    build_helper_dependency_risk_binding,
    helper_dependency_risk_assessment,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.f3_configuration.locks import (  # noqa: E402
    helper_dependency_lock_key,
    operation_lock_requests,
    unconstrained_helper_dependency_lock_key,
)
from ha_mcp_engineering.f3_configuration import locks as locks_module  # noqa: E402
from tests.f3_configuration_fixtures import (  # noqa: E402
    SyntheticConfigurationGateway,
    adapter_for,
    proposal_for,
    valid_config,
)
from tests.test_beta46_helper_risk_semantic_completion import (  # noqa: E402
    _binding as _template_binding,
    _profile as _action_profile,
)


STANDARD_TARGET = "input_boolean.beta48_standard"
CONSEQUENTIAL_TARGET = "input_boolean.guest_mode"
SUPPORTED_HA_VERSION = supported_home_assistant_versions()[-1]


def _snapshot(result) -> DependencyIndexSnapshot:
    return DependencyIndexSnapshot(
        fingerprint="8" * 64,
        generation=48,
        built_at_monotonic=time.monotonic(),
        built_at="2026-08-27T12:00:00+00:00",
        findings=tuple(result.findings),
        dynamic_references=tuple(result.dynamic_references),
        target_metadata=result.target_metadata,
        coverage=tuple(result.coverage),
        automation_action_profiles=tuple(result.automation_action_profiles),
        automation_read_failures=tuple(result.automation_read_failures),
        obligations=tuple(result.obligations),
        obligation_ledger_model=result.obligation_ledger_model,
        label_memberships=result.label_memberships,
        label_membership_fingerprints=(
            result.label_membership_fingerprints
        ),
        label_membership_truncated=result.label_membership_truncated,
        label_registry_complete=result.label_registry_complete,
        home_assistant_version=result.home_assistant_version,
        home_assistant_version_status=result.home_assistant_version_status,
    )


def _binding(snapshot: DependencyIndexSnapshot, entity_id: str) -> dict:
    return build_helper_dependency_risk_binding(
        snapshot,
        entity_id=entity_id,
        index_metadata={
            "freshness": "current",
            "evidence_stale": False,
            "invalidated": False,
        },
    )


def _obligation(**overrides) -> DependencyObligation:
    fields = {
        "evidence_id": "ev_beta48_failure_precedence",
        "source_type": "automation",
        "source_id": "beta48_failure_precedence",
        "source_entity_id": "automation.beta48_failure_precedence",
        "config_path": "$.condition[0].value_template",
        "relation": "condition",
        "outcome": "exact_dependency",
        "obligation_kind": "semantic_operation",
        "reason_code": "synthetic_failure_precedence",
        "semantic_category": "unknown",
        "semantic_registry_version": "semantic-registry-fixture",
        "semantic_registry_fingerprint": "a" * 64,
        "expression_fingerprint": "b" * 64,
        "configuration_fingerprint": "c" * 64,
        "lock_projection": "exact",
    }
    fields.update(overrides)
    return DependencyObligation(**fields)


def _coverage_snapshot(
    obligation: DependencyObligation,
    *,
    source_id: str = "beta48_failure_precedence",
) -> DependencyIndexSnapshot:
    config = {
        "action": [
            {
                "service": "cover.open_cover",
                "target": {"entity_id": "cover.synthetic_garage"},
            }
        ]
    }
    profile: AutomationActionRiskProfile = _action_profile(source_id, config)
    return DependencyIndexSnapshot(
        fingerprint="7" * 64,
        generation=48,
        built_at_monotonic=time.monotonic(),
        built_at="2026-08-27T12:00:00+00:00",
        findings=(),
        dynamic_references=(),
        target_metadata={},
        coverage=(
            SourceCoverageItem(
                "automation", "direct_ha_api", "automation_config", "complete"
            ),
            SourceCoverageItem(
                "blueprint", "direct_ha_api", "blueprint_source", "complete"
            ),
        ),
        automation_action_profiles=(profile,),
        obligations=(obligation,),
        obligation_ledger_model=OBLIGATION_LEDGER_MODEL,
        home_assistant_version=SUPPORTED_HA_VERSION,
        home_assistant_version_status="observed",
    )


class Beta48FullIndexTargetScopingTests(unittest.IsolatedAsyncioTestCase):
    class Rest:
        def __init__(self) -> None:
            self.configs: dict[str, dict] = {}
            # Nineteen three-terminal context expressions and one two-terminal
            # duration expression reproduce the deployed 59-obligation shape
            # across six unrelated consequential sources.
            expression_counts = (4, 3, 3, 3, 3, 3)
            for source_index, count in enumerate(expression_counts):
                conditions = [
                    {
                        "condition": "template",
                        "value_template": "{{ trigger.context.user_id }}",
                    }
                    for _ in range(count)
                ]
                if source_index == 0:
                    conditions.append(
                        {
                            "condition": "template",
                            "value_template": "{{ trigger.for }}",
                        }
                    )
                source_id = f"unrelated_{source_index}"
                self.configs[source_id] = {
                    "alias": f"Synthetic unrelated {source_index}",
                    "trigger": [
                        {
                            "platform": "state",
                            "entity_id": "person.synthetic_resident",
                            "for": {"seconds": 30},
                        }
                    ],
                    "condition": conditions,
                    "action": [
                        {
                            "service": "cover.open_cover",
                            "target": {
                                "entity_id": "cover.synthetic_garage"
                            },
                        }
                    ]
                    + (
                        [{"service": "custom_domain.unknown_effect"}]
                        if source_index == 0
                        else []
                    ),
                    "mode": "single",
                }
            for source_index in range(7):
                source_id = f"guest_consequence_{source_index}"
                self.configs[source_id] = {
                    "alias": f"Synthetic guest consequence {source_index}",
                    "trigger": [
                        {
                            "platform": "state",
                            "entity_id": CONSEQUENTIAL_TARGET,
                        }
                    ],
                    "condition": [],
                    "action": [
                        {
                            "service": "lock.unlock",
                            "target": {
                                "entity_id": f"lock.synthetic_{source_index}"
                            },
                        },
                        {
                            "service": "notify.mobile_app_synthetic",
                            "data": {"message": "Guest mode changed"},
                        },
                    ],
                    "mode": "single",
                }

        async def request(self, method, path):
            if path == "/config":
                return {"version": SUPPORTED_HA_VERSION}
            if path == "/states":
                states = [
                    {
                        "entity_id": STANDARD_TARGET,
                        "state": "off",
                        "attributes": {},
                    },
                    {
                        "entity_id": CONSEQUENTIAL_TARGET,
                        "state": "off",
                        "attributes": {},
                    },
                ]
                states.extend(
                    {
                        "entity_id": f"automation.{source_id}",
                        "state": "on",
                        "attributes": {
                            "id": source_id,
                            "friendly_name": config["alias"],
                        },
                    }
                    for source_id, config in self.configs.items()
                )
                return states
            prefix = "/config/automation/config/"
            if path.startswith(prefix):
                return self.configs[path.removeprefix(prefix)]
            raise AssertionError(path)

    class WebSocket:
        async def command(self, payload):
            if payload in (
                {"type": "config/entity_registry/list"},
                {"type": "config/label_registry/list"},
            ):
                return []
            raise AssertionError(payload)

    async def asyncSetUp(self) -> None:
        result = await DirectHaDependencyProvider(
            self.Rest(), self.WebSocket(), concurrency=4
        ).scan()
        self.snapshot = _snapshot(result)

    async def test_full_index_standard_helper_excludes_unrelated_opacity(self):
        binding = _binding(self.snapshot, STANDARD_TARGET)
        risk = helper_dependency_risk_assessment(
            {"binding": binding, "provenance": {"generation": 48}}
        )

        self.assertEqual(
            "helper-dependency-risk-v13", HELPER_DEPENDENCY_RISK_MODEL
        )
        self.assertEqual(0, binding["exact_dependency_obligation_count"])
        self.assertEqual(0, binding["opaque_obligation_count"])
        self.assertEqual([], binding["relevant_downstream_object_ids"])
        self.assertEqual([], binding["downstream_profiles"])
        self.assertTrue(binding["coverage_complete"])
        self.assertTrue(binding["evidence_complete"])
        self.assertTrue(binding["execution_eligible"])
        self.assertEqual("none", binding["physical_consequence"])
        self.assertEqual("low", risk.level.value)
        self.assertTrue(risk.apply_allowed)
        self.assertGreater(binding["non_relevant_obligation_count"], 59)
        self.assertTrue(binding["non_relevant_obligations_compacted"])

        scoped_context = [
            item
            for item in self.snapshot.obligations
            if item.reason_code
            in {
                "trigger_context_contract_not_guaranteed",
                "trigger_duration_contract_not_guaranteed",
            }
        ]
        self.assertEqual(20, len(scoped_context))
        self.assertTrue(
            all(
                item.outcome == "bounded_semantic_opaque"
                and item.target_selector_scope == "dependency_neutral"
                for item in scoped_context
            )
        )
        self.assertTrue(
            all(item.target_selector_scope for item in self.snapshot.obligations)
        )
        incomplete_profile = next(
            item
            for item in self.snapshot.automation_action_profiles
            if item.source_id == "unrelated_0"
        )
        self.assertFalse(incomplete_profile.semantic_complete)
        self.assertNotIn(
            "automation.unrelated_0",
            binding["relevant_downstream_object_ids"],
        )

    async def test_full_index_consequential_helper_retains_exact_profiles(self):
        binding = _binding(self.snapshot, CONSEQUENTIAL_TARGET)
        risk = helper_dependency_risk_assessment(
            {"binding": binding, "provenance": {"generation": 48}}
        )

        self.assertGreaterEqual(binding["exact_dependency_obligation_count"], 7)
        self.assertEqual(0, binding["opaque_obligation_count"])
        self.assertEqual(7, len(binding["downstream_profiles"]))
        self.assertEqual(
            7, len(binding["consequential_downstream_object_ids"])
        )
        self.assertTrue(binding["evidence_complete"])
        self.assertTrue(binding["execution_eligible"])
        self.assertEqual("safety_critical", binding["physical_consequence"])
        self.assertEqual("high", risk.level.value)
        self.assertTrue(risk.apply_allowed)

        repeated = _binding(self.snapshot, CONSEQUENTIAL_TARGET)
        self.assertEqual(
            binding["evidence_fingerprint"],
            repeated["evidence_fingerprint"],
        )
        self.assertEqual(
            binding["obligation_evidence"],
            repeated["obligation_evidence"],
        )
        self.assertEqual(
            binding["downstream_profiles"],
            repeated["downstream_profiles"],
        )


class Beta48TargetPolarityTests(unittest.TestCase):
    def test_arbitrary_selector_remains_opaque_and_owner_actionable(self):
        binding = _template_binding(
            "{{ states(caller_supplied) }}",
            {
                "action": [
                    {
                        "service": "cover.open_cover",
                        "target": {"entity_id": "cover.synthetic_garage"},
                    }
                ]
            },
            source_id="beta48_arbitrary_selector",
        )
        risk = helper_dependency_risk_assessment(
            {"binding": binding, "provenance": {"generation": 48}}
        )

        self.assertGreater(binding["opaque_obligation_count"], 0)
        self.assertFalse(binding["evidence_complete"])
        self.assertTrue(binding["execution_contract_complete"])
        self.assertTrue(binding["execution_eligible"])
        self.assertTrue(binding["relevant_downstream_object_ids"])
        self.assertEqual("high", risk.level.value)
        self.assertTrue(risk.apply_allowed)

    def test_neutral_trigger_values_become_opaque_when_used_as_selectors(self):
        for index, selector in enumerate(
            ("trigger.context.user_id", "trigger.for")
        ):
            with self.subTest(selector=selector):
                binding = _template_binding(
                    "{{ states(" + selector + ") }}",
                    {
                        "trigger": [
                            {
                                "platform": "state",
                                "entity_id": "person.synthetic_resident",
                                "for": {"seconds": 30},
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
                    },
                    source_id=f"beta48_neutral_selector_{index}",
                )
                self.assertGreater(binding["opaque_obligation_count"], 0)
                self.assertFalse(binding["evidence_complete"])
                self.assertTrue(binding["execution_contract_complete"])
                self.assertTrue(binding["execution_eligible"])

    def test_target_relevance_change_is_fingerprint_material(self):
        unrelated = _template_binding(
            "{{ states('sensor.synthetic') }}",
            {"action": [{"service": "light.turn_on"}]},
            source_id="beta48_relevance_drift",
        )
        included = _template_binding(
            "{{ states('input_boolean.beta46_target') }}",
            {"action": [{"service": "light.turn_on"}]},
            source_id="beta48_relevance_drift",
        )

        self.assertEqual([], unrelated["relevant_downstream_object_ids"])
        self.assertTrue(included["relevant_downstream_object_ids"])
        self.assertNotEqual(
            unrelated["evidence_fingerprint"],
            included["evidence_fingerprint"],
        )

    def test_relevant_semantically_incomplete_profile_remains_disclosed(self):
        binding = _template_binding(
            "{{ states('input_boolean.beta46_target') }}",
            {"action": [{"service": "custom_domain.unknown_effect"}]},
            source_id="beta48_relevant_semantic_failure",
        )

        self.assertGreater(binding["exact_dependency_obligation_count"], 0)
        self.assertTrue(binding["relevant_downstream_object_ids"])
        self.assertFalse(binding["coverage_complete"])
        self.assertFalse(binding["evidence_complete"])
        self.assertTrue(binding["execution_contract_complete"])
        self.assertTrue(binding["execution_eligible"])
        self.assertIn(
            "action_profile_semantic_incomplete",
            binding["coverage_failure_reason_codes"],
        )

    def test_target_scope_is_fingerprint_bound_and_strictly_validated(self):
        item = DependencyObligation(
            evidence_id="ev_beta48_scope",
            source_type="automation",
            source_id="scope_fixture",
            source_entity_id="automation.scope_fixture",
            config_path="$.condition[0].value_template",
            relation="condition",
            outcome="bounded_semantic_opaque",
            obligation_kind="semantic_operation",
            reason_code="synthetic_scope_fixture",
            semantic_category="unknown",
            semantic_registry_version="semantic-registry-fixture",
            semantic_registry_fingerprint="a" * 64,
            expression_fingerprint="b" * 64,
            configuration_fingerprint="c" * 64,
            exact_entity_ids=("sensor.synthetic",),
            target_selector_scope="closed_finite_candidates",
            lock_projection="conservative",
        )
        changed = replace(item, target_selector_scope="target_capable")
        self.assertNotEqual(
            obligation_fingerprint(item), obligation_fingerprint(changed)
        )
        with self.assertRaisesRegex(ValueError, "target scope"):
            replace(item, target_selector_scope="unsupported_scope")

        exact = replace(
            item,
            outcome="exact_dependency",
            exact_entity_ids=("input_boolean.beta46_target",),
            target_selector_scope="dependency_neutral",
        )
        snapshot = DependencyIndexSnapshot(
            fingerprint="9" * 64,
            generation=48,
            built_at_monotonic=time.monotonic(),
            built_at="2026-08-27T12:00:00+00:00",
            findings=(),
            dynamic_references=(),
            target_metadata={},
            coverage=(),
            obligations=(exact,),
        )
        # The exact edge remains visible even under contradictory internal
        # scope evidence; missing coverage then keeps the whole plan closed.
        projected = _binding(snapshot, "input_boolean.beta46_target")
        self.assertEqual(1, projected["exact_dependency_obligation_count"])
        self.assertFalse(projected["execution_eligible"])

    def test_v3_through_v12_are_readable_but_only_v13_executes(self):
        self.assertEqual(
            "helper-dependency-risk-v13", HELPER_DEPENDENCY_RISK_MODEL
        )
        self.assertEqual(
            frozenset({"helper-dependency-risk-v13"}),
            HELPER_DEPENDENCY_RISK_EXECUTION_MODELS,
        )
        for version in range(3, 13):
            model = f"helper-dependency-risk-v{version}"
            self.assertIn(model, HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS)
            self.assertNotIn(model, HELPER_DEPENDENCY_RISK_EXECUTION_MODELS)


class Beta48CoverageFailurePrecedenceTests(unittest.TestCase):
    @staticmethod
    def _failure_signal_cases():
        return (
            (
                "outcome",
                {
                    "outcome": "coverage_failure",
                    "target_selector_scope": "closed_finite_candidates",
                    "lock_projection": "exact",
                },
            ),
            (
                "limit",
                {
                    "outcome": "exact_dependency",
                    "limit_exceeded": True,
                    "target_selector_scope": "dependency_neutral",
                    "lock_projection": "exact",
                },
            ),
            (
                "lock_projection",
                {
                    "outcome": "exact_dependency",
                    "target_selector_scope": "closed_finite_candidates",
                    "lock_projection": "coverage_failure",
                },
            ),
            (
                "selector_scope",
                {
                    "outcome": "exact_dependency",
                    "target_selector_scope": "coverage_failure",
                    "lock_projection": "exact",
                },
            ),
            (
                "combined",
                {
                    "outcome": "coverage_failure",
                    "limit_exceeded": True,
                    "target_selector_scope": "dependency_neutral",
                    "lock_projection": "coverage_failure",
                },
            ),
        )

    @staticmethod
    def _analyzer(source_id: str) -> TemplateObligationAnalyzer:
        return TemplateObligationAnalyzer(
            source_type="automation",
            source_id=source_id,
            config_path="$.condition[0].value_template",
            relation="condition",
            source_entity_id=f"automation.{source_id}",
            source_name="Synthetic Beta 48 overflow fixture",
            source_state="on",
            configuration_fingerprint="d" * 64,
            entity_id_validator=lambda value: "." in value,
        )

    def test_emit_overflow_has_unconditional_failure_scope(self):
        cases = (
            {
                "name": "exact_candidates",
                "outcome": "exact_dependency",
                "exact": tuple(
                    f"input_boolean.overflow_{index:03d}"
                    for index in range(MAX_TEMPLATE_CANDIDATES + 1)
                ),
                "lock": "exact",
            },
            {
                "name": "domains",
                "outcome": "exact_dependency",
                "domains": tuple(
                    f"synthetic_domain_{index:03d}"
                    for index in range(MAX_TEMPLATE_CANDIDATES + 1)
                ),
                "lock": "exact",
            },
            {
                "name": "selectors",
                "outcome": "proven_dependency_neutral",
                "selectors": tuple(
                    f"selector_{index:03d}"
                    for index in range(MAX_TEMPLATE_CANDIDATES + 1)
                ),
                "lock": "none",
            },
            {
                "name": "context",
                "outcome": "proven_dependency_neutral",
                "context": tuple(
                    f"context.path.{index:03d}" for index in range(33)
                ),
                "lock": "none",
            },
        )
        for case in cases:
            with self.subTest(case=case["name"]):
                analyzer = self._analyzer(f"emit_{case['name']}")
                analyzer._emit(  # noqa: SLF001 - exact construction boundary
                    outcome=case["outcome"],
                    kind="semantic_operation",
                    reason="synthetic_emit_overflow",
                    category="unknown",
                    node=None,
                    exact=case.get("exact", ()),
                    domains=case.get("domains"),
                    selectors=case.get("selectors", ()),
                    context=case.get("context", ()),
                    lock=case["lock"],
                )
                item = analyzer._finalize().obligations[0]  # noqa: SLF001
                self.assertEqual("coverage_failure", item.outcome)
                self.assertTrue(item.limit_exceeded)
                self.assertEqual(
                    "coverage_failure", item.target_selector_scope
                )
                self.assertEqual("coverage_failure", item.lock_projection)

    def test_constructor_normalizes_contradictory_failure_authority(self):
        cases = (
            _obligation(
                outcome="coverage_failure",
                target_selector_scope="closed_finite_candidates",
                lock_projection="exact",
                exact_entity_ids=("input_boolean.retained",),
            ),
            _obligation(
                outcome="proven_dependency_neutral",
                limit_exceeded=True,
                target_selector_scope="dependency_neutral",
                lock_projection="none",
            ),
            _obligation(
                outcome="exact_dependency",
                target_selector_scope="closed_entity_domains",
                possible_entity_domains=("sensor",),
                lock_projection="coverage_failure",
            ),
        )
        for item in cases:
            with self.subTest(item=item):
                self.assertEqual(
                    "coverage_failure", item.target_selector_scope
                )
                self.assertEqual("coverage_failure", item.lock_projection)

    def test_each_consequence_failure_signal_preserves_exact_authority(self):
        operation = SimpleNamespace(
            resource_type="automation",
            current_config=lambda: {},
            proposed_config=lambda: None,
        )
        for name, failure_signal in self._failure_signal_cases():
            with self.subTest(case=name):
                item = _obligation(
                    exact_entity_ids=(STANDARD_TARGET,),
                    **failure_signal,
                )
                self.assertEqual((STANDARD_TARGET,), item.exact_entity_ids)

                binding = _binding(
                    _coverage_snapshot(item), STANDARD_TARGET
                )
                risk = helper_dependency_risk_assessment(
                    {
                        "binding": binding,
                        "provenance": {"generation": 48},
                    }
                )
                projected = binding["obligation_evidence"][0]
                self.assertEqual(
                    "coverage_failure", projected["target_outcome"]
                )
                self.assertFalse(binding["coverage_complete"])
                self.assertFalse(binding["evidence_complete"])
                self.assertTrue(binding["execution_contract_complete"])
                self.assertTrue(binding["execution_eligible"])
                self.assertGreater(binding["coverage_failure_count"], 0)
                self.assertTrue(risk.apply_allowed)

                plan = SimpleNamespace(
                    operation=ChangeOperation.SET_INPUT_BOOLEAN_STATE,
                    operational=SimpleNamespace(
                        baseline={"dependency_risk": binding}
                    ),
                    risk=risk,
                )
                self.assertTrue(
                    ChangeGovernanceService
                    ._helper_dependency_plan_is_actionable(  # noqa: SLF001
                        plan
                    )
                )

                with patch.object(
                    locks_module,
                    "extract_document_with_obligations",
                    return_value=((), (), (item,)),
                ):
                    requests = (
                        locks_module._automation_helper_dependency_locks(  # noqa: SLF001
                            operation
                        )
                    )
                keys = {request.key for request in requests}
                self.assertIn(
                    helper_dependency_lock_key(STANDARD_TARGET), keys
                )
                self.assertIn(
                    unconstrained_helper_dependency_lock_key(), keys
                )

    def test_failure_signals_are_canonicalized_after_bounding(self):
        for name, failure_signal in self._failure_signal_cases():
            with self.subTest(case=name):
                item = _obligation(
                    exact_entity_ids=(STANDARD_TARGET,),
                    **failure_signal,
                )
                self.assertTrue(item.coverage_failure_authority)
                self.assertEqual("coverage_failure", item.outcome)
                self.assertEqual(
                    "coverage_failure", item.target_selector_scope
                )
                self.assertEqual("coverage_failure", item.lock_projection)
                self.assertEqual((STANDARD_TARGET,), item.exact_entity_ids)

    def test_ordinary_exact_target_remains_complete_and_exact_only(self):
        item = _obligation(
            exact_entity_ids=(STANDARD_TARGET,),
            target_selector_scope="closed_finite_candidates",
        )
        binding = _binding(_coverage_snapshot(item), STANDARD_TARGET)
        risk = helper_dependency_risk_assessment(
            {"binding": binding, "provenance": {"generation": 48}}
        )
        self.assertFalse(item.coverage_failure_authority)
        self.assertEqual("exact_dependency", item.outcome)
        self.assertEqual(
            "exact_dependency",
            binding["obligation_evidence"][0]["target_outcome"],
        )
        self.assertTrue(binding["coverage_complete"])
        self.assertTrue(binding["evidence_complete"])
        self.assertTrue(binding["execution_eligible"])
        self.assertTrue(risk.apply_allowed)

        operation = SimpleNamespace(
            resource_type="automation",
            current_config=lambda: {},
            proposed_config=lambda: None,
        )
        with patch.object(
            locks_module,
            "extract_document_with_obligations",
            return_value=((), (), (item,)),
        ):
            requests = locks_module._automation_helper_dependency_locks(  # noqa: SLF001
                operation
            )
        keys = {request.key for request in requests}
        self.assertIn(helper_dependency_lock_key(STANDARD_TARGET), keys)
        self.assertNotIn(unconstrained_helper_dependency_lock_key(), keys)

    def test_failure_classification_changes_binding_fingerprint(self):
        exact = _obligation(
            exact_entity_ids=(STANDARD_TARGET,),
            target_selector_scope="closed_finite_candidates",
        )
        failed = _obligation(
            exact_entity_ids=(STANDARD_TARGET,),
            target_selector_scope="closed_finite_candidates",
            lock_projection="coverage_failure",
        )
        self.assertNotEqual(
            _binding(_coverage_snapshot(exact), STANDARD_TARGET)[
                "evidence_fingerprint"
            ],
            _binding(_coverage_snapshot(failed), STANDARD_TARGET)[
                "evidence_fingerprint"
            ],
        )

    def test_bounded_target_evidence_forces_failure_scope_and_lock(self):
        cases = (
            {
                "exact_entity_ids": tuple(
                    f"input_boolean.bound_{index:05d}" for index in range(1000)
                )
            },
            {
                "possible_entity_domains": tuple(
                    f"synthetic_domain_{index:05d}" for index in range(400)
                )
            },
            {
                "literal_selectors": tuple(
                    f"synthetic_selector_{index:05d}" for index in range(400)
                )
            },
            {
                "context_provenance": tuple(
                    f"synthetic.context.{index:05d}" for index in range(400)
                )
            },
            {
                "literal_selectors": (
                    "Authorization: Bearer synthetic_beta48_token_"
                    "abcdefghijklmnopqrstuvwxyz",
                )
            },
        )
        for evidence in cases:
            with self.subTest(evidence=next(iter(evidence))):
                item = _obligation(**evidence)
                self.assertTrue(item.evidence_bounded)
                self.assertTrue(item.limit_exceeded)
                self.assertEqual(
                    "coverage_failure", item.target_selector_scope
                )
                self.assertEqual("coverage_failure", item.lock_projection)

    def test_clipped_target_is_not_projected_as_an_exclusion(self):
        retained_first = tuple(
            f"input_boolean.a_{index:05d}" for index in range(1000)
        )
        item = _obligation(
            exact_entity_ids=retained_first + (STANDARD_TARGET,),
            target_selector_scope="closed_finite_candidates",
        )
        self.assertNotIn(STANDARD_TARGET, item.exact_entity_ids)

        first = _binding(_coverage_snapshot(item), STANDARD_TARGET)
        repeated_item = _obligation(
            exact_entity_ids=retained_first + (STANDARD_TARGET,),
            target_selector_scope="closed_finite_candidates",
        )
        repeated = _binding(_coverage_snapshot(repeated_item), STANDARD_TARGET)
        risk = helper_dependency_risk_assessment(
            {"binding": first, "provenance": {"generation": 48}}
        )

        self.assertEqual(0, first["exact_dependency_obligation_count"])
        self.assertGreater(first["coverage_failure_count"], 0)
        self.assertFalse(first["evidence_complete"])
        self.assertTrue(first["execution_contract_complete"])
        self.assertTrue(first["execution_eligible"])
        self.assertEqual("high", risk.level.value)
        self.assertTrue(risk.apply_allowed)
        self.assertEqual(
            "coverage_failure",
            first["obligation_evidence"][0]["target_outcome"],
        )
        self.assertEqual(
            first["evidence_fingerprint"], repeated["evidence_fingerprint"]
        )

    def test_f3_defense_rejects_mutated_closed_failure_authority(self):
        failure_signals = (
            {
                "outcome": "coverage_failure",
                "limit_exceeded": False,
                "lock_projection": "exact",
                "target_selector_scope": "closed_finite_candidates",
            },
            {
                "outcome": "exact_dependency",
                "limit_exceeded": True,
                "lock_projection": "exact",
                "target_selector_scope": "dependency_neutral",
            },
            {
                "outcome": "exact_dependency",
                "limit_exceeded": False,
                "lock_projection": "coverage_failure",
                "target_selector_scope": "closed_entity_domains",
            },
        )
        operation = SimpleNamespace(
            resource_type="automation",
            current_config=lambda: {},
            proposed_config=lambda: None,
        )
        for failure_signal in failure_signals:
            with self.subTest(failure_signal=failure_signal):
                item = _obligation(exact_entity_ids=(STANDARD_TARGET,))
                # Bypass frozen-constructor normalization deliberately. F3
                # must not depend on every historical/internal producer being
                # well behaved.
                for field, value in failure_signal.items():
                    object.__setattr__(item, field, value)
                with patch.object(
                    locks_module,
                    "extract_document_with_obligations",
                    return_value=((), (), (item,)),
                ):
                    requests = (
                        locks_module._automation_helper_dependency_locks(  # noqa: SLF001
                            operation
                        )
                    )
                keys = {request.key for request in requests}
                self.assertIn(
                    helper_dependency_lock_key(STANDARD_TARGET), keys
                )
                self.assertIn(
                    unconstrained_helper_dependency_lock_key(), keys
                )


class Beta48ConfigurationLockPolarityTests(unittest.IsolatedAsyncioTestCase):
    async def _keys(self, template: str, *, action: str = "create") -> set[str]:
        proposed = valid_config("automation")
        proposed["trigger"] = [
            {
                "platform": "state",
                "entity_id": "person.synthetic_resident",
                "for": {"seconds": 30},
            }
        ]
        proposed["condition"] = [
            {"condition": "template", "value_template": template}
        ]
        gateway = SyntheticConfigurationGateway()
        operation = await adapter_for(
            "automation", action, gateway
        ).prepare(
            proposal_for(
                "automation",
                action,
                current_config=(
                    valid_config("automation") if action == "update" else None
                ),
                proposed_config=proposed,
            )
        )
        return {request.key for request in operation_lock_requests(operation)}

    async def test_exact_opaque_and_neutral_lock_projections_agree(self):
        exact = await self._keys(
            "{{ states('input_boolean.beta48_standard') }}"
        )
        opaque = await self._keys("{{ states(caller_supplied) }}")
        neutral = await self._keys("{{ trigger.for }}")

        self.assertIn(
            helper_dependency_lock_key(STANDARD_TARGET), exact
        )
        self.assertNotIn(
            unconstrained_helper_dependency_lock_key(), exact
        )
        self.assertIn(
            unconstrained_helper_dependency_lock_key(), opaque
        )
        self.assertFalse(
            any(key.startswith("helper_dependency:") for key in neutral)
        )

    async def test_production_overflow_path_is_conservatively_locked(self):
        candidates = [
            f"input_boolean.beta48_overflow_{index:03d}"
            for index in range(MAX_TEMPLATE_CANDIDATES)
        ] + [STANDARD_TARGET]
        rendered = ",".join(repr(item) for item in candidates)
        template = (
            "{% set candidates = ["
            + rendered
            + "] %}{% for entity_id in candidates %}"
            "{{ states(entity_id) }}{% endfor %}"
        )
        for action in ("create", "update"):
            with self.subTest(action=action):
                keys = await self._keys(template, action=action)
                self.assertIn(unconstrained_helper_dependency_lock_key(), keys)

    async def test_below_limit_exact_candidates_remain_exact_only(self):
        template = (
            "{% for entity_id in "
            "['input_boolean.beta48_standard', 'sensor.synthetic'] %}"
            "{{ states(entity_id) }}{% endfor %}"
        )
        keys = await self._keys(template)
        self.assertIn(helper_dependency_lock_key(STANDARD_TARGET), keys)
        self.assertNotIn(unconstrained_helper_dependency_lock_key(), keys)


if __name__ == "__main__":
    unittest.main()
