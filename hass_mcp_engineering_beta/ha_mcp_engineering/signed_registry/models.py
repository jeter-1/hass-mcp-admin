"""Strict data models for a distributable compatibility registry envelope."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import re
from typing import Any, Mapping

from .canonical import canonical_json, sha256_digest


SCHEMA_VERSION = 1
MAX_ENVELOPE_BYTES = 4 * 1024 * 1024
MAX_ENTRIES = 512
MAX_REVOCATIONS = 512

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_SEMANTIC_VERSION = re.compile(
    r"^(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})\."
    r"(?:0|[1-9][0-9]{0,3})$"
)
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(
    r"^20[0-9]{2}-(?:0[1-9]|1[0-2])-"
    r"(?:0[1-9]|[12][0-9]|3[01])T"
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"
)
_DATE = re.compile(
    r"^20[0-9]{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])$"
)
_PLATFORMS = frozenset({"linux/amd64", "linux/arm64", "linux/arm/v7"})
_CLASSIFICATIONS = frozenset(
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
_ENTITY_LOOKUP_STATUSES = frozenset(
    {
        "ambiguous_upstream_service_call_failed",
        "deterministic_entity_not_found",
    }
)


class RegistryErrorCode(str, Enum):
    """Stable, bounded failure taxonomy for registry validation."""

    ENVELOPE_OVERSIZED = "registry_envelope_oversized"
    MALFORMED_JSON = "registry_malformed_json"
    DUPLICATE_JSON_MEMBER = "registry_duplicate_json_member"
    ENVELOPE_FIELDS_INVALID = "registry_envelope_fields_invalid"
    UNKNOWN_SCHEMA_VERSION = "registry_unknown_schema_version"
    REGISTRY_ID_INVALID = "registry_id_invalid"
    SEQUENCE_INVALID = "registry_sequence_invalid"
    TIMESTAMP_INVALID = "registry_timestamp_invalid"
    EXPIRATION_INVALID = "registry_expiration_invalid"
    PREVIOUS_DIGEST_INVALID = "registry_previous_digest_invalid"
    KEY_ID_INVALID = "registry_key_id_invalid"
    ENTRIES_INVALID = "registry_entries_invalid"
    ENTRY_FIELDS_INVALID = "registry_entry_fields_invalid"
    ENTRY_IDENTITY_INVALID = "registry_entry_identity_invalid"
    ENTRY_PROVENANCE_INVALID = "registry_entry_provenance_invalid"
    ENTRY_IMAGE_INVALID = "registry_entry_image_invalid"
    ENTRY_CATALOG_INVALID = "registry_entry_catalog_invalid"
    ENTRY_EVIDENCE_INVALID = "registry_entry_evidence_invalid"
    TOOL_CONTRACT_INVALID = "registry_tool_contract_invalid"
    DASHBOARD_CONTRACT_INVALID = "registry_dashboard_contract_invalid"
    PROVIDER_CONSTRAINT_INVALID = "registry_provider_constraint_invalid"
    REVOCATIONS_INVALID = "registry_revocations_invalid"
    REVOCATION_FIELDS_INVALID = "registry_revocation_fields_invalid"
    REVOCATION_IDENTITY_INVALID = "registry_revocation_identity_invalid"
    DUPLICATE_ENTRY = "registry_duplicate_entry"
    DUPLICATE_REVOCATION = "registry_duplicate_revocation"
    ENTRY_REVOCATION_CONTRADICTION = "registry_entry_revocation_contradiction"
    SIGNATURE_ENCODING_INVALID = "registry_signature_encoding_invalid"
    SIGNATURE_LENGTH_INVALID = "registry_signature_length_invalid"
    TRUST_ANCHOR_INVALID = "registry_trust_anchor_invalid"
    UNKNOWN_KEY = "registry_unknown_key"
    INVALID_SIGNATURE = "registry_invalid_signature"
    ACCEPTED_STATE_INVALID = "registry_accepted_state_invalid"
    CLOCK_INVALID = "registry_clock_invalid"
    EXPIRED = "registry_expired"
    GENERATED_IN_FUTURE = "registry_generated_in_future"
    REGISTRY_ID_MISMATCH = "registry_id_mismatch"
    ROLLBACK = "registry_sequence_rollback"
    REPLAY_CONFLICT = "registry_sequence_replay_conflict"
    PREVIOUS_DIGEST_MISMATCH = "registry_previous_digest_mismatch"
    INITIAL_CHAIN_INVALID = "registry_initial_chain_invalid"


class RegistryValidationError(ValueError):
    """Internal fail-closed exception carrying only a stable error code."""

    def __init__(self, code: RegistryErrorCode):
        super().__init__(code.value)
        self.code = code


def _fail(code: RegistryErrorCode) -> None:
    raise RegistryValidationError(code)


def _strict_json_loads(raw: bytes) -> Any:
    if not isinstance(raw, bytes):
        _fail(RegistryErrorCode.MALFORMED_JSON)
    if len(raw) > MAX_ENVELOPE_BYTES:
        _fail(RegistryErrorCode.ENVELOPE_OVERSIZED)

    def reject_duplicates(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail(RegistryErrorCode.DUPLICATE_JSON_MEMBER)
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        _fail(RegistryErrorCode.MALFORMED_JSON)

    try:
        return json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except RegistryValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        _fail(RegistryErrorCode.MALFORMED_JSON)


def _exact_mapping(
    value: Any,
    fields: set[str],
    code: RegistryErrorCode,
) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        _fail(code)
    return value


def _bounded_string(
    value: Any,
    *,
    maximum: int,
    code: RegistryErrorCode,
    minimum: int = 1,
) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        _fail(code)
    try:
        if len(value.encode("utf-8", errors="strict")) > maximum * 4:
            _fail(code)
    except UnicodeEncodeError:
        _fail(code)
    return value


def _identifier(
    value: Any,
    *,
    code: RegistryErrorCode,
) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        _fail(code)
    return value


def _fingerprint(
    value: Any,
    *,
    code: RegistryErrorCode,
) -> str:
    if not isinstance(value, str) or not _HEX_64.fullmatch(value):
        _fail(code)
    return value


def _digest(
    value: Any,
    *,
    code: RegistryErrorCode,
) -> str:
    if not isinstance(value, str) or not _SHA256_DIGEST.fullmatch(value):
        _fail(code)
    return value


def parse_utc_timestamp(
    value: Any,
    *,
    code: RegistryErrorCode = RegistryErrorCode.TIMESTAMP_INVALID,
) -> datetime:
    """Parse the registry's canonical second-precision UTC timestamp."""

    if not isinstance(value, str) or not _UTC_TIMESTAMP.fullmatch(value):
        _fail(code)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        _fail(code)
    return parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ToolContract:
    """Reviewed per-tool wire evidence and policy classification."""

    tool_name: str
    input_schema_fingerprint: str
    description_fingerprint: str
    annotation_fingerprint: str
    output_contract_fingerprint: str
    runtime_contract_fingerprint: str
    policy_classification: str
    reviewed_automatic_read: bool
    quarantine_reason: str | None
    argument_restrictions: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Any) -> "ToolContract":
        value = _exact_mapping(
            value,
            {
                "tool_name",
                "input_schema_fingerprint",
                "description_fingerprint",
                "annotation_fingerprint",
                "output_contract_fingerprint",
                "runtime_contract_fingerprint",
                "policy_classification",
                "reviewed_automatic_read",
                "quarantine_reason",
                "argument_restrictions",
            },
            RegistryErrorCode.TOOL_CONTRACT_INVALID,
        )
        tool_name = value["tool_name"]
        if not isinstance(tool_name, str) or not _TOOL_NAME.fullmatch(
            tool_name
        ):
            _fail(RegistryErrorCode.TOOL_CONTRACT_INVALID)
        fingerprints = tuple(
            _fingerprint(
                value[name],
                code=RegistryErrorCode.TOOL_CONTRACT_INVALID,
            )
            for name in (
                "input_schema_fingerprint",
                "description_fingerprint",
                "annotation_fingerprint",
                "output_contract_fingerprint",
                "runtime_contract_fingerprint",
            )
        )
        classification = value["policy_classification"]
        if classification not in _CLASSIFICATIONS:
            _fail(RegistryErrorCode.TOOL_CONTRACT_INVALID)
        automatic = value["reviewed_automatic_read"]
        if not isinstance(automatic, bool) or automatic != (
            classification == "automatic_read"
        ):
            _fail(RegistryErrorCode.TOOL_CONTRACT_INVALID)
        quarantine = value["quarantine_reason"]
        if automatic:
            if quarantine is not None:
                _fail(RegistryErrorCode.TOOL_CONTRACT_INVALID)
        else:
            quarantine = _bounded_string(
                quarantine,
                maximum=256,
                code=RegistryErrorCode.TOOL_CONTRACT_INVALID,
            )
        raw_restrictions = value["argument_restrictions"]
        if not isinstance(raw_restrictions, list):
            _fail(RegistryErrorCode.TOOL_CONTRACT_INVALID)
        restrictions = tuple(
            _bounded_string(
                item,
                maximum=256,
                code=RegistryErrorCode.TOOL_CONTRACT_INVALID,
            )
            for item in raw_restrictions
        )
        return cls(
            tool_name=tool_name,
            input_schema_fingerprint=fingerprints[0],
            description_fingerprint=fingerprints[1],
            annotation_fingerprint=fingerprints[2],
            output_contract_fingerprint=fingerprints[3],
            runtime_contract_fingerprint=fingerprints[4],
            policy_classification=classification,
            reviewed_automatic_read=automatic,
            quarantine_reason=quarantine,
            argument_restrictions=restrictions,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "input_schema_fingerprint": self.input_schema_fingerprint,
            "description_fingerprint": self.description_fingerprint,
            "annotation_fingerprint": self.annotation_fingerprint,
            "output_contract_fingerprint": (
                self.output_contract_fingerprint
            ),
            "runtime_contract_fingerprint": (
                self.runtime_contract_fingerprint
            ),
            "policy_classification": self.policy_classification,
            "reviewed_automatic_read": self.reviewed_automatic_read,
            "quarantine_reason": self.quarantine_reason,
            "argument_restrictions": list(self.argument_restrictions),
        }


