"""HAMCP-304: never-approvable prohibited operational plans project normally.

Before HAMCP-304, inspection screened a plan's stored approval kind
(``apply`` or ``rollback``) against the approval *action* vocabulary, so
every plan showed ``unrecognized_value``. Expiry never lost the kind. The
prohibited-plan check also accepted only ``change_plan_created`` as a first
event, but every contract-v3 operational plan records its own operation's
creation event, so a never-approvable prohibited operational plan failed
projection as ``approval_sequence_inconsistent`` as soon as it was stored.

The fixtures under ``tests/fixtures/hamcp304_beta3_prohibited_plans`` are
written by the exact v2.4.2-beta.3 code in the live shapes of plans
``2ab4764f``, ``faba2fbd``, ``4cf748d1`` (prohibited) and ``d3af624a``
(expired elevated); see the provenance file. All data is synthetic and
offline, and every Home Assistant client entry point fails the test if it is
reached.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance import record_inspection  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ChangeEvent,
    ChangeOperation,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
    OPERATIONAL_PLAN_CREATION_EVENTS,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)
from tests.test_2_1a_beta2_operational_lifecycle import (  # noqa: E402
    FakeLifecycleGateway,
)
from tests.test_2_1a_operational_backup import (  # noqa: E402
    FakeOperationalGateway,
)
from tests.test_beta37_exact_helper_state import (  # noqa: E402
    FakeHelperStateGateway,
    UnusedLegacyGateway,
)
from tests.test_hamcp253_record_acknowledgement import (  # noqa: E402
    no_home_assistant,
)


FIXTURES = ROOT / "tests" / "fixtures" / "hamcp304_beta3_prohibited_plans"
PROVENANCE = ROOT / "tests" / "fixtures" / "hamcp304_beta3_prohibited_plans_provenance.json"
GENERATOR = ROOT / "scripts" / "generate_hamcp304_beta3_prohibited_plan_fixture.py"
PLAN_STORE = "plans/operational-administration-v3"
LEDGER = "plans/hamcp253-record-acknowledgement-v1/acknowledgements.json"
ADMIN = "home_assistant_admin_ingress:hamcp304-admin"
REASON = "Reviewed: HAMCP-304 regression fixture"
PROHIBITED = (
    "prohibited_input_boolean_acknowledged",
    "prohibited_input_boolean_unacknowledged",
    "prohibited_input_select_acknowledged",
)
ACKNOWLEDGED = (
    "prohibited_input_boolean_acknowledged",
    "prohibited_input_select_acknowledged",
)
EXPIRED = "expired_elevated_input_boolean"
OPERATIONAL = tuple(OPERATIONAL_PLAN_CREATION_EVENTS)
NON_OPERATIONAL = tuple(
    operation for operation in ChangeOperation if operation not in OPERATIONAL_PLAN_CREATION_EVENTS
)


def provenance() -> dict:
    return json.loads(PROVENANCE.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FixtureProvenanceTests(unittest.TestCase):
    def test_fixture_bytes_match_the_shipped_writer(self):
        data = provenance()
        self.assertEqual(data["source"]["tag"], "v2.4.2-beta.3")
        self.assertEqual(
            data["source"]["commit"], "446a0dd79629a93165fe68e897ff5a119c42f25e"
        )
        self.assertEqual(data["generator_sha256"], _sha256(GENERATOR))
        self.assertEqual(sorted(data["records"]), sorted((*PROHIBITED, EXPIRED)))
        for kind, record in data["records"].items():
            with self.subTest(kind=kind):
                on_disk = {
                    str(path.relative_to(FIXTURES / kind)): _sha256(path)
                    for path in sorted((FIXTURES / kind).rglob("*"))
                    if path.is_file()
                }
                self.assertEqual(on_disk, record["files"])
                self.assertEqual(record["provider_dispatch_count"], 0)

    def test_shipped_view_is_the_reported_live_defect(self):
        records = provenance()["records"]
        for kind in PROHIBITED:
            with self.subTest(kind=kind):
                view = records[kind]["shipped_view"]
                self.assertEqual(view["policy_class"], "prohibited")
                self.assertEqual(view["approval"]["approval_kind"], "unrecognized_value")
                self.assertEqual(view["approval"]["bundle_state"], "prohibited")
                self.assertIs(view["failure"]["projection_failed"], True)
                self.assertEqual(
                    view["failure"]["category"], "approval_sequence_inconsistent"
                )
                self.assertEqual(
                    view["events"],
                    [OPERATIONAL_PLAN_CREATION_EVENTS[ChangeOperation(records[kind]["operation"])]],
                )
        expired = records[EXPIRED]["shipped_view"]
        self.assertEqual(expired["status"], "expired")
        self.assertEqual(expired["policy_class"], "elevated_admin")
        self.assertEqual(expired["approval"]["approval_kind"], "unrecognized_value")
        self.assertIs(expired["failure"]["projection_failed"], False)
        self.assertEqual(expired["events"], [
            "set_input_boolean_state_plan_created",
            "external_approval_requested",
            "external_approval_invalidated",
            "change_plan_expired",
        ])

    def test_expiry_kept_the_stored_approval_kind(self):
        # The shipped expiry path stored ``apply``; only the view lost it.
        record = provenance()["records"][EXPIRED]
        stored = json.loads(
            (FIXTURES / EXPIRED / PLAN_STORE / f"{record['plan_id']}.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(stored["status"], "expired")
        self.assertEqual(stored["approval"]["state"], "invalidated")
        self.assertEqual(stored["approval"]["approval_kind"], "apply")


class FixtureCase(unittest.IsolatedAsyncioTestCase):
    """A current service over a disposable copy of one or more fixtures."""

    async def asyncSetUp(self):
        self.records = provenance()["records"]
        self.telemetry, self.context = begin_request("hamcp304")
        self.telemetry.caller_id = "hamcp304-mcp-requester"
        self.addCleanup(end_request, self.context)
        self.fresh()

    def fresh(self) -> None:
        """Start over in a new empty store; used once per subtest."""

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.helper = FakeHelperStateGateway()

    def load(self, kind: str) -> str:
        """Copy one fixture in verbatim and start a service over it."""

        shutil.copytree(FIXTURES / kind, self.root, dirs_exist_ok=True)
        record = self.records[kind]
        # Well past each plan's expiry, as the live records were.
        self.now = datetime.fromisoformat(record["clock"]) + timedelta(days=30)
        self.restart()
        return record["plan_id"]

    def restart(self) -> ChangeGovernanceService:
        self.repository = ChangePlanRepository(self.root / "plans")
        self.service = ChangeGovernanceService(
            self.repository,
            UnusedLegacyGateway(),
            AuditLogger(str(self.root / "audit.jsonl"), "hamcp304-test-secret"),
            now=lambda: self.now,
            helper_state_gateway=self.helper,
        )
        return self.service

    def plan_path(self, plan_id: str) -> Path:
        return self.root / PLAN_STORE / f"{plan_id}.json"

    def ledger_bytes(self) -> bytes | None:
        path = self.root / LEDGER
        return path.read_bytes() if path.exists() else None

    def inspect(self, plan_id: str) -> dict:
        return self.service.inspect_governance_record(
            record_kind="change_plan", record_id=plan_id
        )

    def acknowledge(self, plan_id: str, fingerprint: str, classification="historical_record"):
        return self.service.acknowledge_governance_record(
            record_kind="change_plan",
            record_id=plan_id,
            action="acknowledge",
            classification=classification,
            reason=REASON,
            record_fingerprint=fingerprint,
            principal=ADMIN,
        )


class LiveShapedProjectionTests(FixtureCase):
    async def test_prohibited_plans_project_as_terminal_prohibited(self):
        for kind in PROHIBITED:
            with self.subTest(kind=kind):
                self.fresh()
                plan_id = self.load(kind)
                before = self.plan_path(plan_id).read_bytes()
                ledger_before = self.ledger_bytes()
                with no_home_assistant(self):
                    view = self.inspect(plan_id)
                    plan = self.service.get_plan(plan_id)
                    listed = self.service.list_plans(limit=20)
                    health = self.service.health_summary()
                self.assertEqual(view["policy_class"], "prohibited")
                self.assertIs(view["terminal"], True)
                self.assertEqual(view["approval"]["approval_kind"], "apply")
                self.assertEqual(view["approval"]["bundle_state"], "prohibited")
                self.assertIs(view["failure"]["projection_failed"], False)
                self.assertIsNone(view["failure"]["category"])
                self.assertIsNone(view["failure"]["reason_code"])
                self.assertEqual(view["unreadable_fields"], [])
                # Terminal prohibited plans stay acknowledgeable.
                self.assertIs(view["acknowledgeable"], True)
                self.assertEqual(plan["plan_id"], plan_id)
                self.assertEqual(plan["policy_decision"]["policy_class"], "prohibited")
                self.assertFalse(plan["approval_actionable"])
                self.assertIn(plan_id, [item["plan_id"] for item in listed["plans"]])
                self.assertEqual(health["projection_failure_count"], 0)
                self.assertIsNone(health["projection_failure_warning"])
                acknowledgement = health["record_acknowledgement"]
                self.assertIs(acknowledgement["counts_consistent"], True)
                bucket = acknowledgement["projection_failed_plans"]
                for key in (
                    "unacknowledged_ids", "acknowledged_historical_ids",
                    "acknowledged_current_defect_ids",
                ):
                    self.assertNotIn(plan_id, bucket[key])
                self.assertEqual(bucket["stale_acknowledgement_count"], 0)
                # Reading never rewrites the plan or the ledger.
                self.assertEqual(self.plan_path(plan_id).read_bytes(), before)
                self.assertEqual(self.ledger_bytes(), ledger_before)
                self.assertEqual(self.helper.dispatch_count, 0)

    async def test_expired_elevated_plan_shows_its_stored_kind(self):
        plan_id = self.load(EXPIRED)
        before = self.plan_path(plan_id).read_bytes()
        with no_home_assistant(self):
            view = self.inspect(plan_id)
            health = self.service.health_summary()
        self.assertEqual(view["status"], "expired")
        self.assertEqual(view["policy_class"], "elevated_admin")
        self.assertEqual(view["approval"]["state"], "invalidated")
        self.assertEqual(view["approval"]["approval_kind"], "apply")
        self.assertEqual(view["approval"]["bundle_state"], "invalidated")
        self.assertIs(view["failure"]["projection_failed"], False)
        self.assertIs(view["acknowledgeable"], False)
        self.assertEqual(health["projection_failure_count"], 0)
        self.assertIs(health["record_acknowledgement"]["counts_consistent"], True)
        self.assertEqual(self.plan_path(plan_id).read_bytes(), before)

    async def test_all_live_shapes_together(self):
        # One store holding every live shape, as the deployment did. Ledgers
        # are per fixture, so only the unacknowledged ones are combined.
        ids = [self.load(kind) for kind in (
            "prohibited_input_boolean_unacknowledged", EXPIRED,
        )]
        with no_home_assistant(self):
            health = self.service.health_summary()
        self.assertEqual(health["projection_failure_count"], 0)
        self.assertIsNone(health["projection_failure_warning"])
        self.assertIs(health["record_acknowledgement"]["counts_consistent"], True)
        self.assertEqual(
            health["record_acknowledgement"]["projection_failed_plans"]["unacknowledged_count"], 0
        )
        for plan_id in ids:
            self.assertIs(self.inspect(plan_id)["failure"]["projection_failed"], False)

    async def test_rollback_kind_is_recognized(self):
        plan_id = self.load(EXPIRED)
        plan = self.repository.read_for_inspection(plan_id)
        plan.approval.approval_kind = "rollback"
        view = record_inspection.plan_view(self.service, plan, plan_id)
        self.assertEqual(view["approval"]["approval_kind"], "rollback")
        for value in ("plan_approval", "Apply", "", "rollback "):
            with self.subTest(value=value):
                plan.approval.approval_kind = value
                view = record_inspection.plan_view(self.service, plan, plan_id)
                self.assertEqual(view["approval"]["approval_kind"], "unrecognized_value")

    async def test_prohibited_plans_still_refuse_approval_and_apply(self):
        for kind in PROHIBITED:
            with self.subTest(kind=kind):
                self.fresh()
                plan_id = self.load(kind)
                plan_hash = self.records[kind]["plan_hash"]
                with no_home_assistant(self):
                    with self.assertRaises(GovernanceError) as approved:
                        self.service.approve(plan_id, plan_hash)
                    self.assertEqual(approved.exception.code, ErrorCode.PROHIBITED_CHANGE)
                    # Without an execution runtime the refusal code varies by
                    # path; what matters is that nothing was dispatched.
                    with self.assertRaises(GovernanceError):
                        await self.service.apply(plan_id, plan_hash)
                    view = self.inspect(plan_id)
                self.assertIs(view["failure"]["projection_failed"], False)
                self.assertEqual(self.helper.dispatch_count, 0)
                self.assertIsNone(
                    self.service.task_repository.get_for_plan(plan_id)
                )


class AcknowledgementFallbackTests(FixtureCase):
    async def test_pre_fix_acknowledgement_stays_acknowledged(self):
        for kind in ACKNOWLEDGED:
            with self.subTest(kind=kind):
                self.fresh()
                plan_id = self.load(kind)
                record = self.records[kind]
                acknowledged = record["acknowledgement"]["record_fingerprint"]
                ledger_before = self.ledger_bytes()
                with no_home_assistant(self):
                    view = self.inspect(plan_id)
                # The view changed, so its fingerprint did too.
                self.assertNotEqual(view["record_fingerprint"], acknowledged)
                # The pre-fix view of the unchanged record is reproduced exactly.
                self.assertEqual(
                    self.service._pre_hamcp304_fingerprint("change_plan", plan_id),
                    acknowledged,
                )
                self.assertEqual(view["acknowledgement"]["state"], "acknowledged")
                self.assertEqual(
                    view["acknowledgement"]["classification"],
                    record["acknowledgement"]["classification"],
                )
                self.assertEqual(self.ledger_bytes(), ledger_before)

    async def test_pre_fix_view_reproduces_every_shipped_fingerprint(self):
        for kind in (*PROHIBITED, EXPIRED):
            with self.subTest(kind=kind):
                self.fresh()
                plan_id = self.load(kind)
                self.assertEqual(
                    self.service._pre_hamcp304_fingerprint("change_plan", plan_id),
                    self.records[kind]["shipped_view"]["record_fingerprint"],
                )

    async def test_changed_record_makes_the_acknowledgement_stale(self):
        for kind in ACKNOWLEDGED:
            with self.subTest(kind=kind):
                self.fresh()
                plan_id = self.load(kind)
                acknowledged = self.records[kind]["acknowledgement"]["record_fingerprint"]
                # A real governance write: approval is refused and recorded.
                with self.assertRaises(GovernanceError):
                    self.service.approve(plan_id, self.records[kind]["plan_hash"])
                self.restart()
                view = self.inspect(plan_id)
                self.assertIs(view["failure"]["projection_failed"], False)
                self.assertNotEqual(view["record_fingerprint"], acknowledged)
                self.assertNotEqual(
                    self.service._pre_hamcp304_fingerprint("change_plan", plan_id),
                    acknowledged,
                )
                self.assertEqual(view["acknowledgement"]["state"], "stale")

    async def test_fallback_runs_only_on_mismatch(self):
        plan_id = self.load("prohibited_input_boolean_acknowledged")
        original = ChangeGovernanceService._pre_hamcp304_fingerprint
        calls: list[str] = []

        def spy(service, kind, record_id):
            calls.append(record_id)
            return original(service, kind, record_id)

        with patch.object(ChangeGovernanceService, "_pre_hamcp304_fingerprint", spy):
            self.assertEqual(self.inspect(plan_id)["acknowledgement"]["state"], "acknowledged")
            self.assertEqual(calls, [plan_id])
            # Re-acknowledge against the current view: the fallback is no
            # longer consulted.
            current = self.inspect(plan_id)["record_fingerprint"]
            calls.clear()
            outcome = self.acknowledge(plan_id, current)
            self.assertEqual(outcome["status"], "acknowledged", outcome)
            calls.clear()
            view = self.inspect(plan_id)
            self.assertEqual(view["acknowledgement"]["state"], "acknowledged")
            self.assertEqual(calls, [])
            self.service.health_summary()
            self.assertEqual(calls, [])

    async def test_unacknowledged_record_never_consults_the_fallback(self):
        plan_id = self.load("prohibited_input_boolean_unacknowledged")
        with patch.object(
            ChangeGovernanceService, "_pre_hamcp304_fingerprint",
            side_effect=AssertionError("fallback consulted without an entry"),
        ):
            view = self.inspect(plan_id)
        self.assertEqual(view["acknowledgement"]["state"], "unacknowledged")

    async def test_fallback_is_never_stored(self):
        plan_id = self.load("prohibited_input_boolean_unacknowledged")
        pre_fix = self.records["prohibited_input_boolean_unacknowledged"]["shipped_view"][
            "record_fingerprint"
        ]
        self.assertEqual(
            self.service._pre_hamcp304_fingerprint("change_plan", plan_id), pre_fix
        )
        # A new acknowledgement must bind the current view, never the pre-fix one.
        outcome = self.acknowledge(plan_id, pre_fix)
        self.assertEqual(outcome["status"], "refused")
        self.assertEqual(outcome["refusal_reason"], "record_fingerprint_stale")
        self.assertIsNone(self.ledger_bytes())
        current = self.inspect(plan_id)["record_fingerprint"]
        self.assertEqual(self.acknowledge(plan_id, current)["status"], "acknowledged")
        events = json.loads(self.ledger_bytes())["events"]
        stored = {event["body"].get("record_fingerprint") for event in events} - {None}
        self.assertEqual(stored, {current})

    async def test_acknowledged_plan_reading_writes_nothing(self):
        plan_id = self.load("prohibited_input_select_acknowledged")
        ledger_before = self.ledger_bytes()
        plan_before = self.plan_path(plan_id).read_bytes()
        for _ in range(2):
            self.inspect(plan_id)
            self.service.health_summary()
            self.service.stuck_governance_records()
        self.assertEqual(self.ledger_bytes(), ledger_before)
        self.assertEqual(self.plan_path(plan_id).read_bytes(), plan_before)


class CreationEventMappingTests(FixtureCase):
    """Only each operation's own creation event marks a never-approvable plan."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        plan_id = self.load("prohibited_input_boolean_unacknowledged")
        self.base = self.repository.read_for_inspection(plan_id)

    def variant(self, operation, events, **changes):
        plan = copy.deepcopy(self.base)
        plan.operation = operation
        plan.events = [
            ChangeEvent(
                event=name,
                result_status=status,
                error_code=None,
                timestamp=self.base.events[0].timestamp,
                request_id=self.base.events[0].request_id,
                caller_id=self.base.events[0].caller_id,
            )
            if isinstance(name, str) else name
            for name, status in events
        ]
        for name, value in changes.items():
            setattr(plan, name, value)
        return plan

    def has_evidence(self, plan) -> bool:
        return ChangeGovernanceService._prohibited_plan_has_execution_evidence(plan)

    def test_every_operational_operation_has_exactly_one_event(self):
        self.assertEqual(set(OPERATIONAL), {
            ChangeOperation.CREATE_FULL_BACKUP,
            ChangeOperation.CONTROLLED_RELOAD,
            ChangeOperation.RESTART_ADDON,
            ChangeOperation.RESTART_HOME_ASSISTANT,
            ChangeOperation.SET_INPUT_BOOLEAN_STATE,
            ChangeOperation.SET_INPUT_SELECT_OPTION,
        })
        self.assertEqual(
            len(set(OPERATIONAL_PLAN_CREATION_EVENTS.values())), len(OPERATIONAL)
        )

    def test_own_creation_event_only(self):
        unrelated = (
            "change_plan_created",
            "dashboard_update_plan_created",
            "rollback_plan_created",
            "hamcp304_plan_created",
            *(f"{operation.value}_plan_created" for operation in NON_OPERATIONAL),
        )
        for operation in OPERATIONAL:
            own = OPERATIONAL_PLAN_CREATION_EVENTS[operation]
            with self.subTest(operation=operation.value, event=own):
                self.assertFalse(self.has_evidence(
                    self.variant(operation, [(own, "success")])
                ))
                # A superseded never-approvable plan is still recognized.
                self.assertFalse(self.has_evidence(
                    self.variant(operation, [
                        (own, "success"), ("change_plan_superseded", "rejected"),
                    ])
                ))
            others = [
                event for other, event in OPERATIONAL_PLAN_CREATION_EVENTS.items()
                if other is not operation
            ]
            for event in (*others, *unrelated):
                with self.subTest(operation=operation.value, event=event):
                    self.assertTrue(self.has_evidence(
                        self.variant(operation, [(event, "success")])
                    ))
                    # Not even beside the plan's own event.
                    self.assertTrue(self.has_evidence(
                        self.variant(operation, [(own, "success"), (event, "success")])
                    ))

    def test_non_operational_plans_keep_change_plan_created(self):
        for operation in NON_OPERATIONAL:
            with self.subTest(operation=operation.value):
                self.assertTrue(self.has_evidence(self.variant(
                    operation, [(f"{operation.value}_plan_created", "success")]
                )))
        for operation in OPERATIONAL:
            own = OPERATIONAL_PLAN_CREATION_EVENTS[operation]
            for changes in (
                {"contract_version": 2},
                {"plan_family": "dashboard_administration"},
                {"plan_family": "configuration"},
                {"operational": None},
            ):
                with self.subTest(operation=operation.value, changes=changes):
                    self.assertTrue(self.has_evidence(
                        self.variant(operation, [(own, "success")], **changes)
                    ))
                    if changes != {"contract_version": 2}:
                        continue
                    self.assertFalse(self.has_evidence(self.variant(
                        operation, [("change_plan_created", "success")], **changes
                    )))

    def test_event_sequence_and_dispatch_negatives(self):
        operation = ChangeOperation.SET_INPUT_BOOLEAN_STATE
        own = OPERATIONAL_PLAN_CREATION_EVENTS[operation]
        for events in (
            [],
            [(own, "rejected")],
            [(own, "success"), (own, "success")],
            [("change_plan_superseded", "rejected"), (own, "success")],
            [(own, "success"), ("change_plan_superseded", "rejected"),
             ("change_plan_superseded", "rejected")],
            [(own, "success"), ("external_approval_requested", "success")],
            [(own, "success"), ("change_apply_started", "success")],
        ):
            with self.subTest(events=events):
                self.assertTrue(self.has_evidence(self.variant(operation, events)))
        for changes in (
            {"applied_at": self.base.created_at},
            {"apply_request_id": "hamcp304-dispatch"},
            {"execution_outcome": "applied"},
        ):
            with self.subTest(changes=changes):
                self.assertTrue(self.has_evidence(
                    self.variant(operation, [(own, "success")], **changes)
                ))

    async def test_appended_unrelated_event_is_still_a_projection_failure(self):
        # Provenance: ``ChangeGovernanceService._record`` is the event writer
        # every governance transition uses; the repository persists it.
        plan_id = self.base.plan_id
        plan = self.repository.get(plan_id)
        self.service._record(plan, "set_input_select_option_plan_created", "success")
        self.repository.save(plan)
        self.restart()
        view = self.inspect(plan_id)
        self.assertIs(view["failure"]["projection_failed"], True)
        self.assertEqual(view["failure"]["category"], "approval_sequence_inconsistent")
        health = self.service.health_summary()
        self.assertEqual(health["projection_failure_count"], 1)
        with self.assertRaises(GovernanceError) as approved:
            self.service.approve(plan_id, self.records[
                "prohibited_input_boolean_unacknowledged"]["plan_hash"])
        self.assertEqual(approved.exception.code, ErrorCode.APPROVAL_SEQUENCE_FAILURE)
        self.assertEqual(self.helper.dispatch_count, 0)


