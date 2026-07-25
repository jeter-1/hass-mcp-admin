"""Deterministic, fail-closed policy for reviewed upstream MCP tools."""

from __future__ import annotations

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
RELEASE_REGISTRY_FORMAT_VERSION = 1
REVIEWED_UPSTREAM_SERVER = "ha-mcp"
REVIEWED_UPSTREAM_VERSION = "7.14.1"
REVIEWED_UPSTREAM_PROTOCOL = "2025-03-26"
UPSTREAM_SOURCE_REPOSITORY = "https://github.com/homeassistant-ai/ha-mcp"
_POLICY_RESOURCE = re.compile(
    r"^upstream_tool_policy(?:_[0-9]+_[0-9]+_[0-9]+)?\.json$"
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
CLASSIFICATIONS = frozenset(
    {
        "automatic_read",
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
        return {name: counts.get(name, 0) for name in sorted(CLASSIFICATIONS)}

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
    policy_resource: str
    policy_sha256: str
    review_provenance: tuple[str, ...]
    review_date: str
    dashboard_attestation_status: str
    dashboard_attestation_entry_id: str | None
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
        "policy_resource",
        "policy_sha256",
        "review_provenance",
        "review_date",
        "dashboard_attestation",
        "error_contract_fingerprint",
        "entity_lookup_missing_resource_status",
        "tool_contracts",
    }
    if not isinstance(value, dict) or set(value) != expected:
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
    }:
        raise UpstreamToolPolicyError(
            "release_registry_dashboard_attestation_invalid"
        )
    dashboard_status = dashboard["status"]
    dashboard_entry_id = dashboard["entry_id"]
    if dashboard_status not in {"reviewed", "quarantined"}:
        raise UpstreamToolPolicyError(
            "release_registry_dashboard_attestation_invalid"
        )
    if dashboard_status == "reviewed":
        if (
            not isinstance(dashboard_entry_id, str)
            or not 1 <= len(dashboard_entry_id) <= 128
        ):
            raise UpstreamToolPolicyError(
                "release_registry_dashboard_attestation_invalid"
            )
    elif dashboard_entry_id is not None:
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
        policy_resource=resource,
        policy_sha256=expected_policy_digest,
        review_provenance=tuple(provenance),
        review_date=review_date,
        dashboard_attestation_status=dashboard_status,
        dashboard_attestation_entry_id=dashboard_entry_id,
        error_contract_fingerprint=error_contract,
        entity_lookup_missing_resource_status=entity_status,
        tool_contracts=tuple(sorted(tool_contracts.items())),
        policy=policy,
    )