@dataclass(frozen=True)
class DashboardAttestationContract:
    """Reviewed dashboard attestation and compiled constraint evidence."""

    status: str
    entry_id: str | None
    attestation_fingerprint: str | None
    compiled_constraints_fingerprint: str | None

    @classmethod
    def from_mapping(
        cls,
        value: Any,
    ) -> "DashboardAttestationContract":
        value = _exact_mapping(
            value,
            {
                "status",
                "entry_id",
                "attestation_fingerprint",
                "compiled_constraints_fingerprint",
            },
            RegistryErrorCode.DASHBOARD_CONTRACT_INVALID,
        )
        status = value["status"]
        if status not in {"reviewed", "quarantined"}:
            _fail(RegistryErrorCode.DASHBOARD_CONTRACT_INVALID)
        if status == "reviewed":
            entry_id = _identifier(
                value["entry_id"],
                code=RegistryErrorCode.DASHBOARD_CONTRACT_INVALID,
            )
            attestation = _fingerprint(
                value["attestation_fingerprint"],
                code=RegistryErrorCode.DASHBOARD_CONTRACT_INVALID,
            )
            constraints = _fingerprint(
                value["compiled_constraints_fingerprint"],
                code=RegistryErrorCode.DASHBOARD_CONTRACT_INVALID,
            )
        else:
            if any(
                value[name] is not None
                for name in (
                    "entry_id",
                    "attestation_fingerprint",
                    "compiled_constraints_fingerprint",
                )
            ):
                _fail(RegistryErrorCode.DASHBOARD_CONTRACT_INVALID)
            entry_id = None
            attestation = None
            constraints = None
        return cls(
            status=status,
            entry_id=entry_id,
            attestation_fingerprint=attestation,
            compiled_constraints_fingerprint=constraints,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "entry_id": self.entry_id,
            "attestation_fingerprint": self.attestation_fingerprint,
            "compiled_constraints_fingerprint": (
                self.compiled_constraints_fingerprint
            ),
        }


