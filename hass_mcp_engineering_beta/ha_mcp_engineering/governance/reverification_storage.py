"""Bounded supplementary evidence; never rewrites execution records."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import uuid

from ..f3.models import NormalizedLockRequest, MAX_LOCK_TOKENS


MODEL = "configuration-reverification-v1"
MAX_EVENTS = 512
MAX_EVENT_BYTES = 262_144
MAX_STORE_BYTES = 16 * 1024 * 1024


class ReceiptStorageError(RuntimeError):
    """Fixed diagnostic only; no storage payload or exception reflection."""


class ReceiptBusy(ReceiptStorageError):
    pass


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def fingerprint(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def validate_body(kind, body):
    """Reject malformed or internally contradictory optional evidence."""
    try:
        binding = body["binding"]
        if (set(binding) != {"task_id", "expected_plan_hash", "request_id"}
                or not re.fullmatch(r"[a-f0-9]{32}", binding["task_id"])
                or not re.fullmatch(r"[a-f0-9]{64}", binding["expected_plan_hash"])
                or len(binding["request_id"]) != 36
                or str(uuid.UUID(binding["request_id"])) != binding["request_id"]
                or not re.fullmatch(r"[a-f0-9]{32}", body["plan_id"])):
            raise ValueError
        if kind == "started":
            if set(body) != {"binding", "plan_id", "history_hash", "started_at", "lock_requests"}:
                raise ValueError
            if not re.fullmatch(r"[a-f0-9]{64}", body["history_hash"]):
                raise ValueError
            at = datetime.fromisoformat(body["started_at"])
            requests = body["lock_requests"]
            if not isinstance(requests, list) or not 1 <= len(requests) <= MAX_LOCK_TOKENS:
                raise ValueError
            keys = []
            for request in requests:
                if set(request) != {"key", "scopes", "mode", "reason_codes"}:
                    raise ValueError
                NormalizedLockRequest(request["key"], tuple(request["scopes"]),
                                      request["mode"], tuple(request["reason_codes"])).validate()
                keys.append(request["key"])
            if keys != sorted(set(keys)):
                raise ValueError
        elif kind == "finished":
            at = datetime.fromisoformat(body["checked_at"])
            statuses = {"current_configuration_verified", "configuration_mismatch", "read_unavailable",
                        "authority_unavailable", "authority_changed", "concurrent_change",
                        "read_timeout", "authority_cleanup_failed", "interrupted_read"}
            if (body["status"] not in statuses
                    or type(body["review_resolved"]) is not bool
                    or body["review_resolved"] != (body["status"] == "current_configuration_verified")
                    or not re.fullmatch(r"[a-f0-9]{64}", body["audit_event_id"])
                    or body["receipt_persisted"] is not False or body["replayed"] is not False
                    or body["fallback"] != "none"
                    or any(type(body[key]) is not int or body[key] != 0 for key in (
                        "configuration_mutations", "device_commands", "approval_consumptions", "redispatches"))):
                raise ValueError
            if body["status"] != "interrupted_read":
                rows = body["operations"]
                if (not isinstance(rows, list) or len(rows) > 8
                        or type(body["object_read_attempts"]) is not int
                        or not len(rows) <= body["object_read_attempts"] <= 8
                        or type(body["configuration_check_attempts"]) is not int
                        or body["configuration_check_attempts"] not in {0, 1}
                        or body["configuration_check"] not in {"valid", "failed", "not_collected"}):
                    raise ValueError
                if body["review_resolved"] and (
                        not rows or len(rows) != body["object_read_attempts"]
                        or body["configuration_check_attempts"] != 1 or body["configuration_check"] != "valid"
                        or any(row.get(key) is not True for row in rows for key in (
                            "identity_match", "semantic_match", "normalization_valid"))):
                    raise ValueError
        else:
            raise ValueError
        if at.tzinfo is None:
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise ReceiptStorageError("receipt_body_invalid") from None


class ReverificationStore:
    """Optional append-only event ledger under the existing trusted disk boundary.

    The nonblocking flock spans a single explicit read operation. Readers of the
    public projection see an atomic old/new snapshot. Hashes detect accidental
    damage, not an attacker able to rewrite both records and their hashes.
    """

    def __init__(self, root):
        self.root = Path(root) / MODEL
        self.path = self.root / "receipts.json"
        self.fault_hook = None
        self.commit_unknown = False

    def _inject(self, stage):
        if self.fault_hook is not None:
            self.fault_hook(stage)

    def read(self):
        if self.commit_unknown:
            raise ReceiptStorageError("receipt_commit_unknown")
        try:
            if self.root.is_symlink():
                raise ReceiptStorageError("receipt_namespace_invalid")
            try:
                fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            except FileNotFoundError:
                return []
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STORE_BYTES:
                    raise ReceiptStorageError("receipt_storage_invalid")
                raw = stream.read(MAX_STORE_BYTES + 1)
            if len(raw) > MAX_STORE_BYTES:
                raise ReceiptStorageError("receipt_storage_invalid")
            value = json.loads(raw)
            if (not isinstance(value, dict) or set(value) != {"model", "events"}
                    or value["model"] != MODEL or not isinstance(value["events"], list)
                    or len(value["events"]) > MAX_EVENTS):
                raise ReceiptStorageError("receipt_storage_invalid")
            previous = "0" * 64
            started, finished = {}, set()
            for index, event in enumerate(value["events"], 1):
                if (not isinstance(event, dict)
                        or set(event) != {"sequence", "kind", "body", "previous", "hash"}
                        or type(event["sequence"]) is not int or event["sequence"] != index
                        or event["kind"] not in {"started", "finished"}
                        or not isinstance(event["body"], dict)
                        or len(encoded(event)) > MAX_EVENT_BYTES
                        or event["previous"] != previous):
                    raise ReceiptStorageError("receipt_storage_invalid")
                body = event["body"]
                validate_body(event["kind"], body)
                binding = body.get("binding")
                if (not isinstance(binding, dict)
                        or set(binding) != {"task_id", "expected_plan_hash", "request_id"}
                        or not all(isinstance(v, str) for v in binding.values())):
                    raise ReceiptStorageError("receipt_storage_invalid")
                request_id = binding["request_id"]
                if event["kind"] == "started":
                    if request_id in started or any(k not in finished for k in started):
                        raise ReceiptStorageError("receipt_sequence_invalid")
                    started[request_id] = body
                else:
                    start = started.get(request_id)
                    if (start is None or start["binding"] != binding or request_id in finished
                            or start["plan_id"] != body["plan_id"]
                            or (body["status"] != "interrupted_read"
                                and start["history_hash"] != body.get("history_hash"))):
                        raise ReceiptStorageError("receipt_sequence_invalid")
                    finished.add(request_id)
                material = {k: event[k] for k in ("sequence", "kind", "body", "previous")}
                if event["hash"] != fingerprint(material):
                    raise ReceiptStorageError("receipt_integrity_failure")
                previous = event["hash"]
            return value["events"]
        except ReceiptStorageError:
            raise
        except (OSError, ValueError, TypeError, RecursionError, OverflowError):
            raise ReceiptStorageError("receipt_storage_unavailable") from None

    @contextmanager
    def transaction(self):
        """One reader globally, with no waiting and no provider lock inversion."""
        fd = None
        try:
            if self.root.is_symlink():
                raise ReceiptStorageError("receipt_namespace_invalid")
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.root / ".transaction.lock",
                         os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ReceiptBusy("reverification_in_progress") from None
        except (OSError, ValueError, RecursionError):
            if fd is not None:
                os.close(fd)
            raise ReceiptStorageError("receipt_storage_unavailable") from None
        except BaseException:
            if fd is not None:
                os.close(fd)
            raise
        try:
            yield ReceiptTransaction(self, self.read())
        finally:
            if fd is not None:
                os.close(fd)

    def _write(self, events):
        raw = encoded({"model": MODEL, "events": events})
        if len(raw) > MAX_STORE_BYTES:
            raise ReceiptStorageError("receipt_capacity_exceeded")
        temporary = self.root / (".receipt-" + uuid.uuid4().hex)
        replaced = False
        try:
            self._inject("before_write")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            self._inject("before_replace")
            os.replace(temporary, self.path)
            replaced = True
            self._inject("after_replace")
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except (OSError, ValueError, RecursionError):
            self.commit_unknown = replaced
            raise ReceiptStorageError("receipt_persistence_failed") from None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


class ReceiptTransaction:
    def __init__(self, store, events):
        self.store, self.events = store, events

    def append(self, kind, body):
        if len(self.events) >= MAX_EVENTS:
            raise ReceiptStorageError("receipt_capacity_exceeded")
        validate_body(kind, body)
        prior = self.events[-1] if self.events else None
        if kind == "started":
            if ((prior is not None and prior["kind"] == "started")
                    or any(e["body"]["binding"]["request_id"] == body["binding"]["request_id"] for e in self.events)):
                raise ReceiptStorageError("receipt_sequence_invalid")
        elif (prior is None or prior["kind"] != "started"
              or prior["body"]["binding"] != body["binding"]
              or prior["body"]["plan_id"] != body["plan_id"]
              or (body["status"] != "interrupted_read"
                  and prior["body"]["history_hash"] != body.get("history_hash"))):
            raise ReceiptStorageError("receipt_sequence_invalid")
        event = {"sequence": len(self.events) + 1, "kind": kind,
                 "body": deepcopy(body),
                 "previous": self.events[-1]["hash"] if self.events else "0" * 64}
        event["hash"] = fingerprint(event)
        if len(encoded(event)) > MAX_EVENT_BYTES:
            raise ReceiptStorageError("receipt_capacity_exceeded")
        updated = [*self.events, event]
        self.store._write(updated)
        self.events = updated
        return deepcopy(event)
