"""HAMCP-189 PR A: Core authority before "already satisfied", R6, durable cancel.

All fixtures are offline and synthetic: a real signed Core runtime, governed
helper-state and operational F3 runtimes, and fake provider gateways.  No live
Home Assistant, ha-mcp or network endpoint is reached.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.f3.executor import DispatchAuthorityRefused  # noqa: E402
from ha_mcp_engineering.f3.persistence import ExecutionStorageError  # noqa: E402
from ha_mcp_engineering.f3_runtime.registry import (  # noqa: E402
    CONFIGURATION_REGISTRATIONS,
    DASHBOARD_REGISTRATION,
    ENTITY_METADATA_REGISTRATION,
    HELPER_STATE_REGISTRATION,
    OPERATIONAL_REGISTRATIONS,
)
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    OPERATOR_CANCEL_REASON,
    F3RuntimeIntegration,
    _CoreDispatchAuthorityGuard,
    _CoreVerificationAdapter,
)
from ha_mcp_engineering.governance.models import ApprovalState  # noqa: E402
from ha_mcp_engineering.governance.service import ChangeGovernanceService  # noqa: E402
from ha_mcp_engineering.governance.storage import ChangePlanRepository  # noqa: E402
from ha_mcp_engineering.governance.task_models import ExecutionTaskState  # noqa: E402
from ha_mcp_engineering.ha_core_readmission import routes  # noqa: E402
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    current_telemetry,
    end_request,
)
from tests.f3_synthetic_adapter import (  # noqa: E402
    SyntheticOperationAdapter,
    prepared_dashboard_operation,
)
from tests.test_2_1a_beta2_operational_lifecycle import (  # noqa: E402
    Clock,
    LegacyGateway,
)
from tests import test_core_semantic_continuity as continuity  # noqa: E402
from tests import test_f3_adapter_core as adapter_core  # noqa: E402
from tests import test_f3_runtime_integration as integration  # noqa: E402


def _child(runtime, task_id):
    declaration = runtime.children.declarations_for_task(task_id)[0]
    return runtime.children.get(declaration["child_id"])


def _events(record):
    return [] if record is None else [event["event_type"] for event in record.events]


class CoreAuthorityBeforeAlreadySatisfiedTests(unittest.IsolatedAsyncioTestCase):
    """R3/R3b (O1): no verified outcome without current Core authority."""

    grant = continuity.CoreSemanticContinuityTests.grant
    configure = continuity.CoreSemanticContinuityTests.configure
    assert_settled = continuity.CoreSemanticContinuityTests.assert_settled

    async def asyncSetUp(self):
        await continuity.CoreSemanticContinuityTests.asyncSetUp(self)
        await self.configure()
        self.reads: list[bool] = []
        original = self.helper.read_state

        async def read_state(entity_id):
            # Fake gateways do not consult telemetry; record what a real
            # client would have been told for this read.
            telemetry = current_telemetry()
            self.reads.append(
                telemetry is not None and telemetry.authorize_core_dispatch()
            )
            return await original(entity_id)

        self.helper.read_state = read_state

    async def planned(self, desired="on"):
        created = await self.service.create_helper_state_plan(
            entity_id=self.helper.entity_id, desired_state=desired)
        plan = await self.grant(created)
        self.reads.clear()
        return plan

    def counters(self):
        return dict(self.core.health_snapshot()["counters"])

    async def test_degraded_core_already_reached_refuses_before_any_read(self):
        plan = await self.planned()
        self.helper.set_observed_state("on", self.clock)
        # Degrade Core: the signed registry entry expires.
        self.registry_case.clock += timedelta(days=2)
        before = self.counters()
        network_before = len(self.network.calls)

        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        child = _child(self.runtime, result["task_id"])

        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertEqual(child.normalized_outcome, "provider_unavailable_pre_dispatch")
        self.assertTrue(any(
            "dispatch_authority_unavailable" in event["diagnostic_codes"]
            for event in child.events), child.events)
        self.assertNotIn("preflight_noop_verified", _events(child))
        self.assertFalse(any(
            "desired_state_already_reached" in event["diagnostic_codes"]
            for event in child.events))
        self.assertFalse(child.preflight_completed)
        self.assertEqual(self.reads, [])
        self.assertEqual(len(self.network.calls), network_before)
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assertEqual(self.service._load(plan["plan_id"]).approval.state,
                         ApprovalState.APPROVED)
        # The probe's "zero lease attempts" is now a refused lease attempt.
        self.assertGreater(self.counters()["lease_attempts"], before["lease_attempts"])
        self.assertGreater(self.counters()["lease_failures"], before["lease_failures"])
        await self.runtime.recover_once("synthetic_after_refusal")
        self.assertEqual(self.reads, [])
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assert_settled()

    async def test_admitted_core_already_reached_verifies_under_current_authority(self):
        plan = await self.planned()
        self.helper.set_observed_state("on", self.clock)
        before = self.counters()

        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        child = _child(self.runtime, result["task_id"])

        # HAMCP-181 outcome strings are unchanged; only the ordering moved.
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertIn("preflight_noop_verified", _events(child))
        self.assertTrue(any(
            event["diagnostic_codes"] == ["desired_state_already_reached"]
            for event in child.events))
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assertTrue(self.reads)
        self.assertTrue(all(self.reads), self.reads)
        self.assertGreater(self.counters()["lease_attempts"], before["lease_attempts"])
        self.assertEqual(self.service._load(plan["plan_id"]).approval.state,
                         ApprovalState.APPROVED)
        self.assert_settled()

    async def test_authority_retired_during_preflight_read_cannot_verify(self):
        plan = await self.planned()
        self.helper.set_observed_state("on", self.clock)
        read = self.helper.read_state
        after: list[bool] = []

        async def retiring_read(entity_id):
            result = await read(entity_id)
            self.core.request_reconciliation(connection_changed=True)
            after.append(current_telemetry().authorize_core_dispatch())
            return result

        self.helper.read_state = retiring_read
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        child = _child(self.runtime, result["task_id"])

        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertNotIn("preflight_noop_verified", _events(child))
        self.assertEqual(after[:1], [False])
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assertEqual(self.service._load(plan["plan_id"]).approval.state,
                         ApprovalState.APPROVED)
        self.assert_settled()

    async def test_dispatch_window_opens_only_after_before_dispatch(self):
        plan = await self.planned()
        window: list[tuple[str, bool]] = []
        set_state = self.helper.set_state

        async def observed_set_state(entity_id, desired_state, *, before_dispatch):
            window.append(("before", current_telemetry().authorize_core_dispatch()))

            async def wrapped():
                await before_dispatch()
                window.append(("after", current_telemetry().authorize_core_dispatch()))

            return await set_state(entity_id, desired_state, before_dispatch=wrapped)

        self.helper.set_state = observed_set_state
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])

        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(window, [("before", False), ("after", True)])
        self.assertEqual(self.helper.dispatch_count, 1)
        self.assertTrue(all(self.reads), self.reads)
        self.assert_settled()

    async def test_retirement_after_before_dispatch_closes_window(self):
        plan = await self.planned()
        window: list[bool] = []
        set_state = self.helper.set_state

        async def retiring_set_state(entity_id, desired_state, *, before_dispatch):
            async def wrapped():
                await before_dispatch()
                self.core.request_reconciliation(connection_changed=True)
                window.append(current_telemetry().authorize_core_dispatch())

            return await set_state(entity_id, desired_state, before_dispatch=wrapped)

        self.helper.set_state = retiring_set_state
        await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(window, [False])

    async def test_expiry_at_dispatch_guard_reconcile_has_zero_dispatch(self):
        plan = await self.planned()
        reconcile = self.core.reconcile_once
        seen: list[str] = []

        async def expire(reason):
            await reconcile(reason)
            seen.append(reason)
            if seen.count("mutation_pre_dispatch") == 2:
                self.registry_case.clock += timedelta(days=2)

        with patch.object(self.core, "reconcile_once", side_effect=expire):
            result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assertEqual(self.service._load(plan["plan_id"]).approval.state,
                         ApprovalState.APPROVED)
        self.assert_settled()

    async def test_cancel_racing_dispatch_wins_without_dispatch(self):
        plan = await self.planned()
        reconcile = self.core.reconcile_once
        paused = asyncio.Event()
        release = asyncio.Event()
        calls: list[str] = []

        async def pause(reason):
            await reconcile(reason)
            calls.append(reason)
            if calls.count("mutation_pre_dispatch") == 2:
                paused.set()
                await release.wait()

        with patch.object(self.core, "reconcile_once", side_effect=pause):
            apply = asyncio.create_task(
                self.service.apply(plan["plan_id"], plan["plan_hash"]))
            await asyncio.wait_for(paused.wait(), 5)
            task_id = self.service.task_repository.get_for_plan(plan["plan_id"]).task_id
            child = _child(self.runtime, task_id)
            self.assertIsNotNone(child)
            self.assertFalse(child.terminal)
            self.assertIsNone(child.dispatch_intent)
            # A same-process cancel serializes behind apply's plan lock; the
            # race is with another process, which holds no in-memory lock and
            # meets this apply only through durable child storage.
            cancelled = await asyncio.wait_for(
                self.runtime.cancel(self.service._load_task(task_id)), 5)
            release.set()
            result = await asyncio.wait_for(apply, 5)

        self.assertEqual(cancelled["status"], "cancelled_pre_dispatch")
        self.assertFalse(cancelled["provider_contacted"])
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assertFalse(result["provider_dispatch_occurred"])
        record = _child(self.runtime, task_id)
        self.assertIsNone(record.dispatch_intent)
        self.assertEqual(record.normalized_outcome, "cancelled_pre_dispatch")
        self.assertEqual(self.service._load(plan["plan_id"]).approval.state,
                         ApprovalState.APPROVED)
        self.assertEqual(self.service._load_task(task_id).state,
                         ExecutionTaskState.CANCELLED_PRE_DISPATCH)
        await self.runtime.recover_once("synthetic_after_cancel")
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assert_settled()


class _StubCore:
    def __init__(self):
        self.reconciles = 0
        self.acquires = 0

    async def reconcile_once(self, reason):
        self.reconciles += 1

    def acquire_f3(self, prepared, *, plan_created_at):
        self.acquires += 1
        return object()

    def consume(self, authority):
        return object()

    def revalidate(self, authority, commits):
        return True

    def release(self, authority):
        return None

    def finish(self, commits):
        return True


class _Refusing:
    def __init__(self, exc):
        self.exc = exc

    def acquire(self, prepared, preflight):
        raise self.exc


class UnmappedOperationRefusalTests(unittest.IsolatedAsyncioTestCase):
    """R6: an unmapped F3 operation names its exact refusal."""

    def setUp(self):
        self.harness = adapter_core.SharedExecutorTests()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        _, self.context = begin_request("hamcp189-r6")
        self.addCleanup(end_request, self.context)

    async def run_executor(self, adapter, authority):
        result = await self.harness.executor().execute(
            adapter=adapter,
            prepared=prepared_dashboard_operation(),
            identity=adapter_core._identity(),
            approval_consumption=self.harness.approval,
            dispatch_authority=authority,
        )
        record = self.harness.executions.get("task-synthetic")
        codes = {code for event in record.events for code in event["diagnostic_codes"]}
        return result, codes

    async def test_wrapped_preflight_refuses_unmapped_before_any_read(self):
        core = _StubCore()
        inner = SyntheticOperationAdapter()
        guard = _CoreDispatchAuthorityGuard(core, datetime.now(timezone.utc).isoformat())
        result, codes = await self.run_executor(
            _CoreVerificationAdapter(inner, core, guard), guard)
        self.assertEqual(result.outcome, "provider_unavailable_pre_dispatch")
        self.assertIn("core_requirement_unmapped", codes)
        self.assertNotIn("preflight_failed", codes)
        self.assertEqual(inner.counters.preflight_invocations, 0)
        self.assertEqual(inner.counters.dispatch_invocations, 0)
        self.assertEqual((core.reconciles, core.acquires), (0, 0))
        self.assertEqual(self.harness.approval.invocations, 0)

    async def test_dispatch_guard_refuses_unmapped_before_lease(self):
        core = _StubCore()
        inner = SyntheticOperationAdapter()
        guard = _CoreDispatchAuthorityGuard(core, datetime.now(timezone.utc).isoformat())
        result, codes = await self.run_executor(inner, guard)
        self.assertEqual(result.outcome, "provider_unavailable_pre_dispatch")
        self.assertIn("core_requirement_unmapped", codes)
        self.assertEqual(inner.counters.dispatch_invocations, 0)
        self.assertEqual((core.reconciles, core.acquires), (0, 0))
        self.assertEqual(self.harness.approval.invocations, 0)

    async def test_unknown_refusal_codes_collapse_to_generic(self):
        for exc in (DispatchAuthorityRefused("Free Text!"), RuntimeError("boom")):
            with self.subTest(exc=type(exc).__name__):
                self.harness.doCleanups()
                self.harness.setUp()
                result, codes = await self.run_executor(SyntheticOperationAdapter(), _Refusing(exc))
                self.assertIn("dispatch_authority_unavailable", codes)
                self.assertNotIn("Free Text!", codes)

    def test_every_registered_operation_has_a_core_route(self):
        registered = {(cap, cap) for cap, _resource, _action in CONFIGURATION_REGISTRATIONS}
        registered.add((ENTITY_METADATA_REGISTRATION[0], ENTITY_METADATA_REGISTRATION[0]))
        registered.update((cap, op) for cap, op, _kind in OPERATIONAL_REGISTRATIONS)
        registered.add(HELPER_STATE_REGISTRATION[:2])
        registered.add(DASHBOARD_REGISTRATION[:2])
        routed = (routes._F3_DASHBOARD_OPERATIONS
                  | routes._F3_TYPED_HELPER_OPERATIONS
                  | routes._F3_GOVERNED_CONFIGURATION_OPERATIONS
                  | routes._F3_ENTITY_METADATA_OPERATIONS)
        self.assertEqual(registered, set(routed))
        for cap, op in registered:
            prepared = SimpleNamespace(capability_id=cap, capability_identity=cap, operation=op)
            self.assertNotIn("core.unsupported_f3_operation", routes.f3_requirements(prepared))


class DurableCancelTests(unittest.IsolatedAsyncioTestCase):
    _grant = integration._OperationalActivationBase._grant
    asyncTearDown = integration._OperationalActivationBase.asyncTearDown

    """R2 (O3): cancel is recorded before provider contact and honored."""

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clock = Clock()
        self.lifecycle = _OutageLifecycleGateway()
        self.lifecycle.now = self.clock
        self.provider = "up"
        self.identity_reads = 0
        self.telemetry, self.context = begin_request("hamcp189-cancel")
        self.telemetry.caller_id = "mcp-requester"
        self.service, self.runtime = self.build()
        await self.runtime.recover_once("startup")

    def build(self):
        service = ChangeGovernanceService(
            ChangePlanRepository(self.root / "plans"),
            LegacyGateway(),
            AuditLogger(str(self.root / "audit.jsonl"), "hamcp189-secret"),
            now=self.clock,
            lifecycle_gateway=self.lifecycle,
        )
        runtime = F3RuntimeIntegration(
            service=service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=integration._UnusedExactConfigurationGateway(),
            backup_gateway=None,
            lifecycle_gateway=self.lifecycle,
            provider_identity_reader=self.read_identity,
            retention_days=90,
        )
        service.f3_runtime = runtime
        return service, runtime

    async def read_identity(self):
        self.identity_reads += 1
        if self.provider == "down":
            raise GovernanceError(ErrorCode.OPERATIONAL_PROVIDER_UNAVAILABLE)
        digest = "b" * 64 if self.provider == "drifted" else "a" * 64
        return {"slug": "core_ha_mcp", "evidence_hash": digest}

    async def initialized(self):
        created = await self.service.create_reload_plan(reload_target="automation")
        approved = await self._grant(created)
        plan = self.service._load(approved["plan_id"])
        task, _prepared, _requests = await self.runtime._initialize(plan, approved["plan_hash"])
        self.assertIsNone(_child(self.runtime, task.task_id))
        return approved, plan, task

    def provider_contacts(self):
        return (self.identity_reads, self.lifecycle.planning_count,
                self.lifecycle.dispatch_count, self.lifecycle.verification_count)

    def assert_operator_cancelled(self, task_id):
        child = _child(self.runtime, task_id)
        self.assertIsNotNone(child)
        self.assertEqual(child.normalized_outcome, "cancelled_pre_dispatch")
        self.assertIsNone(child.dispatch_intent)
        self.assertTrue(any(
            event["event_type"] == "execution_cancelled"
            and OPERATOR_CANCEL_REASON in event["diagnostic_codes"]
            for event in child.events), child.events)

    async def test_cancel_during_outage_is_durable_and_recovery_never_dispatches(self):
        approved, _plan, task = await self.initialized()
        self.provider = "down"
        self.lifecycle.outage = True
        before = self.provider_contacts()

        result = await self.service.cancel_execution_task(task.task_id)

        self.assertEqual(result["status"], "cancelled_pre_dispatch")
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertIs(result["provider_contacted"], False)
        self.assertEqual(self.provider_contacts(), before)
        self.assert_operator_cancelled(task.task_id)
        self.assertEqual(self.service._load_task(task.task_id).state,
                         ExecutionTaskState.CANCELLED_PRE_DISPATCH)

        # The provider returns; neither recovery nor a repeated apply dispatches.
        self.provider = "up"
        self.lifecycle.outage = False
        await self.runtime.recover_once("synthetic_provider_returned")
        await self.runtime.recover_once("synthetic_provider_returned_again")
        try:
            await self.service.apply(approved["plan_id"], approved["plan_hash"])
        except GovernanceError:
            pass
        self.assertEqual(self.lifecycle.dispatch_count, 0)
        self.assertEqual(self.service._load_task(task.task_id).state,
                         ExecutionTaskState.CANCELLED_PRE_DISPATCH)

    async def test_cancel_after_identity_drift_binds_stored_identity(self):
        _approved, plan, task = await self.initialized()
        self.provider = "drifted"
        # Apply/recovery keep the live drift refusal.
        with self.assertRaises(GovernanceError) as raised:
            await self.runtime._load_prepared(plan, task)
        self.assertEqual(raised.exception.details.get("reason"),
                         "provider_lock_identity_changed")
        reads = self.identity_reads

        result = await self.service.cancel_execution_task(task.task_id)

        self.assertEqual(result["status"], "cancelled_pre_dispatch")
        self.assertEqual(self.identity_reads, reads)
        self.assert_operator_cancelled(task.task_id)
        self.provider = "up"
        await self.runtime.recover_once("synthetic_after_drift")
        self.assertEqual(self.lifecycle.dispatch_count, 0)

    async def test_cancel_after_intent_is_still_refused(self):
        created = await self.service.create_home_assistant_restart_plan()
        plan = await self._grant(created)
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(result["task_state"], "observing")
        self.provider = "down"
        with self.assertRaises(GovernanceError) as raised:
            await self.service.cancel_execution_task(result["task_id"])
        self.assertEqual(raised.exception.code,
                         ErrorCode.CANCELLATION_NOT_PERMITTED_AFTER_DISPATCH)
        self.assertEqual(self.lifecycle.dispatch_count, 1)

    async def test_restart_between_child_cancel_and_public_event(self):
        approved, _plan, task = await self.initialized()
        status_before = self.service._load(approved["plan_id"]).status
        record = self.service._record_task_event

        def crash(task_arg, event_type, *args, **kwargs):
            if event_type == "task_cancelled_pre_dispatch":
                raise OSError("synthetic process loss")
            return record(task_arg, event_type, *args, **kwargs)

        self.provider = "down"
        with patch.object(self.service, "_record_task_event", side_effect=crash):
            # The projection transaction reports the loss as a storage error.
            with self.assertRaises((OSError, ExecutionStorageError)):
                await self.service.cancel_execution_task(task.task_id)
        self.assert_operator_cancelled(task.task_id)
        self.assertIn(self.service._load_task(task.task_id).state,
                      {ExecutionTaskState.CREATED, ExecutionTaskState.PREFLIGHT})

        # Restart: fresh service and runtime over the same durable storage.
        self.provider = "up"
        self.service, self.runtime = self.build()
        await self.runtime.recover_once("startup")

        self.assertEqual(self.service._load_task(task.task_id).state,
                         ExecutionTaskState.CANCELLED_PRE_DISPATCH)
        self.assertEqual(self.service._load(approved["plan_id"]).status, status_before)
        self.assertEqual(self.lifecycle.dispatch_count, 0)
        await self.runtime.recover_once("synthetic_second_pass")
        self.assertEqual(self.lifecycle.dispatch_count, 0)

    async def test_cancel_recorded_during_recovery_await_prevents_dispatch(self):
        _approved, _plan, task = await self.initialized()
        load = self.runtime._load_prepared
        executed: list[str] = []
        cancels: list[dict] = []

        async def load_then_cancel(plan_arg, task_arg, **kwargs):
            result = await load(plan_arg, task_arg, **kwargs)
            if not kwargs.get("cancel_only") and not cancels:
                cancels.append(await self.service.cancel_execution_task(task_arg.task_id))
            return result

        execute = self.runtime._execute_child

        async def counted_execute(*args, **kwargs):
            executed.append("execute")
            return await execute(*args, **kwargs)

        with patch.object(self.runtime, "_load_prepared", side_effect=load_then_cancel), \
                patch.object(self.runtime, "_execute_child", side_effect=counted_execute):
            await asyncio.wait_for(self.runtime.recover_once("synthetic_toctou"), 10)

        self.assertEqual([item["status"] for item in cancels], ["cancelled_pre_dispatch"])
        self.assertEqual(executed, [])
        self.assertEqual(self.lifecycle.dispatch_count, 0)
        self.assertEqual(self.service._load_task(task.task_id).state,
                         ExecutionTaskState.CANCELLED_PRE_DISPATCH)


class _OutageLifecycleGateway(integration._F3LifecycleGateway):
    """Lifecycle fake whose every provider method fails during an outage."""

    outage = False

    def _check(self):
        if self.outage:
            raise AssertionError("provider contacted during simulated outage")

    async def planning_evidence(self, operation, target):
        self._check()
        return await super().planning_evidence(operation, target)

    async def dispatch_reload(self, target, *, before_dispatch):
        self._check()
        return await super().dispatch_reload(target, before_dispatch=before_dispatch)

    async def verify_reload(self, target):
        self._check()
        return await super().verify_reload(target)


if __name__ == "__main__":
    unittest.main()
