"""Deterministic, fail-closed policy for reviewed upstream MCP tools."""

from __future__ import annotations

from collections.abc import Sequence
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any


POLICY_PATH = Path(__file__).with_name("upstream_tool_policy.json")
RELEASE_REGISTRY_PATH = Path(__file__).with_name(
    "upstream_release_registry.json"
)
POLICY_SCHEMA_VERSION = 1
RELEASE_REGISTRY_FORMAT_VERSION = 2
REVIEWED_CAPTURE_FORMAT_VERSION = 1
REVIEWED_UPSTREAM_SERVER = "ha-mcp"
REVIEWED_UPSTREAM_VERSION = "7.14.1"
REVIEWED_UPSTREAM_PROTOCOL = "2025-03-26"
UPSTREAM_SOURCE_REPOSITORY = "https://github.com/homeassistant-ai/ha-mcp"
_POLICY_RESOURCE = re.compile(
    r"^upstream_tool_policy(?:_[0-9]+_[0-9]+_[0-9]+)?\.json$"
)
_CAPTURE_RESOURCE = re.compile(
    r"^docs/evidence/upstream-read-compatibility/"
    r"ha-mcp-(?:0|[1-9][0-9]{0,3})\."
    r"(?:0|[1-9][0-9]{0,3})\."
    r"(?:0|[1-9][0-9]{0,3})\.json$"
)
MAX_RUNTIME_DESCRIPTION_BYTES = 8_192
MAX_RUNTIME_ANNOTATION_TITLE_BYTES = 512
_RUNTIME_DESCRIPTION_FINGERPRINT_DOMAIN = (
    b"ha-mcp-engineering/runtime-description/v1\0"
)
_RUNTIME_ANNOTATION_FINGERPRINT_DOMAIN = (
    b"ha-mcp-engineering/runtime-safety-annotations/v1\0"
)
RUNTIME_SAFETY_ANNOTATION_FIELDS = (
    "readOnlyHint",
    "destructiveHint",
    "idempotentHint",
    "openWorldHint",
)
RUNTIME_CONTRACT_FINGERPRINT_MODEL_V1 = "mcp-full-tool-descriptor-v1"
RUNTIME_CONTRACT_FINGERPRINT_MODEL_V2 = (
    "ha-mcp-operational-tool-descriptor-v2"
)
RUNTIME_POLICY_STATE_FINGERPRINT_MODEL_V1 = (
    "ha-mcp-policy-runtime-state-v1"
)
REVIEWED_NORMALIZED_CATALOG_FINGERPRINT_MODEL_V1 = (
    "ha-mcp-reviewed-normalized-catalog-v1"
)
RUNTIME_CONTRACT_FINGERPRINT_MODELS = frozenset(
    {
        RUNTIME_CONTRACT_FINGERPRINT_MODEL_V1,
        RUNTIME_CONTRACT_FINGERPRINT_MODEL_V2,
    }
)
MAX_RUNTIME_POLICY_RULE_COUNT = 10_000
RUNTIME_POLICY_DYNAMIC_PATHS = frozenset(
    {
        "/_meta/ha_mcp/policy/deployment",
        "/_meta/ha_mcp/policy/enabled",
        "/_meta/ha_mcp/policy/live",
        "/_meta/ha_mcp/policy/rules",
    }
)
MAX_REVIEWED_CATALOG_DIAGNOSTICS = 16
REVIEWED_CATALOG_COMPONENTS = (
    "classification",
    "input_schema_fingerprint",
    "description_fingerprint",
    "annotation_fingerprint",
    "output_contract_fingerprint",
    "runtime_contract_fingerprint",
)
RUNTIME_CONTRACT_DIAGNOSTIC_PATHS = (
    "/title",
    "/annotations/title",
    "/_meta/fastmcp/tags",
    "/_meta/ha_mcp/llm_api_exposed",
    "/_meta/ha_mcp/pinned",
    "/_meta/ha_mcp/policy/deployment",
    "/_meta/ha_mcp/policy/enabled",
    "/_meta/ha_mcp/policy/live",
    "/_meta/ha_mcp/policy/rules",
)
CLASSIFICATIONS = frozenset(
    {
        "automatic_read",
        "held_for_canary",
        "mixed_or_requires_wrapper",
        "persistent_write",
        "physical_or_high_risk_action",
        "prohibited",
        "unsupported",
    }
)
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SEMANTIC_VERSION = re.compile(
    r"^(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})\."
    r"(?:0|[1-9][0-9]{0,3})$"
)
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class UpstreamToolPolicyError(ValueError):
    """A committed policy document is malformed or internally inconsistent."""


def _reject_duplicate_json_members(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for name, item in pairs:
        if name in value:
            raise UpstreamToolPolicyError("policy_duplicate_json_member")
        value[name] = item
    return value


def _reject_nonfinite_json_constant(_value: str) -> None:
    raise UpstreamToolPolicyError("policy_nonfinite_json_constant")


def _load_strict_json(path: Path, *, error: str) -> Any:
    try:
        raw = path.read_bytes()
        return json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_members,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        UpstreamToolPolicyError,
    ) as exc:
        raise UpstreamToolPolicyError(error) from exc


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def schema_fingerprint(schema: Any) -> str:
    return hashlib.sha256(canonical_json(schema)).hexdigest()


def catalog_fingerprint(tools: list[dict[str, Any]]) -> str:
    ordered = sorted(tools, key=lambda item: str(item.get("name", "")))
    return hashlib.sha256(canonical_json(ordered)).hexdigest()


def runtime_contract_fingerprint(
    tool: dict[str, Any],
    *,
    model: str = RUNTIME_CONTRACT_FINGERPRINT_MODEL_V1,
) -> str:
    """Fingerprint one runtime descriptor under an explicit admission model."""

    if model == RUNTIME_CONTRACT_FINGERPRINT_MODEL_V1:
        return schema_fingerprint(tool)
    if model != RUNTIME_CONTRACT_FINGERPRINT_MODEL_V2:
        raise UpstreamToolPolicyError(
            "runtime_contract_fingerprint_model_invalid"
        )
    normalized = json.loads(canonical_json(tool))
    meta = normalized.get("_meta")
    if not isinstance(meta, dict):
        return schema_fingerprint(normalized)
    ha_mcp = meta.get("ha_mcp")
    if not isinstance(ha_mcp, dict):
        return schema_fingerprint(normalized)
    policy = ha_mcp.get("policy")
    ha_mcp["policy"] = runtime_policy_state_fingerprint_projection(policy)
    return schema_fingerprint(normalized)


def runtime_policy_state_fingerprint_projection(
    policy: Any,
) -> dict[str, Any]:
    """Project only validity of the exact reviewed dynamic policy shape."""

    policy_valid = (
        isinstance(policy, dict)
        and set(policy) == {"deployment", "enabled", "live", "rules"}
        and policy.get("deployment") in {"standalone", "addon"}
        and isinstance(policy.get("enabled"), bool)
        and isinstance(policy.get("live"), bool)
        and isinstance(policy.get("rules"), int)
        and not isinstance(policy.get("rules"), bool)
        and 0 <= policy["rules"] <= MAX_RUNTIME_POLICY_RULE_COUNT
    )
    return {
        "fingerprint_model": RUNTIME_POLICY_STATE_FINGERPRINT_MODEL_V1,
        "valid": policy_valid,
    }


def runtime_contract_field_fingerprints(
    tool: dict[str, Any],
) -> dict[str, str]:
    """Fingerprint bounded diagnostic fields without publishing their values."""

    values: dict[str, str] = {}
    for pointer in RUNTIME_CONTRACT_DIAGNOSTIC_PATHS:
        current: Any = tool
        present = True
        for name in pointer.lstrip("/").split("/"):
            if not isinstance(current, dict) or name not in current:
                present = False
                current = None
                break
            current = current[name]
        values[pointer] = schema_fingerprint(
            {"present": present, "value": current if present else None}
        )
    return values


def runtime_description_fingerprint(value: Any) -> str | None:
    """Fingerprint one exact, bounded runtime description fail closed."""

    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_RUNTIME_DESCRIPTION_BYTES
    ):
        return None
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return None
    if len(encoded) > MAX_RUNTIME_DESCRIPTION_BYTES:
        return None
    return hashlib.sha256(
        _RUNTIME_DESCRIPTION_FINGERPRINT_DOMAIN + encoded
    ).hexdigest()


def runtime_annotation_fingerprint(value: Any) -> str | None:
    """Fingerprint exact safe wire annotations, preserving field presence."""

    if not isinstance(value, dict):
        return None
    allowed = {*RUNTIME_SAFETY_ANNOTATION_FIELDS, "title"}
    if set(value) - allowed:
        return None
    title = value.get("title")
    if "title" in value:
        if not isinstance(title, str) or not title:
            return None
        try:
            title_bytes = title.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            return None
        if len(title_bytes) > MAX_RUNTIME_ANNOTATION_TITLE_BYTES:
            return None
    projection: dict[str, dict[str, bool | None]] = {}
    for name in RUNTIME_SAFETY_ANNOTATION_FIELDS:
        present = name in value
        observed = value.get(name)
        if present and not isinstance(observed, bool):
            return None
        projection[name] = {
            "present": present,
            "value": observed if present else None,
        }
    read_only = projection["readOnlyHint"]
    destructive = projection["destructiveHint"]
    if not read_only["present"] or read_only["value"] is not True:
        return None
    if destructive["present"] and destructive["value"] is not False:
        return None
    return hashlib.sha256(
        _RUNTIME_ANNOTATION_FINGERPRINT_DOMAIN
        + canonical_json(projection)
    ).hexdigest()


