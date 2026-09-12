"""Offline Core registry preparation; review and signing are distinct steps.

No network, HA access, publication or repository mutation is performed here.
The owner supplies reviewed provenance and an independent Core signing key.
Only references to existing compiled contracts can be prepared for admission.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.ha_core_readmission.probe_profiles import known_probe_profile
from ha_mcp_engineering.ha_core_readmission.profiles import CORE_CAPABILITY_PROFILES
from ha_mcp_engineering.ha_core_readmission.registry import CORE_REGISTRY_BINDING
from ha_mcp_engineering.ha_core_readmission.registry_models import (
    CORE_REGISTRY_ID, CORE_REGISTRY_KEY_ID, CoreReleaseEntry,
)
from ha_mcp_engineering.ha_mcp_readmission.registry import (
    MAX_CACHE_BYTES, MAX_REVOCATION_SOURCE_ENVELOPES, _parse_signed_journal,
)
from ha_mcp_engineering.signed_registry import (
    ReleaseRevocation, TrustAnchorStore, canonical_json, sha256_digest,
)
from ha_mcp_engineering.signed_registry.models import parse_utc_timestamp

MODEL = "core-registry-review-candidate-v1"
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
MAX_CHAIN = 32


def strict_json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result

    def nonfinite(_value):
        raise ValueError("nonfinite JSON")

    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=nonfinite)


def read_bounded(path: Path, maximum=MAX_CACHE_BYTES) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if not 1 <= len(raw) <= maximum:
        raise ValueError("input exceeds bound")
    return raw


def utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("UTC timestamp required")
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def parse_journal(raw: bytes | None, public_key):
    if raw is None:
        return None
    return _parse_signed_journal(
        raw, trust_anchors=TrustAnchorStore({CORE_REGISTRY_KEY_ID: public_key}),
        binding=CORE_REGISTRY_BINDING,
    )


def require_known_contracts(entry: CoreReleaseEntry) -> None:
    if known_probe_profile(entry.probe_profile_id, entry.probe_profile_sha256) is None:
        raise ValueError("review requires an existing compiled probe profile")
    profiles = {p.capability_id: p for p in CORE_CAPABILITY_PROFILES}
    for reference in entry.capabilities:
        profile = profiles.get(reference.capability_id)
        if profile is None or not profile.auto_eligible or any(
            reference.to_mapping()[key] != profile.to_mapping()[key]
            for key in reference.to_mapping()
        ):
            raise ValueError("review requires existing exact capability contracts")


def prepare_candidate(*, entry, evidence: bytes, previous: bytes | None,
                      public_key, operation="add", reason=None, now=None):
    """Produce unsigned review material; successful probes alone cannot sign it."""
    timestamp = now or datetime.now(timezone.utc)
    parsed = CoreReleaseEntry.from_mapping(entry)
    require_known_contracts(parsed)
    if not 1 <= len(evidence) <= MAX_EVIDENCE_BYTES:
        raise ValueError("review evidence exceeds bound")
    if hashlib.sha256(evidence).hexdigest() != parsed.evidence_sha256:
        raise ValueError("review evidence hash mismatch")
    current = parse_journal(previous, public_key)
    tip = None if current is None else current.accepted
    entries = [] if tip is None else [e.to_mapping() for e in tip.entries]
    # Preserve every verified tombstone, including compacted denial sources.
    tombstones = {}
    if current is not None:
        for envelope in (*current.envelopes, *current.revocation_sources):
            for revocation in envelope.revocations:
                tombstones[revocation.release_identity] = revocation.to_mapping()
    if operation == "add":
        if parsed.release_identity in tombstones:
            raise ValueError("revoked release cannot be re-added")
        old = next((e for e in entries if e["version"] == parsed.version), None)
        if old is not None and any(
            old[key] != parsed.to_mapping()[key]
            for key in old if key != "evidence_sha256"
        ):
            raise ValueError("renewal cannot replace release identity or contracts")
        entries = [e for e in entries if e["version"] != parsed.version]
        entries.append(parsed.to_mapping())
    elif operation == "revoke":
        tombstone = ReleaseRevocation.from_mapping({
            **{key: entry[key] for key in
               ("entry_id", "server_name", "version", "image_index_digest")},
            "revoked_at": utc(timestamp), "reason": reason,
        })
        tombstones[parsed.release_identity] = tombstone.to_mapping()
        entries = [e for e in entries if e["version"] != parsed.version]
    else:
        raise ValueError("unknown operation")
    return {
        "model": MODEL,
        "operation": operation,
        "public_key_sha256": hashlib.sha256(public_key.public_bytes_raw()).hexdigest(),
        "previous_journal_sha256": None if previous is None else hashlib.sha256(previous).hexdigest(),
        "review_evidence_sha256": parsed.evidence_sha256,
        "envelope": {
            "schema_version": 1, "registry_id": CORE_REGISTRY_ID,
            "key_id": CORE_REGISTRY_KEY_ID,
            "sequence": 1 if tip is None else tip.sequence + 1,
            "previous_registry_sha256": None if tip is None else tip.content_digest,
            "generated_at": utc(timestamp),
            "expires_at": utc(timestamp + timedelta(days=90)),
            "entries": sorted(entries, key=lambda e: e["version"]),
            "revocations": sorted(tombstones.values(), key=lambda e: e["version"]),
        },
    }


def signed(unsigned, key):
    return {**unsigned, "signature": base64.b64encode(
        key.sign(canonical_json(unsigned))).decode("ascii")}


def sign_candidate(candidate: bytes, *, expected_sha256: str,
                   previous: bytes | None, key, now=None) -> bytes:
    """Sign exactly reviewed bytes, fenced to the current authenticated journal."""
    if hashlib.sha256(candidate).hexdigest() != expected_sha256:
        raise ValueError("reviewed candidate hash mismatch")
    value = strict_json(candidate)
    if set(value) != {"model", "operation", "public_key_sha256",
                      "previous_journal_sha256", "review_evidence_sha256", "envelope"}:
        raise ValueError("candidate fields invalid")
    if value["model"] != MODEL or value["operation"] not in {"add", "revoke"}:
        raise ValueError("candidate model invalid")
    if value["public_key_sha256"] != hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest():
        raise ValueError("reviewed Core key mismatch")
    if value["previous_journal_sha256"] != (None if previous is None else hashlib.sha256(previous).hexdigest()):
        raise ValueError("current journal changed after review")
    current = parse_journal(previous, key.public_key())
    tip = None if current is None else current.accepted
    if (value["envelope"]["sequence"] != (1 if tip is None else tip.sequence + 1)
            or value["envelope"]["previous_registry_sha256"] != (
                None if tip is None else tip.content_digest)):
        raise ValueError("reviewed journal successor mismatch")
    envelope = signed(value["envelope"], key)
    # Validate closed schemas, chain, timestamps and retained denials before output.
    envelopes = ([] if current is None else [e.to_mapping() for e in current.envelopes]) + [envelope]
    sources = [] if current is None else [e.to_mapping() for e in current.revocation_sources]
    removed, envelopes = envelopes[:-MAX_CHAIN], envelopes[-MAX_CHAIN:]
    for old in removed:
        if old["revocations"] and old not in sources:
            sources.append(old)
    if len(sources) > MAX_REVOCATION_SOURCE_ENVELOPES:
        raise ValueError("retained revocation bound exhausted")
    journal = signed({
        "schema_version": 1, "registry_id": CORE_REGISTRY_ID,
        "key_id": CORE_REGISTRY_KEY_ID,
        "checkpoint_sequence": envelopes[0]["sequence"],
        "checkpoint_previous_registry_sha256": envelopes[0]["previous_registry_sha256"],
        "envelopes": envelopes, "revocation_sources": sources,
    }, key)
    raw = canonical_json(journal) + b"\n"
    if len(raw) > MAX_CACHE_BYTES:
        raise ValueError("journal exceeds runtime bound")
    result = parse_journal(raw, key.public_key())
    timestamp = now or datetime.now(timezone.utc)
    generated = parse_utc_timestamp(result.accepted.generated_at)
    expires = parse_utc_timestamp(result.accepted.expires_at)
    if not generated <= timestamp < expires or expires - generated > timedelta(days=90):
        raise ValueError("candidate is not current at signing")
    for entry in result.accepted.entries:
        require_known_contracts(entry)
    return raw


def write_new(path: Path, raw: bytes) -> None:
    # Never overwrite a reviewed candidate, historical receipt or published file.
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("add", "revoke", "sign"))
    parser.add_argument("--entry", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--public-key", type=Path, help="Base64 public key only")
    parser.add_argument("--previous", type=Path, help="Current signed journal; omit only for bootstrap")
    parser.add_argument("--reason")
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--expected-candidate-sha256")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    previous = None if args.previous is None else read_bounded(args.previous)
    if args.operation == "sign":
        encoded = os.environ.get("HA_CORE_RELEASE_REGISTRY_SIGNING_KEY", "")
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(encoded, validate=True))
        raw = sign_candidate(read_bounded(args.candidate),
                             expected_sha256=args.expected_candidate_sha256,
                             previous=previous, key=key)
    else:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(
            read_bounded(args.public_key, 256).strip(), validate=True))
        value = prepare_candidate(
            entry=strict_json(read_bounded(args.entry, 65536)),
            evidence=read_bounded(args.evidence, MAX_EVIDENCE_BYTES),
            previous=previous, public_key=public_key, operation=args.operation,
            reason=args.reason,
        )
        raw = canonical_json(value) + b"\n"
    write_new(args.output, raw)
    print("Prepared output SHA256: " + hashlib.sha256(raw).hexdigest())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Input and key errors must never export supplied values or traceback.
        raise SystemExit("Core registry preparation refused; review inputs and bounds.") from None
