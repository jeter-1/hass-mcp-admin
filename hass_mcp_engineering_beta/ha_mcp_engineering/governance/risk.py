"""Deterministic, structure-first automation change risk classification."""

from __future__ import annotations

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
_TARGET_SELECTORS = ("entity_id", "device_id", "area_id")


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
