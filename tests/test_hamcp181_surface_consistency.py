"""HAMCP-181 PR1: every public surface reports one set of dispatch facts.

The S16 staleness regression is first: before PR1, a verified F3 reload left
``plan.operational.dispatch`` at ``dispatched:false`` / ``attempt_count:0``
and ``operational.verification.status`` at ``not_run``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

import tests.test_f3_runtime_integration as integration  # noqa: E402

logging.disable(logging.CRITICAL)


class _OperationalSurfaceBase(integration._OperationalActivationBase):
    asyncSetUp = integration.F3OperationalActivationTests.asyncSetUp

    async def _verified_reload(self):
        created = await self.service.create_reload_plan(reload_target="automation")
        plan = await self._grant(created)
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(self.lifecycle.dispatch_count, 1)
        return plan, result

    def _audit_records(self):
        path = Path(self.temp.name) / "audit.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]


class S16OperationalBlockTests(_OperationalSurfaceBase):
    """§7 item 6: the stale legacy block on F3 plans is projected truthfully."""

    async def test_verified_reload_operational_dispatch_is_not_stale(self):
        plan, result = await self._verified_reload()
        public = self.service.get_plan(plan["plan_id"])
        public = public.get("plan", public)
        dispatch = public["operational"]["dispatch"]
        verification = public["operational"]["verification"]

        self.assertTrue(dispatch["dispatched"])
        self.assertEqual(dispatch["attempt_count"], 1)
        self.assertTrue(dispatch["provider_response_received"])
        self.assertEqual(dispatch["projection_source"], "f3_dispatch_facts")
        self.assertEqual(
            dispatch["dispatch_phase"], "sent_response_received"
        )
        self.assertEqual(dispatch["attempt_counts_basis"], "saved_dispatch_intent")
        self.assertEqual(verification["status"], "verified")
        self.assertEqual(
            verification["projection_source"], "execution_task.verification_summary"
        )
        # Every surface agrees with the execution-task projection.
        task = public["execution_task"]
        self.assertTrue(task["provider_dispatch_occurred"])
        self.assertEqual(task["dispatch_phase"], dispatch["dispatch_phase"])
        self.assertTrue(result["provider_dispatch_occurred"])
        self.assertEqual(result["dispatch_phase"], dispatch["dispatch_phase"])

    async def test_verified_reload_survives_restart_without_stale_block(self):
        plan, _ = await self._verified_reload()
        await self.runtime.recover_once("restart")
        public = self.service.get_plan(plan["plan_id"])
        public = public.get("plan", public)
        self.assertTrue(public["operational"]["dispatch"]["dispatched"])
        self.assertEqual(public["operational"]["verification"]["status"], "verified")

    async def test_stored_plan_operational_block_is_not_rewritten(self):
        # Display only: the stored legacy block keeps its persisted values.
        plan, _ = await self._verified_reload()
        stored = self.service._load(plan["plan_id"])
        self.assertFalse(stored.operational.dispatch.get("dispatched"))
        self.assertEqual(stored.operational.verification.status, "not_run")


if __name__ == "__main__":
    unittest.main()