@dataclass(frozen=True)
class ProviderArgumentConstraint:
    """Data-only evidence for one reviewed provider argument boundary."""

    provider_id: str
    tool_name: str
    constraints_fingerprint: str

    @classmethod
    def from_mapping(cls, value: Any) -> "ProviderArgumentConstraint":
        value = _exact_mapping(
            value,
            {
                "provider_id",
                "tool_name",
                "constraints_fingerprint",
            },
            RegistryErrorCode.PROVIDER_CONSTRAINT_INVALID,
        )
        provider_id = _identifier(
            value["provider_id"],
            code=RegistryErrorCode.PROVIDER_CONSTRAINT_INVALID,
        )
        tool_name = value["tool_name"]
        if not isinstance(tool_name, str) or not _TOOL_NAME.fullmatch(
            tool_name
        ):
            _fail(RegistryErrorCode.PROVIDER_CONSTRAINT_INVALID)
        return cls(
            provider_id=provider_id,
            tool_name=tool_name,
            constraints_fingerprint=_fingerprint(
                value["constraints_fingerprint"],
                code=RegistryErrorCode.PROVIDER_CONSTRAINT_INVALID,
            ),
        )

    @property
    def identity(self) -> tuple[str, str]:
        return self.provider_id, self.tool_name

    def to_mapping(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "tool_name": self.tool_name,
            "constraints_fingerprint": self.constraints_fingerprint,
        }


