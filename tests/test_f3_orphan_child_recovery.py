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
from dataclasses import replace
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

    def _store_active_checkpoint(self, *declarations):
        expected = self.runtime.children.active_recovery_checkpoint()
        checkpoint = (
            self.runtime.children.active_recovery_checkpoint_for_candidates(
                declarations
            )
        )
        self.runtime.children.replace_active_recovery_checkpoint(
            expected=expected,
            next_checkpoint=checkpoint,
        )
        return checkpoint

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

    async def _initialized_hvac_sequence(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        plan = self.service._load(created["plan_id"])
        task, prepared, requests = await self.runtime._initialize(
            plan, created["plan_hash"]
        )
        task = self.runtime._enter_public_preflight(task)
        declarations = self.runtime.children.declarations_for_task(
            task.task_id
        )
        self.assertEqual(3, len(declarations))
        return plan, task, declarations, prepared, requests

    async def _complete_sequence_child(
        self,
        *,
        plan,
        task,
        declaration,
        operation,
        requests,
    ):
        result = await self.runtime._execute_child(
            plan, task, declaration, operation, requests
        )
        record = self.runtime.children.get(declaration["child_id"])
        self.assertTrue(result.terminal)
        self.assertEqual("succeeded_verified", result.outcome)
        self.assertTrue(record.terminal)
        self.assertEqual("succeeded_verified", record.normalized_outcome)
        self.assertEqual(1, record.dispatch_count)
        return record

    def _terminalize_post_intent_child(
        self,
        declaration,
        *,
        diagnostic_code="rr14_terminal_verification_evidence",
    ):
        record = self.runtime.children.get(declaration["child_id"])
        identity = record.execution_identity()
        terminal = self.runtime.children.record_verification(
            declaration["child_id"],
            owner_id=identity.owner_id,
            claim_generation=record.claim_generation,
            outcome="manual_review_required",
            terminal=True,
            diagnostic_codes=(diagnostic_code,),
            now=self.service.now(),
        )
        self.assertTrue(terminal.terminal)
        self.assertEqual("manual_review_required", terminal.normalized_outcome)
        self.assertEqual(1, terminal.dispatch_count)
        return terminal

    def _succeed_post_intent_child(
        self,
        declaration,
        *,
        diagnostic_code="rr15_terminal_success_evidence",
    ):
        record = self.runtime.children.get(declaration["child_id"])
        identity = record.execution_identity()
        terminal = self.runtime.children.record_verification(
            declaration["child_id"],
            owner_id=identity.owner_id,
            claim_generation=record.claim_generation,
            outcome="succeeded_verified",
            terminal=True,
            diagnostic_codes=(diagnostic_code,),
            now=self.service.now(),
        )
        self.assertTrue(terminal.terminal)
        self.assertEqual("succeeded_verified", terminal.normalized_outcome)
        self.assertEqual(1, terminal.dispatch_count)
        return terminal

    def _succeed_no_dispatch_child(
        self,
        declaration,
        *,
        diagnostic_code="rr16_preflight_noop_verified",
    ):
        self._claim_child(declaration)
        self._hold_child_lock(declaration)
        identity, claim, _timing = self._claims[declaration["child_id"]]
        terminal = self.runtime.children.terminalize_verified_no_dispatch(
            declaration["child_id"],
            owner_id=identity.owner_id,
            claim_generation=claim.claim_generation,
            resulting_state_fingerprint=hashlib.sha256(
                f"{declaration['child_id']}:state".encode()
            ).hexdigest(),
            evidence_hash=hashlib.sha256(
                f"{declaration['child_id']}:evidence".encode()
            ).hexdigest(),
            diagnostic_codes=(diagnostic_code,),
            now=self.service.now(),
        )
        self._release_fixture_locks(declaration["child_id"])
        self.assertTrue(terminal.terminal)
        self.assertEqual("succeeded_verified", terminal.normalized_outcome)
        self.assertIsNone(terminal.dispatch_intent)
        self.assertEqual(0, terminal.dispatch_count)
        return terminal

    def _fail_no_dispatch_child(
        self,
        declaration,
        *,
        diagnostic_code="rr16_failed_pre_dispatch",
    ):
        self._claim_child(declaration)
        self._hold_child_lock(declaration)
        identity, claim, _timing = self._claims[declaration["child_id"]]
        terminal = self.runtime.children.terminalize_pre_dispatch(
            declaration["child_id"],
            owner_id=identity.owner_id,
            claim_generation=claim.claim_generation,
            outcome="failed_pre_dispatch",
            diagnostic_codes=(diagnostic_code,),
            now=self.service.now(),
        )
        self._release_fixture_locks(declaration["child_id"])
        self.assertTrue(terminal.terminal)
        self.assertIsNone(terminal.dispatch_intent)
        self.assertEqual(0, terminal.dispatch_count)
        return terminal

    def _succeed_manual_post_intent_child(self, declaration):
        self._claim_child(declaration)
        self._hold_child_lock(declaration)
        identity, claim, timing = self._claims[declaration["child_id"]]
        self.runtime.children.record_preflight(
            declaration["child_id"],
            owner_id=identity.owner_id,
            claim_generation=claim.claim_generation,
            now=self.service.now(),
        )
        self.runtime.children.commit_dispatch_intent(
            declaration["child_id"],
            owner_id=identity.owner_id,
            claim_generation=claim.claim_generation,
            request_id=declaration["request_id"],
            provider_operation=declaration["operation_id"],
            provider_arguments_hash=hashlib.sha256(
                f"{declaration['child_id']}:arguments".encode()
            ).hexdigest(),
            timing=timing,
            now=self.service.now(),
        )
        terminal = self.runtime.children.record_verification(
            declaration["child_id"],
            owner_id=identity.owner_id,
            claim_generation=claim.claim_generation,
            outcome="succeeded_verified",
            terminal=True,
            diagnostic_codes=("rr16_post_intent_verified",),
            now=self.service.now(),
        )
        self._release_fixture_locks(declaration["child_id"])
        self.assertEqual(1, terminal.dispatch_count)
        return terminal

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

    def _populate_legacy_nonterminal_prefix(self, *, task_count: int):
        """Populate a mixed-index prefix without creating F3 authority."""

        timestamp = (self.service.now() + timedelta(days=1)).isoformat()
        repository = self.service.task_repository
        created_ids = []
        for task_index in range(task_count):
            prefix = f"rr11-legacy-{task_index}"
            task = new_execution_task(
                task_id=hashlib.sha256(
                    f"{prefix}-task".encode()
                ).hexdigest()[:32],
                plan_id=hashlib.sha256(
                    f"{prefix}-plan".encode()
                ).hexdigest()[:32],
                plan_hash=hashlib.sha256(
                    f"{prefix}-plan-hash".encode()
                ).hexdigest(),
                operation="configuration_plan",
                target={
                    "target_type": "automation",
                    "target_id": f"legacy_{task_index}",
                },
                timestamp=timestamp,
                execution_request_id=f"legacy-request-{task_index}",
                idempotency_key=hashlib.sha256(
                    f"{prefix}-idempotency".encode()
                ).hexdigest(),
                approval_reference={},
                legacy_projection={
                    "execution_authority": "legacy_pre_f3"
                },
            )
            repository._path(
                task.task_id, plan_id=task.plan_id
            ).write_text(
                json.dumps(
                    task.to_dict(), sort_keys=True, separators=(",", ":")
                ),
                encoding="utf-8",
            )
            created_ids.append(task.task_id)
        repository.rebuild_navigation_index()
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

    async def test_historical_policy_post_intent_recovery_is_readback_only(self):
        task, declaration = await self._post_intent_active_child(
            "beta54_historical_post_intent"
        )
        record = self.runtime.children.get(declaration["child_id"])
        assert record is not None and record.dispatch_intent is not None
        historical = copy.deepcopy(
            self.service._load(declaration["plan_id"])
        )
        assert historical.policy_decision is not None
        historical.policy_decision = replace(
            historical.policy_decision,
            policy_version="f2-v1",
        )
        prepared = (
            self.runtime._prepared_cache[declaration["child_id"]],
        )
        requests = self.runtime._sequence_lock_cache[task.task_id]
        writes_before = sum(
            call[0] == "write" for call in self.gateway.calls
        )

        with patch.object(
            self.service,
            "_load_for_projection",
            return_value=historical,
        ), patch.object(
            self.runtime,
            "_load_prepared",
            return_value=(prepared, requests),
        ):
            result = await self.runtime._recover_active_candidates(
                ((declaration, record),),
                now=self.service.now(),
                sweep_started=0.0,
                transition_limit=1,
                recovery_mode="post_intent",
                monotonic=lambda: 0.0,
            )

        recovered = self.runtime.children.get(declaration["child_id"])
        assert recovered is not None
        self.assertEqual(1, result["processed"])
        self.assertEqual(1, recovered.dispatch_count)
        self.assertEqual(
            writes_before,
            sum(call[0] == "write" for call in self.gateway.calls),
        )

    async def test_historical_policy_pre_intent_recovery_is_refused(self):
        plan, task, declarations, _prepared, _requests = (
            await self._initialized_hvac_sequence()
        )
        declaration = declarations[0]
        historical = copy.deepcopy(plan)
        assert historical.policy_decision is not None
        historical.policy_decision = replace(
            historical.policy_decision,
            policy_version="f2-v1",
        )
        writes_before = sum(
            call[0] == "write" for call in self.gateway.calls
        )

        with patch.object(
            self.service,
            "_load_for_projection",
            return_value=historical,
        ), patch.object(
            self.runtime,
            "_load_prepared",
            side_effect=AssertionError(
                "historical pre-intent work must not be prepared"
            ),
        ):
            result = await self.runtime._recover_active_candidates(
                ((declaration, None),),
                now=self.service.now(),
                sweep_started=0.0,
                transition_limit=1,
                recovery_mode="pre_intent",
                monotonic=lambda: 0.0,
            )

        self.assertEqual(0, result["processed"])
        self.assertEqual(
            (declaration["child_id"],), result["retry_child_ids"]
        )
        self.assertEqual(
            writes_before,
            sum(call[0] == "write" for call in self.gateway.calls),
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
        original_write_batch = self.service.audit.write_batch

        def append_then_crash(entries):
            entries = tuple(entries)
            written = original_write_batch(entries)
            if any(
                entry.get("event") == "f3_execution_cancelled"
                for entry in entries
            ):
                raise SystemExit("simulated process loss after audit append")
            return written

        with patch.object(
            self.service.audit,
            "write_batch",
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
        original_write_batch = self.service.audit.write_batch

        def append_then_crash(entries):
            entries = tuple(entries)
            written = original_write_batch(entries)
            if any(
                entry.get("event") == "f3_execution_cancelled"
                for entry in entries
            ):
                raise SystemExit("simulated process loss after audit append")
            return written

        with patch.object(
            self.service.audit,
            "write_batch",
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
        original_write_batch = self.service.audit.write_batch

        def append_then_crash(entries):
            entries = tuple(entries)
            written = original_write_batch(entries)
            if any(
                entry.get("event") == "f3_execution_started"
                for entry in entries
            ):
                raise SystemExit("simulated persisted-event append crash")
            return written

        with patch.object(
            self.service.audit,
            "write_batch",
            side_effect=append_then_crash,
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
        with patch.object(
            self.service.audit, "write_batch", return_value=0
        ):
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

    async def test_persisted_audit_replay_scans_retained_logs_once_per_batch(
        self,
    ):
        _task, declaration = await self._post_intent_active_child(
            "bounded_audit_replay_scan"
        )
        child_id = declaration["child_id"]
        record = self.runtime.children.get(child_id)
        self.assertGreater(len(record.events), 1)
        self.runtime.children.update_runtime(
            child_id, changes={"audited_event_count": 0}
        )
        self.audit_path.write_text("", encoding="utf-8")

        with patch.object(
            self.service.audit,
            "_idempotency_keys",
            wraps=self.service.audit._idempotency_keys,
        ) as retained_scan:
            self.assertTrue(
                self.runtime._audit_record_events(declaration, record)
            )
            self.assertTrue(
                self.runtime._audit_record_events(declaration, record)
            )

        self.assertEqual(1, retained_scan.call_count)
        self.assertEqual(
            len(record.events),
            self.runtime.children.runtime(child_id)["audited_event_count"],
        )

    async def test_partial_audit_batch_persists_prefix_and_retries_suffix_once(
        self,
    ):
        _task, declaration = await self._post_intent_active_child(
            "partial_prefix_audit_replay"
        )
        child_id = declaration["child_id"]
        record = self.runtime.children.get(child_id)
        self.assertGreater(len(record.events), 1)
        self.runtime.children.update_runtime(
            child_id, changes={"audited_event_count": 0}
        )

        rotated_path = Path(f"{self.audit_path}.1")
        self.audit_path.unlink(missing_ok=True)
        rotated_path.unlink(missing_ok=True)
        rotated_identity = hashlib.sha256(
            b"retained-audit-predecessor-fixture"
        ).hexdigest()
        current_identity = hashlib.sha256(
            b"current-audit-fixture"
        ).hexdigest()
        retained_audit = AuditLogger(
            str(rotated_path), "dev14-test-access-secret"
        )
        self.assertTrue(
            retained_audit.write(
                {
                    "event": "retained_audit_fixture",
                    "audit_event_id": rotated_identity,
                }
            )
        )
        self.assertTrue(
            self.service.audit.write(
                {
                    "event": "current_audit_fixture",
                    "audit_event_id": current_identity,
                }
            )
        )
        self.assertTrue(self.audit_path.exists())
        self.assertTrue(rotated_path.exists())

        expected_ids = tuple(
            _persisted_audit_event_id(child_id, event)
            for event in record.events
        )

        def retained_entries():
            entries = []
            for path in (self.audit_path, rotated_path):
                entries.extend(
                    json.loads(line)
                    for line in path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if line.strip()
                )
            return entries

        original_batch = self.service.audit.write_batch
        original_append = self.service.audit._write_safe_unlocked
        batch_results = []
        durable_appends = 0

        def observe_batch(entries):
            result = original_batch(entries)
            batch_results.append(result)
            return result

        def append_prefix_then_fail(entry):
            nonlocal durable_appends
            if durable_appends >= 1:
                raise OSError("synthetic failure after durable audit prefix")
            original_append(entry)
            durable_appends += 1

        with (
            patch.object(
                self.service.audit,
                "_idempotency_keys",
                wraps=self.service.audit._idempotency_keys,
            ) as retained_scan,
            patch.object(
                self.service.audit,
                "write_batch",
                side_effect=observe_batch,
            ),
            patch.object(
                self.service.audit,
                "_write_safe_unlocked",
                side_effect=append_prefix_then_fail,
            ),
        ):
            self.assertFalse(
                self.runtime._audit_record_events(declaration, record)
            )

        self.assertEqual([1], batch_results)
        self.assertEqual(1, retained_scan.call_count)
        self.assertEqual(
            1,
            self.runtime.children.runtime(child_id)["audited_event_count"],
        )
        first_attempt_entries = retained_entries()
        first_attempt_ids = [
            item.get("audit_event_id") for item in first_attempt_entries
        ]
        self.assertEqual(1, first_attempt_ids.count(expected_ids[0]))
        for identity in expected_ids[1:]:
            self.assertEqual(0, first_attempt_ids.count(identity))

        retry_results = []

        def observe_retry(entries):
            result = original_batch(entries)
            retry_results.append(result)
            return result

        with (
            patch.object(
                self.service.audit,
                "_idempotency_keys",
                wraps=self.service.audit._idempotency_keys,
            ) as retry_scan,
            patch.object(
                self.service.audit,
                "write_batch",
                side_effect=observe_retry,
            ),
        ):
            self.assertTrue(
                self.runtime._audit_record_events(declaration, record)
            )

        self.assertEqual([len(record.events) - 1], retry_results)
        self.assertEqual(1, retry_scan.call_count)
        self.assertEqual(
            len(record.events),
            self.runtime.children.runtime(child_id)["audited_event_count"],
        )
        after_retry_entries = retained_entries()
        after_retry_ids = [
            item.get("audit_event_id") for item in after_retry_entries
        ]
        self.assertEqual(
            len(record.events) - 1,
            len(after_retry_entries) - len(first_attempt_entries),
        )
        for identity in expected_ids:
            self.assertEqual(1, after_retry_ids.count(identity))

        restarted = self._restart_runtime()
        restarted.children.update_runtime(
            child_id, changes={"audited_event_count": 0}
        )
        restarted_record = restarted.children.get(child_id)
        restart_results = []

        def observe_restart(entries):
            result = original_batch(entries)
            restart_results.append(result)
            return result

        before_restart_replay = retained_entries()
        with (
            patch.object(
                self.service.audit,
                "_idempotency_keys",
                wraps=self.service.audit._idempotency_keys,
            ) as restart_scan,
            patch.object(
                self.service.audit,
                "write_batch",
                side_effect=observe_restart,
            ),
        ):
            self.assertTrue(
                restarted._audit_record_events(declaration, restarted_record)
            )

        self.assertEqual([len(record.events)], restart_results)
        self.assertEqual(1, restart_scan.call_count)
        self.assertEqual(
            len(record.events),
            restarted.children.runtime(child_id)["audited_event_count"],
        )
        self.assertEqual(before_restart_replay, retained_entries())
        final_ids = [
            item["audit_event_id"]
            for item in retained_entries()
            if "audit_event_id" in item
        ]
        self.assertEqual(len(final_ids), len(set(final_ids)))
        self.assertEqual(
            {rotated_identity, current_identity, *expected_ids},
            set(final_ids),
        )

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

    async def test_removed_active_cursor_target_does_not_hide_f3_work(self):
        _task, declaration = await self._post_intent_active_child(
            "removed_active_cursor_target"
        )
        missing_cursor = self.runtime.children.active_recovery_cursor_for_task(
            hashlib.sha256(b"removed-active-cursor").hexdigest()[:32]
        )
        self.runtime.children.advance_active_recovery_cursor(
            expected=None, next_cursor=missing_cursor
        )
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )

        result = await self.runtime.recover_once("periodic")

        record = self.runtime.children.get(declaration["child_id"])
        self.assertEqual(1, result["active_recovery_transitions"])
        self.assertEqual(1, record.dispatch_count)
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_mixed_nonterminal_prefix_cannot_hide_f3_authority(self):
        task, declaration = await self._post_intent_active_child(
            "rr11_filtered_authority"
        )
        self._populate_legacy_nonterminal_prefix(task_count=1_024)
        repository = self.service.task_repository

        self.assertNotIn(
            task.task_id, repository.nonterminal_task_ids()[:1_024]
        )
        self.assertEqual(
            (task.task_id,),
            repository.f3_nonterminal_task_ids(limit=1_024),
        )

        # Rebuild exactly as a process restart would, then recover through the
        # real authority-filtered navigation path.
        self.service.task_repository = type(repository)(
            repository.governance_root,
            retention_days=repository.retention_days,
        )
        self._restart_runtime()
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )

        result = await self.runtime.recover_once("startup")

        record = self.runtime.children.get(declaration["child_id"])
        self.assertEqual(1, result["active_recovery_transitions"])
        self.assertEqual(1, result["active_tasks_examined"])
        self.assertEqual(1, record.dispatch_count)
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_post_intent_batch_precedes_historical_time_exhaustion(self):
        declarations = []
        for index in range(17):
            _task, declaration = await self._post_intent_active_child(
                f"rr12_post_intent_{index:02d}"
            )
            declarations.append(declaration)
            self._release_fixture_locks(declaration["child_id"])
        self._populate_terminal_history(task_count=1)
        elapsed = {"value": False}
        page_calls = {"count": 0}
        original_page = self.runtime.children.recovery_declaration_page

        def consume_historical_budget(**kwargs):
            page_calls["count"] += 1
            page = original_page(**kwargs)
            elapsed["value"] = True
            return page

        self.runtime._recovery_monotonic = (
            lambda: 6.0 if elapsed["value"] else 0.0
        )
        with patch.object(
            self.runtime.children,
            "recovery_declaration_page",
            side_effect=consume_historical_budget,
        ):
            first = await self.runtime.recover_once("periodic")

        self.assertEqual(16, first["active_recovery_transitions"])
        self.assertEqual(16, first["recovery_transitions"])
        self.assertEqual(0, page_calls["count"])
        self.assertEqual(
            0, sum(item[0] == "write" for item in self.gateway.calls)
        )
        self.assertTrue(
            all(
                self.runtime.children.get(item["child_id"]).dispatch_count
                == 1
                for item in declarations
            )
        )

        elapsed["value"] = False
        self.runtime._recovery_monotonic = lambda: 0.0
        second = await self.runtime.recover_once("periodic")

        self.assertEqual(1, second["active_recovery_transitions"])
        self.assertGreater(second["manifest_reads"], 0)
        self.assertEqual(
            0, sum(item[0] == "write" for item in self.gateway.calls)
        )

    async def test_historical_fairness_follows_fifteen_post_intent_items(self):
        post_intent = []
        for index in range(15):
            _task, declaration = await self._post_intent_active_child(
                f"rr12_fair_post_{index:02d}"
            )
            post_intent.append(declaration)
            self._release_fixture_locks(declaration["child_id"])
        _task, pre_intent = await self._preintent_active_child(
            "rr12_fair_pre_intent"
        )
        self._populate_terminal_history(task_count=1)
        order = []
        original_execute = self.runtime._execute_child
        original_reconcile = self.runtime._reconcile_orphaned_children

        async def record_active(
            plan, task, declaration, operation, requests
        ):
            order.append(declaration["child_id"])
            return await original_execute(
                plan, task, declaration, operation, requests
            )

        def record_history(**kwargs):
            order.append("historical_scan")
            return original_reconcile(**kwargs)

        self.runtime._recovery_monotonic = lambda: 0.0
        with (
            patch.object(
                self.runtime, "_execute_child", side_effect=record_active
            ),
            patch.object(
                self.runtime,
                "_reconcile_orphaned_children",
                side_effect=record_history,
            ),
        ):
            result = await self.runtime.recover_once("periodic")

        post_ids = {item["child_id"] for item in post_intent}
        self.assertEqual(post_ids, set(order[:15]))
        self.assertEqual("historical_scan", order[15])
        self.assertEqual(pre_intent["child_id"], order[16])
        self.assertEqual(16, result["active_recovery_transitions"])
        self.assertEqual(
            [("write", "create", "automation", "rr12_fair_pre_intent")],
            [item for item in self.gateway.calls if item[0] == "write"],
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

        self.runtime._recovery_monotonic = lambda: 0.0
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

    async def test_post_intent_discovery_budget_exhaustion_checkpoints_progress(
        self,
    ):
        _task, target = await self._post_intent_active_child(
            "rr13_discovered_post_intent"
        )
        _task, backed_off = await self._preintent_active_child(
            "rr13_newer_backed_off"
        )
        self.runtime.children.update_runtime(
            backed_off["child_id"],
            changes={
                "backoff_seconds": 30,
                "next_eligible_at": (
                    self.service.now() + timedelta(seconds=30)
                ).isoformat(),
            },
        )
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )
        ticks = iter((0.0, 0.0, 0.0, 0.0, 6.0))
        self.runtime._recovery_monotonic = lambda: next(ticks, 6.0)

        first = await self.runtime.recover_once("periodic")

        self.assertEqual(0, first["active_recovery_transitions"])
        checkpoint_reader = getattr(
            self.runtime.children, "active_recovery_checkpoint", None
        )
        checkpoint = (
            None if checkpoint_reader is None else checkpoint_reader()
        )
        self.assertFalse(
            self.runtime.children.get(target["child_id"]).terminal
        )

        ticks = iter((0.0, 0.0, 0.0, 0.0, 6.0))
        self.runtime._recovery_monotonic = lambda: next(ticks, 6.0)
        second = await self.runtime.recover_once("periodic")

        record = self.runtime.children.get(target["child_id"])
        self.assertEqual(1, second["active_recovery_transitions"])
        self.assertEqual(
            [target["child_id"]],
            [item["child_id"] for item in checkpoint["candidates"]],
        )
        self.assertTrue(
            record.terminal
            or any(
                item["event_type"] == "recovery_claimed"
                for item in record.events
            )
        )
        self.assertEqual(1, record.dispatch_count)
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_budget_exhausted_before_discovery_retries_next_sweep(self):
        _task, target = await self._post_intent_active_child(
            "rr13_before_discovery_budget"
        )
        before_cursor = self.runtime.children.active_recovery_cursor()
        ticks = iter((0.0, 0.0, 6.0))
        self.runtime._recovery_monotonic = lambda: next(ticks, 6.0)

        first = await self.runtime.recover_once("periodic")

        self.assertEqual(0, first["active_tasks_examined"])
        self.assertEqual(0, first["active_recovery_transitions"])
        self.assertEqual(
            before_cursor, self.runtime.children.active_recovery_cursor()
        )
        self.assertIsNone(
            self.runtime.children.active_recovery_checkpoint()
        )

        self.runtime._recovery_monotonic = lambda: 0.0
        second = await self.runtime.recover_once("periodic")

        self.assertEqual(1, second["active_recovery_transitions"])
        self.assertEqual(
            1, self.runtime.children.get(target["child_id"]).dispatch_count
        )
        self.assertEqual(
            0, sum(item[0] == "write" for item in self.gateway.calls)
        )

    async def test_discovered_checkpoint_survives_restart_before_recovery(self):
        _task, target = await self._post_intent_active_child(
            "rr13_restart_checkpoint"
        )
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )
        ticks = iter((0.0, 0.0, 0.0, 6.0))
        self.runtime._recovery_monotonic = lambda: next(ticks, 6.0)

        first = await self.runtime.recover_once("periodic")
        checkpoint = self.runtime.children.active_recovery_checkpoint()
        self.assertEqual(0, first["active_recovery_transitions"])
        self.assertEqual(target["child_id"], checkpoint["candidates"][0]["child_id"])

        self._restart_runtime()
        self.runtime._recovery_monotonic = lambda: 0.0
        self.assertEqual(
            checkpoint, self.runtime.children.active_recovery_checkpoint()
        )
        second = await self.runtime.recover_once("startup")

        record = self.runtime.children.get(target["child_id"])
        self.assertEqual(1, second["active_recovery_transitions"])
        self.assertEqual(1, record.dispatch_count)
        self.assertIsNone(
            self.runtime.children.active_recovery_checkpoint()
        )
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_checkpoint_crash_boundaries_remain_replay_safe(self):
        _task, before_persist = await self._post_intent_active_child(
            "rr13_crash_before_checkpoint"
        )
        original_replace = (
            self.runtime.children.replace_active_recovery_checkpoint
        )

        def crash_before_checkpoint(*, expected, next_checkpoint):
            if next_checkpoint is not None:
                raise SystemExit("simulated crash before checkpoint")
            return original_replace(
                expected=expected, next_checkpoint=next_checkpoint
            )

        with patch.object(
            self.runtime.children,
            "replace_active_recovery_checkpoint",
            side_effect=crash_before_checkpoint,
        ):
            with self.assertRaises(SystemExit):
                await self.runtime.recover_once("periodic")
        self.assertIsNone(
            self.runtime.children.active_recovery_checkpoint()
        )

        self._restart_runtime()
        recovered = await self.runtime.recover_once("startup")
        self.assertEqual(1, recovered["active_recovery_transitions"])
        self.assertEqual(
            1,
            self.runtime.children.get(before_persist["child_id"]).dispatch_count,
        )

    async def test_crash_after_checkpoint_persistence_resumes_without_dispatch(
        self,
    ):
        _task, after_persist = await self._post_intent_active_child(
            "rr13_crash_after_checkpoint"
        )
        original_replace = (
            self.runtime.children.replace_active_recovery_checkpoint
        )

        def crash_after_checkpoint(*, expected, next_checkpoint):
            result = original_replace(
                expected=expected, next_checkpoint=next_checkpoint
            )
            if next_checkpoint is not None:
                raise SystemExit("simulated crash after checkpoint")
            return result

        with patch.object(
            self.runtime.children,
            "replace_active_recovery_checkpoint",
            side_effect=crash_after_checkpoint,
        ):
            with self.assertRaises(SystemExit):
                await self.runtime.recover_once("periodic")
        self.assertEqual(
            after_persist["child_id"],
            self.runtime.children.active_recovery_checkpoint()["candidates"][0][
                "child_id"
            ],
        )

        self._restart_runtime()
        resumed = await self.runtime.recover_once("startup")
        record = self.runtime.children.get(after_persist["child_id"])
        self.assertEqual(1, resumed["active_recovery_transitions"])
        self.assertEqual(1, record.dispatch_count)
        self.assertEqual(
            0, sum(item[0] == "write" for item in self.gateway.calls)
        )

    async def test_post_intent_transition_crash_before_cursor_is_replay_safe(self):
        _task, target = await self._post_intent_active_child(
            "rr13_transition_before_cursor"
        )
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )

        with patch.object(
            self.runtime.children,
            "advance_active_recovery_cursor",
            side_effect=SystemExit("simulated crash before cursor advance"),
        ):
            with self.assertRaises(SystemExit):
                await self.runtime.recover_once("periodic")

        self._restart_runtime()
        await self.runtime.recover_once("startup")
        record = self.runtime.children.get(target["child_id"])
        self.assertEqual(1, record.dispatch_count)
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_checkpoint_compare_and_swap_conflict_loses_no_candidate(self):
        _task, target = await self._post_intent_active_child(
            "rr13_checkpoint_cas"
        )
        original_replace = (
            self.runtime.children.replace_active_recovery_checkpoint
        )
        conflict = {"raised": False}

        def conflict_once(*, expected, next_checkpoint):
            if next_checkpoint is not None and not conflict["raised"]:
                conflict["raised"] = True
                raise ExecutionStorageError(
                    "synthetic checkpoint compare-and-swap conflict"
                )
            return original_replace(
                expected=expected, next_checkpoint=next_checkpoint
            )

        with patch.object(
            self.runtime.children,
            "replace_active_recovery_checkpoint",
            side_effect=conflict_once,
        ):
            with self.assertRaises(ExecutionStorageError):
                await self.runtime.recover_once("periodic")
        self.assertIsNone(
            self.runtime.children.active_recovery_checkpoint()
        )

        result = await self.runtime.recover_once("periodic")

        self.assertEqual(1, result["active_recovery_transitions"])
        self.assertEqual(
            1, self.runtime.children.get(target["child_id"]).dispatch_count
        )
        self.assertEqual(
            0, sum(item[0] == "write" for item in self.gateway.calls)
        )

    async def test_dense_ineligible_prefix_checkpoints_monotonic_progress(self):
        _task, target = await self._post_intent_active_child(
            "rr13_dense_prefix_target"
        )
        for index in range(5):
            _task, backed_off = await self._preintent_active_child(
                f"rr13_dense_prefix_{index}"
            )
            self.runtime.children.update_runtime(
                backed_off["child_id"],
                changes={
                    "backoff_seconds": 30,
                    "next_eligible_at": (
                        self.service.now() + timedelta(seconds=30)
                    ).isoformat(),
                },
            )
        ticks = iter((*([0.0] * 8), 6.0))
        self.runtime._recovery_monotonic = lambda: next(ticks, 6.0)

        first = await self.runtime.recover_once("periodic")

        self.assertEqual(0, first["active_recovery_transitions"])
        self.assertEqual(6, first["active_tasks_examined"])
        self.assertEqual(
            target["child_id"],
            self.runtime.children.active_recovery_checkpoint()["candidates"][0][
                "child_id"
            ],
        )
        self.assertIsNotNone(
            self.runtime.children.active_recovery_cursor()
        )

        self.runtime._recovery_monotonic = lambda: 0.0
        second = await self.runtime.recover_once("periodic")

        self.assertEqual(1, second["active_recovery_transitions"])
        self.assertEqual(
            1, self.runtime.children.get(target["child_id"]).dispatch_count
        )

    async def test_checkpoint_scan_persists_progress_before_time_exhaustion(self):
        _task, backed_off = await self._post_intent_active_child(
            "rr13_checkpoint_backed_off"
        )
        self._release_fixture_locks(backed_off["child_id"])
        _task, target = await self._post_intent_active_child(
            "rr13_checkpoint_after_prefix"
        )
        self._store_active_checkpoint(backed_off, target)
        self.runtime.children.update_runtime(
            backed_off["child_id"],
            changes={
                "backoff_seconds": 30,
                "next_eligible_at": (
                    self.service.now() + timedelta(seconds=30)
                ).isoformat(),
            },
        )
        ticks = iter((0.0, 0.0, 6.0))
        self.runtime._recovery_monotonic = lambda: next(ticks, 6.0)

        first = await self.runtime.recover_once("periodic")

        self.assertEqual(0, first["active_recovery_transitions"])
        self.assertEqual(
            [backed_off["child_id"], target["child_id"]],
            [
                item["child_id"]
                for item in self.runtime.children.active_recovery_checkpoint()[
                    "candidates"
                ]
            ],
        )

        self.runtime._recovery_monotonic = lambda: 0.0
        second = await self.runtime.recover_once("periodic")

        self.assertEqual(1, second["active_recovery_transitions"])
        self.assertEqual(
            1, self.runtime.children.get(target["child_id"]).dispatch_count
        )
        self.assertEqual(
            [backed_off["child_id"]],
            [
                item["child_id"]
                for item in self.runtime.children.active_recovery_checkpoint()[
                    "candidates"
                ]
            ],
        )
        self.assertEqual(
            0, sum(item[0] == "write" for item in self.gateway.calls)
        )

    async def test_checkpoint_boundary_preserves_post_intent_batch_order(self):
        declarations = []
        for index in range(17):
            _task, declaration = await self._post_intent_active_child(
                f"rr13_checkpoint_batch_{index:02d}"
            )
            declarations.append(declaration)
            self._release_fixture_locks(declaration["child_id"])
        expected = sorted(
            declarations,
            key=lambda item: (
                datetime.fromisoformat(
                    self.runtime.children.get(item["child_id"])
                    .dispatch_intent["evidence_deadline"]
                ),
                item["public_task_id"].encode("utf-8"),
                item["operation_ordinal"],
                item["child_id"].encode("utf-8"),
            ),
        )
        ticks = iter((*([0.0] * 19), 6.0))
        self.runtime._recovery_monotonic = lambda: next(ticks, 6.0)

        first = await self.runtime.recover_once("periodic")

        checkpoint = self.runtime.children.active_recovery_checkpoint()
        self.assertEqual(0, first["active_recovery_transitions"])
        self.assertEqual(
            [item["child_id"] for item in expected[:16]],
            [item["child_id"] for item in checkpoint["candidates"]],
        )

        order = []
        original_execute = self.runtime._execute_child

        async def record_order(plan, task, declaration, operation, requests):
            order.append(declaration["child_id"])
            return await original_execute(
                plan, task, declaration, operation, requests
            )

        self.runtime._recovery_monotonic = lambda: 0.0
        with patch.object(
            self.runtime, "_execute_child", side_effect=record_order
        ):
            second = await self.runtime.recover_once("periodic")
            third = await self.runtime.recover_once("periodic")

        self.assertEqual(16, second["active_recovery_transitions"])
        self.assertEqual(1, third["active_recovery_transitions"])
        self.assertEqual(
            [item["child_id"] for item in expected], order
        )
        self.assertTrue(
            all(
                self.runtime.children.get(item["child_id"]).dispatch_count
                == 1
                for item in declarations
            )
        )
        self.assertEqual(
            0, sum(item[0] == "write" for item in self.gateway.calls)
        )

    async def test_terminal_post_intent_checkpoint_projects_without_redispatch(
        self,
    ):
        task, declaration = await self._post_intent_active_child(
            "rr14_terminal_projection"
        )
        checkpoint = self._store_active_checkpoint(declaration)
        self._terminalize_post_intent_child(declaration)
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )

        with patch.object(
            self.runtime,
            "_execute_child",
            side_effect=AssertionError("terminal child must not execute"),
        ):
            result = await self.runtime.recover_once("periodic")

        public = self.service.get_execution_task(task.task_id)
        child = next(
            item
            for item in public["f3_children"]
            if item["child_execution_id"] == declaration["child_id"]
        )
        self.assertEqual(1, result["active_recovery_transitions"])
        self.assertEqual("manual_review_required", public["state"])
        self.assertEqual("manual_review_required", public["terminal_outcome"])
        self.assertEqual("terminal", child["state"])
        self.assertEqual("manual_review_required", child["normalized_outcome"])
        self.assertEqual(1, child["dispatch_count"])
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )
        self.assertIsNone(self.runtime.children.active_recovery_checkpoint())
        self.assertIn(
            "rr14_terminal_verification_evidence",
            {
                item.get("evidence_references", {}).get("reason_code")
                for item in self._audit_entries()
            },
        )

        projected_event_count = public["event_count"]
        projected_audit_ids = [
            item["audit_event_id"]
            for item in self._audit_entries()
            if "audit_event_id" in item
        ]
        self._store_active_checkpoint(declaration)
        repeated = await self.runtime.recover_once("periodic")
        self.assertEqual(0, repeated["active_recovery_transitions"])
        self.assertIsNone(self.runtime.children.active_recovery_checkpoint())
        self.assertEqual(
            projected_event_count,
            self.service.get_execution_task(task.task_id)["event_count"],
        )
        self.assertEqual(
            projected_audit_ids,
            [
                item["audit_event_id"]
                for item in self._audit_entries()
                if "audit_event_id" in item
            ],
        )
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )
        self._restart_runtime()
        restarted = await self.runtime.recover_once("startup")
        self.assertEqual(0, restarted["active_recovery_transitions"])
        self.assertEqual(
            projected_event_count,
            self.service.get_execution_task(task.task_id)["event_count"],
        )
        self.assertEqual(
            projected_audit_ids,
            [
                item["audit_event_id"]
                for item in self._audit_entries()
                if "audit_event_id" in item
            ],
        )
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )
        self.assertNotEqual(
            checkpoint, self.runtime.children.active_recovery_checkpoint()
        )

    async def test_successful_post_intent_child_without_checkpoint_projects(self):
        task, declaration = await self._post_intent_active_child(
            "rr15_success_without_checkpoint"
        )
        terminal = self._succeed_post_intent_child(declaration)
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )
        self.assertIsNone(self.runtime.children.active_recovery_checkpoint())

        original_project = self.runtime._project

        def project_after_checkpoint(plan, public_task):
            checkpoint = self.runtime.children.active_recovery_checkpoint()
            self.assertEqual(
                [declaration["child_id"]],
                [item["child_id"] for item in checkpoint["candidates"]],
            )
            return original_project(plan, public_task)

        with (
            patch.object(
                self.runtime,
                "_load_prepared",
                side_effect=AssertionError(
                    "terminal child must not prepare a provider operation"
                ),
            ),
            patch.object(
                self.runtime,
                "_execute_child",
                side_effect=AssertionError("terminal child must not execute"),
            ),
            patch.object(
                self.runtime,
                "_project",
                side_effect=project_after_checkpoint,
            ),
        ):
            result = await self.runtime.recover_once("periodic")

        public = self.service.get_execution_task(task.task_id)
        child = next(
            item
            for item in public["f3_children"]
            if item["child_execution_id"] == declaration["child_id"]
        )
        self.assertEqual(1, result["active_recovery_transitions"])
        self.assertEqual("succeeded_verified", public["state"])
        self.assertEqual("succeeded_verified", public["terminal_outcome"])
        self.assertEqual("terminal", child["state"])
        self.assertEqual("succeeded_verified", child["normalized_outcome"])
        self.assertEqual(1, child["dispatch_count"])
        self.assertEqual(
            terminal.events,
            self.runtime.children.get(declaration["child_id"]).events,
        )
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )
        self.assertIsNone(self.runtime.children.active_recovery_checkpoint())

    async def test_successful_child_is_discovered_after_restart_without_checkpoint(
        self,
    ):
        task, declaration = await self._post_intent_active_child(
            "rr15_success_restart_discovery"
        )
        self._succeed_post_intent_child(
            declaration,
            diagnostic_code="rr15_success_restart_evidence",
        )
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )
        self.assertIsNone(self.runtime.children.active_recovery_checkpoint())
        self._restart_runtime()

        with patch.object(
            self.runtime,
            "_execute_child",
            side_effect=AssertionError("terminal child must not execute"),
        ):
            result = await self.runtime.recover_once("startup")

        public = self.service.get_execution_task(task.task_id)
        self.assertEqual(1, result["active_recovery_transitions"])
        self.assertEqual("succeeded_verified", public["state"])
        self.assertEqual("succeeded_verified", public["terminal_outcome"])
        self.assertEqual(
            1,
            self.runtime.children.get(declaration["child_id"]).dispatch_count,
        )
        self.assertIsNone(self.runtime.children.active_recovery_checkpoint())
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_checkpointed_success_projection_is_restart_idempotent(self):
        task, declaration = await self._post_intent_active_child(
            "rr15_checkpointed_success"
        )
        self._store_active_checkpoint(declaration)
        self._succeed_post_intent_child(
            declaration,
            diagnostic_code="rr15_checkpointed_success_evidence",
        )
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )

        with patch.object(
            self.runtime,
            "_execute_child",
            side_effect=AssertionError("terminal child must not execute"),
        ):
            first = await self.runtime.recover_once("periodic")

        public = self.service.get_execution_task(task.task_id)
        event_count = public["event_count"]
        audit_ids = [
            item["audit_event_id"]
            for item in self._audit_entries()
            if "audit_event_id" in item
        ]
        self.assertEqual(1, first["active_recovery_transitions"])
        self.assertEqual("succeeded_verified", public["state"])
        self.assertIsNone(self.runtime.children.active_recovery_checkpoint())

        repeated = await self.runtime.recover_once("periodic")
        self._restart_runtime()
        restarted = await self.runtime.recover_once("startup")

        self.assertEqual(0, repeated["active_recovery_transitions"])
        self.assertEqual(0, restarted["active_recovery_transitions"])
        self.assertEqual(
            event_count,
            self.service.get_execution_task(task.task_id)["event_count"],
        )
        self.assertEqual(
            audit_ids,
            [
                item["audit_event_id"]
                for item in self._audit_entries()
                if "audit_event_id" in item
            ],
        )
        self.assertEqual(
            1,
            self.runtime.children.get(declaration["child_id"]).dispatch_count,
        )
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_success_projection_failure_remains_checkpointed_for_retry(self):
        task, declaration = await self._post_intent_active_child(
            "rr15_success_projection_retry"
        )
        self._succeed_post_intent_child(
            declaration,
            diagnostic_code="rr15_success_projection_retry_evidence",
        )
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )

        with patch.object(
            self.runtime,
            "_project",
            side_effect=RuntimeError("synthetic successful projection failure"),
        ):
            failed = await self.runtime.recover_once("periodic")

        retained = self.runtime.children.active_recovery_checkpoint()
        retry_at = datetime.fromisoformat(
            self.runtime.children.runtime(declaration["child_id"])[
                "next_eligible_at"
            ]
        )
        self.assertEqual(1, failed["active_recovery_transitions"])
        self.assertEqual(
            [declaration["child_id"]],
            [item["child_id"] for item in retained["candidates"]],
        )
        self.assertEqual("preflight", self.service.get_execution_task(
            task.task_id
        )["state"])

        self.service.now = lambda: retry_at
        recovered = await self.runtime.recover_once("periodic")

        self.assertEqual(1, recovered["active_recovery_transitions"])
        self.assertEqual(
            "succeeded_verified",
            self.service.get_execution_task(task.task_id)["state"],
        )
        self.assertIsNone(self.runtime.children.active_recovery_checkpoint())
        self.assertEqual(
            1,
            self.runtime.children.get(declaration["child_id"]).dispatch_count,
        )
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )
        audit_ids = [
            item["audit_event_id"]
            for item in self._audit_entries()
            if "audit_event_id" in item
        ]
        repeated = await self.runtime.recover_once("periodic")
        self.assertEqual(0, repeated["active_recovery_transitions"])
        self.assertEqual(
            audit_ids,
            [
                item["audit_event_id"]
                for item in self._audit_entries()
                if "audit_event_id" in item
            ],
        )

    async def test_verified_no_dispatch_child_projects_from_discovery(self):
        task, declaration = await self._preintent_active_child(
            "rr16_no_dispatch_projection"
        )
        terminal = self._succeed_no_dispatch_child(declaration)
        before_writes = sum(item[0] == "write" for item in self.gateway.calls)
        original_project = self.runtime._project

        def project_after_checkpoint(plan, public_task):
            checkpoint = self.runtime.children.active_recovery_checkpoint()
            self.assertEqual(
                [declaration["child_id"]],
                [item["child_id"] for item in checkpoint["candidates"]],
            )
            return original_project(plan, public_task)

        with (
            patch.object(
                self.runtime,
                "_load_prepared",
                side_effect=AssertionError(
                    "terminal child must not prepare a provider operation"
                ),
            ),
            patch.object(
                self.runtime,
                "_execute_child",
                side_effect=AssertionError("terminal child must not execute"),
            ),
            patch.object(
                self.runtime,
                "_project",
                side_effect=project_after_checkpoint,
            ),
        ):
            result = await self.runtime.recover_once("periodic")

        public = self.service.get_execution_task(task.task_id)
        child = public["f3_children"][0]
        self.assertEqual(1, result["active_recovery_transitions"])
        self.assertEqual("succeeded_verified", public["state"])
        self.assertEqual("succeeded_verified", public["terminal_outcome"])
        self.assertEqual("succeeded_verified", child["normalized_outcome"])
        self.assertEqual(0, child["dispatch_count"])
        self.assertEqual(terminal.events, self.runtime.children.get(
            declaration["child_id"]
        ).events)
        self.assertIsNone(self.runtime.children.active_recovery_checkpoint())
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

        event_count = public["event_count"]
        audit_ids = [
            item["audit_event_id"]
            for item in self._audit_entries()
            if "audit_event_id" in item
        ]
        repeated = await self.runtime.recover_once("periodic")
        self._restart_runtime()
        restarted = await self.runtime.recover_once("startup")
        self.assertEqual(0, repeated["active_recovery_transitions"])
        self.assertEqual(0, restarted["active_recovery_transitions"])
        self.assertEqual(
            event_count,
            self.service.get_execution_task(task.task_id)["event_count"],
        )
        self.assertEqual(
            audit_ids,
            [
                item["audit_event_id"]
                for item in self._audit_entries()
                if "audit_event_id" in item
            ],
        )

    async def test_no_dispatch_projection_failure_retries_after_restart(self):
        task, declaration = await self._preintent_active_child(
            "rr16_no_dispatch_retry"
        )
        self._succeed_no_dispatch_child(declaration)
        before_writes = sum(item[0] == "write" for item in self.gateway.calls)

        with patch.object(
            self.runtime,
            "_project",
            side_effect=RuntimeError("synthetic no-dispatch projection failure"),
        ):
            failed = await self.runtime.recover_once("periodic")

        retained = self.runtime.children.active_recovery_checkpoint()
        retry_at = datetime.fromisoformat(
            self.runtime.children.runtime(declaration["child_id"])[
                "next_eligible_at"
            ]
        )
        self.assertEqual(1, failed["active_recovery_transitions"])
        self.assertEqual(
            [declaration["child_id"]],
            [item["child_id"] for item in retained["candidates"]],
        )
        self.assertEqual(
            "preflight", self.service.get_execution_task(task.task_id)["state"]
        )

        self._restart_runtime()
        self.service.now = lambda: retry_at
        with patch.object(
            self.runtime,
            "_execute_child",
            side_effect=AssertionError("terminal child must not execute"),
        ):
            recovered = await self.runtime.recover_once("startup")

        self.assertEqual(1, recovered["active_recovery_transitions"])
        self.assertEqual(
            "succeeded_verified",
            self.service.get_execution_task(task.task_id)["state"],
        )
        self.assertIsNone(self.runtime.children.active_recovery_checkpoint())
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_checkpointed_preintent_terminalizes_no_dispatch_before_reload(
        self,
    ):
        task, declaration = await self._preintent_active_child(
            "rr16_terminal_before_checkpoint_reload"
        )
        self._store_active_checkpoint(declaration)
        self._succeed_no_dispatch_child(declaration)
        before_writes = sum(item[0] == "write" for item in self.gateway.calls)

        with patch.object(
            self.runtime,
            "_execute_child",
            side_effect=AssertionError("terminal child must not execute"),
        ):
            recovered = await self.runtime.recover_once("periodic")

        self.assertEqual(1, recovered["active_recovery_transitions"])
        self.assertEqual(
            "succeeded_verified",
            self.service.get_execution_task(task.task_id)["state"],
        )
        self.assertIsNone(self.runtime.children.active_recovery_checkpoint())
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_complete_no_dispatch_and_mixed_sequences_project(self):
        before_writes = sum(item[0] == "write" for item in self.gateway.calls)
        for suffix, modes in (
            ("all_no_dispatch", ("noop", "noop", "noop")),
            ("mixed", ("noop", "post_intent", "noop")),
        ):
            plan, task, declarations, _prepared, _requests = (
                await self._initialized_hvac_sequence()
            )
            for declaration, mode in zip(declarations, modes, strict=True):
                if mode == "noop":
                    self._succeed_no_dispatch_child(declaration)
                else:
                    await self.runtime._consume_approval_counted(
                        plan, task, declaration
                    )
                    self._succeed_manual_post_intent_child(declaration)
            with self.subTest(sequence=suffix):
                with patch.object(
                    self.runtime,
                    "_execute_child",
                    side_effect=AssertionError(
                        "terminal child must not execute"
                    ),
                ):
                    recovered = await self.runtime.recover_once("periodic")
                public = self.service.get_execution_task(task.task_id)
                self.assertGreaterEqual(
                    recovered["active_recovery_transitions"], 1
                )
                self.assertEqual("succeeded_verified", public["state"])
                self.assertEqual(
                    [0 if mode == "noop" else 1 for mode in modes],
                    [item["dispatch_count"] for item in public["f3_children"]],
                )
                self.assertEqual(
                    ["succeeded_verified"] * len(declarations),
                    [
                        item["normalized_outcome"]
                        for item in public["f3_children"]
                    ],
                )
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_no_dispatch_failure_projects_failed_pre_dispatch(self):
        task, declaration = await self._preintent_active_child(
            "rr16_failed_pre_dispatch"
        )
        self._fail_no_dispatch_child(declaration)
        before_writes = sum(item[0] == "write" for item in self.gateway.calls)

        with patch.object(
            self.runtime,
            "_execute_child",
            side_effect=AssertionError("terminal child must not execute"),
        ):
            recovered = await self.runtime.recover_once("periodic")

        public = self.service.get_execution_task(task.task_id)
        self.assertEqual(1, recovered["active_recovery_transitions"])
        self.assertEqual("failed_pre_dispatch", public["state"])
        self.assertEqual("failed_pre_dispatch", public["terminal_outcome"])
        self.assertEqual(
            "failed_pre_dispatch",
            public["f3_children"][0]["normalized_outcome"],
        )
        self.assertEqual(0, public["f3_children"][0]["dispatch_count"])
        self.assertIsNone(self.runtime.children.active_recovery_checkpoint())
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_successful_prior_child_does_not_displace_later_operation(self):
        plan, task, declarations, prepared, requests = (
            await self._initialized_hvac_sequence()
        )
        await self._complete_sequence_child(
            plan=plan,
            task=task,
            declaration=declarations[0],
            operation=prepared[0],
            requests=requests,
        )
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )

        selection = self.runtime._active_recovery_candidates(
            now=self.service.now(),
            sweep_started=0.0,
            monotonic=lambda: 0.0,
        )

        self.assertEqual(1, len(selection["candidates"]))
        selected, selected_record = selection["candidates"][0]
        self.assertEqual(declarations[1]["child_id"], selected["child_id"])
        self.assertIsNone(selected_record)
        self.assertEqual("preflight", self.service.get_execution_task(
            task.task_id
        )["state"])
        self.assertEqual(
            1,
            self.runtime.children.get(declarations[0]["child_id"]).dispatch_count,
        )
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_no_dispatch_success_does_not_displace_later_operation(self):
        _plan, task, declarations, _prepared, _requests = (
            await self._initialized_hvac_sequence()
        )
        self._succeed_no_dispatch_child(declarations[0])
        before_writes = sum(item[0] == "write" for item in self.gateway.calls)

        selection = self.runtime._active_recovery_candidates(
            now=self.service.now(),
            sweep_started=0.0,
            monotonic=lambda: 0.0,
        )

        selected, selected_record = next(
            item
            for item in selection["candidates"]
            if item[0]["public_task_id"] == task.task_id
        )
        self.assertEqual(declarations[1]["child_id"], selected["child_id"])
        self.assertIsNone(selected_record)
        self.assertEqual(
            "preflight", self.service.get_execution_task(task.task_id)["state"]
        )
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_corrupt_no_dispatch_projection_proof_fails_closed(self):
        _task, declaration = await self._preintent_active_child(
            "rr16_corrupt_noop_proof"
        )
        self._succeed_no_dispatch_child(declaration)
        path = self.runtime.children._path(declaration["child_id"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["execution"]["events"] = [
            item
            for item in payload["execution"]["events"]
            if item["event_type"] != "preflight_noop_verified"
        ]
        path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

        with self.assertRaises(ExecutionRecordCorrupt):
            await self.runtime.recover_once("periodic")

    async def test_corrupt_terminal_dispatch_classes_fail_closed(self):
        _task, no_intent = await self._preintent_active_child(
            "rr16_corrupt_dispatch_count_without_intent"
        )
        self._succeed_no_dispatch_child(no_intent)
        no_intent_path = self.runtime.children._path(no_intent["child_id"])
        no_intent_payload = json.loads(
            no_intent_path.read_text(encoding="utf-8")
        )
        no_intent_payload["execution"]["dispatch_count"] = 1
        no_intent_path.write_text(
            json.dumps(
                no_intent_payload, sort_keys=True, separators=(",", ":")
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ExecutionRecordCorrupt):
            self.runtime.children.get(no_intent["child_id"])

        _task, post_intent = await self._post_intent_active_child(
            "rr16_corrupt_dispatch_count_above_one"
        )
        self._terminalize_post_intent_child(post_intent)
        post_intent_path = self.runtime.children._path(
            post_intent["child_id"]
        )
        post_intent_payload = json.loads(
            post_intent_path.read_text(encoding="utf-8")
        )
        post_intent_payload["execution"]["dispatch_count"] = 2
        post_intent_path.write_text(
            json.dumps(
                post_intent_payload, sort_keys=True, separators=(",", ":")
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ExecutionRecordCorrupt):
            self.runtime.children.get(post_intent["child_id"])

    async def test_writer_impossible_success_cannot_satisfy_dependency(self):
        plan, task, declarations, prepared, requests = (
            await self._initialized_hvac_sequence()
        )
        first = declarations[0]
        second = declarations[1]
        await self._complete_sequence_child(
            plan=plan,
            task=task,
            declaration=first,
            operation=prepared[0],
            requests=requests,
        )
        path = self.runtime.children._path(first["child_id"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        execution = payload["execution"]
        execution["lock_tokens"] = []
        execution["dispatch_intent"]["lock_tokens"] = []
        execution["events"] = [
            item
            for item in execution["events"]
            if item["event_type"]
            not in {"locks_acquired", "preflight_completed"}
        ]
        for sequence, event in enumerate(execution["events"], start=1):
            event["sequence"] = sequence
        path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        public_before = self.service.task_repository.get(task.task_id)
        provider_calls_before = tuple(self.gateway.calls)

        with (
            patch.object(
                self.runtime,
                "_load_prepared",
                side_effect=AssertionError(
                    "dependent provider preparation must remain unreachable"
                ),
            ),
            patch.object(
                self.runtime,
                "_execute_child",
                side_effect=AssertionError(
                    "dependent child execution must remain unreachable"
                ),
            ),
        ):
            with self.assertRaises(ExecutionRecordCorrupt):
                await self.runtime.recover_once("periodic")

        with self.assertRaises(ExecutionRecordCorrupt):
            self.runtime.children.get(first["child_id"])
        self.assertIsNone(self.runtime.children.get(second["child_id"]))
        public_after = self.service.task_repository.get(task.task_id)
        self.assertEqual(public_before.state, public_after.state)
        self.assertEqual(len(public_before.events), len(public_after.events))
        self.assertEqual(provider_calls_before, tuple(self.gateway.calls))

    async def _assert_contradictory_execution_remains_unsettled(
        self, kind: str
    ):
        task, declaration = await self._preintent_active_child(
            f"rr17_{kind}"
        )
        self._claim_child(declaration)
        _owner, handle = self._hold_child_lock(
            declaration,
            expired=True,
            conflict_hold=True,
        )
        self._set_runtime_tokens(declaration["child_id"], handle)
        identity, claim, timing = self._claims[declaration["child_id"]]
        if kind == "intent_with_pre_dispatch_outcome":
            self.runtime.children.record_preflight(
                declaration["child_id"],
                owner_id=identity.owner_id,
                claim_generation=claim.claim_generation,
                now=self.service.now(),
            )
            self.runtime.children.commit_dispatch_intent(
                declaration["child_id"],
                owner_id=identity.owner_id,
                claim_generation=claim.claim_generation,
                request_id=declaration["request_id"],
                provider_operation=declaration["operation_id"],
                provider_arguments_hash=hashlib.sha256(
                    f"{declaration['child_id']}:arguments".encode()
                ).hexdigest(),
                timing=timing,
                now=self.service.now(),
            )
        else:
            self.runtime.children.terminalize_pre_dispatch(
                declaration["child_id"],
                owner_id=identity.owner_id,
                claim_generation=claim.claim_generation,
                outcome="failed_pre_dispatch",
                now=self.service.now(),
            )
        checkpoint = self._store_active_checkpoint(declaration)
        path = self.runtime.children._path(declaration["child_id"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        changes = {
            "no_intent_failed_post_dispatch": {
                "normalized_outcome": "failed_post_dispatch",
                "task_state": "failed_post_dispatch",
            },
            "no_intent_manual_review": {
                "normalized_outcome": "manual_review_required",
                "task_state": "manual_review_required",
            },
            "no_intent_provider_response": {
                "provider_response_received": True,
            },
            "no_intent_observation": {"observation_attempts": 1},
            "no_intent_verification": {"verification_attempts": 1},
            "no_intent_post_dispatch_event_evidence": {
                "evidence": {
                    "manual_review_reason_code": "synthetic_post_dispatch",
                },
            },
            "intent_with_pre_dispatch_outcome": {
                "state": "terminal",
                "normalized_outcome": "failed_pre_dispatch",
                "task_state": "failed_pre_dispatch",
                "terminal": True,
            },
        }[kind]
        payload["execution"].update(changes)
        if kind == "no_intent_post_dispatch_event_evidence":
            payload["execution"]["events"].append(
                {
                    "sequence": len(payload["execution"]["events"]) + 1,
                    "event_type": "verification_recorded",
                    "occurred_at": self.service.now().isoformat(),
                    "diagnostic_codes": [],
                }
            )
        path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

        public_before = self.service.task_repository.get(task.task_id)
        lock_before = tuple(self.runtime.locks.records())
        runtime_before = self.runtime.children.runtime(
            declaration["child_id"]
        )
        writes_before = sum(
            item[0] == "write" for item in self.gateway.calls
        )
        with (
            patch.object(
                self.runtime,
                "_load_prepared",
                side_effect=AssertionError(
                    "corrupt execution must not prepare a provider operation"
                ),
            ),
            patch.object(
                self.runtime,
                "_execute_child",
                side_effect=AssertionError(
                    "corrupt execution must not execute"
                ),
            ),
        ):
            with self.assertRaises(ExecutionRecordCorrupt):
                await self.runtime.recover_once("periodic")
            with self.assertRaises(ExecutionRecordCorrupt):
                await self.runtime.recover_once("periodic")

        public_after = self.service.task_repository.get(task.task_id)
        self.assertEqual(public_before.state, public_after.state)
        self.assertEqual(
            public_before.terminal_outcome,
            public_after.terminal_outcome,
        )
        self.assertEqual(len(public_before.events), len(public_after.events))
        self.assertEqual(
            checkpoint,
            self.runtime.children.active_recovery_checkpoint(),
        )
        self.assertEqual(lock_before, tuple(self.runtime.locks.records()))
        self.assertEqual(
            runtime_before["selective_hold_tokens"],
            self.runtime.children.runtime(declaration["child_id"])[
                "selective_hold_tokens"
            ],
        )
        self.assertEqual(
            writes_before,
            sum(item[0] == "write" for item in self.gateway.calls),
        )
        durable_after = json.loads(path.read_text(encoding="utf-8"))[
            "execution"
        ]
        for field_name, expected in changes.items():
            self.assertEqual(expected, durable_after[field_name])
        if kind == "no_intent_post_dispatch_event_evidence":
            self.assertEqual(
                "verification_recorded",
                durable_after["events"][-1]["event_type"],
            )

    async def test_no_intent_failed_post_dispatch_fails_closed(self):
        await self._assert_contradictory_execution_remains_unsettled(
            "no_intent_failed_post_dispatch"
        )

    async def test_no_intent_manual_review_fails_closed(self):
        await self._assert_contradictory_execution_remains_unsettled(
            "no_intent_manual_review"
        )

    async def test_no_intent_provider_response_fails_closed(self):
        await self._assert_contradictory_execution_remains_unsettled(
            "no_intent_provider_response"
        )

    async def test_no_intent_observation_fails_closed(self):
        await self._assert_contradictory_execution_remains_unsettled(
            "no_intent_observation"
        )

    async def test_no_intent_verification_fails_closed(self):
        await self._assert_contradictory_execution_remains_unsettled(
            "no_intent_verification"
        )

    async def test_no_intent_post_dispatch_event_evidence_fails_closed(self):
        await self._assert_contradictory_execution_remains_unsettled(
            "no_intent_post_dispatch_event_evidence"
        )

    async def test_intent_with_pre_dispatch_outcome_fails_closed(self):
        await self._assert_contradictory_execution_remains_unsettled(
            "intent_with_pre_dispatch_outcome"
        )

    async def test_seventeen_no_dispatch_projections_obey_batch_bound(self):
        fixtures = []
        for index in range(RECOVERY_BATCH_SIZE + 1):
            task, declaration = await self._preintent_active_child(
                f"rr16_batch_{index:02d}"
            )
            self._succeed_no_dispatch_child(declaration)
            fixtures.append((task, declaration))
        before_writes = sum(item[0] == "write" for item in self.gateway.calls)

        self.runtime._recovery_monotonic = lambda: 0.0
        with patch.object(
            self.runtime,
            "_execute_child",
            side_effect=AssertionError("terminal child must not execute"),
        ):
            first = await self.runtime.recover_once("periodic")
            second = await self.runtime.recover_once("periodic")

        self.assertEqual(RECOVERY_BATCH_SIZE, first["active_recovery_transitions"])
        self.assertEqual(1, second["active_recovery_transitions"])
        self.assertTrue(
            all(
                self.service.get_execution_task(task.task_id)["state"]
                == "succeeded_verified"
                for task, _declaration in fixtures
            )
        )
        self.assertTrue(
            all(
                self.runtime.children.get(declaration["child_id"]).dispatch_count
                == 0
                for _task, declaration in fixtures
            )
        )
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_terminal_failure_after_success_controls_parent_projection(self):
        plan, task, declarations, prepared, requests = (
            await self._initialized_hvac_sequence()
        )
        await self._complete_sequence_child(
            plan=plan,
            task=task,
            declaration=declarations[0],
            operation=prepared[0],
            requests=requests,
        )
        self.gateway.fail_write_target = ("script", "set_hvac_comfort")
        failed = await self.runtime._execute_child(
            plan,
            task,
            declarations[1],
            prepared[1],
            requests,
        )
        failed_record = self.runtime.children.get(
            declarations[1]["child_id"]
        )
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )
        self.assertTrue(failed.terminal)
        self.assertNotEqual("succeeded_verified", failed.outcome)
        self.assertTrue(failed_record.terminal)

        with patch.object(
            self.runtime,
            "_execute_child",
            side_effect=AssertionError("terminal sequence must only project"),
        ):
            recovered = await self.runtime.recover_once("periodic")

        public = self.service.get_execution_task(task.task_id)
        projected = {
            item["child_execution_id"]: item
            for item in public["f3_children"]
        }
        self.assertEqual(1, recovered["active_recovery_transitions"])
        self.assertEqual("manual_review_required", public["terminal_outcome"])
        self.assertNotEqual("succeeded_verified", public["terminal_outcome"])
        self.assertEqual(
            "succeeded_verified",
            projected[declarations[0]["child_id"]]["normalized_outcome"],
        )
        self.assertEqual(
            failed.outcome,
            projected[declarations[1]["child_id"]]["normalized_outcome"],
        )
        self.assertEqual(
            1,
            self.runtime.children.get(declarations[0]["child_id"]).dispatch_count,
        )
        self.assertEqual(1, failed_record.dispatch_count)
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_complete_successful_sequence_projects_once(self):
        plan, task, declarations, prepared, requests = (
            await self._initialized_hvac_sequence()
        )
        for declaration, operation in zip(
            declarations, prepared, strict=True
        ):
            await self._complete_sequence_child(
                plan=plan,
                task=task,
                declaration=declaration,
                operation=operation,
                requests=requests,
            )
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )
        self.assertEqual("preflight", self.service.get_execution_task(
            task.task_id
        )["state"])
        self.assertIsNone(self.runtime.children.active_recovery_checkpoint())

        with patch.object(
            self.runtime,
            "_execute_child",
            side_effect=AssertionError("successful sequence must only project"),
        ):
            result = await self.runtime.recover_once("periodic")

        public = self.service.get_execution_task(task.task_id)
        self.assertEqual(1, result["active_recovery_transitions"])
        self.assertEqual("succeeded_verified", public["state"])
        self.assertEqual("succeeded_verified", public["terminal_outcome"])
        self.assertEqual(
            ["succeeded_verified"] * len(declarations),
            [item["normalized_outcome"] for item in public["f3_children"]],
        )
        self.assertTrue(
            all(
                self.runtime.children.get(item["child_id"]).dispatch_count == 1
                for item in declarations
            )
        )
        self.assertIsNone(self.runtime.children.active_recovery_checkpoint())
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_terminalization_between_selection_and_reload_projects(self):
        task, declaration = await self._post_intent_active_child(
            "rr14_terminal_during_reload"
        )
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )
        original_reload = self.runtime._reload_active_candidate
        terminalized = {"value": False}

        def terminalize_then_reload(reference, *, now, recovery_mode):
            if (
                reference["child_id"] == declaration["child_id"]
                and not terminalized["value"]
            ):
                terminalized["value"] = True
                self._terminalize_post_intent_child(
                    declaration,
                    diagnostic_code="rr14_terminal_between_selection_reload",
                )
            return original_reload(
                reference,
                now=now,
                recovery_mode=recovery_mode,
            )

        with (
            patch.object(
                self.runtime,
                "_reload_active_candidate",
                side_effect=terminalize_then_reload,
            ),
            patch.object(
                self.runtime,
                "_execute_child",
                side_effect=AssertionError("terminal child must not execute"),
            ),
        ):
            result = await self.runtime.recover_once("periodic")

        public = self.service.get_execution_task(task.task_id)
        self.assertTrue(terminalized["value"])
        self.assertEqual(1, result["active_recovery_transitions"])
        self.assertEqual("manual_review_required", public["state"])
        self.assertIsNone(self.runtime.children.active_recovery_checkpoint())
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_terminal_projection_failure_remains_checkpointed_for_retry(
        self,
    ):
        task, declaration = await self._post_intent_active_child(
            "rr14_projection_retry"
        )
        self._store_active_checkpoint(declaration)
        self._terminalize_post_intent_child(
            declaration,
            diagnostic_code="rr14_projection_retry_evidence",
        )
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )
        before_cursor = self.runtime.children.active_recovery_cursor()

        with patch.object(
            self.runtime,
            "_project",
            side_effect=RuntimeError("synthetic public projection failure"),
        ):
            failed = await self.runtime.recover_once("periodic")

        retained = self.runtime.children.active_recovery_checkpoint()
        retry_at = datetime.fromisoformat(
            self.runtime.children.runtime(declaration["child_id"])[
                "next_eligible_at"
            ]
        )
        self.assertEqual(1, failed["active_recovery_transitions"])
        self.assertEqual(
            [declaration["child_id"]],
            [item["child_id"] for item in retained["candidates"]],
        )
        self.assertEqual(
            before_cursor, self.runtime.children.active_recovery_cursor()
        )
        self.assertNotIn(
            self.service.get_execution_task(task.task_id)["state"],
            {"manual_review_required", "failed_post_dispatch"},
        )

        deferred = await self.runtime.recover_once("periodic")
        self.assertEqual(0, deferred["active_recovery_transitions"])
        self.assertEqual(
            retained, self.runtime.children.active_recovery_checkpoint()
        )
        self.assertEqual(
            before_cursor, self.runtime.children.active_recovery_cursor()
        )

        self.service.now = lambda: retry_at
        recovered = await self.runtime.recover_once("periodic")
        self.assertEqual(1, recovered["active_recovery_transitions"])
        self.assertEqual(
            "manual_review_required",
            self.service.get_execution_task(task.task_id)["state"],
        )
        self.assertIsNone(self.runtime.children.active_recovery_checkpoint())
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_terminal_projection_crash_replays_after_restart(self):
        task, declaration = await self._post_intent_active_child(
            "rr14_projection_crash"
        )
        checkpoint = self._store_active_checkpoint(declaration)
        self._terminalize_post_intent_child(
            declaration,
            diagnostic_code="rr14_projection_crash_evidence",
        )
        before_writes = sum(
            item[0] == "write" for item in self.gateway.calls
        )

        with patch.object(
            self.runtime,
            "_project",
            side_effect=SystemExit("synthetic crash before public projection"),
        ):
            with self.assertRaises(SystemExit):
                await self.runtime.recover_once("periodic")

        self.assertEqual(
            checkpoint, self.runtime.children.active_recovery_checkpoint()
        )
        self._restart_runtime()
        recovered = await self.runtime.recover_once("startup")
        self.assertEqual(1, recovered["active_recovery_transitions"])
        self.assertEqual(
            "manual_review_required",
            self.service.get_execution_task(task.task_id)["state"],
        )
        self.assertIsNone(self.runtime.children.active_recovery_checkpoint())
        self.assertEqual(
            before_writes,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

    async def test_checkpoint_reloads_changed_candidate_authority(self):
        fixtures = {}
        for name in (
            "removed",
            "authority",
            "operation",
            "attempt",
            "backoff",
            "dispatch_evidence",
        ):
            task, declaration = await self._post_intent_active_child(
                f"rr13_changed_{name}"
            )
            fixtures[name] = (task, declaration)
            self._release_fixture_locks(declaration["child_id"])

        checkpoint = (
            self.runtime.children.active_recovery_checkpoint_for_candidates(
                item[1] for item in fixtures.values()
            )
        )
        for item in checkpoint["candidates"]:
            if item["child_id"] == fixtures["operation"][1]["child_id"]:
                item["operation_id"] = "changed_operation"
            if item["child_id"] == fixtures["attempt"][1]["child_id"]:
                item["attempt_id"] = "changed-attempt"
        self.runtime.children.replace_active_recovery_checkpoint(
            expected=None, next_checkpoint=checkpoint
        )

        removed_task = fixtures["removed"][0]
        self.service.task_repository._path(removed_task.task_id).unlink()
        self.service.task_repository.rebuild_navigation_index()

        authority_task = fixtures["authority"][0]
        changed_projection = {
            **authority_task.legacy_projection,
            "execution_authority": "legacy_execution",
        }
        authority_task.append_event(
            "execution_authority_changed",
            self.service.now().isoformat(),
            changes={"legacy_projection": changed_projection},
            request_id="synthetic-rr13-authority-change",
        )
        self.service.task_repository.save(authority_task)

        retry_at = self.service.now() + timedelta(seconds=30)
        self.runtime.children.update_runtime(
            fixtures["backoff"][1]["child_id"],
            changes={
                "backoff_seconds": 30,
                "next_eligible_at": retry_at.isoformat(),
            },
        )

        dispatch_id = fixtures["dispatch_evidence"][1]["child_id"]
        dispatch_record = self.runtime.children.get(dispatch_id)
        dispatch_identity = dispatch_record.execution_identity()
        changed_deadline = self.service.now() + timedelta(seconds=60)

        def change_dispatch_evidence(record):
            record.dispatch_intent["evidence_deadline"] = (
                changed_deadline.isoformat()
            )

        self.runtime.children.mutate_claimed(
            dispatch_id,
            owner_id=dispatch_identity.owner_id,
            claim_generation=dispatch_record.claim_generation,
            mutator=change_dispatch_evidence,
        )
        order = []
        expired = {"value": False}
        original_execute = self.runtime._execute_child

        async def expire_after_current_authority(
            plan, task, declaration, operation, requests
        ):
            order.append(declaration["child_id"])
            result = await original_execute(
                plan, task, declaration, operation, requests
            )
            expired["value"] = True
            return result

        self.runtime._recovery_monotonic = (
            lambda: 6.0 if expired["value"] else 0.0
        )
        with patch.object(
            self.runtime,
            "_execute_child",
            side_effect=expire_after_current_authority,
        ):
            result = await self.runtime.recover_once("periodic")

        self.assertEqual(1, result["active_recovery_transitions"])
        self.assertEqual([dispatch_id], order)
        self.assertEqual(
            changed_deadline,
            datetime.fromisoformat(
                self.runtime.children.get(dispatch_id).dispatch_intent[
                    "evidence_deadline"
                ]
            ),
        )
        self.assertEqual(
            [fixtures["backoff"][1]["child_id"]],
            [
                item["child_id"]
                for item in self.runtime.children.active_recovery_checkpoint()[
                    "candidates"
                ]
            ],
        )
        self.assertEqual(
            0, sum(item[0] == "write" for item in self.gateway.calls)
        )

        self.service.now = lambda: retry_at
        self.runtime._recovery_monotonic = lambda: 0.0
        resumed = await self.runtime.recover_once("periodic")
        self.assertGreaterEqual(resumed["active_recovery_transitions"], 1)
        self.assertEqual(
            1,
            self.runtime.children.get(
                fixtures["backoff"][1]["child_id"]
            ).dispatch_count,
        )
        self.assertIsNone(
            self.runtime.children.active_recovery_checkpoint()
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

    async def test_full_deferred_checkpoint_does_not_block_fresh_readback(
        self,
    ):
        retry_at = self.service.now() + timedelta(seconds=300)
        deferred = []
        for index in range(RECOVERY_BATCH_SIZE):
            _task, declaration = await self._post_intent_active_child(
                f"checkpoint_deferred_{index:02d}"
            )
            self._release_fixture_locks(declaration["child_id"])
            self.runtime.children.update_runtime(
                declaration["child_id"],
                changes={
                    "backoff_seconds": 300,
                    "next_eligible_at": retry_at.isoformat(),
                },
            )
            deferred.append(declaration)
        self._store_active_checkpoint(*deferred)
        _fresh_task, fresh = await self._post_intent_active_child(
            "checkpoint_fresh_readback"
        )
        self._release_fixture_locks(fresh["child_id"])
        writes_before = sum(
            item[0] == "write" for item in self.gateway.calls
        )
        self.runtime._recovery_monotonic = lambda: 0.0

        first = await self.runtime.recover_once("periodic")

        self.assertEqual(1, first["active_recovery_transitions"])
        fresh_record = self.runtime.children.get(fresh["child_id"])
        self.assertEqual(1, fresh_record.dispatch_count)
        self.assertTrue(
            fresh_record.terminal
            or any(
                item["event_type"] == "recovery_claimed"
                for item in fresh_record.events
            )
        )
        retained = self.runtime.children.active_recovery_checkpoint()
        retained_ids = {
            item["child_id"]
            for item in (() if retained is None else retained["candidates"])
        }
        deferred_ids = {item["child_id"] for item in deferred}
        self.assertLessEqual(len(retained_ids), RECOVERY_BATCH_SIZE)
        self.assertEqual(1, len(deferred_ids - retained_ids))
        self.assertEqual(
            writes_before,
            sum(item[0] == "write" for item in self.gateway.calls),
        )

        self.service.now = lambda: retry_at
        second = await self.runtime.recover_once("periodic")

        self.assertEqual(
            RECOVERY_BATCH_SIZE, second["active_recovery_transitions"]
        )
        self.assertTrue(
            all(
                self.runtime.children.get(item["child_id"]).dispatch_count
                == 1
                for item in deferred
            )
        )
        self.assertEqual(
            writes_before,
            sum(item[0] == "write" for item in self.gateway.calls),
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

    async def test_corrupt_nonauthoritative_navigation_resets_on_startup_and_periodic(
        self,
    ):
        navigation_paths = {
            "declaration_cursor": self.runtime.children.recovery_cursor_path,
            "active_cursor": self.runtime.children.active_recovery_cursor_path,
            "active_checkpoint": (
                self.runtime.children.active_recovery_checkpoint_path
            ),
        }
        for trigger, payload in (
            ("startup", "{"),
            ("periodic", '{"model":"unknown"}'),
        ):
            for navigation_kind, path in navigation_paths.items():
                with self.subTest(
                    trigger=trigger, navigation_kind=navigation_kind
                ):
                    path.write_text(payload, encoding="utf-8")
                    with (
                        patch(
                            "ha_mcp_engineering.f3_runtime.repository."
                            "log_event"
                        ) as diagnostic,
                        patch.object(
                            self.runtime,
                            "_execute_child",
                            side_effect=AssertionError(
                                "navigation reset cannot authorize dispatch"
                            ),
                        ),
                    ):
                        result = await self.runtime.recover_once(trigger)

                    self.assertFalse(path.exists())
                    self.assertEqual(0, result["recovery_transitions"])
                    diagnostic.assert_called_once()
                    self.assertEqual(
                        navigation_kind,
                        diagnostic.call_args.kwargs["context"][
                            "navigation_kind"
                        ],
                    )

    async def test_malformed_authoritative_manifest_remains_fail_closed(
        self,
    ):
        task, declaration = await self._preintent_active_child(
            "rr18_malformed_authoritative_manifest"
        )
        child_id = declaration["child_id"]
        manifest_path = (
            self.runtime.children.root / f"{task.task_id}.manifest.json"
        )
        self.assertTrue(manifest_path.exists())
        self.assertIsNone(self.runtime.children.get(child_id))
        child_path = self.runtime.children._path(child_id)
        child_bytes_before = (
            child_path.read_bytes() if child_path.exists() else None
        )
        manifest_path.write_text("{", encoding="utf-8")
        malformed_bytes = manifest_path.read_bytes()
        related_before = tuple(
            sorted(
                path.name
                for path in manifest_path.parent.glob(
                    f"{manifest_path.name}*"
                )
            )
        )
        public_before = self.service.task_repository.get(task.task_id).to_dict()
        provider_service_calls_before = tuple(self.gateway.calls)

        def assert_authoritative_state_unchanged(runtime):
            self.assertTrue(manifest_path.exists())
            self.assertEqual(malformed_bytes, manifest_path.read_bytes())
            self.assertEqual(
                related_before,
                tuple(
                    sorted(
                        path.name
                        for path in manifest_path.parent.glob(
                            f"{manifest_path.name}*"
                        )
                    )
                ),
            )
            self.assertIsNone(runtime.children.get(child_id))
            self.assertEqual(
                child_bytes_before,
                child_path.read_bytes() if child_path.exists() else None,
            )
            public_after = self.service.task_repository.get(task.task_id)
            self.assertEqual(public_before, public_after.to_dict())
            self.assertIsNone(public_after.dispatched_at)
            self.assertEqual([], public_after.provider_attempts)
            self.assertEqual(
                provider_service_calls_before, tuple(self.gateway.calls)
            )

        navigation_path = (
            self.runtime.children.active_recovery_checkpoint_path
        )
        navigation_path.write_text("{", encoding="utf-8")
        with (
            patch.object(
                self.runtime,
                "_load_prepared",
                side_effect=AssertionError(
                    "corrupt manifest cannot prepare provider work"
                ),
            ),
            patch.object(
                self.runtime,
                "_execute_child",
                side_effect=AssertionError(
                    "corrupt manifest cannot authorize dispatch"
                ),
            ),
        ):
            with self.assertRaises(ExecutionRecordCorrupt):
                await self.runtime.recover_once("periodic")

        self.assertFalse(navigation_path.exists())
        assert_authoritative_state_unchanged(self.runtime)

        startup_navigation_path = (
            self.runtime.children.active_recovery_checkpoint_path
        )
        startup_navigation_path.write_text("{", encoding="utf-8")
        with (
            patch.object(
                F3RuntimeIntegration,
                "_load_prepared",
                side_effect=AssertionError(
                    "startup cannot prepare through a corrupt manifest"
                ),
            ),
            patch.object(
                F3RuntimeIntegration,
                "_execute_child",
                side_effect=AssertionError(
                    "startup cannot dispatch through a corrupt manifest"
                ),
            ),
        ):
            with self.assertRaises(ExecutionRecordCorrupt):
                self._restart_runtime()

        self.assertTrue(startup_navigation_path.exists())
        assert_authoritative_state_unchanged(self.runtime)

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

    def test_active_recovery_checkpoint_compare_and_swap_fails_closed(self):
        first = self.runtime.children.active_recovery_checkpoint_for_candidates(
            (
                child_declaration(
                    public_task_id="a" * 32,
                    plan_id="b" * 32,
                    plan_hash="c" * 64,
                    plan_contract_version=2,
                    operation_id="checkpoint_first",
                    ordinal=0,
                    dependency_ids=(),
                    adapter_id="checkpoint_adapter",
                    capability_id="update_automation_configuration",
                    prepared_operation_hash="d" * 64,
                    target_type="automation",
                    target_id="checkpoint_first",
                    attempt_id="checkpoint-attempt-first",
                    request_id="checkpoint-request-first",
                    idempotency_key="checkpoint-key-first",
                    complete_lock_request_hash="e" * 64,
                    approval_bundle_hash="f" * 64,
                    selective_hold_keys=("automation:checkpoint_first",),
                ),
            )
        )
        second = copy.deepcopy(first)
        second["candidates"][0]["operation_id"] = "checkpoint_second"
        self.runtime.children.replace_active_recovery_checkpoint(
            expected=None, next_checkpoint=first
        )

        with self.assertRaises(ExecutionStorageError):
            self.runtime.children.replace_active_recovery_checkpoint(
                expected=None, next_checkpoint=second
            )
        self.assertEqual(
            first, self.runtime.children.active_recovery_checkpoint()
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
