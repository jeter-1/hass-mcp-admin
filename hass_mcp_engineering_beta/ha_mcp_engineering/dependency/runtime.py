"""Dependency-analysis composition registry and invalidation hook."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .index import DependencyIndex
from .provider import DirectHaDependencyProvider
from .service import EntityDependencyAnalysisService


@dataclass
class DependencyAnalysisRuntime:
    service: EntityDependencyAnalysisService | None = None
    prewarm_task: asyncio.Task[bool] | None = None

    def configure(self, rest_client, websocket_client, *, secret: str = "", timeout: float = 60.0) -> None:
        provider = DirectHaDependencyProvider(rest_client, websocket_client, secret=secret, timeout=timeout)
        self.service = EntityDependencyAnalysisService(DependencyIndex(provider))
        self.prewarm_task = None

    def start_prewarm(self) -> asyncio.Task[bool]:
        """Start one optional prewarm after Home Assistant answers a safe probe."""

        service = self.require()
        if self.prewarm_task is None or self.prewarm_task.done():
            provider = service.index.provider

            async def run() -> bool:
                return await service.index.prewarm(
                    lambda: provider.rest_client.request("GET", "/config")
                )

            self.prewarm_task = asyncio.create_task(run())
        return self.prewarm_task

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
