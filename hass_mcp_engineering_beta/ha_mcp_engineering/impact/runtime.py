"""Change-impact analysis composition registry."""

from __future__ import annotations

from dataclasses import dataclass

from .provider import DirectHaImpactProvider
from .service import ChangeImpactAnalysisService


@dataclass
class ChangeImpactAnalysisRuntime:
    service: ChangeImpactAnalysisService | None = None

    def configure(
        self,
        dependency_index,
        rest_client,
        websocket_client,
        *,
        secret: str = "",
        ha_token: str = "",
        timeout: float = 60.0,
    ) -> None:
        provider = DirectHaImpactProvider(
            dependency_index,
            rest_client,
            websocket_client,
            secret=secret,
            ha_token=ha_token,
            timeout=timeout,
        )
        self.service = ChangeImpactAnalysisService(
            provider, timeout_seconds=timeout
        )

    def require(self) -> ChangeImpactAnalysisService:
        if not self.service:
            raise RuntimeError("Change-impact analysis is not configured")
        return self.service

    def health(self) -> dict:
        return {
            "configured": self.service is not None,
            "scope": "single_entity_change_impact",
            "read_only": True,
            "shared_dependency_index": True,
            "general_result_cache_supported": False,
        }


CHANGE_IMPACT_ANALYSIS = ChangeImpactAnalysisRuntime()