@dataclass(frozen=True)
class ReviewedToolAnnotations:
    """Binary-owned MCP annotations reviewed with an exact upstream schema."""

    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool

    @classmethod
    def from_mapping(cls, value: Any) -> "ReviewedToolAnnotations":
        if not isinstance(value, dict) or set(value) != {
            "readOnlyHint",
            "destructiveHint",
            "idempotentHint",
            "openWorldHint",
        }:
            raise UpstreamToolPolicyError("policy_annotations_fields_invalid")
        if any(not isinstance(item, bool) for item in value.values()):
            raise UpstreamToolPolicyError("policy_annotations_value_invalid")
        return cls(
            read_only=value["readOnlyHint"],
            destructive=value["destructiveHint"],
            idempotent=value["idempotentHint"],
            open_world=value["openWorldHint"],
        )


@dataclass(frozen=True)
class UpstreamToolPolicyEntry:
    upstream_name: str
    exposed_name: str
    description: str
    classification: str
    input_schema_fingerprint: str
    reason: str
    collision_status: str
    collision_policy: str
    argument_restrictions: tuple[str, ...]
    response_limit_bytes: int
    timeout_seconds: float
    source_evidence: tuple[str, ...]
    reviewed_annotations: ReviewedToolAnnotations

    @classmethod
    def from_mapping(cls, value: Any) -> "UpstreamToolPolicyEntry":
        if not isinstance(value, dict):
            raise UpstreamToolPolicyError("policy_entry_invalid")
        expected = {
            "upstream_name",
            "exposed_name",
            "description",
            "classification",
            "input_schema_fingerprint",
            "reason",
            "collision_status",
            "collision_policy",
            "argument_restrictions",
            "response_limit_bytes",
            "timeout_seconds",
            "source_evidence",
            "reviewed_annotations",
        }
        if set(value) != expected:
            raise UpstreamToolPolicyError("policy_entry_fields_invalid")
        upstream_name = value["upstream_name"]
        exposed_name = value["exposed_name"]
        classification = value["classification"]
        fingerprint = value["input_schema_fingerprint"]
        if not isinstance(upstream_name, str) or not _TOOL_NAME.fullmatch(upstream_name):
            raise UpstreamToolPolicyError("policy_upstream_name_invalid")
        if not isinstance(exposed_name, str) or not _TOOL_NAME.fullmatch(exposed_name):
            raise UpstreamToolPolicyError("policy_exposed_name_invalid")
        if classification not in CLASSIFICATIONS:
            raise UpstreamToolPolicyError("policy_classification_invalid")
        if not isinstance(fingerprint, str) or not _HEX_64.fullmatch(fingerprint):
            raise UpstreamToolPolicyError("policy_schema_fingerprint_invalid")
        description = value["description"]
        reason = value["reason"]
        if not isinstance(description, str) or not 1 <= len(description) <= 500:
            raise UpstreamToolPolicyError("policy_description_invalid")
        if not isinstance(reason, str) or not 1 <= len(reason) <= 1_000:
            raise UpstreamToolPolicyError("policy_reason_invalid")
        if value["collision_status"] not in {"none", "collides"}:
            raise UpstreamToolPolicyError("policy_collision_status_invalid")
        if value["collision_policy"] != "alias_upstream_on_collision":
            raise UpstreamToolPolicyError("policy_collision_policy_invalid")
        restrictions = value["argument_restrictions"]
        evidence = value["source_evidence"]
        if not isinstance(restrictions, list) or any(
            not isinstance(item, str) or len(item) > 256 for item in restrictions
        ):
            raise UpstreamToolPolicyError("policy_argument_restrictions_invalid")
        if not isinstance(evidence, list) or not evidence or any(
            not isinstance(item, str) or not 1 <= len(item) <= 512 for item in evidence
        ):
            raise UpstreamToolPolicyError("policy_source_evidence_invalid")
        response_limit = value["response_limit_bytes"]
        timeout = value["timeout_seconds"]
        if isinstance(response_limit, bool) or not isinstance(response_limit, int):
            raise UpstreamToolPolicyError("policy_response_limit_invalid")
        if not 4_096 <= response_limit <= 1_000_000:
            raise UpstreamToolPolicyError("policy_response_limit_invalid")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise UpstreamToolPolicyError("policy_timeout_invalid")
        if not 1 <= float(timeout) <= 300:
            raise UpstreamToolPolicyError("policy_timeout_invalid")
        reviewed_annotations = ReviewedToolAnnotations.from_mapping(
            value["reviewed_annotations"]
        )
        if classification == "automatic_read" and (
            not reviewed_annotations.read_only
            or reviewed_annotations.destructive
        ):
            raise UpstreamToolPolicyError("policy_automatic_read_annotations_invalid")
        return cls(
            upstream_name=upstream_name,
            exposed_name=exposed_name,
            description=description,
            classification=classification,
            input_schema_fingerprint=fingerprint,
            reason=reason,
            collision_status=value["collision_status"],
            collision_policy=value["collision_policy"],
            argument_restrictions=tuple(restrictions),
            response_limit_bytes=response_limit,
            timeout_seconds=float(timeout),
            source_evidence=tuple(evidence),
            reviewed_annotations=reviewed_annotations,
        )


@dataclass(frozen=True)
class UpstreamToolPolicy:
    schema_version: int
    upstream_server: str
    reviewed_upstream_version: str
    reviewed_source_tag: str
    reviewed_source_commit: str
    reviewed_stock_catalog_tool_count: int
    reviewed_stock_catalog_fingerprint: str
    reviewed_runtime_description_fingerprints: tuple[tuple[str, str], ...]
    reviewed_runtime_annotation_fingerprints: tuple[tuple[str, str], ...]
    reviewed_runtime_output_schema_fingerprints: tuple[tuple[str, str], ...]
    tools: tuple[UpstreamToolPolicyEntry, ...]

    @property
    def by_name(self) -> dict[str, UpstreamToolPolicyEntry]:
        return {entry.upstream_name: entry for entry in self.tools}

    @property
    def classification_counts(self) -> dict[str, int]:
        counts = Counter(entry.classification for entry in self.tools)
        return {
            name: counts.get(name, 0)
            for name in sorted(CLASSIFICATIONS)
            if name != "held_for_canary" or counts.get(name, 0)
        }

    @property
    def reviewed_runtime_description_fingerprints_by_name(
        self,
    ) -> dict[str, str]:
        return dict(self.reviewed_runtime_description_fingerprints)

    @property
    def reviewed_runtime_annotation_fingerprints_by_name(
        self,
    ) -> dict[str, str]:
        return dict(self.reviewed_runtime_annotation_fingerprints)

    @property
    def reviewed_runtime_output_schema_fingerprints_by_name(
        self,
    ) -> dict[str, str]:
        return dict(self.reviewed_runtime_output_schema_fingerprints)


