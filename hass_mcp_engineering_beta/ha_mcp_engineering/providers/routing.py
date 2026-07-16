"""Deterministic capability routing and explicit fallback enforcement."""

from __future__ import annotations

import asyncio
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
    UPSTREAM_DASHBOARD = "upstream_dashboard"
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
    ProviderCapability.CONFIGURATION_INTEGRITY_ANALYSIS,
    ProviderCapability.INCIDENT_CORRELATION,
    ProviderCapability.AUDIT,
    ProviderCapability.HANDOFF_GENERATION,
}
_STANDARD_PREFERRED = {
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
    ProviderCapability.BROAD_ENTITY_SEARCH,
    ProviderCapability.TEMPLATE_RENDER,
    ProviderCapability.HISTORY_READ,
    ProviderCapability.LOGBOOK_READ,
    ProviderCapability.ERROR_LOG_READ,
    ProviderCapability.AUTOMATION_LIST,
    ProviderCapability.DEVICE_REGISTRY_READ,
    ProviderCapability.ENTITY_REGISTRY_READ,
    ProviderCapability.BLUEPRINT_LIST,
    ProviderCapability.LEGACY_AUTOMATION_WRITE,
    ProviderCapability.CURRENT_ENTITY_STATE,
    ProviderCapability.AREA_LOOKUP,
    ProviderCapability.SERVICE_DISCOVERY,
}
_UPSTREAM_DASHBOARD = {
    ProviderCapability.DASHBOARD_INVENTORY,
    ProviderCapability.DASHBOARD_CONFIGURATION_EVIDENCE,
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
            return RoutingDecision(
                capability,
                CapabilityRoute.STANDARD_MCP_PREFERRED,
                "standard_ha_mcp",
            )
        if capability in _DIRECT_REQUIRED:
            return RoutingDecision(capability, CapabilityRoute.DIRECT_HA_REQUIRED, "direct_ha_api")
        if capability in _TRANSITIONAL_DIRECT:
            return RoutingDecision(capability, CapabilityRoute.TRANSITIONAL_DIRECT, "direct_ha_api")
        if capability in _UPSTREAM_DASHBOARD:
            return RoutingDecision(
                capability,
                CapabilityRoute.UPSTREAM_DASHBOARD,
                "upstream_dashboard",
            )
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
    "automation_reliability_analysis": ProviderCapability.RELIABILITY_ANALYSIS,
    "change_impact_analysis": ProviderCapability.IMPACT_ANALYSIS,
    "configuration_integrity_analysis": ProviderCapability.CONFIGURATION_INTEGRITY_ANALYSIS,
    "incident_correlation": ProviderCapability.INCIDENT_CORRELATION,
    "handoff_generation": ProviderCapability.HANDOFF_GENERATION,
    "list_dashboards": ProviderCapability.DASHBOARD_INVENTORY,
    "get_dashboard_config": ProviderCapability.DASHBOARD_CONFIGURATION_EVIDENCE,
}

ANALYTICAL_PROVIDER_POLICIES = {
    "automation_reliability_analysis": {
        "policy_id": "single_automation_reliability_read",
        "access": "read",
        "orchestrator": "engineering",
        "scope": "one automation plus bounded configuration, trace, entity, registry, blueprint, and sanitized System Log evidence",
        "writes_allowed": "none",
        "fallback_policy": "none",
    },
    "change_impact_analysis": {
        "policy_id": "single_entity_change_impact_read",
        "access": "read",
        "orchestrator": "engineering",
        "scope": "one entity, one proposed rename/remove/disable operation, the shared dependency index, and bounded supporting evidence",
        "writes_allowed": "none",
        "fallback_policy": "none",
    },
    "configuration_integrity_analysis": {
        "policy_id": "global_configuration_integrity_read",
        "access": "read",
        "orchestrator": "engineering",
        "scope": "bounded global state, entity-registry, dependency-index, source-coverage, and sanitized evidence inventories",
        "writes_allowed": "none",
        "fallback_policy": "none",
    },
    "incident_correlation": {
        "policy_id": "bounded_incident_correlation_read",
        "access": "read",
        "orchestrator": "engineering",
        "scope": "one focus entity, one automation, or both with bounded trace, state, history, logbook, System Log, dependency, integrity, and reliability evidence",
        "writes_allowed": "none",
        "fallback_policy": "none",
    },
    "handoff_generation": {
        "policy_id": "bounded_handoff_generation_read",
        "access": "read",
        "orchestrator": "engineering",
        "scope": "one bounded system-status, focused-review, incident, or change handoff from internal read-only evidence services",
        "writes_allowed": "none",
        "fallback_policy": "none",
    },
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
        "get_entity",
        "search_entities",
        "list_areas",
        "search_services",
        "list_services",
    }
)

