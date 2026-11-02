"""HAMCP-276: F3 dispatch authority on the real Core runtime.

Live, an approved helper-options plan refused at ``dispatch_authority`` with
``dispatch_authority_unavailable`` while seq 15 admitted its profile: the plan
was created before a Core authority retirement and restoration, so the real
``CoreRuntime.acquire`` refused it as stale (``_plan_is_current_locked``) and
the F3 wrappers reported the generic code.  Existing helper-options tests used
an admitting Core double that never applies the stale-plan cutoff.

Everything here runs the real ``CoreRuntime`` under a TEST-signed registry,
the real F3 runtime, registry, wrappers and lease code, and synthetic Home
Assistant transports.  Nothing reaches a live system.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import unittest

from tests.hamcp232_helper_options_fixtures import (
    FILTER_LOWPASS,
    HelperOptionsServiceTestCase,
    TEMPLATE_SENSOR,
)
import tests.test_core_2026_10_device_registry as october
from tests.core_registry_fixtures import NOW
from tests.support.core_gate import reconcile_through_gate

from ha_mcp_engineering.errors import ErrorCode, GovernanceError
from ha_mcp_engineering.f3.executor import DispatchAuthorityRefused
from ha_mcp_engineering.f3_runtime import pre_dispatch_reasons as reasons
from ha_mcp_engineering.f3_runtime.runtime import (
    _CoreDispatchAuthorityGuard,
    _CoreVerificationAdapter,
)
from ha_mcp_engineering.governance import helper_options as contract
from ha_mcp_engineering.ha_core_readmission.profiles import (
    CORE_RUNTIME_CAPABILITY_PROFILES,
)
from ha_mcp_engineering.ha_core_readmission.registry_models import CoreRegistryEnvelope
from ha_mcp_engineering.ha_core_readmission.routes import (
    CORE_AUTHORITY_CHANGED_SINCE_PLAN,
    F3_ENTRY_ADMISSION_PROFILES,
    f3_requirements,
)


KEYS = ("capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")
COMPILED = frozenset(p.capability_id for p in CORE_RUNTIME_CAPABILITY_PROFILES)
GENERIC = "dispatch_authority_unavailable"
PENDING = "pending_signed_registry_admission"
STALE = CORE_AUTHORITY_CHANGED_SINCE_PLAN
VERIFICATION = "core.f3_mutation_verification"
OPERATIONAL_FAMILIES = {"operational_administration", "dashboard_update"}
TEMPLATE = "sensor.double_power"
KILOWATT = {"entity_id": TEMPLATE, "set": {"unit_of_measurement": "kW"}}

# Compiled Core profiles that no F3 identity requires, each with the reason
# F3 does not activate it.  Adding a compiled profile forces a decision here.
NOT_F3 = {
    "core.basic_rest_read": "read",
    "core.basic_websocket_read": "read",
    "core.state_service_discovery": "read",
    "core.non_device_registry_read": "read",
    "core.direct_device_registry_read": "read",
    "core.delegated_device_effective_area": "read",
    "core.direct_entity_state_read": "read",
    "core.automation_configuration_read": "read",
    "core.automation_trace_read": "read",
    "core.integration_inspection_metadata_read": "read",
    "core.automation_baseline_metadata_read": "read",
    "core.script_trace_read": "read",
    "core.template_semantics": "planning",
    "core.configuration_validation": "planning",
    "core.dependency_helper_planning": "planning",
    "core.governance_observability": "observability",
    # control_fan / control_power are direct typed tools, not F3 plans.
    "core.typed_fan_operation": "direct_typed_tool",
    "core.typed_power_operation": "direct_typed_tool",
}


def f3_identity(entry) -> SimpleNamespace:
    """The exact (capability, operation) pair the registry binds."""

    operation = (
        entry.plan_operation
        if entry.operation_family in OPERATIONAL_FAMILIES
        else entry.capability_id
    )
    return SimpleNamespace(
        capability_id=entry.capability_id,
        capability_identity=entry.capability_id,
        operation=operation,
        target={"target_type": entry.target_type, "target_id": "synthetic_target"},
    )


def operation_profiles(prepared) -> frozenset[str]:
    """The identity's own operation profiles (all but shared verification)."""

    return frozenset(f3_requirements(prepared)) - {VERIFICATION}


