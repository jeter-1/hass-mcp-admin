"""Deficiency #2: orphaned F3 children under a terminal parent.

The recovery sweep skipped every child whose public task was already terminal.
A parent that failed before dispatch therefore stranded its children in
``preflight``/``not_started`` permanently: the sweep never revisited them,
their hold projections were never cleared, and ``nonterminal_execution_count``
never converged. Live evidence showed ~498 sweeps across 4.7 hours with no
change.

The invariant restored here:

    terminal parent + proven zero dispatch => no child remains nonterminal.

"Proven zero dispatch" is durable rather than inferred. The executor commits
the dispatch intent *before* invoking a provider, so a record with no intent
provably never dispatched; a crash after the intent leaves it set and is
deliberately excluded from this path.
"""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3.contracts import (  # noqa: E402
    LockMode,
    LockRequest,
    LockScope,
)
from ha_mcp_engineering.governance.task_models import (  # noqa: E402
    ExecutionTaskState,
)
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
)
from tests.test_f3_runtime_integration import (  # noqa: E402
    _ExactFakeConfigurationGateway,
    _provider_identity,
)
from ha_mcp_engineering.f3.models import (  # noqa: E402
    ExecutionIdentity,
    ExecutorTiming,
    LockOwner,
    LockTiming,
)
from ha_mcp_engineering.f3.contracts import (  # noqa: E402
    F3_ADAPTER_CONTRACT_MODEL,
)
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
    ORPHAN_RECOVERY_SCAN_LIMIT,
    ORPHAN_RECONCILIATION_RESULT,
    RECOVERY_BATCH_SIZE,
)


class _PreparedStub:
    """Minimal prepared-operation shape the execution claim reads."""

    contract_model = F3_ADAPTER_CONTRACT_MODEL

    def __init__(self, declaration):
        self.adapter_id = declaration["adapter_id"]
        self.operation = declaration["operation_id"]
        self.prepared_operation_hash = declaration["prepared_operation_hash"]
        self.plan_id = declaration["plan_id"]
        self.target = type(
            "_Target",
            (),
            {
                "target_id": declaration["target_id"],
                "target_type": declaration["target_type"],
            },
        )()


