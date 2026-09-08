"""Dependency-analysis composition registry and invalidation hook."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
import time
from typing import Any

from .authority import BUILD_TIMEOUT_SECONDS, dependency_build_authority
from .index import DependencyIndex
from .provider import DirectHaDependencyProvider
from .service import EntityDependencyAnalysisService


@dataclass
class DependencyAnalysisRuntime:
    service: EntityDependencyAnalysisService | None = None
    prewarm_task: asyncio.Task[bool] | None = None
    core_runtime: Any | None = None

    def configure(
        self,
        rest_client,
        websocket_client,
        *,
        secret: str = "",
        timeout: float = 60.0,
        soft_ttl_seconds: float = 600.0,
        hard_ttl_seconds: float = 3600.0,
        core_runtime: Any | None = None,
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
        self.bind_core_runtime(core_runtime)

    def bind_core_runtime(self, core_runtime: Any | None) -> None:
        """Bind background reads to the same Core generation as public routes."""

        self.core_runtime = core_runtime
        if self.service is not None:
            self.service.index.build_scope = self._build_scope
        if core_runtime is not None:
            core_runtime.register_reconciliation_listener(
                self.invalidate_core_authority
            )

    def invalidate_core_authority(self) -> None:
        """Make dependency evidence unusable when Core authority changes."""

        if self.service:
            self.service.index.invalidate("core_authority_changed")

    def _build_scope(
        self, deadline: float
    ) -> AbstractContextManager[Callable[[], None]]:
        return dependency_build_authority(self.core_runtime, deadline)

    async def _authorized_prewarm(self, provider) -> bool:
        """Check connectivity separately; the shared build owns its own scope."""

        async def connectivity_check() -> Any:
            deadline = time.monotonic() + BUILD_TIMEOUT_SECONDS
            async with asyncio.timeout(BUILD_TIMEOUT_SECONDS):
                with self._build_scope(deadline):
                    return await provider.rest_client.request("GET", "/config")

        return await self.require().index.prewarm(connectivity_check)

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
                    complete = await self._authorized_prewarm(provider)
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

    def invalidate(self, reason: str = "configuration_changed") -> None:
        if self.service:
            self.service.index.invalidate(reason)

    def health(self) -> dict:
        return self.service.index.health() if self.service else {"configured": False}


DEPENDENCY_ANALYSIS = DependencyAnalysisRuntime()
