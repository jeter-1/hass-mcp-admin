"""HAMCP-270 informational scene, group and dashboard references.

The four governed planners (entity availability/exposure, configuration
delete, entity metadata and helper options) disclose the HAMCP-227 global
source findings for their targets as the ``global_source_consumers`` sibling
of the approval-bound consumer evidence.  These tests prove the sibling is
shown with truthful per-source completeness, binds nothing (every plan,
policy, semantic-projection and consumer fingerprint stays byte-identical
when the global findings change), never moves risk, stays bounded, and that
an unavailable store is disclosed rather than refused.
"""

from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.global_sources import (  # noqa: E402
    GlobalSourceDiagnostics,
    dashboard_findings,
)
from ha_mcp_engineering.dependency.models import (  # noqa: E402
    DependencyFinding,
    SourceCoverageItem,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    global_reference_disclosure as grd,
)
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    evaluate_change_policy,
)
from ha_mcp_engineering.models.responses import (  # noqa: E402
    _compact_json,
    dump_json_with_truncation,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)

from tests.hamcp069b_fixtures import automation_profile, finding  # noqa: E402
from tests.hamcp232_helper_options_fixtures import (  # noqa: E402
    HelperOptionsServiceTestCase,
)


LIGHT = "light.porch"
FILTERED = "sensor.filtered_power"
RESPONSE_LIMIT = 60_000
OLD_STATEMENT = "dashboards_scripts_and_templates_not_scanned"


def member(target: str, source_type: str, name: str, index: int = 0) -> DependencyFinding:
    """Loaded membership exactly as ``collect_membership_sources`` shapes it."""

    entity = f"{source_type}.{name}"
    relation = f"{source_type}_member"
    path = f"$.attributes.entity_id[{index}]"
    return DependencyFinding(
        evidence_id=f"{source_type}|{entity}|{target}|{relation}|{path}",
        target_entity_id=target,
        source_type=source_type,
        source_id=entity,
        source_entity_id=entity,
        source_name=name.replace("_", " ").title(),
        relation=relation,
        config_path=path,
        match_type="loaded_membership",
        evidence_summary=(
            f"Entity is a member of this {source_type} in its loaded state "
            "attributes; stored configuration is not read."
        ),
    )


def cards(target: str, url_path: str = "home", *, count: int = 1, conditional: bool = False,
          title: str = "Home") -> list[DependencyFinding]:
    """Dashboard findings built by the shipped HAMCP-227 constructor."""

    return dashboard_findings(
        url_path,
        title,
        [
            {
                "kind": "entity_reference",
                "entity_id": target,
                "rule_id": "card_entity",
                "pointer": f"/views/0/cards/{index}",
                "conditional": conditional,
            }
            for index in range(count)
        ],
        secret="",
    )


def coverage(source_type: str, completeness: str, *warnings: str) -> SourceCoverageItem:
    return SourceCoverageItem(
        source_type=source_type,
        provider=f"synthetic_{source_type}",
        provider_capability="read",
        completeness=completeness,
        warnings=list(warnings),
    )


def diagnostics(
    findings=(),
    *,
    dashboard: str = "partial",
    scene: str = "complete",
    group: str = "complete",
    fingerprint: str = "1" * 64,
    authority_current=None,
) -> GlobalSourceDiagnostics:
    return GlobalSourceDiagnostics(
        tuple(findings),
        (
            coverage("scene", scene),
            coverage("group", group),
            coverage("dashboard", dashboard, "Dashboard provider read is partial (HAMCP-277)."),
            coverage("template", "unavailable", "Template references are deferred (HAMCP-264)."),
        ),
        fingerprint,
        authority_current=authority_current,
    )


def held(global_sources) -> object:
    class Snapshot:
        generation = 7

    snapshot = Snapshot()
    snapshot.global_sources = global_sources
    return snapshot


def references_for(target: str) -> list[DependencyFinding]:
    return [
        member(target, "scene", "movie_night"),
        member(target, "group", "downstairs", 2),
        *cards(target, conditional=True),
    ]


