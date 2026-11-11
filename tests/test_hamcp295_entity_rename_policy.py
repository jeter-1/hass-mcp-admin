"""HAMCP-295/109 rename approval policy and persistence (design 3.1, 6, 6.1, 8).

Josh decisions (in-session 2026-11-08), not reopened here: a plan-level
elevated acknowledgement whenever any identified reference is not rewritten
(item 7) and ``elevated_admin`` for actuator renames (item 8).
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
FIXTURES = ROOT / "tests" / "fixtures" / "hamcp109_downgrade"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.governance import entity_rename_policy as erp  # noqa: E402
from ha_mcp_engineering.governance import policy as policy_module  # noqa: E402
from ha_mcp_engineering.governance import risk  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalActionKind,
    ApprovalPolicyClass,
    ChangeOperation,
    ChangePlan,
    ChangeRiskAssessment,
    ChangeTarget,
    ConfigurationOperation,
    PhysicalConsequence,
    PlanStatus,
    RiskDelta,
    RiskLevel,
)
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    evaluate_change_policy,
    policy_subject_payload,
)
from ha_mcp_engineering.governance.task_models import new_execution_task, ExecutionTask  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_ENTITY_METADATA_PROFILES,
)


TS = "2026-11-09T00:00:00+00:00"
DASHBOARD_REF = {
    "holder_type": "dashboard",
    "holder_id": "overview",
    "position": "$.views[0].cards[0].entity",
    "reason": "no_governed_writer",
}
PLAN_APPROVAL = ApprovalActionKind.PLAN_APPROVAL
ELEVATED_ACK = ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT


def rename_op(old="sensor.a", new="sensor.b", refs=(), **overrides):
    refs = [dict(item) for item in refs]
    values = dict(
        operation_id="op_rename",
        order=0,
        depends_on=[],
        resource_type="entity_registry_rename",
        action="update",
        target_id=old,
        helper_type=None,
        proposed_config={
            "entity_id": old,
            "new_entity_id": new,
            "not_rewritten_references": refs,
            "not_rewritten_count": len(refs),
        },
        current_config={"entity_id": old},
        normalized_proposed_config={"entity_id": new},
        normalized_current_config={"entity_id": old},
        current_state_fingerprint="a" * 64,
        proposed_config_hash="b" * 64,
        normalization_version=2,
        risk=ChangeRiskAssessment(level=RiskLevel.MEDIUM),
        compensation={"prepared_operation_hash": "c" * 64},
    )
    values.update(overrides)
    return ConfigurationOperation(**values)


def rewrite_op(order, resource_type="automation", old="sensor.a", new="sensor.b", **overrides):
    proposed = {"id": f"h{order}", "alias": "a", "triggers": [{"trigger": "state", "entity_id": new}], "actions": []}
    current = {"id": f"h{order}", "alias": "a", "triggers": [{"trigger": "state", "entity_id": old}], "actions": []}
    values = dict(
        operation_id=f"op_{order}",
        order=order,
        depends_on=["op_rename"],
        resource_type=resource_type,
        action="update",
        target_id=f"h{order}",
        helper_type=None,
        proposed_config=proposed,
        current_config=current,
        normalized_proposed_config=proposed,
        normalized_current_config=current,
        current_state_fingerprint="d" * 64,
        proposed_config_hash="e" * 64,
        normalization_version=2,
        risk=ChangeRiskAssessment(level=RiskLevel.MEDIUM),
        compensation={"prepared_operation_hash": "f" * 64},
    )
    values.update(overrides)
    return ConfigurationOperation(**values)


def make_plan(operations, refs=None, mode=erp.EXECUTION_MODE_DEPENDENCY_BOUND, **overrides):
    if refs is None:
        rename = next(
            (op for op in operations if op.resource_type == "entity_registry_rename"), None
        )
        refs = (
            deepcopy(rename.proposed_config["not_rewritten_references"])
            if rename is not None
            else None
        )
    values = dict(
        plan_id="plan_rename",
        plan_version=1,
        created_at=TS,
        updated_at=TS,
        expires_at="2026-11-10T00:00:00+00:00",
        status=PlanStatus.AWAITING_APPROVAL,
        title="t",
        description="d",
        requested_by="r",
        target=ChangeTarget("configuration_plan", "plan_rename"),
        operation=ChangeOperation.CONFIGURATION_PLAN,
        proposed_config={},
        current_config=None,
        normalized_proposed_config={},
        normalized_current_config=None,
        current_state_fingerprint="a" * 64,
        proposed_config_hash="b" * 64,
        risk=ChangeRiskAssessment(level=RiskLevel.MEDIUM),
        contract_version=2,
        operations=list(operations),
        execution_mode=mode,
        not_rewritten_references=refs,
        not_rewritten_count=None if refs is None else len(refs),
    )
    values.update(overrides)
    return ChangePlan(**values)


def decide(*operations, **kwargs):
    return evaluate_change_policy(make_plan(operations, **kwargs))


class ClassificationTests(unittest.TestCase):
    def test_c1_non_actuator_with_every_reference_rewritten_is_standard(self):
        for old, new in (("sensor.a", "sensor.b"), ("input_boolean.a", "input_boolean.b")):
            decision = decide(rename_op(old, new))
            self.assertIs(decision.policy_class, ApprovalPolicyClass.STANDARD_ADMIN)
            self.assertEqual(decision.required_acknowledgements, (PLAN_APPROVAL,))
            self.assertNotIn(erp.ACTUATOR_BASIS, decision.reason_codes)
            self.assertNotIn(erp.NOT_REWRITTEN_BASIS, decision.reason_codes)

    def test_c2_n32_not_rewritten_reference_requires_the_elevated_acknowledgement(self):
        decision = decide(rename_op(refs=[DASHBOARD_REF]))
        self.assertIs(decision.policy_class, ApprovalPolicyClass.ELEVATED_ADMIN)
        self.assertIn(erp.NOT_REWRITTEN_BASIS, decision.reason_codes)
        self.assertIs(decision.risk_delta, RiskDelta.MODERATE)
        self.assertIs(decision.physical_consequence, PhysicalConsequence.NONE)
        # Plan approval alone is never enough: the existing apply gate
        # (service.py ELEVATED_RISK_ACKNOWLEDGEMENT_REQUIRED) keys on this.
        self.assertEqual(decision.required_acknowledgements, (PLAN_APPROVAL, ELEVATED_ACK))

    def test_c3_n37_every_actuator_domain_is_elevated_never_standard(self):
        self.assertEqual(len(erp.ACTUATOR_DOMAINS), 12)
        for domain in sorted(erp.ACTUATOR_DOMAINS):
            with self.subTest(domain=domain):
                decision = decide(rename_op(f"{domain}.a", f"{domain}.b"))
                self.assertIs(decision.policy_class, ApprovalPolicyClass.ELEVATED_ADMIN)
                self.assertIn(erp.ACTUATOR_BASIS, decision.reason_codes)
                self.assertIs(decision.risk_delta, RiskDelta.HIGH)
                self.assertEqual(
                    decision.physical_consequence,
                    PhysicalConsequence.SAFETY_CRITICAL
                    if domain in {"alarm_control_panel", "lock", "valve"}
                    else PhysicalConsequence.INDIRECT,
                )
                self.assertEqual(decision.required_acknowledgements, (PLAN_APPROVAL, ELEVATED_ACK))

    def test_c4_actuator_with_not_rewritten_reference_has_one_second_action(self):
        decision = decide(rename_op("light.a", "light.b", refs=[DASHBOARD_REF]))
        self.assertEqual(decision.required_acknowledgements, (PLAN_APPROVAL, ELEVATED_ACK))
        self.assertIn(erp.ACTUATOR_BASIS, decision.reason_codes)
        self.assertIn(erp.NOT_REWRITTEN_BASIS, decision.reason_codes)

    def test_c6_safety_critical_rename_with_references_is_still_actionable(self):
        refs = [dict(DASHBOARD_REF, holder_id=f"d{i}") for i in range(10)]
        decision = decide(rename_op("lock.a", "lock.b", refs=refs))
        self.assertIs(decision.policy_class, ApprovalPolicyClass.ELEVATED_ADMIN)

    def test_c8_actuator_set_is_the_risk_union(self):
        self.assertEqual(
            erp.ACTUATOR_DOMAINS,
            risk._HELPER_DIRECT_EFFECT_DOMAINS | risk._HELPER_TRANSITIVE_EFFECT_DOMAINS,
        )
        self.assertIs(erp.SAFETY_CRITICAL_DOMAINS, risk._HELPER_SAFETY_CRITICAL_DOMAINS)

    def test_dependent_rewrite_inherits_the_rename_floor(self):
        decision = decide(rename_op("light.a", "light.b"), rewrite_op(1, old="light.a", new="light.b"))
        self.assertIs(decision.policy_class, ApprovalPolicyClass.ELEVATED_ADMIN)
        self.assertIn(ELEVATED_ACK, decision.required_acknowledgements)

    def test_n35_no_exactness_shortcut_removes_the_acknowledgement(self):
        plan = make_plan([rename_op("light.a", "light.b"), rewrite_op(1, old="light.a", new="light.b")])
        patches = (
            mock.patch.object(policy_module, "_exact_existing_automation_update", return_value=True),
            mock.patch.object(policy_module, "bound_proof", return_value=object()),
            mock.patch.object(policy_module.inverse_eligibility, "bound", return_value=object()),
            mock.patch.object(policy_module, "_exact_existing_dashboard_update", return_value=True),
        )
        for patcher in patches:
            with self.subTest(branch=patcher.attribute), patcher:
                decision = evaluate_change_policy(plan)
                self.assertIn(ELEVATED_ACK, decision.required_acknowledgements)


class IncompleteShapeTests(unittest.TestCase):
    def assertProhibited(self, plan):
        decision = evaluate_change_policy(plan)
        self.assertIs(decision.policy_class, ApprovalPolicyClass.PROHIBITED)
        self.assertIn(erp.INCOMPLETE_REASON, decision.reason_codes)
        self.assertEqual(decision.required_acknowledgements, ())

    def test_the_complete_shape_is_actionable(self):
        decision = decide(rename_op(), rewrite_op(1), rewrite_op(2))
        self.assertIs(decision.policy_class, ApprovalPolicyClass.STANDARD_ADMIN)

    def test_wrong_or_missing_execution_mode(self):
        for mode in (None, erp.EXECUTION_MODE_ORDERED, "other"):
            with self.subTest(mode=mode):
                self.assertProhibited(make_plan([rename_op()], mode=mode))

    def test_execution_mode_without_a_rename_is_prohibited(self):
        self.assertProhibited(make_plan([rewrite_op(0, depends_on=[])], refs=[]))

    def test_missing_compensation_on_any_step(self):
        self.assertProhibited(make_plan([rename_op(compensation=None)]))
        self.assertProhibited(make_plan([rename_op(), rewrite_op(1, compensation=None)]))

    def test_plan_and_operation_not_rewritten_sets_must_match(self):
        self.assertProhibited(make_plan([rename_op(refs=[DASHBOARD_REF])], refs=[]))
        plan = make_plan([rename_op(refs=[DASHBOARD_REF])])
        plan.not_rewritten_count = 0
        self.assertProhibited(plan)
        op = rename_op(refs=[DASHBOARD_REF])
        op.proposed_config["not_rewritten_count"] = 2
        self.assertProhibited(make_plan([op]))

    def test_cross_domain_or_identical_or_malformed_ids(self):
        for old, new in (("sensor.a", "light.a"), ("sensor.a", "sensor.a"), ("sensor.a", "Sensor.B")):
            with self.subTest(new=new):
                self.assertProhibited(make_plan([rename_op(old, new)]))

    def test_rename_must_be_first_and_alone(self):
        self.assertProhibited(make_plan([rewrite_op(0, depends_on=[]), rename_op(order=1)]))
        second = rename_op("sensor.c", "sensor.d", operation_id="op_rename_2", order=1, depends_on=["op_rename"])
        self.assertProhibited(make_plan([rename_op(), second]))
        self.assertProhibited(make_plan([rename_op(depends_on=["x"])]))

    def test_rewrite_type_action_and_dependency(self):
        self.assertProhibited(make_plan([rename_op(), rewrite_op(1, resource_type="dashboard")]))
        # Another governed kind is refused by its own contract or by this one.
        mixed = decide(rename_op(), rewrite_op(1, resource_type="entity_registry_metadata"))
        self.assertIs(mixed.policy_class, ApprovalPolicyClass.PROHIBITED)
        self.assertProhibited(make_plan([rename_op(), rewrite_op(1, action="create")]))
        self.assertProhibited(make_plan([rename_op(), rewrite_op(1, depends_on=[])]))

    def test_more_than_eight_operations(self):
        self.assertIsNotNone(decide(rename_op(), *[rewrite_op(i) for i in range(1, 8)]))
        self.assertIs(
            decide(rename_op(), *[rewrite_op(i) for i in range(1, 8)]).policy_class,
            ApprovalPolicyClass.STANDARD_ADMIN,
        )
        self.assertProhibited(make_plan([rename_op(), *[rewrite_op(i) for i in range(1, 9)]]))

    def test_risk_assessment_must_allow_apply(self):
        blocked = ChangeRiskAssessment(level=RiskLevel.MEDIUM, apply_allowed=False)
        self.assertProhibited(make_plan([rename_op(risk=blocked)]))

    def test_caller_execution_mode_is_refused(self):
        self.assertEqual(
            erp.caller_execution_mode_refusal({"execution_mode": erp.EXECUTION_MODE_DEPENDENCY_BOUND}),
            erp.EXECUTION_MODE_NOT_CALLER_SELECTABLE,
        )
        self.assertIsNone(erp.caller_execution_mode_refusal({"entity_id": "sensor.a"}))


class SceneRewriteTests(unittest.TestCase):
    """HAMCP-071 keeps a scene update alone except as a rename rewrite."""

    def test_scene_rewrite_beside_a_rename_is_not_refused_as_mixed(self):
        standard = policy_module.OperationPolicyClassification(
            ApprovalPolicyClass.STANDARD_ADMIN, RiskDelta.LOW, PhysicalConsequence.NONE, ("scene_ok",)
        )
        with mock.patch.object(policy_module, "_scene_configuration_operation_policy", return_value=standard):
            renamed = decide(rename_op(), rewrite_op(1, resource_type="scene_configuration"))
            self.assertIs(renamed.policy_class, ApprovalPolicyClass.STANDARD_ADMIN)
            mixed = evaluate_change_policy(
                make_plan(
                    [rewrite_op(0, depends_on=[]), rewrite_op(1, resource_type="scene_configuration", depends_on=[])],
                    mode=None,
                    refs=None,
                )
            )
            self.assertIs(mixed.policy_class, ApprovalPolicyClass.PROHIBITED)

    def test_scene_rewrite_still_needs_its_own_complete_contract(self):
        decision = decide(rename_op(), rewrite_op(1, resource_type="scene_configuration"))
        self.assertIs(decision.policy_class, ApprovalPolicyClass.PROHIBITED)


class PersistenceTests(unittest.TestCase):
    def legacy_plan(self):
        plan = make_plan([rewrite_op(0, depends_on=[], compensation=None)], mode=None, refs=None)
        return plan

    def test_policy_subject_is_unchanged_for_plans_without_the_mode(self):
        plan = self.legacy_plan()
        self.assertEqual(erp.policy_subject_extension(plan), {})
        self.assertNotIn("execution_mode", policy_subject_payload(plan))

    def test_c9_n36_rename_keys_are_bound_into_the_policy_subject(self):
        refs = [dict(DASHBOARD_REF, holder_id=f"d{i}") for i in range(10)]
        plan = make_plan([rename_op(refs=refs)])
        subject = policy_subject_payload(plan)
        self.assertEqual(subject["not_rewritten_count"], 10)
        self.assertEqual(subject["execution_mode"], erp.EXECUTION_MODE_DEPENDENCY_BOUND)
        baseline = evaluate_change_policy(plan).policy_subject_hash
        altered = deepcopy(plan)
        altered.not_rewritten_references = altered.not_rewritten_references[:-1]
        altered.operations[0].proposed_config["not_rewritten_references"] = altered.not_rewritten_references
        altered.operations[0].proposed_config["not_rewritten_count"] = 9
        altered.not_rewritten_count = 9
        self.assertNotEqual(evaluate_change_policy(altered).policy_subject_hash, baseline)
        changed = deepcopy(plan)
        changed.operations[0].compensation = {"prepared_operation_hash": "0" * 64}
        self.assertNotEqual(evaluate_change_policy(changed).policy_subject_hash, baseline)

    def test_existing_record_shape_has_no_new_keys(self):
        record = self.legacy_plan().to_dict()
        for key in ("execution_mode", "not_rewritten_references", "not_rewritten_count"):
            self.assertNotIn(key, record)
        self.assertNotIn("compensation", record["operations"][0])
        self.assertEqual(_canonical(ChangePlan.from_dict(_canonical(record)).to_dict()), _canonical(record))

    def test_new_record_round_trips(self):
        record = make_plan([rename_op(refs=[DASHBOARD_REF]), rewrite_op(1)]).to_dict()
        self.assertEqual(record["execution_mode"], erp.EXECUTION_MODE_DEPENDENCY_BOUND)
        self.assertEqual(_canonical(ChangePlan.from_dict(_canonical(record)).to_dict()), _canonical(record))


def _canonical(record):
    return json.loads(json.dumps(record, sort_keys=True))


def _git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _load_shipped_models():
    provenance = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))
    name = "shipped_v2_4_2_beta7_models.py.txt"
    data = (FIXTURES / name).read_bytes()
    if _git_blob_sha1(data) != provenance["files"][name]["git_blob_sha1"]:
        raise AssertionError("shipped reader fixture does not match its provenance")
    spec = importlib.util.spec_from_loader("hamcp109_shipped_governance_models", loader=None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(data, str(FIXTURES / name), "exec"), module.__dict__)
    return module, provenance


class DowngradeTests(unittest.TestCase):
    """N20 with the exact shipped v2.4.2-beta.7 reader (root AGENTS.md)."""

    @classmethod
    def setUpClass(cls):
        cls.shipped, cls.provenance = _load_shipped_models()

    def test_shipped_reader_refuses_a_dependency_bound_plan(self):
        record = make_plan([rename_op(refs=[DASHBOARD_REF]), rewrite_op(1)]).to_dict()
        with self.assertRaises(TypeError):
            self.shipped.ChangePlan.from_dict(deepcopy(record))
        # Each discriminator key alone is enough for the refusal.
        for key in ("execution_mode", "not_rewritten_references", "not_rewritten_count"):
            with self.subTest(key=key):
                legacy = self.legacy_record()
                legacy[key] = record[key]
                with self.assertRaises(TypeError):
                    self.shipped.ChangePlan.from_dict(legacy)
        operation = self.legacy_record()["operations"][0]
        operation["compensation"] = {"prepared_operation_hash": "c" * 64}
        with self.assertRaises(TypeError):
            self.shipped.ConfigurationOperation.from_dict(operation)

    def legacy_record(self):
        return make_plan([rewrite_op(0, depends_on=[], compensation=None)], mode=None, refs=None).to_dict()

    def test_shipped_writer_records_load_unchanged_in_the_new_reader(self):
        shipped_plan = self.shipped.ChangePlan.from_dict(self.legacy_record())
        written = shipped_plan.to_dict()
        self.assertEqual(
            _canonical(ChangePlan.from_dict(_canonical(written)).to_dict()), _canonical(written)
        )
        self.assertIsNone(ChangePlan.from_dict(deepcopy(written)).execution_mode)

    def test_task_reader_ignores_extra_keys_so_the_plan_record_is_the_binding(self):
        # The task reader is unchanged from the shipped release ...
        source = (BETA_DIR / "ha_mcp_engineering" / "governance" / "task_models.py").read_bytes()
        expected = self.provenance["unchanged_readers"][
            "hass_mcp_engineering_beta/ha_mcp_engineering/governance/task_models.py"
        ]
        self.assertEqual(_git_blob_sha1(source), expected)
        # ... and silently drops an unknown key, so a task-level marker can
        # never be the only refusal point (design 8, handoff caveat).
        task = new_execution_task(
            task_id="task_1", plan_id="plan_rename", plan_hash="a" * 64,
            operation="configuration_plan", target={}, timestamp=TS,
            execution_request_id="req_1", idempotency_key="k" * 32,
            approval_reference={}, legacy_projection={},
        )
        record = task.to_dict()
        record["execution_mode"] = erp.EXECUTION_MODE_DEPENDENCY_BOUND
        self.assertNotIn("execution_mode", ExecutionTask.from_dict(record).to_dict())


class SignedSiblingTests(unittest.TestCase):
    def test_n31_metadata_profile_fingerprint_is_unchanged(self):
        (profile,) = CORE_ENTITY_METADATA_PROFILES
        self.assertEqual(
            profile.contract_fingerprint,
            "sha256:cc09630efbc0e1e6d0a587696406f6502c62fe730a767bc0a8cfc96a9988f767",
        )


if __name__ == "__main__":
    unittest.main()
