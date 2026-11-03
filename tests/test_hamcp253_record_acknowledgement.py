"""HAMCP-253: inspect and acknowledge stuck governance records.

Inspection returns bounded metadata for one exact plan or execution task,
including records that other plan and task tools refuse. An administrator
acknowledgement appends one entry to a separate hash-chained ledger. It only
decides whether health still warns about a record that is already stuck. It
never writes the plan or task, calls Home Assistant, or changes what may be
approved, applied or recovered.

Every fixture below is produced by a real governance code path, named in a
provenance comment. All data is synthetic and offline. No live Home Assistant,
provider or MCP endpoint is contacted, and every Home Assistant client entry
point is patched to fail the test if it is reached.
"""

from __future__ import annotations

import contextlib
import copy
from datetime import timedelta
import fcntl
import inspect
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.clients.rest import HomeAssistantRestClient  # noqa: E402
from ha_mcp_engineering.clients.websocket import (  # noqa: E402
    HomeAssistantWebSocketClient,
)
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance import (  # noqa: E402
    record_acknowledgement as ledger_module,
)
from ha_mcp_engineering.governance import record_inspection  # noqa: E402
from ha_mcp_engineering.governance.models import PlanStatus  # noqa: E402
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
    RESTART_VERIFICATION_WINDOW_EXPIRED,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.providers.dispatch import (  # noqa: E402
    CanonicalProviderDispatcher,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)
from ha_mcp_engineering.tools import (  # noqa: E402
    get_registered_server,
    governance as governance_tools,
    registered_tools,
)
from tests.test_2_1a_beta2_operational_lifecycle import (  # noqa: E402
    Clock as LifecycleClock,
    FakeLifecycleGateway,
    LegacyGateway,
    SelfRestartRecoveryGateway,
)
from tests.test_governance import (  # noqa: E402
    CURRENT,
    FakeGateway,
    GovernanceTestCase,
)


ADMIN = "home_assistant_admin_ingress:hamcp253-admin"
# Configured as a sensitive value of the service under test.
SECRET = "SYNTHETIC-SECRET-hamcp253-0001"
# Never configured: it only appears inside a plan's proposed configuration.
CONFIG_SECRET = "SYNTHETIC-SECRET-hamcp253-0002"
TOKEN_SHAPED_REASONS = (
    "rotated Bearer SYNTHETIC-bearer-hamcp253-0003",
    "saw eyJSYNTHETIC-hamcp253.SYNTHETIC-payload.SYNTHETIC-signature here",
)
SENSITIVE_VALUES = ("test-access-secret-value", "test-ha-token-value", SECRET)
REASON = "Reviewed: historical record from before HAMCP-253"
EVENT = "governance_record_acknowledgement"
HEX32 = re.compile(r"^[a-f0-9]{32}$")
HEX64 = re.compile(r"^[a-f0-9]{64}$")
APPROVAL_EVENT = re.compile(r"(approv|reject|challenge|supersed|prohibit)")
PROJECTION_WARNING = "one_or_more_governance_plans_could_not_be_projected"
MANUAL_REVIEW_WARNING = "one_or_more_execution_tasks_require_manual_review"


class CountingGateway(FakeGateway):
    """``tests.test_governance.FakeGateway`` that records every call."""

    def __init__(self, configs=None):
        super().__init__(configs)
        self.calls: list[str] = []

    async def get(self, automation_id):
        self.calls.append("get")
        return await super().get(automation_id)

    async def write(self, automation_id, config):
        self.calls.append("write")
        return await super().write(automation_id, config)

    async def validate(self):
        self.calls.append("validate")
        return await super().validate()


@contextlib.contextmanager
def no_home_assistant(test: unittest.TestCase):
    """Fail if any Home Assistant client or provider dispatcher is reached."""

    targets = (
        (HomeAssistantRestClient, "request"),
        (HomeAssistantWebSocketClient, "command"),
        (HomeAssistantWebSocketClient, "command_batch"),
        (CanonicalProviderDispatcher, "execute"),
    )
    mocks = []
    with contextlib.ExitStack() as stack:
        for owner, name in targets:
            mock = AsyncMock(
                side_effect=AssertionError(f"HAMCP-253 reached {owner.__name__}.{name}")
            )
            stack.enter_context(patch.object(owner, name, mock))
            mocks.append(mock)
        yield mocks
    for mock in mocks:
        test.assertEqual(mock.await_count, 0)
        test.assertEqual(mock.call_count, 0)


async def outcome_of(operation):
    """The error code a governance call refuses with, or what it returned."""

    try:
        result = operation()
        if inspect.isawaitable(result):
            result = await result
    except GovernanceError as exc:
        return exc.code
    return ("returned", result.get("status") if isinstance(result, dict) else None)


def empty_acknowledgement_block():
    """An acknowledgement view that saw no stuck records at all."""

    def empty():
        return {
            "unacknowledged_count": 0, "acknowledged_historical_count": 0,
            "acknowledged_current_defect_count": 0, "stale_acknowledgement_count": 0,
            "unacknowledged_ids": [], "acknowledged_historical_ids": [],
            "acknowledged_current_defect_ids": [], "ids_truncated": False,
        }

    return {
        "model": ledger_module.MODEL, "ledger_status": "ok", "ledger_entry_count": 0,
        "manual_review_index_status": "ok",
        "projection_failed_plans": empty(), "manual_review_tasks": empty(),
        "acknowledgement_affects_execution": False,
    }


