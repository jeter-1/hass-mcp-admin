"""HAMCP-213 PR 1 batch 4 (review H5): a called script the same plan changes.

Effect analysis summarizes a script the plan itself changes from the plan's
own bodies: its proposed (post-plan) body, plus its current body because a
non-atomic plan can stop with the caller written and the callee not. The
acknowledgement requirements derive from that.

At dispatch, before every caller write (configuration service, F3 first
apply, resume and recovery), the live callee is read and must match its
exact expected state at that point in the plan's order: the pre-plan
fingerprint while the plan has not written it, the plan's proposed
fingerprint once it has. Any other state refuses the caller as stale with
no write.

Every reproduction runs through the configuration service and the F3
runtime. All fixtures are synthetic and anonymized.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.f3_runtime import runtime as f3_runtime_module  # noqa: E402
from ha_mcp_engineering.governance.physical_effects import (  # noqa: E402
    compare_operation,
    plan_acknowledgement_binding,
)
from tests import test_dev14_configuration_plans as dev14  # noqa: E402
from tests import test_f3_runtime_integration as f3  # noqa: E402
from tests import test_hamcp213_called_script_effects as cs  # noqa: E402
from tests import test_hamcp213_review_corrections as rc  # noqa: E402

CALLER = "review_caller"
CALLEE = cs.GARAGE_SCRIPT
UNLOCK_OBJECT = rc.UNLOCK["target"]["entity_id"]
UNLOCK_CALLEE = rc.script(rc.UNLOCK, alias="Review entry unlock")


def _described(config, text="Updated display text"):
    value = copy.deepcopy(config)
    value["description"] = text
    return value


class _SimulatedCrash(Exception):
    """A process stop between two F3 children (test instrumentation)."""


class _InPlanCalleeChecks(rc._ReviewCorrectionChecks):
    """Shared reproductions; each concrete class selects one dispatch path."""

    # Only the helpers of the shared checks are reused here.
    test_h1_unresolvable_effects_require_the_acknowledgement = None
    test_h2_reference_steps_before_an_effect_are_compared_in_full = None
    test_h3_unlock_and_disarm_always_require_the_acknowledgement = None
    test_controls_keep_plan_approval_only = None
    test_garage_retained_effect_exception_is_unchanged = None
    test_pr001_verdicts_are_unchanged_by_the_corrections = None

    def seed(self, callee=None):
        self.gateway.configs[("automation", CALLER)] = rc.automation(
            cs.run(), conditions=(rc.GUARD,)
        )
        self.gateway.configs[("script", CALLEE)] = copy.deepcopy(
            callee if callee is not None else cs.LIGHT_SCRIPT
        )

    async def plan(self, *, callee_first, callee_after=None):
        caller_op = {
            "operation_id": "caller",
            "resource_type": "automation",
            "action": "update",
            "target_id": CALLER,
            "depends_on": [],
            "proposed_config": rc.automation(cs.run()),
        }
        callee_op = {
            "operation_id": "callee",
            "resource_type": "script",
            "action": "update",
            "target_id": CALLEE,
            "depends_on": [],
            "proposed_config": copy.deepcopy(
                callee_after
                if callee_after is not None
                else _described(cs.LIGHT_SCRIPT)
            ),
        }
        if callee_first:
            caller_op["depends_on"] = ["callee"]
            operations = [callee_op, caller_op]
        else:
            callee_op["depends_on"] = ["caller"]
            operations = [caller_op, callee_op]
        return await self.service.create_configuration_plan(
            title="In-plan callee review",
            description="HAMCP-213 batch 4 synthetic reproduction",
            operations=operations,
        )

    def stored(self, created):
        return {
            item.operation_id: item
            for item in self.repository.get(created["plan_id"]).operations
        }

    def guard_removed(self):
        return not self.gateway.configs[("automation", CALLER)].get("condition")

    def callee_body(self):
        value = copy.deepcopy(self.gateway.configs[("script", CALLEE)])
        value.pop("id", None)
        return value

    async def apply_outcome(self, created):
        """Apply; return ``(refusal error code or None, result or None)``."""

        try:
            result = await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )
        except GovernanceError as exc:
            return exc.code, None
        return None, result

    def assert_caller_not_written(self):
        self.assertNotIn(
            ("write", "update", "automation", CALLER),
            [tuple(item[:4]) for item in self.writes()],
        )
        self.assertFalse(self.guard_removed())

    def drift_before_caller_dispatch(self, drifted):
        """Change the callee after it is written, before the caller's check."""

        raise NotImplementedError

    # -- review reproduction --------------------------------------------

    async def test_reviewer_reproduction_refuses_before_the_caller_write(self):
        # Caller first (removes the guard), callee second (description only,
        # light-only bodies): only plan approval is required. The upstream
        # callee becomes an unlock after approval.
        self.seed()
        created = await self.plan(callee_first=False)
        self.assertEqual(
            created["policy_decision"]["required_acknowledgements"],
            ["plan_approval"],
        )
        await self.approve_plan_only(created)
        self.gateway.configs[("script", CALLEE)] = copy.deepcopy(UNLOCK_CALLEE)
        refusal, result = await self.apply_outcome(created)
        self.assertEqual(self.writes(), [])
        self.assert_caller_not_written()
        self.assert_stale_refusal(refusal, result)
        self.assertEqual(self.callee_body(), UNLOCK_CALLEE)

    async def test_drift_before_any_write_refuses_in_callee_first_order(self):
        self.seed()
        created = await self.plan(callee_first=True)
        await self.approve_plan_only(created)
        self.gateway.configs[("script", CALLEE)] = copy.deepcopy(UNLOCK_CALLEE)
        refusal, result = await self.apply_outcome(created)
        self.assertEqual(self.writes(), [])
        self.assert_caller_not_written()
        self.assert_stale_refusal(refusal, result)

    async def test_drift_after_the_callee_write_refuses_the_caller(self):
        # Callee first: the plan writes its proposed callee, which is then
        # changed to an unlock before the caller is written.
        self.seed()
        created = await self.plan(callee_first=True)
        await self.approve_plan_only(created)
        self.drift_before_caller_dispatch(UNLOCK_CALLEE)
        await self.apply_outcome(created)
        self.assertEqual(
            [tuple(item[:4]) for item in self.writes()],
            [("write", "update", "script", CALLEE)],
        )
        self.assert_caller_not_written()
        self.assertEqual(self.callee_body(), UNLOCK_CALLEE)

    async def test_callee_reverted_to_its_pre_plan_body_after_its_write_refuses(self):
        # After the callee's own write the only valid state is the proposed
        # one; its pre-plan body is stale at that point too.
        self.seed()
        created = await self.plan(callee_first=True)
        await self.approve_plan_only(created)
        self.drift_before_caller_dispatch(cs.LIGHT_SCRIPT)
        await self.apply_outcome(created)
        self.assertEqual(len(self.writes()), 1)
        self.assert_caller_not_written()

    async def test_unchanged_in_plan_callee_applies_in_both_orders(self):
        for callee_first in (False, True):
            with self.subTest(callee_first=callee_first):
                self.gateway.calls.clear()
                self.seed()
                created = await self.plan(callee_first=callee_first)
                self.assertEqual(
                    created["policy_decision"]["required_acknowledgements"],
                    ["plan_approval"],
                )
                await self.approve_plan_only(created)
                refusal, _result = await self.apply_outcome(created)
                self.assertIsNone(refusal)
                self.assertEqual(len(self.writes()), 2)
                self.assertTrue(self.guard_removed())
                self.assertEqual(self.callee_body(), _described(cs.LIGHT_SCRIPT))

    # -- post-plan analysis ---------------------------------------------

    async def test_callee_changed_to_unlock_with_caller_guard_removal_requires_acknowledgement(self):
        for callee_first in (True, False):
            with self.subTest(callee_first=callee_first):
                self.gateway.calls.clear()
                self.seed()
                created = await self.plan(
                    callee_first=callee_first, callee_after=UNLOCK_CALLEE
                )
                decision = created["policy_decision"]
                self.assertEqual(decision["policy_class"], "elevated_admin")
                self.assertEqual(
                    decision["required_acknowledgements"],
                    ["plan_approval", rc.SAFETY],
                )
                stored = self.stored(created)
                caller = compare_operation(stored["caller"])
                # The caller is analyzed against the post-plan callee: its own
                # rows name the unlock the plan puts behind the removed guard.
                self.assertTrue(caller.unlock_or_disarm)
                self.assertTrue(
                    any(
                        row["object_id"] == UNLOCK_OBJECT
                        and row["acknowledgement_required"]
                        for row in caller.projection["rows"]
                    ),
                    caller.projection["rows"],
                )
                binding = plan_acknowledgement_binding(
                    self.repository.get(created["plan_id"]).operations
                )
                self.assertIn(
                    {"operation_id": "caller", "object_id": UNLOCK_OBJECT},
                    binding["objects"],
                )
                # Plan approval alone never writes.
                plan_only = await self.approve_plan_only(created)
                self.assertEqual(plan_only["status"], "approval_pending")
                with self.assertRaises(GovernanceError):
                    await self.service.apply(
                        created["plan_id"], created["plan_hash"]
                    )
                self.assertEqual(self.writes(), [])
                # The exact acknowledgement applies both operations once,
                # each caller check seeing the callee state for its position.
                acknowledged, safety = await self.acknowledge(created, plan_only)
                self.assertEqual(acknowledged["status"], "approved")
                self.assertTrue(
                    any(row["operation_id"] == "caller" for row in safety["objects"])
                )
                refusal, _result = await self.apply_outcome(created)
                self.assertIsNone(refusal)
                self.assertEqual(len(self.writes()), 2)
                self.assertTrue(self.guard_removed())
                self.assertEqual(self.callee_body(), UNLOCK_CALLEE)

    async def test_caller_summary_uses_the_proposed_callee_body(self):
        self.seed()
        created = await self.plan(callee_first=True, callee_after=UNLOCK_CALLEE)
        caller = self.stored(created)["caller"]
        configs = caller.called_scripts["scripts"][CALLEE]["configs"]
        self.assertIn(
            self.stored(created)["callee"].normalized_proposed_config, configs
        )


