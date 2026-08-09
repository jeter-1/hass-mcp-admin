"""Atomic beta-only change-plan persistence with quarantine and retention."""

from __future__ import annotations

from bisect import insort
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from heapq import merge
from itertools import islice
import json
import os
from pathlib import Path
import re
import threading
from .models import (
    ApprovalPolicyClass,
    ChangeOperation,
    ChangePlan,
    PlanStatus,
    StepExecutionStatus,
)


PLAN_ID = re.compile(r"^[a-f0-9]{32}$")
OPERATIONAL_NAMESPACE = "operational-administration-v3"
OPERATIONAL_OPERATIONS = frozenset(
    {
        ChangeOperation.CREATE_FULL_BACKUP,
        ChangeOperation.CONTROLLED_RELOAD,
        ChangeOperation.RESTART_ADDON,
        ChangeOperation.RESTART_HOME_ASSISTANT,
    }
)
V3_NAMESPACED_OPERATIONS = OPERATIONAL_OPERATIONS | frozenset(
    {ChangeOperation.UPDATE_DASHBOARD}
)
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

    return bool(
        plan.status in TERMINAL_STATUSES
        or (
            plan.policy_decision is not None
            and plan.policy_decision.policy_class
            == ApprovalPolicyClass.PROHIBITED
            and plan.approval.bundle_state == "prohibited"
        )
        or (
            plan.contract_version >= 2
            and plan.status == PlanStatus.VERIFICATION_FAILED
        )
    )


class ChangePlanStorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlanNavigationEntry:
    """Non-authoritative metadata used only to locate persisted records."""

    plan_id: str
    created_at: str
    updated_at: str
    status: str
    effective_status: str
    terminal: bool
    approval_candidate: bool
    recovery_candidate: bool
    operational: bool

    @property
    def order_key(self) -> tuple[float, str]:
        try:
            created = datetime.fromisoformat(self.created_at).timestamp()
        except (TypeError, ValueError):
            created = 0.0
        return (-created, self.plan_id)


