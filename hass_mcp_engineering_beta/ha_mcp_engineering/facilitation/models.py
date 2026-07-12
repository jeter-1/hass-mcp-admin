"""Bounded, token-efficient contracts for future analytical responses."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable


class DetailLevel(str, Enum):
    SUMMARY = "summary"
    STANDARD = "standard"
    EVIDENCE = "evidence"


@dataclass(frozen=True)
class EvidenceReference:
    """Stable reference to bounded evidence; never embeds raw bulk evidence."""

    reference_id: str
    provider_id: str
    evidence_type: str
    summary: str

    def as_dict(self) -> dict[str, str]:
        return {
            "reference_id": _safe_text(self.reference_id, 128),
            "provider_id": _safe_text(self.provider_id, 64),
            "evidence_type": _safe_text(self.evidence_type, 64),
            "summary": _safe_text(self.summary, 256),
        }


@dataclass(frozen=True)
class SourceCoverage:
    requested: tuple[str, ...]
    completed: tuple[str, ...]
    unavailable: tuple[str, ...] = ()
    partial: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.unavailable and not self.partial and set(self.completed) == set(self.requested)

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "complete": self.complete}


@dataclass(frozen=True)
class PaginationMetadata:
    offset: int
    limit: int
    returned: int
    total: int
    has_more: bool
    next_offset: int | None


@dataclass
class BoundedResult:
    summary: str
    items: list[Any]
    pagination: PaginationMetadata
    detail_level: DetailLevel = DetailLevel.STANDARD
    evidence: list[EvidenceReference] = field(default_factory=list)
    coverage: SourceCoverage | None = None
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["detail_level"] = self.detail_level.value
        if self.coverage:
            value["coverage"] = self.coverage.as_dict()
        if self.detail_level == DetailLevel.SUMMARY:
            value["items"] = []
            value["evidence"] = []
        return value


def bounded_result(
    values: Iterable[Any],
    *,
    summary: str,
    limit: int = 25,
    offset: int = 0,
    detail_level: DetailLevel = DetailLevel.STANDARD,
    identity: Callable[[Any], str] | None = None,
    evidence: Iterable[EvidenceReference] = (),
    evidence_limit: int = 25,
    coverage: SourceCoverage | None = None,
) -> BoundedResult:
    """Deduplicate, paginate, and report truncation without raw registry dumps."""

    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    identity = identity or (lambda item: repr(item))
    unique: list[Any] = []
    seen: set[str] = set()
    for item in values:
        key = identity(item)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    raw_page = unique[offset : offset + limit]
    bounded_items = [_bound_value(item) for item in raw_page]
    page = [item[0] for item in bounded_items]
    content_truncated = any(item[1] for item in bounded_items)
    has_more = offset + len(page) < len(unique)
    evidence_values = list(evidence)
    references = []
    reference_ids: set[str] = set()
    for reference in evidence_values:
        if reference.reference_id not in reference_ids:
            reference_ids.add(reference.reference_id)
            references.append(reference)
        if len(references) >= max(1, min(evidence_limit, 100)):
            break
    evidence_truncated = len(reference_ids) < len({item.reference_id for item in evidence_values})
    truncated = has_more or evidence_truncated or content_truncated
    warnings = ["Results were truncated; use pagination or evidence drill-down."] if truncated else []
    return BoundedResult(
        summary=summary,
        items=page,
        pagination=PaginationMetadata(
            offset=offset,
            limit=limit,
            returned=len(page),
            total=len(unique),
            has_more=has_more,
            next_offset=offset + len(page) if has_more else None,
        ),
        detail_level=detail_level,
        evidence=references if detail_level == DetailLevel.EVIDENCE else [],
        coverage=coverage,
        warnings=warnings,
        truncated=truncated,
    )


def _safe_text(value: str, limit: int) -> str:
    normalized = str(value)
    if "/mcp" in normalized.lower() or "bearer " in normalized.lower():
        return "<redacted>"
    return normalized if len(normalized) <= limit else normalized[:limit] + "...<bounded>"


def _bound_value(value: Any, *, depth: int = 0) -> tuple[Any, bool]:
    if depth > 4:
        return "<bounded>", True
    if isinstance(value, str):
        bounded = _safe_text(value, 2_000)
        return bounded, bounded != value
    if isinstance(value, dict):
        items = list(value.items())
        bounded = {}
        nested_truncated = False
        for key, item in items[:50]:
            normalized = str(key).lower()
            if any(term in normalized for term in ("secret", "token", "authorization", "cookie", "password", "api_key")):
                bounded[str(key)] = "<redacted>"
                nested_truncated = True
                continue
            bounded_item, item_truncated = _bound_value(item, depth=depth + 1)
            bounded[str(key)] = bounded_item
            nested_truncated = nested_truncated or item_truncated
        return bounded, len(items) > 50 or nested_truncated
    if isinstance(value, (list, tuple)):
        items = list(value)
        bounded_items = [_bound_value(item, depth=depth + 1) for item in items[:100]]
        return [item[0] for item in bounded_items], len(items) > 100 or any(item[1] for item in bounded_items)
    return value, False
