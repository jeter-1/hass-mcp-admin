"""Durable ledger of the options flows Engineering opens (HAMCP-232 H2-R1).

Core holds an options flow in memory only, with no timeout, until it is
finished, aborted, or Core restarts.  Every flow this family opens is
recorded here before the open is sent, and bound to its flow id before the
first abort is attempted, so a failed or unknown abort survives an
Engineering restart and is retried by the recovery sweep until Core confirms
the flow is gone (aborted now, or a 404).  The retries are bounded; after the
bound the flow is reported as orphaned, with its entry id.

A lost open response leaves a flow whose id was never received.  Core
2026.10.3 has no read that lists in-progress options flows (the websocket
``config_entries/flow/progress`` and ``flow/subscribe`` commands cover only
config flows, ``components/config/config_entries.py:384-466``), so such a
flow cannot be found or aborted.  Owner decision (HAMCP-232/145 delta 2):
the residual is accepted and disclosed as ``possibly_open``; it holds no
configuration change and is discarded at the next Home Assistant restart.

The ledger never sends anything.  The store is one JSON document under the
F3 storage root, replaced atomically under an exclusive lock; it is removed
when no record remains, so a clean read leaves no bytes behind.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import threading
from typing import Any
import uuid

MODEL = "helper-options-flows-v1"
FILE_NAME = "flows.json"
MAX_RECORDS = 256
MAX_STORE_BYTES = 262_144
# Each recovery sweep retries an unconfirmed abort once; after this many
# failed retries the flow is reported as orphaned and never retried again.
MAX_SWEEP_ABORT_ATTEMPTS = 10
MAX_HEALTH_ENTRIES = 16

STATUS_OPENING = "opening"
STATUS_POSSIBLY_OPEN = "possibly_open"
STATUS_OPEN = "open"
STATUS_ABORT_FAILED = "abort_failed"
STATUS_ORPHANED = "orphaned"
STATUSES = (
    STATUS_OPENING, STATUS_POSSIBLY_OPEN, STATUS_OPEN, STATUS_ABORT_FAILED, STATUS_ORPHANED,
)
# The statuses the recovery sweep retries (an abort by a known flow id).
RETRY_STATUSES = (STATUS_OPEN, STATUS_ABORT_FAILED)
# Residuals no retry can clear: only these are evicted when the ledger is full.
RESIDUAL_STATUSES = (STATUS_POSSIBLY_OPEN, STATUS_ORPHANED)

POSSIBLY_OPEN_DISCLOSURE = (
    "options flow possibly left open on entry {entry_id} (no flow id received); "
    "it holds no configuration change and is discarded at the next Home "
    "Assistant restart"
)
ORPHANED_DISCLOSURE = (
    "options flow {flow_id} on entry {entry_id} could not be aborted after "
    "{attempts} recovery retries; it holds no configuration change and is "
    "discarded at the next Home Assistant restart"
)

_FLOW_ID = re.compile(r"[0-9a-f]{32}\Z")
_ENTRY_ID = re.compile(r"[0-9A-Za-z]{1,64}\Z")
_RECORD_ID = re.compile(r"[0-9a-f]{32}\Z")
_REFERENCE = re.compile(r"[0-9A-Za-z_-]{1,64}\Z")
_PHASES = ("planning", "dispatch", "readback")
_FIELDS = frozenset({
    "record_id", "status", "entry_id", "flow_id", "plan_id", "task_id", "phase",
    "recorded_at", "attempts", "last_attempt_at",
})

# Who opened the flow: set by the F3 adapter around a dispatch or readback.
# Planning runs before a plan exists, so it carries no plan or task id.
_FLOW_CONTEXT: ContextVar[tuple[str | None, str | None, str]] = ContextVar(
    "helper_options_flow_context", default=(None, None, "planning")
)


@contextmanager
def flow_context(*, plan_id: Any, task_id: Any, phase: str) -> Iterator[None]:
    if phase not in _PHASES:
        raise ValueError("unknown options flow phase")
    token = _FLOW_CONTEXT.set((_reference(plan_id), _reference(task_id), phase))
    try:
        yield
    finally:
        _FLOW_CONTEXT.reset(token)


def _reference(value: Any) -> str | None:
    return value if isinstance(value, str) and _REFERENCE.fullmatch(value) else None


class FlowLedgerError(RuntimeError):
    """Fixed diagnostic only; no storage payload or exception reflection."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _timestamp(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _valid_record(record: Any) -> bool:
    if not isinstance(record, dict) or set(record) != _FIELDS:
        return False
    status, flow_id = record["status"], record["flow_id"]
    attempts = record["attempts"]
    return (
        isinstance(record["record_id"], str)
        and bool(_RECORD_ID.fullmatch(record["record_id"]))
        and status in STATUSES
        and isinstance(record["entry_id"], str)
        and bool(_ENTRY_ID.fullmatch(record["entry_id"]))
        # A flow id is known exactly for the statuses that abort by it.
        and (
            (isinstance(flow_id, str) and bool(_FLOW_ID.fullmatch(flow_id)))
            if status in (*RETRY_STATUSES, STATUS_ORPHANED)
            else flow_id is None
        )
        and all(
            record[name] is None or _reference(record[name]) is not None
            for name in ("plan_id", "task_id")
        )
        and record["phase"] in _PHASES
        and _timestamp(record["recorded_at"])
        and type(attempts) is int
        and 0 <= attempts <= MAX_SWEEP_ABORT_ATTEMPTS
        and (record["last_attempt_at"] is None or _timestamp(record["last_attempt_at"]))
    )


