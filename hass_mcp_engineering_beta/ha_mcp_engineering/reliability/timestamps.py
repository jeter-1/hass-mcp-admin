"""Strict UTC timestamp normalization for reliability evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def normalize_timestamp(value: Any) -> str | None:
    """Return one RFC 3339 UTC string or None without inventing time."""

    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_timestamp(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        if not isinstance(value, str) or not value.strip():
            return None
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def normalize_interval(value: Any) -> dict[str, str | None] | None:
    """Normalize scalar or Home Assistant interval timestamps consistently."""

    if isinstance(value, dict):
        started = normalize_timestamp(
            value.get("started_at") or value.get("start") or value.get("started")
        )
        finished = normalize_timestamp(
            value.get("finished_at") or value.get("finish") or value.get("finished")
        )
    else:
        started = normalize_timestamp(value)
        finished = None
    if started is None and finished is None:
        return None
    return {"started_at": started, "finished_at": finished}


def occurrence_timestamp(value: Any) -> str | None:
    """Select the observed instant without fabricating a missing boundary."""

    if not isinstance(value, dict):
        return normalize_timestamp(value)
    return normalize_timestamp(
        value.get("started_at") or value.get("finished_at")
        or value.get("timestamp") or value.get("last_action")
    )


def observation_window(values) -> tuple[str | None, str | None]:
    parsed = []
    for value in values:
        normalized = occurrence_timestamp(value)
        instant = parse_timestamp(normalized)
        if instant is not None:
            parsed.append((instant, normalized))
    if not parsed:
        return None, None
    parsed.sort(key=lambda item: (item[0], item[1]))
    return parsed[0][1], parsed[-1][1]


def newest_first_key(value: dict[str, Any]) -> tuple[float, str]:
    normalized = occurrence_timestamp(value)
    parsed = parse_timestamp(normalized)
    epoch = parsed.timestamp() if parsed is not None else float("-inf")
    return epoch, str(value.get("run_id") or "")
