"""Incident-correlation composition registry."""

from __future__ import annotations

from dataclasses import dataclass

from .provider import DirectHaIncidentProvider
from .service import IncidentCorrelationService


@dataclass
class IncidentCorrelationRuntime:
    service: IncidentCorrelationService | None = None

    def configure(self, dependency_index, rest_client, websocket_client, reliability_provider, *, secret="", ha_token="", timeout=60.0):
        provider = DirectHaIncidentProvider(
            dependency_index, rest_client, websocket_client, reliability_provider,
            secret=secret, ha_token=ha_token, timeout=timeout,
        )
        self.service = IncidentCorrelationService(provider, timeout_seconds=timeout)

    def require(self) -> IncidentCorrelationService:
        if not self.service:
            raise RuntimeError("Incident correlation is not configured")
        return self.service

    def health(self) -> dict:
        return {
            "configured": self.service is not None,
            "scope": "single_bounded_incident_request",
            "read_only": True,
            "shared_dependency_index": True,
            "bounded_pagination_snapshots": True,
            "result_cache_supported": False,
        }


INCIDENT_CORRELATION = IncidentCorrelationRuntime()
