"""Complete bounded approval projection for declared dashboard patch units."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from ..sanitization import sanitize_untrusted_data
from .constants import (
    CANONICAL_OPERATION_ID,
    MAX_DASHBOARD_APPROVAL_PROJECTION_BYTES,
    MAX_PATCH_OPERATIONS,
    MIN_PATCH_OPERATIONS,
    SHA256,
)
from .errors import ApprovalProjectionError
from .json_codec import canonical_json_bytes, engineering_sha256, validate_json_value
from .models import PatchCompilation, PatchKind
from .patch import parse_pointer


APPROVAL_PROJECTION_MODEL = "f3-dashboard-approval-projection-v1"
_PROJECTION_KEYS = frozenset(
    {"model", "complete", "operation_count", "operations", "binding"}
)
_OPERATION_KEYS = frozenset(
    {"operation_id", "operation", "path", "previous", "proposed"}
)
_BINDING_KEYS = frozenset(
    {
        "preread_sha256",
        "canonical_patch_sha256",
        "resulting_sha256",
        "projection_sha256",
    }
)


def _failure(
    message: str,
    *,
    reason: str,
    constraint: str | None = None,
    observed: int | None = None,
    limit: int | None = None,
) -> ApprovalProjectionError:
    return ApprovalProjectionError(
        message,
        reason=reason,
        constraint=constraint,
        observed=observed,
        limit=limit,
        stage="review_projection",
    )


def _snapshot(*, present: bool, value: Any) -> dict[str, Any]:
    if not present:
        return {"state": "absent"}
    return {"state": "value", "value": deepcopy(value)}


def _hash_projection(projection: Mapping[str, Any]) -> str:
    material = deepcopy(dict(projection))
    binding = material.get("binding")
    if not isinstance(binding, dict):
        raise _failure(
            "Dashboard approval binding is malformed",
            reason="approval_projection_malformed",
            constraint="projection_binding",
        )
    binding.pop("projection_sha256", None)
    return engineering_sha256(material)


def _validate_snapshot(snapshot: Any) -> str:
    if not isinstance(snapshot, dict):
        raise _failure(
            "Dashboard approval snapshot is malformed",
            reason="approval_projection_malformed",
            constraint="operation_snapshot",
        )
    state = snapshot.get("state")
    if state == "absent" and set(snapshot) == {"state"}:
        return state
    if state != "value" or set(snapshot) != {"state", "value"}:
        raise _failure(
            "Dashboard approval snapshot is incomplete",
            reason="approval_projection_incomplete",
            constraint="operation_snapshot",
        )
    try:
        validate_json_value(snapshot["value"])
    except ValueError as exc:
        raise _failure(
            "Dashboard approval snapshot is not strict JSON data",
            reason="approval_projection_malformed",
            constraint="operation_snapshot",
        ) from exc
    return state


def build_dashboard_approval_projection(
    compilation: PatchCompilation,
    *,
    known_secrets: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Project complete declared values without exposing unrelated content."""

    if len(compilation.operations) != len(compilation.effects):
        raise _failure(
            "Dashboard approval projection cannot represent every operation",
            reason="approval_projection_incomplete",
            constraint="operation_count",
        )
    operations: list[dict[str, Any]] = []
    for operation, effect in zip(compilation.operations, compilation.effects):
        if (
            operation.operation_id != effect.operation_id
            or operation.operation is not effect.operation
            or operation.path != effect.path
        ):
            raise _failure(
                "Dashboard approval operation binding drifted",
                reason="approval_projection_binding_mismatch",
                constraint="operation_identity",
            )
        operations.append(
            {
                "operation_id": effect.operation_id,
                "operation": effect.operation.value,
                "path": effect.path,
                "previous": _snapshot(
                    present=effect.previous_present,
                    value=effect.previous_value,
                ),
                "proposed": _snapshot(
                    present=effect.proposed_present,
                    value=effect.proposed_value,
                ),
            }
        )
    projection: dict[str, Any] = {
        "model": APPROVAL_PROJECTION_MODEL,
        "complete": True,
        "operation_count": len(operations),
        "operations": operations,
        "binding": {
            "preread_sha256": compilation.preread_sha256,
            "canonical_patch_sha256": compilation.canonical_patch_sha256,
            "resulting_sha256": compilation.resulting_sha256,
        },
    }
    projection["binding"]["projection_sha256"] = _hash_projection(projection)
    validate_dashboard_approval_projection(
        projection,
        known_secrets=known_secrets,
        expected_preread_sha256=compilation.preread_sha256,
        expected_patch_sha256=compilation.canonical_patch_sha256,
        expected_resulting_sha256=compilation.resulting_sha256,
    )
    return projection


