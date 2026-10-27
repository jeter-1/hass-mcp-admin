"""HAMCP-232/145 correction 2 (H2-R1): the durable options-flow ledger and its sweep.

Every options flow the gateway opens is recorded before the open is sent and
its flow id before any abort, in Engineering's own storage.  A recovery sweep
retries each failed or unknown abort, bounded, in an exact-target Core
authority window, until Core confirms the flow gone; after the bound the flow
is reported orphaned.  An open whose answer was lost is reported as possibly
left open (owner decision (b): pinned Core lists no options flows).

Synthetic clients only; no live Home Assistant.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import (  # noqa: E402
    GovernanceError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
)
from ha_mcp_engineering.f3_configuration.adapter import (  # noqa: E402
    PREDISPATCH_READ_WINDOW,
    PredispatchAuthorityRefused,
)
from ha_mcp_engineering.governance import helper_options as contract  # noqa: E402
from ha_mcp_engineering.governance import helper_options_flows as flows  # noqa: E402

from tests.hamcp232_helper_options_fixtures import (  # noqa: E402
    STEP_PREFIX,
    HelperOptionsServiceTestCase,
)


FILTERED = "sensor.filtered_power"
TIME_CONSTANT_20 = {"entity_id": FILTERED, "set": {"time_constant": 20}}
ENTRY = "filterentry01"
SERVER_ERROR = {"status": 500, "provider_response_received": True}


class LedgerTestCase(HelperOptionsServiceTestCase):
    @property
    def ledger(self) -> flows.HelperOptionsFlowLedger:
        return self.runtime.helper_options_flows

    def flow_health(self) -> dict:
        return self.runtime.health()["helper_options_flows"]

    def deletes(self) -> list[str]:
        return [path for method, path in self.options_rest.requests if method == "DELETE"]

    def lose_open(self) -> None:
        """The open is stored by Core, but its answer never arrives."""

        original = self.options_rest.request

        async def request(method, path, body=None, **kwargs):
            result = await original(method, path, body, **kwargs)
            if method == "POST" and str(path) == STEP_PREFIX.rstrip("/"):
                raise HomeAssistantTimeoutError(details={})
            return result

        self.options_rest.request = request
        self.restore_open = lambda: setattr(self.options_rest, "request", original)

    async def strand_held_flow(self) -> tuple[dict, str]:
        """Apply with an unknown submit and a failed abort of the held flow."""

        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        planning_opens = len(self.options_rest.opens())
        self.options_rest.submit_failure = HomeAssistantTimeoutError(details={})
        held: list[str] = []

        def fail_held(entry_id, flow_id, ordinal):
            # Only the held flow's abort fails; the read-back flow's succeeds.
            failing = ordinal == planning_opens + 1
            if failing:
                held.append(flow_id)
            self.options_rest.abort_failure = (
                HomeAssistantApiError(details=SERVER_ERROR) if failing else None
            )

        self.options_rest.after_open = fail_held
        result = await self.apply(created)
        self.created = created
        self.options_rest.after_open = None
        self.options_rest.submit_failure = None
        self.assertIn("options_flow_orphaned", self.child_codes(result["task_id"]))
        (flow_id,) = held
        self.assertEqual(self.options_rest.flows, {flow_id: ENTRY})
        return result, flow_id


class FailedAbortTests(LedgerTestCase):
    async def test_the_flow_id_is_recorded_durably_before_the_abort(self):
        result, flow_id = await self.strand_held_flow()
        document = json.loads(self.ledger.path.read_text(encoding="utf-8"))
        (record,) = document["records"]
        self.assertEqual(
            {key: record[key] for key in ("status", "entry_id", "flow_id", "plan_id", "task_id",
                                          "phase", "attempts")},
            {"status": flows.STATUS_ABORT_FAILED, "entry_id": ENTRY, "flow_id": flow_id,
             "plan_id": self.created["plan_id"], "task_id": result["task_id"], "phase": "dispatch",
             "attempts": 0},
        )
        # A restarted runtime reads the same record and retries it at startup.
        self.options_rest.abort_failure = HomeAssistantApiError(details=SERVER_ERROR)
        await self.restart_runtime()
        self.assertEqual(self.deletes()[-1], STEP_PREFIX + flow_id)
        (candidate,) = self.ledger.retry_candidates()
        self.assertEqual((candidate["record_id"], candidate["flow_id"], candidate["attempts"]),
                         (record["record_id"], flow_id, 1))
        health = self.flow_health()
        self.assertEqual(health["status"], "retrying_abort")
        self.assertEqual(health["counts"][flows.STATUS_ABORT_FAILED], 1)

    async def test_retries_are_bounded_then_the_flow_is_reported_orphaned(self):
        result, flow_id = await self.strand_held_flow()
        self.options_rest.abort_failure = HomeAssistantApiError(details=SERVER_ERROR)
        before = len(self.deletes())
        # The restarted runtime's startup sweep is the first retry.
        await self.restart_runtime()
        self.assertEqual(len(self.deletes()), before + 1)
        for sweep in range(1, flows.MAX_SWEEP_ABORT_ATTEMPTS):
            await self.runtime.recover_once("ledger_bound")
            self.assertEqual(len(self.deletes()), before + sweep + 1)
        self.assertEqual(self.deletes()[-1], STEP_PREFIX + flow_id)
        # Orphaned: never retried again, and reported truthfully.
        for _ in range(3):
            await self.runtime.recover_once("ledger_bound_after")
        self.assertEqual(len(self.deletes()), before + flows.MAX_SWEEP_ABORT_ATTEMPTS)
        self.assertEqual(self.ledger.retry_candidates(), [])
        health = self.flow_health()
        self.assertEqual(health["status"], "attention_required")
        (entry,) = health["flows"]
        self.assertEqual(
            {key: entry[key] for key in ("entry_id", "status", "flow_id", "task_id",
                                         "abort_retry_attempts")},
            {"entry_id": ENTRY, "status": flows.STATUS_ORPHANED, "flow_id": flow_id,
             "task_id": result["task_id"], "abort_retry_attempts": flows.MAX_SWEEP_ABORT_ATTEMPTS},
        )
        self.assertEqual(entry["disclosure"], flows.ORPHANED_DISCLOSURE.format(
            flow_id=flow_id, entry_id=ENTRY, attempts=flows.MAX_SWEEP_ABORT_ATTEMPTS))
        self.assertEqual(self.options_rest.flows, {flow_id: ENTRY})

    async def test_a_confirmed_retry_removes_the_record_and_the_file(self):
        _result, flow_id = await self.strand_held_flow()
        self.options_rest.abort_failure = HomeAssistantApiError(details=SERVER_ERROR)
        await self.runtime.recover_once("ledger_retry_fails")
        self.assertEqual(self.ledger.records()[0]["attempts"], 1)
        self.options_rest.abort_failure = None
        await self.restart_runtime()
        await self.runtime.recover_once("ledger_retry_succeeds")
        self.assertEqual(self.options_rest.flows, {})
        self.assertEqual(self.deletes()[-1], STEP_PREFIX + flow_id)
        self.assertFalse(self.ledger.path.exists())
        self.assertEqual(self.flow_health()["status"], "clear")
        self.assertEqual(self.flow_health()["last_sweep"],
                         {"retried": 1, "confirmed_gone": 1, "orphaned": 0, "skipped": 0})

    async def test_a_flow_core_already_discarded_is_settled_by_the_404(self):
        _result, flow_id = await self.strand_held_flow()
        self.options_rest.abort_failure = None
        self.options_rest.flows.clear()  # a Core restart discarded it
        await self.restart_runtime()
        await self.runtime.recover_once("ledger_core_restarted")
        self.assertEqual(self.deletes()[-1], STEP_PREFIX + flow_id)
        self.assertEqual(self.ledger.records(), [])


class InertSweepTests(LedgerTestCase):
    async def test_no_record_means_no_call_and_no_write(self):
        self.add_filter()
        await self.create_options(TIME_CONSTANT_20)
        # Planning opened and aborted its flow and left nothing behind.
        self.assertFalse(self.ledger.path.exists())
        self.assertFalse(self.ledger.root.exists() and any(self.ledger.root.iterdir()))
        await self.restart_runtime()
        calls = self.ha_calls()
        for _ in range(3):
            await self.runtime.recover_once("ledger_inert")
        self.assertEqual(self.ha_calls(), calls)
        self.assertFalse(self.ledger.path.exists())
        self.assertEqual(self.flow_health()["status"], "clear")

    async def test_without_admission_a_recorded_flow_is_not_touched(self):
        _result, _flow_id = await self.strand_held_flow()
        self.core.withdraw()
        await self.restart_runtime()
        stored, calls = self.ledger.path.read_bytes(), self.ha_calls()
        for _ in range(3):
            await self.runtime.recover_once("ledger_unadmitted")
        self.assertEqual(self.ha_calls(), calls)
        self.assertEqual(self.ledger.path.read_bytes(), stored)

    async def test_a_live_record_is_never_swept(self):
        self.add_filter()
        gateway = self.runtime.helper_options_gateway
        with flows.flow_context(plan_id=None, task_id=None, phase="planning"):
            response = await gateway.open_flow(ENTRY)
        flow_id = response["flow_id"]
        # Held (live) by this process: not the sweep's to abort.
        self.assertEqual(self.ledger.retry_candidates(), [])
        calls = self.ha_calls()
        await self.runtime.recover_once("ledger_live")
        self.assertEqual(self.ha_calls(), calls)
        self.assertEqual(self.flow_health()["counts"]["in_progress"], 1)
        # A release refused before any send hands it to the sweep.
        gateway.flow_unreleased(flow_id)
        self.assertEqual([item["flow_id"] for item in self.ledger.retry_candidates()], [flow_id])
        await self.runtime.recover_once("ledger_released")
        self.assertEqual(self.options_rest.flows, {})
        self.assertFalse(self.ledger.path.exists())

    async def test_a_held_flow_whose_release_window_is_refused_is_retried(self):
        self.add_filter()
        gateway = self.runtime.helper_options_gateway
        with flows.flow_context(plan_id=None, task_id=None, phase="dispatch"):
            response = await gateway.open_flow(ENTRY)
        flow_id = response["flow_id"]
        adapter = self.runtime.registry.adapter(contract.CAPABILITY_IDENTITY)
        adapter._held["synthetic-key"] = SimpleNamespace(flow_id=flow_id)

        @asynccontextmanager
        async def refused():
            raise PredispatchAuthorityRefused()
            yield  # pragma: no cover

        calls = self.ha_calls()
        token = PREDISPATCH_READ_WINDOW.set(refused)
        try:
            self.assertFalse(await adapter._release("synthetic-key", windowed=True))
        finally:
            PREDISPATCH_READ_WINDOW.reset(token)
        adapter._orphaned.discard("synthetic-key")
        # Refused before any send: nothing reached Core, the sweep owns it now.
        self.assertEqual(self.ha_calls(), calls)
        self.assertEqual([item["flow_id"] for item in self.ledger.retry_candidates()], [flow_id])
        await self.runtime.recover_once("ledger_window_refused")
        self.assertEqual(self.options_rest.flows, {})
        self.assertFalse(self.ledger.path.exists())


class LostOpenTests(LedgerTestCase):
    """Owner decision (b): the accepted residual is disclosed, never hidden."""

    def assert_possibly_open(self, *, phase: str, task_id) -> None:
        health = self.flow_health()
        self.assertEqual(health["status"], "attention_required")
        (entry,) = health["flows"]
        self.assertEqual(
            {key: entry[key] for key in ("entry_id", "status", "flow_id", "task_id", "phase")},
            {"entry_id": ENTRY, "status": flows.STATUS_POSSIBLY_OPEN, "flow_id": None,
             "task_id": task_id, "phase": phase},
        )
        self.assertEqual(
            entry["disclosure"],
            "options flow possibly left open on entry filterentry01 (no flow id received); "
            "it holds no configuration change and is discarded at the next Home Assistant "
            "restart",
        )

    async def test_a_lost_planning_open_is_disclosed_in_health(self):
        self.add_filter()
        self.lose_open()
        with self.assertRaises(HomeAssistantTimeoutError):
            await self.create_options(TIME_CONSTANT_20)
        self.restore_open()
        self.assertEqual(self.options_rest.submits(), [])
        self.assert_possibly_open(phase="planning", task_id=None)
        # It survives a restart and is never swept (no flow id to abort).
        await self.restart_runtime()
        calls = self.ha_calls()
        await self.runtime.recover_once("ledger_lost_open")
        self.assertEqual(self.ha_calls(), calls)
        self.assert_possibly_open(phase="planning", task_id=None)

    async def test_a_lost_dispatch_open_is_disclosed_in_the_task_and_health(self):
        self.add_filter()
        created = await self.approved_options(TIME_CONSTANT_20)
        self.lose_open()
        result = await self.apply(created)
        self.restore_open()
        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        self.assertEqual(self.options_rest.submits(), [])
        self.assertIn("options_flow_open_unconfirmed", self.child_codes(result["task_id"]))
        self.assert_possibly_open(phase="dispatch", task_id=result["task_id"])
        await self.restart_runtime()
        self.assert_possibly_open(phase="dispatch", task_id=result["task_id"])

    async def test_a_definite_open_refusal_leaves_no_record(self):
        self.add_filter()
        self.options_rest.websocket.config_entries[ENTRY]["supports_options"] = False
        with self.assertRaises(Exception):
            await self.create_options(TIME_CONSTANT_20)
        self.assertFalse(self.ledger.path.exists())


class LedgerRefusalTests(LedgerTestCase):
    async def assert_open_refused(self) -> None:
        with self.assertRaises(GovernanceError) as caught:
            await self.create_options(TIME_CONSTANT_20)
        details = caught.exception.details
        self.assertEqual(
            (details["reason_code"], details["plan_created"], details["provider_dispatch_occurred"]),
            ("helper_options_flow_unavailable", False, False),
        )
        # Refused by the gateway before the open: nothing reached Core.
        self.assertEqual(self.options_rest.requests, [])

    async def test_no_ledger_refuses_the_open_before_any_send(self):
        self.add_filter()
        self.runtime.helper_options_gateway.flow_ledger = None
        await self.assert_open_refused()

    async def test_a_corrupt_ledger_refuses_the_open_and_reports_unavailable(self):
        self.add_filter()
        self.ledger.root.mkdir(parents=True, exist_ok=True)
        self.ledger.path.write_text("{not json", encoding="utf-8")
        await self.assert_open_refused()
        self.assertEqual(self.flow_health()["status"], "unavailable")

    async def test_a_full_ledger_of_retryable_flows_refuses_the_open(self):
        self.add_filter()
        for index in range(flows.MAX_RECORDS):
            record_id = self.ledger.begin_open(ENTRY)
            self.ledger.opened(record_id, f"{index + 1:032x}", finished=False)
        self.ledger.live.clear()
        self.ledger._live_flows.clear()
        await self.assert_open_refused()
        self.assertEqual(len(self.ledger.records()), flows.MAX_RECORDS)

    async def test_a_full_ledger_evicts_the_oldest_residual_and_counts_it(self):
        self.add_filter()
        first = None
        for _ in range(flows.MAX_RECORDS):
            record_id = self.ledger.begin_open(ENTRY)
            first = first or record_id
            self.ledger.open_failed(record_id, unconfirmed=True)
        await self.create_options(TIME_CONSTANT_20)
        # The planning open evicted the oldest residual, then its own record
        # left with its confirmed abort.
        records = self.ledger.records()
        self.assertEqual(len(records), flows.MAX_RECORDS - 1)
        self.assertNotIn(first, {record["record_id"] for record in records})
        health = self.flow_health()
        self.assertEqual(health["evicted_residual_count"], 1)
        self.assertEqual(len(health["flows"]), 16)
        self.assertTrue(health["details_truncated"])


class LedgerStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ledger = flows.HelperOptionsFlowLedger(
            self.directory.name, now=lambda: datetime(2026, 10, 27, tzinfo=timezone.utc)
        )

    def assert_invalid(self) -> None:
        with self.assertRaises(flows.FlowLedgerError) as caught:
            self.ledger.begin_open(ENTRY)
        self.assertEqual(caught.exception.category, "helper_options_flow_ledger_invalid")
        self.assertEqual(self.ledger.health_summary(),
                         {"status": "unavailable", "reason_code": "helper_options_flow_ledger_invalid"})

    def test_a_record_with_an_unknown_field_is_refused(self):
        record_id = self.ledger.begin_open(ENTRY)
        self.ledger.open_failed(record_id, unconfirmed=True)
        document = json.loads(self.ledger.path.read_text(encoding="utf-8"))
        document["records"][0]["note"] = "x"
        self.ledger.path.write_text(json.dumps(document), encoding="utf-8")
        self.assert_invalid()

    def test_an_open_record_without_a_flow_id_is_refused(self):
        record_id = self.ledger.begin_open(ENTRY)
        self.ledger.opened(record_id, "a" * 32, finished=False)
        document = json.loads(self.ledger.path.read_text(encoding="utf-8"))
        document["records"][0]["flow_id"] = None
        self.ledger.path.write_text(json.dumps(document), encoding="utf-8")
        self.assert_invalid()

    def test_a_symlinked_store_is_refused(self):
        target = Path(self.directory.name) / "elsewhere"
        target.mkdir()
        os.symlink(target, self.ledger.root)
        self.assert_invalid()
        self.assertEqual(list(target.iterdir()), [])

    def test_an_invalid_entry_or_phase_is_refused(self):
        with self.assertRaises(flows.FlowLedgerError):
            self.ledger.begin_open("../entry")
        with self.assertRaises(ValueError):
            with flows.flow_context(plan_id=None, task_id=None, phase="sweep"):
                pass
        self.assertFalse(self.ledger.path.exists())

    def test_the_context_is_recorded_and_unsafe_references_are_dropped(self):
        with flows.flow_context(plan_id="plan_1", task_id="task/../x", phase="readback"):
            self.ledger.begin_open(ENTRY)
        (record,) = self.ledger.records()
        self.assertEqual((record["plan_id"], record["task_id"], record["phase"]),
                         ("plan_1", None, "readback"))

    def test_a_finished_open_and_a_confirmed_abort_leave_no_file(self):
        self.ledger.opened(self.ledger.begin_open(ENTRY), "b" * 32, finished=True)
        self.assertFalse(self.ledger.path.exists())
        self.ledger.opened(self.ledger.begin_open(ENTRY), "c" * 32, finished=False)
        self.ledger.flow_gone("c" * 32)
        self.assertFalse(self.ledger.path.exists())
        self.assertEqual(self.ledger.live, set())

    def test_a_malformed_open_answer_is_kept_as_possibly_open(self):
        record_id = self.ledger.begin_open(ENTRY)
        self.ledger.opened(record_id, "NOT-A-FLOW-ID", finished=False)
        (record,) = self.ledger.records()
        self.assertEqual((record["status"], record["flow_id"]), (flows.STATUS_POSSIBLY_OPEN, None))
        self.assertEqual(self.ledger.retry_candidates(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
