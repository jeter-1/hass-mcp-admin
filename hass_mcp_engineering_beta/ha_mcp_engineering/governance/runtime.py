"""Application-configured governance runtime used by beta MCP tools."""

from __future__ import annotations

from typing import Any
import uuid

from ..clients.websocket import HomeAssistantWebSocketClient
from ..errors import ErrorCode, GovernanceError
from ..providers.supervisor_self import (
    SupervisorSelfAddonIdentityResolver,
)
from ..f3_dashboard.gateway import DashboardExecutionGateway
from .historical_policy import (
    HISTORICAL_POLICY_PROJECTION_MODEL,
    HISTORICAL_POLICY_PROJECTION_PROFILES,
)
from .operational_lifecycle import OperationalLifecycleGateway
from .resources import ConfigurationResourceGateway
from .operational import BackupAdministrationGateway
from .helper_state import HelperStateGateway
from .service import AutomationGateway, ChangeGovernanceService
from .approval_notifications import ApprovalNotificationManager
from .storage import ChangePlanRepository, ChangePlanStorageError
from .task_storage import (
    ExecutionTaskRepository,
    ExecutionTaskStorageError,
)


class _RuntimeGovernanceGateway:
    """Expose both immutable v1 and bounded v2 governance contracts."""

    def __init__(self, rest_client, websocket_client, resources=None):
        self._legacy = AutomationGateway(rest_client)
        self._resources = resources or ConfigurationResourceGateway(
            rest_client, websocket_client
        )

    async def get(self, automation_id: str):
        return await self._legacy.get(automation_id)

    async def write(self, *args):
        if len(args) == 2:
            automation_id, config = args
            return await self._legacy.write(automation_id, config)
        if len(args) == 4:
            action, resource_type, resource_id, config = args
            return await self._resources.write(
                action, resource_type, resource_id, config
            )
        raise TypeError("unsupported governance write signature")

    async def validate(self):
        return await self._legacy.validate()

    async def read(self, resource_type: str, resource_id: str):
        return await self._resources.read(resource_type, resource_id)

    async def validate_all(self):
        return await self._resources.validate_all()


