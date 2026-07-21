"""Administer the data-only upstream trust registry.

The direct operator path pre-verifies a complete output set, replaces each file
atomically, verifies the resulting set, and restores every original on failure.
The workflow path uses the same candidate, signing, evidence-chain, and public
verification primitives without giving the signing process repository-write
authority.
"""

from __future__ import annotations

import argparse
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
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.providers.upstream_contracts import (  # noqa: E402
    CONTRACT_FAMILY,
    ReleaseAttestation,
    canonical_json,
    normalize_runtime_contract,
)
from ha_mcp_engineering.providers.upstream_registry import (  # noqa: E402
    RegistryValidationError,
    _parse_public_key,
    _strict_json_loads,
    re_full_key_id,
    verify_registry,
)


STABLE_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
EVIDENCE_FILE_PATTERN = re.compile(r"^registry-sequence-([0-9]{6})\.json$")
DEFAULT_EXPIRY_DAYS = 90
MIN_EXPIRY_DAYS = 1
MAX_EXPIRY_DAYS = 365
MAX_REASON_LENGTH = 256
MUTATING_OPERATIONS = {"bootstrap", "add", "revoke", "restore", "renew"}
OPERATIONS = (*sorted(MUTATING_OPERATIONS), "verify")
LIFECYCLE_EVIDENCE_TYPE = "upstream_trust_registry_lifecycle"
GENESIS_DIGEST = None


class RegistryOperationError(ValueError):
    """Bounded operator error that contains no remote payload or key material."""

    def __init__(self, message: str, *, category: str = "registry_operation_failed"):
        super().__init__(message)
        self.category = category


class RegistrySetWriteError(RegistryOperationError):
    """A complete-set write failed; the category describes the safe outcome."""

    def __init__(self, category: str, *, rollback_status: str | None = None):
        message = category if rollback_status is None else f"{category}:{rollback_status}"
        super().__init__(message, category=category)
        self.rollback_status = rollback_status


@dataclass(frozen=True)
class RegistryPaths:
    registry: Path
    signature: Path
    evidence_directory: Path
    index: Path
    runtime_evidence: Path
    release_evidence: Path


DEFAULT_PATHS = RegistryPaths(
    registry=ROOT / "upstream-trust" / "upstream-dashboard-registry.json",
    signature=ROOT / "upstream-trust" / "upstream-dashboard-registry.sig.json",
    evidence_directory=ROOT / "docs" / "evidence" / "upstream-compatibility",
    index=ROOT / "docs" / "generated" / "UPSTREAM_TRUST_REGISTRY_INDEX.md",
    runtime_evidence=ROOT / ".compat" / "runtime-evidence.json",
    release_evidence=ROOT / ".compat" / "release-evidence.json",
)


@dataclass(frozen=True)
class SigningMaterial:
    private_key: Ed25519PrivateKey
    public_text: str
    key_id: str


@dataclass(frozen=True)
class PublicMaterial:
    public_text: str
    key_id: str


@dataclass(frozen=True)
class MutationCandidate:
    registry: dict[str, Any]
    lifecycle_payload: dict[str, Any]
    evidence_path: Path
    old_sequence: int
    affected: dict[str, Any] | None
    old_revoked: bool | None
    release_evidence: dict[str, Any] | None
    inspection_manifest: dict[str, Any]


@dataclass(frozen=True)
class OriginalFile:
    existed: bool
    content: bytes | None
    mode: int | None


ReplaceFunction = Callable[[Path, Path], None]
VerifyFunction = Callable[..., dict[str, Any]]


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def sha256_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical_file(value: Any) -> bytes:
    return canonical_json(value) + b"\n"


def _bounded_sha(value: str, *, category: str = "workflow_base_invalid") -> str:
    candidate = (value or "").strip().lower()
    if not SHA_PATTERN.fullmatch(candidate):
        raise RegistryOperationError(category, category=category)
    return candidate


def _paths_root(paths: RegistryPaths) -> Path:
    common = os.path.commonpath(
        [
            str(paths.registry.parent),
            str(paths.signature.parent),
            str(paths.evidence_directory.parent.parent),
            str(paths.index.parent.parent),
        ]
    )
    return Path(common)


def _relative_path(paths: RegistryPaths, path: Path) -> str:
    try:
        return path.resolve().relative_to(_paths_root(paths).resolve()).as_posix()
    except ValueError:
        raise RegistryOperationError("output_path_invalid", category="output_path_invalid") from None


def lifecycle_evidence_path(paths: RegistryPaths, sequence: int) -> Path:
    return paths.evidence_directory / f"registry-sequence-{sequence:06d}.json"


def allowed_output_paths(paths: RegistryPaths, sequence: int) -> list[str]:
    return sorted(
        [
            _relative_path(paths, paths.registry),
            _relative_path(paths, paths.signature),
            _relative_path(paths, lifecycle_evidence_path(paths, sequence)),
            _relative_path(paths, paths.index),
        ]
    )


def paths_for_root(root: Path, template: RegistryPaths = DEFAULT_PATHS) -> RegistryPaths:
    source_root = _paths_root(template)

    def remap(path: Path) -> Path:
        return root / path.resolve().relative_to(source_root.resolve())

    return RegistryPaths(
        registry=remap(template.registry),
        signature=remap(template.signature),
        evidence_directory=remap(template.evidence_directory),
        index=remap(template.index),
        runtime_evidence=remap(template.runtime_evidence),
        release_evidence=remap(template.release_evidence),
    )


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = _strict_json_loads(path.read_bytes())
    except FileNotFoundError:
        raise RegistryOperationError(f"required data file is missing: {path.name}") from None
    except RegistryValidationError as exc:
        raise RegistryOperationError(f"invalid bounded JSON data: {path.name}") from exc
    if not isinstance(value, dict):
        raise RegistryOperationError(f"invalid bounded JSON data: {path.name}")
    return value


def _decode_raw_key(value: str, *, label: str) -> bytes:
    try:
        raw = base64.b64decode((value or "").strip(), validate=True)
    except (binascii.Error, ValueError):
        raise RegistryOperationError(f"protected {label} is missing or invalid") from None
    if len(raw) != 32:
        raise RegistryOperationError(f"protected {label} is missing or invalid")
    return raw


def public_material(
    environment: Mapping[str, str] | None = None,
    *,
    require_key_id: bool = True,
) -> PublicMaterial:
    env = os.environ if environment is None else environment
    public_raw = _decode_raw_key(
        env.get("UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY", ""),
        label="Ed25519 public key",
    )
    key_id = (env.get("UPSTREAM_TRUST_REGISTRY_KEY_ID", "") or "").strip()
    if require_key_id and not re_full_key_id(key_id):
        raise RegistryOperationError("protected registry key identifier is missing or invalid")
    return PublicMaterial(
        public_text=base64.b64encode(public_raw).decode("ascii"),
        key_id=key_id,
    )


def signing_material(environment: Mapping[str, str] | None = None) -> SigningMaterial:
    env = os.environ if environment is None else environment
    private_raw = _decode_raw_key(
        env.get("UPSTREAM_TRUST_REGISTRY_SIGNING_KEY", ""),
        label="Ed25519 signing key",
    )
    public = public_material(env)
    private_key = Ed25519PrivateKey.from_private_bytes(private_raw)
    if private_key.public_key().public_bytes_raw() != base64.b64decode(public.public_text):
        raise RegistryOperationError(
            "protected signing key does not match the configured verification key"
        )
    return SigningMaterial(
        private_key=private_key,
        public_text=public.public_text,
        key_id=public.key_id,
    )


