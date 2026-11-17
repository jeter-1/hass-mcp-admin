"""HAMCP-401 Phase 1 and HAMCP-400: governed lifecycle on ha-mcp 9.2.0.

Architect task 2026-11-16 18:34 CST and ruling 19:35 CST:

* on 9.2.0 the lifecycle provider is admitted for controlled reload, app
  restart and Home Assistant restart, each dispatched at most once with exact
  constructed arguments and read back through the existing gateway;
* the add-on tools are selected per admitted release: 9.2.0 dispatches
  ``ha_manage_app`` and reads ``ha_get_app``; every earlier admitted release
  keeps ``ha_manage_addon`` and ``ha_get_addon``; the renamed-tool redirect is
  never relied on;
* Home Assistant restart is admitted without the app-endpoint binding, so it
  is verified only by Engineering's own observed Core outage and recovery;
* a held disposition (8.5.0 lifecycle, 9.2.0 backup) is ``provider_held``
  with the compatibility entry id in health and no dispatch; an unknown
  version stays ``upstream_version_mismatch``.

Committed captures and in-memory fakes only; every secret is synthetic.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from ha_mcp_engineering.clients.mcp import DashboardTransportError  # noqa: E402
from ha_mcp_engineering.clients.upstream_read import (  # noqa: E402
    CatalogValidationFailure,
    McpReadCatalog,
    McpReadResult,
)
from ha_mcp_engineering.errors import ErrorCode  # noqa: E402
from ha_mcp_engineering.governance.operational_lifecycle import (  # noqa: E402
    OperationalLifecycleGateway,
    _upstream_readmission_matches,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.providers.operational_backup import (  # noqa: E402
    OperationalBackupProviderError,
    ReviewedOperationalBackupProvider,
)
from ha_mcp_engineering.providers.operational_lifecycle import (  # noqa: E402
    ADDON_ACTION_TOOL,
    ADDON_READ_TOOL,
    APP_ACTION_TOOL,
    APP_READ_TOOL,
    HA_RESTART_TOOL,
    LIFECYCLE_ADDON_RESPONSE_ENVELOPE_STRUCTURED,
    LIFECYCLE_ADDON_RESPONSE_MODEL_STRUCTURED_V1,
    LIFECYCLE_ENVELOPE_EVIDENCE_REAL_CORE_SHARED_9_2_0,
    LIFECYCLE_ENVELOPE_EVIDENCE_REAL_CORE_SHARED_9_2_0_SOURCES,
    LIFECYCLE_ENVELOPE_EVIDENCE_SOURCE_DERIVED,
    RELOAD_TOOL,
    OperationalLifecycleProviderError,
    ReviewedOperationalLifecycleProvider,
    _TOOL_POLICY,
    _lifecycle_addon_response_contract,
)
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    _safe_catalog_fingerprint,
)
from ha_mcp_engineering.providers.supervisor_self import (  # noqa: E402
    SupervisorSelfAddonIdentity,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    load_reviewed_upstream_release_registry,
)
from tests.test_2_1a_beta2_operational_lifecycle import (  # noqa: E402
    FakeMcpTransport,
    UPSTREAM_ADDON_NAME,
    UPSTREAM_ADDON_SLUG,
    lifecycle_settings,
)

VERSION = "9.2.0"
ENTRY = "ha-mcp-v9.2.0-9de5bf6c"
PROTOCOL = "2025-03-26"
HELD_LIFECYCLE = "8.5.0"
LEGACY_ADMITTED = "8.1.0"
ENDPOINT = "http://abcdef12-ha-mcp:9583/synthetic-upstream-secret/mcp"
OTHER_ADDON = "local_test_addon"
ACTION_TOOLS = {RELOAD_TOOL, HA_RESTART_TOOL, ADDON_ACTION_TOOL, APP_ACTION_TOOL}
CAPTURES = ROOT / "docs" / "evidence" / "upstream-read-compatibility"
SHARED_ENVELOPE_CAPTURE = (
    ROOT / "docs/evidence/hamcp371/s6/run-b-9.2.0-2/out/9.2.0/calls/t-large-services.json"
)
REGISTRY = load_reviewed_upstream_release_registry()
MODULE = "ha_mcp_engineering.governance.operational_lifecycle"


def catalog_920(*, rename=None, server_version=VERSION) -> McpReadCatalog:
    """The committed 9.2.0 capture in its reviewed runtime order."""

    raw = json.loads((CAPTURES / f"ha-mcp-{VERSION}.json").read_text(encoding="utf-8"))
    review = json.loads(
        (CAPTURES / f"ha-mcp-{VERSION}-contract-review.json").read_text(encoding="utf-8")
    )
    by_name = {tool["name"]: tool for tool in raw["tools"]}
    tools = [deepcopy(by_name[name]) for name in review["runtime_catalog"]["runtime_tool_order"]]
    for tool in tools:
        if rename and tool["name"] in rename:
            tool["name"] = rename[tool["name"]]
    return McpReadCatalog(PROTOCOL, "ha-mcp", server_version, tuple(tools), 1.0)


FS_ON_CAPTURE = (
    ROOT / "docs/evidence/hamcp371/s8/profiles-2/out/standalone-9.2.0-fs-on/capture.json"
)
OPTIONAL_TOOLS = (
    "ha_config_get_yaml",
    "ha_delete_file",
    "ha_list_files",
    "ha_read_file",
    "ha_write_file",
)


def catalog_920_filesystem_on(*, mutate=None, duplicate=None, extra=None) -> McpReadCatalog:
    """The reviewed 9.2.0 stock catalog plus the committed fs-on optional tools.

    The five optional descriptors come from the real 9.2.0 capture with
    filesystem tools enabled; they are interleaved, not appended.
    """

    capture = json.loads(FS_ON_CAPTURE.read_text(encoding="utf-8"))
    optional = {t["name"]: deepcopy(t) for t in capture["tools"] if t["name"] in OPTIONAL_TOOLS}
    if mutate:
        mutate(optional)
    tools = list(catalog_920().tools)
    for index, name in enumerate(OPTIONAL_TOOLS):
        tools.insert(index * 15, optional[name])
    if duplicate:
        tools.append(deepcopy(optional[duplicate]))
    if extra:
        tools.insert(7, extra)
    return McpReadCatalog(PROTOCOL, "ha-mcp", VERSION, tuple(tools), 1.0)


class Transport920(FakeMcpTransport):
    """A 9.2.0 ha-mcp peer: app tool names and the structured envelope."""

    def __init__(self, catalog: McpReadCatalog | None = None) -> None:
        super().__init__(LEGACY_ADMITTED)
        self.catalog = catalog or catalog_920()
        for addon in self.addons:
            if addon["slug"] == UPSTREAM_ADDON_SLUG:
                addon["version"] = self.catalog.server_version
        self.restart_reply = {"success": True, "message": "Home Assistant restart scheduled."}
        self.lose_transport_after_dispatch = False

    async def execute_read(
        self,
        tool_name,
        arguments,
        *,
        timeout_seconds,
        catalog_validator,
        before_dispatch=None,
    ):
        catalog_validator(self.catalog)
        if before_dispatch is not None:
            value = before_dispatch()
            if hasattr(value, "__await__"):
                await value
        self.calls.append((tool_name, deepcopy(arguments)))
        if self.lose_transport_after_dispatch:
            raise DashboardTransportError("connection_failed")
        if tool_name == RELOAD_TOOL:
            payload = {
                "success": True,
                "message": "Reloaded.",
                "target": arguments["target"],
                "service": "automation.reload",
            }
        elif tool_name == APP_ACTION_TOOL:
            payload = {
                "success": True,
                "action": "restart",
                "slug": arguments["slug"],
                "message": "App restarted.",
            }
        elif tool_name == HA_RESTART_TOOL:
            payload = deepcopy(self.restart_reply)
        elif tool_name == APP_READ_TOOL and arguments.get("source") == "installed":
            payload = {
                "success": True,
                "addons": deepcopy(self.addons),
                "summary": {
                    "total_installed": len(self.addons),
                    "running": sum(1 for a in self.addons if a["state"] == "started"),
                    "stopped": sum(1 for a in self.addons if a["state"] != "started"),
                    "updates_available": 0,
                },
            }
        elif tool_name == APP_READ_TOOL:
            addon = next((a for a in self.addons if a["slug"] == arguments["slug"]), None)
            payload = {"success": True, "addon": deepcopy(addon)}
        else:  # a legacy name never reaches a 9.2.0 peer
            raise AssertionError(f"unexpected 9.2.0 tool {tool_name}")
        return McpReadResult(
            protocol_version=self.catalog.protocol_version,
            server_name=self.catalog.server_name,
            server_version=self.catalog.server_version,
            call_result={
                "content": [{"type": "text", "text": json.dumps(payload)}],
                "structuredContent": deepcopy(payload),
                "isError": False,
            },
            connection_latency_ms=1.0,
            tool_call_latency_ms=1.0,
        )


def provider_for(transport) -> ReviewedOperationalLifecycleProvider:
    provider = ReviewedOperationalLifecycleProvider()
    provider.configure(lifecycle_settings(ENDPOINT), transport=transport)
    return provider


def runtime_snapshot() -> dict:
    return {
        "server_version": "2.4.2-beta.10",
        "build_sha": "a" * 40,
        "registered_tool_count": 74,
        "engineering_tool_count": 48,
        "delegated_tool_count": 26,
        "governance_storage_status": "healthy",
        "governance_plan_count": 1,
        "audit_storage_status": "healthy",
        "audit_write_failures": 0,
        "dependency_index_state": "valid",
        "dependency_prewarm_state": "complete",
        "upstream_version": VERSION,
        "upstream_catalog_fingerprint": "catalog-fingerprint",
        "upstream_admission_status": "admitted_exact",
        "fallback_count": 0,
    }


class Rest:
    """Engineering's own Core probe: GET /config, optionally unavailable."""

    def __init__(self) -> None:
        self.available = True
        self.calls = 0

    async def request(self, method, path):
        from ha_mcp_engineering.errors import HomeAssistantUnavailableError

        assert (method, path) == ("GET", "/config"), (method, path)
        self.calls += 1
        if not self.available:
            raise HomeAssistantUnavailableError()
        return {"location_name": "Synthetic Home", "version": "2026.9.2"}


