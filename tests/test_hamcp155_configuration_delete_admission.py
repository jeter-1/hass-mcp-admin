"""HAMCP-155 Core admission at every layer (lesson 1 of the HAMCP-165 review).

``core.configuration_delete_operation`` is runtime-only.  The published
owner-signed registry references it from sequence 13 (HAMCP-222) on 2026.10.1
and from sequence 14 on 2026.10.1 and 2026.10.3 only.  Without such a
reference deletion is refused with ``pending_signed_registry_admission``:

* first at the service, for every entry point (public, internal builder,
  generic configuration plan), before input validation, any Home Assistant
  read and any persistence;
* again at dispatch in every F3 path (first apply, admission withdrawn after
  approval or during preflight, an F3 runtime composed without Core, a
  retained approved plan after a restart, recovery, and the legacy apply
  routes), each with zero DELETE requests.

Only a TEST-signed registry entry that carries the exact reference admits it.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.capabilities import capability_for_tool  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    ErrorCode,
    GovernanceError,
    HomeAssistantApiError,
)
from ha_mcp_engineering.governance import configuration_delete_planning  # noqa: E402
from ha_mcp_engineering.governance.configuration_delete import (  # noqa: E402
    CAPABILITY_IDENTITIES,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_CAPABILITY_PROFILES,
    CORE_CONFIGURATION_DELETE_PROFILES,
    CORE_RUNTIME_CAPABILITY_PROFILES,
    SUPPORTED_CORE_RELEASES,
    compiled_exact_authority,
)
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    CONFIGURATION_DELETE_TOOL,
    StaticToolTargetRefused,
    f3_requirements,
    static_tool_requirements,
    static_tool_target,
)
from ha_mcp_engineering.providers.routing import (  # noqa: E402
    TOOL_CAPABILITY_POLICY,
    ProviderCapability,
)
from ha_mcp_engineering.tools.governance import (  # noqa: E402
    CONFIGURATION_DELETE_INPUT_SCHEMA,
    IDENTIFIER_INPUTS,
)
from ha_mcp_engineering.tools.registry import (  # noqa: E402
    ENGINEERING_STATIC_TOOL_NAMES,
)

# Imported as modules so that their test classes are not collected here.
import tests.test_core_2026_10_device_registry as october  # noqa: E402
import tests.test_ha_core_2026_9_integration as core9  # noqa: E402
import tests.test_hamcp069a_entity_metadata_readmission as readmission  # noqa: E402
from tests.hamcp155_fixtures import (  # noqa: E402
    AdmittingDeleteCore,
    ConfigurationDeleteServiceTestCase,
    PROFILES_155,
)
from tests.test_ha_core_capability_auto_readmission import (  # noqa: E402
    _evidence,
    _snapshot,
)


DELETE = "core.configuration_delete_operation"
PLANNING = "core.dependency_helper_planning"
VERIFICATION = "core.f3_mutation_verification"
GOVERNED = "core.governed_configuration_operation"
UNSUPPORTED = ("core.f3_mutation_verification", "core.unsupported_f3_operation")
PENDING = "pending_signed_registry_admission"
TOOL = CONFIGURATION_DELETE_TOOL
ONE = {
    "deletions": [{"resource_type": "automation", "target_id": "porch_light"}],
    "reason": "Synthetic cleanup",
}
REFERENCE_KEYS = (
    "capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint",
)
PENDING_DETAILS = {
    "reason": PENDING,
    "reason_code": PENDING,
    "resource_type": "configuration_deletion",
    "core_capability": DELETE,
    "plan_created": False,
    "provider_dispatch_occurred": False,
}
Prepared = readmission.Prepared
call = readmission.call
gateway_for = readmission.gateway_for


def ids(profiles) -> set[str]:
    return {profile.capability_id for profile in profiles}


def references() -> list[dict]:
    return [
        {key: profile.to_mapping()[key] for key in REFERENCE_KEYS}
        for profile in CORE_CONFIGURATION_DELETE_PROFILES
    ]


def pending_details(body: bytes) -> dict:
    """The structured details of a pending-admission refusal, else ``{}``."""

    (data,) = [
        line[len(b"data: "):] for line in body.splitlines() if line.startswith(b"data: ")
    ]
    (content,) = json.loads(data)["result"]["content"]
    details = json.loads(content["text"]).get("details") or {}
    if details.get("reason_code") != PENDING:
        return {}
    return details


def apply_target(target_id: str = "porch_light") -> SimpleNamespace:
    return SimpleNamespace(target_type="automation", target_id=target_id)


class DormantProfileTests(unittest.TestCase):
    def test_profile_is_runtime_only_and_outside_compiled_authority(self):
        self.assertEqual(ids(CORE_CONFIGURATION_DELETE_PROFILES), {DELETE})
        self.assertNotIn(DELETE, ids(CORE_CAPABILITY_PROFILES))
        self.assertIn(DELETE, ids(CORE_RUNTIME_CAPABILITY_PROFILES))
        # The sequence-13 batch ends with it; the HAMCP-232/145, HAMCP-106 and
        # HAMCP-071 seq 16 November batch (dormant, unsigned) follows.
        self.assertEqual(CORE_RUNTIME_CAPABILITY_PROFILES[-6].capability_id, DELETE)
        for version, _commit, _digest in SUPPORTED_CORE_RELEASES:
            with self.subTest(version=version):
                self.assertNotIn(
                    DELETE,
                    {
                        item
                        for selection in compiled_exact_authority(version)
                        for item in selection.capability_ids
                    },
                )
        (profile,) = CORE_CONFIGURATION_DELETE_PROFILES
        self.assertTrue(profile.capability_class.mutation_capable)
        self.assertEqual(
            profile.to_mapping()["adapter_id"],
            "compiled-core-configuration-delete-operation-v1",
        )

    def test_contract_fingerprints_for_the_sequence_13_signing(self):
        # Signed sequences 13 and 14 reference exactly this value; the signed
        # create/update contract it sits beside is unchanged.
        (profile,) = CORE_CONFIGURATION_DELETE_PROFILES
        self.assertEqual(
            profile.contract_fingerprint,
            "sha256:5688af5d141b97ca33eadf251ecfbb8f3b1f833024b0ee1acea4973f558f887d",
        )
        (configuration,) = [
            item
            for item in CORE_CAPABILITY_PROFILES
            if item.capability_id == "core.governed_configuration_operation"
        ]
        self.assertEqual(
            configuration.contract_fingerprint,
            "sha256:d517fa73878f8dafea857b5306db750304d3c65522481898800cf86fb091f441",
        )

    def test_signed_sequences_13_and_14_reference_it_exactly(self):
        # HAMCP-222: sequence 13 extends 2026.10.1, sequence 14 adds
        # 2026.10.3; no earlier envelope and no other entry carries it.
        registry = json.loads(
            (ROOT / "upstream-trust/ha-core-release-registry.json").read_text()
        )
        self.assertEqual(registry["envelopes"][-1]["sequence"], 14)
        (profile,) = CORE_CONFIGURATION_DELETE_PROFILES
        carriers = set()
        for envelope in registry["envelopes"]:
            for entry in envelope["entries"]:
                for item in entry["capabilities"]:
                    if item["capability_id"] == DELETE:
                        carriers.add((envelope["sequence"], entry["version"]))
                        self.assertEqual(
                            item, {key: profile.to_mapping()[key] for key in item}
                        )
        self.assertEqual(
            carriers, {(13, "2026.10.1"), (14, "2026.10.1"), (14, "2026.10.3")}
        )

    def test_static_requirements_and_exact_target(self):
        self.assertEqual(
            static_tool_requirements(TOOL, ONE), tuple(sorted((PLANNING, DELETE)))
        )
        self.assertNotIn(GOVERNED, static_tool_requirements(TOOL, ONE))
        two = {
            "deletions": [
                {"resource_type": "script", "target_id": "b"},
                {"resource_type": "automation", "target_id": "a"},
            ],
            "reason": "r",
        }
        self.assertEqual(
            static_tool_target(TOOL, two),
            {"target_type": "configuration_deletion", "target_id": "automation:a,script:b"},
        )
        for arguments in (
            {},
            {"deletions": [], "reason": "r"},
            {"deletions": [{"resource_type": "scene", "target_id": "a"}], "reason": "r"},
            {"deletions": [{"resource_type": "automation", "target_id": "a,b"}], "reason": "r"},
            {"deletions": [{"resource_type": "automation", "target_id": "a"}] * 2,
             "reason": "r"},
            {"deletions": [{"resource_type": "automation", "target_id": f"a{index}"}
                           for index in range(9)], "reason": "r"},
            {**ONE, "extra": True},
            None,
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(StaticToolTargetRefused):
                    static_tool_target(TOOL, arguments)

    def test_f3_routes_bind_only_the_delete_profile(self):
        for identity in CAPABILITY_IDENTITIES.values():
            with self.subTest(identity=identity):
                self.assertEqual(
                    f3_requirements(Prepared(identity)), tuple(sorted((DELETE, VERIFICATION)))
                )
                self.assertNotIn(GOVERNED, f3_requirements(Prepared(identity)))
        for prepared in (
            Prepared("delete_automation_configuration",
                     operation="delete_script_configuration"),
            Prepared("delete_automation"),
            Prepared("update_automation_configuration",
                     operation="delete_automation_configuration"),
        ):
            with self.subTest(identity=prepared.capability_identity, op=prepared.operation):
                self.assertEqual(f3_requirements(prepared), UNSUPPORTED)
        # The create/update identities never map to the delete profile.
        for identity in ("create_automation_configuration", "update_automation_configuration",
                         "create_script_configuration", "update_script_configuration"):
            with self.subTest(identity=identity):
                self.assertNotIn(DELETE, f3_requirements(Prepared(identity)))

    def test_tool_policy_and_closed_schema(self):
        self.assertIn(TOOL, ENGINEERING_STATIC_TOOL_NAMES)
        self.assertEqual(
            TOOL_CAPABILITY_POLICY[TOOL], ProviderCapability.OPERATIONAL_ADMINISTRATION
        )
        self.assertEqual(
            IDENTIFIER_INPUTS[TOOL], ("deletions[].resource_type", "deletions[].target_id")
        )
        schema = CONFIGURATION_DELETE_INPUT_SCHEMA
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["properties"]), {"deletions", "reason", "expiration_minutes"})
        self.assertEqual(schema["required"], ["deletions", "reason"])
        deletions = schema["properties"]["deletions"]
        self.assertEqual((deletions["minItems"], deletions["maxItems"]), (1, 8))
        self.assertIs(deletions["items"]["additionalProperties"], False)
        self.assertEqual(
            deletions["items"]["properties"]["resource_type"]["enum"], ["automation", "script"]
        )
        self.assertEqual(schema["properties"]["reason"]["maxLength"], 500)


class CatalogAndHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def provider(self, **kwargs) -> dict:
        service = ChangeGovernanceService(
            ChangePlanRepository(Path(self.temp.name)), object(), **kwargs
        )
        return service.health_summary()["operational_administration"][
            "configuration_delete_provider"
        ]

    def test_capability_states_the_admission_requirement(self):
        # HAMCP-181 PR2 M2: the static entry is policy, not current status;
        # list_capabilities projects the current admission (as health does).
        capability = capability_for_tool(TOOL)
        self.assertEqual(capability["core_admission_requirement"], "signed_core_registry_admission")
        self.assertEqual(capability["core_admission"], "not_evaluated")
        self.assertEqual(capability["core_capabilities"], [DELETE])
        self.assertEqual(capability["resource_types"], ["automation", "script"])
        self.assertEqual(capability["max_deletions_per_plan"], 8)
        self.assertEqual(capability["fallback"], "none")
        self.assertIs(capability["direct_write_allowed"], False)
        self.assertIs(capability["external_approval_required"], True)
        self.assertIs(capability["readback_required"], True)

    def test_health_shows_pending_admission_without_a_core_call(self):
        for kwargs, configured in (({"configuration_delete_gateway": object()}, True), ({}, False)):
            with self.subTest(configured=configured):
                provider = self.provider(**kwargs)
                self.assertEqual(provider["core_admission"], PENDING)
                self.assertIs(provider["configured"], configured)
                self.assertEqual(
                    provider["operational_status"],
                    "configured_unprobed" if configured else "unavailable",
                )
                self.assertEqual(provider["fallback_count"], 0)
                self.assertEqual(provider["core_capabilities"], [DELETE])


class UnadmittedCompiledCoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime, _source = await core9.Core20269RuntimeTests()._runtime(
            _snapshot("2026.8.1", evidence=_evidence())
        )

    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    async def test_gateway_refuses_with_pending_admission_before_the_app(self):
        self.assertIsNotNone(self.runtime.acquire((PLANNING,)))
        withheld = {
            item["capability_id"]
            for item in self.runtime.health_snapshot()["authority_profiles"]
            if not item["disposition"].startswith("admitted_")
        }
        self.assertIn(DELETE, withheld)
        self.assertIsNone(self.runtime.acquire(
            static_tool_requirements(TOOL, ONE), target=static_tool_target(TOOL, ONE)
        ))
        for identity in CAPABILITY_IDENTITIES.values():
            self.assertIsNone(self.runtime.acquire_f3(Prepared(identity, target=apply_target())))
        gateway, app = gateway_for(self.runtime)
        body = await call(gateway, TOOL, ONE)
        self.assertIn(b"CoreCapabilityUnavailable", body)
        self.assertEqual(pending_details(body), PENDING_DETAILS)
        self.assertEqual(app.calls, [])
        self.assertEqual(self.runtime.health_snapshot()["fallback_count"], 0)


SHIPPED_COMPATIBLE = 22


class SignedTestRegistryAdmissionTests(unittest.IsolatedAsyncioTestCase):
    """A TEST-signed entry with the exact reference admits the profile."""

    asyncSetUp = october.OctoberSignedSelectionTests.asyncSetUp
    publish = october.OctoberSignedSelectionTests.publish

    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    def with_reference(self) -> dict:
        entry = deepcopy(self.entry)
        entry["capabilities"] = [*self.entry["capabilities"], *references()]
        return entry

    async def test_signed_reference_admits_the_profile(self):
        # The shipped entry admits 22 profiles (see below); one more here.
        health = await self.publish(self.with_reference())
        self.assertEqual(health["compatible_count"], SHIPPED_COMPATIBLE + 1)
        requirements = static_tool_requirements(TOOL, ONE)
        # A mutation-capable profile never issues a target-less lease.
        self.assertIsNone(self.core.acquire(requirements))
        authority = self.core.acquire(requirements, target=static_tool_target(TOOL, ONE))
        self.assertIsNotNone(authority)
        commits = self.core.consume(authority)
        self.assertTrue(self.core.revalidate(authority, commits))
        self.core.finish(commits)
        identity = CAPABILITY_IDENTITIES["automation"]
        self.assertIsNone(self.core.acquire_f3(Prepared(identity)))
        self.assertIsNotNone(self.core.acquire_f3(Prepared(identity, target=apply_target())))
        gateway, app = gateway_for(self.core)
        body = await call(gateway, TOOL, ONE)
        self.assertNotIn(b"CoreCapabilityUnavailable", body)
        self.assertEqual(len(app.calls), 1)
        # Without an exact target the gateway refuses before the app.
        body = await call(gateway, TOOL, {"deletions": [], "reason": "r"})
        self.assertIn(b"InvalidStaticToolTarget", body)
        self.assertEqual(len(app.calls), 1)

    async def test_tampered_reference_keeps_the_profile_withheld(self):
        entry = self.with_reference()
        for item in entry["capabilities"]:
            if item["capability_id"] == DELETE:
                item["contract_fingerprint"] = "sha256:" + "0" * 64
        health = await self.publish(entry)
        self.assertEqual(health["compatible_count"], SHIPPED_COMPATIBLE)
        gateway, app = gateway_for(self.core)
        refused = await call(gateway, TOOL, ONE)
        self.assertIn(b"CoreCapabilityUnavailable", refused)
        self.assertEqual(app.calls, [])

    async def test_the_shipped_entry_withholds_it(self):
        health = await self.publish()
        self.assertEqual(health["compatible_count"], SHIPPED_COMPATIBLE)
        gateway, app = gateway_for(self.core)
        body = await call(gateway, TOOL, ONE)
        self.assertIn(b"CoreCapabilityUnavailable", body)
        self.assertEqual(pending_details(body), PENDING_DETAILS)
        self.assertEqual(app.calls, [])


class ServiceAdmissionFirstTests(ConfigurationDeleteServiceTestCase):
    """The service refuses first: 0 reads, nothing stored, every entry point."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add("automation", "porch_light")
        self.config_rest.requests.clear()
        self.ws.commands.clear()

    def assert_pending(self, raised) -> None:
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(raised.exception.details, PENDING_DETAILS)

    def assert_nothing_read_or_stored(self) -> None:
        self.assertEqual(self.config_rest.requests, [])
        self.assertEqual(self.ws.commands, [])
        self.assertEqual(self.snapshot_reads, [])
        self.assertEqual(self.repository.list(), [])

    async def test_unadmitted_core_refuses_before_validation_and_reads(self):
        self.core.withdraw()
        for arguments in (
            deepcopy(ONE),
            # Invalid input is refused for admission, not validated first.
            {"deletions": [], "reason": ""},
            {"deletions": [{"resource_type": "scene", "target_id": "x"}]},
            {"unexpected": 1},
            None,
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_configuration_delete_plan(arguments)
                self.assert_pending(raised)
                self.assert_nothing_read_or_stored()
        self.assertEqual(self.core.planning_leases, [])

    async def test_missing_core_or_runtime_fails_closed(self):
        for label, setup in (
            ("no core runtime", lambda: setattr(self.runtime, "core_runtime", None)),
            ("no f3 runtime", lambda: setattr(self.service, "f3_runtime", None)),
        ):
            with self.subTest(label):
                setup()
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_configuration_delete_plan(deepcopy(ONE))
                self.assert_pending(raised)
                self.assert_nothing_read_or_stored()

    async def test_core_that_cannot_report_status_fails_closed(self):
        class Broken(AdmittingDeleteCore):
            def route_status(self, capability_ids, **_):
                raise RuntimeError("synthetic")

        self.runtime.core_runtime = Broken()
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_configuration_delete_plan(deepcopy(ONE))
        self.assert_pending(raised)
        self.assert_nothing_read_or_stored()

    async def test_admitted_planning_holds_an_exact_target_lease(self):
        created = await self.service.create_configuration_delete_plan(deepcopy(ONE))
        self.assertTrue(created["plan_id"])
        self.assertIn(
            (
                (PLANNING, DELETE),
                {"target_type": "configuration_deletion", "target_id": "automation:porch_light"},
            ),
            self.core.planning_leases,
        )
        self.assertEqual(self.config_rest.writes(), [])

    async def test_admission_withdrawn_during_planning_stores_nothing(self):
        def withdraw(resource_type, target_id, ordinal):
            self.core.withdraw()

        self.config_rest.after_config_get = withdraw
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_configuration_delete_plan(deepcopy(ONE))
        self.assertIs(raised.exception.details["plan_created"], False)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.config_rest.writes(), [])

    async def test_admission_withdrawn_at_the_persist_boundary_stores_nothing(self):
        original = self.service._create_configuration_plan

        async def withdraw_then_create(**kwargs):
            self.core.withdraw()
            return await original(**kwargs)

        self.service._create_configuration_plan = withdraw_then_create
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_configuration_delete_plan(deepcopy(ONE))
        # The held lease no longer revalidates against the admitted set.
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(raised.exception.details["reason_code"], "core_capability_unavailable")
        self.assertIs(raised.exception.details["plan_created"], False)
        self.assertIs(raised.exception.details["provider_dispatch_occurred"], False)
        self.assertEqual(self.repository.list(), [])

    async def prepared(self):
        request = configuration_delete_planning.validate_request(deepcopy(ONE))
        return await configuration_delete_planning.prepare(
            self.service.configuration_delete_gateway, request
        )

    async def test_internal_builders_refuse_without_admission_or_lease(self):
        prepared = await self.prepared()
        reads = len(self.config_rest.requests)
        # No persistence callback: refused even while admitted.
        with self.assertRaises(GovernanceError) as raised:
            await self.service._create_configuration_delete_plan(prepared)
        self.assertEqual(raised.exception.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED)
        self.core.withdraw()
        with self.assertRaises(GovernanceError) as raised:
            await self.service._create_configuration_delete_plan(
                prepared, _before_persist=lambda: None
            )
        self.assert_pending(raised)
        # Admission is the delete builder's first check, before the lease.
        with self.assertRaises(GovernanceError) as raised:
            await self.service._create_configuration_delete_plan(prepared)
        self.assert_pending(raised)
        # The lowest builder checks first too, before anything is written.
        with self.assertRaises(GovernanceError) as raised:
            await self.service._create_configuration_plan(
                title="t", description="d",
                operations=[{
                    "operation_id": "delete_1", "resource_type": "automation",
                    "action": "delete", "target_id": "porch_light",
                    "proposed_config": prepared.items[0].intent,
                }],
                _configuration_delete_context=prepared,
                _before_persist=lambda: None,
            )
        self.assert_pending(raised)
        self.assertEqual(len(self.config_rest.requests), reads)
        self.assertEqual(self.repository.list(), [])

    async def test_generic_configuration_plan_cannot_carry_a_delete(self):
        for resource_type in ("automation", "script"):
            with self.subTest(resource_type=resource_type):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_configuration_plan(
                        title="t", description="d",
                        operations=[{
                            "operation_id": "x", "resource_type": resource_type,
                            "action": "delete", "target_id": "porch_light",
                        }],
                    )
                self.assertIn(
                    raised.exception.code,
                    {ErrorCode.CONFIGURATION_VALIDATION_FAILED, ErrorCode.INVALID_REQUEST},
                )
                self.assert_nothing_read_or_stored()