def validate_dashboard_approval_projection(
    projection: Any,
    *,
    known_secrets: tuple[str, ...] = (),
    expected_preread_sha256: str | None = None,
    expected_patch_sha256: str | None = None,
    expected_resulting_sha256: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Validate completeness, exact binding, safety, and the review bound."""

    if not isinstance(projection, dict) or set(projection) != _PROJECTION_KEYS:
        raise _failure(
            "Dashboard approval projection is malformed",
            reason="approval_projection_malformed",
            constraint="projection_shape",
        )
    if (
        projection.get("model") != APPROVAL_PROJECTION_MODEL
        or projection.get("complete") is not True
    ):
        raise _failure(
            "Dashboard approval projection is incomplete",
            reason="approval_projection_incomplete",
            constraint="projection_identity",
        )
    operations = projection.get("operations")
    operation_count = projection.get("operation_count")
    if (
        not isinstance(operations, list)
        or not isinstance(operation_count, int)
        or isinstance(operation_count, bool)
        or operation_count != len(operations)
        or not MIN_PATCH_OPERATIONS <= operation_count <= MAX_PATCH_OPERATIONS
    ):
        raise _failure(
            "Dashboard approval operation count is incomplete",
            reason="approval_projection_incomplete",
            constraint="operation_count",
        )
    validated: list[dict[str, Any]] = []
    identities: set[str] = set()
    paths: set[tuple[str, ...]] = set()
    for row in operations:
        if not isinstance(row, dict) or set(row) != _OPERATION_KEYS:
            raise _failure(
                "Dashboard approval operation is malformed",
                reason="approval_projection_malformed",
                constraint="operation_shape",
            )
        operation_id = row.get("operation_id")
        if (
            not isinstance(operation_id, str)
            or not CANONICAL_OPERATION_ID.fullmatch(operation_id)
            or operation_id in identities
        ):
            raise _failure(
                "Dashboard approval operation identity is malformed",
                reason="approval_projection_malformed",
                constraint="operation_identity",
            )
        try:
            kind = PatchKind(row.get("operation"))
        except (TypeError, ValueError) as exc:
            raise _failure(
                "Dashboard approval operation type is malformed",
                reason="approval_projection_malformed",
                constraint="operation_type",
            ) from exc
        path = row.get("path")
        try:
            tokens = parse_pointer(path, operation=kind)
        except ValueError as exc:
            raise _failure(
                "Dashboard approval operation path is malformed",
                reason="approval_projection_malformed",
                constraint="operation_path",
            ) from exc
        if tokens in paths:
            raise _failure(
                "Dashboard approval operation paths are duplicated",
                reason="approval_projection_malformed",
                constraint="operation_path",
            )
        previous_state = _validate_snapshot(row.get("previous"))
        proposed_state = _validate_snapshot(row.get("proposed"))
        expected_states = {
            PatchKind.ADD: ("absent", "value"),
            PatchKind.REPLACE: ("value", "value"),
            PatchKind.REMOVE: ("value", "absent"),
        }[kind]
        if (previous_state, proposed_state) != expected_states:
            raise _failure(
                "Dashboard approval operation is semantically incomplete",
                reason="approval_projection_incomplete",
                constraint="operation_snapshot",
            )
        identities.add(operation_id)
        paths.add(tokens)
        validated.append(row)

    binding = projection.get("binding")
    if not isinstance(binding, dict) or set(binding) != _BINDING_KEYS:
        raise _failure(
            "Dashboard approval binding is malformed",
            reason="approval_projection_malformed",
            constraint="projection_binding",
        )
    for name in _BINDING_KEYS:
        value = binding.get(name)
        if not isinstance(value, str) or not SHA256.fullmatch(value):
            raise _failure(
                "Dashboard approval binding digest is malformed",
                reason="approval_projection_malformed",
                constraint="projection_binding",
            )
    expected_bindings = {
        "preread_sha256": expected_preread_sha256,
        "canonical_patch_sha256": expected_patch_sha256,
        "resulting_sha256": expected_resulting_sha256,
    }
    if any(
        expected is not None and binding[name] != expected
        for name, expected in expected_bindings.items()
    ):
        raise _failure(
            "Dashboard approval projection binding does not match the plan",
            reason="approval_projection_binding_mismatch",
            constraint="projection_binding",
        )
    if binding["projection_sha256"] != _hash_projection(projection):
        raise _failure(
            "Dashboard approval projection hash is invalid",
            reason="approval_projection_binding_mismatch",
            constraint="projection_hash",
        )

    sanitation = sanitize_untrusted_data(
        projection,
        known_secrets=known_secrets,
    )
    if sanitation.failed_closed or sanitation.redaction_applied:
        raise _failure(
            "Dashboard approval projection contains protected data",
            reason="approval_projection_contains_protected_data",
            constraint="projection_content",
        )
    encoded_size = len(canonical_json_bytes(projection))
    if encoded_size > MAX_DASHBOARD_APPROVAL_PROJECTION_BYTES:
        raise _failure(
            "Dashboard approval projection exceeds its complete review bound",
            reason="approval_projection_too_large",
            constraint="approval_projection_bytes",
            observed=encoded_size,
            limit=MAX_DASHBOARD_APPROVAL_PROJECTION_BYTES,
        )
    return tuple(validated)
