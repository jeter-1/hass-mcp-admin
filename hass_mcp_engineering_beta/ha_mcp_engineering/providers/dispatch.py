"""Schema-preserving facilitator dispatch for canonical MCP handlers."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from typing import Any, Awaitable, Callable

from ..errors import ErrorCode, GovernanceError, map_exception
from ..models import FailureResponse, SuccessResponse
from ..observability import METRICS
from ..request_context import current_request_id, current_telemetry
from ..tool_framework import timing_since
from .models import (
    EvidenceRequest,
    ProviderCompleteness,
    ProviderFailureCategory,
    ProviderResult,
)
from .routing import (
    CapabilityRoute,
    RoutingPolicy,
    direct_ha_exception_for_tool,
    routing_for_tool,
)
from .standard_mcp import StandardHaMcpGateway


Action = Callable[[], Any | Awaitable[Any]]


class CanonicalProviderDispatcher:
    """Apply declared routing before invoking a canonical compatibility handler."""

    def __init__(self, standard_provider=None, policy: RoutingPolicy | None = None):
        self.standard_provider = standard_provider or StandardHaMcpGateway()
        self.policy = policy or RoutingPolicy()

    async def execute(
        self,
        tool_name: str,
        action: Action,
        *,
        arguments: dict[str, Any],
        response_limit: int,
    ) -> str:
        started = time.perf_counter()
        decision = routing_for_tool(tool_name, self.policy)
        telemetry = current_telemetry()
        provider_id = decision.preferred_provider or "policy"
        provider_result: ProviderResult | None = None
        try:
            if decision.route == CapabilityRoute.STANDARD_MCP_PREFERRED:
                provider_result = await self._standard(tool_name, decision.capability, arguments)
                if not provider_result.succeeded:
                    # A direct compatibility implementation exists, but policy does
                    # not authorize it as fallback for canonical delegated calls.
                    METRICS.record_prohibited_fallback()
                    raise _provider_error(provider_result)
                data, inner_warnings, inner_metadata = _normalize_payload(provider_result.data)
            elif decision.route in {CapabilityRoute.DIRECT_HA_REQUIRED, CapabilityRoute.TRANSITIONAL_DIRECT}:
                if not direct_ha_exception_for_tool(tool_name):
                    METRICS.record_provider_result("policy", "failed")
                    METRICS.record_prohibited_fallback()
                    raise GovernanceError(ErrorCode.PROVIDER_PROHIBITED)
                provider_result, data, inner_warnings, inner_metadata = await self._direct(
                    decision.capability, action
                )
            elif decision.route == CapabilityRoute.PROHIBITED:
                METRICS.record_provider_result("policy", "failed")
                METRICS.record_prohibited_fallback()
                raise GovernanceError(ErrorCode.PROVIDER_PROHIBITED)
            else:
                # Engineering-native tools are intentionally registered without
                # this wrapper; unsupported routes fail closed if encountered.
                METRICS.record_provider_result(provider_id, "failed")
                raise GovernanceError(ErrorCode.PROVIDER_ERROR)

            routing_metadata = _routing_metadata(decision.route, provider_result)
            metadata = {**inner_metadata, **routing_metadata}
            warnings = [*inner_warnings, *provider_result.warnings][:20]
            if telemetry:
                telemetry.result_status = "partial" if provider_result.completeness == ProviderCompleteness.PARTIAL else "success"
                telemetry.completeness = provider_result.completeness.value
            rendered = SuccessResponse(
                operation=tool_name,
                summary=f"Completed {tool_name} through facilitator provider routing.",
                data=data,
                warnings=warnings,
                metadata=metadata,
                timing=timing_since(started),
                request_id=current_request_id(),
            ).to_json(response_limit)
            if "... [truncated at " in rendered:
                METRICS.record_evidence_truncation()
            return rendered
        except Exception as exc:
            code, message, retryable, details = map_exception(exc)
            METRICS.record_error(code.value)
            if telemetry:
                telemetry.error_code = code.value
                telemetry.result_status = "failure"
                telemetry.completeness = (
                    provider_result.completeness.value if provider_result else "failed"
                )
            metadata = _routing_metadata(decision.route, provider_result, provider_id=provider_id)
            return FailureResponse(
                operation=tool_name,
                error=type(exc).__name__,
                error_code=code.value,
                message=message,
                details=details,
                retryable=retryable,
                warnings=(provider_result.warnings[:20] if provider_result else []),
                metadata=metadata,
                timing=timing_since(started),
                request_id=current_request_id(),
            ).to_json(response_limit)

    async def _standard(self, tool_name, capability, arguments) -> ProviderResult:
        started = time.perf_counter()
        try:
            result = await self.standard_provider.fetch(
                EvidenceRequest(capability=capability, query={"operation": tool_name, "arguments": arguments})
            )
        except (asyncio.TimeoutError, TimeoutError):
            result = ProviderResult(
                provider_id="standard_ha_mcp",
                capability=capability,
                completeness=ProviderCompleteness.FAILED,
                failure=_failure(ProviderFailureCategory.TIMEOUT, "The standard provider timed out.", True),
            )
        except Exception:
            result = ProviderResult(
                provider_id="standard_ha_mcp",
                capability=capability,
                completeness=ProviderCompleteness.FAILED,
                failure=_failure(ProviderFailureCategory.UPSTREAM_ERROR, "The standard provider failed.", True),
            )
        result.timing_ms = result.timing_ms or (time.perf_counter() - started) * 1000
        METRICS.record_provider_result(result.provider_id, result.completeness.value)
        return result

    async def _direct(self, capability, action):
        started = time.perf_counter()
        try:
            value = action()
            if inspect.isawaitable(value):
                value = await value
            data, warnings, metadata = _normalize_payload(value)
            result = ProviderResult(
                provider_id="direct_ha_api",
                capability=capability,
                completeness=ProviderCompleteness.COMPLETE,
                timing_ms=(time.perf_counter() - started) * 1000,
            )
            METRICS.record_provider_result(result.provider_id, "complete")
            return result, data, warnings, metadata
        except Exception:
            METRICS.record_provider_result("direct_ha_api", "failed")
            raise


def _normalize_payload(value):
    if not isinstance(value, str):
        return value, [], {}
    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value, [], {}
    if isinstance(payload, dict) and "success" in payload and "operation" in payload:
        if not payload.get("success"):
            try:
                code = ErrorCode(payload.get("error_code"))
            except (ValueError, TypeError):
                code = ErrorCode.PROVIDER_ERROR
            raise GovernanceError(code, details=payload.get("details") or {})
        return payload.get("data"), list(payload.get("warnings") or []), dict(payload.get("metadata") or {})
    return payload, [], {}


def _routing_metadata(route, result, provider_id=None):
    identity = result.provider_id if result else provider_id or "unknown"
    completeness = result.completeness.value if result else "failed"
    failure_category = result.failure.category.value if result and result.failure else None
    coverage = {
        "provider": identity,
        "provider_capability": result.capability.value if result else None,
        "completeness": completeness,
        "evidence_count": result.evidence_count if result else 0,
        "warnings": result.warnings[:10] if result else [],
        "duration_ms": round(result.timing_ms, 3) if result else 0.0,
        "failure_category": failure_category,
        "fallback_occurred": bool(result and result.fallback_occurred),
    }
    return {
        "routing": {
            "classification": route.value,
            "provider": identity,
            "fallback_occurred": coverage["fallback_occurred"],
        },
        "source_coverage": [coverage],
    }


def _provider_error(result: ProviderResult) -> GovernanceError:
    category = result.failure.category if result.failure else ProviderFailureCategory.UPSTREAM_ERROR
    code = {
        ProviderFailureCategory.UNAVAILABLE: ErrorCode.PROVIDER_UNAVAILABLE,
        ProviderFailureCategory.TIMEOUT: ErrorCode.PROVIDER_TIMEOUT,
        ProviderFailureCategory.PROHIBITED: ErrorCode.PROVIDER_PROHIBITED,
    }.get(category, ErrorCode.PROVIDER_ERROR)
    return GovernanceError(code)


def _failure(category, message, retryable=False):
    from .models import ProviderError

    return ProviderError(category, message, retryable)


CANONICAL_DISPATCHER = CanonicalProviderDispatcher()
