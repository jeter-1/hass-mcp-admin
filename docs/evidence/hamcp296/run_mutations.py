"""HAMCP-296 delta evidence: Codex review F1 and F2 (both High) on PR #401.

Run from the repository root with the test interpreter, after committing:

    python docs/evidence/hamcp296/run_mutations.py

Everything runs in a disposable ``git clone --shared`` of HEAD under
``.artifacts/mutation-hamcp296/``; the tree under test is never mutated. The
tests use a temporary store directory, a fake Core and a fake audit sink; no
Home Assistant, network, configuration or secret is read.

1. Regressions: the corrected test module, run against the reviewed head
   afd4acce, must fail each named regression test by an assertion.
2. Mutants: each mutant reopens one reviewed defect in the corrected source
   and is killed only by an assertion failure in its named test; an error,
   import failure or survival is not a kill.
3. Control: the unmutated corrected copy passes the whole module.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / ".artifacts/mutation-hamcp296"
REVIEWED = "afd4acce61174acc152550a3a09877341a4b37bf"
MODULE = "tests.test_hamcp296_trace_watch_lifecycle"
TEST_FILE = "tests/test_hamcp296_trace_watch_lifecycle.py"
PACKAGE = "hass_mcp_engineering_beta/ha_mcp_engineering/trace_snapshots/"
CONTRACTS = PACKAGE + "contracts.py"
STORE = PACKAGE + "store.py"
SERVICE = PACKAGE + "service.py"
F1_TEST = "BudgetTests.test_a_full_watch_split_across_index_and_sidecar_evicts_exactly_one"
F2_TEST = "CoherentCommitTests.test_every_fault_point_leaves_the_prior_or_the_committed_store"

shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True)
HEAD = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
COPY = OUT / "copy"
subprocess.check_call(["git", "clone", "-q", "--shared", "--no-checkout", str(ROOT), str(COPY)])


def checkout(revision: str) -> None:
    subprocess.check_call(["git", "-C", str(COPY), "checkout", "-q", "--force", revision])
    subprocess.check_call(["git", "-C", str(COPY), "clean", "-q", "-fdx"])


def run(test: str, log: str) -> tuple[int, bytes]:
    for cache in COPY.rglob("__pycache__"):
        shutil.rmtree(cache)
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", f"{MODULE}.{test}" if test else MODULE],
                          cwd=COPY, capture_output=True, timeout=1800)
    raw = proc.stdout + proc.stderr
    (OUT / log).write_bytes(raw)
    return proc.returncode, raw


def assertion_failure(code: int, raw: bytes) -> bool:
    return bool(code) and b"FAIL:" in raw and b"AssertionError" in raw and b"ERROR:" not in raw


regressions = [
    ("F1", F1_TEST),
    ("F2", F2_TEST),
]
BINDING = ('        bound = [(value["generation"], slot) for slot, value in valid.items()\n'
           '                 if value["index_sha256"] == digest]\n')
mutants = [
    ("F1", "per-watch depth cut to 5 (the reviewed WATCH_QUOTA)", CONTRACTS,
     "WATCH_QUOTA = 20\n", "WATCH_QUOTA = 5\n", F1_TEST),
    ("F1", "the orphan sweep knows only the entries index.json keeps", SERVICE,
     '        known = {e["snapshot_id"] for e in index["snapshots"]}\n',
     "        from .store import v1_snapshot_ids\n"
     '        known = v1_snapshot_ids(index["snapshots"])\n', F1_TEST),
    ("F1", "a load drops the snapshot entries held in the sidecar", STORE,
     '        snapshots.extend(e for e in present if e["snapshot_id"] in kept)\n',
     "        pass\n", F1_TEST),
    ("F2", "a load takes the newest slot whatever index it is bound to", STORE, BINDING,
     '        bound = [(value["generation"], slot) for slot, value in valid.items()]\n', F2_TEST),
    ("F2", "one sidecar file updated in place (no second slot)", STORE,
     "            target, generation = 1 - (base if committed is None else committed), generation + 1\n",
     "            target, generation = (0 if committed is None else committed), generation + 1\n",
     F2_TEST),
    ("F2", "the first sidecar write has no base state bound to the prior index", STORE,
     "            if base is not None:\n", "            if False:\n", F2_TEST),
]

results = {"head": HEAD, "reviewed_head": REVIEWED, "python": sys.version.split()[0],
           "regressions": [], "mutants": [], "control": None}
corrected_tests = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{HEAD}:{TEST_FILE}"])
checkout(REVIEWED)
(COPY / TEST_FILE).write_bytes(corrected_tests)
for index, (finding, test) in enumerate(regressions, 1):
    code, raw = run(test, f"regression-{index:02d}.log")
    classification = "assertion" if assertion_failure(code, raw) else ("invalid" if code else "passed")
    results["regressions"].append({"id": index, "finding": finding, "revision": REVIEWED, "test": test,
                                   "classification": classification, "exit_code": code})
    print("regression", index, finding, REVIEWED[:8], classification, flush=True)

checkout(HEAD)
for index, (finding, name, file, before, after, test) in enumerate(mutants, 1):
    path = COPY / file
    original = path.read_text()
    assert original.count(before) == 1, (name, original.count(before))
    path.write_text(original.replace(before, after))
    code, raw = run(test, f"mutant-{index:02d}.log")
    classification = "assertion" if assertion_failure(code, raw) else ("invalid" if code else "survived")
    results["mutants"].append({"id": index, "finding": finding, "name": name, "file": file, "test": test,
                               "classification": classification, "exit_code": code})
    path.write_text(original)
    print("mutant", index, finding, name, classification, flush=True)

code, raw = run("", "control.log")
ran = [line for line in raw.decode(errors="replace").splitlines() if line.startswith("Ran ")]
results["control"] = {"name": "control (unmutated corrected copy, whole module)",
                      "classification": "pass" if code == 0 else "fail", "exit_code": code,
                      "ran": ran[-1] if ran else None}
(OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps(results["control"]), flush=True)
if (code or any(item["classification"] != "assertion" for item in results["regressions"])
        or any(item["classification"] != "assertion" for item in results["mutants"])):
    sys.exit(1)
