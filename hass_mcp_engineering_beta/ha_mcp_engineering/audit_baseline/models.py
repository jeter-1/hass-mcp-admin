"""Typed data-only models for offline automation-baseline comparison."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


BASELINE_SCHEMA = "automation-audit-baseline-v1"
LEGACY_BASELINE_SCHEMA = "ha-automation-configuration-baseline-v1"
COMPARISON_SCHEMA = "automation-audit-comparison-v1"
FINGERPRINT_MODEL = "automation-audit-config-json-python-v1"
FINGERPRINT_SERIALIZATION = "python-json-sorted-compact-ascii-v1"
LEGACY_DECLARED_FINGERPRINT_MODEL = "ha-automation-config-fingerprint-v1"
LEGACY_UNRESOLVED_FINGERPRINT_MODEL = "legacy-haab-config-fingerprint-unresolved-v1"
LEGACY_UNRESOLVED_SERIALIZATION = "legacy-reported-sorted-compact-utf8-unresolved-v1"


class Classification(str, Enum):
    UNCHANGED = "UNCHANGED"
    CHANGED = "CHANGED"
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class InstallationAssurance:
    status: str
    installation_id: str | None
    method: str
    limitations: tuple[str, ...] = ()

    @property
    def established(self) -> bool:
        return self.status == "established" and bool(self.installation_id)


@dataclass(frozen=True)
class InventoryScope:
    scope: str
    discovery_method: str
    completeness: str
    declared_count: int
    limit: int | None
    limit_reached: bool
    omitted_count: int | None
    limitations: tuple[str, ...] = ()

    @property
    def absence_is_authoritative(self) -> bool:
        return (
            self.completeness == "complete"
            and not self.limit_reached
            and self.omitted_count == 0
        )


@dataclass(frozen=True)
class FingerprintContract:
    model: str
    algorithm: str
    serialization: str
    input_scope: str
    object_key_order: str
    array_order: str
    unicode_escaping: str
    number_serialization: str
    non_finite_numbers: str
    excluded_fields: tuple[str, ...]
    raw_configuration_persisted: bool


@dataclass(frozen=True)
class RecordCoverage:
    completeness: str
    fallback_occurred: bool
    warning_count: int
    redacted: bool
    truncated: bool
    omitted: bool

    @property
    def comparison_complete(self) -> bool:
        return bool(
            self.completeness == "complete"
            and not self.fallback_occurred
            and self.warning_count == 0
            and not self.redacted
            and not self.truncated
            and not self.omitted
        )


@dataclass(frozen=True)
class AutomationRecord:
    configuration_id: str
    entity_id: str | None
    mapping_status: str
    configuration_status: str
    configuration_digest: str | None
    fingerprint_model: str | None
    collected_at: str | None
    collection_time_status: str
    provider: str | None
    coverage: RecordCoverage
    enabled_state: str | None
    enabled_collected_at: str | None
    enabled_collection_time_status: str

    @property
    def identity_verified(self) -> bool:
        return self.mapping_status == "verified"

    @property
    def configuration_comparison_ready(self) -> bool:
        return bool(
            self.identity_verified
            and self.configuration_status == "readable"
            and self.configuration_digest
            and self.fingerprint_model
            and self.coverage.comparison_complete
        )


@dataclass(frozen=True)
class Baseline:
    schema: str
    baseline_id: str
    source_artifact_sha256: str | None
    source_baseline_id: str | None
    capture_start: str
    capture_end: str
    capture_non_atomic: bool
    installation: InstallationAssurance
    inventory: InventoryScope
    fingerprint_contract: FingerprintContract
    records: tuple[AutomationRecord, ...]
    consistency: dict[str, Any] = field(default_factory=dict)
    authority: dict[str, Any] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()
    structural_assertions: dict[str, Any] = field(default_factory=dict)

    @property
    def records_by_id(self) -> dict[str, AutomationRecord]:
        return {record.configuration_id: record for record in self.records}


@dataclass(frozen=True)
class ComparisonRecord:
    configuration_id: str
    classification: Classification
    reasons: tuple[str, ...]
    earlier_entity_id: str | None
    later_entity_id: str | None
    entity_id_changed: bool
    earlier_enabled_state: str | None
    later_enabled_state: str | None
    enabled_state_changed: bool
    earlier_digest: str | None
    later_digest: str | None

    def public(self) -> dict[str, Any]:
        return {
            "configuration_id": self.configuration_id,
            "classification": self.classification.value,
            "reasons": list(self.reasons),
            "entity_id": {
                "earlier": self.earlier_entity_id,
                "later": self.later_entity_id,
                "changed": self.entity_id_changed,
            },
            "enabled_state": {
                "earlier": self.earlier_enabled_state,
                "later": self.later_enabled_state,
                "changed": self.enabled_state_changed,
            },
            "configuration_digest": {
                "earlier": self.earlier_digest,
                "later": self.later_digest,
            },
        }


@dataclass(frozen=True)
class CaptureInterval:
    started_at: str
    ended_at: str
    non_atomic: bool

    def public(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "non_atomic": self.non_atomic,
        }


@dataclass(frozen=True)
class ComparisonReport:
    earlier_baseline_id: str
    later_baseline_id: str
    earlier_capture: CaptureInterval
    later_capture: CaptureInterval
    comparison_reasons: tuple[str, ...]
    installation_comparable: bool
    installation_reason: str | None
    inventory_scope_comparable: bool
    authority_continuity_comparable: bool
    earlier_inventory_absence_authoritative: bool
    later_inventory_absence_authoritative: bool
    fingerprint_contract_compatible: bool
    records: tuple[ComparisonRecord, ...]
    limitations: tuple[str, ...]

    @property
    def comparison_eligible(self) -> bool:
        return not self.comparison_reasons

    @property
    def counts(self) -> dict[str, int]:
        counts = {item.value: 0 for item in Classification}
        for record in self.records:
            counts[record.classification.value] += 1
        counts["TOTAL"] = len(self.records)
        return counts
