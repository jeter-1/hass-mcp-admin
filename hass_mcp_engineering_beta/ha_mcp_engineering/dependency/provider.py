"""Direct Home Assistant evidence provider for dependency index construction."""

from __future__ import annotations

from abc import abstractmethod
import asyncio
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

    def __init__(self, rest_client, websocket_client, *, secret: str = "", concurrency: int = 5, timeout: float = 60.0):
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
        findings = []
        dynamic = []
        metadata: dict[str, dict[str, Any]] = {}
        coverage: list[SourceCoverageItem] = []

        state_started = time.perf_counter()
        METRICS.record_provider_result("standard_ha_mcp", "unavailable")
        METRICS.record_fallback_attempt()
        try:
            states = await asyncio.wait_for(self.rest_client.request("GET", "/states"), self.timeout)
            if not isinstance(states, list):
                raise TypeError("state response is not a list")
            METRICS.record_provider_result(self.provider_id, "complete")
            METRICS.record_fallback_success()
        except Exception:
            METRICS.record_provider_result(self.provider_id, "failed")
            raise

        registry = []
        registry_warning = []
        try:
            registry = await asyncio.wait_for(
                self.websocket_client.command({"type": "config/entity_registry/list"}), self.timeout
            )
            if not isinstance(registry, list):
                registry = []
                registry_warning.append("Entity registry returned an invalid response.")
            METRICS.record_provider_result(self.provider_id, "complete" if not registry_warning else "partial")
        except Exception:
            registry_warning.append("Entity registry could not be read; target metadata is partial.")
            METRICS.record_provider_result(self.provider_id, "failed")

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
        semaphore = asyncio.Semaphore(self.concurrency)

        async def fetch_automation(state):
            attrs = state.get("attributes") if isinstance(state.get("attributes"), dict) else {}
            internal_id = attrs.get("id")
            if not internal_id:
                return state, None, "Automation has no internal configuration ID."
            try:
                async with semaphore:
                    config = await asyncio.wait_for(
                        self.rest_client.request("GET", f"/config/automation/config/{internal_id}"),
                        self.timeout,
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
                registry_warning, (time.perf_counter() - state_started) * 1000, True,
                "standard_mcp_preferred with explicit transitional direct read fallback",
            )
        )
        return DependencyScanResult(findings, dynamic, metadata, coverage)


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
