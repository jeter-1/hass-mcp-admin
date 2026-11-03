"""HAMCP-277: the dashboard scan reads the household's dashboards within its budget.

Live (beta.55) the dependency index read 0 of 6 dashboards: the
``DASHBOARD_SCAN_SECONDS`` clock started with the build and the reads ran
concurrently with, and waited behind, the build's other sources. These tests
prove:

* the dashboard phase starts after the other sources, so a slow earlier phase
  that holds a shared resource does not spend its budget;
* six dashboards at realistic latencies are all read inside the budget, and
  their dashboard-only references reach entity_dependency_analysis,
  change_impact_analysis and the HAMCP-270 plan disclosure;
* an oversized dashboard is reported exactly as ``too_large`` and the other
  five are still read; dashboards that hang are timed out per read and cannot
  starve the rest;
* when the budget truly runs out, the dashboards that fit are read and the
  rest are named ``not_attempted_budget``; the source is partial, never
  complete, and the first warning carries "Dashboards read: N of M listed";
* named paths are screened and bounded, the read order is fixed, and the
  diagnostic store never changes a plan's bindings, risk or policy.

Every value is synthetic. No live dependency is used.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.dependency import global_sources as g  # noqa: E402
from ha_mcp_engineering.dependency.service import EntityDependencyAnalysisService  # noqa: E402
from ha_mcp_engineering.governance import global_reference_disclosure as grd  # noqa: E402
from ha_mcp_engineering.impact.provider import DirectHaImpactProvider  # noqa: E402
from ha_mcp_engineering.impact.service import ChangeImpactAnalysisService  # noqa: E402

from tests import test_hamcp227_global_sources as t227  # noqa: E402
from tests import test_hamcp270_global_reference_disclosure as t270  # noqa: E402


SECRET = t227.SECRET
# Six storage-mode dashboards, the live count; "synthetic-admin" is the large one.
PATHS = ("synthetic-acceptance", "synthetic-admin", "synthetic-health",
         "synthetic-main", "synthetic-map", "synthetic-tablet")
OVERSIZED = "synthetic-admin"


def only(path: str) -> str:
    return "light.synthetic_only_" + path.removeprefix("synthetic-")


DOCUMENTS = {path: {"views": [{"cards": [{"type": "tile", "entity": only(path)}]}]} for path in PATHS}
# Measured live: one list through the upstream gateway took about 0.4 s. A read
# is a fresh analysis session plus get_dashboard; 0.8 s is the fixture value,
# 1.5 s before the oversized body is refused at the streaming bound.
LIST_SECONDS = 0.4
READ_SECONDS = 0.8
OVERSIZED_SECONDS = 1.5


class LatencyReader(t227.FakeReader):
    """Wall-clock list and read latencies; a hanging read never returns."""

    def __init__(self, documents=DOCUMENTS, *, read_seconds=None, hang=(), core=None,
                 core_seconds=0.0, **kwargs):
        kwargs.setdefault("listed", sorted(documents))
        super().__init__(documents, **kwargs)
        self.read_seconds = dict(read_seconds or {})
        self.hang = set(hang)
        self.core, self.core_seconds = core, core_seconds
        self.opened_at: float | None = None
        self.started: list[str] = []
        self.cancelled: list[str] = []

    @asynccontextmanager
    async def session(self):
        self.opened_at = time.monotonic()
        yield self

    async def list(self, limit):
        await asyncio.sleep(LIST_SECONDS)
        return await super().list(limit)

    async def read(self, path):
        self.started.append(path)
        try:
            if path in self.hang:
                await asyncio.Event().wait()
            await asyncio.sleep(self.read_seconds.get(path, READ_SECONDS))
            if self.core is not None:
                # The Home Assistant side of the read waits for the shared core.
                async with self.core:
                    await asyncio.sleep(self.core_seconds)
        except asyncio.CancelledError:
            self.cancelled.append(path)
            raise
        return await super().read(path)


def collect(reader):
    return asyncio.run(g.collect_dashboard_sources(reader, secret=SECRET))


def outcomes(result, outcome):
    return sorted(path for path, value in result["outcomes"].items() if value == outcome)


class CollectorOutcomeTests(unittest.TestCase):
    """The collector alone: exact per-dashboard outcomes inside the budget."""

    def assert_named(self, result, outcome, paths):
        self.assertEqual(outcomes(result, outcome), sorted(paths))
        if paths and outcome != "read":
            self.assertIn(f"Dashboards {outcome}: " + ", ".join(sorted(paths)),
                          result["coverage"].warnings)

    def test_six_dashboards_at_realistic_latencies_are_all_read(self):
        reader = LatencyReader()
        result = collect(reader)
        self.assertEqual(result["outcomes"], {path: "read" for path in PATHS})
        self.assertEqual(result["coverage"].warnings[0], "Dashboards read: 6 of 6 listed")
        # Without lovelace in the list the source stays partial, and says why.
        self.assertEqual(t227.errors(result), {"dashboard_default_not_listed": 1})
        self.assertEqual(result["coverage"].completeness, "partial")
        self.assertEqual({item.target_entity_id for item in result["findings"]},
                         {only(path) for path in PATHS})
        self.assertEqual(result["profile"]["outcome_counts"], {"read": 6})
        self.assertLess(result["coverage"].duration_ms, g.DASHBOARD_SCAN_SECONDS * 1000)
        self.assertEqual(reader.cancelled, [])

    def test_every_dashboard_read_with_lovelace_listed_is_complete(self):
        documents = {**DOCUMENTS, "lovelace": t227.DEFAULT_DASHBOARD}
        result = collect(LatencyReader(documents))
        self.assertEqual(result["coverage"].completeness, "complete")
        self.assertEqual(t227.errors(result), {})
        self.assertEqual(result["coverage"].warnings, ["Dashboards read: 7 of 7 listed"])
        self.assertEqual(set(result["outcomes"].values()), {"read"})

    def test_an_oversized_dashboard_is_too_large_and_the_other_five_are_read(self):
        reader = LatencyReader(read_seconds={OVERSIZED: OVERSIZED_SECONDS},
                               failures={OVERSIZED: "response_limit"})
        result = collect(reader)
        self.assertEqual(result["coverage"].completeness, "partial")
        self.assertEqual(result["coverage"].warnings[0], "Dashboards read: 5 of 6 listed")
        self.assert_named(result, "read", [path for path in PATHS if path != OVERSIZED])
        self.assert_named(result, "too_large", [OVERSIZED])
        self.assertEqual(t227.errors(result), {"dashboard_default_not_listed": 1,
                                               "dashboard_read_response_limit": 1})
        self.assertEqual([(t.source_id, t.reason_code) for t in result["terminals"]],
                         [(OVERSIZED, "dashboard_document_limit_exceeded")])
        self.assertNotIn(only(OVERSIZED), {item.target_entity_id for item in result["findings"]})
        self.assertEqual(len(result["findings"]), 5)

    def test_hanging_dashboards_time_out_per_read_and_cannot_starve_the_rest(self):
        # The first four in read order fill every worker and never answer.
        hanging = sorted(PATHS)[:g.DASHBOARD_READ_CONCURRENCY]
        reader = LatencyReader(hang=hanging)
        result = collect(reader)
        self.assert_named(result, "timed_out", hanging)
        self.assert_named(result, "read", sorted(PATHS)[g.DASHBOARD_READ_CONCURRENCY:])
        self.assertEqual(sorted(reader.cancelled), hanging)
        self.assertEqual(result["coverage"].warnings[0], "Dashboards read: 2 of 6 listed")
        # The per-read bound, not the phase budget, stopped them.
        self.assertEqual(t227.errors(result), {"dashboard_default_not_listed": 1,
                                               "dashboard_read_timeout": len(hanging)})
        self.assertEqual(result["terminals"], [])
        self.assertLess(result["coverage"].duration_ms, g.DASHBOARD_SCAN_SECONDS * 1000)
        self.assertGreaterEqual(result["coverage"].duration_ms, g.DASHBOARD_READ_SECONDS * 1000)

    def test_a_transport_timeout_and_a_failure_are_named_exactly(self):
        reader = LatencyReader(failures={"synthetic-map": "timeout", "synthetic-main": "upstream_unavailable",
                                         "synthetic-tablet": RuntimeError("synthetic parse failure")})
        result = collect(reader)
        self.assert_named(result, "timed_out", ["synthetic-map"])
        self.assert_named(result, "failed", ["synthetic-main", "synthetic-tablet"])
        self.assertEqual(result["coverage"].warnings[0], "Dashboards read: 3 of 6 listed")
        self.assertEqual(result["profile"]["outcome_counts"],
                         {"read": 3, "timed_out": 1, "failed": 2})

    def test_when_the_budget_runs_out_the_rest_are_not_attempted_budget(self):
        # A serialized upstream: one read at a time, each 2 s of the 6 s budget.
        clock, sleep = t227.FakeClock(), t227.ControlledSleep()
        reader = t227.BudgetReader(DOCUMENTS, clock, read_cost=2.0, listed=sorted(PATHS))
        with patch.object(g, "DASHBOARD_READ_CONCURRENCY", 1):
            result = t227.collect_budget(reader, clock, sleep)
        fit = sorted(PATHS)[:3]
        self.assertEqual(reader.reads, fit)
        self.assert_named(result, "read", fit)
        self.assert_named(result, "not_attempted_budget", sorted(PATHS)[3:])
        self.assertEqual(result["coverage"].completeness, "partial")
        self.assertEqual(result["coverage"].warnings[0], "Dashboards read: 3 of 6 listed")
        self.assertEqual(t227.errors(result), {"dashboard_default_not_listed": 1,
                                               "dashboard_not_read": 3,
                                               "dashboard_scan_budget_exhausted": 1})
        self.assertIn(("dependency_provider", "dashboard_scan_budget_exhausted"),
                      [(t.source_id, t.reason_code) for t in result["terminals"]])

    def test_a_read_cut_by_the_phase_deadline_is_timed_out_and_exhausts_the_budget(self):
        # Two hanging reads start with under DASHBOARD_READ_SECONDS left.
        reader = LatencyReader(hang=["synthetic-acceptance", "synthetic-admin"])
        started = time.monotonic()
        with patch.object(g, "DASHBOARD_SCAN_SECONDS", LIST_SECONDS + 1.0), \
                patch.object(g, "DASHBOARD_READ_CONCURRENCY", 2):
            result = collect(reader)
        self.assertLess(time.monotonic() - started, g.DASHBOARD_SCAN_SECONDS)
        self.assert_named(result, "timed_out", ["synthetic-acceptance", "synthetic-admin"])
        self.assert_named(result, "not_attempted_budget", sorted(PATHS)[2:])
        self.assertEqual(result["coverage"].warnings[0], "Dashboards read: 0 of 6 listed")
        self.assertEqual(t227.errors(result), {"dashboard_default_not_listed": 1,
                                               "dashboard_not_read": 4,
                                               "dashboard_read_timeout": 2,
                                               "dashboard_scan_budget_exhausted": 1})
        self.assertEqual(result["coverage"].completeness, "partial")

    def test_last_reads_capped_by_the_phase_deadline_exhaust_the_budget_without_the_watchdog(self):
        # Under DASHBOARD_READ_SECONDS is left when the last two reads start,
        # so their own timers are the phase remainder. Those timers fire, the
        # watchdog never does, and the budget is still reported exhausted.
        clock = t227.FakeClock()
        timers: list[tuple[float, asyncio.Event]] = []

        async def sleep(seconds):
            # Per-read timers fire once the clock reaches the deadline; the
            # watchdog (the full phase budget) never does.
            event = asyncio.Event()
            timers.append((seconds, event))
            if seconds < g.DASHBOARD_SCAN_SECONDS and clock.now >= g.DASHBOARD_SCAN_SECONDS:
                event.set()
            await event.wait()

        remaining = g.DASHBOARD_READ_SECONDS - 1.0
        documents = {"lovelace": t227.DEFAULT_DASHBOARD, "synthetic-main": DOCUMENTS["synthetic-main"]}

        def on_hang(_):
            if len(reader.reads) == len(documents):
                clock.now = g.DASHBOARD_SCAN_SECONDS
                for seconds, event in timers:
                    if seconds < g.DASHBOARD_SCAN_SECONDS:
                        event.set()

        reader = t227.BudgetReader(documents, clock, list_cost=g.DASHBOARD_SCAN_SECONDS - remaining,
                                   hang_reads=True, on_hang=on_hang)
        result = t227.collect_budget(reader, clock, sleep)
        self.assertEqual(sorted(seconds for seconds, _ in timers),
                         [remaining, remaining, g.DASHBOARD_SCAN_SECONDS])
        self.assertEqual(result["outcomes"], {path: "timed_out" for path in documents})
        self.assertEqual(t227.errors(result), {"dashboard_read_timeout": 2,
                                               "dashboard_scan_budget_exhausted": 1})
        self.assertIn(("dependency_provider", "dashboard_scan_budget_exhausted"),
                      [(t.source_id, t.reason_code) for t in result["terminals"]])
        self.assertEqual(result["coverage"].completeness, "partial")

    def test_a_retired_authority_names_the_rest_not_attempted_authority(self):
        reader = t227.FakeReader(DOCUMENTS, current=False)
        result = collect(reader)
        self.assertEqual(reader.reads, [])
        self.assert_named(result, "not_attempted_authority", PATHS)
        self.assertEqual(t227.errors(result), {"dashboard_default_not_listed": 1,
                                               "dashboard_not_read": 6})
        self.assertEqual(result["coverage"].completeness, "partial")

    def test_reads_follow_a_fixed_sorted_order(self):
        reader = t227.FakeReader(DOCUMENTS, listed=list(reversed(PATHS)))
        with patch.object(g, "DASHBOARD_READ_CONCURRENCY", 1):
            result = collect(reader)
        self.assertEqual(reader.reads, sorted(PATHS))
        self.assertEqual(list(result["outcomes"]), sorted(PATHS))

    def test_named_paths_are_screened_and_bounded_but_the_count_stays_exact(self):
        paths = [f"synthetic-{index:02d}" for index in range(13)] + [f"synthetic-{SECRET}"]
        reader = t227.FakeReader({path: DOCUMENTS[PATHS[0]] for path in paths},
                                 failures={path: "upstream_unavailable" for path in paths})
        result = collect(reader)
        self.assertEqual(result["coverage"].warnings[0], f"Dashboards read: 0 of {len(paths)} listed")
        line = next(item for item in result["coverage"].warnings if item.startswith("Dashboards failed: "))
        self.assertEqual(line, "Dashboards failed: " + ", ".join(paths[:g.MAX_DASHBOARD_OUTCOME_PATHS])
                         + ", and 4 others")
        self.assertFalse(any(SECRET in item for item in result["coverage"].warnings))
        self.assertEqual(result["profile"]["outcome_counts"], {"failed": len(paths)})

    def test_one_other_and_withheld_only_paths_read_exactly(self):
        self.assertEqual(g._outcome_warning("failed", [f"x-{i}" for i in range(11)], ""),
                         "Dashboards failed: " + ", ".join(f"x-{i}" for i in range(10)) + ", and 1 other")
        self.assertEqual(g._outcome_warning("failed", [f"a-{SECRET}"], SECRET),
                         "Dashboards failed: none shown, and 1 other")

    def test_every_listed_dashboard_has_exactly_one_known_outcome(self):
        reader = LatencyReader(read_seconds={OVERSIZED: OVERSIZED_SECONDS},
                               failures={OVERSIZED: "response_limit"}, hang=["synthetic-map"])
        result = collect(reader)
        self.assertEqual(sorted(result["outcomes"]), sorted(PATHS))
        self.assertTrue(set(result["outcomes"].values()) <= set(g.DASHBOARD_OUTCOMES))
        self.assertEqual(sum(result["profile"]["outcome_counts"].values()), len(PATHS))


class Contention:
    """A shared core: the build's automation batch holds it; dashboard reads need it."""

    def __init__(self, busy_seconds):
        self.core = asyncio.Lock()
        self.busy_seconds = busy_seconds
        self.released_at: float | None = None
        original = t227.CountingWebSocket.command_batch
        contention = self

        async def command_batch(self, payloads):
            async with contention.core:
                await asyncio.sleep(contention.busy_seconds)
                contention.released_at = time.monotonic()
            return await original(self, payloads)

        self.patch = patch.object(t227.CountingWebSocket, "command_batch", command_batch)