class Hamcp253Case(GovernanceTestCase):
    """``GovernanceTestCase`` with a counting gateway and a configured secret."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.gateway = CountingGateway({"porch": CURRENT})
        self.service = self.build_service(self.repository)

    def build_service(self, repository):
        # Provenance: the same composition as GovernanceTestCase.asyncSetUp,
        # with one more configured synthetic sensitive value.
        return ChangeGovernanceService(
            repository,
            self.gateway,
            AuditLogger(str(self.audit_path), "test-access-secret-value"),
            now=self.clock,
            sensitive_values=SENSITIVE_VALUES,
        )

    def restart(self):
        """A new process over the same storage root (hamcp198 ``health``)."""

        self.repository = ChangePlanRepository(self.repository.root)
        self.service = self.build_service(self.repository)
        return self.service

    # -- fixtures -------------------------------------------------------

    async def failed_plan(self, proposed=None):
        # Provenance: tests/test_governance.py
        # test_home_assistant_write_failure_consumes_approval. A real
        # externally approved plan whose fake write raises during apply.
        created, _ = await self.approved_plan(proposed)
        self.gateway.fail_write = True
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.AUTOMATION_APPLY_FAILED)
        self.gateway.fail_write = False
        self.assertEqual(self.repository.get(created["plan_id"]).status, PlanStatus.FAILED)
        return created

    async def rejected_plan(self):
        # Provenance: tests/test_beta25_external_approval.py
        # test_rejection_is_terminal_and_historical (Ingress reject decision).
        created = await self.update_plan()
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
            approver_principal="home_assistant_admin_ingress:hamcp253-rejector",
        )
        self.assertEqual(self.repository.get(created["plan_id"]).status, PlanStatus.REJECTED)
        return created

    async def prohibited_plan(self):
        # Provenance: tests/test_f2_policy_approval.py create_prohibited. The
        # F2 policy classifies a lock.unlock action as prohibited.
        proposed = copy.deepcopy(CURRENT)
        proposed["action"] = [
            {"service": "lock.unlock", "target": {"entity_id": "lock.hamcp253_fixture"}}
        ]
        created = await self.update_plan(proposed)
        self.assertEqual(created["policy_decision"]["policy_class"], "prohibited")
        return created

    def corrupt_subject(self, plan_id):
        # Provenance: tests/test_hamcp198_projection_diagnostics.py
        # Base.corrupt_subject. Yields policy_snapshot_mismatch with category
        # policy_subject_hash_not_reproducible.
        path = self.repository._path(plan_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["policy_decision"]["policy_subject_hash"] = "0" * 64
        path.write_text(json.dumps(data), encoding="utf-8")

    async def projection_failed_plans(self, count=1):
        ids = [(await self.update_plan())["plan_id"] for _ in range(count)]
        for plan_id in ids:
            self.corrupt_subject(plan_id)
        # The projection failure index is built when the service starts.
        self.restart()
        return sorted(ids)

    async def approval_sequence_plan(self):
        # Provenance: a real awaiting-approval plan whose stored bundle state
        # claims "fully_approved" while approval.state is still "required".
        # ChangeGovernanceService._approval_bundle_integrity_error reports
        # approval_sequence_failure for exactly that pair; the policy snapshot
        # itself stays intact.
        created = await self.update_plan()
        path = self.repository._path(created["plan_id"])
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["approval"]["state"], "required")
        data["approval"]["bundle_state"] = "fully_approved"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.restart()
        return created["plan_id"]

    def append_events(self, plan_id, count, name="hamcp253_synthetic_event"):
        # Provenance: ChangeGovernanceService._record is the event writer every
        # governance transition uses; ChangePlanRepository.save persists it.
        plan = self.repository.get(plan_id)
        for _ in range(count):
            self.service._record(plan, name, "success")
        self.repository.save(plan)

    # -- observation helpers ---------------------------------------------

    def inspect_record(self, kind, record_id, service=None):
        return (service or self.service).inspect_governance_record(
            record_kind=kind, record_id=record_id
        )

    def acknowledge(
        self,
        kind,
        record_id,
        *,
        action="acknowledge",
        classification="historical_record",
        reason=REASON,
        fingerprint=None,
        principal=ADMIN,
        service=None,
    ):
        service = service or self.service
        if fingerprint is None:
            fingerprint = self.inspect_record(kind, record_id, service)["record_fingerprint"]
        return service.acknowledge_governance_record(
            record_kind=kind,
            record_id=record_id,
            action=action,
            classification=classification,
            reason=reason,
            record_fingerprint=fingerprint,
            principal=principal,
        )

    def ledger_path(self):
        return self.repository.root / ledger_module.MODEL / "acknowledgements.json"

    def ledger_events(self):
        path = self.ledger_path()
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))["events"]

    def audits(self):
        if not self.audit_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def acknowledgement_audits(self):
        return [entry for entry in self.audits() if entry.get("event") == EVENT]

    def plan_bytes(self, plan_id):
        return self.repository._path(plan_id).read_bytes()

    def bucket(self, health, key="projection_failed_plans"):
        return health["record_acknowledgement"][key]

    def assert_success(self, outcome, status):
        self.assertEqual(outcome["status"], status)
        self.assertIsNone(outcome["refusal_reason"])
        self.assertFalse(outcome["record_written"])
        self.assertEqual(outcome["home_assistant_calls"], 0)
        self.assertIs(outcome["execution_eligibility_changed"], False)
        self.assertTrue(outcome["audit_recorded"])

    def assert_refused(self, outcome, reason, *, ledger_before=0, audits_before=None):
        self.assertEqual(outcome["status"], "refused")
        self.assertEqual(outcome["refusal_reason"], reason)
        self.assertIsNone(outcome["ledger_sequence"])
        self.assertFalse(outcome["record_written"])
        self.assertEqual(outcome["home_assistant_calls"], 0)
        self.assertIs(outcome["execution_eligibility_changed"], False)
        self.assertTrue(outcome["audit_recorded"])
        self.assertEqual(len(self.ledger_events()), ledger_before)
        audits = self.acknowledgement_audits()
        if audits_before is not None:
            self.assertEqual(len(audits), audits_before + 1)
        self.assertEqual(audits[-1]["result_status"], "refused")
        self.assertEqual(audits[-1]["error_code"], reason)
        self.assertEqual(audits[-1]["access"], "refused")


class InspectionTests(Hamcp253Case):
    """Positive inspection of every stuck-record category."""

    def assert_plan_view(self, view, plan_id):
        stored = self.repository.get(plan_id)
        self.assertEqual(view["inspection_model"], record_inspection.INSPECTION_MODEL)
        self.assertEqual(view["record_kind"], "change_plan")
        self.assertEqual(view["record_id"], plan_id)
        self.assertIs(view["readable"], True)
        self.assertEqual(view["record_type"], stored.operation.value)
        self.assertEqual(view["status"], stored.status.value)
        self.assertEqual(view["created_at"], stored.created_at)
        self.assertEqual(view["updated_at"], stored.updated_at)
        self.assertEqual(view["policy_class"], stored.policy_decision.policy_class.value)
        self.assertEqual(view["event_count"], len(stored.events))
        self.assertEqual(
            [row["event"] for row in view["events"]],
            [event.event for event in stored.events][: record_inspection.MAX_INSPECTED_EVENTS],
        )
        self.assertEqual(
            [row["order"] for row in view["events"]], list(range(1, len(view["events"]) + 1))
        )
        self.assertEqual(
            view["approval_event_sequence"],
            [event.event for event in stored.events if APPROVAL_EVENT.search(event.event)],
        )
        self.assertIs(view["events_truncated"], False)
        self.assertRegex(view["event_sequence_digest"], HEX64)
        self.assertEqual(view["unreadable_fields"], [])
        self.assertIs(view["acknowledgeable"], True)
        self.assertRegex(view["record_fingerprint"], HEX64)
        self.assertIs(view["payload_included"], False)
        self.assertEqual(
            view["acknowledgement"],
            {
                "state": "unacknowledged",
                "classification": None,
                "recorded_at": None,
                "history_count": 0,
                "ledger_status": "ok",
            },
        )
        # Metadata only: no configuration, target or note is ever included.
        serialized = json.dumps(view)
        for absent in ("proposed_config", "current_config", "Porch light", "porch",
                       "binary_sensor.motion", "Reviewed in test Ingress"):
            self.assertNotIn(absent, serialized)
        return stored

    async def test_projection_failed_plan(self):
        (plan_id,) = await self.projection_failed_plans()
        calls = list(self.gateway.calls)
        with no_home_assistant(self):
            view = self.inspect_record("change_plan", plan_id)
        self.assert_plan_view(view, plan_id)
        self.assertEqual(view["status"], "awaiting_approval")
        self.assertEqual(
            view["failure"]["reason_code"], ErrorCode.POLICY_SNAPSHOT_MISMATCH.value
        )
        self.assertEqual(view["failure"]["category"], "policy_subject_hash_not_reproducible")
        self.assertIs(view["failure"]["projection_failed"], True)
        self.assertEqual(self.gateway.calls, calls)

    async def test_approval_sequence_inconsistent_plan(self):
        plan_id = await self.approval_sequence_plan()
        with no_home_assistant(self):
            view = self.inspect_record("change_plan", plan_id)
        self.assert_plan_view(view, plan_id)
        self.assertEqual(
            view["failure"]["reason_code"], ErrorCode.APPROVAL_SEQUENCE_FAILURE.value
        )
        self.assertEqual(view["failure"]["category"], "approval_sequence_inconsistent")
        self.assertIs(view["failure"]["projection_failed"], True)
        self.assertEqual(view["approval"]["bundle_state"], "fully_approved")
        self.assertEqual(view["approval"]["state"], "required")

    async def test_prohibited_terminal_plan(self):
        created = await self.prohibited_plan()
        calls = list(self.gateway.calls)
        with no_home_assistant(self):
            view = self.inspect_record("change_plan", created["plan_id"])
        self.assert_plan_view(view, created["plan_id"])
        self.assertEqual(view["policy_class"], "prohibited")
        self.assertIs(view["terminal"], True)
        self.assertIs(view["failure"]["projection_failed"], False)
        self.assertIsNone(view["failure"]["category"])
        self.assertEqual(self.gateway.calls, calls)

    async def test_rejected_plan(self):
        created = await self.rejected_plan()
        with no_home_assistant(self):
            view = self.inspect_record("change_plan", created["plan_id"])
        self.assert_plan_view(view, created["plan_id"])
        self.assertEqual(view["status"], "rejected")
        self.assertIs(view["failure"]["projection_failed"], False)
        self.assertIsNone(view["failure"]["category"])
        self.assertTrue(any("reject" in name for name in view["approval_event_sequence"]))

    async def test_failed_plan(self):
        created = await self.failed_plan()
        calls = list(self.gateway.calls)
        with no_home_assistant(self):
            view = self.inspect_record("change_plan", created["plan_id"])
        self.assert_plan_view(view, created["plan_id"])
        self.assertEqual(view["status"], "failed")
        self.assertIs(view["failure"]["projection_failed"], False)
        self.assertIn(
            ErrorCode.AUTOMATION_APPLY_FAILED.value,
            [row["error_code"] for row in view["events"]],
        )
        self.assertEqual(self.gateway.calls, calls)

    async def test_healthy_plan_is_inspectable_but_not_acknowledgeable(self):
        created = await self.update_plan()
        view = self.inspect_record("change_plan", created["plan_id"])
        self.assertIs(view["readable"], True)
        self.assertIs(view["acknowledgeable"], False)
        self.assertEqual(view["status"], "awaiting_approval")
        self.assertIs(view["failure"]["projection_failed"], False)

    async def test_inspection_works_on_a_plan_that_get_change_plan_refuses(self):
        (plan_id,) = await self.projection_failed_plans()
        before = self.plan_bytes(plan_id)
        with self.assertRaises(GovernanceError) as refused:
            self.service.get_plan(plan_id)
        self.assertEqual(refused.exception.code, ErrorCode.POLICY_SNAPSHOT_MISMATCH)
        view = self.inspect_record("change_plan", plan_id)
        self.assertIs(view["readable"], True)
        self.assertIs(view["acknowledgeable"], True)
        self.assertEqual(self.plan_bytes(plan_id), before)

    async def test_inspection_is_repeatable_and_fingerprint_is_stable(self):
        (plan_id,) = await self.projection_failed_plans()
        first = self.inspect_record("change_plan", plan_id)
        second = self.inspect_record("change_plan", plan_id)
        self.assertEqual(first, second)
        self.assertEqual(
            first["record_fingerprint"],
            record_inspection.record_fingerprint(
                {k: v for k, v in first.items() if k != "payload_included"}
            ),
        )

    async def test_corrupt_record_is_reported_unreadable(self):
        created = await self.update_plan()
        # Provenance: a stored plan file that no longer decodes as JSON.
        self.repository._path(created["plan_id"]).write_text("{not json", encoding="utf-8")
        with no_home_assistant(self):
            view = self.inspect_record("change_plan", created["plan_id"])
        self.assertIs(view["readable"], False)
        self.assertEqual(view["unreadable_reason"], ErrorCode.CHANGE_PLAN_STORAGE_ERROR.value)
        self.assertEqual(view["unreadable_fields"], ["record"])
        self.assertIs(view["acknowledgeable"], False)
        self.assertIsNone(view["record_fingerprint"])
        self.assertIs(view["payload_included"], False)
        self.assertEqual(view["acknowledgement"]["state"], "unacknowledged")

    async def test_corrupt_record_acknowledgement_is_refused_as_unreadable(self):
        created = await self.update_plan()
        self.repository._path(created["plan_id"]).write_text("{not json", encoding="utf-8")
        with no_home_assistant(self):
            outcome = self.acknowledge("change_plan", created["plan_id"], fingerprint="0" * 64)
        self.assert_refused(outcome, "record_unreadable")

    async def test_repeated_inspection_never_moves_a_corrupt_record(self):
        # Regression: inspection used ChangePlanRepository.get, which
        # quarantined the file, so the second inspection reported not found.
        created = await self.update_plan()
        path = self.repository._path(created["plan_id"])
        path.write_text("{not json", encoding="utf-8")
        for _ in range(2):
            with no_home_assistant(self):
                view = self.inspect_record("change_plan", created["plan_id"])
            self.assertIs(view["readable"], False)
            self.assertEqual(view["unreadable_reason"], ErrorCode.CHANGE_PLAN_STORAGE_ERROR.value)
        outcome = self.acknowledge("change_plan", created["plan_id"], fingerprint="0" * 64)
        self.assert_refused(outcome, "record_unreadable")
        self.assertEqual(path.read_bytes(), b"{not json")
        self.assertEqual(list(self.repository.quarantine.glob("*")), [])

    async def test_corrupt_plan_acknowledged_as_task_is_a_kind_mismatch(self):
        created = await self.update_plan()
        self.repository._path(created["plan_id"]).write_text("{not json", encoding="utf-8")
        outcome = self.acknowledge("execution_task", created["plan_id"], fingerprint="0" * 64)
        self.assert_refused(outcome, "record_kind_mismatch")
        self.assertEqual(list(self.repository.quarantine.glob("*")), [])

    async def test_approval_sequence_covers_events_beyond_the_displayed_rows(self):
        # Regression: the approval sequence was filtered from the 64
        # displayed rows, so a later approval event disappeared.
        created = await self.update_plan()
        self.append_events(created["plan_id"], 70)
        self.append_events(created["plan_id"], 1, name="hamcp253_approval_late")
        view = self.inspect_record("change_plan", created["plan_id"])
        self.assertIs(view["events_truncated"], True)
        self.assertNotIn("hamcp253_approval_late", [row["event"] for row in view["events"]])
        self.assertEqual(view["approval_event_sequence"][-1], "hamcp253_approval_late")
        self.assertEqual(view["approval_event_count"], len(view["approval_event_sequence"]))
        self.assertIs(view["approval_events_truncated"], False)

    async def test_approval_sequence_has_its_own_bound(self):
        created = await self.update_plan()
        self.append_events(created["plan_id"], 70, name="hamcp253_approval_synthetic")
        view = self.inspect_record("change_plan", created["plan_id"])
        limit = record_inspection.MAX_INSPECTED_EVENTS
        self.assertEqual(len(view["approval_event_sequence"]), limit)
        self.assertGreater(view["approval_event_count"], limit)
        self.assertIs(view["approval_events_truncated"], True)

    async def test_more_than_64_events_are_truncated_with_exact_count(self):
        created = await self.update_plan()
        self.append_events(created["plan_id"], 70)
        stored = self.repository.get(created["plan_id"])
        self.assertGreater(len(stored.events), record_inspection.MAX_INSPECTED_EVENTS)
        view = self.inspect_record("change_plan", created["plan_id"])
        self.assertIs(view["events_truncated"], True)
        self.assertEqual(view["event_count"], len(stored.events))
        self.assertEqual(len(view["events"]), record_inspection.MAX_INSPECTED_EVENTS)
        self.assertEqual(
            [row["order"] for row in view["events"]],
            list(range(1, record_inspection.MAX_INSPECTED_EVENTS + 1)),
        )
        # The digest covers every event, so a hidden later event still
        # changes the fingerprint.
        before = view["record_fingerprint"]
        self.append_events(created["plan_id"], 1, name="hamcp253_hidden_later_event")
        after = self.inspect_record("change_plan", created["plan_id"])
        self.assertEqual(after["events"], view["events"])
        self.assertNotEqual(after["event_sequence_digest"], view["event_sequence_digest"])
        self.assertNotEqual(after["record_fingerprint"], before)

    async def test_inspection_refuses_invalid_and_missing_identifiers(self):
        cases = (
            (("automation", "a" * 32), ErrorCode.INVALID_REQUEST),
            (("change_plan", "A" * 32), ErrorCode.INVALID_REQUEST),
            (("change_plan", "../" + "a" * 29), ErrorCode.INVALID_REQUEST),
            (("change_plan", "0" * 32), ErrorCode.CHANGE_PLAN_NOT_FOUND),
            (("execution_task", "0" * 32), ErrorCode.EXECUTION_TASK_NOT_FOUND),
        )
        for (kind, record_id), code in cases:
            with self.subTest(kind=kind, record_id=record_id):
                with self.assertRaises(GovernanceError) as raised:
                    self.inspect_record(kind, record_id)
                self.assertEqual(raised.exception.code, code)


class AcknowledgementLifecycleTests(Hamcp253Case):
    async def test_acknowledging_a_historical_record_clears_only_its_warning(self):
        plan_ids = await self.projection_failed_plans(2)
        target, other = plan_ids
        calls = list(self.gateway.calls)
        before_health = self.service.health_summary()
        self.assertEqual(before_health["projection_failure_warning"], PROJECTION_WARNING)
        self.assertEqual(self.bucket(before_health)["unacknowledged_count"], 2)
        self.assertEqual(self.bucket(before_health)["acknowledged_historical_count"], 0)
        self.assertEqual(self.bucket(before_health)["unacknowledged_ids"], plan_ids)
        before = {plan_id: self.plan_bytes(plan_id) for plan_id in plan_ids}

        with no_home_assistant(self):
            outcome = self.acknowledge("change_plan", target)
        self.assert_success(outcome, "acknowledged")
        self.assertEqual(outcome["ledger_sequence"], 1)
        self.assertEqual(outcome["classification"], "historical_record")

        partial = self.service.health_summary()
        self.assertEqual(partial["projection_failure_warning"], PROJECTION_WARNING)
        self.assertEqual(self.bucket(partial)["unacknowledged_ids"], [other])
        self.assertEqual(self.bucket(partial)["acknowledged_historical_ids"], [target])

        with no_home_assistant(self):
            self.assert_success(self.acknowledge("change_plan", other), "acknowledged")
        health = self.service.health_summary()
        self.assertIsNone(health["projection_failure_warning"])
        self.assertEqual(self.bucket(health)["unacknowledged_count"], 0)
        self.assertEqual(self.bucket(health)["acknowledged_historical_count"], 2)
        self.assertEqual(self.bucket(health)["acknowledged_historical_ids"], plan_ids)
        self.assertIs(health["record_acknowledgement"]["acknowledgement_affects_execution"], False)
        self.assertEqual(health["record_acknowledgement"]["ledger_status"], "ok")
        self.assertEqual(health["record_acknowledgement"]["ledger_entry_count"], 2)
        # Raw diagnosis is unchanged; only the warning overlay moves.
        self.assertEqual(health["projection_failure_count"], 2)
        # The plans themselves were never rewritten.
        self.assertEqual({plan_id: self.plan_bytes(plan_id) for plan_id in plan_ids}, before)
        self.assertEqual(self.gateway.calls, calls)

        view = self.inspect_record("change_plan", target)
        self.assertEqual(view["acknowledgement"]["state"], "acknowledged")
        self.assertEqual(view["acknowledgement"]["classification"], "historical_record")
        self.assertEqual(view["acknowledgement"]["history_count"], 1)

        events = self.ledger_events()
        self.assertEqual([event["sequence"] for event in events], [1, 2])
        self.assertEqual(events[0]["previous"], "0" * 64)
        self.assertEqual(events[1]["previous"], events[0]["hash"])
        self.assertEqual(events[0]["body"]["principal"], ADMIN)
        self.assertEqual(events[0]["body"]["reason"], REASON)

    async def test_reclassify_revoke_and_repeat(self):
        (plan_id,) = await self.projection_failed_plans()
        audits = len(self.acknowledgement_audits())

        self.assert_success(self.acknowledge("change_plan", plan_id), "acknowledged")
        self.assertEqual(len(self.ledger_events()), 1)

        repeat = self.acknowledge("change_plan", plan_id)
        self.assert_success(repeat, "no_op")
        self.assertIsNone(repeat["ledger_sequence"])
        self.assertEqual(len(self.ledger_events()), 1)

        reclassified = self.acknowledge(
            "change_plan", plan_id, classification="current_defect",
            reason="Reviewed again: still a current defect",
        )
        self.assert_success(reclassified, "reclassified")
        self.assertEqual(reclassified["ledger_sequence"], 2)
        health = self.service.health_summary()
        self.assertIsNone(health["projection_failure_warning"])
        self.assertEqual(self.bucket(health)["acknowledged_current_defect_count"], 1)
        self.assertEqual(self.bucket(health)["acknowledged_current_defect_ids"], [plan_id])
        self.assertEqual(self.bucket(health)["acknowledged_historical_count"], 0)
        self.assertEqual(self.bucket(health)["unacknowledged_count"], 0)
        view = self.inspect_record("change_plan", plan_id)
        self.assertEqual(view["acknowledgement"]["classification"], "current_defect")
        self.assertEqual(view["acknowledgement"]["history_count"], 2)

        revoked = self.acknowledge(
            "change_plan", plan_id, action="revoke", classification=None,
            reason="Withdrawn: needs another review",
        )
        self.assert_success(revoked, "revoked")
        self.assertEqual(revoked["ledger_sequence"], 3)
        health = self.service.health_summary()
        self.assertEqual(health["projection_failure_warning"], PROJECTION_WARNING)
        self.assertEqual(self.bucket(health)["unacknowledged_ids"], [plan_id])
        self.assertEqual(self.bucket(health)["acknowledged_current_defect_count"], 0)
        self.assertEqual(
            self.inspect_record("change_plan", plan_id)["acknowledgement"]["state"], "revoked"
        )

        again = self.acknowledge(
            "change_plan", plan_id, action="revoke", classification=None,
            reason="Withdrawn: needs another review",
        )
        self.assert_success(again, "no_op")
        self.assertEqual(len(self.ledger_events()), 3)

        recorded = self.acknowledgement_audits()[audits:]
        self.assertEqual(
            [(entry["result_status"], entry["error_code"]) for entry in recorded],
            [("acknowledged", None), ("no_op", None), ("reclassified", None),
             ("revoked", None), ("no_op", None)],
        )
        for entry in recorded:
            self.assertEqual(entry["caller_id"], ADMIN)
            self.assertIn("reason_length", entry["parameters"])
        # Audit records the reason length only, never the text.
        audit_text = self.audit_path.read_text(encoding="utf-8")
        self.assertNotIn(REASON, audit_text)
        self.assertNotIn("still a current defect", audit_text)

    async def test_acknowledgement_survives_restart(self):
        (plan_id,) = await self.projection_failed_plans()
        self.assert_success(self.acknowledge("change_plan", plan_id), "acknowledged")
        ledger = self.ledger_path().read_bytes()

        restarted = self.restart()
        view = self.inspect_record("change_plan", plan_id, restarted)
        self.assertEqual(view["acknowledgement"]["state"], "acknowledged")
        self.assertEqual(view["acknowledgement"]["classification"], "historical_record")
        health = restarted.health_summary()
        self.assertIsNone(health["projection_failure_warning"])
        self.assertEqual(self.bucket(health)["acknowledged_historical_ids"], [plan_id])
        self.assertEqual(health["record_acknowledgement"]["ledger_entry_count"], 1)
        self.assertEqual(self.ledger_path().read_bytes(), ledger)

    async def test_changed_record_makes_acknowledgement_stale_and_warns_again(self):
        (plan_id,) = await self.projection_failed_plans()
        reviewed = self.inspect_record("change_plan", plan_id)["record_fingerprint"]
        self.assert_success(self.acknowledge("change_plan", plan_id), "acknowledged")
        self.assertIsNone(self.service.health_summary()["projection_failure_warning"])

        self.append_events(plan_id, 1, name="hamcp253_later_event")
        restarted = self.restart()
        view = self.inspect_record("change_plan", plan_id, restarted)
        self.assertNotEqual(view["record_fingerprint"], reviewed)
        self.assertEqual(view["acknowledgement"]["state"], "stale")
        self.assertIsNone(view["acknowledgement"]["classification"])
        health = restarted.health_summary()
        self.assertEqual(health["projection_failure_warning"], PROJECTION_WARNING)
        self.assertEqual(self.bucket(health)["unacknowledged_ids"], [plan_id])
        self.assertEqual(self.bucket(health)["stale_acknowledgement_count"], 1)

        # The old fingerprint no longer names this record.
        stale = self.acknowledge("change_plan", plan_id, fingerprint=reviewed)
        self.assert_refused(stale, "record_fingerprint_stale", ledger_before=1)
        # A fresh review records a new acknowledgement, not a no-op.
        self.assert_success(self.acknowledge("change_plan", plan_id), "acknowledged")
        self.assertIsNone(restarted.health_summary()["projection_failure_warning"])

    async def test_non_ascii_500_character_reasons_are_accepted(self):
        # Regression: the ledger stores reasons ASCII-escaped, and a
        # 500-character astral reason exceeded the old 4 KiB event bound.
        created = await self.failed_plan()
        plan_id = created["plan_id"]
        self.assert_success(
            self.acknowledge("change_plan", plan_id, reason="\u00e9" * 500), "acknowledged"
        )
        self.assert_success(
            self.acknowledge("change_plan", plan_id, reason="\U0001f642" * 500), "reclassified"
        )
        events = self.ledger_events()
        self.assertEqual(events[-1]["body"]["reason"], "\U0001f642" * 500)
        self.assertLessEqual(
            max(len(ledger_module.encoded(event)) for event in events),
            ledger_module.MAX_EVENT_BYTES,
        )
        self.assertTrue(self.ledger_path().read_bytes().isascii())

    async def test_health_refreshes_a_stale_projection_index(self):
        # Regression: a navigation rebuild after the last projection pass
        # left the acknowledgement view on an outdated index.
        first, second = (await self.update_plan())["plan_id"], (await self.update_plan())["plan_id"]
        self.corrupt_subject(first)
        self.restart()
        self.corrupt_subject(second)
        self.repository.rebuild_navigation_index()
        stuck = self.service.stuck_governance_records()
        self.assertEqual(
            stuck["projection_failed_plans"]["unacknowledged_ids"], sorted([first, second])
        )

    async def test_inconsistent_acknowledgement_totals_keep_the_warning(self):
        (plan_id,) = await self.projection_failed_plans()
        self.assert_success(self.acknowledge("change_plan", plan_id), "acknowledged")
        health = self.service.health_summary()
        self.assertIsNone(health["projection_failure_warning"])
        self.assertIs(health["record_acknowledgement"]["counts_consistent"], True)
        with patch.object(self.service, "_acknowledgement_health",
                          return_value=empty_acknowledgement_block()):
            health = self.service.health_summary()
        self.assertEqual(health["projection_failure_count"], 1)
        self.assertEqual(health["projection_failure_warning"], PROJECTION_WARNING)
        self.assertIs(health["record_acknowledgement"]["counts_consistent"], False)

    async def test_health_id_lists_are_bounded(self):
        limit = record_inspection.MAX_HEALTH_ACKNOWLEDGEMENT_IDS
        ids = await self.projection_failed_plans(limit + 1)
        plans = self.bucket(self.service.health_summary())
        self.assertEqual(plans["unacknowledged_count"], limit + 1)
        self.assertEqual(plans["unacknowledged_ids"], ids[:limit])
        self.assertIs(plans["ids_truncated"], True)

    async def test_symlinked_ledger_namespace_is_refused(self):
        (plan_id,) = await self.projection_failed_plans()
        elsewhere = Path(self.repository.root).parent / "hamcp253-elsewhere"
        elsewhere.mkdir()
        os.symlink(elsewhere, self.repository.root / ledger_module.MODEL)
        outcome = self.acknowledge("change_plan", plan_id)
        self.assert_refused(outcome, "acknowledgement_namespace_invalid")
        self.assertEqual(list(elsewhere.iterdir()), [])
        health = self.service.health_summary()
        self.assertEqual(health["record_acknowledgement"]["ledger_status"], "unavailable")
        self.assertEqual(health["projection_failure_warning"], PROJECTION_WARNING)

    async def test_reason_of_exactly_500_characters_is_accepted(self):
        created = await self.failed_plan()
        outcome = self.acknowledge("change_plan", created["plan_id"], reason="r" * 500)
        self.assert_success(outcome, "acknowledged")
        self.assertEqual(len(self.ledger_events()[-1]["body"]["reason"]), 500)

    async def assert_eligibility_unchanged(self, created):
        plan_id, plan_hash = created["plan_id"], created["plan_hash"]
        writes = self.gateway.write_calls

        def approve():
            return self.service.approve(plan_id, plan_hash)

        def apply():
            return self.service.apply(plan_id, plan_hash)

        approve_before = await outcome_of(approve)
        apply_before = await outcome_of(apply)
        self.assertIsInstance(approve_before, ErrorCode)
        self.assertIsInstance(apply_before, ErrorCode)
        status_before = self.repository.get(plan_id).status

        # Review after the refused attempts so the fingerprint is current.
        with no_home_assistant(self):
            outcome = self.acknowledge("change_plan", plan_id)
        self.assert_success(outcome, "acknowledged")
        self.assertIs(outcome["execution_eligibility_changed"], False)

        self.assertEqual(await outcome_of(approve), approve_before)
        self.assertEqual(await outcome_of(apply), apply_before)
        self.assertEqual(self.gateway.write_calls, writes)
        self.assertEqual(self.repository.get(plan_id).status, status_before)
        # A restarted process reaches the same refusals.
        self.restart()
        self.assertEqual(await outcome_of(approve), approve_before)
        self.assertEqual(await outcome_of(apply), apply_before)
        self.assertEqual(self.gateway.write_calls, writes)
        return approve_before, apply_before

    async def test_acknowledged_failed_plan_stays_refused(self):
        await self.assert_eligibility_unchanged(await self.failed_plan())

    async def test_acknowledged_prohibited_plan_stays_refused(self):
        approve, apply = await self.assert_eligibility_unchanged(await self.prohibited_plan())
        self.assertEqual(approve, ErrorCode.PROHIBITED_CHANGE)
        self.assertEqual(apply, ErrorCode.PROHIBITED_CHANGE)

    async def test_acknowledged_rejected_plan_stays_refused(self):
        approve, _ = await self.assert_eligibility_unchanged(await self.rejected_plan())
        self.assertEqual(approve, ErrorCode.CHANGE_PLAN_REJECTED)


class AcknowledgementRefusalTests(Hamcp253Case):
    """Each refusal: exact reason, no ledger entry, one audit, no HA calls."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.target = (await self.failed_plan())["plan_id"]
        self.fingerprint = self.inspect_record("change_plan", self.target)["record_fingerprint"]
        self.plan_before = self.plan_bytes(self.target)

    def refuse(self, expected_refusal, *, ledger_before=0, **overrides):
        arguments = {
            "record_kind": "change_plan",
            "record_id": self.target,
            "action": "acknowledge",
            "classification": "historical_record",
            "reason": REASON,
            "record_fingerprint": self.fingerprint,
            "principal": ADMIN,
        }
        arguments.update(overrides)
        audits = len(self.acknowledgement_audits())
        calls = list(self.gateway.calls)
        with no_home_assistant(self):
            outcome = self.service.acknowledge_governance_record(**arguments)
        self.assert_refused(outcome, expected_refusal, ledger_before=ledger_before, audits_before=audits)
        self.assertEqual(self.gateway.calls, calls)
        self.assertEqual(self.plan_bytes(self.target), self.plan_before)
        return outcome

    async def test_administrator_required(self):
        for principal in ("caller-test", "home_assistant_admin_ingress:", None,
                          "home_assistant_admin_ingress:bad user", ADMIN + "\n"):
            with self.subTest(principal=principal):
                self.refuse("administrator_required", principal=principal)
                audit = self.acknowledgement_audits()[-1]
                self.assertIs(audit["authenticated"], False)
                self.assertIsNone(audit["caller_id"])

    async def test_record_identifier_invalid(self):
        for kind, record_id in (("automation", self.target), ("change_plan", "A" * 32),
                                ("change_plan", self.target[:31]), ("change_plan", None)):
            with self.subTest(kind=kind, record_id=record_id):
                outcome = self.refuse("record_identifier_invalid",
                                      record_kind=kind, record_id=record_id)
                if kind == "automation":
                    self.assertIsNone(outcome["record_kind"])

    async def test_record_kind_mismatch(self):
        self.refuse("record_kind_mismatch", record_kind="execution_task")

    async def test_record_not_found(self):
        for kind in ("change_plan", "execution_task"):
            with self.subTest(kind=kind):
                self.refuse("record_not_found", record_kind=kind, record_id="0" * 32)

    async def test_record_not_acknowledgeable(self):
        healthy = await self.update_plan()
        view = self.inspect_record("change_plan", healthy["plan_id"])
        self.assertIs(view["acknowledgeable"], False)
        self.refuse("record_not_acknowledgeable", record_id=healthy["plan_id"],
                    record_fingerprint=view["record_fingerprint"])

    async def test_record_fingerprint_stale(self):
        for fingerprint in ("not-a-fingerprint", "A" * 64, self.fingerprint[:63], None,
                            "0" * 64):
            with self.subTest(fingerprint=fingerprint):
                self.refuse("record_fingerprint_stale", record_fingerprint=fingerprint)

    async def test_reason_oversize(self):
        for reason in ("x" * 501, None, 42):
            with self.subTest(reason=str(reason)[:16]):
                self.refuse("reason_oversize", reason=reason)

    async def test_reason_invalid(self):
        for reason in ("bad\x07reason", " leading space", "trailing space ", "",
                       "line\nbreak", "tab\tinside", "zero​width"):
            with self.subTest(reason=repr(reason)):
                self.refuse("reason_invalid", reason=reason)

    async def test_classification_invalid(self):
        for action, classification in (("acknowledge", "urgent"), ("acknowledge", None),
                                       ("acknowledge", "HISTORICAL_RECORD"),
                                       ("revoke", "historical_record")):
            with self.subTest(action=action, classification=classification):
                self.refuse("classification_invalid", action=action,
                            classification=classification)

    async def test_action_invalid(self):
        for action in ("delete", "ACKNOWLEDGE", "approve", None):
            with self.subTest(action=action):
                outcome = self.refuse("action_invalid", action=action)
                self.assertIsNone(outcome["action"])

    async def test_record_not_acknowledged(self):
        self.refuse("record_not_acknowledged", action="revoke", classification=None)

    async def test_acknowledgement_in_progress(self):
        lock_dir = self.repository.root / ledger_module.MODEL
        lock_dir.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock_dir / ".transaction.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.refuse("acknowledgement_in_progress")
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        # Released, the same request records normally.
        self.assert_success(self.acknowledge("change_plan", self.target), "acknowledged")

    async def test_persistence_failure_records_nothing(self):
        for stage in ("before_write", "before_replace"):
            with self.subTest(stage=stage):
                def fault(current, stage=stage):
                    if current == stage:
                        raise OSError("synthetic HAMCP-253 persistence fault")

                self.service.acknowledgements.fault_hook = fault
                try:
                    self.refuse("acknowledgement_persistence_failed")
                finally:
                    self.service.acknowledgements.fault_hook = None
                self.assertFalse(self.ledger_path().exists())
                leftovers = list((self.repository.root / ledger_module.MODEL).glob(".acknowledgement-*"))
                self.assertEqual(leftovers, [])
        self.assert_success(self.acknowledge("change_plan", self.target), "acknowledged")
        self.assertEqual(len(self.ledger_events()), 1)

    async def test_refusal_after_an_entry_leaves_the_ledger_unchanged(self):
        self.assert_success(self.acknowledge("change_plan", self.target), "acknowledged")
        ledger = self.ledger_path().read_bytes()
        self.refuse("reason_invalid", ledger_before=1, reason="")
        self.refuse("record_fingerprint_stale", ledger_before=1, record_fingerprint="0" * 64)
        self.assertEqual(self.ledger_path().read_bytes(), ledger)


