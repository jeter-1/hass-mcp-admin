"""HAMCP-222 owner signing runbook check. PUBLIC key only; never reads a private key.

  candidate STEP: before signing, prove the candidate is exactly the reviewed
      operation fenced to the journal it was prepared from, and print the SHA256
      to pass to the signer's `--expected-candidate-sha256`.
  signed STEP: after signing, verify the new journal with the public key and
      prove it is the previous journal plus exactly the reviewed candidate.

Steps: 1 = extend 2026.10.1 from 22 to 26 references (sequence 13),
       2 = add 2026.10.3 with the same 26 references (sequence 14).
The committed inputs are pinned by SHA256 below, so pinning this file's hash
pins them too. Exit 0 and a final "OK" line only when every check passes.
"""

from __future__ import annotations

import argparse
import base64
from datetime import timedelta
import hashlib
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
# The four sequence-13 contracts, in compiled order (profiles 22-25).
SEQ13_REFS = [
    {"adapter_id": "compiled-core-entity-registry-availability-operation-v1",
     "capability_id": "core.entity_registry_availability_operation",
     "contract_fingerprint": "sha256:ae292fbc16ad82c8d8347b7e2eb1c3a7e8c7644d95783f5bf2ae9f9cc63a76b2",
     "profile_id": "core_entity_registry_availability_operation_v1", "profile_version": 1},
    {"adapter_id": "compiled-core-entity-assistant-exposure-operation-v1",
     "capability_id": "core.entity_assistant_exposure_operation",
     "contract_fingerprint": "sha256:474c8ccaaae83c12f56a2848995ff089097936568a2ad5dc8d25ce855d51dbbd",
     "profile_id": "core_entity_assistant_exposure_operation_v1", "profile_version": 1},
    {"adapter_id": "compiled-core-label-registry-operation-v1",
     "capability_id": "core.label_registry_operation",
     "contract_fingerprint": "sha256:b2f0dd11ff243aa2559c27738d962785e37455d37b836cb5a6997aeabb26813c",
     "profile_id": "core_label_registry_operation_v1", "profile_version": 1},
    {"adapter_id": "compiled-core-configuration-delete-operation-v1",
     "capability_id": "core.configuration_delete_operation",
     "contract_fingerprint": "sha256:5688af5d141b97ca33eadf251ecfbb8f3b1f833024b0ee1acea4973f558f887d",
     "profile_id": "core_configuration_delete_operation_v1", "profile_version": 1},
]
INPUT_SHA256 = {
    "inputs/entry-extend-2026.10.1.json": "db8e9c77ae99b43596ae416da8e88ab0543902567a5bdf3b286198783da84de9",
    "inputs/evidence-extend-2026.10.1.json": "367a9303e10463029ceaaa1620769412e526a5a8cbf1cc803c074f87f1009cf4",
    "inputs/entry-add-2026.10.3.json": "98cdad1001cc9312a5bf82ac1d6ced3e22da4ae8369c2b15772fd6a2172ae31d",
    "inputs/evidence-add-2026.10.3.json": "e245e6347b5327f746aa15085aa690d7a14add3f1fa35d0980ba86d8837a9f01",
}
STEPS = {
    1: ("extend-capabilities", "2026.10.1", "inputs/entry-extend-2026.10.1.json",
        "inputs/evidence-extend-2026.10.1.json"),
    2: ("add", "2026.10.3", "inputs/entry-add-2026.10.3.json",
        "inputs/evidence-add-2026.10.3.json"),
}
EXPECTED_SEQUENCE = {1: 13, 2: 14}
EXPECTED_REFERENCES = {1: {"2026.10.0": 22, "2026.10.1": 26},
                       2: {"2026.10.0": 22, "2026.10.1": 26, "2026.10.3": 26}}