class ServicePathInPlanCalleeTests(_InPlanCalleeChecks, dev14.ConfigurationPlanTestCase):
    """The reproductions through the configuration service."""

    def assert_stale_refusal(self, refusal, result):
        self.assertEqual(refusal, ErrorCode.STALE_TARGET_STATE, result)

    def drift_before_caller_dispatch(self, drifted):
        # The service re-reads the caller's own target immediately before the
        # caller's called-script check; the drift lands between the callee's
        # verified write and that check.
        original = self.gateway.read
        written = []

        async def read(resource_type, resource_id):
            if (
                (resource_type, resource_id) == ("automation", CALLER)
                and ("write", "update", "script", CALLEE)
                in [tuple(item[:4]) for item in self.gateway.calls]
                and not written
            ):
                written.append(True)
                self.gateway.configs[("script", CALLEE)] = copy.deepcopy(drifted)
            return await original(resource_type, resource_id)

        self.gateway.read = read

    async def test_mid_plan_refusal_records_the_stale_callee(self):
        self.seed()
        created = await self.plan(callee_first=True)
        await self.approve_plan_only(created)
        self.drift_before_caller_dispatch(UNLOCK_CALLEE)
        await self.apply_outcome(created)
        caller = self.stored(created)["caller"]
        self.assertEqual(caller.execution_receipt["reason"], "stale_called_script")
        self.assertFalse(caller.execution_receipt["write_attempted"])


