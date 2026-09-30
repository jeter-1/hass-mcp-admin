"""Closed, bounded native logbook read and truthful source-order projection."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import math
import re
from urllib.parse import urlencode

import aiohttp

from ..errors import (
    ErrorCode, GovernanceError, HomeAssistantApiError,
    HomeAssistantUnavailableError, InvalidRequestError,
)
from ..sanitization import sanitize_untrusted_data
from ..models.responses import logbook_data_limit

MAX_BYTES = 1024 * 1024
MAX_RECORDS = 10_000
MAX_ITEMS = 50_000
MAX_DEPTH = 32
MAX_HOURS = 168
MAX_SECONDS = 30
ENTITY_ID = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+", re.ASCII)
NARROW = "Reduce hours or select one entity."


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


def project(text, interval, response_limit, secrets):
    records = _decode(text)
    # Size the actual minimal public receipt instead of reserving a fixed
    # envelope larger than small response limits. Final projection preserves
    # these records, provider completeness and the current request identity.
    budget = logbook_data_limit(response_limit)
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

    async def read(self, client, *, hours, entity_id, response_limit, secrets=()):
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
            query = {"end_time": interval["end"]}
            if entity_id:
                query["entity"] = entity_id
            path = "/logbook/" + interval["start"] + "?" + urlencode(query)
            text = await client.request("GET", path, raw=True, logbook_read=True)
            worker = asyncio.create_task(asyncio.to_thread(project, text, interval, response_limit, secrets))
            return await asyncio.shield(worker)
        finally:
            if worker is not None and not worker.done():
                worker.add_done_callback(self._finished)
            else:
                self._active = False


LOGBOOK = LogbookReader()
