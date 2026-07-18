"""Bounded read-only evidence collection for incident correlation."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import re
import time
from typing import Any
from urllib.parse import quote

from ..dependency.extraction import valid_entity_id
from ..errors import EntityNotFoundError
from ..integrity.models import IntegrityEvidenceBundle, IntegritySourceCoverage
from ..integrity.rules import classify_integrity
from ..observability import METRICS
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
from ..reliability.rules import evaluate_rules
from ..reliability.timestamps import normalize_timestamp, parse_timestamp
from ..sanitization import sanitize_untrusted_data
from ..source_coverage import normalize_coverage
from .models import IncidentEvidenceBundle, IncidentSourceCoverage
from .normalization import (
    deduplicate_and_sort,
    event,
    normalize_history,
    normalize_logbook,
    normalize_traces,
)


MAX_RELATED_ENTITIES = 20
MAX_TIMELINE_EVENTS = 1_000
MAX_EVIDENCE_REFERENCES = 1_000
MAX_SYSTEM_LOG_ENTRIES = 200
MAX_INTEGRITY_FINDINGS = 200


class DirectHaIncidentProvider(EngineeringEvidenceProvider):
    """Engineering orchestration over explicitly approved HA read sources."""

    provider_id = "engineering"
    capabilities = frozenset({ProviderCapability.INCIDENT_CORRELATION})

    def __init__(
        self,
        dependency_index,
        rest_client,
        websocket_client,
        reliability_provider,
        *,
        secret: str = "",
        ha_token: str = "",
        timeout: float = 60.0,
        concurrency: int = 5,
    ):
        self.index = dependency_index
        self.rest_client = rest_client
        self.websocket_client = websocket_client
        self.reliability_provider = reliability_provider
        self.secret = secret
        self.ha_token = ha_token
        self.timeout = max(1.0, min(float(timeout), 120.0))
        self.concurrency = max(1, min(int(concurrency), 5))

    @property
    def available(self) -> bool:
        return True

    def active_index_identity(self) -> dict[str, object]:
        return self.index.active_identity()

    async def fetch(self, request: EvidenceRequest) -> ProviderResult:
        started = time.perf_counter()
        try:
            bundle = await asyncio.wait_for(self.collect(request.query), timeout=self.timeout)
        except (asyncio.TimeoutError, TimeoutError):
            return ProviderResult(
                provider_id=self.provider_id,
                capability=request.capability,
                completeness=ProviderCompleteness.FAILED,
                failure=ProviderError(ProviderFailureCategory.TIMEOUT, "Incident evidence collection timed out.", True),
                coverage=ProviderCoverage(10, 0, ("incident_evidence",)),
                timing_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception:
            return ProviderResult(
                provider_id=self.provider_id,
                capability=request.capability,
                completeness=ProviderCompleteness.FAILED,
                failure=ProviderError(ProviderFailureCategory.UPSTREAM_ERROR, "Incident evidence collection failed.", True),
                coverage=ProviderCoverage(10, 0, ("incident_evidence",)),
                timing_ms=(time.perf_counter() - started) * 1000,
            )
        required = [item for item in bundle.coverage if item.required_for_assessment]
        missing = tuple(item.source_type for item in required if not item.assessment_complete)
        for item in bundle.coverage:
            if not item.requested or not item.upstream_attempted or item.provider == "none":
                continue
            completeness = (
                "complete"
                if item.completeness == "complete"
                else "partial"
                if item.completeness == "partial"
                else "failed"
            )
            METRICS.record_provider_result(
                item.provider, completeness, dispatched=True
            )
        return ProviderResult(
            provider_id=self.provider_id,
            capability=request.capability,
            completeness=ProviderCompleteness.PARTIAL if missing else ProviderCompleteness.COMPLETE,
            warnings=[warning for item in bundle.coverage for warning in item.warnings][:20],
            timing_ms=(time.perf_counter() - started) * 1000,
            coverage=ProviderCoverage(len(required), len(required) - len(missing), missing),
            metadata={
                "source_count": len(bundle.coverage),
                "direct_fallback": False,
                "index_requested": bool(bundle.index.get("requested")),
                "index_cache_hit": bool(bundle.index.get("cache_hit")),
            },
            data=bundle,
        )

    async def collect(self, query: dict[str, Any]) -> IncidentEvidenceBundle:
        started = time.perf_counter()
        analysis_instant = parse_timestamp(query.get("analysis_timestamp"))
        if analysis_instant is None:
            raise ValueError("analysis timestamp is invalid")
        focus_entity = str(query.get("focus_entity_id") or "")
        automation_id = str(query.get("automation_id") or "")
        related = list(query.get("related_entity_ids") or [])[:MAX_RELATED_ENTITIES]
        entity_ids = list(dict.fromkeys([item for item in [focus_entity, *related] if item]))
        lookback_hours = int(query["lookback_hours"])
        evidence: dict[str, Any] = {}
        events = []
        coverage: list[IncidentSourceCoverage] = []

        states, states_coverage = await self._states()
        coverage.append(states_coverage)
        registry, registry_coverage = await self._registry()
        coverage.append(registry_coverage)

        semaphore = asyncio.Semaphore(self.concurrency)
        entity_tasks = [
            self._entity_activity(entity_id, lookback_hours, analysis_instant, semaphore)
            for entity_id in entity_ids
        ]
        entity_results = await asyncio.gather(*entity_tasks)
        history_examined = logbook_examined = history_failed = logbook_failed = 0
        history_duration = logbook_duration = 0.0
        for entity_id, history, logbook, history_meta, logbook_meta in entity_results:
            events.extend(normalize_history(history, entity_id, evidence))
            events.extend(normalize_logbook(logbook, entity_id, evidence))
            history_examined += history_meta[0]
            history_failed += history_meta[1]
            history_duration += history_meta[2]
            logbook_examined += logbook_meta[0]
            logbook_failed += logbook_meta[1]
            logbook_duration += logbook_meta[2]
        history_coverage = _coverage("entity_history", "history_read", bool(entity_ids), history_examined, history_failed, history_duration)
        logbook_coverage = _coverage("logbook", "logbook_read", bool(entity_ids), logbook_examined, logbook_failed, logbook_duration)
        coverage.extend((history_coverage, logbook_coverage))

        reliability_bundle = None
        reliability_findings: list[dict[str, Any]] = []
        if automation_id:
            try:
                reliability_bundle = await self.reliability_provider.collect(
                    automation_id=automation_id,
                    lookback_hours=lookback_hours,
                    trace_limit=int(query["trace_limit"]),
                    analysis_instant=analysis_instant,
                )
                events.extend(normalize_traces(reliability_bundle.traces, automation_id, evidence))
                trace_coverage = next((item for item in reliability_bundle.coverage if item.source_type == "automation_traces"), None)
                config_coverage = next((item for item in reliability_bundle.coverage if item.source_type == "automation_config"), None)
                coverage.append(_from_reliability("automation_config", config_coverage, True))
                coverage.append(_from_reliability("automation_traces", trace_coverage, True))
                if query.get("include_reliability_context"):
                    reliability_findings = [item.public() for item in evaluate_rules(reliability_bundle)][:100]
                    for item in reliability_findings:
                        events.append(event(evidence, source_type="automation_reliability", source_object=str(item.get("finding_id") or automation_id),
                            event_type="reliability_finding", summary=item.get("title") or item.get("explanation"),
                            timestamp=item.get("last_observed") or item.get("first_observed"), entity_id=item.get("affected_dependency"),
                            automation_id=automation_id, severity=_severity(item.get("severity")), confidence=str(item.get("confidence") or "medium")))
                inferred_entities = [
                    str(item.get("entity_id") or "")
                    for item in reliability_bundle.references
                    if valid_entity_id(str(item.get("entity_id") or ""))
                    and str(item.get("entity_id")) not in entity_ids
                ][: max(0, MAX_RELATED_ENTITIES - len(entity_ids))]
                if inferred_entities:
                    entity_ids.extend(inferred_entities)
                    inferred_results = await asyncio.gather(*[
                        self._entity_activity(entity_id, lookback_hours, analysis_instant, semaphore)
                        for entity_id in inferred_entities
                    ])
                    for entity_id, history, logbook, history_meta, logbook_meta in inferred_results:
                        events.extend(normalize_history(history, entity_id, evidence))
                        events.extend(normalize_logbook(logbook, entity_id, evidence))
                        _merge_activity_coverage(history_coverage, history_meta)
                        _merge_activity_coverage(logbook_coverage, logbook_meta)
            except Exception:
                coverage.append(_failed_coverage("automation_config", "automation_config", True))
                coverage.append(_failed_coverage("automation_traces", "automation_trace", True))
        else:
            coverage.append(_not_requested("automation_config", "automation_config"))
            coverage.append(_not_requested("automation_traces", "automation_trace"))

        system_log_started = time.perf_counter()
        system_log_failed = False
        if reliability_bundle is not None:
            system_logs = reliability_bundle.system_log_entries
            reliability_log_coverage = next(
                (item for item in reliability_bundle.coverage if item.source_type == "system_log"),
                None,
            )
            system_log_failed = bool(
                reliability_log_coverage
                and reliability_log_coverage.completeness
                not in {"complete", "partial"}
            )
        else:
            system_logs, system_log_failed = await self._system_log(entity_ids)
        for item in system_logs[:MAX_SYSTEM_LOG_ENTRIES]:
            if not isinstance(item, dict):
                continue
            summary = item.get("summary") or "A sanitized System Log entry matched structured incident scope."
            level = str(item.get("level") or item.get("severity") or "error").lower()
            events.append(event(evidence, source_type="system_log", source_object=str(item.get("identity") or item.get("id") or "system_log"),
                event_type="system_warning" if "warn" in level else "system_error", summary=summary,
                timestamp=item.get("timestamp"), integration_domain=str(item.get("integration_domain") or "") or None,
                severity="medium" if "error" in level else "low", confidence="medium", coverage_status="partial"))
        coverage.append(IncidentSourceCoverage(
            "system_log", "direct_ha_api", "error_log_read",
            "failed" if system_log_failed else "partial",
            True, False, len(system_logs), 1 if system_log_failed else 0,
            [
                "Sanitized System Log evidence was unavailable."
                if system_log_failed
                else "System Log is a bounded in-memory snapshot with unknown retention coverage."
            ],
            (time.perf_counter() - system_log_started) * 1000, False,
            "provider_upstream_error" if system_log_failed else None, True,
            [] if system_log_failed else ["system_log_retention_limited"],
        ))

        index_requested = bool(query.get("include_dependency_context") or query.get("include_integrity_context"))
        snapshot = None
        rebuilt = False
        lookup_ms = 0.0
        if index_requested:
            try:
                provided_snapshot = query.get("_dependency_snapshot")
                if provided_snapshot is not None:
                    if not all(hasattr(provided_snapshot, name) for name in ("generation", "fingerprint", "findings", "coverage")):
                        raise TypeError("invalid internal dependency snapshot")
                    snapshot, rebuilt, lookup_ms = provided_snapshot, False, 0.0
                else:
                    snapshot, rebuilt, lookup_ms = await self.index.get(refresh=bool(query.get("refresh_index")))
                index_coverage = _index_coverage(snapshot, rebuilt, lookup_ms)
                coverage.append(index_coverage)
                scoped_findings = _scoped_dependency_findings(snapshot, entity_ids, automation_id)
                if query.get("include_dependency_context"):
                    for item in scoped_findings[:200]:
                        events.append(event(evidence, source_type="dependency_index", source_object=item.source_id,
                            event_type="dependency_relationship", summary=f"{item.source_type} {item.source_id} has an exact dependency on {item.target_entity_id}.",
                            entity_id=item.target_entity_id, automation_id=automation_id if item.source_id == automation_id else None,
                            severity="info", confidence="exact", coverage_status=index_coverage.completeness))
            except (asyncio.TimeoutError, TimeoutError):
                coverage.append(_failed_coverage(
                    "dependency_index", "dependency_analysis", True,
                    provider="engineering", failure_category="provider_timeout",
                ))
            except Exception:
                coverage.append(_failed_coverage(
                    "dependency_index", "dependency_analysis", True,
                    provider="engineering", failure_category="provider_upstream_error",
                ))
        else:
            coverage.append(_not_requested("dependency_index", "dependency_analysis"))

        integrity_findings: list[dict[str, Any]] = []
        if query.get("include_integrity_context") and snapshot is not None:
            integrity_findings = self._integrity_context(
                snapshot,
                states,
                registry,
                entity_ids,
                automation_id,
                states_available=states_coverage.completeness == "complete",
                registry_available=registry_coverage.completeness == "complete",
            )
            for item in integrity_findings:
                events.append(event(evidence, source_type="configuration_integrity", source_object=str(item.get("finding_id")),
                    event_type="dynamic_reference_uncertainty" if item.get("finding_type") == "unresolved_dynamic_reference" else "integrity_finding",
                    summary=item.get("explanation") or item.get("finding_type"), entity_id=item.get("target_entity_id"),
                    automation_id=automation_id if item.get("source_id") == automation_id else None,
                    severity=_severity(item.get("severity")), confidence=str(item.get("confidence") or "limited")))
            coverage.append(IncidentSourceCoverage("configuration_integrity", "engineering", "configuration_integrity_analysis", "complete",
                True, False, len(integrity_findings), 0, [], 0.0, True, None, False))
        else:
            coverage.append(_not_requested("configuration_integrity", "configuration_integrity_analysis"))

        if query.get("include_reliability_context") and automation_id:
            coverage.append(IncidentSourceCoverage("automation_reliability", "engineering", "reliability_analysis",
                "complete" if reliability_bundle is not None else "failed", True, False, len(reliability_findings),
                0 if reliability_bundle is not None else 1, [], 0.0, reliability_bundle is not None,
                None if reliability_bundle is not None else "provider_upstream_error", reliability_bundle is not None))
        else:
            coverage.append(_not_requested("automation_reliability", "reliability_analysis"))

        for entity_id in entity_ids:
            state = states.get(entity_id)
            if state:
                events.append(event(evidence, source_type="current_state", source_object=entity_id, event_type="state_changed",
                    summary=f"Current state for {entity_id} is {str(state.get('state') or 'unknown')[:128]}.",
                    timestamp=state.get("last_changed") or state.get("last_updated"), entity_id=entity_id,
                    severity="medium" if state.get("state") == "unavailable" else "info"))

        ordered = deduplicate_and_sort(events)
        timeline_truncated = len(ordered) > MAX_TIMELINE_EVENTS
        ordered = ordered[:MAX_TIMELINE_EVENTS]
        evidence_truncated = len(evidence) > MAX_EVIDENCE_REFERENCES
        if evidence_truncated:
            allowed = {ref for item in ordered for ref in item.evidence_reference_ids}
            evidence = {key: evidence[key] for key in sorted(allowed)[:MAX_EVIDENCE_REFERENCES] if key in evidence}

        automation = reliability_bundle.automation if reliability_bundle is not None else {}
        index = {
            "requested": index_requested,
            "generation": snapshot.generation if snapshot is not None else None,
            "fingerprint": snapshot.fingerprint if snapshot is not None else None,
            "built_at": snapshot.built_at if snapshot is not None else None,
            "cache_hit": bool(snapshot is not None and not rebuilt),
            **(
                self.index.evidence_metadata(snapshot)
                if snapshot is not None
                and callable(getattr(self.index, "evidence_metadata", None))
                else {}
            ),
            "refreshed": bool(snapshot is not None and rebuilt and query.get("refresh_index")),
            "lookup_duration_ms": round(lookup_ms, 3),
            "current_index_build_duration_ms": round(snapshot.build_duration_ms if snapshot is not None and rebuilt else 0.0, 3),
            "original_index_build_duration_ms": round(snapshot.build_duration_ms if snapshot is not None else 0.0, 3),
        }
        return IncidentEvidenceBundle(
            focus={
                "focus_entity_id": focus_entity,
                "automation_id": automation_id,
                "automation_entity_id": str(automation.get("entity_id") or ""),
                "automation_name": str(automation.get("friendly_name") or "")[:160],
                "related_entity_ids": related,
            },
            events=ordered,
            evidence=evidence,
            coverage=coverage,
            index=index,
            reliability_findings=reliability_findings,
            integrity_findings=integrity_findings,
            collection_duration_ms=(time.perf_counter() - started) * 1000,
            evidence_truncated=evidence_truncated,
            timeline_truncated=timeline_truncated,
        )

    async def _states(self):
        started = time.perf_counter()
        try:
            value = await self.rest_client.request("GET", "/states")
            if not isinstance(value, list):
                raise TypeError("states inventory is invalid")
            safe = sanitize_untrusted_data(value, known_secrets=(self.secret, self.ha_token), max_string=500)
            if safe.failed_closed or not isinstance(safe.value, list):
                raise TypeError("states inventory sanitation failed")
            output = {}
            for item in safe.value:
                if not isinstance(item, dict):
                    continue
                entity_id = str(item.get("entity_id") or "").lower()
                if valid_entity_id(entity_id):
                    output[entity_id] = item
            return output, IncidentSourceCoverage("current_state", "direct_ha_api", "current_entity_state", "complete", True, True,
                len(output), 0, [], (time.perf_counter() - started) * 1000, False, None, True)
        except Exception:
            return {}, _failed_coverage("current_state", "current_entity_state", True, started)

    async def _registry(self):
        started = time.perf_counter()
        try:
            value = await self.websocket_client.command({"type": "config/entity_registry/list"})
            if not isinstance(value, list):
                raise TypeError("entity registry is invalid")
            safe = sanitize_untrusted_data(value, known_secrets=(self.secret, self.ha_token), max_string=500)
            if safe.failed_closed or not isinstance(safe.value, list):
                raise TypeError("entity registry sanitation failed")
            output = {}
            for item in safe.value:
                if not isinstance(item, dict):
                    continue
                entity_id = str(item.get("entity_id") or "").lower()
                if valid_entity_id(entity_id):
                    output[entity_id] = item
            return output, IncidentSourceCoverage("entity_registry", "direct_ha_api", "entity_registry_read", "complete", True, True,
                len(output), 0, [], (time.perf_counter() - started) * 1000, False, None, True)
        except Exception:
            return {}, _failed_coverage("entity_registry", "entity_registry_read", True, started)

    async def _entity_activity(self, entity_id, lookback_hours, instant, semaphore):
        start = normalize_timestamp(instant - timedelta(hours=lookback_hours))
        encoded_start = quote(start or "", safe="-:TZ.")
        encoded_entity = quote(entity_id, safe="._")
        async def call(path):
            began = time.perf_counter()
            try:
                async with semaphore:
                    value = await self.rest_client.request("GET", path)
                safe = sanitize_untrusted_data(value, known_secrets=(self.secret, self.ha_token), max_string=500)
                if safe.failed_closed:
                    raise TypeError("evidence sanitation failed")
                count = len(safe.value) if isinstance(safe.value, list) else 0
                return safe.value, (count, 0, (time.perf_counter() - began) * 1000)
            except Exception:
                return [], (0, 1, (time.perf_counter() - began) * 1000)
        history, logbook = await asyncio.gather(
            call(f"/history/period/{encoded_start}?filter_entity_id={encoded_entity}&minimal_response&no_attributes"),
            call(f"/logbook/{encoded_start}?entity={encoded_entity}"),
        )
        return entity_id, history[0], logbook[0], history[1], logbook[1]

    async def _system_log(self, entity_ids):
        try:
            value = await self.websocket_client.command({"type": "system_log/list"})
            safe = sanitize_untrusted_data(value, known_secrets=(self.secret, self.ha_token), max_string=1_024)
            if safe.failed_closed or not isinstance(safe.value, list):
                return [], True
            output = []
            for item in safe.value[:MAX_SYSTEM_LOG_ENTRIES]:
                if not isinstance(item, dict):
                    continue
                text = json.dumps(item, sort_keys=True, default=str).lower()
                if entity_ids and not any(_contains_exact(text, entity_id) for entity_id in entity_ids):
                    continue
                messages = item.get("message") or []
                if isinstance(messages, str):
                    messages = [messages]
                output.append({
                    "identity": str(item.get("hash") or item.get("id") or "system_log")[:128],
                    "timestamp": normalize_timestamp(item.get("timestamp") or item.get("last_occurred")),
                    "summary": " ".join(str(part) for part in list(messages)[:2])[:320] or "Sanitized System Log entry.",
                    "level": str(item.get("level") or "error")[:32],
                    "integration_domain": str(item.get("name") or "").split(".", 1)[0][:64],
                })
            return output, False
        except Exception:
            return [], True

    def _integrity_context(
        self,
        snapshot,
        states,
        registry,
        entity_ids,
        automation_id,
        *,
        states_available,
        registry_available,
    ):
        scoped = _scoped_dependency_findings(snapshot, entity_ids, automation_id)
        dynamic = [asdict(item) for item in snapshot.dynamic_references if automation_id and item.source_id == automation_id][:100]
        bundle = IntegrityEvidenceBundle(
            exact_references=[asdict(item) for item in scoped],
            dynamic_references=dynamic,
            current_states=states,
            entity_registry=registry,
            states_available=states_available,
            registry_available=registry_available,
            coverage=[IntegritySourceCoverage("incident_scope", "engineering", "dependency_analysis", "complete")],
            index={"generation": snapshot.generation, "fingerprint": snapshot.fingerprint},
            evidence_collection_duration_ms=0.0,
            orphan_scope_complete=False,
        )
        findings, _evidence, _warnings = classify_integrity(
            bundle,
            finding_types=["missing_entity_reference", "disabled_entity_reference", "registry_only_entity_reference", "unresolved_dynamic_reference"],
            include_orphan_candidates=False,
        )
        return [item.public() for item in findings[:MAX_INTEGRITY_FINDINGS]]


def _coverage(source_type, capability, requested, examined, failed, duration):
    if not requested:
        return _not_requested(source_type, capability)
    normalized = normalize_coverage(
        source_type=source_type,
        completeness="complete" if failed == 0 else "partial",
        requested=True,
        required=True,
        items_examined=examined,
        failed_items=failed,
    )
    return IncidentSourceCoverage(
        source_type, "direct_ha_api", capability, normalized.completeness,
        True, True, examined, normalized.failed_items,
        [] if failed == 0 else [f"{failed} bounded request(s) failed."], duration,
        False, normalized.failure_category, True, list(normalized.coverage_limitations),
    )


def _merge_activity_coverage(item, values):
    examined, failed, duration = values
    item.requested = True
    item.required_for_assessment = True
    item.items_examined += max(0, int(examined))
    item.failed_items += max(0, int(failed))
    item.duration_ms += max(0.0, float(duration))
    item.upstream_attempted = True
    normalized = normalize_coverage(
        source_type=item.source_type,
        completeness="partial" if item.failed_items else "complete",
        requested=True,
        required=item.required_for_assessment,
        items_examined=item.items_examined,
        failed_items=item.failed_items,
    )
    item.completeness = normalized.completeness
    item.failed_items = normalized.failed_items
    item.failure_category = normalized.failure_category
    item.coverage_limitations = list(normalized.coverage_limitations)
    item.warnings = [f"{item.failed_items} bounded request(s) failed."] if item.failed_items else []


def _failed_coverage(
    source_type,
    capability,
    required,
    started=None,
    *,
    provider="direct_ha_api",
    failure_category="provider_upstream_error",
):
    return IncidentSourceCoverage(source_type, provider, capability, "failed", True, required, 0, 1,
        [f"{source_type.replace('_', ' ').title()} evidence was unavailable."],
        (time.perf_counter() - started) * 1000 if isinstance(started, float) else 0.0,
        False, failure_category, True)


def _not_requested(source_type, capability):
    return IncidentSourceCoverage(source_type, "none", capability, "not_requested", False, False, 0, 0, [], 0.0, False, None, False)


def _from_reliability(source_type, item, required):
    if item is None:
        return _failed_coverage(source_type, source_type, required)
    completeness = item.completeness if item.completeness in {"complete", "partial", "failed", "not_supported", "not_requested"} else "failed"
    normalized = normalize_coverage(
        source_type=source_type,
        completeness=completeness,
        requested=completeness != "not_requested",
        required=required,
        items_examined=item.items_examined,
        failed_items=item.failed_items,
        failure_category=(
            "provider_upstream_error"
            if completeness == "failed"
            else None
        ),
        unsupported=completeness == "not_supported",
    )
    return IncidentSourceCoverage(source_type, item.provider, item.provider_capability, normalized.completeness,
        completeness != "not_requested", required, item.items_examined, normalized.failed_items,
        list(item.warnings), item.duration_ms, False, normalized.failure_category,
        completeness not in {"not_requested", "not_supported"}, list(normalized.coverage_limitations))


def _index_coverage(snapshot, rebuilt, lookup_ms):
    requested = [item for item in snapshot.coverage if item.completeness != "not_requested"]
    failed_items = sum(max(0, int(item.failed_item_count)) for item in requested)
    unsupported_types = sorted(
        item.source_type
        for item in requested
        if item.provider == "none"
        and item.failed_item_count == 0
        and item.completeness in {"unavailable", "unsupported", "not_supported"}
    )
    partial = bool(
        failed_items
        or unsupported_types
        or any(item.completeness == "partial" for item in requested)
    )
    limitation_ids = (
        ["dependency_index_unsupported_source_types"]
        if unsupported_types
        else []
    )
    normalized = normalize_coverage(
        source_type="dependency_index",
        completeness="partial" if partial else "complete",
        requested=True,
        required=True,
        items_examined=len(snapshot.findings),
        failed_items=failed_items,
        failure_category="item_read_failure" if failed_items else None,
        limitation_ids=limitation_ids,
    )
    warnings = sorted(dict.fromkeys(
        str(warning)[:240]
        for item in requested
        for warning in item.warnings
        if str(warning).strip()
    ))[:10]
    return IncidentSourceCoverage(
        "dependency_index", "engineering", "dependency_analysis",
        normalized.completeness, True, True, len(snapshot.findings),
        normalized.failed_items, warnings, lookup_ms, not rebuilt,
        normalized.failure_category, True, list(normalized.coverage_limitations),
    )


def _scoped_dependency_findings(snapshot, entity_ids, automation_id):
    scope = set(entity_ids)
    return [item for item in snapshot.findings if item.target_entity_id in scope or (automation_id and item.source_id == automation_id)][:500]


def _contains_exact(text: str, token: str) -> bool:
    return bool(token and re.search(rf"(?<![a-z0-9_.-]){re.escape(token.lower())}(?![a-z0-9_.-])", text))


def _severity(value):
    return str(value) if str(value) in {"high", "medium", "low", "info"} else "info"
