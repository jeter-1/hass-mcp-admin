"""Static exact-reference extraction for Home Assistant configuration objects."""

from __future__ import annotations

from dataclasses import replace
import json
import re
from typing import Any, Iterable

from ..logging_config import redact_data
from .models import DependencyFinding, DynamicReference, evidence_id


ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
ENTITY_TOKEN = re.compile(r"(?<![a-z0-9_])([a-z0-9_]+\.[a-z0-9_]+)(?![a-z0-9_])", re.I)
HELPER_LITERAL = re.compile(
    r"(?:states|is_state|state_attr|expand)\(\s*(['\"])([a-z0-9_]+\.[a-z0-9_]+)\1",
    re.I,
)
STATES_DOT = re.compile(r"states\.([a-z0-9_]+)\.([a-z0-9_]+)", re.I)
FREE_TEXT_KEYS = {"alias", "description", "message", "title", "name", "friendly_name", "event_type"}
TEMPLATE_KEYS = {"value_template", "template", "availability", "state", "condition", "until", "while"}


def valid_entity_id(value: str) -> bool:
    return bool(ENTITY_ID.fullmatch(value))


def extract_document(
    *,
    source_type: str,
    source_id: str,
    config: dict[str, Any],
    source_entity_id: str | None = None,
    source_name: str | None = None,
    source_state: str | None = None,
    secret: str = "",
) -> tuple[list[DependencyFinding], list[DynamicReference]]:
    findings: list[DependencyFinding] = []
    dynamic: list[DynamicReference] = []
    blueprint = config.get("use_blueprint") if isinstance(config, dict) else None
    blueprint_path = blueprint.get("path") if isinstance(blueprint, dict) else None

    def add(entity_id: str, relation: str, path: str, *, match_type: str = "structured_exact", blueprint_input: str | None = None, excerpt: str | None = None):
        normalized = entity_id.strip().lower()
        if not valid_entity_id(normalized):
            return
        eid = evidence_id(source_type, source_id, normalized, relation, path, blueprint_input)
        findings.append(
            DependencyFinding(
                evidence_id=eid,
                target_entity_id=normalized,
                source_type=source_type,
                source_id=source_id,
                source_entity_id=source_entity_id,
                source_name=_bounded(source_name, secret=secret),
                relation=relation,
                config_path=path,
                confidence="exact" if match_type != "blueprint_resolved" else "resolved",
                match_type=match_type,
                blueprint_path=_bounded(blueprint_path, 256, secret),
                blueprint_input=blueprint_input,
                source_state=source_state,
                evidence_summary=_summary(relation),
                excerpt=_bounded(excerpt, 240, secret) if excerpt else None,
            )
        )

    def add_dynamic(path: str, text: str):
        safe = _bounded(text, 240, secret)
        dynamic.append(
            DynamicReference(
                evidence_id=evidence_id(source_type, source_id, "dynamic", path),
                source_type=source_type,
                source_id=source_id,
                config_path=path,
                warning="Dynamic template reference could not be resolved statically.",
                excerpt=safe,
                source_entity_id=_bounded(source_entity_id, 128, secret),
                source_name=_bounded(source_name, 160, secret),
                source_state=_bounded(source_state, 32, secret),
            )
        )

    def walk(value: Any, path: str, relation: str, parent_key: str = ""):
        if isinstance(value, dict):
            if source_type == "scene" and path in {"$", "$.entities"}:
                entities = value.get("entities") if path == "$" else value
                if isinstance(entities, dict):
                    for entity in entities:
                        add(str(entity), "scene_entity", f"$.entities.{entity}")
            for key, item in value.items():
                child_path = f"{path}.{key}"
                child_relation = _relation_for(path, key, relation, source_type)
                if key == "entity_id":
                    for entity in _literal_entities(item):
                        add(entity, child_relation, child_path)
                elif source_type == "group" and key == "entities":
                    for entity in _literal_entities(item):
                        add(entity, "group_member", child_path)
                elif path.endswith(".use_blueprint.input"):
                    if not any(term in key.lower() for term in ("secret", "token", "password", "webhook", "api_key", "url")):
                        for entity in _literal_entities_deep(item):
                            add(entity, "blueprint_input", child_path, blueprint_input=key)
                walk(item, child_path, child_relation, key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]", relation, parent_key)
        elif isinstance(value, str):
            if parent_key in FREE_TEXT_KEYS:
                return
            if _is_template(value, parent_key):
                literals = _template_literals(value)
                for entity in literals:
                    add(entity, "template_literal", path, match_type="template_literal", excerpt=value)
                if _has_unresolved_template_reference(value):
                    add_dynamic(path, value)

    walk(config, "$", "other_structured_reference")
    return _deduplicate(findings), _deduplicate_dynamic(dynamic)


