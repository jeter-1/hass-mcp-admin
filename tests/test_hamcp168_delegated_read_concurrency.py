"""HAMCP-168 characterization: Engineering does not serialize delegated reads.

Live 2.4.1-beta.3 evidence showed bursts of concurrent delegated `ha_search`
calls finishing together (~56 s for five, ~40 s for three) with
`completeness=partial`. These offline tests pin the Engineering side of that
path with synthetic peers: concurrent delegated reads are all in flight at
once, each completes as soon as its own upstream exchange does, and
`partial` is ha-mcp's own flag passed through, not something Engineering
derives from latency or concurrency. The lockstep itself is therefore not
reproducible here; it is attributed to the upstream ha-mcp provider.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import unittest

from tests.test_readonly_upstream_gateway import (
    FakeTransport,
    McpReadResult,
    catalog_tool,
    initialize,
    policy_entry,
    registered_tools,
)

_BURST = 5


class _HeldSearchTransport(FakeTransport):
    """Holds each ha_search exchange until the test releases it by key."""

    def __init__(self, tools, *, partial_by_key):
        super().__init__(tools)
        self.partial_by_key = partial_by_key
        self.in_flight = 0
        self.max_in_flight = 0
        self.entered = {key: asyncio.Event() for key in partial_by_key}
        self.release = {key: asyncio.Event() for key in partial_by_key}

    async def execute_read(
        self,
        tool_name,
        arguments,
        *,
        timeout_seconds,
        catalog_validator,
        before_dispatch=None,
    ):
        self.attempts.append((tool_name, dict(arguments), timeout_seconds))
        catalog = self.catalog
        catalog_validator(catalog)
        if before_dispatch is not None:
            prepared = before_dispatch()
            if inspect.isawaitable(prepared):
                await prepared
        result = self.result
        if tool_name == "ha_search":
            key = arguments["entity_id"]
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            self.entered[key].set()
            try:
                await self.release[key].wait()
            finally:
                self.in_flight -= 1
            result = {
                "structuredContent": {
                    "results": [{"entity_id": key}],
                    "partial": self.partial_by_key[key],
                },
                "isError": False,
            }
        self.calls.append((tool_name, dict(arguments), timeout_seconds))
        return McpReadResult(
            protocol_version=catalog.protocol_version,
            server_name=catalog.server_name,
            server_version=catalog.server_version,
            call_result=result,
            connection_latency_ms=1.0,
            tool_call_latency_ms=2.0,
        )


class ConcurrentDelegatedReadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.keys = [f"sensor.hamcp168_{index}" for index in range(_BURST)]
        # Mixed upstream flags: partial must follow ha-mcp, call by call.
        self.partial_by_key = {
            key: index % 2 == 0 for index, key in enumerate(self.keys)
        }
        entries = [policy_entry("ha_search"), policy_entry("ha_get_state")]
        tools = [catalog_tool(entry.upstream_name) for entry in entries]
        self.transport = _HeldSearchTransport(
            tools, partial_by_key=self.partial_by_key
        )
        _gateway, server, _ = await initialize(
            entries, tools, transport=self.transport
        )
        self.search = registered_tools(server).get("ha_search")
        self.get_state = registered_tools(server).get("ha_get_state")

    async def test_burst_is_fully_concurrent_and_completes_individually(self):
        tasks = {
            key: asyncio.create_task(self.search.run({"entity_id": key}))
            for key in self.keys
        }
        # All five reach the upstream exchange together: no Engineering lock,
        # queue or semaphore admits them one at a time.
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in self.transport.entered.values())),
            timeout=2,
        )
        self.assertEqual(_BURST, self.transport.max_in_flight)

        # An unrelated delegated read is not queued behind the held burst.
        state = json.loads(
            await asyncio.wait_for(
                self.get_state.run({"entity_id": "sun.sun"}), timeout=2
            )
        )
        self.assertTrue(state["success"])
        self.assertTrue(all(not task.done() for task in tasks.values()))

        # Release in reverse order: each call returns when its own upstream
        # exchange does. Engineering does not batch or release them together.
        for key in reversed(self.keys):
            self.transport.release[key].set()
            value = json.loads(await asyncio.wait_for(tasks[key], timeout=2))
            self.assertTrue(value["success"])
            expected = "partial" if self.partial_by_key[key] else "complete"
            self.assertEqual(expected, value["metadata"]["completeness"])
            pending = [k for k in self.keys if not tasks[k].done()]
            self.assertEqual(self.keys[: self.keys.index(key)], pending)
        self.assertEqual(0, self.transport.in_flight)


if __name__ == "__main__":
    unittest.main()
