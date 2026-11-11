"""Planning of created-object rollback delete children (HAMCP-298 §3.4.3).

A created-object rollback plan holds, in strict reverse forward order, one
delete child per Engineering-created object.  An automation or script child
is the unchanged HAMCP-155 child (``configuration_delete_planning``); a
scene or helper child is prepared here with the closed
``created_object_delete_intent_v1`` and the same disclosure shape, basis
rules and classification.  Nothing here dispatches.
"""

from __future__ import annotations

from typing import Any

from . import configuration_delete as delete_contract
from . import created_object_delete as contract
from . import global_reference_disclosure
from .configuration_delete_planning import (
    BASIS_PROTECTED_DEPENDENCY,
    BASIS_PROTECTED_EFFECT,
    CONSUMER_COVERAGE_WARNING,
    DISCLOSURE_MODEL,
    MAX_DELETIONS,
    NO_ROLLBACK_DISCLOSURE,
    RECREATE_WINDOW_DAYS,
    ROLLBACK_DISCLOSURE,
    Item,
    Preparation,
    Refusal,
    Request,
    _read,
    classify,
    prepare_item,
)
from .entity_availability_exposure_planning import (
    INCOMPLETE_IMPACT_TEXT,
    MAX_DISCLOSED_CONSUMERS,
    MAX_STATEMENTS,
    UNAVAILABLE_COVERAGE,
)

KIND_NAMES = {
    "scene_configuration": "scene",
    "input_boolean": "input_boolean helper",
    "input_number": "input_number helper",
}
SCENE_READBACK_PROVES = ("config_404", "registry_entry_absent", "state_absent")
HELPER_READBACK_PROVES = (
    "storage_item_absent",
    "registry_entry_absent",
    "state_absent",
)
HELPER_DURABILITY_WARNING = (
    "Helper absence is verified in the Home Assistant runtime and entity "
    "registry, not on disk: Core saves the helper storage file about 10 "
    "seconds after the delete. A crash or power loss inside that window can "
    "lose the save, and the helper can reappear after a restart."
)
SCENE_RUNTIME_NOTE = (
    "Deleting a scene removes its stored configuration and its entity; it "
    "does not change any member entity's current state."
)


def plan_resource_type(resource_type: str) -> str:
    """The plan operation's resource type for a delete child's type."""

    return "helper" if resource_type in contract.HELPER_TYPES else resource_type


def plan_helper_type(resource_type: str) -> str | None:
    return resource_type if resource_type in contract.HELPER_TYPES else None


def expected_basis_created(
    resource_type: str, current: Any, dependency_reasons: list[str]
) -> tuple[str, ...]:
    """The HAMCP-155 bases of a created scene or helper delete (recomputed).

    A scene whose members include a protected entity removes a protected
    effect (the scene could set it); a helper has no effects of its own.
    Dependents raise either, as in HAMCP-155 §3.2.
    """

    from .scene_configuration_planning import id_protection_reasons

    basis: list[str] = []
    if resource_type == "scene_configuration":
        entities = current.get("entities") if isinstance(current, dict) else None
        if not isinstance(entities, dict) or id_protection_reasons(list(entities)):
            basis.append(BASIS_PROTECTED_EFFECT)
    if dependency_reasons:
        basis.append(BASIS_PROTECTED_DEPENDENCY)
    return tuple(basis)


def recreate_body(resource_type: str, current: dict[str, Any]) -> dict[str, Any]:
    """The governed create body that would recreate the deleted object."""

    if resource_type in contract.HELPER_TYPES:
        return {key: value for key, value in current.items() if key != "id"}
    return dict(current)


def recreate_errors(
    resource_type: str,
    target_id: str,
    current: dict[str, Any],
    known_secrets: tuple[str, ...],
) -> list[str]:
    """Why the existing governed create could not recreate this object."""

    from .resources import validate_resource, validate_resource_create_identity
    from .scene_configuration import create_proposed_errors

    try:
        if resource_type == "scene_configuration":
            return create_proposed_errors(target_id, current)
        body = recreate_body(resource_type, current)
        _valid, errors, _warnings = validate_resource(
            resource_type, target_id, body, known_secrets
        )
        errors = list(errors)
        errors.extend(
            validate_resource_create_identity(resource_type, target_id, body)
        )
    except (TypeError, ValueError):
        return ["pre-image is not reviewable for recreate"]
    return errors


def _identity_matches(resource_type: str, target_id: str, current: Any) -> bool:
    if not isinstance(current, dict):
        return False
    try:
        storage_id = contract.identity_model.storage_object_id_for(
            resource_type, target_id
        )
    except contract.identity_model.IdentityMismatch:
        return False
    return current.get("id") == storage_id


