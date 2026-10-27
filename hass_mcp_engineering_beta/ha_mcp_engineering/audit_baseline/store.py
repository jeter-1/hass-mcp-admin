"""Durable, bounded, Engineering-local automation baseline store (HAMCP-228).

It follows the HAMCP-225 trace snapshot store and the
``governance/reverification_storage.py`` precedent: a nonblocking ``flock``,
``O_NOFOLLOW`` reads of regular files only, mode 0600 files in 0700
directories, temporary file plus ``fsync`` plus ``rename``, and fixed
diagnostics with no payload reflection. Only the validated baseline report
(identities, digests, statuses, outcomes; never a configuration body) is
stored, in a self-binding record envelope (its capture id, caller, creation
time and resume origin plus the report digest). Every load checks the file
digest, the binding, the ``artifact_sha256`` and the baseline validator; a
damaged record is deleted and the index keeps a bounded, content-free
quarantine stub. At capacity the store refuses; it never evicts.

A missing or unreadable index never deletes a committed record (HAMCP-226/228
review 1, H2): the index is rebuilt from the record files, each verified from
its own envelope; a file that fails is quarantined as a stub, and the rebuild
is reported in the store status. Hashes detect accidental damage, not an
attacker able to rewrite both a record and the index.
"""

from __future__ import annotations

from contextlib import contextmanager
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import uuid

from . import capture_contracts as c
from .validation import BaselineValidationError, _new_baseline


ENTRY_KEYS = frozenset({"capture_id", "baseline_id", "caller", "created_at_ms", "bytes", "sha256",
                        "artifact_sha256", "resumed_from"})
QUARANTINE_KEYS = frozenset({"capture_id", "quarantined_at_ms", "reason", "observed_bytes", "observed_sha256"})
DAMAGE_REASONS = frozenset({"missing", "unreadable", "digest_mismatch", "invalid_record"})
RECOVERY_KEYS = frozenset({"index_rebuilds", "last_index_rebuild"})
REBUILD_KEYS = frozenset({"at_ms", "reason", "recovered", "quarantined"})
REBUILD_REASONS = frozenset({"index_missing", "index_unreadable"})
BINDING_KEYS = ("capture_id", "baseline_id", "caller", "created_at_ms", "artifact_sha256", "resumed_from")
RECORD_KEYS = frozenset({"model", "binding", "report_sha256", "report"})
# The envelope adds the binding and the report digest to the report bound.
RECORD_BYTES = c.SNAPSHOT_BYTES + 4096
_SHA = frozenset("0123456789abcdef")


class StoreError(RuntimeError):
    """Fixed diagnostic only; no storage payload or exception reflection."""

    def __init__(self, reason, oldest_age_seconds=None):
        super().__init__(reason)
        self.reason = reason
        self.oldest_age_seconds = oldest_age_seconds


def empty_index():
    return {"model": c.STORE_MODEL, "baselines": [], "quarantine": {"entries": [], "dropped": 0},
            "recovery": {"index_rebuilds": 0, "last_index_rebuild": None}}


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _count(value):
    return type(value) is int and 0 <= value <= 10**15


def _hex64(value):
    return type(value) is str and len(value) == 64 and set(value) <= _SHA


def _capture_id(value):
    return type(value) is str and c.CAPTURE_ID.fullmatch(value) is not None


def _caller(value):
    return type(value) is str and 0 < len(value) <= 256 and value.isprintable() and value != "anonymous"


def _valid_entry(entry):
    return (type(entry) is dict and set(entry) == ENTRY_KEYS and _capture_id(entry["capture_id"])
            and type(entry["baseline_id"]) is str and c.ID.fullmatch(entry["baseline_id"]) is not None
            and _caller(entry["caller"]) and _count(entry["created_at_ms"])
            and type(entry["bytes"]) is int and 0 < entry["bytes"] <= RECORD_BYTES
            and _hex64(entry["sha256"])
            and type(entry["artifact_sha256"]) is str and entry["artifact_sha256"].startswith("sha256:")
            and _hex64(entry["artifact_sha256"][7:])
            and (entry["resumed_from"] is None or _capture_id(entry["resumed_from"])))


def _valid_stub(stub):
    return (type(stub) is dict and set(stub) == QUARANTINE_KEYS and _capture_id(stub["capture_id"])
            and _count(stub["quarantined_at_ms"]) and stub["reason"] in DAMAGE_REASONS
            and (stub["observed_bytes"] is None or _count(stub["observed_bytes"]))
            and (stub["observed_sha256"] is None or _hex64(stub["observed_sha256"])))


