"""Static exact-reference extraction for Home Assistant configuration objects."""

from __future__ import annotations

import ast
from dataclasses import replace
import re
from typing import Any, Iterable

from ..logging_config import redact_data
from .models import DependencyFinding, DynamicReference, evidence_id


ENTITY_ID_COMPONENT = re.compile(r"^[a-z0-9_]+$")
ENTITY_BEARING_KEYS = frozenset({"entity_id"})
ENTITY_TEMPLATE_HELPERS = frozenset(
    {"states", "is_state", "is_state_attr", "state_attr", "expand"}
)
MAX_TEMPLATE_SEGMENT_CHARS = 65_536
MAX_TEMPLATE_ARGUMENT_CHARS = 4_096
MAX_LITERAL_ARGUMENTS = 100
MAX_TEMPLATE_NESTING = 8
FREE_TEXT_KEYS = {"alias", "description", "message", "title", "name", "friendly_name", "event_type"}
TEMPLATE_KEYS = {"value_template", "template", "availability", "state", "condition", "until", "while"}


def valid_entity_id(value: str) -> bool:
    """Return whether *value* is an exact canonical Home Assistant entity ID.

    Syntax alone never establishes that a string is an entity reference; callers
    must also establish an entity-bearing configuration or template context.
    """

    if (
        not isinstance(value, str)
        or len(value) > 255
        or value != value.strip()
        or value != value.lower()
    ):
        return False
    if value.count(".") != 1 or any(marker in value for marker in ("{{", "}}", "{%", "%}")):
        return False
    domain, object_id = value.split(".", 1)
    if not domain or not object_id:
        return False
    if not ENTITY_ID_COMPONENT.fullmatch(domain) or not ENTITY_ID_COMPONENT.fullmatch(object_id):
        return False
    # Custom integrations may introduce domains unknown to this server. Requiring
    # a letter in each component rejects decimals and version fragments without a
    # brittle allow-list of Home Assistant domains.
    return any(char.isalpha() for char in domain) and any(
        char.isalpha() for char in object_id
    )


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
        if not valid_entity_id(entity_id):
            return
        normalized = entity_id
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
                if key in ENTITY_BEARING_KEYS:
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
            if parent_key in FREE_TEXT_KEYS and not (
                "{{" in value or "{%" in value or "{#" in value
            ):
                return
            if _is_template(value, parent_key):
                literals, unresolved = _template_references(value)
                for entity in literals:
                    add(entity, "template_literal", path, match_type="template_literal", excerpt=value)
                if unresolved:
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
            if isinstance(item, str) and valid_entity_id(item):
                yield item


def _literal_entities_deep(value: Any) -> Iterable[str]:
    if isinstance(value, str) and valid_entity_id(value):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _literal_entities_deep(item)
    elif isinstance(value, list):
        for item in value:
            yield from _literal_entities_deep(item)


def _is_template(value: str, key: str) -> bool:
    return "{{" in value or "{%" in value or key in TEMPLATE_KEYS


def _template_references(value: str) -> tuple[list[str], bool]:
    """Extract references only from recognized Home Assistant template syntax.

    The scanner never executes Jinja and never promotes arbitrary dotted tokens.
    It examines helper calls and ``states`` lookup syntax outside quoted prose and
    template comments. Dynamic arguments are reported without inventing targets.
    """

    exact: set[str] = set()
    unresolved = False
    for segment in _template_code_segments(value):
        bounded = segment[:MAX_TEMPLATE_SEGMENT_CHARS]
        found, dynamic = _scan_template_segment(bounded)
        exact.update(found)
        unresolved = unresolved or dynamic or len(segment) > len(bounded)
    return sorted(exact), unresolved


def _template_code_segments(value: str) -> list[str]:
    segments: list[str] = []
    saw_tag = False
    cursor = 0
    while cursor < len(value):
        positions = [
            (position, opener, closer)
            for opener, closer in (("{{", "}}"), ("{%", "%}"), ("{#", "#}"))
            if (position := value.find(opener, cursor)) >= 0
        ]
        if not positions:
            break
        start, opener, closer = min(positions, key=lambda item: item[0])
        saw_tag = True
        end = value.find(closer, start + 2)
        if end < 0:
            end = len(value)
        if opener != "{#":
            segments.append(value[start + 2 : end])
        cursor = min(len(value), end + 2)
    if not saw_tag:
        return [value]
    return segments


