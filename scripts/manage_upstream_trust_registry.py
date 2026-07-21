"""Administer the data-only upstream trust registry.

This release-operator CLI deliberately has fixed repository output paths and no
runtime or network configuration.  Signing material is accepted only through
the protected environment and is never printed or written.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


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
DEFAULT_EXPIRY_DAYS = 90
MIN_EXPIRY_DAYS = 1
MAX_EXPIRY_DAYS = 365
MAX_REASON_LENGTH = 256
MUTATING_OPERATIONS = {"bootstrap", "add", "revoke", "restore", "renew"}
OPERATIONS = (*sorted(MUTATING_OPERATIONS), "verify")


class RegistryOperationError(ValueError):
    """Bounded operator error that contains no remote payload or key material."""


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


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
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


def signing_material(environment: Mapping[str, str] | None = None) -> SigningMaterial:
    env = os.environ if environment is None else environment
    private_raw = _decode_raw_key(
        env.get("UPSTREAM_TRUST_REGISTRY_SIGNING_KEY", ""),
        label="Ed25519 signing key",
    )
    public_raw = _decode_raw_key(
        env.get("UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY", ""),
        label="Ed25519 public key",
    )
    key_id = (env.get("UPSTREAM_TRUST_REGISTRY_KEY_ID", "") or "").strip()
    if not re_full_key_id(key_id):
        raise RegistryOperationError("protected registry key identifier is missing or invalid")
    private_key = Ed25519PrivateKey.from_private_bytes(private_raw)
    if private_key.public_key().public_bytes_raw() != public_raw:
        raise RegistryOperationError(
            "protected signing key does not match the configured verification key"
        )
    return SigningMaterial(
        private_key=private_key,
        public_text=base64.b64encode(public_raw).decode("ascii"),
        key_id=key_id,
    )


def verification_public_key(environment: Mapping[str, str] | None = None) -> str:
    env = os.environ if environment is None else environment
    raw = _decode_raw_key(
        env.get("UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY", ""),
        label="Ed25519 public key",
    )
    return base64.b64encode(raw).decode("ascii")


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


def _read_verified_current(
    paths: RegistryPaths,
    *,
    public_text: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    registry_raw = paths.registry.read_bytes()
    signature_raw = paths.signature.read_bytes()
    registry = _strict_json_loads(registry_raw)
    if not isinstance(registry, dict):
        raise RegistryOperationError("existing registry is malformed")
    signature_value = _strict_json_loads(signature_raw)
    if registry_raw != canonical_json(registry) + b"\n" or signature_raw != canonical_json(
        signature_value
    ) + b"\n":
        raise RegistryOperationError("existing registry files are not canonical JSON")
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
    signature = signature_value
    if not isinstance(signature, dict):
        raise RegistryOperationError("existing detached signature is malformed")
    return registry, signature


def _reviewed_release_entry(
    version: str,
    *,
    paths: RegistryPaths,
    reviewed_at: str,
) -> tuple[dict[str, Any], bytes]:
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
    evidence = {
        "schema_version": 1,
        "version": version,
        "release": release,
        "runtime": runtime,
        "compiled_contract_family": CONTRACT_FAMILY,
        "reviewed_at": reviewed_at,
    }
    evidence_bytes = canonical_json(evidence) + b"\n"
    evidence_digest = "sha256:" + hashlib.sha256(evidence_bytes).hexdigest()
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
        "review_evidence_digest": evidence_digest,
        "reviewed_at": reviewed_at,
        "revoked": False,
    }
    ReleaseAttestation.from_mapping(entry)
    return entry, evidence_bytes


def render_index(registry: Mapping[str, Any]) -> bytes:
    rows = "".join(
        f"| {item['upstream_version']} | `{item['entry_id']}` | "
        f"`{item['contract_family']}` | {str(item['revoked']).lower()} |\n"
        for item in registry["entries"]
    )
    value = (
        "# Upstream trust registry index\n\n"
        f"Sequence: `{registry['sequence']}`  \n"
        f"Generated: `{registry['generated_at']}`  \n"
        f"Expires: `{registry['expires_at']}`  \n\n"
        "| Version | Entry | Family | Revoked |\n"
        "|---|---|---|---|\n"
        f"{rows}"
    )
    return value.encode("utf-8")


def _operation_evidence(
    *,
    operation: str,
    old_sequence: int,
    registry: Mapping[str, Any],
    affected: Mapping[str, Any] | None,
    old_revoked: bool | None,
    reason: str,
) -> bytes:
    value = {
        "schema_version": 1,
        "operation": operation,
        "old_sequence": old_sequence,
        "new_sequence": registry["sequence"],
        "affected_entry": (
            None
            if affected is None
            else {
                "entry_id": affected["entry_id"],
                "server_name": affected["server_name"],
                "upstream_version": affected["upstream_version"],
                "contract_family": affected["contract_family"],
            }
        ),
        "old_revoked": old_revoked,
        "new_revoked": None if affected is None else affected["revoked"],
        "generated_at": registry["generated_at"],
        "expires_at": registry["expires_at"],
        "key_id": registry["key_id"],
        "operator_reason": reason or None,
        "data_only": True,
    }
    return canonical_json(value) + b"\n"


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


def _atomic_write_many(outputs: Mapping[Path, bytes]) -> None:
    temporary: dict[Path, Path] = {}
    try:
        for target, content in outputs.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{target.name}.",
                dir=target.parent,
                delete=False,
            )
            temp_path = Path(handle.name)
            temporary[target] = temp_path
            with handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        for target, temp_path in temporary.items():
            os.replace(temp_path, target)
    finally:
        for temp_path in temporary.values():
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


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


def _summary(
    *,
    operation: str,
    old_sequence: int,
    registry: Mapping[str, Any],
    affected: Mapping[str, Any] | None,
    old_revoked: bool | None,
    evidence_path: Path,
    evidence_bytes: bytes,
    dry_run: bool,
) -> dict[str, Any]:
    registry_digest = "sha256:" + hashlib.sha256(canonical_json(registry)).hexdigest()
    return {
        "schema_version": 1,
        "operation": operation,
        "dry_run": dry_run,
        "old_sequence": old_sequence,
        "new_sequence": registry["sequence"],
        "affected_entry_id": None if affected is None else affected["entry_id"],
        "server_name": None if affected is None else affected["server_name"],
        "upstream_version": None if affected is None else affected["upstream_version"],
        "contract_family": (
            CONTRACT_FAMILY if affected is None else affected["contract_family"]
        ),
        "source_commit": None if affected is None else affected["source_commit"],
        "image_index_digest": (
            None if affected is None else affected["image_index_digest"]
        ),
        "old_revoked": old_revoked,
        "new_revoked": None if affected is None else affected["revoked"],
        "generated_at": registry["generated_at"],
        "expires_at": registry["expires_at"],
        "key_id": registry["key_id"],
        "registry_digest": registry_digest,
        "evidence_digest": "sha256:"
        + hashlib.sha256(evidence_bytes).hexdigest(),
        "evidence_path": str(evidence_path.relative_to(ROOT)).replace("\\", "/")
        if evidence_path.is_relative_to(ROOT)
        else evidence_path.name,
        "data_only": True,
        "engineering_image_change_required": False,
        "written": not dry_run,
    }


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
) -> dict[str, Any]:
    if operation not in MUTATING_OPERATIONS:
        raise RegistryOperationError("unsupported mutating registry operation")
    if isinstance(expected_current_sequence, bool) or expected_current_sequence < 0:
        raise RegistryOperationError("expected_current_sequence must be a nonnegative integer")
    expiry_days = _validate_expiry_days(expiry_days)
    reason = _validate_reason(operator_reason)
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0
    )
    material = signing_material(environment)
    generated_at = utc_text(clock)

    registry_exists = paths.registry.exists()
    signature_exists = paths.signature.exists()
    if operation == "bootstrap":
        if registry_exists or signature_exists:
            raise RegistryOperationError("bootstrap requires no existing registry or signature")
        if expected_current_sequence != 0:
            raise RegistryOperationError("bootstrap expected_current_sequence must be zero")
        current = {"sequence": 0, "entries": []}
    else:
        if not registry_exists or not signature_exists:
            raise RegistryOperationError("the committed registry and signature are required")
        current, _signature = _read_verified_current(
            paths,
            public_text=material.public_text,
        )
        if current.get("key_id") != material.key_id:
            raise RegistryOperationError("configured key identifier does not match the registry")
    old_sequence = current.get("sequence")
    if isinstance(old_sequence, bool) or not isinstance(old_sequence, int):
        raise RegistryOperationError("existing registry sequence is invalid")
    if old_sequence != expected_current_sequence:
        raise RegistryOperationError("stale expected_current_sequence")

    entries_value = current.get("entries")
    if not isinstance(entries_value, list):
        raise RegistryOperationError("existing registry entries are malformed")
    entries = [dict(item) for item in entries_value if isinstance(item, dict)]
    if len(entries) != len(entries_value):
        raise RegistryOperationError("existing registry entries are malformed")
    affected: dict[str, Any] | None = None
    old_revoked: bool | None = None
    release_evidence: bytes | None = None

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
    elif operation == "renew":
        if upstream_version:
            raise RegistryOperationError("upstream_version is not accepted for renew")

    updated = {
        "schema_version": 1,
        "sequence": old_sequence + 1,
        "generated_at": generated_at,
        "expires_at": utc_text(clock + timedelta(days=expiry_days)),
        "key_id": material.key_id,
        "entries": entries,
    }
    registry_bytes, signature_bytes = _sign_registry(
        updated,
        material=material,
        now=clock,
    )
    if release_evidence is not None and affected is not None:
        evidence_path = paths.evidence_directory / f"ha-mcp-{affected['upstream_version']}.json"
        evidence_bytes = release_evidence
    else:
        evidence_path = paths.evidence_directory / (
            f"registry-sequence-{updated['sequence']:06d}.json"
        )
        evidence_bytes = _operation_evidence(
            operation=operation,
            old_sequence=old_sequence,
            registry=updated,
            affected=affected,
            old_revoked=old_revoked,
            reason=reason,
        )
    outputs = {
        paths.registry: registry_bytes,
        paths.signature: signature_bytes,
        evidence_path: evidence_bytes,
        paths.index: render_index(updated),
    }
    summary = _summary(
        operation=operation,
        old_sequence=old_sequence,
        registry=updated,
        affected=affected,
        old_revoked=old_revoked,
        evidence_path=evidence_path,
        evidence_bytes=evidence_bytes,
        dry_run=dry_run,
    )
    if not dry_run:
        _atomic_write_many(outputs)
    return summary


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
    if registry_raw != canonical_json(registry_value) + b"\n" or signature_raw != canonical_json(
        signature_value
    ) + b"\n":
        raise RegistryOperationError("registry files are not canonical JSON")
    verified = verify_registry(
        registry_raw,
        signature_raw,
        public_key=_parse_public_key(public_text),
        now=clock,
        source="operator_verify",
    )
    registry = registry_value
    if not isinstance(registry, dict):
        raise RegistryOperationError("registry is malformed")
    expected_index = render_index(registry)
    if paths.index.read_bytes() != expected_index:
        raise RegistryOperationError("generated registry index is out of date")
    for entry in registry["entries"]:
        evidence_path = paths.evidence_directory / f"ha-mcp-{entry['upstream_version']}.json"
        evidence_raw = evidence_path.read_bytes()
        evidence_value = _strict_json_loads(evidence_raw)
        if evidence_raw != canonical_json(evidence_value) + b"\n":
            raise RegistryOperationError("review evidence is not canonical JSON")
        evidence_digest = "sha256:" + hashlib.sha256(evidence_raw).hexdigest()
        if evidence_digest != entry["review_evidence_digest"]:
            raise RegistryOperationError("review evidence digest mismatch")
    operation_evidence = paths.evidence_directory / (
        f"registry-sequence-{registry['sequence']:06d}.json"
    )
    if operation_evidence.exists():
        operation_raw = operation_evidence.read_bytes()
        operation_value = _strict_json_loads(operation_raw)
        if operation_raw != canonical_json(operation_value) + b"\n":
            raise RegistryOperationError("operation evidence is not canonical JSON")
        if (
            not isinstance(operation_value, dict)
            or operation_value.get("new_sequence") != registry["sequence"]
            or operation_value.get("generated_at") != registry["generated_at"]
            or operation_value.get("expires_at") != registry["expires_at"]
            or operation_value.get("key_id") != registry["key_id"]
            or operation_value.get("data_only") is not True
        ):
            raise RegistryOperationError("operation evidence does not match the registry")
    elif not any(
        entry.get("reviewed_at") == registry["generated_at"]
        for entry in registry["entries"]
    ):
        raise RegistryOperationError("generated registry evidence is missing")
    return {
        "schema_version": 1,
        "operation": "verify",
        "sequence": verified.sequence,
        "entry_count": len(verified.entries),
        "key_id": verified.key_id,
        "registry_digest": verified.payload_digest,
        "signature_valid": True,
        "generated_evidence_valid": True,
        "written": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", choices=OPERATIONS, required=True)
    parser.add_argument("--upstream-version", default="")
    parser.add_argument("--expected-current-sequence", type=int)
    parser.add_argument("--expiry-days", type=int, default=DEFAULT_EXPIRY_DAYS)
    parser.add_argument("--operator-reason", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.operation == "verify":
            if args.upstream_version or args.expected_current_sequence is not None or args.dry_run:
                raise RegistryOperationError(
                    "verify does not accept version, sequence, or dry-run arguments"
                )
            result = verify_committed_registry()
        else:
            if args.expected_current_sequence is None:
                raise RegistryOperationError(
                    "expected_current_sequence is required for mutating operations"
                )
            result = mutate_registry(
                operation=args.operation,
                upstream_version=args.upstream_version or None,
                expected_current_sequence=args.expected_current_sequence,
                expiry_days=args.expiry_days,
                operator_reason=args.operator_reason,
                dry_run=args.dry_run,
            )
    except (ValueError, OSError) as exc:
        message = str(exc)
        if len(message) > 240:
            message = "registry lifecycle operation failed"
        print(message, file=sys.stderr)
        return 2
    print(canonical_json(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
