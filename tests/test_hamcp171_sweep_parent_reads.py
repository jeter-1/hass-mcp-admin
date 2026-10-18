"""HAMCP-171: the recovery sweep reads a parent task only when it needs it.

The 30 s historical sweep read every declaration's public task before looking
at the child. Live beta.16 evidence showed 239 of 239 parent reads per pass
were for children whose status alone decided the outcome (terminal children
with a durable dispatch intent and no recorded fault). The sweep now reads the
child first and reads the parent only for the two decisions that consult it:
orphan cleanup (no dispatch intent) and terminal fault projection.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
)
from tests import test_f3_orphan_child_recovery as orphan_suite  # noqa: E402


class SweepParentReadTests(ConfigurationPlanTestCase):
    _orphaned_task = orphan_suite.OrphanChildRecoveryTests._orphaned_task
    _claim_child = orphan_suite.OrphanChildRecoveryTests._claim_child
    _build_live_orphan = (
        orphan_suite.OrphanChildRecoveryTests._build_live_orphan
    )

    async def asyncSetUp(self):
        # Same runtime as the orphan recovery suite.
        await super().asyncSetUp()
        self._claims = {}
        self.runtime = orphan_suite.F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=orphan_suite._ExactFakeConfigurationGateway(
                self.gateway
            ),
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=orphan_suite._provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    @contextmanager
    def count_sweep_parent_reads(self):
        """Count public-task reads made inside the historical slice only."""
        reads: list[str] = []
        examined: list[int] = []
        inside = [False]
        repository = self.service.task_repository
        original_get = repository.get
        original_slice = self.runtime._reconcile_orphaned_children

        def get(task_id, *args, **kwargs):
            if inside[0]:
                reads.append(task_id)
            return original_get(task_id, *args, **kwargs)

        def historical_slice(*args, **kwargs):
            inside[0] = True
            try:
                result = original_slice(*args, **kwargs)
                examined.append(result["examined"])
                return result
            finally:
                inside[0] = False

        with patch.object(repository, "get", side_effect=get), patch.object(
            self.runtime, "_reconcile_orphaned_children",
            side_effect=historical_slice,
        ):
            yield reads, examined

    async def dispatched_terminal_task(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        applied = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        task = self.service.task_repository.get(applied["task_id"])
        declarations = self.runtime.children.declarations_for_task(
            task.task_id
        )
        for item in declarations:
            record = self.runtime.children.get(item["child_id"])
            self.assertTrue(record.terminal)
            self.assertIsNotNone(record.dispatch_intent)
        return task, declarations

    async def test_terminal_dispatched_children_read_no_parent(self):
        task, declarations = await self.dispatched_terminal_task()
        before = {
            item["child_id"]: self.runtime.children.get(item["child_id"])
            for item in declarations
        }
        with self.count_sweep_parent_reads() as (reads, examined):
            for _ in range(2):
                await self.runtime.recover_once("periodic")
        self.assertTrue(examined and sum(examined) >= len(declarations))
        self.assertEqual([], reads)
        for child_id, record in before.items():
            self.assertEqual(record, self.runtime.children.get(child_id))

    async def test_orphan_cleanup_still_reads_parent_and_converges(self):
        task, declarations = await self._build_live_orphan()
        with self.count_sweep_parent_reads() as (reads, _examined):
            result = await self.runtime.recover_once("periodic")
        self.assertIn(task.task_id, reads)
        self.assertEqual(1, result["orphaned_children_terminalized"])
        claimed = self.runtime.children.get(declarations[0]["child_id"])
        self.assertTrue(claimed.terminal)
        self.assertEqual("cancelled_pre_dispatch", claimed.normalized_outcome)

    async def test_terminal_fault_still_reads_parent_and_clears(self):
        task, declarations = await self.dispatched_terminal_task()
        fault_key = "recovery_child:" + declarations[0]["child_id"]
        self.runtime._readiness_fault(fault_key)
        with self.runtime._readiness_lock:
            self.assertIn(fault_key, self.runtime._readiness_faults)
        with self.count_sweep_parent_reads() as (reads, _examined):
            await self.runtime.recover_once("periodic")
        self.assertIn(task.task_id, reads)
        with self.runtime._readiness_lock:
            self.assertNotIn(fault_key, self.runtime._readiness_faults)

    async def test_unread_corrupt_terminal_parent_is_still_surfaced(self):
        # The sweep no longer reads (and so no longer trips over) a terminal
        # parent it does not need. Corruption stays visible to health and to
        # every authoritative read; nothing is dispatched or redispatched.
        task, declarations = await self.dispatched_terminal_task()
        path = self.service.task_repository._path(task.task_id, plan_id=task.plan_id)
        self.assertTrue(path.exists())
        path.write_text("{")
        calls_before = len(self.gateway.calls)
        with self.count_sweep_parent_reads() as (reads, _examined):
            await self.runtime.recover_once("periodic")
        self.assertEqual([], reads)
        self.assertEqual(calls_before, len(self.gateway.calls))
        with self.assertRaises(GovernanceError) as error:
            await self.service.async_health_summary()
        self.assertEqual(
            error.exception.code, ErrorCode.EXECUTION_TASK_STORAGE_ERROR
        )
        self.assertNotEqual(
            error.exception.details.get("reason"), "health_snapshot_superseded"
        )


if __name__ == "__main__":
    unittest.main()
