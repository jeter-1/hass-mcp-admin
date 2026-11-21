"""HAMCP-209 offline index-build measurement (synthetic, no live system).

Runs one dependency-index build against the synthetic Home Assistant model in
``tests/hamcp209_fixtures.py``: N automations, with Core's stored-config route
modelled as one ``mutation_lock`` and a whole-file re-parse per stored read or
write (cost = ``PARSE_SECONDS`` x stored automations). The default parse cost,
2.65 ms per automation, gives the ~0.30 s per serialized read seen live at
N=113 (HAMCP-171: 33.7-41.5 s builds).

During the build a governed-style automation POST is issued 50 ms in (Core's
same lock), and a second index reader arrives, as a write right after restart
does while the startup prewarm builds. The write is also timed on an idle model.

Usage (the tree under test supplies ``ha_mcp_engineering``):
    python docs/evidence/hamcp209/measure.py --tree <tree> --label <name> --output out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--automations", type=int, default=113)
    parser.add_argument("--parse-seconds", type=float, default=0.00265)
    parser.add_argument("--websocket-seconds-per-command", type=float, default=0.0005)
    arguments = parser.parse_args()
    sys.path.insert(0, str(arguments.tree.resolve() / "hass_mcp_engineering_beta"))
    sys.path.insert(1, str(ROOT))
    report = asyncio.run(measure(arguments))
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("label", "build_wall_ms", "automation_config_rest_reads",
                                                   "concurrent_write_ms", "index_waiter_ms")}))
    return 0


async def measure(arguments) -> dict:
    from ha_mcp_engineering.dependency.index import DependencyIndex
    from ha_mcp_engineering.dependency.provider import DirectHaDependencyProvider
    from tests.hamcp209_fixtures import SyntheticHomeAssistant, generated_automations

    def model():
        return SyntheticHomeAssistant(
            generated_automations(arguments.automations),
            parse_seconds_per_automation=arguments.parse_seconds,
            websocket_seconds_per_command=arguments.websocket_seconds_per_command,
        )

    # Idle write: the same POST with nothing else holding Core's lock.
    idle = model()
    started = time.perf_counter()
    await idle.rest.request("POST", "/config/automation/config/synthetic_idle_write", body={"id": "x"})
    idle_write_ms = (time.perf_counter() - started) * 1000

    ha = model()
    provider = DirectHaDependencyProvider(ha.rest, ha.websocket, secret="synthetic-not-a-credential")
    scans = []

    class Capture:
        provider_id = provider.provider_id

        async def scan(self):
            result = await provider.scan()
            scans.append(result)
            return result

    index = DependencyIndex(Capture())
    build_started = time.perf_counter()
    build = asyncio.create_task(index.get(refresh=True))
    await asyncio.sleep(0.05)
    build_running_at_write = not build.done()

    async def write():
        started = time.perf_counter()
        await ha.rest.request("POST", "/config/automation/config/synthetic_bulk_0000",
                              body=dict(ha.automations["automation.synthetic_bulk_0000"]["stored"]))
        return (time.perf_counter() - started) * 1000

    async def waiter():
        started = time.perf_counter()
        await index.get()
        return (time.perf_counter() - started) * 1000

    write_ms, waiter_ms = await asyncio.gather(write(), waiter())
    await build
    build_wall_ms = (time.perf_counter() - build_started) * 1000
    profile = scans[0].profile
    coverage = next(item for item in scans[0].coverage if item.source_type == "automation")
    read_waits = sorted(ha.lock_waits)
    return {
        "label": arguments.label,
        "synthetic": True,
        "automations": arguments.automations,
        "parse_seconds_per_automation": arguments.parse_seconds,
        "modelled_seconds_per_serialized_read": round(arguments.parse_seconds * arguments.automations, 4),
        "websocket_seconds_per_command": arguments.websocket_seconds_per_command,
        "build_wall_ms": round(build_wall_ms, 1),
        "scan_wall_ms": profile.get("scan_wall_time_ms"),
        "request_count": profile.get("request_count"),
        "request_count_by_operation": profile.get("request_count_by_operation"),
        "automation_config_rest_reads": ha.rest_counts["automation_config_read"],
        "websocket_batch_connections": len(ha.batch_calls),
        "websocket_batch_commands": sum(len(item) for item in ha.batch_calls),
        "average_request_queue_wait_ms": profile.get("average_request_queue_wait_ms"),
        "maximum_single_request_queue_wait_ms": profile.get("maximum_single_request_queue_wait_ms"),
        "cumulative_queue_wait_ms": profile.get("cumulative_queue_wait_ms"),
        "core_lock_wait_max_ms": round(read_waits[-1] * 1000, 1) if read_waits else 0.0,
        "automation_config_source": profile.get("automation_config_source", "stored_rest (pre-HAMCP-209)"),
        "automation_coverage": {
            "completeness": coverage.completeness,
            "failed_item_count": coverage.failed_item_count,
        },
        "idle_write_ms": round(idle_write_ms, 1),
        "concurrent_write_ms": round(write_ms, 1),
        "index_waiter_ms": round(waiter_ms, 1),
        "build_running_when_write_issued": build_running_at_write,
    }


if __name__ == "__main__":
    raise SystemExit(main())
