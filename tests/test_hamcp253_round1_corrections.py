"""HAMCP-253 round-1 corrections (Codex review of PR #354).

R1-M1: inspection, the stuck-record list and every acknowledgement
eligibility read never quarantine, rebuild or write anything, whether the
navigation index is cold or invalidated and whether an unrelated record is
corrupt.

R1-M2: stored metadata is untrusted. A configured sensitive value persisted
as an event name, a plan identifier or a task identifier never reaches
inspection, the Ingress pages, health or audit.

R1-M3: an acknowledgement, reclassification or revoke takes effect only once
its audit event is durably written. Without a usable audit destination it is
refused before the ledger changes; an interrupted request is reported as
pending and reconciled without a duplicate audit event; ``audit_recorded`` is
true only when audit bytes were written.

Fixtures come from the real governance code paths named in
``tests/test_hamcp253_record_acknowledgement.py``; the damaged records are
direct edits of those files, each named where it is made. All values are
synthetic and offline, and every Home Assistant entry point is patched to
fail the test if it is reached.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.approval_web import IngressApprovalApplication  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance import (  # noqa: E402
    record_acknowledgement as ledger_module,
    record_inspection,
)
from ha_mcp_engineering.governance.record_acknowledgement import (  # noqa: E402
    AcknowledgementStorageError,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.tools import (  # noqa: E402
    get_registered_server,
    governance as governance_tools,
    registered_tools,
)
from tests.test_hamcp253_record_acknowledgement import (  # noqa: E402
    ADMIN,
    PROJECTION_WARNING,
    REASON,
    SECRET,
    SENSITIVE_VALUES,
    Hamcp253Case,
    ManualReviewTaskCase,
    no_home_assistant,
)
from tests.test_hamcp253_record_acknowledgement_web import (  # noqa: E402
    ADMIN_PRINCIPAL,
    Hamcp253IngressCase,
    RuntimeShim,
    ingress_client,
)
from tests.approval_identity_support import StaticAdministratorVerifier  # noqa: E402


# Configured, and shaped like an ordinary token, so that only the shared
# sanitizer (not the token pattern) can keep it out of a view.
TOKEN_SECRET = "synthetic-secret-hamcp253-0004"
SCREENED_VALUES = (*SENSITIVE_VALUES, TOKEN_SECRET)
SECRETS = (SECRET, TOKEN_SECRET)
UNRECOGNIZED = "unrecognized_value"
UNRELATED_PLAN = "c" * 32
UNRELATED_TASK = "d" * 32


def tree(root: Path) -> dict[str, bytes]:
    """Every file under ``root``, including quarantine, by relative path."""

    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def plan_navigation(repository) -> tuple:
    return (
        repository.generation, repository.index_rebuild_count,
        repository.index_update_count, repository.index_invalidation_count,
        repository._observed_directory_token, frozenset(repository._entries),
    )


def task_navigation(repository) -> tuple:
    return (
        repository.generation, repository.index_rebuild_count,
        repository.index_update_count, repository.index_invalidation_count,
        repository.corruption_count, repository.quarantine_generation,
        repository._observed_directory_token, frozenset(repository._entries),
    )


def make_cold(repository) -> None:
    # The state of an index that was never built: nothing observed.
    repository._entries.clear()
    repository._observed_directory_token = None


def rewrite_json(path: Path, change) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def assert_absent(test: unittest.TestCase, text: str, where: str) -> None:
    for secret in SECRETS:
        test.assertNotIn(secret, text, f"{where} echoed a configured secret")


# -- R1-M1 ------------------------------------------------------------------


class PlanInspectionNeverMutatesStorageTests(Hamcp253Case):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.unreadable = (await self.update_plan())["plan_id"]
        (self.stuck,) = await self.projection_failed_plans()
        # Provenance: a stored record that no longer decodes as JSON, written
        # after start so startup (which quarantines) has not seen it.
        self.repository._path(self.unreadable).write_text("{not json", encoding="utf-8")

    async def assert_reads_leave_storage_unchanged(self):
        root = self.repository.root
        before, navigation = tree(root), plan_navigation(self.repository)
        audits = len(self.acknowledgement_audits())
        calls = list(self.gateway.calls)
        with no_home_assistant(self):
            for _ in range(3):
                view = self.inspect_record("change_plan", self.unreadable)
                self.assertIs(view["readable"], False)
                self.assertEqual(
                    view["unreadable_reason"], ErrorCode.CHANGE_PLAN_STORAGE_ERROR.value
                )
                view = self.inspect_record("change_plan", UNRELATED_PLAN)
                self.assertIs(view["readable"], False)
                view = self.inspect_record("change_plan", self.stuck)
                self.assertIs(view["acknowledgeable"], True)
                self.service.stuck_governance_records()
                self.assertEqual(tree(root), before)
            for record_id in (self.unreadable, UNRELATED_PLAN):
                self.assert_refused(
                    self.acknowledge("change_plan", record_id, fingerprint="0" * 64),
                    "record_unreadable", audits_before=audits,
                )
                audits += 1
            self.assert_refused(
                self.acknowledge("execution_task", self.unreadable, fingerprint="0" * 64),
                "record_kind_mismatch", audits_before=audits,
            )
            self.assert_refused(
                self.acknowledge("change_plan", self.stuck, fingerprint="0" * 64),
                "record_fingerprint_stale", audits_before=audits + 1,
            )
        self.assertEqual(tree(root), before)
        self.assertEqual(list(self.repository.quarantine.glob("*")), [])
        self.assertEqual(plan_navigation(self.repository), navigation)
        self.assertEqual(self.gateway.calls, calls)
        self.assertFalse(self.ledger_path().exists())

    async def test_invalidated_index_with_an_unrelated_corrupt_record(self):
        # A new file changes the directory token, so the index is stale.
        (self.repository.root / f"{UNRELATED_PLAN}.json").write_text(
            "{not json", encoding="utf-8"
        )
        self.assertNotEqual(
            self.repository._observed_directory_token, self.repository._directory_token()
        )
        await self.assert_reads_leave_storage_unchanged()

    async def test_cold_index_with_an_unrelated_corrupt_record(self):
        (self.repository.root / f"{UNRELATED_PLAN}.json").write_text(
            "{not json", encoding="utf-8"
        )
        make_cold(self.repository)
        # A navigation rebuild since the projection pass: the stuck list
        # then reads every record as stored instead of from the index.
        self.repository.index_rebuild_count += 1
        await self.assert_reads_leave_storage_unchanged()
        self.assertEqual(
            self.service.stuck_governance_records()["projection_failed_plans"][
                "unacknowledged_ids"
            ],
            [self.stuck],
        )

    async def test_already_quarantined_record_is_refused_not_missing(self):
        # Provenance: ChangePlanRepository.get quarantines a corrupt record,
        # which is the normal path this correction leaves unchanged.
        with self.assertRaises(Exception):
            self.repository.get(self.unreadable)
        self.assertFalse(self.repository._path(self.unreadable).exists())
        self.assertEqual(len(list(self.repository.quarantine.glob("*.corrupt"))), 1)
        before = tree(self.repository.root)
        with no_home_assistant(self):
            view = self.inspect_record("change_plan", self.unreadable)
            outcome = self.acknowledge("change_plan", self.unreadable, fingerprint="0" * 64)
        self.assertIs(view["readable"], False)
        self.assert_refused(outcome, "record_unreadable")
        self.assertEqual(tree(self.repository.root), before)

    async def test_two_files_for_one_identifier_are_unreadable(self):
        # Provenance: a legacy and an operational file for one identifier.
        path = self.repository._path(self.stuck)
        twin = self.repository._path(self.stuck, operational=True)
        twin.write_bytes(path.read_bytes())
        before = tree(self.repository.root)
        view = self.inspect_record("change_plan", self.stuck)
        self.assertIs(view["readable"], False)
        self.assert_refused(
            self.acknowledge("change_plan", self.stuck, fingerprint="0" * 64),
            "record_unreadable",
        )
        self.assertEqual(tree(self.repository.root), before)


class TaskInspectionNeverMutatesStorageTests(ManualReviewTaskCase):
    def corrupt_unrelated_task(self):
        repository = self.recovered.task_repository
        (repository.root / f"{self.plan_id}.{UNRELATED_TASK}.json").write_text(
            "{not json", encoding="utf-8"
        )

    async def assert_reads_leave_storage_unchanged(self):
        repository = self.recovered.task_repository
        root = self.root
        before, navigation = tree(root), task_navigation(repository)
        counters = self.counters()
        with no_home_assistant(self):
            for _ in range(3):
                view = self.recovered.inspect_governance_record(
                    record_kind="execution_task", record_id=self.task_id
                )
                self.assertIs(view["acknowledgeable"], True)
                view = self.recovered.inspect_governance_record(
                    record_kind="execution_task", record_id=UNRELATED_TASK
                )
                self.assertIs(view["readable"], False)
                self.assertEqual(
                    view["unreadable_reason"], ErrorCode.EXECUTION_TASK_STORAGE_ERROR.value
                )
                stuck = self.recovered.stuck_governance_records()
                self.assertEqual(
                    stuck["manual_review_tasks"]["unacknowledged_ids"], [self.task_id]
                )
                self.assertEqual(tree(root), before)
            for kind, expected in (("execution_task", "record_unreadable"),
                                   ("change_plan", "record_kind_mismatch")):
                outcome = self.recovered.acknowledge_governance_record(
                    record_kind=kind, record_id=UNRELATED_TASK, action="acknowledge",
                    classification="historical_record", reason=REASON,
                    record_fingerprint="0" * 64, principal=ADMIN,
                )
                self.assertEqual(outcome["status"], "refused")
                self.assertEqual(outcome["refusal_reason"], expected)
        self.assertEqual(tree(root), before)
        self.assertEqual(list(repository.quarantine.glob("*")), [])
        self.assertEqual(task_navigation(repository), navigation)
        self.assertEqual(self.counters(), counters)

    async def test_invalidated_task_index_with_an_unrelated_corrupt_task(self):
        self.corrupt_unrelated_task()
        self.assertTrue(self.recovered.task_repository._navigation_index_stale())
        await self.assert_reads_leave_storage_unchanged()

    async def test_cold_task_index_with_an_unrelated_corrupt_task(self):
        self.corrupt_unrelated_task()
        make_cold(self.recovered.task_repository)
        await self.assert_reads_leave_storage_unchanged()

    async def test_quarantined_task_is_refused_not_missing(self):
        repository = self.recovered.task_repository
        path = repository._path(self.task_id)
        path.write_text("{not json", encoding="utf-8")
        # Provenance: the normal read path quarantines a corrupt task.
        with self.assertRaises(Exception):
            repository.get(self.task_id)
        self.assertFalse(path.exists())
        before = tree(self.root)
        view = self.recovered.inspect_governance_record(
            record_kind="execution_task", record_id=self.task_id
        )
        self.assertIs(view["readable"], False)
        self.assertEqual(tree(self.root), before)


# -- R1-M2 ------------------------------------------------------------------


class ScreenedServiceMixin:
    def build_service(self, repository):
        # Hamcp253Case.build_service with one more configured value.
        return ChangeGovernanceService(
            repository,
            self.gateway,
            AuditLogger(str(self.audit_path), "test-access-secret-value"),
            now=self.clock,
            sensitive_values=SCREENED_VALUES,
        )


class StoredMetadataIsNeverEchoedTests(ScreenedServiceMixin, Hamcp253IngressCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.service = self.build_service(self.repository)

    async def surfaces(self, kind, record_id, *, health=True):
        """Every output that can name the record, as text."""

        outputs = {
            "inspection": json.dumps(self.inspect_record(kind, record_id)),
            "stuck list": json.dumps(self.service.stuck_governance_records()),
        }
        if health:
            outputs["health"] = json.dumps(self.service.health_summary())
        tool = registered_tools(get_registered_server())["inspect_governance_record"]
        with patch.object(governance_tools.GOVERNANCE, "require", return_value=self.service):
            outputs["mcp tool"] = await tool.run(
                {"record_kind": kind, "record_id": record_id}
            )
        app = self.mount()
        async with ingress_client(app) as client:
            for path in (self.review_path(record_id, kind), "/records"):
                response = await client.get(path)
                self.assertEqual(response.status_code, 200)
                outputs[f"ingress {path}"] = response.text
        return outputs

    def assert_never_echoed(self, outputs):
        for where, text in outputs.items():
            assert_absent(self, text, where)
        assert_absent(self, self.audit_path.read_text(encoding="utf-8")
                      if self.audit_path.exists() else "", "audit")

    async def test_secret_event_names_on_a_policy_snapshot_mismatch_record(self):
        (plan_id,) = await self.projection_failed_plans()
        path = self.repository._path(plan_id)

        def add_events(data):
            # Provenance: the last real event of the record, copied with a
            # configured value as its name (a damaged or hostile record).
            for name in SECRETS:
                data["events"].append({**data["events"][-1], "event": name})

        rewrite_json(path, add_events)
        self.restart()
        self.assertIn(TOKEN_SECRET, path.read_text(encoding="utf-8"))
        with no_home_assistant(self):
            view = self.inspect_record("change_plan", plan_id)
            self.assertEqual(view["failure"]["reason_code"], "policy_snapshot_mismatch")
            self.assertEqual([row["event"] for row in view["events"][-2:]],
                             [UNRECOGNIZED, UNRECOGNIZED])
            self.assertIs(view["acknowledgeable"], True)
            outcome = self.acknowledge("change_plan", plan_id)
            self.assertEqual(outcome["status"], "acknowledged")
            self.assert_never_echoed(await self.surfaces("change_plan", plan_id))

    async def test_secret_stored_plan_identifier(self):
        (plan_id,) = await self.projection_failed_plans()
        path = self.repository._path(plan_id)
        # A navigation rebuild after the projection pass, then the damage, so
        # the stuck list reads the record as stored.
        self.repository.rebuild_navigation_index()
        rewrite_json(path, lambda data: data.update(plan_id=TOKEN_SECRET))
        with no_home_assistant(self):
            view = self.inspect_record("change_plan", plan_id)
            self.assertEqual(view["record_id"], plan_id)
            self.assertEqual(view["stored_identity"], "stored_identifier_mismatch")
            self.assertIs(view["acknowledgeable"], False)
            outcome = self.acknowledge("change_plan", plan_id)
            self.assertEqual(outcome["refusal_reason"], "record_not_acknowledgeable")
            stuck = self.service.stuck_governance_records()["projection_failed_plans"]
            self.assertEqual(stuck["unrecognized_identifier_count"], 1)
            self.assertEqual(stuck["unacknowledged_ids"], [])
            self.assertEqual(stuck["unacknowledged_count"], 1)
            # Not health_summary: its navigation rebuild cannot index a
            # non-hexadecimal stored plan identifier and raises. That
            # predates HAMCP-253 and is reported separately; the health row
            # screen is covered below.
            self.assert_never_echoed(
                await self.surfaces("change_plan", plan_id, health=False)
            )

    async def test_health_identification_never_lists_a_stored_secret(self):
        from ha_mcp_engineering.governance.projection_diagnostics import (
            projection_failure_identification,
        )

        (plan_id,) = await self.projection_failed_plans()
        plan = self.repository.read_for_inspection(plan_id)
        error = self.service._projection_failure_for_plan(plan)
        self.assertIsNotNone(error)
        plan.plan_id = TOKEN_SECRET
        rows, truncated = projection_failure_identification([(plan, error)])
        self.assertEqual(rows[0]["plan_id"], UNRECOGNIZED)
        self.assertEqual(rows[0]["category"], "policy_subject_hash_not_reproducible")
        self.assertIs(truncated, False)


class StoredTaskMetadataIsNeverEchoedTests(ManualReviewTaskCase):
    def build(self, lifecycle_gateway):
        service = super().build(lifecycle_gateway)
        service.sensitive_values = SCREENED_VALUES
        return service

    async def test_secret_stored_task_and_plan_identifiers(self):
        # A task's identifiers are bound by its materialization, so a stored
        # change makes the record unreadable: it is reported with the
        # requested identifier and nothing it stores.
        repository = self.recovered.task_repository
        path = repository._path(self.task_id)
        rewrite_json(path, lambda data: data.update(task_id=TOKEN_SECRET, plan_id=SECRET))
        make_cold(repository)
        with no_home_assistant(self):
            view = self.recovered.inspect_governance_record(
                record_kind="execution_task", record_id=self.task_id
            )
            self.assertEqual(view["record_id"], self.task_id)
            self.assertIs(view["readable"], False)
            outcome = self.recovered.acknowledge_governance_record(
                record_kind="execution_task", record_id=self.task_id,
                action="acknowledge", classification="historical_record",
                reason=REASON, record_fingerprint="0" * 64, principal=ADMIN,
            )
            self.assertEqual(outcome["refusal_reason"], "record_unreadable")
            outputs = {
                "inspection": json.dumps(view),
                "stuck list": json.dumps(self.recovered.stuck_governance_records()),
            }
            app = IngressApprovalApplication(
                RuntimeShim(self.recovered),
                administrator_verifier=StaticAdministratorVerifier({}),
            ).create()
            async with ingress_client(app) as client:
                for route in (f"/records/execution_task/{self.task_id}", "/records"):
                    response = await client.get(route)
                    self.assertEqual(response.status_code, 200)
                    outputs[route] = response.text
        self.assertIn(TOKEN_SECRET, path.read_text(encoding="utf-8"))
        # Normal health quarantines the corrupt task and then fails closed on
        # the plan it belonged to; that is unchanged, and names nothing stored.
        try:
            outputs["health"] = json.dumps(self.recovered.health_summary())
        except GovernanceError as exc:
            outputs["health"] = f"{exc.code.value} {exc} {exc.details!r}"
        outputs["audit"] = self.audit_path.read_text(encoding="utf-8")
        for where, text in outputs.items():
            assert_absent(self, text, where)

    async def test_task_view_reports_a_stored_identifier_mismatch_as_a_code(self):
        # The view itself, given a task whose stored identifiers differ from
        # the identifier it was read by.
        task = self.recovered.task_repository.read_for_inspection(self.task_id)
        task.task_id, task.plan_id = TOKEN_SECRET, SECRET
        task.events[-1].event_type = TOKEN_SECRET
        view = record_inspection.task_view(
            task, self.task_id, sensitive_values=SCREENED_VALUES
        )
        self.assertEqual(view["record_id"], self.task_id)
        self.assertEqual(view["stored_identity"], "stored_identifier_mismatch")
        self.assertEqual(view["plan_id"], UNRECOGNIZED)
        self.assertEqual(view["events"][-1]["event"], UNRECOGNIZED)
        self.assertIs(view["acknowledgeable"], False)
        assert_absent(self, json.dumps(view), "task view")
        task.task_id, task.plan_id = self.task_id, self.plan_id
        view = record_inspection.task_view(
            task, self.task_id, sensitive_values=SCREENED_VALUES
        )
        self.assertEqual(view["stored_identity"], "matches_request")
        self.assertEqual(view["plan_id"], self.plan_id)
        self.assertIs(view["acknowledgeable"], True)


# -- R1-M3 ------------------------------------------------------------------


class AuditCoordinationTests(Hamcp253Case):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        (self.target,) = await self.projection_failed_plans()

    def audit_bytes(self):
        return self.audit_path.read_bytes() if self.audit_path.exists() else b""

    def assert_audit_recorded_is_truthful(self, outcome, before):
        self.assertIs(outcome["audit_recorded"], self.audit_bytes() != before)

    def assert_still_warns(self):
        health = self.service.health_summary()
        self.assertEqual(health["projection_failure_warning"], PROJECTION_WARNING)
        self.assertEqual(self.bucket(health)["unacknowledged_ids"], [self.target])
        return health

    async def test_disabled_audit_refuses_before_the_ledger_changes(self):
        self.service.audit = AuditLogger(
            str(self.audit_path), "test-access-secret-value", enabled=False
        )
        before = self.audit_bytes()
        with no_home_assistant(self):
            outcome = self.acknowledge("change_plan", self.target)
        self.assertEqual(outcome["status"], "refused")
        self.assertEqual(outcome["refusal_reason"], "audit_unavailable")
        self.assertIsNone(outcome["ledger_sequence"])
        self.assertIs(outcome["audit_recorded"], False)
        self.assert_audit_recorded_is_truthful(outcome, before)
        self.assertFalse(self.ledger_path().exists())
        self.assert_still_warns()

    async def test_disabled_audit_refuses_revoke_and_reclassify(self):
        self.assert_success(self.acknowledge("change_plan", self.target), "acknowledged")
        ledger = self.ledger_path().read_bytes()
        self.service.audit = AuditLogger(
            str(self.audit_path), "test-access-secret-value", enabled=False
        )
        for overrides in ({"classification": "current_defect"},
                          {"action": "revoke", "classification": None}):
            with self.subTest(**overrides):
                outcome = self.acknowledge("change_plan", self.target, **overrides)
                self.assertEqual(outcome["refusal_reason"], "audit_unavailable")
                self.assertIs(outcome["audit_recorded"], False)
                self.assertEqual(self.ledger_path().read_bytes(), ledger)
        view = self.inspect_record("change_plan", self.target)
        self.assertEqual(view["acknowledgement"]["classification"], "historical_record")

    async def test_unconfigured_audit_path_is_unavailable(self):
        self.service.audit = AuditLogger("", "test-access-secret-value")
        outcome = self.acknowledge("change_plan", self.target)
        self.assertEqual(outcome["refusal_reason"], "audit_unavailable")
        self.assertFalse(self.ledger_path().exists())

    async def test_sink_reporting_failure_abandons_the_decision(self):
        for failure in ({"return_value": 0}, {"side_effect": OSError("synthetic")}):
            with self.subTest(failure=failure):
                before, decisions = self.audit_bytes(), len(self.decisions())
                with patch.object(self.service.audit, "write_batch", **failure):
                    with no_home_assistant(self):
                        outcome = self.acknowledge("change_plan", self.target)
                self.assertEqual(outcome["status"], "refused")
                self.assertEqual(outcome["refusal_reason"], "audit_write_failed")
                self.assertIsNone(outcome["ledger_sequence"])
                self.assertIs(outcome["audit_recorded"], False)
                self.assert_audit_recorded_is_truthful(outcome, before)
                decision = self.decisions()[-1]
                self.assertEqual(len(self.decisions()), decisions + 1)
                self.assertEqual(self.completions()[decision["sequence"]], "abandoned")
                health = self.assert_still_warns()
                self.assertEqual(health["record_acknowledgement"]["audit_pending_count"], 0)
                view = self.inspect_record("change_plan", self.target)
                self.assertEqual(view["acknowledgement"]["state"], "unacknowledged")
                self.assertIs(view["acknowledgement"]["audit_pending"], False)
        self.assertEqual(
            self.service.health_summary()["record_acknowledgement"]["audit_abandoned_count"], 2
        )
        # A working sink afterwards records a new, effective decision.
        before = self.audit_bytes()
        outcome = self.acknowledge("change_plan", self.target)
        self.assert_success(outcome, "acknowledged")
        self.assert_audit_recorded_is_truthful(outcome, before)
        self.assertEqual(outcome["ledger_sequence"], 5)
        self.assertEqual(self.completions(), {1: "abandoned", 3: "abandoned", 5: "recorded"})

    async def test_audit_failure_on_a_refusal_reports_audit_recorded_false(self):
        before = self.audit_bytes()
        with patch.object(self.service.audit, "write_batch", return_value=0):
            outcome = self.acknowledge("change_plan", self.target, fingerprint="0" * 64)
        self.assertEqual(outcome["refusal_reason"], "record_fingerprint_stale")
        self.assertIs(outcome["audit_recorded"], False)
        self.assertEqual(self.audit_bytes(), before)
        outcome = self.acknowledge("change_plan", self.target, fingerprint="0" * 64)
        self.assertIs(outcome["audit_recorded"], True)
        self.assertNotEqual(self.audit_bytes(), before)

    def decision_audits(self, decision):
        identifier = ledger_module.audit_event_id(decision)
        return [entry for entry in self.acknowledgement_audits()
                if entry.get("audit_event_id") == identifier]

    async def test_crash_before_completion_is_pending_then_reconciled_at_start(self):
        writes = []

        def fault(stage):
            if stage == "before_write":
                writes.append(stage)
                if len(writes) == 2:
                    raise OSError("synthetic crash before the completion")

        self.service.acknowledgements.fault_hook = fault
        before = self.audit_bytes()
        with no_home_assistant(self):
            outcome = self.acknowledge("change_plan", self.target)
        self.service.acknowledgements.fault_hook = None
        self.assertEqual(outcome["status"], "pending")
        self.assertEqual(outcome["refusal_reason"], "acknowledgement_audit_reconciliation_pending")
        self.assertEqual(outcome["ledger_sequence"], 1)
        self.assertIs(outcome["audit_recorded"], True)
        self.assert_audit_recorded_is_truthful(outcome, before)
        (decision,) = self.decisions()
        self.assertEqual(self.completions(), {})
        self.assertEqual(len(self.decision_audits(decision)), 1)
        # Pending is reported and never in effect.
        health = self.assert_still_warns()
        self.assertEqual(health["record_acknowledgement"]["audit_pending_count"], 1)
        view = self.inspect_record("change_plan", self.target)
        self.assertEqual(view["acknowledgement"]["state"], "unacknowledged")
        self.assertIs(view["acknowledgement"]["audit_pending"], True)
        # Inspection and health never reconcile: nothing was written.
        self.assertEqual(self.completions(), {})

        self.restart()
        self.assertEqual(self.completions(), {1: "recorded"})
        # The audit event already written is not written again.
        self.assertEqual(len(self.decision_audits(decision)), 1)
        health = self.service.health_summary()
        self.assertIsNone(health["projection_failure_warning"])
        self.assertEqual(health["record_acknowledgement"]["audit_pending_count"], 0)
        self.assertEqual(self.bucket(health)["acknowledged_historical_ids"], [self.target])

    def interrupted_decision(self):
        """A decision whose process died before its audit write."""

        view = self.inspect_record("change_plan", self.target)
        body = {
            "entry_type": ledger_module.DECISION, "record_kind": "change_plan",
            "record_id": self.target, "action": "acknowledge",
            "classification": "historical_record", "reason": REASON,
            "record_fingerprint": view["record_fingerprint"], "principal": ADMIN,
            "recorded_at": self.service._timestamp(),
        }
        with self.service.acknowledgements.transaction() as transaction:
            return transaction.append(body)

    async def test_crash_before_the_audit_write_is_reconciled_by_the_next_writer(self):
        decision = self.interrupted_decision()
        self.assertEqual(self.decision_audits(decision), [])
        self.assert_still_warns()
        with no_home_assistant(self):
            outcome = self.acknowledge("change_plan", self.target)
        # The reconciled decision already says the same thing.
        self.assertEqual(outcome["status"], "no_op")
        (audit,) = self.decision_audits(decision)
        self.assertIs(audit["parameters"]["audit_reconciled"], True)
        self.assertEqual(audit["result_status"], "acknowledged")
        self.assertEqual(audit["parameters"]["ledger_sequence"], 1)
        self.assertEqual(self.completions(), {1: "recorded"})
        self.assertIsNone(self.service.health_summary()["projection_failure_warning"])

    async def test_unreconcilable_pending_decision_blocks_new_decisions(self):
        decision = self.interrupted_decision()
        ledger = self.ledger_path().read_bytes()
        with patch.object(self.service.audit, "write_batch", return_value=0):
            outcome = self.acknowledge("change_plan", self.target)
        self.assertEqual(outcome["status"], "refused")
        self.assertEqual(outcome["refusal_reason"], "acknowledgement_audit_pending")
        self.assertIs(outcome["audit_recorded"], False)
        self.assertEqual(self.ledger_path().read_bytes(), ledger)
        # Without an audit destination a restart leaves it pending too.
        self.service.audit = AuditLogger(
            str(self.audit_path), "test-access-secret-value", enabled=False
        )
        self.service._reconcile_acknowledgement_audits_at_start()
        self.assertEqual(self.ledger_path().read_bytes(), ledger)
        self.assertEqual(self.decision_audits(decision), [])
        with self.service.acknowledgements.transaction() as transaction:
            with self.assertRaises(AcknowledgementStorageError) as raised:
                transaction.append(decision["body"])
        self.assertEqual(str(raised.exception), "acknowledgement_audit_pending")


class CompletionValidationTests(Hamcp253Case):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        (self.target,) = await self.projection_failed_plans()
        self.assert_success(self.acknowledge("change_plan", self.target), "acknowledged")
        self.assert_success(
            self.acknowledge("change_plan", self.target, classification="current_defect"),
            "reclassified",
        )

    def rechain(self, change):
        """Edit the ledger and recompute every hash, so only meaning differs."""

        path = self.ledger_path()
        value = json.loads(path.read_text(encoding="utf-8"))
        change(value["events"])
        previous = "0" * 64
        for index, event in enumerate(value["events"], 1):
            event["sequence"], event["previous"] = index, previous
            event["hash"] = ledger_module.fingerprint(
                {key: event[key] for key in ("sequence", "body", "previous")}
            )
            previous = event["hash"]
        replacement = path.with_suffix(".tamper")
        replacement.write_text(json.dumps(value), encoding="utf-8")
        os.replace(replacement, path)

    def assert_rejected(self, change):
        self.rechain(change)
        with self.assertRaises(AcknowledgementStorageError) as raised:
            self.service.acknowledgements.read()
        self.assertEqual(str(raised.exception), "acknowledgement_storage_invalid")
        health = self.service.health_summary()
        self.assertEqual(health["record_acknowledgement"]["ledger_status"], "unavailable")
        self.assertEqual(health["projection_failure_warning"], PROJECTION_WARNING)

    async def test_untampered_ledger_reads(self):
        self.rechain(lambda events: None)
        events = self.service.acknowledgements.read()
        self.assertEqual(ledger_module.completions(events), {1: "recorded", 3: "recorded"})

    async def test_completion_with_a_wrong_audit_identifier(self):
        def change(events):
            events[1]["body"]["audit_event_id"] = "0" * 64
        self.assert_rejected(change)

    async def test_completion_of_the_wrong_decision(self):
        def change(events):
            events[3]["body"]["decision_sequence"] = 1
            events[3]["body"]["audit_event_id"] = events[1]["body"]["audit_event_id"]
        self.assert_rejected(change)

    async def test_completion_naming_a_later_or_its_own_entry(self):
        for target in (4, 5, 0):
            with self.subTest(target=target):
                self.setUp_ledger = self.ledger_path().read_bytes()

                def change(events, target=target):
                    events[3]["body"]["decision_sequence"] = target
                self.assert_rejected(change)
                self.ledger_path().write_bytes(self.setUp_ledger)

    async def test_completion_naming_a_completion(self):
        def change(events):
            events[3]["body"]["decision_sequence"] = 2
        self.assert_rejected(change)

    async def test_malformed_completion_body(self):
        for key, value in (("outcome", "maybe"), ("decision_sequence", "1"),
                           ("recorded_at", "2026-11-03T00:00:00"), ("extra", 1)):
            with self.subTest(key=key):
                saved = self.ledger_path().read_bytes()

                def change(events, key=key, value=value):
                    events[1]["body"][key] = value
                self.rechain(change)
                with self.assertRaises(AcknowledgementStorageError) as raised:
                    self.service.acknowledgements.read()
                self.assertEqual(str(raised.exception), "acknowledgement_body_invalid")
                self.ledger_path().write_bytes(saved)

    async def test_decision_without_entry_type_is_invalid(self):
        def change(events):
            del events[0]["body"]["entry_type"]
        self.rechain(change)
        with self.assertRaises(AcknowledgementStorageError) as raised:
            self.service.acknowledgements.read()
        self.assertEqual(str(raised.exception), "acknowledgement_body_invalid")


if __name__ == "__main__":
    unittest.main()
