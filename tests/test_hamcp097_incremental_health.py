"""HAMCP-097: incremental governance health and truthful startup validation.

Health may reuse a decoded terminal record, its projection and the assembled
aggregate only for byte-identical input (canonical path, inode, size,
mtime_ns, SHA-256 and detector context). These tests prove that every
governance field equals the full projection (a deep audit or a restarted
process) across the plan lifecycle, that the reuse is real and bounded by
active change rather than history, and that a call answered before the
startup historical validation completes says so instead of guessing.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from tests import test_beta11_restart_reconciliation as restart_fixtures
from tests import test_governance as fixtures
from tests.test_2_1a_beta2_operational_lifecycle import LegacyGateway
from tests.test_f3_runtime_integration import (
    _F3LifecycleGateway,
    _UnusedExactConfigurationGateway,
    _provider_identity,
)
from ha_mcp_engineering.audit import AuditLogger
from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration
from ha_mcp_engineering.governance import service as service_module
from ha_mcp_engineering.governance.runtime import GovernanceRuntime
from ha_mcp_engineering.governance.service import ChangeGovernanceService
from ha_mcp_engineering.governance.models import ChangePlan
from ha_mcp_engineering.governance import storage as storage_module
from ha_mcp_engineering.governance import task_storage as task_storage_module
from ha_mcp_engineering.governance.storage import ChangePlanRepository, PlanRecordEvidence
from ha_mcp_engineering.observability import METRICS
from ha_mcp_engineering.request_context import begin_request, end_request


# Process counters and timings: they describe how this call ran, not the
# governed history, and legitimately differ between incremental and full.
_VOLATILE_PATHS = (
    ("plan_store_scaling",),
    ("historical_validation",),
    ("storage", "navigation"),
    ("execution_tasks", "rehydration_attempts"),
    ("execution_tasks", "storage", "navigation"),
    ("execution_tasks", "storage", "rehydration_attempts"),
)
# Events counted since *this* process started (startup restoration, F3 lock
# and child metrics), not derived from records: a restart resets them.
_PROCESS_EVENT_PATHS = (
    ("storage", "quarantine_restoration"),
    ("f3", "lock_conflict_count"),
    ("f3", "stale_lock_recovery_count"),
    ("f3", "duplicate_execution_prevention_count"),
    ("f3", "blind_redispatch_prevention_count"),
    ("f3", "dispatch_intent_failure_count"),
)


def governed(summary, *, restart=False):
    """The summary without per-process counters and timings."""
    result = copy.deepcopy(summary)
    for path in _VOLATILE_PATHS + (_PROCESS_EVENT_PATHS if restart else ()):
        node = result
        for key in path[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(path[-1], None)
    return result


def index_of(summary):
    return summary["plan_store_scaling"]["incremental_health_index"]


def hot(summary):
    return summary["plan_store_scaling"]["hot_paths"]["governance_health"]


class _DifferentialMixin:
    async def full_projection(self):
        """A fresh process over the same records, with a deep audit."""
        service = await self.fresh_service()
        return await service.async_health_summary(deep_storage_audit=True)

    async def assert_incremental_equals_full(self, label, *, restart=True):
        incremental = await self.service.async_health_summary()
        deep = await self.service.async_health_summary(deep_storage_audit=True)
        with self.subTest(step=label, against="deep_audit"):
            self.assertEqual(governed(incremental), governed(deep))
        warm = await self.service.async_health_summary()
        with self.subTest(step=label, against="warm_after_deep"):
            self.assertEqual(governed(warm), governed(deep))
            self.assertEqual(hot(warm)["terminal_plan_records_deserialized"], 0)
            self.assertEqual(hot(warm)["terminal_task_records_deserialized"], 0)
        if restart:
            fresh = await self.full_projection()
            with self.subTest(step=label, against="restart"):
                self.assertEqual(
                    governed(warm, restart=True), governed(fresh, restart=True)
                )
        return warm

    async def outcome(self, fresh=False, **kwargs):
        """The governed summary, or the refusal code; never a partial answer.

        ``fresh`` is a restarted process, whose constructor already projects.
        """
        try:
            service = await self.fresh_service() if fresh else self.service
            summary = await service.async_health_summary(**kwargs)
        except GovernanceError as exc:
            return ("refused", exc.code)
        return ("answered", summary)

    async def assert_outcome_equals_full(self, label):
        """Ordinary, deep and restarted health agree, refusals included.

        The ordinary call runs first, so nothing from the deep audit can
        stand in for an aggregate that should have been invalidated.
        """
        ordinary = await self.outcome()
        deep = await self.outcome(deep_storage_audit=True)
        fresh = await self.outcome(fresh=True, deep_storage_audit=True)
        normal = [
            (kind, governed(value, restart=True) if kind == "answered" else value)
            for kind, value in (ordinary, deep, fresh)
        ]
        with self.subTest(step=label):
            self.assertEqual(normal[0], normal[1])
            self.assertEqual(normal[0], normal[2])
        return ordinary


class _DistinctPlans:
    revision = 0

    async def update_plan(self, proposed=None, **kwargs):
        # Each plan proposes distinct content, so none is a no-op after an apply.
        self.revision += 1
        config = copy.deepcopy(proposed or fixtures.CURRENT)
        config["description"] = f"Revision {self.revision}"
        return await fixtures.GovernanceTestCase.update_plan(self, config, **kwargs)


class LegacyDifferentialTests(
    _DifferentialMixin, _DistinctPlans, fixtures.GovernanceTestCase
):
    async def fresh_service(self):
        return ChangeGovernanceService(
            ChangePlanRepository(self.repository.root),
            self.gateway,
            AuditLogger(str(self.audit_path), "test-access-secret-value"),
            now=self.clock,
            sensitive_values=("test-access-secret-value", "test-ha-token-value"),
        )

    async def reject(self, created):
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        _, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind=pending["approval_kind"],
            csrf_nonce=csrf,
            decision="reject",
            approver_principal="home_assistant_admin_ingress:test-user",
        )

    async def test_lifecycle_steps_equal_full_projection(self):
        await self.assert_incremental_equals_full("empty")
        created = await self.update_plan()
        await self.assert_incremental_equals_full("create")
        await self.externally_approve(created["plan_id"], created["plan_hash"])
        await self.assert_incremental_equals_full("approve")
        await self.service.apply(created["plan_id"])
        await self.assert_incremental_equals_full("apply_success")
        rejected = await self.update_plan()
        await self.reject(rejected)
        rejected_summary = await self.assert_incremental_equals_full("reject")
        self.assertEqual(rejected_summary["rejected_plans"], 1)

        failing, _ = await self.approved_plan()
        self.gateway.fail_write = True
        with self.assertRaises(GovernanceError):
            await self.service.apply(failing["plan_id"])
        self.gateway.fail_write = False
        failed = await self.assert_incremental_equals_full("apply_failure")
        self.assertEqual(failed["failed_apply_count"], 1)

        await self.update_plan(expiration_minutes=5)
        self.clock.advance(minutes=6)
        expired = await self.assert_incremental_equals_full("expire")
        self.assertEqual(expired["expired_plans"], 1)

        requested = await self.service.rollback_change(created["plan_id"])
        await self.assert_incremental_equals_full("rollback_requested")
        await self.externally_approve(created["plan_id"], requested["plan_hash"])
        result = await self.service.rollback_change(
            created["plan_id"], requested["plan_hash"]
        )
        self.assertEqual(result["status"], "rolled_back")
        rolled = await self.assert_incremental_equals_full("rollback")
        self.assertGreater(index_of(rolled)["totals"]["projection_hits"], 0)
        self.assertGreater(index_of(rolled)["totals"]["assembly_reuses"], 0)

        # R1-H1: quarantine-only events change no tracked record proof.
        tasks = self.service.task_repository.quarantine
        tasks.mkdir(parents=True, exist_ok=True)
        task_corrupt = tasks / f"{created['plan_id']}.{'e' * 32}.1.corrupt"
        task_corrupt.write_text("{", encoding="utf-8")
        await self.assert_outcome_equals_full("task_quarantine_added")
        task_corrupt.unlink()
        await self.assert_incremental_equals_full("task_quarantine_removed")

        plans = self.repository.quarantine
        plans.mkdir(parents=True, exist_ok=True)
        (plans / f"{'b' * 32}.1700000000.corrupt").write_text("{", encoding="utf-8")
        await self.assert_incremental_equals_full("plan_quarantine_added")
        retained = self.service.repository.restore_quarantined_records()
        self.assertEqual((retained["restored"], retained["retained"]), (0, 1))
        kept = await self.service.async_health_summary()
        self.assertEqual(index_of(kept)["last_run"]["assembly"], "rebuilt")
        await self.assert_incremental_equals_full("plan_quarantine_retained")

        path = self.repository._path(rejected["plan_id"])
        os.replace(path, plans / f"{rejected['plan_id']}.1700000001.corrupt")
        # No restart here: a restarted process would restore the record itself.
        absent = await self.assert_incremental_equals_full("plan_quarantined", restart=False)
        self.assertEqual(absent["rejected_plans"], 0)
        restored = self.service.repository.restore_quarantined_records()
        self.assertEqual(restored["restored"], 1)
        back = await self.assert_incremental_equals_full("plan_quarantine_restored")
        self.assertEqual(back["rejected_plans"], 1)

        # A tampered terminal record is still detected after all of that.
        data = json.loads(path.read_text(encoding="utf-8"))
        data["policy_decision"]["policy_subject_hash"] = "0" * 64
        path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")),
                        encoding="utf-8")
        tampered = await self.assert_incremental_equals_full("tamper_after_restoration")
        self.assertEqual(tampered["projection_failure_count"], 1)

    async def test_projection_failure_and_tamper_are_reflected_when_warm(self):
        rejected = await self.update_plan()
        await self.reject(rejected)
        created, _ = await self.approved_plan()
        await self.service.apply(created["plan_id"])
        await self.service.async_health_summary()
        warm = await self.service.async_health_summary()
        self.assertEqual(warm["projection_failure_count"], 0)
        self.assertEqual(hot(warm)["terminal_plan_records_deserialized"], 0)

        path = self.repository._path(rejected["plan_id"])
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsNotNone(data.get("policy_decision"))
        data["policy_decision"]["policy_subject_hash"] = "0" * 64
        path.write_text(
            json.dumps(data, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        tampered = await self.service.async_health_summary()
        self.assertEqual(tampered["projection_failure_count"], 1)
        self.assertIn(rejected["plan_id"], json.dumps(tampered["projection_failures"]))
        run = index_of(tampered)["last_run"]
        self.assertEqual(run["projection_misses"], 1)
        self.assertEqual(run["projection_invalidations"], 1)
        self.assertEqual(run["assembly"], "rebuilt")
        self.assertEqual(hot(tampered)["terminal_plan_records_deserialized"], 1)
        await self.assert_incremental_equals_full("projection_failure")

    async def test_same_stat_byte_change_is_never_served_from_the_index(self):
        created, _ = await self.approved_plan()
        await self.service.apply(created["plan_id"])
        await self.service.async_health_summary()
        path = self.repository._path(created["plan_id"])
        raw = path.read_bytes()
        old = b'"title":"Update porch automation"'
        new = b'"title":"Update porch automatioN"'
        self.assertIn(old, raw)
        before = os.stat(path)
        offset = raw.index(old)
        with open(path, "r+b") as handle:
            handle.seek(offset)
            handle.write(new)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = os.stat(path)
        self.assertEqual(
            (before.st_ino, before.st_size, before.st_mtime_ns),
            (after.st_ino, after.st_size, after.st_mtime_ns),
        )
        changed = await self.service.async_health_summary()
        # The digest differs, so the record is decoded and projected again.
        self.assertEqual(hot(changed)["terminal_plan_records_deserialized"], 1)
        self.assertEqual(index_of(changed)["last_run"]["projection_misses"], 1)
        self.assertEqual(index_of(changed)["last_run"]["assembly"], "rebuilt")
        memo = self.repository.health_record_memo.records()
        self.assertEqual(
            [plan.title for plan in memo], ["Update porch automatioN"]
        )
        await self.assert_incremental_equals_full("same_stat_change")

    async def test_corruption_discards_nothing_hidden_and_matches_deep(self):
        created, _ = await self.approved_plan()
        await self.service.apply(created["plan_id"])
        await self.service.async_health_summary()
        bad = self.repository.root / ("a" * 32 + ".json")
        bad.write_text("{bad json", encoding="utf-8")
        # Unchanged from the base: the scan that meets the corrupt record
        # quarantines it and fails closed, and every derived entry is dropped.
        with self.assertRaises(GovernanceError) as raised:
            await self.service.async_health_summary()
        self.assertEqual(raised.exception.code, ErrorCode.CHANGE_PLAN_STORAGE_ERROR)
        self.assertEqual(self.service._health_projection_memo, {})
        self.assertIsNone(self.service._health_assembly_memo)
        self.assertEqual(self.repository.health_record_memo.metrics()["entries"], 0)
        after = await self.service.async_health_summary()
        self.assertEqual(after["storage_corruption_count"], 1)
        self.assertEqual(hot(after)["terminal_plan_records_deserialized"], 1)
        await self.assert_incremental_equals_full("corruption", restart=False)

    async def test_removed_record_is_pruned_from_every_index(self):
        created, _ = await self.approved_plan()
        await self.service.apply(created["plan_id"])
        rejected = await self.update_plan()
        await self.reject(rejected)
        await self.service.async_health_summary()
        path = self.repository._path(rejected["plan_id"])
        held = path.read_bytes()
        path.unlink()
        removed = await self.service.async_health_summary()
        self.assertEqual(removed["total_plans"], 1)
        self.assertEqual(index_of(removed)["projection_entries"], 1)
        self.assertEqual(index_of(removed)["plan_records"]["entries"], 1)
        self.assertEqual(index_of(removed)["last_run"]["projection_invalidations"], 1)
        path.write_bytes(held)
        restored = await self.assert_incremental_equals_full("restored")
        self.assertEqual(restored["total_plans"], 2)

    async def test_index_entries_equal_a_fresh_decode(self):
        created, _ = await self.approved_plan()
        await self.service.apply(created["plan_id"])
        rejected = await self.update_plan()
        await self.reject(rejected)
        for _ in range(3):
            await self.service.async_health_summary()
        memo = self.repository.health_record_memo.records()
        self.assertEqual(len(memo), 2)
        for plan in memo:
            fresh = ChangePlan.from_dict(
                json.loads(self.repository._path(plan.plan_id).read_text("utf-8"))
            )
            self.assertEqual(plan.to_dict(), fresh.to_dict())
        for task in self.service.task_repository.health_record_memo.records():
            self.assertEqual(
                task.to_dict(),
                self.service.task_repository.get(task.task_id).to_dict(),
            )

    async def test_errors_and_deep_audit_discard_the_index(self):
        created, _ = await self.approved_plan()
        await self.service.apply(created["plan_id"])
        await self.service.async_health_summary()
        self.assertEqual(index_of(await self.service.async_health_summary())["projection_entries"], 1)
        discards = self.service._health_incremental_totals["discards"]
        self.service.deep_audit_plan_store()
        self.assertEqual(self.service._health_incremental_totals["discards"], discards + 1)
        self.assertEqual(self.repository.health_record_memo.metrics()["entries"], 0)
        await self.service.async_health_summary()
        with patch.object(
            service_module, "_validate_health_records",
            side_effect=RuntimeError("synthetic detector failure"),
        ):
            with self.assertRaises(RuntimeError):
                await self.service.async_health_summary()
        self.assertEqual(self.service._health_projection_memo, {})
        self.assertIsNone(self.service._health_assembly_memo)
        self.assertEqual(self.repository.health_record_memo.metrics()["entries"], 0)
        await self.assert_incremental_equals_full("after_failure")

    async def test_authorization_never_reads_the_index(self):
        created, _ = await self.approved_plan()
        await self.service.async_health_summary()
        await self.service.async_health_summary()
        with patch.object(
            type(self.repository.health_record_memo), "lookup",
            side_effect=AssertionError("authorization consulted the health index"),
        ):
            result = await self.service.apply(created["plan_id"])
        self.assertEqual(result["status"], "applied")

    async def _two_terminal(self):
        created, _ = await self.approved_plan()
        await self.service.apply(created["plan_id"])
        rejected = await self.update_plan()
        await self.reject(rejected)
        return created, rejected

    # Kills mutant A2 (docs/evidence/hamcp097-mutations).
    async def test_clock_moving_backwards_rebuilds_the_assembly(self):
        await self._two_terminal()
        await self.service.async_health_summary()
        self.assertEqual(
            index_of(await self.service.async_health_summary())["last_run"]["assembly"], "reused")
        self.clock.advance(minutes=-1)
        earlier = await self.service.async_health_summary()
        self.assertEqual(index_of(earlier)["last_run"]["assembly"], "rebuilt")

    # Kills mutant C1 (docs/evidence/hamcp097-mutations).
    async def test_record_memo_counts_hits_and_misses(self):
        await self._two_terminal()
        await self.service.async_health_summary()
        warm = await self.service.async_health_summary()
        records = index_of(warm)["plan_records"]
        self.assertEqual(
            (records["entries"], records["hits"], records["misses"]), (2, 2, 2))

    # Kills mutant C5 (docs/evidence/hamcp097-mutations).
    async def test_warm_projection_replays_classified_outcomes(self):
        rejected = await self.update_plan()
        await self.reject(rejected)
        path = self.repository._path(rejected["plan_id"])
        data = json.loads(path.read_text(encoding="utf-8"))
        data["policy_decision"]["policy_subject_hash"] = "0" * 64
        path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        with patch.object(
            METRICS, "record_classified_outcome", wraps=METRICS.record_classified_outcome
        ) as recorded:
            cold = await self.service.async_health_summary()
            cold_calls = list(recorded.call_args_list)
            recorded.reset_mock()
            warm = await self.service.async_health_summary()
            warm_calls = list(recorded.call_args_list)
        self.assertEqual(cold["projection_failure_count"], 1)
        self.assertEqual(index_of(warm)["last_run"]["projection_hits"], 1)
        self.assertTrue(cold_calls)
        self.assertEqual(warm_calls, cold_calls)


class LegacyScalingTests(_DistinctPlans, fixtures.GovernanceTestCase):
    async def populate(self, count):
        for index in range(count):
            created = await self.update_plan()
            if index % 2:
                await self.externally_approve(created["plan_id"], created["plan_hash"])
                await self.service.apply(created["plan_id"])
            else:
                pending = self.service.approve(created["plan_id"], created["plan_hash"])
                _, csrf = await self.service.issue_external_csrf(
                    created["plan_id"], pending["challenge_id"]
                )
                await self.service.decide_external_approval(
                    plan_id=created["plan_id"],
                    challenge_id=pending["challenge_id"],
                    expected_plan_hash=created["plan_hash"],
                    approval_kind=pending["approval_kind"],
                    csrf_nonce=csrf,
                    decision="reject",
                    approver_principal="home_assistant_admin_ingress:test-user",
                )

    async def measure(self, count):
        await self.populate(count)
        await self.service.async_health_summary()
        warm = await self.service.async_health_summary()
        self.assertEqual(warm["total_plans"], count)
        self.assertEqual(index_of(warm)["last_run"]["projection_hits"], count)
        self.assertEqual(index_of(warm)["last_run"]["projection_misses"], 0)
        self.assertEqual(index_of(warm)["last_run"]["assembly"], "reused")
        return hot(warm)

    async def test_warm_deserializations_do_not_grow_with_history(self):
        per_scale = {}
        for scale in (1, 2, 4):
            with self.subTest(scale=scale):
                await self.asyncTearDown()
                await self.asyncSetUp()
                warm = await self.measure(4 * scale)
                per_scale[scale] = (
                    warm["plan_records_deserialized"],
                    warm["terminal_plan_records_deserialized"],
                    warm["task_records_deserialized"],
                    warm["terminal_task_records_deserialized"],
                )
        self.assertEqual(per_scale, {1: (0, 0, 0, 0), 2: (0, 0, 0, 0), 4: (0, 0, 0, 0)})

    async def test_k_changed_records_cause_exactly_k_reprojections(self):
        await self.populate(8)
        await self.service.async_health_summary()
        baseline = await self.service.async_health_summary()
        paths = sorted(self.repository.root.glob("*.json"))
        self.assertEqual(len(paths), 8)
        for k in (1, 3):
            with self.subTest(k=k):
                for path in paths[:k]:
                    stat = path.stat()
                    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
                changed = await self.service.async_health_summary()
                run = index_of(changed)["last_run"]
                self.assertEqual(run["projection_misses"], k)
                self.assertEqual(run["projection_invalidations"], k)
                self.assertEqual(run["projection_hits"], 8 - k)
                self.assertEqual(hot(changed)["terminal_plan_records_deserialized"], k)
                self.assertEqual(governed(changed), governed(baseline))


class _ControlledValidation:
    """Hold the safety detector in its worker until released."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.original = service_module._validate_health_records

    def __call__(self, plans, sensitive_values):
        self.entered.set()
        if not self.release.wait(5):
            raise AssertionError("test did not release historical validation")
        return self.original(plans, sensitive_values)

    async def wait_entered(self):
        while not self.entered.is_set():
            await asyncio.sleep(0.001)


