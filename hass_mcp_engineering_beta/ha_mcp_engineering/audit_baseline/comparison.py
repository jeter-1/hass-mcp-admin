"""Pure local comparison semantics for validated automation baselines."""

from __future__ import annotations

import json
from typing import Any

from .models import (
    COMPARISON_SCHEMA,
    FINGERPRINT_MODEL,
    AutomationRecord,
    Baseline,
    Classification,
    ComparisonRecord,
    ComparisonReport,
)
from .validation import BaselineValidationError


MAX_OUTPUT_BYTES = 1_000_000
MAX_OUTPUT_DETAILS = 1_000


def _installation_relation(earlier: Baseline, later: Baseline) -> tuple[bool, str | None]:
    if not earlier.installation.established or not later.installation.established:
        return False, "installation_identity_unestablished"
    if earlier.installation.installation_id != later.installation.installation_id:
        return False, "installation_identity_mismatch"
    return True, None


def _shared_unknown_reasons(
    earlier: AutomationRecord,
    later: AutomationRecord,
    *,
    installation_comparable: bool,
    installation_reason: str | None,
    fingerprint_contract_compatible: bool,
    inventory_scope_comparable: bool,
    earlier_authority_continuous: bool,
    later_authority_continuous: bool,
) -> list[str]:
    reasons: list[str] = []
    if not installation_comparable:
        reasons.append(installation_reason or "installation_identity_unestablished")
    if not inventory_scope_comparable:
        reasons.append("inventory_scope_mismatch")
    if not earlier.identity_verified:
        reasons.append("earlier_mapping_unverified")
    if not later.identity_verified:
        reasons.append("later_mapping_unverified")
    if earlier.configuration_status != "readable":
        reasons.append("earlier_configuration_not_readable")
    if later.configuration_status != "readable":
        reasons.append("later_configuration_not_readable")
    if not earlier.coverage.comparison_complete:
        reasons.append("earlier_configuration_coverage_incomplete")
    if not later.coverage.comparison_complete:
        reasons.append("later_configuration_coverage_incomplete")
    if not earlier_authority_continuous:
        reasons.append("earlier_authority_continuity_unestablished")
    if not later_authority_continuous:
        reasons.append("later_authority_continuity_unestablished")
    if not fingerprint_contract_compatible:
        reasons.append("fingerprint_contract_incompatible")
    if earlier.fingerprint_model != later.fingerprint_model:
        reasons.append("record_fingerprint_model_mismatch")
    if earlier.configuration_digest is None:
        reasons.append("earlier_configuration_digest_missing")
    if later.configuration_digest is None:
        reasons.append("later_configuration_digest_missing")
    return sorted(set(reasons))


def _separate_observations(
    earlier: AutomationRecord | None,
    later: AutomationRecord | None,
    *,
    installation_comparable: bool,
) -> tuple[bool, bool]:
    if not installation_comparable or earlier is None or later is None:
        return False, False
    entity_changed = bool(
        earlier.identity_verified
        and later.identity_verified
        and earlier.entity_id
        and later.entity_id
        and earlier.entity_id != later.entity_id
    )
    enabled_changed = bool(
        earlier.enabled_state in {"on", "off"}
        and later.enabled_state in {"on", "off"}
        and earlier.enabled_state != later.enabled_state
    )
    return entity_changed, enabled_changed