class SignedCore(unittest.IsolatedAsyncioTestCase):
    """The real ``CoreRuntime`` behind a TEST-signed registry (not seq 15)."""

    asyncSetUp = october.OctoberSignedSelectionTests.asyncSetUp

    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    def entry_without(self, *withheld: str) -> dict:
        entry = deepcopy(self.entry)
        entry["capabilities"] = [
            {key: profile.to_mapping()[key] for key in KEYS}
            for profile in CORE_RUNTIME_CAPABILITY_PROFILES
            if profile.capability_id not in withheld
        ]
        return entry

    async def admit(self, *withheld: str) -> dict:
        """Publish the next signed envelope in one authenticated chain."""

        chain = getattr(self, "chain", [])
        sequence = len(chain) + 1
        envelope = json.loads(self.signer.raw(
            sequence=sequence,
            previous_registry_sha256=(
                CoreRegistryEnvelope.from_mapping(chain[-1]).content_digest if chain else None
            ),
            generated_at=NOW - timedelta(minutes=1) + timedelta(seconds=sequence),
            entries=[self.entry_without(*withheld)],
        ))
        self.chain = [*chain, envelope]
        self.raw = self.signer.journal_raw(envelopes=self.chain)
        self.assertTrue(await self.registry.refresh())
        return await reconcile_through_gate(self.core)

    async def retire_and_restore(self) -> dict:
        """The live 14:37:05Z retirement and 14:37:26Z restoration."""

        self.core.request_reconciliation(connection_changed=True)
        self.assertFalse(self.core.route_status((contract.PROFILE,))["available"])
        return await reconcile_through_gate(self.core, "failure_retry")


async def signed_core(case: unittest.IsolatedAsyncioTestCase, *withheld: str) -> SignedCore:
    authority = SignedCore()
    await authority.asyncSetUp()
    case.addAsyncCleanup(authority.asyncTearDown)
    case.addCleanup(authority.doCleanups)
    health = await authority.admit(*withheld)
    case.assertEqual(health["compatible_count"], len(COMPILED) - len(withheld))
    return authority


