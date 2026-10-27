"""Durable, bounded, Engineering-local trace snapshot store (HAMCP-225).

It follows the ``governance/reverification_storage.py`` precedent: a
nonblocking ``flock``, ``O_NOFOLLOW`` reads of regular files only, mode 0600,
temporary file plus ``fsync`` plus ``rename``, fixed diagnostics with no
payload reflection. A damaged record is deleted, never retained: the index
keeps a bounded, content-free quarantine stub for it (HAMCP-225 review H3).
Hashes detect accidental damage, not an attacker able to rewrite both a
record and the index.
"""

from __future__ import annotations

from contextlib import contextmanager
import errno
import fcntl
import json
import os
from pathlib import Path
import stat
import uuid

from ..identifiers import AUTOMATION_CONFIG_ID_PATTERN
from . import contracts as c


SNAPSHOT_KEYS = frozenset({
    "snapshot_id", "automation_id", "run_id", "origin", "watch_id", "captured_at_ms",
    "captured_by", "completeness", "bytes", "sha256", "state", "script_execution",
    "started_at",
})
WATCH_KEYS = frozenset({
    "watch_id", "automation_id", "mode", "created_at_ms", "expires_at_ms", "caller_id",
    "request_id", "seen_run_ids", "baseline_done", "skipped_polls", "uncaptured_runs",
    "captured_count", "evicted_count", "last_poll_at_ms",
})
_COUNTERS = ("skipped_polls", "uncaptured_runs", "captured_count", "evicted_count")
QUARANTINE_KEYS = frozenset({
    "snapshot_id", "automation_id", "run_id", "quarantined_at_ms", "reason",
    "observed_bytes", "observed_sha256",
})
DAMAGE_REASONS = frozenset({"missing", "unreadable", "digest_mismatch", "invalid_record"})


