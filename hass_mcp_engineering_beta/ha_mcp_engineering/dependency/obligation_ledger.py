"""Whole-template, parse-only Home Assistant dependency obligations.

The analyzer is an abstract interpreter over the pinned Jinja AST.  It never
loads, compiles, renders, or invokes a template.  Every dependency-sensitive
construct terminates in exact evidence, dependency-neutral evidence, bounded
semantic opacity, or coverage failure.  Unknown nodes and callables have an
unconditional opaque fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Callable, Iterable

from jinja2 import TemplateSyntaxError, nodes
from jinja2.sandbox import ImmutableSandboxedEnvironment

from .models import DependencyObligation, evidence_id
from .semantic_registry import (
    SEMANTIC_REGISTRY_MODEL,
    semantic_category,
    semantic_registry_identity,
)


MAX_TEMPLATE_SOURCE_CHARS = 65_536
MAX_TEMPLATE_AST_NODES = 4_096
MAX_TEMPLATE_WORK_UNITS = 16_384
MAX_TEMPLATE_DEPTH = 64
MAX_TEMPLATE_OBLIGATIONS = 256
MAX_TEMPLATE_CANDIDATES = 128
MAX_TEMPLATE_BINDINGS = 256
MAX_TEMPLATE_EXTERNAL_REFERENCES = 32
MAX_TEMPLATE_ABSTRACT_VALUE_UNITS = 8_192
MAX_TEMPLATE_VALUE_DEPTH = 8

_ENVIRONMENT = ImmutableSandboxedEnvironment(
    extensions=("jinja2.ext.loopcontrols", "jinja2.ext.do")
)
_MAPPING_METHODS = frozenset({"get", "items", "keys", "values"})
_ORDINARY_STRING_METHODS = frozenset(
    {
        "capitalize",
        "casefold",
        "endswith",
        "find",
        "format",
        "index",
        "isalnum",
        "isalpha",
        "isascii",
        "isdecimal",
        "isdigit",
        "isidentifier",
        "islower",
        "isnumeric",
        "isprintable",
        "isspace",
        "istitle",
        "isupper",
        "join",
        "lower",
        "lstrip",
        "partition",
        "removeprefix",
        "removesuffix",
        "replace",
        "rfind",
        "rindex",
        "rpartition",
        "rsplit",
        "rstrip",
        "split",
        "splitlines",
        "startswith",
        "strip",
        "swapcase",
        "title",
        "upper",
        "zfill",
    }
)
_DYNAMIC_DISPATCH_FILTERS = frozenset(
    {"map", "select", "reject", "selectattr", "rejectattr"}
)
_REORDERING_OR_RESHAPING_FILTERS = frozenset(
    {"batch", "reverse", "slice", "sort", "unique"}
)
_MAPPING_ITERATION_FILTERS = frozenset(
    {"batch", "first", "last", "list", "reverse", "slice", "sort", "unique"}
)
_STATE_CONTEXT_ATTRIBUTES = frozenset(
    {"entity_id", "from_state", "to_state", "zone"}
)
_VALUE_RETURNING_STATE_HELPERS = frozenset(
    {"states", "state_attr", "state_translated", "state_attr_translated"}
)
_DYNAMIC_CONTEXT_SCALAR_ATTRIBUTES = frozenset(
    {"alias", "description", "event_type", "id", "platform"}
)
_WAIT_DYNAMIC_SCALAR_ATTRIBUTES = frozenset({"completed", "remaining"})
_NEUTRAL_CONTEXT_ATTRIBUTES = frozenset({"idx"})


@dataclass
class TemplateContextEvidence:
    """Bounded exact context supplied by the surrounding configuration."""

    trigger_entity_ids: tuple[str, ...] = ()
    trigger_from_state_entity_ids: tuple[str, ...] = ()
    trigger_to_state_entity_ids: tuple[str, ...] = ()
    trigger_zone_entity_ids: tuple[str, ...] = ()
    wait_trigger_entity_ids: tuple[str, ...] = ()
    wait_trigger_from_state_entity_ids: tuple[str, ...] = ()
    wait_trigger_to_state_entity_ids: tuple[str, ...] = ()
    wait_trigger_zone_entity_ids: tuple[str, ...] = ()
    this_entity_id: str | None = None
    variable_entity_ids: dict[str, tuple[str, ...]] = field(
        default_factory=dict
    )
    incomplete_variable_names: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()


@dataclass
class TemplateLedgerResult:
    obligations: tuple[DependencyObligation, ...]
    ast_node_count: int
    work_units: int
    coverage_failed: bool
    semantic_registry_sha256: str


@dataclass
class _Value:
    entity_ids: set[str] = field(default_factory=set)
    literal_strings: set[str] = field(default_factory=set)
    literal_numbers: set[float] = field(default_factory=set)
    possible_domains: set[str] = field(default_factory=set)
    domain_evidence_complete: bool = False
    callables: set[str] = field(default_factory=set)
    fields: dict[str, "_Value"] = field(default_factory=dict)
    items: list["_Value"] = field(default_factory=list)
    method_receivers: dict[str, "_Value"] = field(default_factory=dict)
    # Jinja's dot and bracket lookup orders differ for mappings, while a
    # Namespace stores real attributes that may legitimately be named get,
    # items, keys, or values.  Preserve the receiver kind so those names do
    # not silently change meaning during abstract evaluation.
    container_kinds: set[str] = field(default_factory=set)
    # Stable only within one analysis. Namespace identity lets mutations
    # survive aliases and executing child scopes without sharing mutable
    # branch state or exposing the identity in persisted evidence.
    namespace_ids: set[int] = field(default_factory=set)
    context_paths: set[str] = field(default_factory=set)
    ordinary: bool = False
    unknown: bool = False
    state_collection: bool = False
    state_object: bool = False
    state_attribute_container: bool = False
    projection_uncertain: bool = False
    # A runtime scalar whose value is unknown but whose production alone is
    # not an entity-selection obligation.  The taint survives reviewed scalar
    # transformations and becomes opaque only if later consumed by a state or
    # entity selector.
    dynamic_scalar: bool = False
    external: bool = False
    complete: bool = True
    limit_exceeded: bool = False

    def copy(self) -> "_Value":
        # Nested abstract values are treated as immutable. Shallow structural
        # copies prevent alias transport from recursively cloning the same
        # finite container while still isolating top-level mutations.
        return _Value(
            entity_ids=set(self.entity_ids),
            literal_strings=set(self.literal_strings),
            literal_numbers=set(self.literal_numbers),
            possible_domains=set(self.possible_domains),
            domain_evidence_complete=self.domain_evidence_complete,
            callables=set(self.callables),
            fields=dict(self.fields),
            items=list(self.items),
            method_receivers=dict(self.method_receivers),
            container_kinds=set(self.container_kinds),
            namespace_ids=set(self.namespace_ids),
            context_paths=set(self.context_paths),
            ordinary=self.ordinary,
            unknown=self.unknown,
            state_collection=self.state_collection,
            state_object=self.state_object,
            state_attribute_container=self.state_attribute_container,
            projection_uncertain=self.projection_uncertain,
            dynamic_scalar=self.dynamic_scalar,
            external=self.external,
            complete=self.complete,
            limit_exceeded=self.limit_exceeded,
        )


class _AnalysisLimit(RuntimeError):
    pass


def _ordinary_value(*, strings: Iterable[str] = ()) -> _Value:
    return _Value(
        literal_strings=set(strings), ordinary=True, complete=True
    )


def _unknown_value(*, external: bool = False) -> _Value:
    return _Value(
        unknown=True,
        external=external,
        complete=False,
    )


def _dynamic_scalar_value() -> _Value:
    return _Value(
        ordinary=True,
        unknown=True,
        dynamic_scalar=True,
        complete=False,
    )


def _callable_value(name: str) -> _Value:
    return _Value(callables={name}, complete=True)


def _values_equivalent(
    left: _Value,
    right: _Value,
    *,
    _depth: int = 0,
    _seen: set[tuple[int, int]] | None = None,
) -> bool:
    """Compare bounded abstract values without recursively copying graphs."""

    if left is right:
        return True
    if _depth > MAX_TEMPLATE_VALUE_DEPTH:
        return False
    if _seen is None:
        _seen = set()
    pair = (id(left), id(right))
    if pair in _seen:
        return True
    _seen.add(pair)
    scalar_equal = all(
        getattr(left, name) == getattr(right, name)
        for name in (
            "entity_ids",
            "literal_strings",
            "literal_numbers",
            "possible_domains",
            "domain_evidence_complete",
            "callables",
            "container_kinds",
            "namespace_ids",
            "context_paths",
            "ordinary",
            "unknown",
            "state_collection",
            "state_object",
            "state_attribute_container",
            "external",
            "complete",
            "limit_exceeded",
            "projection_uncertain",
            "dynamic_scalar",
        )
    )
    if not scalar_equal:
        return False
    if set(left.fields) != set(right.fields):
        return False
    if set(left.method_receivers) != set(right.method_receivers):
        return False
    if len(left.items) != len(right.items):
        return False
    return bool(
        all(
            _values_equivalent(
                left.fields[key],
                right.fields[key],
                _depth=_depth + 1,
                _seen=_seen,
            )
            for key in sorted(left.fields)
        )
        and all(
            _values_equivalent(
                left.method_receivers[key],
                right.method_receivers[key],
                _depth=_depth + 1,
                _seen=_seen,
            )
            for key in sorted(left.method_receivers)
        )
        and all(
            _values_equivalent(
                left_item,
                right_item,
                _depth=_depth + 1,
                _seen=_seen,
            )
            for left_item, right_item in zip(left.items, right.items)
        )
    )


def _merge_values(
    values: Iterable[_Value],
    *,
    _depth: int = 0,
    _budget: list[int] | None = None,
) -> _Value:
    if _budget is None:
        _budget = [MAX_TEMPLATE_ABSTRACT_VALUE_UNITS]
    if _depth > MAX_TEMPLATE_VALUE_DEPTH or _budget[0] <= 0:
        return _Value(
            unknown=True,
            complete=False,
            limit_exceeded=True,
        )
    candidates: list[_Value] = []
    merge_overflow = False
    for value in values:
        _budget[0] -= 1
        if _budget[0] < 0:
            merge_overflow = True
            break
        if len(candidates) < MAX_TEMPLATE_CANDIDATES:
            candidates.append(value)
        else:
            merge_overflow = True
    if not candidates:
        return _ordinary_value()
    result = _Value(
        ordinary=all(value.ordinary for value in candidates),
        unknown=any(value.unknown for value in candidates),
        state_collection=any(value.state_collection for value in candidates),
        state_object=any(value.state_object for value in candidates),
        state_attribute_container=any(
            value.state_attribute_container for value in candidates
        ),
        projection_uncertain=any(
            value.projection_uncertain for value in candidates
        ),
        dynamic_scalar=any(value.dynamic_scalar for value in candidates),
        external=any(value.external for value in candidates),
        complete=all(value.complete for value in candidates),
        domain_evidence_complete=all(
            value.domain_evidence_complete for value in candidates
        ),
        limit_exceeded=bool(
            merge_overflow
            or any(value.limit_exceeded for value in candidates)
        ),
    )
    field_groups: dict[str, list[_Value]] = {}
    receiver_groups: dict[str, list[_Value]] = {}
    for value in candidates:
        result.entity_ids.update(value.entity_ids)
        result.literal_strings.update(value.literal_strings)
        result.literal_numbers.update(value.literal_numbers)
        result.possible_domains.update(value.possible_domains)
        result.callables.update(value.callables)
        result.container_kinds.update(value.container_kinds)
        result.namespace_ids.update(value.namespace_ids)
        result.context_paths.update(value.context_paths)
        for item in value.items:
            _budget[0] -= 1
            if _budget[0] < 0:
                merge_overflow = True
                break
            if len(result.items) < MAX_TEMPLATE_CANDIDATES:
                result.items.append(item.copy())
            else:
                merge_overflow = True
        for key, item in value.fields.items():
            _budget[0] -= 1
            if _budget[0] < 0:
                merge_overflow = True
                break
            if key not in field_groups and len(field_groups) >= MAX_TEMPLATE_CANDIDATES:
                merge_overflow = True
                continue
            group = field_groups.setdefault(key, [])
            if len(group) < MAX_TEMPLATE_CANDIDATES:
                group.append(item)
            else:
                merge_overflow = True
        for key, item in value.method_receivers.items():
            _budget[0] -= 1
            if _budget[0] < 0:
                merge_overflow = True
                break
            if key not in receiver_groups and len(receiver_groups) >= MAX_TEMPLATE_CANDIDATES:
                merge_overflow = True
                continue
            group = receiver_groups.setdefault(key, [])
            if len(group) < MAX_TEMPLATE_CANDIDATES:
                group.append(item)
            else:
                merge_overflow = True
    result.fields = {
        key: _merge_values(
            group,
            _depth=_depth + 1,
            _budget=_budget,
        )
        for key, group in sorted(field_groups.items())
    }
    result.method_receivers = {
        key: _merge_values(
            group,
            _depth=_depth + 1,
            _budget=_budget,
        )
        for key, group in sorted(receiver_groups.items())
    }
    if any(
        value.limit_exceeded
        for value in (*result.fields.values(), *result.method_receivers.values())
    ):
        merge_overflow = True
    if (
        merge_overflow
        or
        len(result.entity_ids) > MAX_TEMPLATE_CANDIDATES
        or len(result.literal_strings) > MAX_TEMPLATE_CANDIDATES
        or len(result.literal_numbers) > MAX_TEMPLATE_CANDIDATES
        or len(result.possible_domains) > MAX_TEMPLATE_CANDIDATES
        or len(result.callables) > MAX_TEMPLATE_CANDIDATES
        or len(result.container_kinds) > MAX_TEMPLATE_CANDIDATES
        or len(result.namespace_ids) > MAX_TEMPLATE_CANDIDATES
        or len(result.items) > MAX_TEMPLATE_CANDIDATES
        or len(result.fields) > MAX_TEMPLATE_CANDIDATES
        or len(result.method_receivers) > MAX_TEMPLATE_CANDIDATES
        or len(result.context_paths) > MAX_TEMPLATE_CANDIDATES
    ):
        result.entity_ids = set(
            sorted(result.entity_ids)[:MAX_TEMPLATE_CANDIDATES]
        )
        result.literal_strings = set(
            sorted(result.literal_strings)[:MAX_TEMPLATE_CANDIDATES]
        )
        result.literal_numbers = set(
            sorted(result.literal_numbers)[:MAX_TEMPLATE_CANDIDATES]
        )
        result.possible_domains = set(
            sorted(result.possible_domains)[:MAX_TEMPLATE_CANDIDATES]
        )
        result.callables = set(
            sorted(result.callables)[:MAX_TEMPLATE_CANDIDATES]
        )
        result.container_kinds = set(
            sorted(result.container_kinds)[:MAX_TEMPLATE_CANDIDATES]
        )
        result.namespace_ids = set(
            sorted(result.namespace_ids)[:MAX_TEMPLATE_CANDIDATES]
        )
        result.items = result.items[:MAX_TEMPLATE_CANDIDATES]
        result.fields = dict(
            list(sorted(result.fields.items()))[:MAX_TEMPLATE_CANDIDATES]
        )
        result.method_receivers = dict(
            list(sorted(result.method_receivers.items()))[
                :MAX_TEMPLATE_CANDIDATES
            ]
        )
        result.context_paths = set(
            sorted(result.context_paths)[:MAX_TEMPLATE_CANDIDATES]
        )
        result.complete = False
        result.limit_exceeded = True
        result.unknown = True
    return result


class TemplateObligationAnalyzer:
    """Bounded abstract interpreter for one complete Jinja template."""

    def __init__(
        self,
        *,
        source_type: str,
        source_id: str,
        config_path: str,
        relation: str,
        source_entity_id: str | None,
        source_name: str | None,
        source_state: str | None,
        configuration_fingerprint: str,
        entity_id_validator: Callable[[str], bool],
        context: TemplateContextEvidence | None = None,
        entity_output_role: bool = False,
    ):
        self.source_type = source_type
        self.source_id = source_id
        self.config_path = config_path
        self.relation = relation
        self.source_entity_id = source_entity_id
        self.source_name = source_name
        self.source_state = source_state
        self.configuration_fingerprint = configuration_fingerprint
        self.valid_entity_id = entity_id_validator
        self.context = context or TemplateContextEvidence()
        self.entity_output_role = entity_output_role
        self.registry = semantic_registry_identity()
        self._raw_obligations: list[dict[str, Any]] = []
        self._work_units = 0
        self._ast_nodes = 0
        self._external_count = 0
        self._macros: dict[str, nodes.Macro] = {}
        self._active_macros: set[str] = set()
        self._visited_nodes: set[int] = set()
        self._abstract_value_units = 0
        self._abstract_value_sizes: dict[int, int] = {}
        self._namespace_counter = 0
        self._namespace_history: dict[int, _Value] = {}

    def analyze(self, source: str) -> TemplateLedgerResult:
        if len(source) > MAX_TEMPLATE_SOURCE_CHARS:
            self._emit(
                outcome="coverage_failure",
                kind="template_source",
                reason="template_source_limit_exceeded",
                category="external_opaque",
                node=None,
                limit=True,
            )
            return self._finalize()
        try:
            tree = _ENVIRONMENT.parse(source)
        except TemplateSyntaxError as exc:
            self._emit(
                outcome="bounded_semantic_opaque",
                kind="template_parse",
                reason="template_parse_error",
                category="external_opaque",
                node=None,
                context=(f"line:{max(0, int(exc.lineno or 0))}",),
                lock="conservative",
            )
            return self._finalize()
        except Exception:
            self._emit(
                outcome="coverage_failure",
                kind="template_parse",
                reason="template_parser_failure",
                category="external_opaque",
                node=None,
                limit=True,
            )
            return self._finalize()

        try:
            self._ast_nodes = 1 + sum(1 for _ in tree.find_all(nodes.Node))
            if self._ast_nodes > MAX_TEMPLATE_AST_NODES:
                raise _AnalysisLimit("template_ast_node_limit_exceeded")
            self._visited_nodes.add(id(tree))
            scope = self._initial_scope()
            self._analyze_statements(tree.body, scope, depth=0)
            self._audit_unvisited_nodes(tree)
        except (_AnalysisLimit, RecursionError) as exc:
            reason = (
                str(exc)
                if isinstance(exc, _AnalysisLimit) and str(exc)
                else "template_analysis_recursion_limit_exceeded"
            )
            self._emit(
                outcome="coverage_failure",
                kind="template_analysis",
                reason=reason,
                category="external_opaque",
                node=None,
                limit=True,
            )
        return self._finalize()

    def _initial_scope(self) -> dict[str, _Value]:
        scope: dict[str, _Value] = {}
        incomplete = set(self.context.incomplete_variable_names)
        for name, entity_ids in sorted(
            self.context.variable_entity_ids.items()
        ):
            scope[name] = _Value(
                entity_ids=set(entity_ids),
                literal_strings=set(entity_ids),
                unknown=name in incomplete,
                complete=name not in incomplete,
            )
        for name in sorted(incomplete.difference(scope)):
            scope[name] = _unknown_value()
        return scope

    def _tick(self, depth: int) -> None:
        self._work_units += 1
        if self._work_units > MAX_TEMPLATE_WORK_UNITS:
            raise _AnalysisLimit("template_work_limit_exceeded")
        if depth > MAX_TEMPLATE_DEPTH:
            raise _AnalysisLimit("template_depth_limit_exceeded")

    def _merge(
        self,
        values: Iterable[_Value],
        *,
        node: nodes.Node | None,
    ) -> _Value:
        """Merge finite alternatives and make any bound breach terminal."""

        result = _merge_values(values)
        if result.limit_exceeded:
            self._emit(
                outcome="coverage_failure",
                kind="template_abstract_value",
                reason="template_abstract_value_limit_exceeded",
                category="external_opaque",
                node=node,
                limit=True,
                lock="coverage_failure",
            )
        return result

    def _project_value(self, source: _Value, selected: _Value) -> _Value:
        """Project one member without losing parent uncertainty or limits."""

        result = selected.copy()
        uncertain = bool(
            source.unknown
            or not source.complete
            or source.projection_uncertain
        )
        if uncertain:
            result.entity_ids.update(source.entity_ids)
            result.literal_strings.update(source.literal_strings)
            result.literal_numbers.update(source.literal_numbers)
            result.possible_domains.update(source.possible_domains)
            result.callables.update(source.callables)
            result.context_paths.update(source.context_paths)
            result.state_collection = bool(
                result.state_collection or source.state_collection
            )
            result.state_object = bool(
                result.state_object or source.state_object
            )
            result.dynamic_scalar = bool(
                result.dynamic_scalar or source.dynamic_scalar
            )
            result.external = bool(result.external or source.external)
            result.unknown = True
            result.complete = False
            result.domain_evidence_complete = bool(
                result.domain_evidence_complete
                and source.domain_evidence_complete
            )
        if source.limit_exceeded:
            result.limit_exceeded = True
            result.unknown = True
            result.complete = False
        return result

    def _analyze_statements(
        self,
        statements: Iterable[nodes.Stmt],
        scope: dict[str, _Value],
        *,
        depth: int,
    ) -> None:
        for statement in statements:
            self._analyze_statement(statement, scope, depth=depth + 1)

    def _analyze_statement(
        self, node: nodes.Stmt, scope: dict[str, _Value], *, depth: int
    ) -> None:
        self._tick(depth)
        self._visited_nodes.add(id(node))
        if isinstance(node, nodes.Output):
            for child in node.nodes:
                value = self._eval(child, scope, depth=depth + 1)
                if value.state_collection or value.state_object:
                    self._consume_entity_value(
                        value,
                        node=child,
                        kind="rendered_state_value",
                        reason="state_value_rendered_or_iterated",
                    )
                elif value.context_paths and not value.dynamic_scalar:
                    self._consume_entity_value(
                        value,
                        node=child,
                        kind="rendered_context_value",
                        reason="state_bearing_context_rendered",
                    )
                if self.entity_output_role and not isinstance(
                    child, nodes.TemplateData
                ):
                    self._consume_entity_value(
                        value,
                        node=child,
                        kind="templated_entity_output",
                        reason="entity_bearing_configuration_value",
                    )
            return
        if isinstance(node, nodes.Assign):
            value = self._eval(node.node, scope, depth=depth + 1)
            self._bind(node.target, value, scope)
            return
        if isinstance(node, nodes.AssignBlock):
            self._analyze_statements(node.body, dict(scope), depth=depth + 1)
            filter_node = getattr(node, "filter", None)
            if isinstance(filter_node, nodes.Expr):
                self._eval(filter_node, scope, depth=depth + 1)
            self._bind(node.target, _unknown_value(), scope)
            return
        if isinstance(node, nodes.ExprStmt):
            self._eval(node.node, scope, depth=depth + 1)
            return
        if isinstance(node, nodes.For):
            iterable = self._eval(node.iter, scope, depth=depth + 1)
            if iterable.state_collection:
                self._consume_entity_value(
                    iterable,
                    node=node.iter,
                    kind="state_collection_iteration",
                    reason="state_collection_iterated",
                )
            child_scope = dict(scope)
            self._bind(node.target, self._iteration_value(iterable), child_scope)
            if node.test is not None:
                self._eval(node.test, child_scope, depth=depth + 1)
            self._analyze_statements(node.body, child_scope, depth=depth + 1)
            self._analyze_statements(node.else_, dict(scope), depth=depth + 1)
            return
        if isinstance(node, nodes.If):
            self._eval(node.test, scope, depth=depth + 1)
            body_scope = dict(scope)
            else_scope = dict(scope)
            self._analyze_statements(node.body, body_scope, depth=depth + 1)
            self._analyze_statements(node.elif_, else_scope, depth=depth + 1)
            self._analyze_statements(node.else_, else_scope, depth=depth + 1)
            self._merge_branch_scopes(scope, body_scope, else_scope)
            return
        if isinstance(node, nodes.Macro):
            self._macros[node.name] = node
            scope[node.name] = _callable_value(f"local_macro:{node.name}")
            # Macro bodies/defaults are dormant until invocation. Mark the
            # subtree as covered by that reviewed rule; invocation analyzes
            # the body and any selected defaults with the live call scope.
            self._mark_subtree(node)
            self._check_binding_limit(scope)
            return
        if isinstance(node, nodes.CallBlock):
            self._eval(node.call, scope, depth=depth + 1)
            self._analyze_statements(node.body, dict(scope), depth=depth + 1)
            return
        if isinstance(node, nodes.With):
            child_scope = dict(scope)
            for target, value_node in zip(node.targets, node.values):
                self._bind(
                    target,
                    self._eval(value_node, scope, depth=depth + 1),
                    child_scope,
                )
            self._analyze_statements(node.body, child_scope, depth=depth + 1)
            return
        if isinstance(node, (nodes.Import, nodes.FromImport)):
            self._external_template_boundary(node, scope)
            return
        if isinstance(node, (nodes.Include, nodes.Extends)):
            self._external_template_boundary(node, scope)
            return
        if isinstance(node, nodes.FilterBlock):
            self._emit(
                outcome="bounded_semantic_opaque",
                kind="filter_block",
                reason="filter_block_result_semantics_opaque",
                category="dynamic_filter_test_dispatch",
                node=node,
                lock="conservative",
            )
            self._analyze_statements(node.body, dict(scope), depth=depth + 1)
            return
        if isinstance(node, (nodes.Block, nodes.Scope, nodes.OverlayScope)):
            body = getattr(node, "body", ())
            context = getattr(node, "context", None)
            if isinstance(context, nodes.Expr):
                self._eval(context, scope, depth=depth + 1)
            self._analyze_statements(body, dict(scope), depth=depth + 1)
            return
        if isinstance(node, nodes.ScopedEvalContextModifier):
            for option in node.options:
                self._visited_nodes.add(id(option))
                self._eval(option.value, scope, depth=depth + 1)
            self._analyze_statements(node.body, dict(scope), depth=depth + 1)
            return
        if isinstance(node, nodes.EvalContextModifier):
            for option in node.options:
                self._visited_nodes.add(id(option))
                self._eval(option.value, scope, depth=depth + 1)
            return
        if isinstance(node, (nodes.Break, nodes.Continue)):
            return
        self._unknown_node(node, scope, depth=depth)

    def _eval(
        self, node: nodes.Node | None, scope: dict[str, _Value], *, depth: int
    ) -> _Value:
        self._tick(depth)
        if node is None:
            return _ordinary_value()
        self._visited_nodes.add(id(node))
        if isinstance(node, nodes.Const):
            if isinstance(node.value, str):
                value = _ordinary_value(strings=(node.value,))
                if self.valid_entity_id(node.value):
                    value.entity_ids.add(node.value)
                return value
            if isinstance(node.value, (int, float)) and not isinstance(
                node.value, bool
            ):
                return _Value(
                    literal_numbers={float(node.value)}, ordinary=True
                )
            return _ordinary_value()
        if isinstance(node, nodes.TemplateData):
            return _ordinary_value(strings=(str(node.data),))
        if isinstance(node, nodes.Name):
            if node.ctx in {"store", "param"}:
                return _ordinary_value()
            if node.name in scope:
                return scope[node.name].copy()
            category = semantic_category("globals", node.name)
            if category != "unknown":
                value = _callable_value(f"global:{node.name}")
                if node.name == "states":
                    value.state_collection = True
                return value
            if node.name == "trigger":
                entity_ids = set(self.context.trigger_entity_ids)
                return _Value(
                    entity_ids=entity_ids,
                    context_paths={"trigger"},
                    unknown=not bool(entity_ids),
                    complete=bool(entity_ids),
                )
            if node.name == "wait":
                return _Value(context_paths={"wait"}, complete=True)
            if node.name == "this":
                entity_ids = (
                    {self.context.this_entity_id}
                    if self.context.this_entity_id
                    else set()
                )
                return _Value(
                    entity_ids=entity_ids,
                    state_object=True,
                    context_paths={"this"},
                    unknown=not bool(entity_ids),
                    complete=bool(entity_ids),
                )
            return _unknown_value()
        if isinstance(node, (nodes.List, nodes.Tuple)):
            values = [
                self._eval(item, scope, depth=depth + 1)
                for item in node.items
            ]
            merged = self._merge(values, node=node)
            merged.items = [
                value.copy()
                for value in values[:MAX_TEMPLATE_CANDIDATES]
            ]
            # Receiver semantics describe the outer value, not the values it
            # transports. Preserve child provenance in items while keeping
            # attribute/item dispatch aware that this value is a sequence.
            merged.container_kinds = {"sequence"}
            if len(values) > MAX_TEMPLATE_CANDIDATES:
                merged.complete = False
                merged.unknown = True
                merged.limit_exceeded = True
                self._emit(
                    outcome="coverage_failure",
                    kind="template_value_container",
                    reason="template_value_container_limit_exceeded",
                    category="external_opaque",
                    node=node,
                    limit=True,
                    lock="coverage_failure",
                )
            self._account_value_graph(merged)
            return merged
        if isinstance(node, nodes.Dict):
            fields: dict[str, _Value] = {}
            unknown = False
            limit_exceeded = len(node.items) > MAX_TEMPLATE_CANDIDATES
            for index, pair in enumerate(node.items):
                self._visited_nodes.add(id(pair))
                key = self._eval(pair.key, scope, depth=depth + 1)
                value = self._eval(pair.value, scope, depth=depth + 1)
                if (
                    index < MAX_TEMPLATE_CANDIDATES
                    and len(key.literal_strings) == 1
                    and key.complete
                ):
                    fields[next(iter(key.literal_strings))] = value
                else:
                    unknown = True
            result = _Value(
                fields=fields,
                container_kinds={"mapping"},
                ordinary=all(
                    value.ordinary for value in fields.values()
                ),
                unknown=unknown or limit_exceeded,
                complete=not unknown and not limit_exceeded,
                limit_exceeded=limit_exceeded,
            )
            if limit_exceeded:
                self._emit(
                    outcome="coverage_failure",
                    kind="template_value_container",
                    reason="template_value_container_limit_exceeded",
                    category="external_opaque",
                    node=node,
                    limit=True,
                    lock="coverage_failure",
                )
            self._account_value_graph(result)
            return result
        if isinstance(node, nodes.Pair):
            result = self._merge(
                (
                    self._eval(node.key, scope, depth=depth + 1),
                    self._eval(node.value, scope, depth=depth + 1),
                ),
                node=node,
            )
            return result
        if isinstance(node, nodes.Keyword):
            return self._eval(node.value, scope, depth=depth + 1)
        if isinstance(node, nodes.CondExpr):
            self._eval(node.test, scope, depth=depth + 1)
            first = self._eval(node.expr1, scope, depth=depth + 1)
            second = self._eval(node.expr2, scope, depth=depth + 1)
            result = self._merge(
                (first, second),
                node=node,
            )
            result.projection_uncertain = not _values_equivalent(first, second)
            return result
        if isinstance(node, (nodes.And, nodes.Or)):
            left = self._eval(node.left, scope, depth=depth + 1)
            right = self._eval(node.right, scope, depth=depth + 1)
            result = self._merge(
                (left, right),
                node=node,
            )
            result.projection_uncertain = not _values_equivalent(left, right)
            return result
        if isinstance(node, nodes.Concat):
            values = [
                self._eval(item, scope, depth=depth + 1)
                for item in node.nodes
            ]
            if all(
                len(value.literal_strings) == 1 and value.complete
                for value in values
            ):
                text = "".join(
                    next(iter(value.literal_strings)) for value in values
                )
                return _ordinary_value(strings=(text,))
            if len(values) == 2:
                prefix, suffix = values
                if (
                    len(prefix.literal_strings) == 1
                    and prefix.complete
                    and not prefix.unknown
                    and suffix.unknown
                    and not suffix.entity_ids
                    and not suffix.literal_strings
                    and not suffix.possible_domains
                    and not suffix.callables
                    and not suffix.fields
                    and not suffix.items
                    and not suffix.context_paths
                    and not suffix.state_collection
                    and not suffix.state_object
                    and not suffix.external
                ):
                    prefix_text = next(iter(prefix.literal_strings))
                    domain = prefix_text[:-1] if prefix_text.endswith(".") else ""
                    if (
                        domain
                        and (domain[0].isalpha() or domain[0] == "_")
                        and all(
                            character.isalnum() or character == "_"
                            for character in domain
                        )
                    ):
                        return _Value(
                            possible_domains={domain},
                            domain_evidence_complete=True,
                            unknown=True,
                            complete=False,
                        )
            return self._merge(values, node=node)
        if isinstance(node, nodes.Getattr):
            base = self._eval(node.node, scope, depth=depth + 1)
            return self._get_attribute(
                base, node.attr, node=node, scope=scope, depth=depth
            )
        if isinstance(node, nodes.Getitem):
            base = self._eval(node.node, scope, depth=depth + 1)
            key = self._eval(node.arg, scope, depth=depth + 1)
            return self._get_item(
                base, key, node=node, scope=scope, depth=depth
            )
        if isinstance(node, nodes.Slice):
            result = self._merge(
                (
                    self._eval(value, scope, depth=depth + 1)
                    for value in (node.start, node.stop, node.step)
                    if value is not None
                ),
                node=node,
            )
            result.container_kinds = {"slice_selector"}
            return result
        if isinstance(node, nodes.Call):
            return self._call(node, scope, depth=depth)
        if isinstance(node, nodes.Filter):
            return self._filter(node, scope, depth=depth)
        if isinstance(node, nodes.Test):
            return self._test(node, scope, depth=depth)
        if isinstance(node, nodes.Compare):
            self._eval(node.expr, scope, depth=depth + 1)
            for operand in node.ops:
                self._visited_nodes.add(id(operand))
                value = self._eval(
                    operand.expr, scope, depth=depth + 1
                )
                if (
                    operand.op in {"in", "notin"}
                    and (value.state_collection or value.state_object)
                ):
                    self._consume_entity_value(
                        value,
                        node=operand.expr,
                        kind="membership_state_operand",
                        reason="membership_iterates_state_value",
                    )
            return _ordinary_value()
        if isinstance(node, nodes.Operand):
            return self._eval(node.expr, scope, depth=depth + 1)
        if isinstance(
            node,
            (
                nodes.Add,
                nodes.Sub,
                nodes.Mul,
                nodes.Div,
                nodes.FloorDiv,
                nodes.Mod,
                nodes.Pow,
            ),
        ):
            left = self._eval(node.left, scope, depth=depth + 1)
            right = self._eval(node.right, scope, depth=depth + 1)
            if isinstance(node, nodes.Add):
                if (
                    left.container_kinds == {"sequence"}
                    and right.container_kinds == {"sequence"}
                ):
                    items = [*left.items, *right.items]
                    sequence_overflow = len(items) > MAX_TEMPLATE_CANDIDATES
                    if sequence_overflow:
                        self._emit(
                            outcome="coverage_failure",
                            kind="sequence_addition",
                            reason="template_value_container_limit_exceeded",
                            category="external_opaque",
                            node=node,
                            limit=True,
                            lock="coverage_failure",
                        )
                        items = items[:MAX_TEMPLATE_CANDIDATES]
                    result = self._merge(items, node=node)
                    result.items = [item.copy() for item in items]
                    result.container_kinds = {"sequence"}
                    result.unknown = bool(
                        left.unknown
                        or right.unknown
                        or not left.complete
                        or not right.complete
                        or sequence_overflow
                    )
                    result.complete = not result.unknown
                    result.limit_exceeded = bool(
                        result.limit_exceeded or sequence_overflow
                    )
                    return result
                if (
                    len(left.literal_strings) == 1
                    and len(right.literal_strings) == 1
                    and left.complete
                    and right.complete
                    and not left.container_kinds
                    and not right.container_kinds
                    and not left.items
                    and not right.items
                    and not left.fields
                    and not right.fields
                ):
                    return _ordinary_value(
                        strings=(
                            next(iter(left.literal_strings))
                            + next(iter(right.literal_strings)),
                        )
                    )
                result = self._merge((left, right), node=node)
                result.dynamic_scalar = True
                result.unknown = True
                result.complete = False
                return result
            if (
                left.literal_numbers
                and right.literal_numbers
                and left.complete
                and right.complete
                and not left.unknown
                and not right.unknown
            ):
                return _ordinary_value()
            return _dynamic_scalar_value()
        if isinstance(node, nodes.Not):
            self._eval(node.node, scope, depth=depth + 1)
            return _ordinary_value()
        if isinstance(node, (nodes.Neg, nodes.Pos)):
            value = self._eval(node.node, scope, depth=depth + 1)
            if (
                value.literal_numbers
                and value.complete
                and not value.unknown
            ):
                return _ordinary_value()
            return _dynamic_scalar_value()
        if isinstance(
            node,
            (
                nodes.Template,
                nodes.MarkSafe,
                nodes.MarkSafeIfAutoescape,
            ),
        ):
            child = getattr(node, "expr", None) or getattr(node, "node", None)
            return self._eval(child, scope, depth=depth + 1)
        if isinstance(
            node,
            (
                nodes.EnvironmentAttribute,
                nodes.ExtensionAttribute,
                nodes.ImportedName,
                nodes.InternalName,
                nodes.ContextReference,
                nodes.DerivedContextReference,
                nodes.NSRef,
            ),
        ):
            self._emit(
                outcome="bounded_semantic_opaque",
                kind="jinja_runtime_value",
                reason=f"{type(node).__name__}_runtime_value_opaque",
                category="external_opaque",
                node=node,
                lock="conservative",
            )
            return _unknown_value(external=True)
        self._unknown_node(node, scope, depth=depth)
        return _unknown_value()

    def _call(
        self, node: nodes.Call, scope: dict[str, _Value], *, depth: int
    ) -> _Value:
        function = self._eval(node.node, scope, depth=depth + 1)
        arguments = [
            self._eval(item, scope, depth=depth + 1)
            for item in node.args
        ]
        keyword_values = {
            item.key: self._eval(item.value, scope, depth=depth + 1)
            for item in node.kwargs
        }
        self._visited_nodes.update(id(item) for item in node.kwargs)
        if node.dyn_args is not None or node.dyn_kwargs is not None:
            if node.dyn_args is not None:
                self._eval(node.dyn_args, scope, depth=depth + 1)
            if node.dyn_kwargs is not None:
                self._eval(node.dyn_kwargs, scope, depth=depth + 1)
            self._emit(
                outcome="bounded_semantic_opaque",
                kind="dynamic_call_arguments",
                reason="dynamic_call_argument_unpacking",
                category="unknown",
                node=node,
                lock="conservative",
            )

        results: list[_Value] = []
        for callable_name in sorted(function.callables):
            if callable_name.startswith("global:"):
                name = callable_name.split(":", 1)[1]
                results.append(
                    self._call_global(
                        name,
                        arguments,
                        keyword_values,
                        node=node,
                        scope=scope,
                        depth=depth,
                    )
                )
            elif callable_name.startswith("method:"):
                method = callable_name.split(":", 1)[1]
                receiver = function.method_receivers.get(method)
                results.append(
                    self._call_mapping_method(
                        method,
                        receiver or _unknown_value(),
                        arguments,
                        keyword_values,
                        node=node,
                    )
                )
            elif callable_name.startswith("local_macro:"):
                name = callable_name.split(":", 1)[1]
                results.append(
                    self._call_local_macro(
                        name,
                        arguments,
                        keyword_values,
                        scope=scope,
                        depth=depth,
                        node=node,
                    )
                )
            elif callable_name.startswith("ordinary_method:"):
                method = callable_name.split(":", 1)[1]
                receiver = function.method_receivers.get(method)
                for argument in (*arguments, *keyword_values.values()):
                    if argument.state_collection or argument.state_object:
                        self._consume_entity_value(
                            argument,
                            node=node,
                            kind=f"ordinary_method_{method}_argument",
                            reason=(
                                f"ordinary_method_{method}_consumes_state_value"
                            ),
                        )
                self._neutral(
                    node, f"ordinary_string_method_{method}_dependency_neutral"
                )
                # A reviewed scalar/string method is dependency-neutral at
                # this point, but its result may later be used as an entity
                # selector. Preserve value provenance until that consumption.
                results.append(_dynamic_scalar_value())
            else:
                self._emit(
                    outcome="bounded_semantic_opaque",
                    kind="callable",
                    reason="unreviewed_callable_binding",
                    category="unknown",
                    node=node,
                    lock="conservative",
                )
                results.append(_unknown_value())
        if not function.callables:
            self._emit(
                outcome="bounded_semantic_opaque",
                kind="callable",
                reason=(
                    "external_callable_binding"
                    if function.external
                    else "unknown_callable_binding"
                ),
                category="external_opaque" if function.external else "unknown",
                node=node,
                lock="conservative",
            )
            results.append(_unknown_value(external=function.external))
        if function.unknown and function.callables:
            self._emit(
                outcome="bounded_semantic_opaque",
                kind="callable",
                reason="mixed_callable_provenance",
                category="unknown",
                node=node,
                lock="conservative",
            )
            results.append(_unknown_value())
        return self._merge(results, node=node)

    def _call_global(
        self,
        name: str,
        arguments: list[_Value],
        keyword_values: dict[str, _Value],
        *,
        node: nodes.Node,
        scope: dict[str, _Value],
        depth: int,
    ) -> _Value:
        category = semantic_category("globals", name)
        if category == "state_entity_access":
            if name == "distance" and len(arguments) >= 2 and all(
                value.literal_numbers
                and not value.entity_ids
                and not value.literal_strings
                and not value.possible_domains
                and not value.callables
                and not value.fields
                and not value.items
                and not value.context_paths
                and not value.state_collection
                and not value.state_object
                and not value.unknown
                and value.complete
                for value in arguments[:2]
            ):
                self._neutral(node, "distance_coordinate_arguments")
                return _ordinary_value()
            targets = (
                arguments
                if name in {"expand", "closest", "distance"}
                else arguments[:1]
            )
            if not targets:
                self._opaque(node, f"{name}_target_missing")
            for target in targets:
                self._consume_entity_value(
                    target,
                    node=node,
                    kind=f"global_{name}",
                    reason=f"{name}_entity_access",
                )
            if name == "states" and not arguments:
                return _Value(
                    state_collection=True, unknown=True, complete=False
                )
            if name in {"expand", "closest"}:
                combined = self._merge(targets, node=node)
                exact = {
                    item
                    for item in (
                        *combined.entity_ids,
                        *combined.literal_strings,
                    )
                    if self.valid_entity_id(item)
                }
                membership_opaque = bool(
                    combined.unknown
                    or not combined.complete
                    or any(item.startswith("group.") for item in exact)
                    or not exact
                )
                if membership_opaque:
                    self._emit(
                        outcome="bounded_semantic_opaque",
                        kind=f"global_{name}_result",
                        reason=f"{name}_expanded_membership_opaque",
                        category="state_entity_access",
                        node=node,
                        exact=tuple(sorted(exact)),
                        lock="conservative",
                    )
                return _Value(
                    entity_ids=exact,
                    state_collection=name == "expand",
                    state_object=name == "closest",
                    unknown=membership_opaque,
                    complete=not membership_opaque,
                )
            if name in _VALUE_RETURNING_STATE_HELPERS:
                return _dynamic_scalar_value()
            return _ordinary_value()
        if category == "entity_set_producer":
            selectors = arguments[:1]
            literals = sorted(
                {
                    value
                    for item in selectors
                    for value in item.literal_strings
                }
            )
            self._emit(
                outcome="bounded_semantic_opaque",
                kind=f"global_{name}",
                reason=f"{name}_entity_set_membership_unavailable",
                category=category,
                node=node,
                selectors=tuple(literals),
                lock="conservative",
            )
            return _Value(
                state_collection=True,
                unknown=True,
                complete=False,
            )
        if category == "attribute_item_access":
            self._emit(
                outcome="bounded_semantic_opaque",
                kind=f"global_{name}",
                reason=f"{name}_registry_lookup_opaque",
                category=category,
                node=node,
                lock="conservative",
            )
            return _unknown_value()
        if category == "dependency_neutral":
            if name in {"dict", "namespace"}:
                # Positional constructor inputs are iterated to build fields;
                # keyword values are stored as-is.  Preserve keyword value
                # provenance in the resulting container without consuming it
                # as entity-selector evidence until a later operation actually
                # uses that value as a selector.
                for argument in arguments:
                    if argument.state_collection or argument.state_object:
                        self._consume_entity_value(
                            argument,
                            node=node,
                            kind=f"global_{name}_iterable_argument",
                            reason=f"{name}_iterates_state_value",
                        )
                if len(keyword_values) > MAX_TEMPLATE_CANDIDATES:
                    self._emit(
                        outcome="coverage_failure",
                        kind=f"global_{name}_constructor",
                        reason="template_value_container_limit_exceeded",
                        category="external_opaque",
                        node=node,
                        limit=True,
                        lock="coverage_failure",
                    )
            self._neutral(node, f"canonical_{name}_dependency_neutral")
            if name in {"dict", "namespace"}:
                return self._construct_field_container(
                    name,
                    arguments,
                    keyword_values,
                    node=node,
                )
            return _ordinary_value()
        if category == "provenance_preserving":
            self._neutral(node, f"canonical_{name}_operand_provenance_preserved")
            return self._merge(
                (*arguments, *keyword_values.values()),
                node=node,
            )
        self._opaque(node, f"unknown_global_{name}")
        return _unknown_value()

    def _construct_field_container(
        self,
        name: str,
        arguments: list[_Value],
        keyword_values: dict[str, _Value],
        *,
        node: nodes.Node,
    ) -> _Value:
        """Resolve bounded dict/namespace construction without execution."""

        fields: dict[str, _Value] = {}
        positional_complete = len(arguments) <= 1
        source = arguments[0] if len(arguments) == 1 else None
        if source is not None:
            if (
                source.container_kinds == {"mapping"}
                and source.complete
                and not source.unknown
            ):
                fields.update(
                    {key: value.copy() for key, value in source.fields.items()}
                )
            elif (
                source.container_kinds == {"sequence"}
                and not source.projection_uncertain
                and not source.limit_exceeded
                and bool(
                    source.items
                    or (source.complete and not source.unknown)
                )
            ):
                for pair in source.items:
                    if (
                        pair.container_kinds != {"sequence"}
                        or pair.projection_uncertain
                        or pair.limit_exceeded
                        or len(pair.items) != 2
                        or len(pair.items[0].literal_strings) != 1
                        or not pair.items[0].complete
                        or pair.items[0].unknown
                    ):
                        positional_complete = False
                        break
                    key = next(iter(pair.items[0].literal_strings))
                    fields[key] = pair.items[1].copy()
            else:
                positional_complete = False

        constructor_limit = bool(
            len(fields) + len(keyword_values) > MAX_TEMPLATE_CANDIDATES
            or any(value.limit_exceeded for value in arguments)
        )
        if not positional_complete:
            self._emit(
                outcome="bounded_semantic_opaque",
                kind=f"global_{name}_constructor",
                reason=f"{name}_positional_mapping_opaque",
                category="attribute_item_access",
                node=node,
                lock="conservative",
            )
        for key, value in list(keyword_values.items())[
            :MAX_TEMPLATE_CANDIDATES
        ]:
            fields[key] = value.copy()
        if constructor_limit:
            self._emit(
                outcome="coverage_failure",
                kind=f"global_{name}_constructor",
                reason="template_value_container_limit_exceeded",
                category="external_opaque",
                node=node,
                limit=True,
                lock="coverage_failure",
            )

        source_candidates = source or _ordinary_value()
        complete = bool(
            positional_complete
            and not constructor_limit
            and all(
                value.complete and not value.unknown
                for value in fields.values()
            )
        )
        result = _Value(
            entity_ids=set(source_candidates.entity_ids),
            literal_strings=set(source_candidates.literal_strings),
            possible_domains=set(source_candidates.possible_domains),
            domain_evidence_complete=(
                source_candidates.domain_evidence_complete
            ),
            callables=set(source_candidates.callables),
            fields=fields,
            container_kinds={
                "mapping" if name == "dict" else "namespace"
            },
            ordinary=all(value.ordinary for value in fields.values()),
            unknown=not complete,
            dynamic_scalar=source_candidates.dynamic_scalar,
            external=source_candidates.external,
            complete=complete,
            limit_exceeded=constructor_limit,
        )
        if name == "namespace":
            if self._namespace_counter >= MAX_TEMPLATE_BINDINGS:
                raise _AnalysisLimit("template_namespace_limit_exceeded")
            self._namespace_counter += 1
            result.namespace_ids = {self._namespace_counter}
            self._namespace_history[self._namespace_counter] = result.copy()
        return result

    def _resolve_namespace_history(
        self,
        value: _Value,
        *,
        node: nodes.Node | None,
    ) -> _Value:
        histories = [
            self._namespace_history[identity]
            for identity in sorted(value.namespace_ids)
            if identity in self._namespace_history
        ]
        if not histories:
            return value.copy()
        candidates = [value, *histories]
        result = self._merge(candidates, node=node)
        if not all(
            _values_equivalent(candidates[0], candidate)
            for candidate in candidates[1:]
        ):
            result.projection_uncertain = True
        return result

    def _record_namespace_mutation(
        self,
        value: _Value,
        *,
        node: nodes.Node,
    ) -> None:
        for identity in sorted(value.namespace_ids):
            previous = self._namespace_history.get(identity)
            if previous is None:
                self._namespace_history[identity] = value.copy()
                continue
            merged = self._merge((previous, value), node=node)
            if not _values_equivalent(previous, value):
                merged.projection_uncertain = True
            self._namespace_history[identity] = merged

    def _call_mapping_method(
        self,
        method: str,
        receiver: _Value,
        arguments: list[_Value],
        keyword_values: dict[str, _Value],
        *,
        node: nodes.Node,
    ) -> _Value:
        if method == "get":
            key = arguments[0] if arguments else _unknown_value()
            default = (
                arguments[1]
                if len(arguments) > 1
                else keyword_values.get("default", _ordinary_value())
            )
            selected = [
                receiver.fields[value]
                for value in sorted(key.literal_strings)
                if value in receiver.fields
            ]
            if key.complete and len(key.literal_strings) == 1:
                return self._project_value(
                    receiver,
                    self._merge(selected or [default], node=node),
                )
            return self._project_value(
                receiver,
                self._merge(
                    [*receiver.fields.values(), default, _unknown_value()],
                    node=node,
                ),
            )
        if method == "keys":
            return self._project_value(receiver, _Value(
                literal_strings=set(receiver.fields),
                items=[
                    _ordinary_value(strings=(key,))
                    for key in sorted(receiver.fields)
                ],
                container_kinds={"sequence"},
                ordinary=True,
                unknown=receiver.unknown,
                complete=receiver.complete,
                limit_exceeded=receiver.limit_exceeded,
            ))
        if method == "values":
            return self._project_value(receiver, _Value(
                items=[value.copy() for value in receiver.fields.values()],
                container_kinds={"sequence"},
                ordinary=all(value.ordinary for value in receiver.fields.values()),
                unknown=receiver.unknown,
                complete=receiver.complete,
                limit_exceeded=receiver.limit_exceeded,
            ))
        if method == "items":
            pairs = [
                _Value(
                    items=[_ordinary_value(strings=(key,)), value.copy()],
                    container_kinds={"sequence"},
                    ordinary=value.ordinary,
                    complete=value.complete,
                )
                for key, value in sorted(receiver.fields.items())
            ]
            return self._project_value(receiver, _Value(
                items=pairs,
                container_kinds={"sequence"},
                ordinary=all(value.ordinary for value in pairs),
                unknown=receiver.unknown,
                complete=receiver.complete,
                limit_exceeded=receiver.limit_exceeded,
            ))
        self._opaque(node, "unknown_mapping_method")
        return _unknown_value()

    def _call_local_macro(
        self,
        name: str,
        arguments: list[_Value],
        keyword_values: dict[str, _Value],
        *,
        scope: dict[str, _Value],
        depth: int,
        node: nodes.Node,
    ) -> _Value:
        macro = self._macros.get(name)
        if macro is None or name in self._active_macros:
            self._opaque(node, "local_macro_body_unavailable_or_recursive")
            return _unknown_value()
        self._active_macros.add(name)
        try:
            child_scope = dict(scope)
            default_offset = len(macro.args) - len(macro.defaults)
            for index, target in enumerate(macro.args):
                if index < len(arguments):
                    value = arguments[index]
                elif target.name in keyword_values:
                    value = keyword_values[target.name]
                elif index >= default_offset:
                    value = self._eval(
                        macro.defaults[index - default_offset],
                        scope,
                        depth=depth + 1,
                    )
                else:
                    value = _unknown_value()
                self._bind(target, value, child_scope)
            self._analyze_statements(macro.body, child_scope, depth=depth + 1)
        finally:
            self._active_macros.discard(name)
        return _unknown_value()

    def _filter(
        self, node: nodes.Filter, scope: dict[str, _Value], *, depth: int
    ) -> _Value:
        operand = self._eval(node.node, scope, depth=depth + 1)
        arguments = [
            self._eval(item, scope, depth=depth + 1)
            for item in node.args
        ]
        keywords = {
            item.key: self._eval(item.value, scope, depth=depth + 1)
            for item in node.kwargs
        }
        self._visited_nodes.update(id(item) for item in node.kwargs)
        if node.dyn_args is not None:
            self._eval(node.dyn_args, scope, depth=depth + 1)
            self._emit(
                outcome="bounded_semantic_opaque",
                kind=f"filter_{node.name}_dynamic_arguments",
                reason="dynamic_filter_argument_unpacking",
                category="dynamic_filter_test_dispatch",
                node=node,
                lock="conservative",
            )
        if node.dyn_kwargs is not None:
            self._eval(node.dyn_kwargs, scope, depth=depth + 1)
            self._emit(
                outcome="bounded_semantic_opaque",
                kind=f"filter_{node.name}_dynamic_keywords",
                reason="dynamic_filter_keyword_unpacking",
                category="dynamic_filter_test_dispatch",
                node=node,
                lock="conservative",
            )
        if operand.state_collection or operand.state_object:
            self._consume_entity_value(
                operand,
                node=node.node,
                kind="filter_state_operand",
                reason="state_value_consumed_by_filter",
            )
        category = semantic_category("filters", node.name)
        if category == "state_entity_access":
            self._consume_entity_value(
                operand,
                node=node,
                kind=f"filter_{node.name}",
                reason=f"{node.name}_filter_entity_access",
            )
            if node.name in _VALUE_RETURNING_STATE_HELPERS:
                return _dynamic_scalar_value()
            return _ordinary_value()
        if node.name in _DYNAMIC_DISPATCH_FILTERS:
            return self._dynamic_filter_dispatch(
                node.name, operand, arguments, keywords, node=node
            )
        if node.name == "attr":
            if arguments and len(arguments[0].literal_strings) == 1:
                return self._get_attribute(
                    operand,
                    next(iter(arguments[0].literal_strings)),
                    node=node,
                    scope=scope,
                    depth=depth,
                )
            self._opaque(node, "dynamic_attr_filter")
            return _unknown_value()
        if node.name == "as_function":
            self._emit(
                outcome="bounded_semantic_opaque",
                kind="filter_as_function",
                reason=(
                    "external_macro_as_function"
                    if operand.external
                    else "macro_return_callable_opaque"
                ),
                category="provenance_preserving",
                node=node,
                lock="conservative",
            )
            return _unknown_value(external=operand.external)
        if node.name == "format":
            self._neutral(node, "format_filter_result_dependency_neutral")
            # Formatting can synthesize a valid entity ID from individually
            # non-entity operands. Keep the runtime scalar tainted so a later
            # entity-selector use becomes explicit semantic opacity.
            return _dynamic_scalar_value()
        if category == "provenance_preserving":
            if (
                node.name in _MAPPING_ITERATION_FILTERS
                and "mapping" in operand.container_kinds
            ):
                mapping_items = self._mapping_iteration_value(
                    operand,
                    node=node,
                )
                if node.name == "first" and mapping_items.items:
                    return self._project_value(
                        mapping_items,
                        mapping_items.items[0],
                    )
                if node.name == "last" and mapping_items.items:
                    return self._project_value(
                        mapping_items,
                        mapping_items.items[-1],
                    )
                if node.name == "list":
                    return mapping_items
                mapping_items.projection_uncertain = True
                mapping_items.unknown = True
                mapping_items.complete = False
                return mapping_items
            if node.name == "first" and operand.items:
                return self._project_value(operand, operand.items[0])
            if node.name == "last" and operand.items:
                return self._project_value(operand, operand.items[-1])
            result = self._merge(
                (operand, *arguments, *keywords.values()),
                node=node,
            )
            if node.name in _REORDERING_OR_RESHAPING_FILTERS:
                # These filters can change which source element a later
                # first/last/subscript observes.  Until their exact structure
                # is modeled, retain the full candidate union through every
                # later narrowing operation.
                result.projection_uncertain = True
                result.unknown = True
                result.complete = False
            return result
        if category == "dependency_neutral":
            for argument in (*arguments, *keywords.values()):
                if argument.state_collection or argument.state_object:
                    self._consume_entity_value(
                        argument,
                        node=node,
                        kind=f"filter_{node.name}_argument",
                        reason=f"{node.name}_filter_consumes_state_value",
                    )
            self._neutral(node, f"filter_{node.name}_dependency_neutral")
            return _dynamic_scalar_value()
        if category == "attribute_item_access":
            self._opaque(node, f"filter_{node.name}_attribute_access_opaque")
            return _unknown_value()
        self._opaque(node, f"unknown_filter_{node.name}")
        return _unknown_value()

    def _mapping_iteration_value(
        self,
        value: _Value,
        *,
        node: nodes.Node,
    ) -> _Value:
        """Project Jinja mapping iteration as its bounded key sequence."""

        keys = list(value.fields)
        if len(keys) > MAX_TEMPLATE_CANDIDATES:
            self._emit(
                outcome="coverage_failure",
                kind="mapping_iteration",
                reason="template_value_container_limit_exceeded",
                category="external_opaque",
                node=node,
                limit=True,
                lock="coverage_failure",
            )
            keys = keys[:MAX_TEMPLATE_CANDIDATES]
        items = [_ordinary_value(strings=(key,)) for key in keys]
        result = _Value(
            literal_strings=set(keys),
            entity_ids={key for key in keys if self.valid_entity_id(key)},
            items=items,
            container_kinds={"sequence"},
            ordinary=True,
            unknown=bool(
                value.unknown
                or not value.complete
                or value.projection_uncertain
                or value.container_kinds != {"mapping"}
            ),
            complete=bool(
                value.complete
                and not value.unknown
                and not value.projection_uncertain
                and value.container_kinds == {"mapping"}
            ),
            projection_uncertain=value.projection_uncertain,
            limit_exceeded=value.limit_exceeded,
        )
        return self._project_value(value, result)

    def _dynamic_filter_dispatch(
        self,
        name: str,
        operand: _Value,
        arguments: list[_Value],
        keywords: dict[str, _Value],
        *,
        node: nodes.Node,
    ) -> _Value:
        def selection_result() -> _Value:
            result = operand.copy()
            result.projection_uncertain = True
            result.unknown = True
            result.complete = False
            return result

        if name in {"selectattr", "rejectattr"}:
            if (
                not arguments
                or len(arguments[0].literal_strings) != 1
                or not arguments[0].complete
            ):
                self._opaque_from_value(
                    node,
                    operand,
                    f"{name}_dynamic_attribute_dispatch",
                    kind=f"filter_{name}",
                )
                return operand.copy()
            attribute = next(iter(arguments[0].literal_strings))
            test_name: str | None = None
            if len(arguments) > 1:
                if (
                    len(arguments[1].literal_strings) != 1
                    or not arguments[1].complete
                ):
                    self._opaque_from_value(
                        node,
                        operand,
                        f"{name}_dynamic_test_dispatch",
                        kind=f"filter_{name}",
                    )
                    return operand.copy()
                test_name = next(iter(arguments[1].literal_strings))
            category = (
                "dependency_neutral"
                if test_name is None
                else semantic_category("tests", test_name)
            )
            projected_attribute = self._project_collection_attribute(
                operand,
                attribute,
                default=None,
                node=node,
            )
            if category == "dependency_neutral":
                self._neutral(
                    node,
                    f"{name}_{attribute}_{test_name or 'truthy'}_neutral",
                )
                return selection_result()
            if category == "state_entity_access":
                self._consume_entity_value(
                    projected_attribute,
                    node=node,
                    kind=f"filter_{name}_{test_name}",
                    reason=f"{name}_dispatches_{test_name}",
                )
                return selection_result()
            self._opaque_from_value(
                node,
                projected_attribute,
                f"{name}_attribute_test_dispatch",
                kind=f"filter_{name}",
            )
            return selection_result()
        if name == "map" and "attribute" in keywords:
            attribute = keywords["attribute"]
            if len(attribute.literal_strings) == 1 and attribute.complete:
                attribute_name = next(iter(attribute.literal_strings))
                projected = self._project_collection_attribute(
                    operand,
                    attribute_name,
                    default=keywords.get("default"),
                    node=node,
                )
                self._neutral(
                    node,
                    f"map_{attribute_name}_provenance_preserved",
                )
                return projected
            self._opaque(
                node,
                "map_attribute_result_provenance_opaque",
            )
            return _unknown_value()
        if not arguments or len(arguments[0].literal_strings) != 1:
            if name in {"select", "reject"}:
                self._opaque_from_value(
                    node,
                    operand,
                    f"{name}_dynamic_dispatch_name",
                    kind=f"filter_{name}",
                )
                return selection_result()
            self._opaque(node, f"{name}_dynamic_dispatch_name")
            return _unknown_value()
        dispatched = next(iter(arguments[0].literal_strings))
        surface = "filters" if name == "map" else "tests"
        category = semantic_category(surface, dispatched)
        if category == "state_entity_access":
            self._consume_entity_value(
                operand,
                node=node,
                kind=f"filter_{name}_{dispatched}",
                reason=f"{name}_dispatches_{dispatched}",
            )
            return selection_result() if name != "map" else _unknown_value()
        if category in {"dependency_neutral", "provenance_preserving"}:
            self._neutral(node, f"{name}_{dispatched}_dependency_neutral")
            return selection_result() if name != "map" else _unknown_value()
        if name in {"select", "reject"}:
            self._opaque_from_value(
                node,
                operand,
                f"{name}_unknown_dispatch_{dispatched}",
                kind=f"filter_{name}",
            )
            return selection_result()
        self._opaque(node, f"{name}_unknown_dispatch_{dispatched}")
        return _unknown_value()

    def _project_collection_attribute(
        self,
        operand: _Value,
        attribute: str,
        *,
        default: _Value | None,
        node: nodes.Node,
    ) -> _Value:
        """Project a finite collection member without losing provenance.

        Jinja's ``map(attribute=...)`` and the ``*attr`` filters access each
        element, not the merged collection root.  Projecting the root can lose
        a helper stored in a mapping member or incorrectly substitute an
        unrelated scalar candidate.  Missing members use the reviewed map
        default when present; otherwise they are dependency-neutral Undefined
        values.  Unbounded elements remain unknown so a later state lookup
        becomes an explicit opaque obligation.
        """

        if "mapping" in operand.container_kinds:
            operand = self._mapping_iteration_value(operand, node=node)

        raw_parts = attribute.split(".")
        if (
            not raw_parts
            or any(not part for part in raw_parts)
            or len(raw_parts) > MAX_TEMPLATE_VALUE_DEPTH
        ):
            self._emit(
                outcome="coverage_failure",
                kind="filter_attribute_path",
                reason="template_attribute_path_limit_exceeded",
                category="attribute_item_access",
                node=node,
                limit=True,
                lock="coverage_failure",
            )
            value = _unknown_value()
            value.limit_exceeded = True
            return value
        parts: list[str | int] = [
            int(part) if part.isdigit() else part for part in raw_parts
        ]

        def missing_or_uncertain(value: _Value) -> _Value:
            if value.complete and not value.unknown:
                return (
                    default.copy()
                    if default is not None
                    else _ordinary_value()
                )
            selected = self._merge(
                (
                    value,
                    default
                    if default is not None
                    else _unknown_value(),
                ),
                node=node,
            )
            selected.unknown = True
            selected.complete = False
            return selected

        def project_part(value: _Value, part: str | int) -> _Value:
            if value.namespace_ids:
                value = self._resolve_namespace_history(value, node=node)
            if isinstance(part, int):
                if value.items and -len(value.items) <= part < len(
                    value.items
                ):
                    return self._project_value(value, value.items[part])
                return missing_or_uncertain(value)
            if value.fields and part in value.fields:
                return self._project_value(value, value.fields[part])
            if (
                value.fields
                and "mapping" in value.container_kinds
                and part in _MAPPING_METHODS
            ):
                method = _callable_value(f"method:{part}")
                method.method_receivers[part] = value.copy()
                return self._project_value(value, method)
            if value.state_object and part == "entity_id":
                return _Value(
                    entity_ids=set(value.entity_ids),
                    literal_strings=set(value.entity_ids),
                    possible_domains=set(value.possible_domains),
                    domain_evidence_complete=(
                        value.domain_evidence_complete
                    ),
                    unknown=value.unknown,
                    complete=value.complete,
                    limit_exceeded=value.limit_exceeded,
                )
            if (
                value.state_collection
                and part == "entity_id"
                and value.possible_domains
                and value.domain_evidence_complete
            ):
                return _Value(
                    entity_ids=set(value.entity_ids),
                    literal_strings=set(value.entity_ids),
                    possible_domains=set(value.possible_domains),
                    domain_evidence_complete=True,
                    unknown=value.unknown,
                    complete=value.complete,
                    limit_exceeded=value.limit_exceeded,
                )
            if value.state_object or value.state_collection:
                # State attributes other than entity_id are runtime values.
                # They may themselves contain an entity ID later consumed by
                # a reviewed state helper; a map default applies only to an
                # actual Undefined value and cannot discharge that ambiguity.
                selected = self._merge(
                    (
                        value,
                        default
                        if default is not None
                        else _unknown_value(),
                    ),
                    node=node,
                )
                selected.unknown = True
                selected.complete = False
                return selected
            if value.context_paths:
                selected = missing_or_uncertain(value)
                selected.context_paths.update(value.context_paths)
                selected.unknown = True
                selected.complete = False
                return selected
            if (
                value.ordinary
                and not value.unknown
                and part in _ORDINARY_STRING_METHODS
            ):
                method = _callable_value(f"ordinary_method:{part}")
                method.method_receivers[part] = value.copy()
                return method
            return missing_or_uncertain(value)

        def project_path(value: _Value) -> _Value:
            selected = value.copy()
            for part in parts:
                selected = project_part(selected, part)
            return selected

        projected: list[_Value] = []
        if operand.items:
            for item in operand.items[:MAX_TEMPLATE_CANDIDATES]:
                projected.append(project_path(item))
            result = self._merge(projected, node=node)
            result.items = [item.copy() for item in projected]
            if len(operand.items) > MAX_TEMPLATE_CANDIDATES:
                result.limit_exceeded = True
                result.unknown = True
                result.complete = False
            return self._project_value(operand, result)

        return project_path(operand)

    def _test(
        self, node: nodes.Test, scope: dict[str, _Value], *, depth: int
    ) -> _Value:
        operand = self._eval(node.node, scope, depth=depth + 1)
        for item in node.args:
            self._eval(item, scope, depth=depth + 1)
        for item in node.kwargs:
            self._visited_nodes.add(id(item))
            self._eval(item.value, scope, depth=depth + 1)
        if node.dyn_args is not None:
            self._eval(node.dyn_args, scope, depth=depth + 1)
            self._emit(
                outcome="bounded_semantic_opaque",
                kind=f"test_{node.name}_dynamic_arguments",
                reason="dynamic_test_argument_unpacking",
                category="dynamic_filter_test_dispatch",
                node=node,
                lock="conservative",
            )
        if node.dyn_kwargs is not None:
            self._eval(node.dyn_kwargs, scope, depth=depth + 1)
            self._emit(
                outcome="bounded_semantic_opaque",
                kind=f"test_{node.name}_dynamic_keywords",
                reason="dynamic_test_keyword_unpacking",
                category="dynamic_filter_test_dispatch",
                node=node,
                lock="conservative",
            )
        category = semantic_category("tests", node.name)
        if category == "state_entity_access":
            self._consume_entity_value(
                operand,
                node=node,
                kind=f"test_{node.name}",
                reason=f"{node.name}_test_entity_access",
            )
        elif category == "dependency_neutral":
            self._neutral(node, f"test_{node.name}_dependency_neutral")
        else:
            self._opaque(node, f"unknown_test_{node.name}")
        return _ordinary_value()

    def _get_attribute(
        self,
        base: _Value,
        attribute: str,
        *,
        node: nodes.Node,
        scope: dict[str, _Value],
        depth: int,
    ) -> _Value:
        if base.namespace_ids:
            base = self._resolve_namespace_history(base, node=node)
        if base.state_object:
            self._consume_entity_value(
                base,
                node=node,
                kind="state_object_attribute",
                reason=f"state_object_{attribute}_access",
            )
            if attribute == "attributes":
                return _Value(
                    state_attribute_container=True,
                    ordinary=True,
                    complete=base.complete,
                )
            if attribute == "entity_id":
                return _Value(
                    entity_ids=set(base.entity_ids),
                    literal_strings=set(base.entity_ids),
                    ordinary=True,
                    unknown=base.unknown,
                    complete=base.complete,
                    limit_exceeded=base.limit_exceeded,
                )
            return _dynamic_scalar_value()
        if base.state_attribute_container:
            if attribute in _MAPPING_METHODS:
                value = _callable_value(f"ordinary_method:{attribute}")
                value.method_receivers[attribute] = base.copy()
                return value
            return _dynamic_scalar_value()
        if base.context_paths:
            return self._context_attribute(base, attribute, node=node)
        if base.state_collection:
            if base.possible_domains and base.domain_evidence_complete:
                candidates = {
                    f"{domain}.{attribute}"
                    for domain in base.possible_domains
                    if self.valid_entity_id(f"{domain}.{attribute}")
                }
                value = _Value(
                    entity_ids=candidates,
                    state_object=True,
                    unknown=bool(
                        base.unknown
                        or not base.complete
                        or base.projection_uncertain
                        or not candidates
                    ),
                    complete=bool(
                        base.complete
                        and not base.unknown
                        and not base.projection_uncertain
                        and candidates
                    ),
                    limit_exceeded=base.limit_exceeded,
                )
                self._consume_entity_value(
                    value,
                    node=node,
                    kind="states_domain_object_access",
                    reason="states_domain_object_entity_access",
                )
                return value
            if (
                base.complete
                and not base.unknown
                and not base.projection_uncertain
                and not base.possible_domains
                and attribute
                and (attribute[0].isalpha() or attribute[0] == "_")
                and all(character.isalnum() or character == "_" for character in attribute)
            ):
                value = _Value(
                    possible_domains={attribute},
                    domain_evidence_complete=True,
                    state_collection=True,
                    complete=True,
                )
                return value
            candidates = {
                f"{domain}.{attribute}"
                for domain in base.possible_domains
                if self.valid_entity_id(f"{domain}.{attribute}")
            }
            value = _Value(
                entity_ids=candidates,
                possible_domains=set(base.possible_domains),
                domain_evidence_complete=False,
                state_object=True,
                unknown=True,
                complete=False,
                limit_exceeded=base.limit_exceeded,
            )
            self._consume_entity_value(
                value,
                node=node,
                kind="state_collection_attribute",
                reason="state_collection_attribute_entity_access",
            )
            return value
        if base.container_kinds.intersection({"mapping", "namespace"}):
            alternatives: list[_Value] = []
            mapping_possible = "mapping" in base.container_kinds
            namespace_possible = "namespace" in base.container_kinds
            if mapping_possible and attribute in _MAPPING_METHODS:
                method = _callable_value(f"method:{attribute}")
                method.method_receivers[attribute] = base.copy()
                alternatives.append(method)
            # Mapping dot lookup is attribute-first, so a method name shadows
            # an item. Namespace fields are real attributes and take their
            # stored value even when their name collides with a dict method.
            if attribute in base.fields and (
                attribute not in _MAPPING_METHODS
                or namespace_possible
                or not mapping_possible
            ):
                alternatives.append(base.fields[attribute])
            if alternatives:
                selected = self._merge(alternatives, node=node)
                if len(alternatives) > 1:
                    selected.projection_uncertain = True
                return self._project_value(base, selected)
            if base.container_kinds == {"namespace"} and base.complete:
                return _ordinary_value()
        if base.ordinary and (
            not base.unknown or base.dynamic_scalar
        ):
            if attribute in _ORDINARY_STRING_METHODS:
                value = _callable_value(f"ordinary_method:{attribute}")
                value.method_receivers[attribute] = base.copy()
                return value
        self._opaque(node, "unknown_attribute_receiver")
        return _unknown_value(external=base.external)

    def _get_item(
        self,
        base: _Value,
        key: _Value,
        *,
        node: nodes.Node,
        scope: dict[str, _Value],
        depth: int,
    ) -> _Value:
        if base.namespace_ids:
            base = self._resolve_namespace_history(base, node=node)
        if "slice_selector" in key.container_kinds:
            if base.items:
                result = self._merge(base.items, node=node)
                result.items = [item.copy() for item in base.items]
                result.container_kinds = {"sequence"}
                result.projection_uncertain = True
                result.unknown = True
                result.complete = False
                return self._project_value(base, result)
            if base.dynamic_scalar or (
                base.ordinary and not base.unknown
            ):
                return _dynamic_scalar_value()
            self._opaque(node, "slice_receiver_semantics_opaque")
            return _unknown_value(external=base.external)
        if base.state_collection:
            exact: set[str] = set()
            domains: set[str] = set()
            for candidate in sorted(key.literal_strings):
                if self.valid_entity_id(candidate):
                    exact.add(candidate)
                elif (
                    candidate
                    and (candidate[0].isalpha() or candidate[0] == "_")
                    and all(
                        character.isalnum() or character == "_"
                        for character in candidate
                    )
                ):
                    if base.possible_domains:
                        exact.update(
                            f"{domain}.{candidate}"
                            for domain in base.possible_domains
                            if self.valid_entity_id(
                                f"{domain}.{candidate}"
                            )
                        )
                    else:
                        domains.add(candidate)
            if exact:
                value = _Value(
                    entity_ids=exact,
                    state_object=True,
                    unknown=bool(
                        base.unknown
                        or not base.complete
                        or base.projection_uncertain
                        or key.unknown
                        or not key.complete
                    ),
                    complete=bool(
                        base.complete
                        and not base.unknown
                        and not base.projection_uncertain
                        and key.complete
                        and not key.unknown
                    ),
                    limit_exceeded=bool(
                        base.limit_exceeded or key.limit_exceeded
                    ),
                )
                self._consume_entity_value(
                    value,
                    node=node,
                    kind="states_item_access",
                    reason="states_item_entity_access",
                )
                return value
            if (
                domains
                and base.complete
                and not base.unknown
                and not base.projection_uncertain
                and key.complete
                and not key.unknown
            ):
                value = _Value(
                    possible_domains=domains,
                    domain_evidence_complete=True,
                    state_collection=True,
                    complete=True,
                )
                return value
            unresolved = _Value(
                entity_ids=set(key.entity_ids),
                literal_strings=set(key.literal_strings),
                possible_domains=set(
                    base.possible_domains or key.possible_domains
                ),
                domain_evidence_complete=bool(
                    (
                        base.possible_domains
                        and base.domain_evidence_complete
                    )
                    or (
                        not base.possible_domains
                        and key.possible_domains
                        and key.domain_evidence_complete
                    )
                ),
                state_object=True,
                unknown=True,
                complete=False,
                limit_exceeded=bool(
                    base.limit_exceeded or key.limit_exceeded
                ),
            )
            self._consume_entity_value(
                unresolved,
                node=node,
                kind="states_item_access",
                reason="states_item_entity_access",
            )
            return unresolved
        if base.state_object:
            self._consume_entity_value(
                base,
                node=node,
                kind="state_object_item",
                reason="state_object_item_access",
            )
            return _dynamic_scalar_value()
        if base.state_attribute_container:
            return _dynamic_scalar_value()
        if base.items and len(key.literal_numbers) == 1 and key.complete:
            index = int(next(iter(key.literal_numbers)))
            if -len(base.items) <= index < len(base.items):
                return self._project_value(base, base.items[index])
            return self._project_value(base, _unknown_value())
        if base.items:
            projected = self._merge(base.items, node=node)
            projected.projection_uncertain = True
            projected.unknown = True
            projected.complete = False
            return self._project_value(base, projected)
        if base.container_kinds.intersection({"mapping", "namespace"}):
            selected = [
                base.fields[item]
                for item in sorted(key.literal_strings)
                if item in base.fields
            ]
            fallbacks: list[_Value] = []
            for item in sorted(key.literal_strings):
                if (
                    item not in base.fields
                    and "mapping" in base.container_kinds
                    and item in _MAPPING_METHODS
                ):
                    method = _callable_value(f"method:{item}")
                    method.method_receivers[item] = base.copy()
                    fallbacks.append(method)
            if key.complete and key.literal_strings:
                if selected or fallbacks:
                    return self._project_value(
                        base,
                        self._merge((*selected, *fallbacks), node=node),
                    )
                return self._project_value(base, _unknown_value())
            options = [value.copy() for value in base.fields.values()]
            if "mapping" in base.container_kinds:
                for method_name in sorted(_MAPPING_METHODS):
                    if method_name in base.fields:
                        continue
                    method = _callable_value(f"method:{method_name}")
                    method.method_receivers[method_name] = base.copy()
                    options.append(method)
            options.append(_unknown_value())
            return self._project_value(
                base,
                self._merge(options, node=node),
            )
        if base.context_paths:
            if key.complete and len(key.literal_strings) == 1:
                return self._context_attribute(
                    base,
                    next(iter(key.literal_strings)),
                    node=node,
                )
            self._opaque(node, "dynamic_context_item_access")
            return _unknown_value()
        if base.dynamic_scalar:
            return _dynamic_scalar_value()
        if base.ordinary and not base.unknown:
            return _dynamic_scalar_value()
        self._opaque(node, "unknown_item_receiver")
        return _unknown_value(external=base.external)

    def _context_attribute(
        self, base: _Value, attribute: str, *, node: nodes.Node
    ) -> _Value:
        paths = {
            f"{path}.{attribute}" for path in base.context_paths
        }
        if any(path.endswith(".event.data") for path in paths):
            self._opaque(node, "event_context_data_opaque")
            return _Value(context_paths=paths, unknown=True, complete=False)
        if paths in ({"trigger.event"}, {"wait.trigger.event"}):
            return _Value(context_paths=paths, complete=True)
        if all(
            path.endswith(".time_fired") or path.endswith(".context")
            for path in paths
        ):
            self._neutral(node, "event_context_metadata_dependency_neutral")
            return _ordinary_value()
        if paths == {"wait.trigger"}:
            ids = set(self.context.wait_trigger_entity_ids)
            return _Value(
                entity_ids=ids,
                context_paths=paths,
                unknown=not bool(ids),
                complete=bool(ids),
            )
        if any(
            path.startswith("wait.trigger.")
            and path.split("wait.trigger.", 1)[1]
            in _STATE_CONTEXT_ATTRIBUTES
            for path in paths
        ):
            attribute_name = next(iter(paths)).rsplit(".", 1)[-1]
            specific_ids = {
                "from_state": self.context.wait_trigger_from_state_entity_ids,
                "to_state": self.context.wait_trigger_to_state_entity_ids,
                "zone": self.context.wait_trigger_zone_entity_ids,
            }.get(attribute_name, ())
            fallback_ids = (
                self.context.wait_trigger_entity_ids
                if attribute_name in {"entity_id", "from_state", "to_state"}
                else ()
            )
            ids = set(specific_ids or fallback_ids)
            if ids:
                value = _Value(
                    entity_ids=ids,
                    state_object=True,
                    context_paths=paths,
                    complete=True,
                )
                self._consume_entity_value(
                    value,
                    node=node,
                    kind="wait_trigger_context",
                    reason="wait_trigger_exact_configuration_provenance",
                )
                return value
            self._opaque(node, "wait_trigger_context_opaque")
            return _Value(context_paths=paths, unknown=True, complete=False)
        if any(
            path.startswith("trigger.")
            and path.split(".", 1)[1] in _STATE_CONTEXT_ATTRIBUTES
            for path in paths
        ):
            attribute_name = next(iter(paths)).rsplit(".", 1)[-1]
            specific_ids = {
                "from_state": self.context.trigger_from_state_entity_ids,
                "to_state": self.context.trigger_to_state_entity_ids,
                "zone": self.context.trigger_zone_entity_ids,
            }.get(attribute_name, ())
            fallback_ids = (
                self.context.trigger_entity_ids
                if attribute_name in {"entity_id", "from_state", "to_state"}
                else ()
            )
            ids = set(specific_ids or fallback_ids)
            if ids:
                value = _Value(
                    entity_ids=ids,
                    state_object=attribute in {"from_state", "to_state", "zone"},
                    context_paths=paths,
                    complete=True,
                )
                self._consume_entity_value(
                    value,
                    node=node,
                    kind="trigger_context",
                    reason="trigger_exact_configuration_provenance",
                )
                return value
            self._opaque(node, "trigger_context_entity_opaque")
            return _Value(context_paths=paths, unknown=True, complete=False)
        if (
            attribute in _DYNAMIC_CONTEXT_SCALAR_ATTRIBUTES
            or (
                attribute in _WAIT_DYNAMIC_SCALAR_ATTRIBUTES
                and paths == {f"wait.{attribute}"}
            )
        ):
            self._neutral(node, "trigger_context_scalar_dependency_neutral")
            value = _dynamic_scalar_value()
            value.context_paths = paths
            return value
        if attribute in _NEUTRAL_CONTEXT_ATTRIBUTES:
            self._neutral(node, "trigger_context_metadata_dependency_neutral")
            return _ordinary_value()
        self._opaque(node, "unknown_context_attribute")
        return _Value(context_paths=paths, unknown=True, complete=False)

    def _consume_entity_value(
        self,
        value: _Value,
        *,
        node: nodes.Node,
        kind: str,
        reason: str,
    ) -> None:
        exact = sorted(
            {
                item
                for item in (*value.entity_ids, *value.literal_strings)
                if self.valid_entity_id(item)
            }
        )
        if value.limit_exceeded:
            self._emit(
                outcome="coverage_failure",
                kind=kind,
                reason="entity_candidate_limit_exceeded",
                category="state_entity_access",
                node=node,
                exact=tuple(exact),
                domains=(
                    tuple(sorted(value.possible_domains))
                    if value.domain_evidence_complete
                    else None
                ),
                context=tuple(sorted(value.context_paths)),
                limit=True,
                lock="coverage_failure",
            )
            return
        if exact:
            self._emit(
                outcome="exact_dependency",
                kind=kind,
                reason=reason,
                category="state_entity_access",
                node=node,
                exact=tuple(exact),
                domains=(
                    tuple(sorted(value.possible_domains))
                    if value.domain_evidence_complete
                    else None
                ),
                context=tuple(sorted(value.context_paths)),
                lock="exact",
            )
            if value.complete and not value.unknown:
                return
        if value.possible_domains and value.domain_evidence_complete:
            self._emit(
                outcome="exact_dependency",
                kind=kind,
                reason=reason,
                category="state_entity_access",
                node=node,
                domains=tuple(sorted(value.possible_domains)),
                context=tuple(sorted(value.context_paths)),
                lock="conservative",
            )
            return
        self._emit(
            outcome="bounded_semantic_opaque",
            kind=kind,
            reason=f"{reason}_target_opaque",
            category="state_entity_access",
            node=node,
            exact=tuple(exact),
            domains=(
                tuple(sorted(value.possible_domains))
                if value.domain_evidence_complete
                else None
            ),
            context=tuple(sorted(value.context_paths)),
            limit=False,
            lock="conservative",
        )

    def _external_template_boundary(
        self, node: nodes.Stmt, scope: dict[str, _Value]
    ) -> None:
        self._external_count += 1
        if self._external_count > MAX_TEMPLATE_EXTERNAL_REFERENCES:
            raise _AnalysisLimit("external_template_reference_limit_exceeded")
        template_node = getattr(node, "template", None)
        if isinstance(template_node, nodes.Expr):
            self._eval(template_node, scope, depth=1)
        name = (
            str(template_node.value)
            if isinstance(template_node, nodes.Const)
            and isinstance(template_node.value, str)
            else None
        )
        kind = type(node).__name__.lower()
        self._emit(
            outcome="bounded_semantic_opaque",
            kind=f"external_template_{kind}",
            reason=(
                "external_template_content_unavailable"
                if name is not None
                else "dynamic_external_template_name"
            ),
            category="external_opaque",
            node=node,
            external=name,
            lock="conservative",
        )
        if isinstance(node, nodes.Import):
            scope[node.target] = _unknown_value(external=True)
        elif isinstance(node, nodes.FromImport):
            for item in node.names:
                target = item[1] if isinstance(item, tuple) else item
                scope[str(target)] = _unknown_value(external=True)
        self._check_binding_limit(scope)

    def _bind(
        self, target: nodes.Node, value: _Value, scope: dict[str, _Value]
    ) -> None:
        self._visited_nodes.add(id(target))
        if isinstance(target, nodes.Name):
            scope[target.name] = value.copy()
        elif isinstance(target, (nodes.Tuple, nodes.List)):
            if value.state_collection or value.state_object:
                self._consume_entity_value(
                    value,
                    node=target,
                    kind="destructuring_state_operand",
                    reason="destructuring_iterates_state_value",
                )
            for index, item in enumerate(target.items):
                selected = (
                    value.items[index]
                    if index < len(value.items)
                    else self._iteration_value(value)
                )
                self._bind(item, selected, scope)
        elif isinstance(target, nodes.NSRef):
            prior = scope.get(
                target.name,
                _Value(fields={}, container_kinds={"namespace"}),
            ).copy()
            if prior.namespace_ids:
                prior = self._resolve_namespace_history(
                    prior,
                    node=target,
                )
            prior.container_kinds.add("namespace")
            if (
                target.attr not in prior.fields
                and len(prior.fields) >= MAX_TEMPLATE_CANDIDATES
            ):
                raise _AnalysisLimit(
                    "template_value_container_limit_exceeded"
                )
            prior.fields[target.attr] = value.copy()
            scope[target.name] = prior
            self._record_namespace_mutation(prior, node=target)
        else:
            self._emit(
                outcome="bounded_semantic_opaque",
                kind="binding",
                reason="unsupported_binding_target",
                category="unknown",
                node=target,
                lock="conservative",
            )
        self._check_binding_limit(scope)

    def _iteration_value(self, value: _Value) -> _Value:
        if value.namespace_ids:
            value = self._resolve_namespace_history(value, node=None)
        if value.items:
            structured = [item for item in value.items if item.items]
            if structured:
                widths = {len(item.items) for item in structured}
                if (
                    len(structured) == len(value.items)
                    and len(widths) == 1
                    and next(iter(widths)) <= MAX_TEMPLATE_CANDIDATES
                ):
                    # Iteration over finite tuple/list alternatives is a
                    # position-wise union.  Flattening pairs in sequence and
                    # destructuring only the first pair can erase later entity
                    # candidates (notably mapping.items()).
                    width = next(iter(widths))
                    columns = [
                        self._merge(
                            [item.items[index] for item in structured],
                            node=None,
                        )
                        for index in range(width)
                    ]
                    return self._project_value(
                        value,
                        _Value(
                            items=columns,
                            ordinary=all(item.ordinary for item in columns),
                            unknown=any(item.unknown for item in columns),
                            complete=all(item.complete for item in columns),
                            limit_exceeded=any(
                                item.limit_exceeded for item in columns
                            ),
                        ),
                    )
                # Heterogeneous structured alternatives cannot be projected
                # positionally.  Preserve all provenance and force bounded
                # uncertainty instead of choosing one shape.
                merged = self._merge(value.items, node=None)
                merged.items = []
                merged.unknown = True
                merged.complete = False
                merged.projection_uncertain = True
                return self._project_value(value, merged)
            return self._project_value(
                value,
                self._merge(value.items, node=None),
            )
        if value.fields:
            return self._project_value(
                value,
                _ordinary_value(strings=tuple(value.fields)),
            )
        return value.copy()

    def _merge_branch_scopes(
        self,
        target: dict[str, _Value],
        first: dict[str, _Value],
        second: dict[str, _Value],
    ) -> None:
        for name in sorted(set(first).union(second)):
            values = [
                item
                for item in (first.get(name), second.get(name))
                if item is not None
            ]
            if values:
                merged = self._merge(values, node=None)
                if len(values) > 1 and not all(
                    _values_equivalent(values[0], value)
                    for value in values[1:]
                ):
                    merged.projection_uncertain = True
                target[name] = merged
        self._check_binding_limit(target)

    def _check_binding_limit(self, scope: dict[str, _Value]) -> None:
        if len(scope) > MAX_TEMPLATE_BINDINGS:
            raise _AnalysisLimit("template_binding_limit_exceeded")
        for value in scope.values():
            self._account_value_graph(value)

    def _account_value_graph(self, value: _Value) -> None:
        """Bound abstract-value growth independently of AST/work-unit size."""

        pending: list[tuple[_Value, int]] = [(value, 0)]
        visited: set[int] = set()
        while pending:
            current, depth = pending.pop()
            identity = id(current)
            if identity in visited:
                continue
            visited.add(identity)
            if depth > MAX_TEMPLATE_VALUE_DEPTH:
                raise _AnalysisLimit("template_abstract_value_limit_exceeded")
            size = (
                1
                + len(current.entity_ids)
                + len(current.literal_strings)
                + len(current.literal_numbers)
                + len(current.possible_domains)
                + len(current.callables)
                + len(current.container_kinds)
                + len(current.namespace_ids)
                + len(current.fields)
                + len(current.items)
                + len(current.method_receivers)
                + len(current.context_paths)
            )
            prior = self._abstract_value_sizes.get(identity, 0)
            if size > prior:
                self._abstract_value_units += size - prior
                self._abstract_value_sizes[identity] = size
            if self._abstract_value_units > MAX_TEMPLATE_ABSTRACT_VALUE_UNITS:
                raise _AnalysisLimit("template_abstract_value_limit_exceeded")
            pending.extend(
                (child, depth + 1)
                for child in (
                    *current.fields.values(),
                    *current.items,
                    *current.method_receivers.values(),
                )
            )

    def _mark_subtree(self, node: nodes.Node) -> None:
        """Mark a subtree discharged by an explicit dormant-boundary rule."""

        self._visited_nodes.add(id(node))
        self._visited_nodes.update(
            id(child) for child in node.find_all(nodes.Node)
        )

    def _audit_unvisited_nodes(self, tree: nodes.Template) -> None:
        """Terminate skipped AST expressions/statements conservatively."""

        for node in tree.find_all(nodes.Node):
            if id(node) in self._visited_nodes:
                continue
            if not isinstance(node, (nodes.Expr, nodes.Stmt)):
                continue
            self._visited_nodes.add(id(node))
            self._emit(
                outcome="bounded_semantic_opaque",
                kind="unvisited_ast_node",
                reason=f"unvisited_ast_node_{type(node).__name__}",
                category="unknown",
                node=node,
                lock="conservative",
            )

    def _unknown_node(
        self,
        node: nodes.Node,
        scope: dict[str, _Value],
        *,
        depth: int,
    ) -> None:
        self._emit(
            outcome="bounded_semantic_opaque",
            kind="unknown_ast_node",
            reason=f"unknown_ast_node_{type(node).__name__}",
            category="unknown",
            node=node,
            lock="conservative",
        )
        for child in node.iter_child_nodes():
            if isinstance(child, nodes.Stmt):
                self._analyze_statement(child, scope, depth=depth + 1)
            elif isinstance(child, nodes.Expr):
                self._eval(child, scope, depth=depth + 1)

    def _neutral(self, node: nodes.Node, reason: str) -> None:
        self._emit(
            outcome="proven_dependency_neutral",
            kind="reviewed_semantic",
            reason=reason,
            category="dependency_neutral",
            node=node,
        )

    def _opaque(self, node: nodes.Node, reason: str) -> None:
        self._emit(
            outcome="bounded_semantic_opaque",
            kind="semantic_operation",
            reason=reason,
            category="unknown",
            node=node,
            lock="conservative",
        )

    def _opaque_from_value(
        self,
        node: nodes.Node,
        value: _Value,
        reason: str,
        *,
        kind: str,
    ) -> None:
        exact = tuple(
            sorted(
                {
                    candidate
                    for candidate in (*value.entity_ids, *value.literal_strings)
                    if self.valid_entity_id(candidate)
                }
            )
        )
        self._emit(
            outcome=(
                "coverage_failure"
                if value.limit_exceeded
                else "bounded_semantic_opaque"
            ),
            kind=kind,
            reason=reason,
            category="dynamic_filter_test_dispatch",
            node=node,
            exact=exact,
            domains=(
                tuple(sorted(value.possible_domains))
                if value.domain_evidence_complete
                else None
            ),
            context=tuple(sorted(value.context_paths)),
            limit=value.limit_exceeded,
            lock=(
                "coverage_failure" if value.limit_exceeded else "conservative"
            ),
        )

    def _emit(
        self,
        *,
        outcome: str,
        kind: str,
        reason: str,
        category: str,
        node: nodes.Node | None,
        exact: tuple[str, ...] = (),
        domains: tuple[str, ...] | None = None,
        selectors: tuple[str, ...] = (),
        external: str | None = None,
        context: tuple[str, ...] = (),
        limit: bool = False,
        lock: str = "none",
    ) -> None:
        overflow = bool(
            len(set(exact)) > MAX_TEMPLATE_CANDIDATES
            or (
                domains is not None
                and len(set(domains)) > MAX_TEMPLATE_CANDIDATES
            )
            or len(set(selectors)) > MAX_TEMPLATE_CANDIDATES
            or len(set(context)) > 32
        )
        if overflow:
            outcome = "coverage_failure"
            kind = "template_analysis"
            reason = "obligation_evidence_limit_exceeded"
            category = "external_opaque"
            limit = True
            lock = "coverage_failure"
        if isinstance(external, str) and len(external) > 256:
            external = "oversized_sha256:" + hashlib.sha256(
                external.encode("utf-8", errors="replace")
            ).hexdigest()
        # Reserve the final bounded slot for the terminal coverage failure so
        # the result never grows past the advertised maximum.
        if len(self._raw_obligations) >= MAX_TEMPLATE_OBLIGATIONS - 1:
            if not any(
                item.get("reason") == "template_obligation_limit_exceeded"
                for item in self._raw_obligations
            ):
                self._raw_obligations.append(
                    {
                        "outcome": "coverage_failure",
                        "kind": "template_analysis",
                        "reason": "template_obligation_limit_exceeded",
                        "category": "external_opaque",
                        "node_type": "Template",
                        "line": 0,
                        "exact": (),
                        "domains": None,
                        "selectors": (),
                        "external": None,
                        "context": (),
                        "limit": True,
                        "lock": "coverage_failure",
                        "node_fingerprint": hashlib.sha256(
                            b"template_obligation_limit_exceeded"
                        ).hexdigest(),
                    }
                )
            return
        try:
            node_material = (
                node.dump()
                if node is not None
                else f"{kind}:{reason}:{self.config_path}"
            )
        except (RecursionError, TypeError, ValueError):
            node_material = f"{type(node).__name__}:{kind}:{reason}"
        self._raw_obligations.append(
            {
                "outcome": outcome,
                "kind": kind,
                "reason": reason[:96],
                "category": category,
                "node_type": type(node).__name__ if node is not None else "Template",
                "line": max(0, int(getattr(node, "lineno", 0) or 0)),
                "exact": tuple(sorted(set(exact)))[:MAX_TEMPLATE_CANDIDATES],
                "domains": (
                    tuple(sorted(set(domains)))[:MAX_TEMPLATE_CANDIDATES]
                    if domains is not None
                    else None
                ),
                "selectors": tuple(sorted(set(selectors)))[:MAX_TEMPLATE_CANDIDATES],
                "external": external[:256] if isinstance(external, str) else None,
                "context": tuple(sorted(set(context)))[:32],
                "limit": limit,
                "lock": lock,
                "node_fingerprint": hashlib.sha256(
                    node_material.encode("utf-8", errors="replace")
                ).hexdigest(),
            }
        )

    def _finalize(self) -> TemplateLedgerResult:
        if not self._raw_obligations:
            self._emit(
                outcome="proven_dependency_neutral",
                kind="whole_template",
                reason="whole_template_dependency_neutral",
                category="dependency_neutral",
                node=None,
            )
        ordered = sorted(
            self._raw_obligations,
            key=lambda item: json.dumps(
                item, sort_keys=True, separators=(",", ":")
            ),
        )
        obligations: list[DependencyObligation] = []
        for ordinal, item in enumerate(ordered):
            expression_fingerprint = str(item["node_fingerprint"])
            obligation_id = evidence_id(
                self.source_type,
                self.source_id,
                self.config_path,
                "obligation",
                ordinal,
                expression_fingerprint,
                item["outcome"],
            )
            obligations.append(
                DependencyObligation(
                    evidence_id=obligation_id,
                    source_type=self.source_type,
                    source_id=self.source_id,
                    config_path=self.config_path,
                    relation=self.relation,
                    outcome=str(item["outcome"]),
                    obligation_kind=str(item["kind"]),
                    reason_code=str(item["reason"]),
                    semantic_category=str(item["category"]),
                    semantic_registry_version=SEMANTIC_REGISTRY_MODEL,
                    semantic_registry_fingerprint=str(
                        self.registry["sha256"]
                    ),
                    expression_fingerprint=expression_fingerprint,
                    configuration_fingerprint=self.configuration_fingerprint,
                    exact_entity_ids=tuple(item["exact"]),
                    possible_entity_domains=item["domains"],
                    literal_selectors=tuple(item["selectors"]),
                    source_entity_id=self.source_entity_id,
                    source_name=self.source_name,
                    source_state=self.source_state,
                    external_template_name=item["external"],
                    context_provenance=tuple(item["context"]),
                    limit_exceeded=bool(item["limit"]),
                    lock_projection=str(item["lock"]),
                )
            )
        return TemplateLedgerResult(
            obligations=tuple(obligations),
            ast_node_count=self._ast_nodes,
            work_units=self._work_units,
            coverage_failed=any(
                item.outcome == "coverage_failure" for item in obligations
            ),
            semantic_registry_sha256=str(self.registry["sha256"]),
        )


def analyze_template_obligations(
    source: str,
    *,
    source_type: str,
    source_id: str,
    config_path: str,
    relation: str,
    source_entity_id: str | None,
    source_name: str | None,
    source_state: str | None,
    configuration_fingerprint: str,
    entity_id_validator: Callable[[str], bool],
    context: TemplateContextEvidence | None = None,
    entity_output_role: bool = False,
) -> TemplateLedgerResult:
    return TemplateObligationAnalyzer(
        source_type=source_type,
        source_id=source_id,
        config_path=config_path,
        relation=relation,
        source_entity_id=source_entity_id,
        source_name=source_name,
        source_state=source_state,
        configuration_fingerprint=configuration_fingerprint,
        entity_id_validator=entity_id_validator,
        context=context,
        entity_output_role=entity_output_role,
    ).analyze(source)


__all__ = [
    "MAX_TEMPLATE_AST_NODES",
    "MAX_TEMPLATE_BINDINGS",
    "MAX_TEMPLATE_CANDIDATES",
    "MAX_TEMPLATE_DEPTH",
    "MAX_TEMPLATE_OBLIGATIONS",
    "MAX_TEMPLATE_SOURCE_CHARS",
    "MAX_TEMPLATE_WORK_UNITS",
    "TemplateContextEvidence",
    "TemplateLedgerResult",
    "TemplateObligationAnalyzer",
    "analyze_template_obligations",
]
