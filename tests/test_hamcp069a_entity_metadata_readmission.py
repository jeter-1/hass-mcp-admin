"""HAMCP-069A-1 Core readmission, health, static-route and upstream boundaries.

The entity metadata profile is dormant: it is a runtime profile that no
compiled authority and no signed probe fingerprint covers, so on every
supported Core it is admitted only by an exact owner-signed reference. While
unadmitted, the planner fails closed before the MCP application and the F3
apply identity cannot acquire a route.  Upstream ``ha_set_entity`` remains a
prohibited delegated write.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering import entity_ids  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.governance import (  # noqa: E402
    entity_metadata,
    entity_metadata_planning,
)
from ha_mcp_engineering.governance.entity_metadata_planning import (  # noqa: E402
    EntityMetadataRefusal,
    validate_request,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.ha_core_readmission.probe_profiles import (  # noqa: E402
    CHILD_DEVICE_NAME_PARTS_PROBE_PROFILE,
    CHILD_DEVICE_PROBE_PROFILE,
    LEGACY_PROBE_PROFILE,
)
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_CAPABILITY_PROFILES,
    CORE_ENTITY_METADATA_PROFILES,
    CORE_RUNTIME_CAPABILITY_PROFILES,
    SUPPORTED_CORE_RELEASES,
    compiled_exact_authority,
)
from ha_mcp_engineering.ha_core_readmission import routes  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.runtime import CoreRuntime  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    STATIC_TARGET_INVALID_ENTITY_ID,
    STATIC_TOOL_CORE_REQUIREMENTS,
    StaticToolTargetRefused,
    f3_requirements,
    static_tool_requirements,
    static_tool_target,
)
from ha_mcp_engineering.providers.routing import (  # noqa: E402
    TOOL_CAPABILITY_POLICY,
    ProviderCapability,
)
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    ALLOWED_UPSTREAM_TOOLS,
    PROHIBITED_UPSTREAM_TOOLS,
)
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration  # noqa: E402
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.tools.governance import (  # noqa: E402
    ENTITY_METADATA_INPUT_SCHEMA,
)
from ha_mcp_engineering.tools.registry import (  # noqa: E402
    ENGINEERING_STATIC_TOOL_NAMES,
)

# Imported as modules so that their test classes are not collected here.
import tests.test_core_2026_10_device_registry as october  # noqa: E402
import tests.test_ha_core_2026_9_integration as core9  # noqa: E402
from tests.hamcp069a_entity_fixtures import (  # noqa: E402
    EntityMetadataServiceTestCase,
    _provider_identity,
)
from tests.test_ha_core_capability_auto_readmission import (  # noqa: E402
    _evidence,
    _snapshot,
)
from tests.test_readonly_upstream_gateway import settings  # noqa: E402


ENTITY_METADATA = "core.entity_registry_metadata_operation"
PLANNING = "core.dependency_helper_planning"
VERIFICATION = "core.f3_mutation_verification"
F3_IDENTITY = "update_entity_registry_metadata_configuration"
ENGINE = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
TARGET = {"target_type": "entity_registry_metadata", "target_id": "light.porch"}
ARGUMENTS = {"entity_id": "light.porch", "name": "x"}
MISSING = object()
AT_LIMIT = "light." + "a" * (255 - len("light."))
OVER_LIMIT = AT_LIMIT + "a"

# One case table for both callers of the shared validator (R1).  Each value
# is either accepted by admission and the planner or refused by both.
ENTITY_ID_CASES = (
    ("valid", "light.porch", True),
    ("valid_digits_underscores", "sensor.porch_temp_2", True),
    ("exactly_255", AT_LIMIT, True),
    ("missing", MISSING, False),
    ("null", None, False),
    ("number", 3, False),
    ("float", 3.5, False),
    ("bool", True, False),
    ("list", ["light.porch"], False),
    ("object", {"entity_id": "light.porch"}, False),
    ("empty", "", False),
    ("not_an_entity", "not-an-entity", False),
    ("slash", "light.porch/other", False),
    ("uppercase", "LIGHT.PORCH", False),
    ("leading_space", " light.porch", False),
    ("trailing_space", "light.porch ", False),
    ("trailing_newline", "light.porch\n", False),
    ("missing_dot", "lightporch", False),
    ("two_dots", "light.porch.other", False),
    ("wildcard", "light.*", False),
    ("overlength", "light." + "a" * 256, False),
    ("exactly_256", OVER_LIMIT, False),
)
INVALID_ENTITY_ID_CASES = tuple(case for case in ENTITY_ID_CASES if not case[2])


def entity_arguments(value) -> dict:
    arguments = {"name": "x"}
    if value is not MISSING:
        arguments["entity_id"] = value
    return arguments


class RecordingCore:
    """Wraps a real Core runtime and records every gateway acquisition."""

    def __init__(self, core):
        self.core = core
        self.acquired: list[tuple[tuple, dict]] = []
        self.consumed = 0

    def acquire(self, *args, **kwargs):
        self.acquired.append((args, kwargs))
        return self.core.acquire(*args, **kwargs)

    def consume(self, authority):
        self.consumed += 1
        return self.core.consume(authority)

    def __getattr__(self, name):
        return getattr(self.core, name)

# Static tools whose static route acquires no Core authority, reviewed at
# 069A-1.  ``server_info`` and ``get_server_health`` are conditional (only
# ``check_ha: false`` is empty) and are therefore not listed.  Adding a tool
# here is a reviewed decision; an unmapped new tool fails the completeness
# test instead of silently skipping Core acquisition.
REVIEWED_EMPTY_STATIC_ROUTES = frozenset(
    {
        "apply_change_plan",
        "approve_change_plan",
        "call_service",
        "cancel_execution_task",
        "control_fan",
        "control_power",
        "delete_automation",
        "get_audit_log",
        "get_blueprint",
        "get_change_plan",
        "get_core_log_history",
        "get_execution_task",
        "get_incident_logs",
        "list_capabilities",
        "list_change_plans",
        "list_execution_tasks",
        "reload_domain",
        "reverify_configuration_task",
        "rollback_change",
        "run_held_read_canary",
        "upsert_automation",
    }
)


class Prepared:
    def __init__(self, identity: str, operation: str | None = None, target=None):
        self.capability_id = identity
        self.capability_identity = identity
        self.operation = identity if operation is None else operation
        self.target = target


def ids(profiles) -> set[str]:
    return {profile.capability_id for profile in profiles}


def gateway_for(runtime) -> tuple[AuthenticatedMcpGateway, core9._RecordingMcpApp]:
    app = core9._RecordingMcpApp()
    configured = settings()
    gateway = AuthenticatedMcpGateway(
        app,
        configured,
        AuditLogger("unused", configured.access_secret, enabled=False),
        core_runtime=runtime,
    )
    return gateway, app


async def call(gateway, tool_name: str, arguments: dict | None = None) -> bytes:
    return await core9.Core20269StaticRouteTests._call(gateway, tool_name, arguments)


class DormantProfileTests(unittest.TestCase):
    def test_profile_is_runtime_only_and_outside_compiled_authority(self):
        self.assertEqual(ids(CORE_ENTITY_METADATA_PROFILES), {ENTITY_METADATA})
        self.assertIn(ENTITY_METADATA, ids(CORE_RUNTIME_CAPABILITY_PROFILES))
        self.assertNotIn(ENTITY_METADATA, ids(CORE_CAPABILITY_PROFILES))
        self.assertEqual(len(CORE_CAPABILITY_PROFILES), 17)
        # 22 through 069A, plus the two dormant 069B profiles and the
        # dormant 165 label profile.
        self.assertEqual(len(CORE_RUNTIME_CAPABILITY_PROFILES), 25)
        (profile,) = CORE_ENTITY_METADATA_PROFILES
        mapping = profile.to_mapping()
        self.assertEqual(
            mapping["adapter_id"], "compiled-core-entity-registry-metadata-operation-v1"
        )
        self.assertTrue(profile.capability_class.mutation_capable)
        # 069A-1 does not implement label creation (design §15 V3).
        self.assertNotIn("websocket_label_registry_create", mapping["required_checks"])
        for version, _commit, _digest in SUPPORTED_CORE_RELEASES:
            with self.subTest(version=version):
                self.assertNotIn(
                    ENTITY_METADATA,
                    {
                        capability_id
                        for selection in compiled_exact_authority(version)
                        for capability_id in selection.capability_ids
                    },
                )

    def test_signed_probe_fingerprints_are_unchanged(self):
        self.assertEqual(
            LEGACY_PROBE_PROFILE.contract_fingerprint,
            "sha256:017d6f12f0363f71385cece5bd72f9f8ddfc9adfec8787026411fe9933d1115a",
        )
        self.assertEqual(
            CHILD_DEVICE_PROBE_PROFILE.contract_fingerprint,
            "sha256:c2706d08f75ecef2a7d37265df6eddb03f95bc97d87a08d8632f861202594f46",
        )
        # The owner-signed registry pins the October probe profile; read only.
        registry = json.loads(
            (ROOT / "upstream-trust/ha-core-release-registry.json").read_text()
        )
        entries = registry["envelopes"][-1]["entries"]
        october_entry = next(e for e in entries if e["version"] == "2026.10.0")
        self.assertEqual(
            october_entry["probe_profile_id"],
            CHILD_DEVICE_NAME_PARTS_PROBE_PROFILE.profile_id,
        )
        self.assertEqual(
            october_entry["probe_profile_sha256"],
            CHILD_DEVICE_NAME_PARTS_PROBE_PROFILE.contract_fingerprint,
        )
        # HAMCP-182 sequence 12 selects the profile on exactly 2026.10.0 and
        # 2026.10.1, with the compiled contract; no other entry in any signed
        # envelope has ever carried it.
        (profile,) = CORE_ENTITY_METADATA_PROFILES
        self.assertEqual(registry["envelopes"][-1]["sequence"], 12)
        self.assertEqual(
            sorted(e["version"] for e in entries), ["2026.10.0", "2026.10.1"]
        )
        for entry in entries:
            with self.subTest(version=entry["version"]):
                (reference,) = [
                    item
                    for item in entry["capabilities"]
                    if item["capability_id"] == ENTITY_METADATA
                ]
                self.assertEqual(
                    reference,
                    {key: profile.to_mapping()[key] for key in reference},
                )
        carriers = {
            (envelope["sequence"], entry["version"])
            for envelope in registry["envelopes"]
            for entry in envelope["entries"]
            if ENTITY_METADATA
            in {item["capability_id"] for item in entry["capabilities"]}
        }
        self.assertEqual(
            carriers,
            {
                (10, "2026.10.1"),
                (11, "2026.10.0"),
                (11, "2026.10.1"),
                (12, "2026.10.0"),
                (12, "2026.10.1"),
            },
        )

    def test_static_and_f3_routes_require_the_dormant_profile(self):
        self.assertEqual(
            static_tool_requirements("create_entity_metadata_plan", {}),
            (PLANNING, ENTITY_METADATA),
        )
        self.assertEqual(
            f3_requirements(Prepared(F3_IDENTITY)), (ENTITY_METADATA, VERIFICATION)
        )
        self.assertNotIn(
            "core.governed_configuration_operation",
            f3_requirements(Prepared(F3_IDENTITY)),
        )
        unsupported = ("core.f3_mutation_verification", "core.unsupported_f3_operation")
        for prepared in (
            # The design name before V5 and the 069A-2 identity are not routes.
            Prepared("update_entity_registry_metadata"),
            Prepared("create_label_registry_entry"),
            Prepared(F3_IDENTITY, operation="create_entity_registry_metadata_configuration"),
        ):
            with self.subTest(identity=prepared.capability_identity, op=prepared.operation):
                self.assertEqual(f3_requirements(prepared), unsupported)
        conflicting = Prepared(F3_IDENTITY)
        conflicting.capability_id = "update_automation_configuration"
        self.assertEqual(f3_requirements(conflicting), unsupported)


class UnadmittedCompiledCoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime, _source = await core9.Core20269RuntimeTests()._runtime(
            _snapshot("2026.8.1", evidence=_evidence())
        )

    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    async def test_planner_and_apply_identity_fail_closed_without_transport(self):
        self.assertIsNotNone(self.runtime.acquire((PLANNING,)))
        self.assertIsNone(
            self.runtime.acquire(static_tool_requirements("create_entity_metadata_plan", {}))
        )
        self.assertIsNone(self.runtime.acquire(
            static_tool_requirements("create_entity_metadata_plan", ARGUMENTS), target=TARGET
        ))
        self.assertIsNone(
            self.runtime.acquire_f3(Prepared(F3_IDENTITY, target=SimpleNamespace(**TARGET)))
        )
        withheld = {
            item["capability_id"]
            for item in self.runtime.health_snapshot()["authority_profiles"]
            if not item["disposition"].startswith("admitted_")
        }
        self.assertIn(ENTITY_METADATA, withheld)

        gateway, app = gateway_for(self.runtime)
        refused = await call(gateway, "create_entity_metadata_plan", ARGUMENTS)
        self.assertIn(b"CoreCapabilityUnavailable", refused)
        # The MCP application owns every Home Assistant interaction for the
        # tool, so refusal before it proves zero transport.
        self.assertEqual(app.calls, [])
        # An admitted planning tool on the same runtime still reaches the app.
        admitted = await call(gateway, "create_change_plan", {})
        self.assertNotIn(b"CoreCapabilityUnavailable", admitted)
        self.assertEqual(len(app.calls), 1)
        self.assertEqual(self.runtime.health_snapshot()["fallback_count"], 0)


class SignedOctoberAdmissionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = october.OctoberSignedSelectionTests.asyncSetUp
    publish = october.OctoberSignedSelectionTests.publish

    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    async def test_owner_signed_reference_admits_the_exact_profile(self):
        health = await self.publish()
        self.assertEqual(health["compatible_count"], 22)
        requirements = static_tool_requirements("create_entity_metadata_plan", ARGUMENTS)
        # The mutation-capable profile never issues a target-less lease.
        self.assertIsNone(self.core.acquire(requirements))
        authority = self.core.acquire(requirements, target=TARGET)
        self.assertIsNotNone(authority)
        commits = self.core.consume(authority)
        self.assertIsNotNone(commits)
        self.assertTrue(self.core.revalidate(authority, commits))
        self.core.finish(commits)
        # The planner binds the same target identity as the F3 apply identity.
        apply_target = SimpleNamespace(**TARGET)
        self.assertEqual(
            self.core.target_fingerprint(apply_target), self.core.target_fingerprint(TARGET)
        )
        self.assertIsNone(self.core.acquire_f3(Prepared(F3_IDENTITY)))
        self.assertIsNotNone(self.core.acquire_f3(Prepared(F3_IDENTITY, target=apply_target)))

        gateway, app = gateway_for(self.core)
        admitted = await call(gateway, "create_entity_metadata_plan", ARGUMENTS)
        self.assertNotIn(b"CoreCapabilityUnavailable", admitted)
        self.assertEqual(len(app.calls), 1)
        # Without an exact entity target the gateway refuses before the app.
        for arguments in ({}, {"entity_id": ""}, {"entity_id": ["light.porch"]}):
            with self.subTest(arguments=arguments):
                refused = await call(gateway, "create_entity_metadata_plan", arguments)
                self.assertIn(b"InvalidStaticToolTarget", refused)
        self.assertEqual(len(app.calls), 1)
        self.assertEqual(self.core.health_snapshot()["fallback_count"], 0)

    async def test_invalid_entity_id_is_refused_before_authority_and_app(self):
        """R1: with the profile admitted, no malformed target obtains a lease."""

        await self.publish()
        for label, value, _accepted in INVALID_ENTITY_ID_CASES:
            with self.subTest(case=label):
                recording = RecordingCore(self.core)
                gateway, app = gateway_for(recording)
                body = await call(
                    gateway, "create_entity_metadata_plan", entity_arguments(value)
                )
                self.assertIn(b"InvalidStaticToolTarget", body)
                self.assertIn(STATIC_TARGET_INVALID_ENTITY_ID.encode(), body)
                self.assertIn(b"INVALID_REQUEST", body.upper())
                self.assertNotIn(b"CoreCapabilityUnavailable", body)
                # Zero Core acquisitions and commits, and zero application
                # calls: the application owns every provider interaction.
                self.assertEqual(recording.acquired, [])
                self.assertEqual(recording.consumed, 0)
                self.assertEqual(app.calls, [])
        # Valid control on the same admitted runtime: one targeted lease and
        # one application call.
        recording = RecordingCore(self.core)
        gateway, app = gateway_for(recording)
        body = await call(gateway, "create_entity_metadata_plan", ARGUMENTS)
        self.assertNotIn(b"InvalidStaticToolTarget", body)
        self.assertNotIn(b"CoreCapabilityUnavailable", body)
        self.assertEqual(
            recording.acquired,
            [((static_tool_requirements("create_entity_metadata_plan", ARGUMENTS),),
              {"target": TARGET})],
        )
        self.assertEqual(len(app.calls), 1)
        self.assertEqual(self.core.health_snapshot()["fallback_count"], 0)

    async def test_entity_id_at_the_length_boundary(self):
        await self.publish()
        for entity_id, admitted in ((AT_LIMIT, True), (OVER_LIMIT, False)):
            with self.subTest(length=len(entity_id)):
                gateway, app = gateway_for(self.core)
                body = await call(
                    gateway, "create_entity_metadata_plan", entity_arguments(entity_id)
                )
                self.assertEqual(b"InvalidStaticToolTarget" in body, not admitted)
                self.assertNotIn(b"CoreCapabilityUnavailable", body)
                self.assertEqual(len(app.calls), 1 if admitted else 0)

    async def test_signed_entry_without_the_profile_keeps_it_withheld(self):
        entry = dict(self.entry)
        entry["capabilities"] = [
            item for item in self.entry["capabilities"]
            if item["capability_id"] != ENTITY_METADATA
        ]
        health = await self.publish(entry)
        self.assertEqual(health["compatible_count"], 21)
        self.assertIsNotNone(self.core.acquire((PLANNING,)))
        self.assertIsNone(
            self.core.acquire(static_tool_requirements("create_entity_metadata_plan", {}))
        )
        self.assertIsNone(self.core.acquire(
            static_tool_requirements("create_entity_metadata_plan", ARGUMENTS), target=TARGET
        ))
        self.assertIsNone(
            self.core.acquire_f3(Prepared(F3_IDENTITY, target=SimpleNamespace(**TARGET)))
        )
        gateway, app = gateway_for(self.core)
        refused = await call(gateway, "create_entity_metadata_plan", ARGUMENTS)
        self.assertIn(b"CoreCapabilityUnavailable", refused)
        self.assertEqual(app.calls, [])
        self.assertEqual(health["fallback_count"], 0)


class ProviderHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def provider(self, **kwargs) -> dict:
        service = ChangeGovernanceService(
            ChangePlanRepository(Path(self.temp.name)), object(), **kwargs
        )
        summary = service.health_summary()
        return summary["operational_administration"]["entity_metadata_provider"]

    def test_configured_and_unconfigured_provider_health(self):
        common = {
            "provider": "direct_home_assistant_entity_registry",
            "provider_contract": "compiled-core-entity-registry-metadata-operation-v1",
            "provider_contract_status": "code_owned_exact",
            "core_capability": ENTITY_METADATA,
            "core_admission": "owner_signed_reference_required",
            "label_creation": False,
            "fallback": "none",
            "fallback_count": 0,
            "fallback_policy": "none",
        }
        self.assertEqual(
            self.provider(entity_metadata_gateway=object()),
            {**common, "configured": True, "operational_status": "configured_unprobed"},
        )
        self.assertEqual(
            self.provider(),
            {**common, "configured": False, "operational_status": "unavailable"},
        )


class StaticToolCompletenessTests(unittest.TestCase):
    """F6: an unmapped static tool would skip Core acquisition at the gateway."""

    def test_every_static_tool_is_mapped_or_reviewed_empty(self):
        self.assertTrue(set(STATIC_TOOL_CORE_REQUIREMENTS) <= set(ENGINEERING_STATIC_TOOL_NAMES))
        empty = {
            name
            for name in ENGINEERING_STATIC_TOOL_NAMES
            if static_tool_requirements(name, {}) == ()
        }
        self.assertEqual(empty, REVIEWED_EMPTY_STATIC_ROUTES)
        for conditional in ("server_info", "get_server_health"):
            with self.subTest(tool=conditional):
                self.assertNotEqual(static_tool_requirements(conditional, {}), ())
                self.assertEqual(
                    static_tool_requirements(conditional, {"check_ha": False}), ()
                )
        self.assertNotIn("create_entity_metadata_plan", REVIEWED_EMPTY_STATIC_ROUTES)
        self.assertIn("create_entity_metadata_plan", ENGINEERING_STATIC_TOOL_NAMES)

    def test_mutation_capable_static_routes_bind_an_exact_target(self):
        mutation = {
            profile.capability_id
            for profile in CORE_RUNTIME_CAPABILITY_PROFILES
            if profile.capability_class.mutation_capable
        }
        targeted = {
            name
            for name, requirements in STATIC_TOOL_CORE_REQUIREMENTS.items()
            if mutation.intersection(requirements)
        }
        # Without a target such a route could never acquire a lease.
        self.assertEqual(
            targeted,
            {
                "create_entity_metadata_plan",
                # HAMCP-069B and HAMCP-165 bind their own exact targets
                # (their tests cover them).
                "create_entity_availability_exposure_plan",
                "create_label_plan",
            },
        )
        self.assertEqual(static_tool_target("create_entity_metadata_plan", ARGUMENTS), TARGET)
        for arguments in (None, [], {}, {"entity_id": ""}, {"entity_id": 3}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(StaticToolTargetRefused) as raised:
                    static_tool_target("create_entity_metadata_plan", arguments)
                self.assertEqual(raised.exception.reason_code, STATIC_TARGET_INVALID_ENTITY_ID)
                self.assertEqual(raised.exception.field, "entity_id")
        # Every other tool keeps the base behaviour: no target, never a
        # refusal, whatever entity_id it is given.
        shapes = [None, {}] + [entity_arguments(value) for _l, value, _a in ENTITY_ID_CASES]
        for name in ENGINEERING_STATIC_TOOL_NAMES:
            if name not in {
                "create_entity_metadata_plan",
                "create_entity_availability_exposure_plan",
                "create_label_plan",
            }:
                for arguments in shapes:
                    with self.subTest(tool=name, arguments=repr(arguments)[:40]):
                        self.assertIsNone(static_tool_target(name, arguments))


class SharedEntityIdValidatorTests(unittest.TestCase):
    """R1: admission and the planner apply one entity_id rule."""

    def test_admission_and_planner_call_the_same_function(self):
        shared = entity_ids.validate_entity_id
        self.assertIs(routes.validate_entity_id, shared)
        self.assertIs(entity_metadata.validate_entity_id, shared)
        self.assertIs(entity_metadata_planning.validate_entity_id, shared)
        # No module on the entity metadata path keeps its own copy of the
        # rule; the advertised input schema is derived from the same source.
        rule = r"[a-z0-9_]+\.[a-z0-9_]+"
        for relative in (
            "entity_ids.py",
            "governance/entity_metadata.py",
            "governance/entity_metadata_planning.py",
            "governance/service.py",
            "ha_core_readmission/routes.py",
            "routing.py",
            "tools/governance.py",
        ):
            with self.subTest(module=relative):
                self.assertEqual(
                    rule in (ENGINE / relative).read_text(), relative == "entity_ids.py"
                )
        schema = ENTITY_METADATA_INPUT_SCHEMA["properties"]["entity_id"]
        self.assertEqual(schema["pattern"], r"^[a-z0-9_]+\.[a-z0-9_]+$")
        self.assertEqual(schema["pattern"], entity_ids.ENTITY_ID_PATTERN.pattern)
        self.assertEqual(schema["maxLength"], 255)
        self.assertEqual(schema["maxLength"], entity_ids.MAX_ENTITY_ID_LENGTH)

    def test_shared_case_table_gives_the_same_decision_on_both_paths(self):
        for label, value, accepted in ENTITY_ID_CASES:
            with self.subTest(case=label):
                arguments = entity_arguments(value)
                try:
                    target = static_tool_target("create_entity_metadata_plan", arguments)
                except StaticToolTargetRefused:
                    admission = False
                else:
                    admission = True
                    self.assertEqual(
                        target,
                        {"target_type": "entity_registry_metadata", "target_id": value},
                    )
                try:
                    request = validate_request(arguments)
                except EntityMetadataRefusal as refusal:
                    planner = False
                    self.assertEqual(refusal.reason_code, "entity_metadata_invalid_value")
                    self.assertEqual(refusal.details, {"field": "entity_id"})
                else:
                    planner = True
                    self.assertEqual(request.entity_id, value)
                self.assertEqual(admission, accepted)
                self.assertEqual(planner, accepted)

    def test_length_boundary(self):
        self.assertEqual(len(AT_LIMIT), 255)
        self.assertEqual(len(OVER_LIMIT), 256)
        self.assertEqual(entity_ids.validate_entity_id(AT_LIMIT), AT_LIMIT)
        with self.assertRaises(ValueError):
            entity_ids.validate_entity_id(OVER_LIMIT)


class OtherToolGatewayCallShapeTests(unittest.IsolatedAsyncioTestCase):
    """R1 leaves every other tool's Core acquisition call shape unchanged."""

    async def test_other_tools_acquire_without_a_target(self):
        class RefusingCore:
            def __init__(self):
                self.acquired = []

            def acquire(self, *args, **kwargs):
                self.acquired.append((args, kwargs))
                return None

        reached = 0
        for name in sorted(ENGINEERING_STATIC_TOOL_NAMES):
            if name in {
                "create_entity_metadata_plan",
                "create_entity_availability_exposure_plan",
                "create_label_plan",
            }:
                continue
            for arguments in ({}, {"entity_id": "LIGHT.PORCH"}, {"entity_id": "light.porch"}):
                requirements = static_tool_requirements(name, arguments)
                if not requirements:
                    continue
                with self.subTest(tool=name, arguments=arguments):
                    core = RefusingCore()
                    gateway, app = gateway_for(core)
                    body = await call(gateway, name, arguments)
                    self.assertNotIn(b"InvalidStaticToolTarget", body)
                    self.assertEqual(app.calls, [])
                    if core.acquired:
                        reached += 1
                        self.assertEqual(core.acquired, [((requirements,), {})])
        # Tools refused by their own earlier argument validation never reach
        # acquisition at base either; the rest must all have been driven.
        self.assertGreater(reached, 3 * 20)


class UnknownCoreRefusalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    async def test_unknown_cores_refuse_valid_and_invalid_targets_before_app(self):
        for version in ("2026.11.0", "2099.1.0"):
            runtime, _source = await core9.Core20269RuntimeTests()._runtime(
                _snapshot(version, evidence=_evidence())
            )
            for label, value, accepted in (
                ("valid", "light.porch", True),
                *INVALID_ENTITY_ID_CASES,
            ):
                with self.subTest(version=version, case=label):
                    gateway, app = gateway_for(runtime)
                    body = await call(
                        gateway, "create_entity_metadata_plan", entity_arguments(value)
                    )
                    self.assertIn(
                        b"CoreCapabilityUnavailable" if accepted else b"InvalidStaticToolTarget",
                        body,
                    )
                    self.assertEqual(app.calls, [])
            self.assertIsNone(runtime.acquire_f3(
                Prepared(F3_IDENTITY, target=SimpleNamespace(**TARGET))
            ))


class _RecordingF3Core:
    def __init__(self, admit: bool):
        self.admit = admit
        self.seen: list[tuple] = []

    async def reconcile_once(self, reason):
        return None

    def acquire_f3(self, prepared, plan_created_at=None):
        self.seen.append(
            (
                f3_requirements(prepared),
                CoreRuntime.target_fingerprint(prepared.target),
                prepared.target.target_type,
                prepared.target.target_id,
            )
        )
        return object() if self.admit else None

    def consume(self, authority):
        return ("commit",)

    def revalidate(self, authority, commits):
        return True

    def release(self, authority):
        return True

    def finish(self, commits):
        return True