class WebSocket:
    async def command(self, payload):
        if payload == {"type": "get_services"}:
            return {"automation": {"reload": {}}}
        if payload == {"type": "get_states"}:
            return [{"entity_id": "automation.fixture", "state": "on"}]
        raise AssertionError(f"unexpected WebSocket request {payload}")


def gateway_for(provider, rest=None) -> OperationalLifecycleGateway:
    async def validate_configuration():
        return {"result": "valid", "errors": None}

    async def self_identity():
        return SupervisorSelfAddonIdentity(
            slug="df26dea6_hass_mcp_engineering_beta",
            name="HA MCP Engineering Server Beta",
            version="2.4.2-beta.10",
            repository="df26dea6",
        )

    return OperationalLifecycleGateway(
        provider,
        rest or Rest(),
        WebSocket(),
        configuration_validator=validate_configuration,
        runtime_snapshot=runtime_snapshot,
        process_instance_id="hamcp401-process",
        self_addon_identity_resolver=self_identity,
    )


def action_calls(transport) -> list:
    return [(name, args) for name, args in transport.calls if name in ACTION_TOOLS]


class ReviewedRowTests(unittest.TestCase):
    def test_920_lifecycle_admitted_and_backup_held(self):
        release = REGISTRY.by_version[VERSION]
        self.assertEqual(release.entry_id, ENTRY)
        self.assertEqual(release.provider_disposition("lifecycle"), "admitted")
        self.assertEqual(release.provider_disposition("backup"), "held")
        self.assertEqual(REGISTRY.by_version[HELD_LIFECYCLE].provider_disposition("lifecycle"), "held")

    def test_920_classifications_equal_tool_policy(self):
        tools = {tool["name"]: tool for tool in catalog_920().tools}
        for name in (RELOAD_TOOL, HA_RESTART_TOOL, APP_ACTION_TOOL, APP_READ_TOOL):
            with self.subTest(tool=name):
                self.assertIn(name, tools)
        self.assertEqual(_TOOL_POLICY[RELOAD_TOOL], "physical_or_high_risk_action")
        self.assertEqual(_TOOL_POLICY[HA_RESTART_TOOL], "physical_or_high_risk_action")
        self.assertEqual(_TOOL_POLICY[APP_ACTION_TOOL], "mixed_or_requires_wrapper")
        self.assertEqual(_TOOL_POLICY[APP_READ_TOOL], "mixed_or_requires_wrapper")
        self.assertNotIn(ADDON_ACTION_TOOL, tools)
        self.assertNotIn(ADDON_READ_TOOL, tools)

    def test_920_contract_row_names_app_tools_and_envelope_evidence(self):
        contract = _lifecycle_addon_response_contract(
            entry_id=ENTRY, upstream_version=VERSION, protocol_version=PROTOCOL
        )
        self.assertIsNotNone(contract)
        self.assertEqual(contract.model, LIFECYCLE_ADDON_RESPONSE_MODEL_STRUCTURED_V1)
        self.assertEqual(contract.envelope_variant, LIFECYCLE_ADDON_RESPONSE_ENVELOPE_STRUCTURED)
        self.assertEqual(contract.upstream_tool(ADDON_ACTION_TOOL), APP_ACTION_TOOL)
        self.assertEqual(contract.upstream_tool(ADDON_READ_TOOL), APP_READ_TOOL)
        self.assertEqual(contract.upstream_tool(RELOAD_TOOL), RELOAD_TOOL)
        self.assertEqual(contract.upstream_tool(HA_RESTART_TOOL), HA_RESTART_TOOL)
        evidence = dict(contract.envelope_evidence)
        self.assertEqual(
            LIFECYCLE_ENVELOPE_EVIDENCE_REAL_CORE_SHARED_9_2_0,
            "real-Core 9.2.0 capture of the shared envelope, from another tool; "
            "not a direct call of ha_reload_core or ha_restart",
        )
        self.assertEqual(evidence[RELOAD_TOOL], LIFECYCLE_ENVELOPE_EVIDENCE_REAL_CORE_SHARED_9_2_0)
        self.assertEqual(evidence[HA_RESTART_TOOL], LIFECYCLE_ENVELOPE_EVIDENCE_REAL_CORE_SHARED_9_2_0)
        self.assertEqual(evidence[APP_ACTION_TOOL], LIFECYCLE_ENVELOPE_EVIDENCE_SOURCE_DERIVED)
        self.assertEqual(evidence[APP_READ_TOOL], LIFECYCLE_ENVELOPE_EVIDENCE_SOURCE_DERIVED)
        self.assertEqual(LIFECYCLE_ENVELOPE_EVIDENCE_SOURCE_DERIVED, "source_derived")
        for source in LIFECYCLE_ENVELOPE_EVIDENCE_REAL_CORE_SHARED_9_2_0_SOURCES:
            with self.subTest(source=source):
                self.assertTrue((ROOT / source).is_file())

    def test_shared_envelope_capture_is_one_text_item_equal_to_structured(self):
        capture = json.loads(SHARED_ENVELOPE_CAPTURE.read_text(encoding="utf-8"))
        self.assertEqual(capture["upstream_tool"], "ha_list_services")
        result = capture["exchange"]["response"]["result"]
        content = result["content"]
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(json.loads(content[0]["text"]), result["structuredContent"])

    def test_four_tools_share_the_open_output_schema(self):
        tools = {tool["name"]: tool for tool in catalog_920().tools}
        for name in (RELOAD_TOOL, HA_RESTART_TOOL, APP_ACTION_TOOL, APP_READ_TOOL):
            with self.subTest(tool=name):
                self.assertEqual(
                    tools[name].get("outputSchema"),
                    {"additionalProperties": True, "type": "object"},
                )

    def test_every_other_admitted_release_keeps_legacy_names(self):
        for release in REGISTRY.releases:
            if release.version == VERSION or release.provider_disposition("lifecycle") != "admitted":
                continue
            contract = _lifecycle_addon_response_contract(
                entry_id=release.entry_id,
                upstream_version=release.version,
                protocol_version=PROTOCOL,
            )
            if contract is None:
                continue
            with self.subTest(version=release.version):
                self.assertEqual(contract.upstream_tool(ADDON_ACTION_TOOL), ADDON_ACTION_TOOL)
                self.assertEqual(contract.upstream_tool(ADDON_READ_TOOL), ADDON_READ_TOOL)


class PositiveDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_reload_dispatches_once_with_target_and_reads_back(self):
        transport = Transport920()
        provider = provider_for(transport)
        gateway = gateway_for(provider)
        planned = await gateway.planning_evidence("controlled_reload", "automation")
        self.assertEqual(planned["provider"]["compatibility_entry_id"], ENTRY)
        persisted = []
        result = await gateway.dispatch_reload("automation", before_dispatch=lambda: persisted.append(1))
        self.assertEqual(action_calls(transport), [(RELOAD_TOOL, {"target": "automations"})])
        self.assertNotIn("entry_id", transport.calls[-1][1])
        self.assertEqual(persisted, [1])
        self.assertEqual(result.provider_evidence.compatibility_entry_id, ENTRY)
        verified = await gateway.verify_reload("automation")
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(len(action_calls(transport)), 1)
        health = provider.health_snapshot()
        self.assertEqual(health["dispatch_counts"]["controlled_reload"], 1)
        self.assertEqual(health["selected_compatibility_entry_id"], ENTRY)
        self.assertEqual(health["fallback_count"], 0)

    async def test_app_restart_dispatches_once_through_app_tools_and_reads_back(self):
        transport = Transport920()
        provider = provider_for(transport)
        gateway = gateway_for(provider)
        planned = await gateway.planning_evidence("restart_addon", OTHER_ADDON)
        self.assertEqual(planned["baseline"]["target_class"], "other_addon")
        self.assertEqual(planned["provider"]["compatibility_entry_id"], ENTRY)
        persisted = []
        result = await gateway.dispatch_addon_restart(
            OTHER_ADDON, before_dispatch=lambda: persisted.append(1)
        )
        self.assertEqual(
            action_calls(transport),
            [(APP_ACTION_TOOL, {"slug": OTHER_ADDON, "action": "restart"})],
        )
        self.assertEqual(persisted, [1])
        verified = await gateway.verify_addon_restart(
            OTHER_ADDON,
            baseline=planned["baseline"],
            provider_response_received=result.provider_response_received,
            provider_evidence=result.provider_evidence.as_dict(),
        )
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["evidence"]["restart_proof"], "provider_acknowledgement")
        names = {name for name, _args in transport.calls}
        self.assertLessEqual(names, {APP_READ_TOOL, APP_ACTION_TOOL})
        self.assertIn(APP_READ_TOOL, names)
        self.assertEqual(len(action_calls(transport)), 1)
        self.assertEqual(provider.health_snapshot()["selected_compatibility_entry_id"], ENTRY)

    async def test_home_assistant_restart_dispatches_confirm_only(self):
        transport = Transport920()
        provider = provider_for(transport)
        gateway = gateway_for(provider)
        await gateway.planning_evidence("restart_home_assistant", "home_assistant")
        persisted = []
        result = await gateway.dispatch_home_assistant_restart(
            before_dispatch=lambda: persisted.append(1)
        )
        self.assertEqual(action_calls(transport), [(HA_RESTART_TOOL, {"confirm": True})])
        self.assertNotIn("safe_mode", transport.calls[-1][1])
        self.assertEqual(persisted, [1])
        self.assertEqual(result.response, {"success": True, "restart_initiated": True})
        self.assertEqual(result.provider_evidence.compatibility_entry_id, ENTRY)

    async def test_transport_loss_after_dispatch_is_indeterminate_and_not_retried(self):
        for operation in ("controlled_reload", "restart_addon", "restart_home_assistant"):
            with self.subTest(operation=operation):
                transport = Transport920()
                provider = provider_for(transport)
                await provider.probe(operation)
                transport.lose_transport_after_dispatch = True
                with self.assertRaises(OperationalLifecycleProviderError) as raised:
                    if operation == "controlled_reload":
                        await provider.reload("automation", before_dispatch=lambda: None)
                    elif operation == "restart_addon":
                        await provider.restart_addon(OTHER_ADDON, before_dispatch=lambda: None)
                    else:
                        await provider.restart_home_assistant(before_dispatch=lambda: None)
                self.assertEqual(raised.exception.category, "indeterminate_dispatch")
                self.assertTrue(raised.exception.dispatched)
                self.assertEqual(len(action_calls(transport)), 1)