@dataclass(frozen=True)
class ReviewedReleaseToolContract:
    """Deterministic evidence for one tool in one reviewed upstream release."""

    input_schema_fingerprint: str
    description_fingerprint: str
    annotation_fingerprint: str
    output_contract_fingerprint: str
    runtime_contract_fingerprint: str
    runtime_contract_field_fingerprints: tuple[tuple[str, str], ...]
    policy_classification: str
    reviewed_automatic_read: bool
    quarantine_reason: str | None

    @classmethod
    def from_mapping(cls, value: Any) -> "ReviewedReleaseToolContract":
        expected = {
            "input_schema_fingerprint",
            "description_fingerprint",
            "annotation_fingerprint",
            "output_contract_fingerprint",
            "runtime_contract_fingerprint",
            "runtime_contract_field_fingerprints",
            "policy_classification",
            "reviewed_automatic_read",
            "quarantine_reason",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise UpstreamToolPolicyError(
                "registry_tool_contract_fields_invalid"
            )
        for name in (
            "input_schema_fingerprint",
            "description_fingerprint",
            "annotation_fingerprint",
            "output_contract_fingerprint",
            "runtime_contract_fingerprint",
        ):
            item = value[name]
            if not isinstance(item, str) or not _HEX_64.fullmatch(item):
                raise UpstreamToolPolicyError(
                    "registry_tool_contract_fingerprint_invalid"
                )
        diagnostic_fields = value["runtime_contract_field_fingerprints"]
        if (
            not isinstance(diagnostic_fields, dict)
            or set(diagnostic_fields) != set(RUNTIME_CONTRACT_DIAGNOSTIC_PATHS)
            or any(
                not isinstance(item, str) or not _HEX_64.fullmatch(item)
                for item in diagnostic_fields.values()
            )
        ):
            raise UpstreamToolPolicyError(
                "registry_tool_contract_diagnostic_fingerprints_invalid"
            )
        classification = value["policy_classification"]
        if classification not in CLASSIFICATIONS:
            raise UpstreamToolPolicyError(
                "registry_tool_contract_classification_invalid"
            )
        automatic = value["reviewed_automatic_read"]
        if not isinstance(automatic, bool):
            raise UpstreamToolPolicyError(
                "registry_tool_contract_decision_invalid"
            )
        quarantine = value["quarantine_reason"]
        if quarantine is not None and (
            not isinstance(quarantine, str)
            or not 1 <= len(quarantine) <= 256
        ):
            raise UpstreamToolPolicyError(
                "registry_tool_contract_quarantine_invalid"
            )
        if automatic != (classification == "automatic_read"):
            raise UpstreamToolPolicyError(
                "registry_tool_contract_decision_invalid"
            )
        if automatic and quarantine is not None:
            raise UpstreamToolPolicyError(
                "registry_tool_contract_quarantine_invalid"
            )
        if not automatic and quarantine is None:
            raise UpstreamToolPolicyError(
                "registry_tool_contract_quarantine_invalid"
            )
        return cls(
            input_schema_fingerprint=value["input_schema_fingerprint"],
            description_fingerprint=value["description_fingerprint"],
            annotation_fingerprint=value["annotation_fingerprint"],
            output_contract_fingerprint=value[
                "output_contract_fingerprint"
            ],
            runtime_contract_fingerprint=value[
                "runtime_contract_fingerprint"
            ],
            runtime_contract_field_fingerprints=tuple(
                sorted(diagnostic_fields.items())
            ),
            policy_classification=classification,
            reviewed_automatic_read=automatic,
            quarantine_reason=quarantine,
        )


@dataclass(frozen=True)
class ReviewedUpstreamRelease:
    """One exact, human-reviewed upstream release and its compiled policy."""

    entry_id: str
    server_name: str
    version: str
    allowed_protocol_versions: tuple[str, ...]
    source_repository: str
    release_tag: str
    source_commit: str
    image_index_digest: str
    architecture_image_digests: tuple[tuple[str, str], ...]
    image_revision: str
    advertised_tool_count: int
    catalog_fingerprint: str
    runtime_contract_fingerprint_model: str
    strict_full_contract_fingerprint: str | None
    strict_full_contract_fingerprint_model: str | None
    addon_artifact_digests: tuple[
        tuple[str, tuple[tuple[str, str], ...]], ...
    ]
    capture_resource: str
    capture_sha256: str
    capture_format_version: int
    policy_resource: str
    policy_sha256: str
    review_provenance: tuple[str, ...]
    review_date: str
    dashboard_attestation_status: str
    dashboard_attestation_entry_id: str | None
    dashboard_attestation_fingerprint: str | None
    dashboard_compiled_constraints_fingerprint: str | None
    error_contract_fingerprint: str
    entity_lookup_missing_resource_status: str
    tool_contracts: tuple[tuple[str, ReviewedReleaseToolContract], ...]
    policy: UpstreamToolPolicy

    @property
    def tool_contracts_by_name(
        self,
    ) -> dict[str, ReviewedReleaseToolContract]:
        return dict(self.tool_contracts)

    @property
    def architecture_image_digests_by_platform(self) -> dict[str, str]:
        return dict(self.architecture_image_digests)

    @property
    def addon_artifact_digests_by_platform(
        self,
    ) -> dict[str, dict[str, str]]:
        return {
            platform: dict(digests)
            for platform, digests in self.addon_artifact_digests
        }


@dataclass(frozen=True)
class ReviewedUpstreamReleaseRegistry:
    """Source-controlled reviewed release authority; never upstream supplied."""

    registry_format_version: int
    default_version: str
    releases: tuple[ReviewedUpstreamRelease, ...]

    @property
    def by_version(self) -> dict[str, ReviewedUpstreamRelease]:
        return {entry.version: entry for entry in self.releases}

    @property
    def supported_versions(self) -> tuple[str, ...]:
        return tuple(entry.version for entry in self.releases)

    @property
    def default_release(self) -> ReviewedUpstreamRelease:
        return self.by_version[self.default_version]


@dataclass(frozen=True)
class ReviewedCatalogToolMismatch:
    """One bounded, value-free mismatch in a reviewed catalog."""

    tool_name: str
    components: tuple[str, ...]
    runtime_contract_diff_fields: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "components": list(self.components),
            "runtime_contract_diff_fields": list(
                self.runtime_contract_diff_fields
            ),
        }


@dataclass(frozen=True)
class ReviewedCatalogValidation:
    """Fail-closed exact-release validation for one complete tools catalog."""

    selected_compatibility_entry_id: str
    observed_server_name: str
    observed_upstream_version: str
    observed_protocol_version: str
    runtime_contract_fingerprint_model: str
    aggregate_fingerprint_model: str
    expected_tool_count: int
    observed_tool_count: int
    reviewed_accounted_count: int
    missing_tool_count: int
    missing_tools: tuple[str, ...]
    additional_tool_count: int
    additional_tools: tuple[str, ...]
    duplicated_tool_count: int
    duplicated_tools: tuple[str, ...]
    unreviewed_tool_count: int
    unreviewed_tools: tuple[str, ...]
    invalid_descriptor_count: int
    classification_mismatch_count: int
    classification_mismatches: tuple[str, ...]
    component_mismatch_counts: tuple[tuple[str, int], ...]
    mismatch_diagnostics: tuple[ReviewedCatalogToolMismatch, ...]
    diagnostics_truncated: bool
    reviewed_standalone_raw_catalog_fingerprint: str
    observed_raw_catalog_fingerprint: str | None
    expected_normalized_catalog_fingerprint: str | None
    normalized_catalog_fingerprint: str | None
    validation_status: str

    @property
    def valid(self) -> bool:
        return self.validation_status == "accepted_exact"

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected_compatibility_entry_id": (
                self.selected_compatibility_entry_id
            ),
            "observed_server_name": self.observed_server_name,
            "observed_upstream_version": self.observed_upstream_version,
            "observed_protocol_version": self.observed_protocol_version,
            "runtime_contract_fingerprint_model": (
                self.runtime_contract_fingerprint_model
            ),
            "aggregate_fingerprint_model": self.aggregate_fingerprint_model,
            "expected_tool_count": self.expected_tool_count,
            "observed_tool_count": self.observed_tool_count,
            "reviewed_accounted_count": self.reviewed_accounted_count,
            "missing_tool_count": self.missing_tool_count,
            "missing_tools": list(self.missing_tools),
            "additional_tool_count": self.additional_tool_count,
            "additional_tools": list(self.additional_tools),
            "duplicated_tool_count": self.duplicated_tool_count,
            "duplicated_tools": list(self.duplicated_tools),
            "unreviewed_tool_count": self.unreviewed_tool_count,
            "unreviewed_tools": list(self.unreviewed_tools),
            "invalid_descriptor_count": self.invalid_descriptor_count,
            "classification_mismatch_count": (
                self.classification_mismatch_count
            ),
            "classification_mismatches": list(
                self.classification_mismatches
            ),
            "component_mismatch_counts": dict(
                self.component_mismatch_counts
            ),
            "mismatch_diagnostics": [
                item.as_dict() for item in self.mismatch_diagnostics
            ],
            "diagnostics_truncated": self.diagnostics_truncated,
            "reviewed_standalone_raw_catalog_fingerprint": (
                self.reviewed_standalone_raw_catalog_fingerprint
            ),
            "observed_raw_catalog_fingerprint": (
                self.observed_raw_catalog_fingerprint
            ),
            "expected_normalized_catalog_fingerprint": (
                self.expected_normalized_catalog_fingerprint
            ),
            "normalized_catalog_fingerprint": (
                self.normalized_catalog_fingerprint
            ),
            "validation_status": self.validation_status,
            "valid": self.valid,
        }


def _catalog_tool_component_fingerprints(
    tool: dict[str, Any],
    *,
    runtime_contract_fingerprint_model: str,
) -> dict[str, str]:
    return {
        "input_schema_fingerprint": schema_fingerprint(
            tool.get("inputSchema")
        ),
        "description_fingerprint": (
            runtime_description_fingerprint(tool.get("description"))
            or schema_fingerprint({"invalid_description": True})
        ),
        "annotation_fingerprint": schema_fingerprint(
            {
                "present": "annotations" in tool,
                "value": tool.get("annotations"),
            }
        ),
        "output_contract_fingerprint": schema_fingerprint(
            {
                "present": "outputSchema" in tool,
                "value": tool.get("outputSchema"),
            }
        ),
        "runtime_contract_fingerprint": runtime_contract_fingerprint(
            tool,
            model=runtime_contract_fingerprint_model,
        ),
    }


def _normalized_catalog_fingerprint(
    release: ReviewedUpstreamRelease,
    entries: list[dict[str, str]],
) -> str:
    return schema_fingerprint(
        {
            "fingerprint_model": (
                REVIEWED_NORMALIZED_CATALOG_FINGERPRINT_MODEL_V1
            ),
            "selected_compatibility_entry_id": release.entry_id,
            "runtime_contract_fingerprint_model": (
                release.runtime_contract_fingerprint_model
            ),
            "tools": sorted(entries, key=lambda item: item["name"]),
        }
    )


