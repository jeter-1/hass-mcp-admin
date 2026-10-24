"""Planning rules for governed label create, edit and assignment (HAMCP-165).

Input is validated before any Home Assistant read (design §2, §3).  Planning
lists the label registry once, simulates the plan's label children in order
over that namespace (§3, §4), and binds each child's exact baseline and the
namespace fingerprint as of just before it.  Entity assignment children are
prepared by the signed HAMCP-069A planning path unchanged, over an overlay
that answers for the plan's own labels with their FINAL id and name: the
exact definitions the registry holds once the earlier label children have
succeeded.  Nothing here dispatches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..errors import AuthorizationError
from . import entity_metadata_planning
from .entity_metadata import (
    ENTITY_METADATA_RESOURCE_TYPE,
    MAX_REGISTRY_LIST_ITEMS,
    EntityMetadataProviderError,
    _bounded_text,
    validate_entity_id,
)
from .entity_metadata_planning import EntityMetadataRefusal
from .label_registry import (
    BINDINGS_KEY,
    FIELD_ERROR_CODES,
    LABEL_FIELDS,
    LABEL_ID_PATTERN,
    LABEL_PROVIDER,
    LABEL_RESOURCE_TYPE,
    NEVER_SENT_COMMANDS,
    changed_fields,
    field_valid,
    namespace_fingerprint,
    normalize_entry,
    normalize_name,
    predicted_label_id,
)
from .models import ChangeRiskAssessment, RiskLevel

MAX_CHILDREN = 8
POLICY_TRIGGER = "label_registry_policy"
FIELD_NOT_SUPPORTED = "label_field_not_supported"
INVALID_REQUEST = "invalid_label_request"
TOO_MANY_OPERATIONS = "label_plan_too_many_operations"
ROLLBACK_NOT_SUPPORTED = "label_plan_rollback_not_supported"
DISCLOSURE_MODEL = "label_registry_plan_disclosure_v1"
_CREATE_KEYS = frozenset({"operation", "entity_ids", *LABEL_FIELDS})
_UPDATE_KEYS = frozenset({"operation", "label_id", "entity_ids", *LABEL_FIELDS})
NON_ATOMIC_DISCLOSURE = (
    "Not atomic: children run in order and the plan stops on the first "
    "failure. A label created or edited by an earlier child stays in place "
    "when a later child fails."
)
ROLLBACK_DISCLOSURE = (
    "Rollback is not available for a label plan "
    "(label_plan_rollback_not_supported): undoing a create needs label "
    "deletion, which is HAMCP-155. A corrective edit is a new plan."
)
LABEL_RISK_REASON = (
    "Label registry metadata change; no device or physical effect"
)


@dataclass(frozen=True)
class LabelOperationRequest:
    index: int
    operation: str
    label_id: str | None
    values: dict[str, Any]
    entity_ids: tuple[str, ...]


@dataclass(frozen=True)
class Request:
    operations: tuple[LabelOperationRequest, ...]
    expiration_minutes: int

    @property
    def label_ids(self) -> tuple[str, ...]:
        """Each child's exact target: predicted slug (create) or named id."""

        return tuple(
            operation.label_id
            if operation.label_id is not None
            else predicted_label_id(operation.values["name"])
            for operation in self.operations
        )

    @property
    def entity_ids(self) -> tuple[str, ...]:
        """Every assigned entity once, in first-appearance order."""

        seen: dict[str, None] = {}
        for operation in self.operations:
            for entity_id in operation.entity_ids:
                seen.setdefault(entity_id, None)
        return tuple(seen)


@dataclass
class Item:
    """One plan child: a label child or an entity assignment child."""

    operation_id: str
    resource_type: str
    action: str
    target_id: str
    current: dict[str, Any] | None
    final: dict[str, Any]
    depends_on: list[str]
    classification: dict[str, Any]
    changed: tuple[str, ...] = ()
    entity: Any | None = None  # EntityMetadataPreparation for an entity child
    created_labels: tuple[str, ...] = ()


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

    def operations(self) -> list[dict[str, Any]]:
        return [
            {
                "operation_id": item.operation_id,
                "resource_type": item.resource_type,
                "action": item.action,
                "target_id": item.target_id,
                "proposed_config": item.final,
                **({"depends_on": list(item.depends_on)} if item.depends_on else {}),
            }
            for item in self.items
        ]


