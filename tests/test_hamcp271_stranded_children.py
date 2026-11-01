"""HAMCP-271: stranded F3 children under terminal parents in other families.

HAMCP-263 settled the dashboard family. Every configuration family and the
operational exact helper-state and input-select families have the same 120 s
apply lease and evidence window, so a dispatched child whose parent was
decided by the pre-HAMCP-263 generic sweep stays ``observing`` forever under
the terminal parent and its expired locks (``home_assistant:core`` shared)
block every later governed configuration apply. These tests reproduce that
shape per family and prove, offline and with exactly one provider write on
every path, that the generalized HAMCP-263 mechanism settles it with one
read-only readback while the terminal parent record stays unchanged.
"""

from __future__ import annotations

import copy
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
from types import SimpleNamespace  # noqa: E402

from ha_mcp_engineering.f3_runtime.registry import (  # noqa: E402
    OPERATIONAL_REGISTRATIONS,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    configuration_rollback_eligibility as inverse_eligibility,
)
from ha_mcp_engineering.governance.models import ChangeOperation  # noqa: E402
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
    LockStorageError,
    TERMINAL_PARENT_CLEANUP_PENDING,
    TERMINAL_PARENT_READBACK_RESULT,
)
from ha_mcp_engineering.governance.helper_state import (  # noqa: E402
    HelperStateGatewayError,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.errors import HomeAssistantUnavailableError  # noqa: E402
from ha_mcp_engineering.f3.contracts import (  # noqa: E402
    NormalizedOperationOutcome,
)
from ha_mcp_engineering.f3_configuration.adapter import (  # noqa: E402
    ConfigurationOperationAdapter,
)
from ha_mcp_engineering.f3_configuration.delete import (  # noqa: E402
    ConfigurationDeleteAdapter,
)
from ha_mcp_engineering.f3_configuration.helper_options import (  # noqa: E402
    HelperOptionsAdapter,
)
from tests.f3_configuration_fixtures import (  # noqa: E402
    ConfigurationLifecycleHarness,
    SyntheticConfigurationGateway,
    adapter_for,
    proposal_for,
    recovery_context,
)
from tests.hamcp145_input_select_fixtures import (  # noqa: E402
    ENTITY as SELECT_ENTITY,
    InputSelectServiceTestCase,
    _provider_identity as select_provider_identity,
)
from tests.test_beta37_exact_helper_state import (  # noqa: E402
    FakeDependencyRiskReader,
    FakeHelperStateGateway,
)
from tests.test_dev14_configuration_plans import (  # noqa: E402
    PROPOSED_SCRIPT,
    ConfigurationPlanTestCase,
    FakeConfigurationGateway,
)
from tests.test_f3_runtime_integration import (  # noqa: E402
    _ExactFakeConfigurationGateway,
)

LIVE_REASON = "dispatched_task_has_no_readback_reconciler"
SWEEP_STEP = timedelta(seconds=40)


class _ReadbackConfigurationGateway(FakeConfigurationGateway):
    """Configuration fake whose post-write readbacks can be unavailable."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_reads_after_write = 0
        self.post_write_reads = 0
        self.watch = None

    async def read(self, resource_type, resource_id):
        if (resource_type, resource_id) == self.watch and any(
            call[0] == "write" and call[2:] == self.watch
            for call in self.calls
        ):
            self.post_write_reads += 1
            if self.fail_reads_after_write > 0:
                self.fail_reads_after_write -= 1
                raise RuntimeError("synthetic readback unavailable")
        return await super().read(resource_type, resource_id)

    def writes(self, target=None):
        return [
            call
            for call in self.calls
            if call[0] == "write" and (target is None or call[2:] == target)
        ]


class _ReadbackHelperStateGateway(FakeHelperStateGateway):
    """Helper-state fake whose post-dispatch reads can be unavailable."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_reads_after_write = 0
        self.post_write_reads = 0

    async def read_state(self, entity_id: str):
        if self.dispatch_count:
            self.post_write_reads += 1
            if self.fail_reads_after_write > 0:
                self.fail_reads_after_write -= 1
                raise HelperStateGatewayError("state_unavailable")
        return await super().read_state(entity_id)


class _StrandedFamilyHarness(unittest.IsolatedAsyncioTestCase):
    create_automation_plan = ConfigurationPlanTestCase.create_automation_plan
    approve = ConfigurationPlanTestCase.approve

    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.clock = [datetime.now(timezone.utc)]
        self.gateway = _ReadbackConfigurationGateway()
        self.helper = _ReadbackHelperStateGateway()
        self.dependency = FakeDependencyRiskReader(self.helper.entity_id)
        self._compose()
        await self.runtime.recover_once("startup")

    def _compose(self):
        """Compose one process over the durable storage (also a restart)."""

        self.repository = ChangePlanRepository(self.root / "plans")
        self.service = ChangeGovernanceService(
            self.repository,
            self.gateway,
            AuditLogger(str(self.root / "audit.jsonl"), "test-access-secret"),
            helper_state_gateway=self.helper,
            helper_dependency_risk_reader=self.dependency,
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
            helper_state_gateway=self.helper,
            provider_identity_reader=(
                dashboard_suite._unrelated_lifecycle_provider_identity
            ),
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime

    async def asyncTearDown(self):
        self.temporary.cleanup()

    # -- family drivers ------------------------------------------------------

    async def _script_plan(self):
        return await self.service.create_configuration_plan(
            title="Governed script verification fixture",
            description="HAMCP-271 stranded configuration child",
            operations=[
                {
                    "operation_id": "update_comfort_script",
                    "resource_type": "script",
                    "action": "update",
                    "target_id": "set_hvac_comfort",
                    "depends_on": [],
                    "proposed_config": copy.deepcopy(PROPOSED_SCRIPT),
                }
            ],
        )

    async def _dispatch_script_observing(self, *, failed_reads: int):
        created = await self._script_plan()
        await self.approve(created)
        self.gateway.watch = ("script", "set_hvac_comfort")
        self.gateway.fail_reads_after_write = failed_reads
        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual("observing", result["task_state"], result)
        self.assertEqual(1, len(self.gateway.writes(self.gateway.watch)))
        return result["task_id"]

    async def _helper_plan(self):
        created = await self.service.create_helper_state_plan(
            entity_id=self.helper.entity_id, desired_state="on"
        )
        plan = created["plan"]
        pending = self.service.approve(plan["plan_id"], plan["plan_hash"])
        _, csrf = await self.service.issue_external_csrf(
            plan["plan_id"], pending["challenge_id"]
        )
        await self.service.decide_external_approval(
            plan_id=plan["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=plan["plan_hash"],
            approval_kind="apply",
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal="home_assistant_admin_ingress:hamcp271",
        )
        return plan

    async def _dispatch_helper_observing(self, *, failed_reads: int):
        plan = await self._helper_plan()
        self.helper.fail_reads_after_write = failed_reads
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual("observing", result["task_state"], result)
        self.assertEqual(1, self.helper.dispatch_count)
        return result["task_id"]

    # -- shared steps --------------------------------------------------------

    async def _pass(self, *, sweep: bool = True, recover: bool = True):
        self.clock[0] += SWEEP_STEP
        if recover:
            await self.runtime.recover_once("periodic")
        if sweep:
            return await self.service.reconcile_execution_tasks(
                trigger="periodic"
            )
        return None

    async def _strand(self, task_id: str):
        """Reproduce the deployed shape with the pre-HAMCP-263 sweep."""

        child_id = self.runtime.children.declarations_for_task(task_id)[0][
            "child_id"
        ]
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
        records = self.runtime.locks.records()
        self.assertIn(
            ("home_assistant:core", "shared"),
            {(item.key, item.mode) for item in records},
        )
        self.assertTrue(
            all(
                not item.conflict_hold
                and datetime.fromisoformat(item.lease_expires_at)
                < self.clock[0]
                for item in records
            )
        )
        return child_id, parent

    async def _unrelated_apply(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        writes = len(self.gateway.writes(("automation", "apply_hvac_comfort")))
        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        return result, (
            len(self.gateway.writes(("automation", "apply_hvac_comfort")))
            - writes
        )

    def _assert_blocked(self, blocked, writes):
        self.assertEqual("failed_pre_dispatch", blocked["task_state"])
        blocked_child = self.runtime.children.get(
            self.runtime.children.declarations_for_task(
                blocked["task_id"]
            )[0]["child_id"]
        )
        self.assertEqual("lock_conflict", blocked_child.normalized_outcome)
        self.assertEqual(0, writes)


class _Crash(BaseException):
    """Abrupt process stop: escapes every ``except Exception`` handler."""


class _StrandedFamilyMatrix:
    """Per-family matrix; a concrete class supplies the family driver.

    Driver: ``_dispatch_observing(failed_reads=...)``, ``_reads_unavailable``,
    ``_post_write_reads``, ``_provider_writes``, ``_drift`` and
    ``_unrelated_apply``.
    """

    # -- shared steps --------------------------------------------------------

    def _parent_bytes(self, task_id):
        return self.service.task_repository._path(task_id).read_bytes()

    def _marker(self, child_id):
        return self.runtime.children.runtime(child_id)["reconciliation_result"]

    async def _f3_status(self):
        summary = await self.service.async_health_summary()
        return summary["f3"]["status"]

    async def _live_shape(self):
        task_id = await self._dispatch_observing(failed_reads=100)
        child_id, parent = await self._strand(task_id)
        self._reads_unavailable(0)
        return task_id, child_id, parent

    def _assert_settled(self, task_id, child_id, before, reads, *, outcome):
        child = self.runtime.children.get(child_id)
        self.assertTrue(child.terminal)
        self.assertEqual(outcome, child.normalized_outcome)
        self.assertEqual(1, child.dispatch_count)
        self.assertEqual((), self.runtime.locks.records())
        self.assertEqual(
            [], self.runtime.children.runtime(child_id)["selective_hold_tokens"]
        )
        self.assertEqual(TERMINAL_PARENT_READBACK_RESULT, self._marker(child_id))
        self.assertEqual(before, self._parent_bytes(task_id))
        self.assertEqual(1, self._post_write_reads() - reads)
        self.assertEqual(1, self._provider_writes())

    # -- Part 1 repro, settled -----------------------------------------------

    async def test_repro_blocked_then_settled_by_one_readback(self):
        task_id, child_id, parent = await self._live_shape()
        before = self._parent_bytes(task_id)
        blocked, writes = await self._unrelated_apply()
        self._assert_blocked(blocked, writes)
        self.assertEqual("recovering", await self._f3_status())
        reads = self._post_write_reads()

        await self.runtime.recover_once("periodic")

        self._assert_settled(
            task_id, child_id, before, reads, outcome="succeeded_verified"
        )
        self.assertEqual(parent, self.service.task_repository.get(task_id))
        self.assertEqual("ready", await self._f3_status())
        # Repeated sweeps are idempotent: nothing is re-read or rewritten.
        settled = self.runtime.children.get(child_id)
        settled_runtime = self.runtime.children.runtime(child_id)
        for _ in range(2):
            await self._pass()
        self.assertEqual(settled, self.runtime.children.get(child_id))
        self.assertEqual(settled_runtime, self.runtime.children.runtime(child_id))
        self.assertEqual(1, self._post_write_reads() - reads)
        applied, writes = await self._unrelated_apply()
        self.assertEqual("succeeded_verified", applied["task_state"], applied)
        self.assertEqual(1, writes)
        self.assertEqual(before, self._parent_bytes(task_id))
        self.assertEqual(1, self._provider_writes())

    async def test_mismatch_is_verification_failure(self):
        task_id, child_id, _parent = await self._live_shape()
        before = self._parent_bytes(task_id)
        self._drift()
        reads = self._post_write_reads()

        await self.runtime.recover_once("periodic")

        self._assert_settled(
            task_id, child_id, before, reads, outcome="verification_mismatch"
        )
        self.assertEqual("ready", await self._f3_status())

    async def test_unavailable_is_indeterminate_with_a_reason(self):
        task_id, child_id, _parent = await self._live_shape()
        before = self._parent_bytes(task_id)
        self._reads_unavailable(100)
        reads = self._post_write_reads()

        await self.runtime.recover_once("periodic")

        self._assert_settled(
            task_id, child_id, before, reads, outcome="manual_review_required"
        )
        child = self.runtime.children.get(child_id)
        self.assertEqual(
            {"manual_review_reason_code": self.UNAVAILABLE_REASON},
            child.evidence,
        )
        observed = [
            event["diagnostic_codes"]
            for event in child.events
            if event["event_type"] == "observation_recorded"
        ]
        self.assertTrue(set(self.UNAVAILABLE_DIAGNOSTICS) <= set(observed[-1]))
        self.assertNotEqual("recovering", await self._f3_status())
        await self._pass()
        self.assertEqual(1, self._post_write_reads() - reads)

    # -- fault injection (HAMCP-263 R1 marker, reused) ----------------------

    async def _stop_during_cleanup(self, stop):
        task_id, child_id, _parent = await self._live_shape()
        before = self._parent_bytes(task_id)
        held = len(self.runtime.locks.records())
        self._reads_unavailable(100)
        reads = self._post_write_reads()
        with stop():
            with self.assertRaises(_Crash):
                await self.runtime.recover_once("periodic")
        child = self.runtime.children.get(child_id)
        self.assertTrue(child.terminal)
        self.assertEqual("manual_review_required", child.normalized_outcome)
        self.assertEqual(TERMINAL_PARENT_CLEANUP_PENDING, self._marker(child_id))
        self.assertEqual(1, self._post_write_reads() - reads)
        self.assertEqual(before, self._parent_bytes(task_id))
        return task_id, child_id, before, reads, held

    async def _restart_and_assert_settled(self, task_id, child_id, before,
                                          reads):
        self._reads_unavailable(0)
        self.clock[0] += timedelta(minutes=10)
        self._compose()
        await self.runtime.recover_once("startup")
        await self._pass()
        self._assert_settled(
            task_id, child_id, before, reads, outcome="manual_review_required"
        )
        self.assertNotEqual("recovering", await self._f3_status())
        settled = self.runtime.children.get(child_id)
        settled_runtime = self.runtime.children.runtime(child_id)
        for _ in range(2):
            self._compose()
            await self.runtime.recover_once("startup")
            await self._pass()
        self.assertEqual(settled, self.runtime.children.get(child_id))
        self.assertEqual(settled_runtime, self.runtime.children.runtime(child_id))
        self.assertEqual(1, self._post_write_reads() - reads)
        applied, writes = await self._unrelated_apply()
        self.assertEqual("succeeded_verified", applied["task_state"], applied)
        self.assertEqual(1, writes)
        self.assertEqual(before, self._parent_bytes(task_id))

    async def test_stop_before_any_release(self):
        stopped = await self._stop_during_cleanup(
            lambda: patch.object(
                type(self.runtime.locks), "release", side_effect=_Crash()
            )
        )
        self.assertEqual(stopped[4], len(self.runtime.locks.records()))
        await self._restart_and_assert_settled(*stopped[:4])

    async def test_stop_after_partial_release(self):
        store_type = type(self.runtime.locks)
        original = store_type.release

        def release_one_then_stop(store, handle, **kwargs):
            original(store, replace(handle, tokens=handle.tokens[:1]), **kwargs)
            raise _Crash()

        stopped = await self._stop_during_cleanup(
            lambda: patch.object(store_type, "release", release_one_then_stop)
        )
        self.assertEqual(stopped[4] - 1, len(self.runtime.locks.records()))
        await self._restart_and_assert_settled(*stopped[:4])

    async def test_stop_after_all_releases_before_marker_clears(self):
        original = F3RuntimeIntegration._release_terminal_parent_child_locks

        def release_then_stop(runtime, declaration, record):
            original(runtime, declaration, record)
            raise _Crash()

        stopped = await self._stop_during_cleanup(
            lambda: patch.object(
                F3RuntimeIntegration,
                "_release_terminal_parent_child_locks",
                release_then_stop,
            )
        )
        self.assertEqual((), self.runtime.locks.records())
        await self._restart_and_assert_settled(*stopped[:4])

    # -- negative reachability ----------------------------------------------

    async def test_live_owner_under_active_parent_is_never_released(self):
        task_id = await self._dispatch_observing(failed_reads=100)
        child_id = self.runtime.children.declarations_for_task(task_id)[0][
            "child_id"
        ]
        held = self.runtime.locks.records()
        reads = self._post_write_reads()
        # Inside the live lease the parent is active and owned by F3: the
        # historical scan collects nothing and nothing is released or read.
        await self._pass()
        self.assertFalse(self.runtime.children.get(child_id).terminal)
        self.assertEqual(
            {(item.key, item.mode) for item in held},
            {(item.key, item.mode) for item in self.runtime.locks.records()},
        )
        self.assertNotEqual(
            TERMINAL_PARENT_CLEANUP_PENDING, self._marker(child_id)
        )
        declaration = self.runtime.children.declarations_for_task(task_id)[0]
        with self.assertRaises(GovernanceError):
            self.runtime._release_terminal_parent_child_locks(
                declaration, self.runtime.children.get(child_id)
            )
        self.assertFalse(
            await self.runtime._recover_terminal_parent_child(
                declaration, now=self.clock[0]
            )
        )
        self.assertEqual(len(held), len(self.runtime.locks.records()))
        self.assertLessEqual(self._post_write_reads() - reads, 1)
        self.assertEqual(1, self._provider_writes())

    # Isolated controls: each gate refuses on its own when the family check
    # fails, even if a caller reaches it directly past the historical scan.

    def _ineligible(self):
        return patch.object(
            F3RuntimeIntegration,
            "_terminal_parent_readback_family",
            lambda *_args, **_kwargs: False,
        )

    async def test_ineligible_family_is_never_read_by_direct_recovery(self):
        task_id, child_id, _parent = await self._live_shape()
        before = self._parent_bytes(task_id)
        held = self.runtime.locks.records()
        reads = self._post_write_reads()
        declaration = self.runtime.children.declarations_for_task(task_id)[0]
        with self._ineligible():
            self.assertFalse(
                await self.runtime._recover_terminal_parent_child(
                    declaration, now=self.clock[0]
                )
            )
        self.assertFalse(self.runtime.children.get(child_id).terminal)
        self.assertEqual(held, self.runtime.locks.records())
        self.assertEqual(0, self._post_write_reads() - reads)
        self.assertEqual(1, self._provider_writes())
        self.assertEqual(before, self._parent_bytes(task_id))

    async def test_ineligible_family_cleanup_resume_releases_nothing(self):
        stopped = await self._stop_during_cleanup(
            lambda: patch.object(
                type(self.runtime.locks), "release", side_effect=_Crash()
            )
        )
        task_id, child_id = stopped[:2]
        held = self.runtime.locks.records()
        declaration = self.runtime.children.declarations_for_task(task_id)[0]
        with self._ineligible():
            self.assertFalse(
                self.runtime._finish_terminal_parent_child_cleanup(
                    declaration, now=self.clock[0]
                )
            )
        self.assertEqual(held, self.runtime.locks.records())
        self.assertEqual(TERMINAL_PARENT_CLEANUP_PENDING, self._marker(child_id))
        await self._restart_and_assert_settled(*stopped[:4])

    # -- the ordinary recover path reads once at its deadline ---------------

    async def _active_until_terminal(self, task_id):
        for _ in range(12):
            if self.service.task_repository.get(task_id).state.value not in {
                "observing", "running", "dispatching", "verifying",
            }:
                break
            await self._pass()
        return self.service.task_repository.get(task_id)

    async def test_active_recovery_final_read_at_deadline_verifies(self):
        task_id = await self._dispatch_observing(failed_reads=100)
        child_id = self.runtime.children.declarations_for_task(task_id)[0][
            "child_id"
        ]
        # Unavailable throughout the live lease, then readable at the
        # deadline: one final read decides it instead of a blind review.
        reads = self._post_write_reads()
        self.clock[0] += timedelta(seconds=121)
        self._reads_unavailable(0)
        parent = await self._active_until_terminal(task_id)
        self.assertEqual("succeeded_verified", parent.state.value)
        child = self.runtime.children.get(child_id)
        self.assertEqual("succeeded_verified", child.normalized_outcome)
        self.assertEqual(1, self._post_write_reads() - reads)
        self.assertEqual((), self.runtime.locks.records())
        self.assertEqual(1, self._provider_writes())

    async def test_active_recovery_final_read_unavailable_is_indeterminate(
        self,
    ):
        task_id = await self._dispatch_observing(failed_reads=100)
        reads = self._post_write_reads()
        self.clock[0] += timedelta(seconds=121)
        parent = await self._active_until_terminal(task_id)
        self.assertEqual("manual_review_required", parent.state.value)
        self.assertEqual(1, self._post_write_reads() - reads)
        self.assertEqual(1, self._provider_writes())


class _MarkerPreservation:
    """HAMCP-271 R1-M1: a transient read never loses the cleanup marker.

    Real stores: an unavailable readback plus a failed lock release leaves a
    terminal child, its locks and the durable marker. A later sweep whose
    durable plan reads fail (once, or twice) must keep the marker and every
    lock, project nothing and retry; once reads recover, the same process or
    a restart finishes the exact-owner release with no further read or
    dispatch.
    """

    async def _marked_after_release_fault(self):
        task_id, child_id, _parent = await self._live_shape()
        before = self._parent_bytes(task_id)
        held = {(item.key, item.mode) for item in self.runtime.locks.records()}
        self._reads_unavailable(100)
        reads = self._post_write_reads()
        with patch.object(
            type(self.runtime.locks),
            "release",
            side_effect=LockStorageError("synthetic release failure"),
        ):
            await self.runtime.recover_once("periodic")
        child = self.runtime.children.get(child_id)
        self.assertTrue(child.terminal)
        self.assertEqual("manual_review_required", child.normalized_outcome)
        self.assertEqual(TERMINAL_PARENT_CLEANUP_PENDING, self._marker(child_id))
        self.assertEqual(held, self._lock_keys())
        self._reads_unavailable(0)
        return task_id, child_id, before, reads, held

    def _lock_keys(self):
        return {(item.key, item.mode) for item in self.runtime.locks.records()}

    def _plan_reads(self, failing):
        """Patch durable plan reads; ``failing(calls, plan)`` may raise."""

        original = self.service._load_for_projection
        calls = [0]

        def read(*args, **kwargs):
            calls[0] += 1
            return failing(calls[0], original(*args, **kwargs))

        return patch.object(
            self.service, "_load_for_projection", side_effect=read
        ), calls

    def _eligibility_fault(self, owner, name):
        """Patch ``owner.name`` to fail only inside the eligibility check.

        Other callers (projection, the generic sweep) keep the real answer,
        so the test isolates the strict finish of the marked child.
        """

        original = getattr(owner, name)
        calls = [0]

        def lookup(*args, **kwargs):
            # The mock's own call frames sit between this and the caller.
            frame = sys._getframe(1)
            for _ in range(8):
                if frame is None:
                    break
                if frame.f_code.co_name == "_terminal_parent_readback_family":
                    calls[0] += 1
                    raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
                frame = frame.f_back
            return original(*args, **kwargs)

        return patch.object(owner, name, side_effect=lookup), calls

    async def _unreadable_sweep(self, child_id, task_id, before, held, failing,
                                *, minimum_calls, fault=None):
        self.clock[0] += timedelta(minutes=10)
        retry = self.runtime.children.runtime(child_id)
        reading, calls = fault or self._plan_reads(failing)
        with reading:
            try:
                await self.runtime.recover_once("periodic")
            except GovernanceError:
                # A storage outage may also fail the generic expired-lock
                # sweep; the scheduler retries that pass (unchanged here).
                pass
        self.assertGreaterEqual(calls[0], minimum_calls)
        runtime = self.runtime.children.runtime(child_id)
        self.assertEqual(TERMINAL_PARENT_CLEANUP_PENDING, runtime[
            "reconciliation_result"
        ])
        # HAMCP-263's generic sweep may release the expired non-target
        # shared keys of a terminal owner; the exclusive target key, whose
        # release needs the readback, stays held until cleanup finishes.
        remaining = self._lock_keys()
        self.assertLessEqual(remaining, held)
        self.assertEqual(
            {item for item in held if item[1] == "exclusive"},
            {item for item in remaining if item[1] == "exclusive"},
        )
        self.assertTrue(any(item[1] == "exclusive" for item in remaining))
        self.assertEqual(before, self._parent_bytes(task_id))
        # Bounded retry bookkeeping, never a silent skip.
        self.assertEqual(self.clock[0].isoformat(), runtime[
            "last_reconciliation_at"
        ])
        self.assertGreater(runtime["backoff_seconds"], retry["backoff_seconds"])
        self.assertIsNotNone(runtime["next_eligible_at"])
        self.assertIn(await self._f3_status(), {"recovering", "unavailable"})

    async def _assert_resumed(self, task_id, child_id, before, reads, *,
                              restart):
        self.clock[0] += timedelta(minutes=10)
        if restart:
            self._compose()
            await self.runtime.recover_once("startup")
        else:
            await self.runtime.recover_once("periodic")
        self._assert_settled(
            task_id, child_id, before, reads, outcome="manual_review_required"
        )
        self.assertNotEqual("recovering", await self._f3_status())
        settled = self.runtime.children.get(child_id)
        settled_runtime = self.runtime.children.runtime(child_id)
        for _ in range(2):
            self.clock[0] += timedelta(minutes=10)
            self._compose()
            await self.runtime.recover_once("startup")
            await self._pass()
        self.assertEqual(settled, self.runtime.children.get(child_id))
        self.assertEqual(settled_runtime, self.runtime.children.runtime(child_id))
        self.assertEqual(1, self._post_write_reads() - reads)
        applied, writes = await self._unrelated_apply()
        self.assertEqual("succeeded_verified", applied["task_state"], applied)
        self.assertEqual(1, writes)
        self.assertEqual(before, self._parent_bytes(task_id))
        self.assertEqual(1, self._provider_writes())

    async def _transient_plan_reads(self, failures, *, restart):
        task_id, child_id, before, reads, held = (
            await self._marked_after_release_fault()
        )

        def failing(call, plan):
            if call <= failures:
                raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
            return plan

        await self._unreadable_sweep(
            child_id, task_id, before, held, failing, minimum_calls=failures
        )
        await self._assert_resumed(
            task_id, child_id, before, reads, restart=restart
        )

    async def test_one_plan_read_failure_keeps_marker_same_process(self):
        await self._transient_plan_reads(1, restart=False)

    async def test_one_plan_read_failure_keeps_marker_restart(self):
        await self._transient_plan_reads(1, restart=True)

    async def test_two_plan_read_failures_keep_marker_same_process(self):
        await self._transient_plan_reads(2, restart=False)

    async def test_two_plan_read_failures_keep_marker_restart(self):
        await self._transient_plan_reads(2, restart=True)

    async def test_mismatched_plan_read_keeps_marker(self):
        task_id, child_id, before, reads, held = (
            await self._marked_after_release_fault()
        )

        def mismatched(call, plan):
            return replace(plan, plan_id="other-plan") if call == 1 else plan

        await self._unreadable_sweep(
            child_id, task_id, before, held, mismatched, minimum_calls=1
        )
        await self._assert_resumed(
            task_id, child_id, before, reads, restart=False
        )

    async def test_registry_lookup_failure_keeps_marker(self):
        # An unreadable registry is unknown eligibility, never "another
        # family" (which would return early and strand the locks).
        task_id, child_id, before, reads, held = (
            await self._marked_after_release_fault()
        )
        await self._unreadable_sweep(
            child_id, task_id, before, held, None, minimum_calls=1,
            fault=self._eligibility_fault(self.runtime.registry, "entry"),
        )
        await self._assert_resumed(
            task_id, child_id, before, reads, restart=True
        )

    async def test_rollback_marker_read_failure_keeps_marker(self):
        # A failed HAMCP-135 inverse-marker check is unknown eligibility too.
        task_id, child_id, before, reads, held = (
            await self._marked_after_release_fault()
        )
        await self._unreadable_sweep(
            child_id, task_id, before, held, None, minimum_calls=1,
            fault=self._eligibility_fault(inverse_eligibility, "markers"),
        )
        await self._assert_resumed(
            task_id, child_id, before, reads, restart=False
        )

    async def test_missing_child_after_readback_keeps_marker(self):
        # A child read that returns nothing after the readback is a storage
        # fault, never "not terminal" (which would replace the marker).
        task_id, child_id, _parent = await self._live_shape()
        before = self._parent_bytes(task_id)
        self._reads_unavailable(100)
        reads = self._post_write_reads()
        executed = []
        original_execute = F3RuntimeIntegration._execute_child
        children = self.runtime.children
        original_get = children.get

        async def execute(runtime, *args, **kwargs):
            result = await original_execute(runtime, *args, **kwargs)
            executed.append(True)
            return result

        def get(identifier):
            if executed == [True] and identifier == child_id:
                executed.append(False)
                return None
            return original_get(identifier)

        with patch.object(F3RuntimeIntegration, "_execute_child", execute), \
                patch.object(children, "get", side_effect=get):
            await self.runtime.recover_once("periodic")
        self.assertEqual([True, False], executed)
        self.assertTrue(self.runtime.children.get(child_id).terminal)
        self.assertEqual(TERMINAL_PARENT_CLEANUP_PENDING, self._marker(child_id))
        self.assertEqual(before, self._parent_bytes(task_id))
        self._reads_unavailable(0)
        await self._assert_resumed(
            task_id, child_id, before, reads, restart=True
        )


class ConfigurationScriptFamilyTests(
    _MarkerPreservation, _StrandedFamilyMatrix, _StrandedFamilyHarness
):
    UNAVAILABLE_REASON = "adapter_recovery_requires_manual_review"
    UNAVAILABLE_DIAGNOSTICS = (
        "post_dispatch_evidence_deadline_expired",
        "readback_unavailable",
    )

    async def _dispatch_observing(self, *, failed_reads):
        return await self._dispatch_script_observing(failed_reads=failed_reads)

    def _reads_unavailable(self, count):
        self.gateway.fail_reads_after_write = count

    def _post_write_reads(self):
        return self.gateway.post_write_reads

    def _provider_writes(self):
        return len(self.gateway.writes(("script", "set_hvac_comfort")))

    def _drift(self):
        self.gateway.configs[("script", "set_hvac_comfort")]["alias"] = (
            "Drifted by another writer"
        )


class HelperStateFamilyTests(_StrandedFamilyMatrix, _StrandedFamilyHarness):
    UNAVAILABLE_REASON = "adapter_recovery_requires_manual_review"
    UNAVAILABLE_DIAGNOSTICS = ("evidence_deadline_expired",)

    async def _dispatch_observing(self, *, failed_reads):
        return await self._dispatch_helper_observing(failed_reads=failed_reads)

    def _reads_unavailable(self, count):
        self.helper.fail_reads_after_write = count

    def _post_write_reads(self):
        return self.helper.post_write_reads

    def _provider_writes(self):
        return self.helper.dispatch_count

    def _drift(self):
        self.helper.set_observed_state("off", _ClockView(self.clock))


class InputSelectFamilyTests(_StrandedFamilyMatrix, InputSelectServiceTestCase):
    """Operational ``set_input_select_option`` under the Core authority window.

    The unrelated operation is a configuration-family entity metadata change,
    so a settled operational child also unblocks the other family.
    """

    UNAVAILABLE_REASON = "adapter_recovery_requires_manual_review"
    UNAVAILABLE_DIAGNOSTICS = ("evidence_deadline_expired",)
    UNRELATED = "sensor.porch"

    _pass = _StrandedFamilyHarness._pass
    _strand = _StrandedFamilyHarness._strand
    _assert_blocked = _StrandedFamilyHarness._assert_blocked

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add_entity(self.UNRELATED, icon="mdi:old")
        self.failing_reads = 0
        self.reads_after_send = 0
        original = self.select_rest.request

        async def request(method, path, **kwargs):
            if self.select_ws.commands:
                self.reads_after_send += 1
                if self.failing_reads > 0:
                    self.failing_reads -= 1
                    raise HomeAssistantUnavailableError()
            return await original(method, path, **kwargs)

        self.select_rest.request = request

    def _compose(self):
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=self.resource_gateway,
            backup_gateway=None,
            lifecycle_gateway=None,
            input_select_gateway=self.select_gateway,
            provider_identity_reader=select_provider_identity,
            retention_days=90,
            core_runtime=self.core,
        )
        self.service.f3_runtime = self.runtime

    async def _dispatch_observing(self, *, failed_reads):
        plan = await self.approved_select("Away")
        self.failing_reads = failed_reads
        result = await self.apply(plan)
        self.assertEqual("observing", result["task_state"], result)
        self.assertEqual(1, len(self.sent()))
        return result["task_id"]

    def _reads_unavailable(self, count):
        self.failing_reads = count

    def _post_write_reads(self):
        return self.reads_after_send

    def _provider_writes(self):
        return len(self.sent())

    def _drift(self):
        self.select_rest.set_external(SELECT_ENTITY, state="Night")

    async def _unrelated_apply(self):
        created = await self.service.create_entity_metadata_plan(
            {"entity_id": self.UNRELATED, "name": "Porch"}
        )
        await self.approve(created)
        before = self.ws.entries[self.UNRELATED].get("name")
        result = await self.apply(created)
        after = self.ws.entries[self.UNRELATED].get("name")
        return result, int(after != before)


EXPIRED = "2026-08-04T11:59:59+00:00"
RESOURCES = ("automation", "script", "input_boolean", "input_number")


class ConfigurationFinalReadTests(unittest.IsolatedAsyncioTestCase):
    """Past the deadline, configuration recovery reads exactly once.

    Before HAMCP-271 the expired branch returned manual review without a
    read, so a terminal-parent child could never reach a truthful outcome.
    """

    async def _dispatched(self, resource_type, action):
        proposal = proposal_for(resource_type, action)
        gateway = SyntheticConfigurationGateway()
        if action == "update":
            gateway.states[(resource_type, proposal.target_id)] = (
                proposal.current_config()
            )
        adapter = adapter_for(resource_type, action, gateway)
        prepared = await adapter.prepare(proposal)
        preflight = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        self.assertTrue(preflight.eligible, preflight.diagnostic_codes)
        harness = ConfigurationLifecycleHarness(adapter)
        await adapter.dispatch(prepared, preflight, before_dispatch=harness.intent)
        self.assertEqual(1, gateway.counters.dispatches)
        return proposal, gateway, harness, prepared

    async def _final(self, harness, prepared, gateway):
        reads = gateway.counters.reads
        observation = await harness.recover(
            prepared, recovery_context(deadline=EXPIRED)
        )
        self.assertEqual(1, gateway.counters.reads - reads)
        self.assertEqual(1, gateway.counters.dispatches)
        return observation

    async def test_readable_at_deadline_verifies(self):
        for resource_type in RESOURCES:
            for action in ("create", "update"):
                with self.subTest(resource_type=resource_type, action=action):
                    _p, gateway, harness, prepared = await self._dispatched(
                        resource_type, action
                    )
                    observation = await self._final(harness, prepared, gateway)
                    self.assertTrue(observation.observation_complete)
                    verification = await harness.verify(prepared, observation)
                    self.assertEqual(
                        NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
                        verification.outcome,
                    )

    async def test_drift_at_deadline_is_a_verification_mismatch(self):
        for resource_type in RESOURCES:
            for action in ("create", "update"):
                with self.subTest(resource_type=resource_type, action=action):
                    proposal, gateway, harness, prepared = await self._dispatched(
                        resource_type, action
                    )
                    key = (resource_type, proposal.target_id)
                    field = "alias" if "alias" in gateway.states[key] else "name"
                    gateway.states[key][field] = "Drifted by another writer"
                    observation = await self._final(harness, prepared, gateway)
                    self.assertTrue(observation.observation_complete)
                    verification = await harness.verify(prepared, observation)
                    self.assertEqual(
                        NormalizedOperationOutcome.VERIFICATION_MISMATCH,
                        verification.outcome,
                    )

    async def test_unreadable_at_deadline_is_manual_review_with_a_reason(self):
        for resource_type in RESOURCES:
            for action in ("create", "update"):
                with self.subTest(resource_type=resource_type, action=action):
                    _p, gateway, harness, prepared = await self._dispatched(
                        resource_type, action
                    )
                    gateway.read_error_count = 100
                    observation = await self._final(harness, prepared, gateway)
                    self.assertEqual(
                        NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED,
                        observation.outcome,
                    )
                    self.assertFalse(observation.observation_complete)
                    self.assertIn(
                        "post_dispatch_evidence_deadline_expired",
                        observation.diagnostic_codes,
                    )

    async def test_contradictory_context_past_deadline_never_reads(self):
        _p, gateway, harness, prepared = await self._dispatched(
            "script", "update"
        )
        reads = gateway.counters.reads
        observation = await harness.recover(
            prepared,
            replace(
                recovery_context(deadline=EXPIRED),
                dispatch_intent_recorded=False,
            ),
        )
        self.assertEqual(
            NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED,
            observation.outcome,
        )
        self.assertEqual(reads, gateway.counters.reads)
        self.assertEqual(1, gateway.counters.dispatches)

    def test_every_configuration_adapter_shares_the_one_recover(self):
        # Delete and helper options override only ``_observe``; labels,
        # scenes, entity metadata, availability/exposure and dashboard
        # metadata are ``ConfigurationOperationAdapter`` instances.
        for adapter_type in (ConfigurationDeleteAdapter, HelperOptionsAdapter):
            with self.subTest(adapter=adapter_type.__name__):
                self.assertIs(
                    ConfigurationOperationAdapter.recover, adapter_type.recover
                )


class FamilyEligibilityTests(_StrandedFamilyHarness):
    """Only the listed families are read or released; nothing else is."""

    def _plan(self, operation, *, inverse=False, plan_id="plan-271"):
        evidence = (
            [{"trigger": inverse_eligibility.TRIGGER, "proof": {}}]
            if inverse
            else []
        )
        return SimpleNamespace(
            plan_id=plan_id,
            operation=operation,
            operations=(
                SimpleNamespace(risk=SimpleNamespace(evidence=evidence)),
            ),
        )

    def _eligible(
        self, capability_id, adapter_id, operation=None, *, durable=None
    ):
        """Without ``operation`` the durable plan (``durable``) decides."""

        plan = None if operation is None else self._plan(operation)
        if durable is None:
            durable = self._plan(ChangeOperation.CONFIGURATION_PLAN)
        with patch.object(
            self.runtime.service, "_load_for_projection", return_value=durable
        ):
            return self.runtime._terminal_parent_readback_family(
                {"capability_id": capability_id, "plan_id": "plan-271"},
                SimpleNamespace(adapter_id=adapter_id),
                plan,
            )

    def test_every_registered_capability_is_classified(self):
        listed = {
            "configuration_operation": None,
            "operational_administration": {
                "set_exact_input_boolean_state": (
                    ChangeOperation.SET_INPUT_BOOLEAN_STATE
                ),
                "set_exact_input_select_option": (
                    ChangeOperation.SET_INPUT_SELECT_OPTION
                ),
            },
            "dashboard_update": {
                "update_existing_dashboard": ChangeOperation.UPDATE_DASHBOARD
            },
        }
        excluded = {item[0] for item in OPERATIONAL_REGISTRATIONS}
        self.assertEqual(
            {
                "create_full_home_assistant_backup",
                "reload_home_assistant_configuration_domain",
                "restart_installed_home_assistant_addon",
                "restart_home_assistant_core",
            },
            excluded,
        )
        for entry in self.runtime.registry.entries:
            capability = entry.capability_id
            with self.subTest(capability=capability):
                if capability in excluded:
                    self.assertFalse(self._eligible(capability, entry.adapter_id))
                    for operation in ChangeOperation:
                        self.assertFalse(
                            self._eligible(capability, entry.adapter_id, operation)
                        )
                    continue
                self.assertIn(entry.adapter_id, listed)
                self.assertTrue(self._eligible(capability, entry.adapter_id))
                family = listed[entry.adapter_id]
                allowed = (
                    {
                        ChangeOperation.CREATE_AUTOMATION,
                        ChangeOperation.UPDATE_AUTOMATION,
                        ChangeOperation.CONFIGURATION_PLAN,
                    }
                    if family is None
                    else {family[capability]}
                )
                for operation in ChangeOperation:
                    self.assertEqual(
                        operation in allowed,
                        self._eligible(capability, entry.adapter_id, operation),
                        operation,
                    )

    def test_adapter_disagreement_is_never_eligible(self):
        for capability, adapter_id in (
            ("set_exact_input_boolean_state", "configuration_operation"),
            ("update_existing_dashboard", "operational_administration"),
            ("create_full_home_assistant_backup", "configuration_operation"),
            ("unregistered_capability", "configuration_operation"),
            ("set_exact_input_boolean_state", "fan_power_control"),
            (None, "operational_administration"),
        ):
            with self.subTest(capability=capability, adapter=adapter_id):
                self.assertFalse(self._eligible(capability, adapter_id))

    def test_registry_lookup_failure_is_never_eligible(self):
        with patch.object(
            type(self.runtime.registry), "entry", side_effect=KeyError("x")
        ):
            for capability, adapter_id in (
                ("update_script_configuration", "configuration_operation"),
                ("set_exact_input_boolean_state", "operational_administration"),
                ("update_existing_dashboard", "dashboard_update"),
            ):
                with self.subTest(capability=capability):
                    self.assertFalse(self._eligible(capability, adapter_id))
                    self.assertFalse(
                        self._eligible(
                            capability,
                            adapter_id,
                            ChangeOperation.CONFIGURATION_PLAN,
                        )
                    )


    def test_governed_rollback_plan_is_never_eligible(self):
        # HAMCP-135: an inverse plan's post-intent child stays conflict-held
        # for manual review; it is never read or released here.
        capability = "update_script_configuration"
        adapter_id = "configuration_operation"
        for operation in (
            ChangeOperation.CREATE_AUTOMATION,
            ChangeOperation.UPDATE_AUTOMATION,
            ChangeOperation.CONFIGURATION_PLAN,
        ):
            with self.subTest(operation=operation):
                self.assertTrue(self._eligible(capability, adapter_id, operation))
                inverse = self._plan(operation, inverse=True)
                self.assertFalse(
                    self.runtime._terminal_parent_readback_family(
                        {"capability_id": capability}, SimpleNamespace(
                            adapter_id=adapter_id), inverse
                    )
                )
                self.assertFalse(
                    self._eligible(capability, adapter_id, durable=inverse)
                )

    def test_unreadable_rollback_markers_are_never_eligible(self):
        unreadable = SimpleNamespace(
            plan_id="plan-271",
            operation=ChangeOperation.CONFIGURATION_PLAN,
            operations=(SimpleNamespace(),),
        )
        capability = "update_script_configuration"
        adapter_id = "configuration_operation"
        self.assertFalse(
            self.runtime._terminal_parent_readback_family(
                {"capability_id": capability},
                SimpleNamespace(adapter_id=adapter_id),
                unreadable,
            )
        )
        self.assertFalse(
            self._eligible(capability, adapter_id, durable=unreadable)
        )

    def test_configuration_without_a_readable_durable_plan_is_never_eligible(self):
        capability = "update_script_configuration"
        adapter_id = "configuration_operation"
        self.assertTrue(self._eligible(capability, adapter_id))
        self.assertFalse(
            self._eligible(
                capability,
                adapter_id,
                durable=self._plan(
                    ChangeOperation.CONFIGURATION_PLAN, plan_id="other-plan"
                ),
            )
        )
        self.assertFalse(
            self._eligible(
                capability,
                adapter_id,
                durable=self._plan(ChangeOperation.UPDATE_DASHBOARD),
            )
        )
        with patch.object(
            self.runtime.service,
            "_load_for_projection",
            side_effect=GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR),
        ):
            self.assertFalse(
                self.runtime._terminal_parent_readback_family(
                    {"capability_id": capability, "plan_id": "plan-271"},
                    SimpleNamespace(adapter_id=adapter_id),
                )
            )
        self.assertFalse(
            self.runtime._terminal_parent_readback_family(
                {"capability_id": capability},
                SimpleNamespace(adapter_id=adapter_id),
            )
        )

    def test_other_families_never_load_a_durable_plan(self):
        with patch.object(
            self.runtime.service,
            "_load_for_projection",
            side_effect=AssertionError("no plan load"),
        ) as load:
            for capability, adapter_id in (
                ("set_exact_input_boolean_state", "operational_administration"),
                ("set_exact_input_select_option", "operational_administration"),
                ("update_existing_dashboard", "dashboard_update"),
            ):
                with self.subTest(capability=capability):
                    self.assertTrue(
                        self.runtime._terminal_parent_readback_family(
                            {"capability_id": capability},
                            SimpleNamespace(adapter_id=adapter_id),
                        )
                    )
        load.assert_not_called()


class _ClockView:
    def __init__(self, clock):
        self.clock = clock

    def __call__(self):
        return self.clock[0]

    def advance(self, *, seconds: int = 1):
        self.clock[0] += timedelta(seconds=seconds)


if __name__ == "__main__":
    unittest.main()
