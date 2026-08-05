"""Pure F3 lock-set calculation for configuration operations."""

from __future__ import annotations

import re
from collections.abc import Iterable

from ha_mcp_engineering.f3.contracts import LockMode, LockRequest, LockScope

from ..governance.normalize import stable_hash
from .models import PreparedConfigurationOperation


_LOCK_KEY = re.compile(
    r"^[a-z][a-z0-9_]{0,63}:[a-z0-9][a-z0-9_.-]{0,255}$"
)


def resource_lock_key(resource_type: str, target_id: str) -> str:
    """Map exact target identity to the frozen lower-case lock namespace."""

    if resource_type == "automation":
        prefix = "automation"
    elif resource_type == "script":
        prefix = "script"
    elif resource_type in {"input_boolean", "input_number"}:
        prefix = "helper"
    else:
        raise ValueError("configuration resource type has no lock identity")
    key = f"{prefix}:{target_id.lower()}"
    _validate_key(key)
    return key


def operation_lock_requests(
    operation: PreparedConfigurationOperation,
) -> tuple[LockRequest, ...]:
    """Return the exact resource, matching reload, and core lock graph."""

    reload_key = {
        "automation": "reload:automation",
        "script": "reload:script",
        "input_boolean": "reload:input_boolean",
        "input_number": "reload:input_number",
    }.get(operation.resource_type)
    if reload_key is None:
        raise ValueError("configuration resource type has no reload lock")

    return normalize_lock_requests(
        (
            LockRequest(
                key=resource_lock_key(
                    operation.resource_type, operation.target.target_id
                ),
                scopes=(LockScope.RESOURCE,),
                mode=LockMode.EXCLUSIVE,
                reason_codes=("configuration_target_mutation",),
            ),
            LockRequest(
                key=reload_key,
                scopes=(LockScope.RESOURCE,),
                mode=LockMode.SHARED,
                reason_codes=("matching_configuration_reload_dependency",),
            ),
            LockRequest(
                key="home_assistant:core",
                scopes=(LockScope.RESOURCE,),
                mode=LockMode.SHARED,
                reason_codes=("home_assistant_availability_dependency",),
            ),
        )
    )


def complete_configuration_lock_set(
    operations: tuple[PreparedConfigurationOperation, ...]
    | list[PreparedConfigurationOperation],
) -> tuple[LockRequest, ...]:
    """Calculate the complete 1-8 operation lock union before dispatch."""

    if not 1 <= len(operations) <= 8:
        raise ValueError("configuration sequence must contain 1 to 8 operations")
    operation_ids = [operation.operation_id for operation in operations]
    if len(operation_ids) != len(set(operation_ids)):
        raise ValueError("configuration operation IDs must be unique")
    targets = [
        resource_lock_key(
            operation.resource_type, operation.target.target_id
        )
        for operation in operations
    ]
    if len(targets) != len(set(targets)):
        raise ValueError("configuration targets must be unique")
    return normalize_lock_requests(
        request
        for operation in operations
        for request in operation_lock_requests(operation)
    )


def normalize_lock_requests(
    requests: Iterable[LockRequest],
) -> tuple[LockRequest, ...]:
    """Union duplicate evidence, apply exclusive dominance, and byte-sort."""

    merged: dict[
        str, tuple[set[LockScope], LockMode, set[str]]
    ] = {}
    for request in requests:
        _validate_key(request.key)
        if not request.scopes or not request.reason_codes:
            raise ValueError("lock evidence must not be empty")
        if any(not isinstance(scope, LockScope) for scope in request.scopes):
            raise ValueError("lock scope is invalid")
        if not isinstance(request.mode, LockMode):
            raise ValueError("lock mode is invalid")
        if any(not _valid_reason(code) for code in request.reason_codes):
            raise ValueError("lock reason code is invalid")
        scopes, mode, reasons = merged.get(
            request.key,
            (set(), LockMode.SHARED, set()),
        )
        scopes.update(request.scopes)
        reasons.update(request.reason_codes)
        if request.mode == LockMode.EXCLUSIVE:
            mode = LockMode.EXCLUSIVE
        merged[request.key] = scopes, mode, reasons

    result = []
    for key in sorted(merged, key=lambda item: item.encode("utf-8")):
        scopes, mode, reasons = merged[key]
        result.append(
            LockRequest(
                key=key,
                scopes=tuple(
                    sorted(scopes, key=lambda item: item.value.encode("utf-8"))
                ),
                mode=mode,
                reason_codes=tuple(
                    sorted(reasons, key=lambda item: item.encode("utf-8"))
                ),
            )
        )
    return tuple(result)


def lock_set_hash(requests: Iterable[LockRequest]) -> str:
    normalized = normalize_lock_requests(requests)
    return stable_hash(
        [
            {
                "key": request.key,
                "scopes": [scope.value for scope in request.scopes],
                "mode": request.mode.value,
                "reason_codes": list(request.reason_codes),
            }
            for request in normalized
        ]
    )


def _validate_key(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) > 320
        or value.count(":") != 1
        or value != value.lower()
        or not _LOCK_KEY.fullmatch(value)
    ):
        raise ValueError("lock key is not canonical")


def _valid_reason(value: str) -> bool:
    return bool(
        isinstance(value, str)
        and 1 <= len(value) <= 96
        and value == value.lower()
        and value[0].isalpha()
        and value.replace("_", "a").isalnum()
    )
