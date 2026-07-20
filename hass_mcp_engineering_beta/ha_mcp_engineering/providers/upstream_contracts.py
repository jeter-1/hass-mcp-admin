"""Compiled dashboard policy and data-only upstream release attestations.

The registry can attest an exact upstream release to a family defined here.  It
cannot add a tool, argument, operation, or family.  Descriptive MCP metadata is
excluded from admission fingerprints; every dispatch-relevant schema and safety
annotation remains covered.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from ..clients.mcp import REQUIRED_DASHBOARD_TOOL


CONTRACT_FAMILY = "ha_mcp_dashboard_read_v2"
TRUST_MODE = "reviewed_argument_constrained"
REQUIRED_PROTOCOL_VERSION = "2025-03-26"
REQUIRED_SERVER_NAME = "ha-mcp"
BUILTIN_ATTESTATIONS_PATH = (
    Path(__file__).with_name("contracts") / "upstream_dashboard_builtin_attestations.json"
)

IGNORED_SCHEMA_KEYS = frozenset(
    {"description", "title", "examples", "example", "$comment"}
)
SAFETY_ANNOTATIONS = (
    "readOnlyHint",
    "destructiveHint",
    "idempotentHint",
    "openWorldHint",
)
ALLOWED_SCHEMA_PROPERTIES = frozenset(
    {
        "url_path",
        "list_only",
        "force_reload",
        "entity_id",
        "card_type",
        "heading",
        "include_config",
        "include_screenshot",
        "view_path",
        "mode",
        "query",
    }
)
COMPILED_ARGUMENT_SHAPES = {
    "list_dashboards": {
        "list_only": True,
        "include_screenshot": False,
    },
    "get_dashboard_config": {
        "url_path": "<exact-canonical-path>",
        "list_only": False,
        "force_reload": "<boolean>",
        "include_screenshot": False,
    },
}
PROHIBITED_ARGUMENTS = frozenset(
    {
        "mode",
        "query",
        "entity_id",
        "card_type",
        "heading",
        "include_config",
        "view_path",
        "full_page",
        "theme",
        "dark_mode",
        "viewport_presets",
        "width",
        "height",
        "render_timeout_seconds",
        "return_screenshot",
        "config",
        "python_transform",
        "service",
        "domain",
    }
)
HASH_CONTRACT = {
    "algorithm": "sha256",
    "serialization": "json-sort-keys-compact-ascii",
    "lowercase_hex_characters": 16,
}
OUTPUT_CONTRACT = {
    "list": {
        "required": {"success": "boolean", "dashboards": "array"},
        "optional": {"count": "integer", "warnings": "array"},
    },
    "get": {
        "required": {
            "success": "boolean",
            "url_path": "string",
            "config": "object",
            "config_hash": "lowercase-hex-16",
        },
        "optional": {"warnings": "array", "resolved_from": "string"},
    },
    "not_found": {
        "distinguishable_codes": ["DASHBOARD_NOT_FOUND", "RESOURCE_NOT_FOUND"]
    },
    "hash_contract": HASH_CONTRACT,
}


class ContractValidationError(ValueError):
    """Bounded fail-closed contract validation outcome."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class NormalizedRuntimeContract:
    input_contract: dict[str, Any]
    security_contract: dict[str, Any]
    output_contract: dict[str, Any]
    runtime_contract: dict[str, Any]
    input_fingerprint: str
    security_fingerprint: str
    output_fingerprint: str
    runtime_fingerprint: str


@dataclass(frozen=True)
class CompiledContractFamily:
    """Binary-owned family definition that signed data cannot extend."""

    family_id: str
    tool_name: str
    trust_mode: str
    protocol_version: str
    normalizer: str
    response_policy: str
    hash_contract: Mapping[str, Any]
    error_taxonomy: tuple[str, ...]


COMPILED_CONTRACT_FAMILIES = {
    CONTRACT_FAMILY: CompiledContractFamily(
        family_id=CONTRACT_FAMILY,
        tool_name=REQUIRED_DASHBOARD_TOOL,
        trust_mode=TRUST_MODE,
        protocol_version=REQUIRED_PROTOCOL_VERSION,
        normalizer="normalize_dashboard_read_v2",
        response_policy="bounded_structured_omission_before_envelope_limit",
        hash_contract=HASH_CONTRACT,
        error_taxonomy=(
            "dashboard_not_found",
            "invalid_response",
            "response_too_large",
            "upstream_error",
        ),
    )
}


