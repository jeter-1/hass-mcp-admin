"""HAMCP-161 Part 0 builder: rehearse the owner signing sequence with an EPHEMERAL key.

Run with the beta.4 (a3aa4d1) checkout's scripts/prepare_core_release_registry.py,
i.e. exactly the utility the owner will use. The production signing key is never
read, requested or needed: a fresh in-memory Ed25519 key re-signs the unchanged
unsigned envelopes of the current production journal (content digests, sequence
numbers and the predecessor chain are therefore identical), then every planned
operation is prepared and signed with prepare_candidate/sign_candidate.

Output: <out>/<variant>/journal-step<N>.json, <out>/public-key.txt and
<out>/manifest.json. The ephemeral private key is discarded at exit.
"""

from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--repo", type=Path, required=True, help="beta.4 source checkout")
parser.add_argument("--production-journal", type=Path, required=True)
parser.add_argument("--production-public-key", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--inputs", type=Path, default=None,
                    help="optional dir with the real entry/evidence files (else synthetic)")
args = parser.parse_args()

sys.path[:0] = [str(args.repo / "scripts"), str(args.repo / "hass_mcp_engineering_beta")]
import prepare_core_release_registry as prepare  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey, Ed25519PublicKey)
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_RUNTIME_CAPABILITY_PROFILES)
from ha_mcp_engineering.signed_registry import canonical_json  # noqa: E402

REFERENCE_KEYS = ("capability_id", "profile_id", "profile_version", "adapter_id",
                  "contract_fingerprint")
ENTITY = next(p for p in CORE_RUNTIME_CAPABILITY_PROFILES
              if p.capability_id == "core.entity_registry_metadata_operation")
ENTITY_REF = {k: ENTITY.to_mapping()[k] for k in REFERENCE_KEYS}
REASON = ("outside the CI support window; retired by owner direction 2026-10-12 "
          "(HAMCP-161)")
T0 = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)

production_raw = args.production_journal.read_bytes()
production_public = Ed25519PublicKey.from_public_bytes(base64.b64decode(
    args.production_public_key.read_bytes().strip(), validate=True))
production = prepare.parse_journal(production_raw, production_public)

key = Ed25519PrivateKey.generate()
public = key.public_key()


def twin_journal() -> bytes:
    """Same unsigned envelopes and chain, re-signed with the ephemeral key."""
    value = json.loads(production_raw)
    envelopes = [prepare.signed({k: v for k, v in e.items() if k != "signature"}, key)
                 for e in value["envelopes"]]
    assert value["revocation_sources"] == []
    journal = prepare.signed({
        "schema_version": 1, "registry_id": value["registry_id"], "key_id": value["key_id"],
        "checkpoint_sequence": value["checkpoint_sequence"],
        "checkpoint_previous_registry_sha256": value["checkpoint_previous_registry_sha256"],
        "envelopes": envelopes, "revocation_sources": [],
    }, key)
    raw = canonical_json(journal) + b"\n"
    twin = prepare.parse_journal(raw, public)
    assert twin.accepted.content_digest == production.accepted.content_digest
    assert twin.accepted.sequence == production.accepted.sequence
    assert [e.to_mapping() for e in twin.accepted.entries] == [
        e.to_mapping() for e in production.accepted.entries]
    return raw


def entries(raw: bytes) -> dict[str, dict]:
    return {e.version: e.to_mapping() for e in prepare.parse_journal(raw, public).accepted.entries}


def load_input(name: str):
    if args.inputs is None or not (args.inputs / name).exists():
        return None
    return (args.inputs / name).read_bytes()


def synthetic(label: str) -> bytes:
    return canonical_json({"model": "hamcp161-harness-synthetic-evidence", "label": label})


def step(raw: bytes, *, operation: str, entry: dict, evidence: bytes, now: datetime,
         reason: str | None = None) -> tuple[bytes, bytes]:
    candidate = canonical_json(prepare.prepare_candidate(
        entry=entry, evidence=evidence, previous=raw, public_key=public,
        operation=operation, reason=reason, now=now)) + b"\n"
    signed = prepare.sign_candidate(
        candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(),
        previous=raw, key=key, now=now)
    return candidate, signed


