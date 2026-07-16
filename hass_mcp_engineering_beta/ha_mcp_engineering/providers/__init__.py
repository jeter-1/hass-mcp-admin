"""Evidence-provider and delegation boundaries for facilitator orchestration."""

from .base import EngineeringEvidenceProvider
from .direct_ha import DirectHaApiProvider
from .models import (
    EvidenceRequest,
    ProviderCapability,
    ProviderCompleteness,
    ProviderCoverage,
    ProviderError,
    ProviderFailureCategory,
    ProviderResult,
)
from .routing import (
    CapabilityRoute,
    DIRECT_HA_TOOL_EXCEPTIONS,
    DIRECT_HA_READ_POLICIES,
    EvidenceRouter,
    RoutingDecision,
    RoutingPolicy,
    TOOL_CAPABILITY_POLICY,
    direct_ha_exception_for_tool,
    direct_ha_policy_for_tool,
    routing_for_tool,
)
from .standard_mcp import StandardHaMcpGateway
from .dispatch import CANONICAL_DISPATCHER, CanonicalProviderDispatcher
from .upstream_dashboard import (
    ALLOWED_UPSTREAM_TOOLS,
    FAILURE_CATEGORIES,
    PROVIDER_ID as UPSTREAM_DASHBOARD_PROVIDER_ID,
    UPSTREAM_DASHBOARD,
    DashboardProviderResult,
    UpstreamDashboardProvider,
    ensure_dashboard_tool_allowed,
)

__all__ = [
    "CapabilityRoute",
    "DIRECT_HA_TOOL_EXCEPTIONS",
    "DIRECT_HA_READ_POLICIES",
    "DirectHaApiProvider",
    "EngineeringEvidenceProvider",
    "EvidenceRequest",
    "EvidenceRouter",
    "ProviderCapability",
    "ProviderCompleteness",
    "ProviderCoverage",
    "ProviderError",
    "ProviderFailureCategory",
    "ProviderResult",
    "RoutingDecision",
    "RoutingPolicy",
    "StandardHaMcpGateway",
    "TOOL_CAPABILITY_POLICY",
    "direct_ha_exception_for_tool",
    "direct_ha_policy_for_tool",
    "routing_for_tool",
    "CANONICAL_DISPATCHER",
    "CanonicalProviderDispatcher",
    "ALLOWED_UPSTREAM_TOOLS",
    "DashboardProviderResult",
    "FAILURE_CATEGORIES",
    "UPSTREAM_DASHBOARD",
    "UPSTREAM_DASHBOARD_PROVIDER_ID",
    "UpstreamDashboardProvider",
    "ensure_dashboard_tool_allowed",
]
