"""HAMCP-069B review 1 corrections (PR #297 at 241b43b).

Each reviewer reproduction is a permanent test here, followed by the
regression set for each finding:

* H1: incomplete dependency/impact evidence, for any reason, makes a disable
  protected (OD-4) through one shared predicate, ``dependency_evidence_gaps``.
* M1: the service layer enforces signed-registry admission before any Home
  Assistant read and before persistence, on every entry point.

Every provider is synthetic; nothing here reaches a live system.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest

from tests.hamcp069b_fixtures import (
    AdmittingCore,
    AvailabilityExposureServiceTestCase,
    automation_profile,
    coverage_item,
    exposure_snapshot,
    finding,
    script_diagnostics,
)
from tests.test_hamcp069b_entity_availability_exposure_execution import (
    child_disclosures,
    policy,
)
import tests.test_hamcp069b_entity_availability_exposure_admission as admission

from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from ha_mcp_engineering.governance import entity_availability_exposure_planning as planning
from ha_mcp_engineering.governance.entity_metadata_disclosure import (
    EntityMetadataEvidenceUnavailable,
)
from ha_mcp_engineering.ha_core_readmission.runtime import CoreRuntime


AVAILABILITY = admission.AVAILABILITY
EXPOSURE = admission.EXPOSURE
INCOMPLETE = planning.INCOMPLETE_IMPACT_TEXT
PENDING = "pending_signed_registry_admission"

# Pinned Core default-exposure classifier (the reviewer's first probe).
# Provenance: home-assistant/core tag 2026.10.1, commit
# abe1fdd7cd6b4de1715c87b6fd87d92699514ef9,
# homeassistant/components/homeassistant/exposed_entities.py lines 32-44
# (DEFAULT_EXPOSED_DOMAINS) and 304-316 (the domain branch of
# ExposedEntities._is_default_exposed), copied verbatim.
CORE_DEFAULT_EXPOSED_DOMAINS = frozenset({
    "climate", "cover", "fan", "humidifier", "light", "media_player",
    "scene", "switch", "todo", "vacuum", "water_heater",
})
CORE_SOURCE = Path(
    "/home/josh/.cache/hamcp131/upstream/core/homeassistant/components/"
    "homeassistant/exposed_entities.py"
)


def core_default_exposed(entity_id: str, entry: SimpleNamespace) -> bool:
    """The pinned domain branch of Core's ``_is_default_exposed``."""

    if entry and (entry.entity_category is not None or entry.hidden_by is not None):
        return False
    return entity_id.split(".", 1)[0] in CORE_DEFAULT_EXPOSED_DOMAINS


