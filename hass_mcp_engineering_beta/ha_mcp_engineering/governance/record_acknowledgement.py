"""HAMCP-253: append-only administrator acknowledgements of stuck records.

An acknowledgement is supplementary evidence about one exact governance
record state. It never rewrites, deletes or re-validates the plan or task it
names, and nothing that approves, applies or recovers a record reads it. The
ledger only decides whether health warns about a record that is already stuck.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import unicodedata
import uuid


MODEL = "hamcp253-record-acknowledgement-v1"
MAX_EVENTS = 2048
MAX_STORE_BYTES = 4 * 1024 * 1024
MAX_REASON_CHARS = 500
# Events are stored ASCII-escaped: one astral character becomes a 12-byte
# surrogate-pair escape. A valid reason of any script must always fit, so
# the bound covers 12 bytes per reason character plus the fixed fields.
MAX_EVENT_BYTES = 8192
RECORD_KINDS = ("change_plan", "execution_task")
CLASSIFICATIONS = ("current_defect", "historical_record")
ACTIONS = ("acknowledge", "revoke")
ADMIN_PRINCIPAL = re.compile(r"^home_assistant_admin_ingress:[A-Za-z0-9_-]{1,128}$")
_RECORD_ID = re.compile(r"^[a-f0-9]{32}$")
_HASH = re.compile(r"^[a-f0-9]{64}$")
_BODY_KEYS = frozenset({
    "record_kind", "record_id", "action", "classification", "reason",
    "record_fingerprint", "principal", "recorded_at",
})


class AcknowledgementStorageError(RuntimeError):
    """Fixed diagnostic only; never storage contents."""


def encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def fingerprint(value) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def reason_shape_valid(reason) -> bool:
    """Bounded printable text: no control or format characters."""

    return (
        isinstance(reason, str)
        and 1 <= len(reason) <= MAX_REASON_CHARS
        and reason == reason.strip()
        and not any(
            unicodedata.category(character).startswith(("C", "Z"))
            and character != " "
            for character in reason
        )
    )


def validate_body(body) -> None:
    try:
        if not isinstance(body, dict) or set(body) != _BODY_KEYS:
            raise ValueError
        if (body["record_kind"] not in RECORD_KINDS
                or not isinstance(body["record_id"], str)
                or not _RECORD_ID.fullmatch(body["record_id"])
                or body["action"] not in ACTIONS
                or not isinstance(body["record_fingerprint"], str)
                or not _HASH.fullmatch(body["record_fingerprint"])
                or not isinstance(body["principal"], str)
                or not ADMIN_PRINCIPAL.fullmatch(body["principal"])
                or not reason_shape_valid(body["reason"])):
            raise ValueError
        if body["action"] == "acknowledge":
            if body["classification"] not in CLASSIFICATIONS:
                raise ValueError
        elif body["classification"] is not None:
            raise ValueError
        if datetime.fromisoformat(body["recorded_at"]).tzinfo is None:
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise AcknowledgementStorageError("acknowledgement_body_invalid") from None


def current_entries(events) -> dict[tuple[str, str], dict]:
    """The latest entry per record; earlier entries stay as history."""

    latest: dict[tuple[str, str], dict] = {}
    for event in events:
        body = event["body"]
        key = (body["record_kind"], body["record_id"])
        history = latest.get(key, {}).get("history_count", 0) + 1
        latest[key] = {**body, "sequence": event["sequence"],
                       "history_count": history}
    return latest


class AcknowledgementStore:
    """One hash-chained JSON ledger under the governance storage root.

    Hashes detect accidental damage, not an attacker able to rewrite both
    entries and their hashes. Plans and tasks are stored elsewhere and are
    never opened for writing here.
    """

    def __init__(self, root):
        self.root = Path(root) / MODEL
        self.path = self.root / "acknowledgements.json"
        self.fault_hook = None
        self._memo_lock = threading.Lock()
        self._memo: tuple[tuple[int, int, int], list] | None = None

    def _inject(self, stage):
        if self.fault_hook is not None:
            self.fault_hook(stage)

    def token(self) -> tuple[int, int, int] | None:
        """Identity of the persisted ledger, or None when absent."""
        try:
            info = os.stat(self.path, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError:
            raise AcknowledgementStorageError("acknowledgement_storage_unavailable") from None
        return (info.st_ino, info.st_size, info.st_mtime_ns)

    def read(self) -> list:
        try:
            if self.root.is_symlink():
                raise AcknowledgementStorageError("acknowledgement_namespace_invalid")
            try:
                fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            except FileNotFoundError:
                return []
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STORE_BYTES:
                    raise AcknowledgementStorageError("acknowledgement_storage_invalid")
                token = (info.st_ino, info.st_size, info.st_mtime_ns)
                with self._memo_lock:
                    if self._memo is not None and self._memo[0] == token:
                        return deepcopy(self._memo[1])
                raw = stream.read(MAX_STORE_BYTES + 1)
            if len(raw) > MAX_STORE_BYTES:
                raise AcknowledgementStorageError("acknowledgement_storage_invalid")
            value = json.loads(raw)
            if (not isinstance(value, dict) or set(value) != {"model", "events"}
                    or value["model"] != MODEL or not isinstance(value["events"], list)
                    or len(value["events"]) > MAX_EVENTS):
                raise AcknowledgementStorageError("acknowledgement_storage_invalid")
            previous = "0" * 64
            for index, event in enumerate(value["events"], 1):
                if (not isinstance(event, dict)
                        or set(event) != {"sequence", "body", "previous", "hash"}
                        or type(event["sequence"]) is not int or event["sequence"] != index
                        or event["previous"] != previous
                        or len(encoded(event)) > MAX_EVENT_BYTES):
                    raise AcknowledgementStorageError("acknowledgement_storage_invalid")
                validate_body(event["body"])
                material = {k: event[k] for k in ("sequence", "body", "previous")}
                if event["hash"] != fingerprint(material):
                    raise AcknowledgementStorageError("acknowledgement_integrity_failure")
                previous = event["hash"]
            with self._memo_lock:
                self._memo = (token, deepcopy(value["events"]))
            return value["events"]
        except AcknowledgementStorageError:
            raise
        except (ValueError, TypeError, RecursionError, OverflowError):
            raise AcknowledgementStorageError("acknowledgement_integrity_failure") from None
        except OSError:
            raise AcknowledgementStorageError("acknowledgement_storage_unavailable") from None

    @contextmanager
    def transaction(self):
        """One writer at a time; a held lock refuses instead of waiting."""
        fd = None
        try:
            if self.root.is_symlink():
                raise AcknowledgementStorageError("acknowledgement_namespace_invalid")
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.root / ".transaction.lock",
                         os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise AcknowledgementStorageError("acknowledgement_in_progress") from None
        except (OSError, ValueError, RecursionError):
            if fd is not None:
                os.close(fd)
            raise AcknowledgementStorageError("acknowledgement_storage_unavailable") from None
        except BaseException:
            if fd is not None:
                os.close(fd)
            raise
        try:
            yield AcknowledgementTransaction(self, self.read())
        finally:
            os.close(fd)

    def _write(self, events) -> None:
        raw = encoded({"model": MODEL, "events": events})
        if len(raw) > MAX_STORE_BYTES:
            raise AcknowledgementStorageError("acknowledgement_capacity_exceeded")
        temporary = self.root / (".acknowledgement-" + uuid.uuid4().hex)
        try:
            self._inject("before_write")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            self._inject("before_replace")
            os.replace(temporary, self.path)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except (OSError, ValueError, RecursionError):
            raise AcknowledgementStorageError("acknowledgement_persistence_failed") from None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


class AcknowledgementTransaction:
    def __init__(self, store, events):
        self.store, self.events = store, events

    def append(self, body) -> dict:
        if len(self.events) >= MAX_EVENTS:
            raise AcknowledgementStorageError("acknowledgement_capacity_exceeded")
        validate_body(body)
        event = {"sequence": len(self.events) + 1, "body": deepcopy(body),
                 "previous": self.events[-1]["hash"] if self.events else "0" * 64}
        event["hash"] = fingerprint(event)
        if len(encoded(event)) > MAX_EVENT_BYTES:
            raise AcknowledgementStorageError("acknowledgement_capacity_exceeded")
        updated = [*self.events, event]
        self.store._write(updated)
        self.events = updated
        return deepcopy(event)
