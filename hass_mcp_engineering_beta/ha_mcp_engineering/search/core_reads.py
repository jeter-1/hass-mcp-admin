"""Sequential fixed Core reads with fail-closed projection (HAMCP-481 W2).

A ``CoreReadSession`` is opened for a declared set of reads from
``core_contracts.READS``. Opening it checks the Core route authority of
exactly those reads' requirements; every read re-checks that the authority
(route generation and observed Core version) is unchanged before and after
it is dispatched. A change, or a read that was not declared, refuses the
whole session; nothing falls back.

Each reply is projected through its fixed shape. A wrong top-level shape is
an ``unavailable`` result. A malformed or duplicate row is dropped, and a
field that is absent or of the wrong kind becomes ``INVALID``; either makes
the result ``partial`` with a closed reason. Exception text and provider
values never enter a result or a source record.

Replies are not sanitized here; the caller that builds a client-visible
envelope sanitizes it, as every Engineering tool does.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Awaitable, Callable

from ..clients.paths import rest_path
from ..clients.rest import ExpectedHttpStatus
from ..entity_ids import STRICT_ENTITY_ID
from ..errors import (
    AuthorizationError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ..observability import METRICS
from ..request_context import current_caller_id, current_telemetry
from . import core_contracts as c


class _Invalid:
    """A present field whose value does not match the fixed shape."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "INVALID"


INVALID = _Invalid()


@dataclass(frozen=True, slots=True)
class ReadResult:
    """One read's closed outcome and its projected value.

    ``observed`` is set only on ``inventory_bound_exceeded``: the length of
    the list Core returned, whose body is withheld (design section 6.3).
    """

    read: str
    status: str
    reason: str | None = None
    value: Any = None
    partial: tuple[str, ...] = ()
    dropped: int = 0
    items: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))
    observed: int | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def rest_route(name: str, *values: str):
    """The route of a fixed REST read, from a literal ``rest_path`` template.

    The HAMCP-200 path-safety scan allows a route template only as a literal
    ``rest_path`` argument, so the table's REST ``command`` is descriptive.
    """

    if name == "states" and not values:
        return rest_path("/states")
    if name == "scene_config" and len(values) == 1:
        return rest_path("/config/scene/config/{}", *values)
    raise c.CoreReadError("invalid_arguments")


def _unavailable(read: str, reason: str) -> ReadResult:
    return ReadResult(read, c.UNAVAILABLE,
                      reason if reason in c.UNAVAILABLE_REASONS else "source_unavailable")


def _finished(read: str, value: Any, partial: set[str], dropped: int = 0, items=None) -> ReadResult:
    if not partial <= c.PARTIAL_REASONS:
        # Only the closed vocabulary leaves this module.
        partial = (partial & c.PARTIAL_REASONS) | {"field_drift"}
    return ReadResult(read, c.PARTIAL if partial else c.COMPLETE, None, value,
                      tuple(sorted(partial)), dropped,
                      MappingProxyType(dict(items or {})))


def _kind_valid(kind: str, value: Any) -> bool:
    if kind == c.STR:
        return type(value) is str
    if kind == c.OPTIONAL_STR:
        return value is None or type(value) is str
    if kind == c.STR_LIST:
        return type(value) is list and all(type(item) is str for item in value)
    if kind == c.MAPPING:
        return type(value) is dict
    if kind == c.BOOL:
        return type(value) is bool
    if kind == c.OPTIONAL_INT:
        return value is None or type(value) is int
    raise AssertionError(kind)


def _row_key_valid(read: c.CoreRead, value: Any) -> bool:
    pattern = STRICT_ENTITY_ID if read.key == "entity_id" else c.ROW_ID
    return type(value) is str and pattern.fullmatch(value) is not None


