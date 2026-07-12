import asyncio
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalState,
    PlanStatus,
    RiskLevel,
)
from ha_mcp_engineering.governance.normalize import (  # noqa: E402
    normalize_automation,
    stable_hash,
    state_fingerprint,
    structured_diff,
)
from ha_mcp_engineering.governance.risk import classify_risk  # noqa: E402
from ha_mcp_engineering.governance.models import ChangeOperation  # noqa: E402
from ha_mcp_engineering.governance.service import ChangeGovernanceService  # noqa: E402
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
    ChangePlanStorageError,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402


CURRENT = {
    "alias": "Porch light",
    "description": "Original",
    "mode": "single",
    "trigger": [{"platform": "state", "entity_id": "binary_sensor.motion"}],
    "condition": [],
    "action": [{"service": "notify.mobile_app", "data": {"message": "Motion"}}],
}


class Clock:
    def __init__(self):
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class FakeGateway:
    def __init__(self, configs=None):
        self.configs = copy.deepcopy(configs or {})
        self.write_calls = 0
        self.fail_write = False
        self.read_back_mismatch = False
        self.validation_result = {"result": "valid", "errors": None}
        self.write_started = asyncio.Event()
        self.release_write = None

    async def get(self, automation_id):
        return copy.deepcopy(self.configs.get(automation_id))

    async def write(self, automation_id, config):
        self.write_calls += 1
        self.write_started.set()
        if self.release_write is not None:
            await self.release_write.wait()
        if self.fail_write:
            raise RuntimeError("safe fake write failure")
        stored = copy.deepcopy(config)
        if self.read_back_mismatch:
            stored["alias"] = "Unexpected read-back"
        self.configs[automation_id] = stored
        return {"result": "ok"}

    async def validate(self):
        return copy.deepcopy(self.validation_result)


class GovernanceTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.gateway = FakeGateway({"porch": CURRENT})
        self.audit_path = Path(self.tempdir.name) / "audit.jsonl"
        self.repository = ChangePlanRepository(Path(self.tempdir.name) / "plans")
        self.service = ChangeGovernanceService(
            self.repository,
            self.gateway,
            AuditLogger(str(self.audit_path), "test-access-secret-value"),
            now=self.clock,
            sensitive_values=("test-access-secret-value", "test-ha-token-value"),
        )
        self.telemetry, self.context_token = begin_request("governance-request-123")
        self.telemetry.caller_id = "caller-test"

    async def asyncTearDown(self):
        end_request(self.context_token)
        self.tempdir.cleanup()

    async def update_plan(self, proposed=None, **kwargs):
        config = copy.deepcopy(proposed or CURRENT)
        config["description"] = config.get("description", "") + " updated"
        return await self.service.create_plan(
            title="Update porch automation",
            description="Governed test update",
            operation="update_automation",
            automation_id="porch",
            proposed_config=config,
            **kwargs,
        )

    async def approved_plan(self, proposed=None):
        created = await self.update_plan(proposed)
        approved = self.service.approve(created["plan_id"], created["plan_hash"])
        return created, approved


