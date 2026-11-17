"""HAMCP-419 (E052): governed automation writes accept a boolean initial_state.

Home Assistant 2026.7.2 declares ``vol.Optional(CONF_INITIAL_STATE):
cv.boolean`` (components/automation/config.py) and stores the submitted item
as given (components/config/automation.py ``_write_value``), so a JSON boolean
reads back exactly.  Non-boolean values are refused before any Home Assistant
read or write, and persisted validation-failed history keeps the shipped
writer's field set and error text.
"""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from tests.test_governance import CURRENT, GovernanceTestCase
from tests import test_beta34_historical_policy_projection as historical_tests
from tests.test_terminal_history_projection import value as terminal_fixture

from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from ha_mcp_engineering.governance.historical_policy import (
    terminal_nonexecution_projection_match,
)
from ha_mcp_engineering.governance.models import ChangePlan, PlanStatus
from ha_mcp_engineering.governance.normalize import (
    normalize_automation,
    stable_hash,
)
from ha_mcp_engineering.governance.resources import validate_resource
from ha_mcp_engineering.governance.validation import (
    AUTOMATION_CONFIGURATION_FIELDS,
    HISTORICAL_AUTOMATION_CONFIGURATION_FIELDS,
    validate_automation,
)


NON_BOOLEAN_VALUES = ("on", "true", 1, 0, None, [], {})


class _CountingGateway:
    """Wrap the fake so every Home Assistant read is counted as well."""

    def __init__(self, inner):
        self.inner = inner
        self.read_calls = 0

    async def get(self, automation_id):
        self.read_calls += 1
        return await self.inner.get(automation_id)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class InitialStateValidationTests(unittest.TestCase):
    def test_boolean_initial_state_is_valid(self):
        for state in (True, False):
            with self.subTest(state=state):
                valid, errors, _ = validate_automation(
                    "porch", {**CURRENT, "initial_state": state}
                )
                self.assertTrue(valid, errors)
                self.assertEqual(errors, [])

    def test_non_boolean_initial_state_is_refused(self):
        for state in NON_BOOLEAN_VALUES:
            with self.subTest(state=state):
                valid, errors, _ = validate_automation(
                    "porch", {**CURRENT, "initial_state": state}
                )
                self.assertFalse(valid)
                self.assertEqual(
                    errors, ["automation initial_state must be a boolean"]
                )

    def test_configuration_resource_path_uses_the_same_contract(self):
        valid, errors, _ = validate_resource(
            "automation", "porch", {**CURRENT, "initial_state": False}
        )
        self.assertTrue(valid, errors)
        valid, errors, _ = validate_resource(
            "automation", "porch", {**CURRENT, "initial_state": "off"}
        )
        self.assertFalse(valid)
        self.assertIn("automation initial_state must be a boolean", errors)

    def test_only_initial_state_is_added(self):
        self.assertEqual(
            AUTOMATION_CONFIGURATION_FIELDS
            - HISTORICAL_AUTOMATION_CONFIGURATION_FIELDS,
            {"initial_state"},
        )
        valid, errors, _ = validate_automation(
            "porch", {**CURRENT, "future_field": True}
        )
        self.assertFalse(valid)
        self.assertEqual(
            errors, ["automation config contains unsupported fields: future_field"]
        )

    def test_historical_field_set_keeps_shipped_error_text(self):
        # The shipped writer refused initial_state as an unsupported field
        # and applied no boolean check.
        for state in (True, "on"):
            with self.subTest(state=state):
                _, errors, _ = validate_automation(
                    "porch",
                    {**CURRENT, "initial_state": state},
                    configuration_fields=HISTORICAL_AUTOMATION_CONFIGURATION_FIELDS,
                )
                self.assertEqual(
                    errors,
                    ["automation config contains unsupported fields: initial_state"],
                )


