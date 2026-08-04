"""Cross-process atomic durable locks for the isolated F3 execution core."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import fcntl
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, TypeVar

from .models import (
    LOCK_RECORD_SCHEMA_VERSION,
    LOCK_STATE_SCHEMA_VERSION,
    LockHandle,
    LockOwner,
    LockRecord,
    LockTiming,
    LockToken,
    NormalizedLockRequest,
    bounded_codes,
    enum_value,
    parse_timestamp,
    timestamp,
    utc_now,
    validate_lock_key,
)
from .observability import EventSink, LockMetrics, null_event_sink


LOCK_NAMESPACE = "f3-operation-locks-v1"
LOCK_STATE_FILE = "state.json"
LOCK_TRANSACTION_FILE = ".transaction.lock"
LOCK_TEMP_PREFIX = ".state.tmp-"
ORPHAN_TEMP_RETENTION_SECONDS = 86400


class DurableLockError(RuntimeError):
    """Base class for fail-closed durable lock errors."""


class LockStorageError(DurableLockError):
    pass


class LockRecordCorrupt(LockStorageError):
    pass


class LockConflict(DurableLockError):
    def __init__(self, keys: tuple[str, ...]):
        super().__init__("required durable lock is held by an incompatible owner")
        self.keys = keys


class LockWaitTimeout(DurableLockError):
    pass


class LockWaitCancelled(DurableLockError):
    pass


class LockOwnershipError(DurableLockError):
    pass


class LockLeaseExpired(DurableLockError):
    pass


class StaleRecoveryAction(str, Enum):
    RELEASE = "release"
    TRANSFER_FOR_OBSERVATION = "transfer_for_observation"
    CONFLICT_HOLD = "conflict_hold"


@dataclass(frozen=True)
class StaleRecoveryDecision:
    action: StaleRecoveryAction
    reason_code: str

    def validate(self) -> None:
        if bounded_codes((self.reason_code,)) != (self.reason_code,):
            raise ValueError("stale recovery reason is invalid")


@dataclass(frozen=True)
class StaleRecoveryResult:
    released: tuple[LockToken, ...]
    held: tuple[LockToken, ...]
    transferred_handle: LockHandle | None


def _bytewise(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values), key=lambda item: item.encode("utf-8")))


def normalize_lock_requests(
    requests: Iterable[object],
) -> tuple[NormalizedLockRequest, ...]:
    """Merge duplicates and return one bytewise-sorted request per key."""

    merged: dict[str, dict[str, Any]] = {}
    saw_request = False
    for request in requests:
        saw_request = True
        try:
            key = validate_lock_key(getattr(request, "key"))
            scopes = tuple(
                enum_value(item) for item in getattr(request, "scopes")
            )
            mode = enum_value(getattr(request, "mode"))
            reasons = tuple(str(item) for item in getattr(request, "reason_codes"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("lock request is invalid") from exc
        if not scopes or set(scopes) - {"resource", "provider"}:
            raise ValueError("lock request scopes are invalid")
        canonical_reasons = bounded_codes(reasons)
        if mode not in {"shared", "exclusive"}:
            raise ValueError("lock request mode is invalid")
        entry = merged.setdefault(
            key,
            {"scopes": set(), "mode": "shared", "reasons": set()},
        )
        entry["scopes"].update(scopes)
        entry["reasons"].update(canonical_reasons)
        if mode == "exclusive":
            entry["mode"] = "exclusive"
    if not saw_request:
        raise ValueError("at least one lock request is required")
    normalized: list[NormalizedLockRequest] = []
    for key in sorted(merged, key=lambda item: item.encode("utf-8")):
        entry = merged[key]
        request = NormalizedLockRequest(
            key=key,
            scopes=_bytewise(entry["scopes"]),
            mode=entry["mode"],
            reason_codes=_bytewise(entry["reasons"]),
        )
        request.validate()
        normalized.append(request)
    return tuple(normalized)


T = TypeVar("T")
StateMutator = Callable[[dict[str, Any]], tuple[T, bool]]
FaultHook = Callable[[str], None]


class DurableLockStore:
    """Versioned JSON lock state serialized by a stable ``flock`` inode.

    The transaction file is created once and never replaced.  Every operation
    holds an exclusive advisory lock on that inode across state read,
    validation, conflict evaluation, and atomic state replacement.  The local
    re-entrant lock adds deterministic same-process thread exclusion because
    ``flock`` ownership alone is process-oriented.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        metrics: LockMetrics | None = None,
        event_sink: EventSink | None = None,
        fault_hook: FaultHook | None = None,
    ):
        self.root = Path(root) / LOCK_NAMESPACE
        self.state_path = self.root / LOCK_STATE_FILE
        self.transaction_path = self.root / LOCK_TRANSACTION_FILE
        self.metrics = metrics or LockMetrics()
        self.event_sink = event_sink or null_event_sink
        self._fault_hook = fault_hook
        self._thread_lock = threading.RLock()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.transaction_path.touch(exist_ok=True)
        except OSError as exc:
            raise LockStorageError("unable to initialize durable lock storage") from exc

    def _inject(self, stage: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(stage)

    @contextmanager
    def _exclusive_transaction(self):
        try:
            with self._thread_lock:
                with open(self.transaction_path, "a+b") as handle:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except DurableLockError:
            raise
        except OSError as exc:
            raise LockStorageError("durable lock transaction failed") from exc

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "schema_version": LOCK_STATE_SCHEMA_VERSION,
            "next_generation": 1,
            "records": [],
        }

    def _read_state(self) -> dict[str, Any]:
        try:
            self._inject("before_state_read")
            raw = self.state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self._empty_state()
        except OSError as exc:
            raise LockStorageError("durable lock state read failed") from exc
        try:
            state = json.loads(raw)
            if not isinstance(state, dict):
                raise ValueError("state is not an object")
            if set(state) != {"schema_version", "next_generation", "records"}:
                raise ValueError("state fields are invalid")
            if type(state["schema_version"]) is not int:
                raise ValueError("lock-state schema version is invalid")
            if state["schema_version"] != LOCK_STATE_SCHEMA_VERSION:
                raise ValueError("unsupported lock-state schema")
            if type(state["next_generation"]) is not int:
                raise ValueError("lock-state generation is invalid")
            next_generation = state["next_generation"]
            records = [LockRecord.from_dict(item) for item in state["records"]]
            ordered = sorted(records, key=lambda item: (item.key.encode("utf-8"), item.generation))
            if records != ordered:
                raise ValueError("lock records are not canonical")
            identities = [(item.key, item.generation) for item in records]
            if len(identities) != len(set(identities)):
                raise ValueError("lock record identity is duplicated")
            if next_generation < 1 or any(
                item.generation >= next_generation for item in records
            ):
                raise ValueError("lock generation state is contradictory")
            return {
                "schema_version": LOCK_STATE_SCHEMA_VERSION,
                "next_generation": next_generation,
                "records": records,
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self.metrics.increment("corrupted_records")
            raise LockRecordCorrupt("durable lock state is corrupt") from exc

    def _write_state(self, state: dict[str, Any]) -> None:
        records = sorted(
            state["records"],
            key=lambda item: (item.key.encode("utf-8"), item.generation),
        )
        payload = json.dumps(
            {
                "schema_version": LOCK_STATE_SCHEMA_VERSION,
                "next_generation": state["next_generation"],
                "records": [item.to_dict() for item in records],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        temporary = self.root / (
            f"{LOCK_TEMP_PREFIX}{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
        )
        try:
            self._inject("before_state_write")
            with open(temporary, "x", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self._inject("before_state_replace")
            os.replace(temporary, self.state_path)
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            self._inject("after_state_replace")
        except DurableLockError:
            raise
        except OSError as exc:
            raise LockStorageError("atomic durable lock state write failed") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _transact(self, mutator: StateMutator[T]) -> T:
        with self._exclusive_transaction():
            state = self._read_state()
            result, changed = mutator(state)
            if changed:
                self._write_state(state)
            return result

    @staticmethod
    def _same_owner(record: LockRecord, owner: LockOwner) -> bool:
        return (
            record.owner_id == owner.owner_id
            and record.task_id == owner.task_id
            and record.plan_id == owner.plan_id
            and record.operation_id == owner.operation_id
            and record.attempt_id == owner.attempt_id
        )

    @staticmethod
    def _record_active(record: LockRecord, now: datetime) -> bool:
        return record.conflict_hold or (
            parse_timestamp(record.lease_expires_at, field_name="lease_expires_at")
            > now
        )

    def acquire_once(
        self,
        requests: Iterable[object],
        *,
        owner: LockOwner,
        timing: LockTiming,
        now: datetime | None = None,
    ) -> LockHandle:
        owner.validate()
        timing.validate()
        normalized = normalize_lock_requests(requests)
        instant = now or utc_now()
        acquired_at = timestamp(instant)
        lease_expires_at = timestamp(
            instant + timedelta(seconds=timing.lease_seconds)
        )
        self.metrics.increment("acquisition_attempts")

        def mutate(state: dict[str, Any]) -> tuple[LockHandle, bool]:
            records: list[LockRecord] = state["records"]
            existing_for_owner = [
                record for record in records if self._same_owner(record, owner)
            ]
            requested_keys = tuple(item.key for item in normalized)
            if existing_for_owner:
                by_key = {record.key: record for record in existing_for_owner}
                if tuple(sorted(by_key, key=lambda item: item.encode("utf-8"))) != requested_keys:
                    raise LockOwnershipError(
                        "owner already holds a different durable lock set"
                    )
                if any(
                    not self._record_active(by_key[item.key], instant)
                    or by_key[item.key].mode != item.mode
                    or by_key[item.key].scopes != item.scopes
                    or by_key[item.key].evidence_references != item.reason_codes
                    for item in normalized
                ):
                    raise LockOwnershipError(
                        "owner lock set does not match the requested contract"
                    )
                handle = LockHandle(
                    owner=owner,
                    tokens=tuple(
                        LockToken(item.key, by_key[item.key].generation, item.mode)
                        for item in normalized
                    ),
                    acquired_at=min(record.acquired_at for record in existing_for_owner),
                    lease_expires_at=min(
                        existing_for_owner,
                        key=lambda item: parse_timestamp(
                            item.lease_expires_at, field_name="lease_expires_at"
                        ),
                    ).lease_expires_at,
                    timing=timing,
                )
                handle.validate()
                return handle, False

            conflicts: set[str] = set()
            for request in normalized:
                holders = [record for record in records if record.key == request.key]
                for holder in holders:
                    # Expired leases remain conflicts until task-aware stale
                    # recovery explicitly releases, transfers, or holds them.
                    if holder.conflict_hold or request.mode == "exclusive" or holder.mode == "exclusive":
                        conflicts.add(request.key)
                    elif not self._record_active(holder, instant):
                        conflicts.add(request.key)
            if conflicts:
                keys = _bytewise(conflicts)
                self.metrics.increment("conflicts")
                for key in keys:
                    self.event_sink(
                        {
                            "event_type": "lock_conflict",
                            "task_id": owner.task_id,
                            "attempt_id": owner.attempt_id,
                            "owner_id": owner.owner_id,
                            "lock_key": key,
                        }
                    )
                raise LockConflict(keys)

            tokens: list[LockToken] = []
            for request in normalized:
                generation = state["next_generation"]
                state["next_generation"] += 1
                record = LockRecord(
                    schema_version=LOCK_RECORD_SCHEMA_VERSION,
                    key=request.key,
                    scopes=request.scopes,
                    mode=request.mode,
                    owner_id=owner.owner_id,
                    task_id=owner.task_id,
                    plan_id=owner.plan_id,
                    operation_id=owner.operation_id,
                    attempt_id=owner.attempt_id,
                    acquired_at=acquired_at,
                    lease_expires_at=lease_expires_at,
                    last_renewed_at=acquired_at,
                    generation=generation,
                    evidence_references=request.reason_codes,
                )
                record.validate()
                records.append(record)
                tokens.append(LockToken(request.key, generation, request.mode))
            handle = LockHandle(
                owner=owner,
                tokens=tuple(tokens),
                acquired_at=acquired_at,
                lease_expires_at=lease_expires_at,
                timing=timing,
            )
            handle.validate()
            return handle, True

        handle = self._transact(mutate)
        self.metrics.increment("acquisitions")
        for token in handle.tokens:
            self.event_sink(
                {
                    "event_type": "lock_acquired",
                    "task_id": owner.task_id,
                    "attempt_id": owner.attempt_id,
                    "owner_id": owner.owner_id,
                    "lock_key": token.key,
                    "generation": token.generation,
                }
            )
        return handle

    async def acquire(
        self,
        requests: Iterable[object],
        *,
        owner: LockOwner,
        timing: LockTiming,
        now: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        cancelled: Callable[[], bool] | None = None,
    ) -> LockHandle:
        timing.validate()
        started = monotonic()
        while True:
            if cancelled is not None and cancelled():
                raise LockWaitCancelled("durable lock wait was cancelled")
            try:
                return self.acquire_once(
                    requests,
                    owner=owner,
                    timing=timing,
                    now=now(),
                )
            except LockConflict:
                elapsed = monotonic() - started
                if timing.wait_timeout_seconds == 0:
                    raise
                if elapsed >= timing.wait_timeout_seconds:
                    self.metrics.increment("wait_timeouts")
                    raise LockWaitTimeout("durable lock wait timed out")
                await sleep(
                    min(
                        timing.poll_interval_seconds,
                        timing.wait_timeout_seconds - elapsed,
                    )
                )

    def validate_handle(
        self,
        handle: LockHandle,
        *,
        now: datetime | None = None,
        allow_conflict_hold: bool = False,
    ) -> None:
        handle.validate()
        instant = now or utc_now()

        def read(state: dict[str, Any]) -> tuple[None, bool]:
            records: list[LockRecord] = state["records"]
            for token in handle.tokens:
                matches = [
                    record
                    for record in records
                    if record.key == token.key and record.generation == token.generation
                ]
                if len(matches) != 1 or not self._same_owner(matches[0], handle.owner):
                    self.metrics.increment("fencing_rejections")
                    raise LockOwnershipError("durable lock fencing token is stale")
                record = matches[0]
                if record.conflict_hold and not allow_conflict_hold:
                    raise LockOwnershipError("durable lock is a conflict hold")
                if not record.conflict_hold and not self._record_active(record, instant):
                    raise LockLeaseExpired("durable lock lease expired")
            return None, False

        self._transact(read)

    def renew(
        self,
        handle: LockHandle,
        *,
        now: datetime | None = None,
    ) -> LockHandle:
        handle.validate()
        instant = now or utc_now()
        renewed_at = timestamp(instant)
        expires_at = timestamp(
            instant + timedelta(seconds=handle.timing.lease_seconds)
        )

        def mutate(state: dict[str, Any]) -> tuple[LockHandle, bool]:
            records: list[LockRecord] = state["records"]
            selected: list[LockRecord] = []
            for token in handle.tokens:
                matches = [
                    item for item in records
                    if item.key == token.key and item.generation == token.generation
                ]
                if len(matches) != 1 or not self._same_owner(matches[0], handle.owner):
                    raise LockOwnershipError("durable lock fencing token is stale")
                record = matches[0]
                if record.conflict_hold:
                    raise LockOwnershipError("conflict hold cannot be renewed")
                if not self._record_active(record, instant):
                    raise LockLeaseExpired("durable lock lease expired before renewal")
                selected.append(record)
            self._inject("during_lock_renewal")
            for record in selected:
                record.last_renewed_at = renewed_at
                record.lease_expires_at = expires_at
            renewed = LockHandle(
                owner=handle.owner,
                tokens=handle.tokens,
                acquired_at=handle.acquired_at,
                lease_expires_at=expires_at,
                timing=handle.timing,
            )
            renewed.validate()
            return renewed, True

        try:
            result = self._transact(mutate)
        except DurableLockError:
            self.metrics.increment("renewal_failures")
            self.metrics.increment("fencing_rejections")
            raise
        self.metrics.increment("renewals")
        return result

    def release(self, handle: LockHandle) -> tuple[str, ...]:
        handle.validate()
        reverse_tokens = tuple(reversed(handle.tokens))

        def mutate(state: dict[str, Any]) -> tuple[tuple[str, ...], bool]:
            records: list[LockRecord] = state["records"]
            selected: list[LockRecord] = []
            for token in reverse_tokens:
                matches = [
                    item for item in records
                    if item.key == token.key and item.generation == token.generation
                ]
                if len(matches) != 1 or not self._same_owner(matches[0], handle.owner):
                    raise LockOwnershipError("durable lock release was fenced")
                selected.append(matches[0])
            self._inject("during_lock_release")
            for record in selected:
                records.remove(record)
            return tuple(token.key for token in reverse_tokens), True

        try:
            released = self._transact(mutate)
        except DurableLockError:
            self.metrics.increment("release_failures")
            self.metrics.increment("fencing_rejections")
            raise
        self.metrics.increment("releases")
        for key in released:
            self.event_sink(
                {
                    "event_type": "lock_released",
                    "task_id": handle.owner.task_id,
                    "attempt_id": handle.owner.attempt_id,
                    "owner_id": handle.owner.owner_id,
                    "lock_key": key,
                }
            )
        return released

    def promote_to_conflict_hold(
        self,
        handle: LockHandle,
        *,
        reason_code: str,
    ) -> None:
        reason = bounded_codes((reason_code,))[0]

        def mutate(state: dict[str, Any]) -> tuple[None, bool]:
            records: list[LockRecord] = state["records"]
            for token in handle.tokens:
                matches = [
                    item for item in records
                    if item.key == token.key and item.generation == token.generation
                ]
                if len(matches) != 1 or not self._same_owner(matches[0], handle.owner):
                    raise LockOwnershipError("conflict-hold promotion was fenced")
                record = matches[0]
                record.conflict_hold = True
                record.evidence_references = _bytewise(
                    (*record.evidence_references, reason)
                )
                if len(record.evidence_references) > 16:
                    raise LockOwnershipError("conflict-hold evidence bound exceeded")
            return None, True

        self._transact(mutate)

    def expired_records(self, *, now: datetime | None = None) -> tuple[LockRecord, ...]:
        instant = now or utc_now()

        def read(state: dict[str, Any]) -> tuple[tuple[LockRecord, ...], bool]:
            records = tuple(
                LockRecord.from_dict(item.to_dict())
                for item in state["records"]
                if not item.conflict_hold
                and parse_timestamp(item.lease_expires_at, field_name="lease_expires_at")
                <= instant
            )
            return records, False

        return self._transact(read)

    def recover_expired(
        self,
        decisions: Mapping[tuple[str, int], StaleRecoveryDecision],
        *,
        transfer_owner: LockOwner | None = None,
        transfer_timing: LockTiming | None = None,
        now: datetime | None = None,
    ) -> StaleRecoveryResult:
        for decision in decisions.values():
            decision.validate()
        if transfer_owner is not None:
            transfer_owner.validate()
        if transfer_timing is not None:
            transfer_timing.validate()
        instant = now or utc_now()

        def mutate(state: dict[str, Any]) -> tuple[StaleRecoveryResult, bool]:
            records: list[LockRecord] = state["records"]
            released: list[LockToken] = []
            held: list[LockToken] = []
            transferred: list[LockToken] = []
            transferred_at = timestamp(instant)
            transferred_expiry = (
                timestamp(instant + timedelta(seconds=transfer_timing.lease_seconds))
                if transfer_timing is not None
                else None
            )
            changed = False
            for identity, decision in sorted(decisions.items()):
                key, generation = identity
                matches = [
                    item for item in records
                    if item.key == key and item.generation == generation
                ]
                if len(matches) != 1:
                    raise LockOwnershipError("stale recovery generation changed")
                record = matches[0]
                if record.conflict_hold or self._record_active(record, instant):
                    raise LockOwnershipError("stale recovery requires an expired lease")
                token = LockToken(record.key, record.generation, record.mode)
                if decision.action == StaleRecoveryAction.RELEASE:
                    records.remove(record)
                    released.append(token)
                elif decision.action == StaleRecoveryAction.CONFLICT_HOLD:
                    record.conflict_hold = True
                    record.evidence_references = _bytewise(
                        (*record.evidence_references, decision.reason_code)
                    )
                    held.append(token)
                else:
                    if transfer_owner is None or transfer_timing is None or transferred_expiry is None:
                        raise LockOwnershipError("stale transfer requires an owner and timing")
                    if (
                        transfer_owner.task_id != record.task_id
                        or transfer_owner.plan_id != record.plan_id
                        or transfer_owner.operation_id != record.operation_id
                        or transfer_owner.attempt_id != record.attempt_id
                    ):
                        raise LockOwnershipError("stale transfer changed durable task identity")
                    new_generation = state["next_generation"]
                    state["next_generation"] += 1
                    record.owner_id = transfer_owner.owner_id
                    record.acquired_at = transferred_at
                    record.last_renewed_at = transferred_at
                    record.lease_expires_at = transferred_expiry
                    record.generation = new_generation
                    record.evidence_references = _bytewise(
                        (*record.evidence_references, decision.reason_code)
                    )
                    transferred.append(
                        LockToken(record.key, new_generation, record.mode)
                    )
                changed = True
            handle = None
            if transferred:
                handle = LockHandle(
                    owner=transfer_owner,
                    tokens=tuple(sorted(transferred, key=lambda item: item.key.encode("utf-8"))),
                    acquired_at=transferred_at,
                    lease_expires_at=str(transferred_expiry),
                    timing=transfer_timing,
                )
                handle.validate()
            return (
                StaleRecoveryResult(
                    released=tuple(released),
                    held=tuple(held),
                    transferred_handle=handle,
                ),
                changed,
            )

        result = self._transact(mutate)
        recovered_count = len(result.released) + len(result.held)
        if result.transferred_handle is not None:
            recovered_count += len(result.transferred_handle.tokens)
        self.metrics.increment("stale_recoveries", recovered_count)
        return result

    def records(self) -> tuple[LockRecord, ...]:
        def read(state: dict[str, Any]) -> tuple[tuple[LockRecord, ...], bool]:
            return (
                tuple(LockRecord.from_dict(item.to_dict()) for item in state["records"]),
                False,
            )

        return self._transact(read)

    def snapshot(self, *, now: datetime | None = None) -> dict[str, int]:
        instant = now or utc_now()
        records = self.records()
        active = sum(self._record_active(item, instant) for item in records)
        holds = sum(item.conflict_hold for item in records)
        expired = len(records) - active
        return self.metrics.snapshot(
            current_active_lock_count=active,
            current_conflict_hold_count=holds,
            current_expired_lease_count=expired,
        )

    def cleanup_orphaned_temporary_files(
        self,
        *,
        now_epoch: float | None = None,
    ) -> int:
        cutoff = (now_epoch if now_epoch is not None else time.time()) - (
            ORPHAN_TEMP_RETENTION_SECONDS
        )
        removed = 0
        with self._exclusive_transaction():
            for path in sorted(self.root.glob(f"{LOCK_TEMP_PREFIX}*")):
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise LockStorageError(
                        "durable lock temporary cleanup failed"
                    ) from exc
        return removed
