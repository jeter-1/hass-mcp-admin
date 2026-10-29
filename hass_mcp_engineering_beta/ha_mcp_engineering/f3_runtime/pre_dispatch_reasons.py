"""HAMCP-249: closed-vocabulary reasons for F3 pre-dispatch refusals.

Reporting only.  A reason is derived from the diagnostic codes the executor
already stores on the child's durable terminal event; nothing here changes what
preflight accepts or refuses, and the strict F3 execution-record schema is not
extended.  Every emitted ``error_code`` and ``check`` comes from the fixed
tables below, ``detail`` is fixed text, and identifiers are bounded.  A record
without a terminal event code reads as ``unknown_legacy``: a reason is never
inferred from state that does not name one.
"""

from __future__ import annotations

import re
from typing import Any

# The F3 execution record's identifier and diagnostic bounds (f3/models.py),
# restated so the governance service can import this module without importing
# ``..f3`` (HAMCP-181 design section 5).  A test pins them equal.
EVIDENCE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,95}$")
MAX_DIAGNOSTIC_ITEMS = 16
MAX_DIAGNOSTIC_LENGTH = 96


def bounded_diagnostics(values: object) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        return ()
    result: list[str] = []
    for value in values[:MAX_DIAGNOSTIC_ITEMS]:
        if isinstance(value, str) and EVIDENCE_PATTERN.fullmatch(value):
            result.append(value[:MAX_DIAGNOSTIC_LENGTH])
    return tuple(sorted(set(result), key=lambda item: item.encode("utf-8")))


REASON_MODEL = "f3_pre_dispatch_reason"
REASON_SCHEMA_VERSION = 1
UNKNOWN_LEGACY = "unknown_legacy"
UNCLASSIFIED = "unclassified_pre_dispatch_refusal"
SOURCE_CHILD_EVENT = "child_terminal_event"
SOURCE_UNKNOWN_LEGACY = "unknown_legacy"
SOURCE_PARENT_LAST_ERROR = "parent_last_error"
LEGACY_F1_CHECK = "legacy_f1_apply"
VERIFIER_DETAIL_PREFIX = "automation_verifier_"
SUMMARY_KEY = "pre_dispatch_refusal"
MAX_IDENTIFIER_LENGTH = 160

PRE_DISPATCH_OUTCOMES = frozenset(
    {
        "preflight_rejected",
        "lock_conflict",
        "provider_unavailable_pre_dispatch",
        "failed_pre_dispatch",
        "cancelled_pre_dispatch",
    }
)
TERMINAL_EVENT_TYPES = frozenset({"pre_dispatch_terminal", "execution_cancelled"})

