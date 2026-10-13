#!/usr/bin/env python3
"""Generate HAMCP-129 delegated platform-read fixtures from pinned ha-mcp source.

Every ``result`` in the generated ``results-<version>.json`` files is the exact
``structured_content`` returned by the pinned upstream ha-mcp tool, invoked
through a bare FastMCP instance on which only that tool group's upstream
registration function ran. The upstream code talks to a disposable loopback
fake Home Assistant Core and Supervisor peer (defined in this file) that serves
only synthetic data. Nothing here contacts a live Home Assistant, Supervisor,
deployed MCP endpoint, or any non-loopback host; a socket guard installed in the
worker process enforces that.

Usage (orchestrator)::

    python generate.py \
        --source-843 /path/ha-mcp@eac7a3a --python-843 /venv-8.4.3/bin/python \
        --source-850 /path/ha-mcp@311d6dc --python-850 /venv-8.5.0/bin/python \
        --source-860 /path/ha-mcp@fc54437 --python-860 /venv-8.6.0/bin/python \
        --output-dir tests/fixtures/hamcp129

Each ``--python-*`` interpreter must have the version's locked runtime
dependencies installed (the ha-mcp package itself is imported from
``<source>/src`` and asserted to resolve there). Each (version, case) runs in a
fresh worker subprocess so pooled WebSocket clients, capability caches, and the
update-check memo cannot leak between cases.

The outputs must not be edited by hand; rerun this generator instead.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

# ---------------------------------------------------------------------------
# Pinned upstream sources
# ---------------------------------------------------------------------------

VERSIONS: dict[str, dict[str, str]] = {
    "8.4.3": {"commit": "eac7a3aa7063432e9af17e7d7726040e909c7b8f", "flag": "843"},
    "8.5.0": {"commit": "311d6dc273fb4e9a5b8cde0de15f69472a64fe44", "flag": "850"},
    "8.6.0": {"commit": "fc54437a804858732e4bc927add98e202d879a09", "flag": "860"},
}

PORT = 38129
HOST = "127.0.0.1"
CORE_PREFIX = "/core"
SUPERVISOR_PREFIX = "/supervisor"
HA_URL = f"http://{HOST}:{PORT}{CORE_PREFIX}"
SUPERVISOR_URL = f"http://{HOST}:{PORT}{SUPERVISOR_PREFIX}"
CORE_TOKEN = "synthetic-core-token-not-a-secret"
SUPERVISOR_TOKEN = "synthetic-supervisor-token-not-a-secret"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

ALL_SECTIONS = (
    "dead_entities,matter_network,repairs,themes,thread_network,"
    "zha_network,zwave_network"
)

# (case, tool, arguments, scenario, mode). ``mode`` "addon" runs ha-mcp as an
# add-on (SUPERVISOR_TOKEN set, direct Supervisor REST, update check served by
# the fake Supervisor). "core_ws_proxy" runs it off-host: Supervisor calls go
# through Core's ``supervisor/api`` WebSocket command and the update check is
# disabled (it would otherwise target PyPI).
CASES: list[tuple[str, str, dict[str, Any], str, str]] = [
    ("health_baseline", "ha_get_system_health", {}, "ok", "addon"),
    (
        "health_all_sections",
        "ha_get_system_health",
        {"include": ALL_SECTIONS, "include_dismissed_repairs": True},
        "ok",
        "addon",
    ),
    (
        "health_sections_unavailable",
        "ha_get_system_health",
        {"include": ALL_SECTIONS, "include_dismissed_repairs": True},
        "sections_unavailable",
        "addon",
    ),
    (
        "health_baseline_unavailable",
        "ha_get_system_health",
        {"include": "dead_entities"},
        "baseline_unavailable",
        "addon",
    ),
    (
        "apps_installed",
        "ha_get_app",
        {"source": "installed", "include_stats": False},
        "ok",
        "addon",
    ),
    (
        "apps_installed_stats",
        "ha_get_app",
        {"source": "installed", "include_stats": True},
        "stats_failure",
        "addon",
    ),
    (
        "apps_installed@core_ws_proxy",
        "ha_get_app",
        {"source": "installed", "include_stats": False},
        "ok",
        "core_ws_proxy",
    ),
    (
        "apps_installed_stats@core_ws_proxy",
        "ha_get_app",
        {"source": "installed", "include_stats": True},
        "stats_failure",
        "core_ws_proxy",
    ),
    (
        "integrations_list",
        "ha_get_integration",
        {"limit": 200, "offset": 0, "exact_match": True, "include_options": False},
        "ok",
        "addon",
    ),
    (
        "integrations_query",
        "ha_get_integration",
        {
            "limit": 200,
            "offset": 0,
            "exact_match": True,
            "include_options": False,
            "query": "zha",
        },
        "ok",
        "addon",
    ),
]

TOOL_REGISTRATION = {
    "ha_get_system_health": ("ha_mcp.tools.tools_system", "register_system_tools"),
    "ha_get_app": ("ha_mcp.tools.tools_addons", "register_addon_tools"),
    "ha_get_integration": (
        "ha_mcp.tools.tools_integrations",
        "register_integration_tools",
    ),
}

# Read-only WebSocket commands the fake peer is willing to observe. Anything
# else (options flows, service calls, registry writes, reloads) fails the run.
ALLOWED_WS_TYPES = frozenset(
    {
        "auth",
        "system_health/info",
        "ha_mcp_tools/info",
        "repairs/list_issues",
        "zha/devices",
        "config_entries/get",
        "zwave_js/network_status",
        "otbr/info",
        "frontend/get_themes",
        "config/entity_registry/list",
        "logger/log_info",
        "supervisor/api",
    }
)

# ---------------------------------------------------------------------------
# Synthetic world (all values are fabricated; canaries are marked "synthetic")
# ---------------------------------------------------------------------------

TS = 1767225600.0  # 2026-01-01T00:00:00Z, fixed
ISO = "2026-01-01T00:00:00+00:00"
LONG_TITLE = (
    "Synthetic integration with a deliberately long title that exceeds one "
    "hundred and twenty characters so truncation behaviour can be observed "
    "end to end"
)
LONG_DESCRIPTION = (
    "Synthetic Beta is a fabricated add-on used only by HAMCP-129 fixtures. "
    "Its description is intentionally longer than two hundred characters so "
    "that any upstream or Engineering-side truncation of app descriptions can "
    "be observed deterministically in generated fixture output."
)
EMAIL_CANARY = "synthetic.user@example.invalid"
REMOVED_ENTRY_ID = "01SYNTHREMOVEDENTRY0000000X"


def _entry(
    entry_id: str,
    domain: str,
    title: str,
    state: str,
    *,
    supports_options: bool = True,
    reason: str | None = None,
    disabled_by: str | None = None,
) -> dict[str, Any]:
    """A config entry in Core's ``as_json_fragment`` shape plus canary extras."""
    return {
        "created_at": TS,
        "disabled_by": disabled_by,
        "domain": domain,
        "entry_id": entry_id,
        "error_reason_translation_key": None,
        "error_reason_translation_placeholders": None,
        "modified_at": TS,
        "num_subentries": 0,
        "pref_disable_new_entities": False,
        "pref_disable_polling": False,
        "reason": reason,
        "source": "user",
        "state": state,
        "supported_subentry_types": {},
        "supports_options": supports_options,
        "supports_reconfigure": False,
        "supports_remove_device": False,
        "supports_unload": True,
        "title": title,
        # Canary extras Core does not send in this listing.
        "data": {
            "synthetic_secret": f"synthetic-canary-data-{domain}",
            "host": "synthetic-host.example.invalid",
        },
        "unique_id": f"synthetic-unique-id-{domain}",
        "options": {"synthetic_option": f"synthetic-canary-option-{domain}"},
    }