class RealCoreServiceTests(HelperOptionsServiceTestCase):
    """Real service and F3 runtime on the real Core runtime."""

    async def use_signed_core(self, *withheld: str) -> SignedCore:
        authority = await signed_core(self, *withheld)
        # Plans are created now, after the admission above.
        self.clock[0] = datetime.now(timezone.utc)
        self.runtime.core_runtime = authority.core
        return authority

    def refusal(self, result: dict) -> dict:
        (child,) = self.child_records(result["task_id"])
        return reasons.child_reason(
            self.runtime.children.declarations_for_task(result["task_id"])[0], child
        )

    def assert_stale_refusal(self, authority: SignedCore, result: dict) -> None:
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertEqual(self.child_codes(result["task_id"]), {STALE})
        reason = self.refusal(result)
        self.assertEqual(reason["error_code"], STALE)
        self.assertEqual(reason["check"], "core_authority_freshness")
        self.assertIn("Create and approve a fresh plan", reason["detail"])
        counters = authority.core.health_snapshot()["counters"]
        self.assertEqual(counters["stale_plan_rejections"], 1)

    async def retire_and_restore(self, authority: SignedCore) -> dict:
        restored = await authority.retire_and_restore()
        # The service clock is frozen; later plans postdate the restoration.
        self.clock[0] = datetime.now(timezone.utc)
        return restored

    async def approved_availability(self) -> dict:
        # ``create``/``approved`` here are the configuration-delete helpers.
        created = await self.service.create_entity_availability_exposure_plan(
            {"operation": "disable", "entity_ids": ["light.porch"]}
        )
        await self.approve(created)
        return created

    # -- reproduction --------------------------------------------------------

    async def test_reproduction_plan_predating_retirement_is_refused_truthfully(self):
        authority = await self.use_signed_core()
        self.add_template_sensor()
        created = await self.approved_options(KILOWATT)
        restored = await self.retire_and_restore(authority)
        # Restored at the full admitted set, exactly as live (31/0).
        self.assertEqual(restored["compatible_count"], len(COMPILED))
        self.assertTrue(authority.core.route_status((contract.PROFILE,))["available"])
        calls = self.ha_calls()
        result = await self.apply(created)
        self.assert_stale_refusal(authority, result)
        self.assertEqual(self.ha_calls(), calls, "no Home Assistant call at all")
        self.assertEqual(self.options_rest.submits(), [])
        self.assertEqual(self.options_rest.visible("templateentry01"), TEMPLATE_SENSOR)
        # The same change, planned under current authority, applies once.
        fresh = await self.approved_options(KILOWATT)
        applied = await self.apply(fresh)
        self.assertEqual(applied["task_state"], "succeeded_verified", applied)
        self.assertEqual(len(self.options_rest.submits()), 1)
        self.assertEqual(self.options_rest.visible("templateentry01")["unit_of_measurement"], "kW")

    async def test_real_lease_refuses_the_restored_plan_for_staleness_alone(self):
        # Pins the mechanism: the real lease is refused for staleness alone.
        authority = await self.use_signed_core()
        self.add_template_sensor()
        created = await self.approved_options(KILOWATT)
        await self.retire_and_restore(authority)
        plan = self.service.get_plan(created["plan_id"])
        self.assertFalse(authority.core.plan_is_current(plan["created_at"]))
        prepared = SimpleNamespace(
            capability_id=contract.CAPABILITY_IDENTITY,
            operation=contract.CAPABILITY_IDENTITY,
            target={"target_type": contract.RESOURCE_TYPE, "target_id": "templateentry01"},
        )
        self.assertIsNone(
            authority.core.acquire_f3(prepared, plan_created_at=plan["created_at"])
        )
        self.assertIsNotNone(authority.core.acquire_f3(prepared))

    # -- helper options end to end ------------------------------------------

    async def test_helper_options_change_verifies_rollback_restores_and_no_op_refused(self):
        authority = await self.use_signed_core()
        self.add_filter()
        created = await self.approved_options(
            {"entity_id": "sensor.filtered_power", "set": {"time_constant": 20}}
        )
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertIn("exact_readback_verified", self.child_codes(result["task_id"]))
        self.assertEqual(len(self.options_rest.submits()), 1)
        self.assertEqual(self.options_rest.visible("filterentry01")["time_constant"], 20)

        rollback = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(rollback["status"], "rollback_plan_created", rollback)
        self.assertFalse(rollback["provider_dispatch_occurred"])
        restore = self.service.get_plan(rollback["rollback_plan_id"])
        await self.approve(restore)
        restored = await self.apply(restore)
        self.assertEqual(restored["task_state"], "succeeded_verified", restored)
        self.assertEqual(len(self.options_rest.submits()), 2)
        self.assertEqual(self.options_rest.visible("filterentry01"), FILTER_LOWPASS)

        submits = len(self.options_rest.submits())
        with self.assertRaises(GovernanceError):
            await self.create_options(
                {"entity_id": "sensor.filtered_power",
                 "set": {"time_constant": FILTER_LOWPASS["time_constant"]}}
            )
        self.assertEqual(len(self.options_rest.submits()), submits)
        self.assertEqual(authority.core.health_snapshot()["fallback_count"], 0)
        self.assertEqual(getattr(self.options_rest, "authority_refusals", []), [])
        self.assert_never_sent()

    async def test_withdrawn_helper_options_profile_refuses_before_any_task(self):
        authority = await self.use_signed_core()
        self.add_template_sensor()
        created = await self.approved_options(KILOWATT)
        await authority.admit(contract.PROFILE)
        self.assertFalse(authority.core.route_status((contract.PROFILE,))["available"])
        calls = self.ha_calls()
        with self.assertRaises(GovernanceError) as refused:
            await self.apply(created)
        self.assertEqual(refused.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(refused.exception.details["reason_code"], PENDING)
        self.assertIsNone(self.service.task_repository.get_for_plan(created["plan_id"]))
        self.assertEqual(self.ha_calls(), calls)

    async def test_unadmitted_helper_options_cannot_plan(self):
        await self.use_signed_core(contract.PROFILE)
        self.add_template_sensor()
        calls = self.ha_calls()
        with self.assertRaises(GovernanceError) as refused:
            await self.create_options(KILOWATT)
        self.assertEqual(refused.exception.details["reason_code"], PENDING)
        self.assertEqual(self.ha_calls(), calls)

    # -- the same gap in other capabilities ----------------------------------

    async def test_stale_entity_availability_plan_is_refused_truthfully(self):
        authority = await self.use_signed_core()
        self.add_entity("light.porch")
        created = await self.approved_availability()
        await self.retire_and_restore(authority)
        result = await self.apply(created)
        self.assert_stale_refusal(authority, result)
        self.assertEqual(self.ws.writes(), [])
        fresh = await self.approved_availability()
        self.assertEqual((await self.apply(fresh))["task_state"], "succeeded_verified")
        self.assertEqual(len(self.ws.writes()), 1)

    async def test_stale_configuration_delete_plan_is_refused_truthfully(self):
        authority = await self.use_signed_core()
        self.add("automation", "synthetic_cleanup")
        created = await self.create_one("automation", "synthetic_cleanup")
        await self.approve(created)
        await self.retire_and_restore(authority)
        result = await self.apply(created)
        self.assert_stale_refusal(authority, result)
        self.assertEqual(self.config_rest.writes(), [])
        fresh = await self.create_one("automation", "synthetic_cleanup")
        await self.approve(fresh)
        self.assertEqual((await self.apply(fresh))["task_state"], "succeeded_verified")
        self.assertEqual(len(self.config_rest.writes()), 1)


class CountingAdapter:
    """Stands in for the provider adapter behind the real Core wrappers."""

    def __init__(self):
        self.capabilities = SimpleNamespace()
        self.preflights = 0

    async def preflight(self, prepared, *, acquired_locks):
        self.preflights += 1
        return "preflight"


class F3AuthorityMatrixTests(HelperOptionsServiceTestCase):
    """Every activated F3 identity across the real Core lease boundary."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.entries = self.runtime.registry.entries
        self.authority = await signed_core(self)
        self.core = self.authority.core

    async def attempt(self, entry, plan_created_at: str):
        """Wrapper preflight, then the executor's guard acquisition."""

        prepared = f3_identity(entry)
        inner = CountingAdapter()
        guard = _CoreDispatchAuthorityGuard(self.core, plan_created_at)
        wrapped = _CoreVerificationAdapter(inner, self.core, guard)
        try:
            await wrapped.preflight(prepared, acquired_locks=())
            authority = await guard.acquire(prepared, "preflight")
        except DispatchAuthorityRefused as refused:
            return refused.diagnostic_code, inner.preflights
        commits = guard.consume(authority)
        self.assertIsNotNone(commits)
        self.assertTrue(guard.authorizes_current_call())
        self.assertTrue(guard.finish(commits))
        return "authorized", inner.preflights

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def test_activated_identities_and_counts(self):
        health = self.runtime.registry.health()
        self.assertEqual(health["activated_capability_count"], len(self.entries))
        self.assertEqual(len(self.entries), 25)
        # 18 configuration adapters, one shared operational adapter, one
        # dashboard adapter.
        self.assertEqual(health["registered_adapter_count"], 20)

    def test_activated_set_matches_compiled_profiles_with_justified_gaps(self):
        required = set()
        for entry in self.entries:
            requirements = set(f3_requirements(f3_identity(entry)))
            with self.subTest(identity=entry.capability_id):
                self.assertIn(VERIFICATION, requirements)
                self.assertNotIn("core.unsupported_f3_operation", requirements)
                self.assertLessEqual(requirements, COMPILED)
            required |= requirements
        self.assertEqual(COMPILED - required, set(NOT_F3))
        self.assertEqual(required | set(NOT_F3), set(COMPILED))
        # Every runtime-only write profile that gates an F3 entry is activated.
        self.assertLessEqual(F3_ENTRY_ADMISSION_PROFILES - {"core.script_trace_read"}, required)

    async def test_admitted_identity_is_authorized_once(self):
        for entry in self.entries:
            with self.subTest(identity=entry.capability_id):
                self.assertEqual(await self.attempt(entry, self.now()), ("authorized", 1))

    async def test_withheld_identity_is_refused_before_any_provider_read(self):
        for entry in self.entries:
            withheld = operation_profiles(f3_identity(entry))
            with self.subTest(identity=entry.capability_id):
                await self.authority.admit(*withheld)
                self.assertEqual(await self.attempt(entry, self.now()), (GENERIC, 0))
                await self.authority.admit()
                self.assertEqual(await self.attempt(entry, self.now()), ("authorized", 1))

    async def test_plan_predating_authority_change_is_refused_with_its_reason(self):
        before = self.now()
        await self.authority.retire_and_restore()
        for entry in self.entries:
            with self.subTest(identity=entry.capability_id):
                self.assertEqual(await self.attempt(entry, before), (STALE, 0))
                self.assertEqual(await self.attempt(entry, self.now()), ("authorized", 1))

    async def test_guard_names_its_own_stale_refusal(self):
        # The executor's guard acquisition after a passed preflight (a
        # retirement between the two) reports the same reason.
        before = self.now()
        await self.authority.retire_and_restore()
        entry = self.runtime.registry.entry(contract.CAPABILITY_IDENTITY)
        for created_at, expected in ((before, STALE), (None, None)):
            guard = _CoreDispatchAuthorityGuard(self.core, created_at)
            if expected is None:
                self.assertIsNotNone(await guard.acquire(f3_identity(entry), "preflight"))
                continue
            with self.assertRaises(DispatchAuthorityRefused) as refused:
                await guard.acquire(f3_identity(entry), "preflight")
            self.assertEqual(refused.exception.diagnostic_code, expected)

    async def test_stale_and_withheld_keeps_the_generic_reason(self):
        # A withdrawal is also an authority change; the plan is stale, but the
        # profile is not admitted, so a fresh plan is not the remedy.
        before = self.now()
        entry = self.runtime.registry.entry(contract.CAPABILITY_IDENTITY)
        await self.authority.admit(contract.PROFILE)
        self.assertFalse(self.core.plan_is_current(before))
        self.assertEqual(await self.attempt(entry, before), (GENERIC, 0))

    async def test_current_plan_refused_for_capacity_keeps_the_generic_reason(self):
        # Core refuses a current, admitted plan when its issued-lease capacity
        # is exhausted. Staleness is not the reason, so it is not named.
        entry = self.runtime.registry.entry(contract.CAPABILITY_IDENTITY)
        requirements = f3_requirements(f3_identity(entry))
        held = []
        while True:
            lease = self.core.acquire(
                (VERIFICATION,), target={"target_type": "automation", "target_id": "held"}
            )
            if lease is None:
                break
            held.append(lease)
        self.assertTrue(held)
        created_at = self.now()
        self.assertTrue(self.core.route_status(requirements)["available"])
        self.assertTrue(self.core.plan_is_current(created_at))
        stale_before = self.core.health_snapshot()["counters"]["stale_plan_rejections"]
        self.assertEqual(await self.attempt(entry, created_at), (GENERIC, 0))
        self.assertEqual(
            self.core.health_snapshot()["counters"]["stale_plan_rejections"], stale_before
        )
        for lease in held:
            self.core.release(lease)
        self.assertEqual(await self.attempt(entry, self.now()), ("authorized", 1))

    async def test_unmapped_identity_names_the_unmapped_requirement(self):
        entry = SimpleNamespace(
            capability_id="unreviewed_configuration", plan_operation="unreviewed",
            operation_family="configuration", target_type="automation",
        )
        self.assertEqual(
            await self.attempt(entry, self.now()), ("core_requirement_unmapped", 0)
        )

    async def test_runtime_without_reporting_methods_keeps_the_generic_reason(self):
        class Refusing:
            async def reconcile_once(self, reason):
                return None

            def acquire_f3(self, prepared, plan_created_at=None):
                return None

        entry = self.runtime.registry.entry(contract.CAPABILITY_IDENTITY)
        guard = _CoreDispatchAuthorityGuard(Refusing(), self.now())
        inner = CountingAdapter()
        wrapped = _CoreVerificationAdapter(inner, Refusing(), guard)
        with self.assertRaises(DispatchAuthorityRefused) as refused:
            await wrapped.preflight(f3_identity(entry), acquired_locks=())
        self.assertEqual(refused.exception.diagnostic_code, GENERIC)
        self.assertEqual(inner.preflights, 0)
        # The guard keeps its pre-HAMCP-276 contract: a refused lease that is
        # not stale stays None, which the executor reports generically.
        self.assertIsNone(await guard.acquire(f3_identity(entry), "preflight"))

    async def test_guard_returns_none_for_a_refusal_that_is_not_stale(self):
        entry = self.runtime.registry.entry(contract.CAPABILITY_IDENTITY)
        await self.authority.admit(*operation_profiles(f3_identity(entry)))
        guard = _CoreDispatchAuthorityGuard(self.core, self.now())
        self.assertIsNone(await guard.acquire(f3_identity(entry), "preflight"))


class ReasonVocabularyTests(unittest.TestCase):
    def test_stale_code_is_reported_verbatim_with_its_own_check(self):
        from ha_mcp_engineering.f3.executor import _authority_refusal_code

        self.assertEqual(_authority_refusal_code(DispatchAuthorityRefused(STALE)), STALE)
        self.assertIn(STALE, reasons.REASON_CODES)
        self.assertEqual(reasons.REASON_CHECKS[STALE], "core_authority_freshness")
        self.assertEqual(reasons.classify((STALE,))[:2], (STALE, "core_authority_freshness"))
        self.assertTrue(reasons.EVIDENCE_PATTERN.fullmatch(STALE))
        self.assertEqual(reasons.REASON_CHECKS[GENERIC], "dispatch_authority")


if __name__ == "__main__":
    unittest.main()
