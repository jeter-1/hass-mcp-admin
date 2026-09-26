"""Parse-only script invocation evidence for the diagnostic graph, never authority.

Service names bind registry storage keys, not entity object IDs. See the pinned
Core source evidence in docs/SCRIPT_CALL_DEPENDENCIES.md. No template is rendered.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import replace
import re
from typing import Any

from ..sanitization import sanitize_untrusted_data
from .models import DependencyFinding, evidence_id

MAX_ACTION_NODES = 10_000
MAX_ACTION_DEPTH = 32
MAX_CALLS = 10_000
MAX_GRAPH_STEPS = 5_000
MAX_GRAPH_RESULTS = 500
_SERVICE = re.compile(r"script\.([a-z0-9_]{1,128})\Z", re.ASCII)
_ENTITY = re.compile(r"script\.[a-z0-9_]{1,121}\Z", re.ASCII)
_BUILTINS = frozenset({"turn_on", "turn_off", "toggle", "reload"})


def extract_script_calls(
    config: dict, *, source_type: str, source_id: str,
    source_entity_id: str | None, identities: dict[str, str], secret: str,
) -> tuple[list[DependencyFinding], Counter]:
    """Walk only action bodies. Unknown selectors remain gaps, never guessed edges."""
    calls: list[DependencyFinding] = []
    gaps: Counter = Counter()
    if sanitize_untrusted_data([source_id, source_entity_id], known_secrets=(secret,)).redaction_applied:
        return calls, Counter({"script_call_source_identity_redacted": 1})
    by_key = {key: entity for entity, key in identities.items()}
    visited = 0
    targets_seen = 0

    def add(target: str, path: str) -> None:
        if len(calls) >= MAX_CALLS:
            gaps["script_call_edge_limit_exceeded"] += 1
            return
        if sanitize_untrusted_data(target, known_secrets=(secret,)).redaction_applied:
            gaps["script_call_target_redacted"] += 1
            return
        calls.append(DependencyFinding(
            evidence_id=evidence_id("script_call", source_type, source_id, path, target),
            target_entity_id=target, source_type=source_type, source_id=source_id,
            source_entity_id=source_entity_id, source_name=None, relation="script_call",
            config_path=path, evidence_summary="Literal script invocation in an action position; execution is not observed.",
        ))

    def target_values(value: Any, path: str) -> None:
        nonlocal targets_seen
        if targets_seen >= MAX_CALLS:
            gaps["script_call_target_items_exceeded"] += 1
            return
        targets_seen += 1
        if isinstance(value, str) and _ENTITY.fullmatch(value):
            add(value, path)
            if value not in identities:
                gaps["script_call_target_identity_unavailable"] += 1
        elif isinstance(value, list):
            for i, member in enumerate(value[:MAX_CALLS]):
                if targets_seen >= MAX_CALLS:
                    gaps["script_call_target_items_exceeded"] += 1
                    break
                if isinstance(member, list):
                    gaps["script_call_target_unresolved"] += 1
                else:
                    target_values(member, f"{path}[{i}]")
            if len(value) > MAX_CALLS:
                gaps["script_call_edge_limit_exceeded"] += 1
        else:
            gaps["script_call_target_unresolved"] += 1

    def service(step: dict, path: str) -> None:
        keys = [key for key in ("action", "service", "service_template") if key in step]
        if not keys:
            return
        if len(keys) != 1:
            gaps["script_call_service_ambiguous"] += 1
            return
        key = keys[0]
        value = step[key]
        if not isinstance(value, str) or any(token in value for token in ("{{", "{%", "{#")):
            gaps["script_call_service_dynamic"] += 1
            return
        match = _SERVICE.fullmatch(value)
        if not match:
            if value.startswith("script."):
                gaps["script_call_service_unresolved"] += 1
            # Generic turn_on/toggle can invoke scripts but is not resolved here.
            if value in {"homeassistant.turn_on", "homeassistant.toggle"}:
                gaps["script_call_generic_service_unresolved"] += 1
            return
        name = match[1]
        if name in _BUILTINS and name in by_key:
            gaps["script_call_reserved_service_collision"] += 1
            return
        if name == "toggle":
            gaps["script_call_toggle_unresolved"] += 1
        elif name == "turn_on":
            if sum(isinstance(step.get(section), dict) and "entity_id" in step[section]
                   for section in ("target", "data", "data_template")) > 1:
                gaps["script_call_target_ambiguous"] += 1
                return
            found = False
            for section in ("target", "data", "data_template"):
                if section not in step:
                    continue
                fields = step[section]
                if not isinstance(fields, dict):
                    gaps["script_call_target_unresolved"] += 1
                    continue
                if "entity_id" in fields:
                    found = True
                    target_values(fields["entity_id"], f"{path}.{section}.entity_id")
                if any(k in fields for k in ("area_id", "device_id", "label_id", "floor_id")):
                    gaps["script_call_selector_unresolved"] += 1
            if not found:
                gaps["script_call_target_unresolved"] += 1
        elif name not in _BUILTINS:
            target = by_key.get(name)
            if target is None:
                gaps["script_call_service_identity_unavailable"] += 1
            else:
                add(target, f"{path}.{key}")

    def walk(value: Any, path: str, depth: int) -> None:
        nonlocal visited
        if depth > MAX_ACTION_DEPTH:
            gaps["script_call_action_depth_exceeded"] += 1
            return
        visited += 1
        if visited > MAX_ACTION_NODES:
            gaps["script_call_action_nodes_exceeded"] += 1
            return
        if isinstance(value, list):
            for i, item in enumerate(value):
                if visited >= MAX_ACTION_NODES:
                    gaps["script_call_action_nodes_exceeded"] += 1
                    return
                walk(item, f"{path}[{i}]", depth + 1)
            return
        if not isinstance(value, dict):
            gaps["script_call_action_invalid"] += 1
            return
        if value.get("enabled") is False:
            return
        if "enabled" in value and value["enabled"] is not True:
            gaps["script_call_action_enabled_unresolved"] += 1
            return
        service(value, path)
        # These keys are action containers, never arbitrary nested data/variables.
        for key in ("sequence", "then", "else", "default", "parallel"):
            if key in value:
                walk(value[key], f"{path}.{key}", depth + 1)
        if "choose" in value:
            options = value["choose"]
            if isinstance(options, list):
                for i, option in enumerate(options[:MAX_ACTION_NODES]):
                    if visited >= MAX_ACTION_NODES:
                        gaps["script_call_action_nodes_exceeded"] += 1
                        break
                    visited += 1
                    if isinstance(option, dict) and "sequence" in option:
                        walk(option["sequence"], f"{path}.choose[{i}].sequence", depth + 1)
                if len(options) > MAX_ACTION_NODES:
                    gaps["script_call_action_nodes_exceeded"] += 1
            else:
                gaps["script_call_action_invalid"] += 1
        if "repeat" in value:
            repeat = value["repeat"]
            if isinstance(repeat, dict) and "sequence" in repeat:
                walk(repeat["sequence"], f"{path}.repeat.sequence", depth + 1)
            else:
                gaps["script_call_action_invalid"] += 1

    roots = ("sequence",) if source_type == "script" else ("action", "actions")
    present = [key for key in roots if key in config]
    if len(present) > 1:
        gaps["script_call_action_root_ambiguous"] += 1
    else:
        for key in present:
            walk(config[key], f"$.{key}", 0)
    if "use_blueprint" in config:
        gaps["script_call_blueprint_body_unresolved"] += 1
    return calls, gaps


def traverse_script_calls(
    findings, calls, *, target: str, requested: list[str], max_depth: int,
) -> tuple[list[DependencyFinding], list[str]]:
    """Follow reverse invocation paths, retaining distinct bounded evidence paths.

    A state observation of a script is not a call. Only script-owned references
    seed paths, and only parsed invocation edges extend them. Source filtering
    applies to results; script intermediates remain visible to the traversal.
    """
    inbound = defaultdict(list)
    for call in calls:
        inbound[call.target_entity_id].append(call)
    for edges in inbound.values():
        edges.sort(key=lambda item: item.evidence_id)
    queue = deque()
    results = []
    gaps = set()
    steps = 0
    for item in sorted(findings, key=lambda item: item.evidence_id):
        if item.target_entity_id == target and item.source_type == "script" and item.source_entity_id:
            if len(queue) >= MAX_GRAPH_STEPS:
                gaps.add("script_call_traversal_limit_exceeded")
                break
            queue.append((item, (item.evidence_id,), frozenset({target})))
    while queue:
        item, path, visited = queue.popleft()
        current = item.source_entity_id
        if current in visited:
            gaps.add("script_call_cycle_detected")
            continue
        edges = inbound.get(current, ())
        if len(path) >= max_depth:
            if edges:
                gaps.add("script_call_max_depth_reached")
            continue
        for call in edges:
            steps += 1
            if steps > MAX_GRAPH_STEPS or len(results) >= MAX_GRAPH_RESULTS:
                gaps.add("script_call_traversal_limit_exceeded")
                return results, sorted(gaps)
            if call.source_entity_id in visited or call.source_entity_id == current:
                gaps.add("script_call_cycle_detected")
                continue
            chain = (*path, call.evidence_id)
            if call.source_type in requested:
                results.append(replace(
                    call, evidence_id=evidence_id("script_indirect", *chain),
                    direct=False, depth=len(chain), confidence="exact_static_chain",
                    evidence_path=chain,
                    evidence_summary=f"Static reference through script invocation of {current}; execution is not observed.",
                ))
            if call.source_type == "script":
                queue.append((call, chain, visited | {current}))
    return results, sorted(gaps)
