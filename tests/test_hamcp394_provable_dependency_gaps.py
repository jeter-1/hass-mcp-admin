"""HAMCP-394: a script-only evidence gap is disclosed and does not elevate.

Release 2.11.0 Lane C GO, 2026-11-27.  A disable, a configuration delete and
a created-object delete each bind only the dependency reasons that can hide an
elevating fact.  The three script gaps (``SCRIPT_ONLY_GAPS``) hide script
findings, which ``_consumer_evidence`` lists as consumers without a reason; an
automation that calls a script is already unresolved in its own action
profile.  So every completion of the missing script evidence classifies the
change the same, and these planners drop those reasons.  The gaps stay in the
disclosure, and the statement says why they do not elevate.  Any other gap,
alone or beside a script gap, keeps the OD-4 elevation and its wording.

The raw evidence is unchanged, so the preflight consumer fingerprint still
refuses a plan whose evidence changed after approval, and a plan bound before
this change (script reasons included) still validates against its own
bindings.  Every provider is synthetic; nothing here reaches a live system.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import replace
import inspect
from pathlib import Path
import re
import unittest
from unittest import mock

from tests.hamcp069b_fixtures import (
    AvailabilityExposureServiceTestCase,
    automation_profile,
    coverage_item,
    exposure_snapshot,
    finding,
    script_diagnostics,
)
from tests.hamcp155_fixtures import ConfigurationDeleteServiceTestCase
from tests.hamcp329_298_fixtures import CreatedObjectServiceTestCase
from tests.test_hamcp069b_entity_availability_exposure_execution import (
    child_disclosures,
    policy,
)

from ha_mcp_engineering.governance import (
    configuration_delete_planning as delete_planning,
    created_object_delete_planning as created_planning,
    entity_availability_exposure_planning as planning,
)
from ha_mcp_engineering.governance.risk import automation_action_consequence_profile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
PREFIX = planning.INCOMPLETE_PREFIX
INCOMPLETE = planning.INCOMPLETE_IMPACT_TEXT
SCRIPT_ONLY = "the script consumer list is incomplete"
PROTECTED_DEPENDENCY = delete_planning.BASIS_PROTECTED_DEPENDENCY
PROTECTED_EFFECT = delete_planning.BASIS_PROTECTED_EFFECT
# The planners that apply the HAMCP-394 filter, by module attribute.
PLANNERS = (planning, delete_planning, created_planning)


def retired_scripts(snapshot) -> None:
    snapshot.script_diagnostics = replace(
        script_diagnostics(), authority_current=lambda: False
    )


# Each way the script evidence can be missing, and the gap it yields.
SCRIPT_CAUSES = {
    "reader_absent": (
        lambda s: setattr(s, "script_diagnostics", None),
        "script_evidence_unavailable",
    ),
    "coverage_unavailable": (
        lambda s: setattr(s, "script_diagnostics", script_diagnostics("unavailable")),
        "script_evidence_unavailable",
    ),
    "authority_retired": (retired_scripts, "script_evidence_unavailable"),
    "read_failures": (
        lambda s: setattr(s, "script_diagnostics", script_diagnostics(failed_item_count=2)),
        "script_read_failures",
    ),
    "inventory_bounded": (
        lambda s: setattr(s, "script_diagnostics", script_diagnostics(warnings=[
            "Inventory was bounded; the exact omitted script count is unknown."])),
        "script_inventory_bounded",
    ),
}


def with_coverage(snapshot, source_type: str, item):
    kept = tuple(c for c in snapshot.coverage if c.source_type != source_type)
    snapshot.coverage = kept + ((item,) if item is not None else ())
    return snapshot


# Gaps a script finding cannot account for, each beside a script gap.
ELEVATING_CAUSES = {
    "automation_read_failures": lambda s: setattr(s, "automation_read_failures", (object(),)),
    "obligation_ledger_truncated": lambda s: setattr(s, "obligation_overflow_count", 1),
    "dynamic_references_truncated": lambda s: setattr(s, "dynamic_reference_overflow_count", 2),
    "automation_inventory_incomplete": lambda s: with_coverage(
        s, "automation", coverage_item("automation", "partial")),
    "home_assistant_version_unavailable": lambda s: setattr(
        s, "home_assistant_version_status", "unavailable"),
}


def script_gap_snapshot(cause: str = "reader_absent", *extra: str):
    snapshot = exposure_snapshot()
    SCRIPT_CAUSES[cause][0](snapshot)
    for name in extra:
        ELEVATING_CAUSES[name](snapshot)
    return snapshot


def identity(reasons):
    return list(reasons)


@contextmanager
def mutant(filter_function):
    """Patch the HAMCP-394 filter in every planner that applies it."""

    with ExitStack() as stack:
        for module in PLANNERS:
            stack.enter_context(
                mock.patch.object(module, "elevating_dependency_reasons", filter_function)
            )
        yield


def drop_every_incomplete(reasons):
    """Mutant: drop every evidence-gap reason, not only the script ones."""

    return [reason for reason in reasons if not reason.startswith(PREFIX)]


def script_only_text(entity_id: str, gaps: list[str]) -> str:
    return (
        f"For {entity_id}, the script consumer list is incomplete ({', '.join(gaps)}); "
        "script consumers never raise the approval level, so these gaps do not "
        "elevate the change."
    )


class PredicateTests(unittest.TestCase):
    def test_the_script_only_set_is_exactly_what_the_script_branch_emits(self):
        source = inspect.getsource(planning.dependency_evidence_gaps)
        head, tail = source.split('scripts = getattr(snapshot, "script_diagnostics"', 1)
        added = re.compile(r'gaps\.add\("([a-z_]+)"\)')
        self.assertEqual(set(added.findall(tail)), planning.SCRIPT_ONLY_GAPS)
        self.assertFalse(set(added.findall(head)) & planning.SCRIPT_ONLY_GAPS)
        for cause, (change, gap) in SCRIPT_CAUSES.items():
            with self.subTest(cause=cause):
                snapshot = exposure_snapshot()
                change(snapshot)
                self.assertEqual(planning.dependency_evidence_gaps(snapshot, "light.porch"), [gap])
        for cause, change in ELEVATING_CAUSES.items():
            with self.subTest(cause=cause):
                snapshot = exposure_snapshot()
                change(snapshot)
                gaps = planning.dependency_evidence_gaps(snapshot, "light.porch")
                self.assertIn(cause, gaps)
                self.assertFalse(set(gaps) & planning.SCRIPT_ONLY_GAPS)

    def test_the_filter_drops_only_script_gap_reasons_and_keeps_order(self):
        reasons = [
            PREFIX + "automation_read_failures",
            PREFIX + "script_evidence_unavailable",
            "feeds_protected_automation:alarm",
            PREFIX + "script_inventory_bounded",
            PREFIX + "script_read_failures",
            "unresolved_automation_consequence:evening",
        ]
        self.assertEqual(
            planning.elevating_dependency_reasons(reasons),
            [
                PREFIX + "automation_read_failures",
                "feeds_protected_automation:alarm",
                "unresolved_automation_consequence:evening",
            ],
        )
        self.assertEqual(planning.elevating_dependency_reasons([]), [])
        # A bare gap name is not a reason; the filter matches the prefixed form.
        self.assertEqual(
            planning.elevating_dependency_reasons(["script_evidence_unavailable"]),
            ["script_evidence_unavailable"],
        )
        self.assertEqual(
            planning.elevating_gaps(["automation_read_failures", "script_read_failures"]),
            ["automation_read_failures"],
        )

    def test_the_statement_follows_the_elevation(self):
        gaps = ["script_evidence_unavailable", "script_read_failures"]
        self.assertEqual(
            planning.incomplete_impact_statement("light.porch", gaps, "protected."),
            script_only_text("light.porch", gaps),
        )
        mixed = ["automation_read_failures", "script_evidence_unavailable"]
        self.assertEqual(
            planning.incomplete_impact_statement("light.porch", mixed, "protected."),
            f"For light.porch, {INCOMPLETE} (automation_read_failures, "
            "script_evidence_unavailable); protected.",
        )

    def test_raw_consumer_evidence_still_carries_the_script_reason(self):
        """The fingerprinted evidence is unchanged; only the planners filter."""

        evidence = planning._consumer_evidence(script_gap_snapshot(), "light.porch")
        self.assertIs(evidence["evidence_complete"], False)
        self.assertEqual(evidence["evidence_gaps"], ["script_evidence_unavailable"])
        self.assertEqual(
            evidence["dependency_reasons"], [PREFIX + "script_evidence_unavailable"]
        )

    def test_an_automation_that_calls_a_script_is_unresolved_in_its_own_profile(self):
        """Why a script gap never hides an automation's consequence."""

        for action in (
            {"action": "script.evening_routine"},
            {"action": "script.turn_on", "target": {"entity_id": "script.evening_routine"}},
        ):
            with self.subTest(action=action["action"]):
                profile = automation_action_consequence_profile({"actions": [action]})
                self.assertEqual(profile["physical_consequence"], "unknown", profile)
                self.assertIn("transitive_action_target_unresolved", profile["reason_codes"])

    def test_only_the_approved_planners_apply_the_filter(self):
        names = re.compile(r"\b(elevating_dependency_reasons|elevating_gaps|incomplete_impact_statement)\b")
        users = {
            path.relative_to(PACKAGE).as_posix()
            for path in PACKAGE.rglob("*.py")
            if names.search(path.read_text(encoding="utf-8"))
        }
        self.assertEqual(
            users,
            {
                "governance/entity_availability_exposure_planning.py",
                "governance/configuration_delete_planning.py",
                "governance/created_object_delete_planning.py",
            },
        )