class HelperOptionsFlowLedger:
    """The durable record of every options flow this family opened.

    ``live`` holds the records owned by running code in this process (an
    open in flight, a flow held for its submit).  The sweep never touches a
    live record; after a restart nothing is live, so every recorded flow is
    the sweep's to settle.
    """

    def __init__(self, storage_root: Any, *, now: Callable[[], datetime]) -> None:
        self.root = Path(storage_root) / MODEL
        self.path = self.root / FILE_NAME
        self.now = now
        self.live: set[str] = set()
        # flow id -> its live record id, released without a storage read.
        self._live_flows: dict[str, str] = {}
        self._lock = threading.Lock()

    # --- storage -------------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        try:
            if self.root.is_symlink():
                raise FlowLedgerError("helper_options_flow_ledger_invalid")
            try:
                fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            except FileNotFoundError:
                return {"model": MODEL, "records": [], "evicted": 0}
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STORE_BYTES:
                    raise FlowLedgerError("helper_options_flow_ledger_invalid")
                raw = stream.read(MAX_STORE_BYTES + 1)
            value = json.loads(raw)
        except FlowLedgerError:
            raise
        except (ValueError, TypeError, RecursionError):
            raise FlowLedgerError("helper_options_flow_ledger_invalid") from None
        except OSError:
            raise FlowLedgerError("helper_options_flow_ledger_unavailable") from None
        if (
            not isinstance(value, dict)
            or set(value) != {"model", "records", "evicted"}
            or value["model"] != MODEL
            or not isinstance(value["records"], list)
            or len(value["records"]) > MAX_RECORDS
            or type(value["evicted"]) is not int
            or value["evicted"] < 0
            or not all(_valid_record(record) for record in value["records"])
            or len({record["record_id"] for record in value["records"]})
            != len(value["records"])
        ):
            raise FlowLedgerError("helper_options_flow_ledger_invalid")
        return value

    def _write(self, document: dict[str, Any]) -> None:
        try:
            if not document["records"] and not document["evicted"]:
                self.path.unlink(missing_ok=True)
            else:
                raw = json.dumps(
                    document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                ).encode("utf-8")
                if len(raw) > MAX_STORE_BYTES:
                    raise FlowLedgerError("helper_options_flow_ledger_full")
                temporary = self.root / (".flows-" + uuid.uuid4().hex)
                try:
                    fd = os.open(
                        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                    )
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, self.path)
                finally:
                    temporary.unlink(missing_ok=True)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except FlowLedgerError:
            raise
        except (OSError, ValueError, TypeError):
            raise FlowLedgerError("helper_options_flow_ledger_unavailable") from None

    @contextmanager
    def _transaction(self) -> Iterator[dict[str, Any]]:
        """Read, change and replace the document under an exclusive lock.

        The lock is a ``flock`` of the namespace directory itself, so the
        store leaves no lock file behind.  The document is written only when
        the caller changed it.
        """

        with self._lock:
            directory = None
            try:
                if self.root.is_symlink():
                    raise FlowLedgerError("helper_options_flow_ledger_invalid")
                self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
                directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                fcntl.flock(directory, fcntl.LOCK_EX)
            except FlowLedgerError:
                if directory is not None:
                    os.close(directory)
                raise
            except OSError:
                if directory is not None:
                    os.close(directory)
                raise FlowLedgerError("helper_options_flow_ledger_unavailable") from None
            try:
                document = self._read()
                before = json.dumps(document, sort_keys=True)
                yield document
                if json.dumps(document, sort_keys=True) != before:
                    self._write(document)
            finally:
                os.close(directory)

    def records(self) -> list[dict[str, Any]]:
        return [dict(record) for record in self._read()["records"]]

    # --- the gateway's hooks ---------------------------------------------------

    def begin_open(self, entry_id: str) -> str:
        """Record the open intent; raised errors mean the open is not sent."""

        if not isinstance(entry_id, str) or not _ENTRY_ID.fullmatch(entry_id):
            raise FlowLedgerError("helper_options_flow_ledger_invalid")
        plan_id, task_id, phase = _FLOW_CONTEXT.get()
        record_id = uuid.uuid4().hex
        record = {
            "record_id": record_id,
            "status": STATUS_OPENING,
            "entry_id": entry_id,
            "flow_id": None,
            "plan_id": plan_id,
            "task_id": task_id,
            "phase": phase,
            "recorded_at": self.now().isoformat(),
            "attempts": 0,
            "last_attempt_at": None,
        }
        with self._transaction() as document:
            records = document["records"]
            if len(records) >= MAX_RECORDS:
                residual = [
                    item for item in records
                    if item["status"] in RESIDUAL_STATUSES
                    or (item["status"] == STATUS_OPENING and item["record_id"] not in self.live)
                ]
                if not residual:
                    raise FlowLedgerError("helper_options_flow_ledger_full")
                oldest = min(residual, key=lambda item: item["recorded_at"])
                records.remove(oldest)
                document["evicted"] += 1
            records.append(record)
        self.live.add(record_id)
        return record_id

    def _update(self, select: Callable[[dict[str, Any]], bool], change: Callable[[dict[str, Any]], bool]) -> None:
        """Apply ``change`` to each selected record; False removes it."""

        with self._transaction() as document:
            kept = []
            for record in document["records"]:
                if select(record) and not change(record):
                    continue
                kept.append(record)
            document["records"] = kept

    def open_failed(self, record_id: str, *, unconfirmed: bool) -> None:
        """The open raised: an unconfirmed open may have created a flow."""

        self.live.discard(record_id)

        def change(record: dict[str, Any]) -> bool:
            record["status"] = STATUS_POSSIBLY_OPEN
            return unconfirmed

        self._update(lambda record: record["record_id"] == record_id, change)

    def opened(self, record_id: str, flow_id: Any, *, finished: bool) -> None:
        """Bind the received flow id; a finished flow leaves no record.

        A response with no valid flow id that is not a finished flow may
        still have created a flow nobody can abort: possibly open.
        """

        bound = isinstance(flow_id, str) and bool(_FLOW_ID.fullmatch(flow_id))
        if finished or not bound:
            self.live.discard(record_id)

        def change(record: dict[str, Any]) -> bool:
            if finished:
                return False
            if bound:
                record["status"], record["flow_id"] = STATUS_OPEN, flow_id
            else:
                record["status"] = STATUS_POSSIBLY_OPEN
            return True

        self._update(lambda record: record["record_id"] == record_id, change)
        if bound and not finished and record_id in self.live:
            self._live_flows[flow_id] = record_id

    def _flow_records(self, flow_id: str) -> Callable[[dict[str, Any]], bool]:
        return lambda record: record["flow_id"] == flow_id

    def _release_live(self, flow_id: str) -> None:
        record_id = self._live_flows.pop(flow_id, None)
        if record_id is not None:
            self.live.discard(record_id)

    def flow_gone(self, flow_id: str) -> None:
        """Core confirmed the flow is gone: aborted, finished, or a 404."""

        self._release_live(flow_id)
        self._update(self._flow_records(flow_id), lambda record: False)

    def abort_failed(self, flow_id: str) -> None:
        """The abort failed or its outcome is unknown: the sweep retries it."""

        self._release_live(flow_id)

        def change(record: dict[str, Any]) -> bool:
            if record["status"] == STATUS_OPEN:
                record["status"] = STATUS_ABORT_FAILED
            return True

        self._update(self._flow_records(flow_id), change)

    # --- the recovery sweep ------------------------------------------------------

    def retry_candidates(self) -> list[dict[str, Any]]:
        return [
            dict(record) for record in self._read()["records"]
            if record["status"] in RETRY_STATUSES and record["record_id"] not in self.live
        ]

    def sweep_attempted(self, record_id: str, *, gone: bool) -> str | None:
        """Record one sweep retry; returns the record's new status.

        None means the record was removed (Core confirmed the flow gone);
        ``"skipped"`` means no retryable, unowned record matched.
        """

        outcome: list[str | None] = ["skipped"]

        def change(record: dict[str, Any]) -> bool:
            if record["status"] not in RETRY_STATUSES or gone:
                outcome[0] = None if gone else record["status"]
                return not gone
            record["attempts"] += 1
            record["last_attempt_at"] = self.now().isoformat()
            record["status"] = (
                STATUS_ORPHANED
                if record["attempts"] >= MAX_SWEEP_ABORT_ATTEMPTS
                else STATUS_ABORT_FAILED
            )
            outcome[0] = record["status"]
            return True

        self._update(
            lambda record: record["record_id"] == record_id and record_id not in self.live,
            change,
        )
        return outcome[0]

    # --- health ------------------------------------------------------------------

    def health_summary(self) -> dict[str, Any]:
        """A bounded projection for server health; never raises."""

        try:
            document = self._read()
        except FlowLedgerError as exc:
            return {"status": "unavailable", "reason_code": exc.category}
        except Exception:  # noqa: BLE001 - health never raises
            return {"status": "unavailable", "reason_code": "helper_options_flow_ledger_unavailable"}
        counts = {status: 0 for status in (*RESIDUAL_STATUSES, *RETRY_STATUSES, "in_progress")}
        entries = []
        for record in document["records"]:
            status = record["status"]
            if record["record_id"] in self.live:
                status = "in_progress"
            elif status == STATUS_OPENING:
                # The open never resolved before a restart: no id received.
                status = STATUS_POSSIBLY_OPEN
            counts[status] += 1
            if status == "in_progress":
                continue
            entry = {
                "entry_id": record["entry_id"],
                "status": status,
                "flow_id": record["flow_id"],
                "plan_id": record["plan_id"],
                "task_id": record["task_id"],
                "phase": record["phase"],
                "recorded_at": record["recorded_at"],
                "abort_retry_attempts": record["attempts"],
            }
            if status == STATUS_POSSIBLY_OPEN:
                entry["disclosure"] = POSSIBLY_OPEN_DISCLOSURE.format(entry_id=record["entry_id"])
            elif status == STATUS_ORPHANED:
                entry["disclosure"] = ORPHANED_DISCLOSURE.format(
                    flow_id=record["flow_id"], entry_id=record["entry_id"],
                    attempts=record["attempts"],
                )
            entries.append(entry)
        residual = counts[STATUS_POSSIBLY_OPEN] + counts[STATUS_ORPHANED]
        pending = counts[STATUS_OPEN] + counts[STATUS_ABORT_FAILED]
        return {
            "status": (
                "attention_required" if residual
                else "retrying_abort" if pending
                else "clear"
            ),
            "counts": counts,
            "evicted_residual_count": document["evicted"],
            "max_abort_retry_attempts": MAX_SWEEP_ABORT_ATTEMPTS,
            "flows": entries[:MAX_HEALTH_ENTRIES],
            "details_truncated": len(entries) > MAX_HEALTH_ENTRIES,
        }


__all__ = [
    "FlowLedgerError",
    "HelperOptionsFlowLedger",
    "MAX_RECORDS",
    "MAX_SWEEP_ABORT_ATTEMPTS",
    "MODEL",
    "ORPHANED_DISCLOSURE",
    "POSSIBLY_OPEN_DISCLOSURE",
    "STATUS_ABORT_FAILED",
    "STATUS_OPEN",
    "STATUS_OPENING",
    "STATUS_ORPHANED",
    "STATUS_POSSIBLY_OPEN",
    "flow_context",
]
