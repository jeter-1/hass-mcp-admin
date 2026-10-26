"""HAMCP-181 PR1: unit tests for the shared dispatch-facts projection.

Records are synthetic duck-typed stand-ins for F3 execution records; the
projection reads only the attributes listed in ``FakeRecord``.  Every row of
the design section 2.1 parent truth table is asserted, plus the single (b)
predicate controls, the P1/P3 (r-proven) positives and negatives, and the
section 2.2 field sets.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from ha_mcp_engineering.f3_runtime import dispatch_facts as df  # noqa: E402

INTENT_AT = "2026-10-14T12:00:00+00:00"
RESPONSE_AT = "2026-10-14T12:00:02+00:00"


@dataclass
class FakeRecord:
    operation: str = "update_configuration"
    target_type: str = "automation"
    intent: bool = True
    response: bool = False
    outcome: str | None = None
    codes: tuple[str, ...] = ()
    noop: bool = False
    unreadable: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        self.target = {"target_type": self.target_type, "target_id": "x"}
        self.dispatch_intent = {"committed_at": INTENT_AT} if self.intent else None
        self.dispatch_count = 1 if self.intent else 0
        self.provider_response_received = self.response
        self.normalized_outcome = self.outcome
        if self.noop:
            self.events.append({"event_type": "preflight_noop_verified"})
        if self.outcome is not None:
            self.events.append({
                "event_type": df.DISPATCH_RESULT_EVENT,
                "occurred_at": RESPONSE_AT,
                "diagnostic_codes": list(self.codes),
            })

    def execution_class(self):
        if self.unreadable:
            raise ValueError("contradictory record")
        if self.noop:
            return "verified_no_dispatch"
        return "other"


def sent_verified():
    return FakeRecord(response=True, outcome="succeeded_verified")


def sent_unverified():
    return FakeRecord(response=True, outcome="verification_mismatch")


def uncertain():
    return FakeRecord(outcome="dispatch_indeterminate", codes=("provider_result_uncertain",))


def not_sent():
    return FakeRecord(outcome="dispatch_failed_confirmed", codes=(df.PROVIDER_CALL_NOT_SENT,))


def r_proven():
    return FakeRecord(response=True, outcome="dispatch_failed_confirmed",
                      codes=(df.PROVIDER_REJECTED_MUTATION,))


def r_unproven():
    return FakeRecord(operation="update_helper_state", target_type="input_boolean",
                      response=True, outcome="dispatch_failed_confirmed",
                      codes=("provider_rejection_confirmed",))


def already_present():
    return FakeRecord(intent=False, noop=True)


def pre_intent():
    return FakeRecord(intent=False)


MISSING = object()


def child(record):
    if record is MISSING:
        return df.child_facts(None, missing=True)
    return df.child_facts(record)


class ChildPhaseTests(unittest.TestCase):
    def test_every_child_phase(self):
        cases = {
            "sent": (sent_verified(), df.PHASE_SENT_RESPONSE_RECEIVED),
            "c": (uncertain(), df.PHASE_SEND_UNCERTAIN),
            "lost_response": (FakeRecord(), df.PHASE_SEND_UNCERTAIN),
            "b": (not_sent(), df.PHASE_INTENT_RECORDED_NOT_SENT),
            "r_proven": (r_proven(), df.PHASE_SENT_REJECTED_NO_MUTATION),
            "r_unproven": (r_unproven(), df.PHASE_SENT_FAILED_MUTATION_UNPROVEN),
            "a": (already_present(), df.PHASE_ALREADY_PRESENT),
            "pre": (pre_intent(), df.PHASE_PRE_INTENT),
            "unreadable": (FakeRecord(unreadable=True), df.PHASE_SEND_UNCERTAIN),
        }
        for name, (record, phase) in cases.items():
            with self.subTest(name):
                self.assertEqual(df.child_facts(record).phase, phase)

    def test_uncertainty_code_with_a_response_stays_send_uncertain(self):
        # The OD-15 entity-registry error result after send (HAMCP-183) and
        # the configuration exception that carries a response record
        # ``dispatch_indeterminate`` with a received response.  Design
        # section 3 (c) keeps them send-uncertain, and readback never
        # upgrades the phase or claims a change.
        shapes = {
            "registry_error_after_send": ("provider_error_after_send",
                                          "provider_response_indeterminate"),
            "configuration_exception": ("provider_response_indeterminate",),
        }
        for name, codes in shapes.items():
            for outcome in ("dispatch_indeterminate", "succeeded_verified",
                            "verification_mismatch", "manual_review_required"):
                with self.subTest(name, outcome=outcome):
                    record = FakeRecord(operation="update_entity_metadata",
                                        target_type="entity_registry_metadata",
                                        response=True, outcome=outcome, codes=codes)
                    facts = df.child_facts(record)
                    self.assertEqual(facts.phase, df.PHASE_SEND_UNCERTAIN)
                    self.assertIsNone(facts.changed_by_engineering)
                    self.assertEqual(facts.provider_response_recorded_at, RESPONSE_AT)
                    parent = df.aggregate((facts,))
                    self.assertTrue(parent.provider_dispatch_occurred)
                    self.assertIsNone(parent.changed_by_engineering)

    def test_missing_child_is_uncertain_and_absent_record_is_pre_intent(self):
        self.assertEqual(df.child_facts(None, missing=True).phase, df.PHASE_SEND_UNCERTAIN)
        self.assertTrue(df.child_facts(None, missing=True).evidence_conflict)
        self.assertEqual(df.child_facts(None).phase, df.PHASE_PRE_INTENT)


class SingleBPredicateTests(unittest.TestCase):
    """Section 2.1 (b) predicate and its section 5.3 controls."""

    def test_both_proof_codes(self):
        for capability, code in (
            (df.DASHBOARD_CAPABILITY_ID, df.SETTER_CALL_NOT_SENT),
            (df.DASHBOARD_CAPABILITY_ID, df.PROVIDER_CALL_NOT_SENT),
            ("update_configuration", df.PROVIDER_CALL_NOT_SENT),
        ):
            with self.subTest(capability=capability, code=code):
                record = FakeRecord(operation=capability,
                                    outcome="dispatch_failed_confirmed", codes=(code,))
                facts = df.child_facts(record)
                self.assertEqual(facts.phase, df.PHASE_INTENT_RECORDED_NOT_SENT)
                self.assertEqual(facts.setter_proof, code == df.SETTER_CALL_NOT_SENT)
                self.assertEqual(
                    facts.attempt_counts_basis,
                    df.BASIS_NO_SETTER_CALL if code == df.SETTER_CALL_NOT_SENT
                    else df.BASIS_NO_PROVIDER_CALL,
                )

    def test_proof_code_missing_or_informational_only_is_uncertain(self):
        for codes in ((), ("provider_confirmed_no_mutation",),
                      ("dispatch_authority_retired_before_send",),
                      ("before_dispatch_failed",), ("fan_provider_authority_retired",)):
            with self.subTest(codes=codes):
                record = FakeRecord(outcome="dispatch_failed_confirmed", codes=codes)
                self.assertEqual(df.child_facts(record).phase, df.PHASE_SEND_UNCERTAIN)

    def test_informational_codes_never_prevent_b(self):
        record = FakeRecord(outcome="dispatch_failed_confirmed", codes=(
            df.PROVIDER_CALL_NOT_SENT, "provider_confirmed_no_mutation",
            "dispatch_authority_retired_before_send"))
        self.assertEqual(df.child_facts(record).phase, df.PHASE_INTENT_RECORDED_NOT_SENT)

    def test_response_received_with_proof_code_is_contradiction(self):
        for outcome in ("dispatch_failed_confirmed", "succeeded_verified"):
            with self.subTest(outcome=outcome):
                record = FakeRecord(response=True, outcome=outcome,
                                    codes=(df.PROVIDER_CALL_NOT_SENT,))
                facts = df.child_facts(record)
                self.assertEqual(facts.phase, df.PHASE_SEND_UNCERTAIN)
                self.assertTrue(facts.evidence_conflict)

    def test_proof_code_on_wrong_event_is_uncertain(self):
        record = FakeRecord(outcome="dispatch_failed_confirmed")
        record.events.insert(0, {"event_type": "preflight_completed",
                                 "diagnostic_codes": [df.PROVIDER_CALL_NOT_SENT]})
        self.assertEqual(df.child_facts(record).phase, df.PHASE_SEND_UNCERTAIN)

    def test_each_conflicting_code_is_uncertain(self):
        for code in sorted(df._B_CONFLICTING_CODES):
            with self.subTest(code=code):
                record = FakeRecord(outcome="dispatch_failed_confirmed",
                                    codes=(df.PROVIDER_CALL_NOT_SENT, code))
                facts = df.child_facts(record)
                self.assertEqual(facts.phase, df.PHASE_SEND_UNCERTAIN)
                self.assertTrue(facts.evidence_conflict)

    def test_setter_code_on_non_dashboard_capability_is_uncertain(self):
        record = FakeRecord(operation="update_configuration",
                            outcome="dispatch_failed_confirmed",
                            codes=(df.SETTER_CALL_NOT_SENT,))
        facts = df.child_facts(record)
        self.assertEqual(facts.phase, df.PHASE_SEND_UNCERTAIN)
        self.assertTrue(facts.evidence_conflict)

    def test_wrong_outcome_with_proof_code_is_never_b(self):
        for outcome in ("dispatch_indeterminate", "observing", None):
            with self.subTest(outcome=outcome):
                record = FakeRecord(outcome=outcome, codes=(df.PROVIDER_CALL_NOT_SENT,))
                self.assertNotEqual(df.child_facts(record).phase,
                                    df.PHASE_INTENT_RECORDED_NOT_SENT)

    def test_no_intent_is_never_b(self):
        record = FakeRecord(intent=False, outcome="dispatch_failed_confirmed",
                            codes=(df.PROVIDER_CALL_NOT_SENT,))
        self.assertEqual(df.child_facts(record).phase, df.PHASE_PRE_INTENT)

    def test_dashboard_wrapper_matches_b_predicate(self):
        b1 = FakeRecord(operation=df.DASHBOARD_CAPABILITY_ID,
                        outcome="dispatch_failed_confirmed",
                        codes=(df.SETTER_CALL_NOT_SENT, "provider_confirmed_no_mutation"))
        self.assertTrue(df.is_dashboard_setter_not_sent(df.DASHBOARD_CAPABILITY_ID, b1))
        self.assertFalse(df.is_dashboard_setter_not_sent("update_configuration", b1))
        rejection = FakeRecord(operation=df.DASHBOARD_CAPABILITY_ID, response=True,
                               outcome="observing",
                               codes=("structured_provider_rejection_received",))
        self.assertFalse(
            df.is_dashboard_setter_not_sent(df.DASHBOARD_CAPABILITY_ID, rejection)
        )


class RProvenTests(unittest.TestCase):
    """P1 and P3 are the complete set of non-mutation proofs (R3-1)."""

    def test_p1_automation_and_script_rest_4xx(self):
        for target_type in ("automation", "script"):
            with self.subTest(target_type=target_type):
                record = FakeRecord(target_type=target_type, response=True,
                                    outcome="dispatch_failed_confirmed",
                                    codes=(df.PROVIDER_REJECTED_MUTATION,))
                self.assertEqual(df.child_facts(record).phase,
                                 df.PHASE_SENT_REJECTED_NO_MUTATION)

    def test_p3_entity_metadata_unauthorized(self):
        record = FakeRecord(operation="update_entity_registry_metadata_configuration",
                            target_type="entity_registry_metadata", response=True,
                            outcome="dispatch_failed_confirmed",
                            codes=(df.PROVIDER_REJECTED_MUTATION,
                                   df.PROVIDER_PERMISSION_DENIED))
        self.assertEqual(df.child_facts(record).phase, df.PHASE_SENT_REJECTED_NO_MUTATION)

    def test_p1_requires_the_rejection_proof_code(self):
        # PR2 review 1 (L1): a received confirmed failure of an automation or
        # script without the P1 proof code is never claimed as no mutation.
        for target_type in ("automation", "script"):
            with self.subTest(target_type=target_type):
                record = FakeRecord(target_type=target_type, response=True,
                                    outcome="dispatch_failed_confirmed", codes=())
                facts = df.child_facts(record)
                self.assertEqual(facts.phase, df.PHASE_SENT_FAILED_MUTATION_UNPROVEN)
                self.assertNotEqual(facts.phase, df.PHASE_SENT_REJECTED_NO_MUTATION)
                parent = df.dispatch_facts(task=SimpleNamespace(dispatched_at=None),
                                           children=[facts])
                self.assertEqual(parent.dispatch_phase,
                                 df.PHASE_SENT_FAILED_MUTATION_UNPROVEN)
                self.assertIsNone(parent.outcome_code)
                self.assertIsNot(parent.changed_by_engineering, False)

    def test_negatives_are_unproven(self):
        negatives = {
            "backup_failed": FakeRecord(operation="create_backup", target_type="backup",
                                        codes=("provider_rejection_confirmed", "backup_failed")),
            "operation_failed": FakeRecord(operation="reload", target_type="reload",
                                           codes=("provider_rejection_confirmed",
                                                  "operation_failed")),
            "helper_provider_rejected": FakeRecord(operation="update_helper_state",
                                                   target_type="input_boolean",
                                                   codes=("provider_rejection_confirmed",
                                                          "provider_rejected")),
            # Constructed historical beta.8 shape: no permission code.
            "registry_error_without_permission": FakeRecord(
                operation="update_entity_registry_metadata_configuration",
                target_type="entity_registry_metadata",
                codes=(df.PROVIDER_REJECTED_MUTATION,)),
            "input_websocket_error": FakeRecord(target_type="input_number",
                                                codes=(df.PROVIDER_REJECTED_MUTATION,)),
            "dashboard_structured_rejection": FakeRecord(
                operation=df.DASHBOARD_CAPABILITY_ID, target_type="dashboard",
                codes=("structured_provider_rejection_received",)),
            "unreadable_target_type": FakeRecord(target_type="",
                                                 codes=(df.PROVIDER_REJECTED_MUTATION,)),
            "p1_with_conflicting_code": FakeRecord(
                codes=(df.PROVIDER_REJECTED_MUTATION, "provider_completed_unexpectedly")),
        }
        for name, record in negatives.items():
            with self.subTest(name):
                # Rebuild so the dispatch-result event carries the codes.
                record = replace(record, response=True, events=[],
                                 outcome="dispatch_failed_confirmed")
                self.assertEqual(df._codes(df._dispatch_result_event(record)),
                                 frozenset(record.codes))
                facts = df.child_facts(record)
                self.assertEqual(facts.phase, df.PHASE_SENT_FAILED_MUTATION_UNPROVEN)
                self.assertIsNone(facts.changed_by_engineering)

    def test_exception_without_response_is_never_r(self):
        record = FakeRecord(outcome="dispatch_failed_confirmed",
                            codes=(df.PROVIDER_REJECTED_MUTATION,))
        self.assertEqual(df.child_facts(record).phase, df.PHASE_SEND_UNCERTAIN)


TRUTH_TABLE = (
    # children, phase, provider_dispatch_occurred, changed_by_engineering
    ((sent_verified, uncertain), df.PHASE_SEND_UNCERTAIN, True, None),
    ((sent_verified, r_unproven), df.PHASE_SENT_FAILED_MUTATION_UNPROVEN, True, None),
    ((sent_verified, MISSING), df.PHASE_SEND_UNCERTAIN, True, None),
    ((sent_verified, sent_verified), df.PHASE_SENT_RESPONSE_RECEIVED, True, True),
    ((sent_verified, r_proven), df.PHASE_SENT_RESPONSE_RECEIVED, True, True),
    ((sent_verified, not_sent), df.PHASE_SENT_RESPONSE_RECEIVED, True, True),
    ((sent_unverified, not_sent), df.PHASE_SENT_RESPONSE_RECEIVED, True, None),
    ((r_unproven, not_sent), df.PHASE_SENT_FAILED_MUTATION_UNPROVEN, True, None),
    ((r_proven, uncertain), df.PHASE_SEND_UNCERTAIN, True, None),
    ((r_proven, not_sent), df.PHASE_SENT_REJECTED_NO_MUTATION, True, False),
    ((not_sent, uncertain), df.PHASE_SEND_UNCERTAIN, True, None),
    ((not_sent, MISSING), df.PHASE_SEND_UNCERTAIN, True, None),
    ((not_sent, not_sent), df.PHASE_INTENT_RECORDED_NOT_SENT, False, False),
    ((already_present, not_sent), df.PHASE_INTENT_RECORDED_NOT_SENT, False, False),
    ((pre_intent, not_sent), df.PHASE_INTENT_RECORDED_NOT_SENT, False, False),
    ((already_present, already_present), df.PHASE_ALREADY_PRESENT, False, False),
    ((already_present, pre_intent), df.PHASE_PRE_INTENT, False, False),
    ((already_present, lambda: None), df.PHASE_PRE_INTENT, False, False),
    ((already_present, MISSING), df.PHASE_SEND_UNCERTAIN, True, None),
    ((pre_intent, pre_intent), df.PHASE_PRE_INTENT, False, False),
)


def _children(makers):
    return [child(maker if maker is MISSING else maker()) for maker in makers]


class ParentTruthTableTests(unittest.TestCase):
    def test_every_truth_table_row_in_both_orders(self):
        for makers, phase, occurred, changed in TRUTH_TABLE:
            for order in (makers, tuple(reversed(makers))):
                with self.subTest(order=[getattr(m, "__name__", "missing") for m in order]):
                    facts = df.dispatch_facts(task=SimpleNamespace(dispatched_at=None),
                                              children=_children(order))
                    self.assertEqual(facts.dispatch_phase, phase)
                    self.assertIs(facts.provider_dispatch_occurred, occurred)
                    self.assertIs(facts.changed_by_engineering, changed)

    def test_all_children_predicates_agree_with_phase(self):
        for makers, phase, _, _ in TRUTH_TABLE:
            with self.subTest(phase=phase, makers=[getattr(m, "__name__", "missing")
                                                    for m in makers]):
                facts = df.dispatch_facts(task=SimpleNamespace(dispatched_at=None),
                                          children=_children(makers))
                self.assertEqual(facts.already_present, phase == df.PHASE_ALREADY_PRESENT)
                self.assertEqual(facts.not_sent, phase == df.PHASE_INTENT_RECORDED_NOT_SENT)
                fields = facts.public_fields()
                # (a) strings only in the all-(a) row; (b) flags only for (b).
                self.assertEqual("outcome_code" in fields and fields["outcome_code"]
                                 == df.ALREADY_PRESENT_OUTCOME,
                                 phase == df.PHASE_ALREADY_PRESENT)
                self.assertEqual(df.PROVIDER_CALL_NOT_SENT in fields,
                                 phase == df.PHASE_INTENT_RECORDED_NOT_SENT)

    def test_uncertainty_dominates_every_other_phase(self):
        for other in (sent_verified, sent_unverified, r_proven, r_unproven,
                      not_sent, already_present, pre_intent):
            with self.subTest(other=other.__name__):
                facts = df.dispatch_facts(task=SimpleNamespace(dispatched_at=None),
                                          children=_children((other, uncertain)))
                self.assertEqual(facts.dispatch_phase, df.PHASE_SEND_UNCERTAIN)
                self.assertTrue(facts.provider_dispatch_occurred)
                self.assertIsNone(facts.changed_by_engineering)
                self.assertNotIn("outcome_code", facts.public_fields())

    def test_unestablished_manifest_counts_as_one_uncertain_child(self):
        for children in (None, []):
            with self.subTest(children=children):
                facts = df.dispatch_facts(task=SimpleNamespace(dispatched_at=None),
                                          children=children)
                self.assertEqual(facts.dispatch_phase, df.PHASE_SEND_UNCERTAIN)
                self.assertEqual(facts.child_dispatch_phases,
                                 {df.PHASE_SEND_UNCERTAIN: 1})


class FieldSetTests(unittest.TestCase):
    """Section 2.2: which client fields each phase adds."""

    def facts(self, *makers, dispatched_at=None):
        return df.dispatch_facts(task=SimpleNamespace(dispatched_at=dispatched_at),
                                 children=_children(makers))

    def test_sent_and_uncertain_add_only_phase_and_basis_qualifiers(self):
        for maker in (sent_verified, uncertain):
            with self.subTest(maker.__name__):
                fields = self.facts(maker, dispatched_at=INTENT_AT).public_fields()
                for absent in ("changed_by_engineering", "intent_recorded",
                               "dispatch_performed", "result_text", "outcome_code",
                               df.PROVIDER_CALL_NOT_SENT, df.SETTER_CALL_NOT_SENT,
                               "retryable"):
                    self.assertNotIn(absent, fields)
                self.assertEqual(fields["attempt_counts_basis"], df.BASIS_SAVED_INTENT)
                self.assertEqual(fields["attempt_count_scope"],
                                 df.ATTEMPT_COUNT_SCOPE_PARENT)
                self.assertEqual(fields["dispatched_at"], INTENT_AT)
                self.assertEqual(fields["dispatched_at_basis"],
                                 df.DISPATCHED_AT_INTENT_COMMIT_TIME)

    def test_already_present_fields(self):
        fields = self.facts(already_present).public_fields()
        self.assertEqual(fields["outcome_code"], df.ALREADY_PRESENT_OUTCOME)
        self.assertIs(fields["dispatch_performed"], False)
        self.assertIs(fields["changed_by_engineering"], False)
        self.assertIs(fields["intent_recorded"], False)
        self.assertIs(fields["provider_dispatch_occurred"], False)
        self.assertEqual(fields["attempt_counts_basis"], df.BASIS_NO_INTENT)
        self.assertEqual(fields["dispatched_at_basis"], df.DISPATCHED_AT_NO_INTENT)

    def test_not_sent_fields_and_governed_dispatched_at_null(self):
        fields = self.facts(not_sent, dispatched_at=INTENT_AT).public_fields()
        self.assertIs(fields[df.PROVIDER_CALL_NOT_SENT], True)
        self.assertNotIn(df.SETTER_CALL_NOT_SENT, fields)
        self.assertIs(fields["retryable"], False)
        self.assertIs(fields["intent_recorded"], True)
        self.assertIs(fields["dispatch_performed"], False)
        self.assertEqual(fields["attempt_counts_basis"], df.BASIS_NO_PROVIDER_CALL)
        # OD-6(ii): governed (b) publishes null; the intent time is the
        # companion field, never dispatched_at.
        self.assertIsNone(fields["dispatched_at"])
        self.assertEqual(fields["dispatched_at_basis"], df.DISPATCHED_AT_CALL_NOT_SENT)
        self.assertEqual(fields["dispatch_intent_recorded_at"], INTENT_AT)

    def test_dashboard_all_b1_keeps_253_basis_at_one_and_three_children(self):
        def b1():
            return FakeRecord(operation=df.DASHBOARD_CAPABILITY_ID,
                              outcome="dispatch_failed_confirmed",
                              codes=(df.SETTER_CALL_NOT_SENT,))
        for count in (1, 3):
            with self.subTest(count=count):
                fields = self.facts(*([b1] * count)).public_fields()
                self.assertEqual(fields["attempt_counts_basis"], df.BASIS_NO_SETTER_CALL)
                self.assertIs(fields[df.SETTER_CALL_NOT_SENT], True)
                self.assertNotIn(df.PROVIDER_CALL_NOT_SENT, fields)

    def test_r_proven_and_r_unproven_fields(self):
        proven = self.facts(r_proven).public_fields()
        self.assertEqual(proven["outcome_code"], df.PROVIDER_REJECTED_MUTATION)
        self.assertIs(proven["changed_by_engineering"], False)
        self.assertIs(proven["provider_response_received"], True)
        self.assertEqual(proven["result_text"], df.REJECTED_NO_MUTATION_TEXT)
        self.assertNotIn(df.PROVIDER_CALL_NOT_SENT, proven)
        unproven = self.facts(r_unproven).public_fields()
        self.assertNotIn("outcome_code", unproven)
        self.assertIsNone(unproven["changed_by_engineering"])
        self.assertIs(unproven["dispatch_performed"], True)
        self.assertEqual(unproven["result_text"], df.FAILED_MUTATION_UNPROVEN_TEXT)

    def test_status_is_never_projected(self):
        for makers, _, _, _ in TRUTH_TABLE:
            self.assertNotIn("status", self.facts(*makers).public_fields())

    def test_child_dispatch_phases_counts(self):
        facts = self.facts(sent_verified, sent_verified, not_sent)
        self.assertEqual(dict(facts.child_dispatch_phases), {
            df.PHASE_SENT_RESPONSE_RECEIVED: 2, df.PHASE_INTENT_RECORDED_NOT_SENT: 1})


class LegacyAndRecordTests(unittest.TestCase):
    def test_legacy_task_keeps_stored_intent_predicate(self):
        cases = (
            (SimpleNamespace(dispatched_at=INTENT_AT, provider_attempts=[],
                             terminal_outcome=None), df.PHASE_SEND_UNCERTAIN, True),
            (SimpleNamespace(dispatched_at=None, provider_attempts=[{"dispatch_count": 1}],
                             terminal_outcome=None), df.PHASE_SEND_UNCERTAIN, True),
            (SimpleNamespace(dispatched_at=None, provider_attempts=[],
                             terminal_outcome=df.ALREADY_PRESENT_OUTCOME),
             df.PHASE_ALREADY_PRESENT, False),
            (SimpleNamespace(dispatched_at=None, provider_attempts=[],
                             terminal_outcome="failed_pre_dispatch"),
             df.PHASE_PRE_INTENT, False),
        )
        for task, phase, occurred in cases:
            with self.subTest(phase=phase):
                facts = df.legacy_dispatch_facts(task)
                self.assertEqual(facts.dispatch_phase, phase)
                self.assertIs(facts.provider_dispatch_occurred, occurred)

    def test_fan_power_record_never_publishes_a_send_time(self):
        cases = (
            (sent_verified(), df.DISPATCHED_AT_SEND_TIME_NOT_RECORDED, INTENT_AT, RESPONSE_AT),
            (FakeRecord(), df.DISPATCHED_AT_SEND_TIME_NOT_RECORDED, INTENT_AT, None),
            (already_present(), df.DISPATCHED_AT_NO_INTENT, None, None),
            (not_sent(), df.DISPATCHED_AT_CALL_NOT_SENT, INTENT_AT, None),
        )
        for record, basis, intent_at, response_at in cases:
            with self.subTest(basis=basis, response=response_at):
                facts = df.dispatch_facts_for_record(record)
                self.assertIsNone(facts.dispatched_at)
                self.assertEqual(facts.dispatched_at_basis, basis)
                self.assertEqual(facts.dispatch_intent_recorded_at, intent_at)
                self.assertEqual(facts.provider_response_recorded_at, response_at)

    def test_outcome_counts_one_per_parent(self):
        facts = [df.dispatch_facts(task=SimpleNamespace(dispatched_at=None),
                                   children=_children(makers))
                 for makers in ((already_present,), (not_sent, not_sent),
                                (r_proven,), (r_unproven,), (sent_verified,),
                                (uncertain, not_sent))]
        self.assertEqual(df.outcome_counts(facts), {
            "already_present_not_dispatched": 1,
            "failed_after_intent_not_dispatched": 1,
            "sent_rejected_no_mutation": 1,
            "sent_failed_mutation_unproven": 1,
        })
        self.assertEqual(df.outcome_counts([]), dict.fromkeys(
            df.OUTCOME_COUNT_KEYS.values(), 0))


class CounterQualifierTests(unittest.TestCase):
    def test_nested_counters_get_their_own_child_basis(self):
        records = {"c1": sent_verified(), "c2": not_sent()}
        children = [df.child_facts(record, child_id=key) for key, record in records.items()]
        facts = df.dispatch_facts(task=SimpleNamespace(dispatched_at=None), children=children)
        value = {
            "provider_attempts": [{"child_execution_id": "c1", "dispatch_count": 1},
                                  {"child_execution_id": "c2", "dispatch_count": 1},
                                  {"dispatch_count": 0}],
            "verification_summary": {"children": [
                {"child_execution_id": "c1", "dispatch_count": 1}]},
            "f3_children": [{"child_execution_id": "c2", "dispatch_count": 1}],
        }
        df.qualify_counters(value, facts)
        self.assertEqual([item["attempt_counts_basis"] for item in value["provider_attempts"]],
                         [df.BASIS_SAVED_INTENT, df.BASIS_NO_PROVIDER_CALL, df.BASIS_NO_INTENT])
        self.assertEqual(value["verification_summary"]["children"][0]["attempt_counts_basis"],
                         df.BASIS_SAVED_INTENT)
        self.assertEqual(value["f3_children"][0]["dispatch_phase"],
                         df.PHASE_INTENT_RECORDED_NOT_SENT)
        # Counters keep their stored values.
        self.assertEqual([item["dispatch_count"] for item in value["provider_attempts"]],
                         [1, 1, 0])


FIXTURES = ROOT / "tests" / "fixtures" / "f3_beta8_dispatch_phase" / "beta8"

# shape: (parent phase, provider_dispatch_occurred, changed_by_engineering,
#         child phases in declaration order)
BETA8_EXPECTED = {
    "a_helper_already_present": (df.PHASE_ALREADY_PRESENT, False, False,
                                 [df.PHASE_ALREADY_PRESENT]),
    "b_dashboard_setter_not_sent": (df.PHASE_INTENT_RECORDED_NOT_SENT, False, False,
                                    [df.PHASE_INTENT_RECORDED_NOT_SENT]),
    "r_proven_configuration_4xx": (df.PHASE_SENT_REJECTED_NO_MUTATION, True, False,
                                   [df.PHASE_SENT_REJECTED_NO_MUTATION]),
    "r_unproven_operational_rejection": (df.PHASE_SENT_FAILED_MUTATION_UNPROVEN, True, None,
                                         [df.PHASE_SENT_FAILED_MUTATION_UNPROVEN]),
    "c_response_lost_verified": (df.PHASE_SEND_UNCERTAIN, True, None,
                                 [df.PHASE_SEND_UNCERTAIN]),
    "sent_verified_configuration": (df.PHASE_SENT_RESPONSE_RECEIVED, True, True,
                                    [df.PHASE_SENT_RESPONSE_RECEIVED]),
    "v9_entity_metadata_already_present": (df.PHASE_ALREADY_PRESENT, False, False,
                                           [df.PHASE_ALREADY_PRESENT]),
    "mixed_three_child_parent": (df.PHASE_SENT_RESPONSE_RECEIVED, True, True,
                                 [df.PHASE_SENT_RESPONSE_RECEIVED,
                                  df.PHASE_SENT_RESPONSE_RECEIVED,
                                  df.PHASE_SENT_REJECTED_NO_MUTATION]),
}


def _fixture_hashes(shape: str) -> dict[str, str]:
    import hashlib

    prefix = f"{shape}/store/"
    provenance = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))
    files = {key[len(prefix):]: value for key, value in provenance["files"].items()
             if key.startswith(prefix)}
    for relative, digest in files.items():
        actual = hashlib.sha256((FIXTURES / shape / "store" / relative).read_bytes()).hexdigest()
        assert actual == digest, (shape, relative)
    return files


class Beta8HistoricalRecordTests(unittest.IsolatedAsyncioTestCase):
    """Records written by the exact beta.8 writer (7370ad2) project correctly.

    The current runtime opens a copy of each committed store, runs startup
    recovery, and reads every surface.  No stored file may change: the
    projection is display only and no migration rewrites historical records.
    """

    async def open(self, shape: str):
        import hashlib
        import shutil
        import tempfile
        from datetime import datetime, timedelta

        import tests.test_dev14_configuration_plans as dev14
        import tests.test_f3_runtime_integration as f3_integration
        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration
        from ha_mcp_engineering.governance.service import ChangeGovernanceService
        from ha_mcp_engineering.governance.storage import ChangePlanRepository
        from ha_mcp_engineering.request_context import begin_request, end_request

        hashes = _fixture_hashes(shape)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        store = root / "plans"
        shutil.copytree(FIXTURES / shape / "store", store)
        (task_path,) = (store / "execution-tasks-v1").glob("*.json")
        stored_task = json.loads(task_path.read_text(encoding="utf-8"))
        now = datetime.fromisoformat(stored_task["updated_at"]) + timedelta(minutes=1)
        gateway = dev14.FakeConfigurationGateway()
        service = ChangeGovernanceService(
            ChangePlanRepository(store), gateway,
            AuditLogger(str(root / "audit.jsonl"), "synthetic-hamcp181-secret"),
            now=lambda: now)
        _telemetry, context = begin_request("synthetic-hamcp181-beta8-reader")
        self.addCleanup(end_request, context)
        runtime = F3RuntimeIntegration(
            service=service, storage_root=str(store),
            configuration_gateway=f3_integration._ExactFakeConfigurationGateway(gateway),
            backup_gateway=None, lifecycle_gateway=None,
            provider_identity_reader=f3_integration._provider_identity, retention_days=90)
        service.f3_runtime = runtime
        await runtime.recover_once("startup")
        self.assertEqual([call for call in gateway.calls if call[0] == "write"], [])

        def unchanged():
            for relative, digest in hashes.items():
                actual = hashlib.sha256((store / relative).read_bytes()).hexdigest()
                self.assertEqual(actual, digest, f"{shape}: {relative} was rewritten")

        return service, runtime, stored_task, unchanged

    async def test_every_beta8_shape_projects_and_is_never_rewritten(self):
        for shape, (phase, occurred, changed, child_phases) in BETA8_EXPECTED.items():
            with self.subTest(shape=shape):
                service, runtime, stored, unchanged = await self.open(shape)
                task = service.task_repository.get(stored["task_id"])
                facts = runtime.dispatch_facts_for_task(task)
                self.assertEqual(facts.dispatch_phase, phase)
                self.assertIs(facts.provider_dispatch_occurred, occurred)
                self.assertIs(facts.changed_by_engineering, changed)
                self.assertEqual([child.phase for child in facts.children], child_phases)
                view = service.get_execution_task(stored["task_id"])
                listed = [item for item in service.list_execution_tasks()["tasks"]
                          if item["task_id"] == stored["task_id"]]
                plan = service.get_plan(stored["plan_id"])
                for surface in (view, *listed):
                    self.assertEqual(surface["dispatch_phase"], phase, surface)
                    self.assertIs(surface["provider_dispatch_occurred"], occurred)
                self.assertEqual(plan["execution_task"]["dispatch_phase"], phase)
                # Stored values the projection must not alter.
                self.assertEqual(view["state"], stored["state"])
                self.assertEqual(view["terminal_outcome"], stored["terminal_outcome"])
                unchanged()

    async def test_beta8_dashboard_b_keeps_stored_intent_but_publishes_null(self):
        service, runtime, stored, unchanged = await self.open("b_dashboard_setter_not_sent")
        self.assertIsNotNone(stored["dispatched_at"])
        view = service.get_execution_task(stored["task_id"])
        self.assertIsNone(view["dispatched_at"])
        self.assertEqual(view["dispatched_at_basis"], df.DISPATCHED_AT_CALL_NOT_SENT)
        self.assertEqual(view["attempt_counts_basis"], df.BASIS_NO_SETTER_CALL)
        self.assertIs(view["intent_recorded"], True)
        self.assertIs(view["dispatch_performed"], False)
        self.assertEqual(view["dispatch_intent_recorded_at"], stored["dispatched_at"])
        unchanged()

    async def test_beta8_intent_phases_keep_the_stored_dispatched_at(self):
        for shape in ("r_proven_configuration_4xx", "r_unproven_operational_rejection",
                      "c_response_lost_verified", "sent_verified_configuration",
                      "mixed_three_child_parent"):
            with self.subTest(shape=shape):
                service, _runtime, stored, unchanged = await self.open(shape)
                view = service.get_execution_task(stored["task_id"])
                self.assertEqual(view["dispatched_at"], stored["dispatched_at"])
                self.assertEqual(view["dispatched_at_basis"],
                                 df.DISPATCHED_AT_INTENT_COMMIT_TIME)
                self.assertEqual(view["provider_attempt_count"],
                                 len(stored["provider_attempts"]))
                self.assertGreater(view["provider_attempt_count"], 0)
                unchanged()

    async def test_beta8_no_intent_shapes_publish_no_intent_basis(self):
        for shape in ("a_helper_already_present", "v9_entity_metadata_already_present"):
            with self.subTest(shape=shape):
                service, _runtime, stored, unchanged = await self.open(shape)
                view = service.get_execution_task(stored["task_id"])
                self.assertIsNone(view["dispatched_at"])
                self.assertEqual(view["dispatched_at_basis"], df.DISPATCHED_AT_NO_INTENT)
                self.assertEqual(view["attempt_counts_basis"], df.BASIS_NO_INTENT)
                unchanged()


if __name__ == "__main__":
    unittest.main()