class PlanCreationTests(GovernanceTestCase):
    async def test_create_automation_plan(self):
        config = copy.deepcopy(CURRENT)
        result = await self.service.create_plan(
            title="Create",
            description="New automation",
            operation="create_automation",
            automation_id="new_automation",
            proposed_config=config,
        )
        self.assertEqual(result["operation"], "create_automation")
        self.assertIsNone(result["current_config"])
        self.assertEqual(result["status"], "awaiting_approval")
        self.assertEqual(result["risk"]["level"], "medium")

    async def test_update_automation_plan_and_dry_run(self):
        result = await self.update_plan()
        self.assertEqual(result["operation"], "update_automation")
        self.assertTrue(result["dry_run_results"]["has_changes"])
        self.assertIn("description", [item["field"] for item in result["dry_run_results"]["changed_fields"]])
        self.assertEqual(result["requested_by"], "caller-test")

    async def test_invalid_config_persists_validation_failure(self):
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_plan(
                title="Invalid",
                description="Missing action",
                operation="create_automation",
                automation_id="invalid",
                proposed_config={"trigger": []},
            )
        self.assertEqual(raised.exception.code, ErrorCode.AUTOMATION_VALIDATION_FAILED)
        plans = self.repository.list()
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].status, PlanStatus.VALIDATION_FAILED)

    async def test_no_change_does_not_create_plan(self):
        result = await self.service.create_plan(
            title="No change",
            description="No change",
            operation="update_automation",
            automation_id="porch",
            proposed_config=copy.deepcopy(CURRENT),
        )
        self.assertEqual(result["outcome"], "no_change")
        self.assertFalse(result["plan_created"])
        self.assertEqual(self.repository.list(), [])

    async def test_existing_id_collision(self):
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_plan(
                title="Collision",
                description="Collision",
                operation="create_automation",
                automation_id="porch",
                proposed_config=copy.deepcopy(CURRENT),
            )
        self.assertEqual(raised.exception.code, ErrorCode.CONFIGURATION_CONFLICT)

    async def test_missing_update_target(self):
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_plan(
                title="Missing",
                description="Missing",
                operation="update_automation",
                automation_id="missing",
                proposed_config=copy.deepcopy(CURRENT),
            )
        self.assertEqual(raised.exception.code, ErrorCode.AUTOMATION_NOT_FOUND)

    async def test_expiration_marks_plan_expired(self):
        result = await self.update_plan(expiration_minutes=5)
        self.clock.advance(minutes=6)
        expired = self.service.get_plan(result["plan_id"])
        self.assertEqual(expired["status"], "expired")

    async def test_new_plan_supersedes_prior_target_plan(self):
        first = await self.update_plan()
        proposed = copy.deepcopy(CURRENT)
        proposed["alias"] = "Second proposal"
        second = await self.update_plan(proposed)
        self.assertEqual(self.service.get_plan(first["plan_id"])["status"], "superseded")
        self.assertEqual(second["status"], "awaiting_approval")

    async def test_secret_fields_are_rejected_without_persistence(self):
        config = copy.deepcopy(CURRENT)
        config["trigger"] = [{"platform": "webhook", "webhook_id": "not-a-real-secret"}]
        with self.assertRaises(GovernanceError):
            await self.service.create_plan(
                title="Unsafe", description="Unsafe", operation="create_automation",
                automation_id="unsafe", proposed_config=config,
            )
        self.assertEqual(self.repository.list(), [])

    async def test_secret_value_under_benign_key_is_not_persisted(self):
        config = copy.deepcopy(CURRENT)
        config["description"] = "test-access-secret-value"
        with self.assertRaises(GovernanceError):
            await self.service.create_plan(
                title="Unsafe value", description="Unsafe", operation="create_automation",
                automation_id="unsafe_value", proposed_config=config,
            )
        self.assertEqual(self.repository.list(), [])

    async def test_secret_value_is_removed_from_caller_context(self):
        result = await self.update_plan(
            caller_context={"ticket": "safe-123", "note": "test-access-secret-value"}
        )
        self.assertEqual(result["caller_context"], {"ticket": "safe-123"})
        stored = (Path(self.tempdir.name) / "plans" / f"{result['plan_id']}.json").read_text()
        self.assertNotIn("test-access-secret-value", stored)


