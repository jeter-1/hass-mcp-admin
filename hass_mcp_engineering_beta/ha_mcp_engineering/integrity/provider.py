"""Read-only evidence provider for global configuration-integrity analysis."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import asdict
import time
from typing import Any

from ..clients.paths import rest_path
from ..dependency.bounded_sources import bounded_source_entries
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
from ..sanitization import ReferenceMarkerRegistry, sanitize_untrusted_data
from ..untrusted_text import screen_fields
from .models import IntegrityEvidenceBundle, IntegritySourceCoverage


MAX_INDEX_REFERENCES = 10_000
MAX_DYNAMIC_REFERENCES = 1_000


# HAMCP-226: the screened fields of each index reference (``untrusted_text``).
REFERENCE_IDENTIFIER_KEYS = (
    "target_entity_id",
    "source_id",
    "source_entity_id",
    "config_path",
    "blueprint_path",
    "blueprint_input",
    "possible_entity_ids",
    "literal_label_selectors",
)
REFERENCE_TEXT_KEYS = ("source_name", "excerpt", "evidence_summary", "warning")
# HAMCP-226 review 1 (H1): the screened fields of each state and registry row.
STATE_TEXT_KEYS = ("state", "last_changed")
REGISTRY_TEXT_KEYS = ("platform", "disabled_by")


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
        current_states, states_withheld, states_text = (
            states_result if states_available else ({}, 0, 0)
        )
        entity_registry, registry_withheld, registry_text = (
            registry_result if registry_available else ({}, 0, 0)
        )

        exact_references = []
        dynamic_references = []
        sanitation_failures: Counter = Counter()
        outside_dynamic_count = 0
        # HAMCP-191 D4: read the snapshot's identity record so bounded
        # extraction text survives this re-scan.
        markers = ReferenceMarkerRegistry(
            inherits=getattr(snapshot, "sanitizer_registry", None)
        )

        screened_text_count = states_text + registry_text
        for finding in snapshot.findings[:MAX_INDEX_REFERENCES]:
            source_type = _effective_source_type(asdict(finding))
            if source_type not in requested:
                continue
            screened, withheld_ids, withheld_text = screen_fields(
                asdict(finding),
                identifier_keys=REFERENCE_IDENTIFIER_KEYS,
                text_keys=REFERENCE_TEXT_KEYS,
                known_secrets=(self.secret, self.ha_token),
            )
            if withheld_ids:
                # A withheld identity cannot be named or grouped; the source
                # is reported as a coverage failure instead (HAMCP-226).
                sanitation_failures[source_type] += 1
                continue
            screened_text_count += len(withheld_text)
            safe = sanitize_untrusted_data(
                screened,
                known_secrets=(self.secret, self.ha_token),
                max_string=500,
                registry=markers,
            )
            if safe.failed_closed or not isinstance(safe.value, dict):
                sanitation_failures[source_type] += 1
                continue
            exact_references.append(safe.value)

        for item in snapshot.dynamic_references[:MAX_DYNAMIC_REFERENCES]:
            if item.source_type not in requested:
                outside_dynamic_count += 1
                continue
            screened, withheld_ids, withheld_text = screen_fields(
                asdict(item),
                identifier_keys=REFERENCE_IDENTIFIER_KEYS,
                text_keys=REFERENCE_TEXT_KEYS,
                known_secrets=(self.secret, self.ha_token),
            )
            if withheld_ids:
                sanitation_failures[item.source_type] += 1
                continue
            screened_text_count += len(withheld_text)
            safe = sanitize_untrusted_data(
                screened,
                known_secrets=(self.secret, self.ha_token),
                max_string=500,
                registry=markers,
            )
            if safe.failed_closed or not isinstance(safe.value, dict):
                sanitation_failures[item.source_type] += 1
                continue
            dynamic_references.append(safe.value)

        coverage = [
            _inventory_coverage(
                "current_states",
                ProviderCapability.CURRENT_ENTITY_STATE.value,
                available=states_available,
                examined=len(current_states),
                withheld=states_withheld,
                failure="The complete current-state inventory could not be read.",
                duration_ms=inventories_duration_ms,
            ),
            _inventory_coverage(
                "entity_registry",
                ProviderCapability.ENTITY_REGISTRY_READ.value,
                available=registry_available,
                examined=len(entity_registry),
                withheld=registry_withheld,
                failure="The complete entity-registry inventory could not be read.",
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

        # HAMCP-226: name each requested-scope source that hit a bound.
        scripts = getattr(snapshot, "script_diagnostics", None)
        bounded = bounded_source_entries(
            (
                *getattr(snapshot, "obligations", ()),
                *(
                    getattr(scripts.visible(), "bounded_obligations", ())
                    if scripts is not None
                    else ()
                ),
            ),
            known_secrets=(self.secret, self.ha_token),
            source_types=requested,
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
                **(
                    self.index.evidence_metadata(snapshot)
                    if callable(getattr(self.index, "evidence_metadata", None))
                    else {}
                ),
            },
            evidence_collection_duration_ms=(time.perf_counter() - started) * 1000,
            dynamic_outside_requested_scope_count=outside_dynamic_count,
            screened_text_count=screened_text_count,
            bounded_sources=bounded,
            orphan_scope_complete=set(requested) == set(SOURCE_TYPES),
        )

    async def _current_states(self) -> tuple[dict[str, dict[str, Any]], int, int]:
        """``(states, withheld rows, withheld text fields)``."""

        value = await self.rest_client.request("GET", rest_path("/states"))
        if not isinstance(value, list):
            raise TypeError("current-state inventory is invalid")
        value, withheld_rows, withheld_text = self._screen_inventory(
            value, STATE_TEXT_KEYS
        )
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
                # HAMCP-226: evidence for unavailable references; the rule
                # keeps it only as an ISO 8601 timestamp.
                "last_changed": str(item.get("last_changed") or "")[:64],
            }
        return output, withheld_rows, withheld_text

    async def _entity_registry(self) -> tuple[dict[str, dict[str, Any]], int, int]:
        """``(registry, withheld rows, withheld text fields)``."""

        value = await self.websocket_client.command(
            {"type": "config/entity_registry/list"}
        )
        if not isinstance(value, list):
            raise TypeError("entity-registry inventory is invalid")
        value, withheld_rows, withheld_text = self._screen_inventory(
            value, REGISTRY_TEXT_KEYS
        )
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
        return output, withheld_rows, withheld_text

    def _screen_inventory(
        self, rows: list[Any], text_keys: tuple[str, ...]
    ) -> tuple[list[Any], int, int]:
        """Screen each row's original entity ID and copied text (HAMCP-226).

        Runs before the sanitizer and the cuts below. A withheld entity ID
        drops its row, so that inventory is reported partial; a withheld
        text field keeps its row with the marker in place of the text.
        """

        output, withheld_rows, withheld_text = [], 0, 0
        for item in rows:
            if not isinstance(item, dict):
                output.append(item)
                continue
            screened, withheld_ids, withheld_fields = screen_fields(
                item,
                identifier_keys=("entity_id",),
                text_keys=text_keys,
                known_secrets=(self.secret, self.ha_token),
            )
            if withheld_ids:
                withheld_rows += 1
                continue
            withheld_text += len(withheld_fields)
            output.append(screened)
        return output, withheld_rows, withheld_text

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


def _inventory_coverage(
    source_type, capability, *, available, examined, withheld, failure, duration_ms
) -> IntegritySourceCoverage:
    warnings = [] if available else [failure]
    if withheld:
        warnings.append(
            f"{withheld} {source_type} record(s) were withheld whole by the credential screen; the inventory is partial."
        )
    return IntegritySourceCoverage(
        source_type,
        "direct_ha_api",
        capability,
        "failed" if not available else "partial" if withheld else "complete",
        items_examined=examined,
        failed_items=withheld if available else 1,
        warnings=warnings,
        duration_ms=duration_ms,
    )


def _effective_source_type(item: dict[str, Any]) -> str:
    return (
        "blueprint"
        if str(item.get("relation") or "").startswith("blueprint")
        else str(item.get("source_type") or "unknown")
    )


def _canonical_entity_id(value: str) -> bool:
    from ..dependency.extraction import valid_entity_id

    return value == value.lower() and valid_entity_id(value)