def project_rows(name: str, raw: Any) -> ReadResult:
    """A list read, keyed by row identity in Core's order."""

    read = c.READS[name]
    if type(raw) is not list:
        return _unavailable(name, "invalid_response")
    if len(raw) > c.MAX_ITEMS:
        # The body is withheld; only its length is reported.
        return ReadResult(name, c.UNAVAILABLE, "inventory_bound_exceeded", observed=len(raw))
    fields = c.ROW_FIELDS[name]
    optional = c.OPTIONAL_FIELDS.get(name, frozenset())
    rows: dict[str, dict] = {}
    partial: set[str] = set()
    dropped = 0
    for row in raw:
        identity = row.get(read.key) if type(row) is dict else None
        if not _row_key_valid(read, identity):
            partial.add("malformed_rows")
            dropped += 1
            continue
        if identity in rows:
            # Never a guess between two rows that claim one identity.
            partial.add("duplicate_rows")
            dropped += 1
            continue
        projected = dict(row) if name in c.WHOLE_ROWS else {read.key: identity}
        for field_name, kind in fields.items():
            if field_name not in row:
                if field_name not in optional:
                    projected[field_name] = INVALID
                    partial.add("field_drift")
                continue
            value = row[field_name]
            if not _kind_valid(kind, value):
                projected[field_name] = INVALID
                partial.add("field_drift")
            else:
                projected[field_name] = tuple(value) if kind == c.STR_LIST else value
        rows[identity] = projected
    if raw and not rows:
        # A populated reply with no usable row is a changed shape, not an
        # empty inventory.
        return _unavailable(name, "invalid_response")
    return _finished(name, MappingProxyType(rows), partial, dropped)


def project_config(name: str, outcome: Any) -> ReadResult:
    """One ``automation/config`` or ``script/config`` batch outcome."""

    if type(outcome) is not tuple or len(outcome) != 2:
        return _unavailable(name, "invalid_response")
    ok, value = outcome
    if ok is not True:
        if ok is False and value == "not_found":
            return ReadResult(name, c.NOT_FOUND, "not_found")
        return _unavailable(name, "command_failed")
    if type(value) is not dict or type(value.get("config")) is not dict:
        return _unavailable(name, "invalid_response")
    return _finished(name, value["config"], {"field_drift"} if set(value) != {"config"} else set())


def project_related(outcome: Any) -> ReadResult:
    """One ``search/related`` batch outcome: the modelled buckets only."""

    name = "search_related"
    if type(outcome) is not tuple or len(outcome) != 2:
        return _unavailable(name, "invalid_response")
    ok, value = outcome
    if ok is not True:
        return _unavailable(name, "command_failed")
    if type(value) is not dict:
        return _unavailable(name, "invalid_response")
    buckets: dict[str, Any] = {}
    partial: set[str] = set()
    for item_type, items in value.items():
        if item_type not in c.CORE_RELATED_TYPES:
            partial.add("field_drift")
        elif item_type not in c.RELATED_TYPES:
            continue
        elif type(items) is list and all(type(item) is str and c.ROW_ID.fullmatch(item) for item in items):
            buckets[item_type] = tuple(items)
        else:
            buckets[item_type] = INVALID
            partial.add("field_drift")
    return _finished(name, MappingProxyType(buckets), partial)


def project_scene_config(raw: Any) -> ReadResult:
    name = "scene_config"
    if isinstance(raw, ExpectedHttpStatus) and raw.status == 404:
        return ReadResult(name, c.NOT_FOUND, "not_found")
    if type(raw) is not dict:
        return _unavailable(name, "invalid_response")
    return _finished(name, raw, set())


def _aliases(entry: dict) -> Any:
    """String aliases only; ``None`` is Core's computed-name sentinel."""

    aliases = entry.get("aliases")
    if type(aliases) is not list or not all(item is None or type(item) is str for item in aliases):
        return INVALID
    return tuple(item for item in aliases if type(item) is str)


