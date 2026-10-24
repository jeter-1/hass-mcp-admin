"""HAMCP-155 merge seams with origin/main fff324b (HAMCP-165, 2.4.1-beta.34).

The owner-authorized union of HAMCP-155 deletion, HAMCP-165 labels and
HAMCP-069B availability/exposure meets at three seams:

1. ``_create_configuration_plan`` admits exactly one planning family (entity
   metadata, availability/exposure, label, deletion); any mix is refused, a
   label plan is never a rollback, and a delete is never a rollback.
2. Each family's diff and risk come from its own branch; none falls through
   to another family's branch or to the generic default, and the generic
   route refuses every family type and an unknown type.
3. The first-apply entry check (before any persistence) and the
   dispatch-time admission cover every dormant profile: deletion, label and
   both HAMCP-069B types.

Every path runs against the synthetic offline fixtures; nothing here reaches
Home Assistant.
"""

from __future__ import annotations

from copy import deepcopy
from itertools import combinations
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    UNSIGNED_PROFILE_PENDING_ADMISSION as PENDING,
    UNSIGNED_PROFILE_REQUIREMENTS,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    configuration_delete_planning,
    entity_availability_exposure_planning,
    entity_metadata_planning,
    label_registry_planning,
)
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    AVAILABILITY_PROFILE,
    CONFIGURATION_DELETE_PROFILE,
    EXPOSURE_PROFILE,
    F3_ENTRY_ADMISSION_PROFILES,
    LABEL_PROFILE,
    f3_entry_admission_profiles,
)

from tests.hamcp155_fixtures import ConfigurationDeleteServiceTestCase  # noqa: E402
from tests.hamcp165_fixtures import LABEL_CREATE, bypass_entry_admission  # noqa: E402
from tests.test_hamcp165_entry_admission import store_bytes  # noqa: E402
from tests.test_hamcp165_label_registry import (  # noqa: E402
    CREATE_HOLIDAY,
    ServiceTestCase as LabelServiceCase,
)

# Seam 1: the four planning-family contexts of the internal builder.
FAMILIES = (
    "_entity_metadata_context",
    "_availability_exposure_context",
    "_label_context",
    "_configuration_delete_context",
)
# Seam 2: the diff key each family's own branch writes.
DIFF_KEYS = {
    "entity_metadata",
    "entity_availability_exposure",
    "label_registry",
    "configuration_delete",
}
PLANNING = {
    "entity_metadata": entity_metadata_planning,
    "entity_availability_exposure": entity_availability_exposure_planning,
    "label_registry": label_registry_planning,
    "configuration_delete": configuration_delete_planning,
}
CORE_GUARD = "dispatch_authority_unavailable"
DISABLE = {"operation": "disable", "entity_ids": ["light.porch"]}
EXPOSE = {"operation": "expose", "entity_ids": ["light.porch"], "assistant": "conversation"}


def generic_operation(resource_type: str, action: str = "update") -> dict:
    return {
        "operation_id": "x",
        "resource_type": resource_type,
        "action": action,
        "target_id": "porch_light",
        "proposed_config": {"alias": "Porch"},
    }


def family_diffs(created: dict) -> list[set[str]]:
    return [
        set(operation["dry_run_results"]) & DIFF_KEYS
        for operation in created["dry_run_results"]["operations"]
    ]


def other_families_raise(own: str, *, keep: tuple[str, ...] = ()):
    """Patch every other family's projection and risk to fail the test."""

    patchers = []
    for key, module in PLANNING.items():
        if key == own:
            continue
        for name in ("projection", "risk_assessment"):
            if (key, name) in keep:
                continue
            patchers.append(patch.object(
                module, name, side_effect=AssertionError(f"fell through to {key}.{name}")))
    return patchers


