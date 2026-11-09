"""Compiled 9.1.0 projections for ledger rows 13, 14 and 17-24 (HAMCP-319 section 7).

Each entry in ``PROJECTIONS`` maps an upstream tool name to
``(project(payload, arguments) -> payload, completeness(payload) -> (partial, warnings))``.
``project`` raises ``ValueError`` on an unrecognized shape (fail closed).
Warnings are fixed Engineering strings, never upstream text.

Source pin: ha-mcp 9.1.0 at d2e3f4b0647d815fb32d530fed15a6a99b1d4b33. Upstream
error results (``isError``) are classified by the gateway before projection,
so a projection only sees non-error payloads; a non-error payload that does
not report ``success: true`` in a recognized envelope is refused here rather
than returned as an empty success (design F3).
"""
from __future__ import annotations

from typing import Any

# Section 7.1: the single bundled skill (skill_loader.py:153).
_BEST_PRACTICES_SKILL = "home-assistant-best-practices"

# enforcement.py:130 _STATE_CONTENT_WARNING_PREFIX (9.1.0). Matching a pinned,
# version-compiled prefix classifies the omission; the upstream text is never
# echoed as an Engineering warning.
_STATE_VISIBILITY_PREFIX = "Entity Visibility enforce mode omitted "

UNVERIFIED = "The upstream result completeness could not be verified."
UPSTREAM_WARNINGS = (
    "The upstream provider reported warnings; the result may be incomplete."
)
UPSTREAM_PARTIAL = "The upstream provider reported partial coverage."
SEARCH_PARTIAL = "The upstream search reported partial coverage."
VISIBLE_SCOPE = (
    "Coverage is limited to entities visible to the upstream provider; "
    "entities hidden by its visibility policy are omitted without notice."
)
STATE_WITHHELD = (
    "Entity attributes or records were withheld by the upstream visibility "
    "policy; a withheld mapping is unavailable, not empty."
)
STATE_INCOMPLETE = "Some requested entity states could not be read."
ENTITY_INCOMPLETE = "Some requested entity registry entries could not be read."

# Row 17: blocks that describe the upstream server or Engineering's own
# internal client session rather than household data (tools_search.py:660-702).
_OVERVIEW_DROPPED = frozenset(
    {"mcp_client", "tool_discovery", "settings_url", "settings_url_hint"}
)

_SEARCH_BUCKETS = (
    "entities", "automations", "scripts", "scenes", "helpers", "dashboards",
)


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("invalid_response")


def _check_warnings(payload: dict) -> None:
    warnings = payload.get("warnings")
    if warnings is not None:
        _require(
            isinstance(warnings, list) and all(isinstance(w, str) for w in warnings)
        )


def _success_envelope(payload: Any) -> dict:
    _require(isinstance(payload, dict) and payload.get("success") is True)
    _check_warnings(payload)
    return payload


def _optional(payload: dict, key: str, kind) -> None:
    if key in payload:
        _require(isinstance(payload[key], kind))


def _upstream_warnings(payload: Any) -> list:
    warnings = payload.get("warnings") if isinstance(payload, dict) else None
    return warnings if isinstance(warnings, list) else []


def _generic(payload: Any, *, extra: tuple = ()) -> tuple[bool, list[str]]:
    """Upstream ``partial: true`` or any upstream warning makes the read partial."""

    if not isinstance(payload, dict):
        return True, [UNVERIFIED]
    if "partial" in payload and not isinstance(payload["partial"], bool):
        return True, [UNVERIFIED]
    partial = False
    warnings: list[str] = list(extra)
    if payload.get("partial") is True:
        partial = True
        warnings.append(UPSTREAM_PARTIAL)
    if _upstream_warnings(payload):
        partial = True
        warnings.append(UPSTREAM_WARNINGS)
    return partial, warnings


# Row 13 ha_get_entity (tools_entities.py:1794; single :1946, list :1996,
# unique_id :1428, empty list :336). ``uses_device_name`` is additive.
def _project_entity(payload: Any, arguments: dict) -> dict:
    data = _success_envelope(payload)
    if "entity_entry" in data:
        _require(isinstance(data["entity_entry"], dict))
    else:
        _require(isinstance(data.get("entity_entries"), list))
        _optional(data, "errors", list)
    return data