class NormalizationAndRiskTests(unittest.TestCase):
    def test_normalization_is_deterministic_and_preserves_sequence_order(self):
        first = {"actions": [{"service": "notify.one"}, {"delay": 1}], "trigger": [], "condition": []}
        second = {"condition": [], "trigger": [], "action": [{"service": "notify.one"}, {"delay": 1}]}
        self.assertEqual(normalize_automation(first), normalize_automation(second))
        self.assertEqual(stable_hash(normalize_automation(first)), stable_hash(normalize_automation(second)))
        reversed_actions = copy.deepcopy(second)
        reversed_actions["action"].reverse()
        self.assertNotEqual(normalize_automation(first), normalize_automation(reversed_actions))

    def test_unknown_fields_are_preserved(self):
        normalized = normalize_automation({"trigger": [], "action": [], "future_field": {"x": 1}})
        self.assertEqual(normalized["future_field"], {"x": 1})

    def test_low_risk_alias_change(self):
        proposed = copy.deepcopy(CURRENT)
        proposed["alias"] = "Renamed"
        risk = classify_risk(ChangeOperation.UPDATE_AUTOMATION, structured_diff(CURRENT, proposed), proposed)
        self.assertEqual(risk.level, RiskLevel.LOW)

    def test_medium_risk_trigger_change(self):
        proposed = copy.deepcopy(CURRENT)
        proposed["trigger"] = [{"platform": "time", "at": "12:00:00"}]
        risk = classify_risk(ChangeOperation.UPDATE_AUTOMATION, structured_diff(CURRENT, proposed), proposed)
        self.assertEqual(risk.level, RiskLevel.MEDIUM)

    def test_medium_risk_mode_change(self):
        proposed = copy.deepcopy(CURRENT)
        proposed["mode"] = "parallel"
        risk = classify_risk(ChangeOperation.UPDATE_AUTOMATION, structured_diff(CURRENT, proposed), proposed)
        self.assertEqual(risk.level, RiskLevel.MEDIUM)

    def test_high_risk_lock_action(self):
        proposed = copy.deepcopy(CURRENT)
        proposed["action"] = [{"service": "lock.unlock", "target": {"entity_id": "lock.example"}}]
        risk = classify_risk(ChangeOperation.UPDATE_AUTOMATION, structured_diff(CURRENT, proposed), proposed)
        self.assertEqual(risk.level, RiskLevel.HIGH)
        self.assertFalse(risk.apply_allowed)

    def test_high_risk_broad_target(self):
        proposed = copy.deepcopy(CURRENT)
        proposed["action"] = [{"service": "light.turn_off", "target": {"entity_id": "all"}}]
        risk = classify_risk(ChangeOperation.UPDATE_AUTOMATION, structured_diff(CURRENT, proposed), proposed)
        self.assertEqual(risk.level, RiskLevel.HIGH)

    def test_high_risk_large_area_target(self):
        proposed = copy.deepcopy(CURRENT)
        proposed["action"] = [{"service": "light.turn_off", "target": {"area_id": ["a", "b", "c", "d"]}}]
        risk = classify_risk(ChangeOperation.UPDATE_AUTOMATION, structured_diff(CURRENT, proposed), proposed)
        self.assertEqual(risk.level, RiskLevel.HIGH)

    def test_message_template_is_not_actionable_high_risk(self):
        proposed = copy.deepcopy(CURRENT)
        proposed["action"] = [{"service": "notify.example", "data": {"message": "{{ states('sensor.example') }}"}}]
        risk = classify_risk(ChangeOperation.UPDATE_AUTOMATION, structured_diff(CURRENT, proposed), proposed)
        self.assertEqual(risk.level, RiskLevel.MEDIUM)

    def test_high_risk_water_action(self):
        proposed = copy.deepcopy(CURRENT)
        proposed["action"] = [{"service": "switch.turn_off", "target": {"entity_id": "switch.example_water"}}]
        risk = classify_risk(ChangeOperation.UPDATE_AUTOMATION, structured_diff(CURRENT, proposed), proposed)
        self.assertEqual(risk.level, RiskLevel.HIGH)

    def _create_risk(self, **overrides):
        proposed = copy.deepcopy(CURRENT)
        proposed.update(overrides)
        return classify_risk(
            ChangeOperation.CREATE_AUTOMATION,
            structured_diff(None, proposed),
            proposed,
        )

    def test_smoke_test_alias_only_is_not_high(self):
        self.assertNotEqual(self._create_risk(alias="Smoke Test").level, RiskLevel.HIGH)

    def test_fire_description_only_is_not_high(self):
        self.assertNotEqual(self._create_risk(description="fire drill text").level, RiskLevel.HIGH)

    def test_all_in_alias_only_is_not_high(self):
        self.assertNotEqual(self._create_risk(alias="all").level, RiskLevel.HIGH)

    def test_never_fire_custom_event_is_not_high(self):
        risk = self._create_risk(trigger=[{"platform": "event", "event_type": "never_fire"}])
        self.assertNotEqual(risk.level, RiskLevel.HIGH)

    def test_logbook_message_sensitive_words_are_not_high(self):
        risk = self._create_risk(action=[{"service": "logbook.log", "data": {"message": "alarm lock fire"}}])
        self.assertNotEqual(risk.level, RiskLevel.HIGH)

    def test_notification_text_garage_door_is_not_high(self):
        risk = self._create_risk(action=[{"service": "notify.mobile_app", "data": {"message": "garage door"}}])
        self.assertNotEqual(risk.level, RiskLevel.HIGH)

    def test_lock_lock_action_is_high_with_structured_evidence(self):
        risk = self._create_risk(action=[{"service": "lock.lock", "target": {"entity_id": "lock.front"}}])
        self.assertEqual(risk.level, RiskLevel.HIGH)
        self.assertTrue(any(item["field"].endswith(".service") for item in risk.evidence))
        self.assertNotIn("lock.front", json.dumps(risk.evidence))

    def test_garage_cover_open_is_high(self):
        risk = self._create_risk(action=[{"service": "cover.open_cover", "target": {"entity_id": "cover.garage_door"}}])
        self.assertEqual(risk.level, RiskLevel.HIGH)

    def test_alarm_disarm_is_high(self):
        risk = self._create_risk(action=[{"service": "alarm_control_panel.alarm_disarm", "target": {"entity_id": "alarm_control_panel.home"}}])
        self.assertEqual(risk.level, RiskLevel.HIGH)

    def test_valve_shutoff_is_high(self):
        risk = self._create_risk(action=[{"service": "valve.close", "target": {"entity_id": "valve.main"}}])
        self.assertEqual(risk.level, RiskLevel.HIGH)

    def test_homeassistant_restart_is_high(self):
        self.assertEqual(self._create_risk(action=[{"service": "homeassistant.restart"}]).level, RiskLevel.HIGH)

    def test_broad_destructive_service_is_high(self):
        self.assertEqual(self._create_risk(action=[{"service": "hassio.host_reboot"}]).level, RiskLevel.HIGH)

    def test_dynamic_service_is_conservative_medium_with_warning(self):
        risk = self._create_risk(action=[{"service": "{{ dynamic_domain }}.{{ dynamic_service }}"}])
        self.assertEqual(risk.level, RiskLevel.MEDIUM)
        self.assertTrue(risk.warnings)
        self.assertTrue(any(item["trigger"] == "unresolved_dynamic_service" for item in risk.evidence))

    def test_risk_reasons_and_evidence_are_deterministic(self):
        proposed = {**copy.deepcopy(CURRENT), "action": [{"service": "lock.lock", "target": {"entity_id": "lock.front"}}]}
        first = classify_risk(ChangeOperation.CREATE_AUTOMATION, structured_diff(None, proposed), proposed)
        second = classify_risk(ChangeOperation.CREATE_AUTOMATION, structured_diff(None, proposed), proposed)
        self.assertEqual(first.reasons, second.reasons)
        self.assertEqual(first.evidence, second.evidence)