class DisableTests(AvailabilityExposureServiceTestCase):
    async def disable(self, entity_id: str) -> dict:
        self.add_entity(entity_id)
        return await self.create(operation="disable", entity_ids=[entity_id])

    def assert_script_only_standard(self, created: dict, entity_id: str, gap: str) -> None:
        self.assertEqual(policy(created)[:2], ("standard_admin", "none"))
        disclosure = child_disclosures(created)[0]
        self.assertEqual(disclosure["protection"]["dependency_reasons"], [])
        self.assertIs(disclosure["protection"]["protected"], False)
        self.assertIs(disclosure["consumers"]["evidence_complete"], False)
        self.assertEqual(disclosure["consumers"]["evidence_gaps"], [gap])
        statements = disclosure["consequence_statements"]
        self.assertIn(script_only_text(entity_id, [gap]), statements)
        self.assertFalse(any(INCOMPLETE in text for text in statements), statements)

    async def test_each_script_cause_is_standard_and_disclosed(self):
        for index, (cause, (change, gap)) in enumerate(SCRIPT_CAUSES.items()):
            with self.subTest(cause=cause):
                entity_id = f"light.script_{index}"
                self.snapshot = script_gap_snapshot(cause)
                created = await self.disable(entity_id)
                self.assert_script_only_standard(created, entity_id, gap)

    async def test_any_elevating_gap_beside_a_script_gap_keeps_the_protected_text(self):
        for index, cause in enumerate(ELEVATING_CAUSES):
            with self.subTest(cause=cause):
                entity_id = f"light.mixed_{index}"
                self.snapshot = script_gap_snapshot("reader_absent", cause)
                created = await self.disable(entity_id)
                self.assertEqual(policy(created)[:2], ("elevated_admin", "unknown"))
                disclosure = child_disclosures(created)[0]
                reasons = disclosure["protection"]["dependency_reasons"]
                self.assertIn(PREFIX + cause, reasons)
                self.assertNotIn(PREFIX + "script_evidence_unavailable", reasons)
                self.assertIn("script_evidence_unavailable", disclosure["consumers"]["evidence_gaps"])
                statements = disclosure["consequence_statements"]
                self.assertTrue(any(INCOMPLETE in text for text in statements), statements)
                self.assertFalse(any(SCRIPT_ONLY in text for text in statements), statements)

    async def test_an_unresolved_consumer_automation_is_still_elevated(self):
        self.snapshot = script_gap_snapshot()
        self.snapshot.findings = (finding("light.porch", "evening_routine"),)
        self.snapshot.automation_action_profiles = (
            automation_profile("evening_routine", "unknown"),
        )
        created = await self.disable("light.porch")
        self.assertEqual(policy(created)[:2], ("elevated_admin", "unknown"))
        self.assertEqual(
            child_disclosures(created)[0]["protection"]["dependency_reasons"],
            ["unresolved_automation_consequence:evening_routine"],
        )

    async def test_a_safety_critical_consumer_is_still_safety_critical(self):
        self.snapshot = script_gap_snapshot()
        self.snapshot.findings = (finding("binary_sensor.door", "alarm"),)
        self.snapshot.automation_action_profiles = (automation_profile("alarm", "safety_critical"),)
        created = await self.disable("binary_sensor.door")
        self.assertEqual(policy(created)[:2], ("elevated_admin", "safety_critical"))
        self.assertEqual(
            child_disclosures(created)[0]["protection"]["dependency_reasons"],
            ["feeds_protected_automation:alarm"],
        )

    async def test_an_unchanged_script_gap_plan_applies_once(self):
        self.snapshot = script_gap_snapshot()
        created = await self.disable("light.porch")
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(len(self.ws.writes()), 1)

    async def test_evidence_completed_after_approval_is_refused(self):
        self.snapshot = script_gap_snapshot()
        created = await self.disable("light.porch")
        await self.approve(created)
        self.snapshot = exposure_snapshot()
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertIn("dependency_evidence_changed", self.child_codes(result["task_id"]))
        self.assertEqual(self.ws.writes(), [])

    async def test_a_plan_bound_before_this_change_still_approves_and_applies(self):
        self.snapshot = script_gap_snapshot()
        with mutant(identity):
            created = await self.disable("light.porch")
        self.assertEqual(policy(created)[0], "elevated_admin")
        self.assertEqual(
            child_disclosures(created)[0]["protection"]["dependency_reasons"],
            [PREFIX + "script_evidence_unavailable"],
        )
        self.service.get_plan(created["plan_id"])
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(len(self.ws.writes()), 1)

    async def test_the_mutants_are_caught(self):
        self.snapshot = script_gap_snapshot()
        with mutant(identity):
            unfiltered = await self.disable("light.identity")
        self.assertEqual(policy(unfiltered)[0], "elevated_admin")
        self.snapshot = script_gap_snapshot("reader_absent", "automation_read_failures")
        with mutant(drop_every_incomplete):
            dropped = await self.disable("light.dropped")
        self.assertEqual(policy(dropped)[0], "standard_admin")


