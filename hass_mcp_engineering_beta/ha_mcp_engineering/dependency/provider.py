"""Direct Home Assistant evidence provider for dependency index construction."""

from __future__ import annotations

from abc import abstractmethod
import asyncio
from collections import Counter, defaultdict
from pathlib import Path
import time
from typing import Any

from ..facilitation import EvidenceReference
from ..observability import METRICS
from ..logging_config import redact_data
from ..providers import (
    EngineeringEvidenceProvider,
    EvidenceRequest,
    ProviderCapability,
    ProviderCompleteness,
    ProviderCoverage,
    ProviderResult,
)
from .extraction import extract_document, resolve_blueprint_roles
from .models import DependencyScanResult, SOURCE_TYPES, SourceCoverageItem


class DependencySourceProvider(EngineeringEvidenceProvider):
    provider_id = "dependency_source_provider"
    capabilities = frozenset({ProviderCapability.DEPENDENCY_ANALYSIS})

    @abstractmethod
    async def scan(self) -> DependencyScanResult:
        raise NotImplementedError


class DirectHaDependencyProvider(DependencySourceProvider):
    provider_id = "direct_ha_api"
    capabilities = frozenset(
        {
            ProviderCapability.DEPENDENCY_ANALYSIS,
            ProviderCapability.AUTOMATION_CONFIG,
            ProviderCapability.BLUEPRINT_SOURCE,
            ProviderCapability.ENTITY_REGISTRY_READ,
            ProviderCapability.CURRENT_ENTITY_STATE,
        }
    )

    def __init__(self, rest_client, websocket_client, *, secret: str = "", concurrency: int = 8, timeout: float = 60.0):
        self.rest_client = rest_client
        self.websocket_client = websocket_client
        self.secret = secret
        self.concurrency = max(1, min(concurrency, 10))
        self.timeout = max(1.0, min(timeout, 120.0))

    @property
    def available(self) -> bool:
        return True

    async def fetch(self, request: EvidenceRequest) -> ProviderResult:
        started = time.perf_counter()
        scan = await self.scan()
        completeness = ProviderCompleteness.COMPLETE
        if any(item.completeness == "partial" for item in scan.coverage):
            completeness = ProviderCompleteness.PARTIAL
        if all(item.completeness in {"unavailable", "unsupported"} for item in scan.coverage):
            completeness = ProviderCompleteness.UNAVAILABLE
        references = [
            EvidenceReference(item.evidence_id, self.provider_id, item.relation, item.evidence_summary)
            for item in scan.findings[: max(1, min(request.max_evidence, 100))]
        ]
        return ProviderResult(
            provider_id=self.provider_id,
            capability=request.capability,
            completeness=completeness,
            evidence=references,
            warnings=[warning for item in scan.coverage for warning in item.warnings][:20],
            timing_ms=(time.perf_counter() - started) * 1000,
            coverage=ProviderCoverage(len(scan.coverage), sum(item.completeness == "complete" for item in scan.coverage)),
        )

    async def scan(self) -> DependencyScanResult:
        scan_started = time.perf_counter()
        findings = []
        dynamic = []
        metadata: dict[str, dict[str, Any]] = {}
        coverage: list[SourceCoverageItem] = []
        request_counts: Counter[str] = Counter()
        request_time_ms: dict[str, float] = defaultdict(float)
        queue_wait_samples_ms: list[float] = []
        active_requests = 0
        maximum_concurrency = 0
        semaphore = asyncio.Semaphore(self.concurrency)

        async def request(operation: str, factory, *, queued: bool = False):
            nonlocal active_requests, maximum_concurrency
            queued_at = time.perf_counter()
            if queued:
                await semaphore.acquire()
                queue_wait_samples_ms.append((time.perf_counter() - queued_at) * 1000)
            started = time.perf_counter()
            request_counts[operation] += 1
            active_requests += 1
            maximum_concurrency = max(maximum_concurrency, active_requests)
            try:
                return await asyncio.wait_for(factory(), self.timeout)
            finally:
                active_requests -= 1
                request_time_ms[operation] += (time.perf_counter() - started) * 1000
                if queued:
                    semaphore.release()

        state_started = time.perf_counter()
        try:
            states = await request("states_inventory", lambda: self.rest_client.request("GET", "/states"))
            if not isinstance(states, list):
                raise TypeError("state response is not a list")
            METRICS.record_provider_result(self.provider_id, "complete", dispatched=True)
        except Exception:
            METRICS.record_provider_result(self.provider_id, "failed", dispatched=True)
            raise

        registry = []
        registry_warning = []
        try:
            registry = await request(
                "entity_registry_inventory",
                lambda: self.websocket_client.command({"type": "config/entity_registry/list"}),
            )
            if not isinstance(registry, list):
                registry = []
                registry_warning.append("Entity registry returned an invalid response.")
            METRICS.record_provider_result(
                self.provider_id,
                "complete" if not registry_warning else "partial",
                dispatched=True,
            )
        except Exception:
            registry_warning.append("Entity registry could not be read; target metadata is partial.")
            METRICS.record_provider_result(self.provider_id, "failed", dispatched=True)

        for state in states:
            entity_id = str(state.get("entity_id", "")).lower()
            if not entity_id:
                continue
            attrs = state.get("attributes") if isinstance(state.get("attributes"), dict) else {}
            metadata[entity_id] = {
                "entity_id": entity_id,
                "entity_exists": True,
                "registry_entry_exists": False,
                "domain": entity_id.split(".", 1)[0],
                "friendly_name": redact_data(attrs.get("friendly_name"), secret=self.secret, max_string=160),
                "state": redact_data(state.get("state"), secret=self.secret, max_string=128),
            }
        for entry in registry:
            entity_id = str(entry.get("entity_id", "")).lower()
            if not entity_id:
                continue
            item = metadata.setdefault(
                entity_id,
                {"entity_id": entity_id, "entity_exists": False, "domain": entity_id.split(".", 1)[0]},
            )
            item.update(
                {
                    "registry_entry_exists": True,
                    "platform": entry.get("platform"),
                    "device_id": entry.get("device_id"),
                    "area_id": entry.get("area_id"),
                    "disabled": bool(entry.get("disabled_by")),
                    "hidden": bool(entry.get("hidden_by")),
                }
            )

        automations = [state for state in states if str(state.get("entity_id", "")).startswith("automation.")]
        async def fetch_automation(state):
            attrs = state.get("attributes") if isinstance(state.get("attributes"), dict) else {}
            internal_id = attrs.get("id")
            if not internal_id:
                return state, None, "Automation has no internal configuration ID."
            try:
                config = await request(
                    "automation_config",
                    lambda: self.rest_client.request("GET", f"/config/automation/config/{internal_id}"),
                    queued=True,
                )
                if not isinstance(config, dict):
                    return state, None, "Automation configuration response was invalid."
                return state, config, None
            except Exception:
                return state, None, "Automation configuration could not be read."

        auto_started = time.perf_counter()
        results = await asyncio.gather(*(fetch_automation(state) for state in automations))
        failed = 0
        blueprint_failures = 0
        parse_started = time.perf_counter()
        for state, config, failure in results:
            attrs = state.get("attributes") if isinstance(state.get("attributes"), dict) else {}
            internal_id = str(attrs.get("id") or state.get("entity_id"))
            if failure or config is None:
                failed += 1
                continue
            extracted, unresolved = extract_document(
                source_type="automation",
                source_id=internal_id,
                source_entity_id=state.get("entity_id"),
                source_name=attrs.get("friendly_name"),
                source_state=state.get("state"),
                config=config,
                secret=self.secret,
            )
            findings.extend(extracted)
            dynamic.extend(unresolved)
            blueprint = config.get("use_blueprint")
            if isinstance(blueprint, dict):
                path = blueprint.get("path")
                parsed = _read_blueprint(path) if isinstance(path, str) else None
                if parsed is None:
                    blueprint_failures += 1
                else:
                    findings.extend(resolve_blueprint_roles(extracted, parsed, source_id=internal_id))

        automation_status = "complete" if failed == 0 else ("partial" if results else "unavailable")
        coverage.append(
            SourceCoverageItem(
                "automation", self.provider_id, ProviderCapability.AUTOMATION_CONFIG.value,
                automation_status, sum(item.source_type == "automation" for item in findings), failed,
                [f"{failed} automation configuration(s) could not be read."] if failed else [],
                (time.perf_counter() - auto_started) * 1000,
            )
        )
        blueprint_status = "complete" if blueprint_failures == 0 else "partial"
        coverage.append(
            SourceCoverageItem(
                "blueprint", self.provider_id, ProviderCapability.BLUEPRINT_SOURCE.value,
                blueprint_status, sum(item.relation.startswith("blueprint") for item in findings), blueprint_failures,
                [f"{blueprint_failures} blueprint source(s) could not be resolved; input findings were retained."] if blueprint_failures else [],
                (time.perf_counter() - auto_started) * 1000,
            )
        )
        for source_type in SOURCE_TYPES:
            if source_type in {"automation", "blueprint"}:
                continue
            coverage.append(
                SourceCoverageItem(
                    source_type, "none", f"{source_type}_configuration", "unavailable", 0, 0,
                    [f"Reliable {source_type} configuration access is not available in this beta."], 0.0,
                )
            )
        coverage.append(
            SourceCoverageItem(
                "entity_metadata", self.provider_id, ProviderCapability.CURRENT_ENTITY_STATE.value,
                "partial" if registry_warning else "complete", len(metadata), 1 if registry_warning else 0,
                registry_warning, (time.perf_counter() - state_started) * 1000, False,
                "transitional_direct exact administrative read",
            )
        )
        parsing_ms = (time.perf_counter() - parse_started) * 1000
        return DependencyScanResult(
            findings,
            dynamic,
            metadata,
            coverage,
            profile={
                "request_count": sum(request_counts.values()),
                "request_count_by_operation": dict(sorted(request_counts.items())),
                "time_by_operation_ms": {
                    key: round(value, 3) for key, value in sorted(request_time_ms.items())
                },
                "automation_count": len(automations),
                "inventory_calls_duplicated": False,
                "state_inventory_reused": True,
                "entity_registry_snapshot_reused": True,
                "configured_max_concurrency": self.concurrency,
                "observed_max_concurrency": maximum_concurrency,
                # Cumulative wait is per-request effort, not elapsed wall time.
                "queue_wait_ms": round(sum(queue_wait_samples_ms), 3),
                "cumulative_queue_wait_ms": round(sum(queue_wait_samples_ms), 3),
                "maximum_single_request_queue_wait_ms": round(
                    max(queue_wait_samples_ms, default=0.0), 3
                ),
                "average_request_queue_wait_ms": round(
                    sum(queue_wait_samples_ms) / len(queue_wait_samples_ms)
                    if queue_wait_samples_ms else 0.0,
                    3,
                ),
                "network_attempt_time_ms": round(sum(request_time_ms.values()), 3),
                "parsing_indexing_time_ms": round(parsing_ms, 3),
                "scan_wall_time_ms": round((time.perf_counter() - scan_started) * 1000, 3),
                "build_wall_clock_ms": round((time.perf_counter() - scan_started) * 1000, 3),
            },
        )


def _read_blueprint(path: str | None) -> dict[str, Any] | None:
    if not path or not path.endswith((".yaml", ".yml")):
        return None
    try:
        import yaml
    except ImportError:
        return None
    for base in ("/homeassistant/blueprints", "/config/blueprints"):
        root = Path(base, "automation").resolve()
        candidate = Path(root, path).resolve()
        if root not in candidate.parents or not candidate.is_file():
            continue
        try:
            class BlueprintLoader(yaml.SafeLoader):
                pass
            BlueprintLoader.add_constructor(
                "!input", lambda loader, node: {"__blueprint_input__": loader.construct_scalar(node)}
            )
            value = yaml.load(candidate.read_text(encoding="utf-8"), Loader=BlueprintLoader)
            return value if isinstance(value, dict) else None
        except Exception:
            return None
    return None