def verification_public_key(environment: Mapping[str, str] | None = None) -> str:
    return public_material(environment, require_key_id=False).public_text


def _validate_reason(value: str) -> str:
    reason = value.strip()
    if len(reason) > MAX_REASON_LENGTH or any(ord(char) < 32 for char in reason):
        raise RegistryOperationError("operator reason is invalid or exceeds its bound")
    return reason


def _validate_expiry_days(value: int) -> int:
    if isinstance(value, bool) or not MIN_EXPIRY_DAYS <= value <= MAX_EXPIRY_DAYS:
        raise RegistryOperationError(
            f"expiry_days must be between {MIN_EXPIRY_DAYS} and {MAX_EXPIRY_DAYS}"
        )
    return value


def _validate_registry_shape(registry: Mapping[str, Any]) -> None:
    if set(registry) != {
        "schema_version",
        "sequence",
        "generated_at",
        "expires_at",
        "key_id",
        "entries",
    }:
        raise RegistryOperationError("registry_schema_invalid", category="registry_schema_invalid")
    if registry.get("schema_version") != 1:
        raise RegistryOperationError("registry_schema_invalid", category="registry_schema_invalid")
    sequence = registry.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise RegistryOperationError("registry_sequence_invalid", category="registry_sequence_invalid")
    if not re_full_key_id(registry.get("key_id")):
        raise RegistryOperationError("registry_key_id_invalid", category="registry_key_id_invalid")
    for name in ("generated_at", "expires_at"):
        value = registry.get(name)
        if not isinstance(value, str) or not value.endswith("Z"):
            raise RegistryOperationError("registry_timestamp_invalid", category="registry_timestamp_invalid")
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise RegistryOperationError(
                "registry_timestamp_invalid", category="registry_timestamp_invalid"
            ) from None
    entries = registry.get("entries")
    if not isinstance(entries, list):
        raise RegistryOperationError("registry_entries_invalid", category="registry_entries_invalid")
    identities: set[tuple[str, str, str]] = set()
    entry_ids: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            raise RegistryOperationError(
                "registry_entries_invalid", category="registry_entries_invalid"
            )
        ReleaseAttestation.from_mapping(item)
        identity = (
            item["server_name"],
            item["upstream_version"],
            item["contract_family"],
        )
        if identity in identities or item["entry_id"] in entry_ids:
            raise RegistryOperationError(
                "registry_entry_duplicate", category="registry_entry_duplicate"
            )
        identities.add(identity)
        entry_ids.add(item["entry_id"])


