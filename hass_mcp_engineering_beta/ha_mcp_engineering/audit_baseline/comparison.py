"""Pure local comparison semantics for validated automation baselines."""

from __future__ import annotations

import json
from typing import Any

from .models import (
    COMPARISON_SCHEMA,
    FINGERPRINT_MODEL,
    AutomationRecord,
    Baseline,
    CaptureInterval,
    Classification,
    ComparisonRecord,
    ComparisonReport,
)
from .validation import BaselineValidationError, _parse_timestamp


MAX_OUTPUT_BYTES = 1_000_000
MAX_OUTPUT_DETAILS = 1_000

# HAMCP-235 (owner decision, Josh, 2026-10-27, option a): what a comparison
# cannot verify is stated once here, not repeated as a reason on every row.
CAVEAT_STATEMENTS = {
    "installation_identity_unverified":
        "installation identity unverified: assumes both sides are from this installation",
    "authority_continuity_unestablished":
        "Core authority continuity unestablished: assumes the same Home Assistant Core served each capture",
    "non_atomic_capture":
        "captures are not atomic: a change and its reversion inside a capture interval can go unobserved",
}


def _caveat(code: str, sides: list[str]) -> dict[str, Any]:
    return {"code": code, "statement": CAVEAT_STATEMENTS[code], "sides": sides}


def _comparison_reasons(earlier: Baseline, later: Baseline) -> tuple[str, ...]:
    reasons: list[str] = []
    if _parse_timestamp(later.capture_start, "capture_start_invalid") < _parse_timestamp(
        earlier.capture_end, "capture_end_invalid"
    ):
        reasons.append("capture_intervals_not_ordered")
    if earlier.baseline_id == later.baseline_id:
        reasons.append("same_baseline_id")
    if (
        earlier.source_artifact_sha256 is not None
        and earlier.source_artifact_sha256 == later.source_artifact_sha256
    ):
        reasons.append("same_source_artifact_digest")
    return tuple(sorted(reasons))


