"""Deterministic, evidence-backed incident correlation rules."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from datetime import timedelta
from typing import Iterable

from ..reliability.timestamps import parse_timestamp
from .models import IncidentEvent, IncidentHypothesis, stable_id
from .normalization import deduplicate_and_sort


CONFIDENCE_RANK = {"confirmed": 0, "high": 1, "medium": 2, "low": 3, "insufficient": 4}
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}
CAUSAL_RANK = {
    "confirmed_cause": 0,
    "probable_contributor": 1,
    "possible_contributor": 2,
    "correlated_condition": 3,
    "contradictory_evidence": 4,
    "insufficient_evidence": 5,
}


def correlate(
    events: list[IncidentEvent],
    *,
    correlation_window_minutes: int,
    missing_evidence: Iterable[str] = (),
    coverage_limitations: Iterable[str] = (),
) -> tuple[list[IncidentHypothesis], int]:
    """Return stable ranked hypotheses and the number of bounded clusters."""

    events = deduplicate_and_sort(events)
    missing = tuple(dict.fromkeys(str(item)[:128] for item in missing_evidence))[:20]
    limitations = tuple(dict.fromkeys(str(item)[:128] for item in coverage_limitations))[:20]
    clusters = _clusters(events, correlation_window_minutes)
    hypotheses: list[IncidentHypothesis] = []
    for cluster_id, members in clusters:
        hypotheses.extend(_cluster_rules(cluster_id, members, missing, limitations))

    hypotheses.extend(_global_rules(events, missing, limitations))
    deduplicated: dict[tuple[str, tuple[str, ...], tuple[str, ...], str], IncidentHypothesis] = {}
    for item in hypotheses:
        key = (item.rule_id, item.automation_ids, item.affected_entity_ids, _cluster_from_id(item.hypothesis_id))
        existing = deduplicated.get(key)
        if existing is None:
            deduplicated[key] = item
            continue
        deduplicated[key] = replace(
            existing,
            supporting_evidence_reference_ids=tuple(
                dict.fromkeys((*existing.supporting_evidence_reference_ids, *item.supporting_evidence_reference_ids))
            )[:30],
            contradicting_evidence_reference_ids=tuple(
                dict.fromkeys((*existing.contradicting_evidence_reference_ids, *item.contradicting_evidence_reference_ids))
            )[:20],
        )

    ordered = sorted(
        deduplicated.values(),
        key=lambda item: (
            CONFIDENCE_RANK[item.confidence],
            SEVERITY_RANK[item.severity],
            CAUSAL_RANK[item.causal_status],
            -len(item.supporting_evidence_reference_ids),
            item.first_observed or "9999",
            item.rule_id,
            item.hypothesis_id,
        ),
    )
    return [replace(item, rank=index + 1) for index, item in enumerate(ordered)], len(clusters)


def _cluster_rules(
    cluster_id: str,
    events: list[IncidentEvent],
    missing_evidence: tuple[str, ...],
    limitations: tuple[str, ...],
):
    output: list[IncidentHypothesis] = []
    failures = [item for item in events if item.event_type == "automation_action_failed"]
    unavailable = [item for item in events if item.event_type == "entity_became_unavailable"]
    missing_references = [item for item in events if item.event_type == "integrity_finding" and "missing" in item.summary.lower()]
    services = [item for item in events if item.event_type == "service_call_observed"]
    state_changes = [item for item in events if item.event_type in {"state_changed", "entity_became_unavailable", "entity_recovered"}]
    automation_activity = [item for item in events if item.event_type.startswith("automation_")]
    system_errors = [item for item in events if item.event_type in {"system_error", "system_warning"}]
    integrity = [item for item in events if item.event_type == "integrity_finding"]
    dynamic = [item for item in events if item.event_type == "dynamic_reference_uncertainty"]
    recovered = [item for item in events if item.event_type == "entity_recovered"]
    completed = [item for item in events if item.event_type == "automation_completed"]

    if failures and unavailable:
        shared = _shared_entities(failures, unavailable)
        if shared:
            direct = any(item.entity_id in shared for item in failures)
            output.append(_hypothesis(
                "trace_failure_with_unavailable_dependency", cluster_id, events,
                "Unavailable dependency likely contributed to an automation failure",
                "high" if direct else "medium", "medium", "probable_contributor",
                "A trace failure and an unavailable related entity were observed in the same bounded incident cluster."
                + (" The trace directly identifies the dependency." if direct else " Temporal proximity alone does not establish causation."),
                [*failures, *unavailable], missing=missing_evidence, limitations=limitations, entities=shared,
            ))
    if failures and missing_references:
        output.append(_hypothesis(
            "trace_failure_with_missing_reference", cluster_id, events,
            "A confirmed missing reference may have contributed to the trace failure",
            "high", "medium", "probable_contributor",
            "A trace failure overlaps bounded configuration-integrity evidence for an exact missing reference.",
            [*failures, *missing_references], missing=missing_evidence, limitations=limitations,
        ))
    if services and state_changes:
        output.append(_hypothesis(
            "service_call_followed_by_state_change", cluster_id, events,
            "A service observation preceded or accompanied a state change",
            "medium", "low", "possible_contributor",
            "A service observation and state transition occurred within the configured correlation window; timing alone is not causal proof.",
            [*services, *state_changes], missing=missing_evidence, limitations=limitations,
        ))
    if state_changes and automation_activity and not services:
        output.append(_hypothesis(
            "unexpected_state_change_with_automation_activity", cluster_id, events,
            "Entity activity correlated with automation activity",
            "medium", "low", "correlated_condition",
            "Entity and automation activity occurred in the same bounded cluster without direct service context.",
            [*state_changes, *automation_activity], missing=missing_evidence, limitations=limitations,
        ))
    if system_errors and (failures or state_changes):
        shared_domain = bool(
            {item.integration_domain for item in system_errors if item.integration_domain}
            & {item.integration_domain for item in events if item.integration_domain and item not in system_errors}
        )
        output.append(_hypothesis(
            "integration_error_with_related_entities", cluster_id, events,
            "A structured integration error correlated with related activity",
            "medium" if shared_domain else "low", "medium", "possible_contributor",
            "Structured warning or error evidence overlaps related incident activity. Free-form text was not used as sole high-confidence evidence.",
            [*system_errors, *failures, *state_changes], missing=missing_evidence, limitations=limitations,
        ))
    if integrity:
        output.append(_hypothesis(
            "configuration_integrity_contributor", cluster_id, events,
            "Configuration-integrity evidence may contribute to the incident",
            "medium", "medium", "possible_contributor",
            "A bounded configuration-integrity finding is materially related to the focus automation or entity.",
            integrity, missing=missing_evidence, limitations=limitations,
        ))
    if dynamic:
        output.append(_hypothesis(
            "dynamic_reference_uncertainty", cluster_id, events,
            "Dynamic entity selection limits causal analysis",
            "low", "low", "insufficient_evidence",
            "A dynamic expression may select entity IDs, but no target was invented and manual review is required.",
            dynamic, missing=missing_evidence, limitations=limitations,
        ))
    if unavailable and recovered:
        output.append(_hypothesis(
            "recovery_after_dependency_restoration", cluster_id, events,
            "Related activity recovered after an entity became available",
            "medium", "low", "correlated_condition",
            "An unavailable entity later recovered inside the incident window. This supports correlation but not a definitive cause.",
            [*unavailable, *recovered], missing=missing_evidence, limitations=limitations,
        ))
    if failures and completed:
        output.append(_hypothesis(
            "conflicting_evidence", cluster_id, events,
            "Both failed and completed automation evidence is present",
            "low", "low", "contradictory_evidence",
            "Successful and failed trace evidence coexist, reducing confidence in a single explanation.",
            failures, contradicting=completed, missing=missing_evidence, limitations=limitations,
        ))
    return output


def _global_rules(
    events: list[IncidentEvent],
    missing: tuple[str, ...],
    limitations: tuple[str, ...],
):
    failures = [item for item in events if item.event_type == "automation_action_failed"]
    grouped = defaultdict(list)
    for item in failures:
        grouped[(item.automation_id, item.entity_id, item.summary)].append(item)
    output = []
    for key, values in sorted(grouped.items(), key=lambda item: str(item[0])):
        if len({item.run_id or item.event_id for item in values}) < 2:
            continue
        output.append(_hypothesis(
            "repeated_trace_failure_pattern", "global", values,
            "A trace failure pattern repeated across distinct runs", "high", "medium", "probable_contributor",
            "The same bounded failure signature occurred in more than one distinct trace run.",
            values, missing=missing, limitations=limitations,
        ))

    dependency_failures = defaultdict(list)
    for item in failures:
        if item.entity_id:
            dependency_failures[item.entity_id].append(item)
    for entity_id, values in sorted(dependency_failures.items()):
        if len({item.automation_id for item in values if item.automation_id}) < 2:
            continue
        output.append(_hypothesis(
            "shared_dependency_failure", "global", values,
            "Multiple automations share a failing dependency", "high", "medium", "probable_contributor",
            "Distinct automation failures directly identify the same dependency.", values,
            missing=missing, limitations=limitations, entities=(entity_id,),
        ))
    if not output and not any(item.event_type in {"automation_action_failed", "system_error", "integrity_finding", "entity_became_unavailable"} for item in events):
        supporting = events[:3]
        output.append(_hypothesis(
            "insufficient_evidence", "global", events,
            "Available evidence does not establish a correlated anomaly",
            "insufficient", "info", "insufficient_evidence",
            "The bounded sources did not provide enough structured evidence to identify a probable contributor.",
            supporting, missing=missing, limitations=limitations,
        ))
    return output


def _hypothesis(rule_id, cluster_id, members, title, confidence, severity, causal_status, explanation,
                supporting, *, contradicting=(), missing=(), limitations=(), entities=()):
    supporting = list(supporting)
    primary_entities = {str(item.entity_id) for item in supporting if item.entity_id}
    primary_automations = {str(item.automation_id) for item in supporting if item.automation_id}
    dependency_context = [
        item
        for item in members
        if item.event_type == "dependency_relationship"
        and (
            (item.entity_id and item.entity_id in primary_entities)
            or (item.automation_id and item.automation_id in primary_automations)
        )
    ]
    refs = tuple(dict.fromkeys(
        ref
        for item in [*supporting, *dependency_context]
        for ref in item.evidence_reference_ids
    ))[:30]
    contradict_refs = tuple(dict.fromkeys(ref for item in contradicting for ref in item.evidence_reference_ids))[:20]
    affected = tuple(sorted({*(str(item.entity_id) for item in members if item.entity_id), *entities}))
    automations = tuple(sorted({str(item.automation_id) for item in members if item.automation_id}))
    times = sorted(item.timestamp for item in members if item.timestamp)
    if contradict_refs:
        confidence = _lower_confidence(confidence)
    if missing:
        confidence = _lower_confidence(confidence)
    if limitations and confidence in {"confirmed", "high"}:
        confidence = "medium"
    return IncidentHypothesis(
        hypothesis_id=stable_id("hypothesis", rule_id, cluster_id, affected, automations),
        rule_id=rule_id,
        title=title,
        confidence=confidence,
        severity=severity,
        causal_status=causal_status,
        explanation=explanation[:600],
        supporting_evidence_reference_ids=refs,
        contradicting_evidence_reference_ids=contradict_refs,
        missing_evidence=tuple(missing)[:10],
        coverage_limitations=tuple(limitations)[:10],
        affected_entity_ids=affected,
        automation_ids=automations,
        first_observed=times[0] if times else None,
        last_observed=times[-1] if times else None,
        manual_review_required=causal_status != "confirmed_cause",
    )


def _clusters(events: list[IncidentEvent], window_minutes: int):
    dated = [item for item in events if parse_timestamp(item.timestamp) is not None]
    undated = [item for item in events if parse_timestamp(item.timestamp) is None]
    clusters: list[list[IncidentEvent]] = []
    window = timedelta(minutes=window_minutes)
    for item in dated:
        current = parse_timestamp(item.timestamp)
        compatible = None
        for values in reversed(clusters):
            previous = parse_timestamp(values[-1].timestamp)
            if previous is not None and current - previous <= window and _related(item, values):
                compatible = values
                break
        if compatible is None:
            clusters.append([item])
        else:
            compatible.append(item)
    for item in undated:
        clusters.append([item])
    return [(stable_id("cluster", index, *(item.event_id for item in values)), values) for index, values in enumerate(clusters)]


def _related(item: IncidentEvent, values: list[IncidentEvent]) -> bool:
    for other in values:
        if item.run_id and item.run_id == other.run_id:
            return True
        if item.automation_id and item.automation_id == other.automation_id:
            return True
        if item.entity_id and item.entity_id == other.entity_id:
            return True
        if item.integration_domain and item.integration_domain == other.integration_domain:
            return True
        if item.cluster_key and item.cluster_key == other.cluster_key:
            return True
    return False


def _shared_entities(first, second):
    return tuple(sorted({item.entity_id for item in first if item.entity_id} & {item.entity_id for item in second if item.entity_id}))


def _lower_confidence(value: str) -> str:
    order = ["confirmed", "high", "medium", "low", "insufficient"]
    return order[min(order.index(value) + 1, len(order) - 1)]


def _cluster_from_id(value: str) -> str:
    return value
