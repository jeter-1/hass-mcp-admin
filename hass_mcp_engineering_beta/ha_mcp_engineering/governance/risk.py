"""Deterministic automation change risk classification."""

from __future__ import annotations

from typing import Any, Iterable

from .models import ChangeOperation, ChangeRiskAssessment, RiskLevel


HIGH_SERVICE_PREFIXES = (
    "lock.",
    "alarm_control_panel.",
    "homeassistant.restart",
    "homeassistant.stop",
    "hassio.",
    "automation.",
    "script.",
    "water_heater.turn_off",
    "valve.close",
)
HIGH_TERMS = (
    "garage",
    "door_lock",
    "water",
    "security_bypass",
    "alarm",
    "smoke",
    "fire",
    "safety",
)
MEDIUM_SERVICE_PREFIXES = ("light.", "switch.", "climate.", "cover.", "fan.")


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _services(config: dict[str, Any]) -> list[str]:
    found = []
    for value in _walk(config.get("action", config.get("actions", []))):
        if isinstance(value, dict):
            service = value.get("service") or value.get("action")
            if isinstance(service, str):
                found.append(service.lower())
    return sorted(set(found))


def _strings(config: dict[str, Any]) -> list[str]:
    return [value.lower() for value in _walk(config) if isinstance(value, str)]


def classify_risk(
    operation: ChangeOperation,
    diff: dict[str, Any],
    proposed: dict[str, Any],
) -> ChangeRiskAssessment:
    fields = {item["field"] for item in diff.get("changed_fields", [])}
    services = _services(proposed)
    strings = _strings(proposed)
    reasons: list[str] = []
    behavioral_change = operation == ChangeOperation.CREATE_AUTOMATION or bool(
        fields
        & {
            "triggers",
            "conditions",
            "actions",
            "variables",
            "trace_settings",
            "blueprint_usage",
            "mode",
            "maximum_runs",
        }
    )

    high_services = [service for service in services if service.startswith(HIGH_SERVICE_PREFIXES)]
    if behavioral_change and high_services:
        reasons.append("Safety-sensitive or infrastructure service action: " + ", ".join(high_services))
    if behavioral_change and any(term in text for term in HIGH_TERMS for text in strings):
        reasons.append("Safety- or security-sensitive target detected")
    if behavioral_change and any(
        isinstance(value, str) and value.lower() in {"all", "*"}
        for value in _walk(proposed.get("target", proposed))
    ):
        reasons.append("Broad or unrestricted target detected")
    if behavioral_change and _has_broad_target(proposed):
        reasons.append("Broad entity or area target detected")
    if behavioral_change and (
        any("template" in key.lower() and isinstance(value, str) for key, value in _key_values(proposed))
        or any("{{" in value or "{%" in value for value in strings)
    ):
        reasons.append("Unrestricted template content requires high-risk review")
    if reasons:
        return ChangeRiskAssessment(RiskLevel.HIGH, sorted(set(reasons)), False)

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
    if medium_reasons:
        return ChangeRiskAssessment(RiskLevel.MEDIUM, sorted(set(medium_reasons)), True)

    return ChangeRiskAssessment(
        RiskLevel.LOW,
        ["Only low-impact metadata, logging, notification text, or minor timing changed"],
        True,
    )


def _key_values(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _key_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _key_values(item)


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
