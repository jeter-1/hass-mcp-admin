"""HAMCP-165 review1 corrections: dispatch admission, builder admission, final-read drift.

H1: every F3 path (first apply, retained approved plan across restart,
recovery) for a dormant seq-13 profile (labels and both 069B resource types)
re-checks Core admission at dispatch.  No Core, a Core that cannot report
status, or an unadmitted profile refuses with zero Home Assistant commands.
M1: generic ``create_configuration_plan`` routing refuses an unadmitted
dormant profile before any read or store.
M3: the final pre-dispatch label read judges the exact target against its
bound baseline; any drift refuses as stale and is never overwritten.
"""

from __future__ import annotations

from copy import deepcopy
import unittest

from tests.hamcp069b_fixtures import AdmittingCore, AvailabilityExposureServiceTestCase
from tests.hamcp165_fixtures import LABEL_UPDATE, LabelAdmittingCore
from tests.test_hamcp165_label_registry import (
    CREATE_HOLIDAY,
    PENDING_DETAILS,
    ServiceTestCase,
)

from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from ha_mcp_engineering.f3_runtime.runtime import (
    UNSIGNED_PROFILE_CORE_AUTHORITY_UNAVAILABLE as NO_CORE,
    UNSIGNED_PROFILE_PENDING_ADMISSION as PENDING,
    UNSIGNED_PROFILE_REQUIREMENTS,
)

# A Core that is present but no longer admits the profile is refused first by
# the existing Core dispatch-authority guard; the dormant-profile gate is the
# independent second barrier (and the only one when Core is absent).
CORE_GUARD = "dispatch_authority_unavailable"
RECOLOR = {"operation": "update", "label_id": "outdoor", "color": "red"}


class BrokenLabelCore(LabelAdmittingCore):
    def route_status(self, capability_ids, **_):
        raise RuntimeError("synthetic")


class BrokenCore(AdmittingCore):
    def route_status(self, capability_ids, **_):
        raise RuntimeError("synthetic")


class WithdrawAtDispatchCore(LabelAdmittingCore):
    """Admits the preflight status check, then withdraws before the send."""

    armed = False

    def route_status(self, capability_ids, **kwargs):
        if self.armed:
            self.route_queries.append(tuple(capability_ids))
            if len(self.route_queries) > 1:
                self.withdraw()
        return {"available": bool(capability_ids) and self._admits(capability_ids)}


def generic_operation(resource_type: str, target_id: str, proposed: dict) -> dict:
    return {
        "operation_id": "x",
        "resource_type": resource_type,
        "action": "update",
        "target_id": target_id,
        "proposed_config": proposed,
    }