def _valid_recovery(value):
    last = value.get("last_index_rebuild") if type(value) is dict else None
    return (type(value) is dict and set(value) == RECOVERY_KEYS and _count(value["index_rebuilds"])
            and (last is None or (type(last) is dict and set(last) == REBUILD_KEYS and _count(last["at_ms"])
                                  and last["reason"] in REBUILD_REASONS and _count(last["recovered"])
                                  and _count(last["quarantined"]))))


def validate_index(value):
    if (type(value) is not dict or set(value) != {"model", "baselines", "quarantine", "recovery"}
            or value["model"] != c.STORE_MODEL or type(value["baselines"]) is not list
            or len(value["baselines"]) > c.STORE_BASELINES
            or not all(_valid_entry(entry) for entry in value["baselines"])
            or type(value["quarantine"]) is not dict or set(value["quarantine"]) != {"entries", "dropped"}
            or type(value["quarantine"]["entries"]) is not list
            or len(value["quarantine"]["entries"]) > c.QUARANTINE_MAX
            or not all(_valid_stub(stub) for stub in value["quarantine"]["entries"])
            or not _count(value["quarantine"]["dropped"]) or not _valid_recovery(value["recovery"])):
        raise StoreError("store_invalid")
    ids = [entry["capture_id"] for entry in value["baselines"]]
    if len(set(ids)) != len(ids):
        raise StoreError("store_invalid")
    return value


def encode_record(entry, report_bytes):
    """The self-binding record envelope for a canonical report."""

    return c.canonical({"model": c.STORE_MODEL, "binding": {key: entry[key] for key in BINDING_KEYS},
                        "report_sha256": sha256(report_bytes), "report": json.loads(report_bytes)})


def verify_record(raw, capture_id):
    """``(report, entry)`` from a record verified by its own envelope, or ``None``.

    The bytes must be the exact canonical envelope; the binding must name this
    capture id; the report must match its digest, its ``artifact_sha256`` and
    the baseline validator.
    """

    try:
        record = json.loads(raw)
        if type(record) is not dict or set(record) != RECORD_KEYS or record["model"] != c.STORE_MODEL:
            return None
        binding = record["binding"]
        if type(binding) is not dict or set(binding) != set(BINDING_KEYS) or binding["capture_id"] != capture_id:
            return None
        entry = dict(binding, bytes=len(raw), sha256=sha256(raw))
        if not _valid_entry(entry) or c.canonical(record) != raw:
            return None
        report_bytes = c.canonical(record["report"])
        if record["report_sha256"] != sha256(report_bytes):
            return None
        report = validate_report(report_bytes, entry)
    except (ValueError, TypeError, RecursionError, OverflowError):
        return None
    return None if report is None else (report, entry)


def validate_report(raw, entry):
    """The stored report, or ``None`` when it is not a valid baseline report."""

    try:
        report = json.loads(raw)
        if (type(report) is not dict or set(report) != {"baseline", "diagnostics"}
                or type(report["diagnostics"]) is not dict
                or type(report["diagnostics"].get("record_outcomes")) is not dict):
            return None
        baseline = report["baseline"]
        if (c.digest(baseline) != entry["artifact_sha256"]
                or type(baseline) is not dict or baseline.get("baseline_id") != entry["baseline_id"]):
            return None
        _new_baseline(baseline)
    except (ValueError, TypeError, RecursionError, OverflowError, BaselineValidationError):
        return None
    return report


