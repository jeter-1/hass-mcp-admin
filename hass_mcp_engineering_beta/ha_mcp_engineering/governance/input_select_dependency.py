"""Domain-parameterized dependency reader for input_select (HAMCP-145, OD-11).

This reader reuses the reviewed v13 obligation projection unchanged, with
two input_select corrections, and adds per-option trigger disclosure:

* A ``call_service`` event-trigger obligation that the extractor proved
  disjoint is proven disjoint only for ``input_boolean``.  The extractor
  does not retain enough to re-prove it for ``input_select``, so every such
  terminal is re-projected as target-capable opaque evidence with
  conservative locks.  Opacity elevates risk; it never refuses on
  consequence alone (OD-4).
* The model is ``helper-dependency-risk-input-select-v2``.  Its evidence
  fingerprint binds the target-scoped material for the re-projected snapshot
  plus the literal state-trigger records for the exact target, so a trigger
  edit stales an approval.  The ``input_boolean`` v13 binding is unchanged.

HAMCP-274 (v2) decides relevance by evidence about this target:

* A ``call_service`` filter proven disjoint for ``input_boolean`` stays a
  proven exclusion when neither ``input_select`` nor ``select_option`` is
  among its literal values: whichever complete selector proved the
  ``input_boolean`` exclusion then proves this one too.
* A literal ``label_entities`` selector left incomplete only by malformed
  registry records naming other entities is decided from the target's own
  record.
* An attributable coverage gap makes only its own automation relevant, a
  consumer set beyond the disclosure bound takes the coarse
  ``reload:automation`` fence instead of refusing, and missing evidence
  never carries a positive consequence label (``helper_dependency``).

v1 remains a compatible, readable model: a pending v1 plan is replanned
rather than applied, and recovery may still reconstruct its lock graph.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..dependency.models import DependencyObligation

from ..dependency.state_trigger_values import (
    DISABLED_EFFECT,
    DISABLED_EFFECT_MEANING,
    StateTriggerValues,
    option_effect,
)
from .helper_dependency import (
    HelperDependencyRiskService,
    _failed_binding,
    _safe_automation_identity,
    build_helper_dependency_risk_binding,
    target_scoped_label_disposition,
)
from .normalize import stable_hash

INPUT_SELECT_DEPENDENCY_MODEL_V1 = "helper-dependency-risk-input-select-v1"
INPUT_SELECT_DEPENDENCY_MODEL = "helper-dependency-risk-input-select-v2"
INPUT_SELECT_DEPENDENCY_EXECUTION_MODELS = frozenset({INPUT_SELECT_DEPENDENCY_MODEL})
# Readable and recoverable, never fresh execution authority.
INPUT_SELECT_DEPENDENCY_COMPATIBLE_MODELS = frozenset(
    {INPUT_SELECT_DEPENDENCY_MODEL_V1, INPUT_SELECT_DEPENDENCY_MODEL}
)
REPROJECTED_REASON = "call_service_filter_unproven_for_input_select"
LABEL_TARGET_INCLUDED_REASON = "label_membership_target_included"
LABEL_TARGET_EXCLUDED_REASON = "label_membership_target_excluded"
MAX_OPTION_TRIGGER_RECORDS = 64
_INPUT_SELECT_EVENT_SELECTORS = frozenset(
    {"domain:input_select", "service:select_option"}
)


def _call_service_exclusion_holds(item: DependencyObligation) -> bool:
    """Return whether an input_boolean exclusion also excludes input_select.

    The extractor proved the filter disjoint from ``input_boolean`` either by
    a complete domain set without ``input_boolean`` or by a complete service
    set without ``turn_on``/``turn_off``.  Literal selectors are retained in
    full or the obligation is reclassified as a coverage failure, so when
    neither ``input_select`` nor ``select_option`` is a literal value, the
    complete set that proved the first exclusion proves this one.
    """

    return bool(
        not item.coverage_failure_authority
        and not item.evidence_bounded
        and not set(item.literal_selectors) & _INPUT_SELECT_EVENT_SELECTORS
    )


def _label_target_projection(
    item: DependencyObligation, snapshot: Any, entity_id: str
) -> tuple[DependencyObligation | None, list[list[str]]]:
    """Decide an opaque literal ``label_entities`` terminal for one target."""

    if item.coverage_failure_authority or item.outcome != "bounded_semantic_opaque":
        return None, []
    context = set(item.context_provenance)
    producers = {
        value.removeprefix("entity_set_producer:")
        for value in context
        if value.startswith("entity_set_producer:")
    }
    transforms = {
        value
        for value in context
        if value.startswith("entity_selector_transform:")
    }
    selectors = tuple(sorted(set(item.literal_selectors)))
    if (
        producers != {"label_entities"}
        # expand() could reach the target through a labelled group.
        or transforms
        or "entity_selector_provenance:complete" not in context
        or "entity_selector_provenance:incomplete" in context
        or "entity_non_selector_evidence:complete" not in context
        or "entity_non_selector_evidence:incomplete" in context
        or not selectors
    ):
        return None, []
    authority = getattr(snapshot, "label_selector_authority", {}) or {}
    dispositions = []
    for selector in selectors:
        evidence = authority.get(selector)
        if evidence is None:
            return None, []
        dispositions.append(
            [
                evidence.selector_fingerprint,
                target_scoped_label_disposition(
                    snapshot, selector, evidence, entity_id=entity_id
                ),
            ]
        )
    outcomes = {disposition for _, disposition in dispositions}
    if "unresolved" in outcomes:
        return None, dispositions
    if "included" in outcomes or entity_id in item.exact_entity_ids:
        return (
            replace(
                item,
                outcome="exact_dependency",
                reason_code=LABEL_TARGET_INCLUDED_REASON,
                exact_entity_ids=tuple(
                    sorted(
                        {*item.exact_entity_ids, entity_id},
                        key=lambda value: value.encode("utf-8"),
                    )
                ),
                lock_projection="exact",
            ),
            dispositions,
        )
    return (
        replace(
            item,
            outcome="proven_target_exclusion",
            reason_code=LABEL_TARGET_EXCLUDED_REASON,
            target_selector_scope="closed_finite_candidates",
            lock_projection="none",
        ),
        dispositions,
    )


def _reproject(
    snapshot: Any, entity_id: str | None = None
) -> tuple[Any, int, dict[str, Any]]:
    obligations = tuple(getattr(snapshot, "obligations", ()) or ())
    changed = 0
    retained = 0
    label_dispositions: dict[str, str] = {}
    label_changed = 0
    projected = []
    for item in obligations:
        if (
            item.obligation_kind == "call_service_event_trigger"
            and item.outcome == "proven_target_exclusion"
        ):
            if entity_id is not None and _call_service_exclusion_holds(item):
                retained += 1
            else:
                changed += 1
                item = replace(
                    item,
                    outcome="bounded_semantic_opaque",
                    target_selector_scope="target_capable",
                    lock_projection="conservative",
                    reason_code=REPROJECTED_REASON,
                )
        elif entity_id is not None and item.source_type in {"automation", "blueprint"}:
            decided, dispositions = _label_target_projection(item, snapshot, entity_id)
            for fingerprint, disposition in dispositions:
                label_dispositions[fingerprint] = disposition
            if decided is not None:
                label_changed += 1
                item = decided
        projected.append(item)
    summary = {
        "call_service_exclusions_retained": retained,
        "label_obligations_target_decided": label_changed,
        "label_target_dispositions": [
            [key, label_dispositions[key]] for key in sorted(label_dispositions)
        ],
    }
    if not changed and not label_changed:
        return snapshot, 0, summary
    return replace(snapshot, obligations=tuple(projected)), changed, summary


def _trigger_records(snapshot: Any, entity_id: str) -> tuple[list[dict[str, Any]], bool]:
    records = []
    for item in getattr(snapshot, "state_trigger_values", ()) or ():
        if entity_id not in item.entity_ids:
            continue
        records.append({
            "automation_id": _safe_automation_identity(item.source_id, item.source_entity_id),
            "configuration_path": item.config_path,
            "to": list(item.to) if item.to is not None else None,
            "from": list(item.from_) if item.from_ is not None else None,
            "not_to": list(item.not_to),
            "not_from": list(item.not_from),
            "complete": item.complete,
            "enablement": item.enablement,
            "reason_codes": list(item.reason_codes),
        })
    records.sort(key=lambda value: (value["automation_id"], value["configuration_path"]))
    truncated = len(records) > MAX_OPTION_TRIGGER_RECORDS
    return records[:MAX_OPTION_TRIGGER_RECORDS], truncated


def _wrap(base: dict[str, Any], *, records: list[dict[str, Any]], truncated: bool,
          reprojected: int, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = dict(summary or {
        "call_service_exclusions_retained": 0,
        "label_obligations_target_decided": 0,
        "label_target_dispositions": [],
    })
    material = {
        "model": INPUT_SELECT_DEPENDENCY_MODEL,
        "base_model": base.get("model"),
        "base_evidence_fingerprint": base.get("evidence_fingerprint"),
        "option_trigger_records": records,
        "option_trigger_records_truncated": truncated,
        "reprojected_call_service_obligations": reprojected,
        **summary,
    }
    binding = {
        **base,
        "model": INPUT_SELECT_DEPENDENCY_MODEL,
        "base_model": base.get("model"),
        "base_evidence_fingerprint": base.get("evidence_fingerprint"),
        "option_trigger_records": records,
        "option_trigger_records_truncated": truncated,
        "reprojected_call_service_obligations": reprojected,
        **summary,
    }
    if reprojected:
        codes = list(binding.get("consequence_uncertainty_reason_codes") or [])
        if REPROJECTED_REASON not in codes:
            binding["consequence_uncertainty_reason_codes"] = sorted([*codes, REPROJECTED_REASON])
    binding["evidence_fingerprint"] = stable_hash(material)
    return binding


def build_input_select_dependency_binding(
    snapshot: Any, *, entity_id: str, index_metadata: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(entity_id, str) or not entity_id.startswith("input_select."):
        raise ValueError("input_select dependency target is invalid")
    reprojected_snapshot, reprojected, summary = _reproject(snapshot, entity_id)
    base = build_helper_dependency_risk_binding(
        reprojected_snapshot,
        entity_id=entity_id,
        index_metadata=index_metadata,
        target_scoped=True,
    )
    records, truncated = _trigger_records(snapshot, entity_id)
    return _wrap(
        base, records=records, truncated=truncated, reprojected=reprojected,
        summary=summary,
    )


def failed_input_select_binding(entity_id: str) -> dict[str, Any]:
    return _wrap(_failed_binding(entity_id, "failed"), records=[], truncated=False, reprojected=0)


async def read_input_select_dependency_risk(
    entity_id: str, *, refresh: bool = True, fenced: bool = False
) -> dict[str, Any]:
    from ..dependency import DEPENDENCY_ANALYSIS

    try:
        index = DEPENDENCY_ANALYSIS.require().index
    except Exception as exc:
        return {
            "binding": failed_input_select_binding(entity_id),
            "provenance": {
                "provider": "dependency_index",
                "completeness": "failed",
                "failure_category": type(exc).__name__[:64],
                "fallback": "none",
                "fallback_occurred": False,
            },
        }
    evidence = await HelperDependencyRiskService(
        index, binding_builder=build_input_select_dependency_binding
    ).assess(entity_id, refresh=refresh, fenced=fenced)
    binding = evidence.get("binding")
    if isinstance(binding, dict) and binding.get("model") != INPUT_SELECT_DEPENDENCY_MODEL:
        # The service's own failure path builds a v13 failed binding.
        evidence["binding"] = failed_input_select_binding(entity_id)
    return evidence


def option_trigger_disclosure(
    binding: dict[str, Any], *, current: str, option: str
) -> dict[str, Any]:
    """Project the bound records onto one ``current -> option`` change."""

    records = binding.get("option_trigger_records") or []
    triggers = []
    for record in records:
        effect = "unknown"
        if record.get("enablement") == "disabled":
            effect = DISABLED_EFFECT
        elif record.get("complete") is True and record.get("enablement") == "enabled":
            effect = option_effect(
                StateTriggerValues(
                    source_id="", source_entity_id=None,
                    config_path=str(record.get("configuration_path")), entity_ids=(),
                    to=tuple(record["to"]) if record.get("to") is not None else None,
                    from_=tuple(record["from"]) if record.get("from") is not None else None,
                    not_to=tuple(record.get("not_to") or ()),
                    not_from=tuple(record.get("not_from") or ()),
                    complete=True,
                ),
                current=current, option=option,
            )
        triggers.append({
            "automation_id": record.get("automation_id"),
            "configuration_path": record.get("configuration_path"),
            "effect": effect,
            **({"effect_meaning": DISABLED_EFFECT_MEANING} if effect == DISABLED_EFFECT else {}),
            "reason_codes": list(record.get("reason_codes") or []),
        })
    with_records = {item["automation_id"] for item in triggers}
    relevant = list(binding.get("relevant_downstream_object_ids") or [])
    return {
        "current_option": current,
        "requested_option": option,
        "option_triggers": triggers,
        "option_triggers_truncated": bool(binding.get("option_trigger_records_truncated")),
        # Dependencies that are not literal state triggers (conditions,
        # templates, other trigger kinds) have no per-option projection.
        "automations_without_literal_option_trigger": sorted(
            item for item in relevant if item not in with_records
        ),
        "per_option_effect_is_not_risk_evidence": True,
    }


__all__ = [
    "INPUT_SELECT_DEPENDENCY_COMPATIBLE_MODELS",
    "INPUT_SELECT_DEPENDENCY_EXECUTION_MODELS",
    "INPUT_SELECT_DEPENDENCY_MODEL",
    "INPUT_SELECT_DEPENDENCY_MODEL_V1",
    "LABEL_TARGET_EXCLUDED_REASON",
    "LABEL_TARGET_INCLUDED_REASON",
    "REPROJECTED_REASON",
    "build_input_select_dependency_binding",
    "failed_input_select_binding",
    "option_trigger_disclosure",
    "read_input_select_dependency_risk",
]
