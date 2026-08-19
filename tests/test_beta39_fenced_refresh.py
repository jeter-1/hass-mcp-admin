"""B39-136-R2: the governed post-lock refresh is fenced by a build epoch.

A build that started before the lock fence describes the pre-lock world.
Joining it would make the final preflight decide execution eligibility from
evidence read before the operation was serialized.  These tests hold a build
in flight across the fence and require a second, post-fence source read.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

import asyncio  # noqa: E402

from ha_mcp_engineering.dependency.index import (  # noqa: E402
    DependencyFenceError,
    DependencyIndex,
)
from ha_mcp_engineering.dependency.models import (  # noqa: E402
    OBLIGATION_LEDGER_MODEL,
    DependencyScanResult,
    SourceCoverageItem,
)
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DependencySourceProvider,
    ProviderCapability,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    HelperDependencyRiskService,
)


def _coverage():
    return [
        SourceCoverageItem(
            "automation",
            "direct_ha_api",
            "automation_config",
            "complete",
            obligation_ledger_completeness="complete",
        )
    ]


class _GatedProvider(DependencySourceProvider):
    """A provider whose source read can be held open across a fence."""

    provider_id = "direct_ha_api"
    capabilities = frozenset({ProviderCapability.DEPENDENCY_ANALYSIS})

    def __init__(self) -> None:
        self.scan_started = asyncio.Event()
        self.release = asyncio.Event()
        self.scan_count = 0
        self.hold_first_scan = True

    @property
    def available(self) -> bool:
        return True

    async def scan(self):
        self.scan_count += 1
        first = self.scan_count == 1
        if first:
            self.scan_started.set()
        if first and self.hold_first_scan:
            await self.release.wait()
        return DependencyScanResult(
            findings=[],
            dynamic_references=[],
            target_metadata={},
            coverage=_coverage(),
            obligations=[],
            obligation_ledger_model=OBLIGATION_LEDGER_MODEL,
        )

    async def fetch(self, request):
        raise NotImplementedError


class FencedRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def _settle(self):
        for _ in range(6):
            await asyncio.sleep(0)

    async def test_in_flight_prefence_build_cannot_satisfy_the_fence(self):
        provider = _GatedProvider()
        index = DependencyIndex(provider)

        early = asyncio.create_task(index.get(refresh=True))
        await provider.scan_started.wait()
        self.assertEqual(1, provider.scan_count)

        # The lock is taken here.  Anything already reading source predates it.
        fence = index.open_source_fence("test_lock")
        provider.hold_first_scan = False
        fenced = asyncio.create_task(index.get(min_source_epoch=fence))
        await self._settle()

        # The fenced caller must not be satisfied by the in-flight read.
        self.assertFalse(fenced.done())
        provider.release.set()
        early_snapshot, _, _ = await early
        fenced_snapshot, refreshed, _ = await fenced

        self.assertEqual(2, provider.scan_count)
        self.assertLess(early_snapshot.source_epoch, fence)
        self.assertGreaterEqual(fenced_snapshot.source_epoch, fence)
        self.assertTrue(refreshed)
        self.assertIsNot(early_snapshot, fenced_snapshot)

    async def test_multiple_fenced_callers_share_one_post_fence_scan(self):
        provider = _GatedProvider()
        index = DependencyIndex(provider)

        early = asyncio.create_task(index.get(refresh=True))
        await provider.scan_started.wait()
        fence = index.open_source_fence("test_lock")
        provider.hold_first_scan = False

        callers = [
            asyncio.create_task(index.get(min_source_epoch=fence))
            for _ in range(3)
        ]
        await self._settle()
        provider.release.set()
        await early
        results = await asyncio.gather(*callers)

        # One shared post-fence build serves every fenced caller.
        self.assertEqual(2, provider.scan_count)
        for snapshot, refreshed, _ in results:
            self.assertGreaterEqual(snapshot.source_epoch, fence)
            self.assertTrue(refreshed)

    async def test_cancelling_one_fenced_caller_does_not_cancel_the_build(self):
        provider = _GatedProvider()
        index = DependencyIndex(provider)

        early = asyncio.create_task(index.get(refresh=True))
        await provider.scan_started.wait()
        fence = index.open_source_fence("test_lock")
        provider.hold_first_scan = False

        abandoned = asyncio.create_task(index.get(min_source_epoch=fence))
        survivor = asyncio.create_task(index.get(min_source_epoch=fence))
        await self._settle()
        abandoned.cancel()
        provider.release.set()
        await early
        with self.assertRaises(asyncio.CancelledError):
            await abandoned
        snapshot, refreshed, _ = await survivor

        self.assertGreaterEqual(snapshot.source_epoch, fence)
        self.assertTrue(refreshed)
        self.assertIsNotNone(index.snapshot)

    async def test_invalidation_during_a_build_is_not_cleared_by_it(self):
        provider = _GatedProvider()
        index = DependencyIndex(provider)

        build = asyncio.create_task(index.get(refresh=True))
        await provider.scan_started.wait()
        # Configuration changed while this build was already reading source.
        index.invalidate("configuration_changed")
        provider.hold_first_scan = False
        provider.release.set()
        await build

        self.assertTrue(
            index.invalidated,
            "a pre-invalidation read cleared a later invalidation",
        )
        self.assertIsNotNone(index.snapshot)

        # A build that starts after the invalidation may clear it.
        await index.get(refresh=True)
        self.assertFalse(index.invalidated)

    async def test_fence_failure_is_bounded_and_fails_closed(self):
        class NeverCurrentIndex(DependencyIndex):
            async def _build(self, mode):
                snapshot = await super()._build(mode)
                # Simulate a source read that can never observe the fence.
                object.__setattr__(snapshot, "source_epoch", -1)
                return snapshot

        provider = _GatedProvider()
        provider.hold_first_scan = False
        index = NeverCurrentIndex(provider)
        fence = index.open_source_fence("test_lock")
        with self.assertRaises(DependencyFenceError):
            await index.get(min_source_epoch=fence)


class FencedHelperRiskServiceTests(unittest.IsolatedAsyncioTestCase):
    """The helper risk service opens the fence only for the post-lock read."""

    async def test_planning_read_is_not_fenced(self):
        observed: list[int | None] = []

        class RecordingIndex:
            snapshot = None

            def open_source_fence(self, reason="governed_lock_fence"):
                raise AssertionError("planning must not open a fence")

            async def get(self, *, refresh=False, min_source_epoch=None):
                observed.append(min_source_epoch)
                raise RuntimeError("provider unavailable")

        evidence = await HelperDependencyRiskService(RecordingIndex()).assess(
            "input_boolean.example", refresh=True
        )
        self.assertEqual([None], observed)
        self.assertEqual("failed", evidence["binding"]["completeness"])
        self.assertFalse(evidence["binding"]["execution_eligible"])

    async def test_preflight_read_opens_and_requires_the_fence(self):
        provider = _GatedProvider()
        provider.hold_first_scan = False
        index = DependencyIndex(provider)
        await index.get(refresh=True)
        before = provider.scan_count

        evidence = await HelperDependencyRiskService(index).assess(
            "input_boolean.example", refresh=True, fenced=True
        )

        self.assertGreater(provider.scan_count, before)
        self.assertTrue(evidence["provenance"]["fenced"])
        self.assertGreaterEqual(
            evidence["provenance"]["source_epoch"], 1
        )


if __name__ == "__main__":
    unittest.main()
