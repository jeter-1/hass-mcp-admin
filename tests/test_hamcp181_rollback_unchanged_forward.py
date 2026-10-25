"""HAMCP-181 PR2 (design section 4.4, OD-11): no rollback of an unchanged forward.

A legacy ``update_automation`` plan applied through F3 builds a rollback from
its stored ``current_config``.  When every child of the forward task is
proven unchanged -- (b) proven not sent, or (r-proven) sent and rejected
without mutation -- there is nothing to reverse, so rollback planning refuses
with ``rollback_not_available`` and reason ``forward_unchanged``.  The
refusal is the only change: an (r-unproven) or uncertain (c) forward keeps
today's admission, a sent-and-verified forward still creates a rollback plan,
and the contract-v2 branch still reverses only its verified children.

Every transport is a synthetic fake that enforces the Core-authority window.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from tests.test_dev14_configuration_plans import (  # noqa: E402
    PROPOSED_AUTOMATION,
    PROPOSED_SCRIPT,
)
# The chain base is imported through its module so the loader does not
# collect (and rerun) its own tests from this module.
from tests import test_hamcp181_proven_no_call_producers as producers  # noqa: E402
from tests.test_hamcp181_proven_no_call_producers import (  # noqa: E402
    _raise_callback_failure,
    at_intent,
)
from ha_mcp_engineering.errors import (  # noqa: E402
    ErrorCode,
    GovernanceError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
)
from ha_mcp_engineering.f3_runtime import dispatch_facts as df  # noqa: E402


AUTOMATION_ID = "apply_hvac_comfort"
AUTOMATION_POST = ("POST", f"/config/automation/config/{AUTOMATION_ID}")


class LegacyAutomationRollbackTests(producers.ConfigurationChainTests):
    """The F3 legacy branch of ``_plan_fresh_rollback`` (contract version 1)."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        async def get(automation_id):
            # The legacy planner reads the current automation by identifier.
            return copy.deepcopy(self.gateway.configs.get(("automation", automation_id)))

        self.gateway.get = get

    async def create_legacy_plan(self):
        created = await self.service.create_plan(
            title="Legacy automation update",
            description="HAMCP-181 OD-11 fixture",
            operation="update_automation",
            automation_id=AUTOMATION_ID,
            proposed_config=copy.deepcopy(PROPOSED_AUTOMATION),
        )
        plan = self.service.repository.get(created["plan_id"])
        self.assertEqual(plan.contract_version, 1)
        self.assertIsNotNone(plan.current_config)
        return created

    async def forward(self):
        created = await self.create_legacy_plan()
        result, view, _ = await self.applied(created)
        return created, result, view

    def changed(self, result):
        task = self.service.task_repository.get(result["task_id"])
        return self.runtime.dispatch_facts_for_task(task).changed_by_engineering

    def writes(self):
        return [call for call in self.gateway.calls if call[0] == "write"]

    async def assert_refused_unchanged(self, created):
        writes = len(self.writes())
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"], created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertEqual(
            raised.exception.details,
            {
                "resource_id": created["plan_id"],
                "reason": "forward_unchanged",
                "provider_dispatch_occurred": False,
            },
        )
        self.assertEqual(len(self.writes()), writes)

    async def assert_rollback_created(self, created):
        writes = len(self.writes())
        result = await self.service.rollback_change(created["plan_id"], created["plan_hash"])
        self.assertEqual(result["status"], "rollback_plan_created")
        self.assertTrue(result["approval_required"])
        rollback = self.service.repository.get(result["rollback_plan_id"])
        self.assertEqual(
            [operation.operation_id for operation in rollback.operations],
            ["rollback_legacy_automation"],
        )
        # Planning never writes; the rollback plan still needs its own approval.
        self.assertEqual(len(self.writes()), writes)
        return result

    async def test_proven_not_sent_forward_refuses_rollback(self):
        with at_intent(_raise_callback_failure):
            created, result, view = await self.forward()
        self.assertEqual(result["task_state"], "failed_post_dispatch")
        self.assertEqual(view["terminal_outcome"], df.NOT_SENT_OUTCOME)
        self.assertEqual(self.writes(), [])
        await self.assert_refused_unchanged(created)

    async def test_proven_rejected_forward_refuses_rollback(self):
        self.routed.add(("automation", AUTOMATION_ID))
        self.rest.responses[AUTOMATION_POST] = HomeAssistantApiError(
            details={"status": 400, "provider_response_received": True}
        )
        created, result, view = await self.forward()
        self.assertEqual(view["dispatch_phase"], df.PHASE_SENT_REJECTED_NO_MUTATION)
        self.assertIs(self.changed(result), False)
        await self.assert_refused_unchanged(created)

    async def test_unproven_forward_keeps_rollback_admission(self):
        self.routed.add(("automation", AUTOMATION_ID))
        self.rest.responses[AUTOMATION_POST] = HomeAssistantTimeoutError()
        created, result, view = await self.forward()
        self.assertEqual(view["dispatch_phase"], df.PHASE_SEND_UNCERTAIN)
        self.assertIsNone(self.changed(result))
        await self.assert_rollback_created(created)

    async def test_sent_and_verified_forward_still_creates_a_rollback_plan(self):
        created, result, view = await self.forward()
        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(view["dispatch_phase"], df.PHASE_SENT_RESPONSE_RECEIVED)
        self.assertIs(self.changed(result), True)
        # The chain harness logs a pass-through write twice; the child
        # record is the single-dispatch authority.
        declarations = self.runtime.children.declarations_for_task(result["task_id"])
        self.assertEqual(
            [self.runtime.children.get(item["child_id"]).dispatch_count for item in declarations],
            [1],
        )
        await self.assert_rollback_created(created)

    async def test_refusal_is_repeatable_and_never_redispatches(self):
        with at_intent(_raise_callback_failure):
            created, result, _ = await self.forward()
        await self.assert_refused_unchanged(created)
        await self.assert_refused_unchanged(created)
        # A second apply of the forward plan reuses its terminal authority and
        # refuses: the intent is kept and the operation is never redispatched.
        with self.assertRaises(GovernanceError):
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(self.writes(), [])
        task = self.service.task_repository.get(result["task_id"])
        self.assertEqual(task.state.value, "failed_post_dispatch")


