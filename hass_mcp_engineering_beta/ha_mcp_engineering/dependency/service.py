"""Entity dependency analysis orchestration, assessment, and pagination."""

from __future__ import annotations

from dataclasses import dataclass, replace
import base64
import hashlib
import json
from typing import Any

from ..errors import EngineeringServerError, ErrorCode, GovernanceError, InvalidRequestError
from ..observability import METRICS
from .extraction import valid_entity_id
from .index import DependencyIndex
from .models import DependencyFinding, SOURCE_TYPES, SourceCoverageItem


@dataclass
class AnalysisOutput:
    data: dict[str, Any]
    warnings: list[str]
    metadata: dict[str, Any]
    partial: bool


class EntityDependencyAnalysisService:
    def __init__(self, index: DependencyIndex):
        self.index = index

    async def analyze(
        self,
        *,
        entity_id: str,
        detail_level: str = "summary",
        include_indirect: bool = False,
        max_depth: int = 2,
        source_types: list[str] | None = None,
        limit: int = 50,
        cursor: str = "",
        refresh_index: bool = False,
    ) -> AnalysisOutput:
        METRICS.record_dependency_analysis_request()
        target = entity_id.strip().lower()
        if not valid_entity_id(target):
            METRICS.record_dependency_analysis_failure()
            raise InvalidRequestError(details={"operation": "entity_dependency_analysis"})
        if detail_level not in {"summary", "standard", "evidence"}:
            METRICS.record_dependency_analysis_failure()
            raise InvalidRequestError(details={"operation": "entity_dependency_analysis"})
        try:
            max_depth = int(max_depth)
            limit = int(limit)
        except (TypeError, ValueError) as exc:
            METRICS.record_dependency_analysis_failure()
            raise InvalidRequestError(details={"operation": "entity_dependency_analysis"}) from exc
        if not 1 <= max_depth <= 3 or not 1 <= limit <= 100:
            METRICS.record_dependency_analysis_failure()
            raise InvalidRequestError(details={"operation": "entity_dependency_analysis"})
        requested = list(dict.fromkeys(source_types or SOURCE_TYPES))
        if any(item not in SOURCE_TYPES for item in requested):
            METRICS.record_dependency_analysis_failure()
            raise InvalidRequestError(details={"operation": "entity_dependency_analysis"})

        try:
            snapshot, rebuilt = await self.index.get(refresh=refresh_index)
        except EngineeringServerError:
            METRICS.record_dependency_analysis_failure()
            raise
        except Exception as exc:
            METRICS.record_dependency_analysis_failure()
            raise GovernanceError(ErrorCode.ANALYSIS_UNAVAILABLE) from exc

        direct = [item for item in snapshot.findings if item.target_entity_id == target and _source_selected(item, requested)]
        findings = list(direct)
        if include_indirect:
            findings.extend(_indirect(snapshot.findings, target, requested, max_depth))
        findings = sorted({item.evidence_id: item for item in findings}.values(), key=_sort_key)

        query_fingerprint = _query_fingerprint(target, detail_level, include_indirect, max_depth, requested)
        offset = _decode_cursor(cursor, snapshot.fingerprint, query_fingerprint) if cursor else 0
        effective_limit = min(limit, 10) if detail_level == "summary" else limit
        page = findings[offset : offset + effective_limit]
        next_offset = offset + len(page)
        has_more = next_offset < len(findings)
        next_cursor = _encode_cursor(snapshot.fingerprint, query_fingerprint, next_offset) if has_more else None
        if has_more:
            METRICS.record_dependency_truncation()

        metadata = dict(snapshot.target_metadata.get(target, {}))
        metadata.setdefault("entity_id", target)
        metadata.setdefault("entity_exists", False)
        metadata.setdefault("registry_entry_exists", False)
        metadata.setdefault("domain", target.split(".", 1)[0])
        metadata = _safe_target_metadata(metadata)

        coverage = _coverage_for(snapshot.coverage, requested, findings)
        partial = any(item.completeness in {"partial", "unavailable", "unsupported"} for item in coverage if item.completeness != "not_requested")
        dynamic = [item for item in snapshot.dynamic_references if item.source_type in requested][:10]
        if dynamic:
            METRICS.record_dependency_unresolved(len(dynamic))
        warnings = [warning for item in coverage for warning in item.warnings]
        warnings.extend(item.warning + f" Source: {item.source_type}/{item.source_id}, path {item.config_path}." for item in dynamic)
        if has_more:
            warnings.append("Findings were truncated; continue with the returned cursor.")
        possible_stale = not metadata["entity_exists"] and bool(direct)
        if possible_stale:
            warnings.append("The target entity is missing but configuration references remain; this may be stale configuration.")

        assessment_status, reason = _assessment(bool(direct), any(not item.direct for item in findings), partial, possible_stale)
        data = {
            "target": metadata,
            "overview": {
                "dependency_status": "referenced" if findings else "not_detected",
                "direct_reference_count": len(direct),
                "indirect_reference_count": sum(not item.direct for item in findings),
                "unique_source_count": len({(item.source_type, item.source_id) for item in findings}),
                "unresolved_dynamic_reference_count": len(dynamic),
                "coverage_complete": not partial,
                "possible_stale_reference": possible_stale,
            },
            "assessment": {"rename_or_removal_status": assessment_status, "reason": reason},
            "findings": [item.public(include_excerpt=detail_level == "evidence") for item in page],
            "source_coverage": [item.public() for item in coverage],
            "pagination": {
                "limit": effective_limit,
                "returned": len(page),
                "total": len(findings),
                "has_more": has_more,
                "next_cursor": next_cursor,
            },
            "index": {
                "fingerprint": snapshot.fingerprint[:16],
                "generation": snapshot.generation,
                "built_at": snapshot.built_at,
                "cache_hit": not rebuilt,
                "refreshed": bool(refresh_index and rebuilt),
            },
        }
        METRICS.record_dependency_analysis_success()
        if partial:
            METRICS.record_dependency_analysis_partial()
        return AnalysisOutput(data, list(dict.fromkeys(warnings))[:20], {"detail_level": detail_level, "partial": partial}, partial)


