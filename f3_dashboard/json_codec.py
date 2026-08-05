"""Canonical JSON validation, cloning, sizing, and hashing."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any

from .constants import MAX_JSON_DEPTH, MAX_JSON_NODES
from .errors import PatchValidationError


JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


def validate_json_value(
    value: Any,
    *,
    max_depth: int = MAX_JSON_DEPTH,
    max_nodes: int = MAX_JSON_NODES,
) -> None:
    """Reject non-JSON, executable, non-finite, or unbounded values."""

    remaining = max_nodes
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        remaining -= 1
        if remaining < 0:
            raise PatchValidationError("JSON node limit exceeded")
        if depth > max_depth:
            raise PatchValidationError("JSON depth limit exceeded")
        if current is None or isinstance(current, (str, bool)):
            continue
        if isinstance(current, int) and not isinstance(current, bool):
            continue
        if isinstance(current, float):
            if not math.isfinite(current):
                raise PatchValidationError("Non-finite numbers are prohibited")
            continue
        if isinstance(current, list):
            stack.extend((item, depth + 1) for item in reversed(current))
            continue
        if isinstance(current, dict):
            for key, item in reversed(tuple(current.items())):
                if not isinstance(key, str):
                    raise PatchValidationError("JSON object keys must be strings")
                stack.append((item, depth + 1))
            continue
        if callable(current):
            raise PatchValidationError("Executable or callable values are prohibited")
        raise PatchValidationError(
            f"Unsupported JSON value type: {type(current).__name__}"
        )


def canonical_json_bytes(value: Any, *, ensure_ascii: bool = False) -> bytes:
    validate_json_value(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=ensure_ascii,
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_text(value: Any, *, ensure_ascii: bool = False) -> str:
    return canonical_json_bytes(value, ensure_ascii=ensure_ascii).decode("utf-8")


def engineering_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def strict_json_equal(
    left: Any,
    right: Any,
    *,
    max_depth: int = MAX_JSON_DEPTH,
    max_nodes: int = MAX_JSON_NODES,
) -> bool:
    """Compare JSON recursively by exact JSON/Python type and value."""

    validate_json_value(left, max_depth=max_depth, max_nodes=max_nodes)
    validate_json_value(right, max_depth=max_depth, max_nodes=max_nodes)
    remaining = max_nodes
    stack: list[tuple[Any, Any, int]] = [(left, right, 0)]
    while stack:
        current_left, current_right, depth = stack.pop()
        remaining -= 1
        if remaining < 0:
            raise PatchValidationError("JSON equality node limit exceeded")
        if depth > max_depth:
            raise PatchValidationError("JSON equality depth limit exceeded")
        if type(current_left) is not type(current_right):
            return False
        if isinstance(current_left, dict):
            if current_left.keys() != current_right.keys():
                return False
            stack.extend(
                (current_left[key], current_right[key], depth + 1)
                for key in reversed(tuple(current_left))
            )
            continue
        if isinstance(current_left, list):
            if len(current_left) != len(current_right):
                return False
            stack.extend(
                (left_item, right_item, depth + 1)
                for left_item, right_item in reversed(
                    tuple(zip(current_left, current_right, strict=True))
                )
            )
            continue
        if current_left != current_right:
            return False
    return True


def upstream_config_hash(value: Any) -> str:
    return hashlib.sha256(
        canonical_json_bytes(value, ensure_ascii=True)
    ).hexdigest()[:16]


def serialized_size(value: Any, *, ensure_ascii: bool = False) -> int:
    return len(canonical_json_bytes(value, ensure_ascii=ensure_ascii))


def clone_json(value: Any) -> Any:
    validate_json_value(value)
    return deepcopy(value)
