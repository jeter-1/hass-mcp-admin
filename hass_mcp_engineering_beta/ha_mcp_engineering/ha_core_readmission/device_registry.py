"""Bounded Core 2026.9 child-device and effective-area validation.

Raw registry records are transient probe input.  Only counts, a reason code,
and a deterministic semantic fingerprint are retained as compatibility
evidence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import math
from typing import Any

from .models import CoreReadmissionError, canonical_json, fingerprint


MAX_DEVICE_RECORDS = 4096
MAX_DEVICE_RECORD_BYTES = 1_000_000
MAX_DEVICE_SEMANTIC_FINGERPRINT_BYTES = MAX_DEVICE_RECORD_BYTES * 4
MAX_DEVICE_ID_CHARS = 128
_CHILD_REQUIRED_FIELDS = frozenset(
    {
        "id",
        "parent_device_id",
        "config_entry_id",
        "config_subentry_id",
        "area_id",
        "name",
        "name_by_user",
        "labels",
        "identifiers",
        "disabled_by",
        "created_at",
        "modified_at",
    }
)
_REGULAR_REQUIRED_FIELDS = frozenset(
    {
        "area_id",
        "configuration_url",
        "config_entries",
        "config_entries_subentries",
        "config_entry_id",
        "config_subentry_id",
        "connections",
        "created_at",
        "disabled_by",
        "entry_type",
        "hw_version",
        "id",
        "identifiers",
        "labels",
        "manufacturer",
        "model",
        "model_id",
        "modified_at",
        "name_by_user",
        "name",
        "parent_device_id",
        "primary_config_entry",
        "serial_number",
        "sw_version",
        "via_device_id",
    }
)


@dataclass(frozen=True)
class DeviceRegistryAssessment:
    complete: bool
    reason_code: str
    record_count: int
    child_count: int
    semantic_fingerprint: str


def _failure(reason: str, *, records: int = 0, children: int = 0) -> DeviceRegistryAssessment:
    return DeviceRegistryAssessment(
        complete=False,
        reason_code=reason,
        record_count=records,
        child_count=children,
        semantic_fingerprint=fingerprint(
            {
                "model": "core-device-registry-assessment-v1",
                "complete": False,
                "reason_code": reason,
                "record_count": records,
                "child_count": children,
            }
        ),
    )


def _identifier(value: Any) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_DEVICE_ID_CHARS
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return None
    return value


def _optional_identifier(value: Any) -> bool:
    return value is None or _identifier(value) is not None


def _optional_display_name(value: Any) -> bool:
    # Core permits string-or-null display text, including empty/long strings.
    # These are not target identities. The complete registry's byte limit is
    # enforced before field validation; retain the original text unchanged.
    return value is None or isinstance(value, str)


def _timestamp(value: Any) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(value)
        and value >= 0
    )


def _string_sequence(value: Any, *, pairs: bool = False) -> bool:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or len(value) > 256
    ):
        return False
    if pairs:
        return all(
            isinstance(item, Sequence)
            and not isinstance(item, (str, bytes))
            and len(item) == 2
            and all(_identifier(part) is not None for part in item)
            for item in value
        )
    return all(_identifier(item) is not None for item in value)


def _base_fields_valid(item: Mapping[str, Any]) -> bool:
    return bool(
        _identifier(item.get("config_entry_id"))
        and _optional_identifier(item.get("config_subentry_id"))
        and _optional_identifier(item.get("area_id"))
        and _optional_identifier(item.get("disabled_by"))
        and _optional_display_name(item.get("name"))
        and _optional_display_name(item.get("name_by_user"))
        and _timestamp(item.get("created_at"))
        and _timestamp(item.get("modified_at"))
        and _string_sequence(item.get("identifiers"), pairs=True)
        and _string_sequence(item.get("labels"))
    )


def assess_device_registry(value: Any) -> DeviceRegistryAssessment:
    """Validate a complete Core registry list without retaining its contents."""

    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or len(value) > MAX_DEVICE_RECORDS
    ):
        return _failure("device_registry_oversized_or_invalid")
    try:
        canonical_json(value, maximum=MAX_DEVICE_RECORD_BYTES)
    except CoreReadmissionError:
        return _failure("device_registry_oversized_or_invalid")

    records: dict[str, Mapping[str, Any]] = {}
    children: dict[str, Mapping[str, Any]] = {}
    for item in value:
        if not isinstance(item, Mapping):
            return _failure("device_record_malformed", records=len(records))
        device_id = _identifier(item.get("id"))
        if device_id is None:
            return _failure("device_identity_malformed", records=len(records))
        if device_id in records:
            return _failure("device_identity_duplicate", records=len(records))
        if "parent_device_id" not in item:
            return _failure("device_parent_discriminator_missing", records=len(records))
        parent = item.get("parent_device_id")
        if parent is not None and _identifier(parent) is None:
            return _failure("child_parent_identity_malformed", records=len(records))
        expected_fields = (
            _CHILD_REQUIRED_FIELDS
            if parent is not None
            else _REGULAR_REQUIRED_FIELDS
        )
        if set(item) != expected_fields:
            return _failure(
                "child_device_record_incomplete"
                if parent is not None
                else "regular_device_record_incomplete",
                records=len(records),
            )
        if not _base_fields_valid(item):
            return _failure("device_record_malformed", records=len(records))
        if parent is None:
            if (
                not _string_sequence(item.get("config_entries"))
                or not isinstance(item.get("config_entries_subentries"), Mapping)
                or not _string_sequence(item.get("connections"), pairs=True)
                or not _optional_identifier(item.get("primary_config_entry"))
            ):
                return _failure("regular_device_record_malformed", records=len(records))
        area = item.get("area_id")
        if area is not None and _identifier(area) is None:
            return _failure("device_area_identity_malformed", records=len(records))
        records[device_id] = item
        if parent is not None:
            children[device_id] = item

    for device_id, item in children.items():
        seen = {device_id}
        parent_id = str(item["parent_device_id"])
        while True:
            if parent_id in seen:
                return _failure(
                    "device_parent_cycle",
                    records=len(records),
                    children=len(children),
                )
            seen.add(parent_id)
            parent = records.get(parent_id)
            if parent is None:
                return _failure(
                    "child_parent_missing",
                    records=len(records),
                    children=len(children),
                )
            next_parent = parent.get("parent_device_id")
            if next_parent is None:
                break
            parent_id = str(next_parent)
        # Core 2026.9 constrains a child to one regular parent.  A chain is
        # malformed even when it terminates and happens to resolve an area.
        if records[str(item["parent_device_id"])].get("parent_device_id") is not None:
            return _failure(
                "child_parent_not_regular",
                records=len(records),
                children=len(children),
            )

    effective_areas: dict[str, str | None] = {}
    for device_id, item in records.items():
        seen: set[str] = set()
        current_id = device_id
        current = item
        area = current.get("area_id")
        while area is None and current.get("parent_device_id") is not None:
            if current_id in seen:
                return _failure(
                    "device_parent_cycle",
                    records=len(records),
                    children=len(children),
                )
            seen.add(current_id)
            parent_id = str(current["parent_device_id"])
            parent = records.get(parent_id)
            if parent is None:
                return _failure(
                    "child_parent_missing",
                    records=len(records),
                    children=len(children),
                )
            current_id = parent_id
            current = parent
            area = current.get("area_id")
        effective_areas[device_id] = area if isinstance(area, str) else None

    material = {
        "model": "core-device-registry-semantics-v1",
        "record_count": len(records),
        "child_count": len(children),
        "identities": sorted(records),
        "parents": {
            key: children[key]["parent_device_id"] for key in sorted(children)
        },
        "effective_areas": {
            key: effective_areas[key] for key in sorted(effective_areas)
        },
    }
    # The raw registry is already capped at one million bytes. This projection
    # repeats each validated identity only in the bounded identity, parent, and
    # effective-area indexes, so a four-times cap covers every accepted input
    # without reusing the much smaller public-report serialization limit.
    try:
        semantic_fingerprint = "sha256:" + hashlib.sha256(
            canonical_json(
                material,
                maximum=MAX_DEVICE_SEMANTIC_FINGERPRINT_BYTES,
            )
        ).hexdigest()
    except CoreReadmissionError:
        return _failure(
            "device_semantic_fingerprint_oversized",
            records=len(records),
            children=len(children),
        )
    return DeviceRegistryAssessment(
        complete=True,
        reason_code="device_registry_complete",
        record_count=len(records),
        child_count=len(children),
        semantic_fingerprint=semantic_fingerprint,
    )


def assess_ha_mcp_device_projection(
    core_records: Any,
    entity_records: Any,
    projection: Any,
) -> DeviceRegistryAssessment:
    """Prove whether a delegated result retains child/effective-area semantics.

    The projection is a testable contract rather than raw provider output.  It
    contains only identity, parent, and effective-area fields and therefore
    cannot make an unreviewed upstream response authoritative.
    """

    core = assess_device_registry(core_records)
    if not core.complete:
        return core
    entity_assessment = assess_device_entity_semantics(
        core_records, entity_records
    )
    if not entity_assessment.complete:
        return entity_assessment
    if not isinstance(projection, Mapping) or set(projection) != {
        "devices",
        "entities",
    }:
        return _failure(
            "delegated_device_projection_malformed",
            records=core.record_count,
            children=core.child_count,
        )
    projected_devices = projection["devices"]
    projected_entities = projection["entities"]
    if not _projection_sequence(
        projected_devices,
        identity="device_id",
        fields={"device_id", "parent_device_id", "effective_area_id"},
    ) or not _projection_sequence(
        projected_entities,
        identity="entity_id",
        fields={"entity_id", "effective_area_id"},
    ):
        return _failure(
            "delegated_device_projection_malformed",
            records=core.record_count,
            children=core.child_count,
        )

    records = {str(item["id"]): item for item in core_records}

    def effective_device_area(device_id: str) -> str | None:
        item = records[device_id]
        area = item.get("area_id")
        if isinstance(area, str):
            return area
        parent_id = item.get("parent_device_id")
        if isinstance(parent_id, str):
            parent_area = records[parent_id].get("area_id")
            return parent_area if isinstance(parent_area, str) else None
        return None

    expected_devices = {
        device_id: (
            item.get("parent_device_id"),
            effective_device_area(device_id),
        )
        for device_id, item in records.items()
    }
    observed_devices = {
        str(item["device_id"]): (
            item.get("parent_device_id"),
            item.get("effective_area_id"),
        )
        for item in projected_devices
    }
    if observed_devices != expected_devices:
        return _failure(
            "delegated_effective_area_semantics_changed",
            records=core.record_count,
            children=core.child_count,
        )

    expected_entities: dict[str, str | None] = {}
    for item in entity_records:
        entity_id = str(item["entity_id"])
        area = item.get("area_id")
        device_id = item.get("device_id")
        expected_entities[entity_id] = (
            area
            if isinstance(area, str)
            else effective_device_area(device_id)
            if isinstance(device_id, str)
            else None
        )
    observed_entities = {
        str(item["entity_id"]): item.get("effective_area_id")
        for item in projected_entities
    }
    if observed_entities != expected_entities:
        return _failure(
            "delegated_entity_effective_area_semantics_changed",
            records=core.record_count,
            children=core.child_count,
        )
    return DeviceRegistryAssessment(
        complete=True,
        reason_code="delegated_device_projection_complete",
        record_count=core.record_count + len(expected_entities),
        child_count=core.child_count,
        semantic_fingerprint=fingerprint(
            {
                "model": "delegated-device-projection-v1",
                "device_semantics": expected_devices,
                "entity_semantics": expected_entities,
            }
        ),
    )


def _projection_sequence(
    value: Any,
    *,
    identity: str,
    fields: set[str],
) -> bool:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or len(value) > MAX_DEVICE_RECORDS
    ):
        return False
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != fields:
            return False
        item_id = _identifier(item.get(identity))
        if item_id is None or item_id in seen:
            return False
        if not _optional_identifier(item.get("effective_area_id")):
            return False
        if "parent_device_id" in item and not _optional_identifier(
            item.get("parent_device_id")
        ):
            return False
        seen.add(item_id)
    return True


def assess_device_entity_semantics(
    device_records: Any,
    entity_records: Any,
) -> DeviceRegistryAssessment:
    """Validate entity-to-child-device and effective-area semantics."""

    devices = assess_device_registry(device_records)
    if not devices.complete:
        return devices
    if (
        isinstance(entity_records, (str, bytes))
        or not isinstance(entity_records, Sequence)
        or len(entity_records) > MAX_DEVICE_RECORDS
    ):
        return _failure(
            "entity_registry_oversized_or_invalid",
            records=devices.record_count,
            children=devices.child_count,
        )
    try:
        canonical_json(entity_records, maximum=MAX_DEVICE_RECORD_BYTES)
    except CoreReadmissionError:
        return _failure(
            "entity_registry_oversized_or_invalid",
            records=devices.record_count,
            children=devices.child_count,
        )
    records = {
        str(item["id"]): item
        for item in device_records
        if isinstance(item, Mapping) and "id" in item
    }

    def device_area(device_id: str) -> str | None:
        item = records[device_id]
        area = item.get("area_id")
        if isinstance(area, str):
            return area
        parent_id = item.get("parent_device_id")
        if isinstance(parent_id, str) and parent_id in records:
            parent_area = records[parent_id].get("area_id")
            return parent_area if isinstance(parent_area, str) else None
        return None

    entities: dict[str, Mapping[str, Any]] = {}
    effective: dict[str, str | None] = {}
    for item in entity_records:
        if not isinstance(item, Mapping):
            return _failure(
                "entity_record_malformed",
                records=devices.record_count,
                children=devices.child_count,
            )
        entity_id = _identifier(item.get("entity_id"))
        if entity_id is None or "." not in entity_id:
            return _failure(
                "entity_identity_malformed",
                records=devices.record_count,
                children=devices.child_count,
            )
        if entity_id in entities:
            return _failure(
                "entity_identity_duplicate",
                records=devices.record_count,
                children=devices.child_count,
            )
        entity_area = item.get("area_id")
        if entity_area is not None and _identifier(entity_area) is None:
            return _failure(
                "entity_area_identity_malformed",
                records=devices.record_count,
                children=devices.child_count,
            )
        device_id = item.get("device_id")
        if device_id is not None:
            if _identifier(device_id) is None or device_id not in records:
                return _failure(
                    "entity_device_missing",
                    records=devices.record_count,
                    children=devices.child_count,
                )
        entities[entity_id] = item
        effective[entity_id] = (
            entity_area
            if isinstance(entity_area, str)
            else device_area(device_id)
            if isinstance(device_id, str)
            else None
        )
    return DeviceRegistryAssessment(
        complete=True,
        reason_code="device_entity_semantics_complete",
        record_count=devices.record_count + len(entities),
        child_count=devices.child_count,
        semantic_fingerprint=fingerprint(
            {
                "model": "core-device-entity-semantics-v1",
                "device_semantic_fingerprint": devices.semantic_fingerprint,
                "entity_devices": {
                    key: entities[key].get("device_id")
                    for key in sorted(entities)
                },
                "effective_areas": {
                    key: effective[key] for key in sorted(effective)
                },
            }
        ),
    )


__all__ = [
    "DeviceRegistryAssessment",
    "MAX_DEVICE_RECORDS",
    "assess_device_registry",
    "assess_device_entity_semantics",
    "assess_ha_mcp_device_projection",
]
