"""F1 durable execution-task schema and persistence contracts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.governance.task_models import (
    ALLOWED_TASK_TRANSITIONS,
    ExecutionTaskState,
    RESERVED_TASK_STATES,
    SINGLE_DISPATCH_OPERATIONS,
    TASK_SCHEMA_VERSION,
    new_execution_task,
)
from ha_mcp_engineering.governance.task_storage import (
    ExecutionTaskRepository,
    ExecutionTaskStorageError,
    TASK_NAMESPACE,
)
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalState,
    PlanStatus,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)
from tests.test_beta25_external_approval import (  # noqa: E402
    ExternalApprovalTestCase,
)
from tests.test_2_1a_beta2_operational_lifecycle import (  # noqa: E402
    Clock as LifecycleClock,
    FakeLifecycleGateway,
    LegacyGateway,
)


BASE_TIME = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def timestamp(offset: int = 0) -> str:
    return (BASE_TIME + timedelta(seconds=offset)).isoformat()


def make_task(
    *,
    plan_id: str | None = None,
    operation: str = "controlled_reload",
):
    plan_id = plan_id or uuid.uuid4().hex
    return new_execution_task(
        task_id=uuid.uuid4().hex,
        plan_id=plan_id,
        plan_hash="a" * 64,
        operation=operation,
        target={"target_type": "reload_domain", "target_id": "automation"},
        timestamp=timestamp(),
        execution_request_id=uuid.uuid4().hex,
        idempotency_key="b" * 64,
        approval_reference={
            "approval_kind": "apply",
            "authority_version": 2,
            "bound_plan_hash": "a" * 64,
        },
        legacy_projection={
            "plan_status": "approved",
            "execution_outcome": "not_applied",
        },
    )


def consume_task_approval(task, at: str) -> None:
    task.append_event(
        "approval_consumed",
        at,
        changes={
            "approval_reference": {
                **task.approval_reference,
                "approval_state": "consumed",
            }
        },
    )


class ExecutionTaskModelTests(unittest.TestCase):
    def test_schema_round_trip_and_deterministic_materialization(self):
        task = make_task()
        task.append_event(
            "preflight_started",
            timestamp(1),
            new_state=ExecutionTaskState.PREFLIGHT,
            changes={"started_at": timestamp(1)},
        )
        consume_task_approval(task, timestamp(2))
        task.append_event(
            "dispatch_attempted",
            timestamp(2),
            new_state=ExecutionTaskState.DISPATCHING,
            changes={
                "dispatched_at": timestamp(2),
                "maximum_post_dispatch_deadline": timestamp(24 * 60 * 60 + 2),
                "provider_attempts": [
                    {
                        "attempt": 1,
                        "attempted_at": timestamp(2),
                        "provider": "upstream_operational_lifecycle",
                        "response_received": False,
                    }
                ],
            },
        )
        task.append_event(
            "verification_started",
            timestamp(3),
            new_state=ExecutionTaskState.VERIFYING,
            changes={"verification_summary": {"status": "pending"}},
        )

        encoded = json.loads(
            json.dumps(task.to_dict(), sort_keys=True)
        )
        restored = type(task).from_dict(encoded)

        self.assertEqual(restored.to_dict(), task.to_dict())
        self.assertEqual(restored.task_schema_version, TASK_SCHEMA_VERSION)
        self.assertEqual(
            restored.maximum_post_dispatch_deadline,
            timestamp(24 * 60 * 60 + 2),
        )

    def test_illegal_and_reserved_transitions_fail_closed(self):
        task = make_task()
        with self.assertRaisesRegex(ValueError, "illegal"):
            task.append_event(
                "task_completed",
                timestamp(1),
                new_state=ExecutionTaskState.SUCCEEDED_VERIFIED,
            )
        transition_targets = set().union(
            *ALLOWED_TASK_TRANSITIONS.values()
        )
        self.assertTrue(transition_targets.isdisjoint(RESERVED_TASK_STATES))
        for reserved_state in RESERVED_TASK_STATES:
            with self.subTest(state=reserved_state.value):
                with self.assertRaisesRegex(ValueError, "illegal"):
                    task.append_event(
                        reserved_state.value,
                        timestamp(1),
                        new_state=reserved_state,
                    )
                forged = task.to_dict()
                forged["state"] = reserved_state.value
                forged["events"][0]["state_after"] = reserved_state.value
                forged["events"][0]["changes"]["state"] = (
                    reserved_state.value
                )
                with self.assertRaisesRegex(ValueError, "reserved"):
                    type(task).from_dict(forged)

    def test_terminal_task_cannot_reopen(self):
        task = make_task()
        task.append_event(
            "task_cancelled_pre_dispatch",
            timestamp(1),
            new_state=ExecutionTaskState.CANCELLED_PRE_DISPATCH,
            changes={
                "completed_at": timestamp(1),
                "terminal_outcome": "cancelled_pre_dispatch",
            },
        )
        with self.assertRaisesRegex(ValueError, "terminal"):
            task.append_event("verification_started", timestamp(2))

    def test_dispatch_without_consumed_approval_is_contradictory(self):
        task = make_task()
        task.append_event(
            "preflight_started",
            timestamp(1),
            new_state=ExecutionTaskState.PREFLIGHT,
            changes={"started_at": timestamp(1)},
        )
        task.append_event(
            "dispatch_attempted",
            timestamp(2),
            new_state=ExecutionTaskState.DISPATCHING,
            changes={
                "dispatched_at": timestamp(2),
                "maximum_post_dispatch_deadline": timestamp(
                    24 * 60 * 60 + 2
                ),
                "provider_attempts": [
                    {
                        "attempt": 1,
                        "attempted_at": timestamp(2),
                        "provider": "test_provider",
                        "response_received": False,
                    }
                ],
            },
        )

        with self.assertRaisesRegex(ValueError, "contradictory"):
            task.validate()

    def test_second_irreversible_dispatch_event_is_rejected(self):
        task = make_task()
        self.assertIn(task.operation, SINGLE_DISPATCH_OPERATIONS)
        task.append_event(
            "preflight_started",
            timestamp(1),
            new_state=ExecutionTaskState.PREFLIGHT,
            changes={"started_at": timestamp(1)},
        )
        consume_task_approval(task, timestamp(2))
        first_attempt = {
            "attempt": 1,
            "attempted_at": timestamp(2),
            "provider": "upstream_operational_lifecycle",
            "response_received": False,
        }
        task.append_event(
            "dispatch_attempted",
            timestamp(2),
            new_state=ExecutionTaskState.DISPATCHING,
            changes={
                "dispatched_at": timestamp(2),
                "maximum_post_dispatch_deadline": timestamp(
                    24 * 60 * 60 + 2
                ),
                "provider_attempts": [first_attempt],
            },
        )

        with self.assertRaisesRegex(ValueError, "single-dispatch"):
            task.append_event(
                "dispatch_attempted",
                timestamp(3),
            )

        self.assertEqual(len(task.events), 4)
        self.assertEqual(len(task.provider_attempts), 1)

    def test_configuration_task_retains_multiple_provider_attempts(self):
        task = make_task(operation="configuration_plan")
        self.assertNotIn(task.operation, SINGLE_DISPATCH_OPERATIONS)
        task.append_event(
            "preflight_started",
            timestamp(1),
            new_state=ExecutionTaskState.PREFLIGHT,
            changes={"started_at": timestamp(1)},
        )
        consume_task_approval(task, timestamp(2))
        attempts = [
            {
                "attempt": 1,
                "attempted_at": timestamp(2),
                "provider": "engineering_configuration_provider",
                "response_received": True,
            }
        ]
        task.append_event(
            "dispatch_attempted",
            timestamp(2),
            new_state=ExecutionTaskState.DISPATCHING,
            changes={
                "dispatched_at": timestamp(2),
                "maximum_post_dispatch_deadline": timestamp(
                    24 * 60 * 60 + 2
                ),
                "provider_attempts": attempts,
            },
        )
        attempts.append(
            {
                "attempt": 2,
                "attempted_at": timestamp(3),
                "provider": "engineering_configuration_provider",
                "response_received": False,
            }
        )
        task.append_event(
            "dispatch_attempted",
            timestamp(3),
            changes={"provider_attempts": attempts},
        )

        task.validate()
        restored = type(task).from_dict(task.to_dict())
        self.assertEqual(len(restored.provider_attempts), 2)
        self.assertEqual(
            [
                event.event_type
                for event in restored.events
                if event.event_type == "dispatch_attempted"
            ],
            ["dispatch_attempted", "dispatch_attempted"],
        )


class ExecutionTaskStorageTests(unittest.TestCase):
    def test_namespace_isolated_from_legacy_plan_enumeration(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ExecutionTaskRepository(directory)
            task = make_task()
            repository.save(task)

            self.assertEqual(
                repository.root, Path(directory) / TASK_NAMESPACE
            )
            self.assertEqual(
                list(Path(directory).glob("*.json")), []
            )
            self.assertEqual(repository.get(task.task_id), task)

    def test_one_task_per_plan_and_idempotency_key(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ExecutionTaskRepository(directory)
            task = make_task()
            repository.save(task)
            duplicate = make_task(plan_id=task.plan_id)
            duplicate.idempotency_key = "c" * 64
            duplicate.events[0].changes["idempotency_key"] = "c" * 64

            with self.assertRaisesRegex(
                ExecutionTaskStorageError, "ownership"
            ):
                repository.save(duplicate)

    def test_append_only_history_and_deadline_are_immutable(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ExecutionTaskRepository(directory)
            task = make_task()
            repository.save(task)
            task.append_event(
                "preflight_started",
                timestamp(1),
                new_state=ExecutionTaskState.PREFLIGHT,
                changes={"started_at": timestamp(1)},
            )
            consume_task_approval(task, timestamp(2))
            task.append_event(
                "dispatch_attempted",
                timestamp(2),
                new_state=ExecutionTaskState.DISPATCHING,
                changes={
                    "dispatched_at": timestamp(2),
                    "maximum_post_dispatch_deadline": timestamp(
                        24 * 60 * 60 + 2
                    ),
                    "provider_attempts": [
                        {
                            "attempt": 1,
                            "attempted_at": timestamp(2),
                            "provider": "test_provider",
                            "response_received": False,
                        }
                    ],
                },
            )
            repository.save(task)

            changed = repository.get(task.task_id)
            self.assertIsNotNone(changed)
            assert changed is not None
            changed.maximum_post_dispatch_deadline = timestamp(
                24 * 60 * 60 + 3
            )
            changed.events[-1].changes[
                "maximum_post_dispatch_deadline"
            ] = changed.maximum_post_dispatch_deadline
            with self.assertRaises(ExecutionTaskStorageError):
                repository.save(changed)

    def test_interrupted_materialization_does_not_replace_previous_record(self):
        stages: list[str] = []

        def fail_before_replace(stage: str) -> None:
            stages.append(stage)
            if stage == "before_task_replace":
                raise OSError("injected materialization interruption")

        with tempfile.TemporaryDirectory() as directory:
            repository = ExecutionTaskRepository(directory)
            task = make_task()
            repository.save(task)
            original = repository._path(task.task_id).read_bytes()
            task.append_event(
                "preflight_started",
                timestamp(1),
                new_state=ExecutionTaskState.PREFLIGHT,
                changes={"started_at": timestamp(1)},
            )
            repository._fault_hook = fail_before_replace

            with self.assertRaises(ExecutionTaskStorageError):
                repository.save(task)

            self.assertEqual(repository._path(task.task_id).read_bytes(), original)
            self.assertIn("before_task_replace", stages)

    def test_interrupted_event_write_preserves_previous_envelope(self):
        def fail_before_write(stage: str) -> None:
            if stage == "before_task_write":
                raise OSError("injected event-write interruption")

        with tempfile.TemporaryDirectory() as directory:
            repository = ExecutionTaskRepository(directory)
            task = make_task()
            repository.save(task)
            original = repository._path(task.task_id).read_bytes()
            task.append_event(
                "preflight_started",
                timestamp(1),
                new_state=ExecutionTaskState.PREFLIGHT,
                changes={"started_at": timestamp(1)},
            )
            repository._fault_hook = fail_before_write

            with self.assertRaises(ExecutionTaskStorageError):
                repository.save(task)

            self.assertEqual(
                repository._path(task.task_id).read_bytes(), original
            )
            self.assertEqual(repository.event_write_failures, 1)

    def test_corrupt_materialization_is_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ExecutionTaskRepository(directory)
            task = make_task()
            repository.save(task)
            path = repository._path(task.task_id)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["state"] = "succeeded_verified"
            path.write_text(json.dumps(value), encoding="utf-8")

            with self.assertRaises(ExecutionTaskStorageError):
                repository.get(task.task_id)
            self.assertEqual(repository.corruption_count, 1)
            self.assertFalse(path.exists())

    def test_corrupt_task_still_reserves_plan_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ExecutionTaskRepository(directory)
            task = make_task()
            repository.save(task)
            path = repository._path(task.task_id)
            path.write_text("{", encoding="utf-8")

            with self.assertRaises(ExecutionTaskStorageError):
                repository.get_for_plan(task.plan_id)
            replacement = make_task(plan_id=task.plan_id)
            replacement.idempotency_key = "c" * 64
            replacement.events[0].changes["idempotency_key"] = "c" * 64
            with self.assertRaises(ExecutionTaskStorageError):
                repository.save(replacement)

    def test_forged_duplicate_dispatch_is_quarantined_and_reserves_plan(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            repository = ExecutionTaskRepository(directory)
            task = make_task(operation="configuration_plan")
            task.append_event(
                "preflight_started",
                timestamp(1),
                new_state=ExecutionTaskState.PREFLIGHT,
                changes={"started_at": timestamp(1)},
            )
            consume_task_approval(task, timestamp(2))
            attempts = [
                {
                    "attempt": 1,
                    "attempted_at": timestamp(2),
                    "provider": "engineering_configuration_provider",
                    "response_received": True,
                },
                {
                    "attempt": 2,
                    "attempted_at": timestamp(3),
                    "provider": "engineering_configuration_provider",
                    "response_received": False,
                },
            ]
            task.append_event(
                "dispatch_attempted",
                timestamp(2),
                new_state=ExecutionTaskState.DISPATCHING,
                changes={
                    "dispatched_at": timestamp(2),
                    "maximum_post_dispatch_deadline": timestamp(
                        24 * 60 * 60 + 2
                    ),
                    "provider_attempts": attempts,
                },
            )
            forged = task.to_dict()
            forged["operation"] = "controlled_reload"
            forged["events"][0]["changes"]["operation"] = (
                "controlled_reload"
            )
            path = repository._path(
                task.task_id, plan_id=task.plan_id
            )
            path.write_text(
                json.dumps(forged, sort_keys=True), encoding="utf-8"
            )

            with self.assertRaises(ExecutionTaskStorageError):
                repository.get(task.task_id)

            self.assertEqual(repository.corruption_count, 1)
            self.assertFalse(path.exists())
            replacement = make_task(plan_id=task.plan_id)
            with self.assertRaisesRegex(
                ExecutionTaskStorageError, "ownership"
            ):
                repository.save(replacement)


class DurableTaskApplyTests(ExternalApprovalTestCase):
    async def test_apply_returns_one_authoritative_task_and_preserves_plan_hash(
        self,
    ):
        created = await self.create()
        legacy_projection = self.service.get_plan(created["plan_id"])[
            "execution_task"
        ]
        self.assertEqual(legacy_projection["record_kind"], "legacy_plan")
        self.assertIsNone(legacy_projection["task_id"])
        await self.grant(created)
        persisted = self.repository.get(created["plan_id"])
        plan_hash_before = self.service.plan_hash(persisted)

        applied = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        task = self.service.get_execution_task(applied["task_id"])
        duplicate = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )

        self.assertEqual(applied["task_state"], "succeeded_verified")
        self.assertFalse(applied["task_reused"])
        self.assertEqual(task["plan_id"], created["plan_id"])
        self.assertEqual(task["plan_hash"], created["plan_hash"])
        self.assertEqual(task["state"], "succeeded_verified")
        self.assertEqual(
            self.service.get_plan(created["plan_id"])["execution_task"][
                "task_id"
            ],
            applied["task_id"],
        )
        listed = self.service.list_execution_tasks(
            state="succeeded_verified",
            terminal_outcome=task["terminal_outcome"],
            plan_id=created["plan_id"],
        )
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["tasks"][0]["task_id"], applied["task_id"])
        self.assertEqual(duplicate["status"], "already_applied")
        self.assertTrue(duplicate["task_reused"])
        self.assertEqual(duplicate["task_id"], applied["task_id"])
        self.assertEqual(self.gateway.writes, 1)
        self.assertEqual(
            self.service.plan_hash(
                self.repository.get(created["plan_id"])
            ),
            plan_hash_before,
        )
        self.assertEqual(
            self.service.health_summary()["execution_tasks"][
                "no_blind_redispatch_preventions"
            ],
            1,
        )

    async def test_invalid_apply_authority_creates_no_task_or_dispatch(self):
        created = await self.create()

        with self.assertRaises(GovernanceError):
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )
        with self.assertRaises(GovernanceError):
            await self.service.apply(
                created["plan_id"], "0" * 64
            )

        self.assertEqual(self.service.task_repository.list(), [])
        self.assertEqual(self.gateway.writes, 0)

    async def test_concurrent_duplicate_apply_creates_one_task_and_one_dispatch(
        self,
    ):
        created = await self.create()
        await self.grant(created)

        first, second = await asyncio.gather(
            self.service.apply(
                created["plan_id"], created["plan_hash"]
            ),
            self.service.apply(
                created["plan_id"], created["plan_hash"]
            ),
        )

        self.assertEqual({first["status"], second["status"]}, {
            "applied",
            "already_applied",
        })
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(self.gateway.writes, 1)
        self.assertEqual(
            len(self.service.task_repository.list()), 1
        )

    async def test_pre_dispatch_cancellation_preserves_approval_and_calls_no_provider(
        self,
    ):
        created = await self.create()
        await self.grant(created)
        plan = self.repository.get(created["plan_id"])
        task = self.service._create_task_for_plan(
            plan, created["plan_hash"]
        )
        self.service._record_task_event(
            task,
            "preflight_started",
            new_state=ExecutionTaskState.PREFLIGHT,
            changes={"started_at": self.service._timestamp()},
        )

        cancelled = await self.service.cancel_execution_task(task.task_id)

        self.assertEqual(cancelled["status"], "cancelled_pre_dispatch")
        self.assertFalse(cancelled["approval_consumed"])
        self.assertEqual(self.gateway.writes, 0)
        self.assertEqual(
            self.repository.get(created["plan_id"]).approval.state.value,
            "approved",
        )
        audit = [
            json.loads(line)
            for line in self.audit_path.read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        task_events = [
            entry
            for entry in audit
            if entry.get("task_id") == task.task_id
        ]
        self.assertTrue(task_events)
        self.assertTrue(
            all(entry.get("access") == "write" for entry in task_events)
        )
        self.assertTrue(
            all(
                entry.get("operation_class")
                == "execution_task_lifecycle"
                for entry in task_events
            )
        )
        cancellation_audit = next(
            entry
            for entry in reversed(task_events)
            if entry.get("event") == "task_cancelled_pre_dispatch"
        )
        self.assertFalse(cancellation_audit["approval_consumed"])

    async def test_consumed_undispatched_cancellation_reports_authority_truthfully(
        self,
    ):
        created = await self.create()
        await self.grant(created)
        plan = self.repository.get(created["plan_id"])
        task = self.service._create_task_for_plan(
            plan, created["plan_hash"]
        )
        self.service._record_task_event(
            task,
            "preflight_started",
            new_state=ExecutionTaskState.PREFLIGHT,
            changes={"started_at": self.service._timestamp()},
        )
        plan.approval.state = ApprovalState.CONSUMED
        plan.approval.consumed_at = self.service._timestamp()
        self.repository.save(plan)
        self.service._record_task_event(
            task,
            "approval_consumed",
            changes={
                "approval_reference": (
                    self.service._task_approval_reference(plan)
                )
            },
        )

        cancelled = await self.service.cancel_execution_task(task.task_id)
        persisted = self.service.get_execution_task(task.task_id)

        self.assertEqual(cancelled["status"], "cancelled_pre_dispatch")
        self.assertTrue(cancelled["approval_consumed"])
        self.assertEqual(persisted["state"], "cancelled_pre_dispatch")
        self.assertEqual(
            persisted["approval_reference"]["approval_state"], "consumed"
        )
        self.assertEqual(persisted["provider_attempt_count"], 0)
        self.assertFalse(cancelled["provider_dispatch_occurred"])
        self.assertFalse(
            any(
                event["event_type"] == "dispatch_attempted"
                for event in persisted["lifecycle_events"]
            )
        )
        self.assertEqual(self.gateway.writes, 0)
        audit = [
            json.loads(line)
            for line in self.audit_path.read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        cancellation_audit = next(
            entry
            for entry in reversed(audit)
            if entry.get("task_id") == task.task_id
            and entry.get("event") == "task_cancelled_pre_dispatch"
        )
        self.assertTrue(cancellation_audit["approval_consumed"])
        self.assertFalse(
            cancellation_audit["provider_dispatch_occurred"]
        )

    async def test_task_rehydrates_after_service_recreation(self):
        created = await self.create()
        await self.grant(created)
        applied = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        original = self.service.get_execution_task(applied["task_id"])

        recreated = type(self.service)(
            self.repository,
            self.gateway,
            now=self.clock,
        )
        restored = recreated.get_execution_task(applied["task_id"])

        self.assertEqual(restored, original)
        self.assertEqual(self.gateway.writes, 1)

    async def test_post_dispatch_deadline_moves_unresolved_task_to_manual_review(
        self,
    ):
        created = await self.create()
        await self.grant(created)
        plan = self.repository.get(created["plan_id"])
        task = self.service._create_task_for_plan(
            plan, created["plan_hash"]
        )
        self.service._record_task_event(
            task,
            "preflight_started",
            new_state=ExecutionTaskState.PREFLIGHT,
            changes={"started_at": self.service._timestamp()},
        )
        plan.approval.state = ApprovalState.CONSUMED
        plan.approval.consumed_at = self.service._timestamp()
        self.repository.save(plan)
        self.service._record_task_event(
            task,
            "approval_consumed",
            changes={
                "approval_reference": (
                    self.service._task_approval_reference(plan)
                )
            },
        )
        dispatched_at = self.service._timestamp()
        self.service._record_task_event(
            task,
            "dispatch_attempted",
            new_state=ExecutionTaskState.DISPATCHING,
            changes={
                "dispatched_at": dispatched_at,
                "maximum_post_dispatch_deadline": (
                    self.clock.value + timedelta(hours=24)
                ).isoformat(),
                "provider_attempts": [
                    {
                        "attempt": 1,
                        "attempted_at": dispatched_at,
                        "provider": "test_provider",
                        "response_received": False,
                    }
                ],
            },
        )
        self.clock.advance(hours=24, seconds=1)

        result = await self.service.reconcile_execution_tasks(
            trigger="startup"
        )
        restored = self.service.get_execution_task(task.task_id)

        self.assertEqual(result["manual_review_required"], 1)
        self.assertEqual(restored["state"], "manual_review_required")
        self.assertEqual(
            restored["manual_review_reason"],
            "maximum_post_dispatch_deadline_exceeded",
        )
        self.assertEqual(self.gateway.writes, 0)

    async def test_deadline_boundary_is_immutable_and_inclusive(self):
        created = await self.create()
        await self.grant(created)
        plan = self.repository.get(created["plan_id"])
        task = self.service._create_task_for_plan(
            plan, created["plan_hash"]
        )
        self.service._record_task_event(
            task,
            "preflight_started",
            new_state=ExecutionTaskState.PREFLIGHT,
            changes={"started_at": self.service._timestamp()},
        )
        plan.approval.state = ApprovalState.CONSUMED
        plan.approval.consumed_at = self.service._timestamp()
        self.repository.save(plan)
        self.service._record_task_event(
            task,
            "approval_consumed",
            changes={
                "approval_reference": (
                    self.service._task_approval_reference(plan)
                )
            },
        )
        dispatched_at = self.service._timestamp()
        deadline = (
            self.clock.value + timedelta(hours=24)
        ).isoformat()
        self.service._record_task_event(
            task,
            "dispatch_attempted",
            new_state=ExecutionTaskState.DISPATCHING,
            changes={
                "dispatched_at": dispatched_at,
                "maximum_post_dispatch_deadline": deadline,
                "provider_attempts": [
                    {
                        "attempt": 1,
                        "attempted_at": dispatched_at,
                        "provider": "test_provider",
                        "response_received": False,
                    }
                ],
            },
        )
        self.clock.advance(hours=24)

        await self.service.reconcile_execution_tasks(trigger="startup")
        restored = self.service.get_execution_task(task.task_id)

        self.assertEqual(restored["state"], "manual_review_required")
        self.assertEqual(
            restored["maximum_post_dispatch_deadline"], deadline
        )

    async def test_persisted_terminal_plan_wins_before_deadline_manual_review(
        self,
    ):
        created = await self.create()
        await self.grant(created)
        plan = self.repository.get(created["plan_id"])
        task = self.service._create_task_for_plan(
            plan, created["plan_hash"]
        )
        self.service._record_task_event(
            task,
            "preflight_started",
            new_state=ExecutionTaskState.PREFLIGHT,
            changes={"started_at": self.service._timestamp()},
        )
        plan.approval.state = ApprovalState.CONSUMED
        plan.approval.consumed_at = self.service._timestamp()
        self.repository.save(plan)
        self.service._record_task_event(
            task,
            "approval_consumed",
            changes={
                "approval_reference": (
                    self.service._task_approval_reference(plan)
                )
            },
        )
        dispatched_at = self.service._timestamp()
        self.service._record_task_event(
            task,
            "dispatch_attempted",
            new_state=ExecutionTaskState.DISPATCHING,
            changes={
                "dispatched_at": dispatched_at,
                "maximum_post_dispatch_deadline": (
                    self.clock.value + timedelta(hours=24)
                ).isoformat(),
                "provider_attempts": [
                    {
                        "attempt": 1,
                        "attempted_at": dispatched_at,
                        "provider": "test_provider",
                        "response_received": True,
                    }
                ],
            },
        )
        plan = self.repository.get(created["plan_id"])
        plan.status = PlanStatus.APPLIED
        plan.execution_outcome = "applied_verified"
        self.repository.save(plan)
        self.clock.advance(hours=24)

        result = await self.service.reconcile_execution_tasks(
            trigger="startup"
        )
        restored = self.service.get_execution_task(task.task_id)

        self.assertEqual(result["completed"], 1)
        self.assertEqual(restored["state"], "succeeded_verified")
        self.assertEqual(self.gateway.writes, 0)


class DurableOperationalTaskRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = LifecycleClock()
        self.lifecycle = FakeLifecycleGateway()
        self.lifecycle.now = self.clock
        self.root = Path(self.temp.name) / "plans"
        self.repository = ChangePlanRepository(self.root)
        self.audit_path = Path(self.temp.name) / "audit.jsonl"
        self.service = ChangeGovernanceService(
            self.repository,
            LegacyGateway(),
            AuditLogger(str(self.audit_path), "task-recovery-secret"),
            now=self.clock,
            sensitive_values=("task-recovery-secret",),
            lifecycle_gateway=self.lifecycle,
        )
        self.telemetry, self.context = begin_request("task-recovery")
        self.telemetry.caller_id = "mcp-requester"

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    async def _approved_reload(self):
        created = await self.service.create_reload_plan(
            reload_target="automation"
        )
        plan = created["plan"]
        pending = self.service.approve(
            plan["plan_id"], plan["plan_hash"]
        )
        _, csrf = await self.service.issue_external_csrf(
            plan["plan_id"], pending["challenge_id"]
        )
        await self.service.decide_external_approval(
            plan_id=plan["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=plan["plan_hash"],
            approval_kind="apply",
            csrf_nonce=csrf,
            decision="approve",
            approver_principal="home_assistant_admin_ingress:fixture",
        )
        return plan

    async def test_lost_provider_response_rehydrates_and_verifies_without_redispatch(
        self,
    ):
        plan = await self._approved_reload()
        self.lifecycle.mode = "ambiguous"
        self.lifecycle.verification_status = "pending"

        initial = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertNotEqual(initial["status"], "applied")
        task = self.service.task_repository.get_for_plan(plan["plan_id"])
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.state, ExecutionTaskState.OBSERVING)
        self.assertEqual(self.lifecycle.dispatch_count, 1)

        recovered = ChangeGovernanceService(
            self.repository,
            LegacyGateway(),
            AuditLogger(str(self.audit_path), "task-recovery-secret"),
            now=self.clock,
            sensitive_values=("task-recovery-secret",),
            lifecycle_gateway=self.lifecycle,
        )
        self.lifecycle.mode = "success"
        self.lifecycle.verification_status = "verified"

        rehydrated = await recovered.reconcile_execution_tasks(
            trigger="startup"
        )
        reconciled = await recovered.reconcile_operational_plans(
            trigger="startup"
        )
        restored = recovered.get_execution_task(task.task_id)

        self.assertEqual(rehydrated["provider_dispatches"], 0)
        self.assertEqual(reconciled["completed"], 1)
        self.assertEqual(restored["state"], "succeeded_verified")
        self.assertEqual(self.lifecycle.dispatch_count, 1)

    async def test_post_dispatch_cancel_is_rejected_and_verification_continues(
        self,
    ):
        plan = await self._approved_reload()
        self.lifecycle.mode = "ambiguous"
        self.lifecycle.verification_status = "pending"
        initial = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertNotEqual(initial["status"], "applied")
        task = self.service.task_repository.get_for_plan(plan["plan_id"])
        assert task is not None

        with self.assertRaises(GovernanceError) as rejected:
            await self.service.cancel_execution_task(task.task_id)
        self.assertEqual(
            rejected.exception.code,
            ErrorCode.CANCELLATION_NOT_PERMITTED_AFTER_DISPATCH,
        )
        self.assertEqual(self.lifecycle.dispatch_count, 1)

        self.lifecycle.mode = "success"
        self.lifecycle.verification_status = "verified"
        result = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(self.lifecycle.dispatch_count, 1)
        self.assertGreaterEqual(
            self.service.health_summary()["execution_tasks"][
                "no_blind_redispatch_preventions"
            ],
            1,
        )


if __name__ == "__main__":
    unittest.main()
