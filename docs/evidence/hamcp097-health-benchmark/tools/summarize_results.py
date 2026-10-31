"""Tabulate .artifacts/h097/results/*.json into a markdown summary (stdout)."""
import json, statistics, sys
from pathlib import Path
RES = Path(__file__).resolve().parent / "results"
groups = {}
for f in sorted(RES.glob("x*_r*_*.json")):
    scale, rnd, variant = f.stem.split("_", 2)
    groups.setdefault((scale, variant), []).append(json.loads(f.read_text()))
def st(v):
    v = sorted(x for x in v if x is not None)
    return f"{statistics.median(v):.0f} / {v[0]:.0f}" if v else "n/a"
print("| scale | variant | runs | startup ms med/min | first call ms med/min | first-call status | warm ms med/min (n) | warm validation_wait | warm projection | warm assembly | warm overlay | warm snapshot | load1 range |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for (scale, variant), runs in sorted(groups.items(), key=lambda k: (int(k[0][0][1:2]), k[0][0], k[0][1])):
    warm = [r for run in runs for r in run["rows"] if r["call"].startswith("warm")]
    first = [run["rows"][0] for run in runs]
    def ph(k): return st([(r["phase_elapsed_ms"] or {}).get(k) for r in warm])
    loads = [float(run["summary"][e]["loadavg"][0]) for run in runs for e in ("environment_before", "environment_after")]
    print(f"| {scale} | {variant} | {len(runs)} | {st([run['summary']['startup_construction_ms'] for run in runs])} | "
          f"{st([r['wall_ms'] for r in first])} | {','.join(sorted({str(r.get('status') or 'complete') for r in first}))} | "
          f"{st([r['wall_ms'] for r in warm])} ({len(warm)}) | {ph('validation_wait_ms')} | {ph('projection_ms')} | "
          f"{ph('assembly_ms')} | {ph('overlay_ms')} | {ph('snapshot_ms')} | {min(loads):.1f}-{max(loads):.1f} |")
print()
print("Per-call deserialization (plan terminal / task terminal from hot_paths.governance_health; incremental_health_index.last_run):")
for (scale, variant), runs in sorted(groups.items(), key=lambda k: (int(k[0][0][1:2]), k[0][0], k[0][1])):
    run = runs[0]
    for r in run["rows"]:
        c = r["plan_store_scaling_counters"]
        idx = (r.get("incremental_health_index") or {}).get("last_run")
        print(f"- {scale} {variant} r1 {r['call']}: wall={r['wall_ms']}ms terminal_plan={c.get('terminal_plan_records_deserialized')} "
              f"terminal_task={c.get('terminal_task_records_deserialized')} cache_hit={c.get('health_cache_hit_count')} "
              f"rebuild={c.get('health_cache_rebuild_count')} index.last_run={json.dumps(idx) if idx else None}")
print()
print("Startup historical validation (after_sv): launch_to_complete_ms per run, plans_validated:")
for (scale, variant), runs in sorted(groups.items(), key=lambda k: (int(k[0][0][1:2]), k[0][0], k[0][1])):
    if variant != "after_sv":
        continue
    hv = [run["summary"].get("historical_validation") or {} for run in runs]
    print(f"- {scale}: " + ", ".join(f"{h.get('launch_to_complete_ms')} ms ({(h.get('state') or {}).get('plans_validated')} plans)" for h in hv))
