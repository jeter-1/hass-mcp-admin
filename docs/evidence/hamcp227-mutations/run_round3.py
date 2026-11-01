"""HAMCP-227 round 3 (Codex R1 corrections): replay M01-M56 and add M57-M70.

Run from a scratch directory that holds copies of run_mutations.py (as
run.py), isolated_targets.py and this file, with the disposable
`git clone --shared` copy at ./tree. Each mutant gets a full-module run and
an isolated run of its target test; each target test is first run on the
clean tree as its control. A timeout is never a kill.
"""
import json, os, subprocess, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
ns = {"__file__": str(HERE / "run.py")}
exec((HERE / "run.py").read_text().split("results = {}")[0], ns)
TREE, PY, TEST = ns["TREE"], ns["PY"], ns["TEST"]
GS, DR, IP, BS = ns["GS"], ns["DR"], ns["IP"], ns["BS"]
ENG = ns["ENG"]; SVI = ENG + "impact/service.py"
tns = {}; exec((HERE / "isolated_targets.py").read_text(), tns)
TARGETS = {f"M{k:02d}": v for k, v in tns["TARGETS"].items()}
M, W, R, B, C = ("MembershipSourceTests.", "DashboardWalkTests.", "DashboardDependencyReaderTests.",
                 "DependencyBuildTests.", "ConsumerTests.")
D, P, T = "DashboardBudgetTests.", "ImpactPaginationBindingTests.", "BoundTableTests."

# The R1 rewrite of collect_dashboard_sources changed three anchors; the
# mutation intent is unchanged.
REANCHORED = {
 "M20": (GS, "        if not_read:\n", "        if False:\n"),
 "M22": (GS, "if expired() or not reader.current():", "if expired():"),
 "M54": (GS, "                    except Exception:\n                        completed.add(entry[\"url_path\"])",
         "                    except ZeroDivisionError:\n                        completed.add(entry[\"url_path\"])"),
}
# Targets that named the removed wall-clock budget test now name its
# simulated-clock replacements.
TARGETS.update({"M19": D + "test_list_that_never_returns_is_unavailable_and_reads_nothing",
                "M20": D + "test_budget_overrun_mid_list_leaves_the_rest_unread",
                "M28": D + "test_list_that_consumes_the_budget_starts_no_read"})
NEW = [
 ("M57", GS, "if expired() or not reader.current():", "if not reader.current():",
  "worker reads past the deadline (no expired() gate)", D + "test_budget_overrun_mid_list_leaves_the_rest_unread"),
 ("M58", GS, "            if expired():\n                return\n", "            if False:\n                return\n",
  "lists after the session consumed the budget", D + "test_session_that_consumes_the_budget_lists_nothing"),
 ("M59", GS, "await asyncio.wait((collection, watchdog), return_when=asyncio.FIRST_COMPLETED)",
  "await asyncio.wait((collection,))", "ignores the watchdog",
  D + "test_list_that_never_returns_is_unavailable_and_reads_nothing"),
 ("M60", GS, "                budget_exhausted = True\n                collection.cancel()",
  "                budget_exhausted = True\n                pass", "never cancels in-flight work at the deadline",
  D + "test_reads_in_flight_at_the_deadline_are_cancelled_and_counted"),
 ("M61", GS, "            if not collection.done():\n                budget_exhausted = True\n",
  "            if not collection.done():\n", "watchdog expiry does not mark the budget exhausted",
  D + "test_list_that_never_returns_is_unavailable_and_reads_nothing"),
 ("M62", GS, '            terminals.append(GlobalBoundedTerminal(\n                "dashboard", "dependency_provider", None, "dashboard_scan_budget_exhausted",\n            ))',
  "            pass", "no budget terminal in bounded_sources", D + "test_budget_overrun_mid_list_leaves_the_rest_unread"),
 ("M63", GS, '                    if document["failure"] == "response_limit":', "                    if False:",
  "no document-limit terminal for an oversize document", D + "test_oversize_document_is_partial_with_a_document_terminal"),
 ("M64", GS, 'errors["dashboard_not_read"] += not_read', "pass",
  "unread dashboards are not counted", D + "test_list_that_consumes_the_budget_starts_no_read"),
 ("M65", GS, "sleep(max(0.0, deadline - clock()))", "sleep(DASHBOARD_SCAN_SECONDS * 2)",
  "watchdog not bound to the deadline", D + "test_list_that_never_returns_is_unavailable_and_reads_nothing"),
 ("M66", SVI, "        if _active_diagnostic_identity(self.provider) != snapshot.diagnostic_fingerprint:",
  "        if False:", "continuation ignores the diagnostic partition",
  P + "test_retired_dashboard_authority_makes_the_cursor_stale"),
 ("M67", SVI, "                or _active_diagnostic_identity(self.provider)\n                != diagnostic_fingerprint\n", "",
  "commit ignores a diagnostic change before commit", P + "test_diagnostic_change_before_commit_refuses_the_cursor"),
 ("M68", SVI, "    except Exception:\n        return None\n    return value if isinstance(value, str) else None",
  "    except Exception:\n        return \"\"\n    return value if isinstance(value, str) else None",
  "a failing diagnostic identity reads as no partition", P + "test_failing_identity_is_not_read_as_an_absent_partition"),
 ("M69", IP, '            diagnostic_fingerprint=(\n                global_sources.fingerprint if global_sources is not None else ""\n            ),\n', "",
  "the bundle omits the partition it was built from", P + "test_unchanged_partition_pages_are_consistent_and_read_nothing"),
 ("M70", BS, '        "dashboard_scan_budget_exhausted": bound(g, "DASHBOARD_SCAN_SECONDS"),\n', "",
  "bounded_sources does not name the budget bound", T + "test_bound_table_names_the_partition_limits"),
]
TARGETS.update({mid: target for mid, *_rest, target in NEW})
MUTANTS = [(mid, *REANCHORED.get(mid, (rel, old, new))) for mid, rel, old, new in ns["M"]]
# M56 lived in run_m56.py; same anchor and edit.
MUTANTS.append(("M56", IP, 'source_type in GLOBAL_SOURCE_TYPES\n                        else "direct_ha_api" if selected else "none"',
                'source_type in GLOBAL_SOURCE_TYPES\n                        and item.provider != "none"\n                        else "direct_ha_api" if selected else "none"'))
