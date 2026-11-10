"""Approval policy for the entity registry rename step (HAMCP-295 section 3.1).

Josh decision (in-session 2026-11-08), item 8: elevated for actuators only,
the 069A E1 pattern.  Item 7: a plan-level elevated acknowledgement whenever
any identified reference is not rewritten.  Neither basis ever produces
``prohibited``; only an incomplete execution contract does.

The actuator set is the union of the two ``governance/risk.py`` frozensets
the 069A E1 classifier uses, imported rather than copied (C8).
"""

from __future__ import annotations

from typing import Any

from ..entity_ids import STRICT_ENTITY_ID
from .entity_rename import (
    ENTITY_RENAME_RESOURCE_TYPE,
    entity_domain,
)
from .models import (
    ApprovalPolicyClass,
    ChangePlan,
    ConfigurationOperation,
    PhysicalConsequence,
    RiskDelta,
)
from .risk import (
    _HELPER_DIRECT_EFFECT_DOMAINS,
    _HELPER_SAFETY_CRITICAL_DOMAINS,
    _HELPER_TRANSITIVE_EFFECT_DOMAINS,
)


ACTUATOR_DOMAINS = _HELPER_DIRECT_EFFECT_DOMAINS | _HELPER_TRANSITIVE_EFFECT_DOMAINS
SAFETY_CRITICAL_DOMAINS = _HELPER_SAFETY_CRITICAL_DOMAINS

ACTUATOR_BASIS = "e1_actuator_entity_rename"
NOT_REWRITTEN_BASIS = "entity_rename_references_not_rewritten"
ELEVATED_REASON = "entity_rename_elevated_policy"
STANDARD_REASON = "entity_rename_standard_policy"
INCOMPLETE_REASON = "entity_rename_execution_contract_incomplete"

EXECUTION_MODE_ORDERED = "ordered_stop_on_failure"
EXECUTION_MODE_DEPENDENCY_BOUND = "dependency_bound_compensating_v1"
EXECUTION_MODES = frozenset({EXECUTION_MODE_ORDERED, EXECUTION_MODE_DEPENDENCY_BOUND})
EXECUTION_MODE_NOT_CALLER_SELECTABLE = "execution_mode_not_caller_selectable"

# Holders a rename plan may rewrite (B1), with the resource types the
# existing governed adapters own.
REWRITE_RESOURCE_TYPES = frozenset({"automation", "script", "scene_configuration"})
MAX_PLAN_OPERATIONS = 8
MAX_DEPENDENT_REWRITES = MAX_PLAN_OPERATIONS - 1


def is_actuator_domain(domain: str) -> bool:
    return domain in ACTUATOR_DOMAINS


def caller_execution_mode_refusal(arguments: Any) -> str | None:
    """Return the refusal for a caller that supplies ``execution_mode``."""

    if isinstance(arguments, dict) and "execution_mode" in arguments:
        return EXECUTION_MODE_NOT_CALLER_SELECTABLE
    return None


def plan_contains_rename(plan: ChangePlan) -> bool:
    return any(
        operation.resource_type == ENTITY_RENAME_RESOURCE_TYPE
        for operation in plan.operations
    )


def _not_rewritten(operation: ConfigurationOperation) -> tuple[list[Any], int] | None:
    proposed = operation.proposed_config
    if not isinstance(proposed, dict):
        return None
    references = proposed.get("not_rewritten_references")
    count = proposed.get("not_rewritten_count")
    if (
        not isinstance(references, list)
        or type(count) is not int
        or count < 0
        or count != len(references)
    ):
        return None
    return references, count


