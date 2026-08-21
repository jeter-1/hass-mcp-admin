"""Durable execution-record corruption, fencing, and retention tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.f3.models import (  # noqa: E402
    ExecutionIdentity,
    ExecutorTiming,
)
from ha_mcp_engineering.f3.persistence import (  # noqa: E402
    BlindRedispatchProhibited,
    DuplicateExecutionActive,
    DurableExecutionRepository,
    ExecutionRecordCorrupt,
    ExecutionStorageError,
)
from tests.f3_synthetic_adapter import (  # noqa: E402
    prepared_dashboard_operation,
)


TIMING = ExecutorTiming(120, 60, 3, 3)
NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


def _identity(
    *, owner: str = "owner-primary", request: str = "request-primary"
) -> ExecutionIdentity:
    return ExecutionIdentity(
        task_id="task-persistence",
        plan_id="plan-persistence",
        attempt_id="attempt-persistence",
        request_id=request,
        owner_id=owner,
    )


class DurableExecutionRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = DurableExecutionRepository(self.temporary.name)
        self.prepared = prepared_dashboard_operation()

    def claim(self, identity: ExecutionIdentity | None = None):
        return self.repository.claim(
            identity=identity or _identity(),
            prepared=self.prepared,
            timing=TIMING,
            now=NOW,
        )

    @staticmethod
    def _add_synthetic_lock(record) -> None:
        record.lock_tokens = [
            {
                "key": "dashboard:overview",
                "generation": 1,
                "mode": "exclusive",
                "owner_id": "owner-primary",
            }
        ]

    def test_different_owner_cannot_claim_active_task(self):
        self.claim()
        with self.assertRaises(DuplicateExecutionActive):
            self.claim(
                _identity(owner="owner-other", request="request-other")
            )

    def test_same_owner_duplicate_is_active_until_claim_expires(self):
        self.claim()
        with self.assertRaises(DuplicateExecutionActive):
            self.claim()

    def test_task_identity_cannot_be_reused_for_another_target(self):
        self.claim()
        with self.assertRaises(ExecutionRecordCorrupt):
            self.repository.claim(
                identity=_identity(),
                prepared=prepared_dashboard_operation(url_path="other"),
                timing=TIMING,
                now=NOW,
            )

    def test_unknown_schema_and_unknown_fields_fail_closed(self):
        self.claim()
        path = self.repository._path("task-persistence")
        value = json.loads(path.read_text(encoding="utf-8"))
        value["schema_version"] += 1
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ExecutionRecordCorrupt):
            self.repository.get("task-persistence")
        value["schema_version"] -= 1
        value["unknown"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ExecutionRecordCorrupt):
            self.repository.get("task-persistence")

    def test_type_confusion_and_malformed_lock_tokens_fail_closed(self):
        self.claim()
        path = self.repository._path("task-persistence")
        original = json.loads(path.read_text(encoding="utf-8"))
        corruptions = (
            ("terminal", "false"),
            ("claim_generation", True),
            ("preflight_completed", 1),
        )
        for field_name, value in corruptions:
            payload = dict(original)
            payload[field_name] = value
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.subTest(field_name=field_name):
                with self.assertRaises(ExecutionRecordCorrupt):
                    self.repository.get("task-persistence")
        payload = dict(original)
        payload["lock_tokens"] = [{"key": "dashboard:overview"}]
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ExecutionRecordCorrupt):
            self.repository.get("task-persistence")

    def test_contradictory_dispatch_classes_fail_closed_on_load(self):
        corruptions = {
            "no_intent_failed_post_dispatch": {
                "normalized_outcome": "failed_post_dispatch",
                "task_state": "failed_post_dispatch",
            },
            "no_intent_manual_review": {
                "normalized_outcome": "manual_review_required",
                "task_state": "manual_review_required",
            },
            "no_intent_provider_response": {
                "provider_response_received": True,
            },
            "no_intent_observation": {"observation_attempts": 1},
            "no_intent_verification": {"verification_attempts": 1},
            "no_intent_post_dispatch_evidence": {
                "evidence": {
                    "manual_review_reason_code": "synthetic_post_dispatch",
                }
            },
            "no_intent_post_dispatch_event": {},
        }
        for name, changes in corruptions.items():
            with self.subTest(name=name):
                repository = DurableExecutionRepository(
                    Path(self.temporary.name) / name
                )
                claim = repository.claim(
                    identity=_identity(),
                    prepared=self.prepared,
                    timing=TIMING,
                    now=NOW,
                )
                repository.terminalize_pre_dispatch(
                    "task-persistence",
                    owner_id="owner-primary",
                    claim_generation=claim.claim_generation,
                    outcome="failed_pre_dispatch",
                    now=NOW,
                )
                path = repository._path("task-persistence")
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload.update(changes)
                if name == "no_intent_post_dispatch_event":
                    payload["events"].append(
                        {
                            "sequence": len(payload["events"]) + 1,
                            "event_type": "verification_recorded",
                            "occurred_at": NOW.isoformat(),
                            "diagnostic_codes": [],
                        }
                    )
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ExecutionRecordCorrupt):
                    repository.get("task-persistence")

        repository = DurableExecutionRepository(
            Path(self.temporary.name) / "intent_with_pre_dispatch_outcome"
        )
        claim = repository.claim(
            identity=_identity(),
            prepared=self.prepared,
            timing=TIMING,
            now=NOW,
        )
        record = repository.get("task-persistence")
        assert record is not None
        self._add_synthetic_lock(record)
        repository._write_unlocked(record)
        repository.record_preflight(
            "task-persistence",
            owner_id="owner-primary",
            claim_generation=claim.claim_generation,
            now=NOW,
        )
        repository.commit_dispatch_intent(
            "task-persistence",
            owner_id="owner-primary",
            claim_generation=claim.claim_generation,
            request_id="request-primary",
            provider_operation="synthetic_dashboard_update",
            provider_arguments_hash="a" * 64,
            timing=TIMING,
            now=NOW,
        )
        path = repository._path("task-persistence")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update(
            {
                "state": "terminal",
                "normalized_outcome": "failed_pre_dispatch",
                "task_state": "failed_pre_dispatch",
                "terminal": True,
            }
        )
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ExecutionRecordCorrupt):
            repository.get("task-persistence")

    def test_authoritative_terminal_writers_satisfy_closed_classes(self):
        for outcome in (
            "preflight_rejected",
            "lock_conflict",
            "provider_unavailable_pre_dispatch",
            "failed_pre_dispatch",
            "cancelled_pre_dispatch",
        ):
            with self.subTest(outcome=outcome):
                repository = DurableExecutionRepository(
                    Path(self.temporary.name) / outcome
                )
                claim = repository.claim(
                    identity=_identity(),
                    prepared=self.prepared,
                    timing=TIMING,
                    now=NOW,
                )
                repository.terminalize_pre_dispatch(
                    "task-persistence",
                    owner_id="owner-primary",
                    claim_generation=claim.claim_generation,
                    outcome=outcome,
                    now=NOW,
                )
                loaded = repository.get("task-persistence")
                self.assertEqual(outcome, loaded.normalized_outcome)

        no_dispatch = DurableExecutionRepository(
            Path(self.temporary.name) / "verified_no_dispatch"
        )
        claim = no_dispatch.claim(
            identity=_identity(),
            prepared=self.prepared,
            timing=TIMING,
            now=NOW,
        )
        record = no_dispatch.get("task-persistence")
        assert record is not None
        self._add_synthetic_lock(record)
        no_dispatch._write_unlocked(record)
        no_dispatch.terminalize_verified_no_dispatch(
            "task-persistence",
            owner_id="owner-primary",
            claim_generation=claim.claim_generation,
            resulting_state_fingerprint="b" * 64,
            evidence_hash="c" * 64,
            now=NOW,
        )
        self.assertEqual(
            "succeeded_verified",
            no_dispatch.get("task-persistence").normalized_outcome,
        )

        for outcome in ("succeeded_verified", "failed_post_dispatch"):
            with self.subTest(post_intent_outcome=outcome):
                repository = DurableExecutionRepository(
                    Path(self.temporary.name) / f"post_intent_{outcome}"
                )
                claim = repository.claim(
                    identity=_identity(),
                    prepared=self.prepared,
                    timing=TIMING,
                    now=NOW,
                )
                record = repository.get("task-persistence")
                assert record is not None
                self._add_synthetic_lock(record)
                repository._write_unlocked(record)
                repository.record_preflight(
                    "task-persistence",
                    owner_id="owner-primary",
                    claim_generation=claim.claim_generation,
                    now=NOW,
                )
                repository.commit_dispatch_intent(
                    "task-persistence",
                    owner_id="owner-primary",
                    claim_generation=claim.claim_generation,
                    request_id="request-primary",
                    provider_operation="synthetic_dashboard_update",
                    provider_arguments_hash="a" * 64,
                    timing=TIMING,
                    now=NOW,
                )
                repository.record_verification(
                    "task-persistence",
                    owner_id="owner-primary",
                    claim_generation=claim.claim_generation,
                    outcome=outcome,
                    terminal=True,
                    now=NOW,
                )
                loaded = repository.get("task-persistence")
                self.assertEqual(outcome, loaded.normalized_outcome)

    def test_invalid_json_fails_closed_without_replacement_authority(self):
        self.claim()
        path = self.repository._path("task-persistence")
        path.write_text("{", encoding="utf-8")
        with self.assertRaises(ExecutionRecordCorrupt):
            self.repository.get("task-persistence")
        with self.assertRaises(ExecutionRecordCorrupt):
            self.claim()
        self.assertTrue(path.exists())

    def test_read_failure_is_storage_error(self):
        def fail(stage: str) -> None:
            if stage == "before_execution_read":
                raise OSError("synthetic read failure")

        repository = DurableExecutionRepository(
            self.temporary.name, fault_hook=fail
        )
        with self.assertRaises(ExecutionStorageError):
            repository.get("task-persistence")

    def test_after_intent_write_uncertainty_remains_possibly_dispatched(self):
        class Fault:
            active = False

            def __call__(self, stage: str) -> None:
                if self.active and stage == "after_durable_intent_persistence":
                    raise RuntimeError("synthetic response loss")

        fault = Fault()
        repository = DurableExecutionRepository(
            self.temporary.name, fault_hook=fault
        )
        claim = repository.claim(
            identity=_identity(),
            prepared=self.prepared,
            timing=TIMING,
            now=NOW,
        )
        # The persistence API requires real held-lock evidence.  This test uses
        # bounded synthetic tokens because lock ownership is independently
        # proven by the lock-manager suite.
        record = repository.get("task-persistence")
        assert record is not None
        record.lock_tokens = [
            {
                "key": "dashboard:overview",
                "generation": 1,
                "mode": "exclusive",
                "owner_id": "owner-primary",
            }
        ]
        record.preflight_completed = True
        repository._write_unlocked(record)
        fault.active = True
        with self.assertRaises(RuntimeError):
            repository.commit_dispatch_intent(
                "task-persistence",
                owner_id="owner-primary",
                claim_generation=claim.claim_generation,
                request_id="request-primary",
                provider_operation="synthetic_dashboard_update",
                provider_arguments_hash="a" * 64,
                timing=TIMING,
                now=NOW,
            )
        durable = DurableExecutionRepository(self.temporary.name).get(
            "task-persistence"
        )
        self.assertIsNotNone(durable)
        assert durable is not None
        self.assertIsNotNone(durable.dispatch_intent)
        self.assertEqual(durable.dispatch_count, 1)

    def test_second_intent_is_permanently_rejected(self):
        claim = self.claim()
        record = self.repository.get("task-persistence")
        assert record is not None
        record.lock_tokens = [
            {
                "key": "dashboard:overview",
                "generation": 1,
                "mode": "exclusive",
                "owner_id": "owner-primary",
            }
        ]
        record.preflight_completed = True
        self.repository._write_unlocked(record)
        arguments = dict(
            task_id="task-persistence",
            owner_id="owner-primary",
            claim_generation=claim.claim_generation,
            request_id="request-primary",
            provider_operation="synthetic_dashboard_update",
            provider_arguments_hash="a" * 64,
            timing=TIMING,
            now=NOW,
        )
        self.repository.commit_dispatch_intent(**arguments)
        with self.assertRaises(BlindRedispatchProhibited):
            self.repository.commit_dispatch_intent(**arguments)

    def test_pre_intent_retry_preserves_operation_identity_without_intent(self):
        claim = self.claim()
        record = self.repository.get("task-persistence")
        assert record is not None
        record.lock_tokens = [
            {
                "key": "dashboard:overview",
                "generation": 1,
                "mode": "exclusive",
                "owner_id": "owner-primary",
            }
        ]
        record.preflight_completed = True
        self.repository._write_unlocked(record)
        retryable = self.repository.record_pre_intent_retry(
            "task-persistence",
            owner_id="owner-primary",
            claim_generation=claim.claim_generation,
            diagnostic_code="approval_consumed_intent_not_recorded",
            now=NOW,
        )
        self.assertFalse(retryable.terminal)
        self.assertIsNone(retryable.dispatch_intent)
        self.assertEqual(retryable.dispatch_count, 0)
        self.assertEqual(retryable.execution_identity().task_id, "task-persistence")
        self.assertEqual(retryable.execution_identity().plan_id, "plan-persistence")
        self.assertEqual(
            retryable.execution_identity().attempt_id, "attempt-persistence"
        )
        self.assertEqual(
            retryable.events[-1]["event_type"], "pre_intent_retry_required"
        )
        reclaimed = self.repository.claim(
            identity=_identity(request="request-retry"),
            prepared=self.prepared,
            timing=TIMING,
            now=NOW,
        )
        self.assertFalse(reclaimed.created)
        self.assertEqual(reclaimed.claim_generation, 2)
        self.assertIsNone(reclaimed.record.normalized_outcome)

    def test_cleanup_removes_only_old_terminal_records(self):
        claim = self.claim()
        self.repository.terminalize_pre_dispatch(
            "task-persistence",
            owner_id="owner-primary",
            claim_generation=claim.claim_generation,
            outcome="failed_pre_dispatch",
            now=NOW,
        )
        self.assertEqual(
            self.repository.cleanup(now=NOW + timedelta(days=91)), 1
        )
        self.assertIsNone(self.repository.get("task-persistence"))

    def test_cleanup_retains_nonterminal_records(self):
        self.claim()
        self.assertEqual(
            self.repository.cleanup(now=NOW + timedelta(days=91)), 0
        )
        self.assertIsNotNone(self.repository.get("task-persistence"))


if __name__ == "__main__":
    unittest.main()