def changed_references_for(target: str) -> list[DependencyFinding]:
    return [
        member(target, "scene", "bedtime", 4),
        member(target, "group", "outdoor"),
        *cards(target, "garden", count=3, title="Garden"),
    ]


def row(disclosure: dict, source_type: str) -> dict:
    value = {"completeness": disclosure["completeness"][source_type]}
    if source_type in disclosure["reasons"]:
        value["reason"] = disclosure["reasons"][source_type]
    return value


class DisclosureModuleTests(unittest.TestCase):
    """The pure builder: completeness, statements, bounds and failure modes."""

    def test_read_disclosure_lists_references_with_per_source_completeness(self):
        disclosure = grd.build(held(diagnostics(references_for(LIGHT))), (LIGHT,))
        self.assertEqual(disclosure["model"], grd.MODEL)
        self.assertEqual(disclosure["status"], "read")
        self.assertIs(disclosure["informational"], True)
        self.assertEqual(disclosure["binding"], "none")
        self.assertIs(disclosure["affects_risk"], False)
        self.assertEqual(
            list(disclosure["completeness"].items()),
            [("scene", "complete"), ("group", "complete"),
             ("dashboard", "partial"), ("template", "unavailable")],
        )
        # Reasons are carried for incomplete sources only.
        self.assertEqual(disclosure["reasons"], {
            "dashboard": "Dashboard provider read is partial (HAMCP-277).",
            "template": "Template references are deferred (HAMCP-264).",
        })
        self.assertEqual(
            [(item["source_type"], item["source_id"], item["location"])
             for item in disclosure["items"]],
            [("scene", "scene.movie_night", "$.attributes.entity_id[0]"),
             ("group", "group.downstairs", "$.attributes.entity_id[2]"),
             ("dashboard", "home", "$.views[0].cards[0]")],
        )
        (dashboard,) = [i for i in disclosure["items"] if i["source_type"] == "dashboard"]
        self.assertIs(dashboard["conditional"], True)
        self.assertEqual(dashboard["source_name"], "Home")
        self.assertEqual(dashboard["relation"], "dashboard_card_entity")
        self.assertEqual(disclosure["counts_by_source"], {"scene": 1, "group": 1, "dashboard": 1})
        self.assertEqual(disclosure["count"], 3)
        self.assertFalse(disclosure["truncated"])
        self.assertEqual(disclosure["provenance"],
                         {"global_sources_fingerprint": "1" * 16, "dependency_index_generation": 7})

    def test_statements_are_informational_membership_and_name_partial_sources(self):
        statements = grd.build(held(diagnostics(references_for(LIGHT))), (LIGHT,))["statements"]
        self.assertEqual(statements[0], grd.INFORMATIONAL_STATEMENT)
        self.assertIn("Information only: these references are not approval-bound", statements[0])
        self.assertIn("never change risk, policy or approval", statements[0])
        self.assertEqual(statements[1], grd.MEMBERSHIP_STATEMENT)
        self.assertIn("loaded membership", statements[1])
        self.assertIn(
            "Scanned: scenes (complete), groups (complete), dashboards (partial); "
            "not scanned: templates.", statements[2],
        )
        self.assertIn("Results may be partial: dashboards partial, templates unavailable.",
                      statements)
        self.assertEqual(statements[-1], grd.BOUND_COVERAGE_STATEMENT)
        self.assertFalse(any(OLD_STATEMENT in item for item in statements))
        self.assertTrue(all(len(item) <= grd.MAX_STATEMENT for item in statements))

    def test_only_the_targets_references_are_disclosed(self):
        findings = [*references_for(LIGHT), *references_for("light.other"),
                    finding(LIGHT, "porch_motion")]
        disclosure = grd.build(held(diagnostics(findings)), (LIGHT,))
        self.assertEqual({item["target_entity_id"] for item in disclosure["items"]}, {LIGHT})
        # Automations stay in the bound consumer evidence, never here.
        self.assertNotIn("automation", {item["source_type"] for item in disclosure["items"]})
        self.assertEqual(disclosure["count"], 3)

    def test_duplicates_collapse_and_order_is_stable(self):
        findings = references_for(LIGHT)
        forward = grd.build(held(diagnostics(findings + findings)), (LIGHT,))
        backward = grd.build(held(diagnostics(list(reversed(findings)))), (LIGHT,))
        self.assertEqual(forward["items"], backward["items"])
        self.assertEqual(forward["count"], 3)

    def test_no_reference_with_incomplete_sources_is_not_proof(self):
        disclosure = grd.build(held(diagnostics()), (LIGHT,))
        self.assertEqual(disclosure["status"], "read")
        self.assertEqual(disclosure["items"], [])
        self.assertIn(
            "No reference found; not proof of none while sources are incomplete.",
            disclosure["statements"],
        )

    def test_dashboards_unavailable_live_are_disclosed_truthfully(self):
        disclosure = grd.build(
            held(diagnostics(references_for(LIGHT), dashboard="unavailable")), (LIGHT,)
        )
        self.assertEqual(row(disclosure, "dashboard")["completeness"], "unavailable")
        self.assertIn("not scanned: dashboards, templates.", disclosure["statements"][2])
        self.assertIn("Results may be partial: dashboards unavailable, templates unavailable.",
                      disclosure["statements"])

    def test_retired_dashboard_authority_withholds_cached_dashboard_references(self):
        disclosure = grd.build(
            held(diagnostics(references_for(LIGHT), authority_current=lambda: False)), (LIGHT,)
        )
        self.assertEqual(disclosure["status"], "read")
        self.assertEqual(disclosure["counts_by_source"]["dashboard"], 0)
        self.assertEqual(row(disclosure, "dashboard")["completeness"], "unavailable")
        self.assertEqual(
            row(disclosure, "dashboard")["reason"],
            "Dashboard provider authority changed; cached dashboard evidence is withheld.",
        )
        self.assertEqual(disclosure["counts_by_source"]["scene"], 1)

    def test_unknown_completeness_is_unavailable_never_complete(self):
        sources = diagnostics(references_for(LIGHT))
        sources = replace(sources, coverage=(
            replace(sources.coverage[0], completeness="assumed_complete"), *sources.coverage[1:],
        ))
        self.assertEqual(row(grd.build(held(sources), (LIGHT,)), "scene")["completeness"],
                         "unavailable")

    def test_complete_sources_carry_no_reason_even_with_a_warning(self):
        sources = diagnostics(references_for(LIGHT))
        sources = replace(sources, coverage=(
            replace(sources.coverage[0], warnings=["Scene registry was read twice."]),
            *sources.coverage[1:],
        ))
        disclosure = grd.build(held(sources), (LIGHT,))
        self.assertEqual(disclosure["completeness"]["scene"], "complete")
        self.assertNotIn("scene", disclosure["reasons"])
        self.assertEqual(set(disclosure["reasons"]), {"dashboard", "template"})

    def test_truncation_keeps_the_count_and_says_so(self):
        findings = [member(LIGHT, "scene", f"scene_{index:02d}") for index in range(10)]
        disclosure = grd.build(held(diagnostics(findings)), (LIGHT,), limit=2)
        self.assertEqual(len(disclosure["items"]), 2)
        self.assertEqual(disclosure["count"], 10)
        self.assertEqual(disclosure["counts_by_source"]["scene"], 10)
        self.assertTrue(disclosure["truncated"])
        self.assertIn("Showing 2 of 10 references.", disclosure["statements"])

    def test_long_values_and_reasons_are_bounded(self):
        long_name = "x" * 500
        findings = [member(LIGHT, "scene", long_name)]
        sources = diagnostics(findings)
        sources = replace(sources, coverage=(
            replace(sources.coverage[0], completeness="partial", warnings=["w" * 900] * 9),
            *sources.coverage[1:],
        ))
        disclosure = grd.build(held(sources), (LIGHT,))
        (item,) = disclosure["items"]
        self.assertLessEqual(len(item["source_id"]), grd.MAX_ID)
        self.assertLessEqual(len(item["source_name"]), grd.MAX_NAME)
        # One bounded reason per incomplete source.
        self.assertEqual(len(row(disclosure, "scene")["reason"]), grd.MAX_REASON)

    def test_per_child_limit_shares_one_plan_budget(self):
        self.assertEqual([grd.per_child_limit(n) for n in (0, 1, 2, 3, 4, 5, 8, 16)],
                         [8, 8, 4, 2, 2, 1, 1, 1])

    def test_unavailable_store_is_disclosed_never_raised(self):
        class Broken:
            def visible(self):
                raise RuntimeError("synthetic unreadable store")

        class Exploding:
            @property
            def global_sources(self):
                raise RuntimeError("synthetic snapshot failure")

        def failing_reader():
            raise RuntimeError("synthetic reader failure")

        cases = {
            "dependency_snapshot_not_held": lambda: None,
            "global_source_diagnostics_absent": lambda: held(None),
            "global_source_diagnostics_unreadable": lambda: held(Broken()),
        }
        for reason, reader in cases.items():
            with self.subTest(reason=reason):
                disclosure = grd.disclose(reader, (LIGHT,))
                self.assertEqual(disclosure["status"], "unavailable")
                self.assertEqual(disclosure["unavailable_reason"], reason)
                self.assertEqual(disclosure["items"], [])
                self.assertNotIn("provenance", disclosure)
                self.assertEqual(set(disclosure["completeness"].values()), {"unavailable"})
                self.assertEqual(disclosure["reasons"], {})
                self.assertEqual(disclosure["statements"][0], grd.INFORMATIONAL_STATEMENT)
                self.assertIn(f"unavailable ({reason}); this is not proof",
                              disclosure["statements"][2])
        self.assertEqual(grd.disclose(failing_reader, (LIGHT,))["unavailable_reason"],
                         "dependency_snapshot_not_held")
        self.assertEqual(grd.disclose(None, (LIGHT,))["unavailable_reason"],
                         "dependency_snapshot_not_held")
        self.assertEqual(grd.disclose(lambda: Exploding(), (LIGHT,))["unavailable_reason"],
                         "global_source_diagnostics_unreadable")

    def test_malformed_findings_never_raise(self):
        class Malformed:
            fingerprint = 17

            def visible(self):
                return self

            @property
            def findings(self):
                raise RuntimeError("synthetic malformed findings")

            def coverage_for(self, source_type):
                raise RuntimeError("synthetic malformed coverage")

        disclosure = grd.disclose(lambda: held(Malformed()), (LIGHT,))
        self.assertEqual(disclosure["status"], "unavailable")
        self.assertEqual(disclosure["unavailable_reason"], "global_source_diagnostics_unreadable")