class StoreError(RuntimeError):
    """Fixed diagnostic only; no storage payload or exception reflection."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def empty_index():
    return {"model": c.MODEL, "snapshots": [], "watches": [],
            "quarantine": {"entries": [], "dropped": 0}}


def _text(value, limit, *, optional=False):
    if value is None and optional:
        return True
    return type(value) is str and 0 < len(value) <= limit


def _count(value):
    # Counters and millisecond epochs (2026 is about 1.8e12 ms).
    return type(value) is int and 0 <= value <= 10**15


def _match(pattern, value):
    return type(value) is str and pattern.fullmatch(value) is not None


def _valid_snapshot(entry):
    return (type(entry) is dict and set(entry) == SNAPSHOT_KEYS
            and type(entry["snapshot_id"]) is str and c.SNAPSHOT_ID.fullmatch(entry["snapshot_id"])
            and _match(AUTOMATION_CONFIG_ID_PATTERN, entry["automation_id"])
            and _match(c.RUN_ID, entry["run_id"])
            and entry["origin"] in {"manual", "trace_watch"}
            and ((entry["origin"] == "manual" and entry["watch_id"] is None)
                 or (entry["origin"] == "trace_watch" and type(entry["watch_id"]) is str
                     and c.WATCH_ID.fullmatch(entry["watch_id"])))
            and _count(entry["captured_at_ms"]) and _match(c.CALLER, entry["captured_by"])
            and entry["completeness"] in {"complete", "partial"}
            and type(entry["bytes"]) is int and 0 < entry["bytes"] <= c.SNAPSHOT_BYTES
            and type(entry["sha256"]) is str and len(entry["sha256"]) == 64
            and all(_text(entry[k], 160, optional=True)
                    for k in ("state", "script_execution", "started_at")))


def _valid_watch(watch):
    return (type(watch) is dict and set(watch) == WATCH_KEYS
            and type(watch["watch_id"]) is str and c.WATCH_ID.fullmatch(watch["watch_id"])
            and _match(AUTOMATION_CONFIG_ID_PATTERN, watch["automation_id"])
            and watch["mode"] in c.WATCH_MODES
            and _count(watch["created_at_ms"]) and _count(watch["expires_at_ms"])
            # Identity fields are pattern-bound: a refusal returns the watch
            # list in error details, outside the strict response exit.
            and _match(c.CALLER, watch["caller_id"]) and _text(watch["request_id"], 128)
            and type(watch["seen_run_ids"]) is list and len(watch["seen_run_ids"]) <= c.MAX_SEEN_RUNS
            and all(type(r) is str and c.RUN_ID.fullmatch(r) for r in watch["seen_run_ids"])
            and type(watch["baseline_done"]) is bool
            and all(_count(watch[k]) for k in _COUNTERS)
            and (watch["last_poll_at_ms"] is None or _count(watch["last_poll_at_ms"])))


def _valid_stub(stub):
    return (type(stub) is dict and set(stub) == QUARANTINE_KEYS
            and type(stub["snapshot_id"]) is str and c.SNAPSHOT_ID.fullmatch(stub["snapshot_id"])
            and _match(AUTOMATION_CONFIG_ID_PATTERN, stub["automation_id"])
            and _match(c.RUN_ID, stub["run_id"])
            and _count(stub["quarantined_at_ms"]) and stub["reason"] in DAMAGE_REASONS
            and (stub["observed_bytes"] is None or _count(stub["observed_bytes"]))
            and (stub["observed_sha256"] is None
                 or (type(stub["observed_sha256"]) is str and len(stub["observed_sha256"]) == 64)))


def _valid_quarantine(value):
    return (type(value) is dict and set(value) == {"entries", "dropped"}
            and type(value["entries"]) is list and len(value["entries"]) <= c.QUARANTINE_MAX
            and all(_valid_stub(stub) for stub in value["entries"]) and _count(value["dropped"]))


def validate_index(value):
    if (type(value) is not dict or set(value) != {"model", "snapshots", "watches", "quarantine"}
            or value["model"] != c.MODEL or type(value["snapshots"]) is not list
            or type(value["watches"]) is not list
            or len(value["snapshots"]) > c.MAX_SNAPSHOTS or len(value["watches"]) > c.MAX_WATCHES
            or not all(_valid_snapshot(e) for e in value["snapshots"])
            or not all(_valid_watch(w) for w in value["watches"])
            or not _valid_quarantine(value["quarantine"])):
        raise StoreError("store_invalid")
    ids = [e["snapshot_id"] for e in value["snapshots"]]
    runs = [(e["automation_id"], e["run_id"]) for e in value["snapshots"]]
    watched = [w["automation_id"] for w in value["watches"]]
    if (len(set(ids)) != len(ids) or len(set(runs)) != len(runs)
            or len(set(watched)) != len(watched)
            or sum(e["bytes"] for e in value["snapshots"]) > c.STORE_BYTES):
        raise StoreError("store_invalid")
    return value


class TraceSnapshotStore:
    """One namespace under the governance disk boundary; one writer at a time."""

    def __init__(self, root):
        self.root = Path(root) / c.MODEL
        self.records = self.root / "records"
        self.quarantine_root = self.root / "quarantine"
        self.index_path = self.root / "index.json"
        self.fault_hook = None
        self.commit_unknown = False

    def _inject(self, stage):
        if self.fault_hook is not None:
            self.fault_hook(stage)

    def _check_namespace(self):
        for path in (self.root, self.records, self.quarantine_root):
            if path.is_symlink():
                raise StoreError("store_invalid")

    @contextmanager
    def transaction(self):
        """Nonblocking exclusive ownership; a second owner is refused, not queued."""

        fd = None
        try:
            if self.commit_unknown:
                raise StoreError("store_unavailable")
            if self.root.parent.is_symlink():
                raise StoreError("store_invalid")
            self._check_namespace()
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.records.mkdir(exist_ok=True, mode=0o700)
            self._check_namespace()
            fd = os.open(self.root / ".transaction.lock",
                         os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StoreError("store_busy") from None
        except StoreError:
            if fd is not None:
                os.close(fd)
            raise
        except (OSError, ValueError, RecursionError):
            if fd is not None:
                os.close(fd)
            raise StoreError("store_unavailable") from None
        try:
            yield self
        finally:
            os.close(fd)

    def _read_regular(self, path, limit):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise StoreError("store_invalid") from None
            raise
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise StoreError("store_invalid")
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise StoreError("store_invalid")
        return raw

    def load_index(self):
        try:
            raw = self._read_regular(self.index_path, c.INDEX_BYTES)
            if raw is None:
                return empty_index()
            return validate_index(json.loads(raw))
        except StoreError:
            raise
        except (ValueError, TypeError, RecursionError, OverflowError):
            raise StoreError("store_invalid") from None
        except OSError:
            raise StoreError("store_unavailable") from None

    def _replace(self, raw, target, stage):
        temporary = target.parent / (".pending-" + uuid.uuid4().hex)
        replaced = False
        try:
            self._inject("before_" + stage)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            replaced = True
            self._inject("after_" + stage)
            directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except (OSError, ValueError, RecursionError):
            if replaced and stage == "index":
                self.commit_unknown = True
            raise StoreError("store_unavailable") from None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def write_index(self, index):
        raw = c.canonical(validate_index(index))
        if len(raw) > c.INDEX_BYTES:
            raise StoreError("store_invalid")
        self._replace(raw, self.index_path, "index")

    def write_record(self, snapshot_id, raw):
        if not c.SNAPSHOT_ID.fullmatch(snapshot_id) or len(raw) > c.SNAPSHOT_BYTES:
            raise StoreError("store_invalid")
        self._replace(raw, self.records / (snapshot_id + ".json"), "record")

    def read_record(self, entry):
        """``(record, None)``, or ``(None, damage)`` for a damaged record.

        ``damage`` holds only the fixed reason and the observed length and
        digest; the damaged bytes are never parsed into a response or kept.
        """

        path = self.records / (entry["snapshot_id"] + ".json")
        try:
            raw = self._read_regular(path, c.SNAPSHOT_BYTES)
        except StoreError:
            return None, {"reason": "unreadable", "observed_bytes": None, "observed_sha256": None}
        except OSError:
            raise StoreError("store_unavailable") from None
        if raw is None:
            return None, {"reason": "missing", "observed_bytes": None, "observed_sha256": None}
        observed = {"observed_bytes": len(raw), "observed_sha256": c.sha256(raw)}
        if len(raw) != entry["bytes"] or observed["observed_sha256"] != entry["sha256"]:
            return None, {"reason": "digest_mismatch", **observed}
        try:
            record = json.loads(raw)
        except (ValueError, RecursionError):
            return None, {"reason": "invalid_record", **observed}
        if (type(record) is not dict or record.get("model") != c.RECORD_MODEL
                or record.get("snapshot_id") != entry["snapshot_id"]):
            return None, {"reason": "invalid_record", **observed}
        return record, None

    def remove_record(self, snapshot_id):
        try:
            (self.records / (snapshot_id + ".json")).unlink(missing_ok=True)
        except OSError:
            raise StoreError("store_unavailable") from None

    def record_files(self):
        """Committed record names and abandoned temporaries, for the sweep."""

        try:
            names = os.listdir(self.records)
        except FileNotFoundError:
            return set(), []
        except OSError:
            raise StoreError("store_unavailable") from None
        committed = {name[:-5] for name in names
                     if name.endswith(".json") and c.SNAPSHOT_ID.fullmatch(name[:-5])}
        pending = [name for name in names if name.startswith(".pending-")]
        return committed, pending

    def remove_pending(self, name):
        try:
            (self.records / name).unlink(missing_ok=True)
        except OSError:
            raise StoreError("store_unavailable") from None