class ContractV2RollbackUnchangedTests(producers.ConfigurationChainTests):
    """The contract-v2 branch is unchanged: verified children only."""

    async def test_mixed_plan_reverses_only_the_verified_child(self):
        created = await self.service.create_configuration_plan(
            title="Mixed verified and not-sent fixture",
            description="Reverse only the verified update",
            operations=[
                {
                    "operation_id": "verified_automation_update",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": AUTOMATION_ID,
                    "depends_on": [],
                    "proposed_config": copy.deepcopy(PROPOSED_AUTOMATION),
                },
                {
                    "operation_id": "rejected_script_update",
                    "resource_type": "script",
                    "action": "update",
                    "target_id": "set_hvac_comfort",
                    "depends_on": ["verified_automation_update"],
                    "proposed_config": copy.deepcopy(PROPOSED_SCRIPT),
                },
            ],
        )
        self.routed.add(("script", "set_hvac_comfort"))
        self.rest.responses[("POST", "/config/script/config/set_hvac_comfort")] = (
            HomeAssistantApiError(details={"status": 400, "provider_response_received": True})
        )
        await self.applied(created)
        rollback = await self.service.rollback_change(created["plan_id"], created["plan_hash"])
        self.assertEqual(rollback["status"], "rollback_plan_created")
        self.assertEqual(rollback["operations_excluded"], ["rejected_script_update"])

    async def test_all_unchanged_contract_v2_plan_keeps_its_existing_refusal(self):
        with at_intent(_raise_callback_failure):
            created = await self.create_automation_plan()
            _, view, _ = await self.applied(created)
        self.assertEqual(view["terminal_outcome"], df.NOT_SENT_OUTCOME)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"], created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertNotEqual(
            (raised.exception.details or {}).get("reason"), "forward_unchanged"
        )


# Inherited cases run in their own modules.
for _name in [name for name in dir(producers.ConfigurationChainTests) if name.startswith("test_")]:
    setattr(LegacyAutomationRollbackTests, _name, None)
    setattr(ContractV2RollbackUnchangedTests, _name, None)


if __name__ == "__main__":
    unittest.main()
