"""HAMCP-285 evidence-based consequence labels and the input_boolean v14 reader.

Live evidence: on the installation, an input_boolean that no automation names
was planned as ``elevated_admin`` / ``high`` / ``safety_critical`` with a
response near 80,000 characters, because global dependency gaps (an unrelated
malformed registry record, opaque obligations, label selectors that do not
include it) were attributed to every helper and the consequence of every
template-heavy automation was disclosed against it.  Helper options edits and
entity disables with only *incomplete* evidence were labelled
``safety_critical`` although nothing positive named a protected object.  An
approved beta.59 input_boolean record (dependency model v13) raised an
unstructured ``ValueError`` instead of asking for a replan.

The fixtures here are shaped like that installation: sixty template-heavy
automations with broad triggers that never name the helper, five label
automations that lock doors over a label the helper does not carry, and one
unrelated malformed registry record (``tests/hamcp285_fixtures.py``).

Every input_boolean service test drives the shipped governance service and F3
runtime with the shipped runtime reader
(``helper_dependency.read_runtime_helper_dependency_risk``) over a real
``DependencyIndex``.  Helper options and availability tests drive the shipped
HAMCP-232 and HAMCP-069B harnesses.  The reproduction tests (``Reproduction*``)
use only the public plan surface, so the same test runs against the parent
commit, where it fails with the live classification.  No test reaches a
network, a live Home Assistant, or the ha-mcp provider.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from typing import Any
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency import provider as dependency_provider  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.f3.contracts import LockMode  # noqa: E402
from ha_mcp_engineering.f3.operational_locks import (  # noqa: E402
    OperationalLockSetCalculator,
)
from ha_mcp_engineering.f3.operational_models import (  # noqa: E402
    operational_policy_expectation_is_valid,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    entity_availability_exposure_planning as availability_planning,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    helper_dependency as helper_module,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    helper_options as options_contract,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    helper_options_planning as options_planning,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    input_select_dependency,
)
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalPolicyClass,
)
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    evaluate_change_policy,
)

from tests.hamcp069b_fixtures import (  # noqa: E402
    AvailabilityExposureServiceTestCase,
    automation_profile,
    exposure_snapshot,
    finding,
)
from tests.hamcp232_helper_options_fixtures import (  # noqa: E402
    HelperOptionsServiceTestCase,
)
from tests.hamcp285_fixtures import (  # noqa: E402
    ENTITY,
    RESPONSE_LIMIT,
    InputBooleanServiceTestCase,
    consumer,
    over_budget_consumers,
    template,
)


# Literal expectations, not values imported from the code under test.
V14 = "helper-dependency-risk-v14"
V13 = "helper-dependency-risk-v13"
INPUT_SELECT_V2 = "helper-dependency-risk-input-select-v2"
POSITIVE_EVIDENCE_V1 = "positive_evidence_v1"
HELPER_CHAIN_GAP = (
    "dependency_evidence_incomplete:config_entry_helper_chain_coverage_unavailable"
)
SUPERSEDED_REPLAN = {
    "reason": "helper_dependency_evidence_superseded_replan_required",
    "replan_required": True,
}
FLAG_GROUP_STATE = {
    "entity_id": "group.flags",
    "state": "on",
    "attributes": {"entity_id": [ENTITY]},
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _chars(value: Any) -> int:
    return len(json.dumps(value, indent=2, ensure_ascii=True, default=str))


# --------------------------------------------------------------------------
# Shared input_boolean assertions
# --------------------------------------------------------------------------


class InputBooleanCase(InputBooleanServiceTestCase):
    def decision(self, plan: dict[str, Any]) -> tuple[str, str, str]:
        policy = plan["policy_decision"]
        return (policy["policy_class"], policy["risk_delta"],
                policy["physical_consequence"])

    def assert_elevated_and_named(self, plan: dict[str, Any], automation: str,
                                  consequence: str | None = None) -> dict[str, Any]:
        policy_class, risk_delta, physical = self.decision(plan)
        self.assertEqual(policy_class, "elevated_admin", plan["policy_decision"])
        self.assertNotEqual(policy_class, "prohibited")
        self.assertTrue(plan["approval_actionable"])
        if consequence is not None:
            self.assertEqual(physical, consequence)
        binding = self.stored_binding(plan)
        self.assertEqual(binding["model"], V14)
        self.assertGreaterEqual(binding["relevant_consumer_count"], 1)
        self.assertIn(f"automation.{automation}",
                      binding["relevant_downstream_object_ids"])
        self.assertIn(f"automation.{automation}",
                      {item["automation_id"] for item in binding["downstream_profiles"]})
        return binding

    def assert_standard(self, plan: dict[str, Any]) -> dict[str, Any]:
        self.assertEqual(self.decision(plan), ("standard_admin", "low", "none"),
                         plan["policy_decision"])
        self.assertEqual(plan["risk"]["level"], "low")
        self.assertTrue(plan["approval_actionable"])
        binding = self.stored_binding(plan)
        self.assertEqual(binding["model"], V14)
        self.assertEqual(binding["relevant_consumer_count"], 0)
        self.assertEqual(binding["downstream_profiles"], [])
        return binding

    async def assert_refused_before_dispatch(self, result: dict[str, Any],
                                             code: str) -> None:
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertIn(code, self.child_codes(result["task_id"]))
        self.assertEqual(self.helper.dispatch_count, 0)


# --------------------------------------------------------------------------
# A. Reproductions of the live evidence (fail on the parent commit)
# --------------------------------------------------------------------------


class ReproductionInputBooleanTests(InputBooleanCase):
    async def test_an_unreferenced_input_boolean_in_the_installation_is_standard(self):
        created = await self.planned("on")
        plan = created["plan"]
        self.assertEqual(self.decision(plan), ("standard_admin", "low", "none"),
                         plan["policy_decision"])
        self.assertEqual(plan["risk"]["level"], "low")
        self.assertTrue(plan["approval_actionable"])
        self.assertFalse(plan["helper_dependency_replan_required"])
        binding = self.stored_binding(plan)
        self.assertEqual(binding["model"], V14)
        self.assertEqual(binding["downstream_profiles"], [])
        self.assertEqual(binding["relevant_consumer_count"], 0)
        self.assertEqual(binding["physical_consequence"], "none")
        # The unrelated malformed record and the label automations are decided
        # irrelevant, not attributed to this helper.
        self.assertNotIn("entity_registry_malformed_relevant_record",
                         binding["consequence_uncertainty_reason_codes"])
        self.assertLess(self.chars(created), RESPONSE_LIMIT)
        self.assertNotIn("response_paging", plan)
        self.assertEqual(
            [key for key in self.lock_modes(plan) if key.startswith("automation:")],
            [],
        )
        self.assertEqual(self.helper.dispatch_count, 0)


class ReproductionHelperOptionsTests(HelperOptionsServiceTestCase):
    async def test_options_on_an_unreferenced_template_with_only_the_chain_gap_are_unknown(self):
        entity_id = self.add_template_sensor()
        created = await self.create_options(
            {"entity_id": entity_id, "set": {"state": "{{ 3 }}"}}
        )
        policy = created["policy_decision"]
        self.assertEqual(
            (policy["policy_class"], policy["risk_delta"],
             policy["physical_consequence"]),
            ("elevated_admin", "high", "unknown"),
            policy,
        )
        self.assertTrue(created["approval_actionable"])
        self.assertEqual(created["risk"]["level"], "high")
        disclosure = self.options_disclosure(created)
        self.assertEqual(disclosure["protection"]["dependency_reasons"],
                         [HELPER_CHAIN_GAP])
        self.assertEqual(disclosure["policy_basis"]["physical_consequence"], "unknown")
        self.assertFalse(disclosure["consumers"]["evidence_complete"])
        self.assertIn("config_entry_helper_chain_coverage_unavailable",
                      disclosure["consumers"]["evidence_gaps"])
        self.assertEqual(disclosure["consumers"]["count"], 0)
        # The gap is stated to the approver in plain language.
        self.assertTrue(any("could not be fully determined" in reason
                            for reason in created["risk"]["reasons"]))
        marker = created["risk"]["evidence"][0]
        self.assertEqual(marker["physical_consequence"], "unknown")
        self.assertEqual(marker["consequence_model"], POSITIVE_EVIDENCE_V1)
        self.assertEqual(self.options_rest.submits(), [])


class ReproductionAvailabilityTests(AvailabilityExposureServiceTestCase):
    async def test_a_disable_with_incomplete_dependency_evidence_is_unknown(self):
        self.add_entity("sensor.hall_motion")
        self.snapshot = exposure_snapshot(
            findings=(finding("sensor.hall_motion", "unknown_effect"),),
            read_failures=(SimpleNamespace(source_id="broken_automation"),),
        )
        created = await self.create(operation="disable",
                                    entity_ids=["sensor.hall_motion"])
        policy = created["policy_decision"]
        self.assertEqual(
            (policy["policy_class"], policy["risk_delta"],
             policy["physical_consequence"]),
            ("elevated_admin", "high", "unknown"),
            policy,
        )
        self.assertTrue(created["approval_actionable"])
        disclosure = created["dry_run_results"]["operations"][0]["dry_run_results"][
            "entity_availability_exposure"
        ]
        reasons = disclosure["protection"]["dependency_reasons"]
        self.assertIn("unresolved_automation_consequence:unknown_effect", reasons)
        self.assertIn("dependency_evidence_incomplete:automation_read_failures", reasons)
        self.assertFalse(any(item.startswith("feeds_protected_automation:")
                             for item in reasons))
        self.assertEqual(disclosure["protection"]["object_reasons"], [])
        self.assertEqual(created["risk"]["evidence"][0]["physical_consequence"],
                         "unknown")
        self.assertEqual(self.ws.writes(), [])


# --------------------------------------------------------------------------
# B. Classification matrix (input_boolean)
# --------------------------------------------------------------------------


class InputBooleanMatrixTests(InputBooleanCase):
    async def test_literal_reference_is_elevated_and_named(self):
        plan = (await self.planned("on", extra={"uses_flag": consumer(
            "Uses flag",
            triggers=[{"trigger": "state", "entity_id": ENTITY, "to": "on"}],
            actions=[{"action": "light.turn_off",
                      "target": {"entity_id": "light.room_0"}}],
        )}))["plan"]
        binding = self.assert_elevated_and_named(plan, "uses_flag", "direct")
        self.assertEqual(binding["relevant_consumer_counts_by_class"]["referenced"], 1)
        # Only the referencing automation is relevant: no installation noise.
        self.assertEqual(binding["relevant_consumer_count"], 1)

    async def test_expand_group_membership_is_elevated_and_named(self):
        plan = (await self.planned("on", extra={"flag_expand": consumer(
            "Flag expand",
            conditions=template(
                "{{ expand('group.flags') | selectattr('state', 'eq', 'on') "
                "| list | count > 0 }}"
            ),
        )}, extra_states=[FLAG_GROUP_STATE]))["plan"]
        self.assert_elevated_and_named(plan, "flag_expand")

    async def test_area_iteration_is_elevated_and_named(self):
        plan = (await self.planned("on", extra={"flag_area": consumer(
            "Flag area",
            conditions=template(
                "{{ area_entities('living_room') | select('is_state', 'on') "
                "| list | count > 0 }}"
            ),
        )}))["plan"]
        self.assert_elevated_and_named(plan, "flag_area")

    async def test_label_target_including_the_helper_is_elevated_and_named(self):
        plan = (await self.planned(
            "on",
            extra={"flag_label": consumer(
                "Flag label",
                conditions=template(
                    "{{ label_entities('flags') | select('is_state', 'on') "
                    "| list | count > 0 }}"
                ),
            )},
            registry=[{"entity_id": ENTITY, "labels": ["flags"]}],
            labels=[{"label_id": "flags", "name": "Flags"}],
        ))["plan"]
        self.assert_elevated_and_named(plan, "flag_label")

    async def test_domain_wide_iteration_is_elevated_and_named(self):
        plan = (await self.planned("on", extra={"all_flags": consumer(
            "All flags",
            conditions=template(
                "{{ states.input_boolean | selectattr('state', 'eq', 'on') "
                "| list | count > 0 }}"
            ),
        )}))["plan"]
        self.assert_elevated_and_named(plan, "all_flags")

    async def test_an_opaque_consumer_is_elevated_and_disclosed_not_prohibited(self):
        plan = (await self.planned("on", extra={"dynamic": consumer(
            "Dynamic",
            conditions=template(
                "{{ is_state('input_boolean.' ~ states('sensor.which'), 'on') }}"
            ),
        )}))["plan"]
        binding = self.assert_elevated_and_named(plan, "dynamic", "unknown")
        self.assertGreaterEqual(binding["relevant_consumer_counts_by_class"]["opaque"], 1)
        self.assertIn("opaque_downstream_obligations",
                      binding["consequence_uncertainty_reason_codes"])
        self.assertTrue(binding["execution_contract_complete"])

    async def test_template_heavy_unrelated_automations_stay_standard(self):
        heavy = {
            f"heavy_{n}": consumer(
                f"Heavy {n}",
                triggers=[
                    {"trigger": "state", "entity_id": "sensor.room_1"},
                    {"trigger": "template",
                     "value_template": "{{ states('sensor.room_2') | int(0) > 4 }}"},
                ],
                conditions=template(
                    "{{ states.sensor | selectattr('state', 'eq', 'on') | list "
                    "| count > 1 and is_state('input_boolean.other', 'on') }}"
                ),
                actions=[{"action": "lock.unlock",
                          "target": {"entity_id": "lock.front"}}],
            )
            for n in range(5)
        }
        plan = (await self.planned("on", extra=heavy))["plan"]
        self.assert_standard(plan)

    async def test_a_positive_lock_consumer_is_safety_critical(self):
        plan = (await self.planned("on", extra={"flag_lock": consumer(
            "Flag lock",
            triggers=[{"trigger": "state", "entity_id": ENTITY, "to": "on"}],
            actions=[{"action": "lock.unlock",
                      "target": {"entity_id": "lock.front"}}],
        )}))["plan"]
        self.assert_elevated_and_named(plan, "flag_lock", "safety_critical")
        self.assertEqual(plan["risk"]["level"], "high")


class InputBooleanRefusalTests(InputBooleanCase):
    async def test_stale_state_is_refused_before_dispatch(self):
        plan = (await self.planned("on"))["plan"]
        await self.grant(plan)
        # Same value, newer change: the planned baseline no longer holds.
        self.helper.set_observed_state("off", self.clock)
        result = await self.apply(plan)
        await self.assert_refused_before_dispatch(result, "stale_baseline")

    async def test_ambiguous_or_invalid_targets_are_refused_before_any_read(self):
        self.install()
        for entity_id in ("input_boolean.*", f"{ENTITY},input_boolean.other",
                          "switch.not_allowed", "input_boolean.", ""):
            with self.subTest(entity_id=entity_id):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_helper_state_plan(
                        entity_id=entity_id, desired_state="on"
                    )
                self.assertEqual(raised.exception.code, ErrorCode.INVALID_REQUEST)
                self.assertEqual(raised.exception.details,
                                 {"reason": "invalid_exact_helper_state_request"})
        self.assertEqual(self.service.repository.list(), [])
        self.assertEqual(self.reads, [])
        self.assertEqual(self.rest.requests, [])
        self.assertEqual(self.helper.dispatch_count, 0)

    async def test_an_unverified_provider_is_refused(self):
        self.install()
        self.helper.provider_evidence = {
            "provider": "ha_mcp", "provider_contract_model": "unreviewed",
        }
        with self.assertRaises(GovernanceError) as raised:
            await self.create("on")
        self.assertEqual(raised.exception.code, ErrorCode.INTERNAL_INVARIANT_VIOLATION)
        self.assertEqual(self.service.repository.list(), [])
        self.assertEqual(self.reads, [])
        self.assertEqual(self.helper.dispatch_count, 0)

    async def test_dependency_drift_after_approval_is_refused(self):
        plan = (await self.planned("on"))["plan"]
        await self.grant(plan)
        self.rest.configs["late"] = consumer(
            "Late", triggers=[{"trigger": "state", "entity_id": ENTITY}],
            actions=[{"action": "lock.unlock", "target": {"entity_id": "lock.front"}}],
        )
        result = await self.apply(plan)
        await self.assert_refused_before_dispatch(result, "dependency_risk_drift")


class InputBooleanLockTests(InputBooleanCase):
    async def test_a_bounded_graph_locks_each_consumer_precisely(self):
        plan = (await self.planned("on", extra={"uses_flag": consumer(
            "Uses flag",
            triggers=[{"trigger": "state", "entity_id": ENTITY}],
        )}))["plan"]
        modes = self.lock_modes(plan)
        automation_keys = [key for key in modes if key.startswith("automation:")]
        self.assertEqual(len(automation_keys), 1, modes)
        self.assertIn("uses_flag", automation_keys[0])
        self.assertEqual(modes["reload:automation"], LockMode.SHARED)
        projection = self.stored_binding(plan)["dependency_lock_projection"]
        self.assertFalse(projection["coarse_automation_fence"])

    async def test_an_over_budget_graph_takes_the_coarse_automation_fence(self):
        plan = (await self.planned("on", extra=over_budget_consumers(60)))["plan"]
        binding = self.stored_binding(plan)
        self.assertTrue(binding["relevant_consumers_truncated"])
        self.assertTrue(binding["dependency_lock_projection"]["coarse_automation_fence"])
        modes = self.lock_modes(plan)
        self.assertEqual(modes["reload:automation"], LockMode.EXCLUSIVE)
        self.assertEqual(self.decision(plan)[0], "elevated_admin")

    async def test_an_unattributable_coverage_gap_scopes_all_under_the_fence(self):
        with mock.patch.object(dependency_provider, "MAX_AUTOMATION_SOURCES", 10):
            plan = (await self.planned("on"))["plan"]
        binding = self.stored_binding(plan)
        self.assertEqual(binding["coverage_failure_attribution"], "conservative_all")
        projection = binding["dependency_lock_projection"]
        self.assertTrue(projection["coarse_automation_fence"])
        self.assertIn("unattributable_coverage_failure",
                      projection["coarse_automation_fence_reason_codes"])
        self.assertEqual(self.lock_modes(plan)["reload:automation"], LockMode.EXCLUSIVE)
        self.assertEqual(self.decision(plan)[0], "elevated_admin")

    async def test_an_unstated_fence_is_invalid_lock_evidence(self):
        plan = (await self.planned("on"))["plan"]
        baseline = deepcopy(self.service._load(plan["plan_id"]).operational.baseline)
        baseline["dependency_risk"]["dependency_lock_projection"].pop(
            "coarse_automation_fence"
        )
        with self.assertRaises(ValueError):
            self.lock_modes(plan, baseline=baseline)


# --------------------------------------------------------------------------
# B. Positive safety evidence and policy markers (options, availability)
# --------------------------------------------------------------------------


def _marker_change(plan: Any, change: dict[str, Any], drop: tuple[str, ...] = ()) -> Any:
    operation = plan.operations[0]
    evidence = tuple(
        {key: value for key, value in {**item, **change}.items() if key not in drop}
        if isinstance(item, dict) and "trigger" in item else item
        for item in operation.risk.evidence
    )
    tampered = deepcopy(plan)
    tampered.operations = [
        replace(operation, risk=replace(operation.risk, evidence=evidence))
    ]
    return tampered


class HelperOptionsLabelTests(HelperOptionsServiceTestCase):
    async def options_plan(self, snapshot: Any = None) -> dict[str, Any]:
        entity_id = self.add_template_sensor()
        if snapshot is not None:
            self.snapshot = snapshot(entity_id)
        return await self.create_options(
            {"entity_id": entity_id, "set": {"state": "{{ 3 }}"}}
        )

    async def test_a_feed_into_a_safety_critical_automation_is_safety_critical(self):
        created = await self.options_plan(lambda entity_id: exposure_snapshot(
            findings=(finding(entity_id, "unlock_door"),),
            profiles=(automation_profile("unlock_door", "safety_critical"),),
        ))
        self.assertEqual(created["policy_decision"]["policy_class"], "elevated_admin")
        self.assertEqual(created["policy_decision"]["physical_consequence"],
                         "safety_critical")
        self.assertIn("feeds_protected_automation:unlock_door",
                      self.options_disclosure(created)["protection"]["dependency_reasons"])
        self.assertTrue(created["approval_actionable"])

    async def test_a_protected_object_is_safety_critical(self):
        entity_id = self.add_template_binary_sensor()
        self.ws.entries[entity_id]["original_device_class"] = "smoke"
        created = await self.create_options(
            {"entity_id": entity_id, "set": {"state": "{{ false }}"}}
        )
        reasons = self.options_disclosure(created)["protection"]["dependency_reasons"]
        self.assertIn(f"protected_object:{entity_id}:safety_sensor_smoke", reasons)
        self.assertEqual(created["policy_decision"]["physical_consequence"],
                         "safety_critical")
        self.assertTrue(created["approval_actionable"])

    async def test_a_proposed_protected_object_is_safety_critical(self):
        entity_id = self.add_template_binary_sensor()
        created = await self.create_options(
            {"entity_id": entity_id, "set": {"device_class": "smoke"}}
        )
        reasons = self.options_disclosure(created)["protection"]["dependency_reasons"]
        self.assertIn(f"proposed_protected_object:{entity_id}:safety_sensor_smoke",
                      reasons)
        self.assertEqual(created["policy_decision"]["physical_consequence"],
                         "safety_critical")

    async def test_an_unresolved_consumer_without_positive_evidence_is_unknown(self):
        created = await self.options_plan(lambda entity_id: exposure_snapshot(
            findings=(finding(entity_id, "opaque_effect"),),
        ))
        self.assertEqual(created["policy_decision"]["physical_consequence"], "unknown")
        self.assertIn("unresolved_automation_consequence:opaque_effect",
                      self.options_disclosure(created)["protection"]["dependency_reasons"])

    async def test_a_legacy_marker_keeps_the_safety_critical_rule(self):
        created = await self.options_plan()
        plan = self.repository.get(created["plan_id"])
        legacy = _marker_change(plan, {"physical_consequence": "safety_critical"},
                                drop=("consequence_model",))
        decision = evaluate_change_policy(legacy)
        self.assertEqual(decision.policy_class, ApprovalPolicyClass.ELEVATED_ADMIN)
        self.assertEqual(decision.physical_consequence.value, "safety_critical")
        # A legacy marker cannot claim the new label.
        relabelled = _marker_change(plan, {}, drop=("consequence_model",))
        self.assertEqual(evaluate_change_policy(relabelled).policy_class,
                         ApprovalPolicyClass.PROHIBITED)

    async def test_an_unknown_consequence_model_is_prohibited(self):
        created = await self.options_plan()
        plan = self.repository.get(created["plan_id"])
        self.assertEqual(evaluate_change_policy(plan).policy_class,
                         ApprovalPolicyClass.ELEVATED_ADMIN)
        for value in ("positive_evidence_v2", "", None):
            with self.subTest(consequence_model=value):
                decision = evaluate_change_policy(
                    _marker_change(plan, {"consequence_model": value})
                )
                self.assertEqual(decision.policy_class, ApprovalPolicyClass.PROHIBITED)
                self.assertIn("config_entry_helper_options_contract_incomplete",
                              decision.reason_codes)

    def test_bounded_dependency_reasons_keeps_positive_reasons_past_64(self):
        bounded = getattr(options_planning, "bounded_dependency_reasons", None)
        self.assertIsNotNone(bounded, "bounded_dependency_reasons is not defined")
        others = {f"a_gap:{n:03d}" for n in range(100)}
        positive = {"protected_object:sensor.z:smoke",
                    "proposed_protected_object:sensor.z:gas",
                    "feeds_protected_automation:zz_unlock"}
        kept = bounded(others | positive)
        self.assertEqual(len(kept), options_contract.MAX_DEPENDENCY_REASONS)
        self.assertEqual(kept, sorted(kept))
        self.assertTrue(positive <= set(kept))
        # Each reason is bounded in length, and positive evidence survives that.
        long_positive = "protected_object:" + "x" * 400
        kept = bounded(others | {long_positive})
        self.assertIn(long_positive[: options_contract.MAX_DEPENDENCY_REASON_LENGTH], kept)
        self.assertTrue(all(len(item) <= options_contract.MAX_DEPENDENCY_REASON_LENGTH
                            for item in kept))
        self.assertEqual(options_planning.expected_consequence(kept), "safety_critical")

    async def test_the_plan_binds_consumer_evidence_beyond_the_disclosure_bound(self):
        entity_id = self.add_template_sensor()

        def snapshot(tail: str) -> Any:
            return exposure_snapshot(findings=tuple(
                [finding(entity_id, f"auto_{n:03d}") for n in range(70)]
                + [finding(entity_id, tail)]
            ))

        self.snapshot = snapshot("zz_first")
        created = await self.approved_options(
            {"entity_id": entity_id, "set": {"state": "{{ 3 }}"}}
        )
        stored = self.repository.get(created["plan_id"])
        intent = stored.operations[0].proposed_config[options_contract.INTENT_KEY]
        self.assertEqual(len(intent["dependency_reasons"]),
                         options_contract.MAX_DEPENDENCY_REASONS)
        self.assertNotIn("unresolved_automation_consequence:zz_first",
                         intent["dependency_reasons"])
        # The persisted list cannot see the tail; the bound fingerprint can.
        self.snapshot = snapshot("zz_second")
        evidence = await self.resource_gateway.helper_options.entry_consumer_evidence(
            [entity_id]
        )
        self.assertNotEqual(options_contract.consumer_fingerprint(evidence),
                            intent["consumer_evidence_fingerprint"])
        tampered = deepcopy(stored)
        operation = tampered.operations[0]
        changed = deepcopy(operation.proposed_config)
        changed[options_contract.INTENT_KEY]["consumer_evidence_fingerprint"] = (
            options_contract.consumer_fingerprint(evidence)
        )
        tampered.operations = [replace(operation, proposed_config=changed)]
        self.assertNotEqual(self.service.plan_hash(tampered), created["plan_hash"])
        # Execution rechecks the full evidence and refuses before any submit.
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertIn("dependency_evidence_changed", self.child_codes(result["task_id"]))
        self.assertEqual(self.options_rest.submits(), [])


class AvailabilityLabelTests(AvailabilityExposureServiceTestCase):
    async def disable(self, entity_id: str) -> dict[str, Any]:
        return await self.create(operation="disable", entity_ids=[entity_id])

    async def test_a_feed_into_a_safety_critical_automation_is_safety_critical(self):
        self.add_entity("sensor.hall_motion")
        self.snapshot = exposure_snapshot(
            findings=(finding("sensor.hall_motion", "unlock_on_motion"),),
            profiles=(automation_profile("unlock_on_motion", "safety_critical"),),
        )
        created = await self.disable("sensor.hall_motion")
        self.assertEqual(created["policy_decision"]["policy_class"], "elevated_admin")
        self.assertEqual(created["policy_decision"]["physical_consequence"],
                         "safety_critical")
        disclosure = created["dry_run_results"]["operations"][0]["dry_run_results"][
            "entity_availability_exposure"
        ]
        self.assertIn("feeds_protected_automation:unlock_on_motion",
                      disclosure["protection"]["dependency_reasons"])

    async def test_a_protected_object_is_safety_critical(self):
        self.add_entity("binary_sensor.smoke", original_device_class="smoke")
        created = await self.disable("binary_sensor.smoke")
        self.assertEqual(created["policy_decision"]["physical_consequence"],
                         "safety_critical")
        self.assertIn("disable_protected_object",
                      created["policy_decision"]["reason_codes"])

    async def test_a_known_non_safety_consumer_is_unknown_not_safety_critical(self):
        self.add_entity("sensor.hall_motion")
        self.snapshot = exposure_snapshot(
            findings=(finding("sensor.hall_motion", "porch_light"),),
            profiles=(automation_profile("porch_light", "direct"),),
        )
        created = await self.disable("sensor.hall_motion")
        self.assertNotEqual(created["policy_decision"]["physical_consequence"],
                            "safety_critical")

    async def test_a_legacy_marker_keeps_the_safety_critical_rule(self):
        self.add_entity("sensor.hall_motion")
        self.snapshot = exposure_snapshot(
            findings=(finding("sensor.hall_motion", "unknown_effect"),),
        )
        created = await self.disable("sensor.hall_motion")
        plan = self.repository.get(created["plan_id"])
        legacy = _marker_change(plan, {"physical_consequence": "safety_critical"},
                                drop=("consequence_model",))
        decision = evaluate_change_policy(legacy)
        self.assertEqual(decision.policy_class, ApprovalPolicyClass.ELEVATED_ADMIN)
        self.assertEqual(decision.physical_consequence.value, "safety_critical")
        relabelled = _marker_change(plan, {}, drop=("consequence_model",))
        self.assertEqual(evaluate_change_policy(relabelled).policy_class,
                         ApprovalPolicyClass.PROHIBITED)

    async def test_an_unknown_consequence_model_is_prohibited(self):
        self.add_entity("sensor.hall_motion")
        self.snapshot = exposure_snapshot(
            findings=(finding("sensor.hall_motion", "unknown_effect"),),
        )
        created = await self.disable("sensor.hall_motion")
        plan = self.repository.get(created["plan_id"])
        for value in ("positive_evidence_v2", "", None):
            with self.subTest(consequence_model=value):
                decision = evaluate_change_policy(
                    _marker_change(plan, {"consequence_model": value})
                )
                self.assertEqual(decision.policy_class, ApprovalPolicyClass.PROHIBITED)
                self.assertIn("entity_availability_exposure_execution_contract_incomplete",
                              decision.reason_codes)

    async def test_a_standard_plan_keeps_the_shipped_marker_shape(self):
        # Both rules label a standard change ``none``, so the marker is only
        # written for elevated plans and standard plans keep shipped bytes.
        self.add_entity("sensor.hall_motion")
        self.snapshot = exposure_snapshot()
        created = await self.disable("sensor.hall_motion")
        policy = created["policy_decision"]
        self.assertEqual(
            (policy["policy_class"], policy["risk_delta"],
             policy["physical_consequence"]),
            ("standard_admin", "low", "none"),
        )
        marker = created["risk"]["evidence"][0]
        self.assertNotIn("consequence_model", marker)
        plan = self.repository.get(created["plan_id"])
        self.assertEqual(evaluate_change_policy(plan).policy_class,
                         ApprovalPolicyClass.STANDARD_ADMIN)

    async def test_an_elevated_plan_carries_the_marker(self):
        self.add_entity("sensor.hall_motion")
        self.snapshot = exposure_snapshot(
            findings=(finding("sensor.hall_motion", "unknown_effect"),),
        )
        created = await self.disable("sensor.hall_motion")
        self.assertEqual(created["policy_decision"]["policy_class"], "elevated_admin")
        self.assertEqual(created["risk"]["evidence"][0]["consequence_model"],
                         POSITIVE_EVIDENCE_V1)

    def test_positive_evidence_decides_the_label(self):
        expected = availability_planning.expected_consequence
        gap = "dependency_evidence_incomplete:automation_read_failures"
        self.assertEqual(expected([], []), "none")
        self.assertEqual(expected(["disable_protected_dependency"], [gap]), "unknown")
        self.assertEqual(
            expected(["disable_protected_dependency"],
                     [gap, "feeds_protected_automation:unlock"]),
            "safety_critical",
        )
        self.assertEqual(expected(["disable_protected_object"], []), "safety_critical")


# --------------------------------------------------------------------------
# C. Exact beta.59 v13 records (provenance-backed, hash-bearing bytes untouched)
# --------------------------------------------------------------------------


BETA59_FIXTURES = ROOT / "tests" / "fixtures" / "hamcp285_beta59_input_boolean_v13"
BETA59_PROVENANCE = (
    ROOT / "tests" / "fixtures" / "hamcp285_beta59_input_boolean_v13_provenance.json"
)
BETA59_GENERATOR = (
    ROOT / "scripts" / "generate_hamcp285_beta59_input_boolean_v13_fixture.py"
)
BETA59_COMMIT = "8615ad146a2cf239410708a3bd6254e46c9c8ed0"


class Beta59RecordCase(InputBooleanCase):
    @staticmethod
    def provenance() -> dict[str, Any]:
        return json.loads(BETA59_PROVENANCE.read_text(encoding="utf-8"))

    def restore(self, kind: str) -> dict[str, Any]:
        record = self.provenance()["records"][kind]
        for relative, digest in record["files"].items():
            source = BETA59_FIXTURES / kind / relative
            self.assertEqual(_sha256(source), digest, relative)
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        self.install()
        return record


class ReproductionBeta59ApprovedRecordTests(Beta59RecordCase):
    async def test_the_approved_v13_record_apply_returns_the_structured_replan(self):
        record = self.restore("approved")
        plan_id = record["plan_id"]
        public = self.service.get_plan_observability(plan_id)
        self.assertEqual(public["status"], "approved")
        before = _tree_hashes(self.root)
        try:
            await self.service.apply(plan_id, record["plan_hash"])
        except Exception as exc:  # noqa: BLE001
            raised = exc
        else:
            raised = None
        # Any other outcome, a ValueError or a dispatch included, fails here.
        self.assertIsInstance(raised, GovernanceError, repr(raised))
        self.assertEqual(raised.code, ErrorCode.OPERATIONAL_VALIDATION_FAILED)
        self.assertEqual(raised.details, {"resource_id": plan_id, **SUPERSEDED_REPLAN})
        self.assertEqual(self.helper.dispatch_count, 0, "zero dispatch")
        self.assertEqual(_tree_hashes(self.root), before,
                         "persisted files byte-unchanged")
        self.assertIsNone(self.service.task_repository.get_for_plan(plan_id))
        self.assertEqual(self.service._load(plan_id).approval.state.value, "approved",
                         "approval not consumed")
        self.assertEqual(self.runtime.readiness_state()["faults"], [])


class Beta59RecordTests(Beta59RecordCase):
    def test_provenance_binds_the_exact_beta59_writer(self):
        provenance = self.provenance()
        self.assertEqual(provenance["source"]["commit"], BETA59_COMMIT)
        self.assertEqual(provenance["source"]["tag"], "v2.4.1-beta.59")
        self.assertEqual(provenance["compatibility_model"],
                         "hamcp285-beta59-input-boolean-v13-replan-v1")
        self.assertEqual(provenance["generator_sha256"], _sha256(BETA59_GENERATOR))
        self.assertEqual(set(provenance["records"]), {"pending", "approved"})
        for kind, record in provenance["records"].items():
            with self.subTest(kind=kind):
                self.assertEqual(_tree_hashes(BETA59_FIXTURES / kind), record["files"])
                self.assertEqual(record["dependency_model"], V13)
                self.assertEqual(record["provider_write_count"], 0)
        self.assertEqual(provenance["records"]["pending"]["approval_state"], "required")
        self.assertEqual(provenance["records"]["approved"]["approval_state"], "approved")

    def test_the_stored_bytes_hash_to_the_recorded_plan_hash(self):
        for kind in ("pending", "approved"):
            with self.subTest(kind=kind):
                record = self.restore(kind)
                stored = self.service._load(record["plan_id"])
                self.assertEqual(self.service.plan_hash(stored), record["plan_hash"])
                self.assertEqual(stored.operational.baseline["dependency_risk"]["model"],
                                 V13)

    async def test_the_pending_record_requires_a_replan_and_approve_is_refused(self):
        record = self.restore("pending")
        plan_id = record["plan_id"]
        public = self.service.get_plan_observability(plan_id)
        self.assertTrue(public["helper_dependency_replan_required"])
        self.assertFalse(public["approval_actionable"])
        self.assertEqual(public["next_required_operation"], "create_change_plan")
        with self.assertRaises(GovernanceError) as raised:
            self.service.approve(plan_id, record["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.OPERATIONAL_VALIDATION_FAILED)
        self.assertEqual(raised.exception.details,
                         {"resource_id": plan_id, **SUPERSEDED_REPLAN})
        self.assertEqual(self.helper.dispatch_count, 0)

    def test_the_recovery_lock_graph_equals_the_shipped_v13_graph(self):
        for kind in ("pending", "approved"):
            with self.subTest(kind=kind):
                record = self.restore(kind)
                plan = {"plan_id": record["plan_id"]}
                try:
                    graph = [[key, mode.value] for key, mode in
                             self.lock_modes(plan, readback=True).items()]
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"shipped v13 recovery graph not reconstructed: {exc!r}")
                self.assertEqual(graph, record["shipped_execution_lock_graph"])
                with self.assertRaises(ValueError):
                    self.lock_modes(plan)


# --------------------------------------------------------------------------
# D. End to end on an unreferenced input_boolean, and a helper options apply
# --------------------------------------------------------------------------


class InputBooleanEndToEndTests(InputBooleanCase):
    async def test_apply_reverse_and_no_op_on_an_unreferenced_helper(self):
        forward = (await self.planned("on"))["plan"]
        self.assert_standard(forward)
        await self.grant(forward)
        result = await self.apply(forward)
        self.assertEqual(result["task_state"], "succeeded_verified",
                         self.child(result["task_id"]).to_dict())
        self.assertTrue(result["provider_dispatch_occurred"])
        self.assertEqual(self.helper.dispatched, [(ENTITY, "on")])
        self.assertEqual(self.reads, [False, True],
                         "planning read unfenced, preflight read fenced")

        reverse = (await self.planned("off"))["plan"]
        self.assert_standard(reverse)
        await self.grant(reverse)
        result = await self.apply(reverse)
        self.assertEqual(result["task_state"], "succeeded_verified",
                         self.child(result["task_id"]).to_dict())
        self.assertEqual(self.helper.dispatched, [(ENTITY, "on"), (ENTITY, "off")])
        self.assertEqual(self.helper.state, "off")

        plans_before = self.service.repository.list()
        noop = await self.create("off")
        self.assertEqual(noop["status"], "no_change")
        self.assertEqual(noop["outcome"], "already_in_desired_state")
        self.assertFalse(noop["plan_created"])
        self.assertTrue(noop["verified"])
        self.assertFalse(noop["provider_dispatch_occurred"])
        self.assertEqual(self.service.repository.list(), plans_before)
        self.assertEqual(self.helper.dispatch_count, 2)

    async def test_a_bounded_notify_only_consumer_applies_as_medium_elevated(self):
        plan = (await self.planned("on", extra={"flag_notice": consumer(
            "Flag notice",
            triggers=[{"trigger": "state", "entity_id": ENTITY, "to": "on"}],
            actions=[{"action": "notify.notify", "data": {"message": "x"}}],
        )}))["plan"]
        self.assert_elevated_and_named(plan, "flag_notice", "none")
        self.assertEqual(self.decision(plan), ("elevated_admin", "moderate", "none"))
        self.assertEqual(plan["risk"]["level"], "medium")
        await self.grant(plan)
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified",
                         self.child(result["task_id"]).to_dict())
        self.assertNotIn("policy_snapshot_mismatch",
                         self.child_codes(result["task_id"]))
        self.assertEqual(self.helper.dispatched, [(ENTITY, "on")])


class HelperPolicyExpectationTests(unittest.TestCase):
    def test_the_medium_elevated_family_is_accepted_only_for_helper_values(self):
        medium = (("elevated_admin", "moderate", "none"), "medium")
        for operation in ("set_input_boolean_state", "set_input_select_option"):
            self.assertTrue(operational_policy_expectation_is_valid(operation, *medium))
            # Neighbouring tuples that policy never issues stay invalid.
            for invalid in (
                (("elevated_admin", "moderate", "none"), "high"),
                (("elevated_admin", "moderate", "none"), "low"),
                (("elevated_admin", "moderate", "direct"), "medium"),
                (("standard_admin", "moderate", "none"), "medium"),
            ):
                self.assertFalse(
                    operational_policy_expectation_is_valid(operation, *invalid),
                    invalid,
                )
        self.assertFalse(
            operational_policy_expectation_is_valid("controlled_reload", *medium)
        )


class HelperOptionsEndToEndTests(HelperOptionsServiceTestCase):
    async def test_an_approved_unknown_options_edit_applies_and_verifies(self):
        entity_id = self.add_template_sensor()
        created = await self.approved_options(
            {"entity_id": entity_id, "set": {"state": "{{ 3 }}"}}
        )
        self.assertEqual(created["policy_decision"]["physical_consequence"], "unknown")
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(len(self.options_rest.submits()), 1)
        self.assertEqual(self.options_rest.visible("templateentry01")["state"], "{{ 3 }}")


# --------------------------------------------------------------------------
# E. input_select is unchanged
# --------------------------------------------------------------------------


class InputSelectUnchangedTests(unittest.TestCase):
    def test_the_input_select_model_is_unchanged(self):
        self.assertEqual(input_select_dependency.INPUT_SELECT_DEPENDENCY_MODEL,
                         INPUT_SELECT_V2)

    def test_the_input_boolean_model_is_v14_and_the_generic_model_v13(self):
        self.assertEqual(getattr(helper_module, "INPUT_BOOLEAN_DEPENDENCY_MODEL", None),
                         V14)
        self.assertEqual(helper_module.HELPER_DEPENDENCY_RISK_MODEL, V13)


# --------------------------------------------------------------------------
# F. Response size
# --------------------------------------------------------------------------


class InputBooleanResponseSizeTests(InputBooleanCase):
    async def test_an_over_budget_plan_response_is_bounded_with_counts(self):
        created = await self.planned("on", extra=over_budget_consumers(120))
        self.assertLess(self.chars(created), RESPONSE_LIMIT)
        plan = created["plan"]
        paging = plan["response_paging"]
        self.assertTrue(paging["response_compacted"])
        self.assertEqual(paging["paging_tool"], "get_change_plan")
        self.assertEqual(paging["plan_id"], plan["plan_id"])
        profiles = paging["omitted_sections"]["downstream_profiles"]
        self.assertTrue(profiles["truncated"])
        self.assertEqual(profiles["returned_count"], 0)
        stored = self.stored_binding(plan)
        self.assertTrue(stored["relevant_consumers_truncated"])
        self.assertEqual(len(stored["downstream_profiles"]), profiles["total_count"])
        self.assertEqual(plan["plan_hash"],
                         self.service.plan_hash(self.service._load(plan["plan_id"])))
        page = self.service.get_plan_observability(
            plan["plan_id"], detail_section="downstream_profiles",
        )
        self.assertLess(self.chars(page), RESPONSE_LIMIT)


class HelperOptionsResponseSizeTests(HelperOptionsServiceTestCase):
    async def test_many_consumers_and_reasons_stay_bounded(self):
        entity_id = self.add_template_sensor()
        self.snapshot = exposure_snapshot(
            findings=tuple(finding(entity_id, f"auto_{n:03d}") for n in range(200))
            + (finding(entity_id, "zz_unlock"),),
            profiles=(automation_profile("zz_unlock", "safety_critical"),),
        )
        created = await self.create_options(
            {"entity_id": entity_id, "set": {"state": "{{ 3 }}"}}
        )
        self.assertLess(_chars(created), RESPONSE_LIMIT)
        disclosure = self.options_disclosure(created)
        self.assertTrue(disclosure["consumers"]["truncated"])
        self.assertEqual(disclosure["consumers"]["count"], 201)
        self.assertLess(len(disclosure["consumers"]["items"]), 201)
        reasons = disclosure["protection"]["dependency_reasons"]
        self.assertEqual(len(reasons), options_contract.MAX_DEPENDENCY_REASONS)
        self.assertIn("feeds_protected_automation:zz_unlock", reasons)
        self.assertEqual(created["policy_decision"]["physical_consequence"],
                         "safety_critical")


class AvailabilityResponseSizeTests(AvailabilityExposureServiceTestCase):
    async def test_many_consumers_and_reasons_stay_bounded(self):
        self.add_entity("sensor.hall_motion")
        self.snapshot = exposure_snapshot(
            findings=tuple(finding("sensor.hall_motion", f"auto_{n:03d}")
                           for n in range(200)),
            read_failures=(SimpleNamespace(source_id="broken_automation"),),
        )
        created = await self.create(operation="disable",
                                    entity_ids=["sensor.hall_motion"])
        self.assertLess(_chars(created), RESPONSE_LIMIT)
        disclosure = created["dry_run_results"]["operations"][0]["dry_run_results"][
            "entity_availability_exposure"
        ]
        self.assertTrue(disclosure["consumers"]["truncated"])
        self.assertEqual(disclosure["consumers"]["count"], 200)
        self.assertLess(len(disclosure["consumers"]["items"]), 200)
        self.assertEqual(created["policy_decision"]["physical_consequence"], "unknown")


if __name__ == "__main__":
    unittest.main()