class DashboardPhaseClockTests(unittest.IsolatedAsyncioTestCase):
    """The budget clock starts at the dashboard phase, not at build start."""

    async def test_a_slow_earlier_phase_does_not_spend_the_dashboard_budget(self):
        # The earlier phase holds the core for longer than the whole dashboard
        # budget. Started at build start (beta.55) the reads queued behind it
        # and 0 of 6 were read; started at the phase, all six are read.
        budget = 2.0
        contention = Contention(busy_seconds=budget + 0.5)
        reader = LatencyReader(read_seconds={path: 0.1 for path in PATHS},
                               core=contention.core, core_seconds=0.05)
        home = t227.Home()
        home.automations.update({item["entity_id"]: item for item in t227.representative_automations()})
        started = time.monotonic()
        with patch.object(g, "DASHBOARD_SCAN_SECONDS", budget), contention.patch:
            _, snapshot, _, _ = await t227.build(home, reader)
        self.assertIsNotNone(contention.released_at, "the automation batch did not run")
        self.assertGreaterEqual(contention.released_at - started, budget)
        # The phase opened its session only after the earlier phase finished.
        self.assertGreaterEqual(reader.opened_at, contention.released_at)
        row = snapshot.global_sources.coverage_for("dashboard")
        self.assertEqual(row.warnings[0], "Dashboards read: 6 of 6 listed")
        self.assertNotIn("dashboard_scan_budget_exhausted: 1", row.warnings)
        self.assertLess(row.duration_ms, budget * 1000)
        dashboard_findings = {item.target_entity_id for item in snapshot.global_sources.findings
                              if item.source_type == "dashboard"}
        self.assertEqual(dashboard_findings, {only(path) for path in PATHS})