CONFIG_ENTRIES: list[dict[str, Any]] = [
    _entry("01SYNTHZHA00000000000000000", "zha", "Synthetic ZHA Coordinator", "loaded"),
    _entry(
        "01SYNTHZHATOOLKIT0000000000",
        "zha_toolkit",
        "Synthetic ZHA Toolkit",
        "setup_retry",
        supports_options=False,
        reason="synthetic retry reason",
    ),
    _entry("01SYNTHZWAVEJS000000000000", "zwave_js", "Synthetic Z-Wave JS", "loaded"),
    _entry(
        "01SYNTHMATTER0000000000000",
        "matter",
        f"Matter Server for {EMAIL_CANARY}",
        "loaded",
    ),
    _entry(
        "01SYNTHOTBR000000000000000",
        "otbr",
        "Synthetic OpenThread Border Router",
        "loaded",
    ),
    _entry(
        "01SYNTHCLOUDACCOUNT0000000",
        "synthetic_account",
        f"Account {EMAIL_CANARY}",
        "loaded",
    ),
    _entry(
        "01SYNTHLONGTITLE0000000000",
        "synthetic_long_title",
        LONG_TITLE,
        "not_loaded",
        supports_options=False,
        disabled_by="user",
    ),
]

LOGGER_INFO = [
    {"domain": "zha", "level": 10},
    {"domain": "zwave_js", "level": 20},
    {"domain": "synthetic_account", "level": 15},
]

SYSTEM_HEALTH_DATA: dict[str, Any] = {
    "homeassistant": {
        "info": {
            "installation_type": "Home Assistant OS",
            "version": "2026.10.0",
            "dev": False,
            "hassio": True,
            "virtualenv": False,
            "python_version": "3.13.7",
            "docker": True,
            "arch": "x86_64",
            "timezone": "Synthetic/Canary_Zone",
            "os_name": "Linux",
            "os_version": "6.12.0-synthetic",
            "user": "synthetic-user",
            "config_dir": "/synthetic/config-dir",
            "run_as_root": True,
        }
    },
    "hassio": {
        "info": {
            "host_os": "Home Assistant OS 16.0",
            "update_channel": "stable",
            "supervisor_version": "supervisor-2026.10.0",
            "agent_version": "1.7.2",
            "docker_version": "28.3.3",
            "disk_total": "30.8 GB",
            "disk_used": "9.1 GB",
            "nameservers": "192.0.2.53, 198.51.100.53",
            "healthy": True,
            "supported": True,
            "host_connectivity": {"type": "pending"},
            "supervisor_connectivity": True,
            "ntp_synchronized": True,
            "virtualization": "kvm",
            "board": "ova",
            "supervisor_api": "ok",
            "version_api": {"type": "failed", "error": "synthetic-error"},
            "installed_addons": (
                "Synthetic Alpha (1.0.0), Synthetic Beta (2.0.0), "
                "Synthetic Gamma (3.0.0)"
            ),
        },
        "manage_url": "/hassio/system",
    },
    "cloud": {
        "info": {
            "logged_in": True,
            "subscription_expiration": "2027-01-01T00:00:00+00:00",
            "relayer_connected": True,
            "relayer_region": "synthetic-region",
            "remote_enabled": True,
            "remote_connected": True,
            "alexa_enabled": False,
            "google_enabled": False,
            "cloud_ice_servers_enabled": True,
            "remote_server": "synthetic-remote.example.invalid",
            "certificate_status": "ready",
            "certificate_fingerprint": "53:59:4E:54:48:45:54:49:43",
            "instance_id": "synthetic-instance-id-0000",
            "can_reach_cert_server": "ok",
            "can_reach_cloud_auth": "ok",
            "can_reach_cloud": "ok",
        }
    },
    "synthetic_domain": {"info": {"synthetic_key": "synthetic-value"}},
}

