"""HAMCP-379: recovery containment for the typed write families.

A typed write (fan, power, helper value, device configuration) whose outcome is
uncertain is recovered by the 30 s sweep only. These tests drive that sweep on
virtual time against the families' own offline fakes and show, for each family:

- the three wedge paths settle to manual review within the recovery bound
  (path 1: Home Assistant reads outlast the sweep budget; path 2: a recovery
  pass dies between the lock transfer and the record update; path 3:
  get_execution_task polled against a live process);
- recovery never dispatches: dispatch_count stays 1 and the provider write
  count never rises;
- get_execution_task is a pure read;
- a wedged task's home_assistant:core lock blocks the other families until it
  settles, and the entity hold outlives the settlement;
- a record an older release filled to the event bound settles and still loads
  under the unchanged models;
- an executor without the flag behaves as before.

No Home Assistant, no network, no credentials: every provider is a test fake.
"""
import ast
import asyncio
import contextlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import functools
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests"), str(ROOT)]

import tests.test_typed_fan as fan_fixtures  # noqa: E402
import tests.test_typed_power as power_fixtures  # noqa: E402
import tests.test_typed_helper_value as helper_fixtures  # noqa: E402
import tests.test_hamcp297_device_configuration as device_fixtures  # noqa: E402
from ha_mcp_engineering.device_configuration.service import DEVICE_CONFIGURATION_OPERATIONS  # noqa: E402
from ha_mcp_engineering.errors import HomeAssistantTimeoutError, HomeAssistantUnavailableError  # noqa: E402
from ha_mcp_engineering.f3.executor import ExecutorTiming, SharedOperationExecutor  # noqa: E402
from ha_mcp_engineering.f3.locks import LockStorageError  # noqa: E402
from ha_mcp_engineering.f3.models import (  # noqa: E402
    MAX_EXECUTION_EVENTS, ExecutionRecord, LockHandle, LockOwner, LockToken)
from ha_mcp_engineering.fan.service import FAN_LOCK_TIMING, FAN_OPERATIONS  # noqa: E402
from ha_mcp_engineering.helper_value.service import HELPER_VALUE_OPERATIONS  # noqa: E402
from ha_mcp_engineering.power.service import POWER_OPERATIONS  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.tools.governance import get_execution_task  # noqa: E402

BASE = datetime(2026, 11, 10, 6, tzinfo=timezone.utc)
FAMILIES = ("fan", "power", "helper_value", "device_configuration")
OPERATIONS = {"fan": FAN_OPERATIONS, "power": POWER_OPERATIONS,
              "helper_value": HELPER_VALUE_OPERATIONS,
              "device_configuration": DEVICE_CONFIGURATION_OPERATIONS}
BOOKKEEPING = ("recovery_claimed", "recovery_locks_transferred", "execution_claim_yielded")
# The ruled bounds, restated here so a changed constant fails a test.
CLAIM_BOUND = 8
EVENT_BACKSTOP = 120
BOUND_REASON = "recovery_bound_reached"
DEADLINE_REASON = "post_dispatch_evidence_deadline_expired"
SWEEP_SECONDS = 30


class ProcessLoss(BaseException):
    """Synthetic process death: not an Exception, so no handler records it."""


class VirtualLoop(asyncio.SelectorEventLoop):
    """Timers advance virtual time instead of sleeping; file I/O stays real."""

    def __init__(self):
        super().__init__()
        self.virtual_now = 0.0
        real_select = self._selector.select

        def select(timeout=None):
            events = real_select(0)
            if not events and timeout:
                self.virtual_now += timeout
            return events

        self._selector.select = select

    def time(self):
        return self.virtual_now


class CountingCore:
    """The family's synthetic Core authority input, with every call counted."""

    def __init__(self, inner):
        self.inner, self.calls, self.hang_seconds = inner, 0, 0.0

    async def reconcile_once(self, trigger):
        self.calls += 1
        if self.hang_seconds:
            # The probe of Home Assistant takes this long and then fails, so
            # no Core authority is available to the read that asked for it.
            await asyncio.sleep(self.hang_seconds)
            self.inner.allowed = False
        return await self.inner.reconcile_once(trigger)

    def __getattr__(self, name):
        value = getattr(self.inner, name)
        if not callable(value):
            return value

        def counted(*args, **kwargs):
            self.calls += 1
            return value(*args, **kwargs)
        return counted


