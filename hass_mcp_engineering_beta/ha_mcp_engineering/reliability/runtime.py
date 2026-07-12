"""Reliability-analysis composition registry."""

from __future__ import annotations

from dataclasses import dataclass

from .provider import DirectHaReliabilityProvider
from .service import AutomationReliabilityAnalysisService


@dataclass
class ReliabilityAnalysisRuntime:
    service: AutomationReliabilityAnalysisService | None = None

    def configure(self, rest_client, websocket_client, *, secret: str = "", ha_token: str = "", timeout: float = 60.0) -> None:
        provider = DirectHaReliabilityProvider(
            rest_client, websocket_client, secret=secret, ha_token=ha_token
        )
        self.service = AutomationReliabilityAnalysisService(provider, timeout_seconds=timeout)

    def require(self) -> AutomationReliabilityAnalysisService:
        if not self.service:
            raise RuntimeError("Reliability analysis is not configured")
        return self.service

    def health(self) -> dict:
        return {"configured": self.service is not None, "scope": "single_automation", "read_only": True}


RELIABILITY_ANALYSIS = ReliabilityAnalysisRuntime()