def compare_baselines(earlier: Baseline, later: Baseline) -> ComparisonReport:
    """Compare two already validated baselines without provider access."""

    installation_comparable, installation_reason = _installation_relation(earlier, later)
    inventory_scope_comparable = earlier.inventory.scope == later.inventory.scope
    fingerprint_contract_compatible = bool(
        earlier.fingerprint_contract == later.fingerprint_contract
        and earlier.fingerprint_contract.model == FINGERPRINT_MODEL
    )
    earlier_authority_continuous = (
        earlier.consistency.get("authority_drift")
        == "none_observed_at_capture_fences"
    )
    later_authority_continuous = (
        later.consistency.get("authority_drift")
        == "none_observed_at_capture_fences"
    )
    authority_continuity_comparable = bool(
        earlier_authority_continuous and later_authority_continuous
    )
    earlier_inventory_absence_authoritative = bool(
        earlier.inventory.absence_is_authoritative
        and earlier.consistency.get("inventory_drift")
        == "none_observed_at_capture_fences"
    )
    later_inventory_absence_authoritative = bool(
        later.inventory.absence_is_authoritative
        and later.consistency.get("inventory_drift")
        == "none_observed_at_capture_fences"
    )

    earlier_by_id = earlier.records_by_id
    later_by_id = later.records_by_id
    all_ids = sorted(set(earlier_by_id) | set(later_by_id))
    results: list[ComparisonRecord] = []

    for configuration_id in all_ids:
        old = earlier_by_id.get(configuration_id)
        new = later_by_id.get(configuration_id)
        reasons: list[str] = []

        if old is not None and new is not None:
            reasons = _shared_unknown_reasons(
                old,
                new,
                installation_comparable=installation_comparable,
                installation_reason=installation_reason,
                fingerprint_contract_compatible=fingerprint_contract_compatible,
                inventory_scope_comparable=inventory_scope_comparable,
                earlier_authority_continuous=earlier_authority_continuous,
                later_authority_continuous=later_authority_continuous,
            )
            if reasons:
                classification = Classification.UNKNOWN
            elif old.configuration_digest == new.configuration_digest:
                classification = Classification.UNCHANGED
            else:
                classification = Classification.CHANGED
        elif old is None and new is not None:
            if not installation_comparable:
                reasons.append(installation_reason or "installation_identity_unestablished")
            if not inventory_scope_comparable:
                reasons.append("inventory_scope_mismatch")
            if not earlier_inventory_absence_authoritative:
                reasons.append("earlier_inventory_absence_not_authoritative")
            if not new.identity_verified:
                reasons.append("later_mapping_unverified")
            classification = Classification.UNKNOWN if reasons else Classification.ADDED
        elif old is not None and new is None:
            if not installation_comparable:
                reasons.append(installation_reason or "installation_identity_unestablished")
            if not inventory_scope_comparable:
                reasons.append("inventory_scope_mismatch")
            if not later_inventory_absence_authoritative:
                reasons.append("later_inventory_absence_not_authoritative")
            if not old.identity_verified:
                reasons.append("earlier_mapping_unverified")
            classification = Classification.UNKNOWN if reasons else Classification.REMOVED
        else:  # pragma: no cover - union construction makes this unreachable
            raise AssertionError("comparison identity union is inconsistent")

        entity_changed, enabled_changed = _separate_observations(
            old, new, installation_comparable=installation_comparable
        )
        results.append(
            ComparisonRecord(
                configuration_id=configuration_id,
                classification=classification,
                reasons=tuple(sorted(set(reasons))),
                earlier_entity_id=old.entity_id if old else None,
                later_entity_id=new.entity_id if new else None,
                entity_id_changed=entity_changed,
                earlier_enabled_state=old.enabled_state if old else None,
                later_enabled_state=new.enabled_state if new else None,
                enabled_state_changed=enabled_changed,
                earlier_digest=old.configuration_digest if old else None,
                later_digest=new.configuration_digest if new else None,
            )
        )

    limitations = (
        "configuration_digests_identify_captured_representation_changes_not_field_level_edits",
        "matching_digests_do_not_prove_uninterrupted_equality_during_non_atomic_capture_intervals",
        "unchanged_blueprint_reference_or_inputs_do_not_prove_external_blueprint_body_or_effective_behavior_unchanged",
        "raw_configuration_bodies_are_not_required_or_emitted_by_comparison",
    )
    return ComparisonReport(
        earlier_baseline_id=earlier.baseline_id,
        later_baseline_id=later.baseline_id,
        installation_comparable=installation_comparable,
        installation_reason=installation_reason,
        inventory_scope_comparable=inventory_scope_comparable,
        authority_continuity_comparable=authority_continuity_comparable,
        earlier_inventory_absence_authoritative=earlier_inventory_absence_authoritative,
        later_inventory_absence_authoritative=later_inventory_absence_authoritative,
        fingerprint_contract_compatible=fingerprint_contract_compatible,
        records=tuple(results),
        limitations=limitations,
    )


