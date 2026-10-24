"""HAMCP-069B Core admission, routing, catalog and never-sent boundary tests.

Both 069B profiles are runtime-only.  No owner-signed registry sequence
(through 12) references them, so both resource types are refused with
``pending_signed_registry_admission``; only a TEST-signed registry entry that
carries the exact references admits them here.
"""

from __future__ import annotations

import ast
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
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration  # noqa: E402
from ha_mcp_engineering.governance.entity_availability_exposure import (  # noqa: E402
    ALLOWED_COMMANDS,
    NEVER_SENT_COMMANDS,
    READ_COMMANDS,
    AvailabilityExposureContractError,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_CAPABILITY_PROFILES,
    CORE_ENTITY_AVAILABILITY_EXPOSURE_PROFILES,
    CORE_ENTITY_METADATA_PROFILES,
    CORE_RUNTIME_CAPABILITY_PROFILES,
    SUPPORTED_CORE_RELEASES,
    compiled_exact_authority,
)
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    AVAILABILITY_EXPOSURE_TOOL,
    STATIC_TARGET_INVALID_ENTITY_IDS,
    STATIC_TARGET_INVALID_OPERATION,
    StaticToolTargetRefused,
    f3_requirements,
    static_tool_requirements,
    static_tool_target,
)
from ha_mcp_engineering.ha_core_readmission.runtime import CoreRuntime  # noqa: E402
from ha_mcp_engineering.providers.routing import (  # noqa: E402
    TOOL_CAPABILITY_POLICY,
    ProviderCapability,
)
from ha_mcp_engineering.tools.governance import (  # noqa: E402
    ENTITY_AVAILABILITY_EXPOSURE_INPUT_SCHEMA,
)
from ha_mcp_engineering.tools.registry import (  # noqa: E402
    ENGINEERING_STATIC_TOOL_NAMES,
)

# Imported as modules so that their test classes are not collected here.
import tests.test_core_2026_10_device_registry as october  # noqa: E402
import tests.test_ha_core_2026_9_integration as core9  # noqa: E402
import tests.test_hamcp069a_entity_metadata_readmission as readmission  # noqa: E402
from tests.hamcp069a_entity_fixtures import _provider_identity  # noqa: E402
from tests.hamcp069b_fixtures import (  # noqa: E402
    AvailabilityExposureServiceTestCase,
)
from tests.test_ha_core_capability_auto_readmission import (  # noqa: E402
    _evidence,
    _snapshot,
)


AVAILABILITY = "core.entity_registry_availability_operation"
EXPOSURE = "core.entity_assistant_exposure_operation"
ENTITY_METADATA = "core.entity_registry_metadata_operation"
PLANNING = "core.dependency_helper_planning"
VERIFICATION = "core.f3_mutation_verification"
UNSUPPORTED = ("core.f3_mutation_verification", "core.unsupported_f3_operation")
F3 = {
    AVAILABILITY: "update_entity_registry_availability_configuration",
    EXPOSURE: "update_entity_assistant_exposure_configuration",
}
TOOL = AVAILABILITY_EXPOSURE_TOOL
DISABLE = {"operation": "disable", "entity_ids": ["light.porch"]}
EXPOSE = {"operation": "expose", "entity_ids": ["light.porch"], "assistant": "conversation"}
ENGINE = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
REFERENCE_KEYS = (
    "capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint",
)
Prepared = readmission.Prepared
call = readmission.call
gateway_for = readmission.gateway_for


def ids(profiles) -> set[str]:
    return {profile.capability_id for profile in profiles}


def target(resource_type: str, *entity_ids: str) -> dict:
    return {"target_type": resource_type, "target_id": ",".join(sorted(entity_ids))}


def references() -> list[dict]:
    return [
        {key: profile.to_mapping()[key] for key in REFERENCE_KEYS}
        for profile in CORE_ENTITY_AVAILABILITY_EXPOSURE_PROFILES
    ]


