"""Compiled 9.1.0 ha_get_history projection: rule 3 state rows and the section 6.1 statistics projection (ledger row 16).

Each entry in ``PROJECTIONS`` maps an upstream tool name to
``(project(payload, arguments) -> payload, completeness(payload) -> (partial, warnings))``.
``project`` raises ``ValueError`` on an unrecognized shape (fail closed).
Warnings are fixed Engineering strings, never upstream text.

The projection reproduces the shipped ha-mcp 8.5.0 public result
(``311d6dc`` ``tools_history.py``) from the pinned 9.1.0 result
(``d2e3f4b`` ``tools_history.py:209-541``):

- state rows: 9.1 ``format_history_response`` (``history_response.py:49-74``)
  expands ``s/a/lu/lc`` and localizes event times; rows are re-emitted in the
  8.5 order ``{state, last_changed, last_updated[, attributes]}``
  (8.5 ``:977-988``). Absent ``attributes`` stay absent and are disclosed as
  unavailable; they are never filled with ``{}``.
- statistics (``ha-mcp-9.1.0-history-statistics-v1``): ``start`` plus the
  requested types, or 8.5's defaults (8.5 ``:1077-1115``, ``:1180``), taken
  only when present; 9.1 ``end``, ``unit_source``, ``unit_reason`` and
  ``statistics_metadata`` are dropped.
- every 9.1 top-level warning must match a closed template from the pinned
  source; anything else fails closed. Warnings are rebuilt from validated
  structure and closed reason sets, never copied.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..history_slices.contracts import parse_upstream_us

PROJECTION_ID = "ha-mcp-9.1.0-history-statistics-v1"
# 8.5 tools_history.py:1145 (removed in 9.1; Engineering keeps the refusal).
VALID_PERIODS = ("5minute", "hour", "day", "week", "month", "year")
# 8.5 tools_history.py:1048 and the defaults at :1180.
VALID_STATISTIC_TYPES = ("mean", "min", "max", "sum", "state", "change")
DEFAULT_STATISTIC_TYPES = VALID_STATISTIC_TYPES

_ALWAYS_KEEP = frozenset({"success", "warnings"})
_HISTORY_DATA = ("success", "source", "entities", "period", "query_params")
_STATISTICS_DATA = (
    "success", "source", "entities", "period_type", "time_range",
    "statistic_types", "query_params",
)
_PAGINATION = ("total_count", "offset", "limit", "count", "has_more", "next_offset")
_HISTORY_ENTITY = frozenset(("entity_id", "period", "states", *_PAGINATION))
_STATISTICS_ENTITY = frozenset((
    "entity_id", "period", "statistics", "unit_of_measurement", "unit_source",
    "statistics_metadata", *_PAGINATION,
))
_HISTORY_ROW = frozenset({"state", "attributes", "last_updated", "last_changed"})
# 9.1 statistics_helpers.py:52-60: reasons that leave the output unit unknown.
_UNKNOWN_UNIT_REASONS = frozenset({
    "statistics_metadata_unavailable", "statistics_metadata_missing",
    "display_unit_not_reported", "invalid_display_unit_metadata",
})
_TIMEZONE = re.compile(r"[A-Za-z0-9_+\-/]{1,64}")

# Exact 9.1 texts (response_helpers.py:443-455, history_response.py:64-73).
_NATIVE_NOTE = (
    "Timestamp fields retain their native format; see the tool description for units."
)
_TZ_FAILED_91 = (
    "Could not fetch Home Assistant timezone; timezone context defaults to UTC."
)


def _history_note_91(name: str) -> str:
    return (
        f"History event times use {name}. When Core omits lc, last_changed "
        "falls back to last_updated; it may not be the actual state-change time "
        "with significant_changes_only or a window-start snapshot. "
        "Attributes, unknown fields and query bounds are unchanged."
        " UTC is used if HA's timezone is unavailable."
    )


# 9.1 warning templates (statistics_helpers.py:95-102,
# statistics_resets.py:109-113, tools_history.py:1092-1096).
_UNIT_91 = re.compile(
    r"(?P<entity>.+): statistics output unit is unknown \((?P<reason>[a-z_]+)\); "
    r"do not infer it from current state attributes\."
)
_RESET_91 = re.compile(
    r"(?P<entity>.+): last_reset omitted because its unconverted timestamp "
    r"could not be read; Core may apply value-unit conversion to this field\."
)
_EMPTY = re.compile(
    r"No statistics found for: (?P<entities>.+)\. These entities may not have "
    r"state_class attribute or may not have recorded data yet\."
)

# Engineering-built data.warnings markers read back by ``completeness``.
_ATTRIBUTES = re.compile(
    r"(?P<entity>.+): attributes unavailable for (?P<missing>[0-9]+) of "
    r"(?P<total>[0-9]+) returned states; they were not reported, "
    r"and they are not shown as empty\."
)
_TYPO = re.compile(r"fields \[.*\] not found in response — available keys: \[.*\]")

UNITS_UNKNOWN_WARNING = (
    "Statistics output units are unknown for one or more entities; "
    "values are returned without a unit."
)
ATTRIBUTES_UNAVAILABLE_WARNING = (
    "History attributes were not reported for one or more returned states; "
    "they are unavailable, not empty."
)
UNVERIFIED_WARNING = "The upstream history completeness could not be verified."


def _string_list(value: Any, *, allow_csv: bool) -> list[str]:
    """ha-mcp ``parse_string_list_param`` (8.5 util_helpers.py:200-244)."""

    if isinstance(value, list):
        if all(isinstance(item, str) for item in value):
            return list(value)
        raise ValueError("history_list_invalid")
    if isinstance(value, str):
        if value.strip().startswith("[") or not allow_csv:
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, RecursionError):
                raise ValueError("history_list_invalid") from None
            if not isinstance(parsed, list) or not all(isinstance(i, str) for i in parsed):
                raise ValueError("history_list_invalid")
            return parsed
        return [item.strip() for item in value.split(",") if item.strip()]
    raise ValueError("history_list_invalid")


def parse_statistic_types(value: Any) -> list[str] | None:
    """8.5 ``_parse_statistic_types`` (``tools_history.py:1028-1074``) semantics."""

    if value is None:
        return None
    if isinstance(value, str):
        if value.startswith("["):
            parsed = _string_list(value, allow_csv=False)
        elif "," in value:
            parsed = [item.strip() for item in value.split(",") if item.strip()]
        else:
            parsed = [value.strip()]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        parsed = list(value)
    else:
        raise ValueError("history_statistic_types_invalid")
    if not parsed:
        raise ValueError("history_statistic_types_empty")
    if any(item not in VALID_STATISTIC_TYPES for item in parsed):
        raise ValueError("history_statistic_types_invalid")
    return parsed


def _entity_ids(value: Any) -> list[str]:
    """``_parse_entity_ids`` (8.5 ``:524-563``, 9.1 ``:548-587``)."""

    if isinstance(value, str):
        if value.startswith("["):
            parsed = _string_list(value, allow_csv=False)
        elif "," in value:
            parsed = [item.strip() for item in value.split(",") if item.strip()]
        else:
            parsed = [value.strip()]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        parsed = list(value)
    else:
        raise ValueError("invalid_response")
    if not parsed:
        raise ValueError("invalid_response")
    return parsed


def dispatch_arguments(arguments: dict) -> dict:
    """Row 16 dispatch: the 8.5 client-side statistics refusals, then the rule 3 pins.

    8.5 refused an invalid ``period`` (``:1145-1154``) and an empty or unknown
    ``statistic_types`` (``:1049-1073``) before any recorder query; 9.1 accepts
    both. Engineering refuses them before dispatch (``ValueError`` means zero
    upstream calls). Statistic types are forwarded already normalized.
    """

    out = dict(arguments)
    if out.get("source", "history") == "statistics":
        if out.get("period", "day") not in VALID_PERIODS:
            raise ValueError("history_period_invalid")
        if "statistic_types" in out:
            out["statistic_types"] = parse_statistic_types(out["statistic_types"])
    out.update(summary=False, include_schema=False, core_options=None)
    return out


def _int(value: Any, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError("invalid_response")
    return value


def _str(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("invalid_response")
    return value


def _bounds(value: Any) -> dict:
    if type(value) is not dict or set(value) != {"start", "end"}:
        raise ValueError("invalid_response")
    return {"start": _str(value["start"]), "end": _str(value["end"])}


def _pagination(entity: dict, rows: list) -> dict:
    out = {
        "total_count": _int(entity["total_count"]),
        "offset": _int(entity["offset"]),
        "limit": _int(entity["limit"], 1),
        "count": _int(entity["count"]),
        "has_more": entity["has_more"],
        "next_offset": entity["next_offset"],
    }
    if (out["count"] != len(rows) or type(out["has_more"]) is not bool
            or (out["next_offset"] is not None and type(out["next_offset"]) is not int)):
        raise ValueError("invalid_response")
    return out


def _expected_keys(full: tuple, fields: Any) -> tuple[tuple, str | None]:
    """The 8.5/9.1 ``project_fields`` selection and its typo text (identical in both)."""

    if fields is None:
        return full, None
    parsed = _string_list(fields, allow_csv=True)
    keep = set(parsed) | _ALWAYS_KEEP
    unknown = sorted(set(parsed) - set(full) - _ALWAYS_KEEP)
    typo = None
    if unknown:
        available = sorted(k for k in full if k not in _ALWAYS_KEEP)
        typo = f"fields {unknown!r} not found in response — available keys: {available!r}"
    return tuple(k for k in full if k in keep), typo


def _metadata(metadata: Any, source: str, timezone_failed: bool) -> dict:
    if type(metadata) is not dict or set(metadata) != {
        "home_assistant_timezone", "timestamp_format", "note",
    }:
        raise ValueError("invalid_response")
    name = _str(metadata["home_assistant_timezone"])
    if not _TIMEZONE.fullmatch(name) or (timezone_failed and name != "UTC"):
        raise ValueError("invalid_response")
    if source == "statistics":
        expected = ("native", _NATIVE_NOTE)
    else:
        expected = (f"ISO 8601 ({name})", _history_note_91(name))
    if (metadata["timestamp_format"], metadata["note"]) != expected:
        raise ValueError("invalid_response")
    # The shipped 8.5 add_timezone_metadata wrapper (util_helpers.py:829-851).
    if timezone_failed:
        return {
            "home_assistant_timezone": "UTC",
            "timestamp_format": "ISO 8601 (UTC)",
            "note": "Could not fetch Home Assistant timezone — timestamps are in UTC.",
        }
    return {
        "home_assistant_timezone": name,
        "timestamp_format": f"ISO 8601 ({name})",
        "note": (
            "Per-record timestamp fields (last_changed, last_updated, last_reported, "
            f"when, last_triggered) have been converted to {name} local time. "
            "Query-window boundary fields (period, start_time, end_time) remain in UTC."
        ),
    }


def _history_entities(entities: Any, requested: list[str], minimal: bool):
    if type(entities) is not list or len(entities) != len(requested):
        raise ValueError("invalid_response")
    out, markers = [], []
    for entity, entity_id in zip(entities, requested):
        # A summary entity (9.1 summary=True) is never accepted: summary is pinned False.
        if type(entity) is not dict or set(entity) != _HISTORY_ENTITY:
            raise ValueError("invalid_response")
        if entity["entity_id"] != entity_id or type(entity["states"]) is not list:
            raise ValueError("invalid_response")
        rows, missing = [], 0
        for row in entity["states"]:
            if type(row) is not dict or not set(row) <= _HISTORY_ROW:
                raise ValueError("invalid_response")
            state = _str(row.get("state"))
            for key in ("last_changed", "last_updated"):
                if parse_upstream_us(row.get(key)) is None:
                    raise ValueError("invalid_response")
            if "attributes" in row and type(row["attributes"]) is not dict:
                raise ValueError("invalid_response")
            item = {
                "state": state,
                "last_changed": row["last_changed"],
                "last_updated": row["last_updated"],
            }
            if not minimal:
                if "attributes" in row:
                    item["attributes"] = row["attributes"]
                else:
                    missing += 1
            rows.append(item)
        out.append({
            "entity_id": entity_id,
            "period": _bounds(entity["period"]),
            "states": rows,
            **_pagination(entity, rows),
        })
        if missing:
            markers.append(
                f"{entity_id}: attributes unavailable for {missing} of {len(rows)} "
                "returned states; they were not reported, and they are "
                "not shown as empty."
            )
    return out, markers


def _history(data: dict, arguments: dict, requested: list[str], keys: tuple):
    minimal = arguments.get("minimal_response", True)
    out, markers = {}, []
    for key in keys:
        value = data[key]
        if key == "success":
            out[key] = True
        elif key == "source":
            if value != "history":
                raise ValueError("invalid_response")
            out[key] = value
        elif key == "period":
            out[key] = _bounds(value)
        elif key == "query_params":
            expected = {"minimal_response", "significant_changes_only", "limit",
                        "offset", "order", "summary"}
            if (type(value) is not dict or set(value) != expected
                    or value["summary"] is not False
                    or value["minimal_response"] is not minimal
                    or type(value["significant_changes_only"]) is not bool
                    or value["order"] not in ("asc", "desc")):
                raise ValueError("invalid_response")
            out[key] = {
                "minimal_response": minimal,
                "significant_changes_only": value["significant_changes_only"],
                "limit": _int(value["limit"], 1),
                "offset": _int(value["offset"]),
                "order": value["order"],
            }
        else:
            out[key], markers = _history_entities(value, requested, minimal)
    return out, markers


def _statistics_entities(entities: Any, requested: list[str], period: str, types: tuple):
    if type(entities) is not list or len(entities) != len(requested):
        raise ValueError("invalid_response")
    out, unknown, empty = [], [], []
    for entity, entity_id in zip(entities, requested):
        if type(entity) is not dict:
            raise ValueError("invalid_response")
        keys = set(entity)
        reason = entity.get("unit_reason")
        if keys - {"unit_reason"} != _STATISTICS_ENTITY:
            raise ValueError("invalid_response")
        if (entity["entity_id"] != entity_id or entity["period"] != period
                or type(entity["statistics"]) is not list
                or (entity["statistics_metadata"] is not None
                    and type(entity["statistics_metadata"]) is not dict)):
            raise ValueError("invalid_response")
        unit = entity["unit_of_measurement"]
        if entity["unit_source"] == "unknown":
            if unit is not None or reason not in _UNKNOWN_UNIT_REASONS:
                raise ValueError("invalid_response")
            unknown.append((entity_id, reason))
        elif entity["unit_source"] == "recorder_metadata":
            if unit is None:
                if reason != "statistics_are_unitless":
                    raise ValueError("invalid_response")
            elif type(unit) is not str or "unit_reason" in keys:
                raise ValueError("invalid_response")
        else:
            raise ValueError("invalid_response")
        rows = []
        for row in entity["statistics"]:
            if type(row) is not dict or "start" not in row:
                raise ValueError("invalid_response")
            start = row["start"]
            if type(start) not in (int, float, str):
                raise ValueError("invalid_response")
            item = {"start": start}
            for name in types:
                if name in row:
                    if row[name] is not None and type(row[name]) not in (int, float):
                        raise ValueError("invalid_response")
                    item[name] = row[name]
            rows.append(item)
        pagination = _pagination(entity, rows)
        if pagination["count"] == 0:
            empty.append(entity_id)
        out.append({
            "entity_id": entity_id,
            "period": period,
            "statistics": rows,
            "unit_of_measurement": unit,
            **pagination,
        })
    return out, unknown, empty


def _statistics(data: dict, arguments: dict, requested: list[str], keys: tuple):
    requested_types = parse_statistic_types(arguments.get("statistic_types"))
    types = tuple(requested_types or DEFAULT_STATISTIC_TYPES)
    period = arguments.get("period", "day")
    out, unknown, empty = {}, None, None
    for key in keys:
        value = data[key]
        if key == "success":
            out[key] = True
        elif key == "source":
            if value != "statistics":
                raise ValueError("invalid_response")
            out[key] = value
        elif key == "period_type":
            if value != period:
                raise ValueError("invalid_response")
            out[key] = value
        elif key == "time_range":
            out[key] = _bounds(value)
        elif key == "statistic_types":
            if (type(value) is not list or not all(type(i) is str for i in value)
                    or (requested_types is not None and value != requested_types)):
                raise ValueError("invalid_response")
            # 8.5 semantics: the requested list, else the six defaults.
            out[key] = list(types)
        elif key == "query_params":
            if (type(value) is not dict or set(value) != {"statistic_types", "limit", "offset"}
                    or value["statistic_types"] != requested_types):
                raise ValueError("invalid_response")
            out[key] = {
                "statistic_types": requested_types,
                "limit": _int(value["limit"], 1),
                "offset": _int(value["offset"]),
            }
        else:
            out[key], unknown, empty = _statistics_entities(value, requested, period, types)
    return out, unknown, empty


def _classify_warnings(warnings: list, source: str, requested: list[str], typo: str | None):
    """Match every 9.1 top-level warning to a closed template; else fail closed."""

    units, empty, timezone_failed, typo_seen = [], None, False, False
    for text in warnings:
        if type(text) is not str:
            raise ValueError("invalid_response")
        if text == _TZ_FAILED_91 and not timezone_failed:
            timezone_failed = True
            continue
        if typo is not None and text == typo and not typo_seen:
            typo_seen = True
            continue
        if source == "statistics":
            match = _UNIT_91.fullmatch(text)
            if match and match["entity"] in requested and match["reason"] in _UNKNOWN_UNIT_REASONS:
                units.append((match["entity"], match["reason"]))
                continue
            match = _RESET_91.fullmatch(text)
            if match and match["entity"] in requested:
                # Withheld: it concerns last_reset, a field this row never returns.
                continue
            match = _EMPTY.fullmatch(text)
            if match and empty is None:
                ids = match["entities"].split(", ")
                if all(item in requested for item in ids):
                    empty = ids
                    continue
        raise ValueError("invalid_response")
    if typo is not None and not typo_seen:
        raise ValueError("invalid_response")
    return units, empty, timezone_failed


def _empty_warning(entity_ids: list[str]) -> str:
    # 8.5 tools_history.py:1212-1216 (text identical in 9.1 :1092-1096).
    return (
        f"No statistics found for: {', '.join(entity_ids)}. "
        "These entities may not have state_class attribute or may not have recorded data yet."
    )


def _unit_warning(entity_id: str, reason: str) -> str:
    return (
        f"{entity_id}: statistics output unit is unknown ({reason}); "
        "do not infer it from current state attributes."
    )


def project(payload: Any, arguments: dict) -> dict:
    """9.1 ``ha_get_history`` result -> shipped 8.5 public result (fail closed)."""

    if (type(payload) is not dict or not {"data", "metadata"} <= set(payload)
            or not set(payload) <= {"data", "metadata", "warnings"}):
        raise ValueError("invalid_response")
    upstream_warnings = payload.get("warnings", [])
    if type(upstream_warnings) is not list or ("warnings" in payload and not upstream_warnings):
        raise ValueError("invalid_response")
    source = arguments.get("source", "history")
    if source not in ("history", "statistics"):
        raise ValueError("invalid_response")
    requested = _entity_ids(arguments.get("entity_ids"))
    full = _STATISTICS_DATA if source == "statistics" else _HISTORY_DATA
    keys, typo = _expected_keys(full, arguments.get("fields"))
    data = payload["data"]
    # 9.1 hoists data.warnings to the top level (response_helpers.py:427-432).
    if type(data) is not dict or set(data) != set(keys) or data.get("success") is not True:
        raise ValueError("invalid_response")
    units, empty, timezone_failed = _classify_warnings(
        upstream_warnings, source, requested, typo,
    )
    metadata = _metadata(payload["metadata"], source, timezone_failed)
    warnings: list[str] = []
    if source == "history":
        projected, markers = _history(data, arguments, requested, keys)
        warnings.extend(markers)
    else:
        projected, unknown, empty_rows = _statistics(data, arguments, requested, keys)
        if unknown is not None and unknown != units:
            raise ValueError("invalid_response")
        if empty_rows is not None and (empty_rows or None) != empty:
            raise ValueError("invalid_response")
        # 9.1 unit warnings retained as bounded, rebuilt warnings (section 6.1, F3).
        warnings.extend(_unit_warning(entity_id, reason) for entity_id, reason in units)
        if empty is not None:
            warnings.append(_empty_warning(empty))
    if typo is not None:
        warnings.append(typo)
    if warnings:
        projected["warnings"] = warnings
    return {"data": projected, "metadata": metadata}


def completeness(payload: Any) -> tuple[bool, list[str]]:
    """F3 for row 16: read back the fixed markers from the projected, sanitized payload."""

    data = payload.get("data") if type(payload) is dict else None
    if type(data) is not dict:
        return True, [UNVERIFIED_WARNING]
    warnings = data.get("warnings", [])
    if type(warnings) is not list:
        return True, [UNVERIFIED_WARNING]
    flags: list[str] = []
    for text in warnings:
        if type(text) is not str:
            found = UNVERIFIED_WARNING
        elif _UNIT_91.fullmatch(text):
            found = UNITS_UNKNOWN_WARNING
        elif _ATTRIBUTES.fullmatch(text):
            found = ATTRIBUTES_UNAVAILABLE_WARNING
        elif _EMPTY.fullmatch(text) or _TYPO.fullmatch(text):
            continue
        else:
            found = UNVERIFIED_WARNING
        if found not in flags:
            flags.append(found)
    return bool(flags), flags


PROJECTIONS: dict = {"ha_get_history": (project, completeness)}
