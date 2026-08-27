"""Beta 48 target-scoped helper dependency evidence."""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.models import (  # noqa: E402
    DependencyObligation,
    DependencyIndexSnapshot,
    obligation_fingerprint,
)
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.dependency.semantic_registry import (  # noqa: E402
    supported_home_assistant_versions,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS,
    HELPER_DEPENDENCY_RISK_EXECUTION_MODELS,
    HELPER_DEPENDENCY_RISK_MODEL,
    build_helper_dependency_risk_binding,
    helper_dependency_risk_assessment,
)
from ha_mcp_engineering.f3_configuration.locks import (  # noqa: E402
    helper_dependency_lock_key,
    operation_lock_requests,
    unconstrained_helper_dependency_lock_key,
)
from tests.f3_configuration_fixtures import (  # noqa: E402
    SyntheticConfigurationGateway,
    adapter_for,
    proposal_for,
    valid_config,
)
from tests.test_beta46_helper_risk_semantic_completion import (  # noqa: E402
    _binding as _template_binding,
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

        self.assertEqual("helper-dependency-risk-v7", HELPER_DEPENDENCY_RISK_MODEL)
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
    def test_arbitrary_selector_remains_opaque_and_nonactionable(self):
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
        self.assertFalse(binding["execution_eligible"])
        self.assertTrue(binding["relevant_downstream_object_ids"])
        self.assertEqual("high", risk.level.value)
        self.assertFalse(risk.apply_allowed)

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
                self.assertFalse(binding["execution_eligible"])

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

    def test_relevant_semantically_incomplete_profile_remains_blocking(self):
        binding = _template_binding(
            "{{ states('input_boolean.beta46_target') }}",
            {"action": [{"service": "custom_domain.unknown_effect"}]},
            source_id="beta48_relevant_semantic_failure",
        )

        self.assertGreater(binding["exact_dependency_obligation_count"], 0)
        self.assertTrue(binding["relevant_downstream_object_ids"])
        self.assertFalse(binding["coverage_complete"])
        self.assertFalse(binding["evidence_complete"])
        self.assertFalse(binding["execution_eligible"])
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

    def test_v3_through_v6_are_readable_but_only_v7_executes(self):
        self.assertEqual("helper-dependency-risk-v7", HELPER_DEPENDENCY_RISK_MODEL)
        self.assertEqual(
            frozenset({"helper-dependency-risk-v7"}),
            HELPER_DEPENDENCY_RISK_EXECUTION_MODELS,
        )
        for version in range(3, 7):
            model = f"helper-dependency-risk-v{version}"
            self.assertIn(model, HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS)
            self.assertNotIn(model, HELPER_DEPENDENCY_RISK_EXECUTION_MODELS)


class Beta48ConfigurationLockPolarityTests(unittest.IsolatedAsyncioTestCase):
    async def _keys(self, template: str) -> set[str]:
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
            "automation", "create", gateway
        ).prepare(
            proposal_for(
                "automation",
                "create",
                current_config=None,
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


if __name__ == "__main__":
    unittest.main()