def pending_details(body: bytes) -> dict:
    """The structured details of a pending-admission refusal, else ``{}``."""

    (data,) = [
        line[len(b"data: "):] for line in body.splitlines() if line.startswith(b"data: ")
    ]
    (content,) = json.loads(data)["result"]["content"]
    details = json.loads(content["text"]).get("details") or {}
    if details.get("reason_code") != "pending_signed_registry_admission":
        return {}
    return details


class DormantProfileTests(unittest.TestCase):
    def test_profiles_are_runtime_only_and_outside_compiled_authority(self):
        self.assertEqual(ids(CORE_ENTITY_AVAILABILITY_EXPOSURE_PROFILES), {AVAILABILITY, EXPOSURE})
        self.assertEqual(len(CORE_CAPABILITY_PROFILES), 17)
        # HAMCP-165 and HAMCP-155 add the dormant label and configuration delete profiles.
        self.assertEqual(len(CORE_RUNTIME_CAPABILITY_PROFILES), 26)
        for capability_id in (AVAILABILITY, EXPOSURE):
            with self.subTest(capability_id=capability_id):
                self.assertIn(capability_id, ids(CORE_RUNTIME_CAPABILITY_PROFILES))
                self.assertNotIn(capability_id, ids(CORE_CAPABILITY_PROFILES))
                for version, _commit, _digest in SUPPORTED_CORE_RELEASES:
                    self.assertNotIn(
                        capability_id,
                        {
                            item
                            for selection in compiled_exact_authority(version)
                            for item in selection.capability_ids
                        },
                    )
        checks = {
            profile.capability_id: set(profile.to_mapping()["required_checks"])
            for profile in CORE_ENTITY_AVAILABILITY_EXPOSURE_PROFILES
        }
        for profile in CORE_ENTITY_AVAILABILITY_EXPOSURE_PROFILES:
            with self.subTest(profile=profile.capability_id):
                self.assertTrue(profile.capability_class.mutation_capable)
                # Same as 069A: eligibility still needs an exact signed reference.
                self.assertEqual(
                    profile.auto_eligible, CORE_ENTITY_METADATA_PROFILES[0].auto_eligible
                )
        self.assertIn("websocket_entity_registry_update_disabled_by_user_or_null",
                      checks[AVAILABILITY])
        self.assertIn("websocket_expose_entity_single_assistant_single_entity",
                      checks[EXPOSURE])
        # Neither contract names a cloud check or an "expose new entities" setter.
        self.assertFalse(
            {c for c in checks[AVAILABILITY] | checks[EXPOSURE]
             if "cloud" in c or ("expose_new" in c and not c.endswith("_get"))}
        )

    def test_each_profile_names_every_read_its_gateway_sends(self):
        # HAMCP-222 OD-5 (Josh, 2026-10-23): the signed contract must name the
        # enable disclosure's registry list and the exposure disclosure's
        # read of the "expose new entities" setting.
        from ha_mcp_engineering.governance import entity_availability_exposure as boundary

        checks = {
            profile.capability_id: tuple(profile.required_checks)
            for profile in CORE_ENTITY_AVAILABILITY_EXPOSURE_PROFILES
        }
        self.assertEqual(checks[AVAILABILITY], (
            "websocket_entity_registry_get",
            "websocket_entity_registry_update_disabled_by_user_or_null",
            "websocket_config_entries_get_single",
            "websocket_entity_registry_list",
            "entity_registry_readback_exact",
        ))
        self.assertEqual(checks[EXPOSURE], (
            "websocket_entity_registry_get",
            "websocket_expose_entity_single_assistant_single_entity",
            "websocket_expose_new_entities_get",
            "entity_registry_options_readback_exact",
        ))
        named = {
            "config/entity_registry/get": "websocket_entity_registry_get",
            "config/entity_registry/list": "websocket_entity_registry_list",
            "config_entries/get_single": "websocket_config_entries_get_single",
            "homeassistant/expose_new_entities/get": "websocket_expose_new_entities_get",
        }
        self.assertEqual(set(boundary.READ_COMMANDS), set(named))
        every_check = set(checks[AVAILABILITY]) | set(checks[EXPOSURE])
        for command, check in named.items():
            with self.subTest(command=command):
                self.assertIn(check, every_check)
        self.assertEqual(
            {profile.capability_id: profile.contract_fingerprint
             for profile in CORE_ENTITY_AVAILABILITY_EXPOSURE_PROFILES},
            {
                AVAILABILITY: "sha256:ae292fbc16ad82c8d8347b7e2eb1c3a7e8c7644d95783f5bf2ae9f9cc63a76b2",
                EXPOSURE: "sha256:474c8ccaaae83c12f56a2848995ff089097936568a2ad5dc8d25ce855d51dbbd",
            },
        )

    def test_no_signed_envelope_has_ever_referenced_them(self):
        registry = json.loads(
            (ROOT / "upstream-trust/ha-core-release-registry.json").read_text()
        )
        self.assertEqual(registry["envelopes"][-1]["sequence"], 12)
        carriers = {
            (envelope["sequence"], entry["version"], item["capability_id"])
            for envelope in registry["envelopes"]
            for entry in envelope["entries"]
            for item in entry["capabilities"]
            if item["capability_id"] in {AVAILABILITY, EXPOSURE}
        }
        self.assertEqual(carriers, set())

    def test_069a_contract_fingerprint_is_unchanged(self):
        (profile,) = CORE_ENTITY_METADATA_PROFILES
        self.assertEqual(
            profile.contract_fingerprint,
            "sha256:cc09630efbc0e1e6d0a587696406f6502c62fe730a767bc0a8cfc96a9988f767",
        )

    def test_static_requirements_select_exactly_one_profile(self):
        for arguments, selected, unselected in (
            ({"operation": "disable"}, AVAILABILITY, EXPOSURE),
            ({"operation": "enable"}, AVAILABILITY, EXPOSURE),
            ({"operation": "expose"}, EXPOSURE, AVAILABILITY),
            ({"operation": "unexpose"}, EXPOSURE, AVAILABILITY),
        ):
            with self.subTest(arguments=arguments):
                requirements = static_tool_requirements(TOOL, arguments)
                self.assertEqual(requirements, tuple(sorted((PLANNING, selected))))
                self.assertNotIn(unselected, requirements)
        # An unknown operation keeps both profiles; its target is refused.
        for arguments in ({}, {"operation": "rename"}, {"operation": 3}, None):
            with self.subTest(arguments=arguments):
                self.assertEqual(
                    static_tool_requirements(TOOL, arguments),
                    tuple(sorted((PLANNING, AVAILABILITY, EXPOSURE))),
                )

    def test_static_target_binds_the_exact_sorted_entity_set(self):
        self.assertEqual(
            static_tool_target(
                TOOL, {"operation": "disable", "entity_ids": ["light.b", "light.a"]}
            ),
            target("entity_registry_availability", "light.a", "light.b"),
        )
        self.assertEqual(
            static_tool_target(TOOL, EXPOSE),
            target("entity_assistant_exposure", "light.porch"),
        )
        eight = [f"light.room_{index}" for index in range(8)]
        self.assertEqual(
            static_tool_target(TOOL, {"operation": "enable", "entity_ids": eight})["target_id"],
            ",".join(sorted(eight)),
        )
        for arguments, reason in (
            ({}, STATIC_TARGET_INVALID_OPERATION),
            ({"operation": "rename", "entity_ids": ["light.a"]}, STATIC_TARGET_INVALID_OPERATION),
            ({"operation": "disable"}, STATIC_TARGET_INVALID_ENTITY_IDS),
            ({"operation": "disable", "entity_ids": []}, STATIC_TARGET_INVALID_ENTITY_IDS),
            ({"operation": "disable", "entity_ids": "light.a"}, STATIC_TARGET_INVALID_ENTITY_IDS),
            ({"operation": "disable", "entity_ids": [*eight, "light.z"]},
             STATIC_TARGET_INVALID_ENTITY_IDS),
            ({"operation": "disable", "entity_ids": ["light.a", "light.a"]},
             STATIC_TARGET_INVALID_ENTITY_IDS),
            ({"operation": "disable", "entity_ids": ["light.a,light.b"]},
             STATIC_TARGET_INVALID_ENTITY_IDS),
            ({"operation": "disable", "entity_ids": ["Light.A"]}, STATIC_TARGET_INVALID_ENTITY_IDS),
            ({"operation": "disable", "entity_ids": [7]}, STATIC_TARGET_INVALID_ENTITY_IDS),
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(StaticToolTargetRefused) as raised:
                    static_tool_target(TOOL, arguments)
                self.assertEqual(raised.exception.reason_code, reason)

    def test_f3_routes(self):
        for profile, identity in F3.items():
            with self.subTest(identity=identity):
                self.assertEqual(
                    f3_requirements(Prepared(identity)), tuple(sorted((profile, VERIFICATION)))
                )
                self.assertNotIn(
                    "core.governed_configuration_operation",
                    f3_requirements(Prepared(identity)),
                )
        for prepared in (
            Prepared("update_entity_registry_availability"),
            Prepared("update_entity_assistant_exposure"),
            Prepared(F3[AVAILABILITY], operation=F3[EXPOSURE]),
            Prepared(F3[EXPOSURE], operation="create_entity_assistant_exposure_configuration"),
        ):
            with self.subTest(identity=prepared.capability_identity, op=prepared.operation):
                self.assertEqual(f3_requirements(prepared), UNSUPPORTED)

    def test_tool_policy_and_closed_schema(self):
        self.assertIn(TOOL, ENGINEERING_STATIC_TOOL_NAMES)
        self.assertEqual(TOOL_CAPABILITY_POLICY[TOOL], ProviderCapability.OPERATIONAL_ADMINISTRATION)
        schema = ENTITY_AVAILABILITY_EXPOSURE_INPUT_SCHEMA
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["properties"]),
                         {"operation", "entity_ids", "assistant", "expiration_minutes"})
        self.assertEqual(schema["properties"]["entity_ids"]["maxItems"], 8)
        self.assertIs(schema["properties"]["entity_ids"]["uniqueItems"], True)
        self.assertEqual(
            schema["properties"]["assistant"]["enum"], ["conversation", "cloud.google_assistant"]
        )
        self.assertNotIn("cloud.alexa", json.dumps(schema))
        self.assertNotIn("reason", schema["properties"])


class CatalogAndHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def provider(self, **kwargs) -> dict:
        service = ChangeGovernanceService(
            ChangePlanRepository(Path(self.temp.name)), object(), **kwargs
        )
        summary = service.health_summary()
        return summary["operational_administration"]["entity_availability_exposure_provider"]

    def test_capability_shows_pending_admission(self):
        capability = capability_for_tool(TOOL)
        self.assertEqual(capability["core_admission"], "pending_signed_registry_admission")
        self.assertEqual(sorted(capability["core_capabilities"]), [EXPOSURE, AVAILABILITY])
        self.assertEqual(capability["max_entities_per_plan"], 8)
        self.assertEqual(
            capability["writable_assistants"], ["cloud.google_assistant", "conversation"]
        )
        self.assertEqual(capability["fallback"], "none")
        self.assertIs(capability["direct_write_allowed"], False)
        self.assertIs(capability["external_approval_required"], True)

    def test_health_shows_pending_admission_without_a_core_call(self):
        for kwargs, configured in (
            ({"entity_availability_exposure_gateway": object()}, True),
            ({}, False),
        ):
            with self.subTest(configured=configured):
                provider = self.provider(**kwargs)
                self.assertEqual(provider["core_admission"], "pending_signed_registry_admission")
                self.assertIs(provider["configured"], configured)
                self.assertEqual(
                    provider["operational_status"],
                    "configured_unprobed" if configured else "unavailable",
                )
                self.assertEqual(provider["fallback_count"], 0)
                self.assertEqual(provider["fallback"], "none")
                self.assertEqual(sorted(provider["core_capabilities"]), [EXPOSURE, AVAILABILITY])


class UnadmittedCompiledCoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime, _source = await core9.Core20269RuntimeTests()._runtime(
            _snapshot("2026.8.1", evidence=_evidence())
        )

    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    async def test_both_resource_types_refuse_with_pending_admission(self):
        self.assertIsNotNone(self.runtime.acquire((PLANNING,)))
        withheld = {
            item["capability_id"]
            for item in self.runtime.health_snapshot()["authority_profiles"]
            if not item["disposition"].startswith("admitted_")
        }
        self.assertTrue({AVAILABILITY, EXPOSURE} <= withheld)
        gateway, app = gateway_for(self.runtime)
        for arguments, resource_type, profile in (
            (DISABLE, "entity_registry_availability", AVAILABILITY),
            ({"operation": "enable", "entity_ids": ["light.porch"]},
             "entity_registry_availability", AVAILABILITY),
            (EXPOSE, "entity_assistant_exposure", EXPOSURE),
            ({**EXPOSE, "operation": "unexpose"}, "entity_assistant_exposure", EXPOSURE),
        ):
            with self.subTest(operation=arguments["operation"]):
                self.assertIsNone(self.runtime.acquire(
                    static_tool_requirements(TOOL, arguments),
                    target=static_tool_target(TOOL, arguments),
                ))
                self.assertIsNone(self.runtime.acquire_f3(Prepared(
                    F3[profile],
                    target=SimpleNamespace(**target(resource_type, "light.porch")),
                )))
                body = await call(gateway, TOOL, arguments)
                self.assertIn(b"CoreCapabilityUnavailable", body)
                self.assertEqual(
                    pending_details(body),
                    {
                        "reason": "pending_signed_registry_admission",
                        "reason_code": "pending_signed_registry_admission",
                        "resource_type": resource_type,
                        "core_capability": profile,
                        "plan_created": False,
                        "provider_dispatch_occurred": False,
                    },
                )
        # The MCP application owns every Home Assistant interaction.
        self.assertEqual(app.calls, [])
        # Other unavailable tools keep the generic refusal without details.
        body = await call(gateway, "create_entity_metadata_plan",
                          {"entity_id": "light.porch", "name": "x"})
        self.assertIn(b"CoreCapabilityUnavailable", body)
        self.assertEqual(pending_details(body), {})
        admitted = await call(gateway, "create_change_plan", {})
        self.assertNotIn(b"CoreCapabilityUnavailable", admitted)
        self.assertEqual(len(app.calls), 1)
        self.assertEqual(self.runtime.health_snapshot()["fallback_count"], 0)


