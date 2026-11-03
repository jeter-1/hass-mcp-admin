"""HAMCP-277 round-1 mutation run (R1-M1, R1-M2, R1-L3) in a copied tree.

Mutates only the round-1 corrections: the read-start deadline guard inside
the scheduled read, and the inventory accounting kept separate from the read
queue. Same mechanics and classification as run_mutations.py (round 0).

Usage, from the worktree root:
  python docs/evidence/hamcp277-mutations/run_round1_mutations.py <scratch-dir> <results.json>
<scratch-dir> holds tree/ (the shared clone at the commit under test) and
tmp/. Each mutant is one or more exact-string edits; every anchor must match
exactly once. Only a run in which every test ran and at least one test FAILED
(an assertion) counts as a kill; an errors-only run is recorded for a hand
check, and a timeout or a short test count is never a kill.
"""
import json, os, re, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRATCH = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
TREE = SCRATCH / "tree"
LOGS = SCRATCH / "logs"
PY = str(ROOT / ".artifacts" / "venv" / "bin" / "python")
TESTS = ["tests.test_hamcp277_dashboard_scan_budget", "tests.test_hamcp227_global_sources"]
TIMEOUT = 300
ENG = "hass_mcp_engineering_beta/ha_mcp_engineering/"
GS, PV = ENG + "dependency/global_sources.py", ENG + "dependency/provider.py"

M = [
 # R1-M1: the read-start guard inside the scheduled read.
 ("R01", [(GS, "        if seconds <= 0:\n            return _NOT_STARTED", "        if False:\n            return _NOT_STARTED")]),
 ("R02", [(GS, "        if seconds <= 0:\n", "        if seconds < 0:\n")]),
 # Round-0 shape: the allowance is taken when the read is queued, not when it runs.
 ("R03", [(GS, "status, seconds, document = await _within(start, allowance, sleep)",
           "queued = allowance()\n                    status, seconds, document = await _within(start, lambda: queued, sleep)")]),
 ("R04", [(GS, "return min(DASHBOARD_READ_SECONDS, deadline - clock())", "return deadline - clock()")]),
 ("R05", [(GS, "                    budget_exhausted = True\n                    return\n", "                    return\n")]),
 ("R06", [(GS, "                    if read_started is not None:\n                        active -= 1",
           "                    if True:\n                        active -= 1")]),
 ("R07", [(GS, "        if timer is not None:\n            timer.cancel()\n", "")]),
 ("R08", [(GS, '    if task.cancelled():\n        return "timeout", seconds, None', '    if False:\n        return "timeout", seconds, None')]),
 ("R09", [(GS, "        await sleep(limit)\n        task.cancel()", "        await sleep(limit)")]),
 # R1-L3: N11 on the corrected code (equal-boundary attribution).
 ("R10", [(GS, "seconds < DASHBOARD_READ_SECONDS\n", "seconds <= DASHBOARD_READ_SECONDS\n")]),
 # The worker's own pre-check, now backed by the in-read guard.
 ("R11", [(GS, "                    if expired():\n                        return\n                    if not reader.current():",
           "                    if not reader.current():")]),
 # R1-M2: inventory accounting separate from the read queue.
 ("R12", [(GS, '                outcomes[entry["url_path"]] = "not_attempted_limit"\n', "")]),
 ("R13", [(GS, '                errors["dashboard_not_read_inventory_limit"] += 1\n', "")]),
 ("R14", [(GS, '[:MAX_DEPENDENCY_DASHBOARDS + 1]\n', '[:MAX_DEPENDENCY_DASHBOARDS]\n')]),
 ("R15", [(GS, 'inventory_bounded = (inventory.get("truncated") is True\n                                 or len(entries) > MAX_DEPENDENCY_DASHBOARDS + 1)',
           "inventory_bounded = False")]),
 ("R16", [(GS, '(inventory.get("truncated") is True\n                                 or len(entries) > MAX_DEPENDENCY_DASHBOARDS + 1)',
           '(inventory.get("truncated") is True)')]),
 ("R17", [(GS, 'f"Dashboards read: {successes} of at least {len(listed)} (inventory truncated)"',
           'f"Dashboards read: {successes} of {len(listed)} listed"')]),
 ("R18", [(GS, "        if omitted:\n            first +=", "        if False:\n            first +=")]),
 ("R19", [(GS, "queued = listed[:MAX_DEPENDENCY_DASHBOARDS]", "queued = listed")]),
 ("R20", [(GS, "if len(entries) > MAX_DEPENDENCY_DASHBOARDS or inventory_bounded:", "if inventory_bounded:")]),
 ("R21", [(GS, '    "not_attempted_limit",\n)', ")")]),
]


def run(label):
    env = dict(os.environ, TMPDIR=str(SCRATCH / "tmp"), PYTHONDONTWRITEBYTECODE="1")
    started = time.time()
    try:
        p = subprocess.run([PY, "-m", "unittest", *TESTS], cwd=TREE, env=env,
                           capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"rc": "timeout", "s": TIMEOUT}
    (LOGS / f"{label}.log").write_text(p.stderr)
    ran = re.findall(r"^Ran (\d+) tests?", p.stderr, re.M)
    last = p.stderr.strip().splitlines()[-1:] if p.stderr.strip() else []
    counts = dict(re.findall(r"(failures|errors)=(\d+)", last[0])) if last else {}
    return {"rc": p.returncode, "s": round(time.time() - started, 1),
            "ran": int(ran[-1]) if ran else 0, "tail": last,
            "failures": int(counts.get("failures", 0)), "errors": int(counts.get("errors", 0))}


def classify(result, baseline_ran):
    if result["rc"] == "timeout":
        return "timeout (not a kill)"
    if result["rc"] == 0:
        return "SURVIVED"
    if result["ran"] != baseline_ran:
        return "not_a_kill(test count)"
    if result["failures"]:
        return "killed(assertion)"
    return "errors_only (hand check)"


LOGS.mkdir(parents=True, exist_ok=True)
results = {"tests": TESTS, "head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=TREE,
                                                  capture_output=True, text=True).stdout.strip()}
results["control_baseline"] = base = run("control_baseline")
print("control", base, flush=True)
assert base["rc"] == 0, "baseline must pass"
for mid, edits in M:
    originals = {}
    try:
        for rel, old, new in edits:
            path = TREE / rel
            src = path.read_text()
            originals.setdefault(path, src)
            n = src.count(old)
            if n != 1:
                raise LookupError(f"{rel.split('/')[-1]}: anchor count {n}")
            path.write_text(src.replace(old, new))
    except LookupError as error:
        results[mid] = {"status": f"not_applied({error})"}
    else:
        outcome = run(mid)
        outcome["status"] = classify(outcome, base["ran"])
        outcome["files"] = sorted({rel.split("/")[-1] for rel, _, _ in edits})
        results[mid] = outcome
    finally:
        for path, src in originals.items():
            path.write_text(src)
    print(mid, results[mid], flush=True)
results["control_final"] = final = run("control_final")
assert final["rc"] == 0, "final control must pass"
OUT.write_text(json.dumps(results, indent=1) + "\n")
statuses = [v["status"] for k, v in results.items() if k.startswith("R")]
print("done:", {s: statuses.count(s) for s in sorted(set(statuses))},
      "survived:", [k for k, v in results.items() if k.startswith("R") and v["status"] == "SURVIVED"], flush=True)
