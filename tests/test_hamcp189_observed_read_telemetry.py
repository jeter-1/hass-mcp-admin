"""HAMCP-189 R2-telemetry: a refused ha-mcp read is not counted as an attempt.

The operational lifecycle provider's observed reads (add-on inventory and
identity, which cancel and apply-prepare use) count an upstream attempt only
after the request's Core authorizer admits the dispatch.  A refused read
reports zero attempts, every begun attempt is finished, and a dispatch the
caller has stopped waiting for is not started.  All data is synthetic.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.providers.operational_lifecycle import (  # noqa: E402
    OperationalLifecycleProviderError,
    ReviewedOperationalLifecycleProvider,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)

from tests.test_2_1a_beta2_operational_lifecycle import (  # noqa: E402
    FakeMcpTransport,
    UPSTREAM_ADDON_SLUG,
    lifecycle_settings,
)


ENDPOINT = "http://abcdef12-ha-mcp:9583/synthetic-upstream-secret/mcp"


def provider_with(transport):
    provider = ReviewedOperationalLifecycleProvider()
    provider.configure(lifecycle_settings(ENDPOINT), transport=transport)
    return provider


class ObservedReadTelemetryTests(unittest.IsolatedAsyncioTestCase):
    def assert_balanced(self, telemetry):
        self.assertEqual(telemetry.upstream_active_requests, 0)
        self.assertLessEqual(telemetry.upstream_max_concurrent_requests, 1)

    async def get_addon(self, provider, authorizer, request_id):
        telemetry, token = begin_request(request_id)
        telemetry.core_dispatch_authorizer = authorizer
        error = None
        try:
            await provider.get_addon(UPSTREAM_ADDON_SLUG)
        except OperationalLifecycleProviderError as exc:
            error = exc
        finally:
            end_request(token)
        return telemetry, error

    async def test_refused_read_reports_zero_upstream_attempts(self):
        transport = FakeMcpTransport()
        telemetry, error = await self.get_addon(
            provider_with(transport), lambda: False, "r2-refused"
        )
        self.assertIsNotNone(error)
        self.assertEqual(error.category, "provider_unavailable")
        self.assertFalse(error.dispatched)
        self.assertEqual(transport.calls, [])
        self.assertEqual(telemetry.upstream_request_count, 0)
        self.assertEqual(telemetry.upstream_duration_ms, 0.0)
        self.assertIsNone(telemetry.upstream_span_started)
        self.assert_balanced(telemetry)

    async def test_retired_authority_counts_only_the_admitted_read(self):
        transport = FakeMcpTransport()
        admitted = iter((True,))
        telemetry, error = await self.get_addon(
            provider_with(transport),
            lambda: next(admitted, False),
            "r2-retired",
        )
        self.assertIsNotNone(error)
        self.assertEqual(error.category, "provider_unavailable")
        self.assertEqual(len(transport.calls), 1)
        # Only the inventory read was dispatched; the refused identity read is
        # not an attempt.
        self.assertEqual(telemetry.upstream_request_count, 1)
        self.assert_balanced(telemetry)

    async def test_admitted_reads_count_every_dispatch(self):
        # Preservation: an admitted request counts each dispatched read once.
        transport = FakeMcpTransport()
        telemetry, error = await self.get_addon(
            provider_with(transport), lambda: True, "r2-admitted"
        )
        self.assertIsNone(error)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(telemetry.upstream_request_count, 2)
        self.assertIsNotNone(telemetry.upstream_span_started)
        self.assertIsNotNone(telemetry.upstream_span_finished)
        self.assert_balanced(telemetry)

    async def test_failure_before_dispatch_is_not_an_attempt(self):
        # A transport failure before the dispatch hook runs (catalog or
        # session failure) reached no upstream tool call.
        class CatalogFailure(FakeMcpTransport):
            async def execute_read(self, *args, **kwargs):
                raise OSError("synthetic catalog failure")

        transport = CatalogFailure()
        telemetry, error = await self.get_addon(
            provider_with(transport), lambda: True, "r2-catalog"
        )
        self.assertIsNotNone(error)
        self.assertEqual(telemetry.upstream_request_count, 0)
        self.assert_balanced(telemetry)

    async def test_late_dispatch_after_the_caller_returned_is_not_started(self):
        # A retained-session worker can run the dispatch hook after the
        # caller stopped waiting (timeout).  The hook then refuses, so no
        # attempt is begun without a matching finish and nothing dispatches.
        hooks = []

        class AbandonedTransport(FakeMcpTransport):
            async def execute_read(
                self, tool_name, arguments, *, before_dispatch=None, **kwargs
            ):
                hooks.append(before_dispatch)
                raise TimeoutError("synthetic caller timeout")

        transport = AbandonedTransport()
        telemetry, error = await self.get_addon(
            provider_with(transport), lambda: True, "r2-abandoned"
        )
        self.assertIsNotNone(error)
        self.assertEqual(len(hooks), 1)
        with self.assertRaises(OperationalLifecycleProviderError) as raised:
            await hooks[0]()
        self.assertEqual(raised.exception.category, "provider_unavailable")
        self.assertFalse(raised.exception.dispatched)
        self.assertEqual(telemetry.upstream_request_count, 0)
        self.assert_balanced(telemetry)


if __name__ == "__main__":
    unittest.main()