def project_entries(chunk: tuple[str, ...], raw: Any) -> tuple[dict[str, Any], set[str]] | None:
    """One ``get_entries`` chunk; ``None`` when the reply is not a mapping."""

    if type(raw) is not dict:
        return None
    entries: dict[str, Any] = {}
    partial: set[str] = set()
    if not set(raw) <= set(chunk):
        partial.add("field_drift")
    for entity_id in chunk:
        if entity_id not in raw:
            partial.add("field_drift")
            continue
        entry = raw[entity_id]
        if entry is None:
            # Core has no registry entry for this entity.
            entries[entity_id] = None
        elif type(entry) is not dict:
            entries[entity_id] = INVALID
            partial.add("field_drift")
        else:
            aliases = _aliases(entry)
            if aliases is INVALID:
                partial.add("field_drift")
            entries[entity_id] = MappingProxyType({"aliases": aliases})
    return entries, partial


class CoreReadSession:
    """The fixed reads of one search, under one unchanged Core authority."""

    def __init__(self, rest_client, websocket_client, core_runtime, reads, *,
                 clock: Callable[[], datetime] = utc_now):
        names = tuple(reads) if type(reads) in (tuple, list, frozenset, set) else None
        if (not names or not all(type(name) is str and name in c.READS for name in names)
                or len(set(names)) != len(names)):
            raise c.CoreReadError("invalid_arguments")
        if current_caller_id() == "anonymous":
            raise c.CoreReadError("authority_unavailable")
        self.rest_client = rest_client
        self.client = websocket_client
        self.core_runtime = core_runtime
        self.clock = clock
        self.reads = frozenset(names)
        self.requirements = c.requirements_for(self.reads)
        self.sources: list[dict] = []
        self._authority = self.authority()

    def authority(self):
        status = self.core_runtime.route_status(self.requirements)
        observation = self.core_runtime.current_observation
        telemetry = current_telemetry()
        if (type(status) is not dict or status.get("available") is not True
                or status.get("generation") is None or observation is None
                or telemetry is None or telemetry.core_dispatch_authorizer is None
                or not telemetry.authorize_core_dispatch()):
            raise c.CoreReadError("authority_unavailable")
        return status["generation"], observation.version

    def _declared(self, name: str, shape: str) -> c.CoreRead:
        read = c.READS.get(name) if type(name) is str else None
        if read is None or read.shape != shape or name not in self.reads:
            raise c.CoreReadError("invalid_arguments")
        return read

    async def _dispatch(self, call: Callable[[], Awaitable[Any]]) -> tuple[Any, str | None]:
        if self.authority() != self._authority:
            raise c.CoreReadError("authority_unavailable")
        telemetry = current_telemetry()
        attempts_before = telemetry.ha_request_count if telemetry else 0
        raw, reason, outcome = None, None, "failed"
        try:
            raw = await call()
            outcome = "complete"
        except asyncio.CancelledError:
            raise
        except AuthorizationError:
            reason = "permission_failure"
        except HomeAssistantTimeoutError:
            reason = "timeout"
        except HomeAssistantUnavailableError:
            reason = "home_assistant_unavailable"
        except HomeAssistantApiError:
            reason = "command_failed"
        except Exception:
            # Exception text and provider values never enter a result.
            reason = "source_unavailable"
        finally:
            # A cancelled read is a failure, never a completed one.
            METRICS.record_provider_result(
                "direct_ha_api", outcome,
                dispatched=bool(telemetry and telemetry.ha_request_count > attempts_before),
            )
        if self.authority() != self._authority:
            raise c.CoreReadError("authority_unavailable")
        return raw, reason

    def _record(self, result: ReadResult) -> ReadResult:
        self.sources.append({
            "source": result.read, "status": result.status, "reason": result.reason,
            "partial": list(result.partial), "observed": result.observed,
            "observed_at": iso(self.clock()),
        })
        return result

    async def inventory(self, name: str) -> ReadResult:
        """A whole list: the states, a registry or a helper collection."""

        read = self._declared(name, "rows")
        if read.transport == "rest":
            call = lambda: self.rest_client.request("GET", rest_route(name))  # noqa: E731
        else:
            call = lambda: self.client.command({"type": read.command})  # noqa: E731
        raw, reason = await self._dispatch(call)
        return self._record(_unavailable(name, reason) if reason else project_rows(name, raw))

    async def batch(self, name: str, items) -> ReadResult:
        """``automation/config``, ``script/config`` or ``search/related``,
        one pipelined payload per item; each item keeps its own outcome."""

        read = self._declared(name, "related" if name == "search_related" else "config")
        items = tuple(items) if type(items) in (tuple, list) else None
        if (items is None or len(items) > c.MAX_BATCH_ITEMS
                or not all(type(item) is str for item in items) or len(set(items)) != len(items)):
            raise c.CoreReadError("invalid_arguments")
        payloads = [c.batch_payload(name, item) for item in items]
        if not items:
            # Nothing is dispatched, so no source is recorded.
            return _finished(name, None, set())
        raw, reason = await self._dispatch(lambda: self.client.command_batch(payloads))
        if reason:
            return self._record(_unavailable(name, reason))
        if type(raw) is not list or len(raw) != len(items):
            return self._record(_unavailable(name, "invalid_response"))
        project = project_related if read.shape == "related" else (
            lambda outcome: project_config(name, outcome))
        outcomes = {item: project(outcome) for item, outcome in zip(items, raw)}
        failed = sum(1 for outcome in outcomes.values() if outcome.status == c.UNAVAILABLE)
        if failed == len(items):
            return self._record(_unavailable(name, "command_failed"))
        partial = {"item_failures"} if failed else set()
        if any(outcome.status == c.PARTIAL for outcome in outcomes.values()):
            partial.add("field_drift")
        return self._record(_finished(name, None, partial, items=outcomes))

    async def scene_config(self, config_id: str) -> ReadResult:
        read = self._declared("scene_config", "mapping")
        if type(config_id) is not str or read.argument.fullmatch(config_id) is None:
            raise c.CoreReadError("invalid_arguments")
        raw, reason = await self._dispatch(lambda: self.rest_client.request(
            "GET", rest_route(read.name, config_id), expected_statuses=frozenset({404})))
        return self._record(_unavailable(read.name, reason) if reason else project_scene_config(raw))

    async def entity_entries(self, entity_ids) -> ReadResult:
        """Registry aliases by entity, in chunks of ``GET_ENTRIES_CHUNK``.

        A failed chunk leaves its entities out and makes the result partial,
        as ha-mcp 9.2.0's alias enrichment does; every chunk failing makes it
        unavailable.
        """

        name = "entity_registry_entries"
        self._declared(name, "entries")
        entity_ids = tuple(entity_ids) if type(entity_ids) in (tuple, list) else None
        if (entity_ids is None or len(entity_ids) > c.MAX_ITEMS
                or not all(type(item) is str for item in entity_ids)
                or len(set(entity_ids)) != len(entity_ids)):
            raise c.CoreReadError("invalid_arguments")
        chunks = [entity_ids[start:start + c.GET_ENTRIES_CHUNK]
                  for start in range(0, len(entity_ids), c.GET_ENTRIES_CHUNK)]
        payloads = [c.entries_payload(chunk) for chunk in chunks]
        if not chunks:
            return _finished(name, MappingProxyType({}), set())
        entries: dict[str, Any] = {}
        partial: set[str] = set()
        reasons: list[str] = []
        for chunk, payload in zip(chunks, payloads):
            raw, reason = await self._dispatch(lambda payload=payload: self.client.command(payload))
            projected = None if reason else project_entries(chunk, raw)
            if projected is None:
                reasons.append(reason or "invalid_response")
                continue
            chunk_entries, chunk_partial = projected
            entries.update(chunk_entries)
            partial |= chunk_partial
        if len(reasons) == len(chunks):
            return self._record(_unavailable(name, reasons[0]))
        if reasons:
            partial.add("item_failures")
        return self._record(_finished(name, MappingProxyType(entries), partial))
