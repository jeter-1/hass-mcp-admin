"""HAMCP-181 PR1 (design section 2.4.2, OD-6(ii)): fan/power ``dispatched_at``.

Fan and power keep publishing the literal ``dispatched_at: null``.  The
companion fields ``dispatched_at_basis``, ``dispatch_intent_recorded_at`` and
``provider_response_recorded_at`` come from stored fields only.  Every case runs
through the fan service and the power service, and checks the receipt, the
snapshot audit and the ordinary request audit summary.  All transports,
authority inputs and audit files are synthetic.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from tests.test_typed_fan import Clock, Core, Transport  # noqa: E402
from tests.test_typed_power import PowerTransport  # noqa: E402
from tests.test_hamcp181_dispatch_facts import (  # noqa: E402
    FakeRecord, already_present, not_sent, r_proven, sent_verified, uncertain,
)
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.f3_runtime import dispatch_facts as df  # noqa: E402
from ha_mcp_engineering.fan import audit as fan_audit  # noqa: E402
from ha_mcp_engineering.fan.authority import FanCoreAuthority  # noqa: E402
from ha_mcp_engineering.fan.contracts import FanRequest, digest as fan_digest  # noqa: E402
from ha_mcp_engineering.fan.service import FanService  # noqa: E402
from ha_mcp_engineering.power import service as power_module  # noqa: E402
from ha_mcp_engineering.power.contracts import PowerRequest, digest as power_digest  # noqa: E402
from ha_mcp_engineering.power.service import PowerCoreAuthority, PowerService  # noqa: E402
from ha_mcp_engineering.providers.upstream_fan import FanProvider  # noqa: E402
from ha_mcp_engineering.providers.upstream_power import PowerProvider  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402

logging.disable(logging.CRITICAL)

FACT_KEYS = ("dispatched_at", "dispatched_at_basis", "dispatch_intent_recorded_at",
             "provider_response_recorded_at", "dispatch_phase", "attempt_counts_basis")


def _delayed(transport, clock):
    """Advance the fake clock between the intent commit and the provider send."""

    original = transport.execute_read
    transport.sent_at = None

    async def execute_read(tool, args, *, catalog_validator, before_dispatch, **kwargs):
        async def guarded():
            await before_dispatch()
            if tool == "ha_call_service":
                clock.advance(7)
                transport.sent_at = clock().isoformat()
                clock.advance(3)
        return await original(tool, args, catalog_validator=catalog_validator,
                              before_dispatch=guarded, **kwargs)

    transport.execute_read = execute_read


class _Kind:
    def __init__(self, test):
        self.test = test

    async def build(self, *, delayed=False, mode="success"):
        test = self.test
        test.tmp = tempfile.TemporaryDirectory()
        test.addCleanup(test.tmp.cleanup)
        test.audit_path = Path(test.tmp.name) / "audit.jsonl"
        test.audit = AuditLogger(str(test.audit_path), "synthetic-secret-never-export")
        test.clock, test.core = Clock(), Core()
        test.transport = self.transport()
        test.transport.mode = mode
        if delayed:
            _delayed(test.transport, test.clock)
        test.service = self.service(test)
        test.addAsyncCleanup(test.service.close)
        test.telemetry, token = begin_request("synthetic-hamcp181-request")
        test.addCleanup(end_request, token)

    def rebuilt(self):
        return self.service(self.test)


class FanKind(_Kind):
    name = "fan"

    def transport(self):
        return Transport()

    def service(self, test):
        provider = FanProvider(test.transport, lambda: test.transport.authority)
        return FanService(test.tmp.name, provider, FanCoreAuthority(test.core),
                          now=test.clock, audit=test.audit)

    def request(self, test, noop=False):
        return FanRequest(entity_id="fan.synthetic",
                          action="set_percentage" if noop else "turn_on",
                          percentage=0 if noop else 50,
                          operation_id=f"{int(test.clock().timestamp())}-{uuid.uuid4().hex}")

    def bind(self, test, request):
        test.telemetry.ordinary_fan_binding = fan_digest(request.model_dump())

    def summary(self, test):
        return fan_audit.request_summary(test.telemetry)


class PowerKind(_Kind):
    name = "power"

    def transport(self):
        return PowerTransport("8.5.0")

    def service(self, test):
        provider = PowerProvider(test.transport, lambda *args: test.transport.authority)
        return PowerService(test.tmp.name, provider, PowerCoreAuthority(test.core),
                            now=test.clock, audit=test.audit)

    def request(self, test, noop=False):
        return PowerRequest(entity_id="light.synthetic",
                            action="turn_off" if noop else "turn_on",
                            operation_id=f"{int(test.clock().timestamp())}-{uuid.uuid4().hex}")

    def bind(self, test, request):
        test.telemetry.ordinary_power_binding = power_digest(request.model_dump())

    def summary(self, test):
        return power_module.request_summary(test.telemetry)


class _FanPowerCases:
    kind_type = None

    async def run_case(self, *, delayed=False, mode="success", noop=False):
        self.kind = self.kind_type(self)
        await self.kind.build(delayed=delayed, mode=mode)
        request = self.kind.request(self, noop=noop)
        self.kind.bind(self, request)
        result = await self.service.control(request)
        summary = self.kind.summary(self)
        rows = [json.loads(line) for line in self.audit_path.read_text().splitlines()]
        snapshot = [row for row in rows if row["event"].endswith("_execution_snapshot")][-1]
        return request, result, summary, snapshot

    def assert_same_facts(self, *surfaces):
        first = {key: surfaces[0].get(key) for key in FACT_KEYS}
        for surface in surfaces:
            self.assertIsNone(surface["dispatched_at"])
            self.assertEqual({key: surface.get(key) for key in FACT_KEYS}, first, surface)
        return first

    async def test_1_success_keeps_null_with_ordered_companion_times(self):
        _, result, summary, snapshot = await self.run_case()
        self.assertEqual(result["state"], "succeeded_verified")
        facts = self.assert_same_facts(result, summary, snapshot)
        self.assertEqual(facts["dispatched_at_basis"], df.DISPATCHED_AT_SEND_TIME_NOT_RECORDED)
        record = self.service.executions.get(result["task_id"])
        self.assertEqual(facts["dispatch_intent_recorded_at"],
                         record.dispatch_intent["committed_at"])
        result_event = [e for e in record.events
                        if e["event_type"] == df.DISPATCH_RESULT_EVENT][-1]
        self.assertEqual(facts["provider_response_recorded_at"], result_event["occurred_at"])
        self.assertLessEqual(facts["dispatch_intent_recorded_at"],
                             facts["provider_response_recorded_at"])
        self.assertEqual(facts["dispatch_phase"], df.PHASE_SENT_RESPONSE_RECEIVED)

    async def test_2_response_lost_has_no_response_time(self):
        _, result, summary, snapshot = await self.run_case(mode="timeout")
        self.assertEqual(result["state"], "succeeded_verified")
        facts = self.assert_same_facts(result, summary, snapshot)
        self.assertIsNone(facts["provider_response_recorded_at"])
        self.assertIsNotNone(facts["dispatch_intent_recorded_at"])
        self.assertEqual(facts["dispatched_at_basis"], df.DISPATCHED_AT_SEND_TIME_NOT_RECORDED)
        self.assertEqual(facts["dispatch_phase"], df.PHASE_SEND_UNCERTAIN)
        self.assertTrue(result["provider_dispatch_occurred"])

    async def test_3_preflight_noop_has_no_times(self):
        _, result, summary, snapshot = await self.run_case(noop=True)
        self.assertEqual(result["state"], "succeeded_verified")
        self.assertEqual(self.transport.writes, 0)
        facts = self.assert_same_facts(result, summary, snapshot)
        self.assertEqual(facts["dispatched_at_basis"], df.DISPATCHED_AT_NO_INTENT)
        self.assertEqual(facts["attempt_counts_basis"], df.BASIS_NO_INTENT)
        self.assertIsNone(facts["dispatch_intent_recorded_at"])
        self.assertIsNone(facts["provider_response_recorded_at"])
        self.assertFalse(result["provider_dispatch_occurred"])

    async def test_5_delayed_send_is_never_published_as_a_send_time(self):
        _, result, summary, snapshot = await self.run_case(delayed=True)
        facts = self.assert_same_facts(result, summary, snapshot)
        sent_at = self.transport.sent_at
        self.assertIsNotNone(sent_at)
        self.assertGreater(sent_at, facts["dispatch_intent_recorded_at"])
        self.assertLessEqual(sent_at, facts["provider_response_recorded_at"])
        for surface in (result, summary, snapshot):
            self.assertNotIn(sent_at, json.dumps(surface))
            self.assertNotEqual(surface["dispatched_at"], facts["dispatch_intent_recorded_at"])

    async def test_6_restart_returns_identical_values(self):
        _, result, _, _ = await self.run_case(delayed=True)
        self.clock.advance(3600)
        rebuilt = self.kind.rebuilt()
        self.addAsyncCleanup(rebuilt.close)
        again = rebuilt.receipt(result["task_id"])
        self.assertEqual({key: again.get(key) for key in FACT_KEYS},
                         {key: result.get(key) for key in FACT_KEYS})


class FanDispatchedAtTests(_FanPowerCases, unittest.IsolatedAsyncioTestCase):
    kind_type = FanKind


class PowerDispatchedAtTests(_FanPowerCases, unittest.IsolatedAsyncioTestCase):
    kind_type = PowerKind


class ConstructedNotSentTests(unittest.TestCase):
    """Test 4: proven no-call (b) through the fan/power projection (constructed)."""

    def test_fan_power_fact_fields_for_b(self):
        fields = fan_audit.dispatch_fact_fields(not_sent())
        self.assertEqual(fields["dispatched_at_basis"], df.DISPATCHED_AT_CALL_NOT_SENT)
        self.assertEqual(fields["dispatch_phase"], df.PHASE_INTENT_RECORDED_NOT_SENT)
        self.assertNotIn("dispatched_at", fields)
        self.assertIs(fields["provider_dispatch_occurred"], False)

    def test_request_summary_bounds_and_nulls(self):
        for kind, summarize in (("fan", fan_audit.request_summary),
                                ("power", power_module.request_summary)):
            with self.subTest(kind=kind):
                context = {
                    **fan_audit.dispatch_fact_fields(not_sent()),
                    "dispatched_at": "2026-10-14T12:00:00+00:00",
                    "dispatch_phase_extra": "x",
                }
                safe = summarize(SimpleNamespace(audit_context=context))
                self.assertIsNone(safe["dispatched_at"])
                self.assertEqual(safe["dispatched_at_basis"], df.DISPATCHED_AT_CALL_NOT_SENT)
                self.assertNotIn("dispatch_phase_extra", safe)
                # Values outside the closed vocabularies never cross.
                bad = summarize(SimpleNamespace(audit_context={
                    "dispatch_phase": "verified_by_magic",
                    "dispatched_at_basis": "provider_send_time",
                    "dispatch_intent_recorded_at": "not-a-time <script>",
                    "attempt_counts_basis": df.BASIS_NO_SETTER_CALL,
                }))
                for key in ("dispatch_phase", "dispatched_at_basis",
                            "dispatch_intent_recorded_at", "attempt_counts_basis",
                            "dispatched_at"):
                    self.assertNotIn(key, bad)


class GovernedDispatchedAtTests(unittest.TestCase):
    """Test 8: governed equivalents use the stored value except for (b)."""

    STORED = "2026-10-14T12:00:00+00:00"

    def facts(self, *makers):
        children = [df.child_facts(maker(), child_id=str(index))
                    for index, maker in enumerate(makers)]
        return df.dispatch_facts(task=SimpleNamespace(dispatched_at=self.STORED),
                                 children=children)

    def test_b_publishes_null_with_call_not_sent(self):
        fields = self.facts(not_sent, not_sent).public_fields()
        self.assertIsNone(fields["dispatched_at"])
        self.assertEqual(fields["dispatched_at_basis"], df.DISPATCHED_AT_CALL_NOT_SENT)

    def test_every_other_intent_phase_keeps_the_stored_value(self):
        for makers in ((sent_verified,), (uncertain,), (r_proven,),
                       (sent_verified, not_sent, uncertain), (not_sent, r_proven)):
            with self.subTest(makers=[maker.__name__ for maker in makers]):
                fields = self.facts(*makers).public_fields()
                self.assertEqual(fields["dispatched_at"], self.STORED)
                self.assertEqual(fields["dispatched_at_basis"],
                                 df.DISPATCHED_AT_INTENT_COMMIT_TIME)

    def test_parent_companion_times_are_earliest_child_values(self):
        early = FakeRecord(response=True, outcome="succeeded_verified")
        early.dispatch_intent = {"committed_at": "2026-10-14T11:00:00+00:00"}
        children = [df.child_facts(sent_verified(), child_id="late"),
                    df.child_facts(early, child_id="early"),
                    df.child_facts(already_present(), child_id="none")]
        facts = df.dispatch_facts(task=SimpleNamespace(dispatched_at=self.STORED),
                                  children=children)
        self.assertEqual(facts.dispatch_intent_recorded_at, "2026-10-14T11:00:00+00:00")
        self.assertEqual([child.dispatch_intent_recorded_at for child in facts.children],
                         ["2026-10-14T12:00:00+00:00", "2026-10-14T11:00:00+00:00", None])


if __name__ == "__main__":
    unittest.main()
