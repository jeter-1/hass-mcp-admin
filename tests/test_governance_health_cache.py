"""Health cache behavior using current-writer records and offline providers."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests import test_governance as governance_fixtures
from tests import test_f2_policy_approval as approval_fixtures
from tests import test_beta11_restart_reconciliation as restart_fixtures
from tests import test_f3_runtime_integration as f3_fixtures
from tests.test_dev14_configuration_plans import PROPOSED_AUTOMATION
from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from ha_mcp_engineering.governance.models import ApprovalState, PlanStatus
from ha_mcp_engineering.governance.storage import ChangePlanRepository, ChangePlanStorageError
from ha_mcp_engineering.governance.task_storage import ExecutionTaskStorageError


class GovernanceHealthCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = governance_fixtures.GovernanceTestCase()
        await self.fixture.asyncSetUp()
        self.service = self.fixture.service
        self.repository = self.fixture.repository

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()

    async def test_new_plan_and_approval_changes_rebuild_then_hits_are_stable(self):
        self.assertEqual(self.service.health_summary()["total_plans"], 0)
        created = await self.fixture.update_plan()
        first = self.service.health_summary()
        self.assertEqual(first["total_plans"], 1)
        self.assertEqual(first["plans_requiring_approval"], 1)
        self.assertGreater(first["plan_store_scaling"]["hot_paths"]["governance_health"]["plan_records_deserialized"], 0)
        for _ in range(2):
            warm = self.service.health_summary()
            self.assertEqual(warm["plans_requiring_approval"], 1)
            self.assertEqual(warm["plan_store_scaling"]["hot_paths"]["governance_health"]["plan_records_deserialized"], 0)
        await self.fixture.externally_approve(created["plan_id"], created["plan_hash"])
        approved = self.service.health_summary()
        self.assertEqual(approved["externally_approved_plans"], 1)
        self.assertEqual(approved["plans_requiring_approval"], 0)
        self.assertEqual(self.fixture.gateway.write_calls, 0)

    async def test_plan_expiry_rebuilds_at_boundary_and_is_persisted_once(self):
        created = await self.fixture.update_plan(expiration_minutes=5)
        self.service.health_summary()
        self.fixture.clock.value = datetime.fromisoformat(created["expires_at"]) - timedelta(microseconds=1)
        self.assertEqual(self.service.health_summary()["expired_plans"], 0)
        self.fixture.clock.advance(microseconds=1)
        expired = self.service.health_summary()
        self.assertEqual(expired["expired_plans"], 1)
        self.assertEqual(expired["plans_requiring_approval"], 0)
        path = self.repository._path(created["plan_id"])
        persisted = path.read_bytes()
        self.service.health_summary()
        self.assertEqual(path.read_bytes(), persisted)
        self.assertEqual(self.repository.get(created["plan_id"]).status, PlanStatus.EXPIRED)
        self.assertEqual(self.fixture.gateway.write_calls, 0)

    async def test_pending_challenge_expiry_is_not_frozen(self):
        created = await self.fixture.update_plan(expiration_minutes=120)
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertEqual(self.service.health_summary()["pending_challenge_count"], 1)
        self.fixture.clock.value = datetime.fromisoformat(pending["challenge_expires_at"])
        health = self.service.health_summary()
        self.assertEqual(health["pending_challenge_count"], 0)
        self.assertEqual(health["pending_plan_approvals"], 0)
        self.assertEqual(health["expired_challenge_count"], 1)
        self.assertEqual(self.repository.get(created["plan_id"]).approval.state, ApprovalState.EXPIRED)
        self.assertEqual(self.service.health_summary()["expired_challenge_count"], 1)

    async def test_backward_clock_invalidates_instead_of_extending_reuse(self):
        await self.fixture.update_plan()
        self.service.health_summary()
        self.fixture.clock.advance(seconds=-1)
        health = self.service.health_summary()
        self.assertGreater(health["plan_store_scaling"]["hot_paths"]["governance_health"]["plan_records_deserialized"], 0)

    async def test_live_locks_and_provider_state_refresh_without_history(self):
        await self.fixture.update_plan()
        provider = {"provider": "synthetic", "operational_status": "available", "fallback": "none"}
        self.service.lifecycle_gateway = SimpleNamespace(health_snapshot=lambda: dict(provider))
        f3_state = {"status": "ready", "active_locks": 0, "recovery_failure_count": 0}
        f3_health = Mock(side_effect=lambda: dict(f3_state))
        self.service.f3_runtime = SimpleNamespace(health=f3_health)
        self.service.health_summary()
        f3_health.assert_called_once()
        f3_health.reset_mock()
        lock = asyncio.Lock()
        self.service._target_locks[("operational_lifecycle", "synthetic")] = lock
        await lock.acquire()
        try:
            self.service._active_lifecycle_reconciliations = 1
            f3_state.update(status="not_ready", active_locks=1, recovery_failure_count=1)
            provider["operational_status"] = "unavailable"
            self.service.task_repository.event_write_failures += 1
            self.service.task_repository.materialization_failures += 1
            health = self.service.health_summary()
            self.assertEqual(health["active_apply_operations"], 1)
            self.assertEqual(health["operational_administration"]["active_operational_applies"], 1)
            self.assertEqual(health["operational_administration"]["operations"]["restart_home_assistant"]["active_reconciliations"], 1)
            self.assertEqual(health["operational_administration"]["lifecycle_provider"]["operational_status"], "unavailable")
            self.assertEqual(health["execution_tasks"]["event_write_failures"], 1)
            self.assertEqual(health["execution_tasks"]["materialization_failures"], 1)
            self.assertEqual(health["execution_tasks"]["storage_status"], "degraded")
            self.assertEqual(health["f3"], f3_state)
            self.assertEqual(health["plan_store_scaling"]["hot_paths"]["governance_health"]["plan_records_deserialized"], 0)
            f3_health.assert_called_once()
        finally:
            lock.release()
        self.assertEqual(self.service.health_summary()["active_apply_operations"], 0)

    async def test_f3_failure_is_not_hidden_by_cached_ready_health(self):
        f3_health = Mock(return_value={"status": "ready"})
        self.service.f3_runtime = SimpleNamespace(health=f3_health)
        self.service.health_summary()
        f3_health.side_effect = RuntimeError("synthetic failure")
        with self.assertRaises(RuntimeError):
            self.service.health_summary()

    async def test_returned_data_is_independent_of_cached_data(self):
        health = self.service.health_summary()
        health["execution_tasks"]["verified_successes"] = 999
        health["operational_administration"]["operations"].clear()
        next_health = self.service.health_summary()
        self.assertEqual(next_health["execution_tasks"]["verified_successes"], 0)
        self.assertTrue(next_health["operational_administration"]["operations"])

    async def test_supported_external_replacement_invalidates(self):
        created = await self.fixture.update_plan()
        self.service.health_summary()
        other_repository = ChangePlanRepository(self.repository.root)
        other_service = governance_fixtures.ChangeGovernanceService(
            other_repository, self.fixture.gateway, now=self.fixture.clock
        )
        pending = other_service.approve(created["plan_id"], created["plan_hash"])
        _, csrf = await other_service.issue_external_csrf(created["plan_id"], pending["challenge_id"])
        await other_service.decide_external_approval(
            plan_id=created["plan_id"], challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"], approval_kind="apply",
            csrf_nonce=csrf, decision="reject",
            approver_principal="home_assistant_admin_ingress:synthetic-reviewer",
        )
        health = self.service.health_summary()
        self.assertEqual(health["rejected_plans"], 1)
        self.assertEqual(health["plans_requiring_approval"], 0)
        self.assertGreater(health["plan_store_scaling"]["hot_paths"]["governance_health"]["plan_records_deserialized"], 0)

    async def test_detected_corruption_cannot_return_cached_healthy_count(self):
        created = await self.fixture.update_plan()
        self.service.health_summary()
        path = self.repository._path(created["plan_id"])
        replacement = path.with_suffix(".replacement")
        replacement.write_text("not json", encoding="utf-8")
        replacement.replace(path)
        health = self.service.health_summary()
        self.assertEqual(health["total_plans"], 0)
        # Repository status describes access; historical corruption is a
        # separate persisted observation and must remain visible after reuse.
        self.assertGreater(health["storage_corruption_count"], 0)
        self.assertEqual(self.service.health_summary()["storage_corruption_count"], health["storage_corruption_count"])

    async def test_plan_build_failure_leaves_cache_unusable(self):
        await self.fixture.update_plan()
        self.service.health_summary()
        self.repository.rebuild_navigation_index()
        with patch.object(self.repository, "list", side_effect=ChangePlanStorageError("synthetic")):
            for _ in range(2):
                with self.assertRaises(GovernanceError) as error:
                    self.service.health_summary()
                self.assertEqual(error.exception.code, ErrorCode.CHANGE_PLAN_STORAGE_ERROR)
        self.assertEqual(self.service.health_summary()["total_plans"], 1)

    async def test_failed_task_history_is_visible_and_not_cached(self):
        self.service.health_summary()
        self.service.task_repository.rebuild_navigation_index()
        with patch.object(self.service.task_repository, "list", side_effect=ExecutionTaskStorageError("synthetic")) as listing:
            for _ in range(2):
                health = self.service.health_summary()
                self.assertEqual(health["execution_tasks"]["storage_status"], "error")
            self.assertEqual(listing.call_count, 2)
        self.assertEqual(self.service.health_summary()["execution_tasks"]["storage_status"], "healthy")

    async def test_generation_change_during_build_is_not_tagged_current(self):
        created = await self.fixture.update_plan()
        health_method = self.repository.health
        def change_after_snapshot():
            plan = self.repository.get(created["plan_id"])
            plan.status = PlanStatus.REJECTED
            self.repository.save(plan)
            return health_method()
        with patch.object(self.repository, "health", side_effect=change_after_snapshot):
            with self.assertRaises(GovernanceError) as error:
                self.service.health_summary()
        self.assertEqual(error.exception.code, ErrorCode.CHANGE_PLAN_STORAGE_ERROR)
        self.assertEqual(self.service.health_summary()["rejected_plans"], 1)

    async def test_task_generation_change_during_assembly_is_rejected(self):
        await self.fixture.update_plan()
        actionability = self.service._approval_is_actionable
        changed = False

        def change_during_projection(plan):
            nonlocal changed
            if not changed:
                changed = True
                self.service.task_repository.rebuild_navigation_index()
            return actionability(plan)

        with patch.object(self.service, "_approval_is_actionable", side_effect=change_during_projection):
            with self.assertRaises(GovernanceError) as error:
                self.service.health_summary()
        self.assertEqual(error.exception.code, ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        self.assertEqual(self.service.health_summary()["total_plans"], 1)

    async def test_projection_only_rebuild_cannot_retag_old_health(self):
        created = await self.fixture.update_plan()
        self.service.health_summary()
        plan = self.repository.get(created["plan_id"])
        plan.status = PlanStatus.REJECTED
        self.repository.save(plan)
        self.service._rebuild_projection_failure_index(invalidate_health=False)
        self.assertEqual(self.service.health_summary()["rejected_plans"], 1)


class ElevatedHealthCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_elevated_ack_deadline_expires_once_without_dispatch(self):
        fixture = approval_fixtures.F2ApprovalTestCase()
        await fixture.asyncSetUp()
        try:
            created = await fixture.create_elevated()
            pending = fixture.service.approve(created["plan_id"], created["plan_hash"])
            acknowledgement = await fixture.decide(created, pending)
            self.assertEqual(acknowledgement["approval_action"], "elevated_risk_acknowledgement")
            self.assertEqual(fixture.service.health_summary()["pending_elevated_acknowledgements"], 1)
            fixture.clock.value = datetime.fromisoformat(acknowledgement["challenge_expires_at"])
            health = fixture.service.health_summary()
            self.assertEqual(health["pending_elevated_acknowledgements"], 0)
            self.assertEqual(health["pending_challenge_count"], 0)
            self.assertEqual(health["expired_challenge_count"], 1)
            self.assertEqual(fixture.service.health_summary()["expired_challenge_count"], 1)
            self.assertEqual(fixture.gateway.writes, 0)
        finally:
            await fixture.asyncTearDown()


class RestartHealthCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_backoff_deadline_and_provider_presence_refresh_without_dispatch(self):
        fixture = restart_fixtures.Beta11RestartReconciliationTests()
        await fixture.asyncSetUp()
        try:
            _, task = await fixture._pending_addon_restart()
            service = fixture._recovered()
            await service.reconcile_operational_plans(trigger="startup")
            health = service.health_summary()
            self.assertEqual(health["restart_reconciliation"]["pending_backoff_record_count"], 1)
            persisted = service.task_repository.get(task.task_id)
            next_at = persisted.verification_summary["restart_reconciliation"]["next_attempt_at"]
            fixture.clock.value = datetime.fromisoformat(next_at)
            health = service.health_summary()
            self.assertEqual(health["restart_reconciliation"]["pending_eligible_record_count"], 1)
            self.assertEqual(health["restart_reconciliation"]["pending_backoff_record_count"], 0)
            gateway = service.lifecycle_gateway
            service.lifecycle_gateway = None
            self.assertEqual(service.health_summary()["restart_reconciliation"]["pending_eligible_record_count"], 0)
            service.lifecycle_gateway = gateway
            self.assertEqual(service.health_summary()["restart_reconciliation"]["pending_eligible_record_count"], 1)
            fixture.clock.value = datetime.fromisoformat(task.maximum_post_dispatch_deadline)
            self.assertEqual(service.health_summary()["restart_reconciliation"]["pending_eligible_record_count"], 0)
            self.assertEqual(gateway.dispatch_count, 0)
        finally:
            await fixture.asyncTearDown()


class F3HealthCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_writer_terminal_history_hits_and_duplicate_is_not_dispatched(self):
        fixture = f3_fixtures.F3ConfigurationActivationTests()
        await fixture.asyncSetUp()
        try:
            for index in range(6):
                config = copy.deepcopy(PROPOSED_AUTOMATION)
                config["description"] = f"Synthetic cache fixture {index}"
                created = await fixture.service.create_configuration_plan(
                    title="Cache test", description="Synthetic exact current-writer history",
                    operations=[{"operation_id": "update", "resource_type": "automation", "action": "update", "target_id": "apply_hvac_comfort", "depends_on": [], "proposed_config": config}],
                )
                await fixture.approve(created)
                applied = await fixture.service.apply(created["plan_id"], created["plan_hash"])
                self.assertEqual(applied["task_state"], "succeeded_verified")
            service = fixture.service
            with patch.object(fixture.runtime, "health", wraps=fixture.runtime.health) as health_call:
                first = service.health_summary()
                self.assertEqual(health_call.call_count, 1)
                self.assertEqual(first["execution_tasks"]["verified_successes"], 6)
                for _ in range(2):
                    health_call.reset_mock()
                    warm = service.health_summary()
                    self.assertEqual(health_call.call_count, 1)
                    self.assertEqual(warm["execution_tasks"]["verified_successes"], 6)
                    self.assertEqual(warm["plan_store_scaling"]["hot_paths"]["governance_health"]["terminal_plan_records_deserialized"], 0)
                    self.assertEqual(warm["f3"]["status"], "ready")
            before_calls = list(fixture.gateway.calls)
            before = service.health_summary()["execution_tasks"]["event_count"]
            await service.apply(created["plan_id"], created["plan_hash"])
            self.assertFalse(any(call[0] == "write" for call in fixture.gateway.calls[len(before_calls):]))
            after = service.health_summary()
            self.assertGreater(after["execution_tasks"]["event_count"], before)
            self.assertEqual(after["execution_tasks"]["verified_successes"], 6)
            self.assertGreater(after["execution_tasks"]["no_blind_redispatch_preventions"], 0)
        finally:
            await fixture.asyncTearDown()
