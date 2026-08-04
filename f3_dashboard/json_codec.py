"""Canonical JSON, hashing, and fixed Python-literal generation."""

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


def upstream_config_hash(value: Any) -> str:
    return hashlib.sha256(
        canonical_json_bytes(value, ensure_ascii=True)
    ).hexdigest()[:16]


def serialized_size(value: Any, *, ensure_ascii: bool = False) -> int:
    return len(canonical_json_bytes(value, ensure_ascii=ensure_ascii))


def clone_json(value: Any) -> Any:
    validate_json_value(value)
    return deepcopy(value)


def python_literal(value: Any) -> str:
    """Return a deterministic Python literal for already-validated JSON data."""

    validate_json_value(value)
    if value is None:
        return "None"
    if value is True:
        return "True"
    if value is False:
        return "False"
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ",".join(python_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            f"{python_literal(key)}:{python_literal(value[key])}"
            for key in sorted(value)
        ) + "}"
    raise PatchValidationError("Unsupported generated-transform value")
