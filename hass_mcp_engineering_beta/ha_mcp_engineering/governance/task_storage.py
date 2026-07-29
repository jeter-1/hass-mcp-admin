"""Crash-safe versioned persistence for durable execution tasks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


class ExecutionTaskStorageError(RuntimeError):
    pass


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
        self.retention_days = retention_days
        self.corruption_count = 0
        self.write_failures = 0
        self.event_write_failures = 0
        self.materialization_failures = 0
        self.rehydration_attempts = 0
        self._fault_hook = fault_hook
        self._lock = threading.RLock()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.quarantine.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ExecutionTaskStorageError(
                "Unable to initialize execution-task storage"
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
            with self._lock:
                self._require_unique(task)
                previous = self._load(path, quarantine_corrupt=False)
                if previous is not None:
                    self._require_append_only(previous, task)
                self._inject("before_task_write")
                with open(temporary, "x", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._inject("before_task_replace")
                os.replace(temporary, path)
                self._inject("after_task_replace")
        except ExecutionTaskStorageError:
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
            self.rehydration_attempts += 1
            return self._load(self._path(task_id))

    def get_for_plan(self, plan_id: str) -> ExecutionTask | None:
        if not PLAN_ID.fullmatch(plan_id):
            return None
        paths = sorted(self.root.glob(f"{plan_id}.*.json"))
        quarantined = sorted(
            self.quarantine.glob(f"{plan_id}.*.corrupt")
        )
        if quarantined:
            raise ExecutionTaskStorageError(
                "Execution-task plan ownership is quarantined"
            )
        if len(paths) > 1:
            raise ExecutionTaskStorageError(
                "Execution-task plan ownership is ambiguous"
            )
        if not paths:
            return None
        task = self._load(paths[0])
        if task is None or task.plan_id != plan_id:
            raise ExecutionTaskStorageError(
                "Execution-task plan ownership is corrupt"
            )
        return task

    def get_for_idempotency(
        self, idempotency_key: str
    ) -> ExecutionTask | None:
        matches = [
            task
            for task in self.list()
            if task.idempotency_key == idempotency_key
        ]
        if len(matches) > 1:
            raise ExecutionTaskStorageError(
                "Execution-task idempotency ownership is ambiguous"
            )
        return matches[0] if matches else None

    def _load(
        self,
        path: Path,
        *,
        quarantine_corrupt: bool = True,
    ) -> ExecutionTask | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return ExecutionTask.from_dict(value)
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
        for task in self.list():
            if task.state not in TERMINAL_TASK_STATES:
                continue
            try:
                completed = datetime.fromisoformat(
                    task.completed_at or task.updated_at
                )
            except ValueError:
                continue
            if completed < cutoff:
                try:
                    self._path(
                        task.task_id, plan_id=task.plan_id
                    ).unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    self.write_failures += 1
        return removed

    def health(self) -> dict[str, object]:
        tasks = self.list()
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
            "record_count": len(tasks),
            "event_count": sum(len(task.events) for task in tasks),
            "corruption_count": self.corruption_count,
            "write_failures": self.write_failures,
            "event_write_failures": self.event_write_failures,
            "materialization_failures": self.materialization_failures,
            "rehydration_attempts": self.rehydration_attempts,
            "retention_days": self.retention_days,
            "manual_review_count": sum(
                task.state == ExecutionTaskState.MANUAL_REVIEW_REQUIRED
                for task in tasks
            ),
        }