def _entity_completeness(payload: Any) -> tuple[bool, list[str]]:
    partial, warnings = _generic(payload)
    if isinstance(payload, dict) and payload.get("errors"):
        partial = True
        warnings.insert(0, ENTITY_INCOMPLETE)
    return partial, warnings


# Row 14 ha_get_entity_exposure (tools_voice_assistant.py:206). Code-identical.
def _project_exposure(payload: Any, arguments: dict) -> dict:
    data = _success_envelope(payload)
    if "exposed_to" in data:
        _require(
            isinstance(data["exposed_to"], dict)
            and isinstance(data.get("entity_id"), str)
        )
    else:
        _require(isinstance(data.get("exposed_entities"), (dict, list)))
    return data


# Row 17 ha_get_overview (tools_search.py:431, assembly :560-704).
def _project_overview(payload: Any, arguments: dict) -> dict:
    data = _success_envelope(payload)
    for key in ("system_summary", "domain_stats", "area_analysis", "ai_insights"):
        _optional(data, key, dict)
    _optional(data, "partial", bool)
    return {key: value for key, value in data.items() if key not in _OVERVIEW_DROPPED}


def _overview_completeness(payload: Any) -> tuple[bool, list[str]]:
    # smart_search/_overview.py:530-537: ``partial`` marks genuinely missing
    # data; visibility warnings annotate an otherwise complete result.
    return _generic(payload, extra=(VISIBLE_SCOPE,))


# Row 18 ha_get_skill_guide (server.py:970-1083). Only ``skill+file`` reaches
# here (read_arguments refuses the inventory and file-list tiers). The 9.1
# response is mapped back to the shipped 8.5 tier-3 envelope (8.5
# server.py:2002-2039); ``skill`` is taken from the validated upstream
# ``uri``. ``how_to_use`` describes the 9.1 file-only call form, which the
# shipped public schema does not accept, and is dropped. ``content``
# (including any strict-BPS acknowledgement line) is passed unchanged, as for
# the 8.6 adapter.
def _project_skill_guide(payload: Any, arguments: dict) -> dict:
    data = _success_envelope(payload)
    file = arguments.get("file")
    _require(
        arguments.get("skill") == _BEST_PRACTICES_SKILL and isinstance(file, str)
    )
    _require(data.get("file") == file)
    _require(data.get("uri") == f"skill://{_BEST_PRACTICES_SKILL}/{file}")
    _require(isinstance(data.get("content"), str))
    _optional(data, "skill_content_hint", str)
    result: dict[str, Any] = {}
    if "skill_content_hint" in data:
        result["skill_content_hint"] = data["skill_content_hint"]
    result.update(
        {
            "success": True,
            "skill": _BEST_PRACTICES_SKILL,
            "file": file,
            "uri": data["uri"],
            "content": data["content"],
        }
    )
    return result


def _complete(payload: Any) -> tuple[bool, list[str]]:
    return (False, []) if isinstance(payload, dict) else (True, [UNVERIFIED])


# Row 19 ha_get_state (tools_search.py:712; search/state.py:116-215;
# response_helpers.py:392-433 hoists ``warnings`` beside ``data``). Absent
# ``attributes`` are never fabricated as ``{}``.
def _project_state(payload: Any, arguments: dict) -> dict:
    _require(isinstance(payload, dict))
    _check_warnings(payload)
    data = payload.get("data")
    _require(isinstance(data, dict) and isinstance(payload.get("metadata"), dict))
    if isinstance(arguments.get("entity_id"), list):
        _require(isinstance(data.get("states"), dict))
        _require(
            isinstance(data.get("success"), bool)
            and isinstance(data.get("count"), int)
        )
        _optional(data, "errors", list)
        _optional(data, "partial", bool)
    else:
        _require("states" not in data)
        _optional(data, "attributes", dict)
    return payload