class InitialStateGovernedWriteTests(GovernanceTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.counting = _CountingGateway(self.gateway)
        self.service.gateway = self.counting

    async def test_create_with_initial_state_applies_and_reads_back(self):
        for state in (True, False):
            with self.subTest(state=state):
                automation_id = f"new_{str(state).lower()}"
                proposed = {**copy.deepcopy(CURRENT), "initial_state": state}
                created = await self.service.create_plan(
                    title="Create", description="Create with initial_state",
                    operation="create_automation",
                    automation_id=automation_id, proposed_config=proposed,
                )
                self.assertEqual(created["status"], "awaiting_approval")
                await self.externally_approve(
                    created["plan_id"], created["plan_hash"]
                )
                writes = self.gateway.write_calls
                result = await self.service.apply(created["plan_id"])
                self.assertEqual(result["status"], "applied")
                self.assertEqual(self.gateway.write_calls, writes + 1)
                stored = self.gateway.configs[automation_id]
                self.assertIs(stored["initial_state"], state)
                plan = self.repository.get(created["plan_id"])
                self.assertEqual(plan.verification.status, "passed")
                self.assertEqual(plan.verification.mismatch_fields, [])

    async def test_update_setting_initial_state_is_medium_risk_and_verified(self):
        proposed = {**copy.deepcopy(CURRENT), "initial_state": False}
        created = await self.service.create_plan(
            title="Disable at start", description="initial_state false",
            operation="update_automation", automation_id="porch",
            proposed_config=proposed,
        )
        changed = created["dry_run_results"]["changed_fields"]
        self.assertEqual(
            [(item["field"], item["change_type"]) for item in changed],
            [("other:initial_state", "added")],
        )
        self.assertEqual(created["risk"]["level"], "medium")
        self.assertIn(
            "Behavior-impacting initial_state change", created["risk"]["reasons"]
        )
        await self.externally_approve(created["plan_id"], created["plan_hash"])
        result = await self.service.apply(created["plan_id"])
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.gateway.write_calls, 1)
        self.assertIs(self.gateway.configs["porch"]["initial_state"], False)
        plan = self.repository.get(created["plan_id"])
        self.assertEqual(plan.verification.status, "passed")

    async def test_readback_that_drops_initial_state_fails_verification(self):
        proposed = {**copy.deepcopy(CURRENT), "initial_state": False}
        created = await self.service.create_plan(
            title="Disable at start", description="initial_state false",
            operation="update_automation", automation_id="porch",
            proposed_config=proposed,
        )
        await self.externally_approve(created["plan_id"], created["plan_hash"])
        original_write = self.gateway.write

        async def dropping_write(automation_id, config):
            config = {k: v for k, v in config.items() if k != "initial_state"}
            return await original_write(automation_id, config)

        with patch.object(self.gateway, "write", dropping_write):
            try:
                await self.service.apply(created["plan_id"])
            except GovernanceError:
                pass
        plan = self.repository.get(created["plan_id"])
        self.assertNotEqual(plan.verification.status, "passed")
        self.assertIn("other:initial_state", plan.verification.mismatch_fields)
        self.assertNotEqual(plan.status, PlanStatus.APPLIED)

    async def test_non_boolean_initial_state_is_refused_before_io(self):
        for state in NON_BOOLEAN_VALUES:
            for operation, automation_id in (
                ("create_automation", "new_invalid"),
                ("update_automation", "porch"),
            ):
                with self.subTest(state=state, operation=operation):
                    reads = self.counting.read_calls
                    with self.assertRaises(GovernanceError) as raised:
                        await self.service.create_plan(
                            title="Invalid", description="Invalid initial_state",
                            operation=operation, automation_id=automation_id,
                            proposed_config={**copy.deepcopy(CURRENT), "initial_state": state},
                        )
                    self.assertEqual(
                        raised.exception.code, ErrorCode.AUTOMATION_VALIDATION_FAILED
                    )
                    self.assertIn(
                        "automation initial_state must be a boolean",
                        raised.exception.details["validation_errors"],
                    )
                    self.assertEqual(self.counting.read_calls, reads)
        self.assertEqual(self.gateway.write_calls, 0)
        self.assertTrue(
            all(
                plan.status is PlanStatus.VALIDATION_FAILED
                for plan in self.repository.list()
            )
        )


class HistoricalInitialStateRecordTests(unittest.TestCase):
    """A shipped validation-failed record refused for initial_state still matches.

    The shipped writer's fixture is varied in memory only, as the existing
    terminal-history tests do; its error text is the shipped writer's.
    """

    def _variant(self, name, state):
        data = terminal_fixture(name)
        data["proposed_config"]["initial_state"] = state
        data["normalized_proposed_config"] = normalize_automation(
            data["proposed_config"]
        )
        data["proposed_config_hash"] = stable_hash(data["normalized_proposed_config"])
        errors = list(data["validation_results"]["errors"])
        errors.insert(
            1, "automation config contains unsupported fields: initial_state"
        )
        data["validation_results"] = {"valid": False, "errors": errors}
        historical_tests.HistoricalPolicyProjectionTests._rebind_snapshot_hashes(data)
        return data

    def test_record_refused_for_initial_state_still_projects(self):
        for name in ("invalid_automation_f2_v1", "invalid_automation_f2_v2"):
            for state in (True, "on"):
                with self.subTest(name=name, state=state):
                    data = self._variant(name, state)
                    plan = ChangePlan.from_dict(json.loads(json.dumps(data)))
                    self.assertIsNotNone(terminal_nonexecution_projection_match(plan))

    def test_record_with_todays_error_text_is_not_history(self):
        for name in ("invalid_automation_f2_v1", "invalid_automation_f2_v2"):
            with self.subTest(name=name):
                data = self._variant(name, "on")
                errors = [
                    error
                    for error in data["validation_results"]["errors"]
                    if "unsupported fields" not in error
                ]
                errors.append("automation initial_state must be a boolean")
                data["validation_results"] = {"valid": False, "errors": errors}
                historical_tests.HistoricalPolicyProjectionTests._rebind_snapshot_hashes(data)
                plan = ChangePlan.from_dict(json.loads(json.dumps(data)))
                self.assertIsNone(terminal_nonexecution_projection_match(plan))


if __name__ == "__main__":
    unittest.main()
