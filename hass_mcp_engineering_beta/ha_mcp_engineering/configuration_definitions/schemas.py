"""Positive per-integration key allowlists, pinned to Core 2026.11.3.

Every key the report can copy from a YAML definition is named here; any
other key is counted, never copied. ``db_url`` is not in any allowlist and is
withheld in every position (``contracts.WITHHELD_KEYS``).

Sources (``homeassistant/`` package of Core
``92da9f154079459b9e547126f9bd9fce726de16a``):

- sql: ``components/sql/__init__.py:46-70`` ``QUERY_SCHEMA``;
- template: ``components/template/config.py:261-349`` (block),
  ``schemas.py:43-71`` (entity), ``sensor.py:67-91``,
  ``binary_sensor.py:59-80``;
- filter ``sensor.py:153-179``; derivative ``sensor.py:101-119``;
  statistics ``sensor.py:581-606``; group ``__init__.py:67-85``,
  ``sensor.py:95-110``, ``binary_sensor.py:40-48``; integration
  ``sensor.py:91-113``; min_max ``sensor.py:69-79``; history_stats
  ``sensor.py:90-117``; threshold ``binary_sensor.py:68-83``; trend
  ``binary_sensor.py:76-91``; utility_meter ``__init__.py:102-131``;
- the shared platform keys ``helpers/config_validation.py:1290-1296``.
"""

from __future__ import annotations

from types import MappingProxyType

CORE_PIN = "2026.11.3@92da9f154079459b9e547126f9bd9fce726de16a"

SQL_KEYS = frozenset({
    "name", "query", "column", "unit_of_measurement", "value_template", "unique_id",
    "device_class", "state_class", "availability", "icon", "picture",
})
# Keys whose value is a template; a name containing template syntax is never
# slugified into a derived entity ID.
TEMPLATE_MARKERS = ("{{", "{%", "{#")

TEMPLATE_BLOCK_KEYS = frozenset({
    "unique_id", "triggers", "trigger", "conditions", "condition", "actions", "action", "variables",
    "trigger_variables",
})
TEMPLATE_PLATFORMS = (
    "alarm_control_panel", "binary_sensor", "button", "climate", "cover", "device_tracker", "event",
    "fan", "image", "light", "lock", "media_player", "number", "select", "sensor", "switch", "update",
    "vacuum", "water_heater", "weather",
)
TEMPLATE_COMMON_KEYS = frozenset({
    "name", "unique_id", "default_entity_id", "availability", "icon", "picture", "attributes",
    "variables", "conditions",
})
TEMPLATE_ENTITY_KEYS = MappingProxyType({
    "sensor": TEMPLATE_COMMON_KEYS | {
        "state", "device_class", "state_class", "unit_of_measurement", "last_reset"},
    "binary_sensor": TEMPLATE_COMMON_KEYS | {
        "state", "auto_off", "delay_on", "delay_off", "device_class", "unit_of_measurement"},
})
TEMPLATE_DEFAULT_NAMES = MappingProxyType({
    "sensor": "Template Sensor", "binary_sensor": "Template Binary Sensor",
})

PLATFORM_COMMON_KEYS = frozenset({"platform", "entity_namespace", "scan_interval"})

SENSOR_PLATFORM_KEYS = MappingProxyType({
    "filter": frozenset({"entity_id", "name", "unique_id", "filters"}),
    "derivative": frozenset({"source", "name", "unique_id", "round", "unit_prefix", "unit_time",
                             "unit", "time_window", "max_sub_interval"}),
    "statistics": frozenset({"entity_id", "name", "unique_id", "state_characteristic",
                             "sampling_size", "max_age", "keep_last_sample", "precision",
                             "percentile"}),
    "group": frozenset({"entities", "type", "name", "unique_id", "ignore_non_numeric",
                        "unit_of_measurement", "device_class", "state_class"}),
    "integration": frozenset({"source", "name", "unique_id", "round", "unit_prefix", "unit_time",
                              "device_class", "max_sub_interval", "method"}),
    "min_max": frozenset({"entity_ids", "type", "name", "round_digits", "unique_id"}),
    "history_stats": frozenset({"entity_id", "state", "start", "end", "duration",
                                "min_state_duration", "type", "name", "unique_id", "state_class"}),
})
BINARY_SENSOR_PLATFORM_KEYS = MappingProxyType({
    "threshold": frozenset({"entity_id", "device_class", "hysteresis", "lower", "name", "upper"}),
    "trend": frozenset({"sensors"}),
    "group": frozenset({"entities", "name", "unique_id", "device_class", "all"}),
})
TREND_SENSOR_KEYS = frozenset({
    "entity_id", "attribute", "device_class", "friendly_name", "invert", "max_samples",
    "min_gradient", "sample_duration", "min_samples", "unique_id",
})
GROUP_KEYS = frozenset({"entities", "name", "icon", "all"})
UTILITY_METER_KEYS = frozenset({
    "source", "name", "unique_id", "cycle", "offset", "delta_values", "net_consumption",
    "periodically_resetting", "tariffs", "cron", "always_available",
})

# ``<domain>: - platform: X`` forms Core 2026.11.3 rejects with a repair issue
# and no entity (sql ``sensor.py:71-80``, template ``helpers.py:196-228``,
# utility_meter ``sensor.py:185-194``). They are projected so the owner can
# find them, and flagged ``unsupported_legacy_platform``.
LEGACY_PLATFORM_KEYS = MappingProxyType({
    "sql": SQL_KEYS,
    "utility_meter": UTILITY_METER_KEYS,
    # The legacy template platform's own keys are not projected: only its
    # presence and the count of its keys.
    "template": frozenset(),
})

# Keys whose value names the entities a definition reads (a missing one is a
# dependency signal in the diagnosis).
DEPENDENCY_KEYS = MappingProxyType({
    "filter": ("entity_id",), "derivative": ("source",), "statistics": ("entity_id",),
    "integration": ("source",), "min_max": ("entity_ids",), "history_stats": ("entity_id",),
    "threshold": ("entity_id",), "trend": ("entity_id",), "group": ("entities",),
    "utility_meter": ("source",),
})

# Fixed default names (``name`` absent) that drive the entity ID. A callable
# default depends on other keys of the same definition.
SENSOR_DEFAULT_NAMES = MappingProxyType({
    "statistics": "Statistical characteristic",  # statistics/sensor.py:538
    "history_stats": "unnamed statistics",       # history_stats/sensor.py:90-117
    # group/sensor.py:366-368 replaces this default with "Sensor group <type>".
    "group": "Sensor Group",
})
BINARY_SENSOR_DEFAULT_NAMES = MappingProxyType({
    "threshold": "Threshold",                     # threshold/binary_sensor.py:57
    "group": "Binary Sensor Group",               # group/binary_sensor.py:40-48
})
# A nameless entity without a unique_id (helpers/entity_platform.py:1178-1195,
# DEVICE_DEFAULT_NAME const.py:49).
DEVICE_DEFAULT_NAME = "Unnamed Device"

# Registry ``platform`` for each definition kind: YAML entities are created by
# the integration's own platform (discovery or ``platform:``), so the registry
# platform is the integration domain.
REGISTRY_PLATFORM = MappingProxyType({name: name for name in (
    "sql", "template", "filter", "derivative", "statistics", "group", "integration", "min_max",
    "history_stats", "threshold", "trend", "utility_meter",
)})
