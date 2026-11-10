"""HAMCP-298 rollback of governed automation and script creates.

The rollback of a verified create is a new, separately approved HAMCP-155
delete plan (``docs/HAMCP_329_298_SCENE_CREATE_AND_CREATE_ROLLBACK_DESIGN.md``
§3.4).  Each delete child keeps the unchanged ``configuration_delete_intent``
and its 155 policy marker; this layer adds one closed ownership marker to the
child's risk evidence.  The marker is in the policy subject (so the plan hash
binds it through the policy decision) and in the prepared operation's risk
evidence hash (so the F3 delete preflight can recheck it under the locks).

Option E (Josh decision 2026-11-08, item 13): when the registry entry's
``id`` and ``created_at`` and the body equal the creation capture and only
``modified_at`` is newer, the child is included with a disclosure and the
``created_object_registry_modified_since_create`` elevation.  That basis is
ownership evidence, never a physical consequence.
"""

from __future__ import annotations

from typing import Any

from . import created_object_identity as identity_model

MODEL = "hamcp298-created-object-rollback-v1"
SOURCE = "created_object_rollback"
POLICY_TRIGGER = "created_object_rollback_policy"
BASIS_MODIFIED_SINCE_CREATE = "created_object_registry_modified_since_create"
INCOMPLETE = "created_object_rollback_contract_incomplete"
OWNED = "owned"
MODIFIED_SINCE_CREATE = "modified_since_create"

# Exclusion reasons (§3.4.1, §3.4.2, §3.4.4).
NOT_VERIFIED = "created_object_not_verified"
IDENTITY_NOT_CAPTURED = "created_object_identity_not_captured"
CHANGED = "created_object_changed"
IDENTITY_CHANGED = "created_object_identity_changed"
ALREADY_ABSENT = "created_object_already_absent"
PREREQUISITE_EXCLUDED = "created_object_rollback_prerequisite_excluded"
READ_UNAVAILABLE = "created_object_read_unavailable"
NO_ELIGIBLE_CHILD = "no_eligible_created_object"
# Mixed forward plans (§3.4.4): an update child's restore.
RESTORE_NOT_VERIFIED = "rollback_restore_not_verified"
RESTORE_TARGET_CHANGED = "rollback_restore_target_changed"
PENDING_ADMISSION = "pending_signed_registry_admission"
# Update resource types a mixed rollback may restore (the generic route).
RESTORE_RESOURCE_TYPES = frozenset({"automation", "script", "helper"})

E_DISCLOSURE = (
    "The registry entry changed after Engineering created it; it may have "
    "been edited, or deleted and recreated outside Engineering, which Home "
    "Assistant does not distinguish."
)
RESIDUAL_DISCLOSURE = (
    "Ownership is the creation-time identity Engineering captured, never "
    "proof: a replacement that kept the same registry entry and an "
    "identical body cannot be distinguished from the created object."
)
REASON = "Rollback of governed create"

MARKER_FIELDS = frozenset(
    {
        "trigger",
        "field",
        "model",
        "source",
        "source_plan_id",
        "source_plan_hash",
        "source_operation_id",
        "source_child_id",
        "capture_fingerprint",
        "created_fingerprint",
        "registry_entry_id",
        "registry_created_at",
        "captured_modified_at",
        "observed_modified_at",
        "captured_entity_id",
        "observed_entity_id",
        "ownership",
        "basis",
    }
)


def marker(
    *,
    operation_id: str,
    identity: dict[str, Any],
    ownership: dict[str, Any],
    source_plan_id: str,
    source_plan_hash: str,
    source_operation_id: str,
    source_child_id: str,
) -> dict[str, str]:
    """The one closed ownership marker for a rollback delete child."""

    status = ownership["status"]
    if status not in {OWNED, MODIFIED_SINCE_CREATE}:
        raise ValueError("only an owned child is planned")
    return {
        "trigger": POLICY_TRIGGER,
        "field": operation_id,
        "model": MODEL,
        "source": SOURCE,
        "source_plan_id": source_plan_id,
        "source_plan_hash": source_plan_hash,
        "source_operation_id": source_operation_id,
        "source_child_id": source_child_id,
        "capture_fingerprint": identity["capture_fingerprint"],
        "created_fingerprint": identity["created_fingerprint"],
        "registry_entry_id": identity["registry_entry_id"],
        "registry_created_at": identity["registry_created_at"],
        "captured_modified_at": identity["registry_modified_at"],
        "observed_modified_at": ownership["observed_modified_at"],
        "captured_entity_id": identity["entity_id"],
        "observed_entity_id": ownership["observed_entity_id"],
        "ownership": status,
        "basis": (
            BASIS_MODIFIED_SINCE_CREATE if status == MODIFIED_SINCE_CREATE else ""
        ),
    }


