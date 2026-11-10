"""HAMCP-109 dependency-bound compensating execution (design section 3.2).

Every ownership class is derived from a genuine ``DurableExecutionRecord``
written through ``DurableExecutionRepository``; no record is hand-built.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.f3.contracts import (  # noqa: E402
    F3_ADAPTER_CONTRACT_MODEL,
    OperationTarget,
    PreparedOperation,
)
from ha_mcp_engineering.f3.models import (  # noqa: E402
    ExecutionIdentity,
    ExecutorTiming,
    LockHandle,
    LockOwner,
    LockTiming,
    LockToken,
)
from ha_mcp_engineering.f3.persistence import DurableExecutionRepository  # noqa: E402
from ha_mcp_engineering.f3_runtime import dependency_bound as db  # noqa: E402


TIMING = ExecutorTiming(120, 60, 3, 3)
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
PLAN = db.PlanBinding(
    plan_id="plan-rename",
    plan_hash="1" * 64,
    public_task_id="task-rename",
    approval_bundle_hash="2" * 64,
)


def _step(ordinal: int, operation_id: str, target_type: str, target_id: str, deps=()):
    return db.StepContract(
        operation_id=operation_id,
        ordinal=ordinal,
        dependency_ids=tuple(deps),
        target_type=target_type,
        target_id=target_id,
        capability_id=f"cap_{target_type}",
        prepared_operation_hash=f"{ordinal + 3:x}" * 64,
        provider_arguments_hash=f"{ordinal + 7:x}" * 64,
        planned_result_fingerprint=f"{ordinal + 10:x}" * 64,
        compensation_prepared_operation_hash=f"{ordinal + 1:x}" * 32 + "0" * 32,
    )


RENAME = _step(0, "op_rename", "entity_registry_rename", "light.kitchen")
AUTOMATION = _step(1, "op_automation", "automation", "kitchen_motion", ("op_rename",))
SCRIPT = _step(2, "op_script", "script", "kitchen_scene", ("op_rename",))
STEPS = (RENAME, AUTOMATION, SCRIPT)


class ChildFactory:
    """Writes one real child execution record per step and kind."""

    def __init__(self, directory: str) -> None:
        self.repository = DurableExecutionRepository(directory)

    @staticmethod
    def declaration(step: db.StepContract) -> dict:
        value = {
            "child_id": f"child-{step.operation_id}",
            "public_task_id": PLAN.public_task_id,
            "plan_id": PLAN.plan_id,
            "plan_hash": PLAN.plan_hash,
            "operation_id": step.operation_id,
            "operation_ordinal": step.ordinal,
            "operation_dependency_ids": list(step.dependency_ids),
            "capability_id": step.capability_id,
            "prepared_operation_hash": step.prepared_operation_hash,
            "target_type": step.target_type,
            "target_id": step.target_id,
            "attempt_id": f"attempt-{step.operation_id}",
            "request_id": f"request-{step.operation_id}",
            "approval_bundle_hash": PLAN.approval_bundle_hash,
        }
        value["declaration_hash"] = db.canonical_hash(value)
        return value

    def write(self, step: db.StepContract, kind: str, *, fingerprint: str | None = None):
        declaration = self.declaration(step)
        task_id = declaration["child_id"]
        owner = "owner-rename"
        prepared = PreparedOperation(
            contract_model=F3_ADAPTER_CONTRACT_MODEL,
            adapter_id=f"adapter_{step.target_type}",
            operation="update",
            target=OperationTarget(step.target_type, step.target_id),
            current_state_fingerprint="a" * 64,
            normalized_proposed_hash="b" * 64,
            prepared_operation_hash=step.prepared_operation_hash,
            risk_level="high",
            policy_decision_hash="d" * 64,
            approval_bundle_hash=PLAN.approval_bundle_hash,
            expected_effects=("configuration_changed",),
            verification_contract_model="exact_readback_v1",
            verification_contract_hash="f" * 64,
            rollback_available=True,
        )
        claim = self.repository.claim(
            identity=ExecutionIdentity(
                task_id=task_id,
                plan_id=PLAN.plan_id,
                attempt_id=declaration["attempt_id"],
                request_id=declaration["request_id"],
                owner_id=owner,
            ),
            prepared=prepared,
            timing=TIMING,
            now=NOW,
        )
        fenced = {"owner_id": owner, "claim_generation": claim.claim_generation}
        if kind == "pre_intent":
            return declaration, self.repository.get(task_id)
        self.repository.record_locks(
            task_id,
            handle=LockHandle(
                owner=LockOwner(owner, task_id, PLAN.plan_id, step.operation_id, declaration["attempt_id"]),
                tokens=(LockToken(f"{step.target_type}:{step.target_id}", 1, "exclusive"),),
                acquired_at=NOW.isoformat(),
                lease_expires_at=(NOW + timedelta(seconds=120)).isoformat(),
                timing=LockTiming(120, 20, 0, 0.05),
            ),
            now=NOW,
            **fenced,
        )
        if kind == "refused":
            self.repository.terminalize_pre_dispatch(task_id, outcome="preflight_rejected", now=NOW, **fenced)
            return declaration, self.repository.get(task_id)
        if kind == "noop":
            self.repository.terminalize_verified_no_dispatch(
                task_id,
                resulting_state_fingerprint=fingerprint or step.planned_result_fingerprint,
                evidence_hash="e" * 64,
                now=NOW,
                **fenced,
            )
            return declaration, self.repository.get(task_id)
        self.repository.record_preflight(task_id, now=NOW, **fenced)
        self.repository.commit_dispatch_intent(
            task_id,
            request_id=declaration["request_id"],
            provider_operation="update",
            provider_arguments_hash=step.provider_arguments_hash,
            timing=TIMING,
            now=NOW,
            **fenced,
        )
        if kind == "owned":
            self.repository.record_verification(
                task_id,
                outcome="succeeded_verified",
                terminal=True,
                evidence={
                    "evidence_hash": "e" * 64,
                    "resulting_state_fingerprint": fingerprint or step.planned_result_fingerprint,
                },
                now=NOW,
                **fenced,
            )
        elif kind == "dispatch_failed":
            self.repository.record_dispatch_result(
                task_id, outcome="dispatch_failed_confirmed", provider_response_received=True, now=NOW, **fenced
            )
        elif kind == "dispatch_indeterminate":
            self.repository.record_dispatch_result(
                task_id, outcome="dispatch_indeterminate", provider_response_received=False, now=NOW, **fenced
            )
        elif kind == "verification_failed":
            self.repository.record_verification(
                task_id, outcome="failed_post_dispatch", terminal=True, now=NOW, **fenced
            )
        elif kind != "intent_only":
            raise AssertionError(kind)
        return declaration, self.repository.get(task_id)


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.factory = ChildFactory(temporary.name)

    def classify(self, step, kind, readback=None, **kwargs):
        declaration, record = self.factory.write(step, kind, **kwargs)
        return db.classify_child(
            plan=PLAN, step=step, declaration=declaration, record=record, readback=readback
        )


class OwnershipClassTests(_Base):
    def test_dispatched_and_verified_child_is_owned(self):
        child = self.classify(RENAME, "owned")
        self.assertEqual(child.ownership, db.OWNED_VERIFIED)
        self.assertTrue(child.dispatched)
        self.assertTrue(child.changed_by_engineering)
        self.assertEqual(len(child.record_hash), 64)

    def test_verified_noop_is_already_present_and_never_owned(self):
        child = self.classify(AUTOMATION, "noop")
        self.assertEqual(child.ownership, db.ALREADY_PRESENT_VERIFIED)
        self.assertFalse(child.dispatched)
        self.assertFalse(child.changed_by_engineering)

    def test_noop_with_a_different_result_is_indeterminate(self):
        child = self.classify(AUTOMATION, "noop", fingerprint="9" * 64)
        self.assertEqual(child.ownership, db.INDETERMINATE)

    def test_refused_before_intent_is_not_applied(self):
        child = self.classify(AUTOMATION, "refused")
        self.assertEqual(
            (child.ownership, child.dispatched), (db.NOT_APPLIED_DETERMINATE, False)
        )

    def test_recovered_pre_intent_never_creates_write_ownership(self):
        present = self.classify(
            AUTOMATION, "pre_intent", readback=db.Readback(forward_result_present=True)
        )
        self.assertEqual(present.ownership, db.ALREADY_PRESENT_VERIFIED)
        self.assertFalse(present.changed_by_engineering)

    def test_recovered_pre_intent_without_result_is_not_applied(self):
        child = self.classify(SCRIPT, "pre_intent")
        self.assertEqual(child.ownership, db.NOT_APPLIED_DETERMINATE)

    def test_dispatch_failed_needs_proven_not_applied_readback(self):
        unproven = self.classify(AUTOMATION, "dispatch_failed")
        self.assertEqual(unproven.ownership, db.INDETERMINATE)

    def test_dispatch_failed_with_proven_not_applied_is_determinate(self):
        child = self.classify(
            AUTOMATION, "dispatch_failed", readback=db.Readback(not_applied_proven=True)
        )
        self.assertEqual((child.ownership, child.dispatched), (db.NOT_APPLIED_DETERMINATE, True))

    def test_dispatch_failed_contradicted_by_forward_result_is_indeterminate(self):
        child = self.classify(
            AUTOMATION,
            "dispatch_failed",
            readback=db.Readback(not_applied_proven=True, forward_result_present=True),
        )
        self.assertEqual(child.ownership, db.INDETERMINATE)

    def test_unknown_post_intent_outcomes_are_indeterminate(self):
        for kind in ("intent_only", "dispatch_indeterminate", "verification_failed"):
            with self.subTest(kind=kind):
                self.setUp()
                child = self.classify(SCRIPT, kind, readback=db.Readback(not_applied_proven=True))
                self.assertEqual(child.ownership, db.INDETERMINATE)
                self.assertTrue(child.dispatched)

    def test_owned_with_a_different_verified_result_is_indeterminate(self):
        child = self.classify(RENAME, "owned", fingerprint="9" * 64)
        self.assertEqual(child.ownership, db.INDETERMINATE)

    def test_no_record_is_not_attempted(self):
        child = db.classify_child(
            plan=PLAN, step=SCRIPT, declaration=ChildFactory.declaration(SCRIPT), record=None
        )
        self.assertEqual(child.ownership, db.NOT_ATTEMPTED)


class ForgedRecordTests(_Base):
    """A contradiction never grants compensation authority."""

    def test_declaration_for_another_plan_step_or_hash_is_indeterminate(self):
        declaration, record = self.factory.write(RENAME, "owned")
        forged = (
            ("plan_hash", "9" * 64),
            ("approval_bundle_hash", "9" * 64),
            ("operation_ordinal", 4),
            ("operation_dependency_ids", ["op_other"]),
            ("target_id", "light.other"),
            ("capability_id", "cap_other"),
            ("prepared_operation_hash", "9" * 64),
        )
        for key, value in forged:
            with self.subTest(key=key):
                changed = dict(declaration, **{key: value})
                changed["declaration_hash"] = db.canonical_hash(
                    {k: v for k, v in changed.items() if k != "declaration_hash"}
                )
                child = db.classify_child(plan=PLAN, step=RENAME, declaration=changed, record=record)
                self.assertEqual(child.ownership, db.INDETERMINATE)
                self.assertEqual(child.reason, "declaration_not_bound_to_plan")

    def test_tampered_declaration_hash_is_indeterminate(self):
        declaration, record = self.factory.write(RENAME, "owned")
        declaration["request_id"] = "request-other"
        child = db.classify_child(plan=PLAN, step=RENAME, declaration=declaration, record=record)
        self.assertEqual(child.ownership, db.INDETERMINATE)

    def test_record_of_another_child_is_indeterminate(self):
        _, other_record = self.factory.write(AUTOMATION, "owned")
        child = db.classify_child(
            plan=PLAN,
            step=RENAME,
            declaration=ChildFactory.declaration(RENAME),
            record=other_record,
        )
        self.assertEqual((child.ownership, child.reason), (db.INDETERMINATE, "record_identity_mismatch"))

    def test_mutated_record_that_fails_validation_is_indeterminate(self):
        declaration, record = self.factory.write(RENAME, "owned")
        forged = copy.deepcopy(record)
        forged.dispatch_count = 2
        child = db.classify_child(plan=PLAN, step=RENAME, declaration=declaration, record=forged)
        self.assertEqual(child.ownership, db.INDETERMINATE)

    def test_provider_arguments_mismatch_is_indeterminate(self):
        declaration, record = self.factory.write(RENAME, "owned")
        step = replace(RENAME, provider_arguments_hash="9" * 64)
        child = db.classify_child(plan=PLAN, step=step, declaration=declaration, record=record)
        self.assertEqual((child.ownership, child.reason), (db.INDETERMINATE, "provider_arguments_mismatch"))

    def test_classification_never_reads_parent_projections(self):
        declaration, record = self.factory.write(RENAME, "refused")
        declaration = dict(declaration, completed_operation_ids=["op_rename"], terminal_outcome="succeeded_verified")
        declaration["declaration_hash"] = db.canonical_hash(
            {k: v for k, v in declaration.items() if k != "declaration_hash"}
        )
        child = db.classify_child(plan=PLAN, step=RENAME, declaration=declaration, record=record)
        self.assertEqual(child.ownership, db.NOT_APPLIED_DETERMINATE)


def _class(step, ownership, *, dispatched=None):
    return db.ChildClass(
        step.operation_id,
        ownership,
        "synthetic",
        child_id=f"child-{step.operation_id}",
        record_hash="5" * 64,
        dispatched=(ownership == db.OWNED_VERIFIED) if dispatched is None else dispatched,
    )


class CompensationRuleTests(unittest.TestCase):
    def test_all_owned_succeeds_and_any_owned_reports_applied(self):
        result = db.assess_forward([_class(s, db.OWNED_VERIFIED) for s in STEPS])
        self.assertEqual(result.outcome, db.OUTCOME_SUCCEEDED)
        projection = db.terminal_projection(result.outcome, result.classes)
        self.assertEqual(projection["status"], "applied")

    def test_all_noop_is_already_present_not_applied(self):
        classes = [_class(s, db.ALREADY_PRESENT_VERIFIED) for s in STEPS]
        result = db.assess_forward(classes)
        self.assertEqual(result.outcome, db.OUTCOME_ALREADY_PRESENT)
        projection = db.terminal_projection(result.outcome, classes)
        self.assertEqual(projection["status"], "already_present")
        self.assertEqual(projection["owned_operation_ids"], [])

    def test_in_progress_until_every_step_is_determinate(self):
        classes = [_class(RENAME, db.OWNED_VERIFIED), _class(AUTOMATION, db.NOT_ATTEMPTED)]
        self.assertIsNone(db.assess_forward(classes).outcome)

    def test_any_indeterminate_holds_and_never_compensates(self):
        classes = [
            _class(RENAME, db.OWNED_VERIFIED),
            _class(AUTOMATION, db.NOT_APPLIED_DETERMINATE),
            _class(SCRIPT, db.INDETERMINATE),
        ]
        result = db.assess_forward(classes)
        self.assertEqual(result.outcome, db.OUTCOME_HELD_INDETERMINATE)
        self.assertEqual(result.compensation_steps, [])
        with self.assertRaises(ValueError):
            db.build_compensation_decision(
                plan=PLAN, sequence_hash="6" * 64, assessment=result, compensation_hashes={}, reason="x"
            )
        projection = db.terminal_projection(result.outcome, classes)
        self.assertEqual(projection["task_state"], db.TASK_STATE_MANUAL_REVIEW)
        self.assertEqual(projection["unresolved_operation_ids"], ["op_script"])

    def test_failure_with_no_owned_step_is_failed_no_writes(self):
        classes = [_class(RENAME, db.NOT_APPLIED_DETERMINATE), _class(AUTOMATION, db.NOT_ATTEMPTED)]
        result = db.assess_forward(classes)
        self.assertEqual(result.outcome, db.OUTCOME_FAILED_NO_WRITES)
        projection = db.terminal_projection(result.outcome, classes)
        self.assertEqual(projection["task_state"], db.TASK_STATE_FAILED_PRE_DISPATCH)
        dispatched = [_class(RENAME, db.NOT_APPLIED_DETERMINATE, dispatched=True)]
        self.assertEqual(
            db.terminal_projection(db.OUTCOME_FAILED_NO_WRITES, dispatched)["task_state"],
            db.TASK_STATE_FAILED_POST_DISPATCH,
        )

    def test_failure_after_owned_steps_compensates_in_reverse_excluding_noops(self):
        classes = [
            _class(RENAME, db.OWNED_VERIFIED),
            _class(AUTOMATION, db.ALREADY_PRESENT_VERIFIED),
            _class(SCRIPT, db.NOT_APPLIED_DETERMINATE),
        ]
        result = db.assess_forward(classes)
        self.assertEqual(result.outcome, db.COMPENSATE)
        self.assertEqual([c.operation_id for c in result.compensation_steps], ["op_rename"])
        self.assertEqual([c.operation_id for c in result.excluded], ["op_automation"])

        classes = [
            _class(RENAME, db.OWNED_VERIFIED),
            _class(AUTOMATION, db.OWNED_VERIFIED),
            _class(SCRIPT, db.NOT_APPLIED_DETERMINATE),
        ]
        result = db.assess_forward(classes)
        self.assertEqual(
            [c.operation_id for c in result.compensation_steps], ["op_automation", "op_rename"]
        )

    def test_dependencies_require_owned_or_already_present(self):
        for ownership, expected in (
            (db.OWNED_VERIFIED, True),
            (db.ALREADY_PRESENT_VERIFIED, True),
            (db.NOT_APPLIED_DETERMINATE, False),
            (db.INDETERMINATE, False),
            (db.NOT_ATTEMPTED, False),
        ):
            with self.subTest(ownership=ownership):
                self.assertEqual(
                    db.dependencies_satisfied(AUTOMATION, {"op_rename": _class(RENAME, ownership)}),
                    expected,
                )
        self.assertFalse(db.dependencies_satisfied(AUTOMATION, {}))


def _decision(classes=None):
    classes = classes or [
        _class(RENAME, db.OWNED_VERIFIED),
        _class(AUTOMATION, db.OWNED_VERIFIED),
        _class(SCRIPT, db.NOT_APPLIED_DETERMINATE),
    ]
    assessment = db.assess_forward(classes)
    return assessment, db.build_compensation_decision(
        plan=PLAN,
        sequence_hash="6" * 64,
        assessment=assessment,
        compensation_hashes={s.operation_id: s.compensation_prepared_operation_hash for s in STEPS},
        reason="forward_step_not_applied",
    )


class Recorder:
    def __init__(self, *, locks=True, drift_at=None, child_result=None, existing=None):
        self.calls: list[tuple[str, str | None]] = []
        self.locks = locks
        self.drift_at = drift_at
        self.child_result = child_result or {}
        self.existing = existing or {}
        self.persisted = None

    def hooks(self) -> db.CompensationHooks:
        def persist(decision):
            self.calls.append(("persist", None))
            self.persisted = decision

        def transfer():
            self.calls.append(("locks", None))
            return self.locks

        def bundle(entry):
            self.calls.append(("bundle", entry["operation_id"]))
            return entry["operation_id"] != self.drift_at

        def run(entry):
            self.calls.append(("run", entry["operation_id"]))
            ownership = self.child_result.get(entry["operation_id"], db.OWNED_VERIFIED)
            return db.ChildClass(entry["operation_id"], ownership, "compensation")

        def existing(entry):
            ownership = self.existing.get(entry["operation_id"])
            return None if ownership is None else db.ChildClass(entry["operation_id"], ownership, "recorded")

        return db.CompensationHooks(persist, transfer, bundle, run, existing)


class CompensationSequencerTests(unittest.TestCase):
    def test_decision_binds_owned_set_and_record_hashes(self):
        _, decision = _decision()
        self.assertEqual(decision["model"], db.DECISION_MODEL)
        self.assertEqual(
            [e["operation_id"] for e in decision["compensation_set"]], ["op_automation", "op_rename"]
        )
        self.assertEqual(decision["failed_operation_id"], "op_script")
        base = {k: v for k, v in decision.items() if k != "decision_hash"}
        self.assertEqual(decision["decision_hash"], db.canonical_hash(base))

    def test_missing_precomputed_compensation_refuses_decision(self):
        assessment, _ = _decision()
        with self.assertRaises(ValueError):
            db.build_compensation_decision(
                plan=PLAN, sequence_hash="6" * 64, assessment=assessment,
                compensation_hashes={"op_rename": "7" * 64}, reason="x",
            )

    def test_decision_is_persisted_before_any_compensation_child(self):
        _, decision = _decision()
        recorder = Recorder()
        result = db.run_compensation(decision, recorder.hooks())
        self.assertEqual(recorder.calls[0], ("persist", None))
        self.assertEqual(recorder.calls[1], ("locks", None))
        self.assertEqual(
            recorder.calls[2:],
            [("bundle", "op_automation"), ("run", "op_automation"), ("bundle", "op_rename"), ("run", "op_rename")],
        )
        self.assertEqual(result.outcome, db.OUTCOME_COMPENSATED)
        self.assertEqual(result.compensated, ["op_automation", "op_rename"])
        self.assertEqual(result.child_runs, 2)

    def test_lock_loss_stops_before_any_child(self):
        _, decision = _decision()
        recorder = Recorder(locks=False)
        result = db.run_compensation(decision, recorder.hooks())
        self.assertEqual((result.outcome, result.stop_reason), (db.OUTCOME_COMPENSATION_INCOMPLETE, db.COMPENSATION_LOCKS_LOST))
        self.assertNotIn("run", [c[0] for c in recorder.calls])
        self.assertEqual(result.child_runs, 0)

    def test_drift_stops_and_reports_remaining_steps(self):
        _, decision = _decision()
        recorder = Recorder(drift_at="op_rename")
        result = db.run_compensation(decision, recorder.hooks())
        self.assertEqual(result.stop_reason, db.COMPENSATION_DRIFT)
        self.assertEqual(result.compensated, ["op_automation"])
        self.assertEqual(
            result.step_states,
            [
                {"operation_id": "op_automation", "state": db.OWNED_VERIFIED},
                {"operation_id": "op_rename", "state": db.NOT_ATTEMPTED},
            ],
        )
        self.assertNotIn(("run", "op_rename"), recorder.calls)

    def test_failed_or_unknown_compensation_child_is_one_attempt_and_stops(self):
        for ownership, reason in (
            (db.NOT_APPLIED_DETERMINATE, db.COMPENSATION_STEP_FAILED),
            (db.INDETERMINATE, db.COMPENSATION_STEP_INDETERMINATE),
        ):
            with self.subTest(ownership=ownership):
                _, decision = _decision()
                recorder = Recorder(child_result={"op_automation": ownership})
                result = db.run_compensation(decision, recorder.hooks())
                self.assertEqual(result.stop_reason, reason)
                self.assertEqual(result.child_runs, 1)
                self.assertEqual([c for c in recorder.calls if c[0] == "run"], [("run", "op_automation")])

    def test_reentry_never_resends_a_recorded_child(self):
        _, decision = _decision()
        recorder = Recorder(existing={"op_automation": db.OWNED_VERIFIED})
        result = db.run_compensation(decision, recorder.hooks(), persisted=copy.deepcopy(decision))
        self.assertNotIn(("persist", None), recorder.calls)
        self.assertEqual([c for c in recorder.calls if c[0] == "run"], [("run", "op_rename")])
        self.assertEqual(result.outcome, db.OUTCOME_COMPENSATED)

    def test_reentry_on_unresolved_recorded_child_stops_without_resend(self):
        _, decision = _decision()
        recorder = Recorder(existing={"op_automation": db.INDETERMINATE})
        result = db.run_compensation(decision, recorder.hooks(), persisted=copy.deepcopy(decision))
        self.assertEqual(result.stop_reason, db.COMPENSATION_STEP_INDETERMINATE)
        self.assertEqual(result.child_runs, 0)

    def test_persisted_decision_is_never_recomputed_with_a_different_set(self):
        _, decision = _decision()
        _, other = _decision(
            [
                _class(RENAME, db.OWNED_VERIFIED),
                _class(AUTOMATION, db.ALREADY_PRESENT_VERIFIED),
                _class(SCRIPT, db.NOT_APPLIED_DETERMINATE),
            ]
        )
        recorder = Recorder()
        with self.assertRaises(db.CompensationDecisionMismatchError):
            db.run_compensation(other, recorder.hooks(), persisted=decision)
        tampered = dict(decision, reason="other")
        with self.assertRaises(db.CompensationDecisionMismatchError):
            db.verify_decision_unchanged(tampered, tampered)
        self.assertEqual(recorder.calls, [])


class TerminalProjectionTests(unittest.TestCase):
    def test_partial_failure_preserves_and_reports_every_step(self):
        classes = [
            _class(RENAME, db.OWNED_VERIFIED),
            _class(AUTOMATION, db.ALREADY_PRESENT_VERIFIED),
            _class(SCRIPT, db.NOT_APPLIED_DETERMINATE),
        ]
        projection = db.terminal_projection(
            db.OUTCOME_COMPENSATION_INCOMPLETE,
            classes,
            compensated=[],
            stop_reason=db.COMPENSATION_DRIFT,
        )
        self.assertEqual(projection["task_state"], db.TASK_STATE_MANUAL_REVIEW)
        self.assertEqual(projection["status"], "manual_review_required")
        self.assertEqual(projection["owned_operation_ids"], ["op_rename"])
        self.assertEqual(projection["noop_operation_ids"], ["op_automation"])
        self.assertEqual(projection["failed_operation_ids"], ["op_script"])
        self.assertEqual(projection["compensated_operation_ids"], [])
        self.assertEqual(projection["stop_reason"], db.COMPENSATION_DRIFT)
        self.assertEqual(
            projection["excluded_from_compensation"],
            [{"operation_id": "op_automation", "reason": db.EXCLUDED_NOT_DISPATCHED}],
        )
        self.assertEqual(
            [(s["operation_id"], s["changed_by_engineering"]) for s in projection["steps"]],
            [("op_rename", True), ("op_automation", False), ("op_script", False)],
        )

    def test_compensated_outcome_is_failed_post_dispatch(self):
        classes = [_class(RENAME, db.OWNED_VERIFIED), _class(SCRIPT, db.NOT_APPLIED_DETERMINATE)]
        projection = db.terminal_projection(db.OUTCOME_COMPENSATED, classes, compensated=["op_rename"])
        self.assertEqual(
            (projection["task_state"], projection["status"]), (db.TASK_STATE_FAILED_POST_DISPATCH, "failed")
        )
        self.assertEqual(projection["compensated_operation_ids"], ["op_rename"])

    def test_unknown_outcome_is_refused(self):
        with self.assertRaises(ValueError):
            db.terminal_projection("partially_applied", [])


class EndToEndRecordTests(_Base):
    """Real records drive the rule: rename owned, rewrite refused -> compensate."""

    def test_real_records_produce_decision_over_owned_rename_only(self):
        classes = [
            self.classify(RENAME, "owned"),
            self.classify(AUTOMATION, "noop"),
            self.classify(SCRIPT, "refused"),
        ]
        assessment = db.assess_forward(classes)
        self.assertEqual(assessment.outcome, db.COMPENSATE)
        decision = db.build_compensation_decision(
            plan=PLAN,
            sequence_hash="6" * 64,
            assessment=assessment,
            compensation_hashes={RENAME.operation_id: RENAME.compensation_prepared_operation_hash},
            reason="forward_step_not_applied",
        )
        self.assertEqual(
            decision["compensation_set"][0]["forward_record_hash"], classes[0].record_hash
        )
        self.assertEqual(
            decision["excluded_from_compensation"],
            [{"operation_id": "op_automation", "reason": db.EXCLUDED_NOT_DISPATCHED}],
        )

    def test_real_unknown_dispatch_holds_the_whole_plan(self):
        classes = [self.classify(RENAME, "owned"), self.classify(AUTOMATION, "dispatch_indeterminate")]
        self.assertEqual(db.assess_forward(classes).outcome, db.OUTCOME_HELD_INDETERMINATE)


if __name__ == "__main__":
    unittest.main()
