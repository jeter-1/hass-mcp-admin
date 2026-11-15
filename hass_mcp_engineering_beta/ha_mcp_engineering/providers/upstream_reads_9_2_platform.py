"""9.2.0 platform reads: default-withhold counts and warning rule (HAMCP-371 D5).

The shared ``upstream_platform_reads.py`` already returns a closed rebuild for
``ha_get_system_health``, ``ha_get_app`` and ``ha_get_integration``: it names
each returned key, and every other upstream key is dropped without a count. On
ha-mcp 9.2.0 only, one gate in ``project_result`` calls ``project`` (the D5
ruling of 2026-11-15 13:25), which keeps the shipped rebuild and adds what the
S10 engine (``upstream_reads_9_2_keys``) does for the other 9.2.0 reads:

- ``fields_withheld`` counts the distinct upstream names outside ``KNOWN``,
  the contract's admitted, consumed-structure and recomputed keys
  (``tests/fixtures/ha_mcp_9_2_0/output-key-contract.json``). A rebuild has no
  open container, so every name is walked. On the health read each key of a
  ``health_info.data.<domain>.info`` block is named ``<domain>.<key>`` and its
  value is not walked; the domain names are data. ``info`` keys the rebuild
  does not return are therefore withheld and counted, never dropped silently.
  ``fields_dropped_by_policy`` is 0: the rebuild has no policy drops. Both
  counts are capped at 999.
- No upstream warning text is reviewed on these reads. Every upstream warning
  is replaced: the shipped ``upstream_warnings_withheld`` code becomes
  ``upstream_warning_replaced``, ``upstream_warnings_replaced`` counts per
  occurrence, and the read becomes partial with the reason
  ``upstream_warnings_replaced``. Engineering's own codes
  (``baseline_unavailable``, ``stats_unavailable:<slug>``) are kept.

The ``ha_get_app`` projection has no successful 9.2.0 receipt (no Supervisor in
the local lane, 05:35 ruling); its tests use a synthetic payload.
"""
from __future__ import annotations

from typing import Any

from .upstream_reads_9_2_keys import COUNT_CAP, REPLACED_WARNING

RELEASES = frozenset({"9.2.0"})

HEALTH = "ha_get_system_health"
APP = "ha_get_app"
INTEGRATION = "ha_get_integration"

SHIPPED_WITHHELD = "upstream_warnings_withheld"
REPLACED_REASON = "upstream_warnings_replaced"

# (outer, map, inner): each key of ``inner`` under a data key ``d`` of
# ``outer.map`` is named ``d.key``.
QUALIFY = {HEALTH: ("health_info", "data", "info")}

