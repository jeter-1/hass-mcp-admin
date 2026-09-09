"""Production Core authority lifecycle and route-lease integration boundary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable

from .coordinator import CoreReadmissionCoordinator
from .models import (
    MAX_REPORT_BYTES,
    CoreAuthoritySelection,
    CoreDispatchCommit,
    CoreDisposition,
    CoreObservation,
    CoreRouteLease,
    canonical_json,
    fingerprint,
)
from .observation import CoreObservationCollector, CoreSnapshotSource
from .profiles import CORE_CAPABILITY_PROFILES, compiled_exact_authority
from .routes import delegated_provider_compatibility, f3_requirements


CORE_RECONCILIATION_INTERVAL_SECONDS = 300.0
CORE_MONITOR_ATTACHMENT_TIMEOUT_SECONDS = 30.0
MAX_CORE_AUDIT_EVENTS = 32


@dataclass(frozen=True)
class CoreRouteAuthority:
    capability_ids: tuple[str, ...]
    leases: tuple[CoreRouteLease, ...]
    generation: int
    observation_fingerprint: str
    target_fingerprint: str | None


class CoreRuntime:
    """Own Core-only authority without retiring ha-mcp or transport state."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._reconciliation_lock = asyncio.Lock()
        self._reprobe_event = asyncio.Event()
        self._configured = False
        self._source: CoreSnapshotSource | None = None
        self._collector: CoreObservationCollector | None = None
        self._coordinator = CoreReadmissionCoordinator(CORE_CAPABILITY_PROFILES)
        self._authority_provider: Callable[
            [str], tuple[CoreAuthoritySelection, ...]
        ] = compiled_exact_authority
        self._observation: CoreObservation | None = None
        self._connection_epoch = 0
        self._connection_monitor_task: asyncio.Task[None] | None = None
        self._connection_monitor_token: object | None = None
        self._connection_monitor_version: str | None = None
        self._connection_monitor_epoch: int | None = None
        self._initialized = False
        self._last_material_change_at: datetime | None = None
        self._listeners: list[Callable[[], None]] = []
        self._audit_sink: Callable[[dict[str, Any]], Any] | None = None
        self._events: tuple[dict[str, Any], ...] = ()
        self._counters = {
            "verification_attempts": 0,
            "verification_successes": 0,
            "verification_failures": 0,
            "retirements": 0,
            "reprobes": 0,
            "lease_attempts": 0,
            "lease_failures": 0,
            "lease_commits": 0,
            "stale_plan_rejections": 0,
            "catalog_withdrawals": 0,
            "catalog_restorations": 0,
            "audit_write_failures": 0,
        }

    def configure(
        self,
        settings: Any,
        *,
        source: CoreSnapshotSource | None = None,
        authority_provider: Callable[
            [str], tuple[CoreAuthoritySelection, ...]
        ] = compiled_exact_authority,
        audit_sink: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        """Configure one runtime; no probe or provider dispatch occurs here."""

        if source is None:
            from .source import AiohttpCoreSnapshotSource

            source = AiohttpCoreSnapshotSource(settings)
        with self._lock:
            # ``CORE_READMISSION`` is process-global, while test and embedding
            # callers may configure and serve it from successive event loops.
            # Recreate loop-bound coordination primitives at the same startup
            # boundary instead of carrying a waiter from a retired loop.
            self._reconciliation_lock = asyncio.Lock()
            self._reprobe_event = asyncio.Event()
            self._source = source
            self._collector = CoreObservationCollector(self._source)
            self._authority_provider = authority_provider
            self._audit_sink = audit_sink
            self._coordinator = CoreReadmissionCoordinator(CORE_CAPABILITY_PROFILES)
            self._observation = None
            self._connection_epoch = 0
            self._connection_monitor_task = None
            self._connection_monitor_token = None
            self._connection_monitor_version = None
            self._connection_monitor_epoch = None
            self._initialized = False
            self._last_material_change_at = None
            self._listeners = []
            self._events = ()
            for key in self._counters:
                self._counters[key] = 0
            self._configured = True

    @property
    def configured(self) -> bool:
        with self._lock:
            return self._configured

    @property
    def initialized(self) -> bool:
        with self._lock:
            return self._initialized

    @property
    def current_observation(self) -> CoreObservation | None:
        with self._lock:
            return self._observation

    def register_reconciliation_listener(self, listener: Callable[[], None]) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def request_reconciliation(self, *, connection_changed: bool = False) -> None:
        source = self._source
        listeners: tuple[Callable[[], None], ...] = ()
        monitor_to_cancel: asyncio.Task[None] | None = None
        if connection_changed:
            with self._lock:
                had_observation = self._observation is not None or self._initialized
                self._connection_epoch += 1
                monitor_to_cancel = self._connection_monitor_task
                self._connection_monitor_task = None
                self._connection_monitor_token = None
                self._connection_monitor_version = None
                self._connection_monitor_epoch = None
                retired_generation = (
                    self._coordinator.retire_current_generation()
                )
                self._observation = None
                self._initialized = False
                self._last_material_change_at = datetime.now(timezone.utc)
                if retired_generation is not None:
                    self._counters["retirements"] += 1
                self._append_event_locked(
                    "core_connection_changed",
                    "core_connection_changed",
                    retired_generation,
                )
                # Once authority is absent, repeated failed probes must not
                # invalidate the same dependency evidence again. Real published
                # transitions still notify every listener on retirement.
                listeners = tuple(self._listeners) if had_observation else ()
            if monitor_to_cancel is not None and not monitor_to_cancel.done():
                try:
                    current_task = asyncio.current_task()
                except RuntimeError:
                    current_task = None
                if monitor_to_cancel is not current_task:
                    monitor_to_cancel.cancel()
            marker = getattr(source, "mark_connection_changed", None)
            if callable(marker):
                try:
                    marker()
                except Exception:
                    # Source lifecycle bookkeeping cannot preserve authority
                    # after the connection itself has moved.
                    pass
        self._reprobe_event.set()
        for listener in listeners:
            listener()

    async def reconcile_once(self, trigger: str = "periodic") -> dict[str, Any]:
        async with self._reconciliation_lock:
            collector = self._collector
            if collector is None:
                raise RuntimeError("Core readmission is not configured")
            with self._lock:
                prior_observation = self._observation
                connection_epoch = self._connection_epoch
                self._counters["verification_attempts"] += 1
                if trigger != "startup":
                    self._counters["reprobes"] += 1
            observation = await collector.collect()
            monitor_required, monitor_ready, newly_attached = (
                await self._ensure_connection_monitor(observation)
            )
            if monitor_required and not monitor_ready:
                # A failed or incomplete observation cannot leave the prior
                # monitored generation usable.  Retire it just as an observed
                # socket movement would, then retry from a fresh source epoch.
                self.request_reconciliation(connection_changed=True)
                with self._lock:
                    self._counters["verification_failures"] += 1
                    self._append_event_locked(
                        "core_reconciliation",
                        "core_lifecycle_monitor_unavailable",
                        None,
                    )
                return self.health_snapshot()
            if newly_attached:
                # The first observation supplied only the expected version for
                # the watcher.  Recollect the complete two-snapshot evidence
                # under that authenticated lifecycle fence before publishing.
                observation = await collector.collect()
            authority = (
                self._authority_provider(observation.version)
                if observation.version is not None
                else ()
            )
            now = datetime.now(timezone.utc)
            with self._lock:
                if connection_epoch != self._connection_epoch:
                    self._counters["verification_failures"] += 1
                    self._append_event_locked(
                        "core_reconciliation",
                        "core_connection_generation_stale",
                        None,
                    )
                    return self.health_snapshot()
                if monitor_required and (
                    self._connection_monitor_version != observation.version
                    or self._connection_monitor_epoch != self._connection_epoch
                ):
                    self._counters["verification_failures"] += 1
                    self._append_event_locked(
                        "core_reconciliation",
                        "core_lifecycle_monitor_stale",
                        None,
                    )
                    return self.health_snapshot()
                # Publish the decision generation and its exact observation
                # under one runtime lock.  Routes and health must never see a
                # new generation paired with the prior observation.
                result = self._coordinator.reconcile(observation, authority)
                changed = bool(result.published and not result.idempotent)
                self._initialized = True
                if result.published:
                    self._observation = observation
                if result.published and any(
                    item.disposition.admitted
                    for item in result.generation.decisions
                ):
                    self._counters["verification_successes"] += 1
                else:
                    self._counters["verification_failures"] += 1
                # Any new authority generation invalidates plans created under
                # the preceding evidence, including same-version revocation or
                # capability-profile changes.  A mere periodic replay remains
                # idempotent and does not churn this cutoff.
                if changed:
                    self._last_material_change_at = now
                    if prior_observation is not None:
                        self._counters["retirements"] += 1
                self._append_event_locked(
                    "core_reconciliation",
                    result.reason_code,
                    result.generation.generation if result.generation else None,
                )
                listeners = tuple(self._listeners) if changed else ()
                audit_sink = self._audit_sink
                audit_event = (
                    self._audit_event_locked(
                        trigger=trigger,
                        observation=observation,
                        generation=result.generation,
                    )
                    if changed
                    or not any(
                        item.disposition.admitted
                        for item in result.generation.decisions
                    )
                    else None
                )
            for listener in listeners:
                listener()
            if audit_sink is not None and audit_event is not None:
                try:
                    if audit_sink(audit_event) is False:
                        with self._lock:
                            self._counters["audit_write_failures"] += 1
                except Exception:
                    with self._lock:
                        self._counters["audit_write_failures"] += 1
            return self.health_snapshot()

    async def reconcile_until_initialized(
        self,
        *,
        retry_delays: tuple[float, ...] = (0.25, 1.0, 3.0),
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> dict[str, Any]:
        snapshot = await self.reconcile_once("startup")
        for delay in retry_delays:
            if snapshot.get("identity_agreement"):
                break
            await sleep(delay)
            snapshot = await self.reconcile_once("startup_retry")
        return snapshot

    async def supervise(
        self,
        *,
        interval_seconds: float = CORE_RECONCILIATION_INTERVAL_SECONDS,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("Core reconciliation interval must be positive")
        retry = False
        try:
            while True:
                if retry:
                    # Failure/monitor retirement can itself set the wakeup.
                    # Coalesce those hints during one existing reconciliation
                    # interval instead of creating an immediate retry chain.
                    # External changes still retire authority synchronously;
                    # after recovery they resume waking the supervisor at once.
                    await sleep(interval_seconds)
                    trigger = "failure_retry"
                else:
                    try:
                        await asyncio.wait_for(
                            self._reprobe_event.wait(), timeout=interval_seconds
                        )
                        trigger = "identity_or_connection_change"
                    except TimeoutError:
                        trigger = "periodic"
                self._reprobe_event.clear()
                snapshot = await self.reconcile_once(trigger)
                retry = not (
                    snapshot["initialized"] and snapshot["identity_agreement"]
                )
        finally:
            with self._lock:
                monitor_task = self._connection_monitor_task
                self._connection_monitor_task = None
                self._connection_monitor_token = None
                self._connection_monitor_version = None
                self._connection_monitor_epoch = None
            if monitor_task is not None:
                monitor_task.cancel()
                await asyncio.gather(monitor_task, return_exceptions=True)

    async def _ensure_connection_monitor(
        self,
        observation: CoreObservation,
    ) -> tuple[bool, bool, bool]:
        """Require one authenticated watcher before positive publication."""

        source = self._source
        monitor = getattr(source, "wait_for_connection_change", None)
        if not callable(monitor):
            return False, True, False
        if (
            not observation.connected
            or not observation.authenticated
            or not observation.stable
            or observation.version is None
        ):
            return True, False, False
        with self._lock:
            epoch = self._connection_epoch
            existing = self._connection_monitor_task
            if (
                existing is not None
                and not existing.done()
                and self._connection_monitor_version == observation.version
                and self._connection_monitor_epoch == epoch
            ):
                return True, True, False
            token = object()
            ready = asyncio.Event()
            self._connection_monitor_token = token
            self._connection_monitor_version = None
            self._connection_monitor_epoch = None
            task = asyncio.create_task(
                self._supervise_connection_changes(
                    monitor,
                    expected_version=observation.version,
                    expected_epoch=epoch,
                    token=token,
                    ready=ready,
                ),
                name="home-assistant-core-connection-monitor",
            )
            self._connection_monitor_task = task
        if existing is not None and not existing.done():
            existing.cancel()
            await asyncio.gather(existing, return_exceptions=True)

        ready_wait = asyncio.create_task(ready.wait())
        done, _pending = await asyncio.wait(
            {task, ready_wait},
            timeout=CORE_MONITOR_ATTACHMENT_TIMEOUT_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        attached = ready_wait in done and ready_wait.result()
        if not ready_wait.done():
            ready_wait.cancel()
            await asyncio.gather(ready_wait, return_exceptions=True)
        if not attached:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            return True, False, False
        with self._lock:
            current = (
                self._connection_monitor_task is task
                and self._connection_monitor_token is token
                and self._connection_monitor_version == observation.version
                and self._connection_monitor_epoch == epoch
                and epoch == self._connection_epoch
            )
        return True, current, current

    async def _supervise_connection_changes(
        self,
        monitor: Callable[[str, Callable[[], None]], Any],
        *,
        expected_version: str,
        expected_epoch: int,
        token: object,
        ready: asyncio.Event,
    ) -> None:
        """Bind one exact lifecycle socket and retire it as soon as it moves."""

        attached = False

        def on_attached() -> None:
            nonlocal attached
            with self._lock:
                if (
                    self._connection_monitor_token is not token
                    or self._connection_epoch != expected_epoch
                ):
                    return
                attached = True
                self._connection_monitor_version = expected_version
                self._connection_monitor_epoch = expected_epoch
                ready.set()

        try:
            await monitor(expected_version, on_attached)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Exception text is not authority or public evidence. Failure to
            # establish or retain the watcher is a lifecycle movement.
            pass
        finally:
            if not attached:
                ready.set()
        if attached:
            self.request_reconciliation(connection_changed=True)

    def route_status(
        self,
        capability_ids: tuple[str, ...],
        *,
        delegated_tool: str | None = None,
        delegated_adapter_version: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            generation = self._coordinator.current_generation
            observation = self._observation
            decisions = []
            for capability_id in capability_ids:
                decision = (
                    generation.decision_for(capability_id)
                    if generation is not None
                    else None
                )
                decisions.append(decision)
            available = bool(capability_ids) and all(
                item is not None and item.disposition.admitted
                for item in decisions
            )
            provider_compatible, provider_reason = delegated_provider_compatibility(
                tool_name=delegated_tool or "",
                core_version=observation.version if observation is not None else None,
                adapter_version=delegated_adapter_version,
            )
            available = available and provider_compatible
            disposition = (
                "admitted"
                if available
                else "quarantined"
                if not provider_compatible
                else next(
                    (
                        item.disposition.value
                        for item in decisions
                        if item is not None and not item.disposition.admitted
                    ),
                    CoreDisposition.UNAVAILABLE.value,
                )
            )
            reasons = tuple(
                sorted(
                    {
                        item.reason_code
                        for item in decisions
                        if item is not None and not item.disposition.admitted
                    }
                    | ({provider_reason} if provider_reason is not None else set())
                )
            )
            return {
                "available": available,
                "disposition": disposition,
                "reason_codes": reasons,
                "generation": generation.generation if generation else None,
            }

    def delegated_tool_available(
        self,
        tool_name: str,
        requirements: tuple[str, ...],
        *,
        adapter_version: str | None = None,
    ) -> bool:
        return bool(
            self.route_status(
                requirements,
                delegated_tool=tool_name,
                delegated_adapter_version=adapter_version,
            )["available"]
        )

    @staticmethod
    def target_fingerprint(target: Any) -> str:
        if isinstance(target, dict):
            material = target
        else:
            material = {
                "target_type": str(getattr(target, "target_type", "")),
                "target_id": str(getattr(target, "target_id", "")),
            }
        return fingerprint({"model": "core-route-target-v1", "target": material})

    def acquire(
        self,
        capability_ids: tuple[str, ...],
        *,
        target: Any | None = None,
        plan_created_at: str | None = None,
        delegated_tool: str | None = None,
        delegated_adapter_version: str | None = None,
    ) -> CoreRouteAuthority | None:
        with self._lock:
            self._counters["lease_attempts"] += 1
            observation = self._observation
            generation = self._coordinator.current_generation
            if observation is None or generation is None:
                self._counters["lease_failures"] += 1
                return None
            provider_compatible, _provider_reason = delegated_provider_compatibility(
                tool_name=delegated_tool or "",
                core_version=observation.version,
                adapter_version=delegated_adapter_version,
            )
            if not provider_compatible:
                self._counters["lease_failures"] += 1
                return None
            if not self._plan_is_current_locked(plan_created_at):
                self._counters["stale_plan_rejections"] += 1
                self._counters["lease_failures"] += 1
                return None
            target_fingerprint = (
                self.target_fingerprint(target) if target is not None else None
            )
            leases = self._coordinator.acquire_routes(
                capability_ids,
                session_fingerprint=observation.session_fingerprint,
                target_fingerprint=target_fingerprint,
            )
            if leases is None:
                self._counters["lease_failures"] += 1
                return None
            return CoreRouteAuthority(
                capability_ids=tuple(sorted(capability_ids)),
                leases=leases,
                generation=generation.generation,
                observation_fingerprint=observation.fingerprint,
                target_fingerprint=target_fingerprint,
            )

    def acquire_f3(
        self,
        prepared: Any,
        *,
        plan_created_at: str | None = None,
    ) -> CoreRouteAuthority | None:
        return self.acquire(
            f3_requirements(prepared),
            target=getattr(prepared, "target", None),
            plan_created_at=plan_created_at,
        )

    def consume(
        self, authority: CoreRouteAuthority
    ) -> tuple[CoreDispatchCommit, ...] | None:
        with self._lock:
            observation = self._observation
            if observation is None:
                self._counters["lease_failures"] += 1
                return None
            commits = self._coordinator.commit_routes(
                authority.leases,
                observation=observation,
                target_fingerprint=authority.target_fingerprint,
            )
            if commits is None:
                self._counters["lease_failures"] += 1
                return None
            self._counters["lease_commits"] += len(commits)
            return commits

    def revalidate(
        self,
        authority: CoreRouteAuthority,
        commits: tuple[CoreDispatchCommit, ...],
    ) -> bool:
        """Require the same current Core authority before every provider call."""

        with self._lock:
            observation = self._observation
            generation = self._coordinator.current_generation
            valid = bool(
                isinstance(authority, CoreRouteAuthority)
                and observation is not None
                and generation is not None
                and authority.generation == generation.generation
                and authority.observation_fingerprint == observation.fingerprint
                and isinstance(commits, tuple)
                and bool(commits)
                and all(isinstance(item, CoreDispatchCommit) for item in commits)
                and tuple(item.lease for item in commits) == authority.leases
                and self._coordinator.validate_commits(
                    commits,
                    observation=observation,
                    target_fingerprint=authority.target_fingerprint,
                )
            )
            if not valid:
                self._counters["lease_failures"] += 1
            return valid

    def release(self, authority: CoreRouteAuthority | None) -> bool:
        return bool(
            authority is not None
            and self._coordinator.release_routes(authority.leases)
        )

    def finish(self, commits: tuple[CoreDispatchCommit, ...] | None) -> bool:
        return bool(commits and self._coordinator.finish_commits(commits))

    def record_catalog_change(self, *, restored: bool, count: int = 1) -> None:
        if count < 0:
            raise ValueError("catalog change count must not be negative")
        with self._lock:
            key = "catalog_restorations" if restored else "catalog_withdrawals"
            self._counters[key] += count

    def _plan_is_current_locked(self, created_at: str | None) -> bool:
        cutoff = self._last_material_change_at
        if cutoff is None or created_at is None:
            return True
        try:
            created = datetime.fromisoformat(created_at)
        except (TypeError, ValueError):
            return False
        if created.tzinfo is None:
            return False
        return created >= cutoff

    def _append_event_locked(
        self, event_type: str, reason_code: str, generation: int | None
    ) -> None:
        event = {
            "event_type": event_type,
            "reason_code": reason_code,
            "generation": generation,
        }
        self._events = (*self._events, event)[-MAX_CORE_AUDIT_EVENTS:]

    @staticmethod
    def _audit_event_locked(
        *,
        trigger: str,
        observation: CoreObservation,
        generation: Any,
    ) -> dict[str, Any]:
        allowed_triggers = {
            "startup",
            "startup_retry",
            "periodic",
            "identity_or_connection_change",
            "mutation_pre_dispatch",
            "mutation_verification",
        }
        decisions = generation.decisions
        admitted = sum(item.disposition.admitted for item in decisions)
        return {
            "event": "home_assistant_core_authority_reconciled",
            "result_status": "success" if admitted else "withheld",
            "analysis_summary": {
                "model": "ha-core-authority-audit-v1",
                "trigger": trigger if trigger in allowed_triggers else "other",
                "observed_core_version": observation.version,
                "identity_agreement": observation.identity_agrees,
                "generation": generation.generation,
                "disposition": generation.disposition.value,
                "admitted_count": admitted,
                "withheld_count": len(decisions) - admitted,
                "capabilities": [
                    {
                        "capability_id": item.capability_id,
                        "disposition": item.disposition.value,
                        "reason_code": item.reason_code,
                        "authority_source": (
                            item.authority_source.value
                            if item.authority_source is not None
                            else None
                        ),
                        "profile_id": item.profile_id,
                        "adapter_id": item.adapter_id,
                    }
                    for item in decisions
                ],
                "fallback_count": 0,
            },
        }

    def health_snapshot(self) -> dict[str, Any]:
        with self._lock:
            assessment = self._coordinator.update_assessment()
            generation = self._coordinator.current_generation
            observation = self._observation
            decisions = generation.decisions if generation else ()
            projection = {
                **assessment,
                "configured": self._configured,
                "initialized": self._initialized,
                "observed_core_version": observation.version if observation else None,
                "identity_agreement": bool(observation and observation.identity_agrees),
                "authority_profiles": [
                    {
                        "capability_id": item.capability_id,
                        "disposition": item.disposition.value,
                        "reason_code": item.reason_code,
                        "authority_source": (
                            item.authority_source.value
                            if item.authority_source is not None
                            else None
                        ),
                        "profile_id": item.profile_id,
                        "adapter_id": item.adapter_id,
                    }
                    for item in decisions
                ],
                "counters": dict(self._counters),
                "recent_events": [dict(item) for item in self._events],
                "fallback_count": 0,
            }
            canonical_json(projection, maximum=MAX_REPORT_BYTES)
            return projection

    def health_projection(self) -> dict[str, Any]:
        """Return the bounded Core authority summary used by public health."""

        with self._lock:
            projection = {
                **self._coordinator.health_projection(),
                "configured": self._configured,
                "initialized": self._initialized,
                "observed_core_version": (
                    self._observation.version if self._observation else None
                ),
                "identity_agreement": bool(
                    self._observation and self._observation.identity_agrees
                ),
                "counters": dict(self._counters),
                "recent_event_count": len(self._events),
                "fallback_count": 0,
            }
            canonical_json(projection, maximum=MAX_REPORT_BYTES)
            return projection


CORE_READMISSION = CoreRuntime()


__all__ = [
    "CORE_READMISSION",
    "CORE_RECONCILIATION_INTERVAL_SECONDS",
    "CoreRouteAuthority",
    "CoreRuntime",
]