_CHECK_DETAILS: dict[str, str] = {
    "lock_acquisition": (
        "Another task holds a lock this operation needs. Wait for it to "
        "finish, then create and approve a fresh plan."
    ),
    "lock_storage": (
        "The durable lock store failed before dispatch. Check add-on storage "
        "health, then retry with a fresh plan."
    ),
    "preflight_gate": (
        "The prepared operation or its policy snapshot no longer matches the "
        "approved plan. Create and approve a fresh plan."
    ),
    "plan_expiry": "The approved plan expired before dispatch. Create and approve a fresh plan.",
    "provider_admission": (
        "The reviewed provider for this operation is not admitted. Check "
        "provider health before retrying."
    ),
    "static_validation": (
        "The proposed configuration failed static validation. Correct the "
        "configuration and create a fresh plan."
    ),
    "plan_binding": (
        "The proposed configuration no longer matches its approved hash. "
        "Create and approve a fresh plan."
    ),
    "home_assistant_config_check": (
        "Home Assistant configuration check did not pass. Fix the reported "
        "configuration errors in Home Assistant, then retry."
    ),
    "readback_verifiability": (
        "The proposed configuration uses a shape the readback verifier cannot "
        "compare, so success could not be proven. Rewrite the named construct "
        "in a supported form."
    ),
    "target_state": (
        "The target's current state differs from what the plan expects. "
        "Re-read the target and create a fresh plan."
    ),
    "provider_read": (
        "The target's current state could not be read before dispatch. Check "
        "Home Assistant availability, then retry."
    ),
    "called_script_freshness": (
        "A script the automation calls changed after planning. Create and "
        "approve a fresh plan."
    ),
    "legacy_task_conflict": (
        "A legacy execution task is still active on this target. Let it "
        "finish before retrying."
    ),
    "retry_bundle": (
        "The retry bundle's prior members could not be proven current. "
        "Re-read the targets and create a fresh plan."
    ),
    "dispatch_authority": (
        "Core dispatch authority was unavailable or refused this operation. "
        "Check Core admission state before retrying."
    ),
    "preflight_exception": (
        "Preflight stopped on an internal error before dispatch. Check the "
        "add-on log for this child, then retry."
    ),
    "adapter_pre_dispatch": (
        "The provider adapter stopped before the irreversible boundary. "
        "Nothing was sent; retry with a fresh plan."
    ),
    "operator_cancel": "The operation was cancelled before dispatch.",
    "adapter_preflight": (
        "The adapter refused this operation before dispatch with the listed "
        "diagnostic codes."
    ),
    LEGACY_F1_CHECK: (
        "The legacy apply path refused this task before dispatch with the "
        "recorded error code."
    ),
    UNKNOWN_LEGACY: (
        "This refusal was recorded before reasons were persisted; no reason "
        "is available."
    ),
}

# Priority order: the first code present names the reason.  Durable codes
# are stored sorted, so their order carries no meaning.
_REASONS: tuple[tuple[str, str], ...] = (
    ("lock_storage_failure", "lock_storage"),
    ("lock_conflict", "lock_acquisition"),
    ("wait_timeout", "lock_acquisition"),
    ("lock_set_invalid", "lock_acquisition"),
    ("complete_lock_set_not_held", "lock_acquisition"),
    ("prepared_operation_invalid", "preflight_gate"),
    ("policy_snapshot_mismatch", "preflight_gate"),
    ("plan_expired", "plan_expiry"),
    ("provider_not_admitted", "provider_admission"),
    ("static_validation_failed", "static_validation"),
    ("proposed_hash_mismatch", "plan_binding"),
    ("configuration_validation_failed", "home_assistant_config_check"),
    ("configuration_validation_unavailable", "home_assistant_config_check"),
    ("configuration_not_verifiable", "readback_verifiability"),
    ("target_already_exists", "target_state"),
    ("target_entity_id_reserved", "target_state"),
    ("update_target_missing", "target_state"),
    ("resource_identity_mismatch", "target_state"),
    ("stale_target_state", "target_state"),
    ("dependency_evidence_changed", "target_state"),
    ("stale_reference_definition", "target_state"),
    ("label_namespace_changed", "target_state"),
    ("resource_read_unavailable", "provider_read"),
    ("stale_called_script", "called_script_freshness"),
    ("legacy_active_task_conflict", "legacy_task_conflict"),
    ("retry_bundle_proof_invalid", "retry_bundle"),
    ("retry_bundle_prefix_unverified", "retry_bundle"),
    ("retry_bundle_identity_mismatch", "retry_bundle"),
    ("retry_bundle_state_mismatch", "retry_bundle"),
    ("retry_bundle_read_unavailable", "retry_bundle"),
    ("core_requirement_unmapped", "dispatch_authority"),
    ("core_authority_unavailable", "dispatch_authority"),
    ("pending_signed_registry_admission", "dispatch_authority"),
    ("dispatch_authority_unavailable", "dispatch_authority"),
    ("adapter_failed_before_irreversible_boundary", "adapter_pre_dispatch"),
    ("preflight_failed", "preflight_exception"),
    ("operator_cancel_requested", "operator_cancel"),
)
REASON_CHECKS: dict[str, str] = dict(_REASONS)

