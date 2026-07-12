"""Atomic beta-only change-plan persistence with quarantine and retention."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import threading
from typing import Iterable

from .models import ChangePlan, PlanStatus


PLAN_ID = re.compile(r"^[a-f0-9]{32}$")
TERMINAL_STATUSES = {
    PlanStatus.VALIDATION_FAILED,
    PlanStatus.APPLIED,
    PlanStatus.FAILED,
    PlanStatus.ROLLED_BACK,
    PlanStatus.ROLLBACK_FAILED,
    PlanStatus.EXPIRED,
    PlanStatus.SUPERSEDED,
}


class ChangePlanStorageError(RuntimeError):
    pass


class ChangePlanRepository:
    def __init__(self, root: str | Path, *, retention_days: int = 90):
        self.root = Path(root)
        self.quarantine = self.root / "quarantine"
        self.retention_days = retention_days
        self.corruption_count = 0
        self.write_failures = 0
        self._lock = threading.RLock()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.quarantine.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ChangePlanStorageError("Unable to initialize governance storage") from exc

    def _path(self, plan_id: str) -> Path:
        if not PLAN_ID.fullmatch(plan_id):
            raise ChangePlanStorageError("Invalid change plan identifier")
        return self.root / f"{plan_id}.json"

    def save(self, plan: ChangePlan) -> None:
        path = self._path(plan.plan_id)
        temporary = path.with_suffix(f".tmp-{os.getpid()}-{threading.get_ident()}")
        payload = json.dumps(plan.to_dict(), sort_keys=True, separators=(",", ":"))
        try:
            with self._lock:
                with open(temporary, "x", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
        except OSError as exc:
            self.write_failures += 1
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ChangePlanStorageError("Atomic governance storage write failed") from exc

    def get(self, plan_id: str) -> ChangePlan | None:
        path = self._path(plan_id)
        if not path.exists():
            return None
        try:
            with self._lock:
                value = json.loads(path.read_text(encoding="utf-8"))
            return ChangePlan.from_dict(value)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            self._quarantine(path)
            return None

    def list(self) -> list[ChangePlan]:
        plans = []
        for path in sorted(self.root.glob("*.json")):
            plan = self.get(path.stem)
            if plan:
                plans.append(plan)
        return sorted(plans, key=lambda plan: plan.created_at, reverse=True)

    def _quarantine(self, path: Path) -> None:
        self.corruption_count += 1
        try:
            destination = self.quarantine / f"{path.stem}.{int(datetime.now().timestamp())}.corrupt"
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
        for plan in self.list():
            if plan.status not in TERMINAL_STATUSES:
                continue
            try:
                updated = datetime.fromisoformat(plan.updated_at)
            except ValueError:
                continue
            if updated < cutoff:
                try:
                    self._path(plan.plan_id).unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    self.write_failures += 1
        return removed

    def recover_incomplete(self, timestamp: str) -> int:
        recovered = 0
        for plan in self.list():
            if plan.status not in {PlanStatus.APPLYING}:
                continue
            plan.status = PlanStatus.FAILED
            plan.updated_at = timestamp
            plan.failure_information = {
                "error_code": "automation_apply_failed",
                "reason": "An incomplete apply was detected after server restart.",
            }
            self.save(plan)
            recovered += 1
        return recovered

    def health(self) -> dict[str, int | str | bool]:
        try:
            plans = self.list()
            status = "healthy"
        except ChangePlanStorageError:
            plans = []
            status = "error"
        return {
            "configured": True,
            "status": status,
            "total_plans": len(plans),
            "corruption_count": self.corruption_count,
            "write_failures": self.write_failures,
            "retention_days": self.retention_days,
        }
