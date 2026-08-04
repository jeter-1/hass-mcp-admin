"""Durable attempt and dispatch-intent persistence for F3-A."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, TypeVar

from .models import (
    EXECUTION_RECORD_SCHEMA_VERSION,
    F3_ADAPTER_CONTRACT_MODEL,
    NORMALIZED_OUTCOME_TO_TASK_STATE,
    ExecutionIdentity,
    ExecutionRecord,
    ExecutorTiming,
    LockHandle,
    append_execution_event,
    bounded_diagnostics,
    parse_timestamp,
    timestamp,
    utc_now,
    validate_identifier,
    validate_sha256,
)
from .observability import EventSink, ExecutorMetrics, null_event_sink


EXECUTION_NAMESPACE = "f3-operation-executions-v1"
EXECUTION_TRANSACTION_FILE = ".transaction.lock"
EXECUTION_TEMP_PREFIX = ".execution.tmp-"
EXECUTION_RETENTION_DAYS = 90


class ExecutionStorageError(RuntimeError):
    pass


class ExecutionRecordCorrupt(ExecutionStorageError):
    pass


class DuplicateExecutionActive(ExecutionStorageError):
    def __init__(self, record: ExecutionRecord):
        super().__init__("execution task is already active")
        self.record = record


class BlindRedispatchProhibited(ExecutionStorageError):
    pass


class ExecutionClaimLost(ExecutionStorageError):
    pass


@dataclass(frozen=True)
class ExecutionClaim:
    record: ExecutionRecord
    claim_generation: int
    created: bool


FaultHook = Callable[[str], None]
T = TypeVar("T")


class DurableExecutionRepository:
    """Versioned attempt records with a durable one-dispatch boundary."""

    def __init__(
        self,
        root: str | Path,
        *,
        retention_days: int = EXECUTION_RETENTION_DAYS,
        metrics: ExecutorMetrics | None = None,
        event_sink: EventSink | None = None,
        fault_hook: FaultHook | None = None,
    ):
        if not 1 <= retention_days <= 365:
            raise ValueError("execution retention is invalid")
        self.root = Path(root) / EXECUTION_NAMESPACE
        self.transaction_path = self.root / EXECUTION_TRANSACTION_FILE
        self.retention_days = retention_days
        self.metrics = metrics or ExecutorMetrics()
        self.event_sink = event_sink or null_event_sink
        self._fault_hook = fault_hook
        self._thread_lock = threading.RLock()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.transaction_path.touch(exist_ok=True)
        except OSError as exc:
            raise ExecutionStorageError(
                "unable to initialize durable execution storage"
            ) from exc

    def _inject(self, stage: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(stage)

    @staticmethod
    def _record_name(task_id: str) -> str:
        validate_identifier(task_id, field_name="task_id")
        return hashlib.sha256(task_id.encode("utf-8")).hexdigest() + ".json"

    def _path(self, task_id: str) -> Path:
        return self.root / self._record_name(task_id)

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
        except ExecutionStorageError:
            raise
        except OSError as exc:
            raise ExecutionStorageError(
                "durable execution transaction failed"
            ) from exc

    def _read_unlocked(self, task_id: str) -> ExecutionRecord | None:
        path = self._path(task_id)
        try:
            self._inject("before_execution_read")
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ExecutionStorageError("execution record read failed") from exc
        except json.JSONDecodeError as exc:
            raise ExecutionRecordCorrupt("execution record is corrupt") from exc
        try:
            record = ExecutionRecord.from_dict(value)
        except (KeyError, TypeError, ValueError) as exc:
            raise ExecutionRecordCorrupt("execution record is corrupt") from exc
        if record.execution_identity().task_id != task_id:
            raise ExecutionRecordCorrupt("execution record identity is corrupt")
        return record

    def _write_unlocked(self, record: ExecutionRecord) -> None:
        record.validate()
        path = self._path(record.execution_identity().task_id)
        temporary = self.root / (
            f"{EXECUTION_TEMP_PREFIX}{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
        )
        payload = json.dumps(
            record.to_dict(), sort_keys=True, separators=(",", ":")
        )
        try:
            self._inject("before_execution_write")
            with open(temporary, "x", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self._inject("before_execution_replace")
            os.replace(temporary, path)
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            self._inject("after_execution_replace")
        except ExecutionStorageError:
            raise
        except OSError as exc:
            raise ExecutionStorageError(
                "atomic execution-record write failed"
            ) from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _prepared_identity(prepared: object) -> tuple[str, str, str, str, str]:
        try:
            adapter_model = str(getattr(prepared, "contract_model"))
            adapter_id = str(getattr(prepared, "adapter_id"))
            operation = str(getattr(prepared, "operation"))
            target = getattr(prepared, "target")
            target_type = str(getattr(target, "target_type"))
            target_id = str(getattr(target, "target_id"))
            prepared_hash = str(getattr(prepared, "prepared_operation_hash"))
        except AttributeError as exc:
            raise ExecutionStorageError("prepared operation identity is invalid") from exc
        if adapter_model != F3_ADAPTER_CONTRACT_MODEL:
            raise ExecutionStorageError("prepared operation model is invalid")
        validate_identifier(adapter_id, field_name="adapter_id")
        validate_identifier(operation, field_name="operation")
        validate_identifier(target_type, field_name="target_type")
        validate_identifier(target_id, field_name="target_id")
        validate_sha256(prepared_hash, field_name="prepared_operation_hash")
        return adapter_id, operation, target_type, target_id, prepared_hash

    @staticmethod
    def _require_same_operation(
        record: ExecutionRecord,
        identity: ExecutionIdentity,
        prepared: object,
    ) -> None:
        adapter_id, operation, target_type, target_id, prepared_hash = (
            DurableExecutionRepository._prepared_identity(prepared)
        )
        durable = record.execution_identity()
        if (
            durable.task_id != identity.task_id
            or durable.plan_id != identity.plan_id
            or durable.attempt_id != identity.attempt_id
            or record.adapter_id != adapter_id
            or record.operation != operation
            or record.target
            != {"target_type": target_type, "target_id": target_id}
            or record.prepared_operation_hash != prepared_hash
        ):
            raise ExecutionRecordCorrupt(
                "task identity was reused for a different prepared operation"
            )

    def claim(
        self,
        *,
        identity: ExecutionIdentity,
        prepared: object,
        timing: ExecutorTiming,
        now: datetime | None = None,
    ) -> ExecutionClaim:
        identity.validate()
        timing.validate()
        instant = now or utc_now()
        now_text = timestamp(instant)
        claim_expiry = timestamp(
            instant + timedelta(seconds=timing.claim_lease_seconds)
        )
        adapter_id, operation, target_type, target_id, prepared_hash = (
            self._prepared_identity(prepared)
        )
        with self._exclusive_transaction():
            record = self._read_unlocked(identity.task_id)
            if record is None:
                record = ExecutionRecord(
                    schema_version=EXECUTION_RECORD_SCHEMA_VERSION,
                    identity={
                        "task_id": identity.task_id,
                        "plan_id": identity.plan_id,
                        "attempt_id": identity.attempt_id,
                        "request_id": identity.request_id,
                        "owner_id": identity.owner_id,
                    },
                    adapter_model=F3_ADAPTER_CONTRACT_MODEL,
                    adapter_id=adapter_id,
                    operation=operation,
                    target={"target_type": target_type, "target_id": target_id},
                    prepared_operation_hash=prepared_hash,
                    state="planning",
                    normalized_outcome=None,
                    task_state="created",
                    terminal=False,
                    created_at=now_text,
                    updated_at=now_text,
                    claim_generation=1,
                    claim_expires_at=claim_expiry,
                )
                append_execution_event(
                    record, event_type="execution_started", occurred_at=now_text
                )
                self._write_unlocked(record)
                return ExecutionClaim(record, 1, True)
            self._require_same_operation(record, identity, prepared)
            if record.terminal:
                return ExecutionClaim(record, record.claim_generation, False)
            durable_identity = record.execution_identity()
            claim_active = (
                parse_timestamp(
                    record.claim_expires_at, field_name="claim_expires_at"
                )
                > instant
            )
            if claim_active and durable_identity.owner_id != identity.owner_id:
                raise DuplicateExecutionActive(record)
            if record.dispatch_intent is not None:
                # Claim transfer permits observation-only reconstruction.  It
                # never clears durable intent or the consumed dispatch count.
                record.identity["owner_id"] = identity.owner_id
                record.identity["request_id"] = identity.request_id
                record.claim_generation += 1
                record.claim_expires_at = claim_expiry
                append_execution_event(
                    record,
                    event_type="recovery_claimed",
                    occurred_at=now_text,
                    diagnostic_codes=("redispatch_prohibited",),
                )
                self._write_unlocked(record)
                return ExecutionClaim(record, record.claim_generation, False)
            record.identity["owner_id"] = identity.owner_id
            record.identity["request_id"] = identity.request_id
            record.claim_generation += 1
            record.claim_expires_at = claim_expiry
            record.state = "planning"
            record.task_state = "preflight"
            append_execution_event(
                record, event_type="execution_reclaimed", occurred_at=now_text
            )
            self._write_unlocked(record)
            return ExecutionClaim(record, record.claim_generation, False)

    @staticmethod
    def _require_claim(
        record: ExecutionRecord,
        *,
        owner_id: str,
        claim_generation: int,
    ) -> None:
        if (
            record.execution_identity().owner_id != owner_id
            or record.claim_generation != claim_generation
        ):
            raise ExecutionClaimLost("execution claim was fenced")

    def mutate_claimed(
        self,
        task_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        mutator: Callable[[ExecutionRecord], None],
    ) -> ExecutionRecord:
        with self._exclusive_transaction():
            record = self._read_unlocked(task_id)
            if record is None:
                raise ExecutionRecordCorrupt("execution record is missing")
            self._require_claim(
                record,
                owner_id=owner_id,
                claim_generation=claim_generation,
            )
            mutator(record)
            record.validate()
            self._write_unlocked(record)
            return record

    def record_locks(
        self,
        task_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        handle: LockHandle,
        now: datetime | None = None,
    ) -> ExecutionRecord:
        now_text = timestamp(now or utc_now())
        handle.validate()

        def update(record: ExecutionRecord) -> None:
            if record.dispatch_intent is not None:
                raise BlindRedispatchProhibited(
                    "locks cannot create a new dispatch after durable intent"
                )
            record.lock_tokens = [
                {
                    "key": token.key,
                    "generation": token.generation,
                    "mode": token.mode,
                    "owner_id": handle.owner.owner_id,
                }
                for token in handle.tokens
            ]
            record.state = "preflight"
            record.task_state = "preflight"
            append_execution_event(
                record, event_type="locks_acquired", occurred_at=now_text
            )

        return self.mutate_claimed(
            task_id,
            owner_id=owner_id,
            claim_generation=claim_generation,
            mutator=update,
        )

    def replace_recovery_locks(
        self,
        task_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        handle: LockHandle,
        now: datetime | None = None,
    ) -> ExecutionRecord:
        now_text = timestamp(now or utc_now())
        handle.validate()

        def update(record: ExecutionRecord) -> None:
            if record.dispatch_intent is None:
                raise ExecutionStorageError(
                    "recovery lock transfer requires durable intent"
                )
            record.lock_tokens = [
                {
                    "key": token.key,
                    "generation": token.generation,
                    "mode": token.mode,
                    "owner_id": handle.owner.owner_id,
                }
                for token in handle.tokens
            ]
            record.dispatch_intent["lock_tokens"] = [
                dict(item) for item in record.lock_tokens
            ]
            append_execution_event(
                record,
                event_type="recovery_locks_transferred",
                occurred_at=now_text,
                diagnostic_codes=("observation_only",),
            )

        return self.mutate_claimed(
            task_id,
            owner_id=owner_id,
            claim_generation=claim_generation,
            mutator=update,
        )

    def record_preflight(
        self,
        task_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        now: datetime | None = None,
    ) -> ExecutionRecord:
        now_text = timestamp(now or utc_now())

        def update(record: ExecutionRecord) -> None:
            if not record.lock_tokens or record.dispatch_intent is not None:
                raise ExecutionStorageError("preflight boundary is invalid")
            record.preflight_completed = True
            append_execution_event(
                record, event_type="preflight_completed", occurred_at=now_text
            )

        return self.mutate_claimed(
            task_id,
            owner_id=owner_id,
            claim_generation=claim_generation,
            mutator=update,
        )

    def commit_dispatch_intent(
        self,
        task_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        request_id: str,
        provider_operation: str,
        provider_arguments_hash: str,
        timing: ExecutorTiming,
        now: datetime | None = None,
    ) -> ExecutionRecord:
        validate_identifier(request_id, field_name="request_id")
        validate_identifier(provider_operation, field_name="provider_operation")
        validate_sha256(
            provider_arguments_hash, field_name="provider_arguments_hash"
        )
        timing.validate()
        instant = now or utc_now()
        now_text = timestamp(instant)
        deadline = timestamp(
            instant
            + timedelta(seconds=timing.post_dispatch_evidence_seconds)
        )
        with self._exclusive_transaction():
            record = self._read_unlocked(task_id)
            if record is None:
                raise ExecutionRecordCorrupt("execution record is missing")
            self._require_claim(
                record,
                owner_id=owner_id,
                claim_generation=claim_generation,
            )
            if record.terminal:
                raise ExecutionStorageError(
                    "terminal execution cannot commit dispatch intent"
                )
            if record.dispatch_intent is not None or record.dispatch_count:
                self.metrics.increment("blind_redispatch_preventions")
                raise BlindRedispatchProhibited(
                    "durable intent permanently prohibits another dispatch"
                )
            if not record.preflight_completed or not record.lock_tokens:
                raise ExecutionStorageError(
                    "durable intent requires preflight and held locks"
                )
            self._inject("before_durable_intent_persistence")
            record.dispatch_intent = {
                "committed_at": now_text,
                "evidence_deadline": deadline,
                "request_id": request_id,
                "provider_operation": provider_operation,
                "provider_arguments_hash": provider_arguments_hash,
                "lock_tokens": [dict(item) for item in record.lock_tokens],
                "possibly_dispatched": True,
            }
            # Reserving the only invocation at intent commit is the durable
            # no-blind-redispatch fence.  A crash before network I/O still
            # consumes the attempt and recovery is observation-only.
            record.dispatch_count = 1
            record.state = "dispatching"
            record.task_state = "dispatching"
            append_execution_event(
                record,
                event_type="dispatch_intent_committed",
                occurred_at=now_text,
                diagnostic_codes=("possibly_dispatched",),
            )
            self._write_unlocked(record)
            self._inject("after_durable_intent_persistence")
            return record

    def record_dispatch_result(
        self,
        task_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        outcome: str,
        provider_response_received: bool,
        diagnostic_codes: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> ExecutionRecord:
        if outcome not in {
            "dispatch_failed_confirmed",
            "dispatch_indeterminate",
            "observing",
        }:
            raise ValueError("dispatch outcome is invalid")
        now_text = timestamp(now or utc_now())

        def update(record: ExecutionRecord) -> None:
            if record.dispatch_intent is None or record.dispatch_count != 1:
                raise ExecutionStorageError("dispatch result has no durable intent")
            record.normalized_outcome = outcome
            record.task_state = NORMALIZED_OUTCOME_TO_TASK_STATE[outcome]
            record.state = (
                "terminal" if outcome == "dispatch_failed_confirmed" else "observation"
            )
            record.terminal = outcome == "dispatch_failed_confirmed"
            record.provider_response_received = provider_response_received
            append_execution_event(
                record,
                event_type="dispatch_result_recorded",
                occurred_at=now_text,
                diagnostic_codes=bounded_diagnostics(diagnostic_codes),
            )

        return self.mutate_claimed(
            task_id,
            owner_id=owner_id,
            claim_generation=claim_generation,
            mutator=update,
        )

    def record_observation(
        self,
        task_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        outcome: str,
        diagnostic_codes: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> ExecutionRecord:
        if outcome not in {"observing", "verification_mismatch", "manual_review_required"}:
            raise ValueError("observation outcome is invalid")
        now_text = timestamp(now or utc_now())

        def update(record: ExecutionRecord) -> None:
            if record.dispatch_intent is None:
                raise ExecutionStorageError("post-dispatch observation lacks intent")
            record.observation_attempts += 1
            record.normalized_outcome = outcome
            record.task_state = NORMALIZED_OUTCOME_TO_TASK_STATE[outcome]
            record.state = "observation"
            append_execution_event(
                record,
                event_type="observation_recorded",
                occurred_at=now_text,
                diagnostic_codes=bounded_diagnostics(diagnostic_codes),
            )

        return self.mutate_claimed(
            task_id,
            owner_id=owner_id,
            claim_generation=claim_generation,
            mutator=update,
        )

    def record_verification(
        self,
        task_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        outcome: str,
        terminal: bool,
        diagnostic_codes: tuple[str, ...] = (),
        evidence: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ExecutionRecord:
        if outcome not in {
            "observing",
            "verification_mismatch",
            "succeeded_verified",
            "failed_post_dispatch",
            "manual_review_required",
        }:
            raise ValueError("verification outcome is invalid")
        if terminal != (outcome != "observing"):
            raise ValueError("verification terminality is invalid")
        now_text = timestamp(now or utc_now())

        def update(record: ExecutionRecord) -> None:
            if record.dispatch_intent is None:
                raise ExecutionStorageError("verification lacks durable intent")
            record.verification_attempts += 1
            record.normalized_outcome = outcome
            record.task_state = NORMALIZED_OUTCOME_TO_TASK_STATE[outcome]
            record.state = "terminal" if terminal else "observation"
            record.terminal = terminal
            record.evidence = self._bounded_evidence(evidence or {})
            append_execution_event(
                record,
                event_type="verification_recorded",
                occurred_at=now_text,
                diagnostic_codes=bounded_diagnostics(diagnostic_codes),
            )

        return self.mutate_claimed(
            task_id,
            owner_id=owner_id,
            claim_generation=claim_generation,
            mutator=update,
        )

    def terminalize_pre_dispatch(
        self,
        task_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        outcome: str,
        diagnostic_codes: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> ExecutionRecord:
        if outcome not in {
            "preflight_rejected",
            "lock_conflict",
            "provider_unavailable_pre_dispatch",
            "failed_pre_dispatch",
            "cancelled_pre_dispatch",
        }:
            raise ValueError("pre-dispatch outcome is invalid")
        now_text = timestamp(now or utc_now())

        def update(record: ExecutionRecord) -> None:
            if record.dispatch_intent is not None:
                raise BlindRedispatchProhibited(
                    "post-intent work cannot become pre-dispatch"
                )
            record.normalized_outcome = outcome
            record.task_state = NORMALIZED_OUTCOME_TO_TASK_STATE[outcome]
            record.state = "terminal"
            record.terminal = True
            append_execution_event(
                record,
                event_type="pre_dispatch_terminal",
                occurred_at=now_text,
                diagnostic_codes=bounded_diagnostics(diagnostic_codes),
            )

        return self.mutate_claimed(
            task_id,
            owner_id=owner_id,
            claim_generation=claim_generation,
            mutator=update,
        )

    def cancel(self, task_id: str, *, now: datetime | None = None) -> bool:
        now_text = timestamp(now or utc_now())
        with self._exclusive_transaction():
            record = self._read_unlocked(task_id)
            if record is None:
                return False
            if record.dispatch_intent is not None:
                append_execution_event(
                    record,
                    event_type="cancellation_rejected",
                    occurred_at=now_text,
                    diagnostic_codes=("dispatch_intent_exists",),
                )
                self._write_unlocked(record)
                return False
            if record.terminal:
                return record.normalized_outcome == "cancelled_pre_dispatch"
            record.normalized_outcome = "cancelled_pre_dispatch"
            record.task_state = "cancelled_pre_dispatch"
            record.state = "terminal"
            record.terminal = True
            append_execution_event(
                record, event_type="execution_cancelled", occurred_at=now_text
            )
            self._write_unlocked(record)
            self.metrics.increment("cancellations")
            return True

    @staticmethod
    def _bounded_evidence(value: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "evidence_hash",
            "resulting_state_fingerprint",
            "mismatch_fields",
            "manual_review_reason_code",
        }
        if set(value) - allowed:
            raise ValueError("execution evidence fields are not bounded")
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"evidence_hash", "resulting_state_fingerprint"}:
                result[key] = validate_sha256(item, field_name=key)
            elif key == "mismatch_fields":
                if not isinstance(item, (list, tuple)):
                    raise ValueError("mismatch fields are invalid")
                result[key] = list(bounded_diagnostics(item))
            else:
                result[key] = bounded_diagnostics((item,))[0]
        return result

    def get(self, task_id: str) -> ExecutionRecord | None:
        with self._exclusive_transaction():
            return self._read_unlocked(task_id)

    def list(self) -> tuple[ExecutionRecord, ...]:
        with self._exclusive_transaction():
            records: list[ExecutionRecord] = []
            for path in sorted(self.root.glob("*.json")):
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    records.append(ExecutionRecord.from_dict(value))
                except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                    raise ExecutionRecordCorrupt(
                        "execution namespace contains a corrupt record"
                    ) from exc
            identifiers = [item.execution_identity().task_id for item in records]
            if len(identifiers) != len(set(identifiers)):
                raise ExecutionRecordCorrupt("execution task identity is duplicated")
            return tuple(sorted(records, key=lambda item: item.created_at))

    def cleanup(self, *, now: datetime | None = None) -> int:
        instant = now or utc_now()
        cutoff = instant - timedelta(days=self.retention_days)
        removed = 0
        with self._exclusive_transaction():
            for path in sorted(self.root.glob("*.json")):
                try:
                    record = ExecutionRecord.from_dict(
                        json.loads(path.read_text(encoding="utf-8"))
                    )
                    updated = parse_timestamp(record.updated_at, field_name="updated_at")
                    if record.terminal and updated < cutoff:
                        path.unlink()
                        removed += 1
                except FileNotFoundError:
                    continue
                except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                    raise ExecutionRecordCorrupt(
                        "execution cleanup encountered a corrupt record"
                    ) from exc
        return removed
