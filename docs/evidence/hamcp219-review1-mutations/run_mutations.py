"""HAMCP-219 review 1 H1/M1 source mutations against a snapshot of the task worktree."""
import subprocess, sys, time
from pathlib import Path

TREE = Path(sys.argv[1])
PY = sys.argv[2]
MODULES = ["tests.test_alarmo_interval_observer", "tests.test_dashboard_disposable_acceptance",
           "tests.test_automation_baseline_disposable", "tests.test_hamcp219_review1_harness_probes"]
A = "scripts/alarmo_inspection_contract_acceptance.py"
D = "scripts/dashboard_analysis_disposable_acceptance.py"
B = "scripts/automation_baseline_contract_acceptance.py"
AO = "tests/fixtures/alarmo_interval_observer/custom_components/alarmo_interval_observer/__init__.py"
DO = "tests/fixtures/dashboard_interval_observer/custom_components/dashboard_interval_observer/__init__.py"
MUTATIONS = [
    ("H1-tolerate-without-retry", A, "retryable.append(index)", "pass"),
    ("H1-accept-nonclean-retry", A,
     "    try:\n        proof = verify(outcome, observation, False)\n    except ObservationError as error:",
     "    try:\n        proof = verify(outcome, observation, True)\n    except RetryableObservation:\n"
     "        proof = {\"result\": \"PASS\"}\n    except ObservationError as error:"),
    ("H1-client-tolerates-repairs-by-name", A, 'TOLERATED_STORES = ("auth", "core.restore_state")',
     'TOLERATED_STORES = ("auth", "core.restore_state", "repairs.issue_registry")'),
    ("H1-strict-default-ignored", A, 'if retry_allowed is True and event["store"] == RETRY_STORE',
     'if event["store"] == RETRY_STORE'),
    ("H1-retry-before-other-checks", A, "retryable.append(index)\n",
     'raise RetryableObservation("early", {"condition": "retryable_delayed_save"})\n'),
    ("M1-alarmo-any-kind-retries", A, ' and event["kind"] == RETRY_EVENT_KIND', ""),
    ("H1-discard-diagnostic-unchecked", A, 'if error.diagnostic != item["diagnostic"]:', "if False:"),
    ("H1-discard-duplicate-window", A, '\n                or item["window"] in seen):', "):"),
    ("H1-observer-alarmo-tolerates-repairs", AO, 'TOLERATED_KEYS = frozenset({"auth", "core.restore_state"})',
     'TOLERATED_KEYS = frozenset({"auth", "core.restore_state", "repairs.issue_registry"})'),
    ("H1-dashboard-tolerates-repairs-by-name", D, 'TOLERATED_STORES = ("auth", "core.restore_state")',
     'TOLERATED_STORES = ("auth", "core.restore_state", "repairs.issue_registry")'),
    ("H1-dashboard-tolerate-without-retry", D, "retryable += 1", "pass"),
    ("H1-dashboard-strict-default-ignored", D, "elif retry_allowed is True and retryable_delayed_save(event)",
     "elif retryable_delayed_save(event)"),
    ("H1-dashboard-never-requests-rerun", D, "            if retryable:\n                raise RetryableObservation",
     "            if False:\n                raise RetryableObservation"),
    ("M1-dashboard-any-kind-retries", D, '\n            and event.get("kind") == RETRY_EVENT_KIND)', ")"),
    ("H1-observer-dashboard-untracked-repairs", DO, "(store.key in HASH_KEYS or store.key in RETRY_KEYS)",
     "store.key in HASH_KEYS"),
    ("H1-baseline-discards-unverified", B, "discarded = verify_discarded(result['discarded_observations'],",
     "discarded = (lambda *a: [])(result['discarded_observations'],"),
    ("H1-baseline-screen-never-reruns", B, "    except RetryableObservation:\n        raise",
     "    except RetryableObservation:\n        pass"),
]

def run(label):
    start = time.monotonic()
    result = subprocess.run([PY, "-m", "unittest", *MODULES], cwd=TREE, capture_output=True, text=True,
                            timeout=1200, env={"PATH": "/usr/bin:/bin", "PYTHONPATH": ".:tests:hass_mcp_engineering_beta",
                                               "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(TREE.parent / "home")})
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