def validate_reviewed_release_catalog(
    release: ReviewedUpstreamRelease,
    *,
    observed_server_name: str,
    observed_upstream_version: str,
    observed_protocol_version: str,
    tools: Sequence[Any],
) -> ReviewedCatalogValidation:
    """Validate a complete catalog under one exact release-declared model.

    Raw catalog fingerprints are retained as diagnostic evidence. Admission is
    derived from the exact reviewed tool set and every per-tool contract
    component, with runtime descriptors evaluated under the selected release's
    explicit fingerprint model.
    """

    runtime_model = release.runtime_contract_fingerprint_model
    expected_contracts = release.tool_contracts_by_name
    expected_policy = release.policy.by_name
    expected_names = set(expected_contracts)
    component_counts = Counter(
        {name: 0 for name in REVIEWED_CATALOG_COMPONENTS}
    )
    classification_mismatches = sorted(
        name
        for name in expected_names | set(expected_policy)
        if name not in expected_contracts
        or name not in expected_policy
        or expected_contracts[name].policy_classification
        != expected_policy[name].classification
    )
    component_counts["classification"] = len(classification_mismatches)

    descriptors_by_name: dict[str, list[dict[str, Any]]] = {}
    invalid_descriptor_count = 0
    raw_tools: list[dict[str, Any]] = []
    for item in tools:
        if not isinstance(item, dict):
            invalid_descriptor_count += 1
            continue
        raw_tools.append(dict(item))
        name = item.get("name")
        if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
            invalid_descriptor_count += 1
            continue
        descriptors_by_name.setdefault(name, []).append(item)

    observed_names = set(descriptors_by_name)
    missing = sorted(expected_names - observed_names)
    additional = sorted(observed_names - expected_names)
    duplicated = sorted(
        name
        for name, descriptors in descriptors_by_name.items()
        if len(descriptors) != 1
    )
    unreviewed = list(additional)

    observed_raw_fingerprint: str | None = None
    try:
        if len(raw_tools) == len(tools):
            observed_raw_fingerprint = catalog_fingerprint(raw_tools)
    except (
        TypeError,
        ValueError,
        OverflowError,
        UnicodeError,
        RecursionError,
    ):
        invalid_descriptor_count += 1

    expected_entries: list[dict[str, str]] = []
    for name in sorted(expected_names):
        contract = expected_contracts[name]
        expected_entries.append(
            {
                "name": name,
                "classification": contract.policy_classification,
                "input_schema_fingerprint": (
                    contract.input_schema_fingerprint
                ),
                "description_fingerprint": (
                    contract.description_fingerprint
                ),
                "annotation_fingerprint": contract.annotation_fingerprint,
                "output_contract_fingerprint": (
                    contract.output_contract_fingerprint
                ),
                "runtime_contract_fingerprint": (
                    contract.runtime_contract_fingerprint
                ),
            }
        )

    expected_normalized: str | None = None
    runtime_model_supported = runtime_model in (
        RUNTIME_CONTRACT_FINGERPRINT_MODELS
    )
    if runtime_model_supported:
        expected_normalized = _normalized_catalog_fingerprint(
            release, expected_entries
        )

    observed_entries: list[dict[str, str]] = []
    mismatch_items: list[ReviewedCatalogToolMismatch] = []
    accounted = 0
    for name in sorted(expected_names & observed_names):
        descriptors = descriptors_by_name[name]
        if len(descriptors) != 1:
            continue
        tool = descriptors[0]
        components: list[str] = []
        diff_fields: tuple[str, ...] = ()
        try:
            observed_components = _catalog_tool_component_fingerprints(
                tool,
                runtime_contract_fingerprint_model=runtime_model,
            )
        except (
            TypeError,
            ValueError,
            OverflowError,
            UnicodeError,
            RecursionError,
        ):
            invalid_descriptor_count += 1
            components.append("descriptor_invalid")
            observed_components = None

        contract = expected_contracts[name]
        classification = (
            expected_policy[name].classification
            if name in expected_policy
            else "unreviewed"
        )
        if (
            name in classification_mismatches
            or classification != contract.policy_classification
        ):
            components.append("classification")
        if observed_components is not None:
            for component, observed in observed_components.items():
                if observed != getattr(contract, component):
                    components.append(component)
                    component_counts[component] += 1
            observed_entries.append(
                {
                    "name": name,
                    "classification": classification,
                    **observed_components,
                }
            )
            if "runtime_contract_fingerprint" in components:
                expected_fields = dict(
                    contract.runtime_contract_field_fingerprints
                )
                try:
                    observed_fields = runtime_contract_field_fingerprints(
                        tool
                    )
                except (
                    TypeError,
                    ValueError,
                    OverflowError,
                    UnicodeError,
                    RecursionError,
                ):
                    observed_fields = {}
                meta = tool.get("_meta")
                ha_mcp = (
                    meta.get("ha_mcp") if isinstance(meta, dict) else None
                )
                policy = (
                    ha_mcp.get("policy")
                    if isinstance(ha_mcp, dict)
                    else None
                )
                normalized_policy_paths = (
                    RUNTIME_POLICY_DYNAMIC_PATHS
                    if runtime_model
                    == RUNTIME_CONTRACT_FINGERPRINT_MODEL_V2
                    and runtime_policy_state_fingerprint_projection(policy)[
                        "valid"
                    ]
                    else frozenset()
                )
                diff_fields = tuple(
                    sorted(
                        pointer
                        for pointer in expected_fields.keys()
                        | observed_fields.keys()
                        if expected_fields.get(pointer)
                        != observed_fields.get(pointer)
                        and pointer not in normalized_policy_paths
                    )[:MAX_REVIEWED_CATALOG_DIAGNOSTICS]
                )
        if components:
            mismatch_items.append(
                ReviewedCatalogToolMismatch(
                    tool_name=name,
                    components=tuple(sorted(set(components))),
                    runtime_contract_diff_fields=diff_fields,
                )
            )
        else:
            accounted += 1

    normalized_fingerprint: str | None = None
    if (
        runtime_model_supported
        and not missing
        and not additional
        and not duplicated
        and invalid_descriptor_count == 0
        and len(observed_entries) == len(expected_entries)
    ):
        normalized_fingerprint = _normalized_catalog_fingerprint(
            release, observed_entries
        )

    identity_status = None
    if observed_server_name != release.server_name:
        identity_status = "server_identity_mismatch"
    elif observed_upstream_version != release.version:
        identity_status = "upstream_version_mismatch"
    elif observed_protocol_version not in release.allowed_protocol_versions:
        identity_status = "unsupported_protocol_version"
    elif not runtime_model_supported:
        identity_status = "unsupported_runtime_fingerprint_model"

    valid = (
        identity_status is None
        and len(tools) == release.advertised_tool_count
        and not missing
        and not additional
        and not duplicated
        and invalid_descriptor_count == 0
        and not classification_mismatches
        and all(value == 0 for value in component_counts.values())
        and accounted == release.advertised_tool_count
        and expected_normalized is not None
        and normalized_fingerprint == expected_normalized
    )
    validation_status = (
        "accepted_exact"
        if valid
        else identity_status or "rejected_catalog_mismatch"
    )

    bounded_mismatches = mismatch_items[
        :MAX_REVIEWED_CATALOG_DIAGNOSTICS
    ]
    bounded_missing = missing[:MAX_REVIEWED_CATALOG_DIAGNOSTICS]
    bounded_additional = additional[:MAX_REVIEWED_CATALOG_DIAGNOSTICS]
    bounded_duplicated = duplicated[:MAX_REVIEWED_CATALOG_DIAGNOSTICS]
    bounded_unreviewed = unreviewed[:MAX_REVIEWED_CATALOG_DIAGNOSTICS]
    bounded_classifications = classification_mismatches[
        :MAX_REVIEWED_CATALOG_DIAGNOSTICS
    ]
    diagnostics_truncated = any(
        (
            len(mismatch_items) > len(bounded_mismatches),
            len(missing) > len(bounded_missing),
            len(additional) > len(bounded_additional),
            len(duplicated) > len(bounded_duplicated),
            len(unreviewed) > len(bounded_unreviewed),
            len(classification_mismatches)
            > len(bounded_classifications),
        )
    )
    return ReviewedCatalogValidation(
        selected_compatibility_entry_id=release.entry_id,
        observed_server_name=observed_server_name,
        observed_upstream_version=observed_upstream_version,
        observed_protocol_version=observed_protocol_version,
        runtime_contract_fingerprint_model=runtime_model,
        aggregate_fingerprint_model=(
            REVIEWED_NORMALIZED_CATALOG_FINGERPRINT_MODEL_V1
        ),
        expected_tool_count=release.advertised_tool_count,
        observed_tool_count=len(tools),
        reviewed_accounted_count=accounted,
        missing_tool_count=len(missing),
        missing_tools=tuple(bounded_missing),
        additional_tool_count=len(additional),
        additional_tools=tuple(bounded_additional),
        duplicated_tool_count=len(duplicated),
        duplicated_tools=tuple(bounded_duplicated),
        unreviewed_tool_count=len(unreviewed),
        unreviewed_tools=tuple(bounded_unreviewed),
        invalid_descriptor_count=invalid_descriptor_count,
        classification_mismatch_count=len(classification_mismatches),
        classification_mismatches=tuple(bounded_classifications),
        component_mismatch_counts=tuple(
            (name, component_counts[name])
            for name in REVIEWED_CATALOG_COMPONENTS
        ),
        mismatch_diagnostics=tuple(bounded_mismatches),
        diagnostics_truncated=diagnostics_truncated,
        reviewed_standalone_raw_catalog_fingerprint=(
            release.catalog_fingerprint
        ),
        observed_raw_catalog_fingerprint=observed_raw_fingerprint,
        expected_normalized_catalog_fingerprint=expected_normalized,
        normalized_catalog_fingerprint=normalized_fingerprint,
        validation_status=validation_status,
    )


