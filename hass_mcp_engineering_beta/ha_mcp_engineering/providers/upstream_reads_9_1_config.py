"""Compiled 9.1.0 projections for ledger rows 1-11 (HAMCP-319 section 7).

Each entry in ``PROJECTIONS`` maps an upstream tool name to
``(project(payload, arguments) -> payload, completeness(payload) -> (partial, warnings))``.
``project`` raises ``ValueError`` on an unrecognized shape (fail closed).
Warnings are fixed Engineering strings, never upstream text.

Every accepted shape is the exact success envelope the pinned 9.1.0 source
(``d2e3f4b0``) builds; citations are to ``src/ha_mcp/tools/``. ``project``
receives the validated public 8.5 arguments, so echoed identities, paging and
order are checked against what Engineering sent. Additive 9.1 fields are passed
through (bounded later by the generic sanitizer and response limit); nothing is
invented. The PD rows (8 and 10) disclose fields 9.1 does not report in a
closed ``unavailable_fields`` list instead of synthesizing them.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

GROUP_FIELDS_UNAVAILABLE_WARNING = (
    "Some group records do not report the all or order fields on ha-mcp "
    "9.1.0; they are listed in unavailable_fields and were not synthesized."
)
TEMPLATE_FIELDS_UNAVAILABLE_WARNING = (
    "ha-mcp 9.1.0 does not report request_id or evaluation_time for template "
    "evaluation; they are listed in unavailable_fields and were not synthesized."
)
_GROUP_OPTIONAL_FIELDS = ("all", "order")
_TEMPLATE_UNAVAILABLE_FIELDS = ("evaluation_time", "request_id")
# 8.5 reported these dashboard resource types with a zero count; 9.1 counts
# only types present (resource_codec.py:262-268), so an absent key is zero.
_RESOURCE_TYPES_8_5 = ("css", "js", "module")
_UNAVAILABLE = "unavailable_fields"


def _fail() -> None:
    raise ValueError("invalid_response")


def _count(value: Any) -> bool:
    return type(value) is int and value >= 0


def _string(value: Any) -> bool:
    return isinstance(value, str)


def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _optional_dict(value: Any) -> bool:
    return value is None or isinstance(value, dict)


def _present(_value: Any) -> bool:
    return True


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _dict_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def _equals(expected: Any) -> Callable[[Any], bool]:
    return lambda value: type(value) is type(expected) and value == expected


def _envelope(
    payload: Any,
    required: dict[str, Callable[[Any], bool]],
    optional: dict[str, Callable[[Any], bool]] | None = None,
    forbidden: tuple[str, ...] = (),
) -> dict:
    """Return a private copy of an exact 9.1 success envelope or fail closed."""

    if not isinstance(payload, dict) or payload.get("success") is not True:
        _fail()
    for key, check in required.items():
        if key not in payload or not check(payload[key]):
            _fail()
    for key, check in (optional or {}).items():
        if key in payload and not check(payload[key]):
            _fail()
    if any(key in payload for key in forbidden):
        _fail()
    return deepcopy(payload)


def _pagination(payload: dict, items: list, arguments: dict) -> None:
    """``build_pagination_metadata`` (response_helpers.py:271-291) exactly."""

    offset, limit = arguments.get("offset", 0), arguments.get("limit", 100)
    total, count = payload.get("total_count"), payload.get("count")
    if not _count(total) or not _count(count) or count != len(items):
        _fail()
    has_more = offset + count < total
    if (
        not _equals(offset)(payload.get("offset"))
        or not _equals(limit)(payload.get("limit"))
        or count > limit
        or payload.get("has_more") is not has_more
        or payload.get("next_offset") != (offset + limit if has_more else None)
    ):
        _fail()


def _complete(_payload: Any) -> tuple[bool, list[str]]:
    return False, []


_WARNINGS = {"warnings": _string_list}


# Row 1 (PJ): tools_config_automations.py:465-476.
def _automation(payload: Any, arguments: dict) -> dict:
    return _envelope(
        payload,
        {
            "action": _equals("get"),
            "automation_id": _string,
            "config": _is_dict,
            "config_hash": _string,
            "entity": _optional_dict,
            "validation": _optional_dict,
            "references": _optional_dict,
        },
        _WARNINGS,
    )


# Row 2 (P): tools_calendar.py:239-250.
def _calendar(payload: Any, arguments: dict) -> dict:
    result = _envelope(
        payload,
        {
            "entity_id": _equals(arguments.get("entity_id")),
            "events": _dict_list,
            "count": _count,
            "total_available": _count,
            "time_range": lambda value: isinstance(value, dict)
            and set(value) == {"start", "end"}
            and all(isinstance(item, str) for item in value.values()),
            "message": _string,
        },
    )
    if (
        result["count"] != len(result["events"])
        or result["count"] > arguments.get("max_results", 20)
        or result["total_available"] < result["count"]
    ):
        _fail()
    return result


# Row 3 (P): tools_categories.py:105-126.
def _category(payload: Any, arguments: dict) -> dict:
    category_id = arguments.get("category_id")
    common = {"scope": _equals(arguments.get("scope")), "message": _string}
    if category_id is None:
        result = _envelope(
            payload,
            {**common, "count": _count, "categories": _dict_list},
            forbidden=("category", "category_id"),
        )
        if result["count"] != len(result["categories"]):
            _fail()
        return result
    return _envelope(
        payload,
        {**common, "category_id": _equals(category_id), "category": _is_dict},
        forbidden=("categories", "count"),
    )


# Row 4 (P): tools_labels.py:628-646.
def _label(payload: Any, arguments: dict) -> dict:
    label_id = arguments.get("label_id")
    if label_id is None:
        result = _envelope(
            payload,
            {"count": _count, "labels": _dict_list, "message": _string},
            forbidden=("label", "label_id"),
        )
        if result["count"] != len(result["labels"]):
            _fail()
        return result
    return _envelope(
        payload,
        {"label_id": _equals(label_id), "label": _is_dict, "message": _string},
        forbidden=("labels", "count"),
    )


# Row 5 (PJ): tools_config_scenes.py:447-458 (_legacy_get_scene). Discovery
# (tools_config_scenes.py:348-355) is never reached because scene_id is always
# sent; a discovery-shaped result is refused.
def _scene(payload: Any, arguments: dict) -> dict:
    return _envelope(
        payload,
        {
            "action": _equals("get"),
            "scene_id": _string,
            "config": _is_dict,
            "config_hash": _string,
        },
        {"category": _string, **_WARNINGS},
        forbidden=("scenes", "total_count", "has_more"),
    )


# Row 6 (P): tools_config_scripts.py:237-256 (_legacy_get_script). The
# canonical bare ``script_id`` and body-level ``config`` are kept for the
# downstream dependency consumer (dependency/script_sources.py); completeness
# stays complete so that consumer keeps accepting the read.
def _script(payload: Any, arguments: dict) -> dict:
    return _envelope(
        payload,
        {
            "action": _equals("get"),
            "script_id": _string,
            "config": _is_dict,
            "config_hash": _string,
        },
        _WARNINGS,
    )


# Row 7 (PJ): tools_resources.py:246-269; rows resource_codec.py:239-275.
def _resources(payload: Any, arguments: dict) -> dict:
    result = _envelope(
        payload,
        {
            "action": _equals("list"),
            "resources": _dict_list,
            "inline_count": _count,
            "by_type": lambda value: isinstance(value, dict)
            and all(isinstance(key, str) and _count(item) for key, item in value.items()),
        },
        {
            "content_truncated": _count,
            "content_truncation_note": _string,
            "migration_hint": _string,
            "resource_mode": _string,
            "note": _string,
        },
    )
    _pagination(result, result["resources"], arguments)
    # include_hash is never sent; a hash-bearing row means it was honored.
    if any("_config_hash" in row for row in result["resources"]):
        _fail()
    if (
        sum(result["by_type"].values()) > result["total_count"]
        or result["inline_count"] > result["total_count"]
    ):
        _fail()
    result["by_type"] = {
        **{name: 0 for name in _RESOURCE_TYPES_8_5}, **result["by_type"],
    }
    return result


# Row 8 (PD): tools_groups.py:149-163 (record), :179-186 (envelope).
def _groups(payload: Any, arguments: dict) -> dict:
    result = _envelope(payload, {"groups": _dict_list, "message": _string})
    _pagination(result, result["groups"], arguments)
    for record in result["groups"]:
        entity_id = record.get("entity_id")
        if (
            _UNAVAILABLE in record
            or not isinstance(entity_id, str)
            or not entity_id.startswith("group.")
            or record.get("object_id") != entity_id.removeprefix("group.")
            or not isinstance(record.get("entity_ids"), list)
        ):
            _fail()
        missing = [name for name in _GROUP_OPTIONAL_FIELDS if name not in record]
        if missing:
            record[_UNAVAILABLE] = missing
    return result


def _groups_completeness(payload: Any) -> tuple[bool, list[str]]:
    groups = payload.get("groups") if isinstance(payload, dict) else None
    if isinstance(groups, list) and any(
        isinstance(record, dict) and _UNAVAILABLE in record for record in groups
    ):
        return True, [GROUP_FIELDS_UNAVAILABLE_WARNING]
    return False, []


# Row 9 (PJ): tools_config_helpers.py:246-255 paginated by
# config_helpers/listing.py:148-192. The unpaginated non-list fallback and
# describe mode are refused.
def _helpers(payload: Any, arguments: dict) -> dict:
    result = _envelope(
        payload,
        {
            "helper_type": _equals(arguments.get("helper_type")),
            "helpers": _dict_list,
            "message": _string,
        },
        _WARNINGS,
    )
    _pagination(result, result["helpers"], arguments)
    return result


# Row 10 (PD): tools_utility.py:150-165 (_render_template). The condition
# executor is never reached; 9.1 reports no request_id or evaluation_time,
# which are disclosed rather than synthesized.
def _template(payload: Any, arguments: dict) -> dict:
    result = _envelope(
        payload,
        {
            "template": _equals(arguments.get("template")),
            "result": _present,
            "listeners": _is_dict,
        },
        _WARNINGS,
        forbidden=("condition", _UNAVAILABLE, *_TEMPLATE_UNAVAILABLE_FIELDS),
    )
    result[_UNAVAILABLE] = list(_TEMPLATE_UNAVAILABLE_FIELDS)
    return result


def _template_completeness(_payload: Any) -> tuple[bool, list[str]]:
    return True, [TEMPLATE_FIELDS_UNAVAILABLE_WARNING]


# Row 11 (P): tools_traces.py:528-595 (list), :598-729 (detail and the
# metadata keys _filter_trace_sections always keeps).
def _traces(payload: Any, arguments: dict) -> dict:
    automation = arguments.get("automation_id")
    run_id = arguments.get("run_id")
    if run_id is not None:
        return _envelope(
            payload,
            {
                "automation_id": _equals(automation),
                "run_id": _equals(run_id),
                "timestamp": _present,
                "state": _present,
            },
            forbidden=("traces", "trace_count"),
        )
    result = _envelope(
        payload,
        {
            "automation_id": _equals(automation),
            "trace_count": _count,
            "total_available": _count,
            "offset": _equals(arguments.get("offset", 0)),
            "order": _equals(arguments.get("order", "newest")),
            "has_more": lambda value: isinstance(value, bool),
            "traces": _dict_list,
            "hint": _string,
        },
        {"diagnostics": _is_dict},
    )
    if (
        result["trace_count"] != len(result["traces"])
        or result["trace_count"] > arguments.get("limit", 10)
        or result["has_more"] is not (
            result["offset"] + result["trace_count"] < result["total_available"]
        )
    ):
        _fail()
    return result


PROJECTIONS: dict = {
    "ha_config_get_automation": (_automation, _complete),
    "ha_config_get_calendar_events": (_calendar, _complete),
    "ha_config_get_category": (_category, _complete),
    "ha_config_get_label": (_label, _complete),
    "ha_config_get_scene": (_scene, _complete),
    "ha_config_get_script": (_script, _complete),
    "ha_config_list_dashboard_resources": (_resources, _complete),
    "ha_config_list_groups": (_groups, _groups_completeness),
    "ha_config_list_helpers": (_helpers, _complete),
    "ha_eval_template": (_template, _template_completeness),
    "ha_get_automation_traces": (_traces, _complete),
}
