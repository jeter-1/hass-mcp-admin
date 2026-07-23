"""Atomic beta-only change-plan persistence with quarantine and retention."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import threading
from .models import ChangePlan, PlanStatus, StepExecutionStatus


PLAN_ID = re.compile(r"^[a-f0-9]{32}$")
TERMINAL_STATUSES = {
    PlanStatus.VALIDATION_FAILED,
    PlanStatus.APPLIED,
    PlanStatus.FAILED,
    PlanStatus.ROLLED_BACK,
    PlanStatus.ROLLBACK_FAILED,
    PlanStatus.EXPIRED,
    PlanStatus.SUPERSEDED,
    PlanStatus.REJECTED,
}


def is_terminal_plan(plan: ChangePlan) -> bool:
    """Return lifecycle finality without changing contract-v1 semantics."""

    return plan.status in TERMINAL_STATUSES or (
        plan.contract_version >= 2
        and plan.status == PlanStatus.VERIFICATION_FAILED
    )


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

    def _read_path(self, plan_id: str) -> Path | None:
        """Return a safe record path, or None for a non-record identifier.

        Invalid/nonexistent identifiers are lookup misses, not storage health
        failures. Save paths remain strict because generated plan IDs must
        always satisfy the repository format.
        """
        if not PLAN_ID.fullmatch(plan_id):
            return None
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
        path = self._read_path(plan_id)
        if path is None:
            return None
        try:
            with self._lock:
                value = json.loads(path.read_text(encoding="utf-8"))
            return ChangePlan.from_dict(value)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ChangePlanStorageError("Governance record read failed") from exc
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self._quarantine(path)
            raise ChangePlanStorageError("Governance record is corrupt") from exc

    def list(self) -> list[ChangePlan]:
        plans = []
        for path in sorted(self.root.glob("*.json")):
            try:
                plan = self.get(path.stem)
            except ChangePlanStorageError:
                # Corrupt records are quarantined and do not block startup or
                # healthy records. Genuine directory enumeration failures are
                # raised by glob before this point.
                continue
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
            if not is_terminal_plan(plan):
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
            if plan.contract_version >= 2:
                attempted_write_count = 0
                successful_write_count = 0
                verified_write_count = 0
                ambiguous_write_count = 0
                interrupted_write_count = 0
                completed_operation_count = 0
                interrupted_operation_id: str | None = None
                blocking_operation_id: str | None = None
                no_mutation_error_code: str | None = None
                no_mutation_failure_reason: str | None = None
                for operation in sorted(
                    plan.operations, key=lambda item: item.order
                ):
                    receipt = dict(operation.execution_receipt or {})
                    original_status = operation.execution_status
                    write_attempted = (
                        receipt.get("write_attempted") is True
                        or original_status == StepExecutionStatus.APPLYING
                    )
                    write_completed = (
                        receipt.get("write_completed") is True
                    )
                    outcome = receipt.get("outcome")
                    ambiguous = outcome in {
                        "state_proven_desired_after_ambiguous_write",
                        "write_and_resulting_state_unconfirmed",
                        "interrupted_before_exact_verification",
                    } or (
                        write_attempted
                        and not write_completed
                        and original_status
                        in {
                            StepExecutionStatus.APPLYING,
                            StepExecutionStatus.FAILED,
                        }
                    )
                    mutation_completed = bool(
                        write_completed
                        or outcome
                        == "state_proven_desired_after_ambiguous_write"
                    )

                    if write_attempted:
                        attempted_write_count += 1
                    if write_completed:
                        successful_write_count += 1
                    if ambiguous:
                        ambiguous_write_count += 1
                    if (
                        original_status
                        == StepExecutionStatus.APPLIED_VERIFIED
                    ):
                        if mutation_completed:
                            verified_write_count += 1
                            completed_operation_count += 1
                        continue
                    if (
                        original_status
                        == StepExecutionStatus.APPLYING
                    ):
                        interrupted_operation_id = (
                            interrupted_operation_id
                            or operation.operation_id
                        )
                        blocking_operation_id = (
                            blocking_operation_id
                            or operation.operation_id
                        )
                        interrupted_write_count += 1
                        operation.execution_status = (
                            StepExecutionStatus.FAILED
                        )
                        receipt.setdefault("write_attempted", True)
                        receipt.setdefault("write_completed", False)
                        receipt.setdefault("readback_completed", False)
                        receipt["outcome"] = (
                            "interrupted_before_exact_verification"
                        )
                        receipt["recovery_detected_at"] = timestamp
                        operation.execution_receipt = receipt
                        operation.failure_information = {
                            **(operation.failure_information or {}),
                            "error_code": "configuration_apply_failed",
                            "reason": "server_restart_during_apply",
                        }
                        continue
                    if (
                        original_status
                        in {
                            StepExecutionStatus.FAILED,
                            StepExecutionStatus.VERIFICATION_FAILED,
                        }
                    ):
                        blocking_operation_id = (
                            blocking_operation_id
                            or operation.operation_id
                        )
                        if mutation_completed:
                            completed_operation_count += 1
                        elif not write_attempted and not ambiguous:
                            candidate_code = (
                                operation.failure_information or {}
                            ).get("error_code")
                            if candidate_code in {
                                "configuration_apply_failed",
                                "configuration_conflict",
                                "configuration_verification_failed",
                                "stale_target_state",
                            }:
                                no_mutation_error_code = (
                                    no_mutation_error_code
                                    or candidate_code
                                )
                            candidate_reason = (
                                operation.failure_information or {}
                            ).get("reason")
                            if isinstance(candidate_reason, str):
                                no_mutation_failure_reason = (
                                    no_mutation_failure_reason
                                    or candidate_reason[:160]
                                )
                        if ambiguous:
                            receipt.setdefault(
                                "recovery_detected_at", timestamp
                            )
                            operation.execution_receipt = receipt

                for operation in sorted(
                    plan.operations, key=lambda item: item.order
                ):
                    if (
                        operation.execution_status
                        != StepExecutionStatus.PENDING
                    ):
                        continue
                    receipt = dict(operation.execution_receipt or {})
                    operation.execution_status = (
                        StepExecutionStatus.NOT_ATTEMPTED_DEPENDENCY_FAILURE
                    )
                    receipt.setdefault("write_attempted", False)
                    receipt["reason"] = (
                        "server_restart_after_incomplete_apply"
                    )
                    if blocking_operation_id:
                        receipt["blocked_by_operation_id"] = (
                            blocking_operation_id
                        )
                    operation.execution_receipt = receipt

                partial_or_uncertain = bool(
                    attempted_write_count
                    or successful_write_count
                    or ambiguous_write_count
                )
                plan.status = PlanStatus.FAILED
                plan.execution_outcome = (
                    "partial_failure"
                    if partial_or_uncertain
                    else "not_applied"
                )
                if plan.configuration_check_status in {None, "not_run"}:
                    plan.configuration_check_status = (
                        "not_run_after_restart"
                    )
                failure_code = (
                    "configuration_partial_failure"
                    if partial_or_uncertain
                    else (
                        no_mutation_error_code
                        or "configuration_apply_failed"
                    )
                )
                plan.failure_information = {
                    "error_code": failure_code,
                    "reason": (
                        "An incomplete ordered apply was detected after "
                        "server restart; execution was not resumed."
                        if partial_or_uncertain
                        else (
                            no_mutation_failure_reason
                            or (
                                "A non-mutating operation failure was "
                                "recovered after server restart; execution "
                                "was not resumed."
                            )
                        )
                    ),
                    "interrupted_operation_id": interrupted_operation_id,
                    "attempted_write_count": attempted_write_count,
                    "successful_write_count": successful_write_count,
                    "verified_write_count": verified_write_count,
                    "ambiguous_write_count": ambiguous_write_count,
                    "interrupted_write_count": interrupted_write_count,
                    "completed_operation_count": completed_operation_count,
                }
                plan.updated_at = timestamp
                self.save(plan)
                recovered += 1
                continue

            # Contract-v1 restart recovery is intentionally unchanged.
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
