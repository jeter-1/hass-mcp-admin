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
DESTRUCTIVE_ACTION_NAMES = {"delete", "remove", "shutdown", "reboot", "restart", "stop"}
MEDIUM_SERVICE_PREFIXES = ("light.", "switch.", "climate.", "cover.", "fan.")
SENSITIVE_ENTITY_DOMAINS = {"lock", "alarm_control_panel", "valve"}
WATER_TARGET_TERMS = {"water", "shutoff", "shut_off", "main_valve"}


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


def _action_nodes(value: Any, path: str) -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        if "service" in value or "action" in value:
            yield path, value
        for key, item in value.items():
            if key in {"choose", "sequence", "default", "then", "else", "repeat", "parallel"}:
                yield from _action_nodes(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _action_nodes(item, f"{path}[{index}]")


def _target_values(node: dict[str, Any]) -> Iterable[tuple[str, str]]:
    containers: list[tuple[str, Any]] = [("target", node.get("target"))]
    if "entity_id" in node:
        containers.append(("entity_id", node.get("entity_id")))
    data = node.get("data")
    if isinstance(data, dict) and "entity_id" in data:
        containers.append(("data.entity_id", data.get("entity_id")))
    for field, container in containers:
        if isinstance(container, dict):
            items = container.get("entity_id", [])
        else:
            items = container
        if isinstance(items, str):
            items = [items]
        if isinstance(items, list):
            for item in items:
                if isinstance(item, str):
                    yield field, item.lower()


def _blueprint_targets(config: dict[str, Any]) -> Iterable[tuple[str, str]]:
    blueprint = config.get("use_blueprint")
    inputs = blueprint.get("input") if isinstance(blueprint, dict) else None
    if not isinstance(inputs, dict):
        return
    for key, value in inputs.items():
        for item in _walk(value):
            if isinstance(item, str) and "." in item and not _has_template(item):
                yield f"use_blueprint.input.{key}", item.lower()


def _structured_analysis(config: dict[str, Any]) -> tuple[list[str], list[dict[str, str]], list[str], list[str]]:
    services: set[str] = set()
    evidence: list[dict[str, str]] = []
    warnings: set[str] = set()
    targets: list[str] = []
    for root_path, root in _action_roots(config):
        for path, node in _action_nodes(root, root_path):
            service = node.get("service") or node.get("action")
            if isinstance(service, str):
                if _has_template(service):
                    warnings.add("Dynamic service action could not be bounded structurally.")
                    evidence.append({"field": f"{path}.service", "trigger": "unresolved_dynamic_service"})
                else:
                    normalized = service.lower().strip()
                    services.add(normalized)
                    if _is_high_service(normalized):
                        evidence.append({"field": f"{path}.service", "trigger": "high_risk_service", "service": normalized})
            for field, entity_id in _target_values(node):
                targets.append(entity_id)
                if _has_template(entity_id):
                    warnings.add("Dynamic action target could not be bounded structurally.")
                    evidence.append({"field": f"{path}.{field}", "trigger": "unresolved_dynamic_target"})
                    continue
                domain = entity_id.split(".", 1)[0]
                if domain in SENSITIVE_ENTITY_DOMAINS:
                    evidence.append({"field": f"{path}.{field}", "trigger": "sensitive_entity_domain", "domain": domain})
                if domain == "cover" and "garage" in entity_id:
                    evidence.append({"field": f"{path}.{field}", "trigger": "garage_cover_target", "domain": domain})
                if any(term in entity_id for term in WATER_TARGET_TERMS):
                    evidence.append({"field": f"{path}.{field}", "trigger": "water_control_target", "domain": domain})
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
    return sorted(services), [unique_evidence[key] for key in sorted(unique_evidence)], sorted(warnings), targets


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
    if behavioral_change and any(_has_broad_target(root) for _, root in _action_roots(proposed)):
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


def _has_broad_target(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"entity_id", "device_id"} and isinstance(item, list) and len(item) > 10:
                return True
            if key == "area_id" and isinstance(item, list) and len(item) > 3:
                return True
            if _has_broad_target(item):
                return True
    elif isinstance(value, list):
        return any(_has_broad_target(item) for item in value)
    return False


def _has_unrestricted_action_target(config: dict[str, Any]) -> bool:
    for root_path, root in _action_roots(config):
        for _path, node in _action_nodes(root, root_path):
            for key in ("entity_id", "device_id", "area_id"):
                containers = [node.get(key)]
                if isinstance(node.get("target"), dict):
                    containers.append(node["target"].get(key))
                for container in containers:
                    values = [container] if isinstance(container, str) else container
                    if isinstance(values, list) and any(
                        isinstance(item, str) and item.lower() in {"all", "*"}
                        for item in values
                    ):
                        return True
    return False