class RedactionTests(Hamcp253Case):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        proposed = copy.deepcopy(CURRENT)
        proposed["description"] = f"Contains {CONFIG_SECRET}"
        proposed["action"] = [
            {"service": "notify.mobile_app", "data": {"message": CONFIG_SECRET}}
        ]
        self.target = (await self.failed_plan(proposed))["plan_id"]
        # Precondition: the secret-like value really is in the stored record.
        self.assertIn(CONFIG_SECRET, self.repository._path(self.target).read_text(encoding="utf-8"))

    async def test_sensitive_or_token_shaped_reason_is_refused(self):
        reasons = (
            f"copied {SECRET} by mistake",
            "copied test-ha-token-value by mistake",
            *TOKEN_SHAPED_REASONS,
        )
        fingerprint = self.inspect_record("change_plan", self.target)["record_fingerprint"]
        for reason in reasons:
            with self.subTest(reason=reason[:12]):
                with no_home_assistant(self):
                    outcome = self.acknowledge("change_plan", self.target, reason=reason,
                                               fingerprint=fingerprint)
                self.assert_refused(outcome, "reason_contains_sensitive_value")
                self.assertNotIn(reason, json.dumps(outcome))
        audit_text = json.dumps(self.acknowledgement_audits())
        for value in (SECRET, "test-ha-token-value", "SYNTHETIC-bearer-hamcp253-0003",
                      "SYNTHETIC-signature"):
            self.assertNotIn(value, audit_text)
        self.assertFalse(self.ledger_path().exists())

    async def test_configuration_secret_never_leaves_the_plan(self):
        view = self.inspect_record("change_plan", self.target)
        outcome = self.acknowledge("change_plan", self.target)
        self.assert_success(outcome, "acknowledged")
        health = self.service.health_summary()
        stuck = self.service.stuck_governance_records()
        after = self.inspect_record("change_plan", self.target)
        for label, value in (
            ("inspection", view),
            ("inspection_after", after),
            ("outcome", outcome),
            ("health", health),
            ("stuck", stuck),
            ("audit", self.acknowledgement_audits()),
        ):
            with self.subTest(surface=label):
                self.assertNotIn(CONFIG_SECRET, json.dumps(value, default=str))
        self.assertNotIn(CONFIG_SECRET, self.ledger_path().read_text(encoding="utf-8"))


