"""HAMCP-227: scene, group, dashboard and template reference coverage.

The references live in a diagnostic partition (``GlobalSourceDiagnostics``)
outside the helper obligation ledger and the snapshot fingerprint. These tests
prove:

* scene and group references come from the loaded ``entity_id`` attribute at
  ``$.attributes.entity_id[n]`` with no extra request, and every gap (no member
  list, a stateless group helper, a member, inventory or finding bound, a
  withheld member) is partial with a counted reason;
* dashboard references come from the reviewed list and read path through the
  real dashboard walker, at exact paths through conditional, stack and grid
  nesting; a custom card, a strategy, a missing ``lovelace`` entry, truncation
  and a budget overrun are partial; a read failure stays with its dashboard;
* the dashboard reader dispatches exactly one list and one read per dashboard,
  each gated, leased and audited, and dispatches nothing without authority;
* templates and complete script enumeration are reported as deferred;
* truncation of the index marks every dropped source type partial;
* entity_dependency_analysis and change_impact_analysis show scene-, group-
  and dashboard-only references, and the source_types filter excludes them;
* the snapshot fingerprint and coverage rows are unchanged, and more coverage
  never downgrades risk.

Every value is synthetic. No live dependency is used.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.dashboard_analysis import contracts as dc  # noqa: E402
from ha_mcp_engineering.dashboard_analysis import rules  # noqa: E402
from ha_mcp_engineering.dashboard_analysis.dependency_reader import (  # noqa: E402
    _EMPTY_REGISTRY,
    _EMPTY_STATES,
    CAPABILITIES,
    DashboardDependencyReader,
    summarize_scan,
)
from ha_mcp_engineering.dependency import global_sources as g  # noqa: E402
from ha_mcp_engineering.dependency.bounded_sources import bound_table  # noqa: E402
from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.provider import DirectHaDependencyProvider  # noqa: E402
from ha_mcp_engineering.dependency.script_sources import _GAPS as SCRIPT_GAPS  # noqa: E402
from ha_mcp_engineering.dependency.service import EntityDependencyAnalysisService  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.impact.provider import DirectHaImpactProvider  # noqa: E402
from ha_mcp_engineering.observability import METRICS  # noqa: E402
from ha_mcp_engineering.impact.service import ChangeImpactAnalysisService  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, current_telemetry, end_request  # noqa: E402
from tests.hamcp209_fixtures import read_blueprint, representative_automations  # noqa: E402
from tests.hamcp212_fixtures import SyntheticScriptHome  # noqa: E402
from tests.test_hamcp209_fast_dependency_index import _AnalysisRest  # noqa: E402
from tests.test_hamcp212_loaded_script_config import _ScriptAnalysisWebSocket  # noqa: E402


SECRET = "synthetic-not-a-credential"
BLUEPRINT_READER = "ha_mcp_engineering.dependency.provider._read_blueprint_with_status"
ALL_SOURCES = ["automation", "blueprint", "script", "scene", "group", "template", "dashboard"]
SCENE = {"entity_id": "scene.synthetic_evening", "state": "2026-01-01T00:00:00+00:00",
         "attributes": {"friendly_name": "Synthetic evening",
                        "entity_id": ["light.synthetic_only_scene", "light.synthetic_shared"]}}
GROUP = {"entity_id": "group.synthetic_downstairs", "state": "on",
         "attributes": {"friendly_name": "Synthetic downstairs",
                        "entity_id": ["light.synthetic_only_group", "light.synthetic_shared"]}}
TARGETS = ("light.synthetic_only_scene", "light.synthetic_only_group",
           "light.synthetic_only_dashboard", "light.synthetic_shared", "light.synthetic_unreferenced")
DEFAULT_DASHBOARD = {"views": [{"cards": [{"type": "tile", "entity": "light.synthetic_only_dashboard"}]}]}


def state(entity_id, members=None, *, value="on"):
    attributes = {"friendly_name": entity_id}
    if members is not None:
        attributes["entity_id"] = members
    return {"entity_id": entity_id, "state": value, "attributes": attributes}


def registry_row(entity_id, platform="synthetic"):
    return {"entity_id": entity_id, "platform": platform}


class Home(SyntheticScriptHome):
    """The HAMCP-212 model plus scenes, groups, targets and registry extras."""

    def __init__(self, *, scenes=True, extra_states=(), extra_registry=()):
        super().__init__([], [])
        self.extra = ([SCENE, GROUP] if scenes else []) + list(extra_states)
        self.extra_registry = list(extra_registry)
        self.ws_counts: Counter[str] = Counter()

    def states(self):
        rows = super().states() + [dict(row) for row in self.extra]
        present = {row["entity_id"] for row in rows}
        rows += [state(entity) for entity in TARGETS if entity not in present]
        return rows

    def registry(self):
        return super().registry() + list(self.extra_registry)


class CountingWebSocket(_ScriptAnalysisWebSocket):
    async def command(self, payload):
        self.ha.ws_counts[str(payload.get("type"))] += 1
        return await super().command(payload)


def walk(configuration):
    result = rules.scan(configuration, "h" * 64, _EMPTY_STATES, _EMPTY_REGISTRY,
                        core_version=rules.CORE_VERSION, known_secrets=())
    return {**summarize_scan(configuration, result), "projection_hash": result["projection_hash"]}


class FakeReader:
    """The session contract of ``DashboardDependencyReader`` over real walks."""

    provenance = "synthetic dashboard reader"

    def __init__(self, documents, *, failures=None, listed=None, truncated=False,
                 partial=False, delay=0.0, list_error=None, current=True):
        self.documents = documents
        self.failures = failures or {}
        self.listed = listed
        self.truncated, self.partial, self.delay = truncated, partial, delay
        self.list_error = list_error
        self.is_current = current
        self.lists = 0
        self.reads: list[str] = []

    def current(self):
        return self.is_current

    @asynccontextmanager
    async def session(self):
        yield self

    async def list(self, limit):
        self.lists += 1
        if self.list_error is not None:
            raise self.list_error
        paths = self.listed if self.listed is not None else list(self.documents)
        return {"dashboards": [{"url_path": path, "title": f"Synthetic {path}"} for path in paths],
                "truncated": self.truncated, "partial": self.partial}

    async def read(self, path):
        self.reads.append(path)
        if self.delay:
            await asyncio.sleep(self.delay)
        if path in self.failures:
            failure = self.failures[path]
            if isinstance(failure, Exception):
                raise failure
            return {"failure": failure}
        return walk(self.documents[path])


def collect(reader):
    return asyncio.run(g.collect_dashboard_sources(reader, secret=SECRET))


def errors(result):
    return dict(result["errors"])


async def build(home, reader=None, *, max_edges=10_000):
    rest = _AnalysisRest(home)
    websocket = CountingWebSocket(home)
    factory = (lambda: reader) if reader is not None else None
    index = DependencyIndex(
        DirectHaDependencyProvider(rest, websocket, secret=SECRET, dashboard_reader_factory=factory),
        max_edges=max_edges,
    )
    with patch(BLUEPRINT_READER, side_effect=read_blueprint):
        snapshot, _, _ = await index.get(refresh=True)
    return index, snapshot, rest, websocket


def coverage_row(rows, source_type):
    return next(item for item in rows if item["source_type"] == source_type)


class MembershipSourceTests(unittest.TestCase):
    def collect(self, states, registry=(), *, registry_complete=True, secret=SECRET):
        results, templates = g.collect_membership_sources(
            list(states), list(registry), registry_complete=registry_complete, secret=secret,
        )
        return {item.coverage.source_type: item for item in results}, templates

    def test_scene_and_group_members_are_found_at_their_attribute_paths(self):
        results, _ = self.collect([SCENE, GROUP])
        for source_type, row in (("scene", SCENE), ("group", GROUP)):
            result = results[source_type]
            self.assertEqual(result.coverage.completeness, "complete")
            self.assertEqual(result.coverage.provider, "direct_ha_api")
            self.assertEqual(result.coverage.provider_capability, f"{source_type}_loaded_membership")
            self.assertEqual(result.coverage.obligation_ledger_completeness, "unavailable")
            self.assertIn("loaded membership", result.coverage.warnings[0])
            found = sorted((item.target_entity_id, item.config_path) for item in result.findings)
            self.assertEqual(found, sorted(
                (member, f"$.attributes.entity_id[{position}]")
                for position, member in enumerate(row["attributes"]["entity_id"])
            ))
            for item in result.findings:
                self.assertEqual(item.source_type, source_type)
                self.assertEqual(item.source_id, row["entity_id"])
                self.assertEqual(item.relation, f"{source_type}_member")
                self.assertEqual(item.match_type, "loaded_membership")
                self.assertTrue(item.direct)
            self.assertEqual(result.terminals, [])

    def test_scene_or_group_without_a_member_list_is_partial(self):
        results, _ = self.collect([state("scene.synthetic_bare"), state("group.synthetic_bare", "x")])
        self.assertEqual(results["scene"].coverage.completeness, "partial")
        self.assertIn("scene_members_not_exposed: 1", results["scene"].coverage.warnings)
        self.assertEqual(results["group"].coverage.completeness, "partial")
        self.assertIn("group_members_not_exposed: 1", results["group"].coverage.warnings)

    def test_stateless_group_helper_is_partial(self):
        results, _ = self.collect([SCENE, GROUP], [registry_row("light.synthetic_helper_group", "group")])
        self.assertEqual(results["group"].coverage.completeness, "partial")
        self.assertIn("group_state_unavailable: 1", results["group"].coverage.warnings)
        self.assertEqual(results["scene"].coverage.completeness, "complete")

    def test_registry_only_scene_is_partial(self):
        results, _ = self.collect([], [registry_row("scene.synthetic_registry_only", "homeassistant")])
        self.assertEqual(results["scene"].coverage.completeness, "partial")
        self.assertIn("scene_state_unavailable: 1", results["scene"].coverage.warnings)

    def test_incomplete_registry_is_partial(self):
        results, _ = self.collect([SCENE, GROUP], registry_complete=False)
        for result in results.values():
            self.assertEqual(result.coverage.completeness, "partial")
            self.assertIn("entity_registry_incomplete: 1", result.coverage.warnings)

    def test_member_limit_is_partial_with_a_terminal(self):
        members = [f"light.synthetic_{i}" for i in range(g.MAX_MEMBERS_PER_SOURCE + 1)]
        results, _ = self.collect([state("group.synthetic_big", members)])
        result = results["group"]
        self.assertEqual(result.coverage.completeness, "partial")
        self.assertEqual(len(result.findings), g.MAX_MEMBERS_PER_SOURCE)
        self.assertEqual([(t.source_id, t.reason_code) for t in result.terminals],
                         [("group.synthetic_big", "group_member_limit_exceeded")])

    def test_exactly_the_member_limit_is_complete(self):
        members = [f"light.synthetic_{i}" for i in range(g.MAX_MEMBERS_PER_SOURCE)]
        results, _ = self.collect([state("group.synthetic_big", members)])
        self.assertEqual(results["group"].coverage.completeness, "complete")
        self.assertEqual(len(results["group"].findings), g.MAX_MEMBERS_PER_SOURCE)

    def test_inventory_limit_is_partial_with_a_terminal(self):
        with patch.object(g, "MAX_SCENE_SOURCES", 2):
            results, _ = self.collect([state(f"scene.synthetic_{i}", ["light.synthetic_a"]) for i in range(3)])
        result = results["scene"]
        self.assertEqual(result.coverage.completeness, "partial")
        self.assertEqual(len(result.findings), 2)
        self.assertIn("scene_inventory_limit_exceeded",
                      [terminal.reason_code for terminal in result.terminals])
        with patch.object(g, "MAX_SCENE_SOURCES", 3):
            results, _ = self.collect([state(f"scene.synthetic_{i}", ["light.synthetic_a"]) for i in range(3)])
        self.assertEqual(results["scene"].coverage.completeness, "complete")

    def test_finding_limit_is_partial_with_a_terminal(self):
        with patch.object(g, "MAX_MEMBERSHIP_FINDINGS", 3):
            results, _ = self.collect([state("group.synthetic_a", ["light.a", "light.b"]),
                                       state("group.synthetic_b", ["light.c", "light.d"])])
        result = results["group"]
        self.assertEqual(result.coverage.completeness, "partial")
        self.assertEqual(len(result.findings), 3)
        self.assertIn("group_finding_limit_exceeded",
                      [terminal.reason_code for terminal in result.terminals])
        with patch.object(g, "MAX_MEMBERSHIP_FINDINGS", 4):
            results, _ = self.collect([state("group.synthetic_a", ["light.a", "light.b"]),
                                       state("group.synthetic_b", ["light.c", "light.d"])])
        self.assertEqual(results["group"].coverage.completeness, "complete")

    def test_invalid_or_secret_shaped_members_and_identities_are_withheld(self):
        known = "synthetic_known_secret_value_0123456789"
        results, _ = self.collect([
            state("scene.synthetic_mixed", ["light.ok", 7, "not an entity", f"light.{known}"]),
            state(f"group.{known}", ["light.ok"]),
        ], secret=known)
        scene = results["scene"]
        self.assertEqual([item.target_entity_id for item in scene.findings], ["light.ok"])
        self.assertEqual(scene.coverage.completeness, "partial")
        self.assertIn("scene_member_invalid_or_withheld: 3", scene.coverage.warnings)
        group = results["group"]
        self.assertEqual(group.findings, [])
        self.assertIn("group_identity_invalid_or_withheld: 1", group.coverage.warnings)
        self.assertNotIn(known, repr([scene.findings, scene.coverage, group.findings, group.coverage]))

    def test_untrusted_source_name_is_bounded(self):
        row = state("scene.synthetic_long", ["light.ok"])
        row["attributes"]["friendly_name"] = "N" * 5000
        results, _ = self.collect([row])
        name = results["scene"].findings[0].source_name
        self.assertTrue(name.startswith("N" * 150))
        self.assertLess(len(name), 200)

    def test_template_platform_count_is_reported(self):
        _, count = self.collect([], [registry_row("sensor.synthetic_t1", "template"),
                                     registry_row("sensor.synthetic_t2", "template")])
        self.assertEqual(count, 2)
        row = g.template_row(count)
        self.assertEqual((row.source_type, row.provider, row.completeness, row.evidence_count),
                         ("template", "none", "unavailable", 0))
        self.assertEqual(row.warnings[0], g.TEMPLATE_DEFERRED_WARNING)
        self.assertIn("HAMCP-264", row.warnings[0])
        self.assertIn("2 template-platform entity registry entries", row.warnings[1])
        self.assertIn("1 template-platform entity registry entry ", g.template_row(1).warnings[1])
        self.assertEqual(g.template_row(None).warnings, [g.TEMPLATE_DEFERRED_WARNING])

    def test_script_enumeration_is_reported_as_deferred(self):
        self.assertTrue(any("HAMCP-264" in gap and "script enumeration" in gap.lower() for gap in SCRIPT_GAPS))


class DashboardWalkTests(unittest.TestCase):
    def test_nested_conditional_stack_and_grid_cards_have_exact_paths(self):
        configuration = {"views": [{"cards": [
            {"type": "conditional",
             "conditions": [{"condition": "state", "entity": "light.synthetic_cond", "state": "on"}],
             "card": {"type": "tile", "entity": "light.synthetic_inner"}},
            {"type": "vertical-stack", "cards": [{"type": "tile", "entity": "light.synthetic_stack"}]},
            {"type": "grid", "cards": [{"type": "button", "entity": "switch.synthetic_grid"}]},
        ]}]}
        reader = FakeReader({"lovelace": configuration})
        result = collect(reader)
        self.assertEqual(result["coverage"].completeness, "complete")
        self.assertEqual(result["coverage"].provider, "upstream_dashboard")
        found = {(item.target_entity_id, item.config_path, item.relation) for item in result["findings"]}
        self.assertEqual(found, {
            ("light.synthetic_cond", "$.views[0].cards[0].conditions[0].entity", "dashboard_condition_entity"),
            ("light.synthetic_inner", "$.views[0].cards[0].card.entity", "dashboard_literal_entity"),
            ("light.synthetic_stack", "$.views[0].cards[1].cards[0].entity", "dashboard_literal_entity"),
            ("switch.synthetic_grid", "$.views[0].cards[2].cards[0].entity", "dashboard_literal_entity"),
        })
        for item in result["findings"]:
            self.assertEqual((item.source_type, item.source_id, item.source_name),
                             ("dashboard", "lovelace", "Synthetic lovelace"))
        conditional = {item.target_entity_id for item in result["findings"]
                       if "only under a condition" in item.evidence_summary}
        self.assertEqual(conditional, {"light.synthetic_cond", "light.synthetic_inner"})
        self.assertEqual((reader.lists, reader.reads), (1, ["lovelace"]))

    def test_custom_card_is_partial_and_keeps_the_other_references(self):
        result = collect(FakeReader({"lovelace": {"views": [{"cards": [
            {"type": "custom:synthetic-card", "entity": "light.synthetic_custom"},
            {"type": "tile", "entity": "light.synthetic_ok"},
        ]}]}}))
        self.assertEqual(result["coverage"].completeness, "partial")
        self.assertEqual(errors(result), {"dashboard_unsupported_component": 1})
        self.assertEqual([item.target_entity_id for item in result["findings"]], ["light.synthetic_ok"])

    def test_dashboard_strategy_is_partial(self):
        result = collect(FakeReader({"lovelace": {"strategy": {"type": "original-states"}}}))
        self.assertEqual(result["coverage"].completeness, "partial")
        self.assertEqual(errors(result), {"dashboard_strategy_not_expanded": 1})
        self.assertEqual(result["findings"], [])

    def test_view_strategy_is_partial(self):
        result = collect(FakeReader({"lovelace": {"views": [{"strategy": {"type": "synthetic"}}]}}))
        self.assertEqual(result["coverage"].completeness, "partial")

    def test_read_failure_is_isolated_to_its_dashboard(self):
        reader = FakeReader(
            {"lovelace": DEFAULT_DASHBOARD,
             "synthetic-broken": DEFAULT_DASHBOARD,
             "synthetic-raises": DEFAULT_DASHBOARD,
             "synthetic-other": {"views": [{"cards": [{"type": "tile", "entity": "light.synthetic_other"}]}]}},
            failures={"synthetic-broken": "upstream_unavailable", "synthetic-raises": RuntimeError("synthetic")},
        )
        result = collect(reader)
        self.assertEqual(result["coverage"].completeness, "partial")
        self.assertEqual(errors(result), {"dashboard_read_upstream_unavailable": 1,
                                          "dashboard_read_or_parse_failure": 1})
        self.assertEqual({item.source_id for item in result["findings"]}, {"lovelace", "synthetic-other"})
        self.assertIn("Dashboards not analyzed after a read failure: synthetic-broken, synthetic-raises",
                      result["coverage"].warnings)
        self.assertEqual(sorted(reader.reads), sorted(reader.documents))

    def test_missing_default_dashboard_is_partial(self):
        result = collect(FakeReader({"synthetic-extra": DEFAULT_DASHBOARD}))
        self.assertEqual(result["coverage"].completeness, "partial")
        self.assertEqual(errors(result), {"dashboard_default_not_listed": 1})
        self.assertTrue(any("lovelace" in warning for warning in result["coverage"].warnings))

    def test_inventory_limit_is_partial_with_a_terminal(self):
        documents = {"lovelace": DEFAULT_DASHBOARD, **{
            f"synthetic-{i}": DEFAULT_DASHBOARD for i in range(g.MAX_DEPENDENCY_DASHBOARDS)}}
        reader = FakeReader(documents)
        result = collect(reader)
        self.assertEqual(result["coverage"].completeness, "partial")
        self.assertEqual(errors(result), {"dashboard_inventory_limit_exceeded": 1})
        self.assertEqual(len(reader.reads), g.MAX_DEPENDENCY_DASHBOARDS)
        self.assertEqual([t.reason_code for t in result["terminals"]], ["dashboard_inventory_limit_exceeded"])
        exact = FakeReader(dict(list(documents.items())[:g.MAX_DEPENDENCY_DASHBOARDS]))
        self.assertEqual(collect(exact)["coverage"].completeness, "complete")

    def test_truncated_or_partial_list_is_partial(self):
        result = collect(FakeReader({"lovelace": DEFAULT_DASHBOARD}, truncated=True))
        self.assertEqual(errors(result), {"dashboard_inventory_limit_exceeded": 1})
        result = collect(FakeReader({"lovelace": DEFAULT_DASHBOARD}, partial=True))
        self.assertEqual(errors(result), {"dashboard_inventory_partial": 1})
        self.assertEqual(result["coverage"].completeness, "partial")

    def test_reads_are_bounded_by_the_concurrency_limit(self):
        documents = {"lovelace": DEFAULT_DASHBOARD, **{f"synthetic-{i}": DEFAULT_DASHBOARD for i in range(9)}}
        result = collect(FakeReader(documents, delay=0.01))
        self.assertEqual(result["coverage"].completeness, "complete")
        self.assertEqual(result["profile"]["maximum_concurrent_reads"], g.DASHBOARD_READ_CONCURRENCY)
        self.assertEqual(result["profile"]["successful_documents"], len(documents))

    def test_finding_limit_is_partial_with_a_terminal(self):
        configuration = {"views": [{"cards": [
            {"type": "tile", "entity": f"light.synthetic_{i}"} for i in range(4)]}]}
        with patch.object(g, "MAX_DASHBOARD_FINDINGS", 3):
            result = collect(FakeReader({"lovelace": configuration}))
        self.assertEqual(result["coverage"].completeness, "partial")
        self.assertEqual(len(result["findings"]), 3)
        self.assertEqual(errors(result), {"dashboard_finding_limit_exceeded": 1})
        with patch.object(g, "MAX_DASHBOARD_FINDINGS", 4):
            self.assertEqual(collect(FakeReader({"lovelace": configuration}))["coverage"].completeness, "complete")

    def test_projection_truncation_is_partial_with_a_terminal(self):
        configuration = {"views": [{"cards": [
            {"type": "tile", "entity": f"light.synthetic_{i}"} for i in range(dc.ITEMS + 10)]}]}
        result = collect(FakeReader({"lovelace": configuration}))
        self.assertEqual(result["coverage"].completeness, "partial")
        self.assertEqual(errors(result), {"dashboard_projection_limit_exceeded": 1})
        self.assertEqual(walk(configuration)["gaps"], {})
        self.assertIn(("lovelace", "dashboard_projection_limit_exceeded"),
                      [(t.source_id, t.reason_code) for t in result["terminals"]])

    def test_no_reader_is_unavailable(self):
        result = collect(None)
        self.assertEqual((result["coverage"].completeness, result["coverage"].provider), ("unavailable", "none"))
        self.assertEqual(errors(result), {"dashboard_provider_unavailable": 1})
        self.assertIn("no fallback", result["coverage"].policy)

    def test_list_failure_is_unavailable_with_the_reason(self):
        result = collect(FakeReader({}, list_error=dc.AnalysisError("authority_unavailable")))
        self.assertEqual(result["coverage"].completeness, "unavailable")
        self.assertEqual(errors(result), {"dashboard_inventory_authority_unavailable": 1})
        result = collect(FakeReader({}, list_error=RuntimeError("synthetic")))
        self.assertEqual(errors(result), {"dashboard_inventory_unavailable": 1})

    def test_retired_authority_withholds_cached_dashboard_evidence(self):
        reader = FakeReader({"lovelace": DEFAULT_DASHBOARD})
        dashboards = collect(reader)
        sources = asyncio.run(g.collect_global_sources(
            [SCENE], [], registry_complete=True, secret=SECRET, dashboards=dashboards))
        self.assertIn("dashboard", {item.source_type for item in sources.visible().findings})
        reader.is_current = False
        visible = sources.visible()
        self.assertEqual({item.source_type for item in visible.findings}, {"scene"})
        self.assertEqual(visible.coverage_for("dashboard").completeness, "unavailable")
        self.assertNotEqual(visible.fingerprint, sources.fingerprint)

    def test_retired_authority_after_the_list_stops_further_reads(self):
        reader = FakeReader({"lovelace": DEFAULT_DASHBOARD}, current=False)
        result = collect(reader)
        self.assertEqual((reader.lists, reader.reads), (1, []))
        self.assertEqual(result["coverage"].completeness, "partial")
        self.assertEqual(errors(result), {"dashboard_not_read": 1})

    def test_untrusted_title_and_segments_are_bounded(self):
        findings = g.dashboard_findings("lovelace", "T" * 5000, [
            {"kind": "entity_reference", "entity_id": "light.ok", "rule_id": "literal_entity",
             "pointer": "/views/0/" + "a b" + "/entity", "conditional": False},
            {"kind": "entity_reference", "entity_id": "light.bad", "rule_id": "Bad Rule!", "pointer": "/x"},
            {"kind": "coverage_gap", "entity_id": "light.gap", "rule_id": "coverage", "pointer": "/y"},
        ], secret=SECRET)
        self.assertEqual([item.target_entity_id for item in findings], ["light.ok"])
        self.assertEqual(findings[0].config_path, "$.views[0].<key>.entity")
        self.assertTrue(findings[0].source_name.startswith("T" * 150))
        self.assertLess(len(findings[0].source_name), 200)


class FakeClock:
    """Simulated monotonic clock; only the test advances it."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class ControlledSleep:
    """The watchdog's sleep; it returns only when the test fires it."""

    def __init__(self):
        self.fire = asyncio.Event()
        self.durations: list[float] = []

    async def __call__(self, seconds):
        self.durations.append(seconds)
        await self.fire.wait()