class LiveScaleAnalysisTests(unittest.IsolatedAsyncioTestCase):
    """Six dashboards, one oversized, at realistic latencies through the build."""

    async def asyncSetUp(self):
        self.home = t227.Home(extra_states=[t227.state(only(path)) for path in PATHS])
        self.reader = LatencyReader(read_seconds={OVERSIZED: OVERSIZED_SECONDS},
                                    failures={OVERSIZED: "response_limit"})
        self.index, self.snapshot, self.rest, self.websocket = await t227.build(self.home, self.reader)
        self.patch = patch(t227.BLUEPRINT_READER, side_effect=t227.read_blueprint)
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()

    async def test_references_reach_dependency_analysis(self):
        for path in PATHS:
            with self.subTest(path=path):
                output = await EntityDependencyAnalysisService(self.index).analyze(
                    entity_id=only(path), detail_level="evidence")
                data = output.data
                row = t227.coverage_row(data["source_coverage"], "dashboard")
                self.assertEqual(row["completeness"], "partial")
                self.assertEqual(row["warnings"][0], "Dashboards read: 5 of 6 listed")
                self.assertIn(f"Dashboards too_large: {OVERSIZED}", row["warnings"])
                found = [(item["source_type"], item["source_id"]) for item in data["findings"]]
                if path == OVERSIZED:
                    # Not read, so no reference is claimed, and none is denied.
                    self.assertEqual(found, [])
                    self.assertNotEqual(data["overview"]["dependency_status"], "referenced")
                    self.assertFalse(data["overview"]["coverage_complete"])
                else:
                    self.assertEqual(found, [("dashboard", path)])
                    self.assertEqual(data["overview"]["dependency_status"], "referenced")

    async def test_references_reach_change_impact_analysis(self):
        provider = DirectHaImpactProvider(self.index, self.rest, self.websocket,
                                          secret=SECRET, ha_token=SECRET)
        output = await ChangeImpactAnalysisService(provider).analyze(
            entity_id=only("synthetic-main"), operation="remove_entity",
            detail_level="evidence", source_types=t227.ALL_SOURCES)
        affected = {(item["affected_object_type"], item["affected_object_id"])
                    for item in output.data["findings"] if item["rule_id"] == "remove_orphaned_consumer"}
        self.assertIn(("dashboard", "dashboard:synthetic-main"), affected)
        row = next(item for item in output.data["source_coverage_matrix"]
                   if item["source_type"] == "dashboard")
        self.assertEqual(row["completeness"], "partial")

    async def test_references_and_the_read_count_reach_the_plan_disclosure(self):
        block = grd.disclose(lambda: self.snapshot, [only("synthetic-main")])
        (child,) = block["children"]
        self.assertEqual(block["status"], "read")
        self.assertEqual(block["completeness"]["dashboard"], "partial")
        # The one-line reason is the exact read count.
        self.assertEqual(block["reasons"]["dashboard"], "Dashboards read: 5 of 6 listed")
        self.assertEqual(child["by_source"], {"dashboard": 1})
        self.assertEqual([(item["type"], item["id"]) for item in child["items"]],
                         [("dashboard", "synthetic-main")])