def _caveats(pairs: tuple[tuple[str, Baseline], ...], *, unverified_sides: tuple[str, ...] = ()) -> tuple[dict[str, Any], ...]:
    installation = [side for side, item in pairs if not item.installation.established] + list(unverified_sides)
    authority = [side for side, item in pairs
                 if item.consistency.get("authority_drift") != "none_observed_at_capture_fences"]
    atomic = [side for side, item in pairs if item.capture_non_atomic]
    return tuple(_caveat(code, sides) for code, sides in (
        ("installation_identity_unverified", installation),
        ("authority_continuity_unestablished", authority),
        ("non_atomic_capture", atomic)) if sides)


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
    installation_mismatch: bool,
    fingerprint_contract_compatible: bool,
    inventory_scope_comparable: bool,
) -> list[str]:
    reasons: list[str] = []
    # HAMCP-235: an unestablished identity is a comparison-level caveat; only
    # two established, different installations make a row incomparable.
    if installation_mismatch:
        reasons.append("installation_identity_mismatch")
    if not inventory_scope_comparable:
        reasons.append("inventory_scope_mismatch")
    if not earlier.identity_verified:
        reasons.append("earlier_mapping_unverified")
    if not later.identity_verified:
        reasons.append("later_mapping_unverified")
    # HAMCP-228: a record the capture never attempted is unknown, not changed.
    if "timed_out" in (earlier.configuration_status, later.configuration_status):
        reasons.append("timed_out")
    if earlier.configuration_status != "readable":
        reasons.append("earlier_configuration_not_readable")
    if later.configuration_status != "readable":
        reasons.append("later_configuration_not_readable")
    if not earlier.coverage.comparison_complete:
        reasons.append("earlier_configuration_coverage_incomplete")
    if not later.coverage.comparison_complete:
        reasons.append("later_configuration_coverage_incomplete")
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
    installation_mismatch: bool,
    inventory_scope_comparable: bool,
) -> tuple[bool, bool]:
    if (
        installation_mismatch
        or not inventory_scope_comparable
        or earlier is None
        or later is None
        or not earlier.identity_verified
        or not later.identity_verified
    ):
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

    comparison_reasons = _comparison_reasons(earlier, later)
    installation_comparable, installation_reason = _installation_relation(earlier, later)
    installation_mismatch = installation_reason == "installation_identity_mismatch"
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
                installation_mismatch=installation_mismatch,
                fingerprint_contract_compatible=fingerprint_contract_compatible,
                inventory_scope_comparable=inventory_scope_comparable,
            )
            if reasons:
                classification = Classification.UNKNOWN
            elif old.configuration_digest == new.configuration_digest:
                classification = Classification.UNCHANGED
            else:
                classification = Classification.CHANGED
        elif old is None and new is not None:
            if installation_mismatch:
                reasons.append("installation_identity_mismatch")
            if not inventory_scope_comparable:
                reasons.append("inventory_scope_mismatch")
            if not earlier_inventory_absence_authoritative:
                reasons.append("earlier_inventory_absence_not_authoritative")
            if not new.identity_verified:
                reasons.append("later_mapping_unverified")
            classification = Classification.UNKNOWN if reasons else Classification.ADDED
        elif old is not None and new is None:
            if installation_mismatch:
                reasons.append("installation_identity_mismatch")
            if not inventory_scope_comparable:
                reasons.append("inventory_scope_mismatch")
            if not later_inventory_absence_authoritative:
                reasons.append("later_inventory_absence_not_authoritative")
            if not old.identity_verified:
                reasons.append("earlier_mapping_unverified")
            classification = Classification.UNKNOWN if reasons else Classification.REMOVED
        else:  # pragma: no cover - union construction makes this unreachable
            raise AssertionError("comparison identity union is inconsistent")

        if comparison_reasons:
            classification = Classification.UNKNOWN
            reasons.extend(comparison_reasons)
            entity_changed, enabled_changed = False, False
        else:
            entity_changed, enabled_changed = _separate_observations(
                old, new, installation_mismatch=installation_mismatch,
                inventory_scope_comparable=inventory_scope_comparable,
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
        earlier_capture=CaptureInterval(
            earlier.capture_start, earlier.capture_end, earlier.capture_non_atomic
        ),
        later_capture=CaptureInterval(
            later.capture_start, later.capture_end, later.capture_non_atomic
        ),
        comparison_reasons=comparison_reasons,
        installation_comparable=installation_comparable,
        installation_reason=installation_reason,
        inventory_scope_comparable=inventory_scope_comparable,
        authority_continuity_comparable=authority_continuity_comparable,
        earlier_inventory_absence_authoritative=earlier_inventory_absence_authoritative,
        later_inventory_absence_authoritative=later_inventory_absence_authoritative,
        fingerprint_contract_compatible=fingerprint_contract_compatible,
        records=tuple(results),
        limitations=limitations,
        caveats=_caveats((("earlier", earlier), ("later", later))),
    )