DIRECT_HA_READ_POLICIES = {
    "render_template": {
        "policy_id": "bounded_template_render_read",
        "capability": ProviderCapability.TEMPLATE_RENDER.value,
        "access": "read",
        "justification": "Template validation requires bounded server-side rendering without persisting configuration.",
    },
    "list_automation_traces": {
        "policy_id": "bounded_automation_trace_list_read",
        "capability": ProviderCapability.AUTOMATION_TRACE.value,
        "access": "read",
        "justification": "Retained automation trace headers require the read-only trace WebSocket API.",
    },
    "get_automation_trace": {
        "policy_id": "bounded_automation_trace_read",
        "capability": ProviderCapability.AUTOMATION_TRACE.value,
        "access": "read",
        "justification": "One retained automation trace requires the read-only trace WebSocket API.",
    },
    "get_blueprint": {
        "policy_id": "bounded_blueprint_source_read",
        "capability": ProviderCapability.BLUEPRINT_SOURCE.value,
        "access": "read",
        "justification": "Exact blueprint evidence requires bounded read-only access to the mounted blueprint source.",
    },
    "check_config": {
        "policy_id": "configuration_validation_read",
        "capability": ProviderCapability.CONFIG_VALIDATION.value,
        "access": "read",
        "justification": "Home Assistant configuration validation performs no configuration mutation.",
    },
    "get_history": {
        "policy_id": "bounded_entity_history_read",
        "capability": ProviderCapability.HISTORY_READ.value,
        "access": "read",
        "justification": "Bounded exact entity history requires the Home Assistant history API.",
    },
    "get_logbook": {
        "policy_id": "bounded_logbook_read",
        "capability": ProviderCapability.LOGBOOK_READ.value,
        "access": "read",
        "justification": "Bounded logbook evidence requires the Home Assistant logbook API.",
    },
    "get_error_log": {
        "policy_id": "structured_system_log_read",
        "capability": ProviderCapability.ERROR_LOG_READ.value,
        "access": "read",
        "justification": "Recent Core warnings and errors require the admin-only System Log WebSocket API.",
    },
    "get_entity": {
        "policy_id": "exact_entity_state_read",
        "capability": ProviderCapability.CURRENT_ENTITY_STATE.value,
        "access": "read",
        "justification": "Exact state and attributes require entity-ID REST lookup.",
    },
    "search_entities": {
        "policy_id": "bounded_entity_state_search",
        "capability": ProviderCapability.BROAD_ENTITY_SEARCH.value,
        "access": "read",
        "justification": "Bounded entity discovery requires one read-only Home Assistant state inventory while Standard HA MCP delegation is unavailable.",
    },
    "list_automations": {
        "policy_id": "bounded_automation_inventory_read",
        "capability": ProviderCapability.AUTOMATION_LIST.value,
        "access": "read",
        "justification": "Automation discovery requires bounded read-only state and configuration inventory access.",
    },
    "get_automation_config": {
        "policy_id": "exact_automation_config_read",
        "capability": ProviderCapability.AUTOMATION_CONFIG.value,
        "access": "read",
        "justification": "Exact automation configuration requires the read-only automation config endpoint.",
    },
    "list_devices": {
        "policy_id": "bounded_device_registry_read",
        "capability": ProviderCapability.DEVICE_REGISTRY_READ.value,
        "access": "read",
        "justification": "Device relationships require bounded read-only device-registry enumeration.",
    },
    "list_entity_registry": {
        "policy_id": "bounded_entity_registry_read",
        "capability": ProviderCapability.ENTITY_REGISTRY_READ.value,
        "access": "read",
        "justification": "Exact registry metadata requires bounded read-only entity-registry enumeration.",
    },
    "list_blueprints": {
        "policy_id": "bounded_blueprint_inventory_read",
        "capability": ProviderCapability.BLUEPRINT_LIST.value,
        "access": "read",
        "justification": "Blueprint discovery requires bounded read-only blueprint inventory access.",
    },
    "list_areas": {
        "policy_id": "complete_area_registry_read",
        "capability": ProviderCapability.AREA_LOOKUP.value,
        "access": "read",
        "justification": "Complete area enumeration requires the area-registry WebSocket API.",
    },
    "search_services": {
        "policy_id": "bounded_service_catalog_search",
        "capability": ProviderCapability.SERVICE_DISCOVERY.value,
        "access": "read",
        "justification": "Service discovery requires a bounded read of the service catalog.",
    },
    "list_services": {
        "policy_id": "bounded_service_schema_read",
        "capability": ProviderCapability.SERVICE_DISCOVERY.value,
        "access": "read",
        "justification": "Full service schemas require a bounded read of the service catalog.",
    },
}


