"""Minimal offline signing core for the upstream trust registry.

This module is intentionally isolated from the Engineering application.  Its
only non-standard-library dependency is ``cryptography``.  The protected
workflow uses it to validate raw inspection evidence, reconstruct an approved
registry transition, sign prepared canonical bytes, and verify the resulting
data-only artifact set.
"""

from __future__ import annotations

import base64
import binascii
import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


CONTRACT_FAMILY = "ha_mcp_dashboard_read_v2"
REQUIRED_DASHBOARD_TOOL = "ha_config_get_dashboard"
REQUIRED_PROTOCOL_VERSION = "2025-03-26"
REQUIRED_SERVER_NAME = "ha-mcp"
LIFECYCLE_EVIDENCE_TYPE = "upstream_trust_registry_lifecycle"
REGISTRY_PATH = "upstream-trust/upstream-dashboard-registry.json"
REGISTRY_SIGNATURE_PATH = "upstream-trust/upstream-dashboard-registry.sig.json"
EVIDENCE_DIRECTORY = "docs/evidence/upstream-compatibility"
INDEX_PATH = "docs/generated/UPSTREAM_TRUST_REGISTRY_INDEX.md"
INSPECTION_ARTIFACT_NAME = "registry-inspection-evidence"
WHEELHOUSE_ARTIFACT_NAME = "registry-signing-wheelhouse"
SIGNED_ARTIFACT_NAME = "registry-signed-data"
STABLE_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
EVIDENCE_FILE_PATTERN = re.compile(r"^registry-sequence-([0-9]{6})\.json$")
MAX_JSON_BYTES = 2_000_000
MAX_REASON_LENGTH = 256
MIN_EXPIRY_DAYS = 1
MAX_EXPIRY_DAYS = 365
MUTATING_OPERATIONS = {"bootstrap", "add", "revoke", "restore", "renew"}