def comparison_report_dict(report: ComparisonReport) -> dict[str, Any]:
    return {
        "schema": COMPARISON_SCHEMA,
        "earlier_baseline_id": report.earlier_baseline_id,
        "later_baseline_id": report.later_baseline_id,
        "capture_intervals": {
            "earlier": report.earlier_capture.public(),
            "later": report.later_capture.public(),
        },
        "global": {
            "comparison_eligible": report.comparison_eligible,
            "comparison_reasons": list(report.comparison_reasons),
            "installation_comparable": report.installation_comparable,
            "installation_reason": report.installation_reason,
            "inventory_scope_comparable": report.inventory_scope_comparable,
            "authority_continuity_comparable": report.authority_continuity_comparable,
            "earlier_inventory_absence_authoritative": report.earlier_inventory_absence_authoritative,
            "later_inventory_absence_authoritative": report.later_inventory_absence_authoritative,
            "fingerprint_contract_compatible": report.fingerprint_contract_compatible,
        },
        "caveats": [dict(item, sides=list(item["sides"])) for item in report.caveats],
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
    """Render one deterministic report without repeated whole-report fitting."""

    return _encoded(
        bound_report_dict(
            comparison_report_dict(report), max_bytes=max_bytes, max_details=max_details
        )
    )


def bound_report_dict(
    value: dict[str, Any],
    *,
    max_bytes: int = MAX_OUTPUT_BYTES,
    max_details: int = MAX_OUTPUT_DETAILS,
) -> dict[str, Any]:
    """Select the leading ``records`` of ``value`` that fit ``max_bytes``.

    ``value`` holds ``records``, ``details_truncated`` and
    ``omitted_detail_count``. (``compare_automation_baselines`` pages its
    records instead of truncating them.) The fixed header is serialized
    once. Each record detail is serialized once while accounting exact list
    bytes. The complete selected report is then serialized once for a final
    defensive size check.
    """

    if type(max_bytes) is not int or max_bytes < 1_024 or max_bytes > MAX_OUTPUT_BYTES:
        raise BaselineValidationError("output_size_limit_invalid")
    if type(max_details) is not int or not 0 <= max_details <= MAX_OUTPUT_DETAILS:
        raise BaselineValidationError("output_detail_limit_invalid")

    value = dict(value)
    all_details = value.pop("records")
    total = len(all_details)
    value["details_truncated"] = total > 0
    value["omitted_detail_count"] = total
    value["records"] = []
    base_size = len(_encoded(value))
    if base_size > max_bytes:
        raise BaselineValidationError("output_size_limit_exceeded")

    selected: list[dict[str, Any]] = []
    detail_bytes = 0
    for detail in all_details[:max_details]:
        encoded_detail = _encoded(detail)
        candidate_detail_bytes = detail_bytes + len(encoded_detail) + (1 if selected else 0)
        omitted = total - len(selected) - 1
        # Adjust the exact header for count digits and true -> false (+1 byte)
        # without repeatedly serializing the accumulated report.
        header_delta = len(str(omitted)) - len(str(total)) + (1 if omitted == 0 else 0)
        # Replacing [] with [details] increases total by exact detail bytes and commas.
        if base_size + header_delta + candidate_detail_bytes > max_bytes:
            break
        selected.append(detail)
        detail_bytes = candidate_detail_bytes

    omitted = len(all_details) - len(selected)
    value["records"] = selected
    value["details_truncated"] = omitted > 0
    value["omitted_detail_count"] = omitted
    if len(_encoded(value)) > max_bytes:
        # Exact incremental sizing should make this only an implementation
        # regression guard, never a trigger for repeated whole-report fitting.
        raise BaselineValidationError("output_size_limit_exceeded")
    return value


# HAMCP-228: recorded fingerprints (the register keeps 16-character prefixes).
FINGERPRINT_COMPARISON_SCHEMA = "automation-audit-fingerprint-comparison-v1"
PREFIX_MATCH = "PREFIX_MATCH"
MIN_PREFIX = 16
TOOL_COUNT_KEYS = ("UNCHANGED", PREFIX_MATCH, "CHANGED", "ADDED", "REMOVED", "UNKNOWN", "TOTAL")


def compare_recorded_fingerprints(
    capture: Baseline,
    fingerprints: dict[str, str],
    fingerprint_model: str,
) -> dict[str, Any]:
    """Compare one validated capture with caller-recorded fingerprints.

    ``fingerprints`` maps a configuration id to 16..64 lowercase hex
    characters (the leading part of the record's sha256 configuration
    digest). A full 64-character match is ``UNCHANGED``; a shorter matching
    prefix is ``PREFIX_MATCH`` (reason ``prefix_match:<n>``), never counted as
    ``UNCHANGED``. A differing prefix proves a different digest: ``CHANGED``.
    Recorded fingerprints carry no installation identity or inventory
    authority, so an id on one side only is ``UNKNOWN``, never ``ADDED`` or
    ``REMOVED``. A ``fingerprint_model`` other than the capture's makes every
    record ``UNKNOWN`` (``fingerprint_model_mismatch``).
    """

    model_compatible = bool(
        fingerprint_model == FINGERPRINT_MODEL
        and capture.fingerprint_contract.model == FINGERPRINT_MODEL
    )
    authority_continuous = (
        capture.consistency.get("authority_drift") == "none_observed_at_capture_fences"
    )
    by_id = capture.records_by_id
    counts = {key: 0 for key in TOOL_COUNT_KEYS}
    details: list[dict[str, Any]] = []
    for configuration_id in sorted(set(by_id) | set(fingerprints)):
        record = by_id.get(configuration_id)
        recorded = fingerprints.get(configuration_id)
        reasons: list[str] = []
        if not model_compatible:
            reasons.append("fingerprint_model_mismatch")
        if record is None:
            reasons.append("absent_from_capture")
        elif recorded is None:
            reasons.append("absent_from_recorded_fingerprints")
        else:
            # HAMCP-235: installation identity and authority continuity are
            # comparison-level caveats, not row reasons.
            if not record.identity_verified:
                reasons.append("capture_mapping_unverified")
            if record.configuration_status == "timed_out":
                reasons.append("timed_out")
            if record.configuration_status != "readable":
                reasons.append("capture_configuration_not_readable")
            if not record.coverage.comparison_complete:
                reasons.append("capture_configuration_coverage_incomplete")
            if record.fingerprint_model != fingerprint_model:
                reasons.append("fingerprint_model_mismatch")
            if record.configuration_digest is None:
                reasons.append("capture_configuration_digest_missing")
        if reasons:
            classification = "UNKNOWN"
        else:
            digest = record.configuration_digest.removeprefix("sha256:")
            if not digest.startswith(recorded):
                classification = "CHANGED"
            elif len(recorded) == len(digest):
                classification = "UNCHANGED"
            else:
                classification = PREFIX_MATCH
                reasons.append(f"prefix_match:{len(recorded)}")
        counts[classification] += 1
        details.append({
            "configuration_id": configuration_id,
            "classification": classification,
            "reasons": sorted(set(reasons)),
            "entity_id": record.entity_id if record else None,
            "enabled_state": record.enabled_state if record else None,
            "configuration_digest": {
                "capture": record.configuration_digest if record else None,
                "recorded": recorded,
            },
        })
    counts["TOTAL"] = len(details)
    return {
        "schema": FINGERPRINT_COMPARISON_SCHEMA,
        "capture_baseline_id": capture.baseline_id,
        "capture_interval": CaptureInterval(
            capture.capture_start, capture.capture_end, capture.capture_non_atomic
        ).public(),
        "global": {
            "fingerprint_model": fingerprint_model,
            "fingerprint_model_compatible": model_compatible,
            "capture_installation_established": capture.installation.established,
            "capture_authority_continuous": authority_continuous,
            "recorded_side_installation_identity": "not_recorded",
            "recorded_side_inventory_authority": "not_recorded",
        },
        "caveats": list(_caveats((("capture", capture),), unverified_sides=("recorded_fingerprints",))),
        "counts": counts,
        "limitations": [
            "unchanged_counts_full_digest_matches_only_prefix_match_is_counted_separately",
            "recorded_fingerprints_carry_no_installation_identity_or_inventory_authority",
            "an_id_on_one_side_only_is_unknown_not_added_or_removed",
            "configuration_digests_identify_captured_representation_changes_not_field_level_edits",
        ],
        "details_truncated": False,
        "omitted_detail_count": 0,
        "records": details,
    }
