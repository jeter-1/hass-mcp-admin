"""Direct Home Assistant evidence provider for dependency index construction."""

from __future__ import annotations

from abc import abstractmethod
import asyncio
from collections import Counter, defaultdict
import hashlib
import json
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
from .extraction import (
    extract_document,
    resolve_blueprint_roles,
    valid_entity_id,
)
from .models import (
    AutomationReadFailure,
    AutomationActionRiskProfile,
    DependencyScanResult,
    SOURCE_TYPES,
    SourceCoverageItem,
)


MAX_LABEL_REGISTRY_ENTRIES = 1_000
MAX_LABEL_MEMBERSHIP = 128
MAX_LITERAL_LABEL_SELECTORS = 256
MAX_ENTITY_LABELS = 64


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
        # Import after the dependency package is initialized; governance uses
        # this shared index at runtime and a module-level import would create a
        # package initialization cycle.
        from ..governance.risk import (
            automation_action_consequence_profile,
        )

        scan_started = time.perf_counter()
        findings = []
        dynamic = []
        automation_action_profiles: list[
            AutomationActionRiskProfile
        ] = []
        automation_read_failures: list[AutomationReadFailure] = []
        metadata: dict[str, dict[str, Any]] = {}
        label_memberships: dict[str, tuple[str, ...]] = {}
        label_membership_fingerprints: dict[str, str] = {}
        label_membership_truncated: tuple[str, ...] = ()
        label_registry_complete = False
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
                return state, None, "automation_id_missing"
            try:
                config = await request(
                    "automation_config",
                    lambda: self.rest_client.request("GET", f"/config/automation/config/{internal_id}"),
                    queued=True,
                )
                if not isinstance(config, dict):
                    return state, None, "automation_config_invalid"
                return state, config, None
            except Exception:
                return state, None, "automation_config_unreadable"

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
                automation_read_failures.append(
                    AutomationReadFailure(
                        source_id=internal_id,
                        source_entity_id=(
                            str(state.get("entity_id"))
                            if state.get("entity_id")
                            else None
                        ),
                        reason_code=str(
                            failure or "automation_config_unreadable"
                        ),
                    )
                )
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
            action_config = config
            if isinstance(blueprint, dict):
                path = blueprint.get("path")
                parsed = _read_blueprint(path) if isinstance(path, str) else None
                if parsed is None:
                    blueprint_failures += 1
                    action_config = {
                        "action": [
                            {"service": "{{ unresolved_blueprint_action }}"}
                        ]
                    }
                else:
                    findings.extend(resolve_blueprint_roles(extracted, parsed, source_id=internal_id))
                    action_config = parsed
            consequence = automation_action_consequence_profile(
                action_config
            )
            automation_action_profiles.append(
                AutomationActionRiskProfile(
                    source_id=internal_id,
                    source_entity_id=(
                        str(state.get("entity_id"))
                        if state.get("entity_id")
                        else None
                    ),
                    risk_level=str(consequence["risk_level"]),
                    physical_consequence=str(
                        consequence["physical_consequence"]
                    ),
                    complete=bool(consequence["complete"]),
                    truncated=bool(consequence["truncated"]),
                    action_domains=tuple(
                        str(item)
                        for item in consequence["action_domains"]
                    ),
                    services=tuple(
                        str(item) for item in consequence["services"]
                    ),
                    reason_codes=tuple(
                        str(item)
                        for item in consequence["reason_codes"]
                    ),
                    effect_projection_model=str(
                        consequence["effect_projection_model"]
                    ),
                    effect_targets=tuple(
                        str(item)
                        for item in consequence["effect_targets"]
                    ),
                    effect_data=tuple(
                        str(item)
                        for item in consequence["effect_data"]
                    ),
                    effect_structure_fingerprint=str(
                        consequence["effect_structure_fingerprint"]
                    ),
                    effect_projection_fingerprint=str(
                        consequence["effect_projection_fingerprint"]
                    ),
                    effect_projection_clipped=bool(
                        consequence["effect_projection_clipped"]
                    ),
                    evidence_fingerprint=str(
                        consequence["evidence_fingerprint"]
                    ),
                )
            )

        literal_label_selectors = sorted(
            {
                selector
                for item in dynamic
                for selector in item.literal_label_selectors
            },
            key=lambda item: item.encode("utf-8"),
        )
        label_warning: list[str] = []
        if literal_label_selectors:
            if len(literal_label_selectors) > MAX_LITERAL_LABEL_SELECTORS:
                literal_label_selectors = literal_label_selectors[
                    :MAX_LITERAL_LABEL_SELECTORS
                ]
                label_warning.append(
                    "Literal label selectors exceeded the bounded dependency payload."
                )
            label_registry: list[Any] = []
            try:
                label_registry = await request(
                    "label_registry_inventory",
                    lambda: self.websocket_client.command(
                        {"type": "config/label_registry/list"}
                    ),
                )
                if not isinstance(label_registry, list):
                    label_registry = []
                    label_warning.append(
                        "Label registry returned an invalid response."
                    )
                elif len(label_registry) > MAX_LABEL_REGISTRY_ENTRIES:
                    label_registry = sorted(
                        label_registry,
                        key=lambda item: json.dumps(
                            item, sort_keys=True, default=str
                        ),
                    )[:MAX_LABEL_REGISTRY_ENTRIES]
                    label_warning.append(
                        "Label registry exceeded the bounded dependency payload."
                    )
            except Exception:
                label_warning.append(
                    "Label registry could not be read for dynamic dependency resolution."
                )
            (
                label_memberships,
                label_membership_fingerprints,
                label_membership_truncated,
                membership_complete,
            ) = _build_label_membership_evidence(
                literal_label_selectors,
                entity_registry=registry,
                label_registry=label_registry,
            )
            label_registry_complete = bool(
                membership_complete
                and not registry_warning
                and not label_warning
            )
            registry_warning.extend(label_warning)

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
            automation_action_profiles=automation_action_profiles,
            automation_read_failures=automation_read_failures,
            label_memberships=label_memberships,
            label_membership_fingerprints=(
                label_membership_fingerprints
            ),
            label_membership_truncated=(
                label_membership_truncated
            ),
            label_registry_complete=label_registry_complete,
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