class GovernanceRuntime:
    def __init__(self):
        self.service: ChangeGovernanceService | None = None
        self.storage_error: str | None = None

    def configure(
        self,
        settings,
        audit,
        rest_client,
        websocket_client=None,
        operational_provider=None,
        lifecycle_provider=None,
        runtime_snapshot=None,
        dashboard_provider=None,
    ) -> None:
        try:
            repository = ChangePlanRepository(
                settings.governance_path,
                retention_days=settings.governance_retention_days,
            )
            task_repository = ExecutionTaskRepository(
                settings.governance_path,
                retention_days=settings.governance_retention_days,
            )
            websocket_client = (
                websocket_client
                if websocket_client is not None
                else HomeAssistantWebSocketClient(settings)
            )
            self_addon_identity = (
                SupervisorSelfAddonIdentityResolver.from_settings(settings)
            )
            operational_gateway = (
                BackupAdministrationGateway(
                    operational_provider, websocket_client
                )
                if operational_provider is not None
                else None
            )
            lifecycle_gateway = (
                OperationalLifecycleGateway(
                    lifecycle_provider,
                    rest_client,
                    websocket_client,
                    configuration_validator=(
                        self._configuration_validator(
                            rest_client, websocket_client
                        )
                    ),
                    runtime_snapshot=runtime_snapshot or (lambda: {}),
                    process_instance_id=uuid.uuid4().hex,
                    self_addon_identity_resolver=(self_addon_identity.resolve),
                    sensitive_values=(
                        settings.access_secret,
                        settings.ha_token,
                    ),
                )
                if lifecycle_provider is not None
                else None
            )
            dashboard_gateway = (
                DashboardExecutionGateway(
                    dashboard_provider,
                    response_limit=settings.response_size_limit,
                )
                if dashboard_provider is not None
                else None
            )
            provider_identity_reader = (
                lifecycle_gateway.authoritative_provider_identity
                if lifecycle_gateway is not None
                else self._unavailable_provider_identity
            )
            resource_gateway = ConfigurationResourceGateway(
                rest_client, websocket_client
            )
            helper_state_gateway = HelperStateGateway(
                rest_client, websocket_client
            )
            approval_notifications = ApprovalNotificationManager(
                rest_client,
                audit,
                service=settings.approval_notification_service,
                timeout_seconds=settings.ha_timeout_seconds,
                addon_identity_resolver=self_addon_identity.resolve,
            )
            self.service = ChangeGovernanceService(
                repository,
                _RuntimeGovernanceGateway(
                    rest_client, websocket_client, resource_gateway
                ),
                audit,
                sensitive_values=(settings.access_secret, settings.ha_token),
                operational_gateway=operational_gateway,
                lifecycle_gateway=lifecycle_gateway,
                helper_state_gateway=helper_state_gateway,
                task_repository=task_repository,
                dashboard_gateway=dashboard_gateway,
                provider_identity_reader=provider_identity_reader,
                approval_notifications=approval_notifications,
            )
            from ..f3_runtime.runtime import F3RuntimeIntegration

            self.service.f3_runtime = F3RuntimeIntegration(
                service=self.service,
                storage_root=settings.governance_path,
                configuration_gateway=resource_gateway,
                backup_gateway=operational_gateway,
                lifecycle_gateway=lifecycle_gateway,
                helper_state_gateway=helper_state_gateway,
                dashboard_gateway=dashboard_gateway,
                provider_identity_reader=provider_identity_reader,
                retention_days=settings.governance_retention_days,
            )
            self.storage_error = None
        except ChangePlanStorageError:
            self.service = None
            self.storage_error = "change_plan_storage_error"
        except ExecutionTaskStorageError:
            self.service = None
            self.storage_error = "execution_task_storage_error"

    @staticmethod
    def _configuration_validator(rest_client, websocket_client):
        resources = ConfigurationResourceGateway(
            rest_client, websocket_client
        )
        return resources.validate_all

    @staticmethod
    async def _unavailable_provider_identity() -> dict[str, str]:
        raise RuntimeError("operational provider identity is unavailable")

    def require(self) -> ChangeGovernanceService:
        if not self.service:
            raise GovernanceError(
                ErrorCode.EXECUTION_TASK_STORAGE_ERROR
                if self.storage_error == "execution_task_storage_error"
                else ErrorCode.CHANGE_PLAN_STORAGE_ERROR
            )
        return self.service

    def health_summary(self) -> dict[str, Any]:
        if not self.service:
            operation_names = (
                "create_full_backup",
                "controlled_reload",
                "restart_addon",
                "restart_home_assistant",
                "set_input_boolean_state",
            )
            unavailable_operation = {
                "plans_created": 0,
                "apply_attempts": 0,
                "dispatch_attempts": 0,
                "dispatch_successes": 0,
                "verified_successes": 0,
                "pre_dispatch_failures": 0,
                "post_dispatch_failures": 0,
                "verification_failures": 0,
                "verification_pending_plans": 0,
                "indeterminate_outcomes": 0,
                "active_reconciliations": 0,
                "eligible_readback_reconciliations": 0,
                "no_blind_redispatch_preventions": 0,
                "last_successful_operation_timestamp": None,
                "last_failure_category": self.storage_error,
                "fallback_count": 0,
                "provider_identity": None,
                "provider_availability": "unavailable",
                "provider_contract_status": "unavailable_or_unverified",
            }
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
                "approval_authority_version": 3,
                "plans_by_policy_class": {
                    "standard_admin": 0,
                    "elevated_admin": 0,
                    "prohibited": 0,
                    "legacy_without_policy_snapshot": 0,
                    "projection_failed": 0,
                },
                "projection_failure_count": 0,
                "projection_failure_warning": None,
                "historical_policy_snapshot_compatibility": {
                    "model": HISTORICAL_POLICY_PROJECTION_MODEL,
                    "compatible_count": 0,
                    "profile_counts": dict.fromkeys(
                        HISTORICAL_POLICY_PROJECTION_PROFILES, 0
                    ),
                    "authorization_effect": "none_projection_only",
                },
                "policy_class_accounting_valid": True,
                "pending_plan_approvals": 0,
                "pending_elevated_acknowledgements": 0,
                "granted_elevated_acknowledgements": 0,
                "consumed_standard_approval_bundles": 0,
                "consumed_elevated_approval_bundles": 0,
                "prohibited_policy_decisions": 0,
                "policy_snapshot_mismatches": 0,
                "approval_principal_mismatches": 0,
                "approval_sequence_failures": 0,
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
                "execution_tasks": {
                    "storage": {
                        "configured": False,
                        "status": "error",
                        "record_count": 0,
                        "event_count": 0,
                        "corruption_count": 0,
                        "write_failures": 0,
                        "event_write_failures": 0,
                        "materialization_failures": 0,
                        "rehydration_attempts": 0,
                    },
                    "storage_configured": False,
                    "storage_status": "error",
                    "record_count": 0,
                    "event_count": 0,
                    "active_tasks_by_state": {},
                    "nonterminal_tasks": 0,
                    "tasks_verifying": 0,
                    "tasks_manual_review": 0,
                    "tasks_created": 0,
                    "verified_successes": 0,
                    "failed_pre_dispatch": 0,
                    "failed_post_dispatch": 0,
                    "cancellations": 0,
                    "manual_review_outcomes": 0,
                    "no_blind_redispatch_preventions": 0,
                    "rehydration_attempts": 0,
                    "reconciliation_runs": 0,
                    "event_write_failures": 0,
                    "materialization_failures": 0,
                    "last_task_failure_category": self.storage_error,
                },
                "restart_reconciliation": {
                    "active": False,
                    "plan_id": None,
                    "task_id": None,
                    "task_state": None,
                    "operation": None,
                    "attempt_count": 0,
                    "last_attempt_at": None,
                    "next_attempt_at": None,
                    "backoff_seconds": 0,
                    "evidence_deadline": None,
                    "last_result": self.storage_error,
                    "pending_eligible_record_count": 0,
                    "pending_backoff_record_count": 0,
                    "terminalized_record_count": 0,
                    "expired_record_count": 0,
                    "expensive_probe_count": 0,
                    "expensive_probes_avoided": 0,
                    "cheap_gate_rejection_count": 0,
                    "single_flight_collision_count": 0,
                    "manual_review_terminalization_count": 0,
                    "failure_count": 0,
                },
                "operational_administration": {
                    "plans_by_type": {
                        operation: 0 for operation in operation_names
                    },
                    "operations": {
                        operation: dict(unavailable_operation)
                        for operation in operation_names
                    },
                    "backup_plans_created": 0,
                    "backup_applies_attempted": 0,
                    "successful_backups": 0,
                    "failed_backups": 0,
                    "indeterminate_outcomes": 0,
                    "verification_failures": 0,
                    "active_operational_applies": 0,
                    "last_successful_backup_at": None,
                    "last_operational_failure_category": None,
                    "provider": {
                        "configured": False,
                        "operational_status": "unavailable",
                        "fallback_count": 0,
                        "fallback_policy": "none",
                    },
                    "lifecycle_provider": {
                        "configured": False,
                        "operational_status": "unavailable",
                        "fallback_count": 0,
                        "fallback_policy": "none",
                    },
                    "fallback_count": 0,
                    "rollback_available": False,
                },
            }
        return self.service.health_summary()


GOVERNANCE = GovernanceRuntime()
