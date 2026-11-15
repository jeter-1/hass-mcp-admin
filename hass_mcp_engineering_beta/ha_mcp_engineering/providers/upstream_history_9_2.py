"""Compiled 9.2.0 ha_get_history: time semantics and the closed key set (HAMCP-371 S9).

Exact ha-mcp 9.2.0 (``51e3aac7`` ``tools_history.py:74-112,405-420``) resolves
an ISO ``start_time`` or ``end_time`` without an offset in Home Assistant local
time, where 9.1 forced UTC. Engineering does not forward a time whose meaning
changed between the releases (21:36 record, item 6):

- accepted: a full ISO date and time with an explicit offset or ``Z``, or a
  relative form matched by the exact upstream pattern (``30min``, ``24h``,
  ``7d``, ``2w``, ``1m``); accepted values are forwarded unchanged;
- refused before dispatch with ``HistoryTimeOffsetRequired``: every other
  value Python's ``datetime.fromisoformat`` parses, offset-free and date-only
  values included;
- refused before dispatch with a plain ``ValueError`` (the existing generic
  ``invalid_request``): values upstream cannot parse (22:59 record, item 7).

The result is projected by the unedited 9.1 module (``upstream_history_9_1``):
the 9.2.0 history and statistics shapes equal 9.1's in every real-Core receipt
(``docs/evidence/hamcp371/s6``). Before that projection runs:

- keys outside the reviewed key set are withheld and counted, not a
  whole-read refusal (item 7). ``ADMITTED`` and ``POLICY_DROPS`` equal the
  output-key contract (``tests/fixtures/ha_mcp_9_2_0/output-key-contract.json``);
  ``OPEN`` containers pass uncounted (item 11). Counts are distinct names, the
  policy drops reported apart from unreviewed keys, names never disclosed
  (item 10), using the contract's walk;
- an upstream warning passes to the 9.1 projection only when it matches one of
  its reviewed, closed templates; any other warning is replaced by the fixed
  code ``upstream_warning_replaced`` and counted (item 12).

After it, each echoed query bound must carry an explicit offset (fail closed).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re
from typing import Any

from . import upstream_history_9_1 as _history_9_1

PROJECTION_ID = "ha-mcp-9.2.0-history-v1"
CATEGORY = "history_time_offset_required"
REPLACED_WARNING = "upstream_warning_replaced"
COUNT_CAP = 999

# The output-key contract row 16 (S9): every name the shipped 9.1 projection
# reads or returns. A test asserts equality with the committed contract.
ADMITTED = frozenset({
    "attributes", "change", "count", "data", "end", "entities", "entity_id", "has_more",
    "home_assistant_timezone", "last_changed", "last_updated", "limit", "max", "mean",
    "metadata", "min", "minimal_response", "next_offset", "note", "offset", "order",
    "period", "period_type", "query_params", "significant_changes_only", "source",
    "start", "state", "states", "statistic_types", "statistics", "success", "sum",
    "time_range", "timestamp_format", "total_count", "unit_of_measurement", "warnings",
})
# The shipped 8.5-parity drops (upstream_history_9_1.py:17-20,329-339,388-396):
# kept for the 9.1 projection, which removes them, and counted as policy drops.
POLICY_DROPS = frozenset({
    "last_reset", "statistics_metadata", "summary", "unit_reason", "unit_source",
})
# Integration-defined containers (item 11): kept whole and not descended.
OPEN = frozenset({"attributes", "config", "filter", "selector"})

# tools_history.py:94-95 at 51e3aac7, applied as upstream applies it.
_RELATIVE = re.compile(r"^(\d+)(min|[hdwm])$")
_RELATIVE_SECONDS = {"min": 60, "h": 3600, "d": 86400, "w": 604800, "m": 2592000}
_FULL = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}"
    r"(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)


class HistoryTimeOffsetRequired(ValueError):
    """A parseable time whose meaning 9.2.0 changed; refused before dispatch."""


def _time(value: Any) -> bool:
    """True when the value needs an explicit offset; ``ValueError`` when unparseable."""

    if value is None:
        return False
    if type(value) is not str:
        raise ValueError("history_time_invalid")
    try:
        match = _RELATIVE.match(value.lower().strip())
        if match:
            # Upstream's own overflow refusal (tools_history.py:97-104).
            datetime.now(UTC) - timedelta(
                seconds=int(match.group(1)) * _RELATIVE_SECONDS[match.group(2)])
            return False
        parsed = datetime.fromisoformat(value)
    except (ValueError, OverflowError):
        raise ValueError("history_time_invalid") from None
    return parsed.tzinfo is None or not _FULL.fullmatch(value)


def dispatch_arguments(arguments: dict) -> dict:
    """Row 16 on 9.2.0: the time check, then the unchanged 9.1 dispatch row."""

    needs_offset = [_time(arguments.get(key)) for key in ("start_time", "end_time")]
    if any(needs_offset):
        raise HistoryTimeOffsetRequired(CATEGORY)
    return _history_9_1.dispatch_arguments(arguments)


def _names(value: Any, acc: set) -> set:
    """The contract's distinct-name walk (generate_output_key_contract.names)."""

    if isinstance(value, dict):
        for key, item in value.items():
            acc.add(key)
            if key not in OPEN and key not in POLICY_DROPS:
                _names(item, acc)
    elif isinstance(value, list):
        for item in value:
            _names(item, acc)
    return acc


