"""Closed contract for HAMCP-225 automation trace snapshots and watches."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re

from ..errors import ErrorCode, GovernanceError
from ..identifiers import AUTOMATION_CONFIG_ID_PATTERN


MODEL = "trace-snapshots-v1"
RECORD_MODEL = "automation-trace-snapshot-v1"
CAPABILITY = "core.automation_trace_read"
REQUIREMENTS = (CAPABILITY,)

# Decision B (Josh, 2026-10-26): the design note section 2.4 bounds.
MAX_SNAPSHOTS = 200
SNAPSHOT_BYTES = 262_144
STORE_BYTES = 32 * 1024 * 1024
INDEX_BYTES = 2 * 1024 * 1024
RETENTION_SECONDS = 90 * 24 * 3600
MAX_CAPTURES = 5
NOTE_CHARS = 200
LIST_LIMIT = 50
PAGE_BYTES = 40_000
# A damaged record is deleted; the index keeps at most this many content-free
# quarantine stubs (identity, observed length and digest), aged on the same
# retention rule. The oldest stub goes first and is counted as dropped.
QUARANTINE_MAX = 20

# Decisions W1 and E2: at most 10 watches, a 30 s poll, 20 snapshots a watch.
MAX_WATCHES = 10
WATCH_QUOTA = 20
WATCH_MAX_HOURS = 168
POLL_SECONDS = 30.0
TICK_SECONDS = 20.0
MAX_SEEN_RUNS = 100
# Share of a tick spent on Core reads; the rest is the local commit reserve.
READ_SHARE = 0.8
WATCH_MODES = ("failures", "executed")
WATCH_ACTIONS = ("add", "remove", "list")

# Core 2026.10 script/automation stop reasons a failure watch captures.
FAILURE_EXECUTIONS = frozenset({
    "error", "aborted", "cancelled", "failed_single", "failed_max_runs",
    "disallowed_recursion_detected",
})
OUTCOMES = ("captured", "already_captured", "trace_not_retained", "run_in_progress",
            "over_size_bound", "store_full", "read_failed")

RUN_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
# Caller attribution (a 12-hex caller hash, "anonymous" or "trace_watch").
CALLER = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")
SNAPSHOT_ID = re.compile(r"[0-9a-f]{32}\Z")
# An identifier the shared credential screen withholds is stored, audited and
# returned only as this keyed digest (review H2); the form is reserved, so a
# caller can never supply one.
WITHHELD_ID = re.compile(r"withheld_[0-9a-f]{32}\Z")
WATCH_ID = re.compile(r"[0-9a-f]{32}\Z")
# get: <snapshot id>:<page>:<mac>; list: <captured ms>:<snapshot id>:<mac>.
PAGE_CURSOR = re.compile(r"[0-9a-f]{32}:[0-9]{1,4}:[0-9a-f]{64}\Z")
LIST_CURSOR = re.compile(r"[0-9]{1,16}:[0-9a-f]{32}:[0-9a-f]{64}\Z")

REASONS = frozenset({
    "invalid_arguments", "invalid_cursor", "snapshot_not_found", "watch_not_found",
    "watch_limit", "authority_unavailable", "authority_drift", "source_unavailable",
    "store_busy", "store_unavailable", "store_invalid", "audit_unavailable",
})


class TraceSnapshotError(GovernanceError):
    """Fixed reason only; never a stored payload or an exception's text."""

    def __init__(self, reason, *, watches=None):
        if reason not in REASONS:
            reason = "store_unavailable"
        code = (ErrorCode.INVALID_REQUEST if reason == "invalid_arguments" else
                ErrorCode.INVALID_CURSOR if reason == "invalid_cursor" else
                ErrorCode.RESOURCE_NOT_FOUND if reason in {"snapshot_not_found", "watch_not_found"} else
                ErrorCode.PROVIDER_UNAVAILABLE if reason in {"authority_unavailable", "authority_drift"} else
                ErrorCode.ANALYSIS_UNAVAILABLE)
        details = {"reason": reason}
        if watches is not None:
            details["watches"] = watches
        super().__init__(code, details=details)
        self.reason = reason

    @property
    def retryable(self) -> bool:
        return self.reason in {"store_busy", "source_unavailable", "authority_unavailable"}


def _automation_id(value):
    if (type(value) is not str or not AUTOMATION_CONFIG_ID_PATTERN.fullmatch(value)
            or WITHHELD_ID.fullmatch(value)):
        raise TraceSnapshotError("invalid_arguments")
    return value


def _strict_bool(value):
    if type(value) is not bool:
        raise TraceSnapshotError("invalid_arguments")
    return value


def _closed(arguments, allowed):
    if type(arguments) is not dict or set(arguments) - set(allowed):
        raise TraceSnapshotError("invalid_arguments")


def validate_capture(arguments):
    _closed(arguments, {"automation_id", "run_id", "include_condition_failures", "note"})
    _automation_id(arguments.get("automation_id"))
    run_id = arguments.get("run_id", "")
    if type(run_id) is not str or (run_id and (not RUN_ID.fullmatch(run_id)
                                               or WITHHELD_ID.fullmatch(run_id))):
        raise TraceSnapshotError("invalid_arguments")
    _strict_bool(arguments.get("include_condition_failures", False))
    note = arguments.get("note", "")
    if type(note) is not str or len(note) > NOTE_CHARS:
        raise TraceSnapshotError("invalid_arguments")


def validate_list(arguments):
    _closed(arguments, {"automation_id", "limit", "cursor"})
    automation_id = arguments.get("automation_id", "")
    if type(automation_id) is not str:
        raise TraceSnapshotError("invalid_arguments")
    if automation_id:
        _automation_id(automation_id)
    limit = arguments.get("limit", 20)
    if type(limit) is not int or not 1 <= limit <= LIST_LIMIT:
        raise TraceSnapshotError("invalid_arguments")
    cursor = arguments.get("cursor", "")
    if type(cursor) is not str or (cursor and not LIST_CURSOR.fullmatch(cursor)):
        raise TraceSnapshotError("invalid_arguments")


def validate_get(arguments):
    _closed(arguments, {"snapshot_id", "cursor"})
    snapshot_id = arguments.get("snapshot_id")
    if type(snapshot_id) is not str or not SNAPSHOT_ID.fullmatch(snapshot_id):
        raise TraceSnapshotError("invalid_arguments")
    cursor = arguments.get("cursor", "")
    if type(cursor) is not str or (cursor and not PAGE_CURSOR.fullmatch(cursor)):
        raise TraceSnapshotError("invalid_arguments")


def validate_watch(arguments):
    _closed(arguments, {"action", "automation_id", "mode", "duration_hours"})
    action = arguments.get("action")
    if action not in WATCH_ACTIONS:
        raise TraceSnapshotError("invalid_arguments")
    automation_id = arguments.get("automation_id", "")
    mode = arguments.get("mode", "failures")
    hours = arguments.get("duration_hours", 24)
    if type(mode) is not str or mode not in WATCH_MODES:
        raise TraceSnapshotError("invalid_arguments")
    if type(hours) is not int or not 1 <= hours <= WATCH_MAX_HOURS:
        raise TraceSnapshotError("invalid_arguments")
    if action == "list":
        if automation_id != "" or set(arguments) - {"action"}:
            raise TraceSnapshotError("invalid_arguments")
        return
    _automation_id(automation_id)
    if action == "remove" and set(arguments) - {"action", "automation_id"}:
        raise TraceSnapshotError("invalid_arguments")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def utc_text(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")
