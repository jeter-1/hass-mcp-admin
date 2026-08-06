"""Exact response adapter for HA 2026.8 composite-device migration."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


ADAPTER_ID = "ha-get-device-composite-ha-2026.8-v1"
EXACT_HA_VERSION = "2026.8.0"
REVIEWED_UPSTREAM_VERSIONS = frozenset({"8.1.0", "8.1.1"})


class CompositeDeviceCompatibilityError(RuntimeError):
    """The exact compatibility evidence was malformed or incomplete."""


def _eligible_composite_result(
    payload: Any,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], str] | None:
    """Recognize only the reviewed empty-join response shape."""

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
    config_entries = device.get("config_entries")
    if (
        not isinstance(config_entries, list)
        or len({item for item in config_entries if isinstance(item, str)}) < 2
    ):
        return None
    return device, device_id


async def adapt_ha_get_device_composite_result(
    payload: Any,
    *,
    arguments: dict[str, Any],
    upstream_version: str,
    rest_client: Any,
    websocket_client: Any,
) -> tuple[Any, str | None]:
    """Restore the reviewed entity join for one exact composite lookup.

    ha-mcp 8.1.0/8.1.1 receives correct split entity rows from its component on
    Home Assistant 2026.8, but keys those rows by their live split ids before
    reading the map with the old composite id. This adapter is deliberately
    exact-versioned and activates only for that incoherent empty-join envelope.
    """

    if upstream_version not in REVIEWED_UPSTREAM_VERSIONS:
        return payload, None
    eligible = _eligible_composite_result(payload, arguments)
    if eligible is None:
        return payload, None
    device, device_id = eligible

    runtime = await rest_client.request("GET", "/config")
    if not isinstance(runtime, dict):
        raise CompositeDeviceCompatibilityError()
    if runtime.get("version") != EXACT_HA_VERSION:
        return payload, None

    composite_splits = await websocket_client.command(
        {"type": "config/device_registry/list_composite_splits"}
    )
    if not isinstance(composite_splits, dict):
        raise CompositeDeviceCompatibilityError()
    split_contract = composite_splits.get(device_id)
    if not isinstance(split_contract, dict):
        raise CompositeDeviceCompatibilityError()
    split_ids = split_contract.get("split_ids")
    if (
        not isinstance(split_ids, list)
        or len(split_ids) < 2
        or any(not isinstance(item, str) or not item for item in split_ids)
        or len(set(split_ids)) != len(split_ids)
    ):
        raise CompositeDeviceCompatibilityError()

    entity_rows = await websocket_client.command(
        {"type": "config/entity_registry/list"}
    )
    if not isinstance(entity_rows, list):
        raise CompositeDeviceCompatibilityError()
    split_id_set = set(split_ids)
    selected: list[dict[str, Any]] = []
    for row in entity_rows:
        if not isinstance(row, dict) or row.get("device_id") not in split_id_set:
            continue
        entity_id = row.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id:
            raise CompositeDeviceCompatibilityError()
        selected.append(
            {
                "entity_id": entity_id,
                "name": row.get("name") or row.get("original_name"),
                "platform": row.get("platform"),
            }
        )
    selected.sort(key=lambda item: item["entity_id"])
    if not selected:
        return payload, None

    adapted = deepcopy(payload)
    adapted_device = deepcopy(device)
    adapted_device["entities"] = selected
    adapted["device"] = adapted_device
    adapted["entities"] = selected
    adapted["entity_count"] = len(selected)
    return adapted, ADAPTER_ID
