"""Crash-safe versioned persistence for durable execution tasks."""

from __future__ import annotations

from bisect import insort
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import threading
from typing import Callable

from .task_models import (
    ExecutionTask,
    ExecutionTaskState,
    TERMINAL_TASK_STATES,
)


TASK_NAMESPACE = "execution-tasks-v1"
TASK_ID = re.compile(r"^[a-f0-9]{32}$")
PLAN_ID = re.compile(r"^[a-f0-9]{32}$")
F3_EXECUTION_AUTHORITY = "f3_child_sequence"


class ExecutionTaskStorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskNavigationEntry:
    """Non-authoritative task metadata for bounded navigation only."""

    task_id: str
    plan_id: str
    idempotency_key: str
    created_at: str
    updated_at: str
    completed_at: str | None
    state: str
    terminal: bool
    event_count: int
    execution_authority: str | None

    @property
    def order_key(self) -> tuple[float, str]:
        try:
            created = datetime.fromisoformat(self.created_at).timestamp()
        except (TypeError, ValueError):
            created = 0.0
        return (-created, self.task_id)


class ExecutionTaskRepository:
    def __init__(
        self,
        root: str | Path,
        *,
        retention_days: int = 90,
        fault_hook: Callable[[str], None] | None = None,
    ):
        self.governance_root = Path(root)
        self.root = self.governance_root / TASK_NAMESPACE
        self.quarantine = self.root / "quarantine"
        self.transaction_path = self.root / ".transaction.lock"
        self.retention_days = retention_days
        self.corruption_count = 0
        self.write_failures = 0
        self.event_write_failures = 0
        self.materialization_failures = 0
        self.rehydration_attempts = 0
        self._fault_hook = fault_hook
        self._lock = threading.RLock()
        self._entries: dict[str, TaskNavigationEntry] = {}
        self._ordered_keys: list[tuple[float, str]] = []
        self._nonterminal_ids: set[str] = set()
        self._nonterminal_keys: list[tuple[float, str]] = []
        self._f3_nonterminal_ids: set[str] = set()
        self._f3_nonterminal_keys: list[tuple[float, str]] = []
        self._task_by_plan: dict[str, str] = {}
        self._task_by_idempotency: dict[str, str] = {}
        self._expected_nonterminal_count = 0
        self._expected_nonterminal_signature = (0, 0, 0)
        self._expected_f3_nonterminal_signature = (0, 0, 0)
        self._total_event_count = 0
        self._manual_review_count = 0
        self._legacy_task_count = 0
        self._legacy_active_task_count = 0
        self.generation = 0
        self.index_rebuild_count = 0
        self.index_update_count = 0
        self.index_invalidation_count = 0
        self.records_deserialized = 0
        self.terminal_records_deserialized = 0
        self.full_history_scan_count = 0
        self._observed_directory_token: tuple[int, int] | None = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.quarantine.mkdir(parents=True, exist_ok=True)
            self.transaction_path.touch(exist_ok=True)
        except OSError as exc:
            raise ExecutionTaskStorageError(
                "Unable to initialize execution-task storage"
            ) from exc
        self.rebuild_navigation_index()

    @staticmethod
    def _navigation_entry(task: ExecutionTask) -> TaskNavigationEntry:
        return TaskNavigationEntry(
            task_id=task.task_id,
            plan_id=task.plan_id,
            idempotency_key=task.idempotency_key,
            created_at=task.created_at,
            updated_at=task.updated_at,
            completed_at=task.completed_at,
            state=task.state.value,
            terminal=task.state in TERMINAL_TASK_STATES,
            event_count=len(task.events),
            execution_authority=(
                str(task.legacy_projection.get("execution_authority"))
                if task.legacy_projection.get("execution_authority")
                is not None
                else None
            ),
        )

    @staticmethod
    def _identifier_signature(values: set[str]) -> tuple[int, int, int]:
        numeric = tuple(int(value, 16) for value in values)
        xor = 0
        for value in numeric:
            xor ^= value
        return (len(numeric), sum(numeric), xor)

    def _refresh_expected_nonterminal_signature(self) -> None:
        self._expected_nonterminal_signature = self._identifier_signature(
            self._nonterminal_ids
        )
        self._expected_f3_nonterminal_signature = (
            self._identifier_signature(self._f3_nonterminal_ids)
        )

    def _remove_entry(self, task_id: str) -> None:
        entry = self._entries.pop(task_id, None)
        if entry is None:
            return
        try:
            self._ordered_keys.remove(entry.order_key)
        except ValueError:
            self.index_invalidation_count += 1
        self._nonterminal_ids.discard(task_id)
        if not entry.terminal:
            self._expected_nonterminal_count -= 1
            self._nonterminal_keys.remove(entry.order_key)
            if entry.execution_authority == F3_EXECUTION_AUTHORITY:
                self._f3_nonterminal_ids.discard(task_id)
                self._f3_nonterminal_keys.remove(entry.order_key)
        self._total_event_count -= entry.event_count
        if entry.state == ExecutionTaskState.MANUAL_REVIEW_REQUIRED.value:
            self._manual_review_count -= 1
        if entry.execution_authority != F3_EXECUTION_AUTHORITY:
            self._legacy_task_count -= 1
            if not entry.terminal:
                self._legacy_active_task_count -= 1
        if self._task_by_plan.get(entry.plan_id) == task_id:
            self._task_by_plan.pop(entry.plan_id, None)
        if self._task_by_idempotency.get(entry.idempotency_key) == task_id:
            self._task_by_idempotency.pop(entry.idempotency_key, None)
        self._refresh_expected_nonterminal_signature()

    def _put_entry(self, task: ExecutionTask, *, count_update: bool = True) -> None:
        self._remove_entry(task.task_id)
        entry = self._navigation_entry(task)
        self._entries[task.task_id] = entry
        self._task_by_plan[task.plan_id] = task.task_id
        self._task_by_idempotency[task.idempotency_key] = task.task_id
        insort(self._ordered_keys, entry.order_key)
        if not entry.terminal:
            self._nonterminal_ids.add(task.task_id)
            insort(self._nonterminal_keys, entry.order_key)
            self._expected_nonterminal_count += 1
            if entry.execution_authority == F3_EXECUTION_AUTHORITY:
                self._f3_nonterminal_ids.add(task.task_id)
                insort(self._f3_nonterminal_keys, entry.order_key)
        self._total_event_count += entry.event_count
        if entry.state == ExecutionTaskState.MANUAL_REVIEW_REQUIRED.value:
            self._manual_review_count += 1
        if entry.execution_authority != F3_EXECUTION_AUTHORITY:
            self._legacy_task_count += 1
            if not entry.terminal:
                self._legacy_active_task_count += 1
        self._refresh_expected_nonterminal_signature()
        if count_update:
            self.generation += 1
            self.index_update_count += 1

    def _record_paths(self) -> list[Path]:
        return sorted(self.root.glob("*.json"))

    def _directory_token(self) -> tuple[int, int]:
        try:
            state = self.root.stat()
        except OSError as exc:
            raise ExecutionTaskStorageError(
                "Unable to inspect execution-task storage generation"
            ) from exc
        return (state.st_mtime_ns, state.st_ctime_ns)

    def _mark_navigation_current(self) -> None:
        self._observed_directory_token = self._directory_token()

    def rebuild_navigation_index(self) -> dict[str, int]:
        """Deep-audit tasks and rebuild navigation from persisted authority."""

        with self._lock:
            self._entries.clear()
            self._ordered_keys.clear()
            self._nonterminal_ids.clear()
            self._nonterminal_keys.clear()
            self._f3_nonterminal_ids.clear()
            self._f3_nonterminal_keys.clear()
            self._task_by_plan.clear()
            self._task_by_idempotency.clear()
            self._expected_nonterminal_count = 0
            self._total_event_count = 0
            self._manual_review_count = 0
            self._legacy_task_count = 0
            self._legacy_active_task_count = 0
            self._refresh_expected_nonterminal_signature()
            before = self.records_deserialized
            paths = self._record_paths()
            idempotency_keys: set[str] = set()
            for path in paths:
                try:
                    task = self._load(path)
                except ExecutionTaskStorageError:
                    continue
                if task is None:
                    continue
                if (
                    task.plan_id in self._task_by_plan
                    or task.idempotency_key in idempotency_keys
                ):
                    raise ExecutionTaskStorageError(
                        "Execution-task ownership is ambiguous"
                    )
                self._put_entry(task, count_update=False)
                idempotency_keys.add(task.idempotency_key)
            self.generation += 1
            self.index_rebuild_count += 1
            self._mark_navigation_current()
            return {
                "records_enumerated": len(paths),
                "records_deserialized": self.records_deserialized - before,
                "indexed_records": len(self._entries),
                "nonterminal_records": len(self._nonterminal_ids),
            }

    def _ensure_navigation_index(self) -> None:
        if (
            self._observed_directory_token != self._directory_token()
            or len(self._ordered_keys) != len(self._entries)
            or len(self._task_by_plan) != len(self._entries)
            or len(self._task_by_idempotency) != len(self._entries)
            or len(self._nonterminal_ids)
            != self._expected_nonterminal_count
            or len(self._nonterminal_keys)
            != self._expected_nonterminal_count
            or self._identifier_signature(self._nonterminal_ids)
            != self._expected_nonterminal_signature
            or self._identifier_signature(
                {task_id for _, task_id in self._nonterminal_keys}
            )
            != self._expected_nonterminal_signature
            or not self._f3_nonterminal_ids.issubset(
                self._nonterminal_ids
            )
            or len(self._f3_nonterminal_keys)
            != len(self._f3_nonterminal_ids)
            or self._identifier_signature(self._f3_nonterminal_ids)
            != self._expected_f3_nonterminal_signature
            or self._identifier_signature(
                {task_id for _, task_id in self._f3_nonterminal_keys}
            )
            != self._expected_f3_nonterminal_signature
            or any(
                task_id not in self._entries
                or self._entries[task_id].terminal
                or self._entries[task_id].execution_authority
                != F3_EXECUTION_AUTHORITY
                for task_id in self._f3_nonterminal_ids
            )
        ):
            self.index_invalidation_count += 1
            self.rebuild_navigation_index()

    def nonterminal_task_ids(self) -> tuple[str, ...]:
        with self._lock:
            self._ensure_navigation_index()
            return tuple(task_id for _, task_id in self._nonterminal_keys)

    def f3_nonterminal_task_ids(self, *, limit: int) -> tuple[str, ...]:
        """Return bounded F3-authority navigation without mixed-task scans.

        The in-memory index is non-authoritative scheduling evidence. Callers
        must reload each task and the corresponding F3 authority before use.
        A namespace larger than its independently reviewed F3 bound fails
        closed instead of silently making tail tasks unreachable.
        """

        if type(limit) is not int or limit < 1:
            raise ExecutionTaskStorageError(
                "F3 nonterminal navigation limit is invalid"
            )
        with self._lock:
            self._ensure_navigation_index()
            if len(self._f3_nonterminal_keys) > limit:
                raise ExecutionTaskStorageError(
                    "F3 nonterminal namespace exceeds navigation bound"
                )
            return tuple(
                task_id for _, task_id in self._f3_nonterminal_keys
            )

    def list_nonterminal(self) -> list[ExecutionTask]:
        tasks: list[ExecutionTask] = []
        for task_id in self.nonterminal_task_ids():
            task = self.get(task_id)
            if task is not None and task.state not in TERMINAL_TASK_STATES:
                tasks.append(task)
        return tasks

    def navigation_metrics(self) -> dict[str, int]:
        with self._lock:
            self._ensure_navigation_index()
            return {
                "generation": self.generation,
                "record_count": len(self._entries),
                "nonterminal_record_count": len(self._nonterminal_ids),
                "index_rebuild_count": self.index_rebuild_count,
                "index_update_count": self.index_update_count,
                "index_invalidation_count": self.index_invalidation_count,
                "records_deserialized": self.records_deserialized,
                "terminal_records_deserialized": (
                    self.terminal_records_deserialized
                ),
                "full_history_scan_count": self.full_history_scan_count,
                "legacy_task_count": self._legacy_task_count,
                "legacy_active_task_count": self._legacy_active_task_count,
            }

    @contextmanager
    def transaction(self):
        """Serialize task ownership and event appends across processes."""

        try:
            with self._lock:
                with open(self.transaction_path, "a+b") as handle:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ExecutionTaskStorageError:
            raise
        except OSError as exc:
            raise ExecutionTaskStorageError(
                "Execution-task transaction failed"
            ) from exc

    def _path(
        self, task_id: str, *, plan_id: str | None = None
    ) -> Path:
        if not TASK_ID.fullmatch(task_id):
            raise ExecutionTaskStorageError(
                "Invalid execution-task identifier"
            )
        if plan_id is not None:
            if not PLAN_ID.fullmatch(plan_id):
                raise ExecutionTaskStorageError(
                    "Invalid execution-task plan identifier"
                )
            return self.root / f"{plan_id}.{task_id}.json"
        matches = sorted(self.root.glob(f"*.{task_id}.json"))
        if len(matches) > 1:
            raise ExecutionTaskStorageError(
                "Execution-task identifier is ambiguous"
            )
        return (
            matches[0]
            if matches
            else self.root / f"missing.{task_id}.json"
        )

    def _inject(self, stage: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(stage)

    def save(self, task: ExecutionTask) -> None:
        try:
            task.validate()
        except (TypeError, ValueError, KeyError) as exc:
            self.materialization_failures += 1
            raise ExecutionTaskStorageError(
                "Execution-task materialization is invalid"
            ) from exc
        path = self._path(task.task_id, plan_id=task.plan_id)
        temporary = path.with_suffix(
            f".tmp-{os.getpid()}-{threading.get_ident()}"
        )
        payload = json.dumps(
            task.to_dict(), sort_keys=True, separators=(",", ":")
        )
        try:
            with self.transaction():
                self._ensure_navigation_index()
                previous = self._load(path, quarantine_corrupt=False)
                if previous is None:
                    # New ownership still receives an authoritative full-store
                    # uniqueness check. Existing task transitions reload their
                    # exact authority and avoid terminal-history enumeration.
                    self._require_unique(task)
                if previous is not None:
                    self._require_append_only(previous, task)
                self._inject("before_task_write")
                with open(temporary, "x", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._inject("before_task_replace")
                os.replace(temporary, path)
                directory_fd = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                self._inject("after_task_replace")
                self._put_entry(task)
                self._mark_navigation_current()
        except ExecutionTaskStorageError as exc:
            if isinstance(exc.__cause__, OSError):
                self.write_failures += 1
                self.event_write_failures += 1
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        except OSError as exc:
            self.write_failures += 1
            self.event_write_failures += 1
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ExecutionTaskStorageError(
                "Atomic execution-task write failed"
            ) from exc

    @staticmethod
    def _require_append_only(
        previous: ExecutionTask, task: ExecutionTask
    ) -> None:
        immutable = (
            "task_id",
            "task_schema_version",
            "plan_id",
            "plan_hash",
            "operation",
            "target",
            "created_at",
            "execution_request_id",
            "idempotency_key",
        )
        if any(
            getattr(previous, field) != getattr(task, field)
            for field in immutable
        ):
            raise ExecutionTaskStorageError(
                "Execution-task immutable identity changed"
            )
        prior_events = [
            event.to_dict() for event in previous.events
        ]
        current_prefix = [
            event.to_dict()
            for event in task.events[: len(previous.events)]
        ]
        if prior_events != current_prefix:
            raise ExecutionTaskStorageError(
                "Execution-task event history is not append-only"
            )
        if len(task.events) < len(previous.events):
            raise ExecutionTaskStorageError(
                "Execution-task event history was truncated"
            )
        if (
            previous.maximum_post_dispatch_deadline is not None
            and task.maximum_post_dispatch_deadline
            != previous.maximum_post_dispatch_deadline
        ):
            raise ExecutionTaskStorageError(
                "Execution-task dispatch deadline changed"
            )

    def _require_unique(self, task: ExecutionTask) -> None:
        ownership_paths = sorted(
            self.root.glob(f"{task.plan_id}.*.json")
        ) + sorted(self.quarantine.glob(f"{task.plan_id}.*.corrupt"))
        if any(
            path != self._path(task.task_id, plan_id=task.plan_id)
            for path in ownership_paths
        ):
            # The filename itself durably reserves plan ownership even when a
            # corrupt payload cannot be deserialized safely.
            raise ExecutionTaskStorageError(
                "Execution-task ownership is already reserved"
            )
        for candidate in self._list_unlocked():
            if candidate.task_id == task.task_id:
                continue
            if (
                candidate.plan_id == task.plan_id
                or candidate.idempotency_key == task.idempotency_key
            ):
                raise ExecutionTaskStorageError(
                    "Execution-task ownership is not unique"
                )

    def get(self, task_id: str) -> ExecutionTask | None:
        if not TASK_ID.fullmatch(task_id):
            return None
        with self._lock:
            self._ensure_navigation_index()
            self.rehydration_attempts += 1
            entry = self._entries.get(task_id)
            task = self._load(
                self._path(task_id, plan_id=entry.plan_id)
                if entry is not None
                else self._path(task_id)
            )
            if task is None and entry is None and any(
                self.quarantine.glob(f"*.{task_id}.*.corrupt")
            ):
                raise ExecutionTaskStorageError(
                    "Execution-task identifier is quarantined"
                )
            if task is not None and entry is None:
                self._put_entry(task)
                self._mark_navigation_current()
            return task

    def get_for_plan(self, plan_id: str) -> ExecutionTask | None:
        if not PLAN_ID.fullmatch(plan_id):
            return None
        with self._lock:
            self._ensure_navigation_index()
            task_id = self._task_by_plan.get(plan_id)
            paths = (
                sorted(self.root.glob(f"{plan_id}.*.json"))
                if task_id is None
                else [self._path(task_id, plan_id=plan_id)]
            )
            quarantined = sorted(
                self.quarantine.glob(f"{plan_id}.*.corrupt")
            )
            if quarantined:
                raise ExecutionTaskStorageError(
                    "Execution-task plan ownership is quarantined"
                )
            if not paths:
                return None
            task = self._load(paths[0])
            if task is None or task.plan_id != plan_id:
                raise ExecutionTaskStorageError(
                    "Execution-task plan ownership is corrupt"
                )
            if task_id is None:
                self._put_entry(task)
                self._mark_navigation_current()
            return task

    def get_for_idempotency(
        self, idempotency_key: str
    ) -> ExecutionTask | None:
        with self._lock:
            self._ensure_navigation_index()
            task_id = self._task_by_idempotency.get(idempotency_key)
            if task_id is None:
                matches = [
                    task
                    for task in self._list_unlocked()
                    if task.idempotency_key == idempotency_key
                ]
                if len(matches) > 1:
                    raise ExecutionTaskStorageError(
                        "Execution-task idempotency ownership is ambiguous"
                    )
                if not matches:
                    return None
                task = matches[0]
                self._put_entry(task)
                self._mark_navigation_current()
                return task
            entry = self._entries.get(task_id)
            if entry is None:
                raise ExecutionTaskStorageError(
                    "Execution-task idempotency ownership is invalid"
                )
            task = self._load(
                self._path(task_id, plan_id=entry.plan_id)
            )
            if task is None or task.idempotency_key != idempotency_key:
                raise ExecutionTaskStorageError(
                    "Execution-task idempotency ownership is corrupt"
                )
            return task

    def _load(
        self,
        path: Path,
        *,
        quarantine_corrupt: bool = True,
    ) -> ExecutionTask | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            task = ExecutionTask.from_dict(value)
            self.records_deserialized += 1
            if task.state in TERMINAL_TASK_STATES:
                self.terminal_records_deserialized += 1
            return task
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ExecutionTaskStorageError(
                "Execution-task record read failed"
            ) from exc
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            if quarantine_corrupt:
                self._quarantine(path)
            raise ExecutionTaskStorageError(
                "Execution-task record is corrupt"
            ) from exc

    def _list_unlocked(self) -> list[ExecutionTask]:
        tasks: list[ExecutionTask] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                task = self._load(path)
            except ExecutionTaskStorageError:
                continue
            if task is not None:
                tasks.append(task)
        return tasks

    def list(self) -> list[ExecutionTask]:
        with self._lock:
            self.rehydration_attempts += 1
            self.full_history_scan_count += 1
            tasks = self._list_unlocked()
            plan_ids = [task.plan_id for task in tasks]
            keys = [task.idempotency_key for task in tasks]
            if len(plan_ids) != len(set(plan_ids)) or len(keys) != len(
                set(keys)
            ):
                raise ExecutionTaskStorageError(
                    "Execution-task ownership is ambiguous"
                )
            return sorted(
                tasks, key=lambda task: task.created_at, reverse=True
            )

    def _quarantine(self, path: Path) -> None:
        self.corruption_count += 1
        parts = path.stem.split(".")
        if len(parts) == 2:
            self._remove_entry(parts[1])
            self.generation += 1
            self.index_invalidation_count += 1
        try:
            destination = self.quarantine / (
                f"{path.stem}.{int(datetime.now().timestamp())}.corrupt"
            )
            os.replace(path, destination)
        except OSError:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def cleanup(self, *, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=self.retention_days)
        removed = 0
        with self._lock:
            candidates = tuple(self._entries.values())
        for entry in candidates:
            if not entry.terminal:
                continue
            try:
                completed = datetime.fromisoformat(
                    entry.completed_at or entry.updated_at
                )
            except ValueError:
                continue
            if completed < cutoff:
                try:
                    self._path(
                        entry.task_id, plan_id=entry.plan_id
                    ).unlink(missing_ok=True)
                    with self._lock:
                        self._remove_entry(entry.task_id)
                        self.generation += 1
                        self.index_update_count += 1
                    removed += 1
                except OSError:
                    self.write_failures += 1
        return removed

    def health(self) -> dict[str, object]:
        with self._lock:
            self._ensure_navigation_index()
            record_count = len(self._entries)
            event_count = self._total_event_count
            manual_review_count = self._manual_review_count
        return {
            "configured": True,
            "status": (
                "degraded"
                if self.corruption_count
                or self.write_failures
                or self.materialization_failures
                else "healthy"
            ),
            "namespace": TASK_NAMESPACE,
            "record_count": record_count,
            "event_count": event_count,
            "corruption_count": self.corruption_count,
            "write_failures": self.write_failures,
            "event_write_failures": self.event_write_failures,
            "materialization_failures": self.materialization_failures,
            "rehydration_attempts": self.rehydration_attempts,
            "retention_days": self.retention_days,
            "manual_review_count": manual_review_count,
            "navigation": self.navigation_metrics(),
        }
