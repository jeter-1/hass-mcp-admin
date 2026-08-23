"""Deterministic sanitized fixtures for bounded blueprint dependency tests."""

from __future__ import annotations

from typing import Any


LARGE_ROOT_VARIABLE_COUNT = 140
LARGE_TEMPLATE_COUNT = 3_000
LARGE_PADDING_VALUE_COUNT = 4_500
LARGE_RESOLUTION_VALUE_NODES = 10_648


def small_motion_light_blueprint() -> dict[str, Any]:
    """Return a compact control shaped like HA Core's motion-light example."""

    return {
        "blueprint": {
            "name": "Synthetic motion-light control",
            "domain": "automation",
            "input": {
                "motion_entity": {},
                "light_entity": {},
            },
        },
        "triggers": [
            {
                "trigger": "state",
                "entity_id": {"__blueprint_input__": "motion_entity"},
                "to": "on",
            }
        ],
        "actions": [
            {
                "action": "light.turn_on",
                "target": {
                    "entity_id": {"__blueprint_input__": "light_entity"}
                },
            }
        ],
    }


def large_sensor_light_structural_blueprint() -> dict[str, Any]:
    """Return a source-safe structural analogue of the pinned Sensor Light.

    The fixture intentionally crosses the former 10,000-value resolution,
    128-root-variable, and 2,000-document-obligation limits.  Repeated neutral
    templates exercise document-local semantic caching without carrying any
    upstream blueprint text, live entity IDs, or production observations.
    """

    neutral_template = "{{ 'synthetic non-entity text' }}"
    return {
        "blueprint": {
            "name": "Synthetic large sensor-light structural fixture",
            "domain": "automation",
            "input": {},
        },
        "variables": {
            f"synthetic_variable_{index:03d}": index
            for index in range(LARGE_ROOT_VARIABLE_COUNT)
        },
        "actions": [
            {"value_template": neutral_template}
            for _index in range(LARGE_TEMPLATE_COUNT)
        ],
        "synthetic_padding": [
            index for index in range(LARGE_PADDING_VALUE_COUNT)
        ],
    }


__all__ = [
    "LARGE_PADDING_VALUE_COUNT",
    "LARGE_RESOLUTION_VALUE_NODES",
    "LARGE_ROOT_VARIABLE_COUNT",
    "LARGE_TEMPLATE_COUNT",
    "large_sensor_light_structural_blueprint",
    "small_motion_light_blueprint",
]
