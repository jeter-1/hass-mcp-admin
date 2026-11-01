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
  are never released;
* (R1) the terminal-parent lock cleanup is crash-safe: a durable marker is
  written before the readback can terminalize the child and cleared only
  after its last exact-owner lock is released, so a fault or process stop at
  any point resumes on a later sweep without another read or dispatch.
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
from ha_mcp_engineering.f3_runtime import runtime as runtime_module  # noqa: E402
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3_EXECUTION_AUTHORITY,
    F3RuntimeIntegration,
    LockStorageError,
    TERMINAL_PARENT_CLEANUP_PENDING,
    TERMINAL_PARENT_READBACK_PENDING,
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
LIVE_LOCKS = {
    ("addon:ha_mcp", "shared"),
    ("dashboard:main-operations", "exclusive"),
    ("home_assistant:core", "shared"),
}
SWEEP_STEP = timedelta(seconds=40)


class _Crash(BaseException):
    """Abrupt process stop: escapes every ``except Exception`` handler."""


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

        # Settled once: a direct readback of the terminal child is refused
        # before any preparation, and further passes read nothing.
        settled_child = self.runtime.children.get(child_id)
        settled_runtime = self.runtime.children.runtime(child_id)
        with patch.object(
            self.runtime,
            "_load_prepared",
            wraps=self.runtime._load_prepared,
        ) as load_prepared:
            self.assertFalse(
                await self.runtime._recover_terminal_parent_child(
                    self.runtime.children.declarations_for_task(task_id)[0],
                    now=self.clock[0],
                )
            )
        load_prepared.assert_not_called()
        self.assertEqual(settled_child, self.runtime.children.get(child_id))
        self.assertEqual(
            settled_runtime, self.runtime.children.runtime(child_id)
        )
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

    async def test_live_shape_changed_plan_is_never_prepared_or_read(self):
        # The persisted plan must still hash to the child's bound plan hash
        # before anything is prepared, read or released.
        task_id, child_id, parent = await self._live_shape()
        reads = self.dashboard.post_write_reads
        declaration = self.runtime.children.declarations_for_task(task_id)[0]
        with patch.object(
            self.service, "plan_hash", return_value="0" * 64
        ), patch.object(
            self.runtime, "_load_prepared", wraps=self.runtime._load_prepared
        ) as load_prepared:
            settled = await self.runtime._recover_terminal_parent_child(
                declaration, now=self.clock[0]
            )
        self.assertFalse(settled)
        load_prepared.assert_not_called()
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

    async def test_terminal_parent_readback_inside_lease_waits_bounded(self):
        # The pre-fix sweep terminalized the parent about 30 s after
        # dispatch, while the apply lease was still active. An unsettled
        # readback waits with a bounded backoff rather than spinning.
        task_id, child_id = await self._dispatch_observing(failed_reads=100)
        with patch.object(
            self.service,
            "_f3_owns_post_dispatch_reconciliation",
            return_value=False,
        ):
            await self._pass()
        parent = self.service.task_repository.get(task_id)
        self.assertEqual("manual_review_required", parent.state.value)
        # Past active recovery's backoff, still inside the 120 s lease.
        self.clock[0] += timedelta(seconds=31)
        await self.runtime.recover_once("periodic")
        runtime = self.runtime.children.runtime(child_id)
        self.assertEqual(
            TERMINAL_PARENT_READBACK_PENDING, runtime["reconciliation_result"]
        )
        self.assertTrue(30 <= runtime["backoff_seconds"] <= 300)
        self.assertGreater(
            datetime.fromisoformat(runtime["next_eligible_at"]),
            self.clock[0],
        )
        self.assertFalse(self.runtime.children.get(child_id).terminal)
        self.assertEqual(3, len(self.runtime.locks.records()))
        self.assertEqual(parent, self.service.task_repository.get(task_id))
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
        # A terminal parent without F3 authority, or one that cannot be
        # read, is never collected.
        parent = self.service.task_repository.get(task_id)
        legacy = replace(
            parent,
            legacy_projection={
                **dict(parent.legacy_projection),
                "execution_authority": "legacy",
            },
        )
        repository = self.service.task_repository
        with patch.object(repository, "get", return_value=legacy):
            self.assertEqual((), collected(task_id))
        with patch.object(repository, "get", side_effect=OSError("synthetic")):
            self.assertEqual((), collected(task_id))
        # Under the same terminal parent, an already terminal child and a
        # child without a dispatch intent are never collected for a read.
        children = self.runtime.children
        record = children.get(child_id)
        for shape in (
            replace(record, terminal=True),
            replace(record, dispatch_intent=None),
        ):
            with patch.object(children, "get", return_value=shape):
                self.assertEqual((), collected(task_id))
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


    # -- R1: crash-safe, resumable terminal-parent lock cleanup ---------------

    def _parent_bytes(self, task_id):
        return self.service.task_repository._path(task_id).read_bytes()

    def _marker(self, child_id):
        return self.runtime.children.runtime(child_id)["reconciliation_result"]

    async def _stop_during_cleanup(self, *, unavailable: bool, stop):
        """Run the first terminal-parent recovery pass until ``stop`` fires."""

        task_id, child_id, _parent = await self._live_shape()
        before = self._parent_bytes(task_id)
        if unavailable:
            self.dashboard.fail_reads_after_write = 100
        reads = self.dashboard.post_write_reads
        with stop():
            with self.assertRaises(_Crash):
                await self.runtime.recover_once("periodic")
        child = self.runtime.children.get(child_id)
        self.assertTrue(child.terminal)
        self.assertEqual(
            "manual_review_required" if unavailable else "succeeded_verified",
            child.normalized_outcome,
        )
        self.assertEqual(TERMINAL_PARENT_CLEANUP_PENDING, self._marker(child_id))
        self.assertEqual(1, self.dashboard.post_write_reads - reads)
        self.assertEqual(before, self._parent_bytes(task_id))
        return task_id, child_id, before, reads

    async def _restart_and_assert_settled(
        self, task_id, child_id, before, reads, *, outcome
    ):
        self.dashboard.fail_reads_after_write = 0
        self.clock[0] += timedelta(minutes=10)
        self._compose()
        await self.runtime.recover_once("startup")
        await self._pass()
        child = self.runtime.children.get(child_id)
        self.assertEqual(outcome, child.normalized_outcome)
        self.assertEqual(1, child.dispatch_count)
        self.assertEqual((), self.runtime.locks.records())
        self.assertEqual(TERMINAL_PARENT_READBACK_RESULT, self._marker(child_id))
        self.assertNotEqual("recovering", await self._f3_status())
        self.assertEqual(before, self._parent_bytes(task_id))
        self.assertEqual(1, self.dashboard.post_write_reads - reads)
        self.assertEqual(1, self.dashboard.write_count)
        # Repeated sweeps and restarts are idempotent: nothing is re-read,
        # rewritten or released again.
        settled = self.runtime.children.get(child_id)
        settled_runtime = self.runtime.children.runtime(child_id)
        for _ in range(2):
            self._compose()
            await self.runtime.recover_once("startup")
            await self._pass()
        self.assertEqual(settled, self.runtime.children.get(child_id))
        self.assertEqual(settled_runtime, self.runtime.children.runtime(child_id))
        self.assertEqual(before, self._parent_bytes(task_id))
        self.assertEqual(1, self.dashboard.post_write_reads - reads)
        # The Step 1 regression shape: an unrelated governed apply succeeds.
        applied, _calls = await self._unrelated_apply()
        self.assertEqual("succeeded_verified", applied["task_state"])
        self.assertEqual(1, self.dashboard.write_count)

    def _assert_cleanup_pending_after_restart(self, child_id):
        # The stopped process is faulted; a fresh composition reads only the
        # durable state, which still reports the unfinished cleanup as
        # pending reconciliation work rather than settled.
        self._compose()
        self.assertIn(child_id, repr(self.runtime.reconciliation_items()))
        self.assertNotEqual("ready", self.runtime._health()["status"])

    def _stop_at_release(self):
        return patch.object(
            type(self.runtime.locks), "release", side_effect=_Crash()
        )

    async def test_r1_stop_after_terminal_before_any_release(self):
        task_id, child_id, before, reads = await self._stop_during_cleanup(
            unavailable=True, stop=self._stop_at_release
        )
        self.assertEqual(LIVE_LOCKS, {
            (item.key, item.mode) for item in self.runtime.locks.records()
        })
        self._assert_cleanup_pending_after_restart(child_id)
        await self._restart_and_assert_settled(
            task_id, child_id, before, reads, outcome="manual_review_required"
        )

    async def test_r1_stop_after_verified_terminal_before_any_release(self):
        task_id, child_id, before, reads = await self._stop_during_cleanup(
            unavailable=False, stop=self._stop_at_release
        )
        self.assertEqual(3, len(self.runtime.locks.records()))
        await self._restart_and_assert_settled(
            task_id, child_id, before, reads, outcome="succeeded_verified"
        )

    async def test_r1_stop_after_partial_release(self):
        store_type = type(self.runtime.locks)
        original = store_type.release

        def release_one_then_stop(store, handle, **kwargs):
            original(store, replace(handle, tokens=handle.tokens[:1]), **kwargs)
            raise _Crash()

        task_id, child_id, before, reads = await self._stop_during_cleanup(
            unavailable=True,
            stop=lambda: patch.object(
                store_type, "release", release_one_then_stop
            ),
        )
        self.assertEqual(2, len(self.runtime.locks.records()))
        await self._restart_and_assert_settled(
            task_id, child_id, before, reads, outcome="manual_review_required"
        )

    async def test_r1_stop_after_all_releases_before_marker_clears(self):
        original = F3RuntimeIntegration._release_terminal_parent_child_locks

        def release_then_stop(runtime, declaration, record):
            original(runtime, declaration, record)
            raise _Crash()

        task_id, child_id, before, reads = await self._stop_during_cleanup(
            unavailable=True,
            stop=lambda: patch.object(
                F3RuntimeIntegration,
                "_release_terminal_parent_child_locks",
                release_then_stop,
            ),
        )
        self.assertEqual((), self.runtime.locks.records())
        self._assert_cleanup_pending_after_restart(child_id)
        await self._restart_and_assert_settled(
            task_id, child_id, before, reads, outcome="manual_review_required"
        )

    async def test_r1_stop_after_marker_before_terminal_reads_once(self):
        # The marker is durable before the executor runs. A stop before the
        # child is terminal leaves a nonterminal child that still receives
        # its one readback; the marker alone never skips that read.
        task_id, child_id, _parent = await self._live_shape()
        before = self._parent_bytes(task_id)
        reads = self.dashboard.post_write_reads
        with patch.object(
            F3RuntimeIntegration, "_execute_child", side_effect=_Crash()
        ):
            with self.assertRaises(_Crash):
                await self.runtime.recover_once("periodic")
        self.assertFalse(self.runtime.children.get(child_id).terminal)
        self.assertEqual(TERMINAL_PARENT_CLEANUP_PENDING, self._marker(child_id))
        self.assertEqual(0, self.dashboard.post_write_reads - reads)
        self.assertEqual(3, len(self.runtime.locks.records()))
        self._compose()
        await self.runtime.recover_once("startup")
        child = self.runtime.children.get(child_id)
        self.assertEqual("succeeded_verified", child.normalized_outcome)
        self.assertEqual((), self.runtime.locks.records())
        self.assertEqual(TERMINAL_PARENT_READBACK_RESULT, self._marker(child_id))
        self.assertEqual(1, self.dashboard.post_write_reads - reads)
        self.assertEqual(before, self._parent_bytes(task_id))
        self.assertEqual(1, self.dashboard.write_count)

    async def test_r1_transient_release_failure_resumes_without_projection(
        self,
    ):
        # The reviewer's reproduction: release fails transiently during the
        # first pass and the executor suppresses it after persisting the
        # terminal child. Cleanup is not reported settled; the next eligible
        # sweep in the same process (with its readiness fault still set)
        # finishes it through the cleanup path, never terminal projection.
        task_id, child_id, _parent = await self._live_shape()
        before = self._parent_bytes(task_id)
        self.dashboard.fail_reads_after_write = 100
        reads = self.dashboard.post_write_reads
        with patch.object(
            type(self.runtime.locks),
            "release",
            side_effect=LockStorageError("synthetic transient write failure"),
        ):
            await self.runtime.recover_once("periodic")
        child = self.runtime.children.get(child_id)
        self.assertEqual("manual_review_required", child.normalized_outcome)
        self.assertEqual(3, len(self.runtime.locks.records()))
        self.assertEqual(TERMINAL_PARENT_CLEANUP_PENDING, self._marker(child_id))
        self.assertIsNotNone(
            self.runtime.children.runtime(child_id)["next_eligible_at"]
        )
        self.assertNotEqual("ready", await self._f3_status())
        self.clock[0] += timedelta(minutes=10)
        with patch.object(
            self.runtime,
            "_reconcile_terminal_child_fault",
            side_effect=AssertionError("terminal projection"),
        ), patch.object(
            self.runtime, "_load_prepared", side_effect=AssertionError("read")
        ):
            await self.runtime.recover_once("periodic")
        self.assertEqual((), self.runtime.locks.records())
        self.assertEqual(TERMINAL_PARENT_READBACK_RESULT, self._marker(child_id))
        self.assertEqual(before, self._parent_bytes(task_id))
        self.assertEqual(1, self.dashboard.post_write_reads - reads)
        await self._restart_and_assert_settled(
            task_id, child_id, before, reads, outcome="manual_review_required"
        )

    async def _pending_cleanup(self):
        task_id, child_id, before, _reads = await self._stop_during_cleanup(
            unavailable=True, stop=self._stop_at_release
        )
        self._compose()
        declaration = self.runtime.children.declarations_for_task(task_id)[0]
        return task_id, child_id, before, declaration

    async def _assert_cleanup_refused(self, task_id, child_id, before,
                                      declaration, *, retry: bool):
        runtime_before = self.runtime.children.runtime(child_id)
        self.assertFalse(
            self.runtime._finish_terminal_parent_child_cleanup(
                declaration, now=self.clock[0]
            )
        )
        self.assertEqual(3, len(self.runtime.locks.records()))
        self.assertEqual(TERMINAL_PARENT_CLEANUP_PENDING, self._marker(child_id))
        runtime_after = self.runtime.children.runtime(child_id)
        if retry:
            self.assertGreater(
                datetime.fromisoformat(runtime_after["next_eligible_at"]),
                self.clock[0],
            )
        else:
            self.assertEqual(runtime_before, runtime_after)
        self.assertEqual(before, self._parent_bytes(task_id))
        self.assertEqual(1, self.dashboard.write_count)

    async def test_r1_cleanup_refuses_a_foreign_lock_owner(self):
        task_id, child_id, before, declaration = await self._pending_cleanup()
        records = self.runtime.locks.records

        def foreign(*args, **kwargs):
            return tuple(
                replace(item, owner_id="foreign-owner")
                if item.key == "dashboard:main-operations" else item
                for item in records(*args, **kwargs)
            )

        with patch.object(self.runtime.locks, "records", side_effect=foreign):
            await self._assert_cleanup_refused(
                task_id, child_id, before, declaration, retry=True
            )

    async def test_r1_cleanup_refuses_a_changed_lock_mode(self):
        # Mode is not part of the store's release fence; the exact-authority
        # check is what refuses it.
        task_id, child_id, before, declaration = await self._pending_cleanup()
        records = self.runtime.locks.records

        def shared(*args, **kwargs):
            return tuple(
                replace(item, mode="shared")
                if item.key == "dashboard:main-operations" else item
                for item in records(*args, **kwargs)
            )

        with patch.object(self.runtime.locks, "records", side_effect=shared):
            await self._assert_cleanup_refused(
                task_id, child_id, before, declaration, retry=True
            )

    async def test_r1_cleanup_never_releases_a_conflict_hold(self):
        task_id, child_id, before, declaration = await self._pending_cleanup()
        records = self.runtime.locks.records

        def held(*args, **kwargs):
            return tuple(
                replace(item, conflict_hold=True)
                if item.key == "dashboard:main-operations" else item
                for item in records(*args, **kwargs)
            )

        with patch.object(self.runtime.locks, "records", side_effect=held):
            await self._assert_cleanup_refused(
                task_id, child_id, before, declaration, retry=True
            )

    async def test_r1_cleanup_requires_a_terminal_f3_parent(self):
        task_id, child_id, before, declaration = await self._pending_cleanup()
        repository = self.service.task_repository
        parent = repository.get(task_id)
        for substitute in (
            replace(parent, state=type(parent.state)("observing")),
            replace(parent, legacy_projection={}),
            None,
        ):
            with self.subTest(substitute=getattr(substitute, "state", None)):
                with patch.object(repository, "get", return_value=substitute):
                    await self._assert_cleanup_refused(
                        task_id, child_id, before, declaration, retry=False
                    )

    async def test_r1_cleanup_requires_the_dashboard_adapter(self):
        task_id, child_id, before, declaration = await self._pending_cleanup()
        children = self.runtime.children
        get = children.get

        def other_adapter(child):
            record = get(child)
            return replace(record, adapter_id="configuration")

        with patch.object(children, "get", side_effect=other_adapter):
            await self._assert_cleanup_refused(
                task_id, child_id, before, declaration, retry=False
            )

    async def test_r1_cleanup_keeps_marker_while_locks_remain(self):
        task_id, child_id, before, declaration = await self._pending_cleanup()
        with patch.object(
            self.runtime, "_release_terminal_parent_child_locks", return_value=1
        ):
            await self._assert_cleanup_refused(
                task_id, child_id, before, declaration, retry=True
            )

    async def test_r1_cleanup_never_releases_a_live_nonterminal_owner(self):
        # A marker on a nonterminal child (stop before terminal persistence)
        # is not cleanup authority: the release step itself refuses.
        task_id, child_id, _parent = await self._live_shape()
        declaration = self.runtime.children.declarations_for_task(task_id)[0]
        record = self.runtime.children.get(child_id)
        with self.assertRaises(GovernanceError) as error:
            self.runtime._release_terminal_parent_child_locks(
                declaration, record
            )
        self.assertEqual(
            ErrorCode.EXECUTION_TASK_INVALID_STATE, error.exception.code
        )
        self.runtime.children.update_runtime(
            child_id,
            changes={"reconciliation_result": TERMINAL_PARENT_CLEANUP_PENDING},
        )
        runtime_before = self.runtime.children.runtime(child_id)
        self.assertFalse(
            self.runtime._finish_terminal_parent_child_cleanup(
                declaration, now=self.clock[0]
            )
        )
        self.assertEqual(3, len(self.runtime.locks.records()))
        self.assertEqual(
            runtime_before, self.runtime.children.runtime(child_id)
        )

    async def test_r1_ordinary_manual_review_hold_is_not_cleanup(self):
        # An active-parent manual-review child never carries the marker, so
        # its declared target key stays retained across sweeps and restarts.
        task_id, child_id = await self._dispatch_observing(failed_reads=100)
        with patch.object(
            type(self.runtime.locks),
            "promote_to_conflict_hold",
            side_effect=LockStorageError("synthetic"),
        ):
            for _ in range(4):
                await self._pass()
        self.assertNotEqual(
            TERMINAL_PARENT_CLEANUP_PENDING, self._marker(child_id)
        )
        self.clock[0] += timedelta(seconds=180)
        for _ in range(2):
            self._compose()
            await self.runtime.recover_once("startup")
            await self._pass()
        self.assertEqual(
            ["dashboard:main-operations"],
            [item.key for item in self.runtime.locks.records()],
        )
        del task_id


if __name__ == "__main__":
    unittest.main()