def resolve_blueprint_roles(
    findings: list[DependencyFinding],
    blueprint_config: dict[str, Any],
    *,
    source_id: str,
) -> list[DependencyFinding]:
    """Map !input markers to structural blueprint roles without exposing source."""
    roles: dict[str, set[str]] = {}

    def walk(value: Any, path: str, relation: str):
        if isinstance(value, dict):
            if set(value) == {"__blueprint_input__"}:
                roles.setdefault(str(value["__blueprint_input__"]), set()).add(relation)
                return
            for key, item in value.items():
                walk(item, f"{path}.{key}", _relation_for(path, key, relation, "blueprint"))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]", relation)

    walk(blueprint_config, "$", "other_structured_reference")
    resolved = []
    for finding in findings:
        if finding.relation != "blueprint_input" or not finding.blueprint_input:
            continue
        for role in sorted(roles.get(finding.blueprint_input, ())):
            resolved.append(
                replace(
                    finding,
                    evidence_id=evidence_id(finding.evidence_id, "resolved", role),
                    relation="blueprint_resolved_role",
                    confidence="resolved",
                    match_type="blueprint_resolved",
                    config_path=f"use_blueprint.input.{finding.blueprint_input} -> {role}",
                    evidence_summary=f"Blueprint input resolves to {role}.",
                    excerpt=None,
                )
            )
    return resolved


def _relation_for(path: str, key: str, current: str, source_type: str) -> str:
    lowered = key.lower()
    if lowered in {"trigger", "triggers"}:
        return "trigger"
    if lowered == "wait_for_trigger":
        return "wait_for_trigger"
    if lowered in {"condition", "conditions"}:
        if ".choose" in path:
            return "choose_condition"
        if ".if" in path:
            return "if_condition"
        if ".repeat" in path:
            return "repeat_condition"
        return "condition"
    if lowered in {"if"}:
        return "if_condition"
    if lowered in {"while", "until"} and ".repeat" in path:
        return "repeat_condition"
    if lowered == "target":
        return "service_target" if ".action" in path or ".sequence" in path else "action_target"
    if lowered == "data":
        return "action_data"
    if source_type == "script" and lowered in {"sequence", "action", "actions"}:
        return "script_reference"
    if lowered in {"action", "actions", "sequence", "parallel", "then", "else", "choose", "repeat"}:
        return "action_target"
    return current


def _literal_entities(value: Any) -> Iterable[str]:
    values = [value] if isinstance(value, str) else value
    if isinstance(values, list):
        for item in values:
            if isinstance(item, str) and valid_entity_id(item.strip().lower()):
                yield item


def _literal_entities_deep(value: Any) -> Iterable[str]:
    if isinstance(value, str) and valid_entity_id(value.strip().lower()):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _literal_entities_deep(item)
    elif isinstance(value, list):
        for item in value:
            yield from _literal_entities_deep(item)


def _is_template(value: str, key: str) -> bool:
    return "{{" in value or "{%" in value or key in TEMPLATE_KEYS


def _template_literals(value: str) -> list[str]:
    found = {match.group(2).lower() for match in HELPER_LITERAL.finditer(value)}
    found.update(f"{match.group(1)}.{match.group(2)}".lower() for match in STATES_DOT.finditer(value))
    if "[" in value and "]" in value:
        found.update(token.lower() for token in ENTITY_TOKEN.findall(value) if valid_entity_id(token.lower()))
    return sorted(found)


def _has_unresolved_template_reference(value: str) -> bool:
    helper_calls = re.findall(r"(?:states|is_state|state_attr|expand)\((.*?)\)", value, re.I | re.S)
    if not helper_calls:
        return False
    literal_count = len(HELPER_LITERAL.findall(value))
    return len(helper_calls) > literal_count


def _summary(relation: str) -> str:
    return {
        "trigger": "Entity is used by a trigger.",
        "condition": "Entity is used by a condition.",
        "action_target": "Entity is used by an action.",
        "service_target": "Entity is targeted by a service action.",
        "action_data": "Entity is supplied in action data.",
        "template_literal": "Entity is referenced literally by a behavioral template.",
        "blueprint_input": "Entity is supplied to a blueprint input.",
        "group_member": "Entity is an explicit group member.",
    }.get(relation, f"Entity has a {relation} reference.")


def _bounded(value: Any, limit: int = 160, secret: str = "") -> str | None:
    if value is None:
        return None
    safe = redact_data(str(value), secret=secret, max_string=limit)
    text = str(safe)
    for marker in ("authorization:", "bearer ", "/mcp"):
        if marker in text.lower():
            return "<redacted>"
    return text


def _deduplicate(findings: list[DependencyFinding]) -> list[DependencyFinding]:
    return sorted({item.evidence_id: item for item in findings}.values(), key=lambda item: item.evidence_id)


def _deduplicate_dynamic(items: list[DynamicReference]) -> list[DynamicReference]:
    return sorted({item.evidence_id: item for item in items}.values(), key=lambda item: item.evidence_id)
