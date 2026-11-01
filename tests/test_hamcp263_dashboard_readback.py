"""HAMCP-263: a dispatched dashboard update reaches a truthful terminal state.

Live beta.52 evidence: one governed dashboard update dispatched once and
landed, its first readback was unavailable, and the 30 s governance sweep
marked the parent ``dispatched_task_has_no_readback_reconciler`` before F3
recovery could take over the child's 120 s apply lease. The child stayed
``observing`` forever under the terminal parent and its three expired locks
blocked every later governed configuration apply (``home_assistant:core``
shared). These tests prove, offline and with exactly one provider write on
every path:

* the sweep leaves F3-owned dispatched tasks pending (bounded by the maximum
  post-dispatch deadline) and F3 projects the truthful terminal outcome;
* dashboard readback recovery decides its evidence deadline with one final
  read (verified, mismatch, or bounded indeterminate);
* a child stranded under an already-terminal parent receives one read-only
  readback, becomes terminal, releases its locks and leaves F3 ``recovering``
  while the terminal parent record is unchanged;
* expired leases of a terminal post-intent owner are released, except the
  declared target keys of an unresolved manual-review owner, and live leases
  are never released.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(Path(__file__).parent))

import test_dashboard_update_mvp as dashboard_suite  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.f3.locks import LockStorageError  # noqa: E402
from ha_mcp_engineering.f3_runtime import runtime as runtime_module  # noqa: E402
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3_EXECUTION_AUTHORITY,
    F3RuntimeIntegration,
    TERMINAL_PARENT_READBACK_RESULT,
    _TerminalParentReadbackLockStore,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
    FakeConfigurationGateway,
)
from tests.test_f3_runtime_integration import (  # noqa: E402
    _ExactFakeConfigurationGateway,
)

LIVE_REASON = "dispatched_task_has_no_readback_reconciler"
SWEEP_STEP = timedelta(seconds=40)


class _ReadbackGateway(dashboard_suite._DashboardGateway):
    """Dashboard fake whose post-write readbacks can be unavailable."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_reads_after_write = 0
        self.post_write_reads = 0

    async def preread(self, *, url_path: str):
        if self.write_count:
            self.post_write_reads += 1
            if self.fail_reads_after_write > 0:
                self.fail_reads_after_write -= 1
                self.preread_count += 1
                raise RuntimeError("synthetic readback unavailable")
        return await super().preread(url_path=url_path)


