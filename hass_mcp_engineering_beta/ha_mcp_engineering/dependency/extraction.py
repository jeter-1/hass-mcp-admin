"""Static exact-reference extraction for Home Assistant configuration objects."""

from __future__ import annotations

import ast
from dataclasses import replace
import re
from typing import Any, Iterable

from ..logging_config import redact_data
from .dynamic_resolution import (
    BoundedTemplateContext,
    CandidateResolution,
    MAX_DYNAMIC_LABEL_SELECTORS,
)
from .models import DependencyFinding, DynamicReference, evidence_id


ENTITY_ID_COMPONENT = re.compile(r"^[a-z0-9_]+$")
ENTITY_BEARING_KEYS = frozenset({"entity_id"})
ENTITY_TEMPLATE_HELPERS = frozenset(
    {
        "states",
        "is_state",
        "is_state_attr",
        "state_attr",
        "has_value",
        "expand",
    }
)
ENTITY_TEMPLATE_FILTERS = frozenset(
    {"states", "state_attr", "has_value"}
)
ENTITY_TEMPLATE_TESTS = frozenset(
    {"is_state", "is_state_attr", "has_value"}
)
ENTITY_COLLECTION_TEST_FILTERS = frozenset({"select", "reject"})
ENTITY_COLLECTION_ATTRIBUTE_TEST_FILTERS = frozenset(
    {"selectattr", "rejectattr"}
)
ENTITY_COLLECTION_MAP_FILTER = "map"
COLLECTION_CANDIDATE_PRESERVING_FILTERS = frozenset(
    {
        "select",
        "reject",
        "selectattr",
        "rejectattr",
        "list",
        "unique",
        "sort",
        "reverse",
    }
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
    resolved_selector_count = 0

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

    def add_dynamic(
        path: str,
        text: str,
        resolution: CandidateResolution,
    ):
        nonlocal resolved_selector_count
        safe_selectors = tuple(
            value
            for selector in resolution.literal_label_selectors
            if isinstance(
                (value := _bounded(selector, 128, secret)), str
            )
        )
        selector_sanitization_incomplete = bool(
            safe_selectors != resolution.literal_label_selectors
        )
        resolved_selector_count += len(
            safe_selectors
        )
        selector_limit_exceeded = (
            resolved_selector_count > MAX_DYNAMIC_LABEL_SELECTORS
        )
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
                possible_entity_domains=(
                    resolution.possible_entity_domains
                ),
                possible_entity_ids=resolution.entity_ids,
                literal_label_selectors=(
                    safe_selectors
                ),
                candidate_resolution_kind=(
                    "resolution_limit"
                    if selector_limit_exceeded
                    else "unresolved"
                    if selector_sanitization_incomplete
                    else resolution.kind
                ),
                candidate_resolution_complete=bool(
                    resolution.complete
                    and not selector_limit_exceeded
                    and not selector_sanitization_incomplete
                ),
                candidate_resolution_limit_exceeded=bool(
                    resolution.limit_exceeded
                    or selector_limit_exceeded
                ),
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
                literals, unresolved, resolution = (
                    _template_references(value)
                )
                for entity in literals:
                    add(entity, "template_literal", path, match_type="template_literal", excerpt=value)
                if unresolved:
                    add_dynamic(path, value, resolution)

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


def _template_references(
    value: str,
) -> tuple[list[str], bool, CandidateResolution]:
    """Extract references only from recognized Home Assistant template syntax.

    The scanner never executes Jinja and never promotes arbitrary dotted tokens.
    It examines helper calls and ``states`` lookup syntax outside quoted prose and
    template comments. Dynamic arguments are reported without inventing targets.
    """

    exact: set[str] = set()
    dynamic_resolutions: list[CandidateResolution] = []
    context = BoundedTemplateContext(valid_entity_id)
    for segment_type, segment in _template_segments(value):
        bounded = segment[:MAX_TEMPLATE_SEGMENT_CHARS]
        if segment_type == "statement":
            context.apply_statement(bounded)
        found, resolutions = _scan_template_segment(
            bounded, candidate_context=context
        )
        exact.update(found)
        dynamic_resolutions.extend(resolutions)
        if len(segment) > len(bounded):
            dynamic_resolutions.append(
                CandidateResolution(
                    complete=False,
                    limit_exceeded=True,
                    kind="resolution_limit",
                )
            )
    if not dynamic_resolutions:
        return sorted(exact), False, CandidateResolution()
    complete = all(item.complete for item in dynamic_resolutions)
    limit_exceeded = any(
        item.limit_exceeded for item in dynamic_resolutions
    )
    entity_ids = sorted(
        {
            entity_id
            for item in dynamic_resolutions
            for entity_id in item.entity_ids
        }
    )
    labels = sorted(
        {
            label
            for item in dynamic_resolutions
            for label in item.literal_label_selectors
        }
    )
    if len(entity_ids) > MAX_LITERAL_ARGUMENTS:
        entity_ids = entity_ids[:MAX_LITERAL_ARGUMENTS]
        complete = False
        limit_exceeded = True
    if len(labels) > MAX_DYNAMIC_LABEL_SELECTORS:
        labels = labels[:MAX_DYNAMIC_LABEL_SELECTORS]
        complete = False
        limit_exceeded = True
    domains: tuple[str, ...] | None
    if complete and not labels:
        domain_values: set[str] = set()
        for item in dynamic_resolutions:
            if item.possible_entity_domains is None:
                complete = False
                break
            domain_values.update(item.possible_entity_domains)
        domains = tuple(sorted(domain_values)) if complete else None
    else:
        domains = None
    kinds = {item.kind for item in dynamic_resolutions}
    if limit_exceeded:
        kind = "resolution_limit"
    elif len(kinds) == 1:
        kind = next(iter(kinds))
    elif complete:
        kind = "finite_union"
    else:
        kind = "unresolved"
    return sorted(exact), True, CandidateResolution(
        entity_ids=tuple(entity_ids),
        literal_label_selectors=tuple(labels),
        possible_entity_domains=domains,
        complete=complete,
        limit_exceeded=limit_exceeded,
        kind=kind,
    )


def _template_segments(value: str) -> list[tuple[str, str]]:
    segments: list[tuple[str, str]] = []
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
            segments.append(
                (
                    "statement" if opener == "{%" else "expression",
                    value[start + 2 : end],
                )
            )
        cursor = min(len(value), end + 2)
    if not saw_tag:
        return [("expression", value)]
    return segments


def _scan_template_segment(
    value: str,
    *,
    depth: int = 0,
    candidate_context: BoundedTemplateContext | None = None,
) -> tuple[set[str], list[CandidateResolution]]:
    exact, unresolved = _scan_template_entity_operators(
        value, candidate_context=candidate_context
    )
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
        if _is_filter_or_test_identifier(value, start):
            # Filter and test arguments are not entity operands.  Their entity
            # operand is projected by _scan_template_entity_operators().
            continue
        lookahead = cursor
        while lookahead < len(value) and value[lookahead].isspace():
            lookahead += 1

        if lookahead < len(value) and value[lookahead] == "(":
            inner, end = _extract_balanced(value, lookahead, "(", ")")
            if inner is None:
                unresolved.append(CandidateResolution())
                cursor = lookahead + 1
                continue
            arguments = _split_top_level_args(inner)
            target_arguments = arguments if name == "expand" else arguments[:1]
            if not target_arguments:
                unresolved.append(CandidateResolution())
            for argument in target_arguments:
                literals = _literal_string_arguments(argument)
                if literals is None:
                    unresolved.append(
                        _resolve_dynamic_argument(
                            argument, candidate_context
                        )
                    )
                    continue
                entities = tuple(
                    item for item in literals if valid_entity_id(item)
                )
                exact.update(entities)
                if not literals or len(entities) != len(literals):
                    unresolved.append(CandidateResolution())
            if depth < MAX_TEMPLATE_NESTING:
                nested, nested_dynamic = _scan_template_segment(
                    inner,
                    depth=depth + 1,
                    candidate_context=candidate_context,
                )
                exact.update(nested)
                unresolved.extend(nested_dynamic)
            elif any(helper in inner for helper in ENTITY_TEMPLATE_HELPERS):
                unresolved.append(
                    CandidateResolution(
                        complete=False,
                        limit_exceeded=True,
                        kind="resolution_limit",
                    )
                )
            cursor = end
            continue

        if name == "states" and lookahead < len(value) and value[lookahead] == "[":
            inner, end = _extract_balanced(value, lookahead, "[", "]")
            if inner is None:
                unresolved.append(CandidateResolution())
                cursor = lookahead + 1
                continue
            literals = _literal_string_arguments(inner)
            if literals is None:
                unresolved.append(
                    _resolve_dynamic_argument(inner, candidate_context)
                )
            else:
                entities = tuple(
                    item for item in literals if valid_entity_id(item)
                )
                exact.update(entities)
                if not literals or len(entities) != len(literals):
                    unresolved.append(CandidateResolution())
            cursor = end
            continue

        if name == "states" and value.startswith(".", lookahead):
            match = re.match(r"\.([a-z0-9_]+)\.([a-z0-9_]+)", value[lookahead:])
            if match:
                entity_id = f"{match.group(1)}.{match.group(2)}"
                if valid_entity_id(entity_id):
                    exact.add(entity_id)
                cursor = lookahead + match.end()
                continue
            domain_match = re.match(
                r"\.([a-z0-9_]+)(?![a-z0-9_.])",
                value[lookahead:],
            )
            if domain_match:
                domain = domain_match.group(1)
                if (
                    ENTITY_ID_COMPONENT.fullmatch(domain)
                    and any(character.isalpha() for character in domain)
                ):
                    unresolved.append(
                        CandidateResolution(
                            possible_entity_domains=(domain,),
                            complete=True,
                            kind="proven_domain_collection",
                        )
                    )
                else:
                    unresolved.append(CandidateResolution())
                cursor = lookahead + domain_match.end()
                continue
        if name == "states":
            # Bare ``states`` is the official all-state collection.  It can
            # select the target helper and therefore must remain explicitly
            # unresolved rather than disappearing as zero evidence.
            unresolved.append(
                CandidateResolution(kind="unrestricted_state_collection")
            )
    return exact, unresolved


def _scan_template_entity_operators(
    value: str,
    *,
    candidate_context: BoundedTemplateContext | None,
) -> tuple[set[str], list[CandidateResolution]]:
    """Project reviewed Home Assistant entity filters and tests.

    Jinja filters/tests place the entity operand before the operator, unlike
    the equivalent function forms.  This scanner recognizes only those
    reviewed operators and a bounded trailing operand atom.  Anything that
    uses one of the operators but cannot be proven exact or finite is emitted
    as unresolved evidence instead of disappearing from the index.
    """

    exact: set[str] = set()
    unresolved = _scan_template_collection_entity_operators(
        value, candidate_context=candidate_context
    )
    cursor = 0
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if char == "|":
            name_start = cursor + 1
            while name_start < len(value) and value[name_start].isspace():
                name_start += 1
            name_end = name_start
            while name_end < len(value) and (
                value[name_end].isalnum() or value[name_end] == "_"
            ):
                name_end += 1
            if value[name_start:name_end] in ENTITY_TEMPLATE_FILTERS:
                _project_template_operator_operand(
                    value[:cursor],
                    exact=exact,
                    unresolved=unresolved,
                    candidate_context=candidate_context,
                )
            cursor = max(cursor + 1, name_end)
            continue
        if char.isalpha() or char == "_":
            start = cursor
            cursor += 1
            while cursor < len(value) and (
                value[cursor].isalnum() or value[cursor] == "_"
            ):
                cursor += 1
            if value[start:cursor] != "is":
                continue
            name_start = cursor
            while name_start < len(value) and value[name_start].isspace():
                name_start += 1
            name_end = name_start
            while name_end < len(value) and (
                value[name_end].isalnum() or value[name_end] == "_"
            ):
                name_end += 1
            if value[name_start:name_end] == "not":
                name_start = name_end
                while (
                    name_start < len(value)
                    and value[name_start].isspace()
                ):
                    name_start += 1
                name_end = name_start
                while name_end < len(value) and (
                    value[name_end].isalnum()
                    or value[name_end] == "_"
                ):
                    name_end += 1
            if value[name_start:name_end] in ENTITY_TEMPLATE_TESTS:
                _project_template_operator_operand(
                    value[:start],
                    exact=exact,
                    unresolved=unresolved,
                    candidate_context=candidate_context,
                )
            cursor = max(cursor, name_end)
            continue
        cursor += 1
    return exact, unresolved


def _scan_template_collection_entity_operators(
    value: str,
    *,
    candidate_context: BoundedTemplateContext | None,
) -> list[CandidateResolution]:
    """Project bounded candidates used by reviewed collection operators.

    Home Assistant exposes state-aware filters and tests through Jinja's
    ``map``/``select``/``reject`` collection operators.  These operators receive
    their entity candidates from the collection to the left of the pipeline,
    not from the quoted filter/test name.  Only a finite literal/context value
    or an exact ``states.<domain>`` collection is conclusive.  Any ambiguous
    collection or operator remains explicit incomplete evidence.
    """

    parts = _split_top_level_pipeline(value)
    if len(parts) < 2:
        return []
    base_expression = parts[0]
    prior_stages: list[str] = []
    for stage in parts[1:]:
        parsed = _parse_pipeline_stage(stage)
        if parsed is None:
            prior_stages.append(stage)
            continue
        filter_name, arguments = parsed
        operator_name: str | None = None
        reviewed = False
        operator_unresolved = False
        if filter_name in ENTITY_COLLECTION_TEST_FILTERS:
            if arguments is None:
                operator_unresolved = True
            elif arguments:
                operator_name = _literal_operator_name(arguments[0])
                reviewed = operator_name in ENTITY_TEMPLATE_TESTS
                operator_unresolved = operator_name is None
        elif filter_name in ENTITY_COLLECTION_ATTRIBUTE_TEST_FILTERS:
            if arguments is None:
                operator_unresolved = True
            elif len(arguments) >= 2:
                attribute_name = _literal_operator_name(arguments[0])
                operator_name = _literal_operator_name(arguments[1])
                reviewed = bool(
                    attribute_name == "entity_id"
                    and operator_name in ENTITY_TEMPLATE_TESTS
                )
                operator_unresolved = bool(
                    operator_name is None
                    or (
                        operator_name in ENTITY_TEMPLATE_TESTS
                        and attribute_name != "entity_id"
                    )
                )
        elif filter_name == ENTITY_COLLECTION_MAP_FILTER:
            if arguments is None:
                operator_unresolved = True
            elif arguments:
                operator_name = _literal_operator_name(arguments[0])
                reviewed = operator_name in ENTITY_TEMPLATE_FILTERS
                operator_unresolved = bool(
                    operator_name is None
                    and not re.match(
                        r"\s*attribute\s*=", arguments[0]
                    )
                )

        if reviewed or operator_unresolved:
            if not _pipeline_preserves_collection_candidates(prior_stages):
                resolution = CandidateResolution()
            elif operator_unresolved:
                resolution = CandidateResolution()
            else:
                resolution = _resolve_collection_candidate_expression(
                    base_expression, candidate_context
                )
            kind_operator = operator_name or "unresolved_operator"
            return [
                replace(
                    resolution,
                    kind=(
                        f"collection_{filter_name}_{kind_operator}_"
                        f"{resolution.kind}"
                    ),
                )
            ]
        prior_stages.append(stage)
    return []


def _split_top_level_pipeline(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    stack: list[str] = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    cursor = 0
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if char in pairs:
            stack.append(pairs[char])
        elif stack and char == stack[-1]:
            stack.pop()
        elif char == "|" and not stack:
            parts.append(value[start:cursor].strip())
            start = cursor + 1
        cursor += 1
    parts.append(value[start:].strip())
    return parts


def _parse_pipeline_stage(
    stage: str,
) -> tuple[str, list[str] | None] | None:
    match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", stage)
    if match is None:
        return None
    name = match.group(1)
    cursor = match.end()
    while cursor < len(stage) and stage[cursor].isspace():
        cursor += 1
    if cursor == len(stage):
        return name, []
    if stage[cursor] != "(":
        return name, None
    inner, end = _extract_balanced(stage, cursor, "(", ")")
    if inner is None or stage[end:].strip():
        return name, None
    return name, _split_top_level_args(inner)


def _literal_operator_name(argument: str) -> str | None:
    if len(argument) > MAX_TEMPLATE_ARGUMENT_CHARS:
        return None
    try:
        parsed = ast.literal_eval(argument.strip())
    except (RecursionError, SyntaxError, ValueError):
        return None
    if not isinstance(parsed, str):
        return None
    return parsed


def _pipeline_preserves_collection_candidates(
    stages: list[str],
) -> bool:
    for stage in stages:
        parsed = _parse_pipeline_stage(stage)
        if parsed is None:
            return False
        name, arguments = parsed
        if name == ENTITY_COLLECTION_MAP_FILTER:
            if _map_stage_preserves_entity_candidates(arguments):
                continue
            return False
        if name not in COLLECTION_CANDIDATE_PRESERVING_FILTERS:
            return False
        if arguments is None:
            return False
    return True


def _map_stage_preserves_entity_candidates(
    arguments: list[str] | None,
) -> bool:
    if arguments is None or len(arguments) != 1:
        return False
    return bool(
        re.fullmatch(
            r"\s*attribute\s*=\s*(['\"])entity_id\1\s*",
            arguments[0],
        )
    )


def _resolve_collection_candidate_expression(
    expression: str,
    candidate_context: BoundedTemplateContext | None,
) -> CandidateResolution:
    bounded = expression.strip()
    if not bounded:
        return CandidateResolution()
    if len(bounded) > MAX_TEMPLATE_ARGUMENT_CHARS:
        return CandidateResolution(
            complete=False,
            limit_exceeded=True,
            kind="resolution_limit",
        )
    literals = _literal_string_arguments(bounded)
    if literals is not None:
        entity_ids = tuple(
            sorted({item for item in literals if valid_entity_id(item)})
        )
        complete = bool(entity_ids and len(entity_ids) == len(literals))
        return CandidateResolution(
            entity_ids=entity_ids,
            possible_entity_domains=(
                tuple(
                    sorted(
                        {item.split(".", 1)[0] for item in entity_ids}
                    )
                )
                if complete
                else None
            ),
            complete=complete,
            kind=(
                "finite_collection_candidates"
                if complete
                else "unresolved"
            ),
        )
    domain_match = re.fullmatch(r"states\.([a-z0-9_]+)", bounded)
    if domain_match is not None:
        domain = domain_match.group(1)
        if ENTITY_ID_COMPONENT.fullmatch(domain) and any(
            character.isalpha() for character in domain
        ):
            return CandidateResolution(
                possible_entity_domains=(domain,),
                complete=True,
                kind="proven_domain_collection",
            )
        return CandidateResolution()
    return _resolve_dynamic_argument(bounded, candidate_context)


def _project_template_operator_operand(
    prefix: str,
    *,
    exact: set[str],
    unresolved: list[CandidateResolution],
    candidate_context: BoundedTemplateContext | None,
) -> None:
    operand = _trailing_template_operand(prefix)
    if operand is None:
        unresolved.append(CandidateResolution())
        return
    literals = _literal_string_arguments(operand)
    if literals is not None:
        entities = tuple(
            item for item in literals if valid_entity_id(item)
        )
        if entities and len(entities) == len(literals):
            exact.update(entities)
            return
        unresolved.append(CandidateResolution())
        return
    unresolved.append(
        _resolve_dynamic_argument(operand, candidate_context)
    )


def _trailing_template_operand(value: str) -> str | None:
    """Return one bounded trailing quoted literal or variable expression."""

    bounded = value[-MAX_TEMPLATE_ARGUMENT_CHARS:].rstrip()
    if not bounded:
        return None
    if bounded[-1] in {"'", '"'}:
        quote = bounded[-1]
        cursor = len(bounded) - 2
        while cursor >= 0:
            if bounded[cursor] == quote:
                escapes = 0
                previous = cursor - 1
                while previous >= 0 and bounded[previous] == "\\":
                    escapes += 1
                    previous -= 1
                if escapes % 2 == 0:
                    return bounded[cursor:]
            cursor -= 1
        return None
    match = re.search(
        r"(?P<operand>[A-Za-z_][A-Za-z0-9_]*"
        r"(?:\.[A-Za-z_][A-Za-z0-9_]*|\[[^\[\]]+\])*)\s*$",
        bounded,
    )
    if match is None:
        return None
    return match.group("operand")


def _is_filter_or_test_identifier(value: str, start: int) -> bool:
    prefix = value[:start].rstrip()
    if prefix.endswith("|"):
        return True
    return bool(re.search(r"\bis(?:\s+not)?\s*$", prefix))


def _resolve_dynamic_argument(
    argument: str,
    candidate_context: BoundedTemplateContext | None,
) -> CandidateResolution:
    if candidate_context is not None:
        resolved = candidate_context.resolve(argument)
        if resolved.complete or resolved.limit_exceeded:
            return resolved
    domains = _constrained_dynamic_entity_domains(argument)
    if domains is not None:
        return CandidateResolution(
            possible_entity_domains=tuple(sorted(domains)),
            complete=True,
            kind="proven_domain",
        )
    return CandidateResolution()


def _constrained_dynamic_entity_domains(
    argument: str,
) -> frozenset[str] | None:
    """Recognize one complete fixed-domain plus simple-name expression."""

    match = re.fullmatch(
        r"\s*(?P<quote>['\"])(?P<domain>[a-z0-9_]+)\."
        r"(?P=quote)\s*~\s*"
        r"(?P<suffix>[A-Za-z_][A-Za-z0-9_]*)\s*",
        argument,
    )
    if match is None:
        return None
    domain = match.group("domain")
    if not ENTITY_ID_COMPONENT.fullmatch(domain) or not any(
        character.isalpha() for character in domain
    ):
        return None
    return frozenset({domain})


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