class RealCreatePathEventTests(unittest.IsolatedAsyncioTestCase):
    """Each mapped event is the one its real create path records."""

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.lifecycle = FakeLifecycleGateway()
        self.service = ChangeGovernanceService(
            ChangePlanRepository(root / "plans"),
            UnusedLegacyGateway(),
            AuditLogger(str(root / "audit.jsonl"), "hamcp304-test-secret"),
            sensitive_values=("hamcp304-test-secret",),
            lifecycle_gateway=self.lifecycle,
            operational_gateway=FakeOperationalGateway(),
        )
        self.telemetry, self.context = begin_request("hamcp304-create")
        self.telemetry.caller_id = "mcp-requester"

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    async def test_lifecycle_and_backup_create_paths(self):
        creators = {
            ChangeOperation.CREATE_FULL_BACKUP: lambda: self.service.create_backup_plan(
                backup_name="HAMCP-304 backup"
            ),
            ChangeOperation.CONTROLLED_RELOAD: lambda: self.service.create_reload_plan(
                reload_target="automation"
            ),
            ChangeOperation.RESTART_ADDON: lambda: self.service.create_addon_restart_plan(
                addon_slug="local_test_addon"
            ),
            ChangeOperation.RESTART_HOME_ASSISTANT: (
                self.service.create_home_assistant_restart_plan
            ),
        }
        for operation, create in creators.items():
            with self.subTest(operation=operation.value):
                created = await create()
                stored = self.service.repository.get(created["plan"]["plan_id"])
                self.assertEqual(stored.operation, operation)
                self.assertEqual(
                    stored.events[0].event, OPERATIONAL_PLAN_CREATION_EVENTS[operation]
                )
                self.assertEqual(
                    ChangeGovernanceService._prohibited_creation_event(stored),
                    stored.events[0].event,
                )

    def test_helper_create_paths_match_the_fixtures(self):
        for kind in PROHIBITED:
            record = provenance()["records"][kind]
            with self.subTest(kind=kind):
                self.assertEqual(
                    record["shipped_view"]["events"][0],
                    OPERATIONAL_PLAN_CREATION_EVENTS[ChangeOperation(record["operation"])],
                )


if __name__ == "__main__":
    unittest.main()