class BudgetReader(FakeReader):
    """A reader whose list and reads move the simulated clock or never return."""

    def __init__(self, documents, clock, *, session_cost=0.0, list_cost=0.0, read_cost=0.0,
                 hang_list=False, hang_reads=False, on_hang=None, **kwargs):
        super().__init__(documents, **kwargs)
        self.clock = clock
        self.session_cost = session_cost
        self.list_cost, self.read_cost = list_cost, read_cost
        self.hang_list, self.hang_reads, self.on_hang = hang_list, hang_reads, on_hang
        self.sessions_open = 0
        self.sessions_closed = 0
        self.cancelled: list[str] = []

    @asynccontextmanager
    async def session(self):
        self.sessions_open += 1
        self.clock.now += self.session_cost
        try:
            yield self
        finally:
            self.sessions_closed += 1

    async def _hang(self, name):
        if self.on_hang is not None:
            self.on_hang(name)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.append(name)
            raise

    async def list(self, limit):
        if self.hang_list:
            self.lists += 1
            await self._hang("list")
        self.clock.now += self.list_cost
        return await super().list(limit)

    async def read(self, path):
        if self.hang_reads:
            self.reads.append(path)
            await self._hang(path)
        self.clock.now += self.read_cost
        return await super().read(path)


# Failure detector only: a correct collector finishes on the simulated clock and
# never reaches it. It turns broken cancellation into a failure, not a hang.
HANG_GUARD_SECONDS = 30


