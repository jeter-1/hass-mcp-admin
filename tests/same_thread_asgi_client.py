"""Synchronous test facade over one same-thread ASGI event loop."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


class SameThreadAsgiTestClient:
    """Drive ASGI lifespan and requests without a blocking-portal thread."""

    def __init__(
        self,
        app,
        *,
        lifespan_app=None,
        base_url: str = "http://testserver",
        follow_redirects: bool = False,
    ) -> None:
        self.app = app
        self.lifespan_app = lifespan_app or app
        self.base_url = base_url
        self.follow_redirects = follow_redirects
        self.loop: asyncio.AbstractEventLoop | None = None
        self.loop_closed = False
        self.pending_task_count = 0
        self._commands: asyncio.Queue | None = None
        self._started: asyncio.Future | None = None
        self._driver: asyncio.Task | None = None

    async def _drive(self) -> None:
        assert self._commands is not None
        assert self._started is not None
        lifespan = self.lifespan_app.router.lifespan_context(
            self.lifespan_app
        )
        try:
            async with lifespan:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.app),
                    base_url=self.base_url,
                    follow_redirects=self.follow_redirects,
                ) as client:
                    self._started.set_result(True)
                    while True:
                        command = await self._commands.get()
                        if command is None:
                            break
                        method, url, kwargs, result = command
                        try:
                            response = await client.request(
                                method, url, **kwargs
                            )
                        except BaseException as exc:
                            result.set_exception(exc)
                        else:
                            result.set_result(response)
        except BaseException as exc:
            if not self._started.done():
                self._started.set_exception(exc)
            raise

    def __enter__(self) -> "SameThreadAsgiTestClient":
        if self.loop is not None:
            raise RuntimeError("ASGI test client is already entered")
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._commands = asyncio.Queue()
        self._started = self.loop.create_future()
        self._driver = self.loop.create_task(self._drive())
        self.loop.run_until_complete(self._started)
        return self

    def request(self, method: str, url: str, **kwargs: Any):
        if self.loop is None or self._commands is None:
            raise RuntimeError("ASGI test client is not entered")
        result = self.loop.create_future()
        self._commands.put_nowait((method, url, kwargs, result))
        return self.loop.run_until_complete(result)

    def get(self, url: str, **kwargs: Any):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any):
        return self.request("POST", url, **kwargs)

    def __exit__(self, exc_type, exc, traceback) -> None:
        if (
            self.loop is None
            or self._commands is None
            or self._driver is None
        ):
            return
        loop = self.loop
        pending: list[asyncio.Task] = []
        try:
            self._commands.put_nowait(None)
            loop.run_until_complete(self._driver)
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            pending = [
                task
                for task in asyncio.all_tasks(loop)
                if not task.done()
            ]
            if pending:
                for task in pending:
                    task.cancel()
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            remaining = [
                task
                for task in asyncio.all_tasks(loop)
                if not task.done()
            ]
            self.pending_task_count = len(remaining)
            if remaining:
                raise AssertionError(
                    "ASGI test client left pending asyncio tasks: "
                    + ", ".join(repr(task) for task in remaining)
                )
        finally:
            asyncio.set_event_loop(None)
            loop.close()
            self.loop_closed = loop.is_closed()
            self.loop = None
