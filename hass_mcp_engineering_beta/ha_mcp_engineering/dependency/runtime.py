"""Dependency-analysis composition registry and invalidation hook."""

from __future__ import annotations

from dataclasses import dataclass

from .index import DependencyIndex
from .provider import DirectHaDependencyProvider
from .service import EntityDependencyAnalysisService


@dataclass
class DependencyAnalysisRuntime:
    service: EntityDependencyAnalysisService | None = None

    def configure(self, rest_client, websocket_client, *, secret: str = "", timeout: float = 60.0) -> None:
        provider = DirectHaDependencyProvider(rest_client, websocket_client, secret=secret, timeout=timeout)
        self.service = EntityDependencyAnalysisService(DependencyIndex(provider))

    def require(self) -> EntityDependencyAnalysisService:
        if not self.service:
            raise RuntimeError("Dependency analysis is not configured")
        return self.service

    def invalidate(self) -> None:
        if self.service:
            self.service.index.invalidate()

    def health(self) -> dict:
        return self.service.index.health() if self.service else {"configured": False}


DEPENDENCY_ANALYSIS = DependencyAnalysisRuntime()
