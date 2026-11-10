"""HAMCP-329/298 ownership, eligibility and acknowledgement refusals.

The contract is ``docs/HAMCP_329_298_SCENE_CREATE_AND_CREATE_ROLLBACK_DESIGN.md``
§3.3-§3.5 and the §6 rows on eligibility, replacement, the option-E negatives
and the preflight-stale checks.  Each case covers the created scene and both
created helper domains; the automation and script cases are in
``test_hamcp298_created_object_rollback.py``.  Every refusal proves that no
write was sent and that the created object is still there.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance import (  # noqa: E402
    created_object_rollback as rollback_model,
)

import tests.hamcp155_fixtures as fixtures155  # noqa: E402
from tests.hamcp329_298_fixtures import (  # noqa: E402
    CreatedObjectServiceTestCase,
    EVENING,
    LAMP,
    SCENE_PREFIX,
    automation_config,
    boolean_config,
    helper_operation,
    number_config,
    slug,
)


KINDS = ("scene", "input_boolean", "input_number")
OTHER_DOMAIN = {"input_boolean": "input_number", "input_number": "input_boolean"}


@dataclass
class Subject:
    """One governed, verified, captured create and where its object lives."""

    kind: str
    created: dict
    object_id: str
    operation_id: str
    entity_id: str


class OwnershipTestCase(CreatedObjectServiceTestCase):
    async def subject(self, kind: str, tag: str) -> Subject:
        name = f"Synthetic {kind.replace('_', ' ')} {tag.replace('_', ' ')}"
        object_id = slug(name)
        if kind == "scene":
            created = await self.created_scene(object_id, name=name)
            return Subject(
                kind,
                created,
                object_id,
                "scene_create_1",
                self.config_rest.scene_entity(object_id),
            )
        config = boolean_config(name) if kind == "input_boolean" else number_config(name)
        created = await self.created_helper(kind, config)
        return Subject(
            kind, created, object_id, "create_helper", f"{kind}.{object_id}"
        )

    def entry(self, subject: Subject) -> dict:
        return self.ws.entries[subject.entity_id]

    def body(self, subject: Subject) -> dict:
        if subject.kind == "scene":
            return self.config_rest.scenes[subject.object_id]
        return self.ws.helpers[subject.kind][subject.object_id]

    def present(self, subject: Subject) -> bool:
        if subject.kind == "scene":
            return subject.object_id in self.config_rest.scenes
        return subject.object_id in self.ws.helpers[subject.kind]

    def remove(self, subject: Subject) -> None:
        if subject.kind == "scene":
            self.remove_scene(subject.object_id)
        else:
            self.remove_helper(subject.kind, subject.object_id)

    def rename(self, subject: Subject, suffix: str) -> None:
        subject.entity_id = self.rename_entity(
            subject.entity_id, f"{subject.entity_id}_{suffix}"
        )

    def change(self, subject: Subject, what: str) -> None:
        """One out-of-band change by a user or by Home Assistant."""

        if what == "absent":
            self.remove(subject)
            return
        if what == "renamed":
            self.rename(subject, "again")
            return
        entry = self.entry(subject)
        # Every change below also moves ``modified_at``, as Core does: the
        # option-E basis must never be what is reported for any of them.
        entry["modified_at"] = float(entry["modified_at"]) + 1
        if what == "registry_id":
            entry["id"] = "replacemententry1"
        elif what == "created_at":
            entry["created_at"] = 9.5
        elif what == "body":
            self.body(subject)["name"] = "Changed by the user"
        elif what == "unique_id":
            entry["unique_id"] = "another_object"
        elif what == "other_domain":
            entry["platform"] = OTHER_DOMAIN[subject.kind]
        elif what == "other_platform":
            entry["platform"] = "hue"
        elif what != "modified_at":
            raise AssertionError(what)


PLANNING_REFUSALS = {
    "registry_id": rollback_model.IDENTITY_CHANGED,
    "created_at": rollback_model.IDENTITY_CHANGED,
    "body": rollback_model.CHANGED,
    "unique_id": rollback_model.IDENTITY_CHANGED,
    "absent": rollback_model.ALREADY_ABSENT,
}


class PlanningOwnershipTests(OwnershipTestCase):
    """§3.4: a created object that is no longer provably ours is excluded."""

    async def assert_refused(self, kind: str, what: str, reason: str) -> None:
        subject = await self.subject(kind, what)
        self.change(subject, what)
        was_present = self.present(subject)
        mark = self.mark()
        await self.refused_rollback(subject.created, {subject.operation_id: reason})
        self.assertEqual(self.writes_since(mark), [])
        self.assertEqual(self.present(subject), was_present)

    async def test_scene_changes_are_refused_at_planning(self):
        cases = dict(PLANNING_REFUSALS, other_platform=rollback_model.READ_UNAVAILABLE)
        for what, reason in cases.items():
            with self.subTest(change=what):
                await self.assert_refused("scene", what, reason)

    async def test_input_boolean_changes_are_refused_at_planning(self):
        cases = dict(PLANNING_REFUSALS, other_domain=rollback_model.IDENTITY_CHANGED)
        for what, reason in cases.items():
            with self.subTest(change=what):
                await self.assert_refused("input_boolean", what, reason)

    async def test_input_number_changes_are_refused_at_planning(self):
        cases = dict(PLANNING_REFUSALS, other_domain=rollback_model.IDENTITY_CHANGED)
        for what, reason in cases.items():
            with self.subTest(change=what):
                await self.assert_refused("input_number", what, reason)

    async def test_a_helper_of_the_other_domain_with_the_same_object_id_is_untouched(self):
        # Distinct full ids, one shared object id: the rollback of one never
        # reads the other as its own and never deletes it.
        config = {"input_boolean": boolean_config("Shared"), "input_number": number_config("Shared")}
        created = {
            domain: await self.created_helper(domain, config[domain])
            for domain in ("input_boolean", "input_number")
        }
        mark = self.mark()
        result = await self.apply_rollback(
            await self.service.rollback_change(created["input_boolean"]["plan_id"])
        )
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.writes_since(mark),
            [{"type": "input_boolean/delete", "input_boolean_id": "shared"}],
        )
        self.assertNotIn("shared", self.ws.helpers["input_boolean"])
        self.assertIn("shared", self.ws.helpers["input_number"])
        self.assertIn("input_number.shared", self.ws.entries)

    async def test_an_unchanged_modified_at_carries_no_option_e_basis(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                subject = await self.subject(kind, "unchanged")
                _rollback, plan = await self.rollback_plan(
                    subject.created, approve=False
                )
                (marker,) = self.markers(plan["plan_id"])
                self.assertEqual(marker["ownership"], rollback_model.OWNED)
                self.assertEqual(marker["basis"], "")
                self.assertEqual(
                    marker["captured_modified_at"], marker["observed_modified_at"]
                )
                decision = self.policy(plan["plan_id"])
                self.assertEqual(decision.policy_class.value, "standard_admin")
                self.assertNotIn(
                    rollback_model.BASIS_MODIFIED_SINCE_CREATE, decision.reason_codes
                )
                (operation,) = self.operations(plan["plan_id"])
                self.assertFalse(
                    [
                        reason
                        for reason in operation.risk.reasons
                        if rollback_model.E_DISCLOSURE in reason
                    ]
                )
                child = plan["dry_run_results"]["operations"][0]["dry_run_results"]
                disclosed = child["configuration_delete"]["created_object_rollback"]
                self.assertNotIn("statement", disclosed)
                # One approval step is enough for an unchanged child.
                granted = await self.plan_approval_only(plan)
                self.assertNotEqual(granted["status"], "approval_pending", granted)


PREFLIGHT_REFUSALS = {
    "modified_at": "created_object_identity_changed",
    "renamed": "registry_entry_changed",
    "registry_id": "registry_entry_changed",
    "created_at": "created_object_identity_changed",
    "body": "stale_target_state",
    "absent": "delete_target_missing",
}


class PreflightStaleTests(OwnershipTestCase):
    """§3.5: any change between planning and preflight refuses before intent."""

    async def assert_stale(self, kind: str, what: str, *, option_e: bool) -> None:
        subject = await self.subject(kind, f"{'e ' if option_e else ''}{what}")
        if option_e:
            self.rename(subject, "renamed")
        _rollback, plan = await self.rollback_plan(subject.created, approve=False)
        decision = self.policy(plan["plan_id"])
        self.assertEqual(
            decision.policy_class.value,
            "elevated_admin" if option_e else "standard_admin",
        )
        # The full approval, with the elevated acknowledgement when asked for.
        granted = await self.approve(plan)
        self.assertNotEqual(granted["status"], "approval_pending", granted)
        self.change(subject, what)
        was_present = self.present(subject)
        mark = self.mark()
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertIs(result["provider_dispatch_occurred"], False)
        self.assertEqual(
            self.children(plan["plan_id"]),
            [("delete_1", "terminal", "preflight_rejected", 0)],
        )
        self.assertEqual(self.codes(plan["plan_id"]), {PREFLIGHT_REFUSALS[what]})
        self.assertEqual(self.writes_since(mark), [])
        self.assertEqual(self.present(subject), was_present)

    async def test_scene_changes_after_planning(self):
        for what in PREFLIGHT_REFUSALS:
            with self.subTest(change=what):
                await self.assert_stale("scene", what, option_e=False)

    async def test_input_boolean_changes_after_planning(self):
        for what in PREFLIGHT_REFUSALS:
            with self.subTest(change=what):
                await self.assert_stale("input_boolean", what, option_e=False)

    async def test_input_number_changes_after_planning(self):
        for what in PREFLIGHT_REFUSALS:
            with self.subTest(change=what):
                await self.assert_stale("input_number", what, option_e=False)

    async def test_an_acknowledged_option_e_scene_is_still_refused(self):
        for what in ("registry_id", "created_at", "body", "modified_at", "renamed"):
            with self.subTest(change=what):
                await self.assert_stale("scene", what, option_e=True)

    async def test_an_acknowledged_option_e_input_boolean_is_still_refused(self):
        for what in ("registry_id", "created_at", "body", "modified_at", "renamed"):
            with self.subTest(change=what):
                await self.assert_stale("input_boolean", what, option_e=True)

    async def test_an_acknowledged_option_e_input_number_is_still_refused(self):
        for what in ("registry_id", "created_at", "body", "modified_at", "renamed"):
            with self.subTest(change=what):
                await self.assert_stale("input_number", what, option_e=True)


class AcknowledgementTests(OwnershipTestCase):
    """§3.4.2: an option-E child needs the elevated acknowledgement of its plan."""

    async def e_plan(self, kind: str, tag: str) -> tuple[Subject, dict]:
        subject = await self.subject(kind, tag)
        self.rename(subject, "renamed")
        _rollback, plan = await self.rollback_plan(subject.created, approve=False)
        self.assertEqual(
            self.policy(plan["plan_id"]).policy_class.value, "elevated_admin"
        )
        return subject, plan

    async def assert_not_applied(self, subject: Subject, plan: dict) -> None:
        mark = self.mark()
        with self.assertRaises(GovernanceError) as raised:
            await self.apply(plan)
        self.assertEqual(
            raised.exception.code,
            ErrorCode.APPROVAL_HASH_MISMATCH,
            raised.exception.details,
        )
        self.assertEqual(self.reads_since(mark), [])
        self.assertIsNone(self.service.task_repository.get_for_plan(plan["plan_id"]))
        self.assertTrue(self.present(subject))
        self.assertEqual(
            self.service.get_plan(plan["plan_id"])["status"], "awaiting_approval"
        )

    async def test_plan_approval_alone_dispatches_nothing(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                subject, plan = await self.e_plan(kind, "unacknowledged")
                first = await self.plan_approval_only(plan)
                self.assertEqual(first["status"], "approval_pending", first)
                self.assertEqual(
                    first["approval_action"], "elevated_risk_acknowledgement"
                )
                await self.assert_not_applied(subject, plan)

    async def decide(self, plan: dict, pending: dict, plan_hash: str) -> dict:
        _review, csrf = await self.service.issue_external_csrf(
            plan["plan_id"], pending["challenge_id"]
        )
        return await self.service.decide_external_approval(
            plan_id=plan["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=plan_hash,
            approval_kind=pending["approval_kind"],
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal=fixtures155.REVIEWER,
        )

    async def test_an_acknowledgement_is_bound_to_one_plan_hash(self):
        first_subject, first_plan = await self.e_plan("scene", "first")
        second_subject, second_plan = await self.e_plan("input_number", "second")
        self.assertNotEqual(first_plan["plan_hash"], second_plan["plan_hash"])
        # The first plan is acknowledged in full; that says nothing about the
        # second one.
        await self.approve(first_plan)
        pending = await self.plan_approval_only(second_plan)
        self.assertEqual(pending["status"], "approval_pending", pending)
        await self.assert_not_applied(second_subject, second_plan)
        with self.assertRaises(GovernanceError) as raised:
            await self.decide(second_plan, pending, first_plan["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.APPROVAL_HASH_MISMATCH)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(second_plan["plan_id"], first_plan["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.APPROVAL_HASH_MISMATCH)
        await self.assert_not_applied(second_subject, second_plan)
        # Each plan's own acknowledgement applies exactly its own delete.
        mark = self.mark()
        granted = await self.decide(second_plan, pending, second_plan["plan_hash"])
        self.assertNotEqual(granted["status"], "approval_pending", granted)
        result = await self.apply(second_plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.writes_since(mark),
            [
                {
                    "type": "input_number/delete",
                    "input_number_id": second_subject.object_id,
                }
            ],
        )
        self.assertTrue(self.present(first_subject))
        mark = self.mark()
        result = await self.apply(first_plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            self.writes_since(mark),
            [("DELETE", f"{SCENE_PREFIX}{first_subject.object_id}")],
        )

    async def test_a_refused_stale_plan_is_never_reissued_with_its_acknowledgement(self):
        subject, plan = await self.e_plan("scene", "stale")
        await self.approve(plan)
        self.change(subject, "modified_at")
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        mark = self.mark()
        again = await self.service.rollback_change(subject.created["plan_id"])
        self.assertEqual(again["rollback_plan_id"], plan["plan_id"])
        self.assertEqual(again["plan_hash"], plan["plan_hash"])
        with self.assertRaises(GovernanceError) as raised:
            await self.apply(plan)
        self.assertEqual(raised.exception.code, ErrorCode.DUPLICATE_APPLY_ATTEMPT)
        self.assertEqual(self.writes_since(mark), [])
        self.assertTrue(self.present(subject))


class EligibilityTests(OwnershipTestCase):
    """§3.4.1: only a verified, captured create is ever rolled back."""

    async def assert_forward_not_verified(self, plan: dict) -> None:
        mark = self.mark()
        error = await self.refused(plan["plan_id"])
        self.assertEqual(error.code, ErrorCode.ROLLBACK_NOT_AVAILABLE, error.details)
        self.assertEqual(error.details["reason"], "forward_not_verified", error.details)
        self.assertEqual(error.details["operations_excluded"], [])
        self.assertEqual(self.reads_since(mark), [])
        projected = self.service.get_plan(plan["plan_id"])["rollback"]
        self.assertIs(projected["available"], False)

    async def test_a_create_that_was_never_applied_has_no_rollback(self):
        for label, build in (
            ("scene", self.scene_plan),
            (
                "helper",
                lambda: self.configuration_plan(
                    helper_operation("input_boolean", boolean_config())
                ),
            ),
        ):
            with self.subTest(create=label):
                plan = await build()
                await self.assert_forward_not_verified(plan)
                await self.approve(plan)
                await self.assert_forward_not_verified(plan)

    async def test_a_create_refused_at_preflight_has_no_rollback(self):
        plan = await self.scene_plan()
        await self.approve(plan)
        self.config_rest.add_scene(
            EVENING, {"name": "A user's own", "entities": {LAMP: {"state": "on"}}}
        )
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        await self.assert_forward_not_verified(plan)
        # The user's scene with the same config id is never deleted.
        self.assertIn(EVENING, self.config_rest.scenes)
        self.assertEqual(self.config_rest.scene_deletes(), [])

    async def test_a_scene_stored_but_never_registered_is_not_success_or_eligible(self):
        self.config_rest.scene_post_registers_entity = False
        plan = await self.scene_plan()
        await self.approve(plan)
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "manual_review_required", result)
        self.assertEqual(
            self.children(plan["plan_id"]),
            [("scene_create_1", "terminal", "manual_review_required", 1)],
        )
        self.assertIn(
            "scene_config_stored_registry_unverified", self.codes(plan["plan_id"])
        )
        self.assertNotIn("exact_readback_verified", self.codes(plan["plan_id"]))
        self.assertEqual(len(self.config_rest.scene_posts), 1)
        self.assertEqual(self.identity_values(plan["plan_id"]), [None])
        # The registration arriving later changes nothing: no rollback-time
        # identity is read or adopted.
        self.config_rest.register_scene(EVENING, self.config_rest.scenes[EVENING])
        mark = self.mark()
        await self.refused_rollback(
            plan, {"scene_create_1": rollback_model.NOT_VERIFIED}
        )
        self.assertEqual(self.reads_since(mark), [])
        self.assertIn(EVENING, self.config_rest.scenes)
        projected = self.service.get_plan(plan["plan_id"])["rollback"]
        self.assertIs(projected["available"], False)

    async def test_a_helper_created_under_another_id_is_not_eligible(self):
        for domain, config in (
            ("input_boolean", boolean_config()),
            ("input_number", number_config()),
        ):
            with self.subTest(domain=domain):
                # Core answered the create with a de-duplicated object id.
                self.ws.generated_id = f"{slug(config['name'])}_2"
                plan = await self.configuration_plan(helper_operation(domain, config))
                await self.approve(plan)
                result = await self.apply(plan)
                self.ws.generated_id = None
                self.assertEqual(result["task_state"], "failed_post_dispatch", result)
                self.assertEqual(
                    self.children(plan["plan_id"]),
                    [("create_helper", "terminal", "verification_mismatch", 1)],
                )
                self.assertEqual(self.identity_values(plan["plan_id"]), [None])
                mark = self.mark()
                await self.refused_rollback(
                    plan, {"create_helper": rollback_model.NOT_VERIFIED}
                )
                self.assertEqual(self.reads_since(mark), [])
                self.assertIn(f"{slug(config['name'])}_2", self.ws.helpers[domain])

    async def assert_uncaptured(self, plan: dict, outcome: str) -> None:
        (record,) = self.identity_values(plan["plan_id"])
        self.assertEqual(record["outcome"], outcome, record)
        self.assertIsNone(record["identity"])
        mark = self.mark()
        await self.refused_rollback(
            plan, {"create_helper": rollback_model.IDENTITY_NOT_CAPTURED}
        )
        self.assertEqual(self.reads_since(mark), [])

    async def test_a_helper_whose_entry_never_appeared_is_never_adopted(self):
        for domain, config in (
            ("input_boolean", boolean_config()),
            ("input_number", number_config()),
        ):
            with self.subTest(domain=domain):
                object_id = slug(config["name"])
                self.ws.create_registers_entity = False
                self.capture_sleeps.clear()
                plan = await self.configuration_plan(helper_operation(domain, config))
                await self.approve(plan)
                result = await self.apply(plan)
                self.ws.create_registers_entity = True
                # The forward outcome is unchanged by the failed capture.
                self.assertEqual(result["task_state"], "succeeded_verified", result)
                self.assertEqual(self.capture_sleeps, [0.5] * 5)
                # The entry registering afterwards is not adopted.
                self.ws.register_helper(domain, object_id)
                await self.assert_uncaptured(
                    plan, "registry_entry_not_observed_within_bound"
                )
                self.assertIn(object_id, self.ws.helpers[domain])

    async def test_a_helper_entry_of_the_other_domain_is_never_captured(self):
        for domain, config in (
            ("input_boolean", boolean_config()),
            ("input_number", number_config()),
        ):
            with self.subTest(domain=domain):
                register = self.ws.register_helper

                def other_domain(kind: str, object_id: str) -> str:
                    entity_id = register(kind, object_id)
                    self.ws.entries[entity_id]["platform"] = OTHER_DOMAIN[kind]
                    return entity_id

                with mock.patch.object(self.ws, "register_helper", other_domain):
                    plan = await self.configuration_plan(
                        helper_operation(domain, config)
                    )
                    await self.approve(plan)
                    result = await self.apply(plan)
                self.assertEqual(result["task_state"], "succeeded_verified", result)
                await self.assert_uncaptured(
                    plan, "registry_entry_not_observed_within_bound"
                )
                self.assertIn(slug(config["name"]), self.ws.helpers[domain])

    async def test_a_helper_entry_without_a_creation_time_is_an_identity_mismatch(self):
        for domain, config in (
            ("input_boolean", boolean_config()),
            ("input_number", number_config()),
        ):
            with self.subTest(domain=domain):
                register = self.ws.register_helper

                def undated(kind: str, object_id: str) -> str:
                    entity_id = register(kind, object_id)
                    del self.ws.entries[entity_id]["created_at"]
                    return entity_id

                with mock.patch.object(self.ws, "register_helper", undated):
                    plan = await self.configuration_plan(
                        helper_operation(domain, config)
                    )
                    await self.approve(plan)
                    result = await self.apply(plan)
                self.assertEqual(result["task_state"], "succeeded_verified", result)
                await self.assert_uncaptured(plan, "identity_mismatch")

    async def test_a_record_without_its_identity_is_never_adopted(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                subject = await self.subject(kind, "older record")
                (declaration,) = self.runtime.children.declarations_for_task(
                    self.task_id(subject.created["plan_id"])
                )
                self.runtime.created_identities._path(declaration["child_id"]).unlink()
                mark = self.mark()
                await self.refused_rollback(
                    subject.created,
                    {subject.operation_id: rollback_model.IDENTITY_NOT_CAPTURED},
                )
                self.assertEqual(self.reads_since(mark), [])
                self.assertTrue(self.present(subject))

    async def test_a_legacy_contract_1_create_keeps_unavailable_for_create(self):
        legacy_gateway = mock.Mock()
        legacy_gateway.get = mock.AsyncMock(return_value=None)
        with mock.patch.object(self.service, "gateway", legacy_gateway):
            created = await self.service.create_plan(
                title="Legacy create",
                description="An older contract-1 record",
                operation="create_automation",
                automation_id="legacy_one",
                proposed_config=automation_config("legacy_one"),
            )
        plan = self.service._load(created["plan_id"])
        self.assertEqual(plan.contract_version, 1)
        self.assertEqual(plan.rollback.status, "unavailable_for_create")
        # The automation it once created exists and is registered.
        self.add("automation", "legacy_one")
        mark = self.mark()
        error = await self.refused(created["plan_id"])
        self.assertEqual(error.code, ErrorCode.ROLLBACK_NOT_AVAILABLE, error.details)
        self.assertEqual(self.reads_since(mark), [])
        projected = self.service.get_plan(created["plan_id"])["rollback"]
        self.assertIs(projected["available"], False)
        self.assertEqual(projected["status"], "unavailable_for_create")
        self.assertIn("legacy_one", self.config_rest.configs["automation"])


if __name__ == "__main__":
    unittest.main()
