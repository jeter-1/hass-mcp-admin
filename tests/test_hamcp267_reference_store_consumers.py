"""HAMCP-267: integrity, incident and handoff consume the HAMCP-227 partition.

``configuration_integrity_analysis``, ``incident_correlation`` and
``handoff_generation`` read the scene, group and dashboard references from
``snapshot.global_sources.visible()`` -- the diagnostic partition the index
build already read. These tests prove:

* the governance binding (snapshot fingerprint, helper obligation ledger,
  snapshot rows, each output's ``index.fingerprint`` and the HAMCP-270 plan
  bindings) is byte-identical with and without the partition and before and
  after the analyses run;
* each analysis shows scene, group and dashboard references, with exact paths
  and provider attribution, and loaded membership stays labelled as such;
* every incomplete source (custom card, exhausted budget, list failure,
  missing reader, withheld authority, unexposed members, incomplete registry,
  the partition reference cap) is reported partial, failed or as a named
  limitation, never complete and never silently absent, and none escalates
  to an analysis failure;
* withheld dashboard authority removes cached dashboard evidence everywhere;
* pagination cursors are bound to the visible partition and fail closed;
* a scoped reference left out at an incident or handoff bound is disclosed as
  a named limitation and a truncation, never as complete (Codex F1);
* the source_types filter excludes the partition;
* membership state never raises a finding's severity;
* the analyses make no new request, and an absent store keeps the prior
  bounded behavior.

Every value is synthetic. No live dependency is used.
"""

from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.dashboard_analysis import contracts as dc  # noqa: E402
from ha_mcp_engineering.dependency import global_sources as g  # noqa: E402
from ha_mcp_engineering.dependency.models import obligation_fingerprint  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.handoff.provider import EngineeringHandoffProvider  # noqa: E402
from ha_mcp_engineering.handoff.service import HandoffGenerationService  # noqa: E402
from ha_mcp_engineering.incident.provider import DirectHaIncidentProvider  # noqa: E402
from ha_mcp_engineering.incident.service import IncidentCorrelationService  # noqa: E402
from ha_mcp_engineering.integrity import provider as integrity_provider  # noqa: E402
from ha_mcp_engineering.integrity.provider import DirectHaIntegrityProvider  # noqa: E402
from ha_mcp_engineering.integrity.service import ConfigurationIntegrityAnalysisService  # noqa: E402
from ha_mcp_engineering.observability import METRICS  # noqa: E402
from ha_mcp_engineering.reliability.provider import DirectHaReliabilityProvider  # noqa: E402
from tests import test_hamcp270_global_reference_disclosure as t270  # noqa: E402
from tests.hamcp209_fixtures import read_blueprint  # noqa: E402
from tests.test_hamcp227_global_sources import (  # noqa: E402
    ALL_SOURCES,
    BLUEPRINT_READER,
    BudgetReader,
    FakeClock,
    FakeReader,
    Home,
    SECRET,
    build,
    registry_row,
)


MISSING_SCENE = "light.synthetic_missing_scene"
MISSING_GROUP = "light.synthetic_missing_group"
MISSING_DASHBOARD = "light.synthetic_missing_dashboard"
REGISTRY_ONLY = "light.synthetic_registry_only"
ONLY_SCENE = "light.synthetic_only_scene"
ONLY_GROUP = "light.synthetic_only_group"
ONLY_DASHBOARD = "light.synthetic_only_dashboard"
BROKEN_SCENE = {"entity_id": "scene.synthetic_broken", "state": "2026-01-01T00:00:00+00:00",
                "attributes": {"friendly_name": "Synthetic broken", "entity_id": [MISSING_SCENE]}}
# An "on" group: its state must never be read as consumer enablement.
BROKEN_GROUP = {"entity_id": "group.synthetic_broken", "state": "on",
                "attributes": {"friendly_name": "Synthetic broken group", "entity_id": [MISSING_GROUP]}}
DASHBOARD = {"views": [{"cards": [
    {"type": "tile", "entity": ONLY_DASHBOARD},
    {"type": "tile", "entity": MISSING_DASHBOARD},
    {"type": "tile", "entity": REGISTRY_ONLY},
]}]}
CONSUMED = ("scene", "group", "dashboard")
GOVERNANCE_ROW_TYPES = ("scene", "group", "template", "dashboard")


def broken_home(**changes) -> Home:
    return Home(extra_states=[BROKEN_SCENE, BROKEN_GROUP, *changes.pop("extra_states", ())],
                extra_registry=[registry_row(REGISTRY_ONLY), *changes.pop("extra_registry", ())],
                **changes)


def dashboard_reader(**changes) -> FakeReader:
    return FakeReader({"lovelace": DASHBOARD}, **changes)


def rows(data) -> dict:
    return {item["source_type"]: item for item in data["source_coverage_matrix"]}


def governance_bytes(snapshot) -> dict:
    """Every governance-bound value of a snapshot, serialized."""

    return {
        "fingerprint": snapshot.fingerprint,
        "generation": snapshot.generation,
        "findings": json.dumps([asdict(item) for item in snapshot.findings], sort_keys=True, default=str),
        "obligations": json.dumps([asdict(item) for item in snapshot.obligations], sort_keys=True, default=str),
        "obligation_fingerprints": [obligation_fingerprint(item) for item in snapshot.obligations],
        "obligation_overflow_count": snapshot.obligation_overflow_count,
        "obligation_overflow_fingerprint": snapshot.obligation_overflow_fingerprint,
        "obligation_ledger_model": snapshot.obligation_ledger_model,
        "rows": json.dumps([asdict(item) for item in snapshot.coverage
                            if item.source_type in GOVERNANCE_ROW_TYPES], sort_keys=True, default=str),
    }


