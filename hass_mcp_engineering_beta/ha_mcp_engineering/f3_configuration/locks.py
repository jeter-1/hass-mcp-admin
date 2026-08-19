"""Pure F3 lock-set calculation for configuration operations."""

from __future__ import annotations

import re
from collections.abc import Iterable

from ha_mcp_engineering.f3.contracts import LockMode, LockRequest, LockScope

from ..dependency.extraction import (
    extract_document_with_obligations,
    valid_entity_id,
)
from ..governance.normalize import stable_hash
from .models import PreparedConfigurationOperation


_LOCK_KEY = re.compile(
    r"^[a-z][a-z0-9_]{0,63}:[a-z0-9][a-z0-9_.-]{0,255}$"
)
_NON_CAUSAL_AUTOMATION_RELATIONS = frozenset(
    {"action_data", "action_target", "service_target"}
)
UNCONSTRAINED_INPUT_BOOLEAN_DEPENDENCY = "input_boolean_dynamic"


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


def helper_dependency_lock_key(entity_id: str) -> str:
    """Return the exact causal-dependency lock for one input_boolean."""

    if (
        not valid_entity_id(entity_id)
        or not entity_id.startswith("input_boolean.")
    ):
        raise ValueError("helper dependency identity is invalid")
    key = f"helper_dependency:{entity_id}"
    _validate_key(key)
    return key


def unconstrained_helper_dependency_lock_key() -> str:
    key = (
        "helper_dependency:"
        + UNCONSTRAINED_INPUT_BOOLEAN_DEPENDENCY
    )
    _validate_key(key)
    return key


def _automation_helper_dependency_locks(
    operation: PreparedConfigurationOperation,
) -> tuple[LockRequest, ...]:
    if operation.resource_type != "automation":
        return ()
    exact_helpers: set[str] = set()
    unconstrained = False
    configurations = (
        operation.current_config(),
        operation.proposed_config(),
    )
    for index, config in enumerate(configurations):
        if config is None:
            continue
        findings, dynamic, obligations = extract_document_with_obligations(
            source_type="automation",
            source_id=f"lock_projection_{index}",
            source_entity_id=None,
            source_name=None,
            source_state=None,
            config=config,
            secret="",
        )
        exact_helpers.update(
            item.target_entity_id
            for item in findings
            if item.target_entity_id.startswith("input_boolean.")
            and item.relation not in _NON_CAUSAL_AUTOMATION_RELATIONS
        )
        for item in obligations:
            if (
                item.obligation_kind == "structured_entity_reference"
                and item.relation in _NON_CAUSAL_AUTOMATION_RELATIONS
            ):
                # Literal service targets/action data describe the action and
                # are not causal helper reads.  Template-derived obligations
                # at the same paths can read a helper and must participate in
                # exact or conservative dependency locking.
                continue
            exact_helpers.update(
                entity_id
                for entity_id in item.exact_entity_ids
                if entity_id.startswith("input_boolean.")
            )
            if item.outcome == "proven_dependency_neutral":
                continue
            if (
                item.outcome == "proven_target_exclusion"
                and not any(
                    entity_id.startswith("input_boolean.")
                    for entity_id in item.exact_entity_ids
                )
            ):
                continue
            domains = item.possible_entity_domains
            could_select_helper = bool(
                domains is None or "input_boolean" in domains
            )
            unconstrained = bool(
                unconstrained
                or item.outcome == "coverage_failure"
                or item.limit_exceeded
                # An opaque callable/filter/test may introduce an entity read
                # beyond the visible operand domain.  Domain hints can narrow
                # exact terminals, but cannot discharge semantic opacity.
                or item.outcome == "bounded_semantic_opaque"
                or (
                    item.outcome == "exact_dependency"
                    and not item.exact_entity_ids
                    and could_select_helper
                )
            )
    requests = [
        LockRequest(
            key=helper_dependency_lock_key(entity_id),
            scopes=(LockScope.RESOURCE,),
            mode=LockMode.EXCLUSIVE,
            reason_codes=(
                "automation_helper_dependency_mutation",
            ),
        )
        for entity_id in sorted(
            exact_helpers, key=lambda item: item.encode("utf-8")
        )
    ]
    if unconstrained:
        requests.append(
            LockRequest(
                key=unconstrained_helper_dependency_lock_key(),
                scopes=(LockScope.RESOURCE,),
                mode=LockMode.EXCLUSIVE,
                reason_codes=(
                    "unconstrained_helper_dependency_mutation",
                ),
            )
        )
    return tuple(requests)


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
            *_automation_helper_dependency_locks(operation),
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
