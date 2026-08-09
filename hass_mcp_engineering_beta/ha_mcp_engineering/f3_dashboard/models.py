"""Immutable value models for F3-B planning and readback verification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class PatchKind(str, Enum):
    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"


class RiskCategory(str, Enum):
    DISPLAY_ONLY = "entity_display_only"
    NAVIGATION = "navigation"
    MORE_INFO = "more_info"
    TOGGLE = "toggle"
    SERVICE_ACTION = "service_or_action_invocation"
    CONFIRMATION = "confirmation_protected_action"
    HIGH_CONSEQUENCE = "high_consequence_action"
    DESTRUCTIVE_ADMIN = "destructive_administrative_action"
    OPAQUE_CUSTOM = "opaque_custom_card_action"
    TEMPLATE_OR_CONDITIONAL = "templated_or_conditional_action"
    UNKNOWN = "unknown_action_semantics"


class RiskDisposition(str, Enum):
    STANDARD_REVIEW = "standard_review"
    ELEVATED_REVIEW = "elevated_manual_review"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class AtomicityStatus(str, Enum):
    BLOCKED = "blocked"
    OPERATOR_ACCEPTED_NON_ATOMIC = "operator_accepted_non_atomic"
    PROVEN_ATOMIC = "proven_atomic"
    AUTHORITATIVE_WRITER_EXCLUSION = "authoritative_writer_exclusion"


class VerificationOutcome(str, Enum):
    SUCCEEDED_VERIFIED = "succeeded_verified"
    FAILED_CONFIRMED_NO_WRITE = "failed_confirmed_no_write"
    VERIFICATION_MISMATCH = "verification_mismatch"
    FAILED_POST_DISPATCH = "failed_post_dispatch"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


@dataclass(frozen=True)
class DashboardInventoryRow:
    url_path: str
    mode: str
    dashboard_id: str | None = None


@dataclass(frozen=True)
class DashboardPreread:
    """Exact internal read result; never a public sanitized projection."""

    inventory: tuple[DashboardInventoryRow, ...]
    canonical_url_path: str
    configuration: dict[str, Any]
    config_hash: str
    completeness: str
    configuration_returned: bool
    sanitized: bool
    truncated: bool
    preread_at: str
    upstream_version: str
    protocol_version: str
    compatibility_entry: str
    dashboard_contract_model: str


@dataclass(frozen=True)
class RawDashboardEvidence:
    model: str
    canonical_url_path: str
    storage_mode_confirmed: bool
    configuration: dict[str, Any]
    upstream_config_hash: str
    engineering_config_sha256: str
    serialized_size_bytes: int
    preread_at: str
    upstream_version: str
    protocol_version: str
    compatibility_entry: str
    dashboard_contract_model: str
    completeness: str


@dataclass(frozen=True)
class PatchOperation:
    operation_id: str
    operation: PatchKind
    path: str
    tokens: tuple[str, ...]
    value_present: bool
    value: Any = None


@dataclass(frozen=True)
class PatchEffect:
    operation_id: str
    operation: PatchKind
    path: str
    previous_present: bool
    previous_value: Any
    proposed_present: bool
    proposed_value: Any
    leaf_change_count: int


@dataclass(frozen=True)
class PatchCompilation:
    model: str
    operations: tuple[PatchOperation, ...]
    effects: tuple[PatchEffect, ...]
    resulting_configuration: dict[str, Any]
    preread_sha256: str
    canonical_patch_sha256: str
    resulting_sha256: str
    resulting_upstream_config_hash: str
    serialized_patch_bytes: int
    resulting_size_bytes: int
    configuration_growth_bytes: int
    semantic_leaf_change_count: int


@dataclass(frozen=True)
class ValueSummary:
    data_role: str
    value_type: str
    present: bool
    redacted: bool
    truncated: bool
    preview: str | None
    item_count: int | None = None


@dataclass(frozen=True)
class SemanticDiffEntry:
    operation_id: str
    path: str
    operation: str
    previous: ValueSummary
    proposed: ValueSummary
    context: tuple[str, ...]
    leaf_change_count: int
    risk_flags: tuple[str, ...]


@dataclass(frozen=True)
class SemanticDiff:
    model: str
    entries: tuple[SemanticDiffEntry, ...]
    leaf_change_count: int
    truncated: bool
    preread_sha256: str
    patch_sha256: str
    resulting_sha256: str
    semantic_diff_sha256: str
    serialized_size_bytes: int


@dataclass(frozen=True)
class RiskFinding:
    category: RiskCategory
    path: str
    action: str | None
    service: str | None
    entity_domain: str | None
    confirmation_present: bool
    semantic_binding_sha256: str
    introduced_or_changed: bool
    reason_code: str


@dataclass(frozen=True)
class DashboardRiskEvidence:
    model: str
    disposition: RiskDisposition
    findings: tuple[RiskFinding, ...]
    manual_review_required: bool
    opaque_custom_action_count: int
    high_consequence_action_count: int
    destructive_admin_action_count: int
    evidence_sha256: str


@dataclass(frozen=True)
class AtomicityDecision:
    model: str
    status: AtomicityStatus
    mechanism: str | None
    reason_codes: tuple[str, ...]
    exact_upstream_release: str
    home_assistant_release: str
    source_evidence_sha256: str


@dataclass(frozen=True)
class ProviderRuntimeEvidence:
    upstream_version: str
    protocol_version: str
    compatibility_entry: str
    source_commit: str
    tool_name: str
    input_schema_fingerprint: str
    annotation_fingerprint: str
    description_fingerprint: str
    output_contract_fingerprint: str
    runtime_contract_fingerprint: str
    policy_classification: str


@dataclass(frozen=True)
class ProviderAdmission:
    admitted_for_planning: bool
    executable: bool
    exact_release: str
    compatibility_entry: str
    provider_contract_hash: str
    diagnostic_codes: tuple[str, ...]


@dataclass(frozen=True)
class ProviderPlanningProjection:
    tool_name: str
    target_url_path: str
    current_config_hash: str
    resulting_configuration_sha256: str
    resulting_upstream_config_hash: str
    resulting_size_bytes: int
    binding_sha256: str
    potential_ephemeral_argument_names: tuple[str, ...]
    prohibited_argument_names: tuple[str, ...]
    executable: bool
    blocked_reason: str


@dataclass(frozen=True)
class ProviderResponseEvidence:
    response_received: bool
    success_claimed: bool
    write_committed_claimed: bool
    post_write_verified_claimed: bool
    upstream_config_hash: str | None
    response_evidence_sha256: str
    diagnostic_codes: tuple[str, ...]


@dataclass(frozen=True)
class DashboardUpdateProposal:
    model: str
    plan_id: str
    title: str
    description: str
    created_at: str
    expires_at: str
    requested_by: str
    target_type: str
    target_id: str
    raw_evidence: RawDashboardEvidence
    compilation: PatchCompilation
    semantic_diff: SemanticDiff
    risk: DashboardRiskEvidence
    provider_admission: ProviderAdmission
    provider_projection: ProviderPlanningProjection
    atomicity: AtomicityDecision
    required_approval: str
    rollback_available: bool
    executable: bool
    lock_keys: tuple[str, ...]
    proposal_sha256: str


@dataclass(frozen=True)
class DashboardArtifactRecord:
    schema: str
    plan_id: str
    created_at: str
    expires_at: str
    proposal_sha256: str
    payload_sha256: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class DashboardVerification:
    model: str
    outcome: VerificationOutcome
    verified: bool | None
    canonical_url_path: str
    resulting_upstream_config_hash: str | None
    resulting_engineering_sha256: str | None
    mismatch_paths: tuple[str, ...]
    untouched_fields_preserved: bool | None
    observation_complete: bool
    diagnostic_codes: tuple[str, ...]
    evidence_sha256: str


@dataclass(frozen=True)
class DashboardPreflight:
    eligible: bool
    stale: bool
    plan_expired: bool
    approval_bundle_validated: bool
    complete_lock_keys_present: bool
    fencing_validated: bool
    atomicity_validated: bool
    observed_upstream_config_hash: str | None
    observed_engineering_sha256: str | None
    diagnostic_codes: tuple[str, ...]