class LabelDispatchAdmissionTests(ServiceTestCase):
    """H1 for labels: a retained approved plan never touches HA without admission."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.ws.add_label("outdoor", "Outdoor", color="green")

    async def apply_refused(self, created: dict, code: str) -> dict:
        self.ws.commands.clear()
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertIn(code, self.child_codes(result["task_id"]))
        self.assertEqual(self.ws.commands, [])
        return result

    async def recover_reads_nothing(self) -> None:
        self.ws.commands.clear()
        await self.restart_runtime()
        await self.runtime.recover_once("synthetic_restart")
        self.assertEqual(self.ws.commands, [])

    async def test_retained_approved_create_without_core_refuses_with_zero_commands(self):
        created = await self.approved(CREATE_HOLIDAY)
        self.core = None
        await self.restart_runtime()
        await self.apply_refused(created, NO_CORE)
        await self.recover_reads_nothing()
        self.assertIsNone(self.ws.label("holiday"))

    async def test_retained_approved_update_without_core_refuses_with_zero_commands(self):
        created = await self.approved(RECOLOR)
        self.core = None
        await self.restart_runtime()
        await self.apply_refused(created, NO_CORE)
        await self.recover_reads_nothing()
        self.assertEqual(self.ws.label("outdoor")["color"], "green")

    async def test_core_that_cannot_report_status_refuses_with_zero_commands(self):
        created = await self.approved(RECOLOR)
        self.core = BrokenLabelCore()
        await self.restart_runtime()
        await self.apply_refused(created, PENDING)
        await self.recover_reads_nothing()
        self.assertEqual(self.ws.label("outdoor")["color"], "green")

    async def test_withdrawn_profile_refuses_with_zero_commands_across_restart(self):
        created = await self.approved(RECOLOR)
        self.core.withdraw()
        await self.apply_refused(created, CORE_GUARD)
        await self.recover_reads_nothing()
        self.assertEqual(self.ws.label("outdoor")["color"], "green")

    async def test_admission_withdrawn_at_the_dispatch_boundary_writes_nothing(self):
        created = await self.approved(RECOLOR)
        self.core = WithdrawAtDispatchCore()
        await self.restart_runtime()
        self.core.armed = True
        self.ws.commands.clear()
        result = await self.apply(created)
        self.assertGreaterEqual(len(self.core.route_queries), 2)
        self.assertNotEqual(result["task_state"], "succeeded_verified", result)
        self.assertNotIn(LABEL_UPDATE, self.ws.command_types())
        self.assertEqual(self.ws.label("outdoor")["color"], "green")

    async def test_admitted_core_still_applies(self):
        # Useful success: the gate refuses only while admission is absent.
        created = await self.approved(RECOLOR)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual([write["type"] for write in self.ws.writes()], [LABEL_UPDATE])
        self.assertEqual(self.ws.label("outdoor")["color"], "red")


class LabelBuilderAdmissionTests(ServiceTestCase):
    """M1 for labels: generic configuration routing refuses before any read or store."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.ws.add_label("outdoor", "Outdoor")
        self.ws.commands.clear()

    async def test_generic_configuration_plan_refuses_unadmitted_label_operations(self):
        for label, setup in (
            ("withdrawn", lambda: self.core.withdraw()),
            ("no core runtime", lambda: setattr(self.runtime, "core_runtime", None)),
            ("broken core", lambda: setattr(self.runtime, "core_runtime", BrokenLabelCore())),
            ("no f3 runtime", lambda: setattr(self.service, "f3_runtime", None)),
        ):
            with self.subTest(label):
                setup()
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_configuration_plan(
                        title="t", description="d",
                        operations=[generic_operation(
                            "label_registry_entry", "outdoor", {"name": "Garden"})],
                    )
                self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
                self.assertEqual(raised.exception.details, PENDING_DETAILS)
                self.assert_nothing_read_or_stored()
                self.assertEqual(self.core.planning_leases, [])


