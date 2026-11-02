"""Informational scene, group and dashboard references for governed plans.

HAMCP-270: availability/exposure, delete, entity metadata and helper options
plans disclose the HAMCP-227 global-source findings for their targets before
approval.  The disclosure is information only:

* it is built from ``global_sources.visible()`` of the dependency snapshot the
  index already holds, read once per plan (never a build or refresh, so no
  new Home Assistant read is sent);
* it is projected once per plan response as
  ``dry_run_results.global_source_consumers``: the shared coverage context,
  then one entry per child naming that child's targets.  It is never part of
  a consumer fingerprint, plan hash, policy subject or decision hash,
  semantic projection or approval binding;
* it never raises or lowers risk or policy;
* an absent or unreadable store is disclosed as ``unavailable``; it is never
  a refusal and never "no references".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

KEY = "global_source_consumers"
MODEL = "global_source_consumers_v1"
# Disclosure order; templates are always listed so their gap stays visible.
SOURCE_TYPES = ("scene", "group", "dashboard", "template")
REFERENCE_SOURCE_TYPES = ("scene", "group", "dashboard")
COMPLETENESS = ("complete", "partial", "unavailable")
# One plan shares a small item budget; display fields have fixed caps.
MAX_REFERENCES = 8
MAX_PLAN_REFERENCES = 8
MAX_REASON = 120
MAX_NAME = 24
MAX_ID = 32
MAX_RELATION = 20
MAX_LOCATION = 32
MAX_STATEMENT = 200
INFORMATIONAL_STATEMENT = (
    "Information only: not approval-bound; never changes risk, policy or approval."
)
MEMBERSHIP_STATEMENT = (
    "Scene/group references are loaded membership, not stored configuration."
)
BOUND_COVERAGE_STATEMENT = (
    "Bound note dashboards_scripts_and_templates_not_scanned describes the "
    "risk evidence, not these references."
)
UNAVAILABLE_REASONS = (
    "dependency_snapshot_not_held",
    "global_source_diagnostics_absent",
    "global_source_diagnostics_unreadable",
)

SnapshotReader = Callable[[], Any]


@dataclass(frozen=True)
class Reference:
    """One screened finding, keyed by its full original identity."""

    source_type: str
    source_id: str | None
    target: str
    location: str | None
    relation: str | None
    name: str | None
    conditional: bool | None

    @property
    def identity(self) -> tuple[str, ...]:
        return (self.source_type, str(self.source_id), self.target,
                str(self.location), str(self.relation))

    @property
    def order(self) -> tuple[Any, ...]:
        return (SOURCE_TYPES.index(self.source_type), self.source_id or "",
                self.target, self.location or "", self.relation or "")


@dataclass(frozen=True)
class Context:
    """The one held-snapshot read a plan's disclosure is built from."""

    status: str
    reason: str | None
    sources: tuple[dict[str, Any], ...]
    references: tuple[Reference, ...] = ()
    provenance: dict[str, Any] | None = None


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


def _source_rows(visible: Any | None) -> tuple[dict[str, Any], ...]:
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
    return tuple(rows)


def _references(visible: Any) -> tuple[Reference, ...]:
    """Deduplicate and order on the full original identity, before display."""

    seen: dict[tuple[str, ...], Reference] = {}
    for finding in getattr(visible, "findings", ()) or ():
        source_type = getattr(finding, "source_type", None)
        target = getattr(finding, "target_entity_id", None)
        if source_type not in REFERENCE_SOURCE_TYPES or not isinstance(target, str):
            continue
        conditional = None
        if source_type == "dashboard":
            summary = getattr(finding, "evidence_summary", "") or ""
            conditional = "under a condition" in str(summary)
        reference = Reference(
            source_type=source_type,
            source_id=_original(getattr(finding, "source_id", None)),
            target=target,
            location=_original(getattr(finding, "config_path", None)),
            relation=_original(getattr(finding, "relation", None)),
            name=_original(getattr(finding, "source_name", None)),
            conditional=conditional,
        )
        seen.setdefault(reference.identity, reference)
    return tuple(sorted(seen.values(), key=lambda item: item.order))


