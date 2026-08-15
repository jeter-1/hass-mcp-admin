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
_IF_STATEMENT = re.compile(r"^\s*if\b")
_END_IF_STATEMENT = re.compile(r"^\s*endif\b")
_ELSE_STATEMENT = re.compile(r"^\s*else\b")
_ELIF_STATEMENT = re.compile(r"^\s*elif\b")
_UNREVIEWED_SCOPE_STATEMENT = re.compile(
    r"^\s*(?:macro|endmacro|call|endcall|block|endblock|filter|endfilter|with|endwith)\b"
)
_MACRO_SCOPE_STATEMENT = re.compile(
    rf"^\s*macro\s+{_NAME}\s*\((?P<arguments>.*?)\)", re.DOTALL
)
_WITH_SCOPE_STATEMENT = re.compile(
    r"^\s*with\s+(?P<bindings>.*?)\s*$", re.DOTALL
)
_CALL_SCOPE_STATEMENT = re.compile(
    r"^\s*call\s*\((?P<arguments>.*?)\)", re.DOTALL
)
_SCOPE_ASSIGNMENT_NAME = re.compile(rf"(?:^|,)\s*(?P<name>{_NAME})\s*=")


@dataclass(frozen=True)
class CandidateResolution:
    """Public bounded result for one dynamic entity expression."""

    entity_ids: tuple[str, ...] = ()
    literal_label_selectors: tuple[str, ...] = ()
    possible_entity_domains: tuple[str, ...] | None = None
    complete: bool = False
    limit_exceeded: bool = False
    kind: str = "unresolved"
    entity_selector_present: bool = True


