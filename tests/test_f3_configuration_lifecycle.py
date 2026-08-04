"""Preflight, dispatch, verification, and recovery tests for F3-C1."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from f3_contracts.operation_adapter import NormalizedOperationOutcome
from ha_mcp_engineering.f3_configuration.adapter import (
    ConfigurationOperationAdapter,
)
from ha_mcp_engineering.f3_configuration.observability import (
    ConfigurationAdapterMetrics,
    InMemoryConfigurationEventSink,
)

from tests.f3_configuration_fixtures import (
    ConfigurationLifecycleHarness,
    SyntheticConfigurationGateway,
    SyntheticProcessLoss,
    adapter_for,
    proposal_for,
    recovery_context,
    valid_config,
)


class LifecycleTestCase(unittest.IsolatedAsyncioTestCase):
    async def prepared(
        self,
        resource_type: str = "automation",
        action: str = "update",
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
        return proposal, gateway, adapter, prepared

    async def eligible(self, *args, **kwargs):
        proposal, gateway, adapter, prepared = await self.prepared(
            *args, **kwargs
        )
        preflight = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        self.assertTrue(preflight.eligible, preflight.diagnostic_codes)
        return proposal, gateway, adapter, prepared, preflight


class PreflightTests(LifecycleTestCase):
    async def test_missing_complete_lock_set_rejects_before_provider_dispatch(self):
        _proposal, gateway, adapter, prepared = await self.prepared()
        result = await adapter.preflight(prepared, acquired_locks=())
        self.assertEqual(result.outcome, NormalizedOperationOutcome.LOCK_CONFLICT)
        self.assertEqual(gateway.counters.dispatches, 0)
        self.assertEqual(gateway.counters.simulated_mutations, 0)

    async def test_approval_policy_provider_and_expiry_each_fail_closed(self):
        variants = (
            ("approval", {"approval_consumed": False}, "approval_not_consumed"),
            ("policy", {"policy_snapshot_valid": False}, "policy_snapshot_mismatch"),
            ("provider", {"provider_admitted": False}, "provider_not_admitted"),
        )
        for name, kwargs, code in variants:
            with self.subTest(name=name):
                proposal = proposal_for("automation", "update", **kwargs)
                _p, gateway, adapter, prepared = await self.prepared(
                    proposal=proposal
                )
                result = await adapter.preflight(
                    prepared,
                    acquired_locks=adapter.lock_requests(prepared),
                )
                self.assertFalse(result.eligible)
                self.assertIn(code, result.diagnostic_codes)
                self.assertEqual(gateway.counters.dispatches, 0)

        proposal = replace(
            proposal_for("automation", "update"),
            plan_expires_at="2026-08-04T11:59:59+00:00",
        )
        _p, gateway, adapter, prepared = await self.prepared(proposal=proposal)
        result = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        self.assertIn("plan_expired", result.diagnostic_codes)
        self.assertEqual(gateway.counters.dispatches, 0)

    async def test_full_configuration_check_is_required_at_preflight(self):
        proposal = proposal_for("script", "update")
        gateway = SyntheticConfigurationGateway(
            {("script", proposal.target_id): proposal.current_config()}
        )
        gateway.validation_result = {"result": "valid"}
        adapter = adapter_for("script", "update", gateway)
        prepared = await adapter.prepare(proposal)
        result = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        self.assertFalse(result.eligible)
        self.assertIn("configuration_validation_failed", result.diagnostic_codes)
        self.assertEqual(gateway.counters.validation_calls, 1)
        self.assertEqual(gateway.counters.dispatches, 0)

    async def test_dispatch_rereads_authoritative_state_immediately_before_intent(self):
        proposal, gateway, adapter, prepared, preflight = await self.eligible()
        reads_after_preflight = gateway.counters.reads
        stale = valid_config("automation")
        stale["alias"] = "external edit"
        stale["id"] = proposal.target_id
        gateway.states[("automation", proposal.target_id)] = stale
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        self.assertEqual(
            dispatch.outcome,
            NormalizedOperationOutcome.PREFLIGHT_REJECTED,
        )
        self.assertGreater(gateway.counters.reads, reads_after_preflight)
        self.assertEqual(harness.intent_commits, 0)
        self.assertEqual(gateway.counters.dispatches, 0)


class DurableDispatchTests(LifecycleTestCase):
    async def test_intent_persistence_failure_invokes_provider_zero_times(self):
        _p, gateway, adapter, prepared, preflight = await self.eligible()

        async def fail_intent():
            raise OSError("synthetic persistence failure")

        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=fail_intent
        )
        self.assertEqual(
            dispatch.outcome,
            NormalizedOperationOutcome.FAILED_PRE_DISPATCH,
        )
        self.assertEqual(dispatch.adapter_dispatch_count, 0)
        self.assertEqual(dispatch.provider_mutation_count, 0)
        self.assertEqual(gateway.counters.dispatches, 0)
        self.assertEqual(gateway.counters.simulated_mutations, 0)

    async def test_confirmed_provider_failure_is_one_call_and_zero_mutations(self):
        _p, gateway, adapter, prepared, preflight = await self.eligible()
        gateway.dispatch_mode = "confirmed_failure"
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        self.assertEqual(
            dispatch.outcome,
            NormalizedOperationOutcome.DISPATCH_FAILED_CONFIRMED,
        )
        self.assertTrue(dispatch.dispatch_intent_recorded)
        self.assertEqual(dispatch.adapter_dispatch_count, 1)
        self.assertEqual(dispatch.provider_mutation_count, 0)
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(gateway.counters.simulated_mutations, 0)

    async def test_success_response_is_only_observing_until_exact_readback(self):
        _p, gateway, adapter, prepared, preflight = await self.eligible()
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        self.assertEqual(dispatch.outcome, NormalizedOperationOutcome.OBSERVING)
        observation = await harness.observe(prepared, dispatch)
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
        )
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(gateway.counters.simulated_mutations, 1)

    async def test_malformed_provider_response_never_authorizes_redispatch(self):
        _p, gateway, adapter, prepared, preflight = await self.eligible()
        gateway.dispatch_mode = "malformed_provider_response"
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        self.assertEqual(
            dispatch.outcome,
            NormalizedOperationOutcome.DISPATCH_INDETERMINATE,
        )
        self.assertTrue(dispatch.provider_response_received)
        observation = await harness.recover(
            prepared, recovery_context(response_received=True)
        )
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
        )
        self.assertEqual(gateway.counters.dispatches, 1)


class ResponseAndProcessLossTests(LifecycleTestCase):
    async def test_response_loss_before_effect_recovers_by_readback_without_redispatch(self):
        _p, gateway, adapter, prepared, preflight = await self.eligible()
        gateway.dispatch_mode = "response_lost_before_effect"
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        self.assertEqual(
            dispatch.outcome,
            NormalizedOperationOutcome.DISPATCH_INDETERMINATE,
        )
        observation = await harness.recover(prepared, recovery_context())
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.VERIFICATION_MISMATCH,
        )
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(gateway.counters.simulated_mutations, 0)

    async def test_response_loss_after_effect_recovers_verified_without_redispatch(self):
        _p, gateway, adapter, prepared, preflight = await self.eligible()
        gateway.dispatch_mode = "response_lost_after_effect"
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        observation = await harness.recover(prepared, recovery_context())
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
        )
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(gateway.counters.simulated_mutations, 1)

    async def test_process_loss_before_and_after_effect_never_redispatches(self):
        for mode, expected_mutations, expected_outcome in (
            (
                "process_loss_before_effect",
                0,
                NormalizedOperationOutcome.VERIFICATION_MISMATCH,
            ),
            (
                "process_loss_after_effect",
                1,
                NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
            ),
        ):
            with self.subTest(mode=mode):
                _p, gateway, adapter, prepared, preflight = await self.eligible()
                gateway.dispatch_mode = mode
                harness = ConfigurationLifecycleHarness(adapter)
                with self.assertRaises(SyntheticProcessLoss):
                    await adapter.dispatch(
                        prepared,
                        preflight,
                        before_dispatch=harness.intent,
                    )
                reconstructed = adapter_for("automation", "update", gateway)
                recovered = ConfigurationLifecycleHarness(reconstructed)
                observation = await recovered.recover(
                    prepared, recovery_context()
                )
                verification = await recovered.verify(prepared, observation)
                self.assertEqual(verification.outcome, expected_outcome)
                self.assertEqual(gateway.counters.dispatches, 1)
                self.assertEqual(
                    gateway.counters.simulated_mutations, expected_mutations
                )

    async def test_process_loss_after_intent_before_provider_is_observation_only(self):
        _p, gateway, adapter, prepared, preflight = await self.eligible()
        intent_committed = False

        async def commit_then_lose_process():
            nonlocal intent_committed
            intent_committed = True
            raise SyntheticProcessLoss()

        with self.assertRaises(SyntheticProcessLoss):
            await adapter.dispatch(
                prepared,
                preflight,
                before_dispatch=commit_then_lose_process,
            )
        self.assertTrue(intent_committed)
        reconstructed = adapter_for("automation", "update", gateway)
        harness = ConfigurationLifecycleHarness(reconstructed)
        observation = await harness.recover(prepared, recovery_context())
        verification = await harness.verify(prepared, observation)
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.VERIFICATION_MISMATCH,
        )
        self.assertEqual(gateway.counters.dispatches, 0)
        self.assertEqual(gateway.counters.simulated_mutations, 0)

    async def test_unreadable_recovery_observes_until_fixed_deadline_then_manual_review(self):
        _p, gateway, adapter, prepared, _preflight = await self.eligible()
        gateway.read_error_count = 1
        harness = ConfigurationLifecycleHarness(adapter)
        observation = await harness.recover(prepared, recovery_context())
        self.assertFalse(observation.observation_complete)
        self.assertEqual(observation.outcome, NormalizedOperationOutcome.OBSERVING)
        expired = await harness.recover(
            prepared,
            recovery_context(deadline="2026-08-04T11:59:59+00:00"),
        )
        self.assertEqual(
            expired.outcome,
            NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED,
        )
        self.assertEqual(gateway.counters.dispatches, 0)

    async def test_contradictory_recovery_context_fails_closed(self):
        _p, gateway, adapter, prepared = await self.prepared()
        harness = ConfigurationLifecycleHarness(adapter)
        context = replace(
            recovery_context(),
            dispatch_intent_recorded=False,
        )
        observation = await harness.recover(prepared, context)
        self.assertEqual(
            observation.outcome,
            NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED,
        )
        self.assertEqual(gateway.counters.dispatches, 0)

    async def test_intent_without_provider_call_still_recovers_by_readback_only(self):
        _p, gateway, adapter, prepared = await self.prepared()
        harness = ConfigurationLifecycleHarness(adapter)
        context = replace(
            recovery_context(),
            provider_invocation_may_have_occurred=False,
        )
        observation = await harness.recover(prepared, context)
        self.assertEqual(
            observation.outcome,
            NormalizedOperationOutcome.OBSERVING,
        )
        self.assertTrue(observation.observation_complete)
        self.assertEqual(gateway.counters.dispatches, 0)
        self.assertEqual(gateway.counters.simulated_mutations, 0)

    async def test_pre_intent_process_loss_can_only_resume_through_fresh_preflight(self):
        _p, gateway, adapter, prepared, _preflight = await self.eligible()
        reconstructed = adapter_for("automation", "update", gateway)
        renewed = await reconstructed.preflight(
            prepared,
            acquired_locks=reconstructed.lock_requests(prepared),
        )
        self.assertTrue(renewed.eligible)
        self.assertEqual(gateway.counters.dispatches, 0)
        harness = ConfigurationLifecycleHarness(reconstructed)
        dispatched = await reconstructed.dispatch(
            prepared,
            renewed,
            before_dispatch=harness.intent,
        )
        self.assertEqual(dispatched.adapter_dispatch_count, 1)
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(gateway.counters.simulated_mutations, 1)

    async def test_all_post_intent_reconstruction_points_are_readback_only(self):
        _p, gateway, adapter, prepared, preflight = await self.eligible()
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

        # These snapshots represent loss after response, before/during
        # readback, during verification, and after verification before release.
        for point in (
            "after_provider_response",
            "before_readback",
            "during_readback",
            "during_verification",
            "after_verification_before_lock_release",
        ):
            with self.subTest(point=point):
                reconstructed = adapter_for("automation", "update", gateway)
                recovered = ConfigurationLifecycleHarness(reconstructed)
                readback = await recovered.recover(
                    prepared,
                    recovery_context(response_received=True),
                )
                result = await recovered.verify(prepared, readback)
                self.assertEqual(
                    result.outcome,
                    NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
                )
                self.assertEqual(gateway.counters.dispatches, 1)
                self.assertEqual(gateway.counters.simulated_mutations, 1)


class ExternalWriterConcurrencyTests(LifecycleTestCase):
    async def test_external_writer_before_lock_or_preflight_is_stale_rejection(self):
        proposal = proposal_for("automation", "update")
        external = proposal.current_config()
        external["alias"] = "external before lock"
        gateway = SyntheticConfigurationGateway(
            {("automation", proposal.target_id): external}
        )
        adapter = adapter_for("automation", "update", gateway)
        prepared = await adapter.prepare(proposal)
        preflight = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        self.assertIn("stale_target_state", preflight.diagnostic_codes)
        self.assertEqual(gateway.counters.dispatches, 0)

    async def test_external_writer_after_preflight_before_late_reread_is_not_overwritten(self):
        proposal, gateway, adapter, prepared, preflight = await self.eligible()
        external = proposal.current_config()
        external["alias"] = "external after preflight"
        gateway.states[("automation", proposal.target_id)] = external
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        self.assertEqual(
            dispatch.outcome,
            NormalizedOperationOutcome.PREFLIGHT_REJECTED,
        )
        self.assertEqual(gateway.counters.dispatches, 0)

    async def test_external_writer_after_late_reread_exposes_non_atomic_limitation(self):
        proposal, gateway, adapter, prepared, preflight = await self.eligible()

        async def intent_with_external_write():
            external = proposal.current_config()
            external["alias"] = "external inside non-atomic window"
            gateway.states[("automation", proposal.target_id)] = external

        dispatch = await adapter.dispatch(
            prepared,
            preflight,
            before_dispatch=intent_with_external_write,
        )
        harness = ConfigurationLifecycleHarness(adapter)
        observation = await harness.observe(prepared, dispatch)
        verification = await harness.verify(prepared, observation)
        # Exact final readback cannot prove the external edit was not
        # overwritten between the final preread and the provider save.
        self.assertEqual(
            verification.outcome,
            NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
        )
        self.assertEqual(gateway.counters.dispatches, 1)
        self.assertEqual(gateway.counters.simulated_mutations, 1)

    async def test_external_writer_immediately_after_save_is_verification_mismatch(self):
        proposal, gateway, adapter, prepared, preflight = await self.eligible()

        def external_after_save(gateway, resource_type, target):
            gateway.states[(resource_type, target)]["alias"] = "external after save"

        gateway.after_write_hook = external_after_save
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


class MetricsAndSanitizationTests(LifecycleTestCase):
    async def test_metrics_and_events_contain_only_closed_labels_and_hashes(self):
        proposal = proposal_for("automation", "update")
        gateway = SyntheticConfigurationGateway(
            {("automation", proposal.target_id): proposal.current_config()}
        )
        metrics = ConfigurationAdapterMetrics()
        sink = InMemoryConfigurationEventSink()
        adapter = ConfigurationOperationAdapter(
            "automation",
            "update",
            gateway,
            metrics=metrics,
            event_sink=sink,
            now=lambda: __import__(
                "tests.f3_configuration_fixtures",
                fromlist=["FIXED_NOW"],
            ).FIXED_NOW,
        )
        prepared = await adapter.prepare(proposal)
        preflight = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        harness = ConfigurationLifecycleHarness(adapter)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=harness.intent
        )
        observation = await harness.observe(prepared, dispatch)
        await harness.verify(prepared, observation)
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["dispatch_attempts:automation:update"], 1)
        self.assertEqual(snapshot["verification_successes:automation:update"], 1)
        rendered = repr((snapshot, sink.snapshot()))
        self.assertNotIn("Porch light", rendered)
        self.assertNotIn("light.synthetic", rendered)
        self.assertNotIn("notify", rendered)
        self.assertTrue(all(len(event.target_identity_hash) == 64 for event in sink.snapshot()))


if __name__ == "__main__":
    unittest.main()
