"""Configuration-integrity analysis composition registry."""

from __future__ import annotations

from dataclasses import dataclass

from .provider import DirectHaIntegrityProvider
from .service import ConfigurationIntegrityAnalysisService


@dataclass
class ConfigurationIntegrityAnalysisRuntime:
    service: ConfigurationIntegrityAnalysisService | None = None

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
        provider = DirectHaIntegrityProvider(
            dependency_index,
            rest_client,
            websocket_client,
            secret=secret,
            ha_token=ha_token,
            timeout=timeout,
        )
        self.service = ConfigurationIntegrityAnalysisService(
            provider, timeout_seconds=timeout
        )

    def require(self) -> ConfigurationIntegrityAnalysisService:
        if not self.service:
            raise RuntimeError(
                "Configuration-integrity analysis is not configured"
            )
        return self.service

    def health(self) -> dict:
        return {
            "configured": self.service is not None,
            "scope": "global_configuration_integrity",
            "read_only": True,
            "shared_dependency_index": True,
            "bounded_pagination_snapshots": True,
            "general_result_cache_supported": False,
        }


CONFIGURATION_INTEGRITY_ANALYSIS = ConfigurationIntegrityAnalysisRuntime()