def collect_budget(reader, clock, sleep):
    return asyncio.run(asyncio.wait_for(
        g.collect_dashboard_sources(reader, secret=SECRET, clock=clock, sleep=sleep), HANG_GUARD_SECONDS,
    ))


class DashboardBudgetTests(unittest.TestCase):
    """R1-M1: one deadline covers the session, the list and every read."""

    DOCUMENTS = {"lovelace": DEFAULT_DASHBOARD, **{
        f"synthetic-{i}": {"views": [{"cards": [{"type": "tile", "entity": f"light.synthetic_{i}"}]}]}
        for i in range(7)}}

    def test_list_that_never_returns_is_unavailable_and_reads_nothing(self):
        clock, sleep = FakeClock(), ControlledSleep()
        reader = BudgetReader(self.DOCUMENTS, clock, hang_list=True, on_hang=lambda _: sleep.fire.set())
        result = collect_budget(reader, clock, sleep)
        self.assertEqual(result["coverage"].completeness, "unavailable")
        self.assertEqual(errors(result), {"dashboard_scan_budget_exhausted": 1})
        self.assertEqual((reader.lists, reader.reads, reader.cancelled), (1, [], ["list"]))
        self.assertEqual((reader.sessions_open, reader.sessions_closed), (1, 1))
        self.assertEqual(sleep.durations, [g.DASHBOARD_SCAN_SECONDS])
        self.assertEqual([t.reason_code for t in result["terminals"]], ["dashboard_scan_budget_exhausted"])

    def test_session_that_consumes_the_budget_lists_nothing(self):
        clock, sleep = FakeClock(), ControlledSleep()
        reader = BudgetReader(self.DOCUMENTS, clock, session_cost=g.DASHBOARD_SCAN_SECONDS)
        result = collect_budget(reader, clock, sleep)
        self.assertEqual(result["coverage"].completeness, "unavailable")
        self.assertEqual(errors(result), {"dashboard_scan_budget_exhausted": 1})
        self.assertEqual((reader.lists, reader.reads), (0, []))
        self.assertEqual((reader.sessions_open, reader.sessions_closed), (1, 1))

    def test_list_that_consumes_the_budget_starts_no_read(self):
        clock, sleep = FakeClock(), ControlledSleep()
        reader = BudgetReader(self.DOCUMENTS, clock, list_cost=g.DASHBOARD_SCAN_SECONDS)
        result = collect_budget(reader, clock, sleep)
        self.assertEqual(result["coverage"].completeness, "partial")
        self.assertEqual(reader.reads, [])
        self.assertEqual(errors(result), {"dashboard_scan_budget_exhausted": 1,
                                          "dashboard_not_read": len(self.DOCUMENTS)})
        self.assertEqual(result["findings"], [])
        self.assertEqual((reader.sessions_open, reader.sessions_closed), (1, 1))

    def test_budget_overrun_mid_list_leaves_the_rest_unread(self):
        clock, sleep = FakeClock(), ControlledSleep()
        reader = BudgetReader(self.DOCUMENTS, clock, read_cost=2.0)
        result = collect_budget(reader, clock, sleep)
        self.assertEqual(result["coverage"].completeness, "partial")
        self.assertEqual(len(reader.reads), 3)
        self.assertEqual(errors(result), {"dashboard_scan_budget_exhausted": 1,
                                          "dashboard_not_read": len(self.DOCUMENTS) - 3})
        self.assertEqual({item.source_id for item in result["findings"]}, set(reader.reads))
        self.assertIn(("dependency_provider", "dashboard_scan_budget_exhausted"),
                      [(t.source_id, t.reason_code) for t in result["terminals"]])

    def test_reads_in_flight_at_the_deadline_are_cancelled_and_counted(self):
        clock, sleep = FakeClock(), ControlledSleep()
        started = []

        def on_hang(name):
            started.append(name)
            if len(started) == g.DASHBOARD_READ_CONCURRENCY:
                sleep.fire.set()

        reader = BudgetReader(self.DOCUMENTS, clock, hang_reads=True, on_hang=on_hang)
        result = collect_budget(reader, clock, sleep)
        self.assertEqual(result["coverage"].completeness, "partial")
        self.assertEqual(len(reader.reads), g.DASHBOARD_READ_CONCURRENCY)
        self.assertEqual(sorted(reader.cancelled), sorted(reader.reads))
        self.assertEqual(errors(result), {"dashboard_scan_budget_exhausted": 1,
                                          "dashboard_not_read": len(self.DOCUMENTS)})
        self.assertEqual((reader.sessions_open, reader.sessions_closed), (1, 1))

    def test_within_the_budget_is_complete(self):
        clock, sleep = FakeClock(), ControlledSleep()
        cost = g.DASHBOARD_SCAN_SECONDS / (len(self.DOCUMENTS) + 2)
        reader = BudgetReader(self.DOCUMENTS, clock, list_cost=cost, read_cost=cost)
        result = collect_budget(reader, clock, sleep)
        self.assertEqual(result["coverage"].completeness, "complete")
        self.assertEqual(errors(result), {})
        self.assertEqual(sorted(reader.reads), sorted(self.DOCUMENTS))
        self.assertEqual(result["terminals"], [])
        self.assertLess(clock.now, g.DASHBOARD_SCAN_SECONDS)

    def test_oversize_document_is_partial_with_a_document_terminal(self):
        reader = FakeReader(self.DOCUMENTS, failures={"synthetic-3": "response_limit"})
        result = collect(reader)
        self.assertEqual(result["coverage"].completeness, "partial")
        self.assertEqual(errors(result), {"dashboard_read_response_limit": 1})
        self.assertEqual([(t.source_id, t.reason_code) for t in result["terminals"]],
                         [("synthetic-3", "dashboard_document_limit_exceeded")])
        self.assertNotIn("synthetic-3", {item.source_id for item in result["findings"]})
        self.assertEqual(len({item.source_id for item in result["findings"]}), len(self.DOCUMENTS) - 1)