class RouteWithdrawnCore(AdmittingDeleteCore):
    """F3 leases stay valid; only the delete profile's route status is withdrawn.

    This isolates the adapter's own admission check from the runtime lease.
    """

    route_withdrawn = False

    def route_status(self, capability_ids, **kwargs):
        if self.route_withdrawn and DELETE in capability_ids:
            return {"available": False}
        return super().route_status(capability_ids, **kwargs)


class _SimulatedCrash(Exception):
    pass


class DispatchAdmissionTests(ConfigurationDeleteServiceTestCase):
    """Admission is re-checked at dispatch in every F3 path; 0 DELETEs."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add("automation", "porch_light")

    async def approved_plan(self) -> dict:
        return await self.approved("automation", "porch_light")

    def assert_not_deleted(self) -> None:
        self.assertEqual(self.config_rest.deletes(), [])
        self.assertIn("porch_light", self.config_rest.configs["automation"])
        self.assertIn("automation.porch_light", self.ws.entries)

    async def assert_entry_refused(self, created: dict) -> None:
        """M3: an unadmitted first apply refuses before any persistence."""

        reads = self.reads()
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(raised.exception.details["reason_code"], PENDING)
        self.assertEqual(raised.exception.details["core_capability"], DELETE)
        self.assertIs(raised.exception.details["provider_dispatch_occurred"], False)
        self.assertIsNone(
            self.service.task_repository.get_for_plan(created["plan_id"])
        )
        self.assertEqual(self.reads(), reads)
        self.assert_not_deleted()

    async def test_admission_withdrawn_after_approval(self):
        created = await self.approved_plan()
        self.core.withdraw()
        await self.assert_entry_refused(created)

    async def test_adapter_refuses_at_preflight_while_the_lease_holds(self):
        self.core = RouteWithdrawnCore(self.core.admitted)
        await self.restart_runtime()
        created = await self.approved_plan()
        gets = dict(self.config_rest.config_gets)
        entry = self.runtime._refuse_unadmitted_entry

        def withdraw_after_entry(plan):
            # The entry check passed; withdraw before the adapter preflight.
            entry(plan)
            self.core.route_withdrawn = True

        self.runtime._refuse_unadmitted_entry = withdraw_after_entry
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertIn("core_admission_unavailable", self.child_codes(result["task_id"]))
        # Preflight refuses before its live-state read, not only at dispatch.
        self.assertEqual(dict(self.config_rest.config_gets), gets)
        self.assert_not_deleted()

    async def test_adapter_refuses_immediately_before_the_delete(self):
        self.core = RouteWithdrawnCore(self.core.admitted)
        await self.restart_runtime()
        created = await self.approved_plan()
        gets = self.config_rest.config_gets[("automation", "porch_light")]

        def withdraw_after_preflight_read(resource_type, target_id, ordinal):
            # The preflight config read has passed the preflight admission
            # check; withdraw before the dispatch-time re-check.
            if ordinal == gets + 1:
                self.core.route_withdrawn = True

        self.config_rest.after_config_get = withdraw_after_preflight_read
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertIn("core_admission_unavailable", self.child_codes(result["task_id"]))
        self.assert_not_deleted()

    async def test_observation_refuses_when_admission_is_withdrawn_mid_delete(self):
        # Withdrawn after the dispatch re-check: the one DELETE is sent, but
        # verification refuses to claim success without admission.
        self.core = RouteWithdrawnCore(self.core.admitted)
        await self.restart_runtime()
        created = await self.approved_plan()
        self.config_rest.before_delete = lambda: setattr(
            self.core, "route_withdrawn", True
        )
        result = await self.apply(created)
        self.assertNotEqual(result.get("task_state"), "succeeded_verified", result)
        codes = self.child_codes(result["task_id"])
        self.assertIn("core_admission_unavailable", codes)
        self.assertNotIn("exact_readback_verified", codes)
        self.assertEqual(len(self.config_rest.deletes()), 1)

    async def test_resume_and_recovery_mid_plan_never_delete_without_admission(self):
        # One child deleted, then a process stop; admission is withdrawn
        # before the plan is resumed or recovered.
        for mode in ("resume", "recovery"):
            for withdrawal in ("lease", "route"):
                with self.subTest(mode=mode, withdrawal=withdrawal):
                    self.core = RouteWithdrawnCore(self.core.admitted | set(PROFILES_155))
                    await self.restart_runtime()
                    first, second = f"{mode}_{withdrawal}_a", f"{mode}_{withdrawal}_b"
                    self.add("automation", first)
                    self.add("automation", second)
                    created = await self.create(
                        deletions=[
                            {"resource_type": "automation", "target_id": target_id}
                            for target_id in (first, second)
                        ]
                    )
                    await self.approve(created)
                    original = self.runtime._execute_child

                    async def crash_after_first(*args, **kwargs):
                        result = await original(*args, **kwargs)
                        self.runtime._execute_child = original
                        raise _SimulatedCrash()

                    self.runtime._execute_child = crash_after_first
                    deletes = len(self.config_rest.deletes())
                    with self.assertRaises(_SimulatedCrash):
                        await self.service.apply(created["plan_id"], created["plan_hash"])
                    self.assertEqual(len(self.config_rest.deletes()), deletes + 1)
                    if withdrawal == "lease":
                        self.core.withdraw()
                    else:
                        self.core.route_withdrawn = True
                    if mode == "resume":
                        result = await self.apply(created)
                        self.assertNotEqual(
                            result.get("task_state"), "succeeded_verified", result
                        )
                    else:
                        await self.runtime.recover_once("startup")
                    self.assertEqual(len(self.config_rest.deletes()), deletes + 1)
                    self.assertIn(second, self.config_rest.configs["automation"])
                    self.assertIn(f"automation.{second}", self.ws.entries)

    async def test_f3_composed_without_core_never_deletes(self):
        created = await self.approved_plan()
        self.runtime.core_runtime = None
        await self.assert_entry_refused(created)

    async def test_retained_approved_plan_after_restart_never_deletes(self):
        created = await self.approved_plan()
        self.core.withdraw()
        await self.restart_runtime()
        await self.runtime.recover_once("synthetic_restart")
        await self.assert_entry_refused(created)
        await self.restart_runtime()
        await self.runtime.recover_once("synthetic_restart")
        self.assert_not_deleted()

    async def test_legacy_apply_routes_never_delete(self):
        created = await self.approved_plan()
        self.service.f3_runtime = None
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(raised.exception.details["reason_code"], PENDING)
        plan = self.repository.get(created["plan_id"])
        with self.assertRaises(GovernanceError) as raised:
            await self.service._apply_configuration_plan(plan, created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(self.repository.list_tasks() if hasattr(self.repository, "list_tasks") else [], [])
        self.assert_not_deleted()

    async def test_recovery_after_an_uncertain_delete_never_redispatches(self):
        created = await self.approved_plan()
        self.config_rest.delete_failure = HomeAssistantApiError(
            details={"provider_response_received": False}
        )
        self.config_rest.ignore_deletes = True
        first = await self.apply(created)
        self.assertNotEqual(first["task_state"], "succeeded_verified", first)
        deletes = len(self.config_rest.deletes())
        self.assertEqual(deletes, 1)
        self.config_rest.delete_failure = None
        # Admission withdrawn: recovery observes nothing and sends nothing.
        self.core.withdraw()
        await self.restart_runtime()
        await self.runtime.recover_once("synthetic_restart")
        self.assertEqual(len(self.config_rest.deletes()), deletes)
        # Re-admitted: recovery is readback only, never a second DELETE.
        self.core.admitted |= set(PROFILES_155)
        await self.restart_runtime()
        await self.runtime.recover_once("synthetic_restart")
        self.assertEqual(len(self.config_rest.deletes()), deletes)


class GovernanceToolTests(ConfigurationDeleteServiceTestCase):
    async def test_tool_reaches_the_service_and_creates_one_plan(self):
        from ha_mcp_engineering.tools import governance as tools

        self.add("automation", "porch_light")
        with patch.object(tools.GOVERNANCE, "service", self.service):
            body = await tools.create_configuration_delete_plan(
                deletions=[{"resource_type": "automation", "target_id": "porch_light"}],
                reason="Synthetic cleanup",
            )
        self.assertIn("plan_id", body)
        self.assertEqual(len(self.repository.list()), 1)
        self.assertEqual(self.config_rest.writes(), [])

    async def test_tool_refuses_pending_admission_without_reads(self):
        from ha_mcp_engineering.tools import governance as tools

        self.add("automation", "porch_light")
        self.config_rest.requests.clear()
        self.core.withdraw()
        with patch.object(tools.GOVERNANCE, "service", self.service):
            body = await tools.create_configuration_delete_plan(
                deletions=[{"resource_type": "automation", "target_id": "porch_light"}],
                reason="Synthetic cleanup",
            )
        self.assertIn(PENDING, body)
        self.assertEqual(self.config_rest.requests, [])
        self.assertEqual(self.repository.list(), [])


if __name__ == "__main__":
    unittest.main()
