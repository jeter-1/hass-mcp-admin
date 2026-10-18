"""HAMCP-171 mutation runner (offline, deterministic).

Each mutation is an exact textual replacement in shipped source (every old
fragment must match exactly once), applied to a disposable copy of the
working tree outside the repository. The named test modules then run; a
mutation is killed when they fail. Nothing in the repository is modified.
The machinery is the HAMCP-162 runner's.

Usage:
    python docs/evidence/hamcp171-mutations/run_mutations.py \
        --work ~/tmp/hamcp171-runs/mut --output ~/tmp/hamcp171-runs/mut/results.json

Optional ``--only ID [ID ...]`` reruns a subset; ``--workers N`` sets the
number of tree copies run concurrently (default 2).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
E = "hass_mcp_engineering_beta/ha_mcp_engineering"
SERVICE = f"{E}/governance/service.py"
RUNTIME = f"{E}/f3_runtime/runtime.py"

T_CACHE = "tests.test_hamcp171_health_verdict_cache"
T_SWEEP = "tests.test_hamcp171_sweep_parent_reads"
T_ORPHAN = "tests.test_f3_orphan_child_recovery"
T_ASYNC = "tests.test_governance_async_health"


def m(identifier, item, path, replacements, tests=(T_CACHE,)):
    return {
        "id": identifier,
        "design_item": item,
        "path": path,
        "replacements": [list(pair) for pair in replacements],
        "tests": list(tests),
    }


MUTATIONS = [
    m("A-cache-ignores-sha256", "brief: the cache ignores the content hash", SERVICE, [(
        "else (observed.path, observed.stat, observed.sha256, context)",
        "else (observed.path, observed.stat, \"\", context)",
    )]),
    m("B1-cache-survives-record-change", "brief: the cache survives a record change", SERVICE, [(
        "and self._safety_verdict_cache.get(plan.plan_id) == proof",
        "and plan.plan_id in self._safety_verdict_cache",
    )]),
    m("B2-no-invalidation-on-worker-failure", "D1: a failed scan keeps cached verdicts", SERVICE, [(
        "                    self._safety_verdict_invalidate()\n                    raise",
        "                    raise",
    )]),
    m("B3-no-invalidation-on-abandoned-job-failure",
      "D1: an abandoned job's drained failure keeps cached verdicts", SERVICE, [(
        "                    # caller cancellation (BaseException) must not be swallowed.\n"
        "                    self._safety_verdict_invalidate()",
        "                    # caller cancellation (BaseException) must not be swallowed.\n"
        "                    pass",
    )]),
    m("B4-deep-audit-keeps-verdicts", "D1: the internal deep audit keeps cached verdicts", SERVICE, [(
        "        self._safety_verdict_invalidate()\n        plan = self.repository.rebuild_navigation_index()",
        "        plan = self.repository.rebuild_navigation_index()",
    )]),
    m("F-deep-rescan-uses-cache", "D1: the deep re-scan reuses cached verdicts", SERVICE, [(
        "        if deep:\n            self._safety_verdict_cache.clear()\n",
        "        if deep:\n",
    ), (
        "                not deep\n                and proof is not None\n",
        "                proof is not None\n",
    )]),
    m("C-sweep-skips-needed-parent-read", "brief: the sweep skips a needed parent read", RUNTIME, [(
        "            if record is not None and (\n                record.dispatch_intent is None",
        "            if False and record is not None and (\n                record.dispatch_intent is None",
    )], (T_SWEEP, T_ORPHAN)),
    m("D-worker-on-loop-thread", "GIL/event loop: validation runs on the owner loop", SERVICE, [(
        "job = asyncio.create_task(asyncio.to_thread(\n                    _validate_health_records,",
        "job = asyncio.create_task((lambda f, *a: asyncio.sleep(0, f(*a)))(\n"
        "                    _validate_health_records,",
    )], (T_ASYNC,)),
]

_FAILURE_LINE = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]+)\)", re.MULTILINE)


def apply(tree: Path, mutation: dict) -> dict[Path, str]:
    target = tree / mutation["path"]
    original = target.read_text(encoding="utf-8")
    text = original
    for old, new in mutation["replacements"]:
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{mutation['id']}: fragment matched {count} times: {old!r}")
        text = text.replace(old, new)
    target.write_text(text, encoding="utf-8")
    return {target: original}


def run_tests(tree: Path, modules, timeout: int, *, failfast: bool) -> tuple[int, str, float]:
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH="tests")
    command = [sys.executable, "-m", "unittest"] + (["-f"] if failfast else []) + list(modules)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, cwd=tree, env=environment, capture_output=True, text=True, timeout=timeout,
        )
        code, output = completed.returncode, completed.stdout + completed.stderr
    except subprocess.TimeoutExpired:
        code, output = -1, "TIMEOUT"
    return code, output, time.monotonic() - started


def copy_tree(destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        ROOT, destination,
        ignore=shutil.ignore_patterns(".git", ".artifacts", "__pycache__", "*.pyc"),
        symlinks=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--only", nargs="*")
    arguments = parser.parse_args()
    work = arguments.work.expanduser().resolve()
    if work == ROOT or ROOT in work.parents:
        raise SystemExit("--work must be outside the repository")
    log_dir = arguments.output.expanduser().resolve().parent / "mutation-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    selected = [item for item in MUTATIONS if not arguments.only or item["id"] in arguments.only]
    if len({item["id"] for item in MUTATIONS}) != len(MUTATIONS):
        raise SystemExit("duplicate mutation id")
    trees: "queue.Queue[Path]" = queue.Queue()
    for index in range(arguments.workers):
        tree = work / f"tree-{index}"
        copy_tree(tree)
        trees.put(tree)
    # The unmutated baseline must pass every module the mutations use.
    baseline_modules = sorted({module for item in selected for module in item["tests"]})
    tree = trees.get()
    code, output, seconds = run_tests(tree, baseline_modules, arguments.timeout, failfast=False)
    trees.put(tree)
    baseline_log = log_dir / "baseline.log"
    baseline_log.write_text(output, encoding="utf-8")
    baseline = {"log_path": str(baseline_log), "modules": baseline_modules, "returncode": code, "seconds": round(seconds, 1),
                "summary": output.strip().splitlines()[-1] if output.strip() else ""}
    print(f"baseline rc={code} {baseline['summary']} ({seconds:.0f}s)", flush=True)
    if code != 0:
        print(output[-4000:])
        return 2

    def one(mutation: dict) -> dict:
        tree = trees.get()
        try:
            originals = apply(tree, mutation)
            try:
                code, output, seconds = run_tests(
                    tree, mutation["tests"], arguments.timeout, failfast=True
                )
            finally:
                for path, text in originals.items():
                    path.write_text(text, encoding="utf-8")
        finally:
            trees.put(tree)
        failures = [f"{kind} {name} ({where})" for kind, name, where in _FAILURE_LINE.findall(output)]
        import_error = "ImportError" in output or "SyntaxError" in output or "Failed to import" in output
        log_path = log_dir / f"{mutation['id']}.log"
        log_path.write_text(output, encoding="utf-8")
        result = {
            **mutation,
            "log_path": str(log_path),
            "returncode": code,
            "killed": code != 0,
            "killed_by_import_or_syntax_error": import_error,
            "first_failures": failures[:3],
            "seconds": round(seconds, 1),
        }
        state = "KILLED" if result["killed"] else "SURVIVED"
        print(f"{state:8} {mutation['id']:34} {seconds:5.0f}s {failures[:1]}", flush=True)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=arguments.workers) as pool:
        results = list(pool.map(one, selected))
    survived = [item["id"] for item in results if not item["killed"]]
    report = {
        "baseline": baseline,
        "mutation_count": len(results),
        "killed": len(results) - len(survived),
        "survived": survived,
        "python": sys.version.split()[0],
        "results": results,
    }
    output_path = arguments.output.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"{report['killed']}/{len(results)} killed; survived: {survived}")
    return 0 if not survived else 1


if __name__ == "__main__":
    raise SystemExit(main())