class ManualReviewTaskTests(unittest.IsolatedAsyncioTestCase):
    """A real MANUAL_REVIEW_REQUIRED execution task (beta11 restart flow)."""

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "plans"
        self.audit_path = Path(self.temp.name) / "audit.jsonl"
        self.clock = LifecycleClock()
        self.gateway = FakeLifecycleGateway()
        self.gateway.now = self.clock
        self.service = self.build(self.gateway)
        self.telemetry, self.context = begin_request("hamcp253-manual-review")
        self.telemetry.caller_id = "mcp-requester"
        self.addAsyncCleanup(self._cleanup)
        # Provenance: tests/test_beta11_restart_reconciliation.py
        # test_expired_durable_restart_terminalizes_without_probe.
        created = await self.service.create_addon_restart_plan(addon_slug="local_test_addon")
        plan = await self._grant(created)
        self.gateway.mode = "ambiguous"
        self.gateway.verification_status = "pending"
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(result["status"], "verification_pending")
        task = self.service.task_repository.get_for_plan(plan["plan_id"])
        self.clock.value = self.clock.value + timedelta(hours=24, seconds=1)
        self.recovered_gateway = SelfRestartRecoveryGateway("process-two")
        self.recovered_gateway.now = self.clock
        self.recovered_gateway.missing_readback = True
        self.recovered = self.build(self.recovered_gateway)
        await self.recovered.reconcile_execution_tasks(trigger="startup")
        self.task_id = task.task_id
        self.plan_id = plan["plan_id"]
        persisted = self.recovered.get_execution_task(self.task_id)
        self.assertEqual(persisted["state"], "manual_review_required")

    async def _cleanup(self):
        end_request(self.context)
        self.temp.cleanup()

    def build(self, lifecycle_gateway):
        return ChangeGovernanceService(
            ChangePlanRepository(self.root),
            LegacyGateway(),
            AuditLogger(str(self.audit_path), "test-access-secret-value"),
            now=self.clock,
            lifecycle_gateway=lifecycle_gateway,
            sensitive_values=SENSITIVE_VALUES,
        )

    async def _grant(self, created):
        # Provenance: Beta11RestartReconciliationTests._grant.
        plan = created["plan"]
        pending = self.service.approve(plan["plan_id"], plan["plan_hash"])
        _, csrf = await self.service.issue_external_csrf(plan["plan_id"], pending["challenge_id"])
        result = await self.service.decide_external_approval(
            plan_id=plan["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=plan["plan_hash"],
            approval_kind="apply",
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal="home_assistant_admin_ingress:fixture",
        )
        if result.get("status") == "approval_pending":
            _, csrf = await self.service.issue_external_csrf(plan["plan_id"], result["challenge_id"])
            await self.service.decide_external_approval(
                plan_id=plan["plan_id"],
                challenge_id=result["challenge_id"],
                expected_plan_hash=plan["plan_hash"],
                approval_kind="apply",
                approval_action=result["approval_action"],
                csrf_nonce=csrf,
                decision="approve",
                approver_principal="home_assistant_admin_ingress:fixture",
            )
        return plan

    def counters(self):
        gateway = self.recovered_gateway
        return (gateway.dispatch_count, gateway.planning_count, gateway.verification_count,
                len(gateway.home_assistant_verification_calls))

    async def test_manual_review_task_is_inspected_and_acknowledged(self):
        counters = self.counters()
        task_path = self.recovered.task_repository._path(self.task_id)
        task_bytes = task_path.read_bytes()
        with no_home_assistant(self):
            view = self.recovered.inspect_governance_record(
                record_kind="execution_task", record_id=self.task_id
            )
        self.assertEqual(view["record_kind"], "execution_task")
        self.assertEqual(view["record_id"], self.task_id)
        self.assertIs(view["readable"], True)
        self.assertEqual(view["status"], "manual_review_required")
        self.assertEqual(view["plan_id"], self.plan_id)
        self.assertIsNotNone(view["record_type"])
        self.assertIsNotNone(view["created_at"])
        self.assertIsNotNone(view["updated_at"])
        self.assertIn("policy_class", view)
        self.assertIs(view["failure"]["manual_review_required"], True)
        self.assertEqual(view["failure"]["reason_code"], RESTART_VERIFICATION_WINDOW_EXPIRED)
        self.assertEqual(
            [row["order"] for row in view["events"]], list(range(1, len(view["events"]) + 1))
        )
        self.assertEqual(view["event_count"], len(view["events"]))
        self.assertIs(view["events_truncated"], False)
        self.assertEqual(view["unreadable_fields"], [])
        self.assertIs(view["acknowledgeable"], True)
        self.assertRegex(view["record_fingerprint"], HEX64)
        self.assertIs(view["payload_included"], False)
        self.assertEqual(view["acknowledgement"]["state"], "unacknowledged")

        health = self.recovered.health_summary()
        self.assertEqual(health["manual_review_warning"], MANUAL_REVIEW_WARNING)
        tasks = health["record_acknowledgement"]["manual_review_tasks"]
        self.assertEqual(tasks["unacknowledged_ids"], [self.task_id])

        with no_home_assistant(self):
            outcome = self.recovered.acknowledge_governance_record(
                record_kind="execution_task",
                record_id=self.task_id,
                action="acknowledge",
                classification="historical_record",
                reason=REASON,
                record_fingerprint=view["record_fingerprint"],
                principal=ADMIN,
            )
        self.assertEqual(outcome["status"], "acknowledged")
        self.assertIs(outcome["execution_eligibility_changed"], False)
        health = self.recovered.health_summary()
        self.assertIsNone(health["manual_review_warning"])
        tasks = health["record_acknowledgement"]["manual_review_tasks"]
        self.assertEqual(tasks["unacknowledged_count"], 0)
        self.assertEqual(tasks["acknowledged_historical_ids"], [self.task_id])
        self.assertEqual(health["execution_tasks"]["tasks_manual_review"], 1)
        self.assertEqual(task_path.read_bytes(), task_bytes)
        self.assertEqual(
            self.recovered.get_execution_task(self.task_id)["state"], "manual_review_required"
        )
        self.assertEqual(self.counters(), counters)

    async def test_inconsistent_task_totals_keep_the_manual_review_warning(self):
        with patch.object(self.recovered, "_acknowledgement_health",
                          return_value=empty_acknowledgement_block()):
            health = self.recovered.health_summary()
        self.assertEqual(health["execution_tasks"]["tasks_manual_review"], 1)
        self.assertEqual(health["manual_review_warning"], MANUAL_REVIEW_WARNING)
        self.assertIs(health["record_acknowledgement"]["counts_consistent"], False)

    async def test_repeated_inspection_never_moves_a_corrupt_task(self):
        repository = self.recovered.task_repository
        path = repository._path(self.task_id)
        path.write_text("{not json", encoding="utf-8")
        for _ in range(2):
            with no_home_assistant(self):
                view = self.recovered.inspect_governance_record(
                    record_kind="execution_task", record_id=self.task_id
                )
            self.assertIs(view["readable"], False)
            self.assertEqual(
                view["unreadable_reason"], ErrorCode.EXECUTION_TASK_STORAGE_ERROR.value
            )
        self.assertEqual(path.read_bytes(), b"{not json")
        self.assertEqual(list(repository.quarantine.glob(f"*{self.task_id}*")), [])

    async def test_task_identifier_as_plan_is_a_kind_mismatch(self):
        with no_home_assistant(self):
            outcome = self.recovered.acknowledge_governance_record(
                record_kind="change_plan",
                record_id=self.task_id,
                action="acknowledge",
                classification="historical_record",
                reason=REASON,
                record_fingerprint="0" * 64,
                principal=ADMIN,
            )
        self.assertEqual(outcome["status"], "refused")
        self.assertEqual(outcome["refusal_reason"], "record_kind_mismatch")
        ledger = self.root / ledger_module.MODEL / "acknowledgements.json"
        self.assertFalse(ledger.exists())
        audits = [
            json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        recorded = [entry for entry in audits if entry.get("event") == EVENT]
        self.assertEqual(recorded[-1]["error_code"], "record_kind_mismatch")


class McpRegistrationTests(Hamcp253Case):
    def tools(self):
        return registered_tools(get_registered_server())

    async def test_inspection_tool_is_registered_read_only(self):
        tool = self.tools()["inspect_governance_record"]
        annotations = tool.annotations
        self.assertIs(annotations.readOnlyHint, True)
        self.assertIs(annotations.destructiveHint, False)
        self.assertEqual(set(tool.parameters["properties"]), {"record_kind", "record_id"})

    async def test_no_acknowledgement_tool_is_reachable_over_mcp(self):
        names = set(self.tools())
        self.assertEqual(sorted(name for name in names if "acknowledg" in name), [])
        self.assertNotIn("acknowledge_governance_record", names)
        self.assertNotIn("revoke_governance_record_acknowledgement", names)

    async def test_served_tool_inspects_without_acknowledging(self):
        (plan_id,) = await self.projection_failed_plans()
        view = self.inspect_record("change_plan", plan_id)
        tool = self.tools()["inspect_governance_record"]
        with patch.object(governance_tools.GOVERNANCE, "require", return_value=self.service):
            with no_home_assistant(self):
                rendered = await tool.run({"record_kind": "change_plan", "record_id": plan_id})
        self.assertTrue(json.loads(rendered)["success"])
        self.assertIn(view["record_fingerprint"], rendered)
        self.assertFalse(self.ledger_path().exists())
        self.assertEqual(self.acknowledgement_audits(), [])

    async def test_invalid_arguments_never_reach_governance(self):
        tool = self.tools()["inspect_governance_record"]
        unavailable = Mock(side_effect=AssertionError("governance reached"))
        with patch.object(governance_tools.GOVERNANCE, "require", unavailable):
            for arguments in ({"record_kind": "change_plan", "record_id": "NOT-HEX"},
                              {"record_kind": "automation", "record_id": "a" * 32},
                              {"record_kind": "change_plan"}):
                with self.subTest(arguments=arguments):
                    try:
                        rendered = await tool.run(arguments)
                    except Exception:
                        continue  # refused by argument validation
                    self.assertFalse(json.loads(rendered)["success"])
        self.assertEqual(unavailable.call_count, 0)


if __name__ == "__main__":
    unittest.main()
