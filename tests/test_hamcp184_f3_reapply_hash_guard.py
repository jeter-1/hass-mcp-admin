"""HAMCP-184 F-01: F3 re-apply validates the caller's expected plan hash.

The first F3 apply refuses an empty or mismatched ``expected_plan_hash`` inside
initialization, and the pre-Beta-20 legacy path refuses it before any terminal
or duplicate handling. A re-apply of an already-initialized F3 task must carry
the same caller-intent guard: refuse with ``approval_hash_mismatch`` before any
dispatch, task event, plan change or audit of execution progress. A matching
same-ID replay keeps today's behavior (same task, no redispatch).
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
)
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
)
from tests.test_f3_runtime_integration import (  # noqa: E402
    _ExactFakeConfigurationGateway,
    _provider_identity,
)

WRONG_HASHES = ("", "0" * 64)


class F3ReapplyHashGuardTests(ConfigurationPlanTestCase):
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

    def _writes(self) -> int:
        return sum(call[0] == "write" for call in self.gateway.calls)

    def _snapshot(self, plan_id: str) -> tuple:
        task = self.service.task_repository.get_for_plan(plan_id)
        plan = self.service._load(plan_id)
        declarations = self.runtime.children.declarations_for_task(task.task_id)
        children = tuple(
            None if record is None else (
                record.state, record.dispatch_count, len(record.events)
            )
            for record in (
                self.runtime.children.get(item["child_id"])
                for item in declarations
            )
        )
        return (
            task.task_id,
            task.state,
            len(task.events),
            plan.status,
            plan.execution_outcome,
            len(plan.events),
            plan.approval.state,
            children,
        )

    async def test_first_apply_refuses_wrong_hash_without_dispatch(self):
        # Baseline: the initialization path already refuses (unchanged).
        created = await self.create_automation_plan()
        await self.approve(created)
        for wrong in WRONG_HASHES:
            with self.subTest(expected_plan_hash=wrong):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.apply(created["plan_id"], wrong)
                self.assertEqual(
                    raised.exception.code, ErrorCode.APPROVAL_HASH_MISMATCH
                )
                self.assertEqual(self._writes(), 0)
                self.assertIsNone(
                    self.service.task_repository.get_for_plan(created["plan_id"])
                )

    async def test_reapply_of_terminal_task_refuses_mismatched_hash(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        first = await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(first["task_state"], "succeeded_verified")
        writes = self._writes()
        before = self._snapshot(created["plan_id"])
        audit_before = self.audit_path.read_text(encoding="utf-8")

        for wrong in WRONG_HASHES:
            with self.subTest(expected_plan_hash=wrong):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.apply(created["plan_id"], wrong)
                self.assertEqual(
                    raised.exception.code, ErrorCode.APPROVAL_HASH_MISMATCH
                )
                self.assertEqual(self._writes(), writes)
                self.assertEqual(self._snapshot(created["plan_id"]), before)
        audit_after = self.audit_path.read_text(encoding="utf-8")
        self.assertNotIn("duplicate_apply_prevented", audit_after[len(audit_before):])

        # A matching same-ID replay is unchanged: same task, no redispatch.
        replay = await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(replay["task_id"], first["task_id"])
        self.assertEqual(replay["task_state"], "succeeded_verified")
        self.assertEqual(self._writes(), writes)

    async def test_reapply_of_undispatched_task_refuses_mismatch_then_matching_hash_dispatches_once(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        original = F3RuntimeIntegration._execute_child
        interrupted = False

        async def interrupt_before_child(runtime, *args, **kwargs):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise RuntimeError("synthetic interruption before child intent")
            return await original(runtime, *args, **kwargs)

        with patch.object(
            F3RuntimeIntegration, "_execute_child", interrupt_before_child
        ):
            with self.assertRaises(RuntimeError):
                await self.service.apply(created["plan_id"], created["plan_hash"])
            task = self.service.task_repository.get_for_plan(created["plan_id"])
            self.assertIsNotNone(task)
            declarations = self.runtime.children.declarations_for_task(task.task_id)
            self.assertEqual(len(declarations), 1)
            self.assertIsNone(self.runtime.children.get(declarations[0]["child_id"]))
            self.assertEqual(self._writes(), 0)
            before = self._snapshot(created["plan_id"])

            for wrong in WRONG_HASHES:
                with self.subTest(expected_plan_hash=wrong):
                    with self.assertRaises(GovernanceError) as raised:
                        await self.service.apply(created["plan_id"], wrong)
                    self.assertEqual(
                        raised.exception.code, ErrorCode.APPROVAL_HASH_MISMATCH
                    )
                    self.assertEqual(self._writes(), 0)
                    self.assertEqual(self._snapshot(created["plan_id"]), before)
                    self.assertIsNone(
                        self.runtime.children.get(declarations[0]["child_id"])
                    )

            resumed = await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )
        self.assertEqual(resumed["task_id"], task.task_id)
        self.assertEqual(resumed["task_state"], "succeeded_verified")
        self.assertEqual(self._writes(), 1)
        self.assertEqual(
            self.runtime.children.get(declarations[0]["child_id"]).dispatch_count,
            1,
        )


if __name__ == "__main__":
    unittest.main()