class RealPreparedTargetBindingTests(EntityMetadataServiceTestCase):
    """A real prepared F3 operation binds the planner's lease target."""

    admit = True

    async def restart_runtime(self):
        self.core = _RecordingF3Core(self.admit)
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

    async def test_apply_target_matches_planner_lease(self):
        self.add_entity("light.porch", name="Old porch")
        created = await self.create(entity_id="light.porch", name="Porch")
        await self.approve(created)
        result = await self.apply(created)
        planner = CoreRuntime.target_fingerprint(
            static_tool_target("create_entity_metadata_plan", {"entity_id": "light.porch"})
        )
        if self.admit:
            self.assertEqual(result["status"], "applied", result)
            self.assertEqual(
                self.ws.updates(),
                [{"type": "config/entity_registry/update", "entity_id": "light.porch",
                  "name": "Porch"}],
            )
        else:
            self.assertEqual(self.ws.updates(), [])
        self.assertTrue(self.core.seen)
        for requirements, fingerprint, target_type, target_id in self.core.seen:
            self.assertEqual(fingerprint, planner)
            self.assertEqual((target_type, target_id), ("entity_registry_metadata", "light.porch"))
            self.assertEqual(requirements, (ENTITY_METADATA, VERIFICATION))


class RefusedPreparedTargetBindingTests(RealPreparedTargetBindingTests):
    admit = False


