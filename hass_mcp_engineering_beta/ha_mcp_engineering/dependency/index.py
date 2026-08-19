"""Bounded, single-flight dependency index with explicit evidence freshness."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
from typing import Any

from ..observability import METRICS
from .models import (
    DependencyObligation,
    DependencyIndexSnapshot,
    DynamicReference,
    dynamic_reference_fingerprint,
    obligation_fingerprint,
    snapshot_fingerprint,
)
from .extraction import make_coverage_failure_obligation
from .provider import DependencySourceProvider


DEFAULT_SOFT_TTL_SECONDS = 600.0
DEFAULT_HARD_TTL_SECONDS = 3600.0
MAX_AUTOMATION_ACTION_PROFILES = 1_000
MAX_AUTOMATION_READ_FAILURES = 1_000
MAX_DYNAMIC_REFERENCES = 1_000
MAX_DEPENDENCY_OBLIGATIONS = 10_000
# A fenced refresh normally needs one rebuild.  The bound keeps a pathological
# invalidation storm from looping instead of failing closed.
MAX_FENCED_BUILD_ATTEMPTS = 8


class DependencyFenceError(RuntimeError):
    """A governed post-lock refresh could not be satisfied after the fence."""

    category = "dependency_fence_unsatisfied"


def _dynamic_reference_sort_key(
    item: DynamicReference,
) -> tuple[str, str, str, str, str, str]:
    return (
        item.source_type,
        item.source_entity_id or "",
        item.source_id,
        item.config_path,
        item.evidence_id,
        dynamic_reference_fingerprint(item),
    )


def _dynamic_reference_overflow_fingerprint(
    items: list[DynamicReference],
) -> str | None:
    if not items:
        return None
    encoded = json.dumps(
        [dynamic_reference_fingerprint(item) for item in items],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _obligation_sort_key(
    item: DependencyObligation,
) -> tuple[str, str, str, str, str, str]:
    return (
        item.source_type,
        item.source_entity_id or "",
        item.source_id,
        item.config_path,
        item.evidence_id,
        obligation_fingerprint(item),
    )


def _obligation_overflow_fingerprint(
    items: list[DependencyObligation],
) -> str | None:
    if not items:
        return None
    encoded = json.dumps(
        [obligation_fingerprint(item) for item in items],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
        # Monotonic source-read epoch.  Both configuration invalidation and a
        # governed lock fence open a new epoch, so "read after this point" is
        # expressible without depending on wall-clock time or on task identity.
        self._source_epoch = 0
        # Epoch opened by the most recent invalidation.  A build may clear the
        # invalidated flag only when its own source read began at or after it,
        # so an in-flight build cannot erase a later invalidation.
        self._invalidation_epoch = 0
        self._last_fence_reason: str | None = None
        # Epoch at which the in-flight build's provider scan began.  ``None``
        # means the build exists but has not read any source yet, so its read
        # is still guaranteed to begin at or after the current epoch.
        self._build_scan_epoch: int | None = None
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

    def open_source_fence(self, reason: str = "governed_lock_fence") -> int:
        """Open a new source-read epoch and return its fence token.

        A governed caller opens the fence while it holds the complete lock
        set.  Evidence produced by a scan that started before the fence
        describes a pre-lock world and must not satisfy the post-lock
        refresh, even when that scan is still running.
        """

        self._source_epoch += 1
        self._last_fence_reason = reason
        return self._source_epoch

    def _build_can_satisfy(self, fence: int) -> bool:
        """Return whether the in-flight build's read began after ``fence``."""

        return self._build_scan_epoch is None or self._build_scan_epoch >= fence

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

    async def get(
        self,
        *,
        refresh: bool = False,
        min_source_epoch: int | None = None,
    ) -> tuple[DependencyIndexSnapshot, bool, float]:
        """Return current/stale-usable evidence or await one mandatory build.

        Soft-expired evidence is returned immediately while a manager-owned refresh
        runs. Awaiters are shielded so cancelling one caller cannot cancel the shared
        build.

        ``min_source_epoch`` carries a fence token from
        :meth:`open_source_fence`.  It forces a refresh and accepts only a
        snapshot whose provider scan began at or after that fence, so a build
        already in flight when the lock was taken can never satisfy the
        governed post-lock refresh.
        """

        lookup_started = time.perf_counter()
        if min_source_epoch is not None:
            refresh = True
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
        if min_source_epoch is not None:
            reason = "fenced_refresh"
        for _ in range(MAX_FENCED_BUILD_ATTEMPTS):
            task = self._build_task
            if (
                min_source_epoch is not None
                and task is not None
                and not task.done()
                and not self._build_can_satisfy(min_source_epoch)
            ):
                # This build read source before the fence.  Let it finish for
                # every other awaiter, adopt nothing from it, then rebuild.
                await asyncio.shield(
                    asyncio.gather(task, return_exceptions=True)
                )
                continue
            task = self._ensure_build(
                mode=(
                    "foreground_refresh"
                    if self.snapshot is not None
                    else "initial"
                ),
                reason=reason,
            )
            snapshot = await asyncio.shield(task)
            if (
                min_source_epoch is None
                or snapshot.source_epoch >= min_source_epoch
            ):
                return (
                    snapshot,
                    True,
                    (time.perf_counter() - lookup_started) * 1000,
                )
        raise DependencyFenceError(
            "fenced dependency refresh did not observe a post-fence scan"
        )

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
        # The new build has not read any source yet, so its read is guaranteed
        # to begin at or after the epoch in force right now.
        self._build_scan_epoch = None
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
            # The epoch is captured immediately before the source read, so a
            # fence opened after this point is provably not covered by it.
            source_epoch = self._source_epoch
            self._build_scan_epoch = source_epoch
            scan = await self.provider.scan()
            next_generation = self.generation + 1
            findings = sorted(scan.findings, key=lambda item: item.evidence_id)[: self.max_edges]
            findings_truncated = len(scan.findings) > self.max_edges
            profiles = sorted(
                scan.automation_action_profiles,
                key=lambda item: (
                    item.source_entity_id or "",
                    item.source_id,
                ),
            )[:MAX_AUTOMATION_ACTION_PROFILES]
            profiles_truncated = (
                len(scan.automation_action_profiles)
                > MAX_AUTOMATION_ACTION_PROFILES
            )
            read_failures = sorted(
                scan.automation_read_failures,
                key=lambda item: (
                    item.source_entity_id or "",
                    item.source_id,
                    item.reason_code,
                ),
            )[:MAX_AUTOMATION_READ_FAILURES]
            read_failures_truncated = (
                len(scan.automation_read_failures)
                > MAX_AUTOMATION_READ_FAILURES
            )
            ordered_dynamic_references = sorted(
                scan.dynamic_references,
                key=_dynamic_reference_sort_key,
            )
            dynamic_references = ordered_dynamic_references[
                :MAX_DYNAMIC_REFERENCES
            ]
            dynamic_reference_overflow = ordered_dynamic_references[
                MAX_DYNAMIC_REFERENCES:
            ]
            dynamic_references_truncated = bool(
                dynamic_reference_overflow
            )
            dynamic_reference_overflow_count = len(
                dynamic_reference_overflow
            )
            dynamic_reference_overflow_fingerprint = (
                _dynamic_reference_overflow_fingerprint(
                    dynamic_reference_overflow
                )
            )
            ordered_obligations = sorted(
                scan.obligations,
                key=_obligation_sort_key,
            )
            obligation_overflow: list[DependencyObligation] = []
            if len(ordered_obligations) > MAX_DEPENDENCY_OBLIGATIONS:
                # Reserve one retained terminal that explicitly prevents an
                # overflow from looking like complete absence.
                obligation_overflow = ordered_obligations[
                    MAX_DEPENDENCY_OBLIGATIONS - 1:
                ]
                obligations = ordered_obligations[
                    :MAX_DEPENDENCY_OBLIGATIONS - 1
                ]
            else:
                obligations = ordered_obligations
            obligations_truncated = bool(obligation_overflow)
            obligation_overflow_count = len(obligation_overflow)
            obligation_overflow_fingerprint = (
                _obligation_overflow_fingerprint(obligation_overflow)
            )
            if obligations_truncated:
                obligations.append(
                    make_coverage_failure_obligation(
                        source_type="automation",
                        source_id="dependency_index",
                        source_entity_id=None,
                        config_path="$",
                        relation="other_structured_reference",
                        reason_code="dependency_obligation_index_overflow",
                        configuration_fingerprint=(
                            obligation_overflow_fingerprint
                        ),
                        limit_exceeded=True,
                    )
                )
            coverage = list(scan.coverage)
            if (
                findings_truncated
                or profiles_truncated
                or read_failures_truncated
                or dynamic_references_truncated
                or obligations_truncated
            ):
                METRICS.record_dependency_truncation()
                coverage = [
                    replace(
                        item,
                        completeness=(
                            "partial"
                            if item.source_type == "automation"
                            else item.completeness
                        ),
                        warnings=(
                            [
                                *item.warnings,
                                "Automation dependency evidence exceeded the bounded index payload.",
                            ]
                            if item.source_type == "automation"
                            else list(item.warnings)
                        ),
                    )
                    for item in coverage
                ]
            fingerprint = snapshot_fingerprint(
                findings,
                coverage,
                next_generation,
                profiles,
                read_failures,
                dynamic_references=dynamic_references,
                dynamic_reference_overflow_count=(
                    dynamic_reference_overflow_count
                ),
                dynamic_reference_overflow_fingerprint=(
                    dynamic_reference_overflow_fingerprint
                ),
                label_membership_fingerprints=(
                    scan.label_membership_fingerprints
                ),
                label_membership_truncated=(
                    scan.label_membership_truncated
                ),
                label_registry_complete=(
                    scan.label_registry_complete
                ),
                obligations=obligations,
                obligation_overflow_count=obligation_overflow_count,
                obligation_overflow_fingerprint=(
                    obligation_overflow_fingerprint
                ),
                obligation_ledger_model=scan.obligation_ledger_model,
                home_assistant_version=scan.home_assistant_version,
                home_assistant_version_status=(
                    scan.home_assistant_version_status
                ),
            )
            build_duration_ms = (time.perf_counter() - build_started) * 1000
            replacement = DependencyIndexSnapshot(
                fingerprint=fingerprint,
                generation=next_generation,
                built_at_monotonic=time.monotonic(),
                built_at=_utc_now(),
                findings=tuple(findings),
                dynamic_references=tuple(dynamic_references),
                target_metadata=scan.target_metadata,
                coverage=tuple(coverage),
                build_duration_ms=build_duration_ms,
                build_profile=dict(scan.profile),
                automation_action_profiles=tuple(profiles),
                automation_read_failures=tuple(read_failures),
                dynamic_reference_overflow_count=(
                    dynamic_reference_overflow_count
                ),
                dynamic_reference_overflow_fingerprint=(
                    dynamic_reference_overflow_fingerprint
                ),
                label_memberships=dict(scan.label_memberships),
                label_membership_fingerprints=dict(
                    scan.label_membership_fingerprints
                ),
                label_membership_truncated=tuple(
                    scan.label_membership_truncated
                ),
                label_registry_complete=bool(
                    scan.label_registry_complete
                ),
                obligations=tuple(obligations),
                obligation_overflow_count=(
                    obligation_overflow_count
                ),
                obligation_overflow_fingerprint=(
                    obligation_overflow_fingerprint
                ),
                obligation_ledger_model=scan.obligation_ledger_model,
                home_assistant_version=scan.home_assistant_version,
                home_assistant_version_status=(
                    scan.home_assistant_version_status
                ),
                source_epoch=source_epoch,
            )
            # Publish the complete replacement atomically after every build step.
            self.snapshot = replacement
            self.generation = next_generation
            # An invalidation raised after this build began reading describes
            # configuration this build never saw, so completing must not clear
            # it.  Only a read that began at or after the invalidation may.
            if self._invalidation_epoch <= source_epoch:
                self.invalidated = False
                self._invalidation_reason = "within_ttl"
            self._build_completed_at = _utc_now()
            self._last_refresh_completed_at = self._build_completed_at
            self._last_refresh_failure_category = None
            METRICS.set_dependency_index_state(
                source_count=len(coverage),
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
            self._build_scan_epoch = None

    async def shutdown(self) -> None:
        task = self._build_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def invalidate(self, reason: str = "configuration_changed") -> None:
        self.invalidated = True
        self._invalidation_reason = reason
        self._source_epoch += 1
        self._invalidation_epoch = self._source_epoch
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