# The readback verifier's fixed-literal refusal messages
# (governance/normalize.py), as identifiers.  A test pins this set to the
# source literals, so a new verifier message cannot go unreported.
AUTOMATION_VERIFIER_DETAILS = frozenset(
    VERIFIER_DETAIL_PREFIX + slug
    for slug in (
        "ambiguous_automation_action_alias",
        "ambiguous_automation_action_family",
        "ambiguous_top_level_automation_aliases",
        "invalid_automation_action_alias",
        "invalid_automation_action_alias_label",
        "invalid_automation_action_mapping_field",
        "invalid_automation_action_modifier",
        "invalid_automation_action_timeout",
        "invalid_automation_choose_branch_alias",
        "invalid_automation_condition_collection",
        "invalid_automation_delay_action",
        "invalid_automation_device_action_field",
        "invalid_automation_repeat_collection",
        "invalid_automation_repeat_count",
        "invalid_automation_response_variable",
        "invalid_automation_simple_action_option",
        "invalid_automation_simple_action_value",
        "invalid_automation_variables_action",
        "invalid_automation_wait_template",
        "invalid_automation_wait_trigger_action",
        "orphan_automation_conditional_action",
        "orphan_automation_default_action",
        "unsupported_automation_action_family",
        "unsupported_automation_action_sequence",
        "unsupported_automation_action_step",
        "unsupported_automation_choose_action",
        "unsupported_automation_choose_branch",
        "unsupported_automation_conditional_action",
        "unsupported_automation_device_action_fields",
        "unsupported_automation_mapping_keys",
        "unsupported_automation_parallel_action",
        "unsupported_automation_repeat_action",
        "unsupported_automation_sequence_action",
        "unsupported_automation_service_action_fields",
        "unsupported_automation_simple_action_fields",
        "unsupported_automation_trigger_condition_ids",
    )
)
REASON_CODES = frozenset(REASON_CHECKS) | {UNCLASSIFIED, UNKNOWN_LEGACY}
REASON_FIELDS = (
    "reason_model",
    "reason_schema_version",
    "error_code",
    "check",
    "detail",
    "verifier_rule",
    "reason_source",
    "normalized_outcome",
    "child_execution_id",
    "operation_id",
    "ordinal",
    "target_type",
    "target_id",
    "diagnostic_codes",
)


