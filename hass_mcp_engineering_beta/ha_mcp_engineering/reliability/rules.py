"""Small deterministic reliability rule engine with evidence-backed findings."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import json
import re
from typing import Any

from .models import (
    ReliabilityEvidenceBundle,
    ReliabilityEvidenceReference,
    ReliabilityFinding,
    ReliabilityRootCauseGroup,
    stable_id,
)
from .timestamps import normalize_interval, normalize_timestamp, observation_window


RULES: tuple[dict[str, Any], ...] = (
    {"rule_id": "automation_disabled", "required_evidence": ("automation_state",), "severity": "info"},
    {"rule_id": "missing_referenced_entity", "required_evidence": ("entity_state",), "severity": "high"},
    {"rule_id": "unavailable_referenced_entity", "required_evidence": ("entity_state",), "severity": "medium"},
    {"rule_id": "unknown_referenced_entity", "required_evidence": ("entity_state",), "severity": "low"},
    {"rule_id": "disabled_referenced_entity", "required_evidence": ("entity_registry",), "severity": "medium"},
    {"rule_id": "repeated_trace_failure", "required_evidence": ("automation_traces",), "severity": "high"},
    {"rule_id": "repeated_condition_stop", "required_evidence": ("automation_traces",), "severity": "info"},
    {"rule_id": "repeated_action_error", "required_evidence": ("automation_traces",), "severity": "high"},
    {"rule_id": "correlated_system_log_error", "required_evidence": ("system_log",), "severity": "medium"},
    {"rule_id": "mode_concurrency_conflict", "required_evidence": ("automation_traces",), "severity": "medium"},
    {"rule_id": "unresolved_dynamic_reference", "required_evidence": ("automation_config",), "severity": "low"},
    {"rule_id": "blueprint_evidence_unavailable", "required_evidence": ("blueprint_source",), "severity": "low"},
    {"rule_id": "no_recent_execution_evidence", "required_evidence": ("automation_traces",), "severity": "info"},
    {"rule_id": "trace_evidence_unavailable", "required_evidence": ("automation_traces",), "severity": "low"},
)

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_CONCURRENCY = re.compile(r"max_exceeded|already running|maximum (?:number of )?runs|queue(?:d)? full", re.I)


def evaluate_rules(bundle: ReliabilityEvidenceBundle) -> list[ReliabilityFinding]:
    findings: list[ReliabilityFinding] = []
    findings.extend(_automation_status(bundle))
    findings.extend(_entity_rules(bundle))
    findings.extend(_trace_rules(bundle))
    findings.extend(_system_log_rules(bundle))
    findings.extend(_coverage_rules(bundle))
    values = sorted(
        {item.finding_id: item for item in findings}.values(),
        key=lambda item: (_SEVERITY_ORDER[item.severity], item.rule_id, item.finding_id),
    )
    groups = {group.root_cause_group_id: group for group in build_root_cause_groups(values)}
    by_group: dict[str, list[ReliabilityFinding]] = defaultdict(list)
    for item in values:
        if item.root_cause_group_id:
            by_group[item.root_cause_group_id].append(item)
    output: list[ReliabilityFinding] = []
    for item in values:
        members = by_group.get(item.root_cause_group_id or "", [])
        primary = groups.get(item.root_cause_group_id or "")
        output.append(replace(
            item,
            related_finding_ids=tuple(member.finding_id for member in members if member.finding_id != item.finding_id),
            root_cause_relationship="primary" if primary and primary.primary_finding_id == item.finding_id else "supporting",
        ))
    return output


def build_root_cause_groups(findings: list[ReliabilityFinding]) -> list[ReliabilityRootCauseGroup]:
    grouped: dict[str, list[ReliabilityFinding]] = defaultdict(list)
    for item in findings:
        grouped[item.root_cause_group_id or stable_id("root", item.finding_id)].append(item)
    values: list[ReliabilityRootCauseGroup] = []
    for group_id, members in grouped.items():
        ordered = sorted(members, key=lambda item: (_SEVERITY_ORDER[item.severity], item.rule_id, item.finding_id))
        first, last = observation_window(
            timestamp for item in members for timestamp in (item.first_observed, item.last_observed)
        )
        occurrences = {value for item in members for value in item.occurrence_ids if value}
        refs = tuple(dict.fromkeys(value for item in ordered for value in item.evidence_references))[:20]
        values.append(ReliabilityRootCauseGroup(
            root_cause_group_id=group_id,
            primary_finding_id=ordered[0].finding_id,
            member_finding_ids=tuple(item.finding_id for item in ordered),
            unique_occurrence_count=len(occurrences) or max((item.occurrence_count or 1 for item in members), default=1),
            highest_severity=ordered[0].severity,
            first_observed=first,
            last_observed=last,
            affected_step=next((item.trace_step for item in ordered if item.trace_step), None),
            affected_dependency=next((item.affected_dependency for item in ordered if item.affected_dependency), None),
            evidence_references=refs,
        ))
    return sorted(values, key=lambda item: (_SEVERITY_ORDER[item.highest_severity], item.root_cause_group_id))


def _reference(bundle, *, source_type, source_id, summary, timestamp=None, config_path=None, run_id=None,
               trace_step=None, interval=None, correlation_basis=(), confidence=None):
    timestamp = normalize_timestamp(timestamp)
    interval = normalize_interval(interval) if interval is not None else None
    reference_id = stable_id("ev", source_type, source_id, timestamp, config_path, run_id, trace_step, summary)
    bundle.evidence.setdefault(reference_id, ReliabilityEvidenceReference(
        reference_id=reference_id, source_type=source_type, source_id=str(source_id)[:128],
        summary=str(summary)[:256], timestamp=timestamp, configuration_path=config_path,
        trace_run_id=run_id, trace_step=trace_step, interval=interval,
        correlation_basis=tuple(correlation_basis), confidence=confidence,
    ))
    return reference_id


def _finding(bundle, rule_id, title, severity, confidence, status, explanation, *, key="", refs=(),
             root_key=None, occurrence_ids=(), **kwargs):
    finding_id = stable_id("finding", rule_id, bundle.automation_id, key, kwargs.get("configuration_path"), kwargs.get("trace_step"))
    return ReliabilityFinding(
        finding_id=finding_id, rule_id=rule_id, title=title, severity=severity, confidence=confidence,
        status=status, explanation=explanation[:600], automation_id=bundle.automation_id,
        automation_entity_id=bundle.automation.get("entity_id"), evidence_references=tuple(dict.fromkeys(refs))[:20],
        root_cause_group_id=stable_id("root", bundle.automation_id, root_key or finding_id),
        occurrence_ids=tuple(dict.fromkeys(str(value) for value in occurrence_ids if value)), **kwargs,
    )


def _automation_status(bundle):
    if str(bundle.automation.get("state", "")).lower() != "off" and not bundle.automation.get("disabled"):
        return []
    ref = _reference(bundle, source_type="automation_state", source_id=bundle.automation.get("entity_id") or bundle.automation_id, summary="Automation state is disabled or off.")
    return [_finding(bundle, "automation_disabled", "Automation is disabled", "info", "exact", "confirmed",
        "The automation is currently disabled. This is operational status and may be intentional.", refs=(ref,),
        operational_impact="The automation cannot execute while disabled.", recommended_next_investigation="Confirm whether the disabled state is intentional.", governed_change_required=True)]


def _entity_rules(bundle):
    findings = []
    mapping = {
        "missing": ("missing_referenced_entity", "Referenced entity is missing", "high", "confirmed", "The exact referenced entity does not exist."),
        "unavailable": ("unavailable_referenced_entity", "Referenced entity is unavailable", "medium", "probable", "The exact referenced entity currently reports unavailable."),
        "unknown": ("unknown_referenced_entity", "Referenced entity state is unknown", "low", "possible", "The exact referenced entity currently reports unknown."),
        "disabled": ("disabled_referenced_entity", "Referenced entity is registry-disabled", "medium", "confirmed", "The exact referenced entity is disabled in the entity registry."),
    }
    for item in bundle.references:
        statuses = [item.get("status")] + (["disabled"] if item.get("registry_disabled") and item.get("status") != "disabled" else [])
        for status in statuses:
            if status not in mapping:
                continue
            rule_id, title, severity, finding_status, explanation = mapping[status]
            entity_id, config_path = item.get("entity_id"), item.get("config_path")
            ref = _reference(bundle, source_type="entity_reference", source_id=entity_id, summary=f"{entity_id} is {status}.", config_path=config_path)
            findings.append(_finding(bundle, rule_id, title, severity, "exact", finding_status, explanation,
                key=entity_id, refs=(ref,), configuration_path=config_path, affected_dependency=entity_id,
                operational_impact="The referenced step may not evaluate or execute as configured.",
                recommended_next_investigation=f"Inspect {entity_id} and the cited configuration path.", governed_change_required=status in {"missing", "disabled"}))
    return findings


def _trace_rules(bundle):
    if not bundle.traces:
        coverage = next(
            (item for item in bundle.coverage if item.source_type == "automation_traces"),
            None,
        )
        trustworthy_empty = bool(
            coverage
            and (
                coverage.trustworthy_empty
                or (
                    coverage.completeness == "complete"
                    and coverage.collection_state is None
                )
            )
        )
        if not trustworthy_empty:
            ref = _reference(
                bundle,
                source_type="trace_coverage",
                source_id=bundle.automation_id,
                summary="Trace evidence could not be evaluated completely.",
            )
            return [_finding(
                bundle, "trace_evidence_unavailable", "Trace evidence is incomplete",
                "low", "exact", "evidence_gap",
                "Trace retrieval or timestamp normalization was incomplete; this is a source limitation, not evidence that the automation did not execute.",
                refs=(ref,),
                operational_impact="Runtime reliability could not be assessed completely from traces.",
                recommended_next_investigation="Inspect bounded trace source coverage and retry when the source is healthy.",
            )]
        ref = _reference(bundle, source_type="trace_coverage", source_id=bundle.automation_id, summary="No trace runs were available in the requested lookback.")
        return [_finding(bundle, "no_recent_execution_evidence", "No recent execution evidence", "info", "exact", "evidence_gap",
            "No recent traces were available. This is an evidence gap, not proof of unreliable behavior.", refs=(ref,),
            operational_impact="Runtime reliability could not be assessed from traces.", recommended_next_investigation="Re-run the analysis after the automation executes naturally.")]
    findings = []
    errors, conditions, actions = defaultdict(list), defaultdict(list), defaultdict(list)
    concurrency = []
    for trace in bundle.traces:
        step = str(trace.get("failure_step") or trace.get("last_step") or "unknown")[:160]
        error = _normalize_error(trace.get("error"))
        dependency = str(trace.get("affected_dependency") or "")[:128]
        key = (step, error, dependency)
        if error:
            errors[key].append(trace)
            if trace.get("action_error") or "action" in step.lower() or "service" in step.lower():
                actions[key].append(trace)
            if _CONCURRENCY.search(error):
                concurrency.append(trace)
        if trace.get("condition_stop_step"):
            conditions[str(trace["condition_stop_step"])[:160]].append(trace)
    for rule_id, title, groups in (("repeated_trace_failure", "Repeated trace failure", errors), ("repeated_action_error", "Repeated action or service error", actions)):
        for (step, error, dependency), traces in groups.items():
            if len(traces) < 2:
                continue
            refs = tuple(_trace_ref(bundle, item, f"{title} at {step}.", step) for item in traces[:10])
            first, last = observation_window(traces)
            occurrence_ids = tuple(str(item.get("run_id") or item.get("timestamp") or "unknown") for item in traces)
            findings.append(_finding(bundle, rule_id, title, "high", "high", "probable",
                f"{len(traces)} recent traces contain the same sanitized failure signature at the same step.",
                key=f"{step}:{error}:{dependency}", root_key=f"trace_failure:{step}:{error}:{dependency}", refs=refs,
                trace_step=step, occurrence_count=len(set(occurrence_ids)), occurrence_ids=occurrence_ids,
                first_observed=first, last_observed=last, affected_dependency=dependency or None,
                operational_impact="Repeated runs are not completing the same execution path.",
                recommended_next_investigation="Inspect the cited trace step and referenced dependency.", governed_change_required=True))
    for step, traces in conditions.items():
        if len(traces) < 2:
            continue
        first, last = observation_window(traces)
        occurrences = tuple(str(item.get("run_id") or item.get("timestamp") or "unknown") for item in traces)
        refs = tuple(_trace_ref(bundle, item, f"Trace stopped at condition {step}.", step) for item in traces[:10])
        findings.append(_finding(bundle, "repeated_condition_stop", "Repeated condition stop", "info", "high", "possible",
            f"{len(traces)} traces stopped at the same condition. The condition may be working as designed.", key=step, refs=refs,
            trace_step=step, occurrence_count=len(set(occurrences)), occurrence_ids=occurrences, first_observed=first, last_observed=last,
            operational_impact="The action path following this condition was not reached in the observed runs.",
            recommended_next_investigation="Compare the condition inputs with the intended automation behavior."))
    if concurrency:
        first, last = observation_window(concurrency)
        occurrences = tuple(str(item.get("run_id") or item.get("timestamp") or "unknown") for item in concurrency)
        refs = tuple(_trace_ref(bundle, item, "Trace contains an explicit concurrency rejection.", item.get("last_step")) for item in concurrency[:10])
        findings.append(_finding(bundle, "mode_concurrency_conflict", "Explicit mode or concurrency rejection", "medium", "exact", "confirmed",
            f"{len(concurrency)} trace(s) contain explicit max-exceeded or overlapping-run evidence.", key="concurrency", refs=refs,
            occurrence_count=len(set(occurrences)), occurrence_ids=occurrences, first_observed=first, last_observed=last,
            operational_impact="One or more runs were rejected or constrained by automation mode.",
            recommended_next_investigation="Compare observed overlap with the configured mode and max concurrency.", governed_change_required=True))
    return findings


def _system_log_rules(bundle):
    if not bundle.system_log_entries:
        return []
    refs, occurrences = [], []
    for item in bundle.system_log_entries[:20]:
        occurrences.append(str(item.get("identity") or item.get("timestamp") or "unknown"))
        refs.append(_reference(bundle, source_type="system_log", source_id=item.get("identity", "system_log"),
            summary=item.get("summary", "Sanitized System Log entry met explicit correlation rules."), timestamp=item.get("timestamp"),
            correlation_basis=item.get("correlation_basis", ()), confidence=item.get("confidence")))
    first, last = observation_window(bundle.system_log_entries)
    return [_finding(bundle, "correlated_system_log_error", "Correlated Home Assistant System Log error", "medium", "high", "probable",
        f"{len(bundle.system_log_entries)} sanitized System Log entry or entries met documented explicit correlation rules.",
        key="system_log", refs=tuple(refs), occurrence_count=len(set(occurrences)), occurrence_ids=tuple(occurrences),
        first_observed=first, last_observed=last, operational_impact="Home Assistant recorded an error with explicit evidence linking it to the analyzed automation.",
        recommended_next_investigation="Inspect the bounded sanitized log evidence alongside the matching trace.")]


def _coverage_rules(bundle):
    findings = []
    for item in bundle.dynamic_references[:20]:
        ref = _reference(bundle, source_type="automation_config", source_id=bundle.automation_id, summary="Dynamic reference could not be resolved statically.", config_path=item.get("config_path"))
        findings.append(_finding(bundle, "unresolved_dynamic_reference", "Unresolved dynamic reference", "low", "exact", "evidence_gap",
            "A template or dynamic reference could not be resolved statically; coverage is incomplete.", key=item.get("evidence_id", "dynamic"), refs=(ref,),
            configuration_path=item.get("config_path"), operational_impact="The referenced runtime target could not be verified.",
            recommended_next_investigation="Inspect the cited template with bounded live inputs."))
    if bundle.blueprint_path and bundle.blueprint is None:
        ref = _reference(bundle, source_type="blueprint_source", source_id=bundle.blueprint_path, summary="Blueprint source was unavailable for analysis.")
        findings.append(_finding(bundle, "blueprint_evidence_unavailable", "Blueprint evidence is unavailable", "low", "exact", "evidence_gap",
            "The automation uses a blueprint, but its source could not be retrieved; behavioral analysis is partial.", key=bundle.blueprint_path,
            refs=(ref,), configuration_path="$.use_blueprint.path", operational_impact="Blueprint-defined behavior was not fully assessed.",
            recommended_next_investigation="Verify the blueprint mount and source path."))
    return findings


def _trace_ref(bundle, trace, summary, step):
    interval = {"start": trace.get("started_at") or trace.get("timestamp"), "finish": trace.get("finished_at")}
    return _reference(bundle, source_type="automation_trace", source_id=trace.get("run_id", "unknown"), summary=summary,
        timestamp=trace.get("started_at") or trace.get("timestamp"), interval=interval,
        run_id=trace.get("run_id"), trace_step=step)


def _normalize_error(value: Any) -> str:
    if value in (None, "", False):
        return ""
    text = json.dumps(value, sort_keys=True, default=str) if not isinstance(value, str) else value
    text = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", text.lower())
    return re.sub(r"\s+", " ", text).strip()[:300]