CANDIDATE_FIELDS = {"model", "operation", "public_key_sha256", "previous_journal_sha256",
                    "review_evidence_sha256", "envelope"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit("FAILED: " + message)


def strict(raw: bytes, what: str):
    # The shipped loader the signer uses: duplicate JSON members and non-finite values refuse.
    try:
        return prepare.strict_json(raw)
    except ValueError as error:
        raise SystemExit(f"FAILED: {what} is not strict JSON ({error})") from None


def unique(values: list, message: str) -> None:
    require(len(values) == len(set(values)), message)


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
for path in (entry_path, evidence_path):
    require(sha((HERE / path).read_bytes()) == INPUT_SHA256[path], f"{path} is not the reviewed input")
entry = strict((HERE / entry_path).read_bytes(), entry_path)
evidence = (HERE / evidence_path).read_bytes()

previous_raw = args.previous.read_bytes()
current = prepare.parse_journal(previous_raw, public)
previous = current.accepted
require(previous.sequence == EXPECTED_SEQUENCE[args.step] - 1,
        f"previous journal sequence {previous.sequence} is not the step's predecessor")
before = {e.version: e.to_mapping() for e in previous.entries}

candidate_raw = args.candidate.read_bytes()
candidate = strict(candidate_raw, "candidate")
require(isinstance(candidate, dict) and candidate_raw == canonical_json(candidate) + b"\n",
        "candidate is not canonical")
extension = operation == "extend-capabilities"
require(set(candidate) == CANDIDATE_FIELDS | ({"extension_review"} if extension else set()),
        "candidate fields differ")
envelope = candidate["envelope"]
require(candidate["model"] == (prepare.EXTENSION_MODEL if extension else prepare.MODEL)
        and candidate["operation"] == operation, "candidate operation differs")
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
# Release identities must be unique before entries are keyed by version below.
unique([e["version"] for e in envelope["entries"]], "candidate repeats a release version")
unique([e["entry_id"] for e in envelope["entries"]], "candidate repeats a release entry_id")
unique([r["version"] for r in envelope["revocations"]], "candidate repeats a revocation")
# The shipped closed envelope schema and transition rules, as the signer applies them.
try:
    prepare.validate_transition(candidate, current, generated)
except (KeyError, TypeError, ValueError) as error:
    raise SystemExit(f"FAILED: shipped transition validation refused ({error})") from None
after = {e["version"]: e for e in envelope["entries"]}
old_revocations = [r.to_mapping() for r in previous.revocations]
require(envelope["revocations"] == old_revocations, "revocations changed")
if extension:
    require(version in before, "extended version is not present")
    old = before[version]
    require(canonical_json(candidate["extension_review"]) == evidence,
            "extension review is not the reviewed evidence bytes")
    require(not any(ref in old["capabilities"] for ref in SEQ13_REFS), "a reference is already present")
    require(entry == {**old, "capabilities": old["capabilities"] + SEQ13_REFS,
                      "evidence_sha256": sha(evidence)},
            "entry is not the signed entry plus exactly the four sequence-13 references")
else:
    require(version not in before, "added version is already present")
    require(version not in {r["version"] for r in old_revocations}, "added version is revoked")
    # Same reviewed contracts as the signed 2026.10.1 entry; new release identity only.
    sibling = before["2026.10.1"]
    require(entry["capabilities"] == sibling["capabilities"]
            and entry["probe_profile_id"] == sibling["probe_profile_id"]
            and entry["probe_profile_sha256"] == sibling["probe_profile_sha256"]
            and entry["server_name"] == sibling["server_name"],
            "added entry does not carry the signed 2026.10.1 contracts and probe")
    require(entry["entry_id"] == f"ha-core-{version}-reviewed-v1", "added entry_id differs")
require(after == {**before, version: entry}, "entries are not previous with only the reviewed change")
require({v: len(e["capabilities"]) for v, e in after.items()} == EXPECTED_REFERENCES[args.step],
        "reference counts differ")
print(f"Step {args.step} {operation} {version}: sequence {envelope['sequence']}, "
      f"references { {v: len(e['capabilities']) for v, e in sorted(after.items())} }, "
      f"revoked {sorted(r['version'] for r in envelope['revocations'])}")
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
