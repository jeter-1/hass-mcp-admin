"""Valid bounded receipts preserve completed action and reconciliation facts."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.models.responses import MAX_CHARS, dump_json
from ha_mcp_engineering.tool_framework import run_structured
from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from tests import test_beta37_exact_helper_state as helper_fixtures
from tests import test_f3_runtime_integration as operational_fixtures
from ha_mcp_engineering.tools import governance
from ha_mcp_engineering.request_context import begin_request, end_request


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

    def test_task_accounting_does_not_infer_missing_dispatch_or_verification(self):
        from ha_mcp_engineering.models.responses import SuccessResponse

        for observed in ({}, {"provider_attempt_count": 0, "dispatched_at": None},
                         {"provider_attempt_count": 1, "dispatched_at": None,
                          "verification_summary": {"status": None}}):
            with self.subTest(observed=observed):
                value = {"task_id": "a" * 32, "plan_id": "b" * 32,
                         "plan_hash": "c" * 64, "state": "observing",
                         "payload": "雪" * 6000, **observed}
                original = deepcopy(value)
                encoded = SuccessResponse(
                    "get_execution_task", "Read task", value, request_id="r" * 128
                ).to_json(1024)
                data = json.loads(encoded)["data"]
                self.assertLessEqual(len(encoded.encode("utf-8")), 1024)
                self.assertEqual(data["state"], "observing")
                for key in ("provider_attempt_count", "dispatched_at"):
                    self.assertEqual(key in data, key in observed)
                    if key in observed:
                        self.assertEqual(data[key], observed[key])
                self.assertNotIn("provider_dispatch_occurred", data)
                self.assertNotIn("verified", data)
                status = data.get("verification_summary", {})
                if "verification_summary" in observed:
                    self.assertTrue("status" in status or "task_verification_status" in data)
                    self.assertIsNone(status.get("status", data.get("task_verification_status")))
                else:
                    self.assertNotIn("status", status)
                    self.assertNotIn("task_verification_status", data)
                self.assertEqual(value, original)


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


class TaskReceiptTests(unittest.IsolatedAsyncioTestCase):
    """RC5-REVIEW-1: real task reader over disposable governed executions.

    The observing regression is ported from the preserved independent review's
    test_task_receipt.py, reviewed against b14f8cf07a8dc569719b25c3edd60cfe19f1c68b.
    """

    asyncSetUp = operational_fixtures.F3OperationalActivationTests.asyncSetUp
    asyncTearDown = operational_fixtures.F3OperationalActivationTests.asyncTearDown
    _grant = operational_fixtures.F3OperationalActivationTests._grant

    async def create_reload(self):
        created = await self.service.create_reload_plan(reload_target="automation")
        return await self._grant(created)

    async def assert_task_receipt(self, task_id, state, limit):
        before = self.service.get_execution_task(task_id)
        self.assertEqual(before["state"], state)
        dispatches = self.lifecycle.dispatch_count
        _, token = begin_request("r" * 128)
        try:
            with (
                patch.object(governance.GOVERNANCE, "service", self.service),
                patch.object(governance, "SETTINGS", SimpleNamespace(response_size_limit=limit)),
            ):
                for _ in range(2):
                    encoded = await governance.get_execution_task(task_id)
                    receipt = json.loads(encoded)
                    data = receipt["data"]
                    self.assertTrue(receipt["success"])
                    self.assertEqual(receipt["request_id"], "r" * 128)
                    self.assertLessEqual(len(encoded), limit)
                    self.assertLessEqual(len(encoded.encode("utf-8")), limit)
                    for key in ("task_id", "plan_id", "plan_hash", "state",
                                "provider_attempt_count", "dispatched_at"):
                        self.assertIn(key, data)
                        self.assertEqual(data[key], before[key], key)
                    if "terminal_outcome" in data:
                        self.assertEqual(data["terminal_outcome"], before["terminal_outcome"])
                    if before["terminal_outcome"] is None:
                        self.assertIn("terminal_outcome", data)
                    status = before.get("verification_summary", {})
                    if "status" in status:
                        actual = data.get("verification_summary", {}).get(
                            "status", data.get("task_verification_status", "missing")
                        )
                        self.assertEqual(actual, status["status"])
                    else:
                        self.assertNotIn("task_verification_status", data)
                    if "provider_dispatch_occurred" not in before:
                        self.assertNotIn("provider_dispatch_occurred", data)
                    if limit == 1024:
                        self.assertTrue(receipt["response_completeness"]["truncated"])
                        self.assertFalse(receipt["response_completeness"]["approval_disclosures_complete"])
        finally:
            end_request(token)
        self.assertEqual(self.lifecycle.dispatch_count, dispatches)
        self.assertEqual(self.service.get_execution_task(task_id), before)

    async def observing_task(self):
        self.lifecycle.mode = "ambiguous"
        plan = await self.create_reload()
        applied = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(applied["task_state"], "observing")
        self.assertEqual(self.lifecycle.dispatch_count, 1)
        return applied["task_id"]

    async def test_minimum_budget_preserves_observing_task_facts(self):
        await self.assert_task_receipt(await self.observing_task(), "observing", 1024)

    async def test_default_budget_preserves_observing_task_facts(self):
        await self.assert_task_receipt(await self.observing_task(), "observing", 60000)

    async def test_pre_dispatch_task_retains_zero_attempts_and_null_dispatch(self):
        plan = await self.create_reload()
        task, _, _ = await self.runtime._initialize(
            self.service._load(plan["plan_id"]), plan["plan_hash"]
        )
        before = self.service.get_execution_task(task.task_id)
        self.assertEqual(before["provider_attempt_count"], 0)
        self.assertIsNone(before["dispatched_at"])
        self.assertIsNone(before["terminal_outcome"])
        for limit in (1024, 60000):
            await self.assert_task_receipt(task.task_id, "created", limit)
        self.assertEqual(self.lifecycle.dispatch_count, 0)

    async def test_successful_task_retains_verified_outcome(self):
        plan = await self.create_reload()
        applied = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        for limit in (1024, 60000):
            await self.assert_task_receipt(applied["task_id"], "succeeded_verified", limit)
        self.assertEqual(self.lifecycle.dispatch_count, 1)

    async def test_failed_task_remains_distinct_without_dispatch(self):
        plan = await self.create_reload()
        self.lifecycle.mode = "pre_dispatch_failure"
        applied = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        before = self.service.get_execution_task(applied["task_id"])
        self.assertEqual(before["provider_attempt_count"], 0)
        self.assertIsNone(before["dispatched_at"])
        for limit in (1024, 60000):
            await self.assert_task_receipt(applied["task_id"], "failed_pre_dispatch", limit)
        self.assertEqual(self.lifecycle.dispatch_count, 0)

    async def test_manual_review_task_remains_unverified_without_redispatch(self):
        task_id = await self.observing_task()
        self.clock.advance(seconds=901)
        await self.runtime.recover_once("receipt_regression_expired_evidence")
        for limit in (1024, 60000):
            await self.assert_task_receipt(task_id, "manual_review_required", limit)
        self.assertEqual(self.lifecycle.dispatch_count, 1)
