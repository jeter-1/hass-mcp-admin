"""Public verification authority follows persisted F3 tasks without mutation."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.tools import governance
from tests import test_beta37_exact_helper_state as helpers
from tests import test_f3_runtime_integration as operational
from tests import test_2_1a_beta2_operational_lifecycle as legacy


class AttributionAssertions:
    async def assert_attribution(self, plan_id, task_id, state, provider):
        task = self.service.get_execution_task(task_id)
        self.assertEqual(task["state"], state)
        plan_before = self.service._load(plan_id).to_dict()
        task_before = self.service._load_task(task_id).to_dict()
        dispatches = provider.dispatch_count
        with patch.object(governance.GOVERNANCE, "service", self.service), patch.object(
            governance, "SETTINGS", SimpleNamespace(response_size_limit=60000)
        ):
            for _ in range(2):
                plan_response = json.loads(await governance.get_change_plan(plan_id))
                task_response = json.loads(await governance.get_execution_task(task_id))
                self.assertTrue(plan_response["success"])
                self.assertTrue(task_response["success"])
                public = plan_response["data"]
                pointer = public["authoritative_verification_field"]
                self.assertEqual(pointer, "execution_task.verification_summary")
                selected = public
                for key in pointer.split("."):
                    selected = selected[key]
                self.assertEqual(selected, task_response["data"]["verification_summary"])
                self.assertEqual(public["execution_task"]["task_state"], state)
                self.assertEqual(public["operational"]["verification"], plan_before["operational"]["verification"])
                compact = self.service._compact_plan_observability_core(public)
                selected = compact
                for key in compact["authoritative_verification_field"].split("."):
                    selected = selected[key]
                self.assertEqual(selected, task["verification_summary"])
        self.assertEqual(provider.dispatch_count, dispatches)
        self.assertEqual(self.service._load(plan_id).to_dict(), plan_before)
        self.assertEqual(self.service._load_task(task_id).to_dict(), task_before)
        self.assertEqual(self.service.get_execution_task(task_id), task)


class HelperVerificationTests(AttributionAssertions, unittest.IsolatedAsyncioTestCase):
    asyncSetUp = helpers.ExactHelperStateRuntimeTests.asyncSetUp
    asyncTearDown = helpers.ExactHelperStateRuntimeTests.asyncTearDown
    grant = helpers.ExactHelperStateRuntimeTests.grant
    create_and_grant = helpers.ExactHelperStateRuntimeTests.create_and_grant

    async def test_verified_helper_uses_actual_task_authority(self):
        plan = await self.create_and_grant("on")
        applied = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        await self.assert_attribution(plan["plan_id"], applied["task_id"], "succeeded_verified", self.helper)
        self.assertEqual(self.helper.dispatch_count, 1)
        task = self.service.get_execution_task(applied["task_id"])
        self.assertEqual(task["verification_summary"]["status"], "verified")
        self.assertEqual(task["f3_children"][0]["dispatch_count"], 1)
        self.assertEqual(self.service._load(plan["plan_id"]).operational.verification.status, "not_run")


class TaskVerificationTests(AttributionAssertions, unittest.IsolatedAsyncioTestCase):
    asyncSetUp = operational.F3OperationalActivationTests.asyncSetUp
    asyncTearDown = operational.F3OperationalActivationTests.asyncTearDown
    _grant = operational.F3OperationalActivationTests._grant

    async def create(self):
        return await self._grant(await self.service.create_reload_plan(reload_target="automation"))

    async def test_created_task_does_not_invent_verification(self):
        plan = await self.create()
        task, _, _ = await self.runtime._initialize(self.service._load(plan["plan_id"]), plan["plan_hash"])
        await self.assert_attribution(plan["plan_id"], task.task_id, "created", self.lifecycle)
        self.assertEqual(self.lifecycle.dispatch_count, 0)

    async def test_observing_task_retains_pending_evidence(self):
        self.lifecycle.mode = "ambiguous"
        plan = await self.create()
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        await self.assert_attribution(plan["plan_id"], result["task_id"], "observing", self.lifecycle)
        self.assertEqual(self.lifecycle.dispatch_count, 1)

    async def test_failed_pre_dispatch_task_does_not_claim_dispatch(self):
        plan = await self.create()
        self.lifecycle.mode = "pre_dispatch_failure"
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        await self.assert_attribution(plan["plan_id"], result["task_id"], "failed_pre_dispatch", self.lifecycle)
        self.assertEqual(self.lifecycle.dispatch_count, 0)

    async def test_manual_review_preserves_causal_parent_and_children(self):
        self.lifecycle.mode = "ambiguous"
        plan = await self.create()
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.clock.advance(seconds=901)
        await self.runtime.recover_once("synthetic_attribution_expiry")
        await self.assert_attribution(plan["plan_id"], result["task_id"], "manual_review_required", self.lifecycle)
        self.assertEqual(self.lifecycle.dispatch_count, 1)


class LegacyVerificationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = legacy.GovernedOperationalLifecycleTests.asyncSetUp
    asyncTearDown = legacy.GovernedOperationalLifecycleTests.asyncTearDown

    async def test_taskless_legacy_plan_retains_operational_authority(self):
        result = await self.service.create_reload_plan(reload_target="automation")
        plan = self.service.get_plan(result["plan"]["plan_id"])
        self.assertEqual(plan["authoritative_verification_field"], "operational.verification")
        self.assertIsNone(plan["execution_task"]["task_id"])
        self.assertNotIn("verification_summary", plan["execution_task"])
        self.assertEqual(plan["operational"]["verification"]["status"], "not_run")
