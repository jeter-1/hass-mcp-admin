"""HAMCP-270 informational scene, group and dashboard references.

The four governed planners (entity availability/exposure, configuration
delete, entity metadata and helper options) disclose the HAMCP-227 global
source findings for their targets once per plan response, as
``dry_run_results.global_source_consumers``: the shared coverage context and
one entry per child.  These tests prove the block is shown with truthful
per-source completeness, binds nothing (every plan, policy, semantic-projection
and consumer fingerprint stays byte-identical when the global findings
change), never moves risk, keeps distinct references distinct, fits the tool
response envelope wherever the plan fit before, and that an unavailable store
is disclosed rather than refused.
"""

from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


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
from ha_mcp_engineering.tools import governance as tools  # noqa: E402
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
ZERO_STATEMENT = "A count of 0 is not proof of none while a source is incomplete."


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


def row(block: dict, source_type: str) -> dict:
    value = {"completeness": block["completeness"][source_type]}
    if source_type in block["reasons"]:
        value["reason"] = block["reasons"][source_type]
    return value


def reader_for(findings=(), **changes):
    sources = diagnostics(findings, **changes)
    return lambda: held(sources)


class FieldAssertions:
    """Missing fields and raised errors fail by assertion, never by error."""

    def field(self, mapping, key):
        self.assertIsInstance(mapping, dict)
        self.assertIn(key, mapping)
        return mapping[key]

    def quietly(self, function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as error:  # pragma: no cover - reached only on regression
            self.fail(f"{getattr(function, '__name__', function)} raised {error!r}")

    def disclose(self, reader, targets=(LIGHT,), **kwargs) -> tuple[dict, dict]:
        block = self.quietly(grd.disclose, reader, targets, **kwargs)
        (child,) = self.field(block, "children")
        return block, child


class DisclosureModuleTests(FieldAssertions, unittest.TestCase):
    """The pure builder: completeness, statements, bounds and failure modes."""

    def test_read_disclosure_lists_references_with_per_source_completeness(self):
        block, child = self.disclose(reader_for(references_for(LIGHT)))
        self.assertEqual(self.field(block, "model"), grd.MODEL)
        self.assertEqual(self.field(block, "status"), "read")
        self.assertIs(self.field(block, "informational"), True)
        self.assertEqual(
            list(self.field(block, "completeness").items()),
            [("scene", "complete"), ("group", "complete"),
             ("dashboard", "partial"), ("template", "unavailable")],
        )
        # Reasons are carried for incomplete sources only.
        self.assertEqual(self.field(block, "reasons"), {
            "dashboard": "Dashboard provider read is partial (HAMCP-277).",
            "template": "Template references are deferred (HAMCP-264).",
        })
        self.assertEqual(self.field(block, "provenance"), {"fingerprint": "1" * 16, "generation": 7})
        self.assertEqual(self.field(child, "target"), LIGHT)
        # Membership location and relation are implied; a dashboard shows both.
        self.assertEqual(self.field(child, "items"), [
            {"type": "scene", "id": "scene.movie_night", "name": "Movie Night"},
            {"type": "group", "id": "group.downstairs", "name": "Downstairs"},
            {"type": "dashboard", "id": "home", "name": "Home", "location": "$.views[0].cards[0]",
             "relation": "card_entity", "conditional": True},
        ])
        self.assertEqual(self.field(child, "by_source"), {"scene": 1, "group": 1, "dashboard": 1})
        self.assertEqual(self.field(child, "count"), 3)
        self.assertIs(self.field(child, "truncated"), False)

    def test_statements_are_informational_membership_and_clarify_the_bound_note(self):
        block, _ = self.disclose(reader_for(references_for(LIGHT)))
        statements = self.field(block, "statements")
        self.assertEqual(statements, [
            grd.INFORMATIONAL_STATEMENT, grd.MEMBERSHIP_STATEMENT, ZERO_STATEMENT,
            grd.BOUND_COVERAGE_STATEMENT,
        ])
        self.assertIn("not approval-bound", statements[0])
        self.assertIn("never changes risk, policy or approval", statements[0])
        self.assertIn("loaded membership", statements[1])
        # The bound note is named only to say it describes the risk evidence.
        self.assertEqual([item for item in statements if OLD_STATEMENT in item],
                         [grd.BOUND_COVERAGE_STATEMENT])
        self.assertIn("risk evidence, not these references", statements[-1])
        self.assertTrue(all(len(item) <= grd.MAX_STATEMENT for item in statements))

    def test_complete_sources_make_no_zero_count_caveat(self):
        block, _ = self.disclose(reader_for([], dashboard="complete"))
        sources = diagnostics([], dashboard="complete")
        # Templates stay unavailable (HAMCP-264), so the caveat remains.
        self.assertIn(ZERO_STATEMENT, self.field(block, "statements"))
        complete = replace(sources, coverage=tuple(
            replace(item, completeness="complete", warnings=[]) for item in sources.coverage
        ))
        block, child = self.disclose(lambda: held(complete))
        self.assertNotIn(ZERO_STATEMENT, self.field(block, "statements"))
        self.assertEqual(self.field(block, "reasons"), {})
        self.assertEqual(self.field(child, "count"), 0)
        self.assertEqual(self.field(child, "by_source"), {})

    def test_only_the_targets_references_are_disclosed(self):
        findings = [*references_for(LIGHT), *references_for("light.other"),
                    finding(LIGHT, "porch_motion")]
        _, child = self.disclose(reader_for(findings))
        # Single-target children name the target once, not per item.
        self.assertTrue(all("target" not in item for item in self.field(child, "items")))
        # Automations stay in the bound consumer evidence, never here.
        self.assertNotIn("automation", {item["type"] for item in child["items"]})
        self.assertEqual(self.field(child, "count"), 3)

    def test_a_multi_target_child_names_each_items_target(self):
        context = self.quietly(grd.read, reader_for([*references_for(LIGHT),
                                                     *references_for("light.other")]))
        child = self.quietly(grd.child, context, (LIGHT, "light.other", LIGHT))
        self.assertEqual(self.field(child, "targets"), [LIGHT, "light.other"])
        self.assertNotIn("target", child)
        self.assertEqual(self.field(child, "count"), 6)
        self.assertEqual({item["target"] for item in child["items"]}, {LIGHT, "light.other"})

    def test_duplicates_collapse_and_order_is_stable(self):
        findings = references_for(LIGHT)
        _, forward = self.disclose(reader_for(findings + findings))
        _, backward = self.disclose(reader_for(list(reversed(findings))))
        self.assertEqual(self.field(forward, "items"), self.field(backward, "items"))
        self.assertEqual(self.field(forward, "count"), 3)

    def test_no_reference_with_incomplete_sources_is_not_proof(self):
        block, child = self.disclose(reader_for())
        self.assertEqual(self.field(block, "status"), "read")
        self.assertEqual(self.field(child, "items"), [])
        self.assertEqual(self.field(child, "count"), 0)
        self.assertIn(ZERO_STATEMENT, self.field(block, "statements"))

    def test_dashboards_unavailable_live_are_disclosed_truthfully(self):
        block, child = self.disclose(reader_for(references_for(LIGHT), dashboard="unavailable"))
        self.assertEqual(row(block, "dashboard")["completeness"], "unavailable")
        self.assertEqual(row(block, "dashboard")["reason"],
                         "Dashboard provider read is partial (HAMCP-277).")
        self.assertIn(ZERO_STATEMENT, self.field(block, "statements"))
        self.assertEqual(self.field(child, "by_source")["dashboard"], 1)

    def test_retired_dashboard_authority_withholds_cached_dashboard_references(self):
        block, child = self.disclose(
            reader_for(references_for(LIGHT), authority_current=lambda: False)
        )
        self.assertEqual(self.field(block, "status"), "read")
        # Absent from by_source means 0.
        self.assertEqual(self.field(child, "by_source"), {"scene": 1, "group": 1})
        self.assertEqual(row(block, "dashboard")["completeness"], "unavailable")
        self.assertEqual(
            row(block, "dashboard")["reason"],
            "Dashboard provider authority changed; cached dashboard evidence is withheld.",
        )

    def test_unknown_completeness_is_unavailable_never_complete(self):
        sources = diagnostics(references_for(LIGHT))
        sources = replace(sources, coverage=(
            replace(sources.coverage[0], completeness="assumed_complete"), *sources.coverage[1:],
        ))
        block, _ = self.disclose(lambda: held(sources))
        self.assertEqual(row(block, "scene")["completeness"], "unavailable")

    def test_complete_sources_carry_no_reason_even_with_a_warning(self):
        sources = diagnostics(references_for(LIGHT))
        sources = replace(sources, coverage=(
            replace(sources.coverage[0], warnings=["Scene registry was read twice."]),
            *sources.coverage[1:],
        ))
        block, _ = self.disclose(lambda: held(sources))
        self.assertEqual(self.field(block, "completeness")["scene"], "complete")
        self.assertNotIn("scene", self.field(block, "reasons"))
        self.assertEqual(set(block["reasons"]), {"dashboard", "template"})

    def test_truncation_keeps_the_count_and_says_so(self):
        findings = [member(LIGHT, "scene", f"scene_{index:02d}") for index in range(10)]
        block, child = self.disclose(reader_for(findings), limit=2)
        self.assertEqual(len(self.field(child, "items")), 2)
        self.assertEqual(self.field(child, "count"), 10)
        self.assertEqual(self.field(child, "by_source"), {"scene": 10})
        self.assertIs(self.field(child, "truncated"), True)
        self.assertIn("Each child lists at most 2; counts are totals.",
                      self.field(block, "statements"))

    def test_long_values_and_reasons_are_bounded(self):
        findings = [member(LIGHT, "scene", "x" * 500),
                    *cards(LIGHT, "u" * 500, title="t" * 500)]
        sources = diagnostics(findings)
        sources = replace(sources, coverage=(
            replace(sources.coverage[0], completeness="partial", warnings=["w" * 900] * 9),
            *sources.coverage[1:],
        ))
        block, child = self.disclose(lambda: held(sources))
        scene, dashboard = self.field(child, "items")
        for item in (scene, dashboard):
            self.assertLessEqual(len(self.field(item, "id")), grd.MAX_ID)
            self.assertLessEqual(len(self.field(item, "name")), grd.MAX_NAME)
        self.assertLessEqual(len(self.field(dashboard, "location")), grd.MAX_LOCATION)
        self.assertLessEqual(len(self.field(dashboard, "relation")), grd.MAX_RELATION)
        # One bounded reason per incomplete source.
        self.assertEqual(len(row(block, "scene")["reason"]), grd.MAX_REASON)

    def test_per_child_limit_shares_one_plan_budget(self):
        self.assertEqual([grd.per_child_limit(n) for n in (0, 1, 2, 3, 4, 5, 8, 16)],
                         [8, 8, 4, 2, 2, 1, 1, 1])
        # All 8 slots are used when every child has more than its share.
        for children in (1, 2, 4, 8):
            with self.subTest(children=children):
                targets = [f"light.slot_{index}" for index in range(children)]
                context = self.quietly(grd.read, reader_for([
                    member(target, "scene", f"scene_{n:02d}") for target in targets for n in range(9)
                ]))
                limit = grd.per_child_limit(children)
                shown = [self.quietly(grd.child, context, (target,), limit=limit) for target in targets]
                self.assertEqual(sum(len(self.field(item, "items")) for item in shown), 8)
                self.assertTrue(all(item["count"] == 9 and item["truncated"] for item in shown))

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
                block, child = self.disclose(reader)
                self.assertEqual(self.field(block, "status"), "unavailable")
                self.assertEqual(self.field(block, "unavailable_reason"), reason)
                # Unknown, not zero: no count or items are claimed.
                self.assertEqual(child, {"target": LIGHT})
                self.assertNotIn("provenance", block)
                self.assertEqual(set(self.field(block, "completeness").values()), {"unavailable"})
                self.assertEqual(self.field(block, "reasons"), {})
                statements = self.field(block, "statements")
                self.assertEqual(statements[0], grd.INFORMATIONAL_STATEMENT)
                self.assertIn(f"References unavailable ({reason}): not proof", statements[2])
                self.assertEqual(statements[-1], grd.BOUND_COVERAGE_STATEMENT)
        for reader, reason in (
            (failing_reader, "dependency_snapshot_not_held"),
            (None, "dependency_snapshot_not_held"),
            (lambda: Exploding(), "global_source_diagnostics_unreadable"),
        ):
            block, _ = self.disclose(reader)
            self.assertEqual(self.field(block, "unavailable_reason"), reason)

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

        block, _ = self.disclose(lambda: held(Malformed()))
        self.assertEqual(self.field(block, "status"), "unavailable")
        self.assertEqual(self.field(block, "unavailable_reason"),
                         "global_source_diagnostics_unreadable")

    def test_malformed_finding_fields_never_raise(self):
        class Odd:
            source_type = "scene"
            target_entity_id = LIGHT
            source_id = 17
            source_name = None
            relation = None
            config_path = ["not", "a", "string"]
            evidence_summary = None

        class Visible:
            findings = (Odd(), *references_for(LIGHT))
            fingerprint = None

            def coverage_for(self, source_type):
                return coverage(source_type, "complete")

        class Store:
            def visible(self):
                return Visible()

        block, child = self.disclose(lambda: held(Store()))
        self.assertEqual(self.field(block, "status"), "read")
        self.assertEqual(self.field(child, "count"), 4)
        self.assertIn({"type": "scene"}, self.field(child, "items"))
        self.assertEqual(self.field(block, "provenance"), {"fingerprint": None, "generation": 7})


class DistinctIdentityTests(FieldAssertions, unittest.TestCase):
    """R1-L1: dedupe and order on the full original identity, before display."""

    def test_long_distinct_scene_and_group_ids_at_one_position_stay_separate(self):
        for source_type in ("scene", "group"):
            with self.subTest(source_type=source_type):
                prefix = "p" * 200
                findings = [member(LIGHT, source_type, f"{prefix}_alpha"),
                            member(LIGHT, source_type, f"{prefix}_beta")]
                _, child = self.disclose(reader_for(findings))
                self.assertEqual(self.field(child, "count"), 2)
                self.assertEqual(self.field(child, "by_source"), {source_type: 2})
                self.assertIs(self.field(child, "truncated"), False)
                first, second = self.field(child, "items")
                self.assertNotEqual(first, second)
                # Displays collide after truncation, so the second carries a
                # short unbound suffix and stays bounded.
                self.assertEqual(second["id"], first["id"] + "~2")
                self.assertLessEqual(len(second["id"]), grd.MAX_ID + 3)

    def test_long_distinct_dashboard_ids_and_locations_stay_separate(self):
        long_path = "views" + "/x" * 120
        findings = [
            *cards(LIGHT, "u" * 200 + "a"),
            *cards(LIGHT, "u" * 200 + "b"),
            *dashboard_findings("home", "Home", [
                {"kind": "entity_reference", "entity_id": LIGHT, "rule_id": "card_entity",
                 "pointer": f"/{long_path}/{suffix}", "conditional": False}
                for suffix in ("left", "right")
            ], secret=""),
        ]
        _, child = self.disclose(reader_for(findings))
        self.assertEqual(self.field(child, "count"), 4)
        self.assertEqual(self.field(child, "by_source"), {"dashboard": 4})
        items = self.field(child, "items")
        self.assertEqual(len(items), 4)
        self.assertEqual(len({json.dumps(item, sort_keys=True) for item in items}), 4)
        for item in items:
            self.assertLessEqual(len(item["id"]), grd.MAX_ID + 3)
            self.assertLessEqual(len(item["location"]), grd.MAX_LOCATION)

    def test_distinct_identities_count_even_when_not_shown(self):
        findings = [member(LIGHT, "scene", "p" * 200 + f"_{index}") for index in range(5)]
        _, child = self.disclose(reader_for(findings), limit=2)
        self.assertEqual(self.field(child, "count"), 5)
        self.assertEqual(len(self.field(child, "items")), 2)
        self.assertIs(self.field(child, "truncated"), True)

    def test_true_duplicates_still_collapse(self):
        findings = [member(LIGHT, "scene", "p" * 200), member(LIGHT, "scene", "p" * 200),
                    *cards(LIGHT, "u" * 200), *cards(LIGHT, "u" * 200)]
        _, child = self.disclose(reader_for(findings))
        self.assertEqual(self.field(child, "count"), 2)
        self.assertEqual(len(self.field(child, "items")), 2)
        self.assertFalse(any("~" in item["id"] for item in child["items"]))

    def test_order_and_bounds_are_deterministic(self):
        findings = [
            *[member(LIGHT, "group", "g" * 100 + f"_{index}", index) for index in range(3)],
            *[member(LIGHT, "scene", "s" * 100 + f"_{index}") for index in range(3)],
            *cards(LIGHT, "u" * 100, count=3),
        ]
        outputs = set()
        for ordering in (findings, list(reversed(findings)), findings[3:] + findings[:3]):
            block = self.quietly(grd.disclose, reader_for(ordering), (LIGHT,))
            outputs.add(json.dumps(block, sort_keys=True))
        self.assertEqual(len(outputs), 1)
        (child,) = json.loads(outputs.pop())["children"]
        self.assertEqual([item["type"] for item in child["items"]],
                         ["scene"] * 3 + ["group"] * 3 + ["dashboard"] * 2)
        self.assertEqual(child["count"], 9)


class PlanDisclosureTestCase(FieldAssertions, HelperOptionsServiceTestCase):
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

    def block(self, created: dict) -> dict:
        """The one plan-level block; no operation carries a copy."""

        results = self.field(created, "dry_run_results")
        for operation in self.field(results, "operations"):
            self.assertNotIn(grd.KEY, self.field(operation, "dry_run_results"))
        for operation in self.field(created, "operations"):
            self.assertNotIn(grd.KEY, operation.get("dry_run_results") or {})
        self.assertEqual(json.dumps(created, default=str).count(f'"{grd.KEY}"'), 1)
        return self.field(results, grd.KEY)

    def children(self, created: dict) -> list[dict]:
        return self.field(self.block(created), "children")

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

    def bound_disclosure(self, created: dict) -> str:
        """Every dry-run result the bound fingerprints read, without the block."""

        return json.dumps(
            _strip(_rewrite(created["dry_run_results"], "drop")), sort_keys=True, default=str
        )


class FourDisclosureTests(PlanDisclosureTestCase):
    async def test_each_disclosure_shows_the_block_with_completeness_and_statements(self):
        for planner in self.PLANNERS:
            with self.subTest(planner=planner):
                created, target = await getattr(self, planner)()
                self.set_global(references_for(target))
                created, target = await getattr(self, planner)()
                block = self.block(created)
                self.assertEqual(self.field(block, "status"), "read")
                (child,) = self.field(block, "children")
                self.assertEqual(self.field(child, "target"), target)
                self.assertEqual(self.field(child, "by_source"),
                                 {"scene": 1, "group": 1, "dashboard": 1})
                self.assertEqual(row(block, "dashboard")["completeness"], "partial")
                self.assertEqual(row(block, "template")["completeness"], "unavailable")
                self.assertEqual(self.field(block, "statements"), [
                    grd.INFORMATIONAL_STATEMENT, grd.MEMBERSHIP_STATEMENT, ZERO_STATEMENT,
                    grd.BOUND_COVERAGE_STATEMENT,
                ])
                self.assertEqual(len(self.field(child, "items")), 3)

    async def test_multi_child_plans_list_each_child_in_operation_order(self):
        targets = [f"light.room_{index}" for index in range(3)]
        for target in targets:
            self.add_entity(target)
        self.set_global([finding for target in targets for finding in references_for(target)])
        created = await self.create_availability(operation="disable", entity_ids=targets)
        children = self.children(created)
        self.assertEqual([self.field(child, "target") for child in children], targets)
        limit = grd.per_child_limit(3)
        self.assertTrue(all(child["count"] == 3 and len(child["items"]) == limit
                            and child["truncated"] for child in children))
        self.assertIn(f"Each child lists at most {limit}; counts are totals.",
                      self.field(self.block(created), "statements"))

    async def test_dashboards_unavailable_are_truthful_in_every_disclosure(self):
        self.set_global([], dashboard="unavailable")
        for planner in self.PLANNERS:
            with self.subTest(planner=planner):
                created, target = await getattr(self, planner)()
                self.set_global(
                    [member(target, "scene", "movie_night")], dashboard="unavailable"
                )
                created, _ = await getattr(self, planner)()
                block = self.block(created)
                (child,) = self.field(block, "children")
                self.assertEqual(row(block, "dashboard")["completeness"], "unavailable")
                self.assertEqual(self.field(child, "by_source"), {"scene": 1})
                self.assertIn(ZERO_STATEMENT, self.field(block, "statements"))

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
                    created, target = await getattr(self, planner)()
                    self.assertIn(created["status"], {"awaiting_approval", "draft"}, created)
                    block = self.block(created)
                    self.assertEqual(self.field(block, "status"), "unavailable")
                    self.assertIn(self.field(block, "unavailable_reason"), grd.UNAVAILABLE_REASONS)
                    self.assertEqual(self.field(block, "children"), [{"target": target}])

    async def test_an_unavailable_plan_still_approves_and_applies(self):
        self.service.global_reference_snapshot_reader = lambda: None
        created, _ = await self.plan_disable()
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["status"], "applied", result)

    async def test_public_plan_reads_do_not_carry_the_block(self):
        self.set_global(references_for(LIGHT))
        created, _ = await self.plan_disable()
        self.block(created)
        public = self.service.get_plan_observability(created["plan_id"])
        listed = self.service.list_plans()
        self.assertNotIn(grd.KEY, json.dumps([public, listed], default=str))

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

    async def test_label_plans_reuse_metadata_projection_without_a_block(self):
        from ha_mcp_engineering.governance import entity_metadata_planning as planning

        self.add_entity(LIGHT)
        self.set_global(references_for(LIGHT))
        request = planning.EntityMetadataRequest(LIGHT, {"name": "Porch light"}, 30)
        gateway = self.resource_gateway.entity_metadata
        # The label planners call prepare without a reader: no block, and the
        # projection never carries one either way.
        unwired = await planning.prepare(gateway, self.snapshot_reader, request)
        self.assertEqual(unwired.global_references, {})
        wired = await planning.prepare(
            gateway, self.snapshot_reader, request, global_reader=lambda: self.snapshot
        )
        (child,) = self.field(wired.global_references, "children")
        self.assertEqual(self.field(child, "count"), 3)
        self.assertNotIn(grd.KEY, json.dumps(planning.projection(wired), default=str))
        self.assertEqual(planning.projection(wired), planning.projection(unwired))


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
                    block = self.block(created)
                    observed[name] = (
                        self.bindings(created),
                        self.bound_disclosure(created),
                        block,
                        self.field(block, "children")[0],
                    )
                baseline_bindings, baseline_disclosure, _, _ = observed["absent"]
                for name, (bindings, disclosure, _, _) in observed.items():
                    self.assertEqual(bindings, baseline_bindings, name)
                    self.assertEqual(disclosure, baseline_disclosure, name)
                # ...while the block itself truthfully changed.
                self.assertEqual(observed["absent"][2]["status"], "unavailable")
                self.assertNotIn("count", observed["absent"][3])
                self.assertEqual(observed["added"][3]["count"], 3)
                self.assertEqual(observed["changed"][3]["count"], 5)
                self.assertNotIn("dashboard", observed["withheld"][3]["by_source"])
                self.assertEqual(observed["removed"][3]["count"], 0)

    async def test_stripping_or_rewriting_the_block_leaves_every_hash_identical(self):
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
                result[key] = {"status": "read", "children": [{"count": 3, "items": [{"type": "scene"}] * 3}]}
            else:
                result[key] = _rewrite(item, mode)
        return result
    if isinstance(value, list):
        return [_rewrite(item, mode) for item in value]
    return value