def load_upstream_tool_policy(
    path: Path = POLICY_PATH,
    *,
    expected_version: str = REVIEWED_UPSTREAM_VERSION,
    expected_source_tag: str = "v7.14.1",
    expected_source_commit: str = "255acec1affa6528004a122eb83e30aee9c77713",
) -> UpstreamToolPolicy:
    value = _load_strict_json(path, error="policy_document_unreadable")
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "upstream_server",
        "reviewed_upstream_version",
        "reviewed_source_tag",
        "reviewed_source_commit",
        "reviewed_stock_catalog_tool_count",
        "reviewed_stock_catalog_fingerprint",
        "reviewed_runtime_description_fingerprints",
        "reviewed_runtime_annotation_fingerprints",
        "reviewed_runtime_output_schema_fingerprints",
        "tools",
    }:
        raise UpstreamToolPolicyError("policy_document_fields_invalid")
    if value["schema_version"] != POLICY_SCHEMA_VERSION:
        raise UpstreamToolPolicyError("policy_schema_version_invalid")
    if value["upstream_server"] != REVIEWED_UPSTREAM_SERVER:
        raise UpstreamToolPolicyError("policy_server_invalid")
    if value["reviewed_upstream_version"] != expected_version:
        raise UpstreamToolPolicyError("policy_version_invalid")
    if value["reviewed_source_tag"] != expected_source_tag:
        raise UpstreamToolPolicyError("policy_source_tag_invalid")
    if value["reviewed_source_commit"] != expected_source_commit:
        raise UpstreamToolPolicyError("policy_source_commit_invalid")
    stock_tool_count = value["reviewed_stock_catalog_tool_count"]
    if (
        isinstance(stock_tool_count, bool)
        or not isinstance(stock_tool_count, int)
        or not 1 <= stock_tool_count <= 512
    ):
        raise UpstreamToolPolicyError("policy_stock_catalog_count_invalid")
    if not isinstance(value["reviewed_stock_catalog_fingerprint"], str) or not _HEX_64.fullmatch(
        value["reviewed_stock_catalog_fingerprint"]
    ):
        raise UpstreamToolPolicyError("policy_stock_catalog_fingerprint_invalid")
    if not isinstance(value["tools"], list) or not value["tools"]:
        raise UpstreamToolPolicyError("policy_tools_invalid")
    entries = tuple(UpstreamToolPolicyEntry.from_mapping(item) for item in value["tools"])
    if len(entries) != stock_tool_count:
        raise UpstreamToolPolicyError("policy_stock_catalog_count_invalid")
    names = [entry.upstream_name for entry in entries]
    if names != sorted(names) or len(names) != len(set(names)):
        raise UpstreamToolPolicyError("policy_tool_order_or_uniqueness_invalid")
    exposed = [entry.exposed_name for entry in entries]
    if len(exposed) != len(set(exposed)):
        raise UpstreamToolPolicyError("policy_exposed_name_duplicate")
    description_fingerprints = value[
        "reviewed_runtime_description_fingerprints"
    ]
    annotation_fingerprints = value[
        "reviewed_runtime_annotation_fingerprints"
    ]
    output_schema_fingerprints = value[
        "reviewed_runtime_output_schema_fingerprints"
    ]
    automatic_names = {
        entry.upstream_name
        for entry in entries
        if entry.classification == "automatic_read"
    }
    if (
        not isinstance(description_fingerprints, dict)
        or set(description_fingerprints) != automatic_names
        or any(
            not isinstance(name, str)
            or not isinstance(fingerprint, str)
            or not _HEX_64.fullmatch(fingerprint)
            for name, fingerprint in description_fingerprints.items()
        )
    ):
        raise UpstreamToolPolicyError(
            "policy_runtime_description_fingerprints_invalid"
        )
    if (
        not isinstance(annotation_fingerprints, dict)
        or set(annotation_fingerprints) != automatic_names
        or any(
            not isinstance(name, str)
            or not isinstance(fingerprint, str)
            or not _HEX_64.fullmatch(fingerprint)
            for name, fingerprint in annotation_fingerprints.items()
        )
    ):
        raise UpstreamToolPolicyError(
            "policy_runtime_annotation_fingerprints_invalid"
        )
    if (
        not isinstance(output_schema_fingerprints, dict)
        or set(output_schema_fingerprints) != automatic_names
        or any(
            not isinstance(name, str)
            or not isinstance(fingerprint, str)
            or not _HEX_64.fullmatch(fingerprint)
            for name, fingerprint in output_schema_fingerprints.items()
        )
    ):
        raise UpstreamToolPolicyError(
            "policy_runtime_output_schema_fingerprints_invalid"
        )
    return UpstreamToolPolicy(
        schema_version=value["schema_version"],
        upstream_server=value["upstream_server"],
        reviewed_upstream_version=value["reviewed_upstream_version"],
        reviewed_source_tag=value["reviewed_source_tag"],
        reviewed_source_commit=value["reviewed_source_commit"],
        reviewed_stock_catalog_tool_count=stock_tool_count,
        reviewed_stock_catalog_fingerprint=value[
            "reviewed_stock_catalog_fingerprint"
        ],
        reviewed_runtime_description_fingerprints=tuple(
            sorted(description_fingerprints.items())
        ),
        reviewed_runtime_annotation_fingerprints=tuple(
            sorted(annotation_fingerprints.items())
        ),
        reviewed_runtime_output_schema_fingerprints=tuple(
            sorted(output_schema_fingerprints.items())
        ),
        tools=entries,
    )


def load_reviewed_upstream_release_registry(
    path: Path = RELEASE_REGISTRY_PATH,
) -> ReviewedUpstreamReleaseRegistry:
    """Load exact compiled release authority and all version-scoped policies."""

    value = _load_strict_json(
        path, error="release_registry_document_unreadable"
    )
    if not isinstance(value, dict) or set(value) != {
        "registry_format_version",
        "default_version",
        "releases",
    }:
        raise UpstreamToolPolicyError(
            "release_registry_document_fields_invalid"
        )
    if value["registry_format_version"] != RELEASE_REGISTRY_FORMAT_VERSION:
        raise UpstreamToolPolicyError(
            "release_registry_format_version_invalid"
        )
    default_version = value["default_version"]
    if (
        not isinstance(default_version, str)
        or not _SEMANTIC_VERSION.fullmatch(default_version)
    ):
        raise UpstreamToolPolicyError(
            "release_registry_default_version_invalid"
        )
    raw_releases = value["releases"]
    if not isinstance(raw_releases, list) or not raw_releases:
        raise UpstreamToolPolicyError("release_registry_entries_invalid")
    releases = tuple(
        _load_reviewed_release(item, registry_path=path)
        for item in raw_releases
    )
    versions = [entry.version for entry in releases]
    if len(versions) != len(set(versions)):
        raise UpstreamToolPolicyError(
            "release_registry_version_duplicate"
        )
    if versions != sorted(
        versions, key=lambda item: tuple(int(part) for part in item.split("."))
    ):
        raise UpstreamToolPolicyError(
            "release_registry_version_order_invalid"
        )
    entry_ids = [entry.entry_id for entry in releases]
    if len(entry_ids) != len(set(entry_ids)):
        raise UpstreamToolPolicyError(
            "release_registry_entry_id_duplicate"
        )
    image_digests = [entry.image_index_digest for entry in releases]
    if len(image_digests) != len(set(image_digests)):
        raise UpstreamToolPolicyError(
            "release_registry_image_digest_conflict"
        )
    if default_version not in set(versions):
        raise UpstreamToolPolicyError(
            "release_registry_default_version_missing"
        )
    return ReviewedUpstreamReleaseRegistry(
        registry_format_version=value["registry_format_version"],
        default_version=default_version,
        releases=releases,
    )