class ConfigurationDeleteTests(ConfigurationDeleteServiceTestCase):
    def disclosure(self, created: dict) -> dict:
        (operation,) = created["dry_run_results"]["operations"]
        return operation["dry_run_results"]["configuration_delete"]

    def decision(self, created: dict):
        return self.service._load(created["plan_id"]).policy_decision

    async def test_a_plain_delete_with_a_script_gap_is_standard(self):
        self.snapshot = script_gap_snapshot()
        for resource_type, target_id in (("automation", "hall"), ("script", "evening")):
            with self.subTest(resource_type=resource_type):
                self.add(resource_type, target_id)
                created = await self.create_one(resource_type, target_id)
                self.assertEqual(self.decision(created).policy_class.value, "standard_admin")
                self.assertEqual(created["risk"]["level"], "medium")
                shown = self.disclosure(created)
                self.assertEqual(shown["protection"]["basis"], [])
                self.assertEqual(shown["protection"]["dependency_reasons"], [])
                self.assertEqual(shown["consumers"]["evidence_gaps"], ["script_evidence_unavailable"])
                statements = shown["consequence_statements"]
                self.assertIn(
                    script_only_text(shown["entity_id"], ["script_evidence_unavailable"]),
                    statements,
                )
                self.assertFalse(any(INCOMPLETE in text for text in statements), statements)

    async def test_an_elevating_gap_beside_a_script_gap_keeps_the_protected_text(self):
        self.snapshot = script_gap_snapshot("reader_absent", "automation_read_failures")
        self.add("automation", "hall")
        created = await self.create_one("automation", "hall")
        self.assertEqual(self.decision(created).policy_class.value, "elevated_admin")
        shown = self.disclosure(created)
        self.assertEqual(shown["protection"]["basis"], [PROTECTED_DEPENDENCY])
        self.assertEqual(
            shown["protection"]["dependency_reasons"], [PREFIX + "automation_read_failures"]
        )
        self.assertTrue(
            any(
                INCOMPLETE in text and text.endswith("this is treated as protected.")
                for text in shown["consequence_statements"]
            ),
            shown["consequence_statements"],
        )
        self.assertFalse(any(SCRIPT_ONLY in text for text in shown["consequence_statements"]))

    async def test_a_protected_effect_is_elevated_by_the_effect_alone(self):
        self.snapshot = script_gap_snapshot()
        self.add("automation", "night_lock", {
            "id": "night_lock", "alias": "Night lock", "description": "",
            "triggers": [{"trigger": "time", "at": "23:00:00"}], "conditions": [],
            "actions": [{"action": "lock.lock", "target": {"entity_id": "lock.front_door"}}],
            "mode": "single",
        })
        created = await self.create_one("automation", "night_lock")
        self.assertEqual(self.decision(created).policy_class.value, "elevated_admin")
        shown = self.disclosure(created)
        self.assertEqual(shown["protection"]["basis"], [PROTECTED_EFFECT])
        self.assertEqual(shown["protection"]["dependency_reasons"], [])

    async def test_evidence_completed_after_approval_is_refused(self):
        self.snapshot = script_gap_snapshot()
        self.add("automation", "hall")
        created = await self.approved("automation", "hall")
        self.snapshot = exposure_snapshot()
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertIs(result["provider_dispatch_occurred"], False)
        self.assertIn("dependency_evidence_changed", self.child_codes(result["task_id"]))
        self.assertEqual(self.config_rest.writes(), [])

    async def test_an_unchanged_script_gap_delete_applies(self):
        self.snapshot = script_gap_snapshot()
        self.add("automation", "hall")
        created = await self.approved("automation", "hall")
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(self.config_rest.writes(), [("DELETE", "/config/automation/config/hall")])

    async def test_a_plan_bound_before_this_change_still_applies(self):
        self.snapshot = script_gap_snapshot()
        self.add("automation", "hall")
        with mutant(identity):
            created = await self.create_one("automation", "hall")
        shown = self.disclosure(created)
        self.assertEqual(shown["protection"]["basis"], [PROTECTED_DEPENDENCY])
        self.assertEqual(
            shown["protection"]["dependency_reasons"], [PREFIX + "script_evidence_unavailable"]
        )
        self.service.get_plan(created["plan_id"])
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(self.config_rest.writes(), [("DELETE", "/config/automation/config/hall")])

    async def test_the_mutants_are_caught(self):
        self.snapshot = script_gap_snapshot()
        self.add("automation", "identity")
        with mutant(identity):
            unfiltered = await self.create_one("automation", "identity")
        self.assertEqual(self.decision(unfiltered).policy_class.value, "elevated_admin")
        self.snapshot = script_gap_snapshot("reader_absent", "automation_read_failures")
        self.add("automation", "dropped")
        with mutant(drop_every_incomplete):
            dropped = await self.create_one("automation", "dropped")
        self.assertEqual(self.decision(dropped).policy_class.value, "standard_admin")


