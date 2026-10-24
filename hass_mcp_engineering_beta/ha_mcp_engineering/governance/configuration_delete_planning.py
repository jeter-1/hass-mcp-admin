"""Planning rules for governed deletion of automations and scripts (HAMCP-155).

Input is validated before any Home Assistant read.  Planning reads each exact
UI-managed configuration (the pre-image), the one registry entry Core's
delete hook removes, and the deleted entity's consumers from the dependency
index.  It classifies each child by the protected effects the delete
REMOVES (design §3.1) and by its dependents (design §3.2): standard, or
``elevated_admin`` through the existing elevated approval.  A delete is
never classified as "no effect" because it is not a create or update.
Nothing here dispatches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..errors import AuthorizationError, HomeAssistantApiError
from . import configuration_delete as delete_contract
from .entity_availability_exposure_planning import (
    INCOMPLETE_IMPACT_TEXT,
    MAX_DISCLOSED_CONSUMERS,
    MAX_STATEMENTS,
    UNAVAILABLE_COVERAGE,
    _consumer_evidence,
)
from .entity_metadata_planning import EntityMetadataRefusal
from .models import ChangeRiskAssessment, RiskLevel
from .physical_effects import (
    PhysicalEffectComparison,
    compare_physical_effects,
)

ALLOWED_INPUT_FIELDS = frozenset({"deletions", "reason", "expiration_minutes"})
DELETION_FIELDS = frozenset({"resource_type", "target_id"})
MAX_DELETIONS = 8
MAX_REASON_LENGTH = 500
POLICY_TRIGGER = "configuration_delete_policy"
DISCLOSURE_MODEL = "configuration_delete_disclosure_v1"
INVALID_VALUE = "configuration_delete_invalid_value"
FIELD_NOT_SUPPORTED = "configuration_delete_field_not_supported"
# Core keeps a DeletedRegistryEntry for 30 days (ORPHANED_ENTITY_KEEP_SECONDS).
RECREATE_WINDOW_DAYS = 30
BASIS_PROTECTED_EFFECT = "delete_removes_protected_effect"
BASIS_UNLOCK_OR_DISARM_FLOOR = "delete_unlock_or_disarm_floor"
BASIS_PROTECTED_DEPENDENCY = "delete_protected_dependency"
READBACK_PROVES = ("config_404", "registry_entry_absent", "state_absent")
KIND_NAMES = {"automation": "automation", "script": "script"}
CONSUMER_COVERAGE_WARNING = (
    "Consumer coverage: automations are indexed, scripts are partial, and "
    "scenes, groups, templates and dashboards are unavailable; an empty list "
    "is not proof that nothing uses this entity."
)
SCRIPT_CALL_WARNING = (
    "Script calls in a deleted body are not resolved for a delete; each one "
    "is shown as an effect that cannot be determined and needs the "
    "acknowledgement."
)
RUNTIME_NOTE = (
    "Home Assistant removes the entity in a background step after the "
    "configuration is deleted; if that is not observed within the readback "
    "bound the child is reported as config_removed_runtime_unverified, never "
    "as success."
)
ROLLBACK_DISCLOSURE = (
    "Rollback is a new, separately approved plan that recreates the exact "
    "captured configuration under the governed create. Home Assistant "
    "restores the entity's registry metadata (entity_id, name, area, labels, "
    "exposure) only if the recreate happens within "
    f"{RECREATE_WINDOW_DAYS} days and the deleted registry entry was not "
    "purged; this is disclosed, not promised."
)
NO_ROLLBACK_DISCLOSURE = (
    "No rollback: the captured configuration cannot be recreated through the "
    "governed create ({reason})."
)


class ConfigurationDeleteRefusal(EntityMetadataRefusal):
    """A deterministic refusal with a design reason code; no plan exists."""


Refusal = ConfigurationDeleteRefusal


@dataclass(frozen=True)
class Request:
    deletions: tuple[tuple[str, str], ...]
    reason: str
    expiration_minutes: int

    @property
    def lease_target_id(self) -> str:
        return ",".join(
            sorted(f"{resource_type}:{target_id}" for resource_type, target_id in self.deletions)
        )


@dataclass
class Item:
    """One prepared delete child: the exact pre-image, intent and evidence."""

    operation_id: str
    resource_type: str
    target_id: str
    entity_id: str
    registry_entry_id: str
    current: dict[str, Any]
    intent: dict[str, Any]
    disclosure: dict[str, Any]
    classification: dict[str, Any]


@dataclass
class Preparation:
    request: Request
    items: list[Item]
    plan_disclosure: dict[str, Any] = field(default_factory=dict)

    def item(self, operation_id: str) -> Item:
        for candidate in self.items:
            if candidate.operation_id == operation_id:
                return candidate
        raise KeyError(operation_id)


def validate_request(arguments: Any) -> Request:
    """Validate the closed input before any Home Assistant read."""

    from .resources import _validate_resource_id as validate_resource_id

    if not isinstance(arguments, dict) or not all(
        isinstance(key, str) for key in arguments
    ):
        raise Refusal("configuration_delete_invalid_request")
    unsupported = sorted(set(arguments) - ALLOWED_INPUT_FIELDS)
    if unsupported:
        raise Refusal(FIELD_NOT_SUPPORTED, fields=unsupported[:16])
    deletions = arguments.get("deletions")
    if not isinstance(deletions, list) or not deletions:
        raise Refusal(INVALID_VALUE, field="deletions")
    if len(deletions) > MAX_DELETIONS:
        raise Refusal("configuration_delete_too_many_targets", limit=MAX_DELETIONS)
    validated: list[tuple[str, str]] = []
    for value in deletions:
        if not isinstance(value, dict) or set(value) != DELETION_FIELDS:
            raise Refusal(INVALID_VALUE, field="deletions")
        resource_type = value["resource_type"]
        target_id = value["target_id"]
        if resource_type not in delete_contract.RESOURCE_TYPES:
            raise Refusal(
                "configuration_delete_resource_type_not_supported",
                field="resource_type",
            )
        if (
            not isinstance(target_id, str)
            or target_id != target_id.strip()
            or validate_resource_id(resource_type, target_id)
        ):
            raise Refusal(INVALID_VALUE, field="target_id")
        validated.append((resource_type, target_id))
    if len(set(validated)) != len(validated):
        raise Refusal("configuration_delete_duplicate_target")
    reason = arguments.get("reason")
    if (
        not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > MAX_REASON_LENGTH
    ):
        raise Refusal(INVALID_VALUE, field="reason")
    expiration = arguments.get("expiration_minutes", 120)
    if type(expiration) is not int or not 5 <= expiration <= 1440:
        raise Refusal(INVALID_VALUE, field="expiration_minutes")
    return Request(tuple(validated), reason.strip(), expiration)


def compare_removed_effects(
    resource_type: str, normalized_current: dict[str, Any] | None
) -> PhysicalEffectComparison:
    """Compare the pre-image against absence: every effect is removed."""

    return compare_physical_effects(
        resource_type, delete_contract.ACTION, normalized_current, None, None
    )


def expected_basis(
    comparison: PhysicalEffectComparison, dependency_reasons: list[str]
) -> tuple[str, ...]:
    """The single protection decision; policy recomputes it from the plan."""

    basis: list[str] = []
    if comparison.protected:
        basis.append(BASIS_PROTECTED_EFFECT)
    elif comparison.unlock_or_disarm:
        basis.append(BASIS_UNLOCK_OR_DISARM_FLOOR)
    if dependency_reasons:
        basis.append(BASIS_PROTECTED_DEPENDENCY)
    return tuple(basis)


def _effect_rows(comparison: PhysicalEffectComparison) -> list[dict[str, Any]]:
    rows = (comparison.projection or {}).get("rows", [])
    return [
        {
            "object_id": row["object_id"],
            "verdict": row["verdict"],
            "protection": row["protection"],
            "services": sorted(
                {item["service"] for item in row.get("before", [])}
            ),
            "reason_codes": list(row.get("reason_codes", [])),
        }
        for row in rows
    ]


def _name(source: dict[str, Any]) -> str:
    return str(source.get("source_entity_id") or source.get("source_id") or "unknown")


def _statements(
    resource_type: str,
    target_id: str,
    entity_id: str,
    effects: list[dict[str, Any]],
    floor: bool,
    consumers: dict[str, Any],
) -> list[str]:
    kind = KIND_NAMES[resource_type]
    statements = [
        f"Deleting {kind} {target_id} removes {entity_id}: "
        + (
            "it stops triggering"
            if resource_type == "automation"
            else "it can no longer be called"
        )
        + ", and any current run is stopped."
    ]
    removed = [row for row in effects if row["verdict"] != "unresolved"]
    unresolved = [row for row in effects if row["verdict"] == "unresolved"]
    if removed:
        statements.append(
            f"Deleting {kind} {target_id} removes these protected effects: "
            + ", ".join(
                f"{row['object_id']} ({', '.join(row['services']) or row['protection']})"
                for row in removed
            )
            + "."
        )
    if unresolved:
        statements.append(
            f"Deleting {kind} {target_id} removes effects that cannot be "
            "determined: "
            + ", ".join(row["object_id"] for row in unresolved)
            + "; they are treated as protected."
        )
    if floor:
        statements.append(
            f"{kind.capitalize()} {target_id} contains, or may contain, an "
            "unlock or disarm action."
        )
    for source in consumers["items"][: MAX_STATEMENTS - 5]:
        statements.append(
            f"Deleting this breaks {source['source_type']} {_name(source)} "
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


def classify(
    basis: tuple[str, ...], statements: list[str], warnings: list[str]
) -> dict[str, Any]:
    return {
        "policy": "elevated" if basis else "standard",
        "physical_consequence": "safety_critical" if basis else "none",
        "basis": list(basis),
        "statements": list(statements),
        "warnings": list(warnings),
    }


def risk_assessment(
    operation_id: str, classification: dict[str, Any]
) -> ChangeRiskAssessment:
    """Return the risk record and the one policy marker for a delete child."""

    elevated = classification["policy"] == "elevated"
    reasons = list(classification["statements"])
    if elevated:
        reasons = [
            "Protected delete: "
            + ", ".join(classification["basis"])
            + " (elevated approval required)",
            *reasons,
        ][:MAX_STATEMENTS]
    return ChangeRiskAssessment(
        # A delete is destructive even when nothing protected is removed.
        level=RiskLevel.HIGH if elevated else RiskLevel.MEDIUM,
        reasons=reasons or ["Configuration delete"],
        apply_allowed=True,
        evidence=[
            {
                "field": operation_id,
                "trigger": POLICY_TRIGGER,
                "policy": classification["policy"],
                "physical_consequence": classification["physical_consequence"],
                "basis": ",".join(classification["basis"]),
            }
        ],
        warnings=list(classification["warnings"]),
    )


def _recreate_errors(
    resource_type: str,
    target_id: str,
    current: dict[str, Any],
    known_secrets: tuple[str, ...],
) -> list[str]:
    from .resources import validate_resource, validate_resource_create_identity

    try:
        _valid, errors, _warnings = validate_resource(
            resource_type, target_id, current, known_secrets
        )
        errors = list(errors)
        errors.extend(
            validate_resource_create_identity(resource_type, target_id, current)
        )
    except (TypeError, ValueError):
        return ["pre-image is not reviewable for recreate"]
    return errors


async def _read(call: Any, reason_code: str, **details: Any) -> Any:
    try:
        return await call
    except AuthorizationError:
        raise Refusal("configuration_delete_permission_failure", kind="permission") from None
    except (
        delete_contract.ConfigurationDeleteProviderError,
        HomeAssistantApiError,
    ):
        raise Refusal(reason_code, kind="unavailable", **details) from None


async def prepare(
    gateway: Any,
    request: Request,
    *,
    known_secrets: tuple[str, ...] = (),
) -> Preparation:
    """Read, refuse, bind and classify every child.  Nothing is dispatched."""

    from .resources import normalize_resource_config, resource_fingerprint
    from .resources import resource_identity_matches

    items: list[Item] = []
    for index, (resource_type, target_id) in enumerate(request.deletions):
        target = {"resource_type": resource_type, "target_id": target_id}
        current = await _read(
            gateway.read_config(resource_type, target_id),
            "configuration_delete_target_unreadable",
            **target,
        )
        if current is None:
            # A YAML-package or !include item has no config GET either.
            raise Refusal(
                "configuration_delete_target_not_found", kind="not_found", **target
            )
        if not isinstance(current, dict) or not resource_identity_matches(
            resource_type, target_id, current
        ):
            raise Refusal(
                "configuration_delete_target_unreviewable",
                kind="unreviewable",
                **target,
            )
        entry = await _read(
            gateway.registry_entry(resource_type, target_id),
            "configuration_delete_registry_unreadable",
            **target,
        )
        if entry is None:
            # Without the entry Core would not unload the entity, and the
            # runtime half of the absence proof could not be read.
            raise Refusal(
                "configuration_delete_registry_entry_missing",
                kind="not_found",
                **target,
            )
        entity_id = entry["entity_id"]
        registry_entry_id = entry["id"]
        evidence = await _read(
            gateway.consumer_evidence(entity_id),
            "configuration_delete_dependency_evidence_unavailable",
            **target,
        )
        if evidence is None:
            # The fenced index reader is not wired; preflight could never
            # recheck the bound evidence, so nothing is planned.
            raise Refusal(
                "configuration_delete_dependency_evidence_unavailable",
                kind="unavailable",
                **target,
            )
        normalized_current = normalize_resource_config(resource_type, current)
        comparison = compare_removed_effects(resource_type, normalized_current)
        dependency_reasons = sorted(set(evidence["dependency_reasons"]))[
            : delete_contract.MAX_DEPENDENCY_REASONS
        ]
        basis = expected_basis(comparison, dependency_reasons)
        effects = _effect_rows(comparison)
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
        statements = _statements(
            resource_type,
            target_id,
            entity_id,
            effects,
            comparison.unlock_or_disarm,
            consumers,
        )
        recreate_errors = _recreate_errors(
            resource_type, target_id, current, known_secrets
        )
        recreate_eligible = not recreate_errors
        warnings = [CONSUMER_COVERAGE_WARNING, RUNTIME_NOTE]
        if any(
            code.startswith("called_script_")
            for row in effects
            for code in row["reason_codes"]
        ):
            warnings.append(SCRIPT_CALL_WARNING)
        recreate_disclosure = (
            ROLLBACK_DISCLOSURE
            if recreate_eligible
            else NO_ROLLBACK_DISCLOSURE.format(
                reason="; ".join(recreate_errors)[:200]
            )
        )
        # Shown to the approver with every child, not only in the dry run.
        warnings.append(recreate_disclosure)
        intent = delete_contract.build_intent(
            resource_type=resource_type,
            target_id=target_id,
            entity_id=entity_id,
            registry_entry_id=registry_entry_id,
            pre_image_fingerprint=resource_fingerprint(resource_type, current),
            consumer_evidence_fingerprint=delete_contract.consumer_fingerprint(
                evidence
            ),
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
                "removed_effect_verdict": comparison.verdict,
                "unlock_or_disarm": comparison.unlock_or_disarm,
                "dependency_reasons": dependency_reasons,
            },
            "removed_effects": effects,
            "consumers": consumers,
            "consequence_statements": statements,
            "warnings": [item[:500] for item in warnings],
            "readback_proves": list(READBACK_PROVES),
            "recreate": {
                "eligible": recreate_eligible,
                "window_days": RECREATE_WINDOW_DAYS,
                "disclosure": recreate_disclosure,
            },
            "provider": delete_contract.PROVIDER,
            "fallback": "none",
        }
        items.append(
            Item(
                operation_id=f"delete_{index + 1}",
                resource_type=resource_type,
                target_id=target_id,
                entity_id=entity_id,
                registry_entry_id=registry_entry_id,
                current=current,
                intent=intent,
                disclosure=disclosure,
                classification=classify(basis, statements, disclosure["warnings"]),
            )
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
            "provider": delete_contract.PROVIDER,
            "fallback": "none",
            "rollback": "new_separately_approved_recreate_plan",
        },
    )


def evidence_for_snapshot(snapshot: Any | None, entity_id: str) -> dict[str, Any]:
    """The 069B consumer evidence for one deleted entity (tests and tooling)."""

    return _consumer_evidence(snapshot, entity_id)


def projection(item: Item) -> dict[str, Any]:
    """Return the bounded per-child plan projection."""

    return {
        **item.disclosure,
        "policy_basis": {
            "policy": item.classification["policy"],
            "physical_consequence": item.classification["physical_consequence"],
            "basis": list(item.classification["basis"]),
        },
    }


__all__ = [
    "ConfigurationDeleteRefusal",
    "Item",
    "MAX_DELETIONS",
    "POLICY_TRIGGER",
    "Preparation",
    "Request",
    "compare_removed_effects",
    "expected_basis",
    "prepare",
    "projection",
    "risk_assessment",
    "validate_request",
]
