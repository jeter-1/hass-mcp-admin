"""HAMCP-225 correction batch 2 mutations: one source edit at a time in a disposable copy."""
import json, subprocess, sys, time
from pathlib import Path

ROOT = Path(sys.argv[1]); LOGS = Path(sys.argv[2]); PY = sys.argv[3]
only = set(sys.argv[4:])
E = "hass_mcp_engineering_beta/ha_mcp_engineering/"
SVC, NORM, CON = E + "trace_snapshots/service.py", E + "trace_normalization.py", E + "trace_snapshots/contracts.py"
CORE = ["tests.test_hamcp225_trace_snapshots", "tests.test_hamcp225_review1_reviewer_probes",
        "tests.test_hamcp225_review1_store_probes", "tests.test_hamcp225_review1_wire_probes"]
CROSS = CORE + ["tests.test_hamcp225_cross_path_credentials"]
ALL = CROSS + ["tests.test_hamcp225_review2_delta_extra"]
STRICT = "__import__('ha_mcp_engineering.sanitization', fromlist=['x']).sanitize_untrusted_data"
MUTANTS = [
    # R1 (owner decision): identifiers and names back to the labelled mode
    ("R1-identifiers-labelled", NORM, "found = marker(item, not identity)", "found = marker(item, True)", ALL),
    ("R1-identity-lists-labelled", NORM, "identity and type(child) is str) for child in item]",
     "False) for child in item]", ALL),
    # R2: the post-lock authority re-check
    ("R2-no-post-lock-recheck", SVC, "if authorized is not None and not authorized():", "if False:", ALL),
    ("R2-guard-fails-open", SVC, "                    answer.set_result(False)\n",
     "                    answer.set_result(True)\n", ALL),
]

def run(name, modules):
    log = LOGS / f"{name}.log"
    start = time.time()
    with open(log, "w") as out:
        r = subprocess.run([PY, "-m", "unittest", *modules], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                           timeout=1800, env={"TMPDIR": "/var/tmp/hamcp225-mut/tmp", "PATH": "/usr/bin:/bin"})
    text = log.read_text(errors="replace")
    failing = [l for l in text.splitlines() if l.startswith(("FAIL:", "ERROR:"))]
    return r.returncode, failing, round(time.time() - start, 1), str(log)

results = []
rc, failing, secs, log = run("baseline", sorted(set(ALL)))
results.append({"id": "baseline", "returncode": rc, "result": "PASS" if rc == 0 else "FAIL", "seconds": secs, "log": log})
print("baseline", rc, flush=True)
if rc != 0:
    sys.exit("baseline failed")
for name, path, old, new, modules in MUTANTS:
    if only and name not in only:
        continue
    target = ROOT / path
    source = target.read_text()
    assert source.count(old) == 1, (name, source.count(old))
    try:
        target.write_text(source.replace(old, new))
        rc, failing, secs, log = run(name, modules)
    finally:
        target.write_text(source)
    verdict = "KILLED" if rc else "SURVIVED"
    results.append({"id": name, "file": path, "result": verdict, "killing_tests": failing[:12],
                    "killing_count": len(failing), "seconds": secs, "log": log})
    print(name, verdict, len(failing), flush=True)
(LOGS / "results.json").write_text(json.dumps(results, indent=2))
print("SURVIVED:", [r["id"] for r in results if r["result"] == "SURVIVED"])