class OrphanChildRecoveryTests(ConfigurationPlanTestCase):
    """Reproduce the live orphan's shape and prove the sweep converges it."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._claims = {}
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=_ExactFakeConfigurationGateway(
                self.gateway
            ),
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=_provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    async def _orphaned_task(self):
        """Build a parent that terminates before dispatch, with children.

        Mirrors execution task ab8d7cd1...: parent `failed_pre_dispatch` with
        no provider attempt. A pre-existing active legacy task on the same
        resource makes the apply fail in preflight, which is a real
        zero-dispatch terminal path rather than a hand-written state.
        """

        blocking = await self.create_automation_plan()
        await self.approve(blocking)
        blocking_plan = self.service._load(blocking["plan_id"])
        legacy_task = self.service._create_task_for_plan(
            blocking_plan, blocking["plan_hash"]
        )
        self.service._record_task_event(
            legacy_task,
            "preflight_started",
            new_state=ExecutionTaskState.PREFLIGHT,
            changes={"started_at": self.service._timestamp()},
        )

        created = await self.create_hvac_plan()
        await self.approve(created)
        applied = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual("failed_pre_dispatch", applied["task_state"])
        task = self.service.task_repository.get(applied["task_id"])
        declarations = self.runtime.children.declarations_for_task(
            task.task_id
        )
        self.assertGreaterEqual(len(declarations), 2)
        # The premise of the whole fix: terminal parent, zero dispatch.
        self.assertEqual([], list(task.provider_attempts))
        self.assertIsNone(task.dispatched_at)
        return task, declarations

    def _claim_child(self, declaration):
        """Give one child a nonterminal, never-dispatched execution record."""

        identity = ExecutionIdentity(
            task_id=declaration["child_id"],
            plan_id=declaration["plan_id"],
            attempt_id="attempt-orphan",
            request_id="request-orphan",
            owner_id="owner-orphan",
        )
        timing = ExecutorTiming(
            post_dispatch_evidence_seconds=120.0,
            claim_lease_seconds=120.0,
            max_observation_attempts=3,
            max_verification_attempts=3,
        )
        claim = self.runtime.children.claim(
            identity=identity,
            prepared=_PreparedStub(declaration),
            timing=timing,
            now=self.service.now(),
        )
        self._claims[declaration["child_id"]] = (identity, claim, timing)
        record = self.runtime.children.get(declaration["child_id"])
        # The premise: nonterminal and provably never dispatched.
        self.assertFalse(record.terminal)
        self.assertIsNone(record.dispatch_intent)
        return record

    def _hold_child_lock(
        self,
        declaration,
        *,
        expired=False,
        conflict_hold=False,
    ):
        """Acquire and persist an exact child-owned lock through real stores."""

        identity, claim, _timing = self._claims[declaration["child_id"]]
        record = self.runtime.children.get(declaration["child_id"])
        key = (
            declaration["selective_hold_keys"][0]
            if declaration["selective_hold_keys"]
            else f"{declaration['target_type']}:{declaration['target_id']}"
        )
        owner = LockOwner(
            owner_id=identity.owner_id,
            task_id=identity.task_id,
            plan_id=identity.plan_id,
            operation_id=record.operation,
            attempt_id=identity.attempt_id,
        )
        timing = LockTiming(120, 20, 0, 0.05)
        acquired_at = self.service.now() - (
            timedelta(seconds=121) if expired else timedelta(0)
        )
        handle = self.runtime.locks.acquire_once(
            (
                LockRequest(
                    key=key,
                    scopes=(LockScope.RESOURCE,),
                    mode=LockMode.EXCLUSIVE,
                    reason_codes=("orphan_recovery_test",),
                ),
            ),
            owner=owner,
            timing=timing,
            now=acquired_at,
        )
        self.runtime.children.record_locks(
            declaration["child_id"],
            owner_id=identity.owner_id,
            claim_generation=claim.claim_generation,
            handle=handle,
            now=self.service.now(),
        )
        if conflict_hold:
            self.runtime.locks.promote_to_conflict_hold(
                handle,
                reason_code="orphan_recovery_test",
            )
        return owner, handle

    def _set_runtime_tokens(self, child_id, handle):
        self.runtime.children.update_runtime(
            child_id,
            changes={
                "selective_hold_tokens": [
                    {
                        "key": token.key,
                        "generation": token.generation,
                        "mode": token.mode,
                    }
                    for token in handle.tokens
                ]
            },
        )

    def _audit_entries(self):
        if not self.audit_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    async def _build_live_orphan(self):
        """Assemble the exact shape observed on the deployed server."""

        task, declarations = await self._orphaned_task()
        claimable = next(
            item
            for item in declarations
            if self.runtime.children.get(item["child_id"]) is None
        )
        self._claim_child(claimable)
        declarations = (
            claimable,
            *(item for item in declarations if item is not claimable),
        )
        health = self.runtime.children.health()
        self.assertEqual(1, health["nonterminal_execution_count"])
        return task, declarations

    def _restart_runtime(self):
        restarted = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=_ExactFakeConfigurationGateway(
                self.gateway
            ),
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=_provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = restarted
        self.runtime = restarted
        return restarted

    async def test_orphaned_children_converge_in_one_sweep(self):
        task, declarations = await self._build_live_orphan()

        result = await self.runtime.recover_once("periodic")

        self.assertEqual(1, result["orphaned_children_terminalized"])
        health = self.runtime.children.health()
        self.assertEqual(0, health["nonterminal_execution_count"])

        claimed = self.runtime.children.get(declarations[0]["child_id"])
        self.assertTrue(claimed.terminal)
        self.assertEqual("cancelled_pre_dispatch", claimed.normalized_outcome)
        # Never invent success for something that never ran.
        self.assertNotEqual("succeeded_verified", claimed.normalized_outcome)

    async def test_health_returns_to_ready(self):
        await self._build_live_orphan()
        self.assertEqual("recovering", self.runtime.health()["status"])

        await self.runtime.recover_once("periodic")

        health = self.runtime.health()
        self.assertEqual("ready", health["status"])
        self.assertEqual(0, health["nonterminal_execution_count"])

    async def test_every_child_reports_a_terminal_non_success_state(self):
        task, _declarations = await self._build_live_orphan()

        await self.runtime.recover_once("periodic")

        detail = self.runtime.decorate_task(
            self.service._load_task(task.task_id)
        )
        children = detail["f3_children"]
        self.assertGreaterEqual(len(children), 2)
        outcomes = set()
        for child in children:
            with self.subTest(child=child["operation_id"]):
                self.assertEqual("terminal", child["state"])
                self.assertIn(
                    child["normalized_outcome"],
                    {"preflight_rejected", "cancelled_pre_dispatch"},
                )
                outcomes.add(child["normalized_outcome"])
                self.assertEqual(0, child["dispatch_count"])
        self.assertIn("cancelled_pre_dispatch", outcomes)

    async def test_sweep_is_idempotent(self):
        await self._build_live_orphan()

        first = await self.runtime.recover_once("periodic")
        claimed_before = self.runtime.children.list()
        events_before = sum(len(item.events) for item in claimed_before)

        second = await self.runtime.recover_once("periodic")

        self.assertEqual(1, first["orphaned_children_terminalized"])
        # Second pass finds nothing left to do and writes no new events.
        self.assertEqual(0, second["orphaned_children_terminalized"])
        events_after = sum(
            len(item.events) for item in self.runtime.children.list()
        )
        self.assertEqual(events_before, events_after)
        self.assertEqual(
            0, self.runtime.children.health()["nonterminal_execution_count"]
        )

    async def test_no_provider_dispatch_occurs_during_recovery(self):
        await self._build_live_orphan()
        before = len(self.gateway.calls)

        await self.runtime.recover_once("periodic")

        # Bookkeeping correction, never an execution.
        self.assertEqual(before, len(self.gateway.calls))
        self.assertEqual(
            0, sum(call[0] == "write" for call in self.gateway.calls)
        )

    async def test_post_intent_child_is_never_terminalized_pre_dispatch(self):
        # Use a child that genuinely reached dispatch through the normal path,
        # so the durable intent is real rather than hand-written.
        created = await self.create_automation_plan()
        await self.approve(created)
        applied = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        task = self.service.task_repository.get(applied["task_id"])
        declaration = self.runtime.children.declarations_for_task(
            task.task_id
        )[0]
        child_id = declaration["child_id"]
        record = self.runtime.children.get(child_id)
        self.assertIsNotNone(record.dispatch_intent)

        # The primitive the sweep relies on refuses post-intent records, so
        # the no-blind-redispatch guarantee holds at the storage layer and not
        # merely in the caller's predicate.
        cancelled = self.runtime.children.cancel(
            child_id, now=self.service.now()
        )

        self.assertFalse(cancelled)
        after = self.runtime.children.get(child_id)
        self.assertNotEqual(
            "cancelled_pre_dispatch", after.normalized_outcome
        )
        self.assertTrue(
            any(
                "dispatch_intent_exists" in event["diagnostic_codes"]
                for event in after.events
            )
        )

    async def test_parent_that_dispatched_is_out_of_scope(self):
        # A plan that ran normally reaches a terminal state *with* provider
        # attempts. Its children must never be swept by this path.
        created = await self.create_automation_plan()
        await self.approve(created)
        applied = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        task = self.service.task_repository.get(applied["task_id"])
        self.assertTrue(task.provider_attempts)
        self.assertIsNotNone(task.dispatched_at)
        declarations = self.runtime.children.declarations_for_task(
            task.task_id
        )
        before = {
            item["child_id"]: self.runtime.children.get(item["child_id"])
            for item in declarations
        }

        result = await self.runtime.recover_once("periodic")

        self.assertEqual(0, result["orphaned_children_terminalized"])
        for child_id, record in before.items():
            after = self.runtime.children.get(child_id)
            self.assertEqual(
                record.normalized_outcome, after.normalized_outcome
            )


    async def test_reconciled_orphans_leave_no_pending_reconciliation_items(
        self,
    ):
        task, declarations = await self._build_live_orphan()
        child_ids = {item["child_id"] for item in declarations}
        before = {
            item["child_id"]
            for item in self.runtime.reconciliation_items()
            if item["child_id"] in child_ids
        }
        self.assertTrue(before, "fixture should start with pending items")

        await self.runtime.recover_once("periodic")

        after = [
            item
            for item in self.runtime.reconciliation_items()
            if item["child_id"] in child_ids
        ]
        # Nothing about this task still needs an operator's attention.
        self.assertEqual([], after)

    async def test_unexpired_owned_lock_and_projection_are_settled(self):
        _task, declarations = await self._build_live_orphan()
        child_id = declarations[0]["child_id"]
        _owner, handle = self._hold_child_lock(declarations[0])
        self._set_runtime_tokens(child_id, handle)

        await self.runtime.recover_once("periodic")
        await self.runtime.recover_once("periodic")

        self.assertEqual([], list(self.runtime.locks.records()))
        self.assertEqual(
            [],
            self.runtime.children.runtime(child_id)[
                "selective_hold_tokens"
            ],
        )
        self.assertEqual([], self.runtime.reconciliation_items())

    async def test_expired_lock_projection_converges_across_two_sweeps(self):
        _task, declarations = await self._build_live_orphan()
        child_id = declarations[0]["child_id"]
        _owner, handle = self._hold_child_lock(
            declarations[0], expired=True
        )
        self._set_runtime_tokens(child_id, handle)

        await self.runtime.recover_once("periodic")
        await self.runtime.recover_once("periodic")

        self.assertEqual([], list(self.runtime.locks.records()))
        self.assertEqual(
            [],
            self.runtime.children.runtime(child_id)[
                "selective_hold_tokens"
            ],
        )
        self.assertEqual([], self.runtime.reconciliation_items())

    async def test_selective_conflict_hold_is_released_with_exact_fencing(self):
        _task, declarations = await self._build_live_orphan()
        child_id = declarations[0]["child_id"]
        _owner, handle = self._hold_child_lock(
            declarations[0], conflict_hold=True
        )
        projected = self.runtime.children.runtime(child_id)[
            "selective_hold_tokens"
        ]
        self.assertEqual(handle.tokens[0].generation, projected[0]["generation"])

        await self.runtime.recover_once("periodic")
        await self.runtime.recover_once("periodic")

        self.assertEqual([], list(self.runtime.locks.records()))
        self.assertEqual(
            [],
            self.runtime.children.runtime(child_id)[
                "selective_hold_tokens"
            ],
        )

    async def test_stale_projection_without_lock_is_cleared(self):
        _task, declarations = await self._build_live_orphan()
        child_id = declarations[0]["child_id"]
        owner, handle = self._hold_child_lock(
            declarations[0], conflict_hold=True
        )
        self.runtime.locks.release_conflict_hold(
            owner=owner,
            tokens=handle.tokens,
            reason_code="orphan_recovery_test",
        )
        self.assertEqual([], list(self.runtime.locks.records()))
        self.assertTrue(
            self.runtime.children.runtime(child_id)["selective_hold_tokens"]
        )

        await self.runtime.recover_once("periodic")

        self.assertEqual(
            [],
            self.runtime.children.runtime(child_id)[
                "selective_hold_tokens"
            ],
        )

    async def test_crash_after_terminalization_restarts_into_lock_cleanup(self):
        _task, declarations = await self._build_live_orphan()
        child_id = declarations[0]["child_id"]
        _owner, handle = self._hold_child_lock(declarations[0])
        self._set_runtime_tokens(child_id, handle)

        with patch.object(
            self.runtime,
            "_release_orphaned_child_locks",
            side_effect=SystemExit("simulated process loss"),
        ):
            with self.assertRaises(SystemExit):
                await self.runtime.recover_once("periodic")
        claimed = self.runtime.children.get(child_id)
        self.assertTrue(claimed.terminal)
        self.assertTrue(list(self.runtime.locks.records()))

        # Reconstruct the runtime exactly as a process restart would.
        restarted = self._restart_runtime()
        await self.runtime.recover_once("startup")
        await self.runtime.recover_once("periodic")

        self.assertEqual([], list(self.runtime.locks.records()))
        self.assertEqual(
            [],
            self.runtime.children.runtime(child_id)[
                "selective_hold_tokens"
            ],
        )
        self.assertEqual([], self.runtime.reconciliation_items())

    async def test_crash_after_lock_release_restarts_into_token_cleanup(self):
        _task, declarations = await self._build_live_orphan()
        child_id = declarations[0]["child_id"]
        _owner, handle = self._hold_child_lock(declarations[0])
        self._set_runtime_tokens(child_id, handle)
        original_update = self.runtime.children.update_runtime

        def crash_before_projection(target_id, *, changes):
            if (
                target_id == child_id
                and "selective_hold_tokens" in changes
                and not self.runtime.locks.records()
            ):
                raise SystemExit("simulated process loss")
            return original_update(target_id, changes=changes)

        with patch.object(
            self.runtime.children,
            "update_runtime",
            side_effect=crash_before_projection,
        ):
            with self.assertRaises(SystemExit):
                await self.runtime.recover_once("periodic")
        self.assertEqual([], list(self.runtime.locks.records()))
        self.assertTrue(
            self.runtime.children.runtime(child_id)["selective_hold_tokens"]
        )

        self._restart_runtime()
        await self.runtime.recover_once("startup")
        await self.runtime.recover_once("periodic")

        self.assertEqual([], list(self.runtime.locks.records()))
        self.assertEqual(
            [],
            self.runtime.children.runtime(child_id)["selective_hold_tokens"],
        )
        self.assertEqual([], self.runtime.reconciliation_items())

    async def test_crash_after_token_cleanup_restarts_into_audit_delivery(self):
        _task, declarations = await self._build_live_orphan()
        child_id = declarations[0]["child_id"]
        _owner, handle = self._hold_child_lock(declarations[0])
        self._set_runtime_tokens(child_id, handle)

        with patch.object(
            self.runtime,
            "_audit_record_events",
            side_effect=SystemExit("simulated process loss"),
        ):
            with self.assertRaises(SystemExit):
                await self.runtime.recover_once("periodic")
        runtime = self.runtime.children.runtime(child_id)
        self.assertEqual([], runtime["selective_hold_tokens"])
        self.assertEqual(
            "orphaned_pre_dispatch_audit_pending",
            runtime["reconciliation_result"],
        )

        self._restart_runtime()
        await self.runtime.recover_once("startup")
        await self.runtime.recover_once("periodic")

        self.assertEqual(
            ORPHAN_RECONCILIATION_RESULT,
            self.runtime.children.runtime(child_id)[
                "reconciliation_result"
            ],
        )
        self.assertEqual([], self.runtime.reconciliation_items())

    async def test_crash_after_audit_cursor_restarts_without_duplicate_audit(self):
        _task, declarations = await self._build_live_orphan()
        child_id = declarations[0]["child_id"]
        before = self.runtime.children.get(child_id)
        self.runtime._audit_record_events(declarations[0], before)
        self.audit_path.write_text("", encoding="utf-8")
        original_update = self.runtime.children.update_runtime

        def crash_before_completion(target_id, *, changes):
            if changes.get("reconciliation_result") == ORPHAN_RECONCILIATION_RESULT:
                raise SystemExit("simulated process loss")
            return original_update(target_id, changes=changes)

        with patch.object(
            self.runtime.children,
            "update_runtime",
            side_effect=crash_before_completion,
        ):
            with self.assertRaises(SystemExit):
                await self.runtime.recover_once("periodic")
        first = [
            item
            for item in self._audit_entries()
            if item.get("event") == "f3_execution_cancelled"
        ]
        self.assertEqual(1, len(first))

        self._restart_runtime()
        await self.runtime.recover_once("startup")
        await self.runtime.recover_once("periodic")
        second = [
            item
            for item in self._audit_entries()
            if item.get("event") == "f3_execution_cancelled"
        ]

        self.assertEqual(first, second)
        self.assertEqual(
            ORPHAN_RECONCILIATION_RESULT,
            self.runtime.children.runtime(child_id)[
                "reconciliation_result"
            ],
        )

    async def test_later_fencing_generation_is_never_released(self):
        _task, declarations = await self._build_live_orphan()
        child_id = declarations[0]["child_id"]
        owner, handle = self._hold_child_lock(declarations[0])
        self._set_runtime_tokens(child_id, handle)
        self.runtime.locks.release(handle)
        later_owner = LockOwner(
            owner_id="later-owner",
            task_id=child_id,
            plan_id=owner.plan_id,
            operation_id=owner.operation_id,
            attempt_id="later-attempt",
        )
        later = self.runtime.locks.acquire_once(
            (
                LockRequest(
                    key=handle.tokens[0].key,
                    scopes=(LockScope.RESOURCE,),
                    mode=LockMode.EXCLUSIVE,
                    reason_codes=("later_generation",),
                ),
            ),
            owner=later_owner,
            timing=LockTiming(120, 20, 0, 0.05),
            now=self.service.now(),
        )

        await self.runtime.recover_once("periodic")

        records = list(self.runtime.locks.records())
        self.assertEqual(1, len(records))
        self.assertEqual(later.tokens[0].generation, records[0].generation)
        self.assertEqual("later-owner", records[0].owner_id)
        self.assertEqual(
            "bounded_retry",
            self.runtime.children.runtime(child_id)[
                "reconciliation_result"
            ],
        )

    async def test_public_parent_and_child_projections_agree_exactly(self):
        task, _declarations = await self._build_live_orphan()
        original_error = task.last_error

        await self.runtime.recover_once("periodic")

        detail = self.runtime.decorate_task(
            self.service._load_task(task.task_id)
        )
        public_children = detail["f3_children"]
        summary_children = detail["verification_summary"]["children"]
        by_id = {
            child["child_execution_id"]: child
            for child in summary_children
        }
        for child in public_children:
            projected = by_id[child["child_execution_id"]]
            self.assertEqual(child["state"], projected["state"])
            self.assertEqual(
                child["normalized_outcome"],
                projected["normalized_outcome"],
            )
            self.assertEqual(child["dispatch_count"], projected["dispatch_count"])
            self.assertEqual("terminal", projected["state"])
            self.assertTrue(projected["terminal"])
        self.assertEqual("failed_pre_dispatch", detail["state"])
        self.assertEqual("failed_pre_dispatch", detail["terminal_outcome"])
        self.assertEqual(original_error, detail["last_error"])
        child_ids = {item["child_execution_id"] for item in public_children}
        self.assertEqual(
            child_ids,
            set(detail["legacy_projection"]["child_execution_ids"]),
        )
        self.assertFalse(
            any(
                item["child_id"] in child_ids
                for item in self.runtime.reconciliation_items()
            )
        )

    async def test_orphan_cancellation_audit_is_once_across_restart(self):
        task, declarations = await self._build_live_orphan()
        child_id = declarations[0]["child_id"]
        before_record = self.runtime.children.get(child_id)
        self.runtime._audit_record_events(declarations[0], before_record)
        self.audit_path.write_text("", encoding="utf-8")

        await self.runtime.recover_once("periodic")
        first = [
            item
            for item in self._audit_entries()
            if item.get("event") == "f3_execution_cancelled"
        ]

        restarted = self._restart_runtime()
        await self.runtime.recover_once("startup")
        second = [
            item
            for item in self._audit_entries()
            if item.get("event") == "f3_execution_cancelled"
        ]

        self.assertEqual(1, len(first))
        self.assertEqual(first, second)
        self.assertEqual(task.task_id, first[0]["task_id"])
        self.assertEqual(child_id, first[0]["child_execution_id"])
        self.assertEqual(0, first[0]["evidence_references"]["dispatch_count"])
        self.assertEqual(
            "orphaned_terminal_parent_recovery",
            first[0]["evidence_references"]["reason_code"],
        )
        self.assertEqual("none", first[0]["fallback"])

    async def test_maximum_namespace_reconciliation_is_bounded_and_fair(self):
        declarations = tuple(
            {
                "child_id": f"child-{index:04d}",
                "public_task_id": f"task-{index // 8:04d}",
            }
            for index in range(8_192)
        )
        attempted = []

        candidate_runtime = {
            "next_eligible_at": None,
        }
        with (
            patch.object(
                self.runtime,
                "_orphan_cleanup_pending",
                return_value=True,
            ) as cleanup_pending,
            patch.object(self.runtime.locks, "records", return_value=()),
            patch.object(
                self.runtime.children, "get", return_value=object()
            ),
            patch.object(
                self.runtime.children,
                "runtime",
                return_value=candidate_runtime,
            ),
            patch.object(
                self.service.task_repository, "get", return_value=object()
            ),
            patch.object(
                self.runtime,
                "_reconcile_orphaned_child",
                side_effect=lambda declaration, **_kwargs: (
                    attempted.append(declaration["child_id"]) or (True, True)
                ),
            ),
        ):
            first = self.runtime._reconcile_orphaned_children(
                declarations=declarations,
                now=self.service.now(),
                sweep_started=0.0,
                transition_limit=RECOVERY_BATCH_SIZE,
                monotonic=lambda: 0.0,
            )
            second = self.runtime._reconcile_orphaned_children(
                declarations=declarations,
                now=self.service.now(),
                sweep_started=0.0,
                transition_limit=RECOVERY_BATCH_SIZE,
                monotonic=lambda: 0.0,
            )

        self.assertEqual(RECOVERY_BATCH_SIZE, first["processed"])
        self.assertEqual(RECOVERY_BATCH_SIZE, second["processed"])
        self.assertEqual(RECOVERY_BATCH_SIZE * 2, len(set(attempted)))
        self.assertLessEqual(
            cleanup_pending.call_count,
            ORPHAN_RECOVERY_SCAN_LIMIT * 2,
        )

        self.runtime._orphan_recovery_after = None
        timed_attempts = []
        clock_calls = 0

        def bounded_clock():
            nonlocal clock_calls
            clock_calls += 1
            return 0.0 if clock_calls <= 3 else 6.0

        with (
            patch.object(
                self.runtime,
                "_orphan_cleanup_pending",
                return_value=True,
            ),
            patch.object(self.runtime.locks, "records", return_value=()),
            patch.object(
                self.runtime.children, "get", return_value=object()
            ),
            patch.object(
                self.runtime.children,
                "runtime",
                return_value=candidate_runtime,
            ),
            patch.object(
                self.service.task_repository, "get", return_value=object()
            ),
            patch.object(
                self.runtime,
                "_reconcile_orphaned_child",
                side_effect=lambda declaration, **_kwargs: (
                    timed_attempts.append(declaration["child_id"])
                    or (True, True)
                ),
            ),
        ):
            timed = self.runtime._reconcile_orphaned_children(
                declarations=declarations,
                now=self.service.now(),
                sweep_started=0.0,
                transition_limit=RECOVERY_BATCH_SIZE,
                monotonic=bounded_clock,
            )
        self.assertLessEqual(timed["processed"], 1)
        self.assertEqual(timed["processed"], len(timed_attempts))

if __name__ == "__main__":
    unittest.main()
