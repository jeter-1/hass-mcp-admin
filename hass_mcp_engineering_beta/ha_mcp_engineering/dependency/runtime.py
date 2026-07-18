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

    def configure(
        self,
        rest_client,
        websocket_client,
        *,
        secret: str = "",
        timeout: float = 60.0,
        soft_ttl_seconds: float = 600.0,
        hard_ttl_seconds: float = 3600.0,
    ) -> None:
        provider = DirectHaDependencyProvider(
            rest_client, websocket_client, secret=secret, timeout=timeout
        )
        self.service = EntityDependencyAnalysisService(
            DependencyIndex(
                provider,
                soft_ttl_seconds=soft_ttl_seconds,
                hard_ttl_seconds=hard_ttl_seconds,
            )
        )
        self.prewarm_task = None

    def start_prewarm(
        self,
        *,
        startup_delay_seconds: float = 45.0,
        retry_delay_seconds: float = 300.0,
    ) -> asyncio.Task[bool]:
        """Schedule nonblocking prewarm attempts with a bounded retry interval."""

        service = self.require()
        if self.prewarm_task is None or self.prewarm_task.done():
            provider = service.index.provider
            service.index.configure_prewarm(enabled=True)

            async def run() -> bool:
                await asyncio.sleep(max(0.0, startup_delay_seconds))
                while True:
                    complete = await service.index.prewarm(
                        lambda: provider.rest_client.request("GET", "/config")
                    )
                    if complete:
                        return True
                    service.index.note_prewarm_retry(retry_delay_seconds)
                    await asyncio.sleep(max(300.0, retry_delay_seconds))

            self.prewarm_task = asyncio.create_task(run(), name="dependency-index-prewarm")
        return self.prewarm_task

    async def shutdown(self) -> None:
        if self.prewarm_task is not None and not self.prewarm_task.done():
            self.prewarm_task.cancel()
        if self.prewarm_task is not None:
            await asyncio.gather(self.prewarm_task, return_exceptions=True)
        if self.service:
            await self.service.index.shutdown()

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