class CreatedObjectDeleteTests(CreatedObjectServiceTestCase):
    def disclosure(self, plan: dict) -> dict:
        child = plan["dry_run_results"]["operations"][0]["dry_run_results"]
        return child["configuration_delete"]

    async def test_a_script_only_gap_gives_the_script_only_text(self):
        for kind in ("input_boolean", "scene"):
            with self.subTest(kind=kind):
                self.snapshot = exposure_snapshot()
                created = (
                    await self.created_scene() if kind == "scene"
                    else await self.created_helper(kind)
                )
                self.snapshot = script_gap_snapshot()
                _rollback, plan = await self.rollback_plan(created, approve=False)
                self.assertEqual(self.policy(plan["plan_id"]).policy_class.value, "standard_admin")
                shown = self.disclosure(plan)
                self.assertEqual(shown["protection"]["basis"], [])
                self.assertEqual(shown["protection"]["dependency_reasons"], [])
                self.assertEqual(shown["consumers"]["evidence_gaps"], ["script_evidence_unavailable"])
                statements = shown["consequence_statements"]
                self.assertIn(
                    script_only_text(shown["entity_id"], ["script_evidence_unavailable"]),
                    statements,
                )
                self.assertFalse(any(INCOMPLETE in text for text in statements), statements)
                mark = self.mark()
                await self.approve(plan)
                result = await self.apply(plan)
                self.assertEqual(result["task_state"], "succeeded_verified", result)
                self.assertEqual(len(self.writes_since(mark)), 1)

    async def test_any_elevating_gap_keeps_the_protected_text(self):
        for cause, kind in (
            ("automation_read_failures", "input_boolean"),
            ("obligation_ledger_truncated", "input_number"),
        ):
            with self.subTest(cause=cause):
                self.snapshot = exposure_snapshot()
                created = await self.created_helper(kind)
                self.snapshot = script_gap_snapshot("reader_absent", cause)
                _rollback, plan = await self.rollback_plan(created, approve=False)
                self.assertEqual(self.policy(plan["plan_id"]).policy_class.value, "elevated_admin")
                shown = self.disclosure(plan)
                self.assertEqual(shown["protection"]["basis"], [PROTECTED_DEPENDENCY])
                self.assertEqual(shown["protection"]["dependency_reasons"], [PREFIX + cause])
                statements = shown["consequence_statements"]
                self.assertTrue(
                    any(
                        INCOMPLETE in text and text.endswith("this is treated as protected.")
                        for text in statements
                    ),
                    statements,
                )
                self.assertFalse(any(SCRIPT_ONLY in text for text in statements), statements)

    def test_a_protected_scene_member_keeps_the_effect_basis(self):
        scene = {"id": "synthetic_night", "entities": {"lock.front_door": {"state": "locked"}}}
        self.assertEqual(
            created_planning.expected_basis_created("scene_configuration", scene, []),
            (PROTECTED_EFFECT,),
        )
        self.assertEqual(
            created_planning.expected_basis_created(
                "scene_configuration", scene, [PREFIX + "automation_read_failures"]
            ),
            (PROTECTED_EFFECT, PROTECTED_DEPENDENCY),
        )

    async def test_a_plan_bound_before_this_change_still_validates_and_applies(self):
        created = await self.created_helper("input_boolean")
        self.snapshot = script_gap_snapshot()
        with mutant(identity):
            _rollback, plan = await self.rollback_plan(created, approve=False)
        shown = self.disclosure(plan)
        self.assertEqual(shown["protection"]["basis"], [PROTECTED_DEPENDENCY])
        self.assertEqual(
            shown["protection"]["dependency_reasons"], [PREFIX + "script_evidence_unavailable"]
        )
        self.assertEqual(self.policy(plan["plan_id"]).policy_class.value, "elevated_admin")
        self.service.get_plan(plan["plan_id"])
        mark = self.mark()
        await self.approve(plan)
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(len(self.writes_since(mark)), 1)

    async def test_evidence_completed_after_approval_is_refused(self):
        created = await self.created_helper("input_boolean")
        self.snapshot = script_gap_snapshot()
        _rollback, plan = await self.rollback_plan(created, approve=True)
        self.snapshot = exposure_snapshot()
        mark = self.mark()
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertIn("dependency_evidence_changed", self.codes(plan["plan_id"]))
        self.assertEqual(self.writes_since(mark), [])

    async def test_the_mutants_are_caught(self):
        created = await self.created_helper("input_boolean")
        self.snapshot = script_gap_snapshot()
        with mutant(identity):
            _rollback, plan = await self.rollback_plan(created, approve=False)
        self.assertEqual(self.policy(plan["plan_id"]).policy_class.value, "elevated_admin")
        other = await self.created_helper("input_number")
        self.snapshot = script_gap_snapshot("reader_absent", "automation_read_failures")
        with mutant(drop_every_incomplete):
            _rollback, dropped = await self.rollback_plan(other, approve=False)
        self.assertEqual(self.policy(dropped["plan_id"]).policy_class.value, "standard_admin")


if __name__ == "__main__":
    unittest.main()
