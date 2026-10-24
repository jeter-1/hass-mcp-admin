"""HAMCP-219 review 2 (M3) source mutations against a detached worktree of the task branch.

The M3 mutations remove or weaken the dashboard lane's offline replay of its
retained windows. The review 1 dashboard mutations are re-anchored where
verify_case's body moved to the module-level verify_case_window, so they show
that the review 1 corrections did not regress.
"""
import os, subprocess, sys, time
from pathlib import Path

TREE = Path(sys.argv[1])
PY = sys.argv[2]
MODULES = ["test_dashboard_disposable_acceptance", "test_hamcp219_review2_dashboard_receipt_probe",
           "test_alarmo_interval_observer", "test_automation_baseline_disposable",
           "test_hamcp219_review1_harness_probes"]
D = "scripts/dashboard_analysis_disposable_acceptance.py"
MUTATIONS = [
    # M3: the dashboard offline verification removed (the required mutation).
    ("M3-replay-removed", D,
     'replayed = verify_retained_windows(lane.out, digest((lane.fixture / "__init__.py").read_bytes()))',
     "replayed = sorted(discarded)"),
    ("M3-replay-body-skipped", D, "    out = Path(out)\n    receipts = json.loads(",
     "    return []\n    out = Path(out)\n    receipts = json.loads("),
    ("M3-declaration-unchecked", D,
     '        require(replayed == sorted(discarded), "discarded window declaration mismatch")\n', ""),
    ("M3-missing-window-tolerated", D, '    require(type(sha256) is str and path.is_file(), "retained window missing")\n',
     "    if not path.is_file():\n        return {}\n"),
    ("M3-digest-unchecked", D, '    require(digest(data) == sha256, "retained window altered")\n', ""),
    ("M3-kept-window-not-replayed", D,
     "        verify_case_window(wire, observation, False, native_reads, upstream_read, observer_sha256=observer_sha256)\n",
     ""),
    ("M3-kept-window-replayed-leniently", D,
     "verify_case_window(wire, observation, False, native_reads",
     "verify_case_window(wire, observation, True, native_reads"),
    ("M3-discarded-not-reverified", D, "    return verify_discarded(discarded, replay, set(names))",
     '    return sorted(item["window"] for item in discarded)'),
    ("M3-replay-swallows-refusal", D,
     '            raise ObservationError("Discarded dashboard window refused", {"condition": "window_refused"}) from None',
     "            return"),
    ("M3-receipt-wire-unchecked", D, '        require(wire == receipt["wire"], "case receipt wire mismatch")\n', ""),
    ("M3-outcome-unchecked", D, '        require(receipt["success"] is success, "case outcome mismatch")\n', ""),
    ("M3-sequence-unchecked", D, 'and middle == ["continuation-" + str(page) for page in range(len(middle))], ',
     ", "),
    ("M3-interval-reuse-unchecked", D, "    require(len(identities) == len(receipts) + len(discarded)",
     "    require(True or len(identities) == len(receipts) + len(discarded)"),
    ("M3-unreceipted-window-ignored", D, '"unreceipted retained window")', '"unreceipted retained window") if False else None'),
    ("M3-undeclared-discarded-ignored", D, '            require(receipt["discarded_sha256"] is None and not any(path.exists() for path in paths),\n'
     '                    "undeclared discarded window")\n', ""),
    ("M3-discarded-digest-not-recorded", D,
     '"observation_sha256": digest(serialized(item["observation"])),\n',
     '"observation_sha256": None,\n'),
    ("M3-live-call-own-reads", D, '*case_expectation("provider-refusal"))', "False, 0, 1)"),
    # Review 1 dashboard mutations, re-anchored (H1/M1 must not regress).
    ("R1-H1-dashboard-tolerates-repairs-by-name", D, 'TOLERATED_STORES = ("auth", "core.restore_state")',
     'TOLERATED_STORES = ("auth", "core.restore_state", "repairs.issue_registry")'),
    ("R1-H1-dashboard-tolerate-without-retry", D, "retryable += 1", "pass"),
    ("R1-H1-dashboard-strict-default-ignored", D, "elif retry_allowed is True and retryable_delayed_save(event)",
     "elif retryable_delayed_save(event)"),
    ("R1-H1-dashboard-never-requests-rerun", D, "    if retryable:\n        raise RetryableObservation",
     "    if False:\n        raise RetryableObservation"),
    ("R1-M1-dashboard-any-kind-retries", D, '\n            and event.get("kind") == RETRY_EVENT_KIND)', ")"),
]


def run(label):
    start = time.monotonic()
    result = subprocess.run([PY, "-m", "unittest", *MODULES], cwd=TREE, capture_output=True, text=True,
                            timeout=1200, env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "tests:hass_mcp_engineering_beta",
                                               "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(TREE.parent / "home"),
                                               "TMPDIR": os.environ["TMPDIR"]})
    tail = [line for line in result.stderr.splitlines() if line.startswith(("Ran ", "OK", "FAILED"))]
    failed = [line for line in result.stderr.splitlines() if line.startswith(("FAIL:", "ERROR:"))]
    print(f"{label}: rc={result.returncode} {' '.join(tail)} {time.monotonic()-start:.0f}s", flush=True)
    for line in failed[:6]:
        print("    " + line, flush=True)
    return result.returncode


if run("UNMUTATED") != 0:
    sys.exit("control failed")
killed = 0
for name, rel, old, new in MUTATIONS:
    path = TREE / rel
    original = path.read_text()
    if original.count(old) != 1:
        sys.exit(f"{name}: anchor count {original.count(old)}")
    path.write_text(original.replace(old, new))
    try:
        rc = run(name)
    finally:
        path.write_text(original)
    killed += rc != 0
    print(f"  -> {'KILLED' if rc else 'SURVIVED'}", flush=True)
print(f"TOTAL killed={killed}/{len(MUTATIONS)}", flush=True)
if run("RESTORED") != 0:
    sys.exit("restored control failed")