class BaselineStore:
    """One namespace under the governance disk boundary; one writer at a time."""

    def __init__(self, root):
        self.root = Path(root) / c.STORE_MODEL
        self.records = self.root / "records"
        self.index_path = self.root / "index.json"
        self.fault_hook = None
        self.commit_unknown = False
        self.status = {"index_rebuilds": 0, "last_index_rebuild": None}

    def _inject(self, stage):
        if self.fault_hook is not None:
            self.fault_hook(stage)

    def _check_namespace(self):
        for path in (self.root, self.records):
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
            fd = os.open(self.root / ".transaction.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
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

    def load_index(self, now_ms=None):
        """The index; a missing or unreadable one is rebuilt from the records, never emptied."""

        try:
            raw = self._read_regular(self.index_path, c.STORE_INDEX_BYTES)
            index = None if raw is None else validate_index(json.loads(raw))
            reason = "index_missing"
        except (StoreError, ValueError, TypeError, RecursionError, OverflowError):
            index, reason = None, "index_unreadable"
        except OSError:
            raise StoreError("store_unavailable") from None
        if index is None:
            index = self._rebuild(reason, int(time.time() * 1000) if now_ms is None else now_ms)
        self.status = copy_status(index)
        return index

    def _record_names(self):
        try:
            return sorted(os.listdir(self.records))
        except FileNotFoundError:
            return []
        except OSError:
            raise StoreError("store_unavailable") from None

    def _rebuild(self, reason, now_ms):
        """Re-index every record file that verifies from its own envelope (review 1, H2).

        A file that fails verification is quarantined as a content-free stub,
        as on a damaged load. Only age expiry, through the ordinary sweep, may
        later remove a verified record.
        """

        index = empty_index()
        names = [name for name in self._record_names() if name.endswith(".json") and _capture_id(name[:-5])]
        if reason == "index_missing" and not names:
            # A store in first use: nothing to recover and nothing to report.
            return index
        horizon = now_ms - c.STORE_RETENTION_SECONDS * 1000
        recovered, damaged = [], []
        for name in names:
            capture_id = name[:-5]
            try:
                raw = self._read_regular(self.records / name, RECORD_BYTES)
            except StoreError:
                damage = {"reason": "unreadable", "observed_bytes": None, "observed_sha256": None}
            except OSError:
                raise StoreError("store_unavailable") from None
            else:
                if raw is None:
                    continue
                verified = verify_record(raw, capture_id)
                if verified is not None:
                    if verified[1]["created_at_ms"] > horizon:
                        recovered.append(verified[1])
                    continue
                damage = {"reason": "invalid_record", "observed_bytes": len(raw), "observed_sha256": sha256(raw)}
            damaged.append((capture_id, damage))
        if len(recovered) > c.STORE_BASELINES:
            # More verified records than the store admits: fail closed before
            # any file is touched.
            raise StoreError("store_invalid")
        index["baselines"] = sorted(recovered, key=lambda entry: (entry["created_at_ms"], entry["capture_id"]))
        stubs = index["quarantine"]["entries"]
        for capture_id, damage in damaged:
            stubs.append({"capture_id": capture_id, "quarantined_at_ms": now_ms, **damage})
        index["quarantine"]["dropped"] = max(0, len(stubs) - c.QUARANTINE_MAX)
        del stubs[:index["quarantine"]["dropped"]]
        index["recovery"] = {"index_rebuilds": 1, "last_index_rebuild": {
            "at_ms": now_ms, "reason": reason, "recovered": len(recovered), "quarantined": len(damaged)}}
        # The stubs are committed before a damaged file is removed; a removal
        # interrupted here is finished by the next sweep (an unindexed name).
        self._write_index(index)
        for capture_id, _ in damaged:
            self._remove(capture_id + ".json")
        return index

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

    def _write_index(self, index):
        raw = c.canonical(validate_index(index))
        if len(raw) > c.STORE_INDEX_BYTES:
            raise StoreError("store_invalid")
        self._replace(raw, self.index_path, "index")
        self.status = copy_status(index)

    def _remove(self, name):
        try:
            (self.records / name).unlink(missing_ok=True)
        except OSError:
            raise StoreError("store_unavailable") from None

    def _sweep(self, index, now_ms):
        """Age expiry, abandoned temporaries and unindexed records; ``True`` if the index changed."""

        horizon = now_ms - c.STORE_RETENTION_SECONDS * 1000
        kept = [entry for entry in index["baselines"] if entry["created_at_ms"] > horizon]
        changed = len(kept) != len(index["baselines"])
        index["baselines"] = kept
        live = {entry["capture_id"] + ".json" for entry in kept}
        # Only the store's own names. With a valid index, an unindexed record
        # is an uncommitted put or an expired baseline.
        for name in self._record_names():
            if name.startswith(".pending-") or (name.endswith(".json") and _capture_id(name[:-5])
                                                and name not in live):
                self._remove(name)
        return changed

    def _quarantine(self, index, entry, damage, now_ms):
        self._remove(entry["capture_id"] + ".json")
        index["baselines"] = [item for item in index["baselines"] if item["capture_id"] != entry["capture_id"]]
        stubs = index["quarantine"]["entries"]
        stubs.append({"capture_id": entry["capture_id"], "quarantined_at_ms": now_ms, **damage})
        if len(stubs) > c.QUARANTINE_MAX:
            index["quarantine"]["dropped"] += len(stubs) - c.QUARANTINE_MAX
            del stubs[:len(stubs) - c.QUARANTINE_MAX]

    def _require_capacity(self, index, now_ms, changed):
        if len(index["baselines"]) >= c.STORE_BASELINES:
            if changed:
                self._write_index(index)
            oldest = min(entry["created_at_ms"] for entry in index["baselines"])
            raise StoreError("store_full", max(0, (now_ms - oldest) // 1000))

    def check_capacity(self, now_ms):
        """Sweep, then refuse at capacity before any Home Assistant read."""

        with self.transaction():
            index = self.load_index(now_ms)
            changed = self._sweep(index, now_ms)
            self._require_capacity(index, now_ms, changed)
            if changed:
                self._write_index(index)

    def put(self, *, capture_id, caller, encoded, artifact_sha256, baseline_id, resumed_from, now_ms):
        """Commit one report under ``capture_id``. Refuses at capacity."""

        if not _caller(caller) or len(encoded) > c.SNAPSHOT_BYTES:
            raise StoreError("store_invalid")
        with self.transaction():
            index = self.load_index(now_ms)
            changed = self._sweep(index, now_ms)
            self._require_capacity(index, now_ms, changed)
            if any(entry["capture_id"] == capture_id for entry in index["baselines"]):
                raise StoreError("store_invalid")
            entry = {"capture_id": capture_id, "baseline_id": baseline_id, "caller": caller,
                     "created_at_ms": now_ms, "bytes": len(encoded), "sha256": sha256(encoded),
                     "artifact_sha256": artifact_sha256, "resumed_from": resumed_from}
            try:
                record = encode_record(entry, encoded)
            except (ValueError, TypeError, RecursionError, OverflowError):
                raise StoreError("store_invalid") from None
            entry.update(bytes=len(record), sha256=sha256(record))
            if not _valid_entry(entry):
                raise StoreError("store_invalid")
            # Record first: an index write that fails leaves an unindexed
            # record, which the next sweep removes; nothing partial is listed.
            self._replace(record, self.records / (capture_id + ".json"), "record")
            index["baselines"].append(entry)
            self._write_index(index)
            return capture_id

    def get(self, capture_id, caller, now_ms):
        """``(report, entry)`` for the caller's own baseline, digest and schema verified."""

        if not _capture_id(capture_id):
            raise StoreError("baseline_not_found")
        with self.transaction():
            index = self.load_index(now_ms)
            changed = self._sweep(index, now_ms)
            entry = next((item for item in index["baselines"] if item["capture_id"] == capture_id), None)
            # Another caller's baseline is indistinguishable from a missing one.
            if entry is None or entry["caller"] != caller:
                if changed:
                    self._write_index(index)
                raise StoreError("baseline_not_found")
            damage, report = None, None
            try:
                raw = self._read_regular(self.records / (capture_id + ".json"), RECORD_BYTES)
            except StoreError:
                damage = {"reason": "unreadable", "observed_bytes": None, "observed_sha256": None}
            except OSError:
                raise StoreError("store_unavailable") from None
            else:
                if raw is None:
                    damage = {"reason": "missing", "observed_bytes": None, "observed_sha256": None}
                else:
                    observed = {"observed_bytes": len(raw), "observed_sha256": sha256(raw)}
                    if len(raw) != entry["bytes"] or observed["observed_sha256"] != entry["sha256"]:
                        damage = {"reason": "digest_mismatch", **observed}
                    else:
                        verified = verify_record(raw, capture_id)
                        # The record's own binding must be the indexed one.
                        if verified is None or verified[1] != entry:
                            damage = {"reason": "invalid_record", **observed}
                        else:
                            report = verified[0]
            if damage is not None:
                self._quarantine(index, entry, damage, now_ms)
                self._write_index(index)
                raise StoreError("baseline_damaged")
            if changed:
                self._write_index(index)
            return report, dict(entry)


def copy_status(index):
    """The content-free store status: index rebuilds only (review 1, H2)."""

    recovery = index["recovery"]
    last = recovery["last_index_rebuild"]
    return {"index_rebuilds": recovery["index_rebuilds"], "last_index_rebuild": None if last is None else dict(last)}
