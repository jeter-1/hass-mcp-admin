"""Standard Home Assistant MCP delegation boundary.

No nested standard-MCP transport is configured in Beta 6. Returning explicit
unavailability prevents capability metadata from being mistaken for delegation.
"""

from __future__ import annotations

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


class StandardHaMcpGateway(EngineeringEvidenceProvider):
    provider_id = "standard_ha_mcp"
    capabilities = frozenset(
        {
            ProviderCapability.CURRENT_ENTITY_STATE,
            ProviderCapability.BROAD_ENTITY_SEARCH,
            ProviderCapability.AREA_LOOKUP,
            ProviderCapability.SERVICE_DISCOVERY,
            ProviderCapability.ORDINARY_SERVICE_EXECUTION,
        }
    )

    @property
    def available(self) -> bool:
        return False

    async def fetch(self, request: EvidenceRequest) -> ProviderResult:
        return ProviderResult(
            provider_id=self.provider_id,
            capability=request.capability,
            completeness=ProviderCompleteness.UNAVAILABLE,
            warnings=["Standard Home Assistant MCP delegation transport is not configured."],
            failure=ProviderError(
                ProviderFailureCategory.UNAVAILABLE,
                "The standard Home Assistant MCP provider is unavailable.",
                retryable=False,
            ),
            coverage=ProviderCoverage(1, 0, (self.provider_id,)),
            metadata={"implementation": "transitional_unavailable"},
        )