class LabelFinalReadDriftTests(ServiceTestCase):
    """M3: the final namespace read judges the exact target; drift is never overwritten."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.ws.add_label(
            "outdoor", "Outdoor", color="green", icon="mdi:tree", description="Yard"
        )

    async def drift_at_final_read(self, created: dict, drift) -> dict:
        gateway = self.service.label_registry_gateway
        original = gateway.namespace
        fired = [False]

        async def drifting():
            if not fired[0]:
                fired[0] = True
                drift()
            return await original()

        gateway.namespace = drifting
        self.ws.commands.clear()
        try:
            result = await self.apply(created)
        finally:
            gateway.namespace = original
        self.assertTrue(fired[0])
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertEqual(self.ws.writes(), [])
        return result

    async def test_each_target_field_drift_refuses_stale(self):
        for field, value in (
            ("color", "blue"),
            ("icon", "mdi:flower"),
            ("description", "Garden"),
            ("name", "Patio"),
        ):
            with self.subTest(field=field):
                before = deepcopy(self.ws.label("outdoor"))
                created = await self.approved(RECOLOR)
                result = await self.drift_at_final_read(
                    created,
                    lambda: self.ws.set_external_label("outdoor", **{field: value}),
                )
                codes = self.child_codes(result["task_id"])
                self.assertIn("stale_target_state", codes)
                self.assertNotIn("label_namespace_changed", codes)
                after = self.ws.label("outdoor")
                self.assertEqual(after[field], value)
                if field != "color":
                    self.assertEqual(after["color"], "green")
                self.ws.set_external_label("outdoor", **{field: before[field]})

    async def test_deleted_target_refuses_stale(self):
        created = await self.approved(RECOLOR)
        result = await self.drift_at_final_read(
            created, lambda: self.ws.labels.remove(self.ws.label("outdoor"))
        )
        self.assertIn("stale_target_state", self.child_codes(result["task_id"]))
        self.assertIsNone(self.ws.label("outdoor"))

    async def test_create_target_that_appears_refuses_stale(self):
        created = await self.approved(CREATE_HOLIDAY)
        result = await self.drift_at_final_read(
            created, lambda: self.ws.add_label("holiday", "Holiday", color="blue")
        )
        self.assertIn("stale_target_state", self.child_codes(result["task_id"]))
        self.assertEqual(self.ws.label("holiday")["color"], "blue")

    async def test_unrelated_namespace_change_still_refuses_namespace_changed(self):
        created = await self.approved(RECOLOR)
        result = await self.drift_at_final_read(
            created, lambda: self.ws.add_label("other", "Other")
        )
        codes = self.child_codes(result["task_id"])
        self.assertIn("label_namespace_changed", codes)
        self.assertNotIn("stale_target_state", codes)
        self.assertEqual(self.ws.label("outdoor")["color"], "green")

    async def test_unchanged_target_applies(self):
        created = await self.approved(RECOLOR)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(self.ws.label("outdoor")["color"], "red")


class AvailabilityExposureDispatchAdmissionTests(AvailabilityExposureServiceTestCase):
    """H1 audit for 069B: the same F3 dispatch gate covers both resource types."""

    CASES = (
        ("entity_registry_availability",
         {"operation": "disable", "entity_ids": ["light.porch"]}),
        ("entity_assistant_exposure",
         {"operation": "expose", "entity_ids": ["light.porch"], "assistant": "conversation"}),
    )

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add_entity("light.porch")
        self.baseline = deepcopy(self.ws.entries["light.porch"])

    def test_both_resource_types_are_gated(self):
        self.assertEqual(
            sorted(UNSIGNED_PROFILE_REQUIREMENTS),
            ["entity_assistant_exposure", "entity_registry_availability",
             "label_registry_entry"],
        )

    async def approved_under_admission(self, request: dict) -> dict:
        self.core = AdmittingCore()
        await self.restart_runtime()
        return await self.approved(**deepcopy(request))

    async def refused(self, created: dict, code: str) -> None:
        self.ws.commands.clear()
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertIn(code, self.child_codes(result["task_id"]))
        self.assertEqual(self.ws.commands, [])
        await self.restart_runtime()
        await self.runtime.recover_once("synthetic_restart")
        self.assertEqual(self.ws.commands, [])
        self.assertEqual(self.ws.entries["light.porch"], self.baseline)

    async def test_retained_approved_plan_without_core_refuses_with_zero_commands(self):
        for resource_type, request in self.CASES:
            with self.subTest(resource_type):
                created = await self.approved_under_admission(request)
                self.core = None
                await self.restart_runtime()
                await self.refused(created, NO_CORE)

    async def test_core_that_cannot_report_status_refuses_with_zero_commands(self):
        for resource_type, request in self.CASES:
            with self.subTest(resource_type):
                created = await self.approved_under_admission(request)
                self.core = BrokenCore()
                await self.restart_runtime()
                await self.refused(created, PENDING)

    async def test_withdrawn_profile_refuses_with_zero_commands(self):
        for resource_type, request in self.CASES:
            with self.subTest(resource_type):
                created = await self.approved_under_admission(request)
                self.core.withdraw()
                await self.refused(created, CORE_GUARD)

    async def test_generic_configuration_plan_refuses_unadmitted_069b_types(self):
        self.core.withdraw()
        for resource_type, _ in self.CASES:
            with self.subTest(resource_type):
                self.ws.commands.clear()
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_configuration_plan(
                        title="t", description="d",
                        operations=[generic_operation(
                            resource_type, "light.porch", {"disabled_by": "user"})],
                    )
                self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
                details = raised.exception.details
                self.assertEqual(details["reason_code"], PENDING)
                self.assertEqual(details["resource_type"], resource_type)
                self.assertIs(details["plan_created"], False)
                self.assertEqual(self.ws.commands, [])
                self.assertEqual(self.snapshot_reads, [])
                self.assertEqual(self.repository.list(), [])


if __name__ == "__main__":
    unittest.main()
