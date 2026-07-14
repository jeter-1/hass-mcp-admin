"""Approval, application, verification, rollback, and concurrency workflow."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import logging
import json
import secrets
import time
from typing import Any, Callable
import uuid

from ..audit import AuditLogger
from ..clients.rest import ExpectedHttpStatus, HomeAssistantRestClient
from ..errors import ErrorCode, GovernanceError, HomeAssistantApiError
from ..logging_config import get_logger, log_event
from ..request_context import current_caller_id, current_request_id
from ..sanitization import sanitize_untrusted_data
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
from .normalize import (
    AUTOMATION_NORMALIZATION_VERSION,
    normalize_automation,
    stable_hash,
    state_fingerprint,
    structured_diff,
)
from .risk import classify_risk
from .storage import TERMINAL_STATUSES, ChangePlanRepository, ChangePlanStorageError
from .validation import sanitize_context, validate_automation


APPROVAL_AUTHORITY_VERSION = 2
APPROVAL_CHANNEL = "home_assistant_ingress"
APPROVAL_CHALLENGE_TTL = timedelta(minutes=15)
DEFAULT_APPROVER_PRINCIPAL = "home_assistant_admin_ingress"


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
            "normalization_version": plan.normalization_version,
            "risk_level": plan.risk.level.value,
            "approval_kind": plan.approval.approval_kind,
            "rollback_expected_fingerprint": plan.rollback.expected_current_fingerprint,
        }
        # Beta 24 plan hashes predate external approval authority. Preserve
        # those historical hashes exactly for readable audit/history while
        # requiring every executable Beta 25 plan to bind authority version 2.
        # Legacy active plans still fail closed before any provider access.
        if plan.approval.authority_version >= APPROVAL_AUTHORITY_VERSION:
            immutable["approval_authority_version"] = plan.approval.authority_version
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
        approval_principal: str | None = None,
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
            "approval_authority_version": plan.approval.authority_version,
            "approval_kind": plan.approval.approval_kind,
            "approval_channel": plan.approval.channel,
            "challenge_id": plan.approval.challenge_id,
            "approver_principal": approval_principal,
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
        # A terminal plan has already completed its lifecycle transition.  In
        # particular, an expired plan must never be "expired" again merely
        # because a read surface inspects it.
        if plan.status in TERMINAL_STATUSES:
            return False
        if self.now() >= datetime.fromisoformat(plan.expires_at):
            plan.status = PlanStatus.EXPIRED
            plan.approval.state = ApprovalState.INVALIDATED
            plan.approval.csrf_digest = None
            if plan.approval.challenge_id:
                self._record(
                    plan,
                    "external_approval_invalidated",
                    "rejected",
                    error_code=ErrorCode.CHANGE_PLAN_EXPIRED.value,
                )
            self._record(plan, "change_plan_expired", "rejected", error_code=ErrorCode.CHANGE_PLAN_EXPIRED.value)
            return True
        return False

    def _challenge_has_expired(self, plan: ChangePlan) -> bool:
        """Return the effective clock state for an external-pending challenge."""

        if plan.approval.state != ApprovalState.EXTERNAL_PENDING:
            return False
        try:
            return not plan.approval.challenge_expires_at or self.now() >= datetime.fromisoformat(
                plan.approval.challenge_expires_at
            )
        except ValueError:
            return True

    def _invalidate_terminal_challenge_if_needed(self, plan: ChangePlan) -> bool:
        """Reconcile an impossible persisted pending challenge on a terminal plan."""

        if (
            plan.status not in TERMINAL_STATUSES
            or plan.approval.state != ApprovalState.EXTERNAL_PENDING
        ):
            return False
        plan.approval.state = ApprovalState.INVALIDATED
        plan.approval.csrf_digest = None
        self._record(
            plan,
            "external_approval_invalidated",
            "rejected",
            error_code=(
                ErrorCode.CHANGE_PLAN_EXPIRED.value
                if plan.status == PlanStatus.EXPIRED
                else ErrorCode.EXTERNAL_APPROVAL_INVALID.value
            ),
        )
        return True

    def _resolve_lifecycle(self, plan: ChangePlan) -> tuple[bool, bool]:
        """Persist each effective plan or challenge expiry transition once.

        Every read and enforcement surface uses this resolver so an expired
        challenge cannot remain actionable until a later apply attempt.
        """

        plan_expired = self._expire_if_needed(plan)
        if self._invalidate_terminal_challenge_if_needed(plan):
            return plan_expired, False
        challenge_expired = self._expire_challenge_if_needed(plan)
        return plan_expired, challenge_expired

    def _public(self, plan: ChangePlan, *, include_configs: bool = True) -> dict[str, Any]:
        value = plan.to_dict()
        # CSRF material is private to the Ingress authority and must never be
        # returned through MCP plan reads or summaries.
        if isinstance(value.get("approval"), dict):
            value["approval"].pop("csrf_digest", None)
            value["approval"].pop("csrf_issued_at", None)
        value["plan_hash"] = self.plan_hash(plan)
        value["apply_allowed"] = (
            plan.status == PlanStatus.APPROVED
            and plan.approval.state == ApprovalState.APPROVED
            and plan.approval.authority_version == APPROVAL_AUTHORITY_VERSION
            and plan.approval.channel == APPROVAL_CHANNEL
            and bool(plan.approval.approver_principal)
            and plan.approval.principal_separation_enforced
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
            normalization_version=AUTOMATION_NORMALIZATION_VERSION,
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
            self._resolve_lifecycle(plan)
            if (
                plan.plan_id != new_plan.plan_id
                and plan.target_id == new_plan.target_id
                and plan.status in {
                    PlanStatus.AWAITING_APPROVAL,
                    PlanStatus.APPROVED,
                    PlanStatus.ROLLBACK_PENDING,
                }
            ):
                plan.status = PlanStatus.SUPERSEDED
                plan.approval.state = ApprovalState.INVALIDATED
                plan.approval.csrf_digest = None
                if plan.approval.challenge_id:
                    self._record(plan, "external_approval_invalidated", "rejected")
                self._record(plan, "change_plan_superseded", "rejected")

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        plan = self._load(plan_id)
        self._resolve_lifecycle(plan)
        return self._public(plan)

    def resolved_plans(self) -> list[ChangePlan]:
        """Return persisted plans after applying the shared effective lifecycle."""

        plans = self.repository.list()
        for plan in plans:
            self._resolve_lifecycle(plan)
        return plans

    def list_plans(self, status: str = "", limit: int = 20) -> dict[str, Any]:
        plans = []
        for plan in self.resolved_plans():
            if status and plan.status.value != status:
                continue
            plans.append(self._public(plan, include_configs=False))
            if len(plans) >= max(1, min(limit, 100)):
                break
        return {"count": len(plans), "plans": plans}

    def approve(self, plan_id: str, expected_plan_hash: str, approval_note: str = "") -> dict[str, Any]:
        """Request external approval without granting authority to the MCP caller."""

        plan = self._load(plan_id)
        self._resolve_lifecycle(plan)
        if plan.status == PlanStatus.EXPIRED:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_EXPIRED)
        if plan.status == PlanStatus.REJECTED or plan.approval.state == ApprovalState.REJECTED:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_REJECTED)
        if plan.approval.authority_version != APPROVAL_AUTHORITY_VERSION:
            raise GovernanceError(
                ErrorCode.APPROVAL_AUTHORITY_MISMATCH,
                details={"resource_id": plan.plan_id, "reason": "active_plan_must_be_recreated"},
            )
        if plan.approval.state in {ApprovalState.APPROVED, ApprovalState.CONSUMED}:
            raise GovernanceError(ErrorCode.APPROVAL_ALREADY_CONSUMED)
        if plan.status not in {PlanStatus.AWAITING_APPROVAL, PlanStatus.ROLLBACK_PENDING}:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_NOT_APPROVED)
        if not plan.validation_results.get("valid"):
            raise GovernanceError(ErrorCode.AUTOMATION_VALIDATION_FAILED)
        self._require_current_normalization(plan)
        if plan.risk.level == RiskLevel.HIGH:
            self._record(plan, "change_apply_rejected", "rejected", error_code=ErrorCode.HIGH_RISK_CHANGE_REJECTED.value)
            raise GovernanceError(ErrorCode.HIGH_RISK_CHANGE_REJECTED)
        calculated = self.plan_hash(plan)
        if expected_plan_hash != calculated:
            raise GovernanceError(ErrorCode.APPROVAL_HASH_MISMATCH)

        if self._active_challenge_matches(plan, calculated):
            return self._approval_pending_response(plan)
        if plan.approval.state == ApprovalState.EXTERNAL_PENDING:
            plan.approval.state = ApprovalState.INVALIDATED
            plan.approval.csrf_digest = None
            self._record(plan, "external_approval_invalidated", "rejected")

        approval_kind = "rollback" if plan.status == PlanStatus.ROLLBACK_PENDING else "apply"
        requested_at = self._timestamp()
        challenge_expires = min(
            self.now() + APPROVAL_CHALLENGE_TTL,
            datetime.fromisoformat(plan.expires_at),
        ).isoformat()
        sanitized_note = sanitize_untrusted_data(
            approval_note[:500],
            known_secrets=self.sensitive_values,
            max_string=500,
        ).value
        plan.approval = ChangeApproval(
            state=ApprovalState.EXTERNAL_PENDING,
            authority_version=APPROVAL_AUTHORITY_VERSION,
            channel=APPROVAL_CHANNEL,
            bound_plan_hash=calculated,
            approval_kind=approval_kind,
            challenge_id=secrets.token_urlsafe(24),
            challenge_requested_at=requested_at,
            challenge_expires_at=challenge_expires,
            challenge_plan_version=plan.plan_version,
            challenge_target_type=plan.target_type,
            challenge_target_id=plan.target_id,
            challenge_operation=plan.operation.value,
            challenge_risk_level=plan.risk.level.value,
            request_note=sanitized_note if isinstance(sanitized_note, str) and sanitized_note else None,
        )
        self._record(plan, "external_approval_requested", "success")
        return self._approval_pending_response(plan)

    def _approval_pending_response(self, plan: ChangePlan) -> dict[str, Any]:
        summary = {
            "status": "approval_pending",
            "plan_id": plan.plan_id,
            "approval_kind": plan.approval.approval_kind,
            "bound_plan_hash": plan.approval.bound_plan_hash,
            "external_approval_required": True,
            "approval_channel": APPROVAL_CHANNEL,
            "challenge_id": plan.approval.challenge_id,
            "requested_at": plan.approval.challenge_requested_at,
            "challenge_expires_at": plan.approval.challenge_expires_at,
            "approval_ui": "Open the HA MCP Engineering approval panel in Home Assistant.",
            "plan_expires_at": plan.expires_at,
            "plan_status": plan.status.value,
            "approval_state": plan.approval.state.value,
            "authority_version": APPROVAL_AUTHORITY_VERSION,
        }
        return summary

    def _active_challenge_matches(self, plan: ChangePlan, calculated: str) -> bool:
        approval = plan.approval
        return bool(
            approval.state == ApprovalState.EXTERNAL_PENDING
            and plan.status in {PlanStatus.AWAITING_APPROVAL, PlanStatus.ROLLBACK_PENDING}
            and approval.authority_version == APPROVAL_AUTHORITY_VERSION
            and approval.channel == APPROVAL_CHANNEL
            and approval.bound_plan_hash == calculated
            and approval.challenge_plan_version == plan.plan_version
            and approval.challenge_target_type == plan.target_type
            and approval.challenge_target_id == plan.target_id
            and approval.challenge_operation == plan.operation.value
            and approval.challenge_risk_level == plan.risk.level.value
            and approval.approval_kind
            == ("rollback" if plan.status == PlanStatus.ROLLBACK_PENDING else "apply")
            and not self._challenge_has_expired(plan)
        )

    def _expire_challenge_if_needed(self, plan: ChangePlan) -> bool:
        if not self._challenge_has_expired(plan):
            return False
        plan.approval.state = ApprovalState.EXPIRED
        plan.approval.csrf_digest = None
        self._record(
            plan,
            "external_approval_expired",
            "rejected",
            error_code=ErrorCode.EXTERNAL_APPROVAL_EXPIRED.value,
        )
        return True

    def pending_external_reviews(self) -> list[dict[str, Any]]:
        reviews: list[dict[str, Any]] = []
        for plan in self.resolved_plans():
            calculated = self.plan_hash(plan)
            if not self._active_challenge_matches(plan, calculated):
                continue
            reviews.append(self._review_summary(plan))
        return reviews

    def _review_summary(self, plan: ChangePlan) -> dict[str, Any]:
        changed_fields = []
        for item in plan.dry_run_results.get("changed_fields", [])[:50]:
            if not isinstance(item, dict):
                continue
            sanitized = sanitize_untrusted_data(
                item,
                known_secrets=self.sensitive_values,
                max_string=500,
            )
            item = sanitized.value if isinstance(sanitized.value, dict) else {}
            changed_fields.append(
                {
                    "field": str(item.get("field") or "")[:160],
                    "before": str(item.get("before") or "")[:500],
                    "after": str(item.get("after") or "")[:500],
                }
            )
        summary = {
            "plan_id": plan.plan_id,
            "title": plan.title[:160],
            "description": plan.description[:1000],
            "plan_hash": self.plan_hash(plan),
            "plan_version": plan.plan_version,
            "approval_kind": plan.approval.approval_kind,
            "operation": plan.operation.value,
            "target_type": plan.target_type,
            "target_id": plan.target_id,
            "risk_level": plan.risk.level.value,
            "expires_at": plan.expires_at,
            "challenge_id": plan.approval.challenge_id,
            "challenge_expires_at": plan.approval.challenge_expires_at,
            "request_note": str(
                sanitize_untrusted_data(
                    plan.approval.request_note or "",
                    known_secrets=self.sensitive_values,
                    max_string=500,
                ).value
            )[:500],
            "changed_fields": changed_fields,
            "warnings": [str(value)[:500] for value in plan.warnings[:20]],
            "validation_valid": bool(plan.validation_results.get("valid")),
            "apply_allowed": self._public(plan, include_configs=False)["apply_allowed"],
            "approval_state": plan.approval.state.value,
            "original_apply_timestamp": plan.applied_at if plan.approval.approval_kind == "rollback" else None,
            "current_post_apply_fingerprint": plan.post_apply_fingerprint if plan.approval.approval_kind == "rollback" else None,
            "snapshot_fingerprint": plan.snapshot.fingerprint if plan.snapshot and plan.approval.approval_kind == "rollback" else None,
            "rollback_target": plan.target_id if plan.approval.approval_kind == "rollback" else None,
        }
        sanitized = sanitize_untrusted_data(
            summary,
            known_secrets=self.sensitive_values,
            max_string=2_000,
        ).value
        if not isinstance(sanitized, dict):
            raise GovernanceError(ErrorCode.INTERNAL_SERVER_ERROR)
        return sanitized

    async def issue_external_csrf(self, plan_id: str, challenge_id: str) -> tuple[dict[str, Any], str]:
        lock = self._plan_locks.setdefault(plan_id, asyncio.Lock())
        async with lock:
            plan = self._load(plan_id)
            self._resolve_lifecycle(plan)
            if plan.status == PlanStatus.EXPIRED:
                raise GovernanceError(ErrorCode.CHANGE_PLAN_EXPIRED)
            if plan.approval.state == ApprovalState.EXPIRED:
                raise GovernanceError(ErrorCode.EXTERNAL_APPROVAL_EXPIRED)
            calculated = self.plan_hash(plan)
            if plan.approval.challenge_id != challenge_id or not self._active_challenge_matches(plan, calculated):
                raise GovernanceError(ErrorCode.EXTERNAL_APPROVAL_INVALID)
            nonce = secrets.token_urlsafe(32)
            plan.approval.csrf_digest = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
            plan.approval.csrf_issued_at = self._timestamp()
            self._record(plan, "external_approval_viewed", "success")
            return self._review_summary(plan), nonce

    async def decide_external_approval(
        self,
        *,
        plan_id: str,
        challenge_id: str,
        expected_plan_hash: str,
        approval_kind: str,
        csrf_nonce: str,
        decision: str,
        approver_principal: str,
    ) -> dict[str, Any]:
        """Perform the private Ingress-authority decision under the plan lock."""

        lock = self._plan_locks.setdefault(plan_id, asyncio.Lock())
        async with lock:
            plan = self._load(plan_id)
            self._resolve_lifecycle(plan)
            if plan.status == PlanStatus.EXPIRED:
                raise GovernanceError(ErrorCode.CHANGE_PLAN_EXPIRED)
            if plan.approval.state == ApprovalState.EXPIRED:
                raise GovernanceError(ErrorCode.EXTERNAL_APPROVAL_EXPIRED)
            calculated = self.plan_hash(plan)
            approval = plan.approval
            if approval.challenge_id != challenge_id or not self._active_challenge_matches(plan, calculated):
                self._reject_external_decision(plan, ErrorCode.EXTERNAL_APPROVAL_INVALID)
            if expected_plan_hash != calculated or approval.bound_plan_hash != calculated:
                self._reject_external_decision(plan, ErrorCode.APPROVAL_HASH_MISMATCH)
            if approval_kind != approval.approval_kind:
                self._reject_external_decision(plan, ErrorCode.EXTERNAL_APPROVAL_INVALID)
            csrf_digest = hashlib.sha256(csrf_nonce.encode("utf-8")).hexdigest()
            if not approval.csrf_digest or not hmac.compare_digest(approval.csrf_digest, csrf_digest):
                self._reject_external_decision(plan, ErrorCode.EXTERNAL_APPROVAL_INVALID)
            approval.csrf_digest = None
            approval.csrf_issued_at = None
            principal = (approver_principal or DEFAULT_APPROVER_PRINCIPAL)[:160]
            if decision == "approve":
                approval.state = ApprovalState.APPROVED
                approval.approved_at = self._timestamp()
                approval.approval_expires_at = plan.expires_at
                approval.channel = APPROVAL_CHANNEL
                approval.approver_principal = principal
                approval.principal_separation_enforced = True
                if approval.approval_kind == "apply":
                    plan.status = PlanStatus.APPROVED
                else:
                    plan.rollback.approved_at = approval.approved_at
                self._record(
                    plan,
                    "external_approval_granted",
                    "success",
                    approval_principal=principal,
                )
                return {"status": "approved", "plan_id": plan.plan_id, "approval_kind": approval.approval_kind}
            if decision == "reject":
                approval.state = ApprovalState.REJECTED
                approval.channel = APPROVAL_CHANNEL
                approval.approver_principal = principal
                approval.principal_separation_enforced = True
                plan.status = PlanStatus.REJECTED
                self._record(
                    plan,
                    "external_approval_rejected",
                    "rejected",
                    error_code=ErrorCode.CHANGE_PLAN_REJECTED.value,
                    approval_principal=principal,
                )
                return {"status": "rejected", "plan_id": plan.plan_id, "approval_kind": approval.approval_kind}
            self._reject_external_decision(plan, ErrorCode.EXTERNAL_APPROVAL_INVALID)

    def _reject_external_decision(self, plan: ChangePlan, code: ErrorCode) -> None:
        self._record(
            plan,
            "external_approval_decision_failed",
            "rejected",
            error_code=code.value,
        )
        raise GovernanceError(code)

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
        self._resolve_lifecycle(plan)
        if plan.status == PlanStatus.EXPIRED:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_EXPIRED)
        if plan.risk.level == RiskLevel.HIGH:
            self._reject_apply(plan, ErrorCode.HIGH_RISK_CHANGE_REJECTED)
        self._require_current_normalization(plan)
        if _automation_id_mismatch(plan.target_id, plan.proposed_config):
            self._reject_identity_mismatch(plan)
        if plan.status == PlanStatus.APPLIED:
            current = await self.gateway.get(plan.target_id)
            if (
                not _automation_id_mismatch(plan.target_id, current)
                and state_fingerprint(current) == plan.proposed_config_hash
            ):
                return {"status": "already_applied", "plan": self._public(plan, include_configs=False)}
            mismatch = ["automation_id"] if _automation_id_mismatch(plan.target_id, current) else []
            raise GovernanceError(
                ErrorCode.AUTOMATION_VERIFICATION_FAILED
                if mismatch
                else ErrorCode.APPROVAL_ALREADY_CONSUMED,
                details={"resource_id": plan.plan_id, "mismatch_fields": mismatch},
            )
        if plan.status == PlanStatus.REJECTED or plan.approval.state == ApprovalState.REJECTED:
            self._reject_apply(plan, ErrorCode.CHANGE_PLAN_REJECTED)
        if plan.approval.authority_version != APPROVAL_AUTHORITY_VERSION:
            self._reject_apply(plan, ErrorCode.APPROVAL_AUTHORITY_MISMATCH)
        if plan.approval.state == ApprovalState.CONSUMED:
            self._reject_apply(plan, ErrorCode.APPROVAL_ALREADY_CONSUMED)
        if not self._valid_external_approval(plan, "apply"):
            self._reject_apply(plan, ErrorCode.EXTERNAL_APPROVAL_REQUIRED)
        calculated = self.plan_hash(plan)
        if (
            stable_hash(normalize_automation(plan.proposed_config) or {})
            != plan.proposed_config_hash
            or plan.approval.bound_plan_hash != calculated
            or (expected_plan_hash and expected_plan_hash != calculated)
        ):
            self._reject_apply(plan, ErrorCode.APPROVAL_HASH_MISMATCH)
        current = await self.gateway.get(plan.target_id)
        if _automation_id_mismatch(plan.target_id, current):
            self._reject_identity_mismatch(plan)
        if state_fingerprint(current) != plan.current_state_fingerprint:
            self._record(plan, "change_apply_rejected", "rejected", error_code=ErrorCode.STALE_TARGET_STATE.value)
            raise GovernanceError(ErrorCode.STALE_TARGET_STATE)

        plan.snapshot = ChangeSnapshot(self._timestamp(), current, state_fingerprint(current))
        plan.status = PlanStatus.APPLYING
        plan.apply_request_id = current_request_id()
        plan.approval.state = ApprovalState.CONSUMED
        plan.approval.consumed_at = self._timestamp()
        self._record(plan, "external_approval_consumed", "success")
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
        elif _automation_id_mismatch(plan.target_id, actual):
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
        from ..dependency import DEPENDENCY_ANALYSIS
        DEPENDENCY_ANALYSIS.invalidate()
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

    def _reject_identity_mismatch(self, plan: ChangePlan) -> None:
        self._record(
            plan,
            "change_apply_rejected",
            "rejected",
            error_code=ErrorCode.AUTOMATION_VERIFICATION_FAILED.value,
        )
        raise GovernanceError(
            ErrorCode.AUTOMATION_VERIFICATION_FAILED,
            details={
                "resource_id": plan.plan_id,
                "mismatch_fields": ["automation_id"],
            },
        )

    def _valid_external_approval(self, plan: ChangePlan, approval_kind: str) -> bool:
        approval = plan.approval
        try:
            unexpired = bool(
                approval.approval_expires_at
                and self.now() < datetime.fromisoformat(approval.approval_expires_at)
            )
        except ValueError:
            unexpired = False
        return bool(
            plan.status
            == (PlanStatus.APPROVED if approval_kind == "apply" else PlanStatus.ROLLBACK_PENDING)
            and approval.state == ApprovalState.APPROVED
            and approval.authority_version == APPROVAL_AUTHORITY_VERSION
            and approval.channel == APPROVAL_CHANNEL
            and approval.approval_kind == approval_kind
            and approval.principal_separation_enforced
            and approval.approver_principal
            and approval.bound_plan_hash == self.plan_hash(plan)
            and unexpired
        )

    @staticmethod
    def _require_current_normalization(plan: ChangePlan) -> None:
        proposed_hash = stable_hash(normalize_automation(plan.proposed_config) or {})
        current_fingerprint = state_fingerprint(plan.current_config)
        if (
            plan.normalization_version != AUTOMATION_NORMALIZATION_VERSION
            or proposed_hash != plan.proposed_config_hash
            or current_fingerprint != plan.current_state_fingerprint
        ):
            raise GovernanceError(
                ErrorCode.APPROVAL_HASH_MISMATCH,
                details={
                    "resource_id": plan.plan_id,
                    "reason": "normalization_version_mismatch",
                },
            )

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
            self._resolve_lifecycle(plan)
            if plan.status == PlanStatus.EXPIRED:
                raise GovernanceError(ErrorCode.CHANGE_PLAN_EXPIRED)
            if plan.operation == ChangeOperation.CREATE_AUTOMATION or not plan.snapshot:
                raise GovernanceError(ErrorCode.ROLLBACK_NOT_AVAILABLE)
            if plan.status in {PlanStatus.APPLIED, PlanStatus.VERIFICATION_FAILED}:
                plan.plan_version += 1
                plan.status = PlanStatus.ROLLBACK_PENDING
                plan.rollback.available = True
                plan.rollback.status = "awaiting_approval"
                plan.rollback.requested_at = self._timestamp()
                plan.rollback.expected_current_fingerprint = plan.post_apply_fingerprint
                plan.approval = ChangeApproval(
                    authority_version=APPROVAL_AUTHORITY_VERSION,
                    approval_kind="rollback",
                )
                self._record(plan, "rollback_requested", "success")
                return {
                    "status": "rollback_pending",
                    "plan_id": plan.plan_id,
                    "approval_required": True,
                    "plan_hash": self.plan_hash(plan),
                }
            if plan.status != PlanStatus.ROLLBACK_PENDING:
                raise GovernanceError(ErrorCode.ROLLBACK_NOT_AVAILABLE)
            if plan.approval.authority_version != APPROVAL_AUTHORITY_VERSION:
                raise GovernanceError(ErrorCode.APPROVAL_AUTHORITY_MISMATCH)
            if not self._valid_external_approval(plan, "rollback"):
                self._record(
                    plan,
                    "rollback_failed",
                    "rejected",
                    error_code=ErrorCode.EXTERNAL_APPROVAL_REQUIRED.value,
                )
                raise GovernanceError(ErrorCode.EXTERNAL_APPROVAL_REQUIRED)
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
        self._record(plan, "external_approval_consumed", "success")
        plan.rollback.status = "applying"
        plan.rollback.request_id = current_request_id()
        self._record(plan, "rollback_started", "success")
        if _automation_id_mismatch(plan.target_id, plan.snapshot.config):
            plan.status = PlanStatus.ROLLBACK_FAILED
            plan.rollback.status = "verification_failed"
            plan.rollback.failure_code = ErrorCode.ROLLBACK_FAILED.value
            self._record(plan, "rollback_failed", "failure", error_code=ErrorCode.ROLLBACK_FAILED.value)
            raise GovernanceError(ErrorCode.ROLLBACK_FAILED)
        try:
            await self.gateway.write(plan.target_id, plan.snapshot.config or {})
            actual = await self.gateway.get(plan.target_id)
        except Exception as exc:
            plan.status = PlanStatus.ROLLBACK_FAILED
            plan.rollback.status = "failed"
            plan.rollback.failure_code = ErrorCode.ROLLBACK_FAILED.value
            self._record(plan, "rollback_failed", "failure", error_code=ErrorCode.ROLLBACK_FAILED.value)
            raise GovernanceError(ErrorCode.ROLLBACK_FAILED) from exc
        if (
            actual is None
            or _automation_id_mismatch(plan.target_id, actual)
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
        from ..dependency import DEPENDENCY_ANALYSIS
        DEPENDENCY_ANALYSIS.invalidate()
        self._record(plan, "rollback_succeeded", "success")
        return {"status": "rolled_back", "plan": self._public(plan, include_configs=False)}

    def health_summary(self) -> dict[str, Any]:
        plans = self.resolved_plans()
        storage = self.repository.health()
        events = [event.event for plan in plans for event in plan.events]
        approval_failures = sorted(
            (
                event
                for plan in plans
                for event in plan.events
                if event.event.startswith("external_approval") and event.error_code
            ),
            key=lambda event: event.timestamp,
            reverse=True,
        )
        return {
            "enabled": True,
            "storage": storage,
            "storage_status": storage["status"],
            "storage_corruption_count": storage["corruption_count"],
            "total_plans": len(plans),
            "plans_awaiting_approval": sum(plan.status == PlanStatus.AWAITING_APPROVAL for plan in plans),
            "external_approval_enabled": True,
            "ingress_approval_ui_configured": True,
            "approval_authority_version": APPROVAL_AUTHORITY_VERSION,
            "pending_challenge_count": sum(
                plan.approval.state == ApprovalState.EXTERNAL_PENDING
                and self._active_challenge_matches(plan, self.plan_hash(plan))
                for plan in plans
            ),
            "granted_approval_count": events.count("external_approval_granted"),
            "rejected_approval_count": events.count("external_approval_rejected"),
            "expired_challenge_count": events.count("external_approval_expired"),
            "invalidated_challenge_count": events.count("external_approval_invalidated"),
            "approval_consumption_count": events.count("external_approval_consumed"),
            "last_approval_failure_category": (
                approval_failures[0].error_code if approval_failures else None
            ),
            "rejected_plans": sum(plan.status == PlanStatus.REJECTED for plan in plans),
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


def _automation_id_mismatch(
    expected_automation_id: str, config: dict[str, Any] | None
) -> bool:
    """Check identity metadata independently from behavioral normalization."""

    return bool(
        isinstance(config, dict)
        and config.get("id") is not None
        and str(config["id"]) != expected_automation_id
    )
