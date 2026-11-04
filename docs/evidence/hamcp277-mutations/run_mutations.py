"""HAMCP-277 mutation run in a copied tree (git clone --shared). Scratch only.

Usage, from the worktree root:
  python docs/evidence/hamcp277-mutations/run_mutations.py <scratch-dir> <results.json>
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
 # Phase placement: start the collector at build start, concurrent with the
 # other sources (the pre-fix shape that let them spend its budget).
 ("N01", [(PV, "return await self._scan_sources(create_script_reader(self.dashboard_reader_factory))",
           "from .global_sources import collect_dashboard_sources as _early\n"
           "        _reader = create_script_reader(self.dashboard_reader_factory)\n"
           "        return await self._scan_sources(\n"
           "            asyncio.ensure_future(_early(_reader, secret=self.secret)) if _reader is not None else None)"),
          (PV, "dashboards = await collect_dashboard_sources(dashboard_reader, secret=self.secret)",
           "dashboards = await dashboard_reader")]),
 # Per-read timeout.
 ("N02", [(GS, "finished, document = await _within(\n                        session.read(path), min(DASHBOARD_READ_SECONDS, remaining), sleep,\n                    )",
           "finished, document = True, await session.read(path)")]),
 ("N03", [(GS, "min(DASHBOARD_READ_SECONDS, remaining)", "DASHBOARD_READ_SECONDS")]),
 ("N04", [(GS, "DASHBOARD_READ_SECONDS = 3.0", "DASHBOARD_READ_SECONDS = 6.0")]),
 ("N05", [(GS, "    if task.cancelled():\n        return False, None", "    if False:\n        return False, None")]),
 ("N06", [(GS, "        if not task.done():\n            task.cancel()\n        await asyncio.gather(timer",
           "        if False:\n            task.cancel()\n        await asyncio.gather(timer")]),
 ("N07", [(GS, "timer = asyncio.ensure_future(sleep(max(0.0, seconds)))", "timer = asyncio.ensure_future(sleep(seconds))")]),
 # Order.
 ("N08", [(GS, 'sorted(listed, key=lambda item: item["url_path"])[:MAX_DEPENDENCY_DASHBOARDS]',
           "listed[:MAX_DEPENDENCY_DASHBOARDS]")]),
 # Budget attribution of a phase-capped read timeout.
 ("N09", [(GS, "budget_exhausted = budget_exhausted or remaining < DASHBOARD_READ_SECONDS",
           "budget_exhausted = budget_exhausted")]),
 ("N10", [(GS, "budget_exhausted = budget_exhausted or remaining < DASHBOARD_READ_SECONDS",
           "budget_exhausted = True")]),
 ("N11", [(GS, "remaining < DASHBOARD_READ_SECONDS\n", "remaining <= DASHBOARD_READ_SECONDS\n")]),
 # Per-dashboard outcome mapping.
 ("N12", [(GS, '("too_large" if failure == "response_limit"', '("failed" if failure == "response_limit"')]),
 ("N13", [(GS, 'else "timed_out" if failure == "timeout" else "failed")', 'else "failed")')]),
 ("N14", [(GS, 'else "timed_out" if failure == "timeout" else "failed")', 'else "timed_out")')]),
 ("N15", [(GS, '                outcomes[path] = "read"\n', "")]),
 ("N16", [(GS, '                        outcomes[entry["url_path"]] = "failed"\n', "")]),
 ("N17", [(GS, "                attempted.add(path)\n", "")]),
 ("N18", [(GS, "            if path in attempted:\n", "            if False:\n")]),
 ("N19", [(GS, '                outcomes[path] = "timed_out"\n                errors["dashboard_read_timeout"] += 1\n',
           '                outcomes[path] = "timed_out"\n')]),
 ("N20", [(GS, '("not_attempted_budget" if budget_exhausted', '("not_attempted_failure" if budget_exhausted')]),
 ("N21", [(GS, 'else "not_attempted_authority" if stale else', 'else "not_attempted_failure" if stale else')]),
 ("N22", [(GS, "                        stale = True\n", "")]),
 ("N23", [(GS, '            errors["dashboard_not_read"] += 1\n    if findings_bounded:', "    if findings_bounded:")]),
 ("N24", [(GS, "                    if expired():\n                        return\n                    if not reader.current():",
           "                    if not reader.current():")]),
 # Report: first-line count, named outcomes, bounded and screened paths.
 ("N25", [(GS, "{successes} of {len(listed)} listed", "{successes} listed")]),
 ("N26", [(GS, "    if inventory_read:\n        # First,", "    if False:\n        # First,")]),
 ("N27", [(GS, 'warnings.extend(f"{key}: {count}" for key, count in sorted(errors.items()))',
           'warnings[0:0] = [f"{key}: {count}" for key, count in sorted(errors.items())]')]),
 ("N28", [(GS, "for outcome in DASHBOARD_OUTCOMES[1:] if outcome in by_outcome)",
           "for outcome in DASHBOARD_OUTCOMES[2:] if outcome in by_outcome)")]),
 ("N29", [(GS, "shown = [path for path in paths if screen(path, (secret,) if secret else ()) is None]",
           "shown = list(paths)")]),
 ("N30", [(GS, "    shown = shown[:MAX_DASHBOARD_OUTCOME_PATHS]\n", "")]),
 ("N31", [(GS, "MAX_DASHBOARD_OUTCOME_PATHS = 10", "MAX_DASHBOARD_OUTCOME_PATHS = 11")]),
 ("N32", [(GS, "(\", \".join(shown) if shown else \"none shown\")", "\", \".join(shown)")]),
 ("N33", [(GS, "{'s' if others != 1 else ''}", "s")]),
 ("N34", [(GS, "others = len(paths) - len(shown)", "others = 0")]),
 ("N35", [(GS, "for outcome in DASHBOARD_OUTCOMES if outcome in by_outcome}",
           "for outcome in DASHBOARD_OUTCOMES[1:] if outcome in by_outcome}")]),
 ("N36", [(GS, '"outcomes": dict(sorted(outcomes.items())),', '"outcomes": {},')]),
 # Completeness.
 ("N37", [(GS, "    if not errors:\n        completeness = \"complete\"", "    if not terminals:\n        completeness = \"complete\"")]),
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
statuses = [v["status"] for k, v in results.items() if k.startswith("N")]
print("done:", {s: statuses.count(s) for s in sorted(set(statuses))},
      "survived:", [k for k, v in results.items() if k.startswith("N") and v["status"] == "SURVIVED"], flush=True)
