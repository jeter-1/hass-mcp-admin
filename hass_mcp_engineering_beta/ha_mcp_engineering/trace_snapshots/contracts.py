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

# Decision B (Josh, 2026-10-26): the design note section 2.4 bounds, with
# the overall caps raised by HAMCP-296 (below).
SNAPSHOT_BYTES = 262_144
# HAMCP-296 overall caps (orchestrator default, pending Codex/Josh): above the
# 400-snapshot, 100 MiB watch reservation, so manual captures keep at least
# 500 - 400 = 100 snapshots and 128 - 100 = 28 MiB (>= 100 full-size
# 256 KiB snapshots). Before HAMCP-296 they were 200 and 32 MiB.
MAX_SNAPSHOTS = 500
STORE_BYTES = 128 * 1024 * 1024
# The shipped (2.4.x/2.5.0) v1 index reader marks the whole store
# ``store_invalid`` when ``index.json`` names more than 200 snapshots or more
# than 32 MiB of summed entry bytes (shipped ``store.validate_index``; the
# HAMCP-296 beta.6 fixture provenance records both verdicts). ``index.json``
# therefore never exceeds the shipped bounds: manual captures first, then
# the newest watch captures, stay in it and the rest live in the
# watch sidecar, which shipped code ignores.
V1_INDEX_MAX_SNAPSHOTS = 200
V1_INDEX_MAX_BYTES = 32 * 1024 * 1024
# ``index.json`` size bound (unchanged; the shipped reader uses the same).
# A worst-case snapshot entry is 6,575 canonical bytes (256-character
# automation ID, 128-character run ID, three 160-character text fields of
# astral characters at 12 escaped bytes each), so the 200 v1 entries take
# about 1.32 MB; with 10 worst-case watch rows (15,346 bytes each) and 20
# quarantine stubs (648 bytes each) the index stays under 1.49 MB of the
# 2,097,152 (test ``WorstCaseSizeTests``). A single 500-entry index could
# need about 3.29 MB, one more reason the entries past the v1 bounds live in
# the sidecar.
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

# Decision W1 (30 s poll, 168 h term) and HAMCP-296 Q1 (Josh, 2026-11-07:
# raise the cap with a stated bound and add priority). The bound is 20
# watches: at a 30 s poll that is at most 40 ``trace/list`` reads a minute
# (20 at the old cap of 10) and each watch's share of a tick's 16 s read time
# falls from 1.6 s to 0.8 s.
MAX_WATCHES = 20
# The shipped (2.4.x/2.5.0) v1 index reader rejects more than 10 watches and
# would mark the whole store ``store_invalid``. ``index.json`` therefore
# never holds more than 10: the rest live in the watch sidecar, which
# shipped code ignores, so a downgrade keeps the 10 highest-priority watches.
V1_INDEX_MAX_WATCHES = 10
# Decision E2 (Josh, 2026-10-26), kept by Josh on 2026-11-07: each watch
# holds at most 20 snapshots and evicts only its own oldest past that.
WATCH_QUOTA = 20
# The watch-origin reservation (Josh, 2026-11-07: grow it to 20 x 20 = 400
# snapshots, 400 x 256 KiB = 100 MiB): watch captures together (active,
# ended or lapsed watches) never exceed it; a watch capture past it is
# ``store_full`` and nothing is evicted for it (refuse, not evict).
WATCH_STORE_SNAPSHOTS = MAX_WATCHES * WATCH_QUOTA
WATCH_STORE_BYTES = WATCH_STORE_SNAPSHOTS * SNAPSHOT_BYTES
WATCH_MAX_HOURS = 168
POLL_SECONDS = 30.0
TICK_SECONDS = 20.0
MAX_SEEN_RUNS = 100
# Share of a tick spent on Core reads; the rest is the local commit reserve.
READ_SHARE = 0.8
WATCH_MODES = ("failures", "executed")
WATCH_ACTIONS = ("add", "remove", "list", "renew")
WATCH_PRIORITIES = ("high", "normal")

# HAMCP-296 lifecycle (implementation defaults pending Josh, Q2): a watch
# warns when its time left is at most min(24 h, half its current term);
# at most 20 lapsed records are kept, each for at most 7 days.
#
# HAMCP-296 watch sidecar: everything past the v1 index bounds (watches
# 11-20, priority and renewal rows, lapsed records and the snapshot entries
# index.json does not keep) is ONE state, written to one of two slot files
# (``watch-sidecar-a.json`` / ``-b.json``) and bound to the exact index.json
# bytes it was written with (sha256) and a generation. A load uses only the
# slot bound to the index on disk, so a sidecar and an index from different
# transactions are never combined (Codex review F2). Worst case: 500 entries
# of 6,575 bytes plus 20 watch rows of 15,346 bytes, 20 metadata rows and 20
# lapsed records is about 3.61 MB of the 4,194,304 (``WorstCaseSizeTests``).
SIDECAR_MODEL = "trace-watch-sidecar-v1"
SIDECAR_BYTES = 4 * 1024 * 1024
EXPIRY_WARNING_HOURS = 24
LAPSED_MAX = 20
LAPSED_RETENTION_SECONDS = 7 * 24 * 3600
LAPSE_REASONS = ("expired", "identifier_unavailable", "displaced", "store_recovery")
HEALTH_LIST = 10
RENEW_OUTCOMES = ("renewed", "unchanged", "reinstated", "watch_limit", "identifier_unavailable")

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

    def __init__(self, reason, *, watches=None, lifecycle=None):
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
        if lifecycle is not None:
            details["lifecycle"] = lifecycle
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
    _closed(arguments, {"action", "automation_id", "mode", "duration_hours", "priority",
                        "displace", "include_lapsed"})
    action = arguments.get("action")
    if type(action) is not str or action not in WATCH_ACTIONS:
        raise TraceSnapshotError("invalid_arguments")
    automation_id = arguments.get("automation_id", "")
    mode = arguments.get("mode", "failures")
    hours = arguments.get("duration_hours", 24)
    priority = arguments.get("priority")
    if type(mode) is not str or mode not in WATCH_MODES:
        raise TraceSnapshotError("invalid_arguments")
    if type(hours) is not int or not 1 <= hours <= WATCH_MAX_HOURS:
        raise TraceSnapshotError("invalid_arguments")
    if priority is not None and (type(priority) is not str or priority not in WATCH_PRIORITIES):
        raise TraceSnapshotError("invalid_arguments")
    _strict_bool(arguments.get("displace", False))
    include_lapsed = _strict_bool(arguments.get("include_lapsed", False))
    present = set(arguments) - {"action"}
    if action == "list":
        if automation_id != "" or present:
            raise TraceSnapshotError("invalid_arguments")
        return
    if action == "renew":
        # ``""`` renews every active watch; include_lapsed only then (Q3 default).
        if present - {"automation_id", "duration_hours", "include_lapsed"}:
            raise TraceSnapshotError("invalid_arguments")
        if type(automation_id) is not str:
            raise TraceSnapshotError("invalid_arguments")
        if automation_id:
            _automation_id(automation_id)
            if include_lapsed:
                raise TraceSnapshotError("invalid_arguments")
        return
    _automation_id(automation_id)
    if action == "remove" and present - {"automation_id"}:
        raise TraceSnapshotError("invalid_arguments")
    if action == "add" and "include_lapsed" in present:
        raise TraceSnapshotError("invalid_arguments")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def utc_text(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")