@dataclass(frozen=True)
class ReviewedReleaseEntry:
    """One exact reviewed release, independent of runtime admission."""

    entry_id: str
    approval_status: str
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
    capture_resource: str
    capture_sha256: str
    capture_format_version: int
    policy_resource: str
    policy_sha256: str
    review_provenance: tuple[str, ...]
    review_date: str
    dashboard_attestation: DashboardAttestationContract
    error_contract_fingerprint: str
    entity_lookup_missing_resource_status: str
    tool_contracts: tuple[ToolContract, ...]
    provider_argument_constraints: tuple[
        ProviderArgumentConstraint, ...
    ]

    @classmethod
    def from_mapping(cls, value: Any) -> "ReviewedReleaseEntry":
        value = _exact_mapping(
            value,
            {
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
                "provider_argument_constraints",
            },
            RegistryErrorCode.ENTRY_FIELDS_INVALID,
        )
        entry_id = _identifier(
            value["entry_id"],
            code=RegistryErrorCode.ENTRY_IDENTITY_INVALID,
        )
        if value["approval_status"] != "reviewed":
            _fail(RegistryErrorCode.ENTRY_IDENTITY_INVALID)
        server_name = _bounded_string(
            value["server_name"],
            maximum=64,
            code=RegistryErrorCode.ENTRY_IDENTITY_INVALID,
        )
        version = value["version"]
        if not isinstance(version, str) or not _SEMANTIC_VERSION.fullmatch(
            version
        ):
            _fail(RegistryErrorCode.ENTRY_IDENTITY_INVALID)
        raw_protocols = value["allowed_protocol_versions"]
        if not isinstance(raw_protocols, list) or not raw_protocols:
            _fail(RegistryErrorCode.ENTRY_IDENTITY_INVALID)
        protocols = tuple(
            _bounded_string(
                item,
                maximum=64,
                code=RegistryErrorCode.ENTRY_IDENTITY_INVALID,
            )
            for item in raw_protocols
        )
        if list(protocols) != sorted(set(protocols)):
            _fail(RegistryErrorCode.ENTRY_IDENTITY_INVALID)
        source_repository = _bounded_string(
            value["source_repository"],
            maximum=512,
            code=RegistryErrorCode.ENTRY_PROVENANCE_INVALID,
        )
        release_tag = _bounded_string(
            value["release_tag"],
            maximum=80,
            code=RegistryErrorCode.ENTRY_PROVENANCE_INVALID,
        )
        if release_tag != f"v{version}":
            _fail(RegistryErrorCode.ENTRY_PROVENANCE_INVALID)
        source_commit = value["source_commit"]
        if (
            not isinstance(source_commit, str)
            or not _COMMIT_SHA.fullmatch(source_commit)
        ):
            _fail(RegistryErrorCode.ENTRY_PROVENANCE_INVALID)
        image_revision = value["image_revision"]
        if (
            not isinstance(image_revision, str)
            or not _COMMIT_SHA.fullmatch(image_revision)
        ):
            _fail(RegistryErrorCode.ENTRY_IMAGE_INVALID)
        image_index_digest = _digest(
            value["image_index_digest"],
            code=RegistryErrorCode.ENTRY_IMAGE_INVALID,
        )
        architecture = value["architecture_image_digests"]
        if (
            not isinstance(architecture, dict)
            or not {"linux/amd64", "linux/arm64"} <= set(architecture)
            or set(architecture) - _PLATFORMS
        ):
            _fail(RegistryErrorCode.ENTRY_IMAGE_INVALID)
        architecture_items = tuple(
            sorted(
                (
                    platform,
                    _digest(
                        digest,
                        code=RegistryErrorCode.ENTRY_IMAGE_INVALID,
                    ),
                )
                for platform, digest in architecture.items()
            )
        )
        advertised_tool_count = value["advertised_tool_count"]
        if (
            isinstance(advertised_tool_count, bool)
            or not isinstance(advertised_tool_count, int)
            or not 1 <= advertised_tool_count <= 512
        ):
            _fail(RegistryErrorCode.ENTRY_CATALOG_INVALID)
        catalog_fingerprint = _fingerprint(
            value["catalog_fingerprint"],
            code=RegistryErrorCode.ENTRY_CATALOG_INVALID,
        )
        capture_resource = _bounded_string(
            value["capture_resource"],
            maximum=512,
            code=RegistryErrorCode.ENTRY_EVIDENCE_INVALID,
        )
        capture_sha256 = _digest(
            value["capture_sha256"],
            code=RegistryErrorCode.ENTRY_EVIDENCE_INVALID,
        )
        capture_format_version = value["capture_format_version"]
        if (
            isinstance(capture_format_version, bool)
            or not isinstance(capture_format_version, int)
            or capture_format_version < 1
        ):
            _fail(RegistryErrorCode.ENTRY_EVIDENCE_INVALID)
        policy_resource = _bounded_string(
            value["policy_resource"],
            maximum=512,
            code=RegistryErrorCode.ENTRY_EVIDENCE_INVALID,
        )
        policy_sha256 = _digest(
            value["policy_sha256"],
            code=RegistryErrorCode.ENTRY_EVIDENCE_INVALID,
        )
        raw_provenance = value["review_provenance"]
        if not isinstance(raw_provenance, list) or not raw_provenance:
            _fail(RegistryErrorCode.ENTRY_EVIDENCE_INVALID)
        provenance = tuple(
            _bounded_string(
                item,
                maximum=512,
                code=RegistryErrorCode.ENTRY_EVIDENCE_INVALID,
            )
            for item in raw_provenance
        )
        review_date = value["review_date"]
        if not isinstance(review_date, str) or not _DATE.fullmatch(
            review_date
        ):
            _fail(RegistryErrorCode.ENTRY_EVIDENCE_INVALID)
        try:
            datetime.strptime(review_date, "%Y-%m-%d")
        except ValueError:
            _fail(RegistryErrorCode.ENTRY_EVIDENCE_INVALID)
        error_contract = _fingerprint(
            value["error_contract_fingerprint"],
            code=RegistryErrorCode.ENTRY_EVIDENCE_INVALID,
        )
        entity_status = value["entity_lookup_missing_resource_status"]
        if entity_status not in _ENTITY_LOOKUP_STATUSES:
            _fail(RegistryErrorCode.ENTRY_EVIDENCE_INVALID)
        raw_tool_contracts = value["tool_contracts"]
        if (
            not isinstance(raw_tool_contracts, list)
            or not raw_tool_contracts
            or len(raw_tool_contracts) != advertised_tool_count
        ):
            _fail(RegistryErrorCode.TOOL_CONTRACT_INVALID)
        tool_contracts = tuple(
            ToolContract.from_mapping(item)
            for item in raw_tool_contracts
        )
        tool_names = [item.tool_name for item in tool_contracts]
        if (
            len(tool_names) != len(set(tool_names))
            or tool_names != sorted(tool_names)
        ):
            _fail(RegistryErrorCode.TOOL_CONTRACT_INVALID)
        raw_constraints = value["provider_argument_constraints"]
        if not isinstance(raw_constraints, list) or not raw_constraints:
            _fail(RegistryErrorCode.PROVIDER_CONSTRAINT_INVALID)
        constraints = tuple(
            ProviderArgumentConstraint.from_mapping(item)
            for item in raw_constraints
        )
        constraint_ids = [item.identity for item in constraints]
        if (
            len(constraint_ids) != len(set(constraint_ids))
            or constraint_ids != sorted(constraint_ids)
            or any(
                item.tool_name not in set(tool_names)
                for item in constraints
            )
        ):
            _fail(RegistryErrorCode.PROVIDER_CONSTRAINT_INVALID)
        return cls(
            entry_id=entry_id,
            approval_status="reviewed",
            server_name=server_name,
            version=version,
            allowed_protocol_versions=protocols,
            source_repository=source_repository,
            release_tag=release_tag,
            source_commit=source_commit,
            image_index_digest=image_index_digest,
            architecture_image_digests=architecture_items,
            image_revision=image_revision,
            advertised_tool_count=advertised_tool_count,
            catalog_fingerprint=catalog_fingerprint,
            capture_resource=capture_resource,
            capture_sha256=capture_sha256,
            capture_format_version=capture_format_version,
            policy_resource=policy_resource,
            policy_sha256=policy_sha256,
            review_provenance=provenance,
            review_date=review_date,
            dashboard_attestation=(
                DashboardAttestationContract.from_mapping(
                    value["dashboard_attestation"]
                )
            ),
            error_contract_fingerprint=error_contract,
            entity_lookup_missing_resource_status=entity_status,
            tool_contracts=tool_contracts,
            provider_argument_constraints=constraints,
        )

    @property
    def release_identity(self) -> tuple[str, str]:
        return self.server_name, self.version

    @property
    def revocation_identity(self) -> tuple[str, str, str, str]:
        return (
            self.entry_id,
            self.server_name,
            self.version,
            self.image_index_digest,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "approval_status": self.approval_status,
            "server_name": self.server_name,
            "version": self.version,
            "allowed_protocol_versions": list(
                self.allowed_protocol_versions
            ),
            "source_repository": self.source_repository,
            "release_tag": self.release_tag,
            "source_commit": self.source_commit,
            "image_index_digest": self.image_index_digest,
            "architecture_image_digests": dict(
                self.architecture_image_digests
            ),
            "image_revision": self.image_revision,
            "advertised_tool_count": self.advertised_tool_count,
            "catalog_fingerprint": self.catalog_fingerprint,
            "capture_resource": self.capture_resource,
            "capture_sha256": self.capture_sha256,
            "capture_format_version": self.capture_format_version,
            "policy_resource": self.policy_resource,
            "policy_sha256": self.policy_sha256,
            "review_provenance": list(self.review_provenance),
            "review_date": self.review_date,
            "dashboard_attestation": (
                self.dashboard_attestation.to_mapping()
            ),
            "error_contract_fingerprint": (
                self.error_contract_fingerprint
            ),
            "entity_lookup_missing_resource_status": (
                self.entity_lookup_missing_resource_status
            ),
            "tool_contracts": [
                item.to_mapping() for item in self.tool_contracts
            ],
            "provider_argument_constraints": [
                item.to_mapping()
                for item in self.provider_argument_constraints
            ],
        }