class SignedTestRegistryAdmissionTests(unittest.IsolatedAsyncioTestCase):
    """A TEST-signed entry with the exact references admits the profiles."""

    asyncSetUp = october.OctoberSignedSelectionTests.asyncSetUp
    publish = october.OctoberSignedSelectionTests.publish

    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    def with_references(self, *capability_ids: str) -> dict:
        entry = deepcopy(self.entry)
        entry["capabilities"] = [
            *self.entry["capabilities"],
            *(item for item in references() if item["capability_id"] in capability_ids),
        ]
        return entry

    async def test_signed_references_admit_both_profiles(self):
        health = await self.publish(self.with_references(AVAILABILITY, EXPOSURE))
        self.assertEqual(health["compatible_count"], 24)
        gateway, app = gateway_for(self.core)
        for arguments, resource_type, profile in (
            (DISABLE, "entity_registry_availability", AVAILABILITY),
            (EXPOSE, "entity_assistant_exposure", EXPOSURE),
        ):
            with self.subTest(profile=profile):
                requirements = static_tool_requirements(TOOL, arguments)
                # A mutation-capable profile never issues a target-less lease.
                self.assertIsNone(self.core.acquire(requirements))
                authority = self.core.acquire(
                    requirements, target=static_tool_target(TOOL, arguments)
                )
                self.assertIsNotNone(authority)
                commits = self.core.consume(authority)
                self.assertTrue(self.core.revalidate(authority, commits))
                self.core.finish(commits)
                apply_target = SimpleNamespace(**target(resource_type, "light.porch"))
                self.assertIsNone(self.core.acquire_f3(Prepared(F3[profile])))
                self.assertIsNotNone(
                    self.core.acquire_f3(Prepared(F3[profile], target=apply_target))
                )
                body = await call(gateway, TOOL, arguments)
                self.assertNotIn(b"CoreCapabilityUnavailable", body)
        self.assertEqual(len(app.calls), 2)
        # Without an exact target the gateway refuses before the app.
        for arguments in ({"operation": "disable"}, {"operation": "rename",
                                                     "entity_ids": ["light.porch"]}):
            with self.subTest(arguments=arguments):
                body = await call(gateway, TOOL, arguments)
                self.assertIn(b"InvalidStaticToolTarget", body)
        self.assertEqual(len(app.calls), 2)
        self.assertEqual(self.core.health_snapshot()["fallback_count"], 0)

    async def test_one_reference_admits_only_its_own_resource_type(self):
        health = await self.publish(self.with_references(AVAILABILITY))
        self.assertEqual(health["compatible_count"], 23)
        gateway, app = gateway_for(self.core)
        admitted = await call(gateway, TOOL, DISABLE)
        self.assertNotIn(b"CoreCapabilityUnavailable", admitted)
        refused = await call(gateway, TOOL, EXPOSE)
        self.assertIn(b"CoreCapabilityUnavailable", refused)
        self.assertEqual(pending_details(refused)["core_capability"], EXPOSURE)
        self.assertEqual(len(app.calls), 1)
        self.assertIsNone(self.core.acquire_f3(Prepared(
            F3[EXPOSURE],
            target=SimpleNamespace(**target("entity_assistant_exposure", "light.porch")),
        )))

    async def test_tampered_reference_keeps_the_profile_withheld(self):
        entry = self.with_references(AVAILABILITY, EXPOSURE)
        for item in entry["capabilities"]:
            if item["capability_id"] == EXPOSURE:
                item["contract_fingerprint"] = "sha256:" + "0" * 64
        health = await self.publish(entry)
        self.assertEqual(health["compatible_count"], 23)
        gateway, app = gateway_for(self.core)
        refused = await call(gateway, TOOL, EXPOSE)
        self.assertIn(b"CoreCapabilityUnavailable", refused)
        self.assertEqual(app.calls, [])

    async def test_the_shipped_entry_without_references_withholds_both(self):
        health = await self.publish()
        self.assertEqual(health["compatible_count"], 22)
        gateway, app = gateway_for(self.core)
        for arguments in (DISABLE, EXPOSE):
            body = await call(gateway, TOOL, arguments)
            self.assertIn(b"CoreCapabilityUnavailable", body)
        self.assertEqual(app.calls, [])