class Process:
    """One simulated add-on process for one family on a durable store root.

    A restart is a new Process on the same root: new provider fakes, a new
    service, no in-memory task. ``fail`` models Home Assistant reads only:
    ``hang`` waits ``hang_seconds`` (ha_timeout_seconds) and raises a timeout,
    ``refuse`` raises at once. The single write path is never altered by it.
    """

    def __init__(self, family, root, loop, *, applied=False):
        self.family, self.loop = family, loop
        self.fail, self.hang_seconds = None, 60.0
        self.read_attempts = 0
        getattr(self, "_build_" + family)(str(root), applied)
        self.operations = OPERATIONS[family]
        # Every adapter.dispatch call by this process, whatever it then does.
        self.dispatches = 0
        adapter, dispatch = self.service.adapter, self.service.adapter.dispatch

        async def counted(*args, **kwargs):
            self.dispatches += 1
            return await dispatch(*args, **kwargs)
        adapter.dispatch = counted

    def now(self):
        return BASE + timedelta(seconds=self.loop.time())

    def operation_id(self):
        return f"{int(self.now().timestamp())}-{uuid.uuid4().hex}"

    async def _read_fault(self):
        self.read_attempts += 1
        if self.fail == "refuse":
            raise HomeAssistantUnavailableError(details={"method": "GET", "endpoint_category": "synthetic"})
        if self.fail == "hang":
            await asyncio.sleep(self.hang_seconds)
            raise HomeAssistantTimeoutError(details={"method": "GET", "endpoint_category": "synthetic"})

    async def _before_upstream(self, tool):
        if tool != "ha_call_service":
            await self._read_fault()

    def _upstream(self, transport):
        transport.before = self._before_upstream
        self.transport = transport
        self.writes = lambda: transport.writes
        self.provider_calls = lambda: len(transport.calls)

    def _build_fan(self, root, applied):
        transport = fan_fixtures.Transport()
        if applied:
            transport.state.update(state="on", last_updated="synthetic-revision-2")
            transport.state["attributes"]["percentage"] = 50
        self._upstream(transport)
        self.core = CountingCore(fan_fixtures.Core())
        self.service = fan_fixtures.FanService(
            root, fan_fixtures.FanProvider(transport, lambda: transport.authority),
            fan_fixtures.FanCoreAuthority(self.core), now=self.now, sleep=asyncio.sleep)
        self.binding = "ordinary_fan_binding"
        self.entity = "fan.synthetic"
        self.make_request = lambda: fan_fixtures.FanRequest(
            entity_id="fan.synthetic", action="turn_on", percentage=50, operation_id=self.operation_id())
        self.digest = fan_fixtures.digest

    def _build_power(self, root, applied):
        transport = power_fixtures.PowerTransport("8.5.0")
        if applied:
            transport.states["light.synthetic"].update(state="on", last_updated="synthetic-revision-2")
        self._upstream(transport)
        self.core = CountingCore(fan_fixtures.Core())
        self.service = power_fixtures.PowerService(
            root, power_fixtures.PowerProvider(transport, lambda *args: transport.authority),
            power_fixtures.PowerCoreAuthority(self.core), now=self.now, sleep=asyncio.sleep)
        self.binding = "ordinary_power_binding"
        self.entity = "light.synthetic"
        self.make_request = lambda: power_fixtures.PowerRequest(
            entity_id="light.synthetic", action="turn_on", operation_id=self.operation_id())
        self.digest = power_fixtures.digest

    def _build_helper_value(self, root, applied):
        transport = helper_fixtures.HelperTransport("8.5.0")
        if applied:
            transport.states["input_boolean.synthetic"].update(state="on", last_updated="synthetic-revision-2")
        self._upstream(transport)
        self.core = CountingCore(helper_fixtures.SignedCore())

        async def dependency_reader(entity_id):
            return deepcopy(helper_fixtures.OPAQUE_ONLY)

        self.service = helper_fixtures.HelperService(
            root, helper_fixtures.HelperProvider(transport, lambda *args: transport.authority),
            helper_fixtures.HelperCoreAuthority(self.core), now=self.now, sleep=asyncio.sleep,
            dependency_reader=dependency_reader)
        self.binding = "ordinary_helper_value_binding"
        self.entity = "input_boolean.synthetic"
        self.make_request = lambda: helper_fixtures.HelperRequest(
            entity_id="input_boolean.synthetic", action="turn_on", value=None, operation_id=self.operation_id())
        self.digest = helper_fixtures.digest

    def _build_device_configuration(self, root, applied):
        process = self

        class HomeAssistant(device_fixtures.FakeHomeAssistant):
            async def command(self, payload):
                if payload["type"] != "call_service":
                    await process._read_fault()
                return await super().command(payload)

            async def request(self, method, path):
                await process._read_fault()
                return await super().request(method, path)

        peer = HomeAssistant()
        if applied:
            peer.entities[device_fixtures.SWITCH]["state"] = "on"
            peer.touch(device_fixtures.SWITCH)
        self.peer = peer
        self.writes = lambda: len(peer.writes)
        self.provider_calls = lambda: len(peer.commands) + len(peer.rest)
        self.core = CountingCore(device_fixtures.SignedCore())
        self.service = device_fixtures.DeviceConfigurationService(
            root, peer.gateway(), device_fixtures.DeviceConfigurationCoreAuthority(self.core),
            now=self.now, sleep=asyncio.sleep)
        self.binding = "ordinary_device_configuration_binding"
        self.entity = device_fixtures.SWITCH
        self.make_request = lambda: device_fixtures.DeviceConfigurationRequest(
            entity_id=device_fixtures.SWITCH, action="turn_on", operation_id=self.operation_id())
        self.digest = device_fixtures.digest

    def slow_reads(self, seconds):
        """From now on every Home Assistant read takes ``seconds`` and fails.

        ``seconds`` stands for ha_timeout_seconds. Device configuration reads
        Home Assistant directly. For the other three families the read bound
        by that timeout is the Core authority probe that precedes each
        observation (their provider reads are cut off upstream at 20 s).
        """
        if self.family == "device_configuration":
            self.fail, self.hang_seconds = "hang", seconds
        else:
            self.core.hang_seconds = seconds

    def counters(self):
        return {"writes": self.writes(), "dispatches": self.dispatches,
                "provider_calls": self.provider_calls(), "core_calls": self.core.calls,
                "read_attempts": self.read_attempts,
                "recovery_failures": self.service.recovery_failures}

    # -- the one provider write ------------------------------------------------

    def die_inside_dispatch(self):
        """The process dies inside the provider write, after the durable intent."""
        if self.family == "device_configuration":
            peer = self.peer

            def die(payload):
                peer.writes.append(deepcopy(payload))
                raise ProcessLoss()
            peer.call_service = die
            return
        transport, original = self.transport, self.transport.execute_read

        async def execute_read(tool, args, *, catalog_validator, before_dispatch, **kwargs):
            if tool != "ha_call_service":
                return await original(tool, args, catalog_validator=catalog_validator,
                                      before_dispatch=before_dispatch, **kwargs)

            async def die():
                await before_dispatch()  # the durable dispatch intent is committed here
                transport.writes += 1
                raise ProcessLoss()
            return await original(tool, args, catalog_validator=catalog_validator,
                                  before_dispatch=die, **kwargs)
        transport.execute_read = execute_read

    def acknowledge_without_effect(self):
        """The write is acknowledged but the entity never changes."""
        if self.family == "device_configuration":
            self.peer.mode = "ack_only"
        else:
            self.transport.mode = "ack_only"

    # -- observations ------------------------------------------------------------

    def record(self, task_id):
        return self.service.executions.get(task_id)

    def lock_rows(self, task_id=None):
        return sorted((row.key, row.mode.value if hasattr(row.mode, "value") else str(row.mode),
                       row.generation, row.task_id, bool(row.conflict_hold))
                      for row in self.service.locks.records()
                      if task_id is None or row.task_id == task_id)

    def new_executor_with_containment_off(self):
        """The executor exactly as plan-governed callers build it: no containment flag."""
        service = self.service
        return SharedOperationExecutor(
            lock_store=service.locks, execution_repository=service.executions,
            lock_timing=FAN_LOCK_TIMING, executor_timing=ExecutorTiming(180, 120, 6, 6),
            now=service.now, final_read_at_deadline=True)


def tree_digest(root):
    """Every file under the store root, by relative path and content."""
    out = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def event_types(record):
    return [event["event_type"] for event in record.events]


def codes(record, event_type=None):
    return [list(event["diagnostic_codes"]) for event in record.events
            if event_type is None or event["event_type"] == event_type]


def virtual(test):
    """Run an async test body on the test's virtual-time loop."""
    @functools.wraps(test)
    def run(self):
        self.loop.run_until_complete(test(self))
    return run