REPAIR_ISSUES = [
    {
        "breaks_in_ha_version": "2026.12.0",
        "created": ISO,
        "dismissed_version": None,
        "ignored": False,
        "domain": "synthetic_account",
        "is_fixable": True,
        "issue_domain": None,
        "issue_id": "synthetic_active_issue",
        "learn_more_url": "https://synthetic-docs.example.invalid/learn-more",
        "severity": "warning",
        "translation_key": "synthetic_active_issue",
        "translation_placeholders": {
            "host": "synthetic-host.example.invalid",
            "url": "https://synthetic-host.example.invalid/admin",
        },
        "data": {"synthetic_issue_data": "synthetic-canary-issue-data"},
    },
    {
        "breaks_in_ha_version": None,
        "created": ISO,
        "dismissed_version": "2026.9.0",
        "ignored": True,
        "domain": "zha",
        "is_fixable": False,
        "issue_domain": None,
        "issue_id": "synthetic_dismissed_issue",
        "learn_more_url": None,
        "severity": "error",
        "translation_key": "synthetic_dismissed_issue",
        "translation_placeholders": {"host": "192.0.2.10"},
        "data": {"synthetic_issue_data": "synthetic-canary-dismissed-data"},
    },
]

ZHA_DEVICES = [
    {
        "ieee": "00:11:22:33:44:55:66:77",
        "nwk": "0x0000",
        "manufacturer": "Synthetic Radios",
        "model": "SYN-COORD",
        "name": "Synthetic Radios SYN-COORD",
        "quirk_applied": False,
        "quirk_class": "zigpy.device.Device",
        "manufacturer_code": 4660,
        "power_source": "Mains",
        "lqi": 255,
        "rssi": None,
        "last_seen": ISO,
        "available": True,
        "device_type": "Coordinator",
        "user_given_name": None,
        "device_reg_id": "synthetic-device-reg-coordinator",
        "area_id": None,
        "active_coordinator": True,
        "synthetic_unknown_key": "synthetic-canary-zha-extra",
    },
    {
        "ieee": "00:11:22:33:44:55:66:88",
        "nwk": "0x1234",
        "manufacturer": "Synthetic Sensors",
        "model": "SYN-TEMP",
        "name": "Synthetic Sensors SYN-TEMP",
        "quirk_applied": True,
        "quirk_class": "synthetic.quirks.SyntheticTemp",
        "manufacturer_code": 4661,
        "power_source": "Battery or Unknown",
        "lqi": 120,
        "rssi": -71,
        "last_seen": ISO,
        "available": False,
        "device_type": "EndDevice",
        "user_given_name": "Synthetic Hallway Sensor",
        "device_reg_id": "synthetic-device-reg-temp",
        "area_id": "synthetic_hallway",
        "active_coordinator": False,
        "synthetic_unknown_key": "synthetic-canary-zha-extra-2",
    },
]

ZWAVE_STATUS = {
    "client": {
        "ws_server_url": "ws://synthetic-zwave.example.invalid:3000",
        "state": "connected",
        "driver_version": "15.0.0",
        "server_version": "3.0.0",
        "server_logging_enabled": False,
    },
    "controller": {
        "home_id": 3735928559,
        "sdk_version": "7.22.1",
        "type": 1,
        "own_node_id": 1,
        "is_primary": True,
        "is_using_home_id_from_other_network": False,
        "is_sis_present": True,
        "was_real_primary": True,
        "is_suc": True,
        "node_type": 0,
        "firmware_version": "7.22.1",
        "manufacturer_id": 134,
        "product_id": 90,
        "product_type": 1,
        "supported_function_types": [2, 3, 4],
        "suc_node_id": 1,
        "supports_timers": False,
        "supports_long_range": True,
        "is_rebuilding_routes": False,
        "inclusion_state": 0,
        "rf_region": 1,
        "status": 0,
        "synthetic_controller_extra": "synthetic-canary-zwave-controller",
        "nodes": [
            {
                "node_id": 1,
                "is_routing": True,
                "status": 4,
                "is_secure": False,
                "ready": True,
                "zwave_plus_version": None,
                "highest_security_class": None,
                "is_controller_node": True,
                "has_firmware_update_cc": False,
                "synthetic_node_extra": "synthetic-canary-zwave-node-1",
            },
            {
                "node_id": 7,
                "is_routing": True,
                "status": 3,
                "is_secure": True,
                "ready": True,
                "zwave_plus_version": 2,
                "highest_security_class": 1,
                "is_controller_node": False,
                "has_firmware_update_cc": True,
                "synthetic_node_extra": "synthetic-canary-zwave-node-7",
            },
        ],
    },
}

