"""Pure complete lock graph for runtime-inert F3 operational adapters."""

from __future__ import annotations

from collections.abc import Iterable

from ha_mcp_engineering.f3.contracts import LockMode, LockRequest, LockScope

from .models import validate_lock_key
from .operational_models import (
    CONTROLLED_RELOAD,
    CREATE_FULL_BACKUP,
    RESTART_ADDON,
    RESTART_HOME_ASSISTANT,
    SET_INPUT_BOOLEAN_STATE,
    PreparedOperationalOperation,
)


def normalize_operational_lock_requests(
    requests: Iterable[LockRequest],
) -> tuple[LockRequest, ...]:
    """Union canonical evidence, apply exclusive dominance, and byte-sort."""

    merged: dict[str, tuple[set[LockScope], LockMode, set[str]]] = {}
    for request in requests:
        validate_lock_key(request.key)
        if not request.scopes or not request.reason_codes:
            raise ValueError("lock evidence must not be empty")
        if any(not isinstance(scope, LockScope) for scope in request.scopes):
            raise ValueError("lock scope is invalid")
        if not isinstance(request.mode, LockMode):
            raise ValueError("lock mode is invalid")
        scopes, mode, reasons = merged.get(
            request.key, (set(), LockMode.SHARED, set())
        )
        scopes.update(request.scopes)
        reasons.update(request.reason_codes)
        if request.mode == LockMode.EXCLUSIVE:
            mode = LockMode.EXCLUSIVE
        merged[request.key] = scopes, mode, reasons
    if not merged:
        raise ValueError("complete operational lock set is empty")
    return tuple(
        LockRequest(
            key=key,
            scopes=tuple(
                sorted(
                    merged[key][0],
                    key=lambda scope: scope.value.encode("utf-8"),
                )
            ),
            mode=merged[key][1],
            reason_codes=tuple(
                sorted(merged[key][2], key=lambda code: code.encode("utf-8"))
            ),
        )
        for key in sorted(merged, key=lambda value: value.encode("utf-8"))
    )


class OperationalLockSetCalculator:
    """Calculate every target and dependency key before final preflight."""

    def calculate(
        self, operation: PreparedOperationalOperation
    ) -> tuple[LockRequest, ...]:
        operation.validate()
        requests: list[LockRequest] = [
            LockRequest(
                key="home_assistant:core",
                scopes=(LockScope.RESOURCE,),
                mode=(
                    LockMode.EXCLUSIVE
                    if operation.operation == RESTART_HOME_ASSISTANT
                    else LockMode.SHARED
                ),
                reason_codes=(
                    "home_assistant_core_mutation"
                    if operation.operation == RESTART_HOME_ASSISTANT
                    else "home_assistant_availability_dependency",
                ),
            ),
        ]
        if operation.operation != SET_INPUT_BOOLEAN_STATE:
            requests.append(
                LockRequest(
                    key=f"addon:{operation.authoritative_provider_slug}",
                    scopes=(LockScope.PROVIDER,),
                    mode=LockMode.SHARED,
                    reason_codes=("upstream_provider_dependency",),
                )
            )
        if operation.operation == CREATE_FULL_BACKUP:
            requests.append(
                LockRequest(
                    key="backup:local_full_backup",
                    scopes=(LockScope.RESOURCE,),
                    mode=LockMode.EXCLUSIVE,
                    reason_codes=("full_backup_mutation",),
                )
            )
        elif operation.operation == CONTROLLED_RELOAD:
            requests.append(
                LockRequest(
                    key=f"reload:{operation.target.target_id}",
                    scopes=(LockScope.RESOURCE,),
                    mode=LockMode.EXCLUSIVE,
                    reason_codes=("configuration_domain_reload",),
                )
            )
        elif operation.operation == RESTART_ADDON:
            requests.append(
                LockRequest(
                    key=f"addon:{operation.target.target_id}",
                    scopes=(LockScope.RESOURCE,),
                    mode=LockMode.EXCLUSIVE,
                    reason_codes=("installed_addon_restart",),
                )
            )
        elif operation.operation == SET_INPUT_BOOLEAN_STATE:
            requests.append(
                LockRequest(
                    key=f"input_boolean:{operation.target.target_id}",
                    scopes=(LockScope.RESOURCE,),
                    mode=LockMode.EXCLUSIVE,
                    reason_codes=("exact_input_boolean_state_mutation",),
                )
            )
        elif operation.operation != RESTART_HOME_ASSISTANT:
            raise ValueError("unknown operational lock model")
        return normalize_operational_lock_requests(requests)