def _refuse(reason_code: str, **details: Any) -> EntityMetadataRefusal:
    return EntityMetadataRefusal(reason_code, **details)


def _validate_operation(index: int, raw: Any) -> LabelOperationRequest:
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        raise _refuse(INVALID_REQUEST, operation_index=index)
    operation = raw.get("operation")
    if operation not in ("create", "update"):
        raise _refuse(INVALID_REQUEST, operation_index=index, field="operation")
    allowed = _CREATE_KEYS if operation == "create" else _UPDATE_KEYS
    unsupported = sorted(set(raw) - allowed)
    if unsupported:
        # Deletion, device or area assignment, label_id on create and any
        # unknown key are refused as a whole; nothing is partially accepted.
        raise _refuse(
            FIELD_NOT_SUPPORTED, operation_index=index, fields=unsupported[:16]
        )
    label_id = None
    if operation == "update":
        label_id = raw.get("label_id")
        if not isinstance(label_id, str) or not LABEL_ID_PATTERN.fullmatch(label_id):
            raise _refuse("invalid_label_id", operation_index=index)
    elif "name" not in raw:
        raise _refuse(
            FIELD_ERROR_CODES["name"], operation_index=index, field="name"
        )
    values: dict[str, Any] = {}
    for name in LABEL_FIELDS:
        if name in raw:
            if not field_valid(name, raw[name]):
                raise _refuse(
                    FIELD_ERROR_CODES[name], operation_index=index, field=name
                )
            values[name] = raw[name]
    if operation == "update" and not values:
        raise _refuse("label_no_change", operation_index=index, label_id=label_id)
    entity_ids = raw.get("entity_ids", [])
    if not isinstance(entity_ids, list) or len(entity_ids) > MAX_CHILDREN:
        raise _refuse(INVALID_REQUEST, operation_index=index, field="entity_ids")
    validated: list[str] = []
    for value in entity_ids:
        try:
            validated.append(validate_entity_id(value))
        except ValueError:
            raise _refuse(
                INVALID_REQUEST, operation_index=index, field="entity_ids"
            ) from None
    if len(set(validated)) != len(validated):
        raise _refuse("duplicate_label_request", operation_index=index, field="entity_ids")
    return LabelOperationRequest(index, operation, label_id, values, tuple(validated))


def validate_request(arguments: Any) -> Request:
    """Validate the closed input before any Home Assistant read."""

    if not isinstance(arguments, dict) or not all(
        isinstance(key, str) for key in arguments
    ):
        raise _refuse(INVALID_REQUEST)
    unsupported = sorted(set(arguments) - {"operations", "expiration_minutes"})
    if unsupported:
        raise _refuse(FIELD_NOT_SUPPORTED, fields=unsupported[:16])
    operations = arguments.get("operations")
    if not isinstance(operations, list) or not operations:
        raise _refuse(INVALID_REQUEST, field="operations")
    if len(operations) > MAX_CHILDREN:
        raise _refuse(TOO_MANY_OPERATIONS, limit=MAX_CHILDREN)
    expiration = arguments.get("expiration_minutes", 120)
    if type(expiration) is not int or not 5 <= expiration <= 1440:
        raise _refuse(INVALID_REQUEST, field="expiration_minutes")
    validated = tuple(
        _validate_operation(index, raw) for index, raw in enumerate(operations)
    )
    updated = [item.label_id for item in validated if item.label_id is not None]
    created = [
        normalize_name(item.values["name"])
        for item in validated
        if item.operation == "create"
    ]
    if len(set(updated)) != len(updated) or len(set(created)) != len(created):
        raise _refuse("duplicate_label_request")
    request = Request(validated, expiration)
    if len(set(request.label_ids)) != len(request.label_ids):
        # Two children would target one exact label id (for example a create
        # whose slug is an id the same plan edits).
        raise _refuse("duplicate_label_request")
    # Design §8: the bound is on children, label children plus one merged
    # assignment child per entity, and is enforced before any read.
    if len(validated) + len(request.entity_ids) > MAX_CHILDREN:
        raise _refuse(TOO_MANY_OPERATIONS, limit=MAX_CHILDREN)
    return request


def classify() -> dict[str, Any]:
    """Design §7: a label child is registry metadata with no device effect."""

    return {"policy": "standard", "physical_consequence": "none", "basis": []}