class ChangePlanRepository:
    def __init__(self, root: str | Path, *, retention_days: int = 90):
        self.root = Path(root)
        self.quarantine = self.root / "quarantine"
        self.operational_root = self.root / OPERATIONAL_NAMESPACE
        self.operational_quarantine = (
            self.operational_root / "quarantine"
        )
        self.retention_days = retention_days
        self.corruption_count = 0
        self.write_failures = 0
        self._lock = threading.RLock()
        self._entries: dict[str, PlanNavigationEntry] = {}
        self._ordered_keys: list[tuple[float, str]] = []
        self._status_keys: dict[str, list[tuple[float, str]]] = {}
        self._active_ids: set[str] = set()
        self._approval_candidate_ids: set[str] = set()
        self._recovery_candidate_ids: set[str] = set()
        self._active_keys: list[tuple[float, str]] = []
        self._approval_candidate_keys: list[tuple[float, str]] = []
        self._recovery_candidate_keys: list[tuple[float, str]] = []
        self._expected_active_count = 0
        self._expected_approval_candidate_count = 0
        self._expected_recovery_candidate_count = 0
        self._expected_active_signature = (0, 0, 0)
        self._expected_approval_signature = (0, 0, 0)
        self._expected_recovery_signature = (0, 0, 0)
        self.generation = 0
        self.index_rebuild_count = 0
        self.index_update_count = 0
        self.index_invalidation_count = 0
        self.records_deserialized = 0
        self.terminal_records_deserialized = 0
        self.full_history_scan_count = 0
        self._observed_directory_token: tuple[tuple[int, int], ...] | None = (
            None
        )
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.quarantine.mkdir(parents=True, exist_ok=True)
            self.operational_root.mkdir(parents=True, exist_ok=True)
            self.operational_quarantine.mkdir(
                parents=True, exist_ok=True
            )
        except OSError as exc:
            raise ChangePlanStorageError(
                "Unable to initialize governance storage"
            ) from exc
        self.rebuild_navigation_index()

    @staticmethod
    def _navigation_entry(plan: ChangePlan) -> PlanNavigationEntry:
        terminal = is_terminal_plan(plan)
        prohibited = bool(
            plan.policy_decision is not None
            and plan.policy_decision.policy_class
            == ApprovalPolicyClass.PROHIBITED
        )
        approval_candidate = bool(
            not terminal
            and (
                (
                    plan.approval.challenge_id
                    and plan.approval.state.value == "external_pending"
                )
                or (
                    plan.approval.elevated_risk_acknowledgement
                    is not None
                    and plan.approval.elevated_risk_acknowledgement.challenge_id
                    and plan.approval.elevated_risk_acknowledgement.state.value
                    == "external_pending"
                )
            )
        )
        recovery_candidate = bool(
            not terminal
            and plan.status
            in {PlanStatus.APPLYING, PlanStatus.VERIFICATION_REQUIRED}
        )
        return PlanNavigationEntry(
            plan_id=plan.plan_id,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
            status=plan.status.value,
            effective_status=("prohibited" if prohibited else plan.status.value),
            terminal=terminal,
            approval_candidate=approval_candidate,
            recovery_candidate=recovery_candidate,
            operational=ChangePlanRepository._is_operational(plan),
        )

    @staticmethod
    def _identifier_signature(values: set[str]) -> tuple[int, int, int]:
        numeric = tuple(int(value, 16) for value in values)
        xor = 0
        for value in numeric:
            xor ^= value
        return (len(numeric), sum(numeric), xor)

    def _refresh_expected_active_signatures(self) -> None:
        self._expected_active_signature = self._identifier_signature(
            self._active_ids
        )
        self._expected_approval_signature = self._identifier_signature(
            self._approval_candidate_ids
        )
        self._expected_recovery_signature = self._identifier_signature(
            self._recovery_candidate_ids
        )

    def _remove_entry(self, plan_id: str) -> None:
        entry = self._entries.pop(plan_id, None)
        if entry is None:
            return
        try:
            self._ordered_keys.remove(entry.order_key)
        except ValueError:
            self.index_invalidation_count += 1
        bucket = self._status_keys.get(entry.effective_status)
        if bucket is not None:
            try:
                bucket.remove(entry.order_key)
            except ValueError:
                self.index_invalidation_count += 1
            if not bucket:
                self._status_keys.pop(entry.effective_status, None)
        self._active_ids.discard(plan_id)
        self._approval_candidate_ids.discard(plan_id)
        self._recovery_candidate_ids.discard(plan_id)
        if not entry.terminal:
            self._expected_active_count -= 1
            self._active_keys.remove(entry.order_key)
        if entry.approval_candidate:
            self._expected_approval_candidate_count -= 1
            self._approval_candidate_keys.remove(entry.order_key)
        if entry.recovery_candidate:
            self._expected_recovery_candidate_count -= 1
            self._recovery_candidate_keys.remove(entry.order_key)
        self._refresh_expected_active_signatures()

    def _put_entry(self, plan: ChangePlan, *, count_update: bool = True) -> None:
        self._remove_entry(plan.plan_id)
        entry = self._navigation_entry(plan)
        self._entries[plan.plan_id] = entry
        insort(self._ordered_keys, entry.order_key)
        insort(
            self._status_keys.setdefault(entry.effective_status, []),
            entry.order_key,
        )
        if not entry.terminal:
            self._active_ids.add(plan.plan_id)
            insort(self._active_keys, entry.order_key)
            self._expected_active_count += 1
        if entry.approval_candidate:
            self._approval_candidate_ids.add(plan.plan_id)
            insort(self._approval_candidate_keys, entry.order_key)
            self._expected_approval_candidate_count += 1
        if entry.recovery_candidate:
            self._recovery_candidate_ids.add(plan.plan_id)
            insort(self._recovery_candidate_keys, entry.order_key)
            self._expected_recovery_candidate_count += 1
        self._refresh_expected_active_signatures()
        if count_update:
            self.generation += 1
            self.index_update_count += 1

    def _record_paths(self) -> list[Path]:
        paths = sorted(self.root.glob("*.json")) + sorted(
            self.operational_root.glob("*.json")
        )
        identifiers = [path.stem for path in paths]
        if len(identifiers) != len(set(identifiers)):
            raise ChangePlanStorageError(
                "Ambiguous governance record identifier"
            )
        return paths

    def _directory_token(self) -> tuple[tuple[int, int], ...]:
        try:
            values = []
            for path in (self.root, self.operational_root):
                state = path.stat()
                values.append((state.st_mtime_ns, state.st_ctime_ns))
            return tuple(values)
        except OSError as exc:
            raise ChangePlanStorageError(
                "Unable to inspect governance storage generation"
            ) from exc

    def _mark_navigation_current(self) -> None:
        self._observed_directory_token = self._directory_token()

    def _empty_index_has_persisted_records(self) -> bool:
        if self._entries:
            return False
        return bool(
            next(self.root.glob("*.json"), None)
            or next(self.operational_root.glob("*.json"), None)
        )

    def _reset_navigation_index(self) -> None:
        self._entries.clear()
        self._ordered_keys.clear()
        self._status_keys.clear()
        self._active_ids.clear()
        self._approval_candidate_ids.clear()
        self._recovery_candidate_ids.clear()
        self._active_keys.clear()
        self._approval_candidate_keys.clear()
        self._recovery_candidate_keys.clear()
        self._expected_active_count = 0
        self._expected_approval_candidate_count = 0
        self._expected_recovery_candidate_count = 0
        self._refresh_expected_active_signatures()

    def _scan_and_rebuild(
        self, *, count_full_history_scan: bool
    ) -> tuple[list[ChangePlan], dict[str, int]]:
        with self._lock:
            self._reset_navigation_index()
            if count_full_history_scan:
                self.full_history_scan_count += 1
            deserialized_before = self.records_deserialized
            paths = self._record_paths()
            plans: list[ChangePlan] = []
            for path in paths:
                try:
                    plan = self._load(path)
                except ChangePlanStorageError:
                    continue
                if plan is not None:
                    plans.append(plan)
                    self._put_entry(plan, count_update=False)
            self.generation += 1
            self.index_rebuild_count += 1
            self._mark_navigation_current()
            return plans, {
                "records_enumerated": len(paths),
                "records_deserialized": (
                    self.records_deserialized - deserialized_before
                ),
                "indexed_records": len(self._entries),
                "active_records": len(self._active_ids),
            }

    def rebuild_navigation_index(self) -> dict[str, int]:
        """Deep-audit storage and rebuild navigation from authoritative files."""

        _plans, evidence = self._scan_and_rebuild(
            count_full_history_scan=False
        )
        return evidence

    def _ensure_navigation_index(self) -> None:
        if (
            self._observed_directory_token != self._directory_token()
            or self._empty_index_has_persisted_records()
            or len(self._ordered_keys) != len(self._entries)
            or sum(len(value) for value in self._status_keys.values())
            != len(self._entries)
            or len(self._active_ids) != self._expected_active_count
            or len(self._active_keys) != self._expected_active_count
            or len(self._approval_candidate_ids)
            != self._expected_approval_candidate_count
            or len(self._approval_candidate_keys)
            != self._expected_approval_candidate_count
            or len(self._recovery_candidate_ids)
            != self._expected_recovery_candidate_count
            or len(self._recovery_candidate_keys)
            != self._expected_recovery_candidate_count
            or self._identifier_signature(self._active_ids)
            != self._expected_active_signature
            or self._identifier_signature(
                {plan_id for _, plan_id in self._active_keys}
            )
            != self._expected_active_signature
            or self._identifier_signature(self._approval_candidate_ids)
            != self._expected_approval_signature
            or self._identifier_signature(
                {
                    plan_id
                    for _, plan_id in self._approval_candidate_keys
                }
            )
            != self._expected_approval_signature
            or self._identifier_signature(self._recovery_candidate_ids)
            != self._expected_recovery_signature
            or self._identifier_signature(
                {plan_id for _, plan_id in self._recovery_candidate_keys}
            )
            != self._expected_recovery_signature
        ):
            self.index_invalidation_count += 1
            self.rebuild_navigation_index()

    def navigation_plan_ids(
        self,
        *,
        status: str = "",
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[str, ...]:
        """Return one bounded ordered page without loading plan authority."""

        with self._lock:
            self._ensure_navigation_index()
            start = max(0, int(offset))
            stop = None if limit is None else start + max(0, int(limit))
            if not status:
                keys = self._ordered_keys[start:stop]
            else:
                # Active records can change effective lifecycle with time.
                # Merge only that bounded set with the indexed status bucket;
                # filtering active IDs from the bucket prevents duplicates.
                bucket = (
                    key
                    for key in self._status_keys.get(status, ())
                    if key[1] not in self._active_ids
                )
                keys = tuple(
                    islice(
                        merge(bucket, self._active_keys), start, stop
                    )
                )
            return tuple(key[1] for key in keys)

    def active_plan_ids(self) -> tuple[str, ...]:
        with self._lock:
            self._ensure_navigation_index()
            return tuple(plan_id for _, plan_id in self._active_keys)

    def approval_candidate_ids(self) -> tuple[str, ...]:
        with self._lock:
            self._ensure_navigation_index()
            return tuple(
                plan_id for _, plan_id in self._approval_candidate_keys
            )

    def recovery_candidate_ids(self) -> tuple[str, ...]:
        with self._lock:
            self._ensure_navigation_index()
            return tuple(
                plan_id for _, plan_id in self._recovery_candidate_keys
            )

    def navigation_metrics(self) -> dict[str, int]:
        with self._lock:
            self._ensure_navigation_index()
            return {
                "generation": self.generation,
                "record_count": len(self._entries),
                "active_record_count": len(self._active_ids),
                "approval_candidate_count": len(
                    self._approval_candidate_ids
                ),
                "recovery_candidate_count": len(
                    self._recovery_candidate_ids
                ),
                "index_rebuild_count": self.index_rebuild_count,
                "index_update_count": self.index_update_count,
                "index_invalidation_count": self.index_invalidation_count,
                "records_deserialized": self.records_deserialized,
                "terminal_records_deserialized": (
                    self.terminal_records_deserialized
                ),
                "full_history_scan_count": self.full_history_scan_count,
            }

    def _path(self, plan_id: str, *, operational: bool = False) -> Path:
        if not PLAN_ID.fullmatch(plan_id):
            raise ChangePlanStorageError("Invalid change plan identifier")
        directory = self.operational_root if operational else self.root
        return directory / f"{plan_id}.json"

    def _read_paths(self, plan_id: str) -> tuple[Path, Path] | None:
        """Return safe legacy and operational record paths.

        Invalid/nonexistent identifiers are lookup misses, not storage health
        failures. Save paths remain strict because generated plan IDs must
        always satisfy the repository format.
        """
        if not PLAN_ID.fullmatch(plan_id):
            return None
        return (
            self._path(plan_id),
            self._path(plan_id, operational=True),
        )

    @staticmethod
    def _is_operational(plan: ChangePlan) -> bool:
        if plan.contract_version != 3 or plan.operational is None:
            return False
        expected_family = (
            "dashboard_update"
            if plan.operation is ChangeOperation.UPDATE_DASHBOARD
            else "operational_administration"
        )
        return bool(
            plan.operation in V3_NAMESPACED_OPERATIONS
            and plan.plan_family == expected_family
            and plan.operational.family == expected_family
            and plan.operational.operation == plan.operation.value
        )

    def _path_for_plan(self, plan: ChangePlan) -> Path:
        if self._is_operational(plan):
            return self._path(plan.plan_id, operational=True)
        if plan.contract_version >= 3:
            raise ChangePlanStorageError(
                "Unsupported governance storage contract"
            )
        return self._path(plan.plan_id)

    def save(self, plan: ChangePlan) -> None:
        path = self._path_for_plan(plan)
        other = (
            self._path(plan.plan_id)
            if path.parent == self.operational_root
            else self._path(plan.plan_id, operational=True)
        )
        temporary = path.with_suffix(
            f".tmp-{os.getpid()}-{threading.get_ident()}"
        )
        payload = json.dumps(
            plan.to_dict(), sort_keys=True, separators=(",", ":")
        )
        try:
            with self._lock:
                self._ensure_navigation_index()
                if other.exists():
                    raise ChangePlanStorageError(
                        "Ambiguous governance record identifier"
                    )
                with open(temporary, "x", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
                self._put_entry(plan)
                self._mark_navigation_current()
        except ChangePlanStorageError:
            raise
        except OSError as exc:
            self.write_failures += 1
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ChangePlanStorageError(
                "Atomic governance storage write failed"
            ) from exc

    def get(self, plan_id: str) -> ChangePlan | None:
        paths = self._read_paths(plan_id)
        if paths is None:
            return None
        with self._lock:
            existing = [path for path in paths if path.exists()]
            if len(existing) > 1:
                raise ChangePlanStorageError(
                    "Ambiguous governance record identifier"
                )
            if not existing:
                return self._load(paths[0])
            plan = self._load(existing[0])
            if plan is not None:
                entry = self._navigation_entry(plan)
                if self._entries.get(plan.plan_id) != entry:
                    self._put_entry(plan)
                    self._mark_navigation_current()
            return plan

    def _load(self, path: Path) -> ChangePlan | None:
        try:
            with self._lock:
                value = json.loads(path.read_text(encoding="utf-8"))
            plan = ChangePlan.from_dict(value)
            self.records_deserialized += 1
            if is_terminal_plan(plan):
                self.terminal_records_deserialized += 1
            return plan
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ChangePlanStorageError("Governance record read failed") from exc
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self._quarantine(path)
            raise ChangePlanStorageError("Governance record is corrupt") from exc

    def list(self) -> list[ChangePlan]:
        plans, _evidence = self._scan_and_rebuild(
            count_full_history_scan=True
        )
        return sorted(plans, key=lambda plan: plan.created_at, reverse=True)

    def _quarantine(self, path: Path) -> None:
        self.corruption_count += 1
        self._remove_entry(path.stem)
        self.generation += 1
        self.index_invalidation_count += 1
        try:
            quarantine = (
                self.operational_quarantine
                if path.parent == self.operational_root
                else self.quarantine
            )
            destination = quarantine / (
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
                updated = datetime.fromisoformat(entry.updated_at)
            except ValueError:
                continue
            if updated < cutoff:
                try:
                    path = self._path(
                        entry.plan_id, operational=entry.operational
                    )
                    path.unlink(missing_ok=True)
                    with self._lock:
                        self._remove_entry(entry.plan_id)
                        self.generation += 1
                        self.index_update_count += 1
                    removed += 1
                except OSError:
                    self.write_failures += 1
        return removed

    def recover_incomplete(self, timestamp: str) -> int:
        recovered = 0
        for plan_id in self.active_plan_ids():
            plan = self.get(plan_id)
            if plan is None:
                continue
            if plan.status not in {PlanStatus.APPLYING}:
                continue
            if (
                plan.contract_version == 3
                and plan.operation in OPERATIONAL_OPERATIONS
                and plan.operational is not None
            ):
                dispatch = plan.operational.dispatch
                if dispatch.get("attempt_count") == 1:
                    plan.status = PlanStatus.VERIFICATION_REQUIRED
                    plan.execution_outcome = "indeterminate"
                    plan.operational.final_outcome = "verification_required"
                    plan.operational.verification.status = (
                        "verification_required"
                    )
                    plan.operational.verification.evidence = {
                        "reason": "server_restart_after_dispatch",
                        "redispatch_performed": False,
                    }
                    plan.failure_information = {
                        "error_code": (
                            "backup_dispatch_indeterminate"
                            if plan.operation
                            == ChangeOperation.CREATE_FULL_BACKUP
                            else "operational_dispatch_indeterminate"
                        ),
                        "reason": (
                            "A dispatched operational apply was recovered after "
                            "restart and requires read-only verification."
                        ),
                    }
                else:
                    plan.status = PlanStatus.FAILED
                    plan.execution_outcome = "not_applied"
                    plan.failure_information = {
                        "error_code": (
                            "backup_creation_failed"
                            if plan.operation
                            == ChangeOperation.CREATE_FULL_BACKUP
                            else "operational_provider_unavailable"
                        ),
                        "reason": (
                            "An operational apply was interrupted before "
                            "provider dispatch."
                        ),
                    }
                plan.updated_at = timestamp
                self.save(plan)
                recovered += 1
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
        with self._lock:
            self._ensure_navigation_index()
            plan_count = len(self._entries)
        return {
            "configured": True,
            "status": "healthy",
            "total_plans": plan_count,
            "corruption_count": self.corruption_count,
            "write_failures": self.write_failures,
            "retention_days": self.retention_days,
            "navigation": self.navigation_metrics(),
        }