class HomeAssistantRestartVerificationTests(unittest.IsolatedAsyncioTestCase):
    """Ruling 19:35 item 1: verification rests on Engineering's own outage."""

    async def _dispatched(self, rest):
        transport = Transport920()
        provider = provider_for(transport)
        gateway = gateway_for(provider, rest)
        planned = await gateway.planning_evidence("restart_home_assistant", "home_assistant")
        await gateway.dispatch_home_assistant_restart(before_dispatch=lambda: None)
        return transport, gateway, planned["baseline"]

    async def test_reply_without_observed_outage_is_not_verified(self):
        rest = Rest()
        transport, gateway, baseline = await self._dispatched(rest)
        deadline = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
        with patch(f"{MODULE}.RESTART_DISRUPTION_PROBE_ATTEMPTS", 2), patch(
            f"{MODULE}.RESTART_DISRUPTION_PROBE_INTERVAL_SECONDS", 0
        ):
            result = await gateway.verify_home_assistant_restart(
                baseline=baseline,
                restart_dispatch_confirmed=True,
                authoritative_outage_observed=False,
                outage_observation_window_open=True,
                outage_observation_deadline=deadline,
            )
        self.assertNotEqual(result["status"], "verified")
        self.assertIn("restart_evidence", result["mismatch_fields"])
        self.assertFalse(result["evidence"]["outage_observed"])
        self.assertFalse(result["evidence"]["redispatch_performed"])
        self.assertEqual(action_calls(transport), [(HA_RESTART_TOOL, {"confirm": True})])

    async def test_reply_with_observed_outage_and_recovery_is_verified(self):
        rest = Rest()
        transport, gateway, baseline = await self._dispatched(rest)
        deadline = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
        rest.available = False
        outage = await gateway.verify_home_assistant_restart(
            baseline=baseline,
            restart_dispatch_confirmed=True,
            authoritative_outage_observed=False,
            outage_observation_window_open=True,
            outage_observation_deadline=deadline,
        )
        self.assertEqual(outage["status"], "pending")
        self.assertTrue(outage["evidence"]["outage_observed"])
        self.assertEqual(
            outage["evidence"]["restart_evidence_sources"],
            ["home_assistant_core_connection_probe"],
        )
        rest.available = True
        recovered = await gateway.verify_home_assistant_restart(
            baseline=baseline,
            restart_dispatch_confirmed=True,
            authoritative_outage_observed=True,
            outage_observation_window_open=False,
            outage_observation_deadline=deadline,
        )
        self.assertEqual(recovered["status"], "verified", recovered["mismatch_fields"])
        self.assertTrue(recovered["evidence"]["home_assistant_reconnected"])
        self.assertEqual(recovered["evidence"]["upstream"]["compatibility_entry_id"], ENTRY)
        self.assertFalse(recovered["evidence"]["redispatch_performed"])
        self.assertEqual(action_calls(transport), [(HA_RESTART_TOOL, {"confirm": True})])