def _statements(
    resource_type: str,
    target_id: str,
    entity_id: str,
    basis: tuple[str, ...],
    consumers: dict[str, Any],
) -> list[str]:
    kind = KIND_NAMES[resource_type]
    statements = [
        f"Deleting {kind} {target_id} removes {entity_id} and its stored "
        "configuration."
    ]
    if BASIS_PROTECTED_EFFECT in basis:
        statements.append(
            f"Scene {target_id} can set a protected entity; deleting it "
            "removes that protected effect and is treated as protected."
        )
    for source in consumers["items"][: MAX_STATEMENTS - 4]:
        name = source.get("source_entity_id") or source.get("source_id") or "unknown"
        statements.append(
            f"Deleting this breaks {source['source_type']} {name} "
            f"({source['relation']})."
        )
    if not consumers["evidence_complete"]:
        statements.append(
            f"For {entity_id}, {INCOMPLETE_IMPACT_TEXT} ("
            + ", ".join(consumers["evidence_gaps"])
            + "); this is treated as protected."
        )
    elif not consumers["count"]:
        statements.append("No indexed automation or script consumer was found.")
    return [item[:300] for item in statements[:MAX_STATEMENTS]]


async def prepare_created_item(
    gateway: Any,
    index: int,
    resource_type: str,
    target_id: str,
    *,
    known_secrets: tuple[str, ...],
    global_context: Any,
    global_limit: int,
) -> Item:
    """Read, refuse, bind and classify one created scene or helper delete."""

    from .resources import resource_fingerprint

    if resource_type not in contract.RESOURCE_TYPES:
        raise Refusal("configuration_delete_invalid_value", field="resource_type")
    target = {"resource_type": resource_type, "target_id": target_id}
    current = await _read(
        gateway.read_config(resource_type, target_id),
        "configuration_delete_target_unreadable",
        **target,
    )
    if current is None:
        raise Refusal(
            "configuration_delete_target_not_found", kind="not_found", **target
        )
    if not _identity_matches(resource_type, target_id, current):
        raise Refusal(
            "configuration_delete_target_unreviewable", kind="unreviewable", **target
        )
    entry = await _read(
        gateway.registry_entry(resource_type, target_id),
        "configuration_delete_registry_unreadable",
        **target,
    )
    if entry is None:
        raise Refusal(
            "configuration_delete_registry_entry_missing", kind="not_found", **target
        )
    entity_id = entry["entity_id"]
    registry_entry_id = entry["id"]
    evidence = await _read(
        gateway.consumer_evidence(entity_id),
        "configuration_delete_dependency_evidence_unavailable",
        **target,
    )
    if evidence is None:
        raise Refusal(
            "configuration_delete_dependency_evidence_unavailable",
            kind="unavailable",
            **target,
        )
    dependency_reasons = sorted(set(evidence["dependency_reasons"]))[
        : delete_contract.MAX_DEPENDENCY_REASONS
    ]
    basis = expected_basis_created(resource_type, current, dependency_reasons)
    consumers = {
        "status": evidence["status"],
        "evidence_complete": evidence["evidence_complete"],
        "evidence_gaps": evidence["evidence_gaps"],
        "items": evidence["consumers"][:MAX_DISCLOSED_CONSUMERS],
        "count": evidence["consumer_count"],
        "truncated": evidence["consumer_count"] > MAX_DISCLOSED_CONSUMERS,
        "coverage": evidence["coverage"],
        "coverage_unavailable": list(UNAVAILABLE_COVERAGE),
    }
    statements = _statements(resource_type, target_id, entity_id, basis, consumers)
    errors = recreate_errors(resource_type, target_id, current, known_secrets)
    recreate_eligible = not errors
    warnings = [CONSUMER_COVERAGE_WARNING]
    if resource_type in contract.HELPER_TYPES:
        warnings.append(HELPER_DURABILITY_WARNING)
    else:
        warnings.append(SCENE_RUNTIME_NOTE)
    recreate_disclosure = (
        ROLLBACK_DISCLOSURE
        if recreate_eligible
        else NO_ROLLBACK_DISCLOSURE.format(reason="; ".join(errors)[:200])
    )
    warnings.append(recreate_disclosure)
    intent = contract.build_intent(
        resource_type=resource_type,
        target_id=target_id,
        entity_id=entity_id,
        registry_entry_id=registry_entry_id,
        pre_image_fingerprint=resource_fingerprint(resource_type, current),
        consumer_evidence_fingerprint=delete_contract.consumer_fingerprint(evidence),
        dependency_reasons=dependency_reasons,
        recreate_eligible=recreate_eligible,
    )
    disclosure: dict[str, Any] = {
        "model": DISCLOSURE_MODEL,
        "resource_type": resource_type,
        "target_id": target_id,
        "entity_id": entity_id,
        "registry_entry_id": registry_entry_id,
        "protection": {
            "protected": bool(basis),
            "basis": list(basis),
            "removed_effect_verdict": (
                "protected" if BASIS_PROTECTED_EFFECT in basis else "none"
            ),
            "unlock_or_disarm": False,
            "dependency_reasons": dependency_reasons,
        },
        "removed_effects": [],
        "consumers": consumers,
        "consequence_statements": statements,
        "warnings": [item[:500] for item in warnings],
        "readback_proves": list(
            HELPER_READBACK_PROVES
            if resource_type in contract.HELPER_TYPES
            else SCENE_READBACK_PROVES
        ),
        "delete_argument": contract.provider_payload(resource_type, target_id),
        "recreate": {
            "eligible": recreate_eligible,
            "window_days": RECREATE_WINDOW_DAYS,
            "disclosure": recreate_disclosure,
        },
        "provider": contract.PROVIDER,
        "fallback": "none",
    }
    return Item(
        operation_id=f"delete_{index + 1}",
        resource_type=resource_type,
        target_id=target_id,
        entity_id=entity_id,
        registry_entry_id=registry_entry_id,
        current=current,
        intent=intent,
        disclosure=disclosure,
        classification=classify(basis, statements, disclosure["warnings"]),
        global_references=global_reference_disclosure.child(
            global_context, (entity_id,), limit=global_limit,
        ),
    )


