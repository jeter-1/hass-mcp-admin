"""Normalize bounded HA evidence into deterministic incident events."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable

from ..reliability.timestamps import normalize_timestamp, parse_timestamp
from .models import IncidentEvent, IncidentEvidenceReference, stable_id


MAX_SUMMARY_CHARS = 320
SOURCE_PRIORITY = {
    "automation_trace": 0,
    "history": 1,
    "logbook": 2,
    "system_log": 3,
    "configuration_integrity": 4,
    "automation_reliability": 5,
    "dependency_index": 6,
    "current_state": 7,
}


def reference(
    evidence: dict[str, IncidentEvidenceReference],
    *,
    source_type: str,
    source_object: str,
    summary: Any,
    confidence: str = "exact",
    coverage_status: str = "complete",
    timestamp: Any = None,
) -> str:
    normalized = normalize_timestamp(timestamp)
    bounded = _bounded_text(summary)
    reference_id = stable_id("incident_ev", source_type, source_object, normalized, bounded)
    evidence.setdefault(
        reference_id,
        IncidentEvidenceReference(
            reference_id=reference_id,
            source_type=source_type,
            source_object=str(source_object)[:128],
            summary=bounded,
            confidence=confidence,
            coverage_status=coverage_status,
            timestamp=normalized,
        ),
    )
    return reference_id


def event(
    evidence: dict[str, IncidentEvidenceReference],
    *,
    source_type: str,
    source_object: str,
    event_type: str,
    summary: Any,
    timestamp: Any = None,
    entity_id: str | None = None,
    automation_id: str | None = None,
    run_id: str | None = None,
    integration_domain: str | None = None,
    severity: str = "info",
    confidence: str = "exact",
    coverage_status: str = "complete",
    cluster_key: str | None = None,
) -> IncidentEvent:
    normalized = normalize_timestamp(timestamp)
    ref = reference(
        evidence,
        source_type=source_type,
        source_object=source_object,
        summary=summary,
        confidence=confidence,
        coverage_status=coverage_status,
        timestamp=timestamp,
    )
    return IncidentEvent(
        event_id=stable_id(
            "incident_event",
            source_type,
            source_object,
            normalized,
            event_type,
            entity_id,
            automation_id,
            run_id,
            ref,
        ),
        timestamp=normalized,
        original_timestamp=str(timestamp)[:128] if timestamp not in (None, "") else None,
        event_type=event_type,
        source_type=source_type,
        entity_id=entity_id,
        automation_id=automation_id,
        run_id=run_id,
        integration_domain=integration_domain,
        severity=severity,
        summary=_bounded_text(summary),
        evidence_reference_ids=(ref,),
        cluster_key=cluster_key,
    )


def normalize_history(
    values: Any,
    entity_id: str,
    evidence: dict[str, IncidentEvidenceReference],
) -> list[IncidentEvent]:
    rows = values[0] if isinstance(values, list) and values and isinstance(values[0], list) else values
    if not isinstance(rows, list):
        return []
    output: list[IncidentEvent] = []
    previous = None
    for item in rows[:500]:
        if not isinstance(item, dict):
            continue
        state = str(item.get("state") or "")[:128]
        timestamp = item.get("last_changed") or item.get("last_updated")
        if state == "unavailable":
            kind = "entity_became_unavailable"
        elif previous == "unavailable" and state and state != "unavailable":
            kind = "entity_recovered"
        else:
            kind = "state_changed"
        output.append(
            event(
                evidence,
                source_type="history",
                source_object=entity_id,
                event_type=kind,
                summary=f"{entity_id} changed state to {state or 'unknown'}.",
                timestamp=timestamp,
                entity_id=entity_id,
                severity="medium" if kind == "entity_became_unavailable" else "info",
                cluster_key=entity_id,
            )
        )
        previous = state
    return output


def normalize_logbook(
    values: Any,
    entity_id: str,
    evidence: dict[str, IncidentEvidenceReference],
) -> list[IncidentEvent]:
    if not isinstance(values, list):
        return []
    output = []
    for item in values[:300]:
        if not isinstance(item, dict):
            continue
        context_entity = str(item.get("entity_id") or entity_id)[:128]
        domain = str(item.get("domain") or "")[:64] or None
        message = item.get("message") or item.get("name") or "Logbook activity was recorded."
        output.append(
            event(
                evidence,
                source_type="logbook",
                source_object=context_entity,
                event_type=("service_call_observed" if item.get("domain") and item.get("service") else "state_changed"),
                summary=message,
                timestamp=item.get("when") or item.get("timestamp") or item.get("time_fired"),
                entity_id=context_entity or None,
                integration_domain=domain,
                cluster_key=context_entity or domain,
            )
        )
    return output


def normalize_traces(
    traces: Iterable[dict[str, Any]],
    automation_id: str,
    evidence: dict[str, IncidentEvidenceReference],
) -> list[IncidentEvent]:
    output: list[IncidentEvent] = []
    for trace in list(traces)[:50]:
        if not isinstance(trace, dict):
            continue
        run_id = str(trace.get("run_id") or "")[:128] or None
        timestamp = trace.get("started_at") or trace.get("timestamp")
        output.append(event(evidence, source_type="automation_trace", source_object=run_id or automation_id,
            event_type="automation_triggered", summary="Automation trace began.", timestamp=timestamp,
            automation_id=automation_id, run_id=run_id, cluster_key=run_id or automation_id))
        if trace.get("condition_stop_step"):
            output.append(event(evidence, source_type="automation_trace", source_object=run_id or automation_id,
                event_type="automation_condition_failed", summary=f"Trace stopped at condition {trace.get('condition_stop_step')}.",
                timestamp=timestamp, automation_id=automation_id, run_id=run_id, severity="low",
                cluster_key=run_id or automation_id))
        if trace.get("error"):
            affected = str(trace.get("affected_dependency") or "")[:128] or None
            output.append(event(evidence, source_type="automation_trace", source_object=run_id or automation_id,
                event_type="automation_action_failed", summary=f"Automation action failed at {trace.get('failure_step') or trace.get('last_step') or 'an unknown step'}.",
                timestamp=timestamp, entity_id=affected, automation_id=automation_id, run_id=run_id,
                severity="medium", confidence="high", cluster_key=run_id or affected or automation_id))
        else:
            output.append(event(evidence, source_type="automation_trace", source_object=run_id or automation_id,
                event_type="automation_completed", summary="Automation trace completed without a normalized action error.",
                timestamp=trace.get("finished_at") or timestamp, automation_id=automation_id, run_id=run_id,
                cluster_key=run_id or automation_id))
        for service in list(trace.get("services") or [])[:20]:
            output.append(event(evidence, source_type="automation_trace", source_object=run_id or automation_id,
                event_type="service_call_observed", summary=f"Trace recorded service {str(service)[:128]}.", timestamp=timestamp,
                automation_id=automation_id, run_id=run_id, integration_domain=str(service).split(".", 1)[0],
                cluster_key=run_id or automation_id))
    return output


def deduplicate_and_sort(events: Iterable[IncidentEvent]) -> list[IncidentEvent]:
    values = {item.event_id: item for item in events}
    return sorted(
        values.values(),
        key=lambda item: (
            _timestamp_sort(item.timestamp),
            SOURCE_PRIORITY.get(item.source_type, 99),
            item.event_type,
            item.event_id,
        ),
    )


def _timestamp_sort(value: str | None) -> tuple[int, float]:
    parsed = parse_timestamp(value)
    if parsed is None:
        return (1, 0.0)
    return (0, parsed.astimezone(timezone.utc).timestamp())


def _bounded_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(
        r"(?i)\bauthorization\s*:\s*\S+(?:\s+\S+)?",
        "[REDACTED:header]",
        text,
    )
    return text[:MAX_SUMMARY_CHARS] or "Bounded evidence was observed."