MUTANTS += [(mid, rel, old, new) for mid, rel, old, new, _what, _t in NEW]
ENV = dict(os.environ, TMPDIR=str(HERE / "tmp"), PYTHONDONTWRITEBYTECODE="1")

def run(target):
    t = time.time()
    try:
        p = subprocess.run([PY, "-m", "unittest", target], cwd=TREE, env=ENV, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return "timeout", 300, []
    lines = p.stderr.strip().splitlines()
    return p.returncode, round(time.time() - t, 1), lines[-1:] + [l for l in lines if l.startswith(("FAIL:", "ERROR:"))][:3]

(HERE / "tmp").mkdir(exist_ok=True)
results = {"control_baseline": dict(zip(("rc", "s", "tail"), run(TEST)))}
assert results["control_baseline"]["rc"] == 0, results
controls = {}
for target in sorted(set(TARGETS.values())):
    rc, _s, tail = run(f"tests.test_hamcp227_global_sources.{target}")
    controls[target] = "pass" if rc == 0 else f"FAIL {tail}"
results["isolated_controls"] = controls
assert all(v == "pass" for v in controls.values()), controls
for mid, rel, old, new in MUTANTS:
    path = TREE / rel; src = path.read_text(); n = src.count(old)
    if n != 1:
        results[mid] = {"status": f"not_applied(count={n})"}; print(mid, results[mid], flush=True); continue
    path.write_text(src.replace(old, new))
    try:
        rc, dur, tail = run(TEST)
        irc, idur, itail = run(f"tests.test_hamcp227_global_sources.{TARGETS[mid]}")
    finally:
        path.write_text(src)
    results[mid] = {"file": rel.split("/")[-1], "status": "timeout" if rc == "timeout" else "killed" if rc != 0 else "SURVIVED",
                    "s": dur, "tail": tail, "isolated_target": TARGETS[mid],
                    "isolated": "timeout" if irc == "timeout" else "killed" if irc != 0 else "SURVIVED", "isolated_tail": itail,
                    **({"reanchored": True} if mid in REANCHORED else {})}
    print(mid, results[mid]["status"], results[mid]["isolated"], dur, flush=True)
results["control_final"] = dict(zip(("rc", "s", "tail"), run(TEST)))
results["descriptions"] = {mid: what for mid, _r, _o, _n, what, _t in NEW}
(HERE / "results_round3.json").write_text(json.dumps(results, indent=1))
mut = {k: v for k, v in results.items() if k.startswith("M")}
print("done", len(mut), "mutants;", sum(v["status"] == "killed" for v in mut.values()), "killed;",
      sum(v.get("isolated") == "killed" for v in mut.values()), "killed in isolation;",
      [k for k, v in mut.items() if v["status"] != "killed" or v.get("isolated") != "killed"], "not killed", flush=True)