class FakeCoreRuntime:
    def __init__(self):
        self.calls: list[str] = []
        self.available = True
        self.generation = 1
        self.acquire_result = object()
        self.revalidates = True
        self.current_observation = SimpleNamespace(version=rules.CORE_VERSION, fingerprint="f" * 64)

    def route_status(self, capabilities):
        assert tuple(capabilities) == CAPABILITIES
        return {"available": self.available, "generation": self.generation}

    def acquire(self, capabilities):
        assert tuple(capabilities) == CAPABILITIES
        self.calls.append("acquire")
        return self.acquire_result

    def consume(self, authority):
        self.calls.append("consume")
        return ("commit",)

    def revalidate(self, authority, commits):
        return self.revalidates

    def finish(self, commits):
        self.calls.append("finish")

    def release(self, authority):
        self.calls.append("release")


class FakeUpstream:
    def __init__(self, documents, *, listed=None):
        self.documents = documents
        self.listed = listed
        self.calls: list[tuple] = []
        self.callers: list[str | None] = []
        self.authority = "synthetic-upstream"

    def analysis_authority(self):
        return self.authority

    async def list_dashboards(self, *, limit, response_limit):
        self.calls.append(("list", limit))
        self.callers.append(current_telemetry().caller_id)
        paths = self.listed if self.listed is not None else [{"url_path": p, "title": p} for p in self.documents]
        return SimpleNamespace(data={"dashboards": paths, "truncated": False}, completeness="complete")

    async def get_analysis_configuration(self, *, url_path, authorize, budget):
        authorize()
        self.calls.append(("read", url_path))
        self.callers.append(current_telemetry().caller_id)
        return ({"configuration": self.documents[url_path], "config_hash": "c" * 64,
                 "engineering_config_hash": "e" * 64}, {})