INCIDENT_VALUES = {
    "related_entity_ids": [], "lookback_hours": 24, "correlation_window_minutes": 30,
    "trace_limit": 10, "include_dependency_context": True, "include_integrity_context": True,
    "include_reliability_context": True, "detail_level": "evidence", "limit": 50, "cursor": "",
    "refresh_index": False,
}
HANDOFF_VALUES = {
    "handoff_type": "focused_review", "title": "", "automation_ids": [], "change_plan_ids": [],
    "lookback_hours": 168, "include_runtime_health": False, "include_governance_context": False,
    "include_dependency_context": True, "include_integrity_context": True,
    "include_reliability_context": False, "include_incident_context": True,
    "include_recommendations": True, "detail_level": "evidence", "limit": 50, "cursor": "",
    "refresh_index": False, "output_format": "structured",
}


class Harness(unittest.IsolatedAsyncioTestCase):
    """The real index, the three real services and a synthetic home."""

    async def asyncSetUp(self):
        METRICS.reset()
        self.blueprints = patch(BLUEPRINT_READER, side_effect=read_blueprint)
        self.blueprints.start()

    async def asyncTearDown(self):
        self.blueprints.stop()
        METRICS.reset()

    async def setup_home(self, home=None, reader=None, *, no_reader=False):
        self.home = home if home is not None else broken_home()
        self.reader = None if no_reader else (reader if reader is not None else dashboard_reader())
        self.index, self.snapshot, self.rest, self.websocket = await build(self.home, self.reader)
        self.services()

    def services(self):
        self.integrity_service = ConfigurationIntegrityAnalysisService(DirectHaIntegrityProvider(
            self.index, self.rest, self.websocket, secret=SECRET, ha_token=SECRET))
        reliability = DirectHaReliabilityProvider(self.rest, self.websocket, secret=SECRET, ha_token=SECRET)
        self.incident_service = IncidentCorrelationService(DirectHaIncidentProvider(
            self.index, self.rest, self.websocket, reliability, secret=SECRET, ha_token=SECRET))
        self.handoff_service = HandoffGenerationService(EngineeringHandoffProvider(
            governance=SimpleNamespace(), incident=self.incident_service, dependency_index=self.index,
            rest_client=self.rest, health=SimpleNamespace(), secret=SECRET, ha_token=SECRET))

    async def integrity(self, **kwargs):
        values = {"source_types": ALL_SOURCES, "detail_level": "evidence", "limit": 100}
        return await self.integrity_service.analyze(**{**values, **kwargs})

    async def incident(self, focus, **kwargs):
        return await self.incident_service.analyze(
            focus_entity_id=focus, automation_id="", **{**INCIDENT_VALUES, **kwargs})

    async def handoff(self, focus_ids, **kwargs):
        return await self.handoff_service.generate(
            focus_entity_ids=list(focus_ids), **{**HANDOFF_VALUES, **kwargs})

    async def all_three(self, focus=ONLY_DASHBOARD):
        return {
            "integrity": await self.integrity(),
            "incident": await self.incident(focus),
            "handoff": await self.handoff([focus]),
        }

    async def set_partition(self, *, reader=None, registry_complete=True, states=None):
        """Replace the partition only; the governance snapshot is untouched."""

        dashboards = await g.collect_dashboard_sources(reader, secret=SECRET)
        self.index.snapshot = replace(self.index.snapshot, global_sources=await g.collect_global_sources(
            self.home.states() if states is None else states, self.home.registry(),
            registry_complete=registry_complete, secret=SECRET, dashboards=dashboards))

    @staticmethod
    def index_fingerprint(name, output):
        provenance = output.data["index_and_cache_provenance"]
        return provenance["index_fingerprint"] if name == "integrity" else provenance["fingerprint"]


class GovernanceInvarianceTests(Harness):
    """Note section 6 item 1: governance and ledger bytes never move."""

    async def test_plain_and_rich_builds_bind_identical_governance_bytes(self):
        await self.setup_home(Home(scenes=False), no_reader=True)
        plain = governance_bytes(self.index.snapshot)
        plain_outputs = await self.all_three()
        await self.setup_home()
        rich = governance_bytes(self.index.snapshot)
        rich_outputs = await self.all_three()
        self.assertEqual(rich, plain)
        for source_type in GOVERNANCE_ROW_TYPES:
            row = next(item for item in self.index.snapshot.coverage if item.source_type == source_type)
            self.assertEqual(row.completeness, "unavailable", source_type)
        self.assertFalse(set(CONSUMED) & {item.source_type for item in self.index.snapshot.findings})
        for name in plain_outputs:
            self.assertEqual(self.index_fingerprint(name, rich_outputs[name]), rich["fingerprint"], name)
            self.assertEqual(self.index_fingerprint(name, plain_outputs[name]), plain["fingerprint"], name)
        # Not vacuous: the rich analyses did consume the partition.
        self.assertIn("dashboard", {item.get("source_type") for item in rich_outputs["integrity"].data["findings"]})
        self.assertNotIn("dashboard", {item.get("source_type") for item in plain_outputs["integrity"].data["findings"]})

    async def test_the_analyses_never_mutate_the_snapshot_or_the_partition(self):
        await self.setup_home()
        snapshot = self.index.snapshot
        before = governance_bytes(snapshot)
        partition = snapshot.global_sources
        partition_before = (partition.fingerprint, partition.findings, partition.coverage,
                            partition.bounded_obligations)
        for focus in (ONLY_SCENE, ONLY_GROUP, ONLY_DASHBOARD, MISSING_DASHBOARD):
            await self.all_three(focus)
        self.assertIs(self.index.snapshot, snapshot)
        self.assertEqual(governance_bytes(self.index.snapshot), before)
        self.assertEqual((partition.fingerprint, partition.findings, partition.coverage,
                          partition.bounded_obligations), partition_before)

    async def test_a_partition_change_moves_no_output_index_fingerprint(self):
        await self.setup_home()
        before = {name: self.index_fingerprint(name, output) for name, output in (await self.all_three()).items()}
        await self.set_partition(reader=None)
        after = {name: self.index_fingerprint(name, output) for name, output in (await self.all_three()).items()}
        self.assertEqual(after, before)
        self.assertEqual(set(before.values()), {self.index.snapshot.fingerprint})


