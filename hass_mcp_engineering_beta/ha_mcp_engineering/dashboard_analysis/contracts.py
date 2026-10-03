"""Closed arguments, strict decoding and owned work for dashboard analysis."""

import asyncio
import hashlib
import json
import re

from ..errors import ErrorCode, GovernanceError

MODEL = "dashboard-integrity-v1"
REQUIREMENTS = ("core.dashboard_configuration_read", "core.basic_rest_read",
                "core.non_device_registry_read")
DASHBOARD_BYTES = 2 * 1024 * 1024
INVENTORY_BYTES = 4 * 1024 * 1024
TOTAL_BYTES = 10 * 1024 * 1024
AUTH_BYTES = 65_536
INVENTORY_ENTRIES = 10_000
INVENTORY_NODES = 100_000
INVENTORY_DEPTH = 16
SCAN_NODES = 10_000
SCAN_DEPTH = 32
UNIQUE_REFERENCES = 512
ITEMS = 1024
SCALAR_CHARS = 2000
SNAPSHOT_BYTES = 512 * 1024
SNAPSHOTS = 2
TTL_SECONDS = 300
COLLECTION_SECONDS = 30
READ_SECONDS = 10
PAGE_BYTES = 32 * 1024
ENVELOPE_RESERVE = 4096
PATH = re.compile(r"[a-z0-9_-]{1,256}\Z")
ENTITY = re.compile(r"[a-z][a-z0-9_]*\.[a-z0-9_]+\Z")
CURSOR = re.compile(r"[0-9a-f]{32}:[0-9]{1,4}:[0-9a-f]{64}\Z")
REASONS = frozenset({"invalid_arguments", "invalid_cursor", "snapshot_expired",
    "capacity_busy", "authority_unavailable", "authority_drift", "access_denied",
    "source_unavailable", "malformed_response", "response_limit", "structural_limit",
    "timeout", "output_limit", "identity_mismatch", "hash_mismatch", "source_gate_pending",
    "source_rejected"})


class AnalysisError(GovernanceError):
    """A fixed reason, never provider text, argument values or exception details."""

    def __init__(self, reason):
        reason = reason if reason in REASONS else "source_unavailable"
        code = (ErrorCode.INVALID_REQUEST if reason == "invalid_arguments" else
                ErrorCode.INVALID_CURSOR if reason in {"invalid_cursor", "snapshot_expired"} else
                ErrorCode.PROVIDER_UNAVAILABLE if reason.startswith("authority_") else
                ErrorCode.ANALYSIS_UNAVAILABLE)
        super().__init__(code, details={"reason": reason})
        self.reason = reason

    @property
    def retryable(self):
        return self.reason in {"capacity_busy", "timeout", "source_unavailable"}


def validate_arguments(arguments):
    if (type(arguments) is not dict or set(arguments) - {"url_path", "limit", "cursor"}
            or type(arguments.get("url_path")) is not str
            or not PATH.fullmatch(arguments["url_path"])
            or type(arguments.get("limit", 25)) is not int
            or not 1 <= arguments.get("limit", 25) <= 100
            or type(arguments.get("cursor", "")) is not str
            or len(arguments.get("cursor", "")) > 2048
            or (arguments.get("cursor") and not CURSOR.fullmatch(arguments["cursor"]))):
        raise AnalysisError("invalid_arguments")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def parse(raw, *, maximum=INVENTORY_BYTES, depth=INVENTORY_DEPTH, nodes=INVENTORY_NODES):
    """Bound bytes and lexical nesting before JSON allocates a nested object.

    Byte-bound strings remain ephemeral. Duplicate keys and nonfinite numbers
    are rejected before semantic projection. Node accounting includes keys.
    """
    if type(raw) is not bytes or len(raw) > maximum:
        raise AnalysisError("response_limit")
    nesting, quoted, escaped = 0, False, False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            nesting += 1
            if nesting > depth:
                raise AnalysisError("structural_limit")
        elif byte in (93, 125):
            nesting -= 1
    count = 0

    def pairs(items):
        nonlocal count
        result = {}
        for key, value in items:
            if key in result:
                raise AnalysisError("malformed_response")
            count += 1
            if count > nodes:
                raise AnalysisError("structural_limit")
            result[key] = value
        return result

    def invalid(_):
        raise AnalysisError("malformed_response")

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
        stack = [value]
        while stack:
            item = stack.pop()
            count += 1
            if count > nodes:
                raise AnalysisError("structural_limit")
            if type(item) in (dict, list):
                if len(item) + len(stack) + count > nodes:
                    raise AnalysisError("structural_limit")
                stack.extend(item.values() if type(item) is dict else item)
            elif type(item) is float:
                import math
                if not math.isfinite(item):
                    raise AnalysisError("malformed_response")
        return value
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise AnalysisError("malformed_response") from None


async def worker(function, *args, **kwargs):
    """Drain finite detached work before releasing its capacity on cancellation."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()
        raise
