"""Synthetic recorder history behind the native Core history read (HAMCP-131, HAMCP-511).

Request semantics follow pinned Home Assistant Core 2026.11.3
``GET /api/history/period/<start>`` for one entity (``filter_entity_id``)
with ``minimal_response``, ``no_attributes`` and
``significant_changes_only=1`` (see ``history_slices/native.py``):

- the state prevailing before ``start`` comes from the last row with
  ``last_updated < start``, only when the recorder holds a row older than
  ``start`` (``_get_oldest_possible_ts``). It is reported AT ``start``, not at
  its own time (the start-state query selects ``literal(value=0)`` as
  ``last_updated_ts`` and the state is built with
  ``last_updated_ts or start_time_ts``);
- changes are rows with ``start < last_updated < end`` (a row exactly at
  ``start`` is in neither set; a row exactly at ``end`` is excluded);
- consecutive equal states are collapsed, except for the
  ``NEED_ATTRIBUTE_DOMAINS``, whose attribute-only updates read as repeated
  states;
- the body is ``[]`` or ``[[rows]]``; row 0, and every row of a
  ``NEED_ATTRIBUTE_DOMAINS`` entity, is a full state with empty attributes
  and ``last_changed == last_updated``; every other row is
  ``{state, last_changed}``. Times are UTC ``isoformat()`` (``+00:00``,
  microseconds only when non-zero).

``FakeReader`` is a per-page ``NativeHistoryReader`` factory over a fake
Core REST client: the real reader, parser and sanitizer run; only the
transport is synthetic. Its ``active`` flag is the tool's Core lease (the
test installs it as the request telemetry's authorizer). Synthetic data
only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json

from ha_mcp_engineering.clients.paths import is_route
from ha_mcp_engineering.clients.single_read import SINGLE_READ
from ha_mcp_engineering.errors import HomeAssistantTimeoutError, HomeAssistantUnavailableError
from ha_mcp_engineering.history_slices import native
from ha_mcp_engineering.request_context import current_telemetry


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
# The exact query the reader must send, in order.
QUERY_KEYS = ("end_time", "filter_entity_id", "minimal_response", "no_attributes", "significant_changes_only")


def us(text: str) -> int:
    """Exact microseconds for a UTC ISO timestamp without an offset."""

    moment = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    return (moment - EPOCH) // timedelta(microseconds=1)


def render(ts_us: int) -> str:
    return (EPOCH + timedelta(microseconds=ts_us)).isoformat()


def parse_us(text: str) -> int:
    return (datetime.fromisoformat(text) - EPOCH) // timedelta(microseconds=1)


@dataclass
class Recorder:
    entity_id: str = "sensor.synthetic_history_131"
    rows: list[tuple[int, str]] = field(default_factory=list)
    # The oldest row the whole recorder holds; None means the entity's own.
    oldest_us: int | None = None
    calls: list[dict] = field(default_factory=list)

    @property
    def full_rows(self) -> bool:
        return self.entity_id.split(".", 1)[0] in native.NEED_ATTRIBUTE_DOMAINS

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
                # Reported at the requested start, not at its own time.
                result.append((start_us, before[-1][1]))
        result.extend(row for row in self.rows if start_us < row[0] < end_us)
        if self.full_rows:
            return result
        collapsed = []
        for row in result:
            if not collapsed or collapsed[-1][1] != row[1]:
                collapsed.append(row)
        return collapsed

    def period_body(self, entity_id: str, start_us: int, end_us: int) -> list:
        """The pinned Core JSON body for one entity and window."""

        rows = self.query(start_us, end_us) if entity_id == self.entity_id else []
        if not rows:
            return []
        return [[core_row(entity_id, ts_us, state, full=index == 0 or self.full_rows)
                 for index, (ts_us, state) in enumerate(rows)]]


def core_row(entity_id: str, ts_us: int, state: str, *, full: bool) -> dict:
    if full:
        return {"entity_id": entity_id, "state": state, "attributes": {},
                "last_changed": render(ts_us), "last_updated": render(ts_us)}
    return {"state": state, "last_changed": render(ts_us)}


class FakeReader:
    """A per-page ``NativeHistoryReader`` factory over a fake Core client.

    Hooks, by Core request index (``len(recorder.calls)``):
    ``before_call(index)`` (before the transport's lease check, so it can
    retire the lease ahead of a dispatch); ``fail_at`` (a set of indexes failing with a
    timeout, or a dict of index to the exception to raise); ``transform``
    (the parsed body to a new body, or to raw text). ``violations`` lists
    every request that is not the exact pinned shape.
    """

    def __init__(self, recorder: Recorder, *, version: str = native.VERSION, contract: str = native.CONTRACT,
                 known_secrets: tuple[str, ...] = ()):
        self.recorder = recorder
        self.version = version
        self.contract = contract
        self.known_secrets = known_secrets
        self.active = True
        self.fail_at: set[int] | dict[int, BaseException] = set()
        self.before_call = None
        self.transform = None
        self.readers: list[native.NativeHistoryReader] = []
        self.violations: list[str] = []

    def __call__(self) -> native.NativeHistoryReader:
        reader = native.NativeHistoryReader(self, known_secrets=self.known_secrets)
        if self.version != native.VERSION:
            reader.version = self.version
        if self.contract != native.CONTRACT:
            reader.contract = self.contract
        self.readers.append(reader)
        return reader

    def current(self) -> bool:
        return self.active

    def _check(self, method, path, body, raw, expected_statuses, logbook_read) -> dict | None:
        if (method != "GET" or body is not None or raw is not True or expected_statuses or logbook_read
                or not is_route(path) or path.template != "/history/period/{}" or path.kinds != ("timestamp",)
                or tuple(key for key, _ in path.query) != QUERY_KEYS):
            self.violations.append(f"request shape: {method} {path!s}")
            return None
        query = dict(path.query)
        if (query["minimal_response"] is not None or query["no_attributes"] is not None
                or query["significant_changes_only"] != "1"):
            self.violations.append(f"query flags: {path!s}")
            return None
        if SINGLE_READ.get() is not True:
            self.violations.append("request outside SINGLE_READ")
        return {"entity_id": query["filter_entity_id"], "start_time": path.values[0], "end_time": query["end_time"]}

    async def request(self, method, path, body=None, raw=False, expected_statuses=frozenset(), *,
                      logbook_read=False):
        call = self._check(method, path, body, raw, expected_statuses, logbook_read)
        if call is None:
            raise HomeAssistantUnavailableError(details={"method": method})
        index = len(self.recorder.calls)
        if self.before_call is not None:
            self.before_call(index)
        # The real transport refuses before dispatch when the lease is gone.
        telemetry = current_telemetry()
        if telemetry is not None and not telemetry.authorize_core_dispatch():
            raise HomeAssistantUnavailableError(details={"method": method})
        if index in self.fail_at:
            self.recorder.calls.append({"failed": True, **call})
            raise self.fail_at[index] if isinstance(self.fail_at, dict) else HomeAssistantTimeoutError()
        self.recorder.calls.append(call)
        result = self.recorder.period_body(call["entity_id"], parse_us(call["start_time"]),
                                           parse_us(call["end_time"]))
        if self.transform is not None:
            result = self.transform(result)
        return result if type(result) is str else json.dumps(result)