class PlanBindingInvarianceTests(t270.PlanDisclosureTestCase):
    """The HAMCP-270 plan bindings are identical with and without the analyses."""

    async def run_analyses(self, sources, target):
        harness = Harness()
        harness.home = Home()
        harness.reader = None
        with patch(BLUEPRINT_READER, side_effect=read_blueprint):
            harness.index, _, harness.rest, harness.websocket = await build(harness.home)
            harness.index.snapshot = replace(harness.index.snapshot, global_sources=sources)
            harness.services()
            outputs = await harness.all_three(target)
        return outputs

    async def test_plan_bindings_ignore_the_three_analyses(self):
        for planner in self.PLANNERS:
            with self.subTest(planner=planner):
                _, target = await getattr(self, planner)()
                sources = t270.diagnostics(t270.references_for(target))
                self.snapshot.global_sources = None
                absent, _ = await getattr(self, planner)()
                self.snapshot.global_sources = sources
                before, _ = await getattr(self, planner)()
                outputs = await self.run_analyses(sources, target)
                # The analyses saw the partition-only references for the target.
                self.assertEqual(outputs["incident"].data["events_by_type"]["dependency_relationship"], 3)
                handoff = " ".join(item["summary"] for item in outputs["handoff"].data["handoff_items"]
                                   if item["section"] == "confirmed_findings")
                self.assertIn("scene.movie_night", handoff, planner)
                after, _ = await getattr(self, planner)()
                self.assertEqual(self.bindings(after), self.bindings(before))
                self.assertEqual(self.bindings(after), self.bindings(absent))
                self.assertEqual(self.bound_disclosure(after), self.bound_disclosure(before))


