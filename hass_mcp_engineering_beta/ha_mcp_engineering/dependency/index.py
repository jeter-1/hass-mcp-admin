"""Bounded in-memory dependency index with deterministic generation cursors."""

from __future__ import annotations

import asyncio
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
        self._invalidation_reason = "process_restart"
        self._build_task: asyncio.Task[DependencyIndexSnapshot] | None = None
        self._build_started_at: str | None = None
        self._build_completed_at: str | None = None
        self._last_build_failure_category: str | None = None
        self._prewarm_state = "disabled"
        self._prewarm_started_at: str | None = None
        self._prewarm_completed_at: str | None = None
        self._prewarm_failure_category: str | None = None

    async def prewarm(self, connectivity_check) -> bool:
        """Build once after a successful connectivity probe without blocking startup."""

        if self._prewarm_state in {"checking_connectivity", "building", "complete"}:
            return self._prewarm_state == "complete"
        self._prewarm_state = "checking_connectivity"
        self._prewarm_started_at = datetime.now(timezone.utc).isoformat()
        self._prewarm_completed_at = None
        self._prewarm_failure_category = None
        try:
            await connectivity_check()
        except Exception:
            self._prewarm_state = "failed"
            self._prewarm_failure_category = "connectivity_not_ready"
            self._prewarm_completed_at = datetime.now(timezone.utc).isoformat()
            return False
        self._prewarm_state = "building"
        try:
            await self.get()
        except Exception:
            self._prewarm_state = "failed"
            self._prewarm_failure_category = "build_failed"
            self._prewarm_completed_at = datetime.now(timezone.utc).isoformat()
            return False
        self._prewarm_state = "complete"
        self._prewarm_completed_at = datetime.now(timezone.utc).isoformat()
        return True

    def disable_prewarm(self) -> None:
        self._prewarm_state = "disabled"

    async def get(self, *, refresh: bool = False) -> tuple[DependencyIndexSnapshot, bool, float]:
        lookup_started = time.perf_counter()
        now = time.monotonic()
        valid = self.snapshot and not self.invalidated and now - self.snapshot.built_at_monotonic < self.ttl_seconds
        if valid and not refresh:
            METRICS.record_dependency_cache_hit()
            return self.snapshot, False, (time.perf_counter() - lookup_started) * 1000
        METRICS.record_dependency_cache_miss()
        if self._build_task is None or self._build_task.done():
            self._invalidation_reason = "explicit_refresh" if refresh else self._invalidation_reason
            self._build_task = asyncio.create_task(self._build())
        snapshot = await asyncio.shield(self._build_task)
        return snapshot, True, (time.perf_counter() - lookup_started) * 1000

    async def _build(self) -> DependencyIndexSnapshot:
        build_started = time.perf_counter()
        self._build_started_at = datetime.now(timezone.utc).isoformat()
        self._last_build_failure_category = None
        METRICS.record_dependency_index_build()
        try:
            scan = await self.provider.scan()
        except Exception as exc:
            METRICS.record_dependency_index_failure()
            self._last_build_failure_category = type(exc).__name__
            self._build_completed_at = datetime.now(timezone.utc).isoformat()
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
            build_profile=dict(scan.profile),
        )
        self.invalidated = False
        self._invalidation_reason = "within_ttl"
        self._build_completed_at = datetime.now(timezone.utc).isoformat()
        METRICS.set_dependency_index_state(
            source_count=len(scan.coverage),
            edge_count=len(findings),
            unresolved_count=len(scan.dynamic_references),
            built_at=self.snapshot.built_at,
        )
        return self.snapshot

    def invalidate(self, reason: str = "configuration_changed") -> None:
        self.invalidated = True
        self._invalidation_reason = reason
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
            "build_state": self._build_state(age),
            "validity_reason": self._validity_reason(age),
        }

    def _build_state(self, age: float | None) -> str:
        if self._build_task is not None and not self._build_task.done():
            return "building"
        if self._last_build_failure_category and self.snapshot is None:
            return "failed"
        if self.snapshot is None:
            return "unbuilt"
        if self.invalidated:
            return "invalidated"
        if age is not None and age >= self.ttl_seconds:
            return "expired"
        return "valid"

    def _validity_reason(self, age: float | None) -> str:
        state = self._build_state(age)
        if state == "valid":
            return "within_ttl"
        if state == "expired":
            return "age_expired"
        if state == "failed":
            return "build_failed"
        if state == "unbuilt":
            return "process_restart"
        return self._invalidation_reason

    def health(self) -> dict:
        age = None
        if self.snapshot:
            age = round(max(0.0, time.monotonic() - self.snapshot.built_at_monotonic), 3)
        build_state = self._build_state(age)
        built_at = self.snapshot.built_at if self.snapshot else None
        expires_at = None
        if built_at:
            expires_at = datetime.fromtimestamp(
                datetime.fromisoformat(built_at).timestamp() + self.ttl_seconds,
                timezone.utc,
            ).isoformat()
        return {
            "configured": True,
            "build_state": build_state,
            "validity_reason": self._validity_reason(age),
            "ttl_seconds": self.ttl_seconds,
            "generation": self.snapshot.generation if self.snapshot else 0,
            "fingerprint": self.snapshot.fingerprint[:12] if self.snapshot else None,
            "built_at": built_at,
            "expires_at": expires_at,
            "age_seconds": age,
            "valid": build_state == "valid",
            "invalidated": self.invalidated,
            "build_started_at": self._build_started_at,
            "build_completed_at": self._build_completed_at,
            "build_duration_ms": round(self.snapshot.build_duration_ms, 3) if self.snapshot else None,
            "last_build_failure_category": self._last_build_failure_category,
            "build_progress": (
                {"phase": "network_inventory", "shared_callers": True}
                if build_state == "building"
                else None
            ),
            "last_build_profile": dict(self.snapshot.build_profile) if self.snapshot else None,
            "prewarm_state": self._prewarm_state,
            "prewarm_started_at": self._prewarm_started_at,
            "prewarm_completed_at": self._prewarm_completed_at,
            "prewarm_failure_category": self._prewarm_failure_category,
        }