def comparison_report_dict(report: ComparisonReport) -> dict[str, Any]:
    return {
        "schema": COMPARISON_SCHEMA,
        "earlier_baseline_id": report.earlier_baseline_id,
        "later_baseline_id": report.later_baseline_id,
        "global": {
            "installation_comparable": report.installation_comparable,
            "installation_reason": report.installation_reason,
            "inventory_scope_comparable": report.inventory_scope_comparable,
            "authority_continuity_comparable": report.authority_continuity_comparable,
            "earlier_inventory_absence_authoritative": report.earlier_inventory_absence_authoritative,
            "later_inventory_absence_authoritative": report.later_inventory_absence_authoritative,
            "fingerprint_contract_compatible": report.fingerprint_contract_compatible,
        },
        "counts": report.counts,
        "entity_id_change_count": sum(item.entity_id_changed for item in report.records),
        "enabled_state_change_count": sum(item.enabled_state_changed for item in report.records),
        "limitations": list(report.limitations),
        "details_truncated": False,
        "omitted_detail_count": 0,
        "records": [item.public() for item in report.records],
    }


def _encoded(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise BaselineValidationError("comparison_serialization_failed") from None


def render_bounded_report(
    report: ComparisonReport,
    *,
    max_bytes: int = MAX_OUTPUT_BYTES,
    max_details: int = MAX_OUTPUT_DETAILS,
) -> bytes:
    """Render one deterministic report without repeated whole-report fitting.

    The fixed header is serialized once. Each record detail is serialized once
    while accounting exact list bytes. The complete selected report is then
    serialized once for a final defensive size check.
    """

    if type(max_bytes) is not int or max_bytes < 1_024 or max_bytes > MAX_OUTPUT_BYTES:
        raise BaselineValidationError("output_size_limit_invalid")
    if type(max_details) is not int or not 0 <= max_details <= MAX_OUTPUT_DETAILS:
        raise BaselineValidationError("output_detail_limit_invalid")

    value = comparison_report_dict(report)
    all_details = value.pop("records")
    # ``false`` is one byte longer than ``true`` in JSON, so use the longer
    # representation while sizing the fixed header.  This makes the estimate
    # conservative even when every detail fits and the final flag becomes false.
    value["details_truncated"] = False
    value["omitted_detail_count"] = len(all_details)
    value["records"] = []
    base_size = len(_encoded(value))
    if base_size > max_bytes:
        raise BaselineValidationError("output_size_limit_exceeded")

    selected: list[dict[str, Any]] = []
    detail_bytes = 0
    for detail in all_details[:max_details]:
        encoded_detail = _encoded(detail)
        candidate_detail_bytes = detail_bytes + len(encoded_detail) + (1 if selected else 0)
        # Replacing [] with [details] increases total by exact detail bytes and commas.
        if base_size + candidate_detail_bytes > max_bytes:
            break
        selected.append(detail)
        detail_bytes = candidate_detail_bytes

    omitted = len(all_details) - len(selected)
    value["records"] = selected
    value["details_truncated"] = omitted > 0
    value["omitted_detail_count"] = omitted
    rendered = _encoded(value)
    if len(rendered) > max_bytes:
        # The conservative header used the maximum omitted count, so this should
        # only catch an implementation regression rather than trigger refitting.
        raise BaselineValidationError("output_size_limit_exceeded")
    return rendered
