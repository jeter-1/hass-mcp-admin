"""Stable v2 beta error taxonomy and safe exception mapping."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    AUTHENTICATION_FAILURE = "authentication_failure"
    AUTHORIZATION_FAILURE = "authorization_failure"
    INVALID_REQUEST = "invalid_request"
    VALIDATION_FAILURE = "validation_failure"
    HA_UNAVAILABLE = "home_assistant_unavailable"
    HA_API_ERROR = "home_assistant_api_error"
    HA_TIMEOUT = "home_assistant_timeout"
    ENTITY_NOT_FOUND = "entity_not_found"
    AUTOMATION_NOT_FOUND = "automation_not_found"
    RESOURCE_NOT_FOUND = "resource_not_found"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    CONFIGURATION_CONFLICT = "configuration_conflict"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    INTERNAL_SERVER_ERROR = "internal_server_error"
    CHANGE_PLAN_NOT_FOUND = "change_plan_not_found"
    CHANGE_PLAN_EXPIRED = "change_plan_expired"
    CHANGE_PLAN_NOT_APPROVED = "change_plan_not_approved"
    APPROVAL_HASH_MISMATCH = "approval_hash_mismatch"
    APPROVAL_ALREADY_CONSUMED = "approval_already_consumed"
    EXTERNAL_APPROVAL_REQUIRED = "external_approval_required"
    APPROVAL_AUTHORITY_MISMATCH = "approval_authority_mismatch"
    EXTERNAL_APPROVAL_INVALID = "external_approval_invalid"
    EXTERNAL_APPROVAL_EXPIRED = "external_approval_expired"
    CHANGE_PLAN_REJECTED = "change_plan_rejected"
    STALE_TARGET_STATE = "stale_target_state"
    CHANGE_IN_PROGRESS = "change_in_progress"
    UNSUPPORTED_CHANGE_OPERATION = "unsupported_change_operation"
    HIGH_RISK_CHANGE_REJECTED = "high_risk_change_rejected"
    AUTOMATION_VALIDATION_FAILED = "automation_validation_failed"
    AUTOMATION_APPLY_FAILED = "automation_apply_failed"
    AUTOMATION_VERIFICATION_FAILED = "automation_verification_failed"
    CONFIGURATION_VALIDATION_FAILED = "configuration_validation_failed"
    CONFIGURATION_APPLY_FAILED = "configuration_apply_failed"
    CONFIGURATION_VERIFICATION_FAILED = "configuration_verification_failed"
    CONFIGURATION_PARTIAL_FAILURE = "configuration_partial_failure"
    ROLLBACK_NOT_AVAILABLE = "rollback_not_available"
    ROLLBACK_APPROVAL_REQUIRED = "rollback_approval_required"
    ROLLBACK_FAILED = "rollback_failed"
    CHANGE_PLAN_STORAGE_ERROR = "change_plan_storage_error"
    INVALID_CURSOR = "invalid_cursor"
    STALE_CURSOR = "stale_cursor"
    ANALYSIS_UNAVAILABLE = "analysis_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_PROHIBITED = "provider_prohibited"
    UPSTREAM_DASHBOARD_NOT_CONFIGURED = "upstream_dashboard_not_configured"
    UPSTREAM_DASHBOARD_AUTHENTICATION_FAILED = (
        "upstream_dashboard_authentication_failed"
    )
    UPSTREAM_DASHBOARD_ENDPOINT_REJECTED = "upstream_dashboard_endpoint_rejected"
    UPSTREAM_DASHBOARD_CONNECTION_FAILED = "upstream_dashboard_connection_failed"
    UPSTREAM_DASHBOARD_TIMEOUT = "upstream_dashboard_timeout"
    UPSTREAM_DASHBOARD_PROTOCOL_ERROR = "upstream_dashboard_protocol_error"
    UPSTREAM_DASHBOARD_INVALID_RESPONSE = "upstream_dashboard_invalid_response"
    UPSTREAM_DASHBOARD_REQUIRED_TOOL_MISSING = (
        "upstream_dashboard_required_tool_missing"
    )
    UPSTREAM_DASHBOARD_SCHEMA_INCOMPATIBLE = (
        "upstream_dashboard_schema_incompatible"
    )
    UPSTREAM_DASHBOARD_SERVER_IDENTITY_MISMATCH = (
        "upstream_dashboard_server_identity_mismatch"
    )
    UPSTREAM_DASHBOARD_VERSION_MISMATCH = (
        "upstream_dashboard_version_mismatch"
    )
    UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH = (
        "upstream_dashboard_reviewed_contract_mismatch"
    )
    UPSTREAM_DASHBOARD_REVIEWED_ANNOTATION_MISMATCH = (
        "upstream_dashboard_reviewed_annotation_mismatch"
    )
    UPSTREAM_DASHBOARD_UNSUPPORTED_TRUST_PROFILE = (
        "upstream_dashboard_unsupported_trust_profile"
    )
    UPSTREAM_DASHBOARD_PROHIBITED_ARGUMENT = (
        "upstream_dashboard_prohibited_argument"
    )
    UPSTREAM_DASHBOARD_HASH_CONTRACT_MISMATCH = (
        "upstream_dashboard_hash_contract_mismatch"
    )
    UPSTREAM_DASHBOARD_UPSTREAM_ERROR = "upstream_dashboard_upstream_error"
    UPSTREAM_DASHBOARD_RESPONSE_TOO_LARGE = "upstream_dashboard_response_too_large"
    DASHBOARD_NOT_FOUND = "dashboard_not_found"
    UPSTREAM_DASHBOARD_INTERNAL_ERROR = "upstream_dashboard_internal_error"


@dataclass(frozen=True)
class ErrorDefinition:
    message: str
    retryable: bool
    http_status: int
    mcp_mapping: str
    safe_detail_fields: tuple[str, ...] = (
        "exception_type",
        "operation",
        "resource_id",
        "status",
        "endpoint_category",
    )


ERROR_CATALOG: dict[ErrorCode, ErrorDefinition] = {
    ErrorCode.AUTHENTICATION_FAILURE: ErrorDefinition("Authentication failed.", False, 404, "invalid_request"),
    ErrorCode.AUTHORIZATION_FAILURE: ErrorDefinition("The operation is not authorized.", False, 403, "invalid_request"),
    ErrorCode.INVALID_REQUEST: ErrorDefinition("The request is invalid.", False, 400, "invalid_request"),
    ErrorCode.VALIDATION_FAILURE: ErrorDefinition("Request validation failed.", False, 422, "invalid_params"),
    ErrorCode.HA_UNAVAILABLE: ErrorDefinition("Home Assistant is unavailable.", True, 503, "internal_error"),
    ErrorCode.HA_API_ERROR: ErrorDefinition("Home Assistant rejected the request.", False, 502, "internal_error"),
    ErrorCode.HA_TIMEOUT: ErrorDefinition("Home Assistant timed out.", True, 504, "internal_error"),
    ErrorCode.ENTITY_NOT_FOUND: ErrorDefinition("The entity was not found.", False, 404, "invalid_params"),
    ErrorCode.AUTOMATION_NOT_FOUND: ErrorDefinition("The automation was not found.", False, 404, "invalid_params"),
    ErrorCode.RESOURCE_NOT_FOUND: ErrorDefinition("The requested resource was not found.", False, 404, "invalid_params"),
    ErrorCode.UNSUPPORTED_OPERATION: ErrorDefinition("The operation is unsupported.", False, 405, "method_not_found"),
    ErrorCode.CONFIGURATION_CONFLICT: ErrorDefinition("The configuration conflicts with current state.", False, 409, "invalid_request"),
    ErrorCode.RATE_LIMIT_EXCEEDED: ErrorDefinition("The request rate limit was exceeded.", True, 429, "server_error"),
    ErrorCode.INTERNAL_SERVER_ERROR: ErrorDefinition("An internal server error occurred.", False, 500, "internal_error"),
    ErrorCode.CHANGE_PLAN_NOT_FOUND: ErrorDefinition("The change plan was not found.", False, 404, "invalid_params"),
    ErrorCode.DASHBOARD_NOT_FOUND: ErrorDefinition("The dashboard was not found.", False, 404, "invalid_params"),
    ErrorCode.CHANGE_PLAN_EXPIRED: ErrorDefinition("The change plan has expired.", False, 409, "invalid_request"),
    ErrorCode.CHANGE_PLAN_NOT_APPROVED: ErrorDefinition("The change plan is not approved.", False, 409, "invalid_request"),
    ErrorCode.APPROVAL_HASH_MISMATCH: ErrorDefinition("The approval does not match the immutable plan content.", False, 409, "invalid_request"),
    ErrorCode.APPROVAL_ALREADY_CONSUMED: ErrorDefinition("The approval has already been consumed.", False, 409, "invalid_request"),
    ErrorCode.EXTERNAL_APPROVAL_REQUIRED: ErrorDefinition("External Home Assistant administrator approval is required.", False, 409, "invalid_request"),
    ErrorCode.APPROVAL_AUTHORITY_MISMATCH: ErrorDefinition("The plan uses an approval authority that cannot authorize this release.", False, 409, "invalid_request"),
    ErrorCode.EXTERNAL_APPROVAL_INVALID: ErrorDefinition("The external approval challenge is invalid or no longer active.", False, 409, "invalid_request"),
    ErrorCode.EXTERNAL_APPROVAL_EXPIRED: ErrorDefinition("The external approval challenge has expired.", False, 409, "invalid_request"),
    ErrorCode.CHANGE_PLAN_REJECTED: ErrorDefinition("The change plan was rejected and cannot be reopened.", False, 409, "invalid_request"),
    ErrorCode.STALE_TARGET_STATE: ErrorDefinition("Home Assistant state changed after planning.", False, 409, "invalid_request"),
    ErrorCode.CHANGE_IN_PROGRESS: ErrorDefinition("Another governed change is in progress for this target.", True, 409, "server_error"),
    ErrorCode.UNSUPPORTED_CHANGE_OPERATION: ErrorDefinition("The requested change operation is unsupported.", False, 405, "method_not_found"),
    ErrorCode.HIGH_RISK_CHANGE_REJECTED: ErrorDefinition("High-risk changes cannot be approved or applied in this milestone.", False, 403, "invalid_request"),
    ErrorCode.AUTOMATION_VALIDATION_FAILED: ErrorDefinition("The proposed automation failed validation.", False, 422, "invalid_params"),
    ErrorCode.AUTOMATION_APPLY_FAILED: ErrorDefinition("Home Assistant could not apply the automation change.", False, 502, "internal_error"),
    ErrorCode.AUTOMATION_VERIFICATION_FAILED: ErrorDefinition("The stored automation did not match the approved configuration.", False, 409, "internal_error"),
    ErrorCode.CONFIGURATION_VALIDATION_FAILED: ErrorDefinition("The proposed configuration operation failed validation.", False, 422, "invalid_params"),
    ErrorCode.CONFIGURATION_APPLY_FAILED: ErrorDefinition("Home Assistant could not apply the configuration operation.", False, 502, "internal_error"),
    ErrorCode.CONFIGURATION_VERIFICATION_FAILED: ErrorDefinition("The stored resource did not match the approved configuration.", False, 409, "internal_error"),
    ErrorCode.CONFIGURATION_PARTIAL_FAILURE: ErrorDefinition("The ordered configuration plan stopped after a write attempt left the overall result partial or uncertain.", False, 409, "internal_error"),
    ErrorCode.ROLLBACK_NOT_AVAILABLE: ErrorDefinition("Rollback is not available for this change.", False, 409, "invalid_request"),
    ErrorCode.ROLLBACK_APPROVAL_REQUIRED: ErrorDefinition("Rollback requires a separate approval.", False, 409, "invalid_request"),
    ErrorCode.ROLLBACK_FAILED: ErrorDefinition("The governed rollback failed.", False, 502, "internal_error"),
    ErrorCode.CHANGE_PLAN_STORAGE_ERROR: ErrorDefinition("Governance storage is unavailable.", True, 503, "internal_error"),
    ErrorCode.INVALID_CURSOR: ErrorDefinition("The pagination cursor is invalid.", False, 400, "invalid_params"),
    ErrorCode.STALE_CURSOR: ErrorDefinition(
        "The pagination snapshot expired or its dependency index changed; restart pagination.",
        False,
        409,
        "invalid_request",
    ),
    ErrorCode.ANALYSIS_UNAVAILABLE: ErrorDefinition("Analysis evidence is unavailable.", True, 503, "internal_error"),
    ErrorCode.PROVIDER_UNAVAILABLE: ErrorDefinition("The required capability provider is unavailable.", False, 503, "internal_error"),
    ErrorCode.PROVIDER_TIMEOUT: ErrorDefinition("The capability provider timed out.", True, 504, "internal_error"),
    ErrorCode.PROVIDER_ERROR: ErrorDefinition("The capability provider failed.", True, 502, "internal_error"),
    ErrorCode.PROVIDER_PROHIBITED: ErrorDefinition("Provider policy prohibits this operation or fallback.", False, 403, "invalid_request"),
    ErrorCode.UPSTREAM_DASHBOARD_NOT_CONFIGURED: ErrorDefinition(
        "The upstream dashboard provider is not configured.",
        False,
        503,
        "internal_error",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_AUTHENTICATION_FAILED: ErrorDefinition(
        "The upstream dashboard provider rejected authentication.",
        False,
        502,
        "internal_error",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_ENDPOINT_REJECTED: ErrorDefinition(
        "The configured upstream dashboard endpoint or secret path was rejected.",
        False,
        502,
        "internal_error",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_CONNECTION_FAILED: ErrorDefinition(
        "The upstream dashboard provider could not be reached.",
        True,
        503,
        "internal_error",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_TIMEOUT: ErrorDefinition(
        "The upstream dashboard provider timed out.",
        True,
        504,
        "internal_error",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_PROTOCOL_ERROR: ErrorDefinition(
        "The upstream dashboard provider returned an incompatible MCP protocol response.",
        False,
        502,
        "internal_error",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_INVALID_RESPONSE: ErrorDefinition(
        "The upstream dashboard provider returned an invalid response.",
        False,
        502,
        "internal_error",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_REQUIRED_TOOL_MISSING: ErrorDefinition(
        "The required upstream dashboard read tool is unavailable.",
        False,
        503,
        "internal_error",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_SCHEMA_INCOMPATIBLE: ErrorDefinition(
        "The required upstream dashboard read schema is incompatible.",
        False,
        503,
        "internal_error",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_SERVER_IDENTITY_MISMATCH: ErrorDefinition(
        "The upstream dashboard server identity does not match the reviewed profile.",
        False,
        503,
        "internal_error",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_VERSION_MISMATCH: ErrorDefinition(
        "The upstream dashboard server version does not match the reviewed profile.",
        False,
        503,
        "internal_error",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH: ErrorDefinition(
        "The upstream dashboard tool contract does not match the reviewed profile.",
        False,
        503,
        "internal_error",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_ANNOTATION_MISMATCH: ErrorDefinition(
        "The upstream dashboard annotations do not match the reviewed profile.",
        False,
        503,
        "internal_error",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_UNSUPPORTED_TRUST_PROFILE: ErrorDefinition(
        "The upstream dashboard endpoint does not satisfy a supported trust profile.",
        False,
        503,
        "internal_error",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_PROHIBITED_ARGUMENT: ErrorDefinition(
        "The dashboard provider rejected a prohibited upstream argument.",
        False,
        403,
        "invalid_request",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_HASH_CONTRACT_MISMATCH: ErrorDefinition(
        "The upstream dashboard hash contract could not be verified.",
        False,
        502,
        "internal_error",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_UPSTREAM_ERROR: ErrorDefinition(
        "The upstream dashboard read failed.",
        True,
        502,
        "internal_error",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_RESPONSE_TOO_LARGE: ErrorDefinition(
        "The dashboard configuration exceeds the Engineering response limit.",
        False,
        413,
        "invalid_request",
    ),
    ErrorCode.UPSTREAM_DASHBOARD_INTERNAL_ERROR: ErrorDefinition(
        "The upstream dashboard provider encountered an internal error.",
        False,
        500,
        "internal_error",
    ),
}


class EngineeringServerError(RuntimeError):
    code = ErrorCode.INTERNAL_SERVER_ERROR

    def __init__(self, message: str | None = None, *, details: dict[str, Any] | None = None):
        definition = ERROR_CATALOG[self.code]
        super().__init__(message or definition.message)
        self.safe_message = message or definition.message
        self.details = details or {}

    @property
    def retryable(self) -> bool:
        return ERROR_CATALOG[self.code].retryable


class ConfigurationError(EngineeringServerError):
    code = ErrorCode.VALIDATION_FAILURE


class AuthenticationError(EngineeringServerError):
    code = ErrorCode.AUTHENTICATION_FAILURE


class AuthorizationError(EngineeringServerError):
    code = ErrorCode.AUTHORIZATION_FAILURE


class InvalidRequestError(EngineeringServerError):
    code = ErrorCode.INVALID_REQUEST


class HomeAssistantUnavailableError(EngineeringServerError):
    code = ErrorCode.HA_UNAVAILABLE


class HomeAssistantApiError(EngineeringServerError):
    code = ErrorCode.HA_API_ERROR


class HomeAssistantTimeoutError(EngineeringServerError):
    code = ErrorCode.HA_TIMEOUT


class EntityNotFoundError(EngineeringServerError):
    code = ErrorCode.ENTITY_NOT_FOUND


class AutomationNotFoundError(EngineeringServerError):
    code = ErrorCode.AUTOMATION_NOT_FOUND


class GovernanceError(EngineeringServerError):
    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ):
        self.code = code
        super().__init__(message, details=details)


class DashboardProviderError(EngineeringServerError):
    """Safe dashboard-provider error with a stable public code."""

    def __init__(
        self,
        code: ErrorCode,
        *,
        details: dict[str, Any] | None = None,
    ):
        if not code.value.startswith("upstream_dashboard_") and code != ErrorCode.DASHBOARD_NOT_FOUND:
            raise ValueError("DashboardProviderError requires a dashboard error code")
        self.code = code
        super().__init__(details=details)


# Compatibility name retained for the scaffold imports.
HomeAssistantError = HomeAssistantApiError


def error_definition(code: ErrorCode | str) -> ErrorDefinition:
    return ERROR_CATALOG[ErrorCode(code)]


def map_exception(exc: Exception) -> tuple[ErrorCode, str, bool, dict[str, Any]]:
    if isinstance(exc, EngineeringServerError):
        return exc.code, exc.safe_message, exc.retryable, dict(exc.details)
    if isinstance(exc, (ValueError, TypeError, json_error_types())):
        definition = ERROR_CATALOG[ErrorCode.INVALID_REQUEST]
        return ErrorCode.INVALID_REQUEST, definition.message, definition.retryable, {
            "exception_type": type(exc).__name__
        }
    definition = ERROR_CATALOG[ErrorCode.INTERNAL_SERVER_ERROR]
    return ErrorCode.INTERNAL_SERVER_ERROR, definition.message, definition.retryable, {
        "exception_type": type(exc).__name__
    }


def json_error_types():
    import json

    return json.JSONDecodeError