def _load_reviewed_release(
    value: Any,
    *,
    registry_path: Path,
) -> ReviewedUpstreamRelease:
    expected = {
        "entry_id",
        "approval_status",
        "server_name",
        "version",
        "allowed_protocol_versions",
        "source_repository",
        "release_tag",
        "source_commit",
        "image_index_digest",
        "architecture_image_digests",
        "image_revision",
        "advertised_tool_count",
        "catalog_fingerprint",
        "runtime_contract_fingerprint_model",
        "capture_resource",
        "capture_sha256",
        "capture_format_version",
        "policy_resource",
        "policy_sha256",
        "review_provenance",
        "review_date",
        "dashboard_attestation",
        "error_contract_fingerprint",
        "entity_lookup_missing_resource_status",
        "tool_contracts",
    }
    optional = {
        "strict_full_contract_fingerprint",
        "strict_full_contract_fingerprint_model",
        "addon_artifact_digests",
    }
    if (
        not isinstance(value, dict)
        or not expected <= set(value)
        or set(value) - expected - optional
    ):
        raise UpstreamToolPolicyError(
            "release_registry_entry_fields_invalid"
        )
    if value["approval_status"] != "reviewed":
        raise UpstreamToolPolicyError(
            "release_registry_release_not_approved"
        )
    version = value["version"]
    if (
        not isinstance(version, str)
        or not _SEMANTIC_VERSION.fullmatch(version)
    ):
        raise UpstreamToolPolicyError(
            "release_registry_version_invalid"
        )
    digest = value["image_index_digest"]
    expected_entry_id = (
        f"ha-mcp-v{version}-{str(digest).removeprefix('sha256:')[:8]}"
    )
    if value["entry_id"] != expected_entry_id:
        raise UpstreamToolPolicyError(
            "release_registry_entry_id_invalid"
        )
    if value["server_name"] != REVIEWED_UPSTREAM_SERVER:
        raise UpstreamToolPolicyError(
            "release_registry_server_invalid"
        )
    protocols = value["allowed_protocol_versions"]
    if (
        not isinstance(protocols, list)
        or protocols != sorted(set(protocols))
        or REVIEWED_UPSTREAM_PROTOCOL not in protocols
        or any(
            not isinstance(item, str) or not 1 <= len(item) <= 64
            for item in protocols
        )
    ):
        raise UpstreamToolPolicyError(
            "release_registry_protocols_invalid"
        )
    if value["source_repository"] != UPSTREAM_SOURCE_REPOSITORY:
        raise UpstreamToolPolicyError(
            "release_registry_source_repository_invalid"
        )
    if value["release_tag"] != f"v{version}":
        raise UpstreamToolPolicyError(
            "release_registry_release_tag_invalid"
        )
    for field in ("source_commit", "image_revision"):
        if (
            not isinstance(value[field], str)
            or not _COMMIT_SHA.fullmatch(value[field])
        ):
            raise UpstreamToolPolicyError(
                "release_registry_source_commit_invalid"
            )
    if not isinstance(digest, str) or not _SHA256_DIGEST.fullmatch(digest):
        raise UpstreamToolPolicyError(
            "release_registry_image_digest_invalid"
        )
    architecture_digests = value["architecture_image_digests"]
    if (
        not isinstance(architecture_digests, dict)
        or not {"linux/amd64", "linux/arm64"} <= set(architecture_digests)
        or any(
            platform
            not in {"linux/amd64", "linux/arm64", "linux/arm/v7"}
            or not isinstance(item, str)
            or not _SHA256_DIGEST.fullmatch(item)
            for platform, item in architecture_digests.items()
        )
    ):
        raise UpstreamToolPolicyError(
            "release_registry_architecture_digests_invalid"
        )
    tool_count = value["advertised_tool_count"]
    if (
        isinstance(tool_count, bool)
        or not isinstance(tool_count, int)
        or not 1 <= tool_count <= 512
    ):
        raise UpstreamToolPolicyError(
            "release_registry_tool_count_invalid"
        )
    catalog = value["catalog_fingerprint"]
    if not isinstance(catalog, str) or not _HEX_64.fullmatch(catalog):
        raise UpstreamToolPolicyError(
            "release_registry_catalog_fingerprint_invalid"
        )
    runtime_model = value["runtime_contract_fingerprint_model"]
    if runtime_model not in RUNTIME_CONTRACT_FINGERPRINT_MODELS:
        raise UpstreamToolPolicyError(
            "release_registry_runtime_fingerprint_model_invalid"
        )
    strict_fingerprint = value.get("strict_full_contract_fingerprint")
    strict_model = value.get("strict_full_contract_fingerprint_model")
    addon_artifacts = value.get("addon_artifact_digests", {})
    if (strict_fingerprint is None) != (strict_model is None):
        raise UpstreamToolPolicyError(
            "release_registry_strict_fingerprint_invalid"
        )
    if strict_fingerprint is not None and (
        not isinstance(strict_fingerprint, str)
        or not _HEX_64.fullmatch(strict_fingerprint)
        or strict_model != "ha-mcp-strict-full-contract-v1"
    ):
        raise UpstreamToolPolicyError(
            "release_registry_strict_fingerprint_invalid"
        )
    if (
        not isinstance(addon_artifacts, dict)
        or set(addon_artifacts) - {"linux/amd64", "linux/arm64"}
        or any(
            not isinstance(item, dict)
            or set(item) != {"index_digest", "image_manifest_digest"}
            or any(
                not isinstance(digest_value, str)
                or not _SHA256_DIGEST.fullmatch(digest_value)
                for digest_value in item.values()
            )
            for item in addon_artifacts.values()
        )
    ):
        raise UpstreamToolPolicyError(
            "release_registry_addon_artifacts_invalid"
        )
    capture_resource = value["capture_resource"]
    if (
        not isinstance(capture_resource, str)
        or not _CAPTURE_RESOURCE.fullmatch(capture_resource)
        or capture_resource
        != (
            "docs/evidence/upstream-read-compatibility/"
            f"ha-mcp-{version}.json"
        )
    ):
        raise UpstreamToolPolicyError(
            "release_registry_capture_resource_invalid"
        )
    capture_sha256 = value["capture_sha256"]
    if (
        not isinstance(capture_sha256, str)
        or not _SHA256_DIGEST.fullmatch(capture_sha256)
    ):
        raise UpstreamToolPolicyError(
            "release_registry_capture_digest_invalid"
        )
    if value["capture_format_version"] != REVIEWED_CAPTURE_FORMAT_VERSION:
        raise UpstreamToolPolicyError(
            "release_registry_capture_format_invalid"
        )
    resource = value["policy_resource"]
    if (
        not isinstance(resource, str)
        or not _POLICY_RESOURCE.fullmatch(resource)
    ):
        raise UpstreamToolPolicyError(
            "release_registry_policy_resource_invalid"
        )
    resource_path = registry_path.parent / resource
    expected_policy_digest = value["policy_sha256"]
    if (
        not isinstance(expected_policy_digest, str)
        or not _SHA256_DIGEST.fullmatch(expected_policy_digest)
    ):
        raise UpstreamToolPolicyError(
            "release_registry_policy_digest_invalid"
        )
    try:
        actual_policy_digest = (
            "sha256:" + hashlib.sha256(resource_path.read_bytes()).hexdigest()
        )
    except OSError as exc:
        raise UpstreamToolPolicyError(
            "release_registry_policy_resource_unreadable"
        ) from exc
    if actual_policy_digest != expected_policy_digest:
        raise UpstreamToolPolicyError(
            "release_registry_policy_digest_mismatch"
        )
    policy = load_upstream_tool_policy(
        resource_path,
        expected_version=version,
        expected_source_tag=value["release_tag"],
        expected_source_commit=value["source_commit"],
    )
    if (
        policy.reviewed_stock_catalog_tool_count != tool_count
        or policy.reviewed_stock_catalog_fingerprint != catalog
    ):
        raise UpstreamToolPolicyError(
            "release_registry_policy_catalog_mismatch"
        )
    provenance = value["review_provenance"]
    if (
        not isinstance(provenance, list)
        or not provenance
        or any(
            not isinstance(item, str) or not 1 <= len(item) <= 512
            for item in provenance
        )
    ):
        raise UpstreamToolPolicyError(
            "release_registry_review_provenance_invalid"
        )
    review_date = value["review_date"]
    if (
        not isinstance(review_date, str)
        or not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", review_date)
    ):
        raise UpstreamToolPolicyError(
            "release_registry_review_date_invalid"
        )
    dashboard = value["dashboard_attestation"]
    if not isinstance(dashboard, dict) or set(dashboard) != {
        "status",
        "entry_id",
        "attestation_fingerprint",
        "compiled_constraints_fingerprint",
    }:
        raise UpstreamToolPolicyError(
            "release_registry_dashboard_attestation_invalid"
        )
    dashboard_status = dashboard["status"]
    dashboard_entry_id = dashboard["entry_id"]
    dashboard_attestation_fingerprint = dashboard[
        "attestation_fingerprint"
    ]
    dashboard_constraints_fingerprint = dashboard[
        "compiled_constraints_fingerprint"
    ]
    if dashboard_status not in {"reviewed", "quarantined"}:
        raise UpstreamToolPolicyError(
            "release_registry_dashboard_attestation_invalid"
        )
    if dashboard_status == "reviewed":
        if (
            not isinstance(dashboard_entry_id, str)
            or not 1 <= len(dashboard_entry_id) <= 128
            or not isinstance(dashboard_attestation_fingerprint, str)
            or not _HEX_64.fullmatch(
                dashboard_attestation_fingerprint
            )
            or not isinstance(dashboard_constraints_fingerprint, str)
            or not _HEX_64.fullmatch(
                dashboard_constraints_fingerprint
            )
        ):
            raise UpstreamToolPolicyError(
                "release_registry_dashboard_attestation_invalid"
            )
    elif (
        dashboard_entry_id is not None
        or dashboard_attestation_fingerprint is not None
        or dashboard_constraints_fingerprint is not None
    ):
        raise UpstreamToolPolicyError(
            "release_registry_dashboard_attestation_invalid"
        )
    error_contract = value["error_contract_fingerprint"]
    if (
        not isinstance(error_contract, str)
        or not _HEX_64.fullmatch(error_contract)
    ):
        raise UpstreamToolPolicyError(
            "release_registry_error_contract_invalid"
        )
    entity_status = value["entity_lookup_missing_resource_status"]
    if entity_status not in {
        "ambiguous_upstream_service_call_failed",
        "deterministic_entity_not_found",
    }:
        raise UpstreamToolPolicyError(
            "release_registry_entity_lookup_status_invalid"
        )
    raw_tool_contracts = value["tool_contracts"]
    if not isinstance(raw_tool_contracts, dict):
        raise UpstreamToolPolicyError(
            "release_registry_tool_contracts_invalid"
        )
    tool_contracts = {
        name: ReviewedReleaseToolContract.from_mapping(item)
        for name, item in raw_tool_contracts.items()
        if isinstance(name, str) and _TOOL_NAME.fullmatch(name)
    }
    if (
        len(tool_contracts) != len(raw_tool_contracts)
        or set(tool_contracts) != set(policy.by_name)
        or len(tool_contracts) != tool_count
    ):
        raise UpstreamToolPolicyError(
            "release_registry_tool_contracts_incomplete"
        )
    for name, contract in tool_contracts.items():
        policy_entry = policy.by_name[name]
        if (
            contract.input_schema_fingerprint
            != policy_entry.input_schema_fingerprint
            or contract.policy_classification
            != policy_entry.classification
            or contract.reviewed_automatic_read
            != (policy_entry.classification == "automatic_read")
        ):
            raise UpstreamToolPolicyError(
                "release_registry_tool_contract_policy_mismatch"
            )
    return ReviewedUpstreamRelease(
        entry_id=value["entry_id"],
        server_name=value["server_name"],
        version=version,
        allowed_protocol_versions=tuple(protocols),
        source_repository=value["source_repository"],
        release_tag=value["release_tag"],
        source_commit=value["source_commit"],
        image_index_digest=digest,
        architecture_image_digests=tuple(
            sorted(architecture_digests.items())
        ),
        image_revision=value["image_revision"],
        advertised_tool_count=tool_count,
        catalog_fingerprint=catalog,
        runtime_contract_fingerprint_model=runtime_model,
        strict_full_contract_fingerprint=strict_fingerprint,
        strict_full_contract_fingerprint_model=strict_model,
        addon_artifact_digests=tuple(
            (platform, tuple(sorted(item.items())))
            for platform, item in sorted(addon_artifacts.items())
        ),
        capture_resource=capture_resource,
        capture_sha256=capture_sha256,
        capture_format_version=value["capture_format_version"],
        policy_resource=resource,
        policy_sha256=expected_policy_digest,
        review_provenance=tuple(provenance),
        review_date=review_date,
        dashboard_attestation_status=dashboard_status,
        dashboard_attestation_entry_id=dashboard_entry_id,
        dashboard_attestation_fingerprint=(
            dashboard_attestation_fingerprint
        ),
        dashboard_compiled_constraints_fingerprint=(
            dashboard_constraints_fingerprint
        ),
        error_contract_fingerprint=error_contract,
        entity_lookup_missing_resource_status=entity_status,
        tool_contracts=tuple(sorted(tool_contracts.items())),
        policy=policy,
    )


