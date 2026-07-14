"""Application-configured governance runtime used by beta MCP tools."""

from __future__ import annotations

from typing import Any

from ..errors import ErrorCode, GovernanceError
from .service import AutomationGateway, ChangeGovernanceService
from .storage import ChangePlanRepository, ChangePlanStorageError


class GovernanceRuntime:
    def __init__(self):
        self.service: ChangeGovernanceService | None = None
        self.storage_error: str | None = None

    def configure(self, settings, audit, rest_client) -> None:
        try:
            repository = ChangePlanRepository(
                settings.governance_path,
                retention_days=settings.governance_retention_days,
            )
            self.service = ChangeGovernanceService(
                repository,
                AutomationGateway(rest_client),
                audit,
                sensitive_values=(settings.access_secret, settings.ha_token),
            )
            self.storage_error = None
        except ChangePlanStorageError:
            self.service = None
            self.storage_error = "change_plan_storage_error"

    def require(self) -> ChangeGovernanceService:
        if not self.service:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_STORAGE_ERROR)
        return self.service

    def health_summary(self) -> dict[str, Any]:
        if not self.service:
            return {
                "enabled": True,
                "storage": {"configured": False, "status": "error"},
                "storage_status": "error",
                "storage_corruption_count": 0,
                "error_code": self.storage_error,
                "total_plans": 0,
                "plans_awaiting_approval": 0,
                "external_approval_enabled": True,
                "ingress_approval_ui_configured": True,
                "approval_authority_version": 2,
                "pending_challenge_count": 0,
                "granted_approval_count": 0,
                "rejected_approval_count": 0,
                "expired_challenge_count": 0,
                "invalidated_challenge_count": 0,
                "approval_consumption_count": 0,
                "last_approval_failure_category": None,
                "rejected_plans": 0,
                "expired_plans": 0,
                "active_apply_operations": 0,
                "failed_apply_count": 0,
                "rollback_pending_count": 0,
                "last_successful_change_at": None,
            }
        return self.service.health_summary()


GOVERNANCE = GovernanceRuntime()
