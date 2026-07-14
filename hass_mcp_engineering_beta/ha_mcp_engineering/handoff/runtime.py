"""Application composition registry for handoff generation."""

from __future__ import annotations

from dataclasses import dataclass

from .provider import EngineeringHandoffProvider
from .service import HandoffGenerationService


@dataclass
class HandoffGenerationRuntime:
    service: HandoffGenerationService | None = None

    def configure(self, *, governance, incident, dependency_index, rest_client, health, secret="", ha_token="", timeout=60.0):
        provider = EngineeringHandoffProvider(
            governance=governance,
            incident=incident,
            dependency_index=dependency_index,
            rest_client=rest_client,
            health=health,
            secret=secret,
            ha_token=ha_token,
        )
        self.service = HandoffGenerationService(provider, timeout_seconds=timeout)

    def require(self) -> HandoffGenerationService:
        if self.service is None:
            raise RuntimeError("Handoff generation is not configured")
        return self.service

    def health(self) -> dict:
        return {
            "configured": self.service is not None,
            "scope": "one_bounded_snapshot_handoff",
            "read_only": True,
            "structured_and_markdown": True,
            "bounded_pagination_snapshots": True,
            "result_cache_supported": False,
        }


HANDOFF_GENERATION = HandoffGenerationRuntime()