def reviewed_tool_contracts_from_capture(
    capture_value: dict[str, Any],
    policy: UpstreamToolPolicy,
    *,
    runtime_contract_fingerprint_model: str = (
        RUNTIME_CONTRACT_FINGERPRINT_MODEL_V1
    ),
) -> dict[str, dict[str, Any]]:
    """Derive the complete evidence ledger from one normalized capture."""

    policy_by_name = policy.by_name
    values: dict[str, dict[str, Any]] = {}
    tools = capture_value.get("tools")
    if not isinstance(tools, list):
        raise UpstreamToolPolicyError("reviewed_capture_tools_invalid")
    for tool in tools:
        if not isinstance(tool, dict):
            raise UpstreamToolPolicyError(
                "reviewed_capture_tool_descriptor_invalid"
            )
        name = tool.get("name")
        if not isinstance(name, str) or name not in policy_by_name:
            raise UpstreamToolPolicyError(
                "reviewed_capture_tool_policy_missing"
            )
        if "inputSchema" not in tool:
            raise UpstreamToolPolicyError(
                "reviewed_capture_tool_descriptor_invalid"
            )
        policy_entry = policy_by_name[name]
        classification = policy_entry.classification
        values[name] = {
            "input_schema_fingerprint": schema_fingerprint(
                tool["inputSchema"]
            ),
            "description_fingerprint": (
                runtime_description_fingerprint(
                    tool.get("description")
                )
                or schema_fingerprint({"invalid_description": True})
            ),
            "annotation_fingerprint": schema_fingerprint(
                {
                    "present": "annotations" in tool,
                    "value": tool.get("annotations"),
                }
            ),
            "output_contract_fingerprint": schema_fingerprint(
                {
                    "present": "outputSchema" in tool,
                    "value": tool.get("outputSchema"),
                }
            ),
            "runtime_contract_fingerprint": runtime_contract_fingerprint(
                tool, model=runtime_contract_fingerprint_model
            ),
            "runtime_contract_field_fingerprints": (
                runtime_contract_field_fingerprints(tool)
            ),
            "policy_classification": classification,
            "reviewed_automatic_read": (
                classification == "automatic_read"
            ),
            "quarantine_reason": (
                None
                if classification == "automatic_read"
                else f"policy:{classification}"
            ),
        }
    if set(values) != set(policy_by_name):
        raise UpstreamToolPolicyError(
            "reviewed_capture_tool_contracts_incomplete"
        )
    return dict(sorted(values.items()))


def _reviewed_capture(
    release: ReviewedUpstreamRelease,
    *,
    repository_root: Path,
    verify_digest: bool,
) -> tuple[dict[str, Any], str]:
    capture_path = repository_root / release.capture_resource
    try:
        raw = capture_path.read_bytes()
    except OSError as exc:
        raise UpstreamToolPolicyError(
            "reviewed_capture_unreadable"
        ) from exc
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    if verify_digest and digest != release.capture_sha256:
        raise UpstreamToolPolicyError(
            "reviewed_capture_digest_mismatch"
        )
    capture_value = _load_strict_json(
        capture_path, error="reviewed_capture_invalid"
    )
    if (
        not isinstance(capture_value, dict)
        or set(capture_value)
        != {
            "capture_format_version",
            "catalog_fingerprint",
            "error_shapes",
            "protocol_version",
            "server_name",
            "server_version",
            "tool_count",
            "tools",
        }
    ):
        raise UpstreamToolPolicyError(
            "reviewed_capture_fields_invalid"
        )
    if raw != canonical_json(capture_value) + b"\n":
        raise UpstreamToolPolicyError(
            "reviewed_capture_not_canonical"
        )
    if (
        capture_value["capture_format_version"]
        != REVIEWED_CAPTURE_FORMAT_VERSION
        or capture_value["capture_format_version"]
        != release.capture_format_version
    ):
        raise UpstreamToolPolicyError(
            "reviewed_capture_format_mismatch"
        )
    if (
        capture_value["server_name"] != release.server_name
        or capture_value["server_version"] != release.version
        or capture_value["protocol_version"]
        not in release.allowed_protocol_versions
    ):
        raise UpstreamToolPolicyError(
            "reviewed_capture_identity_mismatch"
        )
    tools = capture_value["tools"]
    if (
        not isinstance(tools, list)
        or not 1 <= len(tools) <= 512
        or any(not isinstance(tool, dict) for tool in tools)
    ):
        raise UpstreamToolPolicyError(
            "reviewed_capture_tools_invalid"
        )
    names = [tool.get("name") for tool in tools]
    if (
        any(
            not isinstance(name, str)
            or not _TOOL_NAME.fullmatch(name)
            for name in names
        )
        or names != sorted(names)
        or len(names) != len(set(names))
    ):
        raise UpstreamToolPolicyError(
            "reviewed_capture_tool_order_or_uniqueness_invalid"
        )
    observed_catalog_fingerprint = catalog_fingerprint(tools)
    if (
        capture_value["tool_count"] != len(tools)
        or capture_value["catalog_fingerprint"]
        != observed_catalog_fingerprint
    ):
        raise UpstreamToolPolicyError(
            "reviewed_capture_catalog_invalid"
        )
    if not isinstance(capture_value["error_shapes"], dict):
        raise UpstreamToolPolicyError(
            "reviewed_capture_error_shapes_invalid"
        )
    return capture_value, digest