class UpstreamEntityWriteTests(EntityMetadataServiceTestCase):
    """Upstream ``ha_set_entity`` is never a route for entity metadata."""

    def test_upstream_entity_write_stays_prohibited(self):
        self.assertIn("ha_set_entity", PROHIBITED_UPSTREAM_TOOLS)
        self.assertNotIn("ha_set_entity", ALLOWED_UPSTREAM_TOOLS)
        self.assertNotIn("ha_set_entity", ENGINEERING_STATIC_TOOL_NAMES)
        for path in sorted(ENGINE.glob("upstream_tool_policy_*.json")):
            policy = json.loads(path.read_text())
            for tool in policy["tools"]:
                if "ha_set_entity" in (tool.get("exposed_name"), tool.get("upstream_name")):
                    with self.subTest(policy=path.name):
                        self.assertNotEqual(tool["classification"], "automatic_read")
        self.assertIs(
            TOOL_CAPABILITY_POLICY["create_entity_metadata_plan"],
            ProviderCapability.OPERATIONAL_ADMINISTRATION,
        )
        # No module on the entity metadata path names the upstream tool.
        for relative in (
            "governance/entity_metadata_planning.py",
            "governance/service.py",
            "governance/policy.py",
            "governance/resources.py",
            "f3_configuration/adapter.py",
            "f3_configuration/gateway.py",
            "f3_runtime/runtime.py",
            "tools/governance.py",
        ):
            with self.subTest(module=relative):
                self.assertNotIn("ha_set_entity", (ENGINE / relative).read_text())

    async def test_applied_update_uses_only_the_native_registry_command(self):
        self.add_entity("light.porch", name="Old porch")
        created = await self.create(entity_id="light.porch", name="New porch")
        await self.approve(created)
        result = await self.apply(created)
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(
            self.ws.updates(),
            [{"type": "config/entity_registry/update", "entity_id": "light.porch", "name": "New porch"}],
        )
        command_types = {command["type"] for command in self.ws.commands}
        self.assertTrue(all(kind.startswith("config/") for kind in command_types), command_types)
        self.assertFalse(any("ha_set_entity" in json.dumps(c) for c in self.ws.commands))
        self.assertFalse(any("ha_set_entity" in json.dumps(r) for r in self.rest.requests))


if __name__ == "__main__":
    unittest.main()
