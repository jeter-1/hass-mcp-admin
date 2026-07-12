"""Deterministic, behavior-preserving automation normalization and diffing."""

from __future__ import annotations

import hashlib
import json
from typing import Any


ALIASES = {"triggers": "trigger", "conditions": "condition", "actions": "action"}
OPTIONAL_EMPTY = {"condition": [], "variables": {}, "trace": {}}
DIFF_LABELS = {
    "alias": "alias",
    "description": "description",
    "mode": "mode",
    "max": "maximum_runs",
    "trigger": "triggers",
    "condition": "conditions",
    "action": "actions",
    "variables": "variables",
    "trace": "trace_settings",
    "use_blueprint": "blueprint_usage",
}


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def normalize_automation(config: dict[str, Any] | None) -> dict[str, Any] | None:
    if config is None:
        return None
    normalized: dict[str, Any] = {}
    for original_key, value in config.items():
        key = ALIASES.get(original_key, original_key)
        if key in normalized and original_key != key:
            # Never silently discard an unknown/duplicate representation.
            normalized[original_key] = _canonical(value)
        else:
            normalized[key] = _canonical(value)
    for key, empty in OPTIONAL_EMPTY.items():
        if normalized.get(key) == empty:
            normalized.pop(key)
    return _canonical(normalized)


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def state_fingerprint(config: dict[str, Any] | None) -> str:
    return stable_hash(normalize_automation(config))


def _summary(value: Any) -> Any:
    if isinstance(value, list):
        return {"type": "list", "count": len(value)}
    if isinstance(value, dict):
        return {"type": "object", "keys": sorted(value)[:20], "key_count": len(value)}
    if isinstance(value, str):
        return value[:160] + ("..." if len(value) > 160 else "")
    return value


def structured_diff(
    current: dict[str, Any] | None, proposed: dict[str, Any]
) -> dict[str, Any]:
    before = normalize_automation(current) or {}
    after = normalize_automation(proposed) or {}
    changed, unchanged = [], []
    for key in sorted(set(before) | set(after)):
        label = DIFF_LABELS.get(key, f"other:{key}")
        if before.get(key) == after.get(key):
            unchanged.append(label)
            continue
        change_type = "added" if key not in before else "removed" if key not in after else "modified"
        changed.append(
            {
                "field": label,
                "change_type": change_type,
                "before": _summary(before.get(key)),
                "after": _summary(after.get(key)),
            }
        )
    return {
        "has_changes": bool(changed),
        "changed_fields": changed,
        "unchanged_fields": unchanged,
        "meaningful_change_count": len(changed),
    }
