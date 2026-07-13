"""Engineering provider for bounded, read-only change-impact evidence."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
import re
import time
from typing import Any

from ..clients.rest import ExpectedHttpStatus
from ..dependency.models import SOURCE_TYPES
from ..dependency.service import select_dependency_findings
from ..errors import EntityNotFoundError, ErrorCode, GovernanceError
from ..providers import (
    EngineeringEvidenceProvider,
    EvidenceRequest,
    ProviderCapability,
    ProviderCompleteness,
    ProviderCoverage,
    ProviderError,
    ProviderFailureCategory,
    ProviderResult,
)
from ..reliability.timestamps import normalize_timestamp, parse_timestamp
from ..sanitization import sanitize_untrusted_data
from ..trace_normalization import fetch_normalized_trace_list
from .models import (
    ImpactEvidenceBundle,
    ImpactEvidenceReference,
    ImpactSourceCoverage,
    stable_id,
)


MAX_AFFECTED_AUTOMATIONS_FOR_TRACES = 5
MAX_SYSTEM_LOG_MATCHES = 10
TRACE_LOOKBACK_HOURS = 168
MAX_DYNAMIC_REFERENCES = 100


class DirectHaImpactProvider(EngineeringEvidenceProvider):
    """Orchestrate approved direct reads around the shared dependency index."""

    provider_id = "engineering"
    capabilities = frozenset({ProviderCapability.IMPACT_ANALYSIS})

    def __init__(
        self,
        index,
        rest_client,
        websocket_client,
        *,
        secret: str = "",
        ha_token: str = "",
        timeout: float = 60.0,
    ):
        self.index = index
        self.rest_client = rest_client
        self.websocket_client = websocket_client
        self.secret = secret
        self.ha_token = ha_token
        self.timeout = max(1.0, min(float(timeout), 120.0))

    @property
    def available(self) -> bool:
        return True

    def active_index_identity(self) -> dict[str, object]:
        """Expose only the committed dependency-index identity for cursor checks."""

        return self.index.active_identity()

    async def fetch(self, request: EvidenceRequest) -> ProviderResult:
        started = time.perf_counter()
        try:
            bundle = await asyncio.wait_for(
                self.collect(request.query), timeout=self.timeout
            )
        except EntityNotFoundError:
            raise
        except GovernanceError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            return ProviderResult(
                provider_id=self.provider_id,
                capability=request.capability,
                completeness=ProviderCompleteness.FAILED,
                failure=ProviderError(
                    ProviderFailureCategory.TIMEOUT,
                    "Change-impact evidence collection timed out.",
                    True,
                ),
                coverage=ProviderCoverage(1, 0, ("change_impact_evidence",)),
                timing_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception:
            return ProviderResult(
                provider_id=self.provider_id,
                capability=request.capability,
                completeness=ProviderCompleteness.FAILED,
                failure=ProviderError(
                    ProviderFailureCategory.UPSTREAM_ERROR,
                    "Change-impact evidence collection failed.",
                    True,
                ),
                coverage=ProviderCoverage(1, 0, ("change_impact_evidence",)),
                timing_ms=(time.perf_counter() - started) * 1000,
            )

        required = [item for item in bundle.coverage if item.required_for_assessment]
        completed = sum(item.assessment_complete for item in required)
        missing = tuple(
            item.source_type for item in required if not item.assessment_complete
        )
        completeness = (
            ProviderCompleteness.COMPLETE
            if not missing
            else ProviderCompleteness.PARTIAL
        )
        return ProviderResult(
            provider_id=self.provider_id,
            capability=request.capability,
            completeness=completeness,
            warnings=[
                warning for item in bundle.coverage for warning in item.warnings
            ][:20],
            timing_ms=(time.perf_counter() - started) * 1000,
            coverage=ProviderCoverage(len(required), completed, missing),
            metadata={
                "source_count": len(bundle.coverage),
                "direct_fallback": False,
                "index_cache_hit": bool(bundle.index.get("cache_hit")),
                "index_generation": int(bundle.index.get("generation", 0)),
            },
            data=bundle,
        )

    async def collect(self, query: dict[str, Any]) -> ImpactEvidenceBundle:
        started = time.perf_counter()
        entity_id = str(query["entity_id"])
        operation = str(query["operation"])
        replacement = query.get("replacement_entity_id") or None
        requested = list(query["source_types"])
        analysis_instant = parse_timestamp(query.get("analysis_timestamp"))
        if analysis_instant is None:
            raise ValueError("analysis timestamp is invalid")

        snapshot, rebuilt, lookup_ms = await self.index.get(
            refresh=bool(query.get("refresh_index"))
        )
        evidence: dict[str, ImpactEvidenceReference] = {}
        coverage: list[ImpactSourceCoverage] = []

        state_started = time.perf_counter()
        target_state, target_state_missing = await self._exact_state(entity_id)
        coverage.append(
            ImpactSourceCoverage(
                "target_state",
                "direct_ha_api",
                ProviderCapability.CURRENT_ENTITY_STATE.value,
                "complete",
                items_examined=0 if target_state_missing else 1,
                duration_ms=(time.perf_counter() - state_started) * 1000,
            )
        )

        registry_started = time.perf_counter()
        registry_values, registry_coverage = await self._entity_registry()
        registry_coverage.duration_ms = (
            time.perf_counter() - registry_started
        ) * 1000
        coverage.append(registry_coverage)
        target_registry = registry_values.get(entity_id)
        if target_state_missing and target_registry is None:
            if registry_coverage.completeness != "complete":
                raise GovernanceError(ErrorCode.ANALYSIS_UNAVAILABLE)
            raise EntityNotFoundError(
                details={"operation": "change_impact_analysis", "resource_id": entity_id}
            )

        target = self._target_summary(
            entity_id, target_state, target_state_missing, target_registry
        )
        target_reference = ImpactEvidenceReference(
            reference_id=stable_id(
                "impact_evidence", entity_id, "target_state", target["state_status"]
            ),
            source_type="target_state",
            source_id=entity_id,
            evidence_kind="target_state",
            summary="Exact current state-machine presence was inspected.",
            affected_object_type="entity",
            affected_object_id=entity_id,
        )
        evidence[target_reference.reference_id] = target_reference

        if target_registry is not None:
            reference = ImpactEvidenceReference(
                reference_id=stable_id(
                    "impact_evidence",
                    entity_id,
                    "entity_registry_relationship",
                    bool(target_registry.get("disabled_by")),
                ),
                source_type="entity_registry",
                source_id=entity_id,
                evidence_kind="entity_registry_relationship",
                summary="An exact entity-registry entry exists for the target.",
                affected_object_type="entity_registry_entry",
                affected_object_id=entity_id,
            )
            evidence[reference.reference_id] = reference
            if target.get("device_id"):
                reference = ImpactEvidenceReference(
                    reference_id=stable_id(
                        "impact_evidence", entity_id, "device", target["device_id"]
                    ),
                    source_type="entity_registry",
                    source_id=entity_id,
                    evidence_kind="device_registry_relationship",
                    summary="The target registry entry contains a device relationship.",
                    affected_object_type="device",
                    affected_object_id=str(target["device_id"])[:128],
                )
                evidence[reference.reference_id] = reference
                coverage.append(
                    ImpactSourceCoverage(
                        "device_registry",
                        "direct_ha_api",
                        ProviderCapability.DEVICE_REGISTRY_READ.value,
                        "partial",
                        requested=True,
                        required_for_assessment=False,
                        items_examined=1,
                        warnings=[
                            "Only the exact entity-registry device link was inspected; the full device record was not collected."
                        ],
                    )
                )
            else:
                coverage.append(
                    ImpactSourceCoverage(
                        "device_registry",
                        "direct_ha_api",
                        ProviderCapability.DEVICE_REGISTRY_READ.value,
                        "not_requested",
                        requested=False,
                        required_for_assessment=False,
                    )
                )
            if target.get("area_id"):
                reference = ImpactEvidenceReference(
                    reference_id=stable_id(
                        "impact_evidence", entity_id, "area", target["area_id"]
                    ),
                    source_type="entity_registry",
                    source_id=entity_id,
                    evidence_kind="area_relationship",
                    summary="The target registry entry contains an area relationship.",
                    affected_object_type="area",
                    affected_object_id=str(target["area_id"])[:128],
                )
                evidence[reference.reference_id] = reference
                coverage.append(
                    ImpactSourceCoverage(
                        "area_registry",
                        "direct_ha_api",
                        ProviderCapability.AREA_LOOKUP.value,
                        "partial",
                        requested=True,
                        required_for_assessment=False,
                        items_examined=1,
                        warnings=[
                            "Only the exact entity-registry area link was inspected; the full area record was not collected."
                        ],
                    )
                )
            else:
                coverage.append(
                    ImpactSourceCoverage(
                        "area_registry",
                        "direct_ha_api",
                        ProviderCapability.AREA_LOOKUP.value,
                        "not_requested",
                        requested=False,
                        required_for_assessment=False,
                    )
                )
        else:
            coverage.extend(
                (
                    ImpactSourceCoverage(
                        "device_registry",
                        "direct_ha_api",
                        ProviderCapability.DEVICE_REGISTRY_READ.value,
                        "not_requested",
                        requested=False,
                        required_for_assessment=False,
                    ),
                    ImpactSourceCoverage(
                        "area_registry",
                        "direct_ha_api",
                        ProviderCapability.AREA_LOOKUP.value,
                        "not_requested",
                        requested=False,
                        required_for_assessment=False,
                    ),
                )
            )

        replacement_conflict = False
        if replacement:
            replacement_state, replacement_state_missing = await self._exact_state(
                replacement
            )
            replacement_registry = registry_values.get(replacement)
            replacement_conflict = (
                not replacement_state_missing or replacement_registry is not None
            )
            if replacement_conflict:
                reference = ImpactEvidenceReference(
                    reference_id=stable_id(
                        "impact_evidence", replacement, "rename_destination_conflict"
                    ),
                    source_type="target_state",
                    source_id=replacement,
                    evidence_kind="rename_destination_conflict",
                    summary="Current state or registry evidence confirms that the rename destination exists.",
                    affected_object_type="entity",
                    affected_object_id=replacement,
                )
                evidence[reference.reference_id] = reference
            del replacement_state

        direct, selected = select_dependency_findings(
            snapshot.findings,
            entity_id,
            requested,
            include_indirect=bool(query["include_indirect"]),
            max_depth=int(query["max_depth"]),
        )
        direct_ids = {item.evidence_id for item in direct}
        dependencies = []
        sanitation_failures: dict[str, int] = {}
        for finding in selected:
            safe, failed = self._safe_dependency(finding, finding.evidence_id in direct_ids)
            if failed:
                sanitation_failures[safe["source_type"]] = (
                    sanitation_failures.get(safe["source_type"], 0) + 1
                )
            dependencies.append(safe)
            reference = ImpactEvidenceReference(
                reference_id=safe["reference_id"],
                source_type=safe["source_type"],
                source_id=safe["source_id"],
                evidence_kind="static_reference",
                summary=safe["summary"],
                affected_object_type=safe["affected_object_type"],
                affected_object_id=safe["affected_object_id"],
                confidence=safe["confidence"],
                configuration_paths=(safe["configuration_path"],),
                dependency_path=tuple(safe.get("dependency_path", ())),
                excerpt=safe.get("excerpt"),
            )
            evidence[reference.reference_id] = reference

        affected_sources = {
            (item["source_type"], item["source_id"]) for item in dependencies
        }
        dynamic = []
        confirmed_dynamic_count = 0
        unresolved_in_scope_count = 0
        outside_scope_count = sum(
            item.source_type not in requested
            for item in snapshot.dynamic_references
        )
        dynamic_uncertainty_counts: Counter = Counter()
        dynamic_truncated_counts: Counter = Counter()
        for item in snapshot.dynamic_references:
            if item.source_type not in requested:
                continue
            safe = sanitize_untrusted_data(
                asdict(item),
                known_secrets=(self.secret, self.ha_token),
                max_string=500,
            )
            value = safe.value if isinstance(safe.value, dict) else {}
            source_type = str(value.get("source_type") or "unknown")[:64]
            source_id = str(value.get("source_id") or "unknown")[:128]
            confirmed_target_related = (
                source_type,
                source_id,
            ) in affected_sources
            relation_status = (
                "confirmed_target_related"
                if confirmed_target_related
                else "unresolved_in_requested_scope"
            )
            if confirmed_target_related:
                confirmed_dynamic_count += 1
            else:
                unresolved_in_scope_count += 1
            dynamic_uncertainty_counts[source_type] += 1
            if safe.failed_closed:
                sanitation_failures[source_type] = (
                    sanitation_failures.get(source_type, 0) + 1
                )
            if len(dynamic) >= MAX_DYNAMIC_REFERENCES:
                dynamic_truncated_counts[source_type] += 1
                continue
            affected_id = self._affected_object_id(
                source_type or "configuration",
                source_id,
                None,
            )
            record = {
                "reference_id": str(value.get("evidence_id") or stable_id("dynamic", affected_id)),
                "source_type": source_type,
                "source_id": source_id,
                "affected_object_type": source_type or "configuration",
                "affected_object_id": affected_id,
                "configuration_path": str(value.get("config_path") or "unknown")[:256],
                "relation_status": relation_status,
            }
            dynamic.append(record)
            reference = ImpactEvidenceReference(
                reference_id=record["reference_id"],
                source_type=record["source_type"],
                source_id=record["source_id"],
                evidence_kind="unresolved_dynamic_reference",
                summary=(
                    "A dynamic reference in a confirmed target-related object could not be resolved statically."
                    if confirmed_target_related
                    else "An unresolved dynamic reference exists within the requested source scope."
                ),
                affected_object_type=record["affected_object_type"],
                affected_object_id=affected_id,
                confidence="limited",
                configuration_paths=(record["configuration_path"],),
                excerpt=str(value.get("excerpt") or "")[:300] or None,
            )
            evidence[reference.reference_id] = reference

        static_coverage = self._static_coverage(
            snapshot.coverage,
            requested,
            dependencies,
            rebuilt=rebuilt,
            sanitation_failures=sanitation_failures,
            dynamic_uncertainty_counts=dynamic_uncertainty_counts,
            dynamic_truncated_counts=dynamic_truncated_counts,
        )
        coverage.extend(static_coverage)

        traces, trace_coverage = await self._recent_traces(
            dependencies, analysis_instant
        )
        coverage.append(trace_coverage)
        for item in traces:
            reference = ImpactEvidenceReference(
                reference_id=item["reference_id"],
                source_type="automation_traces",
                source_id=item["affected_object_id"],
                evidence_kind="recent_trace_reference",
                summary="A statically affected automation has a retained recent trace header.",
                affected_object_type="automation",
                affected_object_id=item["affected_object_id"],
                confidence="high",
                timestamp=item.get("timestamp"),
            )
            evidence[reference.reference_id] = reference

        logs, log_coverage = await self._system_log(entity_id)
        coverage.append(log_coverage)
        for item in logs:
            reference = ImpactEvidenceReference(
                reference_id=item["reference_id"],
                source_type="system_log",
                source_id="bounded_snapshot",
                evidence_kind="correlated_system_log_reference",
                summary="A sanitized System Log entry contains the exact target identifier.",
                affected_object_type="entity",
                affected_object_id=entity_id,
                confidence="high",
                timestamp=item.get("timestamp"),
                excerpt=item.get("excerpt"),
            )
            evidence[reference.reference_id] = reference

        for item in coverage:
            if not item.required_for_assessment or item.assessment_complete:
                continue
            reference = ImpactEvidenceReference(
                reference_id=stable_id(
                    "impact_evidence",
                    entity_id,
                    "coverage",
                    item.source_type,
                    item.completeness,
                ),
                source_type=item.source_type,
                source_id="coverage",
                evidence_kind="source_coverage_incomplete",
                summary=f"Required {item.source_type} coverage is {item.completeness}.",
                affected_object_type="analysis",
                affected_object_id="source_coverage",
                confidence="exact",
            )
            evidence[reference.reference_id] = reference

        direct_dependencies = [item for item in dependencies if item["direct"]]
        indirect_dependencies = [item for item in dependencies if not item["direct"]]
        return ImpactEvidenceBundle(
            entity_id=entity_id,
            operation=operation,
            replacement_entity_id=replacement,
            target=target,
            replacement_conflict=replacement_conflict,
            direct_dependencies=direct_dependencies,
            indirect_dependencies=indirect_dependencies,
            dynamic_references=dynamic[:100],
            recent_traces=traces,
            system_log_entries=logs,
            evidence=evidence,
            coverage=coverage,
            index={
                "fingerprint": snapshot.fingerprint,
                "generation": snapshot.generation,
                "built_at": snapshot.built_at,
                "cache_hit": not rebuilt,
                "refreshed": bool(query.get("refresh_index") and rebuilt),
                "lookup_duration_ms": round(lookup_ms, 3),
                "original_build_duration_ms": round(snapshot.build_duration_ms, 3),
                "current_index_build_duration_ms": round(
                    snapshot.build_duration_ms if rebuilt else 0.0, 3
                ),
            },
            evidence_collection_duration_ms=(time.perf_counter() - started) * 1000,
            confirmed_target_related_dynamic_count=confirmed_dynamic_count,
            unresolved_in_requested_scope_count=unresolved_in_scope_count,
            dynamic_outside_requested_scope_count=outside_scope_count,
        )

    async def _exact_state(self, entity_id: str) -> tuple[dict[str, Any] | None, bool]:
        try:
            value = await self.rest_client.request(
                "GET", f"/states/{entity_id}", expected_statuses=frozenset({404})
            )
        except EntityNotFoundError:
            return None, True
        if isinstance(value, ExpectedHttpStatus) and value.status == 404:
            return None, True
        if not isinstance(value, dict):
            raise TypeError("entity state response is invalid")
        sanitized = sanitize_untrusted_data(
            value,
            known_secrets=(self.secret, self.ha_token),
            max_string=500,
        )
        if sanitized.failed_closed or not isinstance(sanitized.value, dict):
            raise GovernanceError(ErrorCode.ANALYSIS_UNAVAILABLE)
        return sanitized.value, False

    async def _entity_registry(
        self,
    ) -> tuple[dict[str, dict[str, Any]], ImpactSourceCoverage]:
        try:
            value = await self.websocket_client.command(
                {"type": "config/entity_registry/list"}
            )
            if not isinstance(value, list):
                raise TypeError("entity registry response is invalid")
            sanitized = sanitize_untrusted_data(
                value,
                known_secrets=(self.secret, self.ha_token),
                max_string=500,
            )
            if sanitized.failed_closed or not isinstance(sanitized.value, list):
                raise GovernanceError(ErrorCode.ANALYSIS_UNAVAILABLE)
            entries = {
                str(item.get("entity_id", "")).lower(): item
                for item in sanitized.value
                if isinstance(item, dict)
                and re.fullmatch(
                    r"[a-z0-9_]+\.[a-z0-9_]+",
                    str(item.get("entity_id", "")).lower(),
                )
            }
            return entries, ImpactSourceCoverage(
                "entity_registry",
                "direct_ha_api",
                ProviderCapability.ENTITY_REGISTRY_READ.value,
                "complete",
                items_examined=len(entries),
            )
        except GovernanceError:
            raise
        except Exception:
            return {}, ImpactSourceCoverage(
                "entity_registry",
                "direct_ha_api",
                ProviderCapability.ENTITY_REGISTRY_READ.value,
                "unavailable",
                items_examined=0,
                failed_items=1,
                warnings=["Exact entity-registry evidence was unavailable."],
            )

    def _target_summary(self, entity_id, state, missing, registry):
        attrs = state.get("attributes") if isinstance(state, dict) and isinstance(state.get("attributes"), dict) else {}
        raw_state = str(state.get("state", "")) if isinstance(state, dict) else ""
        state_status = (
            "missing"
            if missing
            else "unavailable"
            if raw_state.lower() == "unavailable"
            else "unknown"
            if raw_state.lower() == "unknown"
            else "available"
        )
        value = {
            "entity_id": entity_id,
            "domain": entity_id.split(".", 1)[0],
            "state_status": state_status,
            "state_machine_entry_exists": not missing,
            "registry_entry_exists": registry is not None,
            "friendly_name": attrs.get("friendly_name") if attrs else None,
            "platform": registry.get("platform") if registry else None,
            "device_id": registry.get("device_id") if registry else None,
            "area_id": registry.get("area_id") if registry else None,
            "disabled": bool(registry and registry.get("disabled_by")),
            "hidden": bool(registry and registry.get("hidden_by")),
        }
        safe = sanitize_untrusted_data(
            value,
            known_secrets=(self.secret, self.ha_token),
            max_string=200,
        )
        if safe.failed_closed or not isinstance(safe.value, dict):
            raise GovernanceError(ErrorCode.ANALYSIS_UNAVAILABLE)
        return {key: item for key, item in safe.value.items() if item is not None}

    def _safe_dependency(self, finding, direct: bool):
        sanitized = sanitize_untrusted_data(
            asdict(finding),
            known_secrets=(self.secret, self.ha_token),
            max_string=500,
        )
        value = sanitized.value if isinstance(sanitized.value, dict) else {}
        source_type = str(value.get("source_type") or "unknown")[:64]
        source_id = str(value.get("source_id") or "unknown")[:128]
        affected_id = self._affected_object_id(
            source_type, source_id, value.get("source_entity_id")
        )
        return {
            "reference_id": str(
                value.get("evidence_id")
                or stable_id("impact_evidence", source_type, source_id)
            )[:128],
            "source_type": source_type,
            "source_id": source_id,
            "affected_object_type": source_type,
            "affected_object_id": affected_id,
            "relation": str(value.get("relation") or "reference")[:64],
            "configuration_path": str(value.get("config_path") or "unknown")[:256],
            "direct": bool(direct),
            "depth": max(1, min(int(value.get("depth") or 1), 3)),
            "confidence": str(value.get("confidence") or "exact")[:32],
            "summary": str(
                value.get("evidence_summary")
                or "Exact structured entity reference."
            )[:300],
            "dependency_path": tuple(
                str(item)[:128] for item in list(value.get("evidence_path") or ())[:10]
            ),
            "excerpt": str(value.get("excerpt") or "")[:300] or None,
        }, sanitized.failed_closed

    @staticmethod
    def _affected_object_id(source_type, source_id, source_entity_id):
        if source_entity_id:
            return str(source_entity_id)[:128]
        return f"{source_type}:{source_id}"[:128]

    def _static_coverage(
        self,
        source_items,
        requested,
        dependencies,
        *,
        rebuilt,
        sanitation_failures,
        dynamic_uncertainty_counts,
        dynamic_truncated_counts,
    ):
        by_source = {item.source_type: item for item in source_items}
        output = []
        for source_type in SOURCE_TYPES:
            selected = source_type in requested
            item = by_source.get(source_type)
            if not selected:
                completeness = "not_requested"
                warnings = []
                failed = 0
                duration = 0.0
            elif item is None:
                completeness = "not_supported"
                warnings = [
                    f"Reliable {source_type} configuration inspection is not supported by this beta."
                ]
                failed = 0
                duration = 0.0
            else:
                completeness = item.completeness
                if completeness in {"unavailable", "unsupported"} and source_type not in {
                    "automation",
                    "blueprint",
                }:
                    completeness = "not_supported"
                warnings = list(item.warnings)
                failed = item.failed_item_count
                duration = 0.0 if not rebuilt else item.duration_ms
            sanitation_failed = sanitation_failures.get(source_type, 0)
            if sanitation_failed:
                completeness = "partial"
                failed += sanitation_failed
                warnings.append(
                    f"{sanitation_failed} {source_type} evidence field(s) failed closed during sanitization."
                )
            unresolved_dynamic = dynamic_uncertainty_counts.get(source_type, 0)
            if unresolved_dynamic:
                if completeness == "complete":
                    completeness = "partial"
                warnings.append(
                    f"{unresolved_dynamic} unresolved dynamic reference(s) in {source_type} require manual review."
                )
            truncated_dynamic = dynamic_truncated_counts.get(source_type, 0)
            if truncated_dynamic:
                if completeness == "complete":
                    completeness = "partial"
                warnings.append(
                    f"{truncated_dynamic} additional {source_type} dynamic reference(s) exceeded the bounded evidence payload."
                )
            output.append(
                ImpactSourceCoverage(
                    source_type,
                    "direct_ha_api" if selected else "none",
                    (
                        ProviderCapability.AUTOMATION_CONFIG.value
                        if source_type == "automation"
                        else ProviderCapability.BLUEPRINT_SOURCE.value
                        if source_type == "blueprint"
                        else f"{source_type}_configuration"
                    ),
                    completeness,
                    requested=selected,
                    required_for_assessment=selected,
                    items_examined=sum(
                        item["source_type"] == source_type for item in dependencies
                    ),
                    failed_items=failed,
                    warnings=warnings[:10],
                    duration_ms=duration,
                    truncated=bool(truncated_dynamic),
                    cached_provenance=not rebuilt,
                    original_index_build_duration_ms=(
                        item.duration_ms if item is not None else None
                    ),
                )
            )
        return output

    async def _recent_traces(self, dependencies, analysis_instant: datetime):
        started = time.perf_counter()
        automations = sorted(
            {
                item["source_id"]
                for item in dependencies
                if item["source_type"] == "automation" and item["direct"]
            }
        )
        if not automations:
            return [], ImpactSourceCoverage(
                "automation_traces",
                "direct_ha_api",
                ProviderCapability.AUTOMATION_TRACE.value,
                "not_requested",
                requested=False,
                required_for_assessment=False,
            )
        selected = automations[:MAX_AFFECTED_AUTOMATIONS_FOR_TRACES]
        failed = 0
        malformed = 0
        records = []
        cutoff = analysis_instant - timedelta(hours=TRACE_LOOKBACK_HOURS)
        for automation_id in selected:
            try:
                normalized = await fetch_normalized_trace_list(
                    self.websocket_client.command,
                    automation_id,
                    known_secrets=(self.secret, self.ha_token),
                )
                malformed += normalized.malformed_entries
                eligible = [
                    item for item in normalized.headers if item.started_instant >= cutoff
                ]
                if eligible:
                    most_recent = eligible[0]
                    affected_id = next(
                        (
                            item["affected_object_id"]
                            for item in dependencies
                            if item["source_type"] == "automation"
                            and item["source_id"] == automation_id
                        ),
                        f"automation:{automation_id}",
                    )
                    records.append(
                        {
                            "reference_id": stable_id(
                                "impact_evidence",
                                "trace",
                                automation_id,
                                most_recent.started_at,
                            ),
                            "affected_object_id": affected_id,
                            "timestamp": most_recent.started_at,
                        }
                    )
            except Exception:
                failed += 1
        truncated = len(automations) > len(selected)
        completeness = "partial" if failed or malformed or truncated else "complete"
        warnings = []
        if truncated:
            warnings.append(
                f"Recent trace inspection was capped at {MAX_AFFECTED_AUTOMATIONS_FOR_TRACES} affected automations."
            )
        if failed:
            warnings.append(f"{failed} affected automation trace list(s) were unavailable.")
        if malformed:
            warnings.append(f"{malformed} trace header(s) were malformed and excluded.")
        return records, ImpactSourceCoverage(
            "automation_traces",
            "direct_ha_api",
            ProviderCapability.AUTOMATION_TRACE.value,
            completeness,
            requested=True,
            required_for_assessment=False,
            items_examined=len(selected) - failed,
            failed_items=failed + malformed,
            warnings=warnings,
            duration_ms=(time.perf_counter() - started) * 1000,
            truncated=truncated,
            snapshot_completeness=completeness,
            retention_coverage="bounded_home_assistant_retention",
        )

    async def _system_log(self, entity_id: str):
        started = time.perf_counter()
        try:
            raw = await self.websocket_client.command({"type": "system_log/list"})
            if not isinstance(raw, list):
                raise TypeError("system log response is invalid")
            sanitation = sanitize_untrusted_data(
                raw,
                known_secrets=(self.secret, self.ha_token),
                max_string=2_048,
            )
            if not isinstance(sanitation.value, list):
                raise TypeError("sanitized system log response is invalid")
            exact = re.compile(
                rf"(?<![a-z0-9_]){re.escape(entity_id.lower())}(?![a-z0-9_])"
            )
            matched = []
            for item in sanitation.value:
                if not isinstance(item, dict):
                    continue
                encoded = json.dumps(item, sort_keys=True, default=str)
                if not exact.search(encoded.lower()):
                    continue
                messages = item.get("message") or []
                if isinstance(messages, str):
                    messages = [messages]
                excerpt = " ".join(str(value) for value in list(messages)[:2])[:400]
                timestamp = normalize_timestamp(
                    item.get("timestamp")
                    or item.get("last_occurred")
                    or item.get("first_occurred")
                )
                matched.append(
                    {
                        "reference_id": stable_id(
                            "impact_evidence",
                            "system_log",
                            entity_id,
                            timestamp,
                            excerpt,
                        ),
                        "timestamp": timestamp,
                        "excerpt": excerpt or "Sanitized correlated System Log entry.",
                    }
                )
            truncated = len(matched) > MAX_SYSTEM_LOG_MATCHES
            bounded = matched[:MAX_SYSTEM_LOG_MATCHES]
            warnings = [
                "System Log is an in-memory deduplicated snapshot; retention completeness is unknown."
            ]
            if truncated:
                warnings.append(
                    f"Correlated System Log evidence was capped at {MAX_SYSTEM_LOG_MATCHES} entries."
                )
            if sanitation.failed_closed:
                warnings.append(
                    "One or more System Log fields failed closed during sanitization."
                )
            return bounded, ImpactSourceCoverage(
                "system_log",
                "direct_ha_api",
                ProviderCapability.ERROR_LOG_READ.value,
                "partial",
                requested=True,
                required_for_assessment=False,
                items_examined=len(bounded),
                failed_items=1 if sanitation.failed_closed else 0,
                warnings=warnings,
                duration_ms=(time.perf_counter() - started) * 1000,
                truncated=truncated,
                snapshot_completeness=(
                    "partial" if truncated or sanitation.failed_closed else "complete"
                ),
                retention_coverage="unknown",
            )
        except Exception:
            return [], ImpactSourceCoverage(
                "system_log",
                "direct_ha_api",
                ProviderCapability.ERROR_LOG_READ.value,
                "unavailable",
                requested=True,
                required_for_assessment=False,
                items_examined=0,
                failed_items=1,
                warnings=["Sanitized System Log evidence was unavailable."],
                duration_ms=(time.perf_counter() - started) * 1000,
                snapshot_completeness="unavailable",
                retention_coverage="unknown",
            )