class HistoricalValidationTests(
    _DifferentialMixin, _DistinctPlans, fixtures.GovernanceTestCase
):
    fresh_service = LegacyDifferentialTests.fresh_service

    async def asyncTearDown(self):
        job = self.service._historical_validation_job
        if job is not None and not job.done():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        await super().asyncTearDown()

    async def history(self):
        created, _ = await self.approved_plan()
        await self.service.apply(created["plan_id"])
        rejected = await self.update_plan()
        await LegacyDifferentialTests.reject(self, rejected)
        # A restarted process: nothing derived, no verdict cached.
        self.service = await self.fresh_service()

    async def test_not_started_before_launch(self):
        summary = await self.service.async_health_summary()
        self.assertEqual(summary["historical_validation"]["state"], "not_started")
        self.assertNotIn("pending_fields", summary)

    async def test_in_progress_call_answers_with_explicit_pending_markers(self):
        await self.history()
        self.service.historical_validation_wait_seconds = 0.05
        controlled = _ControlledValidation()
        with patch.object(service_module, "_validate_health_records", side_effect=controlled):
            job = self.service.start_historical_validation()
            self.assertIs(self.service.start_historical_validation(), job)
            await controlled.wait_entered()
            pending = await self.service.async_health_summary()
            self.assertEqual(pending["status"], "pending")
            self.assertEqual(pending["pending_reason"], "historical_validation_in_progress")
            self.assertEqual(pending["historical_validation"]["state"], "in_progress")
            self.assertEqual(pending["historical_validation"]["writes"], "none")
            self.assertEqual(pending["historical_validation"]["lifecycle_writes"], "none")
            self.assertEqual(pending["historical_validation"]["quarantined_records"],
                             {"plans": 0, "tasks": 0})
            self.assertGreaterEqual(pending["historical_validation"]["waited_ms"], 0.0)
            for field in (
                "total_plans", "projection_failure_count", "projection_failures",
                "plans_by_policy_class", "execution_tasks", "storage",
                "storage_corruption_count", "rejected_plans", "approval_notifications",
            ):
                self.assertIn(field, pending["pending_fields"])
                self.assertIsNone(pending[field])
            live = {"active_apply_operations", "historical_validation", "plan_store_scaling"}
            # Build-time constants, plus the fixed "unavailable" F3 descriptor
            # when no F3 runtime is composed, are reported as-is.
            static = {
                "enabled", "external_approval_enabled", "ingress_approval_ui_configured",
                "approval_authority_version", "projection_failure_category_model", "f3",
            }
            live |= static
            self.assertFalse(live & set(pending["pending_fields"]))
            self.assertEqual(
                pending["plan_store_scaling"]["authorization_source"], "persisted_records"
            )
            controlled.release.set()
            await job
        complete = await self.full_projection()
        # Every field of a complete summary is either pending or process-live.
        self.assertEqual(set(pending["pending_fields"]) | live, set(complete))
        for field in static:
            self.assertEqual(pending[field], complete[field], field)
        state = self.service._historical_validation
        self.assertEqual(state["state"], "complete")
        self.assertEqual(state["plans_validated"], 2)
        self.assertEqual(state["deferred_lifecycle_plans"], 0)
        self.assertTrue(state["assembly_prewarmed"])
        first = await self.service.async_health_summary()
        self.assertEqual(first["historical_validation"]["state"], "complete")
        self.assertNotIn("pending_fields", first)
        self.assertEqual(hot(first)["terminal_plan_records_deserialized"], 0)
        self.assertEqual(index_of(first)["last_run"]["assembly"], "reused")
        self.assertEqual(governed(first), governed(await self.full_projection()))

    async def test_deep_audit_waits_for_validation_then_rescans(self):
        await self.history()
        self.service.historical_validation_wait_seconds = 0.01
        controlled = _ControlledValidation()
        with patch.object(service_module, "_validate_health_records", side_effect=controlled):
            job = self.service.start_historical_validation()
            await controlled.wait_entered()
            deep = asyncio.create_task(
                self.service.async_health_summary(deep_storage_audit=True)
            )
            await asyncio.sleep(0.05)
            self.assertFalse(deep.done())
            controlled.release.set()
            result = await deep
        self.assertTrue(job.done())
        self.assertNotIn("pending_fields", result)
        self.assertEqual(index_of(result)["last_run"]["mode"], "deep_rescan")
        self.assertEqual(hot(result)["terminal_plan_records_deserialized"], 2)
        self.assertEqual(governed(result), governed(await self.full_projection()))

    async def test_validation_never_persists_a_lifecycle_transition(self):
        await self.history()
        await self.update_plan(expiration_minutes=5)
        # Construct before expiry: the constructor's summary would persist it.
        self.service = await self.fresh_service()
        self.clock.advance(minutes=6)
        before = {path.name: path.read_bytes() for path in self.repository.root.glob("*.json")}
        generation = self.service.repository.generation
        await self.service.start_historical_validation()
        after = {path.name: path.read_bytes() for path in self.repository.root.glob("*.json")}
        self.assertEqual(after, before)
        self.assertEqual(self.service.repository.generation, generation)
        state = self.service._historical_validation
        self.assertEqual(state["state"], "complete")
        self.assertEqual(state["deferred_lifecycle_plans"], 1)
        self.assertFalse(state["assembly_prewarmed"])
        self.assertEqual(index_of(await self.service.async_health_summary())["last_run"]["mode"], "incremental")
        summary = await self.assert_incremental_equals_full("deferred_expiry")
        self.assertEqual(summary["expired_plans"], 1)

    # R1-M1 (Codex round 1). Kills mutants M1, M2, M3, M4.
    async def test_startup_quarantine_is_reported_as_a_write(self):
        await self.history()
        task_repository = self.service.task_repository
        cases = (
            ("plans", ErrorCode.CHANGE_PLAN_STORAGE_ERROR,
             self.repository.root / f"{'a' * 32}.json",
             self.service.repository.quarantine),
            ("tasks", ErrorCode.EXECUTION_TASK_STORAGE_ERROR,
             task_repository.root / f"{'a' * 32}.{'f' * 32}.json",
             task_repository.quarantine),
        )
        for kind, code, path, quarantine in cases:
            with self.subTest(kind=kind):
                self.service = await self.fresh_service()
                path.write_text('{"synthetic-corrupt-marker": ', encoding="utf-8")
                await self.service.start_historical_validation()
                self.assertFalse(path.exists())
                self.assertEqual(len(list(quarantine.glob(f"{path.stem}.*.corrupt"))), 1)
                report = self.service._historical_validation_health()
                self.assertEqual(report["state"], "failed")
                self.assertEqual(report["failure_category"], code.value)
                self.assertEqual(report["writes"], "corrupt_record_quarantine")
                self.assertEqual(report["lifecycle_writes"], "none")
                expected = {"plans": 0, "tasks": 0, kind: 1}
                self.assertEqual(report["quarantined_records"], expected)
                summary = await self.service.async_health_summary()
                self.assertEqual(summary["historical_validation"]["quarantined_records"],
                                 expected)
                text = json.dumps(summary["historical_validation"])
                for secret in ("a" * 32, "f" * 32, "synthetic-corrupt-marker",
                               str(self.repository.root), ".corrupt"):
                    self.assertNotIn(secret, text)
                # Counts are frozen at the end of validation: a later health
                # quarantine is not attributed to it.
                later = self.repository.root / f"{'9' * 32}.json"
                later.write_text("{", encoding="utf-8")
                with self.assertRaises(GovernanceError):
                    await self.service.async_health_summary()
                self.assertEqual(
                    self.service._historical_validation_health()["quarantined_records"],
                    expected)

    async def test_clean_startup_reports_no_writes(self):
        await self.history()
        before = {p.name: p.read_bytes() for p in self.repository.root.rglob("*") if p.is_file()}
        await self.service.start_historical_validation()
        after = {p.name: p.read_bytes() for p in self.repository.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        report = (await self.service.async_health_summary())["historical_validation"]
        self.assertEqual(report["state"], "complete")
        self.assertEqual(report["writes"], "none")
        self.assertEqual(report["lifecycle_writes"], "none")
        self.assertEqual(report["quarantined_records"], {"plans": 0, "tasks": 0})

    async def test_failed_validation_is_reported_and_health_still_scans(self):
        await self.history()
        with patch.object(
            service_module, "_validate_health_records",
            side_effect=RuntimeError("synthetic detector failure"),
        ):
            await self.service.start_historical_validation()
        state = self.service._historical_validation
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["failure_category"], "RuntimeError")
        self.assertIsNotNone(state["elapsed_ms"])
        summary = await self.service.async_health_summary()
        self.assertEqual(summary["historical_validation"]["state"], "failed")
        self.assertEqual(hot(summary)["terminal_plan_records_deserialized"], 2)
        self.assertEqual(governed(summary), governed(await self.full_projection()))

    async def test_storage_failure_category_has_no_detail(self):
        await self.history()
        with patch.object(
            self.service.repository, "collect_health",
            side_effect=OSError("/secret/path unreadable"),
        ):
            await self.service.start_historical_validation()
        state = self.service._historical_validation
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["failure_category"], ErrorCode.CHANGE_PLAN_STORAGE_ERROR.value)
        self.assertNotIn("secret", json.dumps(self.service._historical_validation_health()))

    async def test_cancelled_validation_is_reported(self):
        await self.history()
        controlled = _ControlledValidation()
        with patch.object(service_module, "_validate_health_records", side_effect=controlled):
            job = self.service.start_historical_validation()
            await controlled.wait_entered()
            job.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await job
            controlled.release.set()
        self.assertEqual(self.service._historical_validation["state"], "cancelled")
        summary = await self.service.async_health_summary()
        self.assertEqual(summary["historical_validation"]["state"], "cancelled")
        self.assertEqual(governed(summary), governed(await self.full_projection()))

    async def test_runtime_without_service_starts_nothing(self):
        self.assertIsNone(GovernanceRuntime.start_historical_validation(
            type("Stub", (), {"service": None})()
        ))

    # Kills mutant T4, T5 (docs/evidence/hamcp097-mutations).
    async def test_validation_finishing_within_the_budget_answers_in_full(self):
        await self.history()
        self.service.historical_validation_wait_seconds = 5.0
        controlled = _ControlledValidation()
        with patch.object(service_module, "_validate_health_records", side_effect=controlled):
            job = self.service.start_historical_validation()
            await controlled.wait_entered()
            call = asyncio.create_task(self.service.async_health_summary())
            await asyncio.sleep(0.05)
            self.assertFalse(call.done())
            controlled.release.set()
            summary = await call
        self.assertTrue(job.done())
        self.assertNotIn("pending_fields", summary)
        self.assertEqual(summary["historical_validation"]["state"], "complete")
        self.assertGreater(hot(summary)["phase_elapsed_ms"]["historical_validation_wait_ms"], 0.0)
        self.assertEqual(governed(summary), governed(await self.full_projection()))

    # Kills mutant T8 (docs/evidence/hamcp097-mutations).
    async def test_superseded_validation_is_classified_without_detail(self):
        await self.history()
        with patch.object(
            self.service, "_async_health_scan",
            side_effect=GovernanceError(
                ErrorCode.CHANGE_PLAN_STORAGE_ERROR,
                details={"reason": "health_snapshot_superseded"},
            ),
        ):
            await self.service.start_historical_validation()
        state = self.service._historical_validation
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["failure_category"], "superseded")

    # Kills mutant W3 (docs/evidence/hamcp097-mutations).
    async def test_validation_defers_an_expired_challenge_without_writing(self):
        await self.history()
        created = await self.update_plan()
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertIn("challenge_id", pending)
        self.service = await self.fresh_service()
        # Challenge TTL is 60 minutes; the plan itself expires at 120.
        self.clock.advance(minutes=61)
        before = {p.name: p.read_bytes() for p in self.repository.root.glob("*.json")}
        await self.service.start_historical_validation()
        after = {p.name: p.read_bytes() for p in self.repository.root.glob("*.json")}
        self.assertEqual(after, before)
        state = self.service._historical_validation
        self.assertEqual(state["state"], "complete")
        self.assertEqual(state["deferred_lifecycle_plans"], 1)
        self.assertFalse(state["assembly_prewarmed"])
        await self.assert_incremental_equals_full("deferred_challenge_expiry")
        # The next health call, not the validation, persisted the expiry.
        resolved = {p.name: p.read_bytes() for p in self.repository.root.glob("*.json")}
        self.assertNotEqual(resolved, before)


