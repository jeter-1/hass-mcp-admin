"""HAMCP-253: append-only administrator acknowledgements of stuck records.

An acknowledgement is supplementary evidence about one exact governance
record state. It never rewrites, deletes or re-validates the plan or task it
names, and nothing that approves, applies or recovers a record reads it. The
ledger only decides whether health warns about a record that is already stuck.

Audit coordination (R1-M3): a decision entry is written first and is not
effective until a later completion entry records that its audit event was
durably written. The audit event carries an identifier derived from the
decision entry, so writing it again after a crash is idempotent. A decision
without a completion is pending: it acknowledges nothing, health reports it,
and the next writer reconciles it before appending anything else.
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
DECISION = "decision"
AUDIT_COMPLETION = "audit_completion"
# ``recorded``: the decision's audit event is durably written and the
# decision is effective. ``abandoned``: the audit write failed while the
# request was answered, so the decision never took effect.
COMPLETION_OUTCOMES = ("recorded", "abandoned")
_BODY_KEYS = frozenset({
    "entry_type", "record_kind", "record_id", "action", "classification",
    "reason", "record_fingerprint", "principal", "recorded_at",
})
_COMPLETION_KEYS = frozenset({
    "entry_type", "decision_sequence", "audit_event_id", "outcome",
    "recorded_at",
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


def audit_event_id(event) -> str:
    """The audit identifier of one decision entry, stable across retries."""

    return fingerprint({"model": MODEL, "audit_of": event["hash"]})


def _timestamp_valid(value) -> None:
    if datetime.fromisoformat(value).tzinfo is None:
        raise ValueError


def validate_body(body) -> None:
    try:
        if (not isinstance(body, dict) or set(body) != _BODY_KEYS
                or body["entry_type"] != DECISION):
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
        _timestamp_valid(body["recorded_at"])
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise AcknowledgementStorageError("acknowledgement_body_invalid") from None


def validate_completion(body) -> None:
    try:
        if (not isinstance(body, dict) or set(body) != _COMPLETION_KEYS
                or body["entry_type"] != AUDIT_COMPLETION
                or type(body["decision_sequence"]) is not int
                or not isinstance(body["audit_event_id"], str)
                or not _HASH.fullmatch(body["audit_event_id"])
                or body["outcome"] not in COMPLETION_OUTCOMES):
            raise ValueError
        _timestamp_valid(body["recorded_at"])
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise AcknowledgementStorageError("acknowledgement_body_invalid") from None


def completions(events) -> dict[int, str]:
    """Completion outcome by decision sequence."""

    return {
        event["body"]["decision_sequence"]: event["body"]["outcome"]
        for event in events
        if event["body"]["entry_type"] == AUDIT_COMPLETION
    }


def pending_decisions(events) -> list[dict]:
    """Decisions whose audit event is not yet known to be written."""

    done = completions(events)
    return [
        event for event in events
        if event["body"]["entry_type"] == DECISION
        and event["sequence"] not in done
    ]


def audit_counts(events) -> dict[str, int]:
    done = completions(events)
    return {
        "audit_pending_count": len(pending_decisions(events)),
        "audit_abandoned_count": sum(
            outcome == "abandoned" for outcome in done.values()
        ),
    }


def current_entries(events) -> dict[tuple[str, str], dict]:
    """The latest effective decision per record; earlier ones are history.

    Only a decision whose audit event is recorded is effective. A pending or
    abandoned decision never acknowledges or revokes anything.
    """

    done = completions(events)
    latest: dict[tuple[str, str], dict] = {}
    for event in events:
        body = event["body"]
        if body["entry_type"] != DECISION or done.get(event["sequence"]) != "recorded":
            continue
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
            completed: set[int] = set()
            for index, event in enumerate(value["events"], 1):
                if (not isinstance(event, dict)
                        or set(event) != {"sequence", "body", "previous", "hash"}
                        or type(event["sequence"]) is not int or event["sequence"] != index
                        or event["previous"] != previous
                        or not isinstance(event["body"], dict)
                        or len(encoded(event)) > MAX_EVENT_BYTES):
                    raise AcknowledgementStorageError("acknowledgement_storage_invalid")
                if event["body"].get("entry_type") == AUDIT_COMPLETION:
                    validate_completion(event["body"])
                else:
                    validate_body(event["body"])
                material = {k: event[k] for k in ("sequence", "body", "previous")}
                if event["hash"] != fingerprint(material):
                    raise AcknowledgementStorageError("acknowledgement_integrity_failure")
                body = event["body"]
                if body["entry_type"] == AUDIT_COMPLETION:
                    # A completion names one earlier decision, by its audit
                    # identifier, and completes it at most once.
                    target = body["decision_sequence"]
                    if (not 1 <= target < index or target in completed
                            or value["events"][target - 1]["body"]["entry_type"] != DECISION
                            or body["audit_event_id"]
                            != audit_event_id(value["events"][target - 1])):
                        raise AcknowledgementStorageError("acknowledgement_storage_invalid")
                    completed.add(target)
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
        """Append one decision; it is pending until ``complete`` records it."""

        if pending_decisions(self.events):
            # Reconcile first: never stack a decision on an unaudited one.
            raise AcknowledgementStorageError("acknowledgement_audit_pending")
        if len(self.events) + 2 > MAX_EVENTS:
            # Room for the decision and its completion, or neither.
            raise AcknowledgementStorageError("acknowledgement_capacity_exceeded")
        validate_body(body)
        return self._append(body)

    def complete(self, decision, outcome, recorded_at) -> dict:
        if (decision not in pending_decisions(self.events)
                or outcome not in COMPLETION_OUTCOMES):
            raise AcknowledgementStorageError("acknowledgement_body_invalid")
        body = {
            "entry_type": AUDIT_COMPLETION,
            "decision_sequence": decision["sequence"],
            "audit_event_id": audit_event_id(decision),
            "outcome": outcome,
            "recorded_at": recorded_at,
        }
        validate_completion(body)
        return self._append(body)

    def _append(self, body) -> dict:
        if len(self.events) >= MAX_EVENTS:
            raise AcknowledgementStorageError("acknowledgement_capacity_exceeded")
        event = {"sequence": len(self.events) + 1, "body": deepcopy(body),
                 "previous": self.events[-1]["hash"] if self.events else "0" * 64}
        event["hash"] = fingerprint(event)
        if len(encoded(event)) > MAX_EVENT_BYTES:
            raise AcknowledgementStorageError("acknowledgement_capacity_exceeded")
        updated = [*self.events, event]
        self.store._write(updated)
        self.events = updated
        return deepcopy(event)
