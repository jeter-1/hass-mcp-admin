"""Beta 34 automation verification truthfulness regressions."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3.contracts import (  # noqa: E402
    NormalizedOperationOutcome,
)
from ha_mcp_engineering.errors import HomeAssistantApiError  # noqa: E402
from ha_mcp_engineering.f3.executor import (  # noqa: E402
    SharedOperationExecutor,
)
from ha_mcp_engineering.f3.locks import DurableLockStore  # noqa: E402
from ha_mcp_engineering.f3.models import (  # noqa: E402
    ExecutionIdentity,
    ExecutorTiming,
    LockTiming,
)
from ha_mcp_engineering.f3.persistence import (  # noqa: E402
    DurableExecutionRepository,
)
from ha_mcp_engineering.f3_configuration.sequence import (  # noqa: E402
    prepare_configuration_sequence,
    single_operation_child_descriptor,
)
from ha_mcp_engineering.governance.normalize import (  # noqa: E402
    AUTOMATION_NORMALIZATION_VERSION,
    MAX_AUTOMATION_TEMPLATE_LENGTH,
    normalize_automation,
    normalize_automation_for_verification,
)
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    compare_resource_verification,
)
from tests.f3_configuration_fixtures import (  # noqa: E402
    ConfigurationLifecycleHarness,
    FIXED_NOW,
    SyntheticConfigurationGateway,
    adapter_for,
    proposal_for,
    recovery_context,
)
from tests.f3_synthetic_adapter import (  # noqa: E402
    SyntheticApprovalRecorder,
)


GARAGE_AUTOMATION_ID = "porch_light"
GARAGE_TARGET = "cover.garage_door"
FP300_OCCUPANCY = (
    "binary_sensor.garage_fp300_presence_sensor_occupancy"
)
GARAGE_CATEGORY = "garage"
GARAGE_WAIT_TEMPLATE = """{{ is_state('cover.garage_door', 'open')
   and is_state('binary_sensor.garage_side_door', 'off')
   and is_state('binary_sensor.garage_obstruction', 'off')
   and is_state('binary_sensor.garage_fp300_presence_sensor_occupancy', 'off')
   and (as_timestamp(now()) - as_timestamp(states.cover.garage_door.last_changed)) > 600 }}"""
LOCK_TIMING = LockTiming(60, 10, 0)
EXECUTOR_TIMING = ExecutorTiming(120, 60, 3, 3)


class FakeClock:
    def __init__(self) -> None:
        self.wall = FIXED_NOW
        self.monotonic_value = 0.0

    def now(self):
        return self.wall

    def monotonic(self) -> float:
        return self.monotonic_value

    async def sleep(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.monotonic_value += seconds


def garage_config(*, include_fp300_guard: bool) -> dict:
    conditions = [
        {
            "condition": "state",
            "entity_id": GARAGE_TARGET,
            "state": "open",
        },
        {
            "condition": "state",
            "entity_id": "binary_sensor.garage_side_door",
            "state": "off",
        },
        {
            "condition": "state",
            "entity_id": "binary_sensor.garage_obstruction",
            "state": "off",
        },
        {
            "condition": "state",
            "entity_id": "input_boolean.garage_auto_close",
            "state": "on",
        },
    ]
    if include_fp300_guard:
        conditions.append(
            {
                "condition": "state",
                "entity_id": FP300_OCCUPANCY,
                "state": "off",
            }
        )
    return {
        "id": GARAGE_AUTOMATION_ID,
        "alias": "Garage door auto close",
        "description": "Close only after the existing guarded wait",
        "triggers": [
            {
                "trigger": "state",
                "entity_id": GARAGE_TARGET,
                "to": "open",
                "for": "00:10:00",
            }
        ],
        "conditions": conditions,
        "actions": [
            {
                "wait_template": GARAGE_WAIT_TEMPLATE,
                "timeout": "00:05:00",
                "continue_on_timeout": False,
            },
            {
                "action": "cover.close_cover",
                "target": {"entity_id": GARAGE_TARGET},
            },
        ],
        "mode": "single",
    }


class Beta34GarageVerificationRegressionTests(
    unittest.IsolatedAsyncioTestCase
):
    async def _eligible_case(self):
        current = garage_config(include_fp300_guard=False)
        proposed = garage_config(include_fp300_guard=True)
        proposed["category"] = GARAGE_CATEGORY
        proposal = proposal_for(
            "automation",
            "update",
            current_config=current,
            proposed_config=proposed,
        )
        gateway = SyntheticConfigurationGateway(
            {("automation", GARAGE_AUTOMATION_ID): current}
        )
        adapter = adapter_for("automation", "update", gateway)
        prepared = await adapter.prepare(proposal)
        preflight = await adapter.preflight(
            prepared,
            acquired_locks=adapter.lock_requests(prepared),
        )
        self.assertTrue(preflight.eligible, preflight.diagnostic_codes)
        return proposal, gateway, adapter, prepared, preflight

    @staticmethod
    def _enrich_readback(gateway, resource_type, target_id):
        gateway.states[(resource_type, target_id)][
            "category"
        ] = GARAGE_CATEGORY

    async def _dispatch_with_readback_change(self, change=None):
        _proposal, gateway, adapter, prepared, preflight = (
            await self._eligible_case()
        )

        def after_write(gateway, resource_type, target_id):
            self._enrich_readback(gateway, resource_type, target_id)
            if change is not None:
                change(gateway.states[(resource_type, target_id)])

        gateway.after_write_hook = after_write
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared,
            preflight,
            before_dispatch=harness.intent,
        )
        observed = gateway.states[("automation", GARAGE_AUTOMATION_ID)]
        comparison = compare_resource_verification(
            "automation",
            prepared.proposed_config(),
            deepcopy(observed),
        )
        observation = await harness.observe(prepared, dispatch)
        verification = await harness.verify(prepared, observation)
        return (
            gateway,
            dispatch,
            comparison,
            observation,
            verification,
        )

    async def test_live_shaped_authoritative_readback_verifies(self):
        self.assertGreater(len(GARAGE_WAIT_TEMPLATE), 200)
        (
            gateway,
            _dispatch,
            comparison,
            observation,
            verification,
        ) = await self._dispatch_with_readback_change()

        self.assertTrue(comparison.normalization_valid)
        self.assertTrue(comparison.semantic_match)
        self.assertEqual(
            comparison.normalized_approved_fingerprint,
            comparison.normalized_observed_fingerprint,
        )
        self.assertEqual(observation.mismatch_fields, ())
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
        )
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(gateway.counters.simulated_mutations, 1)

    async def test_behavioral_drift_remains_an_exact_mismatch(self):
        def action_changed(config):
            config["actions"][1]["action"] = "cover.open_cover"

        def target_changed(config):
            config["actions"][1]["target"]["entity_id"] = (
                "cover.detached_garage_door"
            )

        def guard_omitted(config):
            config["conditions"].pop()

        def guard_value_changed(config):
            config["conditions"][-1]["state"] = "on"

        def trigger_changed(config):
            config["triggers"][0]["to"] = "closed"

        def mode_changed(config):
            config["mode"] = "restart"

        def wait_template_changed(config):
            config["actions"][0]["wait_template"] += "\n{# drift #}"

        cases = (
            ("safety_action", action_changed, "actions"),
            ("target", target_changed, "actions"),
            ("guard_omitted", guard_omitted, "conditions"),
            ("guard_value", guard_value_changed, "conditions"),
            ("trigger", trigger_changed, "triggers"),
            ("mode", mode_changed, "mode"),
            ("wait_template", wait_template_changed, "actions"),
        )
        for name, change, mismatch in cases:
            with self.subTest(name=name):
                (
                    gateway,
                    _dispatch,
                    comparison,
                    observation,
                    verification,
                ) = await self._dispatch_with_readback_change(change)
                self.assertTrue(comparison.normalization_valid)
                self.assertFalse(comparison.semantic_match)
                self.assertIn(mismatch, comparison.mismatch_categories)
                self.assertIn(mismatch, observation.mismatch_fields)
                self.assertEqual(
                    verification.outcome,
                    NormalizedOperationOutcome.VERIFICATION_MISMATCH,
                )
                self.assertEqual(gateway.counters.dispatches, 1)

    def test_registry_metadata_is_excluded_but_not_arbitrary_behavior(self):
        approved = garage_config(include_fp300_guard=True)
        observed = deepcopy(approved)
        observed["category"] = GARAGE_CATEGORY
        observed["id"] = GARAGE_AUTOMATION_ID
        comparison = compare_resource_verification(
            "automation", approved, observed
        )
        self.assertTrue(comparison.normalization_valid)
        self.assertTrue(comparison.semantic_match)
        self.assertEqual(
            comparison.normalized_approved_fingerprint,
            comparison.normalized_observed_fingerprint,
        )
        observed["description"] += " changed"
        changed = compare_resource_verification(
            "automation", approved, observed
        )
        self.assertFalse(changed.semantic_match)
        self.assertIn("description", changed.mismatch_categories)

    def test_historical_binding_normalization_versions_are_unchanged(self):
        config = garage_config(include_fp300_guard=True)
        config["category"] = GARAGE_CATEGORY
        version_1 = normalize_automation(config, normalization_version=1)
        version_2 = normalize_automation(config, normalization_version=2)
        current = normalize_automation(
            config,
            normalization_version=AUTOMATION_NORMALIZATION_VERSION,
        )
        self.assertEqual(version_1, version_2)
        self.assertEqual(version_1["category"], GARAGE_CATEGORY)
        self.assertNotIn("category", current)
        self.assertEqual(
            version_1["action"][0]["wait_template"],
            GARAGE_WAIT_TEMPLATE,
        )
        self.assertEqual(
            current["action"][0]["wait_template"],
            GARAGE_WAIT_TEMPLATE,
        )

    def test_empty_wait_template_still_fails_closed(self):
        for value in (
            "  \n ",
            "x" * (MAX_AUTOMATION_TEMPLATE_LENGTH + 1),
        ):
            with self.subTest(length=len(value)):
                config = garage_config(include_fp300_guard=True)
                config["actions"][0]["wait_template"] = value
                with self.assertRaisesRegex(
                    ValueError, "invalid automation wait"
                ):
                    normalize_automation_for_verification(config)

    async def test_response_loss_and_provider_rejection_semantics_are_unchanged(
        self,
    ):
        for mode, expected, response_received, mutations in (
            (
                "response_lost_before_effect",
                NormalizedOperationOutcome.DISPATCH_INDETERMINATE,
                False,
                0,
            ),
            (
                "response_lost_after_effect",
                NormalizedOperationOutcome.DISPATCH_INDETERMINATE,
                False,
                1,
            ),
            (
                "provider_rejection",
                NormalizedOperationOutcome.DISPATCH_FAILED_CONFIRMED,
                True,
                0,
            ),
        ):
            with self.subTest(mode=mode):
                _proposal, gateway, adapter, prepared, preflight = (
                    await self._eligible_case()
                )
                gateway.dispatch_mode = mode
                if mode == "response_lost_after_effect":
                    gateway.after_write_hook = self._enrich_readback
                harness = ConfigurationLifecycleHarness(adapter)
                dispatch = await adapter.dispatch(
                    prepared,
                    preflight,
                    before_dispatch=harness.intent,
                )
                self.assertEqual(dispatch.outcome, expected)
                self.assertEqual(
                    dispatch.provider_response_received,
                    response_received,
                )
                observation = await harness.recover(
                    prepared,
                    recovery_context(
                        response_received=response_received,
                    ),
                )
                verification = await harness.verify(
                    prepared, observation
                )
                self.assertEqual(
                    verification.outcome,
                    (
                        NormalizedOperationOutcome.SUCCEEDED_VERIFIED
                        if mutations
                        else NormalizedOperationOutcome.VERIFICATION_MISMATCH
                    ),
                )
                self.assertEqual(gateway.counters.dispatches, 1)
                self.assertEqual(
                    gateway.counters.simulated_mutations, mutations
                )

    async def test_provider_5xx_remains_indeterminate_with_received_response(
        self,
    ):
        class Provider5xxGateway(SyntheticConfigurationGateway):
            async def write(
                self,
                action,
                resource_type,
                target_id,
                proposed_config,
            ):
                del action, resource_type, target_id, proposed_config
                self.counters.dispatches += 1
                raise HomeAssistantApiError(
                    details={
                        "reason": "synthetic_provider_5xx",
                        "provider_response_received": True,
                    }
                )

        current = garage_config(include_fp300_guard=False)
        proposed = garage_config(include_fp300_guard=True)
        proposal = proposal_for(
            "automation",
            "update",
            current_config=current,
            proposed_config=proposed,
        )
        gateway = Provider5xxGateway(
            {("automation", GARAGE_AUTOMATION_ID): current}
        )
        adapter = adapter_for("automation", "update", gateway)
        prepared = await adapter.prepare(proposal)
        preflight = await adapter.preflight(
            prepared,
            acquired_locks=adapter.lock_requests(prepared),
        )
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared,
            preflight,
            before_dispatch=harness.intent,
        )
        self.assertEqual(
            dispatch.outcome,
            NormalizedOperationOutcome.DISPATCH_INDETERMINATE,
        )
        self.assertTrue(dispatch.provider_response_received)
        observation = await harness.recover(
            prepared,
            recovery_context(response_received=True),
        )
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.VERIFICATION_MISMATCH,
        )
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(gateway.counters.simulated_mutations, 0)

    async def test_duplicate_apply_does_not_redispatch_live_shaped_write(self):
        current = garage_config(include_fp300_guard=False)
        proposed = garage_config(include_fp300_guard=True)
        proposed["category"] = GARAGE_CATEGORY
        proposal = proposal_for(
            "automation",
            "update",
            task_id="execution-beta34-garage-verification",
            plan_id="plan-beta34-garage-verification",
            current_config=current,
            proposed_config=proposed,
        )
        gateway = SyntheticConfigurationGateway(
            {("automation", GARAGE_AUTOMATION_ID): current}
        )
        gateway.after_write_hook = self._enrich_readback
        adapter = adapter_for("automation", "update", gateway)
        prepared = await adapter.prepare(proposal)
        sequence = prepare_configuration_sequence((prepared,))
        child = single_operation_child_descriptor(sequence)
        identity = ExecutionIdentity(
            task_id=child.public_task_id,
            plan_id=child.plan_id,
            attempt_id=child.attempt_id,
            request_id="request-beta34-garage-verification",
            owner_id="owner-beta34-garage-verification",
        )
        clock = FakeClock()
        approval = SyntheticApprovalRecorder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executions = DurableExecutionRepository(root)
            executor = SharedOperationExecutor(
                lock_store=DurableLockStore(root),
                execution_repository=executions,
                lock_timing=LOCK_TIMING,
                executor_timing=EXECUTOR_TIMING,
                now=clock.now,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
            first = await executor.execute(
                adapter=adapter,
                prepared=prepared,
                identity=identity,
                approval_consumption=approval,
            )
            second = await executor.execute(
                adapter=adapter,
                prepared=prepared,
                identity=identity,
                approval_consumption=approval,
            )
        self.assertEqual(first.outcome, "succeeded_verified")
        self.assertEqual(second.outcome, "succeeded_verified")
        self.assertTrue(second.duplicate_execution)
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(gateway.counters.simulated_mutations, 1)
        self.assertEqual(approval.invocations, 1)
        self.assertEqual(approval.consumptions, 1)


if __name__ == "__main__":
    unittest.main()
