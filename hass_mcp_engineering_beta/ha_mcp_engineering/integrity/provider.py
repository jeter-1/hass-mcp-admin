"""Read-only evidence provider for global configuration-integrity analysis."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import asdict
import time
from typing import Any

from ..dependency.models import SOURCE_TYPES
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
from ..sanitization import sanitize_untrusted_data
from .models import IntegrityEvidenceBundle, IntegritySourceCoverage


MAX_INDEX_REFERENCES = 10_000
MAX_DYNAMIC_REFERENCES = 1_000


class DirectHaIntegrityProvider(EngineeringEvidenceProvider):
    """Compose the shared index with one state and one registry inventory."""

    provider_id = "engineering"
    capabilities = frozenset(
        {ProviderCapability.CONFIGURATION_INTEGRITY_ANALYSIS}
    )

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
        """Return only the committed identity; never build during continuation."""

        return self.index.active_identity()

    async def fetch(self, request: EvidenceRequest) -> ProviderResult:
        started = time.perf_counter()
        try:
            bundle = await asyncio.wait_for(
                self.collect(request.query), timeout=self.timeout
            )
        except (asyncio.TimeoutError, TimeoutError):
            return ProviderResult(
                provider_id=self.provider_id,
                capability=request.capability,
                completeness=ProviderCompleteness.FAILED,
                failure=ProviderError(
                    ProviderFailureCategory.TIMEOUT,
                    "Configuration-integrity evidence collection timed out.",
                    True,
                ),
                coverage=ProviderCoverage(1, 0, ("integrity_evidence",)),
                timing_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception:
            return ProviderResult(
                provider_id=self.provider_id,
                capability=request.capability,
                completeness=ProviderCompleteness.FAILED,
                failure=ProviderError(
                    ProviderFailureCategory.UPSTREAM_ERROR,
                    "Configuration-integrity evidence collection failed.",
                    True,
                ),
                coverage=ProviderCoverage(1, 0, ("integrity_evidence",)),
                timing_ms=(time.perf_counter() - started) * 1000,
            )

        required = [item for item in bundle.coverage if item.required_for_assessment]
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
            coverage=ProviderCoverage(
                len(required), len(required) - len(missing), missing
            ),
            metadata={
                "source_count": len(bundle.coverage),
                "direct_fallback": False,
                "index_cache_hit": bool(bundle.index.get("cache_hit")),
                "index_generation": int(bundle.index.get("generation", 0)),
            },
            data=bundle,
        )

    async def collect(self, query: dict[str, Any]) -> IntegrityEvidenceBundle:
        started = time.perf_counter()
        requested = list(query["source_types"])
        snapshot, rebuilt, lookup_ms = await self.index.get(
            refresh=bool(query.get("refresh_index"))
        )

        inventories_started = time.perf_counter()
        states_result, registry_result = await asyncio.gather(
            self._current_states(),
            self._entity_registry(),
            return_exceptions=True,
        )
        inventories_duration_ms = (
            time.perf_counter() - inventories_started
        ) * 1000
        states_available = not isinstance(states_result, Exception)
        registry_available = not isinstance(registry_result, Exception)
        current_states = states_result if states_available else {}
        entity_registry = registry_result if registry_available else {}

        exact_references = []
        dynamic_references = []
        sanitation_failures: Counter = Counter()
        outside_dynamic_count = 0

        for finding in snapshot.findings[:MAX_INDEX_REFERENCES]:
            source_type = _effective_source_type(asdict(finding))
            if source_type not in requested:
                continue
            safe = sanitize_untrusted_data(
                asdict(finding),
                known_secrets=(self.secret, self.ha_token),
                max_string=500,
            )
            if safe.failed_closed or not isinstance(safe.value, dict):
                sanitation_failures[source_type] += 1
                continue
            exact_references.append(safe.value)

        for item in snapshot.dynamic_references[:MAX_DYNAMIC_REFERENCES]:
            if item.source_type not in requested:
                outside_dynamic_count += 1
                continue
            safe = sanitize_untrusted_data(
                asdict(item),
                known_secrets=(self.secret, self.ha_token),
                max_string=500,
            )
            if safe.failed_closed or not isinstance(safe.value, dict):
                sanitation_failures[item.source_type] += 1
                continue
            dynamic_references.append(safe.value)

        coverage = [
            IntegritySourceCoverage(
                "current_states",
                "direct_ha_api",
                ProviderCapability.CURRENT_ENTITY_STATE.value,
                "complete" if states_available else "failed",
                items_examined=len(current_states),
                failed_items=0 if states_available else 1,
                warnings=(
                    []
                    if states_available
                    else ["The complete current-state inventory could not be read."]
                ),
                duration_ms=inventories_duration_ms,
            ),
            IntegritySourceCoverage(
                "entity_registry",
                "direct_ha_api",
                ProviderCapability.ENTITY_REGISTRY_READ.value,
                "complete" if registry_available else "failed",
                items_examined=len(entity_registry),
                failed_items=0 if registry_available else 1,
                warnings=(
                    []
                    if registry_available
                    else ["The complete entity-registry inventory could not be read."]
                ),
                duration_ms=inventories_duration_ms,
            ),
        ]
        coverage.extend(
            self._configuration_coverage(
                snapshot.coverage,
                requested,
                exact_references,
                dynamic_references,
                sanitation_failures=sanitation_failures,
                rebuilt=rebuilt,
            )
        )

        return IntegrityEvidenceBundle(
            exact_references=exact_references,
            dynamic_references=dynamic_references,
            current_states=current_states,
            entity_registry=entity_registry,
            states_available=states_available,
            registry_available=registry_available,
            coverage=coverage,
            index={
                "fingerprint": snapshot.fingerprint,
                "generation": snapshot.generation,
                "built_at": snapshot.built_at,
                "cache_hit": not rebuilt,
                "refreshed": bool(query.get("refresh_index") and rebuilt),
                "lookup_duration_ms": round(lookup_ms, 3),
                "original_build_duration_ms": round(
                    snapshot.build_duration_ms, 3
                ),
                "current_index_build_duration_ms": round(
                    snapshot.build_duration_ms if rebuilt else 0.0, 3
                ),
            },
            evidence_collection_duration_ms=(time.perf_counter() - started) * 1000,
            dynamic_outside_requested_scope_count=outside_dynamic_count,
            orphan_scope_complete=set(requested) == set(SOURCE_TYPES),
        )

    async def _current_states(self) -> dict[str, dict[str, Any]]:
        value = await self.rest_client.request("GET", "/states")
        if not isinstance(value, list):
            raise TypeError("current-state inventory is invalid")
        safe = sanitize_untrusted_data(
            value,
            known_secrets=(self.secret, self.ha_token),
            max_string=500,
        )
        if safe.failed_closed or not isinstance(safe.value, list):
            raise TypeError("current-state inventory sanitation failed")
        output = {}
        for item in safe.value:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entity_id") or "").strip().lower()
            if not _canonical_entity_id(entity_id):
                continue
            output[entity_id] = {
                "entity_id": entity_id,
                "state": str(item.get("state") or "")[:128],
            }
        return output

    async def _entity_registry(self) -> dict[str, dict[str, Any]]:
        value = await self.websocket_client.command(
            {"type": "config/entity_registry/list"}
        )
        if not isinstance(value, list):
            raise TypeError("entity-registry inventory is invalid")
        safe = sanitize_untrusted_data(
            value,
            known_secrets=(self.secret, self.ha_token),
            max_string=500,
        )
        if safe.failed_closed or not isinstance(safe.value, list):
            raise TypeError("entity-registry inventory sanitation failed")
        output = {}
        for item in safe.value:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entity_id") or "").strip().lower()
            if not _canonical_entity_id(entity_id):
                continue
            output[entity_id] = {
                "entity_id": entity_id,
                "platform": str(item.get("platform") or "")[:128] or None,
                "disabled_by": str(item.get("disabled_by") or "")[:64] or None,
            }
        return output

    def _configuration_coverage(
        self,
        source_items,
        requested,
        exact_references,
        dynamic_references,
        *,
        sanitation_failures,
        rebuilt,
    ):
        by_source = {item.source_type: item for item in source_items}
        output = []
        for source_type in SOURCE_TYPES:
            selected = source_type in requested
            item = by_source.get(source_type)
            warnings = []
            failed = 0
            if not selected:
                completeness = "not_requested"
                provider = "none"
            elif item is None:
                completeness = "not_supported"
                provider = "none"
                warnings.append(
                    f"Reliable {source_type} configuration inspection is not supported by this beta."
                )
            elif item.completeness == "complete":
                completeness = "complete"
                provider = item.provider
                warnings.extend(item.warnings)
            elif item.completeness == "partial":
                completeness = "partial"
                provider = item.provider
                failed = item.failed_item_count
                warnings.extend(item.warnings)
            elif source_type in {"automation", "blueprint"} and item.failed_item_count:
                completeness = "failed"
                provider = item.provider
                failed = item.failed_item_count
                warnings.extend(item.warnings)
            else:
                completeness = "not_supported"
                provider = "none"
                warnings.extend(item.warnings)
            sanitation_failed = sanitation_failures.get(source_type, 0)
            if sanitation_failed:
                completeness = "partial"
                failed += sanitation_failed
                warnings.append(
                    f"{sanitation_failed} {source_type} evidence record(s) failed closed during sanitization."
                )
            examined = sum(
                _effective_source_type(reference) == source_type
                for reference in exact_references
            ) + sum(
                str(reference.get("source_type")) == source_type
                for reference in dynamic_references
            )
            output.append(
                IntegritySourceCoverage(
                    source_type,
                    provider,
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
                    items_examined=examined,
                    failed_items=failed,
                    warnings=warnings[:10],
                    duration_ms=(
                        float(item.duration_ms)
                        if rebuilt and item is not None
                        else 0.0
                    ),
                    cached_provenance=not rebuilt,
                    original_index_build_duration_ms=(
                        float(item.duration_ms) if item is not None else None
                    ),
                )
            )
        return output


def _effective_source_type(item: dict[str, Any]) -> str:
    return (
        "blueprint"
        if str(item.get("relation") or "").startswith("blueprint")
        else str(item.get("source_type") or "unknown")
    )


def _canonical_entity_id(value: str) -> bool:
    from ..dependency.extraction import valid_entity_id

    return value == value.lower() and valid_entity_id(value)