OTBR_INFO = {
    "a1b2c3d4e5f60718": {
        "active_dataset_tlvs": "0e0800000000000100005e5953594e5448455449430000",
        "border_agent_id": "53594e544845544943424f5244455200",
        "channel": 15,
        "extended_address": "a1b2c3d4e5f60718",
        "extended_pan_id": "53594e5448455449",
        "url": "http://synthetic-otbr.example.invalid:8081",
    }
}

THEMES = {
    "themes": {
        "synthetic_dark": {"primary-color": "#123456"},
        "synthetic_light": {"primary-color": "#abcdef"},
    },
    "default_theme": "default",
    "default_dark_theme": "synthetic_dark",
}


def _registry(
    entity_id: str, config_entry_id: str | None, *, disabled_by: str | None = None
) -> dict[str, Any]:
    return {
        "area_id": None,
        "categories": {},
        "config_entry_id": config_entry_id,
        "config_subentry_id": None,
        "created_at": TS,
        "device_id": None,
        "disabled_by": disabled_by,
        "entity_category": None,
        "entity_id": entity_id,
        "has_entity_name": True,
        "hidden_by": None,
        "icon": None,
        "id": "synthetic-registry-" + entity_id.replace(".", "-"),
        "labels": [],
        "modified_at": TS,
        "name": None,
        "options": {"synthetic_registry_option": "synthetic-canary-registry"},
        "original_name": entity_id.split(".", 1)[1].replace("_", " ").title(),
        "platform": "synthetic_platform",
        "translation_key": None,
        "unique_id": "synthetic-unique-" + entity_id,
    }


ENTITY_REGISTRY = [
    _registry("sensor.synthetic_alive", "01SYNTHZHA00000000000000000"),
    _registry("sensor.synthetic_stale", "01SYNTHZHA00000000000000000"),
    _registry("sensor.synthetic_orphan", REMOVED_ENTRY_ID),
    _registry("sensor.synthetic_unknown", "01SYNTHZWAVEJS000000000000"),
    _registry("switch.synthetic_offline", "01SYNTHZWAVEJS000000000000"),
    _registry(
        "light.synthetic_disabled", "01SYNTHZHA00000000000000000", disabled_by="user"
    ),
]


def _state(entity_id: str, state: str, **attrs: Any) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": {"friendly_name": entity_id.split(".", 1)[1], **attrs},
        "last_changed": ISO,
        "last_reported": ISO,
        "last_updated": ISO,
        "context": {"id": "01SYNTHCONTEXT000000000000", "parent_id": None, "user_id": None},
    }


STATES = [
    _state("sensor.synthetic_alive", "21.5", unit_of_measurement="°C"),
    _state("sensor.synthetic_stale", "unavailable", restored=True),
    _state("sensor.synthetic_orphan", "unavailable", restored=True),
    _state("sensor.synthetic_unknown", "unknown"),
    _state("switch.synthetic_offline", "unavailable"),
]


def _addon(
    slug: str,
    name: str,
    description: str,
    version: str,
    version_latest: str,
    state: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "slug": slug,
        "description": description,
        "advanced": False,
        "stage": "stable",
        "version": version,
        "version_latest": version_latest,
        "update_available": version != version_latest,
        "available": True,
        "detached": False,
        "homeassistant": None,
        "state": state,
        "repository": "synthetic_repository",
        "build": False,
        "url": "https://synthetic-addons.example.invalid/" + slug,
        "icon": True,
        "logo": False,
        "system_managed": False,
        # Canary extras.
        "options": {"password": f"synthetic-canary-password-{slug}"},
        "network": {"8080/tcp": 8080},
        "ip_address": "172.30.33.9",
        "hostname": f"synthetic-{slug.replace('_', '-')}",
        "ingress_url": f"/api/hassio_ingress/synthetic-ingress-token-{slug}",
        "webui": "http://[HOST]:[PORT:8080]/synthetic",
    }


ADDONS = [
    _addon(
        "synthetic_alpha",
        "Synthetic Alpha",
        "Synthetic add-on alpha.",
        "1.0.0",
        "1.1.0",
        "started",
    ),
    _addon(
        "synthetic_beta",
        "Synthetic Beta",
        LONG_DESCRIPTION,
        "2.0.0",
        "2.0.0",
        "started",
    ),
    _addon(
        "synthetic_gamma",
        "Synthetic Gamma",
        "Synthetic add-on gamma (stopped).",
        "3.0.0",
        "3.0.0",
        "stopped",
    ),
]

ADDON_STATS = {
    "cpu_percent": 1.5,
    "memory_usage": 104857600,
    "memory_limit": 2147483648,
    "memory_percent": 4.88,
    "network_rx": 1024,
    "network_tx": 2048,
    "blk_read": 0,
    "blk_write": 0,
}
STATS_FAILURE_SLUG = "synthetic_beta"
STATS_FAILURE_MESSAGE = "synthetic stats failure"


# ---------------------------------------------------------------------------
# Loopback fake Home Assistant Core + Supervisor peer (stdlib only)
# ---------------------------------------------------------------------------