class FakeAudit:
    def __init__(self):
        self.events: list[dict] = []

    def write(self, event):
        self.events.append(event)


class DashboardDependencyReaderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.core = FakeCoreRuntime()
        self.audit = FakeAudit()
        self.upstream = FakeUpstream({"lovelace": DEFAULT_DASHBOARD, "synthetic-two": DEFAULT_DASHBOARD})
        self.reader = DashboardDependencyReader(self.core, self.upstream, audit=self.audit,
                                                known_secrets=(SECRET,))

    async def collect(self, authorizer=lambda: True):
        telemetry, token = begin_request()
        telemetry.caller_id = "synthetic-caller"
        telemetry.core_dispatch_authorizer = authorizer
        try:
            return telemetry, await g.collect_dashboard_sources(self.reader, secret=SECRET)
        finally:
            end_request(token)

    async def test_one_list_and_one_read_per_dashboard_with_attributed_audit(self):
        parent, result = await self.collect()
        self.assertEqual(result["coverage"].completeness, "complete")
        self.assertEqual(self.upstream.calls[0], ("list", g.MAX_DEPENDENCY_DASHBOARDS + 1))
        self.assertEqual(sorted(self.upstream.calls[1:]), [("read", "lovelace"), ("read", "synthetic-two")])
        self.assertEqual(len(self.upstream.calls), 3)
        self.assertEqual(self.core.calls, ["acquire", "consume", "finish"])
        self.assertEqual(sorted(event["event"] for event in self.audit.events),
                         ["dependency_dashboard_list", "dependency_dashboard_read", "dependency_dashboard_read"])
        for event in self.audit.events:
            self.assertEqual(event["parent_request_id"], parent.request_id)
            self.assertNotEqual(event["request_id"], parent.request_id)
            self.assertEqual((event["provider"], event["result_status"], event["fallback_occurred"]),
                             ("upstream_dashboard", "success", False))
        self.assertEqual(self.upstream.callers, ["synthetic-caller"] * 3)
        self.assertTrue(self.reader.current())
        self.assertIn("no fallback", result["coverage"].policy)

    async def test_no_parent_authority_dispatches_nothing(self):
        result = await g.collect_dashboard_sources(self.reader, secret=SECRET)
        self.assertEqual(result["coverage"].completeness, "unavailable")
        self.assertEqual(errors(result), {"dashboard_inventory_authority_unavailable": 1})
        self.assertEqual((self.upstream.calls, self.core.calls, self.audit.events), ([], [], []))

    async def test_parent_without_a_dispatch_authorizer_acquires_nothing(self):
        telemetry, token = begin_request()
        try:
            self.assertIsNone(telemetry.core_dispatch_authorizer)
            result = await g.collect_dashboard_sources(self.reader, secret=SECRET)
        finally:
            end_request(token)
        self.assertEqual(result["coverage"].completeness, "unavailable")
        self.assertEqual((self.upstream.calls, self.core.calls, self.audit.events), ([], [], []))

    async def test_refused_parent_authorizer_dispatches_nothing(self):
        _, result = await self.collect(authorizer=lambda: False)
        self.assertEqual(result["coverage"].completeness, "unavailable")
        self.assertEqual(self.upstream.calls, [])
        self.assertEqual(self.core.calls, ["acquire", "release"])
        self.assertEqual([event["result_status"] for event in self.audit.events], ["failed_or_cancelled"])

    async def test_refused_lease_dispatches_nothing(self):
        self.core.acquire_result = None
        _, result = await self.collect()
        self.assertEqual(result["coverage"].completeness, "unavailable")
        self.assertEqual(self.upstream.calls, [])
        self.assertEqual(self.core.calls, ["acquire"])

    async def test_failed_revalidation_stops_reads(self):
        self.core.revalidates = False
        _, result = await self.collect()
        self.assertEqual(self.upstream.calls, [])
        self.assertEqual(result["coverage"].completeness, "unavailable")
        self.assertEqual(self.core.calls, ["acquire", "consume", "finish"])

    async def test_route_drift_after_the_list_stops_reads(self):
        telemetry, token = begin_request()
        telemetry.core_dispatch_authorizer = lambda: True
        try:
            async with self.reader.session() as session:
                await session.list(5)
                self.core.generation = 2
                self.assertEqual(await session.read("lovelace"), {"failure": "authority_drift"})
        finally:
            end_request(token)
        self.assertFalse(self.reader.current())
        self.assertEqual([call[0] for call in self.upstream.calls], ["list"])

    async def test_upstream_authority_drift_refuses_the_read_before_dispatch(self):
        telemetry, token = begin_request()
        telemetry.core_dispatch_authorizer = lambda: True
        try:
            async with self.reader.session() as session:
                await session.list(5)
                self.upstream.authority = "synthetic-upstream-replaced"
                self.assertEqual(await session.read("lovelace"), {"failure": "authority_drift"})
        finally:
            end_request(token)
        self.assertEqual([call[0] for call in self.upstream.calls], ["list"])
        self.assertEqual([event["event"] for event in self.audit.events], ["dependency_dashboard_list"])

    async def test_oversize_document_is_refused_by_the_reviewed_size_cap(self):
        from ha_mcp_engineering.providers.upstream_dashboard import _analysis_configuration

        oversize = {"content": [{"type": "text", "text": " " * (dc.DASHBOARD_BYTES + 1)}]}
        original = self.upstream.get_analysis_configuration

        async def read(*, url_path, authorize, budget):
            if url_path == "synthetic-two":
                authorize()
                self.upstream.calls.append(("read", url_path))
                return _analysis_configuration(oversize, url_path), {}
            return await original(url_path=url_path, authorize=authorize, budget=budget)

        self.upstream.get_analysis_configuration = read
        _, result = await self.collect()
        self.assertEqual(result["coverage"].completeness, "partial")
        self.assertEqual(errors(result), {"dashboard_read_response_limit": 1})
        self.assertEqual([(t.source_id, t.reason_code) for t in result["terminals"]],
                         [("synthetic-two", "dashboard_document_limit_exceeded")])
        self.assertEqual({item.source_id for item in result["findings"]}, {"lovelace"})
        self.assertEqual(self.core.calls, ["acquire", "consume", "finish"])

    async def test_invalid_and_secret_shaped_paths_are_screened(self):
        self.upstream.listed = [{"url_path": "lovelace", "title": 5},
                                {"url_path": "Bad Path"}, {"url_path": SECRET}, "not-a-dict"]
        _, result = await self.collect()
        self.assertEqual([call for call in self.upstream.calls if call[0] == "read"], [("read", "lovelace")])
        self.assertEqual(errors(result), {"dashboard_inventory_partial": 1})
        self.assertNotIn(SECRET, repr(result))
        telemetry, token = begin_request()
        telemetry.core_dispatch_authorizer = lambda: True
        try:
            async with self.reader.session() as session:
                await session.list(5)
                self.assertEqual(await session.read("../x"), {"failure": "invalid_arguments"})
        finally:
            end_request(token)