@dataclass(frozen=True)
class ReleaseRevocation:
    """A signed tombstone for one exact reviewed-release identity."""

    entry_id: str
    server_name: str
    version: str
    image_index_digest: str
    revoked_at: str
    reason: str

    @classmethod
    def from_mapping(cls, value: Any) -> "ReleaseRevocation":
        value = _exact_mapping(
            value,
            {
                "entry_id",
                "server_name",
                "version",
                "image_index_digest",
                "revoked_at",
                "reason",
            },
            RegistryErrorCode.REVOCATION_FIELDS_INVALID,
        )
        entry_id = _identifier(
            value["entry_id"],
            code=RegistryErrorCode.REVOCATION_IDENTITY_INVALID,
        )
        server_name = _bounded_string(
            value["server_name"],
            maximum=64,
            code=RegistryErrorCode.REVOCATION_IDENTITY_INVALID,
        )
        version = value["version"]
        if not isinstance(version, str) or not _SEMANTIC_VERSION.fullmatch(
            version
        ):
            _fail(RegistryErrorCode.REVOCATION_IDENTITY_INVALID)
        image_digest = _digest(
            value["image_index_digest"],
            code=RegistryErrorCode.REVOCATION_IDENTITY_INVALID,
        )
        revoked_at = value["revoked_at"]
        parse_utc_timestamp(
            revoked_at,
            code=RegistryErrorCode.REVOCATION_IDENTITY_INVALID,
        )
        reason = _bounded_string(
            value["reason"],
            maximum=512,
            code=RegistryErrorCode.REVOCATION_IDENTITY_INVALID,
        )
        return cls(
            entry_id=entry_id,
            server_name=server_name,
            version=version,
            image_index_digest=image_digest,
            revoked_at=revoked_at,
            reason=reason,
        )

    @property
    def release_identity(self) -> tuple[str, str]:
        return self.server_name, self.version

    @property
    def revocation_identity(self) -> tuple[str, str, str, str]:
        return (
            self.entry_id,
            self.server_name,
            self.version,
            self.image_index_digest,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "server_name": self.server_name,
            "version": self.version,
            "image_index_digest": self.image_index_digest,
            "revoked_at": self.revoked_at,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RegistryEnvelope:
    """Closed signed registry envelope with normalized nested models."""

    schema_version: int
    registry_id: str
    sequence: int
    generated_at: str
    expires_at: str
    previous_registry_sha256: str | None
    key_id: str
    entries: tuple[ReviewedReleaseEntry, ...]
    revocations: tuple[ReleaseRevocation, ...]
    signature: str

    @classmethod
    def from_bytes(cls, raw: bytes) -> "RegistryEnvelope":
        return cls.from_mapping(_strict_json_loads(raw))

    @classmethod
    def from_mapping(cls, value: Any) -> "RegistryEnvelope":
        value = _exact_mapping(
            value,
            {
                "schema_version",
                "registry_id",
                "sequence",
                "generated_at",
                "expires_at",
                "previous_registry_sha256",
                "key_id",
                "entries",
                "revocations",
                "signature",
            },
            RegistryErrorCode.ENVELOPE_FIELDS_INVALID,
        )
        if (
            isinstance(value["schema_version"], bool)
            or not isinstance(value["schema_version"], int)
            or value["schema_version"] != SCHEMA_VERSION
        ):
            _fail(RegistryErrorCode.UNKNOWN_SCHEMA_VERSION)
        registry_id = _identifier(
            value["registry_id"],
            code=RegistryErrorCode.REGISTRY_ID_INVALID,
        )
        sequence = value["sequence"]
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 1
        ):
            _fail(RegistryErrorCode.SEQUENCE_INVALID)
        generated_at = value["generated_at"]
        expires_at = value["expires_at"]
        generated = parse_utc_timestamp(generated_at)
        expires = parse_utc_timestamp(expires_at)
        if expires <= generated:
            _fail(RegistryErrorCode.EXPIRATION_INVALID)
        previous_digest = value["previous_registry_sha256"]
        if previous_digest is not None:
            previous_digest = _digest(
                previous_digest,
                code=RegistryErrorCode.PREVIOUS_DIGEST_INVALID,
            )
        if (sequence == 1) != (previous_digest is None):
            _fail(RegistryErrorCode.PREVIOUS_DIGEST_INVALID)
        key_id = value["key_id"]
        if not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id):
            _fail(RegistryErrorCode.KEY_ID_INVALID)
        raw_entries = value["entries"]
        if (
            not isinstance(raw_entries, list)
            or len(raw_entries) > MAX_ENTRIES
        ):
            _fail(RegistryErrorCode.ENTRIES_INVALID)
        entries = tuple(
            ReviewedReleaseEntry.from_mapping(item)
            for item in raw_entries
        )
        raw_revocations = value["revocations"]
        if (
            not isinstance(raw_revocations, list)
            or len(raw_revocations) > MAX_REVOCATIONS
        ):
            _fail(RegistryErrorCode.REVOCATIONS_INVALID)
        revocations = tuple(
            ReleaseRevocation.from_mapping(item)
            for item in raw_revocations
        )
        if any(
            parse_utc_timestamp(
                item.revoked_at,
                code=RegistryErrorCode.REVOCATION_IDENTITY_INVALID,
            )
            > generated
            for item in revocations
        ):
            _fail(RegistryErrorCode.REVOCATION_IDENTITY_INVALID)
        _validate_registry_identities(entries, revocations)
        signature = _parse_signature(value["signature"])
        return cls(
            schema_version=SCHEMA_VERSION,
            registry_id=registry_id,
            sequence=sequence,
            generated_at=generated_at,
            expires_at=expires_at,
            previous_registry_sha256=previous_digest,
            key_id=key_id,
            entries=entries,
            revocations=revocations,
            signature=signature,
        )

    def unsigned_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "registry_id": self.registry_id,
            "sequence": self.sequence,
            "generated_at": self.generated_at,
            "expires_at": self.expires_at,
            "previous_registry_sha256": (
                self.previous_registry_sha256
            ),
            "key_id": self.key_id,
            "entries": [item.to_mapping() for item in self.entries],
            "revocations": [
                item.to_mapping() for item in self.revocations
            ],
        }

    def to_mapping(self) -> dict[str, Any]:
        return {**self.unsigned_mapping(), "signature": self.signature}

    @property
    def canonical_unsigned(self) -> bytes:
        return canonical_json(self.unsigned_mapping())

    @property
    def content_digest(self) -> str:
        return sha256_digest(self.unsigned_mapping())

    @property
    def signature_bytes(self) -> bytes:
        try:
            return base64.b64decode(
                self.signature,
                validate=True,
            )
        except (binascii.Error, ValueError):
            _fail(RegistryErrorCode.SIGNATURE_ENCODING_INVALID)


