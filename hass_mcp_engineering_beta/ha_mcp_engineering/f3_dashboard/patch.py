"""Bounded RFC 6901 patch compiler for one exact dashboard configuration."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any, Iterable, Mapping

from .constants import (
    CANONICAL_OPERATION_ID,
    MAX_CONFIG_GROWTH_BYTES,
    MAX_INDIVIDUAL_VALUE_BYTES,
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    MAX_PATCH_BYTES,
    MAX_PATCH_OPERATIONS,
    MAX_POINTER_CHARS,
    MAX_POINTER_DEPTH,
    MAX_RESULT_CONFIG_BYTES,
    MAX_SEMANTIC_LEAF_CHANGES,
    MIN_PATCH_OPERATIONS,
    PATCH_MODEL,
)
from .errors import PatchCompilationError, PatchValidationError
from .json_codec import (
    canonical_json_bytes,
    clone_json,
    engineering_sha256,
    serialized_size,
    strict_json_equal,
    upstream_config_hash,
    validate_json_value,
)
from .models import PatchCompilation, PatchEffect, PatchKind, PatchOperation


_OPERATION_KEYS = frozenset({"operation_id", "operation", "path", "value"})
_FUZZY_TOKENS = frozenset({"*", "**", ".", ".."})


def _decode_token(token: str) -> str:
    decoded: list[str] = []
    index = 0
    while index < len(token):
        char = token[index]
        if char != "~":
            decoded.append(char)
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise PatchValidationError("Malformed RFC 6901 escape sequence")
        decoded.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(decoded)


def _encode_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def parse_pointer(
    path: str, *, operation: PatchKind | str | None = None
) -> tuple[str, ...]:
    """Parse one canonical pointer with only RFC 6902 final append syntax."""

    if not isinstance(path, str) or not path or not path.startswith("/"):
        raise PatchValidationError("Patch paths must be non-root RFC 6901 pointers")
    if len(path) > MAX_POINTER_CHARS:
        raise PatchValidationError("Patch pointer exceeds the reviewed length bound")
    encoded_tokens = path[1:].split("/")
    if len(encoded_tokens) > MAX_POINTER_DEPTH:
        raise PatchValidationError("Patch pointer exceeds the reviewed depth bound")
    tokens = tuple(_decode_token(token) for token in encoded_tokens)
    if any(not token for token in tokens):
        raise PatchValidationError("Empty pointer tokens are prohibited")
    append_allowed = operation in {PatchKind.ADD, PatchKind.ADD.value}
    for index, token in enumerate(tokens):
        if token == "-":
            if append_allowed and index == len(tokens) - 1:
                continue
            raise PatchValidationError(
                "The array append token is valid only as the final token of add"
            )
        if (
            token in _FUZZY_TOKENS
            or "*" in token
            or "?" in token
            or "[" in token
            or "]" in token
            or "{" in token
            or "}" in token
            or token.startswith("$")
        ):
            raise PatchValidationError("Wildcard, predicate, or fuzzy selectors are prohibited")
    canonical = "/" + "/".join(_encode_token(token) for token in tokens)
    if canonical != path:
        raise PatchValidationError("Patch pointer is not in canonical form")
    return tokens


def _canonical_operation(raw: Mapping[str, Any]) -> PatchOperation:
    if not isinstance(raw, Mapping) or set(raw) - _OPERATION_KEYS:
        raise PatchValidationError("Patch operations contain unknown fields")
    if set(raw) < {"operation_id", "operation", "path"}:
        raise PatchValidationError("Patch operation fields are incomplete")
    operation_id = raw["operation_id"]
    if not isinstance(operation_id, str) or not CANONICAL_OPERATION_ID.fullmatch(
        operation_id
    ):
        raise PatchValidationError("Patch operation ID is not canonical")
    try:
        operation = PatchKind(raw["operation"])
    except (TypeError, ValueError) as exc:
        raise PatchValidationError("Only add, replace, and remove are supported") from exc
    path = raw["path"]
    tokens = parse_pointer(path, operation=operation)
    value_present = "value" in raw
    if operation is PatchKind.REMOVE and value_present:
        raise PatchValidationError("Remove operations must not include a value")
    if operation is not PatchKind.REMOVE and not value_present:
        raise PatchValidationError("Add and replace operations require a value")
    value = raw.get("value")
    if value_present:
        validate_json_value(value)
        if serialized_size(value) > MAX_INDIVIDUAL_VALUE_BYTES:
            raise PatchValidationError("Patch value exceeds the reviewed bound")
        value = clone_json(value)
    return PatchOperation(
        operation_id=operation_id,
        operation=operation,
        path=path,
        tokens=tokens,
        value_present=value_present,
        value=value,
    )


def canonicalize_patch(
    operations: Iterable[Mapping[str, Any]],
) -> tuple[PatchOperation, ...]:
    if isinstance(operations, (str, bytes, Mapping)):
        raise PatchValidationError("Patch operations must be an ordered list")
    raw_operations = list(operations)
    if not MIN_PATCH_OPERATIONS <= len(raw_operations) <= MAX_PATCH_OPERATIONS:
        raise PatchValidationError("Patch must contain between 1 and 16 operations")
    normalized = tuple(_canonical_operation(raw) for raw in raw_operations)
    ids = [item.operation_id for item in normalized]
    if len(ids) != len(set(ids)):
        raise PatchValidationError("Patch operation IDs must be unique")
    paths = [item.tokens for item in normalized]
    if len(paths) != len(set(paths)):
        raise PatchValidationError("Duplicate canonical patch paths are prohibited")
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            shortest = min(len(left), len(right))
            if left[:shortest] == right[:shortest]:
                raise PatchValidationError("Parent/child patch path conflicts are prohibited")
    projection = patch_projection(normalized)
    if serialized_size(projection) > MAX_PATCH_BYTES:
        raise PatchValidationError("Canonical patch exceeds the reviewed byte bound")
    return normalized


def patch_projection(operations: Iterable[PatchOperation]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for operation in operations:
        row: dict[str, Any] = {
            "operation_id": operation.operation_id,
            "operation": operation.operation.value,
            "path": operation.path,
        }
        if operation.value_present:
            row["value"] = operation.value
        projected.append(row)
    return projected


def _list_index(token: str, *, length: int, allow_end: bool = False) -> int:
    if token.startswith("-"):
        raise PatchCompilationError("Negative list indices are prohibited")
    if not token.isdigit():
        raise PatchCompilationError("List paths require canonical numeric indices")
    if len(token) > 1 and token.startswith("0"):
        raise PatchCompilationError("Leading-zero list indices are prohibited")
    index = int(token)
    if index > length or (index == length and not allow_end):
        raise PatchCompilationError("List index is outside the allowed collection boundary")
    return index


def _resolve_parent(
    document: dict[str, Any], tokens: tuple[str, ...], operation: PatchKind
) -> tuple[Any, str | int]:
    current: Any = document
    for token in tokens[:-1]:
        if isinstance(current, dict):
            if token not in current:
                raise PatchCompilationError("Patch parent path does not exist")
            current = current[token]
        elif isinstance(current, list):
            index = _list_index(token, length=len(current))
            current = current[index]
        else:
            raise PatchCompilationError("Patch parent is not a collection")
    final_token = tokens[-1]
    if isinstance(current, dict):
        if final_token == "-":
            raise PatchCompilationError("The append token requires a list parent")
        return current, final_token
    if isinstance(current, list):
        if final_token == "-":
            if operation is not PatchKind.ADD:
                raise PatchCompilationError("The append token is supported only for add")
            return current, len(current)
        index = _list_index(
            final_token,
            length=len(current),
            allow_end=operation is PatchKind.ADD,
        )
        return current, index
    raise PatchCompilationError("Patch target parent is not a collection")


def _leaf_weight(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> int:
    if budget is None:
        budget = [MAX_JSON_NODES]
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_JSON_DEPTH:
        raise PatchCompilationError("Semantic leaf traversal exceeds its bound")
    if isinstance(value, dict):
        if not value:
            return 1
        return sum(_leaf_weight(item, depth=depth + 1, budget=budget) for item in value.values())
    if isinstance(value, list):
        if not value:
            return 1
        return sum(_leaf_weight(item, depth=depth + 1, budget=budget) for item in value)
    return 1


def semantic_leaf_difference(
    before: Any,
    after: Any,
    *,
    depth: int = 0,
    budget: list[int] | None = None,
) -> int:
    """Count deterministic semantic leaf changes for a replacement."""

    if budget is None:
        budget = [MAX_JSON_NODES]
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_JSON_DEPTH:
        raise PatchCompilationError("Semantic difference traversal exceeds its bound")
    if strict_json_equal(before, after):
        return 0
    if isinstance(before, dict) and isinstance(after, dict):
        count = 0
        for key in sorted(set(before) | set(after)):
            if key not in before:
                count += _leaf_weight(after[key], depth=depth + 1, budget=budget)
            elif key not in after:
                count += _leaf_weight(before[key], depth=depth + 1, budget=budget)
            else:
                count += semantic_leaf_difference(
                    before[key], after[key], depth=depth + 1, budget=budget
                )
        return count
    if isinstance(before, list) and isinstance(after, list):
        count = 1 if len(before) != len(after) else 0
        for index in range(min(len(before), len(after))):
            count += semantic_leaf_difference(
                before[index], after[index], depth=depth + 1, budget=budget
            )
        for item in before[len(after) :]:
            count += _leaf_weight(item, depth=depth + 1, budget=budget)
        for item in after[len(before) :]:
            count += _leaf_weight(item, depth=depth + 1, budget=budget)
        return count
    return max(_leaf_weight(before, budget=budget), _leaf_weight(after, budget=budget))


def _apply_one(
    document: dict[str, Any], operation: PatchOperation
) -> PatchEffect:
    parent, target = _resolve_parent(
        document, operation.tokens, operation.operation
    )
    is_mapping = isinstance(parent, dict)
    if operation.operation is PatchKind.ADD:
        proposed = clone_json(operation.value)
        if is_mapping:
            if target in parent:
                raise PatchCompilationError("Add requires an absent mapping member")
            parent[target] = proposed
            leaf_count = _leaf_weight(proposed)
            previous_present = False
            previous_value = None
            proposed_value = deepcopy(proposed)
        elif isinstance(parent, list) and isinstance(target, int):
            displaced = deepcopy(parent[target:])
            parent.insert(target, proposed)
            leaf_count = _leaf_weight(proposed) + 1
            previous_present = bool(displaced)
            previous_value = displaced if displaced else None
            proposed_value = (
                deepcopy(parent[target:]) if displaced else deepcopy(proposed)
            )
        else:
            raise PatchCompilationError("Array add requires a list parent")
        return PatchEffect(
            operation.operation_id,
            operation.operation,
            operation.path,
            previous_present,
            previous_value,
            True,
            proposed_value,
            leaf_count,
        )

    present = target in parent if is_mapping else True
    previous = deepcopy(parent[target]) if present else None
    if not present:
        raise PatchCompilationError("Replace and remove require an existing target")
    if operation.operation is PatchKind.REPLACE:
        proposed = clone_json(operation.value)
        leaf_count = semantic_leaf_difference(previous, proposed)
        parent[target] = proposed
        return PatchEffect(
            operation.operation_id,
            operation.operation,
            operation.path,
            True,
            previous,
            True,
            deepcopy(proposed),
            leaf_count,
        )

    leaf_count = _leaf_weight(previous) + (1 if isinstance(parent, list) else 0)
    del parent[target]
    return PatchEffect(
        operation.operation_id,
        operation.operation,
        operation.path,
        True,
        previous,
        False,
        None,
        leaf_count,
    )


def compile_dashboard_patch(
    configuration: dict[str, Any],
    operations: Iterable[Mapping[str, Any]],
) -> PatchCompilation:
    """Compile a bounded patch without mutating the caller's configuration."""

    if not isinstance(configuration, dict):
        raise PatchCompilationError("Dashboard configuration must be an object")
    validate_json_value(configuration)
    normalized = canonicalize_patch(operations)
    original = clone_json(configuration)
    result = clone_json(configuration)
    effects: list[PatchEffect] = []
    for operation in normalized:
        effects.append(_apply_one(result, operation))

    if not strict_json_equal(configuration, original):
        raise PatchCompilationError("Patch compilation mutated its input")
    leaf_count = sum(effect.leaf_change_count for effect in effects)
    if leaf_count > MAX_SEMANTIC_LEAF_CHANGES:
        raise PatchCompilationError("Patch exceeds the 16-leaf semantic review bound")
    original_size = serialized_size(original, ensure_ascii=True)
    result_size = serialized_size(result, ensure_ascii=True)
    growth = result_size - original_size
    if result_size > MAX_RESULT_CONFIG_BYTES:
        raise PatchCompilationError("Resulting dashboard exceeds the reviewed size bound")
    if growth > MAX_CONFIG_GROWTH_BYTES:
        raise PatchCompilationError("Dashboard growth exceeds the reviewed bound")

    projection = patch_projection(normalized)
    patch_bytes = canonical_json_bytes(projection)
    return PatchCompilation(
        model=PATCH_MODEL,
        operations=normalized,
        effects=tuple(effects),
        resulting_configuration=result,
        preread_sha256=engineering_sha256(original),
        canonical_patch_sha256=hashlib.sha256(patch_bytes).hexdigest(),
        resulting_sha256=engineering_sha256(result),
        resulting_upstream_config_hash=upstream_config_hash(result),
        serialized_patch_bytes=len(patch_bytes),
        resulting_size_bytes=result_size,
        configuration_growth_bytes=growth,
        semantic_leaf_change_count=leaf_count,
    )


def mismatch_paths(
    expected: Any,
    observed: Any,
    *,
    limit: int,
) -> tuple[str, ...]:
    """Return bounded canonical paths without exposing mismatched values."""

    paths: list[str] = []
    stack: list[tuple[Any, Any, tuple[str, ...]]] = [(expected, observed, ())]
    visited = 0
    while stack and len(paths) < limit:
        left, right, tokens = stack.pop()
        visited += 1
        if visited > MAX_JSON_NODES:
            paths.append("/<traversal-bound>")
            break
        if strict_json_equal(left, right):
            continue
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right), reverse=True):
                if key not in left or key not in right:
                    paths.append("/" + "/".join(_encode_token(item) for item in (*tokens, key)))
                    if len(paths) >= limit:
                        break
                else:
                    stack.append((left[key], right[key], (*tokens, key)))
            continue
        if isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                paths.append("/" + "/".join(_encode_token(item) for item in tokens) or "/")
                if len(paths) >= limit:
                    break
            for index in range(min(len(left), len(right)) - 1, -1, -1):
                stack.append((left[index], right[index], (*tokens, str(index))))
            continue
        paths.append("/" + "/".join(_encode_token(item) for item in tokens) or "/")
    return tuple(paths)
