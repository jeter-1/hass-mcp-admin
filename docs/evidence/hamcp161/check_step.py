"""HAMCP-161 owner signing runbook check. PUBLIC key only; never reads a private key.

  candidate STEP: before signing, prove the candidate is exactly the reviewed
      operation fenced to the journal it was prepared from, and print the SHA256
      to pass to `sign --expected-candidate-sha256`.
  signed STEP: after signing, verify the new journal with the public key and
      prove it is the previous journal plus exactly the reviewed candidate.

Steps: 1 = add 2026.10.1, 2 = revoke 2026.9.2, 3 = revoke 2026.9.3.
Exit 0 and a final "OK" line only when every check passes.
"""

from __future__ import annotations

import argparse
import base64
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "hass_mcp_engineering_beta")]
import prepare_core_release_registry as prepare  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: E402
from ha_mcp_engineering.signed_registry import canonical_json  # noqa: E402
from ha_mcp_engineering.signed_registry.models import parse_utc_timestamp  # noqa: E402

HERE = Path(__file__).resolve().parent
PUBLIC_KEY_SHA256 = "7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac"
REASON = ("outside the CI support window; retired by owner direction 2026-10-12 "
          "(HAMCP-161)")
STEPS = {
    1: ("add", "2026.10.1", "inputs/entry-2026.10.1.json", "inputs/evidence-2026.10.1.json"),
    2: ("revoke", "2026.9.2", "inputs/entry-revoke-2026.9.2.json", "inputs/evidence-revoke-2026.9.2.json"),
    3: ("revoke", "2026.9.3", "inputs/entry-revoke-2026.9.3.json", "inputs/evidence-revoke-2026.9.3.json"),
}
EXPECTED_SEQUENCE = {1: 7, 2: 8, 3: 9}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit("FAILED: " + message)


parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=("candidate", "signed"))
parser.add_argument("step", type=int, choices=sorted(STEPS))
parser.add_argument("--previous", type=Path, required=True,
                    help="signed journal the candidate was prepared from")
parser.add_argument("--candidate", type=Path, required=True)
parser.add_argument("--signed", type=Path, help="signed output (mode signed)")
parser.add_argument("--public-key", type=Path, default=HERE / "core-public-key.txt")
# Only for replaying the ephemeral-key rehearsal (harness/); never used when signing.
parser.add_argument("--rehearsal-key-sha256", default=PUBLIC_KEY_SHA256)
args = parser.parse_args()

key_raw = base64.b64decode(args.public_key.read_bytes().strip(), validate=True)
require(sha(key_raw) == args.rehearsal_key_sha256, "public key fingerprint is not the reviewed Core key")
public = Ed25519PublicKey.from_public_bytes(key_raw)
operation, version, entry_path, evidence_path = STEPS[args.step]
entry = json.loads((HERE / entry_path).read_bytes())
evidence = (HERE / evidence_path).read_bytes()

previous_raw = args.previous.read_bytes()
previous = prepare.parse_journal(previous_raw, public).accepted
require(previous.sequence == EXPECTED_SEQUENCE[args.step] - 1,
        f"previous journal sequence {previous.sequence} is not the step's predecessor")
before = {e.version: e.to_mapping() for e in previous.entries}

candidate_raw = args.candidate.read_bytes()
candidate = json.loads(candidate_raw)
require(candidate_raw == canonical_json(candidate) + b"\n", "candidate is not canonical")
envelope = candidate["envelope"]
require(candidate["model"] == prepare.MODEL and candidate["operation"] == operation,
        "candidate operation differs")
require(candidate["public_key_sha256"] == sha(key_raw), "candidate names another key")
require(candidate["previous_journal_sha256"] == sha(previous_raw),
        "candidate was not prepared from this previous journal")
require(envelope["sequence"] == EXPECTED_SEQUENCE[args.step]
        and envelope["previous_registry_sha256"] == previous.content_digest,
        "candidate is not the direct successor")
require(candidate["review_evidence_sha256"] == sha(evidence) == entry["evidence_sha256"],
        "review evidence binding differs")
generated = parse_utc_timestamp(envelope["generated_at"])
require(parse_utc_timestamp(envelope["expires_at"]) - generated == timedelta(days=90),
        "expiry is not generated_at + 90 days")
after = {e["version"]: e for e in envelope["entries"]}
old_revocations = [r.to_mapping() for r in previous.revocations]
if operation == "add":
    require(version not in before, "version already present")
    require(after == {**before, version: entry}, "entries are not previous + the reviewed entry")
    require(envelope["revocations"] == old_revocations, "revocations changed")
else:
    require(before.get(version) == {**entry, "evidence_sha256": before[version]["evidence_sha256"]},
            "revocation entry is not the exact signed entry")
    require(after == {v: e for v, e in before.items() if v != version},
            "entries are not previous minus the revoked version")
    require(envelope["revocations"][:-1] == old_revocations
            and len(envelope["revocations"]) == len(old_revocations) + 1,
            "revocations are not previous + one tombstone")
    tombstone = envelope["revocations"][-1]
    require(tombstone == {"entry_id": entry["entry_id"], "server_name": entry["server_name"],
                          "version": version, "image_index_digest": entry["image_index_digest"],
                          "revoked_at": envelope["generated_at"], "reason": REASON},
            "tombstone differs")
print(f"Step {args.step} {operation} {version}: sequence {envelope['sequence']}, "
      f"entries {sorted(after)}, revoked {sorted(r['version'] for r in envelope['revocations'])}")
print("Candidate SHA256: " + sha(candidate_raw))

if args.mode == "signed":
    require(args.signed is not None, "--signed is required")
    signed_raw = args.signed.read_bytes()
    result = prepare.parse_journal(signed_raw, public)
    tip = result.accepted
    require(tip.sequence == envelope["sequence"], "signed tip sequence differs")
    require({e.version: e.to_mapping() for e in tip.entries} == after, "signed entries differ")
    require([r.to_mapping() for r in tip.revocations] == envelope["revocations"],
            "signed revocations differ")
    require(tip.generated_at == envelope["generated_at"] and tip.expires_at == envelope["expires_at"],
            "signed timestamps differ")
    require(tip.previous_registry_sha256 == previous.content_digest, "signed chain differs")
    require([e.to_mapping() for e in result.envelopes][:-1]
            == [e.to_mapping() for e in prepare.parse_journal(previous_raw, public).envelopes],
            "earlier signed envelopes changed")
    print(f"Signed journal verified with the public key: sequence {tip.sequence}, "
          f"digest {tip.content_digest}, SHA256 {sha(signed_raw)}")
print("OK")