class PickupTests(Harness):
    """Each analysis shows scene, group and dashboard references."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.setup_home()

    async def test_integrity_reports_scene_group_and_dashboard_missing_references(self):
        output = await self.integrity()
        found = {(item["source_type"], item["source_id"], item["target_entity_id"]):
                 (item["finding_type"], item["severity"], item["configuration_paths"])
                 for item in output.data["findings"] if item.get("source_type") in CONSUMED}
        self.assertEqual(found[("scene", "scene.synthetic_broken", MISSING_SCENE)],
                         ("missing_entity_reference", "medium", ["$.attributes.entity_id[0]"]))
        self.assertEqual(found[("group", "group.synthetic_broken", MISSING_GROUP)],
                         ("missing_entity_reference", "medium", ["$.attributes.entity_id[0]"]))
        self.assertEqual(found[("dashboard", "lovelace", MISSING_DASHBOARD)],
                         ("missing_entity_reference", "medium", ["$.views[0].cards[1].entity"]))
        self.assertEqual(found[("dashboard", "lovelace", REGISTRY_ONLY)][0], "registry_only_entity_reference")
        matrix = rows(output.data)
        self.assertEqual({key: (matrix[key]["provider"], matrix[key]["provider_capability"],
                                matrix[key]["completeness"]) for key in CONSUMED}, {
            "scene": ("direct_ha_api", "scene_loaded_membership", "complete"),
            "group": ("direct_ha_api", "group_loaded_membership", "complete"),
            "dashboard": ("upstream_dashboard", "dashboard_configuration", "complete"),
        })
        self.assertTrue(any("Dashboards read: 1 of 1 listed" in item for item in matrix["dashboard"]["warnings"]))
        self.assertTrue(any("loaded membership" in item for item in matrix["scene"]["warnings"]))
        self.assertEqual((matrix["template"]["provider"], matrix["template"]["completeness"]),
                         ("none", "not_supported"))
        self.assertTrue(any("HAMCP-264" in item for item in matrix["template"]["warnings"]))
        # Membership evidence keeps its "loaded state attributes" summary.
        summaries = [item["summary"] for item in output.data["evidence_references"]
                     if item.get("source_type") == "group"]
        self.assertTrue(summaries)

    async def test_a_missing_member_of_an_on_group_is_medium_not_high(self):
        output = await self.integrity()
        group = [item for item in output.data["findings"] if item.get("source_type") == "group"]
        self.assertEqual([item["severity"] for item in group], ["medium"])
        self.assertNotIn("source_enabled", group[0])
        self.assertEqual(output.data["findings_by_severity"].get("high", 0), 0)

    async def test_a_partition_reference_clears_an_orphan_candidate(self):
        with_partition = await self.integrity(include_orphan_candidates=True)
        orphans = {item["target_entity_id"] for item in with_partition.data["findings"]
                   if item["finding_type"] == "orphan_registry_candidate"}
        self.assertNotIn(REGISTRY_ONLY, orphans)
        self.index.snapshot = replace(self.index.snapshot, global_sources=None)
        without = await self.integrity(include_orphan_candidates=True)
        orphans = {item["target_entity_id"] for item in without.data["findings"]
                   if item["finding_type"] == "orphan_registry_candidate"}
        self.assertIn(REGISTRY_ONLY, orphans)

    async def test_incident_shows_scoped_scene_group_and_dashboard_relationships(self):
        for focus, expected in (
            (ONLY_SCENE, "scene scene.synthetic_evening lists light.synthetic_only_scene in its loaded membership"),
            (ONLY_GROUP, "group group.synthetic_downstairs lists light.synthetic_only_group in its loaded membership"),
            (ONLY_DASHBOARD, "dashboard lovelace references light.synthetic_only_dashboard at $.views[0].cards[0].entity."),
        ):
            with self.subTest(focus=focus):
                data = (await self.incident(focus)).data
                self.assertEqual(data["events_by_type"]["dependency_relationship"], 1)
                summaries = [item["summary"] for item in data["normalized_events"]
                             if item["event_type"] == "dependency_relationship"]
                self.assertEqual(len(summaries), 1)
                self.assertTrue(summaries[0].startswith(expected), summaries[0])
                matrix = rows(data)
                self.assertEqual(matrix["configuration_integrity"]["completeness"], "complete")
                self.assertFalse([item for item in matrix["dependency_index"]["coverage_limitations"]
                                  if item.startswith("dependency_index_") and item.endswith(
                                      ("_partial", "_unavailable"))])
                self.assertIn("dependency_index_unsupported_source_types",
                              matrix["dependency_index"]["coverage_limitations"])

    async def test_incident_integrity_context_includes_partition_findings(self):
        data = (await self.incident(MISSING_DASHBOARD)).data
        self.assertGreaterEqual(data["events_by_type"]["integrity_finding"], 1)
        self.assertTrue(any(item["source_type"] == "configuration_integrity" and "dashboard" in item["summary"]
                            for item in data["evidence_references"]))

    async def test_handoff_lists_scene_group_and_dashboard_confirmed_findings(self):
        data = (await self.handoff([ONLY_SCENE, ONLY_GROUP, ONLY_DASHBOARD])).data
        confirmed = [(item["title"], item["summary"]) for item in data["handoff_items"]
                     if item["section"] == "confirmed_findings"]
        titles = {title for title, _ in confirmed}
        self.assertIn("Scene/group loaded membership", titles)
        self.assertIn("Dashboard reference", titles)
        text = " ".join(summary for _, summary in confirmed)
        for source in ("scene.synthetic_evening", "group.synthetic_downstairs", "dashboard lovelace"):
            self.assertIn(source, text)
        matrix = rows(data)
        self.assertEqual(matrix["configuration_integrity"]["coverage_limitations"],
                         ["bounded_handoff_integrity_scope"])

    async def test_handoff_scope_excludes_unfocused_partition_references(self):
        data = (await self.handoff([ONLY_SCENE])).data
        text = " ".join(item["summary"] for item in data["handoff_items"] if item["section"] == "confirmed_findings")
        self.assertIn("scene.synthetic_evening", text)
        self.assertNotIn("dashboard lovelace", text)
        self.assertNotIn("group.synthetic_downstairs", text)


class IncompleteSourceTests(Harness):
    """Note section 6 items 2-3: incomplete is never complete or silently absent."""

    async def assert_incomplete(self, source_types, *, integrity, limitation, focus=ONLY_SCENE):
        outputs = await self.all_three(focus)
        integrity_rows = rows(outputs["integrity"].data)
        self.assertTrue(outputs["integrity"].partial)
        self.assertEqual(outputs["integrity"].data["result_status"], "partial")
        for source_type in source_types:
            self.assertEqual(integrity_rows[source_type]["completeness"], integrity, source_type)
            self.assertFalse(integrity_rows[source_type]["assessment_complete"], source_type)
            self.assertTrue(integrity_rows[source_type]["warnings"], source_type)
        incident_rows = rows(outputs["incident"].data)
        handoff_rows = rows(outputs["handoff"].data)
        for source_type in source_types:
            name = f"{source_type}_{limitation}"
            self.assertIn(f"dependency_index_{name}", incident_rows["dependency_index"]["coverage_limitations"])
            self.assertIn(f"configuration_integrity_{name}",
                          incident_rows["configuration_integrity"]["coverage_limitations"])
            self.assertIn(f"dependency_index_{name}", handoff_rows["dependency_index"]["coverage_limitations"])
        for matrix in (incident_rows, handoff_rows):
            self.assertEqual(matrix["dependency_index"]["completeness"], "partial")
            # Partition gaps are limitations, never read failures.
            self.assertEqual(matrix["dependency_index"]["failed_items"], 0)
        self.assertEqual(incident_rows["configuration_integrity"]["completeness"], "partial")
        # Q1 implementation default (pending Josh): the row is not required.
        self.assertFalse(incident_rows["configuration_integrity"]["required_for_assessment"])
        self.assertEqual(outputs["incident"].data["result_status"], "partial")
        self.assertEqual(outputs["handoff"].data["result_status"], "partial")
        return outputs

    async def test_custom_card_is_partial(self):
        reader = FakeReader({"lovelace": {"views": [{"cards": [
            {"type": "custom:synthetic-card", "entity": ONLY_DASHBOARD}]}]}})
        await self.setup_home(reader=reader)
        await self.assert_incomplete(["dashboard"], integrity="partial", limitation="partial")

    async def test_exhausted_dashboard_budget_is_failed_with_its_reason(self):
        reader = BudgetReader({"lovelace": DASHBOARD}, FakeClock(), hang_list=True)
        with patch.object(g, "DASHBOARD_SCAN_SECONDS", 0.0):
            await self.setup_home(reader=reader)
        outputs = await self.assert_incomplete(["dashboard"], integrity="failed", limitation="unavailable")
        self.assertIn("dashboard_scan_budget_exhausted: 1",
                      rows(outputs["integrity"].data)["dashboard"]["warnings"])

    async def test_list_failure_is_failed_and_does_not_escalate(self):
        await self.setup_home(reader=FakeReader({}, list_error=dc.AnalysisError("authority_unavailable")))
        outputs = await self.assert_incomplete(["dashboard"], integrity="failed", limitation="unavailable")
        matrix = rows(outputs["integrity"].data)
        self.assertEqual((matrix["dashboard"]["provider"], matrix["dashboard"]["failed_items"]),
                         ("upstream_dashboard", 1))
        for source_type in ("scene", "group"):
            self.assertEqual(matrix[source_type]["completeness"], "complete")

    async def test_missing_reader_is_failed(self):
        await self.setup_home(no_reader=True)
        outputs = await self.assert_incomplete(["dashboard"], integrity="failed", limitation="unavailable")
        self.assertFalse([item for item in outputs["integrity"].data["findings"]
                          if item.get("source_type") == "dashboard"])

    async def test_withheld_authority_removes_dashboard_evidence_everywhere(self):
        await self.setup_home()
        self.reader.is_current = False
        outputs = await self.assert_incomplete(["dashboard"], integrity="failed", limitation="unavailable",
                                               focus=ONLY_DASHBOARD)
        self.assertFalse([item for item in outputs["integrity"].data["findings"]
                          if item.get("source_type") == "dashboard"])
        self.assertTrue(any("authority changed" in item
                            for item in rows(outputs["integrity"].data)["dashboard"]["warnings"]))
        incident = outputs["incident"].data
        self.assertEqual(incident["events_by_type"]["dependency_relationship"], 0)
        self.assertFalse([item for item in incident["evidence_references"] if "dashboard" in item["summary"]
                          and item["source_type"] == "dependency_index"])
        self.assertFalse([item for item in outputs["handoff"].data["handoff_items"]
                          if item["title"] == "Dashboard reference"])
        self.assertTrue(any("authority changed" in item
                            for item in rows(incident)["dependency_index"]["warnings"]))
        # The cached reader is not re-read to regain the evidence.
        self.assertEqual(self.reader.reads, ["lovelace"])

    async def test_unexposed_scene_members_are_partial(self):
        opaque = {"entity_id": "scene.synthetic_opaque", "state": "2026-01-01T00:00:00+00:00",
                  "attributes": {"friendly_name": "Synthetic opaque"}}
        await self.setup_home(broken_home(extra_states=[opaque]))
        outputs = await self.assert_incomplete(["scene"], integrity="partial", limitation="partial")
        self.assertTrue(any("scene_members_not_exposed: 1" in item
                            for item in rows(outputs["integrity"].data)["scene"]["warnings"]))
        self.assertEqual(rows(outputs["integrity"].data)["group"]["completeness"], "complete")

    async def test_incomplete_registry_marks_scenes_and_groups_partial(self):
        await self.setup_home()
        await self.set_partition(reader=dashboard_reader(), registry_complete=False)
        await self.assert_incomplete(["scene", "group"], integrity="partial", limitation="partial")

    async def test_partition_reference_cap_overflow_is_partial(self):
        await self.setup_home()
        with patch.object(integrity_provider, "MAX_PARTITION_REFERENCES", 1):
            output = await self.integrity()
        matrix = rows(output.data)
        limited = [key for key in CONSUMED
                   if any(item.startswith("integrity_partition_reference_limit:")
                          for item in matrix[key]["warnings"])]
        self.assertTrue(limited)
        for source_type in limited:
            self.assertEqual(matrix[source_type]["completeness"], "partial")
        self.assertTrue(output.partial)
        kept = [item for item in output.data["findings"] if item.get("source_type") in CONSUMED]
        self.assertLessEqual(len(kept), 1)

    async def test_incomplete_sources_qualify_orphan_candidates(self):
        await self.setup_home(no_reader=True)
        output = await self.integrity(include_orphan_candidates=True)
        self.assertTrue(any("incompletely read sources: dashboard" in item for item in output.warnings))
        await self.setup_home()
        output = await self.integrity(include_orphan_candidates=True)
        self.assertFalse(any("incompletely read sources" in item for item in output.warnings))


class SourceFilterTests(Harness):
    async def test_automation_only_scope_excludes_the_partition(self):
        await self.setup_home()
        output = await self.integrity(source_types=["automation"])
        self.assertFalse([item for item in output.data["findings"] if item.get("source_type") in CONSUMED])
        matrix = rows(output.data)
        for source_type in (*CONSUMED, "template"):
            self.assertEqual(matrix[source_type]["completeness"], "not_requested", source_type)
            self.assertEqual(matrix[source_type]["provider"], "none", source_type)

    async def test_a_single_partition_source_scope_keeps_only_that_source(self):
        await self.setup_home()
        output = await self.integrity(source_types=["group"])
        self.assertEqual({item.get("source_type") for item in output.data["findings"]}, {"group"})
        self.assertEqual(rows(output.data)["dashboard"]["completeness"], "not_requested")


class CursorBindingTests(Harness):
    """Note section 6 item 4: every cursor is bound to the visible partition."""

    DOCUMENTS = {f"synthetic-{i}": {"views": [{"cards": [
        {"type": "tile", "entity": f"light.synthetic_missing_{i}"}]}]} for i in range(4)}

    async def fresh(self):
        METRICS.reset()
        self.documents_reader = FakeReader(dict(self.DOCUMENTS))
        await self.setup_home(Home(), self.documents_reader)

    def drivers(self):
        related = [f"light.synthetic_missing_{i}" for i in range(1, 4)]
        return {
            "integrity": (self.integrity_service.provider,
                          lambda cursor: self.integrity(limit=1, cursor=cursor)),
            "incident": (self.incident_service.provider,
                         lambda cursor: self.incident("light.synthetic_missing_0", limit=1, cursor=cursor,
                                                      related_entity_ids=related)),
            "handoff": (self.handoff_service.provider,
                        lambda cursor: self.handoff(["light.synthetic_missing_0", *related], limit=1,
                                                    cursor=cursor)),
        }

    async def first(self, name):
        _, page = self.drivers()[name]
        data = (await page("")).data
        self.assertTrue(data["pagination"]["has_more"], name)
        return data["pagination"]["next_cursor"]

    async def stale(self, name, cursor):
        _, page = self.drivers()[name]
        with self.assertRaises(GovernanceError) as raised:
            await page(cursor)
        self.assertEqual(raised.exception.code, ErrorCode.STALE_CURSOR, name)
        return raised.exception.details["reason"]

    async def test_unchanged_partition_pages_continue(self):
        for name in ("integrity", "incident", "handoff"):
            with self.subTest(name=name):
                await self.fresh()
                _, page = self.drivers()[name]
                cursor, seen, reads = await self.first(name), 1, list(self.documents_reader.reads)
                while cursor:
                    data = (await page(cursor)).data
                    seen += 1
                    cursor = data["pagination"]["next_cursor"]
                self.assertGreater(seen, 1)
                self.assertEqual(self.documents_reader.reads, reads)

    async def test_a_replaced_partition_makes_the_cursor_stale(self):
        for name in ("integrity", "incident", "handoff"):
            with self.subTest(name=name):
                await self.fresh()
                cursor = await self.first(name)
                binding = governance_bytes(self.index.snapshot)
                await self.set_partition(reader=FakeReader({"lovelace": DASHBOARD}))
                self.assertEqual(governance_bytes(self.index.snapshot), binding)
                self.assertEqual(await self.stale(name, cursor), "diagnostic_evidence_changed")

    async def test_retired_dashboard_authority_makes_the_cursor_stale(self):
        for name in ("integrity", "incident", "handoff"):
            with self.subTest(name=name):
                await self.fresh()
                cursor = await self.first(name)
                reads = list(self.documents_reader.reads)
                self.documents_reader.is_current = False
                self.assertEqual(await self.stale(name, cursor), "diagnostic_evidence_changed")
                self.assertEqual(self.documents_reader.reads, reads)

    async def test_a_failing_identity_read_fails_closed(self):
        for name in ("integrity", "incident", "handoff"):
            with self.subTest(name=name):
                await self.fresh()
                cursor = await self.first(name)
                provider, _ = self.drivers()[name]
                with patch.object(type(provider), "active_diagnostic_identity",
                                  side_effect=RuntimeError("synthetic")):
                    self.assertEqual(await self.stale(name, cursor), "diagnostic_evidence_changed")

    async def test_a_partition_change_before_commit_refuses_the_page(self):
        for name in ("integrity", "incident", "handoff"):
            with self.subTest(name=name):
                await self.fresh()
                provider, page = self.drivers()[name]
                with patch.object(type(provider), "active_diagnostic_identity", return_value="changed"):
                    with self.assertRaises(GovernanceError) as raised:
                        await page("")
                self.assertEqual(raised.exception.code, ErrorCode.ANALYSIS_UNAVAILABLE)
                if name != "handoff":
                    self.assertEqual(raised.exception.details["reason"], "index_changed_before_snapshot_commit")

    async def test_an_index_refresh_still_makes_the_cursor_stale(self):
        for name in ("integrity", "incident", "handoff"):
            with self.subTest(name=name):
                await self.fresh()
                cursor = await self.first(name)
                await self.index.get(refresh=True)
                await self.stale(name, cursor)


class NoNewRequestTests(Harness):
    """Note section 6 item 7: the partition costs the analyses no request."""

    async def counts_for(self, home, reader, run):
        await self.setup_home(home, reader, no_reader=reader is None)
        await run()  # warm any per-service lazy state identically
        self.home.rest_counts.clear()
        self.home.ws_counts.clear()
        self.home.batch_calls.clear()
        reads = (self.reader.lists, list(self.reader.reads)) if self.reader is not None else None
        await run()
        if self.reader is not None:
            self.assertEqual((self.reader.lists, self.reader.reads), reads)
        return dict(self.home.rest_counts), dict(self.home.ws_counts), len(self.home.batch_calls)

    async def test_each_analysis_makes_the_same_requests_with_and_without_the_partition(self):
        for name in ("integrity", "incident", "handoff"):
            with self.subTest(name=name):
                async def run():
                    outputs = await self.all_three(ONLY_DASHBOARD)
                    return outputs[name]

                plain = await self.counts_for(Home(scenes=False), None, run)
                rich = await self.counts_for(broken_home(), dashboard_reader(), run)
                self.assertEqual(rich, plain)

    async def test_the_same_index_makes_the_same_requests_with_the_partition_removed(self):
        await self.setup_home()
        await self.all_three(ONLY_DASHBOARD)
        self.home.rest_counts.clear()
        self.home.ws_counts.clear()
        await self.all_three(ONLY_DASHBOARD)
        with_partition = (dict(self.home.rest_counts), dict(self.home.ws_counts))
        self.index.snapshot = replace(self.index.snapshot, global_sources=None)
        self.home.rest_counts.clear()
        self.home.ws_counts.clear()
        await self.all_three(ONLY_DASHBOARD)
        self.assertEqual((dict(self.home.rest_counts), dict(self.home.ws_counts)), with_partition)
        self.assertEqual((self.reader.lists, self.reader.reads), (1, ["lovelace"]))


class ConsumerBoundTests(Harness):
    """Codex F1: a scoped reference left out at a consumer bound is disclosed.

    Every partition row is complete here, so only the incident and handoff
    selection (500) and emission (200) bounds can omit evidence. Omissions are
    named limitations with ``failed_items == 0``; they never read complete and
    never appear below the bound.
    """

    MISSING = "light.synthetic_missing_cap"
    PRESENT = "light.synthetic_present_cap"

    async def setup_groups(self, count, target):
        states = [{"entity_id": f"group.synthetic_cap_{i:04d}", "state": "on",
                   "attributes": {"entity_id": [target]}} for i in range(count)]
        extra_states, extra_registry = [], []
        if target == self.PRESENT:
            extra_states.append({"entity_id": target, "state": "on", "attributes": {}})
            extra_registry.append(registry_row(target))
        await self.setup_home(Home(scenes=False, extra_states=[*extra_states, *states],
                                   extra_registry=extra_registry),
                              FakeReader({"lovelace": {"views": []}}))
        partition = self.index.snapshot.global_sources.visible()
        self.assertEqual(sum(item.target_entity_id == target for item in partition.findings), count)
        for source_type in CONSUMED:
            self.assertEqual(partition.coverage_for(source_type).completeness, "complete", source_type)

    def assert_omission_row(self, row, limitation, omitted):
        self.assertEqual(row["failed_items"], 0)
        self.assertIsNone(row["failure_category"])
        if omitted:
            self.assertEqual(row["completeness"], "partial")
            self.assertFalse(row["assessment_complete"] and row["required_for_assessment"])
            self.assertIn(limitation, row["coverage_limitations"])
            self.assertTrue(any(item.startswith(f"partition_reference_limit: {omitted} ")
                                for item in row["warnings"]), row["warnings"])
        else:
            self.assertNotIn(limitation, row["coverage_limitations"])
            self.assertFalse([item for item in row["warnings"]
                              if item.startswith("partition_reference_limit:")])

    async def check_incident(self, target, count, *, dependency, integrity):
        values = {**INCIDENT_VALUES, "include_dependency_context": dependency,
                  "include_integrity_context": integrity}
        bundle = await self.incident_service.provider.collect({
            **values, "focus_entity_id": target, "automation_id": "",
            "analysis_timestamp": "2026-11-07T21:00:00+00:00"})
        output = await self.incident(target, include_dependency_context=dependency,
                                     include_integrity_context=integrity)
        matrix = rows(output.data)
        relationships = sum(item.event_type == "dependency_relationship" for item in bundle.events)
        self.assertEqual(relationships, min(count, 200) if dependency else 0)
        kept = min(count, 200) if dependency else min(count, 500)
        self.assert_omission_row(matrix["dependency_index"],
                                 "dependency_index_partition_reference_limit_exceeded", count - kept)
        missing = target == self.MISSING
        # Only the selected (at most 500) references are classified.
        findings_omitted = max(0, min(count, 500) - 200) if integrity and missing else 0
        if integrity:
            row = matrix["configuration_integrity"]
            self.assertEqual(len(bundle.integrity_findings), min(count, 200) if missing else 0)
            self.assert_omission_row(row, "configuration_integrity_partition_reference_limit_exceeded",
                                     max(0, count - 500))
            self.assertEqual("configuration_integrity_finding_limit_exceeded" in row["coverage_limitations"],
                             bool(findings_omitted))
            self.assertEqual(any(item.startswith(f"integrity_finding_limit: {findings_omitted} ")
                                 for item in row["warnings"]), bool(findings_omitted))
            self.assertEqual(row["completeness"],
                             "partial" if count > 500 or findings_omitted else "complete")
        truncated = bool((dependency and count > 200) or findings_omitted)
        self.assertEqual(bundle.timeline_truncated, truncated)
        self.assertEqual("Bounded normalized-event retention was reached." in output.warnings, truncated)
        self.assertFalse(bundle.evidence_truncated)
        if count - kept or truncated:
            self.assertEqual(output.data["result_status"], "partial")
        return output

    async def check_handoff(self, target, count, *, incident_context):
        bundle = await self.handoff_service.provider._collect({
            **HANDOFF_VALUES, "focus_entity_ids": [target], "include_incident_context": incident_context})
        output = await self.handoff([target], include_incident_context=incident_context)
        items = sum(item.title == "Scene/group loaded membership" for item in bundle.items)
        self.assertEqual(items, min(count, 200))
        omitted = max(0, count - 200)
        self.assertEqual(bundle.item_truncated, bool(omitted))
        self.assertEqual("The bounded handoff item retention limit was reached." in output.warnings,
                         bool(omitted))
        matrix = rows(output.data)
        self.assert_omission_row(matrix["dependency_index"],
                                 "dependency_index_partition_reference_limit_exceeded", omitted)
        self.assertEqual(matrix["configuration_integrity"]["coverage_limitations"],
                         ["bounded_handoff_integrity_scope"])
        if omitted:
            self.assertEqual(output.data["result_status"], "partial")
            if incident_context:
                self.assertEqual(matrix["incident_correlation"]["completeness"], "partial")
        return output

    async def request_counts(self):
        self.home.rest_counts.clear()
        self.home.ws_counts.clear()
        self.home.batch_calls.clear()
        await self.incident(self.MISSING)
        await self.handoff([self.MISSING])
        return dict(self.home.rest_counts), dict(self.home.ws_counts), len(self.home.batch_calls)

    async def test_emission_bound_199_200_201(self):
        requests = {}
        for count in (199, 200, 201):
            with self.subTest(count=count):
                await self.setup_groups(count, self.MISSING)
                before = governance_bytes(self.index.snapshot)
                await self.incident(self.MISSING)  # warm per-service lazy state
                await self.handoff([self.MISSING])
                requests[count] = await self.request_counts()
                for dependency, integrity in ((True, True), (True, False), (False, True)):
                    with self.subTest(dependency=dependency, integrity=integrity):
                        await self.check_incident(self.MISSING, count, dependency=dependency,
                                                  integrity=integrity)
                for incident_context in (False, True):
                    with self.subTest(incident_context=incident_context):
                        await self.check_handoff(self.MISSING, count, incident_context=incident_context)
                self.assertEqual(governance_bytes(self.index.snapshot), before)
        # The disclosure costs no request: the same requests below and above the bound.
        self.assertEqual(requests[201], requests[200])
        self.assertEqual(requests[200], requests[199])

    async def test_selection_bound_499_500_501(self):
        for target in (self.PRESENT, self.MISSING):
            for count in (499, 500, 501):
                with self.subTest(target=target, count=count):
                    await self.setup_groups(count, target)
                    for dependency, integrity in ((True, True), (False, True)):
                        with self.subTest(dependency=dependency, integrity=integrity):
                            await self.check_incident(target, count, dependency=dependency,
                                                      integrity=integrity)
                    await self.check_handoff(target, count, incident_context=False)

    async def test_disclosure_survives_cursor_continuation(self):
        await self.setup_groups(201, self.MISSING)
        first = await self.handoff([self.MISSING], include_incident_context=False, limit=1)
        cursor, pages = first.data["pagination"]["next_cursor"], 1
        self.assertTrue(cursor)
        while cursor and pages < 3:
            page = await self.handoff([self.MISSING], include_incident_context=False, limit=1, cursor=cursor)
            pages += 1
            self.assertEqual(page.data["result_status"], "partial")
            self.assert_omission_row(rows(page.data)["dependency_index"],
                                     "dependency_index_partition_reference_limit_exceeded", 1)
            cursor = page.data["pagination"]["next_cursor"]
        self.assertGreater(pages, 1)
        first = await self.incident(self.MISSING, limit=1)
        self.assertEqual(first.data["result_status"], "partial")
        cursor = first.data["pagination"]["next_cursor"]
        self.assertTrue(cursor)
        page = await self.incident(self.MISSING, limit=1, cursor=cursor)
        self.assertEqual(page.data["result_status"], "partial")
        matrix = rows(page.data)
        self.assert_omission_row(matrix["dependency_index"],
                                 "dependency_index_partition_reference_limit_exceeded", 1)
        self.assertIn("configuration_integrity_finding_limit_exceeded",
                      matrix["configuration_integrity"]["coverage_limitations"])
        self.assertEqual(matrix["configuration_integrity"]["completeness"], "partial")

class AbsentStoreTests(Harness):
    """An absent partition keeps the prior bounded behavior, with no failure."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.setup_home()
        self.index.snapshot = replace(self.index.snapshot, global_sources=None)

    async def test_absent_store_is_not_supported_and_never_raises(self):
        outputs = await self.all_three(ONLY_DASHBOARD)
        matrix = rows(outputs["integrity"].data)
        for source_type in CONSUMED:
            self.assertEqual((matrix[source_type]["provider"], matrix[source_type]["completeness"]),
                             ("none", "not_supported"), source_type)
        self.assertFalse([item for item in outputs["integrity"].data["findings"]
                          if item.get("source_type") in CONSUMED])
        incident_rows = rows(outputs["incident"].data)
        self.assertEqual(incident_rows["configuration_integrity"]["coverage_limitations"], [])
        self.assertFalse([item for item in incident_rows["dependency_index"]["coverage_limitations"]
                          if item.endswith(("_partial", "_unavailable"))])
        self.assertFalse([item for item in outputs["handoff"].data["handoff_items"]
                          if item["title"] in ("Dashboard reference", "Scene/group loaded membership")])

    async def test_absent_store_identity_is_empty_and_pages_continue(self):
        for provider in (self.integrity_service.provider, self.incident_service.provider,
                         self.handoff_service.provider):
            self.assertEqual(provider.active_diagnostic_identity(), "")
        await self.setup_home(broken_home(extra_registry=[registry_row("light.synthetic_orphan_a")]))
        self.index.snapshot = replace(self.index.snapshot, global_sources=None)
        output = await self.integrity(limit=1, include_orphan_candidates=True)
        self.assertTrue(output.data["pagination"]["has_more"])
        second = await self.integrity(limit=1, include_orphan_candidates=True,
                                      cursor=output.data["pagination"]["next_cursor"])
        self.assertTrue(second.data["findings"])


if __name__ == "__main__":
    unittest.main()