def _read_verified_current(
    paths: RegistryPaths,
    *,
    public_text: str,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    registry_raw = paths.registry.read_bytes()
    signature_raw = paths.signature.read_bytes()
    registry = _strict_json_loads(registry_raw)
    signature_value = _strict_json_loads(signature_raw)
    if not isinstance(registry, dict) or not isinstance(signature_value, dict):
        raise RegistryOperationError("existing registry files are malformed")
    if registry_raw != _canonical_file(registry) or signature_raw != _canonical_file(
        signature_value
    ):
        raise RegistryOperationError("existing registry files are not canonical JSON")
    verification_time = now
    if verification_time is None:
        generated_at = registry.get("generated_at")
        if not isinstance(generated_at, str):
            raise RegistryOperationError("existing registry timestamp is malformed")
        try:
            verification_time = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            raise RegistryOperationError("existing registry timestamp is malformed") from None
    try:
        verify_registry(
            registry_raw,
            signature_raw,
            public_key=_parse_public_key(public_text),
            now=verification_time,
            source="operator_existing",
        )
    except RegistryValidationError as exc:
        raise RegistryOperationError(
            f"existing registry verification failed: {exc.category}"
        ) from None
    _validate_registry_shape(registry)
    return registry, signature_value


def _reviewed_release_entry(
    version: str,
    *,
    paths: RegistryPaths,
    reviewed_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not STABLE_VERSION.fullmatch(version):
        raise RegistryOperationError("version must be an exact stable semantic version")
    runtime = load_json(paths.runtime_evidence)
    release = load_json(paths.release_evidence)
    if runtime.get("server_name") != "ha-mcp" or runtime.get("server_version") != version:
        raise RegistryOperationError(
            "runtime identity does not match the requested exact release"
        )
    if release.get("version") != version or release.get("source_tag") != f"v{version}":
        raise RegistryOperationError("release evidence does not match the requested exact tag")
    tool = runtime.get("required_tool")
    if not isinstance(tool, dict):
        raise RegistryOperationError("runtime required-tool descriptor is missing")
    contract = normalize_runtime_contract(
        tool,
        protocol_version=str(runtime.get("protocol_version")),
    )
    expected = runtime.get("contract_fingerprints")
    actual = {
        "input": contract.input_fingerprint,
        "security": contract.security_fingerprint,
        "output": contract.output_fingerprint,
        "runtime": contract.runtime_fingerprint,
    }
    if expected != actual:
        raise RegistryOperationError("runtime contract evidence changed after collection")
    informational = runtime.get("informational_fingerprints")
    informational_keys = {
        "raw_input_schema",
        "reviewed_security_descriptor",
        "fixture_runtime_descriptor",
        "published_runtime_descriptor",
    }
    if not isinstance(informational, dict) or set(informational) != informational_keys:
        raise RegistryOperationError("runtime informational fingerprint evidence is incomplete")
    if runtime.get("write_dispatches") != 0:
        raise RegistryOperationError("runtime review observed an upstream write dispatch")
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
        not isinstance(negative, dict)
        or set(negative.get("rejected_before_dispatch", [])) != required_rejections
        or negative.get("include_screenshot_true_rejected") is not True
        or negative.get("generic_forwarder_present") is not False
    ):
        raise RegistryOperationError("negative write-reachability evidence is incomplete")
    review_evidence = {
        "schema_version": 1,
        "version": version,
        "release": release,
        "runtime": runtime,
        "compiled_contract_family": CONTRACT_FAMILY,
        "reviewed_at": reviewed_at,
    }
    review_evidence_digest = sha256_digest(_canonical_file(review_evidence))
    image_digest = release.get("image_index_digest")
    entry = {
        "entry_id": f"ha-mcp-v{version}-{str(image_digest).split(':')[-1][:8]}",
        "server_name": "ha-mcp",
        "upstream_version": version,
        "source_tag": f"v{version}",
        "source_commit": release.get("source_commit"),
        "image_index_digest": image_digest,
        "platform_digests": release.get("platform_digests"),
        "image_revision": release.get("image_revision"),
        "contract_family": CONTRACT_FAMILY,
        "input_contract_fingerprint": contract.input_fingerprint,
        "security_contract_fingerprint": contract.security_fingerprint,
        "output_contract_fingerprint": contract.output_fingerprint,
        "runtime_contract_fingerprint": contract.runtime_fingerprint,
        "catalog_fingerprint": runtime.get("catalog_fingerprint"),
        "raw_input_schema_fingerprint": informational["raw_input_schema"],
        "reviewed_security_descriptor_fingerprint": informational[
            "reviewed_security_descriptor"
        ],
        "fixture_runtime_descriptor_fingerprint": informational[
            "fixture_runtime_descriptor"
        ],
        "published_runtime_descriptor_fingerprint": informational[
            "published_runtime_descriptor"
        ],
        "review_evidence_digest": review_evidence_digest,
        "reviewed_at": reviewed_at,
        "revoked": False,
    }
    ReleaseAttestation.from_mapping(entry)
    return entry, review_evidence


def render_index(registry: Mapping[str, Any], *, workflow_base_sha: str | None = None) -> bytes:
    rows = "".join(
        f"| {item['upstream_version']} | `{item['entry_id']}` | "
        f"`{item['contract_family']}` | {str(item['revoked']).lower()} |\n"
        for item in registry["entries"]
    )
    base = f"Workflow base: `{workflow_base_sha}`  \n" if workflow_base_sha else ""
    value = (
        "# Upstream trust registry index\n\n"
        f"Sequence: `{registry['sequence']}`  \n"
        f"Generated: `{registry['generated_at']}`  \n"
        f"Expires: `{registry['expires_at']}`  \n"
        f"{base}\n"
        "| Version | Entry | Family | Revoked |\n"
        "|---|---|---|---|\n"
        f"{rows}"
    )
    return value.encode("utf-8")


def _find_entry(entries: list[dict[str, Any]], version: str) -> dict[str, Any]:
    matches = [
        entry
        for entry in entries
        if entry.get("server_name") == "ha-mcp"
        and entry.get("upstream_version") == version
        and entry.get("contract_family") == CONTRACT_FAMILY
    ]
    if len(matches) != 1:
        raise RegistryOperationError("the exact registry entry selector did not match once")
    return matches[0]


def _last_lifecycle_digest(paths: RegistryPaths, sequence: int) -> str | None:
    if sequence == 0:
        return GENESIS_DIGEST
    path = lifecycle_evidence_path(paths, sequence)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise RegistryOperationError(
            "lifecycle_evidence_chain_incomplete",
            category="lifecycle_evidence_chain_incomplete",
        ) from None
    value = _strict_json_loads(raw)
    if raw != _canonical_file(value):
        raise RegistryOperationError(
            "lifecycle_evidence_not_canonical",
            category="lifecycle_evidence_not_canonical",
        )
    return sha256_digest(canonical_json(value))


def _inspection_digests(
    operation: str,
    *,
    paths: RegistryPaths,
    current: Mapping[str, Any] | None,
) -> dict[str, str]:
    if operation in {"bootstrap", "add"}:
        return {
            "release_evidence": sha256_digest(canonical_json(load_json(paths.release_evidence))),
            "runtime_evidence": sha256_digest(canonical_json(load_json(paths.runtime_evidence))),
        }
    if current is None:
        raise RegistryOperationError("inspection_current_registry_missing")
    return {
        "current_registry": sha256_digest(canonical_json(current)),
        "current_registry_signature": sha256_digest(paths.signature.read_bytes()),
    }


def _affected_identity(affected: Mapping[str, Any] | None) -> dict[str, str] | None:
    if affected is None:
        return None
    return {
        "entry_id": affected["entry_id"],
        "server_name": affected["server_name"],
        "upstream_version": affected["upstream_version"],
        "contract_family": affected["contract_family"],
    }


def prepare_mutation_candidate(
    *,
    operation: str,
    upstream_version: str | None,
    expected_current_sequence: int,
    expiry_days: int = DEFAULT_EXPIRY_DAYS,
    operator_reason: str = "",
    paths: RegistryPaths = DEFAULT_PATHS,
    environment: Mapping[str, str] | None = None,
    now: datetime | None = None,
    workflow_base_sha: str | None = None,
    dispatch_sha: str | None = None,
) -> MutationCandidate:
    if operation not in MUTATING_OPERATIONS:
        raise RegistryOperationError("unsupported mutating registry operation")
    if isinstance(expected_current_sequence, bool) or expected_current_sequence < 0:
        raise RegistryOperationError("expected_current_sequence must be a nonnegative integer")
    expiry_days = _validate_expiry_days(expiry_days)
    reason = _validate_reason(operator_reason)
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0
    )
    public = public_material(environment)
    generated_at = utc_text(clock)
    base_sha = _bounded_sha(workflow_base_sha or _local_head_sha())
    dispatch = _bounded_sha(dispatch_sha or base_sha)

    registry_exists = paths.registry.exists()
    signature_exists = paths.signature.exists()
    if operation == "bootstrap":
        if registry_exists or signature_exists:
            raise RegistryOperationError("bootstrap requires no existing registry or signature")
        if expected_current_sequence != 0:
            raise RegistryOperationError("bootstrap expected_current_sequence must be zero")
        current: dict[str, Any] | None = None
        entries: list[dict[str, Any]] = []
        old_sequence = 0
    else:
        if not registry_exists or not signature_exists:
            raise RegistryOperationError("the committed registry and signature are required")
        current, _signature = _read_verified_current(paths, public_text=public.public_text)
        if current.get("key_id") != public.key_id:
            raise RegistryOperationError("configured key identifier does not match the registry")
        old_sequence = current["sequence"]
        entries = copy.deepcopy(current["entries"])
    if old_sequence != expected_current_sequence:
        raise RegistryOperationError("stale expected_current_sequence")

    affected: dict[str, Any] | None = None
    old_revoked: bool | None = None
    release_evidence: dict[str, Any] | None = None
    if operation in {"bootstrap", "add"}:
        if not upstream_version:
            raise RegistryOperationError(f"upstream_version is required for {operation}")
        affected, release_evidence = _reviewed_release_entry(
            upstream_version,
            paths=paths,
            reviewed_at=generated_at,
        )
        identity = (
            affected["server_name"],
            affected["upstream_version"],
            affected["contract_family"],
        )
        if any(item.get("entry_id") == affected["entry_id"] for item in entries):
            raise RegistryOperationError("duplicate registry entry_id")
        if any(
            (item.get("server_name"), item.get("upstream_version"), item.get("contract_family"))
            == identity
            for item in entries
        ):
            raise RegistryOperationError("duplicate server/version/contract-family identity")
        entries.append(affected)
    elif operation in {"revoke", "restore"}:
        if not upstream_version or not STABLE_VERSION.fullmatch(upstream_version):
            raise RegistryOperationError(
                f"an exact stable upstream_version is required for {operation}"
            )
        affected = _find_entry(entries, upstream_version)
        old_revoked = affected.get("revoked")
        if not isinstance(old_revoked, bool):
            raise RegistryOperationError("existing registry revocation state is malformed")
        if operation == "revoke" and old_revoked:
            raise RegistryOperationError("the selected entry is already revoked")
        if operation == "restore" and not old_revoked:
            raise RegistryOperationError("the selected entry is not revoked")
        affected["revoked"] = operation == "revoke"
    elif operation == "renew" and upstream_version:
        raise RegistryOperationError("upstream_version is not accepted for renew")

    updated = {
        "schema_version": 1,
        "sequence": old_sequence + 1,
        "generated_at": generated_at,
        "expires_at": utc_text(clock + timedelta(days=expiry_days)),
        "key_id": public.key_id,
        "entries": entries,
    }
    _validate_registry_shape(updated)
    evidence_path = lifecycle_evidence_path(paths, updated["sequence"])
    output_paths = allowed_output_paths(paths, updated["sequence"])
    inspection_digests = _inspection_digests(
        operation,
        paths=paths,
        current=current,
    )
    release_digest = (
        None if release_evidence is None else sha256_digest(_canonical_file(release_evidence))
    )
    lifecycle_payload = {
        "schema_version": 1,
        "evidence_type": LIFECYCLE_EVIDENCE_TYPE,
        "operation": operation,
        "old_sequence": old_sequence,
        "new_sequence": updated["sequence"],
        "affected_entry": _affected_identity(affected),
        "old_revoked": old_revoked,
        "new_revoked": None if affected is None else affected["revoked"],
        "operator_reason": reason or None,
        "generated_at": updated["generated_at"],
        "expires_at": updated["expires_at"],
        "key_id": updated["key_id"],
        "workflow_base_sha": base_sha,
        "dispatch_sha": dispatch,
        "prior_registry_digest": (
            GENESIS_DIGEST if current is None else sha256_digest(canonical_json(current))
        ),
        "current_registry_digest": sha256_digest(canonical_json(updated)),
        "prior_lifecycle_evidence_digest": _last_lifecycle_digest(paths, old_sequence),
        "data_only": True,
        "allowed_output_paths": output_paths,
        "inspection_evidence_digests": inspection_digests,
        "release_evidence_digest": release_digest,
        "release_evidence": release_evidence,
        "previous_registry": current,
        "current_registry": updated,
    }
    manifest = {
        "schema_version": 1,
        "operation": operation,
        "dispatch_sha": dispatch,
        "workflow_base_sha": base_sha,
        "intended_base_branch": "main",
        "expected_current_sequence": expected_current_sequence,
        "old_sequence": old_sequence,
        "new_sequence": updated["sequence"],
        "candidate_registry_digest": lifecycle_payload["current_registry_digest"],
        "affected_entry": lifecycle_payload["affected_entry"],
        "inspection_evidence_digests": inspection_digests,
        "expiry_days": expiry_days,
        "generated_at": updated["generated_at"],
        "expires_at": updated["expires_at"],
        "key_id": updated["key_id"],
        "contract_family": CONTRACT_FAMILY,
        "data_only": True,
        "allowed_output_paths": output_paths,
        "lifecycle_payload_digest": sha256_digest(canonical_json(lifecycle_payload)),
    }
    return MutationCandidate(
        registry=updated,
        lifecycle_payload=lifecycle_payload,
        evidence_path=evidence_path,
        old_sequence=old_sequence,
        affected=affected,
        old_revoked=old_revoked,
        release_evidence=release_evidence,
        inspection_manifest=manifest,
    )