class Seam1FamilyExclusivityTests(ConfigurationDeleteServiceTestCase):
    """One planning family per plan; neither labels nor deletes are rollbacks."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add("automation", "porch_light")
        self.before = store_bytes(self.root)
        self.reads_before = self.reads()

    async def build(self, **contexts):
        return await self.service._create_configuration_plan(
            title="seam", description="seam",
            operations=[generic_operation("automation")],
            _before_persist=lambda: None, **contexts,
        )

    def assert_nothing_stored(self):
        self.assertEqual(store_bytes(self.root), self.before)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.reads(), self.reads_before)
        self.assertEqual(self.config_rest.writes(), [])

    async def assert_family_check_refuses(self, **contexts):
        # Admission is isolated so the family check is the only gate left.
        with patch.object(self.service, "_require_dormant_profile_admission", return_value=False), \
                patch.object(self.service, "_configuration_delete_core", return_value=None), \
                self.assertRaises(GovernanceError) as raised:
            await self.build(**contexts)
        self.assertEqual(raised.exception.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED)
        # The family check names no operation (the exact-operation checks do).
        self.assertNotIn("validation_errors", raised.exception.details or {})
        self.assert_nothing_stored()

    async def test_every_mix_of_families_is_refused_before_anything_is_stored(self):
        for size in (2, 3, 4):
            for mix in combinations(FAMILIES, size):
                with self.subTest(mix=mix):
                    await self.assert_family_check_refuses(**{name: object() for name in mix})

    async def test_a_mix_is_refused_with_real_admission_too(self):
        # Label and 069B Core are not admitted here: a mix still stores nothing.
        stand_in = {name: object() for name in FAMILIES}
        stand_in["_availability_exposure_context"] = SimpleNamespace(
            request=SimpleNamespace(resource_type="entity_registry_availability"))
        for mix in combinations(FAMILIES, 2):
            with self.subTest(mix=mix), self.assertRaises(GovernanceError):
                await self.build(**{name: stand_in[name] for name in mix})
            self.assert_nothing_stored()

    async def test_a_label_plan_is_never_a_rollback(self):
        await self.assert_family_check_refuses(
            _label_context=object(), _rollback_context={"plan_id": "p"})

    async def test_a_delete_is_never_a_rollback(self):
        await self.assert_family_check_refuses(
            _configuration_delete_context=object(), _rollback_context={"plan_id": "p"})


class Seam2FamilyBranchTests(ConfigurationDeleteServiceTestCase):
    """Each family alone is accepted and uses only its own diff and risk."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add("automation", "porch_light")
        self.add_entity("light.porch")
        self.add_entity("sensor.porch")

    async def planned_alone(self, own, create, keep=()):
        patchers = other_families_raise(own, keep=keep)
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        created = await create()
        for patcher in patchers:
            patcher.stop()
        self.assertEqual(created["status"], "awaiting_approval", created)
        self.assertTrue(family_diffs(created))
        self.assertEqual(family_diffs(created), [{own}] * len(family_diffs(created)))
        return created

    async def test_deletion_selects_its_own_branch(self):
        created = await self.planned_alone(
            "configuration_delete", lambda: self.create_one("automation", "porch_light"))
        (operation,) = created["dry_run_results"]["operations"]
        self.assertEqual(operation["action"], "delete")

    async def test_availability_and_exposure_select_their_own_branch(self):
        for request in (DISABLE, EXPOSE):
            with self.subTest(operation=request["operation"]):
                await self.planned_alone(
                    "entity_availability_exposure",
                    lambda: self.service.create_entity_availability_exposure_plan(deepcopy(request)))

    async def test_entity_metadata_selects_its_own_branch(self):
        await self.planned_alone(
            "entity_metadata",
            lambda: self.service.create_entity_metadata_plan(
                {"entity_id": "sensor.porch", "name": "Porch sensor"}))

    async def test_the_generic_route_refuses_every_family_type_and_an_unknown_type(self):
        cases = [
            generic_operation("automation", "delete"),
            generic_operation("not_a_reviewed_resource_type"),
            generic_operation("label_registry_entry"),
            generic_operation("entity_registry_availability"),
            generic_operation("entity_assistant_exposure"),
            generic_operation("entity_registry_metadata"),
        ]
        for operation in cases:
            with self.subTest(resource_type=operation["resource_type"], action=operation["action"]):
                before = store_bytes(self.root)
                with self.assertRaises(GovernanceError):
                    await self.service.create_configuration_plan(
                        title="t", description="d", operations=[deepcopy(operation)])
                self.assertEqual(store_bytes(self.root), before)
                self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.config_rest.writes(), [])


class Seam2LabelBranchTests(LabelServiceCase):
    """The label family alone is accepted and uses only its own branch."""

    async def test_label_selects_its_own_branch(self):
        self.add_entity("sensor.temp")
        # An entity assignment child is projected and risk-rated by the label
        # branch through the entity metadata helpers, inside its own branch.
        patchers = other_families_raise(
            "label_registry",
            keep=(("entity_metadata", "projection"), ("entity_metadata", "risk_assessment")))
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        for request in (CREATE_HOLIDAY, {**CREATE_HOLIDAY, "name": "Away", "entity_ids": ["sensor.temp"]}):
            with self.subTest(assigned="entity_ids" in request):
                created = await self.create(deepcopy(request))
                self.assertEqual(created["status"], "awaiting_approval", created)
                self.assertEqual(family_diffs(created), [{"label_registry"}] * len(family_diffs(created)))