def entry_101(base: dict[str, dict], *, with_entity: bool) -> tuple[dict, bytes]:
    real_entry, real_evidence = load_input("entry-2026.10.1.json"), load_input("evidence-2026.10.1.json")
    if real_entry is not None and not with_entity:
        return json.loads(real_entry), real_evidence
    evidence = real_evidence or synthetic("add-2026.10.1" + ("+entity" if with_entity else ""))
    if with_entity:
        evidence = synthetic("add-2026.10.1+entity")
    fixture = json.loads((args.repo / "tests/fixtures/core_2026_10_1_lane_provenance.json").read_text())
    october = base["2026.10.0"]
    value = {
        "entry_id": "ha-core-2026.10.1-reviewed-v1", "server_name": october["server_name"],
        "version": "2026.10.1",
        **{k: fixture[k] for k in ("source_commit", "source_tree", "source_archive_sha256",
                                   "image_index_digest", "architecture_manifests")},
        "evidence_sha256": hashlib.sha256(evidence).hexdigest(),
        "probe_profile_id": october["probe_profile_id"],
        "probe_profile_sha256": october["probe_profile_sha256"],
        "capabilities": deepcopy(october["capabilities"]) + ([ENTITY_REF] if with_entity else []),
    }
    return value, evidence


def revoke_inputs(base: dict[str, dict], version: str) -> tuple[dict, bytes]:
    evidence = load_input(f"evidence-revoke-{version}.json") or synthetic("revoke-" + version)
    real_entry = load_input(f"entry-revoke-{version}.json")
    entry = json.loads(real_entry) if real_entry else {
        **base[version], "evidence_sha256": hashlib.sha256(evidence).hexdigest()}
    return entry, evidence


def extend_inputs(base: dict[str, dict], version: str) -> tuple[dict, bytes]:
    old = base[version]
    new = {**deepcopy(old), "capabilities": deepcopy(old["capabilities"]) + [ENTITY_REF]}
    evidence = canonical_json({
        "model": prepare.EXTENSION_EVIDENCE_MODEL, "version": version,
        "previous_entry_sha256": hashlib.sha256(canonical_json(old)).hexdigest(),
        "previous_capabilities": old["capabilities"],
        "added_capabilities": [ENTITY_REF],
        "review_artifacts_sha256": [hashlib.sha256(synthetic("entity-" + version)).hexdigest()],
    })
    new["evidence_sha256"] = hashlib.sha256(evidence).hexdigest()
    return new, evidence


VARIANTS = {
    # (a) and (b) only: the path that applies when (c) is stopped.
    "ab": ["add-2026.10.1", "revoke-2026.9.2", "revoke-2026.9.3"],
    # Full (a)-(c) with the owner's planned order and 2026.10.1 extended last.
    "abc-extend": ["add-2026.10.1", "revoke-2026.9.2", "revoke-2026.9.3",
                   "extend-2026.10.0", "extend-2026.9.4", "extend-2026.10.1"],
    # Full (a)-(c) with the entity reference inside the 2026.10.1 add.
    "abc-in-add": ["add-2026.10.1+entity", "revoke-2026.9.2", "revoke-2026.9.3",
                   "extend-2026.10.0", "extend-2026.9.4"],
}

args.out.mkdir(parents=True, exist_ok=False)
(args.out / "public-key.txt").write_text(
    base64.b64encode(public.public_bytes_raw()).decode() + "\n")
twin = twin_journal()
(args.out / "journal-twin-seq6.json").write_bytes(twin)
manifest = {"ephemeral_public_key_sha256": hashlib.sha256(public.public_bytes_raw()).hexdigest(),
            "production_journal_sha256": hashlib.sha256(production_raw).hexdigest(),
            "production_tip_digest": production.accepted.content_digest,
            "entity_reference": ENTITY_REF, "variants": {}}
for variant, operations in VARIANTS.items():
    directory = args.out / variant
    directory.mkdir()
    raw, records = twin, []
    for index, name in enumerate(operations, start=1):
        base = entries(raw)
        now = T0 + timedelta(minutes=index)
        operation, _, version = name.partition("-")
        if name.startswith("add-"):
            entry, evidence = entry_101(base, with_entity=name.endswith("+entity"))
            candidate, raw = step(raw, operation="add", entry=entry, evidence=evidence, now=now)
        elif operation == "revoke":
            entry, evidence = revoke_inputs(base, version)
            candidate, raw = step(raw, operation="revoke", entry=entry, evidence=evidence,
                                  now=now, reason=REASON)
        else:
            entry, evidence = extend_inputs(base, version)
            candidate, raw = step(raw, operation="extend-capabilities", entry=entry,
                                  evidence=evidence, now=now)
        (directory / f"candidate-step{index}.json").write_bytes(candidate)
        (directory / f"journal-step{index}.json").write_bytes(raw)
        tip = prepare.parse_journal(raw, public).accepted
        records.append({"step": index, "operation": name,
                        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
                        "journal_sha256": hashlib.sha256(raw).hexdigest(),
                        "sequence": tip.sequence,
                        "entries": {e.version: len(e.capabilities) for e in tip.entries},
                        "revoked": sorted(r.version for r in tip.revocations)})
    manifest["variants"][variant] = records
(args.out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
print(json.dumps(manifest, indent=1, sort_keys=True))