class FakePeer:
    """Minimal HTTP/1.1 + RFC 6455 server serving the synthetic world."""

    def __init__(self, scenario: str, build_version: str) -> None:
        self.scenario = scenario
        self.build_version = build_version
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.base_events.Server | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._startup_error: BaseException | None = None

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        self._thread.start()
        self._ready.wait(10)
        if self._startup_error is not None:
            raise self._startup_error
        if not self._ready.is_set():
            raise RuntimeError("fake peer did not start")

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._server = loop.run_until_complete(
                asyncio.start_server(self._handle, HOST, PORT, reuse_address=True)
            )
        except BaseException as exc:  # noqa: BLE001
            self._startup_error = exc
            self._ready.set()
            return
        self._ready.set()
        loop.run_forever()

    def _record(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.requests.append(entry)

    # -- HTTP ----------------------------------------------------------
    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                lines = head.decode("latin-1").split("\r\n")
                method, target, _version = lines[0].split(" ", 2)
                headers: dict[str, str] = {}
                for line in lines[1:]:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        headers[k.strip().lower()] = v.strip()
                body = b""
                if "content-length" in headers:
                    body = await reader.readexactly(int(headers["content-length"]))
                if headers.get("upgrade", "").lower() == "websocket":
                    await self._websocket(reader, writer, method, target, headers)
                    return
                status, payload = self._http(method, target, headers, body)
                raw = json.dumps(payload).encode()
                writer.write(
                    f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(raw)}\r\nConnection: keep-alive\r\n\r\n".encode()
                    + raw
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _auth_label(value: str | None) -> str:
        if value == f"Bearer {CORE_TOKEN}":
            return "synthetic-core-token"
        if value == f"Bearer {SUPERVISOR_TOKEN}":
            return "synthetic-supervisor-token"
        return "none" if value is None else "other"

    def _http(
        self, method: str, target: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, Any]:
        parts = urlsplit(target)
        path = parts.path
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        parsed_body: Any = None
        if body:
            try:
                parsed_body = json.loads(body)
            except ValueError:
                parsed_body = body.decode("utf-8", "replace")
        self._record(
            {
                "transport": "http",
                "method": method,
                "path": path,
                "query": query,
                "body": parsed_body,
                "auth": self._auth_label(headers.get("authorization")),
            }
        )
        if method != "GET":
            return 405, {"message": "fake peer is read-only"}
        if path.startswith(CORE_PREFIX + "/api/"):
            return self._core_rest(path[len(CORE_PREFIX) + len("/api") :])
        if path.startswith(SUPERVISOR_PREFIX + "/"):
            status, data = self._supervisor(path[len(SUPERVISOR_PREFIX) :])
            return status, data
        return 404, {"message": "Not found"}

    def _core_rest(self, sub: str) -> tuple[int, Any]:
        if sub == "/states":
            return 200, copy.deepcopy(STATES)
        if sub == "/config/config_entries/entry":
            return 200, copy.deepcopy(CONFIG_ENTRIES)
        return 404, {"message": "Not found"}

    def _supervisor(self, sub: str) -> tuple[int, Any]:
        """Return (status, Supervisor REST envelope)."""
        if sub == "/addons":
            return 200, {"result": "ok", "data": {"addons": copy.deepcopy(ADDONS)}}
        if sub == "/addons/self/info":
            return 200, {
                "result": "ok",
                "data": {
                    "version": self.build_version,
                    "version_latest": self.build_version,
                    "update_available": False,
                },
            }
        if sub.startswith("/addons/") and sub.endswith("/stats"):
            slug = sub[len("/addons/") : -len("/stats")]
            if self.scenario == "stats_failure" and slug == STATS_FAILURE_SLUG:
                return 400, {
                    "result": "error",
                    "message": STATS_FAILURE_MESSAGE,
                    "data": {},
                }
            if any(a["slug"] == slug for a in ADDONS):
                return 200, {"result": "ok", "data": dict(ADDON_STATS)}
        return 404, {"result": "error", "message": "Not found", "data": {}}

    # -- WebSocket -----------------------------------------------------
    async def _websocket(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        method: str,
        target: str,
        headers: dict[str, str],
    ) -> None:
        self._record(
            {
                "transport": "http",
                "method": method,
                "path": urlsplit(target).path,
                "query": {},
                "body": None,
                "auth": self._auth_label(headers.get("authorization")),
                "upgrade": "websocket",
            }
        )
        if urlsplit(target).path != CORE_PREFIX + "/websocket":
            writer.write(b"HTTP/1.1 404 X\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return
        accept = base64.b64encode(
            hashlib.sha1((headers["sec-websocket-key"] + WS_GUID).encode()).digest()
        ).decode()
        writer.write(
            (
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode()
        )
        await self._ws_send(writer, {"type": "auth_required", "ha_version": "2026.10.0"})
        authed = False
        while True:
            frame = await self._ws_recv(reader, writer)
            if frame is None:
                return
            msg = json.loads(frame)
            recorded = {k: v for k, v in msg.items() if k != "id"}
            if recorded.get("type") == "auth":
                recorded["access_token"] = (
                    "synthetic-core-token"
                    if msg.get("access_token") == CORE_TOKEN
                    else "other"
                )
            self._record({"transport": "ws", "message": recorded})
            if not authed:
                if msg.get("type") == "auth" and msg.get("access_token") == CORE_TOKEN:
                    authed = True
                    await self._ws_send(
                        writer, {"type": "auth_ok", "ha_version": "2026.10.0"}
                    )
                    continue
                await self._ws_send(writer, {"type": "auth_invalid", "message": "x"})
                return
            for out in self._ws_command(msg):
                await self._ws_send(writer, out)

    def _ws_command(self, msg: dict[str, Any]) -> list[dict[str, Any]]:
        mid = msg.get("id")
        mtype = msg.get("type")

        def ok(result: Any) -> list[dict[str, Any]]:
            return [{"id": mid, "type": "result", "success": True, "result": result}]

        def err(code: str, message: str) -> list[dict[str, Any]]:
            return [
                {
                    "id": mid,
                    "type": "result",
                    "success": False,
                    "error": {"code": code, "message": message},
                }
            ]

        unavailable = self.scenario == "sections_unavailable"
        if mtype == "system_health/info":
            if self.scenario == "baseline_unavailable":
                return err("unknown_error", "synthetic system_health failure")
            # Core 2026.10.0 framing: null result, initial event, update, finish.
            return [
                {"id": mid, "type": "result", "success": True, "result": None},
                {
                    "id": mid,
                    "type": "event",
                    "event": {"type": "initial", "data": copy.deepcopy(SYSTEM_HEALTH_DATA)},
                },
                {
                    "id": mid,
                    "type": "event",
                    "event": {
                        "type": "update",
                        "domain": "hassio",
                        "key": "host_connectivity",
                        "success": True,
                        "data": True,
                    },
                },
                {"id": mid, "type": "event", "event": {"type": "finish"}},
            ]
        if mtype == "ha_mcp_tools/info":
            return err("unknown_command", "Unknown command.")
        if mtype == "repairs/list_issues":
            if unavailable:
                return err("unknown_error", "synthetic repairs failure")
            return ok({"issues": copy.deepcopy(REPAIR_ISSUES)})
        if mtype == "zha/devices":
            if unavailable:
                return err("unknown_error", "synthetic zha failure")
            return ok(copy.deepcopy(ZHA_DEVICES))
        if mtype == "config_entries/get":
            if unavailable:
                return err("unknown_error", "synthetic config entries failure")
            entries = copy.deepcopy(CONFIG_ENTRIES)
            if msg.get("domain"):
                entries = [e for e in entries if e["domain"] == msg["domain"]]
            return ok(entries)
        if mtype == "zwave_js/network_status":
            if unavailable:
                return err("unknown_error", "synthetic zwave failure")
            return ok(copy.deepcopy(ZWAVE_STATUS))
        if mtype == "otbr/info":
            if unavailable:
                return err("otbr_info_failed", "synthetic otbr failure")
            return ok(copy.deepcopy(OTBR_INFO))
        if mtype == "frontend/get_themes":
            if unavailable:
                return err("unknown_error", "synthetic themes failure")
            return ok(copy.deepcopy(THEMES))
        if mtype == "config/entity_registry/list":
            if unavailable:
                return err("unknown_error", "synthetic entity registry failure")
            return ok(copy.deepcopy(ENTITY_REGISTRY))
        if mtype == "logger/log_info":
            return ok(copy.deepcopy(LOGGER_INFO))
        if mtype == "supervisor/api":
            if str(msg.get("method", "get")).lower() != "get":
                return err("unknown_error", "fake peer is read-only")
            status, envelope = self._supervisor(str(msg.get("endpoint")))
            if status != 200:
                # Core's handler: HassioAPIError -> send_error(unknown_error, str(err)).
                return err("unknown_error", envelope.get("message", "error"))
            return ok(envelope.get("data", {}))
        return err("unknown_command", "Unknown command.")

    @staticmethod
    async def _ws_send(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        n = len(data)
        if n < 126:
            hdr = bytes([0x81, n])
        elif n < 65536:
            hdr = bytes([0x81, 126]) + n.to_bytes(2, "big")
        else:
            hdr = bytes([0x81, 127]) + n.to_bytes(8, "big")
        writer.write(hdr + data)
        await writer.drain()

    @staticmethod
    async def _ws_recv(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> str | None:
        buf = b""
        while True:
            try:
                b0, b1 = await reader.readexactly(2)
            except (asyncio.IncompleteReadError, ConnectionError):
                return None
            fin, opcode = b0 & 0x80, b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = int.from_bytes(await reader.readexactly(2), "big")
            elif n == 127:
                n = int.from_bytes(await reader.readexactly(8), "big")
            mask = await reader.readexactly(4) if b1 & 0x80 else b"\0\0\0\0"
            data = bytes(
                c ^ mask[i % 4] for i, c in enumerate(await reader.readexactly(n))
            )
            if opcode == 0x8:  # close
                try:
                    writer.write(bytes([0x88, len(data)]) + data)
                    await writer.drain()
                except ConnectionError:
                    pass
                return None
            if opcode == 0x9:  # ping -> pong
                writer.write(bytes([0x8A, len(data)]) + data)
                await writer.drain()
                continue
            if opcode == 0xA:
                continue
            buf += data
            if fin:
                return buf.decode()


# ---------------------------------------------------------------------------
# Worker: runs one (version, case) against the fake peer
# ---------------------------------------------------------------------------


def _install_network_guard() -> None:
    """Refuse every non-loopback resolution or connection in this process."""
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in (HOST, HOST.encode()):
            raise socket.gaierror(f"hamcp129 network guard refused host {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    def _check(sock: socket.socket, address: Any) -> None:
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            if not (isinstance(address, tuple) and address[0] == HOST):
                raise ConnectionRefusedError(
                    f"hamcp129 network guard refused {address!r}"
                )

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        _check(self, address)
        return real_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> Any:
        _check(self, address)
        return real_connect_ex(self, address)

    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]
    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]


async def _invoke(source: Path, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    import importlib

    import ha_mcp

    module_file = Path(ha_mcp.__file__).resolve()
    if not module_file.is_relative_to((source / "src").resolve()):
        raise SystemExit(f"ha_mcp imported from {module_file}, not {source}/src")
    try:
        from ha_mcp._vendor.fastmcp import FastMCP  # 8.5.0+

        fastmcp_origin = "ha_mcp._vendor.fastmcp"
    except ImportError:
        from fastmcp import FastMCP  # 8.4.3

        fastmcp_origin = "fastmcp"
    from ha_mcp.client.rest_client import HomeAssistantClient

    module_name, register_name = TOOL_REGISTRATION[tool]
    module = importlib.import_module(module_name)
    tool_module_file = Path(module.__file__).resolve()  # type: ignore[arg-type]
    if not tool_module_file.is_relative_to((source / "src").resolve()):
        raise SystemExit(f"{module_name} imported from {tool_module_file}")

    client = HomeAssistantClient(
        base_url=HA_URL, token=CORE_TOKEN, timeout=30, verify_ssl=True
    )
    mcp = FastMCP("hamcp129-fixture")
    getattr(module, register_name)(mcp, client)
    record: dict[str, Any] = {"fastmcp": fastmcp_origin}
    try:
        tool_result = await mcp.call_tool(tool, arguments)
    except Exception as exc:  # noqa: BLE001 - the raise IS the fixture
        record["outcome"] = {"raised": type(exc).__name__, "message": str(exc)}
    else:
        structured = tool_result.structured_content
        if structured is None:
            record["outcome"] = {
                "raised": "NoStructuredContent",
                "message": json.dumps(
                    [getattr(c, "text", repr(c)) for c in tool_result.content]
                ),
            }
        else:
            record["outcome"] = {"result": json.loads(json.dumps(structured))}
    return record


def worker_main(args: argparse.Namespace) -> int:
    _install_network_guard()
    source = Path(args.source).resolve()
    sys.path.insert(0, str(source / "src"))
    case = next(c for c in CASES if c[0] == args.case)
    _, tool, arguments, scenario, _mode = case
    peer = FakePeer(scenario, args.version)
    peer.start()
    try:
        record = asyncio.run(_invoke(source, tool, arguments))
    finally:
        peer.stop()
    with peer._lock:
        record["requests"] = list(peer.requests)
    Path(args.result_file).write_text(json.dumps(record), encoding="utf-8")
    return 0


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _git(source: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", "-C", str(source), *argv],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _verify_checkout(version: str, source: Path) -> None:
    expected = VERSIONS[version]["commit"]
    head = _git(source, "rev-parse", "HEAD")
    if head != expected:
        raise SystemExit(f"{source}: HEAD {head} != pinned {expected} for {version}")
    dirty = _git(source, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        raise SystemExit(f"{source}: working tree is not clean:\n{dirty}")


def _worker_env(scratch: Path, version: str, mode: str) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(scratch),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOMEASSISTANT_URL": HA_URL,
        "HOMEASSISTANT_TOKEN": CORE_TOKEN,
        "HA_MCP_BUILD_VERSION": version,
        "HA_MCP_CONFIG_DIR": str(scratch / "config"),
        "NO_PROXY": "*",
    }
    if mode == "addon":
        env["SUPERVISOR_TOKEN"] = SUPERVISOR_TOKEN
        env["SUPERVISOR_BASE_URL"] = SUPERVISOR_URL
    elif mode == "core_ws_proxy":
        env["HA_MCP_DISABLE_UPDATE_CHECK"] = "1"
    else:
        raise SystemExit(f"unknown mode {mode}")
    return env


def _request_key(entry: dict[str, Any]) -> str:
    return json.dumps(entry, sort_keys=True, ensure_ascii=False)


def _check_requests(case: str, requests: list[dict[str, Any]]) -> None:
    for entry in requests:
        if entry["transport"] == "http":
            if entry["method"] != "GET":
                raise SystemExit(f"{case}: non-GET HTTP request {entry}")
            if "flow" in entry["path"]:
                raise SystemExit(f"{case}: flow request {entry}")
        else:
            msg = entry["message"]
            mtype = msg.get("type")
            if mtype not in ALLOWED_WS_TYPES:
                raise SystemExit(f"{case}: unexpected WS command {msg}")
            if mtype == "supervisor/api" and str(msg.get("method")).lower() != "get":
                raise SystemExit(f"{case}: non-GET supervisor/api {msg}")


def _integration_path(requests: list[dict[str, Any]]) -> str:
    paths = {r.get("path") for r in requests if r["transport"] == "http"}
    ws_types = {r["message"].get("type") for r in requests if r["transport"] == "ws"}
    if "ha_mcp_tools/config_entries" in ws_types:
        return "component_ws"
    if CORE_PREFIX + "/api/config/config_entries/entry" in paths:
        return "legacy_rest"
    return "unknown"


def _package_versions(python: str) -> dict[str, Any]:
    code = (
        "import importlib.metadata as m, json, platform, sys;"
        "print(json.dumps({'python': platform.python_version(),"
        "'implementation': platform.python_implementation(),"
        "'packages': {d.metadata['Name'].lower(): d.version for d in m.distributions()"
        " if d.metadata['Name'].lower() not in ('pip', 'setuptools', 'wheel')}}))"
    )
    out = subprocess.run(
        [python, "-I", "-c", code], check=True, capture_output=True, text=True
    ).stdout
    data = json.loads(out)
    data["packages"] = dict(sorted(data["packages"].items()))
    return data


def _dump(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def orchestrate(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve()
    sources = {
        "8.4.3": Path(args.source_843).resolve(),
        "8.5.0": Path(args.source_850).resolve(),
        "8.6.0": Path(args.source_860).resolve(),
    }
    pythons = {
        "8.4.3": args.python_843,
        "8.5.0": args.python_850,
        "8.6.0": args.python_860,
    }
    for version, source in sources.items():
        _verify_checkout(version, source)

    provenance_versions: dict[str, Any] = {}
    integration_paths: dict[str, dict[str, str]] = {}
    for version, source in sources.items():
        results: dict[str, Any] = {}
        fastmcp_origin = None
        for case, tool, arguments, scenario, mode in CASES:
            with tempfile.TemporaryDirectory(prefix="hamcp129-") as tmp:
                scratch = Path(tmp)
                result_file = scratch / "result.json"
                proc = subprocess.run(
                    [
                        pythons[version],
                        "-s",
                        str(script),
                        "--worker",
                        "--version",
                        version,
                        "--source",
                        str(source),
                        "--case",
                        case,
                        "--result-file",
                        str(result_file),
                    ],
                    cwd=scratch,
                    env=_worker_env(scratch, version, mode),
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if proc.returncode != 0 or not result_file.exists():
                    sys.stderr.write(proc.stderr[-8000:])
                    raise SystemExit(f"worker failed: {version} {case}")
                record = json.loads(result_file.read_text(encoding="utf-8"))
            requests = sorted(record["requests"], key=_request_key)
            _check_requests(f"{version}/{case}", requests)
            fastmcp_origin = record["fastmcp"]
            entry: dict[str, Any] = {
                "tool": tool,
                "arguments": arguments,
                "scenario": scenario,
                "mode": mode,
                "requests": requests,
            }
            entry.update(record["outcome"])
            if tool == "ha_get_integration":
                integration_paths.setdefault(version, {})[case] = _integration_path(
                    requests
                )
            results[case] = entry
        _dump(output_dir / f"results-{version}.json", results)
        provenance_versions[version] = {
            "commit": VERSIONS[version]["commit"],
            "checkout_clean": True,
            "fastmcp_used": fastmcp_origin,
            "runtime": _package_versions(pythons[version]),
        }

    provenance = {
        "task": "HAMCP-129",
        "generator": {
            "path": "tests/fixtures/hamcp129/generate.py",
            "sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        },
        "upstream": provenance_versions,
        "integration_list_path": integration_paths,
        "statement": (
            "All data is synthetic and was served offline by a loopback fake Home "
            "Assistant Core/Supervisor peer defined in generate.py "
            f"(http://{HOST}:{PORT}); a worker-process socket guard refused every "
            "non-loopback resolution and connection. No live Home Assistant, "
            "Supervisor, deployed MCP endpoint, credential, or secret was used. "
            "Each result is the exact structured_content returned by the pinned "
            "upstream tool through a bare FastMCP instance with only that tool "
            "group's upstream registration function applied (ha-mcp server "
            "middleware not installed). Outputs were written by the generator "
            "and were not hand-edited."
        ),
        "notes": [
            "requests are sorted by their canonical JSON form for determinism; "
            "order is not temporal and connections are not distinguished.",
            "WebSocket auth tokens and HTTP Authorization headers are recorded "
            "as labels (synthetic-core-token / synthetic-supervisor-token).",
            "mode=addon: SUPERVISOR_TOKEN set, Supervisor REST at "
            f"{SUPERVISOR_URL}, HA at {HA_URL}; the self-update check is enabled "
            "and served by the fake Supervisor /addons/self/info.",
            "mode=core_ws_proxy: no SUPERVISOR_TOKEN; Supervisor reached via "
            "Core 'supervisor/api' WebSocket command; HA_MCP_DISABLE_UPDATE_CHECK=1.",
            "The ha-mcp custom component is absent: 'ha_mcp_tools/info' answers "
            "unknown_command, so component fast paths fall back to legacy reads.",
            "system_health/info follows Home Assistant Core 2026.10.0 framing: a "
            "null result, then an 'initial' event, an 'update' event, and 'finish'.",
        ],
    }
    _dump(output_dir / "provenance.json", provenance)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--source-843")
    parser.add_argument("--source-850")
    parser.add_argument("--source-860")
    parser.add_argument("--python-843", default=sys.executable)
    parser.add_argument("--python-850", default=sys.executable)
    parser.add_argument("--python-860", default=sys.executable)
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", help=argparse.SUPPRESS)
    parser.add_argument("--source", help=argparse.SUPPRESS)
    parser.add_argument("--case", help=argparse.SUPPRESS)
    parser.add_argument("--result-file", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker_main(args)
    if not (args.source_843 and args.source_850 and args.source_860):
        parser.error("--source-843, --source-850 and --source-860 are required")
    return orchestrate(args)


if __name__ == "__main__":
    raise SystemExit(main())
