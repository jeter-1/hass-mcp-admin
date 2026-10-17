"""Durable ordinary receipts with read-only recovery and expiring new IDs.

No governance plan, panel approval, generic service, or implicit restoration is
created. F3's internal approval callback records ordinary request authority;
public receipts explicitly identify that distinction.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import dataclasses
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile
import uuid

from ..f3.executor import SharedOperationExecutor, PreIntentRetryRequired
from ..f3.locks import LockOwnershipError
from ..f3.models import (ExecutionIdentity, ExecutorTiming, LockOwner, LockToken,
                         parse_timestamp, validate_sha256)
from ..request_context import current_telemetry
from .adapter import FanAdapter, prepare_record
from .authority import FanCoreAuthority
from .contracts import FAN_CONTRACTS, PROVIDER, FanRefusal, FanRequest, digest
from .locks import FAN_LOCK_TIMING, FanLockStore
from .audit import FanExecutionRepository, dispatch_fact_fields

MAX_RECORDS = 4096
MAX_ACTIVE = 16
# HAMCP-192 decision 1: reads at 0, 1, 2, 4, 6, 8 and 10 s, at most ~10 s.
INLINE_POLL_DELAYS = (1, 1, 2, 2, 2, 2)
INLINE_POLL_BUDGET_SECONDS = 10
RECOVERY_SWEEP_SECONDS = 30  # application._supervise_f3_recovery cadence
MAX_BLOCKING_TASKS = 4
ADMIN_INGRESS_PREFIX = "home_assistant_admin_ingress:"
HOLD_RELEASE_REASON = "owner_authorized_release"
HOLD_RELEASE_MODEL = "typed-hold-release-v1"
HOLD_RELEASE_FIELDS = frozenset({
    "model", "task_id", "prepared_hash", "record_generation", "hold_key", "hold_generation",
    "authority_hash", "readback_hash", "readback_matches_requested_state",
    "authorized_at", "released_at",
})
MAX_HOLD_RELEASE_ITEMS = 100


class _InlineObservationAdapter:
    """Delegate to the typed adapter; poll only the inline observation.

    The executor calls ``observe`` only right after its own dispatch, while
    the renewed lease is held. Recovery calls ``recover`` (one read per
    sweep). Nothing here can dispatch: every read is the adapter's own
    ``core.read`` -> ``provider.state`` observation.
    """

    def __init__(self, adapter, *, now, sleep):
        self.inner, self._now, self._sleep = adapter, now, sleep

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def observe(self, prepared, dispatch):
        started = self._now()
        reads = 0
        for delay in (*INLINE_POLL_DELAYS, None):
            observation = await self.inner.observe(prepared, dispatch)
            reads += 1
            if observation.intended_result_observed is True or delay is None:
                break
            if (self._now() - started).total_seconds() + delay > INLINE_POLL_BUDGET_SECONDS:
                break
            await self._sleep(delay)
        return dataclasses.replace(observation, attempt_count=reads)


class FanService:
    # Code-owned specializations share ownership/recovery, never domain grants.
    namespace = "ordinary-fan-v1"
    request_type = FanRequest
    adapter_type = FanAdapter
    repository_type = FanExecutionRepository
    contracts = FAN_CONTRACTS
    prepare_record = staticmethod(prepare_record)
    provider_name = PROVIDER
    binding_attribute = "ordinary_fan_binding"
    outcome_key = "fan_outcome"
    refusal = FanRefusal

    @staticmethod
    def receipt_parameters(request):
        return {"percentage": request.percentage}

    def __init__(self, root, provider, core, *, now=lambda: datetime.now(timezone.utc), audit=None,
                 sleep=asyncio.sleep):
        self.root = Path(root) / self.namespace
        self.root.mkdir(parents=True, exist_ok=True)
        self.now = now
        self.provider, self.core = provider, core
        self.adapter = _InlineObservationAdapter(self.adapter_type(provider, core),
                                                 now=now, sleep=sleep)
        # Exact shared lock namespace, independent ordinary execution records.
        self.locks = FanLockStore(root, self.load)
        self.executions = self.repository_type(self.root, self.load, audit)
        self.active = {}
        self.preparing = 0
        self.recovery_failures = 0

    @contextmanager
    def transaction(self):
        with (self.root / ".declarations.lock").open("a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _path(self, task_id):
        import re
        if not re.fullmatch(r"[a-f0-9]{32}", task_id):
            raise self.refusal("fan_task_id_invalid")
        return self.root / (task_id + ".json")

    def load(self, task_id):
        path = self._path(task_id)
        if not path.exists():
            return None
        if path.stat().st_size > 16_384:
            raise self.refusal("fan_receipt_corrupt")
        value = json.loads(path.read_bytes())
        if isinstance(value, dict) and value.get("model") == self.namespace + "-core-bound-v2":
            if set(value) != {"model", "request", "baseline", "prepared_hash",
                              "provider_contract", "core_binding"} or value["core_binding"] is None:
                raise self.refusal("fan_receipt_corrupt")
            try:
                request = self.request_type.model_validate(value["request"]).checked()
                prepared = self.prepare_record(request, value["baseline"],
                                               value["provider_contract"], value["core_binding"])
            except (ValueError, TypeError, KeyError):
                raise self.refusal("fan_receipt_corrupt") from None
            if request.task_id != task_id or prepared.prepared_operation_hash != value["prepared_hash"]:
                raise self.refusal("fan_receipt_corrupt")
            return prepared
        if set(value) != {"model", "request", "baseline", "prepared_hash"} or value["model"] != self.namespace:
            raise self.refusal("fan_receipt_corrupt")
        request = self.request_type.model_validate(value["request"]).checked()
        if request.task_id != task_id:
            raise self.refusal("fan_receipt_corrupt")
        # No storage migration or relabeling: recover the exact contract from
        # the original writer's hash over a closed set of compiled projections.
        matches = []
        for contract in self.contracts:
            prepared = self.prepare_record(request, value["baseline"], contract)
            if prepared.prepared_operation_hash == value["prepared_hash"]:
                matches.append(prepared)
        if len(matches) != 1:
            raise self.refusal("fan_receipt_corrupt")
        return matches[0]

    def save(self, prepared):
        request = prepared.request
        with self.transaction():
            prior = self.load(request.task_id)
            if prior is not None:
                if prior.request != request:
                    raise self.refusal("fan_operation_id_rebound")
                return prior
            if not request.is_fresh(self.now()):
                raise self.refusal("fan_operation_id_expired")
            if len(list(self.root.glob("*.json"))) >= MAX_RECORDS:
                raise self.refusal("fan_receipt_capacity_exhausted")
            value = {"model": self.namespace, "request": request.model_dump(),
                     "baseline": prepared.baseline, "prepared_hash": prepared.prepared_operation_hash}
            if prepared.core_binding is not None:
                value.update(model=self.namespace + "-core-bound-v2",
                             provider_contract=prepared.provider_contract,
                             core_binding=prepared.core_binding)
            payload = json.dumps(value, sort_keys=True, allow_nan=False).encode()
            fd, temp = tempfile.mkstemp(prefix=".fan-", dir=self.root)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload); handle.flush(); os.fsync(handle.fileno())
                os.replace(temp, self._path(request.task_id))
                directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            return prepared

    def executor(self):
        return SharedOperationExecutor(
            lock_store=self.locks, execution_repository=self.executions,
            lock_timing=FAN_LOCK_TIMING,
            executor_timing=ExecutorTiming(180, 120, 6, 6), now=self.now,
            final_read_at_deadline=True,
        )

    def _authorization(self, request):
        telemetry = current_telemetry()
        binding = digest(request.model_dump())
        def check(*, fresh=True):
            if telemetry is None or getattr(telemetry, self.binding_attribute) != binding:
                raise self.refusal("fan_connector_authorization_required")
            if fresh and not request.is_fresh(self.now()):
                raise self.refusal("fan_operation_id_expired")
        check(fresh=False)
        return check

    async def control(self, request):
        telemetry = current_telemetry()
        if telemetry:
            telemetry.audit_context.update(task_id=request.task_id, operation_id=request.operation_id)
        try:
            result = await self._control(request)
        except (Exception, asyncio.CancelledError):
            # Cancellation gets the same durable-state audit cleanup, then
            # propagates unchanged; it never becomes a response or retry.
            if telemetry:
                if "task_id" in self.failure_guidance(request):
                    telemetry.audit_context[self.outcome_key] = "request_failed_reconcile_task"
                else:
                    telemetry.audit_context.pop("task_id", None)
                    telemetry.audit_context[self.outcome_key] = "preparation_failed_no_task"
            raise
        if telemetry:
            telemetry.audit_context.update({key: result[key] for key in (
                "task_id", "operation_id", "operation_hash", "provider_attempt_count",
                "dispatch_intent_recorded", "provider_response_received", "terminal")})
            # HAMCP-181: the same dispatch facts as the receipt (bounded by
            # request_summary's whitelist before any audit write).
            telemetry.audit_context.update({key: result[key] for key in (
                "dispatch_phase", "attempt_counts_basis", "provider_dispatch_occurred",
                "dispatched_at_basis", "dispatch_intent_recorded_at",
                "provider_response_recorded_at") if key in result})
            telemetry.audit_context[self.outcome_key] = result["state"]
        return result

    def failure_guidance(self, request):
        """Point to a task only when durable state says one exists.

        Execution starts only after the declaration is saved, so its absence
        proves no dispatch. No rejection record is written: the same ID becomes
        reconciliation-only once a declaration exists, and not before.
        """
        try:
            saved = self._path(request.task_id).exists()
        except Exception:
            saved = True  # Unknown durable state keeps the conservative pointer.
        if saved:
            return {"task_id": request.task_id, "reconciliation": "get_execution_task"}
        return {"stage": "preparation_failed", "dispatch": "no_dispatch", "reconciliation": "no_task"}

    async def _control(self, request):
        request = self.request_type.model_validate(request.model_dump()).checked()
        check = self._authorization(request)
        prior = self.load(request.task_id)
        if prior is not None:
            if prior.request != request:
                raise self.refusal("fan_operation_id_rebound")
            # Once declared, a repeated call is reconciliation only, even
            # before durable intent.
            return await self.reconcile(request.task_id)
        check()
        if len(self.active) + self.preparing >= MAX_ACTIVE:
            raise self.refusal("fan_capacity_exhausted")
        self.preparing += 1
        try:
            prepared = await self.adapter.prepare(request)
            check()
        finally:
            self.preparing -= 1
        prepared = self.save(prepared)
        if request.task_id in self.active:
            return self.receipt(request.task_id)
        record = self.executions.get(request.task_id)
        if record is not None:
            return await self.reconcile(request.task_id)
        task = asyncio.create_task(self._execute(prepared, check), name=self.namespace + "-execution")
        self.active[request.task_id] = task
        task.add_done_callback(lambda finished: self._finished(request.task_id, finished))
        # Request cancellation never authorizes another mutation. Retirement of
        # the gateway context prevents pre-intent work from outliving authority.
        await asyncio.shield(task)
        return self.receipt(request.task_id)

    def _finished(self, task_id, task):
        self.active.pop(task_id, None)
        if not task.cancelled():
            task.exception()  # retrieve errors even if the client disconnected

    async def _execute(self, prepared, check):
        telemetry = current_telemetry()
        identity = ExecutionIdentity(
            prepared.request.task_id, None, prepared.request.task_id,
            telemetry.request_id if telemetry else uuid.uuid4().hex, uuid.uuid4().hex,
        )
        async def authorize():
            check()
        executor = self.executor()
        try:
            await executor.execute(
                adapter=self.adapter, prepared=prepared, identity=identity,
                approval_consumption=authorize, dispatch_authority=self.core,
            )
        except PreIntentRetryRequired:
            # No ordinary authorization is carried into a later request. A
            # failed pre-intent authorization/persistence boundary is cancelled,
            # never treated as permission for a blind retry.
            await executor.cancel(identity.task_id)

    def receipt(self, task_id):
        prepared = self.load(task_id)
        if prepared is None:
            return None
        record = self.executions.get(task_id)
        self.executions.project(record)
        retained = [item for item in self.locks.records() if item.task_id == task_id]
        return {
            "task_id": task_id, "operation_id": prepared.request.operation_id,
            "operation_hash": prepared.prepared_operation_hash,
            "authorization": "authenticated_connector", "provider": self.provider_name,
            "provider_contract": prepared.provider_contract, "fallback": "none",
            **({"core_binding": prepared.core_binding} if prepared.core_binding is not None else {}),
            "entity_id": prepared.request.entity_id, "action": prepared.request.action,
            **self.receipt_parameters(prepared.request),
            "state": record.task_state if record else "created",
            "terminal": record.terminal if record else False,
            "terminal_outcome": record.normalized_outcome if record and record.terminal else None,
            "provider_attempt_count": record.dispatch_count if record else 0,
            "dispatch_intent_recorded": bool(record and record.dispatch_intent),
            "dispatched_at": None,  # persisted intent is not independent delivery proof
            "provider_response_received": record.provider_response_received if record else False,
            "verification": record.evidence if record else {},
            "redispatch_prohibited": True,
            "consequence_coverage": "incomplete",
            "physical_feedback_verified": False,
            "retained_locks": [{"key": item.key, "generation": item.generation,
                                "mode": item.mode, "conflict_hold": item.conflict_hold}
                               for item in retained],
            "complete": True,
            # HAMCP-181 (OD-6(ii)): additive qualifiers; ``dispatched_at``
            # above stays the literal null.
            **{key: value for key, value in dispatch_fact_fields(record).items()
               if key not in {"dispatched_at"}},
            # HAMCP-192 decisions 4-6: additive, derived when built from the
            # existing record and live locks; nothing here is stored.
            **self._receipt_hints(prepared, record, retained),
        }

    def _receipt_hints(self, prepared, record, retained):
        hints = {}
        if record is None:
            return hints
        status = self._observation_status(record, retained)
        if status is not None:
            hints["observation_status"] = status
        if record.terminal and record.normalized_outcome == "lock_conflict":
            hints["blocking_tasks_observed_at"] = self.now().isoformat()
            hints["blocking_tasks"] = self._blocking_tasks(prepared, record.execution_identity().task_id)
        if record.normalized_outcome == "manual_review_required":
            hints["hold_release"] = self._hold_release_status(record, retained)
        return hints

    @staticmethod
    def _last_event(record, event_type, code=None):
        for event in reversed(record.events):
            if event["event_type"] == event_type and (code is None or code in event["diagnostic_codes"]):
                return event
        return None

    @staticmethod
    def _observing_reason(record):
        for event in reversed(record.events):
            codes = event["diagnostic_codes"]
            if event["event_type"] == "verification_recorded":
                if "verification_failed" in codes:
                    return "verification_failed"
                return "device_state_not_yet_matching"
            if event["event_type"] == "observation_recorded":
                if "observation_failed" in codes:
                    return "observation_read_failed"
                return "observation_incomplete"
            if event["event_type"] == "dispatch_result_recorded":
                if any(code.endswith("_response_uncertain") for code in codes):
                    return "dispatch_response_uncertain"
                return "awaiting_first_observation"
            if event["event_type"] == "dispatch_intent_committed":
                return "dispatch_outcome_unrecorded"
        return "awaiting_first_observation"

    def _observation_status(self, record, retained):
        if record.dispatch_intent is None:
            return None
        observed = self._last_event(record, "observation_recorded")
        verified = self._last_event(record, "verification_recorded", "exact_readback_verified")
        status = {
            "evidence_deadline": record.dispatch_intent["evidence_deadline"],
            "final_read_at_deadline": True,
            "reobservation": "background_sweep_every_30s",
            "observation_attempts": record.observation_attempts,
            "last_observed_at": observed["occurred_at"] if observed else None,
            "verified_at": (verified["occurred_at"]
                            if verified and record.normalized_outcome == "succeeded_verified" else None),
        }
        if not record.terminal:
            status["reason"] = self._observing_reason(record)
            # Recovery re-reads only after both the claim and this task's own
            # lease have expired; the 30 s sweep then performs the read.
            candidates = [record.claim_expires_at] + [
                item.lease_expires_at for item in retained if not item.conflict_hold]
            status["next_reobservation_not_before"] = max(
                candidates, key=lambda value: parse_timestamp(value, field_name="expires_at"))
        return status

    def _blocking_tasks(self, prepared, task_id):
        """Name the current holders of this task's conflicting keys."""
        now = self.now()
        requested = {request.key: getattr(request.mode, "value", request.mode)
                     for request in self.adapter.lock_requests(prepared)}
        holders = []
        for item in self.locks.records():
            if item.task_id == task_id or item.key not in requested:
                continue
            if not item.conflict_hold and parse_timestamp(item.lease_expires_at,
                                                          field_name="lease_expires_at") <= now:
                continue  # expired: no longer an active holder
            if requested[item.key] != "exclusive" and item.mode != "exclusive":
                continue  # shared with shared never conflicts
            holders.append(item)
        result = []
        for item in sorted(holders, key=lambda value: (value.key, value.task_id))[:MAX_BLOCKING_TASKS]:
            try:
                record = self.executions.get(item.task_id)
            except Exception:
                record = None
            entry = {
                "task_id": item.task_id, "lock_key": item.key,
                "conflict_hold": item.conflict_hold,
                "state": (record.task_state if record is not None
                          else "conflict_hold" if item.conflict_hold else "lease_held"),
                "expected_release_at": None if item.conflict_hold else item.lease_expires_at,
                "release": ("governed_hold_release_required" if item.conflict_hold
                            else "verified_readback_or_deadline_final_read"),
            }
            if record is not None and record.dispatch_intent is not None and not item.conflict_hold:
                entry["decision_deadline"] = record.dispatch_intent["evidence_deadline"]
            result.append(entry)
        return result

    def _hold_release_status(self, record, retained):
        try:
            release = self._load_release(record.execution_identity().task_id)
        except FanRefusal:
            return {"released": False, "release_record": "invalid"}
        if release is None:
            held = any(item.conflict_hold for item in retained)
            return {"released": False,
                    "procedure": "home_assistant_admin_ingress_typed_hold_release" if held else None}
        matches = release["readback_matches_requested_state"]
        return {
            "released": release["released_at"] is not None,
            "authorized_at": release["authorized_at"],
            "released_at": release["released_at"],
            "readback_matches_requested_state": matches,
            "readback_evidence_hash": release["readback_hash"],
            "outcome": (("hold_released_state_matches" if matches else "hold_released_state_differs")
                        if release["released_at"] is not None else "hold_release_authorized_pending"),
            "dispatch": "none",
        }

    # HAMCP-192 decision 6: one additive sidecar per released task. Existing
    # declaration, execution and lock formats are unchanged; older code
    # ignores this directory.
    def _release_path(self, task_id):
        return self.root / "hold-releases" / self._path(task_id).name

    def _load_release(self, task_id):
        path = self._release_path(task_id)
        if not path.exists():
            return None
        try:
            if path.stat().st_size > 4096:
                raise ValueError
            value = json.loads(path.read_bytes())
            if (not isinstance(value, dict) or set(value) != HOLD_RELEASE_FIELDS
                    or value["model"] != HOLD_RELEASE_MODEL or value["task_id"] != task_id
                    or type(value["record_generation"]) is not int
                    or type(value["hold_generation"]) is not int
                    or type(value["readback_matches_requested_state"]) is not bool):
                raise ValueError
            for key in ("prepared_hash", "authority_hash", "readback_hash"):
                validate_sha256(value[key], field_name=key)
            parse_timestamp(value["authorized_at"], field_name="authorized_at")
            if value["released_at"] is not None:
                parse_timestamp(value["released_at"], field_name="released_at")
            if not isinstance(value["hold_key"], str) or not value["hold_key"].startswith("entity:"):
                raise ValueError
        except (OSError, ValueError, TypeError):
            raise self.refusal("fan_hold_release_corrupt") from None
        return value

    def _write_release(self, value):
        path = self._release_path(value["task_id"])
        path.parent.mkdir(exist_ok=True)
        payload = json.dumps(value, sort_keys=True, allow_nan=False).encode()
        fd, temp = tempfile.mkstemp(prefix=".release-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            os.replace(temp, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def hold_release_item(self, task_id):
        """The review facts for a releasable hold, or ``None``.

        Releasable means: no live execution, a terminal manual-review record,
        no release recorded yet, and exactly this task's settled entity hold.
        """
        prepared = self.load(task_id)
        if prepared is None or task_id in self.active:
            return None
        record = self.executions.get(task_id)
        if (record is None or not record.terminal
                or record.normalized_outcome != "manual_review_required"
                or self._release_recorded(task_id)):
            return None
        owned = [item for item in self.locks.records() if item.task_id == task_id]
        if not self.locks.hold_settled(record, owned):
            return None
        hold = owned[0]
        observed = self._last_event(record, "observation_recorded")
        return {
            "kind": self.executions.kind, "task_id": task_id,
            "operation_id": prepared.request.operation_id,
            "entity_id": prepared.request.entity_id, "action": prepared.request.action,
            "prepared_hash": record.prepared_operation_hash,
            "record_generation": len(record.events),
            "hold_binding": f"{hold.key}:{hold.generation}",
            "evidence_deadline": (record.dispatch_intent or {}).get("evidence_deadline"),
            "manual_review_reason": record.evidence.get("manual_review_reason_code"),
            "observation_attempts": record.observation_attempts,
            "last_observed_at": observed["occurred_at"] if observed else None,
            "updated_at": record.updated_at,
        }

    def _release_recorded(self, task_id):
        # Any sidecar, valid or not, ends releasability; an invalid one fails closed.
        return self._release_path(task_id).exists()

    def hold_release_items(self):
        items = []
        for path in sorted(self.root.glob("*.json"))[:MAX_RECORDS]:
            try:
                item = self.hold_release_item(path.stem)
            except Exception:
                continue
            if item is not None:
                items.append(item)
            if len(items) >= MAX_HOLD_RELEASE_ITEMS:
                break
        return items

    async def release_hold(self, task_id, *, record_generation, prepared_hash,
                           hold_binding, authorized_principal):
        """Release one manual-review hold on an Ingress administrator decision.

        The decision is the external approval. A fresh readback is recorded
        first and reported truthfully; the task's outcome stays
        ``manual_review_required``. No provider dispatch is reachable here.
        """
        if (not isinstance(authorized_principal, str)
                or not len(ADMIN_INGRESS_PREFIX) < len(authorized_principal) <= 180
                or not authorized_principal.startswith(ADMIN_INGRESS_PREFIX)):
            raise self.refusal("fan_hold_release_authority_required")
        binding = (record_generation, prepared_hash, hold_binding)
        item = self.hold_release_item(task_id)
        if item is None:
            raise self.refusal("fan_hold_release_unavailable")
        if binding != (item["record_generation"], item["prepared_hash"], item["hold_binding"]):
            raise self.refusal("fan_hold_release_stale")
        prepared = self.load(task_id)
        try:
            observation = await self.adapter.inner.observe(prepared, None)
            matches = observation.intended_result_observed is True
            readback_hash = validate_sha256(observation.readback_state_fingerprint,
                                            field_name="readback_state_fingerprint")
        except Exception:
            raise self.refusal("fan_hold_release_readback_unavailable") from None
        hold_key, _, generation = hold_binding.rpartition(":")
        release = {
            "model": HOLD_RELEASE_MODEL, "task_id": task_id, "prepared_hash": prepared_hash,
            "record_generation": record_generation, "hold_key": hold_key,
            "hold_generation": int(generation),
            "authority_hash": digest({"principal": authorized_principal}),
            "readback_hash": readback_hash, "readback_matches_requested_state": matches,
            "authorized_at": self.now().isoformat(), "released_at": None,
        }
        with self.transaction():
            # The read awaited; re-check the exact reviewed facts before the
            # decision becomes durable. A second decision finds this sidecar.
            item = self.hold_release_item(task_id)
            if item is None:
                raise self.refusal("fan_hold_release_unavailable")
            if binding != (item["record_generation"], item["prepared_hash"], item["hold_binding"]):
                raise self.refusal("fan_hold_release_stale")
            self._write_release(release)
        self._finish_hold_release(task_id, release)
        record = self.executions.get(task_id)
        outcome = "hold_released_state_matches" if matches else "hold_released_state_differs"
        return {"status": outcome, "task_id": task_id,
                "readback_matches_requested_state": matches,
                "readback_evidence_hash": readback_hash,
                "terminal_outcome": record.normalized_outcome, "dispatch": "none"}

    def _finish_hold_release(self, task_id, release):
        """Complete an already authorized release; never start one."""
        if release["released_at"] is not None:
            return release
        record = self.executions.get(task_id)
        if (record is None or not record.terminal
                or record.normalized_outcome != "manual_review_required"
                or record.prepared_operation_hash != release["prepared_hash"]):
            raise LockOwnershipError("fan hold release record changed")
        identity = record.execution_identity()
        prepared = self.load(task_id)
        owned = [item for item in self.locks.records() if item.task_id == task_id]
        if owned:
            hold = owned[0]
            if (len(owned) != 1 or hold.key != release["hold_key"] or not hold.conflict_hold
                    or hold.generation != release["hold_generation"]):
                raise LockOwnershipError("fan hold release was fenced")
            owner = LockOwner(identity.owner_id, task_id, None, prepared.operation, identity.attempt_id)
            self.locks.release_conflict_hold(
                owner=owner, tokens=(LockToken(hold.key, hold.generation, hold.mode),),
                reason_code=HOLD_RELEASE_REASON)
        release = {**release, "released_at": self.now().isoformat()}
        self._write_release(release)
        # Audited once, at the transition, whether the decision request or a
        # later recovery pass completes it.
        self._audit_hold_release(prepared, record, release)
        return release

    def _audit_hold_release(self, prepared, record, release):
        audit = self.executions.audit
        if audit is None:
            return
        kind = self.executions.kind
        outcome = ("hold_released_state_matches" if release["readback_matches_requested_state"]
                   else "hold_released_state_differs")
        entry = {
            "event": kind + "_hold_release_action",
            "request_id": record.execution_identity().request_id,
            "task_id": prepared.request.task_id, "operation_id": prepared.request.operation_id,
            "operation_hash": prepared.prepared_operation_hash,
            "entity_id": prepared.request.entity_id,
            "provider": self.provider_name, "fallback": "none",
            "operation_class": "typed_" + kind + "_hold_release",
            "authority_kind": "home_assistant_admin_ingress",
            "authority_hash": release["authority_hash"],
            "readback_evidence_hash": release["readback_hash"],
            "readback_matches_requested_state": release["readback_matches_requested_state"],
            "hold_key": release["hold_key"], "hold_generation": release["hold_generation"],
            "outcome": outcome, "terminal_outcome": record.normalized_outcome,
            "dispatch": "none", "redispatch_prohibited": True,
            "timestamp": release["released_at"],
        }
        entry["audit_event_id"] = digest(entry)
        try:
            if audit.write_batch([entry]) != 1:
                self.executions.audit_projection_failures += 1
        except Exception:
            self.executions.audit_projection_failures += 1

    async def reconcile(self, task_id):
        prepared = self.load(task_id)
        if prepared is None:
            return None
        if task_id in self.active:
            return self.receipt(task_id)
        record = self.executions.get(task_id)
        if record is None:
            # A declaration can be visible before its executor claims ownership
            # in another process. Defer while its original authorization window
            # could still be valid. Once expired, no fresh dispatch can consume
            # that request, so cancellation cannot interrupt authorized work.
            if prepared.request.is_fresh(self.now()):
                return self.receipt(task_id)
            # Claim only to persist cancellation of an ownerless declaration.
            # This path never invokes an adapter or acquires dispatch authority.
            identity = ExecutionIdentity(task_id, None, task_id, uuid.uuid4().hex, uuid.uuid4().hex)
            self.executions.claim(identity=identity, prepared=prepared,
                                  timing=ExecutorTiming(180, 120, 6, 6), now=self.now())
            await self.executor().cancel(task_id)
            return self.receipt(task_id)
        release = self._load_release(task_id)
        if release is not None:
            # An administrator decision exists: only finish it. No executor,
            # adapter or provider path is entered for a released hold.
            self._finish_hold_release(task_id, release)
            return self.receipt(task_id)
        self.locks.reconcile_terminal_hold(record)
        if not record.terminal and parse_timestamp(record.claim_expires_at, field_name="claim_expires_at") > self.now():
            return self.receipt(task_id)
        executor = self.executor()
        if not record.terminal and record.dispatch_intent is None:
            await executor.cancel(task_id)
        identity = ExecutionIdentity(task_id, None, record.execution_identity().attempt_id,
                                     record.execution_identity().request_id, uuid.uuid4().hex)
        async def refuse():
            raise self.refusal("fan_recovery_is_read_only")
        await executor.execute(adapter=self.adapter, prepared=prepared, identity=identity,
                               approval_consumption=refuse, dispatch_authority=self.core)
        return self.receipt(task_id)

    def _hold_settled(self, record, owned):
        try:
            release = self._load_release(record.execution_identity().task_id)
        except FanRefusal:
            return False  # an invalid sidecar is surfaced as a recovery failure
        if release is None:
            return self.locks.hold_settled(record, owned)
        return release["released_at"] is not None and not owned

    async def recover_once(self):
        # Bounded sweep; an active owner wins. Observation cannot turn a saved
        # declaration into fresh ordinary authorization.
        candidates = []
        locks_by_task = {}
        for item in self.locks.records():
            locks_by_task.setdefault(item.task_id, []).append(item)
        for path in sorted(self.root.glob("*.json"))[:MAX_RECORDS]:
            record = self.executions.get(path.stem)
            if (record is None or not record.terminal
                    or (record.normalized_outcome == "manual_review_required"
                        and not self._hold_settled(record, locks_by_task.get(path.stem, [])))):
                candidates.append(path.stem)
        try:
            async with asyncio.timeout(45):
                for task_id in candidates[:8]:
                    try:
                        await self.reconcile(task_id)
                    except Exception:
                        self.recovery_failures += 1
        except TimeoutError:
            self.recovery_failures += 1

    async def close(self):
        tasks = list(self.active.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def health(self):
        records = self.executions.list()
        tasks = {item.execution_identity().task_id for item in records}
        owned = [item for item in self.locks.records() if item.task_id in tasks]
        holds = [item for item in owned if item.conflict_hold]
        return {"configured": True, "active_executions": len(self.active),
                "nonterminal_tasks": sum(not item.terminal for item in records),
                "recovery_failures": self.recovery_failures, "fallback_count": 0,
                "receipt_count": len(list(self.root.glob("*.json"))),
                "receipt_capacity": MAX_RECORDS,
                "audit_projection_failures": self.executions.audit_projection_failures,
                "retained_lock_count": len(owned), "conflict_hold_count": len(holds),
                "conflict_hold_task_count": len({item.task_id for item in holds})}


class FanRuntime:
    service = None

    def require(self):
        if self.service is None:
            raise FanRefusal("fan_service_unavailable")
        return self.service

    def configure(self, settings, core_runtime, read_gateway, *, audit=None):
        from ..providers.upstream_fan import FanProvider
        self.service = FanService(settings.governance_path,
                                  FanProvider.configured(settings, read_gateway),
                                  FanCoreAuthority(core_runtime), audit=audit)


FAN_OPERATIONS = FanRuntime()
