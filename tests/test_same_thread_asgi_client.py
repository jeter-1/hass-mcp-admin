"""Lifecycle regressions for the same-thread ASGI test client."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import gc
import threading
import unittest
from unittest.mock import patch
import warnings

import anyio
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from tests.same_thread_asgi_client import SameThreadAsgiTestClient


class StartupFailure(RuntimeError):
    """Known lifespan-startup failure used by the test fixture."""


class RequestFailure(RuntimeError):
    """Known endpoint failure used by the test fixture."""


class ShutdownFailure(RuntimeError):
    """Known lifespan-shutdown failure used by the test fixture."""


async def _health(_request):
    return JSONResponse({"status": "ok"})


def _healthy_app() -> Starlette:
    return Starlette(routes=[Route("/health", _health)])


class SameThreadAsgiTestClientTests(unittest.TestCase):
    def assert_client_reset(
        self,
        client: SameThreadAsgiTestClient,
    ) -> None:
        self.assertTrue(client.loop_closed)
        self.assertEqual(client.pending_task_count, 0)
        self.assertIsNone(client.loop)
        self.assertIsNone(client._driver)
        self.assertIsNone(client._commands)
        self.assertIsNone(client._started)
        self.assertIsNone(client._client)
        self.assertFalse(client._entered)
        self.assertFalse(client._lifespan_started)

    def assert_owned_loop_is_not_current(
        self,
        owned_loop: asyncio.AbstractEventLoop,
    ) -> None:
        try:
            current_loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        try:
            self.assertIsNot(current_loop, owned_loop)
        finally:
            if not current_loop.is_closed():
                current_loop.close()
            asyncio.set_event_loop(None)

    def assert_no_unretrieved_task_error(
        self,
        contexts: list[dict],
    ) -> None:
        messages = [str(context.get("message", "")) for context in contexts]
        self.assertFalse(
            [
                message
                for message in messages
                if "Task exception was never retrieved" in message
                or "Task was destroyed but it is pending" in message
            ],
            messages,
        )

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

        async def request_number(request):
            return JSONResponse(
                {"request_number": request.path_params["number"]}
            )

        for _ in range(2):
            app = Starlette(
                routes=[
                    Route(
                        "/request/{number}",
                        request_number,
                    )
                ],
                lifespan=lifespan,
            )
            client_context = SameThreadAsgiTestClient(app)
            with client_context as client:
                owned_loop = client.loop
                self.assertIsNotNone(owned_loop)
                first = client.get("/request/1")
                self.assertEqual(first.json(), {"request_number": "1"})
                self.assertIs(client.loop, owned_loop)
                second = client.get("/request/2")
                self.assertEqual(second.json(), {"request_number": "2"})
                self.assertIs(client.loop, owned_loop)
            self.assert_client_reset(client_context)
            self.assert_owned_loop_is_not_current(owned_loop)
            self.assertEqual(
                {thread.ident for thread in threading.enumerate()},
                baseline_threads,
            )

        self.assertEqual(
            lifecycle_events,
            ["startup", "shutdown", "startup", "shutdown"],
        )

    def test_lifespan_startup_failure_releases_all_owned_state(self):
        baseline_threads = {thread.ident for thread in threading.enumerate()}
        startup_failure = StartupFailure("known startup failure")

        @asynccontextmanager
        async def failing_lifespan(_app):
            raise startup_failure
            yield

        failed_app = Starlette(lifespan=failing_lifespan)
        failed_client = SameThreadAsgiTestClient(failed_app)
        owned_loop = asyncio.new_event_loop()
        loop_contexts: list[dict] = []
        owned_loop.set_exception_handler(
            lambda _loop, context: loop_contexts.append(context)
        )

        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always")
            with patch(
                "tests.same_thread_asgi_client.asyncio.new_event_loop",
                return_value=owned_loop,
            ):
                with self.assertRaises(StartupFailure) as caught:
                    failed_client.__enter__()
            gc.collect()

        self.assertIs(caught.exception, startup_failure)
        self.assert_client_reset(failed_client)
        self.assertTrue(failed_client.asyncgens_shutdown_attempted)
        self.assertTrue(
            failed_client.default_executor_shutdown_attempted
        )
        self.assert_owned_loop_is_not_current(owned_loop)
        self.assert_no_unretrieved_task_error(loop_contexts)
        self.assertFalse(
            [
                str(warning.message)
                for warning in caught_warnings
                if "Task exception was never retrieved"
                in str(warning.message)
            ]
        )
        self.assertEqual(
            {thread.ident for thread in threading.enumerate()},
            baseline_threads,
        )

        subsequent_client = SameThreadAsgiTestClient(_healthy_app())
        with subsequent_client as client:
            self.assertEqual(client.get("/health").status_code, 200)
        self.assert_client_reset(subsequent_client)

    def test_startup_exception_remains_primary_when_cleanup_fails(self):
        startup_failure = StartupFailure("primary startup failure")
        cleanup_failure = ShutdownFailure("secondary cleanup failure")

        @asynccontextmanager
        async def failing_lifespan(_app):
            raise startup_failure
            yield

        async def failing_executor_shutdown():
            raise cleanup_failure

        owned_loop = asyncio.new_event_loop()
        owned_loop.shutdown_default_executor = failing_executor_shutdown
        client_context = SameThreadAsgiTestClient(
            Starlette(lifespan=failing_lifespan)
        )
        with patch(
            "tests.same_thread_asgi_client.asyncio.new_event_loop",
            return_value=owned_loop,
        ):
            with self.assertRaises(StartupFailure) as caught:
                client_context.__enter__()

        self.assertIs(caught.exception, startup_failure)
        self.assertTrue(
            any(
                "secondary cleanup failure" in note
                for note in getattr(caught.exception, "__notes__", [])
            )
        )
        self.assert_client_reset(client_context)

    def test_request_failure_preserves_driver_and_later_request(self):
        baseline_threads = {thread.ident for thread in threading.enumerate()}
        request_failure = RequestFailure("known request failure")

        async def fail(_request):
            raise request_failure

        app = Starlette(
            routes=[
                Route("/fail", fail),
                Route("/health", _health),
            ]
        )
        client_context = SameThreadAsgiTestClient(app)
        with client_context as client:
            owned_loop = client.loop
            with self.assertRaises(RequestFailure) as caught:
                client.get("/fail")
            self.assertIs(caught.exception, request_failure)
            self.assertTrue(client._entered)
            self.assertIsNotNone(client._driver)
            self.assertFalse(client._driver.done())
            self.assertEqual(client.get("/health").status_code, 200)

        self.assert_client_reset(client_context)
        self.assert_owned_loop_is_not_current(owned_loop)
        self.assertEqual(
            {thread.ident for thread in threading.enumerate()},
            baseline_threads,
        )

    def test_lifespan_shutdown_failure_still_closes_owned_loop(self):
        baseline_threads = {thread.ident for thread in threading.enumerate()}
        shutdown_failure = ShutdownFailure("known shutdown failure")

        @asynccontextmanager
        async def failing_lifespan(_app):
            yield
            raise shutdown_failure

        app = Starlette(
            routes=[Route("/health", _health)],
            lifespan=failing_lifespan,
        )
        client_context = SameThreadAsgiTestClient(app)
        client = client_context.__enter__()
        owned_loop = client.loop
        loop_contexts: list[dict] = []
        owned_loop.set_exception_handler(
            lambda _loop, context: loop_contexts.append(context)
        )
        self.assertEqual(client.get("/health").status_code, 200)

        with self.assertRaises(ShutdownFailure) as caught:
            client_context.__exit__(None, None, None)

        self.assertIs(caught.exception, shutdown_failure)
        self.assertTrue(client_context.asyncgens_shutdown_attempted)
        self.assertTrue(
            client_context.default_executor_shutdown_attempted
        )
        self.assert_client_reset(client_context)
        self.assert_owned_loop_is_not_current(owned_loop)
        self.assert_no_unretrieved_task_error(loop_contexts)
        self.assertEqual(
            {thread.ident for thread in threading.enumerate()},
            baseline_threads,
        )

    def test_body_exception_remains_primary_when_shutdown_fails(self):
        shutdown_failure = ShutdownFailure("secondary shutdown failure")
        body_failure = RequestFailure("primary body failure")

        @asynccontextmanager
        async def failing_lifespan(_app):
            yield
            raise shutdown_failure

        client_context = SameThreadAsgiTestClient(
            Starlette(lifespan=failing_lifespan)
        )
        with self.assertRaises(RequestFailure) as caught:
            with client_context:
                raise body_failure

        self.assertIs(caught.exception, body_failure)
        self.assertTrue(
            any(
                "secondary shutdown failure" in note
                for note in getattr(caught.exception, "__notes__", [])
            )
        )
        self.assert_client_reset(client_context)

    def test_failure_modes_are_isolated_in_one_process(self):
        baseline_threads = {thread.ident for thread in threading.enumerate()}

        @asynccontextmanager
        async def failing_startup(_app):
            raise StartupFailure("isolated startup failure")
            yield

        startup_client = SameThreadAsgiTestClient(
            Starlette(lifespan=failing_startup)
        )
        with self.assertRaises(StartupFailure):
            startup_client.__enter__()
        self.assert_client_reset(startup_client)

        healthy_client = SameThreadAsgiTestClient(_healthy_app())
        with healthy_client as client:
            self.assertEqual(client.get("/health").status_code, 200)
        self.assert_client_reset(healthy_client)

        @asynccontextmanager
        async def failing_shutdown(_app):
            yield
            raise ShutdownFailure("isolated shutdown failure")

        shutdown_client = SameThreadAsgiTestClient(
            Starlette(lifespan=failing_shutdown)
        )
        shutdown_client.__enter__()
        with self.assertRaises(ShutdownFailure):
            shutdown_client.__exit__(None, None, None)
        self.assert_client_reset(shutdown_client)

        final_client = SameThreadAsgiTestClient(_healthy_app())
        with final_client as client:
            self.assertEqual(client.get("/health").status_code, 200)
        self.assert_client_reset(final_client)
        self.assertEqual(
            {thread.ident for thread in threading.enumerate()},
            baseline_threads,
        )
