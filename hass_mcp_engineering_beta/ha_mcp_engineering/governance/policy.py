"""Deterministic F2 policy classification for immutable change plans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .models import (
    ApprovalActionKind,
    ApprovalPolicyClass,
    ChangeOperation,
    ChangePlan,
    ChangePolicyDecision,
    ConfigurationOperation,
    PhysicalConsequence,
    RiskDelta,
    RiskLevel,
)
from .normalize import stable_hash
from .risk import SAFETY_CRITICAL_SERVICES
from .configuration_eligibility import bound_proof, local_member_marked, markers
from . import configuration_rollback_eligibility as inverse_eligibility
from .physical_effects import (
    MAX_ACKNOWLEDGED_OBJECTS,
    VERDICT_EQUIVALENT,
    VERDICT_NONE,
    compare_operation,
    plan_acknowledgement_binding,
)
from .entity_metadata import ENTITY_METADATA_RESOURCE_TYPE
from .entity_metadata_planning import POLICY_TRIGGER as ENTITY_METADATA_POLICY_TRIGGER
from .entity_availability_exposure import (
    BINDINGS_KEY as AVAILABILITY_EXPOSURE_BINDINGS_KEY,
    RESOURCE_TYPES as AVAILABILITY_EXPOSURE_RESOURCE_TYPES,
    exposure_value,
    proposed_errors as availability_exposure_proposed_errors,
)
from .entity_availability_exposure_planning import (
    MAX_ENTITIES as AVAILABILITY_EXPOSURE_MAX_ENTITIES,
    POLICY_TRIGGER as AVAILABILITY_EXPOSURE_POLICY_TRIGGER,
)
from .configuration_delete import (
    ACTION as CONFIGURATION_DELETE_ACTION,
    INTENT_KEY as CONFIGURATION_DELETE_INTENT_KEY,
    RESOURCE_TYPES as CONFIGURATION_DELETE_RESOURCE_TYPES,
    intent_errors as configuration_delete_intent_errors,
)
from .configuration_delete_planning import (
    MAX_DELETIONS as CONFIGURATION_DELETE_MAX_TARGETS,
    POLICY_TRIGGER as CONFIGURATION_DELETE_POLICY_TRIGGER,
    expected_basis as configuration_delete_expected_basis,
)


POLICY_VERSION = "f2-v3"
# f2-v3 (HAMCP-213) adds the protected physical-effect comparison and the
# safety-critical owner acknowledgement. Exact f2-v1 and f2-v2 snapshots
# remain recognizable review evidence; they never acquire f2-v3 authority.
F2_V2_POLICY_VERSION = "f2-v2"
COMPATIBLE_POLICY_VERSIONS = frozenset(
    {"f2-v1", F2_V2_POLICY_VERSION, POLICY_VERSION}
)

_RISK_RANK = {
    RiskDelta.NONE: 0,
    RiskDelta.LOW: 1,
    RiskDelta.MODERATE: 2,
    RiskDelta.HIGH: 3,
    RiskDelta.CRITICAL: 4,
}
_CONSEQUENCE_RANK = {
    PhysicalConsequence.NONE: 0,
    PhysicalConsequence.INDIRECT: 1,
    PhysicalConsequence.UNKNOWN: 2,
    PhysicalConsequence.DIRECT: 3,
    PhysicalConsequence.SAFETY_CRITICAL: 4,
}
_POLICY_RANK = {
    ApprovalPolicyClass.STANDARD_ADMIN: 0,
    ApprovalPolicyClass.ELEVATED_ADMIN: 1,
    ApprovalPolicyClass.PROHIBITED: 2,
}

_SAFETY_CRITICAL_TRIGGERS = frozenset(
    {
        "sensitive_entity_domain",
        "sensitive_blueprint_input",
        "garage_cover_target",
        "safety_critical_service",
        "water_control_target",
    }
)
_DIRECT_CONSEQUENCE_TRIGGERS = frozenset(
    {
        "high_risk_service",
        "unrestricted_target",
        "large_target_set",
    }
)
_UNCLASSIFIABLE_TRIGGERS = frozenset(
    {
        "unresolved_dynamic_service",
        "unresolved_dynamic_target",
        "unresolved_action_structure",
    }
)

_REVIEWED_CONDITION_FAMILIES = frozenset(
    {
        "and",
        "device",
        "not",
        "numeric_state",
        "or",
        "state",
        "sun",
        "template",
        "time",
        "trigger",
        "zone",
    }
)
_CONDITION_CONTROL_FAMILIES = frozenset({"and", "not", "or"})
_EXECUTABLE_CONDITION_KEYS = frozenset(
    {
        "action",
        "actions",
        "choose",
        "else",
        "parallel",
        "repeat",
        "sequence",
        "service",
        "then",
    }
)
_NON_BEHAVIORAL_AUTOMATION_KEYS = frozenset({"alias", "description"})


@dataclass(frozen=True)
class OperationPolicyClassification:
    policy_class: ApprovalPolicyClass
    risk_delta: RiskDelta
    physical_consequence: PhysicalConsequence
    reason_codes: tuple[str, ...]
    safety_critical_acknowledgement: bool = False


def _risk_delta(level: RiskLevel) -> RiskDelta:
    return {
        RiskLevel.LOW: RiskDelta.LOW,
        RiskLevel.MEDIUM: RiskDelta.MODERATE,
        RiskLevel.HIGH: RiskDelta.HIGH,
    }[level]


def _risk_triggers(operation: ConfigurationOperation) -> set[str]:
    return {
        str(item.get("trigger"))
        for item in operation.risk.evidence
        if isinstance(item, dict) and isinstance(item.get("trigger"), str)
    }


def _risk_services(operation: ConfigurationOperation) -> set[str]:
    return {
        str(item.get("service"))
        for item in operation.risk.evidence
        if isinstance(item, dict)
        and item.get("trigger") in {
            "high_risk_service",
            "safety_critical_service",
        }
        and isinstance(item.get("service"), str)
        and item.get("service") in SAFETY_CRITICAL_SERVICES
    }


def _exact_existing_automation_update(
    operation: ConfigurationOperation,
    *,
    include_scripts: bool = False,
) -> bool:
    """Recognize the first exact typed configuration-write authority.

    Action targets inside the stored automation may remain semantically
    uncertain. They are not the configuration provider's dispatch target:
    that target is the exact existing automation bound by normalized before
    and after configurations, optimistic state, validation, and a typed
    writer/readback contract.
    """

    return bool(
        (
            operation.resource_type == "automation"
            or (include_scripts and operation.resource_type == "script")
        )
        and operation.action == "update"
        and isinstance(operation.current_config, dict)
        and isinstance(operation.proposed_config, dict)
        and isinstance(operation.normalized_current_config, dict)
        and isinstance(operation.normalized_proposed_config, dict)
        and isinstance(operation.target_id, str)
        and bool(operation.target_id)
        and isinstance(operation.current_state_fingerprint, str)
        and len(operation.current_state_fingerprint) == 64
        and isinstance(operation.proposed_config_hash, str)
        and len(operation.proposed_config_hash) == 64
        and operation.normalization_version >= 1
        and operation.validation_results.get("valid") is True
        and operation.risk.apply_allowed
    )


def _exact_configuration_delete(operation: ConfigurationOperation) -> bool:
    """Recognize an exact governed delete of one automation or script (HAMCP-155).

    Exact means: the captured pre-image is the bound current state, the
    proposal is the closed delete intent naming that pre-image, and the
    proposal hash binds the intent. The F3 delete adapter re-reads the
    target, refuses drift, and proves absence by readback.
    """

    from .resources import resource_fingerprint

    proposed = operation.proposed_config
    if (
        operation.resource_type not in CONFIGURATION_DELETE_RESOURCE_TYPES
        or operation.action != CONFIGURATION_DELETE_ACTION
        or operation.helper_type is not None
        or not isinstance(operation.target_id, str)
        or not operation.target_id
        or not isinstance(operation.current_config, dict)
        or not isinstance(operation.normalized_current_config, dict)
        or configuration_delete_intent_errors(
            operation.resource_type, operation.target_id, proposed
        )
        or operation.normalized_proposed_config != proposed
    ):
        return False
    try:
        pre_image = resource_fingerprint(
            operation.resource_type, operation.current_config
        )
    except (TypeError, ValueError, OverflowError):
        return False
    intent = proposed[CONFIGURATION_DELETE_INTENT_KEY]
    return bool(
        operation.current_state_fingerprint == pre_image
        and intent["pre_image_fingerprint"] == pre_image
        and operation.proposed_config_hash == stable_hash(proposed)
        and operation.normalization_version >= 1
        and operation.validation_results.get("valid") is True
        and operation.risk.apply_allowed
    )


def _exact_configuration_write(operation: ConfigurationOperation) -> bool:
    """Recognize an exact automation or script update, create or delete (D3).

    A create is exact when the identifier is new (no current configuration)
    and the proposed configuration is validated, normalized, and hash bound;
    the writer checks the identifier conflict and reads the result back.
    A delete is exact only through :func:`_exact_configuration_delete`.
    """

    if _exact_existing_automation_update(operation, include_scripts=True):
        return True
    if _exact_configuration_delete(operation):
        return True
    return bool(
        operation.resource_type in {"automation", "script"}
        and operation.action == "create"
        and operation.current_config is None
        and operation.normalized_current_config is None
        and isinstance(operation.proposed_config, dict)
        and isinstance(operation.normalized_proposed_config, dict)
        and isinstance(operation.target_id, str)
        and bool(operation.target_id)
        and isinstance(operation.current_state_fingerprint, str)
        and len(operation.current_state_fingerprint) == 64
        and isinstance(operation.proposed_config_hash, str)
        and len(operation.proposed_config_hash) == 64
        and operation.normalization_version >= 1
        and operation.validation_results.get("valid") is True
        and operation.risk.apply_allowed
    )


def _safety_critical_comparison_policy(
    operation: ConfigurationOperation,
    *,
    triggers: set[str],
    safety_critical_services: set[str],
    risk_delta: RiskDelta,
) -> OperationPolicyClassification | None:
    """Map the D2 comparison to policy (HAMCP-213 D3); ``None`` = not protected.

    The comparison selects only which owner decision is required. It never
    removes the exact execution contract: an incomplete contract stays
    prohibited.
    """

    if operation.resource_type not in {"automation", "script"}:
        return None
    comparison = compare_operation(operation)
    risk_protected = bool(
        triggers & _SAFETY_CRITICAL_TRIGGERS or safety_critical_services
    )
    if comparison.verdict == VERDICT_NONE:
        if risk_protected:
            # The two classifiers disagree about a protected object; the
            # comparison cannot be trusted, so fail closed.
            return OperationPolicyClassification(
                ApprovalPolicyClass.PROHIBITED,
                risk_delta,
                PhysicalConsequence.SAFETY_CRITICAL,
                (
                    "safety_critical_effect_comparison_mismatch",
                    "supported_configuration_change",
                ),
            )
        return None
    if not _exact_configuration_write(operation):
        return OperationPolicyClassification(
            ApprovalPolicyClass.PROHIBITED,
            risk_delta,
            PhysicalConsequence.SAFETY_CRITICAL,
            (
                "safety_critical_execution_contract_incomplete",
                "supported_configuration_change",
            ),
        )
    if not comparison.acknowledgement_required:
        if comparison.verdict != VERDICT_EQUIVALENT:
            # Every non-equivalent comparison names acknowledgement rows.
            return OperationPolicyClassification(
                ApprovalPolicyClass.PROHIBITED,
                risk_delta,
                PhysicalConsequence.SAFETY_CRITICAL,
                ("safety_critical_effect_comparison_mismatch",),
            )
        # OD-5: an equivalent comparison keeps the elevated review page with
        # plan approval only; its delta comes from the non-effect changes.
        return OperationPolicyClassification(
            ApprovalPolicyClass.ELEVATED_ADMIN,
            min(risk_delta, RiskDelta.MODERATE, key=lambda item: _RISK_RANK[item]),
            PhysicalConsequence.SAFETY_CRITICAL,
            (
                "safety_critical_effect_equivalent",
                "supported_configuration_change",
            ),
        )
    reasons = {
        "safety_critical_owner_acknowledgement_required",
        "supported_configuration_change",
        f"safety_critical_effect_{comparison.verdict}",
    }
    if safety_critical_services:
        reasons.add("unlock_or_disarm_effect")
    return OperationPolicyClassification(
        ApprovalPolicyClass.ELEVATED_ADMIN,
        max(risk_delta, RiskDelta.HIGH, key=lambda item: _RISK_RANK[item]),
        PhysicalConsequence.SAFETY_CRITICAL,
        tuple(sorted(reasons)),
        safety_critical_acknowledgement=True,
    )


def _exact_existing_dashboard_update(plan: ChangePlan) -> bool:
    """Recognize a fresh typed dashboard write with complete execution authority.

    Dashboard action semantics remain consequence evidence. They do not alter
    the exact provider target, compiled full configuration, optimistic
    baseline, lock graph, one-dispatch ownership, or authoritative reread.
    """

    operational = plan.operational
    baseline = (
        operational.baseline
        if operational is not None
        and isinstance(operational.baseline, dict)
        else {}
    )
    capability = (
        operational.provider_capability_evidence
        if operational is not None
        and isinstance(operational.provider_capability_evidence, dict)
        else {}
    )
    identity_value = baseline.get("dashboard_operational_identity")
    proposal = plan.proposed_config.get("dashboard_update")
    projection = (
        proposal.get("approval_projection")
        if isinstance(proposal, dict)
        else None
    )
    try:
        from ..f3_dashboard.approval_projection import (
            validate_dashboard_approval_projection,
        )
        from ..f3_dashboard.identity import operational_identity_from_mapping

        identity = operational_identity_from_mapping(identity_value)
        validate_dashboard_approval_projection(
            projection,
            expected_preread_sha256=baseline.get(
                "current_engineering_sha256"
            ),
            expected_patch_sha256=baseline.get("canonical_patch_sha256"),
            expected_resulting_sha256=baseline.get(
                "resulting_engineering_sha256"
            ),
        )
    except (TypeError, ValueError):
        return False
    return bool(
        plan.operation == ChangeOperation.UPDATE_DASHBOARD
        and plan.contract_version == 3
        and plan.target.target_type == "dashboard"
        and isinstance(plan.target.target_id, str)
        and bool(plan.target.target_id)
        and plan.risk.apply_allowed
        and plan.validation_results.get("valid") is True
        and plan.validation_results.get("storage_mode_confirmed") is True
        and plan.validation_results.get("exact_provider_contract_admitted")
        is True
        and plan.validation_results.get("approval_projection_complete") is True
        and operational is not None
        and operational.family == "dashboard_update"
        and operational.operation == ChangeOperation.UPDATE_DASHBOARD.value
        and operational.requested_name == plan.target.target_id
        and operational.provider == "upstream_dashboard"
        and capability.get("tool") == "ha_config_set_dashboard"
        and baseline.get("storage_mode_confirmed") is True
        and baseline.get("non_atomic") is True
        and identity.target_url_path == plan.target.target_id
        and identity.authority.compatibility_entry
        == capability.get("compatibility_entry")
        and identity.authority.setter_contract_hash
        == capability.get("provider_contract_hash")
        and identity.evidence_hash
        == baseline.get("dashboard_provider_identity_hash")
        and isinstance(baseline.get("approval_projection_sha256"), str)
        and isinstance(projection, dict)
        and isinstance(projection.get("binding"), dict)
        and projection["binding"].get("projection_sha256")
        == baseline["approval_projection_sha256"]
    )


def _contains_executable_condition_key(value: Any) -> bool:
    if isinstance(value, dict):
        if _EXECUTABLE_CONDITION_KEYS.intersection(value):
            return True
        return any(
            _contains_executable_condition_key(item)
            for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_executable_condition_key(item) for item in value)
    return False


def _reviewed_condition_guard(value: Any, *, depth: int = 0) -> bool:
    """Recognize one bounded condition that can only narrow top-level AND.

    This is deliberately a policy proof, not a replacement for Home
    Assistant configuration validation.  Unknown condition families,
    disabled guards, action-like directives, and excessively nested boolean
    structures are excluded from the retained-effect exception.
    """

    if depth > 4 or not isinstance(value, dict):
        return False
    family = value.get("condition")
    if (
        not isinstance(family, str)
        or family not in _REVIEWED_CONDITION_FAMILIES
        or value.get("enabled", True) is not True
        or _contains_executable_condition_key(value)
    ):
        return False
    if family not in _CONDITION_CONTROL_FAMILIES:
        return True
    children = value.get("conditions")
    return bool(
        isinstance(children, list)
        and children
        and all(
            _reviewed_condition_guard(child, depth=depth + 1)
            for child in children
        )
    )


def _retained_safety_critical_effect_is_non_risk_increasing(
    operation: ConfigurationOperation,
    *,
    triggers: set[str],
    safety_critical_services: set[str],
) -> bool:
    """Prove the narrow reviewed-existing-effect policy exception.

    The exact normalized action/control-flow graph and every other behavioral
    top-level field must be unchanged.  The only behavioral delta permitted is
    appending one or more reviewed conditions to Home Assistant's top-level
    conjunctive condition list.  Existing conditions remain an exact prefix,
    so the proposal cannot make the retained effect run in any state where it
    did not already run.  The proof permits strict narrowing or behavioral
    neutrality; it does not prove that an appended guard is effective in any
    reachable runtime state.
    """

    if (
        operation.resource_type != "automation"
        or operation.action != "update"
        or not isinstance(operation.normalized_current_config, dict)
        or not isinstance(operation.normalized_proposed_config, dict)
        or operation.risk.warnings
        or triggers.intersection(_UNCLASSIFIABLE_TRIGGERS)
        or not (
            triggers.intersection(_SAFETY_CRITICAL_TRIGGERS)
            or safety_critical_services
        )
    ):
        return False

    current = operation.normalized_current_config
    proposed = operation.normalized_proposed_config
    if "use_blueprint" in current or "use_blueprint" in proposed:
        return False
    if (
        not isinstance(current.get("action"), list)
        or not current["action"]
        or current.get("action") != proposed.get("action")
    ):
        return False

    ignored = {*_NON_BEHAVIORAL_AUTOMATION_KEYS, "condition"}
    for key in set(current).union(proposed).difference(ignored):
        if (
            (key in current) != (key in proposed)
            or current.get(key) != proposed.get(key)
        ):
            return False

    current_conditions = current.get("condition", [])
    proposed_conditions = proposed.get("condition", [])
    if (
        not isinstance(current_conditions, list)
        or not isinstance(proposed_conditions, list)
        or len(proposed_conditions) <= len(current_conditions)
        or proposed_conditions[: len(current_conditions)]
        != current_conditions
    ):
        return False
    return all(
        _reviewed_condition_guard(value)
        for value in proposed_conditions[len(current_conditions) :]
    )


_ENTITY_METADATA_ELEVATED_CONSEQUENCES = frozenset(
    {
        PhysicalConsequence.INDIRECT,
        PhysicalConsequence.DIRECT,
        PhysicalConsequence.SAFETY_CRITICAL,
    }
)


def _entity_metadata_operation_policy(
    operation: ConfigurationOperation,
) -> OperationPolicyClassification:
    """Classify one entity registry metadata update (HAMCP-069A-1 §5).

    Only an incomplete execution contract is prohibited; a disclosed
    physical consequence elevates the plan and is never prohibited alone.
    """

    incomplete = OperationPolicyClassification(
        ApprovalPolicyClass.PROHIBITED,
        RiskDelta.CRITICAL,
        PhysicalConsequence.UNKNOWN,
        ("entity_metadata_execution_contract_incomplete",),
    )
    markers_found = [
        item
        for item in operation.risk.evidence
        if isinstance(item, dict)
        and item.get("trigger") == ENTITY_METADATA_POLICY_TRIGGER
    ]
    if (
        operation.action != "update"
        or operation.helper_type is not None
        or not isinstance(operation.target_id, str)
        or not operation.target_id
        or not isinstance(operation.current_config, dict)
        or not isinstance(operation.proposed_config, dict)
        or not isinstance(operation.current_state_fingerprint, str)
        or len(operation.current_state_fingerprint) != 64
        or not isinstance(operation.proposed_config_hash, str)
        or len(operation.proposed_config_hash) != 64
        or operation.risk.apply_allowed is not True
        or len(markers_found) != 1
    ):
        return incomplete
    marker = markers_found[0]
    policy = marker.get("policy")
    try:
        consequence = PhysicalConsequence(marker.get("physical_consequence"))
    except ValueError:
        return incomplete
    basis = marker.get("basis")
    basis_codes = (
        tuple(item for item in basis.split(",") if item)
        if isinstance(basis, str)
        else ()
    )
    if (
        policy == "standard"
        and operation.risk.level is RiskLevel.LOW
        and consequence is PhysicalConsequence.NONE
        and not basis_codes
    ):
        return OperationPolicyClassification(
            ApprovalPolicyClass.STANDARD_ADMIN,
            RiskDelta.LOW,
            PhysicalConsequence.NONE,
            (
                "entity_metadata_standard_policy",
                "supported_configuration_change",
            ),
        )
    if (
        policy == "elevated"
        and operation.risk.level is RiskLevel.HIGH
        and consequence in _ENTITY_METADATA_ELEVATED_CONSEQUENCES
        and basis_codes
    ):
        return OperationPolicyClassification(
            ApprovalPolicyClass.ELEVATED_ADMIN,
            RiskDelta.HIGH,
            consequence,
            tuple(
                sorted(
                    {
                        *basis_codes,
                        "entity_metadata_elevated_policy",
                        "supported_configuration_change",
                    }
                )
            ),
        )
    return incomplete


AVAILABILITY_EXPOSURE_INCOMPLETE = (
    "entity_availability_exposure_execution_contract_incomplete"
)


def _availability_exposure_expected_basis(
    operation: ConfigurationOperation,
) -> tuple[str, ...]:
    """Recompute the OD-5 basis from the bound evidence, not the marker."""

    bindings = operation.proposed_config[AVAILABILITY_EXPOSURE_BINDINGS_KEY]
    kind = bindings["operation"]
    protection = bindings["protection"]
    basis: list[str] = []
    if kind == "expose" and protection["object_reasons"]:
        basis.append("expose_protected_object")
    elif kind == "disable":
        if protection["object_reasons"]:
            basis.append("disable_protected_object")
        if protection["dependency_reasons"]:
            basis.append("disable_protected_dependency")
    elif (
        kind == "enable"
        and protection["object_reasons"]
        and any(
            exposure_value(operation.current_config, assistant)["should_expose"]
            is True
            for assistant in (
                "cloud.alexa", "cloud.google_assistant", "conversation",
            )
        )
    ):
        basis.append("enable_exposed_protected_object")
    return tuple(basis)


def _availability_exposure_operation_policy(
    operation: ConfigurationOperation,
) -> OperationPolicyClassification:
    """Classify one entity enable/disable or exposure update (HAMCP-069B, OD-5).

    The marker must agree exactly with the basis recomputed from the bound
    protection evidence; any disagreement or gap is prohibited as incomplete.
    """

    incomplete = OperationPolicyClassification(
        ApprovalPolicyClass.PROHIBITED,
        RiskDelta.CRITICAL,
        PhysicalConsequence.UNKNOWN,
        (AVAILABILITY_EXPOSURE_INCOMPLETE,),
    )
    markers_found = [
        item
        for item in operation.risk.evidence
        if isinstance(item, dict)
        and item.get("trigger") == AVAILABILITY_EXPOSURE_POLICY_TRIGGER
    ]
    if (
        operation.action != "update"
        or operation.helper_type is not None
        or not isinstance(operation.target_id, str)
        or not operation.target_id
        or not isinstance(operation.current_config, dict)
        or not isinstance(operation.proposed_config, dict)
        or availability_exposure_proposed_errors(
            operation.resource_type, operation.target_id, operation.proposed_config
        )
        or not isinstance(operation.current_state_fingerprint, str)
        or len(operation.current_state_fingerprint) != 64
        or not isinstance(operation.proposed_config_hash, str)
        or len(operation.proposed_config_hash) != 64
        or operation.risk.apply_allowed is not True
        or len(markers_found) != 1
    ):
        return incomplete
    marker = markers_found[0]
    basis = marker.get("basis")
    if not isinstance(basis, str):
        return incomplete
    basis_codes = tuple(item for item in basis.split(",") if item)
    if basis_codes != _availability_exposure_expected_basis(operation):
        return incomplete
    policy = marker.get("policy")
    consequence = marker.get("physical_consequence")
    if (
        not basis_codes
        and policy == "standard"
        and consequence == PhysicalConsequence.NONE.value
        and operation.risk.level is RiskLevel.LOW
    ):
        return OperationPolicyClassification(
            ApprovalPolicyClass.STANDARD_ADMIN,
            RiskDelta.LOW,
            PhysicalConsequence.NONE,
            (
                "entity_availability_exposure_standard_policy",
                "supported_configuration_change",
            ),
        )
    if (
        basis_codes
        and policy == "elevated"
        and consequence == PhysicalConsequence.SAFETY_CRITICAL.value
        and operation.risk.level is RiskLevel.HIGH
    ):
        return OperationPolicyClassification(
            ApprovalPolicyClass.ELEVATED_ADMIN,
            RiskDelta.HIGH,
            PhysicalConsequence.SAFETY_CRITICAL,
            tuple(
                sorted(
                    {
                        *basis_codes,
                        "entity_availability_exposure_elevated_policy",
                        "supported_configuration_change",
                    }
                )
            ),
        )
    return incomplete


def _availability_exposure_plan_complete(operations) -> bool:
    """One 069B type, one operation and assistant, at most 8 children.

    An enable plan also binds exactly one config entry (OD-7).
    """

    types = {op.resource_type for op in operations}
    if len(types) != 1 or not types <= set(AVAILABILITY_EXPOSURE_RESOURCE_TYPES):
        return False
    if not 1 <= len(operations) <= AVAILABILITY_EXPOSURE_MAX_ENTITIES:
        return False
    bindings = []
    for op in operations:
        proposed = op.proposed_config
        value = (
            proposed.get(AVAILABILITY_EXPOSURE_BINDINGS_KEY)
            if isinstance(proposed, dict)
            else None
        )
        if not isinstance(value, dict):
            return False
        bindings.append(value)
    if len({op.target_id for op in operations}) != len(operations):
        return False
    if len({(item.get("operation"), item.get("assistant")) for item in bindings}) != 1:
        return False
    if bindings[0].get("operation") == "enable":
        entries = {
            op.current_config.get("config_entry_id")
            if isinstance(op.current_config, dict)
            else ()
            for op in operations
        }
        if len(entries) != 1:
            return False
    return True


CONFIGURATION_DELETE_INCOMPLETE = "configuration_delete_contract_incomplete"


def _configuration_delete_operation_policy(
    operation: ConfigurationOperation,
) -> OperationPolicyClassification:
    """Classify one governed delete by the effects and dependents it removes.

    The marker written at planning must agree exactly with the basis
    recomputed here from the immutable plan: the protected-effect comparison
    of the pre-image against absence, and the dependency reasons bound in
    the hashed intent. A delete is never "no effect" because it is not a
    create or update; any disagreement or gap is prohibited as incomplete.
    """

    incomplete = OperationPolicyClassification(
        ApprovalPolicyClass.PROHIBITED,
        RiskDelta.CRITICAL,
        PhysicalConsequence.UNKNOWN,
        (CONFIGURATION_DELETE_INCOMPLETE,),
    )
    markers_found = [
        item
        for item in operation.risk.evidence
        if isinstance(item, dict)
        and item.get("trigger") == CONFIGURATION_DELETE_POLICY_TRIGGER
    ]
    if not _exact_configuration_delete(operation) or len(markers_found) != 1:
        return incomplete
    comparison = compare_operation(operation)
    if comparison.bounds_exceeded:
        return _review_bounds_exceeded(
            RiskDelta.HIGH, unlock_or_disarm=comparison.unlock_or_disarm
        )
    intent = operation.proposed_config[CONFIGURATION_DELETE_INTENT_KEY]
    expected = configuration_delete_expected_basis(
        comparison, intent["dependency_reasons"]
    )
    marker = markers_found[0]
    basis = marker.get("basis")
    if not isinstance(basis, str):
        return incomplete
    if tuple(item for item in basis.split(",") if item) != expected:
        return incomplete
    policy = marker.get("policy")
    consequence = marker.get("physical_consequence")
    if (
        not expected
        and policy == "standard"
        and consequence == PhysicalConsequence.NONE.value
        and operation.risk.level is RiskLevel.MEDIUM
    ):
        return OperationPolicyClassification(
            ApprovalPolicyClass.STANDARD_ADMIN,
            RiskDelta.MODERATE,
            PhysicalConsequence.NONE,
            (
                "configuration_delete_standard_policy",
                "supported_configuration_change",
            ),
        )
    if not (
        expected
        and policy == "elevated"
        and consequence == PhysicalConsequence.SAFETY_CRITICAL.value
        and operation.risk.level is RiskLevel.HIGH
    ):
        return incomplete
    reasons = {
        *expected,
        "configuration_delete_elevated_policy",
        "supported_configuration_change",
    }
    acknowledgement = False
    if comparison.protected:
        # The existing HAMCP-213 mapping over the removed rows: elevated
        # plus the per-object safety-critical acknowledgement.
        protected = _safety_critical_comparison_policy(
            operation,
            triggers=set(),
            safety_critical_services=set(),
            risk_delta=RiskDelta.HIGH,
        )
        if (
            protected is None
            or protected.policy_class is not ApprovalPolicyClass.ELEVATED_ADMIN
        ):
            return protected or incomplete
        reasons.update(protected.reason_codes)
        acknowledgement = protected.safety_critical_acknowledgement
    if comparison.unlock_or_disarm:
        reasons.add("unlock_or_disarm_effect")
    return OperationPolicyClassification(
        ApprovalPolicyClass.ELEVATED_ADMIN,
        RiskDelta.HIGH,
        PhysicalConsequence.SAFETY_CRITICAL,
        tuple(sorted(reasons)),
        safety_critical_acknowledgement=acknowledgement,
    )


def _configuration_delete_plan_complete(operations) -> bool:
    """A delete plan holds only governed deletes: 1-8 unique, unordered."""

    if not 1 <= len(operations) <= CONFIGURATION_DELETE_MAX_TARGETS:
        return False
    if any(
        op.action != CONFIGURATION_DELETE_ACTION
        or op.resource_type not in CONFIGURATION_DELETE_RESOURCE_TYPES
        or op.depends_on
        for op in operations
    ):
        return False
    return len({(op.resource_type, op.target_id) for op in operations}) == len(
        operations
    )


SAFETY_CRITICAL_REVIEW_BOUNDS_EXCEEDED = "safety_critical_review_bounds_exceeded"
SPLIT_THE_CHANGE = "too_many_protected_objects_to_review_split_the_change"


def _review_bounds_exceeded(
    risk_delta: RiskDelta, *, unlock_or_disarm: bool
) -> OperationPolicyClassification:
    """Prohibit an incomplete safety-critical review (HAMCP-213 review H3)."""

    return OperationPolicyClassification(
        ApprovalPolicyClass.PROHIBITED,
        max(risk_delta, RiskDelta.HIGH, key=lambda item: _RISK_RANK[item]),
        PhysicalConsequence.SAFETY_CRITICAL,
        tuple(
            sorted(
                {
                    SAFETY_CRITICAL_REVIEW_BOUNDS_EXCEEDED,
                    SPLIT_THE_CHANGE,
                    "supported_configuration_change",
                    *(("unlock_or_disarm_effect",) if unlock_or_disarm else ()),
                }
            )
        ),
    )


def configuration_operation_policy(
    operation: ConfigurationOperation,
    *,
    owner_authoritative: bool = True,
    safety_critical_comparison: bool = True,
    unlock_or_disarm_floor: bool | None = None,
) -> OperationPolicyClassification:
    """Classify one operation for both policy and approval review.

    ``safety_critical_comparison`` selects the f2-v3 (HAMCP-213) protected
    physical-effect mapping and script parity. It is false only for the
    exact recomputation of historical f2-v1 and f2-v2 snapshots and for the
    legacy contract-v1 route. ``unlock_or_disarm_floor`` (default: the same
    as ``safety_critical_comparison``) applies the current OD-6(b) floor; the
    legacy route passes it under current policy.
    """
    if operation.resource_type == ENTITY_METADATA_RESOURCE_TYPE:
        return _entity_metadata_operation_policy(operation)
    if operation.resource_type in AVAILABILITY_EXPOSURE_RESOURCE_TYPES:
        return _availability_exposure_operation_policy(operation)
    if operation.action == CONFIGURATION_DELETE_ACTION:
        return _configuration_delete_operation_policy(operation)
    triggers = _risk_triggers(operation)
    safety_critical_services = _risk_services(operation)
    risk_delta = _risk_delta(operation.risk.level)
    reasons = {"supported_configuration_change"}
    consequence = PhysicalConsequence.NONE
    exact_automation_update = bool(
        owner_authoritative
        and _exact_existing_automation_update(
            operation, include_scripts=safety_critical_comparison
        )
    )

    if unlock_or_disarm_floor is None:
        unlock_or_disarm_floor = safety_critical_comparison
    comparison = (
        compare_operation(operation)
        if unlock_or_disarm_floor
        and operation.resource_type in {"automation", "script"}
        else None
    )
    if comparison is not None and comparison.bounds_exceeded:
        # Review H3: a comparison that exceeded a reviewed bound is
        # incomplete. It is prohibited on every route, before any shortcut,
        # and never a weaker class; the floor scan still reports an unlock
        # or disarm in its reason codes.
        return _review_bounds_exceeded(
            risk_delta, unlock_or_disarm=comparison.unlock_or_disarm
        )
    unlock_or_disarm = bool(comparison is not None and comparison.unlock_or_disarm)
    if unlock_or_disarm and owner_authoritative and safety_critical_comparison:
        # Review H3 / OD-6(b): where an unlock or disarm effect is (or may
        # be) present, no shortcut (retained effect, garage inverse, retry
        # transfer) applies. The comparison mapping always requires the
        # per-object acknowledgement for it.
        protected = _safety_critical_comparison_policy(
            operation,
            triggers=triggers,
            safety_critical_services=safety_critical_services,
            risk_delta=risk_delta,
        )
        if protected is not None:
            return protected

    if not unlock_or_disarm and _retained_safety_critical_effect_is_non_risk_increasing(
        operation,
        triggers=triggers,
        safety_critical_services=safety_critical_services,
    ):
        return OperationPolicyClassification(
            ApprovalPolicyClass.ELEVATED_ADMIN,
            RiskDelta.MODERATE,
            PhysicalConsequence.SAFETY_CRITICAL,
            (
                "retained_safety_critical_effect",
                "non_risk_increasing_condition_guard_added",
                "safety_critical_effect_requires_elevated_review",
                "supported_configuration_change",
            ),
        )

    if operation.resource_type == "helper":
        consequence = PhysicalConsequence.INDIRECT
        reasons.add("helper_change_indirect_consequence")
    if (
        triggers & _SAFETY_CRITICAL_TRIGGERS
        or safety_critical_services
    ):
        consequence = PhysicalConsequence.SAFETY_CRITICAL
        reasons.add("safety_critical_effect_not_reviewed")
        if safety_critical_services:
            reasons.add("safety_critical_service_prohibited")
    elif triggers & _DIRECT_CONSEQUENCE_TRIGGERS or any(
        "Physical-device action detected" in reason
        for reason in operation.risk.reasons
    ):
        consequence = PhysicalConsequence.DIRECT
        reasons.add("direct_physical_consequence")

    consequence_uncertain = bool(
        triggers & _UNCLASSIFIABLE_TRIGGERS
        or any(
            "could not be bounded structurally" in warning
            for warning in operation.risk.warnings
        )
    )
    if owner_authoritative and inverse_eligibility.local_member_marked(operation):
        return OperationPolicyClassification(
            ApprovalPolicyClass.ELEVATED_ADMIN, RiskDelta.HIGH,
            PhysicalConsequence.SAFETY_CRITICAL,
            ("exact_verified_garage_inverse", "owner_decision_required",
             *(("original_internal_retry_restored",) if operation.resource_type == "script" else ())),
        )
    if owner_authoritative and local_member_marked(operation):
        # Whole-composition revalidation occurs in _single_plan_policy. This
        # member classification also supplies the bound review projection.
        return OperationPolicyClassification(
            ApprovalPolicyClass.ELEVATED_ADMIN,
            max(risk_delta, RiskDelta.HIGH, key=lambda item: _RISK_RANK[item]),
            consequence,
            tuple(sorted({*reasons, "proved_configuration_retry_transfer", "owner_decision_required"})),
        )
    if owner_authoritative and safety_critical_comparison:
        protected = _safety_critical_comparison_policy(
            operation,
            triggers=triggers,
            safety_critical_services=safety_critical_services,
            risk_delta=risk_delta,
        )
        if protected is not None:
            return protected
    if consequence_uncertain and exact_automation_update:
        return OperationPolicyClassification(
            ApprovalPolicyClass.ELEVATED_ADMIN,
            max(risk_delta, RiskDelta.HIGH, key=lambda item: _RISK_RANK[item]),
            (
                consequence
                if consequence is not PhysicalConsequence.NONE
                else PhysicalConsequence.UNKNOWN
            ),
            tuple(
                sorted(
                    {
                        *reasons,
                        "automation_consequence_semantics_incomplete",
                        "exact_existing_automation_update",
                        "owner_decision_required",
                    }
                )
            ),
        )
    if consequence_uncertain:
        return OperationPolicyClassification(
            ApprovalPolicyClass.PROHIBITED,
            risk_delta,
            consequence,
            tuple(sorted({*reasons, "unknown_policy_classification"})),
        )
    if (
        consequence == PhysicalConsequence.SAFETY_CRITICAL
        and exact_automation_update
    ):
        return OperationPolicyClassification(
            ApprovalPolicyClass.ELEVATED_ADMIN,
            max(risk_delta, RiskDelta.HIGH, key=lambda item: _RISK_RANK[item]),
            consequence,
            tuple(
                sorted(
                    {
                        *reasons,
                        "exact_existing_automation_update",
                        "owner_decision_required",
                    }
                )
            ),
        )
    if consequence == PhysicalConsequence.SAFETY_CRITICAL:
        return OperationPolicyClassification(
            ApprovalPolicyClass.PROHIBITED,
            risk_delta,
            consequence,
            tuple(sorted(reasons)),
        )
    if risk_delta == RiskDelta.CRITICAL:
        return OperationPolicyClassification(
            ApprovalPolicyClass.PROHIBITED,
            risk_delta,
            consequence,
            tuple(sorted({*reasons, "critical_risk_delta"})),
        )
    if (
        risk_delta == RiskDelta.HIGH
        or consequence == PhysicalConsequence.DIRECT
    ):
        reasons.add(
            "high_risk_delta"
            if risk_delta == RiskDelta.HIGH
            else "direct_consequence_requires_elevation"
        )
        return OperationPolicyClassification(
            ApprovalPolicyClass.ELEVATED_ADMIN,
            risk_delta,
            consequence,
            tuple(sorted(reasons)),
        )
    reasons.add("standard_configuration_policy")
    return OperationPolicyClassification(
        ApprovalPolicyClass.STANDARD_ADMIN,
        risk_delta,
        consequence,
        tuple(sorted(reasons)),
    )


def _single_plan_policy(
    plan: ChangePlan,
    *,
    owner_authoritative: bool = True,
    safety_critical_comparison: bool = True,
) -> tuple[OperationPolicyClassification, ...]:
    if plan.operation == ChangeOperation.CREATE_FULL_BACKUP:
        return (
            OperationPolicyClassification(
                ApprovalPolicyClass.STANDARD_ADMIN,
                RiskDelta.MODERATE,
                PhysicalConsequence.INDIRECT,
                ("full_backup_standard_policy",),
            ),
        )
    if plan.operation == ChangeOperation.CONTROLLED_RELOAD:
        return (
            OperationPolicyClassification(
                ApprovalPolicyClass.STANDARD_ADMIN,
                RiskDelta.MODERATE,
                PhysicalConsequence.INDIRECT,
                ("controlled_reload_standard_policy",),
            ),
        )
    if plan.operation == ChangeOperation.RESTART_ADDON:
        return (
            OperationPolicyClassification(
                ApprovalPolicyClass.ELEVATED_ADMIN,
                RiskDelta.HIGH,
                PhysicalConsequence.INDIRECT,
                ("addon_restart_elevated_policy",),
            ),
        )
    if plan.operation == ChangeOperation.RESTART_HOME_ASSISTANT:
        return (
            OperationPolicyClassification(
                ApprovalPolicyClass.ELEVATED_ADMIN,
                RiskDelta.HIGH,
                PhysicalConsequence.INDIRECT,
                ("home_assistant_restart_elevated_policy",),
            ),
        )
    if plan.operation == ChangeOperation.SET_INPUT_BOOLEAN_STATE:
        baseline = (
            plan.operational.baseline
            if plan.operational is not None
            and isinstance(plan.operational.baseline, dict)
            else {}
        )
        dependency = baseline.get("dependency_risk")
        if not isinstance(dependency, dict):
            return (
                OperationPolicyClassification(
                    ApprovalPolicyClass.PROHIBITED,
                    RiskDelta.CRITICAL,
                    PhysicalConsequence.NONE,
                    ("helper_dependency_evidence_missing",),
                ),
            )
        complete = (
            dependency.get("consequence_evidence_complete") is True
            if owner_authoritative
            else dependency.get("evidence_complete") is True
        )
        eligible = (
            dependency.get("execution_contract_complete") is True
            if owner_authoritative
            else dependency.get("execution_eligible") is True
        )
        precision = str(
            dependency.get(
                "semantic_precision",
                "exact" if complete else "coverage_failure",
            )
        )
        evidence_reason = (
            "helper_dependency_bounded_semantic_opacity"
            if precision == "bounded_opaque"
            else (
                "helper_dependency_coverage_failure"
                if not complete
                else "helper_dependency_evidence_complete"
            )
        )
        consequence = dependency.get("physical_consequence")
        if eligible and consequence == "none":
            return (
                OperationPolicyClassification(
                    ApprovalPolicyClass.STANDARD_ADMIN,
                    RiskDelta.LOW,
                    PhysicalConsequence.NONE,
                    (
                        "exact_input_boolean_state_standard_policy",
                        evidence_reason,
                        "no_consequential_dependency_detected",
                    ),
                ),
            )
        if eligible:
            return (
                OperationPolicyClassification(
                    ApprovalPolicyClass.ELEVATED_ADMIN,
                    RiskDelta.HIGH,
                    (
                        PhysicalConsequence.SAFETY_CRITICAL
                        if consequence == "safety_critical"
                        else PhysicalConsequence.DIRECT
                        if consequence == "direct"
                        else (
                            PhysicalConsequence.UNKNOWN
                            if owner_authoritative
                            else PhysicalConsequence.INDIRECT
                        )
                    ),
                    (
                        (
                            "consequential_helper_dependency_detected"
                            if consequence in {"direct", "safety_critical"}
                            else "unknown_helper_dependency_effect"
                        ),
                        "exact_input_boolean_state_elevated_policy",
                        evidence_reason,
                    ),
                ),
            )
        if not owner_authoritative and precision == "bounded_opaque":
            return (
                OperationPolicyClassification(
                    ApprovalPolicyClass.ELEVATED_ADMIN,
                    RiskDelta.HIGH,
                    (
                        PhysicalConsequence.SAFETY_CRITICAL
                        if consequence == "safety_critical"
                        else PhysicalConsequence.DIRECT
                        if consequence == "direct"
                        else PhysicalConsequence.INDIRECT
                    ),
                    (
                        "exact_input_boolean_state_elevated_policy",
                        "helper_dependency_bounded_semantic_opacity",
                        "helper_dependency_execution_ineligible",
                    ),
                ),
            )
        if not owner_authoritative:
            return (
                OperationPolicyClassification(
                    ApprovalPolicyClass.ELEVATED_ADMIN,
                    RiskDelta.HIGH,
                    PhysicalConsequence.INDIRECT,
                    (
                        "exact_input_boolean_state_elevated_policy",
                        "helper_dependency_coverage_failure",
                        "low_risk_not_established",
                    ),
                ),
            )
        return (
            OperationPolicyClassification(
                ApprovalPolicyClass.PROHIBITED,
                RiskDelta.CRITICAL,
                (
                    PhysicalConsequence.SAFETY_CRITICAL
                    if consequence == "safety_critical"
                    else PhysicalConsequence.DIRECT
                    if consequence == "direct"
                    else PhysicalConsequence.UNKNOWN
                ),
                (
                    "helper_execution_contract_incomplete",
                    "technical_execution_authority_required",
                ),
            ),
        )
    if plan.operation == ChangeOperation.UPDATE_DASHBOARD:
        elevated = plan.risk.level is RiskLevel.HIGH
        return (
            OperationPolicyClassification(
                (
                    ApprovalPolicyClass.ELEVATED_ADMIN
                    if elevated
                    else ApprovalPolicyClass.STANDARD_ADMIN
                ),
                RiskDelta.HIGH if elevated else RiskDelta.MODERATE,
                PhysicalConsequence.NONE,
                (
                    "dashboard_action_change_elevated_policy"
                    if elevated
                    else "dashboard_update_standard_policy",
                    "operator_accepted_non_atomic_dashboard_save",
                ),
            ),
        )
    if plan.operation == ChangeOperation.CONFIGURATION_PLAN:
        if not plan.operations:
            return (
                OperationPolicyClassification(
                    ApprovalPolicyClass.PROHIBITED,
                    RiskDelta.CRITICAL,
                    PhysicalConsequence.NONE,
                    ("unknown_policy_classification",),
                ),
            )
        if len(plan.operations) != 1 and any(
            op.resource_type == ENTITY_METADATA_RESOURCE_TYPE
            for op in plan.operations
        ):
            # An entity metadata update is always a single-operation plan.
            return (
                OperationPolicyClassification(
                    ApprovalPolicyClass.PROHIBITED,
                    RiskDelta.CRITICAL,
                    PhysicalConsequence.UNKNOWN,
                    ("entity_metadata_execution_contract_incomplete",),
                ),
            )
        if any(
            op.resource_type in AVAILABILITY_EXPOSURE_RESOURCE_TYPES
            for op in plan.operations
        ) and not _availability_exposure_plan_complete(plan.operations):
            # One 069B resource type per plan, never mixed, at most 8 children.
            return (
                OperationPolicyClassification(
                    ApprovalPolicyClass.PROHIBITED,
                    RiskDelta.CRITICAL,
                    PhysicalConsequence.UNKNOWN,
                    (AVAILABILITY_EXPOSURE_INCOMPLETE,),
                ),
            )
        if any(
            op.action == CONFIGURATION_DELETE_ACTION for op in plan.operations
        ) and not _configuration_delete_plan_complete(plan.operations):
            # HAMCP-155: deletes are never mixed with other operations.
            return (
                OperationPolicyClassification(
                    ApprovalPolicyClass.PROHIBITED,
                    RiskDelta.CRITICAL,
                    PhysicalConsequence.UNKNOWN,
                    (CONFIGURATION_DELETE_INCOMPLETE,),
                ),
            )
        if any(inverse_eligibility.markers(op) for op in plan.operations) and inverse_eligibility.bound(plan) is None:
            return (OperationPolicyClassification(
                ApprovalPolicyClass.PROHIBITED, RiskDelta.HIGH,
                PhysicalConsequence.SAFETY_CRITICAL, ("garage_inverse_proof_invalid",),
            ),)
        if any(markers(op) for op in plan.operations) and bound_proof(plan.operations) is None:
            return (OperationPolicyClassification(
                ApprovalPolicyClass.PROHIBITED, RiskDelta.HIGH,
                PhysicalConsequence.SAFETY_CRITICAL,
                ("configuration_retry_proof_invalid",),
            ),)
        if safety_critical_comparison:
            binding = plan_acknowledgement_binding(plan.operations)
            if (
                binding is not None
                and len(binding["objects"]) > MAX_ACKNOWLEDGED_OBJECTS
            ):
                # Review H3: more acknowledgement rows than one review can
                # bind is an incomplete review, never an unbindable one.
                return (
                    _review_bounds_exceeded(
                        RiskDelta.HIGH,
                        unlock_or_disarm=any(
                            compare_operation(op).unlock_or_disarm
                            for op in plan.operations
                        ),
                    ),
                )
        return tuple(
            configuration_operation_policy(
                operation,
                owner_authoritative=owner_authoritative,
                safety_critical_comparison=safety_critical_comparison,
            )
            for operation in plan.operations
        )
    if plan.operation in {
        ChangeOperation.CREATE_AUTOMATION,
        ChangeOperation.UPDATE_AUTOMATION,
    }:
        synthetic = ConfigurationOperation(
            operation_id="legacy_automation_operation",
            order=0,
            depends_on=[],
            resource_type="automation",
            action=(
                "create"
                if plan.operation == ChangeOperation.CREATE_AUTOMATION
                else "update"
            ),
            target_id=plan.target_id,
            helper_type=None,
            proposed_config=plan.proposed_config,
            current_config=plan.current_config,
            normalized_proposed_config=plan.normalized_proposed_config,
            normalized_current_config=plan.normalized_current_config,
            current_state_fingerprint=plan.current_state_fingerprint,
            proposed_config_hash=plan.proposed_config_hash,
            normalization_version=plan.normalization_version,
            risk=plan.risk,
            warnings=plan.warnings,
        )
        return (
            configuration_operation_policy(
                synthetic,
                # ADR-022 intentionally authorizes only the current typed
                # configuration-plan route. The legacy contract-v1 route
                # retains its historical consequence-policy boundary, except
                # that under current policy the retained-effect exception no
                # longer admits an unlock or disarm change (review H3).
                owner_authoritative=False,
                safety_critical_comparison=False,
                unlock_or_disarm_floor=safety_critical_comparison,
            ),
        )
    return (
        OperationPolicyClassification(
            ApprovalPolicyClass.PROHIBITED,
            RiskDelta.CRITICAL,
            PhysicalConsequence.NONE,
            ("unknown_policy_classification",),
        ),
    )


def aggregate_policy_classifications(
    values: Iterable[OperationPolicyClassification],
) -> OperationPolicyClassification:
    classifications = tuple(values)
    if not classifications:
        return OperationPolicyClassification(
            ApprovalPolicyClass.PROHIBITED,
            RiskDelta.CRITICAL,
            PhysicalConsequence.NONE,
            ("unknown_policy_classification",),
        )
    return OperationPolicyClassification(
        max(classifications, key=lambda item: _POLICY_RANK[item.policy_class]).policy_class,
        max(classifications, key=lambda item: _RISK_RANK[item.risk_delta]).risk_delta,
        max(
            classifications,
            key=lambda item: _CONSEQUENCE_RANK[item.physical_consequence],
        ).physical_consequence,
        tuple(
            sorted(
                {
                    reason
                    for classification in classifications
                    for reason in classification.reason_codes
                }
            )
        ),
        safety_critical_acknowledgement=any(
            classification.safety_critical_acknowledgement
            for classification in classifications
        ),
    )


def policy_subject_payload(plan: ChangePlan) -> dict[str, Any]:
    """Return the immutable normalized authority evaluated by F2 policy."""

    operations = [
        {
            "operation_id": operation.operation_id,
            "order": operation.order,
            "depends_on": list(operation.depends_on),
            "resource_type": operation.resource_type,
            "helper_type": operation.helper_type,
            "action": operation.action,
            "target_id": operation.target_id,
            "current_state_fingerprint": operation.current_state_fingerprint,
            "proposed_config_hash": operation.proposed_config_hash,
            "raw_proposed_config_hash": stable_hash(operation.proposed_config),
            "normalized_proposed_config_hash": stable_hash(
                operation.normalized_proposed_config
            ),
            "normalization_version": operation.normalization_version,
            "risk_level": operation.risk.level.value,
            "risk_apply_allowed": operation.risk.apply_allowed,
            "risk_reasons": sorted(set(operation.risk.reasons)),
            "risk_evidence": sorted(
                (
                    dict(item)
                    for item in operation.risk.evidence
                    if isinstance(item, dict)
                ),
                key=lambda item: stable_hash(item),
            ),
            "risk_warnings": sorted(set(operation.risk.warnings)),
            **(
                {
                    "semantic_projection_hash": (
                        operation.semantic_projection_hash
                    )
                }
                if operation.semantic_projection_hash is not None
                else {}
            ),
            **(
                {"called_scripts_hash": stable_hash(operation.called_scripts)}
                if operation.called_scripts is not None
                else {}
            ),
        }
        for operation in sorted(plan.operations, key=lambda item: item.order)
    ]
    operational = plan.operational
    return {
        "contract_version": plan.contract_version,
        "plan_family": plan.plan_family,
        "plan_id": plan.plan_id,
        "plan_version": plan.plan_version,
        "created_at": plan.created_at,
        "expires_at": plan.expires_at,
        "title": plan.title,
        "description": plan.description,
        "requested_by": plan.requested_by,
        "operation": plan.operation.value,
        "target": {
            "target_type": plan.target_type,
            "target_id": plan.target_id,
        },
        "current_state_fingerprint": plan.current_state_fingerprint,
        "proposed_config_hash": plan.proposed_config_hash,
        "raw_proposed_config_hash": stable_hash(plan.proposed_config),
        "normalized_proposed_config_hash": stable_hash(
            plan.normalized_proposed_config
        ),
        "normalization_version": plan.normalization_version,
        "risk": {
            "level": plan.risk.level.value,
            "apply_allowed": plan.risk.apply_allowed,
            "reasons": sorted(set(plan.risk.reasons)),
            "evidence": sorted(
                (
                    dict(item)
                    for item in plan.risk.evidence
                    if isinstance(item, dict)
                ),
                key=lambda item: stable_hash(item),
            ),
            "warnings": sorted(set(plan.risk.warnings)),
        },
        "operations": operations,
        "operational": (
            {
                "schema_version": operational.schema_version,
                "family": operational.family,
                "operation": operational.operation,
                "requested_name": operational.requested_name,
                "provider": operational.provider,
                "provider_capability_evidence": (
                    operational.provider_capability_evidence
                ),
                "expected_effects": operational.expected_effects,
                "preconditions": operational.preconditions,
                "verification_contract": operational.verification_contract,
                "baseline": operational.baseline,
                "limitations": operational.limitations,
                "rollback_available": operational.rollback_available,
            }
            if operational is not None
            else None
        ),
    }


def _evaluate_change_policy_version(
    plan: ChangePlan,
    *,
    policy_version: str,
    owner_authoritative: bool,
    safety_critical_comparison: bool = True,
) -> ChangePolicyDecision:
    classification = aggregate_policy_classifications(
        _single_plan_policy(
            plan,
            owner_authoritative=owner_authoritative,
            safety_critical_comparison=safety_critical_comparison,
        )
    )
    dependency_binding = (
        plan.operational.baseline.get("dependency_risk")
        if plan.operation == ChangeOperation.SET_INPUT_BOOLEAN_STATE
        and plan.operational is not None
        and isinstance(plan.operational.baseline, dict)
        else None
    )
    exact_owner_authoritative = bool(
        owner_authoritative
        and (
            (
                isinstance(dependency_binding, dict)
                and dependency_binding.get("execution_contract_complete")
                is True
            )
            or (
                plan.operation == ChangeOperation.CONFIGURATION_PLAN
                and bool(plan.operations)
                and all(
                    _exact_existing_automation_update(
                        operation,
                        include_scripts=safety_critical_comparison,
                    )
                    for operation in plan.operations
                )
            )
            or (plan.operation == ChangeOperation.CONFIGURATION_PLAN
                and bound_proof(plan.operations) is not None)
            or (plan.operation == ChangeOperation.CONFIGURATION_PLAN
                and inverse_eligibility.bound(plan) is not None)
            or _exact_existing_dashboard_update(plan)
        )
    )
    required = {
        ApprovalPolicyClass.STANDARD_ADMIN: (
            ApprovalActionKind.PLAN_APPROVAL,
        ),
        ApprovalPolicyClass.ELEVATED_ADMIN: (
            (ApprovalActionKind.PLAN_APPROVAL,)
            if exact_owner_authoritative
            else (
                ApprovalActionKind.PLAN_APPROVAL,
                ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT,
            )
        ),
        ApprovalPolicyClass.PROHIBITED: (),
    }[classification.policy_class]
    if (
        classification.safety_critical_acknowledgement
        and classification.policy_class is ApprovalPolicyClass.ELEVATED_ADMIN
    ):
        # D1: the safety-critical acknowledgement subsumes the elevated
        # acknowledgement and no exactness shortcut ever removes it. Only a
        # different, recomputed comparison result can.
        required = (
            ApprovalActionKind.PLAN_APPROVAL,
            ApprovalActionKind.SAFETY_CRITICAL_ACKNOWLEDGEMENT,
        )
    subject_hash = stable_hash(policy_subject_payload(plan))
    decision_payload = {
        "policy_version": policy_version,
        "policy_class": classification.policy_class.value,
        "risk_delta": classification.risk_delta.value,
        "physical_consequence": classification.physical_consequence.value,
        "reason_codes": list(classification.reason_codes),
        "required_acknowledgements": [item.value for item in required],
        "policy_subject_hash": subject_hash,
    }
    return ChangePolicyDecision(
        policy_version=policy_version,
        policy_class=classification.policy_class,
        risk_delta=classification.risk_delta,
        physical_consequence=classification.physical_consequence,
        reason_codes=classification.reason_codes,
        required_acknowledgements=required,
        policy_subject_hash=subject_hash,
        policy_decision_hash=stable_hash(decision_payload),
    )


def evaluate_change_policy(plan: ChangePlan) -> ChangePolicyDecision:
    """Evaluate a fresh plan under current owner-authoritative policy."""

    return _evaluate_change_policy_version(
        plan,
        policy_version=POLICY_VERSION,
        owner_authoritative=True,
    )


def persisted_f2_v1_policy_snapshot_matches(plan: ChangePlan) -> bool:
    """Recognize an exact Beta 53 F2 snapshot without granting authority."""

    return bool(
        plan.policy_decision is not None
        and plan.policy_decision.policy_version == "f2-v1"
        and plan.policy_decision
        == _evaluate_change_policy_version(
            plan,
            policy_version="f2-v1",
            owner_authoritative=False,
            safety_critical_comparison=False,
        )
    )


def persisted_f2_v2_policy_snapshot_matches(plan: ChangePlan) -> bool:
    """Recognize an exact pre-HAMCP-213 F2 snapshot without granting authority."""

    return bool(
        plan.policy_decision is not None
        and plan.policy_decision.policy_version == F2_V2_POLICY_VERSION
        and plan.policy_decision
        == _evaluate_change_policy_version(
            plan,
            policy_version=F2_V2_POLICY_VERSION,
            owner_authoritative=True,
            safety_critical_comparison=False,
        )
    )


def persisted_historical_policy_snapshot_matches(plan: ChangePlan) -> bool:
    """Recognize an exact superseded (f2-v1 or f2-v2) snapshot as evidence only."""

    return persisted_f2_v1_policy_snapshot_matches(
        plan
    ) or persisted_f2_v2_policy_snapshot_matches(plan)


def policy_snapshot_matches(plan: ChangePlan) -> bool:
    return bool(
        plan.policy_decision is not None
        and plan.policy_decision == evaluate_change_policy(plan)
    )
