"""Dependency-analysis composition registry and invalidation hook."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from ..request_context import begin_request, end_request
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
        if core_runtime is not None:
            core_runtime.register_reconciliation_listener(
                self.invalidate_core_authority
            )

    def invalidate_core_authority(self) -> None:
        """Make dependency evidence unusable when Core authority changes."""

        if self.service:
            self.service.index.invalidate("core_authority_changed")

    async def _authorized_prewarm(self, provider) -> bool:
        """Run one prewarm attempt under a bounded Core route lease."""

        service = self.require()
        core_runtime = self.core_runtime
        authority = (
            core_runtime.acquire(("core.dependency_helper_planning",))
            if core_runtime is not None
            else None
        )
        if authority is None:
            async def unavailable() -> None:
                raise RuntimeError("core_authority_unavailable")

            return await service.index.prewarm(unavailable)

        commits = None
        telemetry, token = begin_request()

        def authorize_core_dispatch() -> bool:
            nonlocal commits
            if commits is not None:
                return core_runtime.revalidate(authority, commits)
            commits = core_runtime.consume(authority)
            return commits is not None

        telemetry.core_dispatch_authorizer = authorize_core_dispatch

        async def connectivity_check() -> Any:
            # Enforce the lease even for an injected test client; the
            # production REST client performs the same check again directly
            # before opening its transport.
            if not telemetry.authorize_core_dispatch():
                raise RuntimeError("core_authority_unavailable")
            return await provider.rest_client.request("GET", "/config")

        try:
            return await service.index.prewarm(connectivity_check)
        finally:
            telemetry.core_dispatch_authorizer = lambda: False
            if commits is not None:
                core_runtime.finish(commits)
            else:
                core_runtime.release(authority)
            end_request(token)

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