class DashboardReadbackRecoveryTests(unittest.IsolatedAsyncioTestCase):
    _plan = dashboard_suite.DashboardUpdateRuntimeTests._plan
    _approve = dashboard_suite.DashboardUpdateRuntimeTests._approve
    create_automation_plan = ConfigurationPlanTestCase.create_automation_plan
    approve = ConfigurationPlanTestCase.approve

    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.clock = [datetime.now(timezone.utc)]
        self.dashboard = _ReadbackGateway()
        self.gateway = FakeConfigurationGateway()
        self._compose()
        await self.runtime.recover_once("startup")

    def _compose(self):
        """Compose one process over the durable storage (also a restart)."""

        self.repository = ChangePlanRepository(self.root / "plans")
        self.service = ChangeGovernanceService(
            self.repository,
            self.gateway,
            AuditLogger(str(self.root / "audit.jsonl"), "test-access-secret"),
            dashboard_gateway=self.dashboard,
            provider_identity_reader=(
                dashboard_suite._unrelated_lifecycle_provider_identity
            ),
            now=lambda: self.clock[0],
        )
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=_ExactFakeConfigurationGateway(self.gateway),
            backup_gateway=None,
            lifecycle_gateway=None,
            dashboard_gateway=self.dashboard,
            provider_identity_reader=(
                dashboard_suite._unrelated_lifecycle_provider_identity
            ),
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime

    async def asyncTearDown(self):
        self.temporary.cleanup()

    # -- helpers -----------------------------------------------------------

    async def _dispatch_observing(self, *, failed_reads: int):
        created = await self._plan()
        await self._approve(created)
        self.dashboard.fail_reads_after_write = failed_reads
        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual("observing", result["task_state"])
        self.assertEqual(1, self.dashboard.write_count)
        declaration = self.runtime.children.declarations_for_task(
            result["task_id"]
        )[0]
        return result["task_id"], declaration["child_id"]

    async def _pass(self, *, sweep: bool = True, recover: bool = True):
        self.clock[0] += SWEEP_STEP
        if recover:
            await self.runtime.recover_once("periodic")
        if sweep:
            return await self.service.reconcile_execution_tasks(
                trigger="periodic"
            )
        return None

    async def _live_shape(self):
        """Reproduce the deployed beta.52 shape with the pre-fix sweep.

        The first readback is unavailable, F3 recovery yields to the active
        apply lease, and the generic sweep (as before HAMCP-263) decides the
        parent first. Reads stay unavailable until the test restores them.
        """

        task_id, child_id = await self._dispatch_observing(failed_reads=100)
        with patch.object(
            self.service,
            "_f3_owns_post_dispatch_reconciliation",
            return_value=False,
        ):
            await self._pass()
        parent = self.service.task_repository.get(task_id)
        self.assertEqual("manual_review_required", parent.state.value)
        self.assertIn(LIVE_REASON, str(parent.to_dict()))
        child = self.runtime.children.get(child_id)
        self.assertFalse(child.terminal)
        self.assertEqual("observing", child.normalized_outcome)
        # Days later, every retained lease has expired.
        self.clock[0] += timedelta(days=2)
        self.dashboard.fail_reads_after_write = 0
        self.assertEqual(
            {
                ("addon:ha_mcp", "shared"),
                ("dashboard:main-operations", "exclusive"),
                ("home_assistant:core", "shared"),
            },
            {(item.key, item.mode) for item in self.runtime.locks.records()},
        )
        self.assertTrue(
            all(
                not item.conflict_hold
                and datetime.fromisoformat(item.lease_expires_at)
                < self.clock[0]
                for item in self.runtime.locks.records()
            )
        )
        return task_id, child_id, parent

    async def _unrelated_apply(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        writes = len(self.gateway.calls)
        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        return result, len(self.gateway.calls) - writes

    async def _f3_status(self):
        summary = await self.service.async_health_summary()
        return summary["f3"]["status"]

    # -- Step 1 --------------------------------------------------------------

    async def test_step1_expired_retained_locks_block_then_settle(self):
        task_id, child_id, parent = await self._live_shape()
        # Retained expired locks block an unrelated governed apply until the
        # stranded child is settled (the Step 1 lock impact).
        blocked, provider_calls = await self._unrelated_apply()
        self.assertEqual("failed_pre_dispatch", blocked["task_state"])
        blocked_child = self.runtime.children.get(
            self.runtime.children.declarations_for_task(
                blocked["task_id"]
            )[0]["child_id"]
        )
        self.assertEqual("lock_conflict", blocked_child.normalized_outcome)
        self.assertFalse(
            any(call[0] == "write" for call in self.gateway.calls)
        )
        # A declared child without a dispatch intent is not F3-owned
        # post-dispatch work; the dispatched dashboard parent is.
        self.assertIsNone(blocked_child.dispatch_intent)
        self.assertFalse(
            self.runtime.owns_post_intent_reconciliation(blocked["task_id"])
        )
        self.assertTrue(self.runtime.owns_post_intent_reconciliation(task_id))
        del provider_calls
        await self.runtime.recover_once("periodic")
        self.assertEqual((), self.runtime.locks.records())
        applied, _calls = await self._unrelated_apply()
        self.assertEqual("succeeded_verified", applied["task_state"])
        self.assertEqual(1, self.dashboard.write_count)
        del task_id, child_id, parent

    # -- Fix C: the deployed shape -----------------------------------------

    async def test_live_shape_child_terminal_locks_released_parent_unchanged(
        self,
    ):
        task_id, child_id, parent = await self._live_shape()
        self.assertEqual("recovering", await self._f3_status())
        reads = self.dashboard.post_write_reads

        await self.runtime.recover_once("periodic")

        child = self.runtime.children.get(child_id)
        self.assertTrue(child.terminal)
        self.assertEqual("succeeded_verified", child.normalized_outcome)
        self.assertEqual(1, self.dashboard.post_write_reads - reads)
        self.assertEqual((), self.runtime.locks.records())
        self.assertEqual(
            [], self.runtime.children.runtime(child_id)["selective_hold_tokens"]
        )
        self.assertEqual(
            TERMINAL_PARENT_READBACK_RESULT,
            self.runtime.children.runtime(child_id)["reconciliation_result"],
        )
        self.assertEqual(parent, self.service.task_repository.get(task_id))
        self.assertEqual("ready", await self._f3_status())
        self.assertEqual(1, self.dashboard.write_count)

        # Settled once: further passes read nothing and change nothing.
        await self._pass()
        await self._pass()
        self.assertEqual(1, self.dashboard.post_write_reads - reads)
        self.assertEqual(parent, self.service.task_repository.get(task_id))
        applied, _calls = await self._unrelated_apply()
        self.assertEqual("succeeded_verified", applied["task_state"])
        self.assertEqual(1, self.dashboard.write_count)

    async def test_live_shape_mismatch_is_verification_failure(self):
        task_id, child_id, parent = await self._live_shape()
        self.dashboard.configuration["title"] = "Drifted by another writer"

        await self.runtime.recover_once("periodic")

        child = self.runtime.children.get(child_id)
        self.assertTrue(child.terminal)
        self.assertEqual("verification_mismatch", child.normalized_outcome)
        self.assertEqual((), self.runtime.locks.records())
        self.assertEqual(parent, self.service.task_repository.get(task_id))
        self.assertEqual("ready", await self._f3_status())
        self.assertEqual(1, self.dashboard.write_count)

    async def test_live_shape_unavailable_is_bounded_indeterminate(self):
        task_id, child_id, parent = await self._live_shape()
        self.dashboard.fail_reads_after_write = 100
        reads = self.dashboard.post_write_reads

        await self.runtime.recover_once("periodic")

        child = self.runtime.children.get(child_id)
        self.assertTrue(child.terminal)
        self.assertEqual("manual_review_required", child.normalized_outcome)
        # Indeterminate with its reason, after one final unavailable read.
        self.assertEqual(
            {"manual_review_reason_code": "observation_evidence_exhausted"},
            child.evidence,
        )
        self.assertEqual(
            ["dashboard_readback_unavailable", "fallback_none"],
            [
                item["diagnostic_codes"]
                for item in child.events
                if item["event_type"] == "observation_recorded"
            ][-1],
        )
        self.assertEqual(1, self.dashboard.post_write_reads - reads)
        # Released, never promoted to a conflict hold: nothing will ever
        # reconcile this child again under its terminal parent.
        self.assertEqual((), self.runtime.locks.records())
        self.assertEqual(
            [], self.runtime.children.runtime(child_id)["selective_hold_tokens"]
        )
        self.assertEqual(parent, self.service.task_repository.get(task_id))
        self.assertNotEqual("recovering", await self._f3_status())
        await self._pass()
        self.assertEqual(1, self.dashboard.post_write_reads - reads)
        self.assertEqual(1, self.dashboard.write_count)

    async def test_live_shape_binding_failure_retries_without_any_read(self):
        task_id, child_id, parent = await self._live_shape()
        reads = self.dashboard.post_write_reads
        with patch.object(
            self.runtime,
            "_load_prepared",
            side_effect=GovernanceError(ErrorCode.POLICY_SNAPSHOT_MISMATCH),
        ):
            await self.runtime.recover_once("periodic")
            runtime = self.runtime.children.runtime(child_id)
            self.assertEqual("bounded_retry", runtime["reconciliation_result"])
            backoff = runtime["backoff_seconds"]
            self.assertTrue(5 <= backoff <= 300)
            with self.runtime._readiness_lock:
                self.assertIn(
                    "recovery_child:" + child_id,
                    self.runtime._readiness_faults,
                )
            # Inside the backoff nothing is attempted again.
            await self.runtime.recover_once("periodic")
            self.assertEqual(
                backoff,
                self.runtime.children.runtime(child_id)["backoff_seconds"],
            )
        self.assertFalse(self.runtime.children.get(child_id).terminal)
        self.assertEqual(0, self.dashboard.post_write_reads - reads)
        self.assertEqual(3, len(self.runtime.locks.records()))
        self.assertEqual(parent, self.service.task_repository.get(task_id))
        # After the backoff the exact binding resolves and one read settles.
        self.clock[0] += timedelta(seconds=backoff + 1)
        await self.runtime.recover_once("periodic")
        self.assertTrue(self.runtime.children.get(child_id).terminal)
        with self.runtime._readiness_lock:
            self.assertNotIn(
                "recovery_child:" + child_id, self.runtime._readiness_faults
            )
        self.assertEqual(1, self.dashboard.write_count)

    async def test_live_shape_other_adapter_is_not_read_or_released(self):
        # Only the dashboard family reads once at its expired deadline. A
        # child of any other adapter is neither collected nor read, so its
        # locks are never released without evidence.
        task_id, child_id, parent = await self._live_shape()
        reads = self.dashboard.post_write_reads
        with patch.object(
            runtime_module, "DASHBOARD_ADAPTER_ID", "configuration_operation"
        ):
            before = self.runtime.children.runtime(child_id)
            await self.runtime.recover_once("periodic")
            # The historical scan does not collect it at all.
            self.assertEqual(before, self.runtime.children.runtime(child_id))
            with self.runtime._readiness_lock:
                self.assertNotIn(
                    "recovery_child:" + child_id,
                    self.runtime._readiness_faults,
                )
            # The recovery step itself refuses a non-dashboard child too.
            declaration = self.runtime.children.declarations_for_task(
                task_id
            )[0]
            settled = await self.runtime._recover_terminal_parent_child(
                declaration, now=self.clock[0]
            )
        self.assertFalse(settled)
        self.assertEqual(
            "bounded_retry",
            self.runtime.children.runtime(child_id)["reconciliation_result"],
        )
        self.assertFalse(self.runtime.children.get(child_id).terminal)
        self.assertEqual(0, self.dashboard.post_write_reads - reads)
        self.assertEqual(3, len(self.runtime.locks.records()))
        self.assertEqual(parent, self.service.task_repository.get(task_id))
        self.assertEqual(1, self.dashboard.write_count)

    async def test_live_shape_restart_resumes_without_redispatch(self):
        task_id, child_id, parent = await self._live_shape()
        self._compose()
        await self.runtime.recover_once("startup")
        child = self.runtime.children.get(child_id)
        self.assertTrue(child.terminal)
        self.assertEqual("succeeded_verified", child.normalized_outcome)
        self.assertEqual(1, child.dispatch_count)
        self.assertEqual(1, self.dashboard.write_count)
        self.assertEqual((), self.runtime.locks.records())
        self.assertEqual(parent, self.service.task_repository.get(task_id))

    # -- Fix B and the dashboard final read ----------------------------------

    async def test_sweep_leaves_f3_dispatched_task_pending(self):
        task_id, child_id = await self._dispatch_observing(failed_reads=100)
        # F3 recovery cannot take over the active apply lease yet.
        result = await self._pass(recover=False)
        self.assertEqual(1, result["pending"])
        self.assertEqual(0, result["manual_review_required"])
        self.assertEqual(
            "observing", self.service.task_repository.get(task_id).state.value
        )
        self.assertFalse(self.runtime.children.get(child_id).terminal)
        self.assertEqual(1, self.dashboard.write_count)

    async def test_sweep_pending_is_bounded_by_maximum_deadline(self):
        task_id, _child_id = await self._dispatch_observing(failed_reads=100)
        self.clock[0] += timedelta(days=3)
        result = await self.service.reconcile_execution_tasks(
            trigger="periodic"
        )
        self.assertEqual(1, result["manual_review_required"])
        task = self.service.task_repository.get(task_id)
        self.assertEqual("manual_review_required", task.state.value)
        self.assertIn(
            "maximum_post_dispatch_deadline_exceeded", str(task.to_dict())
        )
        self.assertEqual(1, self.dashboard.write_count)

    async def test_end_to_end_transient_readback_then_verified(self):
        task_id, child_id = await self._dispatch_observing(failed_reads=1)
        for _ in range(4):
            await self._pass()
        task = self.service.task_repository.get(task_id)
        self.assertEqual("succeeded_verified", task.state.value)
        child = self.runtime.children.get(child_id)
        self.assertTrue(child.terminal)
        self.assertEqual("succeeded_verified", child.normalized_outcome)
        self.assertEqual((), self.runtime.locks.records())
        self.assertEqual(1, self.dashboard.write_count)
        self.assertEqual("ready", await self._f3_status())

    async def test_end_to_end_mismatch_is_verification_failure(self):
        task_id, child_id = await self._dispatch_observing(failed_reads=1)
        self.dashboard.configuration["title"] = "Drifted by another writer"
        for _ in range(4):
            await self._pass()
        task = self.service.task_repository.get(task_id)
        # The established dashboard projection of a readback mismatch.
        self.assertEqual("failed_post_dispatch", task.state.value)
        child = self.runtime.children.get(child_id)
        self.assertEqual("verification_mismatch", child.normalized_outcome)
        self.assertEqual((), self.runtime.locks.records())
        self.assertEqual(1, self.dashboard.write_count)

    async def test_end_to_end_unavailable_is_bounded_indeterminate(self):
        task_id, child_id = await self._dispatch_observing(failed_reads=100)
        for _ in range(4):
            await self._pass()
        task = self.service.task_repository.get(task_id)
        self.assertEqual("manual_review_required", task.state.value)
        child = self.runtime.children.get(child_id)
        self.assertTrue(child.terminal)
        # Indeterminate with its reason, after one final unavailable read.
        self.assertEqual(
            {"manual_review_reason_code": "observation_evidence_exhausted"},
            child.evidence,
        )
        self.assertEqual(
            ["dashboard_readback_unavailable", "fallback_none"],
            [
                item["diagnostic_codes"]
                for item in child.events
                if item["event_type"] == "observation_recorded"
            ][-1],
        )
        # The active-parent outcome keeps its existing selective hold on the
        # dashboard key only; shared dependency keys are free.
        records = self.runtime.locks.records()
        self.assertEqual(
            [("dashboard:main-operations", True)],
            [(item.key, item.conflict_hold) for item in records],
        )
        applied, _calls = await self._unrelated_apply()
        self.assertEqual("succeeded_verified", applied["task_state"])
        self.assertEqual(1, self.dashboard.write_count)

    async def test_end_to_end_restart_during_observation(self):
        task_id, child_id = await self._dispatch_observing(failed_reads=1)
        self._compose()
        await self.runtime.recover_once("startup")
        for _ in range(4):
            await self._pass()
        task = self.service.task_repository.get(task_id)
        self.assertEqual("succeeded_verified", task.state.value)
        self.assertEqual(1, self.runtime.children.get(child_id).dispatch_count)
        self.assertEqual(1, self.dashboard.write_count)
        self.assertEqual((), self.runtime.locks.records())

    # -- Fix D ---------------------------------------------------------------

    async def _terminal_child_with_unreleased_locks(self, *, mismatch: bool):
        created = await self._plan()
        await self._approve(created)
        if mismatch:
            self.dashboard.mismatched_write = True
        store = self.runtime.locks
        with patch.object(
            type(store), "release", side_effect=LockStorageError("synthetic")
        ):
            result = await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )
        child_id = self.runtime.children.declarations_for_task(
            result["task_id"]
        )[0]["child_id"]
        child = self.runtime.children.get(child_id)
        self.assertTrue(child.terminal)
        self.assertIsNotNone(child.dispatch_intent)
        self.assertEqual(3, len(store.records()))
        return result, child

    async def test_terminal_post_intent_expired_locks_are_released(self):
        result, child = await self._terminal_child_with_unreleased_locks(
            mismatch=False
        )
        self.assertEqual("succeeded_verified", child.normalized_outcome)
        # Live leases of the terminal owner are never released.
        await self.runtime.recover_once("periodic")
        self.assertEqual(3, len(self.runtime.locks.records()))
        self.clock[0] += timedelta(seconds=180)
        await self.runtime.recover_once("periodic")
        self.assertEqual((), self.runtime.locks.records())
        self.assertEqual(1, self.dashboard.write_count)
        del result

    async def test_terminal_mismatch_expired_locks_are_released(self):
        _result, child = await self._terminal_child_with_unreleased_locks(
            mismatch=True
        )
        self.assertEqual("verification_mismatch", child.normalized_outcome)
        self.clock[0] += timedelta(seconds=180)
        await self.runtime.recover_once("periodic")
        self.assertEqual((), self.runtime.locks.records())

    async def test_unresolved_manual_review_keeps_target_key_only(self):
        task_id, child_id = await self._dispatch_observing(failed_reads=100)
        store = self.runtime.locks
        with patch.object(
            type(store),
            "promote_to_conflict_hold",
            side_effect=LockStorageError("synthetic"),
        ):
            for _ in range(4):
                await self._pass()
        child = self.runtime.children.get(child_id)
        self.assertTrue(child.terminal)
        self.assertEqual("manual_review_required", child.normalized_outcome)
        self.clock[0] += timedelta(seconds=180)
        await self.runtime.recover_once("periodic")
        records = store.records()
        self.assertEqual(
            [("dashboard:main-operations", False)],
            [(item.key, item.conflict_hold) for item in records],
        )
        applied, _calls = await self._unrelated_apply()
        self.assertEqual("succeeded_verified", applied["task_state"])
        self.assertEqual(1, self.dashboard.write_count)
        del task_id

    async def test_terminal_post_intent_release_requires_exact_lock_owner(
        self,
    ):
        # A terminal owner releases only the locks its own durable tokens
        # name. An expired record with any other owner stays retained.
        _result, child = await self._terminal_child_with_unreleased_locks(
            mismatch=False
        )
        self.assertEqual("succeeded_verified", child.normalized_outcome)
        self.clock[0] += timedelta(seconds=180)
        store = self.runtime.locks
        expired = store.expired_records

        def foreign_dashboard_owner(*, now=None):
            return tuple(
                replace(item, owner_id="foreign-owner")
                if item.key == "dashboard:main-operations"
                else item
                for item in expired(now=now)
            )

        with patch.object(
            store, "expired_records", side_effect=foreign_dashboard_owner
        ):
            await self.runtime.recover_once("periodic")
        self.assertEqual(
            ["dashboard:main-operations"],
            [item.key for item in store.records()],
        )
        self.assertEqual(1, self.dashboard.write_count)

    async def test_terminal_parent_readback_refuses_an_active_parent(self):
        # Collection already requires a terminal parent; the recovery step
        # re-checks it, so an active parent's child is never read here.
        task_id, child_id = await self._dispatch_observing(failed_reads=100)
        self.clock[0] += timedelta(seconds=180)
        self.dashboard.fail_reads_after_write = 0
        parent = self.service.task_repository.get(task_id)
        before = self.runtime.children.runtime(child_id)
        reads = self.dashboard.post_write_reads
        declaration = self.runtime.children.declarations_for_task(task_id)[0]
        settled = await self.runtime._recover_terminal_parent_child(
            declaration, now=self.clock[0]
        )
        self.assertFalse(settled)
        self.assertEqual(0, self.dashboard.post_write_reads - reads)
        self.assertEqual(before, self.runtime.children.runtime(child_id))
        self.assertFalse(self.runtime.children.get(child_id).terminal)
        self.assertEqual(3, len(self.runtime.locks.records()))
        self.assertEqual(parent, self.service.task_repository.get(task_id))
        self.assertEqual(1, self.dashboard.write_count)

    # -- Unit boundaries -----------------------------------------------------

    async def test_terminal_parent_readback_requires_readback_only(self):
        with self.assertRaises(GovernanceError) as error:
            await self.runtime._execute_child(
                None, None, {}, None, (), terminal_parent_readback=True
            )
        self.assertEqual(
            ErrorCode.POLICY_SNAPSHOT_MISMATCH, error.exception.code
        )

    def test_terminal_parent_lock_store_releases_instead_of_holding(self):
        class _Store:
            def __init__(self):
                self.calls = []

            def release(self, handle):
                self.calls.append(("release", handle))

            def promote_to_conflict_hold(self, handle, *, reason_code):
                self.calls.append(("hold", handle))

            def validate_handle(self, handle, *, now):
                self.calls.append(("validate", handle))

        store = _Store()
        proxy = _TerminalParentReadbackLockStore(store)
        proxy.promote_to_conflict_hold("h", reason_code="x")
        proxy.validate_handle("h", now=None)
        self.assertEqual([("release", "h"), ("validate", "h")], store.calls)

    def test_sweep_ownership_requires_f3_authority_and_runtime(self):
        class _Task:
            task_id = "task"
            legacy_projection = {"execution_authority": "legacy"}

        class _Runtime:
            def __init__(self, result):
                self.result = result

            def owns_post_intent_reconciliation(self, task_id):
                if isinstance(self.result, Exception):
                    raise self.result
                return self.result

        task = _Task()
        self.service.f3_runtime = _Runtime(True)
        self.assertFalse(self.service._f3_owns_post_dispatch_reconciliation(task))
        task.legacy_projection = {"execution_authority": F3_EXECUTION_AUTHORITY}
        self.assertTrue(self.service._f3_owns_post_dispatch_reconciliation(task))
        self.service.f3_runtime = _Runtime(False)
        self.assertFalse(self.service._f3_owns_post_dispatch_reconciliation(task))
        self.service.f3_runtime = _Runtime("yes")
        self.assertFalse(self.service._f3_owns_post_dispatch_reconciliation(task))
        self.service.f3_runtime = _Runtime(RuntimeError("unreadable"))
        self.assertTrue(self.service._f3_owns_post_dispatch_reconciliation(task))
        self.service.f3_runtime = None
        self.assertFalse(self.service._f3_owns_post_dispatch_reconciliation(task))

    async def test_scan_collects_only_children_of_terminal_parents(self):
        def collected(task_id):
            return self.runtime._reconcile_orphaned_children(
                declarations=tuple(
                    self.runtime.children.declarations_for_task(task_id)
                ),
                now=self.clock[0],
                sweep_started=0.0,
                transition_limit=10,
                monotonic=lambda: 0.0,
            )["terminal_parent_readbacks"]

        # A nonterminal parent's dispatched child belongs to active recovery.
        task_id, child_id = await self._dispatch_observing(failed_reads=100)
        self.assertEqual((), collected(task_id))
        with patch.object(
            self.service,
            "_f3_owns_post_dispatch_reconciliation",
            return_value=False,
        ):
            await self._pass()
        self.assertEqual(
            "manual_review_required",
            self.service.task_repository.get(task_id).state.value,
        )
        self.clock[0] += timedelta(days=2)
        self.assertEqual(
            [child_id], [item["child_id"] for item in collected(task_id)]
        )

    def test_terminal_post_intent_key_release_rule(self):
        class _Record:
            def __init__(self, *, terminal=True, intent=True, outcome):
                self.terminal = terminal
                self.dispatch_intent = {"request_id": "r"} if intent else None
                self.normalized_outcome = outcome

        releasable = F3RuntimeIntegration._terminal_post_intent_key_releasable
        held = {"selective_hold_keys": ["dashboard:main-operations"]}
        verified = _Record(outcome="succeeded_verified")
        review = _Record(outcome="manual_review_required")
        self.assertTrue(releasable(held, verified, "dashboard:main-operations"))
        self.assertFalse(
            releasable(held, _Record(terminal=False, outcome="observing"), "x")
        )
        self.assertFalse(
            releasable(held, _Record(intent=False, outcome="succeeded_verified"), "x")
        )
        self.assertFalse(releasable(held, review, "dashboard:main-operations"))
        self.assertTrue(releasable(held, review, "home_assistant:core"))
        # Unknown or empty declared hold keys retain every key.
        for declaration in (
            {},
            {"selective_hold_keys": None},
            {"selective_hold_keys": []},
            {"selective_hold_keys": "home_assistant:core"},
        ):
            self.assertFalse(
                releasable(declaration, review, "home_assistant:core")
            )

    async def test_runtime_ownership_requires_a_dispatch_intent(self):
        self.assertFalse(
            self.runtime.owns_post_intent_reconciliation("missing-task")
        )
        dispatched, _child_id = await self._dispatch_observing(failed_reads=1)
        self.assertTrue(
            self.runtime.owns_post_intent_reconciliation(dispatched)
        )


if __name__ == "__main__":
    unittest.main()
