"""HAMCP-213 D10: PR001 regression on the anonymized owner fixtures.

The audit plan (four updates touching one garage cover) was prohibited under
f2-v2. Under f2-v3 each operation gets its own comparison verdict: op1 and op4
have no protected effect, op2 changes the run structure ahead of a close
(conservative OD-10), and op3 keeps every close equivalent. The whole plan is
actionable after plan approval plus the per-object acknowledgement, and each
of the seven D6 mutations turns its operation's verdict to ``changed``.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.physical_effects import (  # noqa: E402
    VERDICT_CHANGED,
    VERDICT_EQUIVALENT,
    VERDICT_NONE,
    compare_operation,
)
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    configuration_operation_policy,
)
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
)

FIXTURES = ROOT / "tests" / "fixtures" / "hamcp213_pr001"
SOURCE = ROOT / "docs" / "evidence" / "hamcp213" / "pr001"
PRINCIPAL = "home_assistant_admin_ingress:dev14-reviewer"
SAFETY = "safety_critical_acknowledgement"

# (operation id, resource type, target id) in plan order.
PLAN = (
    ("op1", "automation", "pr001_op1_open_warning"),
    ("op2", "automation", "pr001_op2_departure_close"),
    ("op3", "script", "pr001_op3_close_attempt"),
    ("op4", "automation", "pr001_op4_evening_alert"),
)
EXPECTED = {
    "op1": (VERDICT_NONE, "standard_admin", False),
    "op2": (VERDICT_CHANGED, "elevated_admin", True),
    "op3": (VERDICT_EQUIVALENT, "elevated_admin", False),
    "op4": (VERDICT_NONE, "standard_admin", False),
}
RECIPIENT_REASON = "Recipient or environmental-control behavior may change"
RETRY_WARNING = "No complete retry-transformation proof"


def load(name: str) -> dict:
    return yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))


def service(step):
    value = step.get("action") or step.get("service")
    return value if isinstance(value, str) else ""


def first_override_branch(config):
    # op2: first departure branch -> qualifying choose -> the close sequence.
    sequence = config["actions"][0]["choose"][0]["sequence"][6]["choose"][0][
        "sequence"
    ]
    assert service(sequence[2]) == "cover.close_cover"
    assert sequence[2]["target"]["entity_id"] == "cover.garage_main"
    return sequence


def m_remove_continue_on_error(config):
    note = first_override_branch(config)[0]["choose"][0]["sequence"][1]
    assert service(note).startswith("notify.")
    assert note.pop("continue_on_error") is True


def m_delay_before_close(config):
    first_override_branch(config).insert(2, {"delay": "00:05:00"})


def m_condition_before_close(config):
    first_override_branch(config).insert(
        2,
        {
            "condition": "state",
            "entity_id": "input_boolean.visitor_mode",
            "state": "on",
        },
    )


def m_wait_after_close(config):
    first_override_branch(config).append(
        {"wait_template": "{{ false }}", "timeout": "00:10:00"}
    )


def m_script_mode(config):
    assert config["mode"] == "single"
    config["mode"] = "restart"


def m_script_target(config):
    step = config["sequence"][0]["choose"][1]["sequence"][0]
    assert service(step) == "cover.close_cover"
    step["target"]["entity_id"] = "cover.garage_side"


def m_add_close_to_warning(config):
    config["actions"][0]["choose"][1]["sequence"].append(
        {
            "action": "cover.close_cover",
            "target": {"entity_id": "cover.garage_main"},
        }
    )


# The seven D6 mutations; each must give `changed` for its operation.
MUTATIONS = (
    ("op2 added push loses continue_on_error", "op2", m_remove_continue_on_error),
    ("op2 delay inserted before a close", "op2", m_delay_before_close),
    ("op2 condition inserted before a close", "op2", m_condition_before_close),
    ("op2 long wait appended after a close", "op2", m_wait_after_close),
    ("op3 mode single -> restart", "op3", m_script_mode),
    ("op3 close target changed", "op3", m_script_target),
    ("op1 gains a cover.close_cover", "op1", m_add_close_to_warning),
)


class Pr001FixtureProvenanceTests(unittest.TestCase):
    def test_test_copies_match_the_committed_design_fixtures(self):
        provenance = (FIXTURES / "PROVENANCE.md").read_text(encoding="utf-8")
        names = sorted(path.name for path in FIXTURES.glob("*.yaml"))
        self.assertEqual(
            names,
            sorted(f"{op}_{side}.yaml" for op, _t, _i in PLAN for side in ("before", "after")),
        )
        for name in names:
            digest = hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
            self.assertIn(f"`{name}` | `{digest}`", provenance)
            self.assertEqual(
                digest, hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()
            )


class Pr001RegressionTests(ConfigurationPlanTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        for op, resource_type, target in PLAN:
            self.gateway.configs[(resource_type, target)] = load(
                f"{op}_before.yaml"
            )

    def operation(self, op, *, mutate=None):
        _op, resource_type, target = next(row for row in PLAN if row[0] == op)
        proposed = load(f"{op}_after.yaml")
        if mutate is not None:
            mutate(proposed)
        proposed.pop("id", None)
        return {
            "operation_id": op,
            "resource_type": resource_type,
            "action": "update",
            "target_id": target,
            "depends_on": [],
            "proposed_config": proposed,
        }

    async def create(self, operations, title="PR001 audit plan"):
        return await self.service.create_configuration_plan(
            title=title,
            description="HAMCP-213 anonymized PR001 regression",
            operations=operations,
        )

    async def create_pr001_plan(self):
        return await self.create([self.operation(op) for op, _t, _i in PLAN])

    def stored_operations(self, created):
        plan = self.repository.get(created["plan_id"])
        return {item.operation_id: item for item in plan.operations}

    def writes(self):
        return [call for call in self.gateway.calls if call[0] == "write"]

    async def test_each_operation_gets_its_expected_verdict_and_class(self):
        created = await self.create_pr001_plan()
        for op, operation in self.stored_operations(created).items():
            verdict, policy_class, acknowledgement = EXPECTED[op]
            with self.subTest(op=op):
                comparison = compare_operation(operation)
                self.assertEqual(comparison.verdict, verdict, comparison.reason_codes)
                classification = configuration_operation_policy(operation)
                self.assertEqual(classification.policy_class.value, policy_class)
                self.assertIs(
                    classification.safety_critical_acknowledgement,
                    acknowledgement,
                )
                self.assertEqual(
                    comparison.acknowledgement_required, acknowledgement
                )
                if op == "op3":
                    self.assertIn(
                        "safety_critical_effect_equivalent",
                        classification.reason_codes,
                    )
                if op == "op2":
                    self.assertIn(
                        "run_structure_changed", comparison.reason_codes
                    )

    async def test_no_recipient_reason_and_no_retry_warning(self):
        created = await self.create_pr001_plan()
        for op, operation in self.stored_operations(created).items():
            with self.subTest(op=op):
                self.assertNotIn(RECIPIENT_REASON, operation.risk.reasons)
                self.assertFalse(
                    any(RETRY_WARNING in item for item in operation.risk.warnings),
                    operation.risk.warnings,
                )
                self.assertTrue(operation.risk.apply_allowed)

    async def test_whole_plan_is_approvable_and_applies_after_the_acknowledgement(
        self,
    ):
        created = await self.create_pr001_plan()
        decision = created["policy_decision"]
        self.assertEqual(decision["policy_class"], "elevated_admin")
        self.assertEqual(
            decision["required_acknowledgements"], ["plan_approval", SAFETY]
        )
        self.assertNotIn("owner_decision_required", decision["reason_codes"])

        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        plan_only = await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind=pending["approval_kind"],
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal=PRINCIPAL,
        )
        self.assertEqual(plan_only["status"], "approval_pending")
        self.assertEqual(plan_only["approval_action"], SAFETY)

        # Plan approval alone never dispatches the plan.
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(
            raised.exception.code,
            ErrorCode.SAFETY_CRITICAL_ACKNOWLEDGEMENT_REQUIRED,
        )
        self.assertEqual(self.writes(), [])

        review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], plan_only["challenge_id"]
        )
        safety = review["safety_critical_acknowledgement"]
        # Only op2 names objects: op3 is equivalent, op1 and op4 have none.
        self.assertEqual(
            {row["operation_id"] for row in safety["objects"]}, {"op2"}
        )
        self.assertEqual(
            {row["verdict"] for row in safety["objects"]}, {"changed"}
        )
        granted = await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=plan_only["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind=plan_only["approval_kind"],
            approval_action=plan_only["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal=PRINCIPAL,
            acknowledged_objects=tuple(
                row["object_ref"] for row in safety["objects"]
            ),
            acknowledged_projection_hash=safety["projection_hash"],
        )
        self.assertEqual(granted["status"], "approved")
        applied = await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(
            [call[3] for call in self.writes()], [row[2] for row in PLAN]
        )

    async def test_equivalent_script_alone_needs_plan_approval_only(self):
        created = await self.create([self.operation("op3")], title="PR001 op3")
        decision = created["policy_decision"]
        self.assertEqual(decision["policy_class"], "elevated_admin")
        self.assertEqual(decision["required_acknowledgements"], ["plan_approval"])

    async def test_cover_free_operations_alone_are_standard(self):
        created = await self.create(
            [self.operation("op1"), self.operation("op4")], title="PR001 op1+op4"
        )
        decision = created["policy_decision"]
        self.assertEqual(decision["policy_class"], "standard_admin")
        self.assertEqual(decision["required_acknowledgements"], ["plan_approval"])

    def mutation_baseline(self, op):
        """Current config each mutation is compared against.

        op2 is already ``changed`` against its before config (conservative
        OD-10), so a mutation there could pass vacuously. Its mutations are
        compared against the unmutated op2 after config instead; op1 (none)
        and op3 (equivalent) keep their before configs.
        """

        if op == "op2":
            _op, resource_type, target = PLAN[1]
            self.gateway.configs[(resource_type, target)] = load("op2_after.yaml")

    async def test_mutation_baselines_are_not_changed(self):
        # Controls: without a mutation no baseline is `changed`, so each
        # mutation below is what turns the verdict.
        expected = {"op1": VERDICT_NONE, "op2": VERDICT_EQUIVALENT, "op3": VERDICT_EQUIVALENT}
        for op, verdict in expected.items():
            with self.subTest(op=op):
                self.mutation_baseline(op)
                created = await self.create([self.operation(op)], title=f"PR001 {op}")
                comparison = compare_operation(self.stored_operations(created)[op])
                self.assertEqual(comparison.verdict, verdict, comparison.reason_codes)
                self.assertEqual(
                    created["policy_decision"]["required_acknowledgements"],
                    ["plan_approval"],
                )

    async def test_each_d6_mutation_gives_changed(self):
        for name, op, mutate in MUTATIONS:
            with self.subTest(mutation=name):
                self.mutation_baseline(op)
                created = await self.create(
                    [self.operation(op, mutate=mutate)], title=f"PR001 {op}"
                )
                operation = self.stored_operations(created)[op]
                comparison = compare_operation(operation)
                self.assertEqual(
                    comparison.verdict, VERDICT_CHANGED, comparison.reason_codes
                )
                self.assertEqual(
                    created["policy_decision"]["required_acknowledgements"],
                    ["plan_approval", SAFETY],
                )


if __name__ == "__main__":
    unittest.main()