def obligation(**changes) -> SimpleNamespace:
    """A target-independent automation obligation-ledger terminal."""

    values = {
        "source_type": "automation",
        "coverage_failure_authority": False,
        "obligation_kind": "template_state_read",
        "relation": "template_entity",
        "outcome": "bounded_semantic_opaque",
        "exact_entity_ids": (),
        "target_selector_scope": "target_capable",
        "possible_entity_domains": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def with_coverage(snapshot, source_type: str, item) -> SimpleNamespace:
    kept = tuple(c for c in snapshot.coverage if c.source_type != source_type)
    snapshot.coverage = kept + ((item,) if item is not None else ())
    return snapshot


def protection(created: dict) -> dict:
    return child_disclosures(created)[0]["protection"]


class ReviewerReproductions(AvailabilityExposureServiceTestCase):
    """The reviewer's six probes, verbatim in intent (review1/reviewer_probes.py)."""

    async def test_enable_default_exposure_follows_owner_stored_setting_rule(self):
        self.add_entity("cover.entry", disabled_by="user", original_device_class="garage")
        self.ws.expose_new_settings["conversation"] = True
        entry = SimpleNamespace(entity_category=None, hidden_by=None)
        self.assertTrue(core_default_exposed("cover.entry", entry))
        if CORE_SOURCE.exists():
            # Local evidence only: the pinned copy still matches the source.
            text = CORE_SOURCE.read_text(encoding="utf-8")
            for domain in CORE_DEFAULT_EXPOSED_DOMAINS:
                self.assertIn(f'    "{domain}",\n', text)
            self.assertIn("if domain in DEFAULT_EXPOSED_DOMAINS:\n            return True", text)
        created = await self.create(operation="enable", entity_ids=["cover.entry"])
        # OD-5 incorporates design section 6.2: only stored true escalates enable.
        self.assertEqual(child_disclosures(created)[0]["stored_exposure_true"], [])
        self.assertEqual(policy(created)[0], "standard_admin")

    async def test_disable_with_out_of_bounds_dependency_evidence_requires_elevated(self):
        self.add_entity("binary_sensor.entry")
        self.snapshot.obligation_overflow_count = 1
        self.snapshot.coverage = (
            coverage_item(
                "automation", "partial", failed_item_count=1,
                obligation_ledger_failed_item_count=1,
            ),
        )
        created = await self.create(operation="disable", entity_ids=["binary_sensor.entry"])
        self.assertEqual(policy(created)[0], "elevated_admin")
        reasons = protection(created)["dependency_reasons"]
        self.assertIn("dependency_evidence_incomplete:obligation_ledger_truncated", reasons)
        self.assertIn("dependency_evidence_incomplete:automation_inventory_incomplete", reasons)

    async def test_service_refuses_without_admission_before_reads_or_plan(self):
        self.add_entity("light.porch")
        core = CoreRuntime()
        self.runtime.core_runtime = core
        self.assertIsNone(core.acquire((AVAILABILITY,)))
        with self.assertRaises(GovernanceError) as raised:
            await self.create(operation="disable", entity_ids=["light.porch"])
        self.assertEqual(raised.exception.details["reason_code"], PENDING)
        self.assertEqual(self.ws.commands, [])
        self.assertEqual(self.snapshot_reads, [])
        self.assertEqual(self.repository.list(), [])

    async def test_valve_exposure_requires_elevated(self):
        self.add_entity("valve.main")
        created = await self.create(
            operation="expose", entity_ids=["valve.main"], assistant="conversation"
        )
        self.assertEqual(policy(created)[0], "elevated_admin")
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(len(self.ws.writes()), 1)

    async def signed_core(self, *capability_ids: str):
        authority = admission.SignedTestRegistryAdmissionTests()
        await authority.asyncSetUp()
        self.addAsyncCleanup(authority.asyncTearDown)
        self.addCleanup(authority.doCleanups)
        await authority.publish(
            authority.with_references(*capability_ids) if capability_ids else None
        )
        self.clock[0] = datetime.now(timezone.utc)
        self.runtime.core_runtime = authority.core
        return authority

    async def test_signed_fixture_core_and_real_f3_execute_once(self):
        authority = await self.signed_core(AVAILABILITY, EXPOSURE)
        for operation, entity, extra in (
            ("disable", "light.a", {}),
            ("expose", "light.b", {"assistant": "conversation"}),
        ):
            with self.subTest(operation=operation):
                self.add_entity(entity)
                created = await self.create(operation=operation, entity_ids=[entity], **extra)
                await self.approve(created)
                before = len(self.ws.writes())
                result = await self.apply(created)
                self.assertEqual(result["task_state"], "succeeded_verified")
                self.assertEqual(len(self.ws.writes()) - before, 1)
        self.assertEqual(authority.core.health_snapshot()["fallback_count"], 0)

    async def test_unadmitted_f3_apply_and_recovery_never_write(self):
        # After M1 an unadmitted Core can no longer create these plans, so the
        # apply/recovery half of the probe starts from plans created while
        # admitted, then runs them against the reviewer's real signed Core
        # published with the shipped entry (no 069B references).
        authority = await self.signed_core()
        self.runtime.core_runtime = self.core  # Admitting, for planning only.
        plans = []
        for operation, entity, extra in (
            ("disable", "light.c", {}),
            ("expose", "light.d", {"assistant": "conversation"}),
        ):
            self.add_entity(entity)
            created = await self.create(operation=operation, entity_ids=[entity], **extra)
            await self.approve(created)
            plans.append(created)
        self.runtime.core_runtime = authority.core
        self.assertFalse(authority.core.route_status((AVAILABILITY,))["available"])
        self.assertFalse(authority.core.route_status((EXPOSURE,))["available"])
        for created in plans:
            # HAMCP-165: the unadmitted first apply refuses at entry, before
            # any execution-state persistence.
            with self.assertRaises(GovernanceError) as refused:
                await self.apply(created)
            self.assertEqual(refused.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
            self.assertEqual(refused.exception.details["reason_code"], PENDING)
            self.assertIsNone(self.service.task_repository.get_for_plan(created["plan_id"]))
            self.assertEqual(self.ws.writes(), [])
            await self.runtime.recover_once("reviewer_restart")
            self.assertEqual(self.ws.writes(), [])
        with self.assertRaises(GovernanceError) as raised:
            await self.create(operation="disable", entity_ids=["light.c"])
        self.assertEqual(raised.exception.details["reason_code"], PENDING)
        self.assertEqual(len(self.repository.list()), 2)


class EvidencePredicateTests(unittest.TestCase):
    """H1: ``dependency_evidence_gaps`` is the single completeness decision."""

    def gaps(self, snapshot, entity_id="light.porch"):
        return planning.dependency_evidence_gaps(snapshot, entity_id)

    def test_complete_snapshot_has_no_gap(self):
        self.assertEqual(self.gaps(exposure_snapshot()), [])

    def test_structural_script_partiality_is_disclosed_not_a_gap(self):
        snapshot = exposure_snapshot()
        self.assertEqual(snapshot.script_diagnostics.coverage.completeness, "partial")
        self.assertEqual(self.gaps(snapshot), [])

    def test_proven_neutral_and_excluded_obligations_are_not_gaps(self):
        snapshot = exposure_snapshot()
        snapshot.obligations = (
            obligation(target_selector_scope="dependency_neutral"),
            obligation(target_selector_scope="closed_finite_candidates",
                       exact_entity_ids=("light.other",)),
            obligation(target_selector_scope="closed_entity_domains",
                       possible_entity_domains=("switch",)),
            obligation(source_type="script"),
        )
        self.assertEqual(self.gaps(snapshot), [])

    def test_each_cause_is_a_gap(self):
        def degrade(change):
            snapshot = exposure_snapshot()
            change(snapshot)
            return snapshot

        def retired_scripts(s):
            diagnostics = script_diagnostics()
            s.script_diagnostics = replace(diagnostics, authority_current=lambda: False)

        cases = {
            "dependency_index_unavailable": None,
            "home_assistant_version_unsupported": degrade(
                lambda s: setattr(s, "home_assistant_version", "1999.1.0")),
            "home_assistant_version_unavailable": degrade(
                lambda s: setattr(s, "home_assistant_version_status", "unavailable")),
            "obligation_ledger_model_unsupported": degrade(
                lambda s: setattr(s, "obligation_ledger_model", None)),
            "automation_inventory_incomplete": degrade(
                lambda s: with_coverage(s, "automation", coverage_item("automation", "partial"))),
            "blueprint_inventory_incomplete": degrade(
                lambda s: with_coverage(s, "blueprint", None)),
            "dependency_index_payload_truncated": degrade(
                lambda s: with_coverage(s, "automation", coverage_item(
                    "automation", obligation_ledger_completeness="complete",
                    warnings=["Automation dependency evidence exceeded the bounded index payload."],
                ))),
            "automation_read_failures": degrade(
                lambda s: setattr(s, "automation_read_failures", (object(),))),
            "obligation_ledger_truncated": degrade(
                lambda s: setattr(s, "obligation_overflow_count", 1)),
            "dynamic_references_truncated": degrade(
                lambda s: setattr(s, "dynamic_reference_overflow_count", 3)),
            "obligation_coverage_failure": degrade(
                lambda s: setattr(s, "obligations", (obligation(coverage_failure_authority=True),))),
            "unresolved_dynamic_consumer": degrade(
                lambda s: setattr(s, "obligations", (obligation(),))),
            "script_evidence_unavailable": degrade(
                lambda s: setattr(s, "script_diagnostics", None)),
            "script_read_failures": degrade(
                lambda s: setattr(s, "script_diagnostics", script_diagnostics(failed_item_count=2))),
            "script_inventory_bounded": degrade(
                lambda s: setattr(s, "script_diagnostics", script_diagnostics(warnings=[
                    "Inventory was bounded; the exact omitted script count is unknown."]))),
        }
        for gap, snapshot in cases.items():
            with self.subTest(gap=gap):
                self.assertIn(gap, self.gaps(snapshot))
        with self.subTest(gap="script provider authority retired"):
            self.assertIn("script_evidence_unavailable", self.gaps(degrade(retired_scripts)))
        with self.subTest(gap="script coverage unavailable"):
            self.assertIn("script_evidence_unavailable", self.gaps(degrade(
                lambda s: setattr(s, "script_diagnostics", script_diagnostics("unavailable")))))
        with self.subTest(gap="automation coverage missing"):
            self.assertIn("automation_inventory_incomplete", self.gaps(degrade(
                lambda s: with_coverage(s, "automation", None))))
        with self.subTest(gap="automation ledger failures under complete"):
            self.assertIn("automation_inventory_incomplete", self.gaps(degrade(
                lambda s: with_coverage(s, "automation", coverage_item(
                    "automation", obligation_ledger_failed_item_count=1)))))

    def test_consumer_evidence_carries_every_gap_as_a_protected_reason(self):
        snapshot = exposure_snapshot()
        snapshot.obligation_overflow_count = 1
        snapshot.script_diagnostics = None
        evidence = planning._consumer_evidence(snapshot, "light.porch")
        self.assertIs(evidence["evidence_complete"], False)
        self.assertEqual(
            evidence["evidence_gaps"],
            ["obligation_ledger_truncated", "script_evidence_unavailable"],
        )
        self.assertEqual(
            evidence["dependency_reasons"],
            [planning.INCOMPLETE_PREFIX + gap for gap in evidence["evidence_gaps"]],
        )
        complete = planning._consumer_evidence(exposure_snapshot(), "light.porch")
        self.assertIs(complete["evidence_complete"], True)
        self.assertEqual(complete["dependency_reasons"], [])


class IncompleteEvidenceClassificationTests(AvailabilityExposureServiceTestCase):
    """H1 end to end: every cause is elevated, never standard, on every path."""

    def assert_incomplete_elevated(self, created: dict, gap: str) -> None:
        self.assertEqual(policy(created)[0], "elevated_admin")
        self.assertEqual(policy(created)[1], "safety_critical")
        disclosure = child_disclosures(created)[0]
        self.assertIn(
            planning.INCOMPLETE_PREFIX + gap, disclosure["protection"]["dependency_reasons"]
        )
        self.assertIs(disclosure["protection"]["protected"], True)
        self.assertIs(disclosure["consumers"]["evidence_complete"], False)
        self.assertIn(gap, disclosure["consumers"]["evidence_gaps"])
        self.assertTrue(
            any(INCOMPLETE in text for text in disclosure["consequence_statements"]),
            disclosure["consequence_statements"],
        )

    async def test_complete_evidence_for_an_unprotected_entity_is_standard(self):
        self.add_entity("light.porch")
        created = await self.create(operation="disable", entity_ids=["light.porch"])
        self.assertEqual(policy(created)[0], "standard_admin")
        disclosure = child_disclosures(created)[0]
        self.assertIs(disclosure["consumers"]["evidence_complete"], True)
        self.assertFalse(
            any(INCOMPLETE in text for text in disclosure["consequence_statements"])
        )

    async def test_each_incompleteness_cause_is_elevated(self):
        causes = {
            "obligation_ledger_truncated": lambda s: setattr(s, "obligation_overflow_count", 1),
            "automation_inventory_incomplete": lambda s: with_coverage(
                s, "automation", coverage_item("automation", "partial")),
            "dependency_index_payload_truncated": lambda s: with_coverage(
                s, "automation", coverage_item("automation", warnings=[
                    "Automation dependency evidence exceeded the bounded index payload."])),
            "home_assistant_version_unavailable": lambda s: setattr(
                s, "home_assistant_version_status", "unavailable"),
            "unresolved_dynamic_consumer": lambda s: setattr(s, "obligations", (obligation(),)),
            "automation_read_failures": lambda s: setattr(
                s, "automation_read_failures", (object(),)),
            "script_read_failures": lambda s: setattr(
                s, "script_diagnostics", script_diagnostics(failed_item_count=1)),
            "script_evidence_unavailable": lambda s: setattr(s, "script_diagnostics", None),
        }
        for index, (gap, change) in enumerate(causes.items()):
            with self.subTest(gap=gap):
                entity_id = f"light.cause_{index}"
                self.add_entity(entity_id)
                self.snapshot = exposure_snapshot()
                change(self.snapshot)
                created = await self.create(operation="disable", entity_ids=[entity_id])
                self.assert_incomplete_elevated(created, gap)
        with self.subTest(gap="dependency_index_unavailable"):
            self.add_entity("light.cause_unavailable")

            async def unavailable(*, fenced):
                raise EntityMetadataEvidenceUnavailable("dependency_snapshot_unavailable")

            self.service.entity_availability_exposure_snapshot_reader = unavailable
            created = await self.create(operation="disable", entity_ids=["light.cause_unavailable"])
            self.assert_incomplete_elevated(created, "dependency_index_unavailable")
            self.assertEqual(child_disclosures(created)[0]["consumers"]["status"], "unavailable")

    async def test_overflow_without_read_failures_is_elevated(self):
        # The original defect: only read failures were treated as incomplete.
        self.add_entity("light.porch")
        self.snapshot.obligation_overflow_count = 5
        self.assertEqual(self.snapshot.automation_read_failures, ())
        created = await self.create(operation="disable", entity_ids=["light.porch"])
        self.assert_incomplete_elevated(created, "obligation_ledger_truncated")

    async def test_the_approval_for_an_incomplete_evidence_disable_is_elevated(self):
        self.add_entity("light.porch")
        self.snapshot.dynamic_reference_overflow_count = 1
        created = await self.create(operation="disable", entity_ids=["light.porch"])
        granted = self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertEqual(granted["policy_class"], "elevated_admin", granted)
        self.assertIs(granted["external_approval_required"], True)

    async def test_evidence_becoming_incomplete_after_approval_is_refused(self):
        self.add_entity("light.porch")
        created = await self.create(operation="disable", entity_ids=["light.porch"])
        self.assertEqual(policy(created)[0], "standard_admin")
        await self.approve(created)
        self.snapshot = exposure_snapshot()
        self.snapshot.obligation_overflow_count = 1
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertIn("dependency_evidence_changed", self.child_codes(result["task_id"]))
        self.assertEqual(self.ws.writes(), [])

    async def test_evidence_becoming_complete_after_approval_is_refused(self):
        self.add_entity("light.porch")
        self.snapshot.script_diagnostics = None
        created = await self.create(operation="disable", entity_ids=["light.porch"])
        self.assertEqual(policy(created)[0], "elevated_admin")
        await self.approve(created)
        self.snapshot = exposure_snapshot()
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertIn("dependency_evidence_changed", self.child_codes(result["task_id"]))
        self.assertEqual(self.ws.writes(), [])

    async def test_incomplete_evidence_disable_still_executes_once_when_approved(self):
        # Protected is actionable with the elevated approval (tests/AGENTS.md).
        self.add_entity("light.porch")
        self.snapshot.obligation_overflow_count = 1
        created = await self.create(operation="disable", entity_ids=["light.porch"])
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(len(self.ws.writes()), 1)

    async def test_the_rollback_disable_uses_the_same_predicate(self):
        self.ws.add_config_entry("entry_a")
        self.add_entity("light.a", disabled_by="user", config_entry_id="entry_a")
        created = await self.create(operation="enable", entity_ids=["light.a"])
        await self.approve(created)
        self.assertEqual((await self.apply(created))["status"], "applied")
        self.snapshot.obligation_overflow_count = 1
        rollback = await self.service.rollback_change(created["plan_id"], created["plan_hash"])
        self.assertEqual(rollback["rollback_operation"], "disable")
        plan = self.service.get_plan(rollback["rollback_plan_id"])
        self.assertEqual(policy(plan)[0], "elevated_admin")
        disclosure = child_disclosures(plan)[0]
        self.assertIn(
            planning.INCOMPLETE_PREFIX + "obligation_ledger_truncated",
            disclosure["protection"]["dependency_reasons"],
        )

    async def test_a_feeding_profile_still_escalates_with_complete_evidence(self):
        self.add_entity("binary_sensor.door")
        self.snapshot = exposure_snapshot(
            findings=(finding("binary_sensor.door", "alarm"),),
            profiles=(automation_profile("alarm", "safety_critical"),),
        )
        created = await self.create(operation="disable", entity_ids=["binary_sensor.door"])
        self.assertEqual(policy(created)[0], "elevated_admin")
        self.assertEqual(
            protection(created)["dependency_reasons"], ["feeds_protected_automation:alarm"]
        )

    async def test_script_consumers_are_disclosed(self):
        self.add_entity("light.porch")
        script_finding = SimpleNamespace(
            target_entity_id="light.porch", source_type="script", source_id="evening",
            source_entity_id="script.evening", relation="action_target",
            config_path="sequence[0].target.entity_id",
        )
        self.snapshot.script_diagnostics = script_diagnostics(findings=(script_finding,))
        created = await self.create(operation="disable", entity_ids=["light.porch"])
        consumers = child_disclosures(created)[0]["consumers"]
        self.assertEqual(
            [(c["source_type"], c["source_id"]) for c in consumers["items"]],
            [("script", "evening")],
        )


class ServiceAdmissionGateTests(AvailabilityExposureServiceTestCase):
    """M1: the service gate runs first on every entry point."""

    OPERATIONS = (
        ({"operation": "disable", "entity_ids": ["light.porch"]},
         "entity_registry_availability", AVAILABILITY),
        ({"operation": "enable", "entity_ids": ["light.off"]},
         "entity_registry_availability", AVAILABILITY),
        ({"operation": "expose", "entity_ids": ["light.porch"], "assistant": "conversation"},
         "entity_assistant_exposure", EXPOSURE),
        ({"operation": "unexpose", "entity_ids": ["light.porch"],
          "assistant": "cloud.google_assistant"},
         "entity_assistant_exposure", EXPOSURE),
    )

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add_entity("light.porch")
        self.add_entity("light.off", disabled_by="user")

    def assert_pending(self, raised, resource_type: str, profile: str) -> None:
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        details = raised.exception.details
        self.assertEqual(details["reason"], PENDING)
        self.assertEqual(details["reason_code"], PENDING)
        self.assertEqual(details["resource_type"], resource_type)
        self.assertEqual(details["core_capability"], profile)
        self.assertIs(details["plan_created"], False)
        self.assertIs(details["provider_dispatch_occurred"], False)

    def assert_nothing_read_or_stored(self) -> None:
        self.assertEqual(self.ws.commands, [])
        self.assertEqual(self.snapshot_reads, [])
        self.assertEqual(self.repository.list(), [])

    async def test_unadmitted_core_refuses_every_operation_with_zero_reads(self):
        self.core.withdraw()
        for arguments, resource_type, profile in self.OPERATIONS:
            with self.subTest(operation=arguments["operation"]):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_entity_availability_exposure_plan(
                        deepcopy(arguments)
                    )
                self.assert_pending(raised, resource_type, profile)
                self.assert_nothing_read_or_stored()
        self.assertEqual(self.core.planning_leases, [])

    async def test_missing_core_or_runtime_fails_closed(self):
        arguments, resource_type, profile = self.OPERATIONS[0]
        for label, setup in (
            ("no core runtime", lambda: setattr(self.runtime, "core_runtime", None)),
            ("no f3 runtime", lambda: setattr(self.service, "f3_runtime", None)),
        ):
            with self.subTest(label):
                setup()
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_entity_availability_exposure_plan(
                        deepcopy(arguments)
                    )
                self.assert_pending(raised, resource_type, profile)
                self.assert_nothing_read_or_stored()

    async def test_core_that_cannot_report_status_fails_closed(self):
        class Broken(AdmittingCore):
            def route_status(self, capability_ids, **_):
                raise RuntimeError("synthetic")

        self.runtime.core_runtime = Broken()
        arguments, resource_type, profile = self.OPERATIONS[2]
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_entity_availability_exposure_plan(deepcopy(arguments))
        self.assert_pending(raised, resource_type, profile)
        self.assert_nothing_read_or_stored()

    async def test_each_profile_admits_only_its_own_resource_type(self):
        for admitted, refused in ((AVAILABILITY, EXPOSURE), (EXPOSURE, AVAILABILITY)):
            with self.subTest(admitted=admitted):
                self.core.admitted = {admitted}
                for arguments, resource_type, profile in self.OPERATIONS:
                    if profile != refused:
                        continue
                    with self.assertRaises(GovernanceError) as raised:
                        await self.service.create_entity_availability_exposure_plan(
                            deepcopy(arguments)
                        )
                    self.assert_pending(raised, resource_type, profile)
                    self.assert_nothing_read_or_stored()
        self.core.admitted = {AVAILABILITY}
        created = await self.create(operation="disable", entity_ids=["light.porch"])
        self.assertEqual(len(self.repository.list()), 1)
        self.assertTrue(created["plan_id"])

    async def test_admitted_planning_holds_an_exact_target_lease(self):
        await self.create(operation="disable", entity_ids=["light.porch"])
        self.assertEqual(
            self.core.planning_leases,
            [(
                ("core.dependency_helper_planning", AVAILABILITY),
                {"target_type": "entity_registry_availability", "target_id": "light.porch"},
            )],
        )
        self.assertGreater(self.core.revalidations, 0)

    async def test_admission_withdrawn_during_planning_stores_nothing(self):
        async def withdrawing_reader(*, fenced):
            self.snapshot_reads.append(fenced)
            self.core.withdraw()
            return self.snapshot

        self.service.entity_availability_exposure_snapshot_reader = withdrawing_reader
        with self.assertRaises(GovernanceError) as raised:
            await self.create(operation="disable", entity_ids=["light.porch"])
        self.assertIs(raised.exception.details["plan_created"], False)
        self.assertIs(raised.exception.details["provider_dispatch_occurred"], False)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.ws.writes(), [])

    async def test_admission_withdrawn_at_the_persist_boundary_stores_nothing(self):
        original = self.service._create_configuration_plan

        async def withdraw_then_create(**kwargs):
            self.core.withdraw()
            return await original(**kwargs)

        self.service._create_configuration_plan = withdraw_then_create
        with self.assertRaises(GovernanceError):
            await self.create(operation="expose", entity_ids=["light.porch"],
                              assistant="conversation")
        self.assertEqual(self.repository.list(), [])

    async def test_internal_builder_refuses_without_admission_or_lease(self):
        request = planning.validate_request(
            {"operation": "disable", "entity_ids": ["light.porch"]}
        )
        prepared = await planning.prepare(
            self.service.entity_availability_exposure_gateway,
            self.service.entity_availability_exposure_snapshot_reader,
            request,
        )
        with self.assertRaises(GovernanceError) as raised:
            await self.service._create_availability_exposure_plan(prepared)
        self.assertEqual(raised.exception.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED)
        self.core.withdraw()
        with self.assertRaises(GovernanceError) as raised:
            await self.service._create_availability_exposure_plan(
                prepared, _before_persist=lambda: None
            )
        self.assert_pending(raised, "entity_registry_availability", AVAILABILITY)
        self.assertEqual(self.repository.list(), [])

    async def test_generic_configuration_plan_cannot_carry_these_types(self):
        for resource_type in ("entity_registry_availability", "entity_assistant_exposure"):
            with self.subTest(resource_type=resource_type):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_configuration_plan(
                        title="t", description="d",
                        operations=[{
                            "operation_id": "x", "resource_type": resource_type,
                            "action": "update", "target_id": "light.porch",
                            "proposed_config": {"disabled_by": "user"},
                        }],
                    )
                self.assertEqual(
                    raised.exception.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED
                )
                self.assert_nothing_read_or_stored()

    async def applied_enable(self) -> dict:
        self.ws.add_config_entry("entry_a")
        self.add_entity("light.a", disabled_by="user", config_entry_id="entry_a")
        created = await self.create(operation="enable", entity_ids=["light.a"])
        await self.approve(created)
        self.assertEqual((await self.apply(created))["status"], "applied")
        return created

    async def test_inverse_refuses_when_admission_is_withdrawn(self):
        created = await self.applied_enable()
        commands, plans = len(self.ws.commands), len(self.repository.list())
        self.core.withdraw()
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"], created["plan_hash"])
        self.assertIs(raised.exception.details["plan_created"], False)
        self.assertEqual(len(self.ws.commands), commands)
        self.assertEqual(len(self.repository.list()), plans)

    async def test_inverse_without_core_refuses_before_reads(self):
        created = await self.applied_enable()
        commands, plans = len(self.ws.commands), len(self.repository.list())
        self.runtime.core_runtime = None
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"], created["plan_hash"])
        self.assert_pending(raised, "entity_registry_availability", AVAILABILITY)
        self.assertEqual(len(self.ws.commands), commands)
        self.assertEqual(len(self.repository.list()), plans)

    async def test_admitted_inverse_still_plans(self):
        created = await self.applied_enable()
        rollback = await self.service.rollback_change(created["plan_id"], created["plan_hash"])
        self.assertEqual(rollback["status"], "rollback_plan_created")

    async def test_existing_plan_after_withdrawal_never_writes_across_restart(self):
        created = await self.create(operation="disable", entity_ids=["light.porch"])
        await self.approve(created)
        self.core.withdraw()
        # HAMCP-165: refused at entry, before any execution-state persistence.
        with self.assertRaises(GovernanceError) as refused:
            await self.apply(created)
        self.assertEqual(refused.exception.details["reason_code"], PENDING)
        self.assertIsNone(self.service.task_repository.get_for_plan(created["plan_id"]))
        await self.restart_runtime()
        await self.runtime.recover_once("synthetic_restart")
        self.assertEqual(self.ws.writes(), [])

    async def test_nested_under_a_gateway_lease_on_the_real_core(self):
        authority = admission.SignedTestRegistryAdmissionTests()
        await authority.asyncSetUp()
        self.addAsyncCleanup(authority.asyncTearDown)
        self.addCleanup(authority.doCleanups)
        await authority.publish(authority.with_references(AVAILABILITY, EXPOSURE))
        self.clock[0] = datetime.now(timezone.utc)
        self.runtime.core_runtime = authority.core
        arguments = {"operation": "disable", "entity_ids": ["light.porch"]}
        # The MCP gateway holds its own exact-target lease around the tool.
        outer = authority.core.acquire(
            admission.static_tool_requirements(admission.TOOL, arguments),
            target=admission.static_tool_target(admission.TOOL, arguments),
        )
        commits = authority.core.consume(outer)
        try:
            created = await self.service.create_entity_availability_exposure_plan(arguments)
        finally:
            authority.core.finish(commits)
        self.assertEqual(len(self.repository.list()), 1)
        self.assertTrue(created["plan_id"])


if __name__ == "__main__":
    unittest.main()
