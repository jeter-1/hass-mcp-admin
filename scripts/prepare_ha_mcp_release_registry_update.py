"""Prepare one signed, data-only ha-mcp release-registry journal update.

The protected workflow resolves the fixed official source and image, then
places bounded release evidence and an exact runtime capture under ``.compat``.
This utility accepts no repository, registry URL, output-path, profile, adapter,
or key arguments.  The signed record may select only the best matching profile
already compiled into Engineering; it cannot add executable behavior.
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
import tempfile
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.ha_mcp_readmission.ha_mcp import (  # noqa: E402
    _profile_for_release,
    _signed_matching_capabilities,
)
from ha_mcp_engineering.ha_mcp_readmission.registry import (  # noqa: E402
    MAX_AUTHORITY_CHAIN_ENVELOPES,
    MAX_CACHE_BYTES,
    MAX_REVOCATION_SOURCE_ENVELOPES,
    REGISTRY_ID,
    TRUST_ANCHOR_KEY_ID,
    _parse_signed_journal,
)
from ha_mcp_engineering.signed_registry import (  # noqa: E402
    ReleaseRevocation,
    ReviewedReleaseEntry,
    TrustAnchorStore,
    canonical_json,
    sha256_digest,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    catalog_fingerprint,
    load_reviewed_upstream_release_registry,
    runtime_contract_fingerprint,
    runtime_description_fingerprint,
    schema_fingerprint,
)


REGISTRY_PATH = ROOT / "upstream-trust" / "ha-mcp-release-registry.json"
EVIDENCE_DIRECTORY = ROOT / "docs" / "evidence" / "ha-mcp-release-registry"
INDEX_PATH = ROOT / "docs" / "generated" / "HA_MCP_RELEASE_REGISTRY_INDEX.md"
CAPTURE_PATH = ROOT / ".compat" / "ha-mcp-runtime-capture.json"
RELEASE_EVIDENCE_PATH = ROOT / ".compat" / "ha-mcp-release-evidence.json"
STABLE_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")
SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_JOURNAL_ENVELOPES = min(32, MAX_AUTHORITY_CHAIN_ENVELOPES)
EXPIRY_DAYS = 90
MAX_CAPTURE_BYTES = 4 * 1024 * 1024
MAX_RELEASE_EVIDENCE_BYTES = 64 * 1024
EXPECTED_ERROR_PROBES = frozenset(
    {
        "invalid_search",
        "missing_state",
        "missing_automation",
        "missing_registry_entity",
    }
)


def _strict_json(
    path: Path, *, maximum: int
) -> tuple[dict[str, Any], bytes]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise SystemExit(f"duplicate JSON member in {path.name}")
            value[key] = item
        return value

    def reject_nonfinite(_value: str) -> None:
        raise SystemExit(f"non-finite JSON value in {path.name}")

    try:
        raw = path.read_bytes()
        if not 1 <= len(raw) <= maximum:
            raise SystemExit(f"bounded evidence exceeds limit: {path.name}")
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_nonfinite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid bounded evidence: {path.name}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"invalid bounded evidence: {path.name}")
    return value, raw


def _signing_key() -> Ed25519PrivateKey:
    encoded = os.environ.get("HA_MCP_RELEASE_REGISTRY_SIGNING_KEY", "")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) != 32:
            raise ValueError
        return Ed25519PrivateKey.from_private_bytes(raw)
    except (binascii.Error, ValueError):
        raise SystemExit("protected Ed25519 signing key is missing or invalid") from None


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _signed(unsigned: dict[str, Any], key: Ed25519PrivateKey) -> dict[str, Any]:
    return {
        **unsigned,
        "signature": base64.b64encode(key.sign(canonical_json(unsigned))).decode(
            "ascii"
        ),
    }


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _release_evidence(version: str) -> dict[str, Any]:
    value, _raw = _strict_json(
        RELEASE_EVIDENCE_PATH, maximum=MAX_RELEASE_EVIDENCE_BYTES
    )
    required = {
        "architecture_image_digests",
        "image_index_digest",
        "image_revision",
        "source_commit",
        "source_tag",
        "source_tag_object",
        "source_tree",
        "version",
    }
    if set(value) != required or value.get("version") != version:
        raise SystemExit("release evidence does not match the exact version")
    if value.get("source_tag") != f"v{version}":
        raise SystemExit("release evidence does not match the exact tag")
    for field in ("source_commit", "source_tag_object", "source_tree", "image_revision"):
        if not isinstance(value.get(field), str) or not GIT_OBJECT.fullmatch(
            value[field]
        ):
            raise SystemExit(f"release evidence has an invalid {field}")
    if not isinstance(value.get("image_index_digest"), str) or not SHA256_DIGEST.fullmatch(
        value["image_index_digest"]
    ):
        raise SystemExit("release evidence has an invalid image index")
    architecture = value.get("architecture_image_digests")
    if not isinstance(architecture, dict) or set(architecture) != {
        "linux/amd64",
        "linux/arm64",
    }:
        raise SystemExit("release evidence has an incomplete architecture map")
    if any(
        not isinstance(item, str) or not SHA256_DIGEST.fullmatch(item)
        for item in architecture.values()
    ):
        raise SystemExit("release evidence has an invalid architecture digest")
    return value


def _capture(version: str) -> tuple[dict[str, Any], bytes]:
    value, raw = _strict_json(CAPTURE_PATH, maximum=MAX_CAPTURE_BYTES)
    required = {
        "capture_format_version",
        "catalog_fingerprint",
        "error_shapes",
        "protocol_version",
        "server_name",
        "server_version",
        "tool_count",
        "tools",
    }
    tools = value.get("tools")
    error_shapes = value.get("error_shapes")
    if (
        set(value) != required
        or value.get("capture_format_version") != 1
        or value.get("server_name") != "ha-mcp"
        or value.get("server_version") != version
        or value.get("protocol_version") != "2025-03-26"
        or not isinstance(tools, list)
        or not 1 <= len(tools) <= 512
        or value.get("tool_count") != len(tools)
        or value.get("catalog_fingerprint") != catalog_fingerprint(tools)
        or not isinstance(error_shapes, dict)
        or set(error_shapes) != EXPECTED_ERROR_PROBES
    ):
        raise SystemExit("runtime capture is incomplete or mismatched")
    names = [item.get("name") if isinstance(item, dict) else None for item in tools]
    if any(not isinstance(name, str) for name in names) or len(names) != len(
        set(names)
    ):
        raise SystemExit("runtime capture contains malformed or duplicate tools")
    for evidence in error_shapes.values():
        if (
            not isinstance(evidence, dict)
            or set(evidence)
            != {"is_error", "structured_code", "shape_fingerprint"}
            or evidence["is_error"] is not True
            or not isinstance(evidence["structured_code"], str)
            or not 1 <= len(evidence["structured_code"]) <= 128
            or not isinstance(evidence["shape_fingerprint"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", evidence["shape_fingerprint"])
        ):
            raise SystemExit("runtime error evidence is incomplete or malformed")
    return value, raw


def _entry_for_profile(
    *,
    version: str,
    release_evidence: dict[str, Any],
    capture: dict[str, Any],
    capture_digest: str,
    profile_release: Any,
    review_date: str,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    observed = {item["name"]: item for item in capture["tools"]}
    policy_by_name = profile_release.policy.by_name
    contracts: list[dict[str, Any]] = []
    for tool_name, tool in observed.items():
        policy_entry = policy_by_name.get(tool_name)
        classification = (
            policy_entry.classification
            if policy_entry is not None
            else "unsupported"
        )
        restrictions = (
            list(policy_entry.argument_restrictions)
            if policy_entry is not None
            else []
        )
        description = runtime_description_fingerprint(tool.get("description"))
        contracts.append(
            {
                "tool_name": tool_name,
                "input_schema_fingerprint": schema_fingerprint(
                    tool.get("inputSchema")
                ),
                "description_fingerprint": description
                or schema_fingerprint(
                    {"invalid_description": True}
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
                    model=profile_release.runtime_contract_fingerprint_model,
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
                "argument_restrictions": restrictions,
            }
        )
    automatic = tuple(
        sorted(
            item.upstream_name
            for item in profile_release.policy.tools
            if item.classification == "automatic_read"
            and item.upstream_name in observed
        )
    )
    contracts.sort(key=lambda item: item["tool_name"])
    entry = {
        "entry_id": (
            f"ha-mcp-v{version}-"
            f"{release_evidence['image_index_digest'].split(':')[-1][:8]}"
        ),
        "approval_status": "reviewed",
        "server_name": "ha-mcp",
        "version": version,
        "allowed_protocol_versions": ["2025-03-26"],
        "source_repository": "https://github.com/homeassistant-ai/ha-mcp",
        "release_tag": f"v{version}",
        "source_commit": release_evidence["source_commit"],
        "image_index_digest": release_evidence["image_index_digest"],
        "architecture_image_digests": release_evidence[
            "architecture_image_digests"
        ],
        "image_revision": release_evidence["image_revision"],
        "advertised_tool_count": capture["tool_count"],
        "catalog_fingerprint": capture["catalog_fingerprint"],
        "capture_resource": (
            "docs/evidence/ha-mcp-release-registry/"
            f"ha-mcp-{version}.json"
        ),
        "capture_sha256": capture_digest,
        "capture_format_version": capture["capture_format_version"],
        "policy_resource": profile_release.policy_resource,
        "policy_sha256": profile_release.policy_sha256,
        "review_provenance": [
            "Exact official source tag and immutable OCI catalog capture.",
            "Capability-local comparison with a binary-owned Engineering profile.",
        ],
        "review_date": review_date,
        "dashboard_attestation": {
            "status": "quarantined",
            "entry_id": None,
            "attestation_fingerprint": None,
            "compiled_constraints_fingerprint": None,
        },
        "error_contract_fingerprint": schema_fingerprint(
            capture["error_shapes"]
        ),
        "entity_lookup_missing_resource_status": (
            "ambiguous_upstream_service_call_failed"
            if capture["error_shapes"]
            .get("missing_registry_entity", {})
            .get("structured_code")
            == "SERVICE_CALL_FAILED"
            else "deterministic_entity_not_found"
        ),
        "tool_contracts": contracts,
        "provider_argument_constraints": [
            {
                "provider_id": "upstream_read_gateway",
                "tool_name": name,
                "constraints_fingerprint": schema_fingerprint(
                    {
                        "argument_restrictions": list(
                            profile_release.policy.by_name[
                                name
                            ].argument_restrictions
                        )
                    }
                ),
            }
            for name in automatic
        ],
    }
    parsed = ReviewedReleaseEntry.from_mapping(entry)
    matched = _signed_matching_capabilities(
        parsed,
        profile_release,
        _profile_for_release(profile_release),
    )
    return parsed.to_mapping(), matched


def _select_entry(
    *,
    version: str,
    release_evidence: dict[str, Any],
    capture: dict[str, Any],
    capture_digest: str,
    review_date: str,
) -> tuple[dict[str, Any], Any, tuple[str, ...]]:
    candidates: list[tuple[int, str, dict[str, Any], Any, tuple[str, ...]]] = []
    for release in load_reviewed_upstream_release_registry().releases:
        if release.revoked or release.provider_disposition("read_gateway") == "held":
            continue
        entry, matched = _entry_for_profile(
            version=version,
            release_evidence=release_evidence,
            capture=capture,
            capture_digest=capture_digest,
            profile_release=release,
            review_date=review_date,
        )
        candidates.append((len(matched), release.version, entry, release, matched))
    if not candidates:
        raise SystemExit("no binary-owned read profile is available")
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best = candidates[0]
    if best[0] == 0 or sum(item[0] == best[0] for item in candidates) != 1:
        raise SystemExit("release does not select one unambiguous binary profile")
    return best[2], best[3], best[4]


def _load_journal(key: Ed25519PrivateKey):
    if not REGISTRY_PATH.exists():
        return None
    if not 1 <= REGISTRY_PATH.stat().st_size <= MAX_CACHE_BYTES:
        raise SystemExit("existing registry exceeds the runtime cache bound")
    return _parse_signed_journal(
        REGISTRY_PATH.read_bytes(),
        trust_anchors=TrustAnchorStore(
            {TRUST_ANCHOR_KEY_ID: key.public_key()}
        ),
    )


def _build_journal(
    *,
    key: Ed25519PrivateKey,
    current: Any,
    entries: list[dict[str, Any]],
    revocations: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    previous = current.accepted if current is not None else None
    unsigned = {
        "schema_version": 1,
        "registry_id": REGISTRY_ID,
        "sequence": 1 if previous is None else previous.sequence + 1,
        "generated_at": _utc(now),
        "expires_at": _utc(now + timedelta(days=EXPIRY_DAYS)),
        "previous_registry_sha256": (
            None if previous is None else previous.content_digest
        ),
        "key_id": TRUST_ANCHOR_KEY_ID,
        "entries": entries,
        "revocations": revocations,
    }
    envelope = _signed(unsigned, key)
    envelopes = (
        [envelope]
        if current is None
        else [*[item.to_mapping() for item in current.envelopes], envelope]
    )
    revocation_sources = (
        []
        if current is None
        else [item.to_mapping() for item in current.revocation_sources]
    )
    if len(envelopes) > MAX_JOURNAL_ENVELOPES:
        removed = envelopes[: len(envelopes) - MAX_JOURNAL_ENVELOPES]
        envelopes = envelopes[-MAX_JOURNAL_ENVELOPES:]
        existing_digests = {
            sha256_digest({key: value for key, value in item.items() if key != "signature"})
            for item in revocation_sources
        }
        for item in removed:
            if item.get("revocations"):
                digest = sha256_digest(
                    {key: value for key, value in item.items() if key != "signature"}
                )
                if digest not in existing_digests:
                    revocation_sources.append(item)
                    existing_digests.add(digest)
        if len(revocation_sources) > MAX_REVOCATION_SOURCE_ENVELOPES:
            raise SystemExit("retained denial-only revocation capacity exhausted")
    first = envelopes[0]
    journal_unsigned = {
        "schema_version": 1,
        "registry_id": REGISTRY_ID,
        "key_id": TRUST_ANCHOR_KEY_ID,
        "checkpoint_sequence": first["sequence"],
        "checkpoint_previous_registry_sha256": first[
            "previous_registry_sha256"
        ],
        "envelopes": envelopes,
        "revocation_sources": revocation_sources,
    }
    journal = _signed(journal_unsigned, key)
    _parse_signed_journal(
        canonical_json(journal),
        trust_anchors=TrustAnchorStore(
            {TRUST_ANCHOR_KEY_ID: key.public_key()}
        ),
    )
    return journal


def prepare(
    *,
    version: str,
    operation: str,
    revocation_reason: str | None = None,
    now: datetime | None = None,
) -> None:
    if not STABLE_VERSION.fullmatch(version):
        raise SystemExit("version must be one exact stable semantic version")
    if operation not in {"add", "revoke"}:
        raise SystemExit("operation is invalid")
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise SystemExit("registry clock is invalid")
    timestamp = timestamp.astimezone(timezone.utc).replace(microsecond=0)
    key = _signing_key()
    current = _load_journal(key)
    current_entries = (
        []
        if current is None
        else [item.to_mapping() for item in current.accepted.entries]
    )
    current_revocations = (
        []
        if current is None
        else [item.to_mapping() for item in current.accepted.revocations]
    )

    if operation == "add":
        if any(item["version"] == version for item in current_entries):
            raise SystemExit("the exact release already has positive authority")
        if any(item["version"] == version for item in current_revocations):
            raise SystemExit("a revoked release cannot be re-added")
        release = _release_evidence(version)
        capture, capture_raw = _capture(version)
        capture_digest = "sha256:" + hashlib.sha256(capture_raw).hexdigest()
        entry, profile, matched = _select_entry(
            version=version,
            release_evidence=release,
            capture=capture,
            capture_digest=capture_digest,
            review_date=timestamp.strftime("%Y-%m-%d"),
        )
        current_entries.append(entry)
        expected = tuple(
            sorted(
                item.upstream_name
                for item in profile.policy.tools
                if item.classification == "automatic_read"
            )
        )
        evidence = {
            "schema_version": 1,
            "version": version,
            "source_tag_object": release["source_tag_object"],
            "source_commit": release["source_commit"],
            "source_tree": release["source_tree"],
            "image_index_digest": release["image_index_digest"],
            "architecture_image_digests": release[
                "architecture_image_digests"
            ],
            "capture_sha256": capture_digest,
            "catalog_fingerprint": capture["catalog_fingerprint"],
            "advertised_tool_count": capture["tool_count"],
            "error_contract_fingerprint": schema_fingerprint(
                capture["error_shapes"]
            ),
            "selected_binary_profile_version": profile.version,
            "selected_policy_resource": profile.policy_resource,
            "selected_policy_sha256": profile.policy_sha256,
            "matched_read_count": len(matched),
            "matched_reads": list(matched),
            "withheld_reviewed_reads": sorted(set(expected) - set(matched)),
            "unknown_tool_count": len(
                set(item["name"] for item in capture["tools"])
                - set(profile.policy.by_name)
            ),
            "dashboard_authority": "separate_attestation_required",
            "fallback": "none",
            "upstream_write_dispatches": 0,
            "prepared_at": _utc(timestamp),
        }
        evidence_path = EVIDENCE_DIRECTORY / f"ha-mcp-{version}.json"
        _atomic_write(evidence_path, canonical_json(evidence) + b"\n")
    else:
        if not revocation_reason or not 1 <= len(revocation_reason) <= 512:
            raise SystemExit("a bounded revocation reason is required")
        matching = [item for item in current_entries if item["version"] == version]
        if len(matching) != 1:
            raise SystemExit("revocation requires one exact positive entry")
        target = matching[0]
        current_entries = [
            item for item in current_entries if item["version"] != version
        ]
        tombstone = {
            "entry_id": target["entry_id"],
            "server_name": target["server_name"],
            "version": target["version"],
            "image_index_digest": target["image_index_digest"],
            "revoked_at": _utc(timestamp),
            "reason": revocation_reason,
        }
        current_revocations = [
            item for item in current_revocations if item["version"] != version
        ]
        current_revocations.append(ReleaseRevocation.from_mapping(tombstone).to_mapping())

    journal = _build_journal(
        key=key,
        current=current,
        entries=sorted(current_entries, key=lambda item: (item["server_name"], item["version"])),
        revocations=sorted(
            current_revocations,
            key=lambda item: (item["server_name"], item["version"]),
        ),
        now=timestamp,
    )
    _atomic_write(REGISTRY_PATH, canonical_json(journal) + b"\n")
    accepted = _parse_signed_journal(
        REGISTRY_PATH.read_bytes(),
        trust_anchors=TrustAnchorStore(
            {TRUST_ANCHOR_KEY_ID: key.public_key()}
        ),
    ).accepted
    rows = "".join(
        f"| {item.version} | `{item.entry_id}` | positive |\n"
        for item in accepted.entries
    ) + "".join(
        f"| {item.version} | `{item.entry_id}` | revoked |\n"
        for item in accepted.revocations
    )
    _atomic_write(
        INDEX_PATH,
        (
            "# ha-mcp release registry index\n\n"
            f"Sequence: `{accepted.sequence}`  \n"
            f"Generated: `{accepted.generated_at}`  \n\n"
            "| Version | Entry | Authority |\n"
            "|---|---|---|\n"
            f"{rows}"
        ).encode("utf-8"),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--operation", choices=("add", "revoke"), default="add")
    parser.add_argument("--revocation-reason")
    arguments = parser.parse_args()
    prepare(
        version=arguments.version,
        operation=arguments.operation,
        revocation_reason=arguments.revocation_reason,
    )


if __name__ == "__main__":
    main()