def direct_ha_exception_for_tool(tool_name: str, *, access: str = "read") -> bool:
    """Return whether a canonical tool has an explicit matching direct-HA policy."""
    if tool_name not in DIRECT_HA_TOOL_EXCEPTIONS:
        return False
    policy = DIRECT_HA_READ_POLICIES.get(tool_name)
    capability = TOOL_CAPABILITY_POLICY.get(tool_name)
    return bool(
        policy
        and capability
        and policy["access"] == access
        and policy["capability"] == capability.value
    )


def direct_ha_policy_for_tool(tool_name: str) -> dict[str, str] | None:
    """Return safe public metadata for an explicit direct-read policy."""

    policy = DIRECT_HA_READ_POLICIES.get(tool_name)
    return dict(policy) if policy else None


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
        provider = self.providers.get(provider_id) if provider_id else None
        dispatched = bool(provider and getattr(provider, "available", True))
        if not dispatched:
            result = ProviderResult(
                provider_id=provider_id or "none",
                capability=request.capability,
                completeness=ProviderCompleteness.UNAVAILABLE,
                failure=ProviderError(ProviderFailureCategory.UNAVAILABLE, "The selected provider is unavailable."),
                coverage=ProviderCoverage(1, 0, (provider_id or "none",)),
            )
        else:
            try:
                result = await provider.fetch(request)
            except (asyncio.TimeoutError, TimeoutError):
                result = ProviderResult(
                    provider_id=provider_id,
                    capability=request.capability,
                    completeness=ProviderCompleteness.FAILED,
                    failure=ProviderError(
                        ProviderFailureCategory.TIMEOUT,
                        "The selected provider timed out.",
                        True,
                    ),
                    coverage=ProviderCoverage(1, 0, (provider_id,)),
                )
            except Exception:
                result = ProviderResult(
                    provider_id=provider_id,
                    capability=request.capability,
                    completeness=ProviderCompleteness.FAILED,
                    failure=ProviderError(
                        ProviderFailureCategory.UPSTREAM_ERROR,
                        "The selected provider failed.",
                        True,
                    ),
                    coverage=ProviderCoverage(1, 0, (provider_id,)),
                )
        evidence_limit = max(1, min(request.max_evidence, 100))
        if len(result.evidence) > evidence_limit:
            result.evidence = result.evidence[:evidence_limit]
            result.completeness = ProviderCompleteness.PARTIAL
            result.warnings = [
                *result.warnings[:19],
                "Evidence was truncated; request a bounded drill-down.",
            ]
            METRICS.record_evidence_truncation()
        METRICS.record_provider_result(
            result.provider_id,
            result.completeness.value,
            dispatched=dispatched,
        )
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
