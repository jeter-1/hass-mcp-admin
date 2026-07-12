"""Deterministic capability routing and explicit fallback enforcement."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from ..observability import METRICS
from .base import EngineeringEvidenceProvider
from .models import (
    EvidenceRequest,
    ProviderCapability,
    ProviderCompleteness,
    ProviderCoverage,
    ProviderError,
    ProviderFailureCategory,
    ProviderResult,
)


class CapabilityRoute(str, Enum):
    ENGINEERING_NATIVE = "engineering_native"
    STANDARD_MCP_PREFERRED = "standard_mcp_preferred"
    DIRECT_HA_REQUIRED = "direct_ha_required"
    TRANSITIONAL_DIRECT = "transitional_direct"
    UNSUPPORTED = "unsupported"
    PROHIBITED = "prohibited"


@dataclass(frozen=True)
class RoutingDecision:
    capability: ProviderCapability
    route: CapabilityRoute
    preferred_provider: str | None
    fallback_providers: tuple[str, ...] = ()
    explicit_direct_fallback_allowed: bool = False


_ENGINEERING_NATIVE = {
    ProviderCapability.GOVERNANCE_PERSISTENCE,
    ProviderCapability.RISK_ASSESSMENT,
    ProviderCapability.DEPENDENCY_ANALYSIS,
    ProviderCapability.RELIABILITY_ANALYSIS,
    ProviderCapability.IMPACT_ANALYSIS,
    ProviderCapability.AUDIT,
    ProviderCapability.HANDOFF_GENERATION,
}
_STANDARD_PREFERRED = {
    ProviderCapability.CURRENT_ENTITY_STATE,
    ProviderCapability.BROAD_ENTITY_SEARCH,
    ProviderCapability.AREA_LOOKUP,
    ProviderCapability.SERVICE_DISCOVERY,
    ProviderCapability.ORDINARY_SERVICE_EXECUTION,
}
_DIRECT_REQUIRED = {
    ProviderCapability.AUTOMATION_CONFIG,
    ProviderCapability.AUTOMATION_TRACE,
    ProviderCapability.BLUEPRINT_SOURCE,
    ProviderCapability.CONFIG_VALIDATION,
    ProviderCapability.GOVERNED_APPLY,
    ProviderCapability.EXACT_VERIFICATION,
    ProviderCapability.GOVERNED_ROLLBACK,
}
_TRANSITIONAL_DIRECT = {
    ProviderCapability.TEMPLATE_RENDER,
    ProviderCapability.HISTORY_READ,
    ProviderCapability.LOGBOOK_READ,
    ProviderCapability.ERROR_LOG_READ,
    ProviderCapability.AUTOMATION_LIST,
    ProviderCapability.DEVICE_REGISTRY_READ,
    ProviderCapability.ENTITY_REGISTRY_READ,
    ProviderCapability.BLUEPRINT_LIST,
    ProviderCapability.LEGACY_AUTOMATION_WRITE,
}
_PROHIBITED = {
    ProviderCapability.UNGOVERNED_PHYSICAL_ACTION,
    ProviderCapability.SECRET_BEARING_DIAGNOSTICS,
}


class RoutingPolicy:
    def resolve(self, capability: ProviderCapability) -> RoutingDecision:
        if capability in _ENGINEERING_NATIVE:
            return RoutingDecision(capability, CapabilityRoute.ENGINEERING_NATIVE, "engineering")
        if capability in _STANDARD_PREFERRED:
            fallback = ("direct_ha_api",) if capability == ProviderCapability.CURRENT_ENTITY_STATE else ()
            return RoutingDecision(
                capability,
                CapabilityRoute.STANDARD_MCP_PREFERRED,
                "standard_ha_mcp",
                fallback,
                explicit_direct_fallback_allowed=bool(fallback),
            )
        if capability in _DIRECT_REQUIRED:
            return RoutingDecision(capability, CapabilityRoute.DIRECT_HA_REQUIRED, "direct_ha_api")
        if capability in _TRANSITIONAL_DIRECT:
            return RoutingDecision(capability, CapabilityRoute.TRANSITIONAL_DIRECT, "direct_ha_api")
        if capability in _PROHIBITED:
            return RoutingDecision(capability, CapabilityRoute.PROHIBITED, None)
        return RoutingDecision(capability, CapabilityRoute.UNSUPPORTED, None)


TOOL_CAPABILITY_POLICY: dict[str, ProviderCapability] = {
    "server_info": ProviderCapability.HANDOFF_GENERATION,
    "list_capabilities": ProviderCapability.HANDOFF_GENERATION,
    "render_template": ProviderCapability.TEMPLATE_RENDER,
    "list_automation_traces": ProviderCapability.AUTOMATION_TRACE,
    "get_automation_trace": ProviderCapability.AUTOMATION_TRACE,
    "get_blueprint": ProviderCapability.BLUEPRINT_SOURCE,
    "check_config": ProviderCapability.CONFIG_VALIDATION,
    "get_audit_log": ProviderCapability.AUDIT,
    "get_history": ProviderCapability.HISTORY_READ,
    "get_logbook": ProviderCapability.LOGBOOK_READ,
    "get_error_log": ProviderCapability.ERROR_LOG_READ,
    "list_automations": ProviderCapability.AUTOMATION_LIST,
    "get_automation_config": ProviderCapability.AUTOMATION_CONFIG,
    "list_devices": ProviderCapability.DEVICE_REGISTRY_READ,
    "list_entity_registry": ProviderCapability.ENTITY_REGISTRY_READ,
    "search_entities": ProviderCapability.BROAD_ENTITY_SEARCH,
    "upsert_automation": ProviderCapability.LEGACY_AUTOMATION_WRITE,
    "get_entity": ProviderCapability.CURRENT_ENTITY_STATE,
    "search_services": ProviderCapability.SERVICE_DISCOVERY,
    "list_services": ProviderCapability.SERVICE_DISCOVERY,
    "list_areas": ProviderCapability.AREA_LOOKUP,
    "list_blueprints": ProviderCapability.BLUEPRINT_LIST,
    "call_service": ProviderCapability.ORDINARY_SERVICE_EXECUTION,
    "delete_automation": ProviderCapability.UNGOVERNED_PHYSICAL_ACTION,
    "reload_domain": ProviderCapability.ORDINARY_SERVICE_EXECUTION,
    "get_server_health": ProviderCapability.AUDIT,
    "create_change_plan": ProviderCapability.RISK_ASSESSMENT,
    "get_change_plan": ProviderCapability.GOVERNANCE_PERSISTENCE,
    "list_change_plans": ProviderCapability.GOVERNANCE_PERSISTENCE,
    "approve_change_plan": ProviderCapability.GOVERNANCE_PERSISTENCE,
    "apply_change_plan": ProviderCapability.GOVERNED_APPLY,
    "rollback_change": ProviderCapability.GOVERNED_ROLLBACK,
    "entity_dependency_analysis": ProviderCapability.DEPENDENCY_ANALYSIS,
}

# Canonical tools in this allowlist may execute their existing direct-HA
# implementation. Capability classification alone is intentionally insufficient:
# direct access is a tool-specific exception that must be reviewed explicitly.
DIRECT_HA_TOOL_EXCEPTIONS = frozenset(
    {
        "render_template",
        "list_automation_traces",
        "get_automation_trace",
        "get_blueprint",
        "check_config",
        "get_history",
        "get_logbook",
        "get_error_log",
        "list_automations",
        "get_automation_config",
        "list_devices",
        "list_entity_registry",
        "upsert_automation",
        "list_blueprints",
    }
)


def direct_ha_exception_for_tool(tool_name: str) -> bool:
    """Return whether a canonical tool has an explicit direct-HA exception."""

    return tool_name in DIRECT_HA_TOOL_EXCEPTIONS


def routing_for_tool(tool_name: str, policy: RoutingPolicy | None = None) -> RoutingDecision:
    capability = TOOL_CAPABILITY_POLICY.get(tool_name, ProviderCapability.UNSUPPORTED_EXPERIMENTAL)
    return (policy or RoutingPolicy()).resolve(capability)


class EvidenceRouter:
    def __init__(self, providers: list[EngineeringEvidenceProvider], policy: RoutingPolicy | None = None):
        self.providers = {provider.provider_id: provider for provider in providers}
        self.policy = policy or RoutingPolicy()

    async def fetch(self, request: EvidenceRequest) -> ProviderResult:
        decision = self.policy.resolve(request.capability)
        if decision.route in {CapabilityRoute.PROHIBITED, CapabilityRoute.UNSUPPORTED}:
            if decision.route == CapabilityRoute.PROHIBITED and request.allow_direct_fallback:
                METRICS.record_prohibited_fallback()
            return _policy_failure(request, decision.route)
        primary = await self._attempt(decision.preferred_provider, request)
        if primary.succeeded:
            return primary
        if not decision.fallback_providers:
            return primary
        if not decision.explicit_direct_fallback_allowed or not request.allow_direct_fallback:
            METRICS.record_prohibited_fallback()
            return primary
        for provider_id in decision.fallback_providers:
            METRICS.record_fallback_attempt()
            result = await self._attempt(provider_id, request)
            result = replace(result, fallback_occurred=True)
            if result.succeeded:
                METRICS.record_fallback_success()
                return result
            primary.warnings.append(f"Explicit fallback provider {provider_id} failed.")
        return primary

    async def _attempt(self, provider_id: str | None, request: EvidenceRequest) -> ProviderResult:
        if not provider_id or provider_id not in self.providers:
            result = ProviderResult(
                provider_id=provider_id or "none",
                capability=request.capability,
                completeness=ProviderCompleteness.UNAVAILABLE,
                failure=ProviderError(ProviderFailureCategory.UNAVAILABLE, "The selected provider is unavailable."),
                coverage=ProviderCoverage(1, 0, (provider_id or "none",)),
            )
        else:
            result = await self.providers[provider_id].fetch(request)
        evidence_limit = max(1, min(request.max_evidence, 100))
        if len(result.evidence) > evidence_limit:
            result.evidence = result.evidence[:evidence_limit]
            result.completeness = ProviderCompleteness.PARTIAL
            result.warnings = [
                *result.warnings[:19],
                "Evidence was truncated; request a bounded drill-down.",
            ]
            METRICS.record_evidence_truncation()
        METRICS.record_provider_result(result.provider_id, result.completeness.value)
        return result


def _policy_failure(request: EvidenceRequest, route: CapabilityRoute) -> ProviderResult:
    prohibited = route == CapabilityRoute.PROHIBITED
    return ProviderResult(
        provider_id="policy",
        capability=request.capability,
        completeness=ProviderCompleteness.FAILED,
        warnings=["Capability routing was denied by policy."],
        failure=ProviderError(
            ProviderFailureCategory.PROHIBITED if prohibited else ProviderFailureCategory.UNSUPPORTED,
            "The requested capability is prohibited." if prohibited else "The requested capability is unsupported.",
        ),
        coverage=ProviderCoverage(1, 0, ("policy",)),
    )
