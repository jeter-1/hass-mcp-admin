"""HAMCP-227 mutation run in a copied tree (git clone --shared). Scratch only."""
import json, os, subprocess, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
TREE = HERE / "tree"
PY = str(HERE.parent / "venv" / "bin" / "python")
ENG = "hass_mcp_engineering_beta/ha_mcp_engineering/"
GS, DR = ENG + "dependency/global_sources.py", ENG + "dashboard_analysis/dependency_reader.py"
IP, SV, IX, PV = ENG + "impact/provider.py", ENG + "dependency/service.py", ENG + "dependency/index.py", ENG + "dependency/provider.py"
BS = ENG + "dependency/bounded_sources.py"
M = [
 ("M01", GS, "if len(members) > MAX_MEMBERS_PER_SOURCE:", "if len(members) >= MAX_MEMBERS_PER_SOURCE:"),
 ("M02", GS, "enumerate(members[:MAX_MEMBERS_PER_SOURCE])", "enumerate(members)"),
 ("M03", GS, "if len(findings) >= MAX_MEMBERSHIP_FINDINGS:", "if len(findings) > MAX_MEMBERSHIP_FINDINGS:"),
 ("M04", GS, 'completeness = "complete" if not errors else "partial"', 'completeness = "complete"'),
 ("M05", GS, "    if not registry_complete:\n", "    if False:\n"),
 ("M06", GS, 'errors[f"{source_type}_members_not_exposed"] += 1', "pass"),
 ("M07", GS, 'errors[f"{source_type}_state_unavailable"] += 1', "pass"),
 ("M08", GS, "return ordered[:limit], len(ordered) > limit", "return ordered[:limit], len(ordered) >= limit"),
 ("M09", GS, 'elif platform == "group" or (', "elif ("),
 ("M10", GS, 'if platform == "template":\n            template_count += 1', 'if platform == "template":\n            pass'),
 ("M11", GS, 'if inventory.get("partial"):', "if False:"),
 ("M12", GS, "if DEFAULT_DASHBOARD_PATH not in paths:", "if False:"),
 ("M13", GS, 'if len(listed) > MAX_DEPENDENCY_DASHBOARDS or inventory.get("truncated"):', "if len(listed) > MAX_DEPENDENCY_DASHBOARDS:"),
 ("M14", GS, 'if len(listed) > MAX_DEPENDENCY_DASHBOARDS or inventory.get("truncated"):', 'if inventory.get("truncated"):'),
 ("M15", GS, "listed = listed[:MAX_DEPENDENCY_DASHBOARDS]", "pass"),
 ("M16", GS, 'errors[f"dashboard_{reason}"] += count', "pass"),
 ("M17", GS, 'if document.get("processing_truncated"):', "if False:"),
 ("M18", GS, "if len(findings) + len(extracted) > MAX_DASHBOARD_FINDINGS:", "if len(findings) + len(extracted) >= MAX_DASHBOARD_FINDINGS:"),
 ("M19", GS, 'errors["dashboard_scan_budget_exhausted"] += 1', "pass"),
 ("M20", GS, "                if not_read:\n", "                if False:\n"),
 ("M21", GS, "range(min(len(listed), DASHBOARD_READ_CONCURRENCY))", "range(len(listed))"),
 ("M22", GS, "if not reader.current():\n                            return", "if False:\n                            return"),
 ("M23", GS, "if self.authority_current():", "if True:"),
 ("M24", GS, ' or item.get("kind") != "entity_reference":', ":"),
 ("M25", GS, "or not _RULE.fullmatch(rule) ", ""),
 ("M26", GS, 'parts.append(".<key>")', 'parts.append("." + segment)'),
 ("M27", GS, 'conditional = item.get("conditional") is True', "conditional = False"),
 ("M28", GS, "elif successes or inventory_read:", "elif successes:"),
 ("M29", GS, "if reason in ANALYSIS_REASONS and not inventory_read", "if False"),
 ("M30", GS, "    if screen(value, (secret,) if secret else ()) is not None:\n        return None\n", ""),
 ("M31", GS, "if item.source_type != \"dashboard\"),\n            tuple(", "if True),\n            tuple("),
 ("M32", DR, "if parent is None or parent.core_dispatch_authorizer is None:", "if parent is None:"),
 ("M33", DR, "if not parent.authorize_core_dispatch():", "if False:"),
 ("M34", DR, "if commits is None or not self.core_runtime.revalidate(authority, commits):", "if commits is None:"),
 ("M35", DR, "            if commits is not None:\n                self.core_runtime.finish(commits)", "            if False:\n                self.core_runtime.finish(commits)"),
 ("M36", DR, "elif authority is not None:\n                self.core_runtime.release(authority)", "elif False:\n                self.core_runtime.release(authority)"),
 ("M37", DR, '"parent_request_id": self.parent.request_id,', '"parent_request_id": None,'),
 ("M38", DR, "\n                    or screen(path, self.reader.known_secrets) is not None):", "):"),
 ("M39", DR, '"partial": result.completeness != "complete" or bool(invalid)', '"partial": result.completeness != "complete"'),
 ("M40", DR, 'if not self.reader.current():\n            return {"failure": "authority_drift"}', 'if False:\n            return {"failure": "authority_drift"}'),
 ("M41", DR, 'if not c.PATH.fullmatch(url_path):\n            return {"failure": "invalid_arguments"}', 'if False:\n            return {"failure": "invalid_arguments"}'),
 ("M42", DR, '    if strategy:\n        gaps["strategy_not_expanded"] = 1', '    if False:\n        gaps["strategy_not_expanded"] = 1'),
 ("M43", DR, '(truncated and reason == "structural_limit")', "False"),
 ("M44", DR, '"pointer", "conditional")', '"pointer")'),
 ("M45", DR, "if not telemetry.authorize_core_dispatch():\n                raise c.AnalysisError(\"authority_unavailable\")", "if False:\n                raise c.AnalysisError(\"authority_unavailable\")"),
 ("M46", DR, "telemetry.caller_id = self.parent.caller_id", "pass"),
 ("M47", IP, 'partition = source_type in GLOBAL_SOURCE_TYPES and item.provider != "none"', "partition = False"),
 ("M48", IP, "+ (tuple(global_sources.findings) if global_sources is not None else ())", "+ ()"),
 ("M49", SV, "output.append(global_sources.coverage_for(item.source_type) or item)", "output.append(item)"),
 ("M50", SV, "+ (global_sources.findings if global_sources else ())", "+ ()"),
 ("M51", IX, "*sorted(scan.findings, key=lambda item: item.evidence_id)[self.max_edges:],", ""),
 ("M52", PV, "registry_complete=entity_registry_complete,\n            secret=self.secret, dashboards=dashboards,", "registry_complete=True,\n            secret=self.secret, dashboards=dashboards,"),
 ("M53", PV, 'request_counts["dashboard_list"] += dashboard_profile["list_requests"]', "pass"),
 ("M54", GS, "                        except Exception:\n                            completed.add(entry[\"url_path\"])", "                        except ZeroDivisionError:\n                            completed.add(entry[\"url_path\"])"),
 ("M55", GS, 'if document.get("failure"):', "if False:"),
]
TEST = "tests.test_hamcp227_global_sources"
def run():
    env = dict(os.environ, TMPDIR=str(HERE.parent / "tmp"), PYTHONDONTWRITEBYTECODE="1")
    t = time.time()
    p = subprocess.run([PY, "-m", "unittest", TEST], cwd=TREE, env=env, capture_output=True, text=True, timeout=300)
    return p.returncode, round(time.time() - t, 1), p.stderr.strip().splitlines()[-1:]
results = {}
rc, dur, tail = run()
results["control_baseline"] = {"rc": rc, "s": dur, "tail": tail}
print("control", rc, dur, tail, flush=True)
assert rc == 0, "baseline must pass"
for mid, rel, old, new in M:
    path = TREE / rel
    src = path.read_text()
    n = src.count(old)
    if n != 1:
        results[mid] = {"status": f"not_applied(count={n})"}; print(mid, results[mid], flush=True); continue
    path.write_text(src.replace(old, new))
    try:
        try:
            rc, dur, tail = run()
        except subprocess.TimeoutExpired:
            rc, dur, tail = "timeout", 300, []
        results[mid] = {"file": rel.split("/")[-1], "status": "killed" if rc != 0 else "SURVIVED", "s": dur, "tail": tail}
    finally:
        path.write_text(src)
    print(mid, results[mid], flush=True)
rc, dur, tail = run()
results["control_final"] = {"rc": rc, "s": dur, "tail": tail}
(HERE / "results.json").write_text(json.dumps(results, indent=1))
print("done", sum(v.get("status") == "killed" for v in results.values()), "killed;",
      [k for k, v in results.items() if v.get("status") == "SURVIVED"], "survived", flush=True)