class F3PathInPlanCalleeTests(_InPlanCalleeChecks, dev14.ConfigurationPlanTestCase):
    """The same reproductions through the F3 runtime, resume and recovery."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.runtime = f3.F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=f3._ExactFakeConfigurationGateway(self.gateway),
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=f3._provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    def assert_stale_refusal(self, refusal, result):
        self.assertIsNone(refusal)
        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        self.assertFalse(result["provider_dispatch_occurred"])

    def drift_before_caller_dispatch(self, drifted):
        # Inject the drift immediately before the caller child's freshness
        # preflight; the check itself is unchanged.
        adapter_class = f3_runtime_module._CalledScriptFreshnessAdapter
        original = adapter_class.preflight
        gateway = self.gateway

        async def preflight(adapter, prepared, *, acquired_locks):
            if getattr(prepared, "operation_id", None) == "caller":
                gateway.configs[("script", CALLEE)] = copy.deepcopy(drifted)
            return await original(adapter, prepared, acquired_locks=acquired_locks)

        adapter_class.preflight = preflight
        self.addCleanup(setattr, adapter_class, "preflight", original)

    def children(self, created):
        task = self.service.task_repository.get_for_plan(created["plan_id"])
        return [
            self.runtime.children.get(item["child_id"])
            for item in self.runtime.children.declarations_for_task(task.task_id)
        ]

    def crash_after(self, operation_id):
        """Stop the runtime once the named child returns."""

        original = self.runtime._execute_child

        async def execute_child(plan, task, declaration, *args, **kwargs):
            result = await original(plan, task, declaration, *args, **kwargs)
            if declaration["operation_id"] == operation_id:
                self.runtime._execute_child = original
                raise _SimulatedCrash(operation_id)
            return result

        self.runtime._execute_child = execute_child

    async def _resume_or_recover(self, created, mode):
        if mode == "resume":
            return await self.apply_outcome(created)
        await self.runtime.recover_once("startup")
        return None, None

    async def test_resume_and_recovery_before_the_first_child_refuse(self):
        # The reviewer's resume/recovery reproduction: the approved task is
        # initialized, then the in-plan callee drifts.
        for mode in ("resume", "recovery"):
            with self.subTest(mode=mode):
                self.gateway.calls.clear()
                self.seed()
                created = await self.plan(callee_first=False)
                await self.approve_plan_only(created)
                plan = self.service._load(created["plan_id"])
                task, _prepared, _requests = await self.runtime._initialize(
                    plan, created["plan_hash"]
                )
                self.runtime._enter_public_preflight(task)
                self.gateway.configs[("script", CALLEE)] = copy.deepcopy(
                    UNLOCK_CALLEE
                )
                await self._resume_or_recover(created, mode)
                self.assertEqual(self.writes(), [])
                self.assert_caller_not_written()
                records = [
                    self.runtime.children.get(item["child_id"])
                    for item in self.runtime.children.declarations_for_task(
                        task.task_id
                    )
                ]
                self.assertTrue(all(
                    record is None or record.dispatch_count == 0
                    for record in records
                ))

    async def test_resume_and_recovery_mid_plan_refuse_a_drifted_written_callee(self):
        for mode in ("resume", "recovery"):
            with self.subTest(mode=mode):
                self.gateway.calls.clear()
                self.seed()
                created = await self.plan(callee_first=True)
                await self.approve_plan_only(created)
                self.crash_after("callee")
                with self.assertRaises(_SimulatedCrash):
                    await self.service.apply(
                        created["plan_id"], created["plan_hash"]
                    )
                self.assertEqual(len(self.writes()), 1)
                self.gateway.configs[("script", CALLEE)] = copy.deepcopy(
                    UNLOCK_CALLEE
                )
                await self._resume_or_recover(created, mode)
                self.assertEqual(len(self.writes()), 1)
                self.assert_caller_not_written()
                callee_record, caller_record = self.children(created)
                self.assertEqual(callee_record.normalized_outcome, "succeeded_verified")
                self.assertTrue(
                    caller_record is None or caller_record.dispatch_count == 0
                )

    async def test_resume_and_recovery_mid_plan_apply_an_unchanged_written_callee(self):
        for mode in ("resume", "recovery"):
            with self.subTest(mode=mode):
                self.gateway.calls.clear()
                self.seed()
                created = await self.plan(callee_first=True)
                await self.approve_plan_only(created)
                self.crash_after("callee")
                with self.assertRaises(_SimulatedCrash):
                    await self.service.apply(
                        created["plan_id"], created["plan_hash"]
                    )
                await self._resume_or_recover(created, mode)
                self.assertEqual(len(self.writes()), 2)
                self.assertTrue(self.guard_removed())
                _callee_record, caller_record = self.children(created)
                self.assertEqual(caller_record.normalized_outcome, "succeeded_verified")
                self.assertEqual(caller_record.dispatch_count, 1)

    async def test_resume_mid_plan_in_caller_first_order_refuses_a_drifted_callee(self):
        # Caller written, then a stop: the callee's own operation refuses its
        # drifted target; the written caller ran against a verified callee.
        self.seed()
        created = await self.plan(callee_first=False)
        await self.approve_plan_only(created)
        self.crash_after("caller")
        with self.assertRaises(_SimulatedCrash):
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(len(self.writes()), 1)
        self.gateway.configs[("script", CALLEE)] = copy.deepcopy(UNLOCK_CALLEE)
        await self.apply_outcome(created)
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.callee_body(), UNLOCK_CALLEE)


if __name__ == "__main__":
    unittest.main()
