"""Production Core authority lifecycle and route-lease integration boundary."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from ..logging_config import get_logger, log_event
from .observation import (
    PROBE_FAILURE_DETAILS,
    CoreObservationCollector,
    CoreSnapshotSource,
)
from .profiles import CORE_RUNTIME_CAPABILITY_PROFILES, compiled_exact_authority
from .routes import delegated_provider_compatibility, f3_requirements
from .registry import CoreReleaseRegistry
from .probe_profiles import compiled_probe_profile
from .models import CORE_IDENTITY


CORE_RECONCILIATION_INTERVAL_SECONDS = 300.0
CORE_MONITOR_ATTACHMENT_TIMEOUT_SECONDS = 30.0
MAX_CORE_AUDIT_EVENTS = 32
# HAMCP-215 recovery pacing. A failed verification is retried after a bounded,
# jittered exponential backoff instead of one fixed reconciliation interval.
CORE_RECOVERY_RETRY_BASE_SECONDS = 20.0
CORE_RECOVERY_RETRY_CAP_SECONDS = 240.0
CORE_RECOVERY_RETRY_JITTER = 0.2
CORE_RECOVERY_MAX_BACKOFF_LEVEL = 8
# Hysteresis: one restoration gate covers every non-admitted state (transient
# retirement, incompatible, unavailable, never initialized after boot, changed
# version or registry authority). Authority is published again only after
# consecutive complete background verifications agreeing on identity, version,
# selected signed-registry authority and reviewed contracts, spaced by the
# confirmation delay. Any observation of a different key, background or on
# demand, restarts the count. A retirement soon
# after a restoration is a flap and needs one more, slower.
CORE_RECOVERY_CONFIRMATIONS = 2
CORE_RECOVERY_CONFIRMATION_SECONDS = 15.0
CORE_RECOVERY_FLAP_WINDOW_SECONDS = 600.0
CORE_RECOVERY_FLAP_CONFIRMATIONS = 3
CORE_RECOVERY_STATES = frozenset({
    "initializing", "stable", "retrying", "backing_off", "confirming",
    "restored", "incompatible",
})
_RECOVERY_REASONS = frozenset({
    "core_connection_changed", "core_lifecycle_monitor_unavailable",
    "core_observed_version_changed", "core_registry_authority_changed",
    "core_authority_incompatible", "core_generation_replaced",
    "core_authority_key_changed",
})
# Only supervisor-paced attempts may count toward recovery confirmation. Any
# other trigger is on demand (mutation pre-dispatch, reverification, tool and
# health callers, unknown names): while authority is not admitted it is refused
# without publishing, is never counted and cannot shorten the spacing.
_RECOVERY_CONFIRMATION_TRIGGERS = frozenset({
    "periodic", "identity_or_connection_change", "failure_retry",
})
# First admission is not restoration: only the first boot sequence of the
# runtime object (or its first background verification) may admit without the
# gate. It is a one-shot latch for the object's lifetime -- in production the
# module-level ``CORE_READMISSION``, so the process lifetime. It is spent at the
# first admission, failed verification or retirement, at the end of the first
# boot sequence, and by any attempt that is not a boot attempt. ``configure``
# never re-arms it: any later re-initialization passes the full gate.
_BOOT_TRIGGERS = frozenset({"startup", "startup_retry"})
_FIRST_ADMISSION_TRIGGERS = _BOOT_TRIGGERS | _RECOVERY_CONFIRMATION_TRIGGERS
# HAMCP-215 H1: work that outlives its configuration is discarded, never
# published. Bounded reasons and phases for the INFO discard record.
_STALE_WORK_REASONS = frozenset({
    "core_configuration_replaced", "core_runtime_generation_replaced",
    "core_boot_exemption_spent",
})
_STALE_WORK_PHASES = frozenset({
    "reconciliation_lock", "registry_refresh", "observation",
    "monitor_attachment", "publication",
})
logger = get_logger("core_authority")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None


# HAMCP-255 (closed by HAMCP-106): v1 listed six fields for every profile
# and outgrew the 8192-character audit payload bound, so the logger stored a
# truncated stub.  v2 is columnar: sorted ``disposition``, ``reason_code`` and
# ``authority_source`` vocabularies, and per capability the indexes of its
# three values.  A fourth element carries the profile id only when it differs
# from the compiled convention (``capability_id`` with dots as underscores,
# plus ``_v1``); the adapter id follows from the admitted compiled profile.
# Each profile's disposition and reason code remain reconstructable from the
# record alone, even when no two profiles share an outcome.
RECONCILED_AUDIT_MODEL = "ha-core-authority-audit-v2"


def _conventional_profile_id(capability_id: str) -> str:
    return capability_id.replace(".", "_") + "_v1"


def _audit_disposition_groups(decisions: Any) -> dict[str, Any]:
    rows = [
        (
            item.capability_id,
            item.disposition.value,
            item.reason_code,
            item.authority_source.value
            if item.authority_source is not None
            else None,
            item.profile_id,
        )
        for item in decisions
    ]

    def vocabulary(column: int) -> list[Any]:
        return sorted({row[column] for row in rows}, key=lambda value: str(value))

    dispositions, reasons, sources = vocabulary(1), vocabulary(2), vocabulary(3)
    profiles: dict[str, list[Any]] = {}
    for capability_id, disposition, reason, source, profile_id in sorted(
        rows, key=lambda row: row[0]
    ):
        entry: list[Any] = [
            dispositions.index(disposition),
            reasons.index(reason),
            sources.index(source),
        ]
        if profile_id != _conventional_profile_id(capability_id):
            entry.append(profile_id)
        profiles[capability_id] = entry
    return {
        "disposition": dispositions,
        "reason_code": reasons,
        "authority_source": sources,
        "profiles": profiles,
    }


_AUDIT_TRIGGERS = frozenset({
    "startup", "startup_retry", "periodic", "identity_or_connection_change",
    "mutation_pre_dispatch", "mutation_verification", "failure_retry",
})
_EARLY_FAILURE_PHASES = {
    "core_lifecycle_monitor_unavailable": "monitor_attachment",
    "core_registry_changed_during_probe": "registry_collection",
    "core_connection_generation_stale": "connection_fence",
    "core_lifecycle_monitor_stale": "monitor_fence",
    "core_registry_changed_during_selection": "registry_selection",
}
_OBSERVATION_REASONS = frozenset({
    "core_observation_unavailable", "malformed_core_evidence",
    "core_version_disagreement", "core_identity_changed", "core_session_changed",
    "core_observation_unstable", "transport_unavailable", "authentication_failed",
    "observation_complete",
})


@dataclass(frozen=True)
class _WorkStamp:
    """What one reconciliation work item was entitled to when it started.

    ``generation`` is the runtime generation (advanced by every configure and
    by supervisor retirement; never reused), ``owner`` the configuration that
    started the work, and ``boot_available`` whether the one-shot boot
    exemption was unspent at start for a trigger that may use it. Publication
    re-checks all three under the runtime lock; a mismatch discards the work.
    """

    generation: int
    owner: object
    boot_available: bool


@dataclass(frozen=True)
class CoreRouteAuthority:
    capability_ids: tuple[str, ...]
    leases: tuple[CoreRouteLease, ...]
    generation: int
    observation_fingerprint: str
    target_fingerprint: str | None
    core_version: str


class CoreRuntime:
    """Own Core-only authority without retiring ha-mcp or transport state."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._reconciliation_lock = asyncio.Lock()
        self._reprobe_event = asyncio.Event()
        self._configured = False
        self._source: CoreSnapshotSource | None = None
        self._collector: CoreObservationCollector | None = None
        self._coordinator = CoreReadmissionCoordinator(CORE_RUNTIME_CAPABILITY_PROFILES)
        self._authority_provider: Callable[
            [str], tuple[CoreAuthoritySelection, ...]
        ] = compiled_exact_authority
        self._observation: CoreObservation | None = None
        self._release_registry: CoreReleaseRegistry | None = None
        self._published_registry_token: str | None = None
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
            "recovery_attempts": 0,
            "recovery_confirmations": 0,
            "recovery_on_demand_refusals": 0,
            "authority_restorations": 0,
            "stale_work_discards": 0,
        }
        self._monotonic: Callable[[], float] = time.monotonic
        # One-shot boot exception (see ``_BOOT_TRIGGERS``). Assigned False only
        # here; ``_spend_boot_exception_locked`` is its only other writer.
        self._boot_exception_spent = False
        # HAMCP-215 H1 work fence (see ``_WorkStamp``). Neither value is ever
        # reset: a superseded configuration's in-flight work cannot match a
        # later one.
        self._runtime_generation = 0
        self._configuration_owner: object = object()
        self._reset_recovery_locked()

    def _reset_recovery_locked(self) -> None:
        """Clear HAMCP-215 recovery bookkeeping at one configure boundary."""

        # Whether the published generation admits any capability.
        self._admitted = False
        self._admitted_capabilities: frozenset[str] = frozenset()
        self._recovery_active = False
        self._recovery_state = "initializing"
        self._consecutive_failures = 0
        self._recovery_confirmations = 0
        self._recovery_required = CORE_RECOVERY_CONFIRMATIONS
        self._recovery_confirmation_key: tuple[Any, ...] | None = None
        self._backoff_level = 0
        self._flap_count = 0
        self._last_failure_reason: str | None = None
        self._last_failure_detail: str | None = None
        self._last_failure_at: datetime | None = None
        self._retired_at: datetime | None = None
        self._last_attempt_at: datetime | None = None
        self._next_recovery_attempt_at: datetime | None = None
        self._last_retry_delay_seconds: float | None = None
        self._last_restored_at: datetime | None = None
        self._last_restored_monotonic: float | None = None
        self._monitor_failure_detail: str | None = None

    def _spend_boot_exception_locked(self) -> None:
        """Close the one-shot first-admission exception; it never reopens."""

        self._boot_exception_spent = True

    def _work_stamp(self, trigger: str, *, owner: object | None = None) -> _WorkStamp:
        """Record what one work item may claim, at the moment it starts."""

        with self._lock:
            return _WorkStamp(
                generation=self._runtime_generation,
                owner=self._configuration_owner if owner is None else owner,
                boot_available=(
                    not self._boot_exception_spent
                    and trigger in _FIRST_ADMISSION_TRIGGERS
                ),
            )

    def _stale_work_reason_locked(self, stamp: _WorkStamp) -> str | None:
        """Why work stamped ``stamp`` no longer belongs to this runtime."""

        if stamp.owner is not self._configuration_owner:
            return "core_configuration_replaced"
        if stamp.generation != self._runtime_generation:
            return "core_runtime_generation_replaced"
        return None

    def _stale_work(
        self, stamp: _WorkStamp, trigger: str, phase: str
    ) -> dict[str, Any] | None:
        """Discard superseded work at a resume point; ``None`` while current."""

        with self._lock:
            reason = self._stale_work_reason_locked(stamp)
        if reason is None:
            return None
        return self._discard_stale_work(trigger, phase, reason)

    def _discard_stale_work(
        self, trigger: str, phase: str, reason: str
    ) -> dict[str, Any]:
        """Drop one superseded result: logged at INFO, it counts as nothing."""

        with self._lock:
            # Diagnostic only: never a verification, failure or confirmation.
            self._counters["stale_work_discards"] += 1
            record = self._recovery_record_locked(
                "core_authority_stale_work_discarded",
                "Superseded Home Assistant Core verification discarded",
                datetime.now(timezone.utc),
                level=logging.INFO,
                reason=reason if reason in _STALE_WORK_REASONS else "other",
                phase=phase if phase in _STALE_WORK_PHASES else "other",
                trigger=trigger if trigger in _AUDIT_TRIGGERS else "other",
            )
        self._emit([record])
        return self.health_snapshot()

    def _cancel_retired_monitor(self, task: asyncio.Task[None] | None) -> None:
        """Best-effort stop of a superseded lifecycle watcher."""

        if task is None or task.done():
            return
        try:
            task.cancel()
        except RuntimeError:
            # Its event loop is already closed; it can never run again.
            pass

    def configure(
        self,
        settings: Any,
        *,
        source: CoreSnapshotSource | None = None,
        authority_provider: Callable[
            [str], tuple[CoreAuthoritySelection, ...]
        ] | None = None,
        release_registry: CoreReleaseRegistry | None = None,
        audit_sink: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        """Configure one runtime; no probe or provider dispatch occurs here."""

        registry = release_registry or CoreReleaseRegistry(
            enabled=getattr(settings, "ha_core_release_registry_enabled", False),
            public_key=getattr(settings, "ha_core_release_registry_public_key", ""),
        )
        if source is None:
            from .source import AiohttpCoreSnapshotSource

            source = AiohttpCoreSnapshotSource(
                settings, profile_selector=registry.probe_profile_for
            )
        with self._lock:
            # Every configure is a new runtime generation and owner: work
            # started under an earlier one is discarded at its next resume
            # point and can never publish (``_WorkStamp``).
            self._runtime_generation += 1
            self._configuration_owner = object()
            retired_monitor = self._connection_monitor_task
            # ``CORE_READMISSION`` is process-global, while test and embedding
            # callers may configure and serve it from successive event loops.
            # Recreate loop-bound coordination primitives at the same startup
            # boundary instead of carrying a waiter from a retired loop.
            self._reconciliation_lock = asyncio.Lock()
            self._reprobe_event = asyncio.Event()
            self._source = source
            self._collector = CoreObservationCollector(self._source)
            self._authority_provider = authority_provider or registry.selections
            self._release_registry = registry
            self._published_registry_token = None
            self._audit_sink = audit_sink
            self._coordinator = CoreReadmissionCoordinator(CORE_RUNTIME_CAPABILITY_PROFILES)
            self._observation = None
            # Never reused: an epoch captured under an earlier configuration
            # cannot match this one.
            self._connection_epoch += 1
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
            was_admitted = self._admitted
            carried = (
                self._recovery_active, self._recovery_required, self._flap_count,
                self._backoff_level, self._retired_at, self._last_restored_at,
                self._last_restored_monotonic,
            )
            self._reset_recovery_locked()
            if self._boot_exception_spent:
                # A re-initialization is not a first boot. Keep the gate and
                # the flap bookkeeping; admitted authority it discards is a
                # withdrawal that restores through the full gate.
                (
                    self._recovery_active, self._recovery_required,
                    self._flap_count, self._backoff_level, self._retired_at,
                    self._last_restored_at, self._last_restored_monotonic,
                ) = carried
                if was_admitted:
                    self._enter_recovery_locked(datetime.now(timezone.utc))
                elif self._recovery_active:
                    self._recovery_state = "retrying"
            self._configured = True
        self._cancel_retired_monitor(retired_monitor)

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

    def request_reconciliation(
        self,
        *,
        connection_changed: bool = False,
        reason: str = "core_connection_changed",
        detail: str | None = None,
    ) -> None:
        source = self._source
        listeners: tuple[Callable[[], None], ...] = ()
        monitor_to_cancel: asyncio.Task[None] | None = None
        records: list[tuple[int, str, str, dict[str, Any]]] = []
        if connection_changed:
            with self._lock:
                had_observation = self._observation is not None or self._initialized
                was_admitted = self._admitted
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
                now = datetime.now(timezone.utc)
                self._last_material_change_at = now
                self._admitted = False
                self._admitted_capabilities = frozenset()
                confirming = self._recovery_confirmations > 0
                if retired_generation is not None:
                    self._spend_boot_exception_locked()
                    self._counters["retirements"] += 1
                    if was_admitted or self._recovery_active:
                        # A connection-level movement is transient until a
                        # complete re-verification proves otherwise.
                        self._enter_recovery_locked(now)
                    records.append(self._retirement_record_locked(
                        reason, retired_generation, now, detail=detail,
                        admitted=was_admitted,
                    ))
                elif confirming:
                    # A movement inside the confirmation window restarts the
                    # consecutive verification count in every gated state.
                    if self._recovery_active:
                        self._enter_recovery_locked(now)
                    self._recovery_state = "retrying"
                    records.append(self._recovery_record_locked(
                        "core_authority_recovery_reset",
                        "Home Assistant Core connection moved during recovery confirmation",
                        now,
                        reason=reason if reason in _RECOVERY_REASONS else "other",
                    ))
                self._recovery_confirmations = 0
                self._recovery_confirmation_key = None
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
        self._emit(records)
        self._reprobe_event.set()
        for listener in listeners:
            listener()

    async def reconcile_once(self, trigger: str = "periodic") -> dict[str, Any]:
        return await self._reconcile(trigger, self._work_stamp(trigger))

    async def _reconcile(self, trigger: str, stamp: _WorkStamp) -> dict[str, Any]:
        try:
            return await self._reconcile_serialized(trigger, stamp)
        finally:
            if stamp.boot_available and trigger not in _BOOT_TRIGGERS:
                # A background attempt that could claim the exemption spends it
                # when it ends instead of when it starts, so its own claim
                # survives the publication re-check. Releasing the asyncio lock
                # does not yield, so no queued attempt runs before this spend.
                # Superseded work never touches the current latch.
                with self._lock:
                    if self._stale_work_reason_locked(stamp) is None:
                        self._spend_boot_exception_locked()

    async def _reconcile_serialized(
        self, trigger: str, stamp: _WorkStamp
    ) -> dict[str, Any]:
        async with self._reconciliation_lock:
            if (stale := self._stale_work(stamp, trigger, "reconciliation_lock")) is not None:
                return stale
            collector = self._collector
            if collector is None:
                raise RuntimeError("Core readmission is not configured")
            registry = self._release_registry
            if registry is not None:
                await registry.refresh_if_due()
                if (stale := self._stale_work(stamp, trigger, "registry_refresh")) is not None:
                    return stale
            self._sync_registry_authority()
            collection_token = (
                registry.collection_token() if registry and registry.enabled else None
            )
            records: list[tuple[int, str, str, dict[str, Any]]] = []
            with self._lock:
                prior_observation = self._observation
                connection_epoch = self._connection_epoch
                self._counters["verification_attempts"] += 1
                if trigger != "startup":
                    self._counters["reprobes"] += 1
                attempt_at = datetime.now(timezone.utc)
                self._last_attempt_at = attempt_at
                if trigger not in _BOOT_TRIGGERS and not stamp.boot_available:
                    self._spend_boot_exception_locked()
                if trigger in _RECOVERY_CONFIRMATION_TRIGGERS and (
                    self._recovery_active or trigger == "failure_retry"
                ):
                    self._counters["recovery_attempts"] += 1
                    self._recovery_state = "retrying"
                    self._next_recovery_attempt_at = None
                    records.append(self._recovery_record_locked(
                        "core_authority_recovery_attempt",
                        "Home Assistant Core authority recovery attempt started",
                        attempt_at,
                    ))
            self._emit(records)
            observation = await collector.collect()
            if (stale := self._stale_work(stamp, trigger, "observation")) is not None:
                return stale
            # A newly observed Core version cannot keep the preceding generation
            # usable: it is a new authority that passes the restoration gate,
            # and with a registry it may await external compatibility data.
            if (
                observation.version is not None
                and self._retire_observed_version_change(
                    observation.version,
                    admitted_only=not (registry and registry.enabled),
                )
            ):
                prior_observation = None
            if registry and registry.enabled and observation.version is not None:
                if not compiled_exact_authority(observation.version):
                    refreshed = await registry.refresh_for_missing_release(
                        server_name=CORE_IDENTITY, version=observation.version
                    )
                    if (stale := self._stale_work(stamp, trigger, "registry_refresh")) is not None:
                        return stale
                    self._sync_registry_authority()
                    if refreshed and registry.collection_token() != collection_token:
                        # One bounded recollection after a missing-release fetch.
                        # A later concurrent change refuses this attempt below.
                        collection_token = registry.collection_token()
                        observation = await collector.collect()
                        if (stale := self._stale_work(stamp, trigger, "observation")) is not None:
                            return stale
            monitor_required, monitor_ready, newly_attached = (
                await self._ensure_connection_monitor(observation)
            )
            if (stale := self._stale_work(stamp, trigger, "monitor_attachment")) is not None:
                return stale
            if monitor_required and not monitor_ready:
                # A failed or incomplete observation cannot leave the prior
                # monitored generation usable.  Retire it just as an observed
                # socket movement would, then retry from a fresh source epoch.
                detail = (
                    self._monitor_failure_detail
                    if observation.reason_code == "observation_complete"
                    and self._monitor_failure_detail is not None
                    else self._failure_detail(collector, observation)
                )
                self.request_reconciliation(
                    connection_changed=True,
                    reason="core_lifecycle_monitor_unavailable",
                    detail=detail,
                )
                self._audit_early_failure(
                    trigger, observation, "core_lifecycle_monitor_unavailable",
                    detail=detail,
                )
                return self.health_snapshot()
            if newly_attached:
                # The first observation supplied only the expected version for
                # the watcher.  Recollect the complete two-snapshot evidence
                # under that authenticated lifecycle fence before publishing.
                observation = await collector.collect()
                if (stale := self._stale_work(stamp, trigger, "observation")) is not None:
                    return stale
            if registry and registry.enabled and registry.collection_token() != collection_token:
                self._sync_registry_authority()
                self._reprobe_event.set()
                self._audit_early_failure(
                    trigger, observation, "core_registry_changed_during_probe"
                )
                return self.health_snapshot()
            # Fence selection itself as well as the preceding probe collection.
            # Retain this token through publication: sampling a fresh token
            # afterward could bind admitted decisions to expired/denied data.
            selected_registry_token = (
                registry.selection_token(observation.version)
                if registry and registry.enabled and observation.version else None
            )
            authority = (
                self._authority_provider(observation.version)
                if observation.version is not None
                else ()
            )
            now = datetime.now(timezone.utc)
            failure_reason = None
            confirmation_pending = False
            listeners: tuple[Callable[[], None], ...] = ()
            audit_sink = None
            audit_event = None
            with self._lock:
                # The publication fence, atomic with everything published
                # below (admission, leases, listeners and catalog restoration
                # all follow only from this block). Work from a replaced
                # configuration or runtime generation is discarded, and so is
                # a boot-exemption claim once the latch is spent: it was valid
                # at start but no longer has an owner.
                stale_reason = self._stale_work_reason_locked(stamp)
                boot_claim = stamp.boot_available and not self._admitted
                if (
                    stale_reason is None
                    and boot_claim
                    and self._boot_exception_spent
                ):
                    stale_reason = "core_boot_exemption_spent"
                if stale_reason is not None:
                    pass  # Discarded after the lock, below: never a failure.
                elif connection_epoch != self._connection_epoch:
                    failure_reason = "core_connection_generation_stale"
                elif monitor_required and (
                    self._connection_monitor_version != observation.version
                    or self._connection_monitor_epoch != self._connection_epoch
                ):
                    failure_reason = "core_lifecycle_monitor_stale"
                elif (
                    selected_registry_token is not None
                    and registry.selection_token(observation.version) != selected_registry_token
                ):
                    self._reprobe_event.set()
                    failure_reason = "core_registry_changed_during_selection"
                # The single restoration gate: whenever no authority is
                # admitted (whatever the reason), an admitting publication
                # needs consecutive complete background verifications agreeing
                # on identity, version, selected registry authority and
                # reviewed contracts. Only the one-shot boot exception is
                # exempt; on-demand never admits.
                gated = not self._admitted and not boot_claim
                gate_records: list[tuple[int, str, str, dict[str, Any]]] = []
                if stale_reason is None and failure_reason is None and gated:
                    # Each confirmation is a full probe; nothing earlier is
                    # replayed or cached.
                    admitted_preview = self._coordinator.preview_admitted(
                        observation, authority
                    )
                    key = (
                        observation.identity, observation.version,
                        selected_registry_token, admitted_preview,
                    )
                    if (
                        self._recovery_confirmation_key is not None
                        and key != self._recovery_confirmation_key
                        and observation.identity_agrees
                        and observation.reason_code == "observation_complete"
                    ):
                        # Any trigger may restart the streak; only background
                        # verifications below may add to it. An incomplete
                        # observation restarts it as a recorded failure.
                        self._recovery_confirmations = 0
                        self._recovery_confirmation_key = None
                        self._recovery_state = "retrying"
                        self._append_event_locked(
                            "core_reconciliation",
                            "core_recovery_confirmation_reset",
                            None,
                        )
                        gate_records.append(self._recovery_record_locked(
                            "core_authority_recovery_reset",
                            "Home Assistant Core authority changed during recovery confirmation",
                            now,
                            reason="core_authority_key_changed",
                            trigger=trigger if trigger in _AUDIT_TRIGGERS else "other",
                        ))
                    if admitted_preview and trigger in _RECOVERY_CONFIRMATION_TRIGGERS:
                        if key == self._recovery_confirmation_key:
                            self._recovery_confirmations += 1
                        else:
                            self._recovery_confirmation_key = key
                            self._recovery_confirmations = 1
                        self._counters["recovery_confirmations"] += 1
                        if self._recovery_confirmations < self._recovery_required:
                            confirmation_pending = True
                            self._recovery_state = "confirming"
                            self._append_event_locked(
                                "core_reconciliation",
                                "core_recovery_confirmation_pending",
                                None,
                            )
                            records = gate_records + [self._recovery_record_locked(
                                "core_authority_recovery_confirming",
                                "Home Assistant Core verified; confirming stability before restoring authority",
                                now,
                                trigger=trigger,
                            )]
                    elif admitted_preview:
                        # Refused without publishing or counting; the streak
                        # and the supervisor's schedule are left unchanged.
                        confirmation_pending = True
                        self._counters["recovery_on_demand_refusals"] += 1
                        self._append_event_locked(
                            "core_reconciliation",
                            "core_recovery_on_demand_refused",
                            None,
                        )
                        records = gate_records + [self._recovery_record_locked(
                            "core_authority_recovery_on_demand_refused",
                            "On-demand Home Assistant Core verification cannot restore authority",
                            now,
                            level=logging.INFO,
                            trigger=trigger if trigger in _AUDIT_TRIGGERS else "other",
                        )]
                if (
                    stale_reason is None
                    and failure_reason is None
                    and not confirmation_pending
                ):
                    # Publish the decision generation and its exact observation
                    # under one runtime lock.  Routes and health must never see a
                    # new generation paired with the prior observation.
                    result = self._coordinator.reconcile(observation, authority)
                    changed = bool(result.published and not result.idempotent)
                    self._initialized = True
                    if result.published:
                        self._observation = observation
                        # A change during coordinator publication must differ from
                        # this token at the final health/use-time synchronization.
                        self._published_registry_token = selected_registry_token
                    admitted_now = bool(result.published and any(
                        item.disposition.admitted
                        for item in result.generation.decisions
                    ))
                    if admitted_now:
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
                    records = gate_records + self._record_publication_locked(
                        result, observation, collector, admitted_now, changed,
                        prior_observation is not None, now, restoring=gated,
                    )
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
                        or trigger == "failure_retry"
                        or not any(
                            item.disposition.admitted
                            for item in result.generation.decisions
                        )
                        else None
                    )
            if stale_reason is not None:
                return self._discard_stale_work(trigger, "publication", stale_reason)
            if failure_reason is not None:
                self._audit_early_failure(trigger, observation, failure_reason)
                return self.health_snapshot()
            self._emit(records)
            for listener in listeners:
                listener()
            self._write_audit(audit_sink, audit_event)
            return self.health_snapshot()

    def _record_publication_locked(
        self,
        result: Any,
        observation: CoreObservation,
        collector: Any,
        admitted_now: bool,
        changed: bool,
        replaced: bool,
        now: datetime,
        *,
        restoring: bool,
    ) -> list[tuple[int, str, str, dict[str, Any]]]:
        """Update recovery state after one coordinator publication."""

        records: list[tuple[int, str, str, dict[str, Any]]] = []
        if not result.published:
            return records
        was_admitted = self._admitted
        previous = self._admitted_capabilities
        current = frozenset(
            item.capability_id
            for item in result.generation.decisions
            if item.disposition.admitted
        )
        self._admitted = admitted_now
        self._admitted_capabilities = current
        if admitted_now:
            self._spend_boot_exception_locked()
            failed_attempts = self._consecutive_failures
            confirmations = self._recovery_confirmations
            self._consecutive_failures = 0
            self._backoff_level = 0
            self._recovery_confirmations = 0
            self._recovery_confirmation_key = None
            self._next_recovery_attempt_at = None
            if restoring:
                outage = (
                    max(0.0, (now - self._retired_at).total_seconds())
                    if self._retired_at is not None else None
                )
                self._recovery_active = False
                self._recovery_state = "restored"
                self._counters["authority_restorations"] += 1
                self._last_restored_at = now
                self._last_restored_monotonic = self._monotonic()
                records.append(self._recovery_record_locked(
                    "core_authority_restored",
                    "Home Assistant Core authority restored after complete re-verification",
                    now,
                    admitted_count=len(current),
                    failed_attempts=failed_attempts,
                    verified_confirmations=confirmations,
                    outage_seconds=round(outage, 3) if outage is not None else None,
                ))
            elif self._recovery_state not in {"restored", "stable"}:
                self._recovery_state = "stable"
            if changed and replaced and previous and not previous <= current:
                records.append(self._recovery_record_locked(
                    "core_authority_capabilities_withdrawn",
                    "Home Assistant Core authority withdrew some capabilities",
                    now,
                    withdrawn_count=len(previous - current),
                    admitted_count=len(current),
                ))
            return records
        complete = bool(
            observation.identity_agrees
            and observation.reason_code == "observation_complete"
        )
        if was_admitted:
            # Withdrawn for any reason: restoration passes the same gate.
            self._enter_recovery_locked(now)
        if complete:
            # The exact Core was observed but no reviewed contract admits it.
            # Keep it withdrawn and re-check it on the reconciliation interval.
            reason = "core_authority_incompatible"
            detail = result.generation.disposition.value
            self._recovery_state = "incompatible"
        else:
            reason = (
                observation.reason_code
                if observation.reason_code in _OBSERVATION_REASONS else "other"
            )
            detail = self._failure_detail(collector, observation)
        if was_admitted and changed:
            records.append(self._retirement_record_locked(
                reason, result.previous_generation, now, detail=detail,
                admitted=True,
            ))
        records.append(self._record_failure_locked(reason, detail, now))
        return records

    def _audit_early_failure(
        self,
        trigger: str,
        observation: CoreObservation,
        reason: str,
        *,
        detail: str | None = None,
    ) -> None:
        """Capture one attempted refusal, then invoke the sink outside the lock."""
        phase = _EARLY_FAILURE_PHASES[reason]
        with self._lock:
            self._counters["verification_failures"] += 1
            self._append_event_locked("core_reconciliation", reason, None)
            record = self._record_failure_locked(
                reason, detail, datetime.now(timezone.utc), phase=phase
            )
            sink = self._audit_sink
            event = {
                "event": "home_assistant_core_authority_reconciliation_failed",
                "result_status": "withheld",
                "analysis_summary": {
                    "model": "ha-core-authority-audit-v1",
                    "trigger": trigger if trigger in _AUDIT_TRIGGERS else "other",
                    "phase": phase,
                    "reason_code": reason,
                    "observation_reason_code": (
                        observation.reason_code
                        if observation.reason_code in _OBSERVATION_REASONS else "other"
                    ),
                    "observed_core_version": observation.version,
                    # No decision generation was published by this attempt.
                    "generation": None,
                    "identity_agreement": observation.identity_agrees,
                    "fallback_count": 0,
                },
            }
        self._emit([record])
        self._write_audit(sink, event)

    @staticmethod
    def _failure_detail(collector: Any, observation: CoreObservation) -> str:
        detail = getattr(collector, "last_failure_detail", None)
        if detail in PROBE_FAILURE_DETAILS:
            return detail
        reason = observation.reason_code
        return reason if reason in _OBSERVATION_REASONS else "other"

    def _record_failure_locked(
        self,
        reason: str,
        detail: str | None,
        now: datetime,
        *,
        phase: str | None = None,
    ) -> tuple[int, str, str, dict[str, Any]]:
        """Count one failed verification; consecutive confirmation restarts."""

        self._spend_boot_exception_locked()
        self._consecutive_failures += 1
        self._recovery_confirmations = 0
        self._recovery_confirmation_key = None
        self._last_failure_reason = reason
        self._last_failure_detail = detail
        self._last_failure_at = now
        if self._recovery_state != "incompatible":
            self._recovery_state = "backing_off"
        return self._recovery_record_locked(
            "core_authority_verification_failed",
            "Home Assistant Core authority verification failed",
            now,
            reason=reason,
            detail=detail,
            phase=phase,
        )

    def _enter_recovery_locked(self, now: datetime) -> None:
        """Arm hysteresis after admitted authority was withdrawn transiently."""

        self._spend_boot_exception_locked()
        if not self._recovery_active:
            restored = self._last_restored_monotonic
            flap = (
                restored is not None
                and self._monotonic() - restored < CORE_RECOVERY_FLAP_WINDOW_SECONDS
            )
            self._flap_count = self._flap_count + 1 if flap else 0
            self._recovery_required = (
                CORE_RECOVERY_FLAP_CONFIRMATIONS if flap
                else CORE_RECOVERY_CONFIRMATIONS
            )
            self._backoff_level = max(
                self._backoff_level,
                min(self._flap_count, CORE_RECOVERY_MAX_BACKOFF_LEVEL),
            )
            self._retired_at = now
            self._recovery_active = True
        self._recovery_confirmations = 0
        self._recovery_confirmation_key = None
        self._recovery_state = "retrying"

    def _retirement_record_locked(
        self,
        reason: str,
        generation: int | None,
        now: datetime,
        *,
        detail: str | None = None,
        admitted: bool,
    ) -> tuple[int, str, str, dict[str, Any]]:
        return self._recovery_record_locked(
            "core_authority_retired",
            "Home Assistant Core authority retired; Core-backed tools are withheld",
            now,
            reason=reason if reason in _RECOVERY_REASONS | _OBSERVATION_REASONS else "other",
            detail=detail,
            generation=generation,
            admitted=admitted,
        )

    def _recovery_record_locked(
        self,
        event: str,
        message: str,
        now: datetime,
        *,
        level: int = logging.WARNING,
        **fields: Any,
    ) -> tuple[int, str, str, dict[str, Any]]:
        context = {
            **{key: value for key, value in fields.items() if value is not None},
            "recovery_state": self._recovery_state,
            "consecutive_failures": self._consecutive_failures,
            "confirmations": self._recovery_confirmations,
            "confirmations_required": self._recovery_required,
            "at": _iso(now),
        }
        return level, event, message, context

    @staticmethod
    def _emit(records: list[tuple[int, str, str, dict[str, Any]]]) -> None:
        for level, event, message, context in records:
            log_event(logger, level, event, message, context=context)

    def _write_audit(self, sink: Any, event: dict[str, Any] | None) -> None:
        if sink is None or event is None:
            return
        try:
            failed = sink(event) is False
        except Exception:
            failed = True
        if failed:
            with self._lock:
                self._counters["audit_write_failures"] += 1

    async def reconcile_until_initialized(
        self,
        *,
        retry_delays: tuple[float, ...] = (0.25, 1.0, 3.0),
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> dict[str, Any]:
        with self._lock:
            # The boot sequence belongs to the configuration it started under.
            owner = self._configuration_owner
        snapshot = await self._reconcile("startup", self._work_stamp("startup", owner=owner))
        for delay in retry_delays:
            with self._lock:
                if owner is not self._configuration_owner:
                    # Replaced mid-sequence: the new configuration owns the
                    # rest of the boot, so this one neither retries nor spends.
                    return snapshot
                # Once the one-shot exception is spent (a failed verification,
                # or a re-initialization), a boot retry can no longer admit:
                # restoration belongs to the supervisor's background gate.
                exhausted = self._boot_exception_spent and not self._admitted
            if snapshot.get("identity_agreement") or exhausted:
                break
            await sleep(delay)
            snapshot = await self._reconcile(
                "startup_retry", self._work_stamp("startup_retry", owner=owner)
            )
        with self._lock:
            if owner is self._configuration_owner:
                # The boot sequence is over: a never-initialized Core now
                # passes the restoration gate like any other non-admitted state.
                self._spend_boot_exception_locked()
        return snapshot

    async def supervise(
        self,
        *,
        interval_seconds: float = CORE_RECONCILIATION_INTERVAL_SECONDS,
        sleep: Callable[[float], Any] = asyncio.sleep,
        retry_base_seconds: float = CORE_RECOVERY_RETRY_BASE_SECONDS,
        retry_cap_seconds: float = CORE_RECOVERY_RETRY_CAP_SECONDS,
        confirmation_seconds: float = CORE_RECOVERY_CONFIRMATION_SECONDS,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("Core reconciliation interval must be positive")
        if retry_base_seconds <= 0 or retry_cap_seconds <= 0 or confirmation_seconds <= 0:
            raise ValueError("Core recovery delays must be positive")
        cap = min(retry_cap_seconds, interval_seconds)
        with self._lock:
            # A supervisor serves only the configuration it started under.
            owner = self._configuration_owner
            # A runtime past its boot exception that holds no authority (a
            # failed boot or a re-initialization) starts on recovery pacing
            # rather than waiting a full reconciliation interval.
            retry = self._boot_exception_spent and (
                not self._initialized
                or (self._recovery_active and self._recovery_state != "incompatible")
                or self._confirming_locked()
            )
        try:
            while True:
                if self._supervisor_retired(owner):
                    return
                if retry:
                    # Failure/monitor retirement can itself set the wakeup.
                    # Coalesce those hints during one bounded, jittered backoff
                    # delay instead of creating an immediate retry chain.
                    # External changes still retire authority synchronously;
                    # after recovery they resume waking the supervisor at once.
                    delay = self._schedule_recovery_attempt(
                        base=retry_base_seconds,
                        cap=cap,
                        confirmation=min(confirmation_seconds, cap),
                        sample=jitter(),
                    )
                    await sleep(delay)
                    trigger = "failure_retry"
                else:
                    try:
                        await asyncio.wait_for(
                            self._reprobe_event.wait(), timeout=interval_seconds
                        )
                        trigger = "identity_or_connection_change"
                    except TimeoutError:
                        trigger = "periodic"
                if self._supervisor_retired(owner):
                    return
                self._reprobe_event.clear()
                snapshot = await self._reconcile(
                    trigger, self._work_stamp(trigger, owner=owner)
                )
                if self._supervisor_retired(owner):
                    return
                with self._lock:
                    recovering = (
                        self._recovery_active
                        and self._recovery_state != "incompatible"
                    ) or self._confirming_locked()
                    retry = recovering or not (
                        snapshot["initialized"] and snapshot["identity_agreement"]
                    )
                    if not retry and self._recovery_state == "incompatible":
                        # Incompatibility stays withdrawn; keep re-checking it.
                        self._next_recovery_attempt_at = datetime.now(
                            timezone.utc
                        ) + timedelta(seconds=interval_seconds)
        finally:
            monitor_task = None
            with self._lock:
                if owner is self._configuration_owner:
                    # Retiring the supervisor retires its runtime generation:
                    # work still in flight from it is discarded. A replaced
                    # configuration's watcher was already stopped by configure;
                    # the current one is never touched from here.
                    self._runtime_generation += 1
                    monitor_task = self._connection_monitor_task
                    self._connection_monitor_task = None
                    self._connection_monitor_token = None
                    self._connection_monitor_version = None
                    self._connection_monitor_epoch = None
            if monitor_task is not None:
                monitor_task.cancel()
                await asyncio.gather(monitor_task, return_exceptions=True)

    def _supervisor_retired(self, owner: object) -> bool:
        """Whether a supervisor's configuration was replaced; logged once."""

        with self._lock:
            if owner is self._configuration_owner:
                return False
            record = self._recovery_record_locked(
                "core_authority_supervisor_retired",
                "Superseded Home Assistant Core supervisor stopped",
                datetime.now(timezone.utc),
                level=logging.INFO,
                reason="core_configuration_replaced",
            )
        self._emit([record])
        return True

    def _confirming_locked(self) -> bool:
        """Whether a gated confirmation sequence is under way."""

        return not self._admitted and self._recovery_confirmations > 0

    def _schedule_recovery_attempt(
        self, *, base: float, cap: float, confirmation: float, sample: float
    ) -> float:
        """Choose and publish the next bounded recovery delay."""

        try:
            unit = min(1.0, max(0.0, float(sample)))
        except (TypeError, ValueError):
            unit = 0.5
        with self._lock:
            if self._confirming_locked():
                spacing = confirmation * (2 ** min(
                    self._flap_count, CORE_RECOVERY_MAX_BACKOFF_LEVEL
                ))
                delay = min(cap, spacing * (1 + CORE_RECOVERY_RETRY_JITTER * unit))
                self._recovery_state = "confirming"
            else:
                level = min(self._backoff_level, CORE_RECOVERY_MAX_BACKOFF_LEVEL)
                delay = min(
                    cap,
                    base * (2 ** level)
                    * (1 + CORE_RECOVERY_RETRY_JITTER * (2 * unit - 1)),
                )
                self._backoff_level = min(
                    level + 1, CORE_RECOVERY_MAX_BACKOFF_LEVEL
                )
                if self._recovery_state != "incompatible":
                    self._recovery_state = "backing_off"
            now = datetime.now(timezone.utc)
            self._last_retry_delay_seconds = round(delay, 3)
            self._next_recovery_attempt_at = now + timedelta(seconds=delay)
            record = self._recovery_record_locked(
                "core_authority_recovery_scheduled",
                "Home Assistant Core authority recovery attempt scheduled",
                now,
                level=logging.INFO,
                delay_seconds=round(delay, 3),
                next_attempt_at=_iso(self._next_recovery_attempt_at),
            )
        self._emit([record])
        return delay

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
        # The source deliberately discards watcher exception text; only
        # whether attachment ended or timed out is a bounded diagnostic.
        self._monitor_failure_detail = (
            "core_lifecycle_monitor_attach_failed" if done
            else "core_lifecycle_monitor_attach_timeout"
        )
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
            if current:
                self._monitor_failure_detail = None
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

    def _retire_observed_version_change(
        self, version: str, *, admitted_only: bool = False
    ) -> bool:
        with self._lock:
            if self._observation is None or self._observation.version == version:
                return False
            if admitted_only and not self._admitted:
                # Nothing usable to withdraw; the gate already applies.
                return False
            retired = self._coordinator.retire_current_generation()
            self._published_registry_token = None
            self._observation = None
            self._initialized = False
            now = datetime.now(timezone.utc)
            self._last_material_change_at = now
            records = self._material_retirement_locked(
                retired, "core_observed_version_changed", now
            )
            self._append_event_locked("core_authority_changed", "core_observed_version_changed", retired)
            listeners = tuple(self._listeners)
        self._emit(records)
        for listener in listeners:
            listener()
        return True

    def _material_retirement_locked(
        self, retired: int | None, reason: str, now: datetime
    ) -> list[tuple[int, str, str, dict[str, Any]]]:
        """Withdraw for changed authority material; restoration is gated."""

        was_admitted = self._admitted
        self._admitted = False
        self._admitted_capabilities = frozenset()
        # Changed authority material restarts any confirmation streak.
        self._recovery_confirmations = 0
        self._recovery_confirmation_key = None
        if retired is None:
            return []
        self._spend_boot_exception_locked()
        if was_admitted:
            self._enter_recovery_locked(now)
        self._counters["retirements"] += 1
        return [self._retirement_record_locked(
            reason, retired, now, admitted=was_admitted
        )]

    def _sync_registry_authority(self) -> None:
        """Withdraw moved/expired authority synchronously; never admit here."""
        registry = self._release_registry
        if registry is None or not registry.enabled:
            return
        with self._lock:
            observation = self._observation
            if (self._published_registry_token is None or observation is None
                    or observation.version is None):
                return
            if registry.selection_token(observation.version) == self._published_registry_token:
                return
            retired = self._coordinator.retire_current_generation()
            self._published_registry_token = None
            self._initialized = False
            now = datetime.now(timezone.utc)
            self._last_material_change_at = now
            records = self._material_retirement_locked(
                retired, "core_registry_authority_changed", now
            )
            self._append_event_locked("core_authority_changed", "core_registry_authority_changed", retired)
            listeners = tuple(self._listeners)
        self._emit(records)
        self._reprobe_event.set()
        for listener in listeners:
            listener()

    def _probe_profile_for(self, version: str | None):
        if version is None:
            return None
        return (self._release_registry.probe_profile_for(version)
                if self._release_registry else compiled_probe_profile(version))

    def route_status(
        self,
        capability_ids: tuple[str, ...],
        *,
        delegated_tool: str | None = None,
        delegated_adapter_version: str | None = None,
    ) -> dict[str, Any]:
        self._sync_registry_authority()
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
                probe_profile=self._probe_profile_for(observation.version if observation else None),
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
        expected_core_version: str | None = None,
        plan_created_at: str | None = None,
        delegated_tool: str | None = None,
        delegated_adapter_version: str | None = None,
    ) -> CoreRouteAuthority | None:
        self._sync_registry_authority()
        with self._lock:
            self._counters["lease_attempts"] += 1
            observation = self._observation
            generation = self._coordinator.current_generation
            if observation is None or generation is None:
                self._counters["lease_failures"] += 1
                return None
            if expected_core_version is not None and observation.version != expected_core_version:
                self._counters["lease_failures"] += 1
                return None
            provider_compatible, _provider_reason = delegated_provider_compatibility(
                tool_name=delegated_tool or "",
                core_version=observation.version,
                adapter_version=delegated_adapter_version,
                probe_profile=self._probe_profile_for(observation.version),
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
                core_version=observation.version,
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
        self._sync_registry_authority()
        with self._lock:
            observation = self._observation
            if (not isinstance(authority, CoreRouteAuthority) or observation is None
                    or authority.core_version != observation.version):
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

        self._sync_registry_authority()
        with self._lock:
            observation = self._observation
            generation = self._coordinator.current_generation
            valid = bool(
                isinstance(authority, CoreRouteAuthority)
                and observation is not None
                and generation is not None
                and authority.generation == generation.generation
                and authority.observation_fingerprint == observation.fingerprint
                and authority.core_version == observation.version
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

    def dependency_semantic_evidence(
        self, authority: CoreRouteAuthority, commits: tuple[CoreDispatchCommit, ...]
    ):
        """Bind collected semantics to current reviewed Core contracts.

        Issue only inside the consumed dependency build. Reuse of this evidence
        checks the original generation, never reacquires or extends its commit.
        The selected compiled probe fingerprint binds the semantic registry hash.
        """

        from ..dependency.semantic_registry import (
            ReviewedCoreSemantics, semantic_registry_identity,
        )

        self._sync_registry_authority()
        with self._lock:
            if (authority.capability_ids != ("core.dependency_helper_planning",)
                    or not self.revalidate(authority, commits)):
                return None
            generation = self._coordinator.current_generation
            observation = self._observation
            if generation is None or observation is None:
                return None
            profile = self._probe_profile_for(observation.version)
            decisions = tuple(generation.decision_for(name) for name in (
                "core.template_semantics", "core.dependency_helper_planning"))
            if profile is None or any(d is None or not d.disposition.admitted for d in decisions):
                return None
            contract = {
                **semantic_registry_identity(),
                "core_version": observation.version,
                "probe_profile_id": profile.profile_id,
                "probe_contract_fingerprint": profile.contract_fingerprint,
                **{d.capability_id: d.contract_fingerprint for d in decisions},
            }

            material = tuple(sorted(contract.items()))

            def current(candidate: ReviewedCoreSemantics) -> bool:
                self._sync_registry_authority()
                with self._lock:
                    # Object identity also rejects configure/reset reuse of a
                    # numerical generation. Retirement cannot revive evidence.
                    selected = self._probe_profile_for(observation.version)
                    return bool(
                        candidate.contract == material
                        and candidate.observed_version == observation.version
                        and self._coordinator.current_generation is generation
                        and self._observation is not None
                        and self._observation.fingerprint == observation.fingerprint
                        and selected == profile
                    )

            evidence = ReviewedCoreSemantics(
                observation.version, material, current)
            return evidence if evidence.matches(observation.version) else None

    def finish(self, commits: tuple[CoreDispatchCommit, ...] | None) -> bool:
        return bool(commits and self._coordinator.finish_commits(commits))

    def record_catalog_change(self, *, restored: bool, count: int = 1) -> None:
        if count < 0:
            raise ValueError("catalog change count must not be negative")
        with self._lock:
            key = "catalog_restorations" if restored else "catalog_withdrawals"
            self._counters[key] += count
            record = (
                self._recovery_record_locked(
                    "core_catalog_tools_restored" if restored
                    else "core_catalog_tools_withdrawn",
                    "Core-backed delegated tools restored to the catalog" if restored
                    else "Core-backed delegated tools withdrawn from the catalog",
                    datetime.now(timezone.utc),
                    count=count,
                )
                if count else None
            )
        if record is not None:
            self._emit([record])

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
        decisions = generation.decisions
        admitted = sum(item.disposition.admitted for item in decisions)
        return {
            "event": "home_assistant_core_authority_reconciled",
            "result_status": "success" if admitted else "withheld",
            "analysis_summary": {
                "model": RECONCILED_AUDIT_MODEL,
                "trigger": trigger if trigger in _AUDIT_TRIGGERS else "other",
                "observed_core_version": observation.version,
                "identity_agreement": observation.identity_agrees,
                "generation": generation.generation,
                "disposition": generation.disposition.value,
                "admitted_count": admitted,
                "withheld_count": len(decisions) - admitted,
                "dispositions": _audit_disposition_groups(decisions),
                "fallback_count": 0,
            },
        }

    def _recovery_projection_locked(self) -> dict[str, Any]:
        return {
            "recovery_state": self._recovery_state,
            "consecutive_failures": self._consecutive_failures,
            "last_failure_reason": self._last_failure_reason,
            "last_failure_detail": self._last_failure_detail,
            "last_failure_at": _iso(self._last_failure_at),
            "retired_at": _iso(self._retired_at),
            "last_attempt_at": _iso(self._last_attempt_at),
            "next_recovery_attempt_at": _iso(self._next_recovery_attempt_at),
            "retry_delay_seconds": self._last_retry_delay_seconds,
            "confirmations": self._recovery_confirmations,
            "confirmations_required": self._recovery_required,
            "last_restored_at": _iso(self._last_restored_at),
        }

    def health_snapshot(self) -> dict[str, Any]:
        self._sync_registry_authority()
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
                "recovery": self._recovery_projection_locked(),
                "release_registry": self._release_registry.snapshot() if self._release_registry else None,
                "recent_events": [dict(item) for item in self._events],
                "fallback_count": 0,
            }
            canonical_json(projection, maximum=MAX_REPORT_BYTES)
            return projection

    def health_projection(self) -> dict[str, Any]:
        """Return the bounded Core authority summary used by public health."""

        self._sync_registry_authority()
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
                "recovery": self._recovery_projection_locked(),
                "release_registry": self._release_registry.snapshot() if self._release_registry else None,
                "recent_event_count": len(self._events),
                "fallback_count": 0,
            }
            canonical_json(projection, maximum=MAX_REPORT_BYTES)
            return projection


CORE_READMISSION = CoreRuntime()


__all__ = [
    "CORE_READMISSION",
    "CORE_RECONCILIATION_INTERVAL_SECONDS",
    "CORE_RECOVERY_CONFIRMATIONS",
    "CORE_RECOVERY_CONFIRMATION_SECONDS",
    "CORE_RECOVERY_FLAP_CONFIRMATIONS",
    "CORE_RECOVERY_FLAP_WINDOW_SECONDS",
    "CORE_RECOVERY_RETRY_BASE_SECONDS",
    "CORE_RECOVERY_RETRY_CAP_SECONDS",
    "CORE_RECOVERY_RETRY_JITTER",
    "CORE_RECOVERY_STATES",
    "CoreRouteAuthority",
    "CoreRuntime",
]