def risk_assessment(
    operation_id: str, item: Item
) -> ChangeRiskAssessment:
    """Return the risk record that F2 policy reads for a label child."""

    return ChangeRiskAssessment(
        level=RiskLevel.LOW,
        reasons=[LABEL_RISK_REASON],
        apply_allowed=True,
        evidence=[
            {
                "field": operation_id,
                "trigger": POLICY_TRIGGER,
                "policy": "standard",
                "physical_consequence": "none",
                "operation": item.action,
                "basis": "",
            }
        ],
        warnings=[NON_ATOMIC_DISCLOSURE],
    )


def projection(
    prepared: Preparation, item: Item, *, plan_id: str | None = None
) -> dict[str, Any]:
    """Return the bounded per-child plan projection."""

    if item.resource_type == ENTITY_METADATA_RESOURCE_TYPE:
        result = entity_metadata_planning.projection(item.entity, plan_id=plan_id)
        result["labels_to_create"] = list(item.created_labels)
        result["rollback_disclosure"] = ROLLBACK_DISCLOSURE
        result["label_plan_depends_on"] = list(item.depends_on)
        return result
    current = normalize_entry(item.current) or {}
    final = normalize_entry(item.final) or {}
    return {
        "label_id": item.target_id,
        "operation": item.action,
        "fields": {
            name: {"current": current.get(name), "final": final[name]}
            for name in item.changed
        },
        "namespace_fingerprint": item.final[BINDINGS_KEY]["namespace_fingerprint"],
        "label_id_derivation": (
            "exact_core_slug" if item.action == "create" else "existing_label_id"
        ),
        "physical_effect": "none",
        "non_atomic_disclosure": NON_ATOMIC_DISCLOSURE,
        "rollback_disclosure": ROLLBACK_DISCLOSURE,
        "provider": LABEL_PROVIDER,
        "fallback": "none",
    }


class PlannedLabelOverlay:
    """Answer label reads with the plan's FINAL labels, all else unchanged.

    Only ``labels`` and ``reference_definitions`` differ from the signed
    069A gateway; every other read is delegated.  The overlay never writes.
    """

    def __init__(self, inner: Any, planned: dict[str, dict[str, Any]]) -> None:
        self._inner = inner
        self._planned = {key: dict(value) for key, value in planned.items()}

    def __getattr__(self, name: str) -> Any:
        if name == "update":
            raise AttributeError(name)
        return getattr(self._inner, name)

    async def labels(self) -> dict[str, dict[str, Any]]:
        result = dict(await self._inner.labels())
        result.update({key: dict(value) for key, value in self._planned.items()})
        return result

    async def reference_definitions(self, ids: dict[str, Any]) -> dict[str, Any]:
        result = await self._inner.reference_definitions(ids)
        labels = dict(result.get("labels") or {})
        for label_id in list(labels):
            if label_id in self._planned:
                labels[label_id] = dict(self._planned[label_id])
        return {**result, "labels": labels}


def _permission() -> EntityMetadataRefusal:
    return EntityMetadataRefusal("label_registry_permission_failure", kind="permission")