class _PlanningAdmittedRecordingCore(readmission._RecordingF3Core):
    """Admits exact-target 069B planning; F3 apply admission follows ``admit``."""

    def route_status(self, capability_ids, **_):
        return {"available": True}

    def acquire(self, capability_ids, *, target=None, **_):
        return SimpleNamespace() if target is not None else None


class RealPreparedTargetBindingTests(AvailabilityExposureServiceTestCase):
    """Each real prepared F3 child binds its own exact entity target."""

    admit = True

    async def restart_runtime(self):
        self.core = _PlanningAdmittedRecordingCore(self.admit)
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=self.resource_gateway,
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=_provider_identity,
            retention_days=90,
            core_runtime=self.core,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    async def test_each_child_binds_its_entity_and_selected_profile(self):
        for entity_id in ("light.a", "light.b"):
            self.add_entity(entity_id)
        cases = (
            ({"operation": "disable", "entity_ids": ["light.a", "light.b"]},
             "entity_registry_availability", AVAILABILITY),
            ({"operation": "expose", "entity_ids": ["light.a"], "assistant": "conversation"},
             "entity_assistant_exposure", EXPOSURE),
        )
        for arguments, resource_type, profile in cases:
            with self.subTest(operation=arguments["operation"]):
                self.core.seen.clear()
                writes = len(self.ws.writes())
                created = await self.approved(**arguments)
                result = await self.apply(created)
                if self.admit:
                    self.assertEqual(result["status"], "applied", result)
                    self.assertEqual(
                        len(self.ws.writes()) - writes, len(arguments["entity_ids"])
                    )
                else:
                    self.assertEqual(len(self.ws.writes()), writes)
                    self.assertIs(result["provider_dispatch_occurred"], False)
                self.assertTrue(self.core.seen)
                for requirements, fingerprint, target_type, target_id in self.core.seen:
                    self.assertEqual(requirements, tuple(sorted((profile, VERIFICATION))))
                    self.assertEqual(target_type, resource_type)
                    self.assertIn(target_id, arguments["entity_ids"])
                    self.assertEqual(
                        fingerprint,
                        CoreRuntime.target_fingerprint(target(resource_type, target_id)),
                    )
        # A single-entity planner lease and its child share one identity.
        self.assertEqual(
            CoreRuntime.target_fingerprint(static_tool_target(TOOL, EXPOSE | {
                "entity_ids": ["light.a"]})),
            CoreRuntime.target_fingerprint(target("entity_assistant_exposure", "light.a")),
        )


