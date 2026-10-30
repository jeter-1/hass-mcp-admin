"""HAMCP-198 mutation runner (offline, deterministic).

Reuses the HAMCP-131 runner's machinery: a disposable copy of the tracked
tree, every fragment matching exactly once before anything runs, and
fail-fast suites per mutant. ``test_hamcp198_projection_diagnostics`` runs
first, then the HAMCP-162 admission suite and the projection suites the
health summary depends on.

Per AGENTS.md (HAMCP-237), the work directory and every mutant's TMPDIR live
under the worktree's ignored ``.artifacts/``; the copy skips ``.artifacts``,
so it never copies itself.

Classes:
- ``C*``: a projection failure misclassified (a check removed, inverted or
  reordered, the approval-code map wrong, the exception fallback removed);
- ``B*``: the health identification unbounded or mis-truncated, unsorted, or
  echoing more than ``{plan_id, category}``;
- ``H*``: the health or unconfigured-runtime fields dropped or constant;
- ``G*``: the gateway accounting flag reverted to the stock-only model, or
  true for an unaccounted or unreviewed advertised tool.

Runs (``results.json`` is the last, complete one):
- Run 1 (complete): baseline OK in 83 s; 26 of 29 killed. G1, G3 and G4
  survived: the one negative made the extra tool both unaccounted and
  unreviewed, so either check alone kept the flag false. The missing,
  duplicate and count-balanced-unreviewed shapes were added; a pre-screen
  of G1, G3 and G4 killed all three.
- Run 2 (complete): baseline OK in 81 s (117 tests); 29 of 29 killed,
  0 survivors, 0 timeouts.

Usage:
    systemd-run --user --scope -p MemoryMax=3G -p OOMPolicy=continue \\
        python docs/evidence/hamcp198-mutations/run_mutations.py \\
        --work .artifacts/hamcp198-mut --output docs/evidence/hamcp198-mutations/results.json
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

import run_mutations as base  # noqa: E402  (the HAMCP-131 runner)

m = base.m
E = base.E
PD = f"{E}/governance/projection_diagnostics.py"
SV = f"{E}/governance/service.py"
RT = f"{E}/governance/runtime.py"
GW = f"{E}/providers/upstream_read_gateway.py"
CLASSIFY = "a projection failure misclassified"
BOUND = "health identification unbounded, unsorted or leaking"
FIELDS = "a health field dropped or constant"
ACCOUNT = "accounting flag masking an unaccounted or unreviewed tool"

MUTATIONS = [
    m("C1-missing-decision-gets-hash-category", CLASSIFY,
      (PD, '        return "category_unavailable"\n    if not persisted',
       '        return "policy_decision_hash_not_reproducible"\n    if not persisted')),
    m("C2-integrity-check-removed", CLASSIFY,
      (PD, "    if not persisted_policy_snapshot_integrity_matches(plan):", "    if False:")),
    m("C3-subject-decision-hash-swapped", CLASSIFY,
      (PD, "        if not hmac.compare_digest(", "        if hmac.compare_digest(")),
    m("C4-approval-hash-binding-unchecked", CLASSIFY,
      (PD, "        approval.policy_decision_hash != decision.policy_decision_hash\n        or ", "        ")),
    m("C5-approval-class-binding-unchecked", CLASSIFY,
      (PD, "        or approval.policy_class != decision.policy_class.value\n", "")),
    m("C6-historical-approval-only-check-removed", CLASSIFY,
      (PD, "    if historical_policy_projection_has_only_approval_mismatch(plan):", "    if False:")),
    m("C7-approval-codes-before-snapshot-checks", CLASSIFY,
      (PD, "    decision = plan.policy_decision\n",
       "    if error_code in _APPROVAL_CATEGORIES:\n"
       "        return _APPROVAL_CATEGORIES[error_code]\n"
       "    decision = plan.policy_decision\n")),
    m("C8-approval-principal-sequence-swapped", CLASSIFY,
      (PD, '    ErrorCode.APPROVAL_PRINCIPAL_MISMATCH: "approval_principal_mismatch",',
       '    ErrorCode.APPROVAL_PRINCIPAL_MISMATCH: "approval_sequence_inconsistent",')),
    m("C9-non-snapshot-code-guard-removed", CLASSIFY,
      (PD, "    if error_code is not ErrorCode.POLICY_SNAPSHOT_MISMATCH:", "    if False:")),
    m("C10-current-drift-check-removed", CLASSIFY,
      (PD, "        if not policy_snapshot_matches(plan):", "        if False:")),
    m("C11-superseded-drift-check-removed", CLASSIFY,
      (PD, "    elif not persisted_historical_policy_snapshot_matches(plan):", "    elif False:")),
    m("C12-policy-version-test-inverted", CLASSIFY,
      (PD, "    if decision.policy_version == POLICY_VERSION:", "    if decision.policy_version != POLICY_VERSION:")),
    m("C13-exception-fallback-removed", CLASSIFY,
      (PD, "    except Exception:", "    except ZeroDivisionError:")),
    m("B1-row-limit-nine", BOUND,
      (PD, "MAX_HEALTH_PROJECTION_FAILURES = 8", "MAX_HEALTH_PROJECTION_FAILURES = 9")),
    m("B2-truncated-at-exactly-eight", BOUND,
      (PD, "    return rows, len(ordered) > MAX_HEALTH_PROJECTION_FAILURES",
       "    return rows, len(ordered) >= MAX_HEALTH_PROJECTION_FAILURES")),
    m("B3-truncation-never-reported", BOUND,
      (PD, "    return rows, len(ordered) > MAX_HEALTH_PROJECTION_FAILURES", "    return rows, False")),
    m("B4-rows-unbounded", BOUND,
      (PD, "        for plan, error_code in ordered[:MAX_HEALTH_PROJECTION_FAILURES]",
       "        for plan, error_code in ordered")),
    m("B5-rows-reverse-sorted", BOUND,
      (PD, "    ordered = sorted(failures, key=lambda item: item[0].plan_id)",
       "    ordered = sorted(failures, key=lambda item: item[0].plan_id, reverse=True)")),
    m("B6-row-echoes-error-code", BOUND,
      (PD, '            "category": projection_failure_category(plan, error_code),\n',
       '            "category": projection_failure_category(plan, error_code),\n'
       '            "error_code": error_code.value,\n')),
    m("B7-row-echoes-policy-subject", BOUND,
      (PD, '            "category": projection_failure_category(plan, error_code),\n',
       '            "category": projection_failure_category(plan, error_code),\n'
       '            "summary": str(plan.config),\n')),
    m("H1-health-rows-dropped", FIELDS,
      (SV, '            "projection_failures": failure_rows,', '            "projection_failures": [],')),
    m("H2-health-truncation-constant", FIELDS,
      (SV, '            "projection_failures_truncated": failures_truncated,',
       '            "projection_failures_truncated": False,')),
    m("H3-health-category-model-dropped", FIELDS,
      (SV, '            "projection_failure_category_model": (\n                PROJECTION_FAILURE_CATEGORY_MODEL\n            ),\n', "")),
    m("H4-runtime-default-rows-dropped", FIELDS,
      (RT, '                "projection_failures": [],\n', "")),
    m("H5-runtime-default-truncated-true", FIELDS,
      (RT, '                "projection_failures_truncated": False,', '                "projection_failures_truncated": True,')),
    m("G1-unreviewed-ignored", ACCOUNT,
      (GW, "            == len(catalog.tools)\n            and not evaluation.unreviewed\n        )\n        self._replace",
       "            == len(catalog.tools)\n        )\n        self._replace")),
    m("G2-reviewed-flag-stock-only", ACCOUNT,
      (GW, '                    "reviewed_tool_accounting_valid": (\n                        optional_accounting_valid',
       '                    "reviewed_tool_accounting_valid": (\n'
       '                        len(policy.tools) == len(catalog.tools)\n'
       '                        and not evaluation.unreviewed')),
    m("G3-count-check-removed", ACCOUNT,
      (GW, "            len(policy.tools) + len(optional.accounted_names)\n            == len(catalog.tools)\n            and not",
       "            True\n            and not")),
    m("G4-count-check-at-least", ACCOUNT,
      (GW, "            len(policy.tools) + len(optional.accounted_names)\n            == len(catalog.tools)",
       "            len(policy.tools) + len(optional.accounted_names)\n            <= len(catalog.tools)")),
]

base.MODULES = ("test_hamcp198_projection_diagnostics", "test_hamcp162_yaml_admission",
                "test_terminal_history_projection", "test_beta34_historical_policy_projection",
                "test_governance_async_health")
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