async def prepare(
    label_gateway: Any,
    entity_gateway: Any,
    snapshot_reader: Callable[..., Awaitable[Any]],
    request: Request,
) -> Preparation:
    """Read, simulate, bind and classify every child.  Nothing dispatches."""

    try:
        entries = await label_gateway.entries()
    except AuthorizationError:
        raise _permission() from None
    except EntityMetadataProviderError:
        raise EntityMetadataRefusal(
            "label_registry_unreviewable", kind="unavailable"
        ) from None
    names = {label_id: entry["name"] for label_id, entry in entries.items()}
    touched: set[str] = set()
    items: list[Item] = []
    label_op_ids: dict[int, str] = {}
    final_ids: dict[int, str] = {}
    planned: dict[str, dict[str, Any]] = {}
    created: set[str] = set()
    for operation in request.operations:
        fingerprint = namespace_fingerprint(names)
        operation_id = f"label_{operation.index + 1}"
        if operation.operation == "create":
            name = operation.values["name"]
            folded = normalize_name(name)
            holders = sorted(
                label_id
                for label_id, existing in names.items()
                if normalize_name(existing) == folded
            )
            if holders:
                if set(holders) & touched:
                    raise _refuse(
                        "duplicate_label_request", operation_index=operation.index
                    )
                raise _refuse(
                    "label_already_exists",
                    operation_index=operation.index,
                    label_id=holders[0],
                )
            label_id = predicted_label_id(name)
            if label_id in names:
                # 069A §6, kept (design §3.1): no suffix prediction.
                raise _refuse(
                    "duplicate_label_request"
                    if label_id in touched
                    else "label_id_collision",
                    operation_index=operation.index,
                    label_id=label_id,
                )
            if len(names) + 1 > MAX_REGISTRY_LIST_ITEMS:
                raise EntityMetadataRefusal(
                    "label_registry_unbounded", kind="unavailable"
                )
            current = None
            final = {
                "label_id": label_id,
                **{name_: operation.values.get(name_) for name_ in LABEL_FIELDS},
            }
            created.add(label_id)
        else:
            label_id = operation.label_id
            if label_id in touched:
                raise _refuse(
                    "duplicate_label_request",
                    operation_index=operation.index,
                    label_id=label_id,
                )
            if label_id not in names:
                raise EntityMetadataRefusal(
                    "label_not_found",
                    kind="not_found",
                    operation_index=operation.index,
                    label_id=label_id,
                )
            current = normalize_entry(entries[label_id])
            final = {**current, **operation.values}
            if not changed_fields(current, final):
                raise _refuse(
                    "label_no_change",
                    operation_index=operation.index,
                    label_id=label_id,
                )
            if final["name"] != current["name"]:
                folded = normalize_name(final["name"])
                holders = sorted(
                    other
                    for other, existing in names.items()
                    if other != label_id and normalize_name(existing) == folded
                )
                if holders:
                    raise _refuse(
                        "duplicate_label_request"
                        if set(holders) & touched
                        else "label_name_in_use",
                        operation_index=operation.index,
                        label_id=label_id,
                    )
        changed = tuple(sorted(changed_fields(current, final)))
        final[BINDINGS_KEY] = {
            "operation": operation.operation,
            "changed_fields": list(changed),
            "namespace_fingerprint": fingerprint,
        }
        names[label_id] = final["name"]
        touched.add(label_id)
        label_op_ids[operation.index] = operation_id
        final_ids[operation.index] = label_id
        planned[label_id] = {"label_id": label_id, "name": _bounded_text(final["name"])}
        items.append(
            Item(
                operation_id=operation_id,
                resource_type=LABEL_RESOURCE_TYPE,
                action=operation.operation,
                target_id=label_id,
                current=current,
                final=final,
                depends_on=[],
                classification=classify(),
                changed=changed,
            )
        )

    overlay = PlannedLabelOverlay(entity_gateway, planned)
    for entity_index, entity_id in enumerate(request.entity_ids):
        sources = [
            operation.index
            for operation in request.operations
            if entity_id in operation.entity_ids
        ]
        label_ids = [final_ids[index] for index in sources]
        entity_request = entity_metadata_planning.validate_request(
            {
                "entity_id": entity_id,
                "add_labels": label_ids,
                "expiration_minutes": request.expiration_minutes,
            }
        )
        entity = await entity_metadata_planning.prepare(
            overlay, snapshot_reader, entity_request
        )
        if entity.no_change is not None:
            raise _refuse(
                "entity_label_already_assigned",
                entity_id=entity_id,
                labels=label_ids[:16],
            )
        items.append(
            Item(
                operation_id=f"assign_{entity_index + 1}",
                resource_type=ENTITY_METADATA_RESOURCE_TYPE,
                action="update",
                target_id=entity_id,
                current=entity.current,
                final=entity.final,
                depends_on=[label_op_ids[index] for index in sources],
                classification=entity.classification,
                entity=entity,
                created_labels=tuple(
                    label_id for label_id in label_ids if label_id in created
                ),
            )
        )
    if len(items) > MAX_CHILDREN:
        raise _refuse(TOO_MANY_OPERATIONS, limit=MAX_CHILDREN)
    prepared = Preparation(request, items)
    prepared.plan_disclosure = {
        "model": DISCLOSURE_MODEL,
        "children": [
            {
                "operation_id": item.operation_id,
                "resource_type": item.resource_type,
                "action": item.action,
                "target_id": item.target_id,
                "depends_on": list(item.depends_on),
            }
            for item in items
        ],
        "labels_created": sorted(created),
        "non_atomic_disclosure": NON_ATOMIC_DISCLOSURE,
        "rollback_disclosure": ROLLBACK_DISCLOSURE,
        "never_sent": list(NEVER_SENT_COMMANDS),
        "provider": LABEL_PROVIDER,
        "fallback": "none",
    }
    return prepared
