"""Create one data-only signed upstream compatibility registry update.

The workflow supplies evidence resolved from fixed upstream locations.  This
script accepts no repository, image, registry URL, output path, or signing key
argument.  The private Ed25519 seed is read only from the protected environment
secret and is never printed.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

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
    _parse_public_key,
    verify_registry,
)


REGISTRY_PATH = ROOT / "upstream-trust" / "upstream-dashboard-registry.json"
SIGNATURE_PATH = ROOT / "upstream-trust" / "upstream-dashboard-registry.sig.json"
EVIDENCE_DIRECTORY = ROOT / "docs" / "evidence" / "upstream-compatibility"
INDEX_PATH = ROOT / "docs" / "generated" / "UPSTREAM_TRUST_REGISTRY_INDEX.md"
RUNTIME_EVIDENCE_PATH = ROOT / ".compat" / "runtime-evidence.json"
RELEASE_EVIDENCE_PATH = ROOT / ".compat" / "release-evidence.json"
STABLE_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"invalid bounded evidence: {path.name}")
    return value


def signing_key() -> Ed25519PrivateKey:
    value = os.environ.get("UPSTREAM_TRUST_REGISTRY_SIGNING_KEY", "")
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) != 32:
            raise ValueError
        return Ed25519PrivateKey.from_private_bytes(raw)
    except (binascii.Error, ValueError):
        raise SystemExit("protected Ed25519 signing key is missing or invalid") from None


def current_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {
            "schema_version": 1,
            "sequence": 0,
            "generated_at": "1970-01-01T00:00:00Z",
            "expires_at": "1970-01-01T00:00:01Z",
            "key_id": "",
            "entries": [],
        }
    value = load_json(REGISTRY_PATH)
    if not SIGNATURE_PATH.exists():
        raise SystemExit("existing registry has no detached signature")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    if not STABLE_VERSION.fullmatch(args.version):
        raise SystemExit("version must be an exact stable semantic version")

    runtime = load_json(RUNTIME_EVIDENCE_PATH)
    release = load_json(RELEASE_EVIDENCE_PATH)
    if runtime.get("server_name") != "ha-mcp" or runtime.get("server_version") != args.version:
        raise SystemExit("runtime identity does not match the requested exact release")
    if release.get("version") != args.version or release.get("source_tag") != f"v{args.version}":
        raise SystemExit("release evidence does not match the requested exact tag")
    tool = runtime.get("required_tool")
    if not isinstance(tool, dict):
        raise SystemExit("runtime required-tool descriptor is missing")
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
        raise SystemExit("runtime contract evidence changed after collection")
    informational = runtime.get("informational_fingerprints")
    informational_keys = {
        "raw_input_schema",
        "reviewed_security_descriptor",
        "fixture_runtime_descriptor",
        "published_runtime_descriptor",
    }
    if not isinstance(informational, dict) or set(informational) != informational_keys:
        raise SystemExit("runtime informational fingerprint evidence is incomplete")
    if runtime.get("write_dispatches") != 0:
        raise SystemExit("runtime review observed an upstream write dispatch")
    negative = runtime.get("negative_reachability")
    required_rejections = {
        "ha_set_entity",
        "ha_set_device",
        "ha_call_service",
        "ha_bulk_control",
        "ha_config_set_dashboard",
        "ha_config_delete_dashboard",
    }
    if (
        not isinstance(negative, dict)
        or set(negative.get("rejected_before_dispatch", [])) != required_rejections
        or negative.get("include_screenshot_true_rejected") is not True
        or negative.get("generic_forwarder_present") is not False
    ):
        raise SystemExit("negative write-reachability evidence is incomplete")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    evidence = {
        "schema_version": 1,
        "version": args.version,
        "release": release,
        "runtime": runtime,
        "compiled_contract_family": CONTRACT_FAMILY,
        "reviewed_at": now.isoformat().replace("+00:00", "Z"),
    }
    evidence_bytes = canonical_json(evidence) + b"\n"
    evidence_digest = "sha256:" + hashlib.sha256(evidence_bytes).hexdigest()
    image_digest = release.get("image_index_digest")
    entry_value = {
        "entry_id": f"ha-mcp-v{args.version}-{str(image_digest).split(':')[-1][:8]}",
        "server_name": "ha-mcp",
        "upstream_version": args.version,
        "source_tag": f"v{args.version}",
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
        "reviewed_at": evidence["reviewed_at"],
        "revoked": False,
    }
    ReleaseAttestation.from_mapping(entry_value)

    private_key = signing_key()
    public_raw = private_key.public_key().public_bytes_raw()
    public_text = base64.b64encode(public_raw).decode()
    configured_public = os.environ.get("UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY", "").strip()
    if configured_public and _parse_public_key(configured_public).public_bytes_raw() != public_raw:
        raise SystemExit("protected signing key does not match the configured verification key")
    registry = current_registry()
    existing_entries = registry.get("entries")
    if not isinstance(existing_entries, list):
        raise SystemExit("existing registry entries are malformed")
    for existing in existing_entries:
        if existing.get("upstream_version") == args.version:
            if existing.get("revoked"):
                raise SystemExit("a revoked release cannot be re-added by the normal workflow")
            raise SystemExit("an exact attestation for this release already exists")
    key_id = os.environ.get("UPSTREAM_TRUST_REGISTRY_KEY_ID", "").strip()
    if not key_id or len(key_id) > 80:
        raise SystemExit("protected registry key identifier is missing or invalid")
    sequence = registry.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise SystemExit("existing registry sequence is invalid")
    updated = {
        "schema_version": 1,
        "sequence": sequence + 1,
        "generated_at": evidence["reviewed_at"],
        "expires_at": (now + timedelta(days=90)).isoformat().replace("+00:00", "Z"),
        "key_id": key_id,
        "entries": [*existing_entries, entry_value],
    }
    signature = {
        "schema_version": 1,
        "algorithm": "Ed25519",
        "key_id": key_id,
        "signature": base64.b64encode(private_key.sign(canonical_json(updated))).decode(),
    }
    verify_registry(
        canonical_json(updated),
        canonical_json(signature),
        public_key=_parse_public_key(public_text),
        now=now,
        source="workflow",
    )

    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SIGNATURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_bytes(canonical_json(updated) + b"\n")
    SIGNATURE_PATH.write_bytes(canonical_json(signature) + b"\n")
    (EVIDENCE_DIRECTORY / f"ha-mcp-{args.version}.json").write_bytes(evidence_bytes)
    INDEX_PATH.write_text(
        "# Upstream trust registry index\n\n"
        f"Sequence: `{updated['sequence']}`  \n"
        f"Generated: `{updated['generated_at']}`  \n\n"
        "| Version | Entry | Family | Revoked |\n"
        "|---|---|---|---|\n"
        + "".join(
            f"| {item['upstream_version']} | `{item['entry_id']}` | `{item['contract_family']}` | {str(item['revoked']).lower()} |\n"
            for item in updated["entries"]
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
