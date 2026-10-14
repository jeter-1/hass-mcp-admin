"""HAMCP-129 platform health, installed-app and config-entry read adapters.

Each public tool wraps one mixed upstream tool. Callers never supply an
upstream argument: Engineering builds a fixed argument shape from a closed
schema, filters in Engineering, and projects every result through a positive
allowlist before the gateway's sanitizer. Unlisted keys are dropped; a listed
key with the wrong JSON type fails the whole call.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import re
from typing import Any

HEALTH = "ha_get_system_health"
APP = "ha_get_app"
INTEGRATION = "ha_get_integration"
_SOURCES = {
    "8.4.3": "eac7a3aa7063432e9af17e7d7726040e909c7b8f",
    "8.5.0": "311d6dc273fb4e9a5b8cde0de15f69472a64fe44",
    "8.6.0": "fc54437a804858732e4bc927add98e202d879a09",
}
_KINDS = {HEALTH: "platform-health", APP: "platform-apps", INTEGRATION: "platform-integrations"}
_FINGERPRINTS = {
    (HEALTH, "8.4.3"): "0c51da5b9c5251dd16d134658f38a65feadb18801791865c736cea1ffc0cd623",
    (HEALTH, "8.5.0"): "0c51da5b9c5251dd16d134658f38a65feadb18801791865c736cea1ffc0cd623",
    (HEALTH, "8.6.0"): "569e6a4dc9c6e7b18d53b2704f2c8db0d2cbd2036e64514774021b0b75e4cd76",
    (APP, "8.4.3"): "71bbe1fb1fc60e97b4d8b08361de2f9dda5de6868797613e1011fc8e10a4d28e",
    (APP, "8.5.0"): "c76674086c0d6f48e85ec8d6f4ef4ee9eff5b582c2795e76e43327cdc2b8f7fe",
    (APP, "8.6.0"): "7c1fba1746d03398144231b4e1fa133291a0182eb01b38ff7f66ef4121298a60",
    (INTEGRATION, "8.4.3"): "5dd76ac034af33a238434633859a7f717eaf5b9d55c557d6b284881ec0804686",
    (INTEGRATION, "8.5.0"): "5dd76ac034af33a238434633859a7f717eaf5b9d55c557d6b284881ec0804686",
    (INTEGRATION, "8.6.0"): "798a5110a0e7717c3db87c8ff231103cdba9e887c35001164f850cf13887cacf",
}
# token -> (tool, version, source commit, exact policy input-schema fingerprint)
ADAPTER_BINDINGS = {
    f"ha-mcp-{version}-{_KINDS[tool]}-v1": (tool, version, _SOURCES[version], fingerprint)
    for (tool, version), fingerprint in _FINGERPRINTS.items()
}

SECTIONS = (
    "repairs", "zha_network", "zwave_network", "thread_network",
    "matter_network", "dead_entities", "themes",
)
_EXCLUDED_KEYS = frozenset({"diagnostics", "config_check", "zha_network_full"})
ENTRY_STATES = (
    "loaded", "setup_error", "migration_error", "setup_retry", "not_loaded",
    "failed_unload", "setup_in_progress", "unload_in_progress",
)
_SLUG = r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}$"
_DOMAIN = r"^[a-z0-9_]{1,64}$"
_ENTRY_ID = r"^[A-Za-z0-9]{1,64}$"
# 1-64 characters, no control characters, no leading or trailing whitespace.
_QUERY = (
    "^[^\\u0000-\\u0020\\u007f-\\u009f]"
    "(?:[^\\u0000-\\u001f\\u007f-\\u009f]{0,62}[^\\u0000-\\u0020\\u007f-\\u009f])?$"
)

PUBLIC_SCHEMAS: dict[str, dict[str, Any]] = {
    HEALTH: {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "sections": {
                "type": "array",
                "items": {"type": "string", "enum": list(SECTIONS)},
                "uniqueItems": True,
                "maxItems": len(SECTIONS),
                "default": [],
                "description": "Optional sections in addition to the platform baseline.",
            },
            "include_dismissed_repairs": {
                "type": "boolean",
                "default": False,
                "description": "Include dismissed repairs. Requires the repairs section.",
            },
        },
    },
    APP: {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "slug": {
                "anyOf": [{"type": "string", "pattern": _SLUG}, {"type": "null"}],
                "default": None,
                "description": "Return only this installed app (filtered by Engineering).",
            },
            "include_stats": {
                "type": "boolean",
                "default": False,
                "description": "Include CPU and memory statistics for started apps (at most 32 started).",
            },
        },
    },
    INTEGRATION: {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "domain": {
                "anyOf": [{"type": "string", "pattern": _DOMAIN}, {"type": "null"}],
                "default": None,
                "description": "Exact integration domain.",
            },
            "entry_id": {
                "anyOf": [{"type": "string", "pattern": _ENTRY_ID}, {"type": "null"}],
                "default": None,
                "description": "One config entry. Cannot be combined with other filters or paging.",
            },
            "query": {
                "anyOf": [{"type": "string", "pattern": _QUERY}, {"type": "null"}],
                "default": None,
                "description": "Case-insensitive substring of the domain or title.",
            },
            "state": {
                "anyOf": [{"type": "string", "enum": list(ENTRY_STATES)}, {"type": "null"}],
                "default": None,
                "description": "Exact config-entry state.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
            "offset": {"type": "integer", "minimum": 0, "maximum": 200, "default": 0},
        },
    },
}

STATS_STARTED_CAP = 32
CAPS = {
    "repairs": 50, "zha_devices": 50, "zwave_nodes": 50, "border_routers": 16,
    "dead_entities": 50, "themes": 64, "other_domains": 64, "apps": 100,
}
UPSTREAM_INTEGRATION_WINDOW = 200
STRING_CAP = 200
TITLE_CAP = 120
EMAIL_MASK = "[email]"
# Any token with non-empty text on both sides of "@" is treated as an e-mail
# address. Over-masking is deliberate; the mask runs before length capping so
# a cut cannot leave a partial address.
_EMAIL_LIKE = re.compile(r"[^\s@]+@[^\s@]+")


class StatsCapExceeded(ValueError):
    """More started apps than the reviewed stats fan-out allows."""


class PlatformResourceNotFound(LookupError):
    """The requested slug or entry is absent from the reviewed listing."""

    def __init__(self, details: dict[str, Any]):
        super().__init__("platform_resource_not_found")
        self.details = details


def binding(entry) -> tuple[str, str, str, str] | None:
    restrictions = getattr(entry, "argument_restrictions", ())
    return ADAPTER_BINDINGS.get(restrictions[0]) if len(restrictions) == 1 else None


def is_platform_adapter(entry) -> bool:
    bound = binding(entry)
    return (
        bound is not None
        and entry.upstream_name == bound[0]
        and entry.exposed_name == bound[0]
        and entry.classification == "mixed_or_requires_wrapper"
        and entry.input_schema_fingerprint == bound[3]
        and entry.reviewed_annotations.read_only
        and not entry.reviewed_annotations.destructive
    )


def adapter_token(entry) -> str:
    return entry.argument_restrictions[0]


PLATFORM_READ_BINDING_ABSENT = "platform_read_binding_absent"


def withhold_unbound_platform_reads(policy, version: str):
    """Return the policy as reusable for ``version`` and the withheld wrappers.

    The adapters are bound to exact upstream source. When ``version`` is not
    the bound version (signed reuse of another release's policy), only those
    entries revert to the unwrapped mixed upstream tool, which is never
    exposed. Every other entry is returned unchanged.
    """
    withheld = tuple(sorted(
        entry.upstream_name
        for entry in policy.tools
        if is_platform_adapter(entry) and binding(entry)[1] != version
    ))
    if not withheld:
        return policy, ()
    tools = tuple(
        replace(entry, argument_restrictions=())
        if entry.upstream_name in withheld else entry
        for entry in policy.tools
    )
    return replace(policy, tools=tools), withheld


def public_schema(entry, observed: dict) -> dict:
    return deepcopy(PUBLIC_SCHEMAS[entry.upstream_name] if is_platform_adapter(entry) else observed)


def _bool(arguments: dict, name: str) -> bool:
    value = arguments.get(name, False)
    if type(value) is not bool:
        raise ValueError(f"platform_{name}_invalid")
    return value


def _optional(arguments: dict, name: str, pattern: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError(f"platform_{name}_invalid")
    return value


def _int(arguments: dict, name: str, default: int, low: int, high: int) -> int:
    value = arguments.get(name, default)
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"platform_{name}_invalid")
    return value


def normalized_arguments(tool: str, arguments: dict) -> dict[str, Any]:
    """Validate the public arguments independently of the published schema."""

    if not isinstance(arguments, dict) or set(arguments) - set(PUBLIC_SCHEMAS[tool]["properties"]):
        raise ValueError("platform_arguments_invalid")
    if tool == HEALTH:
        sections = arguments.get("sections", [])
        if (
            not isinstance(sections, list)
            or len(sections) > len(SECTIONS)
            or any(not isinstance(item, str) or item not in SECTIONS for item in sections)
            or len(set(sections)) != len(sections)
        ):
            raise ValueError("platform_sections_invalid")
        dismissed = _bool(arguments, "include_dismissed_repairs")
        if dismissed and "repairs" not in sections:
            raise ValueError("platform_include_dismissed_repairs_requires_repairs")
        return {"sections": sorted(sections), "include_dismissed_repairs": dismissed}
    if tool == APP:
        return {
            "slug": _optional(arguments, "slug", _SLUG),
            "include_stats": _bool(arguments, "include_stats"),
        }
    values = {
        "domain": _optional(arguments, "domain", _DOMAIN),
        "entry_id": _optional(arguments, "entry_id", _ENTRY_ID),
        "query": _optional(arguments, "query", _QUERY),
        "state": arguments.get("state"),
        "limit": _int(arguments, "limit", 50, 1, 100),
        "offset": _int(arguments, "offset", 0, 0, 200),
    }
    if values["state"] is not None and values["state"] not in ENTRY_STATES:
        raise ValueError("platform_state_invalid")
    if values["entry_id"] is not None and any(
        arguments.get(name) is not None
        for name in ("domain", "query", "state", "limit", "offset")
    ):
        raise ValueError("platform_entry_id_exclusive")
    return values


def read_arguments(entry, arguments: dict, *, stats_precheck: bool = False) -> dict[str, Any]:
    """Build the only upstream argument shape this tool may send."""

    if not is_platform_adapter(entry):
        raise ValueError("platform_adapter_unbound")
    tool = entry.upstream_name
    values = normalized_arguments(tool, arguments)
    if tool == HEALTH:
        if not values["sections"]:
            return {}
        built: dict[str, Any] = {"include": ",".join(values["sections"])}
        if values["include_dismissed_repairs"]:
            built["include_dismissed_repairs"] = True
        return built
    if tool == APP:
        return {
            "source": "installed",
            "include_stats": values["include_stats"] and not stats_precheck,
        }
    built = {
        "limit": UPSTREAM_INTEGRATION_WINDOW,
        "offset": 0,
        "exact_match": True,
        "include_options": False,
    }
    prefilter = values["domain"] or values["query"]
    if prefilter is not None:
        built["query"] = prefilter
    return built


def stats_precheck_required(entry, arguments: dict) -> bool:
    return (
        is_platform_adapter(entry)
        and entry.upstream_name == APP
        and normalized_arguments(APP, arguments)["include_stats"]
    )


def _started_count(payload: Any) -> int:
    addons = payload.get("addons") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("success") is not True or not isinstance(addons, list):
        raise ValueError("platform_app_listing_invalid")
    if any(not isinstance(item, dict) for item in addons):
        raise ValueError("platform_app_listing_invalid")
    return sum(1 for item in addons if item.get("state") == "started")


def check_stats_precheck(payload: Any) -> None:
    """Refuse the stats call when the stats-free snapshot is over the cap.

    This is a sampled bound. Upstream re-lists before its stats fan-out, so it
    cannot stop an app started after this snapshot from being fanned out.
    """

    if _started_count(payload) > STATS_STARTED_CAP:
        raise StatsCapExceeded("platform_stats_started_cap_exceeded")


# --- projection helpers -----------------------------------------------------

_NONE = type(None)
_STR = (str,)
_STR_N = (str, _NONE)
_BOOL = (bool,)
_BOOL_N = (bool, _NONE)
_NUM_N = (int, float, _NONE)
_INT_N = (int, _NONE)
_SCALAR = (str, int, float, bool, _NONE)


class _Projection:
    def __init__(self) -> None:
        self.reasons: set[str] = set()

    def text(self, value: str, cap: int = STRING_CAP, *, mask_email: bool = False) -> str:
        if mask_email:
            masked = _EMAIL_LIKE.sub(EMAIL_MASK, value)
            if masked != value:
                self.reasons.add("email_masked")
            value = masked
        if len(value) > cap:
            self.reasons.add("string_truncated")
            value = value[:cap]
        return value

    def value(self, value: Any, allowed: tuple[type, ...], where: str, **text) -> Any:
        if isinstance(value, bool) and bool not in allowed:
            raise ValueError(f"platform_field_type_invalid:{where}")
        if not isinstance(value, allowed):
            raise ValueError(f"platform_field_type_invalid:{where}")
        if isinstance(value, float) and value != value:
            raise ValueError(f"platform_field_type_invalid:{where}")
        return self.text(value, **text) if isinstance(value, str) else value

    def pick(self, source: Any, spec: dict[str, tuple[type, ...]], where: str,
             *, required: tuple[str, ...] = (), text: dict[str, dict] | None = None) -> dict[str, Any]:
        if not isinstance(source, dict):
            raise ValueError(f"platform_object_invalid:{where}")
        missing = [name for name in required if name not in source]
        if missing:
            raise ValueError(f"platform_field_missing:{where}")
        text = text or {}
        return {
            name: self.value(source[name], allowed, f"{where}.{name}", **text.get(name, {}))
            for name, allowed in spec.items()
            if name in source
        }

    def items(self, source: Any, where: str) -> list[Any]:
        if not isinstance(source, list):
            raise ValueError(f"platform_list_invalid:{where}")
        return source

    def count(self, value: Any, where: str, *, minimum: int = 0) -> int:
        if type(value) is not int or value < minimum:
            raise ValueError(f"platform_count_invalid:{where}")
        return value

    def bounded(self, items: list[Any], cap: int, total: int, reason: str) -> dict[str, Any]:
        kept = items[:cap]
        truncated = total > len(kept)
        if truncated:
            self.reasons.add(reason)
        return {"count": len(kept), "total_count": total, "truncated": truncated, "items": kept}

    def completeness(self) -> dict[str, Any]:
        reasons = sorted(self.reasons - {"email_masked"})
        return {"status": "partial" if reasons else "complete", "reasons": reasons}


# --- system health ------------------------------------------------------------

_PLATFORM_KEYS = {
    "homeassistant": (
        "version", "installation_type", "dev", "hassio", "docker", "virtualenv",
        "python_version", "os_name", "os_version", "arch",
    ),
    "hassio": (
        "host_os", "update_channel", "supervisor_version", "agent_version",
        "docker_version", "disk_total", "disk_used", "disk_life_time", "healthy",
        "supported", "virtualization", "board", "ntp_synchronized",
        "host_connectivity", "supervisor_connectivity", "supervisor_api", "version_api",
    ),
    "cloud": (
        "logged_in", "relayer_connected", "remote_enabled", "remote_connected",
        "alexa_enabled", "google_enabled", "certificate_status", "iot_state",
        "can_reach_cert_server", "can_reach_cloud_auth", "can_reach_cloud",
    ),
}
_SECTION_KEYS = frozenset(SECTIONS) | _EXCLUDED_KEYS
_REPAIR_ISSUE = {
    "domain": _STR_N, "issue_domain": _STR_N, "issue_id": _STR_N, "severity": _STR_N,
    "is_fixable": _BOOL_N, "translation_key": _STR_N, "breaks_in_ha_version": _STR_N,
    "created": _STR_N, "ignored": _BOOL_N, "dismissed_version": _STR_N,
}
_ZHA_DEVICE = {
    "ieee": _STR_N, "name": _STR_N, "manufacturer": _STR_N, "model": _STR_N,
    "lqi": _NUM_N, "rssi": _NUM_N, "available": _BOOL_N,
}
_ZWAVE_CONTROLLER = {
    name: _SCALAR for name in (
        "home_id", "type", "own_node_id", "is_primary", "is_suc", "sdk_version",
        "firmware_version", "status", "inclusion_state", "rf_region",
        "is_rebuilding_routes", "supports_long_range",
    )
}
_ZWAVE_NODE = {
    "node_id": _INT_N, "status": _SCALAR, "is_routing": _BOOL_N, "is_secure": _SCALAR,
    "zwave_plus_version": _INT_N, "is_controller_node": _BOOL_N,
}
_BORDER_ROUTER = {
    "extended_address": _STR, "channel": _INT_N, "extended_pan_id": _STR_N,
    "border_agent_id": _STR_N,
}
_MATTER = {"config_entry_id": _STR_N, "state": _STR_N, "title": _STR_N}
_DEAD_ITEM = {
    "entity_id": _STR_N, "platform": _STR_N, "config_entry_id": _STR_N,
    "disabled_by": _STR_N, "has_state": _BOOL_N,
}


def _platform_value(p: _Projection, value: Any, where: str) -> Any:
    if isinstance(value, dict):
        kind = value.get("type")
        if not isinstance(kind, str):
            raise ValueError(f"platform_field_type_invalid:{where}")
        # Typed Core values never carry their text: an error message, date or
        # pending marker is reduced to a fixed status.
        return kind if kind in {"pending", "failed"} else "ok"
    return p.value(value, _SCALAR, where)


def _project_platform(p: _Projection, health_info: Any, baseline: bool) -> dict[str, Any]:
    if not isinstance(health_info, dict):
        raise ValueError("platform_health_info_invalid")
    if not baseline:
        if health_info:
            raise ValueError("platform_health_info_invalid")
        p.reasons.add("baseline_unavailable")
        return {}
    data = health_info.get("data")
    if health_info.get("type") != "initial" or not isinstance(data, dict):
        raise ValueError("platform_health_info_invalid")
    platform: dict[str, Any] = {}
    other: list[str] = []
    for domain in sorted(data):
        block = data[domain]
        info = block.get("info") if isinstance(block, dict) else None
        if not isinstance(domain, str) or not isinstance(info, dict):
            raise ValueError("platform_health_domain_invalid")
        if domain not in _PLATFORM_KEYS:
            other.append(p.text(domain, 64))
            continue
        platform[domain] = {
            key: _platform_value(p, info[key], f"platform.{domain}.{key}")
            for key in _PLATFORM_KEYS[domain]
            if key in info
        }
    bounded = p.bounded(other, CAPS["other_domains"], len(other), "other_domains_truncated")
    platform["other_domains"] = bounded["items"]
    platform["other_domains_total_count"] = bounded["total_count"]
    platform["other_domains_truncated"] = bounded["truncated"]
    return platform


def _project_section(p: _Projection, name: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"platform_section_invalid:{name}")
    if "error" in raw:
        if not isinstance(raw["error"], str):
            raise ValueError(f"platform_section_invalid:{name}")
        p.reasons.add(f"section_unavailable:{name}")
        return {"error": "section_unavailable"}
    if name == "repairs":
        issues = p.items(raw.get("issues"), name)
        total = len(issues)
        bounded = p.bounded(issues, CAPS["repairs"], total, "repairs_truncated")
        out = {
            "count": bounded["count"], "total_count": total, "truncated": bounded["truncated"],
            "issues": [p.pick(item, _REPAIR_ISSUE, "repairs.issue") for item in bounded["items"]],
        }
        if "dismissed_count" in raw:
            out["dismissed_count"] = p.count(raw["dismissed_count"], "repairs.dismissed_count")
        return out
    if name == "zha_network":
        devices = p.items(raw.get("devices"), name)
        total = p.count(raw.get("total_count", len(devices)), name, minimum=len(devices))
        bounded = p.bounded(devices, CAPS["zha_devices"], total, "zha_network_truncated")
        return {
            "count": bounded["count"], "total_count": total, "truncated": bounded["truncated"],
            "devices": [p.pick(item, _ZHA_DEVICE, "zha_network.device") for item in bounded["items"]],
        }
    if name == "zwave_network":
        nodes = p.items(raw.get("nodes"), name)
        total = p.count(raw.get("total_count", len(nodes)), name, minimum=len(nodes))
        bounded = p.bounded(nodes, CAPS["zwave_nodes"], total, "zwave_network_truncated")
        return {
            "count": bounded["count"], "total_count": total, "truncated": bounded["truncated"],
            "controller": p.pick(raw.get("controller", {}), _ZWAVE_CONTROLLER, "zwave_network.controller"),
            "nodes": [p.pick(item, _ZWAVE_NODE, "zwave_network.node") for item in bounded["items"]],
        }
    if name == "thread_network":
        routers = p.items(raw.get("border_routers"), name)
        bounded = p.bounded(routers, CAPS["border_routers"], len(routers), "thread_network_truncated")
        return {
            "count": bounded["count"], "total_count": len(routers), "truncated": bounded["truncated"],
            "border_routers": [
                p.pick(item, _BORDER_ROUTER, "thread_network.border_router", required=("extended_address",))
                for item in bounded["items"]
            ],
        }
    if name == "matter_network":
        return p.pick(raw, _MATTER, name, text={"title": {"cap": TITLE_CAP, "mask_email": True}})
    if name == "themes":
        names = p.items(raw.get("themes"), name)
        bounded = p.bounded(names, CAPS["themes"], len(names), "themes_truncated")
        return {
            "count": bounded["count"], "total_count": len(names), "truncated": bounded["truncated"],
            "themes": [p.value(item, _STR, "themes.theme") for item in bounded["items"]],
            **p.pick(raw, {"default_theme": _STR_N, "default_dark_theme": _STR_N}, name),
        }
    # dead_entities
    out: dict[str, Any] = {
        "summary": p.pick(raw.get("summary"), {"candidate_total": (int,), "registry_total": (int,)},
                          "dead_entities.summary", required=("candidate_total", "registry_total")),
    }
    for bucket in ("config_entry_orphans", "stale_restored"):
        value = raw.get(bucket)
        if not isinstance(value, dict):
            raise ValueError(f"platform_section_invalid:{name}")
        items = p.items(value.get("items"), f"{name}.{bucket}")
        total = p.count(value.get("total_count", len(items)), bucket, minimum=len(items))
        bounded = p.bounded(items, CAPS["dead_entities"], total, f"dead_entities_truncated:{bucket}")
        out[bucket] = {
            "count": bounded["count"], "total_count": total, "truncated": bounded["truncated"],
            "items": [p.pick(item, _DEAD_ITEM, f"{name}.{bucket}.item") for item in bounded["items"]],
        }
    return out


def _project_health(payload: dict, values: dict) -> dict[str, Any]:
    p = _Projection()
    baseline = payload.get("baseline_available", True)
    if type(baseline) is not bool:
        raise ValueError("platform_baseline_invalid")
    requested = set(values["sections"])
    present = _SECTION_KEYS & set(payload)
    if present != requested:
        raise ValueError("platform_sections_mismatch")
    platform = _project_platform(p, payload.get("health_info"), baseline)
    sections = {name: _project_section(p, name, payload[name]) for name in values["sections"]}
    warnings = []
    if not baseline:
        warnings.append("baseline_unavailable")
    if payload.get("warnings"):
        warnings.append("upstream_warnings_withheld")
    return {
        "success": True,
        "baseline_available": baseline,
        "platform": platform,
        "sections": sections,
        "warnings": warnings,
        "completeness": p.completeness(),
    }


# --- installed apps -------------------------------------------------------------

_APP = {
    "slug": _STR, "name": _STR_N, "version": _STR_N, "state": _STR_N, "installed": _BOOL,
    "update_available": _BOOL_N, "repository": _STR_N, "description": _STR_N,
}
_STATS = {"cpu_percent": _NUM_N, "memory_percent": _NUM_N, "memory_usage": _NUM_N, "memory_limit": _NUM_N}


def _project_apps(payload: dict, values: dict) -> dict[str, Any]:
    p = _Projection()
    addons = p.items(payload.get("addons"), "addons")
    started = _started_count(payload)
    if values["include_stats"] and started > STATS_STARTED_CAP:
        # The listing changed between the pre-check and the stats call.
        raise StatsCapExceeded("platform_stats_started_cap_exceeded")
    apps = []
    unavailable = set()
    for raw in addons:
        app = p.pick(raw, _APP, "app", required=("slug",))
        if not re.fullmatch(_SLUG, raw["slug"]):
            raise ValueError("platform_app_slug_invalid")
        if values["include_stats"]:
            # Upstream collects stats only for started apps and reports a
            # failed or timed-out collection as null. A started app without
            # stats is requested data that is unavailable, never complete.
            stats = raw.get("stats")
            app["stats"] = None if stats is None else p.pick(stats, _STATS, "app.stats")
            if stats is None and raw.get("state") == "started":
                unavailable.add(raw["slug"])
        apps.append(app)
    summary = {
        "total_installed": len(apps),
        "running": started,
        "stopped": len(apps) - started,
        "updates_available": sum(1 for app in apps if app.get("update_available") is True),
    }
    if values["slug"] is not None:
        apps = [app for app in apps if app["slug"] == values["slug"]]
        if not apps:
            raise PlatformResourceNotFound({"completeness": "complete", "completeness_reasons": []})
    bounded = p.bounded(apps, CAPS["apps"], len(apps), "apps_truncated")
    warnings = [
        f"stats_unavailable:{app['slug']}"
        for app in bounded["items"]
        if app["slug"] in unavailable
    ]
    if warnings:
        p.reasons.add("stats_unavailable")
    if payload.get("warnings") and not warnings:
        warnings.append("upstream_warnings_withheld")
    return {
        "success": True,
        "count": bounded["count"],
        "total_count": bounded["total_count"],
        "truncated": bounded["truncated"],
        "apps": bounded["items"],
        "summary": summary,
        "warnings": sorted(warnings),
        "completeness": p.completeness(),
    }


# --- config entries ---------------------------------------------------------------

_ENTRY = {
    "entry_id": _STR, "domain": _STR, "title": _STR_N, "state": _STR_N, "source": _STR_N,
    "disabled_by": _STR_N, "supports_options": _BOOL_N, "supports_unload": _BOOL_N,
    "supports_reconfigure": _BOOL_N, "log_level": _STR_N,
    "pref_disable_new_entities": _BOOL_N, "pref_disable_polling": _BOOL_N,
}


def _project_integrations(payload: dict, values: dict) -> dict[str, Any]:
    p = _Projection()
    rows = p.items(payload.get("entries"), "entries")
    has_more = payload.get("has_more")
    if type(has_more) is not bool or len(rows) > UPSTREAM_INTEGRATION_WINDOW:
        raise ValueError("platform_integration_window_invalid")
    entries = []
    for row in rows:
        if isinstance(row, dict) and "options" in row:
            # include_options is forced false; options must never be present.
            raise ValueError("platform_integration_options_present")
        entries.append(p.pick(
            row, _ENTRY, "entry", required=("entry_id", "domain"),
            text={"title": {"cap": TITLE_CAP, "mask_email": True}},
        ))
    if has_more:
        p.reasons.add("upstream_window_exceeded")
    if values["domain"] is not None:
        entries = [entry for entry in entries if entry["domain"] == values["domain"]]
    if values["query"] is not None:
        needle = values["query"].strip().lower()
        entries = [
            entry for entry in entries
            if needle in entry["domain"].lower() or needle in (entry.get("title") or "").lower()
        ]
    if values["state"] is not None:
        entries = [entry for entry in entries if entry.get("state") == values["state"]]
    if values["entry_id"] is not None:
        entries = [entry for entry in entries if entry["entry_id"] == values["entry_id"]]
        if not entries:
            # Outside a truncated upstream window absence is not definitive.
            raise PlatformResourceNotFound(
                {"completeness": "partial", "completeness_reasons": ["upstream_window_exceeded"]}
                if has_more else {"completeness": "complete", "completeness_reasons": []}
            )
        offset, limit = 0, 1
    else:
        offset, limit = values["offset"], values["limit"]
    summary: dict[str, int] = {}
    for entry in entries:
        key = entry.get("state") if isinstance(entry.get("state"), str) else "unknown"
        summary[key] = summary.get(key, 0) + 1
    page = entries[offset:offset + limit]
    more = offset + len(page) < len(entries)
    return {
        "success": True,
        "total_count": len(entries),
        "offset": offset,
        "limit": limit,
        "count": len(page),
        "has_more": more,
        "next_offset": offset + len(page) if more else None,
        "entries": page,
        "state_summary": dict(sorted(summary.items())),
        "filters": {
            name: values[name] for name in ("domain", "entry_id", "query", "state")
            if values[name] is not None
        },
        "warnings": ["upstream_warnings_withheld"] if payload.get("warnings") else [],
        "completeness": p.completeness(),
    }


_TOP_LEVEL_LISTS = {HEALTH: "health_info", APP: "addons", INTEGRATION: "entries"}


def validate_result(entry, payload: Any) -> None:
    """Reject a failed, out-of-scope or wrongly shaped upstream result."""

    if not is_platform_adapter(entry):
        raise ValueError("platform_adapter_unbound")
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("platform_response_invalid")
    if _EXCLUDED_KEYS & set(payload) or "options" in payload:
        raise ValueError("platform_response_excluded_key")
    container = payload.get(_TOP_LEVEL_LISTS[entry.upstream_name])
    if not isinstance(container, dict if entry.upstream_name == HEALTH else list):
        raise ValueError("platform_response_shape_invalid")


def project_result(entry, payload: Any, arguments: dict) -> dict[str, Any]:
    if not is_platform_adapter(entry):
        raise ValueError("platform_adapter_unbound")
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("platform_response_invalid")
    tool = entry.upstream_name
    values = normalized_arguments(tool, arguments)
    if tool == HEALTH:
        return _project_health(payload, values)
    if tool == APP:
        return _project_apps(payload, values)
    return _project_integrations(payload, values)


def completeness(payload: Any) -> tuple[bool, list[str]]:
    state = payload.get("completeness") if isinstance(payload, dict) else None
    if not isinstance(state, dict) or state.get("status") != "partial":
        return False, []
    reasons = ", ".join(state.get("reasons") or ())
    return True, [f"The reviewed platform read is partial: {reasons}."]
