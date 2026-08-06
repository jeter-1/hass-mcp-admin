"""Complete, deterministic approval projections for configuration plans.

The projection is prepared once from the immutable normalized before/after
configuration and persisted with the operation.  Approval rendering consumes
that record directly; it never reconstructs meaning from mutable Home
Assistant state.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from ..sanitization import sanitize_untrusted_data
from .normalize import stable_hash


SEMANTIC_PROJECTION_SCHEMA_VERSION = 1
# These are explicit configuration-plan product boundaries, not presentation
# clipping limits. The aggregate stays below routing.MAX_MCP_OUTCOME_CAPTURE_BYTES
# (1,100,000), leaving response-envelope margin. Every one of the eight
# supported operations can consume its entire per-operation budget without
# failing the aggregate plan boundary.
MAX_SEMANTIC_PROJECTION_BYTES_PER_OPERATION = 131_072
MAX_SEMANTIC_PROJECTION_BYTES_PER_PLAN = (
    MAX_SEMANTIC_PROJECTION_BYTES_PER_OPERATION * 8
    # Canonical JSON framing for an eight-item list: two brackets and seven
    # commas. This ensures eight individually valid maximum-size projections
    # also satisfy the aggregate product boundary.
    + 9
)

_ABSENT = object()
_CHANGE_TYPES = frozenset({"added", "modified", "removed"})
_SNAPSHOT_STATES = frozenset({"absent", "redacted", "value"})


@dataclass(frozen=True)
class SemanticProjectionError(ValueError):
    """Stable internal reason for a non-reviewable proposal or record."""

    reason: str

    def __str__(self) -> str:
        return self.reason


def canonical_projection_bytes(value: Any) -> bytes:
    """Serialize projection material without incidental formatting authority."""

    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise SemanticProjectionError(
            "projection_serialization_failed"
        ) from exc


def projection_hash(projection: dict[str, Any]) -> str:
    """Hash a projection while excluding only its self-referential digest."""

    value = deepcopy(projection)
    binding = value.get("binding")
    if not isinstance(binding, dict):
        raise SemanticProjectionError("projection_binding_malformed")
    binding.pop("projection_hash", None)
    return hashlib.sha256(canonical_projection_bytes(value)).hexdigest()


def _json_equal(left: Any, right: Any) -> bool:
    if left is _ABSENT or right is _ABSENT:
        return left is right
    return canonical_projection_bytes(left) == canonical_projection_bytes(
        right
    )


def _pointer(segments: tuple[str | int, ...]) -> str:
    if not segments:
        return "/"
    encoded = []
    for segment in segments:
        text = str(segment).replace("~", "~0").replace("/", "~1")
        encoded.append(text)
    return "/" + "/".join(encoded)


def _review_snapshot(
    value: Any,
    *,
    path: tuple[str | int, ...],
    known_secrets: tuple[str, ...],
) -> dict[str, Any]:
    if value is _ABSENT:
        return {"state": "absent"}

    # Preserve key-aware redaction even though the recursive diff projects one
    # leaf at a time.  The raw value is never included when a sensitive field
    # or free-text secret detector fires.
    last_key = next(
        (segment for segment in reversed(path) if isinstance(segment, str)),
        "value",
    )
    inspected = sanitize_untrusted_data(
        {last_key: value},
        known_secrets=known_secrets,
    )
    if inspected.failed_closed:
        raise SemanticProjectionError("projection_sanitization_failed")
    if inspected.redaction_applied:
        categories = list(inspected.redaction_categories)
        if not categories:
            raise SemanticProjectionError("projection_redaction_malformed")
        return {"state": "redacted", "categories": categories}

    # Strict serialization rejects non-finite or otherwise nondeterministic
    # values before any plan can become externally approvable.
    canonical_projection_bytes(value)
    return {"state": "value", "value": deepcopy(value)}


def _append_changes(
    destination: list[dict[str, Any]],
    before: Any,
    after: Any,
    *,
    path: tuple[str | int, ...],
    known_secrets: tuple[str, ...],
) -> None:
    if _json_equal(before, after):
        return

    if isinstance(before, dict) or isinstance(after, dict):
        if before is _ABSENT:
            before_mapping: dict[str, Any] = {}
        elif isinstance(before, dict):
            before_mapping = before
        else:
            before_mapping = {}
        if after is _ABSENT:
            after_mapping: dict[str, Any] = {}
        elif isinstance(after, dict):
            after_mapping = after
        else:
            after_mapping = {}
        if (
            before is _ABSENT or isinstance(before, dict)
        ) and (
            after is _ABSENT or isinstance(after, dict)
        ):
            if any(
                not isinstance(key, str)
                for key in set(before_mapping) | set(after_mapping)
            ):
                raise SemanticProjectionError(
                    "projection_mapping_key_invalid"
                )
            keys = sorted(
                set(before_mapping) | set(after_mapping),
                key=lambda item: item.encode("utf-8"),
            )
            if keys:
                for key in keys:
                    _append_changes(
                        destination,
                        before_mapping.get(key, _ABSENT),
                        after_mapping.get(key, _ABSENT),
                        path=(*path, key),
                        known_secrets=known_secrets,
                    )
                return
            # An empty container added or removed still carries meaning.
            if before is not _ABSENT and after is not _ABSENT:
                return

    if isinstance(before, list) or isinstance(after, list):
        if before is _ABSENT:
            before_list: list[Any] = []
        elif isinstance(before, list):
            before_list = before
        else:
            before_list = []
        if after is _ABSENT:
            after_list: list[Any] = []
        elif isinstance(after, list):
            after_list = after
        else:
            after_list = []
        if (
            before is _ABSENT or isinstance(before, list)
        ) and (
            after is _ABSENT or isinstance(after, list)
        ):
            length = max(len(before_list), len(after_list))
            if length:
                for index in range(length):
                    _append_changes(
                        destination,
                        (
                            before_list[index]
                            if index < len(before_list)
                            else _ABSENT
                        ),
                        (
                            after_list[index]
                            if index < len(after_list)
                            else _ABSENT
                        ),
                        path=(*path, index),
                        known_secrets=known_secrets,
                    )
                return
            if before is not _ABSENT and after is not _ABSENT:
                return

    before_snapshot = _review_snapshot(
        before, path=path, known_secrets=known_secrets
    )
    after_snapshot = _review_snapshot(
        after, path=path, known_secrets=known_secrets
    )
    before_state = before_snapshot["state"]
    after_state = after_snapshot["state"]
    change_type = (
        "added"
        if before_state == "absent"
        else "removed"
        if after_state == "absent"
        else "modified"
    )
    destination.append(
        {
            "change_index": len(destination),
            "path": _pointer(path),
            "change_type": change_type,
            "before": before_snapshot,
            "after": after_snapshot,
            "sensitive_value_redacted": (
                before_state == "redacted" or after_state == "redacted"
            ),
        }
    )


def build_semantic_projection(
    operation: Any,
    *,
    policy_class: str,
    physical_impact: str,
    known_secrets: tuple[str, ...] = (),
) -> tuple[dict[str, Any], str]:
    """Create the complete persisted projection for one prepared operation."""

    before = operation.normalized_current_config
    after = operation.normalized_proposed_config
    if operation.action == "update" and before is None:
        raise SemanticProjectionError("projection_before_state_missing")
    if operation.action == "create" and before is not None:
        raise SemanticProjectionError("projection_create_state_present")
    if not isinstance(after, dict):
        raise SemanticProjectionError("projection_after_state_malformed")

    changes: list[dict[str, Any]] = []
    _append_changes(
        changes,
        _ABSENT if before is None else before,
        after,
        path=(),
        known_secrets=known_secrets,
    )
    redacted_count = sum(
        item["sensitive_value_redacted"] for item in changes
    )
    projection = {
        "projection_schema_version": SEMANTIC_PROJECTION_SCHEMA_VERSION,
        "projection_complete": True,
        "operation_index": operation.order,
        "operation_id": operation.operation_id,
        "operation_type": operation.action,
        "resource": {
            "resource_type": operation.resource_type,
            "resource_subtype": operation.helper_type,
            "target_id": operation.target_id,
        },
        "risk": {
            "risk_classification": operation.risk.level.value,
            "policy_classification": policy_class,
            "physical_impact_classification": physical_impact,
        },
        "changes": changes,
        "redacted_change_count": redacted_count,
        "binding": {
            "current_state_fingerprint": (
                operation.current_state_fingerprint
            ),
            "prepared_config_hash": operation.proposed_config_hash,
            "raw_prepared_config_hash": stable_hash(
                operation.proposed_config
            ),
            "normalized_prepared_config_hash": stable_hash(
                operation.normalized_proposed_config
            ),
        },
    }
    digest = projection_hash(projection)
    projection["binding"]["projection_hash"] = digest
    if (
        len(canonical_projection_bytes(projection))
        > MAX_SEMANTIC_PROJECTION_BYTES_PER_OPERATION
    ):
        raise SemanticProjectionError("projection_size_limit_exceeded")
    return projection, digest


def _validate_snapshot(value: Any) -> None:
    if not isinstance(value, dict) or value.get("state") not in (
        _SNAPSHOT_STATES
    ):
        raise SemanticProjectionError("projection_snapshot_malformed")
    state = value["state"]
    if state == "absent":
        if set(value) != {"state"}:
            raise SemanticProjectionError("projection_snapshot_malformed")
        return
    if state == "redacted":
        categories = value.get("categories")
        if (
            set(value) != {"state", "categories"}
            or not isinstance(categories, list)
            or not categories
            or categories != sorted(set(categories))
            or any(
                not isinstance(item, str) or not item
                for item in categories
            )
        ):
            raise SemanticProjectionError("projection_snapshot_malformed")
        return
    if set(value) != {"state", "value"}:
        raise SemanticProjectionError("projection_snapshot_malformed")
    canonical_projection_bytes(value["value"])


def validate_semantic_projection(
    operation: Any,
    *,
    policy_class: str,
    physical_impact: str,
    known_secrets: tuple[str, ...] = (),
    recompute: bool = False,
) -> None:
    """Validate persisted projection identity without provider or HA access."""

    projection = operation.semantic_projection
    digest = operation.semantic_projection_hash
    if not isinstance(projection, dict) or not isinstance(digest, str):
        raise SemanticProjectionError("projection_missing")
    if set(projection) != {
        "projection_schema_version",
        "projection_complete",
        "operation_index",
        "operation_id",
        "operation_type",
        "resource",
        "risk",
        "changes",
        "redacted_change_count",
        "binding",
    }:
        raise SemanticProjectionError("projection_schema_malformed")
    if (
        projection["projection_schema_version"]
        != SEMANTIC_PROJECTION_SCHEMA_VERSION
    ):
        raise SemanticProjectionError("projection_schema_version_unsupported")
    if projection["projection_complete"] is not True:
        raise SemanticProjectionError("projection_incomplete")
    if (
        projection["operation_index"] != operation.order
        or projection["operation_id"] != operation.operation_id
        or projection["operation_type"] != operation.action
    ):
        raise SemanticProjectionError("projection_operation_mismatch")

    resource = projection["resource"]
    if not isinstance(resource, dict) or resource != {
        "resource_type": operation.resource_type,
        "resource_subtype": operation.helper_type,
        "target_id": operation.target_id,
    }:
        raise SemanticProjectionError("projection_target_mismatch")
    risk = projection["risk"]
    if not isinstance(risk, dict) or risk != {
        "risk_classification": operation.risk.level.value,
        "policy_classification": policy_class,
        "physical_impact_classification": physical_impact,
    }:
        raise SemanticProjectionError("projection_risk_mismatch")

    changes = projection["changes"]
    if not isinstance(changes, list):
        raise SemanticProjectionError("projection_changes_malformed")
    paths: set[str] = set()
    redacted_count = 0
    for index, change in enumerate(changes):
        if not isinstance(change, dict) or set(change) != {
            "change_index",
            "path",
            "change_type",
            "before",
            "after",
            "sensitive_value_redacted",
        }:
            raise SemanticProjectionError("projection_change_malformed")
        if (
            change["change_index"] != index
            or not isinstance(change["path"], str)
            or not change["path"].startswith("/")
            or change["path"] in paths
            or change["change_type"] not in _CHANGE_TYPES
            or not isinstance(change["sensitive_value_redacted"], bool)
        ):
            raise SemanticProjectionError("projection_change_malformed")
        paths.add(change["path"])
        _validate_snapshot(change["before"])
        _validate_snapshot(change["after"])
        sensitive = (
            change["before"]["state"] == "redacted"
            or change["after"]["state"] == "redacted"
        )
        if change["sensitive_value_redacted"] is not sensitive:
            raise SemanticProjectionError("projection_redaction_mismatch")
        redacted_count += int(sensitive)
    if projection["redacted_change_count"] != redacted_count:
        raise SemanticProjectionError("projection_redaction_mismatch")

    binding = projection["binding"]
    expected_binding = {
        "current_state_fingerprint": operation.current_state_fingerprint,
        "prepared_config_hash": operation.proposed_config_hash,
        "raw_prepared_config_hash": stable_hash(operation.proposed_config),
        "normalized_prepared_config_hash": stable_hash(
            operation.normalized_proposed_config
        ),
        "projection_hash": digest,
    }
    if not isinstance(binding, dict) or binding != expected_binding:
        raise SemanticProjectionError("projection_binding_mismatch")
    if projection_hash(projection) != digest:
        raise SemanticProjectionError("projection_hash_mismatch")
    if (
        len(canonical_projection_bytes(projection))
        > MAX_SEMANTIC_PROJECTION_BYTES_PER_OPERATION
    ):
        raise SemanticProjectionError("projection_size_limit_exceeded")

    if recompute:
        expected, expected_hash = build_semantic_projection(
            operation,
            policy_class=policy_class,
            physical_impact=physical_impact,
            known_secrets=known_secrets,
        )
        if expected != projection or expected_hash != digest:
            raise SemanticProjectionError("projection_semantic_mismatch")


def validate_projection_plan_size(operations: list[Any]) -> None:
    """Enforce the one explicit aggregate projection product boundary."""

    projections = [operation.semantic_projection for operation in operations]
    if any(not isinstance(item, dict) for item in projections):
        raise SemanticProjectionError("projection_missing")
    if (
        len(canonical_projection_bytes(projections))
        > MAX_SEMANTIC_PROJECTION_BYTES_PER_PLAN
    ):
        raise SemanticProjectionError("projection_size_limit_exceeded")