def _original(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def read(reader: SnapshotReader | None) -> Context:
    """Read the held snapshot once for a plan; never raises."""

    try:
        snapshot = read_snapshot(reader)
        visible, reason = _visible(snapshot)
        if visible is None:
            return Context("unavailable", reason, _source_rows(None))
        sources = _source_rows(visible)
        references = _references(visible)
        fingerprint = getattr(visible, "fingerprint", None)
        provenance = {
            "fingerprint": fingerprint[:16] if isinstance(fingerprint, str) else None,
            "generation": getattr(snapshot, "generation", None),
        }
        return Context("read", None, sources, references, provenance)
    except Exception:
        # A malformed held snapshot is unavailable evidence, never a refusal.
        return Context("unavailable", "global_source_diagnostics_unreadable", _source_rows(None))


def _display(reference: Reference, *, with_target: bool) -> dict[str, Any]:
    item: dict[str, Any] = {"type": reference.source_type}
    if with_target:
        item["target"] = reference.target
    # A loaded membership's relation and list position are implied by the
    # source type; only other references show where they occur.
    membership = reference.relation == f"{reference.source_type}_member"
    relation = reference.relation
    if isinstance(relation, str) and relation.startswith(f"{reference.source_type}_"):
        relation = relation[len(reference.source_type) + 1:]
    for name, value, limit in (
        ("id", reference.source_id, MAX_ID),
        ("name", reference.name, MAX_NAME),
        ("location", None if membership else reference.location, MAX_LOCATION),
        ("relation", None if membership else relation, MAX_RELATION),
    ):
        text = _text(value, limit)
        if text is not None:
            item[name] = text
    # Shown only when true: a dashboard card displayed under a condition.
    if reference.conditional:
        item["conditional"] = True
    return item


def child(
    context: Context,
    entity_ids: Iterable[str],
    *,
    limit: int = MAX_REFERENCES,
) -> dict[str, Any]:
    """One child's target(s) and their bounded references; never raises.

    Children are listed in operation order; a single target is ``target``.
    """

    targets = tuple(dict.fromkeys(item for item in entity_ids if isinstance(item, str)))
    value: dict[str, Any] = (
        {"target": targets[0]} if len(targets) == 1 else {"targets": list(targets)}
    )
    if context.status != "read":
        # Unknown, not zero: no count is claimed.
        return value
    wanted = set(targets)
    references = [item for item in context.references if item.target in wanted]
    shown = [
        _display(item, with_target=len(targets) > 1)
        for item in references[: max(0, limit)]
    ]
    # Distinct originals whose bounded display forms collide stay distinct,
    # with an unbound display suffix.
    collisions: dict[tuple[Any, ...], int] = {}
    for item in shown:
        key = tuple(sorted((name, str(text)) for name, text in item.items()))
        collisions[key] = collisions.get(key, 0) + 1
        if collisions[key] > 1:
            item["id"] = f"{item.get('id', '')}~{collisions[key]}"
    value.update({
        "count": len(references),
        # Nonzero totals per source; absent means 0.
        "by_source": {
            source_type: total for source_type in REFERENCE_SOURCE_TYPES
            if (total := sum(1 for item in references if item.source_type == source_type))
        },
        "truncated": len(references) > len(shown),
        "items": shown,
    })
    return value


def _statements(context: Context, children: list[dict[str, Any]], limit: int) -> list[str]:
    statements = [INFORMATIONAL_STATEMENT, MEMBERSHIP_STATEMENT]
    if context.status != "read":
        statements.append(
            f"References unavailable ({context.reason}): not proof that nothing "
            "references the targets. Planning continues."
        )
    else:
        if any(row["completeness"] != "complete" for row in context.sources):
            statements.append("A count of 0 is not proof of none while a source is incomplete.")
        if any(item.get("truncated") for item in children):
            statements.append(f"Each child lists at most {limit}; counts are totals.")
    statements.append(BOUND_COVERAGE_STATEMENT)
    return [_text(item, MAX_STATEMENT) or "" for item in statements]


def plan_block(
    context: Context,
    children: Iterable[dict[str, Any]],
    *,
    limit: int = MAX_REFERENCES,
) -> dict[str, Any]:
    """The one per-plan disclosure: shared context, then each child."""

    children = list(children)
    block: dict[str, Any] = {
        "model": MODEL,
        "informational": True,
        "status": context.status,
        "completeness": {row["source_type"]: row["completeness"] for row in context.sources},
        "reasons": {row["source_type"]: row["reason"] for row in context.sources if "reason" in row},
        "statements": _statements(context, children, limit),
    }
    if context.status != "read":
        block["unavailable_reason"] = context.reason
    else:
        block["provenance"] = context.provenance
    block["children"] = children
    return block


def disclose(
    reader: SnapshotReader | None,
    entity_ids: Iterable[str],
    *,
    limit: int = MAX_REFERENCES,
) -> dict[str, Any]:
    """A single-child plan block read from ``reader``; never raises."""

    context = read(reader)
    return plan_block(context, [child(context, entity_ids, limit=limit)], limit=limit)


__all__ = [
    "KEY",
    "MODEL",
    "Context",
    "child",
    "disclose",
    "per_child_limit",
    "plan_block",
    "read",
    "read_snapshot",
]
