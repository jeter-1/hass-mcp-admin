"""Durable ordinary receipts with read-only recovery and expiring new IDs.

No governance plan, panel approval, generic service, or implicit restoration is
created. F3's internal approval callback records ordinary request authority;
public receipts explicitly identify that distinction.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile
import uuid

from ..f3.executor import SharedOperationExecutor, PreIntentRetryRequired
from ..f3.models import ExecutionIdentity, ExecutorTiming, LockTiming, parse_timestamp
from ..request_context import current_telemetry
from .adapter import FanAdapter, prepare_record
from .authority import FanCoreAuthority
from .contracts import FAN_CONTRACT, PROVIDER, FanRefusal, FanRequest, digest
from .locks import FanLockStore
from .audit import FanExecutionRepository

MAX_RECORDS = 4096
MAX_ACTIVE = 16


class FanService:
    def __init__(self, root, provider, core, *, now=lambda: datetime.now(timezone.utc), audit=None):
        self.root = Path(root) / "ordinary-fan-v1"
        self.root.mkdir(parents=True, exist_ok=True)
        self.now = now
        self.provider, self.core = provider, core
        self.adapter = FanAdapter(provider, core)
        # Exact shared lock namespace, independent ordinary execution records.
        self.locks = FanLockStore(root, self.load)
        self.executions = FanExecutionRepository(self.root, self.load, audit)
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
            raise FanRefusal("fan_task_id_invalid")
        return self.root / (task_id + ".json")

    def load(self, task_id):
        path = self._path(task_id)
        if not path.exists():
            return None
        if path.stat().st_size > 16_384:
            raise FanRefusal("fan_receipt_corrupt")
        value = json.loads(path.read_bytes())
        if set(value) != {"model", "request", "baseline", "prepared_hash"} or value["model"] != "ordinary-fan-v1":
            raise FanRefusal("fan_receipt_corrupt")
        request = FanRequest.model_validate(value["request"]).checked()
        if request.task_id != task_id:
            raise FanRefusal("fan_receipt_corrupt")
        prepared = prepare_record(request, value["baseline"])
        if prepared.prepared_operation_hash != value["prepared_hash"]:
            raise FanRefusal("fan_receipt_corrupt")
        return prepared

    def save(self, prepared):
        request = prepared.request
        with self.transaction():
            prior = self.load(request.task_id)
            if prior is not None:
                if prior.request != request:
                    raise FanRefusal("fan_operation_id_rebound")
                return prior
            if not request.is_fresh(self.now()):
                raise FanRefusal("fan_operation_id_expired")
            if len(list(self.root.glob("*.json"))) >= MAX_RECORDS:
                raise FanRefusal("fan_receipt_capacity_exhausted")
            value = {"model": "ordinary-fan-v1", "request": request.model_dump(),
                     "baseline": prepared.baseline, "prepared_hash": prepared.prepared_operation_hash}
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
            lock_timing=LockTiming(120, 10, 0),
            executor_timing=ExecutorTiming(180, 120, 6, 6), now=self.now,
        )

    def _authorization(self, request):
        telemetry = current_telemetry()
        binding = digest(request.model_dump())
        def check(*, fresh=True):
            if telemetry is None or telemetry.ordinary_fan_binding != binding:
                raise FanRefusal("fan_connector_authorization_required")
            if fresh and not request.is_fresh(self.now()):
                raise FanRefusal("fan_operation_id_expired")
        check(fresh=False)
        return check

    async def control(self, request):
        telemetry = current_telemetry()
        if telemetry:
            telemetry.audit_context.update(task_id=request.task_id, operation_id=request.operation_id)
        try:
            result = await self._control(request)
        except Exception:
            if telemetry:
                telemetry.audit_context["fan_outcome"] = "request_failed_reconcile_task"
            raise
        if telemetry:
            telemetry.audit_context.update({key: result[key] for key in (
                "task_id", "operation_id", "operation_hash", "provider_attempt_count",
                "dispatch_intent_recorded", "provider_response_received", "terminal")})
            telemetry.audit_context["fan_outcome"] = result["state"]
        return result

    async def _control(self, request):
        request = FanRequest.model_validate(request.model_dump()).checked()
        check = self._authorization(request)
        prior = self.load(request.task_id)
        if prior is not None:
            if prior.request != request:
                raise FanRefusal("fan_operation_id_rebound")
            # A repeated call is reconciliation only, even before durable intent.
            return await self.reconcile(request.task_id)
        check()
        if len(self.active) + self.preparing >= MAX_ACTIVE:
            raise FanRefusal("fan_capacity_exhausted")
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
        task = asyncio.create_task(self._execute(prepared, check), name="ordinary-fan-execution")
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
            "authorization": "authenticated_connector", "provider": PROVIDER,
            "provider_contract": FAN_CONTRACT, "fallback": "none",
            "entity_id": prepared.request.entity_id, "action": prepared.request.action,
            "percentage": prepared.request.percentage,
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
        }

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
        self.locks.reconcile_terminal_hold(record)
        if not record.terminal and parse_timestamp(record.claim_expires_at, field_name="claim_expires_at") > self.now():
            return self.receipt(task_id)
        executor = self.executor()
        if not record.terminal and record.dispatch_intent is None:
            await executor.cancel(task_id)
        identity = ExecutionIdentity(task_id, None, record.execution_identity().attempt_id,
                                     record.execution_identity().request_id, uuid.uuid4().hex)
        async def refuse():
            raise FanRefusal("fan_recovery_is_read_only")
        await executor.execute(adapter=self.adapter, prepared=prepared, identity=identity,
                               approval_consumption=refuse, dispatch_authority=self.core)
        return self.receipt(task_id)

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
                        and not self.locks.hold_settled(record, locks_by_task.get(path.stem, [])))):
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
