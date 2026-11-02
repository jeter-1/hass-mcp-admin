"""Informational scene, group and dashboard references for governed plans.

HAMCP-270: availability/exposure, delete, entity metadata and helper options
plans disclose the HAMCP-227 global-source findings for their targets before
approval.  The disclosure is information only:

* it is built from ``global_sources.visible()`` of the dependency snapshot the
  index already holds (never a build or refresh, so no new Home Assistant
  read is sent);
* it is projected as the ``global_source_consumers`` sibling of the bound
  consumer evidence and is never part of a consumer fingerprint, plan hash,
  policy subject or decision hash, semantic projection or approval binding;
* it never raises or lowers risk or policy;
* an absent or unreadable store is disclosed as ``unavailable``; it is never
  a refusal and never "no references".
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

KEY = "global_source_consumers"
MODEL = "global_source_consumers_v1"
# Disclosure order; templates are always listed so their gap stays visible.
SOURCE_TYPES = ("scene", "group", "dashboard", "template")
REFERENCE_SOURCE_TYPES = ("scene", "group", "dashboard")
COMPLETENESS = ("complete", "partial", "unavailable")
SOURCE_LABELS = {
    "scene": "scenes",
    "group": "groups",
    "dashboard": "dashboards",
    "template": "templates",
}
# Every child carries its own copy (twice in a plan response), so one plan
# shares a small item budget and each copy has a fixed upper size.
MAX_REFERENCES = 8
MAX_PLAN_REFERENCES = 8
MAX_REASON = 120
MAX_NAME = 64
MAX_ID = 96
MAX_LOCATION = 96
MAX_STATEMENT = 200
INFORMATIONAL_STATEMENT = (
    "Information only: these references are not approval-bound and never "
    "change risk, policy or approval."
)
MEMBERSHIP_STATEMENT = (
    "Scene and group references reflect loaded membership, not stored configuration."
)
BOUND_COVERAGE_STATEMENT = (
    "Coverage notes in the bound consumer evidence describe that evidence only."
)
UNAVAILABLE_REASONS = (
    "dependency_snapshot_not_held",
    "global_source_diagnostics_absent",
    "global_source_diagnostics_unreadable",
)

SnapshotReader = Callable[[], Any]


def per_child_limit(children: int) -> int:
    """Top N per child, so a full plan stays inside one shared budget."""

    return max(1, min(MAX_REFERENCES, MAX_PLAN_REFERENCES // max(1, children)))


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value if len(value) <= limit else value[: limit - 3] + "..."


def read_snapshot(reader: SnapshotReader | None) -> Any | None:
    """Read the held snapshot; any failure is unavailable evidence, not an error."""

    if reader is None:
        return None
    try:
        return reader()
    except Exception:
        return None


def _visible(snapshot: Any | None) -> tuple[Any | None, str | None]:
    if snapshot is None:
        return None, "dependency_snapshot_not_held"
    sources = getattr(snapshot, "global_sources", None)
    if sources is None:
        return None, "global_source_diagnostics_absent"
    try:
        return sources.visible(), None
    except Exception:
        return None, "global_source_diagnostics_unreadable"


def _source_rows(visible: Any | None) -> list[dict[str, Any]]:
    rows = []
    for source_type in SOURCE_TYPES:
        coverage = None
        if visible is not None:
            try:
                coverage = visible.coverage_for(source_type)
            except Exception:
                coverage = None
        completeness = getattr(coverage, "completeness", None)
        if completeness not in COMPLETENESS:
            completeness = "unavailable"
        value = {"source_type": source_type, "completeness": completeness}
        if completeness != "complete":
            # The first reason only; the dependency analysis tools have the rest.
            warnings = [
                text for text in (
                    _text(item, MAX_REASON) for item in list(getattr(coverage, "warnings", None) or [])
                ) if text
            ]
            if warnings:
                value["reason"] = warnings[0]
        rows.append(value)
    return rows


def _references(visible: Any | None, targets: tuple[str, ...]) -> list[dict[str, Any]]:
    if visible is None:
        return []
    wanted = set(targets)
    seen: dict[tuple[str, ...], dict[str, Any]] = {}
    for finding in getattr(visible, "findings", ()) or ():
        source_type = getattr(finding, "source_type", None)
        target = getattr(finding, "target_entity_id", None)
        if source_type not in REFERENCE_SOURCE_TYPES or target not in wanted:
            continue
        reference = {
            "target_entity_id": target,
            "source_type": source_type,
            "source_id": _text(getattr(finding, "source_id", None), MAX_ID),
            "source_name": _text(getattr(finding, "source_name", None), MAX_NAME),
            "relation": _text(getattr(finding, "relation", None), MAX_ID),
            "location": _text(getattr(finding, "config_path", None), MAX_LOCATION),
        }
        if source_type == "dashboard":
            summary = getattr(finding, "evidence_summary", "") or ""
            reference["conditional"] = "under a condition" in summary
        key = tuple(str(reference.get(name)) for name in (
            "target_entity_id", "source_type", "source_id", "relation", "location",
        ))
        seen.setdefault(key, reference)
    order = {name: index for index, name in enumerate(SOURCE_TYPES)}
    return sorted(seen.values(), key=lambda item: (
        order[item["source_type"]], item["source_id"] or "", item["target_entity_id"],
        item["location"] or "", item["relation"] or "",
    ))


def _statements(
    status: str,
    reason: str | None,
    sources: list[dict[str, Any]],
    count: int,
    shown: int,
) -> list[str]:
    statements = [INFORMATIONAL_STATEMENT, MEMBERSHIP_STATEMENT]
    if status == "unavailable":
        statements.append(
            f"References are unavailable ({reason}); this is not proof that "
            "nothing references the target. Planning continues."
        )
    else:
        scanned = [
            f"{SOURCE_LABELS[row['source_type']]} ({row['completeness']})"
            for row in sources if row["completeness"] != "unavailable"
        ]
        not_scanned = [
            SOURCE_LABELS[row["source_type"]]
            for row in sources if row["completeness"] == "unavailable"
        ]
        statements.append(
            "Scanned: " + (", ".join(scanned) or "none") + "; not scanned: "
            + (", ".join(not_scanned) or "none") + "."
        )
        incomplete = [
            f"{SOURCE_LABELS[row['source_type']]} {row['completeness']}"
            for row in sources if row["completeness"] != "complete"
        ]
        if incomplete:
            statements.append("Results may be partial: " + ", ".join(incomplete) + ".")
        if not count:
            statements.append(
                "No reference found"
                + ("; not proof of none while sources are incomplete." if incomplete else ".")
            )
        elif shown < count:
            statements.append(f"Showing {shown} of {count} references.")
    statements.append(BOUND_COVERAGE_STATEMENT)
    return [_text(item, MAX_STATEMENT) or "" for item in statements]


def build(
    snapshot: Any | None,
    entity_ids: Iterable[str],
    *,
    limit: int = MAX_REFERENCES,
) -> dict[str, Any]:
    """Return the bounded informational disclosure for ``entity_ids``."""

    targets = tuple(dict.fromkeys(item for item in entity_ids if isinstance(item, str)))
    visible, reason = _visible(snapshot)
    status = "read" if visible is not None else "unavailable"
    sources = _source_rows(visible)
    references = _references(visible, targets)
    shown = references[: max(0, limit)]
    counts = {
        source_type: sum(1 for item in references if item["source_type"] == source_type)
        for source_type in REFERENCE_SOURCE_TYPES
    }
    disclosure: dict[str, Any] = {
        "model": MODEL,
        "informational": True,
        "binding": "none",
        "affects_risk": False,
        "status": status,
        "targets": list(targets),
        "completeness": {row["source_type"]: row["completeness"] for row in sources},
        "reasons": {row["source_type"]: row["reason"] for row in sources if "reason" in row},
        "items": shown,
        "count": len(references),
        "counts_by_source": counts,
        "limit": limit,
        "truncated": len(references) > len(shown),
        "statements": _statements(status, reason, sources, len(references), len(shown)),
    }
    if reason is not None:
        disclosure["unavailable_reason"] = reason
    else:
        fingerprint = getattr(visible, "fingerprint", None)
        disclosure["provenance"] = {
            "global_sources_fingerprint": fingerprint[:16] if isinstance(fingerprint, str) else None,
            "dependency_index_generation": getattr(snapshot, "generation", None),
        }
    return disclosure


def disclose(
    reader: SnapshotReader | None,
    entity_ids: Iterable[str],
    *,
    limit: int = MAX_REFERENCES,
) -> dict[str, Any]:
    """Read the held snapshot and build the disclosure; never raises."""

    targets = tuple(entity_ids)
    try:
        return build(read_snapshot(reader), targets, limit=limit)
    except Exception:
        # A malformed held snapshot is unavailable evidence, never a refusal.
        return build(_Unreadable(), targets, limit=limit)


class _Unreadable:
    """A snapshot whose global-source partition cannot be read."""

    @property
    def global_sources(self) -> Any:
        return _UnreadableSources()


class _UnreadableSources:
    def visible(self) -> Any:
        raise RuntimeError("global_source_diagnostics_unreadable")


__all__ = [
    "KEY",
    "MODEL",
    "build",
    "disclose",
    "per_child_limit",
    "read_snapshot",
]
