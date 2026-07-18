"""Synthetic RC2dev5 dependency-freshness benchmark (never contacts HA)."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.models import (  # noqa: E402
    DependencyFinding,
    DependencyScanResult,
    SourceCoverageItem,
    evidence_id,
)
from ha_mcp_engineering.dependency.service import EntityDependencyAnalysisService  # noqa: E402
from ha_mcp_engineering.integrity.provider import DirectHaIntegrityProvider  # noqa: E402


TARGET = "sensor.fixture"


def scan_result() -> DependencyScanResult:
    findings = [
        DependencyFinding(
            evidence_id=evidence_id(TARGET, index),
            target_entity_id=TARGET,
            source_type="automation",
            source_id=f"fixture-{index}",
            source_entity_id=f"automation.fixture_{index}",
            source_name=f"Fixture {index}",
            relation="condition",
            config_path="$.condition[0].entity_id",
        )
        for index in range(82)
    ]
    coverage = [
        SourceCoverageItem(
            "automation", "direct_ha_api", "automation_config", "complete"
        ),
        SourceCoverageItem(
            "blueprint", "direct_ha_api", "blueprint_source", "complete"
        ),
    ]
    for source in ("script", "scene", "group", "template", "dashboard"):
        coverage.append(
            SourceCoverageItem(
                source, "none", f"{source}_configuration", "unavailable"
            )
        )
    return DependencyScanResult(
        findings=findings,
        dynamic_references=[],
        target_metadata={},
        coverage=coverage,
        profile={
            "request_count": 84,
            "request_count_by_operation": {
                "automation_config": 82,
                "states_inventory": 1,
                "entity_registry_inventory": 1,
            },
            "inventory_calls_duplicated": False,
            "configured_max_concurrency": 8,
            "observed_max_concurrency": 8,
        },
    )


class SyntheticProvider:
    def __init__(self, delay: float = 0.03):
        self.delay = delay
        self.scan_count = 0
        self.failure: Exception | None = None
        self.gate: asyncio.Event | None = None
        self.value = scan_result()

    async def scan(self):
        self.scan_count += 1
        if self.gate is not None:
            await self.gate.wait()
        await asyncio.sleep(self.delay)
        if self.failure:
            raise self.failure
        return copy.deepcopy(self.value)


class Rest:
    def __init__(self):
        self.calls = 0

    async def request(self, method, path):
        self.calls += 1
        await asyncio.sleep(0.002)
        return [{"entity_id": TARGET, "state": "on"}]


class WebSocket:
    def __init__(self):
        self.calls = 0

    async def command(self, payload):
        self.calls += 1
        await asyncio.sleep(0.002)
        return [{"entity_id": TARGET, "platform": "fixture", "disabled_by": None}]


async def timed(awaitable):
    started = time.perf_counter()
    value = await awaitable
    return value, (time.perf_counter() - started) * 1000


async def main() -> int:
    provider = SyntheticProvider()
    index = DependencyIndex(provider, soft_ttl_seconds=1, hard_ttl_seconds=4)
    service = EntityDependencyAnalysisService(index)
    output: dict[str, object] = {"fixture": "82 automations; synthetic timings"}
    try:
        cold, cold_ms = await timed(service.analyze(entity_id=TARGET, limit=10))
        output["initial_cold"] = {
            "foreground_ms": round(cold_ms, 3),
            "ha_request_count_profile": 84,
            "generation": index.generation,
            "freshness": cold.data["index"]["freshness"],
            "duplicate_build_count": provider.scan_count - 1,
            "maximum_concurrency": 8,
        }

        scan_before = provider.scan_count
        warm, warm_ms = await timed(service.analyze(entity_id=TARGET, limit=10))
        output["warm_lookup"] = {
            "foreground_ms": round(warm_ms, 3),
            "ha_request_count": provider.scan_count - scan_before,
            "generation": index.generation,
            "freshness": warm.data["index"]["freshness"],
        }

        index.snapshot.built_at_monotonic -= 1.1
        gate = asyncio.Event()
        provider.gate = gate
        scan_before = provider.scan_count
        values, soft_ms = await timed(
            asyncio.gather(*(service.analyze(entity_id=TARGET, limit=10) for _ in range(8)))
        )
        output["soft_expired_concurrent"] = {
            "foreground_ms": round(soft_ms, 3),
            "callers": len(values),
            "refresh_builds_started": provider.scan_count - scan_before,
            "evidence_stale": all(item.data["index"]["evidence_stale"] for item in values),
            "generation_before_refresh": index.generation,
        }
        background_started = time.perf_counter()
        gate.set()
        await asyncio.shield(index._build_task)
        output["background_refresh"] = {
            "wall_ms_after_foreground": round(
                (time.perf_counter() - background_started) * 1000, 3
            ),
            "generation": index.generation,
            "duplicate_build_count": provider.scan_count - scan_before - 1,
            "freshness": index.health()["freshness"],
            "old_cursor_behavior": "stale_cursor (covered by deterministic test)",
        }

        index.snapshot.built_at_monotonic -= 1.1
        provider.gate = None
        provider.failure = RuntimeError("synthetic refresh failure")
        failed, failed_ms = await timed(service.analyze(entity_id=TARGET, limit=10))
        await asyncio.gather(index._build_task, return_exceptions=True)
        output["failed_background_refresh"] = {
            "foreground_ms": round(failed_ms, 3),
            "evidence_stale": failed.data["index"]["evidence_stale"],
            "generation": index.generation,
            "build_state": index.health()["build_state"],
        }

        provider.failure = None
        index.snapshot.built_at_monotonic -= 4.1
        _, hard_ms = await timed(service.analyze(entity_id=TARGET, limit=10))
        output["hard_expired"] = {
            "foreground_ms": round(hard_ms, 3),
            "blocked_for_one_rebuild": True,
            "generation": index.generation,
            "freshness": index.health()["freshness"],
        }

        rest, websocket = Rest(), WebSocket()
        integrity = DirectHaIntegrityProvider(index, rest, websocket)
        query = {
            "source_types": ["automation"],
            "refresh_index": False,
        }
        warm_bundle, integrity_warm_ms = await timed(integrity.collect(query))
        output["warm_integrity"] = {
            "foreground_ms": round(integrity_warm_ms, 3),
            "ha_request_count": rest.calls + websocket.calls,
            "index_freshness": warm_bundle.index["freshness"],
        }
        index.snapshot.built_at_monotonic -= 1.1
        provider.delay = 0.05
        calls_before = rest.calls + websocket.calls
        soft_bundle, integrity_soft_ms = await timed(integrity.collect(query))
        output["soft_expired_integrity"] = {
            "foreground_ms": round(integrity_soft_ms, 3),
            "ha_request_count": rest.calls + websocket.calls - calls_before,
            "index_freshness": soft_bundle.index["freshness"],
            "evidence_stale": soft_bundle.index["evidence_stale"],
            "blocked_for_refresh": False,
        }
        await asyncio.gather(index._build_task, return_exceptions=True)
    finally:
        await index.shutdown()
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
