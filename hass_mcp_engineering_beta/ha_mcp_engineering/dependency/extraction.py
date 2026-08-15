"""Static exact-reference extraction for Home Assistant configuration objects."""

from __future__ import annotations

import ast
from dataclasses import replace
import re
from typing import Any, Iterable

from ..logging_config import redact_data
from .dynamic_resolution import (
    BoundedTemplateContext,
    CallableBindingResolution,
    CandidateResolution,
    MAPPING_READ_METHODS,
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
PIPELINE_PROVENANCE_FILTERS = frozenset(
    {
        *COLLECTION_CANDIDATE_PRESERVING_FILTERS,
        "batch",
        "default",
        "dictsort",
        "first",
        "groupby",
        "items",
        "last",
        "map",
        "random",
        "slice",
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
                warning=(
                    "Dynamic template content contains no entity selector."
                    if not resolution.entity_selector_present
                    else "Dynamic template reference could not be resolved statically."
                ),
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
                reference_kind=(
                    "dynamic_entity_selector"
                    if resolution.entity_selector_present
                    else "ordinary_dynamic_template"
                ),
                entity_selector_present=(
                    resolution.entity_selector_present
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
    context = BoundedTemplateContext(
        valid_entity_id,
        entity_helper_names=ENTITY_TEMPLATE_HELPERS,
    )
    for segment_type, segment in _template_segments(value):
        bounded = segment[:MAX_TEMPLATE_SEGMENT_CHARS]
        scan_value = bounded
        binding_expression: str | None = None
        if segment_type == "statement":
            binding_expression = context.binding_expression(bounded)
            if binding_expression is not None:
                scan_value = binding_expression
        callable_alias_transport = bool(
            segment_type == "statement"
            and context.is_callable_alias_transport_statement(bounded)
        )
        found, resolutions = _scan_template_segment(
            scan_value,
            candidate_context=context,
            binding_value=bool(
                binding_expression is not None
                and (
                    context.is_assignment_statement(bounded)
                    or callable_alias_transport
                )
            ),
            collection_use=bool(
                binding_expression is not None
                and context.is_iteration_statement(bounded)
                and not callable_alias_transport
            ),
        )
        exact.update(found)
        dynamic_resolutions.extend(resolutions)
        if segment_type == "statement":
            # Jinja evaluates a set/for expression before introducing its new
            # local binding.  Scan the right-hand side against the prior
            # context, then make the binding available to later segments.
            context.apply_statement(bounded)
        if len(segment) > len(bounded):
            dynamic_resolutions.append(
                CandidateResolution(
                    complete=False,
                    limit_exceeded=True,
                    kind="resolution_limit",
                )
            )
    if not context.control_flow_complete:
        dynamic_resolutions.append(
            CandidateResolution(
                complete=False,
                limit_exceeded=True,
                kind="resolution_limit",
            )
        )
    if not dynamic_resolutions:
        return sorted(exact), False, CandidateResolution()
    selector_resolutions = [
        item
        for item in dynamic_resolutions
        if item.entity_selector_present
    ]
    if not selector_resolutions:
        return sorted(exact), True, CandidateResolution(
            possible_entity_domains=(),
            complete=True,
            kind="ordinary_dynamic_template",
            entity_selector_present=False,
        )
    complete = all(item.complete for item in selector_resolutions)
    limit_exceeded = any(
        item.limit_exceeded for item in selector_resolutions
    )
    entity_ids = sorted(
        {
            entity_id
            for item in selector_resolutions
            for entity_id in item.entity_ids
        }
    )
    labels = sorted(
        {
            label
            for item in selector_resolutions
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
        for item in selector_resolutions:
            if item.possible_entity_domains is None:
                complete = False
                break
            domain_values.update(item.possible_entity_domains)
        domains = tuple(sorted(domain_values)) if complete else None
    else:
        domains = None
    kinds = {item.kind for item in selector_resolutions}
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
        entity_selector_present=True,
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
    binding_value: bool = False,
    collection_use: bool = False,
) -> tuple[set[str], list[CandidateResolution]]:
    exact, unresolved = _scan_template_entity_operators(
        value, candidate_context=candidate_context
    )
    unresolved.extend(
        _scan_unsupported_attr_filters(
            value,
            candidate_context=candidate_context,
        )
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
        callable_binding = (
            candidate_context.callable_binding(name)
            if candidate_context is not None
            else None
        )
        if name not in ENTITY_TEMPLATE_HELPERS and not (
            callable_binding is not None
            and callable_binding.locally_bound
        ):
            continue
        if start > 0 and (value[start - 1].isalnum() or value[start - 1] in {"_", "."}):
            continue
        if _is_filter_or_test_identifier(value, start):
            # Filter and test arguments are not entity operands.  Their entity
            # operand is projected by _scan_template_entity_operators().
            continue
        member_access = False
        member_arguments: tuple[str, ...] = ()
        if (
            candidate_context is not None
            and callable_binding is not None
            and callable_binding.locally_bound
            and callable_binding.has_bounded_members
        ):
            (
                callable_binding,
                cursor,
                member_access,
                member_arguments,
            ) = _direct_member_binding(
                value,
                start=start,
                root_end=cursor,
                candidate_context=candidate_context,
            )
        returned_method_steps = 0
        while True:
            lookahead = cursor
            while (
                lookahead < len(value) and value[lookahead].isspace()
            ):
                lookahead += 1
            if not (
                candidate_context is not None
                and callable_binding is not None
                and callable_binding.locally_bound
                and callable_binding.mapping_method is not None
                and lookahead < len(value)
                and value[lookahead] == "("
            ):
                break
            if returned_method_steps >= MAX_TEMPLATE_NESTING:
                callable_binding = CallableBindingResolution(
                    locally_bound=True,
                    limit_exceeded=True,
                )
                break
            inner, end = _extract_balanced(value, lookahead, "(", ")")
            if inner is None:
                callable_binding = CallableBindingResolution(
                    locally_bound=True
                )
                cursor = min(len(value), lookahead + 1)
                member_access = True
                break
            else:
                member_arguments = (*member_arguments, inner)
                cursor = end
                member_access = True
                returned_method_steps += 1
                callable_binding = candidate_context.member_binding(
                    value[start:cursor]
                )
                if callable_binding.has_bounded_members:
                    (
                        callable_binding,
                        cursor,
                        _nested_access,
                        nested_arguments,
                    ) = _direct_member_binding(
                        value,
                        start=start,
                        root_end=cursor,
                        candidate_context=candidate_context,
                    )
                    member_access = True
                    member_arguments = (
                        *member_arguments,
                        *nested_arguments,
                    )
        for method_arguments in member_arguments:
            if depth < MAX_TEMPLATE_NESTING:
                for argument in _split_top_level_args(method_arguments):
                    nested, nested_dynamic = _scan_template_segment(
                        argument,
                        depth=depth + 1,
                        candidate_context=candidate_context,
                        binding_value=_is_transport_only_argument(
                            argument
                        ),
                    )
                    exact.update(nested)
                    unresolved.extend(nested_dynamic)
            elif method_arguments.strip():
                # An unscanned eager argument can contain a locally bound
                # helper alias even when no canonical helper name is present.
                # Hitting the nesting bound is therefore always explicit.
                unresolved.append(
                    CandidateResolution(
                        complete=False,
                        limit_exceeded=True,
                        kind="resolution_limit",
                    )
                )
        lookahead = cursor
        while lookahead < len(value) and value[lookahead].isspace():
            lookahead += 1

        if callable_binding is not None and callable_binding.locally_bound:
            if lookahead < len(value) and value[lookahead] == "(":
                inner, end = _extract_balanced(value, lookahead, "(", ")")
                if inner is None:
                    unresolved.append(CandidateResolution())
                    cursor = lookahead + 1
                    continue
                if not callable_binding.complete:
                    # A local callable with unproven provenance may still be
                    # an alias of a Home Assistant entity helper.  It must not
                    # suppress target-membership uncertainty.
                    unresolved.append(
                        _binding_uncertainty(callable_binding)
                    )
                    if depth < MAX_TEMPLATE_NESTING:
                        nested, nested_dynamic = _scan_template_segment(
                            inner,
                            depth=depth + 1,
                            candidate_context=candidate_context,
                        )
                        exact.update(nested)
                        unresolved.extend(nested_dynamic)
                    cursor = end
                    continue
                if not callable_binding.entity_helpers:
                    unresolved.append(
                        CandidateResolution(
                            possible_entity_domains=(),
                            complete=True,
                            kind="ordinary_dynamic_template",
                            entity_selector_present=False,
                        )
                    )
                    if depth < MAX_TEMPLATE_NESTING:
                        nested, nested_dynamic = _scan_template_segment(
                            inner,
                            depth=depth + 1,
                            candidate_context=candidate_context,
                        )
                        exact.update(nested)
                        unresolved.extend(nested_dynamic)
                    cursor = end
                    continue
                if len(callable_binding.entity_helpers) != 1:
                    unresolved.append(CandidateResolution())
                    cursor = end
                    continue
                # A proven alias retains the exact canonical helper semantics.
                name = callable_binding.entity_helpers[0]
            elif (
                callable_binding.complete
                and callable_binding.entity_helpers == ("states",)
            ):
                # ``states`` is both callable and the official all-state
                # collection.  A proven alias retains bracket/domain/bare
                # collection semantics when it is not called.
                name = "states"
            elif binding_value:
                # Binding expressions may transport unresolved callable
                # provenance into a later selector-bearing use.  The binding
                # itself does not select an entity.
                continue
            elif (
                (
                    not callable_binding.complete
                    and (
                        member_access
                        or (
                            lookahead < len(value)
                            and value[lookahead] in {"[", "."}
                        )
                        or collection_use
                    )
                )
                or callable_binding.entity_helpers
            ):
                # Bracket, dot, bare collection iteration, or mixed helper
                # provenance can still select an exact Home Assistant entity.
                # Preserve bounded uncertainty instead of treating it as
                # ordinary formatting or dropping it as zero evidence.
                unresolved.append(
                    _binding_uncertainty(callable_binding)
                )
                continue
            elif name in ENTITY_TEMPLATE_HELPERS:
                # A reviewed non-call use of a shadowing local is ordinary
                # template dataflow only after callable provenance has been
                # excluded above.
                unresolved.append(
                    CandidateResolution(
                        possible_entity_domains=(),
                        complete=True,
                        kind="ordinary_dynamic_template",
                        entity_selector_present=False,
                    )
                )
                continue

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
        if name == "states" and not binding_value:
            # Bare ``states`` is the official all-state collection.  It can
            # select the target helper and therefore must remain explicitly
            # unresolved rather than disappearing as zero evidence.
            unresolved.append(
                CandidateResolution(kind="unrestricted_state_collection")
            )
    return exact, unresolved


def _binding_uncertainty(
    binding: CallableBindingResolution,
) -> CandidateResolution:
    if binding.limit_exceeded:
        return CandidateResolution(
            complete=False,
            limit_exceeded=True,
            kind="resolution_limit",
        )
    return CandidateResolution()


def _is_transport_only_argument(value: str) -> bool:
    """Return whether an argument is bounded value transport, not consumption.

    This deliberately small grammar distinguishes a bare helper (or a finite
    container/conditional carrying it) from filters, calls, subscriptions, and
    other operations that may eagerly consume an entity collection.
    """

    try:
        node = ast.parse(value.strip(), mode="eval").body
    except (RecursionError, SyntaxError, ValueError):
        return False

    def transport_only(candidate: ast.AST, *, depth: int = 0) -> bool:
        if depth > MAX_TEMPLATE_NESTING:
            return False
        if isinstance(candidate, (ast.Name, ast.Constant)):
            return True
        if isinstance(candidate, (ast.List, ast.Tuple, ast.Set)):
            return all(
                transport_only(item, depth=depth + 1)
                for item in candidate.elts
            )
        if isinstance(candidate, ast.Dict):
            return all(
                key is not None
                and transport_only(key, depth=depth + 1)
                and transport_only(item, depth=depth + 1)
                for key, item in zip(candidate.keys, candidate.values)
            )
        if isinstance(candidate, ast.IfExp):
            return all(
                transport_only(item, depth=depth + 1)
                for item in (
                    candidate.test,
                    candidate.body,
                    candidate.orelse,
                )
            )
        return False

    return transport_only(node)


def _scan_unsupported_attr_filters(
    value: str,
    *,
    candidate_context: BoundedTemplateContext | None,
) -> list[CandidateResolution]:
    """Fail closed for unsupported Jinja attribute projection.

    Dot, bracket, and reviewed method calls have exact bounded handling.  The
    Jinja ``attr`` filter is attribute-only and can retrieve a bound dictionary
    method from an arbitrarily wrapped operand. Collection ``map`` can likewise
    project a method through either ``map('attr', ...)`` or
    ``map(attribute=...)``. Until those separate operators are modeled exactly,
    method or dynamic projections remain one bounded selector uncertainty rather
    than depending on a fragile operand regex. The scan deliberately skips
    quoted display text and preserves the reviewed exact ``entity_id`` map path.
    """

    transported = _scan_consumed_pipeline_selector_transport(
        value,
        candidate_context=candidate_context,
    )
    if transported:
        return transported

    cursor = 0
    scan_budget = MAX_LITERAL_ARGUMENTS
    while cursor < len(value):
        if value[cursor] in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if value[cursor] != "|":
            cursor += 1
            continue
        name_start = cursor + 1
        while name_start < len(value) and value[name_start].isspace():
            name_start += 1
        name_end = name_start
        while name_end < len(value) and (
            value[name_end].isalnum() or value[name_end] == "_"
        ):
            name_end += 1
        filter_name = value[name_start:name_end]
        argument_start = name_end
        while argument_start < len(value) and value[argument_start].isspace():
            argument_start += 1
        if filter_name == "attr" and (
            argument_start < len(value) and value[argument_start] == "("
        ):
            if scan_budget <= 0:
                return [_candidate_resolution_limit()]
            scan_budget -= 1
            inner, _end = _extract_balanced(
                value, argument_start, "(", ")"
            )
            return [
                CandidateResolution()
                if inner is not None
                else _candidate_resolution_limit()
            ]
        if filter_name != ENTITY_COLLECTION_MAP_FILTER or not (
            argument_start < len(value) and value[argument_start] == "("
        ):
            cursor = max(cursor + 1, name_end)
            continue
        if scan_budget <= 0:
            return [_candidate_resolution_limit()]
        scan_budget -= 1
        inner, end = _extract_balanced(
            value, argument_start, "(", ")"
        )
        if inner is None:
            # Stop after one unmatched suffix. Retrying at every later pipe
            # would turn the bounded scan quadratic on malformed input.
            return [_candidate_resolution_limit()]
        arguments = _split_top_level_args(inner)
        if arguments:
            first_name = _literal_operator_name(arguments[0])
            if first_name == "attr":
                return [CandidateResolution()]
            for argument in arguments:
                attribute = re.fullmatch(
                    r"\s*attribute\s*=\s*(?P<value>.+?)\s*",
                    argument,
                    re.DOTALL,
                )
                if attribute is None:
                    continue
                attribute_name = _literal_operator_name(
                    attribute.group("value")
                )
                if (
                    attribute_name is None
                    or attribute_name in MAPPING_READ_METHODS
                ):
                    return [CandidateResolution()]
        cursor = max(end, cursor + 1)
    return []


def _scan_consumed_pipeline_selector_transport(
    value: str,
    *,
    candidate_context: BoundedTemplateContext | None,
) -> list[CandidateResolution]:
    """Fail closed when a helper-bearing pipeline result is consumed.

    The bounded template evaluator already knows whether the pipeline input is
    a finite ordinary value or can carry an entity helper. This scanner does
    not interpret filters: it recognizes only a grouped pipeline whose result
    is immediately called, subscribed, or accessed as a member. Proven ordinary
    inputs remain low-friction; helper-bearing or unresolved inputs remain one
    deterministic selector uncertainty shared by risk and F3 locking.
    """

    if candidate_context is None or "|" not in value:
        return []
    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = frozenset(pairs.values())
    stack: list[tuple[str, int]] = []
    inspected = 0
    work_budget = [MAX_LITERAL_ARGUMENTS]
    cursor = 0
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if char in pairs:
            stack.append((char, cursor))
            cursor += 1
            continue
        if char in closers and (
            not stack or char != pairs[stack[-1][0]]
        ):
            return [_candidate_resolution_limit()]
        if not stack or char != pairs[stack[-1][0]]:
            cursor += 1
            continue
        _opener, start = stack.pop()
        suffix = cursor + 1
        while suffix < len(value) and value[suffix].isspace():
            suffix += 1
        if suffix >= len(value) or value[suffix] not in {"(", "[", "."}:
            cursor += 1
            continue
        inspected += 1
        if inspected > MAX_LITERAL_ARGUMENTS:
            return [_candidate_resolution_limit()]
        inner = value[start + 1 : cursor].strip()
        resolution = _fragment_pipeline_selector_transport(
            inner,
            candidate_context=candidate_context,
            work_budget=work_budget,
        )
        if resolution is not None:
            return [resolution]
        cursor += 1
    if stack:
        return [_candidate_resolution_limit()]
    return []


def _fragment_pipeline_selector_transport(
    value: str,
    *,
    candidate_context: BoundedTemplateContext,
    work_budget: list[int],
    depth: int = 0,
) -> CandidateResolution | None:
    """Inspect bounded pipeline sources and arguments inside one wrapper."""

    if depth > MAX_TEMPLATE_NESTING or work_budget[0] <= 0:
        return _candidate_resolution_limit()
    work_budget[0] -= 1

    pipeline = _split_top_level_pipeline(value)
    if len(pipeline) > 1:
        expressions = [pipeline[0]]
        for stage in pipeline[1:]:
            parsed = _parse_pipeline_stage(stage)
            if parsed is None:
                return CandidateResolution(
                    kind="unresolved_pipeline_selector_transport"
                )
            filter_name, arguments = parsed
            if filter_name not in PIPELINE_PROVENANCE_FILTERS:
                return CandidateResolution(
                    kind="unresolved_pipeline_selector_transport"
                )
            if arguments is None:
                return CandidateResolution(
                    kind="unresolved_pipeline_selector_transport"
                )
            if filter_name == ENTITY_COLLECTION_MAP_FILTER and arguments:
                first_argument = arguments[0]
                if not re.match(r"\s*attribute\s*=", first_argument):
                    operator_name = _literal_operator_name(first_argument)
                    if operator_name not in (
                        ENTITY_TEMPLATE_FILTERS | {"attr"}
                    ):
                        return CandidateResolution(
                            kind="unresolved_pipeline_selector_transport"
                        )
            introduced_arguments: list[str] = []
            if filter_name == "default":
                # Only positional argument one or ``default_value=`` can be
                # returned. ``boolean=`` changes false-value selection but
                # cannot introduce helper provenance.
                positional_count = 0
                saw_default_value = False
                saw_boolean = False
                saw_keyword = False
                for argument in arguments:
                    assignment = re.fullmatch(
                        r"\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
                        r"(?P<value>.+?)\s*",
                        argument,
                        re.DOTALL,
                    )
                    if assignment is None:
                        if saw_keyword:
                            return CandidateResolution(
                                kind="unresolved_pipeline_selector_transport"
                            )
                        positional_count += 1
                        if positional_count == 1 and not saw_default_value:
                            introduced_arguments.append(argument)
                            saw_default_value = True
                        elif positional_count == 2 and not saw_boolean:
                            saw_boolean = True
                        elif positional_count > 2:
                            return CandidateResolution(
                                kind="unresolved_pipeline_selector_transport"
                            )
                        else:
                            return CandidateResolution(
                                kind="unresolved_pipeline_selector_transport"
                            )
                        continue
                    saw_keyword = True
                    name = assignment.group("name")
                    if name == "default_value" and not saw_default_value:
                        introduced_arguments.append(
                            assignment.group("value")
                        )
                        saw_default_value = True
                    elif name == "boolean" and not saw_boolean:
                        saw_boolean = True
                    else:
                        return CandidateResolution(
                            kind="unresolved_pipeline_selector_transport"
                        )
            elif filter_name == ENTITY_COLLECTION_MAP_FILTER:
                for argument in arguments:
                    assignment = re.fullmatch(
                        r"\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
                        r"(?P<value>.+?)\s*",
                        argument,
                        re.DOTALL,
                    )
                    if (
                        assignment is not None
                        and assignment.group("name") == "default"
                    ):
                        introduced_arguments.append(
                            assignment.group("value")
                        )
            elif filter_name in {"batch", "groupby", "slice"}:
                introduced_keyword = (
                    "default" if filter_name == "groupby" else "fill_with"
                )
                for index, argument in enumerate(arguments):
                    assignment = re.fullmatch(
                        r"\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
                        r"(?P<value>.+?)\s*",
                        argument,
                        re.DOTALL,
                    )
                    if assignment is not None:
                        if assignment.group("name") == introduced_keyword:
                            introduced_arguments.append(
                                assignment.group("value")
                            )
                    elif index == 1:
                        introduced_arguments.append(argument)
            expressions.extend(introduced_arguments)
        for expression in expressions:
            binding = candidate_context.selector_transport_binding(
                expression
            )
            if (
                not binding.complete
                or binding.entity_helpers
                or binding.mapping_method is not None
            ):
                return CandidateResolution(
                    complete=False,
                    limit_exceeded=binding.limit_exceeded,
                    kind=(
                        "resolution_limit"
                        if binding.limit_exceeded
                        else "unresolved_pipeline_selector_transport"
                    ),
                )

    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[tuple[str, int]] = []
    cursor = 0
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if char in pairs:
            stack.append((char, cursor))
            cursor += 1
            continue
        if not stack or char != pairs[stack[-1][0]]:
            cursor += 1
            continue
        _opener, start = stack.pop()
        inner = value[start + 1 : cursor]
        if "|" in inner:
            nested = _fragment_pipeline_selector_transport(
                inner,
                candidate_context=candidate_context,
                work_budget=work_budget,
                depth=depth + 1,
            )
            if nested is not None:
                return nested
        cursor += 1
    return None


def _direct_member_binding(
    value: str,
    *,
    start: int,
    root_end: int,
    candidate_context: BoundedTemplateContext,
) -> tuple[
    CallableBindingResolution,
    int,
    bool,
    tuple[str, ...],
]:
    """Resolve a bounded local mapping member before selector classification.

    The first member that proves a reviewed entity helper becomes the
    canonical selector.  Literal nested mappings may be traversed within the
    existing nesting bound.  Dynamic or malformed member keys return
    incomplete local provenance and are never treated as target exclusion.
    """

    binding = candidate_context.member_binding(value[start:root_end])
    cursor = root_end
    consumed = False
    method_arguments: list[str] = []
    for _depth in range(MAX_TEMPLATE_NESTING):
        member_start = cursor
        while member_start < len(value) and value[member_start].isspace():
            member_start += 1
        member_end = member_start
        if member_start < len(value) and value[member_start] == ".":
            match = re.match(
                r"\.([A-Za-z_][A-Za-z0-9_]*)",
                value[member_start:],
            )
            if match is None:
                return (
                    CallableBindingResolution(locally_bound=True),
                    min(len(value), member_start + 1),
                    True,
                    tuple(method_arguments),
                )
            member_end = member_start + match.end()
            method_name = match.group(1)
            call_start = member_end
            while call_start < len(value) and value[call_start].isspace():
                call_start += 1
            if (
                method_name in MAPPING_READ_METHODS
                and call_start < len(value)
                and value[call_start] == "("
            ):
                inner, call_end = _extract_balanced(
                    value, call_start, "(", ")"
                )
                if inner is None:
                    return (
                        CallableBindingResolution(locally_bound=True),
                        call_end,
                        True,
                        tuple(method_arguments),
                    )
                method_arguments.append(inner)
                member_end = call_end
        elif member_start < len(value) and value[member_start] == "[":
            _inner, member_end = _extract_balanced(
                value, member_start, "[", "]"
            )
            if _inner is None:
                return (
                    CallableBindingResolution(locally_bound=True),
                    member_end,
                    True,
                    tuple(method_arguments),
                )
        else:
            break

        candidate = candidate_context.member_binding(
            value[start:member_end]
        )
        if not candidate.locally_bound:
            break
        binding = candidate
        cursor = member_end
        consumed = True
        if candidate.entity_helpers or not candidate.complete:
            break
        if not candidate.has_bounded_members:
            break
    else:
        next_cursor = cursor
        while next_cursor < len(value) and value[next_cursor].isspace():
            next_cursor += 1
        if next_cursor < len(value) and value[next_cursor] in {".", "["}:
            return (
                CallableBindingResolution(locally_bound=True),
                cursor,
                True,
                tuple(method_arguments),
            )
    return binding, cursor, consumed, tuple(method_arguments)


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
    depth: int = 0,
    scan_budget: list[int] | None = None,
) -> list[CandidateResolution]:
    """Project bounded candidates used by reviewed collection operators.

    Home Assistant exposes state-aware filters and tests through Jinja's
    ``map``/``select``/``reject`` collection operators.  These operators receive
    their entity candidates from the collection to the left of the pipeline,
    not from the quoted filter/test name.  Only a finite literal/context value
    or an exact ``states.<domain>`` collection is conclusive.  Any ambiguous
    collection or operator remains explicit incomplete evidence. Parenthesized
    and container-nested expressions are inspected recursively within the same
    static bounds; templates are never rendered.
    """

    if scan_budget is None:
        scan_budget = [MAX_LITERAL_ARGUMENTS]
    contains_collection_operator = _contains_collection_operator(value)
    malformed_collection_fragment = bool(
        contains_collection_operator
        and _has_unmatched_structural_delimiter(value)
    )
    if depth > MAX_TEMPLATE_NESTING:
        return (
            [
                CandidateResolution(
                    complete=False,
                    limit_exceeded=True,
                    kind="resolution_limit",
                )
            ]
            if contains_collection_operator
            else []
        )

    conditional = _split_top_level_conditional(value)
    if conditional is not None:
        resolutions: list[CandidateResolution] = []
        for branch in conditional:
            if not _contains_collection_operator(branch):
                continue
            if scan_budget[0] <= 0:
                resolutions.append(_candidate_resolution_limit())
                break
            scan_budget[0] -= 1
            resolutions.extend(
                _scan_template_collection_entity_operators(
                    branch,
                    candidate_context=candidate_context,
                    depth=depth + 1,
                    scan_budget=scan_budget,
                )
            )
        if malformed_collection_fragment:
            if scan_budget[0] > 0:
                scan_budget[0] -= 1
            resolutions.append(_candidate_resolution_limit())
        return _bound_candidate_resolutions(resolutions)

    resolutions = _scan_top_level_collection_entity_operator(
        value, candidate_context=candidate_context
    )
    if malformed_collection_fragment:
        if scan_budget[0] > 0:
            scan_budget[0] -= 1
        resolutions.append(_candidate_resolution_limit())
        return _bound_candidate_resolutions(resolutions)
    pairs = {"(": ")", "[": "]", "{": "}"}
    cursor = 0
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        closer = pairs.get(char)
        if closer is None:
            cursor += 1
            continue
        inner, end = _extract_balanced(value, cursor, char, closer)
        if inner is None:
            if _contains_collection_operator(value[cursor:]):
                if scan_budget[0] > 0:
                    scan_budget[0] -= 1
                resolutions.append(_candidate_resolution_limit())
            # The remaining fragment is malformed.  Do not retry every later
            # opener against the same suffix: that turns one bounded scan into
            # quadratic work.  One explicit limit result conservatively binds
            # the whole malformed remainder.
            break
        if scan_budget[0] <= 0 or depth >= MAX_TEMPLATE_NESTING:
            if _contains_collection_operator(inner):
                resolutions.append(_candidate_resolution_limit())
            cursor = end
            continue
        scan_budget[0] -= 1
        resolutions.extend(
            _scan_template_collection_entity_operators(
                inner,
                candidate_context=candidate_context,
                depth=depth + 1,
                scan_budget=scan_budget,
            )
        )
        cursor = end
    return _bound_candidate_resolutions(resolutions)


def _candidate_resolution_limit() -> CandidateResolution:
    return CandidateResolution(
        complete=False,
        limit_exceeded=True,
        kind="resolution_limit",
    )


def _bound_candidate_resolutions(
    resolutions: list[CandidateResolution],
) -> list[CandidateResolution]:
    if len(resolutions) <= MAX_LITERAL_ARGUMENTS:
        return resolutions
    return [
        *resolutions[: MAX_LITERAL_ARGUMENTS - 1],
        _candidate_resolution_limit(),
    ]


def _scan_top_level_collection_entity_operator(
    value: str,
    *,
    candidate_context: BoundedTemplateContext | None,
) -> list[CandidateResolution]:
    """Project the first reviewed operator in one outermost pipeline."""

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


def _contains_collection_operator(value: str) -> bool:
    """Return whether a bounded fragment contains a reviewed collection stage."""

    cursor = 0
    names = (
        ENTITY_COLLECTION_TEST_FILTERS
        | ENTITY_COLLECTION_ATTRIBUTE_TEST_FILTERS
        | {ENTITY_COLLECTION_MAP_FILTER}
    )
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if char != "|":
            cursor += 1
            continue
        start = cursor + 1
        while start < len(value) and value[start].isspace():
            start += 1
        end = start
        while end < len(value) and (
            value[end].isalnum() or value[end] == "_"
        ):
            end += 1
        if value[start:end] in names:
            return True
        cursor = max(cursor + 1, end)
    return False


def _has_unmatched_structural_delimiter(value: str) -> bool:
    """Return whether one static fragment has mismatched grouping syntax."""

    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = set(pairs.values())
    stack: list[str] = []
    cursor = 0
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if char in pairs:
            stack.append(pairs[char])
        elif char in closers:
            if not stack or stack[-1] != char:
                return True
            stack.pop()
        cursor += 1
    return bool(stack)


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


def _split_top_level_conditional(
    value: str,
) -> tuple[str, str, str] | None:
    """Split one bounded Jinja inline conditional without evaluating it."""

    stack: list[str] = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    if_start: int | None = None
    if_end: int | None = None
    cursor = 0
    while cursor < len(value):
        char = value[cursor]
        if char in {"'", '"'}:
            cursor = _skip_quoted(value, cursor)
            continue
        if char in pairs:
            stack.append(pairs[char])
            cursor += 1
            continue
        if stack and char == stack[-1]:
            stack.pop()
            cursor += 1
            continue
        if stack:
            cursor += 1
            continue
        if char.isalpha() or char == "_":
            start = cursor
            cursor += 1
            while cursor < len(value) and (
                value[cursor].isalnum() or value[cursor] == "_"
            ):
                cursor += 1
            token = value[start:cursor]
            if token == "if" and if_start is None:
                if_start = start
                if_end = cursor
            elif token == "else" and if_start is not None:
                true_branch = value[:if_start].strip()
                condition = value[if_end:start].strip()
                false_branch = value[cursor:].strip()
                if true_branch and condition and false_branch:
                    return true_branch, condition, false_branch
                return None
            continue
        cursor += 1
    return None


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
    *,
    depth: int = 0,
) -> CandidateResolution:
    if depth > MAX_TEMPLATE_NESTING:
        return CandidateResolution(
            complete=False,
            limit_exceeded=True,
            kind="resolution_limit",
        )
    bounded = expression.strip()
    if not bounded:
        return CandidateResolution()
    if len(bounded) > MAX_TEMPLATE_ARGUMENT_CHARS:
        return CandidateResolution(
            complete=False,
            limit_exceeded=True,
            kind="resolution_limit",
        )
    if bounded.startswith("("):
        inner, end = _extract_balanced(bounded, 0, "(", ")")
        if inner is not None and end == len(bounded):
            return _resolve_collection_candidate_expression(
                inner,
                candidate_context,
                depth=depth + 1,
            )
    conditional = _split_top_level_conditional(bounded)
    if conditional is not None:
        true_branch, _condition, false_branch = conditional
        return _merge_collection_candidate_resolutions(
            (
                _resolve_collection_candidate_expression(
                    true_branch,
                    candidate_context,
                    depth=depth + 1,
                ),
                _resolve_collection_candidate_expression(
                    false_branch,
                    candidate_context,
                    depth=depth + 1,
                ),
            )
        )
    pipeline = _split_top_level_pipeline(bounded)
    if len(pipeline) > 1:
        if not _pipeline_preserves_collection_candidates(pipeline[1:]):
            return CandidateResolution()
        return _resolve_collection_candidate_expression(
            pipeline[0],
            candidate_context,
            depth=depth + 1,
        )
    literals = _literal_string_arguments(bounded)
    if literals is not None:
        entity_ids = tuple(
            sorted({item for item in literals if valid_entity_id(item)})
        )
        complete = len(entity_ids) == len(literals)
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


def _merge_collection_candidate_resolutions(
    resolutions: tuple[CandidateResolution, ...],
) -> CandidateResolution:
    entity_ids = tuple(
        sorted(
            {
                entity_id
                for resolution in resolutions
                for entity_id in resolution.entity_ids
            }
        )
    )
    labels = tuple(
        sorted(
            {
                label
                for resolution in resolutions
                for label in resolution.literal_label_selectors
            }
        )
    )
    limit_exceeded = any(
        resolution.limit_exceeded for resolution in resolutions
    )
    complete = all(resolution.complete for resolution in resolutions)
    if (
        len(entity_ids) > MAX_LITERAL_ARGUMENTS
        or len(labels) > MAX_DYNAMIC_LABEL_SELECTORS
    ):
        limit_exceeded = True
        complete = False
    entity_ids = entity_ids[:MAX_LITERAL_ARGUMENTS]
    labels = labels[:MAX_DYNAMIC_LABEL_SELECTORS]
    domains: tuple[str, ...] | None = None
    if complete and not labels:
        domain_values: set[str] = set()
        for resolution in resolutions:
            if resolution.possible_entity_domains is None:
                complete = False
                break
            domain_values.update(resolution.possible_entity_domains)
        if complete:
            domains = tuple(sorted(domain_values))
    return CandidateResolution(
        entity_ids=entity_ids,
        literal_label_selectors=labels,
        possible_entity_domains=domains,
        complete=complete,
        limit_exceeded=limit_exceeded,
        kind=(
            "resolution_limit"
            if limit_exceeded
            else (
                "finite_conditional_collection"
                if complete
                else "unresolved"
            )
        ),
    )


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
