"""Planning rules for one governed entity registry metadata update (HAMCP-069A-1).

Input is validated before any Home Assistant read (§1.1, §1.2, D7).  Planning
reads the exact registry entry and the referenced registries, derives the
complete final entry, applies the Home Assistant contract refusals (§2.3,
V4), and classifies the change under the existing F2 classes (§5, D3, D4).
Nothing here dispatches.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..errors import AuthorizationError
from .entity_metadata import (
    ENTITY_ID_PATTERN,
    ENTITY_METADATA_BINDINGS_KEY,
    ENTITY_METADATA_PROVIDER,
    ICON_PATTERN,
    MAX_ENTITY_ID_LENGTH,
    MAX_REFERENCE_ID_LENGTH,
    SLUG_PATTERN,
    area_requires_own_name,
    bound_reference_ids,
    effective_device_class,
    entry_errors,
    entry_fingerprint,
    normalize_entry,
    required_final_references,
    touched_fields,
)
from .entity_metadata_disclosure import (
    EntityMetadataEvidenceUnavailable,
    build_consumer_disclosure,
    consumer_evidence_fingerprint,
    membership_references,
    physical_consumer_consequence,
    snapshot_identity,
)
from .models import ChangeRiskAssessment, RiskLevel
from .risk import (
    _HELPER_DIRECT_EFFECT_DOMAINS,
    _HELPER_SAFETY_CRITICAL_DOMAINS,
    _HELPER_TRANSITIVE_EFFECT_DOMAINS,
)

METADATA_INPUT_FIELDS = (
    "name",
    "icon",
    "aliases",
    "area_id",
    "hidden",
    "add_labels",
    "remove_labels",
    "categories",
)
ALLOWED_INPUT_FIELDS = frozenset({"entity_id", "expiration_minutes", *METADATA_INPUT_FIELDS})
FIELD_NOT_SUPPORTED = "entity_metadata_field_not_supported"
POLICY_TRIGGER = "entity_metadata_policy"
ALIAS_ELEVATED_COVER_CLASSES = frozenset({"garage", "gate", "door"})
ALIAS_ELEVATED_DOMAINS = frozenset({"lock", "alarm_control_panel"})
MAX_ALIASES = 16
MAX_LABEL_CHANGES = 16
MAX_CATEGORY_SCOPES = 8
MAX_TEXT = 100
ALREADY_HIDDEN_BY_INTEGRATION = "entity_metadata_already_hidden_by_integration"
ALREADY_HIDDEN_BY_INTEGRATION_REASON = (
    "already hidden by the integration (hidden_by=integration)"
)
ROLLBACK_DISCLOSURE = (
    "Restores the values captured before plan {plan_id}, replacing the current "
    "values of these fields. If the entity was edited since, this plan is "
    "refused as stale; it never merges."
)


class EntityMetadataRefusal(Exception):
    """A deterministic refusal with a design reason code; no plan exists."""

    def __init__(self, reason_code: str, *, kind: str = "invalid_request", **details: Any) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.kind = kind
        self.details = details


@dataclass(frozen=True)
class EntityMetadataRequest:
    entity_id: str
    values: dict[str, Any]
    expiration_minutes: int


@dataclass
class EntityMetadataPreparation:
    """Everything the configuration-plan builder needs; nothing dispatched."""

    request: EntityMetadataRequest
    current: dict[str, Any]
    final: dict[str, Any]
    touched: tuple[str, ...]
    already_satisfied: list[str]
    refused_no_op: list[dict[str, str]]
    disclosure: dict[str, Any]
    classification: dict[str, Any]
    snapshot: dict[str, Any] | None = None
    no_change: dict[str, Any] | None = field(default=None)


def _text(value: Any, field_name: str, *, pattern=None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_TEXT
        or (pattern is not None and not pattern.fullmatch(value))
    ):
        raise EntityMetadataRefusal("entity_metadata_invalid_value", field=field_name)
    return value


def _reference_ids(value: Any, field_name: str) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > MAX_LABEL_CHANGES
        or not all(
            isinstance(item, str) and 0 < len(item) <= MAX_REFERENCE_ID_LENGTH
            for item in value
        )
        or len(set(value)) != len(value)
    ):
        raise EntityMetadataRefusal("entity_metadata_invalid_value", field=field_name)
    return list(value)


def validate_request(arguments: Any) -> EntityMetadataRequest:
    """Validate the closed input before any Home Assistant read."""

    if not isinstance(arguments, dict) or not all(isinstance(key, str) for key in arguments):
        raise EntityMetadataRefusal("entity_metadata_invalid_request")
    unsupported = sorted(set(arguments) - ALLOWED_INPUT_FIELDS)
    if unsupported:
        # §1.2 and D7: rename, enablement, exposure, options, device fields,
        # multi-entity targets, create_labels and unknown keys are refused as
        # a whole; nothing is partially accepted.
        raise EntityMetadataRefusal(FIELD_NOT_SUPPORTED, fields=unsupported[:16])
    entity_id = arguments.get("entity_id")
    if (
        not isinstance(entity_id, str)
        or len(entity_id) > MAX_ENTITY_ID_LENGTH
        or not ENTITY_ID_PATTERN.fullmatch(entity_id)
    ):
        raise EntityMetadataRefusal("entity_metadata_invalid_value", field="entity_id")
    expiration = arguments.get("expiration_minutes", 120)
    if type(expiration) is not int or not 5 <= expiration <= 1440:
        raise EntityMetadataRefusal("entity_metadata_invalid_value", field="expiration_minutes")
    values: dict[str, Any] = {}
    if "name" in arguments:
        values["name"] = _text(arguments["name"], "name")
    if "icon" in arguments:
        values["icon"] = _text(arguments["icon"], "icon", pattern=ICON_PATTERN)
    if "aliases" in arguments:
        aliases = arguments["aliases"]
        if not isinstance(aliases, list) or len(aliases) > MAX_ALIASES:
            raise EntityMetadataRefusal("entity_metadata_invalid_value", field="aliases")
        folded: set[str] = set()
        nulls = 0
        for alias in aliases:
            if alias is None:
                nulls += 1
                continue
            if (
                not isinstance(alias, str)
                or not 1 <= len(alias) <= MAX_TEXT
                or alias != alias.strip()
                or alias.casefold() in folded
            ):
                raise EntityMetadataRefusal("entity_metadata_invalid_value", field="aliases")
            folded.add(alias.casefold())
        if nulls > 1:
            raise EntityMetadataRefusal("entity_metadata_invalid_value", field="aliases")
        values["aliases"] = list(aliases)
    if "area_id" in arguments:
        area_id = arguments["area_id"]
        if area_id is not None and (
            not isinstance(area_id, str)
            or not 0 < len(area_id) <= MAX_REFERENCE_ID_LENGTH
        ):
            raise EntityMetadataRefusal("entity_metadata_invalid_value", field="area_id")
        values["area_id"] = area_id
    if "hidden" in arguments:
        if type(arguments["hidden"]) is not bool:
            raise EntityMetadataRefusal("entity_metadata_invalid_value", field="hidden")
        values["hidden"] = arguments["hidden"]
    for name in ("add_labels", "remove_labels"):
        if name in arguments:
            values[name] = _reference_ids(arguments[name], name)
    if set(values.get("add_labels", ())) & set(values.get("remove_labels", ())):
        raise EntityMetadataRefusal("entity_metadata_label_overlap")
    if "categories" in arguments:
        categories = arguments["categories"]
        if (
            not isinstance(categories, dict)
            or not 1 <= len(categories) <= MAX_CATEGORY_SCOPES
            or not all(
                isinstance(scope, str)
                and 0 < len(scope) <= MAX_REFERENCE_ID_LENGTH
                and SLUG_PATTERN.fullmatch(scope)
                and (
                    category is None
                    or (
                        isinstance(category, str)
                        and 0 < len(category) <= MAX_REFERENCE_ID_LENGTH
                    )
                )
                for scope, category in categories.items()
            )
        ):
            raise EntityMetadataRefusal("entity_metadata_invalid_value", field="categories")
        values["categories"] = dict(categories)
    if not values:
        raise EntityMetadataRefusal("entity_metadata_no_fields")
    return EntityMetadataRequest(entity_id, values, expiration)


def derive_final(
    current: dict[str, Any], request: EntityMetadataRequest
) -> tuple[dict[str, Any], list[str], list[dict[str, str]]]:
    """Return the complete final entry, satisfied fields and no-op refusals."""

    values = request.values
    final = copy.deepcopy(current)
    final.pop(ENTITY_METADATA_BINDINGS_KEY, None)
    satisfied: list[str] = []
    refused: list[dict[str, str]] = []
    for name in ("name", "icon", "area_id"):
        if name in values:
            if current.get(name) == values[name]:
                satisfied.append(name)
            final[name] = values[name]
    if "aliases" in values:
        if list(current.get("aliases") or []) == values["aliases"]:
            satisfied.append("aliases")
        final["aliases"] = list(values["aliases"])
    if "hidden" in values:
        hidden_by = current.get("hidden_by")
        if values["hidden"]:
            if hidden_by == "integration":
                # V4: hiding is integration-owned; converting it to user
                # hiding is a real change and reporting it as done would be
                # untrue.  The field is refused as a no-op and untouched.
                refused.append(
                    {
                        "field": "hidden",
                        "reason_code": ALREADY_HIDDEN_BY_INTEGRATION,
                        "reason": ALREADY_HIDDEN_BY_INTEGRATION_REASON,
                    }
                )
            elif hidden_by == "user":
                satisfied.append("hidden")
            else:
                final["hidden_by"] = "user"
        else:
            if hidden_by == "integration":
                raise EntityMetadataRefusal("entity_metadata_integration_hidden")
            if hidden_by is None:
                satisfied.append("hidden")
            final["hidden_by"] = None
    labels = list(current.get("labels") or [])
    for label_id in values.get("add_labels", ()):
        if label_id in labels:
            satisfied.append(f"add_labels:{label_id}")
        else:
            labels.append(label_id)
    for label_id in values.get("remove_labels", ()):
        if label_id in labels:
            labels.remove(label_id)
        else:
            satisfied.append(f"remove_labels:{label_id}")
    if "add_labels" in values or "remove_labels" in values:
        final["labels"] = sorted(set(labels))
    if "categories" in values:
        merged = dict(current.get("categories") or {})
        for scope, category in values["categories"].items():
            if merged.get(scope) == category:
                satisfied.append(f"categories:{scope}")
            elif category is None:
                merged.pop(scope, None)
            else:
                merged[scope] = category
        final["categories"] = {scope: merged[scope] for scope in sorted(merged)}
    return final, sorted(satisfied), refused


def classify(
    current: dict[str, Any],
    touched: tuple[str, ...],
    disclosure: dict[str, Any],
) -> dict[str, Any]:
    """Classify under §5 (D3, D4): standard or elevated, never prohibited."""

    entity_id = current["entity_id"]
    domain = entity_id.split(".", 1)[0]
    touched_set = set(touched)
    basis: list[str] = []
    rank = {"none": 0, "indirect": 1, "direct": 2, "safety_critical": 3}
    consequence = "none"

    def raise_to(value: str) -> None:
        nonlocal consequence
        if rank[value] > rank[consequence]:
            consequence = value

    actuator_consequence = (
        "safety_critical" if domain in _HELPER_SAFETY_CRITICAL_DOMAINS else "indirect"
    )
    if touched_set & {"labels", "area_id"} and domain in (
        _HELPER_DIRECT_EFFECT_DOMAINS | _HELPER_TRANSITIVE_EFFECT_DOMAINS
    ):
        basis.append("e1_actuator_membership_change")
        raise_to(actuator_consequence)
    device_class = effective_device_class(current)
    if "aliases" in touched_set and (
        domain in ALIAS_ELEVATED_DOMAINS
        or (domain == "cover" and device_class in ALIAS_ELEVATED_COVER_CLASSES)
    ):
        basis.append("d4_safety_alias_change")
        raise_to(actuator_consequence)
    consumer = physical_consumer_consequence(disclosure)
    if consumer is not None:
        basis.append("e2_indexed_physical_consumer")
        raise_to(consumer)
    return {
        "policy": "elevated" if basis else "standard",
        "physical_consequence": consequence,
        "basis": basis,
        "domain": domain,
        "device_class": device_class,
    }


def risk_assessment(operation_id: str, classification: dict[str, Any]) -> ChangeRiskAssessment:
    """Return the risk record that F2 policy reads for this resource type."""

    elevated = classification["policy"] == "elevated"
    reasons = (
        [f"Entity metadata change elevated: {', '.join(classification['basis'])}"]
        if elevated
        else ["Entity metadata change with no physical membership effect"]
    )
    return ChangeRiskAssessment(
        level=RiskLevel.HIGH if elevated else RiskLevel.LOW,
        reasons=reasons,
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
        warnings=[],
    )


def _field_projection(current: dict[str, Any], final: dict[str, Any], touched: tuple[str, ...]) -> dict[str, Any]:
    before = normalize_entry(current) or {}
    after = normalize_entry(final) or {}
    return {name: {"current": before[name], "final": after[name]} for name in touched}


def projection(prepared: EntityMetadataPreparation, *, plan_id: str | None = None) -> dict[str, Any]:
    """Return the bounded ``entity_metadata`` plan projection (§1.3)."""

    return {
        "entity_id": prepared.request.entity_id,
        "registry_id": prepared.current.get("id"),
        "fields": _field_projection(prepared.current, prepared.final, prepared.touched),
        "already_satisfied": list(prepared.already_satisfied),
        "refused_no_op": [dict(item) for item in prepared.refused_no_op],
        "labels_to_create": [],
        "consumer_disclosure": prepared.disclosure,
        "dependency_snapshot": prepared.snapshot,
        "policy_basis": dict(prepared.classification),
        "disabled_entity": prepared.current.get("disabled_by") is not None,
        "rollback_disclosure": ROLLBACK_DISCLOSURE.format(plan_id=plan_id or "<this plan>"),
        "provider": ENTITY_METADATA_PROVIDER,
        "fallback": "none",
    }


def _no_change(
    request: EntityMetadataRequest,
    current: dict[str, Any],
    satisfied: list[str],
    refused: list[dict[str, str]],
) -> dict[str, Any]:
    base = {
        "status": "no_change",
        "plan_created": False,
        "proposal_only": True,
        "provider_dispatch_occurred": False,
        "target_type": "entity_registry_metadata",
        "target_id": request.entity_id,
        "already_satisfied": list(satisfied),
        "observed_state_fingerprint": entry_fingerprint(current),
        "provider": ENTITY_METADATA_PROVIDER,
        "fallback": "none",
        "fallback_occurred": False,
    }
    if refused:
        # V4: a refused no-op is not "already done".
        first = refused[0]
        return {
            **base,
            "outcome": "refused_no_op",
            "verified": False,
            "reason_code": first["reason_code"],
            "reason": first["reason"],
            "refused_no_op": [dict(item) for item in refused],
        }
    return {**base, "outcome": "already_in_desired_state", "verified": True}


async def prepare(
    gateway: Any,
    snapshot_reader: Callable[..., Awaitable[Any]],
    request: EntityMetadataRequest,
    *,
    current_override: dict[str, Any] | None = None,
    final_override: dict[str, Any] | None = None,
) -> EntityMetadataPreparation:
    """Read, derive and classify one update.  Rollback supplies overrides."""

    entity_id = request.entity_id
    try:
        current = await gateway.read_entry(entity_id)
        if current is None:
            exists = await gateway.state_exists(entity_id)
            raise EntityMetadataRefusal(
                "entity_not_registry_managed" if exists else "entity_not_found",
                kind="not_found",
            )
    except AuthorizationError:
        raise EntityMetadataRefusal(
            "entity_registry_permission_failure", kind="permission"
        ) from None
    if entry_errors(entity_id, current):
        raise EntityMetadataRefusal("entity_metadata_entry_unreviewable", kind="unreviewable")
    if current_override is not None:
        # Rollback: the forward plan's final state must still be current.
        if entry_fingerprint(current) != entry_fingerprint(current_override):
            raise EntityMetadataRefusal("entry_changed_since_apply", kind="rollback")
        final = copy.deepcopy(final_override or {})
        final.pop(ENTITY_METADATA_BINDINGS_KEY, None)
        satisfied: list[str] = []
        refused: list[dict[str, str]] = []
    else:
        final, satisfied, refused = derive_final(current, request)
    touched = touched_fields(current, final)
    if not touched:
        prepared = EntityMetadataPreparation(
            request, current, final, touched, satisfied, refused, {}, {}
        )
        prepared.no_change = _no_change(request, current, satisfied, refused)
        return prepared
    if area_requires_own_name(current, final):
        raise EntityMetadataRefusal("entity_metadata_area_requires_own_name")
    try:
        definitions = await gateway.reference_definitions(
            bound_reference_ids(current, final)
        )
    except AuthorizationError:
        raise EntityMetadataRefusal(
            "entity_registry_permission_failure", kind="permission"
        ) from None
    required = required_final_references(current, final)
    missing = (
        [f"label:{item}" for item in required["labels"] if definitions["labels"].get(item) is None]
        + [f"area:{item}" for item in required["areas"] if definitions["areas"].get(item) is None]
        + [
            f"category:{scope}:{item}"
            for scope, item in required["categories"].items()
            if definitions["categories"].get(scope, {}).get(item) is None
        ]
    )
    if missing:
        raise EntityMetadataRefusal(
            "rollback_reference_missing" if current_override is not None else "entity_metadata_reference_not_found",
            kind="rollback" if current_override is not None else "invalid_request",
            references=missing[:16],
        )
    references = membership_references(current, final, definitions)
    snapshot = None
    try:
        if references["label"] or references["area"]:
            snapshot = await snapshot_reader(fenced=False)
        # A reader that answers with no snapshot is refused here as well.
        disclosure = build_consumer_disclosure(
            snapshot,
            entity_id=entity_id,
            current=current,
            final=final,
            reference_definitions=definitions,
        )
    except EntityMetadataEvidenceUnavailable:
        raise EntityMetadataRefusal(
            "dependency_snapshot_unavailable", kind="unavailable"
        ) from None
    final[ENTITY_METADATA_BINDINGS_KEY] = {
        "reference_definitions": definitions,
        "consumer_evidence_fingerprint": consumer_evidence_fingerprint(disclosure),
    }
    return EntityMetadataPreparation(
        request=request,
        current=current,
        final=final,
        touched=touched,
        already_satisfied=satisfied,
        refused_no_op=refused,
        disclosure=disclosure,
        classification=classify(current, touched, disclosure),
        snapshot=snapshot_identity(snapshot),
    )
