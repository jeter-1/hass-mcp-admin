"""Exact response adapter for HA 2026.8 composite-device migration."""

from __future__ import annotations

from copy import deepcopy
from types import MappingProxyType
from typing import Any


ADAPTER_ID = "ha-get-device-composite-ha-2026.8-v1"
EXACT_HA_VERSION = "2026.8.0"
HA_2026_8_1_ADAPTER_ID = "ha-get-device-composite-ha-2026.8.1-v1"
ADAPTER_IDS_BY_HA_VERSION = MappingProxyType(
    {
        EXACT_HA_VERSION: ADAPTER_ID,
        "2026.8.1": HA_2026_8_1_ADAPTER_ID,
    }
)
REVIEWED_UPSTREAM_VERSIONS = frozenset(
    {"8.1.0", "8.1.1", "8.2.0", "8.4.1"}
)


class CompositeDeviceCompatibilityError(RuntimeError):
    """The exact compatibility evidence was malformed or incomplete."""


def _coarse_composite_candidate(
    payload: Any,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], str] | None:
    """Recognize an empty-join candidate without applying release policy."""

    device_id = arguments.get("device_id")
    entity_id = arguments.get("entity_id")
    if (
        not isinstance(device_id, str)
        or not device_id
        or (entity_id is not None and entity_id != "")
        or not isinstance(payload, dict)
        or payload.get("success") is not True
        or payload.get("queried_by") != "device_id"
        or payload.get("entity_count") != 0
        or payload.get("entities") != []
    ):
        return None
    device = payload.get("device")
    if (
        not isinstance(device, dict)
        or device.get("device_id") != device_id
        or device.get("entities") != []
    ):
        return None
    return device, device_id


def _validated_reviewed_source(
    payload: dict[str, Any],
    device: dict[str, Any],
) -> list[str]:
    """Validate source evidence only after exact release ownership is known."""

    if (
        "queried_entity_id" not in payload
        or payload["queried_entity_id"] is not None
    ):
        raise CompositeDeviceCompatibilityError()

    config_entries = device.get("config_entries")
    if (
        not isinstance(config_entries, list)
        or len(config_entries) < 2
        or any(not isinstance(item, str) or not item for item in config_entries)
        or len(set(config_entries)) != len(config_entries)
    ):
        raise CompositeDeviceCompatibilityError()
    return config_entries


async def adapt_ha_get_device_composite_result(
    payload: Any,
    *,
    arguments: dict[str, Any],
    upstream_version: str,
    rest_client: Any,
    websocket_client: Any,
) -> tuple[Any, str | None]:
    """Restore the reviewed entity join for one exact composite lookup.

    ha-mcp 8.1.0/8.1.1/8.2.0/8.4.1 receives correct split entity rows from its
    component on the separately reviewed Home Assistant 2026.8.0 and 2026.8.1
    contracts, but keys those rows by their live split ids before reading the
    map with the old composite id. Adapter identity is selected by exact Core
    release only after the shared incoherent empty-join envelope matches.
    """

    if upstream_version not in REVIEWED_UPSTREAM_VERSIONS:
        return payload, None
    candidate = _coarse_composite_candidate(payload, arguments)
    if candidate is None:
        return payload, None
    device, device_id = candidate

    runtime = await rest_client.request("GET", "/config")
    if not isinstance(runtime, dict):
        raise CompositeDeviceCompatibilityError()
    runtime_version = runtime.get("version")
    if not isinstance(runtime_version, str):
        raise CompositeDeviceCompatibilityError()
    adapter_id = ADAPTER_IDS_BY_HA_VERSION.get(runtime_version)
    if adapter_id is None:
        return payload, None
    config_entries = _validated_reviewed_source(payload, device)

    composite_splits = await websocket_client.command(
        {"type": "config/device_registry/list_composite_splits"}
    )
    if not isinstance(composite_splits, dict):
        raise CompositeDeviceCompatibilityError()
    split_contract = composite_splits.get(device_id)
    if not isinstance(split_contract, dict):
        raise CompositeDeviceCompatibilityError()
    split_ids = split_contract.get("split_ids")
    primary_id = split_contract.get("primary_id")
    if (
        not isinstance(split_ids, list)
        or len(split_ids) < 2
        or any(not isinstance(item, str) or not item for item in split_ids)
        or len(set(split_ids)) != len(split_ids)
        or len(split_ids) != len(config_entries)
        or (primary_id is not None and primary_id not in split_ids)
    ):
        raise CompositeDeviceCompatibilityError()

    entity_rows = await websocket_client.command(
        {"type": "config/entity_registry/list"}
    )
    if not isinstance(entity_rows, list):
        raise CompositeDeviceCompatibilityError()
    split_id_set = set(split_ids)
    selected: list[dict[str, Any]] = []
    selected_entity_ids: set[str] = set()
    for row in entity_rows:
        if not isinstance(row, dict) or row.get("device_id") not in split_id_set:
            continue
        entity_id = row.get("entity_id")
        if (
            not isinstance(entity_id, str)
            or not entity_id
            or entity_id in selected_entity_ids
        ):
            raise CompositeDeviceCompatibilityError()
        selected_entity_ids.add(entity_id)
        selected.append(
            {
                "entity_id": entity_id,
                "name": row.get("name") or row.get("original_name"),
                "platform": row.get("platform"),
            }
        )
    selected.sort(key=lambda item: item["entity_id"])
    if not selected:
        raise CompositeDeviceCompatibilityError()

    adapted = deepcopy(payload)
    adapted_device = deepcopy(device)
    adapted_device["entities"] = selected
    adapted["device"] = adapted_device
    adapted["entities"] = selected
    adapted["entity_count"] = len(selected)
    return adapted, adapter_id