def _sign_document(payload: Mapping[str, Any], material: SigningMaterial) -> dict[str, Any]:
    return {
        "payload": dict(payload),
        "signature": {
            "schema_version": 1,
            "algorithm": "Ed25519",
            "key_id": material.key_id,
            "signature": base64.b64encode(
                material.private_key.sign(canonical_json(payload))
            ).decode("ascii"),
        },
    }


def _sign_registry(
    registry: Mapping[str, Any],
    *,
    material: SigningMaterial,
    now: datetime,
) -> tuple[bytes, bytes]:
    registry_bytes = canonical_json(registry)
    signature = {
        "schema_version": 1,
        "algorithm": "Ed25519",
        "key_id": material.key_id,
        "signature": base64.b64encode(material.private_key.sign(registry_bytes)).decode(
            "ascii"
        ),
    }
    signature_bytes = canonical_json(signature)
    verify_registry(
        registry_bytes,
        signature_bytes,
        public_key=_parse_public_key(material.public_text),
        now=now,
        source="operator_proposed",
    )
    return registry_bytes + b"\n", signature_bytes + b"\n"


def signed_candidate_outputs(
    candidate: MutationCandidate,
    *,
    material: SigningMaterial,
    paths: RegistryPaths,
    now: datetime,
) -> tuple[dict[Path, bytes], dict[str, Any]]:
    if candidate.registry["key_id"] != material.key_id:
        raise RegistryOperationError("candidate key identifier mismatch")
    if candidate.inspection_manifest["candidate_registry_digest"] != sha256_digest(
        canonical_json(candidate.registry)
    ):
        raise RegistryOperationError("candidate_registry_digest_mismatch")
    registry_bytes, signature_bytes = _sign_registry(
        candidate.registry,
        material=material,
        now=now,
    )
    lifecycle_document = _sign_document(candidate.lifecycle_payload, material)
    evidence_bytes = _canonical_file(lifecycle_document)
    outputs = {
        paths.registry: registry_bytes,
        paths.signature: signature_bytes,
        lifecycle_evidence_path(paths, candidate.registry["sequence"]): evidence_bytes,
        paths.index: render_index(
            candidate.registry,
            workflow_base_sha=candidate.lifecycle_payload["workflow_base_sha"],
        ),
    }
    summary = _summary(
        candidate=candidate,
        evidence_bytes=evidence_bytes,
        dry_run=False,
    )
    return outputs, summary


