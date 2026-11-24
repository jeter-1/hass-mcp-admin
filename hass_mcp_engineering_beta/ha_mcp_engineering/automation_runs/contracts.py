"""Fixed read contract for HAMCP-476 automation run counts.

A run is one ``automation_triggered`` logbook row. Home Assistant Core
2026.11.0 fires that event only after the conditions pass and the action
starts (``automation/entity.py`` ``started_action``), so failed-condition
triggers and runs dropped by the automation's mode are never counted.

Core behavior relied on (Core 2026.11.0, 74cf5a49):

- ``GET /api/logbook/{start}?end_time&entity`` selects rows with
  ``start < time_fired < end`` (``logbook/queries/common.py``: both bounds
  exclusive) for the named entities.
- Each row's ``when`` is the event time as a UTC ISO string at microsecond
  precision (``logbook/processor.py``). An ``automation_triggered`` row is
  ``{name, message, source, entity_id, context_id, domain, when}`` with
  ``message`` ``"triggered"`` or ``"triggered by <source>"``
  (``automation/logbook.py``); state rows carry ``state`` and
  ``logbook_entry`` rows carry no ``source``.

Every value here is a disclosed bound (Josh, relayed 2026-11-24: defaults
24 hours, 8 requests and 60 seconds for named automations; the
all-automations budget sized by the orchestrator from the house's count).
"""

from __future__ import annotations

import math
from typing import Any

from ..clients.logbook import MAX_FILTER_CHARS, MAX_FILTER_ENTITIES, MAX_HOURS, TIMESTAMP, _valid_entity


PROFILE = "automation-run-counts-read-v1"
DEFAULT_HOURS = 24
MAX_IDS = MAX_FILTER_ENTITIES
# Named automations: at most 8 logbook GETs and 60 seconds per call.
IDS_MAX_GETS = 8
IDS_MAX_SECONDS = 60
# All automations: sized from the house's recorded count, 115 automations
# (hass_mcp_engineering_beta/CHANGELOG.md, beta.40 live findings, Core
# 2026.10.3). 115 is 12 batches of 10; 32 GETs cover them with 20 splits
# to spare, or about 2.5 times the automations with no split. More than
# MAX_ALL_AUTOMATIONS is refused before any logbook read, which also keeps
# the reply inside the 60,000-byte response limit.
ALL_MAX_GETS = 32
ALL_MAX_SECONDS = 120
MAX_ALL_AUTOMATIONS = 200
BATCH = MAX_FILTER_ENTITIES
# A sub-window shorter than two of these is never split again.
MIN_SPLIT_SECONDS = 60

ALLOWED_ARGUMENTS = frozenset({"automation_ids", "all_automations", "hours", "start_time", "end_time"})
LIMIT_CAUSES = frozenset({"response_bytes", "record_count", "structure_items"})
CAUSES = LIMIT_CAUSES | {"request_budget", "time_budget"}
COUNTED_EVENT = "automation_triggered"
INVALID_MESSAGE = "Invalid automation run count arguments."


def valid_automation_id(value: Any) -> bool:
    return _valid_entity(value) and value.startswith("automation.")


def validate_arguments(arguments: Any) -> dict[str, Any]:
    """Validate raw caller arguments before the SDK can coerce them.

    Structural checks only; the window's relation to now (start before now,
    start before end, at most 168 hours) is checked by ``resolve_window``.
    """

    try:
        if not isinstance(arguments, dict) or not set(arguments) <= ALLOWED_ARGUMENTS:
            raise ValueError
        all_automations = arguments.get("all_automations")
        if all_automations is not None and type(all_automations) is not bool:
            raise ValueError
        ids = arguments.get("automation_ids")
        if all_automations:
            if ids is not None:
                raise ValueError
            mode = "all_automations"
        else:
            if (type(ids) is not list or not 1 <= len(ids) <= MAX_IDS
                    or not all(valid_automation_id(item) for item in ids)
                    or len(set(ids)) != len(ids)
                    or len(",".join(ids)) > MAX_FILTER_CHARS):
                raise ValueError
            mode = "automation_ids"
        hours = arguments.get("hours")
        if hours is not None and not (type(hours) in (int, float) and 0 < hours <= MAX_HOURS
                                      and math.isfinite(hours)):
            raise ValueError
        times = {}
        for key in ("start_time", "end_time"):
            value = arguments.get(key)
            if value is not None and (type(value) is not str or not TIMESTAMP.fullmatch(value)):
                raise ValueError
            times[key] = value
    except ValueError:
        raise ValueError(INVALID_MESSAGE) from None
    window_kind = ("start_time" if times["start_time"] is not None
                   else "hours" if hours is not None else "default")
    return {"mode": mode, "automation_ids": tuple(ids) if mode == "automation_ids" else None,
            "hours": hours, "start_time": times["start_time"], "end_time": times["end_time"],
            "window_kind": window_kind}


def is_run(record: dict[str, Any]) -> bool:
    """True for an ``automation_triggered`` row (entity checked by the caller)."""

    message = record.get("message")
    return (record.get("domain") == "automation" and "state" not in record
            and "source" in record and type(message) is str
            and (message == "triggered" or message.startswith("triggered by ")))


def batches(automation_ids: list[str]) -> list[tuple[str, ...]]:
    """Consecutive groups of at most 10 ids joining to at most 512 characters."""

    groups, current, size = [], [], 0
    for automation_id in automation_ids:
        added = len(automation_id) + bool(current)
        if current and (len(current) == BATCH or size + added > MAX_FILTER_CHARS):
            groups.append(tuple(current))
            current, size, added = [], 0, len(automation_id)
        current.append(automation_id)
        size += added
    if current:
        groups.append(tuple(current))
    return groups
