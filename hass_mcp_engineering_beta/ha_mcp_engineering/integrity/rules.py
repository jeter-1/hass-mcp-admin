"""Deterministic global configuration-integrity classification rules."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..dependency.extraction import valid_entity_id
from .models import (
    IntegrityEvidenceBundle,
    IntegrityEvidenceReference,
    IntegrityFinding,
    stable_id,
)


SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}


def classify_integrity(
    bundle: IntegrityEvidenceBundle,
    *,
    finding_types: list[str],
    include_orphan_candidates: bool,
) -> tuple[
    list[IntegrityFinding], dict[str, IntegrityEvidenceReference], list[str]
]:
    """Classify sanitized inventories without additional provider access."""

    evidence: dict[str, IntegrityEvidenceReference] = {}
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    selected_types = set(finding_types)

    for item in bundle.exact_references:
        target = str(item.get("target_entity_id") or "").lower()
        if not valid_entity_id(target):
            continue
        source_type = _effective_source_type(item)
        source_id = str(item.get("source_id") or "unknown")[:128]
        registry = bundle.entity_registry.get(target)
        state_exists = target in bundle.current_states
        finding_type = None
        if registry is not None and registry.get("disabled_by"):
            finding_type = "disabled_entity_reference"
        elif state_exists:
            continue
        elif registry is not None and bundle.states_available:
            finding_type = "registry_only_entity_reference"
        elif (
            registry is None
            and bundle.states_available
            and bundle.registry_available
        ):
            finding_type = "missing_entity_reference"
        if finding_type not in selected_types:
            continue
        grouped[(finding_type, target, source_type, source_id)].append(item)

    findings = []
    for (finding_type, target, source_type, source_id), items in sorted(
        grouped.items()
    ):
        source_enabled = _source_enabled(items[0].get("source_state"))
        registry = bundle.entity_registry.get(target) or {}
        paths = tuple(
            sorted(
                {
                    str(item.get("config_path") or "unknown")[:256]
                    for item in items
                }
            )[:20]
        )
        references = []
        for item in items:
            reference_id = str(
                item.get("evidence_id")
                or stable_id(
                    "integrity_evidence",
                    finding_type,
                    target,
                    source_type,
                    source_id,
                    item.get("config_path"),
                )
            )[:128]
            references.append(reference_id)
            evidence[reference_id] = IntegrityEvidenceReference(
                reference_id=reference_id,
                evidence_kind="exact_static_reference",
                summary=str(
                    item.get("evidence_summary")
                    or "An exact static entity reference was inspected."
                )[:300],
                confidence="exact",
                target_entity_id=target,
                source_type=source_type,
                source_id=source_id,
                source_entity_id=_optional(item.get("source_entity_id"), 128),
                source_name=_optional(item.get("source_name"), 160),
                configuration_paths=(
                    str(item.get("config_path") or "unknown")[:256],
                ),
                registry_platform=_optional(registry.get("platform"), 128),
                disabled_by=_optional(registry.get("disabled_by"), 64),
                excerpt=_optional(item.get("excerpt"), 300),
            )
        disabled_by = _optional(registry.get("disabled_by"), 64)
        findings.append(
            IntegrityFinding(
                finding_id=stable_id(
                    "integrity", finding_type, target, source_type, source_id
                ),
                rule_id=finding_type,
                finding_type=finding_type,
                severity=(
                    "high"
                    if finding_type == "missing_entity_reference"
                    and source_enabled is True
                    else "medium"
                ),
                confidence="exact",
                target_entity_id=target,
                source_type=source_type,
                source_id=source_id,
                source_entity_id=_optional(items[0].get("source_entity_id"), 128),
                source_name=_optional(items[0].get("source_name"), 160),
                source_enabled=source_enabled,
                registry_platform=_optional(registry.get("platform"), 128),
                disabled_by=disabled_by,
                disabled_classification=_disabled_classification(disabled_by),
                explanation=_reference_explanation(
                    finding_type, source_type, source_enabled
                ),
                consequence=_reference_consequence(finding_type),
                configuration_paths=paths,
                evidence_references=tuple(sorted(set(references))),
                manual_review_required=True,
                remediation_required=finding_type == "missing_entity_reference",
            )
        )

    if "unresolved_dynamic_reference" in selected_types:
        for item in sorted(
            bundle.dynamic_references,
            key=lambda value: (
                str(value.get("source_type")),
                str(value.get("source_id")),
                str(value.get("config_path")),
            ),
        ):
            source_type = str(item.get("source_type") or "unknown")[:64]
            source_id = str(item.get("source_id") or "unknown")[:128]
            path = str(item.get("config_path") or "unknown")[:256]
            reference_id = str(
                item.get("evidence_id")
                or stable_id("integrity_dynamic", source_type, source_id, path)
            )[:128]
            evidence[reference_id] = IntegrityEvidenceReference(
                reference_id=reference_id,
                evidence_kind="unresolved_dynamic_reference",
                summary=str(
                    item.get("warning")
                    or "A dynamic entity reference could not be resolved statically."
                )[:300],
                confidence="limited",
                source_type=source_type,
                source_id=source_id,
                source_entity_id=_optional(item.get("source_entity_id"), 128),
                source_name=_optional(item.get("source_name"), 160),
                configuration_paths=(path,),
                excerpt=_optional(item.get("excerpt"), 300),
            )
            findings.append(
                IntegrityFinding(
                    finding_id=stable_id(
                        "integrity",
                        "unresolved_dynamic_reference",
                        source_type,
                        source_id,
                        path,
                    ),
                    rule_id="unresolved_dynamic_reference",
                    finding_type="unresolved_dynamic_reference",
                    severity="medium",
                    confidence="limited",
                    source_type=source_type,
                    source_id=source_id,
                    source_entity_id=_optional(
                        item.get("source_entity_id"), 128
                    ),
                    source_name=_optional(item.get("source_name"), 160),
                    source_enabled=_source_enabled(item.get("source_state")),
                    explanation=(
                        f"{_article(source_type)} {source_type} contains a dynamic expression capable of selecting entity IDs that cannot be resolved statically."
                    ),
                    consequence=(
                        "The target relationship is unknown and requires manual review; no entity ID was inferred."
                    ),
                    configuration_paths=(path,),
                    evidence_references=(reference_id,),
                    manual_review_required=True,
                    remediation_required=False,
                )
            )

    warnings = []
    if include_orphan_candidates and "orphan_registry_candidate" in selected_types:
        if not bundle.orphan_scope_complete:
            warnings.append(
                "Orphan registry candidates were omitted because source filtering excluded configuration source types."
            )
        elif bundle.states_available and bundle.registry_available:
            inbound_targets = {
                str(item.get("target_entity_id") or "").lower()
                for item in bundle.exact_references
                if valid_entity_id(
                    str(item.get("target_entity_id") or "").lower()
                )
            }
            for entity_id, registry in sorted(bundle.entity_registry.items()):
                if entity_id in bundle.current_states or entity_id in inbound_targets:
                    continue
                disabled_by = _optional(registry.get("disabled_by"), 64)
                reference_id = stable_id(
                    "integrity_evidence",
                    "orphan_registry_candidate",
                    entity_id,
                    registry.get("platform"),
                    disabled_by,
                )
                evidence[reference_id] = IntegrityEvidenceReference(
                    reference_id=reference_id,
                    evidence_kind="registry_inventory_candidate",
                    summary=(
                        "The registry entry has no current state and no exact inbound reference in the inspected dependency index."
                    ),
                    confidence="limited",
                    target_entity_id=entity_id,
                    registry_platform=_optional(registry.get("platform"), 128),
                    disabled_by=disabled_by,
                )
                findings.append(
                    IntegrityFinding(
                        finding_id=stable_id(
                            "integrity", "orphan_registry_candidate", entity_id
                        ),
                        rule_id="orphan_registry_candidate",
                        finding_type="orphan_registry_candidate",
                        severity="low",
                        confidence="limited",
                        target_entity_id=entity_id,
                        registry_platform=_optional(
                            registry.get("platform"), 128
                        ),
                        disabled_by=disabled_by,
                        disabled_classification=_disabled_classification(
                            disabled_by
                        ),
                        explanation=(
                            "An entity-registry entry has no current state-machine entry and no exact inbound reference in the inspected dependency index."
                        ),
                        consequence=(
                            "This is a manual cleanup candidate only; unsupported sources, integrations, external systems, dynamic templates, or later entity recreation may still establish active use."
                        ),
                        configuration_paths=(),
                        evidence_references=(reference_id,),
                        manual_review_required=True,
                        remediation_required=False,
                    )
                )
        else:
            warnings.append(
                "Orphan registry candidates were omitted because complete state and registry inventories were unavailable."
            )

    return (
        sorted(findings, key=_finding_sort_key),
        evidence,
        warnings[:10],
    )


def _effective_source_type(item: dict[str, Any]) -> str:
    relation = str(item.get("relation") or "")
    return (
        "blueprint"
        if relation.startswith("blueprint")
        else str(item.get("source_type") or "unknown")[:64]
    )


def _source_enabled(value: Any) -> bool | None:
    normalized = str(value or "").strip().lower()
    if normalized == "on":
        return True
    if normalized == "off":
        return False
    return None


def _disabled_classification(value: str | None) -> str:
    if value == "user":
        return "user_disabled"
    if value == "integration":
        return "integration_disabled"
    if value:
        return "other_disabled"
    return "not_disabled"


def _reference_explanation(
    finding_type: str, source_type: str, source_enabled: bool | None
) -> str:
    state = "enabled " if source_enabled is True else "disabled " if source_enabled is False else ""
    article = _article(state + source_type)
    if finding_type == "missing_entity_reference":
        return (
            f"{article} {state}{source_type} contains an exact static reference to an entity absent from the state machine and entity registry."
        )
    if finding_type == "disabled_entity_reference":
        return (
            f"{article} {state}{source_type} contains an exact static reference to a registry entity classified as disabled."
        )
    return (
        f"{article} {state}{source_type} contains an exact static reference to a registry entity with no current state-machine entry."
    )


def _reference_consequence(finding_type: str) -> str:
    if finding_type == "missing_entity_reference":
        return (
            "The referenced trigger, condition, or action may not behave as intended."
        )
    if finding_type == "disabled_entity_reference":
        return (
            "The disabled target may be intentionally retained, but the consumer has a runtime and configuration-integrity risk requiring review."
        )
    return (
        "The target may be unloaded, temporarily absent, or stale; available evidence cannot resolve the condition safely."
    )


def _article(value: str) -> str:
    return "An" if value[:1].lower() in {"a", "e", "i", "o", "u"} else "A"


def _optional(value: Any, limit: int) -> str | None:
    if value in (None, ""):
        return None
    return str(value)[:limit]


def _finding_sort_key(item: IntegrityFinding):
    return (
        SEVERITY_RANK[item.severity],
        item.finding_type,
        item.target_entity_id or "",
        item.source_type or "",
        item.source_id or "",
        item.configuration_paths,
        item.finding_id,
    )