async def prepare_mixed(
    delete_gateway: Any,
    created_gateway: Any,
    request: Request,
    *,
    known_secrets: tuple[str, ...] = (),
    global_reader: global_reference_disclosure.SnapshotReader | None = None,
) -> Preparation:
    """Prepare every delete child of one created-object rollback, in order.

    ``request.deletions`` holds ``(created type, target)`` pairs in rollback
    order; automation and script children use ``delete_gateway`` (HAMCP-155,
    unchanged), scenes and helpers ``created_gateway``.  A gateway that is
    needed but absent refuses before any read.
    """

    types = {resource_type for resource_type, _target in request.deletions}
    if (types & set(delete_contract.RESOURCE_TYPES) and delete_gateway is None) or (
        types & set(contract.RESOURCE_TYPES) and created_gateway is None
    ):
        raise Refusal("configuration_delete_provider_unavailable", kind="unavailable")
    global_limit = global_reference_disclosure.per_child_limit(len(request.deletions))
    global_context = global_reference_disclosure.read(global_reader)
    items: list[Item] = []
    for index, (resource_type, target_id) in enumerate(request.deletions):
        prepare_one = (
            prepare_created_item
            if resource_type in contract.RESOURCE_TYPES
            else prepare_item
        )
        items.append(
            await prepare_one(
                created_gateway
                if resource_type in contract.RESOURCE_TYPES
                else delete_gateway,
                index,
                resource_type,
                target_id,
                known_secrets=known_secrets,
                global_context=global_context,
                global_limit=global_limit,
            )
        )
    providers = sorted(
        {
            contract.PROVIDER
            if resource_type in contract.RESOURCE_TYPES
            else delete_contract.PROVIDER
            for resource_type, _target in request.deletions
        }
    )
    return Preparation(
        request=request,
        items=items,
        plan_disclosure={
            "model": DISCLOSURE_MODEL,
            "reason": request.reason,
            "target_count": len(items),
            "limit": MAX_DELETIONS,
            "execution": "ordered_non_atomic_stop_on_first_failure",
            "provider": providers[0] if len(providers) == 1 else "mixed",
            "providers": providers,
            "fallback": "none",
            "rollback": "new_separately_approved_recreate_plan",
        },
        global_references=global_reference_disclosure.plan_block(
            global_context, [item.global_references for item in items],
            limit=global_limit,
        ),
    )


def lease_request(
    deletions: list[tuple[str, str]], reason: str
) -> Request:
    """A request over created types (scene and helper types are not 155 types)."""

    if not 1 <= len(deletions) <= MAX_DELETIONS or len(set(deletions)) != len(
        deletions
    ):
        raise Refusal("configuration_delete_invalid_value", field="deletions")
    for resource_type, target_id in deletions:
        if (
            resource_type not in contract.RESOURCE_TYPES
            and resource_type not in delete_contract.RESOURCE_TYPES
        ) or not isinstance(target_id, str):
            raise Refusal("configuration_delete_invalid_value", field="deletions")
    return Request(tuple(deletions), reason, 120)


__all__ = [
    "HELPER_DURABILITY_WARNING",
    "KIND_NAMES",
    "expected_basis_created",
    "lease_request",
    "plan_helper_type",
    "plan_resource_type",
    "prepare_created_item",
    "prepare_mixed",
    "recreate_body",
    "recreate_errors",
]
