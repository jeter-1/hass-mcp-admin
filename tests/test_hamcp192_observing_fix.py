"""HAMCP-192: observing fan/power tasks verify inline, sweep within a 30 s
lease, decide at the deadline after one final read, name their blocker, and
release a manual-review hold only on a governed Ingress decision.

Deterministic offline fixtures: synthetic transport, fake clock and fake sleep.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

import httpx

from ha_mcp_engineering.approval_web import create_approval_application
from ha_mcp_engineering.audit import AuditLogger
from ha_mcp_engineering.f3.locks import DurableLockStore, LockConflict
from ha_mcp_engineering.f3.models import LockOwner
from ha_mcp_engineering.fan.authority import FanCoreAuthority
from ha_mcp_engineering.fan.contracts import FanRefusal, FanRequest, digest
from ha_mcp_engineering.fan.locks import FAN_LOCK_TIMING
from ha_mcp_engineering.fan.service import (
    INLINE_POLL_BUDGET_SECONDS, FanService,
)
from ha_mcp_engineering.power.contracts import PowerRequest, digest as power_digest
from ha_mcp_engineering.power.service import PowerCoreAuthority, PowerService
from ha_mcp_engineering.providers.upstream_fan import FanProvider
from ha_mcp_engineering.providers.upstream_power import PowerProvider
from ha_mcp_engineering.request_context import begin_request, end_request
from tests.test_typed_fan import Clock, Core, Transport
from tests.test_typed_power import PowerTransport

ADMIN_HEADERS = {
    "X-Ingress-Path": "/api/hassio_ingress/hamcp192fixture",
    "X-Remote-User-Id": "admin-fixture",
}


class SlowFanTransport(Transport):
    """A fan that acknowledges and reaches the requested state ``delay`` s later."""

    def __init__(self, clock, delay):
        super().__init__()
        self.clock, self.delay = clock, delay
        self.mode = "ack_only"
        self.dispatched_at = None
        self.fail_reads = False

    def reads(self):
        return sum(tool == "ha_get_state" for tool, _ in self.calls)

    def service_calls(self):
        return sum(tool == "ha_call_service" for tool, _ in self.calls)

    async def execute_read(self, tool, args, **kwargs):
        if tool == "ha_get_state" and self.fail_reads:
            raise TimeoutError("synthetic read failure")
        if (tool == "ha_get_state" and self.dispatched_at is not None and self.delay is not None
                and self.clock() >= self.dispatched_at + timedelta(seconds=self.delay)):
            self.reach()
        result = await super().execute_read(tool, args, **kwargs)
        if tool == "ha_call_service":
            # Stamped after the write: the dispatch-boundary read runs nested inside it.
            self.dispatched_at = self.clock()
        return result

    def reach(self):
        self.state.update(state="on", last_updated="synthetic-late-revision")
        self.state["attributes"]["percentage"] = 50


class RecordingAudit:
    def __init__(self):
        self.entries = []

    def write_batch(self, entries):
        self.entries.extend(entries)
        return len(entries)


class ObservingFixture(unittest.IsolatedAsyncioTestCase):
    delay = None

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock, self.core = Clock(), Core()
        self.transport = SlowFanTransport(self.clock, self.delay)
        self.provider = FanProvider(self.transport, lambda: self.transport.authority)
        self.audit = RecordingAudit()
        self.service = self.build()
        self.telemetry, token = begin_request("synthetic-hamcp192")
        self.addCleanup(end_request, token)

    def build(self, cls=FanService):
        service = cls(self.tmp.name, self.provider, FanCoreAuthority(self.core),
                      now=self.clock, audit=self.audit, sleep=self.clock.sleep)
        self.addAsyncCleanup(service.close)
        return service

    def request(self):
        return FanRequest(entity_id="fan.synthetic", action="turn_on", percentage=50,
                          operation_id=f"{int(self.clock().timestamp())}-{uuid.uuid4().hex}")

    async def call(self, request, service=None):
        self.telemetry.ordinary_fan_binding = digest(request.model_dump())
        return await (service or self.service).control(request)

    def owned(self, task_id, service=None):
        return [item for item in (service or self.service).locks.records() if item.task_id == task_id]

    async def sweep(self, task_id, *, until=None, service=None):
        """Model application._supervise_f3_recovery: one pass every 30 s."""
        service = service or self.service
        passes = []
        while True:
            self.clock.advance(30)
            before = self.transport.reads()
            await service.recover_once()
            receipt = service.receipt(task_id)
            passes.append((self.clock(), self.transport.reads() - before, receipt))
            if receipt["terminal"] or (until is not None and self.clock() >= until):
                return passes
            self.assertLess(len(passes), 20, "sweep did not settle")

    async def held_task(self):
        """A never-matching task, settled into its manual-review entity hold."""
        request = self.request()
        await self.call(request)
        await self.sweep(request.task_id)
        receipt = self.service.receipt(request.task_id)
        self.assertEqual(receipt["terminal_outcome"], "manual_review_required", receipt)
        self.assertEqual([item.conflict_hold for item in self.owned(request.task_id)], [True])
        return request


class InlineVerificationTests(ObservingFixture):
    async def test_device_at_3s_and_8s_is_verified_inline(self):
        for delay in (3, 8):
            with self.subTest(delay=delay):
                self.transport.delay, self.transport.dispatched_at = delay, None
                self.transport.state.update(state="off", last_updated="synthetic-revision-1")
                self.transport.state["attributes"]["percentage"] = 0
                self.transport.calls.clear()
                started = self.clock()
                request = self.request()
                receipt = await self.call(request)
                self.assertEqual(receipt["state"], "succeeded_verified", receipt)
                self.assertTrue(receipt["terminal"])
                self.assertEqual(receipt["provider_attempt_count"], 1)
                self.assertEqual(self.owned(request.task_id), [])
                status = receipt["observation_status"]
                self.assertEqual(status["observation_attempts"], 1)
                self.assertIsNotNone(status["verified_at"])
                self.assertNotIn("reason", status)
                elapsed = (self.clock() - started).total_seconds()
                self.assertGreaterEqual(elapsed, delay)
                self.assertLessEqual(elapsed, INLINE_POLL_BUDGET_SECONDS)
                self.assertEqual(self.transport.service_calls(), 1)
        self.assertEqual(self.transport.writes, 2)

    async def test_inline_poll_is_bounded_to_about_ten_seconds(self):
        self.transport.delay = None
        started = self.clock()
        request = self.request()
        receipt = await self.call(request)
        self.assertFalse(receipt["terminal"], receipt)
        self.assertEqual((self.clock() - started).total_seconds(), INLINE_POLL_BUDGET_SECONDS)
        # Reads at 0, 1, 2, 4, 6, 8 and 10 s after dispatch, one write.
        post_dispatch = [tool for tool, _ in self.transport.calls]
        index = post_dispatch.index("ha_call_service")
        self.assertEqual(post_dispatch[index + 1:].count("ha_get_state"), 7)
        self.assertEqual(receipt["observation_status"]["observation_attempts"], 1)


class LeaseAndSweepTests(ObservingFixture):
    delay = 12

    async def test_device_at_12s_observes_then_the_sweep_verifies_within_the_lease(self):
        request = self.request()
        receipt = await self.call(request)
        self.assertFalse(receipt["terminal"], receipt)
        self.assertEqual(receipt["terminal_outcome"], None)
        status = receipt["observation_status"]
        self.assertEqual(status["reason"], "device_state_not_yet_matching")
        self.assertEqual(status["reobservation"], "background_sweep_every_30s")
        self.assertTrue(status["final_read_at_deadline"])
        self.assertIsNone(status["verified_at"])
        held = self.owned(request.task_id)
        self.assertTrue(held)
        for item in held:
            # Decision 2: the lease is 30 s from the last renewal, never 120 s.
            lease = datetime.fromisoformat(item.lease_expires_at) - self.clock()
            self.assertEqual(lease, timedelta(seconds=30))
        self.assertEqual(status["next_reobservation_not_before"], held[0].lease_expires_at)

        # Before the lease expires, a sweep never reads.
        self.clock.advance(29)
        before = self.transport.reads()
        await self.service.recover_once()
        self.assertEqual(self.transport.reads(), before)
        self.assertFalse(self.service.receipt(request.task_id)["terminal"])

        self.clock.advance(1)
        await self.service.recover_once()
        receipt = self.service.receipt(request.task_id)
        self.assertEqual(receipt["state"], "succeeded_verified", receipt)
        self.assertIsNotNone(receipt["observation_status"]["verified_at"])
        self.assertEqual(receipt["observation_status"]["observation_attempts"], 2)
        self.assertEqual(self.owned(request.task_id), [])
        self.assertEqual(self.transport.writes, 1)

    def test_fan_and_power_share_the_30_second_lease(self):
        self.assertEqual(FAN_LOCK_TIMING.lease_seconds, 30)
        self.assertEqual(self.service.executor().lock_timing.lease_seconds, 30)
        self.assertTrue(self.service.executor().final_read_at_deadline)


class SlowDeviceTests(ObservingFixture):
    delay = 130

    async def test_device_at_130s_is_verified_by_a_sweep_without_a_hold(self):
        request = self.request()
        await self.call(request)
        passes = await self.sweep(request.task_id)
        when, _reads, receipt = passes[-1]
        self.assertEqual(receipt["state"], "succeeded_verified", receipt)
        deadline = datetime.fromisoformat(receipt["observation_status"]["evidence_deadline"])
        self.assertLess(when, deadline)
        self.assertEqual(self.owned(request.task_id), [])
        self.assertEqual(self.service.health()["conflict_hold_count"], 0)
        self.assertEqual(self.transport.writes, 1)


class DeadlineFinalReadTests(ObservingFixture):
    delay = 185  # after the 180 s deadline, before the first post-deadline pass

    async def test_device_just_after_the_deadline_is_verified_by_the_final_read(self):
        request = self.request()
        await self.call(request)
        passes = await self.sweep(request.task_id)
        when, reads, receipt = passes[-1]
        deadline = datetime.fromisoformat(receipt["observation_status"]["evidence_deadline"])
        self.assertGreaterEqual(when, deadline)
        self.assertEqual(reads, 1)
        self.assertEqual(receipt["state"], "succeeded_verified", receipt)
        self.assertEqual(self.owned(request.task_id), [])
        self.assertEqual(self.transport.writes, 1)

    async def test_without_the_opt_in_the_shared_executor_keeps_the_pre_read_check(self):
        # The shared executor default stays off for every other F3 family.
        executor_type = type(self.service.executor())
        self.assertFalse(executor_type.__init__.__kwdefaults__["final_read_at_deadline"])

        class PreReadDeadline(FanService):
            def executor(self):
                executor = super().executor()
                executor.final_read_at_deadline = False
                return executor

        service = self.build(PreReadDeadline)
        request = self.request()
        await self.call(request, service)
        passes = await self.sweep(request.task_id, service=service)
        _when, reads, receipt = passes[-1]
        self.assertEqual(reads, 0)  # decided without reading
        self.assertEqual(receipt["state"], "manual_review_required", receipt)


class NeverMatchingTests(ObservingFixture):
    async def test_never_matching_device_reaches_manual_review_after_a_final_read(self):
        request = self.request()
        await self.call(request)
        passes = await self.sweep(request.task_id)
        when, reads, receipt = passes[-1]
        deadline = datetime.fromisoformat(receipt["observation_status"]["evidence_deadline"])
        self.assertGreaterEqual(when, deadline)
        self.assertEqual(reads, 1)  # one final read, then the decision
        self.assertTrue(all(not item[2]["terminal"] for item in passes[:-1]))
        self.assertTrue(all(item[0] < deadline for item in passes[:-1]))
        self.assertEqual(receipt["terminal_outcome"], "manual_review_required", receipt)
        self.assertEqual(receipt["verification"]["manual_review_reason_code"],
                         "post_dispatch_evidence_deadline_expired")
        self.assertEqual([(item.key, item.conflict_hold) for item in self.owned(request.task_id)],
                         [("entity:fan.synthetic", True)])
        self.assertEqual(self.transport.writes, 1)
        self.assertEqual(receipt["hold_release"],
                         {"released": False, "procedure": "home_assistant_admin_ingress_typed_hold_release"})


class BlockingTaskTests(ObservingFixture):
    async def test_lock_conflict_names_the_observing_blocker(self):
        first = self.request()
        await self.call(first)
        blocker = self.service.receipt(first.task_id)
        self.assertFalse(blocker["terminal"])
        lease = self.owned(first.task_id)[0].lease_expires_at
        second = self.request()
        receipt = await self.call(second)
        self.assertEqual(receipt["terminal_outcome"], "lock_conflict", receipt)
        self.assertEqual(receipt["blocking_tasks_observed_at"], self.clock().isoformat())
        self.assertEqual(receipt["blocking_tasks"], [{
            "task_id": first.task_id, "lock_key": "entity:fan.synthetic",
            "conflict_hold": False, "state": blocker["state"],
            "lease_status": "active", "expected_release_at": lease,
            "release": "verified_readback_or_deadline_final_read",
            "decision_deadline": blocker["observation_status"]["evidence_deadline"],
        }])
        self.assertEqual(self.transport.writes, 1)  # refused: no queueing, no superseding

    async def test_lock_conflict_names_a_manual_review_hold(self):
        held = await self.held_task()
        second = self.request()
        receipt = await self.call(second)
        self.assertEqual(receipt["terminal_outcome"], "lock_conflict", receipt)
        self.assertEqual(receipt["blocking_tasks"], [{
            "task_id": held.task_id, "lock_key": "entity:fan.synthetic",
            "conflict_hold": True, "state": "manual_review_required",
            "lease_status": "conflict_hold",
            "expected_release_at": None, "release": "governed_hold_release_required",
        }])
        self.assertEqual(self.transport.writes, 1)


class HoldReleaseServiceTests(ObservingFixture):
    def sidecar(self, task_id):
        path = self.service.root / "hold-releases" / f"{task_id}.json"
        return json.loads(path.read_text()) if path.exists() else None

    async def release(self, request, principal="home_assistant_admin_ingress:admin-fixture",
                      review=None):
        review = review or await self.service.hold_release_review(request.task_id)
        return await self.service.release_hold(
            request.task_id, record_generation=review["record_generation"],
            prepared_hash=review["prepared_hash"], hold_binding=review["hold_binding"],
            reviewed_readback_hash=review["readback_hash"], authorized_principal=principal)

    async def test_release_without_admin_approval_is_refused(self):
        request = await self.held_task()
        review = await self.service.hold_release_review(request.task_id)
        reads = self.transport.reads()
        for principal in (None, "", "authenticated_connector", "home_assistant_admin_ingress:",
                          "home_assistant_admin_panel"):
            with self.assertRaisesRegex(FanRefusal, "fan_hold_release_authority_required"):
                await self.release(request, principal, review)
        self.assertEqual(self.transport.reads(), reads)
        self.assertIsNone(self.sidecar(request.task_id))
        self.assertEqual([item.conflict_hold for item in self.owned(request.task_id)], [True])

    async def test_release_records_a_readback_clears_the_hold_and_never_dispatches(self):
        request = await self.held_task()
        review = await self.service.hold_release_review(request.task_id)
        reads, writes = self.transport.reads(), self.transport.service_calls()
        result = await self.release(request, review=review)
        self.assertEqual(result["status"], "hold_released_state_differs")
        self.assertEqual(result["comparison"], [("state", "on", "off"), ("percentage", 50, 0)])
        self.assertFalse(result["readback_matches_requested_state"])
        self.assertEqual(result["terminal_outcome"], "manual_review_required")
        self.assertEqual(result["dispatch"], "none")
        self.assertEqual(self.transport.reads(), reads + 1)  # one fresh readback
        self.assertEqual(self.transport.service_calls(), writes)
        self.assertEqual(self.transport.writes, 1)
        self.assertEqual(self.owned(request.task_id), [])
        sidecar = self.sidecar(request.task_id)
        self.assertRegex(sidecar["readback_hash"], r"^[a-f0-9]{64}$")
        self.assertEqual(sidecar["readback_hash"], result["readback_evidence_hash"])
        self.assertIsNotNone(sidecar["released_at"])
        self.assertEqual(sidecar["authority_hash"],
                         digest({"principal": "home_assistant_admin_ingress:admin-fixture"}))
        receipt = self.service.receipt(request.task_id)
        self.assertEqual(receipt["terminal_outcome"], "manual_review_required")
        self.assertEqual(receipt["retained_locks"], [])
        self.assertEqual(receipt["hold_release"]["outcome"], "hold_released_state_differs")
        self.assertEqual(receipt["hold_release"]["dispatch"], "none")
        entries = [item for item in self.audit.entries if item["event"] == "fan_hold_release_action"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["dispatch"], "none")
        self.assertEqual(entries[0]["readback_evidence_hash"], sidecar["readback_hash"])
        self.assertEqual(entries[0]["authority_kind"], "home_assistant_admin_ingress")
        self.assertEqual(self.service.health()["conflict_hold_count"], 0)

        # Settled: later sweeps neither read, fail, nor re-promote; never re-released.
        failures = self.service.recovery_failures
        for _ in range(3):
            self.clock.advance(30)
            await self.service.recover_once()
        self.assertEqual(self.service.recovery_failures, failures)
        self.assertEqual(self.transport.reads(), reads + 1)
        self.assertEqual(self.owned(request.task_id), [])
        self.assertIsNone(self.service.hold_release_item(request.task_id))
        with self.assertRaisesRegex(FanRefusal, "fan_hold_release_unavailable"):
            await self.service.release_hold(
                request.task_id, record_generation=0, prepared_hash="0" * 64,
                hold_binding="entity:fan.synthetic:1", reviewed_readback_hash=review["readback_hash"],
                authorized_principal="home_assistant_admin_ingress:admin-fixture")
        self.assertIsNone(await self.service.hold_release_review(request.task_id))
        # Same-ID replay stays reconciliation-only.
        await self.call(request)
        self.assertEqual(self.transport.writes, 1)
        # The entity is free for a new command.
        self.transport.state.update(state="off", last_updated="synthetic-revision-9")
        self.transport.state["attributes"]["percentage"] = 0
        self.transport.delay, self.transport.dispatched_at = 0, None
        fresh = await self.call(self.request())
        self.assertEqual(fresh["state"], "succeeded_verified", fresh)
        self.assertEqual(self.transport.writes, 2)

    async def test_matching_readback_is_reported_truthfully(self):
        request = await self.held_task()
        self.transport.reach()
        result = await self.release(request)
        self.assertEqual(result["status"], "hold_released_state_matches")
        self.assertTrue(result["readback_matches_requested_state"])
        self.assertEqual(self.service.receipt(request.task_id)["terminal_outcome"],
                         "manual_review_required")

    async def test_failed_readback_or_stale_binding_changes_nothing(self):
        request = await self.held_task()
        item = await self.service.hold_release_review(request.task_id)
        for changed in ({"record_generation": item["record_generation"] + 1},
                        {"prepared_hash": "0" * 64},
                        {"hold_binding": "entity:fan.synthetic:999"}):
            arguments = {"record_generation": item["record_generation"],
                         "prepared_hash": item["prepared_hash"],
                         "hold_binding": item["hold_binding"],
                         "reviewed_readback_hash": item["readback_hash"], **changed}
            with self.assertRaisesRegex(FanRefusal, "fan_hold_release_stale"):
                await self.service.release_hold(
                    request.task_id, authorized_principal="home_assistant_admin_ingress:a", **arguments)
        self.transport.fail_reads = True
        with self.assertRaisesRegex(FanRefusal, "fan_hold_release_readback_unavailable"):
            await self.release(request, review=item)
        with self.assertRaisesRegex(FanRefusal, "fan_hold_release_readback_unavailable"):
            await self.service.hold_release_review(request.task_id)
        self.assertIsNone(self.sidecar(request.task_id))
        self.assertEqual([item.conflict_hold for item in self.owned(request.task_id)], [True])
        self.assertEqual(self.transport.writes, 1)

    async def test_interrupted_release_is_finished_by_recovery_without_provider_access(self):
        request = await self.held_task()
        original = self.service.locks.release_conflict_hold

        def crash(**kwargs):
            raise RuntimeError("synthetic process loss after the decision")

        self.service.locks.release_conflict_hold = crash
        with self.assertRaises(RuntimeError):
            await self.release(request)
        self.service.locks.release_conflict_hold = original
        self.assertIsNone(self.sidecar(request.task_id)["released_at"])
        self.assertEqual([item.conflict_hold for item in self.owned(request.task_id)], [True])
        reads = self.transport.reads()
        replacement = self.build()
        self.clock.advance(30)
        await replacement.recover_once()
        self.assertEqual(self.transport.reads(), reads)  # finishing never reads or dispatches
        self.assertEqual(self.owned(request.task_id, replacement), [])
        self.assertIsNotNone(self.sidecar(request.task_id)["released_at"])
        self.assertEqual(replacement.receipt(request.task_id)["hold_release"]["outcome"],
                         "hold_released_state_differs")
        entries = [item for item in self.audit.entries if item["event"] == "fan_hold_release_action"]
        self.assertEqual([item["timestamp"] for item in entries],
                         [self.sidecar(request.task_id)["released_at"]])
        self.assertEqual(self.transport.writes, 1)

    async def test_review_shows_requested_and_freshly_read_state_side_by_side(self):
        request = await self.held_task()
        reads, writes = self.transport.reads(), self.transport.writes
        review = await self.service.hold_release_review(request.task_id)
        self.assertEqual(self.transport.reads(), reads + 1)  # one fresh, read-only readback
        self.assertEqual(review["comparison"], [("state", "on", "off"), ("percentage", 50, 0)])
        self.assertFalse(review["readback_matches_requested_state"])
        self.assertEqual(review["readback_hash"], digest({
            "entity_id": "fan.synthetic", "state": "off", "percentage": 0,
            "supported_features": 49, "last_updated": self.transport.state.get("last_updated"),
        }))
        self.transport.reach()
        review = await self.service.hold_release_review(request.task_id)
        self.assertEqual(review["comparison"], [("state", "on", "on"), ("percentage", 50, 50)])
        self.assertTrue(review["readback_matches_requested_state"])
        self.assertEqual(self.transport.writes, writes)
        self.assertIsNone(self.sidecar(request.task_id))
        self.assertEqual([item.conflict_hold for item in self.owned(request.task_id)], [True])

    async def test_state_change_after_review_refuses_the_release(self):
        request = await self.held_task()
        review = await self.service.hold_release_review(request.task_id)
        self.transport.reach()  # the device moves after the reviewer saw "off"
        with self.assertRaisesRegex(FanRefusal, "fan_hold_release_state_changed"):
            await self.release(request, review=review)
        self.assertIsNone(self.sidecar(request.task_id))
        self.assertEqual([item.conflict_hold for item in self.owned(request.task_id)], [True])
        self.assertEqual(self.transport.writes, 1)
        result = await self.release(request)  # a fresh review covers the new state
        self.assertEqual(result["status"], "hold_released_state_matches")
        self.assertEqual(result["comparison"], [("state", "on", "on"), ("percentage", 50, 50)])

    async def test_invalid_sidecar_fails_closed(self):
        request = await self.held_task()
        path = self.service.root / "hold-releases" / f"{request.task_id}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text('{"model": "typed-hold-release-v1"}')
        self.assertIsNone(self.service.hold_release_item(request.task_id))
        failures = self.service.recovery_failures
        self.clock.advance(30)
        await self.service.recover_once()
        self.assertEqual(self.service.recovery_failures, failures + 1)
        self.assertEqual([item.conflict_hold for item in self.owned(request.task_id)], [True])


class HoldReleaseIngressTests(ObservingFixture):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        app = create_approval_application(object(), typed_services={"fan": lambda: self.service})
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("172.30.32.2", 12345)),
            base_url="http://typed-ingress.local", headers=ADMIN_HEADERS)
        self.addAsyncCleanup(self.client.aclose)

    @staticmethod
    def form(html):
        return dict(re.findall(r'name="([a-z_]+)" value="([^"]*)"', html))

    @staticmethod
    def comparison(html):
        table = re.search(r'<table class="typed-state-comparison">(.*?)</table>', html, re.S)
        return [tuple(re.sub(r"<[^>]+>", "", cell) for cell in re.findall(r"<t[hd]>(.*?)</t[hd]>", row))
                for row in re.findall(r"<tr>(.*?)</tr>", table.group(1), re.S)]

    async def test_ingress_release_requires_admin_approval_and_a_fresh_binding(self):
        request = await self.held_task()
        path = f"/typed/fan/{request.task_id}"
        reads = self.transport.reads()
        for headers in ({"X-Remote-User-Id": ""}, {"X-Ingress-Path": ""}):
            self.assertEqual((await self.client.get("/typed", headers=headers)).status_code, 403)
            self.assertEqual((await self.client.get(path, headers=headers)).status_code, 403)
        self.assertEqual(self.transport.reads(), reads)  # no review readback without an admin
        listing = await self.client.get("/typed")
        self.assertIn(request.task_id, listing.text)
        self.assertEqual((await self.client.get(f"/typed/fan/{'0' * 32}")).status_code, 404)
        self.assertEqual((await self.client.get(f"/typed/climate/{request.task_id}")).status_code, 404)

        review = await self.client.get(path)
        self.assertEqual(review.status_code, 200)
        self.assertIn("sends no command", review.text)
        stale = self.form(review.text)
        stale["record_generation"] = str(int(stale["record_generation"]) + 1)
        self.assertEqual((await self.client.post(path + "/release", data=stale)).status_code, 400)
        # The token is single-use: the original form is now stale too.
        self.assertEqual((await self.client.post(path + "/release", data=self.form(review.text))).status_code, 400)
        review = await self.client.get(path)
        forged = self.form(review.text)
        forged["csrf"] = "forged"
        self.assertEqual((await self.client.post(path + "/release", data=forged)).status_code, 400)
        review = await self.client.get(path)
        other_user = await self.client.post(path + "/release", data=self.form(review.text),
                                            headers={"X-Remote-User-Id": "other-admin"})
        self.assertEqual(other_user.status_code, 400)
        self.assertEqual([item.conflict_hold for item in self.owned(request.task_id)], [True])

        writes, reads = self.transport.service_calls(), self.transport.reads()
        review = await self.client.get(path)
        released = await self.client.post(path + "/release", data=self.form(review.text))
        self.assertEqual(released.status_code, 200, released.text)
        self.assertIn("hold_released_state_differs", released.text)
        self.assertIn("Dispatch: none", released.text)
        self.assertEqual(self.comparison(released.text)[1:], [
            ("state", "on", "off", "DIFFERENT"), ("percentage", "50", "0", "DIFFERENT")])
        self.assertEqual(self.transport.service_calls(), writes)
        self.assertEqual(self.transport.reads(), reads + 2)  # the review read and the release read
        self.assertEqual(self.owned(request.task_id), [])
        self.assertEqual((await self.client.get(path)).status_code, 404)
        self.assertNotIn(request.task_id, (await self.client.get("/typed")).text)


    async def test_review_page_shows_requested_and_fresh_state_side_by_side(self):
        request = await self.held_task()
        path = f"/typed/fan/{request.task_id}"
        review = await self.client.get(path)
        self.assertEqual(review.status_code, 200)
        self.assertEqual(self.comparison(review.text), [
            ("Field", "Requested", "Freshly read now", "Comparison"),
            ("state", "on", "off", "DIFFERENT"),
            ("percentage", "50", "0", "DIFFERENT"),
        ])
        self.assertIn("<strong>The read state DIFFERS from the requested state.</strong>", review.text)
        # The comparison precedes the release control.
        self.assertLess(review.text.index("typed-state-comparison"), review.text.index("<form"))

        # The device moves after review: the decision no longer covers it.
        self.transport.reach()
        refused = await self.client.post(path + "/release", data=self.form(review.text))
        self.assertEqual(refused.status_code, 409)
        self.assertIn("fan_hold_release_state_changed", refused.text)
        self.assertEqual([item.conflict_hold for item in self.owned(request.task_id)], [True])

        review = await self.client.get(path)
        self.assertEqual(self.comparison(review.text)[1:], [
            ("state", "on", "on", "same"), ("percentage", "50", "50", "same")])
        self.assertIn("matches the requested state", review.text)
        released = await self.client.post(path + "/release", data=self.form(review.text))
        self.assertEqual(released.status_code, 200, released.text)
        self.assertIn("hold_released_state_matches", released.text)
        self.assertEqual(self.comparison(released.text)[0][2], "Recorded readback")
        self.assertEqual(self.transport.writes, 1)

    async def test_review_page_without_a_readback_offers_no_release(self):
        request = await self.held_task()
        self.transport.fail_reads = True
        review = await self.client.get(f"/typed/fan/{request.task_id}")
        self.assertEqual(review.status_code, 503)
        self.assertNotIn("<form", review.text)
        self.assertNotIn("csrf", review.text)
        self.assertEqual([item.conflict_hold for item in self.owned(request.task_id)], [True])


class ExpiredBlockerTests(ObservingFixture):
    """Codex M2: receipts name exactly what the lock check refuses."""

    async def test_expired_unreconciled_lease_still_names_its_blocker(self):
        # Codex's reproduction: the lease has expired, the sweep has not run.
        first = self.request()
        await self.call(first)
        owned = {item.key: item for item in self.owned(first.task_id)}
        self.assertEqual(sorted(item.mode for item in owned.values()),
                         ["exclusive", "shared", "shared"])
        self.clock.advance(31)
        self.assertTrue(all(datetime.fromisoformat(item.lease_expires_at) < self.clock()
                            for item in owned.values()))
        receipt = await self.call(self.request())
        self.assertEqual(receipt["terminal_outcome"], "lock_conflict", receipt)
        entries = receipt["blocking_tasks"]
        self.assertEqual({entry["task_id"] for entry in entries}, {first.task_id})
        # The exclusive entity lease and both expired shared leases block.
        self.assertEqual(sorted(entry["lock_key"] for entry in entries), sorted(owned))
        for entry in entries:
            self.assertEqual(entry["lease_status"], "expired_pending_recovery")
            self.assertIsNone(entry["expected_release_at"])  # expiry releases nothing
            self.assertEqual(entry["lease_expired_at"], owned[entry["lock_key"]].lease_expires_at)
            self.assertEqual(entry["release"], "recovery_sweep_reconciles_expired_lease")
            self.assertEqual(entry["state"], "observing")
        self.assertEqual(len(self.owned(first.task_id)), 3)
        self.assertEqual(self.transport.writes, 1)  # refused: no queueing, no superseding

    def shared_holder(self):
        """A synthetic other owner of only the shared core key."""
        request = next(item for item in self.service.adapter.lock_requests(self.prepared_probe())
                       if item.key == "home_assistant:core")
        owner = LockOwner(uuid.uuid4().hex, uuid.uuid4().hex, None, "synthetic-shared-holder",
                          uuid.uuid4().hex)
        self.service.locks.acquire_once((request,), owner=owner, timing=FAN_LOCK_TIMING,
                                        now=self.clock())
        return owner

    def prepared_probe(self):
        from ha_mcp_engineering.fan.adapter import prepare_record
        from ha_mcp_engineering.fan.contracts import FAN_CONTRACTS
        return prepare_record(self.request(), {"state": "off", "percentage": 0,
                                               "supported_features": 49}, FAN_CONTRACTS[-1])

    async def test_active_shared_holder_does_not_block_but_an_expired_one_does(self):
        owner = self.shared_holder()
        self.transport.delay = 0
        first = await self.call(self.request())
        self.assertEqual(first["state"], "succeeded_verified", first)  # shared + shared: no conflict
        self.assertNotIn("blocking_tasks", first)
        self.clock.advance(31)  # the synthetic shared lease expires unreconciled
        receipt = await self.call(self.request())
        self.assertEqual(receipt["terminal_outcome"], "lock_conflict", receipt)
        self.assertEqual([(entry["task_id"], entry["lock_key"], entry["lease_status"], entry["state"])
                          for entry in receipt["blocking_tasks"]],
                         [(owner.task_id, "home_assistant:core", "expired_pending_recovery",
                           "lease_held")])
        self.assertEqual(self.transport.writes, 1)

    async def test_lock_check_and_hints_share_one_predicate(self):
        first = self.request()
        await self.call(first)
        self.clock.advance(31)
        sites = []
        original = DurableLockStore.holder_blocks.__func__

        def spy(cls, holder, mode, now):
            import inspect
            sites.append(inspect.stack()[1].function)
            return original(cls, holder, mode, now)

        with patch.object(DurableLockStore, "holder_blocks", classmethod(spy)):
            receipt = await self.call(self.request())
        self.assertEqual(receipt["terminal_outcome"], "lock_conflict", receipt)
        self.assertIn("mutate", sites)  # acquisition
        self.assertIn("_blocking_tasks", sites)  # receipt hints
        # The predicate is the acquisition rule: every documented combination.
        holder = next(item for item in self.owned(first.task_id) if item.mode == "shared")
        for expired in (False, True):
            instant = (datetime.fromisoformat(holder.lease_expires_at)
                       + timedelta(seconds=1 if expired else -1))
            self.assertIs(DurableLockStore.holder_blocks(holder, "shared", instant), expired)
            self.assertTrue(DurableLockStore.holder_blocks(holder, "exclusive", instant))


class HoldReleaseExactlyOnceTests(ObservingFixture):
    """Codex M1: one durable audit entry per release, whatever interrupts it.

    Uses the production idempotent AuditLogger. ``Crash`` is a BaseException
    standing in for process loss; a replacement service models the restart.
    """

    class Crash(BaseException):
        pass

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.audit_path = Path(self.tmp.name) / "audit.jsonl"
        self.audit = AuditLogger(str(self.audit_path), "synthetic-audit-secret-not-real")
        self.service = self.build()

    def release_entries(self):
        if not self.audit_path.exists():
            return []
        entries = [json.loads(line) for line in self.audit_path.read_text().splitlines()]
        return [item for item in entries if item["event"] == "fan_hold_release_action"]

    async def decide(self, request, service=None):
        service = service or self.service
        review = await service.hold_release_review(request.task_id)
        return await service.release_hold(
            request.task_id, record_generation=review["record_generation"],
            prepared_hash=review["prepared_hash"], hold_binding=review["hold_binding"],
            reviewed_readback_hash=review["readback_hash"],
            authorized_principal="home_assistant_admin_ingress:admin-fixture")

    async def assert_settled_once(self, request, service):
        reads = self.transport.reads()
        for _ in range(3):
            self.clock.advance(30)
            await service.recover_once()
        self.assertEqual(self.transport.reads(), reads)  # finishing never reads or dispatches
        self.assertEqual(self.transport.writes, 1)
        entries = self.release_entries()
        self.assertEqual(len(entries), 1, entries)
        sidecar = service._load_release(request.task_id)
        self.assertIsNotNone(sidecar["released_at"])
        self.assertTrue(sidecar["audit_recorded"])
        self.assertEqual(entries[0]["timestamp"], sidecar["released_at"])
        self.assertEqual(entries[0]["hold_release_id"], service.hold_release_id(sidecar))
        self.assertEqual(entries[0]["dispatch"], "none")
        self.assertEqual(self.owned(request.task_id, service), [])
        self.assertEqual(service.health()["conflict_hold_count"], 0)
        self.assertEqual(service.recovery_failures, 0)
        receipt = service.receipt(request.task_id)
        self.assertEqual(receipt["terminal_outcome"], "manual_review_required")
        self.assertEqual(receipt["hold_release"]["audit_recorded"], True)
        self.assertEqual(receipt["hold_release"]["hold_release_id"], entries[0]["hold_release_id"])

    async def crash_then_restart(self, target, attribute, *, after, when=lambda *args, **kwargs: True):
        request = await self.held_task()
        original = getattr(target, attribute)
        fired = []

        def crashing(*args, **kwargs):
            if fired or not when(*args, **kwargs):
                return original(*args, **kwargs)
            fired.append(True)
            if after:
                original(*args, **kwargs)
            raise self.Crash()

        with patch.object(target, attribute, side_effect=crashing):
            with self.assertRaises(self.Crash):
                await self.decide(request)
        self.assertEqual(fired, [True])
        await self.assert_settled_once(request, self.build())

    async def test_crash_after_the_decision_before_the_hold_is_cleared(self):
        await self.crash_then_restart(self.service.locks, "release_conflict_hold", after=False)

    async def test_crash_after_the_hold_is_cleared_before_released_at(self):
        await self.crash_then_restart(self.service.locks, "release_conflict_hold", after=True)

    async def test_crash_after_released_at_before_the_audit_write(self):
        await self.crash_then_restart(self.audit, "write_batch", after=False)

    async def test_crash_after_the_audit_write_before_audit_recorded(self):
        # The replacement re-projects; the stable event ID deduplicates it.
        await self.crash_then_restart(self.audit, "write_batch", after=True)

    async def test_crash_after_audit_recorded(self):
        await self.crash_then_restart(self.service, "_write_release", after=True,
                                      when=lambda value: value["audit_recorded"])

    async def test_audit_failure_is_retried_by_recovery(self):
        request = await self.held_task()
        with patch.object(self.audit, "_write_safe_unlocked", side_effect=OSError("synthetic")):
            result = await self.decide(request)
        self.assertEqual(result["status"], "hold_released_state_differs")
        self.assertFalse(result["audit_recorded"])
        self.assertEqual(self.release_entries(), [])
        self.assertEqual(self.owned(request.task_id), [])
        self.assertEqual(self.service.health()["audit_projection_failures"], 1)
        self.assertFalse(self.service.receipt(request.task_id)["hold_release"]["audit_recorded"])
        await self.assert_settled_once(request, self.service)

    async def test_concurrent_finishers_settle_one_completion(self):
        request = await self.held_task()
        with patch.object(self.service.locks, "release_conflict_hold", side_effect=self.Crash):
            with self.assertRaises(self.Crash):
                await self.decide(request)
        replacement = self.build()
        started, resume = threading.Event(), threading.Event()
        original_release = self.service.locks.release_conflict_hold
        original_write = self.audit.write_batch
        writes = []

        def paused(**kwargs):
            started.set()
            if not resume.wait(10):
                raise AssertionError("first finisher was not resumed")
            return original_release(**kwargs)

        def counted(entries):
            writes.append(len(entries))
            return original_write(entries)

        with (patch.object(self.service.locks, "release_conflict_hold", side_effect=paused),
              patch.object(self.audit, "write_batch", side_effect=counted),
              ThreadPoolExecutor(max_workers=2) as pool):
            first = pool.submit(self.service._finish_hold_release, request.task_id)
            self.assertTrue(started.wait(10))
            second = pool.submit(replacement._finish_hold_release, request.task_id)
            time.sleep(0.3)
            self.assertFalse(second.done())  # serialized behind the first finisher
            resume.set()
            completed = (first.result(timeout=10), second.result(timeout=10))
        self.assertEqual(completed[0], completed[1])
        self.assertEqual(writes, [1])  # one projection, not one per finisher
        await self.assert_settled_once(request, replacement)

    async def test_without_an_audit_target_the_release_still_settles(self):
        self.audit = None
        self.service = self.build()
        request = await self.held_task()
        result = await self.decide(request)
        self.assertFalse(result["audit_recorded"])
        failures, reads = self.service.recovery_failures, self.transport.reads()
        for _ in range(3):
            self.clock.advance(30)
            await self.service.recover_once()
        self.assertEqual((self.service.recovery_failures, self.transport.reads()), (failures, reads))
        self.assertEqual(self.owned(request.task_id), [])

    async def test_audit_recorded_without_released_at_is_corrupt(self):
        request = await self.held_task()
        with patch.object(self.service.locks, "release_conflict_hold", side_effect=self.Crash):
            with self.assertRaises(self.Crash):
                await self.decide(request)
        path = self.service._release_path(request.task_id)
        value = json.loads(path.read_text())
        path.write_text(json.dumps({**value, "audit_recorded": True}))
        with self.assertRaisesRegex(FanRefusal, "fan_hold_release_corrupt"):
            self.service._load_release(request.task_id)
        self.clock.advance(30)
        await self.service.recover_once()
        self.assertEqual(self.service.recovery_failures, 1)
        self.assertEqual([item.conflict_hold for item in self.owned(request.task_id)], [True])
        self.assertEqual(self.release_entries(), [])


class RestartTests(ObservingFixture):
    delay = 20

    async def test_restart_mid_inline_poll_recovers_without_redispatch(self):
        entered, resume = asyncio.Event(), asyncio.Event()

        async def block(tool):
            if tool == "ha_get_state" and self.transport.writes and self.transport.reads() >= 3:
                entered.set()
                await resume.wait()

        self.transport.before = block
        request = self.request()
        self.telemetry.ordinary_fan_binding = digest(request.model_dump())
        pending = asyncio.create_task(self.service.control(request))
        await entered.wait()
        await self.service.close()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.transport.before = None
        replacement = self.build()
        await replacement.recover_once()
        self.assertFalse(replacement.receipt(request.task_id)["terminal"])  # claim/lease still valid
        passes = await self.sweep(request.task_id, service=replacement)
        self.assertEqual(passes[-1][2]["state"], "succeeded_verified", passes[-1][2])
        self.assertEqual(self.transport.writes, 1)
        self.assertEqual(self.owned(request.task_id, replacement), [])


class PowerInheritanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_power_verifies_a_slow_light_inline_with_the_same_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            clock, core = Clock(), Core()
            transport = PowerTransport("8.5.0")
            transport.mode = "ack_only"
            original = transport.execute_read
            dispatched = []

            async def execute_read(tool, args, **kwargs):
                if tool == "ha_call_service":
                    dispatched.append(clock())
                elif tool == "ha_get_state" and dispatched and clock() >= dispatched[0] + timedelta(seconds=4):
                    transport.states["light.synthetic"].update(state="on", last_updated="synthetic-late")
                return await original(tool, args, **kwargs)

            transport.execute_read = execute_read
            service = PowerService(directory, PowerProvider(transport, lambda *args: transport.authority),
                                   PowerCoreAuthority(core), now=clock, sleep=clock.sleep)
            self.addAsyncCleanup(service.close)
            telemetry, token = begin_request("synthetic-hamcp192-power")
            self.addCleanup(end_request, token)
            request = PowerRequest(entity_id="light.synthetic", action="turn_on",
                                   operation_id=f"{int(clock().timestamp())}-{uuid.uuid4().hex}")
            telemetry.ordinary_power_binding = power_digest(request.model_dump())
            receipt = await service.control(request)
            self.assertEqual(receipt["state"], "succeeded_verified", receipt)
            self.assertEqual(transport.writes, 1)
            self.assertEqual(service.executor().lock_timing.lease_seconds, 30)
            with self.assertRaisesRegex(Exception, "power_hold_release_authority_required"):
                await service.release_hold(request.task_id, record_generation=0, prepared_hash="0" * 64,
                                           hold_binding="entity:light.synthetic:1",
                                           reviewed_readback_hash="0" * 64,
                                           authorized_principal="authenticated_connector")
            # Power compares the one field it controls.
            self.assertEqual(service._state_comparison(request, {"state": "off"}),
                             [("state", "on", "off")])


if __name__ == "__main__":
    unittest.main()
