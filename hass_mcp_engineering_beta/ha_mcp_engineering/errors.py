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
    UNSUPPORTED_OPERATION = "unsupported_operation"
    CONFIGURATION_CONFLICT = "configuration_conflict"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    INTERNAL_SERVER_ERROR = "internal_server_error"


@dataclass(frozen=True)
class ErrorDefinition:
    message: str
    retryable: bool
    http_status: int
    mcp_mapping: str


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
    ErrorCode.UNSUPPORTED_OPERATION: ErrorDefinition("The operation is unsupported.", False, 405, "method_not_found"),
    ErrorCode.CONFIGURATION_CONFLICT: ErrorDefinition("The configuration conflicts with current state.", False, 409, "invalid_request"),
    ErrorCode.RATE_LIMIT_EXCEEDED: ErrorDefinition("The request rate limit was exceeded.", True, 429, "server_error"),
    ErrorCode.INTERNAL_SERVER_ERROR: ErrorDefinition("An internal server error occurred.", False, 500, "internal_error"),
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
