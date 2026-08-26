"""Deterministic, structure-first automation change risk classification."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from jinja2 import Environment, TemplateSyntaxError, nodes

from .models import ChangeOperation, ChangeRiskAssessment, RiskLevel


HIGH_SERVICE_PREFIXES = (
    "lock.",
    "alarm_control_panel.",
    "hassio.",
    "automation.",
    "script.",
)
HIGH_EXACT_SERVICES = {
    "homeassistant.restart",
    "homeassistant.stop",
    "homeassistant.reload_all",
    "water_heater.turn_off",
    "valve.close",
    "valve.open",
}
SAFETY_CRITICAL_SERVICES = frozenset(
    {
        "alarm_control_panel.alarm_disarm",
        "lock.unlock",
    }
)
DESTRUCTIVE_ACTION_NAMES = {"delete", "remove", "shutdown", "reboot", "restart", "stop"}
MEDIUM_SERVICE_PREFIXES = ("light.", "switch.", "climate.", "cover.", "fan.")
SENSITIVE_ENTITY_DOMAINS = {"lock", "alarm_control_panel", "valve"}
WATER_TARGET_TERMS = {"water", "shutoff", "shut_off", "main_valve"}
_ACTION_CONTROL_FAMILIES = frozenset(
    {"choose", "if", "parallel", "repeat", "sequence"}
)
_ACTION_SIMPLE_FAMILIES = frozenset(
    {
        "condition",
        "delay",
        "event",
        "scene",
        "set_conversation_response",
        "stop",
        "variables",
        "wait_for_trigger",
        "wait_template",
    }
)
_ACTION_DEVICE_DISCRIMINATORS = frozenset({"device_id", "domain", "type"})
_TARGET_SELECTORS = (
    "entity_id",
    "device_id",
    "area_id",
    "floor_id",
    "label_id",
)
_HELPER_DIRECT_EFFECT_DOMAINS = frozenset(
    {
        "alarm_control_panel",
        "climate",
        "cover",
        "fan",
        "light",
        "lock",
        "switch",
        "valve",
        "water_heater",
    }
)
_HELPER_SAFETY_CRITICAL_DOMAINS = frozenset(
    {"alarm_control_panel", "lock", "valve"}
)
_HELPER_TRANSITIVE_EFFECT_DOMAINS = frozenset(
    {"automation", "scene", "script"}
)
_HELPER_PROVEN_BENIGN_SERVICE_DOMAINS = frozenset({"notify"})
_HELPER_NOTIFICATION_TEXT_BYTES = 4_096
_HELPER_NOTIFICATION_TEMPLATE_NODE_LIMIT = 512
_HELPER_NOTIFICATION_DATA_FIELDS = frozenset({"message", "title"})
_HELPER_REVIEWED_NONPHYSICAL_NOTIFICATION_CONTROLS = frozenset(
    {
        "clear_badge",
        "clear_notification",
        "kiosk_hide_screensaver",
        "kiosk_show_screensaver",
        "update_complications",
        "update_widgets",
    }
)
_HELPER_EFFECTFUL_NOTIFICATION_CONTROLS = frozenset(
    {
        "remove_channel",
        "request_location_update",
        "tts",
    }
)
_HELPER_DYNAMIC_NOTIFICATION_CONTROL_PREFIXES = frozenset(
    {"command_", "kiosk_"}
)
_HELPER_GENERIC_EFFECT_SERVICES = frozenset(
    {
        "homeassistant.toggle",
        "homeassistant.turn_off",
        "homeassistant.turn_on",
    }
)
_HELPER_TRANSITIVE_ACTION_FAMILIES = frozenset({"event", "scene"})
# Bounds the *display* value lists (action domains, services, reason codes)
# projected for one automation.  It is not a structural traversal budget.
_HELPER_PROFILE_LIMIT = 32
_HELPER_PROFILE_VALUE_BYTES = 256
_HELPER_EFFECT_PROJECTION_MODEL = "automation-action-effect-v2"
_HELPER_EFFECT_STRUCTURE_NODE_LIMIT = 512
_HELPER_EFFECT_STRUCTURE_DEPTH_LIMIT = 16
_HELPER_ACTION_ANALYSIS_STEP_LIMIT = 512
_HELPER_ACTION_ANALYSIS_DEPTH_LIMIT = 16
_HELPER_EFFECT_ANALYSIS_NODE_LIMIT = 4096
# Bounds the effect projection's structural evidence: how many service-call
# data leaves may be flattened, and how many target/data values are retained.
# These are a subset of the action structure, so the bound stays below the
# structure node budget and leaves that budget as the one that catches a
# pathologically large automation.  It is deliberately distinct from
# _HELPER_PROFILE_LIMIT, which bounds display lists: reusing the display bound
# for structural evidence clipped ordinary automations - one notification
# payload can exceed 31 leaves on its own - and a clipped projection makes
# every helper that could reach that automation non-actionable.  Each value is
# independently size-bounded by _HELPER_PROFILE_VALUE_BYTES, so this bounds
# count, not payload size.
_HELPER_EFFECT_PROJECTION_VALUE_LIMIT = 256
_HELPER_EFFECT_DISPLAY_ONLY_FIELDS = frozenset({"alias", "description"})
_HELPER_EFFECT_SAFE_DATA_FIELDS = frozenset(
    {
        "brightness",
        "brightness_pct",
        "color_temp_kelvin",
        "hvac_mode",
        "percentage",
        "position",
        "preset_mode",
        "rgb_color",
        "temperature",
        "tilt_position",
        "transition",
        "volume_level",
    }
)
_HELPER_EFFECT_SENSITIVE_TERMS = frozenset(
    {
        "access_code",
        "api_key",
        "authorization",
        "credential",
        "password",
        "payload",
        "pin",
        "secret",
        "token",
        "webhook",
    }
)
_HELPER_TEMPLATE_PARSER = Environment(autoescape=False)


def _walk(value: Any) -> Iterable[Any]:
    """Walk JSON-like material without consuming the Python call stack."""

    pending = [value]
    while pending:
        item = pending.pop()
        yield item
        if isinstance(item, dict):
            pending.extend(reversed(tuple(item.values())))
        elif isinstance(item, list):
            pending.extend(reversed(item))


def _has_template(value: str) -> bool:
    return "{{" in value or "{%" in value


def _action_roots(config: dict[str, Any]) -> list[tuple[str, Any]]:
    roots = []
    for key in ("action", "actions"):
        if key in config:
            roots.append((key, config[key]))
    return roots


def _action_nodes(
    value: Any,
    path: str,
    *,
    allow_sequence_wrapper: bool = False,
) -> Iterable[tuple[str, dict[str, Any]]]:
    if not isinstance(value, list):
        return
    for index, step in enumerate(value):
        step_path = f"{path}[{index}]"
        if not isinstance(step, dict):
            continue
        if "service" in step or "action" in step:
            yield step_path, step
        choices = step.get("choose")
        if isinstance(choices, list):
            for choice_index, choice in enumerate(choices):
                if isinstance(choice, dict):
                    yield from _action_nodes(
                        choice.get("sequence"),
                        f"{step_path}.choose[{choice_index}].sequence",
                    )
        if "default" in step:
            yield from _action_nodes(
                step.get("default"), f"{step_path}.default"
            )
        for branch in ("then", "else"):
            if branch in step:
                yield from _action_nodes(
                    step.get(branch), f"{step_path}.{branch}"
                )
        repeat = step.get("repeat")
        if isinstance(repeat, dict):
            yield from _action_nodes(
                repeat.get("sequence"), f"{step_path}.repeat.sequence"
            )
        if "parallel" in step:
            yield from _action_nodes(
                step.get("parallel"),
                f"{step_path}.parallel",
                allow_sequence_wrapper=True,
            )
        if allow_sequence_wrapper and "sequence" in step:
            yield from _action_nodes(
                step.get("sequence"), f"{step_path}.sequence"
            )


def _action_steps(
    value: Any,
    path: str,
    *,
    allow_sequence_wrapper: bool = False,
) -> Iterable[tuple[str, dict[str, Any]]]:
    """Yield every bounded action step with its execution-order path."""

    if not isinstance(value, list):
        return
    for index, step in enumerate(value):
        step_path = f"{path}[{index}]"
        if not isinstance(step, dict):
            continue
        yield step_path, step
        choices = step.get("choose")
        if isinstance(choices, list):
            for choice_index, choice in enumerate(choices):
                if isinstance(choice, dict):
                    yield from _action_steps(
                        choice.get("sequence"),
                        f"{step_path}.choose[{choice_index}].sequence",
                    )
        if "default" in step:
            yield from _action_steps(
                step.get("default"), f"{step_path}.default"
            )
        for branch in ("then", "else"):
            if branch in step:
                yield from _action_steps(
                    step.get(branch), f"{step_path}.{branch}"
                )
        repeat = step.get("repeat")
        if isinstance(repeat, dict):
            yield from _action_steps(
                repeat.get("sequence"), f"{step_path}.repeat.sequence"
            )
        if "parallel" in step:
            yield from _action_steps(
                step.get("parallel"),
                f"{step_path}.parallel",
                allow_sequence_wrapper=True,
            )
        if allow_sequence_wrapper and "sequence" in step:
            yield from _action_steps(
                step.get("sequence"), f"{step_path}.sequence"
            )


def _bounded_action_analysis_coverage(config: dict[str, Any]) -> dict[str, Any]:
    """Prove that every action step was visited within reviewed bounds."""

    state: dict[str, Any] = {
        "count": 0,
        "effect_node_count": 0,
        "limit_exceeded": False,
        "reason": None,
        "overflow": hashlib.sha256(),
    }

    def fail(reason: str, path: str, value: Any) -> None:
        state["limit_exceeded"] = True
        state["reason"] = state["reason"] or reason
        state["overflow"].update(
            f"{path}:{_effect_value_hash(value)}".encode("utf-8")
        )

    def inspect_effect_value(value: Any, path: str) -> None:
        pending: list[tuple[Any, str, int]] = [(value, path, 0)]
        while pending and not state["limit_exceeded"]:
            item, item_path, depth = pending.pop()
            if depth > _HELPER_EFFECT_STRUCTURE_DEPTH_LIMIT:
                fail("effect_data_depth_limit_exceeded", item_path, item)
                return
            if state["effect_node_count"] >= (
                _HELPER_EFFECT_ANALYSIS_NODE_LIMIT
            ):
                fail("effect_data_node_limit_exceeded", item_path, item)
                return
            state["effect_node_count"] += 1
            if isinstance(item, dict):
                children = [
                    (item[key], f"{item_path}.{key}", depth + 1)
                    for key in sorted(
                        item, key=lambda key: str(key).encode("utf-8")
                    )
                ]
                pending.extend(reversed(children))
            elif isinstance(item, list):
                pending.extend(
                    (child, f"{item_path}[{index}]", depth + 1)
                    for index, child in reversed(tuple(enumerate(item)))
                )

    pending: list[tuple[Any, str, int, bool]] = [
        (root, root_path, 0, False)
        for root_path, root in reversed(_action_roots(config))
    ]
    while pending and not state["limit_exceeded"]:
        value, path, depth, allow_sequence_wrapper = pending.pop()
        if depth > _HELPER_ACTION_ANALYSIS_DEPTH_LIMIT:
            fail("action_analysis_depth_limit_exceeded", path, value)
            break
        if not isinstance(value, list):
            continue
        for index, step in enumerate(value):
            step_path = f"{path}[{index}]"
            if state["count"] >= _HELPER_ACTION_ANALYSIS_STEP_LIMIT:
                fail(
                    "action_analysis_step_limit_exceeded",
                    step_path,
                    value[index:],
                )
                break
            state["count"] += 1
            if not isinstance(step, dict):
                continue
            for effect_key in ("data", "data_template", "event_data"):
                if effect_key in step:
                    inspect_effect_value(
                        step[effect_key], f"{step_path}.{effect_key}"
                    )
                    if state["limit_exceeded"]:
                        break
            if state["limit_exceeded"]:
                break
            children: list[tuple[Any, str, int, bool]] = []
            choices = step.get("choose")
            if isinstance(choices, list):
                for choice_index, choice in enumerate(choices):
                    if isinstance(choice, dict):
                        children.append(
                            (
                                choice.get("sequence"),
                                f"{step_path}.choose[{choice_index}].sequence",
                                depth + 1,
                                False,
                            )
                        )
            if "default" in step:
                children.append(
                    (
                        step.get("default"),
                        f"{step_path}.default",
                        depth + 1,
                        False,
                    )
                )
            for branch in ("then", "else"):
                if branch in step:
                    children.append(
                        (
                            step.get(branch),
                            f"{step_path}.{branch}",
                            depth + 1,
                            False,
                        )
                    )
            repeat = step.get("repeat")
            if isinstance(repeat, dict):
                children.append(
                    (
                        repeat.get("sequence"),
                        f"{step_path}.repeat.sequence",
                        depth + 1,
                        False,
                    )
                )
            if "parallel" in step:
                children.append(
                    (
                        step.get("parallel"),
                        f"{step_path}.parallel",
                        depth + 1,
                        True,
                    )
                )
            if allow_sequence_wrapper and "sequence" in step:
                children.append(
                    (
                        step.get("sequence"),
                        f"{step_path}.sequence",
                        depth + 1,
                        False,
                    )
                )
            pending.extend(reversed(children))
    return {
        "complete": not state["limit_exceeded"],
        "observed_step_count": int(state["count"]),
        "step_limit": _HELPER_ACTION_ANALYSIS_STEP_LIMIT,
        "depth_limit": _HELPER_ACTION_ANALYSIS_DEPTH_LIMIT,
        "observed_effect_node_count": int(state["effect_node_count"]),
        "effect_node_limit": _HELPER_EFFECT_ANALYSIS_NODE_LIMIT,
        "effect_depth_limit": _HELPER_EFFECT_STRUCTURE_DEPTH_LIMIT,
        "reason": state["reason"],
        "overflow_fingerprint": (
            state["overflow"].hexdigest()
            if state["limit_exceeded"]
            else None
        ),
    }


def _action_families(step: dict[str, Any]) -> tuple[str, ...]:
    families = {
        name
        for name in _ACTION_CONTROL_FAMILIES
        if name in step
    }
    families.update(
        name for name in _ACTION_SIMPLE_FAMILIES if name in step
    )
    if "service" in step or "action" in step:
        families.add("call")
    if _ACTION_DEVICE_DISCRIMINATORS.intersection(step):
        families.add("device")
    if "service" in step and "action" in step:
        families.add("ambiguous_call_alias")
    return tuple(sorted(families))


def _unresolved_action_paths(
    value: Any,
    path: str,
    *,
    allow_sequence_wrapper: bool = False,
) -> Iterable[str]:
    if not isinstance(value, list):
        yield path
        return
    for index, step in enumerate(value):
        step_path = f"{path}[{index}]"
        if not isinstance(step, dict):
            yield step_path
            continue
        families = _action_families(step)
        if len(families) != 1:
            yield step_path
            continue
        family = families[0]
        if family == "sequence":
            if not allow_sequence_wrapper:
                yield step_path
                continue
            yield from _unresolved_action_paths(
                step.get("sequence"),
                f"{step_path}.sequence",
            )
        elif family == "choose":
            choices = step.get("choose")
            if not isinstance(choices, list):
                yield step_path
                continue
            for choice_index, choice in enumerate(choices):
                choice_path = f"{step_path}.choose[{choice_index}]"
                if not isinstance(choice, dict) or "sequence" not in choice:
                    yield choice_path
                    continue
                yield from _unresolved_action_paths(
                    choice.get("sequence"), f"{choice_path}.sequence"
                )
            if "default" in step:
                yield from _unresolved_action_paths(
                    step.get("default"), f"{step_path}.default"
                )
        elif family == "if":
            if not isinstance(step.get("if"), list) or "then" not in step:
                yield step_path
                continue
            yield from _unresolved_action_paths(
                step.get("then"), f"{step_path}.then"
            )
            if "else" in step:
                yield from _unresolved_action_paths(
                    step.get("else"), f"{step_path}.else"
                )
        elif family == "repeat":
            repeat = step.get("repeat")
            if not isinstance(repeat, dict) or "sequence" not in repeat:
                yield step_path
                continue
            yield from _unresolved_action_paths(
                repeat.get("sequence"), f"{step_path}.repeat.sequence"
            )
        elif family == "parallel":
            yield from _unresolved_action_paths(
                step.get("parallel"),
                f"{step_path}.parallel",
                allow_sequence_wrapper=True,
            )


def _target_selectors(
    node: dict[str, Any],
) -> Iterable[tuple[str, str, Any]]:
    target = node.get("target")
    if isinstance(target, dict):
        for selector in _TARGET_SELECTORS:
            if selector in target:
                yield f"target.{selector}", selector, target[selector]
    elif "target" in node:
        yield "target", "target", target
    for selector in _TARGET_SELECTORS:
        if selector in node:
            yield selector, selector, node[selector]
    data = node.get("data")
    if isinstance(data, dict) and "entity_id" in data:
        yield "data.entity_id", "entity_id", data["entity_id"]


def _target_values(node: dict[str, Any]) -> Iterable[tuple[str, str]]:
    for field, selector, value in _target_selectors(node):
        if selector != "entity_id":
            continue
        values = [value] if isinstance(value, str) else value
        if isinstance(values, list):
            for item in values:
                if isinstance(item, str):
                    yield field, item.lower()


def _target_shape_evidence(
    node: dict[str, Any], path: str
) -> tuple[list[dict[str, str]], bool]:
    selectors = list(_target_selectors(node))
    evidence: list[dict[str, str]] = []
    unresolved = False
    if not selectors:
        evidence.append(
            {"field": f"{path}.target", "trigger": "omitted_action_target"}
        )
        return evidence, unresolved

    selector_names = {selector for _field, selector, _value in selectors}
    if len(selector_names) > 1:
        evidence.append(
            {"field": f"{path}.target", "trigger": "mixed_target_selector"}
        )
    for field, selector, value in selectors:
        if selector == "device_id":
            evidence.append(
                {"field": f"{path}.{field}", "trigger": "device_target"}
            )
        elif selector == "area_id":
            evidence.append(
                {"field": f"{path}.{field}", "trigger": "area_target"}
            )
        elif selector == "floor_id":
            evidence.append(
                {"field": f"{path}.{field}", "trigger": "floor_target"}
            )
        elif selector == "label_id":
            evidence.append(
                {"field": f"{path}.{field}", "trigger": "label_target"}
            )
        values = [value] if isinstance(value, str) else value
        selector_unresolved = False
        if isinstance(values, list):
            if len(values) > 1:
                evidence.append(
                    {"field": f"{path}.{field}", "trigger": "target_list"}
                )
            if not values or any(
                not isinstance(item, str)
                or not item.strip()
                or _has_template(item)
                for item in values
            ):
                selector_unresolved = True
        else:
            selector_unresolved = True
        if selector_unresolved:
            unresolved = True
            evidence.append(
                {
                    "field": f"{path}.{field}",
                    "trigger": "unresolved_dynamic_target",
                }
            )
    return evidence, unresolved


def _blueprint_targets(config: dict[str, Any]) -> Iterable[tuple[str, str]]:
    blueprint = config.get("use_blueprint")
    inputs = blueprint.get("input") if isinstance(blueprint, dict) else None
    if not isinstance(inputs, dict):
        return
    for key, value in inputs.items():
        for item in _walk(value):
            if isinstance(item, str) and "." in item and not _has_template(item):
                yield f"use_blueprint.input.{key}", item.lower()


def _structured_analysis(
    config: dict[str, Any],
) -> tuple[list[str], list[dict[str, str]], list[str], list[str]]:
    services: set[str] = set()
    evidence: list[dict[str, str]] = []
    warnings: set[str] = set()
    targets: list[str] = []
    for root_path, root in _action_roots(config):
        for unresolved_path in _unresolved_action_paths(root, root_path):
            warnings.add("Action structure could not be bounded structurally.")
            evidence.append(
                {
                    "field": unresolved_path,
                    "trigger": "unresolved_action_structure",
                }
            )
        for path, node in _action_nodes(root, root_path):
            for alias in ("service", "action"):
                if alias not in node:
                    continue
                service = node[alias]
                if not isinstance(service, str) or not service.strip():
                    warnings.add(
                        "Dynamic service action could not be bounded structurally."
                    )
                    evidence.append(
                        {
                            "field": f"{path}.{alias}",
                            "trigger": "unresolved_dynamic_service",
                        }
                    )
                    continue
                if _has_template(service):
                    warnings.add(
                        "Dynamic service action could not be bounded structurally."
                    )
                    evidence.append(
                        {
                            "field": f"{path}.{alias}",
                            "trigger": "unresolved_dynamic_service",
                        }
                    )
                    continue
                normalized = service.lower().strip()
                services.add(normalized)
                if normalized in SAFETY_CRITICAL_SERVICES:
                    evidence.append(
                        {
                            "field": f"{path}.{alias}",
                            "trigger": "safety_critical_service",
                            "service": normalized,
                        }
                    )
                if _is_high_service(normalized):
                    evidence.append(
                        {
                            "field": f"{path}.{alias}",
                            "trigger": "high_risk_service",
                            "service": normalized,
                        }
                    )
            shape_evidence, unresolved_target = _target_shape_evidence(
                node, path
            )
            evidence.extend(shape_evidence)
            if unresolved_target:
                warnings.add(
                    "Dynamic action target could not be bounded structurally."
                )
            for field, entity_id in _target_values(node):
                targets.append(entity_id)
                if _has_template(entity_id):
                    continue
                domain = entity_id.split(".", 1)[0]
                if domain in SENSITIVE_ENTITY_DOMAINS:
                    evidence.append(
                        {
                            "field": f"{path}.{field}",
                            "trigger": "sensitive_entity_domain",
                            "domain": domain,
                        }
                    )
                if domain == "cover" and "garage" in entity_id:
                    evidence.append(
                        {
                            "field": f"{path}.{field}",
                            "trigger": "garage_cover_target",
                            "domain": domain,
                        }
                    )
                if any(term in entity_id for term in WATER_TARGET_TERMS):
                    evidence.append(
                        {
                            "field": f"{path}.{field}",
                            "trigger": "water_control_target",
                            "domain": domain,
                        }
                    )
    for path, entity_id in _blueprint_targets(config):
        targets.append(entity_id)
        domain = entity_id.split(".", 1)[0]
        if domain in SENSITIVE_ENTITY_DOMAINS:
            evidence.append({"field": path, "trigger": "sensitive_blueprint_input", "domain": domain})
        if domain == "cover" and "garage" in entity_id:
            evidence.append({"field": path, "trigger": "garage_cover_target", "domain": domain})
        if any(term in entity_id for term in WATER_TARGET_TERMS):
            evidence.append({"field": path, "trigger": "water_control_target", "domain": domain})
    unique_evidence = {json.dumps(item, sort_keys=True): item for item in evidence}
    return (
        sorted(services),
        [unique_evidence[key] for key in sorted(unique_evidence)],
        sorted(warnings),
        targets,
    )


def _device_action_domains(config: dict[str, Any]) -> set[str]:
    """Return literal device-action domains from action roots only."""

    domains: set[str] = set()
    for _root_path, root in _action_roots(config):
        for value in _walk(root):
            if not isinstance(value, dict):
                continue
            domain = value.get("domain")
            if (
                isinstance(domain, str)
                and domain == domain.strip().lower()
                and domain
                and isinstance(value.get("device_id"), str)
                and isinstance(value.get("type"), str)
            ):
                domains.add(domain)
    return domains


def _bounded_helper_profile_values(
    values: Iterable[str],
) -> tuple[list[str], bool, int, str]:
    """Return a visible prefix plus count and full-set drift binding."""

    bounded: set[str] = set()
    clipped = False
    for value in values:
        encoded = value.encode("utf-8")
        if len(encoded) > _HELPER_PROFILE_VALUE_BYTES:
            clipped = True
            value = "oversized_sha256:" + hashlib.sha256(encoded).hexdigest()
        bounded.add(value)
    ordered = sorted(bounded)
    return (
        ordered[:_HELPER_PROFILE_LIMIT],
        clipped or len(ordered) > _HELPER_PROFILE_LIMIT,
        len(ordered),
        _effect_value_hash(ordered),
    )


def _effect_value_hash(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
            default=str,
        )
    except RecursionError:
        # Processing-failure evidence must itself remain bounded.  Hash a
        # deterministic structural prefix instead of recursing again through
        # an already over-depth value with repr().  The corresponding profile
        # is non-actionable, so this diagnostic fingerprint is not presented
        # as complete material evidence.
        digest = hashlib.sha256(b"bounded-overflow-v1")
        pending: list[tuple[str, Any, int]] = [("$", value, 0)]
        seen: set[int] = set()
        visited = 0
        while pending and visited < _HELPER_EFFECT_STRUCTURE_NODE_LIMIT:
            path, item, depth = pending.pop()
            visited += 1
            digest.update(path.encode("utf-8")[:_HELPER_PROFILE_VALUE_BYTES])
            digest.update(type(item).__name__.encode("utf-8"))
            if isinstance(item, (dict, list)):
                item_id = id(item)
                if item_id in seen:
                    digest.update(b":cycle")
                    continue
                seen.add(item_id)
            if depth >= _HELPER_EFFECT_STRUCTURE_DEPTH_LIMIT:
                digest.update(b":depth-bound")
                if isinstance(item, (dict, list)):
                    digest.update(str(len(item)).encode("ascii"))
                continue
            if isinstance(item, dict):
                keys = sorted(
                    item, key=lambda key: str(key).encode("utf-8")
                )
                digest.update(str(len(keys)).encode("ascii"))
                children = [
                    (f"{path}.{key}", item[key], depth + 1)
                    for key in keys
                ]
                pending.extend(reversed(children))
            elif isinstance(item, list):
                digest.update(str(len(item)).encode("ascii"))
                pending.extend(
                    (f"{path}[{index}]", child, depth + 1)
                    for index, child in reversed(tuple(enumerate(item)))
                )
            else:
                try:
                    scalar = json.dumps(
                        item,
                        ensure_ascii=True,
                        allow_nan=False,
                        default=lambda ignored: type(ignored).__name__,
                    )
                except (TypeError, ValueError, RecursionError):
                    scalar = type(item).__name__
                digest.update(
                    hashlib.sha256(scalar.encode("utf-8")).digest()
                )
        digest.update(str(len(pending)).encode("ascii"))
        return digest.hexdigest()
    except (TypeError, ValueError):
        encoded = repr(value)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _effect_path_is_sensitive(path: str) -> bool:
    lowered = path.lower()
    return any(term in lowered for term in _HELPER_EFFECT_SENSITIVE_TERMS)


def _bounded_effect_projection_values(
    values: Iterable[str],
) -> tuple[list[str], bool, int, str]:
    """Bound effect details while retaining every omitted value in a hash."""

    normalized: set[str] = set()
    clipped = False
    for value in values:
        encoded = value.encode("utf-8")
        if len(encoded) > _HELPER_PROFILE_VALUE_BYTES:
            clipped = True
            value = "oversized_sha256:" + hashlib.sha256(encoded).hexdigest()
        normalized.add(value)
    ordered = sorted(normalized)
    total_count = len(ordered)
    full_set_fingerprint = _effect_value_hash(ordered)
    if len(ordered) > _HELPER_EFFECT_PROJECTION_VALUE_LIMIT:
        clipped = True
        overflow = _effect_value_hash(
            ordered[_HELPER_EFFECT_PROJECTION_VALUE_LIMIT - 1 :]
        )
        ordered = [
            *ordered[: _HELPER_EFFECT_PROJECTION_VALUE_LIMIT - 1],
            "overflow_sha256:" + overflow,
        ]
    return ordered, clipped, total_count, full_set_fingerprint


def _effect_scalar_token(path: str, value: Any) -> str:
    leaf = path.rsplit(".", 1)[-1].split("[", 1)[0]
    if _effect_path_is_sensitive(path):
        rendered = "sha256:" + _effect_value_hash(value)
    elif isinstance(value, str):
        encoded = value.encode("utf-8")
        if _has_template(value):
            rendered = "dynamic_sha256:" + hashlib.sha256(encoded).hexdigest()
        elif (
            leaf not in _HELPER_EFFECT_SAFE_DATA_FIELDS
            or len(encoded) > _HELPER_PROFILE_VALUE_BYTES
        ):
            rendered = "sha256:" + hashlib.sha256(encoded).hexdigest()
        else:
            rendered = value
    elif value is None or isinstance(value, (bool, int, float)):
        try:
            rendered = json.dumps(value, allow_nan=False)
        except (TypeError, ValueError):
            rendered = "sha256:" + _effect_value_hash(value)
    else:
        rendered = "sha256:" + _effect_value_hash(value)
    return f"{path}={rendered}"


def _flatten_effect_data(
    value: Any,
    path: str,
    output: list[str],
    state: dict[str, Any],
    *,
    depth: int = 0,
) -> None:
    if depth > _HELPER_EFFECT_STRUCTURE_DEPTH_LIMIT:
        state["overflow"].update(
            f"{path}:{_effect_value_hash(value)}".encode("utf-8")
        )
        state["clipped"] = True
        state["processing_limit_exceeded"] = True
        return
    if isinstance(value, dict):
        for key in sorted(value, key=lambda item: str(item).encode("utf-8")):
            _flatten_effect_data(
                value[key],
                f"{path}.{key}",
                output,
                state,
                depth=depth + 1,
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _flatten_effect_data(
                item,
                f"{path}[{index}]",
                output,
                state,
                depth=depth + 1,
            )
        return
    state["total_count"] += 1
    token = _effect_scalar_token(path, value)
    if len(output) < _HELPER_EFFECT_PROJECTION_VALUE_LIMIT:
        output.append(token)
    else:
        state["overflow"].update(token.encode("utf-8"))
        state["clipped"] = True


def _normalize_effect_structure(
    value: Any,
    path: str,
    state: dict[str, Any],
    *,
    depth: int = 0,
) -> Any:
    if (
        depth > _HELPER_EFFECT_STRUCTURE_DEPTH_LIMIT
        or state["remaining"] <= 0
    ):
        state["clipped"] = True
        return {"bounded_sha256": _effect_value_hash(value)}
    state["remaining"] -= 1
    if isinstance(value, dict):
        result = {}
        for key in sorted(value, key=lambda item: str(item).encode("utf-8")):
            if state["remaining"] <= 0:
                state["clipped"] = True
                result["__bounded_sha256"] = _effect_value_hash(value)
                break
            key_text = str(key)
            if key_text in _HELPER_EFFECT_DISPLAY_ONLY_FIELDS:
                continue
            result[key_text] = _normalize_effect_structure(
                value[key],
                f"{path}.{key_text}",
                state,
                depth=depth + 1,
            )
        return result
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            if state["remaining"] <= 0:
                state["clipped"] = True
                result.append({"bounded_sha256": _effect_value_hash(value)})
                break
            result.append(
                _normalize_effect_structure(
                    item,
                    f"{path}[{index}]",
                    state,
                    depth=depth + 1,
                )
            )
        return result
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        if _has_template(value):
            return "dynamic_sha256:" + hashlib.sha256(encoded).hexdigest()
        if (
            _effect_path_is_sensitive(path)
            or (".data." in path and path.rsplit(".", 1)[-1]
                not in _HELPER_EFFECT_SAFE_DATA_FIELDS)
            or len(encoded) > _HELPER_PROFILE_VALUE_BYTES
        ):
            return "sha256:" + hashlib.sha256(encoded).hexdigest()
    return value


def _automation_effect_projection(
    config: dict[str, Any],
) -> dict[str, Any]:
    targets: list[str] = []
    data: list[str] = []
    data_state = {
        "overflow": hashlib.sha256(),
        "clipped": False,
        "processing_limit_exceeded": False,
        "total_count": 0,
    }
    for root_path, root in _action_roots(config):
        for path, step in _action_steps(root, root_path):
            families = set(_action_families(step))
            if families & {"call", "device"}:
                for field, selector, value in _target_selectors(step):
                    values = value if isinstance(value, list) else [value]
                    if not isinstance(values, list):
                        values = [values]
                    for index, item in enumerate(values):
                        item_path = f"{path}.{field}"
                        if len(values) > 1:
                            item_path += f"[{index}]"
                        if isinstance(item, str) and not _has_template(item):
                            token = item.lower()
                        else:
                            token = "sha256:" + _effect_value_hash(item)
                        targets.append(f"{item_path}|{selector}|{token}")
                for key in ("data", "data_template"):
                    if key in step:
                        _flatten_effect_data(
                            step[key],
                            f"{path}.{key}",
                            data,
                            data_state,
                        )
            if "scene" in families:
                scene = step.get("scene")
                token = (
                    scene.lower()
                    if isinstance(scene, str) and not _has_template(scene)
                    else "sha256:" + _effect_value_hash(scene)
                )
                targets.append(f"{path}.scene|entity_id|{token}")
            if "event" in families:
                event_name = step.get("event")
                targets.append(
                    f"{path}.event|event_type|sha256:"
                    + _effect_value_hash(event_name)
                )
                if "event_data" in step:
                    _flatten_effect_data(
                        step["event_data"],
                        f"{path}.event_data",
                        data,
                        data_state,
                    )
    if data_state["clipped"]:
        data.append("overflow_sha256:" + data_state["overflow"].hexdigest())
    (
        effect_targets,
        targets_clipped,
        effect_target_count,
        effect_targets_fingerprint,
    ) = _bounded_effect_projection_values(targets)
    (
        effect_data,
        data_clipped,
        _retained_data_count,
        effect_data_fingerprint,
    ) = _bounded_effect_projection_values(data)
    structure_state = {
        "remaining": _HELPER_EFFECT_STRUCTURE_NODE_LIMIT,
        "clipped": False,
    }
    structure = _normalize_effect_structure(
        {key: value for key, value in _action_roots(config)},
        "actions",
        structure_state,
    )
    structure_fingerprint = _effect_value_hash(structure)
    projection = {
        "model": _HELPER_EFFECT_PROJECTION_MODEL,
        "targets": effect_targets,
        "data": effect_data,
        "structure_fingerprint": structure_fingerprint,
        "target_count": effect_target_count,
        "targets_fingerprint": effect_targets_fingerprint,
        "data_count": int(data_state["total_count"]),
        "data_fingerprint": effect_data_fingerprint,
        # Structure normalization may compact a fully examined action graph
        # into subtree hashes.  That is presentation compaction, not stopped
        # semantic analysis.  Only a data-depth stop loses analytical detail.
        "processing_limit_exceeded": bool(
            data_state["processing_limit_exceeded"]
        ),
        "processing_overflow_fingerprint": (
            data_state["overflow"].hexdigest()
            if data_state["processing_limit_exceeded"]
            else None
        ),
        "projection_clipped": bool(
            targets_clipped
            or data_clipped
            or data_state["clipped"]
            or structure_state["clipped"]
        ),
    }
    return {
        **projection,
        "effect_fingerprint": _effect_value_hash(projection),
    }


def _effect_action_families(config: dict[str, Any]) -> set[str]:
    families: set[str] = set()
    for root_path, root in _action_roots(config):
        for _path, step in _action_steps(root, root_path):
            families.update(_action_families(step))
    return families


def _notification_effect_semantics(
    config: dict[str, Any],
) -> tuple[bool, bool, tuple[str, ...]]:
    """Classify static notification display and reviewed control subsets."""

    seen = False
    blocking_reasons: set[str] = set()
    reason_codes: set[str] = set()

    def template_proves_display_only(message: str) -> bool:
        """Prove one bounded template cannot select a notification control.

        Home Assistant notification controls occupy a closed reviewed set of
        exact values plus the ``command_`` and ``kiosk_`` namespaces.  A
        parsed literal prefix outside every such namespace proves display
        behavior without interpreting or rendering the dynamic suffix.  A
        template beginning with a dynamic value, a malformed template, or a
        prefix that could still form a control remains conservative.
        """

        if len(message.encode("utf-8")) > _HELPER_NOTIFICATION_TEXT_BYTES:
            return False
        try:
            parsed = _HELPER_TEMPLATE_PARSER.parse(message)
            if 1 + sum(1 for _ in parsed.find_all(nodes.Node)) > (
                _HELPER_NOTIFICATION_TEMPLATE_NODE_LIMIT
            ):
                return False
        except (TemplateSyntaxError, TypeError, ValueError, RecursionError):
            return False
        prefix = ""
        for statement in parsed.body:
            if not isinstance(statement, nodes.Output):
                continue
            for item in statement.nodes:
                if isinstance(item, nodes.TemplateData):
                    prefix += str(item.data)
                    normalized = prefix.strip().casefold()
                    if not normalized:
                        continue
                    exact_controls = (
                        _HELPER_REVIEWED_NONPHYSICAL_NOTIFICATION_CONTROLS
                        | _HELPER_EFFECTFUL_NOTIFICATION_CONTROLS
                    )
                    if any(
                        control.startswith(normalized)
                        for control in exact_controls
                    ):
                        return False
                    if any(
                        marker.startswith(normalized)
                        or normalized.startswith(marker)
                        for marker in (
                            _HELPER_DYNAMIC_NOTIFICATION_CONTROL_PREFIXES
                        )
                    ):
                        return False
                    return True
                # The first material output is dynamic, so it can select any
                # control value and cannot be certified as display-only.
                return False
        return False

    def classify_message(message: str) -> str:
        if _has_template(message):
            return (
                "ordinary_display"
                if template_proves_display_only(message)
                else "dynamic_unresolved"
            )
        normalized = message.strip().lower()
        if normalized in (
            _HELPER_REVIEWED_NONPHYSICAL_NOTIFICATION_CONTROLS
        ):
            return "reviewed_nonphysical_control"
        if (
            normalized in _HELPER_EFFECTFUL_NOTIFICATION_CONTROLS
            or normalized.startswith("command_")
            or normalized.startswith("kiosk_")
        ):
            return "effectful_control"
        return "ordinary_display"

    def reviewed_control_payload_is_bounded(
        message: str, payload: dict[str, Any]
    ) -> bool:
        normalized = message.strip().lower()
        if normalized != "clear_notification":
            return set(payload) == {"message"}
        if set(payload) == {"message"}:
            return True
        if set(payload) != {"message", "data"}:
            return False
        control_data = payload.get("data")
        if not isinstance(control_data, dict) or set(control_data) != {
            "tag"
        }:
            return False
        tag = control_data.get("tag")
        return bool(
            isinstance(tag, str)
            and tag
            and not _has_template(tag)
            and len(tag.encode("utf-8"))
            <= _HELPER_PROFILE_VALUE_BYTES
        )

    def note_message(message: str) -> str:
        kind = classify_message(message)
        if kind == "reviewed_nonphysical_control":
            reason_codes.add(
                "reviewed_nonphysical_notification_control"
            )
        elif kind == "effectful_control":
            code = (
                "notification_command_effect"
                if message.strip().lower().startswith("command_")
                else "notification_control_effect"
            )
            blocking_reasons.add(code)
            reason_codes.add(code)
        elif kind == "dynamic_unresolved":
            blocking_reasons.add("dynamic_notification_content")
            reason_codes.add("dynamic_notification_content")
        return kind

    for root_path, root in _action_roots(config):
        for _path, step in _action_steps(root, root_path):
            service = step.get("service", step.get("action"))
            if (
                not isinstance(service, str)
                or _has_template(service)
                or not service.startswith("notify.")
            ):
                continue
            seen = True
            direct_message = step.get("message")
            if isinstance(direct_message, str):
                note_message(direct_message)
            if "data_template" in step:
                blocking_reasons.add("dynamic_notification_content")
                reason_codes.add("dynamic_notification_content")
            if "target" in step:
                blocking_reasons.add("notification_extension_unreviewed")
                reason_codes.add("notification_extension_unreviewed")
            payload = step.get("data")
            if not isinstance(payload, dict):
                blocking_reasons.add("notification_payload_unproven")
                reason_codes.add("notification_payload_unproven")
                continue
            message = payload.get("message")
            if not isinstance(message, str) or not message:
                blocking_reasons.add("notification_payload_unproven")
                reason_codes.add("notification_payload_unproven")
            elif len(message.encode("utf-8")) > _HELPER_NOTIFICATION_TEXT_BYTES:
                blocking_reasons.add("notification_payload_unproven")
                reason_codes.add("notification_payload_unproven")
            else:
                kind = note_message(message)
                if kind == "reviewed_nonphysical_control":
                    if not reviewed_control_payload_is_bounded(
                        message, payload
                    ):
                        blocking_reasons.add(
                            "notification_extension_unreviewed"
                        )
                        reason_codes.add(
                            "notification_extension_unreviewed"
                        )
                elif set(payload) - _HELPER_NOTIFICATION_DATA_FIELDS:
                    blocking_reasons.add(
                        "notification_extension_unreviewed"
                    )
                    reason_codes.add(
                        "notification_extension_unreviewed"
                    )
            title = payload.get("title")
            if title is not None:
                if not isinstance(title, str) or _has_template(title):
                    blocking_reasons.add("dynamic_notification_content")
                    reason_codes.add("dynamic_notification_content")
                elif len(title.encode("utf-8")) > (
                    _HELPER_NOTIFICATION_TEXT_BYTES
                ):
                    blocking_reasons.add("notification_payload_unproven")
                    reason_codes.add("notification_payload_unproven")
    return seen, bool(seen and not blocking_reasons), tuple(
        sorted(reason_codes, key=lambda item: item.encode("utf-8"))
    )


def _unresolved_consequential_action_target(config: dict[str, Any]) -> bool:
    """Return whether a reviewed direct effect lacks bounded target scope.

    Target requirements are assessed per service step.  A targetless notify
    step must not make a separately exact lock or cover action incomplete,
    while a targetless direct entity service remains potentially broad.
    """

    for root_path, root in _action_roots(config):
        for _path, step in _action_steps(root, root_path):
            service = step.get("service", step.get("action"))
            if (
                not isinstance(service, str)
                or not service.strip()
                or _has_template(service)
                or "." not in service
            ):
                continue
            normalized = service.strip().lower()
            domain = normalized.split(".", 1)[0]
            if (
                domain not in _HELPER_DIRECT_EFFECT_DOMAINS
                and normalized not in _HELPER_GENERIC_EFFECT_SERVICES
            ):
                continue
            if not list(_target_selectors(step)):
                return True
    return False


def _processing_failure_action_profile(
    action_coverage: dict[str, Any],
) -> dict[str, Any]:
    """Return a bounded, fail-closed profile after structural analysis stops."""

    reason = str(
        action_coverage.get("reason")
        or "action_analysis_processing_limit_exceeded"
    )
    overflow_fingerprint = action_coverage.get("overflow_fingerprint")
    empty_fingerprint = _effect_value_hash([])
    structure_fingerprint = _effect_value_hash(
        {
            "model": "bounded-processing-failure-v1",
            "reason": reason,
            "overflow_fingerprint": overflow_fingerprint,
        }
    )
    effect_projection = {
        "model": _HELPER_EFFECT_PROJECTION_MODEL,
        "targets": [],
        "data": [],
        "structure_fingerprint": structure_fingerprint,
        "target_count": 0,
        "targets_fingerprint": empty_fingerprint,
        "data_count": 0,
        "data_fingerprint": empty_fingerprint,
        "processing_limit_exceeded": True,
        "processing_overflow_fingerprint": overflow_fingerprint,
        "projection_clipped": False,
    }
    effect_projection_fingerprint = _effect_value_hash(effect_projection)
    normalized = {
        "model": "automation-action-consequence-v3",
        "risk_level": "high",
        "physical_consequence": "unknown",
        "complete": False,
        "analysis_complete": False,
        "semantic_complete": False,
        "presentation_truncated": False,
        "processing_limit_exceeded": True,
        "processing_limit_reason": reason,
        "processing_observed_action_step_count": int(
            action_coverage.get("observed_step_count", 0)
        ),
        "processing_action_step_limit": int(
            action_coverage.get("step_limit", 0)
        ),
        "processing_action_depth_limit": int(
            action_coverage.get("depth_limit", 0)
        ),
        "processing_observed_effect_node_count": int(
            action_coverage.get("observed_effect_node_count", 0)
        ),
        "processing_effect_node_limit": int(
            action_coverage.get("effect_node_limit", 0)
        ),
        "processing_effect_depth_limit": int(
            action_coverage.get("effect_depth_limit", 0)
        ),
        "processing_overflow_fingerprint": overflow_fingerprint,
        "truncated": False,
        "action_domains": [],
        "action_domain_count": 0,
        "action_domains_fingerprint": empty_fingerprint,
        "services": [],
        "service_count": 0,
        "services_fingerprint": empty_fingerprint,
        "reason_codes": ["action_processing_limit_exceeded"],
        "reason_code_count": 1,
        "reason_codes_fingerprint": _effect_value_hash(
            ["action_processing_limit_exceeded"]
        ),
        "effect_projection_model": _HELPER_EFFECT_PROJECTION_MODEL,
        "effect_targets": [],
        "effect_target_count": 0,
        "effect_targets_fingerprint": empty_fingerprint,
        "effect_data": [],
        "effect_data_count": 0,
        "effect_data_fingerprint": empty_fingerprint,
        "effect_structure_fingerprint": structure_fingerprint,
        "effect_projection_fingerprint": effect_projection_fingerprint,
        "effect_projection_clipped": False,
    }
    encoded = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return {
        **normalized,
        "evidence_fingerprint": hashlib.sha256(
            encoded.encode("utf-8")
        ).hexdigest(),
    }


def automation_action_consequence_profile(
    config: dict[str, Any],
) -> dict[str, Any]:
    """Project bounded downstream consequence evidence using the F2 parser.

    This is not a second automation parser or approval model. It reuses the
    structure-first F2 action analysis and returns only the normalized facts
    needed to assess an exact helper dependency.
    """

    action_coverage = _bounded_action_analysis_coverage(config)
    if not action_coverage["complete"]:
        return _processing_failure_action_profile(action_coverage)

    services, evidence, warnings, targets = _structured_analysis(config)
    service_domains = {
        service.split(".", 1)[0]
        for service in services
        if "." in service
    }
    target_domains = {
        target.split(".", 1)[0]
        for target in targets
        if "." in target and not _has_template(target)
    }
    device_domains = _device_action_domains(config)
    action_domains = service_domains | target_domains | device_domains
    action_families = _effect_action_families(config)
    effect = _automation_effect_projection(config)
    (
        notification_present,
        notification_proven_benign,
        notification_reason_codes,
    ) = _notification_effect_semantics(config)
    triggers = {
        str(item.get("trigger"))
        for item in evidence
        if isinstance(item, dict) and isinstance(item.get("trigger"), str)
    }

    safety_critical = bool(
        action_domains & _HELPER_SAFETY_CRITICAL_DOMAINS
        or triggers
        & {
            "garage_cover_target",
            "safety_critical_service",
            "sensitive_blueprint_input",
            "sensitive_entity_domain",
            "water_control_target",
        }
    )
    transitive = bool(
        action_domains & _HELPER_TRANSITIVE_EFFECT_DOMAINS
        or action_families & _HELPER_TRANSITIVE_ACTION_FAMILIES
    )
    broad_selector = bool(
        triggers
        & {
            "area_target",
            "device_target",
            "floor_target",
            "label_target",
            "mixed_target_selector",
            "target_list",
        }
    )
    generic_services = set(services) & _HELPER_GENERIC_EFFECT_SERVICES
    generic_target_known = bool(
        generic_services
        and target_domains
        and target_domains <= _HELPER_DIRECT_EFFECT_DOMAINS
        and not broad_selector
    )
    generic_unknown = bool(generic_services and not generic_target_known)
    unreviewed_homeassistant = any(
        service.startswith("homeassistant.")
        and service not in _HELPER_GENERIC_EFFECT_SERVICES
        for service in services
    )
    recognized_domains = (
        _HELPER_DIRECT_EFFECT_DOMAINS
        | _HELPER_TRANSITIVE_EFFECT_DOMAINS
        | _HELPER_PROVEN_BENIGN_SERVICE_DOMAINS
        | {"homeassistant"}
    )
    unrecognized = bool(
        action_domains - recognized_domains or unreviewed_homeassistant
    )
    known_direct = bool(action_domains & _HELPER_DIRECT_EFFECT_DOMAINS)
    consequential = bool(
        safety_critical
        or known_direct
        or generic_target_known
        or "high_risk_service" in triggers
    )
    (
        all_domains,
        domains_truncated,
        action_domain_count,
        action_domains_fingerprint,
    ) = _bounded_helper_profile_values(action_domains)
    (
        all_services,
        services_truncated,
        service_count,
        services_fingerprint,
    ) = _bounded_helper_profile_values(services)
    processing_limit_exceeded = bool(
        not action_coverage["complete"]
        or effect["processing_limit_exceeded"]
    )
    processing_overflow_fingerprints = [
        item
        for item in (
            action_coverage["overflow_fingerprint"],
            effect["processing_overflow_fingerprint"],
        )
        if isinstance(item, str) and item
    ]
    processing_overflow_fingerprint = (
        processing_overflow_fingerprints[0]
        if len(processing_overflow_fingerprints) == 1
        else _effect_value_hash(processing_overflow_fingerprints)
        if processing_overflow_fingerprints
        else None
    )
    unresolved_effect = bool(
        transitive
        or generic_unknown
        or unrecognized
        or (notification_present and not notification_proven_benign)
        or warnings
        or _unresolved_consequential_action_target(config)
    )
    analysis_complete = not processing_limit_exceeded
    semantic_complete = not unresolved_effect
    incomplete = not (analysis_complete and semantic_complete)

    consequence = (
        "safety_critical"
        if safety_critical
        else "unknown"
        if unresolved_effect
        else "direct"
        if consequential
        else "unknown"
        if incomplete
        else "none"
    )
    reasons = []
    if safety_critical:
        reasons.append("safety_critical_action_family")
    elif consequential:
        reasons.append("consequential_action_family")
    elif unresolved_effect:
        reasons.append("action_effect_unresolved")
    elif not services and not device_domains and not transitive:
        reasons.append("no_effect_action_detected")
    else:
        reasons.append("proven_benign_action_family")
    reasons.extend(notification_reason_codes)
    if transitive:
        reasons.append("transitive_action_target_unresolved")
    if generic_unknown:
        reasons.append("generic_target_effect_unresolved")
    if unrecognized:
        reasons.append("unrecognized_action_effect")
    if broad_selector:
        reasons.append("broad_target_selector")
    if warnings:
        reasons.append("action_structure_incomplete")
    if processing_limit_exceeded:
        reasons.append("action_processing_limit_exceeded")

    (
        all_reasons,
        reasons_truncated,
        reason_code_count,
        reason_codes_fingerprint,
    ) = _bounded_helper_profile_values(reasons)
    display_lists_truncated = any(
        (domains_truncated, services_truncated, reasons_truncated)
    )
    presentation_truncated = bool(
        display_lists_truncated or effect["projection_clipped"]
    )
    complete = not incomplete
    normalized = {
        "model": "automation-action-consequence-v3",
        "risk_level": "high" if consequence != "none" or not complete else "low",
        "physical_consequence": consequence,
        "complete": complete,
        "analysis_complete": analysis_complete,
        "semantic_complete": semantic_complete,
        "presentation_truncated": presentation_truncated,
        "processing_limit_exceeded": processing_limit_exceeded,
        "processing_limit_reason": (
            action_coverage["reason"]
            or (
                "effect_data_depth_limit_exceeded"
                if effect["processing_limit_exceeded"]
                else None
            )
        ),
        "processing_observed_action_step_count": action_coverage[
            "observed_step_count"
        ],
        "processing_action_step_limit": action_coverage["step_limit"],
        "processing_action_depth_limit": action_coverage["depth_limit"],
        "processing_observed_effect_node_count": action_coverage[
            "observed_effect_node_count"
        ],
        "processing_effect_node_limit": action_coverage[
            "effect_node_limit"
        ],
        "processing_effect_depth_limit": action_coverage[
            "effect_depth_limit"
        ],
        "processing_overflow_fingerprint": (
            processing_overflow_fingerprint
        ),
        # Compatibility: this field historically described only the three
        # bounded display lists.  The additive field above describes all
        # presentation compaction, including effect-detail projection.
        "truncated": display_lists_truncated,
        "action_domains": all_domains,
        "action_domain_count": action_domain_count,
        "action_domains_fingerprint": action_domains_fingerprint,
        "services": all_services,
        "service_count": service_count,
        "services_fingerprint": services_fingerprint,
        "reason_codes": all_reasons,
        "reason_code_count": reason_code_count,
        "reason_codes_fingerprint": reason_codes_fingerprint,
        "effect_projection_model": effect["model"],
        "effect_targets": effect["targets"],
        "effect_target_count": effect["target_count"],
        "effect_targets_fingerprint": effect["targets_fingerprint"],
        "effect_data": effect["data"],
        "effect_data_count": effect["data_count"],
        "effect_data_fingerprint": effect["data_fingerprint"],
        "effect_structure_fingerprint": effect[
            "structure_fingerprint"
        ],
        "effect_projection_fingerprint": effect[
            "effect_fingerprint"
        ],
        "effect_projection_clipped": effect[
            "projection_clipped"
        ],
    }
    encoded = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return {
        **normalized,
        "evidence_fingerprint": hashlib.sha256(
            encoded.encode("utf-8")
        ).hexdigest(),
    }


def _is_high_service(service: str) -> bool:
    if service in HIGH_EXACT_SERVICES or service.startswith(HIGH_SERVICE_PREFIXES):
        return True
    action_name = service.rsplit(".", 1)[-1]
    return action_name in DESTRUCTIVE_ACTION_NAMES and service.split(".", 1)[0] in {
        "homeassistant", "hassio", "automation", "script", "system_log"
    }


def classify_risk(
    operation: ChangeOperation,
    diff: dict[str, Any],
    proposed: dict[str, Any],
) -> ChangeRiskAssessment:
    fields = {item["field"] for item in diff.get("changed_fields", [])}
    services, evidence, warnings, _targets = _structured_analysis(proposed)
    reasons: list[str] = []
    behavioral_change = operation == ChangeOperation.CREATE_AUTOMATION or bool(
        fields
        & {
            "triggers", "conditions", "actions", "variables", "trace_settings",
            "blueprint_usage", "mode", "maximum_runs",
        }
    )

    if behavioral_change and any(item["trigger"] in {
        "high_risk_service", "sensitive_entity_domain", "sensitive_blueprint_input",
        "garage_cover_target", "water_control_target",
    } for item in evidence):
        reasons.append("Structured action or target requires high-risk review")
    if behavioral_change and _has_unrestricted_action_target(proposed):
        reasons.append("Broad or unrestricted target detected")
        evidence.append({"field": "action.target", "trigger": "unrestricted_target"})
    if behavioral_change and _has_broad_action_target(proposed):
        reasons.append("Broad entity or area target detected")
        evidence.append({"field": "action.target", "trigger": "large_target_set"})
    if reasons:
        return ChangeRiskAssessment(
            RiskLevel.HIGH,
            sorted(set(reasons)),
            False,
            evidence=_deduplicate_evidence(evidence),
            warnings=warnings,
        )

    medium_reasons: list[str] = []
    if operation == ChangeOperation.CREATE_AUTOMATION:
        medium_reasons.append("Creating a new behavior-producing automation")
    for field in ("triggers", "conditions", "mode", "maximum_runs", "blueprint_usage"):
        if field in fields:
            medium_reasons.append(f"Behavior-impacting {field} change")
    if "actions" in fields and any(service.startswith(MEDIUM_SERVICE_PREFIXES) for service in services):
        medium_reasons.append("Physical-device action detected")
    if "actions" in fields and any(service.startswith(("notify.", "climate.")) for service in services):
        medium_reasons.append("Recipient or environmental-control behavior may change")
    if warnings:
        medium_reasons.append("Actionable service or target structure could not be fully resolved")
    if medium_reasons:
        return ChangeRiskAssessment(
            RiskLevel.MEDIUM,
            sorted(set(medium_reasons)),
            True,
            evidence=_deduplicate_evidence(evidence),
            warnings=warnings,
        )

    return ChangeRiskAssessment(
        RiskLevel.LOW,
        ["Only low-impact metadata, logging, notification text, or minor timing changed"],
        True,
        evidence=[],
        warnings=[],
    )


def _deduplicate_evidence(evidence: list[dict[str, str]]) -> list[dict[str, str]]:
    values = {json.dumps(item, sort_keys=True): item for item in evidence}
    return [values[key] for key in sorted(values)]


def _has_broad_action_target(config: dict[str, Any]) -> bool:
    for root_path, root in _action_roots(config):
        for _path, node in _action_nodes(root, root_path):
            for _field, selector, value in _target_selectors(node):
                if (
                    selector in {"entity_id", "device_id"}
                    and isinstance(value, list)
                    and len(value) > 10
                ):
                    return True
                if (
                    selector == "area_id"
                    and isinstance(value, list)
                    and len(value) > 3
                ):
                    return True
    return False


def _has_unrestricted_action_target(config: dict[str, Any]) -> bool:
    for root_path, root in _action_roots(config):
        for _path, node in _action_nodes(root, root_path):
            for key in ("entity_id", "device_id", "area_id"):
                containers = [node.get(key)]
                if isinstance(node.get("target"), dict):
                    containers.append(node["target"].get(key))
                if key == "entity_id" and isinstance(node.get("data"), dict):
                    containers.append(node["data"].get(key))
                for container in containers:
                    values = [container] if isinstance(container, str) else container
                    if isinstance(values, list) and any(
                        isinstance(item, str) and item.lower() in {"all", "*"}
                        for item in values
                    ):
                        return True
    return False
