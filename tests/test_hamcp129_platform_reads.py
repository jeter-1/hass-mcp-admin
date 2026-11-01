"""HAMCP-129: delegated platform health, installed-app and config-entry reads.

Synthetic transports only. Every group runs for the exact 8.4.3, 8.5.0 and
8.6.0 bindings; no live Home Assistant or deployed endpoint is contacted.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from mcp.server.fastmcp import FastMCP  # noqa: E402

from ha_mcp_engineering.capabilities import build_capability_catalog  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    DELEGATED_CORE_REQUIREMENTS,
    delegated_requirements,
)
from ha_mcp_engineering.ha_mcp_readmission import ha_mcp as readmission  # noqa: E402
from ha_mcp_engineering.ha_mcp_readmission.models import CapabilityKind  # noqa: E402
from ha_mcp_engineering.providers import upstream_platform_reads as platform  # noqa: E402
from ha_mcp_engineering.providers import upstream_read_gateway as gateway_module  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    UpstreamToolPolicyError,
    load_reviewed_upstream_release_registry,
    load_upstream_tool_policy,
    validate_reviewed_release_catalog,
    validate_reviewed_release_evidence,
)
from test_beta58_ha_mcp_8_4_3_continuity import EXACT_READS  # noqa: E402
from test_ha_mcp_production_readmission import _GatewayTransport, _settings  # noqa: E402
from test_upstream_8_5_compatibility import capture  # noqa: E402

HEALTH, APP, INTEGRATION = platform.HEALTH, platform.APP, platform.INTEGRATION
TOOLS = (HEALTH, APP, INTEGRATION)
VERSIONS = ("8.4.3", "8.5.0", "8.6.0")
CANARY = "CANARY"
REFUSED = {"argument_validation", "invalid_arguments"}
POLICY = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering"
SOURCES = {
    "8.4.3": ("v8.4.3", "eac7a3aa7063432e9af17e7d7726040e909c7b8f"),
    "8.5.0": ("v8.5.0", "311d6dc273fb4e9a5b8cde0de15f69472a64fe44"),
    "8.6.0": ("v8.6.0", "fc54437a804858732e4bc927add98e202d879a09"),
}
TOKEN_KIND = {HEALTH: "health", APP: "apps", INTEGRATION: "integrations"}

# Positive allowlists, restated here so a widened module allowlist fails.
PLATFORM_KEYS = {
    "homeassistant": {
        "version", "installation_type", "dev", "hassio", "docker", "virtualenv",
        "python_version", "os_name", "os_version", "arch",
    },
    "hassio": {
        "host_os", "update_channel", "supervisor_version", "agent_version",
        "docker_version", "disk_total", "disk_used", "disk_life_time", "healthy",
        "supported", "virtualization", "board", "ntp_synchronized",
        "host_connectivity", "supervisor_connectivity", "supervisor_api", "version_api",
    },
    "cloud": {
        "logged_in", "relayer_connected", "remote_enabled", "remote_connected",
        "alexa_enabled", "google_enabled", "certificate_status", "iot_state",
        "can_reach_cert_server", "can_reach_cloud_auth", "can_reach_cloud",
    },
}
REPAIR_KEYS = {
    "domain", "issue_domain", "issue_id", "severity", "is_fixable", "translation_key",
    "breaks_in_ha_version", "created", "ignored", "dismissed_version",
}
ZHA_KEYS = {"ieee", "name", "manufacturer", "model", "lqi", "rssi", "available"}
ZWAVE_CONTROLLER_KEYS = {
    "home_id", "type", "own_node_id", "is_primary", "is_suc", "sdk_version",
    "firmware_version", "status", "inclusion_state", "rf_region",
    "is_rebuilding_routes", "supports_long_range",
}
ZWAVE_NODE_KEYS = {"node_id", "status", "is_routing", "is_secure", "zwave_plus_version", "is_controller_node"}
ROUTER_KEYS = {"extended_address", "channel", "extended_pan_id", "border_agent_id"}
MATTER_KEYS = {"config_entry_id", "state", "title"}
DEAD_KEYS = {"entity_id", "platform", "config_entry_id", "disabled_by", "has_state"}
APP_KEYS = {"slug", "name", "version", "state", "installed", "update_available", "repository", "description"}
STATS_KEYS = {"cpu_percent", "memory_percent", "memory_usage", "memory_limit"}
ENTRY_KEYS = {
    "entry_id", "domain", "title", "state", "source", "disabled_by", "supports_options",
    "supports_unload", "supports_reconfigure", "log_level", "pref_disable_new_entities",
    "pref_disable_polling",
}


def _canaries(prefix: str) -> dict:
    """Unlisted keys whose values must never reach a result."""

    return {
        name: f"{CANARY}-{prefix}-{name}"
        for name in (
            "options", "schema", "network", "ip_address", "hostname", "nameservers",
            "instance_id", "remote_server", "certificate_fingerprint",
            "translation_placeholders", "learn_more_url", "active_dataset_tlvs", "url",
            "ws_server_url", "log_level_raw", "ha_mcp_update", "unknown_key",
        )
    }


def _strip(value: dict, *names: str) -> dict:
    return {k: v for k, v in value.items() if k not in names}


def platform_data(**extra_domains) -> dict:
    return {
        "homeassistant": {"info": {
            "version": "2026.10.1", "installation_type": "Home Assistant OS", "dev": False,
            "hassio": True, "docker": True, "virtualenv": False, "python_version": "3.13.7",
            "os_name": "Linux", "os_version": "6.12.0", "arch": "x86_64",
            "timezone": f"{CANARY}-tz", "config_dir": f"{CANARY}-config-dir", "user": f"{CANARY}-user",
            **_canaries("ha"),
        }},
        "hassio": {"info": {
            "host_os": "Home Assistant OS 16.2", "update_channel": "stable",
            "supervisor_version": "supervisor-2026.10.0", "agent_version": "1.7.2",
            "docker_version": "28.3.3", "disk_total": 116.2, "disk_used": 21.4,
            "disk_life_time": None, "healthy": True, "supported": True,
            "virtualization": "kvm", "board": "generic-x86-64", "ntp_synchronized": True,
            "host_connectivity": {"type": "pending"},
            "supervisor_connectivity": True,
            "supervisor_api": {"type": "failed", "error": f"{CANARY}-error", "more_info": f"{CANARY}-more"},
            "version_api": {"type": "ok"},
            "installed_addons": f"{CANARY}-addon-list",
            **_canaries("hassio"),
        }},
        "cloud": {"info": {
            "logged_in": True, "relayer_connected": True, "remote_enabled": True,
            "remote_connected": True, "alexa_enabled": False, "google_enabled": False,
            "certificate_status": "ready", "iot_state": "connected",
            "can_reach_cert_server": {"type": "ok"}, "can_reach_cloud_auth": {"type": "ok"},
            "can_reach_cloud": {"type": "failed", "error": f"{CANARY}-cloud-error"},
            "subscription_expiration": f"{CANARY}-expiry",
            **_canaries("cloud"),
        }},
        "recorder": {"info": {"database_url": f"{CANARY}-database-url"}},
        "zha": {"info": {"ieee": f"{CANARY}-coordinator"}},
        **extra_domains,
    }


SECTION_PAYLOADS = {
    "repairs": {
        "issues": [{
            "domain": "hue", "issue_domain": "hue", "issue_id": "deprecated_bridge",
            "severity": "warning", "is_fixable": False, "translation_key": "deprecated",
            "breaks_in_ha_version": "2026.12.0", "created": "2026-10-01T00:00:00+00:00",
            "ignored": False, "dismissed_version": None, **_canaries("repair"),
        }],
        "dismissed_count": 1,
        "description": f"{CANARY}-repairs-description",
    },
    "zha_network": {
        "devices": [{
            "ieee": "00:11:22:33:44:55:66:77", "name": "Hall sensor", "manufacturer": "Synthetic",
            "model": "S1", "lqi": 211, "rssi": -52, "available": True,
            "nwk": f"{CANARY}-nwk", **_canaries("zha"),
        }],
        "total_count": 1,
        "description": f"{CANARY}-zha-description",
    },
    "zwave_network": {
        "controller": {
            "home_id": 3735928559, "type": "primary", "own_node_id": 1, "is_primary": True,
            "is_suc": True, "sdk_version": "7.21.0", "firmware_version": "7.21.1",
            "status": "ready", "inclusion_state": 0, "rf_region": "Europe",
            "is_rebuilding_routes": False, "supports_long_range": False,
            "nodes": [{"node_id": 9, "secret": f"{CANARY}-controller-node"}],
            **_canaries("zwave-controller"),
        },
        "nodes": [{
            "node_id": 2, "status": "alive", "is_routing": True, "is_secure": True,
            "zwave_plus_version": 2, "is_controller_node": False, "name": f"{CANARY}-node-name",
            **_canaries("zwave-node"),
        }],
        "total_count": 1,
    },
    "thread_network": {
        "border_routers": [{
            "extended_address": "aabbccddeeff0011", "channel": 15,
            "extended_pan_id": "1122334455667788", "border_agent_id": "agent-1",
            **_canaries("thread"),
        }],
        "datasets": f"{CANARY}-datasets",
    },
    "matter_network": {
        "config_entry_id": "01MATTERENTRY", "state": "loaded", "title": "Matter",
        **_canaries("matter"),
    },
    "dead_entities": {
        "summary": {"candidate_total": 1, "registry_total": 40, "notes": f"{CANARY}-notes"},
        "config_entry_orphans": {"items": [{
            "entity_id": "sensor.old", "platform": "hue", "config_entry_id": "01OLD",
            "disabled_by": None, "has_state": False, "original_name": f"{CANARY}-orphan",
            **_canaries("orphan"),
        }], "total_count": 1},
        "stale_restored": {"items": [], "total_count": 0, "hint": f"{CANARY}-hint"},
    },
    "themes": {
        "themes": ["midnight", "ocean"], "default_theme": "default",
        "default_dark_theme": None, "theme_data": {"secret": f"{CANARY}-theme"},
    },
}


def health_payload(sections=(), *, baseline=True, data=None, warnings=None) -> dict:
    payload = {
        "success": True,
        "health_info": {"type": "initial", "data": platform_data() if data is None else data} if baseline else {},
    }
    if not baseline:
        payload["baseline_available"] = False
    for name in sections:
        payload[name] = deepcopy(SECTION_PAYLOADS[name])
    if warnings:
        payload["warnings"] = warnings
    return payload


def app(slug: str, *, state: str = "started", stats=False, **fields) -> dict:
    value = {
        "name": f"App {slug}", "slug": slug, "description": "Synthetic add-on.",
        "version": "1.2.3", "state": state, "installed": True, "update_available": False,
        "repository": "core", "icon": True, "ingress_url": f"{CANARY}-ingress",
        **_canaries(f"app-{slug}"), **fields,
    }
    if stats:
        value["stats"] = {
            "cpu_percent": 1.5, "memory_percent": 2.0, "memory_usage": 104857600,
            "memory_limit": 4294967296, "network_rx": f"{CANARY}-rx", "blk_read": f"{CANARY}-blk",
        }
    return value


def apps_payload(apps, *, warnings=None) -> dict:
    payload = {
        "success": True,
        "addons": apps,
        "summary": {"total_installed": f"{CANARY}-summary", "running": 999},
    }
    if warnings:
        payload["warnings"] = warnings
    return payload


def config_entry(index: int, *, domain="hue", state="loaded", title=None, **fields) -> dict:
    return {
        "entry_id": f"ENTRY{index:04d}", "domain": domain,
        "title": f"Bridge {index}" if title is None else title, "state": state,
        "source": "user", "disabled_by": None, "supports_options": True,
        "supports_unload": True, "supports_reconfigure": False, "log_level": "WARNING",
        "pref_disable_new_entities": False, "pref_disable_polling": False,
        "unique_id": f"{CANARY}-unique-id", "data": {"token": f"{CANARY}-token"},
        "reason": f"{CANARY}-reason", "num_subentries": 0,
        **_strip(_canaries(f"entry-{index}"), "options"), **fields,
    }


def entries_payload(entries, *, has_more=False, warnings=None) -> dict:
    payload = {
        "success": True, "entries": entries, "count": len(entries), "total": len(entries),
        "has_more": has_more, "query": f"{CANARY}-echo",
    }
    if warnings:
        payload["warnings"] = warnings
    return payload


def ok(payload) -> dict:
    return {"isError": False, "structuredContent": deepcopy(payload),
            "content": [{"type": "text", "text": json.dumps(payload)}]}


def default_response(tool, arguments):
    if tool == HEALTH:
        include = arguments.get("include", "")
        return ok(health_payload(include.split(",") if include else ()))
    if tool == APP:
        return ok(apps_payload([
            app("core_ssh", stats=arguments.get("include_stats")),
            app("core_mosquitto", state="stopped", stats=arguments.get("include_stats")),
        ]))
    return ok(entries_payload([config_entry(1), config_entry(2, domain="zha")]))


class Transport(_GatewayTransport):
    def __init__(self, tools, version):
        super().__init__(tools, version=version)
        self.sent = []
        self.respond = default_response

    async def execute_read(self, tool, arguments, **kwargs):
        exchange = await super().execute_read(tool, arguments, **kwargs)
        self.sent.append((tool, deepcopy(arguments)))
        return replace(exchange, call_result=deepcopy(self.respond(tool, arguments)))


def policy_entry(version: str, tool: str):
    return load_reviewed_upstream_release_registry().by_version[version].policy.by_name[tool]


async def make_gateway(version="8.5.0", tools=None, core_runtime=None):
    transport = Transport(capture(version)["tools"] if tools is None else tools, version)
    gateway = UpstreamReadGateway()
    kwargs = {"core_runtime": core_runtime} if core_runtime is not None else {}
    gateway.configure(
        replace(
            _settings("synthetic-no-key"), ha_mcp_release_registry_enabled=False,
            # The endpoint host and path are redaction secrets; keep them
            # distinct from the fixed reason codes under test.
            upstream_dashboard_mcp_url="http://synthetic-provider:9583/synthetic-path-credential/mcp",
        ),
        transport=transport, **kwargs,
    )
    await gateway.initialize(FastMCP(f"synthetic-hamcp129-{version}"))
    return gateway, transport


async def call(gateway, name, arguments):
    _, token = begin_request("synthetic-hamcp129")
    try:
        return json.loads(await gateway._registered_tool_registry.snapshot()[name].run(arguments))
    finally:
        end_request(token)


def refused(test, result, *, dispatched=False, category=None):
    test.assertFalse(result.get("success"), result)
    test.assertNotIn("data", result)
    test.assertEqual(result["metadata"]["upstream_dispatch_occurred"], dispatched, result)
    test.assertFalse(result["metadata"]["fallback_occurred"])
    if category is not None:
        test.assertEqual(result["details"]["failure_category"], category, result)


# Every rejected or never-sent public argument (brief + design §7.1 group 3).
COMMON_FORBIDDEN = (
    {"slug": "core_ssh"}, {"domain": "hue"}, {"entry_id": "ENTRY0001"},
    {"include_options": True}, {"include_options": False}, {"include_schema": True},
    {"diagnostics": True}, {"config_check": True}, {"zha_network_full": True},
    {"repository": "core"}, {"source": "available"}, {"source": "installed"},
    {"include": "repairs"}, {"exact_match": False}, {"query": "x"},
    {"subentry_type": "x"}, {"device_id": "x"}, {"diagnostics_entry_id": "x"},
    {"action": "update"}, {"confirm": True},
)
REFUSALS = {
    HEALTH: [
        *(a for a in COMMON_FORBIDDEN),
        {"sections": ["diagnostics"]}, {"sections": ["config_check"]},
        {"sections": ["zha_network_full"]}, {"sections": ["repairs,diagnostics"]},
        {"sections": ["repairs,themes"]}, {"sections": ["bogus"]},
        {"sections": ["repairs", "repairs"]}, {"sections": "repairs"},
        {"sections": [1]}, {"sections": None}, {"sections": list(platform.SECTIONS) + ["repairs"]},
        {"include_dismissed_repairs": True}, {"include_dismissed_repairs": "yes"},
        {"sections": ["themes"], "include_dismissed_repairs": True},
        {"include_dismissed_repairs": 1},
    ],
    APP: [
        *(a for a in COMMON_FORBIDDEN if set(a) != {"slug"}),
        {"slug": "../core_ssh"}, {"slug": "a/b"}, {"slug": ""}, {"slug": "x" * 65},
        {"slug": ".hidden"}, {"slug": 3}, {"slug": "core ssh"},
        {"include_stats": "true"}, {"include_stats": 1}, {"include_stats": None},
    ],
    INTEGRATION: [
        *(a for a in COMMON_FORBIDDEN if set(a) not in ({"domain"}, {"entry_id"}, {"query"})),
        {"exact_match": True}, {"limit": 0}, {"limit": 101}, {"limit": True},
        {"limit": 1.5}, {"limit": "5"}, {"offset": -1}, {"offset": 201}, {"offset": False},
        {"domain": "Hue"}, {"domain": "hue.x"}, {"domain": ""}, {"domain": "a" * 65},
        {"entry_id": "ab-c"}, {"entry_id": "a" * 65}, {"entry_id": ""},
        {"query": ""}, {"query": " hue"}, {"query": "hue "}, {"query": "x\n"},
        {"query": "x\u0000"}, {"query": "x" * 65}, {"query": 5},
        {"state": "bogus"}, {"state": 1},
        {"entry_id": "ENTRY0001", "domain": "hue"}, {"entry_id": "ENTRY0001", "limit": 10},
        {"entry_id": "ENTRY0001", "limit": 50}, {"entry_id": "ENTRY0001", "offset": 0},
        {"entry_id": "ENTRY0001", "query": "x"}, {"entry_id": "ENTRY0001", "state": "loaded"},
    ],
}
ARGUMENT_TABLE = {
    HEALTH: [
        ({}, {}),
        ({"sections": []}, {}),
        ({"sections": ["themes", "repairs"]}, {"include": "repairs,themes"}),
        ({"sections": ["repairs"], "include_dismissed_repairs": True},
         {"include": "repairs", "include_dismissed_repairs": True}),
        ({"sections": ["repairs"], "include_dismissed_repairs": False}, {"include": "repairs"}),
        ({"sections": list(reversed(platform.SECTIONS))},
         {"include": ",".join(sorted(platform.SECTIONS))}),
    ],
    APP: [
        ({}, {"source": "installed", "include_stats": False}),
        ({"slug": "core_ssh"}, {"source": "installed", "include_stats": False}),
        ({"slug": None, "include_stats": False}, {"source": "installed", "include_stats": False}),
    ],
    INTEGRATION: [
        ({}, {"limit": 200, "offset": 0, "exact_match": True, "include_options": False}),
        ({"domain": "hue"}, {"limit": 200, "offset": 0, "exact_match": True,
                             "include_options": False, "query": "hue"}),
        ({"query": "Bridge"}, {"limit": 200, "offset": 0, "exact_match": True,
                               "include_options": False, "query": "Bridge"}),
        ({"domain": "hue", "query": "Bridge"}, {"limit": 200, "offset": 0, "exact_match": True,
                                                "include_options": False, "query": "hue"}),
        ({"entry_id": "ENTRY0001"}, {"limit": 200, "offset": 0, "exact_match": True,
                                     "include_options": False}),
        ({"state": "loaded", "limit": 10, "offset": 5},
         {"limit": 200, "offset": 0, "exact_match": True, "include_options": False}),
    ],
}


class ModuleTests(unittest.TestCase):
    """Adapter module behaviour, independent of the published JSON schema."""

    def test_bindings_cover_exactly_three_tools_at_three_versions(self):
        self.assertEqual(len(platform.ADAPTER_BINDINGS), 9)
        for token, (tool, version, source, fingerprint) in platform.ADAPTER_BINDINGS.items():
            with self.subTest(token=token):
                self.assertEqual(token, f"ha-mcp-{version}-platform-{TOKEN_KIND[tool].replace('health', 'health')}-v1")
                self.assertEqual(source, SOURCES[version][1])
                entry = policy_entry(version, tool)
                self.assertEqual(entry.argument_restrictions, (token,))
                self.assertEqual(entry.input_schema_fingerprint, fingerprint)
                self.assertEqual(entry.classification, "mixed_or_requires_wrapper")
                self.assertTrue(platform.is_platform_adapter(entry))
                self.assertTrue(entry.is_read_route)

    def test_public_schemas_are_closed(self):
        for tool, schema in platform.PUBLIC_SCHEMAS.items():
            with self.subTest(tool=tool):
                self.assertIs(schema["additionalProperties"], False)
                self.assertEqual(schema["type"], "object")
        self.assertEqual(set(platform.PUBLIC_SCHEMAS[HEALTH]["properties"]),
                         {"sections", "include_dismissed_repairs"})
        self.assertEqual(set(platform.PUBLIC_SCHEMAS[APP]["properties"]), {"slug", "include_stats"})
        self.assertEqual(set(platform.PUBLIC_SCHEMAS[INTEGRATION]["properties"]),
                         {"domain", "entry_id", "query", "state", "limit", "offset"})
        self.assertNotIn("diagnostics", platform.SECTIONS)
        self.assertNotIn("config_check", platform.SECTIONS)
        self.assertNotIn("zha_network_full", platform.SECTIONS)

    def test_argument_construction_table(self):
        for version in VERSIONS:
            for tool, cases in ARGUMENT_TABLE.items():
                entry = policy_entry(version, tool)
                for public, upstream in cases:
                    with self.subTest(version=version, tool=tool, public=public):
                        built = platform.read_arguments(entry, public)
                        self.assertEqual(built, upstream)
                        self.assertFalse({"slug", "domain", "entry_id", "diagnostics",
                                          "config_check", "zha_network_full"} & set(built))
            entry = policy_entry(version, APP)
            self.assertEqual(platform.read_arguments(entry, {"include_stats": True}),
                             {"source": "installed", "include_stats": True})
            self.assertEqual(platform.read_arguments(entry, {"include_stats": True}, stats_precheck=True),
                             {"source": "installed", "include_stats": False})

    def test_module_refuses_every_forbidden_argument_without_the_schema(self):
        for version in VERSIONS:
            for tool, cases in REFUSALS.items():
                entry = policy_entry(version, tool)
                for arguments in cases:
                    with self.subTest(version=version, tool=tool, arguments=arguments):
                        with self.assertRaises(ValueError):
                            platform.read_arguments(entry, arguments)
        with self.assertRaises(ValueError):
            platform.read_arguments(policy_entry("8.5.0", HEALTH), None)

    def test_unbound_entries_never_build_arguments_or_project(self):
        entry = policy_entry("8.5.0", HEALTH)
        for change in (
            {"argument_restrictions": ("ha-mcp-8.6.0-platform-health-v1",)},
            {"argument_restrictions": ("ha-mcp-8.5.0-platform-apps-v1",)},
            {"argument_restrictions": ()},
            {"exposed_name": "ha_get_system_health_raw"},
            {"classification": "automatic_read"},
            {"input_schema_fingerprint": "0" * 64},
        ):
            with self.subTest(change=change):
                drifted = replace(entry, **change)
                self.assertFalse(platform.is_platform_adapter(drifted))
                with self.assertRaises(ValueError):
                    platform.read_arguments(drifted, {})
                with self.assertRaises(ValueError):
                    platform.validate_result(drifted, health_payload())
                with self.assertRaises(ValueError):
                    platform.project_result(drifted, health_payload(), {})
        annotations = replace(entry.reviewed_annotations, destructive=True)
        self.assertFalse(platform.is_platform_adapter(replace(entry, reviewed_annotations=annotations)))
        annotations = replace(entry.reviewed_annotations, read_only=False)
        self.assertFalse(platform.is_platform_adapter(replace(entry, reviewed_annotations=annotations)))

    def test_email_masking(self):
        p = platform._Projection()
        cases = {
            "Home of alice@example.com": "Home of [email]",
            "a@b and c.d+tag@mail.example.org": "[email] and [email]",
            "user@host": "[email]",
            "No address here": "No address here",
            "Hostname homeassistant.local:8123": "Hostname homeassistant.local:8123",
            "trailing @ sign": "trailing @ sign",
            "@leading": "@leading",
            "mqtt://broker.local": "mqtt://broker.local",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(p.text(raw, platform.TITLE_CAP, mask_email=True), expected)
        # Masking runs before the cap: a cut never leaves a partial address.
        raw = "x" * 115 + " alice@example.com"
        masked = platform._Projection().text(raw, platform.TITLE_CAP, mask_email=True)
        self.assertNotIn("alice", masked)
        self.assertNotIn("@", masked)
        self.assertLessEqual(len(masked), platform.TITLE_CAP)
        # "@" at the last kept position: cutting first would leave "synthuser@",
        # which no longer matches and would leak the local part.
        raw = "x" * 109 + " synthuser@example.com"
        self.assertEqual(raw.index("@"), platform.TITLE_CAP - 1)
        p = platform._Projection()
        self.assertEqual(p.text(raw, platform.TITLE_CAP, mask_email=True), "x" * 109 + " [email]")
        self.assertEqual(p.reasons, {"email_masked"})
        # Masking alone keeps a result complete; truncation does not.
        q = platform._Projection()
        q.text("alice@example.com", mask_email=True)
        self.assertEqual(q.completeness(), {"status": "complete", "reasons": []})
        q.text("x" * 201)
        self.assertEqual(q.completeness()["reasons"], ["string_truncated"])

    def test_completeness_messages(self):
        self.assertEqual(platform.completeness({"completeness": {"status": "complete", "reasons": []}}), (False, []))
        partial, warnings = platform.completeness({"completeness": {"status": "partial", "reasons": ["a", "b"]}})
        self.assertTrue(partial)
        self.assertEqual(warnings, ["The reviewed platform read is partial: a, b."])
        self.assertEqual(platform.completeness(None), (False, []))


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_publishes_closed_schemas_and_28_reads(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                g, t = await make_gateway(version)
                tools = g._registered_tool_registry.snapshot()
                self.assertEqual(set(tools), EXACT_READS, g.health_snapshot())
                self.assertEqual(len(tools), 28)
                upstream = {item["name"]: item for item in capture(version)["tools"]}
                for name in TOOLS:
                    self.assertEqual(tools[name].parameters, platform.PUBLIC_SCHEMAS[name])
                    self.assertNotEqual(tools[name].parameters, upstream[name]["inputSchema"])
                    self.assertIs(tools[name].annotations.readOnlyHint, True)
                    self.assertIs(tools[name].annotations.destructiveHint, False)
                    # Design §4.3: reviewed annotations equal upstream's (O2).
                    self.assertIs(tools[name].annotations.openWorldHint, name != INTEGRATION)
                    self.assertNotEqual(tools[name].description, upstream[name]["description"])
                self.assertIn("Supervisor", tools[APP].description)
                self.assertIn("mask", tools[INTEGRATION].description.lower())
                self.assertEqual(t.sent, [])

    async def test_gateway_dispatches_only_the_built_arguments(self):
        for version in VERSIONS:
            g, t = await make_gateway(version)
            for tool, cases in ARGUMENT_TABLE.items():
                for public, upstream in cases:
                    with self.subTest(version=version, tool=tool, public=public):
                        t.sent.clear()
                        result = await call(g, tool, public)
                        self.assertTrue(result["success"], result)
                        self.assertEqual(t.sent, [(tool, upstream)])

    async def test_forbidden_arguments_never_dispatch(self):
        for version in VERSIONS:
            g, t = await make_gateway(version)
            for tool, cases in REFUSALS.items():
                for arguments in cases:
                    with self.subTest(version=version, tool=tool, arguments=arguments):
                        result = await call(g, tool, arguments)
                        refused(self, result)
                        self.assertIn(result["details"]["failure_category"], REFUSED, result)
            self.assertEqual(t.sent, [])
            self.assertEqual(t.calls, 0)

    async def test_module_refusal_holds_even_if_the_published_schema_were_open(self):
        # Defence in depth: a widened schema still cannot forward a field.
        open_schema = {"type": "object", "properties": {}, "additionalProperties": True}
        with patch.dict(platform.PUBLIC_SCHEMAS, {tool: open_schema for tool in TOOLS}):
            g, t = await make_gateway("8.5.0")
            for tool in TOOLS:
                for arguments in ({"diagnostics": True}, {"include_options": True},
                                  {"source": "available"}, {"slug": "../x"}):
                    with self.subTest(tool=tool, arguments=arguments):
                        refused(self, await call(g, tool, arguments))
        self.assertEqual(t.sent, [])

    async def test_projection_canaries_and_positive_allowlists(self):
        for version in VERSIONS:
            g, t = await make_gateway(version)
            with self.subTest(version=version, tool=HEALTH):
                result = await call(g, HEALTH, {"sections": list(platform.SECTIONS)})
                self.assertTrue(result["success"], result)
                self.assertNotIn(CANARY, json.dumps(result))
                data = result["data"]
                self.assertEqual(set(data), {"success", "baseline_available", "platform", "sections",
                                             "warnings", "completeness"})
                for domain, keys in PLATFORM_KEYS.items():
                    self.assertEqual(set(data["platform"][domain]), keys, domain)
                self.assertEqual(data["platform"]["hassio"]["host_connectivity"], "pending")
                self.assertEqual(data["platform"]["hassio"]["supervisor_api"], "failed")
                self.assertEqual(data["platform"]["hassio"]["version_api"], "ok")
                self.assertEqual(data["platform"]["cloud"]["can_reach_cloud"], "failed")
                self.assertEqual(data["platform"]["other_domains"], ["recorder", "zha"])
                sections = data["sections"]
                self.assertEqual(set(sections), set(platform.SECTIONS))
                self.assertEqual(set(sections["repairs"]["issues"][0]), REPAIR_KEYS)
                self.assertEqual(sections["repairs"]["dismissed_count"], 1)
                self.assertEqual(set(sections["zha_network"]["devices"][0]), ZHA_KEYS)
                self.assertEqual(set(sections["zwave_network"]["controller"]), ZWAVE_CONTROLLER_KEYS)
                self.assertEqual(set(sections["zwave_network"]["nodes"][0]), ZWAVE_NODE_KEYS)
                self.assertEqual(set(sections["thread_network"]["border_routers"][0]), ROUTER_KEYS)
                self.assertEqual(set(sections["matter_network"]), MATTER_KEYS)
                self.assertEqual(set(sections["dead_entities"]["config_entry_orphans"]["items"][0]), DEAD_KEYS)
                self.assertEqual(set(sections["dead_entities"]["summary"]), {"candidate_total", "registry_total"})
                self.assertEqual(sections["themes"]["themes"], ["midnight", "ocean"])
                self.assertEqual(result["metadata"]["completeness"], "complete")
            with self.subTest(version=version, tool=APP):
                result = await call(g, APP, {"include_stats": True})
                self.assertTrue(result["success"], result)
                self.assertNotIn(CANARY, json.dumps(result))
                for item in result["data"]["apps"]:
                    self.assertEqual(set(item), APP_KEYS | {"stats"})
                    self.assertEqual(set(item["stats"]), STATS_KEYS)
                self.assertEqual(result["data"]["summary"], {
                    "total_installed": 2, "running": 1, "stopped": 1, "updates_available": 0})
            with self.subTest(version=version, tool=INTEGRATION):
                result = await call(g, INTEGRATION, {})
                self.assertTrue(result["success"], result)
                self.assertNotIn(CANARY, json.dumps(result))
                for item in result["data"]["entries"]:
                    self.assertEqual(set(item), ENTRY_KEYS)
                self.assertEqual(result["data"]["state_summary"], {"loaded": 2})

    async def test_email_like_titles_are_masked_before_output_and_filtering(self):
        g, t = await make_gateway("8.6.0")
        t.respond = lambda tool, args: ok(entries_payload([
            config_entry(1, title="Calendar alice@example.com"),
            config_entry(2, domain="mqtt", title="Broker mqtt.local"),
        ]))
        result = await call(g, INTEGRATION, {})
        self.assertTrue(result["success"], result)
        titles = [item["title"] for item in result["data"]["entries"]]
        self.assertEqual(titles, ["Calendar [email]", "Broker mqtt.local"])
        self.assertNotIn("alice", json.dumps(result))
        # The raw address is not a filter oracle.
        result = await call(g, INTEGRATION, {"query": "alice"})
        self.assertEqual(result["data"]["entries"], [])
        result = await call(g, INTEGRATION, {"query": "example.com"})
        self.assertEqual(result["data"]["entries"], [])
        t.respond = lambda tool, args: ok(health_payload(
            ["matter_network"],
        ) | {"matter_network": {"config_entry_id": "01M", "state": "loaded", "title": "Matter bob@example.org"}})
        result = await call(g, HEALTH, {"sections": ["matter_network"]})
        self.assertEqual(result["data"]["sections"]["matter_network"]["title"], "Matter [email]")

    async def test_shape_refusal_is_invalid_response_without_partial_data(self):
        bad = {
            HEALTH: [
                {"success": False, "health_info": {}},
                {"success": True},
                {"success": True, "health_info": []},
                health_payload() | {"diagnostics": {}},
                health_payload() | {"config_check": {}},
                health_payload() | {"zha_network_full": {}},
                health_payload() | {"repairs": {"issues": []}},
                health_payload(data=platform_data() | {"homeassistant": {"info": {"version": ["x"]}}}),
                health_payload(data=platform_data() | {"homeassistant": {"info": {"dev": float("nan")}}}),
                health_payload(data=platform_data() | {"hassio": {"info": {"healthy": {"no_type": 1}}}}),
                health_payload(data=platform_data() | {"cloud": "x"}),
                health_payload() | {"baseline_available": "yes"},
                {"success": True, "baseline_available": False, "health_info": {"type": "initial"}},
            ],
            APP: [
                {"success": False, "addons": []},
                {"success": True, "addons": {}},
                apps_payload([app("a", installed="yes")]),
                apps_payload([app("a", update_available=1)]),
                apps_payload([app("../a")]),
                apps_payload([_strip(app("a"), "slug")]),
                apps_payload(["core_ssh"]),
            ],
            INTEGRATION: [
                {"success": False, "entries": []},
                {"success": True, "entries": [], "has_more": "no"},
                entries_payload([config_entry(1, title=5)]),
                entries_payload([config_entry(1, supports_options="yes")]),
                entries_payload([_strip(config_entry(1), "domain")]),
                entries_payload([config_entry(1, options={"password": CANARY})]),
                entries_payload([]) | {"options": {"x": CANARY}},
                entries_payload([config_entry(i) for i in range(201)]),
            ],
        }
        public = {HEALTH: {}, APP: {}, INTEGRATION: {}}
        for version in VERSIONS:
            g, t = await make_gateway(version)
            for tool, payloads in bad.items():
                for payload in payloads:
                    with self.subTest(version=version, tool=tool, payload=str(payload)[:80]):
                        t.sent.clear()
                        t.respond = lambda name, args, payload=payload: ok(payload)
                        result = await call(g, tool, public[tool])
                        refused(self, result, dispatched=True, category="invalid_response")
                        self.assertNotIn(CANARY, json.dumps(result))
                        self.assertEqual(len(t.sent), 1)

    async def test_requested_sections_must_match_returned_sections(self):
        g, t = await make_gateway("8.5.0")
        t.respond = lambda tool, args: ok(health_payload(["repairs"]))
        refused(self, await call(g, HEALTH, {"sections": ["repairs", "themes"]}),
                dispatched=True, category="invalid_response")
        refused(self, await call(g, HEALTH, {}), dispatched=True, category="invalid_response")

    async def test_upstream_error_text_never_passes_through(self):
        g, t = await make_gateway("8.5.0")
        t.respond = lambda tool, args: ok(health_payload(["zha_network"]) | {
            "zha_network": {"error": f"{CANARY} stack trace"}, "warnings": [f"{CANARY} warning"]})
        result = await call(g, HEALTH, {"sections": ["zha_network"]})
        self.assertTrue(result["success"], result)
        self.assertNotIn(CANARY, json.dumps(result))
        self.assertEqual(result["data"]["sections"]["zha_network"], {"error": "section_unavailable"})
        self.assertEqual(result["data"]["warnings"], ["upstream_warnings_withheld"])
        self.assertEqual(result["data"]["completeness"]["reasons"], ["section_unavailable:zha_network"])
        self.assertEqual(result["metadata"]["completeness"], "partial")
        t.respond = lambda tool, args: ok(health_payload(baseline=False))
        result = await call(g, HEALTH, {})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["platform"], {})
        self.assertEqual(result["data"]["warnings"], ["baseline_unavailable"])
        self.assertEqual(result["metadata"]["completeness"], "partial")
        t.respond = lambda tool, args: ok(entries_payload([config_entry(1)]) | {
            "warnings": [f"{CANARY} integration warning"]})
        result = await call(g, INTEGRATION, {})
        self.assertTrue(result["success"], result)
        self.assertNotIn(CANARY, json.dumps(result))
        self.assertEqual(result["data"]["warnings"], ["upstream_warnings_withheld"])
        t.respond = lambda tool, args: ok(apps_payload([app("core_synthetic")]) | {
            "warnings": [f"{CANARY} app warning"]})
        result = await call(g, APP, {})
        self.assertTrue(result["success"], result)
        self.assertNotIn(CANARY, json.dumps(result))
        self.assertEqual(result["data"]["warnings"], ["upstream_warnings_withheld"])
        t.respond = lambda tool, args: {"isError": True, "content": [{"type": "text", "text": json.dumps(
            {"success": False, "error": {"code": "INTERNAL", "message": f"{CANARY} upstream"}})}]}
        for tool in TOOLS:
            result = await call(g, tool, {})
            refused(self, result, dispatched=True)
            self.assertNotIn(CANARY, json.dumps(result))

    async def test_worst_case_caps_stay_bounded_and_partial(self):
        def worst(tool, args):
            if tool == HEALTH:
                payload = health_payload(platform.SECTIONS, data=platform_data(**{
                    f"domain_{i:03d}": {"info": {"x": 1}} for i in range(80)}))
                payload["repairs"]["issues"] = [
                    SECTION_PAYLOADS["repairs"]["issues"][0] | {"issue_id": f"issue_{i:04d}"} for i in range(80)]
                payload["zha_network"] = {"devices": [
                    SECTION_PAYLOADS["zha_network"]["devices"][0] | {"name": f"Device {i}"} for i in range(80)],
                    "total_count": 120}
                payload["zwave_network"]["nodes"] = [
                    SECTION_PAYLOADS["zwave_network"]["nodes"][0] | {"node_id": i} for i in range(80)]
                payload["zwave_network"]["total_count"] = 80
                payload["thread_network"]["border_routers"] = [
                    SECTION_PAYLOADS["thread_network"]["border_routers"][0] | {"extended_address": f"{i:016x}"}
                    for i in range(20)]
                for bucket in ("config_entry_orphans", "stale_restored"):
                    payload["dead_entities"][bucket] = {"items": [
                        SECTION_PAYLOADS["dead_entities"]["config_entry_orphans"]["items"][0]
                        | {"entity_id": f"sensor.dead_{i:04d}"} for i in range(80)], "total_count": 300}
                payload["themes"]["themes"] = [f"theme_{i:03d}" for i in range(80)]
                return ok(payload)
            if tool == APP:
                return ok(apps_payload([
                    app(f"addon_{i:03d}", state="stopped" if i >= 32 else "started",
                        stats=args.get("include_stats"), description="d" * 120)
                    for i in range(140)]))
            return ok(entries_payload([config_entry(i, title="t" * 300) for i in range(200)], has_more=True))

        for version in VERSIONS:
            g, t = await make_gateway(version)
            t.respond = worst
            cases = {
                HEALTH: ({"sections": list(platform.SECTIONS)}, {
                    "other_domains_truncated", "repairs_truncated", "zha_network_truncated",
                    "zwave_network_truncated", "thread_network_truncated", "themes_truncated",
                    "dead_entities_truncated:config_entry_orphans", "dead_entities_truncated:stale_restored"}),
                APP: ({"include_stats": True}, {"apps_truncated"}),
                INTEGRATION: ({"limit": 100}, {"upstream_window_exceeded", "string_truncated"}),
            }
            for tool, (args, reasons) in cases.items():
                with self.subTest(version=version, tool=tool):
                    result = await call(g, tool, args)
                    self.assertTrue(result["success"], result)
                    self.assertEqual(set(result["data"]["completeness"]["reasons"]), reasons)
                    self.assertEqual(result["metadata"]["completeness"], "partial")
                    self.assertLess(len(json.dumps(result).encode()), 60_000)
            result = await call(g, HEALTH, {"sections": list(platform.SECTIONS)})
            sections = result["data"]["sections"]
            self.assertEqual((sections["repairs"]["count"], sections["repairs"]["total_count"]), (50, 80))
            self.assertEqual(sections["zha_network"]["total_count"], 120)
            self.assertEqual(sections["thread_network"]["count"], 16)
            self.assertEqual(sections["themes"]["count"], 64)
            self.assertEqual(sections["dead_entities"]["stale_restored"]["total_count"], 300)
            self.assertEqual(len(result["data"]["platform"]["other_domains"]), 64)
            self.assertEqual(result["data"]["platform"]["other_domains_total_count"], 82)
            result = await call(g, APP, {"include_stats": True})
            self.assertEqual((result["data"]["count"], result["data"]["total_count"]), (100, 140))
            result = await call(g, INTEGRATION, {"limit": 100})
            self.assertTrue(all(len(item["title"]) == 120 for item in result["data"]["entries"]))

    async def test_oversized_strings_fail_closed_rather_than_truncate_silently(self):
        g, t = await make_gateway("8.5.0")
        t.respond = lambda tool, args: ok(apps_payload([
            app(f"addon_{i:03d}", state="stopped", **{k: "x" * 500 for k in ("name", "description", "version", "repository")})
            for i in range(100)]))
        result = await call(g, APP, {})
        refused(self, result, dispatched=True, category="response_too_large")

    async def test_integration_filters_pagination_and_entry_lookup(self):
        rows = [
            config_entry(1, domain="zha", state="loaded", title="Coordinator"),
            config_entry(2, domain="zha_toolkit", state="loaded", title="Toolkit"),
            config_entry(3, domain="hue", state="setup_retry", title="Living room bridge"),
            config_entry(4, domain="hue", state="loaded", title="Office Bridge"),
            config_entry(5, domain="mqtt", state="not_loaded", title="Broker"),
        ]
        for version in VERSIONS:
            g, t = await make_gateway(version)
            t.respond = lambda tool, args: ok(entries_payload(rows))
            ids = lambda r: [item["entry_id"] for item in r["data"]["entries"]]  # noqa: E731
            with self.subTest(version=version):
                self.assertEqual(ids(await call(g, INTEGRATION, {"domain": "zha"})), ["ENTRY0001"])
                self.assertEqual(ids(await call(g, INTEGRATION, {"query": "BRIDGE"})), ["ENTRY0003", "ENTRY0004"])
                self.assertEqual(ids(await call(g, INTEGRATION, {"query": "zha"})), ["ENTRY0001", "ENTRY0002"])
                self.assertEqual(ids(await call(g, INTEGRATION, {"state": "loaded"})),
                                 ["ENTRY0001", "ENTRY0002", "ENTRY0004"])
                self.assertEqual(ids(await call(g, INTEGRATION, {"domain": "hue", "state": "loaded"})), ["ENTRY0004"])
                page = await call(g, INTEGRATION, {"limit": 2, "offset": 2})
                self.assertEqual(ids(page), ["ENTRY0003", "ENTRY0004"])
                self.assertEqual((page["data"]["total_count"], page["data"]["has_more"],
                                  page["data"]["next_offset"]), (5, True, 4))
                self.assertEqual(page["data"]["state_summary"],
                                 {"loaded": 3, "not_loaded": 1, "setup_retry": 1})
                last = await call(g, INTEGRATION, {"limit": 2, "offset": 4})
                self.assertEqual((last["data"]["has_more"], last["data"]["next_offset"]), (False, None))
                self.assertEqual((await call(g, INTEGRATION, {"offset": 200}))["data"]["entries"], [])
                single = await call(g, INTEGRATION, {"entry_id": "ENTRY0003"})
                self.assertEqual(ids(single), ["ENTRY0003"])
                self.assertEqual(single["data"]["filters"], {"entry_id": "ENTRY0003"})
                self.assertEqual(single["metadata"]["completeness"], "complete")
                missing = await call(g, INTEGRATION, {"entry_id": "ENTRY9999"})
                refused(self, missing, dispatched=True, category="resource_not_found")
                self.assertEqual(missing["details"]["completeness"], "complete")
            t.respond = lambda tool, args: ok(entries_payload(rows, has_more=True))
            missing = await call(g, INTEGRATION, {"entry_id": "ENTRY9999"})
            refused(self, missing, dispatched=True, category="resource_not_found")
            self.assertEqual(missing["details"]["completeness"], "partial")
            self.assertEqual(missing["details"]["completeness_reasons"], ["upstream_window_exceeded"])
            listed = await call(g, INTEGRATION, {})
            self.assertEqual(listed["metadata"]["completeness"], "partial")
            self.assertEqual(listed["data"]["completeness"]["reasons"], ["upstream_window_exceeded"])

    async def test_app_slug_filter_and_not_found(self):
        for version in VERSIONS:
            g, t = await make_gateway(version)
            with self.subTest(version=version):
                result = await call(g, APP, {"slug": "core_mosquitto"})
                self.assertEqual([item["slug"] for item in result["data"]["apps"]], ["core_mosquitto"])
                self.assertEqual(result["data"]["summary"]["total_installed"], 2)
                missing = await call(g, APP, {"slug": "core_absent"})
                refused(self, missing, dispatched=True, category="resource_not_found")
                self.assertEqual(missing["details"]["completeness"], "complete")
                self.assertEqual(t.sent[-1], (APP, {"source": "installed", "include_stats": False}))

    async def test_stats_precheck_bounds_fan_out(self):
        def listing(started, *, main_started=None):
            def respond(tool, args):
                count = started if not args["include_stats"] or main_started is None else main_started
                return ok(apps_payload([
                    app(f"addon_{i:03d}", state="started" if i < count else "stopped",
                        stats=args["include_stats"]) for i in range(40)]))
            return respond

        precheck = (APP, {"source": "installed", "include_stats": False})
        stats = (APP, {"source": "installed", "include_stats": True})
        for version in VERSIONS:
            with self.subTest(version=version):
                g, t = await make_gateway(version)
                t.respond = listing(33)
                refused(self, await call(g, APP, {"include_stats": True}),
                        dispatched=True, category="response_too_large")
                self.assertEqual(t.sent, [precheck])
                t.sent.clear()
                t.respond = listing(32)
                result = await call(g, APP, {"include_stats": True})
                self.assertTrue(result["success"], result)
                self.assertEqual(t.sent, [precheck, stats])
                t.sent.clear()
                # The listing grew between the pre-check and the stats call.
                t.respond = listing(32, main_started=33)
                refused(self, await call(g, APP, {"include_stats": True}),
                        dispatched=True, category="response_too_large")
                self.assertEqual(t.sent, [precheck, stats])
                t.sent.clear()
                t.respond = listing(40)
                result = await call(g, APP, {})
                self.assertTrue(result["success"], result)
                self.assertEqual(t.sent, [precheck])
                t.sent.clear()
                t.respond = lambda tool, args: ok({"success": True, "addons": "bad"})
                refused(self, await call(g, APP, {"include_stats": True}),
                        dispatched=True, category="invalid_response")
                self.assertEqual(t.sent, [precheck])

    async def test_stats_cap_snapshot_and_documented_race(self):
        """Count the upstream per-app stats requests, not only the refusal.

        The fake models upstream ``list_addons(include_stats=True)``: it
        re-lists and starts one Supervisor stats request per started app.
        Engineering checks its own stats-free snapshot before it dispatches
        the stats call, so it never dispatches that call over the cap. It
        cannot bound upstream's own re-list: an app started between the two
        calls is still fanned out upstream before Engineering refuses. That
        race is the disclosed limitation this test pins.
        """

        precheck = (APP, {"source": "installed", "include_stats": False})
        stats = (APP, {"source": "installed", "include_stats": True})

        class Upstream:
            def __init__(self, started, after_precheck=0):
                self.started = started
                self.after_precheck = after_precheck
                self.fan_out = []

            def __call__(self, tool, args):
                rows = [app(f"addon_{i:03d}", state="started" if i < self.started else "stopped",
                            stats=args["include_stats"]) for i in range(48)]
                if args["include_stats"]:
                    self.fan_out.append(self.started)
                else:
                    self.started += self.after_precheck
                return ok(apps_payload(rows))

        for version in VERSIONS:
            g, t = await make_gateway(version)
            with self.subTest(version=version, case="exactly_32"):
                t.sent.clear()
                t.respond = upstream = Upstream(32)
                result = await call(g, APP, {"include_stats": True})
                self.assertTrue(result["success"], result)
                self.assertEqual(t.sent, [precheck, stats])
                self.assertEqual(upstream.fan_out, [32])
            with self.subTest(version=version, case="33"):
                t.sent.clear()
                t.respond = upstream = Upstream(33)
                refused(self, await call(g, APP, {"include_stats": True}),
                        dispatched=True, category="response_too_large")
                self.assertEqual(t.sent, [precheck])
                self.assertEqual(upstream.fan_out, [])
            with self.subTest(version=version, case="change_between_snapshot_and_dispatch"):
                t.sent.clear()
                t.respond = upstream = Upstream(32, after_precheck=1)
                refused(self, await call(g, APP, {"include_stats": True}),
                        dispatched=True, category="response_too_large")
                self.assertEqual(t.sent, [precheck, stats])
                # Documented limitation: upstream started 33 stats requests.
                self.assertEqual(upstream.fan_out, [33])
            with self.subTest(version=version, case="concurrent_count_changes_mid_collection"):
                t.sent.clear()
                t.respond = upstream = Upstream(30, after_precheck=1)
                results = await asyncio.gather(*(call(g, APP, {"include_stats": True}) for _ in range(8)))
                prechecks = [item for item in t.sent if item == precheck]
                dispatched = [item for item in t.sent if item == stats]
                self.assertEqual(len(prechecks), 8)
                # Only the snapshots that saw at most 32 started apps (30, 31,
                # 32) may dispatch; no call dispatches more than once.
                self.assertEqual(len(dispatched), 3)
                self.assertEqual(len(upstream.fan_out), 3)
                self.assertTrue(all(count <= 30 + 8 for count in upstream.fan_out))
                succeeded = [item for item in results if item.get("success")]
                for item in results:
                    if not item.get("success"):
                        refused(self, item, dispatched=True, category="response_too_large")
                self.assertEqual(len(succeeded), sum(1 for count in upstream.fan_out if count <= 32))
                for item in succeeded:
                    self.assertLessEqual(item["data"]["summary"]["running"], 32)

    async def test_requested_stats_completeness(self):
        def payload(states_stats):
            apps = []
            for slug, state, stats in states_stats:
                item = app(slug, state=state, stats=stats is True)
                if stats is None:
                    item["stats"] = None
                apps.append(item)
            return ok(apps_payload(apps))

        def expect(result, status, unavailable):
            self.assertTrue(result["success"], result)
            data = result["data"]
            reasons = ["stats_unavailable"] if unavailable else []
            self.assertEqual(data["completeness"], {"status": status, "reasons": reasons})
            self.assertEqual(result["metadata"]["completeness"], status)
            self.assertEqual(data["warnings"], [f"stats_unavailable:{slug}" for slug in unavailable])
            for item in data["apps"]:
                if item["slug"] in unavailable:
                    self.assertIsNone(item["stats"])

        # (slug, state, stats): True = available, None = upstream null,
        # False = key absent. Stopped apps never carry stats upstream.
        cases = {
            "all_available": ([("a", "started", True), ("b", "started", True), ("c", "stopped", None)],
                              "complete", []),
            "one_null": ([("a", "started", True), ("b", "started", None), ("c", "stopped", None)],
                         "partial", ["b"]),
            "one_absent": ([("a", "started", True), ("b", "started", False), ("c", "stopped", False)],
                           "partial", ["b"]),
            "all_unavailable": ([("a", "started", None), ("b", "started", False)],
                                "partial", ["a", "b"]),
        }
        for version in VERSIONS:
            g, t = await make_gateway(version)
            for name, (rows, status, unavailable) in cases.items():
                with self.subTest(version=version, case=name):
                    t.respond = lambda tool, args, rows=rows: payload(rows)
                    expect(await call(g, APP, {"include_stats": True}), status, unavailable)
            with self.subTest(version=version, case="slug_scoped"):
                t.respond = lambda tool, args: payload([("a", "started", True), ("b", "started", None)])
                expect(await call(g, APP, {"include_stats": True, "slug": "a"}), "complete", [])
                expect(await call(g, APP, {"include_stats": True, "slug": "b"}), "partial", ["b"])
            with self.subTest(version=version, case="not_requested"):
                t.respond = lambda tool, args: payload([("a", "started", False), ("b", "stopped", False)])
                t.sent.clear()
                result = await call(g, APP, {})
                expect(result, "complete", [])
                self.assertTrue(all("stats" not in item for item in result["data"]["apps"]))
                self.assertEqual(t.sent, [(APP, {"source": "installed", "include_stats": False})])
            with self.subTest(version=version, case="invalid_stats_shape"):
                t.respond = lambda tool, args: ok(apps_payload([app("a") | {"stats": "bad"}]))
                refused(self, await call(g, APP, {"include_stats": True}),
                        dispatched=True, category="invalid_response")



# Exact upstream outputs generated offline by tests/fixtures/hamcp129/generate.py
# (see provenance.json): the pinned ha-mcp release ran against synthetic Core
# and Supervisor fakes, so these are the real upstream shapes, not ours.
FIXTURES = ROOT / "tests/fixtures/hamcp129"
FIXTURE_PUBLIC = {
    "apps_installed": (APP, {}),
    "apps_installed_stats": (APP, {"include_stats": True}),
    "health_baseline": (HEALTH, {}),
    "health_all_sections": (HEALTH, {"sections": list(platform.SECTIONS), "include_dismissed_repairs": True}),
    "health_sections_unavailable": (
        HEALTH, {"sections": list(platform.SECTIONS), "include_dismissed_repairs": True}),
    "health_baseline_unavailable": (HEALTH, {"sections": ["dead_entities"]}),
    "integrations_list": (INTEGRATION, {}),
    "integrations_query": (INTEGRATION, {"query": "zha"}),
}
FIXTURE_COMPLETENESS = {
    "apps_installed": ("partial", ["string_truncated"]),
    # The captured Supervisor fake fails synthetic_beta's stats request.
    "apps_installed_stats": ("partial", ["stats_unavailable", "string_truncated"]),
    "health_baseline": ("complete", []),
    "health_all_sections": ("complete", []),
    "health_sections_unavailable": ("partial", [f"section_unavailable:{name}" for name in sorted(platform.SECTIONS)]),
    "health_baseline_unavailable": ("partial", ["baseline_unavailable"]),
    "integrations_list": ("partial", ["string_truncated"]),
    "integrations_query": ("complete", []),
}
FIXTURE_CANARIES = (
    "synthetic-canary", "synthetic.user@example.invalid", "synthetic-host.example.invalid",
    "synthetic-docs.example.invalid", "192.0.2.", "198.51.100.", "Synthetic/Canary_Zone",
    "synthetic-user", "/synthetic/config-dir", "synthetic-remote.example.invalid",
    "53:59:4E", "synthetic-instance-id", "synthetic-supervisor-token", "synthetic-core-token",
    "synthetic_controller_extra", "synthetic_node_extra", "synthetic_unknown_key",
    "translation_placeholders", "learn_more_url", "log_level_raw", "ha_mcp_update",
)


def _keys_within(test, items, allowed, where):
    for item in items:
        test.assertLessEqual(set(item), allowed, where)


class FixtureReplayTests(unittest.IsolatedAsyncioTestCase):
    async def _replay(self, version, case, cases):
        tool, public = FIXTURE_PUBLIC[case.split("@")[0]]
        mode = case[len(case.split("@")[0]):]
        g, t = await make_gateway(version)
        fixture = cases[case]
        # The stats precheck is the same mode's stats-free listing.
        by_args = {json.dumps(fixture["arguments"], sort_keys=True): fixture["result"]}
        if fixture["arguments"].get("include_stats"):
            plain = cases["apps_installed" + mode]
            by_args[json.dumps(plain["arguments"], sort_keys=True)] = plain["result"]
        t.respond = lambda name, args: ok(by_args[json.dumps(args, sort_keys=True)])
        result = await call(g, tool, public)
        return fixture, t.sent, result

    async def test_exact_upstream_outputs_project_through_the_allowlists(self):
        for version in VERSIONS:
            cases = json.loads((FIXTURES / f"results-{version}.json").read_text(encoding="utf-8"))
            self.assertEqual(set(cases), {name + mode for name in FIXTURE_PUBLIC
                                          for mode in ("", "@core_ws_proxy")
                                          if name + mode in cases})
            for case in sorted(cases):
                with self.subTest(version=version, case=case):
                    fixture, sent, result = await self._replay(version, case, cases)
                    self.assertTrue(result["success"], result)
                    self.assertEqual(sent[-1], (fixture["tool"], fixture["arguments"]))
                    text = json.dumps(result)
                    for canary in FIXTURE_CANARIES:
                        self.assertNotIn(canary, text)
                    status, reasons = FIXTURE_COMPLETENESS[case.split("@")[0]]
                    self.assertEqual(result["data"]["completeness"], {"status": status, "reasons": reasons})
                    data = result["data"]
                    if case.split("@")[0] == "apps_installed_stats":
                        self.assertEqual(data["warnings"], ["stats_unavailable:synthetic_beta"])
                    if fixture["tool"] == HEALTH:
                        for domain, keys in PLATFORM_KEYS.items():
                            self.assertLessEqual(set(data["platform"].get(domain, {})), keys)
                        sections = data["sections"]
                        if "repairs" in sections and "issues" in sections["repairs"]:
                            _keys_within(self, sections["repairs"]["issues"], REPAIR_KEYS, "repairs")
                            _keys_within(self, sections["zha_network"]["devices"], ZHA_KEYS, "zha")
                            _keys_within(self, [sections["zwave_network"]["controller"]],
                                         ZWAVE_CONTROLLER_KEYS, "zwave controller")
                            _keys_within(self, sections["zwave_network"]["nodes"], ZWAVE_NODE_KEYS, "zwave")
                            _keys_within(self, sections["thread_network"]["border_routers"],
                                         ROUTER_KEYS, "thread")
                            _keys_within(self, [sections["matter_network"]], MATTER_KEYS, "matter")
                            for bucket in ("config_entry_orphans", "stale_restored"):
                                _keys_within(self, sections["dead_entities"][bucket]["items"], DEAD_KEYS, bucket)
                            self.assertTrue(sections["repairs"]["issues"])
                            self.assertTrue(sections["zwave_network"]["nodes"])
                    elif fixture["tool"] == APP:
                        self.assertTrue(data["apps"])
                        for item in data["apps"]:
                            self.assertLessEqual(set(item), APP_KEYS | {"stats"})
                            if item.get("stats"):
                                self.assertLessEqual(set(item["stats"]), STATS_KEYS)
                    else:
                        self.assertTrue(data["entries"])
                        _keys_within(self, data["entries"], ENTRY_KEYS, "entries")

    async def test_exact_upstream_email_titles_are_masked(self):
        cases = json.loads((FIXTURES / "results-8.6.0.json").read_text(encoding="utf-8"))
        _, _, result = await self._replay("8.6.0", "integrations_list", cases)
        titles = [entry["title"] for entry in result["data"]["entries"]]
        self.assertTrue(any(platform.EMAIL_MASK in title for title in titles), titles)
        _, _, result = await self._replay("8.6.0", "health_all_sections", cases)
        self.assertIn(platform.EMAIL_MASK, result["data"]["sections"]["matter_network"]["title"])


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_drift_quarantines_only_the_affected_tool(self):
        drifts = {
            "description": lambda item: item.update(description=item["description"] + " drift"),
            "annotations": lambda item: item.update(annotations={**(item.get("annotations") or {}),
                                                                 "destructiveHint": True}),
            "output_schema": lambda item: item.update(outputSchema={"type": "object", "properties": {"x": {"type": "string"}}}),
            "input_schema": lambda item: item["inputSchema"]["properties"].update(extra={"type": "string"}),
        }
        for version in VERSIONS:
            for tool in TOOLS:
                for kind, mutate in drifts.items():
                    with self.subTest(version=version, tool=tool, drift=kind):
                        tools = deepcopy(capture(version)["tools"])
                        mutate(next(item for item in tools if item["name"] == tool))
                        g, t = await make_gateway(version, tools)
                        exposed = set(g._registered_tool_registry.snapshot())
                        self.assertEqual(exposed, EXACT_READS - {tool}, g.health_snapshot())
                        self.assertIn(tool, {item["upstream_name"] for item in g.health_snapshot()["quarantined_tools"]})
                        self.assertEqual(t.sent, [])

    async def test_binding_mismatch_refuses_policy_and_never_exposes_upstream_schema(self):
        for version in VERSIONS:
            token = f"ha-mcp-{version}-platform-apps-v1"
            tool, bound_version, source, _ = platform.ADAPTER_BINDINGS[token]
            with self.subTest(version=version):
                with patch.dict(platform.ADAPTER_BINDINGS, {token: (tool, bound_version, source, "0" * 64)}):
                    tag, commit = SOURCES[version]
                    with self.assertRaises(UpstreamToolPolicyError):
                        load_upstream_tool_policy(
                            POLICY / f"upstream_tool_policy_{version.replace('.', '_')}.json",
                            expected_version=version, expected_source_tag=tag,
                            expected_source_commit=commit)

    def test_policy_data_cannot_move_or_enable_an_adapter(self):
        for version in VERSIONS:
            path = POLICY / f"upstream_tool_policy_{version.replace('.', '_')}.json"
            value = json.loads(path.read_text())
            tag, commit = SOURCES[version]
            binding = {"expected_version": version, "expected_source_tag": tag, "expected_source_commit": commit}
            policy = load_upstream_tool_policy(path, **binding)
            for tool in TOOLS:
                self.assertTrue(policy.by_name[tool].is_read_route)
            other = next(v for v in VERSIONS if v != version)
            uncompiled = "policy_uncompiled_platform_adapter"
            changes = (
                (HEALTH, {"argument_restrictions": [f"ha-mcp-{other}-platform-health-v1"]}, uncompiled),
                (HEALTH, {"argument_restrictions": [f"ha-mcp-{version}-platform-apps-v1"]}, uncompiled),
                (APP, {"argument_restrictions": [f"ha-mcp-{version}-platform-apps-v1", "extra"]}, uncompiled),
                (APP, {"exposed_name": "ha_get_app_raw"}, uncompiled),
                (INTEGRATION, {"classification": "automatic_read"}, uncompiled),
                ("ha_get_state", {"argument_restrictions": [f"ha-mcp-{version}-platform-integrations-v1"]},
                 uncompiled),
                # Dropping the token leaves a wrapper entry whose reviewed
                # descriptor binding no longer compiles.
                (INTEGRATION, {"argument_restrictions": []}, None),
            )
            with tempfile.TemporaryDirectory() as directory:
                candidate = Path(directory) / "policy.json"
                for name, change, code in changes:
                    with self.subTest(version=version, name=name, change=change):
                        mutated = deepcopy(value)
                        next(x for x in mutated["tools"] if x["upstream_name"] == name).update(change)
                        candidate.write_text(json.dumps(mutated))
                        with self.assertRaises(UpstreamToolPolicyError) as raised:
                            load_upstream_tool_policy(candidate, **binding)
                        if code is not None:
                            self.assertEqual(str(raised.exception), code)

    def test_registry_evidence_and_exact_catalogs(self):
        registry = load_reviewed_upstream_release_registry()
        validate_reviewed_release_evidence(repository_root=ROOT)
        for version in VERSIONS:
            with self.subTest(version=version):
                release = registry.by_version[version]
                reads = {e.exposed_name for e in release.policy.tools if e.is_read_route}
                self.assertEqual(reads, EXACT_READS)
                self.assertEqual(release.policy.classification_counts["automatic_read"], 28)
                result = validate_reviewed_release_catalog(
                    release, observed_server_name="ha-mcp", observed_upstream_version=version,
                    observed_protocol_version="2025-03-26", tools=capture(version)["tools"])
                self.assertTrue(result.valid, result)

    def test_signed_reuse_withholds_only_the_unbound_platform_wrappers(self):
        registry = load_reviewed_upstream_release_registry()
        for version in VERSIONS:
            release = registry.by_version[version]
            policy = release.policy
            with self.subTest(version=version, bound=True):
                view, withheld = platform.withhold_unbound_platform_reads(policy, version)
                self.assertIs(view, policy)
                self.assertEqual(withheld, ())
            with self.subTest(version=version, bound=False):
                view, withheld = platform.withhold_unbound_platform_reads(policy, "9.0.0")
                self.assertEqual(withheld, tuple(sorted(TOOLS)))
                self.assertEqual(len(view.tools), len(policy.tools))
                for before, after in zip(policy.tools, view.tools, strict=True):
                    if before.upstream_name in TOOLS:
                        self.assertEqual(after, replace(before, argument_restrictions=()))
                        self.assertEqual(after.classification, "mixed_or_requires_wrapper")
                        self.assertFalse(after.is_read_route)
                        self.assertIsNone(platform.binding(after))
                    else:
                        self.assertIs(after, before)
                self.assertEqual(
                    {e.upstream_name for e in view.tools if e.is_read_route},
                    {e.upstream_name for e in policy.tools if e.is_read_route} - set(TOOLS))
                self.assertEqual(view.classification_counts["automatic_read"],
                                 policy.classification_counts["automatic_read"] - 3)
                profile = readmission._profile_for_release(replace(release, policy=view))
                self.assertFalse({c.capability_id for c in profile.capabilities} & set(TOOLS))
                self.assertEqual(platform.PLATFORM_READ_BINDING_ABSENT, "platform_read_binding_absent")

    def test_readmission_profile_binds_the_adapter_tokens(self):
        registry = load_reviewed_upstream_release_registry()
        for version in VERSIONS:
            release = registry.by_version[version]
            profile = readmission._profile_for_release(release)
            by_id = {c.capability_id: c for c in profile.capabilities}
            for tool in TOOLS:
                with self.subTest(version=version, tool=tool):
                    contract = by_id[tool]
                    self.assertEqual(contract.kind, CapabilityKind.ORDINARY_READ)
                    token = f"ha-mcp-{version}-platform-{TOKEN_KIND[tool]}-v1"
                    expected = readmission._expected_contract_fingerprint(
                        tool_name=tool, argument_restrictions=(token,),
                        release_contract=release.tool_contracts_by_name[tool])
                    self.assertEqual(contract.contract_fingerprint, expected)
                    self.assertNotEqual(contract.contract_fingerprint, readmission._expected_contract_fingerprint(
                        tool_name=tool, argument_restrictions=(),
                        release_contract=release.tool_contracts_by_name[tool]))

    def test_gateway_contract_binds_the_adapter_token(self):
        registry = load_reviewed_upstream_release_registry()
        for version in VERSIONS:
            policy = registry.by_version[version].policy
            observed = {item["name"]: item for item in capture(version)["tools"]}
            for tool in TOOLS:
                with self.subTest(version=version, tool=tool):
                    entry = policy.by_name[tool]
                    kwargs = dict(
                        protocol_version="2025-03-26",
                        reviewed_runtime_description_fingerprint=policy.reviewed_runtime_description_fingerprints_by_name[tool],
                        reviewed_runtime_annotation_fingerprint=policy.reviewed_runtime_annotation_fingerprints_by_name[tool],
                        reviewed_runtime_output_schema_fingerprint=policy.reviewed_runtime_output_schema_fingerprints_by_name[tool],
                    )
                    decision = gateway_module._compare_tool_contract(entry, observed[tool], **kwargs)
                    self.assertTrue(decision.accepted, decision.reason)
                    with patch.object(gateway_module, "is_platform_adapter", lambda _entry: False):
                        generic = gateway_module._compare_tool_contract(entry, observed[tool], **kwargs)
                    self.assertNotEqual(decision.expected_fingerprint, generic.expected_fingerprint)


class CatalogMetadataTests(unittest.TestCase):
    def test_capability_rows_are_truthful_and_bound(self):
        rows = {row["tool"]: row for row in build_capability_catalog()["provider_matrix"]}
        for tool in TOOLS:
            with self.subTest(tool=tool):
                row = rows[tool]
                self.assertEqual(row["selected_provider"], "upstream_platform_reads")
                self.assertEqual(row["fallback_policy"], "none")
                self.assertEqual(row["direct_ha_coverage"], "not_used")
                self.assertEqual(row["trust_mode"], "reviewed_argument_constrained")
                tokens = {
                    token for token, (bound, *_rest) in platform.ADAPTER_BINDINGS.items() if bound == tool}
                self.assertEqual(set(row["trust_profile"].split(", ")), tokens)
        self.assertIn("supervisor_unattested", rows[APP]["core_applicability"])
        self.assertNotIn("rest", rows[APP]["core_applicability"])
        self.assertIn("unattested", rows[HEALTH]["core_applicability"])

    def test_core_routes_cover_every_new_read(self):
        self.assertEqual(delegated_requirements(HEALTH), ("core.basic_websocket_read", "core.basic_rest_read"))
        self.assertEqual(delegated_requirements(APP), ("core.basic_websocket_read",))
        self.assertEqual(delegated_requirements(INTEGRATION), ("core.basic_websocket_read", "core.basic_rest_read"))
        registry = load_reviewed_upstream_release_registry()
        for version in VERSIONS:
            reads = {e.upstream_name for e in registry.by_version[version].policy.tools if e.is_read_route}
            with self.subTest(version=version):
                self.assertTrue(reads <= set(DELEGATED_CORE_REQUIREMENTS), reads - set(DELEGATED_CORE_REQUIREMENTS))

    def test_signed_core_registry_is_read_only_and_covers_supported_cores(self):
        registry = json.loads((ROOT / "upstream-trust/ha-core-release-registry.json").read_text())
        tip = registry["envelopes"][-1]
        self.assertEqual(tip["sequence"], 15)
        entries = {entry["version"]: entry for entry in tip["entries"]}
        self.assertEqual(sorted(entries), ["2026.10.0", "2026.10.1", "2026.10.3"])
        for version in ("2026.10.0", "2026.10.1", "2026.10.3"):
            with self.subTest(version=version):
                capabilities = {item["capability_id"] for item in entries[version]["capabilities"]}
                self.assertTrue({"core.basic_websocket_read", "core.basic_rest_read"} <= capabilities)
        revoked = {item["version"] for item in tip["revocations"]}
        self.assertEqual(revoked, {"2026.9.2", "2026.9.3", "2026.9.4"})
        self.assertFalse(revoked & set(entries))


import tests.test_core_update_continuity as core_continuity  # noqa: E402
from core_registry_fixtures import core_entry  # noqa: E402


class CoreRouteTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = core_continuity.CoreContinuityTests.asyncSetUp
    runtime = core_continuity.CoreContinuityTests.runtime
    assert_admitted = core_continuity.CoreContinuityTests.assert_admitted

    async def test_unknown_core_withholds_all_three_without_dispatch(self):
        core, _ = await self.runtime("2026.9.3")
        self.assert_admitted(core, 0)
        g, t = await make_gateway("8.5.0", core_runtime=core)
        exposed = set(g._registered_tool_registry.snapshot())
        self.assertFalse(set(TOOLS) & exposed)
        health = g.health_snapshot()
        self.assertTrue(set(TOOLS) <= {item["tool"] for item in health["core_withheld_tools"]})
        self.assertEqual(health["core_withheld_read_count"], 28)
        self.assertFalse(health["core_withheld_truncated"])
        self.assertEqual(t.sent, [])

    async def test_withheld_list_reports_truncation(self):
        core, _ = await self.runtime("2026.9.3")
        with patch.object(gateway_module, "MAX_QUARANTINE_RECORDS", 4):
            g, _ = await make_gateway("8.5.0", core_runtime=core)
            health = g.health_snapshot()
        self.assertEqual(len(health["core_withheld_tools"]), 4)
        self.assertTrue(health["core_withheld_truncated"])

    async def test_missing_websocket_profile_withholds_exactly_its_consumers(self):
        entry = core_entry()
        entry["capabilities"] = [
            item for item in entry["capabilities"] if item["capability_id"] != "core.basic_websocket_read"]
        self.raw = self.signer.journal_raw(entries=[entry])
        core, _ = await self.runtime()
        g, t = await make_gateway("8.5.0", core_runtime=core)
        exposed = set(g._registered_tool_registry.snapshot())
        self.assertFalse(set(TOOLS) & exposed, g.health_snapshot())
        self.assertIn("ha_get_skill_guide", exposed)
        self.assertEqual(t.sent, [])

    async def test_admitted_core_leases_each_dispatch_and_releases(self):
        core, _ = await self.runtime()
        self.assert_admitted(core)
        g, t = await make_gateway("8.5.0", core_runtime=core)
        self.assertTrue(set(TOOLS) <= set(g._registered_tool_registry.snapshot()))
        active = []

        def observe(tool, args):
            # Runs after the transport's before_dispatch commit hook.
            active.append(core.health_snapshot()["active_commit_count"])
            return default_response(tool, args)
        t.respond = observe
        for tool, args in ((HEALTH, {"sections": ["repairs"]}), (INTEGRATION, {}), (APP, {"include_stats": True})):
            with self.subTest(tool=tool):
                result = await call(g, tool, args)
                self.assertTrue(result["success"], result)
        self.assertEqual(len(t.sent), 4)
        # One commit per required Core profile: health and integrations hold
        # WS+REST, each app dispatch (pre-check and stats call) holds WS.
        self.assertEqual(active, [2, 2, 1, 1])
        health = core.health_snapshot()
        self.assertEqual(health["active_commit_count"], 0)
        self.assertEqual(health["issued_lease_count"], 0)
        self.assertEqual(health["fallback_count"], 0)


if __name__ == "__main__":
    unittest.main()
