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
import re
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.ha_core_readmission.probe_profiles import known_probe_profile
from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
from ha_mcp_engineering.ha_core_readmission.models import CORE_IDENTITY
from ha_mcp_engineering.ha_core_readmission.registry import CORE_REGISTRY_BINDING
from ha_mcp_engineering.ha_core_readmission.registry_models import (
    CORE_REGISTRY_ID, CORE_REGISTRY_KEY_ID, CoreReleaseEntry, CoreRegistryEnvelope,
)
from ha_mcp_engineering.ha_mcp_readmission.registry import (
    MAX_CACHE_BYTES, SignedReleaseRegistry, _parse_signed_journal,
)
from ha_mcp_engineering.signed_registry import (
    ReleaseRevocation, TrustAnchorStore, canonical_json, sha256_digest,
)
from ha_mcp_engineering.signed_registry.models import parse_utc_timestamp

MODEL = "core-registry-review-candidate-v1"
EXTENSION_MODEL = "core-registry-extension-candidate-v1"
EXTENSION_EVIDENCE_MODEL = "core-capability-extension-evidence-v1"
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
    profiles = {p.capability_id: p for p in CORE_RUNTIME_CAPABILITY_PROFILES}
    for reference in entry.capabilities:
        profile = profiles.get(reference.capability_id)
        if profile is None or not profile.auto_eligible or any(
            reference.to_mapping()[key] != profile.to_mapping()[key]
            for key in reference.to_mapping()
        ):
            raise ValueError("review requires existing exact capability contracts")


def retained_denials(current):
    result = {}
    if current is not None:
        for envelope in (*current.envelopes, *current.revocation_sources):
            for revocation in envelope.revocations:
                result[revocation.release_identity] = revocation.to_mapping()
    return result


def require_extension_evidence(evidence, old, new):
    """Closed, hashable review manifest; no executable or free-text fields."""
    fields = {"model", "version", "previous_entry_sha256", "previous_capabilities",
              "added_capabilities", "review_artifacts_sha256"}
    if not isinstance(evidence, dict) or set(evidence) != fields:
        raise ValueError("extension review fields invalid")
    artifacts = evidence["review_artifacts_sha256"]
    if (not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 32
            or any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v)
                   for v in artifacts) or len(set(artifacts)) != len(artifacts)):
        raise ValueError("extension review artifact bindings invalid")
    old_refs, new_refs = old["capabilities"], new["capabilities"]
    if (evidence["model"] != EXTENSION_EVIDENCE_MODEL
            or evidence["version"] != old["version"]
            or evidence["previous_entry_sha256"] != hashlib.sha256(canonical_json(old)).hexdigest()
            or evidence["previous_capabilities"] != old_refs
            or evidence["added_capabilities"] != new_refs[len(old_refs):]):
        raise ValueError("extension review does not bind the exact transition")