class Scenario(unittest.TestCase):
    family = None

    def setUp(self):
        base = os.environ.get("TMPDIR") or None
        self.tmp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.loop = VirtualLoop()
        asyncio.set_event_loop(self.loop)
        self.addCleanup(asyncio.set_event_loop, None)
        self.addCleanup(self.loop.close)
        self.telemetry, token = begin_request("synthetic-HAMCP-379")
        self.addCleanup(end_request, token)

    def t(self):
        return round(self.loop.time(), 3)

    def process(self, family=None, *, applied=False, root=None):
        return Process(family or self.family, root or self.root, self.loop, applied=applied)

    def authorize(self, process, request):
        """Bind the connector authorization of this test request to ``request``."""
        setattr(self.telemetry, process.binding, process.digest(request.model_dump()))
        return request

    def request(self, process):
        return self.authorize(process, process.make_request())

    async def crash_after_dispatch_intent(self, process=None):
        """Leave a durable post-intent, non-terminal task whose owner is gone."""
        process = process or self.process()
        process.die_inside_dispatch()
        request = self.request(process)
        with self.assertRaises(ProcessLoss):
            await process.service.control(request)
        self.assertEqual(process.writes(), 1)
        return request

    async def tool_read(self, task_id, *processes):
        """The get_execution_task tool, routed to these processes' services."""
        with contextlib.ExitStack() as stack:
            for process in processes:
                stack.enter_context(patch.object(process.operations, "service", process.service))
            return json.loads(await get_execution_task(task_id))

    # -- flows ---------------------------------------------------------------------

    async def die_between_lock_transfer_and_record_update(self, task_id, *, applied=False):
        """Path 2: a recovery pass moves the lock generations and then dies.

        The lock store then names generations the execution record does not.
        """
        dying = self.process(applied=applied)
        dying.service.executor = dying.new_executor_with_containment_off

        def die(*args, **kwargs):
            raise ProcessLoss()
        dying.service.executions.replace_recovery_locks = die
        with self.assertRaises(ProcessLoss):
            await dying.service.recover_once()
        record = dying.record(task_id)
        self.assertFalse(record.terminal)
        recorded = sorted(token["generation"] for token in record.lock_tokens)
        stored = sorted(row[2] for row in dying.lock_rows(task_id))
        self.assertEqual(len(stored), 3)
        self.assertFalse(set(recorded) & set(stored))
        self.assertEqual((dying.writes(), dying.dispatches), (0, 0))

    async def observing_live_task(self, process=None):
        """A healthy in-flight task in a live process.

        The write was acknowledged, the entity has not changed yet, and the
        control call returned while the task is still being observed.
        """
        process = process or self.process()
        process.acknowledge_without_effect()
        request = self.request(process)
        await process.service.control(request)
        record = process.record(request.task_id)
        self.assertFalse(record.terminal)
        self.assertEqual((process.writes(), process.dispatches, record.dispatch_count), (1, 1, 1))
        return process, request

    def supervise(self, process):
        """The application's recovery loop: a startup pass, then one every 30 s."""
        async def run():
            while True:
                await process.service.recover_once()
                await asyncio.sleep(SWEEP_SECONDS)
        task = self.loop.create_task(run())

        def stop():
            task.cancel()
            self.loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
        self.addCleanup(stop)

    async def sweep(self, process, task_id, *, limit, expect=None, rows=3):
        """Run the recovery loop in the foreground until the task is terminal.

        After every pass: this process has dispatched nothing beyond ``expect``,
        the durable dispatch count is 1, the record fits the event bound, and
        a task that is not terminal still holds all of its locks.
        """
        expect = expect or {"writes": 0, "dispatches": 0}
        stop = self.loop.time() + limit
        passes = most = 0
        while True:
            await process.service.recover_once()
            passes += 1
            record = process.record(task_id)
            most = max(most, len(record.events))
            self.assertEqual((process.writes(), process.dispatches),
                             (expect["writes"], expect["dispatches"]))
            self.assertEqual(record.dispatch_count, 1)
            self.assertLessEqual(len(record.events), MAX_EXECUTION_EVENTS)
            if record.terminal:
                return {"passes": passes, "most_events": most}
            held = process.lock_rows(task_id)
            self.assertEqual((len(held), any(row[4] for row in held)), (rows, False))
            if self.loop.time() >= stop:
                self.fail(f"not settled after {limit} s: {passes} passes, {len(record.events)} "
                          f"events, state {record.state}, last {event_types(record)[-4:]}")
            await asyncio.sleep(SWEEP_SECONDS)

    async def poll(self, process, task_id, *, every, limit):
        """get_execution_task at a fixed interval until it reports a terminal task."""
        stop = self.loop.time() + limit
        polls = 0
        while self.loop.time() < stop:
            out = await self.tool_read(task_id, process)
            polls += 1
            self.assertEqual(out.get("data", {}).get("task_id"), task_id,
                             f"poll {polls} at {self.t()} s: {json.dumps(out)[:300]}")
            if out["data"]["terminal"]:
                break
            await asyncio.sleep(every)
        return polls

    # -- assertions ----------------------------------------------------------------

    def assert_entity_hold_only(self, process, task_id):
        """Manual review keeps the entity fence and nothing else of the task."""
        self.assertEqual([(key, hold) for key, _, _, _, hold in process.lock_rows(task_id)],
                         [("entity:" + process.entity, True)])

    def assert_manual_review(self, process, task_id, reason):
        record = process.record(task_id)
        self.assertEqual((record.terminal, record.normalized_outcome, record.dispatch_count),
                         (True, "manual_review_required", 1))
        self.assertEqual(record.evidence.get("manual_review_reason_code"), reason)
        self.assert_entity_hold_only(process, task_id)
        return record

    def assert_bound_settlement(self, process, task_id, *, dropped=0):
        record = self.assert_manual_review(process, task_id, BOUND_REASON)
        last = record.events[-1]
        expected = [BOUND_REASON] + ([f"recovery_bookkeeping_dropped_{dropped}"] if dropped else [])
        self.assertEqual((last["event_type"], sorted(last["diagnostic_codes"])),
                         ("verification_recorded", sorted(expected)))
        return record

    async def assert_stays_settled(self, process, task_id):
        """Later sweeps and reads change nothing about a settled task."""
        before = (tree_digest(self.root), process.lock_rows())
        for _ in range(6):
            await asyncio.sleep(SWEEP_SECONDS)
            await process.service.recover_once()
        await asyncio.sleep(7 * 86400)
        await process.service.recover_once()
        out = await self.tool_read(task_id, process)
        self.assertTrue(out["data"]["terminal"])
        self.assertEqual((tree_digest(self.root), process.lock_rows()), before)
        self.assertEqual((process.writes(), process.dispatches), (0, 0))


def claims(record):
    return event_types(record).count("recovery_claimed")


def all_codes(record):
    return [code for event in record.events for code in event["diagnostic_codes"]]


class AuditCollector:
    """Stands in for the audit logger: keeps every projected batch."""

    def __init__(self):
        self.batches = []

    def write_batch(self, entries):
        self.batches.append(deepcopy(entries))
        return len(entries)

    def entries(self, event):
        return [entry for batch in self.batches for entry in batch if entry["event"] == event]


def completed_observation(event):
    return (event["event_type"] == "observation_recorded"
            and "observation_failed" not in event["diagnostic_codes"])


def occurred(event):
    return datetime.fromisoformat(event["occurred_at"])


def seconds_between(first, second):
    return abs((occurred(second) - occurred(first)).total_seconds())


def counted_claims(events):
    """The amended ruling 3 count, restated (Q6.1, Q6.2).

    After the last completed observation, a recovery claim counts when it is
    the first, or at least 30 s (absolute distance) from the previous counted
    claim. A claim that does not count does not move that start point.
    """
    count, anchor = 0, None
    for event in events:
        if completed_observation(event):
            count, anchor = 0, None
        elif event["event_type"] == "recovery_claimed" and (
                anchor is None or seconds_between(anchor, event) >= SWEEP_SECONDS):
            count, anchor = count + 1, event
    return count


def claim_indexes(record):
    return [index for index, event in enumerate(record.events)
            if event["event_type"] == "recovery_claimed"]


def claim_sequences(record):
    return [record.events[index]["sequence"] for index in claim_indexes(record)]


