"""Hard F3-B atomicity gate derived from exact reviewed source."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .constants import ATOMICITY_MODEL, SUPPORTED_UPSTREAM_VERSIONS
from .errors import AtomicityGateError
from .json_codec import engineering_sha256
from .models import AtomicityDecision, AtomicityStatus


HOME_ASSISTANT_RELEASE = "2026.7.4"
HOME_ASSISTANT_SOURCE_COMMIT = "a4feaf06248c529f60021fc8be93ee69bc9b3084"
UPSTREAM_SOURCE_COMMITS = {
    "7.14.2": "904c14ebbe76de700f7c3535f5cc71c017dca12e",
    "8.0.0": "9dd3ac620e3149cd34ec3c990b6ee81e778191f2",
}


def assess_atomicity(upstream_version: str) -> AtomicityDecision:
    """Return the immutable blocked decision for both reviewed releases."""

    if upstream_version not in SUPPORTED_UPSTREAM_VERSIONS:
        raise AtomicityGateError("Unknown upstream release cannot satisfy atomicity")
    reasons = (
        "upstream_hash_check_and_save_are_separate_awaited_calls",
        "home_assistant_lovelace_save_accepts_no_expected_hash",
        "home_assistant_lovelace_save_has_no_transaction_or_receipt",
        "engineering_lock_cannot_exclude_ui_integrations_or_other_clients",
        "post_write_readback_cannot_detect_an_overwritten_external_edit",
    )
    source_evidence = {
        "model": ATOMICITY_MODEL,
        "upstream_version": upstream_version,
        "upstream_source_commit": UPSTREAM_SOURCE_COMMITS[upstream_version],
        "upstream_hash_read": "_fetch_and_verify_dashboard_hash",
        "upstream_save": "_save_dashboard_python_transform",
        "home_assistant_version": HOME_ASSISTANT_RELEASE,
        "home_assistant_source_commit": HOME_ASSISTANT_SOURCE_COMMIT,
        "home_assistant_command": "lovelace/config/save",
        "home_assistant_arguments": ["config", "url_path"],
        "reason_codes": list(reasons),
    }
    return AtomicityDecision(
        model=ATOMICITY_MODEL,
        status=AtomicityStatus.BLOCKED,
        mechanism=None,
        reason_codes=reasons,
        exact_upstream_release=upstream_version,
        home_assistant_release=HOME_ASSISTANT_RELEASE,
        source_evidence_sha256=engineering_sha256(source_evidence),
    )


def require_executable_atomicity(decision: AtomicityDecision) -> None:
    if decision.status not in {
        AtomicityStatus.PROVEN_ATOMIC,
        AtomicityStatus.AUTHORITATIVE_WRITER_EXCLUSION,
    }:
        raise AtomicityGateError(
            "Executable dashboard writes remain disabled: external-writer "
            "lost-update protection is unproven"
        )


@dataclass(frozen=True)
class NonAtomicInterleavingResult:
    phase: str
    conflict_rejected_before_save: bool
    modeled_setter_saved: bool
    external_write_overwritten: bool
    readback_detects_overwrite: bool
    final_configuration: dict[str, Any]
    setter_invocation_count: int = 0
    fixture_mutation_count: int = 0


def simulate_non_atomic_interleaving(
    *,
    approved_preread: dict[str, Any],
    approved_result: dict[str, Any],
    external_result: dict[str, Any],
    phase: str,
) -> NonAtomicInterleavingResult:
    """Model the exact separate ha-mcp read/check then HA save sequence."""

    preread_snapshot = deepcopy(approved_preread)
    approved_snapshot = deepcopy(approved_result)
    external_snapshot = deepcopy(external_result)
    current = preread_snapshot
    if phase == "before_preflight":
        current = external_snapshot
        return NonAtomicInterleavingResult(
            phase, True, False, False, False, current
        )
    if phase == "after_preflight_before_hash_check":
        current = external_snapshot
    hash_check_matches = engineering_sha256(current) == engineering_sha256(
        preread_snapshot
    )
    if not hash_check_matches:
        return NonAtomicInterleavingResult(
            phase, True, False, False, False, current
        )
    if phase == "during_hash_check_save_gap":
        current = external_snapshot
        current = approved_snapshot
        return NonAtomicInterleavingResult(
            phase, False, True, True, False, current
        )
    current = approved_snapshot
    if phase == "immediately_after_save":
        current = external_snapshot
        return NonAtomicInterleavingResult(
            phase, False, True, False, True, current
        )
    if phase != "unchanged":
        raise AtomicityGateError("Unknown atomicity interleaving phase")
    return NonAtomicInterleavingResult(
        phase, False, True, False, False, current
    )
