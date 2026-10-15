"""HAMCP-182 Part 0 builder: rehearse the owner signing sequence with an EPHEMERAL key.

Run with the 690a00b checkout's scripts/prepare_core_release_registry.py, the
utility the owner will use. The production signing key is never read, requested
or needed: a fresh in-memory Ed25519 key re-signs the unchanged unsigned
envelopes of the production sequence-9 journal (content digests, sequence
numbers and the predecessor chain are identical), then the three planned
operations are prepared and signed with prepare_candidate/sign_candidate using
the REAL committed inputs (docs/evidence/hamcp182/inputs).

Variants:
  planned   extend 2026.10.1 -> extend 2026.10.0 -> revoke 2026.9.4 (the runbook order)
Negative controls (each must be refused by the signing utility):
  stale     step 2 candidates signed against the wrong predecessor (two forms)
  relabel   step 1's extension candidate relabeled as an add
  wrongkey  the journal read with another public key, and the reviewed
            candidate signed with another private key
  expected  a wrong expected candidate SHA256
  replay    2026.10.1 re-extended on the final journal
  re-add    revoked 2026.9.4 re-added on the final journal

Output: <out>/planned/{candidate,journal}-step<N>.json, <out>/public-key.txt,
<out>/journal-twin-seq9.json and <out>/manifest.json. The key is discarded at exit.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--repo", type=Path, required=True, help="690a00b source checkout")
parser.add_argument("--production-journal", type=Path, required=True)
parser.add_argument("--production-public-key", type=Path, required=True)
parser.add_argument("--inputs", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()

sys.path[:0] = [str(args.repo / "scripts"), str(args.repo / "hass_mcp_engineering_beta")]
import prepare_core_release_registry as prepare  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey, Ed25519PublicKey)
from ha_mcp_engineering.signed_registry import canonical_json  # noqa: E402

REASON = "outside the CI support window; retired by owner direction (HAMCP-182)"
T0 = datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc)
STEPS = (("extend-capabilities", "extend", "2026.10.1"),
         ("extend-capabilities", "extend", "2026.10.0"),
         ("revoke", "revoke", "2026.9.4"))

production_raw = args.production_journal.read_bytes()
production_public = Ed25519PublicKey.from_public_bytes(base64.b64decode(
    args.production_public_key.read_bytes().strip(), validate=True))
production = prepare.parse_journal(production_raw, production_public)
assert production.accepted.sequence == 9

key = Ed25519PrivateKey.generate()
public = key.public_key()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def twin_journal() -> bytes:
    """Same unsigned envelopes, revocation sources and chain, re-signed with the ephemeral key."""
    value = json.loads(production_raw)
    resign = lambda e: prepare.signed({k: v for k, v in e.items() if k != "signature"}, key)  # noqa: E731
    journal = prepare.signed({
        **{k: v for k, v in value.items() if k not in {"signature", "envelopes", "revocation_sources"}},
        "envelopes": [resign(e) for e in value["envelopes"]],
        "revocation_sources": [resign(e) for e in value["revocation_sources"]],
    }, key)
    raw = canonical_json(journal) + b"\n"
    twin = prepare.parse_journal(raw, public)
    assert twin.accepted.content_digest == production.accepted.content_digest
    assert twin.accepted.sequence == production.accepted.sequence
    assert [e.to_mapping() for e in twin.accepted.entries] == [
        e.to_mapping() for e in production.accepted.entries]
    assert [r.to_mapping() for r in twin.accepted.revocations] == [
        r.to_mapping() for r in production.accepted.revocations]
    return raw


def inputs(kind: str, version: str) -> tuple[dict, bytes]:
    entry = json.loads((args.inputs / f"entry-{kind}-{version}.json").read_bytes())
    evidence = (args.inputs / f"evidence-{kind}-{version}.json").read_bytes()
    assert entry["evidence_sha256"] == sha(evidence)
    return entry, evidence


def prepare_step(raw: bytes, index: int, *, public_key=None, minutes=None) -> bytes:
    operation, kind, version = STEPS[index - 1]
    entry, evidence = inputs(kind, version)
    return canonical_json(prepare.prepare_candidate(
        entry=entry, evidence=evidence, previous=raw, public_key=public_key or public,
        operation=operation, reason=REASON if operation == "revoke" else None,
        now=T0 + timedelta(minutes=index if minutes is None else minutes))) + b"\n"


def sign(candidate: bytes, raw: bytes, index: int) -> bytes:
    return prepare.sign_candidate(candidate, expected_sha256=sha(candidate), previous=raw,
                                  key=key, now=T0 + timedelta(minutes=index, seconds=30))


def refused(label: str, action) -> dict:
    try:
        action()
    except Exception as error:  # noqa: BLE001 - any refusal shape is recorded
        return {"control": label, "refused": True, "error": f"{type(error).__name__}: {error}"}
    return {"control": label, "refused": False}


args.out.mkdir(parents=True, exist_ok=False)
(args.out / "public-key.txt").write_text(base64.b64encode(public.public_bytes_raw()).decode() + "\n")
twin = twin_journal()
(args.out / "journal-twin-seq9.json").write_bytes(twin)
manifest = {"ephemeral_public_key_sha256": sha(public.public_bytes_raw()),
            "production_journal_sha256": sha(production_raw),
            "production_tip_digest": production.accepted.content_digest,
            "inputs_sha256": {p.name: sha(p.read_bytes()) for p in sorted(args.inputs.iterdir())},
            "planned": [], "negative_controls": []}
directory = args.out / "planned"
directory.mkdir()
raw, journals = twin, [twin]
for index in range(1, len(STEPS) + 1):
    candidate = prepare_step(raw, index)
    raw = sign(candidate, raw, index)
    journals.append(raw)
    (directory / f"candidate-step{index}.json").write_bytes(candidate)
    (directory / f"journal-step{index}.json").write_bytes(raw)
    tip = prepare.parse_journal(raw, public).accepted
    manifest["planned"].append({
        "step": index, "operation": " ".join(STEPS[index - 1][1:]),
        "candidate_sha256": sha(candidate), "journal_sha256": sha(raw), "sequence": tip.sequence,
        "entries": {e.version: len(e.capabilities) for e in tip.entries},
        "revoked": sorted(r.version for r in tip.revocations)})

step1 = prepare_step(twin, 1)
step2_from_step1 = prepare_step(journals[1], 2)
relabeled = json.loads(step1)
relabeled["operation"] = "add"
relabeled = canonical_json(relabeled) + b"\n"
other = Ed25519PrivateKey.generate().public_key()
manifest["negative_controls"] = [
    refused("stale: step 2 candidate signed against sequence 9",
            lambda: sign(step2_from_step1, twin, 2)),
    refused("stale: step 2 prepared from sequence 9 (2026.10.1 not yet extended) then signed on step 1",
            lambda: sign(prepare_step(twin, 2), journals[1], 2)),
    refused("relabel: extension candidate relabeled as add", lambda: sign(relabeled, twin, 1)),
    refused("wrongkey: journal read with another public key",
            lambda: prepare_step(twin, 1, public_key=other)),
    refused("wrongkey: reviewed candidate signed with another private key",
            lambda: prepare.sign_candidate(step1, expected_sha256=sha(step1), previous=twin,
                                           key=Ed25519PrivateKey.generate(),
                                           now=T0 + timedelta(minutes=1, seconds=30))),
    refused("expected-sha: wrong expected candidate sha",
            lambda: prepare.sign_candidate(step1, expected_sha256="0" * 64, previous=twin, key=key,
                                           now=T0 + timedelta(minutes=1, seconds=30))),
    refused("replay: re-extend 2026.10.1 on the final journal",
            lambda: prepare_step(journals[3], 1, minutes=9)),
    refused("re-add: extend revoked 2026.9.4", lambda: prepare.prepare_candidate(
        entry={**json.loads((args.inputs / "entry-revoke-2026.9.4.json").read_bytes())},
        evidence=(args.inputs / "evidence-revoke-2026.9.4.json").read_bytes(),
        previous=journals[3], public_key=public, operation="add", now=T0 + timedelta(minutes=9))),
]
assert all(c["refused"] for c in manifest["negative_controls"]), manifest["negative_controls"]
(args.out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
print(json.dumps(manifest, indent=1, sort_keys=True))
