"""Deterministic, evidence-backed change-impact rules."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .models import (
    ImpactAffectedObjectGroup,
    ImpactEvidenceBundle,
    ImpactFinding,
    stable_id,
)


DIRECT_RULES = {
    "automation": "direct_automation_reference",
    "blueprint": "direct_blueprint_reference",
    "script": "direct_script_reference",
    "scene": "direct_scene_reference",
    "group": "direct_group_reference",
    "template": "direct_template_reference",
    "dashboard": "direct_dashboard_reference",
}
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def evaluate_impact_rules(bundle: ImpactEvidenceBundle) -> list[ImpactFinding]:
    findings: list[ImpactFinding] = []
    grouped: dict[tuple[str, str, str, bool], list[dict]] = defaultdict(list)
    for item in [*bundle.direct_dependencies, *bundle.indirect_dependencies]:
        rule_source = (
            "blueprint"
            if str(item.get("relation", "")).startswith("blueprint")
            else str(item.get("source_type", ""))
        )
        grouped[
            (
                rule_source,
                str(item.get("source_id", "unknown")),
                str(item.get("affected_object_id", item.get("source_id", "unknown"))),
                bool(item.get("direct", True)),
            )
        ].append(item)

    for (source_type, source_id, affected_id, direct), items in sorted(grouped.items()):
        references = tuple(sorted({str(item["reference_id"]) for item in items}))
        depth = min(max(1, int(item.get("depth", 1))) for item in items)
        affected_type = str(items[0].get("affected_object_type") or source_type)
        coverage = (source_type,)
        if direct:
            rule_id = DIRECT_RULES.get(source_type)
            if rule_id:
                findings.append(
                    _finding(
                        rule_id,
                        "medium",
                        "exact",
                        "static_reference",
                        affected_type,
                        affected_id,
                        True,
                        depth,
                        f"A {source_type} contains one or more exact static references to the target entity.",
                        _direct_consequence(bundle.operation, source_type),
                        references,
                        True,
                        True,
                        coverage,
                    )
                )
        else:
            findings.append(
                _finding(
                    "indirect_dependency_path",
                    "medium",
                    "high",
                    "indirect_dependency",
                    affected_type,
                    affected_id,
                    False,
                    depth,
                    "An explicit bounded dependency path connects this object to the target entity.",
                    "The proposed change may alter behavior through the reported dependency path.",
                    references,
                    True,
                    True,
                    coverage,
                )
            )

        if direct and bundle.operation == "rename_entity":
            findings.append(
                _finding(
                    "rename_reference_migration_required",
                    "high",
                    "exact",
                    "reference_migration",
                    affected_type,
                    affected_id,
                    True,
                    depth,
                    "The affected object still uses the current entity ID.",
                    "The reference requires review and migration to the replacement ID; automatic rewriting is not assumed.",
                    references,
                    True,
                    True,
                    coverage,
                )
            )
        elif direct and bundle.operation == "remove_entity":
            findings.append(
                _finding(
                    "remove_orphaned_consumer",
                    "high",
                    "exact",
                    "orphaned_consumer",
                    affected_type,
                    affected_id,
                    True,
                    depth,
                    "The affected object consumes the entity through a static reference.",
                    "Removing the entity would leave this consumer with a stale or missing dependency.",
                    references,
                    True,
                    True,
                    coverage,
                )
            )
        elif direct and bundle.operation == "disable_entity":
            findings.append(
                _finding(
                    "disable_runtime_availability_risk",
                    "medium",
                    "high",
                    "runtime_availability",
                    affected_type,
                    affected_id,
                    True,
                    depth,
                    "The affected object consumes the entity while it is enabled in the state machine.",
                    "Disabling the entity may stop triggers or expose unavailable, unknown, or absent-state behavior.",
                    references,
                    False,
                    True,
                    coverage,
                )
            )

    for item in bundle.dynamic_references:
        ref = str(item["reference_id"])
        findings.append(
            _finding(
                "unresolved_dynamic_reference",
                "medium",
                "limited",
                "dynamic_reference",
                str(item.get("affected_object_type", "configuration")),
                str(item.get("affected_object_id", item.get("source_id", "unknown"))),
                True,
                1,
                "A dynamic reference in an already affected object cannot be resolved statically.",
                "Manual review is required because the target relationship cannot be bounded safely.",
                (ref,),
                False,
                True,
                (str(item.get("source_type", "unknown")),),
            )
        )

    for item in bundle.recent_traces:
        findings.append(
            _finding(
                "recent_trace_reference",
                "low",
                "high",
                "runtime_evidence",
                "automation",
                str(item["affected_object_id"]),
                True,
                1,
                "A statically affected automation also has bounded recent execution evidence.",
                "The consumer appears operationally active and should be reviewed before the proposed change.",
                (str(item["reference_id"]),),
                False,
                True,
                ("automation_traces",),
            )
        )

    if bundle.system_log_entries:
        refs = tuple(
            sorted(str(item["reference_id"]) for item in bundle.system_log_entries)
        )
        findings.append(
            _finding(
                "correlated_system_log_reference",
                "low",
                "high",
                "runtime_evidence",
                "entity",
                bundle.entity_id,
                True,
                0,
                "Sanitized System Log evidence contains an exact target-entity identifier.",
                "The bounded runtime evidence should be reviewed; retention completeness is unknown.",
                refs,
                False,
                True,
                ("system_log",),
            )
        )

    evidence_by_kind: dict[str, list[str]] = defaultdict(list)
    for reference in bundle.evidence.values():
        evidence_by_kind[reference.evidence_kind].append(reference.reference_id)

    for kind, rule_id, object_type, consequence in (
        (
            "entity_registry_relationship",
            "entity_registry_relationship",
            "entity_registry_entry",
            "The entity registry relationship must be reviewed for operation-specific persistence behavior.",
        ),
        (
            "device_registry_relationship",
            "device_registry_relationship",
            "device",
            "The entity is linked to a device; the relationship may persist or change independently of state availability.",
        ),
        (
            "area_relationship",
            "area_relationship",
            "area",
            "The entity has an area relationship that may affect discovery and user-facing organization.",
        ),
    ):
        for reference_id in sorted(evidence_by_kind.get(kind, ())):
            reference = bundle.evidence[reference_id]
            findings.append(
                _finding(
                    rule_id,
                    "info",
                    "exact",
                    "registry_relationship",
                    object_type,
                    reference.affected_object_id,
                    True,
                    0,
                    reference.summary,
                    consequence,
                    (reference_id,),
                    False,
                    True,
                    (reference.source_type,),
                )
            )

    if bundle.replacement_conflict:
        references = tuple(sorted(evidence_by_kind.get("rename_destination_conflict", ())))
        findings.append(
            _finding(
                "rename_destination_conflict",
                "critical",
                "exact",
                "destination_conflict",
                "entity",
                bundle.replacement_entity_id or "replacement",
                True,
                0,
                "The requested replacement entity ID already exists in current state or registry evidence.",
                "The rename is blocked; no overwrite safety is implied.",
                references,
                True,
                True,
                ("target_state", "entity_registry"),
            )
        )

    target_ref = tuple(sorted(evidence_by_kind.get("target_state", ())))
    registry_ref = tuple(sorted(evidence_by_kind.get("entity_registry_relationship", ())))
    if bundle.target.get("state_status") == "unavailable":
        findings.append(
            _finding(
                "target_currently_unavailable",
                "low",
                "exact",
                "target_state",
                "entity",
                bundle.entity_id,
                True,
                0,
                "The target currently reports unavailable.",
                "Current unavailability is not evidence that the entity is safe to change.",
                target_ref,
                False,
                True,
                ("target_state",),
            )
        )
    if bundle.target.get("disabled"):
        findings.append(
            _finding(
                "target_registry_disabled",
                "info",
                "exact",
                "target_registry_state",
                "entity",
                bundle.entity_id,
                True,
                0,
                "The target is disabled in the entity registry.",
                "Registry disablement is distinct from deletion and does not prove that references are safe.",
                registry_ref,
                False,
                True,
                ("entity_registry",),
            )
        )
    if (
        bundle.target.get("registry_entry_exists")
        and bundle.target.get("state_status") == "missing"
    ):
        findings.append(
            _finding(
                "target_missing_from_state_machine",
                "medium",
                "exact",
                "target_state",
                "entity",
                bundle.entity_id,
                True,
                0,
                "The registry entry exists but the entity is absent from the current state machine.",
                "The absence requires review and must not be treated as proof that removal is safe.",
                tuple(sorted(set(target_ref + registry_ref))),
                False,
                True,
                ("target_state", "entity_registry"),
            )
        )

    incomplete_refs = tuple(
        sorted(evidence_by_kind.get("source_coverage_incomplete", ()))
    )
    if incomplete_refs:
        findings.append(
            _finding(
                "source_coverage_incomplete",
                "medium",
                "limited",
                "coverage_limitation",
                "analysis",
                "source_coverage",
                True,
                0,
                "One or more required evidence sources were partial, unavailable, or unsupported.",
                "No clean safety conclusion can be drawn from incomplete required coverage.",
                incomplete_refs,
                False,
                True,
                tuple(
                    sorted(
                        item.source_type
                        for item in bundle.coverage
                        if item.required_for_assessment
                        and not item.assessment_complete
                    )
                ),
            )
        )

    return sorted(
        {item.finding_id: item for item in findings}.values(), key=_finding_sort_key
    )


def build_impact_groups(findings: Iterable[ImpactFinding]) -> list[ImpactAffectedObjectGroup]:
    values: dict[tuple[str, str, str], list[ImpactFinding]] = defaultdict(list)
    for finding in findings:
        values[
            (
                finding.affected_object_type,
                finding.affected_object_id,
                finding.consequence,
            )
        ].append(finding)
    groups = []
    for key, members in sorted(values.items()):
        object_type, object_id, consequence = key
        groups.append(
            ImpactAffectedObjectGroup(
                group_id=stable_id("impact_group", *key),
                affected_object_type=object_type,
                affected_object_id=object_id,
                consequence=consequence,
                finding_ids=tuple(sorted(item.finding_id for item in members)),
                evidence_references=tuple(
                    sorted(
                        {
                            reference
                            for item in members
                            for reference in item.evidence_references
                        }
                    )
                ),
                highest_severity=min(
                    (item.severity for item in members),
                    key=lambda value: SEVERITY_RANK[value],
                ),
                direct=all(item.direct for item in members),
                minimum_depth=min(item.dependency_depth for item in members),
            )
        )
    return sorted(
        groups,
        key=lambda item: (
            SEVERITY_RANK[item.highest_severity],
            item.affected_object_type,
            item.affected_object_id,
            item.group_id,
        ),
    )


def final_assessment(
    findings: list[ImpactFinding], bundle: ImpactEvidenceBundle
) -> str:
    rule_ids = {item.rule_id for item in findings}
    if "rename_destination_conflict" in rule_ids:
        return "blocking_impacts_found"
    substantive = rule_ids - {"source_coverage_incomplete"}
    if substantive:
        return "review_required"
    if not bundle.required_coverage_complete:
        return "no_known_impacts_with_incomplete_coverage"
    return "no_known_impacts_with_complete_coverage"


def remediation_checklist(
    operation: str, findings: list[ImpactFinding]
) -> list[str]:
    """Return inert advisory review prompts, never executable instructions."""

    rules = {item.rule_id for item in findings}
    values = [
        "Review the reported source coverage and unresolved evidence before deciding whether to proceed."
    ]
    if operation == "rename_entity" and "rename_reference_migration_required" in rules:
        values.append(
            "Review each cited consumer and decide how its reference should be migrated; automatic rewriting is not assumed."
        )
    if "rename_destination_conflict" in rules:
        values.append(
            "Choose a different unused destination identifier or resolve the existing destination through a separately governed process."
        )
    if operation in {"remove_entity", "disable_entity"}:
        values.append(
            "Review the affected consumers and expected unavailable, unknown, or missing-state behavior."
        )
    if "unresolved_dynamic_reference" in rules:
        values.append(
            "Manually inspect the cited dynamic references because this analysis cannot resolve their targets safely."
        )
    values.append(
        "Use the governed change workflow separately if a configuration change is later authorized."
    )
    return values[:8]


def _finding(
    rule_id,
    severity,
    confidence,
    impact_type,
    affected_object_type,
    affected_object_id,
    direct,
    depth,
    explanation,
    consequence,
    evidence_references,
    remediation_required,
    manual_review_required,
    source_coverage,
) -> ImpactFinding:
    references = tuple(sorted(set(evidence_references)))
    return ImpactFinding(
        finding_id=stable_id(
            "impact",
            rule_id,
            affected_object_type,
            affected_object_id,
            consequence,
            references,
        ),
        rule_id=rule_id,
        severity=severity,
        confidence=confidence,
        impact_type=impact_type,
        affected_object_type=affected_object_type,
        affected_object_id=affected_object_id,
        direct=direct,
        dependency_depth=max(0, min(int(depth), 3)),
        explanation=str(explanation)[:400],
        consequence=str(consequence)[:400],
        evidence_references=references,
        remediation_required=bool(remediation_required),
        manual_review_required=bool(manual_review_required),
        source_coverage=tuple(sorted(set(source_coverage))),
    )


def _direct_consequence(operation: str, source_type: str) -> str:
    if operation == "rename_entity":
        return f"The {source_type} may continue using the old entity ID until its reference is reviewed."
    if operation == "remove_entity":
        return f"The {source_type} may retain a stale reference after removal."
    return f"The {source_type} may observe unavailable, unknown, or absent-state behavior after disablement."


def _finding_sort_key(item: ImpactFinding):
    return (
        SEVERITY_RANK[item.severity],
        not item.direct,
        item.dependency_depth,
        item.affected_object_type,
        item.affected_object_id,
        item.rule_id,
        item.finding_id,
    )
