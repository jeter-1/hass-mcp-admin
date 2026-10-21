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

Run it under a memory cap. The R1 mutant (no recovery loop) never finishes
and its memory grows without bound; uncapped, it can exhaust host memory
before the ``--timeout``. The recorded results.json came from:
    systemd-run --user -p MemoryMax=3G -p MemorySwapMax=0 \
        -p OOMPolicy=continue --working-directory=<repo> <the command above>
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
        "                        if self._recovery_confirmations < self._recovery_required:\n",
        "                        if self._recovery_confirmations < 1:\n",
    )]),
    m("R3-restore-skips-recovery-gate",
      "brief: restore without the recovery verification gate at all", RUNTIME, [(
        "                if failure_reason is None and gated:\n",
        "                if False:\n",
    )]),
    m("R4-restore-without-lifecycle-fence",
      "brief: restore without full re-verification (no authenticated lifecycle watcher)", RUNTIME, [(
        "        if monitor_required and not monitor_ready:\n",
        "        if False:\n",
    )]),
    m("R5-confirmation-key-ignored",
      "design: confirmations of different versions/contracts count toward one streak", RUNTIME, [
        # The H2 reset alone would restart the streak; disable both checks.
        ("                        and key != self._recovery_confirmation_key\n",
         "                        and False\n"),
        ("                        if key == self._recovery_confirmation_key:\n",
         "                        if self._recovery_confirmation_key is not None:\n"),
    ]),
    m("R6-failure-keeps-confirmations",
      "brief: a failure during confirmation does not restart the count", RUNTIME, [(
        "        self._consecutive_failures += 1\n"
        "        self._recovery_confirmations = 0\n"
        "        self._recovery_confirmation_key = None\n",
        "        self._consecutive_failures += 1\n",
    )]),
    m("R7-connection-move-keeps-confirmations",
      "design: a connection move during confirmation does not restart the count", RUNTIME, [
        ("                elif confirming:\n",
         "                elif False:\n"),
        ("                self._recovery_confirmations = 0\n"
         "                self._recovery_confirmation_key = None\n"
         "                self._append_event_locked(\n"
         "                    \"core_connection_changed\",\n",
         "                self._append_event_locked(\n"
         "                    \"core_connection_changed\",\n"),
    ]),
    m("R8-on-demand-trigger-confirms",
      "H1: an on-demand verification (mutation pre-dispatch, tool or health caller) counts as a confirmation", RUNTIME, [(
        "                    if admitted_preview and trigger in _RECOVERY_CONFIRMATION_TRIGGERS:\n",
        "                    if admitted_preview:\n",
    )]),
    m("G1-incompatible-bypasses-gate",
      "H1: an incompatible (or unreviewed-version) Core restores outside the gate", RUNTIME, [(
        "                gated = not self._admitted and not first_admission\n",
        "                gated = (not self._admitted and not first_admission\n"
        "                         and self._recovery_state != \"incompatible\")\n",
    )]),
    m("G2-boot-window-left-open",
      "H1: never initialized after the boot sequence admits on one background success", RUNTIME, [(
        "            # the restoration gate like any other non-admitted state.\n"
        "            self._spend_boot_exception_locked()\n",
        "            pass\n",
    )]),
    m("G3-failed-attempt-keeps-boot-window",
      "H1: a non-boot first attempt leaves the boot exception unspent", RUNTIME, [(
        "                if trigger not in _BOOT_TRIGGERS:\n"
        "                    self._spend_boot_exception_locked()\n",
        "",
    )]),
    m("G3b-failed-verification-keeps-boot-exception",
      "H1 (review of a6e66a8): a failed verification inside the boot sequence leaves first admission open", RUNTIME, [(
        "        self._spend_boot_exception_locked()\n"
        "        self._consecutive_failures += 1\n",
        "        self._consecutive_failures += 1\n",
    )]),
    m("G3c-boot-retries-continue-after-spent",
      "H1 (review of a6e66a8): boot retries keep running after the exception is spent", RUNTIME, [(
        "            if snapshot.get(\"identity_agreement\") or exhausted:\n",
        "            if snapshot.get(\"identity_agreement\"):\n",
    )]),
    m("E1-reinit-rearms-boot-exception",
      "H1 (review of a6e66a8): re-initialization (configure) resets the one-shot latch", RUNTIME, [(
        "            self._reset_recovery_locked()\n"
        "            if self._boot_exception_spent:\n",
        "            self._boot_exception_spent = False\n"
        "            self._reset_recovery_locked()\n"
        "            if self._boot_exception_spent:\n",
    )]),
    m("E2-admission-keeps-boot-exception",
      "H1 (review of a6e66a8): an admission does not consume the latch", RUNTIME, [(
        "        if admitted_now:\n"
        "            self._spend_boot_exception_locked()\n",
        "        if admitted_now:\n",
    )]),
    m("E3-reinit-drops-withdrawal",
      "H1 (review of a6e66a8): re-initialization discards admitted authority without arming recovery", RUNTIME, [(
        "                if was_admitted:\n"
        "                    self._enter_recovery_locked(datetime.now(timezone.utc))\n",
        "                if False:\n"
        "                    pass\n",
    )]),
    m("E4-reinit-starts-on-periodic-pacing",
      "H1 (review of a6e66a8): after a re-init the supervisor waits a full interval", RUNTIME, [(
        "            retry = self._boot_exception_spent and (\n",
        "            retry = False and (\n",
    )]),
    m("K1-on-demand-changed-key-ignored",
      "H2 (review of a6e66a8): a changed key seen on demand leaves the streak intact", RUNTIME, [(
        "                        self._recovery_confirmation_key is not None\n"
        "                        and key != self._recovery_confirmation_key\n",
        "                        self._recovery_confirmation_key is not None\n"
        "                        and key != self._recovery_confirmation_key\n"
        "                        and trigger in _RECOVERY_CONFIRMATION_TRIGGERS\n",
    )]),
    m("K2-registry-token-not-in-key",
      "H2 (review of a6e66a8): the selected signed-registry authority is not part of the agreement key", RUNTIME, [(
        "                        observation.identity, observation.version,\n"
        "                        selected_registry_token, admitted_preview,\n",
        "                        observation.identity, observation.version,\n"
        "                        admitted_preview,\n",
    )]),
    m("K3-key-reset-not-logged",
      "H2 (review of a6e66a8): a changed-key streak reset is not logged", RUNTIME, [(
        "                            reason=\"core_authority_key_changed\",\n",
        "                            reason=\"core_connection_changed\",\n",
    )]),
    m("K4-incomplete-observation-is-key-change",
      "H2: a failed (incomplete) probe is reported as a changed authority key", RUNTIME, [(
        "                        and observation.identity_agrees\n"
        "                        and observation.reason_code == \"observation_complete\"\n"
        "                    ):\n",
        "                    ):\n",
    )]),
    m("G4-on-demand-first-admission",
      "H1: an on-demand caller may perform the first admission of a fresh runtime", RUNTIME, [(
        "                    and trigger in _FIRST_ADMISSION_TRIGGERS\n",
        "                    and True\n",
    )]),
    m("G5-version-change-retired-only-with-registry",
      "H1: a changed Core version replaces admitted authority without the gate", RUNTIME, [(
        "                observation.version is not None\n"
        "                and self._retire_observed_version_change(\n",
        "                registry and registry.enabled\n"
        "                and observation.version is not None\n"
        "                and self._retire_observed_version_change(\n",
    )]),
    m("G6-incompatible-withdrawal-not-recovery",
      "H1: an incompatible withdrawal is not armed as recovery (no retired_at, no flap)", RUNTIME, [(
        "        if was_admitted:\n"
        "            # Withdrawn for any reason: restoration passes the same gate.\n"
        "            self._enter_recovery_locked(now)\n",
        "",
    )]),
    m("G7-material-retirement-not-recovery",
      "H1: a material (version/registry) retirement is not armed as recovery", RUNTIME, [(
        "        if was_admitted:\n"
        "            self._enter_recovery_locked(now)\n"
        "        self._counters[\"retirements\"] += 1\n",
        "        self._counters[\"retirements\"] += 1\n",
    )]),
    m("G8-confirmation-spacing-only-in-transient-recovery",
      "H1: confirmations of a never-initialized Core are paced as backoff, not confirmation spacing", RUNTIME, [(
        "            if self._confirming_locked():\n",
        "            if self._recovery_active and self._recovery_confirmations > 0:\n",
    )]),
    m("L1-refusals-counted-as-confirmations",
      "L1: refused on-demand calls inflate counters.recovery_confirmations", RUNTIME, [(
        "                        self._counters[\"recovery_on_demand_refusals\"] += 1\n",
        "                        self._counters[\"recovery_confirmations\"] += 1\n",
    )]),
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
