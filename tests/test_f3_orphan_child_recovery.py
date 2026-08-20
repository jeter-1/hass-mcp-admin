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

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3.contracts import (  # noqa: E402
    NormalizedOperationOutcome,
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
)
from ha_mcp_engineering.f3.contracts import (  # noqa: E402
    F3_ADAPTER_CONTRACT_MODEL,
)
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
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

    def _strand_children(self, declarations):
        """Drop execution records so children look never-finished."""

        for declaration in declarations:
            path = self.runtime.children._path(declaration["child_id"])
            envelope = self.runtime.children._raw_envelope(
                declaration["child_id"]
            )
            if envelope is None:
                continue
            envelope["execution"] = None
            self.runtime.children._atomic_write(path, envelope)

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

    async def _build_live_orphan(self):
        """Assemble the exact shape observed on the deployed server."""

        task, declarations = await self._orphaned_task()
        self._strand_children(declarations)
        self._claim_child(declarations[0])
        health = self.runtime.children.health()
        self.assertEqual(1, health["nonterminal_execution_count"])
        return task, declarations

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
        for child in children:
            with self.subTest(child=child["operation_id"]):
                self.assertEqual("terminal", child["state"])
                self.assertEqual(
                    "cancelled_pre_dispatch", child["normalized_outcome"]
                )
                self.assertEqual(0, child["dispatch_count"])

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

if __name__ == "__main__":
    unittest.main()