class ApprovalAndApplyTests(GovernanceTestCase):
    async def test_successful_apply_invalidates_dependency_index(self):
        created, _ = await self.approved_plan()
        with patch("ha_mcp_engineering.dependency.DEPENDENCY_ANALYSIS.invalidate") as invalidate:
            await self.service.apply(created["plan_id"], created["plan_hash"])
        invalidate.assert_called_once()

    async def test_valid_approval_is_separate_from_apply(self):
        created = await self.update_plan()
        approved = self.service.approve(created["plan_id"], created["plan_hash"], "Reviewed")
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(self.gateway.write_calls, 0)

    async def test_wrong_plan_hash(self):
        created = await self.update_plan()
        with self.assertRaises(GovernanceError) as raised:
            self.service.approve(created["plan_id"], "0" * 64)
        self.assertEqual(raised.exception.code, ErrorCode.APPROVAL_HASH_MISMATCH)

    async def test_expired_plan_cannot_be_approved(self):
        created = await self.update_plan()
        self.clock.advance(hours=2)
        with self.assertRaises(GovernanceError) as raised:
            self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.CHANGE_PLAN_EXPIRED)

    async def test_validation_failed_plan_cannot_be_approved(self):
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_plan(
                title="Invalid", description="Invalid", operation="create_automation",
                automation_id="invalid", proposed_config={"trigger": []},
            )
        plan_id = raised.exception.details["resource_id"]
        with self.assertRaises(GovernanceError) as approval:
            self.service.approve(plan_id, self.service.plan_hash(self.repository.get(plan_id)))
        self.assertEqual(approval.exception.code, ErrorCode.CHANGE_PLAN_NOT_APPROVED)

    async def test_high_risk_plan_cannot_be_approved(self):
        proposed = copy.deepcopy(CURRENT)
        proposed["action"] = [{"service": "lock.unlock", "target": {"entity_id": "lock.example"}}]
        created = await self.update_plan(proposed)
        with self.assertRaises(GovernanceError) as raised:
            self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.HIGH_RISK_CHANGE_REJECTED)

    async def test_repeated_approval_is_rejected(self):
        created = await self.update_plan()
        self.service.approve(created["plan_id"], created["plan_hash"])
        with self.assertRaises(GovernanceError) as raised:
            self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.APPROVAL_ALREADY_CONSUMED)

    async def test_plan_mutation_invalidates_approval(self):
        created, _ = await self.approved_plan()
        plan = self.repository.get(created["plan_id"])
        plan.proposed_config["description"] = "mutated after approval"
        self.repository.save(plan)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.APPROVAL_HASH_MISMATCH)

    async def test_approved_update_applies_and_verifies(self):
        created, _ = await self.approved_plan()
        result = await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.gateway.write_calls, 1)
        plan = self.repository.get(created["plan_id"])
        self.assertEqual(plan.verification.status, "passed")
        self.assertEqual(plan.approval.state, ApprovalState.CONSUMED)
        self.assertEqual(plan.apply_request_id, "governance-request-123")

    async def test_approved_create_applies(self):
        proposed = copy.deepcopy(CURRENT)
        created = await self.service.create_plan(
            title="Create", description="Create", operation="create_automation",
            automation_id="new", proposed_config=proposed,
        )
        self.service.approve(created["plan_id"], created["plan_hash"])
        result = await self.service.apply(created["plan_id"])
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.gateway.configs["new"], proposed)

    async def test_apply_without_approval(self):
        created = await self.update_plan()
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.CHANGE_PLAN_NOT_APPROVED)
        audit = [json.loads(line) for line in self.audit_path.read_text().splitlines()]
        self.assertEqual(audit[-1]["event"], "change_apply_rejected")
        self.assertEqual(audit[-1]["result_status"], "rejected")
        self.assertEqual(audit[-1]["error_code"], "change_plan_not_approved")

    async def test_expired_approval_cannot_apply(self):
        created, _ = await self.approved_plan()
        self.clock.advance(hours=2)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.CHANGE_PLAN_EXPIRED)

    async def test_stale_target_state(self):
        created, _ = await self.approved_plan()
        self.gateway.configs["porch"]["alias"] = "External change"
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.STALE_TARGET_STATE)
        self.assertEqual(self.gateway.write_calls, 0)

    async def test_duplicate_apply_is_idempotent(self):
        created, _ = await self.approved_plan()
        await self.service.apply(created["plan_id"])
        duplicate = await self.service.apply(created["plan_id"])
        self.assertEqual(duplicate["status"], "already_applied")
        self.assertEqual(self.gateway.write_calls, 1)

    async def test_concurrent_apply_writes_once(self):
        created, _ = await self.approved_plan()
        first, second = await asyncio.gather(
            self.service.apply(created["plan_id"]),
            self.service.apply(created["plan_id"]),
        )
        self.assertEqual({first["status"], second["status"]}, {"applied", "already_applied"})
        self.assertEqual(self.gateway.write_calls, 1)

    async def test_target_lock_rejects_change_in_progress(self):
        created, _ = await self.approved_plan()
        lock = self.service._target_locks.setdefault("porch", asyncio.Lock())
        await lock.acquire()
        try:
            with self.assertRaises(GovernanceError) as raised:
                await self.service.apply(created["plan_id"])
        finally:
            lock.release()
        self.assertEqual(raised.exception.code, ErrorCode.CHANGE_IN_PROGRESS)
        self.assertEqual(self.gateway.write_calls, 0)

    async def test_home_assistant_write_failure_consumes_approval(self):
        created, _ = await self.approved_plan()
        self.gateway.fail_write = True
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.AUTOMATION_APPLY_FAILED)
        self.assertEqual(self.repository.get(created["plan_id"]).approval.state, ApprovalState.CONSUMED)
        audit = [json.loads(line) for line in self.audit_path.read_text().splitlines()]
        self.assertEqual(audit[-1]["event"], "change_apply_failed")
        self.assertEqual(audit[-1]["result_status"], "failure")
        self.assertEqual(audit[-1]["error_code"], "automation_apply_failed")

    async def test_read_back_mismatch_is_verification_failure(self):
        created, _ = await self.approved_plan()
        self.gateway.read_back_mismatch = True
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.AUTOMATION_VERIFICATION_FAILED)
        plan = self.repository.get(created["plan_id"])
        self.assertEqual(plan.status, PlanStatus.VERIFICATION_FAILED)
        self.assertTrue(plan.rollback.available)

    async def test_config_validation_failure(self):
        created, _ = await self.approved_plan()
        self.gateway.validation_result = {"result": "invalid", "errors": "safe fake error"}
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.AUTOMATION_VERIFICATION_FAILED)

    async def test_audit_and_event_request_ids_match(self):
        created, _ = await self.approved_plan()
        await self.service.apply(created["plan_id"])
        plan = self.repository.get(created["plan_id"])
        self.assertTrue(all(event.request_id == "governance-request-123" for event in plan.events))
        audits = [json.loads(line) for line in self.audit_path.read_text().splitlines()]
        self.assertTrue(all(item["request_id"] == "governance-request-123" for item in audits))
        self.assertIn("change_apply_succeeded", [item["event"] for item in audits])