SAFETY_ANNOTATIONS = (
    "readOnlyHint",
    "destructiveHint",
    "idempotentHint",
    "openWorldHint",
)
IGNORED_SCHEMA_KEYS = frozenset(
    {"description", "title", "examples", "example", "$comment"}
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
    "list_dashboards": {"list_only": True, "include_screenshot": False},
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

LIFECYCLE_PAYLOAD_FIELDS = {
    "schema_version",
    "evidence_type",
    "operation",
    "old_sequence",
    "new_sequence",
    "affected_entry",
    "old_revoked",
    "new_revoked",
    "operator_reason",
    "generated_at",
    "expires_at",
    "key_id",
    "workflow_base_sha",
    "dispatch_sha",
    "prior_registry_digest",
    "current_registry_digest",
    "prior_lifecycle_evidence_digest",
    "data_only",
    "allowed_output_paths",
    "inspection_evidence_digests",
    "release_evidence_digest",
    "release_evidence",
    "previous_registry",
    "current_registry",
}
IDENTITY_FIELDS = {"entry_id", "server_name", "upstream_version", "contract_family"}
SIGNATURE_FIELDS = {"schema_version", "algorithm", "key_id", "signature"}


class SigningCoreError(ValueError):
    """Bounded signing failure without untrusted payload or key material."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class TrustedInputs:
    operation: str
    upstream_version: str
    expected_current_sequence: int
    expiry_days: int
    operator_reason: str
    workflow_base_sha: str
    dispatch_sha: str
    contract_family: str
    output_paths: tuple[str, ...]
    key_id: str


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise SigningCoreError("json_value_invalid") from None


def canonical_file(value: Any) -> bytes:
    return canonical_json(value) + b"\n"


def sha256_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _reject_constant(_value: str) -> None:
    raise SigningCoreError("json_value_invalid")


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SigningCoreError("json_duplicate_key")
        value[key] = item
    return value


def strict_json_bytes(raw: bytes, *, maximum: int = MAX_JSON_BYTES) -> Any:
    if len(raw) > maximum:
        raise SigningCoreError("json_size_exceeded")
    try:
        text = raw.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_constant,
        )
    except SigningCoreError:
        raise
    except (UnicodeError, json.JSONDecodeError):
        raise SigningCoreError("json_invalid") from None


def load_canonical_file(path: Path, *, maximum: int = MAX_JSON_BYTES) -> Any:
    try:
        raw = path.read_bytes()
    except OSError:
        raise SigningCoreError("required_artifact_missing") from None
    value = strict_json_bytes(raw, maximum=maximum)
    if raw != canonical_file(value):
        raise SigningCoreError("canonical_json_required")
    return value


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z") or len(value) > 32:
        raise SigningCoreError("timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise SigningCoreError("timestamp_invalid") from None
    if parsed.utcoffset() != timedelta(0):
        raise SigningCoreError("timestamp_invalid")
    return parsed


def _bounded_sha(value: str, category: str = "workflow_base_invalid") -> str:
    candidate = (value or "").strip().lower()
    if not SHA_PATTERN.fullmatch(candidate):
        raise SigningCoreError(category)
    return candidate


def _bounded_reason(value: str) -> str:
    reason = (value or "").strip()
    if len(reason) > MAX_REASON_LENGTH or any(ord(char) < 32 for char in reason):
        raise SigningCoreError("operator_reason_invalid")
    return reason


def lifecycle_evidence_path(sequence: int) -> str:
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise SigningCoreError("registry_sequence_invalid")
    return f"{EVIDENCE_DIRECTORY}/registry-sequence-{sequence:06d}.json"


def allowed_output_paths(sequence: int) -> list[str]:
    return sorted(
        [
            REGISTRY_PATH,
            REGISTRY_SIGNATURE_PATH,
            lifecycle_evidence_path(sequence),
            INDEX_PATH,
        ]
    )


def validate_trusted_inputs(inputs: TrustedInputs) -> TrustedInputs:
    if inputs.operation not in MUTATING_OPERATIONS:
        raise SigningCoreError("trusted_operation_invalid")
    if inputs.operation in {"bootstrap", "add", "revoke", "restore"}:
        if not STABLE_VERSION.fullmatch(inputs.upstream_version):
            raise SigningCoreError("trusted_selector_invalid")
    elif inputs.upstream_version:
        raise SigningCoreError("trusted_selector_invalid")
    if (
        isinstance(inputs.expected_current_sequence, bool)
        or inputs.expected_current_sequence < 0
    ):
        raise SigningCoreError("trusted_sequence_invalid")
    if not MIN_EXPIRY_DAYS <= inputs.expiry_days <= MAX_EXPIRY_DAYS:
        raise SigningCoreError("trusted_expiry_invalid")
    _bounded_reason(inputs.operator_reason)
    _bounded_sha(inputs.workflow_base_sha)
    _bounded_sha(inputs.dispatch_sha, "dispatch_sha_invalid")
    if inputs.contract_family != CONTRACT_FAMILY:
        raise SigningCoreError("compiled_contract_family_invalid")
    if not KEY_ID_PATTERN.fullmatch(inputs.key_id):
        raise SigningCoreError("registry_key_id_invalid")
    expected_paths = tuple(allowed_output_paths(inputs.expected_current_sequence + 1))
    if tuple(sorted(inputs.output_paths)) != expected_paths or len(inputs.output_paths) != 4:
        raise SigningCoreError("output_path_allowlist_invalid")
    return inputs


def _decode_key(value: str, category: str) -> bytes:
    try:
        raw = base64.b64decode((value or "").strip(), validate=True)
    except (binascii.Error, ValueError):
        raise SigningCoreError(category) from None
    if len(raw) != 32:
        raise SigningCoreError(category)
    return raw


def load_public_key(environment: Mapping[str, str], expected_key_id: str) -> Ed25519PublicKey:
    if environment.get("UPSTREAM_TRUST_REGISTRY_KEY_ID", "").strip() != expected_key_id:
        raise SigningCoreError("registry_key_id_mismatch")
    raw = _decode_key(
        environment.get("UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY", ""),
        "public_key_invalid",
    )
    return Ed25519PublicKey.from_public_bytes(raw)


def load_signing_key(
    environment: Mapping[str, str], expected_key_id: str
) -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    public = load_public_key(environment, expected_key_id)
    private_raw = _decode_key(
        environment.get("UPSTREAM_TRUST_REGISTRY_SIGNING_KEY", ""),
        "private_key_invalid",
    )
    private = Ed25519PrivateKey.from_private_bytes(private_raw)
    if private.public_key().public_bytes_raw() != public.public_bytes_raw():
        raise SigningCoreError("signing_key_pair_mismatch")
    return private, public


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
            return sorted(items, key=canonical_json)
        return items
    if value is None or isinstance(value, (str, bool, int, float)):
        canonical_json(value)
        return value
    raise SigningCoreError("unsupported_schema_structure")


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
        raise SigningCoreError("unsupported_protocol_version")
    if tool.get("name") != REQUIRED_DASHBOARD_TOOL:
        raise SigningCoreError("required_tool_missing")
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
        raise SigningCoreError("upstream_runtime_contract_mismatch")
    meta = tool.get("_meta")
    if meta is not None:
        if not isinstance(meta, dict) or set(meta) - {"fastmcp", "ha_mcp"}:
            raise SigningCoreError("upstream_runtime_contract_mismatch")
        allowed_meta = {
            "fastmcp": {"tags"},
            "ha_mcp": {"llm_api_exposed", "pinned"},
        }
        for namespace, item in meta.items():
            if not isinstance(item, dict) or set(item) - allowed_meta[namespace]:
                raise SigningCoreError("upstream_runtime_contract_mismatch")
    annotations = tool.get("annotations")
    if not isinstance(annotations, dict):
        raise SigningCoreError("upstream_security_contract_mismatch")
    if set(annotations) - {*SAFETY_ANNOTATIONS, "title"}:
        raise SigningCoreError("upstream_security_contract_mismatch")
    expected_annotations = {
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    if "readOnlyHint" in annotations or any(
        annotations.get(key) is not expected
        for key, expected in expected_annotations.items()
    ):
        raise SigningCoreError("upstream_security_contract_mismatch")
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise SigningCoreError("upstream_input_contract_mismatch")
    if schema.get("additionalProperties") is not False:
        raise SigningCoreError("upstream_input_contract_mismatch")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise SigningCoreError("upstream_input_contract_mismatch")
    required_properties = {"url_path", "list_only", "force_reload", "include_screenshot"}
    if not required_properties.issubset(properties) or set(properties) - ALLOWED_SCHEMA_PROPERTIES:
        raise SigningCoreError("upstream_input_contract_mismatch")
    required = schema.get("required", [])
    if not isinstance(required, list) or required:
        raise SigningCoreError("upstream_input_contract_mismatch")
    if _schema_types(properties["url_path"]) != {"string", "null"}:
        raise SigningCoreError("upstream_input_contract_mismatch")
    if properties["url_path"].get("default") is not None:
        raise SigningCoreError("upstream_input_contract_mismatch")
    for name in ("list_only", "force_reload", "include_screenshot"):
        prop = properties[name]
        if not isinstance(prop, dict) or _schema_types(prop) != {"boolean"}:
            raise SigningCoreError("upstream_input_contract_mismatch")
        if prop.get("default") is not False:
            raise SigningCoreError("upstream_input_contract_mismatch")
    if "mode" in properties:
        mode = properties["mode"]
        if _schema_types(mode) != {"string", "null"} or mode.get("default") is not None:
            raise SigningCoreError("upstream_input_contract_mismatch")
        values = {
            branch.get("const")
            for branch in mode.get("anyOf", [])
            if isinstance(branch, dict) and "const" in branch
        }
        if values != {"search"}:
            raise SigningCoreError("upstream_input_contract_mismatch")
    if "query" in properties:
        query = properties["query"]
        if _schema_types(query) != {"string", "null"} or query.get("default") is not None:
            raise SigningCoreError("upstream_input_contract_mismatch")
    if "outputSchema" in tool:
        raise SigningCoreError("upstream_output_contract_mismatch")


def normalize_runtime_contract(tool: Mapping[str, Any], protocol_version: str) -> dict[str, str]:
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
    return {
        "input": hashlib.sha256(canonical_json(input_contract)).hexdigest(),
        "security": hashlib.sha256(canonical_json(security_contract)).hexdigest(),
        "output": hashlib.sha256(canonical_json(output_contract)).hexdigest(),
        "runtime": hashlib.sha256(canonical_json(runtime_contract)).hexdigest(),
    }


def reviewed_security_projection(tool: Mapping[str, Any]) -> dict[str, Any]:
    annotations = tool.get("annotations")
    if not isinstance(annotations, dict):
        annotations = {"__malformed__": annotations}
    annotation_projection = {
        "safety": {
            key: {"present": key in annotations, "value": annotations.get(key)}
            for key in SAFETY_ANNOTATIONS
        },
        "unreviewed": {
            key: value
            for key, value in annotations.items()
            if key not in {*SAFETY_ANNOTATIONS, "title"}
        },
    }
    meta = tool.get("_meta")
    unreviewed_meta: dict[str, Any] = {}
    if meta is not None:
        if not isinstance(meta, dict):
            unreviewed_meta["__malformed__"] = meta
        else:
            for namespace, value in meta.items():
                if not isinstance(value, dict):
                    unreviewed_meta[namespace] = value
                    continue
                excluded = (
                    {"tags"}
                    if namespace == "fastmcp"
                    else {"llm_api_exposed", "pinned"}
                    if namespace == "ha_mcp"
                    else set()
                )
                remaining = {key: item for key, item in value.items() if key not in excluded}
                if remaining:
                    unreviewed_meta[namespace] = remaining
    known_top_level = {
        "name",
        "title",
        "description",
        "inputSchema",
        "outputSchema",
        "annotations",
        "_meta",
    }
    return {
        "name": tool.get("name"),
        "inputSchema": tool.get("inputSchema"),
        "outputSchema": {
            "present": "outputSchema" in tool,
            "value": tool.get("outputSchema"),
        },
        "annotations": annotation_projection,
        "unreviewedTopLevel": {
            key: value for key, value in tool.items() if key not in known_top_level
        },
        "unreviewedMetadata": unreviewed_meta,
    }


def _validate_entry(entry: Mapping[str, Any]) -> None:
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
    if not isinstance(entry, dict) or not required.issubset(entry) or set(entry) - required - optional:
        raise SigningCoreError("attestation_fields_invalid")
    version = entry.get("upstream_version")
    if entry.get("server_name") != REQUIRED_SERVER_NAME or not isinstance(version, str) or not STABLE_VERSION.fullmatch(version):
        raise SigningCoreError("attestation_identity_invalid")
    if entry.get("source_tag") != f"v{version}" or entry.get("contract_family") != CONTRACT_FAMILY:
        raise SigningCoreError("attestation_identity_invalid")
    for name in ("source_commit", "image_revision"):
        if not SHA_PATTERN.fullmatch(str(entry.get(name, ""))):
            raise SigningCoreError("attestation_commit_invalid")
    if not isinstance(entry.get("revoked"), bool):
        raise SigningCoreError("attestation_revocation_invalid")
    for name in ("image_index_digest", "review_evidence_digest"):
        if not DIGEST_PATTERN.fullmatch(str(entry.get(name, ""))):
            raise SigningCoreError("attestation_digest_invalid")
    platforms = entry.get("platform_digests")
    if not isinstance(platforms, dict) or not platforms or set(platforms) - {
        "linux/amd64",
        "linux/arm64",
        "linux/arm/v7",
    }:
        raise SigningCoreError("attestation_platforms_invalid")
    if any(not DIGEST_PATTERN.fullmatch(str(value)) for value in platforms.values()):
        raise SigningCoreError("attestation_digest_invalid")
    fingerprint_names = (
        "input_contract_fingerprint",
        "security_contract_fingerprint",
        "output_contract_fingerprint",
        "runtime_contract_fingerprint",
    )
    for name in fingerprint_names:
        if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get(name, ""))):
            raise SigningCoreError("attestation_fingerprint_invalid")
    for name in (
        "catalog_fingerprint",
        "raw_input_schema_fingerprint",
        "reviewed_security_descriptor_fingerprint",
        "fixture_runtime_descriptor_fingerprint",
        "published_runtime_descriptor_fingerprint",
    ):
        value = entry.get(name)
        if value is not None and not re.fullmatch(r"[0-9a-f]{64}", str(value)):
            raise SigningCoreError("attestation_fingerprint_invalid")
    _parse_utc(entry.get("reviewed_at"))


def validate_registry(registry: Mapping[str, Any]) -> None:
    if not isinstance(registry, dict) or set(registry) != {
        "schema_version",
        "sequence",
        "generated_at",
        "expires_at",
        "key_id",
        "entries",
    }:
        raise SigningCoreError("registry_schema_invalid")
    if registry.get("schema_version") != 1:
        raise SigningCoreError("registry_schema_invalid")
    sequence = registry.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise SigningCoreError("registry_sequence_invalid")
    if not KEY_ID_PATTERN.fullmatch(str(registry.get("key_id", ""))):
        raise SigningCoreError("registry_key_id_invalid")
    generated = _parse_utc(registry.get("generated_at"))
    expires = _parse_utc(registry.get("expires_at"))
    if expires <= generated:
        raise SigningCoreError("registry_timestamp_invalid")
    entries = registry.get("entries")
    if not isinstance(entries, list):
        raise SigningCoreError("registry_entries_invalid")
    entry_ids: set[str] = set()
    identities: set[tuple[str, str, str]] = set()
    for entry in entries:
        _validate_entry(entry)
        entry_id = str(entry["entry_id"])
        identity = (
            str(entry["server_name"]),
            str(entry["upstream_version"]),
            str(entry["contract_family"]),
        )
        if entry_id in entry_ids or identity in identities:
            raise SigningCoreError("registry_entry_duplicate")
        entry_ids.add(entry_id)
        identities.add(identity)


def _signature_object(signature_raw: bytes, expected_key_id: str) -> Mapping[str, Any]:
    signature = strict_json_bytes(signature_raw, maximum=4096)
    if not isinstance(signature, dict) or set(signature) != SIGNATURE_FIELDS:
        raise SigningCoreError("registry_signature_invalid")
    if (
        signature.get("schema_version") != 1
        or signature.get("algorithm") != "Ed25519"
        or signature.get("key_id") != expected_key_id
    ):
        raise SigningCoreError("registry_signature_invalid")
    return signature


def verify_registry_bytes(
    registry_raw: bytes,
    signature_raw: bytes,
    *,
    public_key: Ed25519PublicKey,
    expected_key_id: str,
) -> dict[str, Any]:
    registry = strict_json_bytes(registry_raw)
    signature = _signature_object(signature_raw, expected_key_id)
    if not isinstance(registry, dict):
        raise SigningCoreError("registry_schema_invalid")
    if registry_raw != canonical_file(registry) or signature_raw != canonical_file(signature):
        raise SigningCoreError("canonical_json_required")
    validate_registry(registry)
    if registry.get("key_id") != expected_key_id:
        raise SigningCoreError("registry_key_id_mismatch")
    try:
        encoded = base64.b64decode(str(signature["signature"]), validate=True)
        public_key.verify(encoded, canonical_json(registry))
    except (binascii.Error, InvalidSignature, ValueError):
        raise SigningCoreError("registry_signature_invalid") from None
    return registry


def _validate_exact_directory(directory: Path, expected_files: set[str]) -> None:
    if not directory.is_dir() or directory.is_symlink():
        raise SigningCoreError("artifact_directory_invalid")
    actual: set[str] = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise SigningCoreError("artifact_symlink_rejected")
        relative = path.relative_to(directory).as_posix()
        if path.is_dir():
            raise SigningCoreError("artifact_directory_unexpected")
        if relative.startswith("../") or relative.startswith("/") or relative in actual:
            raise SigningCoreError("artifact_path_invalid")
        actual.add(relative)
    if actual != expected_files:
        raise SigningCoreError("artifact_file_set_mismatch")


def validate_inspection_artifact(directory: Path, trusted: TrustedInputs) -> dict[str, Any]:
    manifest_path = directory / "inspection-manifest.json"
    manifest = load_canonical_file(manifest_path)
    if not isinstance(manifest, dict):
        raise SigningCoreError("inspection_manifest_invalid")
    required_fields = {
        "schema_version",
        "artifact_name",
        "wheelhouse_artifact_name",
        "root_files",
        "operation",
        "upstream_version",
        "expected_current_sequence",
        "expiry_days",
        "operator_reason",
        "workflow_base_sha",
        "dispatch_sha",
        "contract_family",
        "evidence_digests",
    }
    if set(manifest) != required_fields or manifest.get("schema_version") != 1:
        raise SigningCoreError("inspection_manifest_invalid")
    if (
        manifest.get("artifact_name") != INSPECTION_ARTIFACT_NAME
        or manifest.get("wheelhouse_artifact_name") != WHEELHOUSE_ARTIFACT_NAME
        or manifest.get("operation") != trusted.operation
        or manifest.get("upstream_version") != trusted.upstream_version
        or manifest.get("expected_current_sequence") != trusted.expected_current_sequence
        or manifest.get("expiry_days") != trusted.expiry_days
        or manifest.get("operator_reason") != trusted.operator_reason
        or manifest.get("workflow_base_sha") != trusted.workflow_base_sha
        or manifest.get("dispatch_sha") != trusted.dispatch_sha
        or manifest.get("contract_family") != trusted.contract_family
    ):
        raise SigningCoreError("inspection_trusted_input_mismatch")
    root_files = manifest.get("root_files")
    if not isinstance(root_files, list) or any(not isinstance(item, str) for item in root_files):
        raise SigningCoreError("inspection_manifest_invalid")
    expected = set(root_files)
    _validate_exact_directory(directory, expected)
    digests = manifest.get("evidence_digests")
    evidence_files = expected - {"inspection-manifest.json"}
    if not isinstance(digests, dict) or set(digests) != evidence_files:
        raise SigningCoreError("inspection_evidence_digest_invalid")
    for name in evidence_files:
        if digests[name] != sha256_digest((directory / name).read_bytes()):
            raise SigningCoreError("inspection_evidence_digest_mismatch")
    expected_evidence = (
        {"runtime-evidence.json", "release-evidence.json"}
        if trusted.operation in {"bootstrap", "add"}
        else {"current-registry.json", "current-registry-signature.json"}
    )
    if evidence_files != expected_evidence:
        raise SigningCoreError("inspection_file_layout_invalid")
    return manifest


def _reconstruct_release_entry(
    directory: Path, *, version: str, reviewed_at: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime = load_canonical_file(directory / "runtime-evidence.json")
    release = load_canonical_file(directory / "release-evidence.json")
    if not isinstance(runtime, dict) or not isinstance(release, dict):
        raise SigningCoreError("release_evidence_invalid")
    if runtime.get("server_name") != REQUIRED_SERVER_NAME or runtime.get("server_version") != version:
        raise SigningCoreError("runtime_identity_mismatch")
    if release.get("version") != version or release.get("source_tag") != f"v{version}":
        raise SigningCoreError("release_identity_mismatch")
    if (
        release.get("official_repository") != "homeassistant-ai/ha-mcp"
        or release.get("official_image") != "ghcr.io/homeassistant-ai/ha-mcp"
        or release.get("image_source") != "https://github.com/homeassistant-ai/ha-mcp"
        or release.get("slsa_provenance") != "present_per_platform"
        or release.get("dirty_label") not in {"false", "absent"}
    ):
        raise SigningCoreError("release_provenance_invalid")
    tool = runtime.get("required_tool")
    if not isinstance(tool, dict):
        raise SigningCoreError("runtime_tool_evidence_invalid")
    fingerprints = normalize_runtime_contract(tool, str(runtime.get("protocol_version")))
    if runtime.get("contract_fingerprints") != fingerprints:
        raise SigningCoreError("runtime_contract_evidence_mismatch")
    informational = runtime.get("informational_fingerprints")
    if not isinstance(informational, dict) or set(informational) != {
        "raw_input_schema",
        "reviewed_security_descriptor",
        "fixture_runtime_descriptor",
        "published_runtime_descriptor",
    }:
        raise SigningCoreError("runtime_informational_evidence_invalid")
    expected_informational = {
        "raw_input_schema": hashlib.sha256(canonical_json(tool.get("inputSchema"))).hexdigest(),
        "reviewed_security_descriptor": hashlib.sha256(
            canonical_json(reviewed_security_projection(tool))
        ).hexdigest(),
        "published_runtime_descriptor": hashlib.sha256(canonical_json(tool)).hexdigest(),
    }
    for name, value in expected_informational.items():
        if informational.get(name) != value:
            raise SigningCoreError("runtime_informational_evidence_mismatch")
    fixture_descriptor = informational.get("fixture_runtime_descriptor")
    if not re.fullmatch(r"[0-9a-f]{64}", str(fixture_descriptor)):
        raise SigningCoreError("runtime_informational_evidence_invalid")
    required_rejections = {
        "ha_set_entity",
        "ha_set_device",
        "ha_call_service",
        "ha_bulk_control",
        "ha_config_set_dashboard",
        "ha_config_delete_dashboard",
    }
    negative = runtime.get("negative_reachability")
    if (
        runtime.get("write_dispatches") != 0
        or not isinstance(negative, dict)
        or set(negative.get("rejected_before_dispatch", [])) != required_rejections
        or negative.get("include_screenshot_true_rejected") is not True
        or negative.get("generic_forwarder_present") is not False
    ):
        raise SigningCoreError("negative_reachability_evidence_invalid")
    review_evidence = {
        "schema_version": 1,
        "version": version,
        "release": release,
        "runtime": runtime,
        "compiled_contract_family": CONTRACT_FAMILY,
        "reviewed_at": reviewed_at,
    }
    review_digest = sha256_digest(canonical_file(review_evidence))
    image_digest = str(release.get("image_index_digest", ""))
    entry = {
        "entry_id": f"ha-mcp-v{version}-{image_digest.split(':')[-1][:8]}",
        "server_name": REQUIRED_SERVER_NAME,
        "upstream_version": version,
        "source_tag": f"v{version}",
        "source_commit": release.get("source_commit"),
        "image_index_digest": image_digest,
        "platform_digests": release.get("platform_digests"),
        "image_revision": release.get("image_revision"),
        "contract_family": CONTRACT_FAMILY,
        "input_contract_fingerprint": fingerprints["input"],
        "security_contract_fingerprint": fingerprints["security"],
        "output_contract_fingerprint": fingerprints["output"],
        "runtime_contract_fingerprint": fingerprints["runtime"],
        "catalog_fingerprint": runtime.get("catalog_fingerprint"),
        "raw_input_schema_fingerprint": informational["raw_input_schema"],
        "reviewed_security_descriptor_fingerprint": informational[
            "reviewed_security_descriptor"
        ],
        "fixture_runtime_descriptor_fingerprint": fixture_descriptor,
        "published_runtime_descriptor_fingerprint": informational[
            "published_runtime_descriptor"
        ],
        "review_evidence_digest": review_digest,
        "reviewed_at": reviewed_at,
        "revoked": False,
    }
    _validate_entry(entry)
    return entry, review_evidence


def _git(repository: Path, *args: str, binary: bool = False) -> bytes | str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repository,
            check=True,
            capture_output=True,
            text=not binary,
        )
    except (OSError, subprocess.CalledProcessError):
        raise SigningCoreError("git_snapshot_read_failed") from None
    return result.stdout if binary else result.stdout.strip()


def _git_optional_file(repository: Path, relative: str) -> bytes | None:
    result = subprocess.run(
        ["git", "show", f"refs/remotes/origin/main:{relative}"],
        cwd=repository,
        capture_output=True,
    )
    return result.stdout if result.returncode == 0 else None


def _read_current_from_origin(
    repository: Path,
    *,
    public_key: Ed25519PublicKey,
    key_id: str,
    expected_sequence: int,
) -> dict[str, Any] | None:
    registry_raw = _git_optional_file(repository, REGISTRY_PATH)
    signature_raw = _git_optional_file(repository, REGISTRY_SIGNATURE_PATH)
    if expected_sequence == 0:
        if registry_raw is not None or signature_raw is not None:
            raise SigningCoreError("workflow_sequence_stale")
        return None
    if registry_raw is None or signature_raw is None:
        raise SigningCoreError("workflow_sequence_stale")
    registry = verify_registry_bytes(
        registry_raw,
        signature_raw,
        public_key=public_key,
        expected_key_id=key_id,
    )
    if registry.get("sequence") != expected_sequence:
        raise SigningCoreError("workflow_sequence_stale")
    return registry


def _find_entry(entries: list[dict[str, Any]], version: str) -> dict[str, Any]:
    matches = [
        entry
        for entry in entries
        if entry.get("server_name") == REQUIRED_SERVER_NAME
        and entry.get("upstream_version") == version
        and entry.get("contract_family") == CONTRACT_FAMILY
    ]
    if len(matches) != 1:
        raise SigningCoreError("registry_selector_mismatch")
    return matches[0]


def _affected_identity(entry: Mapping[str, Any] | None) -> dict[str, str] | None:
    if entry is None:
        return None
    return {name: str(entry[name]) for name in sorted(IDENTITY_FIELDS)}


def _previous_evidence_digest(repository: Path, sequence: int) -> str | None:
    if sequence == 0:
        return None
    raw = _git_optional_file(repository, lifecycle_evidence_path(sequence))
    if raw is None:
        raise SigningCoreError("lifecycle_evidence_chain_incomplete")
    value = strict_json_bytes(raw)
    if raw != canonical_file(value):
        raise SigningCoreError("lifecycle_evidence_not_canonical")
    return sha256_digest(canonical_json(value))


def prepare_signing(
    *,
    inspection_directory: Path,
    prepared_directory: Path,
    repository: Path,
    trusted: TrustedInputs,
    environment: Mapping[str, str],
    now: datetime | None = None,
) -> dict[str, Any]:
    trusted = validate_trusted_inputs(trusted)
    manifest = validate_inspection_artifact(inspection_directory, trusted)
    public_key = load_public_key(environment, trusted.key_id)
    resolved = str(_git(repository, "rev-parse", "refs/remotes/origin/main"))
    if resolved != trusted.workflow_base_sha:
        raise SigningCoreError("workflow_base_moved")
    current = _read_current_from_origin(
        repository,
        public_key=public_key,
        key_id=trusted.key_id,
        expected_sequence=trusted.expected_current_sequence,
    )
    if trusted.expected_current_sequence:
        inspected_registry = (inspection_directory / "current-registry.json").read_bytes()
        inspected_signature = (
            inspection_directory / "current-registry-signature.json"
        ).read_bytes()
        origin_registry = _git_optional_file(repository, REGISTRY_PATH)
        origin_signature = _git_optional_file(repository, REGISTRY_SIGNATURE_PATH)
        if inspected_registry != origin_registry or inspected_signature != origin_signature:
            raise SigningCoreError("inspection_current_snapshot_mismatch")
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0
    )
    generated_at = _utc_text(clock)
    expires_at = _utc_text(clock + timedelta(days=trusted.expiry_days))
    entries = [] if current is None else copy.deepcopy(current["entries"])
    affected: dict[str, Any] | None = None
    old_revoked: bool | None = None
    release_evidence: dict[str, Any] | None = None
    if trusted.operation in {"bootstrap", "add"}:
        affected, release_evidence = _reconstruct_release_entry(
            inspection_directory,
            version=trusted.upstream_version,
            reviewed_at=generated_at,
        )
        identity = (
            affected["server_name"],
            affected["upstream_version"],
            affected["contract_family"],
        )
        if any(item.get("entry_id") == affected["entry_id"] for item in entries):
            raise SigningCoreError("duplicate_registry_entry")
        if any(
            (item.get("server_name"), item.get("upstream_version"), item.get("contract_family"))
            == identity
            for item in entries
        ):
            raise SigningCoreError("duplicate_registry_identity")
        entries.append(affected)
    elif trusted.operation in {"revoke", "restore"}:
        affected = _find_entry(entries, trusted.upstream_version)
        old_revoked = affected.get("revoked")
        if not isinstance(old_revoked, bool):
            raise SigningCoreError("registry_revocation_invalid")
        if trusted.operation == "revoke" and old_revoked:
            raise SigningCoreError("registry_already_revoked")
        if trusted.operation == "restore" and not old_revoked:
            raise SigningCoreError("registry_not_revoked")
        affected["revoked"] = trusted.operation == "revoke"
    proposed = {
        "schema_version": 1,
        "sequence": trusted.expected_current_sequence + 1,
        "generated_at": generated_at,
        "expires_at": expires_at,
        "key_id": trusted.key_id,
        "entries": entries,
    }
    validate_registry(proposed)
    if proposed["sequence"] != trusted.expected_current_sequence + 1:
        raise SigningCoreError("proposed_sequence_invalid")
    evidence_digests = manifest["evidence_digests"]
    inspection_digests = (
        {
            "release_evidence": evidence_digests["release-evidence.json"],
            "runtime_evidence": evidence_digests["runtime-evidence.json"],
        }
        if trusted.operation in {"bootstrap", "add"}
        else {
            "current_registry": evidence_digests["current-registry.json"],
            "current_registry_signature": evidence_digests[
                "current-registry-signature.json"
            ],
        }
    )
    release_digest = (
        None if release_evidence is None else sha256_digest(canonical_file(release_evidence))
    )
    lifecycle_payload = {
        "schema_version": 1,
        "evidence_type": LIFECYCLE_EVIDENCE_TYPE,
        "operation": trusted.operation,
        "old_sequence": trusted.expected_current_sequence,
        "new_sequence": proposed["sequence"],
        "affected_entry": _affected_identity(affected),
        "old_revoked": old_revoked,
        "new_revoked": None if affected is None else affected["revoked"],
        "operator_reason": trusted.operator_reason or None,
        "generated_at": generated_at,
        "expires_at": expires_at,
        "key_id": trusted.key_id,
        "workflow_base_sha": trusted.workflow_base_sha,
        "dispatch_sha": trusted.dispatch_sha,
        "prior_registry_digest": (
            None if current is None else sha256_digest(canonical_json(current))
        ),
        "current_registry_digest": sha256_digest(canonical_json(proposed)),
        "prior_lifecycle_evidence_digest": _previous_evidence_digest(
            repository, trusted.expected_current_sequence
        ),
        "data_only": True,
        "allowed_output_paths": list(trusted.output_paths),
        "inspection_evidence_digests": inspection_digests,
        "release_evidence_digest": release_digest,
        "release_evidence": release_evidence,
        "previous_registry": current,
        "current_registry": proposed,
    }
    _verify_transition(lifecycle_payload, current, proposed)
    prepared_manifest = {
        "schema_version": 1,
        "operation": trusted.operation,
        "upstream_version": trusted.upstream_version,
        "expected_current_sequence": trusted.expected_current_sequence,
        "new_sequence": proposed["sequence"],
        "expiry_days": trusted.expiry_days,
        "operator_reason": trusted.operator_reason,
        "workflow_base_sha": trusted.workflow_base_sha,
        "dispatch_sha": trusted.dispatch_sha,
        "contract_family": trusted.contract_family,
        "output_paths": list(trusted.output_paths),
        "key_id": trusted.key_id,
        "registry_digest": sha256_digest(canonical_json(proposed)),
        "lifecycle_payload_digest": sha256_digest(canonical_json(lifecycle_payload)),
        "inspection_manifest_digest": sha256_digest(canonical_json(manifest)),
        "prepared": True,
    }
    if prepared_directory.exists():
        shutil.rmtree(prepared_directory)
    prepared_directory.mkdir(parents=True)
    (prepared_directory / "canonical-registry.json").write_bytes(canonical_json(proposed))
    (prepared_directory / "canonical-lifecycle-payload.json").write_bytes(
        canonical_json(lifecycle_payload)
    )
    (prepared_directory / "prepared-signing-manifest.json").write_bytes(
        canonical_file(prepared_manifest)
    )
    _validate_exact_directory(
        prepared_directory,
        {
            "canonical-registry.json",
            "canonical-lifecycle-payload.json",
            "prepared-signing-manifest.json",
        },
    )
    return prepared_manifest


def _load_prepared(directory: Path, trusted: TrustedInputs) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    _validate_exact_directory(
        directory,
        {
            "canonical-registry.json",
            "canonical-lifecycle-payload.json",
            "prepared-signing-manifest.json",
        },
    )
    registry_raw = (directory / "canonical-registry.json").read_bytes()
    lifecycle_raw = (directory / "canonical-lifecycle-payload.json").read_bytes()
    registry = strict_json_bytes(registry_raw)
    lifecycle = strict_json_bytes(lifecycle_raw)
    manifest = load_canonical_file(directory / "prepared-signing-manifest.json")
    if registry_raw != canonical_json(registry) or lifecycle_raw != canonical_json(lifecycle):
        raise SigningCoreError("prepared_canonical_bytes_invalid")
    if not isinstance(registry, dict) or not isinstance(lifecycle, dict) or not isinstance(manifest, dict):
        raise SigningCoreError("prepared_artifact_invalid")
    expected_bindings = {
        "operation": trusted.operation,
        "upstream_version": trusted.upstream_version,
        "expected_current_sequence": trusted.expected_current_sequence,
        "new_sequence": trusted.expected_current_sequence + 1,
        "expiry_days": trusted.expiry_days,
        "operator_reason": trusted.operator_reason,
        "workflow_base_sha": trusted.workflow_base_sha,
        "dispatch_sha": trusted.dispatch_sha,
        "contract_family": trusted.contract_family,
        "output_paths": list(trusted.output_paths),
        "key_id": trusted.key_id,
        "registry_digest": sha256_digest(registry_raw),
        "lifecycle_payload_digest": sha256_digest(lifecycle_raw),
        "prepared": True,
    }
    for key, value in expected_bindings.items():
        if manifest.get(key) != value:
            raise SigningCoreError("prepared_trusted_input_mismatch")
    validate_registry(registry)
    if lifecycle.get("current_registry") != registry or set(lifecycle) != LIFECYCLE_PAYLOAD_FIELDS:
        raise SigningCoreError("prepared_lifecycle_invalid")
    if (
        lifecycle.get("operator_reason") != (trusted.operator_reason or None)
        or lifecycle.get("workflow_base_sha") != trusted.workflow_base_sha
        or lifecycle.get("dispatch_sha") != trusted.dispatch_sha
        or lifecycle.get("allowed_output_paths") != list(trusted.output_paths)
    ):
        raise SigningCoreError("prepared_trusted_input_mismatch")
    generated = _parse_utc(registry["generated_at"])
    expires = _parse_utc(registry["expires_at"])
    if expires - generated != timedelta(days=trusted.expiry_days):
        raise SigningCoreError("prepared_expiry_mismatch")
    return registry, lifecycle, manifest


def sign_prepared(
    *,
    prepared_directory: Path,
    signature_directory: Path,
    trusted: TrustedInputs,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    trusted = validate_trusted_inputs(trusted)
    registry, lifecycle, manifest = _load_prepared(prepared_directory, trusted)
    private, public = load_signing_key(environment, trusted.key_id)
    registry_bytes = canonical_json(registry)
    lifecycle_bytes = canonical_json(lifecycle)
    registry_signature = {
        "schema_version": 1,
        "algorithm": "Ed25519",
        "key_id": trusted.key_id,
        "signature": base64.b64encode(private.sign(registry_bytes)).decode("ascii"),
    }
    lifecycle_document = {
        "payload": lifecycle,
        "signature": {
            "schema_version": 1,
            "algorithm": "Ed25519",
            "key_id": trusted.key_id,
            "signature": base64.b64encode(private.sign(lifecycle_bytes)).decode("ascii"),
        },
    }
    verify_registry_bytes(
        canonical_file(registry),
        canonical_file(registry_signature),
        public_key=public,
        expected_key_id=trusted.key_id,
    )
    try:
        public.verify(
            base64.b64decode(lifecycle_document["signature"]["signature"], validate=True),
            lifecycle_bytes,
        )
    except (binascii.Error, InvalidSignature, ValueError):
        raise SigningCoreError("lifecycle_signature_invalid") from None
    result_manifest = {
        "schema_version": 1,
        "prepared_manifest_digest": sha256_digest(canonical_json(manifest)),
        "registry_digest": sha256_digest(registry_bytes),
        "lifecycle_payload_digest": sha256_digest(lifecycle_bytes),
        "registry_signature_digest": sha256_digest(canonical_json(registry_signature)),
        "lifecycle_document_digest": sha256_digest(canonical_json(lifecycle_document)),
        "key_id": trusted.key_id,
        "signed": True,
    }
    if signature_directory.exists():
        shutil.rmtree(signature_directory)
    signature_directory.mkdir(parents=True)
    (signature_directory / "registry-signature.json").write_bytes(
        canonical_file(registry_signature)
    )
    (signature_directory / "lifecycle-evidence.json").write_bytes(
        canonical_file(lifecycle_document)
    )
    (signature_directory / "signing-result-manifest.json").write_bytes(
        canonical_file(result_manifest)
    )
    _validate_exact_directory(
        signature_directory,
        {
            "registry-signature.json",
            "lifecycle-evidence.json",
            "signing-result-manifest.json",
        },
    )
    return result_manifest


def _entries_by_id(registry: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["entry_id"]: entry for entry in registry["entries"]}


def _verify_transition(
    payload: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> None:
    operation = payload.get("operation")
    affected = payload.get("affected_entry")
    if affected is not None and (
        not isinstance(affected, dict) or set(affected) != IDENTITY_FIELDS
    ):
        raise SigningCoreError("lifecycle_identity_invalid")
    previous_entries = {} if previous is None else _entries_by_id(previous)
    current_entries = _entries_by_id(current)
    if operation == "bootstrap":
        valid = (
            previous is None
            and payload.get("old_sequence") == 0
            and payload.get("new_sequence") == 1
            and len(current_entries) == 1
            and affected is not None
            and affected.get("entry_id") in current_entries
            and payload.get("old_revoked") is None
            and payload.get("new_revoked") is False
        )
    elif operation == "add":
        expected = dict(previous_entries)
        if affected is not None:
            expected[affected["entry_id"]] = current_entries.get(affected["entry_id"])
        valid = (
            affected is not None
            and affected["entry_id"] not in previous_entries
            and expected == current_entries
            and payload.get("old_revoked") is None
            and payload.get("new_revoked") is False
        )
    elif operation in {"revoke", "restore"}:
        expected = copy.deepcopy(previous_entries)
        if affected is not None and affected.get("entry_id") in expected:
            expected[affected["entry_id"]]["revoked"] = operation == "revoke"
        valid = (
            affected is not None
            and affected.get("entry_id") in previous_entries
            and expected == current_entries
            and payload.get("old_revoked") is (operation == "restore")
            and payload.get("new_revoked") is (operation == "revoke")
        )
    elif operation == "renew":
        valid = (
            affected is None
            and previous_entries == current_entries
            and payload.get("old_revoked") is None
            and payload.get("new_revoked") is None
        )
    else:
        valid = False
    if not valid:
        raise SigningCoreError("lifecycle_transition_invalid")


def render_index(registry: Mapping[str, Any], workflow_base_sha: str) -> bytes:
    rows = "".join(
        f"| {item['upstream_version']} | `{item['entry_id']}` | "
        f"`{item['contract_family']}` | {str(item['revoked']).lower()} |\n"
        for item in registry["entries"]
    )
    value = (
        "# Upstream trust registry index\n\n"
        f"Sequence: `{registry['sequence']}`  \n"
        f"Generated: `{registry['generated_at']}`  \n"
        f"Expires: `{registry['expires_at']}`  \n"
        f"Workflow base: `{workflow_base_sha}`  \n\n"
        "| Version | Entry | Family | Revoked |\n"
        "|---|---|---|---|\n"
        f"{rows}"
    )
    return value.encode("utf-8")


def _verify_lifecycle_signature(
    document: Mapping[str, Any], public: Ed25519PublicKey, key_id: str
) -> Mapping[str, Any]:
    if not isinstance(document, dict) or set(document) != {"payload", "signature"}:
        raise SigningCoreError("lifecycle_evidence_schema_invalid")
    payload = document.get("payload")
    signature = document.get("signature")
    if not isinstance(payload, dict) or set(payload) != LIFECYCLE_PAYLOAD_FIELDS:
        raise SigningCoreError("lifecycle_evidence_schema_invalid")
    if not isinstance(signature, dict) or set(signature) != SIGNATURE_FIELDS:
        raise SigningCoreError("lifecycle_evidence_signature_invalid")
    if (
        signature.get("schema_version") != 1
        or signature.get("algorithm") != "Ed25519"
        or signature.get("key_id") != key_id
    ):
        raise SigningCoreError("lifecycle_evidence_signature_invalid")
    try:
        public.verify(
            base64.b64decode(str(signature.get("signature", "")), validate=True),
            canonical_json(payload),
        )
    except (binascii.Error, InvalidSignature, ValueError):
        raise SigningCoreError("lifecycle_evidence_signature_invalid") from None
    return payload


def verify_tree(
    tree: Path,
    *,
    environment: Mapping[str, str],
    expected_key_id: str,
) -> dict[str, Any]:
    public = load_public_key(environment, expected_key_id)
    registry = verify_registry_bytes(
        (tree / REGISTRY_PATH).read_bytes(),
        (tree / REGISTRY_SIGNATURE_PATH).read_bytes(),
        public_key=public,
        expected_key_id=expected_key_id,
    )
    sequence = registry["sequence"]
    evidence_root = tree / EVIDENCE_DIRECTORY
    discovered: dict[int, Path] = {}
    if evidence_root.exists():
        for path in evidence_root.iterdir():
            if path.is_symlink() or not path.is_file():
                raise SigningCoreError("lifecycle_evidence_path_invalid")
            match = EVIDENCE_FILE_PATTERN.fullmatch(path.name)
            if match is None:
                raise SigningCoreError("lifecycle_evidence_path_invalid")
            number = int(match.group(1))
            if number in discovered:
                raise SigningCoreError("lifecycle_evidence_sequence_duplicate")
            discovered[number] = path
    if sorted(discovered) != list(range(1, sequence + 1)):
        raise SigningCoreError("lifecycle_evidence_chain_incomplete")
    previous: Mapping[str, Any] | None = None
    previous_evidence_digest: str | None = None
    introduced: dict[str, str] = {}
    last_payload: Mapping[str, Any] | None = None
    for number in range(1, sequence + 1):
        raw = discovered[number].read_bytes()
        document = strict_json_bytes(raw)
        if not isinstance(document, dict) or raw != canonical_file(document):
            raise SigningCoreError("lifecycle_evidence_not_canonical")
        payload = _verify_lifecycle_signature(document, public, expected_key_id)
        current = payload.get("current_registry")
        if not isinstance(current, dict):
            raise SigningCoreError("lifecycle_registry_snapshot_invalid")
        validate_registry(current)
        if (
            payload.get("schema_version") != 1
            or payload.get("evidence_type") != LIFECYCLE_EVIDENCE_TYPE
            or payload.get("old_sequence") != number - 1
            or payload.get("new_sequence") != number
            or payload.get("key_id") != expected_key_id
            or payload.get("data_only") is not True
            or payload.get("previous_registry") != previous
            or current.get("sequence") != number
            or payload.get("prior_registry_digest")
            != (None if previous is None else sha256_digest(canonical_json(previous)))
            or payload.get("current_registry_digest") != sha256_digest(canonical_json(current))
            or payload.get("prior_lifecycle_evidence_digest") != previous_evidence_digest
            or payload.get("generated_at") != current.get("generated_at")
            or payload.get("expires_at") != current.get("expires_at")
            or payload.get("allowed_output_paths") != allowed_output_paths(number)
            or not SHA_PATTERN.fullmatch(str(payload.get("workflow_base_sha", "")))
            or not SHA_PATTERN.fullmatch(str(payload.get("dispatch_sha", "")))
        ):
            raise SigningCoreError("lifecycle_evidence_chain_mismatch")
        inspection = payload.get("inspection_evidence_digests")
        if (
            not isinstance(inspection, dict)
            or not inspection
            or any(
                not isinstance(key, str) or not DIGEST_PATTERN.fullmatch(str(value))
                for key, value in inspection.items()
            )
        ):
            raise SigningCoreError("lifecycle_inspection_evidence_invalid")
        release_evidence = payload.get("release_evidence")
        release_digest = payload.get("release_evidence_digest")
        if payload.get("operation") in {"bootstrap", "add"}:
            if (
                not isinstance(release_evidence, dict)
                or not DIGEST_PATTERN.fullmatch(str(release_digest))
                or sha256_digest(canonical_file(release_evidence)) != release_digest
            ):
                raise SigningCoreError("lifecycle_release_evidence_invalid")
            affected = payload.get("affected_entry")
            if not isinstance(affected, dict):
                raise SigningCoreError("lifecycle_release_evidence_invalid")
            introduced[str(affected["entry_id"])] = str(release_digest)
        elif release_evidence is not None or release_digest is not None:
            raise SigningCoreError("lifecycle_release_evidence_invalid")
        _verify_transition(payload, previous, current)
        previous = current
        previous_evidence_digest = sha256_digest(canonical_json(document))
        last_payload = payload
    if previous != registry or last_payload is None:
        raise SigningCoreError("lifecycle_current_registry_mismatch")
    for entry in registry["entries"]:
        if introduced.get(entry["entry_id"]) != entry["review_evidence_digest"]:
            raise SigningCoreError("review_evidence_digest_mismatch")
    if (tree / INDEX_PATH).read_bytes() != render_index(
        registry, str(last_payload["workflow_base_sha"])
    ):
        raise SigningCoreError("generated_index_mismatch")
    return {
        "sequence": sequence,
        "registry_digest": sha256_digest(canonical_json(registry)),
        "last_lifecycle_evidence_digest": previous_evidence_digest,
        "workflow_base_sha": last_payload["workflow_base_sha"],
        "dispatch_sha": last_payload["dispatch_sha"],
        "last_operation": last_payload["operation"],
        "entry_count": len(registry["entries"]),
    }


def _copy_origin_managed_tree(repository: Path, tree: Path, sequence: int) -> None:
    if tree.exists():
        shutil.rmtree(tree)
    tree.mkdir(parents=True)
    if sequence == 0:
        return
    paths = [REGISTRY_PATH, REGISTRY_SIGNATURE_PATH, INDEX_PATH]
    paths.extend(lifecycle_evidence_path(number) for number in range(1, sequence + 1))
    for relative in paths:
        raw = _git_optional_file(repository, relative)
        if raw is None:
            raise SigningCoreError("origin_managed_set_incomplete")
        target = tree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def verify_and_assemble_artifacts(
    *,
    prepared_directory: Path,
    signature_directory: Path,
    output_directory: Path,
    repository: Path,
    trusted: TrustedInputs,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    if environment.get("UPSTREAM_TRUST_REGISTRY_SIGNING_KEY"):
        raise SigningCoreError("private_key_present_during_public_verification")
    trusted = validate_trusted_inputs(trusted)
    registry, lifecycle, prepared_manifest = _load_prepared(prepared_directory, trusted)
    _validate_exact_directory(
        signature_directory,
        {
            "registry-signature.json",
            "lifecycle-evidence.json",
            "signing-result-manifest.json",
        },
    )
    signature_value = load_canonical_file(signature_directory / "registry-signature.json")
    lifecycle_document = load_canonical_file(signature_directory / "lifecycle-evidence.json")
    signing_manifest = load_canonical_file(
        signature_directory / "signing-result-manifest.json"
    )
    public = load_public_key(environment, trusted.key_id)
    verify_registry_bytes(
        canonical_file(registry),
        canonical_file(signature_value),
        public_key=public,
        expected_key_id=trusted.key_id,
    )
    verified_payload = _verify_lifecycle_signature(
        lifecycle_document, public, trusted.key_id
    )
    if verified_payload != lifecycle:
        raise SigningCoreError("signed_lifecycle_payload_mismatch")
    expected_signing = {
        "prepared_manifest_digest": sha256_digest(canonical_json(prepared_manifest)),
        "registry_digest": sha256_digest(canonical_json(registry)),
        "lifecycle_payload_digest": sha256_digest(canonical_json(lifecycle)),
        "registry_signature_digest": sha256_digest(canonical_json(signature_value)),
        "lifecycle_document_digest": sha256_digest(canonical_json(lifecycle_document)),
        "key_id": trusted.key_id,
        "signed": True,
    }
    if not isinstance(signing_manifest, dict) or signing_manifest.get("schema_version") != 1:
        raise SigningCoreError("signing_result_manifest_invalid")
    for key, value in expected_signing.items():
        if signing_manifest.get(key) != value:
            raise SigningCoreError("signing_result_manifest_mismatch")
    if output_directory.exists():
        shutil.rmtree(output_directory)
    tree = output_directory / "tree"
    _copy_origin_managed_tree(repository, tree, trusted.expected_current_sequence)
    replacements = {
        REGISTRY_PATH: canonical_file(registry),
        REGISTRY_SIGNATURE_PATH: canonical_file(signature_value),
        lifecycle_evidence_path(registry["sequence"]): canonical_file(lifecycle_document),
        INDEX_PATH: render_index(registry, trusted.workflow_base_sha),
    }
    for relative, content in replacements.items():
        target = tree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    verified = verify_tree(tree, environment=environment, expected_key_id=trusted.key_id)
    affected = lifecycle.get("affected_entry")
    affected_entry = None
    if isinstance(affected, dict):
        affected_entry = next(
            entry
            for entry in registry["entries"]
            if entry["entry_id"] == affected["entry_id"]
        )
    evidence_digest = sha256_digest(canonical_json(lifecycle_document))
    operation_summary = {
        "schema_version": 1,
        "operation": trusted.operation,
        "dry_run": False,
        "old_sequence": trusted.expected_current_sequence,
        "new_sequence": registry["sequence"],
        "expected_current_sequence": trusted.expected_current_sequence,
        "workflow_base_sha": trusted.workflow_base_sha,
        "dispatch_sha": trusted.dispatch_sha,
        "affected_entry_id": None if affected_entry is None else affected_entry["entry_id"],
        "server_name": None if affected_entry is None else affected_entry["server_name"],
        "upstream_version": None if affected_entry is None else affected_entry["upstream_version"],
        "contract_family": trusted.contract_family,
        "source_commit": None if affected_entry is None else affected_entry["source_commit"],
        "image_index_digest": None if affected_entry is None else affected_entry["image_index_digest"],
        "old_revoked": lifecycle["old_revoked"],
        "new_revoked": lifecycle["new_revoked"],
        "generated_at": registry["generated_at"],
        "expires_at": registry["expires_at"],
        "key_id": trusted.key_id,
        "registry_digest": verified["registry_digest"],
        "prior_registry_digest": lifecycle["prior_registry_digest"],
        "prior_lifecycle_evidence_digest": lifecycle[
            "prior_lifecycle_evidence_digest"
        ],
        "evidence_digest": evidence_digest,
        "evidence_path": lifecycle_evidence_path(registry["sequence"]),
        "allowed_output_paths": list(trusted.output_paths),
        "data_only": True,
        "engineering_image_change_required": False,
        "written": True,
    }
    publication_manifest = {
        "schema_version": 1,
        "operation": trusted.operation,
        "workflow_base_sha": trusted.workflow_base_sha,
        "dispatch_sha": trusted.dispatch_sha,
        "expected_current_sequence": trusted.expected_current_sequence,
        "new_sequence": registry["sequence"],
        "registry_digest": verified["registry_digest"],
        "lifecycle_evidence_digest": evidence_digest,
        "changed_paths": list(trusted.output_paths),
        "data_only": True,
        "verified": True,
    }
    (output_directory / "operation-summary.json").write_bytes(
        canonical_file(operation_summary)
    )
    (output_directory / "publication-manifest.json").write_bytes(
        canonical_file(publication_manifest)
    )
    return publication_manifest


def verify_signed_artifact_directory(
    directory: Path,
    *,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    if directory.is_symlink() or not directory.is_dir():
        raise SigningCoreError("publication_artifact_invalid")
    root_names = {path.name for path in directory.iterdir()}
    if root_names != {"tree", "operation-summary.json", "publication-manifest.json"}:
        raise SigningCoreError("publication_artifact_file_set_mismatch")
    manifest = load_canonical_file(directory / "publication-manifest.json")
    summary = load_canonical_file(directory / "operation-summary.json")
    if not isinstance(manifest, dict) or not isinstance(summary, dict):
        raise SigningCoreError("publication_manifest_invalid")
    key_id = summary.get("key_id")
    if not isinstance(key_id, str):
        raise SigningCoreError("publication_manifest_invalid")
    verified = verify_tree(directory / "tree", environment=environment, expected_key_id=key_id)
    if (
        manifest.get("verified") is not True
        or manifest.get("data_only") is not True
        or manifest.get("registry_digest") != verified["registry_digest"]
        or manifest.get("lifecycle_evidence_digest")
        != verified["last_lifecycle_evidence_digest"]
        or manifest.get("new_sequence") != verified["sequence"]
        or manifest.get("workflow_base_sha") != verified["workflow_base_sha"]
        or manifest.get("dispatch_sha") != verified["dispatch_sha"]
        or manifest.get("operation") != verified["last_operation"]
        or manifest.get("changed_paths") != allowed_output_paths(verified["sequence"])
    ):
        raise SigningCoreError("publication_manifest_binding_mismatch")
    if (
        summary.get("operation") != manifest.get("operation")
        or summary.get("workflow_base_sha") != manifest.get("workflow_base_sha")
        or summary.get("dispatch_sha") != manifest.get("dispatch_sha")
        or summary.get("registry_digest") != manifest.get("registry_digest")
        or summary.get("evidence_digest") != manifest.get("lifecycle_evidence_digest")
        or summary.get("allowed_output_paths") != manifest.get("changed_paths")
    ):
        raise SigningCoreError("operation_summary_binding_mismatch")
    return {**verified, "publication_manifest_valid": True}


def validate_main_freshness_values(
    *,
    resolved_main_sha: str,
    workflow_base_sha: str,
    current_sequence: int,
    expected_current_sequence: int,
    phase: str,
    signed_base_sha: str | None = None,
) -> None:
    resolved = _bounded_sha(resolved_main_sha)
    base = _bounded_sha(workflow_base_sha)
    if phase == "publication" and signed_base_sha is not None:
        signed = _bounded_sha(signed_base_sha, "signed_base_mismatch")
        if signed != base:
            raise SigningCoreError("signed_base_mismatch")
    if resolved != base:
        raise SigningCoreError(
            "workflow_base_moved" if phase == "signing" else "publication_base_moved"
        )
    if current_sequence != expected_current_sequence:
        raise SigningCoreError("workflow_sequence_stale")


def check_origin_main_freshness(
    *,
    repository: Path,
    workflow_base_sha: str,
    expected_current_sequence: int,
    phase: str,
    environment: Mapping[str, str],
    signed_base_sha: str | None = None,
) -> dict[str, Any]:
    if phase not in {"signing", "publication"}:
        raise SigningCoreError("freshness_phase_invalid")
    try:
        subprocess.run(
            [
                "git",
                "fetch",
                "--no-tags",
                "origin",
                "+refs/heads/main:refs/remotes/origin/main",
            ],
            cwd=repository,
            check=True,
            capture_output=True,
        )
        resolved = str(_git(repository, "rev-parse", "refs/remotes/origin/main"))
    except SigningCoreError:
        raise SigningCoreError(
            "workflow_base_moved" if phase == "signing" else "publication_base_moved"
        ) from None
    if expected_current_sequence == 0:
        if (
            _git_optional_file(repository, REGISTRY_PATH) is not None
            or _git_optional_file(repository, REGISTRY_SIGNATURE_PATH) is not None
        ):
            raise SigningCoreError("workflow_sequence_stale")
        current_sequence = 0
    else:
        key_id = environment.get("UPSTREAM_TRUST_REGISTRY_KEY_ID", "").strip()
        public = load_public_key(environment, key_id)
        registry = _read_current_from_origin(
            repository,
            public_key=public,
            key_id=key_id,
            expected_sequence=expected_current_sequence,
        )
        if registry is None:
            raise SigningCoreError("workflow_sequence_stale")
        current_sequence = int(registry["sequence"])
    validate_main_freshness_values(
        resolved_main_sha=resolved,
        workflow_base_sha=workflow_base_sha,
        current_sequence=current_sequence,
        expected_current_sequence=expected_current_sequence,
        phase=phase,
        signed_base_sha=signed_base_sha,
    )
    return {
        "schema_version": 1,
        "phase": phase,
        "origin_main_sha": resolved,
        "workflow_base_sha": workflow_base_sha,
        "expected_current_sequence": expected_current_sequence,
        "current_sequence": current_sequence,
        "fresh": True,
    }
