"""Deterministic regression coverage for bounded restart reconciliation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from tests.test_2_1a_beta2_operational_lifecycle import (
    Clock,
    FakeLifecycleGateway,
    LegacyGateway,
    SelfRestartRecoveryGateway,
)

from ha_mcp_engineering.governance.models import (
    PlanStatus,
)
from ha_mcp_engineering.governance.service import (
    ChangeGovernanceService,
    RESTART_DISPATCH_TIMESTAMP_UNAVAILABLE,
    RESTART_RECONCILIATION_BACKOFF_SECONDS,
    RESTART_VERIFICATION_WINDOW_EXPIRED,
)
from ha_mcp_engineering.governance.storage import ChangePlanRepository
from ha_mcp_engineering.request_context import begin_request, end_request


class Beta11RestartReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "plans"
        self.clock = Clock()
        self.gateway = FakeLifecycleGateway()
        self.gateway.now = self.clock
        self.repository = ChangePlanRepository(self.root)
        self.service = ChangeGovernanceService(
            self.repository,
            LegacyGateway(),
            now=self.clock,
            lifecycle_gateway=self.gateway,
        )
        self.telemetry, self.context = begin_request(
            "beta11-restart-reconciliation"
        )
        self.telemetry.caller_id = "mcp-requester"

    async def asyncTearDown(self) -> None:
        end_request(self.context)
        self.temp.cleanup()

    async def _grant(self, created: dict) -> dict:
        plan = created["plan"]
        pending = self.service.approve(plan["plan_id"], plan["plan_hash"])
        _, csrf = await self.service.issue_external_csrf(
            plan["plan_id"], pending["challenge_id"]
        )
        result = await self.service.decide_external_approval(
            plan_id=plan["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=plan["plan_hash"],
            approval_kind="apply",
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal="home_assistant_admin_ingress:fixture",
        )
        if result.get("status") == "approval_pending":
            _, csrf = await self.service.issue_external_csrf(
                plan["plan_id"], result["challenge_id"]
            )
            await self.service.decide_external_approval(
                plan_id=plan["plan_id"],
                challenge_id=result["challenge_id"],
                expected_plan_hash=plan["plan_hash"],
                approval_kind="apply",
                approval_action=result["approval_action"],
                csrf_nonce=csrf,
                decision="approve",
                approver_principal=(
                    "home_assistant_admin_ingress:fixture"
                ),
            )
        return plan

    async def _pending_addon_restart(self) -> tuple[dict, object]:
        created = await self.service.create_addon_restart_plan(
            addon_slug="local_test_addon"
        )
        plan = await self._grant(created)
        self.gateway.mode = "ambiguous"
        self.gateway.verification_status = "pending"
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(result["status"], "verification_pending")
        task = self.service.task_repository.get_for_plan(plan["plan_id"])
        self.assertIsNotNone(task)
        return plan, task

    def _recovered(
        self, gateway: FakeLifecycleGateway | None = None
    ) -> ChangeGovernanceService:
        recovered_gateway = gateway or SelfRestartRecoveryGateway(
            "process-two"
        )
        recovered_gateway.now = self.clock
        if isinstance(recovered_gateway, SelfRestartRecoveryGateway):
            recovered_gateway.missing_readback = True
        return ChangeGovernanceService(
            ChangePlanRepository(self.root),
            LegacyGateway(),
            now=self.clock,
            lifecycle_gateway=recovered_gateway,
        )

    async def test_expired_durable_restart_terminalizes_without_probe(self):
        plan, task = await self._pending_addon_restart()
        deadline = task.maximum_post_dispatch_deadline
        self.clock.value = self.clock.value + timedelta(hours=24, seconds=1)
        recovered_gateway = SelfRestartRecoveryGateway("process-two")
        recovered = self._recovered(recovered_gateway)

        result = await recovered.reconcile_execution_tasks(trigger="startup")
        persisted_task = recovered.get_execution_task(task.task_id)
        persisted_plan = recovered.repository.get(plan["plan_id"])

        self.assertEqual(result["manual_review_required"], 1)
        self.assertEqual(persisted_task["state"], "manual_review_required")
        self.assertEqual(
            persisted_task["manual_review_reason"],
            RESTART_VERIFICATION_WINDOW_EXPIRED,
        )
        self.assertEqual(
            persisted_task["maximum_post_dispatch_deadline"], deadline
        )
        self.assertEqual(persisted_plan.status, PlanStatus.VERIFICATION_FAILED)
        self.assertEqual(
            persisted_plan.failure_information["error_code"],
            RESTART_VERIFICATION_WINDOW_EXPIRED,
        )
        self.assertEqual(recovered_gateway.verification_count, 0)
        self.assertEqual(recovered_gateway.dispatch_count, 0)

    async def test_expired_taskless_historical_plan_terminalizes_idempotently(self):
        created = await self.service.create_addon_restart_plan(
            addon_slug="local_test_addon"
        )
        plan_public = await self._grant(created)
        plan = self.repository.get(plan_public["plan_id"])
        self.service._consume_approval_bundle(plan)
        attempted_at = self.clock().isoformat()
        plan.operational.dispatch.update(
            {
                "attempt_count": 1,
                "dispatched": True,
                "attempted_at": attempted_at,
            }
        )
        plan.status = PlanStatus.VERIFICATION_REQUIRED
        plan.execution_outcome = "verification_pending"
        self.repository.save(plan)
        self.clock.advance(hours=24, seconds=1)
        recovered_gateway = SelfRestartRecoveryGateway("process-two")
        recovered = self._recovered(recovered_gateway)

        first = await recovered.reconcile_operational_plans(trigger="startup")
        record_path = recovered.repository._path(
            plan.plan_id, operational=True
        )
        after_first = record_path.read_bytes()
        second = await recovered.reconcile_operational_plans(trigger="periodic")
        after_second = record_path.read_bytes()

        self.assertEqual(first["failed"], 1)
        self.assertEqual(second["checked"], 0)
        self.assertEqual(after_second, after_first)
        self.assertEqual(recovered_gateway.verification_count, 0)
        self.assertEqual(recovered_gateway.dispatch_count, 0)

    async def test_missing_historical_dispatch_timestamp_fails_closed(self):
        created = await self.service.create_addon_restart_plan(
            addon_slug="local_test_addon"
        )
        public = await self._grant(created)
        plan = self.repository.get(public["plan_id"])
        self.service._consume_approval_bundle(plan)
        plan.operational.dispatch.update(
            {"attempt_count": 1, "dispatched": True}
        )
        plan.status = PlanStatus.VERIFICATION_REQUIRED
        self.repository.save(plan)
        recovered_gateway = SelfRestartRecoveryGateway("process-two")
        recovered = self._recovered(recovered_gateway)

        await recovered.reconcile_operational_plans(trigger="startup")
        persisted = recovered.repository.get(plan.plan_id)

        self.assertEqual(
            persisted.failure_information["error_code"],
            RESTART_DISPATCH_TIMESTAMP_UNAVAILABLE,
        )
        self.assertEqual(recovered_gateway.verification_count, 0)
        self.assertEqual(recovered_gateway.dispatch_count, 0)

    async def test_backoff_is_persisted_grows_and_survives_restart(self):
        plan, task = await self._pending_addon_restart()
        recovered_gateway = SelfRestartRecoveryGateway("process-two")
        recovered = self._recovered(recovered_gateway)

        first = await recovered.reconcile_operational_plans(trigger="startup")
        first_task = recovered.get_execution_task(task.task_id)
        first_state = first_task["verification_summary"][
            "restart_reconciliation"
        ]
        immediate = await recovered.reconcile_operational_plans(
            trigger="periodic"
        )
        self.clock.value = self.clock.value + timedelta(
            seconds=first_state["backoff_seconds"]
        )
        recreated = self._recovered(recovered_gateway)
        second = await recreated.reconcile_operational_plans(
            trigger="startup"
        )
        second_state = recreated.get_execution_task(task.task_id)[
            "verification_summary"
        ]["restart_reconciliation"]

        self.assertEqual(first["checked"], 1)
        self.assertEqual(immediate["checked"], 0)
        self.assertEqual(second["checked"], 1)
        self.assertEqual(first_state["attempt_count"], 1)
        self.assertEqual(
            first_state["backoff_seconds"],
            RESTART_RECONCILIATION_BACKOFF_SECONDS[0],
        )
        self.assertEqual(second_state["attempt_count"], 2)
        self.assertEqual(
            second_state["backoff_seconds"],
            RESTART_RECONCILIATION_BACKOFF_SECONDS[1],
        )
        self.assertEqual(
            second_state["evidence_deadline"],
            task.maximum_post_dispatch_deadline,
        )
        self.assertLessEqual(
            second_state["next_attempt_at"],
            task.maximum_post_dispatch_deadline,
        )
        self.assertEqual(recovered_gateway.dispatch_count, 0)

    async def test_stale_task_has_bounded_probes_over_several_hours(self):
        _plan, _task = await self._pending_addon_restart()
        recovered_gateway = SelfRestartRecoveryGateway("process-two")
        recovered = self._recovered(recovered_gateway)
        end = self.clock.value + timedelta(hours=3)

        while self.clock.value < end:
            await recovered.reconcile_operational_plans(trigger="periodic")
            plans = recovered.repository.list()
            state = plans[0].operational.dispatch.get(
                "restart_reconciliation", {}
            )
            next_attempt = state.get("next_attempt_at")
            if next_attempt is None:
                break
            self.clock.value = datetime.fromisoformat(next_attempt)

        health = recovered.health_summary()["restart_reconciliation"]
        self.assertLessEqual(recovered_gateway.verification_count, 16)
        self.assertEqual(
            health["expensive_probe_count"],
            recovered_gateway.verification_count,
        )
        self.assertEqual(recovered_gateway.dispatch_count, 0)

    async def test_single_flight_and_health_identify_active_task(self):
        plan, task = await self._pending_addon_restart()
        recovered_gateway = SelfRestartRecoveryGateway("process-two")
        recovered_gateway.missing_readback = True
        recovered_gateway.verification_entered = asyncio.Event()
        recovered_gateway.verification_release = asyncio.Event()
        recovered = self._recovered(recovered_gateway)

        first = asyncio.create_task(
            recovered.reconcile_operational_plans(trigger="startup")
        )
        await recovered_gateway.verification_entered.wait()
        active = recovered.health_summary()["restart_reconciliation"]
        collision = await recovered.reconcile_operational_plans(
            trigger="periodic"
        )
        recovered_gateway.verification_release.set()
        await first
        idle = recovered.health_summary()["restart_reconciliation"]

        self.assertTrue(active["active"])
        self.assertEqual(active["plan_id"], plan["plan_id"])
        self.assertEqual(active["task_id"], task.task_id)
        self.assertEqual(collision["checked"], 0)
        self.assertFalse(idle["active"])
        self.assertIsNone(idle["plan_id"])
        self.assertIsNone(idle["task_id"])
        self.assertEqual(idle["single_flight_collision_count"], 1)
        self.assertEqual(recovered_gateway.verification_count, 1)
        self.assertEqual(recovered_gateway.dispatch_count, 0)

    async def test_terminal_record_never_probes_later(self):
        _plan, task = await self._pending_addon_restart()
        self.clock.advance(hours=24, seconds=1)
        recovered_gateway = SelfRestartRecoveryGateway("process-two")
        recovered = self._recovered(recovered_gateway)
        await recovered.reconcile_execution_tasks(trigger="startup")

        for _ in range(20):
            await recovered.reconcile_operational_plans(trigger="periodic")
            self.clock.advance(minutes=15)

        persisted = recovered.get_execution_task(task.task_id)
        self.assertEqual(persisted["state"], "manual_review_required")
        self.assertEqual(recovered_gateway.verification_count, 0)
        self.assertEqual(recovered_gateway.dispatch_count, 0)


if __name__ == "__main__":
    unittest.main()
