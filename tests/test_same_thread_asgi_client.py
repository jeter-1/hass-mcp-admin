"""Lifecycle regressions for the same-thread ASGI test client."""

from __future__ import annotations

from contextlib import asynccontextmanager
import threading
import unittest

import anyio
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from tests.same_thread_asgi_client import SameThreadAsgiTestClient


class SameThreadAsgiTestClientTests(unittest.TestCase):
    def test_repeated_lifespan_closes_without_threads_or_pending_tasks(
        self,
    ):
        baseline_threads = {thread.ident for thread in threading.enumerate()}
        lifecycle_events: list[str] = []

        @asynccontextmanager
        async def lifespan(_app):
            async with anyio.create_task_group() as task_group:
                lifecycle_events.append("startup")
                try:
                    yield
                finally:
                    lifecycle_events.append("shutdown")
                    task_group.cancel_scope.cancel()

        async def health(_request):
            return JSONResponse({"status": "ok"})

        for _ in range(2):
            app = Starlette(
                routes=[Route("/health", health)],
                lifespan=lifespan,
            )
            client_context = SameThreadAsgiTestClient(app)
            with client_context as client:
                response = client.get("/health")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"status": "ok"})
            self.assertTrue(client_context.loop_closed)
            self.assertEqual(client_context.pending_task_count, 0)
            self.assertEqual(
                {thread.ident for thread in threading.enumerate()},
                baseline_threads,
            )

        self.assertEqual(
            lifecycle_events,
            ["startup", "shutdown", "startup", "shutdown"],
        )
