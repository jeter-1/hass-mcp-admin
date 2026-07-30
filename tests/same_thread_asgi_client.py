"""Synchronous test facade over one same-thread ASGI event loop.

The pinned Starlette ``TestClient`` path uses AnyIO's blocking portal and emits
a deprecation warning recommending httpx2. The implementation host reproduced
a cross-thread portal stall that the independent review host did not reproduce,
so the affected fixtures intentionally avoid another synchronous portal-backed
client. Callers pass the inner application as the lifespan owner; the gateway
wrapper's trivial lifespan-delegation branch is therefore outside this helper's
coverage.
"""

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
        self.asyncgens_shutdown_attempted = False
        self.default_executor_shutdown_attempted = False
        self._commands: asyncio.Queue | None = None
        self._started: asyncio.Future | None = None
        self._driver: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None
        self._entered = False
        self._lifespan_started = False

    async def _drive(self) -> None:
        assert self._commands is not None
        assert self._started is not None
        lifespan = self.lifespan_app.router.lifespan_context(
            self.lifespan_app
        )
        try:
            async with lifespan:
                self._lifespan_started = True
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.app),
                    base_url=self.base_url,
                    follow_redirects=self.follow_redirects,
                ) as client:
                    self._client = client
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
                        except Exception as exc:
                            result.set_exception(exc.with_traceback(None))
                        else:
                            result.set_result(response)
        except BaseException as exc:
            if not self._started.done():
                self._started.set_exception(exc)
            raise
        finally:
            self._client = None
            self._lifespan_started = False

    def _reset_owned_state(self) -> None:
        self.loop = None
        self._commands = None
        self._started = None
        self._driver = None
        self._client = None
        self._entered = False
        self._lifespan_started = False

    def _cleanup_owned_loop(
        self,
        *,
        cancel_driver: bool,
        preserve_exception: BaseException | None = None,
    ) -> None:
        """Close this client's loop and retain the initiating exception."""

        loop = self.loop
        driver = self._driver
        cleanup_errors: list[BaseException] = []
        if loop is None:
            self._reset_owned_state()
            return

        try:
            if not loop.is_closed():
                if driver is not None and not driver.done():
                    if cancel_driver:
                        driver.cancel()
                    elif self._commands is not None:
                        self._commands.put_nowait(None)

                if driver is not None:
                    try:
                        results = loop.run_until_complete(
                            asyncio.gather(
                                driver,
                                return_exceptions=True,
                            )
                        )
                    except BaseException as exc:
                        cleanup_errors.append(exc)
                    else:
                        driver_result = results[0]
                        if (
                            isinstance(driver_result, BaseException)
                            and not isinstance(
                                driver_result,
                                asyncio.CancelledError,
                            )
                            and driver_result is not preserve_exception
                        ):
                            cleanup_errors.append(driver_result)

                pending = [
                    task
                    for task in asyncio.all_tasks(loop)
                    if not task.done()
                ]
                if pending:
                    for task in pending:
                        task.cancel()
                    try:
                        loop.run_until_complete(
                            asyncio.gather(
                                *pending,
                                return_exceptions=True,
                            )
                        )
                    except BaseException as exc:
                        cleanup_errors.append(exc)

                self.asyncgens_shutdown_attempted = True
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                except BaseException as exc:
                    cleanup_errors.append(exc)

                shutdown_executor = getattr(
                    loop,
                    "shutdown_default_executor",
                    None,
                )
                if shutdown_executor is not None:
                    self.default_executor_shutdown_attempted = True
                    try:
                        loop.run_until_complete(shutdown_executor())
                    except BaseException as exc:
                        cleanup_errors.append(exc)

                remaining = [
                    task
                    for task in asyncio.all_tasks(loop)
                    if not task.done()
                ]
                self.pending_task_count = len(remaining)
                if remaining:
                    cleanup_errors.append(
                        AssertionError(
                            "ASGI test client left pending asyncio tasks: "
                            + ", ".join(
                                repr(task) for task in remaining
                            )
                        )
                    )
        finally:
            try:
                asyncio.set_event_loop(None)
            except BaseException as exc:
                cleanup_errors.append(exc)
            try:
                if not loop.is_closed():
                    loop.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
            finally:
                self.loop_closed = loop.is_closed()
                self._reset_owned_state()

        if not cleanup_errors:
            return
        if preserve_exception is not None:
            for cleanup_error in cleanup_errors:
                preserve_exception.add_note(
                    "Same-thread ASGI cleanup also failed: "
                    f"{cleanup_error!r}"
                )
            return

        primary_error = cleanup_errors[0]
        for cleanup_error in cleanup_errors[1:]:
            primary_error.add_note(
                "Additional same-thread ASGI cleanup failure: "
                f"{cleanup_error!r}"
            )
        raise primary_error

    def __enter__(self) -> "SameThreadAsgiTestClient":
        if self.loop is not None:
            raise RuntimeError("ASGI test client is already entered")
        self.loop = asyncio.new_event_loop()
        self.loop_closed = False
        self.pending_task_count = 0
        self.asyncgens_shutdown_attempted = False
        self.default_executor_shutdown_attempted = False
        asyncio.set_event_loop(self.loop)
        try:
            self._commands = asyncio.Queue()
            self._started = self.loop.create_future()
            self._driver = self.loop.create_task(self._drive())
            self.loop.run_until_complete(self._started)
        except BaseException as startup_error:
            self._cleanup_owned_loop(
                cancel_driver=True,
                preserve_exception=startup_error,
            )
            raise
        self._entered = True
        return self

    def request(self, method: str, url: str, **kwargs: Any):
        if (
            not self._entered
            or self.loop is None
            or self._commands is None
        ):
            raise RuntimeError("ASGI test client is not entered")
        result = self.loop.create_future()
        self._commands.put_nowait((method, url, kwargs, result))
        return self.loop.run_until_complete(result)

    def get(self, url: str, **kwargs: Any):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any):
        return self.request("POST", url, **kwargs)

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.loop is None:
            return
        self._cleanup_owned_loop(
            cancel_driver=False,
            preserve_exception=exc,
        )
