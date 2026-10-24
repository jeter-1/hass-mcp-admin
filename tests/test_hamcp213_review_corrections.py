"""HAMCP-213 PR 1 independent-review corrections (H1, H2, H3).

Principle: when the comparison cannot prove that a protected effect and its
reachability are unchanged, the verdict is ``changed`` or ``unresolved`` and
the per-object safety-critical acknowledgement is required; never ``none`` or
``equivalent``.

* H1: an effect-capable call whose target or service name is templated,
  variable-driven or a registry selector (area, device, floor, label) is an
  ``unresolved`` protected effect.
* H2: a reference-only step that can run before a protected effect is
  compared in full (nested data, target, continue_on_error, enabled, and its
  position relative to conditions, waits and branches).
* H3: no shortcut (the beta.33 retained-effect exception included) applies
  where an unlock or disarm effect is present (OD-6(b) floor).

Every reproduction runs through both the configuration service and the F3
runtime. For each one, plan approval alone leaves the plan pending with no
write, and the exact acknowledgement keeps it actionable and dispatches it
once. All fixtures are synthetic.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.errors import GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalPolicyClass,
)
from ha_mcp_engineering.governance.normalize import (  # noqa: E402
    normalize_automation,
)
from ha_mcp_engineering.governance.physical_effects import (  # noqa: E402
    VERDICT_CHANGED,
    VERDICT_EQUIVALENT,
    VERDICT_NONE,
    VERDICT_UNRESOLVED,
    _Analyzer,
    compare_operation,
    compare_physical_effects,
)
from ha_mcp_engineering.governance import policy as policy_module  # noqa: E402
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    configuration_operation_policy,
)
from tests import test_dev14_configuration_plans as dev14  # noqa: E402
from tests import test_f3_runtime_integration as f3  # noqa: E402
from tests import test_hamcp213_pr001_fixtures as pr001  # noqa: E402
from tests.test_beta33_risk_delta import (  # noqa: E402
    CURRENT_GARAGE_AUTOMATION,
    GARAGE_TARGET,
    OBSTRUCTION_GUARD,
)

PRINCIPAL = "home_assistant_admin_ingress:dev14-reviewer"
SAFETY = "safety_critical_acknowledgement"
UNLOCK_OR_DISARM = "unlock_or_disarm_effect"

GARAGE = "cover.garage_main"
CLOSE = {"action": "cover.close_cover", "target": {"entity_id": GARAGE}}
OPEN = {"action": "cover.open_cover", "target": {"entity_id": GARAGE}}
DELAY = {"delay": "00:01:00"}
GUARD = {"condition": "state", "entity_id": "binary_sensor.review", "state": "on"}


def call(service, **fields):
    return {"action": service, **fields}


def script(*steps, mode="single", alias="Review script"):
    return {"alias": alias, "mode": mode, "sequence": list(steps)}


def automation(
    *steps,
    trigger_to="on",
    conditions=(),
    mode="single",
    alias="Review automation",
    description="HAMCP-213 review correction",
):
    return {
        "alias": alias,
        "description": description,
        "mode": mode,
        "trigger": [
            {"platform": "state", "entity_id": "binary_sensor.review", "to": trigger_to}
        ],
        "condition": [copy.deepcopy(item) for item in conditions],
        "action": list(steps),
    }


def note(message="Garage", **fields):
    return {
        "action": "notify.mobile_app_review",
        "data": {"message": message},
        **fields,
    }


def nested_note(tag):
    # Reviewer H2 shape: nested notification data under ``data.data``.
    return {
        "action": "notify.mobile_app_review",
        "data": {"message": "Garage", "data": {"tag": tag}},
    }


def templated(service, selector):
    return call(service, target={"entity_id": selector})


def cover(service, **fields):
    return call(f"cover.{service}", **fields)


UNLOCK = {"action": "lock.unlock", "target": {"entity_id": "lock.review_entry"}}
DISARM = {
    "action": "alarm_control_panel.alarm_disarm",
    "target": {"entity_id": "alarm_control_panel.review_house"},
}


@dataclass(frozen=True)
class Case:
    name: str
    kind: str
    before: dict
    after: dict
    verdict: str
    unlock_or_disarm: bool = False


def _variable_script(service):
    return script(
        {"variables": {"door": GARAGE}},
        templated(service, "{{ door }}"),
    )


H1_CASES = (
    # Reviewer reproduction: a templated garage target, close -> open.
    Case(
        "template_target",
        "script",
        script(templated("cover.close_cover", "{{ 'cover.garage_main' }}")),
        script(templated("cover.open_cover", "{{ 'cover.garage_main' }}")),
        VERDICT_UNRESOLVED,
    ),
    Case(
        "variable_target",
        "script",
        _variable_script("cover.close_cover"),
        _variable_script("cover.open_cover"),
        VERDICT_UNRESOLVED,
    ),
    Case(
        "templated_service_name",
        "script",
        script(call("{{ 'cover.close_cover' }}", target={"entity_id": GARAGE})),
        script(call("{{ 'cover.open_cover' }}", target={"entity_id": GARAGE})),
        VERDICT_UNRESOLVED,
        # A service name that cannot be resolved may be an unlock.
        unlock_or_disarm=True,
    ),
    Case(
        "service_template_key",
        "script",
        script({"service_template": "{{ 'cover.close_cover' }}", "target": {"entity_id": GARAGE}}),
        script({"service_template": "{{ 'cover.open_cover' }}", "target": {"entity_id": GARAGE}}),
        VERDICT_UNRESOLVED,
        unlock_or_disarm=True,
    ),
    Case(
        "templated_lock_service",
        "script",
        script(call("{{ 'lock.lock' }}", target={"entity_id": "lock.review_entry"})),
        script(call("{{ 'lock.unlock' }}", target={"entity_id": "lock.review_entry"})),
        VERDICT_UNRESOLVED,
        unlock_or_disarm=True,
    ),
    *(
        Case(
            f"{selector}_target",
            "script",
            script(cover("close_cover", target={selector: "review_garage"})),
            script(cover("open_cover", target={selector: "review_garage"})),
            VERDICT_UNRESOLVED,
        )
        for selector in ("area_id", "device_id", "label_id", "floor_id")
    ),
    Case(
        "area_in_data",
        "script",
        script(cover("close_cover", data={"area_id": "review_garage"})),
        script(cover("open_cover", data={"area_id": "review_garage"})),
        VERDICT_UNRESOLVED,
    ),
    Case(
        "templated_entity_in_data",
        "script",
        script(cover("close_cover", data={"entity_id": "{{ 'cover.garage_main' }}"})),
        script(cover("open_cover", data={"entity_id": "{{ 'cover.garage_main' }}"})),
        VERDICT_UNRESOLVED,
    ),
    Case(
        "device_action_registry_entity",
        "script",
        script(
            {
                "domain": "cover",
                "device_id": "0123456789abcdef0123456789abcdef",
                "entity_id": "fedcba9876543210fedcba9876543210",
                "type": "close",
            }
        ),
        script(
            {
                "domain": "cover",
                "device_id": "0123456789abcdef0123456789abcdef",
                "entity_id": "fedcba9876543210fedcba9876543210",
                "type": "open",
            }
        ),
        VERDICT_UNRESOLVED,
    ),
    Case(
        "generic_turn_on_area",
        "script",
        script(call("homeassistant.turn_off", target={"area_id": "review_garage"})),
        script(call("homeassistant.turn_on", target={"area_id": "review_garage"})),
        VERDICT_UNRESOLVED,
    ),
    Case(
        "lock_area_target",
        "script",
        script(call("lock.lock", target={"area_id": "review_entry"})),
        script(call("lock.unlock", target={"area_id": "review_entry"})),
        VERDICT_UNRESOLVED,
        unlock_or_disarm=True,
    ),
    Case(
        "unknown_service_templated_target",
        "script",
        script(templated("review_vendor.trigger", "{{ first }}")),
        script(templated("review_vendor.trigger", "{{ second }}")),
        VERDICT_UNRESOLVED,
    ),
)

H2_CASES = (
    # Reviewer reproductions: dropping nested notification data hides a
    # changed abort point ahead of the close.
    Case(
        "nested_template_removed",
        "script",
        script(nested_note("{{ 1 / 0 }}"), CLOSE),
        script(nested_note("working"), CLOSE),
        VERDICT_CHANGED,
    ),
    Case(
        "nested_template_removed_continue_on_error",
        "script",
        script(dict(nested_note("{{ 1 / 0 }}"), continue_on_error=True), CLOSE),
        script(dict(nested_note("working"), continue_on_error=True), CLOSE),
        VERDICT_CHANGED,
    ),
    Case(
        "nested_data_removed",
        "script",
        script(nested_note("review"), CLOSE),
        script(note(), CLOSE),
        VERDICT_CHANGED,
    ),
    Case(
        "nested_data_added",
        "script",
        script(note(), CLOSE),
        script(nested_note("review"), CLOSE),
        VERDICT_CHANGED,
    ),
    Case(
        "nested_data_changed",
        "script",
        script(nested_note("first"), CLOSE),
        script(nested_note("second"), CLOSE),
        VERDICT_CHANGED,
    ),
    Case(
        "message_changed",
        "script",
        script(note("first"), CLOSE),
        script(note("second"), CLOSE),
        VERDICT_CHANGED,
    ),
    Case(
        "continue_on_error_toggled",
        "script",
        script(note(), CLOSE),
        script(note(continue_on_error=True), CLOSE),
        VERDICT_CHANGED,
    ),
    Case(
        "enabled_toggled",
        "script",
        script(note(), CLOSE),
        script(note(enabled=False), CLOSE),
        VERDICT_CHANGED,
    ),
    Case(
        "reference_target_changed",
        "script",
        script(
            call("notify.send_message", target={"entity_id": "notify.review_a"}, data={"message": "m"}),
            CLOSE,
        ),
        script(
            call("notify.send_message", target={"entity_id": "notify.review_b"}, data={"message": "m"}),
            CLOSE,
        ),
        VERDICT_CHANGED,
    ),
    Case(
        "moved_across_wait",
        "script",
        script(note(), DELAY, CLOSE),
        script(DELAY, note(), CLOSE),
        VERDICT_CHANGED,
    ),
    Case(
        "moved_across_condition",
        "script",
        script(note(), GUARD, CLOSE),
        script(GUARD, note(), CLOSE),
        VERDICT_CHANGED,
    ),
    Case(
        "changed_in_branch",
        "script",
        script({"choose": [{"conditions": [], "sequence": [note("first")]}]}, CLOSE),
        script({"choose": [{"conditions": [], "sequence": [note("second")]}]}, CLOSE),
        VERDICT_CHANGED,
    ),
    Case(
        "changed_in_parallel_sibling",
        "script",
        script({"parallel": [nested_note("{{ 1 / 0 }}"), CLOSE]}),
        script({"parallel": [nested_note("working"), CLOSE]}),
        VERDICT_CHANGED,
    ),
    Case("added", "script", script(CLOSE), script(note(), CLOSE), VERDICT_CHANGED),
    Case("removed", "script", script(note(), CLOSE), script(CLOSE), VERDICT_CHANGED),
    # Reviewer probe: the close moved across a wait.
    Case(
        "close_reordered_across_wait",
        "script",
        script(DELAY, CLOSE),
        script(CLOSE, DELAY),
        VERDICT_CHANGED,
    ),
)


def _guarded(service_step, **kwargs):
    return automation(service_step, conditions=(GUARD,), **kwargs)


H3_CASES = tuple(
    case
    for label, effect in (("unlock", UNLOCK), ("disarm", DISARM))
    for case in (
        # Reviewer reproductions: an added condition changes when the
        # unlock or disarm runs (the retained-effect exception applied).
        Case(f"{label}_condition_added", "automation", automation(effect), _guarded(effect), VERDICT_CHANGED, True),
        Case(f"{label}_condition_removed", "automation", _guarded(effect), automation(effect), VERDICT_CHANGED, True),
        Case(
            f"{label}_trigger_changed",
            "automation",
            automation(effect),
            automation(effect, trigger_to="off"),
            VERDICT_CHANGED,
            True,
        ),
        Case(
            f"{label}_mode_changed",
            "automation",
            automation(effect),
            automation(effect, mode="restart"),
            VERDICT_CHANGED,
            True,
        ),
        Case(
            f"{label}_wait_added",
            "automation",
            automation(effect),
            automation(DELAY, effect),
            VERDICT_CHANGED,
            True,
        ),
        Case(
            f"{label}_branch_added",
            "automation",
            automation(effect),
            automation({"if": [GUARD], "then": [effect]}),
            VERDICT_CHANGED,
            True,
        ),
        # OD-6(b): an unchanged unlock or disarm in an otherwise-edited
        # automation is equivalent but still needs the acknowledgement.
        Case(
            f"{label}_unchanged_description_edited",
            "automation",
            automation(effect),
            automation(effect, description="Edited description"),
            VERDICT_EQUIVALENT,
            True,
        ),
    )
)

# Controls: nothing above may make these need the acknowledgement.
PLAN_APPROVAL_ONLY_CASES = (
    Case(
        "literal_non_protected_cover",
        "script",
        script(cover("close_cover", target={"entity_id": "cover.review_blinds"})),
        script(cover("open_cover", target={"entity_id": "cover.review_blinds"})),
        VERDICT_NONE,
    ),
    Case(
        "light_area_target",
        "script",
        script(call("light.turn_off", target={"area_id": "review_garage"})),
        script(call("light.turn_on", target={"area_id": "review_garage"})),
        VERDICT_NONE,
    ),
    Case(
        "trailing_wording_after_close",
        "script",
        script(CLOSE, note("first")),
        script(CLOSE, note("second")),
        VERDICT_EQUIVALENT,
    ),
    Case(
        "trailing_nested_data_after_close",
        "script",
        script(CLOSE, nested_note("first")),
        script(CLOSE, nested_note("second")),
        VERDICT_EQUIVALENT,
    ),
)

PR001_VERDICTS = {
    "op1": VERDICT_NONE,
    "op2": VERDICT_CHANGED,
    "op3": VERDICT_EQUIVALENT,
    "op4": VERDICT_NONE,
}


def _body_key(kind):
    return "sequence" if kind == "script" else "action"


class _ReviewCorrectionChecks:
    """Shared checks; each concrete class selects one execution path."""

    def writes(self):
        return [item for item in self.gateway.calls if item[0] == "write"]

    async def create(self, case):
        target_id = f"review_{case.name}"
        self.gateway.configs[(case.kind, target_id)] = copy.deepcopy(case.before)
        created = await self.service.create_configuration_plan(
            title=f"Review correction {case.name}",
            description="HAMCP-213 synthetic review reproduction",
            operations=[
                {
                    "operation_id": "op",
                    "resource_type": case.kind,
                    "action": "update",
                    "target_id": target_id,
                    "depends_on": [],
                    "proposed_config": copy.deepcopy(case.after),
                }
            ],
        )
        (operation,) = self.repository.get(created["plan_id"]).operations
        return created, operation, (case.kind, target_id)

    async def approve_plan_only(self, created):
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertEqual(pending["approval_action"], "plan_approval")
        _review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        return await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind=pending["approval_kind"],
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal=PRINCIPAL,
        )

    async def acknowledge(self, created, plan_only):
        review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], plan_only["challenge_id"]
        )
        safety = review["safety_critical_acknowledgement"]
        return await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=plan_only["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind=plan_only["approval_kind"],
            approval_action=plan_only["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal=PRINCIPAL,
            acknowledged_objects=tuple(row["object_ref"] for row in safety["objects"]),
            acknowledged_projection_hash=safety["projection_hash"],
        ), safety

    def assert_applied_once(self, case, target, writes_before):
        self.assertEqual(len(self.writes()), writes_before + 1)
        key = _body_key(case.kind)
        self.assertEqual(self.gateway.configs[target][key], case.after[key])

    async def assert_acknowledgement_gated(self, case):
        writes_before = len(self.writes())
        created, operation, target = await self.create(case)
        comparison = compare_operation(operation)
        self.assertEqual(comparison.verdict, case.verdict, comparison.reason_codes)
        # The verdict is stable across recomputation (challenge, decision,
        # dispatch all recompute it).
        self.assertEqual(
            compare_operation(operation).projection_hash, comparison.projection_hash
        )
        decision = created["policy_decision"]
        self.assertEqual(decision["policy_class"], "elevated_admin")
        self.assertEqual(decision["required_acknowledgements"], ["plan_approval", SAFETY])
        rows = comparison.projection["rows"]
        self.assertTrue(any(row["acknowledgement_required"] for row in rows))
        self.assertEqual(
            any(UNLOCK_OR_DISARM in row["reason_codes"] for row in rows),
            case.unlock_or_disarm,
            rows,
        )

        # Plan approval alone: pending, refused at apply, no write.
        plan_only = await self.approve_plan_only(created)
        self.assertEqual(plan_only["status"], "approval_pending")
        self.assertEqual(plan_only["approval_action"], SAFETY)
        with self.assertRaises(GovernanceError):
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(len(self.writes()), writes_before)
        self.assertEqual(self.gateway.configs[target], case.before)

        # The exact acknowledgement keeps it owner-actionable: one dispatch.
        granted, safety = await self.acknowledge(created, plan_only)
        self.assertTrue(safety["objects"])
        if case.verdict == VERDICT_UNRESOLVED:
            self.assertTrue(
                any(row["verdict"] == VERDICT_UNRESOLVED for row in safety["objects"])
            )
        self.assertEqual(granted["status"], "approved")
        await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assert_applied_once(case, target, writes_before)

    async def assert_plan_approval_only(self, case):
        writes_before = len(self.writes())
        created, operation, target = await self.create(case)
        comparison = compare_operation(operation)
        self.assertEqual(comparison.verdict, case.verdict, comparison.reason_codes)
        self.assertEqual(
            created["policy_decision"]["required_acknowledgements"], ["plan_approval"]
        )
        granted = await self.approve_plan_only(created)
        self.assertEqual(granted["status"], "approved")
        await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assert_applied_once(case, target, writes_before)

    async def test_h1_unresolvable_effects_require_the_acknowledgement(self):
        for case in H1_CASES:
            with self.subTest(case=case.name):
                await self.assert_acknowledgement_gated(case)

    async def test_h2_reference_steps_before_an_effect_are_compared_in_full(self):
        for case in H2_CASES:
            with self.subTest(case=case.name):
                await self.assert_acknowledgement_gated(case)

    async def test_h3_unlock_and_disarm_always_require_the_acknowledgement(self):
        for case in H3_CASES:
            with self.subTest(case=case.name):
                await self.assert_acknowledgement_gated(case)

    async def test_controls_keep_plan_approval_only(self):
        for case in PLAN_APPROVAL_ONLY_CASES:
            with self.subTest(case=case.name):
                await self.assert_plan_approval_only(case)

    async def test_garage_retained_effect_exception_is_unchanged(self):
        # H3 does not touch the beta.33 exception for a non-unlock effect: an
        # exact appended reviewed guard keeps plan approval only.
        self.gateway.configs[GARAGE_TARGET] = copy.deepcopy(CURRENT_GARAGE_AUTOMATION)
        proposed = copy.deepcopy(CURRENT_GARAGE_AUTOMATION)
        proposed.pop("id")
        proposed["condition"].append(copy.deepcopy(OBSTRUCTION_GUARD))
        created = await self.service.create_configuration_plan(
            title="Retained garage guard",
            description="HAMCP-213 retained-effect control",
            operations=[
                {
                    "operation_id": "guard",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": GARAGE_TARGET[1],
                    "depends_on": [],
                    "proposed_config": proposed,
                }
            ],
        )
        self.assertEqual(
            created["policy_decision"]["required_acknowledgements"], ["plan_approval"]
        )
        granted = await self.approve_plan_only(created)
        self.assertEqual(granted["status"], "approved")

    async def test_pr001_verdicts_are_unchanged_by_the_corrections(self):
        for op, resource_type, target in pr001.PLAN:
            self.gateway.configs[(resource_type, target)] = pr001.load(
                f"{op}_before.yaml"
            )
        operations = []
        for op, resource_type, target in pr001.PLAN:
            proposed = pr001.load(f"{op}_after.yaml")
            proposed.pop("id", None)
            operations.append(
                {
                    "operation_id": op,
                    "resource_type": resource_type,
                    "action": "update",
                    "target_id": target,
                    "depends_on": [],
                    "proposed_config": proposed,
                }
            )
        created = await self.service.create_configuration_plan(
            title="PR001 audit plan",
            description="HAMCP-213 anonymized PR001 regression",
            operations=operations,
        )
        stored = {
            item.operation_id: item
            for item in self.repository.get(created["plan_id"]).operations
        }
        for op, verdict in PR001_VERDICTS.items():
            with self.subTest(op=op):
                hashes = {compare_operation(stored[op]).projection_hash for _ in range(3)}
                self.assertEqual(len(hashes), 1)
                comparison = compare_operation(stored[op])
                self.assertEqual(comparison.verdict, verdict, comparison.reason_codes)
                self.assertFalse(comparison.unlock_or_disarm)
        self.assertEqual(
            created["policy_decision"]["required_acknowledgements"],
            ["plan_approval", SAFETY],
        )
        plan_only = await self.approve_plan_only(created)
        self.assertEqual(plan_only["status"], "approval_pending")
        with self.assertRaises(GovernanceError):
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(self.writes(), [])


class ServicePathReviewCorrectionTests(
    _ReviewCorrectionChecks, dev14.ConfigurationPlanTestCase
):
    """The reproductions through the configuration service."""

    async def test_legacy_route_refuses_an_unlock_guard_change(self):
        # The legacy contract-v1 route keeps its historical boundary, but the
        # retained-effect exception no longer admits an unlock or disarm
        # under current policy; historical recomputation is unchanged.
        for effect in (UNLOCK, DISARM):
            with self.subTest(service=effect["action"]):
                case = Case(
                    f"legacy_{effect['action'].replace('.', '_')}",
                    "automation",
                    automation(effect),
                    _guarded(effect),
                    VERDICT_CHANGED,
                    True,
                )
                _created, operation, _target = await self.create(case)
                legacy = configuration_operation_policy(
                    operation,
                    owner_authoritative=False,
                    safety_critical_comparison=False,
                    unlock_or_disarm_floor=True,
                )
                self.assertEqual(legacy.policy_class, ApprovalPolicyClass.PROHIBITED)
                self.assertFalse(legacy.safety_critical_acknowledgement)
                historical = configuration_operation_policy(
                    operation,
                    owner_authoritative=False,
                    safety_critical_comparison=False,
                )
                self.assertNotEqual(
                    historical.policy_class, ApprovalPolicyClass.PROHIBITED
                )
        self.assertEqual(self.writes(), [])


    async def test_no_shortcut_applies_to_an_unlock_or_disarm(self):
        # Review H3: the garage-inverse and retry-transfer member shortcuts
        # are bypassed where an unlock or disarm is present, even if an
        # operation were marked as a member. A cover control shows that the
        # forced marker otherwise takes the shortcut.
        shortcuts = (
            (policy_module.inverse_eligibility, "local_member_marked", "exact_verified_garage_inverse"),
            (policy_module, "local_member_marked", "proved_configuration_retry_transfer"),
        )
        control = Case(
            "shortcut_cover",
            "automation",
            automation(CLOSE),
            automation(CLOSE, description="Edited description"),
            VERDICT_EQUIVALENT,
        )
        _created, cover_operation, _target = await self.create(control)
        floor_operations = []
        for effect in (UNLOCK, DISARM):
            case = Case(
                f"shortcut_{effect['action'].replace('.', '_')}",
                "automation",
                automation(effect),
                automation(effect, description="Edited description"),
                VERDICT_EQUIVALENT,
                True,
            )
            floor_operations.append((await self.create(case))[1])
        for owner, name, shortcut_reason in shortcuts:
            with self.subTest(shortcut=shortcut_reason), mock.patch.object(
                owner, name, return_value=True
            ):
                taken = configuration_operation_policy(cover_operation)
                self.assertIn(shortcut_reason, taken.reason_codes)
                for operation in floor_operations:
                    result = configuration_operation_policy(operation)
                    self.assertNotIn(shortcut_reason, result.reason_codes)
                    self.assertEqual(result.policy_class, ApprovalPolicyClass.ELEVATED_ADMIN)
                    self.assertTrue(result.safety_critical_acknowledgement)
                    self.assertIn(
                        "safety_critical_owner_acknowledgement_required",
                        result.reason_codes,
                    )
                    self.assertTrue(compare_operation(operation).unlock_or_disarm)
        self.assertEqual(self.writes(), [])


class F3PathReviewCorrectionTests(
    _ReviewCorrectionChecks, dev14.ConfigurationPlanTestCase
):
    """The same reproductions through the F3 runtime."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.runtime = f3.F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=f3._ExactFakeConfigurationGateway(self.gateway),
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=f3._provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")


class ComparatorDeterminismTests(unittest.TestCase):
    """The effect memo must not return a result cached for a reused id."""

    def test_effect_memo_survives_temporary_list_id_reuse(self):
        analyzer = _Analyzer()
        for _ in range(200):
            self.assertTrue(analyzer.contains_effect([copy.deepcopy(CLOSE)]))
            self.assertFalse(analyzer.contains_effect([copy.deepcopy(DELAY)]))

    def test_repeated_comparisons_are_identical(self):
        op2 = normalize_automation(pr001.load("op2_after.yaml"))
        first = compare_physical_effects("automation", "update", op2, copy.deepcopy(op2))
        self.assertEqual(first.verdict, VERDICT_EQUIVALENT, first.reason_codes)
        for _ in range(5):
            again = compare_physical_effects(
                "automation", "update", op2, copy.deepcopy(op2)
            )
            self.assertEqual(again.verdict, first.verdict, again.reason_codes)
            self.assertEqual(again.projection_hash, first.projection_hash)


if __name__ == "__main__":
    unittest.main()
