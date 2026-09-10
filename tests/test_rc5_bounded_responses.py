"""Valid bounded receipts preserve completed action and reconciliation facts."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.models.responses import MAX_CHARS, dump_json
from ha_mcp_engineering.tool_framework import run_structured
from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from tests import test_beta37_exact_helper_state as helper_fixtures


class BoundedResponseTests(unittest.IsolatedAsyncioTestCase):
    def test_small_response_is_byte_identical(self):
        value = {"success": True, "data": {"value": "snowman ☃"}}
        self.assertEqual(dump_json(value), json.dumps(value, indent=2))

    def test_boundary_and_unicode_are_valid_within_configured_bound(self):
        for limit in (1024, 6000, MAX_CHARS):
            for text in ("x", "雪😀"):
                with self.subTest(limit=limit, text=text):
                    value = {"operation": "read", "request_id": "synthetic-request",
                             "success": True, "data": {"payload": text * limit}}
                    original = deepcopy(value)
                    output = dump_json(value, limit=limit)
                    result = json.loads(output)
                    self.assertLessEqual(len(output), limit)
                    self.assertLessEqual(len(output.encode("utf-8")), limit)
                    self.assertTrue(result["success"])
                    self.assertEqual(result["request_id"], "synthetic-request")
                    self.assertTrue(result["response_completeness"]["truncated"])
                    self.assertEqual(value, original)

    def test_exact_pretty_boundary_and_one_less_preserve_full_values(self):
        value = {"data": "x" * 2048}
        size = len(json.dumps(value, indent=2))
        for limit in (size - 1, size, size + 1):
            with self.subTest(limit=limit):
                output = dump_json(value, limit=limit)
                self.assertEqual(json.loads(output), value)
                self.assertLessEqual(len(output), limit)

    async def test_completed_action_oversize_preserves_receipt_without_repeat(self):
        effects = []
        value = {
            "status": "applied", "task_id": "synthetic-task",
            "task_state": "succeeded_verified", "provider_dispatch_occurred": True,
            "plan": {"plan_id": "synthetic-plan", "plan_hash": "a" * 64,
                     "status": "applied", "operational": {
                         "provider": "direct_home_assistant_state",
                         "verification": {"status": "verified"},
                         "baseline": {"disclosure": "x" * (MAX_CHARS * 2)}}},
        }

        async def action():
            effects.append("persisted_completed_action")
            return value

        output = await run_structured("apply_change_plan", "Applied", action)
        receipt = json.loads(output)
        self.assertEqual(effects, ["persisted_completed_action"])
        self.assertLessEqual(len(output), MAX_CHARS)
        self.assertTrue(receipt["success"])
        self.assertEqual(receipt["data"]["task_id"], "synthetic-task")
        self.assertEqual(receipt["data"]["task_state"], "succeeded_verified")
        self.assertTrue(receipt["data"]["provider_dispatch_occurred"])
        self.assertEqual(receipt["data"]["plan"]["plan_hash"], "a" * 64)
        self.assertEqual(receipt["data"]["plan"]["operational"]["verification"]["status"], "verified")
        completeness = receipt["response_completeness"]
        self.assertFalse(completeness["approval_disclosures_complete"])
        self.assertEqual({r["tool"] for r in completeness["retrieval"]},
                         {"get_execution_task", "get_change_plan"})

    async def test_large_failure_preserves_failure_and_reconciliation_ids(self):
        calls = []

        def action():
            calls.append("one")
            raise GovernanceError(ErrorCode.EXECUTION_TASK_STORAGE_ERROR, details={
                "task_id": "synthetic-task", "plan_id": "synthetic-plan",
                "plan_hash": "b" * 64, "provider_dispatch_occurred": False,
                "diagnostic": "x" * (MAX_CHARS * 2),
            })

        output = await run_structured("apply_change_plan", "Apply", action)
        receipt = json.loads(output)
        self.assertFalse(receipt["success"])
        self.assertEqual(receipt["error_code"], "execution_task_storage_error")
        self.assertEqual(receipt["details"]["task_id"], "synthetic-task")
        self.assertEqual(receipt["details"]["plan_hash"], "b" * 64)
        self.assertFalse(receipt["details"]["provider_dispatch_occurred"])
        self.assertEqual(calls, ["one"])
        self.assertLessEqual(len(output), MAX_CHARS)

    async def test_small_failure_remains_complete(self):
        def action():
            raise GovernanceError(ErrorCode.CHANGE_PLAN_NOT_FOUND)
        receipt = json.loads(await run_structured("get_change_plan", "Read", action))
        self.assertFalse(receipt["success"])
        self.assertEqual(receipt["error_code"], "change_plan_not_found")
        self.assertNotIn("response_completeness", receipt)


class MinimumApplyReceiptTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = helper_fixtures.ExactHelperStateRuntimeTests.asyncSetUp
    asyncTearDown = helper_fixtures.ExactHelperStateRuntimeTests.asyncTearDown
    grant = helper_fixtures.ExactHelperStateRuntimeTests.grant
    create_and_grant = helper_fixtures.ExactHelperStateRuntimeTests.create_and_grant

    async def test_maximum_request_identity_preserves_completed_apply_at_minimum_budget(self):
        from ha_mcp_engineering.models.responses import SuccessResponse

        plan = await self.create_and_grant("on")
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(self.helper.dispatch_count, 1)
        for request_id in ("receipt-review", "r" * 128):
            with self.subTest(request_id_length=len(request_id)):
                output = SuccessResponse(
                    "apply_change_plan", "Apply", result, request_id=request_id
                ).to_json(1024)
                receipt = json.loads(output)
                self.assertLessEqual(len(output.encode()), 1024)
                self.assertEqual(receipt["request_id"], request_id)
                self.assertTrue(receipt["success"])
                facts = receipt["data"]
                self.assertEqual(facts["task_id"], result["task_id"])
                self.assertEqual(facts["plan_hash"], plan["plan_hash"])
                self.assertEqual(facts["task_state"], "succeeded_verified")
                self.assertTrue(facts["provider_dispatch_occurred"])
                self.assertFalse(facts["redispatch_performed"])
                self.assertEqual(facts["task_verification_status"], "verified")
                self.assertEqual(facts["provider"], "direct_home_assistant_state")
                completeness = receipt["response_completeness"]
                self.assertFalse(completeness["approval_disclosures_complete"])
                self.assertEqual({r["tool"] for r in completeness["retrieval"]},
                                 {"get_change_plan", "get_execution_task"})
        self.assertEqual(self.helper.dispatch_count, 1)
