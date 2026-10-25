"""HAMCP-222 rehearsal builder: rehearse sequences 13 and 14 with an EPHEMERAL key.

Run with this checkout's scripts/prepare_core_release_registry.py (unchanged from
main and from v2.4.1-beta.36), the utility the owner uses. The production signing
key is never read, requested or needed: a fresh in-memory Ed25519 key re-signs
the unchanged unsigned envelopes of the production sequence-12 journal (content
digests, sequence numbers and the predecessor chain are identical), then the two
planned operations are prepared and signed with prepare_candidate/sign_candidate
using the REAL committed inputs (docs/evidence/hamcp222/inputs).

Step 1 is prepared at the committed candidate's generated_at, so its envelope and
extension review must equal the committed candidate-1 byte for byte (only the key
fingerprint and the predecessor file hash differ). Step 2 is prepared one hour
later, as the owner prepares it after signing step 1.

Variants:
  planned   extend 2026.10.1 (sequence 13) -> add 2026.10.3 (sequence 14)
Negative controls (each must be refused by the signing utility):
  stale     step 2 signed against the wrong predecessor (two forms)
  relabel   step 1 relabeled as an add; step 2 relabeled as an extension
  wrongkey  the journal read with another public key, and the reviewed
            candidate signed with another private key
  expected  a wrong expected candidate SHA256
  expired   step 1 prepared after the sequence-12 tip expired
  replay    2026.10.1 re-extended on the final journal
  renewal   2026.10.3 re-added with 22 references on the final journal
  unknown   2026.10.3 added with a reference to an unknown contract fingerprint
  re-add    revoked 2026.9.4 re-added on the final journal

Output: <out>/planned/{candidate,journal}-step<N>.json, <out>/public-key.txt,
<out>/journal-twin-seq12.json and <out>/manifest.json. The key is discarded at exit.
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
parser.add_argument("--repo", type=Path, required=True, help="this source checkout")
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()

REPO = args.repo.resolve()
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "hass_mcp_engineering_beta")]
import prepare_core_release_registry as prepare  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey, Ed25519PublicKey)
from ha_mcp_engineering.signed_registry import canonical_json  # noqa: E402

HERE = REPO / "docs/evidence/hamcp222"
INPUTS = HERE / "inputs"
COMMITTED_CANDIDATE = HERE / "candidates/candidate-1-extend-2026.10.1.json"
T1 = datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc)  # committed candidate-1 generated_at
T2 = T1 + timedelta(hours=1)
STEPS = (("extend-capabilities", "extend", "2026.10.1", T1),
         ("add", "add", "2026.10.3", T2))

production_raw = (REPO / "upstream-trust/ha-core-release-registry.json").read_bytes()
production_public = Ed25519PublicKey.from_public_bytes(base64.b64decode(
    (HERE / "core-public-key.txt").read_bytes().strip(), validate=True))
production = prepare.parse_journal(production_raw, production_public)
assert production.accepted.sequence == 12

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
    entry = json.loads((INPUTS / f"entry-{kind}-{version}.json").read_bytes())
    evidence = (INPUTS / f"evidence-{kind}-{version}.json").read_bytes()
    assert entry["evidence_sha256"] == sha(evidence)
    return entry, evidence


def prepare_step(raw: bytes, index: int, *, public_key=None, now=None, entry=None,
                 operation=None) -> bytes:
    default_operation, kind, version, at = STEPS[index - 1]
    reviewed, evidence = inputs(kind, version)
    return canonical_json(prepare.prepare_candidate(
        entry=entry or reviewed, evidence=evidence, previous=raw, public_key=public_key or public,
        operation=operation or default_operation, now=now or at)) + b"\n"


def sign(candidate: bytes, raw: bytes, index: int) -> bytes:
    return prepare.sign_candidate(candidate, expected_sha256=sha(candidate), previous=raw,
                                  key=key, now=STEPS[index - 1][3] + timedelta(seconds=30))


def refused(label: str, action) -> dict:
    try:
        action()
    except Exception as error:  # noqa: BLE001 - any refusal shape is recorded
        return {"control": label, "refused": True, "error": f"{type(error).__name__}: {error}"}
    return {"control": label, "refused": False}


def relabel(candidate: bytes, operation: str) -> bytes:
    value = json.loads(candidate)
    value["operation"] = operation
    return canonical_json(value) + b"\n"


args.out.mkdir(parents=True, exist_ok=False)
(args.out / "public-key.txt").write_text(base64.b64encode(public.public_bytes_raw()).decode() + "\n")
twin = twin_journal()
(args.out / "journal-twin-seq12.json").write_bytes(twin)
manifest = {"ephemeral_public_key_sha256": sha(public.public_bytes_raw()),
            "production_journal_sha256": sha(production_raw),
            "production_tip_digest": production.accepted.content_digest,
            "inputs_sha256": {p.name: sha(p.read_bytes()) for p in sorted(INPUTS.iterdir())},
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
        "step": index, "operation": " ".join(STEPS[index - 1][1:3]),
        "candidate_sha256": sha(candidate), "journal_sha256": sha(raw), "sequence": tip.sequence,
        "generated_at": tip.generated_at, "expires_at": tip.expires_at,
        "entries": {e.version: len(e.capabilities) for e in tip.entries},
        "revoked": sorted(r.version for r in tip.revocations)})

# The rehearsed step 1 is the committed candidate apart from key and predecessor-file binding.
committed = json.loads(COMMITTED_CANDIDATE.read_bytes())
rehearsed = json.loads((directory / "candidate-step1.json").read_bytes())
binding = {"public_key_sha256", "previous_journal_sha256"}
assert {k: v for k, v in committed.items() if k not in binding} \
    == {k: v for k, v in rehearsed.items() if k not in binding}
assert committed["previous_journal_sha256"] == sha(production_raw)
manifest["committed_candidate_1_equal_apart_from_key_and_predecessor_file"] = True

step1 = (directory / "candidate-step1.json").read_bytes()
step2 = (directory / "candidate-step2.json").read_bytes()
other = Ed25519PrivateKey.generate().public_key()
added = inputs("add", "2026.10.3")[0]
expired_at = datetime.fromisoformat(production.accepted.expires_at.replace("Z", "+00:00"))
unknown = json.loads(json.dumps(added))
unknown["capabilities"][-1]["contract_fingerprint"] = "sha256:" + "0" * 64
manifest["negative_controls"] = [
    refused("stale: step 2 candidate signed against sequence 12",
            lambda: sign(step2, twin, 2)),
    refused("stale: step 2 prepared from sequence 12 then signed on sequence 13",
            lambda: sign(prepare_step(twin, 2), journals[1], 2)),
    refused("relabel: extension candidate relabeled as add", lambda: sign(relabel(step1, "add"), twin, 1)),
    refused("relabel: add candidate relabeled as extension",
            lambda: sign(relabel(step2, "extend-capabilities"), journals[1], 2)),
    refused("wrongkey: journal read with another public key",
            lambda: prepare_step(twin, 1, public_key=other)),
    refused("wrongkey: reviewed candidate signed with another private key",
            lambda: prepare.sign_candidate(step1, expected_sha256=sha(step1), previous=twin,
                                           key=Ed25519PrivateKey.generate(),
                                           now=T1 + timedelta(seconds=30))),
    refused("expected-sha: wrong expected candidate sha",
            lambda: prepare.sign_candidate(step1, expected_sha256="0" * 64, previous=twin, key=key,
                                           now=T1 + timedelta(seconds=30))),
    refused("expired: step 1 prepared after the sequence-12 tip expired",
            lambda: prepare_step(twin, 1, now=expired_at + timedelta(seconds=1))),
    refused("replay: re-extend 2026.10.1 on the final journal",
            lambda: prepare_step(journals[2], 1, now=T2 + timedelta(minutes=5))),
    refused("renewal: re-add 2026.10.3 with 22 references on the final journal",
            lambda: prepare_step(journals[2], 2, now=T2 + timedelta(minutes=5),
                                 entry={**added, "capabilities": added["capabilities"][:22]})),
    refused("unknown: add 2026.10.3 with an unknown contract fingerprint",
            lambda: prepare_step(journals[1], 2, entry=unknown)),
    refused("re-add: add revoked 2026.9.4", lambda: prepare.prepare_candidate(
        entry=json.loads((REPO / "docs/evidence/hamcp182/inputs/entry-revoke-2026.9.4.json").read_bytes()),
        evidence=(REPO / "docs/evidence/hamcp182/inputs/evidence-revoke-2026.9.4.json").read_bytes(),
        previous=journals[2], public_key=public, operation="add", now=T2 + timedelta(minutes=5))),
]
assert all(c["refused"] for c in manifest["negative_controls"]), manifest["negative_controls"]
(args.out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
print(json.dumps(manifest, indent=1, sort_keys=True))
