"""HAMCP-165: an unadmitted first apply refuses before any persistence.

A label or HAMCP-069B availability/exposure plan whose dormant Core profile
is not currently admitted refuses at apply entry: the plan, task, child,
manifest and lock stores stay byte-identical and Home Assistant receives no
request.  Every first-apply path is covered: F3 with a withdrawn profile, F3
composed without Core, a Core that cannot report status, and the service
without an F3 runtime (the legacy path).  The dispatch-time gate behind the
entry refusal is proved separately in ``test_hamcp165_review1_corrections``.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest

from tests.hamcp069b_fixtures import AdmittingCore, AvailabilityExposureServiceTestCase
from tests.hamcp165_fixtures import LABEL_CREATE, LABEL_UPDATE
from tests.test_hamcp165_label_registry import CREATE_HOLIDAY, ServiceTestCase

from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from ha_mcp_engineering.f3_runtime.runtime import (
    UNSIGNED_PROFILE_CORE_AUTHORITY_UNAVAILABLE as NO_CORE,
    UNSIGNED_PROFILE_PENDING_ADMISSION as PENDING,
)
from ha_mcp_engineering.ha_core_readmission.routes import (
    AVAILABILITY_PROFILE,
    EXPOSURE_PROFILE,
    LABEL_PROFILE,
)

RECOLOR = {"operation": "update", "label_id": "outdoor", "color": "red"}
ASSIGN = {**CREATE_HOLIDAY, "entity_ids": ["sensor.temp"]}


def store_bytes(root: Path) -> dict[str, bytes]:
    """Every plan, task, child, manifest and lock record, byte for byte."""

    store = root / "plans"
    return {
        str(path.relative_to(store)): path.read_bytes()
        for path in sorted(store.rglob("*"))
        if path.is_file()
    }


def _raising_route_status(*_args, **_kwargs):
    raise RuntimeError("synthetic")


class EntryRefusalMixin:
    """Shared refusal assertions; ``self`` is a label or 069B service case."""

    def withdraw(self) -> None:
        self.core.withdraw()

    def no_core(self) -> None:
        self.runtime.core_runtime = None

    def broken(self) -> None:
        self.core.route_status = _raising_route_status

    def no_f3(self) -> None:
        self.service.f3_runtime = None

    SETUPS = (
        ("withdrawn", "withdraw", PENDING),
        ("no core runtime", "no_core", NO_CORE),
        ("core cannot report status", "broken", PENDING),
        ("no F3 runtime", "no_f3", NO_CORE),
    )

    async def assert_refused_before_persistence(
        self, created: dict, setup: str, code: str, profile: str
    ) -> None:
        getattr(self, setup)()
        before = store_bytes(self.root)
        self.assertTrue(before)
        self.ws.commands.clear()
        with self.assertRaises(GovernanceError) as refused:
            await self.apply(created)
        error = refused.exception
        self.assertEqual(error.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(error.details["reason_code"], code)
        self.assertIs(error.details["execution_state_persisted"], False)
        self.assertIs(error.details["provider_dispatch_occurred"], False)
        if setup == "no_f3":
            self.assertEqual(error.details["required_action"], "active_f3_execution_required")
        else:
            self.assertEqual(error.details["core_capability"], profile)
        self.assertEqual(self.ws.commands, [])
        self.assertEqual(store_bytes(self.root), before)
        self.assertIsNone(self.service.task_repository.get_for_plan(created["plan_id"]))

    async def assert_recovery_reads_nothing(self) -> None:
        self.ws.commands.clear()
        await self.restart_runtime()
        await self.runtime.recover_once("synthetic_restart")
        self.assertEqual(self.ws.commands, [])


class LabelEntryAdmissionTests(EntryRefusalMixin, ServiceTestCase):
    """Every label first-apply path refuses before persistence."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.ws.add_label("outdoor", "Outdoor", color="green")
        self.add_entity("sensor.temp")

    async def admitted_plan(self, operation: dict) -> dict:
        self.core.admitted = set(self.core.GATED)
        await self.restart_runtime()
        return await self.approved(deepcopy(operation))

    async def test_every_first_apply_path_refuses_before_persistence(self):
        for name, setup, code in self.SETUPS:
            for operation in (CREATE_HOLIDAY, RECOLOR, ASSIGN):
                with self.subTest(name, operation=operation["operation"],
                                  assigned="entity_ids" in operation):
                    created = await self.admitted_plan(operation)
                    await self.assert_refused_before_persistence(
                        created, setup, code, LABEL_PROFILE
                    )
                    self.core = type(self.core)(self.core.GATED)
                    await self.assert_recovery_reads_nothing()
        self.assertIsNone(self.ws.label("holiday"))
        self.assertEqual(self.ws.label("outdoor")["color"], "green")

    async def test_refused_plan_applies_once_admission_returns(self):
        # Useful success: the entry refusal leaves the approved plan intact,
        # so it applies when Core admits the profile again.
        created = await self.admitted_plan(RECOLOR)
        await self.assert_refused_before_persistence(
            created, "withdraw", PENDING, LABEL_PROFILE
        )
        self.core.admitted = set(self.core.GATED)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual([write["type"] for write in self.ws.writes()], [LABEL_UPDATE])
        self.assertEqual(self.ws.label("outdoor")["color"], "red")

    async def test_refused_create_applies_after_restart_with_admission(self):
        created = await self.admitted_plan(CREATE_HOLIDAY)
        await self.assert_refused_before_persistence(
            created, "no_core", NO_CORE, LABEL_PROFILE
        )
        await self.restart_runtime()
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual([write["type"] for write in self.ws.writes()], [LABEL_CREATE])

    async def test_admitted_first_apply_is_not_refused(self):
        created = await self.admitted_plan(ASSIGN)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertIsNotNone(self.ws.label("holiday"))

    def test_plan_without_dormant_profiles_never_queries_core(self):
        queried = []
        self.runtime.core_runtime = SimpleNamespace(
            route_status=lambda *args, **_: queried.append(args) or {"available": False}
        )
        plan = SimpleNamespace(
            contract_version=2,
            operations=[
                SimpleNamespace(
                    action="update", resource_type="automation", helper_type=None
                )
            ],
        )
        self.runtime._refuse_unadmitted_entry(plan)
        self.service._refuse_dormant_profile_without_f3(plan)
        self.assertEqual(queried, [])