@dataclass(frozen=True)
class ReleaseAttestation:
    entry_id: str
    server_name: str
    upstream_version: str
    source_tag: str
    source_commit: str
    image_index_digest: str
    platform_digests: Mapping[str, str]
    image_revision: str
    contract_family: str
    input_contract_fingerprint: str
    security_contract_fingerprint: str
    output_contract_fingerprint: str
    runtime_contract_fingerprint: str
    catalog_fingerprint: str | None
    # Informational evidence for retained pre-RC2dev9 health fields. These
    # fingerprints are deliberately excluded from admission decisions.
    raw_input_schema_fingerprint: str | None
    reviewed_security_descriptor_fingerprint: str | None
    fixture_runtime_descriptor_fingerprint: str | None
    published_runtime_descriptor_fingerprint: str | None
    review_evidence_digest: str
    reviewed_at: str
    revoked: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReleaseAttestation":
        required = {
            "entry_id",
            "server_name",
            "upstream_version",
            "source_tag",
            "source_commit",
            "image_index_digest",
            "platform_digests",
            "image_revision",
            "contract_family",
            "input_contract_fingerprint",
            "security_contract_fingerprint",
            "output_contract_fingerprint",
            "runtime_contract_fingerprint",
            "catalog_fingerprint",
            "review_evidence_digest",
            "reviewed_at",
            "revoked",
        }
        optional = {
            "raw_input_schema_fingerprint",
            "reviewed_security_descriptor_fingerprint",
            "fixture_runtime_descriptor_fingerprint",
            "published_runtime_descriptor_fingerprint",
        }
        if not required.issubset(value) or set(value) - required - optional:
            raise ContractValidationError("attestation_fields_invalid")
        platform_digests = value["platform_digests"]
        if not isinstance(platform_digests, dict) or not platform_digests:
            raise ContractValidationError("attestation_platforms_invalid")
        result = cls(
            entry_id=_bounded_string(value["entry_id"], 160),
            server_name=_bounded_string(value["server_name"], 64),
            upstream_version=_bounded_string(value["upstream_version"], 64),
            source_tag=_bounded_string(value["source_tag"], 80),
            source_commit=_bounded_string(value["source_commit"], 40),
            image_index_digest=_bounded_string(value["image_index_digest"], 71),
            platform_digests={
                _bounded_string(key, 32): _bounded_string(item, 71)
                for key, item in platform_digests.items()
            },
            image_revision=_bounded_string(value["image_revision"], 40),
            contract_family=_bounded_string(value["contract_family"], 96),
            input_contract_fingerprint=_bounded_string(
                value["input_contract_fingerprint"], 64
            ),
            security_contract_fingerprint=_bounded_string(
                value["security_contract_fingerprint"], 64
            ),
            output_contract_fingerprint=_bounded_string(
                value["output_contract_fingerprint"], 64
            ),
            runtime_contract_fingerprint=_bounded_string(
                value["runtime_contract_fingerprint"], 64
            ),
            catalog_fingerprint=(
                None
                if value["catalog_fingerprint"] is None
                else _bounded_string(value["catalog_fingerprint"], 64)
            ),
            raw_input_schema_fingerprint=_optional_fingerprint(
                value.get("raw_input_schema_fingerprint")
            ),
            reviewed_security_descriptor_fingerprint=_optional_fingerprint(
                value.get("reviewed_security_descriptor_fingerprint")
            ),
            fixture_runtime_descriptor_fingerprint=_optional_fingerprint(
                value.get("fixture_runtime_descriptor_fingerprint")
            ),
            published_runtime_descriptor_fingerprint=_optional_fingerprint(
                value.get("published_runtime_descriptor_fingerprint")
            ),
            review_evidence_digest=_bounded_string(
                value["review_evidence_digest"], 71
            ),
            reviewed_at=_bounded_string(value["reviewed_at"], 32),
            revoked=value["revoked"],
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.server_name != REQUIRED_SERVER_NAME:
            raise ContractValidationError("attestation_server_invalid")
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", self.upstream_version):
            raise ContractValidationError("attestation_version_invalid")
        if self.source_tag != f"v{self.upstream_version}":
            raise ContractValidationError("attestation_tag_invalid")
        if not re.fullmatch(r"[0-9a-f]{40}", self.source_commit):
            raise ContractValidationError("attestation_source_commit_invalid")
        if not re.fullmatch(r"[0-9a-f]{40}", self.image_revision):
            raise ContractValidationError("attestation_image_revision_invalid")
        if self.contract_family not in COMPILED_CONTRACT_FAMILIES:
            raise ContractValidationError("upstream_contract_family_unknown")
        if not isinstance(self.revoked, bool):
            raise ContractValidationError("attestation_revocation_invalid")
        for digest in (self.image_index_digest, self.review_evidence_digest):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise ContractValidationError("attestation_digest_invalid")
        if set(self.platform_digests) - {"linux/amd64", "linux/arm64", "linux/arm/v7"}:
            raise ContractValidationError("attestation_platforms_invalid")
        for digest in self.platform_digests.values():
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise ContractValidationError("attestation_digest_invalid")
        for fingerprint in (
            self.input_contract_fingerprint,
            self.security_contract_fingerprint,
            self.output_contract_fingerprint,
            self.runtime_contract_fingerprint,
        ):
            if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                raise ContractValidationError("attestation_fingerprint_invalid")
        if self.catalog_fingerprint is not None and not re.fullmatch(
            r"[0-9a-f]{64}", self.catalog_fingerprint
        ):
            raise ContractValidationError("attestation_catalog_fingerprint_invalid")
        for fingerprint in (
            self.raw_input_schema_fingerprint,
            self.reviewed_security_descriptor_fingerprint,
            self.fixture_runtime_descriptor_fingerprint,
            self.published_runtime_descriptor_fingerprint,
        ):
            if fingerprint is not None and not re.fullmatch(
                r"[0-9a-f]{64}", fingerprint
            ):
                raise ContractValidationError(
                    "attestation_informational_fingerprint_invalid"
                )


@dataclass(frozen=True)
class AdmissionDecision:
    accepted: bool
    status: str
    failure_category: str | None
    source: str | None
    contract_family: str | None
    attestation: ReleaseAttestation | None
    contract: NormalizedRuntimeContract | None
    input_match: bool = False
    security_match: bool = False
    output_match: bool = False
    runtime_match: bool = False


def canonical_json(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON and reject non-JSON values."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _normalize_json_schema(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            item_key: _normalize_json_schema(item_value, key=item_key)
            for item_key, item_value in sorted(value.items())
            if item_key not in IGNORED_SCHEMA_KEYS
        }
    if isinstance(value, list):
        items = [_normalize_json_schema(item, key=key) for item in value]
        if key in {"required", "enum", "anyOf", "oneOf", "allOf"}:
            return sorted(items, key=lambda item: canonical_json(item))
        return items
    if value is None or isinstance(value, (str, bool, int, float)):
        canonical_json(value)
        return value
    raise ContractValidationError("unsupported_schema_structure")


def _schema_types(schema: Mapping[str, Any]) -> set[str]:
    found: set[str] = set()
    declared = schema.get("type")
    if isinstance(declared, str):
        found.add(declared)
    elif isinstance(declared, list):
        found.update(item for item in declared if isinstance(item, str))
    if schema.get("nullable") is True:
        found.add("null")
    for branch_name in ("anyOf", "oneOf"):
        branches = schema.get(branch_name)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict):
                    found.update(_schema_types(branch))
    return found


