"""Durable, bounded, Engineering-local automation baseline store (HAMCP-228).

It follows the HAMCP-225 trace snapshot store and the
``governance/reverification_storage.py`` precedent: a nonblocking ``flock``,
``O_NOFOLLOW`` reads of regular files only, mode 0600 files in 0700
directories, temporary file plus ``fsync`` plus ``rename``, and fixed
diagnostics with no payload reflection. Only the validated baseline report
(identities, digests, statuses, outcomes; never a configuration body) is
stored. Every load checks the file digest, the ``artifact_sha256`` and the
baseline validator; a damaged record is deleted and the index keeps a bounded,
content-free quarantine stub. At capacity the store refuses; it never evicts.
Hashes detect accidental damage, not an attacker able to rewrite both a
record and the index.
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
import uuid

from . import capture_contracts as c
from .validation import BaselineValidationError, _new_baseline


ENTRY_KEYS = frozenset({"capture_id", "baseline_id", "caller", "created_at_ms", "bytes", "sha256",
                        "artifact_sha256", "resumed_from"})
QUARANTINE_KEYS = frozenset({"capture_id", "quarantined_at_ms", "reason", "observed_bytes", "observed_sha256"})
DAMAGE_REASONS = frozenset({"missing", "unreadable", "digest_mismatch", "invalid_record"})
_SHA = frozenset("0123456789abcdef")


class StoreError(RuntimeError):
    """Fixed diagnostic only; no storage payload or exception reflection."""

    def __init__(self, reason, oldest_age_seconds=None):
        super().__init__(reason)
        self.reason = reason
        self.oldest_age_seconds = oldest_age_seconds


def empty_index():
    return {"model": c.STORE_MODEL, "baselines": [], "quarantine": {"entries": [], "dropped": 0}}


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
            and type(entry["bytes"]) is int and 0 < entry["bytes"] <= c.SNAPSHOT_BYTES
            and _hex64(entry["sha256"])
            and type(entry["artifact_sha256"]) is str and entry["artifact_sha256"].startswith("sha256:")
            and _hex64(entry["artifact_sha256"][7:])
            and (entry["resumed_from"] is None or _capture_id(entry["resumed_from"])))


def _valid_stub(stub):
    return (type(stub) is dict and set(stub) == QUARANTINE_KEYS and _capture_id(stub["capture_id"])
            and _count(stub["quarantined_at_ms"]) and stub["reason"] in DAMAGE_REASONS
            and (stub["observed_bytes"] is None or _count(stub["observed_bytes"]))
            and (stub["observed_sha256"] is None or _hex64(stub["observed_sha256"])))


def validate_index(value):
    if (type(value) is not dict or set(value) != {"model", "baselines", "quarantine"}
            or value["model"] != c.STORE_MODEL or type(value["baselines"]) is not list
            or len(value["baselines"]) > c.STORE_BASELINES
            or not all(_valid_entry(entry) for entry in value["baselines"])
            or type(value["quarantine"]) is not dict or set(value["quarantine"]) != {"entries", "dropped"}
            or type(value["quarantine"]["entries"]) is not list
            or len(value["quarantine"]["entries"]) > c.QUARANTINE_MAX
            or not all(_valid_stub(stub) for stub in value["quarantine"]["entries"])
            or not _count(value["quarantine"]["dropped"])):
        raise StoreError("store_invalid")
    ids = [entry["capture_id"] for entry in value["baselines"]]
    if len(set(ids)) != len(ids):
        raise StoreError("store_invalid")
    return value


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

    def load_index(self):
        try:
            raw = self._read_regular(self.index_path, c.STORE_INDEX_BYTES)
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

    def _write_index(self, index):
        raw = c.canonical(validate_index(index))
        if len(raw) > c.STORE_INDEX_BYTES:
            raise StoreError("store_invalid")
        self._replace(raw, self.index_path, "index")

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
        try:
            names = os.listdir(self.records)
        except FileNotFoundError:
            names = []
        except OSError:
            raise StoreError("store_unavailable") from None
        live = {entry["capture_id"] + ".json" for entry in kept}
        for name in names:
            if name.startswith(".pending-") or (name.endswith(".json") and name not in live):
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
            index = self.load_index()
            changed = self._sweep(index, now_ms)
            self._require_capacity(index, now_ms, changed)
            if changed:
                self._write_index(index)

    def put(self, *, capture_id, caller, encoded, artifact_sha256, baseline_id, resumed_from, now_ms):
        """Commit one report under ``capture_id``. Refuses at capacity."""

        if not _caller(caller) or len(encoded) > c.SNAPSHOT_BYTES:
            raise StoreError("store_invalid")
        with self.transaction():
            index = self.load_index()
            changed = self._sweep(index, now_ms)
            self._require_capacity(index, now_ms, changed)
            if any(entry["capture_id"] == capture_id for entry in index["baselines"]):
                raise StoreError("store_invalid")
            entry = {"capture_id": capture_id, "baseline_id": baseline_id, "caller": caller,
                     "created_at_ms": now_ms, "bytes": len(encoded), "sha256": sha256(encoded),
                     "artifact_sha256": artifact_sha256, "resumed_from": resumed_from}
            if not _valid_entry(entry):
                raise StoreError("store_invalid")
            # Record first: an index write that fails leaves an unindexed
            # record, which the next sweep removes; nothing partial is listed.
            self._replace(encoded, self.records / (capture_id + ".json"), "record")
            index["baselines"].append(entry)
            self._write_index(index)
            return capture_id

    def get(self, capture_id, caller, now_ms):
        """``(report, entry)`` for the caller's own baseline, digest and schema verified."""

        if not _capture_id(capture_id):
            raise StoreError("baseline_not_found")
        with self.transaction():
            index = self.load_index()
            changed = self._sweep(index, now_ms)
            entry = next((item for item in index["baselines"] if item["capture_id"] == capture_id), None)
            # Another caller's baseline is indistinguishable from a missing one.
            if entry is None or entry["caller"] != caller:
                if changed:
                    self._write_index(index)
                raise StoreError("baseline_not_found")
            damage, report = None, None
            try:
                raw = self._read_regular(self.records / (capture_id + ".json"), c.SNAPSHOT_BYTES)
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
                        report = validate_report(raw, entry)
                        if report is None:
                            damage = {"reason": "invalid_record", **observed}
            if damage is not None:
                self._quarantine(index, entry, damage, now_ms)
                self._write_index(index)
                raise StoreError("baseline_damaged")
            if changed:
                self._write_index(index)
            return report, dict(entry)