class AvailabilityExposureEntryAdmissionTests(
    EntryRefusalMixin, AvailabilityExposureServiceTestCase
):
    """Both 069B resource types refuse before persistence on every path."""

    CASES = (
        (AVAILABILITY_PROFILE, {"operation": "disable", "entity_ids": ["light.porch"]}),
        (EXPOSURE_PROFILE,
         {"operation": "expose", "entity_ids": ["light.porch"], "assistant": "conversation"}),
    )

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add_entity("light.porch")
        self.baseline = deepcopy(self.ws.entries["light.porch"])

    async def admitted_plan(self, request: dict) -> dict:
        self.core = AdmittingCore()
        await self.restart_runtime()
        return await self.approved(**deepcopy(request))

    async def test_every_first_apply_path_refuses_before_persistence(self):
        for name, setup, code in self.SETUPS:
            for profile, request in self.CASES:
                with self.subTest(name, operation=request["operation"]):
                    created = await self.admitted_plan(request)
                    await self.assert_refused_before_persistence(
                        created, setup, code, profile
                    )
                    await self.assert_recovery_reads_nothing()
                    self.assertEqual(self.ws.entries["light.porch"], self.baseline)

    async def test_refused_plan_applies_once_admission_returns(self):
        for profile, request in self.CASES:
            with self.subTest(operation=request["operation"]):
                created = await self.admitted_plan(request)
                await self.assert_refused_before_persistence(
                    created, "withdraw", PENDING, profile
                )
                self.core.admitted = {AVAILABILITY_PROFILE, EXPOSURE_PROFILE}
                result = await self.apply(created)
                self.assertEqual(result["task_state"], "succeeded_verified", result)
                self.assertEqual(len(self.ws.writes()), 1)
                self.ws.entries["light.porch"] = deepcopy(self.baseline)
                self.ws.commands.clear()


if __name__ == "__main__":
    unittest.main()
