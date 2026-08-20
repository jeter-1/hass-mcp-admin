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

import copy
from datetime import datetime, timedelta
import hashlib
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
from ha_mcp_engineering.errors import GovernanceError  # noqa: E402
from ha_mcp_engineering.f3.persistence import (  # noqa: E402
    ExecutionStorageError,
)
from ha_mcp_engineering.governance.task_models import (  # noqa: E402
    ExecutionTaskState,
    new_execution_task,
)
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
    PROPOSED_AUTOMATION,
)
from tests.test_f3_runtime_integration import (  # noqa: E402
    _ExactFakeConfigurationGateway,
    _provider_identity,
)
from ha_mcp_engineering.f3.models import (  # noqa: E402
    ExecutionIdentity,
    ExecutorTiming,
    LockHandle,
    LockOwner,
    LockTiming,
    LockToken,
)
from ha_mcp_engineering.f3_runtime.repository import (  # noqa: E402
    canonical_hash,
    child_declaration,
    ExecutionRecordCorrupt,
    RECOVERY_DECLARATION_PAGE_SIZE,
)
from ha_mcp_engineering.f3.contracts import (  # noqa: E402
    F3_ADAPTER_CONTRACT_MODEL,
)
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
    ORPHAN_RECOVERY_SCAN_LIMIT,
    ORPHAN_RECONCILIATION_RESULT,
    RECOVERY_BATCH_SIZE,
    _persisted_audit_event_id,
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
            attempt_id=declaration["attempt_id"],
            request_id=declaration["request_id"],
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

    def _release_fixture_locks(self, child_id):
        record = self.runtime.children.get(child_id)
        identity = record.execution_identity()
        lock_records = [
            item
            for item in self.runtime.locks.records()
            if item.task_id == child_id
        ]
        self.assertTrue(lock_records)
        handle = LockHandle(
            owner=LockOwner(
                owner_id=identity.owner_id,
                task_id=identity.task_id,
                plan_id=identity.plan_id,
                operation_id=record.operation,
                attempt_id=identity.attempt_id,
            ),
            tokens=tuple(
                LockToken(item.key, item.generation, item.mode)
                for item in lock_records
            ),
            acquired_at=min(item.acquired_at for item in lock_records),
            lease_expires_at=min(
                item.lease_expires_at for item in lock_records
            ),
            timing=LockTiming(120, 20, 0, 0.05),
        )
        self.runtime.locks.release(handle)

    def _mutate_exact_lock(self, handle, **changes):
        """Inject one valid but authority-mismatched durable lock field."""

        token = handle.tokens[0]
        expired_at = self.service.now() - timedelta(seconds=1)
        acquired_at = expired_at - timedelta(seconds=120)

        def mutate(state):
            record = next(
                item
                for item in state["records"]
                if item.key == token.key
                and item.generation == token.generation
            )
            record.acquired_at = acquired_at.isoformat()
            record.last_renewed_at = acquired_at.isoformat()
            record.lease_expires_at = expired_at.isoformat()
            for name, value in changes.items():
                setattr(record, name, value)
            if "generation" in changes:
                state["next_generation"] = max(
                    state["next_generation"],
                    int(changes["generation"]) + 1,
                )
            return None, True

        self.runtime.locks._transact(mutate)

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

    async def _post_intent_active_child(
        self, target_id="active_recovery_target"
    ):
        """Create one real, observation-only child with a 120-second deadline."""

        proposed = copy.deepcopy(PROPOSED_AUTOMATION)
        proposed["id"] = target_id
        created = await self.service.create_configuration_plan(
            title="Active recovery deadline fixture",
            description="Exact post-intent scheduler acceptance fixture",
            operations=[
                {
                    "operation_id": "update_active_recovery_automation",
                    "resource_type": "automation",
                    "action": "create",
                    "target_id": target_id,
                    "depends_on": [],
                    "proposed_config": proposed,
                }
            ],
        )
        await self.approve(created)
        def crash_after_intent(stage):
            if stage == "after_durable_intent_persistence":
                raise SystemExit("simulated process loss after durable intent")

        self.runtime.children._fault_hook = crash_after_intent
        try:
            with self.assertRaises(SystemExit):
                await self.service.apply(
                    created["plan_id"], created["plan_hash"]
                )
        finally:
            self.runtime.children._fault_hook = None
        task = self.service.task_repository.get_for_plan(created["plan_id"])
        declaration = self.runtime.children.declarations_for_task(
            task.task_id
        )[0]
        record = self.runtime.children.get(declaration["child_id"])
        identity = record.execution_identity()
        self.runtime.children.mutate_claimed(
            declaration["child_id"],
            owner_id=identity.owner_id,
            claim_generation=record.claim_generation,
            mutator=lambda value: setattr(
                value,
                "claim_expires_at",
                (self.service.now() - timedelta(seconds=1)).isoformat(),
            ),
        )
        record = self.runtime.children.get(declaration["child_id"])
        self.assertEqual(1, record.dispatch_count)
        self.assertEqual(
            self.service.now() + timedelta(seconds=120),
            datetime.fromisoformat(
                record.dispatch_intent["evidence_deadline"]
            ),
        )
        return task, declaration

    async def _preintent_active_child(self, target_id):
        proposed = copy.deepcopy(PROPOSED_AUTOMATION)
        proposed["id"] = target_id
        created = await self.service.create_configuration_plan(
            title=f"Active recovery fixture {target_id}",
            description="Bounded active recovery scheduler fixture",
            operations=[
                {
                    "operation_id": f"create_{target_id}",
                    "resource_type": "automation",
                    "action": "create",
                    "target_id": target_id,
                    "depends_on": [],
                    "proposed_config": proposed,
                }
            ],
        )
        await self.approve(created)
        plan = self.service._load(created["plan_id"])
        task, _prepared, _requests = await self.runtime._initialize(
            plan, created["plan_hash"]
        )
        task = self.runtime._enter_public_preflight(task)
        declaration = self.runtime.children.declarations_for_task(
            task.task_id
        )[0]
        return task, declaration

    def _populate_terminal_history(
        self, *, task_count: int, declarations_per_task: int = 8
    ):
        """Write valid settled F3 history through the real repositories."""

        timestamp = self.service.now().isoformat()
        created_ids = []
        for task_index in range(task_count):
            prefix = f"rr9-history-{task_index}"
            task_id = hashlib.sha256(
                f"{prefix}-task".encode()
            ).hexdigest()[:32]
            plan_id = hashlib.sha256(
                f"{prefix}-plan".encode()
            ).hexdigest()[:32]
            plan_hash = hashlib.sha256(
                f"{prefix}-plan-hash".encode()
            ).hexdigest()
            task = new_execution_task(
                task_id=task_id,
                plan_id=plan_id,
                plan_hash=plan_hash,
                operation="configuration_plan",
                target={
                    "target_type": "automation",
                    "target_id": f"history_{task_index}",
                },
                timestamp=timestamp,
                execution_request_id=f"history-request-{task_index}",
                idempotency_key=hashlib.sha256(
                    f"{prefix}-idempotency".encode()
                ).hexdigest(),
                approval_reference={},
                legacy_projection={},
            )
            declarations = []
            for ordinal in range(declarations_per_task):
                operation_id = f"history_operation_{task_index}_{ordinal}"
                declarations.append(
                    child_declaration(
                        public_task_id=task_id,
                        plan_id=plan_id,
                        plan_hash=plan_hash,
                        plan_contract_version=2,
                        operation_id=operation_id,
                        ordinal=ordinal,
                        dependency_ids=(),
                        adapter_id="history_fixture_adapter",
                        capability_id="update_automation_configuration",
                        prepared_operation_hash=hashlib.sha256(
                            f"{prefix}-prepared-{ordinal}".encode()
                        ).hexdigest(),
                        target_type="automation",
                        target_id=f"history_{task_index}_{ordinal}",
                        attempt_id=f"history-attempt-{task_index}-{ordinal}",
                        request_id=f"history-request-{task_index}",
                        idempotency_key=f"history-key-{task_index}-{ordinal}",
                        complete_lock_request_hash=hashlib.sha256(
                            f"{prefix}-locks".encode()
                        ).hexdigest(),
                        approval_bundle_hash=hashlib.sha256(
                            f"{prefix}-approval".encode()
                        ).hexdigest(),
                        selective_hold_keys=(
                            f"automation:history_{task_index}_{ordinal}",
                        ),
                    )
                )
            sequence_hash = canonical_hash(
                {
                    "model": "rr9-terminal-history-v1",
                    "task_id": task_id,
                    "children": [item["child_id"] for item in declarations],
                }
            )
            self.runtime._mark_task_authority(
                task,
                sequence_hash,
                [item["child_id"] for item in declarations],
            )
            task.append_event(
                "preflight_failed",
                timestamp,
                new_state=ExecutionTaskState.FAILED_PRE_DISPATCH,
                changes={
                    "completed_at": timestamp,
                    "terminal_outcome": "failed_pre_dispatch",
                    "last_error": {
                        "code": "bounded_terminal_history_fixture"
                    },
                },
                request_id=f"history-request-{task_index}",
            )
            self.runtime.children.initialize_task_sequence(
                task=task,
                task_repository=self.service.task_repository,
                declarations=declarations,
                sequence_hash=sequence_hash,
            )
            created_ids.append(task_id)
        return tuple(created_ids)

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

    async def test_crash_after_audit_append_before_cursor_is_idempotent(self):
        _task, declarations = await self._build_live_orphan()
        child_id = declarations[0]["child_id"]
        before = self.runtime.children.get(child_id)
        self.runtime._audit_record_events(declarations[0], before)
        self.audit_path.write_text("", encoding="utf-8")
        original_write = self.service.audit.write

        def append_then_crash(entry):
            written = original_write(entry)
            if entry.get("event") == "f3_execution_cancelled":
                raise SystemExit("simulated process loss after audit append")
            return written

        with patch.object(
            self.service.audit,
            "write",
            side_effect=append_then_crash,
        ):
            with self.assertRaises(SystemExit):
                await self.runtime.recover_once("periodic")
        first = [
            item
            for item in self._audit_entries()
            if item.get("event") == "f3_execution_cancelled"
        ]
        self.assertEqual(1, len(first))
        self.assertRegex(first[0]["audit_event_id"], r"^[0-9a-f]{64}$")

        self._restart_runtime()
        await self.runtime.recover_once("startup")
        second = [
            item
            for item in self._audit_entries()
            if item.get("event") == "f3_execution_cancelled"
        ]

        self.assertEqual(first, second)
        self.assertEqual(
            len(self.runtime.children.get(child_id).events),
            self.runtime.children.runtime(child_id)["audited_event_count"],
        )

    async def test_no_diagnostic_cancellation_audit_is_idempotent(self):
        _task, declarations = await self._build_live_orphan()
        declaration = declarations[0]
        child_id = declaration["child_id"]
        before = self.runtime.children.get(child_id)
        self.runtime._audit_record_events(declaration, before)
        self.audit_path.write_text("", encoding="utf-8")
        self.assertTrue(
            self.runtime.children.cancel(child_id, now=self.service.now())
        )
        original_write = self.service.audit.write

        def append_then_crash(entry):
            written = original_write(entry)
            if entry.get("event") == "f3_execution_cancelled":
                raise SystemExit("simulated process loss after audit append")
            return written

        with patch.object(
            self.service.audit,
            "write",
            side_effect=append_then_crash,
        ):
            with self.assertRaises(SystemExit):
                await self.runtime.recover_once("periodic")
        first = [
            item
            for item in self._audit_entries()
            if item.get("event") == "f3_execution_cancelled"
        ]
        self.assertEqual(1, len(first))
        self.assertRegex(first[0]["audit_event_id"], r"^[0-9a-f]{64}$")

        self._restart_runtime()
        await self.runtime.recover_once("startup")
        second = [
            item
            for item in self._audit_entries()
            if item.get("event") == "f3_execution_cancelled"
        ]
        self.assertEqual(first, second)

    async def test_executor_cancellation_without_diagnostic_has_stable_id(self):
        _task, declarations = await self._build_live_orphan()
        declaration = declarations[0]
        child_id = declaration["child_id"]
        record = self.runtime.children.get(child_id)
        self.runtime._audit_record_events(declaration, record)
        self.audit_path.write_text("", encoding="utf-8")

        cancelled = await self.runtime._executor(120).cancel(child_id)
        self.assertTrue(cancelled)
        terminal = self.runtime.children.get(child_id)
        self.assertTrue(self.runtime._audit_record_events(declaration, terminal))
        first = [
            item
            for item in self._audit_entries()
            if item.get("event") == "f3_execution_cancelled"
        ]
        self.assertEqual(1, len(first))
        expected = _persisted_audit_event_id(
            child_id,
            next(
                item
                for item in terminal.events
                if item["event_type"] == "execution_cancelled"
            ),
        )
        self.assertEqual(expected, first[0]["audit_event_id"])

        self._restart_runtime()
        self.assertTrue(
            self.runtime._audit_record_events(
                declaration, self.runtime.children.get(child_id)
            )
        )
        self.assertEqual(first, [
            item
            for item in self._audit_entries()
            if item.get("event") == "f3_execution_cancelled"
        ])

    async def test_persisted_audit_identity_binds_child_sequence_and_content(self):
        _task, declarations = await self._build_live_orphan()
        declaration = declarations[0]
        record = self.runtime.children.get(declaration["child_id"])
        event = dict(record.events[0])
        identity = _persisted_audit_event_id(
            declaration["child_id"], event
        )
        self.assertEqual(
            identity,
            _persisted_audit_event_id(declaration["child_id"], event),
        )
        self._restart_runtime()
        restarted_event = self.runtime.children.get(
            declaration["child_id"]
        ).events[0]
        self.assertEqual(
            identity,
            _persisted_audit_event_id(
                declaration["child_id"], restarted_event
            ),
        )
        self.assertNotEqual(
            identity,
            _persisted_audit_event_id("different-child", event),
        )
        later = dict(event)
        later["sequence"] = event["sequence"] + 1
        self.assertNotEqual(
            identity,
            _persisted_audit_event_id(declaration["child_id"], later),
        )
        changed = dict(event)
        changed["event_type"] = "execution_replayed"
        self.assertNotEqual(
            identity,
            _persisted_audit_event_id(declaration["child_id"], changed),
        )
        invalid = dict(event)
        invalid["unsupported"] = float("nan")
        with self.assertRaises(GovernanceError):
            _persisted_audit_event_id(declaration["child_id"], invalid)

    async def test_non_cancellation_event_is_idempotent_across_append_crash(self):
        _task, declarations = await self._build_live_orphan()
        declaration = declarations[0]
        child_id = declaration["child_id"]
        record = self.runtime.children.get(child_id)
        self.audit_path.write_text("", encoding="utf-8")
        original_write = self.service.audit.write

        def append_then_crash(entry):
            written = original_write(entry)
            if entry.get("event") == "f3_execution_started":
                raise SystemExit("simulated persisted-event append crash")
            return written

        with patch.object(
            self.service.audit, "write", side_effect=append_then_crash
        ):
            with self.assertRaises(SystemExit):
                self.runtime._audit_record_events(declaration, record)
        first = self._audit_entries()
        self.assertEqual(1, len(first))
        self.assertEqual("f3_execution_started", first[0]["event"])

        self._restart_runtime()
        self.assertTrue(
            self.runtime._audit_record_events(
                declaration, self.runtime.children.get(child_id)
            )
        )
        matching = [
            item
            for item in self._audit_entries()
            if item.get("audit_event_id") == first[0]["audit_event_id"]
        ]
        self.assertEqual(1, len(matching))

    async def test_audit_failure_leaves_cursor_pending_then_retries_once(self):
        _task, declarations = await self._build_live_orphan()
        declaration = declarations[0]
        child_id = declaration["child_id"]
        record = self.runtime.children.get(child_id)
        before = self.runtime.children.runtime(child_id)[
            "audited_event_count"
        ]
        with patch.object(self.service.audit, "write", return_value=False):
            self.assertFalse(
                self.runtime._audit_record_events(declaration, record)
            )
        self.assertEqual(
            before,
            self.runtime.children.runtime(child_id)["audited_event_count"],
        )
        self.assertTrue(self.runtime._audit_record_events(declaration, record))
        self.assertEqual(
            len(record.events),
            self.runtime.children.runtime(child_id)["audited_event_count"],
        )
        identities = [
            item["audit_event_id"]
            for item in self._audit_entries()
            if "audit_event_id" in item
        ]
        self.assertEqual(len(identities), len(set(identities)))

    def test_audit_truncation_preserves_id_and_invalid_id_does_not_dedup(self):
        path = self.root / "bounded-audit.jsonl"
        audit = AuditLogger(
            str(path),
            "synthetic-review-secret",
            max_payload_chars=120,
        )
        identity = "a" * 64
        self.assertTrue(
            audit.write(
                {
                    "event": "f3_execution_started",
                    "audit_event_id": identity,
                    "bounded": "x" * 500,
                }
            )
        )
        truncated = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(truncated["payload_truncated"])
        self.assertEqual(identity, truncated["audit_event_id"])

        invalid = {"event": "f3_event", "audit_event_id": "not-valid"}
        self.assertTrue(audit.write(invalid))
        self.assertTrue(audit.write(invalid))
        entries = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            2,
            sum(item.get("audit_event_id") == "not-valid" for item in entries),
        )

    async def test_active_post_intent_child_is_not_hidden_behind_cursor(self):
        await self._build_live_orphan()
        _task, declaration = await self._post_intent_active_child()
        child_id = declaration["child_id"]
        deadline = self.runtime.children.get(child_id).dispatch_intent[
            "evidence_deadline"
        ]
        current = self.runtime.children.recovery_cursor()
        self.runtime.children.advance_recovery_cursor(
            expected=current,
            next_cursor={
                "model": "f3-recovery-declaration-cursor-v1",
                "schema_version": 1,
                "public_task_id": declaration["public_task_id"],
                "operation_ordinal": declaration["operation_ordinal"],
                "child_id": child_id,
            },
        )
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )
        self._restart_runtime()

        result = await self.runtime.recover_once("startup")

        record = self.runtime.children.get(child_id)
        self.assertEqual(1, result["active_recovery_transitions"])
        self.assertTrue(
            record.terminal
            or any(
                item["event_type"] == "recovery_claimed"
                for item in record.events
            )
        )
        self.assertEqual(1, record.dispatch_count)
        self.assertEqual(deadline, record.dispatch_intent["evidence_deadline"])
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_dense_terminal_history_does_not_delay_active_readback(self):
        _task, declaration = await self._post_intent_active_child(
            "dense_history_active"
        )
        self._populate_terminal_history(task_count=130)
        child_id = declaration["child_id"]
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )
        self._restart_runtime()

        result = await self.runtime.recover_once("startup")

        self.assertEqual(1, result["active_recovery_transitions"])
        self.assertLessEqual(
            result["declarations_examined"],
            RECOVERY_DECLARATION_PAGE_SIZE,
        )
        self.assertLessEqual(
            result["manifest_reads"], RECOVERY_DECLARATION_PAGE_SIZE
        )
        self.assertTrue(
            any(
                item["event_type"] == "recovery_claimed"
                for item in self.runtime.children.get(child_id).events
            )
        )
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_seventeenth_active_task_remains_eligible_for_next_sweep(self):
        declarations = []
        for index in range(17):
            _task, declaration = await self._preintent_active_child(
                f"batch_active_{index:02d}"
            )
            declarations.append(declaration)

        first = await self.runtime.recover_once("periodic")
        first_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )
        second = await self.runtime.recover_once("periodic")
        second_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )

        self.assertEqual(RECOVERY_BATCH_SIZE, first["recovery_transitions"])
        self.assertEqual(16, first_writes)
        self.assertEqual(1, second["recovery_transitions"])
        self.assertEqual(17, second_writes)
        self.assertTrue(
            all(
                self.runtime.children.get(item["child_id"]).terminal
                for item in declarations
            )
        )

    async def test_active_recovery_prioritizes_immutable_evidence_deadline(self):
        base_now = self.service.now()
        current = {"value": base_now}
        self.service.now = lambda: current["value"]
        _first_task, first = await self._post_intent_active_child(
            "deadline_first"
        )
        self._release_fixture_locks(first["child_id"])
        current["value"] = base_now + timedelta(seconds=30)
        _second_task, second = await self._post_intent_active_child(
            "deadline_second"
        )
        order = []
        original_execute = self.runtime._execute_child

        async def ordered_execute(plan, task, declaration, operation, requests):
            order.append(declaration["child_id"])
            return await original_execute(
                plan, task, declaration, operation, requests
            )

        with patch.object(
            self.runtime, "_execute_child", side_effect=ordered_execute
        ):
            await self.runtime.recover_once("periodic")

        self.assertEqual(first["child_id"], order[0])
        first_deadline = datetime.fromisoformat(
            self.runtime.children.get(first["child_id"]).dispatch_intent[
                "evidence_deadline"
            ]
        )
        second_deadline = datetime.fromisoformat(
            self.runtime.children.get(second["child_id"]).dispatch_intent[
                "evidence_deadline"
            ]
        )
        self.assertLess(first_deadline, second_deadline)
        self.assertEqual(
            0, sum(item[0] == "write" for item in self.gateway.calls)
        )

    async def test_equal_deadlines_use_deterministic_task_operation_order(self):
        _first_task, first = await self._post_intent_active_child(
            "equal_deadline_first"
        )
        self._release_fixture_locks(first["child_id"])
        _second_task, second = await self._post_intent_active_child(
            "equal_deadline_second"
        )
        selection = self.runtime._active_recovery_candidates(
            now=self.service.now(),
            sweep_started=0.0,
            monotonic=lambda: 0.0,
        )["candidates"]
        selected = [item[0] for item in selection]
        expected = sorted(
            (first, second),
            key=lambda item: (
                item["public_task_id"].encode("utf-8"),
                item["operation_ordinal"],
                item["child_id"].encode("utf-8"),
            ),
        )
        self.assertEqual(
            [item["child_id"] for item in expected],
            [item["child_id"] for item in selected],
        )

    async def test_time_budget_keeps_unprocessed_active_work_reachable(self):
        declarations = [
            (await self._preintent_active_child(f"timed_active_{index}"))[1]
            for index in range(2)
        ]
        expired = {"value": False}
        self.runtime._recovery_monotonic = (
            lambda: 6.0 if expired["value"] else 0.0
        )
        original_execute = self.runtime._execute_child
        order = []

        async def expire_after_one(plan, task, declaration, operation, requests):
            order.append(declaration["child_id"])
            result = await original_execute(
                plan, task, declaration, operation, requests
            )
            expired["value"] = True
            return result

        with patch.object(
            self.runtime, "_execute_child", side_effect=expire_after_one
        ):
            first = await self.runtime.recover_once("periodic")
            expired["value"] = False
            second = await self.runtime.recover_once("periodic")

        self.assertEqual(1, first["recovery_transitions"])
        self.assertEqual(1, second["recovery_transitions"])
        self.assertEqual(
            {item["child_id"] for item in declarations}, set(order)
        )

    async def test_backoff_does_not_starve_later_active_work(self):
        declarations = [
            (await self._preintent_active_child(f"backoff_active_{index}"))[1]
            for index in range(2)
        ]
        selection = self.runtime._active_recovery_candidates(
            now=self.service.now(),
            sweep_started=0.0,
            monotonic=lambda: 0.0,
        )["candidates"]
        failing_id = selection[0][0]["child_id"]
        later_id = selection[1][0]["child_id"]
        original_execute = self.runtime._execute_child
        failed = {"value": False}

        async def fail_first(plan, task, declaration, operation, requests):
            if declaration["child_id"] == failing_id and not failed["value"]:
                failed["value"] = True
                raise RuntimeError("bounded synthetic recovery failure")
            return await original_execute(
                plan, task, declaration, operation, requests
            )

        with patch.object(
            self.runtime, "_execute_child", side_effect=fail_first
        ):
            first = await self.runtime.recover_once("periodic")
        self.assertEqual(2, first["recovery_transitions"])
        self.assertTrue(self.runtime.children.get(later_id).terminal)
        retry_at = datetime.fromisoformat(
            self.runtime.children.runtime(failing_id)["next_eligible_at"]
        )
        same_time = await self.runtime.recover_once("periodic")
        self.assertEqual(0, same_time["active_recovery_transitions"])
        self.service.now = lambda: retry_at

        retried = await self.runtime.recover_once("periodic")

        self.assertEqual(1, retried["active_recovery_transitions"])
        self.assertTrue(self.runtime.children.get(failing_id).terminal)
        self.assertEqual(
            {item["child_id"] for item in declarations},
            {failing_id, later_id},
        )

    async def test_crash_after_active_discovery_keeps_candidate_reachable(self):
        _task, declaration = await self._preintent_active_child(
            "active_discovery_crash"
        )
        before_cursor = self.runtime.children.active_recovery_cursor()
        with patch.object(
            self.runtime,
            "_recover_active_candidates",
            side_effect=SystemExit("simulated crash after discovery"),
        ):
            with self.assertRaises(SystemExit):
                await self.runtime.recover_once("periodic")
        self.assertEqual(
            before_cursor, self.runtime.children.active_recovery_cursor()
        )

        self._restart_runtime()
        result = await self.runtime.recover_once("startup")

        self.assertEqual(1, result["active_recovery_transitions"])
        self.assertTrue(
            self.runtime.children.get(declaration["child_id"]).terminal
        )
        self.assertEqual(
            1, sum(item[0] == "write" for item in self.gateway.calls)
        )

    async def test_crash_after_active_transition_does_not_redispatch(self):
        _task, declaration = await self._preintent_active_child(
            "active_cursor_crash"
        )
        def crash_before_cursor(**_kwargs):
            raise SystemExit("simulated crash before active cursor commit")

        with patch.object(
            self.runtime.children,
            "advance_active_recovery_cursor",
            side_effect=crash_before_cursor,
        ):
            with self.assertRaises(SystemExit):
                await self.runtime.recover_once("periodic")
        self.assertEqual(
            1, sum(item[0] == "write" for item in self.gateway.calls)
        )

        self._restart_runtime()
        await self.runtime.recover_once("startup")

        record = self.runtime.children.get(declaration["child_id"])
        self.assertTrue(record.terminal)
        self.assertEqual(1, record.dispatch_count)
        self.assertEqual(
            1, sum(item[0] == "write" for item in self.gateway.calls)
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

    async def test_expired_later_fencing_generation_is_never_released(self):
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
            now=self.service.now() - timedelta(seconds=121),
        )
        before = len(self.gateway.calls)

        await self.runtime.recover_once("periodic")

        records = list(self.runtime.locks.records())
        self.assertEqual(1, len(records))
        self.assertEqual(later.tokens[0].generation, records[0].generation)
        self.assertEqual("later-owner", records[0].owner_id)
        self.assertEqual(before, len(self.gateway.calls))

    async def _assert_expired_identity_mismatch_retained(
        self, field_name, value
    ):
        _task, declarations = await self._build_live_orphan()
        child_id = declarations[0]["child_id"]
        _owner, handle = self._hold_child_lock(declarations[0])
        self._set_runtime_tokens(child_id, handle)
        resolved = value(handle) if callable(value) else value
        self._mutate_exact_lock(handle, **{field_name: resolved})
        before = len(self.gateway.calls)

        await self.runtime.recover_once("periodic")

        self.assertEqual(1, len(self.runtime.locks.records()))
        self.assertEqual(before, len(self.gateway.calls))
        runtime = self.runtime.children.runtime(child_id)
        self.assertEqual("bounded_retry", runtime["reconciliation_result"])
        self.assertTrue(
            any(
                item["child_id"] == child_id
                for item in self.runtime.reconciliation_items()
            )
        )

    async def test_expired_task_mismatch_is_retained(self):
        await self._assert_expired_identity_mismatch_retained(
            "task_id", "different-child"
        )

    async def test_expired_plan_mismatch_is_retained(self):
        await self._assert_expired_identity_mismatch_retained(
            "plan_id", "different-plan"
        )

    async def test_expired_operation_mismatch_is_retained(self):
        await self._assert_expired_identity_mismatch_retained(
            "operation_id", "different-operation"
        )

    async def test_expired_attempt_mismatch_is_retained(self):
        await self._assert_expired_identity_mismatch_retained(
            "attempt_id", "different-attempt"
        )

    async def test_expired_owner_mismatch_is_retained(self):
        await self._assert_expired_identity_mismatch_retained(
            "owner_id", "different-owner"
        )

    async def test_expired_key_mismatch_is_retained(self):
        await self._assert_expired_identity_mismatch_retained(
            "key", "automation:different"
        )

    async def test_expired_mode_mismatch_is_retained(self):
        await self._assert_expired_identity_mismatch_retained(
            "mode", "shared"
        )

    async def test_expired_generation_mismatch_is_retained(self):
        await self._assert_expired_identity_mismatch_retained(
            "generation",
            lambda handle: handle.tokens[0].generation + 1,
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
                "operation_ordinal": index % 8,
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
                declarations=declarations[RECOVERY_BATCH_SIZE:],
                now=self.service.now(),
                sweep_started=0.0,
                transition_limit=RECOVERY_BATCH_SIZE,
                start_cursor=first["next_cursor"],
                monotonic=lambda: 0.0,
            )

        self.assertEqual(RECOVERY_BATCH_SIZE, first["processed"])
        self.assertEqual(RECOVERY_BATCH_SIZE, second["processed"])
        self.assertEqual(RECOVERY_BATCH_SIZE * 2, len(set(attempted)))
        self.assertEqual(
            declarations[RECOVERY_BATCH_SIZE - 1]["child_id"],
            first["next_cursor"]["child_id"],
        )
        self.assertLessEqual(
            cleanup_pending.call_count,
            ORPHAN_RECOVERY_SCAN_LIMIT * 2,
        )

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
        self.assertLessEqual(timed["processed"], 3)
        self.assertLess(timed["processed"], RECOVERY_BATCH_SIZE)
        self.assertEqual(timed["processed"], len(timed_attempts))

    def test_repository_discovery_is_declaration_paged(self):
        paths = tuple(
            Path(f"/tmp/task-{index:04d}.manifest.json")
            for index in range(1_024)
        )

        def manifest(public_task_id, *, locked=False):
            self.assertTrue(locked)
            return {
                "declarations": [
                    {
                        "public_task_id": public_task_id,
                        "operation_ordinal": ordinal,
                        "child_id": f"{public_task_id}-child-{ordinal}",
                    }
                    for ordinal in range(8)
                ]
            }

        with (
            patch.object(
                self.runtime.children,
                "_bounded_paths",
                return_value=paths,
            ),
            patch.object(
                self.runtime.children,
                "_read_recovery_cursor_unlocked",
                return_value=None,
            ),
            patch.object(
                self.runtime.children,
                "manifest_for_task",
                side_effect=manifest,
            ) as read_manifest,
        ):
            page = self.runtime.children.recovery_declaration_page(
                limit=RECOVERY_DECLARATION_PAGE_SIZE,
                should_stop=lambda: False,
            )

        self.assertEqual(
            RECOVERY_DECLARATION_PAGE_SIZE, len(page["declarations"])
        )
        self.assertEqual(128, page["manifest_reads"])
        self.assertEqual(128, read_manifest.call_count)

    def test_paged_repository_discovery_is_fair_across_restart(self):
        paths = tuple(
            Path(f"/tmp/task-{index:04d}.manifest.json")
            for index in range(1_024)
        )

        def manifest(public_task_id, *, locked=False):
            self.assertTrue(locked)
            return {
                "declarations": [
                    {
                        "public_task_id": public_task_id,
                        "operation_ordinal": ordinal,
                        "child_id": f"{public_task_id}-child-{ordinal}",
                    }
                    for ordinal in range(8)
                ]
            }

        with (
            patch.object(
                self.runtime.children,
                "_bounded_paths",
                return_value=paths,
            ),
            patch.object(
                self.runtime.children,
                "manifest_for_task",
                side_effect=manifest,
            ),
        ):
            first = self.runtime.children.recovery_declaration_page()
        self.runtime.children.advance_recovery_cursor(
            expected=first["cursor"],
            next_cursor=first["next_cursor"],
        )

        self._restart_runtime()
        with (
            patch.object(
                self.runtime.children,
                "_bounded_paths",
                return_value=paths,
            ),
            patch.object(
                self.runtime.children,
                "manifest_for_task",
                side_effect=manifest,
            ),
        ):
            second = self.runtime.children.recovery_declaration_page()

        first_ids = {
            item["child_id"] for item in first["declarations"]
        }
        second_ids = {
            item["child_id"] for item in second["declarations"]
        }
        self.assertEqual(RECOVERY_DECLARATION_PAGE_SIZE, len(first_ids))
        self.assertEqual(RECOVERY_DECLARATION_PAGE_SIZE, len(second_ids))
        self.assertTrue(first_ids.isdisjoint(second_ids))

    def test_repository_discovery_stops_at_shared_deadline(self):
        paths = tuple(
            Path(f"/tmp/task-{index:04d}.manifest.json")
            for index in range(1_024)
        )
        reads = 0

        def manifest(public_task_id, *, locked=False):
            nonlocal reads
            self.assertTrue(locked)
            reads += 1
            return {
                "declarations": [
                    {
                        "public_task_id": public_task_id,
                        "operation_ordinal": ordinal,
                        "child_id": f"{public_task_id}-child-{ordinal}",
                    }
                    for ordinal in range(8)
                ]
            }

        with (
            patch.object(
                self.runtime.children,
                "_bounded_paths",
                return_value=paths,
            ),
            patch.object(
                self.runtime.children,
                "_read_recovery_cursor_unlocked",
                return_value=None,
            ),
            patch.object(
                self.runtime.children,
                "manifest_for_task",
                side_effect=manifest,
            ),
        ):
            page = self.runtime.children.recovery_declaration_page(
                should_stop=lambda: reads >= 5
            )

        self.assertEqual(5, page["manifest_reads"])
        self.assertEqual(32, len(page["declarations"]))

    def test_corrupt_recovery_cursor_fails_closed(self):
        self.runtime.children.recovery_cursor_path.write_text(
            '{"model":"unknown"}', encoding="utf-8"
        )

        with self.assertRaises(ExecutionRecordCorrupt):
            self.runtime.children.recovery_cursor()

    def test_corrupt_active_recovery_cursor_fails_closed(self):
        self.runtime.children.active_recovery_cursor_path.write_text(
            '{"model":"unknown"}', encoding="utf-8"
        )

        with self.assertRaises(ExecutionRecordCorrupt):
            self.runtime.children.active_recovery_cursor()

    def test_active_recovery_cursor_compare_and_swap_fails_closed(self):
        first = self.runtime.children.active_recovery_cursor_for_task(
            "active-cursor-first"
        )
        second = self.runtime.children.active_recovery_cursor_for_task(
            "active-cursor-second"
        )
        self.runtime.children.advance_active_recovery_cursor(
            expected=None, next_cursor=first
        )

        with self.assertRaises(ExecutionStorageError):
            self.runtime.children.advance_active_recovery_cursor(
                expected=None, next_cursor=second
            )
        self.assertEqual(
            first, self.runtime.children.active_recovery_cursor()
        )

    async def test_recovery_never_uses_full_namespace_discovery(self):
        await self._build_live_orphan()

        with patch.object(
            self.runtime.children,
            "all_declarations",
            side_effect=AssertionError("unbounded discovery was used"),
        ):
            await self.runtime.recover_once("periodic")

    async def test_paged_discovery_cursor_survives_restart(self):
        await self._build_live_orphan()

        first = await self.runtime.recover_once("periodic")
        first_cursor = self.runtime.children.recovery_cursor()
        self.assertIsNotNone(first_cursor)
        self._restart_runtime()
        second_cursor = self.runtime.children.recovery_cursor()

        self.assertEqual(first_cursor, second_cursor)
        second = await self.runtime.recover_once("startup")
        self.assertLessEqual(
            first["declarations_examined"],
            RECOVERY_DECLARATION_PAGE_SIZE,
        )
        self.assertLessEqual(
            second["declarations_examined"],
            RECOVERY_DECLARATION_PAGE_SIZE,
        )

if __name__ == "__main__":
    unittest.main()