def _state_completeness(payload: Any) -> tuple[bool, list[str]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return True, [UNVERIFIED]
    data = payload["data"]
    partial = False
    warnings: list[str] = []
    upstream = _upstream_warnings(payload)
    visibility = [
        w for w in upstream
        if isinstance(w, str) and w.startswith(_STATE_VISIBILITY_PREFIX)
    ]
    if visibility:
        partial = True
        warnings.append(STATE_WITHHELD)
    if "states" in data and (
        data.get("success") is not True
        or data.get("partial") is True
        or data.get("errors")
    ):
        partial = True
        warnings.append(STATE_INCOMPLETE)
    if len(upstream) > len(visibility):
        partial = True
        warnings.append(UPSTREAM_WARNINGS)
    return partial, warnings


# Row 20 ha_get_todo (tools_todo.py:309; unavailable list :232-278). Read only.
def _project_todo(payload: Any, arguments: dict) -> dict:
    data = _success_envelope(payload)
    if "todo_lists" in data:
        _require(isinstance(data["todo_lists"], list))
    else:
        _require(
            isinstance(data.get("items"), list)
            and isinstance(data.get("entity_id"), str)
        )
    return data


# Row 21 ha_get_zone (tools_zones.py:170; envelope :49-104). Extra home and
# YAML rows plus ``entity_id``/``editable``/``source``/``icon`` are additive.
def _project_zone(payload: Any, arguments: dict) -> dict:
    data = _success_envelope(payload)
    if "zones" in data:
        _require(
            isinstance(data["zones"], list)
            and all(isinstance(z, dict) for z in data["zones"])
        )
    else:
        _require(isinstance(data.get("zone"), dict))
    return data


# Row 22 ha_list_floors_areas (tools_areas.py:142, envelope :224-238).
def _project_floors_areas(payload: Any, arguments: dict) -> dict:
    data = _success_envelope(payload)
    for key in ("floors", "unassigned_areas", "orphaned_areas"):
        _optional(data, key, list)
    return data


# Row 23 ha_list_services (tools_services.py:65). Annotation-only change.
def _project_services(payload: Any, arguments: dict) -> dict:
    data = _success_envelope(payload)
    _optional(data, "services", dict)
    return data


# Row 24 ha_search (tools_search.py:61; search/legacy.py:106-155,
# search/response.py:171-200). All-legs failure raises upstream (legacy.py:136)
# and is classified by the gateway as a failure. The shipped 8.5 envelope
# always carries ``warnings`` (8.5 tools_search.py:673); 9.1 omits it when
# empty (response.py:191), so it is normalized back to a list.
def _project_search(payload: Any, arguments: dict) -> dict:
    data = _success_envelope(payload)
    _require(isinstance(data.get("partial"), bool))
    _require(isinstance(data.get("errors", []), list))
    for bucket in _SEARCH_BUCKETS:
        _optional(data, bucket, list)
    result = dict(data)
    result["warnings"] = list(data.get("warnings") or [])
    return result


def _search_completeness(payload: Any) -> tuple[bool, list[str]]:
    # 9.1 hides filtered entities from search without any marker, so every
    # result is qualified as visible scope (design F3).
    if not isinstance(payload, dict) or not isinstance(payload.get("partial"), bool):
        return True, [UNVERIFIED]
    warnings = [VISIBLE_SCOPE]
    if payload["partial"]:
        warnings.append(SEARCH_PARTIAL)
    if _upstream_warnings(payload):
        warnings.append(UPSTREAM_WARNINGS)
    return True, warnings


PROJECTIONS: dict = {
    "ha_get_entity": (_project_entity, _entity_completeness),
    "ha_get_entity_exposure": (_project_exposure, _generic),
    "ha_get_overview": (_project_overview, _overview_completeness),
    "ha_get_skill_guide": (_project_skill_guide, _complete),
    "ha_get_state": (_project_state, _state_completeness),
    "ha_get_todo": (_project_todo, _generic),
    "ha_get_zone": (_project_zone, _generic),
    "ha_list_floors_areas": (_project_floors_areas, _generic),
    "ha_list_services": (_project_services, _generic),
    "ha_search": (_project_search, _search_completeness),
}
