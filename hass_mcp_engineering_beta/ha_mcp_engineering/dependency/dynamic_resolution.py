"""Bounded static candidate resolution for Home Assistant entity templates.

This module deliberately implements a small reviewed expression grammar.  It
never renders Jinja and never calls Home Assistant template helpers.  Its only
purpose is to prove finite candidate sets (or an exact non-target domain) for
dependency analysis.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
import re
from typing import Callable


MAX_DYNAMIC_EXPRESSION_CHARS = 4_096
MAX_DYNAMIC_CANDIDATES = 128
MAX_DYNAMIC_LABEL_SELECTORS = 32
MAX_DYNAMIC_NESTING = 8

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_SET_STATEMENT = re.compile(
    rf"^\s*set\s+(?P<name>{_NAME})\s*=\s*(?P<expression>.+?)\s*$",
    re.DOTALL,
)
_FOR_STATEMENT = re.compile(
    rf"^\s*for\s+(?P<name>{_NAME})\s+in\s+(?P<expression>.+?)\s*$",
    re.DOTALL,
)
_END_FOR_STATEMENT = re.compile(r"^\s*endfor\b")


@dataclass(frozen=True)
class CandidateResolution:
    """Public bounded result for one dynamic entity expression."""

    entity_ids: tuple[str, ...] = ()
    literal_label_selectors: tuple[str, ...] = ()
    possible_entity_domains: tuple[str, ...] | None = None
    complete: bool = False
    limit_exceeded: bool = False
    kind: str = "unresolved"


@dataclass
class _StaticValue:
    entity_ids: set[str] = field(default_factory=set)
    literal_strings: set[str] = field(default_factory=set)
    labels: set[str] = field(default_factory=set)
    fields: dict[str, "_StaticValue"] = field(default_factory=dict)
    iteration: "_StaticValue | None" = None
    complete: bool = True
    limit_exceeded: bool = False
    kinds: set[str] = field(default_factory=set)


def _empty_incomplete(*, limit: bool = False) -> _StaticValue:
    return _StaticValue(
        complete=False,
        limit_exceeded=limit,
        kinds={"resolution_limit" if limit else "unresolved"},
    )


def _iteration_copy(value: _StaticValue) -> _StaticValue:
    """Copy finite iterable material without a self-referential value."""

    return _StaticValue(
        entity_ids=set(value.entity_ids),
        literal_strings=set(value.literal_strings),
        labels=set(value.labels),
        fields=dict(value.fields),
        complete=value.complete,
        limit_exceeded=value.limit_exceeded,
        kinds=set(value.kinds),
    )


def _merge_values(
    values: list[_StaticValue], *, depth: int = 0
) -> _StaticValue:
    if depth > MAX_DYNAMIC_NESTING:
        return _empty_incomplete(limit=True)
    result = _StaticValue()
    field_groups: dict[str, list[_StaticValue]] = {}
    iteration_values: list[_StaticValue] = []
    for value in values:
        result.entity_ids.update(value.entity_ids)
        result.literal_strings.update(value.literal_strings)
        result.labels.update(value.labels)
        result.complete = result.complete and value.complete
        result.limit_exceeded = (
            result.limit_exceeded or value.limit_exceeded
        )
        result.kinds.update(value.kinds)
        for name, field_value in value.fields.items():
            field_groups.setdefault(name, []).append(field_value)
        if value.iteration is not None:
            iteration_values.append(value.iteration)
    if (
        len(result.entity_ids) > MAX_DYNAMIC_CANDIDATES
        or len(result.labels) > MAX_DYNAMIC_LABEL_SELECTORS
    ):
        result.complete = False
        result.limit_exceeded = True
        result.kinds.add("resolution_limit")
        result.entity_ids = set(
            sorted(result.entity_ids)[:MAX_DYNAMIC_CANDIDATES]
        )
        result.labels = set(
            sorted(result.labels)[:MAX_DYNAMIC_LABEL_SELECTORS]
        )
    result.fields = {
        name: _merge_values(group, depth=depth + 1)
        for name, group in sorted(field_groups.items())
    }
    if iteration_values:
        result.iteration = _merge_values(
            iteration_values, depth=depth + 1
        )
    return result


class BoundedTemplateContext:
    """Track finite ``set`` and ``for`` bindings while scanning one template."""

    def __init__(self, entity_id_validator: Callable[[str], bool]):
        self._valid_entity_id = entity_id_validator
        self._bindings: dict[str, _StaticValue] = {}
        self._loop_stack: list[tuple[str, _StaticValue | None]] = []

    def apply_statement(self, statement: str) -> None:
        """Apply only reviewed binding statements; ignore all other Jinja."""

        if len(statement) > MAX_DYNAMIC_EXPRESSION_CHARS:
            return
        if _END_FOR_STATEMENT.match(statement):
            if self._loop_stack:
                name, prior = self._loop_stack.pop()
                if prior is None:
                    self._bindings.pop(name, None)
                else:
                    self._bindings[name] = prior
            return
        match = _SET_STATEMENT.match(statement)
        if match is not None:
            name = match.group("name")
            value = self._evaluate(match.group("expression"))
            prior = self._bindings.get(name)
            # Repeated assignments can represent conditional Jinja branches.
            # Unioning them is conservative and still finite.
            self._bindings[name] = (
                _merge_values([prior, value])
                if prior is not None
                else value
            )
            return
        match = _FOR_STATEMENT.match(statement)
        if match is None:
            return
        name = match.group("name")
        collection = self._evaluate(match.group("expression"))
        loop_value = collection.iteration or collection
        self._loop_stack.append((name, self._bindings.get(name)))
        self._bindings[name] = loop_value

    def resolve(self, expression: str) -> CandidateResolution:
        if len(expression) > MAX_DYNAMIC_EXPRESSION_CHARS:
            return CandidateResolution(
                complete=False,
                limit_exceeded=True,
                kind="resolution_limit",
            )
        value = self._evaluate(expression)
        invalid_values = bool(value.literal_strings)
        complete = bool(
            value.complete
            and not invalid_values
            and (value.entity_ids or value.labels)
        )
        domains = {
            item.split(".", 1)[0] for item in value.entity_ids
        }
        kind = "unresolved"
        if complete:
            if value.labels and value.entity_ids:
                kind = "label_and_finite_candidates"
            elif value.labels:
                kind = "literal_label_selector"
            elif "conditional" in value.kinds:
                kind = "finite_conditional"
            elif "mapping" in value.kinds:
                kind = "finite_mapping"
            else:
                kind = "finite_candidates"
        elif value.limit_exceeded:
            kind = "resolution_limit"
        return CandidateResolution(
            entity_ids=tuple(sorted(value.entity_ids)),
            literal_label_selectors=tuple(sorted(value.labels)),
            possible_entity_domains=(
                tuple(sorted(domains))
                if complete and not value.labels and domains
                else None
            ),
            complete=complete,
            limit_exceeded=value.limit_exceeded,
            kind=kind,
        )

    def _evaluate(self, expression: str) -> _StaticValue:
        if len(expression) > MAX_DYNAMIC_EXPRESSION_CHARS:
            return _empty_incomplete(limit=True)
        try:
            node = ast.parse(expression.strip(), mode="eval").body
        except (RecursionError, SyntaxError, ValueError):
            return _empty_incomplete()
        return self._evaluate_node(node, depth=0)

    def _evaluate_node(self, node: ast.AST, *, depth: int) -> _StaticValue:
        if depth > MAX_DYNAMIC_NESTING:
            return _empty_incomplete(limit=True)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if self._valid_entity_id(text):
                return _StaticValue(
                    entity_ids={text}, kinds={"literal_entity"}
                )
            if len(text) <= 128 and all(
                character >= " " for character in text
            ):
                return _StaticValue(
                    literal_strings={text}, kinds={"literal_string"}
                )
            return _empty_incomplete(limit=len(text) > 128)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            if len(node.elts) > MAX_DYNAMIC_CANDIDATES:
                return _empty_incomplete(limit=True)
            items = [
                self._evaluate_node(item, depth=depth + 1)
                for item in node.elts
            ]
            merged = _merge_values(items, depth=depth + 1)
            result = _merge_values([merged], depth=depth + 1)
            result.iteration = merged
            result.kinds.add("finite_collection")
            return result
        if isinstance(node, ast.Dict):
            if len(node.keys) > MAX_DYNAMIC_CANDIDATES:
                return _empty_incomplete(limit=True)
            fields: dict[str, _StaticValue] = {}
            for key, value_node in zip(node.keys, node.values):
                if not (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and len(key.value) <= 128
                ):
                    return _empty_incomplete()
                fields[key.value] = self._evaluate_node(
                    value_node, depth=depth + 1
                )
            return _StaticValue(
                fields=fields,
                complete=all(value.complete for value in fields.values()),
                limit_exceeded=any(
                    value.limit_exceeded for value in fields.values()
                ),
                kinds={"mapping"},
            )
        if isinstance(node, ast.Name):
            return self._bindings.get(node.id, _empty_incomplete())
        if isinstance(node, ast.Attribute):
            base = self._evaluate_node(node.value, depth=depth + 1)
            result = base.fields.get(node.attr, _empty_incomplete())
            if base.fields:
                result.kinds.add("mapping")
            return result
        if isinstance(node, ast.Subscript):
            base = self._evaluate_node(node.value, depth=depth + 1)
            key = self._evaluate_node(node.slice, depth=depth + 1)
            selected = [
                base.fields[item]
                for item in sorted(key.literal_strings)
                if item in base.fields
            ]
            if selected and key.complete:
                result = _merge_values(selected, depth=depth + 1)
                result.kinds.add("mapping")
                return result
            if base.fields:
                # An unknown key can select only one of these finite mapping
                # values. Missing keys fail the template rather than creating a
                # candidate outside the mapping.
                result = _merge_values(
                    list(base.fields.values()), depth=depth + 1
                )
                result.kinds.add("mapping")
                return result
            return _empty_incomplete()
        if isinstance(node, ast.IfExp):
            value = _merge_values(
                [
                    self._evaluate_node(node.body, depth=depth + 1),
                    self._evaluate_node(node.orelse, depth=depth + 1),
                ],
                depth=depth + 1,
            )
            value.kinds.add("conditional")
            return value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            value = _merge_values(
                [
                    self._evaluate_node(node.left, depth=depth + 1),
                    self._evaluate_node(node.right, depth=depth + 1),
                ],
                depth=depth + 1,
            )
            value.iteration = _iteration_copy(value)
            value.kinds.add("finite_union")
            return value
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "label_entities"
                and len(node.args) == 1
                and not node.keywords
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and 0 < len(node.args[0].value) <= 128
                and all(character >= " " for character in node.args[0].value)
            ):
                return _StaticValue(
                    labels={node.args[0].value},
                    kinds={"literal_label_selector"},
                )
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "values"
                and not node.args
                and not node.keywords
            ):
                base = self._evaluate_node(
                    node.func.value, depth=depth + 1
                )
                if base.fields:
                    value = _merge_values(
                        list(base.fields.values()), depth=depth + 1
                    )
                    value.iteration = _iteration_copy(value)
                    value.kinds.add("mapping")
                    return value
            return _empty_incomplete()
        # Boolean operators, filters, arbitrary calls, macros, and other Jinja
        # features are intentionally outside the reviewed grammar.
        return _empty_incomplete()


__all__ = [
    "BoundedTemplateContext",
    "CandidateResolution",
    "MAX_DYNAMIC_CANDIDATES",
    "MAX_DYNAMIC_EXPRESSION_CHARS",
    "MAX_DYNAMIC_LABEL_SELECTORS",
    "MAX_DYNAMIC_NESTING",
]
