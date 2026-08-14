"""Deterministic, structure-first automation change risk classification."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

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
_HELPER_GENERIC_EFFECT_SERVICES = frozenset(
    {
        "homeassistant.toggle",
        "homeassistant.turn_off",
        "homeassistant.turn_on",
    }
)
_HELPER_TRANSITIVE_ACTION_FAMILIES = frozenset({"event", "scene"})
_HELPER_PROFILE_LIMIT = 32
_HELPER_PROFILE_VALUE_BYTES = 256
_HELPER_EFFECT_PROJECTION_MODEL = "automation-action-effect-v2"
_HELPER_EFFECT_STRUCTURE_NODE_LIMIT = 512
_HELPER_EFFECT_STRUCTURE_DEPTH_LIMIT = 16
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


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


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
) -> tuple[list[str], bool]:
    """Return deterministic bounded values and whether evidence was clipped."""

    bounded: set[str] = set()
    clipped = False
    for value in values:
        encoded = value.encode("utf-8")
        if len(encoded) > _HELPER_PROFILE_VALUE_BYTES:
            clipped = True
            value = "oversized_sha256:" + hashlib.sha256(encoded).hexdigest()
        bounded.add(value)
    ordered = sorted(bounded)
    return ordered[:_HELPER_PROFILE_LIMIT], (
        clipped or len(ordered) > _HELPER_PROFILE_LIMIT
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
    except (TypeError, ValueError):
        encoded = repr(value)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _effect_path_is_sensitive(path: str) -> bool:
    lowered = path.lower()
    return any(term in lowered for term in _HELPER_EFFECT_SENSITIVE_TERMS)


def _bounded_effect_projection_values(
    values: Iterable[str],
) -> tuple[list[str], bool]:
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
    if len(ordered) > _HELPER_PROFILE_LIMIT:
        clipped = True
        overflow = _effect_value_hash(ordered[_HELPER_PROFILE_LIMIT - 1 :])
        ordered = [
            *ordered[: _HELPER_PROFILE_LIMIT - 1],
            "overflow_sha256:" + overflow,
        ]
    return ordered, clipped


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
        return
    if state["remaining"] <= 0:
        state["overflow"].update(
            f"{path}:{_effect_value_hash(value)}".encode("utf-8")
        )
        state["clipped"] = True
        return
    if isinstance(value, dict):
        for key in sorted(value, key=lambda item: str(item).encode("utf-8")):
            if state["remaining"] <= 0:
                state["overflow"].update(
                    f"{path}:{_effect_value_hash(value)}".encode("utf-8")
                )
                state["clipped"] = True
                return
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
            if state["remaining"] <= 0:
                state["overflow"].update(
                    f"{path}:{_effect_value_hash(value)}".encode("utf-8")
                )
                state["clipped"] = True
                return
            _flatten_effect_data(
                item,
                f"{path}[{index}]",
                output,
                state,
                depth=depth + 1,
            )
        return
    output.append(_effect_scalar_token(path, value))
    state["remaining"] -= 1


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
        "remaining": _HELPER_PROFILE_LIMIT - 1,
        "overflow": hashlib.sha256(),
        "clipped": False,
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
    effect_targets, targets_clipped = _bounded_effect_projection_values(
        targets
    )
    effect_data, data_clipped = _bounded_effect_projection_values(data)
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

    def classify_message(message: str) -> str:
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
            if isinstance(direct_message, str) and _has_template(
                direct_message
            ):
                blocking_reasons.add("dynamic_notification_content")
                reason_codes.add("dynamic_notification_content")
            elif isinstance(direct_message, str):
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
            elif _has_template(message):
                blocking_reasons.add("dynamic_notification_content")
                reason_codes.add("dynamic_notification_content")
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


def automation_action_consequence_profile(
    config: dict[str, Any],
) -> dict[str, Any]:
    """Project bounded downstream consequence evidence using the F2 parser.

    This is not a second automation parser or approval model. It reuses the
    structure-first F2 action analysis and returns only the normalized facts
    needed to assess an exact helper dependency.
    """

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
    all_domains, domains_truncated = _bounded_helper_profile_values(
        action_domains
    )
    all_services, services_truncated = _bounded_helper_profile_values(
        services
    )
    unresolved_effect = bool(
        transitive
        or generic_unknown
        or unrecognized
        or (notification_present and not notification_proven_benign)
        or warnings
        or (
            consequential
            and "omitted_action_target" in triggers
        )
    )
    incomplete = bool(
        unresolved_effect or domains_truncated or services_truncated
    )

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

    all_reasons, reasons_truncated = _bounded_helper_profile_values(reasons)
    truncated = any(
        (domains_truncated, services_truncated, reasons_truncated)
    )
    complete = not incomplete and not truncated
    normalized = {
        "model": "automation-action-consequence-v2",
        "risk_level": "high" if consequence != "none" or not complete else "low",
        "physical_consequence": consequence,
        "complete": complete,
        "truncated": truncated,
        "action_domains": all_domains,
        "services": all_services,
        "reason_codes": all_reasons,
        "effect_projection_model": effect["model"],
        "effect_targets": effect["targets"],
        "effect_data": effect["data"],
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