class DependencyBuildTests(unittest.IsolatedAsyncioTestCase):
    async def test_scenes_and_groups_make_no_extra_request(self):
        without = Home(scenes=False)
        await build(without)
        with_sources = Home(scenes=True)
        _, snapshot, _, _ = await build(with_sources)
        self.assertEqual(with_sources.rest_counts, without.rest_counts)
        self.assertEqual(with_sources.ws_counts, without.ws_counts)
        self.assertEqual(len(with_sources.batch_calls), len(without.batch_calls))
        self.assertEqual({item.source_type for item in snapshot.global_sources.findings}, {"scene", "group"})

    async def test_dashboards_cost_one_list_and_one_read_each(self):
        home = Home()
        reader = FakeReader({"lovelace": DEFAULT_DASHBOARD, "synthetic-two": DEFAULT_DASHBOARD})
        _, snapshot, _, _ = await build(home, reader)
        self.assertEqual((reader.lists, sorted(reader.reads)), (1, ["lovelace", "synthetic-two"]))
        operations = snapshot.build_profile["request_count_by_operation"]
        self.assertEqual((operations["dashboard_list"], operations["dashboard_configuration"]), (1, 2))
        baseline = Home()
        await build(baseline)
        self.assertEqual(home.rest_counts, baseline.rest_counts)
        self.assertEqual(home.ws_counts, baseline.ws_counts)

    async def test_snapshot_fingerprint_and_rows_ignore_the_partition(self):
        _, plain, _, _ = await build(Home(scenes=False))
        _, rich, _, _ = await build(Home(), FakeReader({"lovelace": DEFAULT_DASHBOARD}))
        self.assertEqual(rich.fingerprint, plain.fingerprint)
        self.assertEqual(rich.findings, plain.findings)
        self.assertEqual(rich.obligations, plain.obligations)
        for source_type in g.GLOBAL_SOURCE_TYPES:
            row = next(item for item in rich.coverage if item.source_type == source_type)
            self.assertEqual(row, next(item for item in plain.coverage if item.source_type == source_type))
            self.assertEqual(row.completeness, "unavailable")
        self.assertFalse(set(g.GLOBAL_SOURCE_TYPES) & {item.source_type for item in rich.findings})

    async def test_partition_rows_and_template_count(self):
        home = Home(extra_registry=[registry_row("sensor.synthetic_template", "template")])
        _, snapshot, _, _ = await build(home, FakeReader({"lovelace": DEFAULT_DASHBOARD}))
        rows = {item.source_type: item for item in snapshot.global_sources.coverage}
        self.assertEqual({key: (row.provider, row.completeness) for key, row in rows.items()}, {
            "scene": ("direct_ha_api", "complete"), "group": ("direct_ha_api", "complete"),
            "template": ("none", "unavailable"), "dashboard": ("upstream_dashboard", "complete"),
        })
        self.assertIn("1 template-platform", rows["template"].warnings[1])

    async def test_incomplete_build_registry_marks_scenes_and_groups_partial(self):
        home = Home()
        websocket = CountingWebSocket(home)
        command = websocket.command

        async def invalid_registry(payload):
            if payload.get("type") == "config/entity_registry/list":
                return {"synthetic": "not a list"}
            return await command(payload)

        websocket.command = invalid_registry
        index = DependencyIndex(DirectHaDependencyProvider(_AnalysisRest(home), websocket, secret=SECRET))
        with patch(BLUEPRINT_READER, side_effect=read_blueprint):
            snapshot, _, _ = await index.get(refresh=True)
        for source_type in ("scene", "group"):
            row = snapshot.global_sources.coverage_for(source_type)
            self.assertEqual(row.completeness, "partial", source_type)
            self.assertTrue(row.failed_item_count, source_type)

    async def test_failing_dashboard_reader_factory_is_unavailable(self):
        home = Home()
        rest, websocket = _AnalysisRest(home), CountingWebSocket(home)

        def broken():
            raise RuntimeError("synthetic")

        index = DependencyIndex(DirectHaDependencyProvider(
            rest, websocket, secret=SECRET, dashboard_reader_factory=broken))
        with patch(BLUEPRINT_READER, side_effect=read_blueprint):
            snapshot, _, _ = await index.get(refresh=True)
        row = snapshot.global_sources.coverage_for("dashboard")
        self.assertEqual((row.provider, row.completeness), ("none", "unavailable"))
        self.assertEqual(snapshot.global_sources.coverage_for("scene").completeness, "complete")

    async def test_exhausted_dashboard_budget_preserves_the_other_sources(self):
        home = Home()
        _, baseline, _, _ = await build(home)
        reader = BudgetReader({"lovelace": DEFAULT_DASHBOARD}, FakeClock(), hang_list=True)
        with patch.object(g, "DASHBOARD_SCAN_SECONDS", 0.0):
            _, snapshot, _, _ = await build(home, reader)
        self.assertEqual((reader.lists, reader.reads), (0, []))
        self.assertEqual((reader.sessions_open, reader.sessions_closed), (1, 1))
        row = snapshot.global_sources.coverage_for("dashboard")
        self.assertEqual(row.completeness, "unavailable")
        self.assertIn("dashboard_scan_budget_exhausted: 1", row.warnings)
        for source_type in ("scene", "group"):
            self.assertEqual(snapshot.global_sources.coverage_for(source_type).completeness, "complete")
        self.assertEqual(snapshot.fingerprint, baseline.fingerprint)
        self.assertEqual(snapshot.findings, baseline.findings)
        self.assertEqual(
            {item.source_type for item in snapshot.global_sources.findings}, {"scene", "group"})

    async def test_index_truncation_marks_every_dropped_source_partial(self):
        async def build_truncated(max_edges):
            home = SyntheticScriptHome(representative_automations(), [])
            provider = DirectHaDependencyProvider(_AnalysisRest(home), CountingWebSocket(home), secret=SECRET)
            scan = provider.scan

            async def complete_scan():
                result = await scan()
                return replace(result, coverage=[
                    replace(item, completeness="complete", warnings=[])
                    if item.source_type in {"automation", "blueprint"} else item
                    for item in result.coverage
                ])

            provider.scan = complete_scan
            index = DependencyIndex(provider)
            index.max_edges = max_edges  # below the constructor clamp, to force truncation
            with patch(BLUEPRINT_READER, side_effect=read_blueprint):
                snapshot, _, _ = await index.get(refresh=True)
            return snapshot

        full = await build_truncated(10_000)
        rows = {item.source_type: item for item in full.coverage}
        self.assertEqual((rows["automation"].completeness, rows["blueprint"].completeness), ("complete", "complete"))
        ordered = sorted(full.findings, key=lambda item: item.evidence_id)
        blueprint_positions = [n for n, item in enumerate(ordered) if item.source_type == "blueprint"]
        self.assertTrue(blueprint_positions)
        # Keep everything before the last blueprint finding: blueprint is dropped.
        truncated = await build_truncated(blueprint_positions[-1])
        rows = {item.source_type: item for item in truncated.coverage}
        self.assertEqual(rows["blueprint"].completeness, "partial")
        self.assertIn("Blueprint dependency evidence exceeded the bounded index payload.", rows["blueprint"].warnings)
        self.assertEqual(rows["automation"].completeness, "partial")
        # Only sources with dropped evidence change.
        self.assertEqual(rows["entity_metadata"].completeness,
                         {item.source_type: item for item in full.coverage}["entity_metadata"].completeness)

    async def test_health_never_builds(self):
        home = Home()
        reader = FakeReader({"lovelace": DEFAULT_DASHBOARD})
        index = DependencyIndex(DirectHaDependencyProvider(
            _AnalysisRest(home), CountingWebSocket(home), secret=SECRET,
            dashboard_reader_factory=lambda: reader))
        index.health()
        self.assertEqual((reader.lists, reader.reads), (0, []))
        self.assertEqual(sum(home.rest_counts.values()), 0)
        self.assertEqual(sum(home.ws_counts.values()), 0)


class ConsumerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.home = Home()
        self.reader = FakeReader({"lovelace": DEFAULT_DASHBOARD})
        self.index, self.snapshot, self.rest, self.websocket = await build(self.home, self.reader)
        self.patch = patch(BLUEPRINT_READER, side_effect=read_blueprint)
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()

    async def dependency(self, entity_id, **kwargs):
        output = await EntityDependencyAnalysisService(self.index).analyze(
            entity_id=entity_id, detail_level="evidence", **kwargs)
        return output.data

    async def impact(self, entity_id, **kwargs):
        provider = DirectHaImpactProvider(self.index, self.rest, self.websocket, secret=SECRET, ha_token=SECRET)
        output = await ChangeImpactAnalysisService(provider).analyze(
            entity_id=entity_id, operation="remove_entity", detail_level="evidence", **kwargs)
        return output.data

    async def test_dependency_analysis_shows_scene_group_and_dashboard_only_references(self):
        for entity_id, source_type, source_id, provider in (
            ("light.synthetic_only_scene", "scene", "scene.synthetic_evening", "direct_ha_api"),
            ("light.synthetic_only_group", "group", "group.synthetic_downstairs", "direct_ha_api"),
            ("light.synthetic_only_dashboard", "dashboard", "lovelace", "upstream_dashboard"),
        ):
            with self.subTest(source_type=source_type):
                data = await self.dependency(entity_id)
                self.assertEqual(data["overview"]["dependency_status"], "referenced")
                self.assertEqual([(item["source_type"], item["source_id"]) for item in data["findings"]],
                                 [(source_type, source_id)])
                self.assertFalse(data["overview"]["coverage_complete"])
                rows = data["source_coverage"]
                self.assertEqual(coverage_row(rows, source_type)["provider"], provider)
                self.assertEqual(coverage_row(rows, source_type)["completeness"], "complete")
                template = coverage_row(rows, "template")
                self.assertEqual(template["completeness"], "unavailable")
                self.assertTrue(any("HAMCP-264" in warning for warning in template["warnings"]))

    async def test_dependency_source_types_filter_excludes_the_partition(self):
        data = await self.dependency("light.synthetic_shared", source_types=["automation", "script"])
        self.assertEqual(data["findings"], [])
        self.assertEqual(data["overview"]["dependency_status"], "not_detected")
        data = await self.dependency("light.synthetic_shared", source_types=["group"])
        self.assertEqual([item["source_type"] for item in data["findings"]], ["group"])

    async def test_impact_analysis_shows_scene_group_and_dashboard_only_references(self):
        for entity_id, object_type, object_id in (
            ("light.synthetic_only_scene", "scene", "scene.synthetic_evening"),
            ("light.synthetic_only_group", "group", "group.synthetic_downstairs"),
            ("light.synthetic_only_dashboard", "dashboard", "dashboard:lovelace"),
        ):
            with self.subTest(object_type=object_type):
                data = await self.impact(entity_id, source_types=ALL_SOURCES)
                affected = {(item["affected_object_type"], item["affected_object_id"])
                            for item in data["findings"] if item["rule_id"] == "remove_orphaned_consumer"}
                self.assertIn((object_type, object_id), affected)
                self.assertEqual(data["final_assessment"], "review_required")
                row = next(item for item in data["source_coverage_matrix"]
                           if item["source_type"] == object_type)
                self.assertEqual(row["completeness"], "complete")

    async def test_impact_source_types_filter_excludes_the_partition(self):
        data = await self.impact("light.synthetic_only_dashboard", source_types=["automation", "script"])
        self.assertFalse(any(item["affected_object_type"] == "dashboard" for item in data["findings"]))
        self.assertNotEqual(data["final_assessment"], "no_known_impacts_with_complete_coverage")

    async def test_impact_keeps_an_unavailable_dashboard_row_unavailable(self):
        index, _, rest, websocket = await build(
            Home(), FakeReader({}, list_error=dc.AnalysisError("authority_unavailable")))
        provider = DirectHaImpactProvider(index, rest, websocket, secret=SECRET, ha_token=SECRET)
        output = await ChangeImpactAnalysisService(provider).analyze(
            entity_id="light.synthetic_unreferenced", operation="remove_entity",
            detail_level="evidence", source_types=ALL_SOURCES)
        rows = {item["source_type"]: item for item in output.data["source_coverage_matrix"]}
        self.assertEqual((rows["dashboard"]["provider"], rows["dashboard"]["completeness"]),
                         ("upstream_dashboard", "unavailable"))
        self.assertNotEqual(output.data["final_assessment"], "no_known_impacts_with_complete_coverage")

    async def test_impact_reports_deferred_template_coverage(self):
        data = await self.impact("light.synthetic_unreferenced", source_types=ALL_SOURCES)
        self.assertNotEqual(data["final_assessment"], "no_known_impacts_with_complete_coverage")
        self.assertFalse(any(item["rule_id"] == "remove_orphaned_consumer" for item in data["findings"]))
        incomplete = [item for item in data["findings"] if item["rule_id"] == "source_coverage_incomplete"]
        self.assertTrue(incomplete)
        self.assertIn("template", incomplete[0]["source_coverage"])
        self.assertNotIn("dashboard", incomplete[0]["source_coverage"])
        template = next(item for item in data["source_coverage_matrix"] if item["source_type"] == "template")
        # Nothing reads templates, so no provider is claimed for them.
        self.assertEqual((template["provider"], template["completeness"]), ("none", "not_supported"))
        self.assertTrue(any("HAMCP-264" in warning for warning in template["warnings"]))