class ContainmentContract:
    """The HAMCP-379 contract, run once per typed write family."""

    # -- path 1: Home Assistant reads outlast the 45 s sweep budget -------------------

    async def path_1(self, seconds):
        request = await self.crash_after_dispatch_intent()
        await asyncio.sleep(5)  # the add-on restarts
        process = self.process()
        process.slow_reads(seconds)
        started = self.loop.time()
        run = await self.sweep(process, request.task_id, limit=1500)
        return process, request.task_id, dict(run, seconds=self.loop.time() - started)

    @virtual
    async def test_path_1_settles_at_the_claim_bound_at_the_default_timeout(self):
        process, task_id, run = await self.path_1(60.0)
        record = self.assert_bound_settlement(process, task_id)
        self.assertEqual(claims(record), CLAIM_BOUND)
        self.assertLess(run["most_events"], 30)
        self.assertLessEqual(run["seconds"], 1200)
        self.assertNotIn("observation_recorded", event_types(record))
        await self.assert_stays_settled(process, task_id)

    @virtual
    async def test_path_1_settles_at_the_claim_bound_at_the_maximum_timeout(self):
        process, task_id, run = await self.path_1(300.0)
        record = self.assert_bound_settlement(process, task_id)
        self.assertEqual(claims(record), CLAIM_BOUND)
        self.assertLess(run["most_events"], 30)
        self.assertLessEqual(run["seconds"], 1200)

    @virtual
    async def test_path_1_settles_at_the_minimum_timeout(self):
        # Reads that fail fast never reach the bound: the first pass that can
        # read settles the task through the existing failed-observation exit.
        process, task_id, run = await self.path_1(0.001)
        record = self.assert_manual_review(process, task_id, "observation_failed_exhausted")
        self.assertLess(run["most_events"], 30)
        self.assertLessEqual(run["seconds"], 1200)
        self.assertLess(claims(record), CLAIM_BOUND)

    # -- path 2: a recovery pass dies between the lock transfer and the record --------

    async def path_2(self, seconds=0.0, *, applied=False):
        request = await self.crash_after_dispatch_intent()
        await asyncio.sleep(205)  # the dead owner's claim and lock lease have expired
        await self.die_between_lock_transfer_and_record_update(request.task_id, applied=applied)
        process = self.process(applied=applied)
        if seconds:
            process.slow_reads(seconds)
        started = self.loop.time()
        run = await self.sweep(process, request.task_id, limit=1500)
        return process, request.task_id, dict(run, seconds=self.loop.time() - started)

    @virtual
    async def test_path_2_with_healthy_reads_reaches_the_evidence_deadline_exit(self):
        process, task_id, run = await self.path_2()
        record = self.assert_manual_review(process, task_id, DEADLINE_REASON)
        self.assertIn("observation_recorded", event_types(record))
        self.assertLess(run["most_events"], 30)
        self.assertLessEqual(run["seconds"], 300)
        await self.assert_stays_settled(process, task_id)

    @virtual
    async def test_path_2_verifies_a_write_that_was_applied(self):
        process, task_id, run = await self.path_2(applied=True)
        record = process.record(task_id)
        self.assertEqual((record.terminal, record.normalized_outcome, record.dispatch_count),
                         (True, "succeeded_verified", 1))
        self.assertEqual(process.lock_rows(), [])
        self.assertLess(run["most_events"], 30)

    @virtual
    async def test_path_2_settles_at_the_claim_bound_at_the_default_timeout(self):
        process, task_id, run = await self.path_2(60.0)
        record = self.assert_bound_settlement(process, task_id)
        self.assertEqual(claims(record), CLAIM_BOUND)
        self.assertLess(run["most_events"], 30)
        self.assertLessEqual(run["seconds"], 1200)
        await self.assert_stays_settled(process, task_id)

    @virtual
    async def test_path_2_settles_at_the_claim_bound_at_the_maximum_timeout(self):
        process, task_id, run = await self.path_2(300.0)
        record = self.assert_bound_settlement(process, task_id)
        self.assertEqual(claims(record), CLAIM_BOUND)
        self.assertLess(run["most_events"], 30)
        self.assertLessEqual(run["seconds"], 1200)

    @virtual
    async def test_path_2_settles_at_the_minimum_timeout(self):
        process, task_id, run = await self.path_2(0.001)
        record = self.assert_manual_review(process, task_id, "observation_failed_exhausted")
        self.assertLess(run["most_events"], 30)
        self.assertLessEqual(run["seconds"], 300)
        self.assertLess(claims(record), CLAIM_BOUND)

    @virtual
    async def test_path_2_storage_error_in_the_record_update_is_retried(self):
        # The same gap without a process death: the record update fails once.
        request = await self.crash_after_dispatch_intent()
        await asyncio.sleep(205)
        process = self.process()
        update, failures = process.service.executions.replace_recovery_locks, []

        def fail_once(*args, **kwargs):
            if not failures:
                failures.append(self.t())
                from ha_mcp_engineering.f3.persistence import ExecutionStorageError
                raise ExecutionStorageError("synthetic")
            return update(*args, **kwargs)
        process.service.executions.replace_recovery_locks = fail_once
        run = await self.sweep(process, request.task_id, limit=1500)
        self.assertEqual(len(failures), 1)
        self.assert_manual_review(process, request.task_id, DEADLINE_REASON)
        self.assertLess(run["most_events"], 30)

    # -- path 3: get_execution_task polled against a live process ---------------------

    async def path_3(self, seconds=0.0, *, every, limit):
        process = self.process()
        self.supervise(process)
        await asyncio.sleep(1)
        process, request = await self.observing_live_task(process)
        if seconds:
            process.slow_reads(seconds)
        polls = await self.poll(process, request.task_id, every=every, limit=limit)
        record = process.record(request.task_id)
        self.assertEqual((process.writes(), process.dispatches, record.dispatch_count), (1, 1, 1))
        return process, request.task_id, polls

    @virtual
    async def test_path_3_polling_every_second_settles_and_every_poll_returns_the_task(self):
        process, task_id, polls = await self.path_3(every=1.0, limit=420)
        record = self.assert_manual_review(process, task_id, DEADLINE_REASON)
        self.assertGreater(polls, 150)
        self.assertLess(len(record.events), EVENT_BACKSTOP // 2)

    @virtual
    async def test_path_3_settles_at_the_claim_bound_at_the_maximum_timeout(self):
        process, task_id, polls = await self.path_3(300.0, every=2.0, limit=1500)
        record = self.assert_bound_settlement(process, task_id)
        self.assertEqual(claims(record), CLAIM_BOUND)
        self.assertGreater(polls, 150)
        self.assertLess(len(record.events), EVENT_BACKSTOP // 2)

    @virtual
    async def test_path_3_settles_at_the_minimum_timeout(self):
        process, task_id, polls = await self.path_3(0.001, every=1.0, limit=420)
        record = self.assert_manual_review(process, task_id, "observation_failed_exhausted")
        self.assertLess(len(record.events), EVENT_BACKSTOP // 2)

    # -- get_execution_task is a pure read ----------------------------------------------

    async def assert_pure_read(self, process, task_id):
        record = process.record(task_id)
        before = (tree_digest(self.root), process.lock_rows(), process.counters())
        for wait in (0, 1, 29, 200):
            await asyncio.sleep(wait)
            started = self.loop.time()
            out = await self.tool_read(task_id, process)
            self.assertEqual(self.loop.time(), started)  # nothing was awaited on
            self.assertIn("data", out, json.dumps(out)[:300])
            self.assertEqual((out["data"]["task_id"], out["data"]["terminal"]),
                             (task_id, record.terminal))
            self.assertEqual((tree_digest(self.root), process.lock_rows(), process.counters()),
                             before)

    @virtual
    async def test_read_of_a_task_awaiting_recovery_changes_nothing(self):
        request = await self.crash_after_dispatch_intent()
        await asyncio.sleep(205)
        await self.assert_pure_read(self.process(), request.task_id)

    @virtual
    async def test_read_of_a_live_observing_task_changes_nothing(self):
        process, request = await self.observing_live_task()
        await self.assert_pure_read(process, request.task_id)

    @virtual
    async def test_read_of_a_settled_task_changes_nothing(self):
        process, task_id, _ = await self.path_2(60.0)
        await self.assert_pure_read(process, task_id)

    @virtual
    async def test_read_of_a_verified_task_changes_nothing(self):
        process = self.process()
        request = self.request(process)
        await process.service.control(request)
        self.assertEqual(process.record(request.task_id).normalized_outcome, "succeeded_verified")
        await self.assert_pure_read(process, request.task_id)

    # -- records written before containment ---------------------------------------------

    async def old_writer_record(self, events):
        """Path 2 as the executor without containment writes it, up to ``events``."""
        request = await self.crash_after_dispatch_intent()
        task_id = request.task_id
        await asyncio.sleep(205)
        await self.die_between_lock_transfer_and_record_update(task_id)
        old = self.process()
        old.service.executor = old.new_executor_with_containment_off
        self.assertEqual(len(old.record(task_id).events), 5)
        if events % 2 == 0 and events < MAX_EXECUTION_EVENTS:
            # One pass that dies after its claim shifts the count by one.
            release = old.service.executions.yield_claim

            def die(*args, **kwargs):
                raise ProcessLoss()
            old.service.executions.yield_claim = die
            await asyncio.sleep(121)  # the dead pass's claim has expired
            with self.assertRaises(ProcessLoss):
                await old.service.recover_once()
            old.service.executions.yield_claim = release
            await asyncio.sleep(121)
        while len(old.record(task_id).events) < events:
            # Every pass claims and yields; at 127 the yield no longer fits.
            await old.service.recover_once()
            await asyncio.sleep(1)
        record = old.record(task_id)
        self.assertEqual((len(record.events), record.terminal, record.dispatch_count),
                         (events, False, 1))
        self.assertEqual(set(event_types(record)[5:]), {"recovery_claimed", "execution_claim_yielded"}
                         if events > 6 else {"recovery_claimed"})
        self.assertEqual((old.writes(), old.dispatches), (0, 0))
        await asyncio.sleep(121)  # the last claim has expired
        return old, task_id

    async def settle_old_writer_record(self, events, dropped):
        old, task_id = await self.old_writer_record(events)
        before = old.record(task_id)
        process = self.process()
        await self.sweep(process, task_id, limit=600)
        record = self.assert_bound_settlement(process, task_id, dropped=dropped)
        # It loads and round-trips under the unchanged models.
        self.assertLessEqual(len(record.events), MAX_EXECUTION_EVENTS)
        self.assertEqual(ExecutionRecord.from_dict(record.to_dict()).to_dict(), record.to_dict())
        self.assertEqual([event["sequence"] for event in record.events],
                         list(range(1, len(record.events) + 1)))

        def evidence(item):
            return [{key: value for key, value in event.items() if key != "sequence"}
                    for event in item.events if event["event_type"] not in BOOKKEEPING]

        def bookkeeping(item):
            return [{key: value for key, value in event.items() if key != "sequence"}
                    for event in item.events if event["event_type"] in BOOKKEEPING]
        # Every event that is not recovery bookkeeping survives, in order.
        self.assertEqual(evidence(record)[:-1], evidence(before))
        self.assertEqual(record.events[-1]["event_type"], "verification_recorded")
        if dropped:
            # The newest bookkeeping events went; one claim names how many.
            kept = bookkeeping(record)
            self.assertEqual(kept[:-1], bookkeeping(before)[:-dropped])
            self.assertEqual((kept[-1]["event_type"], sorted(kept[-1]["diagnostic_codes"])),
                             ("recovery_claimed", sorted(["redispatch_prohibited",
                                                          f"recovery_bookkeeping_dropped_{dropped}"])))
            self.assertEqual(len(record.events), len(before.events) - dropped + 2)
        else:
            self.assertEqual(record.events[:-1], before.events)
        def intent(item):
            # The lock transfer rewrites the recorded generations, nothing else.
            return {key: value for key, value in item.dispatch_intent.items()
                    if key != "lock_tokens"}
        self.assertEqual((record.dispatch_count, record.observation_attempts, intent(record)),
                         (before.dispatch_count, before.observation_attempts, intent(before)))
        self.assertEqual(sorted(token["generation"] for token in record.lock_tokens),
                         sorted(token["generation"]
                                for token in record.dispatch_intent["lock_tokens"]))
        await self.assert_stays_settled(process, task_id)

    @virtual
    async def test_old_writer_record_at_128_events_settles_after_dropping_two(self):
        await self.settle_old_writer_record(128, dropped=2)

    @virtual
    async def test_old_writer_record_at_127_events_settles_without_dropping(self):
        await self.settle_old_writer_record(127, dropped=0)

    @virtual
    async def test_old_writer_record_at_126_events_settles_without_dropping(self):
        await self.settle_old_writer_record(126, dropped=0)

    @virtual
    async def test_read_returns_a_record_at_128_events(self):
        old, task_id = await self.old_writer_record(128)
        await self.assert_pure_read(old, task_id)
        out = await self.tool_read(task_id, old)
        self.assertEqual((out["data"]["task_id"], out["data"]["terminal"],
                          out["data"]["provider_attempt_count"]), (task_id, False, 1))

    @virtual
    async def test_record_written_under_containment_never_needs_trimming(self):
        from ha_mcp_engineering.f3.persistence import DurableExecutionRepository
        reserve, results = DurableExecutionRepository._reserve_settlement_events, []

        def recorded(record):
            results.append((len(record.events), reserve(record)))
            return results[-1][1]
        with patch.object(DurableExecutionRepository, "_reserve_settlement_events",
                          staticmethod(recorded)):
            process, task_id, run = await self.path_2(300.0)
            for _ in range(120):  # an hour more of sweeps after the settlement
                await asyncio.sleep(SWEEP_SECONDS)
                await process.service.recover_once()
        record = self.assert_bound_settlement(process, task_id)
        # Every claim but the dead pass's own went through the reservation.
        self.assertEqual(len(results), CLAIM_BOUND - 1)
        self.assertEqual({dropped for _, dropped in results}, {0})
        self.assertLess(max(size for size, _ in results), EVENT_BACKSTOP)
        self.assertFalse([code for code in all_codes(record) if "dropped" in code])

    # -- counted claims (amended ruling 3: Q2, Q6.1-6.4) --------------------------------

    def collect_audit(self, process):
        """Capture the family's audit projection of every record write."""
        collector = AuditCollector()
        process.service.executions.audit = collector
        return collector

    def projected_claims(self, process, collector):
        event = process.service.executions.kind + "_recovery_claimed"
        return collector.entries(event)

    async def repeated_control_calls(self, calls):
        """``calls`` identical control calls inside one 30 s window on a live task."""
        process, request = await self.observing_live_task()
        collector = self.collect_audit(process)
        task_id = request.task_id
        before = process.record(task_id)
        started = self.loop.time()
        for _ in range(calls):
            await process.service.control(request)
            await asyncio.sleep(25.0 / calls)
        self.assertLess(self.loop.time() - started, SWEEP_SECONDS)
        record = process.record(task_id)
        self.assertEqual((record.terminal, record.task_state), (False, "observing"))
        self.assertEqual((process.writes(), process.dispatches, record.dispatch_count), (1, 1, 1))
        self.assertEqual(record.claim_generation, before.claim_generation + calls)
        self.assertLessEqual(len(record.events), EVENT_BACKSTOP + 3)
        # One counted claim, and one yield after it, whatever the number of calls.
        self.assertEqual(record.events[:len(before.events)], before.events)
        self.assertEqual(event_types(record)[len(before.events):],
                         ["recovery_claimed", "execution_claim_yielded"])
        self.assertEqual(counted_claims(record.events), 1)
        held = process.lock_rows(task_id)
        self.assertEqual((len(held), any(row[4] for row in held)), (3, False))
        projected = self.projected_claims(process, collector)
        self.assertEqual(sorted({entry["event_sequence"] for entry in projected}),
                         claim_sequences(record))
        return process, request

    @virtual
    async def test_an_ordinary_recovery_pass_records_all_five_events(self):
        """U3: containment suppresses no event of an ordinary pass.

        The first pass after the dead owner's claim expires claims, transfers
        the locks, observes, verifies (not yet verified) and yields, and each
        of the five is recorded once. The next pass, after that completed
        observation, records all five again.
        """
        five = ["recovery_claimed", "recovery_locks_transferred", "observation_recorded",
                "verification_recorded", "execution_claim_yielded"]
        request = await self.crash_after_dispatch_intent()
        task_id = request.task_id
        await asyncio.sleep(121)  # the dead owner's claim has expired
        process = self.process()
        for _ in range(2):
            before = process.record(task_id)
            await process.service.recover_once()
            record = process.record(task_id)
            self.assertEqual(record.events[:len(before.events)], before.events)
            self.assertEqual(event_types(record)[len(before.events):], five)
            # The completed observation after the claim resets the count.
            self.assertEqual(counted_claims(record.events), 0)
            self.assertFalse(record.terminal)
            self.assertEqual((process.writes(), process.dispatches, record.dispatch_count),
                             (0, 0, 1))
            held = process.lock_rows(task_id)
            self.assertEqual((len(held), any(row[4] for row in held)), (3, False))
            await asyncio.sleep(SWEEP_SECONDS)

    @virtual
    async def test_8_identical_control_calls_in_one_window_do_not_settle_a_healthy_task(self):
        await self.repeated_control_calls(8)

    @virtual
    async def test_100_identical_control_calls_in_one_window_do_not_settle_a_healthy_task(self):
        process, request = await self.repeated_control_calls(100)
        # The task still settles normally once its observation can run; the
        # live process's one write is the original dispatch.
        await self.sweep(process, request.task_id, limit=600,
                         expect={"writes": 1, "dispatches": 1})
        self.assert_manual_review(process, request.task_id, DEADLINE_REASON)

    @virtual
    async def test_a_completed_observation_resets_the_claim_count(self):
        process, request = await self.repeated_control_calls(8)
        task_id = request.task_id
        await asyncio.sleep(SWEEP_SECONDS)  # the observing pass's lock lease lapses
        await process.service.control(request)  # this call observes
        record = process.record(task_id)
        self.assertFalse(record.terminal)
        self.assertTrue(completed_observation(record.events[-3]), event_types(record)[-4:])
        self.assertEqual(counted_claims(record.events), 0)
        await asyncio.sleep(1)
        await process.service.control(request)
        record = process.record(task_id)
        # 1 s after the previous counted claim, and it counts: the first since that observation.
        self.assertEqual(event_types(record)[-2:], ["recovery_claimed", "execution_claim_yielded"])
        self.assertLess(seconds_between(*[record.events[i] for i in claim_indexes(record)[-2:]]),
                        SWEEP_SECONDS)
        self.assertEqual(counted_claims(record.events), 1)
        size = len(record.events)
        await asyncio.sleep(1)
        await process.service.control(request)
        self.assertEqual(len(process.record(task_id).events), size)

    @virtual
    async def test_a_failed_observation_does_not_reset_the_claim_count(self):
        process, request = await self.repeated_control_calls(8)
        task_id = request.task_id
        await asyncio.sleep(SWEEP_SECONDS)
        process.fail = "refuse"  # the next read raises at once
        await process.service.control(request)
        record = process.record(task_id)
        self.assertFalse(record.terminal)
        observations = [event for event in record.events if event["event_type"] == "observation_recorded"]
        self.assertIn("observation_failed", observations[-1]["diagnostic_codes"])
        self.assertEqual(counted_claims(record.events), 2)
        size = len(record.events)
        await asyncio.sleep(1)
        await process.service.control(request)
        # Inside the window of the last counted claim: nothing appended.
        self.assertEqual(len(process.record(task_id).events), size)
        self.assertEqual(counted_claims(process.record(task_id).events), 2)

    @virtual
    async def test_the_audit_projection_shows_each_counted_claim(self):
        request = await self.crash_after_dispatch_intent()
        await asyncio.sleep(5)
        process = self.process()
        collector = self.collect_audit(process)
        process.slow_reads(60.0)
        await self.sweep(process, request.task_id, limit=1500)
        record = self.assert_bound_settlement(process, request.task_id)
        self.assertEqual(counted_claims(record.events), CLAIM_BOUND)
        self.assertEqual(claims(record), CLAIM_BOUND)
        projected = self.projected_claims(process, collector)
        self.assertEqual(sorted({entry["event_sequence"] for entry in projected}),
                         claim_sequences(record))
        self.assertEqual(len({entry["audit_event_id"] for entry in projected}), CLAIM_BOUND)
        times = sorted({entry["timestamp"] for entry in projected})
        self.assertEqual(times, [record.events[i]["occurred_at"] for i in claim_indexes(record)])
        self.assertTrue(all(entry["read_only_recovery"] for entry in projected))

    @virtual
    async def test_old_writer_claims_inside_one_window_count_once(self):
        # The executor without the flag claims about once a second here.
        old, task_id = await self.old_writer_record(41)
        before = old.record(task_id)
        from ha_mcp_engineering.f3.persistence import consecutive_recovery_claims
        self.assertGreaterEqual(claims(before), CLAIM_BOUND)
        self.assertEqual(consecutive_recovery_claims(before.events), counted_claims(before.events))
        counted = counted_claims(before.events)
        self.assertLess(counted, CLAIM_BOUND)
        process = self.process()
        process.slow_reads(60.0)
        await self.sweep(process, task_id, limit=1500)
        record = self.assert_bound_settlement(process, task_id)
        self.assertEqual(counted_claims(record.events), CLAIM_BOUND)
        self.assertEqual(claims(record) - claims(before), CLAIM_BOUND - counted)
        self.assertEqual(record.events[:len(before.events)], before.events)

    # -- executors without the flag ---------------------------------------------------------

    @virtual
    async def test_executor_without_containment_still_fills_the_record(self):
        # The plan-governed executors do not pass the flag; path 2 is unchanged for them.
        request = await self.crash_after_dispatch_intent()
        task_id = request.task_id
        await asyncio.sleep(205)
        await self.die_between_lock_transfer_and_record_update(task_id)
        process = self.process()
        process.service.executor = process.new_executor_with_containment_off
        for _ in range(240):  # two hours of sweeps
            await process.service.recover_once()
            await asyncio.sleep(SWEEP_SECONDS)
        record = process.record(task_id)
        self.assertEqual((len(record.events), record.terminal, record.dispatch_count),
                         (MAX_EXECUTION_EVENTS, False, 1))
        self.assertNotIn(BOUND_REASON, all_codes(record))
        self.assertFalse([code for code in all_codes(record) if "dropped" in code])
        self.assertEqual(len(process.lock_rows(task_id)), 3)
        self.assertEqual((process.writes(), process.dispatches), (0, 0))

    async def normal_flows(self, *, containment):
        """Three ordinary settlements, with and without the flag."""
        def build(**kwargs):
            process = self.process(root=root, **kwargs)
            if not containment:
                process.service.executor = process.new_executor_with_containment_off
            return process

        def shape(process, task_id):
            record = process.record(task_id)
            return {"events": list(zip(event_types(record), codes(record))),
                    "outcome": (record.state, record.task_state, record.normalized_outcome,
                                record.terminal, record.dispatch_count,
                                record.observation_attempts, record.verification_attempts),
                    "evidence": sorted(record.evidence),
                    "locks": [(key.split(":")[0], mode, hold)
                              for key, mode, _, _, hold in process.lock_rows(task_id)]}
        out = {}
        root = self.root / f"verified-{containment}"
        process = build()
        request = self.request(process)
        await process.service.control(request)
        out["verified"] = shape(process, request.task_id)

        root = self.root / f"deadline-{containment}"
        process, request = await self.observing_live_task(build())
        await self.sweep(process, request.task_id, limit=600,
                         expect={"writes": 1, "dispatches": 1})
        out["deadline"] = shape(process, request.task_id)

        root = self.root / f"crash-{containment}"
        request = await self.crash_after_dispatch_intent(build())
        await asyncio.sleep(205)
        process = build(applied=True)
        await self.sweep(process, request.task_id, limit=600)
        out["crash"] = shape(process, request.task_id)
        return out

    @virtual
    async def test_normal_settlement_is_the_same_with_and_without_containment(self):
        without = await self.normal_flows(containment=False)
        self.loop.virtual_now = 0.0
        contained = await self.normal_flows(containment=True)
        self.assertEqual(contained, without)
        self.assertEqual(contained["verified"]["outcome"][2], "succeeded_verified")
        self.assertEqual(contained["deadline"]["outcome"][2], "manual_review_required")
        self.assertEqual(contained["crash"]["outcome"][2], "succeeded_verified")
        self.assertEqual(contained["verified"]["locks"], [])
        self.assertEqual(contained["deadline"]["locks"], [("entity", "exclusive", True)])
        self.assertEqual(contained["crash"]["locks"], [])

    # -- locks -------------------------------------------------------------------------------

    @virtual
    async def test_wedged_core_lock_blocks_the_other_families_until_settlement(self):
        request = await self.crash_after_dispatch_intent()
        task_id = request.task_id
        await asyncio.sleep(205)
        await self.die_between_lock_transfer_and_record_update(task_id)
        await asyncio.sleep(31)  # the dead pass's lock lease has expired
        others = [family for family in FAMILIES if family != self.family]
        for family in others:
            blocked = self.process(family)
            attempt = self.request(blocked)
            await blocked.service.control(attempt)
            record = blocked.record(attempt.task_id)
            self.assertEqual((record.terminal, record.task_state, record.dispatch_count),
                             (True, "failed_pre_dispatch", 0), family)
            self.assertIn("lock_conflict", all_codes(record))
            self.assertEqual((blocked.writes(), blocked.dispatches,
                              blocked.lock_rows(attempt.task_id)), (0, 0, []))
        process = self.process()
        await self.sweep(process, task_id, limit=600)
        self.assert_manual_review(process, task_id, DEADLINE_REASON)
        for family in others:
            free = self.process(family)
            attempt = self.request(free)
            await free.service.control(attempt)
            record = free.record(attempt.task_id)
            self.assertEqual((record.normalized_outcome, record.dispatch_count, free.writes()),
                             ("succeeded_verified", 1, 1), family)
        # The settled task's entity stays fenced for its own family.
        self.assert_entity_hold_only(process, task_id)
        fenced = self.process()
        attempt = self.request(fenced)
        await fenced.service.control(attempt)
        record = fenced.record(attempt.task_id)
        self.assertEqual((record.terminal, record.task_state, record.normalized_outcome,
                          record.dispatch_count), (True, "failed_pre_dispatch", "lock_conflict", 0))
        self.assertEqual((fenced.writes(), fenced.dispatches), (0, 0))
        # Repeating the settled request returns its receipt and writes nothing.
        receipt = await fenced.service.control(self.authorize(fenced, request))
        self.assertEqual((receipt["task_id"], receipt["terminal"], receipt["terminal_outcome"]),
                         (task_id, True, "manual_review_required"))
        self.assertEqual((fenced.writes(), fenced.dispatches), (0, 0))
        self.assert_entity_hold_only(process, task_id)

    def release_rows(self, process, task_id):
        """Remove a task's lock rows through the lock store's own release."""
        rows = sorted((row for row in process.service.locks.records() if row.task_id == task_id),
                      key=lambda row: row.key.encode("utf-8"))
        first = rows[0]
        process.service.locks.release(LockHandle(
            LockOwner(first.owner_id, task_id, first.plan_id, first.operation_id, first.attempt_id),
            tuple(LockToken(row.key, row.generation, row.mode) for row in rows),
            min(row.acquired_at for row in rows), min(row.lease_expires_at for row in rows),
            FAN_LOCK_TIMING))
        self.assertEqual(process.lock_rows(task_id), [])

    @virtual
    async def test_task_whose_lock_rows_are_gone_settles_without_touching_other_locks(self):
        request = await self.crash_after_dispatch_intent()
        task_id = request.task_id
        await asyncio.sleep(205)
        process = self.process()
        self.release_rows(process, task_id)
        # Another family's live task now holds rows on the shared keys.
        other, held = await self.observing_live_task(
            self.process(next(family for family in FAMILIES if family != self.family)))
        theirs = other.lock_rows(held.task_id)
        self.assertEqual(len(theirs), 3)
        run = await self.sweep(process, task_id, limit=600, rows=0)
        record = process.record(task_id)
        self.assertEqual((record.terminal, record.normalized_outcome,
                          record.evidence.get("manual_review_reason_code"), claims(record)),
                         (True, "manual_review_required", BOUND_REASON, CLAIM_BOUND))
        self.assertLess(run["most_events"], 30)
        self.assertEqual(process.lock_rows(task_id), [])
        self.assertEqual(other.lock_rows(held.task_id), theirs)

    @virtual
    async def test_bound_settlement_waits_for_a_live_lock_lease(self):
        # Force the bound on a healthy in-flight task: while the pass that
        # dispatched still holds its lease, a second pass must not settle.
        process, request = await self.observing_live_task()
        task_id = request.task_id
        held = process.lock_rows(task_id)
        with patch("ha_mcp_engineering.f3.executor.recovery_bound_reached", lambda record: True):
            await process.service.control(request)
            record = process.record(task_id)
            self.assertFalse(record.terminal)
            self.assertEqual(process.lock_rows(task_id), held)
            await asyncio.sleep(31)
            await process.service.recover_once()
        self.assert_bound_settlement(process, task_id)
        self.assertEqual((process.writes(), process.dispatches), (1, 1))

    # -- leftover lock rows of a terminal task (sweep release by task_id) -------------------

    async def leftover_rows(self, how):
        """A terminal task, not in manual review, whose lock release failed."""
        process = self.process()
        request = self.request(process)
        task_id = request.task_id

        def failing(*args, **kwargs):
            raise LockStorageError("synthetic release failure")
        if how == "verified":
            with patch.object(process.service.locks, "release", failing):
                await process.service.control(request)
            expected = ("succeeded_verified", 1, 1)
        elif how == "pre_dispatch":
            healthy = process._read_fault

            async def refuse_once_locked():
                process.read_attempts += 1
                if process.service.locks.records():
                    raise HomeAssistantUnavailableError(
                        details={"method": "GET", "endpoint_category": "synthetic"})
            process._read_fault = refuse_once_locked
            with patch.object(process.service.locks, "release", failing):
                with contextlib.suppress(Exception):
                    await process.service.control(request)
            process._read_fault = healthy
            expected = ("failed_pre_dispatch", 0, 0)
        else:
            async def die_once_locked():
                if process.service.locks.records():
                    raise ProcessLoss()
            process._read_fault = die_once_locked
            with self.assertRaises(ProcessLoss):
                await process.service.control(request)
            self.assertFalse(process.record(task_id).terminal)
            process = self.process()
            await asyncio.sleep(205)
            with patch.object(process.service.locks, "release", failing):
                await process.service.recover_once()
            expected = ("cancelled_pre_dispatch", 0, 0)
        record = process.record(task_id)
        self.assertEqual((record.terminal, record.task_state, record.dispatch_count,
                          process.writes() if how != "cancelled" else 0),
                         (True,) + expected)
        self.assertEqual(len(process.lock_rows(task_id)), 3)
        return process, task_id, failing

    async def assert_sweep_frees_leftover_rows(self, how):
        process, task_id, failing = await self.leftover_rows(how)
        counters = process.counters()
        await asyncio.sleep(31)  # the leftover lease has expired
        other = self.process(next(family for family in FAMILIES if family != self.family))
        blocked = self.request(other)
        await other.service.control(blocked)
        self.assertEqual(other.record(blocked.task_id).task_state, "failed_pre_dispatch")
        self.assertEqual(other.writes(), 0)
        # A release that keeps failing is counted and grows nothing.
        store = tree_digest(self.root / process.service.namespace)
        rows = process.lock_rows(task_id)
        with patch.object(process.service.locks, "release", failing):
            for _ in range(3):
                await process.service.recover_once()
                await asyncio.sleep(SWEEP_SECONDS)
        self.assertEqual(process.lock_rows(task_id), rows)
        self.assertEqual(tree_digest(self.root / process.service.namespace), store)
        # One sweep with a working lock store frees them, entity included.
        await process.service.recover_once()
        self.assertEqual(process.lock_rows(), [])
        self.assertEqual(tree_digest(self.root / process.service.namespace), store)
        self.assertEqual((process.writes(), process.dispatches),
                         (counters["writes"], counters["dispatches"]))
        self.assertEqual(process.service.recovery_failures, counters["recovery_failures"] + 3)
        free = self.request(other)
        await other.service.control(free)
        self.assertEqual((other.record(free.task_id).normalized_outcome, other.writes()),
                         ("succeeded_verified", 1))

    @virtual
    async def test_sweep_frees_rows_left_after_a_verified_success(self):
        await self.assert_sweep_frees_leftover_rows("verified")

    @virtual
    async def test_sweep_frees_rows_left_after_a_pre_dispatch_failure(self):
        await self.assert_sweep_frees_leftover_rows("pre_dispatch")

    @virtual
    async def test_sweep_frees_rows_left_after_a_pre_dispatch_cancellation(self):
        await self.assert_sweep_frees_leftover_rows("cancelled")


class FanContainment(ContainmentContract, Scenario):
    family = "fan"


class PowerContainment(ContainmentContract, Scenario):
    family = "power"


class HelperValueContainment(ContainmentContract, Scenario):
    family = "helper_value"


class DeviceConfigurationContainment(ContainmentContract, Scenario):
    family = "device_configuration"


class ContainmentScope(unittest.TestCase):
    """Which executors have the behaviour, and the ruled constants."""

    def test_only_the_typed_write_service_turns_containment_on(self):
        package = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
        literal, forwarded = [], []
        for path in sorted(package.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                for keyword in getattr(node, "keywords", []) if isinstance(node, ast.Call) else []:
                    if keyword.arg != "recovery_containment":
                        continue
                    where = str(path.relative_to(package))
                    if isinstance(keyword.value, ast.Constant):
                        literal.append((where, keyword.value.value))
                    else:
                        forwarded.append((where, ast.unparse(keyword.value)))
        self.assertEqual(literal, [("fan/service.py", True)])
        self.assertEqual(set(forwarded), {("f3/executor.py", "self.recovery_containment")})
        from ha_mcp_engineering.fan.service import FanService
        from ha_mcp_engineering.power.service import PowerService
        from ha_mcp_engineering.helper_value.service import HelperService
        from ha_mcp_engineering.device_configuration.service import DeviceConfigurationService
        for service in (PowerService, HelperService, DeviceConfigurationService):
            self.assertIs(service.executor, FanService.executor)
            self.assertIs(service.read_task, FanService.read_task)
            self.assertIs(service.recover_once, FanService.recover_once)

    def test_the_default_executor_has_containment_off(self):
        import inspect
        parameter = inspect.signature(SharedOperationExecutor).parameters.get("recovery_containment")
        self.assertIsNotNone(parameter)
        self.assertIs(parameter.default, False)

    def test_the_ruled_bounds(self):
        from ha_mcp_engineering.f3 import executor, persistence
        from ha_mcp_engineering.fan import service
        self.assertEqual(persistence.RECOVERY_CLAIM_BOUND, CLAIM_BOUND)
        self.assertEqual(persistence.RECOVERY_SETTLE_BOUND, EVENT_BACKSTOP)
        self.assertEqual(MAX_EXECUTION_EVENTS, 128)
        self.assertEqual(executor.RECOVERY_BOUND_REASON, BOUND_REASON)
        self.assertEqual(service.RECOVERY_SWEEP_SECONDS, SWEEP_SECONDS)
        self.assertEqual(persistence.RECOVERY_BOOKKEEPING_EVENT_TYPES, frozenset(BOOKKEEPING))

    def test_the_claim_window_is_the_sweep_interval(self):
        # Q6.4: the private window in f3 equals fan.service.RECOVERY_SWEEP_SECONDS.
        from ha_mcp_engineering.f3 import persistence
        from ha_mcp_engineering.fan import service
        self.assertEqual(persistence._RECOVERY_CLAIM_WINDOW_SECONDS, service.RECOVERY_SWEEP_SECONDS)
        self.assertEqual(persistence._RECOVERY_CLAIM_WINDOW_SECONDS, SWEEP_SECONDS)

    def test_the_claim_count_rule(self):
        from ha_mcp_engineering.f3.persistence import consecutive_recovery_claims, recovery_bound_reached

        def claim(seconds):
            return {"event_type": "recovery_claimed", "diagnostic_codes": ["redispatch_prohibited"],
                    "occurred_at": (BASE + timedelta(seconds=seconds)).isoformat()}

        def observation(failed=False):
            return {"event_type": "observation_recorded", "occurred_at": BASE.isoformat(),
                    "diagnostic_codes": ["observation_failed"] if failed else []}
        cases = {
            "one window": ([claim(0), claim(1), claim(29.999)], 1),
            "boundary": ([claim(0), claim(30)], 2),
            # Q6.1: the uncounted claim at 20 s does not move the start point.
            "start point": ([claim(0), claim(20), claim(40)], 2),
            "start point, uncounted chain": ([claim(0), claim(20), claim(29), claim(45), claim(59)], 2),
            # Q6.2: a clock step back counts by absolute distance.
            "backwards inside": ([claim(0), claim(-10)], 1),
            "backwards outside": ([claim(0), claim(-30)], 2),
            "back and forth": ([claim(0), claim(-40), claim(-5)], 3),
            "completed observation": ([claim(0), claim(30), observation(), claim(31)], 1),
            "failed observation": ([claim(0), observation(failed=True), claim(1)], 1),
            "every 30 s": ([claim(30 * n) for n in range(8)], 8),
            "every second": ([claim(n) for n in range(200)], 7),
        }
        for name, (events, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(counted_claims(events), expected)
                self.assertEqual(consecutive_recovery_claims(events), expected)

        class Record:
            def __init__(self, events):
                self.events = events
        self.assertFalse(recovery_bound_reached(Record([claim(30 * n) for n in range(7)])))
        self.assertTrue(recovery_bound_reached(Record([claim(30 * n) for n in range(8)])))
        self.assertFalse(recovery_bound_reached(Record([claim(n) for n in range(119)])))
        self.assertTrue(recovery_bound_reached(Record([claim(n) for n in range(120)])))


if __name__ == "__main__":
    unittest.main()
