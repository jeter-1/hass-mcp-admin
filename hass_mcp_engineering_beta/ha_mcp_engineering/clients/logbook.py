"""Closed, bounded native logbook read and truthful source-order projection."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import math
import re

import aiohttp

from ..errors import (
    ErrorCode, GovernanceError, HomeAssistantApiError,
    HomeAssistantUnavailableError, InvalidRequestError,
)
from ..sanitization import sanitize_untrusted_data
from ..models.responses import logbook_data_limit
from .paths import rest_path

MAX_BYTES = 1024 * 1024
MAX_RECORDS = 10_000
MAX_ITEMS = 50_000
MAX_DEPTH = 32
MAX_HOURS = 168
MAX_SECONDS = 30
ENTITY_ID = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+", re.ASCII)
NARROW = "Reduce hours or select one entity."
# HAMCP-294 bounded window and filter. Bounds are implementation defaults
# pending Josh (design note Q1): window cap unchanged, at most ten entities,
# a 512-character joined filter (the fixed query-value bound) and a
# 100-character name.
DEFAULT_HOURS = 12
MAX_FILTER_ENTITIES = 10
MAX_FILTER_CHARS = 512
MAX_NAME_CHARS = 100
MAX_WHEN_CHARS = 64
# The REST path's timestamp grammar (``clients.paths``): seconds and an
# explicit offset are required, so a naive time is never guessed.
TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z", re.ASCII
)
CONTINUATION_MARGIN = timedelta(microseconds=1)
# Core records no logbook rows for these domains (Core ``logbook`` const).
CONTINUOUS_DOMAINS = frozenset({"counter", "image", "proximity"})
NAME_MATCH_BASIS = "casefold_substring_of_current_entity_id_or_friendly_name"
NARROW_WINDOW = ("Narrow start_time/end_time or the entity filter (entity_ids or name); "
                 "continuation names the next window.")
BASE_NOTES = ("recorder_retention_not_checked", "core_exclusions_not_observed")


def limit_error(reason, limit):
    return GovernanceError(
        ErrorCode.LOGBOOK_RESPONSE_LIMIT_EXCEEDED,
        details={"reason": reason, "limit": limit, "endpoint_category": "logbook"},
    )


def invalid_response():
    return HomeAssistantApiError("Home Assistant returned an invalid logbook response.",
                                 details={"endpoint_category": "logbook"})


async def no_retry(request, handler):
    try:
        response = await handler(request)
    except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError):
        # Convert before aiohttp's idempotent GET reconnect loop.
        raise HomeAssistantUnavailableError() from None
    if 300 <= response.status < 400:
        response.close()
        raise invalid_response()
    return response


async def read_body(response):
    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
        raise invalid_response()
    chunks, size = [], 0
    async for chunk in response.content.iter_chunked(16_384):
        size += len(chunk)
        if size > MAX_BYTES:
            raise limit_error("response_bytes", MAX_BYTES)
        chunks.append(chunk)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeError:
        raise invalid_response() from None


def _decode(text):
    # Reject excessive nesting before the decoder can recurse. Quotes/escapes
    # are honored; this is only a depth guard, never a replacement JSON parser.
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                raise limit_error("structure_depth", MAX_DEPTH)
        elif char in "]}":
            depth -= 1

    def reject_constant(value):
        raise ValueError("nonfinite_json")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    try:
        records = json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_object)
    except (ValueError, RecursionError):
        raise invalid_response() from None
    if not isinstance(records, list):
        raise invalid_response()
    if len(records) > MAX_RECORDS:
        raise limit_error("record_count", MAX_RECORDS)
    # Iterator frames avoid a second unbounded wide-container allocation.
    stack, examined = [iter((records,))], 0
    while stack:
        try:
            item = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        examined += 1
        if examined > MAX_ITEMS:
            raise limit_error("structure_items", MAX_ITEMS)
        if isinstance(item, dict):
            examined += len(item)  # Keys are examined too, including rejected members.
            if examined > MAX_ITEMS:
                raise limit_error("structure_items", MAX_ITEMS)
            stack.append(iter(item.values()))
        elif isinstance(item, list):
            stack.append(iter(item))
        elif isinstance(item, float) and not math.isfinite(item):
            raise invalid_response()
    if any(not isinstance(record, dict) for record in records):
        raise invalid_response()
    return records


def _json(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def _fit(records, budget, secrets):
    selected, sizes, size = [], [], 2
    for record in records:
        # Only sanitize records that could be retained. Oversized raw scalars
        # cannot drag expensive text sanitization across the complete body.
        if size + len(_json(record)) + bool(selected) > budget:
            break
        sanitized = sanitize_untrusted_data(record, known_secrets=secrets)
        if sanitized.failed_closed:
            raise invalid_response()
        item = sanitized.value
        length = len(_json(item))
        if size + length + bool(selected) > budget:
            break
        size += length + bool(selected)
        selected.append(item)
        sizes.append(length)
    return selected, sizes, size


def project(text, interval, response_limit, secrets):
    records = _decode(text)
    # Size the actual minimal public receipt instead of reserving a fixed
    # envelope larger than small response limits. Final projection preserves
    # these records, provider completeness and the current request identity.
    budget = logbook_data_limit(response_limit)
    selected, sizes, size = _fit(records, budget, secrets)
    if len(selected) == len(records):
        return _json(selected)
    partial = {"entries": [], "truncated": True, "returned": 0, "omitted": len(records),
               "requested_interval": interval, "ordering": "source_order", "required_action": NARROW}
    overhead = len(_json(partial)) - 2  # Replace [] with the retained list.
    while selected:
        count = len(selected)
        digits = len(str(count)) - 1 + len(str(len(records)-count)) - len(str(len(records)))
        if overhead + size + digits <= budget:
            partial.update(entries=selected, returned=count, omitted=len(records)-count)
            return _json(partial)
        size -= sizes.pop() + (len(selected) > 1)
        selected.pop()
    raise limit_error("response_bytes", budget)


def _when(record):
    """A record's ``when`` as an aware UTC time, or None when unusable."""
    value = record.get("when")
    if type(value) is not str or len(value) > MAX_WHEN_CHARS:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _omitted_minima(records):
    """``minima[i]``: earliest ``when`` over ``records[i:]``; False if any is unusable."""
    minima = [None] * (len(records) + 1)
    for index in range(len(records) - 1, -1, -1):
        later = minima[index + 1]
        when = _when(records[index])
        if when is None or later is False:
            minima[index] = False
        else:
            minima[index] = when if later is None else min(when, later)
    return minima