def _source_selected(item: DependencyFinding, requested: list[str]) -> bool:
    if item.relation.startswith("blueprint"):
        return "blueprint" in requested or "automation" in requested
    return item.source_type in requested


def _indirect(all_findings, target: str, requested: list[str], max_depth: int) -> list[DependencyFinding]:
    results = []
    frontier = [(target, 0, tuple())]
    visited = {target}
    while frontier and len(results) < 500:
        current, depth, path = frontier.pop(0)
        if depth >= max_depth:
            continue
        memberships = [
            item for item in all_findings
            if item.target_entity_id == current and item.source_type in {"group", "template"} and item.source_entity_id
        ]
        for membership in sorted(memberships, key=_sort_key):
            intermediate = membership.source_entity_id.lower()
            if intermediate in visited:
                continue
            visited.add(intermediate)
            inbound = [item for item in all_findings if item.target_entity_id == intermediate and _source_selected(item, requested)]
            for item in inbound:
                chain = (*path, membership.evidence_id, item.evidence_id)
                results.append(
                    replace(
                        item,
                        evidence_id="ind_" + hashlib.sha256("|".join(chain).encode()).hexdigest()[:24],
                        direct=False,
                        depth=depth + 2,
                        confidence="exact_static_chain",
                        evidence_path=chain,
                        evidence_summary=f"Static dependency through {intermediate}.",
                    )
                )
            frontier.append((intermediate, depth + 1, (*path, membership.evidence_id)))
    if len(results) >= 500:
        METRICS.record_dependency_truncation()
    return results[:500]


def _coverage_for(items, requested, findings):
    output = []
    for item in items:
        copy = replace(item)
        if item.source_type in SOURCE_TYPES and item.source_type not in requested:
            copy.completeness = "not_requested"
            copy.evidence_count = 0
            copy.failed_item_count = 0
            copy.warnings = []
        else:
            copy.evidence_count = sum(
                (finding.source_type == item.source_type)
                or (item.source_type == "blueprint" and finding.relation.startswith("blueprint"))
                for finding in findings
            )
        output.append(copy)
    return output


def _sort_key(item: DependencyFinding):
    relation_priority = {
        "trigger": 0, "condition": 1, "choose_condition": 1, "if_condition": 1,
        "repeat_condition": 1, "wait_for_trigger": 1, "service_target": 2,
        "action_target": 2, "action_data": 3, "template_literal": 4,
    }
    return (
        not item.direct,
        item.match_type not in {"structured_exact", "blueprint_resolved"},
        relation_priority.get(item.relation, 5),
        item.source_type,
        item.source_name or "",
        item.config_path,
        item.evidence_id,
    )


def _assessment(direct: bool, indirect: bool, partial: bool, stale: bool):
    if direct:
        return "not_safe", "Direct references were found." + (" The missing entity may have stale references." if stale else "")
    if indirect:
        return "references_found", "Explicit indirect references were found."
    if partial:
        return "unknown_due_to_incomplete_coverage", "No references were detected, but source coverage is incomplete."
    return "no_references_detected_within_coverage", "No references were detected within configured complete coverage."


def _safe_target_metadata(value):
    allowed = {"entity_id", "entity_exists", "registry_entry_exists", "domain", "platform", "device_id", "area_id", "disabled", "hidden", "friendly_name", "state"}
    return {key: value.get(key) for key in allowed if key in value}


def _query_fingerprint(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _encode_cursor(index_fingerprint: str, query_fingerprint: str, offset: int) -> str:
    raw = json.dumps({"f": index_fingerprint, "q": query_fingerprint, "o": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str, index_fingerprint: str, query_fingerprint: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode())
        offset = int(value["o"])
        if offset < 0 or value.get("q") != query_fingerprint:
            raise ValueError
    except Exception as exc:
        raise GovernanceError(ErrorCode.INVALID_CURSOR) from exc
    if value.get("f") != index_fingerprint:
        raise GovernanceError(ErrorCode.STALE_CURSOR)
    return offset
