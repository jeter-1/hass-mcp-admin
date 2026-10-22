"""Synthetic recorder history behind the pinned ``ha_get_history`` shape (HAMCP-131).

Request semantics follow Home Assistant 2026.10.1
``recorder/history/__init__.py`` for one entity with ``minimal_response``:

- the state prevailing before ``start`` is the last row with
  ``last_updated < start`` and is returned with its own timestamp, only when
  the recorder holds a row older than ``start`` (``_get_oldest_possible_ts``);
- changes are rows with ``start < last_updated < end`` (a row exactly at
  ``start`` is in neither set; a row exactly at ``end`` is excluded);
- consecutive equal states are collapsed.

ha-mcp 8.5.0/8.6.0 ``_fetch_history`` (identical AST) issues that one query
for the whole window, slices ``[offset:offset + limit]`` client-side, reports
``total_count``/``has_more``/``next_offset`` and renders timestamps in Home
Assistant local time with an offset. The gateway wraps the upstream result in
its success envelope. Synthetic data only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
LOCAL = timezone(timedelta(hours=-5))


def us(text: str) -> int:
    """Exact microseconds for a UTC ISO timestamp without an offset."""

    moment = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    return (moment - EPOCH) // timedelta(microseconds=1)


def render(ts_us: int) -> str:
    return (EPOCH + timedelta(microseconds=ts_us)).astimezone(LOCAL).isoformat()


@dataclass
class Recorder:
    entity_id: str = "climate.mudroom_thermostat"
    rows: list[tuple[int, str]] = field(default_factory=list)
    # The oldest row the whole recorder holds; None means the entity's own.
    oldest_us: int | None = None
    calls: list[dict] = field(default_factory=list)

    def add(self, ts_us: int, state: str) -> None:
        self.rows.append((ts_us, state))
        self.rows.sort(key=lambda row: row[0])

    def purge_before(self, ts_us: int) -> None:
        self.rows = [row for row in self.rows if row[0] >= ts_us]
        self.oldest_us = None

    def query(self, start_us: int, end_us: int) -> list[tuple[int, str]]:
        oldest = self.oldest_us if self.oldest_us is not None else (
            self.rows[0][0] if self.rows else None)
        result = []
        if oldest is not None and oldest < start_us:
            before = [row for row in self.rows if row[0] < start_us]
            if before:
                result.append(before[-1])
        result.extend(row for row in self.rows if start_us < row[0] < end_us)
        collapsed = []
        for row in result:
            if not collapsed or collapsed[-1][1] != row[1]:
                collapsed.append(row)
        return collapsed

    def upstream(self, entity_id: str, start_time: str, end_time: str, offset: int, limit: int) -> dict:
        """The ha-mcp result inside the gateway success envelope."""

        self.calls.append({"entity_id": entity_id, "start_time": start_time, "end_time": end_time,
                           "offset": offset, "limit": limit})
        start_us = (datetime.fromisoformat(start_time) - EPOCH) // timedelta(microseconds=1)
        end_us = (datetime.fromisoformat(end_time) - EPOCH) // timedelta(microseconds=1)
        rows = self.query(start_us, end_us) if entity_id == self.entity_id else []
        page = rows[offset:offset + limit]
        return history_result(entity_id, page, total=len(rows), offset=offset, limit=limit)


def history_result(entity_id: str, page, *, total: int, offset: int, limit: int) -> dict:
    states = [{"state": state, "last_changed": render(ts), "last_updated": render(ts)} for ts, state in page]
    upstream = {
        "data": {
            "success": True, "source": "history",
            "entities": [{
                "entity_id": entity_id, "period": {}, "states": states,
                "total_count": total, "offset": offset, "limit": limit, "count": len(states),
                "has_more": offset + len(states) < total,
                "next_offset": offset + len(states) if offset + len(states) < total else None,
            }],
        },
        "metadata": {"home_assistant_timezone": "Synthetic/Local"},
    }
    return {"operation": "ha_get_history", "summary": "synthetic", "data": upstream, "warnings": [],
            "metadata": {"provider": "upstream_read_gateway", "upstream_tool": "ha_get_history",
                         "upstream_version": "8.6.0", "completeness": "complete"},
            "success": True}


class FakeReader:
    """The ``HistoryReader`` interface over a synthetic recorder."""

    def __init__(self, recorder: Recorder, *, version: str = "8.6.0", contract: str = "synthetic-contract"):
        self.recorder = recorder
        self.version = version
        self.contract = contract
        self.provenance = f"ha-mcp {version}; contract {contract}; one entity per request; no fallback"
        self.active = True
        self.fail_at: set[int] = set()
        self.before_call = None
        self.transform = None

    def current(self) -> bool:
        return self.active

    async def read(self, entity_id, start_time, end_time, offset, limit):
        index = len(self.recorder.calls)
        if self.before_call is not None:
            self.before_call(index)
        if index in self.fail_at:
            self.recorder.calls.append({"failed": True})
            return {"success": False, "error_code": "PROVIDER_UNAVAILABLE",
                    "details": {"failure_category": "transport_timeout"}}
        result = self.recorder.upstream(entity_id, start_time, end_time, offset, limit)
        return self.transform(result) if self.transform else result
