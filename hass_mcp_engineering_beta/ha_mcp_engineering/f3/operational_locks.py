"""Pure complete lock graph for runtime-inert F3 operational adapters."""

from __future__ import annotations

from collections.abc import Iterable

from ha_mcp_engineering.f3.contracts import (
    HA_MCP_PROVIDER_LOCK_KEY,
    LockMode,
    LockRequest,
    LockScope,
)

from ..f3_configuration.locks import (
    helper_dependency_lock_key,
    resource_lock_key,
    unconstrained_helper_dependency_lock_key,
)
from ..governance.helper_dependency import (
    HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS,
    HELPER_DEPENDENCY_RISK_EXECUTION_MODELS,
    INPUT_BOOLEAN_DEPENDENCY_MODEL,
    MAX_RELEVANT_AUTOMATIONS,
)
from ..governance.input_select_dependency import (
    INPUT_SELECT_DEPENDENCY_COMPATIBLE_MODELS,
    INPUT_SELECT_DEPENDENCY_EXECUTION_MODELS,
    INPUT_SELECT_DEPENDENCY_MODEL_V1,
)
from .models import validate_lock_key
from .operational_models import (
    CONTROLLED_RELOAD,
    CREATE_FULL_BACKUP,
    RESTART_ADDON,
    RESTART_HOME_ASSISTANT,
    SET_INPUT_BOOLEAN_STATE,
    SET_INPUT_SELECT_OPTION,
    HELPER_VALUE_OPERATIONS,
    PreparedOperationalOperation,
)


_SHIPPED_HELPER_DEPENDENCY_RISK_V2 = "helper-dependency-risk-v2"
# HAMCP-285: input_boolean models before the target-scoped v14 reader.
_UNFENCED_INPUT_BOOLEAN_MODELS = HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS - {
    INPUT_BOOLEAN_DEPENDENCY_MODEL
}


def _bound_downstream_automation_resources(
    operation: PreparedOperationalOperation,
    *,
    accepted_models: frozenset[str] = (
        HELPER_DEPENDENCY_RISK_EXECUTION_MODELS
    ),
) -> tuple[str, ...]:
    baseline = operation.baseline
    binding = baseline.get("dependency_risk")
    if not isinstance(binding, dict) or (
        binding.get("model") not in accepted_models
    ):
        # Lock projection is execution authority.  A superseded binding is
        # readable and recoverable, but it cannot decide which resources this
        # dispatch must hold, so it fails closed and requires a replan.
        raise ValueError("helper dependency lock evidence is not executable")
    values = binding.get("downstream_automation_resource_ids")
    if (
        not isinstance(values, list)
        or len(values) > MAX_RELEVANT_AUTOMATIONS
        or any(not isinstance(value, str) for value in values)
        or values != sorted(set(values), key=lambda item: item.encode("utf-8"))
    ):
        raise ValueError("helper dependency lock resources are invalid")
    for value in values:
        resource_lock_key("automation", value)
    if binding.get("model") == _SHIPPED_HELPER_DEPENDENCY_RISK_V2:
        # Beta 37 and Beta 38 persisted this bounded resource list and used it
        # directly to calculate the complete helper lock graph.  The
        # dependency_lock_projection field did not exist until v3.  Recovery
        # may reconstruct that shipped graph after a durable intent, but v2
        # remains excluded from fresh execution authority above.
        if "dependency_lock_projection" in binding:
            raise ValueError("helper dependency v2 lock evidence is invalid")
        return tuple(values)
    projection = binding.get("dependency_lock_projection")
    if not isinstance(projection, dict):
        raise ValueError("helper dependency lock projection is invalid")
    if (
        projection.get("exact_helper_dependency") is not True
        or not isinstance(
            projection.get("conservative_helper_dependency"), bool
        )
        or projection.get("automation_resource_ids") != values
        or not isinstance(projection.get("custom_template_reload"), bool)
    ):
        raise ValueError("helper dependency lock projection is invalid")
    return tuple(values)


def _requires_custom_template_reload_lock(
    operation: PreparedOperationalOperation,
    *,
    accepted_models: frozenset[str] = (
        HELPER_DEPENDENCY_RISK_EXECUTION_MODELS
    ),
) -> bool:
    binding = operation.baseline.get("dependency_risk")
    if not isinstance(binding, dict) or (
        binding.get("model") not in accepted_models
    ):
        raise ValueError("helper dependency lock evidence is not executable")
    if binding.get("model") == _SHIPPED_HELPER_DEPENDENCY_RISK_V2:
        if "dependency_lock_projection" in binding:
            raise ValueError("helper dependency v2 lock evidence is invalid")
        # Shipped v2 did not model or acquire a custom-template reload lock.
        return False
    projection = binding.get("dependency_lock_projection")
    if not isinstance(projection, dict):
        raise ValueError("helper dependency lock projection is invalid")
    return projection.get("custom_template_reload") is True


