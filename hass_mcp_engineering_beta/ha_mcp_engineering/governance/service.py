"""Approval, application, verification, rollback, and concurrency workflow."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import json
import time
from typing import Any, Callable
import uuid

from ..audit import AuditLogger
from ..clients.rest import ExpectedHttpStatus, HomeAssistantRestClient
from ..errors import ErrorCode, GovernanceError, HomeAssistantApiError
from ..logging_config import get_logger, log_event
from ..request_context import current_caller_id, current_request_id
from .models import (
    ApprovalState,
    ChangeApproval,
    ChangeEvent,
    ChangeOperation,
    ChangePlan,
    ChangeRollback,
    ChangeSnapshot,
    ChangeTarget,
    ChangeVerification,
    PlanStatus,
    RiskLevel,
)
from .normalize import normalize_automation, stable_hash, state_fingerprint, structured_diff
from .risk import classify_risk
from .storage import ChangePlanRepository, ChangePlanStorageError
from .validation import sanitize_context, validate_automation


class AutomationGateway:
    """Narrow Home Assistant boundary used by governance and test fakes."""

    def __init__(self, client: HomeAssistantRestClient):
        self.client = client

    async def get(self, automation_id: str) -> dict[str, Any] | None:
        value = await self.client.request(
            "GET",
            f"/config/automation/config/{automation_id}",
            expected_statuses=frozenset({404}),
        )
        if isinstance(value, ExpectedHttpStatus) and value.status == 404:
            return None
        if not isinstance(value, dict):
            raise HomeAssistantApiError(
                details={
                    "operation": "automation_config_read",
                    "resource_id": automation_id,
                    "endpoint_category": "config/automation",
                    "reason": "malformed_response",
                }
            )
        return value

    async def write(self, automation_id: str, config: dict[str, Any]) -> Any:
        return await self.client.request(
            "POST", f"/config/automation/config/{automation_id}", body=config
        )

    async def validate(self) -> Any:
        return await self.client.request("POST", "/config/core/check_config")


class ChangeGovernanceService:
    def __init__(
        self,
        repository: ChangePlanRepository,
        gateway: Any,
        audit: AuditLogger | None = None,
        *,
        now: Callable[[], datetime] | None = None,
        sensitive_values: tuple[str, ...] = (),
    ):
        self.repository = repository
        self.gateway = gateway
        self.audit = audit
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.sensitive_values = tuple(value for value in sensitive_values if value)
        self.logger = get_logger("governance")
        self._plan_locks: dict[str, asyncio.Lock] = {}
        self._target_locks: dict[str, asyncio.Lock] = {}
        self.repository.cleanup(now=self.now())
        self.repository.recover_incomplete(self._timestamp())

    def _timestamp(self) -> str:
        return self.now().isoformat()

    def _new_id(self) -> str:
        while True:
            candidate = uuid.uuid4().hex
            if self.repository.get(candidate) is None:
                return candidate

    @staticmethod
    def plan_hash(plan: ChangePlan) -> str:
        calculated_proposed_hash = stable_hash(
            normalize_automation(plan.proposed_config) or {}
        )
        immutable = {
            "plan_id": plan.plan_id,
            "plan_version": plan.plan_version,
            "operation": plan.operation.value,
            "target_type": plan.target_type,
            "target_id": plan.target_id,
            "expires_at": plan.expires_at,
            "current_state_fingerprint": plan.current_state_fingerprint,
            "proposed_config_hash": calculated_proposed_hash,
            "risk_level": plan.risk.level.value,
            "approval_kind": plan.approval.approval_kind,
            "rollback_expected_fingerprint": plan.rollback.expected_current_fingerprint,
        }
        return stable_hash(immutable)

    def _load(self, plan_id: str) -> ChangePlan:
        try:
            plan = self.repository.get(plan_id)
        except ChangePlanStorageError as exc:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_STORAGE_ERROR) from exc
        if plan is None:
            raise GovernanceError(
                ErrorCode.CHANGE_PLAN_NOT_FOUND, details={"resource_id": plan_id}
            )
        return plan

    def _save(self, plan: ChangePlan) -> None:
        plan.updated_at = self._timestamp()
        try:
            self.repository.save(plan)
        except ChangePlanStorageError as exc:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_STORAGE_ERROR) from exc

    def _record(
        self,
        plan: ChangePlan,
        event: str,
        result_status: str,
        *,
        error_code: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        request_id = current_request_id()
        caller_id = current_caller_id()
        plan.events.append(
            ChangeEvent(
                event=event,
                timestamp=self._timestamp(),
                request_id=request_id,
                caller_id=caller_id,
                result_status=result_status,
                error_code=error_code,
                duration_ms=duration_ms,
            )
        )
        safe = {
            "event": event,
            "request_id": request_id,
            "plan_id": plan.plan_id,
            "target_type": plan.target_type,
            "target_id": plan.target_id,
            "operation": plan.operation.value,
            "risk_level": plan.risk.level.value,
            "result_status": result_status,
            "error_code": error_code,
            "duration_ms": duration_ms,
            "caller_id": caller_id,
            "approval_state": plan.approval.state.value,
        }
        # Persist the event and lifecycle state before emitting a success audit.
        # If storage fails, the caller returns change_plan_storage_error and no
        # misleading success record is produced.
        self._save(plan)
        if self.audit:
            self.audit.write(safe)
        log_event(
            self.logger,
            logging.INFO if result_status == "success" else logging.WARNING,
            event,
            "Governed automation change lifecycle event.",
            context=safe,
        )

    def _expire_if_needed(self, plan: ChangePlan) -> bool:
        if plan.status in {
            PlanStatus.APPLIED,
            PlanStatus.ROLLED_BACK,
            PlanStatus.FAILED,
            PlanStatus.ROLLBACK_FAILED,
            PlanStatus.SUPERSEDED,
        }:
            return False
        if self.now() >= datetime.fromisoformat(plan.expires_at):
            plan.status = PlanStatus.EXPIRED
            plan.approval.state = ApprovalState.INVALIDATED
            self._record(plan, "change_plan_expired", "rejected", error_code=ErrorCode.CHANGE_PLAN_EXPIRED.value)
            return True
        return False

    def _public(self, plan: ChangePlan, *, include_configs: bool = True) -> dict[str, Any]:
        value = plan.to_dict()
        value["plan_hash"] = self.plan_hash(plan)
        value["apply_allowed"] = (
            plan.status == PlanStatus.APPROVED
            and plan.approval.state == ApprovalState.APPROVED
            and plan.risk.apply_allowed
        )
        if not include_configs:
            value.pop("proposed_config", None)
            value.pop("current_config", None)
            value.pop("normalized_proposed_config", None)
            value.pop("normalized_current_config", None)
            value.pop("snapshot", None)
            value.pop("events", None)
        return value

    async def create_plan(
        self,
        *,
        title: str,
        description: str,
        operation: str,
        automation_id: str,
        proposed_config: dict[str, Any],
        expiration_minutes: int = 60,
        caller_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            change_operation = ChangeOperation(operation)
        except ValueError as exc:
            raise GovernanceError(ErrorCode.UNSUPPORTED_CHANGE_OPERATION) from exc
        expiration_minutes = max(5, min(int(expiration_minutes), 1440))
        valid, errors, warnings = validate_automation(automation_id, proposed_config)
        encoded_proposal = json.dumps(proposed_config, default=str)
        if any(secret in encoded_proposal for secret in self.sensitive_values):
            raise GovernanceError(
                ErrorCode.AUTOMATION_VALIDATION_FAILED,
                details={"validation_errors": ["The proposal contains prohibited sensitive data."]},
            )
        if any(
            "cannot be persisted" in error
            for error in errors
        ):
            raise GovernanceError(
                ErrorCode.AUTOMATION_VALIDATION_FAILED,
                details={"validation_errors": ["The proposal contains prohibited sensitive data."]},
            )
        current = await self.gateway.get(automation_id) if valid else None
        failure_code = ErrorCode.AUTOMATION_VALIDATION_FAILED
        if valid and change_operation == ChangeOperation.CREATE_AUTOMATION and current is not None:
            errors.append("automation_id already exists")
            valid = False
            failure_code = ErrorCode.CONFIGURATION_CONFLICT
        if valid and change_operation == ChangeOperation.UPDATE_AUTOMATION and current is None:
            errors.append("automation_id does not exist")
            valid = False
            failure_code = ErrorCode.AUTOMATION_NOT_FOUND

        normalized_proposed = normalize_automation(proposed_config) or {}
        normalized_current = normalize_automation(current)
        diff = structured_diff(current, proposed_config)
        if valid and change_operation == ChangeOperation.UPDATE_AUTOMATION and not diff["has_changes"]:
            return {
                "outcome": "no_change",
                "plan_created": False,
                "target_type": "automation",
                "target_id": automation_id,
                "dry_run_results": diff,
                "apply_allowed": False,
            }

        now = self.now()
        risk = classify_risk(change_operation, diff, proposed_config)
        plan = ChangePlan(
            plan_id=self._new_id(),
            plan_version=1,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=expiration_minutes)).isoformat(),
            status=PlanStatus.AWAITING_APPROVAL if valid else PlanStatus.VALIDATION_FAILED,
            title=title[:160],
            description=description[:1000],
            requested_by=current_caller_id(),
            target=ChangeTarget("automation", automation_id),
            operation=change_operation,
            proposed_config=proposed_config,
            current_config=current,
            normalized_proposed_config=normalized_proposed,
            normalized_current_config=normalized_current,
            current_state_fingerprint=state_fingerprint(current),
            proposed_config_hash=stable_hash(normalized_proposed),
            risk=risk,
            warnings=warnings,
            validation_results={"valid": valid, "errors": errors},
            dry_run_results=diff,
            rollback=ChangeRollback(
                available=False,
                status=("not_yet_available" if change_operation == ChangeOperation.UPDATE_AUTOMATION else "unavailable_for_create"),
            ),
            caller_context=sanitize_context(caller_context, self.sensitive_values),
        )
        self._record(
            plan,
            "change_plan_created" if valid else "change_plan_validation_failed",
            "success" if valid else "failure",
            error_code=None if valid else failure_code.value,
        )
        self._supersede_prior(plan)
        if not valid:
            raise GovernanceError(
                failure_code,
                details={"resource_id": plan.plan_id, "validation_errors": errors},
            )
        return self._public(plan)

    def _supersede_prior(self, new_plan: ChangePlan) -> None:
        for plan in self.repository.list():
            if (
                plan.plan_id != new_plan.plan_id
                and plan.target_id == new_plan.target_id
                and plan.status in {PlanStatus.AWAITING_APPROVAL, PlanStatus.APPROVED}
            ):
                plan.status = PlanStatus.SUPERSEDED
                plan.approval.state = ApprovalState.INVALIDATED
                self._record(plan, "change_plan_superseded", "rejected")

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        plan = self._load(plan_id)
        self._expire_if_needed(plan)
        return self._public(plan)

    def list_plans(self, status: str = "", limit: int = 20) -> dict[str, Any]:
        plans = []
        for plan in self.repository.list():
            self._expire_if_needed(plan)
            if status and plan.status.value != status:
                continue
            plans.append(self._public(plan, include_configs=False))
            if len(plans) >= max(1, min(limit, 100)):
                break
        return {"count": len(plans), "plans": plans}

    def approve(self, plan_id: str, expected_plan_hash: str, approval_note: str = "") -> dict[str, Any]:
        plan = self._load(plan_id)
        if self._expire_if_needed(plan):
            raise GovernanceError(ErrorCode.CHANGE_PLAN_EXPIRED)
        if plan.approval.state in {ApprovalState.APPROVED, ApprovalState.CONSUMED}:
            raise GovernanceError(ErrorCode.APPROVAL_ALREADY_CONSUMED)
        if plan.status not in {PlanStatus.AWAITING_APPROVAL, PlanStatus.ROLLBACK_PENDING}:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_NOT_APPROVED)
        if not plan.validation_results.get("valid"):
            raise GovernanceError(ErrorCode.AUTOMATION_VALIDATION_FAILED)
        if plan.risk.level == RiskLevel.HIGH:
            self._record(plan, "change_apply_rejected", "rejected", error_code=ErrorCode.HIGH_RISK_CHANGE_REJECTED.value)
            raise GovernanceError(ErrorCode.HIGH_RISK_CHANGE_REJECTED)
        calculated = self.plan_hash(plan)
        if expected_plan_hash != calculated:
            raise GovernanceError(ErrorCode.APPROVAL_HASH_MISMATCH)
        plan.approval = ChangeApproval(
            state=ApprovalState.APPROVED,
            approved_at=self._timestamp(),
            approving_caller_id=current_caller_id(),
            approval_note=approval_note[:500] or None,
            bound_plan_hash=calculated,
            approval_kind="rollback" if plan.status == PlanStatus.ROLLBACK_PENDING else "apply",
        )
        if plan.status == PlanStatus.AWAITING_APPROVAL:
            plan.status = PlanStatus.APPROVED
            event = "change_plan_approved"
        else:
            plan.rollback.approved_at = plan.approval.approved_at
            event = "rollback_approved"
        self._record(plan, event, "success")
        return {
            "status": "approved",
            "plan_id": plan.plan_id,
            "approval_timestamp": plan.approval.approved_at,
            "expires_at": plan.expires_at,
            "bound_plan_hash": calculated,
            "approval_kind": plan.approval.approval_kind,
            "remaining_restrictions": [
                "Approval is single-use.",
                "Current Home Assistant state must still match the plan fingerprint.",
            ],
        }

    async def apply(self, plan_id: str, expected_plan_hash: str = "") -> dict[str, Any]:
        plan_lock = self._plan_locks.setdefault(plan_id, asyncio.Lock())
        async with plan_lock:
            plan = self._load(plan_id)
            target_lock = self._target_locks.setdefault(plan.target_id, asyncio.Lock())
            if target_lock.locked():
                self._record(
                    plan,
                    "change_apply_rejected",
                    "rejected",
                    error_code=ErrorCode.CHANGE_IN_PROGRESS.value,
                )
                raise GovernanceError(ErrorCode.CHANGE_IN_PROGRESS)
            async with target_lock:
                return await self._apply_locked(plan, expected_plan_hash)

    async def _apply_locked(self, plan: ChangePlan, expected_plan_hash: str) -> dict[str, Any]:
        started = time.perf_counter()
        if self._expire_if_needed(plan):
            raise GovernanceError(ErrorCode.CHANGE_PLAN_EXPIRED)
        if plan.risk.level == RiskLevel.HIGH:
            self._reject_apply(plan, ErrorCode.HIGH_RISK_CHANGE_REJECTED)
        current = await self.gateway.get(plan.target_id)
        if plan.status == PlanStatus.APPLIED:
            if state_fingerprint(current) == plan.proposed_config_hash:
                return {"status": "already_applied", "plan": self._public(plan, include_configs=False)}
            raise GovernanceError(ErrorCode.APPROVAL_ALREADY_CONSUMED)
        if plan.approval.state == ApprovalState.CONSUMED:
            self._reject_apply(plan, ErrorCode.APPROVAL_ALREADY_CONSUMED)
        if plan.status != PlanStatus.APPROVED or plan.approval.state != ApprovalState.APPROVED:
            self._reject_apply(plan, ErrorCode.CHANGE_PLAN_NOT_APPROVED)
        calculated = self.plan_hash(plan)
        if (
            stable_hash(normalize_automation(plan.proposed_config) or {})
            != plan.proposed_config_hash
            or plan.approval.bound_plan_hash != calculated
            or (expected_plan_hash and expected_plan_hash != calculated)
        ):
            self._reject_apply(plan, ErrorCode.APPROVAL_HASH_MISMATCH)
        if state_fingerprint(current) != plan.current_state_fingerprint:
            self._record(plan, "change_apply_rejected", "rejected", error_code=ErrorCode.STALE_TARGET_STATE.value)
            raise GovernanceError(ErrorCode.STALE_TARGET_STATE)

        plan.snapshot = ChangeSnapshot(self._timestamp(), current, state_fingerprint(current))
        plan.status = PlanStatus.APPLYING
        plan.apply_request_id = current_request_id()
        plan.approval.state = ApprovalState.CONSUMED
        plan.approval.consumed_at = self._timestamp()
        self._record(plan, "change_apply_started", "success")
        try:
            await self.gateway.write(plan.target_id, plan.proposed_config)
            actual = await self.gateway.get(plan.target_id)
        except Exception as exc:
            plan.status = PlanStatus.FAILED
            plan.failure_information = {"error_code": ErrorCode.AUTOMATION_APPLY_FAILED.value}
            self._record(plan, "change_apply_failed", "failure", error_code=ErrorCode.AUTOMATION_APPLY_FAILED.value)
            raise GovernanceError(ErrorCode.AUTOMATION_APPLY_FAILED) from exc

        duration = round((time.perf_counter() - started) * 1000, 3)
        actual_fingerprint = state_fingerprint(actual)
        desired_normalized = normalize_automation(plan.proposed_config) or {}
        mismatch = _mismatch_fields(desired_normalized, normalize_automation(actual) or {})
        if actual is None:
            mismatch.append("automation_existence")
        elif actual.get("id") is not None and str(actual.get("id")) != plan.target_id:
            mismatch.append("automation_id")
        config_check = await self._config_check()
        plan.verification = ChangeVerification(
            status="passed" if not mismatch and config_check == "valid" else "failed",
            checked_at=self._timestamp(),
            desired_fingerprint=plan.proposed_config_hash,
            actual_fingerprint=actual_fingerprint,
            config_check_status=config_check,
            mismatch_fields=mismatch,
            duration_ms=duration,
        )
        plan.post_apply_fingerprint = actual_fingerprint
        plan.rollback.available = plan.operation == ChangeOperation.UPDATE_AUTOMATION
        plan.rollback.status = "available" if plan.rollback.available else "unavailable_for_create"
        if plan.verification.status != "passed":
            plan.status = PlanStatus.VERIFICATION_FAILED
            plan.failure_information = {"error_code": ErrorCode.AUTOMATION_VERIFICATION_FAILED.value}
            self._record(plan, "change_verification_failed", "failure", error_code=ErrorCode.AUTOMATION_VERIFICATION_FAILED.value, duration_ms=duration)
            raise GovernanceError(
                ErrorCode.AUTOMATION_VERIFICATION_FAILED,
                details={"resource_id": plan.plan_id, "mismatch_fields": mismatch},
            )
        plan.status = PlanStatus.APPLIED
        plan.applied_at = self._timestamp()
        self._record(plan, "change_apply_succeeded", "success", duration_ms=duration)
        return {"status": "applied", "plan": self._public(plan, include_configs=False)}

    def _reject_apply(self, plan: ChangePlan, code: ErrorCode) -> None:
        self._record(
            plan,
            "change_apply_rejected",
            "rejected",
            error_code=code.value,
        )
        raise GovernanceError(code)

    async def _config_check(self) -> str:
        try:
            result = await self.gateway.validate()
        except Exception:
            return "failed"
        if isinstance(result, dict):
            if result.get("errors"):
                return "failed"
            return "valid" if result.get("result", "valid") == "valid" else "failed"
        return "valid" if str(result).lower() in {"valid", "ok", "none"} else "failed"

    async def rollback_change(self, plan_id: str, expected_plan_hash: str = "") -> dict[str, Any]:
        plan_lock = self._plan_locks.setdefault(plan_id, asyncio.Lock())
        async with plan_lock:
            plan = self._load(plan_id)
            if plan.operation == ChangeOperation.CREATE_AUTOMATION or not plan.snapshot:
                raise GovernanceError(ErrorCode.ROLLBACK_NOT_AVAILABLE)
            if plan.status in {PlanStatus.APPLIED, PlanStatus.VERIFICATION_FAILED}:
                plan.plan_version += 1
                plan.status = PlanStatus.ROLLBACK_PENDING
                plan.rollback.available = True
                plan.rollback.status = "awaiting_approval"
                plan.rollback.requested_at = self._timestamp()
                plan.rollback.expected_current_fingerprint = plan.post_apply_fingerprint
                plan.approval = ChangeApproval(approval_kind="rollback")
                self._record(plan, "rollback_requested", "success")
                return {
                    "status": "rollback_pending",
                    "plan_id": plan.plan_id,
                    "approval_required": True,
                    "plan_hash": self.plan_hash(plan),
                }
            if plan.status != PlanStatus.ROLLBACK_PENDING:
                raise GovernanceError(ErrorCode.ROLLBACK_NOT_AVAILABLE)
            if plan.approval.state != ApprovalState.APPROVED or plan.approval.approval_kind != "rollback":
                raise GovernanceError(ErrorCode.ROLLBACK_APPROVAL_REQUIRED)
            calculated = self.plan_hash(plan)
            if not expected_plan_hash or expected_plan_hash != calculated or plan.approval.bound_plan_hash != calculated:
                self._record(plan, "rollback_failed", "rejected", error_code=ErrorCode.APPROVAL_HASH_MISMATCH.value)
                raise GovernanceError(ErrorCode.APPROVAL_HASH_MISMATCH)
            target_lock = self._target_locks.setdefault(plan.target_id, asyncio.Lock())
            if target_lock.locked():
                self._record(plan, "rollback_failed", "rejected", error_code=ErrorCode.CHANGE_IN_PROGRESS.value)
                raise GovernanceError(ErrorCode.CHANGE_IN_PROGRESS)
            async with target_lock:
                return await self._rollback_locked(plan)

    async def _rollback_locked(self, plan: ChangePlan) -> dict[str, Any]:
        current = await self.gateway.get(plan.target_id)
        if state_fingerprint(current) != plan.rollback.expected_current_fingerprint:
            self._record(plan, "rollback_failed", "rejected", error_code=ErrorCode.STALE_TARGET_STATE.value)
            raise GovernanceError(ErrorCode.STALE_TARGET_STATE)
        plan.approval.state = ApprovalState.CONSUMED
        plan.approval.consumed_at = self._timestamp()
        plan.rollback.status = "applying"
        plan.rollback.request_id = current_request_id()
        self._record(plan, "rollback_started", "success")
        try:
            await self.gateway.write(plan.target_id, plan.snapshot.config or {})
            actual = await self.gateway.get(plan.target_id)
        except Exception as exc:
            plan.status = PlanStatus.ROLLBACK_FAILED
            plan.rollback.status = "failed"
            plan.rollback.failure_code = ErrorCode.ROLLBACK_FAILED.value
            self._record(plan, "rollback_failed", "failure", error_code=ErrorCode.ROLLBACK_FAILED.value)
            raise GovernanceError(ErrorCode.ROLLBACK_FAILED) from exc
        expected_id = actual.get("id") if isinstance(actual, dict) else None
        if (
            actual is None
            or (expected_id is not None and str(expected_id) != plan.target_id)
            or state_fingerprint(actual) != plan.snapshot.fingerprint
            or await self._config_check() != "valid"
        ):
            plan.status = PlanStatus.ROLLBACK_FAILED
            plan.rollback.status = "verification_failed"
            plan.rollback.failure_code = ErrorCode.ROLLBACK_FAILED.value
            self._record(plan, "rollback_failed", "failure", error_code=ErrorCode.ROLLBACK_FAILED.value)
            raise GovernanceError(ErrorCode.ROLLBACK_FAILED)
        plan.status = PlanStatus.ROLLED_BACK
        plan.rollback.status = "rolled_back"
        plan.rollback.rolled_back_at = self._timestamp()
        self._record(plan, "rollback_succeeded", "success")
        return {"status": "rolled_back", "plan": self._public(plan, include_configs=False)}

    def health_summary(self) -> dict[str, Any]:
        plans = self.repository.list()
        storage = self.repository.health()
        return {
            "enabled": True,
            "storage": storage,
            "storage_status": storage["status"],
            "storage_corruption_count": storage["corruption_count"],
            "total_plans": len(plans),
            "plans_awaiting_approval": sum(plan.status == PlanStatus.AWAITING_APPROVAL for plan in plans),
            "expired_plans": sum(plan.status == PlanStatus.EXPIRED for plan in plans),
            "active_apply_operations": sum(lock.locked() for lock in self._target_locks.values()),
            "failed_apply_count": sum(
                (plan.failure_information or {}).get("error_code") == ErrorCode.AUTOMATION_APPLY_FAILED.value
                for plan in plans
            ),
            "rollback_pending_count": sum(plan.status == PlanStatus.ROLLBACK_PENDING for plan in plans),
            "last_successful_change_at": next(
                (plan.applied_at for plan in sorted(plans, key=lambda item: item.applied_at or "", reverse=True) if plan.applied_at),
                None,
            ),
        }


def _mismatch_fields(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    return [
        item["field"]
        for item in structured_diff(expected, actual)["changed_fields"]
    ]
