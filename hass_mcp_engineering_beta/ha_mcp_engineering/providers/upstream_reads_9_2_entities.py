"""9.2.0 zone and services projections (HAMCP-371 S10, research G 2b and 2e).

Each runs after the closed 9.2.0 key set (``upstream_reads_9_2_keys``) and
wraps the unedited 9.1 projection (``upstream_reads_9_1_entities``):

- ``ha_get_zone``: exact 9.2.0 builds the home and YAML zone rows from the zone
  state with ``friendly_name`` moved to ``name`` (``tools_zones.py:380-392,436``
  at 51e3aac7), so Engineering restores ``friendly_name`` from ``name`` on a row
  that lacks it (21:36 record, item 15). The location note and the icon meaning
  go in the payload as ``engineering_notes`` (item 14, ruling 2e).
- ``ha_list_services``: 9.2.0 no longer reports a service field ``type``, and
  Engineering does not synthesize one (item 15); a note says so. ``response`` is
  Core's service response description, copied unchanged
  (``tools_services.py:581-585``), and is admitted on a recorded review
  (item 13).
"""
from __future__ import annotations

from typing import Any

from . import upstream_reads_9_1_entities as _entities_9_1

NOTES = "engineering_notes"
ZONE_LOCATION_NOTE = (
    "persons and device_trackers list who is in each zone, read from the zone "
    "state; they are household location data."
)
ZONE_ICON_NOTE = (
    "icon is the icon stored in the zone, which the Zones editor shows; "
    "entity_icon is the entity registry icon override, which the map and the "
    "zone state show instead."
)
SERVICES_TYPE_NOTE = (
    "ha-mcp 9.2.0 does not report a field type for services; Engineering does "
    "not synthesize one."
)


def _project_zone(payload: Any, arguments: dict) -> dict:
    data = _entities_9_1._project_zone(payload, arguments)
    rows = data["zones"] if "zones" in data else [data["zone"]]
    for row in rows:
        if "friendly_name" not in row and isinstance(row.get("name"), str):
            row["friendly_name"] = row["name"]
    notes = []
    if any("persons" in row or "device_trackers" in row for row in rows):
        notes.append(ZONE_LOCATION_NOTE)
    if any("icon" in row or "entity_icon" in row for row in rows):
        notes.append(ZONE_ICON_NOTE)
    if notes:
        data[NOTES] = notes
    return data


def _project_services(payload: Any, arguments: dict) -> dict:
    data = _entities_9_1._project_services(payload, arguments)
    if isinstance(data.get("services"), dict):
        data[NOTES] = [SERVICES_TYPE_NOTE]
    return data


PROJECTIONS: dict = {
    "ha_get_zone": (_project_zone, _entities_9_1._generic),
    "ha_list_services": (_project_services, _entities_9_1._generic),
}