class Seam3AdmissionCoverageTests(unittest.TestCase):
    """The entry and dispatch checks are keyed to every dormant profile."""

    def test_entry_admission_covers_every_dormant_profile(self):
        self.assertEqual(
            F3_ENTRY_ADMISSION_PROFILES,
            {CONFIGURATION_DELETE_PROFILE, LABEL_PROFILE, AVAILABILITY_PROFILE, EXPOSURE_PROFILE},
        )
        expected = {
            "delete_automation_configuration": (CONFIGURATION_DELETE_PROFILE,),
            "delete_script_configuration": (CONFIGURATION_DELETE_PROFILE,),
            "create_label_registry_entry_configuration": (LABEL_PROFILE,),
            "update_label_registry_entry_configuration": (LABEL_PROFILE,),
            "update_entity_registry_availability_configuration": (AVAILABILITY_PROFILE,),
            "update_entity_assistant_exposure_configuration": (EXPOSURE_PROFILE,),
            "update_automation_configuration": (),
        }
        for identity, profiles in expected.items():
            with self.subTest(identity=identity):
                self.assertEqual(f3_entry_admission_profiles([identity]), profiles)

    def test_dispatch_admission_covers_label_and_both_069b_types(self):
        # Deletion has its own adapter admission (Seam3DeleteTests).
        self.assertEqual(dict(UNSIGNED_PROFILE_REQUIREMENTS), {
            "label_registry_entry": LABEL_PROFILE,
            "entity_registry_availability": AVAILABILITY_PROFILE,
            "entity_assistant_exposure": EXPOSURE_PROFILE,
        })


class Seam3DeleteAnd069BTests(ConfigurationDeleteServiceTestCase):
    """Deletion and both HAMCP-069B profiles: entry, then dispatch."""

    CASES = (
        (CONFIGURATION_DELETE_PROFILE, None),
        (AVAILABILITY_PROFILE, DISABLE),
        (EXPOSURE_PROFILE, EXPOSE),
    )

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add("automation", "porch_light")
        self.add_entity("light.porch")
        self.baseline = deepcopy(self.ws.entries)

    async def approved_plan(self, request):
        if request is None:
            return await self.approved("automation", "porch_light")
        created = await self.service.create_entity_availability_exposure_plan(deepcopy(request))
        await self.approve(created)
        return created

    def assert_untouched(self):
        self.assertEqual(self.config_rest.deletes(), [])
        self.assertEqual(self.ws.writes(), [])
        self.assertIn("porch_light", self.config_rest.configs["automation"])
        self.assertEqual(self.ws.entries, self.baseline)

    async def test_entry_refuses_every_unadmitted_profile_before_persistence(self):
        for profile, request in self.CASES:
            with self.subTest(profile=profile):
                self.core.admitted = set(self.core.admitted) | {profile}
                created = await self.approved_plan(request)
                self.core.withdraw(profile)
                before, reads = store_bytes(self.root), self.reads()
                with self.assertRaises(GovernanceError) as raised:
                    await self.apply(created)
                details = raised.exception.details
                self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
                self.assertEqual(details["reason_code"], PENDING)
                self.assertEqual(details["core_capability"], profile)
                self.assertIs(details["execution_state_persisted"], False)
                self.assertIs(details["provider_dispatch_occurred"], False)
                self.assertEqual(store_bytes(self.root), before)
                self.assertEqual(self.reads(), reads)
                self.assertIsNone(self.service.task_repository.get_for_plan(created["plan_id"]))
                self.assert_untouched()
                self.core.admitted = set(self.core.admitted) | {profile}

    async def test_dispatch_refuses_every_unadmitted_profile_without_a_write(self):
        bypass_entry_admission(self)
        for profile, request in self.CASES:
            with self.subTest(profile=profile):
                self.core.admitted = set(self.core.admitted) | {profile}
                created = await self.approved_plan(request)
                self.core.withdraw(profile)
                result = await self.apply(created)
                self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
                self.assertTrue(
                    self.child_codes(result["task_id"]) & {CORE_GUARD, "core_admission_unavailable"},
                    self.child_codes(result["task_id"]))
                self.assert_untouched()
                self.core.admitted = set(self.core.admitted) | {profile}

    async def test_admitted_profiles_still_apply(self):
        # Useful success: the gates refuse only while admission is absent.
        for _profile, request in self.CASES:
            created = await self.approved_plan(request)
            result = await self.apply(created)
            self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(len(self.config_rest.deletes()), 1)


class Seam3LabelTests(LabelServiceCase):
    """The label profile: entry, then dispatch."""

    async def test_entry_refuses_the_unadmitted_label_profile_before_persistence(self):
        created = await self.approved(deepcopy(CREATE_HOLIDAY))
        self.core.withdraw()
        before = store_bytes(self.root)
        self.ws.commands.clear()
        with self.assertRaises(GovernanceError) as raised:
            await self.apply(created)
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(raised.exception.details["reason_code"], PENDING)
        self.assertEqual(raised.exception.details["core_capability"], LABEL_PROFILE)
        self.assertEqual(store_bytes(self.root), before)
        self.assertEqual(self.ws.commands, [])
        self.assertIsNone(self.service.task_repository.get_for_plan(created["plan_id"]))

    async def test_dispatch_refuses_the_unadmitted_label_profile_without_a_command(self):
        bypass_entry_admission(self)
        created = await self.approved(deepcopy(CREATE_HOLIDAY))
        self.core.withdraw()
        self.ws.commands.clear()
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertEqual(self.ws.commands, [])
        self.assertNotIn(LABEL_CREATE, self.ws.command_types())
        self.assertIsNone(self.ws.label("holiday"))


if __name__ == "__main__":
    unittest.main()