class RefusalTests(unittest.IsolatedAsyncioTestCase):
    async def test_920_catalog_with_only_addon_names_is_required_tool_missing(self):
        renamed = catalog_920(rename={APP_ACTION_TOOL: ADDON_ACTION_TOOL, APP_READ_TOOL: ADDON_READ_TOOL})
        names = {tool["name"] for tool in renamed.tools}
        self.assertFalse({APP_ACTION_TOOL, APP_READ_TOOL} & names)
        transport = Transport920(renamed)
        provider = provider_for(transport)
        with self.assertRaises(OperationalLifecycleProviderError) as raised:
            await provider.probe("restart_addon")
        self.assertEqual(raised.exception.category, "required_tool_missing")
        self.assertFalse(raised.exception.dispatched)
        with self.assertRaises(OperationalLifecycleProviderError) as raised:
            await provider.restart_addon(OTHER_ADDON, before_dispatch=lambda: None)
        self.assertEqual(raised.exception.category, "required_tool_missing")
        self.assertEqual(transport.calls, [])

    async def test_920_app_call_without_prior_probe_is_catalog_mismatch(self):
        persisted = []
        calls = {
            "restart_addon": lambda provider: provider.restart_addon(
                OTHER_ADDON, before_dispatch=lambda: persisted.append(1)
            ),
            "get_addon": lambda provider: provider.get_addon(OTHER_ADDON),
        }
        for name, call in calls.items():
            with self.subTest(call=name):
                transport = Transport920()
                provider = provider_for(transport)
                with self.assertRaises(OperationalLifecycleProviderError) as raised:
                    await call(provider)
                self.assertEqual(raised.exception.category, "catalog_mismatch")
                self.assertFalse(raised.exception.dispatched)
                self.assertEqual((transport.calls, persisted), ([], []))

    async def test_selection_from_another_release_is_catalog_mismatch(self):
        # A 9.2.0 selection never dispatches against a peer that is now 8.1.0.
        transport = Transport920()
        provider = provider_for(transport)
        await provider.probe("restart_addon")
        legacy = FakeMcpTransport(LEGACY_ADMITTED)
        provider._transport = legacy
        with self.assertRaises(OperationalLifecycleProviderError) as raised:
            await provider.restart_addon(OTHER_ADDON, before_dispatch=lambda: None)
        self.assertEqual(raised.exception.category, "catalog_mismatch")
        self.assertEqual(legacy.calls, [])

    async def test_held_lifecycle_850_is_provider_held_with_entry_id(self):
        catalog = McpReadCatalog(PROTOCOL, "ha-mcp", HELD_LIFECYCLE, (), 1.0)
        for operation in ("controlled_reload", "restart_addon", "restart_home_assistant"):
            with self.subTest(operation=operation):
                transport = Transport920(catalog)
                provider = provider_for(transport)
                with self.assertRaises(OperationalLifecycleProviderError) as raised:
                    await provider.probe(operation)
                self.assertEqual(raised.exception.category, "provider_held")
                self.assertFalse(raised.exception.dispatched)
                health = provider.health_snapshot()
                self.assertEqual(
                    health["selected_compatibility_entry_id"],
                    REGISTRY.by_version[HELD_LIFECYCLE].entry_id,
                )
                self.assertEqual(health["observed_upstream_version"], HELD_LIFECYCLE)
                self.assertEqual(sum(health["dispatch_counts"].values()), 0)
                self.assertEqual(transport.calls, [])
        self.assertEqual(ChangeGovernanceService._lifecycle_error_code("provider_held", dispatched=False), ErrorCode.OPERATIONAL_CONTRACT_MISMATCH)

    def test_held_backup_920_is_provider_held_with_entry_id(self):
        provider = ReviewedOperationalBackupProvider()
        with self.assertRaises(OperationalBackupProviderError) as raised:
            provider._validate_catalog(catalog_920())
        self.assertEqual(raised.exception.category, "provider_held")
        self.assertFalse(raised.exception.dispatched)
        health = provider.health_snapshot()
        self.assertEqual(health["selected_compatibility_entry_id"], ENTRY)
        self.assertEqual(health["observed_upstream_version"], VERSION)
        self.assertEqual(
            ChangeGovernanceService._operational_error_code("provider_held", dispatched=False),
            ErrorCode.BACKUP_PROVIDER_UNAVAILABLE,
        )

    async def test_unknown_version_is_still_upstream_version_mismatch(self):
        for version in ("9.2.1", "9.3.0"):
            with self.subTest(version=version):
                self.assertNotIn(version, REGISTRY.by_version)
                transport = Transport920(catalog_920(server_version=version))
                provider = provider_for(transport)
                with self.assertRaises(OperationalLifecycleProviderError) as raised:
                    await provider.probe("restart_home_assistant")
                self.assertEqual(raised.exception.category, "upstream_version_mismatch")
                self.assertIsNone(provider.health_snapshot()["selected_compatibility_entry_id"])
                with self.assertRaises(OperationalBackupProviderError) as backup:
                    ReviewedOperationalBackupProvider()._validate_catalog(
                        catalog_920(server_version=version)
                    )
                self.assertEqual(backup.exception.category, "upstream_version_mismatch")
                self.assertEqual(transport.calls, [])


