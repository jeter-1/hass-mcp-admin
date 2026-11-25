"""HAMCP-503 TB-3: trace watch retention-gap counter.

Core keeps only the newest ``stored_traces`` run traces per automation
(default 5, Core 2026.11.3 ``trace/const.py:19``). A watch poll whose run ring
is full and whose oldest run started after the previous successful listing
may have missed runs that Core evicted in between. Each public watch row
counts those polls in ``retention_gap_polls`` and ``last_retention_gap_at``.

The counter is in process only: it resets on restart and never reaches the
store. It assumes the default cap of 5 and checks only the runs ring.

Reuses the HAMCP-225 harness (fake Core, audit, runtime and clock).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))

from tests.test_hamcp225_trace_snapshots import (  # noqa: E402
    KNOWN, VERSION, ServiceCase, all_store_bytes,
)
from ha_mcp_engineering.trace_snapshots import contracts as c  # noqa: E402
from ha_mcp_engineering.trace_snapshots import service as service_module  # noqa: E402
from ha_mcp_engineering.trace_snapshots.service import TraceSnapshotService  # noqa: E402


AUTHORITY = (7, VERSION)


def at(minute):
    """The epoch seconds of the harness header's ``2026-10-26T10:MM:00Z``."""

    return datetime(2026, 10, 26, 10, minute, tzinfo=timezone.utc).timestamp()


class RetentionGapCase(ServiceCase):
    async def add_watch(self, automation_id="a1", mode="executed"):
        value, _ = await self.service.watch(action="add", automation_id=automation_id, mode=mode)
        return value

    async def row(self, automation_id="a1"):
        value, _ = await self.service.watch(action="list")
        return next(w for w in value["watches"] if w["automation_id"] == automation_id)

    async def poll(self, minute):
        self.clock.now = at(minute)
        await self.service.poll_once(authority=AUTHORITY)

    def add_runs(self, first, count, **kwargs):
        for minute in range(first, first + count):
            self.core.add(f"r{minute:02d}", minute=minute, **kwargs)


class RetentionGapTests(RetentionGapCase):
    async def test_a_full_ring_newer_than_the_previous_listing_counts_one_gap(self):
        await self.add_watch()
        await self.poll(1)  # baseline, nothing listed
        self.add_runs(10, c.MAX_CAPTURES)
        await self.poll(30)
        row = await self.row()
        self.assertEqual(row["retention_gap_polls"], 1)
        self.assertEqual(row["last_retention_gap_at"], c.utc_text(at(30)))
        # The next listing reaches back past the previous one: no new gap.
        await self.poll(40)
        row = await self.row()
        self.assertEqual(row["retention_gap_polls"], 1)
        self.assertEqual(row["last_retention_gap_at"], c.utc_text(at(30)))

    async def test_a_new_watch_reports_zero_and_no_gap_time(self):
        await self.add_watch()
        row = await self.row()
        self.assertEqual(row["retention_gap_polls"], 0)
        self.assertIsNone(row["last_retention_gap_at"])

    async def test_a_ring_below_the_default_cap_counts_nothing(self):
        await self.add_watch()
        await self.poll(1)
        self.add_runs(10, service_module.CORE_DEFAULT_STORED_TRACES - 1)
        await self.poll(30)
        self.assertEqual((await self.row())["retention_gap_polls"], 0)

    async def test_a_full_ring_reaching_back_to_the_previous_listing_counts_nothing(self):
        await self.add_watch()
        self.add_runs(4, 1)
        await self.poll(5)  # lists r04, started before this listing
        self.add_runs(10, 4)
        await self.poll(30)  # five runs; the oldest predates the previous listing
        self.assertEqual((await self.row())["retention_gap_polls"], 0)

    async def test_not_triggered_traces_are_not_watched(self):
        await self.add_watch()
        await self.poll(1)
        self.add_runs(10, 4)
        self.add_runs(20, 5, not_triggered=True)
        await self.poll(40)
        self.assertEqual((await self.row())["retention_gap_polls"], 0)

    async def test_the_first_poll_measures_from_the_watch_creation(self):
        self.clock.now = at(1)
        await self.add_watch()
        self.add_runs(10, 5)
        await self.poll(30)  # first listing; all five started after creation
        self.assertEqual((await self.row())["retention_gap_polls"], 1)

    async def test_runs_older_than_the_watch_are_history_not_a_gap(self):
        self.add_runs(10, 5)
        self.clock.now = at(12)
        await self.add_watch()
        await self.poll(30)
        self.assertEqual((await self.row())["retention_gap_polls"], 0)

    async def test_a_failed_listing_neither_counts_nor_moves_the_reference(self):
        await self.add_watch()
        await self.poll(1)
        self.add_runs(10, 5)
        with patch.object(self.service, "list_runs",
                          side_effect=c.TraceSnapshotError("source_unavailable")):
            await self.poll(30)
        self.assertEqual((await self.row())["retention_gap_polls"], 0)
        # The next successful listing still measures from minute 1.
        await self.poll(31)
        self.assertEqual((await self.row())["retention_gap_polls"], 1)

    async def test_a_restart_resets_the_count_and_falls_back_to_the_stored_poll(self):
        await self.add_watch()
        await self.poll(1)
        self.add_runs(10, 5)
        await self.poll(30)
        self.assertEqual((await self.row())["retention_gap_polls"], 1)
        self.service = TraceSnapshotService(self.root, self.core.command, self.runtime, self.audit,
                                            known_secrets=(KNOWN,), clock=self.clock)
        row = await self.row()
        self.assertEqual(row["retention_gap_polls"], 0)
        self.assertIsNone(row["last_retention_gap_at"])
        # The stored last poll (minute 30) postdates the oldest listed run.
        await self.poll(40)
        self.assertEqual((await self.row())["retention_gap_polls"], 0)

    async def test_a_removed_watch_drops_its_entry(self):
        await self.add_watch()
        await self.poll(1)
        self.add_runs(10, 5)
        await self.poll(30)
        watch_id = (await self.row())["watch_id"]
        self.assertIn(watch_id, self.service.retention_gaps)
        await self.service.watch(action="remove", automation_id="a1")
        await self.poll(31)
        self.assertNotIn(watch_id, self.service.retention_gaps)

    async def test_the_counter_never_reaches_the_store(self):
        await self.add_watch()
        await self.poll(1)
        self.add_runs(10, 5)
        await self.poll(30)
        self.assertEqual((await self.row())["retention_gap_polls"], 1)
        for watch in self.index()["watches"]:
            self.assertFalse({k for k in watch if "retention" in k})
        self.assertNotIn(b"retention_gap", all_store_bytes(self.root))


if __name__ == "__main__":
    unittest.main()