def _strip(value):
    """Drop the plan-identity-bearing rollback disclosure, as before HAMCP-270."""

    if isinstance(value, dict):
        return {key: _strip(item) for key, item in value.items() if key != "rollback_disclosure"}
    if isinstance(value, list):
        return [_strip(item) for item in value]
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


class EnvelopeSizeTests(PlanDisclosureTestCase):
    """R1-M1: the actual tool response envelope, 5-8 children x store states.

    At the base (d09fc8a6) 5, 6 and 7 child disable plans fit the 60,000
    character envelope in every store state and 8 did not (flagged).  They
    must still fit, with headroom, and 8 keeps its truthful flagged shape.
    """

    MIN_HEADROOM = 750
    STORES = ("absent", "empty", "large")

    @staticmethod
    def large(targets: list[str]) -> list:
        findings = []
        for target in targets:
            findings += [member(target, "scene", f"scene_{n:03d}_{'s' * 90}") for n in range(40)]
            findings += [member(target, "group", f"group_{n:03d}") for n in range(40)]
            findings += cards(target, "d" * 300, count=40, title="T" * 400)
        return findings

    async def envelope(self, count: int, store: str) -> tuple[str, list[str]]:
        targets = [f"light.room_{index}" for index in range(count)]
        for target in targets:
            if target not in self.ws.entries:
                self.add_entity(target)
        self.snapshot.global_sources = {
            "absent": lambda: None,
            "empty": lambda: diagnostics(),
            "large": lambda: diagnostics(self.large(targets)),
        }[store]()
        with patch.object(tools.GOVERNANCE, "service", self.service):
            rendered = await tools.create_entity_availability_exposure_plan(
                operation="disable", entity_ids=targets,
            )
        self.assertIsInstance(rendered, str)
        return rendered, targets

    async def test_up_to_seven_children_fit_the_envelope_with_headroom(self):
        for count in (5, 6, 7):
            for store in self.STORES:
                with self.subTest(children=count, store=store):
                    rendered, targets = await self.envelope(count, store)
                    envelope = json.loads(rendered)
                    self.assertTrue(self.field(envelope, "success"))
                    self.assertNotIn("response_completeness", envelope)
                    self.assertLessEqual(len(rendered), RESPONSE_LIMIT - self.MIN_HEADROOM)
                    plan = self.field(envelope, "data")
                    block = self.block(plan)
                    children = self.field(block, "children")
                    self.assertEqual([child["target"] for child in children], targets)
                    if store == "absent":
                        self.assertEqual(block["status"], "unavailable")
                        continue
                    expected = 120 if store == "large" else 0
                    limit = grd.per_child_limit(count)
                    for child in children:
                        self.assertEqual(self.field(child, "count"), expected)
                        self.assertEqual(len(self.field(child, "items")), min(limit, expected))
                        self.assertIs(self.field(child, "truncated"), store == "large")
                    if store == "large":
                        self.assertEqual(children[0]["by_source"],
                                         {"scene": 40, "group": 40, "dashboard": 40})

    async def test_eight_children_keep_the_truthful_flagged_bounded_shape(self):
        for store in self.STORES:
            with self.subTest(store=store):
                rendered, _ = await self.envelope(8, store)
                self.assertLessEqual(len(rendered), RESPONSE_LIMIT)
                envelope = json.loads(rendered)
                completeness = self.field(envelope, "response_completeness")
                self.assertIs(self.field(completeness, "truncated"), True)
                self.assertIs(self.field(completeness, "approval_disclosures_complete"), False)
                # Whatever survives bounding is still truthful.
                block = (envelope.get("data") or {}).get("dry_run_results", {}).get(grd.KEY)
                if block is not None and store == "large":
                    for child in self.field(block, "children"):
                        self.assertEqual(child["count"], 120)
                        self.assertIs(child["truncated"], True)


if __name__ == "__main__":
    unittest.main()