class FilesystemToolsEnabledTests(unittest.IsolatedAsyncioTestCase):
    """I1: a real 9.2.0 session with the optional filesystem tools enabled.

    The exact-image 91_only lane reproduced locally failed every lifecycle
    probe as catalog_mismatch: 82 advertised tools, the 77 reviewed plus the
    five bound optional ones. They are accounted outside stock only on an
    exact per-version binding match, and never dispatched.
    """

    def test_capture_is_the_bound_fs_on_capture(self):
        capture = json.loads(FS_ON_CAPTURE.read_text(encoding="utf-8"))
        self.assertEqual((capture["server_version"], capture["tool_count"]), (VERSION, 82))
        reviewed = set(REGISTRY.by_version[VERSION].tool_contracts_by_name)
        self.assertEqual({t["name"] for t in capture["tools"]} - reviewed, set(OPTIONAL_TOOLS))

    async def test_every_operation_probes_and_binds_with_filesystem_tools_enabled(self):
        for operation in ("controlled_reload", "restart_addon", "restart_home_assistant"):
            with self.subTest(operation=operation):
                transport = Transport920(catalog_920_filesystem_on())
                provider = provider_for(transport)
                evidence = (await provider.probe(operation)).as_dict()
                self.assertEqual(evidence["compatibility_entry_id"], ENTRY)
                self.assertFalse(evidence["fallback_occurred"])
                validation = provider.health_snapshot()["catalog_validation"]
                self.assertEqual(validation["validation_status"], "accepted_exact")
                self.assertEqual(validation["observed_tool_count"], 77)
                self.assertEqual(validation["additional_tools"], [])
                self.assertEqual(transport.calls, [])

    async def test_app_restart_dispatches_once_and_never_an_optional_tool(self):
        transport = Transport920(catalog_920_filesystem_on())
        provider = provider_for(transport)
        await provider.probe("restart_addon")
        persisted = []
        await provider.restart_addon(OTHER_ADDON, before_dispatch=lambda: persisted.append(1))
        self.assertEqual(
            action_calls(transport),
            [(APP_ACTION_TOOL, {"slug": OTHER_ADDON, "action": "restart"})],
        )
        self.assertEqual(persisted, [1])
        self.assertFalse({name for name, _ in transport.calls} & set(OPTIONAL_TOOLS))

    async def test_drifted_duplicated_or_unbound_extras_stay_catalog_mismatch(self):
        def drift(optional):
            optional["ha_write_file"]["description"] += " Drifted."

        def drift_fields(optional):
            optional["ha_config_get_yaml"]["_meta"] = {"synthetic": True}

        cases = {
            "drifted_sibling": catalog_920_filesystem_on(mutate=drift),
            "drifted_fields": catalog_920_filesystem_on(mutate=drift_fields),
            "duplicated_optional": catalog_920_filesystem_on(duplicate="ha_read_file"),
            "unbound_extra": catalog_920_filesystem_on(
                extra={"name": "ha_unreviewed_tool", "inputSchema": {"type": "object"}}
            ),
        }
        for name, catalog in cases.items():
            with self.subTest(case=name):
                transport = Transport920(catalog)
                provider = provider_for(transport)
                with self.assertRaises(OperationalLifecycleProviderError) as raised:
                    await provider.probe("restart_addon")
                self.assertEqual(raised.exception.category, "catalog_mismatch")
                self.assertFalse(raised.exception.dispatched)
                persisted = []
                with self.assertRaises(OperationalLifecycleProviderError) as raised:
                    await provider.restart_addon(
                        OTHER_ADDON, before_dispatch=lambda: persisted.append(1)
                    )
                self.assertEqual(raised.exception.category, "catalog_mismatch")
                self.assertEqual((transport.calls, persisted), ([], []))

    async def test_identity_fingerprint_is_the_gateways_whole_catalog(self):
        """Cursor I1 F1: the provider identity hashes the advertised catalog,
        as the read gateway does, so upstream add-on readmission can match
        while bound optional tools are advertised. Validation still sees the
        77 stock tools only."""

        def runtime(catalog):
            return {
                "upstream_version": VERSION,
                "upstream_protocol": PROTOCOL,
                "upstream_catalog_fingerprint": _safe_catalog_fingerprint(
                    list(catalog.tools)
                ),
                "upstream_admission_status": "admitted_exact",
                "fallback_count": 0,
            }

        fs_on = catalog_920_filesystem_on()
        stock = catalog_920()
        self.assertNotEqual(
            _safe_catalog_fingerprint(list(fs_on.tools)),
            _safe_catalog_fingerprint(list(stock.tools)),
        )
        for name, catalog in (("filesystem_on", fs_on), ("stock_only", stock)):
            with self.subTest(catalog=name):
                transport = Transport920(catalog)
                provider = provider_for(transport)
                evidence = (await provider.probe("restart_addon")).as_dict()
                self.assertEqual(
                    evidence["catalog_fingerprint"],
                    _safe_catalog_fingerprint(list(catalog.tools)),
                )
                validation = provider.health_snapshot()["catalog_validation"]
                self.assertEqual(validation["validation_status"], "accepted_exact")
                self.assertEqual(validation["observed_tool_count"], 77)
                self.assertTrue(
                    _upstream_readmission_matches(evidence, evidence, runtime(catalog))
                )
                self.assertEqual(transport.calls, [])
        # A different advertised catalog, or a non-exact admission, never matches.
        evidence = (
            await provider_for(Transport920(fs_on)).probe("restart_addon")
        ).as_dict()
        self.assertFalse(_upstream_readmission_matches(evidence, evidence, runtime(stock)))
        self.assertFalse(
            _upstream_readmission_matches(
                evidence,
                evidence,
                dict(runtime(fs_on), upstream_admission_status="admitted_with_drift"),
            )
        )

    async def test_missing_stock_tool_is_not_masked_by_optional_tools(self):
        catalog = catalog_920_filesystem_on()
        stock = [t for t in catalog.tools if t["name"] != "ha_get_overview"]
        self.assertEqual(len(stock), len(catalog.tools) - 1)
        transport = Transport920(McpReadCatalog(PROTOCOL, "ha-mcp", VERSION, tuple(stock), 1.0))
        provider = provider_for(transport)
        with self.assertRaises(OperationalLifecycleProviderError) as raised:
            await provider.probe("controlled_reload")
        self.assertEqual(raised.exception.category, "catalog_mismatch")
        validation = provider.health_snapshot()["catalog_validation"]
        self.assertEqual(validation["missing_tools"], ["ha_get_overview"])