def _parse_signature(value: Any) -> str:
    if not isinstance(value, str):
        _fail(RegistryErrorCode.SIGNATURE_ENCODING_INVALID)
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        _fail(RegistryErrorCode.SIGNATURE_ENCODING_INVALID)
    if len(decoded) != 64:
        _fail(RegistryErrorCode.SIGNATURE_LENGTH_INVALID)
    if base64.b64encode(decoded).decode("ascii") != value:
        _fail(RegistryErrorCode.SIGNATURE_ENCODING_INVALID)
    return value


def _validate_registry_identities(
    entries: tuple[ReviewedReleaseEntry, ...],
    revocations: tuple[ReleaseRevocation, ...],
) -> None:
    entry_ids = [item.entry_id for item in entries]
    release_ids = [item.release_identity for item in entries]
    image_digests = [item.image_index_digest for item in entries]
    if (
        len(entry_ids) != len(set(entry_ids))
        or len(release_ids) != len(set(release_ids))
        or len(image_digests) != len(set(image_digests))
    ):
        _fail(RegistryErrorCode.DUPLICATE_ENTRY)
    revocation_ids = [item.entry_id for item in revocations]
    revoked_releases = [item.release_identity for item in revocations]
    if (
        len(revocation_ids) != len(set(revocation_ids))
        or len(revoked_releases) != len(set(revoked_releases))
    ):
        _fail(RegistryErrorCode.DUPLICATE_REVOCATION)
    if (
        set(entry_ids) & set(revocation_ids)
        or set(release_ids) & set(revoked_releases)
    ):
        _fail(RegistryErrorCode.ENTRY_REVOCATION_CONTRADICTION)