def _continuation(minimum, window):
    if minimum is False:
        return {"start_time": None, "end_time": None, "overlap_possible": True,
                "reason": "omitted_when_unavailable"}
    start = minimum - CONTINUATION_MARGIN
    if not window["start"] <= start < window["end"]:
        return {"start_time": None, "end_time": None, "overlap_possible": True,
                "reason": "omitted_when_outside_interval"}
    return {"start_time": start.isoformat(), "end_time": window["end"].isoformat(),
            "overlap_possible": True, "reason": None}


def project_window(text, context, response_limit, secrets):
    """HAMCP-294: the windowed/filtered result object, always one object.

    The same whole-record source-order prefix as ``project``. A truncated
    result names a continuation window starting just before the earliest
    omitted row, so no ordering promise is needed; overlap is disclosed.
    """
    records = _decode(text)
    budget = logbook_data_limit(response_limit)
    selected, sizes, size = _fit(records, budget, secrets)
    minima = _omitted_minima(records)
    total = len(records)
    while True:
        count = len(selected)
        if count or not total:
            truncated = count < total
            value = {
                "entries": [], "truncated": truncated, "returned": count,
                "omitted": total - count,
                "requested_interval": context["requested_interval"],
                "effective_interval": context["effective_interval"],
                "interval_bounds": "exclusive", "ordering": "source_order",
                "filter": context["filter"],
                "continuation": (_continuation(minima[count], context["window"])
                                 if truncated else None),
                "completeness_notes": context["completeness_notes"],
                "required_action": NARROW_WINDOW if truncated else None,
            }
            # Replace [] with the retained list's exact serialized size.
            if len(_json(value)) - 2 + size <= budget:
                value["entries"] = selected
                return _json(value)
        if not selected:
            raise limit_error("response_bytes", budget)
        size -= sizes.pop() + (len(selected) > 1)
        selected.pop()


def _timestamp(value, field):
    message = (f"{field} must be an ISO-8601 time with seconds and an explicit offset "
               "(Z or +HH:MM).")
    if type(value) is not str or not TIMESTAMP.fullmatch(value):
        raise InvalidRequestError(message)
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise InvalidRequestError(message) from None


def _valid_hours(hours):
    # Range first: math.isfinite overflows on a huge int.
    return (type(hours) in (int, float) and 0 < hours <= MAX_HOURS
            and math.isfinite(hours))