def markers(operation: Any) -> list[dict[str, Any]]:
    return [
        item
        for item in operation.risk.evidence
        if isinstance(item, dict) and item.get("trigger") == POLICY_TRIGGER
    ]


def valid_marker(value: Any, operation: Any) -> bool:
    """Closed, self-consistent and bound to this exact delete child."""

    from .configuration_delete import intent as delete_intent

    if not isinstance(value, dict) or set(value) != MARKER_FIELDS:
        return False
    if not all(isinstance(item, str) for item in value.values()):
        return False
    try:
        intent = delete_intent(operation.proposed_config)
    except (TypeError, KeyError, ValueError, AttributeError):
        return False
    modified = value["captured_modified_at"] != value["observed_modified_at"]
    return bool(
        value["model"] == MODEL
        and value["source"] == SOURCE
        and value["field"] == operation.operation_id
        and value["ownership"] == (MODIFIED_SINCE_CREATE if modified else OWNED)
        and value["basis"]
        == (BASIS_MODIFIED_SINCE_CREATE if modified else "")
        and value["created_fingerprint"] == operation.current_state_fingerprint
        and value["created_fingerprint"] == intent.get("pre_image_fingerprint")
        and value["registry_entry_id"] == intent.get("registry_entry_id")
        and value["observed_entity_id"] == intent.get("entity_id")
        and all(
            value[key]
            for key in MARKER_FIELDS - {"basis"}
        )
    )


def entry_matches(binding: dict[str, Any], entry: dict[str, Any] | None) -> bool:
    """Preflight: the live registry entry equals the plan-bound observation."""

    if not isinstance(entry, dict):
        return False
    try:
        created_at = identity_model._timestamp(entry.get("created_at"))
        modified_at = identity_model._timestamp(entry.get("modified_at"))
    except identity_model.IdentityMismatch:
        return False
    return bool(
        entry.get("id") == binding["registry_entry_id"]
        and entry.get("entity_id") == binding["observed_entity_id"]
        and created_at == binding["registry_created_at"]
        and modified_at == binding["observed_modified_at"]
    )


def disclosure(binding: dict[str, str]) -> dict[str, Any]:
    """Per-child approver disclosure; never states proven ownership."""

    result: dict[str, Any] = {
        "model": MODEL,
        "source_plan_id": binding["source_plan_id"],
        "source_operation_id": binding["source_operation_id"],
        "ownership": binding["ownership"],
        "registry_entry_id": binding["registry_entry_id"],
        "captured_modified_at": binding["captured_modified_at"],
        "observed_modified_at": binding["observed_modified_at"],
        "residual_limitation": RESIDUAL_DISCLOSURE,
    }
    if binding["ownership"] == MODIFIED_SINCE_CREATE:
        result["basis"] = BASIS_MODIFIED_SINCE_CREATE
        result["statement"] = E_DISCLOSURE
    if binding["captured_entity_id"] != binding["observed_entity_id"]:
        result["captured_entity_id"] = binding["captured_entity_id"]
        result["observed_entity_id"] = binding["observed_entity_id"]
    return result


def statements(binding: dict[str, str]) -> list[str]:
    """Risk reasons shown with the child (bounded)."""

    if binding["ownership"] != MODIFIED_SINCE_CREATE:
        return []
    text = (
        f"{E_DISCLOSURE} Captured modified_at {binding['captured_modified_at']}, "
        f"observed {binding['observed_modified_at']}"
    )
    if binding["captured_entity_id"] != binding["observed_entity_id"]:
        text += (
            f"; entity id {binding['captured_entity_id']} is now "
            f"{binding['observed_entity_id']}"
        )
    return [(text + ". Elevated acknowledgement required.")[:300]]


__all__ = [
    "BASIS_MODIFIED_SINCE_CREATE",
    "E_DISCLOSURE",
    "MODEL",
    "POLICY_TRIGGER",
    "SOURCE",
    "disclosure",
    "entry_matches",
    "marker",
    "markers",
    "statements",
    "valid_marker",
]
