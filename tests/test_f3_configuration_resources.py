"""Resource-specific F3-C1 configuration strategy acceptance."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from f3_contracts.operation_adapter import NormalizedOperationOutcome
from ha_mcp_engineering.f3_configuration.strategies import strategy_for

from tests.f3_configuration_fixtures import (
    ConfigurationLifecycleHarness,
    SyntheticConfigurationGateway,
    adapter_for,
    proposal_for,
    target_id,
    valid_config,
)


class ResourceConformanceTestCase(unittest.IsolatedAsyncioTestCase):
    async def ready(
        self,
        resource_type: str,
        action: str,
        *,
        gateway: SyntheticConfigurationGateway | None = None,
        proposal=None,
    ):
        proposal = proposal or proposal_for(resource_type, action)
        gateway = gateway or SyntheticConfigurationGateway()
        if action == "update" and not gateway.states:
            gateway.states[(resource_type, proposal.target_id)] = (
                proposal.current_config()
            )
        adapter = adapter_for(resource_type, action, gateway)
        prepared = await adapter.prepare(proposal)
        locks = adapter.lock_requests(prepared)
        preflight = await adapter.preflight(
            prepared, acquired_locks=locks
        )
        return proposal, gateway, adapter, prepared, preflight

    async def assert_success(self, resource_type: str, action: str):
        proposal, gateway, adapter, prepared, preflight = await self.ready(
            resource_type, action
        )
        self.assertTrue(preflight.eligible)
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        observation = await harness.observe(prepared, dispatch)
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
        )
        self.assertTrue(verification.verified)
        self.assertEqual(harness.intent_commits, 1)
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(gateway.counters.simulated_mutations, 1)
        self.assertEqual(
            gateway.states[(resource_type, proposal.target_id)]["id"],
            (
                proposal.target_id.split(".", 1)[1]
                if resource_type in {"input_boolean", "input_number"}
                else proposal.target_id
            ),
        )
        return proposal, gateway, adapter, prepared

    async def assert_duplicate_create_rejected(self, resource_type: str):
        proposal = proposal_for(resource_type, "create")
        existing = proposal.proposed_config()
        existing["id"] = (
            proposal.target_id.split(".", 1)[1]
            if resource_type in {"input_boolean", "input_number"}
            else proposal.target_id
        )
        gateway = SyntheticConfigurationGateway(
            {(resource_type, proposal.target_id): existing}
        )
        adapter = adapter_for(resource_type, "create", gateway)
        prepared = await adapter.prepare(proposal)
        result = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        self.assertFalse(result.eligible)
        self.assertIn("target_already_exists", result.diagnostic_codes)
        self.assertEqual(gateway.counters.dispatches, 0)
        self.assertEqual(gateway.counters.simulated_mutations, 0)

    async def assert_missing_update_rejected(self, resource_type: str):
        proposal = proposal_for(resource_type, "update")
        gateway = SyntheticConfigurationGateway()
        adapter = adapter_for(resource_type, "update", gateway)
        prepared = await adapter.prepare(proposal)
        result = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        self.assertFalse(result.eligible)
        self.assertIn("update_target_missing", result.diagnostic_codes)
        self.assertEqual(gateway.counters.dispatches, 0)

    async def assert_stale_update_rejected(self, resource_type: str):
        proposal = proposal_for(resource_type, "update")
        stale = valid_config(resource_type, updated=True)
        stale["id"] = (
            proposal.target_id.split(".", 1)[1]
            if resource_type in {"input_boolean", "input_number"}
            else proposal.target_id
        )
        gateway = SyntheticConfigurationGateway(
            {(resource_type, proposal.target_id): stale}
        )
        adapter = adapter_for(resource_type, "update", gateway)
        prepared = await adapter.prepare(proposal)
        result = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        self.assertFalse(result.eligible)
        self.assertIn("stale_target_state", result.diagnostic_codes)
        self.assertEqual(gateway.counters.dispatches, 0)


class AutomationStrategyTests(ResourceConformanceTestCase):
    async def test_automation_create_and_update_verify_exact_readback(self):
        for action in ("create", "update"):
            with self.subTest(action=action):
                await self.assert_success("automation", action)

    async def test_automation_duplicate_missing_and_stale_targets_reject_pre_dispatch(self):
        await self.assert_duplicate_create_rejected("automation")
        await self.assert_missing_update_rejected("automation")
        await self.assert_stale_update_rejected("automation")

    async def test_invalid_trigger_condition_and_action_fail_full_validation(self):
        invalid_configs = (
            {"trigger": "bad", "condition": [], "action": []},
            {"trigger": [], "condition": "bad", "action": []},
            {"trigger": [], "condition": [], "action": "bad"},
        )
        for proposed in invalid_configs:
            with self.subTest(proposed=proposed):
                proposal = proposal_for(
                    "automation", "create", proposed_config=proposed
                )
                gateway = SyntheticConfigurationGateway()
                gateway.validation_result = {
                    "result": "invalid",
                    "errors": "synthetic invalid automation",
                }
                adapter = adapter_for("automation", "create", gateway)
                prepared = await adapter.prepare(proposal)
                result = await adapter.preflight(
                    prepared,
                    acquired_locks=adapter.lock_requests(prepared),
                )
                self.assertFalse(result.eligible)
                self.assertIn(
                    "configuration_validation_failed",
                    result.diagnostic_codes,
                )
                self.assertEqual(gateway.counters.dispatches, 0)

    async def test_automation_readback_service_action_alias_is_semantically_equal(self):
        proposal, gateway, adapter, prepared, preflight = await self.ready(
            "automation", "update"
        )

        def canonicalize_action(gateway, resource_type, target):
            step = gateway.states[(resource_type, target)]["action"][0]
            step["action"] = step.pop("service")

        gateway.after_write_hook = canonicalize_action
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        observation = await harness.observe(prepared, dispatch)
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
        )
        self.assertEqual(gateway.counters.dispatches, 1)

    async def test_automation_post_write_configuration_check_failure_is_not_success(self):
        proposal, gateway, adapter, prepared, preflight = await self.ready(
            "automation", "update"
        )

        def invalidate(gateway, _resource_type, _target):
            gateway.validation_result = {
                "result": "invalid",
                "errors": "synthetic post-write failure",
            }

        gateway.after_write_hook = invalidate
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        observation = await harness.observe(prepared, dispatch)
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.VERIFICATION_MISMATCH,
        )
        self.assertIn("configuration_check", verification.mismatch_fields)
        self.assertEqual(gateway.counters.simulated_mutations, 1)

    async def test_automation_fixtures_are_synthetic_and_inert(self):
        proposal, gateway, _adapter, _prepared = await self.assert_success(
            "automation", "create"
        )
        self.assertEqual(proposal.target_id, "porch_light")
        self.assertEqual(gateway.counters.simulated_mutations, 1)
        self.assertNotIn("production", repr(gateway.states).lower())

    async def test_contract_v2_automation_rollback_remains_unavailable(self):
        _proposal, _gateway, adapter, prepared, _preflight = await self.ready(
            "automation", "update"
        )
        self.assertFalse(prepared.rollback_available)
        self.assertIsNone(
            await adapter.prepare_rollback(
                prepared,
                expected_current_fingerprint=prepared.normalized_proposed_hash,
            )
        )


class ScriptStrategyTests(ResourceConformanceTestCase):
    async def test_script_create_and_update_verify_sequence_and_mode(self):
        for action in ("create", "update"):
            with self.subTest(action=action):
                proposal, gateway, _adapter, _prepared = await self.assert_success(
                    "script", action
                )
                observed = gateway.states[("script", proposal.target_id)]
                self.assertEqual(observed["mode"], "single")
                self.assertEqual(len(observed["sequence"]), 1)

    async def test_script_duplicate_missing_and_stale_targets_reject_pre_dispatch(self):
        await self.assert_duplicate_create_rejected("script")
        await self.assert_missing_update_rejected("script")
        await self.assert_stale_update_rejected("script")

    async def test_script_invalid_sequence_and_mode_fail_static_validation(self):
        strategy = strategy_for("script", "create")
        for config in (
            {"alias": "Bad", "sequence": [], "mode": "single"},
            {"alias": "Bad", "sequence": [{}], "mode": "unknown"},
            {"alias": "Bad", "sequence": "service", "mode": "single"},
        ):
            with self.subTest(config=config):
                valid, errors, _ = strategy.validate("notify_house", config)
                self.assertFalse(valid)
                self.assertTrue(errors)

    async def test_script_readback_does_not_apply_automation_only_alias_rules(self):
        proposal, gateway, adapter, prepared, preflight = await self.ready(
            "script", "update"
        )

        def rewrite(gateway, resource_type, target):
            step = gateway.states[(resource_type, target)]["sequence"][0]
            step["action"] = step.pop("service")

        gateway.after_write_hook = rewrite
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        observation = await harness.observe(prepared, dispatch)
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.VERIFICATION_MISMATCH,
        )
        self.assertEqual(gateway.counters.dispatches, 1)


class InputBooleanStrategyTests(ResourceConformanceTestCase):
    async def test_input_boolean_create_and_update_preserve_name_icon_initial(self):
        for action in ("create", "update"):
            with self.subTest(action=action):
                proposal, gateway, _adapter, _prepared = await self.assert_success(
                    "input_boolean", action
                )
                observed = gateway.states[("input_boolean", proposal.target_id)]
                self.assertEqual(observed["icon"], "mdi:toggle-switch")
                self.assertIs(observed["initial"], False)
                self.assertIsInstance(observed["name"], str)

    async def test_input_boolean_duplicate_missing_and_stale_targets_reject_pre_dispatch(self):
        await self.assert_duplicate_create_rejected("input_boolean")
        await self.assert_missing_update_rejected("input_boolean")
        await self.assert_stale_update_rejected("input_boolean")

    def test_input_boolean_null_and_omitted_field_behavior_matches_existing_schema(self):
        strategy = strategy_for("input_boolean", "create")
        valid, errors, _ = strategy.validate(
            "input_boolean.vacation_mode", {"name": "Vacation Mode"}
        )
        self.assertTrue(valid, errors)
        for field in ("name", "icon", "initial"):
            config = {"name": "Vacation Mode", field: None}
            with self.subTest(field=field):
                valid, errors, _ = strategy.validate(
                    "input_boolean.vacation_mode", config
                )
                self.assertFalse(valid)
                self.assertTrue(errors)

    async def test_input_boolean_wrong_helper_type_never_reaches_dispatch(self):
        proposal = proposal_for("input_boolean", "create")
        altered = proposal.__class__(
            **{**proposal.__dict__, "target_id": "input_number.vacation_mode"}
        )
        adapter = adapter_for(
            "input_boolean", "create", SyntheticConfigurationGateway()
        )
        with self.assertRaises(ValueError):
            await adapter.prepare(altered)


class InputNumberStrategyTests(ResourceConformanceTestCase):
    async def test_input_number_create_and_update_verify_exact_helper_type(self):
        for action in ("create", "update"):
            with self.subTest(action=action):
                proposal, gateway, _adapter, _prepared = await self.assert_success(
                    "input_number", action
                )
                observed = gateway.states[("input_number", proposal.target_id)]
                self.assertEqual(observed["id"], "target_temperature")

    async def test_input_number_duplicate_missing_and_stale_targets_reject_pre_dispatch(self):
        await self.assert_duplicate_create_rejected("input_number")
        await self.assert_missing_update_rejected("input_number")
        await self.assert_stale_update_rejected("input_number")

    def test_input_number_min_max_step_initial_and_mode_validation_is_exact(self):
        strategy = strategy_for("input_number", "create")
        invalid = (
            {**valid_config("input_number"), "min": 30, "max": 10},
            {**valid_config("input_number"), "step": 0},
            {**valid_config("input_number"), "step": 100},
            {**valid_config("input_number"), "initial": 31},
            {**valid_config("input_number"), "mode": "dial"},
        )
        for config in invalid:
            with self.subTest(config=config):
                valid, errors, _ = strategy.validate(
                    "input_number.target_temperature", config
                )
                self.assertFalse(valid)
                self.assertTrue(errors)

    def test_input_number_integer_and_float_normalization_are_equivalent(self):
        strategy = strategy_for("input_number", "update")
        integer = valid_config("input_number", updated=True)
        floating = deepcopy(integer)
        for field in ("min", "max", "initial"):
            floating[field] = float(floating[field])
        self.assertEqual(strategy.normalize(integer), strategy.normalize(floating))
        self.assertEqual(strategy.fingerprint(integer), strategy.fingerprint(floating))


if __name__ == "__main__":
    unittest.main()
