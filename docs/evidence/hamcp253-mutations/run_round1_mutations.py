"""HAMCP-253 round-1 correction mutants (R1-M1, R1-M2, R1-M3).

Reuses the copy, preflight, kill rule and reporting of ``run_mutations.py``
with a mutant set aimed only at the code the round-1 corrections added or
changed, and runs the round-1 test module as well as the two original
HAMCP-253 modules. Only an assertion failure (``FAIL:``) is a kill.

Classes:

- ``R1Q*``: inspection and the stuck list never mutate storage (R1-M1);
- ``R1S*``: stored metadata is screened and never echoed (R1-M2);
- ``R1A*``: acknowledgement audit coordination in the service (R1-M3);
- ``R1L*``: ledger decision and completion entries (R1-M3);
- ``P-*``: original ``run_mutations.py`` mutants whose code round 1
  rewrote, ported to the rewritten code so they still apply.

Usage (idle host, from the worktree root):

    systemd-run --user --unit=hamcp253-r1-mutations -p MemoryMax=3G \\
        -p MemorySwapMax=0 -p OOMPolicy=continue \\
        --working-directory=<worktree> /bin/bash -c \\
        '.artifacts/venv/bin/python docs/evidence/hamcp253-mutations/run_round1_mutations.py \\
         --work .artifacts/hamcp253-r1-mut \\
         --output docs/evidence/hamcp253-mutations/results-round1-corrections.json \\
         > .artifacts/hamcp253-r1-mut.log 2>&1'
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_mutations as harness  # noqa: E402
from run_mutations import AW, RA, RI, ST, SV, TS, E, m  # noqa: E402


PD = f"{E}/governance/projection_diagnostics.py"
Q = "inspection or the stuck list mutates storage (R1-M1)"
S = "stored metadata echoed or not screened (R1-M2)"
A = "acknowledgement audit coordination weakened (R1-M3)"
L = "ledger decision or completion validation weakened (R1-M3)"
I = "acknowledgeability or token screening weakened (ported)"

MUTATIONS = [
    # R1-M1: no storage side effects.
    m("R1Q1-plan-quarantined-id-reads-as-missing", Q,
      (ST, '                    raise ChangePlanStorageError(\n'
           '                        "Governance record identifier is quarantined"\n'
           '                    )\n',
           "                    return None\n")),
    m("R1Q2-plan-read-all-quarantines", Q,
      (ST, "                    plan = self._load(path, quarantine_corrupt=False)\n",
           "                    plan = self._load(path, quarantine_corrupt=True)\n")),
    m("R1Q3-plan-read-all-rebuilds-index", Q,
      (ST, "        with self._lock:\n            plans: list[ChangePlan] = []\n",
           "        with self._lock:\n            self.rebuild_navigation_index()\n"
           "            plans: list[ChangePlan] = []\n")),
    m("R1Q4-tasks-manual-review-ids-quarantine", Q,
      (TS, "                    task = self._load(path, quarantine_corrupt=False)\n",
           "                    task = self._load(path, quarantine_corrupt=True)\n")),
    m("R1Q5-tasks-manual-review-ids-rebuild", Q,
      (TS, "            if not self._navigation_index_stale():\n",
           "            self._ensure_navigation_index()\n            if True:\n")),
    m("R1Q6-tasks-inspection-read-rebuilds-index", Q,
      (TS, "            task = self._load(self._path(task_id), quarantine_corrupt=False)\n",
           "            self._ensure_navigation_index()\n"
           "            task = self._load(self._path(task_id), quarantine_corrupt=False)\n")),
    m("R1Q7-tasks-inspection-read-quarantines", Q,
      (TS, "            task = self._load(self._path(task_id), quarantine_corrupt=False)\n",
           "            task = self._load(self._path(task_id), quarantine_corrupt=True)\n")),
    m("R1Q8-stuck-list-refreshes-index", Q,
      (SV, "        return self._acknowledgement_health(storage_read_only=True)\n",
           "        return self._acknowledgement_health(storage_read_only=False)\n")),
    m("R1Q9-stuck-list-trusts-stale-index", Q,
      (SV, "            self._observed_plan_index_rebuild_count\n"
           "            == self.repository.index_rebuild_count\n",
           "            True\n")),
    # R1-M2: screening and the requested identifier.
    m("R1S1-plan-stored-identity-always-matches", S,
      (RI, "STORED_IDENTITY_MATCH if plan.plan_id == record_id\n",
           "STORED_IDENTITY_MATCH if True\n")),
    m("R1S2-tasks-stored-identity-always-matches", S,
      (RI, "STORED_IDENTITY_MATCH if task.task_id == record_id\n",
           "STORED_IDENTITY_MATCH if True\n")),
    m("R1S3-plan-view-echoes-stored-id", S,
      (SV, "            return record_inspection.plan_view(self, plan, record_id)\n",
           "            return record_inspection.plan_view(self, plan, plan.plan_id)\n")),
    m("R1S4-tasks-view-echoes-stored-id", S,
      (SV, "            task, record_id, sensitive_values=self.sensitive_values\n",
           "            task, task.task_id, sensitive_values=self.sensitive_values\n")),
    m("R1S5-open-screen-ignores-sanitizer", S,
      (RI, "        if result.failed_closed or result.redaction_applied or result.value != text:\n",
           "        if False:\n")),
    m("R1S6-open-screen-ignores-configured-values", S,
      (RI, "        result = sanitize_untrusted_data(text, known_secrets=self.sensitive_values)\n",
           "        result = sanitize_untrusted_data(text, known_secrets=())\n")),
    m("R1S7-tasks-view-ignores-configured-values", S,
      (SV, "            task, record_id, sensitive_values=self.sensitive_values\n",
           "            task, record_id, sensitive_values=()\n")),
    m("R1S8-closed-screen-accepts-any-string", S,
      (RI, "        return text if isinstance(text, str) and text in vocabulary else UNRECOGNIZED\n",
           "        return text if isinstance(text, str) else UNRECOGNIZED\n")),
    m("R1S9-tasks-plan-id-echoed", S,
      (RI, '        "plan_id": screen.record_id(task.plan_id) or UNRECOGNIZED,\n',
           '        "plan_id": task.plan_id,\n')),
    m("R1S10-plan-mismatch-acknowledgeable", S,
      (RI, "    acknowledgeable = identity == STORED_IDENTITY_MATCH and (\n",
           "    acknowledgeable = (\n")),
    m("R1S11-tasks-mismatch-acknowledgeable", S,
      (RI, "            manual_review and identity == STORED_IDENTITY_MATCH\n",
           "            manual_review\n")),
    m("R1S12-health-lists-unscreened-ids", S,
      (SV, "                if record_inspection.screened_record_id(\n"
           "                    record_id, self.sensitive_values\n"
           "                ) is None:\n",
           "                if False:\n")),
    m("R1S13-projection-row-echoes-stored-plan-id", S,
      (PD, "                and _RECORD_ID.fullmatch(plan.plan_id)\n",
           "                or True\n")),
    m("R1S14-record-id-screen-ignores-configured-values", S,
      (RI, "    shown = _Screen(sensitive_values).record_id(value)\n",
           "    shown = _Screen(()).record_id(value)\n")),
    m("R1S15-ingress-drops-stored-identity-row", S,
      (AW, '        ("Stored identifier", view.get("stored_identity")),\n', "")),
    # R1-M3: service audit coordination.
    m("R1A1-audit-unavailable-not-refused", A,
      (SV, '            refusal = "audit_unavailable"\n', "            pass\n")),
    m("R1A2-disabled-audit-treated-as-enabled", A,
      (SV, '            and getattr(self.audit, "enabled", False) is True\n', "")),
    m("R1A3-pathless-audit-treated-as-enabled", A,
      (SV, '            and bool(getattr(self.audit, "path", None))\n', "")),
    m("R1A4-failed-audit-completes-as-recorded", A,
      (SV, '                        "recorded" if decision_audited else "abandoned",\n',
           '                        "recorded",\n')),
    m("R1A5-failed-audit-not-refused", A,
      (SV, '                        status, refusal = "refused", "audit_write_failed"\n',
           "                        pass\n")),
    m("R1A6-audit-batch-count-ignored", A,
      (SV, "            return self.audit.write_batch((record,)) == 1\n",
           "            return self.audit.write_batch((record,)) is not None\n")),
    m("R1A7-audit-exception-treated-as-written", A,
      (SV, "        except Exception:\n            return False\n\n"
           "    def _reconcile_acknowledgement_audits(",
           "        except Exception:\n            return True\n\n"
           "    def _reconcile_acknowledgement_audits(")),
    m("R1A8-decision-audit-skips-enabled-check", A,
      (SV, "        if not self._acknowledgement_audit_enabled():\n            return False\n"
           "        try:\n            return self.audit.write_batch",
           "        try:\n            return self.audit.write_batch")),
    m("R1A9-writer-does-not-reconcile", A,
      (SV, "                self._reconcile_acknowledgement_audits(transaction)\n"
           "                status, refusal = self._acknowledgement_decision_status(\n",
           "                status, refusal = self._acknowledgement_decision_status(\n")),
    m("R1A10-unreconcilable-decision-skipped", A,
      (SV, '                raise AcknowledgementStorageError("acknowledgement_audit_pending")\n'
           '            transaction.complete(decision, "recorded", self._timestamp())\n',
           "                continue\n"
           '            transaction.complete(decision, "recorded", self._timestamp())\n')),
    m("R1A11-start-does-not-reconcile", A,
      (SV, "        self._reconcile_acknowledgement_audits_at_start()\n", "")),
    m("R1A12-interrupted-decision-reported-refused", A,
      (SV, '                status = "pending"\n'
           '                refusal = "acknowledgement_audit_reconciliation_pending"\n',
           '                refusal = "acknowledgement_audit_reconciliation_pending"\n')),
    m("R1A13-disabled-logger-reports-recorded", A,
      (SV, "        elif not self._acknowledgement_audit_enabled():\n"
           "            # A disabled logger reports success without writing.\n"
           "            audited = False\n",
           "        elif False:\n            audited = False\n")),
    m("R1A14-outcome-audit-result-ignored", A,
      (SV, "                ) is True\n            except Exception:\n                audited = False\n",
           "                ) or True\n            except Exception:\n                audited = False\n")),
    m("R1A15-abandoned-decision-skips-refusal-audit", A,
      (SV, "                if decision is not None and (decision_audited or not completed)\n",
           "                if decision is not None\n")),
    m("R1A16-start-reconciles-without-audit", A,
      (SV, "            if not self._acknowledgement_audit_enabled():\n                return\n"
           "            with self.acknowledgements.transaction() as transaction:\n",
           "            with self.acknowledgements.transaction() as transaction:\n")),
    m("R1A17-no-enabled-check-before-any-reconcile-write", A,
      (SV, "        if not self._acknowledgement_audit_enabled():\n            return False\n"
           "        try:\n            return self.audit.write_batch",
           "        try:\n            return self.audit.write_batch"),
      (SV, "            if not self._acknowledgement_audit_enabled():\n                return\n"
           "            with self.acknowledgements.transaction() as transaction:\n",
           "            with self.acknowledgements.transaction() as transaction:\n")),
    # Original mutants whose code round 1 rewrote, ported to the new code
    # (Q5 is superseded by R1Q7, which mutates the rewritten read).
    m("P-A14-identical-repeat-appended", A,
      (SV, '            return "no_op", None\n        return ("reclassified"',
           '            return "acknowledged", None\n        return ("reclassified"')),
    m("P-A15-reason-ignored-in-repeat-comparison", A,
      (SV, '            and latest["reason"] == body["reason"]\n', "")),
    m("P-A16-revoke-without-acknowledgement-appended", A,
      (SV, '                return "refused", "record_not_acknowledged"\n',
           '                return "revoked", None\n')),
    m("P-A17-revoke-after-revoke-appended", A,
      (SV, '("no_op" if latest["action"] == "revoke" else "revoked")', '"revoked"')),
    m("P-A20-reason-text-written-to-audit", A,
      (SV, "                    len(reason) if isinstance(reason, str) else None\n",
           "                    reason if isinstance(reason, str) else None\n")),
    m("P-A21-health-memo-not-cleared-after-write", A,
      (SV, "        finally:\n            self._acknowledgement_health_memo = None\n"
           "        return self._acknowledgement_outcome(",
           "        finally:\n            pass\n"
           "        return self._acknowledgement_outcome(")),
    m("P-I6-failed-or-rejected-plan-not-acknowledgeable", I,
      (RI, "        error is not None or plan.status in ACKNOWLEDGEABLE_PLAN_STATUSES or (\n",
           "        error is not None or (\n")),
    m("P-I8-any-task-acknowledgeable", I,
      (RI, "            manual_review and identity == STORED_IDENTITY_MATCH\n", "            True\n")),
    m("P-I9-token-pattern-not-applied", I,
      (RI, "        if not isinstance(text, str) or not pattern.fullmatch(text):\n",
           "        if not isinstance(text, str):\n")),
    # R1-M3: ledger entries.
    m("R1L1-unaudited-decision-effective", L,
      (RA, '        if body["entry_type"] != DECISION or done.get(event["sequence"]) != "recorded":\n',
           '        if body["entry_type"] != DECISION:\n')),
    m("R1L2-append-over-pending-decision", L,
      (RA, "        if pending_decisions(self.events):\n", "        if False:\n")),
    m("R1L3-no-room-reserved-for-completion", L,
      (RA, "        if len(self.events) + 2 > MAX_EVENTS:\n",
           "        if len(self.events) + 1 > MAX_EVENTS:\n")),
    m("R1L4-double-completion-accepted", L,
      (RA, "                    if (not 1 <= target < index or target in completed\n",
           "                    if (not 1 <= target < index\n")),
    m("R1L5-completion-audit-id-unchecked", L,
      (RA, "                            != audit_event_id(value[\"events\"][target - 1])):\n",
           "                            != body[\"audit_event_id\"]):\n")),
    m("R1L6-completion-of-non-decision-accepted", L,
      (RA, '                            or value["events"][target - 1]["body"]["entry_type"] != DECISION\n',
           "")),
    m("R1L7-forward-completion-accepted", L,
      (RA, "                    if (not 1 <= target < index or target in completed\n",
           "                    if (not 1 <= target or target in completed\n")),
    m("R1L8-complete-accepts-settled-decision", L,
      (RA, "        if (decision not in pending_decisions(self.events)\n"
           "                or outcome not in COMPLETION_OUTCOMES):\n",
           "        if (outcome not in COMPLETION_OUTCOMES):\n")),
    m("R1L9-decision-entry-type-unchecked", L,
      (RA, "                or body[\"entry_type\"] != DECISION):\n", "                ):\n")),
    m("R1L10-completion-outcome-unchecked", L,
      (RA, "                or body[\"outcome\"] not in COMPLETION_OUTCOMES):\n", "                ):\n")),
    m("R1L11-no-decision-ever-pending", L,
      (RA, '        and event["sequence"] not in done\n', "        and False\n")),
    m("R1L12-abandoned-count-wrong", L,
      (RA, '            outcome == "abandoned" for outcome in done.values()\n',
           '            outcome == "recorded" for outcome in done.values()\n')),
]

EQUIVALENT: dict[str, str] = {
    "R1A8-decision-audit-skips-enabled-check": (
        "every call of the decision-audit writer follows an enabled check in "
        "the same call (acknowledge refuses audit_unavailable before the "
        "ledger opens; start reconciliation returns before its transaction), "
        "and GovernanceService.audit and AuditLogger.enabled/path are "
        "assigned only at construction, so the writer never sees a disabled "
        "logger; R1A17 shows the pair of checks is covered"
    ),
    "R1A16-start-reconciles-without-audit": (
        "with a disabled logger the writer's own enabled check returns False, "
        "start reconciliation raises acknowledgement_audit_pending and catches "
        "it, so nothing is written (the ledger and audit bytes are asserted "
        "unchanged); R1A17 removes both checks and is killed"
    ),
    "P-A21-health-memo-not-cleared-after-write": (
        "as in the original round: the memo key includes the ledger token "
        "(inode, size, mtime_ns) and every append replaces the file, so the "
        "memo misses after any write; the clear is defence in depth"
    ),
    "R1L3-no-room-reserved-for-completion": (
        "completions each name a distinct earlier decision, so a ledger with "
        "no pending decision has an even length; MAX_EVENTS is 2048, and for "
        "an even length len+1 > 2048 and len+2 > 2048 agree; an odd length "
        "means a pending decision, which append refuses first"
    ),
}


def main() -> int:
    harness.MODULES = (
        "test_hamcp253_record_acknowledgement.py",
        "test_hamcp253_record_acknowledgement_web.py",
        "test_hamcp253_round1_corrections.py",
    )
    harness.MUTATIONS = MUTATIONS
    harness.EQUIVALENT = EQUIVALENT
    return harness.main()


if __name__ == "__main__":
    raise SystemExit(main())
