"""HAMCP-182 Part 2: independently verify the owner-signed sequence 10 -> 11 -> 12 chain.

PUBLIC key only; never reads a private key. No network, no Home Assistant.

Two independent paths:
  * raw: every envelope and journal signature is re-verified with `cryptography`
    Ed25519 over the canonical unsigned bytes, and every chain link, prefix and
    entry difference is recomputed from the JSON, without the registry loaders;
  * runtime: this checkout's CoreReleaseRegistry (the add-on's refresh path)
    accepts each journal, steps 9 -> 10 -> 11 -> 12 and jumps 9 -> 12, and then
    refuses the older journals.

Proves exactly the authorized transitions from the sequence-9 journal main serves:
  10: 2026.10.1 gains exactly the entity-metadata reference (21 -> 22);
  11: 2026.10.0 gains exactly the entity-metadata reference (21 -> 22);
  12: 2026.9.4 is revoked with the directed reason; 10.0/10.1 keep 22 references;
  the 2026.9.2/2026.9.3 tombstones and envelopes 1-9 are unchanged.
With --installed, the published registry file must be byte-identical to journal 3.
Exit 0 and a final "OK" line only when every check passes.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta")]
from ha_mcp_engineering.ha_core_readmission import CORE_IDENTITY  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.registry import (  # noqa: E402
    CORE_REGISTRY_URL, CoreReleaseRegistry)
from ha_mcp_engineering.signed_registry import canonical_json  # noqa: E402

HERE = Path(__file__).resolve().parents[1]
PUBLIC_KEY_SHA256 = "7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac"
FILE_SHA256 = {
    "baseline": "ecaccd79ff79582f633e1406546a844ee4b38920703e4192e6463162dd4a4402",
    "candidate_1": "d9607779d6f7945083dc1ef8126857c880901653e33b32cae808eaece16488d4",
    "journal_1": "2e8ef48427224d2cf48bd1e537d84dba6306be8eddf154c189d4e7a67dd9057a",
    "candidate_2": "30c4d3adb510cef7021b97dbcc75a58edb4858f1c8228e8271993e7f39a0478d",
    "journal_2": "dde7aa3eb03b322514ce47a23482aea6113e560cc5ca66c162a18f59e425a0fb",
    "candidate_3": "05979036cd69710582ce391be13c3dd039169b97f60e44da5ef3f2a58b14ed34",
    "journal_3": "089247cafa29b3ab0c1687a63419d999ae9c894fa0a94248f73008eabf6785e6",
}
CONTENT_DIGEST = {
    10: "sha256:0b9a5c7ce7187f15652e7c29aa06d01f3674de46d12e37c618ccf532e5d867db",
    11: "sha256:73ca0e005a5033917c18b94312fef6643f12d29ad690963bd767d2b567c53047",
    12: "sha256:9d14f06ab0a0870404540ea8cfa70cefe05cc243bebb9fab0b090121081be5fc",
}
OLD_REASON = "outside the CI support window; retired by owner direction 2026-10-12 (HAMCP-161)"
REASON = "outside the CI support window; retired by owner direction (HAMCP-182)"
ENTITY = "core.entity_registry_metadata_operation"
ENTITY_REF = {
    "capability_id": ENTITY,
    "profile_id": "core_entity_registry_metadata_operation_v1",
    "profile_version": 1,
    "adapter_id": "compiled-core-entity-registry-metadata-operation-v1",
    "contract_fingerprint":
        "sha256:cc09630efbc0e1e6d0a587696406f6502c62fe730a767bc0a8cfc96a9988f767",
}
EVIDENCE = {"2026.10.1": "inputs/evidence-extend-2026.10.1.json",
            "2026.10.0": "inputs/evidence-extend-2026.10.0.json"}
JOURNAL_HEADER = ("schema_version", "registry_id", "key_id", "checkpoint_sequence",
                  "checkpoint_previous_registry_sha256", "revocation_sources")

parser = argparse.ArgumentParser()
parser.add_argument("--baseline", type=Path, required=True, help="sequence-9 journal main serves")
parser.add_argument("--candidate-1", type=Path, default=HERE / "candidates/candidate-1-extend-2026.10.1.json")
parser.add_argument("--signed-dir", type=Path, required=True,
                    help="directory with journal-1/2/3.json and candidate-2/3")
parser.add_argument("--installed", type=Path, help="published registry file; must equal journal 3")
parser.add_argument("--public-key", type=Path, default=HERE / "core-public-key.txt")
parser.add_argument("--now", required=True, help="explicit UTC time, e.g. 2026-10-15T14:00:00Z")
args = parser.parse_args()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(value) -> str:
    return "sha256:" + sha(canonical_json(value))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit("FAILED: " + message)


def unsigned(value: dict) -> dict:
    return {k: v for k, v in value.items() if k != "signature"}


def timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def entries(envelope: dict) -> dict[str, dict]:
    versions = [e["version"] for e in envelope["entries"]]
    require(len(versions) == len(set(versions)), f"seq {envelope['sequence']} repeats a version")
    return {e["version"]: e for e in envelope["entries"]}


def verify_raw(public: Ed25519PublicKey, raw: bytes, label: str, sequence: int) -> dict:
    journal = json.loads(raw)
    require(raw == canonical_json(journal) + b"\n", f"{label} is not canonical JSON")
    for value, what in [(journal, f"{label} journal"), *[(e, f"{label} seq {e['sequence']}")
                                                         for e in journal["envelopes"]]]:
        try:
            public.verify(base64.b64decode(value["signature"], validate=True), canonical_json(unsigned(value)))
        except InvalidSignature:
            raise SystemExit(f"FAILED: {what} signature does not verify") from None
    chain = journal["envelopes"]
    require([e["sequence"] for e in chain] == list(range(1, sequence + 1)), f"{label} sequences")
    require(chain[0]["previous_registry_sha256"] is None, f"{label} chain root")
    for before, after in zip(chain, chain[1:]):
        require(after["previous_registry_sha256"] == digest(unsigned(before)),
                f"{label} seq {after['sequence']} does not link to seq {before['sequence']}")
        require(timestamp(after["generated_at"]) > timestamp(before["generated_at"]),
                f"{label} seq {after['sequence']} generated_at is not later")
    return journal


async def runtime_refresh(public: str, now: datetime, journals: list[bytes]) -> list:
    """Feed journals in order to one runtime registry; record (refreshed, sequence, digest)."""
    with tempfile.TemporaryDirectory() as tmp:
        state = {"raw": None}

        async def fetch(url, maximum):
            assert url == CORE_REGISTRY_URL
            return state["raw"]

        registry = CoreReleaseRegistry(enabled=True, public_key=public, cache_path=Path(tmp) / "c.json",
                                       fetcher=fetch, now=lambda: now)
        out = []
        for raw in journals:
            state["raw"] = raw
            try:
                refreshed = await registry.refresh()
            except Exception as error:  # noqa: BLE001 - record any refusal shape
                refreshed = f"raised {type(error).__name__}"
            authority = registry.authority()
            out.append((refreshed, authority.sequence, authority.content_digest,
                        authority.positive_authority_current and not authority.surface_denied))
        return out


def main() -> None:
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")).astimezone(timezone.utc)
    public_b64 = args.public_key.read_text().strip()
    key_raw = base64.b64decode(public_b64, validate=True)
    require(sha(key_raw) == PUBLIC_KEY_SHA256, "public key fingerprint")
    public = Ed25519PublicKey.from_public_bytes(key_raw)
    d = args.signed_dir
    paths = {"baseline": args.baseline, "candidate_1": args.candidate_1,
             "journal_1": d / "journal-1.json", "candidate_2": d / "candidate-2-extend-2026.10.0.json",
             "journal_2": d / "journal-2.json", "candidate_3": d / "candidate-3-revoke-2026.9.4.json",
             "journal_3": d / "journal-3.json"}
    raw = {name: path.read_bytes() for name, path in paths.items()}
    for name, data in raw.items():
        require(sha(data) == FILE_SHA256[name], f"{name} SHA256 {sha(data)} is not the recorded output")
    if args.installed is not None:
        require(args.installed.read_bytes() == raw["journal_3"], "installed registry is not journal 3 bytes")

    # Raw path: signatures, canonical form, links, prefixes.
    journals = [verify_raw(public, raw[n], n, s)
                for n, s in (("baseline", 9), ("journal_1", 10), ("journal_2", 11), ("journal_3", 12))]
    for before, after in zip(journals, journals[1:]):
        require(after["envelopes"][:-1] == before["envelopes"],
                f"seq {after['envelopes'][-1]['sequence']} journal does not keep every earlier envelope")
        require({k: after[k] for k in JOURNAL_HEADER} == {k: before[k] for k in JOURNAL_HEADER},
                "journal header or revocation sources changed")
    tips = {j["envelopes"][-1]["sequence"]: j["envelopes"][-1] for j in journals}
    for sequence in (10, 11, 12):
        require(digest(unsigned(tips[sequence])) == CONTENT_DIGEST[sequence], f"seq {sequence} content digest")
        tip, prior = tips[sequence], tips[sequence - 1]
        generated = timestamp(tip["generated_at"])
        require(timestamp(tip["expires_at"]) - generated == timedelta(days=90), f"seq {sequence} expiry")
        require(generated <= now < timestamp(tip["expires_at"]), f"seq {sequence} is not current at --now")
        require({k: tip[k] for k in ("schema_version", "registry_id", "key_id")}
                == {k: prior[k] for k in ("schema_version", "registry_id", "key_id")}, f"seq {sequence} header")

    # Each signed tip is exactly the reviewed candidate's envelope, fenced to its predecessor file.
    for sequence, candidate_name, previous_name in ((10, "candidate_1", "baseline"),
                                                    (11, "candidate_2", "journal_1"),
                                                    (12, "candidate_3", "journal_2")):
        candidate = json.loads(raw[candidate_name])
        require(candidate["envelope"] == unsigned(tips[sequence]), f"seq {sequence} is not {candidate_name}")
        require(candidate["previous_journal_sha256"] == sha(raw[previous_name]),
                f"{candidate_name} was not prepared from {previous_name}")
        require(candidate["public_key_sha256"] == PUBLIC_KEY_SHA256, f"{candidate_name} key")

    # Exactly the authorized entry and revocation differences.
    profile = {p.capability_id: p.to_mapping() for p in CORE_RUNTIME_CAPABILITY_PROFILES}[ENTITY]
    require(all(profile[k] == v for k, v in ENTITY_REF.items()), "entity reference is not the compiled contract")
    e9, e10, e11, e12 = (entries(tips[s]) for s in (9, 10, 11, 12))
    require(sorted(e9) == ["2026.10.0", "2026.10.1", "2026.9.4"], f"seq 9 entries {sorted(e9)}")

    def extended(old: dict, version: str) -> dict:
        evidence = (HERE / EVIDENCE[version]).read_bytes()
        require(ENTITY_REF not in old["capabilities"], f"{version} already had the reference")
        return {**old, "capabilities": old["capabilities"] + [ENTITY_REF], "evidence_sha256": sha(evidence)}

    x101, x100 = extended(e9["2026.10.1"], "2026.10.1"), extended(e9["2026.10.0"], "2026.10.0")
    require(e10 == {**e9, "2026.10.1": x101}, "seq 10 is not seq 9 plus the 2026.10.1 extension only")
    require(e11 == {**e10, "2026.10.0": x100}, "seq 11 is not seq 10 plus the 2026.10.0 extension only")
    require(e12 == {v: e for v, e in e11.items() if v != "2026.9.4"}, "seq 12 is not seq 11 minus 2026.9.4")
    old_revocations = tips[9]["revocations"]
    require([r["version"] for r in old_revocations] == ["2026.9.2", "2026.9.3"]
            and all(r["reason"] == OLD_REASON for r in old_revocations), "seq 9 revocations")
    require(tips[10]["revocations"] == old_revocations == tips[11]["revocations"],
            "extensions changed revocations")
    removed = e11["2026.9.4"]
    require(tips[12]["revocations"] == old_revocations + [{
        "entry_id": removed["entry_id"], "server_name": removed["server_name"], "version": "2026.9.4",
        "image_index_digest": removed["image_index_digest"], "revoked_at": tips[12]["generated_at"],
        "reason": REASON}], "seq 12 revocations are not seq 9 plus the exact 2026.9.4 tombstone")
    require(removed == e9["2026.9.4"], "the revoked 2026.9.4 entry is not the seq-9 entry")
    for journal in journals:
        for envelope in journal["envelopes"]:
            for entry in envelope["entries"]:
                has = any(c["capability_id"] == ENTITY for c in entry["capabilities"])
                allowed = envelope["sequence"] >= 10 and entry["version"] in ("2026.10.0", "2026.10.1") and (
                    entry["version"] == "2026.10.1" or envelope["sequence"] >= 11)
                require(has == allowed, f"entity reference in seq {envelope['sequence']} {entry['version']}")

    # Runtime path: the add-on's refresh code.
    stepwise = asyncio.run(runtime_refresh(public_b64, now, [raw["baseline"], raw["journal_1"], raw["journal_2"],
                                                             raw["journal_3"], raw["baseline"], raw["journal_1"]]))
    jump = asyncio.run(runtime_refresh(public_b64, now, [raw["baseline"], raw["journal_3"], raw["journal_2"]]))
    require([s[:2] for s in stepwise[:4]] == [(True, 9), (True, 10), (True, 11), (True, 12)]
            and all(s[3] for s in stepwise[:4]), f"runtime stepwise {stepwise[:4]}")
    require([s[2] for s in stepwise[1:4]] == [CONTENT_DIGEST[10], CONTENT_DIGEST[11], CONTENT_DIGEST[12]],
            "runtime content digests")
    require([s[:2] for s in jump[:2]] == [(True, 9), (True, 12)] and jump[1][2] == CONTENT_DIGEST[12],
            f"runtime jump {jump[:2]}")
    for label, record in (("seq 9 after 12", stepwise[4]), ("seq 10 after 12", stepwise[5]),
                          ("seq 11 after 12", jump[2])):
        require(record[0] is not True and record[1] == 12 and record[2] == CONTENT_DIGEST[12],
                f"runtime accepted an older journal ({label}): {record}")

    print(f"Public key fingerprint {PUBLIC_KEY_SHA256}; files {len(raw)} SHA256 match")
    print(f"Raw Ed25519: {sum(len(j['envelopes']) + 1 for j in journals)} signatures verified; "
          f"chain 1..12 linked; envelopes 1-9 unchanged")
    for sequence in (10, 11, 12):
        tip = entries(tips[sequence])
        print(f"seq {sequence} {CONTENT_DIGEST[sequence]} references "
              f"{ {v: len(e['capabilities']) for v, e in sorted(tip.items())} } revoked "
              f"{[r['version'] for r in tips[sequence]['revocations']]} expires_at {tips[sequence]['expires_at']}")
    print(f"Runtime stepwise {[s[:2] for s in stepwise]}; jump {[s[:2] for s in jump]}")
    if args.installed is not None:
        print(f"Installed {args.installed} is byte-identical to journal 3")
    print(f"Registry code: CORE_IDENTITY={CORE_IDENTITY!r}; root {ROOT}")
    print("OK")


main()
