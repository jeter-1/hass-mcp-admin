#!/usr/bin/env python
"""HAMCP-097 offline benchmark: governance async_health_summary latency.

Copies a store built by build_store.py into a fresh temp dir, composes the
governance service exactly as application.py does (GovernanceRuntime.configure
with synthetic settings and the production provider singletons, no network
use), and times one cold and N warm async_health_summary calls.

--source-root DIR selects the code under test (the directory that contains
hass_mcp_engineering_beta); default is the frozen base clone ./base.
The last stdout line is a JSON object.
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import dataclasses
import io
import json
import os
from pathlib import Path
import pstats
import shutil
import statistics
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--store", required=True)
parser.add_argument("--calls", type=int, default=5)
parser.add_argument("--cold-mode", choices=("plain", "startup_validation"), default="plain",
                    help="startup_validation: launch service.start_historical_validation() (after code) right after construction, then make the first call")
parser.add_argument("--profile", action="store_true")
parser.add_argument("--mutate", type=int, default=0)
parser.add_argument("--source-root", default=str(HERE / "base"))
parser.add_argument("--label", default=None, help="suffix for profile file names")
parser.add_argument("--retention-days", type=int, default=365)
parser.add_argument("--json-out", default=None)
ARGS = parser.parse_args()

SOURCE_ROOT = Path(ARGS.source_root).resolve()
sys.path[:0] = [
    str(SOURCE_ROOT),
    str(SOURCE_ROOT / "hass_mcp_engineering_beta"),
    str(SOURCE_ROOT / "tests"),
]

import logging  # noqa: E402

logging.disable(logging.WARNING)

import ha_mcp_engineering  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.clients.rest import HomeAssistantRestClient  # noqa: E402
from ha_mcp_engineering.clients.websocket import HomeAssistantWebSocketClient  # noqa: E402
from ha_mcp_engineering.governance.runtime import GovernanceRuntime  # noqa: E402
from ha_mcp_engineering.providers.operational_backup import (  # noqa: E402
    UPSTREAM_OPERATIONAL_BACKUP,
)
from ha_mcp_engineering.providers.operational_lifecycle import (  # noqa: E402
    UPSTREAM_OPERATIONAL_LIFECYCLE,
)
from ha_mcp_engineering.providers.upstream_dashboard import UPSTREAM_DASHBOARD  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.runtime import CORE_READMISSION  # noqa: E402
from tests.test_readonly_upstream_gateway import settings as synthetic_settings  # noqa: E402

assert str(SOURCE_ROOT) in ha_mcp_engineering.__file__, ha_mcp_engineering.__file__

PLAN_KEYS = ("records_deserialized", "terminal_records_deserialized")


def nav(service):
    plan = service.repository.navigation_metrics()
    task = service.task_repository.navigation_metrics()
    return {
        "plan": {k: plan.get(k) for k in PLAN_KEYS},
        "task": {k: task.get(k) for k in PLAN_KEYS},
    }


def delta(after, before):
    return {
        side: {k: (after[side][k] or 0) - (before[side][k] or 0) for k in PLAN_KEYS}
        for side in ("plan", "task")
    }


def compose(store_copy: Path):
    configured = dataclasses.replace(
        synthetic_settings(),
        audit_path=str(store_copy / "audit.jsonl"),
        governance_path=str(store_copy / "governance"),
        governance_retention_days=ARGS.retention_days,
    )
    # application.py order: providers configured, then GOVERNANCE.configure.
    UPSTREAM_OPERATIONAL_BACKUP.configure(configured)
    UPSTREAM_OPERATIONAL_LIFECYCLE.configure(configured)
    UPSTREAM_DASHBOARD.configure(configured)
    runtime = GovernanceRuntime()
    started = time.perf_counter()
    runtime.configure(
        configured,
        AuditLogger(configured.audit_path, configured.access_secret),
        HomeAssistantRestClient(configured),
        HomeAssistantWebSocketClient(configured),
        UPSTREAM_OPERATIONAL_BACKUP,
        UPSTREAM_OPERATIONAL_LIFECYCLE,
        runtime_snapshot=lambda: {},
        dashboard_provider=UPSTREAM_DASHBOARD,
        core_runtime=CORE_READMISSION,
    )
    elapsed = (time.perf_counter() - started) * 1000
    if runtime.storage_error is not None or runtime.service is None:
        raise SystemExit(f"composition failed: {runtime.storage_error!r}")
    return runtime, elapsed


import ha_mcp_engineering.governance.service as service_module  # noqa: E402

# Python 3.12 cProfile uses sys.monitoring, which is process-wide, so the
# asyncio.to_thread validation worker is included in the profile.


async def one_call(service, label, profile_name=None):
    before = nav(service)
    profiler = cProfile.Profile() if profile_name else None
    started = time.perf_counter()
    if profiler:
        profiler.enable()
    result = await service.async_health_summary(
        home_assistant_status="not_checked",
        home_assistant_websocket_status="not_checked",
    )
    if profiler:
        profiler.disable()
    wall = (time.perf_counter() - started) * 1000
    after = nav(service)
    scaling = result.get("plan_store_scaling") or {}
    hot = ((scaling.get("hot_paths") or {}).get("governance_health")) or {}
    row = {
        "call": label,
        "wall_ms": round(wall, 1),
        "phase_elapsed_ms": hot.get("phase_elapsed_ms"),
        "navigation_delta": delta(after, before),
        # The per-call deserialization counters are reported under
        # plan_store_scaling.hot_paths.governance_health; the health cache
        # counters are top-level plan_store_scaling fields (cumulative).
        "plan_store_scaling_counters": {
            **{k: hot.get(k) for k in (
                "plan_records_deserialized",
                "terminal_plan_records_deserialized",
                "task_records_deserialized",
                "terminal_task_records_deserialized",
                "records_enumerated",
            )},
            **{k: scaling.get(k) for k in (
                "health_cache_rebuild_count",
                "health_cache_hit_count",
            )},
        },
        "governance_health_hot_path": {
            k: v for k, v in hot.items() if k != "phase_elapsed_ms"
        },
        "safety_verdict_cache": scaling.get("safety_verdict_cache"),
        "incremental_health_index": scaling.get("incremental_health_index"),
        "historical_validation": result.get("historical_validation"),
        "status": result.get("status"),
        "pending_reason": result.get("pending_reason"),
        "projection_failure_count": result.get("projection_failure_count"),
        "profiled": bool(profiler),
    }
    if profiler:
        buffer = io.StringIO()
        stats = pstats.Stats(profiler, stream=buffer)
        buffer.write(f"# source_root={SOURCE_ROOT} call={label} "
                     "(all threads)\n")
        stats.sort_stats("cumulative").print_stats(40)
        text = buffer.getvalue().replace(
            str(SOURCE_ROOT / "hass_mcp_engineering_beta") + "/", "").replace(
            str(Path(os.__file__).parent) + "/", "stdlib/")
        (HERE / profile_name).write_text(text)
        row["profile_file"] = str(HERE / profile_name)
    print(json.dumps(row, sort_keys=True), flush=True)
    return row


def environment():
    import platform
    import subprocess
    cpu = next((line.split(":", 1)[1].strip() for line in
                Path("/proc/cpuinfo").read_text().splitlines() if line.startswith("model name")), None)
    mem = next((line.split(":", 1)[1].strip() for line in
                Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemTotal")), None)
    return {
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "loadavg": Path("/proc/loadavg").read_text().split()[:3],
        "uptime": subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip(),
        "nproc": os.cpu_count(),
        "cpu_model": cpu,
        "mem_total": mem,
        "python": platform.python_version(),
    }


def source_identity():
    import hashlib
    import subprocess
    def git(*args):
        return subprocess.run(["git", "-C", str(SOURCE_ROOT), *args],
                              capture_output=True, text=True).stdout
    diff = git("diff", "HEAD", "--", "hass_mcp_engineering_beta/ha_mcp_engineering")
    return {
        "root": str(SOURCE_ROOT),
        "head": git("rev-parse", "HEAD").strip(),
        "runtime_diff_sha256": hashlib.sha256(diff.encode()).hexdigest() if diff else None,
        "runtime_diff_files": git("diff", "HEAD", "--name-only", "--",
                                  "hass_mcp_engineering_beta/ha_mcp_engineering").split(),
    }


async def mutate(service, count):
    """Rewrite K terminal plans through a real repository writer, if any."""
    repository = service.repository
    writer = getattr(repository, "save", None)
    if writer is None:
        return {"skipped": "ChangePlanRepository has no save() writer"}
    from ha_mcp_engineering.governance.storage import is_terminal_plan

    rewritten = []
    for plan in repository.list():
        if len(rewritten) >= count:
            break
        if not is_terminal_plan(plan):
            continue
        try:
            writer(plan)
        except Exception as exc:  # report the real writer's refusal
            return {"skipped": f"repository.save refused a terminal plan: {type(exc).__name__}: {exc}"[:300],
                    "rewritten": len(rewritten)}
        rewritten.append(plan.plan_id)
    return {"rewritten": len(rewritten), "writer": "ChangePlanRepository.save(unchanged loaded plan)"}


async def main():
    tmp_root = Path(os.environ.get("TMPDIR") or tempfile.gettempdir())
    tmp_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="h097-bench-", dir=tmp_root))
    store_copy = work / "store"
    try:
        started = time.perf_counter()
        shutil.copytree(Path(ARGS.store).resolve(), store_copy)
        copy_ms = (time.perf_counter() - started) * 1000
        env_before = environment()
        runtime, startup_ms = compose(store_copy)
        service = runtime.service
        print(json.dumps({"startup_construction_ms": round(startup_ms, 1),
                          "copy_ms": round(copy_ms, 1),
                          "navigation_after_construction": nav(service)}), flush=True)
        label = ARGS.label or Path(ARGS.store).name
        rows = []
        validation = None
        if ARGS.cold_mode == "startup_validation":
            starter = getattr(runtime, "start_historical_validation", None)
            if starter is None:
                raise SystemExit("--cold-mode startup_validation: source has no "
                                 "GovernanceRuntime.start_historical_validation")
            launched = time.perf_counter()
            job = starter()
            rows.append(await one_call(service, "cold_first_after_validation_launch",
                                       f"profile_cold_{label}.txt" if ARGS.profile else None))
            if job is not None:
                await asyncio.gather(job, return_exceptions=True)
            validation = {
                "launch_to_complete_ms": round((time.perf_counter() - launched) * 1000, 1),
                "state": dict(getattr(service, "_historical_validation", {}) or {}),
            }
            print(json.dumps({"historical_validation": validation}, default=str), flush=True)
            rows.append(await one_call(service, "first_after_validation_complete"))
        else:
            rows.append(await one_call(service, "cold",
                                       f"profile_cold_{label}.txt" if ARGS.profile else None))
        for index in range(ARGS.calls):
            rows.append(await one_call(
                service, f"warm{index + 1}",
                f"profile_warm_{label}.txt" if (ARGS.profile and index == 0) else None))
        mutation = None
        if ARGS.mutate:
            mutation = await mutate(service, ARGS.mutate)
            print(json.dumps({"mutation": mutation}), flush=True)
            if mutation.get("rewritten"):
                rows.append(await one_call(service, "after_mutate"))
        warm = [r for r in rows if r["call"].startswith("warm")]
        def stat(values):
            values = sorted(v for v in values if v is not None)
            if not values:
                return None
            return {"median": round(statistics.median(values), 1), "min": round(values[0], 1),
                    "max": round(values[-1], 1), "n": len(values)}
        phase_keys = sorted({k for r in warm for k in (r["phase_elapsed_ms"] or {})})
        summary = {
            "label": label,
            "cold_mode": ARGS.cold_mode,
            "source": source_identity(),
            "environment_before": env_before,
            "environment_after": environment(),
            "historical_validation": validation,
            "warm_wall_ms": stat([r["wall_ms"] for r in warm]),
            "warm_phase_ms": {k: stat([(r["phase_elapsed_ms"] or {}).get(k) for r in warm])
                              for k in phase_keys},
            "source_root": str(SOURCE_ROOT),
            "store": str(Path(ARGS.store).resolve()),
            "startup_construction_ms": round(startup_ms, 1),
            "calls": [
                {k: r[k] for k in ("call", "wall_ms", "phase_elapsed_ms", "navigation_delta",
                                   "plan_store_scaling_counters", "incremental_health_index",
                                   "status", "pending_reason", "historical_validation",
                                   "projection_failure_count", "profiled")}
                for r in rows
            ],
            "mutation": mutation,
        }
        if ARGS.json_out:
            Path(ARGS.json_out).write_text(json.dumps({"summary": summary, "rows": rows},
                                                      indent=1, sort_keys=True, default=str))
        print(json.dumps(summary, sort_keys=True, default=str))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