def _identifier(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if any(ord(char) < 0x21 or ord(char) > 0x7E for char in value):
        return None
    return value[:MAX_IDENTIFIER_LENGTH]


def _terminal_codes(record: Any) -> tuple[str, ...] | None:
    """Codes of the latest durable terminal event; ``None`` when absent."""

    for event in reversed(list(getattr(record, "events", ()) or ())):
        if isinstance(event, dict) and event.get("event_type") in TERMINAL_EVENT_TYPES:
            codes = bounded_diagnostics(event.get("diagnostic_codes"))
            return codes or None
    return None


def is_pre_dispatch_terminal(record: Any) -> bool:
    return bool(
        record is not None
        and record.terminal
        and record.dispatch_intent is None
        and not record.dispatch_count
        and record.normalized_outcome in PRE_DISPATCH_OUTCOMES
    )


def classify(codes: tuple[str, ...] | None) -> tuple[str, str, str | None]:
    """Return ``(error_code, check, verifier_rule)`` for stored codes."""

    if not codes:
        return UNKNOWN_LEGACY, UNKNOWN_LEGACY, None
    present = set(codes)
    rule = next(
        (code for code in sorted(present) if code in AUTOMATION_VERIFIER_DETAILS),
        None,
    )
    for code, check in _REASONS:
        if code in present:
            return code, check, rule
    return UNCLASSIFIED, "adapter_preflight", rule


def build_reason(
    *,
    codes: tuple[str, ...] | None,
    declaration: dict[str, Any],
    normalized_outcome: str | None,
) -> dict[str, Any]:
    error_code, check, rule = classify(codes)
    detail = _CHECK_DETAILS[check]
    if rule is not None:
        detail += " Verifier rule: " + rule[len(VERIFIER_DETAIL_PREFIX):].replace("_", " ") + "."
    ordinal = declaration.get("operation_ordinal")
    return {
        "reason_model": REASON_MODEL,
        "reason_schema_version": REASON_SCHEMA_VERSION,
        "error_code": error_code,
        "check": check,
        "detail": detail,
        "verifier_rule": rule,
        "reason_source": (
            SOURCE_UNKNOWN_LEGACY if error_code == UNKNOWN_LEGACY else SOURCE_CHILD_EVENT
        ),
        "normalized_outcome": (
            normalized_outcome
            if isinstance(normalized_outcome, str)
            and EVIDENCE_PATTERN.fullmatch(normalized_outcome)
            else None
        ),
        "child_execution_id": _identifier(declaration.get("child_id")),
        "operation_id": _identifier(declaration.get("operation_id")),
        "ordinal": ordinal if isinstance(ordinal, int) and not isinstance(ordinal, bool) else None,
        "target_type": _identifier(declaration.get("target_type")),
        "target_id": _identifier(declaration.get("target_id")),
        "diagnostic_codes": list(codes or ()),
    }


def child_reason(declaration: dict[str, Any], record: Any) -> dict[str, Any] | None:
    """The reason for one pre-dispatch terminal child, else ``None``."""

    if not is_pre_dispatch_terminal(record):
        return None
    return build_reason(
        codes=_terminal_codes(record),
        declaration=declaration,
        normalized_outcome=record.normalized_outcome,
    )


def recorded(value: Any) -> dict[str, Any] | None:
    """A previously persisted reason in its reviewed shape, else ``None``."""

    if not isinstance(value, dict) or value.get("reason_model") != REASON_MODEL:
        return None
    if value.get("error_code") not in REASON_CODES:
        return None
    return {key: value.get(key) for key in REASON_FIELDS}


def unknown_legacy(declaration: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_reason(codes=None, declaration=declaration or {}, normalized_outcome=None)


def legacy_f1_reason(code: Any, target: dict[str, Any] | None) -> dict[str, Any]:
    """A legacy F1 refusal: its recorded ``ErrorCode``/fixed literal code.

    Legacy codes come from the closed ``ErrorCode`` enum or fixed call-site
    literals; anything that is not a bounded identifier reads as
    ``unknown_legacy``.
    """

    target = target if isinstance(target, dict) else {}
    known = isinstance(code, str) and EVIDENCE_PATTERN.fullmatch(code) is not None
    reason = build_reason(
        codes=None,
        declaration={
            "target_type": target.get("target_type"),
            "target_id": target.get("target_id"),
        },
        normalized_outcome="failed_pre_dispatch" if known else None,
    )
    if known:
        reason.update(
            {
                "error_code": code,
                "check": LEGACY_F1_CHECK,
                "detail": _CHECK_DETAILS[LEGACY_F1_CHECK],
                "reason_source": SOURCE_PARENT_LAST_ERROR,
                "diagnostic_codes": [code],
            }
        )
    return reason


def legacy_f1_refusal(last_error: Any, target: dict[str, Any] | None) -> dict[str, Any]:
    """Read back a legacy F1 task's refusal without inventing one."""

    if isinstance(last_error, dict):
        stored = last_error.get("failure_information")
        if isinstance(stored, dict) and stored.get("reason_model") == REASON_MODEL:
            return {key: stored.get(key) for key in REASON_FIELDS}
        code = last_error.get("error_code", last_error.get("failure_category"))
        if code is not None:
            return legacy_f1_reason(code, target)
    return unknown_legacy()