class DisclosureNonInteractionTests(t270.PlanDisclosureTestCase):
    """HAMCP-270: a fuller dashboard read never changes a plan's bindings."""

    async def collected(self, reader):
        dashboards = await g.collect_dashboard_sources(reader, secret=SECRET)
        return await g.collect_global_sources([], [], registry_complete=True, secret=SECRET,
                                              dashboards=dashboards)

    async def test_read_too_large_and_budget_outcomes_leave_bindings_risk_and_policy_identical(self):
        documents = {path: {"views": [{"cards": [
            {"type": "tile", "entity": t270.LIGHT}, {"type": "tile", "entity": t270.FILTERED},
        ]}]} for path in PATHS}
        clock, sleep = t227.FakeClock(), t227.ControlledSleep()
        with patch.object(g, "DASHBOARD_READ_CONCURRENCY", 1):
            budget = await g.collect_global_sources(
                [], [], registry_complete=True, secret=SECRET,
                dashboards=await g.collect_dashboard_sources(
                    t227.BudgetReader(documents, clock, read_cost=2.0, listed=sorted(PATHS)),
                    secret=SECRET, clock=clock, sleep=sleep))
        variants = {
            "absent": None,
            "all_read": await self.collected(t227.FakeReader(
                {**documents, "lovelace": t227.DEFAULT_DASHBOARD})),
            "too_large": await self.collected(t227.FakeReader(
                documents, failures={OVERSIZED: "response_limit"})),
            "budget": budget,
        }
        self.assertEqual(variants["all_read"].coverage_for("dashboard").completeness, "complete")
        for planner in self.PLANNERS:
            with self.subTest(planner=planner):
                _, target = await getattr(self, planner)()
                observed = {}
                for name, sources in variants.items():
                    self.snapshot.global_sources = sources
                    created, _ = await getattr(self, planner)()
                    decision = created["policy_decision"]
                    observed[name] = (
                        self.bindings(created), self.bound_disclosure(created),
                        (decision["policy_class"], decision["physical_consequence"],
                         decision["required_acknowledgements"]),
                        created["risk"], self.block(created),
                    )
                baseline = observed["absent"][:4]
                for name, value in observed.items():
                    for index, part in enumerate(("bindings", "bound_disclosure", "policy", "risk")):
                        self.assertEqual(value[index], baseline[index], f"{name}: {part}")
                # ...while the disclosure itself truthfully differs.
                self.assertEqual(observed["absent"][4]["status"], "unavailable")
                self.assertEqual(observed["all_read"][4]["completeness"]["dashboard"], "complete")
                self.assertEqual(observed["too_large"][4]["reasons"]["dashboard"],
                                 "Dashboards read: 5 of 6 listed")
                self.assertEqual(observed["budget"][4]["reasons"]["dashboard"],
                                 "Dashboards read: 3 of 6 listed")
                self.snapshot.global_sources = None


if __name__ == "__main__":
    unittest.main()