def rename_plan_complete(plan: ChangePlan) -> bool:
    """Return whether a plan holding a rename has the one reviewed shape.

    Exactly one rename at order 0 with no dependencies, the dependency-bound
    mode, at most seven dependent rewrites of the B1 holder types that each
    depend on the rename, a plan-level not-rewritten set equal to the one the
    rename operation binds, and no other operation kind.
    """

    operations = sorted(plan.operations, key=lambda item: item.order)
    if (
        not operations
        or len(operations) > MAX_PLAN_OPERATIONS
        or getattr(plan, "execution_mode", None) != EXECUTION_MODE_DEPENDENCY_BOUND
    ):
        return False
    rename, *rewrites = operations
    if (
        rename.resource_type != ENTITY_RENAME_RESOURCE_TYPE
        or rename.order != 0
        or rename.depends_on
        or any(op.resource_type == ENTITY_RENAME_RESOURCE_TYPE for op in rewrites)
    ):
        return False
    for operation in rewrites:
        if (
            operation.resource_type not in REWRITE_RESOURCE_TYPES
            or operation.action != "update"
            or rename.operation_id not in operation.depends_on
        ):
            return False
    if any(getattr(op, "compensation", None) is None for op in operations):
        return False
    bound = _not_rewritten(rename)
    if bound is None:
        return False
    references, count = bound
    return (
        getattr(plan, "not_rewritten_references", None) == references
        and getattr(plan, "not_rewritten_count", None) == count
    )


def entity_rename_operation_policy(operation: ConfigurationOperation) -> Any:
    """Classify one rename step.  Returns ``OperationPolicyClassification``."""

    from .policy import OperationPolicyClassification

    incomplete = OperationPolicyClassification(
        ApprovalPolicyClass.PROHIBITED,
        RiskDelta.CRITICAL,
        PhysicalConsequence.UNKNOWN,
        (INCOMPLETE_REASON,),
    )
    proposed = operation.proposed_config
    old = operation.target_id
    new = proposed.get("new_entity_id") if isinstance(proposed, dict) else None
    bound = _not_rewritten(operation)
    if (
        operation.action != "update"
        or operation.helper_type is not None
        or not isinstance(old, str)
        or STRICT_ENTITY_ID.fullmatch(old) is None
        or not isinstance(new, str)
        or STRICT_ENTITY_ID.fullmatch(new) is None
        or old == new
        or entity_domain(old) != entity_domain(new)
        or proposed.get("entity_id") != old
        or not isinstance(operation.current_config, dict)
        or not isinstance(operation.current_state_fingerprint, str)
        or len(operation.current_state_fingerprint) != 64
        or not isinstance(operation.proposed_config_hash, str)
        or len(operation.proposed_config_hash) != 64
        or operation.risk.apply_allowed is not True
        or bound is None
    ):
        return incomplete
    _references, count = bound
    domain = entity_domain(old)
    bases: set[str] = set()
    if is_actuator_domain(domain):
        bases.add(ACTUATOR_BASIS)
    if count > 0:
        bases.add(NOT_REWRITTEN_BASIS)
    if not bases:
        return OperationPolicyClassification(
            ApprovalPolicyClass.STANDARD_ADMIN,
            RiskDelta.LOW,
            PhysicalConsequence.NONE,
            (STANDARD_REASON, "supported_configuration_change"),
        )
    if ACTUATOR_BASIS in bases:
        risk = RiskDelta.HIGH
        consequence = (
            PhysicalConsequence.SAFETY_CRITICAL
            if domain in SAFETY_CRITICAL_DOMAINS
            else PhysicalConsequence.INDIRECT
        )
    else:
        risk = RiskDelta.MODERATE
        consequence = PhysicalConsequence.NONE
    return OperationPolicyClassification(
        ApprovalPolicyClass.ELEVATED_ADMIN,
        risk,
        consequence,
        tuple(sorted({*bases, ELEVATED_REASON, "supported_configuration_change"})),
    )


def policy_subject_extension(plan: ChangePlan) -> dict[str, Any]:
    """Return the plan-level rename keys bound into the policy subject.

    Empty for every plan that does not carry the dependency-bound mode, so
    every existing policy subject hash is unchanged.
    """

    mode = getattr(plan, "execution_mode", None)
    if mode is None:
        return {}
    return {
        "execution_mode": mode,
        "not_rewritten_references": getattr(plan, "not_rewritten_references", None),
        "not_rewritten_count": getattr(plan, "not_rewritten_count", None),
        "compensation": [
            {
                "operation_id": operation.operation_id,
                "compensation": getattr(operation, "compensation", None),
            }
            for operation in sorted(plan.operations, key=lambda item: item.order)
        ],
    }
