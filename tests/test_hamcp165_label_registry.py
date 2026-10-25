"""HAMCP-165 governed label create, edit and entity assignment (INACTIVE).

``core.label_registry_operation`` is a runtime-only sibling profile.  The
published owner-signed registry references it from sequence 13 (HAMCP-222) on
2026.10.1 and from sequence 14 on 2026.10.1 and 2026.10.3 only.  Without such
a reference every entry point refuses with ``pending_signed_registry_admission``
before any Home Assistant read and before anything is stored.  Only a
TEST-signed registry entry carrying the exact reference, or the admission
double, admits it here.

Every provider is synthetic; nothing here reaches a live system.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.capabilities import capability_for_tool  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance import label_registry as boundary  # noqa: E402
from ha_mcp_engineering.governance import (  # noqa: E402
    label_registry_planning as planning,
)
from ha_mcp_engineering.governance.models import ApprovalPolicyClass  # noqa: E402
from ha_mcp_engineering.governance.policy import evaluate_change_policy  # noqa: E402
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import ChangePlanRepository  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_CAPABILITY_PROFILES,
    CORE_ENTITY_METADATA_PROFILES,
    CORE_LABEL_REGISTRY_PROFILES,
    CORE_RUNTIME_CAPABILITY_PROFILES,
    SUPPORTED_CORE_RELEASES,
    compiled_exact_authority,
)
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    LABEL_PROFILE,
    LABEL_TOOL,
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
    LABEL_PLAN_INPUT_SCHEMA,
)
from ha_mcp_engineering.tools.registry import (  # noqa: E402
    ENGINEERING_STATIC_TOOL_NAMES,
)

import tests.test_core_2026_10_device_registry as october  # noqa: E402
import tests.test_ha_core_2026_9_integration as core9  # noqa: E402
import tests.test_hamcp069a_entity_metadata_readmission as readmission  # noqa: E402
from tests.hamcp069a_entity_fixtures import (  # noqa: E402
    AuthorizationError,
    HomeAssistantApiError,
)
from tests.hamcp165_fixtures import (  # noqa: E402
    LABEL_CREATE,
    LABEL_DELETE,
    LABEL_LIST,
    LABEL_UPDATE,
    UPDATE,
    LabelAdmittingCore,
    LabelServiceTestCase,
    core_slugify,
)
from tests.test_ha_core_capability_auto_readmission import (  # noqa: E402
    _evidence,
    _snapshot,
)


PENDING = "pending_signed_registry_admission"
ENTITY_METADATA = "core.entity_registry_metadata_operation"
PLANNING = "core.dependency_helper_planning"
VERIFICATION = "core.f3_mutation_verification"
UNSUPPORTED = ("core.f3_mutation_verification", "core.unsupported_f3_operation")
CREATE_IDENTITY = "create_label_registry_entry_configuration"
UPDATE_IDENTITY = "update_label_registry_entry_configuration"
LABEL_FINGERPRINT = (
    "sha256:b2f0dd11ff243aa2559c27738d962785e37455d37b836cb5a6997aeabb26813c"
)
ENGINE = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
REFERENCE_KEYS = (
    "capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint",
)
CREATE_HOLIDAY = {"operation": "create", "name": "Holiday"}
PENDING_DETAILS = {
    "reason": PENDING,
    "reason_code": PENDING,
    "resource_type": "label_registry_entry",
    "core_capability": LABEL_PROFILE,
    "plan_created": False,
    "provider_dispatch_occurred": False,
}
Prepared = readmission.Prepared
call = readmission.call
gateway_for = readmission.gateway_for


def references() -> list[dict]:
    return [
        {key: profile.to_mapping()[key] for key in REFERENCE_KEYS}
        for profile in CORE_LABEL_REGISTRY_PROFILES
    ]


def pending_details(body: bytes) -> dict:
    (data,) = [
        line[len(b"data: "):] for line in body.splitlines() if line.startswith(b"data: ")
    ]
    (content,) = json.loads(data)["result"]["content"]
    details = json.loads(content["text"]).get("details") or {}
    if details.get("reason_code") != PENDING:
        return {}
    return details


def arguments(*operations: dict) -> dict:
    return {"operations": [deepcopy(item) for item in operations]}


# ---------------------------------------------------------------------------
# Dormant profile, static routes, schema, catalog and health
# ---------------------------------------------------------------------------


class DormantProfileTests(unittest.TestCase):
    def test_profile_is_runtime_only_and_outside_compiled_authority(self):
        (profile,) = CORE_LABEL_REGISTRY_PROFILES
        self.assertEqual(profile.capability_id, LABEL_PROFILE)
        self.assertEqual(len(CORE_CAPABILITY_PROFILES), 17)
        self.assertEqual(len(CORE_RUNTIME_CAPABILITY_PROFILES), 26)  # + HAMCP-155 delete
        self.assertIn(LABEL_PROFILE, {p.capability_id for p in CORE_RUNTIME_CAPABILITY_PROFILES})
        self.assertNotIn(LABEL_PROFILE, {p.capability_id for p in CORE_CAPABILITY_PROFILES})
        for version, _commit, _digest in SUPPORTED_CORE_RELEASES:
            with self.subTest(version=version):
                self.assertNotIn(
                    LABEL_PROFILE,
                    {
                        item
                        for selection in compiled_exact_authority(version)
                        for item in selection.capability_ids
                    },
                )
        self.assertTrue(profile.capability_class.mutation_capable)
        self.assertEqual(profile.auto_eligible, CORE_ENTITY_METADATA_PROFILES[0].auto_eligible)

    def test_profile_contract_and_fingerprint_are_pinned(self):
        (profile,) = CORE_LABEL_REGISTRY_PROFILES
        mapping = profile.to_mapping()
        self.assertEqual(mapping["profile_id"], "core_label_registry_operation_v1")
        self.assertEqual(mapping["adapter_id"], "compiled-core-label-registry-operation-v1")
        self.assertEqual(tuple(profile.required_checks), (
            "websocket_label_registry_list",
            "websocket_label_registry_create_single",
            "websocket_label_registry_update_single",
            "label_registry_readback_exact",
        ))
        self.assertEqual(profile.contract_fingerprint, LABEL_FINGERPRINT)
        # Every command the gateway can send is named by the contract.
        self.assertEqual(set(boundary.READ_COMMANDS), {LABEL_LIST})
        self.assertEqual(set(boundary.WRITE_COMMANDS.values()), {LABEL_CREATE, LABEL_UPDATE})
        self.assertNotIn("delete", json.dumps(profile.required_checks))

    def test_signed_sequences_13_and_14_reference_it_exactly(self):
        # HAMCP-222: sequence 13 extends 2026.10.1, sequence 14 adds
        # 2026.10.3; no earlier envelope and no other entry carries it.
        registry = json.loads(
            (ROOT / "upstream-trust/ha-core-release-registry.json").read_text()
        )
        self.assertEqual(registry["envelopes"][-1]["sequence"], 14)
        (profile,) = CORE_LABEL_REGISTRY_PROFILES
        carriers = set()
        for envelope in registry["envelopes"]:
            for entry in envelope["entries"]:
                for item in entry["capabilities"]:
                    if item["capability_id"] == LABEL_PROFILE:
                        carriers.add((envelope["sequence"], entry["version"]))
                        self.assertEqual(
                            item, {key: profile.to_mapping()[key] for key in item}
                        )
        self.assertEqual(
            carriers, {(13, "2026.10.1"), (14, "2026.10.1"), (14, "2026.10.3")}
        )

    def test_signed_sibling_fingerprints_are_unchanged(self):
        (profile,) = CORE_ENTITY_METADATA_PROFILES
        self.assertEqual(
            profile.contract_fingerprint,
            "sha256:cc09630efbc0e1e6d0a587696406f6502c62fe730a767bc0a8cfc96a9988f767",
        )

    def test_static_requirements_narrow_only_when_nothing_is_assigned(self):
        full = tuple(sorted((PLANNING, ENTITY_METADATA, LABEL_PROFILE)))
        for value, expected in (
            (arguments(CREATE_HOLIDAY), (LABEL_PROFILE,)),
            (arguments({**CREATE_HOLIDAY, "entity_ids": []}), (LABEL_PROFILE,)),
            (arguments({"operation": "update", "label_id": "a", "color": "red"},
                       CREATE_HOLIDAY), (LABEL_PROFILE,)),
            (arguments({**CREATE_HOLIDAY, "entity_ids": ["sensor.a"]}), full),
            ({}, full),
            (None, full),
            ({"operations": "x"}, full),
            ({"operations": [None]}, full),
        ):
            with self.subTest(arguments=value):
                self.assertEqual(static_tool_requirements(LABEL_TOOL, value), expected)

    def test_static_target_binds_the_exact_sorted_label_set(self):
        self.assertEqual(
            static_tool_target(LABEL_TOOL, arguments(
                {"operation": "update", "label_id": "outdoor", "color": "red"},
                {"operation": "create", "name": "Holiday Lights", "entity_ids": ["sensor.a"]},
            )),
            {"target_type": "label_registry_entry", "target_id": "holiday_lights,outdoor"},
        )
        for value, reason in (
            ({}, "invalid_label_request"),
            (arguments({"operation": "delete", "label_id": "outdoor"}), "invalid_label_request"),
            (arguments({**CREATE_HOLIDAY, "area_id": "kitchen"}), "label_field_not_supported"),
            (arguments({"operation": "create", "name": " bad"}), "invalid_label_name"),
            (arguments(*[{"operation": "create", "name": f"L{i}"} for i in range(9)]),
             "label_plan_too_many_operations"),
        ):
            with self.subTest(arguments=value):
                with self.assertRaises(StaticToolTargetRefused) as raised:
                    static_tool_target(LABEL_TOOL, value)
                self.assertEqual(raised.exception.reason_code, reason)

    def test_f3_routes(self):
        for identity in (CREATE_IDENTITY, UPDATE_IDENTITY):
            with self.subTest(identity=identity):
                requirements = f3_requirements(Prepared(identity))
                self.assertIn(LABEL_PROFILE, requirements)
                self.assertEqual(requirements, tuple(sorted((LABEL_PROFILE, VERIFICATION))))
        for prepared in (
            Prepared("create_label_registry_entry"),
            Prepared(CREATE_IDENTITY, operation=UPDATE_IDENTITY),
            Prepared(UPDATE_IDENTITY, operation="delete_label_registry_entry_configuration"),
            Prepared("delete_label_registry_entry_configuration"),
        ):
            with self.subTest(identity=prepared.capability_identity, op=prepared.operation):
                self.assertEqual(f3_requirements(prepared), UNSUPPORTED)

    def test_tool_policy_and_closed_schema(self):
        self.assertIn(LABEL_TOOL, ENGINEERING_STATIC_TOOL_NAMES)
        self.assertEqual(
            TOOL_CAPABILITY_POLICY[LABEL_TOOL], ProviderCapability.OPERATIONAL_ADMINISTRATION
        )
        schema = LABEL_PLAN_INPUT_SCHEMA
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["properties"]), {"operations", "expiration_minutes"})
        operations = schema["properties"]["operations"]
        self.assertEqual((operations["minItems"], operations["maxItems"]), (1, 8))
        item = operations["items"]
        self.assertIs(item["additionalProperties"], False)
        self.assertEqual(
            set(item["properties"]),
            {"operation", "label_id", "name", "color", "icon", "description", "entity_ids"},
        )
        self.assertEqual(item["properties"]["operation"]["enum"], ["create", "update"])
        self.assertEqual(item["properties"]["entity_ids"]["maxItems"], 8)
        self.assertIs(item["properties"]["entity_ids"]["uniqueItems"], True)
        self.assertEqual(len(boundary.THEME_COLORS), 26)
        for forbidden in ("delete", "area_id", "device_id"):
            self.assertNotIn(forbidden, json.dumps(schema))


class CatalogAndHealthTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def provider(self, **kwargs) -> dict:
        service = ChangeGovernanceService(
            ChangePlanRepository(Path(self.temp.name)), object(), **kwargs
        )
        return service.health_summary()["operational_administration"]["label_registry_provider"]

    def test_capability_shows_pending_admission(self):
        capability = capability_for_tool(LABEL_TOOL)
        self.assertEqual(capability["core_admission"], PENDING)
        self.assertEqual(capability["core_capabilities"], [LABEL_PROFILE, ENTITY_METADATA])
        self.assertEqual(
            capability["resource_types"], ["label_registry_entry", "entity_registry_metadata"]
        )
        self.assertIs(capability["rollback_available"], False)
        self.assertIs(capability["label_deletion"], False)
        self.assertEqual(capability["max_children_per_plan"], 8)
        self.assertEqual(capability["fallback"], "none")
        self.assertIs(capability["direct_write_allowed"], False)
        self.assertIs(capability["external_approval_required"], True)

    def test_health_shows_pending_admission_without_a_core_call(self):
        for kwargs, configured in (({"label_registry_gateway": object()}, True), ({}, False)):
            with self.subTest(configured=configured):
                provider = self.provider(**kwargs)
                self.assertEqual(provider["core_admission"], PENDING)
                self.assertIs(provider["configured"], configured)
                self.assertEqual(
                    provider["operational_status"],
                    "configured_unprobed" if configured else "unavailable",
                )
                self.assertEqual(provider["core_capabilities"], [LABEL_PROFILE])
                self.assertEqual(
                    provider["provider_contracts"],
                    {"label_registry_entry": boundary.LABEL_PROVIDER_CONTRACT},
                )
                self.assertIs(provider["label_deletion"], False)
                self.assertEqual(provider["fallback"], "none")
                self.assertEqual(provider["fallback_count"], 0)


# ---------------------------------------------------------------------------
# Real compiled and TEST-signed Core
# ---------------------------------------------------------------------------


class UnadmittedCompiledCoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime, _source = await core9.Core20269RuntimeTests()._runtime(
            _snapshot("2026.8.1", evidence=_evidence())
        )

    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    async def test_every_label_request_refuses_with_pending_admission(self):
        withheld = {
            item["capability_id"]
            for item in self.runtime.health_snapshot()["authority_profiles"]
            if not item["disposition"].startswith("admitted_")
        }
        self.assertIn(LABEL_PROFILE, withheld)
        self.assertIs(self.runtime.route_status((LABEL_PROFILE,))["available"], False)
        gateway, app = gateway_for(self.runtime)
        for value in (
            arguments(CREATE_HOLIDAY),
            arguments({**CREATE_HOLIDAY, "entity_ids": ["sensor.a"]}),
            arguments({"operation": "update", "label_id": "outdoor", "color": "red"}),
            # Even malformed input is refused for admission first.
            arguments({"operation": "delete", "label_id": "outdoor"}),
            {},
        ):
            with self.subTest(arguments=value):
                body = await call(gateway, LABEL_TOOL, value)
                self.assertIn(b"CoreCapabilityUnavailable", body)
                self.assertEqual(pending_details(body), PENDING_DETAILS)
        for identity in (CREATE_IDENTITY, UPDATE_IDENTITY):
            self.assertIsNone(self.runtime.acquire_f3(Prepared(
                identity,
                target=SimpleNamespace(target_type="label_registry_entry", target_id="holiday"),
            )))
        self.assertEqual(app.calls, [])
        self.assertEqual(self.runtime.health_snapshot()["fallback_count"], 0)


class SignedTestRegistryAdmissionTests(unittest.IsolatedAsyncioTestCase):
    """A TEST-signed entry with the exact reference admits the profile."""

    asyncSetUp = october.OctoberSignedSelectionTests.asyncSetUp
    publish = october.OctoberSignedSelectionTests.publish

    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    def with_references(self, fingerprint: str | None = None) -> dict:
        entry = deepcopy(self.entry)
        items = references()
        if fingerprint is not None:
            items = [{**item, "contract_fingerprint": fingerprint} for item in items]
        entry["capabilities"] = [*self.entry["capabilities"], *items]
        return entry

    async def test_signed_reference_admits_the_profile(self):
        health = await self.publish(self.with_references())
        self.assertEqual(health["compatible_count"], 23)
        self.assertIs(self.core.route_status((LABEL_PROFILE,))["available"], True)
        value = arguments({**CREATE_HOLIDAY, "entity_ids": ["sensor.a"]})
        requirements = static_tool_requirements(LABEL_TOOL, value)
        # A mutation-capable profile never issues a target-less lease.
        self.assertIsNone(self.core.acquire(requirements))
        authority = self.core.acquire(requirements, target=static_tool_target(LABEL_TOOL, value))
        self.assertIsNotNone(authority)
        self.core.finish(self.core.consume(authority))
        self.assertIsNone(self.core.acquire_f3(Prepared(CREATE_IDENTITY)))
        self.assertIsNotNone(self.core.acquire_f3(Prepared(
            CREATE_IDENTITY,
            target=SimpleNamespace(target_type="label_registry_entry", target_id="holiday"),
        )))
        gateway, app = gateway_for(self.core)
        body = await call(gateway, LABEL_TOOL, value)
        self.assertNotIn(b"CoreCapabilityUnavailable", body)
        self.assertEqual(len(app.calls), 1)
        body = await call(gateway, LABEL_TOOL, arguments({"operation": "delete"}))
        self.assertIn(b"InvalidStaticToolTarget", body)
        self.assertEqual(len(app.calls), 1)
        self.assertEqual(self.core.health_snapshot()["fallback_count"], 0)

    async def test_tampered_reference_keeps_the_profile_withheld(self):
        health = await self.publish(self.with_references("sha256:" + "0" * 64))
        self.assertEqual(health["compatible_count"], 22)
        gateway, app = gateway_for(self.core)
        body = await call(gateway, LABEL_TOOL, arguments(CREATE_HOLIDAY))
        self.assertEqual(pending_details(body), PENDING_DETAILS)
        self.assertEqual(app.calls, [])

    async def test_the_shipped_entry_withholds_the_profile(self):
        health = await self.publish()
        self.assertEqual(health["compatible_count"], 22)
        gateway, app = gateway_for(self.core)
        body = await call(gateway, LABEL_TOOL, arguments(CREATE_HOLIDAY))
        self.assertEqual(pending_details(body), PENDING_DETAILS)
        self.assertEqual(app.calls, [])


class ServiceTestCase(LabelServiceTestCase):
    async def signed_core(self) -> CoreRuntime:
        authority = SignedTestRegistryAdmissionTests()
        await authority.asyncSetUp()
        self.addAsyncCleanup(authority.asyncTearDown)
        self.addCleanup(authority.doCleanups)
        await authority.publish(authority.with_references())
        self.clock[0] = datetime.now(timezone.utc)
        self.runtime.core_runtime = authority.core
        return authority.core

    def assert_nothing_read_or_stored(self) -> None:
        self.assertEqual(self.ws.commands, [])
        self.assertEqual(self.snapshot_reads, [])
        self.assertEqual(self.repository.list(), [])

    def assert_refused(self, raised, reason_code: str) -> dict:
        details = raised.exception.details
        self.assertEqual(details["reason_code"], reason_code, details)
        self.assertIs(details["plan_created"], False)
        self.assertIs(details["provider_dispatch_occurred"], False)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.ws.writes(), [])
        return details

    def outcomes(self, task_id: str) -> list[str]:
        return [record.normalized_outcome for record in self.child_records(task_id) if record]


# ---------------------------------------------------------------------------
# Service-layer admission gate
# ---------------------------------------------------------------------------


class ServiceAdmissionGateTests(ServiceTestCase):
    core_admits = False

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.ws.add_label("outdoor", "Outdoor")
        self.add_entity("sensor.temp")
        self.ws.commands.clear()

    def assert_pending(self, raised) -> None:
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(raised.exception.details, PENDING_DETAILS)
        self.assert_nothing_read_or_stored()

    REQUESTS = (
        arguments(CREATE_HOLIDAY),
        arguments({**CREATE_HOLIDAY, "entity_ids": ["sensor.temp"]}),
        arguments({"operation": "update", "label_id": "outdoor", "color": "red"}),
        arguments({"operation": "update", "label_id": "outdoor", "name": "Garden",
                   "entity_ids": ["sensor.temp"]}),
        # Invalid input is still refused for admission first: zero reads.
        arguments({"operation": "delete", "label_id": "outdoor"}),
        {"operations": []},
        None,
    )

    async def test_unadmitted_core_refuses_every_request_with_zero_reads(self):
        for value in self.REQUESTS:
            with self.subTest(arguments=value):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_label_plan(deepcopy(value))
                self.assert_pending(raised)
        self.assertEqual(self.core.planning_leases, [])

    async def test_missing_or_broken_core_fails_closed(self):
        class Broken(LabelAdmittingCore):
            def route_status(self, capability_ids, **_):
                raise RuntimeError("synthetic")

        for label, setup in (
            ("no core runtime", lambda: setattr(self.runtime, "core_runtime", None)),
            ("broken core", lambda: setattr(self.runtime, "core_runtime", Broken())),
            ("no f3 runtime", lambda: setattr(self.service, "f3_runtime", None)),
        ):
            with self.subTest(label):
                setup()
                with self.assertRaises(GovernanceError) as raised:
                    await self.create(CREATE_HOLIDAY)
                self.assert_pending(raised)

    async def test_the_real_core_without_a_signed_reference_refuses(self):
        self.runtime.core_runtime = CoreRuntime()
        with self.assertRaises(GovernanceError) as raised:
            await self.create(CREATE_HOLIDAY)
        self.assert_pending(raised)

    async def test_only_the_label_profile_gates_label_plans(self):
        # The signed 069A profile alone does not admit a label plan.
        self.core.admitted = {ENTITY_METADATA}
        with self.assertRaises(GovernanceError) as raised:
            await self.create({**CREATE_HOLIDAY, "entity_ids": ["sensor.temp"]})
        self.assert_pending(raised)
        self.core.admitted = set(LabelAdmittingCore.GATED)
        created = await self.create(CREATE_HOLIDAY)
        self.assertTrue(created["plan_id"])
        self.assertEqual(len(self.repository.list()), 1)

    async def test_internal_builder_refuses_without_admission_or_lease(self):
        self.core.admitted = set(LabelAdmittingCore.GATED)
        request = planning.validate_request(arguments(CREATE_HOLIDAY))
        prepared = await planning.prepare(
            self.service.label_registry_gateway,
            self.service.entity_metadata_gateway,
            self.service.entity_metadata_snapshot_reader,
            request,
        )
        with self.assertRaises(GovernanceError) as raised:
            await self.service._create_label_plan(prepared)
        self.assertEqual(raised.exception.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED)
        self.core.withdraw()
        with self.assertRaises(GovernanceError) as raised:
            await self.service._create_label_plan(prepared, _before_persist=lambda: None)
        self.assertEqual(raised.exception.details, PENDING_DETAILS)
        self.assertEqual(self.repository.list(), [])

    async def test_generic_configuration_plan_cannot_carry_label_entries(self):
        self.core.admitted = set(LabelAdmittingCore.GATED)
        for action, target_id, proposed in (
            ("create", "holiday", {"name": "Holiday"}),
            ("update", "outdoor", {"name": "Outdoor", "color": "red"}),
            ("delete", "outdoor", {}),
        ):
            with self.subTest(action=action):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_configuration_plan(
                        title="t", description="d",
                        operations=[{
                            "operation_id": "x", "resource_type": "label_registry_entry",
                            "action": action, "target_id": target_id,
                            "proposed_config": proposed,
                        }],
                    )
                self.assertEqual(
                    raised.exception.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED
                )
                self.assert_nothing_read_or_stored()

    async def test_admission_withdrawn_during_planning_stores_nothing(self):
        self.core.admitted = set(LabelAdmittingCore.GATED)
        original = self.service.label_registry_gateway.entries

        async def withdrawing_entries(*args, **kwargs):
            self.core.withdraw()
            return await original(*args, **kwargs)

        self.service.label_registry_gateway.entries = withdrawing_entries
        with self.assertRaises(GovernanceError) as raised:
            await self.create(CREATE_HOLIDAY)
        self.assertIs(raised.exception.details["plan_created"], False)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.ws.writes(), [])

    async def test_admission_withdrawn_at_the_persist_boundary_stores_nothing(self):
        self.core.admitted = set(LabelAdmittingCore.GATED)
        original = self.service._create_configuration_plan

        async def withdraw_then_create(**kwargs):
            self.core.withdraw()
            return await original(**kwargs)

        self.service._create_configuration_plan = withdraw_then_create
        with self.assertRaises(GovernanceError):
            await self.create(CREATE_HOLIDAY)
        self.assertEqual(self.repository.list(), [])

    async def test_existing_plan_after_withdrawal_never_writes_across_restart(self):
        self.core.admitted = set(LabelAdmittingCore.GATED)
        created = await self.approved(CREATE_HOLIDAY)
        self.core.withdraw()
        # Refused at entry, before any execution-state persistence; the
        # dispatch-time gate is proved in test_hamcp165_review1_corrections.
        with self.assertRaises(GovernanceError) as refused:
            await self.apply(created)
        self.assertEqual(refused.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(
            refused.exception.details["reason_code"], "pending_signed_registry_admission"
        )
        self.assertIsNone(self.service.task_repository.get_for_plan(created["plan_id"]))
        await self.restart_runtime()
        await self.runtime.recover_once("synthetic_restart")
        self.assertEqual(self.ws.writes(), [])

    async def test_admitted_planning_holds_an_exact_target_lease(self):
        self.core.admitted = set(LabelAdmittingCore.GATED)
        await self.create(
            {"operation": "update", "label_id": "outdoor", "color": "red"},
            {**CREATE_HOLIDAY, "entity_ids": ["sensor.temp"]},
        )
        self.assertEqual(
            self.core.planning_leases,
            [(
                tuple(sorted((PLANNING, ENTITY_METADATA, LABEL_PROFILE))),
                {"target_type": "label_registry_entry", "target_id": "holiday,outdoor"},
            )],
        )
        self.assertGreater(self.core.revalidations, 0)
        self.assertEqual(self.ws.writes(), [])

    async def test_nested_under_a_gateway_lease_on_the_real_core(self):
        core = await self.signed_core()
        value = arguments(CREATE_HOLIDAY)
        outer = core.acquire(
            static_tool_requirements(LABEL_TOOL, value),
            target=static_tool_target(LABEL_TOOL, value),
        )
        commits = core.consume(outer)
        try:
            created = await self.service.create_label_plan(value)
        finally:
            core.finish(commits)
        self.assertEqual(len(self.repository.list()), 1)
        self.assertTrue(created["plan_id"])


# ---------------------------------------------------------------------------
# End to end: plan -> approve -> apply -> readback
# ---------------------------------------------------------------------------


class EndToEndTests(ServiceTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.ws.add_label("outdoor", "Outdoor", color="green")
        self.add_entity("sensor.temp")
        self.add_entity("sensor.humidity", labels=["outdoor"])

    async def run_plan(self, *operations: dict) -> tuple[dict, dict]:
        before = len(self.ws.writes())
        created = await self.approved(*operations)
        self.assertEqual(len(self.ws.writes()), before)
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(
            set(self.outcomes(result["task_id"])), {"succeeded_verified"}, result
        )
        self.assert_never_sent()
        self.assertTrue(
            self.ws.command_types() <= {LABEL_LIST, LABEL_CREATE, LABEL_UPDATE, UPDATE}
            | {"config/entity_registry/get", "config/entity_registry/list"},
            self.ws.command_types(),
        )
        return created, result

    async def test_create_with_every_field(self):
        created, _result = await self.run_plan({
            "operation": "create", "name": "Holiday Lights", "color": "#00aa55",
            "icon": "mdi:pine-tree", "description": "Seasonal lighting",
        })
        self.assertEqual(created["policy_decision"]["policy_class"], "standard_admin")
        self.assertEqual(created["policy_decision"]["physical_consequence"], "none")
        self.assertIn("label_registry_standard_policy",
                      created["policy_decision"]["reason_codes"])
        self.assertEqual(self.ws.label_writes(), [{
            "type": LABEL_CREATE, "name": "Holiday Lights", "color": "#00aa55",
            "icon": "mdi:pine-tree", "description": "Seasonal lighting",
        }])
        entry = self.ws.label("holiday_lights")
        self.assertEqual(
            (entry["name"], entry["color"], entry["icon"], entry["description"]),
            ("Holiday Lights", "#00aa55", "mdi:pine-tree", "Seasonal lighting"),
        )
        self.assertEqual(core_slugify("Holiday Lights"), "holiday_lights")

    async def test_edit_sends_only_the_changed_fields(self):
        await self.run_plan({
            "operation": "update", "label_id": "outdoor", "name": "Garden",
            "color": "green", "description": "Back yard",
        })
        (write,) = self.ws.label_writes()
        self.assertEqual(write, {
            "type": LABEL_UPDATE, "label_id": "outdoor", "name": "Garden",
            "description": "Back yard",
        })
        entry = self.ws.label("outdoor")
        self.assertEqual((entry["name"], entry["color"], entry["description"]),
                         ("Garden", "green", "Back yard"))

    async def test_case_only_rename_of_the_same_label_is_allowed(self):
        await self.run_plan({"operation": "update", "label_id": "outdoor", "name": "OUTDOOR"})
        self.assertEqual(self.ws.label("outdoor")["name"], "OUTDOOR")

    async def test_create_then_assign_runs_in_order(self):
        created, _result = await self.run_plan(
            {**CREATE_HOLIDAY, "entity_ids": ["sensor.temp", "sensor.humidity"]}
        )
        operations = created["operations"]
        self.assertEqual(
            [(item["operation_id"], item.get("depends_on", [])) for item in operations],
            [("label_1", []), ("assign_1", ["label_1"]), ("assign_2", ["label_1"])],
        )
        self.assertEqual(
            [write["type"] for write in self.ws.writes()], [LABEL_CREATE, UPDATE, UPDATE]
        )
        self.assertEqual(self.ws.entries["sensor.temp"]["labels"], ["holiday"])
        self.assertEqual(
            sorted(self.ws.entries["sensor.humidity"]["labels"]), ["holiday", "outdoor"]
        )
        # Each assignment child sends only the merged labels list.
        for write in self.ws.writes()[1:]:
            self.assertEqual(set(write) - {"id"}, {"type", "entity_id", "labels"})

    async def test_edit_and_create_then_assign_both(self):
        await self.run_plan(
            {"operation": "update", "label_id": "outdoor", "color": "red",
             "entity_ids": ["sensor.temp"]},
            {**CREATE_HOLIDAY, "entity_ids": ["sensor.temp"]},
        )
        self.assertEqual(sorted(self.ws.entries["sensor.temp"]["labels"]),
                         ["holiday", "outdoor"])
        self.assertEqual(self.ws.label("outdoor")["color"], "red")
        # One merged child per entity.
        self.assertEqual([w["type"] for w in self.ws.writes()],
                         [LABEL_UPDATE, LABEL_CREATE, UPDATE])

    async def test_a_light_assignment_keeps_its_069a_classification(self):
        self.add_entity("light.porch")
        created = await self.create({**CREATE_HOLIDAY, "entity_ids": ["light.porch"]})
        self.assertEqual(created["policy_decision"]["policy_class"], "elevated_admin")
        plain = await self.create({"operation": "create", "name": "Porch"})
        self.assertEqual(plain["policy_decision"]["policy_class"], "standard_admin")

    async def test_plan_discloses_non_atomicity_and_no_rollback(self):
        created = await self.create({**CREATE_HOLIDAY, "entity_ids": ["sensor.temp"]})
        text = json.dumps(created)
        self.assertIn(planning.NON_ATOMIC_DISCLOSURE, text)
        self.assertIn(planning.ROLLBACK_DISCLOSURE, text)

    async def test_audit_records_plan_approval_apply_and_verification(self):
        created, _result = await self.run_plan({**CREATE_HOLIDAY, "entity_ids": ["sensor.temp"]})
        events = [item["event"] for item in self.audit_events()]
        for event in (
            "change_plan_created", "external_approval_granted", "approval_consumed",
            "f3_dispatch_intent_committed", "f3_verification_recorded", "task_completed",
        ):
            self.assertIn(event, events)
        # One dispatch per planned child, nothing else.
        self.assertEqual(events.count("f3_dispatch_intent_committed"), 2)
        self.assertEqual(events.count("dispatch_attempted"), 2)
        (plan_created,) = [
            item for item in self.audit_events() if item["event"] == "change_plan_created"
        ]
        self.assertEqual(plan_created["plan_id"], created["plan_id"])
        self.assertEqual(plan_created["policy_class"], "standard_admin")
        self.assertEqual(plan_created["physical_consequence"], "none")
        self.assertEqual(plan_created["caller_id"], "hamcp165-mcp-caller")

    async def test_signed_test_registry_end_to_end(self):
        """The real CoreRuntime with a TEST-signed reference: create, edit, assign."""

        await self.signed_core()
        await self.run_plan({**CREATE_HOLIDAY, "color": "red", "entity_ids": ["sensor.temp"]})
        await self.run_plan({"operation": "update", "label_id": "holiday",
                             "color": "blue", "description": "Edited"})
        entry = self.ws.label("holiday")
        self.assertEqual((entry["color"], entry["description"]), ("blue", "Edited"))
        self.assertEqual(self.ws.entries["sensor.temp"]["labels"], ["holiday"])
        self.assertEqual(
            [w["type"] for w in self.ws.writes()], [LABEL_CREATE, UPDATE, LABEL_UPDATE]
        )


# ---------------------------------------------------------------------------
# Refusals: nothing stored, nothing written
# ---------------------------------------------------------------------------


class ValidationRefusalTests(ServiceTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add_entity("sensor.temp")
        self.ws.commands.clear()

    CASES = (
        ("invalid_label_request", None),
        ("invalid_label_request", {"operations": []}),
        ("invalid_label_request", {"operations": "x"}),
        ("invalid_label_request", arguments({"operation": "delete", "label_id": "outdoor"})),
        ("invalid_label_request", arguments({"operation": "rename", "name": "X"})),
        ("invalid_label_request", {**arguments(CREATE_HOLIDAY), "expiration_minutes": 1}),
        ("invalid_label_request", arguments({**CREATE_HOLIDAY, "entity_ids": ["Bad.Id"]})),
        ("label_field_not_supported", {**arguments(CREATE_HOLIDAY), "reason": "x"}),
        ("label_field_not_supported", arguments({**CREATE_HOLIDAY, "label_id": "holiday"})),
        ("label_field_not_supported", arguments({**CREATE_HOLIDAY, "area_id": "kitchen"})),
        ("label_field_not_supported", arguments({**CREATE_HOLIDAY, "device_id": "d1"})),
        ("label_field_not_supported", arguments({**CREATE_HOLIDAY, "delete": True})),
        ("label_plan_too_many_operations",
         arguments(*[{"operation": "create", "name": f"L{i}"} for i in range(9)])),
        ("label_plan_too_many_operations",
         arguments({**CREATE_HOLIDAY, "entity_ids": [f"sensor.s{i}" for i in range(8)]})),
        ("invalid_label_id", arguments({"operation": "update", "label_id": "Bad-Id",
                                        "color": "red"})),
        ("invalid_label_id", arguments({"operation": "update", "color": "red"})),
        ("invalid_label_name", arguments({"operation": "create"})),
        ("invalid_label_name", arguments({"operation": "create", "name": ""})),
        ("invalid_label_name", arguments({"operation": "create", "name": " Lead"})),
        ("invalid_label_name", arguments({"operation": "create", "name": "Trail-"})),
        ("invalid_label_name", arguments({"operation": "create", "name": "x" * 51})),
        ("invalid_label_name", arguments({"operation": "create", "name": "Café"})),
        ("invalid_label_name", arguments({"operation": "create", "name": 5})),
        ("invalid_label_name", arguments({"operation": "update", "label_id": "a",
                                          "name": None})),
        ("invalid_label_color", arguments({**CREATE_HOLIDAY, "color": "chartreuse"})),
        ("invalid_label_color", arguments({**CREATE_HOLIDAY, "color": "Red"})),
        ("invalid_label_color", arguments({**CREATE_HOLIDAY, "color": "#12345"})),
        ("invalid_label_color", arguments({**CREATE_HOLIDAY, "color": "#GGGGGG"})),
        ("invalid_label_color", arguments({**CREATE_HOLIDAY, "color": 7})),
        ("invalid_label_icon", arguments({**CREATE_HOLIDAY, "icon": "mdi"})),
        ("invalid_label_icon", arguments({**CREATE_HOLIDAY, "icon": "MDI:Home"})),
        ("invalid_label_icon", arguments({**CREATE_HOLIDAY, "icon": "mdi:home:x"})),
        ("invalid_label_icon", arguments({**CREATE_HOLIDAY, "icon": "mdi: home"})),
        ("invalid_label_description", arguments({**CREATE_HOLIDAY, "description": ""})),
        ("invalid_label_description", arguments({**CREATE_HOLIDAY, "description": "x" * 256})),
        ("invalid_label_description", arguments({**CREATE_HOLIDAY, "description": "a\nb"})),
        ("label_no_change", arguments({"operation": "update", "label_id": "outdoor"})),
        ("duplicate_label_request", arguments(
            {"operation": "update", "label_id": "outdoor", "color": "red"},
            {"operation": "update", "label_id": "outdoor", "icon": "mdi:tree"},
        )),
        ("duplicate_label_request", arguments(CREATE_HOLIDAY, {"operation": "create",
                                                               "name": "HOLIDAY"})),
        # Same normalized name, different slugs (front_door / frontdoor): only the
        # pre-read normalized-name check refuses this before any registry read.
        ("duplicate_label_request", arguments({"operation": "create", "name": "Front Door"},
                                              {"operation": "create", "name": "FrontDoor"})),
        ("duplicate_label_request", arguments(
            {"operation": "create", "name": "Holiday Lights"},
            {"operation": "update", "label_id": "holiday_lights", "color": "red"},
        )),
        ("duplicate_label_request", arguments(
            {**CREATE_HOLIDAY, "entity_ids": ["sensor.temp", "sensor.temp"]}
        )),
    )

    async def test_every_validation_refusal_precedes_any_read(self):
        for reason_code, value in self.CASES:
            with self.subTest(reason_code=reason_code, arguments=value):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_label_plan(deepcopy(value))
                self.assertEqual(raised.exception.code, ErrorCode.INVALID_REQUEST)
                self.assert_refused(raised, reason_code)
                self.assert_nothing_read_or_stored()
        self.assertEqual(self.core.planning_leases, [])

    async def test_valid_edge_values_are_accepted(self):
        for value in (
            {"operation": "create", "name": "A"},
            {"operation": "create", "name": "x" * 50},
            {"operation": "create", "name": "Kids_Room-2", "color": "deep-purple",
             "icon": "hass:home-1", "description": "d" * 255},
            {"operation": "create", "name": "Plain", "color": None, "icon": None,
             "description": None},
        ):
            with self.subTest(arguments=value):
                planning.validate_request(arguments(value))
        for color in boundary.THEME_COLORS:
            planning.validate_request(arguments({**CREATE_HOLIDAY, "color": color}))


class RegistryRefusalTests(ServiceTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.ws.add_label("outdoor", "Outdoor")
        self.ws.add_label("garden", "Garden")
        # A label renamed away from its original name keeps its slug id.
        self.ws.add_label("holiday_lights", "Seasonal")
        self.add_entity("sensor.temp", labels=["outdoor"])

    async def refused(self, reason_code: str, *operations: dict) -> dict:
        with self.assertRaises(GovernanceError) as raised:
            await self.create(*operations)
        return self.assert_refused(raised, reason_code)

    async def test_duplicate_name_is_refused_with_the_holder(self):
        for name in ("Garden", "garden", "GAR DEN", "Gar den"):
            with self.subTest(name=name):
                details = await self.refused(
                    "label_already_exists", {"operation": "create", "name": name}
                )
                self.assertEqual(details["label_id"], "garden")

    async def test_rename_onto_another_labels_name_is_refused(self):
        await self.refused(
            "label_name_in_use",
            {"operation": "update", "label_id": "outdoor", "name": "gArDeN"},
        )

    async def test_name_taken_earlier_in_the_same_plan_is_refused(self):
        await self.refused(
            "duplicate_label_request",
            {"operation": "update", "label_id": "outdoor", "name": "Patio"},
            {"operation": "create", "name": "Patio"},
        )

    async def test_slug_collision_is_refused_not_suffixed(self):
        details = await self.refused(
            "label_id_collision", {"operation": "create", "name": "Holiday Lights"}
        )
        self.assertNotIn("holiday_lights_2", json.dumps(details))
        await self.refused("label_id_collision", {"operation": "create", "name": "Holiday-Lights"})

    async def test_unknown_label_is_refused_not_found(self):
        with self.assertRaises(GovernanceError) as raised:
            await self.create({"operation": "update", "label_id": "missing", "color": "red"})
        self.assertEqual(raised.exception.code, ErrorCode.ENTITY_NOT_FOUND)
        self.assert_refused(raised, "label_not_found")

    async def test_update_that_changes_nothing_is_refused(self):
        await self.refused(
            "label_no_change",
            {"operation": "update", "label_id": "outdoor", "name": "Outdoor", "color": None},
        )

    async def test_assignment_already_present_is_refused(self):
        await self.refused(
            "entity_label_already_assigned",
            {"operation": "update", "label_id": "outdoor", "color": "red",
             "entity_ids": ["sensor.temp"]},
        )

    async def test_missing_label_gateway_is_unavailable(self):
        self.service.label_registry_gateway = None
        self.ws.commands.clear()
        with self.assertRaises(GovernanceError) as raised:
            await self.create(CREATE_HOLIDAY)
        self.assertEqual(raised.exception.code, ErrorCode.HA_UNAVAILABLE)
        self.assert_refused(raised, "label_registry_provider_unavailable")
        self.assertEqual(self.ws.commands, [])


# ---------------------------------------------------------------------------
# Dispatch-time stale state, readback, partial results
# ---------------------------------------------------------------------------


class ApplyBoundaryTests(ServiceTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.ws.add_label("outdoor", "Outdoor")
        self.add_entity("sensor.temp")

    async def applied(self, *operations: dict) -> dict:
        created = await self.approved(*operations)
        return await self.apply(created)

    def assert_outcome(self, result, task_state, outcomes, codes=()) -> None:
        self.assertEqual(result["task_state"], task_state, result)
        self.assertEqual(self.outcomes(result["task_id"]), list(outcomes))
        self.assertTrue(set(codes) <= self.child_codes(result["task_id"]),
                        self.child_codes(result["task_id"]))
        self.assert_never_sent()

    async def test_external_edit_before_dispatch_is_refused(self):
        created = await self.approved({"operation": "update", "label_id": "outdoor",
                                       "color": "red"})
        self.ws.set_external_label("outdoor", color="blue")
        result = await self.apply(created)
        self.assert_outcome(result, "failed_pre_dispatch", ["preflight_rejected"],
                            ["stale_target_state"])
        self.assertEqual(self.ws.writes(), [])
        self.assertEqual(self.ws.label("outdoor")["color"], "blue")

    async def test_namespace_change_before_dispatch_is_refused(self):
        created = await self.approved(CREATE_HOLIDAY)
        self.ws.add_label("patio", "Patio")
        result = await self.apply(created)
        self.assert_outcome(result, "failed_pre_dispatch", ["preflight_rejected"],
                            ["label_namespace_changed"])
        self.assertEqual(self.ws.writes(), [])

    async def test_create_target_appearing_before_dispatch_is_refused(self):
        created = await self.approved(CREATE_HOLIDAY)
        self.ws.add_label("holiday", "Holiday")
        result = await self.apply(created)
        self.assert_outcome(result, "failed_pre_dispatch", ["preflight_rejected"],
                            ["target_already_exists"])
        self.assertEqual(self.ws.writes(), [])

    async def test_readback_mismatch_is_never_verified(self):
        for label, setup in (
            ("drifted field", lambda: setattr(self.ws, "drift", {"color": "blue"})),
            ("write ignored", lambda: setattr(self.ws, "ignore_writes", True)),
        ):
            with self.subTest(label):
                setup()
                result = await self.applied({"operation": "create", "name": f"Mismatch {label[0]}",
                                             "color": "red"})
                self.assert_outcome(
                    result, "failed_post_dispatch", ["verification_mismatch"],
                    ["exact_readback_not_verified"],
                )
                self.assertNotIn("exact_readback_verified", self.child_codes(result["task_id"]))
                self.ws.drift, self.ws.ignore_writes = {}, False

    async def test_different_generated_id_is_never_verified(self):
        self.ws.generated_id_override = "holiday_2"
        result = await self.applied({**CREATE_HOLIDAY, "entity_ids": ["sensor.temp"]})
        self.assert_outcome(result, "failed_post_dispatch", ["verification_mismatch"],
                            ["provider_completed_unexpectedly"])
        # Stop on first failure: the assignment is never attempted.
        self.assertEqual([w["type"] for w in self.ws.writes()], [LABEL_CREATE])
        self.assertEqual(self.ws.entries["sensor.temp"]["labels"], [])

    async def test_error_after_send_is_indeterminate_not_verified(self):
        created = await self.approved({"operation": "update", "label_id": "outdoor",
                                       "name": "Patio"})
        self.ws.failures[LABEL_UPDATE] = HomeAssistantApiError(
            "synthetic", details={"provider_response_received": True}
        )
        result = await self.apply(created)
        self.assert_outcome(result, "failed_post_dispatch", ["verification_mismatch"],
                            ["provider_error_after_send"])
        self.assertEqual(len(self.ws.writes()), 1)

    async def test_refused_write_stops_the_plan(self):
        created = await self.approved(CREATE_HOLIDAY, {"operation": "create", "name": "Patio"})
        self.ws.failures[LABEL_CREATE] = AuthorizationError(
            "synthetic", details={"provider_response_received": True}
        )
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        self.assertEqual(self.outcomes(result["task_id"])[0], "dispatch_failed_confirmed")
        self.assertNotIn("succeeded_verified", self.outcomes(result["task_id"]))
        self.assertEqual(len(self.ws.writes()), 1)
        self.assertIsNone(self.ws.label("patio"))

    async def test_partial_result_is_truthful_and_the_label_persists(self):
        created = await self.approved({**CREATE_HOLIDAY, "entity_ids": ["sensor.temp"]})
        self.ws.failures[UPDATE] = AuthorizationError(
            "synthetic", details={"provider_response_received": True}
        )
        result = await self.apply(created)
        self.assert_outcome(
            result, "manual_review_required",
            ["succeeded_verified", "dispatch_failed_confirmed"],
        )
        self.assertIsNotNone(self.ws.label("holiday"))
        self.assertEqual(self.ws.entries["sensor.temp"]["labels"], [])

    async def test_rollback_is_refused_without_reads_or_plans(self):
        created = await self.approved(CREATE_HOLIDAY)
        await self.apply(created)
        commands, plans = len(self.ws.commands), len(self.repository.list())
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"], created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertEqual(raised.exception.details["reason_code"],
                         planning.ROLLBACK_NOT_SUPPORTED)
        self.assertIs(raised.exception.details["provider_dispatch_occurred"], False)
        self.assertEqual(len(self.ws.commands), commands)
        self.assertEqual(len(self.repository.list()), plans)
        self.assertIsNotNone(self.ws.label("holiday"))

    async def test_no_self_approval(self):
        created = await self.create(CREATE_HOLIDAY)
        granted = self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertEqual(granted["status"], "approval_pending", granted)
        self.assertIs(granted["external_approval_required"], True)
        with self.assertRaises(GovernanceError) as raised:
            await self.apply(created)
        self.assertEqual(raised.exception.code, ErrorCode.APPROVAL_HASH_MISMATCH)
        with self.assertRaises(GovernanceError):
            await self.service.apply(created["plan_id"], "sha256:" + "0" * 64)
        self.assertEqual(self.ws.writes(), [])


class PolicyTamperingTests(ServiceTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.ws.add_label("outdoor", "Outdoor")
        self.add_entity("sensor.temp")
        self.add_entity("sensor.other")

    async def planned(self, *operations: dict):
        created = await self.create(*operations)
        return self.repository.get(created["plan_id"])

    def assert_prohibited(self, plan) -> None:
        decision = evaluate_change_policy(plan)
        self.assertEqual(decision.policy_class, ApprovalPolicyClass.PROHIBITED)
        self.assertIn("label_registry_execution_contract_incomplete", decision.reason_codes)

    async def test_shipped_plan_is_standard(self):
        plan = await self.planned({**CREATE_HOLIDAY, "entity_ids": ["sensor.temp"]})
        self.assertEqual(evaluate_change_policy(plan).policy_class,
                         ApprovalPolicyClass.STANDARD_ADMIN)

    async def test_tampered_plan_shapes_are_prohibited(self):
        plan = await self.planned({**CREATE_HOLIDAY, "entity_ids": ["sensor.temp"]})
        label, assign = plan.operations
        other = await self.planned({"operation": "create", "name": "Patio",
                                    "entity_ids": ["sensor.other"]})
        for name, operations in (
            ("assignment first", [assign, label]),
            ("missing dependency", [label, replace(assign, depends_on=[])]),
            ("dependency on another plan", [label, other.operations[1]]),
            ("label depends on something", [replace(label, depends_on=["assign_1"]), assign]),
            ("duplicate label target", [label, replace(label, operation_id="label_2")]),
            ("adds an undeclared label", [label, replace(assign, proposed_config={
                **assign.proposed_config, "labels": ["holiday", "outdoor"]})]),
            ("too many children", [label] + [assign] * 8),
        ):
            with self.subTest(name):
                tampered = deepcopy(plan)
                tampered.operations = operations
                self.assert_prohibited(tampered)

    async def test_tampered_label_proposal_is_prohibited(self):
        plan = await self.planned(CREATE_HOLIDAY)
        (label,) = plan.operations
        for proposed in (
            {**label.proposed_config, "color": "chartreuse"},
            {**label.proposed_config, "label_id": "other"},
            {**label.proposed_config, "area_id": "kitchen"},
        ):
            with self.subTest(proposed=proposed):
                tampered = deepcopy(plan)
                tampered.operations = [replace(label, proposed_config=proposed)]
                decision = evaluate_change_policy(tampered)
                self.assertEqual(decision.policy_class, ApprovalPolicyClass.PROHIBITED)


# ---------------------------------------------------------------------------
# Closed vocabulary and the never-sent boundary
# ---------------------------------------------------------------------------


class NeverSentBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_vocabulary_is_closed_and_disjoint(self):
        self.assertEqual(set(boundary.READ_COMMANDS), {LABEL_LIST})
        self.assertEqual(set(boundary.ALLOWED_COMMANDS), {LABEL_LIST, LABEL_CREATE, LABEL_UPDATE})
        self.assertEqual(set(boundary.NEVER_SENT_COMMANDS), {LABEL_DELETE})
        self.assertFalse(set(boundary.NEVER_SENT_COMMANDS) & set(boundary.ALLOWED_COMMANDS))

    def test_delete_appears_only_in_the_never_sent_list(self):
        """Static check: the delete command is never a sendable value."""

        occurrences = []
        for path in sorted(ENGINE.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if LABEL_DELETE not in text:
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
                # The profile contract's declared never-sent list.
                if isinstance(node, ast.Dict):
                    for key, value in zip(node.keys, node.values):
                        if isinstance(key, ast.Constant) and key.value == "never_sent":
                            never_sent |= {id(item) for item in ast.walk(value)}
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)) and ast.get_docstring(node):
                    docstrings.add(id(node.body[0].value))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and LABEL_DELETE in node.value:
                    kind = (
                        "never_sent" if id(node) in never_sent
                        else "docstring" if id(node) in docstrings
                        else "value"
                    )
                    occurrences.append((path.relative_to(ENGINE).as_posix(), kind))
        self.assertNotIn("value", {kind for _path, kind in occurrences}, occurrences)
        self.assertIn(("governance/label_registry.py", "never_sent"), occurrences)
        self.assertIn(("ha_core_readmission/profiles.py", "never_sent"), occurrences)

    async def test_gateway_refuses_never_sent_and_unknown_commands_before_io(self):
        class Socket:
            def __init__(self):
                self.commands = []

            async def command(self, message):
                self.commands.append(message)
                return None

        socket = Socket()
        gateway = boundary.LabelRegistryGateway(socket)
        for kind in (LABEL_DELETE, "config/label_registry/remove",
                     "config/entity_registry/update", "call_service", "", None):
            with self.subTest(command=kind):
                with self.assertRaises(boundary.LabelRegistryContractError):
                    await gateway._send({"type": kind})
        self.assertEqual(socket.commands, [])


class GovernanceToolTests(ServiceTestCase):
    async def test_tool_reaches_the_service_and_creates_one_plan(self):
        from ha_mcp_engineering.tools import governance as tools

        with patch.object(tools.GOVERNANCE, "service", self.service):
            body = await tools.create_label_plan(operations=[CREATE_HOLIDAY])
        self.assertIn("plan_id", body)
        self.assertEqual(len(self.repository.list()), 1)
        self.assertEqual(self.ws.writes(), [])

    async def test_tool_refuses_while_unadmitted(self):
        from ha_mcp_engineering.tools import governance as tools

        self.core.withdraw()
        with patch.object(tools.GOVERNANCE, "service", self.service):
            body = await tools.create_label_plan(operations=[CREATE_HOLIDAY])
        self.assertIn(PENDING, body)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.ws.commands, [])


if __name__ == "__main__":
    unittest.main()
