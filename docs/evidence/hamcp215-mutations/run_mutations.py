"""HAMCP-215 mutation runner (offline, deterministic).

Each mutation is an exact textual replacement in shipped source (every old
fragment must match exactly once), applied to a disposable copy of the
working tree outside the repository. The named test modules then run; a
mutation is killed when they fail. Nothing in the repository is modified.
The machinery is the HAMCP-212 runner's (itself HAMCP-209's and HAMCP-171's).

Usage:
    python docs/evidence/hamcp215-mutations/run_mutations.py \
        --work ~/.cache/hamcp215/mut --output ~/.cache/hamcp215/mut/results.json

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
RUNTIME = f"{E}/ha_core_readmission/runtime.py"

T_215 = "tests.test_hamcp215_core_authority_recovery"


def m(identifier, item, path, replacements, tests=(T_215,)):
    return {
        "id": identifier,
        "design_item": item,
        "path": path,
        "replacements": [list(pair) for pair in replacements],
        "tests": list(tests),
    }


def skip_log(event):
    """Drop one log event at the single emission point."""
    return (
        "        for level, event, message, context in records:\n",
        "        for level, event, message, context in records:\n"
        f"            if event == {event!r}:\n"
        "                continue\n",
    )


MUTATIONS = [
    m("R1-no-recovery-loop",
      "brief: no recovery loop (a transient waits for the next periodic tick or never recovers)", RUNTIME, [(
        "                    retry = recovering or not (\n",
        "                    retry = False and (\n",
    )]),
    m("R1b-fixed-interval-retry",
      "brief: recovery reverts to the base fixed reconciliation-interval retry (300 s)", RUNTIME, [(
        "                    await sleep(delay)\n"
        "                    trigger = \"failure_retry\"\n",
        "                    await sleep(interval_seconds)\n"
        "                    trigger = \"failure_retry\"\n",
    )]),
    m("R2-restore-without-confirmation",
      "brief: no hysteresis (the first complete success after a transient republishes)", RUNTIME, [(
        "                            or self._recovery_confirmations < self._recovery_required\n",
        "                            or self._recovery_confirmations < 1\n",
    )]),
    m("R3-restore-skips-recovery-gate",
      "brief: restore without the recovery verification gate at all", RUNTIME, [(
        "                if failure_reason is None and self._recovery_active:\n",
        "                if False:\n",
    )]),
    m("R4-restore-without-lifecycle-fence",
      "brief: restore without full re-verification (no authenticated lifecycle watcher)", RUNTIME, [(
        "        if monitor_required and not monitor_ready:\n",
        "        if False:\n",
    )]),
    m("R5-confirmation-key-ignored",
      "design: confirmations of different versions/contracts count toward one streak", RUNTIME, [(
        "                        elif key == self._recovery_confirmation_key:\n",
        "                        elif self._recovery_confirmation_key is not None:\n",
    )]),
    m("R6-failure-keeps-confirmations",
      "brief: a failure during confirmation does not restart the count", RUNTIME, [(
        "        self._consecutive_failures += 1\n"
        "        self._recovery_confirmations = 0\n"
        "        self._recovery_confirmation_key = None\n",
        "        self._consecutive_failures += 1\n",
    )]),
    m("R7-connection-move-keeps-confirmations",
      "design: a connection move during confirmation does not restart the count", RUNTIME, [(
        "                elif self._recovery_active and self._recovery_confirmations:\n",
        "                elif False:\n",
    )]),
    m("R8-on-demand-trigger-confirms",
      "design: on-demand verification (mutation pre-dispatch) counts as a recovery confirmation", RUNTIME, [
        ("                        if trigger not in _RECOVERY_CONFIRMATION_TRIGGERS:\n",
         "                        if False:\n"),
        ("                            trigger not in _RECOVERY_CONFIRMATION_TRIGGERS\n",
         "                            False\n"),
    ]),
    m("R9-no-flap-escalation",
      "brief: flapping is not escalated (same confirmations inside the flap window)", RUNTIME, [(
        "                CORE_RECOVERY_FLAP_CONFIRMATIONS if flap\n",
        "                CORE_RECOVERY_CONFIRMATIONS if flap\n",
    )]),
    m("B1-no-backoff-growth",
      "brief: bounded exponential backoff does not grow", RUNTIME, [(
        "                    level + 1, CORE_RECOVERY_MAX_BACKOFF_LEVEL\n",
        "                    level, CORE_RECOVERY_MAX_BACKOFF_LEVEL\n",
    )]),
    m("B2-backoff-uncapped",
      "brief: backoff is not capped", RUNTIME, [(
        "        cap = min(retry_cap_seconds, interval_seconds)\n",
        "        cap = float(\"inf\")\n",
    )]),
    m("V1-retirement-not-logged",
      "brief: retirement not logged", RUNTIME, [skip_log("core_authority_retired")]),
    m("V2-verification-failure-not-logged",
      "brief: verification failure not logged", RUNTIME, [skip_log("core_authority_verification_failed")]),
    m("V3-recovery-attempt-not-logged",
      "brief: recovery attempt not logged", RUNTIME, [skip_log("core_authority_recovery_attempt")]),
    m("V4-restoration-not-logged",
      "brief: restoration not logged", RUNTIME, [skip_log("core_authority_restored")]),
    m("V5-catalog-change-not-logged",
      "brief: catalog withdrawal/restoration not logged", RUNTIME, [(
        "                if count else None\n",
        "                if False else None\n",
    )]),
    m("H1-failure-reason-not-recorded",
      "brief: health last_failure_reason not recorded", RUNTIME, [(
        "        self._last_failure_reason = reason\n",
        "        pass\n",
    )]),
    m("H2-retired-at-not-recorded",
      "brief: health retired_at not recorded", RUNTIME, [(
        "            self._retired_at = now\n",
        "            pass\n",
    )]),
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
    baseline = {"log": baseline_log.name, "modules": baseline_modules, "returncode": code, "seconds": round(seconds, 1),
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
            "log": log_path.name,
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
