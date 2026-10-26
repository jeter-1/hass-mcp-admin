"""Domain-parameterized dependency reader for input_select (HAMCP-145, OD-11).

This reader reuses the reviewed v13 obligation projection unchanged, with
two input_select corrections, and adds per-option trigger disclosure:

* A ``call_service`` event-trigger obligation that the extractor proved
  disjoint is proven disjoint only for ``input_boolean``.  The extractor
  does not retain enough to re-prove it for ``input_select``, so every such
  terminal is re-projected as target-capable opaque evidence with
  conservative locks.  Opacity elevates risk; it never refuses on
  consequence alone (OD-4).
* The model is ``helper-dependency-risk-input-select-v1``.  Its evidence
  fingerprint binds the v13 material for the re-projected snapshot plus the
  literal state-trigger records for the exact target, so a trigger edit
  stales an approval.  The ``input_boolean`` v13 binding is unchanged.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..dependency.state_trigger_values import StateTriggerValues, option_effect
from .helper_dependency import (
    HelperDependencyRiskService,
    _failed_binding,
    _safe_automation_identity,
    build_helper_dependency_risk_binding,
)
from .normalize import stable_hash

INPUT_SELECT_DEPENDENCY_MODEL = "helper-dependency-risk-input-select-v1"
INPUT_SELECT_DEPENDENCY_EXECUTION_MODELS = frozenset({INPUT_SELECT_DEPENDENCY_MODEL})
REPROJECTED_REASON = "call_service_filter_unproven_for_input_select"
MAX_OPTION_TRIGGER_RECORDS = 64


def _reproject(snapshot: Any) -> tuple[Any, int]:
    obligations = tuple(getattr(snapshot, "obligations", ()) or ())
    changed = 0
    projected = []
    for item in obligations:
        if (
            item.obligation_kind == "call_service_event_trigger"
            and item.outcome == "proven_target_exclusion"
        ):
            changed += 1
            item = replace(
                item,
                outcome="bounded_semantic_opaque",
                target_selector_scope="target_capable",
                lock_projection="conservative",
                reason_code=REPROJECTED_REASON,
            )
        projected.append(item)
    if not changed:
        return snapshot, 0
    return replace(snapshot, obligations=tuple(projected)), changed


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
            "reason_codes": list(item.reason_codes),
        })
    records.sort(key=lambda value: (value["automation_id"], value["configuration_path"]))
    truncated = len(records) > MAX_OPTION_TRIGGER_RECORDS
    return records[:MAX_OPTION_TRIGGER_RECORDS], truncated


def _wrap(base: dict[str, Any], *, records: list[dict[str, Any]], truncated: bool,
          reprojected: int) -> dict[str, Any]:
    material = {
        "model": INPUT_SELECT_DEPENDENCY_MODEL,
        "base_model": base.get("model"),
        "base_evidence_fingerprint": base.get("evidence_fingerprint"),
        "option_trigger_records": records,
        "option_trigger_records_truncated": truncated,
        "reprojected_call_service_obligations": reprojected,
    }
    binding = {
        **base,
        "model": INPUT_SELECT_DEPENDENCY_MODEL,
        "base_model": base.get("model"),
        "base_evidence_fingerprint": base.get("evidence_fingerprint"),
        "option_trigger_records": records,
        "option_trigger_records_truncated": truncated,
        "reprojected_call_service_obligations": reprojected,
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
    reprojected_snapshot, reprojected = _reproject(snapshot)
    base = build_helper_dependency_risk_binding(
        reprojected_snapshot, entity_id=entity_id, index_metadata=index_metadata
    )
    records, truncated = _trigger_records(snapshot, entity_id)
    return _wrap(base, records=records, truncated=truncated, reprojected=reprojected)


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
        if record.get("complete") is True:
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
    "INPUT_SELECT_DEPENDENCY_EXECUTION_MODELS",
    "INPUT_SELECT_DEPENDENCY_MODEL",
    "REPROJECTED_REASON",
    "build_input_select_dependency_binding",
    "failed_input_select_binding",
    "option_trigger_disclosure",
    "read_input_select_dependency_risk",
]