def _dashboard_attestation_projection(attestation: Any) -> dict[str, Any]:
    return {
        "entry_id": attestation.entry_id,
        "server_name": attestation.server_name,
        "upstream_version": attestation.upstream_version,
        "source_tag": attestation.source_tag,
        "source_commit": attestation.source_commit,
        "image_index_digest": attestation.image_index_digest,
        "platform_digests": dict(attestation.platform_digests),
        "image_revision": attestation.image_revision,
        "contract_family": attestation.contract_family,
        "input_contract_fingerprint": (
            attestation.input_contract_fingerprint
        ),
        "security_contract_fingerprint": (
            attestation.security_contract_fingerprint
        ),
        "output_contract_fingerprint": (
            attestation.output_contract_fingerprint
        ),
        "runtime_contract_fingerprint": (
            attestation.runtime_contract_fingerprint
        ),
        "catalog_fingerprint": attestation.catalog_fingerprint,
        "raw_input_schema_fingerprint": (
            attestation.raw_input_schema_fingerprint
        ),
        "reviewed_security_descriptor_fingerprint": (
            attestation.reviewed_security_descriptor_fingerprint
        ),
        "fixture_runtime_descriptor_fingerprint": (
            attestation.fixture_runtime_descriptor_fingerprint
        ),
        "published_runtime_descriptor_fingerprint": (
            attestation.published_runtime_descriptor_fingerprint
        ),
        "review_evidence_digest": attestation.review_evidence_digest,
        "reviewed_at": attestation.reviewed_at,
        "revoked": attestation.revoked,
    }


def _dashboard_compiled_constraints_projection(
    contract_family: str,
) -> dict[str, Any]:
    from .providers.upstream_contracts import (
        ALLOWED_SCHEMA_PROPERTIES,
        COMPILED_ARGUMENT_SHAPES,
        COMPILED_CONTRACT_FAMILIES,
        PROHIBITED_ARGUMENTS,
    )

    family = COMPILED_CONTRACT_FAMILIES.get(contract_family)
    if family is None:
        raise UpstreamToolPolicyError(
            "release_registry_dashboard_contract_family_invalid"
        )
    return {
        "family": {
            "family_id": family.family_id,
            "tool_name": family.tool_name,
            "trust_mode": family.trust_mode,
            "protocol_version": family.protocol_version,
            "normalizer": family.normalizer,
            "response_policy": family.response_policy,
            "hash_contract": dict(family.hash_contract),
            "error_taxonomy": list(family.error_taxonomy),
        },
        "compiled_argument_shapes": COMPILED_ARGUMENT_SHAPES,
        "allowed_schema_properties": sorted(
            ALLOWED_SCHEMA_PROPERTIES
        ),
        "prohibited_arguments": sorted(PROHIBITED_ARGUMENTS),
    }


def _dashboard_evidence(
    release: ReviewedUpstreamRelease,
    *,
    dashboard_attestations_path: Path | None,
) -> tuple[str, str]:
    from .providers.upstream_contracts import (
        BUILTIN_ATTESTATIONS_PATH,
        load_attestations,
    )

    path = (
        dashboard_attestations_path
        if dashboard_attestations_path is not None
        else BUILTIN_ATTESTATIONS_PATH
    )
    try:
        attestations = load_attestations(path)
    except Exception as exc:
        raise UpstreamToolPolicyError(
            "release_registry_dashboard_attestations_invalid"
        ) from exc
    matches = [
        item
        for item in attestations
        if item.entry_id == release.dashboard_attestation_entry_id
    ]
    if len(matches) != 1:
        raise UpstreamToolPolicyError(
            "release_registry_dashboard_attestation_missing"
        )
    attestation = matches[0]
    if (
        attestation.revoked
        or attestation.server_name != release.server_name
        or attestation.upstream_version != release.version
        or attestation.source_tag != release.release_tag
        or attestation.source_commit != release.source_commit
        or attestation.image_index_digest
        != release.image_index_digest
        or dict(attestation.platform_digests)
        != release.architecture_image_digests_by_platform
        or attestation.image_revision != release.image_revision
        or (
            attestation.catalog_fingerprint is not None
            and attestation.catalog_fingerprint
            != release.catalog_fingerprint
        )
    ):
        raise UpstreamToolPolicyError(
            "release_registry_dashboard_attestation_mismatch"
        )
    attestation_fingerprint = schema_fingerprint(
        _dashboard_attestation_projection(attestation)
    )
    constraints_fingerprint = schema_fingerprint(
        _dashboard_compiled_constraints_projection(
            attestation.contract_family
        )
    )
    return attestation_fingerprint, constraints_fingerprint


def generated_reviewed_release_registry(
    path: Path = RELEASE_REGISTRY_PATH,
    *,
    repository_root: Path,
    dashboard_attestations_path: Path | None = None,
) -> dict[str, Any]:
    """Regenerate every evidence-derived registry field deterministically."""

    raw_registry = _load_strict_json(
        path, error="release_registry_document_unreadable"
    )
    registry = load_reviewed_upstream_release_registry(path)
    if not isinstance(raw_registry, dict):
        raise UpstreamToolPolicyError(
            "release_registry_document_fields_invalid"
        )
    generated = json.loads(canonical_json(raw_registry))
    raw_by_version = {
        item["version"]: item for item in generated["releases"]
    }
    for release in registry.releases:
        raw_release = raw_by_version[release.version]
        capture_value, capture_digest = _reviewed_capture(
            release,
            repository_root=repository_root,
            verify_digest=False,
        )
        contracts = reviewed_tool_contracts_from_capture(
            capture_value,
            release.policy,
            runtime_contract_fingerprint_model=(
                release.runtime_contract_fingerprint_model
            ),
        )
        for name, expected in contracts.items():
            policy_entry = release.policy.by_name[name]
            if (
                expected["input_schema_fingerprint"]
                != policy_entry.input_schema_fingerprint
            ):
                raise UpstreamToolPolicyError(
                    "reviewed_capture_policy_schema_mismatch"
                )
        automatic_names = {
            entry.upstream_name
            for entry in release.policy.tools
            if entry.classification == "automatic_read"
        }
        captured_by_name = {
            item["name"]: item for item in capture_value["tools"]
        }
        if release.policy.reviewed_runtime_description_fingerprints_by_name != {
            name: (
                runtime_description_fingerprint(
                    captured_by_name[name].get("description")
                )
                or ""
            )
            for name in sorted(automatic_names)
        }:
            raise UpstreamToolPolicyError(
                "reviewed_capture_policy_description_mismatch"
            )
        if release.policy.reviewed_runtime_annotation_fingerprints_by_name != {
            name: (
                runtime_annotation_fingerprint(
                    captured_by_name[name].get("annotations")
                )
                or ""
            )
            for name in sorted(automatic_names)
        }:
            raise UpstreamToolPolicyError(
                "reviewed_capture_policy_annotation_mismatch"
            )
        if (
            release.policy.reviewed_runtime_output_schema_fingerprints_by_name
            != {
                name: schema_fingerprint(
                    captured_by_name[name].get("outputSchema")
                )
                for name in sorted(automatic_names)
            }
        ):
            raise UpstreamToolPolicyError(
                "reviewed_capture_policy_output_mismatch"
            )
        raw_release["capture_sha256"] = capture_digest
        raw_release["capture_format_version"] = capture_value[
            "capture_format_version"
        ]
        raw_release["advertised_tool_count"] = capture_value[
            "tool_count"
        ]
        raw_release["catalog_fingerprint"] = capture_value[
            "catalog_fingerprint"
        ]
        raw_release["error_contract_fingerprint"] = schema_fingerprint(
            capture_value["error_shapes"]
        )
        raw_release["tool_contracts"] = contracts
        if release.dashboard_attestation_status == "reviewed":
            (
                attestation_fingerprint,
                constraints_fingerprint,
            ) = _dashboard_evidence(
                release,
                dashboard_attestations_path=(
                    dashboard_attestations_path
                ),
            )
            raw_release["dashboard_attestation"][
                "attestation_fingerprint"
            ] = attestation_fingerprint
            raw_release["dashboard_attestation"][
                "compiled_constraints_fingerprint"
            ] = constraints_fingerprint
    return generated


def validate_reviewed_release_evidence(
    path: Path = RELEASE_REGISTRY_PATH,
    *,
    repository_root: Path,
    dashboard_attestations_path: Path | None = None,
) -> ReviewedUpstreamReleaseRegistry:
    """Bind the committed registry to exact captures and dashboard evidence."""

    raw_registry = _load_strict_json(
        path, error="release_registry_document_unreadable"
    )
    registry = load_reviewed_upstream_release_registry(path)
    for release in registry.releases:
        _reviewed_capture(
            release,
            repository_root=repository_root,
            verify_digest=True,
        )
    generated = generated_reviewed_release_registry(
        path,
        repository_root=repository_root,
        dashboard_attestations_path=dashboard_attestations_path,
    )
    if canonical_json(raw_registry) != canonical_json(generated):
        raise UpstreamToolPolicyError(
            "release_registry_generated_evidence_drift"
        )
    for release in registry.releases:
        if release.dashboard_attestation_status == "reviewed":
            (
                attestation_fingerprint,
                constraints_fingerprint,
            ) = _dashboard_evidence(
                release,
                dashboard_attestations_path=(
                    dashboard_attestations_path
                ),
            )
            if (
                release.dashboard_attestation_fingerprint
                != attestation_fingerprint
                or release.dashboard_compiled_constraints_fingerprint
                != constraints_fingerprint
            ):
                raise UpstreamToolPolicyError(
                    "release_registry_dashboard_evidence_drift"
                )
    return registry