def _requires_coarse_automation_fence(
    operation: PreparedOperationalOperation,
) -> bool:
    """A target-scoped projection must state its fence.

    HAMCP-274 introduced the fence for input_select v2; HAMCP-285 extends it
    to input_boolean v14.  Every earlier helper model never modelled it.
    """

    binding = operation.baseline.get("dependency_risk")
    if not isinstance(binding, dict):
        raise ValueError("helper dependency lock projection is invalid")
    if binding.get("model") in _UNFENCED_INPUT_BOOLEAN_MODELS:
        # Recovery reconstructs the shipped graph of a superseded binding;
        # the v2 binding carries no projection at all.
        projection = binding.get("dependency_lock_projection")
        if isinstance(projection, dict) and (
            "coarse_automation_fence" in projection
        ):
            raise ValueError("helper dependency lock evidence is invalid")
        return False
    projection = binding.get("dependency_lock_projection")
    if not isinstance(projection, dict):
        raise ValueError("helper dependency lock projection is invalid")
    if binding.get("model") == INPUT_SELECT_DEPENDENCY_MODEL_V1:
        # v1 never modelled the fence; recovery reconstructs its shipped graph.
        if "coarse_automation_fence" in projection:
            raise ValueError("input_select v1 lock evidence is invalid")
        return False
    fence = projection.get("coarse_automation_fence")
    if not isinstance(fence, bool):
        raise ValueError("helper dependency lock projection is invalid")
    return fence


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
        return self._calculate(
            operation,
            accepted_models=HELPER_DEPENDENCY_RISK_EXECUTION_MODELS,
        )

    def calculate_for_readback(
        self, operation: PreparedOperationalOperation
    ) -> tuple[LockRequest, ...]:
        """Reconstruct shipped lock evidence for post-intent recovery only."""

        return self._calculate(
            operation,
            accepted_models=HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS,
        )

    def _calculate(
        self,
        operation: PreparedOperationalOperation,
        *,
        accepted_models: frozenset[str],
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
        if operation.operation not in HELPER_VALUE_OPERATIONS:
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
            if (
                operation.target.target_id
                == operation.authoritative_provider_slug
            ):
                requests.append(
                    LockRequest(
                        key=HA_MCP_PROVIDER_LOCK_KEY,
                        scopes=(LockScope.PROVIDER,),
                        mode=LockMode.EXCLUSIVE,
                        reason_codes=("selected_upstream_provider_restart",),
                    )
                )
        elif operation.operation in HELPER_VALUE_OPERATIONS:
            if operation.operation == SET_INPUT_SELECT_OPTION:
                # HAMCP-145: only the input_select reader's models have lock
                # authority for this operation.  HAMCP-274: v2 alone executes;
                # recovery may reconstruct a shipped v1 graph.
                domain = "input_select"
                mutation_reason = "exact_input_select_option_mutation"
                accepted_models = (
                    INPUT_SELECT_DEPENDENCY_EXECUTION_MODELS
                    if accepted_models == HELPER_DEPENDENCY_RISK_EXECUTION_MODELS
                    else INPUT_SELECT_DEPENDENCY_COMPATIBLE_MODELS
                )
            else:
                domain = "input_boolean"
                mutation_reason = "exact_input_boolean_state_mutation"
            downstream_automations = (
                _bound_downstream_automation_resources(
                    operation,
                    accepted_models=accepted_models,
                )
            )
            requests.extend(
                (
                    LockRequest(
                        key=resource_lock_key(
                            domain, operation.target.target_id
                        ),
                        scopes=(LockScope.RESOURCE,),
                        mode=LockMode.EXCLUSIVE,
                        reason_codes=(mutation_reason,),
                    ),
                    LockRequest(
                        key=f"reload:{domain}",
                        scopes=(LockScope.RESOURCE,),
                        mode=LockMode.SHARED,
                        reason_codes=(
                            "matching_configuration_reload_dependency",
                        ),
                    ),
                    LockRequest(
                        key="reload:automation",
                        scopes=(LockScope.RESOURCE,),
                        mode=LockMode.SHARED,
                        reason_codes=(
                            "bound_automation_reload_dependency",
                        ),
                    ),
                    LockRequest(
                        key=helper_dependency_lock_key(
                            operation.target.target_id
                        ),
                        scopes=(LockScope.RESOURCE,),
                        mode=LockMode.SHARED,
                        reason_codes=(
                            "exact_helper_dependency_stability",
                        ),
                    ),
                    # This shared key is an unconditional execution stability
                    # fence, not a claim that the plan contains opaque or
                    # coverage-failed evidence.  An unresolved automation
                    # mutation takes the same key exclusively, preventing it
                    # from introducing a helper dependency after the final
                    # fenced refresh and before dispatch/readback completes.
                    LockRequest(
                        key=unconstrained_helper_dependency_lock_key(),
                        scopes=(LockScope.RESOURCE,),
                        mode=LockMode.SHARED,
                        reason_codes=(
                            "unconstrained_helper_dependency_stability",
                        ),
                    ),
                )
            )
            requests.extend(
                LockRequest(
                    key=resource_lock_key("automation", resource_id),
                    scopes=(LockScope.RESOURCE,),
                    mode=LockMode.SHARED,
                    reason_codes=(
                        "bound_downstream_automation_dependency",
                    ),
                )
                for resource_id in downstream_automations
            )
            if _requires_custom_template_reload_lock(
                operation,
                accepted_models=accepted_models,
            ):
                requests.append(
                    LockRequest(
                        key="reload:custom_templates",
                        scopes=(LockScope.RESOURCE,),
                        mode=LockMode.SHARED,
                        reason_codes=(
                            "bound_external_template_dependency",
                        ),
                    )
                )
            if _requires_coarse_automation_fence(operation):
                # Every automation configuration mutation and automation
                # reload holds this key, so exclusive mode fences the whole
                # automation domain when the exact consumer set is not
                # lockable one by one.  Over-locking only.
                requests.append(
                    LockRequest(
                        key="reload:automation",
                        scopes=(LockScope.RESOURCE,),
                        mode=LockMode.EXCLUSIVE,
                        reason_codes=(
                            "coarse_automation_dependency_fence",
                        ),
                    )
                )
        elif operation.operation != RESTART_HOME_ASSISTANT:
            raise ValueError("unknown operational lock model")
        return normalize_operational_lock_requests(requests)
