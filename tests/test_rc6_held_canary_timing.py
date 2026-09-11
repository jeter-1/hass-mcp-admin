"""Held reads account for entered transport work, independently of dispatch."""
import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.request_context import begin_request, end_request, current_telemetry
from tests import test_held_read_canary as held


class ReviewedPairingTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_operation_counts_one_attempt_for_exact_843(self):
        tools = held.captured_tools("8.4.3")
        review = json.loads((held.CAPTURE_ROOT / "ha-mcp-8.4.3-contract-review.json").read_text())
        by_name = {item["name"]: item for item in tools}
        tools = [by_name[name] for name in review["runtime_catalog"]["runtime_tool_order"]]
        gateway, transport, _ = await held.HeldReadCanaryTests.gateway(
            self, version="8.4.3", tools=tools, result=held.error_result()
        )
        telemetry, token = begin_request("synthetic-canary-timing-843")
        try:
            response = json.loads(await gateway.run_held_read_canary(
                upstream_tool_name="ha_get_operation_status",
                expected_compatibility_entry_id="ha-mcp-v8.4.3-d5cea47a",
                arguments={"operation_id": "synthetic-missing"},
            ))
            self.assertEqual(response["error_code"], "resource_not_found")
            self.assertEqual(len(transport.calls), 1)
            self.assertTrue(response["details"]["canary_evidence"]["dispatch_occurred"])
            self.assertEqual(response["timing"]["upstream_request_count"], 1)
            self.assertTrue(response["timing"]["upstream_attempted"])
            self.assertEqual(telemetry.upstream_active_requests, 0)
        finally:
            end_request(token)