def _summary(
    *,
    candidate: MutationCandidate,
    evidence_bytes: bytes,
    dry_run: bool,
) -> dict[str, Any]:
    affected = candidate.affected
    registry = candidate.registry
    return {
        "schema_version": 1,
        "operation": candidate.lifecycle_payload["operation"],
        "dry_run": dry_run,
        "old_sequence": candidate.old_sequence,
        "new_sequence": registry["sequence"],
        "expected_current_sequence": candidate.old_sequence,
        "workflow_base_sha": candidate.lifecycle_payload["workflow_base_sha"],
        "dispatch_sha": candidate.lifecycle_payload["dispatch_sha"],
        "affected_entry_id": None if affected is None else affected["entry_id"],
        "server_name": None if affected is None else affected["server_name"],
        "upstream_version": None if affected is None else affected["upstream_version"],
        "contract_family": CONTRACT_FAMILY if affected is None else affected["contract_family"],
        "source_commit": None if affected is None else affected["source_commit"],
        "image_index_digest": None if affected is None else affected["image_index_digest"],
        "old_revoked": candidate.old_revoked,
        "new_revoked": None if affected is None else affected["revoked"],
        "generated_at": registry["generated_at"],
        "expires_at": registry["expires_at"],
        "key_id": registry["key_id"],
        "registry_digest": sha256_digest(canonical_json(registry)),
        "prior_registry_digest": candidate.lifecycle_payload["prior_registry_digest"],
        "prior_lifecycle_evidence_digest": candidate.lifecycle_payload[
            "prior_lifecycle_evidence_digest"
        ],
        "evidence_digest": sha256_digest(canonical_json(_strict_json_loads(evidence_bytes))),
        "evidence_path": _relative_path(paths=DEFAULT_PATHS, path=candidate.evidence_path)
        if candidate.evidence_path.is_relative_to(ROOT)
        else candidate.evidence_path.name,
        "allowed_output_paths": candidate.lifecycle_payload["allowed_output_paths"],
        "data_only": True,
        "engineering_image_change_required": False,
        "written": not dry_run,
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
EVIDENCE_SIGNATURE_FIELDS = {"schema_version", "algorithm", "key_id", "signature"}
IDENTITY_FIELDS = {"entry_id", "server_name", "upstream_version", "contract_family"}


def _verify_evidence_signature(
    document: Mapping[str, Any],
    *,
    public_key: Ed25519PublicKey,
    expected_key_id: str,
) -> Mapping[str, Any]:
    if set(document) != {"payload", "signature"}:
        raise RegistryOperationError(
            "lifecycle_evidence_schema_invalid",
            category="lifecycle_evidence_schema_invalid",
        )
    payload = document.get("payload")
    signature = document.get("signature")
    if not isinstance(payload, dict) or set(payload) != LIFECYCLE_PAYLOAD_FIELDS:
        raise RegistryOperationError(
            "lifecycle_evidence_schema_invalid",
            category="lifecycle_evidence_schema_invalid",
        )
    if not isinstance(signature, dict) or set(signature) != EVIDENCE_SIGNATURE_FIELDS:
        raise RegistryOperationError(
            "lifecycle_evidence_signature_invalid",
            category="lifecycle_evidence_signature_invalid",
        )
    if (
        signature.get("schema_version") != 1
        or signature.get("algorithm") != "Ed25519"
        or signature.get("key_id") != expected_key_id
    ):
        raise RegistryOperationError(
            "lifecycle_evidence_signature_invalid",
            category="lifecycle_evidence_signature_invalid",
        )
    try:
        signature_raw = base64.b64decode(str(signature.get("signature", "")), validate=True)
        public_key.verify(signature_raw, canonical_json(payload))
    except (binascii.Error, InvalidSignature, ValueError):
        raise RegistryOperationError(
            "lifecycle_evidence_signature_invalid",
            category="lifecycle_evidence_signature_invalid",
        ) from None
    return payload


def _entries_by_id(registry: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["entry_id"]: entry for entry in registry["entries"]}


def _verify_transition(
    payload: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> None:
    operation = payload["operation"]
    affected = payload["affected_entry"]
    if affected is not None and (
        not isinstance(affected, dict) or set(affected) != IDENTITY_FIELDS
    ):
        raise RegistryOperationError(
            "lifecycle_evidence_identity_invalid",
            category="lifecycle_evidence_identity_invalid",
        )
    previous_entries = {} if previous is None else _entries_by_id(previous)
    current_entries = _entries_by_id(current)
    if operation == "bootstrap":
        if (
            previous is not None
            or payload["old_sequence"] != 0
            or payload["new_sequence"] != 1
            or len(current_entries) != 1
            or affected is None
            or affected["entry_id"] not in current_entries
            or payload["old_revoked"] is not None
            or payload["new_revoked"] is not False
        ):
            raise RegistryOperationError(
                "lifecycle_bootstrap_semantics_invalid",
                category="lifecycle_bootstrap_semantics_invalid",
            )
    elif operation == "add":
        if affected is None or affected["entry_id"] in previous_entries:
            raise RegistryOperationError(
                "lifecycle_add_semantics_invalid", category="lifecycle_add_semantics_invalid"
            )
        expected = dict(previous_entries)
        expected[affected["entry_id"]] = current_entries.get(affected["entry_id"])
        if (
            expected != current_entries
            or payload["old_revoked"] is not None
            or payload["new_revoked"] is not False
        ):
            raise RegistryOperationError(
                "lifecycle_add_semantics_invalid", category="lifecycle_add_semantics_invalid"
            )
    elif operation in {"revoke", "restore"}:
        if affected is None or affected["entry_id"] not in previous_entries:
            raise RegistryOperationError(
                "lifecycle_revocation_semantics_invalid",
                category="lifecycle_revocation_semantics_invalid",
            )
        expected = copy.deepcopy(previous_entries)
        expected[affected["entry_id"]]["revoked"] = operation == "revoke"
        required_old = operation == "restore"
        if (
            expected != current_entries
            or payload["old_revoked"] is not required_old
            or payload["new_revoked"] is not (operation == "revoke")
        ):
            raise RegistryOperationError(
                "lifecycle_revocation_semantics_invalid",
                category="lifecycle_revocation_semantics_invalid",
            )
    elif operation == "renew":
        if (
            affected is not None
            or previous_entries != current_entries
            or payload["old_revoked"] is not None
            or payload["new_revoked"] is not None
        ):
            raise RegistryOperationError(
                "lifecycle_renew_semantics_invalid",
                category="lifecycle_renew_semantics_invalid",
            )
    else:
        raise RegistryOperationError(
            "lifecycle_operation_invalid", category="lifecycle_operation_invalid"
        )


def verify_lifecycle_chain(
    *,
    paths: RegistryPaths,
    registry: Mapping[str, Any],
    public_text: str,
    expected_key_id: str,
) -> dict[str, Any]:
    sequence = registry["sequence"]
    paths.evidence_directory.mkdir(parents=True, exist_ok=True)
    discovered: dict[int, Path] = {}
    for path in paths.evidence_directory.glob("registry-sequence-*.json"):
        match = EVIDENCE_FILE_PATTERN.fullmatch(path.name)
        if match is None:
            continue
        number = int(match.group(1))
        if number in discovered:
            raise RegistryOperationError(
                "lifecycle_evidence_sequence_duplicate",
                category="lifecycle_evidence_sequence_duplicate",
            )
        discovered[number] = path
    expected_numbers = list(range(1, sequence + 1))
    if sorted(discovered) != expected_numbers:
        category = (
            "lifecycle_evidence_future_record"
            if any(number > sequence for number in discovered)
            else "lifecycle_evidence_chain_incomplete"
        )
        raise RegistryOperationError(category, category=category)
    public_key = _parse_public_key(public_text)
    previous_registry: Mapping[str, Any] | None = None
    previous_document_digest: str | None = None
    introduced_release_digests: dict[str, str] = {}
    last_base_sha: str | None = None
    last_dispatch_sha: str | None = None
    last_operation: str | None = None
    for number in expected_numbers:
        raw = discovered[number].read_bytes()
        document = _strict_json_loads(raw)
        if not isinstance(document, dict) or raw != _canonical_file(document):
            raise RegistryOperationError(
                "lifecycle_evidence_not_canonical",
                category="lifecycle_evidence_not_canonical",
            )
        payload = _verify_evidence_signature(
            document,
            public_key=public_key,
            expected_key_id=expected_key_id,
        )
        if (
            payload["schema_version"] != 1
            or payload["evidence_type"] != LIFECYCLE_EVIDENCE_TYPE
            or payload["old_sequence"] != number - 1
            or payload["new_sequence"] != number
            or payload["key_id"] != expected_key_id
            or payload["data_only"] is not True
            or not SHA_PATTERN.fullmatch(str(payload["workflow_base_sha"]))
            or not SHA_PATTERN.fullmatch(str(payload["dispatch_sha"]))
        ):
            raise RegistryOperationError(
                "lifecycle_evidence_semantics_invalid",
                category="lifecycle_evidence_semantics_invalid",
            )
        current_registry = payload["current_registry"]
        embedded_previous = payload["previous_registry"]
        if not isinstance(current_registry, dict):
            raise RegistryOperationError(
                "lifecycle_registry_snapshot_invalid",
                category="lifecycle_registry_snapshot_invalid",
            )
        _validate_registry_shape(current_registry)
        if embedded_previous != previous_registry:
            raise RegistryOperationError(
                "lifecycle_prior_registry_mismatch",
                category="lifecycle_prior_registry_mismatch",
            )
        if current_registry["sequence"] != number:
            raise RegistryOperationError(
                "lifecycle_registry_sequence_mismatch",
                category="lifecycle_registry_sequence_mismatch",
            )
        prior_registry_digest = (
            None if previous_registry is None else sha256_digest(canonical_json(previous_registry))
        )
        if (
            payload["prior_registry_digest"] != prior_registry_digest
            or payload["current_registry_digest"]
            != sha256_digest(canonical_json(current_registry))
            or payload["prior_lifecycle_evidence_digest"] != previous_document_digest
            or payload["generated_at"] != current_registry["generated_at"]
            or payload["expires_at"] != current_registry["expires_at"]
            or payload["allowed_output_paths"] != allowed_output_paths(paths, number)
        ):
            raise RegistryOperationError(
                "lifecycle_evidence_chain_mismatch",
                category="lifecycle_evidence_chain_mismatch",
            )
        inspection = payload["inspection_evidence_digests"]
        if (
            not isinstance(inspection, dict)
            or not inspection
            or any(not isinstance(key, str) or not DIGEST_PATTERN.fullmatch(str(value)) for key, value in inspection.items())
        ):
            raise RegistryOperationError(
                "lifecycle_inspection_evidence_invalid",
                category="lifecycle_inspection_evidence_invalid",
            )
        release_evidence = payload["release_evidence"]
        release_digest = payload["release_evidence_digest"]
        if payload["operation"] in {"bootstrap", "add"}:
            if not isinstance(release_evidence, dict) or not DIGEST_PATTERN.fullmatch(
                str(release_digest)
            ):
                raise RegistryOperationError(
                    "lifecycle_release_evidence_invalid",
                    category="lifecycle_release_evidence_invalid",
                )
            if sha256_digest(_canonical_file(release_evidence)) != release_digest:
                raise RegistryOperationError(
                    "lifecycle_release_evidence_invalid",
                    category="lifecycle_release_evidence_invalid",
                )
            affected = payload["affected_entry"]
            if affected is None:
                raise RegistryOperationError(
                    "lifecycle_release_evidence_invalid",
                    category="lifecycle_release_evidence_invalid",
                )
            introduced_release_digests[affected["entry_id"]] = release_digest
        elif release_evidence is not None or release_digest is not None:
            raise RegistryOperationError(
                "lifecycle_release_evidence_invalid",
                category="lifecycle_release_evidence_invalid",
            )
        _verify_transition(payload, previous_registry, current_registry)
        previous_registry = current_registry
        previous_document_digest = sha256_digest(canonical_json(document))
        last_base_sha = payload["workflow_base_sha"]
        last_dispatch_sha = payload["dispatch_sha"]
        last_operation = payload["operation"]
    if previous_registry != registry:
        raise RegistryOperationError(
            "lifecycle_current_registry_mismatch",
            category="lifecycle_current_registry_mismatch",
        )
    for entry in registry["entries"]:
        if introduced_release_digests.get(entry["entry_id"]) != entry["review_evidence_digest"]:
            raise RegistryOperationError(
                "review_evidence_digest_mismatch",
                category="review_evidence_digest_mismatch",
            )
    return {
        "sequence": sequence,
        "record_count": len(expected_numbers),
        "last_evidence_digest": previous_document_digest,
        "workflow_base_sha": last_base_sha,
        "dispatch_sha": last_dispatch_sha,
        "operation": last_operation,
    }


def verify_committed_registry(
    *,
    paths: RegistryPaths = DEFAULT_PATHS,
    environment: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    public_text = verification_public_key(environment)
    registry_raw = paths.registry.read_bytes()
    signature_raw = paths.signature.read_bytes()
    registry_value = _strict_json_loads(registry_raw)
    signature_value = _strict_json_loads(signature_raw)
    if (
        not isinstance(registry_value, dict)
        or registry_raw != _canonical_file(registry_value)
        or signature_raw != _canonical_file(signature_value)
    ):
        raise RegistryOperationError("registry files are not canonical JSON")
    verified = verify_registry(
        registry_raw,
        signature_raw,
        public_key=_parse_public_key(public_text),
        now=clock,
        source="operator_verify",
    )
    registry = registry_value
    _validate_registry_shape(registry)
    chain = verify_lifecycle_chain(
        paths=paths,
        registry=registry,
        public_text=public_text,
        expected_key_id=registry["key_id"],
    )
    if paths.index.read_bytes() != render_index(
        registry, workflow_base_sha=chain["workflow_base_sha"]
    ):
        raise RegistryOperationError("generated registry index is out of date")
    return {
        "schema_version": 1,
        "operation": "verify",
        "sequence": verified.sequence,
        "entry_count": len(verified.entries),
        "key_id": verified.key_id,
        "registry_digest": sha256_digest(canonical_json(registry)),
        "signature_valid": True,
        "generated_evidence_valid": True,
        "lifecycle_evidence_chain_valid": True,
        "lifecycle_evidence_record_count": chain["record_count"],
        "last_lifecycle_evidence_digest": chain["last_evidence_digest"],
        "workflow_base_sha": chain["workflow_base_sha"],
        "dispatch_sha": chain["dispatch_sha"],
        "last_operation": chain["operation"],
        "written": False,
    }


def _copy_current_set(paths: RegistryPaths, stage_root: Path) -> RegistryPaths:
    staged = paths_for_root(stage_root, paths)
    for source, target in (
        (paths.registry, staged.registry),
        (paths.signature, staged.signature),
        (paths.index, staged.index),
    ):
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    if paths.evidence_directory.exists():
        shutil.copytree(paths.evidence_directory, staged.evidence_directory, dirs_exist_ok=True)
    return staged


def _capture_originals(outputs: Mapping[Path, bytes]) -> dict[Path, OriginalFile]:
    originals: dict[Path, OriginalFile] = {}
    for target in outputs:
        if target.exists():
            details = target.stat()
            originals[target] = OriginalFile(
                existed=True,
                content=target.read_bytes(),
                mode=stat.S_IMODE(details.st_mode),
            )
        else:
            originals[target] = OriginalFile(existed=False, content=None, mode=None)
    return originals


def _atomic_replace_bytes(target: Path, content: bytes, replace_func: ReplaceFunction) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=f".{target.name}.",
        dir=target.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        replace_func(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_originals(
    originals: Mapping[Path, OriginalFile],
    *,
    replace_func: ReplaceFunction,
) -> None:
    for target, original in originals.items():
        if original.existed:
            assert original.content is not None
            _atomic_replace_bytes(target, original.content, replace_func)
            if original.mode is not None:
                target.chmod(original.mode)
        else:
            target.unlink(missing_ok=True)


def _originals_match(originals: Mapping[Path, OriginalFile]) -> bool:
    for target, original in originals.items():
        if target.exists() != original.existed:
            return False
        if original.existed and target.read_bytes() != original.content:
            return False
    return True


def replace_verified_output_set(
    outputs: Mapping[Path, bytes],
    *,
    paths: RegistryPaths,
    environment: Mapping[str, str],
    now: datetime,
    replace_func: ReplaceFunction = os.replace,
    rollback_replace_func: ReplaceFunction = os.replace,
    verify_func: VerifyFunction = verify_committed_registry,
) -> None:
    root = _paths_root(paths)
    root.mkdir(parents=True, exist_ok=True)
    originals = _capture_originals(outputs)
    try:
        with tempfile.TemporaryDirectory(prefix=".registry-set-", dir=root) as directory:
            staged_paths = _copy_current_set(paths, Path(directory))
            for target, content in outputs.items():
                relative = target.resolve().relative_to(root.resolve())
                staged_target = Path(directory) / relative
                staged_target.parent.mkdir(parents=True, exist_ok=True)
                staged_target.write_bytes(content)
            try:
                verify_func(paths=staged_paths, environment=environment, now=now)
            except Exception:
                raise RegistrySetWriteError("staging_failed") from None
    except RegistrySetWriteError:
        raise
    except Exception:
        raise RegistrySetWriteError("staging_failed") from None

    ordered = [
        paths.registry,
        paths.signature,
        lifecycle_evidence_path(
            paths, json.loads(outputs[paths.registry].decode("utf-8"))["sequence"]
        ),
        paths.index,
    ]
    if set(ordered) != set(outputs):
        raise RegistrySetWriteError("staging_failed")
    failure_category = "replacement_failed"
    try:
        for target in ordered:
            _atomic_replace_bytes(target, outputs[target], replace_func)
        failure_category = "post_write_verification_failed"
        verify_func(paths=paths, environment=environment, now=now)
        return
    except Exception:
        try:
            _restore_originals(originals, replace_func=rollback_replace_func)
        except Exception:
            raise RegistrySetWriteError("rollback_failed") from None
        if not _originals_match(originals):
            raise RegistrySetWriteError("restored_state_verification_failed") from None
        if originals[paths.registry].existed:
            try:
                verify_func(paths=paths, environment=environment, now=now)
            except Exception:
                raise RegistrySetWriteError("restored_state_verification_failed") from None
        raise RegistrySetWriteError(failure_category, rollback_status="rollback_succeeded") from None


def _atomic_write_many(outputs: Mapping[Path, bytes]) -> None:
    """Deprecated compatibility helper: per-file atomic only, never set-atomic."""
    for target, content in outputs.items():
        _atomic_replace_bytes(target, content, os.replace)


def mutate_registry(
    *,
    operation: str,
    upstream_version: str | None,
    expected_current_sequence: int,
    expiry_days: int = DEFAULT_EXPIRY_DAYS,
    operator_reason: str = "",
    dry_run: bool = False,
    paths: RegistryPaths = DEFAULT_PATHS,
    environment: Mapping[str, str] | None = None,
    now: datetime | None = None,
    workflow_base_sha: str | None = None,
    dispatch_sha: str | None = None,
    replace_func: ReplaceFunction = os.replace,
    rollback_replace_func: ReplaceFunction = os.replace,
    verify_func: VerifyFunction = verify_committed_registry,
) -> dict[str, Any]:
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0
    )
    material = signing_material(environment)
    candidate = prepare_mutation_candidate(
        operation=operation,
        upstream_version=upstream_version,
        expected_current_sequence=expected_current_sequence,
        expiry_days=expiry_days,
        operator_reason=operator_reason,
        paths=paths,
        environment=environment,
        now=clock,
        workflow_base_sha=workflow_base_sha,
        dispatch_sha=dispatch_sha,
    )
    outputs, summary = signed_candidate_outputs(
        candidate,
        material=material,
        paths=paths,
        now=clock,
    )
    if dry_run:
        return {**summary, "dry_run": True, "written": False}
    replace_verified_output_set(
        outputs,
        paths=paths,
        environment=dict(os.environ if environment is None else environment),
        now=clock,
        replace_func=replace_func,
        rollback_replace_func=rollback_replace_func,
        verify_func=verify_func,
    )
    return summary


def write_unsigned_candidate(candidate: MutationCandidate, output_directory: Path) -> dict[str, Any]:
    output_directory.mkdir(parents=True, exist_ok=True)
    value = {
        "schema_version": 1,
        "registry": candidate.registry,
        "lifecycle_payload": candidate.lifecycle_payload,
        "inspection_manifest": candidate.inspection_manifest,
    }
    value["candidate_digest"] = sha256_digest(canonical_json(value))
    candidate_path = output_directory / "unsigned-candidate.json"
    manifest_path = output_directory / "inspection-manifest.json"
    candidate_path.write_bytes(_canonical_file(value))
    manifest_path.write_bytes(_canonical_file(candidate.inspection_manifest))
    return {
        **candidate.inspection_manifest,
        "candidate_digest": value["candidate_digest"],
        "candidate_path": candidate_path.name,
        "inspection_manifest_path": manifest_path.name,
    }


def read_unsigned_candidate(
    directory: Path,
    *,
    paths: RegistryPaths,
) -> MutationCandidate:
    raw = (directory / "unsigned-candidate.json").read_bytes()
    value = _strict_json_loads(raw)
    if not isinstance(value, dict) or raw != _canonical_file(value):
        raise RegistryOperationError("unsigned_candidate_invalid")
    digest = value.pop("candidate_digest", None)
    if digest != sha256_digest(canonical_json(value)):
        raise RegistryOperationError("unsigned_candidate_digest_mismatch")
    registry = value.get("registry")
    payload = value.get("lifecycle_payload")
    manifest = value.get("inspection_manifest")
    if not isinstance(registry, dict) or not isinstance(payload, dict) or not isinstance(manifest, dict):
        raise RegistryOperationError("unsigned_candidate_invalid")
    if manifest.get("candidate_registry_digest") != sha256_digest(canonical_json(registry)):
        raise RegistryOperationError("candidate_registry_digest_mismatch")
    if manifest.get("lifecycle_payload_digest") != sha256_digest(canonical_json(payload)):
        raise RegistryOperationError("candidate_lifecycle_digest_mismatch")
    affected_identity = payload.get("affected_entry")
    affected = None
    if affected_identity is not None:
        affected = next(
            (
                item
                for item in registry.get("entries", [])
                if item.get("entry_id") == affected_identity.get("entry_id")
            ),
            None,
        )
    return MutationCandidate(
        registry=registry,
        lifecycle_payload=payload,
        evidence_path=lifecycle_evidence_path(paths, registry["sequence"]),
        old_sequence=payload["old_sequence"],
        affected=affected,
        old_revoked=payload["old_revoked"],
        release_evidence=payload["release_evidence"],
        inspection_manifest=manifest,
    )


def sign_candidate_directory(
    candidate_directory: Path,
    output_directory: Path,
    *,
    paths: RegistryPaths = DEFAULT_PATHS,
    environment: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    candidate = read_unsigned_candidate(candidate_directory, paths=paths)
    material = signing_material(environment)
    current_digest = None
    if candidate.old_sequence:
        current, _ = _read_verified_current(paths, public_text=material.public_text)
        current_digest = sha256_digest(canonical_json(current))
    if candidate.lifecycle_payload["prior_registry_digest"] != current_digest:
        raise RegistryOperationError("workflow_sequence_stale", category="workflow_sequence_stale")
    outputs, summary = signed_candidate_outputs(
        candidate,
        material=material,
        paths=paths,
        now=clock,
    )
    tree = output_directory / "tree"
    if tree.exists():
        shutil.rmtree(tree)
    staged_paths = _copy_current_set(paths, tree)
    root = _paths_root(paths)
    for target, content in outputs.items():
        destination = tree / target.resolve().relative_to(root.resolve())
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    verify_committed_registry(paths=staged_paths, environment=environment, now=clock)
    publication_manifest = {
        "schema_version": 1,
        "operation": candidate.lifecycle_payload["operation"],
        "workflow_base_sha": candidate.lifecycle_payload["workflow_base_sha"],
        "dispatch_sha": candidate.lifecycle_payload["dispatch_sha"],
        "expected_current_sequence": candidate.old_sequence,
        "new_sequence": candidate.registry["sequence"],
        "registry_digest": sha256_digest(canonical_json(candidate.registry)),
        "lifecycle_evidence_digest": summary["evidence_digest"],
        "changed_paths": candidate.lifecycle_payload["allowed_output_paths"],
        "data_only": True,
        "verified": True,
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "operation-summary.json").write_bytes(_canonical_file(summary))
    (output_directory / "publication-manifest.json").write_bytes(
        _canonical_file(publication_manifest)
    )
    return publication_manifest


def verify_signed_artifact_directory(
    directory: Path,
    *,
    environment: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    manifest_raw = (directory / "publication-manifest.json").read_bytes()
    manifest = _strict_json_loads(manifest_raw)
    manifest_fields = {
        "schema_version",
        "operation",
        "workflow_base_sha",
        "dispatch_sha",
        "expected_current_sequence",
        "new_sequence",
        "registry_digest",
        "lifecycle_evidence_digest",
        "changed_paths",
        "data_only",
        "verified",
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != manifest_fields
        or manifest_raw != _canonical_file(manifest)
    ):
        raise RegistryOperationError("publication_manifest_invalid")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("operation") not in MUTATING_OPERATIONS
        or manifest.get("data_only") is not True
        or manifest.get("verified") is not True
    ):
        raise RegistryOperationError("publication_manifest_invalid")
    tree = directory / "tree"
    paths = paths_for_root(tree)
    result = verify_committed_registry(paths=paths, environment=environment, now=now)
    expected_paths = allowed_output_paths(paths, manifest["new_sequence"])
    normalized_expected = [Path(item).as_posix() for item in manifest["changed_paths"]]
    if normalized_expected != expected_paths:
        raise RegistryOperationError("publication_path_allowlist_invalid")
    if manifest["registry_digest"] != result["registry_digest"]:
        raise RegistryOperationError("publication_registry_digest_mismatch")
    if (
        manifest["new_sequence"] != result["sequence"]
        or manifest["expected_current_sequence"] != result["sequence"] - 1
        or manifest["workflow_base_sha"] != result["workflow_base_sha"]
        or manifest["dispatch_sha"] != result["dispatch_sha"]
        or manifest["operation"] != result["last_operation"]
        or manifest["lifecycle_evidence_digest"]
        != result["last_lifecycle_evidence_digest"]
    ):
        raise RegistryOperationError(
            "publication_manifest_binding_mismatch",
            category="publication_manifest_binding_mismatch",
        )
    summary_raw = (directory / "operation-summary.json").read_bytes()
    summary = _strict_json_loads(summary_raw)
    if not isinstance(summary, dict) or summary_raw != _canonical_file(summary):
        raise RegistryOperationError("operation_summary_invalid")
    summary_bindings = {
        "operation": manifest["operation"],
        "workflow_base_sha": manifest["workflow_base_sha"],
        "dispatch_sha": manifest["dispatch_sha"],
        "expected_current_sequence": manifest["expected_current_sequence"],
        "new_sequence": manifest["new_sequence"],
        "registry_digest": manifest["registry_digest"],
        "evidence_digest": manifest["lifecycle_evidence_digest"],
        "allowed_output_paths": manifest["changed_paths"],
        "data_only": True,
    }
    if any(summary.get(key) != value for key, value in summary_bindings.items()):
        raise RegistryOperationError(
            "operation_summary_binding_mismatch",
            category="operation_summary_binding_mismatch",
        )
    return {**result, "publication_manifest_valid": True}


def _local_head_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "0" * 40


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
        signed = _bounded_sha(signed_base_sha, category="signed_base_mismatch")
        if signed != base:
            raise RegistryOperationError("signed_base_mismatch", category="signed_base_mismatch")
    if resolved != base:
        category = "workflow_base_moved" if phase == "signing" else "publication_base_moved"
        raise RegistryOperationError(category, category=category)
    if current_sequence != expected_current_sequence:
        raise RegistryOperationError(
            "workflow_sequence_stale", category="workflow_sequence_stale"
        )


def check_origin_main_freshness(
    *,
    repository: Path,
    workflow_base_sha: str,
    expected_current_sequence: int,
    phase: str,
    environment: Mapping[str, str] | None = None,
    signed_base_sha: str | None = None,
) -> dict[str, Any]:
    if phase not in {"signing", "publication"}:
        raise RegistryOperationError("freshness_phase_invalid")
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
        resolved = subprocess.run(
            ["git", "rev-parse", "refs/remotes/origin/main"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        category = "workflow_base_moved" if phase == "signing" else "publication_base_moved"
        raise RegistryOperationError(category, category=category) from None
    if expected_current_sequence == 0:
        current_sequence = 0
        for path in (
            "upstream-trust/upstream-dashboard-registry.json",
            "upstream-trust/upstream-dashboard-registry.sig.json",
        ):
            result = subprocess.run(
                ["git", "cat-file", "-e", f"refs/remotes/origin/main:{path}"],
                cwd=repository,
                capture_output=True,
            )
            if result.returncode == 0:
                raise RegistryOperationError(
                    "workflow_sequence_stale", category="workflow_sequence_stale"
                )
    else:
        def show(path: str) -> bytes:
            try:
                return subprocess.run(
                    ["git", "show", f"refs/remotes/origin/main:{path}"],
                    cwd=repository,
                    check=True,
                    capture_output=True,
                ).stdout
            except subprocess.CalledProcessError:
                raise RegistryOperationError(
                    "workflow_sequence_stale", category="workflow_sequence_stale"
                ) from None

        registry_raw = show("upstream-trust/upstream-dashboard-registry.json")
        signature_raw = show("upstream-trust/upstream-dashboard-registry.sig.json")
        public_text = verification_public_key(environment)
        registry = _strict_json_loads(registry_raw)
        if not isinstance(registry, dict):
            raise RegistryOperationError(
                "workflow_sequence_stale", category="workflow_sequence_stale"
            )
        generated = datetime.fromisoformat(registry["generated_at"].replace("Z", "+00:00"))
        try:
            verify_registry(
                registry_raw,
                signature_raw,
                public_key=_parse_public_key(public_text),
                now=generated,
                source=f"workflow_{phase}_main",
            )
        except RegistryValidationError:
            raise RegistryOperationError(
                "workflow_sequence_stale", category="workflow_sequence_stale"
            ) from None
        current_sequence = registry["sequence"]
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


def _candidate_from_cli(args: argparse.Namespace) -> MutationCandidate:
    return prepare_mutation_candidate(
        operation=args.operation,
        upstream_version=args.upstream_version or None,
        expected_current_sequence=args.expected_current_sequence,
        expiry_days=args.expiry_days,
        operator_reason=args.operator_reason,
        workflow_base_sha=args.workflow_base_sha,
        dispatch_sha=args.dispatch_sha,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", choices=OPERATIONS, required=True)
    parser.add_argument(
        "--phase",
        choices=("direct", "prepare", "sign", "verify-artifacts"),
        default="direct",
    )
    parser.add_argument("--upstream-version", default="")
    parser.add_argument("--expected-current-sequence", type=int)
    parser.add_argument("--expiry-days", type=int, default=DEFAULT_EXPIRY_DAYS)
    parser.add_argument("--operator-reason", default="")
    parser.add_argument("--workflow-base-sha", default="")
    parser.add_argument("--dispatch-sha", default="")
    parser.add_argument("--artifact-directory", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.phase == "verify-artifacts":
            if args.artifact_directory is None:
                raise RegistryOperationError("artifact_directory_required")
            result = verify_signed_artifact_directory(args.artifact_directory)
        elif args.phase == "sign":
            if args.artifact_directory is None:
                raise RegistryOperationError("artifact_directory_required")
            result = sign_candidate_directory(
                args.artifact_directory / "unsigned",
                args.artifact_directory / "signed",
            )
        elif args.operation == "verify":
            if args.phase != "direct" or args.upstream_version or args.dry_run:
                raise RegistryOperationError("verify arguments are invalid")
            result = verify_committed_registry()
        else:
            if args.expected_current_sequence is None:
                raise RegistryOperationError(
                    "expected_current_sequence is required for mutating operations"
                )
            if args.phase == "prepare":
                if args.artifact_directory is None:
                    raise RegistryOperationError("artifact_directory_required")
                candidate = _candidate_from_cli(args)
                result = write_unsigned_candidate(candidate, args.artifact_directory)
            elif args.phase == "direct":
                result = mutate_registry(
                    operation=args.operation,
                    upstream_version=args.upstream_version or None,
                    expected_current_sequence=args.expected_current_sequence,
                    expiry_days=args.expiry_days,
                    operator_reason=args.operator_reason,
                    dry_run=args.dry_run,
                    workflow_base_sha=args.workflow_base_sha or None,
                    dispatch_sha=args.dispatch_sha or None,
                )
            else:
                raise RegistryOperationError("unsupported_phase")
    except (ValueError, OSError) as exc:
        category = getattr(exc, "category", "registry_lifecycle_failed")
        status = getattr(exc, "rollback_status", None)
        message = category if status is None else f"{category}:{status}"
        print(message, file=sys.stderr)
        return 2
    print(canonical_json(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