# The contract's admitted | consumed_structure | recomputed_by_engineering
# (test-bound in tests/test_hamcp371_platform_9_2.py).
KNOWN = {
    HEALTH: frozenset({
        "available", "baseline_available", "border_agent_id", "border_routers",
        "breaks_in_ha_version", "candidate_total", "channel",
        "cloud.alexa_enabled", "cloud.can_reach_cert_server", "cloud.can_reach_cloud",
        "cloud.can_reach_cloud_auth", "cloud.certificate_status", "cloud.google_enabled",
        "cloud.iot_state", "cloud.logged_in", "cloud.relayer_connected",
        "cloud.remote_connected", "cloud.remote_enabled", "config_entry_id",
        "config_entry_orphans", "controller", "created", "data", "dead_entities",
        "default_dark_theme", "default_theme", "devices", "disabled_by",
        "dismissed_count", "dismissed_version", "domain", "entity_id", "error",
        "extended_address", "extended_pan_id", "firmware_version", "has_state",
        "hassio.agent_version", "hassio.board", "hassio.disk_life_time",
        "hassio.disk_total", "hassio.disk_used", "hassio.docker_version",
        "hassio.healthy", "hassio.host_connectivity", "hassio.host_os",
        "hassio.ntp_synchronized", "hassio.supervisor_api",
        "hassio.supervisor_connectivity", "hassio.supervisor_version",
        "hassio.supported", "hassio.update_channel", "hassio.version_api",
        "hassio.virtualization", "health_info", "home_id", "homeassistant.arch",
        "homeassistant.dev", "homeassistant.docker", "homeassistant.hassio",
        "homeassistant.installation_type", "homeassistant.os_name",
        "homeassistant.os_version", "homeassistant.python_version",
        "homeassistant.version", "homeassistant.virtualenv", "ieee", "ignored",
        "inclusion_state", "info", "is_controller_node", "is_fixable", "is_primary",
        "is_rebuilding_routes", "is_routing", "is_secure", "is_suc", "issue_domain",
        "issue_id", "issues", "items", "lqi", "manufacturer", "matter_network",
        "model", "name", "node_id", "nodes", "own_node_id", "platform",
        "registry_total", "repairs", "rf_region", "rssi", "sdk_version", "severity",
        "stale_restored", "state", "status", "success", "summary",
        "supports_long_range", "themes", "thread_network", "title", "total_count",
        "translation_key", "type", "warnings", "zha_network", "zwave_network",
        "zwave_plus_version",
    }),
    APP: frozenset({
        "addons", "cpu_percent", "description", "installed", "memory_limit",
        "memory_percent", "memory_usage", "name", "repository", "slug", "state",
        "stats", "success", "update_available", "version", "warnings",
    }),
    INTEGRATION: frozenset({
        "count", "disabled_by", "domain", "entries", "entry_id", "has_more", "limit",
        "log_level", "next_offset", "offset", "pref_disable_new_entities",
        "pref_disable_polling", "query", "source", "state", "state_summary",
        "success", "supports_options", "supports_reconfigure", "supports_unload",
        "title", "total_count", "warnings",
    }),
}


def names(value: Any, tool: str) -> set[str]:
    """Distinct upstream key names, with the health ``info`` names qualified."""

    acc: set[str] = set()
    qualify = QUALIFY.get(tool)

    def walk(v: Any, outer: str | None = None) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                acc.add(k)
                if qualify and outer == qualify[0] and k == qualify[1] and isinstance(x, dict):
                    qualified(x)
                else:
                    walk(x, k)
        elif isinstance(v, list):
            for x in v:
                walk(x, outer)

    def qualified(v: dict) -> None:
        for data_key, block in v.items():
            if not isinstance(block, dict):
                walk(block)
                continue
            for k, x in block.items():
                acc.add(k)
                if k == qualify[2] and isinstance(x, dict):
                    acc.update(f"{data_key}.{name}" for name in x)
                else:
                    walk(x, k)

    walk(value)
    return acc


def _upstream_warnings(payload: dict) -> int:
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        return len(warnings)
    return 1 if warnings else 0


def project(tool: str, payload: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """The shipped rebuild, with the 9.2.0 counts and warning rule."""

    from . import upstream_platform_reads as shipped

    if tool == HEALTH:
        out = shipped._project_health(payload, values)
    elif tool == APP:
        out = shipped._project_apps(payload, values)
    else:
        out = shipped._project_integrations(payload, values)
    replaced = _upstream_warnings(payload)
    out["fields_withheld"] = min(len(names(payload, tool) - KNOWN[tool]), COUNT_CAP)
    out["fields_dropped_by_policy"] = 0
    out["upstream_warnings_replaced"] = min(replaced, COUNT_CAP)
    warnings = [code for code in out["warnings"] if code != SHIPPED_WITHHELD]
    if replaced:
        warnings.append(REPLACED_WARNING)
        reasons = set(out["completeness"]["reasons"]) | {REPLACED_REASON}
        out["completeness"] = {"status": "partial", "reasons": sorted(reasons)}
    out["warnings"] = sorted(warnings) if tool == APP else warnings
    return out