class ProductionTransportTimingTests(unittest.IsolatedAsyncioTestCase):
    harness = held.HeldReadCanaryAuthorityTests.harness
    canary = held.HeldReadCanaryAuthorityTests.canary
    cancel = held.HeldReadCanaryAuthorityTests.cancel
    assert_authority = held.HeldReadCanaryAuthorityTests.assert_authority
    assert_late_callbacks_refused = held.HeldReadCanaryAuthorityTests.assert_late_callbacks_refused

    def assert_timing(self, telemetry, response=None, attempts=1):
        self.assertEqual(telemetry.upstream_request_count, attempts)
        self.assertEqual(telemetry.upstream_active_requests, 0)
        if attempts:
            self.assertIsNotNone(telemetry.upstream_span_finished)
            self.assertGreaterEqual(telemetry.upstream_duration_ms, 0)
        if response is not None:
            self.assertEqual(response["timing"]["upstream_request_count"], attempts)
            self.assertEqual(response["timing"]["upstream_attempted"], bool(attempts))
            self.assertEqual(response["timing"]["upstream_ms"], round(telemetry.upstream_duration_ms, 3))

    async def test_success_missing_and_failures_finish_before_serialization(self):
        cases = [(None, None)] + [
            (after_commit, error)
            for after_commit in (False, True)
            for error in (TimeoutError("synthetic"), ConnectionError("synthetic"), RuntimeError("synthetic"))
        ] + [(True, "missing")]
        for after_commit, error in cases:
            with self.subTest(after_commit=after_commit, error=type(error).__name__):
                h = await self.harness()
                if error == "missing":
                    h.network.result = held.error_result()
                elif error is not None:
                    if after_commit:
                        h.network.call_error = error
                    else:
                        h.network.catalog_error = error
                telemetry, token = begin_request("synthetic-canary-timing")
                try:
                    with patch.object(telemetry, "begin_upstream_attempt", wraps=telemetry.begin_upstream_attempt) as begin, patch.object(telemetry, "finish_upstream_attempt", wraps=telemetry.finish_upstream_attempt) as finish:
                        response = await self.canary(h)
                        self.assertEqual(begin.call_count, 1)
                        self.assertEqual(finish.call_count, 1)
                    self.assert_timing(telemetry, response)
                    self.assertIs(current_telemetry(), telemetry)
                    dispatched = error is None or after_commit
                    self.assertEqual(len(h.network.calls), int(dispatched))
                    self.assert_authority(h, consumed=int(dispatched), released=int(not dispatched), finished=int(dispatched))
                    self.assert_late_callbacks_refused(h)
                    self.assert_timing(telemetry, response)
                    self.assertEqual(response["metadata"]["fallback"], "none")
                finally:
                    end_request(token)

    async def test_local_refusal_has_no_attempt_or_authority(self):
        h = await self.harness()
        telemetry, token = begin_request("synthetic-local-refusal")
        try:
            response = json.loads(await h.gateway.run_held_read_canary(
                upstream_tool_name="ha_get_operation_status",
                expected_compatibility_entry_id="synthetic-wrong-entry",
                arguments={"operation_id": "synthetic-missing"},
            ))
            self.assertFalse(response["success"])
            self.assert_timing(telemetry, response, attempts=0)
            self.assert_authority(h, acquired=0)
            self.assertEqual(h.network.calls, [])
        finally:
            end_request(token)

    async def test_cancellation_finishes_timing_once_without_changing_ownership(self):
        for retained in (False, True):
            for after_commit in (False, True):
                with self.subTest(retained=retained, after_commit=after_commit):
                    h = await self.harness(retained=retained)
                    gate = h.network.call_gate if after_commit else h.network.catalog_gate
                    entered = h.network.call_entered if after_commit else h.network.catalog_entered
                    gate.clear()
                    telemetry, token = begin_request("synthetic-cancel-timing")
                    try:
                        with patch.object(telemetry, "begin_upstream_attempt", wraps=telemetry.begin_upstream_attempt) as begin, patch.object(telemetry, "finish_upstream_attempt", wraps=telemetry.finish_upstream_attempt) as finish:
                            task = asyncio.create_task(self.canary(h))
                            await asyncio.wait_for(entered.wait(), 3)
                            self.assertEqual(telemetry.upstream_active_requests, 1)
                            await self.cancel(task)
                            self.assert_timing(telemetry)
                            self.assert_late_callbacks_refused(h)
                            gate.set()
                            await h.transport.aclose()
                            self.assertEqual(begin.call_count, 1)
                            self.assertEqual(finish.call_count, 1)
                        self.assert_timing(telemetry)
                        self.assert_authority(h, consumed=int(after_commit), released=int(not after_commit), finished=int(after_commit))
                        self.assertEqual(len(h.network.calls), int(after_commit))
                    finally:
                        end_request(token)

    async def test_retained_timeout_and_predispatch_refusal_are_attempts(self):
        for mode in ("timeout", "retired"):
            h = await self.harness(retained=True)
            h.network.catalog_gate.clear()
            telemetry, token = begin_request("synthetic-no-dispatch-attempt")
            try:
                if mode == "timeout":
                    with patch.object(h.transport, "_operation_budget_seconds", return_value=0.05):
                        response = await self.canary(h)
                else:
                    task = asyncio.create_task(self.canary(h))
                    await asyncio.wait_for(h.network.catalog_entered.wait(), 3)
                    h.runtime._coordinator.retire_current_generation()
                    h.network.catalog_gate.set()
                    response = await task
                self.assertFalse(response["success"])
                self.assert_timing(telemetry, response)
                self.assertEqual(h.network.calls, [])
                self.assert_late_callbacks_refused(h)
                h.network.catalog_gate.set()
                await h.transport.aclose()
                self.assert_authority(h, consumed=int(mode == "retired"), released=1)
            finally:
                end_request(token)

    async def test_cancellation_before_start_has_no_attempt(self):
        h = await self.harness(retained=True)
        telemetry, token = begin_request("synthetic-cancel-before-start")
        try:
            task = asyncio.create_task(self.canary(h))
            await self.cancel(task)
            self.assert_timing(telemetry, attempts=0)
            self.assert_authority(h, acquired=0)
            self.assertEqual(h.network.calls, [])
        finally:
            end_request(token)