def resolve_window(now, hours, start_time, end_time):
    """Requested and effective windows; refuse before any request or slot."""
    if start_time is not None and hours is not None:
        # The public hours default stays 12 (v1.1.2 parity), so an explicit
        # start takes precedence over that default; any other hours conflicts.
        if not (_valid_hours(hours) and hours == DEFAULT_HOURS):
            raise InvalidRequestError(
                "hours must be omitted (default 12) when start_time is set.")
        hours = None
    if hours is not None and not _valid_hours(hours):
        raise InvalidRequestError("hours must be finite, positive, and no greater than 168.")
    end = now if end_time is None else _timestamp(end_time, "end_time")
    if start_time is not None:
        start = _timestamp(start_time, "start_time")
    else:
        try:
            start = end - timedelta(hours=DEFAULT_HOURS if hours is None else hours)
        except OverflowError:
            raise InvalidRequestError("end_time is out of range.") from None
    if start >= end:
        raise InvalidRequestError("start_time must be before end_time.")
    if end - start > timedelta(hours=MAX_HOURS):
        raise InvalidRequestError("The window must be no longer than 168 hours.")
    if start >= now:
        raise InvalidRequestError("The window must start before now.")
    effective_end = min(end, now)
    return {
        "start": start, "end": effective_end,
        "requested_interval": {"start": start.isoformat(), "end": end.isoformat()},
        "effective_interval": {"start": start.isoformat(), "end": effective_end.isoformat(),
                               "end_clamped": end > now},
    }


def _valid_entity(value):
    return (type(value) is str and len(value) <= 255
            and ENTITY_ID.fullmatch(value) is not None)


def resolve_filter(entity_id, entity_ids, name):
    """Validate the one entity filter; name matching happens under the slot."""
    if not isinstance(entity_id, str) or len(entity_id) > 255 or (
            entity_id and ENTITY_ID.fullmatch(entity_id) is None):
        raise InvalidRequestError("entity_id must be empty or one valid entity ID of at most 255 characters.")
    if bool(entity_id) + (entity_ids is not None) + (name is not None) > 1:
        raise InvalidRequestError("Use at most one of entity_id, entity_ids and name.")
    if entity_ids is not None:
        if (type(entity_ids) not in (list, tuple)
                or not 1 <= len(entity_ids) <= MAX_FILTER_ENTITIES
                or not all(_valid_entity(item) for item in entity_ids)
                or len(set(entity_ids)) != len(entity_ids)
                or len(",".join(entity_ids)) > MAX_FILTER_CHARS):
            raise InvalidRequestError(
                "entity_ids must be 1-10 unique valid entity IDs joining to at most 512 characters.")
        return {"kind": "entity_ids", "entity_ids": list(entity_ids)}
    if name is not None:
        if type(name) is not str or len(name) > MAX_NAME_CHARS or not name.strip():
            raise InvalidRequestError("name must be 1-100 characters.")
        return {"kind": "name", "name": name}
    if entity_id:
        return {"kind": "entity_id", "entity_ids": [entity_id]}
    return {"kind": "none", "entity_ids": []}


def _states_error():
    return HomeAssistantApiError(
        details={"endpoint_category": "states_inventory", "status": "malformed_response"})


async def resolve_name(client, name):
    """Current-state matches for ``name`` (parity with ``search_entities``)."""
    states = await client.request("GET", rest_path("/states"))
    if not isinstance(states, list):
        raise _states_error()
    needle, matches = name.strip().casefold(), {}
    for item in states:
        if not isinstance(item, dict):
            continue
        entity_id = item.get("entity_id")
        if not _valid_entity(entity_id):
            continue
        attributes = item.get("attributes")
        attributes = attributes if isinstance(attributes, dict) else {}
        friendly = attributes.get("friendly_name")
        friendly = friendly if isinstance(friendly, str) else ""
        if needle in entity_id.casefold() or needle in friendly.casefold():
            matches[entity_id] = attributes
    ids = sorted(matches)
    if not ids:
        raise InvalidRequestError(
            "name matched no current entity.",
            details={"reason": "name_matched_no_entity", "match_count": 0})
    if len(ids) > MAX_FILTER_ENTITIES or len(",".join(ids)) > MAX_FILTER_CHARS:
        reason = ("name_matched_too_many" if len(ids) > MAX_FILTER_ENTITIES
                  else "name_matches_exceed_filter_bound")
        raise InvalidRequestError(
            "name matched too many entities; pass entity_ids instead.",
            details={"reason": reason, "match_count": len(ids),
                     "matched_entity_ids": ids[:MAX_FILTER_ENTITIES]})
    return ids, {entity_id: matches[entity_id] for entity_id in ids}


