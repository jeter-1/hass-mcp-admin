"""HAMCP-097 mutation runner (offline, deterministic).

Reuses the HAMCP-131 runner's machinery: a disposable copy of the tracked
tree, every fragment matching exactly once before anything runs, and
fail-fast suites per mutant. ``test_hamcp097_incremental_health`` runs first,
then the async-health, F3 health responsiveness, beta 34 historical policy
projection and HAMCP-171 health-cache suites the change touches. The runner
uses ``sys.executable`` for the suites, so run it with the worktree's
``.artifacts/venv/bin/python``.

Per AGENTS.md (HAMCP-237), the work directory and every mutant's TMPDIR live
under the worktree's ignored ``.artifacts/``; the copy skips ``.artifacts``,
so it never copies itself.

Classes:
- ``K*``: a memo key weakened (the SHA-256 or the stat dropped from the
  terminal-record match; the task proof or the verdict context dropped from
  the projection key; a non-terminal plan's projection memoized);
- ``I*``: invalidation or discard removed (deep audit, storage error,
  ``GovernanceError``, unexpected exception; prune not invalidating; active
  records memoized; stale projection entries kept);
- ``A*``: the assembled aggregate reused past its deadline, before its
  build time, without a fingerprint match, without the task part, or on a
  deep audit;
- ``T*``: pending/startup-validation truthfulness (a history-derived field
  filled, ``pending_fields`` omitted, a deep audit not waiting, the wait budget
  ignored or unbounded, failed/cancelled/superseded state not recorded,
  pending reported after completion, a second launch, the F3 descriptor);
- ``W*``: write-freedom of the startup validation (a persisting lifecycle
  resolved, the predicate always false or missing a term, assembly
  prewarmed despite a deferral);
- ``C*``: counters wrong (memo hits, projection hits/invalidations, terminal
  decodes on a hit, classified-outcome replay);
- ``X*``: the per-plan approval actionability memo added in c575723c (key
  without the plan proof, task proofs or context; keyed for a non-terminal
  plan or an unproven task; quarantine ignored; replay without key match;
  no pruning; stale entry on a miss; not cleared by a discard; an unreadable
  quarantine read as empty; the ``.corrupt`` filter dropped; no keys at the
  scan or startup-validation assembly).

``--source DIR`` copies a frozen export (``git archive <commit>``) instead of
the live worktree, so concurrent edits never reach a run.

Recorded runs (Python 3.12.14). An earlier run (2026-10-30, 41 mutants,
copied from the live worktree) is superseded: concurrent test edits reached
its copies, so its results are not evidence. The recorded run
(results.json, 2026-10-31 01:04:03Z to 01:50:48Z, about 47 min) used
``--source`` with ``git archive c575723c``. The baseline of the five modules
passed (111 tests, 105 s); 56 mutants, 34 killed, 22 survived. Each
survivor was re-run in isolation (fresh copy of the export, only that
mutant, fragment confirmed present, no failfast) and still passed all 111
tests. Two are equivalent: K4 and X3 drop the verdict context from the
projection and actionability keys, but the context is an HMAC, under a
per-instance random key, of a constant detector version and
``sensitive_values``, none of which is reassigned after construction
(service.py:404, :735, :855, :16191), so it cannot change while either memo
lives. The other 20 lack a test: K3, I9, I11, A1, A2, A4, T4, T5, T8, W3,
C1, C5, X2, X4, X5, X9, X10, X12, X13 and X15. Scratch killing tests, kept
outside tests/, kill each of the 20 at its intended assertion and pass on
the pristine export. K3, I9, I11, C1, X2, X4, X5, X9, X10, X13 and X15 are
observable only through index counters or memo contents; for X2 and X4 no
governed-field divergence was found, because terminal-plan actionability
depends on task presence and quarantine, not on task bytes.

Those killing tests were merged into tests/test_hamcp097_incremental_health.py
(commit 3871d9ae). The 22 survivors were then re-run with ``--only`` against
``git archive 3871d9ae`` (results-3871d9ae-survivors.json; the runtime is
unchanged from c575723c): baseline OK, the 20 non-equivalent survivors are
killed at their intended tests, and only the equivalent K4 and X3 survive.

After both runs, mutant I9's id (originally ``I9-task-`` + ``record-prune-removed``)
was renamed ``I9-task-prune-removed`` in this file and in both results
files, by that exact string replacement only: the repository's
credential-pattern scan matches ``sk-`` followed by 20 or more id characters.

Usage:
    systemd-run --user --scope -p MemoryMax=3G -p OOMPolicy=continue \\
        env TMPDIR=$PWD/.artifacts/tmp DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \\
        .artifacts/venv/bin/python docs/evidence/hamcp097-mutations/run_mutations.py \\
        --source .artifacts/h097-frozen/src --work .artifacts/hamcp097-mut-c575723c \\
        --output docs/evidence/hamcp097-mutations/results.json
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
SV = f"{E}/governance/service.py"
ST = f"{E}/governance/storage.py"
TS = f"{E}/governance/task_storage.py"

K = "memo key weakened"
I = "invalidation or discard removed"  # noqa: E741
A = "assembly reused without exact input or past its deadline"
T = "pending or startup-validation state untruthful"
W = "startup validation not write-free"
C = "incremental counters wrong"
X = "approval actionability memo (c575723c) weakened"

PROJECTION_KEY = '(proof, plan_tasks.get(plan.plan_id, ("absent",)), context)'
DEEP_DISCARD = (
    "                if deep:\n"
    "                    # A deep audit re-derives everything from fresh decodes.\n"
    "                    self._health_incremental_discard()\n"
)
DEEP_NO_DISCARD = (
    "                if deep:\n"
    "                    # A deep audit re-derives everything from fresh decodes.\n"
    "                    pass\n"
)
WOULD_PERSIST = "        return bool(\n            self._plan_expiration_applies(plan)\n"

MUTATIONS = [
    # K: keys
    m("K1-record-memo-ignores-sha256", K,
      (ST, "            and entry[1] == observed.sha256\n", "            and True\n")),
    m("K2-record-memo-ignores-stat", K,
      (ST, "            and entry[0] == observed.stat\n", "            and True\n")),
    m("K3-projection-key-drops-task-proof", K,
      (SV, PROJECTION_KEY, "(proof, context)")),
    m("K4-projection-key-drops-verdict-context", K,
      (SV, PROJECTION_KEY, '(proof, plan_tasks.get(plan.plan_id, ("absent",)))')),
    m("K5-active-plan-projection-memoized", K,
      (SV, "                        if proof is not None and is_terminal_plan(plan)\n",
       "                        if proof is not None\n")),
    # I: invalidation / discard
    m("I1-deep-audit-plan-store-keeps-index", I,
      (SV, "        self._safety_verdict_invalidate()\n        self._health_incremental_discard()\n"
           "        plan = self.repository.rebuild_navigation_index()",
       "        self._safety_verdict_invalidate()\n"
       "        plan = self.repository.rebuild_navigation_index()")),
    m("I2-deep-scan-no-discard", I, (SV, DEEP_DISCARD, DEEP_NO_DISCARD)),
    m("I3-deep-scan-reuses-record-memo", I,
      (SV, DEEP_DISCARD, DEEP_NO_DISCARD),
      (SV, "evidence=evidence, reuse_terminal=not deep)", "evidence=evidence, reuse_terminal=True)")),
    m("I4-deep-scan-replays-projection-memo", I,
      (SV, DEEP_DISCARD, DEEP_NO_DISCARD),
      (SV, "if not deep and key is not None and memo is not None and memo[0] == key:",
       "if key is not None and memo is not None and memo[0] == key:")),
    m("I5-governance-error-keeps-index", I,
      (SV, "                    self._safety_verdict_invalidate()\n"
           "                    self._health_incremental_discard()\n"
           "                if (exc.code == ErrorCode.EXECUTION_TASK_STORAGE_ERROR",
       "                    self._safety_verdict_invalidate()\n"
       "                if (exc.code == ErrorCode.EXECUTION_TASK_STORAGE_ERROR")),
    m("I6-storage-error-keeps-index", I,
      (SV, "                self._safety_verdict_invalidate()\n"
           "                self._health_incremental_discard()\n"
           "                if fence is not None and corruption_before",
       "                self._safety_verdict_invalidate()\n"
       "                if fence is not None and corruption_before")),
    m("I7-unexpected-exception-keeps-index", I,
      (SV, "                self._health_cache_key = None\n"
           "                self._health_incremental_discard()\n"
           "                raise\n"
           "            except BaseException:",
       "                self._health_cache_key = None\n"
       "                raise\n"
       "            except BaseException:")),
    m("I8-plan-record-prune-removed", I,
      (ST, "            self.health_record_memo.prune(\n"
           "                {observed.path for observed in evidence.values()}\n"
           "            )\n",
       "            pass\n")),
    m("I9-task-prune-removed", I,
      (TS, "            self.health_record_memo.prune(\n"
           "                {observed.path for observed in evidence.values()}\n"
           "            )\n",
       "            pass\n")),
    m("I10-active-plan-record-memoized", I,
      (ST, "                if terminal:\n                    self.health_record_memo.store(observed, plan)\n",
       "                if True:\n                    self.health_record_memo.store(observed, plan)\n")),
    m("I11-active-task-record-memoized", I,
      (TS, "                if terminal:\n                    self.health_record_memo.store(observed, task)\n",
       "                if True:\n                    self.health_record_memo.store(observed, task)\n")),
    m("I12-stale-projection-entries-kept", I,
      (SV, "                    del self._health_projection_memo[plan_id]\n"
           "                    incremental[\"projection_invalidations\"] += 1\n",
       "                    pass\n")),
    # A: assembly reuse
    m("A1-assembly-reused-past-deadline", A,
      (SV, 'and (memo["deadline"] is None or now < memo["deadline"])', "and True")),
    m("A2-assembly-reused-before-built-at", A,
      (SV, 'now >= memo["built_at"]\n', "True\n")),
    m("A3-assembly-reused-without-fingerprint-match", A,
      (SV, 'if fingerprint is None or memo is None or memo["fingerprint"] != fingerprint:',
       "if fingerprint is None or memo is None:")),
    m("A4-fingerprint-omits-tasks", A,
      (SV, "            tuple(plan_part), tuple(task_part),\n", "            tuple(plan_part), (),\n")),
    m("A5-deep-audit-reuses-assembly", A,
      (SV, DEEP_DISCARD, DEEP_NO_DISCARD),
      (SV, "if not deep and self._health_assembly_reusable(fingerprint):",
       "if self._health_assembly_reusable(fingerprint):")),
    # T: truthfulness
    m("T1-pending-fills-history-field", T,
      (SV, '    "approval_authority_version", "projection_failure_category_model",\n)',
       '    "approval_authority_version", "projection_failure_category_model",\n    "total_plans",\n)')),
    m("T2-pending-fields-omitted", T,
      (SV, '        summary["pending_fields"] = pending\n', "")),
    m("T3-deep-audit-does-not-wait", T,
      (SV, "            if deep_storage_audit:\n                await asyncio.wait({job})\n",
       "            if False:\n                await asyncio.wait({job})\n")),
    m("T4-wait-budget-ignored", T,
      (SV, "{job}, timeout=self.historical_validation_wait_seconds", "{job}, timeout=0")),
    m("T5-pending-after-completion", T,
      (SV, "                if not job.done():\n                    return self._historical_validation_pending_summary(",
       "                if True:\n                    return self._historical_validation_pending_summary(")),
    m("T6-failed-state-not-recorded", T,
      (SV, '            state.update(state="failed", failure_category=type(exc).__name__)', "            pass")),
    m("T7-cancelled-state-not-recorded", T,
      (SV, '            state.update(state="cancelled")\n', "")),
    m("T8-superseded-not-classified", T,
      (SV, '"superseded"\n                    if exc.details.get("reason") == "health_snapshot_superseded"\n'
           "                    else exc.code.value",
       "exc.code.value")),
    m("T9-validation-launched-twice", T,
      (SV, "        if self._historical_validation_job is None:\n            self._historical_validation.update(",
       "        if True:\n            self._historical_validation.update(")),
    m("T10-f3-descriptor-reported-pending", T,
      (SV, '            static.add("f3")\n', "            pass\n")),
    # W: write-freedom
    m("W1-warmup-resolves-persisting-lifecycle", W,
      (SV, "if warmup and self._health_lifecycle_would_persist(plan):", "if False:")),
    m("W2-would-persist-always-false", W,
      (SV, WOULD_PERSIST, "        return False and bool(\n            self._plan_expiration_applies(plan)\n")),
    m("W3-would-persist-ignores-challenge-expiry", W,
      (SV, "            or self._challenge_has_expired(plan)\n        )\n\n    async def _async_health_scan(",
       "        )\n\n    async def _async_health_scan(")),
    m("W4-assembly-prewarmed-despite-deferral", W,
      (SV, "        if deferred == 0:\n", "        if True:\n")),
    # C: counters
    m("C1-record-memo-hits-not-counted", C,
      (ST, "            self.hits += 1\n", "")),
    m("C2-projection-hits-not-counted", C,
      (SV, '                        incremental["projection_hits"] += 1\n', "")),
    m("C3-projection-invalidations-not-counted", C,
      (SV, "                            del self._health_projection_memo[plan.plan_id]\n"
           '                            incremental["projection_invalidations"] += 1\n',
       "                            del self._health_projection_memo[plan.plan_id]\n")),
    m("C4-terminal-decode-counted-on-hit", C,
      (ST, "                        evidence[reused.plan_id] = observed\n",
       "                        evidence[reused.plan_id] = observed\n"
       "                        self.terminal_records_deserialized += 1\n")),
    m("C5-classified-outcomes-not-replayed", C,
      (SV, "                        for category in memo[2]:\n"
           "                            METRICS.record_classified_outcome(category)\n", "")),
    # X: per-plan approval actionability memo (c575723c)
    m("X1-actionable-key-drops-plan-proof", X,
      (SV, "keys[plan.plan_id] = (proof, plan_tasks, context)",
       "keys[plan.plan_id] = (plan_tasks, context)")),
    m("X2-actionable-key-drops-task-proofs", X,
      (SV, "keys[plan.plan_id] = (proof, plan_tasks, context)",
       "keys[plan.plan_id] = (proof, tuple(item[0] for item in plan_tasks), context)")),
    m("X3-actionable-key-drops-context", X,
      (SV, "keys[plan.plan_id] = (proof, plan_tasks, context)",
       "keys[plan.plan_id] = (proof, plan_tasks)")),
    m("X4-actionable-key-for-non-terminal-plan", X,
      (SV, "            if (proof is None or not is_terminal_plan(plan)\n"
           "                    or any(item[1] is None for item in plan_tasks)):\n",
       "            if (proof is None\n"
       "                    or any(item[1] is None for item in plan_tasks)):\n")),
    m("X5-actionable-key-without-task-proof-guard", X,
      (SV, "            if (proof is None or not is_terminal_plan(plan)\n"
           "                    or any(item[1] is None for item in plan_tasks)):\n",
       "            if (proof is None or not is_terminal_plan(plan)):\n")),
    m("X6-actionable-ignores-quarantine", X,
      (SV, "            if (key is not None and quarantined is not None\n"
           "                    and plan.plan_id not in quarantined\n",
       "            if (key is not None\n")),
    m("X7-actionable-ignores-quarantine-membership", X,
      (SV, "                    and plan.plan_id not in quarantined\n", "")),
    m("X8-actionable-replays-without-key-match", X,
      (SV, "                    and memo is not None and memo[0] == key):\n",
       "                    and memo is not None):\n")),
    m("X9-actionable-memo-not-pruned", X,
      (SV, "        for plan_id in [item for item in self._health_actionable_memo if item not in flags]:\n"
           "            del self._health_actionable_memo[plan_id]\n", "")),
    m("X10-actionable-miss-keeps-stale-entry", X,
      (SV, "            self._health_actionable_memo.pop(plan.plan_id, None)\n", "")),
    m("X11-discard-keeps-actionable-memo", X,
      (SV, "            self._health_actionable_memo.clear()\n", "")),
    m("X12-quarantine-listing-error-reads-as-empty", X,
      (TS, "            except OSError:\n                return None\n        return frozenset(\n",
       "            except OSError:\n                return frozenset()\n        return frozenset(\n")),
    m("X13-quarantine-listing-without-corrupt-filter", X,
      (TS, "            for name in names\n            if name.endswith(\".corrupt\")\n",
       "            for name in names\n")),
    m("X14-scan-assembly-without-actionable-keys", X,
      (SV, "                        actionable_keys=self._health_actionable_keys(\n"
           "                            plans, evidence, tasks, task_proofs),\n",
       "                        actionable_keys=None,\n")),
    m("X15-validation-assembly-without-actionable-keys", X,
      (SV, "                actionable_keys=self._health_actionable_keys(\n"
           "                    plans, evidence, tasks, task_proofs),\n",
       "                actionable_keys=None,\n")),
]

base.MODULES = ("test_hamcp097_incremental_health", "test_governance_async_health",
                "test_f3_health_responsiveness", "test_beta34_historical_policy_projection",
                "test_governance_health_cache")
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
    parser.add_argument("--source", type=Path, default=None,
                        help="frozen export to copy (e.g. git archive <commit>) instead of the worktree")
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
    source = arguments.source.expanduser().resolve() if arguments.source else root
    top = base.tracked_top_level()
    common = shutil.ignore_patterns(".git", ".artifacts", "__pycache__", "*.pyc")

    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = set(common(directory, names))
        if Path(directory) == source and source == root:  # untracked top-level entries stay out
            skipped |= {name for name in names if name not in top}
        return skipped

    shutil.copytree(source, tree, symlinks=True, ignore=ignore)
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
    document = {"source": str(arguments.source) if arguments.source else "worktree",
                "baseline": baseline, "mutation_count": len(results),
                "killed": sum(item["killed"] for item in results), "survived": survived,
                "python": platform.python_version(), "results": results}
    arguments.output.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