def _build_label_membership_evidence(
    selectors: list[str],
    *,
    entity_registry: list[Any],
    label_registry: list[Any],
) -> tuple[
    dict[str, tuple[str, ...]],
    dict[str, str],
    tuple[str, ...],
    bool,
]:
    """Resolve exact literal label names/IDs without template execution."""

    complete = True
    label_ids_by_selector: dict[str, set[str]] = {
        selector: set() for selector in selectors
    }
    for item in label_registry:
        if not isinstance(item, dict):
            complete = False
            continue
        label_id = item.get("label_id")
        name = item.get("name")
        if not isinstance(label_id, str) or not label_id:
            complete = False
            continue
        for selector in selectors:
            if selector == label_id or selector == name:
                label_ids_by_selector[selector].add(label_id)

    memberships: dict[str, set[str]] = {
        selector: set() for selector in selectors
    }
    for item in entity_registry:
        if not isinstance(item, dict):
            complete = False
            continue
        entity_id = item.get("entity_id")
        labels = item.get("labels", [])
        if not isinstance(labels, list):
            complete = False
            continue
        if len(labels) > MAX_ENTITY_LABELS:
            complete = False
            continue
        if not isinstance(entity_id, str) or not valid_entity_id(entity_id):
            continue
        exact_labels = {
            label for label in labels if isinstance(label, str)
        }
        if any(not isinstance(label, str) for label in labels):
            complete = False
        for selector, label_ids in label_ids_by_selector.items():
            if exact_labels.intersection(label_ids):
                memberships[selector].add(entity_id)

    retained: dict[str, tuple[str, ...]] = {}
    fingerprints: dict[str, str] = {}
    truncated: list[str] = []
    for selector in selectors:
        ordered = sorted(
            memberships[selector], key=lambda item: item.encode("utf-8")
        )
        encoded = json.dumps(
            ordered, separators=(",", ":"), ensure_ascii=True
        )
        fingerprints[selector] = hashlib.sha256(
            encoded.encode("utf-8")
        ).hexdigest()
        if len(ordered) > MAX_LABEL_MEMBERSHIP:
            truncated.append(selector)
            retained[selector] = tuple(ordered[:MAX_LABEL_MEMBERSHIP])
        else:
            retained[selector] = tuple(ordered)
    return retained, fingerprints, tuple(sorted(truncated)), complete
