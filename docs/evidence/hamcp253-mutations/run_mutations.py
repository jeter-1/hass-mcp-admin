"""HAMCP-253 mutation runner (offline, deterministic).

Copies the working tree (tracked top-level entries, without ``.git`` and
``.artifacts``) to a disposable directory under the worktree's ignored
``.artifacts/`` (HAMCP-237), checks that every fragment matches exactly once
before anything runs, then applies one mutant at a time and runs the two
HAMCP-253 suites without fail-fast.

Only an assertion failure counts as a kill: a mutant is killed when the
output has at least one ``FAIL:`` line. A mutant that produces only
``ERROR:`` lines (an exception, import or syntax error) is reported as
``error_only`` and is not counted as killed. A timeout is not a kill.

Mutants listed in ``EQUIVALENT`` are expected to survive; each carries the
reason it cannot change observable behaviour.

Classes:

- ``A*``: acknowledgement refusals and outcomes (service);
- ``H*``: health counts, warnings and index freshness (service);
- ``Q*``: side-effect-free inspection reads (storage and service);
- ``I*``: inspection view bounds and acknowledgeability;
- ``L*``: ledger validation and bounds;
- ``W*``: Ingress routes, administrator gate and CSRF binding.

Usage:

    systemd-run --user --unit=hamcp253-mutations -p MemoryMax=3G \\
        -p MemorySwapMax=0 -p OOMPolicy=continue \\
        --working-directory=<worktree> /bin/bash -c \\
        '.artifacts/venv/bin/python docs/evidence/hamcp253-mutations/run_mutations.py \\
         --work .artifacts/hamcp253-mut \\
         --output docs/evidence/hamcp253-mutations/results.json > .artifacts/hamcp253-mut.log 2>&1'
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
E = "hass_mcp_engineering_beta/ha_mcp_engineering"
SV = f"{E}/governance/service.py"
ST = f"{E}/governance/storage.py"
TS = f"{E}/governance/task_storage.py"
RI = f"{E}/governance/record_inspection.py"
RA = f"{E}/governance/record_acknowledgement.py"
AW = f"{E}/approval_web.py"
MODULES = (
    "test_hamcp253_record_acknowledgement.py",
    "test_hamcp253_record_acknowledgement_web.py",
)
_FAILURE_LINE = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]+)\)", re.MULTILINE)


def m(identifier, item, *replacements):
    return {"id": identifier, "class": item, "replacements": replacements}


A = "acknowledgement refusal or outcome weakened"
H = "health counts, warnings or index freshness wrong"
Q = "inspection read has side effects or misreports"
I = "inspection view unbounded or acknowledgeability wrong"
L = "ledger validation or bound weakened"
W = "Ingress gate, CSRF binding or availability weakened"

MUTATIONS = [
    m("A1-administrator-check-skipped", A,
      (SV, '            refusal = "administrator_required"\n', "            pass\n")),
    m("A2-identifier-check-skipped", A,
      (SV, '            refusal = "record_identifier_invalid"\n', "            pass\n")),
    m("A3-action-check-skipped", A,
      (SV, '            refusal = "action_invalid"\n', "            pass\n")),
    m("A4-classification-check-skipped", A,
      (SV, '            refusal = "classification_invalid"\n', "            pass\n")),
    m("A5-reason-oversize-off-by-one", A,
      (SV, "        elif not isinstance(reason, str) or len(reason) > (\n"
           "            acknowledgement_ledger.MAX_REASON_CHARS\n        ):",
       "        elif not isinstance(reason, str) or len(reason) > (\n"
       "            acknowledgement_ledger.MAX_REASON_CHARS + 1\n        ):")),
    m("A6-reason-shape-check-skipped", A,
      (SV, '            refusal = "reason_invalid"\n', "            pass\n")),
    m("A7-redacted-reason-accepted", A,
      (SV, "lambda result: result.failed_closed or result.redaction_applied",
       "lambda result: result.failed_closed")),
    m("A8-malformed-fingerprint-accepted", A,
      (SV, '            refusal = "record_fingerprint_stale"\n        if refusal is None:',
       "            pass\n        if refusal is None:")),
    m("A9-kind-mismatch-reported-as-not-found", A,
      (SV, '"record_kind_mismatch" if exists_as_other else "record_not_found"',
       '"record_not_found"')),
    m("A10-corrupt-other-kind-treated-as-missing", A,
      (SV, "                    exists_as_other = True\n",
       "                    exists_as_other = False\n")),
    m("A11-unreadable-record-not-refused", A,
      (SV, '            if not view["readable"]:\n                refusal = "record_unreadable"',
       '            if False:\n                refusal = "record_unreadable"')),
    m("A12-acknowledgeability-not-checked", A,
      (SV, '            elif not view["acknowledgeable"]:', "            elif False:")),
    m("A13-current-fingerprint-not-compared", A,
      (SV, "            elif not hmac.compare_digest(\n"
           '                view["record_fingerprint"], record_fingerprint\n            ):',
       "            elif False:")),
    m("A14-identical-repeat-appended", A,
      (SV, '                    status = "no_op"\n                elif effective == "acknowledged":',
       '                    status = "acknowledged"\n                elif effective == "acknowledged":')),
    m("A15-reason-ignored-in-repeat-comparison", A,
      (SV, '                    and latest["reason"] == reason\n', "")),
    m("A16-revoke-without-acknowledgement-appended", A,
      (SV, 'status, refusal = "refused", "record_not_acknowledged"', 'status = "revoked"')),
    m("A17-revoke-after-revoke-appended", A,
      (SV, '                    elif latest["action"] == "revoke":\n                        status = "no_op"',
       '                    elif False:\n                        status = "no_op"')),
    m("A18-stale-acknowledgement-still-counts", A,
      (SV, '            else "stale"\n', '            else "acknowledged"\n')),
    m("A19-revoke-projected-as-acknowledged", A,
      (SV, '"revoked" if entry["action"] == "revoke"', '"acknowledged" if entry["action"] == "revoke"')),
    m("A20-reason-text-written-to-audit", A,
      (SV, '                            "reason_length": (\n'
           "                                len(reason) if isinstance(reason, str) else None\n",
       '                            "reason_length": (\n'
       "                                reason if isinstance(reason, str) else None\n")),
    m("A21-health-memo-not-cleared-after-write", A,
      (SV, "        finally:\n            self._acknowledgement_health_memo = None\n",
       "        finally:\n            pass\n")),
    m("H1-stale-projection-index-not-refreshed", H,
      (SV, "        self._ensure_projection_index_current(invalidate_health=False)\n", "")),
    m("H2-plan-consistency-guard-removed", H,
      (SV, "            or (raw_plans and total(plans) != raw_plans)\n", "")),
    m("H3-task-consistency-guard-removed", H,
      (SV, "            or (raw_tasks and total(tasks) != raw_tasks)\n", "")),
    m("H4-plan-warning-from-task-bucket", H,
      (SV, '            if plans["unacknowledged_count"]\n', '            if tasks["unacknowledged_count"]\n')),
    m("H5-manual-review-warning-never-raised", H,
      (SV, '            if tasks["unacknowledged_count"]\n', "            if False\n")),
    m("H6-revoked-or-stale-counted-historical", H,
      (SV, '                    classification if state == "acknowledged" else "unacknowledged"\n',
       '                    "historical_record" if state != "unacknowledged" else "unacknowledged"\n')),
    m("H7-health-id-list-unbounded", H,
      (SV, '"unacknowledged_ids": groups["unacknowledged"][:limit],',
       '"unacknowledged_ids": groups["unacknowledged"],')),
    m("H8-health-truncation-flag-false", H,
      (SV, '"ids_truncated": any(len(group) > limit for group in groups.values()),',
       '"ids_truncated": False,')),
    m("Q1-plan-inspection-read-quarantines", Q,
      (ST, "            return self._load(existing[0], quarantine_corrupt=False)",
       "            return self._load(existing[0])")),
    m("Q2-plan-quarantine-flag-ignored", Q,
      (ST, "            if quarantine_corrupt:\n                self._quarantine(path)",
       "            if True:\n                self._quarantine(path)")),
    m("Q3-plan-inspection-uses-get", Q,
      (SV, "                plan = self.repository.read_for_inspection(record_id)",
       "                plan = self.repository.get(record_id)")),
    m("Q4-task-inspection-uses-get", Q,
      (SV, "            task = self.task_repository.read_for_inspection(record_id)",
       "            task = self.task_repository.get(record_id)")),
    m("Q5-task-inspection-read-quarantines", Q,
      (TS, "                else self._path(task_id),\n                quarantine_corrupt=False,\n",
       "                else self._path(task_id),\n")),
    m("I1-approval-sequence-from-displayed-rows", I,
      (RI, '        row["event"] for row in rows\n',
       '        row["event"] for row in rows[:MAX_INSPECTED_EVENTS]\n')),
    m("I2-approval-sequence-unbounded", I,
      (RI, '"approval_event_sequence": approval[:MAX_INSPECTED_EVENTS],',
       '"approval_event_sequence": approval,')),
    m("I3-approval-truncation-flag-false", I,
      (RI, '"approval_events_truncated": len(approval) > MAX_INSPECTED_EVENTS,',
       '"approval_events_truncated": False,')),
    m("I4-digest-covers-displayed-rows-only", I,
      (RI, '"event_sequence_digest": _digest(rows),',
       '"event_sequence_digest": _digest(rows[:MAX_INSPECTED_EVENTS]),')),
    m("I5-event-rows-unbounded", I,
      (RI, '"events": rows[:MAX_INSPECTED_EVENTS],', '"events": rows,')),
    m("I6-failed-or-rejected-plan-not-acknowledgeable", I,
      (RI, "acknowledgeable = error is not None or plan.status in ACKNOWLEDGEABLE_PLAN_STATUSES or (",
       "acknowledgeable = error is not None or (")),
    m("I7-prohibited-terminal-plan-not-acknowledgeable", I,
      (RI, "        and decision.policy_class == ApprovalPolicyClass.PROHIBITED\n",
       "        and False\n")),
    m("I8-any-task-acknowledgeable", I,
      (RI, '"acknowledgeable": manual_review,', '"acknowledgeable": True,')),
    m("I9-token-pattern-not-applied", I,
      (RI, "return text if isinstance(text, str) and _TOKEN.fullmatch(text) else UNRECOGNIZED",
       "return text")),
    m("L1-chain-link-not-verified", L,
      (RA, '                        or event["previous"] != previous\n', "")),
    m("L2-event-hash-not-verified", L,
      (RA, 'if event["hash"] != fingerprint(material):', "if False:")),
    m("L3-event-bound-back-to-4-kib", L,
      (RA, "MAX_EVENT_BYTES = 8192", "MAX_EVENT_BYTES = 4096")),
    m("L4-surrounding-whitespace-allowed", L,
      (RA, "        and reason == reason.strip()\n", "")),
    m("L5-format-characters-allowed", L,
      (RA, 'unicodedata.category(character).startswith(("C", "Z"))',
       'unicodedata.category(character).startswith(("Cc", "Z"))')),
    m("L6-history-count-not-incremented", L,
      (RA, 'history = latest.get(key, {}).get("history_count", 0) + 1',
       'history = 1')),
    m("L7-symlinked-namespace-accepted-on-write", L,
      (RA, "            if self.root.is_symlink():\n"
           '                raise AcknowledgementStorageError("acknowledgement_namespace_invalid")\n'
           "            self.root.mkdir(",
       "            self.root.mkdir(")),
    m("W1-csrf-binding-shared-across-administrators", W,
      (AW, "self._record_csrf[(principal, kind, record_id)] = (", 'self._record_csrf[("", kind, record_id)] = ('),
      (AW, "self._record_csrf.pop((principal, kind, record_id), None)",
       'self._record_csrf.pop(("", kind, record_id), None)')),
    m("W2-binding-principal-not-compared", W,
      (AW, "            binding = self._record_csrf.pop((principal, kind, record_id), None)\n"
           '            supplied = hashlib.sha256(one("csrf")[:256].encode()).hexdigest()\n'
           "            if binding is None or binding[1] != principal or",
       "            binding = self._record_csrf.pop((principal, kind, record_id), None)\n"
       '            supplied = hashlib.sha256(one("csrf")[:256].encode()).hexdigest()\n'
       "            if binding is None or")),
    m("W3-binding-fingerprint-not-compared", W,
      (AW, '            if not hmac.compare_digest(binding[2], one("record_fingerprint")[:64]):\n'
           "                raise ValueError",
       "            if False:\n                raise ValueError")),
    m("W4-governance-unavailable-unhandled", W,
      (AW, "        try:\n            governance = self.governance.require()\n        except Exception:",
       "        governance = self.governance.require()\n        if False:")),
    m("W5-acknowledge-route-administrator-gate-skipped", W,
      (AW, 'context = await self._require_administrator(request, "record_acknowledge")',
       'context = (request.headers.get("X-Ingress-Path", ""), '
       '"home_assistant_admin_ingress:" + request.headers.get("X-Remote-User-Id", "x"))')),
    m("W6-review-route-administrator-gate-skipped", W,
      (AW, 'context = await self._require_administrator(request, "record_review")',
       'context = (request.headers.get("X-Ingress-Path", ""), '
       '"home_assistant_admin_ingress:" + request.headers.get("X-Remote-User-Id", "x"))')),
    m("W7-form-offered-for-non-acknowledgeable-record", W,
      (AW, 'if view.get("acknowledgeable") and view.get("record_fingerprint"):',
       'if view.get("record_fingerprint"):')),
]

# Expected survivors, each with the reason it cannot change behaviour.
EQUIVALENT = {
    "A21-health-memo-not-cleared-after-write": (
        "The memo key includes the ledger token (inode, size, mtime_ns). Every "
        "append replaces the file, so the token changes and the memo misses; "
        "the explicit clear is defence in depth."
    ),
    "W2-binding-principal-not-compared": (
        "The binding is keyed by (principal, kind, record_id), so another "
        "administrator's pop never finds the binding; the stored-principal "
        "comparison is defence in depth."
    ),
}


def tracked_top_level() -> set[str]:
    output = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True,
                            capture_output=True, text=True).stdout
    return {line.split("/", 1)[0] for line in output.splitlines() if line}


def apply(tree: Path, mutation: dict) -> dict[Path, str]:
    """Apply one mutant; return the originals. Each fragment must match once."""

    originals: dict[Path, str] = {}
    current: dict[Path, str] = {}
    for relative, old, new in mutation["replacements"]:
        path = tree / relative
        text = current.get(path, path.read_text(encoding="utf-8"))
        originals.setdefault(path, text)
        if text.count(old) != 1:
            raise SystemExit(f"{mutation['id']}: fragment matches {text.count(old)} times in {relative}")
        current[path] = text.replace(old, new)
    for path, text in current.items():
        path.write_text(text, encoding="utf-8")
    return originals


def run_tests(tree: Path, scratch: Path, timeout: int) -> tuple[int, str, float]:
    started = time.monotonic()
    outputs, code = [], 0
    for module in MODULES:
        directory = scratch / f"run-{os.getpid()}-{time.monotonic_ns()}"
        directory.mkdir(parents=True)
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", module],
                cwd=tree, capture_output=True, text=True, timeout=timeout,
                env={**os.environ, "TMPDIR": str(directory)},
            )
        except subprocess.TimeoutExpired:
            return 124, "TIMEOUT", time.monotonic() - started
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        outputs.append(f"### {module}\n{completed.stdout}{completed.stderr}")
        code = code or completed.returncode
    return code, "\n".join(outputs), time.monotonic() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--only", action="append", default=[],
                        help="run only these mutation ids (repeatable); the baseline always runs")
    arguments = parser.parse_args()
    work = arguments.work.expanduser().resolve()
    if ROOT / ".artifacts" not in work.parents:
        raise SystemExit("--work must be under the worktree's .artifacts/")
    tree, log_dir, scratch = work / "tree", work / "mutation-logs", work / "tmp"
    if tree.exists():
        shutil.rmtree(tree)
    top = tracked_top_level()
    common = shutil.ignore_patterns(".git", ".artifacts", "__pycache__", "*.pyc")

    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = set(common(directory, names))
        if Path(directory) == ROOT:  # untracked top-level entries stay out
            skipped |= {name for name in names if name not in top}
        return skipped

    shutil.copytree(ROOT, tree, symlinks=True, ignore=ignore)
    log_dir.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    identifiers = [item["id"] for item in MUTATIONS]
    if len(set(identifiers)) != len(identifiers):
        raise SystemExit("duplicate mutation id")
    if set(EQUIVALENT) - set(identifiers):
        raise SystemExit("equivalent id without a mutant")
    unknown = set(arguments.only) - set(identifiers)
    if unknown:
        raise SystemExit(f"unknown mutation ids: {sorted(unknown)}")
    for mutation in MUTATIONS:  # every fragment matches once before anything runs
        for path, text in apply(tree, mutation).items():
            path.write_text(text, encoding="utf-8")

    code, output, seconds = run_tests(tree, scratch, arguments.timeout)
    (log_dir / "baseline.log").write_text(output, encoding="utf-8")
    ran = [int(value) for value in re.findall(r"^Ran (\d+) tests?", output, re.MULTILINE)]
    baseline = {"modules": list(MODULES), "returncode": code, "tests": sum(ran),
                "seconds": round(seconds, 1)}
    print(f"baseline rc={code} tests={sum(ran)} ({seconds:.0f}s)", flush=True)
    if code != 0:
        print(output[-4000:])
        return 2

    results = []
    for mutation in MUTATIONS:
        if arguments.only and mutation["id"] not in arguments.only:
            continue
        originals = apply(tree, mutation)
        try:
            code, output, seconds = run_tests(tree, scratch, arguments.timeout)
        finally:
            for path, text in originals.items():
                path.write_text(text, encoding="utf-8")
        found = _FAILURE_LINE.findall(output)
        assertions = [f"FAIL {name} ({where})" for kind, name, where in found if kind == "FAIL"]
        errors = [f"ERROR {name} ({where})" for kind, name, where in found if kind == "ERROR"]
        timed_out = output == "TIMEOUT"
        killed = bool(assertions) and not timed_out
        verdict = ("killed" if killed else "timeout" if timed_out
                   else "error_only" if errors or code != 0 else "survived")
        log_path = log_dir / f"{mutation['id']}.log"
        log_path.write_text(output, encoding="utf-8")
        results.append({
            "id": mutation["id"], "class": mutation["class"],
            "replacements": [list(item) for item in mutation["replacements"]],
            "verdict": verdict, "killed": killed, "returncode": code,
            "assertion_failures": len(assertions), "errors": len(errors),
            "first_assertion_failures": assertions[:3], "first_errors": errors[:3],
            "expected_equivalent": mutation["id"] in EQUIVALENT,
            "equivalence_reason": EQUIVALENT.get(mutation["id"]),
            "log_path": log_path.relative_to(work).as_posix(), "seconds": round(seconds, 1),
        })
        print(f"{verdict.upper():10} {mutation['id']:52} {(assertions or errors)[:1]}", flush=True)

    not_killed = [item["id"] for item in results if not item["killed"]]
    unexpected = [identifier for identifier in not_killed if identifier not in EQUIVALENT]
    document = {
        "baseline": baseline, "mutation_count": len(results),
        "killed": sum(item["killed"] for item in results),
        "not_killed": not_killed, "equivalent": {key: EQUIVALENT[key] for key in not_killed if key in EQUIVALENT},
        "unexpected_survivors": unexpected,
        "kill_rule": "at least one FAIL (assertion) line; ERROR-only and timeouts are not kills",
        "python": platform.python_version(), "results": results,
    }
    arguments.output.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    return 1 if unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())