class LegacyReleaseUnchangedTests(unittest.IsolatedAsyncioTestCase):
    async def test_810_dispatch_and_reads_keep_legacy_names(self):
        transport = FakeMcpTransport(LEGACY_ADMITTED)
        provider = provider_for(transport)
        await provider.probe("restart_addon")
        addon = await provider.get_addon(UPSTREAM_ADDON_SLUG)
        self.assertEqual(addon["name"], UPSTREAM_ADDON_NAME)
        await provider.reload("automation", before_dispatch=lambda: None)
        await provider.restart_addon(OTHER_ADDON, before_dispatch=lambda: None)
        await provider.restart_home_assistant(before_dispatch=lambda: None)
        self.assertEqual(
            transport.calls,
            [
                (ADDON_READ_TOOL, {"source": "installed", "include_stats": False}),
                (ADDON_READ_TOOL, {"slug": UPSTREAM_ADDON_SLUG}),
                (RELOAD_TOOL, {"target": "automations"}),
                (ADDON_ACTION_TOOL, {"slug": OTHER_ADDON, "action": "restart"}),
                (HA_RESTART_TOOL, {"confirm": True}),
            ],
        )

    async def test_legacy_call_without_probe_keeps_legacy_names(self):
        transport = FakeMcpTransport(LEGACY_ADMITTED)
        provider = provider_for(transport)
        await provider.restart_addon(OTHER_ADDON, before_dispatch=lambda: None)
        self.assertEqual(transport.calls, [(ADDON_ACTION_TOOL, {"slug": OTHER_ADDON, "action": "restart"})])