class PlanDisclosureTestCase(HelperOptionsServiceTestCase):
    """Real service and F3 runtime for all four planners, synthetic HA."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.global_reads = 0
        self.snapshot.global_sources = None

        def held_snapshot():
            self.global_reads += 1
            return self.snapshot

        self.service.global_reference_snapshot_reader = held_snapshot

    def set_global(self, findings=(), **changes) -> None:
        self.snapshot.global_sources = diagnostics(findings, **changes)

    # Planner drivers: each returns (created, target entity id).

    async def create_availability(self, **arguments):
        # ``create`` is the delete fixture's here.
        return await self.service.create_entity_availability_exposure_plan(arguments)

    async def plan_disable(self):
        if LIGHT not in self.ws.entries:
            self.add_entity(LIGHT)
        return await self.create_availability(operation="disable", entity_ids=[LIGHT]), LIGHT

    async def plan_unexpose(self):
        if LIGHT not in self.ws.entries:
            self.add_entity(LIGHT)
        return (
            await self.create_availability(operation="unexpose", entity_ids=[LIGHT], assistant="conversation"),
            LIGHT,
        )

    async def plan_delete(self):
        if "automation.a" not in self.ws.entries:
            self.add("automation", "a")
        return await self.create_one("automation", "a"), "automation.a"

    async def plan_metadata(self):
        if LIGHT not in self.ws.entries:
            self.add_entity(LIGHT)
        return (
            await self.service.create_entity_metadata_plan(
                {"entity_id": LIGHT, "name": "Porch light"}
            ),
            LIGHT,
        )

    async def plan_options(self):
        if not self.options_rest.options:
            self.add_filter()
        return (
            await self.create_options({"entity_id": FILTERED, "set": {"time_constant": 20}}),
            FILTERED,
        )

    PLANNERS = ("plan_disable", "plan_unexpose", "plan_delete", "plan_metadata", "plan_options")

    def siblings(self, created: dict) -> list[tuple[dict, dict]]:
        """(bound container, informational sibling) for every child."""

        found = []
        for operation in created["dry_run_results"]["operations"]:
            results = operation["dry_run_results"]
            containers = [value for value in results.values()
                          if isinstance(value, dict) and grd.KEY in value]
            self.assertEqual(len(containers), 1, sorted(results))
            found.append((containers[0], containers[0][grd.KEY]))
        return found

    def stored(self, created: dict):
        return self.repository.get(created["plan_id"])

    def bindings(self, created: dict, plan_id: str = "normalized") -> dict:
        """Every bound value, with the plan identity normalized across plans."""

        plan = self.stored(created)
        plan = replace(plan, plan_id=plan_id, target=replace(plan.target, target_id=plan_id))
        decision = evaluate_change_policy(plan)
        plan = replace(plan, policy_decision=decision)
        operations = [
            {
                "operation_id": item.operation_id,
                "order": item.order,
                "resource_type": item.resource_type,
                "action": item.action,
                "target_id": item.target_id,
                "proposed_config": item.proposed_config,
                "normalized_proposed_config": item.normalized_proposed_config,
                "current_config": item.current_config,
                "normalized_current_config": item.normalized_current_config,
                "current_state_fingerprint": item.current_state_fingerprint,
                "proposed_config_hash": item.proposed_config_hash,
                "semantic_projection": item.semantic_projection,
                "semantic_projection_hash": item.semantic_projection_hash,
                "called_scripts": item.called_scripts,
                "risk": asdict(item.risk),
            }
            for item in plan.operations
        ]
        return {
            "plan_hash": ChangeGovernanceService.plan_hash(plan),
            "policy_subject_hash": decision.policy_subject_hash,
            "policy_decision_hash": decision.policy_decision_hash,
            "policy_decision": decision.to_dict(),
            "risk": asdict(plan.risk),
            "proposed_config": plan.proposed_config,
            "current_state_fingerprint": plan.current_state_fingerprint,
            "approval_kind": plan.approval.approval_kind,
            "operations": operations,
        }

    def bound_disclosure(self, created: dict) -> list[dict]:
        """The disclosure every bound fingerprint reads, without the sibling."""

        values = []
        for container, _ in self.siblings(created):
            value = {key: item for key, item in container.items()
                     if key not in {grd.KEY, "rollback_disclosure"}}
            values.append(json.dumps(value, sort_keys=True, default=str))
        return values


class FourDisclosureTests(PlanDisclosureTestCase):
    async def test_each_disclosure_shows_the_sibling_with_completeness_and_statements(self):
        for planner in self.PLANNERS:
            with self.subTest(planner=planner):
                created, target = await getattr(self, planner)()
                self.set_global(references_for(target))
                created, target = await getattr(self, planner)()
                ((container, sibling),) = self.siblings(created)
                # A sibling of the approval-bound disclosure (unexpose has no
                # consumer list of its own, so its policy basis is the anchor).
                self.assertIn("policy_basis", container)
                if planner != "plan_unexpose":
                    self.assertTrue({"consumers", "consumer_disclosure"} & set(container), container)
                self.assertEqual(sibling["status"], "read")
                self.assertEqual(sibling["targets"], [target])
                self.assertEqual(sibling["counts_by_source"],
                                 {"scene": 1, "group": 1, "dashboard": 1})
                self.assertEqual(row(sibling, "dashboard")["completeness"], "partial")
                self.assertEqual(row(sibling, "template")["completeness"], "unavailable")
                self.assertEqual(sibling["statements"][0], grd.INFORMATIONAL_STATEMENT)
                self.assertIn(grd.MEMBERSHIP_STATEMENT, sibling["statements"])
                self.assertIn("Results may be partial: dashboards partial, templates unavailable.",
                              sibling["statements"])
                self.assertFalse(any(OLD_STATEMENT in s for s in sibling["statements"]))
                self.assertEqual({item["target_entity_id"] for item in sibling["items"]}, {target})

    async def test_dashboards_unavailable_are_truthful_in_every_disclosure(self):
        self.set_global([], dashboard="unavailable")
        for planner in self.PLANNERS:
            with self.subTest(planner=planner):
                created, target = await getattr(self, planner)()
                self.set_global(
                    [member(target, "scene", "movie_night")], dashboard="unavailable"
                )
                created, _ = await getattr(self, planner)()
                ((_, sibling),) = self.siblings(created)
                self.assertEqual(row(sibling, "dashboard")["completeness"], "unavailable")
                self.assertEqual(sibling["counts_by_source"]["dashboard"], 0)
                self.assertIn("not scanned: dashboards, templates.", sibling["statements"][2])

    async def test_unavailable_store_discloses_unavailable_with_no_refusal(self):
        for reader_name, reader in (
            ("not_held", lambda: None),
            ("raises", lambda: (_ for _ in ()).throw(RuntimeError("synthetic"))),
            ("absent", lambda: self.snapshot),
        ):
            self.snapshot.global_sources = None
            self.service.global_reference_snapshot_reader = reader
            for planner in self.PLANNERS:
                with self.subTest(reader=reader_name, planner=planner):
                    created, _ = await getattr(self, planner)()
                    self.assertIn(created["status"], {"awaiting_approval", "draft"}, created)
                    ((_, sibling),) = self.siblings(created)
                    self.assertEqual(sibling["status"], "unavailable")
                    self.assertIn(sibling["unavailable_reason"], grd.UNAVAILABLE_REASONS)

    async def test_an_unavailable_plan_still_approves_and_applies(self):
        self.service.global_reference_snapshot_reader = lambda: None
        created, _ = await self.plan_disable()
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["status"], "applied", result)

    async def test_the_production_default_reads_the_held_snapshot_only(self):
        # Constructed without an injected reader, the service uses the cached
        # snapshot reader, which never builds or refreshes.
        from ha_mcp_engineering.governance.entity_metadata_disclosure import (
            read_cached_dependency_snapshot,
        )

        service = ChangeGovernanceService(
            self.repository, self.resource_gateway, self.service.audit,
            now=lambda: self.clock[0],
        )
        self.assertIs(service.global_reference_snapshot_reader, read_cached_dependency_snapshot)

    async def test_label_plans_reuse_metadata_projection_without_a_sibling(self):
        from ha_mcp_engineering.governance import entity_metadata_planning as planning

        self.add_entity(LIGHT)
        self.set_global(references_for(LIGHT))
        request = planning.EntityMetadataRequest(LIGHT, {"name": "Porch light"}, 30)
        gateway = self.resource_gateway.entity_metadata
        # The label planners call prepare without a reader: no sibling, so
        # their projections stay byte-identical.
        unwired = await planning.prepare(gateway, self.snapshot_reader, request)
        self.assertNotIn(grd.KEY, planning.projection(unwired))
        wired = await planning.prepare(
            gateway, self.snapshot_reader, request, global_reader=lambda: self.snapshot
        )
        value = planning.projection(wired)
        self.assertEqual(value[grd.KEY]["count"], 3)
        self.assertEqual({k: v for k, v in value.items() if k != grd.KEY},
                         planning.projection(unwired))


class BindingInvarianceTests(PlanDisclosureTestCase):
    async def test_global_findings_never_change_any_binding(self):
        for planner in self.PLANNERS:
            with self.subTest(planner=planner):
                _, target = await getattr(self, planner)()
                variants = {
                    "absent": None,
                    "empty": diagnostics(),
                    "added": diagnostics(references_for(target)),
                    "changed": diagnostics(changed_references_for(target), dashboard="complete",
                                           fingerprint="2" * 64),
                    "withheld": diagnostics(references_for(target),
                                            authority_current=lambda: False),
                    "removed": diagnostics([], dashboard="unavailable", fingerprint="3" * 64),
                }
                observed = {}
                for name, sources in variants.items():
                    self.snapshot.global_sources = sources
                    created, _ = await getattr(self, planner)()
                    stored = self.stored(created)
                    # The recomputation is faithful to what the plan stored.
                    self.assertEqual(ChangeGovernanceService.plan_hash(stored), created["plan_hash"])
                    self.assertEqual(evaluate_change_policy(stored).to_dict(),
                                     stored.policy_decision.to_dict())
                    observed[name] = (
                        self.bindings(created),
                        self.bound_disclosure(created),
                        self.siblings(created)[0][1],
                    )
                baseline_bindings, baseline_disclosure, _ = observed["absent"]
                for name, (bindings, disclosure, sibling) in observed.items():
                    self.assertEqual(bindings, baseline_bindings, name)
                    self.assertEqual(disclosure, baseline_disclosure, name)
                # ...while the sibling itself truthfully changed.
                self.assertEqual(observed["absent"][2]["status"], "unavailable")
                self.assertEqual(observed["added"][2]["count"], 3)
                self.assertEqual(observed["changed"][2]["count"], 5)
                self.assertEqual(observed["withheld"][2]["counts_by_source"]["dashboard"], 0)
                self.assertEqual(observed["removed"][2]["count"], 0)

    async def test_stripping_or_rewriting_the_sibling_leaves_every_hash_identical(self):
        for planner in self.PLANNERS:
            with self.subTest(planner=planner):
                _, target = await getattr(self, planner)()
                self.set_global(references_for(target))
                created, _ = await getattr(self, planner)()
                stored = self.stored(created)
                for rewrite in ("drop", "replace"):
                    altered = replace(stored, operations=[
                        replace(item, dry_run_results=_rewrite(item.dry_run_results, rewrite))
                        for item in stored.operations
                    ], dry_run_results=_rewrite(stored.dry_run_results, rewrite))
                    self.assertEqual(ChangeGovernanceService.plan_hash(altered),
                                     created["plan_hash"], rewrite)
                    self.assertEqual(evaluate_change_policy(altered).to_dict(),
                                     stored.policy_decision.to_dict(), rewrite)

    async def test_an_approved_plan_still_applies_after_global_sources_change(self):
        drivers = {
            "plan_disable": "applied",
            "plan_delete": "applied",
            "plan_metadata": "applied",
            "plan_options": "applied",
        }
        for planner, expected in drivers.items():
            with self.subTest(planner=planner):
                _, target = await getattr(self, planner)()
                self.set_global(references_for(target))
                created, _ = await getattr(self, planner)()
                approved = await self.approve(created)
                self.assertNotEqual(approved.get("status"), "approval_pending")
                # A dashboard, scene and group change after approval.
                self.set_global(changed_references_for(target), dashboard="complete",
                                fingerprint="9" * 64)
                reread = self.service.get_plan(created["plan_id"]) if hasattr(
                    self.service, "get_plan") else None
                if isinstance(reread, dict) and "plan_hash" in reread:
                    self.assertEqual(reread["plan_hash"], created["plan_hash"])
                self.assertEqual(ChangeGovernanceService.plan_hash(self.stored(created)),
                                 created["plan_hash"])
                result = await self.apply(created)
                self.assertEqual(result["status"], expected, result)
                self.set_global([], dashboard="unavailable", fingerprint="8" * 64)


def _rewrite(value, mode: str):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == grd.KEY:
                if mode == "drop":
                    continue
                result[key] = {"status": "read", "items": [{"source_type": "scene"}] * 3}
            else:
                result[key] = _rewrite(item, mode)
        return result
    if isinstance(value, list):
        return [_rewrite(item, mode) for item in value]
    return value


class RiskNeverMovesTests(PlanDisclosureTestCase):
    async def test_policy_and_risk_are_identical_with_and_without_references(self):
        for planner in self.PLANNERS:
            with self.subTest(planner=planner):
                created, target = await getattr(self, planner)()
                without = (created["policy_decision"], created["risk"])
                self.set_global(references_for(target) * 3)
                created, _ = await getattr(self, planner)()
                self.assertEqual((created["policy_decision"]["policy_class"],
                                  created["policy_decision"]["physical_consequence"],
                                  created["policy_decision"]["required_acknowledgements"],
                                  created["risk"]),
                                 (without[0]["policy_class"], without[0]["physical_consequence"],
                                  without[0]["required_acknowledgements"], without[1]))
                self.snapshot.global_sources = None

    async def test_elevated_plans_stay_elevated_when_global_sources_are_complete(self):
        # Helper options are always elevated (HELPER_CHAIN_GAP); a disable
        # whose automation consumer has a physical consequence is elevated.
        self.add_entity(LIGHT)
        self.snapshot.findings = (finding(LIGHT, "porch_motion"),)
        self.snapshot.automation_action_profiles = (
            automation_profile("porch_motion", "safety_critical"),
        )
        baseline = {}
        for planner in ("plan_disable", "plan_options"):
            created, _ = await getattr(self, planner)()
            baseline[planner] = created["policy_decision"]
        for planner in ("plan_disable", "plan_options"):
            with self.subTest(planner=planner):
                _, target = await getattr(self, planner)()
                self.set_global(references_for(target), dashboard="complete")
                created, _ = await getattr(self, planner)()
                for key in ("policy_class", "physical_consequence", "required_acknowledgements"):
                    self.assertEqual(created["policy_decision"][key], baseline[planner][key], key)
                self.assertNotEqual(created["policy_decision"]["policy_class"], "standard_admin")
                self.snapshot.global_sources = None


class SizeBoundTests(PlanDisclosureTestCase):
    def large_findings(self, targets: list[str]) -> list:
        findings = []
        for target in targets:
            findings += [member(target, "scene", f"scene_{n:03d}_{'s' * 90}") for n in range(40)]
            findings += [member(target, "group", f"group_{n:03d}") for n in range(40)]
            findings += cards(target, "d" * 300, count=40, title="T" * 400)
        return findings

    async def plans(self, count: int) -> tuple[dict, dict]:
        targets = [f"light.room_{index}" for index in range(count)]
        for target in targets:
            if target not in self.ws.entries:
                self.add_entity(target)
        self.snapshot.global_sources = None
        baseline = await self.create_availability(operation="disable", entity_ids=targets)
        self.set_global(self.large_findings(targets))
        created = await self.create_availability(operation="disable", entity_ids=targets)
        return baseline, created

    async def test_each_copy_has_a_fixed_upper_size_independent_of_the_store(self):
        for count in (1, 8):
            with self.subTest(children=count):
                _, created = await self.plans(count)
                siblings = self.siblings(created)
                self.assertEqual(len(siblings), count)
                limit = grd.per_child_limit(count)
                for _, sibling in siblings:
                    self.assertEqual(sibling["count"], 120)
                    self.assertEqual(len(sibling["items"]), limit)
                    self.assertTrue(sibling["truncated"])
                    self.assertIn(f"Showing {limit} of 120 references.", sibling["statements"])
                    self.assertLess(len(_compact_json(sibling)), 1_600 + 450 * limit)

    async def test_a_five_child_plan_with_large_reference_sets_fits_the_bound(self):
        _, created = await self.plans(5)
        rendered, truncated = dump_json_with_truncation(created, RESPONSE_LIMIT)
        self.assertFalse(truncated)
        self.assertLessEqual(len(rendered), RESPONSE_LIMIT)
        self.assertEqual(json.loads(rendered), json.loads(json.dumps(created, default=str)))

    async def test_growth_per_child_is_bounded_and_overflow_stays_structurally_flagged(self):
        baseline, created = await self.plans(8)
        growth = len(_compact_json(created)) - len(_compact_json(baseline))
        # Two copies per child (plan and operation dry-run results), one item each.
        self.assertLess(growth, 8 * 2 * 1_000, growth)
        rendered, truncated = dump_json_with_truncation(created, RESPONSE_LIMIT)
        self.assertLessEqual(len(rendered), RESPONSE_LIMIT)
        if truncated:
            # Pre-existing behavior for large plans: the bounded renderer says so.
            self.assertFalse(json.loads(rendered)["response_completeness"]
                             ["approval_disclosures_complete"])


if __name__ == "__main__":
    unittest.main()
