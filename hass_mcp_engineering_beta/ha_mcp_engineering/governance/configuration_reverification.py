"""Dated, supplementary configuration verification without execution authority."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import re
import time
import uuid

from ..clients.single_read import SINGLE_READ
from ..f3.locks import DurableLockStore, DurableLockError
from ..f3.models import LockOwner, LockHandle, LockToken, LockTiming
from ..ha_core_readmission.routes import f3_requirements
from ..request_context import begin_request, current_telemetry, current_caller_id, end_request
from ..version import SERVER_VERSION, BUILD_SHA
from .models import ApprovalState
from .task_models import ExecutionTaskState
from .resources import compare_resource_verification, resource_identity_matches
from .reverification_storage import (
    ReverificationStore, ReceiptStorageError, ReceiptBusy, MAX_EVENTS, fingerprint,
)


DEADLINE_SECONDS = 30
TIMING = LockTiming(60, 20, 0)
HEX_ID = re.compile(r"^[0-9a-f]{32}$")
HASH = re.compile(r"^[0-9a-f]{64}$")
MAX_OPERATIONS = 8


class ReverificationRefused(RuntimeError):
    pass


def validate_binding(task_id, expected_plan_hash, request_id):
    try:
        valid = (isinstance(task_id, str) and HEX_ID.fullmatch(task_id)
                 and isinstance(expected_plan_hash, str) and HASH.fullmatch(expected_plan_hash)
                 and isinstance(request_id, str) and len(request_id) == 36
                 and str(uuid.UUID(request_id)) == request_id)
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ReverificationRefused("invalid_request")
    return {"task_id": task_id, "expected_plan_hash": expected_plan_hash,
            "request_id": request_id}


def _result(status, **extra):
    return {"status": status, "review_resolved": False,
            "receipt_persisted": False, "replayed": False,
            "configuration_mutations": 0, "device_commands": 0,
            "approval_consumptions": 0, "redispatches": 0,
            "fallback": "none", **extra}


class ConfigurationReverification:
    def __init__(self, runtime, root):
        self.runtime, self.service = runtime, runtime.service
        self.store = ReverificationStore(root)
        self.root = root

    def _audit(self, body):
        audit = self.service.audit
        if audit is None or not audit.enabled:
            raise ReverificationRefused("audit_unavailable")
        entry = {
            "event": "configuration_reverification_observation",
            "audit_event_id": fingerprint({"model": "reverification-audit-v1", "body": body}),
            "tool_name": "reverify_configuration_task",
            "request_id": body["binding"]["request_id"],
            "task_id": body["binding"]["task_id"],
            "plan_hash": body["binding"]["expected_plan_hash"],
            "result_status": body["status"], "caller_id": current_caller_id(),
            "receipt_commit": "not_yet_committed",
            "configuration_mutations": 0, "redispatches": 0,
        }
        if audit.write(entry) is not True:
            raise ReverificationRefused("audit_unavailable")
        return entry["audit_event_id"]

    @staticmethod
    def _owner(binding, plan_id):
        identity = uuid.UUID(binding["request_id"]).hex
        return LockOwner("reverify-" + identity, "reverify-" + identity,
                         plan_id, "configuration_reverification", identity)

    def _cleanup(self, start, locks):
        """Only own read locks, while exclusive receipt ownership is held.

        No other reader can still run after acquiring this namespace's flock.
        Recovering these observation-only locks never authorizes an HA call.
        Existing execution owners and conflict holds are not releasable here.
        """
        owner = self._owner(start["binding"], start["plan_id"])
        selected = [r for r in locks.records() if r.owner_id == owner.owner_id]
        if not selected:
            return
        requests = start["lock_requests"]
        expected = [(r["key"], r["mode"], tuple(r["scopes"]), tuple(r["reason_codes"]))
                    for r in requests]
        selected.sort(key=lambda r: r.key.encode("utf-8"))
        observed = [(r.key, r.mode, r.scopes, r.evidence_references) for r in selected]
        if (expected != observed or any(r.conflict_hold for r in selected)
                or any(not locks._same_owner(r, owner) for r in selected)
                or len({r.acquired_at for r in selected}) != 1
                or len({r.lease_expires_at for r in selected}) != 1):
            raise ReverificationRefused("reverification_lock_recovery_required")
        handle = LockHandle(owner, tuple(LockToken(r.key, r.generation, r.mode) for r in selected),
                            selected[0].acquired_at, selected[0].lease_expires_at, TIMING)
        locks.release(handle, pre_dispatch_cleanup=True)

    def _recover_pending(self, transaction, locks):
        if not transaction.events or transaction.events[-1]["kind"] != "started":
            return
        start = transaction.events[-1]["body"]
        self._cleanup(start, locks)
        body = _result("interrupted_read", binding=start["binding"],
                       plan_id=start["plan_id"], checked_at=self.service.now().isoformat(),
                       explanation="No new read was attempted; use a new request ID.")
        body["audit_event_id"] = self._audit(body)
        transaction.append("finished", body)

    async def _history(self, binding):
        service, runtime = self.service, self.runtime
        task = service._load_task(binding["task_id"])
        plan = service._load(task.plan_id)
        if (task.state != ExecutionTaskState.MANUAL_REVIEW_REQUIRED
                or task.legacy_projection.get("execution_authority") != "f3_child_sequence"
                or plan.contract_version not in {1, 2}
                or task.plan_hash != binding["expected_plan_hash"]
                or service.plan_hash(plan) != task.plan_hash
                or task.idempotency_key != service._task_idempotency_key(plan, task.plan_hash)
                or task.approval_reference.get("approval_state") != "consumed"
                or task.approval_reference.get("bound_plan_hash") != task.plan_hash
                or plan.approval.state != ApprovalState.CONSUMED
                or plan.approval.bound_plan_hash != task.plan_hash
                or not plan.approval.consumed_at):
            raise ReverificationRefused("unsupported_history")
        declarations, records = runtime._validate_sequence_state(task)
        if not 1 <= len(records) <= MAX_OPERATIONS:
            raise ReverificationRefused("unsupported_history")
        if not any(r is not None and r.normalized_outcome == "verification_mismatch" for r in records):
            raise ReverificationRefused("unsupported_history")
        prepared, requests = await runtime._load_prepared(plan, task)
        for declaration, record, operation in zip(declarations, records, prepared, strict=True):
            if (record is None or not record.terminal or record.dispatch_count != 1
                    or record.normalized_outcome not in {"succeeded_verified", "verification_mismatch"}
                    or record.identity.get("task_id") != declaration["child_id"]
                    or record.identity.get("plan_id") != plan.plan_id
                    or record.identity.get("attempt_id") != declaration["attempt_id"]
                    or record.operation != declaration["capability_id"]
                    or record.target != {"target_type": declaration["target_type"], "target_id": declaration["target_id"]}
                    or declaration["plan_hash"] != task.plan_hash):
                raise ReverificationRefused("unsupported_history")
            runtime._require_readback_binding(plan=plan, declaration=declaration,
                                             operation=operation, complete_requests=requests,
                                             record=record)
        identity = fingerprint({"plan": plan.to_dict(), "task": task.to_dict(),
                                "declarations": declarations,
                                "records": [r.to_dict() for r in records]})
        return task, plan, prepared, requests, identity

    def projection(self, task):
        try:
            events = self.store.read()
            matching = [e for e in events if e["body"]["binding"]["task_id"] == task.task_id]
            if not matching:
                return None
            event = matching[-1]
            body = event["body"]
            valid = body["binding"]["expected_plan_hash"] == task.plan_hash
            finished = event["kind"] == "finished"
            return {
                "model": "configuration-reverification-v1",
                "status": body["status"] if valid and finished else "unresolved",
                "review_resolved": bool(valid and finished and body.get("review_resolved")),
                "request_id": body["binding"]["request_id"],
                "receipt_hash": event["hash"],
                "checked_at": body.get("checked_at") if finished else None,
                "original_outcome_unchanged": True,
                "evidence_scope": "dated_non_atomic_configuration_observation",
                "current_configuration_continuously_verified": False,
            }
        except (ReceiptStorageError, KeyError, TypeError, ValueError):
            return {"status": "evidence_unavailable", "review_resolved": False,
                    "original_outcome_unchanged": True}

    async def run(self, *, task_id, expected_plan_hash, request_id):
        try:
            binding = validate_binding(task_id, expected_plan_hash, request_id)
        except ReverificationRefused:
            return _result("invalid_request")
        # Lazy storage creation: ordinary getters/old releases do not create
        # a receipt namespace or mutate historical records.
        try:
            with self.store.transaction() as transaction:
                # Bind request identity before any cleanup, provider call or reuse.
                previous = [e for e in transaction.events
                            if e["body"]["binding"]["request_id"] == request_id]
                if previous and previous[0]["body"]["binding"] != binding:
                    return _result("request_binding_mismatch")
                locks = DurableLockStore(self.root)
                self._recover_pending(transaction, locks)
                previous = [e for e in transaction.events
                            if e["body"]["binding"]["request_id"] == request_id]
                if previous:
                    event = previous[-1]
                    return {**event["body"], "receipt_hash": event["hash"],
                            "receipt_persisted": True, "replayed": True}
                if len(transaction.events) > MAX_EVENTS - 2:
                    return _result("receipt_capacity_exceeded")
                async with asyncio.timeout(DEADLINE_SECONDS):
                    return await self._read_and_record(binding, transaction, locks)
        except ReceiptBusy:
            return _result("reverification_in_progress")
        except ReceiptStorageError:
            return _result("evidence_persistence_failed")
        except ReverificationRefused as exc:
            return _result(str(exc))
        except TimeoutError:
            return _result("read_timeout")
        except DurableLockError:
            return _result("concurrent_change")
        except Exception:
            # Provider or corrupted-source exceptions can carry private input.
            return _result("history_or_read_unavailable")

    async def _read_and_record(self, binding, transaction, locks):
        task, plan, prepared, requests, history_hash = await self._history(binding)
        runtime = self.runtime
        core = runtime.core_runtime
        if core is None or self.service.audit is None or not self.service.audit.enabled:
            raise ReverificationRefused("read_authority_or_audit_unavailable")
        if runtime._has_active_legacy_conflict(requests):
            raise ReverificationRefused("concurrent_change")
        # Refuse retained locks of these original children even where shared
        # Core locks would otherwise coexist. Never recover them through this API.
        child_ids = set(task.legacy_projection["child_execution_ids"])
        if any(r.task_id in child_ids for r in locks.records()):
            raise ReverificationRefused("original_execution_locks_unresolved")
        start = {"binding": binding, "plan_id": plan.plan_id,
                 "history_hash": history_hash, "started_at": self.service.now().isoformat(),
                 "lock_requests": [asdict(r) for r in requests]}
        transaction.append("started", start)
        owner = self._owner(binding, plan.plan_id)
        handle = None
        authority, commits, request_token = None, None, None
        single_token = None
        telemetry, prior_authorizer = None, None
        rows, status, config_status = [], "read_unavailable", "not_collected"
        object_attempts, check_attempts = 0, 0
        checked_at = None
        started = time.monotonic()
        try:
            handle = locks.acquire_once(requests, owner=owner, timing=TIMING,
                                        now=self.service.now())
            single_token = SINGLE_READ.set(True)
            await core.reconcile_once("configuration_reverification")
            profiles = tuple(sorted({p for op in prepared for p in f3_requirements(op)}))
            authority = core.acquire(profiles, target={"target_type": "configuration_task", "target_id": task.task_id})
            if authority is None:
                raise ReverificationRefused("authority_unavailable")
            commits = core.consume(authority)
            if commits is None:
                raise ReverificationRefused("authority_unavailable")
            telemetry = current_telemetry()
            if telemetry is None:
                telemetry, request_token = begin_request()
            prior_authorizer = telemetry.core_dispatch_authorizer

            def valid():
                if time.monotonic() - started >= DEADLINE_SECONDS:
                    return False
                locks.validate_handle(handle, now=self.service.now())
                return (core.revalidate(authority, commits) is True
                        and (prior_authorizer is None or prior_authorizer() is True))

            telemetry.core_dispatch_authorizer = valid
            for op in prepared:
                if not valid():
                    raise ReverificationRefused("authority_changed")
                gateway = runtime._configuration_adapters[op.capability_identity].gateway
                object_attempts += 1
                observed = await gateway.read(op.resource_type, op.target.target_id)
                comparison = compare_resource_verification(op.resource_type, op.proposed_config(), observed)
                identity_match = (isinstance(observed, dict) and resource_identity_matches(
                    op.resource_type, op.target.target_id, observed))
                rows.append({"operation_id": op.operation_id, "resource_type": op.resource_type,
                             "target_id": op.target.target_id,
                             "identity_match": identity_match,
                             "semantic_match": comparison.semantic_match,
                             "normalization_valid": comparison.normalization_valid,
                             "approved_fingerprint": comparison.normalized_approved_fingerprint,
                             "observed_fingerprint": comparison.normalized_observed_fingerprint,
                             "verification_normalization_version": comparison.verification_normalization_version,
                             "provider": "direct_ha_api", "fallback": "none"})
            if not valid():
                raise ReverificationRefused("authority_changed")
            check_attempts += 1
            check = await gateway.validate_all()
            # Strict projection only: do not retain or sanitize an arbitrary
            # provider error/configuration body into this receipt.
            config_status = ("valid" if isinstance(check, dict) and check.get("result") == "valid"
                             and "errors" in check and check["errors"] is None else "failed")
            fresh = await self._history(binding)
            if fresh[-1] != history_hash or runtime._has_active_legacy_conflict(requests):
                raise ReverificationRefused("concurrent_change")
            if not valid():
                raise ReverificationRefused("authority_changed")
            checked_at = self.service.now().isoformat()
            status = ("current_configuration_verified" if config_status == "valid"
                      and all(r["identity_match"] and r["semantic_match"] and r["normalization_valid"]
                              for r in rows) else "configuration_mismatch")
        except ReverificationRefused as exc:
            status = str(exc)
        except DurableLockError:
            status = "concurrent_change"
        except TimeoutError:
            status = "read_timeout"
        except Exception:
            status = "read_unavailable"
        finally:
            if single_token is not None:
                SINGLE_READ.reset(single_token)
            if telemetry is not None:
                telemetry.core_dispatch_authorizer = prior_authorizer
            if request_token is not None:
                end_request(request_token)
            try:
                if commits is not None:
                    if core.finish(commits) is not True:
                        status = "authority_cleanup_failed"
                elif authority is not None:
                    core.release(authority)
            except Exception:
                status = "authority_cleanup_failed"
            finally:
                # Includes cancellation and acquisition responses lost after commit.
                # Exact recovery is possible only under this receipt transaction.
                self._cleanup(start, locks)
        body = _result(status, binding=binding, plan_id=plan.plan_id,
                       review_resolved=status == "current_configuration_verified",
                       history_hash=history_hash, operations=rows,
                       configuration_check=config_status,
                       started_at=start["started_at"], checked_at=checked_at or self.service.now().isoformat(),
                       provider="direct_ha_api" if object_attempts or check_attempts else None,
                       object_read_attempts=object_attempts,
                       configuration_check_attempts=check_attempts,
                       attempt_count_scope="application_gateway_calls_not_wire_measurement",
                       identity_reconciliation="separate_current_core_observation",
                       non_atomic=True,
                       evidence_scope="dated_configuration_observation",
                       original_outcome_unchanged=True,
                       verifier={"version": SERVER_VERSION, "build_sha": BUILD_SHA},
                       core_version=getattr(authority, "core_version", None),
                       core_generation=getattr(authority, "generation", None),
                       core_observation_fingerprint=getattr(authority, "observation_fingerprint", None))
        try:
            body["audit_event_id"] = self._audit(body)
            event = transaction.append("finished", body)
        except (ReverificationRefused, ReceiptStorageError) as exc:
            return {**body, "observed_result": status,
                    "status": "audit_unavailable" if isinstance(exc, ReverificationRefused)
                    else "evidence_persistence_failed",
                    "review_resolved": False, "receipt_persisted": False,
                    "receipt_commit_unknown": self.store.commit_unknown}
        return {**body, "receipt_hash": event["hash"], "receipt_persisted": True}
