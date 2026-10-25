"""HAMCP-214: plan verification and rollback availability follow the F3 task.

When a contract-v2 task reaches a terminal state, ``_project_locked`` copies
each ``succeeded_verified`` child's recorded verification onto its operation
(``applied_verified``, the child's ``resulting_state_fingerprint`` as the
operation's ``post_apply_fingerprint``, desired == actual), and shows the plan
``verified`` only when every operation was.  A plan-level fingerprint exists
only for a one-operation plan; a multi-operation plan keeps its fingerprints
per operation.

``_project_rollback_availability`` then replaces only the creation placeholder
(``unavailable_for_configuration_plan`` with no request) once the task is
final, by family:

* label plans are never reversible (``ROLLBACK_NOT_SUPPORTED``);
* availability/exposure: any child dispatched once and verified;
* delete: such a child that is also recreate eligible;
* entity metadata: one operation, a verified task, dispatched once;
* configuration create/update: a verified update with a stored pre-image and
  a resulting fingerprint; an all-create plan says ``unavailable_for_create``.

``available`` is display only: ``rollback_change`` still re-derives admission.
A later rollback status or an existing rollback request is never rewritten.
Each case is read through ``get_plan``, the apply response's nested plan and
``get_execution_task``, which must agree.

Every transport is a synthetic fake; nothing is live.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

# TestCase bases are imported through their modules so the loader does not
# collect (and rerun) the base classes' own tests from this module.
from tests import test_f3_runtime_integration as f3_integration  # noqa: E402
from tests import test_hamcp069a_entity_metadata_execution as metadata_tests  # noqa: E402
from tests import test_hamcp069b_entity_availability_exposure_execution as exposure_tests  # noqa: E402
from tests import test_hamcp155_configuration_delete_execution as delete_tests  # noqa: E402
from tests import test_hamcp165_label_registry as label_tests  # noqa: E402
from tests import test_hamcp181_proven_no_call_producers as producers  # noqa: E402
from tests.hamcp069a_entity_fixtures import UPDATE as METADATA_UPDATE  # noqa: E402
from tests.hamcp155_fixtures import automation_config  # noqa: E402
from tests.test_dev14_configuration_plans import (  # noqa: E402
    PROPOSED_AUTOMATION,
    PROPOSED_SCRIPT,
    hvac_operations,
)
from ha_mcp_engineering.errors import (  # noqa: E402
    AuthorizationError,
    ErrorCode,
    GovernanceError,
    HomeAssistantApiError,
)
from ha_mcp_engineering.f3_runtime.runtime import _recreate_eligible  # noqa: E402
from ha_mcp_engineering.governance import label_registry_planning  # noqa: E402


PLACEHOLDER = "unavailable_for_configuration_plan"
PENDING_CHECK = "available_pending_current_state_check"
NO_VERIFIED = "unavailable_no_verified_dispatched_change"
FOR_CREATE = "unavailable_for_create"
AUTOMATION = ("automation", "apply_hvac_comfort")
SCRIPT = ("script", "set_hvac_comfort")


def unwrap(plan):
    return plan.get("plan", plan)


class _Projection:
    """Read one applied plan on every public surface and check agreement."""

    def surfaces(self, created, result):
        plan = unwrap(self.service.get_plan(created["plan_id"]))
        view = self.service.get_execution_task(result["task_id"])
        nested = result.get("plan")
        if nested is not None:
            nested = unwrap(nested)
            for key in ("verification", "rollback", "post_apply_fingerprint"):
                self.assertEqual(nested.get(key), plan.get(key), key)
        self.assertEqual(view["state"], result["task_state"])
        # The plan shows verified exactly when the task summary does.
        self.assertEqual(
            plan["verification"]["status"] == "verified",
            view["verification_summary"]["status"] == "verified",
            (plan["verification"], view["verification_summary"]),
        )
        return plan, view

    def records(self, task_id):
        return {
            item["operation_id"]: self.runtime.children.get(item["child_id"])
            for item in self.runtime.children.declarations_for_task(task_id)
        }

    def assert_rollback(self, plan, *, available, status):
        self.assertIs(plan["rollback"]["available"], available, plan["rollback"])
        self.assertEqual(plan["rollback"]["status"], status, plan["rollback"])
        self.assertIsNone(plan["rollback"]["request_id"])

    def assert_operation_verified(self, operation, record):
        fingerprint = record.evidence["resulting_state_fingerprint"]
        self.assertIsNotNone(fingerprint)
        self.assertEqual(operation["execution_status"], "applied_verified")
        self.assertEqual(operation["post_apply_fingerprint"], fingerprint)
        verification = operation["verification"]
        self.assertEqual(verification["status"], "verified")
        self.assertEqual(verification["actual_fingerprint"], fingerprint)
        self.assertEqual(verification["desired_fingerprint"], fingerprint)

    def assert_plan_not_verified(self, plan):
        self.assertNotEqual(plan["verification"]["status"], "verified", plan["verification"])
        self.assertIsNone(plan["post_apply_fingerprint"])


# -- configuration create/update -----------------------------------------------


class ConfigurationProjectionTests(_Projection, f3_integration.F3ConfigurationActivationTests):
    async def applied(self, created):
        await self.approve(created)
        return await self.service.apply(created["plan_id"], created["plan_hash"])

    async def create_operations(self, operations, title="HAMCP-214 fixture"):
        return await self.service.create_configuration_plan(
            title=title, description="HAMCP-214 projection fixture", operations=operations,
        )

    def writes(self):
        return [call for call in self.gateway.calls if call[0] == "write"]

    async def test_single_update_projects_plan_verification_and_rollback(self):
        created = await self.create_automation_plan()
        stored = self.service.repository.get(created["plan_id"])
        self.assertEqual(stored.rollback.status, PLACEHOLDER)
        result = await self.applied(created)
        self.assertEqual(result["task_state"], "succeeded_verified")
        plan, _view = self.surfaces(created, result)
        [operation] = plan["operations"]
        record = self.records(result["task_id"])["update_comfort_automation"]
        self.assert_operation_verified(operation, record)
        # One operation: the plan carries its fingerprint, desired == actual.
        verification = plan["verification"]
        self.assertEqual(verification["status"], "verified")
        self.assertIsNotNone(verification["checked_at"])
        self.assertEqual(plan["post_apply_fingerprint"], operation["post_apply_fingerprint"])
        self.assertEqual(verification["actual_fingerprint"], plan["post_apply_fingerprint"])
        self.assertEqual(verification["desired_fingerprint"], verification["actual_fingerprint"])
        self.assert_rollback(plan, available=True, status=PENDING_CHECK)
        # Display only: rollback_change still plans (and never writes).
        writes = len(self.writes())
        rollback = await self.service.rollback_change(created["plan_id"], created["plan_hash"])
        self.assertEqual(rollback["status"], "rollback_plan_created")
        self.assertEqual(len(self.writes()), writes)

    async def test_multi_operation_plan_keeps_fingerprints_per_operation(self):
        created = await self.create_hvac_plan()
        result = await self.applied(created)
        self.assertEqual(result["task_state"], "succeeded_verified")
        plan, _view = self.surfaces(created, result)
        records = self.records(result["task_id"])
        self.assertEqual(len(plan["operations"]), 3)
        for operation in plan["operations"]:
            with self.subTest(operation=operation["operation_id"]):
                self.assert_operation_verified(operation, records[operation["operation_id"]])
        fingerprints = [operation["post_apply_fingerprint"] for operation in plan["operations"]]
        self.assertEqual(len(set(fingerprints)), 3)
        self.assertEqual(plan["verification"]["status"], "verified")
        self.assertIsNone(plan["post_apply_fingerprint"])
        self.assertIsNone(plan["verification"]["desired_fingerprint"])
        self.assertIsNone(plan["verification"]["actual_fingerprint"])
        # The checked_at is the latest operation verification.
        self.assertEqual(
            plan["verification"]["checked_at"],
            max(operation["verification"]["checked_at"] for operation in plan["operations"]),
        )
        self.assert_rollback(plan, available=True, status=PENDING_CHECK)

    async def test_all_create_plan_is_unavailable_for_create(self):
        created = await self.create_operations([hvac_operations()[0]])
        result = await self.applied(created)
        self.assertEqual(result["task_state"], "succeeded_verified")
        plan, _view = self.surfaces(created, result)
        self.assertEqual(plan["verification"]["status"], "verified")
        self.assert_rollback(plan, available=False, status=FOR_CREATE)

    async def test_failed_all_create_plan_is_still_unavailable_for_create(self):
        created = await self.create_operations([hvac_operations()[0]])
        self.gateway.fail_write_target = ("input_number", "input_number.hvac_target")
        result = await self.applied(created)
        self.assertNotEqual(result["task_state"], "succeeded_verified")
        plan, _view = self.surfaces(created, result)
        self.assert_plan_not_verified(plan)
        self.assert_rollback(plan, available=False, status=FOR_CREATE)

    async def test_first_operation_failure_is_not_verified_and_not_available(self):
        created = await self.create_automation_plan()
        self.gateway.fail_write_target = AUTOMATION
        result = await self.applied(created)
        self.assertIn(result["task_state"], {"failed_post_dispatch", "manual_review_required"})
        plan, _view = self.surfaces(created, result)
        self.assert_plan_not_verified(plan)
        [operation] = plan["operations"]
        self.assertNotEqual(operation["execution_status"], "applied_verified")
        self.assertIsNone(operation["post_apply_fingerprint"])
        self.assert_rollback(plan, available=False, status=NO_VERIFIED)

    async def test_mixed_plan_is_available_but_not_verified(self):
        # The helper create and the script update verify; the automation fails.
        created = await self.create_hvac_plan()
        self.gateway.fail_write_target = AUTOMATION
        result = await self.applied(created)
        self.assertNotEqual(result["task_state"], "succeeded_verified")
        plan, _view = self.surfaces(created, result)
        records = self.records(result["task_id"])
        by_id = {operation["operation_id"]: operation for operation in plan["operations"]}
        for operation_id in ("create_target_helper", "update_comfort_script"):
            with self.subTest(operation=operation_id):
                self.assert_operation_verified(by_id[operation_id], records[operation_id])
        failed = by_id["update_comfort_automation"]
        self.assertNotEqual(failed["execution_status"], "applied_verified")
        self.assertIsNone(failed["post_apply_fingerprint"])
        self.assert_plan_not_verified(plan)
        self.assert_rollback(plan, available=True, status=PENDING_CHECK)

    # -- placeholder-only rule -------------------------------------------------

    async def test_rollback_request_is_never_rewritten_by_a_later_projection(self):
        created = await self.create_automation_plan()
        result = await self.applied(created)
        rollback = await self.service.rollback_change(created["plan_id"], created["plan_hash"])
        self.assertEqual(rollback["status"], "rollback_plan_created")
        requested = unwrap(self.service.get_plan(created["plan_id"]))["rollback"]
        self.assertEqual(requested["request_id"], rollback["rollback_plan_id"])
        self.assertNotEqual(requested["status"], PENDING_CHECK)
        await self.runtime.recover_once("startup")
        self.assertEqual(unwrap(self.service.get_plan(created["plan_id"]))["rollback"], requested)
        # A direct re-projection also leaves the request untouched.
        stored = self.service.repository.get(created["plan_id"])
        task = self.service.task_repository.get(result["task_id"])
        self.runtime._project_rollback_availability(stored, task, self.records(result["task_id"]))
        self.assertEqual(stored.rollback.request_id, rollback["rollback_plan_id"])
        self.assertEqual(stored.rollback.status, requested["status"])

    async def test_only_the_creation_placeholder_is_replaced(self):
        created = await self.create_automation_plan()
        result = await self.applied(created)
        task = self.service.task_repository.get(result["task_id"])
        records = self.records(result["task_id"])
        for status, request_id in (
            ("rolled_back", None),
            ("unavailable_for_create", None),
            (PLACEHOLDER, "synthetic-rollback-request"),
        ):
            with self.subTest(status=status, request_id=request_id):
                plan = self.service.repository.get(created["plan_id"])
                plan.rollback.status, plan.rollback.request_id = status, request_id
                plan.rollback.available = False
                self.runtime._project_rollback_availability(plan, task, records)
                self.assertEqual(
                    (plan.rollback.status, plan.rollback.request_id, plan.rollback.available),
                    (status, request_id, False),
                )
        # Positive control: the untouched placeholder is replaced.
        plan = self.service.repository.get(created["plan_id"])
        plan.rollback.status, plan.rollback.request_id = PLACEHOLDER, None
        plan.rollback.available = False
        self.runtime._project_rollback_availability(plan, task, records)
        self.assertEqual((plan.rollback.status, plan.rollback.available), (PENDING_CHECK, True))

    async def test_restart_keeps_the_projected_verification_and_rollback(self):
        created = await self.create_automation_plan()
        result = await self.applied(created)
        before = unwrap(self.service.get_plan(created["plan_id"]))
        await self.runtime.recover_once("startup")
        after = unwrap(self.service.get_plan(created["plan_id"]))
        for key in ("verification", "rollback", "post_apply_fingerprint", "operations"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(
            self.service.get_execution_task(result["task_id"])["state"], "succeeded_verified"
        )


class ConfigurationRejectionTests(_Projection, producers.ConfigurationChainTests):
    """P1: a 400 from the real resource gateway is sent and rejected."""

    def reject(self, resource_type, target_id):
        self.routed.add((resource_type, target_id))
        self.rest.responses[("POST", f"/config/{resource_type}/config/{target_id}")] = (
            HomeAssistantApiError(details={"status": 400, "provider_response_received": True})
        )

    async def test_rejected_single_update_is_not_verified_and_not_available(self):
        self.reject(*AUTOMATION)
        created = await self.create_automation_plan()
        result, _view, _events = await self.applied(created)
        self.assertEqual(result["task_state"], "failed_post_dispatch")
        plan, _ = self.surfaces(created, result)
        self.assert_plan_not_verified(plan)
        self.assert_rollback(plan, available=False, status=NO_VERIFIED)

    async def test_verified_then_rejected_update_is_available_not_verified(self):
        created = await self.service.create_configuration_plan(
            title="Verified then rejected",
            description="HAMCP-214 mixed fixture",
            operations=[
                {
                    "operation_id": "verified_automation_update",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": AUTOMATION[1],
                    "depends_on": [],
                    "proposed_config": copy.deepcopy(PROPOSED_AUTOMATION),
                },
                {
                    "operation_id": "rejected_script_update",
                    "resource_type": "script",
                    "action": "update",
                    "target_id": SCRIPT[1],
                    "depends_on": ["verified_automation_update"],
                    "proposed_config": copy.deepcopy(PROPOSED_SCRIPT),
                },
            ],
        )
        self.reject(*SCRIPT)
        result, _view, _events = await self.applied(created)
        plan, _ = self.surfaces(created, result)
        records = self.records(result["task_id"])
        verified, rejected = plan["operations"]
        self.assert_operation_verified(verified, records["verified_automation_update"])
        self.assertEqual(rejected["execution_status"], "failed")
        self.assertIsNone(rejected["post_apply_fingerprint"])
        self.assert_plan_not_verified(plan)
        self.assert_rollback(plan, available=True, status=PENDING_CHECK)


# -- label ---------------------------------------------------------------------


class LabelProjectionTests(_Projection, label_tests.EndToEndTests):
    async def test_label_plan_rollback_is_not_supported(self):
        created, result = await self.run_plan(
            {"operation": "create", "name": "Holiday Lights"}
        )
        plan, _view = self.surfaces(created, result)
        self.assertEqual(plan["verification"]["status"], "verified")
        self.assert_rollback(
            plan, available=False, status=label_registry_planning.ROLLBACK_NOT_SUPPORTED
        )

    async def test_label_edit_and_assignment_rollback_is_not_supported(self):
        created, result = await self.run_plan(
            {"operation": "update", "label_id": "outdoor", "color": "red",
             "entity_ids": ["sensor.temp"]},
        )
        plan, _view = self.surfaces(created, result)
        self.assertGreater(len(plan["operations"]), 1)
        self.assertIsNone(plan["post_apply_fingerprint"])
        self.assert_rollback(
            plan, available=False, status=label_registry_planning.ROLLBACK_NOT_SUPPORTED
        )


# -- entity metadata -----------------------------------------------------------


class EntityMetadataProjectionTests(_Projection, metadata_tests.RollbackTests):
    async def test_verified_single_update_is_available(self):
        created = await self.approved(name="Porch sensor", icon="mdi:new")
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified")
        plan, _view = self.surfaces(created, result)
        self.assertEqual(plan["verification"]["status"], "verified")
        self.assertIsNotNone(plan["post_apply_fingerprint"])
        self.assert_rollback(plan, available=True, status=PENDING_CHECK)
        rollback = await self.service.rollback_change(created["plan_id"], created["plan_hash"])
        self.assertEqual(rollback["status"], "rollback_plan_created")

    async def test_refused_update_is_not_available(self):
        created = await self.approved()
        self.ws.failures[METADATA_UPDATE] = AuthorizationError(
            details={"provider_response_received": True}
        )
        result = await self.apply(created)
        self.assertNotEqual(result["task_state"], "succeeded_verified")
        plan, _view = self.surfaces(created, result)
        self.assert_plan_not_verified(plan)
        self.assert_rollback(plan, available=False, status=NO_VERIFIED)


# -- availability / exposure ---------------------------------------------------


class AvailabilityExposureProjectionTests(_Projection, exposure_tests.ExecutionTestCase):
    async def test_verified_disable_is_available(self):
        self.add_entity("light.porch")
        created = await self.approved(operation="disable", entity_ids=["light.porch"])
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified")
        plan, _view = self.surfaces(created, result)
        self.assertEqual(plan["verification"]["status"], "verified")
        self.assert_rollback(plan, available=True, status=PENDING_CHECK)

    async def test_refused_expose_is_not_available(self):
        self.add_entity("light.porch")
        created = await self.approved(
            operation="expose", entity_ids=["light.porch"], assistant="conversation"
        )
        self.ws.failures[exposure_tests.EXPOSE] = exposure_tests.unauthorized()
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_post_dispatch")
        plan, _view = self.surfaces(created, result)
        self.assert_plan_not_verified(plan)
        self.assert_rollback(plan, available=False, status=NO_VERIFIED)


# -- configuration delete ------------------------------------------------------


class ConfigurationDeleteProjectionTests(_Projection, delete_tests.EndToEndTests):
    async def test_verified_recreate_eligible_delete_is_available(self):
        self.add("automation", "porch_light")
        created = await self.approved("automation", "porch_light")
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified")
        plan, _view = self.surfaces(created, result)
        self.assertEqual(plan["verification"]["status"], "verified")
        self.assert_rollback(plan, available=True, status=PENDING_CHECK)

    async def test_rejected_delete_is_not_available(self):
        self.add("automation", "a")
        created = await self.approved("automation", "a")
        self.config_rest.before_delete = lambda: self.config_rest.configs["automation"].pop("a")
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_post_dispatch")
        plan, _view = self.surfaces(created, result)
        self.assert_plan_not_verified(plan)
        self.assert_rollback(plan, available=False, status=NO_VERIFIED)

    async def test_verified_delete_without_recreate_eligibility_is_not_available(self):
        # A pre-image the create contract would refuse is deletable but not
        # recreatable, so its delete intent records it as not eligible.
        self.add("automation", "porch_light", {**automation_config("porch_light"), "mode": "bogus"})
        created = await self.create_one("automation", "porch_light")
        stored = self.service.repository.get(created["plan_id"])
        self.assertFalse(_recreate_eligible(stored.operations[0]))
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified")
        plan, _view = self.surfaces(created, result)
        self.assert_rollback(plan, available=False, status=NO_VERIFIED)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)


# Inherited cases run in their own modules.
for _cls in (
    ConfigurationProjectionTests,
    ConfigurationRejectionTests,
    LabelProjectionTests,
    EntityMetadataProjectionTests,
    AvailabilityExposureProjectionTests,
    ConfigurationDeleteProjectionTests,
):
    for _base in _cls.__mro__[1:]:
        for _name in vars(_base):
            if _name.startswith("test_") and _name not in vars(_cls):
                setattr(_cls, _name, None)
# The loop name would otherwise be collected as a second copy of the last class.
del _cls, _base, _name


if __name__ == "__main__":
    unittest.main()
