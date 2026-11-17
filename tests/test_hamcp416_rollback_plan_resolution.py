"""HAMCP-416 (HAAC-E044): rollback_change replans when its rollback is terminal.

A rollback plan already linked to the source is returned only while it is
live: it can still be approved or applied, or its execution task has not
settled. A terminal one (expired, applied, rejected or otherwise final) is
history, so the request plans again from fresh evidence, exactly as a first
request does, and links the new plan. Every result names which happened. No
request writes Home Assistant; all stores and peers are synthetic.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
    ROLLBACK_RESOLUTION_EXISTING_LIVE,
    ROLLBACK_RESOLUTION_FRESH,
)
from ha_mcp_engineering.governance.models import PlanStatus  # noqa: E402
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
from tests.test_hamcp189_rollback_core_lease import LeaseCore  # noqa: E402


class RollbackPlanResolutionTests(ConfigurationPlanTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=_ExactFakeConfigurationGateway(self.gateway),
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=_provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")
        self.runtime.core_runtime = LeaseCore()

    async def applied(self, created):
        # Applies run without Core, as in HAMCP-189, so these tests isolate
        # rollback planning; apply-time Core is covered elsewhere.
        core, self.runtime.core_runtime = self.runtime.core_runtime, None
        try:
            await self.approve(created)
            result = await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )
        finally:
            self.runtime.core_runtime = core
        self.assertEqual(result["task_state"], "succeeded_verified")
        self.gateway.calls.clear()
        return created

    def reads(self):
        return [call for call in self.gateway.calls if call[0] == "read"]

    def writes(self):
        return [call for call in self.gateway.calls if call[0] == "write"]

    async def first_rollback(self):
        source = await self.applied(await self.create_automation_plan())
        first = await self.service.rollback_change(
            source["plan_id"], source["plan_hash"]
        )
        self.assertEqual(first["status"], "rollback_plan_created")
        self.assertEqual(first["rollback_plan_resolution"], ROLLBACK_RESOLUTION_FRESH)
        self.assertNotIn("replaced_rollback_plan_id", first)
        self.gateway.calls.clear()
        return source, first

    def expire(self, plan_id):
        plan = self.repository.get(plan_id)
        plan.expires_at = (self.service.now() - timedelta(minutes=1)).isoformat()
        self.repository.save(plan)

    async def test_live_rollback_plan_is_returned_without_reads(self):
        source, first = await self.first_rollback()
        again = await self.service.rollback_change(
            source["plan_id"], source["plan_hash"]
        )
        self.assertEqual(again["rollback_plan_id"], first["rollback_plan_id"])
        self.assertEqual(again["plan_hash"], first["plan_hash"])
        self.assertEqual(
            again["rollback_plan_resolution"], ROLLBACK_RESOLUTION_EXISTING_LIVE
        )
        self.assertIs(again["approval_required"], True)
        self.assertEqual(self.reads(), [])
        self.assertEqual(self.writes(), [])

    async def test_expired_rollback_plan_is_replanned_from_fresh_evidence(self):
        source, first = await self.first_rollback()
        self.expire(first["rollback_plan_id"])
        again = await self.service.rollback_change(
            source["plan_id"], source["plan_hash"]
        )
        self.assertEqual(again["status"], "rollback_plan_created")
        self.assertEqual(again["rollback_plan_resolution"], ROLLBACK_RESOLUTION_FRESH)
        self.assertNotEqual(again["rollback_plan_id"], first["rollback_plan_id"])
        self.assertEqual(again["replaced_rollback_plan_id"], first["rollback_plan_id"])
        self.assertEqual(again["replaced_rollback_plan_status"], "expired")
        self.assertIs(again["approval_required"], True)
        self.assertIs(again["provider_dispatch_occurred"], False)
        # Fresh evidence: current state was read again; nothing was written.
        self.assertTrue(self.reads())
        self.assertEqual(self.writes(), [])
        self.assertEqual(
            self.repository.get(source["plan_id"]).rollback.request_id,
            again["rollback_plan_id"],
        )
        fresh = self.repository.get(again["rollback_plan_id"])
        self.assertEqual(fresh.status, PlanStatus.AWAITING_APPROVAL)
        # The fresh plan is live, so a further request returns it.
        self.gateway.calls.clear()
        third = await self.service.rollback_change(
            source["plan_id"], source["plan_hash"]
        )
        self.assertEqual(third["rollback_plan_id"], again["rollback_plan_id"])
        self.assertEqual(
            third["rollback_plan_resolution"], ROLLBACK_RESOLUTION_EXISTING_LIVE
        )
        self.assertEqual(self.reads(), [])

    async def test_applied_rollback_is_replanned_and_fresh_evidence_refuses(self):
        source, first = await self.first_rollback()
        await self.applied(
            {"plan_id": first["rollback_plan_id"], "plan_hash": first["plan_hash"]}
        )
        writes_before = len(self.writes())
        # Before HAMCP-416 this returned the applied plan as
        # rollback_plan_created. The prior configuration is restored, so the
        # fresh evidence no longer matches the forward result: nothing to plan.
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(source["plan_id"], source["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertTrue(self.reads())
        self.assertEqual(len(self.writes()), writes_before)
        # The source keeps its link to the applied rollback; no plan was made.
        self.assertEqual(
            self.repository.get(source["plan_id"]).rollback.request_id,
            first["rollback_plan_id"],
        )

    async def test_unsettled_rollback_task_is_live_whatever_the_plan_status(self):
        # Terminal by status, but its execution task has not settled: the
        # rollback may be dispatching, or its outcome awaits manual review, so
        # it is never planned a second time.
        for state in (
            ExecutionTaskState.DISPATCHING,
            ExecutionTaskState.MANUAL_REVIEW_REQUIRED,
        ):
            with self.subTest(state=state.value):
                source, first = await self.first_rollback()
                self.expire(first["rollback_plan_id"])
                repository = self.service.task_repository
                original = repository.get_for_plan

                def unsettled(plan_id, first=first, original=original, state=state):
                    if plan_id == first["rollback_plan_id"]:
                        return SimpleNamespace(state=state)
                    return original(plan_id)

                repository.get_for_plan = unsettled
                try:
                    again = await self.service.rollback_change(
                        source["plan_id"], source["plan_hash"]
                    )
                finally:
                    repository.get_for_plan = original
                self.assertEqual(again["rollback_plan_id"], first["rollback_plan_id"])
                self.assertEqual(
                    again["rollback_plan_resolution"],
                    ROLLBACK_RESOLUTION_EXISTING_LIVE,
                )
                self.assertEqual(self.reads(), [])
                self.assertEqual(self.writes(), [])

    async def test_terminal_rollback_under_degraded_core_refuses_before_any_read(self):
        source, first = await self.first_rollback()
        self.expire(first["rollback_plan_id"])
        core = LeaseCore(admit=False)
        self.runtime.core_runtime = core
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(source["plan_id"], source["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(self.reads(), [])
        self.assertEqual(self.writes(), [])
        self.assertEqual(
            self.repository.get(source["plan_id"]).rollback.request_id,
            first["rollback_plan_id"],
        )
        self.assertEqual(core.outstanding, 0)

    async def test_expiry_check_does_not_persist_the_old_plan(self):
        source, first = await self.first_rollback()
        plan = self.repository.get(first["rollback_plan_id"])
        self.assertTrue(self.runtime._rollback_plan_live(plan))
        self.expire(first["rollback_plan_id"])
        expired = self.repository.get(first["rollback_plan_id"])
        self.assertFalse(self.runtime._rollback_plan_live(expired))
        # The liveness check projects the clock; it does not save.
        self.assertEqual(
            self.repository.get(first["rollback_plan_id"]).status,
            PlanStatus.AWAITING_APPROVAL,
        )


if __name__ == "__main__":
    unittest.main()
