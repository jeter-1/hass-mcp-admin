"""HAMCP-227 round 4 (Codex R2-L1): replay M01-M70 with timeouts classified as invalid.

Run from a scratch directory that holds copies of run_mutations.py (as
run.py), isolated_targets.py, run_round3.py and this file, with the
disposable `git clone --shared` copy at ./tree. The mutants and their
isolated targets are exactly round 3's (read from run_round3.py).

Each run goes through a runner that denies external network access. A run
counts as a kill only when every expected test ran (71 for the module, 1
for an isolated target) and it reported a test failure or test-level error
that is not one of these, each of which is invalid and never a kill:
- `timeout`: the 300 s subprocess guard expired;
- `invalid_timeout`: a test raised TimeoutError;
- `invalid_import`: an import, syntax or collection error, or fewer tests ran;
- `invalid_network`: a test tried to reach the network.
"""
import json, os, re, subprocess, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
r3 = {"__file__": str(HERE / "run_round3.py")}
exec((HERE / "run_round3.py").read_text().split("ENV = dict(")[0], r3)
TREE, PY, TEST = r3["TREE"], r3["PY"], r3["TEST"]
MUTANTS, TARGETS = r3["MUTANTS"], r3["TARGETS"]
MODULE_TESTS = 71
RUNNER = HERE / "net_denied_unittest.py"
RUNNER.write_text(
    "import sys, unittest\n"
    "def deny(event, args):\n"
    "    if event in ('socket.connect', 'socket.getaddrinfo'):\n"
    "        raise RuntimeError('MUTATION_EXTERNAL_NETWORK_DENIED')\n"
    "sys.addaudithook(deny)\n"
    "sys.path.insert(0, '.')\n"
    "unittest.main(module=None, argv=['unittest', *sys.argv[1:]])\n"
)
ENV = dict(os.environ, TMPDIR=str(HERE / "tmp"), PYTHONDONTWRITEBYTECODE="1",
           DOTNET_SYSTEM_GLOBALIZATION_INVARIANT="1")
INVALID_IMPORT = ("ImportError", "ModuleNotFoundError", "SyntaxError", "IndentationError",
                  "Failed to import test module", "_FailedTest")


def run(target, expected):
    t = time.time()
    try:
        p = subprocess.run([PY, str(RUNNER), target], cwd=TREE, env=ENV, capture_output=True,
                           text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"verdict": "timeout", "s": 300}
    out = p.stderr
    lines = out.strip().splitlines()
    ran = re.search(r"^Ran (\d+) tests? in", out, re.M)
    ran = int(ran.group(1)) if ran else 0
    failures = [l for l in lines if l.startswith("FAIL:")]
    errors = [l for l in lines if l.startswith("ERROR:")]
    if p.returncode == 0 and ran == expected:
        verdict = "pass"
    elif "TimeoutError" in out:
        verdict = "invalid_timeout"
    elif "MUTATION_EXTERNAL_NETWORK_DENIED" in out:
        verdict = "invalid_network"
    elif ran != expected or any(marker in out for marker in INVALID_IMPORT):
        verdict = "invalid_import"
    elif failures or errors:
        verdict = "killed"
    else:
        verdict = "invalid_other"
    return {"verdict": verdict, "s": round(time.time() - t, 1), "ran": ran,
            "kind": "failure" if failures else "error" if errors else None,
            "tail": lines[-1:] + (failures + errors)[:3]}


(HERE / "tmp").mkdir(exist_ok=True)
results = {"control_baseline": run(TEST, MODULE_TESTS)}
assert results["control_baseline"]["verdict"] == "pass", results
controls = {}
for target in sorted(set(TARGETS.values())):
    controls[target] = run(f"tests.test_hamcp227_global_sources.{target}", 1)["verdict"]
results["isolated_controls"] = controls
assert all(v == "pass" for v in controls.values()), controls
for mid, rel, old, new in MUTANTS:
    path = TREE / rel; src = path.read_text(); n = src.count(old)
    if n != 1:
        results[mid] = {"status": f"not_applied(count={n})"}; print(mid, results[mid], flush=True); continue
    path.write_text(src.replace(old, new))
    try:
        module = run(TEST, MODULE_TESTS)
        isolated = run(f"tests.test_hamcp227_global_sources.{TARGETS[mid]}", 1)
    finally:
        path.write_text(src)
    results[mid] = {"file": rel.split("/")[-1], "module": module, "isolated_target": TARGETS[mid],
                    "isolated": isolated}
    print(mid, module["verdict"], module.get("kind"), isolated["verdict"], isolated.get("kind"),
          module["s"], isolated["s"], flush=True)
results["control_final"] = run(TEST, MODULE_TESTS)
assert results["control_final"]["verdict"] == "pass", results["control_final"]
(HERE / "results_round4.json").write_text(json.dumps(results, indent=1))
mut = {k: v for k, v in results.items() if k.startswith("M")}
not_killed = [k for k, v in mut.items()
              if v.get("module", {}).get("verdict") != "killed" or v.get("isolated", {}).get("verdict") != "killed"]
print("done", len(mut), "mutants;", sum(v.get("module", {}).get("verdict") == "killed" for v in mut.values()),
      "killed (module);", sum(v.get("isolated", {}).get("verdict") == "killed" for v in mut.values()),
      "killed (isolated);", not_killed, "not killed", flush=True)