class ImpactPaginationBindingTests(unittest.IsolatedAsyncioTestCase):
    """R1-M2: impact cursors are bound to the visible diagnostic partition."""

    TARGET = "light.synthetic_only_dashboard"

    async def asyncSetUp(self):
        self.home = Home()
        self.documents = {"lovelace": DEFAULT_DASHBOARD, **{
            f"synthetic-{i}": {"views": [{"cards": [{"type": "tile", "entity": self.TARGET}]}]}
            for i in range(5)}}
        self.reader = FakeReader(self.documents)
        self.index, _, self.rest, self.websocket = await build(self.home, self.reader)
        self.patch = patch(BLUEPRINT_READER, side_effect=read_blueprint)
        self.patch.start()
        self.service = ChangeImpactAnalysisService(DirectHaImpactProvider(
            self.index, self.rest, self.websocket, secret=SECRET, ha_token=SECRET))
        METRICS.reset()

    async def asyncTearDown(self):
        self.patch.stop()
        METRICS.reset()

    async def page(self, cursor="", limit=1):
        output = await self.service.analyze(
            entity_id=self.TARGET, operation="remove_entity", detail_level="evidence",
            source_types=ALL_SOURCES, limit=limit, cursor=cursor)
        return output.data

    async def stale(self, cursor):
        with self.assertRaises(GovernanceError) as raised:
            await self.page(cursor)
        self.assertEqual(raised.exception.code, ErrorCode.STALE_CURSOR)
        return raised.exception.details["reason"]

    def governance_binding(self):
        snapshot = self.index.snapshot
        return (snapshot.fingerprint, snapshot.generation, snapshot.findings, snapshot.obligations)

    def metrics(self):
        return METRICS.snapshot()["change_impact_analysis"]

    def dashboard_ids(self, findings):
        return {item["affected_object_id"] for item in findings
                if item["affected_object_type"] == "dashboard"}

    async def test_unchanged_partition_pages_are_consistent_and_read_nothing(self):
        whole = await self.page(limit=100)
        reads = list(self.reader.reads)
        first = await self.page()
        pages, cursor = [first], first["pagination"]["next_cursor"]
        while cursor:
            pages.append(await self.page(cursor))
            cursor = pages[-1]["pagination"]["next_cursor"]
        ids = [item["finding_id"] for data in pages for item in data["findings"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sorted(ids), sorted(item["finding_id"] for item in whole["findings"]))
        self.assertEqual(len(self.dashboard_ids(whole["findings"])), len(self.documents))
        self.assertEqual(self.reader.reads, reads)
        metrics = self.metrics()
        self.assertEqual(metrics["cursor_continuations"], len(pages) - 1)
        self.assertEqual(metrics["stale_cursor_events"], 0)

    async def test_retired_dashboard_authority_makes_the_cursor_stale(self):
        first = await self.page()
        reads, binding = list(self.reader.reads), self.governance_binding()
        self.reader.is_current = False
        self.assertEqual(await self.stale(first["pagination"]["next_cursor"]), "diagnostic_evidence_changed")
        metrics = self.metrics()
        self.assertEqual((metrics["cursor_continuations"], metrics["stale_cursor_events"]), (1, 1))
        fresh = await self.page(limit=100)
        self.assertEqual(self.dashboard_ids(fresh["findings"]), set())
        row = next(item for item in fresh["source_coverage_matrix"] if item["source_type"] == "dashboard")
        self.assertEqual(row["completeness"], "unavailable")
        self.assertEqual(self.reader.reads, reads)
        self.assertEqual(self.governance_binding(), binding)

    async def test_replaced_partition_without_an_index_rebuild_makes_the_cursor_stale(self):
        first = await self.page()
        binding = self.governance_binding()
        smaller = FakeReader({"lovelace": DEFAULT_DASHBOARD})
        dashboards = await g.collect_dashboard_sources(smaller, secret=SECRET)
        snapshot = self.index.snapshot
        self.index.snapshot = replace(snapshot, global_sources=await g.collect_global_sources(
            [], [], registry_complete=True, secret=SECRET, dashboards=dashboards))
        self.assertEqual(self.governance_binding(), binding)
        self.assertEqual(await self.stale(first["pagination"]["next_cursor"]), "diagnostic_evidence_changed")
        self.assertEqual(self.metrics()["stale_cursor_events"], 1)

    async def test_index_refresh_makes_the_cursor_stale(self):
        first = await self.page()
        await self.index.get(refresh=True)
        self.assertEqual(await self.stale(first["pagination"]["next_cursor"]),
                         "active_index_replaced_or_invalidated")
        self.assertEqual(self.metrics()["stale_cursor_events"], 1)

    async def test_diagnostic_change_before_commit_refuses_the_cursor(self):
        provider = self.service.provider
        with patch.object(type(provider), "active_diagnostic_identity", return_value="changed"):
            with self.assertRaises(GovernanceError) as raised:
                await self.page()
        self.assertEqual(raised.exception.code, ErrorCode.ANALYSIS_UNAVAILABLE)
        self.assertEqual(raised.exception.details["reason"], "index_changed_before_snapshot_commit")
        self.assertEqual(self.service.pagination_snapshots._values, {})

    async def test_failing_diagnostic_identity_fails_closed(self):
        first = await self.page()
        with patch.object(type(self.service.provider), "active_diagnostic_identity",
                          side_effect=RuntimeError("synthetic")):
            self.assertEqual(await self.stale(first["pagination"]["next_cursor"]),
                             "diagnostic_evidence_changed")


class PolicyNoDowngradeTests(unittest.IsolatedAsyncioTestCase):
    SEVERITY = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

    async def impact(self, home, reader, entity_id):
        index, _, rest, websocket = await build(home, reader)
        with patch(BLUEPRINT_READER, side_effect=read_blueprint):
            provider = DirectHaImpactProvider(index, rest, websocket, secret=SECRET, ha_token=SECRET)
            output = await ChangeImpactAnalysisService(provider).analyze(
                entity_id=entity_id, operation="remove_entity", detail_level="evidence",
                source_types=ALL_SOURCES)
        return output.data

    def worst(self, data):
        return max((self.SEVERITY[item["severity"]] for item in data["findings"]), default=-1)

    async def test_more_coverage_never_lowers_the_assessment(self):
        for entity_id in TARGETS:
            with self.subTest(entity_id=entity_id):
                plain = await self.impact(Home(scenes=False), None, entity_id)
                rich = await self.impact(Home(), FakeReader({"lovelace": DEFAULT_DASHBOARD}), entity_id)
                self.assertGreaterEqual(self.worst(rich), self.worst(plain))
                self.assertNotEqual(rich["final_assessment"], "no_known_impacts_with_complete_coverage")
                plain_rules = {item["rule_id"] for item in plain["findings"]}
                rich_rules = {item["rule_id"] for item in rich["findings"]}
                self.assertLessEqual(plain_rules - {"source_coverage_incomplete"}, rich_rules)

    async def test_partial_dashboard_coverage_stays_limited(self):
        reader = FakeReader({"lovelace": {"views": [{"cards": [
            {"type": "custom:synthetic-card", "entity": "light.synthetic_unreferenced"}]}]}})
        data = await self.impact(Home(), reader, "light.synthetic_unreferenced")
        self.assertNotEqual(data["final_assessment"], "no_known_impacts_with_complete_coverage")
        incomplete = [item for item in data["findings"] if item["rule_id"] == "source_coverage_incomplete"]
        self.assertIn("dashboard", incomplete[0]["source_coverage"])
        row = next(item for item in data["source_coverage_matrix"] if item["source_type"] == "dashboard")
        self.assertEqual(row["completeness"], "partial")


class BoundTableTests(unittest.TestCase):
    def test_bound_table_names_the_partition_limits(self):
        table = bound_table()
        expected = {
            "scene_member_limit_exceeded": g.MAX_MEMBERS_PER_SOURCE,
            "group_member_limit_exceeded": g.MAX_MEMBERS_PER_SOURCE,
            "scene_finding_limit_exceeded": g.MAX_MEMBERSHIP_FINDINGS,
            "group_finding_limit_exceeded": g.MAX_MEMBERSHIP_FINDINGS,
            "scene_inventory_limit_exceeded": g.MAX_SCENE_SOURCES,
            "group_inventory_limit_exceeded": g.MAX_GROUP_SOURCES,
            "dashboard_inventory_limit_exceeded": g.MAX_DEPENDENCY_DASHBOARDS,
            "dashboard_finding_limit_exceeded": g.MAX_DASHBOARD_FINDINGS,
            "dashboard_projection_limit_exceeded": dc.ITEMS,
            "dashboard_document_limit_exceeded": dc.DASHBOARD_BYTES,
            "dashboard_scan_budget_exhausted": int(g.DASHBOARD_SCAN_SECONDS),
        }
        for reason, limit in expected.items():
            self.assertIn(reason, table)
            self.assertEqual(table[reason][1], limit, reason)


if __name__ == "__main__":
    unittest.main()
