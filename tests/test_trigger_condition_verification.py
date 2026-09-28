"""Synthetic F028 regressions: exact trigger guards and pre-dispatch proof."""

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3.executor import SharedOperationExecutor
from ha_mcp_engineering.f3.locks import DurableLockStore
from ha_mcp_engineering.f3.models import ExecutionIdentity
from ha_mcp_engineering.f3.persistence import DurableExecutionRepository
from ha_mcp_engineering.f3_configuration.sequence import (
    prepare_configuration_sequence,
    single_operation_child_descriptor,
)
from ha_mcp_engineering.governance.normalize import (
    normalize_automation,
    normalize_automation_for_verification,
)
from ha_mcp_engineering.governance.resources import compare_resource_verification
from tests.f3_configuration_fixtures import (
    SyntheticConfigurationGateway,
    adapter_for,
    proposal_for,
)
from tests.f3_synthetic_adapter import SyntheticApprovalRecorder
from tests.test_f3_configuration_lifecycle import (
    EXECUTOR_TIMING,
    LOCK_TIMING,
    FakeClock,
)


def synthetic_config(ids):
    """No household configuration; retain the reported nested-default shape."""
    return {
        "id": "porch_light",
        "alias": "Synthetic trigger guard",
        "triggers": [
            {"trigger": "homeassistant", "event": "start", "id": "synthetic_start"}
        ],
        "actions": [{
            "choose": [],
            "default": [
                {"condition": "trigger", "id": deepcopy(ids)},
                {"condition": "state", "entity_id": "input_boolean.synthetic", "state": "on"},
                {"stop": "Synthetic test only"},
            ],
        }],
        "mode": "queued",
        "max": 10,
    }


class TriggerConditionComparisonTests(unittest.TestCase):
    def test_scalar_and_list_ids_verify_without_changing_binding_or_shape(self):
        for ids in ("synthetic_start", ["synthetic_start", "synthetic_reconcile"], [], "", ["", " spaced "]):
            with self.subTest(ids=ids):
                config = synthetic_config(ids)
                before = deepcopy(config)
                result = compare_resource_verification("automation", config, config)
                self.assertTrue(result.normalization_valid)
                self.assertTrue(result.semantic_match)
                normalized, categories = normalize_automation_for_verification(config)
                self.assertEqual(normalized, normalize_automation(config))
                self.assertEqual(normalized["action"][0]["default"][0]["id"], ids)
                self.assertEqual(categories, ())
                self.assertEqual(config, before)

    def test_supported_nested_action_positions_preserve_trigger_ids(self):
        guard = {"condition": "trigger", "id": ["synthetic_start"]}
        variants = (
            [guard],
            [{"choose": [{"conditions": [], "sequence": [guard]}], "default": [guard]}],
            [{"if": [], "then": [guard], "else": [guard]}],
            [{"repeat": {"count": 2, "sequence": [guard]}}],
            [{"parallel": [{"sequence": [guard]}]}],
        )
        for actions in variants:
            with self.subTest(actions=actions):
                config = synthetic_config("synthetic_start")
                config["actions"] = deepcopy(actions)
                result = compare_resource_verification("automation", config, config)
                self.assertTrue(result.normalization_valid)
                self.assertTrue(result.semantic_match)

    def test_behavioral_guard_differences_never_compare_equal(self):
        approved = synthetic_config(["synthetic_start", "synthetic_reconcile"])
        variants = []
        for ids in (["synthetic_start"], ["synthetic_other", "synthetic_reconcile"], ["synthetic_reconcile", "synthetic_start"], "synthetic_start"):
            changed = deepcopy(approved)
            changed["actions"][0]["default"][0]["id"] = ids
            variants.append(changed)
        for mutation in ("drop_trigger", "drop_state", "change_state", "disable_trigger", "enable_error_continuation", "change_trigger_definition"):
            changed = deepcopy(approved)
            actions = changed["actions"][0]["default"]
            if mutation == "drop_trigger":
                actions.pop(0)
            elif mutation == "drop_state":
                actions.pop(1)
            elif mutation == "change_state":
                actions[1]["state"] = "off"
            elif mutation == "disable_trigger":
                actions[0]["enabled"] = False
            elif mutation == "enable_error_continuation":
                actions[0]["continue_on_error"] = True
            else:
                changed["triggers"][0]["id"] = "synthetic_other"
            variants.append(changed)
        for observed in variants:
            with self.subTest(observed=observed):
                result = compare_resource_verification("automation", approved, observed)
                self.assertTrue(result.normalization_valid)
                self.assertFalse(result.semantic_match)

    def test_malformed_missing_or_unknown_trigger_fields_fail_closed(self):
        guards = [
            {"condition": "trigger"},
            *({"condition": "trigger", "id": ids} for ids in (None, 4, True, {}, [None], [4], [True], [[]], [{}])),
            {"condition": "trigger", "id": "synthetic_start", "state": "on"},
            {"condition": "trigger", "id": "synthetic_start", "future_field": "private synthetic marker"},
            {"condition": "state", "id": "synthetic_start", "state": "on"},
        ]
        for guard in guards:
            with self.subTest(guard=guard):
                config = synthetic_config("synthetic_start")
                config["actions"][0]["default"][0] = guard
                result = compare_resource_verification("automation", config, config)
                self.assertFalse(result.normalization_valid)
                self.assertFalse(result.semantic_match)
                self.assertEqual(result.mismatch_categories, ("automation_verification_structure",))

    def test_category_and_service_alias_treatment_is_preserved(self):
        approved = synthetic_config("synthetic_start")
        approved["actions"][0]["default"].append({"service": "notify.synthetic", "data": {"message": "synthetic"}})
        observed = deepcopy(approved)
        observed["category"] = "synthetic_registry_category"
        step = observed["actions"][0]["default"][-1]
        step["action"] = step.pop("service")
        result = compare_resource_verification("automation", approved, observed)
        self.assertTrue(result.semantic_match)
        self.assertTrue(result.normalization_valid)

    def test_scalar_is_not_silently_equated_with_singleton_list(self):
        result = compare_resource_verification("automation", synthetic_config("synthetic_start"), synthetic_config(["synthetic_start"]))
        self.assertTrue(result.normalization_valid)
        self.assertFalse(result.semantic_match)


class TriggerConditionDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, proposed, *, action="update", mutate=None, duplicate=False, expected_preflight_code=None):
        current = synthetic_config("synthetic_previous") if action == "update" else None
        proposal = proposal_for("automation", action, current_config=current, proposed_config=proposed)
        states = {("automation", "porch_light"): current} if current else {}
        gateway = SyntheticConfigurationGateway(states)
        if mutate:
            gateway.after_write_hook = lambda gateway, kind, target: mutate(gateway.states[(kind, target)])
        adapter = adapter_for("automation", action, gateway)
        prepared = await adapter.prepare(proposal)
        if expected_preflight_code is not None:
            preflight = await adapter.preflight(
                prepared, acquired_locks=adapter.lock_requests(prepared)
            )
            self.assertFalse(preflight.eligible)
            self.assertIn(expected_preflight_code, preflight.diagnostic_codes)
        child = single_operation_child_descriptor(prepare_configuration_sequence((prepared,)))
        identity = ExecutionIdentity(task_id=child.public_task_id, plan_id=child.plan_id, attempt_id=child.attempt_id, request_id="synthetic-f028-request", owner_id="synthetic-f028-owner")
        approval = SyntheticApprovalRecorder()
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executor = SharedOperationExecutor(lock_store=DurableLockStore(root), execution_repository=DurableExecutionRepository(root), lock_timing=LOCK_TIMING, executor_timing=EXECUTOR_TIMING, now=clock.now, monotonic=clock.monotonic, sleep=clock.sleep)
            first = await executor.execute(adapter=adapter, prepared=prepared, identity=identity, approval_consumption=approval)
            second = await executor.execute(adapter=adapter, prepared=prepared, identity=identity, approval_consumption=approval) if duplicate else None
        return first, second, gateway, approval

    async def test_valid_guards_create_and_update_verify_once_and_suppress_duplicate(self):
        for action in ("create", "update"):
            for ids in ("synthetic_start", ["synthetic_start", "synthetic_reconcile"]):
                with self.subTest(action=action, ids=ids):
                    first, second, gateway, approval = await self._run(synthetic_config(ids), action=action, duplicate=True)
                    self.assertEqual(first.outcome, "succeeded_verified")
                    self.assertEqual(second.outcome, "succeeded_verified")
                    self.assertTrue(second.duplicate_execution)
                    self.assertEqual(gateway.counters.dispatches, 1)
                    self.assertEqual(gateway.counters.simulated_mutations, 1)
                    self.assertEqual(approval.consumptions, 1)

    async def test_unverifiable_candidate_refuses_before_approval_or_mutation(self):
        for action in ("create", "update"):
            for guard in ({"condition": "trigger", "id": {"unexpected": "synthetic"}}, {"condition": "trigger", "id": "synthetic_start", "future_field": "private synthetic marker"}, {"future_directive": "synthetic"}):
                with self.subTest(action=action, guard=guard):
                    config = synthetic_config("synthetic_start")
                    config["actions"][0]["default"][0] = guard
                    first, _, gateway, approval = await self._run(
                        config, action=action,
                        expected_preflight_code="configuration_not_verifiable",
                    )
                    self.assertEqual(first.outcome, "preflight_rejected")
                    self.assertNotIn("private synthetic marker", str(first))
                    self.assertEqual(approval.invocations, 0)
                    self.assertEqual(approval.consumptions, 0)
                    self.assertEqual(gateway.counters.dispatches, 0)
                    self.assertEqual(gateway.counters.simulated_mutations, 0)
                    self.assertEqual(gateway.counters.reads, 0)
                    self.assertEqual(gateway.counters.validation_calls, 2)

    async def test_changed_saved_guard_remains_failed_without_redispatch(self):
        def change(config):
            config["actions"][0]["default"][0]["id"] = "synthetic_other"
        first, second, gateway, approval = await self._run(synthetic_config("synthetic_start"), mutate=change, duplicate=True)
        self.assertEqual(first.outcome, "verification_mismatch")
        self.assertEqual(second.outcome, "verification_mismatch")
        self.assertTrue(second.duplicate_execution)
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(approval.consumptions, 1)


if __name__ == "__main__":
    unittest.main()
