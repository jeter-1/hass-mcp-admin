"""Bounded, sanitized script trace reads (HAMCP-232/145 batch, design §4b).

The runtime-only profile ``core.script_trace_read`` is admitted only by an
owner-signed registry.  Until then the gateway refuses both tools
``pending_signed_registry_admission`` before any Home Assistant request, and
this service independently refuses ``authority_unavailable`` unless the
profile is admitted and the request holds Core dispatch authority.

Closed vocabulary: ``trace/list`` and ``trace/get``, both with
``domain: "script"`` and the exact, validated script key as ``item_id``.  The
list-all form, ``trace/contexts`` and every ``trace/debug/*`` command are
never built.  Every result passes through the shared untrusted-data sanitizer
before output, exactly as the automation trace reads do.  A missing trace is
reported as not found; there is no fallback.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from .errors import (
    AuthorizationError,
    ErrorCode,
    GovernanceError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from .ha_core_readmission.routes import SCRIPT_TRACE_PROFILE
from .observability import METRICS
from .request_context import current_caller_id, current_telemetry
from .sanitization import redaction_summary, sanitize_untrusted_data
from .trace_normalization import normalize_trace_list

PROFILE = SCRIPT_TRACE_PROFILE
CORE_REQUIREMENTS = (PROFILE,)
PENDING_ADMISSION_REASON = "pending_signed_registry_admission"
PROVIDER = "direct_home_assistant_trace"
PROVIDER_CONTRACT = "compiled-core-script-trace-read-v1"
DOMAIN = "script"
LIST_COMMAND = "trace/list"
GET_COMMAND = "trace/get"
COMMANDS = frozenset({LIST_COMMAND, GET_COMMAND})
NEVER_SENT_COMMANDS = (
    "trace/contexts",
    "trace/debug/breakpoint/clear",
    "trace/debug/breakpoint/list",
    "trace/debug/breakpoint/set",
    "trace/debug/continue",
    "trace/debug/step",
    "trace/debug/stop",
    "trace/subscribe_breakpoints",
)
# A script key is ``cv.slug`` (script/config.py): slugify output only.
SCRIPT_ID_PATTERN = re.compile(r"[a-z0-9_]{1,128}\Z")
# Core issues ``random_uuid_hex()``; a restored trace keeps its stored id,
# so the charset is bounded rather than pinned to one generator.
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
MAX_STRING = 2_000


class ScriptTraceError(GovernanceError):
    """Only a closed reason reaches error and audit serialization."""

    REASONS = frozenset({
        "authority_unavailable",
        "invalid_arguments",
        "trace_not_found",
        "permission_failure",
        "provider_timeout",
        "provider_unavailable",
        "provider_error",
        "invalid_provider_response",
    })

    def __init__(self, reason: str):
        if reason not in self.REASONS:
            reason = "provider_error"
        code = {
            "authority_unavailable": ErrorCode.PROVIDER_UNAVAILABLE,
            "invalid_arguments": ErrorCode.INVALID_REQUEST,
            "trace_not_found": ErrorCode.RESOURCE_NOT_FOUND,
            "permission_failure": ErrorCode.AUTHORIZATION_FAILURE,
            "provider_timeout": ErrorCode.HA_TIMEOUT,
            "provider_unavailable": ErrorCode.HA_UNAVAILABLE,
        }.get(reason, ErrorCode.HA_API_ERROR)
        details: dict[str, Any] = {"reason": reason, "fallback_occurred": False}
        if reason == "authority_unavailable":
            details["core_capability"] = PROFILE
        super().__init__(code, details=details)
        self.reason = reason


def validate_script_id(value: Any) -> str:
    if not isinstance(value, str) or not SCRIPT_ID_PATTERN.fullmatch(value):
        raise ScriptTraceError("invalid_arguments")
    return value


def validate_run_id(value: Any) -> str:
    if not isinstance(value, str) or not RUN_ID_PATTERN.fullmatch(value):
        raise ScriptTraceError("invalid_arguments")
    return value


def list_payload(script_id: str) -> dict[str, Any]:
    return {"type": LIST_COMMAND, "domain": DOMAIN, "item_id": validate_script_id(script_id)}


def get_payload(script_id: str, run_id: str) -> dict[str, Any]:
    return {
        "type": GET_COMMAND,
        "domain": DOMAIN,
        "item_id": validate_script_id(script_id),
        "run_id": validate_run_id(run_id),
    }


class ScriptTraceService:
    def __init__(self, client, core_runtime, *, known_secrets: tuple[str, ...] = ()):
        self.client = client
        self.core_runtime = core_runtime
        self.known_secrets = tuple(secret for secret in known_secrets if secret)

    def authority(self) -> Any:
        """Require the admitted profile and this request's dispatch authority."""

        status = self.core_runtime.route_status(CORE_REQUIREMENTS)
        telemetry = current_telemetry()
        if (status.get("available") is not True or telemetry is None
                or telemetry.core_dispatch_authorizer is None
                or not telemetry.authorize_core_dispatch()):
            raise ScriptTraceError("authority_unavailable")
        return status.get("generation")

    async def _command(self, payload: dict[str, Any]) -> Any:
        if payload.get("type") not in COMMANDS or payload.get("domain") != DOMAIN:
            raise ScriptTraceError("invalid_arguments")  # unreachable by construction
        if current_caller_id() == "anonymous":
            raise ScriptTraceError("authority_unavailable")
        generation = self.authority()
        failure, result = None, None
        try:
            result = await self.client.command(payload)
        except asyncio.CancelledError:
            raise
        except AuthorizationError:
            failure = "permission_failure"
        except HomeAssistantTimeoutError:
            failure = "provider_timeout"
        except HomeAssistantUnavailableError:
            failure = "provider_unavailable"
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            failure = (
                "trace_not_found"
                if payload["type"] == GET_COMMAND and details.get("status") == 404
                else "provider_error"
            )
        except Exception:
            # Exception text and provider values never enter the result.
            failure = "provider_error"
        METRICS.record_provider_result(
            "direct_ha_api", "failed" if failure else "complete", dispatched=True,
        )
        # The admission generation must hold across the read.
        if self.authority() != generation:
            raise ScriptTraceError("authority_unavailable")
        if failure is not None:
            raise ScriptTraceError(failure)
        return result

    async def list_traces(self, script_id: str) -> dict[str, Any]:
        payload = list_payload(script_id)
        result = await self._command(payload)
        if not isinstance(result, list):
            raise ScriptTraceError("invalid_provider_response")
        sanitation = sanitize_untrusted_data(
            result, known_secrets=self.known_secrets, max_string=MAX_STRING
        )
        if not isinstance(sanitation.value, list):
            raise ScriptTraceError("invalid_provider_response")
        normalized = normalize_trace_list(
            sanitation.value, sanitization_failed_closed=sanitation.failed_closed
        )
        return {
            "script_id": payload["item_id"],
            "traces": [header.public() for header in normalized.headers],
            "upstream_runs_returned": normalized.upstream_runs_returned,
            "runs_parsed_successfully": normalized.runs_parsed_successfully,
            "malformed_entries": normalized.malformed_entries,
            "source_truncated": normalized.source_truncated,
            "sanitization_failed_closed": normalized.sanitization_failed_closed,
            "redaction": redaction_summary(sanitation),
            "warnings": list(normalized.warnings),
        }

    async def get_trace(self, script_id: str, run_id: str) -> dict[str, Any]:
        payload = get_payload(script_id, run_id)
        result = await self._command(payload)
        if not isinstance(result, dict):
            raise ScriptTraceError("invalid_provider_response")
        sanitation = sanitize_untrusted_data(
            result, known_secrets=self.known_secrets, max_string=MAX_STRING
        )
        if not isinstance(sanitation.value, dict):
            raise ScriptTraceError("invalid_provider_response")
        return {
            "script_id": payload["item_id"],
            "run_id": payload["run_id"],
            "trace": sanitation.value,
            "sanitization_failed_closed": sanitation.failed_closed,
            "redaction": redaction_summary(sanitation),
        }


class ScriptTraceRuntime:
    def __init__(self) -> None:
        self.service: ScriptTraceService | None = None

    def configure(self, client, core_runtime, settings) -> None:
        self.service = ScriptTraceService(
            client, core_runtime,
            known_secrets=(settings.access_secret, settings.ha_token),
        )

    def require(self) -> ScriptTraceService:
        if self.service is None:
            raise ScriptTraceError("authority_unavailable")
        return self.service


SCRIPT_TRACES = ScriptTraceRuntime()


__all__ = [
    "COMMANDS",
    "CORE_REQUIREMENTS",
    "DOMAIN",
    "NEVER_SENT_COMMANDS",
    "PENDING_ADMISSION_REASON",
    "PROFILE",
    "PROVIDER",
    "PROVIDER_CONTRACT",
    "RUN_ID_PATTERN",
    "SCRIPT_ID_PATTERN",
    "SCRIPT_TRACES",
    "ScriptTraceError",
    "ScriptTraceRuntime",
    "ScriptTraceService",
    "get_payload",
    "list_payload",
    "validate_run_id",
    "validate_script_id",
]