def _validate_compiled_family(tool: Mapping[str, Any], protocol_version: str) -> None:
    if protocol_version != REQUIRED_PROTOCOL_VERSION:
        raise ContractValidationError("unsupported_protocol_version")
    if tool.get("name") != REQUIRED_DASHBOARD_TOOL:
        raise ContractValidationError("required_tool_missing")
    allowed_top_level = {
        "name",
        "title",
        "description",
        "inputSchema",
        "outputSchema",
        "annotations",
        "_meta",
    }
    if set(tool) - allowed_top_level:
        raise ContractValidationError("upstream_runtime_contract_mismatch")
    meta = tool.get("_meta")
    if meta is not None:
        if not isinstance(meta, dict) or set(meta) - {"fastmcp", "ha_mcp"}:
            raise ContractValidationError("upstream_runtime_contract_mismatch")
        allowed_meta = {
            "fastmcp": {"tags"},
            "ha_mcp": {"llm_api_exposed", "pinned"},
        }
        for namespace, value in meta.items():
            if not isinstance(value, dict) or set(value) - allowed_meta[namespace]:
                raise ContractValidationError("upstream_runtime_contract_mismatch")
    annotations = tool.get("annotations")
    if not isinstance(annotations, dict):
        raise ContractValidationError("upstream_security_contract_mismatch")
    allowed_annotation_keys = {*SAFETY_ANNOTATIONS, "title"}
    if set(annotations) - allowed_annotation_keys:
        raise ContractValidationError("upstream_security_contract_mismatch")
    expected = {
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    if "readOnlyHint" in annotations or any(
        annotations.get(key) is not expected_value
        for key, expected_value in expected.items()
    ):
        raise ContractValidationError("upstream_security_contract_mismatch")

    schema = tool.get("inputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ContractValidationError("upstream_input_contract_mismatch")
    if schema.get("additionalProperties") is not False:
        raise ContractValidationError("upstream_input_contract_mismatch")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ContractValidationError("upstream_input_contract_mismatch")
    if not {"url_path", "list_only", "force_reload", "include_screenshot"}.issubset(
        properties
    ):
        raise ContractValidationError("upstream_input_contract_mismatch")
    if set(properties) - ALLOWED_SCHEMA_PROPERTIES:
        raise ContractValidationError("upstream_input_contract_mismatch")
    required = schema.get("required", [])
    if not isinstance(required, list) or required:
        raise ContractValidationError("upstream_input_contract_mismatch")
    if _schema_types(properties["url_path"]) != {"string", "null"}:
        raise ContractValidationError("upstream_input_contract_mismatch")
    if properties["url_path"].get("default") is not None:
        raise ContractValidationError("upstream_input_contract_mismatch")
    for name in ("list_only", "force_reload", "include_screenshot"):
        prop = properties[name]
        if not isinstance(prop, dict) or _schema_types(prop) != {"boolean"}:
            raise ContractValidationError("upstream_input_contract_mismatch")
        if prop.get("default") is not False:
            raise ContractValidationError("upstream_input_contract_mismatch")
    if "mode" in properties:
        mode = properties["mode"]
        if _schema_types(mode) != {"string", "null"} or mode.get("default") is not None:
            raise ContractValidationError("upstream_input_contract_mismatch")
        values = {
            branch.get("const")
            for branch in mode.get("anyOf", [])
            if isinstance(branch, dict) and "const" in branch
        }
        if values != {"search"}:
            raise ContractValidationError("upstream_input_contract_mismatch")
    if "query" in properties:
        query = properties["query"]
        if _schema_types(query) != {"string", "null"} or query.get("default") is not None:
            raise ContractValidationError("upstream_input_contract_mismatch")
    if "outputSchema" in tool:
        raise ContractValidationError("upstream_output_contract_mismatch")


def normalize_runtime_contract(
    tool: Mapping[str, Any], *, protocol_version: str
) -> NormalizedRuntimeContract:
    """Normalize one required tool under the compiled contract family."""

    _validate_compiled_family(tool, protocol_version)
    input_contract = _normalize_json_schema(tool["inputSchema"])
    annotations = tool["annotations"]
    security_contract = {
        "contract_family": CONTRACT_FAMILY,
        "tool_name": REQUIRED_DASHBOARD_TOOL,
        "annotations": {
            key: {"present": key in annotations, "value": annotations.get(key)}
            for key in SAFETY_ANNOTATIONS
        },
        "input_schema": input_contract,
        "compiled_argument_shapes": COMPILED_ARGUMENT_SHAPES,
        "prohibited_arguments": sorted(PROHIBITED_ARGUMENTS),
    }
    output_contract = {
        "declared_output_schema": {
            "present": "outputSchema" in tool,
            "value": _normalize_json_schema(tool.get("outputSchema")),
        },
        "engineering_consumed_contract": OUTPUT_CONTRACT,
    }
    runtime_contract = {
        "contract_family": CONTRACT_FAMILY,
        "protocol_version": protocol_version,
        "tool_name": REQUIRED_DASHBOARD_TOOL,
        "input": input_contract,
        "security": security_contract,
        "output": output_contract,
    }
    return NormalizedRuntimeContract(
        input_contract=input_contract,
        security_contract=security_contract,
        output_contract=output_contract,
        runtime_contract=runtime_contract,
        input_fingerprint=stable_hash(input_contract),
        security_fingerprint=stable_hash(security_contract),
        output_fingerprint=stable_hash(output_contract),
        runtime_fingerprint=stable_hash(runtime_contract),
    )


def load_attestations(path: Path = BUILTIN_ATTESTATIONS_PATH) -> tuple[ReleaseAttestation, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "entries"}:
        raise ContractValidationError("builtin_attestations_invalid")
    if payload["schema_version"] != 1 or not isinstance(payload["entries"], list):
        raise ContractValidationError("builtin_attestations_invalid")
    entries = tuple(ReleaseAttestation.from_mapping(item) for item in payload["entries"])
    _validate_unique_attestations(entries)
    return entries


def _validate_unique_attestations(entries: Iterable[ReleaseAttestation]) -> None:
    identifiers: set[str] = set()
    release_keys: set[tuple[str, str, str]] = set()
    for entry in entries:
        key = (entry.server_name, entry.upstream_version, entry.contract_family)
        if entry.entry_id in identifiers:
            raise ContractValidationError("duplicate_attestation_entry")
        if key in release_keys:
            raise ContractValidationError("conflicting_attestation_entry")
        identifiers.add(entry.entry_id)
        release_keys.add(key)


def decide_admission(
    *,
    server_name: str,
    server_version: str,
    protocol_version: str,
    tool: Mapping[str, Any] | None,
    attestations: Iterable[tuple[ReleaseAttestation, str]],
) -> AdmissionDecision:
    if server_name != REQUIRED_SERVER_NAME:
        return AdmissionDecision(
            False,
            "rejected_contract_mismatch",
            "server_identity_mismatch",
            None,
            None,
            None,
            None,
        )
    if tool is None or tool.get("name") != REQUIRED_DASHBOARD_TOOL:
        return AdmissionDecision(
            False,
            "rejected_contract_mismatch",
            "required_tool_missing",
            None,
            None,
            None,
            None,
        )
    try:
        contract = normalize_runtime_contract(tool, protocol_version=protocol_version)
    except ContractValidationError as exc:
        return AdmissionDecision(
            False,
            "rejected_contract_mismatch",
            exc.reason,
            None,
            CONTRACT_FAMILY,
            None,
            None,
        )

    matching = [
        (entry, source)
        for entry, source in attestations
        if entry.server_name == server_name
        and entry.upstream_version == server_version
        and entry.contract_family == CONTRACT_FAMILY
    ]
    if not matching:
        return AdmissionDecision(
            False,
            "rejected_unknown_release",
            "upstream_attestation_missing",
            None,
            CONTRACT_FAMILY,
            None,
            contract,
        )
    entry, source = matching[0]
    if entry.revoked:
        return AdmissionDecision(
            False,
            "rejected_revoked_attestation",
            "upstream_attestation_revoked",
            source,
            CONTRACT_FAMILY,
            entry,
            contract,
        )
    matches = {
        "input": contract.input_fingerprint == entry.input_contract_fingerprint,
        "security": contract.security_fingerprint
        == entry.security_contract_fingerprint,
        "output": contract.output_fingerprint == entry.output_contract_fingerprint,
        "runtime": contract.runtime_fingerprint == entry.runtime_contract_fingerprint,
    }
    for key in ("input", "security", "output", "runtime"):
        if not matches[key]:
            return AdmissionDecision(
                False,
                "rejected_contract_mismatch",
                f"upstream_{key}_contract_mismatch",
                source,
                CONTRACT_FAMILY,
                entry,
                contract,
                input_match=matches["input"],
                security_match=matches["security"],
                output_match=matches["output"],
                runtime_match=matches["runtime"],
            )
    return AdmissionDecision(
        True,
        (
            "admitted_builtin_attestation"
            if source == "builtin"
            else "admitted_signed_registry_attestation"
        ),
        None,
        source,
        CONTRACT_FAMILY,
        entry,
        contract,
        input_match=True,
        security_match=True,
        output_match=True,
        runtime_match=True,
    )


def _bounded_string(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ContractValidationError("attestation_value_invalid")
    return value


def _optional_fingerprint(value: Any) -> str | None:
    if value is None:
        return None
    return _bounded_string(value, 64)
