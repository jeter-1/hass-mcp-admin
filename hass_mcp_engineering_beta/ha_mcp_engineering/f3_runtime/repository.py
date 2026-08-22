"""Canonical Beta 20 child-execution authority and initialization journal."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Iterable

from ..f3.models import (
    ExecutionRecord,
    LockToken,
    parse_timestamp,
    validate_identifier,
    validate_sha256,
)
from ..f3.persistence import (
    DurableExecutionRepository,
    ExecutionRecordCorrupt,
    ExecutionStorageError,
)
from ..governance.task_models import ExecutionTask
from ..governance.task_storage import ExecutionTaskRepository
from ..logging_config import get_logger, log_event


CHILD_EXECUTION_MODEL = "f3-child-execution-v1"
CHILD_EXECUTION_SCHEMA_VERSION = 1
MAX_F3_PUBLIC_TASKS = 1_024
MAX_F3_CHILD_EXECUTIONS = MAX_F3_PUBLIC_TASKS * 8
CHILD_EXECUTION_NAMESPACE = "f3-child-execution-v1"
INITIALIZATION_MODEL = "f3-sequence-initialization-v1"
RECOVERY_CURSOR_MODEL = "f3-recovery-declaration-cursor-v1"
RECOVERY_CURSOR_SCHEMA_VERSION = 1
RECOVERY_CURSOR_FILE = ".recovery-declaration-cursor.json"
ACTIVE_RECOVERY_CURSOR_MODEL = "f3-active-recovery-cursor-v1"
ACTIVE_RECOVERY_CURSOR_SCHEMA_VERSION = 1
ACTIVE_RECOVERY_CURSOR_FILE = ".active-recovery-cursor.json"
ACTIVE_RECOVERY_CHECKPOINT_MODEL = "f3-active-recovery-checkpoint-v1"
ACTIVE_RECOVERY_CHECKPOINT_SCHEMA_VERSION = 1
ACTIVE_RECOVERY_CHECKPOINT_FILE = ".active-recovery-checkpoint.json"
ACTIVE_RECOVERY_CHECKPOINT_LIMIT = 16
RECOVERY_DECLARATION_PAGE_SIZE = 1_024


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def deterministic_child_id(
    public_task_id: str,
    plan_id: str,
    operation_id: str,
    ordinal: int,
) -> str:
    validate_identifier(public_task_id, field_name="public_task_id")
    validate_identifier(plan_id, field_name="plan_id")
    validate_identifier(operation_id, field_name="operation_id")
    if type(ordinal) is not int or not 0 <= ordinal < 32:
        raise ValueError("child ordinal is invalid")
    return hashlib.sha256(
        f"{public_task_id}\0{plan_id}\0{operation_id}\0{ordinal}".encode()
    ).hexdigest()


def child_declaration(
    *,
    public_task_id: str,
    plan_id: str,
    plan_hash: str,
    plan_contract_version: int,
    operation_id: str,
    ordinal: int,
    dependency_ids: Iterable[str],
    adapter_id: str,
    capability_id: str,
    prepared_operation_hash: str,
    target_type: str,
    target_id: str,
    attempt_id: str,
    request_id: str,
    idempotency_key: str,
    complete_lock_request_hash: str,
    approval_bundle_hash: str,
    selective_hold_keys: Iterable[str],
    provider_dependency_key: str | None = None,
    provider_identity_evidence_hash: str | None = None,
) -> dict[str, Any]:
    declaration = {
        "schema_model": CHILD_EXECUTION_MODEL,
        "schema_version": CHILD_EXECUTION_SCHEMA_VERSION,
        "child_id": deterministic_child_id(
            public_task_id, plan_id, operation_id, ordinal
        ),
        "public_task_id": public_task_id,
        "plan_id": plan_id,
        "plan_hash": plan_hash,
        "plan_contract_version": plan_contract_version,
        "operation_id": operation_id,
        "operation_ordinal": ordinal,
        "operation_dependency_ids": list(dependency_ids),
        "adapter_id": adapter_id,
        "capability_id": capability_id,
        "prepared_operation_hash": prepared_operation_hash,
        "target_type": target_type,
        "target_id": target_id,
        "attempt_id": attempt_id,
        "request_id": request_id,
        "idempotency_key": idempotency_key,
        "complete_lock_request_hash": complete_lock_request_hash,
        "approval_bundle_hash": approval_bundle_hash,
        "selective_hold_keys": sorted(set(selective_hold_keys)),
        "provider_dependency_key": provider_dependency_key,
        "provider_identity_evidence_hash": provider_identity_evidence_hash,
    }
    _validate_declaration(declaration)
    declaration["declaration_hash"] = canonical_hash(declaration)
    return declaration


def _validate_declaration(value: dict[str, Any]) -> None:
    expected = {
        "schema_model", "schema_version", "child_id", "public_task_id",
        "plan_id", "plan_hash", "plan_contract_version", "operation_id",
        "operation_ordinal", "operation_dependency_ids", "adapter_id",
        "capability_id", "prepared_operation_hash", "target_type", "target_id",
        "attempt_id", "request_id", "idempotency_key",
        "complete_lock_request_hash", "approval_bundle_hash",
        "selective_hold_keys",
        "provider_dependency_key", "provider_identity_evidence_hash",
    }
    if set(value) not in (expected, expected | {"declaration_hash"}):
        raise ValueError("child declaration fields are invalid")
    if (
        value["schema_model"] != CHILD_EXECUTION_MODEL
        or value["schema_version"] != CHILD_EXECUTION_SCHEMA_VERSION
        or type(value["plan_contract_version"]) is not int
        or type(value["operation_ordinal"]) is not int
    ):
        raise ValueError("child declaration schema is invalid")
    for name in (
        "child_id", "public_task_id", "plan_id", "operation_id", "adapter_id",
        "capability_id", "target_type", "target_id", "attempt_id", "request_id",
        "idempotency_key",
    ):
        validate_identifier(value[name], field_name=name)
    for name in (
        "plan_hash", "prepared_operation_hash", "complete_lock_request_hash",
        "approval_bundle_hash",
    ):
        validate_sha256(value[name], field_name=name)
    provider_key = value["provider_dependency_key"]
    provider_hash = value["provider_identity_evidence_hash"]
    if (provider_key is None) != (provider_hash is None):
        raise ValueError("provider identity evidence is incomplete")
    if provider_key is not None:
        from ..f3.models import validate_lock_key

        validate_lock_key(provider_key)
        validate_sha256(provider_hash, field_name="provider_identity_evidence_hash")
    dependencies = value["operation_dependency_ids"]
    holds = value["selective_hold_keys"]
    if (
        not isinstance(dependencies, list)
        or len(dependencies) != len(set(dependencies))
        or any(not isinstance(item, str) for item in dependencies)
        or not isinstance(holds, list)
        or not holds
        or holds != sorted(set(holds))
        or any(not isinstance(item, str) for item in holds)
    ):
        raise ValueError("child declaration relationships are invalid")
    if "declaration_hash" in value:
        base = dict(value)
        digest = base.pop("declaration_hash")
        validate_sha256(digest, field_name="declaration_hash")
        if digest != canonical_hash(base):
            raise ValueError("child declaration hash is invalid")


class ChildExecutionRepository(DurableExecutionRepository):
    """F3-A lifecycle persistence embedded in one versioned child envelope."""

    def __init__(self, root: str | Path, **kwargs: Any):
        retention_days = int(kwargs.pop("retention_days", 90))
        metrics = kwargs.pop("metrics", None)
        event_sink = kwargs.pop("event_sink", None)
        fault_hook = kwargs.pop("fault_hook", None)
        if kwargs or not 1 <= retention_days <= 365:
            raise ValueError("child repository options are invalid")
        from ..f3.observability import ExecutorMetrics, null_event_sink

        self.root = Path(root) / CHILD_EXECUTION_NAMESPACE
        self.transaction_path = self.root / ".transaction.lock"
        self.projection_transaction_path = self.root / ".projection-transaction.lock"
        self.initialization_path = self.root / ".initialization.json"
        self.recovery_cursor_path = self.root / RECOVERY_CURSOR_FILE
        self.active_recovery_cursor_path = (
            self.root / ACTIVE_RECOVERY_CURSOR_FILE
        )
        self.active_recovery_checkpoint_path = (
            self.root / ACTIVE_RECOVERY_CHECKPOINT_FILE
        )
        self.retention_days = retention_days
        self.metrics = metrics or ExecutorMetrics()
        self.event_sink = event_sink or null_event_sink
        self._fault_hook = fault_hook
        self._thread_lock = threading.RLock()
        self._navigation_logger = get_logger("f3_recovery_navigation")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.transaction_path.touch(exist_ok=True)
            self.projection_transaction_path.touch(exist_ok=True)
        except OSError as exc:
            raise ExecutionStorageError(
                "unable to initialize child execution storage"
            ) from exc

    @staticmethod
    def _record_name(task_id: str) -> str:
        validate_identifier(task_id, field_name="child_id")
        return hashlib.sha256(task_id.encode()).hexdigest() + ".child.json"

    def _path(self, task_id: str) -> Path:
        return self.root / self._record_name(task_id)

    def _bounded_paths(self, pattern: str, limit: int) -> tuple[Path, ...]:
        values: list[Path] = []
        for path in self.root.glob(pattern):
            values.append(path)
            if len(values) > limit:
                raise ExecutionStorageError(
                    "F3 execution namespace exceeds its reviewed bound"
                )
        return tuple(sorted(values))

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
            raise ExecutionStorageError("child transaction failed") from exc

    @contextmanager
    def public_projection_transaction(self):
        """Serialize schema-1 compatibility projection across processes.

        Provider dispatch remains owned exclusively by the F3 child claim.  The
        separate lock only protects append-only public task and plan projection,
        so a crashed projector can be retried without creating dispatch authority.
        """

        try:
            with self._thread_lock:
                with open(self.projection_transaction_path, "a+b") as handle:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ExecutionStorageError:
            raise
        except OSError as exc:
            raise ExecutionStorageError(
                "public F3 projection transaction failed"
            ) from exc

    def _raw_envelope(self, child_id: str) -> dict[str, Any] | None:
        try:
            value = json.loads(self._path(child_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise ExecutionRecordCorrupt("child record is corrupt") from exc
        try:
            if set(value) != {"declaration", "execution", "runtime"}:
                raise ValueError
            _validate_declaration(value["declaration"])
            if value["declaration"]["child_id"] != child_id:
                raise ValueError
            runtime = value["runtime"]
            if set(runtime) != {
                "approval_consumption_reference", "next_eligible_at",
                "backoff_seconds", "selective_hold_tokens", "record_generation",
                "last_reconciliation_at", "reconciliation_result",
                "audited_event_count",
                "selective_hold_promoted_at", "selective_hold_reason",
                "last_readback_summary",
                "hold_release_authority",
                "operation_evidence",
            }:
                raise ValueError
            if (
                runtime["approval_consumption_reference"] is not None
                and not isinstance(runtime["approval_consumption_reference"], dict)
            ) or (
                runtime["next_eligible_at"] is not None
                and not isinstance(runtime["next_eligible_at"], str)
            ) or (
                type(runtime["backoff_seconds"]) is not int
                or not 0 <= runtime["backoff_seconds"] <= 300
            ) or (
                not isinstance(runtime["selective_hold_tokens"], list)
                or len(runtime["selective_hold_tokens"]) > 16
            ) or (
                type(runtime["record_generation"]) is not int
                or runtime["record_generation"] < 1
            ) or (
                type(runtime["audited_event_count"]) is not int
                or runtime["audited_event_count"] < 0
            ) or (
                runtime["selective_hold_promoted_at"] is not None
                and not isinstance(runtime["selective_hold_promoted_at"], str)
            ) or (
                runtime["selective_hold_reason"] is not None
                and not isinstance(runtime["selective_hold_reason"], str)
            ) or (
                runtime["last_readback_summary"] is not None
                and not isinstance(runtime["last_readback_summary"], dict)
            ) or (
                runtime["hold_release_authority"] is not None
                and not isinstance(runtime["hold_release_authority"], dict)
            ) or (
                not isinstance(runtime["operation_evidence"], dict)
            ):
                raise ValueError
            for item in runtime["selective_hold_tokens"]:
                if not isinstance(item, dict) or set(item) != {
                    "key", "generation", "mode"
                }:
                    raise ValueError
                LockToken(item["key"], item["generation"], item["mode"]).validate()
            for name in (
                "next_eligible_at", "last_reconciliation_at",
                "selective_hold_promoted_at",
            ):
                if runtime[name] is not None:
                    parse_timestamp(runtime[name], field_name=name)
            if runtime["selective_hold_reason"] is not None:
                validate_identifier(
                    runtime["selective_hold_reason"],
                    field_name="selective_hold_reason",
                )
            readback = runtime["last_readback_summary"]
            if readback is not None:
                if set(readback) != {
                    "status", "verified", "observed_hash", "checked_at"
                } or not isinstance(readback["status"], str) or type(
                    readback["verified"]
                ) is not bool:
                    raise ValueError
                validate_identifier(readback["status"], field_name="readback_status")
                if readback["observed_hash"] is not None:
                    validate_sha256(readback["observed_hash"], field_name="observed_hash")
                parse_timestamp(readback["checked_at"], field_name="checked_at")
            release = runtime["hold_release_authority"]
            if release is not None:
                if set(release) != {
                    "authority_hash", "authorized_at", "reason_code",
                    "evidence_hash", "tokens",
                }:
                    raise ValueError
                validate_sha256(release["authority_hash"], field_name="authority_hash")
                validate_sha256(release["evidence_hash"], field_name="evidence_hash")
                validate_identifier(release["reason_code"], field_name="reason_code")
                parse_timestamp(release["authorized_at"], field_name="authorized_at")
                if release["tokens"] != runtime["selective_hold_tokens"]:
                    raise ValueError
            operation_evidence = runtime["operation_evidence"]
            if set(operation_evidence) != {
                "provider_operation_id", "provider_backup_id",
                "outage_observed", "reconnect_observed",
                "provider_readmission_observed", "evidence_hash",
            } or any(
                type(operation_evidence[name]) is not bool
                for name in (
                    "outage_observed", "reconnect_observed",
                    "provider_readmission_observed",
                )
            ):
                raise ValueError
            for name in ("provider_operation_id", "provider_backup_id"):
                if operation_evidence[name] is not None:
                    validate_identifier(operation_evidence[name], field_name=name)
            if operation_evidence["evidence_hash"] is not None:
                validate_sha256(
                    operation_evidence["evidence_hash"],
                    field_name="operation_evidence_hash",
                )
        except (KeyError, TypeError, ValueError):
            raise ExecutionRecordCorrupt("child record is corrupt") from None
        return value

    def declaration(self, child_id: str) -> dict[str, Any]:
        with self._exclusive_transaction():
            envelope = self._raw_envelope(child_id)
            if envelope is None:
                raise ExecutionRecordCorrupt("child declaration is missing")
            return dict(envelope["declaration"])

    def _read_unlocked(self, task_id: str) -> ExecutionRecord | None:
        envelope = self._raw_envelope(task_id)
        if envelope is None or envelope["execution"] is None:
            return None
        try:
            record = ExecutionRecord.from_dict(envelope["execution"])
            if (
                record.execution_identity().task_id != task_id
                or record.prepared_operation_hash
                != envelope["declaration"]["prepared_operation_hash"]
            ):
                raise ValueError
            return record
        except (KeyError, TypeError, ValueError):
            raise ExecutionRecordCorrupt("child execution is corrupt") from None

    def _write_unlocked(self, record: ExecutionRecord) -> None:
        record.validate()
        child_id = record.execution_identity().task_id
        envelope = self._raw_envelope(child_id)
        if envelope is None:
            raise ExecutionStorageError("child declaration must precede execution")
        if record.prepared_operation_hash != envelope["declaration"]["prepared_operation_hash"]:
            raise ExecutionRecordCorrupt("prepared child authority changed")
        runtime = dict(envelope["runtime"])
        runtime["record_generation"] = int(runtime["record_generation"]) + 1
        self._atomic_write(
            self._path(child_id),
            {"declaration": envelope["declaration"], "execution": record.to_dict(), "runtime": runtime},
        )

    def _atomic_write(self, path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_name(
            f".child.tmp-{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
        )
        try:
            with open(temporary, "x", encoding="utf-8") as handle:
                handle.write(json.dumps(value, sort_keys=True, separators=(",", ":")))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise ExecutionStorageError("atomic child write failed") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _new_envelope(declaration: dict[str, Any]) -> dict[str, Any]:
        _validate_declaration(declaration)
        return {
            "declaration": declaration,
            "execution": None,
            "runtime": {
                "approval_consumption_reference": None,
                "next_eligible_at": None,
                "backoff_seconds": 0,
                "selective_hold_tokens": [],
                "record_generation": 1,
                "audited_event_count": 0,
                "selective_hold_promoted_at": None,
                "selective_hold_reason": None,
                "last_readback_summary": None,
                "hold_release_authority": None,
                "operation_evidence": {
                    "provider_operation_id": None,
                    "provider_backup_id": None,
                    "outage_observed": False,
                    "reconnect_observed": False,
                    "provider_readmission_observed": False,
                    "evidence_hash": None,
                },
                "last_reconciliation_at": None,
                "reconciliation_result": None,
            },
        }

    def initialize_task_sequence(
        self,
        *,
        task: ExecutionTask,
        task_repository: ExecutionTaskRepository,
        declarations: Iterable[dict[str, Any]],
        sequence_hash: str,
    ) -> None:
        validate_sha256(sequence_hash, field_name="sequence_hash")
        ordered = tuple(declarations)
        if not 1 <= len(ordered) <= 8 or any(
            item["public_task_id"] != task.task_id for item in ordered
        ):
            raise ExecutionStorageError("F3 sequence identity is invalid")
        manifest = {
            "model": INITIALIZATION_MODEL,
            "public_task": task.to_dict(),
            "sequence_hash": sequence_hash,
            "declarations": list(ordered),
        }
        manifest["manifest_hash"] = canonical_hash(manifest)
        with self._exclusive_transaction():
            existing_task = task_repository.get_for_plan(task.plan_id)
            if existing_task is not None and existing_task.task_id != task.task_id:
                raise ExecutionStorageError(
                    "public execution authority is already owned"
                )
            existing = self.manifest_for_task(task.task_id, locked=True)
            if existing is not None:
                if existing["manifest_hash"] != manifest["manifest_hash"]:
                    raise ExecutionRecordCorrupt("F3 sequence authority changed")
                return
            self._bounded_paths("*.manifest.json", MAX_F3_PUBLIC_TASKS - 1)
            self._atomic_write(self.initialization_path, manifest)
            self._finish_initialization(manifest, task_repository)

    def _finish_initialization(
        self, manifest: dict[str, Any], task_repository: ExecutionTaskRepository
    ) -> None:
        try:
            base = dict(manifest)
            digest = base.pop("manifest_hash")
            if digest != canonical_hash(base) or manifest["model"] != INITIALIZATION_MODEL:
                raise ValueError
            task = ExecutionTask.from_dict(manifest["public_task"])
            declarations = tuple(manifest["declarations"])
            for declaration in declarations:
                _validate_declaration(declaration)
                if declaration["public_task_id"] != task.task_id:
                    raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ExecutionRecordCorrupt("F3 initialization journal is corrupt") from None
        task_repository.save(task)
        for declaration in declarations:
            existing = self._raw_envelope(declaration["child_id"])
            if existing is None:
                self._atomic_write(
                    self._path(declaration["child_id"]), self._new_envelope(declaration)
                )
            elif existing["declaration"] != declaration:
                raise ExecutionRecordCorrupt("F3 child declaration changed")
        self._atomic_write(self.root / f"{task.task_id}.manifest.json", manifest)
        try:
            self.initialization_path.unlink(missing_ok=True)
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise ExecutionStorageError("F3 initialization finalization failed") from exc

    def recover_initialization(self, task_repository: ExecutionTaskRepository) -> bool:
        with self._exclusive_transaction():
            try:
                manifest = json.loads(self.initialization_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return False
            except (OSError, json.JSONDecodeError) as exc:
                raise ExecutionRecordCorrupt("F3 initialization journal is corrupt") from exc
            self._finish_initialization(manifest, task_repository)
            return True

    def manifest_for_task(
        self, public_task_id: str, *, locked: bool = False
    ) -> dict[str, Any] | None:
        def read() -> dict[str, Any] | None:
            try:
                value = json.loads(
                    (self.root / f"{public_task_id}.manifest.json").read_text(encoding="utf-8")
                )
            except FileNotFoundError:
                return None
            except (OSError, json.JSONDecodeError) as exc:
                raise ExecutionRecordCorrupt("F3 manifest is corrupt") from exc
            base = dict(value)
            digest = base.pop("manifest_hash", None)
            if digest != canonical_hash(base):
                raise ExecutionRecordCorrupt("F3 manifest hash is invalid")
            return value
        if locked:
            return read()
        with self._exclusive_transaction():
            return read()

    def list(self) -> tuple[ExecutionRecord, ...]:
        with self._exclusive_transaction():
            records = []
            for path in self._bounded_paths(
                "*.child.json", MAX_F3_CHILD_EXECUTIONS
            ):
                try:
                    envelope = json.loads(path.read_text(encoding="utf-8"))
                    record = self._read_unlocked(envelope["declaration"]["child_id"])
                    if record is not None:
                        records.append(record)
                except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                    raise ExecutionRecordCorrupt("child namespace is corrupt") from None
            identities = [item.execution_identity().task_id for item in records]
            if len(identities) != len(set(identities)):
                raise ExecutionRecordCorrupt("child identity is duplicated")
            return tuple(sorted(records, key=lambda item: item.created_at))

    def declarations_for_task(self, public_task_id: str) -> tuple[dict[str, Any], ...]:
        manifest = self.manifest_for_task(public_task_id)
        return () if manifest is None else tuple(dict(item) for item in manifest["declarations"])

    def all_declarations(self) -> tuple[dict[str, Any], ...]:
        """Return every authority declaration, including pre-claim children."""

        with self._exclusive_transaction():
            values: list[dict[str, Any]] = []
            for path in self._bounded_paths(
                "*.manifest.json", MAX_F3_PUBLIC_TASKS
            ):
                public_task_id = path.name.removesuffix(".manifest.json")
                manifest = self.manifest_for_task(public_task_id, locked=True)
                if manifest is not None:
                    values.extend(dict(item) for item in manifest["declarations"])
            return tuple(
                sorted(
                    values,
                    key=lambda item: (
                        item["public_task_id"], item["operation_ordinal"]
                    ),
                )
            )

    @staticmethod
    def _validate_recovery_cursor(
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        expected = {
            "model",
            "schema_version",
            "public_task_id",
            "operation_ordinal",
            "child_id",
        }
        if (
            not isinstance(value, dict)
            or set(value) != expected
            or value["model"] != RECOVERY_CURSOR_MODEL
            or value["schema_version"] != RECOVERY_CURSOR_SCHEMA_VERSION
            or type(value["operation_ordinal"]) is not int
            or not 0 <= value["operation_ordinal"] < 8
        ):
            raise ExecutionRecordCorrupt(
                "F3 recovery declaration cursor is corrupt"
            )
        try:
            validate_identifier(
                value["public_task_id"], field_name="public_task_id"
            )
            validate_identifier(value["child_id"], field_name="child_id")
        except (TypeError, ValueError) as exc:
            raise ExecutionRecordCorrupt(
                "F3 recovery declaration cursor is corrupt"
            ) from exc
        return dict(value)

    def _reset_corrupt_navigation_unlocked(
        self, path: Path, *, navigation_kind: str
    ) -> None:
        """Discard only non-authoritative recovery navigation evidence."""

        try:
            path.unlink(missing_ok=True)
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise ExecutionStorageError(
                "corrupt F3 recovery navigation could not be reset"
            ) from exc
        log_event(
            self._navigation_logger,
            logging.WARNING,
            "f3_recovery_navigation_reset",
            "Corrupt non-authoritative F3 recovery navigation was reset.",
            context={"navigation_kind": navigation_kind},
        )

    def _read_recovery_cursor_unlocked(self) -> dict[str, Any] | None:
        try:
            value = json.loads(
                self.recovery_cursor_path.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, UnicodeError):
            self._reset_corrupt_navigation_unlocked(
                self.recovery_cursor_path,
                navigation_kind="declaration_cursor",
            )
            return None
        except OSError as exc:
            raise ExecutionStorageError(
                "F3 recovery declaration cursor read failed"
            ) from exc
        try:
            return self._validate_recovery_cursor(value)
        except ExecutionRecordCorrupt:
            self._reset_corrupt_navigation_unlocked(
                self.recovery_cursor_path,
                navigation_kind="declaration_cursor",
            )
            return None

    def recovery_cursor(self) -> dict[str, Any] | None:
        with self._exclusive_transaction():
            return self._read_recovery_cursor_unlocked()

    @staticmethod
    def _validate_active_recovery_cursor(
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        if (
            not isinstance(value, dict)
            or set(value) != {
                "model",
                "schema_version",
                "public_task_id",
            }
            or value["model"] != ACTIVE_RECOVERY_CURSOR_MODEL
            or value["schema_version"]
            != ACTIVE_RECOVERY_CURSOR_SCHEMA_VERSION
        ):
            raise ExecutionRecordCorrupt(
                "F3 active recovery cursor is corrupt"
            )
        try:
            validate_identifier(
                value["public_task_id"], field_name="public_task_id"
            )
        except (TypeError, ValueError) as exc:
            raise ExecutionRecordCorrupt(
                "F3 active recovery cursor is corrupt"
            ) from exc
        return dict(value)

    def _read_active_recovery_cursor_unlocked(
        self,
    ) -> dict[str, Any] | None:
        try:
            value = json.loads(
                self.active_recovery_cursor_path.read_text(
                    encoding="utf-8"
                )
            )
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, UnicodeError):
            self._reset_corrupt_navigation_unlocked(
                self.active_recovery_cursor_path,
                navigation_kind="active_cursor",
            )
            return None
        except OSError as exc:
            raise ExecutionStorageError(
                "F3 active recovery cursor read failed"
            ) from exc
        try:
            return self._validate_active_recovery_cursor(value)
        except ExecutionRecordCorrupt:
            self._reset_corrupt_navigation_unlocked(
                self.active_recovery_cursor_path,
                navigation_kind="active_cursor",
            )
            return None

    def active_recovery_cursor(self) -> dict[str, Any] | None:
        with self._exclusive_transaction():
            return self._read_active_recovery_cursor_unlocked()

    @staticmethod
    def _validate_active_recovery_checkpoint(
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        if (
            not isinstance(value, dict)
            or set(value) != {"model", "schema_version", "candidates"}
            or value["model"] != ACTIVE_RECOVERY_CHECKPOINT_MODEL
            or value["schema_version"]
            != ACTIVE_RECOVERY_CHECKPOINT_SCHEMA_VERSION
            or not isinstance(value["candidates"], list)
            or not 1
            <= len(value["candidates"])
            <= ACTIVE_RECOVERY_CHECKPOINT_LIMIT
        ):
            raise ExecutionRecordCorrupt(
                "F3 active recovery checkpoint is corrupt"
            )
        candidates: list[dict[str, Any]] = []
        child_ids: set[str] = set()
        for item in value["candidates"]:
            if not isinstance(item, dict) or set(item) != {
                "public_task_id",
                "child_id",
                "operation_id",
                "operation_ordinal",
                "attempt_id",
                "declaration_hash",
            }:
                raise ExecutionRecordCorrupt(
                    "F3 active recovery checkpoint is corrupt"
                )
            try:
                for name in (
                    "public_task_id",
                    "child_id",
                    "operation_id",
                    "attempt_id",
                ):
                    validate_identifier(item[name], field_name=name)
                validate_sha256(
                    item["declaration_hash"],
                    field_name="declaration_hash",
                )
                ordinal = item["operation_ordinal"]
                if type(ordinal) is not int or not 0 <= ordinal < 8:
                    raise ValueError
                if item["child_id"] in child_ids:
                    raise ValueError
            except (KeyError, TypeError, ValueError) as exc:
                raise ExecutionRecordCorrupt(
                    "F3 active recovery checkpoint is corrupt"
                ) from exc
            child_ids.add(item["child_id"])
            candidates.append(dict(item))
        return {
            "model": ACTIVE_RECOVERY_CHECKPOINT_MODEL,
            "schema_version": ACTIVE_RECOVERY_CHECKPOINT_SCHEMA_VERSION,
            "candidates": candidates,
        }

    def _read_active_recovery_checkpoint_unlocked(
        self,
    ) -> dict[str, Any] | None:
        try:
            value = json.loads(
                self.active_recovery_checkpoint_path.read_text(
                    encoding="utf-8"
                )
            )
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, UnicodeError):
            self._reset_corrupt_navigation_unlocked(
                self.active_recovery_checkpoint_path,
                navigation_kind="active_checkpoint",
            )
            return None
        except OSError as exc:
            raise ExecutionStorageError(
                "F3 active recovery checkpoint read failed"
            ) from exc
        try:
            return self._validate_active_recovery_checkpoint(value)
        except ExecutionRecordCorrupt:
            self._reset_corrupt_navigation_unlocked(
                self.active_recovery_checkpoint_path,
                navigation_kind="active_checkpoint",
            )
            return None

    def active_recovery_checkpoint(self) -> dict[str, Any] | None:
        """Return bounded, non-authoritative pending recovery navigation."""

        with self._exclusive_transaction():
            return self._read_active_recovery_checkpoint_unlocked()

    @classmethod
    def active_recovery_checkpoint_for_candidates(
        cls, declarations: Iterable[dict[str, Any]]
    ) -> dict[str, Any] | None:
        candidates = [
            {
                "public_task_id": item["public_task_id"],
                "child_id": item["child_id"],
                "operation_id": item["operation_id"],
                "operation_ordinal": item["operation_ordinal"],
                "attempt_id": item["attempt_id"],
                "declaration_hash": item["declaration_hash"],
            }
            for item in declarations
        ]
        if not candidates:
            return None
        return cls._validate_active_recovery_checkpoint(
            {
                "model": ACTIVE_RECOVERY_CHECKPOINT_MODEL,
                "schema_version": ACTIVE_RECOVERY_CHECKPOINT_SCHEMA_VERSION,
                "candidates": candidates,
            }
        )

    @staticmethod
    def active_recovery_cursor_for_task(
        public_task_id: str,
    ) -> dict[str, Any]:
        validate_identifier(public_task_id, field_name="public_task_id")
        return {
            "model": ACTIVE_RECOVERY_CURSOR_MODEL,
            "schema_version": ACTIVE_RECOVERY_CURSOR_SCHEMA_VERSION,
            "public_task_id": public_task_id,
        }

    @staticmethod
    def recovery_cursor_for_declaration(
        declaration: dict[str, Any],
    ) -> dict[str, Any]:
        """Return non-authoritative scheduling evidence for one declaration."""

        try:
            validate_identifier(
                declaration["public_task_id"], field_name="public_task_id"
            )
            validate_identifier(
                declaration["child_id"], field_name="child_id"
            )
            ordinal = declaration["operation_ordinal"]
            if type(ordinal) is not int or not 0 <= ordinal < 8:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise ExecutionRecordCorrupt(
                "F3 recovery declaration cursor source is corrupt"
            ) from exc
        return {
            "model": RECOVERY_CURSOR_MODEL,
            "schema_version": RECOVERY_CURSOR_SCHEMA_VERSION,
            "public_task_id": declaration["public_task_id"],
            "operation_ordinal": declaration["operation_ordinal"],
            "child_id": declaration["child_id"],
        }

    def recovery_declaration_page(
        self,
        *,
        limit: int = RECOVERY_DECLARATION_PAGE_SIZE,
        should_stop: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Read a deterministic, restart-fair declaration page.

        Manifest path ordering is bounded by the reviewed 1,024-task
        namespace, and at most ``limit`` declarations are materialized. The
        returned far-edge cursor is informational; the caller persists only
        the last declaration it safely examined after its sweep completes.
        """

        if not 1 <= limit <= RECOVERY_DECLARATION_PAGE_SIZE:
            raise ValueError("recovery declaration page limit is invalid")
        stop = should_stop or (lambda: False)
        with self._exclusive_transaction():
            cursor = self._read_recovery_cursor_unlocked()
            paths = self._bounded_paths(
                "*.manifest.json", MAX_F3_PUBLIC_TASKS
            )
            if cursor is not None and paths:
                current = cursor["public_task_id"]
                split = next(
                    (
                        index
                        for index, path in enumerate(paths)
                        if path.name.removesuffix(".manifest.json") >= current
                    ),
                    0,
                )
                paths = paths[split:] + paths[:split]
            declarations: list[dict[str, Any]] = []
            next_cursor = cursor
            manifest_reads = 0
            for path in paths:
                if stop() or len(declarations) >= limit:
                    break
                public_task_id = path.name.removesuffix(".manifest.json")
                manifest = self.manifest_for_task(
                    public_task_id, locked=True
                )
                manifest_reads += 1
                if manifest is None:
                    continue
                for declaration in manifest["declarations"]:
                    if stop() or len(declarations) >= limit:
                        break
                    if (
                        cursor is not None
                        and public_task_id == cursor["public_task_id"]
                        and declaration["operation_ordinal"]
                        <= cursor["operation_ordinal"]
                    ):
                        continue
                    item = dict(declaration)
                    declarations.append(item)
                    next_cursor = self.recovery_cursor_for_declaration(item)
            if (
                not declarations
                and cursor is not None
                and paths
                and not stop()
            ):
                # A one-manifest namespace can be positioned after its final
                # child. Wrap once without the cursor skip so it remains
                # eligible on the next bounded sweep.
                public_task_id = paths[0].name.removesuffix(
                    ".manifest.json"
                )
                manifest = self.manifest_for_task(
                    public_task_id, locked=True
                )
                manifest_reads += 1
                if manifest is not None:
                    for declaration in manifest["declarations"]:
                        if stop() or len(declarations) >= limit:
                            break
                        item = dict(declaration)
                        declarations.append(item)
                        next_cursor = self.recovery_cursor_for_declaration(
                            item
                        )
            return {
                "cursor": cursor,
                "next_cursor": next_cursor,
                "declarations": tuple(declarations),
                "manifest_reads": manifest_reads,
            }

    def advance_recovery_cursor(
        self,
        *,
        expected: dict[str, Any] | None,
        next_cursor: dict[str, Any] | None,
    ) -> None:
        validated_expected = self._validate_recovery_cursor(expected)
        validated_next = self._validate_recovery_cursor(next_cursor)
        if validated_next is None:
            return
        with self._exclusive_transaction():
            current = self._read_recovery_cursor_unlocked()
            if current != validated_expected:
                raise ExecutionStorageError(
                    "F3 recovery declaration cursor changed concurrently"
                )
            self._atomic_write(self.recovery_cursor_path, validated_next)

    def advance_active_recovery_cursor(
        self,
        *,
        expected: dict[str, Any] | None,
        next_cursor: dict[str, Any] | None,
    ) -> None:
        validated_expected = self._validate_active_recovery_cursor(expected)
        validated_next = self._validate_active_recovery_cursor(next_cursor)
        if validated_next is None or validated_next == validated_expected:
            return
        with self._exclusive_transaction():
            current = self._read_active_recovery_cursor_unlocked()
            if current != validated_expected:
                raise ExecutionStorageError(
                    "F3 active recovery cursor changed concurrently"
                )
            self._atomic_write(
                self.active_recovery_cursor_path, validated_next
            )

    def replace_active_recovery_checkpoint(
        self,
        *,
        expected: dict[str, Any] | None,
        next_checkpoint: dict[str, Any] | None,
    ) -> None:
        """CAS bounded pending navigation without granting recovery authority."""

        validated_expected = self._validate_active_recovery_checkpoint(
            expected
        )
        validated_next = self._validate_active_recovery_checkpoint(
            next_checkpoint
        )
        if validated_next == validated_expected:
            return
        with self._exclusive_transaction():
            current = self._read_active_recovery_checkpoint_unlocked()
            if current != validated_expected:
                raise ExecutionStorageError(
                    "F3 active recovery checkpoint changed concurrently"
                )
            if validated_next is not None:
                self._atomic_write(
                    self.active_recovery_checkpoint_path, validated_next
                )
                return
            try:
                self.active_recovery_checkpoint_path.unlink(missing_ok=True)
                directory_fd = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError as exc:
                raise ExecutionStorageError(
                    "atomic active recovery checkpoint removal failed"
                ) from exc

    def update_runtime(self, child_id: str, *, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "approval_consumption_reference", "next_eligible_at", "backoff_seconds",
            "selective_hold_tokens", "last_reconciliation_at", "reconciliation_result",
            "audited_event_count",
            "selective_hold_promoted_at", "selective_hold_reason",
            "last_readback_summary",
            "operation_evidence",
            "hold_release_authority",
        }
        if set(changes) - allowed:
            raise ExecutionStorageError("child runtime update is not bounded")
        with self._exclusive_transaction():
            envelope = self._raw_envelope(child_id)
            if envelope is None:
                raise ExecutionRecordCorrupt("child record is missing")
            runtime = dict(envelope["runtime"])
            runtime.update(changes)
            runtime["record_generation"] = int(runtime["record_generation"]) + 1
            envelope["runtime"] = runtime
            self._atomic_write(self._path(child_id), envelope)
            return dict(runtime)

    def runtime(self, child_id: str) -> dict[str, Any]:
        with self._exclusive_transaction():
            envelope = self._raw_envelope(child_id)
            if envelope is None:
                raise ExecutionRecordCorrupt("child record is missing")
            return dict(envelope["runtime"])

    def health(self) -> dict[str, Any]:
        records = self.list()
        manifest_paths = self._bounded_paths(
            "*.manifest.json", MAX_F3_PUBLIC_TASKS
        )
        declarations = sum(
            len(
                self.manifest_for_task(
                    path.name.removesuffix(".manifest.json")
                )["declarations"]
            )
            for path in manifest_paths
        )
        return {
            "status": "healthy",
            "record_count": declarations,
            "materialized_execution_count": len(records),
            "nonterminal_execution_count": sum(not item.terminal for item in records),
            "manual_review_count": sum(
                item.normalized_outcome == "manual_review_required" for item in records
            ),
        }

    def cleanup(self, *, now: object | None = None) -> int:
        """Retain evidence while schema-1 public task history references it.

        Standalone F3-A cleanup cannot safely remove a child envelope without
        coordinating public task retention.  Validation still runs here so a
        corrupt namespace cannot be hidden by cleanup.
        """

        del now
        self.health()
        return 0
