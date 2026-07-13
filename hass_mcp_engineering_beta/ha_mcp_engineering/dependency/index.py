"""Bounded in-memory dependency index with deterministic generation cursors."""

from __future__ import annotations

from datetime import datetime, timezone
import time

from ..observability import METRICS
from .models import DependencyIndexSnapshot, snapshot_fingerprint
from .provider import DependencySourceProvider


class DependencyIndex:
    def __init__(self, provider: DependencySourceProvider, *, ttl_seconds: float = 300.0, max_edges: int = 10_000):
        self.provider = provider
        self.ttl_seconds = max(1.0, ttl_seconds)
        self.max_edges = max(100, min(max_edges, 50_000))
        self.snapshot: DependencyIndexSnapshot | None = None
        self.generation = 0
        self.invalidated = False

    async def get(self, *, refresh: bool = False) -> tuple[DependencyIndexSnapshot, bool, float]:
        lookup_started = time.perf_counter()
        now = time.monotonic()
        valid = self.snapshot and not self.invalidated and now - self.snapshot.built_at_monotonic < self.ttl_seconds
        if valid and not refresh:
            METRICS.record_dependency_cache_hit()
            return self.snapshot, False, (time.perf_counter() - lookup_started) * 1000
        METRICS.record_dependency_cache_miss()
        snapshot = await self._build()
        return snapshot, True, (time.perf_counter() - lookup_started) * 1000

    async def _build(self) -> DependencyIndexSnapshot:
        build_started = time.perf_counter()
        METRICS.record_dependency_index_build()
        try:
            scan = await self.provider.scan()
        except Exception:
            METRICS.record_dependency_index_failure()
            raise
        self.generation += 1
        findings = sorted(scan.findings, key=lambda item: item.evidence_id)[: self.max_edges]
        if len(scan.findings) > self.max_edges:
            METRICS.record_dependency_truncation()
        fingerprint = snapshot_fingerprint(findings, scan.coverage, self.generation)
        build_duration_ms = (time.perf_counter() - build_started) * 1000
        self.snapshot = DependencyIndexSnapshot(
            fingerprint=fingerprint,
            generation=self.generation,
            built_at_monotonic=time.monotonic(),
            built_at=datetime.now(timezone.utc).isoformat(),
            findings=tuple(findings),
            dynamic_references=tuple(scan.dynamic_references[:1000]),
            target_metadata=scan.target_metadata,
            coverage=tuple(scan.coverage),
            build_duration_ms=build_duration_ms,
        )
        self.invalidated = False
        METRICS.set_dependency_index_state(
            source_count=len(scan.coverage),
            edge_count=len(findings),
            unresolved_count=len(scan.dynamic_references),
            built_at=self.snapshot.built_at,
        )
        return self.snapshot

    def invalidate(self) -> None:
        self.invalidated = True
        METRICS.record_dependency_invalidation()

    def active_identity(self) -> dict[str, object]:
        """Return the committed index identity without building or refreshing it."""

        snapshot = self.snapshot
        age = (
            max(0.0, time.monotonic() - snapshot.built_at_monotonic)
            if snapshot
            else None
        )
        valid = bool(
            snapshot
            and not self.invalidated
            and age is not None
            and age < self.ttl_seconds
        )
        return {
            "generation": snapshot.generation if snapshot else 0,
            "fingerprint": snapshot.fingerprint if snapshot else None,
            "valid": valid,
            "invalidated": self.invalidated,
        }

    def health(self) -> dict:
        age = None
        if self.snapshot:
            age = round(max(0.0, time.monotonic() - self.snapshot.built_at_monotonic), 3)
        return {
            "configured": True,
            "generation": self.snapshot.generation if self.snapshot else 0,
            "fingerprint": self.snapshot.fingerprint[:12] if self.snapshot else None,
            "age_seconds": age,
            "valid": bool(self.snapshot and not self.invalidated and age is not None and age < self.ttl_seconds),
            "invalidated": self.invalidated,
            "build_duration_ms": round(self.snapshot.build_duration_ms, 3) if self.snapshot else None,
        }