def _continuous(entity_ids, attributes):
    for entity_id in entity_ids:
        domain = entity_id.partition(".")[0]
        if domain in CONTINUOUS_DOMAINS:
            return True
        state = attributes.get(entity_id, {})
        if domain == "sensor" and ("unit_of_measurement" in state or "state_class" in state):
            return True
    return False


class LogbookReader:
    """One active acquisition/processor, including a cancelled worker's drain."""

    def __init__(self):
        self._active = False

    def _finished(self, worker):
        try:
            worker.result()
        except BaseException:
            pass  # Consume the detached outcome; never log response/exception text.
        self._active = False

    async def read(self, client, *, hours, entity_id, response_limit, secrets=(),
                   start_time=None, end_time=None, entity_ids=None, name=None):
        if start_time is None and end_time is None and entity_ids is None and name is None:
            return await self._read_legacy(client, hours=hours, entity_id=entity_id,
                                           response_limit=response_limit, secrets=secrets)
        window = resolve_window(datetime.now(timezone.utc), hours, start_time, end_time)
        selection = resolve_filter(entity_id, entity_ids, name)
        if self._active:
            raise GovernanceError(ErrorCode.LOGBOOK_BUSY)
        self._active = True
        worker = None
        try:
            # The slot covers the name's states read and the one logbook GET.
            notes, attributes = list(BASE_NOTES), {}
            if selection["kind"] == "name":
                ids, attributes = await resolve_name(client, selection["name"])
                echoed = sanitize_untrusted_data(selection["name"], known_secrets=secrets)
                selection = {"kind": "name", "entity_ids": ids,
                             "name": None if echoed.failed_closed else echoed.value,
                             "match_basis": NAME_MATCH_BASIS, "matched_count": len(ids)}
                notes.append("name_resolved_from_current_states")
            else:
                selection = {**selection, "name": None, "match_basis": None, "matched_count": None}
            if _continuous(selection["entity_ids"], attributes):
                notes.append("continuous_entities_produce_no_rows")
            query = [("end_time", window["effective_interval"]["end"])]
            if selection["entity_ids"]:
                query.append(("entity", ",".join(selection["entity_ids"])))
            path = rest_path(
                "/logbook/{}", window["effective_interval"]["start"], kinds=("timestamp",), query=query
            )
            context = {"window": window, "requested_interval": window["requested_interval"],
                       "effective_interval": window["effective_interval"],
                       "filter": selection, "completeness_notes": notes}
            text = await client.request("GET", path, raw=True, logbook_read=True)
            worker = asyncio.create_task(
                asyncio.to_thread(project_window, text, context, response_limit, secrets))
            return await asyncio.shield(worker)
        finally:
            if worker is not None and not worker.done():
                worker.add_done_callback(self._finished)
            else:
                self._active = False

    async def _read_legacy(self, client, *, hours, entity_id, response_limit, secrets):
        if (type(hours) not in (int, float) or not 0 < hours <= MAX_HOURS
                or not math.isfinite(hours)):
            raise InvalidRequestError("hours must be finite, positive, and no greater than 168.")
        if (not isinstance(entity_id, str) or len(entity_id) > 255
                or (entity_id and ENTITY_ID.fullmatch(entity_id) is None)):
            raise InvalidRequestError("entity_id must be empty or one valid entity ID of at most 255 characters.")
        if self._active:
            raise GovernanceError(ErrorCode.LOGBOOK_BUSY)
        self._active = True
        worker = None
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(hours=hours)
            interval = {"start": start.isoformat(), "end": end.isoformat()}
            query = [("end_time", interval["end"])]
            if entity_id:
                query.append(("entity", entity_id))
            path = rest_path(
                "/logbook/{}", interval["start"], kinds=("timestamp",), query=query
            )
            text = await client.request("GET", path, raw=True, logbook_read=True)
            worker = asyncio.create_task(asyncio.to_thread(project, text, interval, response_limit, secrets))
            return await asyncio.shield(worker)
        finally:
            if worker is not None and not worker.done():
                worker.add_done_callback(self._finished)
            else:
                self._active = False


LOGBOOK = LogbookReader()
