"""HAMCP-249 mutation runner (offline, deterministic).

Reuses the HAMCP-131 runner's machinery: a disposable copy of the tracked
tree, every fragment matching exactly once before anything runs, and
fail-fast suites per mutant. ``test_hamcp249_pre_dispatch_reasons`` runs
first, then the existing F3 runtime and governance suites the change touches.

Per AGENTS.md (HAMCP-237), the work directory and every mutant's TMPDIR live
under the worktree's ignored ``.artifacts/``; the copy skips ``.artifacts``,
so it never copies itself.

Classes:
- ``S*``: a pre-dispatch refusal reason dropped at one reporting surface (the
  parent summary, the parent ``last_error``, the ``preflight_failed`` event
  and add-on log ``error_code``, the child view, the apply result, the task
  view, the repeat-apply details, the legacy F1 ``failure_information``);
- ``C*``: the reason misclassified (priority, verifier rule, adapter codes);
- ``P*``: persistence and legacy semantics (terminal recovery rewriting a
  stored task, a persisted reason not preferred, a legacy reason fabricated
  or a derivable one lost, an unreviewed stored shape accepted);
- ``B*``: bounding and sanitization of identifiers and detail text.

Runs (``results.json`` is the last, complete one):
- Runs 1 and 2 were stopped. A 900 s per-mutant timeout masked S7 (the
  baseline took 860-872 s) and counted as a kill, and C1 survived the
  single-code tests. The timeout is now 2400 s and a timeout counts as a
  survivor. The pairwise priority, mismatch-category, F1-through-F3-view and
  L1 tests were added.
- Run 3 was stopped after P2. P1 survived (recovery never reached the
  terminal-projection path) and so did P2 (no stored reason that differed
  from the derived one). A pre-screen of the P and B mutants against the
  HAMCP-249 module also found P6. The terminal-projection reconcile,
  persisted-reason-wins and projected-fields tests were added.
- Run 4 (complete, final tests): baseline OK in 929 s; 29 of 29 killed, 0 survivors, 0 timeouts.

Usage:
    systemd-run --user --scope -p MemoryMax=3G -p OOMPolicy=continue \\
        python docs/evidence/hamcp249-mutations/run_mutations.py \\
        --work .artifacts/hamcp249-mut --output docs/evidence/hamcp249-mutations/results.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hamcp131-mutations"))

import run_mutations as base  # noqa: E402

m = base.m
E = base.E
RT = f"{E}/f3_runtime/runtime.py"
SV = f"{E}/governance/service.py"
AD = f"{E}/f3_configuration/adapter.py"
NZ = f"{E}/governance/normalize.py"
PR = f"{E}/f3_runtime/pre_dispatch_reasons.py"

S = "a pre-dispatch refusal reason dropped at one reporting surface"
C = "the refusal reason misclassified"
P = "persistence or legacy semantics of the reason"
B = "identifier bounding or sanitization"

MUTATIONS = [
    m("S1-parent-summary-reason-dropped", S,
      (RT, "summary[pre_dispatch_reasons.SUMMARY_KEY] = reason", "pass")),
    m("S2-parent-last-error-dropped", S,
      (RT, 'failed_changes["last_error"] = {', "_dropped = {")),
    m("S3-preflight-failed-event-error-code-dropped", S,
      (RT, 'error_code=reason["error_code"],', "error_code=None,")),
    m("S4-terminal-event-ignores-error-code", S,
      (RT, "result_status=result_status, error_code=error_code,", "result_status=result_status,")),
    m("S5-child-view-reason-dropped", S,
      (RT, '"pre_dispatch_reason": pre_dispatch_reasons.child_reason(',
       '"pre_dispatch_reason": None and pre_dispatch_reasons.child_reason(')),
    m("S6-apply-result-reason-dropped", S,
      (RT, "            result[pre_dispatch_reasons.SUMMARY_KEY] = refusal", "            pass")),
    m("S7-f1-task-view-reason-dropped", S,
      (RT, "            refusal = self.pre_dispatch_refusal(task)\n            if refusal is not None:\n"
           "                value[pre_dispatch_reasons.SUMMARY_KEY] = refusal",
       "            refusal = self.pre_dispatch_refusal(task)\n            if refusal is not None:\n"
       "                pass")),
    m("S8-f3-task-view-reason-dropped", S,
      (RT, "        refusal = self.pre_dispatch_refusal(task, rows=rows)\n        if refusal is not None:\n"
           "            value[pre_dispatch_reasons.SUMMARY_KEY] = refusal",
       "        refusal = self.pre_dispatch_refusal(task, rows=rows)\n        if refusal is not None:\n"
       "            pass")),
    m("S9-repeat-apply-details-reason-dropped", S,
      (SV, "details[pre_dispatch_reasons.SUMMARY_KEY] = (", "_dropped = (")),
    m("S10-f1-manual-review-failure-information-dropped", S,
      (SV, "pre_dispatch_reasons.legacy_f1_reason(\n                                reason, task.target\n"
           "                            )", "None")),
    m("S11-f1-apply-failure-information-dropped", S,
      (SV, "pre_dispatch_reasons.legacy_f1_reason(\n                                error_code, task.target\n"
           "                            )", "None")),
    m("C1-classify-priority-reversed", C,
      (PR, "    for code, check in _REASONS:\n        if code in present:",
       "    for code, check in reversed(_REASONS):\n        if code in present:")),
    m("C2-verifier-rule-never-selected", C,
      (PR, "if code in AUTOMATION_VERIFIER_DETAILS),", "if False),")),
    m("C3-verifier-rule-not-in-detail", C,
      (PR, "    if rule is not None:\n        detail +=", "    if False:\n        detail +=")),
    m("C4-adapter-verifier-rule-code-dropped", C,
      (AD, "*(() if detail is None else (detail,)),", "*(),")),
    m("C5-adapter-mismatch-categories-dropped", C,
      (AD, "*comparison.mismatch_categories,", "")),
    m("C6-verifier-detail-code-outside-closed-set", C,
      (NZ, "code = AUTOMATION_VERIFIER_DETAIL_PREFIX + slug", "code = AUTOMATION_VERIFIER_DETAIL_PREFIX + slug + \"_x\"")),
    m("C7-unclassified-code-reported-as-unknown-legacy", C,
      (PR, 'return UNCLASSIFIED, "adapter_preflight", rule', "return UNKNOWN_LEGACY, UNKNOWN_LEGACY, rule")),
    m("P1-terminal-recovery-rewrites-stored-task", P,
      (RT, "if not terminal_recovery:\n                        summary[pre_dispatch_reasons.SUMMARY_KEY]",
       "if True:\n                        summary[pre_dispatch_reasons.SUMMARY_KEY]")),
    m("P2-persisted-reason-not-preferred", P,
      (RT, "        if stored is not None:\n            return stored", "        if False:\n            return stored")),
    m("P3-legacy-f3-derived-reason-lost", P,
      (RT, "                for item in rows\n                if item[", "                for item in rows[:0]\n                if item[")),
    m("P4-missing-codes-fabricate-a-reason", P,
      (PR, "    if not codes:\n        return UNKNOWN_LEGACY, UNKNOWN_LEGACY, None",
       '    if not codes:\n        return UNCLASSIFIED, "adapter_preflight", None')),
    m("P5-recorded-accepts-unreviewed-error-code", P,
      (PR, '    if value.get("error_code") not in REASON_CODES:\n        return None',
       '    if False:\n        return None')),
    m("P6-recorded-returns-unprojected-value", P,
      (PR, "    return {key: value.get(key) for key in REASON_FIELDS}\n\n\ndef unknown_legacy",
       "    return value\n\n\ndef unknown_legacy")),
    m("P7-legacy-f1-stored-reason-ignored", P,
      (PR, 'if isinstance(stored, dict) and stored.get("reason_model") == REASON_MODEL:', "if False:")),
    m("P8-legacy-f1-code-dropped", P,
      (PR, "    if known:\n        reason.update(", "    if False:\n        reason.update(")),
    m("P9-dispatched-child-reported-as-refusal", P,
      (PR, "        and record.dispatch_intent is None\n        and not record.dispatch_count\n"
           "        and record.normalized_outcome in PRE_DISPATCH_OUTCOMES\n",
       "\n")),
    m("B1-identifier-control-characters-kept", B,
      (PR, "    if any(ord(char) < 0x21 or ord(char) > 0x7E for char in value):\n        return None",
       "    if False:\n        return None")),
    m("B2-identifier-length-unbounded", B,
      (PR, "    return value[:MAX_IDENTIFIER_LENGTH]", "    return value")),
]
base.MODULES = ("test_hamcp249_pre_dispatch_reasons", "test_f3_runtime_integration",
                "test_hamcp135_configuration_execution", "test_trigger_condition_verification",
                "test_hamcp181_dispatch_facts", "test_f3_orphan_child_recovery")
base.MUTATIONS = MUTATIONS

_run_tests = base.run_tests
SCRATCH: Path | None = None


def run_tests(tree: Path, timeout: int, *, failfast: bool) -> tuple[int, str, float]:
    scratch = tempfile.mkdtemp(prefix="mutant-", dir=SCRATCH)
    previous = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = scratch
    try:
        return _run_tests(tree, timeout, failfast=failfast)
    finally:
        if previous is None:
            del os.environ["TMPDIR"]
        else:
            os.environ["TMPDIR"] = previous
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:
    """The HAMCP-131 main, with the work directory under ``.artifacts/``."""

    global SCRATCH
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--only", action="append", default=[],
                        help="run only these mutation ids (repeatable); the baseline always runs")
    arguments = parser.parse_args()
    root = base.ROOT
    work = arguments.work.expanduser().resolve()
    artifacts = root / ".artifacts"
    if artifacts not in work.parents:
        raise SystemExit("--work must be under the worktree's .artifacts/")
    tree, log_dir, SCRATCH = work / "tree", work / "mutation-logs", work / "tmp"
    if tree.exists():
        shutil.rmtree(tree)
    top = base.tracked_top_level()
    common = shutil.ignore_patterns(".git", ".artifacts", "__pycache__", "*.pyc")

    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = set(common(directory, names))
        if Path(directory) == root:  # untracked top-level entries stay out
            skipped |= {name for name in names if name not in top}
        return skipped

    shutil.copytree(root, tree, symlinks=True, ignore=ignore)
    log_dir.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if len({item["id"] for item in MUTATIONS}) != len(MUTATIONS):
        raise SystemExit("duplicate mutation id")
    for mutation in MUTATIONS:  # every fragment matches once before anything runs
        for path, text in base.apply(tree, mutation).items():
            path.write_text(text, encoding="utf-8")

    code, output, seconds = run_tests(tree, arguments.timeout, failfast=False)
    baseline_log = log_dir / "baseline.log"
    baseline_log.write_text(output, encoding="utf-8")
    summary = output.strip().splitlines()[-1] if output.strip() else ""
    ran = re.search(r"^Ran (\d+) tests?", output, re.MULTILINE)
    baseline = {"log_path": baseline_log.relative_to(work).as_posix(), "modules": list(base.MODULES),
                "returncode": code, "tests": int(ran.group(1)) if ran else None,
                "seconds": round(seconds, 1), "summary": summary}
    print(f"baseline rc={code} {summary} ({seconds:.0f}s)", flush=True)
    if code != 0:
        print(output[-4000:])
        return 2

    results = []
    unknown = set(arguments.only) - {item["id"] for item in MUTATIONS}
    if unknown:
        raise SystemExit(f"unknown mutation ids: {sorted(unknown)}")
    for mutation in MUTATIONS:
        if arguments.only and mutation["id"] not in arguments.only:
            continue
        originals = base.apply(tree, mutation)
        try:
            code, output, seconds = run_tests(tree, arguments.timeout, failfast=True)
        finally:
            for path, text in originals.items():
                path.write_text(text, encoding="utf-8")
        failures = [f"{kind} {name} ({where})" for kind, name, where in base._FAILURE_LINE.findall(output)]
        log_path = log_dir / f"{mutation['id']}.log"
        log_path.write_text(output, encoding="utf-8")
        results.append({
            # A timeout is not a kill: the fail-fast suites only run to the
            # limit when no earlier test failed.
            **mutation, "log_path": log_path.relative_to(work).as_posix(), "returncode": code,
            "killed": code != 0 and output != "TIMEOUT", "timed_out": output == "TIMEOUT",
            "killed_by_import_or_syntax_error": any(
                marker in output for marker in ("ImportError", "SyntaxError", "Failed to import")),
            "first_failures": failures[:3], "seconds": round(seconds, 1),
        })
        print(f"{'KILLED' if results[-1]['killed'] else 'SURVIVED':8} {mutation['id']:48} {failures[:1]}", flush=True)

    survived = [item["id"] for item in results if not item["killed"]]
    document = {"baseline": baseline, "mutation_count": len(results),
                "killed": sum(item["killed"] for item in results), "survived": survived,
                "python": platform.python_version(), "results": results}
    arguments.output.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