def validate_transition(value, current, timestamp):
    """Recheck preparation invariants at signing, including operation labels.

    The placeholder signature is used only for the existing closed structural
    parser. It is never verified, retained or emitted as signed authority.
    """
    unsigned = value["envelope"]
    if not isinstance(unsigned, dict) or "signature" in unsigned:
        raise ValueError("unsigned envelope fields invalid")
    parsed = CoreRegistryEnvelope.from_mapping({
        **unsigned, "signature": base64.b64encode(bytes(64)).decode("ascii")})
    tip = None if current is None else current.accepted
    before = {} if tip is None else {e.version: e.to_mapping() for e in tip.entries}
    after = {e.version: e.to_mapping() for e in parsed.entries}
    old_denials = retained_denials(current)
    new_denials = {r.release_identity: r.to_mapping() for r in parsed.revocations}
    review_hash = value["review_evidence_sha256"]
    if not isinstance(review_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", review_hash):
        raise ValueError("review evidence digest invalid")
    changed = [v for v in after if before.get(v) != after[v]]
    removed = set(before) - set(after)
    operation = value["operation"]
    if operation == "revoke":
        # Older writers can withdraw unknown profiles, never replace positives.
        if changed or len(removed) > 1 or not set(old_denials) <= set(new_denials):
            raise ValueError("withdrawal cannot add or alter positive authority or lose denials")
        denial_changes = {v for v in new_denials if old_denials.get(v) != new_denials[v]}
        if len(denial_changes) > 1 or any(
                (CORE_IDENTITY, v) not in new_denials for v in removed):
            raise ValueError("withdrawal must bind one denied release")
        if removed and denial_changes and {v[1] for v in denial_changes} != removed:
            raise ValueError("withdrawal target mismatch")
        return
    if operation not in {"add", "extend-capabilities"} or new_denials != old_denials:
        raise ValueError("positive transition cannot alter retained denials")
    if removed or len(changed) > 1:
        raise ValueError("positive transition must preserve all sibling entries")
    for entry in parsed.entries:
        require_known_contracts(entry)
        if entry.release_identity in old_denials:
            raise ValueError("revoked release cannot be re-added")
    if not any(e["evidence_sha256"] == review_hash for e in after.values()):
        raise ValueError("review evidence is not bound to an entry")
    if changed and after[changed[0]]["evidence_sha256"] != review_hash:
        raise ValueError("review evidence does not bind the changed entry")
    if operation == "add":
        if changed and changed[0] in before:
            old, new = before[changed[0]], after[changed[0]]
            if any(old[k] != new[k] for k in old if k != "evidence_sha256"):
                raise ValueError("renewal cannot replace release identity or contracts")
        return
    if (tip is None or not parse_utc_timestamp(tip.generated_at) <= timestamp
            < parse_utc_timestamp(tip.expires_at)):
        raise ValueError("extension requires a current authenticated predecessor")
    if set(before) != set(after) or len(changed) != 1:
        raise ValueError("extension requires exactly one existing changed entry")
    old, new = before[changed[0]], after[changed[0]]
    if any(old[k] != new[k] for k in old if k not in {"capabilities", "evidence_sha256"}):
        raise ValueError("extension cannot change release or probe identity")
    old_refs, new_refs = old["capabilities"], new["capabilities"]
    if len(new_refs) <= len(old_refs) or new_refs[:len(old_refs)] != old_refs:
        raise ValueError("extension must append to the exact original references")
    if review_hash == old["evidence_sha256"]:
        raise ValueError("extension requires new review evidence")
    evidence = value["extension_review"]
    require_extension_evidence(evidence, old, new)
    if hashlib.sha256(canonical_json(evidence)).hexdigest() != review_hash:
        raise ValueError("extension review hash mismatch")


def prepare_candidate(*, entry, evidence: bytes, previous: bytes | None,
                      public_key, operation="add", reason=None, now=None):
    """Produce unsigned review material; successful probes alone cannot sign it."""
    timestamp = now or datetime.now(timezone.utc)
    parsed = CoreReleaseEntry.from_mapping(entry)
    if not 1 <= len(evidence) <= MAX_EVIDENCE_BYTES:
        raise ValueError("review evidence exceeds bound")
    if hashlib.sha256(evidence).hexdigest() != parsed.evidence_sha256:
        raise ValueError("review evidence hash mismatch")
    current = parse_journal(previous, public_key)
    tip = None if current is None else current.accepted
    entries = [] if tip is None else [e.to_mapping() for e in tip.entries]
    # Preserve every verified tombstone, including compacted denial sources.
    tombstones = retained_denials(current)
    if operation in {"add", "extend-capabilities"}:
        require_known_contracts(parsed)
        if parsed.release_identity in tombstones:
            raise ValueError("revoked release cannot be re-added")
        old = next((e for e in entries if e["version"] == parsed.version), None)
        if operation == "add" and old is not None and any(
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
    value = {
        "model": EXTENSION_MODEL if operation == "extend-capabilities" else MODEL,
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
    if operation == "extend-capabilities":
        value["extension_review"] = strict_json(evidence)
        if canonical_json(value["extension_review"]) != evidence:
            raise ValueError("extension evidence must use canonical JSON bytes")
    validate_transition(value, current, timestamp)
    return value


def signed(unsigned, key):
    return {**unsigned, "signature": base64.b64encode(
        key.sign(canonical_json(unsigned))).decode("ascii")}


def sign_candidate(candidate: bytes, *, expected_sha256: str,
                   previous: bytes | None, key, now=None) -> bytes:
    """Sign exactly reviewed bytes, fenced to the current authenticated journal."""
    if not 1 <= len(candidate) <= MAX_CACHE_BYTES:
        raise ValueError("candidate exceeds bound")
    if hashlib.sha256(candidate).hexdigest() != expected_sha256:
        raise ValueError("reviewed candidate hash mismatch")
    value = strict_json(candidate)
    if not isinstance(value, dict):
        raise ValueError("candidate fields invalid")
    extension = value.get("operation") == "extend-capabilities"
    fields = {"model", "operation", "public_key_sha256",
              "previous_journal_sha256", "review_evidence_sha256", "envelope"}
    if extension:
        fields.add("extension_review")
    if set(value) != fields:
        raise ValueError("candidate fields invalid")
    if (value["model"] != (EXTENSION_MODEL if extension else MODEL)
            or value["operation"] not in {"add", "revoke", "extend-capabilities"}):
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
    timestamp = now or datetime.now(timezone.utc)
    validate_transition(value, current, timestamp)
    envelope = signed(value["envelope"], key)
    # Validate closed schemas, chain, timestamps and retained denials before output.
    previous_envelopes = () if current is None else current.envelopes
    envelopes = [e.to_mapping() for e in previous_envelopes] + [envelope]
    removed, envelopes = envelopes[:-MAX_CHAIN], envelopes[-MAX_CHAIN:]
    # These envelopes were authenticated when parsing the previous journal.
    # Keep coverage of every denial using the runtime's existing retention
    # policy; distinct signatures of the same tombstone add no new coverage.
    sources = [e.to_mapping() for e in SignedReleaseRegistry._minimal_revocation_sources(
        (() if current is None else current.revocation_sources)
        + previous_envelopes[:len(removed)]
    )]
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
    generated = parse_utc_timestamp(result.accepted.generated_at)
    expires = parse_utc_timestamp(result.accepted.expires_at)
    if not generated <= timestamp < expires or expires - generated > timedelta(days=90):
        raise ValueError("candidate is not current at signing")
    if value["operation"] == "revoke":
        # Denial never requires positive applicability. An older writer can
        # withdraw a newer-profile record, but cannot add or alter positives.
        prior_entries = {} if tip is None else {
            e.release_identity: e.to_mapping() for e in tip.entries
        }
        for entry in result.accepted.entries:
            if prior_entries.get(entry.release_identity) != entry.to_mapping():
                raise ValueError("withdrawal cannot add or alter positive authority")
    else:
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
    parser.add_argument("operation", choices=("add", "extend-capabilities", "revoke", "sign"))
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