class RefusedPreparedTargetBindingTests(RealPreparedTargetBindingTests):
    admit = False


class NeverSentBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_vocabulary_is_closed_and_disjoint(self):
        self.assertEqual(
            set(READ_COMMANDS),
            {
                "config/entity_registry/get",
                "config/entity_registry/list",
                "config_entries/get_single",
                "homeassistant/expose_new_entities/get",
            },
        )
        self.assertEqual(
            set(ALLOWED_COMMANDS) - set(READ_COMMANDS),
            {"config/entity_registry/update", "homeassistant/expose_entity"},
        )
        self.assertEqual(
            set(NEVER_SENT_COMMANDS),
            {
                "cloud/alexa/entities/update",
                "cloud/google_assistant/entities/update",
                "cloud/status",
                "homeassistant/expose_entity/list",
                "homeassistant/expose_new_entities/set",
            },
        )
        self.assertFalse(set(NEVER_SENT_COMMANDS) & set(ALLOWED_COMMANDS))

    def test_cloud_status_appears_only_in_the_docstring_and_never_sent_list(self):
        """Static check: ``cloud/status`` is never a sendable value."""

        occurrences = []
        for path in sorted(ENGINE.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "cloud/status" not in text:
                continue
            tree = ast.parse(text)
            never_sent: set[int] = set()
            docstrings: set[int] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and any(
                    isinstance(item, ast.Name) and item.id == "NEVER_SENT_COMMANDS"
                    for item in node.targets
                ):
                    never_sent |= {id(item) for item in ast.walk(node.value)}
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)) and ast.get_docstring(node):
                    docstrings.add(id(node.body[0].value))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and "cloud/status" in node.value:
                    kind = (
                        "never_sent" if id(node) in never_sent
                        else "docstring" if id(node) in docstrings
                        else "value"
                    )
                    occurrences.append((path.relative_to(ENGINE).as_posix(), kind))
        self.assertEqual(
            sorted(occurrences),
            [
                ("governance/entity_availability_exposure.py", "docstring"),
                ("governance/entity_availability_exposure.py", "never_sent"),
            ],
        )

    async def test_gateway_refuses_never_sent_and_unknown_commands_before_io(self):
        from ha_mcp_engineering.governance.entity_availability_exposure import (
            EntityAvailabilityExposureGateway,
        )

        class Socket:
            def __init__(self):
                self.commands = []

            async def command(self, message):
                self.commands.append(message)
                return None

        socket = Socket()
        gateway = EntityAvailabilityExposureGateway(socket)
        for kind in (*NEVER_SENT_COMMANDS, "config/entity_registry/remove",
                     "call_service", "", None):
            with self.subTest(command=kind):
                with self.assertRaises(AvailabilityExposureContractError):
                    await gateway._send({"type": kind})
        self.assertEqual(socket.commands, [])


class GovernanceToolTests(AvailabilityExposureServiceTestCase):
    async def test_tool_reaches_the_service_and_creates_one_plan(self):
        from ha_mcp_engineering.tools import governance as tools

        self.add_entity("light.porch")
        with patch.object(tools.GOVERNANCE, "service", self.service):
            body = await tools.create_entity_availability_exposure_plan(
                operation="disable", entity_ids=["light.porch"]
            )
        self.assertIn("plan_id", body)
        self.assertEqual(len(self.repository.list()), 1)
        self.assertEqual(self.ws.writes(), [])


if __name__ == "__main__":
    unittest.main()