def _scan_template_segment(value: str, *, depth: int = 0) -> tuple[set[str], bool]:
    exact: set[str] = set()
    unresolved = False
    cursor = 0
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if not (char.isalpha() or char == "_"):
            cursor += 1
            continue
        start = cursor
        cursor += 1
        while cursor < len(value) and (value[cursor].isalnum() or value[cursor] == "_"):
            cursor += 1
        name = value[start:cursor]
        if name not in ENTITY_TEMPLATE_HELPERS:
            continue
        if start > 0 and (value[start - 1].isalnum() or value[start - 1] in {"_", "."}):
            continue
        lookahead = cursor
        while lookahead < len(value) and value[lookahead].isspace():
            lookahead += 1

        if lookahead < len(value) and value[lookahead] == "(":
            inner, end = _extract_balanced(value, lookahead, "(", ")")
            if inner is None:
                unresolved = True
                cursor = lookahead + 1
                continue
            arguments = _split_top_level_args(inner)
            target_arguments = arguments if name == "expand" else arguments[:1]
            for argument in target_arguments:
                literals = _literal_string_arguments(argument)
                if literals is None:
                    unresolved = True
                    continue
                exact.update(item for item in literals if valid_entity_id(item))
            if depth < MAX_TEMPLATE_NESTING:
                nested, nested_dynamic = _scan_template_segment(
                    inner, depth=depth + 1
                )
                exact.update(nested)
                unresolved = unresolved or nested_dynamic
            elif any(helper in inner for helper in ENTITY_TEMPLATE_HELPERS):
                unresolved = True
            cursor = end
            continue

        if name == "states" and lookahead < len(value) and value[lookahead] == "[":
            inner, end = _extract_balanced(value, lookahead, "[", "]")
            if inner is None:
                unresolved = True
                cursor = lookahead + 1
                continue
            literals = _literal_string_arguments(inner)
            if literals is None:
                unresolved = True
            else:
                exact.update(item for item in literals if valid_entity_id(item))
            cursor = end
            continue

        if name == "states" and value.startswith(".", lookahead):
            match = re.match(r"\.([a-z0-9_]+)\.([a-z0-9_]+)", value[lookahead:])
            if match:
                entity_id = f"{match.group(1)}.{match.group(2)}"
                if valid_entity_id(entity_id):
                    exact.add(entity_id)
                cursor = lookahead + match.end()
    return exact, unresolved


def _extract_balanced(
    value: str, start: int, opener: str, closer: str
) -> tuple[str | None, int]:
    depth = 0
    cursor = start
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return value[start + 1 : cursor], cursor + 1
        cursor += 1
    return None, start + 1


def _skip_quoted(value: str, start: int) -> int:
    quote = value[start]
    cursor = start + 1
    while cursor < len(value):
        if value[cursor] == "\\":
            cursor += 2
            continue
        if value[cursor] == quote:
            return cursor + 1
        cursor += 1
    return len(value)


def _split_top_level_args(value: str) -> list[str]:
    arguments: list[str] = []
    start = 0
    depth = 0
    cursor = 0
    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = set(pairs.values())
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if char in pairs:
            depth += 1
        elif char in closers:
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            arguments.append(value[start:cursor].strip())
            start = cursor + 1
        cursor += 1
    final = value[start:].strip()
    if final:
        arguments.append(final)
    return arguments


def _literal_string_arguments(value: str) -> tuple[str, ...] | None:
    if len(value) > MAX_TEMPLATE_ARGUMENT_CHARS:
        return None
    try:
        parsed = ast.literal_eval(value.strip())
    except (RecursionError, SyntaxError, ValueError):
        return None
    if isinstance(parsed, str):
        return (parsed,)
    if (
        isinstance(parsed, (list, tuple))
        and len(parsed) <= MAX_LITERAL_ARGUMENTS
        and all(isinstance(item, str) for item in parsed)
    ):
        return tuple(parsed)
    return None


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