@dataclass(frozen=True)
class CallableBindingResolution:
    """Bounded provenance for one locally bound callable name."""

    locally_bound: bool = False
    complete: bool = False
    entity_helpers: tuple[str, ...] = ()
    has_bounded_members: bool = False


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
    entity_helpers: set[str] = field(default_factory=set)


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
        entity_helpers=set(value.entity_helpers),
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
        result.entity_helpers.update(value.entity_helpers)
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

    def __init__(
        self,
        entity_id_validator: Callable[[str], bool],
        *,
        entity_helper_names: frozenset[str] = frozenset(),
    ):
        self._valid_entity_id = entity_id_validator
        self._entity_helper_names = entity_helper_names
        self._bindings: dict[str, _StaticValue] = {}
        self._trusted_bindings: set[str] = set()
        self._loop_stack: list[
            tuple[str, _StaticValue | None, bool]
        ] = []
        self._control_stack: list[str] = []
        self._conditional_depth = 0
        self._control_flow_valid = True
        self._binding_analysis_enabled = True
        self._uncertain_bindings: set[str] = set()
        self._uncertain_binding_overflow = False

    def apply_statement(self, statement: str) -> None:
        """Apply only reviewed binding statements; ignore all other Jinja."""

        if len(statement) > MAX_DYNAMIC_EXPRESSION_CHARS:
            self._control_flow_valid = False
            self._binding_analysis_enabled = False
            self._bindings.clear()
            self._trusted_bindings.clear()
            return
        if _UNREVIEWED_SCOPE_STATEMENT.match(statement):
            # Macro/call/block/filter/with scopes have binding semantics outside
            # this deliberately small grammar.  Preserve the names that can
            # flow through that scope as uncertain rather than erasing their
            # possible Home Assistant entity-helper provenance.
            self._remember_uncertain_bindings(
                (
                    *self._bindings,
                    *self._unreviewed_scope_binding_names(statement),
                )
            )
            self._binding_analysis_enabled = False
            self._bindings.clear()
            self._trusted_bindings.clear()
            return
        if not self._binding_analysis_enabled:
            for pattern in (_SET_STATEMENT, _FOR_STATEMENT):
                match = pattern.match(statement)
                if match is not None:
                    self._remember_uncertain_bindings((match.group("name"),))
                    break
            return
        if _IF_STATEMENT.match(statement):
            self._conditional_depth += 1
            self._control_stack.append("if")
            return
        if _END_IF_STATEMENT.match(statement):
            if not self._control_stack or self._control_stack[-1] != "if":
                self._control_flow_valid = False
                self._binding_analysis_enabled = False
                self._bindings.clear()
                self._trusted_bindings.clear()
                return
            self._control_stack.pop()
            self._conditional_depth = max(
                0, self._conditional_depth - 1
            )
            return
        if _ELIF_STATEMENT.match(statement) or _ELSE_STATEMENT.match(statement):
            if not self._control_stack:
                self._control_flow_valid = False
                self._binding_analysis_enabled = False
                self._bindings.clear()
                self._trusted_bindings.clear()
                return
            if self._control_stack[-1] == "for":
                # Jinja's loop target is not a proven binding in a ``for``
                # ``else`` branch because that branch runs when iteration did
                # not establish the target.
                name, prior, prior_trusted = self._loop_stack[-1]
                if prior is None:
                    self._bindings.pop(name, None)
                else:
                    self._bindings[name] = prior
                if prior_trusted:
                    self._trusted_bindings.add(name)
                else:
                    self._trusted_bindings.discard(name)
            return
        if _END_FOR_STATEMENT.match(statement):
            if (
                not self._loop_stack
                or not self._control_stack
                or self._control_stack[-1] != "for"
            ):
                self._control_flow_valid = False
                self._binding_analysis_enabled = False
                self._bindings.clear()
                self._trusted_bindings.clear()
                return
            self._control_stack.pop()
            name, prior, prior_trusted = self._loop_stack.pop()
            if prior is None:
                self._bindings.pop(name, None)
            else:
                self._bindings[name] = prior
            if prior_trusted:
                self._trusted_bindings.add(name)
            else:
                self._trusted_bindings.discard(name)
            return
        match = _SET_STATEMENT.match(statement)
        if match is not None:
            name = match.group("name")
            value = self._evaluate(match.group("expression"))
            prior = self._bindings.get(name)
            prior_trusted = name in self._trusted_bindings
            # Repeated assignments can represent conditional Jinja branches.
            # Unioning them is conservative and still finite.
            self._bindings[name] = (
                _merge_values([prior, value])
                if prior is not None
                else value
            )
            if prior_trusted or (
                self._conditional_depth == 0
                and not self._loop_stack
            ):
                self._trusted_bindings.add(name)
            else:
                self._trusted_bindings.discard(name)
            return
        match = _FOR_STATEMENT.match(statement)
        if match is None:
            return
        name = match.group("name")
        collection = self._evaluate(match.group("expression"))
        loop_value = collection.iteration or collection
        self._loop_stack.append(
            (
                name,
                self._bindings.get(name),
                name in self._trusted_bindings,
            )
        )
        self._control_stack.append("for")
        self._bindings[name] = loop_value
        self._trusted_bindings.add(name)

    @staticmethod
    def binding_expression(statement: str) -> str | None:
        """Return the evaluated RHS/iterable for a reviewed binding."""

        for pattern in (_SET_STATEMENT, _FOR_STATEMENT):
            match = pattern.match(statement)
            if match is not None:
                return match.group("expression")
        return None

    @staticmethod
    def is_assignment_statement(statement: str) -> bool:
        """Return whether *statement* is a reviewed ``set`` assignment."""

        return _SET_STATEMENT.match(statement) is not None

    @staticmethod
    def is_iteration_statement(statement: str) -> bool:
        """Return whether *statement* is a reviewed ``for`` binding."""

        return _FOR_STATEMENT.match(statement) is not None

    def is_callable_alias_transport_statement(self, statement: str) -> bool:
        """Return whether a ``for`` iterable transports reviewed callables.

        A finite container such as ``[states]`` carries the callable into the
        loop target; it does not itself iterate Home Assistant's state
        collection.  Direct ``for item in states`` remains a collection read.
        """

        match = _FOR_STATEMENT.match(statement)
        if match is None:
            return False
        collection = self._evaluate(match.group("expression"))
        return bool(
            collection.iteration is not None
            and collection.iteration.entity_helpers
        )

    def is_locally_bound(self, name: str) -> bool:
        """Return whether reviewed template dataflow shadows a global helper."""

        return bool(
            (
                self._binding_analysis_enabled
                and name in self._trusted_bindings
            )
            or name in self._uncertain_bindings
            or self._uncertain_binding_overflow
        )

    def callable_binding(self, name: str) -> CallableBindingResolution:
        """Return bounded callable provenance for one reviewed binding.

        Control-flow trust alone is not sufficient to suppress a helper-like
        call: the assigned value may itself be a Home Assistant entity helper.
        Unknown or conditionally established values remain incomplete.
        """

        if (
            name in self._uncertain_bindings
            or self._uncertain_binding_overflow
        ):
            return CallableBindingResolution(locally_bound=True)
        if not self._binding_analysis_enabled or name not in self._bindings:
            return CallableBindingResolution()
        value = self._bindings[name]
        trusted = name in self._trusted_bindings
        return CallableBindingResolution(
            locally_bound=True,
            complete=bool(trusted and value.complete),
            entity_helpers=tuple(sorted(value.entity_helpers)),
            has_bounded_members=bool(value.fields),
        )

    def member_binding(self, expression: str) -> CallableBindingResolution:
        """Return helper provenance for one bounded direct member expression.

        Only literal dot and string-key member paths are conclusive.  A
        dynamic key, malformed path, missing member, or uncertain root remains
        locally bound but incomplete so callers cannot erase selector
        uncertainty.  No Jinja expression is executed.
        """

        if len(expression) > MAX_DYNAMIC_EXPRESSION_CHARS:
            return CallableBindingResolution(locally_bound=True)
        try:
            node = ast.parse(expression.strip(), mode="eval").body
        except (RecursionError, SyntaxError, ValueError):
            return CallableBindingResolution(locally_bound=True)

        root_name, bounded = self._bounded_member_root(node)
        if root_name is None:
            return CallableBindingResolution()
        root = self.callable_binding(root_name)
        if not root.locally_bound:
            return CallableBindingResolution()
        if not root.complete:
            return CallableBindingResolution(locally_bound=True)

        value = self._evaluate_node(node, depth=0)
        if not bounded and (
            not value.complete or value.entity_helpers
        ):
            # A computed key may choose any bounded member.  It is ordinary
            # only when every possible value is conclusively non-helper;
            # otherwise it cannot exclude selector provenance.
            return CallableBindingResolution(locally_bound=True)
        return CallableBindingResolution(
            locally_bound=True,
            complete=value.complete,
            entity_helpers=tuple(sorted(value.entity_helpers)),
            has_bounded_members=bool(value.fields),
        )

    @classmethod
    def _bounded_member_root(
        cls, node: ast.AST
    ) -> tuple[str | None, bool]:
        """Return the local root and whether every member key is literal."""

        if isinstance(node, ast.Name):
            return node.id, True
        if isinstance(node, ast.Attribute):
            return cls._bounded_member_root(node.value)
        if isinstance(node, ast.Subscript):
            root, bounded = cls._bounded_member_root(node.value)
            literal_key = bool(
                isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
                and 0 < len(node.slice.value) <= 128
            )
            return root, bool(bounded and literal_key)
        return None, False

    def _remember_uncertain_bindings(self, names: tuple[str, ...]) -> None:
        """Retain a deterministic bounded set of unreviewed local names."""

        combined = sorted(self._uncertain_bindings.union(names))
        if len(combined) > MAX_DYNAMIC_CANDIDATES:
            self._uncertain_binding_overflow = True
        self._uncertain_bindings = set(
            combined[:MAX_DYNAMIC_CANDIDATES]
        )

    @staticmethod
    def _unreviewed_scope_binding_names(statement: str) -> set[str]:
        """Return bounded local names introduced by an unreviewed scope."""

        parameter_scope = _MACRO_SCOPE_STATEMENT.match(statement)
        if parameter_scope is None:
            parameter_scope = _CALL_SCOPE_STATEMENT.match(statement)
        if parameter_scope is not None:
            names: set[str] = set()
            for argument in parameter_scope.group("arguments").split(","):
                candidate = argument.split("=", 1)[0].strip().lstrip("*")
                if re.fullmatch(_NAME, candidate):
                    names.add(candidate)
            return names
        with_scope = _WITH_SCOPE_STATEMENT.match(statement)
        if with_scope is None:
            return set()
        return {
            match.group("name")
            for match in _SCOPE_ASSIGNMENT_NAME.finditer(
                with_scope.group("bindings")
            )
        }

    @property
    def control_flow_complete(self) -> bool:
        """Return whether reviewed binding scopes closed without mismatch."""

        return bool(
            self._control_flow_valid
            and not self._control_stack
            and not self._loop_stack
            and self._conditional_depth == 0
        )

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
            if (
                self._binding_analysis_enabled
                and node.id in self._trusted_bindings
            ):
                return self._bindings.get(node.id, _empty_incomplete())
            if node.id in self._entity_helper_names:
                return _StaticValue(
                    entity_helpers={node.id},
                    kinds={"entity_helper_callable"},
                )
            return _empty_incomplete()
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
    "CallableBindingResolution",
    "CandidateResolution",
    "MAX_DYNAMIC_CANDIDATES",
    "MAX_DYNAMIC_EXPRESSION_CHARS",
    "MAX_DYNAMIC_LABEL_SELECTORS",
    "MAX_DYNAMIC_NESTING",
]
