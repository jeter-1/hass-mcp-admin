"""Small deterministic reliability rule engine with evidence-backed findings."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
import re
from typing import Any

from .models import (
    ReliabilityEvidenceBundle,
    ReliabilityEvidenceReference,
    ReliabilityFinding,
    stable_id,
)


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
    return sorted(
        {item.finding_id: item for item in findings}.values(),
        key=lambda item: (_SEVERITY_ORDER[item.severity], item.rule_id, item.finding_id),
    )


def _reference(
    bundle: ReliabilityEvidenceBundle,
    *,
    source_type: str,
    source_id: str,
    summary: str,
    timestamp: str | None = None,
    config_path: str | None = None,
    run_id: str | None = None,
    trace_step: str | None = None,
) -> str:
    reference_id = stable_id("ev", source_type, source_id, timestamp, config_path, run_id, trace_step, summary)
    bundle.evidence.setdefault(
        reference_id,
        ReliabilityEvidenceReference(
            reference_id=reference_id,
            source_type=source_type,
            source_id=str(source_id)[:128],
            summary=str(summary)[:256],
            timestamp=timestamp,
            configuration_path=config_path,
            trace_run_id=run_id,
            trace_step=trace_step,
        ),
    )
    return reference_id


def _finding(bundle, rule_id, title, severity, confidence, status, explanation, *, key="", refs=(), **kwargs):
    return ReliabilityFinding(
        finding_id=stable_id("finding", rule_id, bundle.automation_id, key, kwargs.get("configuration_path"), kwargs.get("trace_step")),
        rule_id=rule_id,
        title=title,
        severity=severity,
        confidence=confidence,
        status=status,
        explanation=explanation[:600],
        automation_id=bundle.automation_id,
        automation_entity_id=bundle.automation.get("entity_id"),
        evidence_references=tuple(dict.fromkeys(refs))[:20],
        **kwargs,
    )


def _automation_status(bundle):
    if str(bundle.automation.get("state", "")).lower() != "off" and not bundle.automation.get("disabled"):
        return []
    ref = _reference(
        bundle,
        source_type="automation_state",
        source_id=bundle.automation.get("entity_id") or bundle.automation_id,
        summary="Automation state is disabled or off.",
    )
    return [_finding(
        bundle, "automation_disabled", "Automation is disabled", "info", "exact", "confirmed",
        "The automation is currently disabled. This is operational status and may be intentional.",
        refs=(ref,), operational_impact="The automation cannot execute while disabled.",
        recommended_next_investigation="Confirm whether the disabled state is intentional.",
        governed_change_required=True,
    )]


def _entity_rules(bundle):
    findings = []
    mapping = {
        "missing": ("missing_referenced_entity", "Referenced entity is missing", "high", "confirmed", "The exact referenced entity does not exist."),
        "unavailable": ("unavailable_referenced_entity", "Referenced entity is unavailable", "medium", "probable", "The exact referenced entity currently reports unavailable."),
        "unknown": ("unknown_referenced_entity", "Referenced entity state is unknown", "low", "possible", "The exact referenced entity currently reports unknown."),
        "disabled": ("disabled_referenced_entity", "Referenced entity is registry-disabled", "medium", "confirmed", "The exact referenced entity is disabled in the entity registry."),
    }
    for item in bundle.references:
        statuses = [item.get("status")]
        if item.get("registry_disabled") and "disabled" not in statuses:
            statuses.append("disabled")
        for status in statuses:
            if status not in mapping:
                continue
            rule_id, title, severity, finding_status, explanation = mapping[status]
            config_path = item.get("config_path")
            entity_id = item.get("entity_id")
            ref = _reference(
                bundle, source_type="entity_reference", source_id=entity_id,
                summary=f"{entity_id} is {status}.", config_path=config_path,
            )
            findings.append(_finding(
                bundle, rule_id, title, severity, "exact", finding_status,
                explanation, key=entity_id, refs=(ref,), configuration_path=config_path,
                operational_impact="The referenced step may not evaluate or execute as configured.",
                recommended_next_investigation=f"Inspect {entity_id} and the cited configuration path.",
                governed_change_required=status in {"missing", "disabled"},
            ))
    return findings


def _trace_rules(bundle):
    if not bundle.traces:
        ref = _reference(bundle, source_type="trace_coverage", source_id=bundle.automation_id, summary="No trace runs were available in the requested lookback.")
        return [_finding(
            bundle, "no_recent_execution_evidence", "No recent execution evidence", "info", "exact", "evidence_gap",
            "No recent traces were available. This is an evidence gap, not proof of unreliable behavior.", refs=(ref,),
            operational_impact="Runtime reliability could not be assessed from traces.",
            recommended_next_investigation="Re-run the analysis after the automation executes naturally.",
        )]

    findings = []
    errors: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    conditions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    action_errors: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    concurrency: list[dict[str, Any]] = []
    for trace in bundle.traces:
        step = str(trace.get("failure_step") or trace.get("last_step") or "unknown")[:160]
        error = _normalize_error(trace.get("error"))
        if error:
            errors[(step, error)].append(trace)
            if "action" in step.lower() or "service" in step.lower() or trace.get("action_error"):
                action_errors[(step, error)].append(trace)
            if _CONCURRENCY.search(error):
                concurrency.append(trace)
        condition_step = trace.get("condition_stop_step")
        if condition_step:
            conditions[str(condition_step)[:160]].append(trace)

    for (step, error), traces in errors.items():
        if len(traces) < 2:
            continue
        refs = tuple(_trace_ref(bundle, item, f"Repeated trace failure at {step}.", step) for item in traces[:10])
        findings.append(_finding(
            bundle, "repeated_trace_failure", "Repeated trace failure", "high", "high", "probable",
            f"{len(traces)} recent traces failed at the same step with the same normalized error.",
            key=f"{step}:{error}", refs=refs, trace_step=step, occurrence_count=len(traces),
            first_observed=_timestamp(traces[-1]), last_observed=_timestamp(traces[0]),
            operational_impact="Repeated runs are not completing the same execution path.",
            recommended_next_investigation="Inspect the cited trace step and its referenced dependencies.",
            governed_change_required=True,
        ))
    for step, traces in conditions.items():
        if len(traces) < 2:
            continue
        refs = tuple(_trace_ref(bundle, item, f"Trace stopped at condition {step}.", step) for item in traces[:10])
        findings.append(_finding(
            bundle, "repeated_condition_stop", "Repeated condition stop", "info", "high", "possible",
            f"{len(traces)} traces stopped at the same condition. The condition may be working as designed.",
            key=step, refs=refs, trace_step=step, occurrence_count=len(traces),
            first_observed=_timestamp(traces[-1]), last_observed=_timestamp(traces[0]),
            operational_impact="The action path following this condition was not reached in the observed runs.",
            recommended_next_investigation="Compare the condition inputs with the intended automation behavior.",
        ))
    for (step, error), traces in action_errors.items():
        if len(traces) < 2:
            continue
        refs = tuple(_trace_ref(bundle, item, f"Repeated action error at {step}.", step) for item in traces[:10])
        findings.append(_finding(
            bundle, "repeated_action_error", "Repeated action or service error", "high", "high", "probable",
            f"{len(traces)} traces contain the same explicit action error.", key=f"{step}:{error}", refs=refs,
            trace_step=step, occurrence_count=len(traces), first_observed=_timestamp(traces[-1]),
            last_observed=_timestamp(traces[0]), operational_impact="The affected action repeatedly failed.",
            recommended_next_investigation="Validate the cited service, target, and action parameters.", governed_change_required=True,
        ))
    if concurrency:
        refs = tuple(_trace_ref(bundle, item, "Trace contains an explicit concurrency rejection.", item.get("last_step")) for item in concurrency[:10])
        findings.append(_finding(
            bundle, "mode_concurrency_conflict", "Explicit mode or concurrency rejection", "medium", "exact", "confirmed",
            f"{len(concurrency)} trace(s) contain explicit max-exceeded or overlapping-run evidence.", key="concurrency",
            refs=refs, occurrence_count=len(concurrency), first_observed=_timestamp(concurrency[-1]),
            last_observed=_timestamp(concurrency[0]), operational_impact="One or more runs were rejected or constrained by automation mode.",
            recommended_next_investigation="Compare observed overlap with the configured mode and max concurrency.", governed_change_required=True,
        ))
    return findings


def _system_log_rules(bundle):
    if not bundle.system_log_entries:
        return []
    refs = []
    timestamps = []
    for item in bundle.system_log_entries[:20]:
        timestamp = item.get("timestamp")
        timestamps.append(timestamp)
        refs.append(_reference(
            bundle, source_type="system_log", source_id=item.get("identity", "system_log"),
            summary=item.get("summary", "Sanitized System Log entry correlated to this automation."), timestamp=timestamp,
        ))
    return [_finding(
        bundle, "correlated_system_log_error", "Correlated Home Assistant System Log error", "medium", "high", "probable",
        f"{len(bundle.system_log_entries)} sanitized System Log entry or entries explicitly reference this automation or a cited failed dependency.",
        key="system_log", refs=tuple(refs), occurrence_count=len(bundle.system_log_entries),
        first_observed=next((item for item in reversed(timestamps) if item), None),
        last_observed=next((item for item in timestamps if item), None),
        operational_impact="Home Assistant recorded an error correlated with the analyzed automation.",
        recommended_next_investigation="Inspect the bounded sanitized log evidence alongside the matching trace.",
    )]


def _coverage_rules(bundle):
    findings = []
    for item in bundle.dynamic_references[:20]:
        ref = _reference(
            bundle, source_type="automation_config", source_id=bundle.automation_id,
            summary="Dynamic reference could not be resolved statically.", config_path=item.get("config_path"),
        )
        findings.append(_finding(
            bundle, "unresolved_dynamic_reference", "Unresolved dynamic reference", "low", "exact", "evidence_gap",
            "A template or dynamic reference could not be resolved statically; coverage is incomplete.",
            key=item.get("evidence_id", "dynamic"), refs=(ref,), configuration_path=item.get("config_path"),
            operational_impact="The referenced runtime target could not be verified.",
            recommended_next_investigation="Inspect the cited template with bounded live inputs.",
        ))
    if bundle.blueprint_path and bundle.blueprint is None:
        ref = _reference(
            bundle, source_type="blueprint_source", source_id=bundle.blueprint_path,
            summary="Blueprint source was unavailable for analysis.",
        )
        findings.append(_finding(
            bundle, "blueprint_evidence_unavailable", "Blueprint evidence is unavailable", "low", "exact", "evidence_gap",
            "The automation uses a blueprint, but its source could not be retrieved; behavioral analysis is partial.",
            key=bundle.blueprint_path, refs=(ref,), configuration_path="$.use_blueprint.path",
            operational_impact="Blueprint-defined triggers, conditions, and actions were not fully assessed.",
            recommended_next_investigation="Verify the blueprint mount and source path.",
        ))
    return findings


def _trace_ref(bundle, trace, summary, step):
    return _reference(
        bundle, source_type="automation_trace", source_id=trace.get("run_id", "unknown"), summary=summary,
        timestamp=_timestamp(trace), run_id=trace.get("run_id"), trace_step=step,
    )


def _timestamp(trace):
    return trace.get("timestamp") or trace.get("last_action")


def _normalize_error(value: Any) -> str:
    if value in (None, "", False):
        return ""
    text = json.dumps(value, sort_keys=True, default=str) if not isinstance(value, str) else value
    text = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", text.lower())
    text = re.sub(r"\s+", " ", text).strip()
    return text[:300]