def _withhold(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: item if key in OPEN or key in POLICY_DROPS else _withhold(item)
            for key, item in value.items()
            if key in ADMITTED or key in POLICY_DROPS
        }
    if isinstance(value, list):
        return [_withhold(item) for item in value]
    return value


def _reviewed_warnings(warnings: list, arguments: dict) -> tuple[list, int]:
    """Keep the warnings the 9.1 projection's closed templates accept; count the rest."""

    source = arguments.get("source", "history")
    requested = _history_9_1._entity_ids(arguments.get("entity_ids"))
    full = (_history_9_1._STATISTICS_DATA if source == "statistics"
            else _history_9_1._HISTORY_DATA)
    _keys, typo = _history_9_1._expected_keys(full, arguments.get("fields"))
    kept, replaced, seen = [], 0, set()
    for text in warnings:
        kind = None
        if type(text) is str:
            if text == _history_9_1._TZ_FAILED_91:
                kind = "timezone"
            elif typo is not None and text == typo:
                kind = "typo"
            elif source == "statistics":
                unit = _history_9_1._UNIT_91.fullmatch(text)
                reset = _history_9_1._RESET_91.fullmatch(text)
                empty = _history_9_1._EMPTY.fullmatch(text)
                if (unit and unit["entity"] in requested
                        and unit["reason"] in _history_9_1._UNKNOWN_UNIT_REASONS):
                    kind = ("unit", unit["entity"], unit["reason"])
                elif reset and reset["entity"] in requested:
                    kind = ("reset", reset["entity"])
                elif empty and all(i in requested for i in empty["entities"].split(", ")):
                    kind = "empty"
        if kind is None or kind in seen:
            replaced += 1
            continue
        if kind in ("timezone", "typo", "empty"):
            seen.add(kind)
        kept.append(text)
    return kept, replaced


def _bound(value: Any) -> None:
    try:
        if datetime.fromisoformat(value).tzinfo is None:
            raise ValueError("invalid_response")
    except (TypeError, ValueError):
        raise ValueError("invalid_response") from None


def _echoed_bounds(projected: dict, arguments: dict) -> None:
    """Each echoed query bound (history ``period``, statistics ``time_range``) has an offset."""

    data = projected["data"]
    for key in ("period", "time_range"):
        if type(data.get(key)) is dict:
            _bound(data[key]["start"])
            _bound(data[key]["end"])
    if arguments.get("source", "history") == "history" and type(data.get("entities")) is list:
        for entity in data["entities"]:
            _bound(entity["period"]["start"])
            _bound(entity["period"]["end"])


def project(payload: Any, arguments: dict) -> dict:
    """9.2.0 ``ha_get_history`` result -> shipped 8.5 public result plus counts."""

    if type(payload) is not dict:
        raise ValueError("invalid_response")
    names = _names(payload, set())
    withheld = len(names - ADMITTED - POLICY_DROPS)
    dropped = len(names & POLICY_DROPS)
    filtered = _withhold(payload)
    replaced = 0
    warnings = filtered.get("warnings")
    if type(warnings) is list and warnings:
        kept, replaced = _reviewed_warnings(warnings, arguments)
        if kept:
            filtered["warnings"] = kept
        else:
            del filtered["warnings"]
    projected = _history_9_1.project(filtered, arguments)
    _echoed_bounds(projected, arguments)
    if replaced:
        projected["data"].setdefault("warnings", []).append(REPLACED_WARNING)
    projected["fields_withheld"] = min(withheld, COUNT_CAP)
    projected["fields_dropped_by_policy"] = min(dropped, COUNT_CAP)
    projected["upstream_warnings_replaced"] = min(replaced, COUNT_CAP)
    return projected


def completeness(payload: Any) -> tuple[bool, list[str]]:
    """The 9.1 rule: a replaced warning is unverified, so the read is partial."""

    return _history_9_1.completeness(payload)


PROJECTIONS: dict = {"ha_get_history": (project, completeness)}
