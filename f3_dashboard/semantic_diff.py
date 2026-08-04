"""Bounded reviewer-facing semantic diff over inert dashboard data."""

from __future__ import annotations

from typing import Any

from .constants import (
    MAX_DIFF_PREVIEW_CHARS,
    MAX_SEMANTIC_DIFF_BYTES,
    SEMANTIC_DIFF_MODEL,
)
from .errors import SemanticDiffError
from .json_codec import canonical_json_bytes, engineering_sha256
from .models import (
    DashboardRiskEvidence,
    PatchCompilation,
    RiskFinding,
    SemanticDiff,
    SemanticDiffEntry,
    ValueSummary,
)


_SENSITIVE_FIELD_FRAGMENTS = (
    "token",
    "password",
    "secret",
    "authorization",
    "api_key",
    "access_key",
    "credential",
)


def _sensitive_path(path: str) -> bool:
    lowered = path.lower()
    return any(fragment in lowered for fragment in _SENSITIVE_FIELD_FRAGMENTS)


def _looks_secret(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("bearer ") or lowered.startswith("basic ")


def summarize_value(*, present: bool, value: Any, path: str) -> ValueSummary:
    if not present:
        return ValueSummary("untrusted_data", "missing", False, False, False, None)
    if value is None:
        return ValueSummary("untrusted_data", "null", True, False, False, "null")
    if isinstance(value, bool):
        return ValueSummary("untrusted_data", "boolean", True, False, False, str(value).lower())
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return ValueSummary("untrusted_data", "number", True, False, False, repr(value))
    if isinstance(value, str):
        if _sensitive_path(path) or _looks_secret(value):
            return ValueSummary("untrusted_data", "string", True, True, False, "<redacted>")
        truncated = len(value) > MAX_DIFF_PREVIEW_CHARS
        preview = value[:MAX_DIFF_PREVIEW_CHARS]
        return ValueSummary("untrusted_data", "string", True, False, truncated, preview)
    if isinstance(value, list):
        return ValueSummary(
            "untrusted_data",
            "array",
            True,
            False,
            bool(value),
            "<collection preview omitted>" if value else "[]",
            len(value),
        )
    if isinstance(value, dict):
        keys = sorted(value)
        shown = keys[:8]
        truncated = len(keys) > len(shown)
        preview = "{" + ",".join(shown) + (",…" if truncated else "") + "}"
        if len(preview) > MAX_DIFF_PREVIEW_CHARS:
            preview = preview[:MAX_DIFF_PREVIEW_CHARS]
            truncated = True
        return ValueSummary(
            "untrusted_data", "object", True, False, truncated, preview, len(value)
        )
    raise SemanticDiffError("Semantic diff encountered unsupported data")


def _context(path: str) -> tuple[str, ...]:
    tokens = path[1:].split("/") if path.startswith("/") else []
    context: list[str] = []
    for index, token in enumerate(tokens[:-1]):
        if token in {"views", "sections", "cards", "badges", "features"}:
            next_token = tokens[index + 1]
            if next_token.isdigit():
                context.append(f"{token[:-1] if token.endswith('s') else token}:{next_token}")
    return tuple(context[:6])


def _overlaps(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _risk_flags(path: str, findings: tuple[RiskFinding, ...]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                finding.category.value
                for finding in findings
                if finding.introduced_or_changed and _overlaps(path, finding.path)
            }
        )
    )


def _summary_projection(summary: ValueSummary) -> dict[str, Any]:
    return {
        "data_role": summary.data_role,
        "value_type": summary.value_type,
        "present": summary.present,
        "redacted": summary.redacted,
        "truncated": summary.truncated,
        "preview": summary.preview,
        "item_count": summary.item_count,
    }


def build_semantic_diff(
    compilation: PatchCompilation,
    risk: DashboardRiskEvidence,
) -> SemanticDiff:
    """Build one unambiguous bounded entry for every approved operation."""

    entries: list[SemanticDiffEntry] = []
    for effect in compilation.effects:
        entries.append(
            SemanticDiffEntry(
                operation_id=effect.operation_id,
                path=effect.path,
                operation=effect.operation.value,
                previous=summarize_value(
                    present=effect.previous_present,
                    value=effect.previous_value,
                    path=effect.path,
                ),
                proposed=summarize_value(
                    present=effect.proposed_present,
                    value=effect.proposed_value,
                    path=effect.path,
                ),
                context=_context(effect.path),
                leaf_change_count=effect.leaf_change_count,
                risk_flags=_risk_flags(effect.path, risk.findings),
            )
        )
    if len(entries) != len(compilation.operations):
        raise SemanticDiffError("Semantic diff cannot represent every operation")
    projection = [
        {
            "operation_id": entry.operation_id,
            "path": entry.path,
            "operation": entry.operation,
            "previous": _summary_projection(entry.previous),
            "proposed": _summary_projection(entry.proposed),
            "context": list(entry.context),
            "leaf_change_count": entry.leaf_change_count,
            "risk_flags": list(entry.risk_flags),
        }
        for entry in entries
    ]
    hash_projection = {
        "model": SEMANTIC_DIFF_MODEL,
        "entries": projection,
        "leaf_change_count": compilation.semantic_leaf_change_count,
        "preread_sha256": compilation.preread_sha256,
        "patch_sha256": compilation.canonical_patch_sha256,
        "resulting_sha256": compilation.resulting_sha256,
    }
    encoded = canonical_json_bytes(hash_projection)
    if len(encoded) > MAX_SEMANTIC_DIFF_BYTES:
        raise SemanticDiffError("Semantic diff exceeds the complete review bound")
    return SemanticDiff(
        model=SEMANTIC_DIFF_MODEL,
        entries=tuple(entries),
        leaf_change_count=compilation.semantic_leaf_change_count,
        truncated=any(
            entry.previous.truncated or entry.proposed.truncated for entry in entries
        ),
        preread_sha256=compilation.preread_sha256,
        patch_sha256=compilation.canonical_patch_sha256,
        resulting_sha256=compilation.resulting_sha256,
        semantic_diff_sha256=engineering_sha256(hash_projection),
        serialized_size_bytes=len(encoded),
    )


def semantic_diff_projection(diff: SemanticDiff) -> dict[str, Any]:
    return {
        "model": diff.model,
        "entries": [
            {
                "operation_id": entry.operation_id,
                "path": entry.path,
                "operation": entry.operation,
                "previous": _summary_projection(entry.previous),
                "proposed": _summary_projection(entry.proposed),
                "context": list(entry.context),
                "leaf_change_count": entry.leaf_change_count,
                "risk_flags": list(entry.risk_flags),
            }
            for entry in diff.entries
        ],
        "leaf_change_count": diff.leaf_change_count,
        "truncated": diff.truncated,
        "preread_sha256": diff.preread_sha256,
        "patch_sha256": diff.patch_sha256,
        "resulting_sha256": diff.resulting_sha256,
        "semantic_diff_sha256": diff.semantic_diff_sha256,
        "serialized_size_bytes": diff.serialized_size_bytes,
    }

