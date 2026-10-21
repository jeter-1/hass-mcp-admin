"""HAMCP-164 Core gateway fails closed for tools with no Core requirement mapping.

Every static tool is mapped, explicitly Core-independent, or refused.  A tool
name that is neither mapped, allowlisted, nor a currently published delegated
read is refused before Core acquisition and before the MCP application, so no
provider, upstream, or Home Assistant call can occur.  The allowlist is a
closed exact-name set.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.capabilities import (  # noqa: E402
    replace_dynamic_upstream_capabilities,
)
from ha_mcp_engineering.ha_core_readmission import routes  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_RUNTIME_CAPABILITY_PROFILES,
)
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    CONDITIONAL_CORE_STATIC_TOOLS,
    CORE_INDEPENDENT_DOWNSTREAM_AUTHORITY,
    CORE_INDEPENDENT_LOCAL_STATE,
    CORE_INDEPENDENT_MOUNTED_SOURCE,
    CORE_INDEPENDENT_STATIC_TOOLS,
    CORE_INDEPENDENT_SUPERVISOR_ONLY,
    CORE_REQUIREMENT_UNMAPPED,
    CORE_ROUTE_INDEPENDENT,
    CORE_ROUTE_MAPPED,
    CORE_ROUTE_UNMAPPED,
    DELEGATED_CORE_REQUIREMENTS,
    STATIC_TOOL_CORE_REQUIREMENTS,
    f3_requirements,
    static_tool_core_route,
    static_tool_requirements,
)
from ha_mcp_engineering.providers.routing import (  # noqa: E402
    PREVALIDATION_ENFORCEMENT_TOOLS,
)
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.tools.registry import (  # noqa: E402
    ENGINEERING_STATIC_TOOL_NAMES,
)

import tests.test_ha_core_2026_9_integration as core9  # noqa: E402
from tests.test_ha_core_capability_auto_readmission import (  # noqa: E402
    _evidence,
    _snapshot,
)
from tests.test_readonly_upstream_gateway import settings  # noqa: E402


MAPPED = "mapped"
CONDITIONAL = "conditional"
REFUSED = "prevalidation_refused"
LOCAL = CORE_INDEPENDENT_LOCAL_STATE
SUPERVISOR = CORE_INDEPENDENT_SUPERVISOR_ONLY
MOUNTED = CORE_INDEPENDENT_MOUNTED_SOURCE
DOWNSTREAM = CORE_INDEPENDENT_DOWNSTREAM_AUTHORITY

# The reviewed HAMCP-164 inventory of all 63 static tools.  A new static tool
# fails this test until it is reviewed and added as mapped or allowlisted.
STATIC_CORE_TABLE = {
    "apply_change_plan": DOWNSTREAM,
    "approve_change_plan": LOCAL,
    "automation_reliability_analysis": MAPPED,
    "call_service": REFUSED,
    "cancel_execution_task": LOCAL,
    "capture_automation_baseline": MAPPED,
    "change_impact_analysis": MAPPED,
    "check_config": MAPPED,
    "configuration_integrity_analysis": MAPPED,
    "control_fan": DOWNSTREAM,
    "control_power": DOWNSTREAM,
    "create_addon_restart_plan": MAPPED,
    "create_backup_plan": MAPPED,
    "create_change_plan": MAPPED,
    "create_configuration_plan": MAPPED,
    "create_dashboard_update_plan": MAPPED,
    "create_entity_metadata_plan": MAPPED,
    "create_helper_state_plan": MAPPED,
    "create_home_assistant_restart_plan": MAPPED,
    "create_reload_plan": MAPPED,
    "dashboard_integrity_analysis": MAPPED,
    "delete_automation": REFUSED,
    "entity_dependency_analysis": MAPPED,
    "get_audit_log": LOCAL,
    "get_automation_config": MAPPED,
    "get_automation_trace": MAPPED,
    "get_backup_inventory": MAPPED,
    "get_recorder_settings": MAPPED,
    "get_unregistered_entities": MAPPED,
    "get_configuration_definitions": MAPPED,
    "get_blueprint": MOUNTED,
    "get_change_plan": LOCAL,
    "get_core_log_history": SUPERVISOR,
    "get_dashboard_config": MAPPED,
    "get_entity": MAPPED,
    "get_error_log": MAPPED,
    "get_execution_task": LOCAL,
    "get_history": MAPPED,
    "get_integration_inspection": MAPPED,
    "get_logbook": MAPPED,
    "get_server_health": CONDITIONAL,
    "handoff_generation": MAPPED,
    "incident_correlation": MAPPED,
    "list_areas": MAPPED,
    "list_automation_traces": MAPPED,
    "list_automations": MAPPED,
    "list_blueprints": MAPPED,
    "list_capabilities": LOCAL,
    "list_change_plans": LOCAL,
    "list_dashboards": MAPPED,
    "list_devices": MAPPED,
    "list_entity_registry": MAPPED,
    "list_execution_tasks": LOCAL,
    "list_services": MAPPED,
    "reload_domain": REFUSED,
    "render_template": MAPPED,
    "reverify_configuration_task": DOWNSTREAM,
    "rollback_change": DOWNSTREAM,
    "run_held_read_canary": DOWNSTREAM,
    "search_entities": MAPPED,
    "search_services": MAPPED,
    "server_info": CONDITIONAL,
    "upsert_automation": REFUSED,
}

ALLOWLISTED = frozenset(
    name for name, value in STATIC_CORE_TABLE.items()
    if value not in {MAPPED, CONDITIONAL, REFUSED}
)

# Prefix extensions, case changes, separators, and pattern syntax around
# allowlisted names.  Each must be refused exactly like any unmapped name.
LOOKALIKE_NAMES = tuple(
    sorted(
        {
            *(name + "_extra" for name in ALLOWLISTED),
            *(name[:-1] for name in ALLOWLISTED),
            *(name.upper() for name in ALLOWLISTED),
            *(" " + name for name in ALLOWLISTED),
            *(name + "\n" for name in ALLOWLISTED),
            "get_*",
            "get_core_log_history*",
            "*",
            "list_?hange_plans",
            "[g]et_audit_log",
            "",
        }
    )
)
SYNTHETIC_UNMAPPED = "synthetic_unmapped_engineering_tool"
OPERATION_ID = "1790000000-" + "a" * 32
# Minimal valid arguments where the gateway validates a typed request before
# the Core step; every other allowlisted tool is called with no arguments.
ALLOWLIST_ARGUMENTS = {
    "control_fan": {
        "entity_id": "fan.synthetic", "action": "turn_on", "operation_id": OPERATION_ID,
    },
    "control_power": {
        "entity_id": "light.synthetic", "action": "turn_on", "operation_id": OPERATION_ID,
    },
}


class RecordingCore:
    """A Core runtime that refuses every lease and records each request."""

    def __init__(self):
        self.acquired: list[tuple[tuple, dict]] = []

    def acquire(self, *args, **kwargs):
        self.acquired.append((args, kwargs))
        return None


def gateway_for(runtime, audit: AuditLogger | None = None):
    app = core9._RecordingMcpApp()
    configured = settings()
    gateway = AuthenticatedMcpGateway(
        app,
        configured,
        audit or AuditLogger("unused", configured.access_secret, enabled=False),
        core_runtime=runtime,
    )
    return gateway, app


async def call(gateway, tool_name, arguments: dict | None = None) -> bytes:
    return await core9.Core20269StaticRouteTests._call(gateway, tool_name, arguments)


def tool_result(body: bytes) -> dict:
    text = body.decode("utf-8")
    if text.startswith("event:"):
        text = next(
            line[len("data:"):].strip()
            for line in text.splitlines()
            if line.startswith("data:")
        )
    return json.loads(text)["result"]


def failure_payload(body: bytes) -> dict:
    result = tool_result(body)
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    return json.loads(result["content"][0]["text"])


class StaticCoreTableTests(unittest.TestCase):
    """The complete reviewed inventory is pinned."""

    def test_every_static_tool_is_classified_exactly(self):
        self.assertEqual(len(ENGINEERING_STATIC_TOOL_NAMES), 63)
        self.assertEqual(set(STATIC_CORE_TABLE), set(ENGINEERING_STATIC_TOOL_NAMES))
        for name, expected in sorted(STATIC_CORE_TABLE.items()):
            route = static_tool_core_route(name, {})
            with self.subTest(tool=name):
                if expected == MAPPED:
                    self.assertEqual(route.classification, CORE_ROUTE_MAPPED)
                    self.assertTrue(route.requirements)
                    self.assertEqual(route.requirements, static_tool_requirements(name, {}))
                elif expected == CONDITIONAL:
                    self.assertEqual(route.classification, CORE_ROUTE_MAPPED)
                    self.assertTrue(route.requirements)
                    local = static_tool_core_route(name, {"check_ha": False})
                    self.assertEqual(local.classification, CORE_ROUTE_INDEPENDENT)
                    self.assertEqual(local.requirements, ())
                elif expected == REFUSED:
                    self.assertEqual(route.classification, CORE_ROUTE_UNMAPPED)
                    self.assertIn(name, PREVALIDATION_ENFORCEMENT_TOOLS)
                else:
                    self.assertEqual(route.classification, CORE_ROUTE_INDEPENDENT)
                    self.assertEqual(route.requirements, ())
                    self.assertEqual(CORE_INDEPENDENT_STATIC_TOOLS[name].basis, expected)

    def test_allowlist_is_the_reviewed_closed_set(self):
        self.assertEqual(set(CORE_INDEPENDENT_STATIC_TOOLS), ALLOWLISTED)
        self.assertEqual(len(ALLOWLISTED), 16)
        self.assertIsInstance(CORE_INDEPENDENT_STATIC_TOOLS, MappingProxyType)
        with self.assertRaises(TypeError):
            CORE_INDEPENDENT_STATIC_TOOLS["synthetic"] = CORE_INDEPENDENT_STATIC_TOOLS[
                "get_core_log_history"
            ]
        for name, independence in CORE_INDEPENDENT_STATIC_TOOLS.items():
            with self.subTest(tool=name):
                self.assertIs(type(name), str)
                self.assertFalse(set(name) & set("*?[]"))
                self.assertTrue(independence.justification.strip())
        self.assertEqual(
            set(STATIC_CORE_TABLE[name] for name in ALLOWLISTED),
            set(routes.CORE_INDEPENDENCE_BASES),
        )

    def test_requirement_map_holds_only_non_empty_mappings(self):
        self.assertTrue(set(STATIC_TOOL_CORE_REQUIREMENTS) <= set(ENGINEERING_STATIC_TOOL_NAMES))
        for name, values in STATIC_TOOL_CORE_REQUIREMENTS.items():
            with self.subTest(tool=name):
                self.assertTrue(values)
        self.assertFalse(set(STATIC_TOOL_CORE_REQUIREMENTS) & ALLOWLISTED)
        self.assertFalse(CONDITIONAL_CORE_STATIC_TOOLS & ALLOWLISTED)
        self.assertEqual(
            CONDITIONAL_CORE_STATIC_TOOLS,
            {name for name, value in STATIC_CORE_TABLE.items() if value == CONDITIONAL},
        )

    def test_lookalike_and_pattern_names_are_unmapped(self):
        for name in LOOKALIKE_NAMES:
            with self.subTest(tool=name):
                self.assertEqual(
                    static_tool_core_route(name, {}).classification, CORE_ROUTE_UNMAPPED
                )

    def test_import_validation_rejects_an_open_or_ambiguous_table(self):
        original_map = routes.STATIC_TOOL_CORE_REQUIREMENTS
        original_allowlist = routes.CORE_INDEPENDENT_STATIC_TOOLS
        independence = original_allowlist["get_core_log_history"]
        cases = {
            "empty_mapping": (
                {**original_map, "get_core_log_history": ()}, original_allowlist
            ),
            "mapped_and_allowlisted": (
                {**original_map, "get_blueprint": ("core.basic_rest_read",)},
                original_allowlist,
            ),
            "wildcard_entry": (
                original_map,
                MappingProxyType({**original_allowlist, "get_*": independence}),
            ),
            "unknown_basis": (
                original_map,
                MappingProxyType({
                    **original_allowlist,
                    "synthetic": routes.CoreIndependence("anything", "x"),
                }),
            ),
            "missing_justification": (
                original_map,
                MappingProxyType({
                    **original_allowlist,
                    "synthetic": routes.CoreIndependence(CORE_INDEPENDENT_LOCAL_STATE, ""),
                }),
            ),
        }
        try:
            for label, (mapping, allowlist) in cases.items():
                with self.subTest(case=label):
                    routes.STATIC_TOOL_CORE_REQUIREMENTS = mapping
                    routes.CORE_INDEPENDENT_STATIC_TOOLS = allowlist
                    with self.assertRaises(RuntimeError):
                        routes._validate_static_core_routes()
        finally:
            routes.STATIC_TOOL_CORE_REQUIREMENTS = original_map
            routes.CORE_INDEPENDENT_STATIC_TOOLS = original_allowlist
        routes._validate_static_core_routes()

    def test_every_delegated_route_maps_a_non_empty_requirement_set(self):
        # HAMCP-162 maps ha_config_get_yaml (30 -> 31).
        self.assertEqual(len(DELEGATED_CORE_REQUIREMENTS), 31)
        self.assertFalse(set(DELEGATED_CORE_REQUIREMENTS) & set(ENGINEERING_STATIC_TOOL_NAMES))
        for name, values in DELEGATED_CORE_REQUIREMENTS.items():
            with self.subTest(tool=name):
                self.assertTrue(values)


class GatewayFailClosedTests(unittest.IsolatedAsyncioTestCase):
    """F6 at runtime: an unmapped tool is refused before Core and the app."""

    async def asyncTearDown(self):
        replace_dynamic_upstream_capabilities((), {})

    def assertUnmappedRefusal(self, body: bytes, app, core) -> dict:
        # An unmapped name is not a callable tool: like the MCP SDK's unknown
        # tool result, the refusal sets isError=true (the exact-image read
        # gateway acceptance requires it for ha_call_service).
        self.assertIs(tool_result(body)["isError"], True)
        payload = failure_payload(body)
        self.assertEqual(payload["error"], "CoreRequirementUnmapped")
        self.assertEqual(payload["error_code"], "unsupported_operation")
        self.assertFalse(payload["retryable"])
        details = payload["details"]
        self.assertEqual(details["reason"], CORE_REQUIREMENT_UNMAPPED)
        self.assertEqual(details["reason_code"], CORE_REQUIREMENT_UNMAPPED)
        self.assertFalse(details["core_authority_acquired"])
        self.assertFalse(details["provider_dispatch_occurred"])
        self.assertFalse(details["upstream_attempted"])
        self.assertEqual(details["home_assistant_request_count"], 0)
        self.assertEqual(details["upstream_request_count"], 0)
        # The MCP application owns every provider, upstream, and Home
        # Assistant interaction, so no call to it proves zero transport.
        self.assertEqual(app.calls, [])
        if core is not None:
            self.assertEqual(core.acquired, [])
        return payload

    async def test_synthetic_unmapped_tool_is_refused_with_zero_provider_calls(self):
        core = RecordingCore()
        gateway, app = gateway_for(core)
        body = await call(gateway, SYNTHETIC_UNMAPPED, {"entity_id": "light.porch"})
        self.assertUnmappedRefusal(body, app, core)

    async def test_gateway_without_core_runtime_enforces_no_core_gate(self):
        # Only test and embedding compositions omit the runtime; production
        # wiring is pinned by ProductionCompositionTests.
        gateway, app = gateway_for(None)
        body = await call(gateway, SYNTHETIC_UNMAPPED, {})
        self.assertNotIn(b"CoreRequirementUnmapped", body)
        self.assertEqual(len(app.calls), 1)

    async def test_lookalike_names_are_refused_not_allowlisted(self):
        for name in LOOKALIKE_NAMES:
            with self.subTest(tool=name):
                core = RecordingCore()
                gateway, app = gateway_for(core)
                self.assertUnmappedRefusal(await call(gateway, name), app, core)

    async def test_unpublished_delegated_names_are_refused(self):
        # A delegated name reaches Core only through a currently published
        # read-gateway route; a withheld or unknown one is unmapped here.
        for name in ("ha_get_state", "ha_mcp__ha_get_state", "ha_call_service"):
            with self.subTest(tool=name):
                core = RecordingCore()
                gateway, app = gateway_for(core)
                self.assertUnmappedRefusal(await call(gateway, name), app, core)

    async def test_refusal_is_audited_without_argument_values(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = settings()
            path = Path(directory) / "audit.jsonl"
            audit = AuditLogger(str(path), configured.access_secret)
            core = RecordingCore()
            gateway, app = gateway_for(core, audit)
            body = await call(
                gateway, SYNTHETIC_UNMAPPED, {"payload": "synthetic-sensitive-value"}
            )
            self.assertUnmappedRefusal(body, app, core)
            records = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        tool_records = [item for item in records if item.get("tool_name") == SYNTHETIC_UNMAPPED]
        self.assertEqual(len(tool_records), 1)
        record = tool_records[0]
        self.assertEqual(record["result_status"], "failure")
        self.assertEqual(record["error_code"], "unsupported_operation")
        self.assertEqual(
            record["parameters"],
            {
                "core_route": "unmapped",
                "reason_code": CORE_REQUIREMENT_UNMAPPED,
                "argument_fields": ["payload"],
                "provider_dispatch_occurred": False,
                "upstream_attempted": False,
            },
        )
        self.assertEqual(record["resource_ids"], {})
        self.assertEqual(record["ha_endpoint_categories"], [])
        self.assertNotIn("synthetic-sensitive-value", json.dumps(records))

    async def test_allowlisted_tools_bypass_core_when_authority_is_unavailable(self):
        for name in sorted(ALLOWLISTED):
            with self.subTest(tool=name):
                core = RecordingCore()
                gateway, app = gateway_for(core)
                body = await call(gateway, name, ALLOWLIST_ARGUMENTS.get(name, {}))
                self.assertNotIn(b"CoreRequirementUnmapped", body)
                self.assertNotIn(b"CoreCapabilityUnavailable", body)
                self.assertEqual(core.acquired, [])
                self.assertEqual(len(app.calls), 1)
        for name in sorted(CONDITIONAL_CORE_STATIC_TOOLS):
            with self.subTest(tool=name, check_ha=False):
                core = RecordingCore()
                gateway, app = gateway_for(core)
                body = await call(gateway, name, {"check_ha": False})
                self.assertNotIn(b"CoreCapabilityUnavailable", body)
                self.assertEqual(core.acquired, [])
                self.assertEqual(len(app.calls), 1)

    async def test_get_core_log_history_survives_an_unavailable_core_runtime(self):
        runtime, _source = await core9.Core20269RuntimeTests()._runtime(
            _snapshot("2099.1.0", evidence=_evidence())
        )
        # The unknown Core release admits nothing: a mapped read is refused.
        self.assertIsNone(runtime.acquire(static_tool_requirements("get_history", {})))
        gateway, app = gateway_for(runtime)
        refused = await call(gateway, "get_history", {"entity_id": "light.porch"})
        self.assertIn(b"CoreCapabilityUnavailable", refused)
        self.assertEqual(app.calls, [])
        logs = await call(gateway, "get_core_log_history", {})
        self.assertNotIn(b"CoreCapabilityUnavailable", logs)
        self.assertNotIn(b"CoreRequirementUnmapped", logs)
        self.assertEqual(len(app.calls), 1)

    async def test_mapped_tools_still_acquire_their_exact_requirements(self):
        for name, expected in sorted(STATIC_CORE_TABLE.items()):
            if expected not in {MAPPED, CONDITIONAL} or name == "create_entity_metadata_plan":
                continue
            with self.subTest(tool=name):
                core = RecordingCore()
                gateway, app = gateway_for(core)
                body = await call(gateway, name, {})
                self.assertNotIn(b"CoreRequirementUnmapped", body)
                self.assertEqual(app.calls, [])
                if core.acquired:
                    self.assertEqual(
                        core.acquired, [((static_tool_requirements(name, {}),), {})]
                    )
                    self.assertIn(b"CoreCapabilityUnavailable", body)

    async def test_prevalidation_tools_are_refused_before_the_core_gate(self):
        for name in sorted(PREVALIDATION_ENFORCEMENT_TOOLS):
            with self.subTest(tool=name):
                core = RecordingCore()
                gateway, app = gateway_for(core)
                body = await call(gateway, name, {})
                self.assertNotIn(b"CoreRequirementUnmapped", body)
                self.assertEqual(core.acquired, [])
                self.assertEqual(app.calls, [])

    async def test_published_delegated_reads_pass_to_the_read_gateway(self):
        replace_dynamic_upstream_capabilities(
            (
                {
                    "tool": "ha_get_state",
                    "upstream_tool": "ha_get_state",
                    "status": "delegated",
                    "category": "upstream_read_gateway",
                    "risk": "read",
                },
                {
                    "tool": "ha_not_delegated",
                    "upstream_tool": "ha_not_delegated",
                    "status": "withheld",
                    "category": "upstream_read_gateway",
                    "risk": "read",
                },
            ),
            {},
        )
        core = RecordingCore()
        gateway, app = gateway_for(core)
        body = await call(gateway, "ha_get_state", {"entity_id": "light.porch"})
        self.assertNotIn(b"CoreRequirementUnmapped", body)
        # The gateway acquires nothing for a delegated read; the read gateway
        # acquires the exact delegated requirements before its dispatch.
        self.assertEqual(core.acquired, [])
        self.assertEqual(len(app.calls), 1)
        refused_core = RecordingCore()
        gateway, app = gateway_for(refused_core)
        self.assertUnmappedRefusal(
            await call(gateway, "ha_not_delegated"), app, refused_core
        )

    async def test_non_string_tool_names_remain_with_sdk_validation(self):
        # The SDK rejects a non-string name before any tool resolves; the
        # gateway behaviour for that malformed request is unchanged.
        for name in (None, 3):
            with self.subTest(name=repr(name)):
                core = RecordingCore()
                gateway, app = gateway_for(core)
                body = await call(gateway, name)
                self.assertNotIn(b"CoreRequirementUnmapped", body)
                self.assertEqual(core.acquired, [])


class F3UnmappedIdentityTests(unittest.IsolatedAsyncioTestCase):
    """An F3 identity with no mapping cannot acquire a Core route lease."""

    def test_every_reviewed_f3_identity_maps_a_profiled_requirement(self):
        identities = {
            ("update_existing_dashboard", "update_dashboard"): "core.dashboard_configuration_read",
            ("set_exact_input_boolean_state", "set_input_boolean_state"): "core.typed_helper_operation",
            ("update_entity_registry_metadata_configuration",
             "update_entity_registry_metadata_configuration"): "core.entity_registry_metadata_operation",
            **{
                identity: "core.governed_configuration_operation"
                for identity in (
                    ("create_automation_configuration", "create_automation_configuration"),
                    ("create_full_home_assistant_backup", "create_full_backup"),
                    ("create_input_boolean_configuration", "create_input_boolean_configuration"),
                    ("create_input_number_configuration", "create_input_number_configuration"),
                    ("create_script_configuration", "create_script_configuration"),
                    ("reload_home_assistant_configuration_domain", "controlled_reload"),
                    ("restart_home_assistant_core", "restart_home_assistant"),
                    ("restart_installed_home_assistant_addon", "restart_addon"),
                    ("update_automation_configuration", "update_automation_configuration"),
                    ("update_input_boolean_configuration", "update_input_boolean_configuration"),
                    ("update_input_number_configuration", "update_input_number_configuration"),
                    ("update_script_configuration", "update_script_configuration"),
                )
            },
        }
        reviewed = (
            routes._F3_DASHBOARD_OPERATIONS
            | routes._F3_TYPED_HELPER_OPERATIONS
            | routes._F3_GOVERNED_CONFIGURATION_OPERATIONS
            | routes._F3_ENTITY_METADATA_OPERATIONS
        )
        self.assertEqual(set(reviewed), set(identities))
        self.assertEqual(len(identities), 15)
        profiled = {profile.capability_id for profile in CORE_RUNTIME_CAPABILITY_PROFILES}
        for (capability_id, operation), requirement in identities.items():
            with self.subTest(identity=capability_id):
                values = f3_requirements(
                    SimpleNamespace(capability_id=capability_id, operation=operation)
                )
                self.assertEqual(
                    values, tuple(sorted({"core.f3_mutation_verification", requirement}))
                )
                self.assertTrue(set(values) <= profiled)

    async def asyncTearDown(self):
        replace_dynamic_upstream_capabilities((), {})

    async def test_unknown_identity_is_refused_while_a_mapped_route_is_admitted(self):
        runtime, _source = await core9.Core20269RuntimeTests()._runtime(
            _snapshot("2026.8.1", evidence=_evidence())
        )
        planning = runtime.acquire(("core.dependency_helper_planning",))
        self.assertIsNotNone(planning)
        self.assertTrue(runtime.release(planning))
        self.assertNotIn(
            "core.unsupported_f3_operation",
            {profile.capability_id for profile in CORE_RUNTIME_CAPABILITY_PROFILES},
        )
        before = runtime.health_snapshot().get("fallback_count")
        for prepared in (
            SimpleNamespace(capability_id="synthetic_identity", operation="synthetic_identity"),
            SimpleNamespace(capability_id="", operation=""),
            SimpleNamespace(
                capability_id="update_automation_configuration",
                capability_identity="update_script_configuration",
                operation="update_automation_configuration",
            ),
        ):
            with self.subTest(identity=prepared.capability_id):
                self.assertIn("core.unsupported_f3_operation", f3_requirements(prepared))
                self.assertIsNone(runtime.acquire_f3(prepared))
        self.assertEqual(runtime.health_snapshot().get("fallback_count"), before)
        # The refused identities left no lease that would block a later route.
        again = runtime.acquire(("core.dependency_helper_planning",))
        self.assertIsNotNone(again)
        self.assertTrue(runtime.release(again))


class ProductionCompositionTests(unittest.TestCase):
    def test_production_gateway_is_built_with_the_core_runtime(self):
        from dataclasses import replace

        from ha_mcp_engineering.application import create_application
        from ha_mcp_engineering.ha_core_readmission import CORE_READMISSION

        class SyntheticCoreSource:
            async def capture_core_snapshot(self):
                return _snapshot("2026.8.1", evidence=_evidence())

        with tempfile.TemporaryDirectory() as directory:
            configured = replace(
                settings(),
                audit_path=str(Path(directory) / "audit.jsonl"),
                governance_path=str(Path(directory) / "governance"),
            )
            gateway = create_application(
                configured, core_snapshot_source=SyntheticCoreSource()
            )
        # Production always installs the Core runtime, so the gateway's
        # mapped acquisition and unmapped refusal both apply in deployment.
        self.assertIsInstance(gateway, AuthenticatedMcpGateway)
        self.assertIs(gateway._core_runtime, CORE_READMISSION)

if __name__ == "__main__":
    unittest.main()
