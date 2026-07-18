"""Bounded, single-flight dependency index with explicit evidence freshness."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import time
from typing import Any

from ..observability import METRICS
from .models import DependencyIndexSnapshot, snapshot_fingerprint
from .provider import DependencySourceProvider


DEFAULT_SOFT_TTL_SECONDS = 600.0
DEFAULT_HARD_TTL_SECONDS = 3600.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _failure_category(exc: BaseException) -> str:
    value = getattr(exc, "category", None)
    if isinstance(value, str) and value:
        return value[:64]
    return type(exc).__name__[:64]


class DependencyIndex:
    """Keep the last good snapshot while refreshing it through one shared task."""

    def __init__(
        self,
        provider: DependencySourceProvider,
        *,
        soft_ttl_seconds: float = DEFAULT_SOFT_TTL_SECONDS,
        hard_ttl_seconds: float = DEFAULT_HARD_TTL_SECONDS,
        ttl_seconds: float | None = None,
        max_edges: int = 10_000,
    ):
        # ``ttl_seconds`` remains an internal construction alias for older tests and
        # integrations. It now means soft TTL; the hard bound is always greater.
        if ttl_seconds is not None:
            soft_ttl_seconds = ttl_seconds
            hard_ttl_seconds = max(hard_ttl_seconds, float(ttl_seconds) + 1.0)
        self.soft_ttl_seconds = max(1.0, float(soft_ttl_seconds))
        self.hard_ttl_seconds = max(
            self.soft_ttl_seconds + 1.0, float(hard_ttl_seconds)
        )
        self.ttl_seconds = self.soft_ttl_seconds  # compatibility/diagnostic alias
        self.provider = provider
        self.max_edges = max(100, min(max_edges, 50_000))
        self.snapshot: DependencyIndexSnapshot | None = None
        self.generation = 0
        self.invalidated = False
        self._invalidation_reason = "process_restart"
        self._build_task: asyncio.Task[DependencyIndexSnapshot] | None = None
        self._build_mode: str | None = None
        self._build_started_at: str | None = None
        self._build_completed_at: str | None = None
        self._last_build_failure_category: str | None = None
        self._background_refresh_started_at: str | None = None
        self._last_refresh_completed_at: str | None = None
        self._last_refresh_failure_category: str | None = None
        self._prewarm_state = "disabled"
        self._prewarm_started_at: str | None = None
        self._prewarm_completed_at: str | None = None
        self._prewarm_failure_category: str | None = None
        self._prewarm_attempt_count = 0
        self._next_prewarm_retry_at: str | None = None

    def configure_prewarm(self, *, enabled: bool) -> None:
        self._prewarm_state = "scheduled" if enabled else "disabled"
        if not enabled:
            self._next_prewarm_retry_at = None

    def note_prewarm_retry(self, delay_seconds: float) -> None:
        self._next_prewarm_retry_at = (
            datetime.now(timezone.utc) + timedelta(seconds=max(1.0, delay_seconds))
        ).isoformat()

    async def prewarm(self, connectivity_check) -> bool:
        """Attempt one nonblocking-runtime prewarm through the normal build path."""

        # An on-demand caller may have completed the shared build during the
        # startup delay. Treat that current generation as a successful prewarm
        # instead of launching a redundant refresh.
        if self._is_current(self._age()):
            self._prewarm_state = "complete"
            self._prewarm_completed_at = _utc_now()
            self._prewarm_failure_category = None
            self._next_prewarm_retry_at = None
            return True
        self._prewarm_attempt_count += 1
        self._prewarm_state = "checking_connectivity"
        self._prewarm_started_at = _utc_now()
        self._prewarm_completed_at = None
        self._prewarm_failure_category = None
        self._next_prewarm_retry_at = None
        try:
            await connectivity_check()
        except Exception:
            self._prewarm_state = "failed"
            self._prewarm_failure_category = "connectivity_not_ready"
            self._prewarm_completed_at = _utc_now()
            return False
        self._prewarm_state = "building"
        try:
            await self.get(refresh=self.snapshot is not None)
        except Exception:
            self._prewarm_state = "failed"
            self._prewarm_failure_category = "build_failed"
            self._prewarm_completed_at = _utc_now()
            return False
        self._prewarm_state = "complete"
        self._prewarm_completed_at = _utc_now()
        return True

    def disable_prewarm(self) -> None:
        self.configure_prewarm(enabled=False)

    def _age(self, snapshot: DependencyIndexSnapshot | None = None) -> float | None:
        value = snapshot if snapshot is not None else self.snapshot
        if value is None:
            return None
        return max(0.0, time.monotonic() - value.built_at_monotonic)

    def _is_current(self, age: float | None) -> bool:
        return bool(
            self.snapshot
            and not self.invalidated
            and age is not None
            and age < self.soft_ttl_seconds
        )

    def _is_usable(self, age: float | None) -> bool:
        return bool(
            self.snapshot
            and not self.invalidated
            and age is not None
            and age < self.hard_ttl_seconds
        )

    async def get(self, *, refresh: bool = False) -> tuple[DependencyIndexSnapshot, bool, float]:
        """Return current/stale-usable evidence or await one mandatory build.

        Soft-expired evidence is returned immediately while a manager-owned refresh
        runs. Awaiters are shielded so cancelling one caller cannot cancel the shared
        build.
        """

        lookup_started = time.perf_counter()
        age = self._age()
        if self._is_current(age) and not refresh:
            METRICS.record_dependency_cache_hit()
            return self.snapshot, False, (time.perf_counter() - lookup_started) * 1000

        METRICS.record_dependency_cache_miss()
        if self._is_usable(age) and not refresh:
            self._ensure_build(mode="background_refresh")
            return self.snapshot, False, (time.perf_counter() - lookup_started) * 1000

        reason = "explicit_refresh" if refresh else (
            "configuration_changed" if self.invalidated else "age_expired"
        )
        task = self._ensure_build(
            mode="foreground_refresh" if self.snapshot is not None else "initial",
            reason=reason,
        )
        snapshot = await asyncio.shield(task)
        return snapshot, True, (time.perf_counter() - lookup_started) * 1000

    def _ensure_build(
        self,
        *,
        mode: str,
        reason: str | None = None,
    ) -> asyncio.Task[DependencyIndexSnapshot]:
        task = self._build_task
        if task is not None and not task.done():
            return task
        if reason:
            self._invalidation_reason = reason
        self._build_mode = mode
        if mode == "background_refresh":
            self._background_refresh_started_at = _utc_now()
        task = asyncio.create_task(self._build(mode), name="dependency-index-build")
        self._build_task = task
        task.add_done_callback(self._consume_background_result)
        return task

    @staticmethod
    def _consume_background_result(task: asyncio.Task[DependencyIndexSnapshot]) -> None:
        # Retrieve the result even when no foreground caller awaits a soft refresh.
        # Awaiters can still observe the same result or exception.
        if task.cancelled():
            return
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass

    async def _build(self, mode: str) -> DependencyIndexSnapshot:
        build_started = time.perf_counter()
        self._build_started_at = _utc_now()
        self._last_build_failure_category = None
        METRICS.record_dependency_index_build()
        try:
            scan = await self.provider.scan()
            next_generation = self.generation + 1
            findings = sorted(scan.findings, key=lambda item: item.evidence_id)[: self.max_edges]
            if len(scan.findings) > self.max_edges:
                METRICS.record_dependency_truncation()
            fingerprint = snapshot_fingerprint(findings, scan.coverage, next_generation)
            build_duration_ms = (time.perf_counter() - build_started) * 1000
            replacement = DependencyIndexSnapshot(
                fingerprint=fingerprint,
                generation=next_generation,
                built_at_monotonic=time.monotonic(),
                built_at=_utc_now(),
                findings=tuple(findings),
                dynamic_references=tuple(scan.dynamic_references[:1000]),
                target_metadata=scan.target_metadata,
                coverage=tuple(scan.coverage),
                build_duration_ms=build_duration_ms,
                build_profile=dict(scan.profile),
            )
            # Publish the complete replacement atomically after every build step.
            self.snapshot = replacement
            self.generation = next_generation
            self.invalidated = False
            self._invalidation_reason = "within_ttl"
            self._build_completed_at = _utc_now()
            self._last_refresh_completed_at = self._build_completed_at
            self._last_refresh_failure_category = None
            METRICS.set_dependency_index_state(
                source_count=len(scan.coverage),
                edge_count=len(findings),
                unresolved_count=len(scan.dynamic_references),
                built_at=replacement.built_at,
            )
            return replacement
        except asyncio.CancelledError:
            self._last_build_failure_category = "cancelled"
            self._last_refresh_failure_category = "cancelled"
            self._build_completed_at = _utc_now()
            raise
        except Exception as exc:
            METRICS.record_dependency_index_failure()
            category = _failure_category(exc)
            self._last_build_failure_category = category
            self._last_refresh_failure_category = category
            self._build_completed_at = _utc_now()
            raise
        finally:
            if mode == "background_refresh":
                self._last_refresh_completed_at = _utc_now()
            self._build_mode = None

    async def shutdown(self) -> None:
        task = self._build_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def invalidate(self, reason: str = "configuration_changed") -> None:
        self.invalidated = True
        self._invalidation_reason = reason
        METRICS.record_dependency_invalidation()

    def active_identity(self) -> dict[str, object]:
        """Return committed identity without building or refreshing it."""

        age = self._age()
        usable = self._is_usable(age)
        current = self._is_current(age)
        return {
            "generation": self.snapshot.generation if self.snapshot else 0,
            "fingerprint": self.snapshot.fingerprint if self.snapshot else None,
            # Compatibility: cursor validity means usable within the hard bound.
            "valid": usable,
            "current": current,
            "cursor_usable": usable,
            "invalidated": self.invalidated,
            "build_state": self._build_state(age),
            "validity_reason": self._validity_reason(age),
            "freshness": self._freshness(age),
            "evidence_stale": bool(usable and not current),
            "evidence_age_seconds": round(age, 3) if age is not None else None,
        }

    def _build_state(self, age: float | None) -> str:
        building = self._build_task is not None and not self._build_task.done()
        if building:
            if self._build_mode == "background_refresh" and self._is_usable(age):
                return "stale_refreshing"
            return "building"
        if self.snapshot is None:
            return "failed_without_index" if self._last_build_failure_category else "unbuilt"
        if self.invalidated:
            return "invalidated"
        if age is not None and age >= self.hard_ttl_seconds:
            return "hard_expired"
        if age is not None and age >= self.soft_ttl_seconds:
            if self._last_refresh_failure_category:
                return "refresh_failed_stale_available"
            return "stale_available"
        return "valid"

    def _freshness(self, age: float | None) -> str:
        if self.snapshot is None:
            return "unavailable"
        if self.invalidated:
            return "invalidated"
        if age is not None and age >= self.hard_ttl_seconds:
            return "hard_expired"
        if age is not None and age >= self.soft_ttl_seconds:
            return "stale_within_hard_ttl"
        return "current"

    def _validity_reason(self, age: float | None) -> str:
        state = self._build_state(age)
        if state == "valid":
            return "within_ttl"
        if state in {"stale_refreshing", "stale_available", "refresh_failed_stale_available", "hard_expired"}:
            return "age_expired"
        if state == "failed_without_index":
            return "build_failed"
        if state == "unbuilt":
            return "process_restart"
        return self._invalidation_reason

    @staticmethod
    def _expiry(built_at: str | None, seconds: float) -> str | None:
        if not built_at:
            return None
        return (datetime.fromisoformat(built_at) + timedelta(seconds=seconds)).isoformat()

    def evidence_metadata(self, snapshot: DependencyIndexSnapshot) -> dict[str, Any]:
        age = self._age(snapshot)
        current = bool(
            snapshot is self.snapshot and self._is_current(age)
        )
        usable = bool(
            snapshot is self.snapshot and self._is_usable(age)
        )
        return {
            "build_state": self._build_state(age),
            "validity_reason": self._validity_reason(age),
            "freshness": self._freshness(age),
            "soft_ttl_seconds": self.soft_ttl_seconds,
            "hard_ttl_seconds": self.hard_ttl_seconds,
            "soft_expires_at": self._expiry(snapshot.built_at, self.soft_ttl_seconds),
            "hard_expires_at": self._expiry(snapshot.built_at, self.hard_ttl_seconds),
            "background_refresh_active": bool(
                self._build_task is not None
                and not self._build_task.done()
                and self._build_mode == "background_refresh"
            ),
            "background_refresh_started_at": self._background_refresh_started_at,
            "last_refresh_completed_at": self._last_refresh_completed_at,
            "last_refresh_failure_category": self._last_refresh_failure_category,
            "serving_previous_generation": bool(usable and not current),
            "evidence_stale": bool(usable and not current),
            "evidence_age_seconds": round(age, 3) if age is not None else None,
            "maximum_evidence_age_seconds": self.hard_ttl_seconds,
        }

    def health(self) -> dict[str, Any]:
        age = self._age()
        state = self._build_state(age)
        built_at = self.snapshot.built_at if self.snapshot else None
        return {
            "configured": True,
            "build_state": state,
            "validity_reason": self._validity_reason(age),
            "freshness": self._freshness(age),
            "ttl_seconds": self.soft_ttl_seconds,
            "soft_ttl_seconds": self.soft_ttl_seconds,
            "hard_ttl_seconds": self.hard_ttl_seconds,
            "generation": self.snapshot.generation if self.snapshot else 0,
            "fingerprint": self.snapshot.fingerprint[:12] if self.snapshot else None,
            "built_at": built_at,
            "expires_at": self._expiry(built_at, self.soft_ttl_seconds),
            "soft_expires_at": self._expiry(built_at, self.soft_ttl_seconds),
            "hard_expires_at": self._expiry(built_at, self.hard_ttl_seconds),
            "age_seconds": round(age, 3) if age is not None else None,
            "valid": self._is_usable(age),
            "invalidated": self.invalidated,
            "build_started_at": self._build_started_at,
            "build_completed_at": self._build_completed_at,
            "build_duration_ms": round(self.snapshot.build_duration_ms, 3) if self.snapshot else None,
            "last_build_failure_category": self._last_build_failure_category,
            "build_progress": (
                {"phase": "network_inventory", "shared_callers": True}
                if state in {"building", "stale_refreshing"}
                else None
            ),
            "last_build_profile": dict(self.snapshot.build_profile) if self.snapshot else None,
            "background_refresh_active": bool(
                self._build_task is not None
                and not self._build_task.done()
                and self._build_mode == "background_refresh"
            ),
            "background_refresh_started_at": self._background_refresh_started_at,
            "last_refresh_completed_at": self._last_refresh_completed_at,
            "last_refresh_failure_category": self._last_refresh_failure_category,
            "serving_previous_generation": state in {
                "stale_refreshing", "stale_available", "refresh_failed_stale_available"
            },
            "evidence_stale": self._freshness(age) == "stale_within_hard_ttl",
            "evidence_age_seconds": round(age, 3) if age is not None else None,
            "maximum_evidence_age_seconds": self.hard_ttl_seconds,
            "prewarm_state": self._prewarm_state,
            "prewarm_started_at": self._prewarm_started_at,
            "prewarm_completed_at": self._prewarm_completed_at,
            "prewarm_failure_category": self._prewarm_failure_category,
            "prewarm_attempt_count": self._prewarm_attempt_count,
            "next_prewarm_retry_at": self._next_prewarm_retry_at,
        }