class RollbackTests(GovernanceTestCase):
    async def test_successful_rollback_invalidates_dependency_index(self):
        created, _ = await self.approved_plan()
        await self.service.apply(created["plan_id"], created["plan_hash"])
        pending = await self.service.rollback_change(created["plan_id"])
        self.service.approve(created["plan_id"], pending["plan_hash"])
        with patch("ha_mcp_engineering.dependency.DEPENDENCY_ANALYSIS.invalidate") as invalidate:
            await self.service.rollback_change(created["plan_id"], pending["plan_hash"])
        invalidate.assert_called_once()

    async def applied_update(self):
        created, _ = await self.approved_plan()
        await self.service.apply(created["plan_id"])
        return created

    async def test_valid_separately_approved_rollback(self):
        created = await self.applied_update()
        requested = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(requested["status"], "rollback_pending")
        self.service.approve(created["plan_id"], requested["plan_hash"])
        result = await self.service.rollback_change(created["plan_id"], requested["plan_hash"])
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(normalize_automation(self.gateway.configs["porch"]), normalize_automation(CURRENT))

    async def test_rollback_without_approval(self):
        created = await self.applied_update()
        await self.service.rollback_change(created["plan_id"])
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_APPROVAL_REQUIRED)

    async def test_rollback_after_external_change(self):
        created = await self.applied_update()
        requested = await self.service.rollback_change(created["plan_id"])
        self.service.approve(created["plan_id"], requested["plan_hash"])
        self.gateway.configs["porch"]["alias"] = "External"
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"], requested["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.STALE_TARGET_STATE)

    async def test_rollback_write_failure(self):
        created = await self.applied_update()
        requested = await self.service.rollback_change(created["plan_id"])
        self.service.approve(created["plan_id"], requested["plan_hash"])
        self.gateway.fail_write = True
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"], requested["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_FAILED)

    async def test_rollback_verification_failure(self):
        created = await self.applied_update()
        requested = await self.service.rollback_change(created["plan_id"])
        self.service.approve(created["plan_id"], requested["plan_hash"])
        self.gateway.read_back_mismatch = True
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"], requested["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_FAILED)

    async def test_create_rollback_is_unavailable(self):
        config = copy.deepcopy(CURRENT)
        created = await self.service.create_plan(
            title="Create", description="Create", operation="create_automation",
            automation_id="new", proposed_config=config,
        )
        self.service.approve(created["plan_id"], created["plan_hash"])
        await self.service.apply(created["plan_id"])
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "plans"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_corrupt_record_is_quarantined(self):
        repository = ChangePlanRepository(self.root)
        bad = self.root / ("a" * 32 + ".json")
        bad.write_text("{bad json", encoding="utf-8")
        self.assertEqual(repository.list(), [])
        self.assertEqual(repository.corruption_count, 1)
        self.assertTrue(any(repository.quarantine.iterdir()))

    def test_direct_corrupt_record_is_storage_error(self):
        repository = ChangePlanRepository(self.root)
        plan_id = "b" * 32
        (self.root / f"{plan_id}.json").write_text("{bad json", encoding="utf-8")
        with self.assertRaises(ChangePlanStorageError):
            repository.get(plan_id)
        self.assertEqual(repository.corruption_count, 1)

    def test_invalid_or_missing_lookup_is_not_storage_error(self):
        repository = ChangePlanRepository(self.root)
        self.assertIsNone(repository.get("not-a-plan-id"))
        self.assertIsNone(repository.get("c" * 32))
        health = repository.health()
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["write_failures"], 0)
        self.assertEqual(health["corruption_count"], 0)

    def test_permission_failure_is_storage_error(self):
        repository = ChangePlanRepository(self.root)
        plan_id = "d" * 32
        with patch.object(Path, "read_text", side_effect=PermissionError("safe fake permission error")):
            with self.assertRaises(ChangePlanStorageError):
                repository.get(plan_id)

    def test_atomic_files_have_no_temporary_residue(self):
        async def create():
            gateway = FakeGateway({"porch": CURRENT})
            service = ChangeGovernanceService(ChangePlanRepository(self.root), gateway)
            config = copy.deepcopy(CURRENT)
            config["description"] = "changed"
            return await service.create_plan(
                title="Atomic", description="Atomic", operation="update_automation",
                automation_id="porch", proposed_config=config,
            )
        asyncio.run(create())
        self.assertEqual(len(list(self.root.glob("*.json"))), 1)
        self.assertEqual(list(self.root.glob("*.tmp-*")), [])

    def test_restart_survival(self):
        async def create():
            gateway = FakeGateway({"porch": CURRENT})
            repository = ChangePlanRepository(self.root)
            service = ChangeGovernanceService(repository, gateway)
            config = copy.deepcopy(CURRENT)
            config["description"] = "changed"
            return await service.create_plan(
                title="Restart", description="Restart", operation="update_automation",
                automation_id="porch", proposed_config=config,
            )
        created = asyncio.run(create())
        reloaded = ChangePlanRepository(self.root).get(created["plan_id"])
        self.assertEqual(reloaded.plan_id, created["plan_id"])

    def test_beta5_plan_without_risk_evidence_remains_readable(self):
        async def create():
            repository = ChangePlanRepository(self.root)
            service = ChangeGovernanceService(repository, FakeGateway({"porch": CURRENT}))
            config = copy.deepcopy(CURRENT)
            config["description"] = "changed"
            return await service.create_plan(
                title="Beta 5 compatibility", description="Compatibility",
                operation="update_automation", automation_id="porch",
                proposed_config=config,
            )
        created = asyncio.run(create())
        path = self.root / f"{created['plan_id']}.json"
        stored = json.loads(path.read_text())
        stored["risk"].pop("evidence", None)
        stored["risk"].pop("warnings", None)
        path.write_text(json.dumps(stored))
        reloaded = ChangePlanRepository(self.root).get(created["plan_id"])
        self.assertEqual(reloaded.risk.evidence, [])
        self.assertEqual(reloaded.risk.warnings, [])

    def test_restart_marks_abandoned_apply_failed(self):
        async def create():
            repository = ChangePlanRepository(self.root)
            service = ChangeGovernanceService(repository, FakeGateway({"porch": CURRENT}))
            config = copy.deepcopy(CURRENT)
            config["description"] = "changed"
            created = await service.create_plan(
                title="Recover", description="Recover", operation="update_automation",
                automation_id="porch", proposed_config=config,
            )
            plan = repository.get(created["plan_id"])
            plan.status = PlanStatus.APPLYING
            repository.save(plan)
            return created["plan_id"]
        plan_id = asyncio.run(create())
        ChangeGovernanceService(ChangePlanRepository(self.root), FakeGateway({"porch": CURRENT}))
        recovered = ChangePlanRepository(self.root).get(plan_id)
        self.assertEqual(recovered.status, PlanStatus.FAILED)
        self.assertEqual(recovered.failure_information["error_code"], "automation_apply_failed")

    def test_storage_failure_maps_to_stable_error(self):
        async def create():
            repository = ChangePlanRepository(self.root)
            service = ChangeGovernanceService(repository, FakeGateway({"porch": CURRENT}))
            config = copy.deepcopy(CURRENT)
            config["description"] = "changed"
            with patch.object(repository, "save", side_effect=ChangePlanStorageError("safe fake storage failure")):
                await service.create_plan(
                    title="Storage", description="Storage", operation="update_automation",
                    automation_id="porch", proposed_config=config,
                )
        with self.assertRaises(GovernanceError) as raised:
            asyncio.run(create())
        self.assertEqual(raised.exception.code, ErrorCode.CHANGE_PLAN_STORAGE_ERROR)

    def test_retention_removes_old_terminal_plan(self):
        async def create():
            clock = Clock()
            repository = ChangePlanRepository(self.root, retention_days=1)
            service = ChangeGovernanceService(repository, FakeGateway(), now=clock)
            try:
                await service.create_plan(
                    title="Invalid", description="Invalid", operation="create_automation",
                    automation_id="invalid", proposed_config={"trigger": []},
                )
            except GovernanceError:
                pass
            return repository, clock.value
        repository, created_at = asyncio.run(create())
        self.assertEqual(repository.cleanup(now=created_at + timedelta(days=2)), 1)


if __name__ == "__main__":
    unittest.main()
