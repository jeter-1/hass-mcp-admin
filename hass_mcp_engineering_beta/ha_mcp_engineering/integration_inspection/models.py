"""Strict normalized evidence. Provider objects are never public response models."""

from typing import Annotated, Generic, Literal, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..errors import ErrorCode, GovernanceError


Reason = Literal[
    "source_unavailable", "access_denied", "command_unsupported", "integration_not_installed",
    "integration_not_loaded", "integration_identity_unverified", "integration_version_unsupported",
    "target_not_found", "ambiguous_target", "malformed_response", "invalid_field", "unknown_mode",
    "unknown_field_omitted", "dangling_area", "group_conflict", "registry_incomplete", "scope_incomplete",
    "response_bytes", "structural_budget", "record_limit", "output_bytes", "timeout", "snapshot_expired",
    "snapshot_evicted", "invalid_cursor", "identity_drift", "authority_unavailable", "redacted_by_policy",
    "excluded_by_policy", "freshness_unverified", "capacity_busy", "output_budget_unavailable",
]
Mode = Literal["armed_away", "armed_home", "armed_night", "armed_custom_bypass", "armed_vacation"]
Status = Literal["complete", "partial", "unavailable", "unsupported", "stale", "not_collected"]
FactStatus = Literal["observed", "inferred", "unavailable", "unsupported", "redacted", "stale", "not_collected"]
Precision = Literal["exact", "lower_bound", "unknown"]
Count = Annotated[int, Field(ge=0)]
Seconds = Annotated[int, Field(ge=0, le=2147483647)]
Id = Annotated[str, Field(min_length=1, max_length=128)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Ref(Strict):
    source_id: Id
    pointer: Annotated[str, Field(max_length=512)]


class Evidence(Ref):
    status: Literal["observed", "unavailable", "redacted", "unsupported"]
    value: bool | int | float | str | list[str] | None


T = TypeVar("T")


class Fact(Strict, Generic[T]):
    status: FactStatus
    value: T | None
    evidence: list[Ref]
    reason: Reason | None = None
    derivation_rule: Literal["configured_mode_eligibility_v1"] | None = None

    @model_validator(mode="after")
    def evidence_state(self):
        if self.status not in ("observed", "inferred") and self.value is not None:
            raise ValueError("Unavailable fact cannot carry a value.")
        if self.status == "observed" and not self.evidence:
            raise ValueError("Observed fact requires evidence.")
        return self


class Area(Strict):
    kind: Literal["area"] = "area"
    id: Id


class Master(Strict):
    kind: Literal["master"] = "master"


class Source(Strict):
    source_id: Id
    provider: Literal["direct_ha_api"] = "direct_ha_api"
    kind: Literal["manifest", "config_entries", "alarm_entities", "areas", "sensors", "general", "sensor_groups", "entity_registry"]
    request_id: Id
    captured_at: str
    duration_ms: float
    status: Status
    records_observed: Count | None
    records_retained: Count
    omitted_count: Count | None
    count_precision: Precision
    truncated: bool
    reason: Reason | None
    projection_sha256: str | None
    fallback_occurred: Literal[False] = False


class GeneralRecord(Strict):
    kind: Literal["general"] = "general"
    code_arm_required: Fact[bool]
    code_disarm_required: Fact[bool]
    code_mode_change_required: Fact[bool]
    disarm_after_trigger: Fact[bool]
    ignore_blocking_sensors_after_trigger: Fact[bool]
    master_enabled: Fact[bool]


class ModeRecord(Strict):
    kind: Literal["mode"] = "mode"
    area: Area
    mode: Mode
    enabled: Fact[bool]
    entry_time_seconds: Fact[Seconds]
    exit_time_seconds: Fact[Seconds]
    trigger_time_seconds: Fact[Seconds]


class Registry(Strict):
    status: Literal["resolved", "not_in_registry", "unavailable", "conflict"]
    current_entity_id: Id | None
    registry_entry_id: Id | None
    registry_disabled: bool | None
    evidence: list[Ref]


class Eligibility(Strict):
    mode: Mode
    result: Literal["eligible", "excluded", "indeterminate"]
    status: Literal["inferred"] = "inferred"
    derivation_rule: Literal["configured_mode_eligibility_v1"] = "configured_mode_eligibility_v1"
    evidence: list[Ref]


class SensorRecord(Strict):
    kind: Literal["sensor"] = "sensor"
    configured_entity_id: Id
    alarmo_area: Fact[Area]
    enabled: Fact[bool]
    type: Fact[Literal["door", "window", "motion", "tamper", "environmental", "other"]]
    configured_modes: Fact[list[Mode]]
    use_entry_delay: Fact[bool]
    use_exit_delay: Fact[bool]
    always_on: Fact[bool]
    arm_on_close: Fact[bool]
    allow_open: Fact[bool]
    trigger_unavailable: Fact[bool]
    auto_bypass: Fact[bool]
    auto_bypass_modes: Fact[list[Mode]]
    reported_group_id: Fact[Id]
    registry: Registry
    configured_mode_eligibility: list[Eligibility]


class GroupRecord(Strict):
    kind: Literal["sensor_group"] = "sensor_group"
    group_id: Id
    configured_members: Fact[list[Id]]
    timeout_seconds: Fact[Seconds]
    event_count: Fact[Seconds]
    includes_members_outside_target_area: bool | None


Record = Annotated[Union[GeneralRecord, ModeRecord, SensorRecord, GroupRecord], Field(discriminator="kind")]


class Gap(Strict):
    reason: Reason
    source_id: Id | None = None
    pointer: Annotated[str, Field(max_length=512)] | None = None


class Target(Strict):
    alarm_entity_id: Id
    config_entry_id: Id
    scope: Area | Master
    area_ids: list[Id]


class Compatibility(Strict):
    adapter_id: Literal["alarmo-inspection-v1"]
    adapter_contract_sha256: str
    reviewed_alarmo_source_commit: str
    observed_manifest_version: str
    artifact_provenance: Literal["not_observed_by_this_tool"]
    source_binding: Literal["reviewed_release_metadata_match"]
    core_generation: Count
    core_profile_ids: list[str]
    core_version_observed: str
    upstream_used: Literal[False]


class Membership(Strict):
    outcome: Literal["present", "empty", "partial", "unavailable", "unsupported", "stale"]
    configured_members_observed: Count | None
    configured_members_retained: Count
    total_in_scope: Count | None
    count_precision: Precision


class Privacy(Strict):
    policy: Literal["alarmo_allowlist_v1"]
    never_collected_categories: list[str]
    removed_categories: list[str]


class Freshness(Strict):
    capture_started_at: str
    capture_finished_at: str
    served_at: str
    expires_at: str
    atomic_snapshot: Literal[False]
    status: Literal["captured", "frozen_snapshot", "unverified", "stale"]
    manifest_bracket: Literal["match", "unavailable", "mismatch"]
    configuration_unchanged_during_capture: Literal["not_established"]


class Pagination(Strict):
    requested_limit: Count
    effective_limit: Count
    offset: Count
    returned: Count
    total_retained_records: Count
    omitted_records: Count | None
    has_more: bool
    next_cursor: str | None
    snapshot_fingerprint: str


class Truncation(Strict):
    occurred: bool
    reasons: list[Reason]
    omitted_count: Count | None


class Routing(Strict):
    provider: Literal["engineering"]
    data_providers: list[Literal["direct_ha_api"]]
    fallback_occurred: Literal[False]


class Inspection(Strict):
    model_version: Literal["integration-inspection-v1"]
    integration: Literal["alarmo"]
    target: Target
    compatibility: Compatibility
    assessment: Literal["complete", "partial", "unavailable", "unsupported", "stale"]
    complete_for: Literal["selected_allowlisted_configuration_fields_only"]
    membership: Membership
    records: list[Record]
    sources: list[Source]
    evidence_entries: list[Evidence]
    privacy: Privacy
    freshness: Freshness
    pagination: Pagination
    gaps: list[Gap]
    truncation: Truncation
    behavior_verification: Literal["not_performed"]
    routing: Routing


class InspectionError(GovernanceError):
    """Only a closed reason reaches error/audit serialization."""

    def __init__(self, reason: Reason):
        # Validation forbids exception text or provider-derived diagnostics.
        reason = Gap(reason=reason).reason
        code = (ErrorCode.PROVIDER_UNAVAILABLE if reason == "authority_unavailable" else
                ErrorCode.INVALID_CURSOR if reason in {"invalid_cursor", "snapshot_expired", "snapshot_evicted"}
                else ErrorCode.ANALYSIS_UNAVAILABLE)
        super().__init__(code, details={"reason": reason})
        self.reason = reason
