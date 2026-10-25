"""HAMCP-222 publication: independently verify the owner-signed sequence 12 -> 13 -> 14 chain.

PUBLIC key only; never reads a private key. No network, no Home Assistant.

Two independent paths:
  * raw: every envelope and journal signature is re-verified with `cryptography`
    Ed25519 over the canonical unsigned bytes, and every chain link, prefix and
    entry difference is recomputed from the JSON, without the registry loaders
    or check_step.py;
  * runtime: this checkout's CoreReleaseRegistry (the add-on's refresh path; the
    checkout's runtime package must be the v2.4.1-beta.36 tree) accepts each
    journal, steps 12 -> 13 -> 14 and jumps 12 -> 14, refuses the older journals
    afterwards, and its own selection code yields the admitted capability sets.

Proves exactly the authorized transitions from the sequence-12 journal main serves:
  13: 2026.10.1 gains exactly the four reviewed references, in compiled order (22 -> 26);
  14: 2026.10.3 is added with the same 26 references and probe as 2026.10.1;
  2026.10.0 keeps its 22 references, revocations (2026.9.2-9.4) and envelopes 1-12
  are unchanged, and no other release is admitted.
With --installed, the published registry file must be byte-identical to journal 2.
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
import subprocess
import sys
import tempfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta")]
from ha_mcp_engineering.ha_core_readmission import CORE_IDENTITY  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.models import CoreAuthorityStatus  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.registry import (  # noqa: E402
    CORE_REGISTRY_URL, CoreReleaseRegistry)
from ha_mcp_engineering.signed_registry import canonical_json  # noqa: E402

HERE = Path(__file__).resolve().parents[1]
RUNTIME_TREE = "70479fa2958aafd36d0e74e3082f98c50b11053c"  # v2.4.1-beta.36 ha_mcp_engineering
PUBLIC_KEY_SHA256 = "7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac"
FILE_SHA256 = {
    "baseline": "089247cafa29b3ab0c1687a63419d999ae9c894fa0a94248f73008eabf6785e6",
    "candidate_1": "923bd135b02618046552786261036d53dced703be05da93f27c1f80510072458",
    "journal_1": "bdd10c0d3c2ddeb763f5a1598e0346b05112b96abd79230bae5b1ac107a77045",
    "candidate_2": "0fe9cbd8ed17d06a21657b1098b95e105289ba0cbfff89ca5d4e44044277f695",
    "journal_2": "80d91919ebbd57e4ca1b7a49bc2bb6a9b2996b3b14bef3070916263e82c53817",
}
INPUT_SHA256 = {
    "inputs/evidence-extend-2026.10.1.json": "367a9303e10463029ceaaa1620769412e526a5a8cbf1cc803c074f87f1009cf4",
    "inputs/entry-add-2026.10.3.json": "98cdad1001cc9312a5bf82ac1d6ced3e22da4ae8369c2b15772fd6a2172ae31d",
    "inputs/evidence-add-2026.10.3.json": "e245e6347b5327f746aa15085aa690d7a14add3f1fa35d0980ba86d8837a9f01",
}
CONTENT_DIGEST = {
    12: "sha256:9d14f06ab0a0870404540ea8cfa70cefe05cc243bebb9fab0b090121081be5fc",
    13: "sha256:55311b896557ba43435d44b34f62df018afd1b784c382aa01536f49153a9ac8a",
    14: "sha256:734210cf33344c62886377e6abea5d4df776585d5ba687f22087778fa3e93199",
}
# The four sequence-13 capabilities: compiled profiles 22-25, appended in this order.
NEW = ["core.entity_registry_availability_operation", "core.entity_assistant_exposure_operation",
       "core.label_registry_operation", "core.configuration_delete_operation"]
REVOKED = ["2026.9.2", "2026.9.3", "2026.9.4"]
JOURNAL_HEADER = ("schema_version", "registry_id", "key_id", "checkpoint_sequence",
                  "checkpoint_previous_registry_sha256", "revocation_sources")
# Never listed in any sequence: a control that nothing else gains authority.
UNLISTED = ("2026.10.2", "2026.11.0")

parser = argparse.ArgumentParser()
parser.add_argument("--baseline", type=Path, required=True, help="sequence-12 journal main serves")
parser.add_argument("--candidate-1", type=Path, default=HERE / "candidates/candidate-1-extend-2026.10.1.json")
parser.add_argument("--signed-dir", type=Path, required=True,
                    help="directory with journal-1.json, candidate-2-add-2026.10.3.json and journal-2.json")
parser.add_argument("--installed", type=Path, help="published registry file; must equal journal 2")
parser.add_argument("--public-key", type=Path, default=HERE / "core-public-key.txt")
parser.add_argument("--now", required=True, help="explicit UTC time, e.g. 2026-10-25T12:00:00Z")
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


def git(*argv: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *argv], check=True, capture_output=True,
                          text=True).stdout.strip()


async def runtime_refresh(public: str, now: datetime, journals: list[bytes]) -> list:
    """Feed journals in order to one runtime registry; record each outcome and the admitted sets."""
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
            admitted = {}
            for version in ["2026.10.0", "2026.10.1", "2026.10.3", *REVOKED, *UNLISTED]:
                selected = [c for s in registry.selections(version)
                            if s.status is CoreAuthorityStatus.POSITIVE for c in s.capability_ids]
                require(len(selected) == len(set(selected)), f"{version} selects a capability twice")
                admitted[version] = sorted(selected)
            out.append((refreshed, authority.sequence, authority.content_digest,
                        authority.positive_authority_current and not authority.surface_denied, admitted))
        return out


def main() -> None:
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")).astimezone(timezone.utc)
    require(git("rev-parse", "HEAD:hass_mcp_engineering_beta/ha_mcp_engineering") == RUNTIME_TREE
            and git("status", "--porcelain", "--", "hass_mcp_engineering_beta") == "",
            "checkout runtime is not the clean v2.4.1-beta.36 tree")
    public_b64 = args.public_key.read_text().strip()
    key_raw = base64.b64decode(public_b64, validate=True)
    require(sha(key_raw) == PUBLIC_KEY_SHA256, "public key fingerprint")
    public = Ed25519PublicKey.from_public_bytes(key_raw)
    d = args.signed_dir
    paths = {"baseline": args.baseline, "candidate_1": args.candidate_1,
             "journal_1": d / "journal-1.json", "candidate_2": d / "candidate-2-add-2026.10.3.json",
             "journal_2": d / "journal-2.json"}
    raw = {name: path.read_bytes() for name, path in paths.items()}
    for name, data in raw.items():
        require(sha(data) == FILE_SHA256[name], f"{name} SHA256 {sha(data)} is not the recorded output")
    inputs = {path: (HERE / path).read_bytes() for path in INPUT_SHA256}
    for path, data in inputs.items():
        require(sha(data) == INPUT_SHA256[path], f"{path} is not the reviewed input")
    if args.installed is not None:
        require(args.installed.read_bytes() == raw["journal_2"], "installed registry is not journal 2 bytes")

    # Raw path: signatures, canonical form, links, prefixes.
    journals = [verify_raw(public, raw[n], n, s)
                for n, s in (("baseline", 12), ("journal_1", 13), ("journal_2", 14))]
    for before, after in zip(journals, journals[1:]):
        require(after["envelopes"][:-1] == before["envelopes"],
                f"seq {after['envelopes'][-1]['sequence']} journal does not keep every earlier envelope")
        require({k: after[k] for k in JOURNAL_HEADER} == {k: before[k] for k in JOURNAL_HEADER},
                "journal header or revocation sources changed")
    tips = {j["envelopes"][-1]["sequence"]: j["envelopes"][-1] for j in journals}
    require(digest(unsigned(tips[12])) == CONTENT_DIGEST[12], "seq 12 is not the production tip")
    for sequence in (13, 14):
        require(digest(unsigned(tips[sequence])) == CONTENT_DIGEST[sequence], f"seq {sequence} content digest")
        tip, prior = tips[sequence], tips[sequence - 1]
        generated = timestamp(tip["generated_at"])
        require(timestamp(tip["expires_at"]) - generated == timedelta(days=90), f"seq {sequence} expiry")
        require(generated <= now < timestamp(tip["expires_at"]), f"seq {sequence} is not current at --now")
        require({k: tip[k] for k in ("schema_version", "registry_id", "key_id")}
                == {k: prior[k] for k in ("schema_version", "registry_id", "key_id")}, f"seq {sequence} header")

    # Each signed tip is exactly the reviewed candidate's envelope, fenced to its predecessor file.
    for sequence, candidate_name, previous_name in ((13, "candidate_1", "baseline"),
                                                    (14, "candidate_2", "journal_1")):
        candidate = json.loads(raw[candidate_name])
        require(candidate["envelope"] == unsigned(tips[sequence]), f"seq {sequence} is not {candidate_name}")
        require(candidate["previous_journal_sha256"] == sha(raw[previous_name]),
                f"{candidate_name} was not prepared from {previous_name}")
        require(candidate["public_key_sha256"] == PUBLIC_KEY_SHA256, f"{candidate_name} key")

    # Exactly the authorized entry differences, recomputed from the compiled profiles.
    compiled = [p.to_mapping() for p in CORE_RUNTIME_CAPABILITY_PROFILES]
    reference_keys = ("capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")
    references = [{k: p[k] for k in reference_keys} for p in compiled]
    require(len(references) == 26 and [r["capability_id"] for r in references[22:]] == NEW,
            "the four capabilities are not compiled profiles 22-25")
    new_refs = references[22:]
    e12, e13, e14 = (entries(tips[s]) for s in (12, 13, 14))
    require(sorted(e12) == ["2026.10.0", "2026.10.1"], f"seq 12 entries {sorted(e12)}")
    old = e12["2026.10.1"]
    require(not any(r in old["capabilities"] for r in new_refs), "2026.10.1 already had a new reference")
    x101 = {**old, "capabilities": old["capabilities"] + new_refs,
            "evidence_sha256": sha(inputs["inputs/evidence-extend-2026.10.1.json"])}
    require(e13 == {**e12, "2026.10.1": x101}, "seq 13 is not seq 12 plus the 2026.10.1 extension only")
    added = json.loads(inputs["inputs/entry-add-2026.10.3.json"])
    require(added["capabilities"] == x101["capabilities"]
            and all(added[k] == x101[k] for k in ("probe_profile_id", "probe_profile_sha256", "server_name"))
            and added["entry_id"] == "ha-core-2026.10.3-reviewed-v1" and added["version"] == "2026.10.3"
            and added["evidence_sha256"] == sha(inputs["inputs/evidence-add-2026.10.3.json"]),
            "the 2026.10.3 entry does not carry the 2026.10.1 contracts and probe with its own evidence")
    require(e14 == {**e13, "2026.10.3": added}, "seq 14 is not seq 13 plus the 2026.10.3 entry only")
    for version, entry in e14.items():
        require(all(r in references for r in entry["capabilities"]),
                f"{version} carries a reference that is not a compiled contract")
    require([r["version"] for r in tips[12]["revocations"]] == REVOKED, "seq 12 revocations")
    require(tips[13]["revocations"] == tips[12]["revocations"] == tips[14]["revocations"],
            "revocations changed")
    for journal in journals:
        for envelope in journal["envelopes"]:
            for entry in envelope["entries"]:
                has = [r in entry["capabilities"] for r in new_refs]
                allowed = (envelope["sequence"] >= 13 and entry["version"] == "2026.10.1") or (
                    envelope["sequence"] >= 14 and entry["version"] == "2026.10.3")
                require(has == [allowed] * 4, f"new references in seq {envelope['sequence']} {entry['version']}")

    # Runtime path: the add-on's refresh and selection code.
    stepwise = asyncio.run(runtime_refresh(public_b64, now, [raw["baseline"], raw["journal_1"], raw["journal_2"],
                                                             raw["baseline"], raw["journal_1"]]))
    jump = asyncio.run(runtime_refresh(public_b64, now, [raw["baseline"], raw["journal_2"], raw["journal_1"]]))
    require([s[:2] for s in stepwise[:3]] == [(True, 12), (True, 13), (True, 14)]
            and all(s[3] for s in stepwise[:3]), f"runtime stepwise {[s[:4] for s in stepwise[:3]]}")
    require([s[2] for s in stepwise[:3]] == [CONTENT_DIGEST[12], CONTENT_DIGEST[13], CONTENT_DIGEST[14]],
            "runtime content digests")
    require([s[:2] for s in jump[:2]] == [(True, 12), (True, 14)] and jump[1][2] == CONTENT_DIGEST[14],
            f"runtime jump {[s[:3] for s in jump[:2]]}")
    for label, record in (("seq 12 after 14", stepwise[3]), ("seq 13 after 14", stepwise[4]),
                          ("seq 13 after jump to 14", jump[2])):
        require(record[0] is not True and record[1] == 14 and record[2] == CONTENT_DIGEST[14]
                and record[4] == stepwise[2][4], f"runtime accepted an older journal ({label}): {record[:3]}")
    require(jump[1][4] == stepwise[2][4], "jump 12 -> 14 admits differently from stepping")

    at12, at13, at14 = (stepwise[i][4] for i in range(3))
    ids = lambda entry: sorted(r["capability_id"] for r in entry["capabilities"])  # noqa: E731
    require(at12["2026.10.0"] == ids(e12["2026.10.0"]) and len(at12["2026.10.0"]) == 22, "seq 12 2026.10.0")
    require(at12["2026.10.1"] == ids(e12["2026.10.1"]) and len(at12["2026.10.1"]) == 22, "seq 12 2026.10.1")
    require(at12["2026.10.3"] == [], "2026.10.3 admitted at seq 12")
    require(at13["2026.10.0"] == at12["2026.10.0"], "seq 13 changed 2026.10.0")
    require(at13["2026.10.1"] == sorted(at12["2026.10.1"] + NEW), "seq 13 2026.10.1 is not seq 12 plus the four")
    require(at13["2026.10.3"] == [], "2026.10.3 admitted at seq 13")
    require(at14["2026.10.0"] == at12["2026.10.0"], "seq 14 changed 2026.10.0")
    require(at14["2026.10.1"] == at13["2026.10.1"], "seq 14 changed 2026.10.1")
    require(at14["2026.10.3"] == at13["2026.10.1"] and len(at14["2026.10.3"]) == 26,
            "seq 14 2026.10.3 is not the 26 of 2026.10.1")
    for state in (at12, at13, at14):
        require(all(state[v] == [] for v in [*REVOKED, *UNLISTED]), "a revoked or unlisted release is admitted")

    print(f"Public key fingerprint {PUBLIC_KEY_SHA256}; files {len(raw)} and inputs {len(inputs)} SHA256 match")
    print(f"Raw Ed25519: {sum(len(j['envelopes']) + 1 for j in journals)} signatures verified; "
          f"chain 1..14 linked; envelopes 1-12 unchanged")
    for sequence in (12, 13, 14):
        tip = entries(tips[sequence])
        print(f"seq {sequence} {CONTENT_DIGEST[sequence]} references "
              f"{ {v: len(e['capabilities']) for v, e in sorted(tip.items())} } revoked "
              f"{[r['version'] for r in tips[sequence]['revocations']]} expires_at {tips[sequence]['expires_at']}")
    print(f"Runtime stepwise {[s[:2] for s in stepwise]}; jump {[s[:2] for s in jump]}")
    for sequence, state in ((12, at12), (13, at13), (14, at14)):
        print(f"Runtime admitted at seq {sequence}: " + ", ".join(
            f"{v} {len(state[v])}" for v in ["2026.10.0", "2026.10.1", "2026.10.3", *REVOKED, *UNLISTED]))
    print(f"seq 13 adds to 2026.10.1: {sorted(set(at13['2026.10.1']) - set(at12['2026.10.1']))}")
    print(f"seq 14 2026.10.3 admitted sha256 {sha(canonical_json(at14['2026.10.3']))} "
          f"(equal to 2026.10.1: {at14['2026.10.3'] == at14['2026.10.1']})")
    if args.installed is not None:
        print(f"Installed {args.installed} is byte-identical to journal 2")
    print(f"Registry code: CORE_IDENTITY={CORE_IDENTITY!r}; runtime tree {RUNTIME_TREE} (v2.4.1-beta.36)")
    print("OK")


main()
