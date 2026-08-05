"""Durable-lock atomicity, leases, fencing, and recovery tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
import os
from pathlib import Path
import queue
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.f3.contracts import (  # noqa: E402
    LockMode,
    LockRequest,
    LockScope,
)
from ha_mcp_engineering.f3.locks import (  # noqa: E402
    LOCK_TEMP_PREFIX,
    ORPHAN_TEMP_RETENTION_SECONDS,
    DurableLockStore,
    LockConflict,
    LockLeaseExpired,
    LockOwnershipError,
    LockRecordCorrupt,
    LockStorageError,
    LockWaitCancelled,
    LockWaitTimeout,
    StaleRecoveryAction,
    StaleRecoveryDecision,
    normalize_lock_requests,
)
from ha_mcp_engineering.f3.models import (  # noqa: E402
    LOCK_STATE_SCHEMA_VERSION,
    LockHandle,
    LockOwner,
    LockTiming,
    LockToken,
)


def _request(
    key: str,
    mode: LockMode = LockMode.EXCLUSIVE,
    *,
    scope: LockScope = LockScope.RESOURCE,
    reason: str = "target_mutation",
) -> LockRequest:
    return LockRequest(
        key=key,
        scopes=(scope,),
        mode=mode,
        reason_codes=(reason,),
    )


def _owner(name: str, *, task: str | None = None) -> LockOwner:
    return LockOwner(
        owner_id=name,
        task_id=task or f"task-{name}",
        plan_id=f"plan-{name}",
        operation_id="update_dashboard",
        attempt_id=f"attempt-{name}",
    )


TIMING = LockTiming(
    lease_seconds=60,
    renewal_interval_seconds=10,
    wait_timeout_seconds=0,
)


class FakeClock:
    def __init__(self) -> None:
        self.wall = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
        self.monotonic_value = 0.0

    def now(self) -> datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.monotonic_value

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.monotonic_value += seconds

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)


def _process_acquire(
    root: str,
    owner_name: str,
    start: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    store = DurableLockStore(root)
    start.wait(10)
    try:
        handle = store.acquire_once(
            (_request("dashboard:overview"),),
            owner=_owner(owner_name),
            timing=TIMING,
            now=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
        )
        results.put((owner_name, "acquired"))
        release.wait(10)
        store.release(handle)
    except LockConflict:
        results.put((owner_name, "conflict"))


class LockNormalizationTests(unittest.TestCase):
    def test_duplicate_key_unions_evidence_and_exclusive_dominates(self):
        normalized = normalize_lock_requests(
            (
                _request(
                    "addon:ha_mcp",
                    LockMode.SHARED,
                    scope=LockScope.PROVIDER,
                    reason="provider_dependency",
                ),
                _request(
                    "addon:ha_mcp",
                    LockMode.EXCLUSIVE,
                    reason="addon_mutation",
                ),
            )
        )
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0].mode, "exclusive")
        self.assertEqual(normalized[0].scopes, ("provider", "resource"))
        self.assertEqual(
            normalized[0].reason_codes,
            ("addon_mutation", "provider_dependency"),
        )

    def test_requests_are_bytewise_sorted(self):
        normalized = normalize_lock_requests(
            (
                _request("reload:zha"),
                _request("addon:ha_mcp"),
                _request("dashboard:overview"),
            )
        )
        self.assertEqual(
            tuple(item.key for item in normalized),
            ("addon:ha_mcp", "dashboard:overview", "reload:zha"),
        )

    def test_invalid_uppercase_and_malformed_keys_are_rejected(self):
        for key in (
            "Dashboard:overview",
            "dashboard:OverView",
            "dashboard:",
            "dashboard:bad/path",
            "dashboard:one:two",
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                normalize_lock_requests((_request(key),))

    def test_missing_scope_or_reason_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_lock_requests(
                (
                    LockRequest(
                        key="dashboard:overview",
                        scopes=(),
                        mode=LockMode.EXCLUSIVE,
                        reason_codes=("target_mutation",),
                    ),
                )
            )
        with self.assertRaises(ValueError):
            normalize_lock_requests(
                (
                    LockRequest(
                        key="dashboard:overview",
                        scopes=(LockScope.RESOURCE,),
                        mode=LockMode.EXCLUSIVE,
                        reason_codes=(),
                    ),
                )
            )


class DurableLockStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = DurableLockStore(self.temporary.name)
        self.clock = FakeClock()

    def acquire(
        self,
        owner: LockOwner,
        *requests: LockRequest,
        timing: LockTiming = TIMING,
    ) -> LockHandle:
        return self.store.acquire_once(
            requests,
            owner=owner,
            timing=timing,
            now=self.clock.now(),
        )

    def test_exclusive_conflicts_with_exclusive(self):
        self.acquire(_owner("a"), _request("dashboard:overview"))
        with self.assertRaises(LockConflict):
            self.acquire(_owner("b"), _request("dashboard:overview"))

    def test_exclusive_conflicts_with_shared_in_both_directions(self):
        self.acquire(
            _owner("a"),
            _request("addon:ha_mcp", LockMode.SHARED),
        )
        with self.assertRaises(LockConflict):
            self.acquire(
                _owner("b"),
                _request("addon:ha_mcp", LockMode.EXCLUSIVE),
            )
        other = DurableLockStore(Path(self.temporary.name) / "reverse")
        other.acquire_once(
            (_request("addon:ha_mcp", LockMode.EXCLUSIVE),),
            owner=_owner("a"),
            timing=TIMING,
            now=self.clock.now(),
        )
        with self.assertRaises(LockConflict):
            other.acquire_once(
                (_request("addon:ha_mcp", LockMode.SHARED),),
                owner=_owner("b"),
                timing=TIMING,
                now=self.clock.now(),
            )

    def test_shared_holders_are_compatible(self):
        first = self.acquire(
            _owner("a"), _request("addon:ha_mcp", LockMode.SHARED)
        )
        second = self.acquire(
            _owner("b"), _request("addon:ha_mcp", LockMode.SHARED)
        )
        self.assertNotEqual(
            first.tokens[0].generation, second.tokens[0].generation
        )
        self.assertEqual(len(self.store.records()), 2)

    def test_same_owner_exact_retry_is_idempotent(self):
        owner = _owner("a")
        request = _request("dashboard:overview")
        first = self.acquire(owner, request)
        second = self.acquire(owner, request)
        self.assertEqual(first.tokens, second.tokens)
        self.assertEqual(len(self.store.records()), 1)

    def test_same_owner_cannot_change_its_lock_set(self):
        owner = _owner("a")
        self.acquire(owner, _request("dashboard:overview"))
        with self.assertRaises(LockOwnershipError):
            self.acquire(owner, _request("dashboard:other"))

    def test_multi_lock_acquisition_is_all_or_nothing(self):
        self.acquire(_owner("a"), _request("dashboard:blocked"))
        with self.assertRaises(LockConflict):
            self.acquire(
                _owner("b"),
                _request("dashboard:free"),
                _request("dashboard:blocked"),
            )
        self.assertNotIn(
            "dashboard:free", {item.key for item in self.store.records()}
        )

    def test_opposing_request_order_uses_one_canonical_order(self):
        first = self.acquire(
            _owner("a"),
            _request("dashboard:b"),
            _request("dashboard:a"),
        )
        self.assertEqual(
            tuple(item.key for item in first.tokens),
            ("dashboard:a", "dashboard:b"),
        )
        with self.assertRaises(LockConflict):
            self.acquire(
                _owner("b"),
                _request("dashboard:a"),
                _request("dashboard:b"),
            )

    def test_release_is_reverse_canonical_order(self):
        handle = self.acquire(
            _owner("a"),
            _request("dashboard:b"),
            _request("dashboard:a"),
        )
        self.assertEqual(
            self.store.release(handle),
            ("dashboard:b", "dashboard:a"),
        )

    def test_separate_dashboard_keys_can_coexist(self):
        self.acquire(_owner("a"), _request("dashboard:one"))
        self.acquire(_owner("b"), _request("dashboard:two"))
        self.assertEqual(len(self.store.records()), 2)

    def test_provider_and_resource_scopes_coexist_in_one_set(self):
        handle = self.acquire(
            _owner("a"),
            _request("dashboard:overview"),
            _request(
                "addon:ha_mcp",
                LockMode.SHARED,
                scope=LockScope.PROVIDER,
                reason="provider_dependency",
            ),
        )
        self.assertEqual(len(handle.tokens), 2)

    def test_renewal_extends_every_lease_atomically(self):
        handle = self.acquire(
            _owner("a"),
            _request("dashboard:a"),
            _request("dashboard:b"),
        )
        self.clock.advance(20)
        renewed = self.store.renew(handle, now=self.clock.now())
        self.assertGreater(renewed.lease_expires_at, handle.lease_expires_at)
        self.assertEqual(
            {item.lease_expires_at for item in self.store.records()},
            {renewed.lease_expires_at},
        )

    def test_renewal_after_expiration_fails(self):
        handle = self.acquire(_owner("a"), _request("dashboard:a"))
        self.clock.advance(60)
        with self.assertRaises(LockLeaseExpired):
            self.store.renew(handle, now=self.clock.now())

    def test_owner_task_and_fencing_mismatch_cannot_release(self):
        handle = self.acquire(_owner("a"), _request("dashboard:a"))
        wrong_owner = replace(
            handle,
            owner=replace(handle.owner, owner_id="owner-other"),
        )
        wrong_task = replace(
            handle,
            owner=replace(handle.owner, task_id="task-other"),
        )
        wrong_token = replace(
            handle,
            tokens=(
                LockToken(
                    handle.tokens[0].key,
                    handle.tokens[0].generation + 1,
                    handle.tokens[0].mode,
                ),
            ),
        )
        for candidate in (wrong_owner, wrong_task, wrong_token):
            with self.subTest(candidate=candidate), self.assertRaises(
                LockOwnershipError
            ):
                self.store.release(candidate)
        self.assertEqual(len(self.store.records()), 1)

    def test_expired_lock_requires_explicit_stale_recovery(self):
        handle = self.acquire(_owner("a"), _request("dashboard:a"))
        self.clock.advance(61)
        with self.assertRaises(LockConflict):
            self.acquire(_owner("b"), _request("dashboard:a"))
        result = self.store.recover_expired(
            {
                (handle.tokens[0].key, handle.tokens[0].generation):
                StaleRecoveryDecision(
                    StaleRecoveryAction.RELEASE,
                    "terminal_task_safe_release",
                )
            },
            now=self.clock.now(),
        )
        self.assertEqual(result.released, handle.tokens)
        self.acquire(_owner("b"), _request("dashboard:a"))

    def test_unresolved_stale_lock_becomes_conflict_hold(self):
        handle = self.acquire(_owner("a"), _request("dashboard:a"))
        self.clock.advance(61)
        result = self.store.recover_expired(
            {
                (handle.tokens[0].key, handle.tokens[0].generation):
                StaleRecoveryDecision(
                    StaleRecoveryAction.CONFLICT_HOLD,
                    "possibly_dispatched_unresolved",
                )
            },
            now=self.clock.now(),
        )
        self.assertEqual(result.held, handle.tokens)
        self.assertTrue(self.store.records()[0].conflict_hold)
        with self.assertRaises(LockConflict):
            self.acquire(_owner("b"), _request("dashboard:a"))

    def test_expired_same_task_can_transfer_for_observation_with_new_fence(self):
        old_owner = _owner("a", task="task-shared")
        handle = self.acquire(old_owner, _request("dashboard:a"))
        self.clock.advance(61)
        new_owner = replace(old_owner, owner_id="owner-recovery")
        result = self.store.recover_expired(
            {
                (handle.tokens[0].key, handle.tokens[0].generation):
                StaleRecoveryDecision(
                    StaleRecoveryAction.TRANSFER_FOR_OBSERVATION,
                    "process_reconstruction_observation",
                )
            },
            transfer_owner=new_owner,
            transfer_timing=TIMING,
            now=self.clock.now(),
        )
        transferred = result.transferred_handle
        self.assertIsNotNone(transferred)
        assert transferred is not None
        self.assertGreater(
            transferred.tokens[0].generation, handle.tokens[0].generation
        )
        with self.assertRaises(LockOwnershipError):
            self.store.release(handle)
        self.store.validate_handle(transferred, now=self.clock.now())

    def test_unexpired_lock_survives_new_store_instance(self):
        handle = self.acquire(_owner("a"), _request("dashboard:a"))
        restarted = DurableLockStore(self.temporary.name)
        restarted.validate_handle(handle, now=self.clock.now())
        with self.assertRaises(LockConflict):
            restarted.acquire_once(
                (_request("dashboard:a"),),
                owner=_owner("b"),
                timing=TIMING,
                now=self.clock.now(),
            )

    def test_wait_timeout_uses_injected_monotonic_clock(self):
        self.acquire(_owner("a"), _request("dashboard:a"))
        timing = LockTiming(60, 10, 0.15, 0.05)
        with self.assertRaises(LockWaitTimeout):
            asyncio.run(
                self.store.acquire(
                    (_request("dashboard:a"),),
                    owner=_owner("b"),
                    timing=timing,
                    now=self.clock.now,
                    monotonic=self.clock.monotonic,
                    sleep=self.clock.sleep,
                )
            )
        self.assertAlmostEqual(self.clock.monotonic(), 0.15)

    def test_wait_cancellation_is_pre_acquisition(self):
        with self.assertRaises(LockWaitCancelled):
            asyncio.run(
                self.store.acquire(
                    (_request("dashboard:a"),),
                    owner=_owner("a"),
                    timing=TIMING,
                    cancelled=lambda: True,
                )
            )
        self.assertEqual(self.store.records(), ())

    def test_atomic_write_failure_leaves_no_partial_multi_lock_set(self):
        def fail(stage: str) -> None:
            if stage == "before_state_replace":
                raise OSError("synthetic write failure")

        store = DurableLockStore(self.temporary.name, fault_hook=fail)
        with self.assertRaises(LockStorageError):
            store.acquire_once(
                (_request("dashboard:a"), _request("dashboard:b")),
                owner=_owner("a"),
                timing=TIMING,
                now=self.clock.now(),
            )
        clean = DurableLockStore(self.temporary.name)
        self.assertEqual(clean.records(), ())

    def test_storage_read_failure_fails_closed(self):
        def fail(stage: str) -> None:
            if stage == "before_state_read":
                raise OSError("synthetic read failure")

        store = DurableLockStore(self.temporary.name, fault_hook=fail)
        with self.assertRaises(LockStorageError):
            store.records()

    def test_corrupt_and_unknown_schema_records_fail_closed(self):
        self.store.state_path.write_text("{", encoding="utf-8")
        with self.assertRaises(LockRecordCorrupt):
            self.store.records()
        self.store.state_path.write_text(
            json.dumps(
                {
                    "schema_version": LOCK_STATE_SCHEMA_VERSION + 1,
                    "next_generation": 1,
                    "records": [],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(LockRecordCorrupt):
            self.store.records()

    def test_cleanup_removes_only_old_orphan_temporary_files(self):
        old = self.store.root / f"{LOCK_TEMP_PREFIX}old"
        fresh = self.store.root / f"{LOCK_TEMP_PREFIX}fresh"
        old.write_text("old", encoding="utf-8")
        fresh.write_text("fresh", encoding="utf-8")
        now_epoch = time.time()
        os.utime(
            old,
            (
                now_epoch - ORPHAN_TEMP_RETENTION_SECONDS - 1,
                now_epoch - ORPHAN_TEMP_RETENTION_SECONDS - 1,
            ),
        )
        self.assertEqual(
            self.store.cleanup_orphaned_temporary_files(
                now_epoch=now_epoch
            ),
            1,
        )
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_cross_process_exclusive_acquisition_has_one_winner(self):
        context = multiprocessing.get_context("fork")
        start = context.Event()
        release = context.Event()
        results = context.Queue()
        processes = [
            context.Process(
                target=_process_acquire,
                args=(
                    self.temporary.name,
                    name,
                    start,
                    release,
                    results,
                ),
            )
            for name in ("process-a", "process-b")
        ]
        for process in processes:
            process.start()
        start.set()
        observed = []
        try:
            for _ in processes:
                observed.append(results.get(timeout=10))
        except queue.Empty as exc:
            self.fail(f"cross-process lock result timed out: {exc}")
        finally:
            release.set()
            for process in processes:
                process.join(10)
        self.assertEqual(
            sorted(result for _, result in observed),
            ["acquired", "conflict"],
        )
        self.assertTrue(all(process.exitcode == 0 for process in processes))


if __name__ == "__main__":
    unittest.main()
