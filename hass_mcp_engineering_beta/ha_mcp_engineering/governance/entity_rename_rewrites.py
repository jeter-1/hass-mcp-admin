"""Generated dependent reference rewrites for an entity rename (HAMCP-295 3.3).

An occurrence of ``<old>`` is rewritten to ``<new>`` only inside the exact
boundary B1-B6 of the design.  Every other identified occurrence is listed as
not rewritten with its exact reason code; it is never edited.  The caller
cannot add, remove or supply a rewrite: this module derives every rewrite
from the holder configuration and the HAMCP-227 extractor alone.

Pure: no I/O.  The planner supplies holder configurations read through the
existing governed adapters.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from ..dependency.extraction import (
    ENTITY_BEARING_KEYS,
    FREE_TEXT_KEYS,
    extract_document_obligation_evidence,
)
from .entity_rename import entity_domain
from .entity_rename_policy import MAX_DEPENDENT_REWRITES


# B1: holders with a governed writer, in fixed execution order.
HOLDER_ORDER = ("automation", "script", "scene")
HOLDER_RESOURCE_TYPES = {
    "automation": "automation",
    "script": "script",
    "scene": "scene_configuration",
}
NO_GOVERNED_WRITER_HOLDERS = frozenset({"dashboard", "group", "template", "helper"})

TEMPLATE_NOT_REWRITTEN = "template_occurrence_not_rewritten"
BLUEPRINT_NOT_REWRITTEN = "blueprint_input_not_rewritten"
EVENT_DATA_NOT_REWRITTEN = "event_data_occurrence_not_rewritten"
UNSUPPORTED_VALUE_SHAPE = "unsupported_value_shape"
HOLDER_COVERAGE_INCOMPLETE = "holder_coverage_incomplete"
REWRITE_EVIDENCE_MISMATCH = "rewrite_evidence_mismatch"
NO_GOVERNED_WRITER = "no_governed_writer"
HOLDER_ADAPTER_NOT_ADMITTED = "holder_adapter_not_admitted"
NOT_REWRITTEN_REASONS = frozenset(
    {
        TEMPLATE_NOT_REWRITTEN,
        BLUEPRINT_NOT_REWRITTEN,
        EVENT_DATA_NOT_REWRITTEN,
        UNSUPPORTED_VALUE_SHAPE,
        HOLDER_COVERAGE_INCOMPLETE,
        REWRITE_EVIDENCE_MISMATCH,
        NO_GOVERNED_WRITER,
        HOLDER_ADAPTER_NOT_ADMITTED,
    }
)

REFUSAL_LIMIT = "entity_rename_dependent_steps_exceed_limit"
REFUSAL_REFERENCE_SET_CHANGED = "entity_rename_reference_set_changed"

_TEMPLATE_MARKERS = ("{{", "{%", "{#")
_COVERAGE_FAILURE_OUTCOMES = frozenset({"coverage_failure"})
_COVERAGE_FAILURE_KINDS = frozenset({"coverage_failure", "external_blueprint_source"})
_EVENT_TRIGGER_KINDS = frozenset(
    {"state_changed_event_trigger", "call_service_event_trigger"}
)
_STRUCTURED_KIND = "structured_entity_reference"
_STRUCTURED_OUTCOME = "exact_dependency"
_STRUCTURED_MATCH = "structured_exact"


class EntityRenameRewriteLimitError(ValueError):
    def __init__(self, count: int) -> None:
        super().__init__(REFUSAL_LIMIT)
        self.code = REFUSAL_LIMIT
        self.count = count


@dataclass(frozen=True)
class Occurrence:
    position: tuple[Any, ...]
    path: str
    kind: str  # "value", "list_element" or "scene_key"
    reason: str | None  # None for a B3/B4 candidate


@dataclass
class HolderRewrite:
    holder_type: str
    holder_id: str
    current_config: dict[str, Any]
    proposed_config: dict[str, Any] | None
    rewritten_paths: tuple[str, ...]
    not_rewritten: list[dict[str, str]] = field(default_factory=list)

    @property
    def resource_type(self) -> str:
        return HOLDER_RESOURCE_TYPES[self.holder_type]


@dataclass
class RewritePlan:
    steps: list[HolderRewrite]
    not_rewritten_references: list[dict[str, str]]

    @property
    def not_rewritten_count(self) -> int:
        return len(self.not_rewritten_references)


def _token(old: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9_])" + re.escape(old) + r"(?![a-z0-9_])")


def _child_path(path: str, key: Any) -> str:
    return f"{path}[{key}]" if isinstance(key, int) else f"{path}.{key}"


def _under(path: str, prefixes: frozenset[str]) -> bool:
    return any(
        path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "[")
        for prefix in prefixes
    )


def _scan(
    config: dict[str, Any],
    old: str,
    holder_type: str,
    event_trigger_paths: frozenset[str] = frozenset(),
) -> list[Occurrence]:
    """Return every identified occurrence of ``<old>`` with its B-class."""

    token = _token(old)
    found: list[Occurrence] = []

    def leaf(value: str, position: tuple[Any, ...], path: str, parent_key: Any, kind: str) -> None:
        if not token.search(value):
            return
        if parent_key in FREE_TEXT_KEYS and not any(m in value for m in _TEMPLATE_MARKERS):
            return  # free text is neither rewritten nor counted
        if any(marker in value for marker in _TEMPLATE_MARKERS):
            reason = TEMPLATE_NOT_REWRITTEN
        elif position and position[0] == "use_blueprint":
            reason = BLUEPRINT_NOT_REWRITTEN
        elif _under(path, event_trigger_paths):
            reason = EVENT_DATA_NOT_REWRITTEN
        elif (
            holder_type in {"automation", "script"}
            and parent_key in ENTITY_BEARING_KEYS
            and value == old
        ):
            reason = None
        else:
            reason = UNSUPPORTED_VALUE_SHAPE
        found.append(Occurrence(position, path, kind, reason))

    def walk(value: Any, position: tuple[Any, ...], path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_position = (*position, key)
                child_path = _child_path(path, key)
                if isinstance(key, str) and token.search(key):
                    candidate = (
                        holder_type == "scene" and position == ("entities",) and key == old
                    )
                    found.append(
                        Occurrence(
                            child_position,
                            child_path,
                            "scene_key",
                            None if candidate else UNSUPPORTED_VALUE_SHAPE,
                        )
                    )
                if isinstance(child, str):
                    leaf(child, child_position, child_path, key, "value")
                elif isinstance(child, list) and key in ENTITY_BEARING_KEYS:
                    for index, item in enumerate(child):
                        item_position = (*child_position, index)
                        if isinstance(item, str):
                            # B3(b): the leaf's path is the key's path.
                            leaf(item, item_position, child_path, key, "list_element")
                        else:
                            walk(item, item_position, _child_path(child_path, index))
                else:
                    walk(child, child_position, child_path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                item_position = (*position, index)
                item_path = _child_path(path, index)
                if isinstance(item, str):
                    leaf(item, item_position, item_path, None, "value")
                else:
                    walk(item, item_position, item_path)

    walk(config, (), "$")
    return found


def _extract(holder_type: str, holder_id: str, config: dict[str, Any]):
    _findings, _dynamic, obligations = extract_document_obligation_evidence(
        source_type=holder_type, source_id=holder_id, config=config
    )
    return _findings, obligations


def _structured_paths(findings, obligations, entity_id: str) -> list[str]:
    finding_paths = {
        item.config_path
        for item in findings
        if item.target_entity_id == entity_id and item.match_type == _STRUCTURED_MATCH
    }
    return sorted(
        item.config_path
        for item in obligations
        if item.obligation_kind == _STRUCTURED_KIND
        and item.outcome == _STRUCTURED_OUTCOME
        and tuple(item.exact_entity_ids) == (entity_id,)
        and item.config_path in finding_paths
    )


def _replace(config: dict[str, Any], occurrences: list[Occurrence], old: str, new: str) -> dict[str, Any]:
    proposed = copy.deepcopy(config)
    for occurrence in occurrences:
        *parents, last = occurrence.position
        container: Any = proposed
        for key in parents:
            container = container[key]
        if occurrence.kind == "scene_key":
            items = list(container.items())
            container.clear()
            for key, value in items:
                container[new if key == old else key] = value
        else:
            if container[last] != old:
                raise ValueError("rewrite leaf drifted")
            container[last] = new
    return proposed


def _signature(item: Any, path_map: dict[str, str]) -> tuple[Any, ...]:
    path = item.config_path
    for before, after in path_map.items():
        if path == before or path.startswith(before + ".") or path.startswith(before + "["):
            path = after + path[len(before):]
            break
    return (
        item.relation,
        path,
        item.outcome,
        item.obligation_kind,
        item.reason_code,
        tuple(item.exact_entity_ids),
        item.possible_entity_domains,
        tuple(item.literal_selectors),
    )


def _finding_signature(item: Any, path_map: dict[str, str]) -> tuple[Any, ...]:
    path = item.config_path
    for before, after in path_map.items():
        if path == before or path.startswith(before + ".") or path.startswith(before + "["):
            path = after + path[len(before):]
            break
    return (item.target_entity_id, item.relation, path, item.match_type)


def _b6_holds(
    holder_type: str,
    holder_id: str,
    current: dict[str, Any],
    proposed: dict[str, Any],
    candidates: list[Occurrence],
    old: str,
    new: str,
    before: tuple[list[Any], list[Any]],
) -> bool:
    findings_before, obligations_before = before
    findings_after, obligations_after = _extract(holder_type, holder_id, proposed)
    candidate_paths = [occurrence.path for occurrence in candidates]
    remaining = list(_structured_paths(findings_before, obligations_before, old))
    for path in candidate_paths:
        remaining.remove(path)
    if _structured_paths(findings_after, obligations_after, old) != sorted(remaining):
        return False
    path_map = {
        occurrence.path: occurrence.path[: -len(old)] + new
        for occurrence in candidates
        if occurrence.kind == "scene_key"
    }
    rewritten = {occurrence.path for occurrence in candidates}

    def renamed(sig: tuple[Any, ...], index: int) -> tuple[Any, ...]:
        return sig[:index] + ((new,),) + sig[index + 1:]

    expected_obligations = []
    for item in obligations_before:
        sig = _signature(item, path_map)
        if (
            item.config_path in rewritten
            and item.obligation_kind == _STRUCTURED_KIND
            and tuple(item.exact_entity_ids) == (old,)
        ):
            sig = renamed(sig, 5)
        expected_obligations.append(sig)
    if sorted(map(repr, expected_obligations)) != sorted(
        repr(_signature(item, {})) for item in obligations_after
    ):
        return False
    expected_findings = []
    for item in findings_before:
        sig = _finding_signature(item, path_map)
        if item.config_path in rewritten and item.target_entity_id == old and item.match_type == _STRUCTURED_MATCH:
            sig = (new, *sig[1:])
        expected_findings.append(sig)
    if sorted(map(repr, expected_findings)) != sorted(
        repr(_finding_signature(item, {})) for item in findings_after
    ):
        return False
    # The proposal differs from the current config only at the replaced leaves.
    restored = _replace(
        proposed,
        [
            Occurrence(
                (*o.position[:-1], new) if o.kind == "scene_key" else o.position,
                o.path,
                o.kind,
                None,
            )
            for o in candidates
        ],
        new,
        old,
    )
    return restored == current and list(restored) == list(current)


def plan_holder_rewrite(
    *,
    holder_type: str,
    holder_id: str,
    config: dict[str, Any],
    old: str,
    new: str,
    adapter_admitted: bool = True,
) -> HolderRewrite:
    """Return the B1-B6 rewrite of one holder and its not-rewritten list."""

    if holder_type not in HOLDER_RESOURCE_TYPES:
        raise ValueError("holder type has no governed writer")
    if entity_domain(old) != entity_domain(new) or old == new:
        raise ValueError("rename must keep the domain and change the id")
    if not isinstance(config, dict) or not isinstance(holder_id, str) or not holder_id:
        raise ValueError("holder configuration is malformed")
    findings, obligations = _extract(holder_type, holder_id, config)
    event_trigger_paths = frozenset(
        item.config_path
        for item in obligations
        if item.obligation_kind in _EVENT_TRIGGER_KINDS
    )
    occurrences = _scan(config, old, holder_type, event_trigger_paths)

    def listed(items: list[Occurrence], reason: str | None = None) -> list[dict[str, str]]:
        return [
            {
                "holder_type": holder_type,
                "holder_id": holder_id,
                "position": item.path,
                "reason": reason or item.reason,  # type: ignore[dict-item]
            }
            for item in items
        ]

    def unwritten(entries: list[dict[str, str]]) -> HolderRewrite:
        return HolderRewrite(holder_type, holder_id, config, None, (), entries)

    candidates = [item for item in occurrences if item.reason is None]
    others = [item for item in occurrences if item.reason is not None]
    coverage_failed = any(
        item.outcome in _COVERAGE_FAILURE_OUTCOMES
        or item.obligation_kind in _COVERAGE_FAILURE_KINDS
        or item.limit_exceeded
        for item in obligations
    )
    if coverage_failed:
        # Occurrence-specific reasons are kept; every would-be rewrite is
        # listed as coverage incomplete.  A gap with no identified
        # occurrence is not counted (section 3.3).
        return unwritten(listed(candidates, HOLDER_COVERAGE_INCOMPLETE) + listed(others))
    if not candidates:
        return unwritten(listed(others))
    if not adapter_admitted:
        return unwritten(listed(candidates, HOLDER_ADAPTER_NOT_ADMITTED) + listed(others))
    # B2 + B5: every candidate is a B2 occurrence, one to one, and every B2
    # occurrence that is not a candidate sits at a listed never-rewritten
    # position.  Anything else fails closed for the whole holder.
    structured = _structured_paths(findings, obligations, old)
    candidate_paths = sorted(item.path for item in candidates)
    remaining = list(structured)
    for path in candidate_paths:
        if path not in remaining:
            return unwritten(listed(candidates, REWRITE_EVIDENCE_MISMATCH) + listed(others))
        remaining.remove(path)
    other_paths = {item.path for item in others}
    if any(path not in other_paths for path in remaining):
        return unwritten(listed(candidates, REWRITE_EVIDENCE_MISMATCH) + listed(others))
    proposed = _replace(config, candidates, old, new)
    if not _b6_holds(
        holder_type, holder_id, config, proposed, candidates, old, new, (findings, obligations)
    ):
        return unwritten(listed(candidates, REWRITE_EVIDENCE_MISMATCH) + listed(others))
    return HolderRewrite(
        holder_type,
        holder_id,
        config,
        proposed,
        tuple(sorted({item.path for item in candidates})),
        listed(others),
    )


def _sort_key(entry: dict[str, str]) -> tuple[str, str, str, str]:
    return (entry["holder_type"], entry["holder_id"], entry["position"], entry["reason"])


def plan_rewrites(
    *,
    old: str,
    new: str,
    holders: list[dict[str, Any]],
    other_references: list[dict[str, Any]] = (),  # type: ignore[assignment]
) -> RewritePlan:
    """Return the ordered rewrite steps and the sorted not-rewritten set.

    ``holders``: each ``{holder_type, holder_id, config, adapter_admitted}``
    for a B1 holder the impact evidence identified.  ``other_references``:
    each ``{holder_type, holder_id, position}`` for a holder without a
    governed writer (dashboards, groups); always ``no_governed_writer``.
    """

    seen: set[tuple[str, str]] = set()
    rewrites: list[HolderRewrite] = []
    not_rewritten: list[dict[str, str]] = []
    for holder in holders:
        key = (holder["holder_type"], holder["holder_id"])
        if key in seen:
            raise ValueError("duplicate holder")
        seen.add(key)
        result = plan_holder_rewrite(
            holder_type=holder["holder_type"],
            holder_id=holder["holder_id"],
            config=holder["config"],
            old=old,
            new=new,
            adapter_admitted=holder.get("adapter_admitted", True) is True,
        )
        not_rewritten.extend(result.not_rewritten)
        if result.proposed_config is not None:
            rewrites.append(result)
    for reference in other_references:
        holder_type = reference.get("holder_type")
        if holder_type in HOLDER_RESOURCE_TYPES:
            raise ValueError("a B1 holder must be planned through holders")
        not_rewritten.append(
            {
                "holder_type": str(holder_type),
                "holder_id": str(reference.get("holder_id")),
                "position": str(reference.get("position") or "$"),
                "reason": NO_GOVERNED_WRITER,
            }
        )
    if len(rewrites) > MAX_DEPENDENT_REWRITES:
        raise EntityRenameRewriteLimitError(len(rewrites))
    rewrites.sort(key=lambda item: (HOLDER_ORDER.index(item.holder_type), item.holder_id))
    return RewritePlan(rewrites, sorted(not_rewritten, key=_sort_key))


def reference_set_changed(
    persisted: list[dict[str, str]], rederived: list[dict[str, str]]
) -> bool:
    """Apply-time recheck: any difference refuses the plan (N34)."""

    return sorted(persisted, key=_sort_key) != sorted(rederived, key=_sort_key)