def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(
        f"{name}_hamcp401_test_module", ROOT / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


gateway_acceptance = _load_script("exact_image_read_gateway_acceptance")
FIXTURE_COUNTERS = {
    "rest_reads": 3,
    "websocket_reads": 2,
    "http_mutations": 0,
    "websocket_mutations": 0,
}


class ExactImageOperationalAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    """Architect ruling 2026-11-16 21:56 CST: the exact-image harness follows
    each registry row. Held providers expect ``provider_held``; on 9.2.0 the
    lifecycle probe must bind the reviewed contracts with no dispatch."""

    async def _run(self, version, catalog, *, counters=None):
        calls = []

        async def discover(_transport):
            return catalog

        async def execute_read(_transport, tool_name, arguments, *, catalog_validator, **_):
            calls.append(tool_name)
            try:
                catalog_validator(catalog)
            except Exception as exc:
                raise CatalogValidationFailure(exc) from None
            raise AssertionError("a held provider passed its catalog validator")

        transport = gateway_acceptance.McpReadGatewayTransport
        with patch.object(transport, "discover", discover), patch.object(
            transport, "execute_read", execute_read
        ), patch.object(
            gateway_acceptance,
            "fixture_stats",
            side_effect=counters or [dict(FIXTURE_COUNTERS), dict(FIXTURE_COUNTERS)],
        ):
            result = await gateway_acceptance.held_operational_provider_acceptance(
                REGISTRY.by_version[version],
                endpoint=ENDPOINT,
                fixture_stats_url="http://fixture.invalid/__fixture__/stats",
                settings=lifecycle_settings(ENDPOINT),
            )
        return result, calls

    async def test_920_backup_held_and_lifecycle_probe_binds_reviewed_contracts(self):
        (backup, lifecycle), calls = await self._run(VERSION, catalog_920())
        self.assertEqual(len(calls), 1)  # the held backup attempt only
        self.assertEqual(backup["status"], "quarantined")
        self.assertEqual(backup["provider_dispatch_count"], 0)
        self.assertEqual(lifecycle["status"], "admitted")
        self.assertTrue(lifecycle["probe_only"])
        self.assertEqual(lifecycle["compatibility_entry_id"], ENTRY)
        self.assertEqual(lifecycle["provider_dispatch_count"], 0)
        contracts = REGISTRY.by_version[VERSION].tool_contracts_by_name
        expected = {
            "controlled_reload": (RELOAD_TOOL,),
            "restart_addon": (APP_READ_TOOL, APP_ACTION_TOOL),
            "restart_home_assistant": (HA_RESTART_TOOL,),
        }
        self.assertEqual(set(lifecycle["operations"]), set(expected))
        for operation, tools in expected.items():
            with self.subTest(operation=operation):
                bound = lifecycle["operations"][operation]
                self.assertEqual(bound["compatibility_entry_id"], ENTRY)
                self.assertEqual(
                    bound["tool_contract_fingerprints"],
                    {tool: contracts[tool].runtime_contract_fingerprint for tool in tools},
                )
        self.assertFalse(
            lifecycle["operations"]["controlled_reload"]["argument_constraints"]["entry_id_allowed"]
        )
        self.assertFalse(
            lifecycle["operations"]["restart_home_assistant"]["argument_constraints"]["variants_allowed"]
        )

    async def test_held_850_expects_provider_held_for_both_providers(self):
        catalog = McpReadCatalog(PROTOCOL, "ha-mcp", HELD_LIFECYCLE, (), 1.0)
        (backup, lifecycle), calls = await self._run(HELD_LIFECYCLE, catalog)
        self.assertEqual(len(calls), 2)  # one refused attempt per provider
        for result in (backup, lifecycle):
            self.assertEqual(result["status"], "quarantined")
            self.assertEqual(result["provider_disposition"], "held")
            self.assertEqual(result["provider_dispatch_count"], 0)

    async def test_920_probe_contract_drift_fails_acceptance(self):
        catalog = catalog_920()
        tools = [dict(tool) for tool in catalog.tools]
        for tool in tools:
            if tool["name"] == HA_RESTART_TOOL:
                tool["description"] = "Restart Home Assistant (drifted)."
        drifted = McpReadCatalog(PROTOCOL, "ha-mcp", VERSION, tuple(tools), 1.0)
        with self.assertRaisesRegex(
            gateway_acceptance.AcceptanceFailure, "admitted lifecycle probe"
        ):
            await self._run(VERSION, drifted)

    async def test_fixture_counter_change_fails_acceptance(self):
        changed = dict(FIXTURE_COUNTERS, rest_reads=FIXTURE_COUNTERS["rest_reads"] + 1)
        with self.assertRaisesRegex(
            gateway_acceptance.AcceptanceFailure, "reached Home Assistant"
        ):
            await self._run(
                VERSION, catalog_920(), counters=[dict(FIXTURE_COUNTERS), changed]
            )

    async def test_unreviewed_disposition_or_missing_expectation_fails(self):
        admitted_backup = SimpleNamespace(
            provider_disposition=lambda surface: "admitted",
        )
        with self.assertRaisesRegex(
            gateway_acceptance.AcceptanceFailure, "was not held"
        ):
            await gateway_acceptance.held_operational_provider_acceptance(
                admitted_backup,
                endpoint=ENDPOINT,
                fixture_stats_url="http://fixture.invalid/__fixture__/stats",
                settings=lifecycle_settings(ENDPOINT),
            )
        with self.assertRaisesRegex(
            gateway_acceptance.AcceptanceFailure, "no reviewed probe expectation"
        ):
            await gateway_acceptance.admitted_lifecycle_probe_acceptance(
                REGISTRY.by_version["9.1.2"],
                endpoint=ENDPOINT,
                settings=lifecycle_settings(ENDPOINT),
            )

    def test_addon_runtime_harness_expects_provider_held_on_its_held_path(self):
        source = (ROOT / "scripts" / "exact_addon_runtime_acceptance.py").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            source.count('validator_refusal_category\n        == "provider_held"'), 2
        )
        self.assertNotIn('== "upstream_version_mismatch"', source)


if __name__ == "__main__":
    unittest.main()