class F3DifferentialTests(_DifferentialMixin, unittest.IsolatedAsyncioTestCase):
    """Operational plans through the composed F3 runtime, including review."""

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "plans"
        self.clock = fixtures.Clock()
        self.lifecycle = _F3LifecycleGateway()
        self.lifecycle.now = self.clock
        self.telemetry, self.context = begin_request("hamcp-097-f3")
        self.telemetry.caller_id = "mcp-requester"
        self.service, self.runtime = self.compose()
        await self.runtime.recover_once("startup")

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    def compose(self):
        service = ChangeGovernanceService(
            ChangePlanRepository(self.root),
            LegacyGateway(),
            AuditLogger(str(Path(self.temp.name) / "audit.jsonl"), "f3-test-secret"),
            now=self.clock,
            lifecycle_gateway=self.lifecycle,
        )
        runtime = F3RuntimeIntegration(
            service=service,
            storage_root=str(self.root),
            configuration_gateway=_UnusedExactConfigurationGateway(),
            backup_gateway=None,
            lifecycle_gateway=self.lifecycle,
            provider_identity_reader=_provider_identity,
            retention_days=90,
        )
        service.f3_runtime = runtime
        return service, runtime

    async def fresh_service(self):
        service, runtime = self.compose()
        await runtime.recover_once("startup")
        return service

    async def grant(self, created, decision="approve"):
        plan = created["plan"]
        pending = self.service.approve(plan["plan_id"], plan["plan_hash"])
        _, csrf = await self.service.issue_external_csrf(
            plan["plan_id"], pending["challenge_id"]
        )
        result = await self.service.decide_external_approval(
            plan_id=plan["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=plan["plan_hash"],
            approval_kind="apply",
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision=decision,
            approver_principal="home_assistant_admin_ingress:hamcp-097",
        )
        self.assertNotEqual(result.get("status"), "approval_pending")
        return plan

    async def test_operational_lifecycle_and_manual_review_equal_full(self):
        verified = await self.grant(await self.service.create_reload_plan(reload_target="automation"))
        result = await self.service.apply(verified["plan_id"], verified["plan_hash"])
        self.assertEqual(result["task_state"], "succeeded_verified")
        await self.assert_incremental_equals_full("operational_apply")

        rejected = await self.grant(
            await self.service.create_reload_plan(reload_target="script"), "reject"
        )
        await self.assert_incremental_equals_full("operational_reject")

        self.lifecycle.mode = "ambiguous"
        review = await self.grant(await self.service.create_reload_plan(reload_target="input_boolean"))
        observing = await self.service.apply(review["plan_id"], review["plan_hash"])
        self.lifecycle.mode = "success"
        self.assertEqual(observing["task_state"], "observing")
        # A restart runs startup recovery on the in-flight task, which changes
        # its recovery counters by design; compare without restarting here.
        await self.assert_incremental_equals_full("observing", restart=False)
        self.clock.advance(seconds=901)
        await self.runtime.recover_once("hamcp_097_review_sweep")
        self.assertEqual(
            self.service.get_execution_task(observing["task_id"])["state"],
            "manual_review_required",
        )
        reviewed = await self.assert_incremental_equals_full("manual_review")
        self.assertEqual(reviewed["execution_tasks"]["tasks_manual_review"], 1)

        path = self.service.repository._path_for_plan(
            self.service.repository.get(rejected["plan_id"])
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        data["policy_decision"]["policy_subject_hash"] = "0" * 64
        path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        failed = await self.assert_incremental_equals_full("operational_projection_failure")
        self.assertEqual(failed["projection_failure_count"], 1)
        self.assertGreater(index_of(failed)["task_records"]["entries"], 0)
        self.assertEqual(hot(failed)["terminal_task_records_deserialized"], 0)

    async def test_quarantine_restoration_is_reflected(self):
        rejected = await self.grant(
            await self.service.create_reload_plan(reload_target="automation"), "reject"
        )
        await self.service.async_health_summary()
        repository = self.service.repository
        path = repository._path_for_plan(repository.get(rejected["plan_id"]))
        quarantine = path.parent / "quarantine"
        quarantine.mkdir(exist_ok=True)
        moved = quarantine / f"{rejected['plan_id']}.1700000000.corrupt"
        os.replace(path, moved)
        absent = await self.service.async_health_summary()
        self.assertEqual(absent["total_plans"], 0)
        self.assertEqual(index_of(absent)["projection_entries"], 0)
        restored = repository.restore_quarantined_records()
        self.assertEqual(restored["restored"], 1)
        self.assertTrue(path.exists())
        back = await self.assert_incremental_equals_full("quarantine_restored")
        self.assertEqual(back["total_plans"], 1)

    async def _verified(self):
        verified = await self.grant(
            await self.service.create_reload_plan(reload_target="automation"))
        result = await self.service.apply(verified["plan_id"], verified["plan_hash"])
        self.assertEqual(result["task_state"], "succeeded_verified")
        return verified, result

    # Kills mutant K3, A4 (docs/evidence/hamcp097-mutations).
    async def test_task_record_change_reprojects_its_plan_and_rebuilds(self):
        verified, result = await self._verified()
        await self.service.async_health_summary()
        warm = await self.service.async_health_summary()
        self.assertEqual(index_of(warm)["last_run"]["assembly"], "reused")
        task_path = self.service.task_repository._path(result["task_id"])
        stat = task_path.stat()
        os.utime(task_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        changed = await self.service.async_health_summary()
        run = index_of(changed)["last_run"]
        self.assertEqual(run["projection_misses"], 1)
        self.assertEqual(run["projection_invalidations"], 1)
        self.assertEqual(run["assembly"], "rebuilt")
        await self.assert_incremental_equals_full("task_touched")

    # R1-H1 with F3 composed: a quarantined ownership file for a verified
    # plan, and no other change.
    async def test_quarantine_only_task_change_agrees_with_full_projection(self):
        verified, _ = await self._verified()
        await self.service.async_health_summary()
        warm = await self.service.async_health_summary()
        self.assertEqual(index_of(warm)["last_run"]["assembly"], "reused")
        quarantine = self.service.task_repository.quarantine
        quarantine.mkdir(parents=True, exist_ok=True)
        corrupt = quarantine / f"{verified['plan_id']}.{'e' * 32}.1.corrupt"
        corrupt.write_text("{", encoding="utf-8")
        ordinary = await self.assert_outcome_equals_full("f3_task_quarantine_only")
        self.assertEqual(ordinary, ("refused", ErrorCode.EXECUTION_TASK_STORAGE_ERROR))
        corrupt.unlink()
        # The refusal latches F3 readiness for this process; a restart's
        # startup recovery clears it, so compare in-process only.
        await self.assert_incremental_equals_full(
            "f3_task_quarantine_removed", restart=False)

    # Kills mutant I9 (docs/evidence/hamcp097-mutations).
    async def test_removed_task_record_is_pruned(self):
        verified, result = await self._verified()
        await self.service.async_health_summary()
        warm = await self.service.async_health_summary()
        self.assertEqual(index_of(warm)["task_records"]["entries"], 1)
        task_path = self.service.task_repository._path(result["task_id"])
        held = task_path.read_bytes()
        task_path.unlink()
        removed = await self.service.async_health_summary()
        self.assertEqual(index_of(removed)["task_records"]["entries"], 0)
        task_path.write_bytes(held)
        await self.assert_incremental_equals_full("task_restored", restart=False)

    # Kills mutant I11 (docs/evidence/hamcp097-mutations).
    async def test_active_task_is_decoded_on_every_call(self):
        self.lifecycle.mode = "ambiguous"
        review = await self.grant(
            await self.service.create_reload_plan(reload_target="input_boolean"))
        observing = await self.service.apply(review["plan_id"], review["plan_hash"])
        self.lifecycle.mode = "success"
        self.assertEqual(observing["task_state"], "observing")
        await self.service.async_health_summary()
        warm = await self.service.async_health_summary()
        self.assertEqual(index_of(warm)["task_records"]["entries"], 0)
        self.assertEqual(hot(warm)["task_records_deserialized"], 1)

    # Kills mutant X2 (docs/evidence/hamcp097-mutations).
    async def test_task_record_change_reevaluates_its_plan_actionability(self):
        verified, result = await self._verified()
        await self.service.async_health_summary()
        await self.service.async_health_summary()
        self.assertIn(verified["plan_id"], self.service._health_actionable_memo)
        task_path = self.service.task_repository._path(result["task_id"])
        stat = task_path.stat()
        os.utime(task_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        with patch.object(self.service, "_approval_is_actionable",
                          wraps=self.service._approval_is_actionable) as evaluate:
            changed = await self.service.async_health_summary()
        self.assertIn(verified["plan_id"],
                      [call.args[0].plan_id for call in evaluate.call_args_list])
        self.assertEqual(index_of(changed)["last_run"]["actionable_hits"], 0)
        await self.assert_incremental_equals_full("task_touched_actionability")

    # Kills mutant X5 (docs/evidence/hamcp097-mutations).
    async def test_terminal_plan_with_an_unproven_task_has_no_actionability_key(self):
        verified, result = await self._verified()
        plan = self.service.repository.get(verified["plan_id"])
        task = self.service.task_repository.get(result["task_id"])
        evidence = {plan.plan_id: PlanRecordEvidence(
            path="plan.json", stat=(1, 2, 3), sha256="0" * 64)}
        proven = ("task.json", (4, 5, 6), "1" * 64)
        keys = self.service._health_actionable_keys(
            [plan], evidence, [task], {task.task_id: proven})
        self.assertIn(plan.plan_id, keys)
        self.assertEqual(self.service._health_actionable_keys(
            [plan], evidence, [task], {task.task_id: None}), {})
        self.assertEqual(self.service._health_actionable_keys(
            [plan], evidence, [task], {}), {})


class ProhibitedActionabilityTests(
    _DifferentialMixin, _DistinctPlans, fixtures.GovernanceTestCase
):
    """Assembly re-evaluates actionability only for changed or quarantined plans.

    Deciding whether a prohibited plan's approval is actionable consults
    ``get_for_plan``: the assembly cost that previously grew with retained
    history whenever any record changed.
    """

    fresh_service = LegacyDifferentialTests.fresh_service

    async def asyncSetUp(self):
        await super().asyncSetUp()
        for revision in range(2):
            proposed = copy.deepcopy(fixtures.CURRENT)
            proposed["action"] = [{"service": "lock.unlock",
                                   "target": {"entity_id": f"lock.hamcp097_{revision}"}}]
            created = await self.update_plan(proposed)
            self.assertEqual(created["policy_decision"]["policy_class"], "prohibited")
        self.paths = {path.stem: path for path in self.repository.root.glob("*.json")}
        self.ids = sorted(self.paths)
        self.assertEqual(len(self.ids), 2)
        for plan_id in self.ids:
            with self.lookups() as lookup:
                self.assertFalse(self.service._approval_is_actionable(
                    self.service.repository.get(plan_id)))
            self.assertEqual([call.args[0] for call in lookup.call_args_list], [plan_id])
        self.consulted, self.other = self.ids
        await self.service.async_health_summary()
        self.warm = await self.service.async_health_summary()
        self.assertEqual(index_of(self.warm)["last_run"]["assembly"], "reused")

    def lookups(self):
        repository = self.service.task_repository
        return patch.object(repository, "get_for_plan", wraps=repository.get_for_plan)

    def touch(self, plan_id):
        stat = self.paths[plan_id].stat()
        os.utime(self.paths[plan_id], ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    async def evaluated(self, **kwargs):
        """Health result, plans evaluated for actionability, storage lookups."""
        service = self.service
        with patch.object(service, "_approval_is_actionable",
                          wraps=service._approval_is_actionable) as evaluate:
            with self.lookups() as lookup:
                result = await service.async_health_summary(**kwargs)
        return (result, [call.args[0].plan_id for call in evaluate.call_args_list],
                [call.args[0] for call in lookup.call_args_list])

    async def test_unchanged_plans_are_not_reevaluated(self):
        self.touch(self.other)
        changed, evaluated, lookups = await self.evaluated()
        self.assertEqual(index_of(changed)["last_run"]["assembly"], "rebuilt")
        self.assertEqual(evaluated, [self.other])
        self.assertNotIn(self.consulted, lookups)
        run = index_of(changed)["last_run"]
        self.assertEqual((run["actionable_hits"], run["actionable_misses"]), (1, 1))
        self.assertEqual(index_of(changed)["actionable_entries"], 2)
        self.assertEqual(changed["plans_by_policy_class"]["prohibited"], 2)
        self.assertEqual(governed(changed), governed(self.warm))
        self.assertEqual(governed(changed, restart=True),
                         governed(await self.full_projection(), restart=True))

    async def test_changed_plan_reconsults_task_storage(self):
        self.touch(self.consulted)
        changed, evaluated, lookups = await self.evaluated()
        self.assertEqual(evaluated, [self.consulted])
        self.assertIn(self.consulted, lookups)
        run = index_of(changed)["last_run"]
        self.assertEqual((run["actionable_hits"], run["actionable_misses"]), (1, 1))
        self.assertEqual(governed(changed), governed(self.warm))

    async def test_quarantined_task_file_is_evaluated_against_storage(self):
        quarantine = self.service.task_repository.quarantine
        quarantine.mkdir(parents=True, exist_ok=True)
        (quarantine / f"{self.consulted}.{'e' * 32}.1.corrupt").write_text("{")
        self.touch(self.other)
        service = self.service
        with patch.object(service, "_approval_is_actionable",
                          wraps=service._approval_is_actionable) as evaluate:
            with self.assertRaises(GovernanceError) as raised:
                await service.async_health_summary()
        self.assertEqual(raised.exception.code, ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        self.assertIn(self.consulted, [call.args[0].plan_id for call in evaluate.call_args_list])
        # The failure discards every derived entry, and a full projection
        # refuses identically.
        self.assertEqual(service._health_incremental_health()["actionable_entries"], 0)
        with self.assertRaises(GovernanceError) as full:
            await self.full_projection()
        self.assertEqual(full.exception.code, ErrorCode.EXECUTION_TASK_STORAGE_ERROR)

    # R1-H1 (Codex round 1): no other record changes, so only quarantine
    # state can invalidate the reused aggregate. Kills mutants Q1, Q3.
    async def test_quarantine_only_task_change_is_not_served_from_the_aggregate(self):
        quarantine = self.service.task_repository.quarantine
        quarantine.mkdir(parents=True, exist_ok=True)
        corrupt = quarantine / f"{self.consulted}.{'e' * 32}.1.corrupt"
        corrupt.write_text("{")
        ordinary = await self.outcome()
        self.assertEqual(ordinary, ("refused", ErrorCode.EXECUTION_TASK_STORAGE_ERROR))
        await self.assert_outcome_equals_full("task_quarantine_only")
        # Removing it again is also a quarantine-only change.
        corrupt.unlink()
        back = await self.service.async_health_summary()
        self.assertEqual(governed(back), governed(self.warm))
        await self.assert_incremental_equals_full("task_quarantine_removed")
        reused = await self.service.async_health_summary()
        self.assertEqual(index_of(reused)["last_run"]["assembly"], "reused")
        self.assertEqual(hot(reused)["terminal_plan_records_deserialized"], 0)

    # Kills mutants Q2, Q8.
    async def test_unknown_quarantine_listing_forbids_aggregate_reuse(self):
        real = os.scandir
        denied = {self.service.repository.quarantine, self.service.task_repository.quarantine}
        for target in sorted(denied):
            def scandir(path=".", target=target):
                if Path(path) == target:
                    raise PermissionError("quarantine listing denied")
                return real(path)

            with self.subTest(denied=target.parent.name):
                with patch.object(storage_module.os, "scandir", side_effect=scandir):
                    # Unknown twice in a row: still never reused.
                    for _ in range(2):
                        changed = await self.service.async_health_summary()
                        self.assertEqual(index_of(changed)["last_run"]["assembly"], "rebuilt")
                        self.assertEqual(governed(changed), governed(self.warm))
        # An aggregate built without a known listing is never remembered; the
        # next readable call rebuilds once and is then reused.
        rebuilt = await self.service.async_health_summary()
        self.assertEqual(index_of(rebuilt)["last_run"]["assembly"], "rebuilt")
        warm = await self.service.async_health_summary()
        self.assertEqual(index_of(warm)["last_run"]["assembly"], "reused")

    # The explicit quarantine generation invalidates even where a listing
    # cannot show the event. Kills mutants Q4, Q5, Q6, Q7.
    async def test_quarantine_and_restoration_bump_the_quarantine_generation(self):
        plans, tasks = self.service.repository, self.service.task_repository
        listings = (plans.quarantine_listing(), tasks.quarantine_listing())
        with patch.object(plans, "quarantine_listing", return_value=listings[0]), \
                patch.object(tasks, "quarantine_listing", return_value=listings[1]):
            before = plans.quarantine_generation
            result = plans.restore_quarantined_records()
            self.assertEqual((result["restored"], result["retained"]), (0, 0))
            self.assertEqual(plans.quarantine_generation, before + 1)
            restored = await self.service.async_health_summary()
            self.assertEqual(index_of(restored)["last_run"]["assembly"], "rebuilt")
            self.assertEqual(governed(restored), governed(self.warm))
        for repository, path in (
            (plans, plans.root / f"{'c' * 32}.json"),
            (tasks, tasks.root / f"{'c' * 32}.{'d' * 32}.json"),
        ):
            path.write_text("{", encoding="utf-8")
            before = repository.quarantine_generation
            repository._quarantine(path)
            self.assertEqual(repository.quarantine_generation, before + 1)
            self.assertFalse(path.exists())

    async def test_unknown_quarantine_listing_evaluates_every_plan(self):
        self.touch(self.other)
        with patch.object(self.service.task_repository, "quarantined_plan_prefixes",
                          return_value=None):
            changed, evaluated, lookups = await self.evaluated()
        self.assertEqual(sorted(evaluated), self.ids)
        self.assertIn(self.consulted, lookups)
        run = index_of(changed)["last_run"]
        self.assertEqual((run["actionable_hits"], run["actionable_misses"]), (0, 2))
        self.assertEqual(governed(changed), governed(self.warm))

    async def test_deep_audit_reevaluates_every_plan(self):
        deep, evaluated, lookups = await self.evaluated(deep_storage_audit=True)
        self.assertEqual(sorted(evaluated), self.ids)
        self.assertIn(self.consulted, lookups)
        run = index_of(deep)["last_run"]
        self.assertEqual((run["mode"], run["actionable_hits"], run["actionable_misses"]),
                         ("deep_rescan", 0, 2))

    async def test_authorization_never_reads_the_actionability_memo(self):
        self.service._health_actionable_memo = _Untouchable()
        with self.lookups() as lookup:
            public = self.service.get_plan(self.consulted)
        self.assertFalse(public["approval_actionable"])
        self.assertIn(self.consulted, [call.args[0] for call in lookup.call_args_list])

    # Kills mutant X4 (docs/evidence/hamcp097-mutations).
    async def test_non_terminal_plan_is_evaluated_on_every_assembly(self):
        created = await self.update_plan()
        self.assertEqual(created["status"], "awaiting_approval")
        await self.service.async_health_summary()
        self.touch(self.other)
        changed, evaluated, _ = await self.evaluated()
        self.assertEqual(index_of(changed)["last_run"]["assembly"], "rebuilt")
        self.assertIn(created["plan_id"], evaluated)
        self.assertNotIn(created["plan_id"], self.service._health_actionable_memo)
        self.assertEqual(index_of(changed)["actionable_entries"], 2)

    # Kills mutant X9 (docs/evidence/hamcp097-mutations).
    async def test_removed_plan_leaves_the_actionability_memo(self):
        self.assertEqual(sorted(self.service._health_actionable_memo), self.ids)
        self.paths[self.other].unlink()
        changed, evaluated, _ = await self.evaluated()
        self.assertEqual(changed["total_plans"], 1)
        self.assertEqual(sorted(self.service._health_actionable_memo), [self.consulted])
        self.assertEqual(index_of(changed)["actionable_entries"], 1)

    # Kills mutant X10 (docs/evidence/hamcp097-mutations).
    async def test_full_rebuild_without_keys_drops_actionability_entries(self):
        self.assertEqual(sorted(self.service._health_actionable_memo), self.ids)
        await self.update_plan()
        self.service.health_summary()
        self.assertEqual(self.service._health_actionable_memo, {})

    # Kills mutant X12 (docs/evidence/hamcp097-mutations).
    async def test_unreadable_quarantine_listing_evaluates_every_plan(self):
        repository = self.service.task_repository
        real = os.scandir

        def scandir(path="."):
            if Path(path) == repository.quarantine:
                raise PermissionError("quarantine listing denied")
            return real(path)

        self.touch(self.other)
        with patch.object(task_storage_module.os, "scandir", side_effect=scandir):
            self.assertIsNone(repository.quarantined_plan_prefixes())
            changed, evaluated, lookups = await self.evaluated()
        self.assertEqual(sorted(evaluated), self.ids)
        self.assertIn(self.consulted, lookups)
        run = index_of(changed)["last_run"]
        self.assertEqual((run["actionable_hits"], run["actionable_misses"]), (0, 2))

    # Kills mutant X13 (docs/evidence/hamcp097-mutations).
    async def test_only_corrupt_quarantine_files_force_reevaluation(self):
        quarantine = self.service.task_repository.quarantine
        quarantine.mkdir(parents=True, exist_ok=True)
        (quarantine / f"{self.consulted}.{'e' * 32}.1.json").write_text("{")
        self.assertEqual(self.service.task_repository.quarantined_plan_prefixes(), frozenset())
        self.touch(self.other)
        changed, evaluated, lookups = await self.evaluated()
        self.assertEqual(evaluated, [self.other])
        self.assertNotIn(self.consulted, lookups)
        self.assertEqual(governed(changed), governed(self.warm))

    # Kills mutant X15 (docs/evidence/hamcp097-mutations).
    async def test_startup_validation_prewarms_the_actionability_memo(self):
        self.service = await self.fresh_service()
        await self.service.start_historical_validation()
        state = self.service._historical_validation
        self.assertEqual((state["state"], state["assembly_prewarmed"]), ("complete", True))
        self.assertEqual(sorted(self.service._health_actionable_memo), self.ids)
        self.touch(self.other)
        changed, evaluated, lookups = await self.evaluated()
        self.assertEqual(evaluated, [self.other])
        self.assertNotIn(self.consulted, lookups)


class _Untouchable(dict):
    def __getattribute__(self, name):
        raise AssertionError(f"authorization read the health memo: {name}")


class ServeLaunchTests(unittest.IsolatedAsyncioTestCase):
    """The add-on starts validation after strict recovery and cancels it."""

    @staticmethod
    def configured():
        from ha_mcp_engineering.configuration import Settings

        return Settings(
            ha_url="http://supervisor/core", ha_token="test-token",
            access_secret="hamcp097-listener-access-secret", port=8100,
            audit_path="/data/audit.jsonl", rate_limit_per_minute=120,
            rate_limit_burst=25, destructive_services=frozenset(),
            ingress_port=8110,
        )

    async def serve(self, gateway):
        from ha_mcp_engineering import application

        events = []
        started = asyncio.Event()

        async def validation():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.append("validation_cancelled")
                raise

        def start():
            events.append("validation_started")
            return asyncio.create_task(validation())

        async def recovery(trigger, *, strict=False):
            events.append(f"recovery:{trigger}:{strict}")

        async def upstream(_gateway):
            await asyncio.Event().wait()

        class FakeConfig:
            def __init__(self, app, **kwargs):
                self.port = kwargs["port"]

        class FakeServer:
            def __init__(self, config):
                self.config = config
                self.should_exit = False
                self.install_signal_handlers = lambda: None

            async def serve(self):
                if "validation_started" in events:
                    await started.wait()
                await asyncio.sleep(0)

        with patch.object(application.uvicorn, "Config", FakeConfig), patch.object(
            application.uvicorn, "Server", FakeServer
        ), patch.object(
            application, "create_application", return_value=gateway
        ), patch.object(
            application, "create_approval_application", return_value=object()
        ), patch.object(
            application, "_run_f3_recovery_pass", side_effect=recovery
        ), patch.object(
            application, "_supervise_upstream_reconciliation", side_effect=upstream
        ), patch.object(
            application.TRACE_SNAPSHOTS, "start_poller", return_value=None
        ), patch.object(
            application.GOVERNANCE, "start_historical_validation", side_effect=start
        ):
            await asyncio.wait_for(application._serve(self.configured()), timeout=5)
        return events

    async def test_validation_starts_after_strict_recovery_and_is_cancelled(self):
        from unittest.mock import MagicMock

        from ha_mcp_engineering.routing import AuthenticatedMcpGateway

        events = await self.serve(MagicMock(spec=AuthenticatedMcpGateway))
        self.assertEqual(events[:2], ["recovery:startup:True", "validation_started"])
        self.assertIn("validation_cancelled", events)

    async def test_an_unauthenticated_test_gateway_starts_no_validation(self):
        events = await self.serve(object())
        self.assertNotIn("validation_started", events)


class RestartBackoffAssemblyTests(unittest.IsolatedAsyncioTestCase):
    # Kills mutant A1 (docs/evidence/hamcp097-mutations).
    async def test_async_backoff_deadline_is_not_served_from_a_reused_assembly(self):
        fixture = restart_fixtures.Beta11RestartReconciliationTests()
        await fixture.asyncSetUp()
        try:
            _, task = await fixture._pending_addon_restart()
            service = fixture._recovered()
            await service.reconcile_operational_plans(trigger="startup")
            await service.async_health_summary()
            health = await service.async_health_summary()
            self.assertEqual(index_of(health)["last_run"]["assembly"], "reused")
            self.assertEqual(health["restart_reconciliation"]["pending_backoff_record_count"], 1)
            persisted = service.task_repository.get(task.task_id)
            next_at = persisted.verification_summary["restart_reconciliation"]["next_attempt_at"]
            fixture.clock.value = datetime.fromisoformat(next_at)
            health = await service.async_health_summary()
            self.assertEqual(health["restart_reconciliation"]["pending_eligible_record_count"], 1)
            self.assertEqual(health["restart_reconciliation"]["pending_backoff_record_count"], 0)
            self.assertEqual(index_of(health)["last_run"]["assembly"], "rebuilt")
            self.assertEqual(service.lifecycle_gateway.dispatch_count, 0)
        finally:
            await fixture.asyncTearDown()


if __name__ == "__main__":
    unittest.main()
