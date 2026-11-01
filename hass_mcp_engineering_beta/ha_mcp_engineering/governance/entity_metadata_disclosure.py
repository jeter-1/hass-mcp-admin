"""Consumer disclosure for one entity registry metadata change (HAMCP-069A-1).

Disclosure is decision support (ADR-022).  It is built only from the existing
dependency index; no new index is built.  Only its fingerprint and the
policy-relevant facts (§5 E2) are execution-binding.  The fingerprint is
target-scoped: it excludes the index generation, fingerprint and source
epoch, so an unrelated automation edit does not invalidate an approval while a
change to a consumer of the touched label or area does.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .entity_metadata import (
    ENTITY_METADATA_BINDINGS_KEY,
    normalize_entry,
    touched_fields,
)
from .normalize import stable_hash

DISCLOSURE_MODEL = "entity_metadata_consumer_disclosure_v1"
MAX_DISCLOSED_CONSUMERS = 32
_CONSEQUENCE_RANK = {
    "none": 0, "indirect": 1, "unknown": 2, "direct": 3, "safety_critical": 4,
}
_PHYSICAL_CONSEQUENCES = frozenset({"direct", "safety_critical"})
_MEMBERSHIP_PRODUCERS = {
    "entity_set_producer:label_entities": "label",
    "entity_set_producer:area_entities": "area",
}
_EFFECT_SELECTORS = {"label_id": "label", "area_id": "area"}
# Gaps the existing index cannot see.  They are always disclosed for a
# membership change and are never reported as "no consumers" (§4).
KNOWN_COVERAGE_GAPS = (
    "trigger_and_condition_label_and_area_selectors_not_indexed",
    "area_entities_not_resolved_to_members",
    "dashboards_scripts_and_templates_not_scanned",
)
ASSIST_COVERAGE_NOTE = (
    "Assist and voice addressing is not indexed. Home Assistant refreshes the "
    "default conversation agent when aliases, area or name change."
)


class EntityMetadataEvidenceUnavailable(RuntimeError):
    """The dependency snapshot needed for a membership change is unavailable."""


def membership_references(
    current: dict[str, Any],
    final: dict[str, Any],
    reference_definitions: dict[str, Any],
) -> dict[str, dict[str, set[str]]]:
    """Return the label and area match keys for a membership change.

    Each reference maps to the set of selector spellings the index may hold:
    the id and, when known, the casefolded name with and without spaces.
    """

    before = normalize_entry(current) or {}
    after = normalize_entry(final) or {}
    touched = set(touched_fields(current, final))
    result: dict[str, dict[str, set[str]]] = {"label": {}, "area": {}}
    definitions = reference_definitions if isinstance(reference_definitions, dict) else {}
    if "labels" in touched:
        known = definitions.get("labels") if isinstance(definitions.get("labels"), dict) else {}
        for label_id in sorted(set(before["labels"]) ^ set(after["labels"])):
            result["label"][label_id] = _spellings(label_id, known.get(label_id))
    if "area_id" in touched:
        known = definitions.get("areas") if isinstance(definitions.get("areas"), dict) else {}
        for area_id in (before["area_id"], after["area_id"]):
            if isinstance(area_id, str):
                result["area"][area_id] = _spellings(area_id, known.get(area_id))
    return result


def _spellings(identifier: str, definition: Any) -> set[str]:
    values = {identifier.casefold()}
    name = definition.get("name") if isinstance(definition, dict) else None
    if isinstance(name, str) and name:
        folded = name.casefold()
        values.update({folded, folded.replace(" ", ""), folded.replace(" ", "_")})
    return values


def _match(kind_refs: dict[str, set[str]], value: str) -> str | None:
    folded = value.casefold()
    for reference, spellings in kind_refs.items():
        if folded in spellings:
            return reference
    return None


def build_consumer_disclosure(
    snapshot: Any | None,
    *,
    entity_id: str,
    current: dict[str, Any],
    final: dict[str, Any],
    reference_definitions: dict[str, Any],
) -> dict[str, Any]:
    """Build the bounded, deterministic disclosure for one planned change."""

    touched = set(touched_fields(current, final))
    references = membership_references(current, final, reference_definitions)
    membership_changed = bool(references["label"] or references["area"])
    disclosure: dict[str, Any] = {
        "model": DISCLOSURE_MODEL,
        "entity_id": entity_id,
        "membership_changed": membership_changed,
        "references": {
            kind: sorted(values) for kind, values in references.items()
        },
        "consumers": [],
        "consumer_count": 0,
        "consumers_truncated": False,
        "strongest_physical_consequence": "none",
        "coverage": [],
        "coverage_complete": not membership_changed,
        "coverage_gaps": list(KNOWN_COVERAGE_GAPS) if membership_changed else [],
        "assist_coverage": (
            "unavailable" if touched & {"aliases", "area_id", "name"} else "not_applicable"
        ),
    }
    if disclosure["assist_coverage"] == "unavailable":
        disclosure["assist_note"] = ASSIST_COVERAGE_NOTE
    if not membership_changed:
        return disclosure
    if snapshot is None:
        raise EntityMetadataEvidenceUnavailable("dependency_snapshot_unavailable")

    profiles: dict[str, Any] = {}
    for profile in getattr(snapshot, "automation_action_profiles", ()) or ():
        profiles.setdefault(profile.source_id, profile)
    consumers: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    incomplete: set[str] = set()

    def add(source_type: str, source_id: str, source_entity_id: Any, via: str, reference: str, outcome: str) -> None:
        profile = profiles.get(source_id)
        consequence = getattr(profile, "physical_consequence", None)
        if consequence not in _CONSEQUENCE_RANK:
            consequence = "unknown"
        key = (source_type, source_id, via, reference)
        consumers[key] = {
            "source_type": source_type,
            "source_id": source_id,
            "source_entity_id": source_entity_id if isinstance(source_entity_id, str) else None,
            "via": via,
            "reference": reference,
            "outcome": outcome,
            "services": sorted(getattr(profile, "services", ()) or ())[:8],
            "physical_consequence": consequence,
        }

    for obligation in getattr(snapshot, "obligations", ()) or ():
        for provenance in obligation.context_provenance or ():
            kind = _MEMBERSHIP_PRODUCERS.get(provenance)
            if kind is None:
                continue
            literals = tuple(obligation.literal_selectors or ())
            if not literals:
                incomplete.add(f"non_literal_{kind}_entities_argument")
                continue
            for literal in literals:
                reference = _match(references[kind], literal)
                if reference is not None:
                    add(
                        obligation.source_type,
                        obligation.source_id,
                        obligation.source_entity_id,
                        f"{kind}_entities",
                        f"{kind}:{reference}",
                        obligation.outcome,
                    )
    for profile in profiles.values():
        if profile.effect_projection_clipped:
            incomplete.add("effect_projection_clipped")
        if profile.processing_limit_exceeded:
            incomplete.add("processing_limit_exceeded")
        for token in profile.effect_targets or ():
            parts = token.rsplit("|", 2)
            if len(parts) != 3:
                continue
            kind = _EFFECT_SELECTORS.get(parts[1])
            if kind is None:
                continue
            reference = _match(references[kind], parts[2])
            if reference is not None:
                add(
                    "automation",
                    profile.source_id,
                    profile.source_entity_id,
                    "action_target",
                    f"{kind}:{reference}",
                    "exact" if profile.complete else "opaque",
                )
    if getattr(snapshot, "automation_read_failures", ()):
        incomplete.add("automation_read_failures")
    if getattr(snapshot, "obligation_overflow_count", 0):
        incomplete.add("obligation_overflow")

    coverage = sorted(
        (
            {"source_type": item.source_type, "completeness": item.completeness}
            for item in getattr(snapshot, "coverage", ()) or ()
        ),
        key=lambda item: (item["source_type"], item["completeness"]),
    )
    ordered = [consumers[key] for key in sorted(consumers)]
    strongest = "none"
    for item in ordered:
        if _CONSEQUENCE_RANK[item["physical_consequence"]] > _CONSEQUENCE_RANK[strongest]:
            strongest = item["physical_consequence"]
    disclosure.update(
        {
            "consumers": ordered[:MAX_DISCLOSED_CONSUMERS],
            "consumer_count": len(ordered),
            "consumers_truncated": len(ordered) > MAX_DISCLOSED_CONSUMERS,
            "consumers_fingerprint": stable_hash(ordered),
            "strongest_physical_consequence": strongest,
            "coverage": coverage,
            "coverage_complete": False,
            "coverage_gaps": sorted({*KNOWN_COVERAGE_GAPS, *incomplete}),
        }
    )
    return disclosure


def physical_consumer_consequence(disclosure: dict[str, Any]) -> str | None:
    """Return the E2 consequence, if an indexed physical consumer exists."""

    value = disclosure.get("strongest_physical_consequence")
    return value if value in _PHYSICAL_CONSEQUENCES else None


def consumer_evidence_fingerprint(disclosure: dict[str, Any]) -> str:
    """Return the execution-binding, target-scoped evidence fingerprint."""

    material = {
        key: value
        for key, value in disclosure.items()
        if key not in {"consumers", "assist_note"}
    }
    return stable_hash(material)


SnapshotReader = Callable[..., Awaitable[Any]]


async def read_runtime_dependency_snapshot(*, fenced: bool) -> Any:
    """Read one current dependency snapshot, fenced after the lock set."""

    from ..dependency import DEPENDENCY_ANALYSIS

    try:
        index = DEPENDENCY_ANALYSIS.require().index
        fence = (
            index.open_source_fence("governed_entity_metadata_preflight")
            if fenced
            else None
        )
        snapshot, _rebuilt, _duration = await index.get(
            refresh=True, min_source_epoch=fence
        )
    except Exception as exc:
        raise EntityMetadataEvidenceUnavailable(
            "dependency_snapshot_unavailable"
        ) from exc
    return snapshot


def read_cached_dependency_snapshot() -> Any | None:
    """The current dependency snapshot already held; no build or refresh."""

    from ..dependency import DEPENDENCY_ANALYSIS

    try:
        return DEPENDENCY_ANALYSIS.require().index.current_snapshot()
    except Exception:
        return None


def snapshot_identity(snapshot: Any | None) -> dict[str, Any] | None:
    """Return provenance only; it is not part of the evidence fingerprint."""

    if snapshot is None:
        return None
    return {
        "dependency_index_fingerprint": getattr(snapshot, "fingerprint", None),
        "dependency_index_generation": getattr(snapshot, "generation", None),
        "dependency_index_source_epoch": getattr(snapshot, "source_epoch", None),
    }


def runtime_consumer_evidence(
    reader: SnapshotReader = read_runtime_dependency_snapshot,
) -> Callable[[str, dict[str, Any], dict[str, Any]], Awaitable[str]]:
    """Return the post-lock preflight evidence callable for the gateway."""

    async def evidence(
        entity_id: str, current: dict[str, Any], proposed: dict[str, Any]
    ) -> str:
        bindings = proposed.get(ENTITY_METADATA_BINDINGS_KEY)
        definitions = (
            bindings.get("reference_definitions")
            if isinstance(bindings, dict)
            else None
        )
        references = membership_references(current, proposed, definitions or {})
        snapshot = (
            await reader(fenced=True)
            if references["label"] or references["area"]
            else None
        )
        return consumer_evidence_fingerprint(
            build_consumer_disclosure(
                snapshot,
                entity_id=entity_id,
                current=current,
                final=proposed,
                reference_definitions=definitions or {},
            )
        )

    return evidence
