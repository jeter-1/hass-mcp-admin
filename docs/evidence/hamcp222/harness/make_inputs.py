"""HAMCP-222: verify the seq-13 CI receipts and build the sequence-13 signing inputs.

Deterministic and key-free: every value comes from the retained CI artifacts
(--artifacts), the repository (read with git) and the production journal,
verified with the PUBLIC key only. Nothing here reads, requests or needs a
private key.

Run from a checkout whose Engineering runtime package tree is the one the
receipts bind (2.4.1-beta.36, 70479fa2) and whose support window has 2026.10.1
current and 2026.10.3 candidate (PR #305), with the seq-13 lanes dormant: the
offline verifiers are the shipped ones at that checkout.

  --artifacts DIR  the CI artifacts as downloaded: DIR/zips/<name>.zip and the
                   same zip unzipped as DIR/<name>/.

Writes into --out (docs/evidence/hamcp222); every output path must be new:
  receipts/<core>/*.json                    byte-identical copies of the receipts
  receipts/manifest.json                    run, artifacts, digests, verifier summaries
  inputs/evidence-extend-2026.10.1.json     canonical core-capability-extension-evidence-v1
  inputs/entry-extend-2026.10.1.json        the signed entry + the four seq-13 references
  candidates/candidate-1-extend-2026.10.1.json
                                            unsigned candidate for sequence 13, prepared
                                            from the production sequence-12 journal at
                                            the fixed GENERATED_AT with the shipped
                                            prepare_candidate
"""

from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument("--repo", type=Path, required=True)
parser.add_argument("--artifacts", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()

REPO = args.repo.resolve()
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "hass_mcp_engineering_beta")]
import configuration_delete_contract_acceptance as delete_acceptance  # noqa: E402
import entity_metadata_contract_acceptance as entity_acceptance  # noqa: E402
import prepare_core_release_registry as prepare  # noqa: E402
import seq13_contract_acceptance as seq13_acceptance  # noqa: E402
import support_window  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_RUNTIME_CAPABILITY_PROFILES)
from ha_mcp_engineering.signed_registry import canonical_json  # noqa: E402

# The clean evidence run: PR #306 (seq-13 lanes enabled for this run only).
RUN = {"run_id": 40148395528, "run_attempt": 2, "event": "pull_request", "pull_request": 306,
       "head_sha": "902690a55074e4a6d25e757d542dd954126a6408",
       "merge_sha": "__MERGE_SHA__",
       "workflow": ".github/workflows/ci.yml", "conclusion": "success"}
RUNTIME_TREE = "70479fa2958aafd36d0e74e3082f98c50b11053c"  # v2.4.1-beta.36 ha_mcp_engineering
RELEASE_TAG = "v2.4.1-beta.36"
# zip SHA256 = the GitHub artifact digest, recorded when downloaded.
ARTIFACTS = {
    "2026.10.1": {"slot": "current", "lane": "ha-2026-10-1", "zips": {
        "seq13-contracts-core-2026.10.1-current-amd64": "__",
        "seq13-configuration-delete-core-2026.10.1-current-ha-2026-10-1": "__",
        "entity-metadata-core-2026.10.1-current-amd64": "__",
    }},
    "2026.10.3": {"slot": "candidate", "lane": "ha-2026-10-3", "zips": {
        "seq13-contracts-core-2026.10.3-candidate-amd64": "__",
        "seq13-configuration-delete-core-2026.10.3-candidate-ha-2026-10-3": "__",
        "entity-metadata-core-2026.10.3-candidate-amd64": "__",
    }},
}
JOURNAL = "upstream-trust/ha-core-release-registry.json"
JOURNAL_SHA256 = "089247cafa29b3ab0c1687a63419d999ae9c894fa0a94248f73008eabf6785e6"
PUBLIC_KEY = REPO / "docs/evidence/hamcp182/core-public-key.txt"
PUBLIC_KEY_SHA256 = "7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac"
PREDECESSOR_SHA256 = "1fdf85c48b6d"  # prefix; the full value is recomputed from the journal
GENERATED_AT = datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc)
EXTENDED = "2026.10.1"
SEQ13 = ("core.entity_registry_availability_operation", "core.entity_assistant_exposure_operation",
         "core.label_registry_operation", "core.configuration_delete_operation")
REFERENCE_KEYS = entity_acceptance.REFERENCE_KEYS


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*argv: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *argv], check=True, capture_output=True,
                          text=True).stdout.strip()


def ref(profile) -> dict:
    return {k: profile.to_mapping()[k] for k in REFERENCE_KEYS}


# 1. Checkout, key and production journal.
assert git("rev-parse", "HEAD:hass_mcp_engineering_beta/ha_mcp_engineering") == RUNTIME_TREE
assert git("rev-parse", f"{RELEASE_TAG}:hass_mcp_engineering_beta/ha_mcp_engineering") == RUNTIME_TREE
assert git("rev-parse", f"{RUN['merge_sha']}:hass_mcp_engineering_beta/ha_mcp_engineering") == RUNTIME_TREE
assert git("rev-parse", f"{RUN['merge_sha']}^2") == RUN["head_sha"]
assert git("status", "--porcelain", "--", "hass_mcp_engineering_beta", "scripts", "tests",
           ".github", JOURNAL, "docs/evidence/hamcp182") == ""
assert delete_acceptance.enabled() is False, "verify with the seq-13 lanes dormant"
window = support_window.load(REPO)
slots = {s: (None if window["core"][s] is None else window["core"][s]["version"])
         for s in ("candidate", "current", "rollback")}
assert slots == {"candidate": "2026.10.3", "current": "2026.10.1", "rollback": "2026.10.0"}, slots

key_raw = base64.b64decode(PUBLIC_KEY.read_bytes().strip(), validate=True)
assert sha(key_raw) == PUBLIC_KEY_SHA256
public = Ed25519PublicKey.from_public_bytes(key_raw)
journal_raw = (REPO / JOURNAL).read_bytes()
assert sha(journal_raw) == JOURNAL_SHA256, "production journal is not the reviewed sequence 12"
tip = prepare.parse_journal(journal_raw, public).accepted
assert tip.sequence == 12
entries = {e.version: e.to_mapping() for e in tip.entries}
assert sorted(entries) == ["2026.10.0", "2026.10.1"]
assert sorted(r.version for r in tip.revocations) == ["2026.9.2", "2026.9.3", "2026.9.4"]

compiled = [ref(p) for p in CORE_RUNTIME_CAPABILITY_PROFILES]
assert [r["capability_id"] for r in compiled[22:]] == list(SEQ13) and len(compiled) == 26
assert all(p.auto_eligible for p in CORE_RUNTIME_CAPABILITY_PROFILES)
old = entries[EXTENDED]
assert old["capabilities"] == compiled[:22], "signed 2026.10.1 is not the first 22 compiled references"
predecessor_sha = sha(canonical_json(old["capabilities"]))
assert predecessor_sha.startswith(PREDECESSOR_SHA256)
added = compiled[22:]

# 2. Receipts: digest, member names, shipped offline verifiers, cross-bindings.
for sub in ("inputs", "receipts", "candidates"):
    (args.out / sub).mkdir(parents=True, exist_ok=True)
manifest = {"model": "hamcp222-receipt-manifest-v1", "run": RUN,
            "verified_at_commit": git("rev-parse", "HEAD"), "runtime_tree": RUNTIME_TREE,
            "release_tag": RELEASE_TAG, "predecessor_capabilities_sha256": predecessor_sha,
            "cores": {}}
engineering_source = {"commit": RUN["merge_sha"], "runtime_tree": RUNTIME_TREE}
review = {}
for core, art in ARTIFACTS.items():
    slot, lane = art["slot"], art["lane"]
    files = {}
    for name, digest in art["zips"].items():
        raw_zip = (args.artifacts / "zips" / f"{name}.zip").read_bytes()
        assert sha(raw_zip) == digest, f"{name}: zip differs from the GitHub artifact digest"
        with zipfile.ZipFile(args.artifacts / "zips" / f"{name}.zip") as archive:
            members = sorted(archive.namelist())
            for member in members:
                assert archive.read(member) == (args.artifacts / name / member).read_bytes()
        files[name] = members
    contracts, deletion, entity = (args.artifacts / n for n in art["zips"])
    assert files[contracts.name] == sorted([f"seq13-availability-exposure-{lane}.json",
                                            f"seq13-label-registry-{lane}.json"])
    assert files[deletion.name] == sorted([f"alarmo-cleanup-{lane}.json",
                                           f"seq13-configuration-delete-{lane}.json"])
    assert files[entity.name] == sorted([f"alarmo-cleanup-{lane}.json",
                                         f"entity-metadata-{lane}.json"])
    cleanup_raw = (deletion / f"alarmo-cleanup-{lane}.json").read_bytes()
    assert (entity / f"alarmo-cleanup-{lane}.json").read_bytes() == cleanup_raw
    cleanup = entity_acceptance.load_receipt(cleanup_raw)

    paths = {"label_registry": contracts / f"seq13-label-registry-{lane}.json",
             "availability_exposure": contracts / f"seq13-availability-exposure-{lane}.json",
             "configuration_delete": deletion / f"seq13-configuration-delete-{lane}.json",
             "entity_metadata": entity / f"entity-metadata-{lane}.json",
             "cleanup": deletion / f"alarmo-cleanup-{lane}.json"}
    receipts = {k: entity_acceptance.load_receipt(p.read_bytes()) for k, p in paths.items()}
    summaries = {}
    for key in ("label_registry", "availability_exposure"):
        summaries[key] = seq13_acceptance.verify_result(key, receipts[key], core_version=core,
                                                        support_slot=slot)
    summaries["configuration_delete"] = delete_acceptance.verify_result(
        receipts["configuration_delete"], core_version=core, support_slot=slot)
    delete_raw = paths["configuration_delete"].read_bytes()
    assert delete_raw == entity_acceptance.receipt_bytes(receipts["configuration_delete"])
    assert cleanup["required_seq13_configuration_delete"] == {
        **summaries["configuration_delete"], "receipt_sha256": sha(delete_raw)}
    summaries["entity_metadata"] = entity_acceptance.verify_receipt_files(
        str(paths["entity_metadata"]), str(paths["cleanup"]), core_version=core, support_slot=slot)
    assert cleanup["result"] == "PASS" and cleanup["required_alarmo_interval"] == "PASS"
    assert cleanup["required_automation_baseline"]["result"] == "PASS"
    image = old["image_index_digest"] if core == EXTENDED else window["core"]["candidate"]["image_index_digest"]
    for key in ("label_registry", "availability_exposure", "configuration_delete", "entity_metadata"):
        receipt = receipts[key]
        assert receipt["engineering_source"] == engineering_source, key
        assert receipt["image_index_digest"] == image, key
        if key != "entity_metadata":
            assert receipt["predecessor_capabilities_sha256"] == predecessor_sha, key
    assert [r["capability_id"] for r in receipts["availability_exposure"]["added_capabilities"]] == list(SEQ13[:2])
    assert receipts["availability_exposure"]["added_capabilities"] == added[:2]
    assert receipts["label_registry"]["added_capabilities"] == [added[2]]
    assert receipts["configuration_delete"]["added_capability"] == added[3]

    target = args.out / "receipts" / core
    target.mkdir()
    hashes = {}
    for key, path in paths.items():
        copy = target / path.name
        shutil.copyfile(path, copy)
        hashes[path.name] = sha(copy.read_bytes())
    zips = {name: digest for name, digest in art["zips"].items()}
    manifest["cores"][core] = {"slot": slot, "lane": lane, "zip_sha256": zips,
                               "files_sha256": hashes, "verifier_summaries": summaries}
    review[core] = [hashes[paths[k].name] for k in
                    ("availability_exposure", "label_registry", "configuration_delete", "cleanup")] + [
        zips[contracts.name], zips[deletion.name]]

# 3. Sequence 13 inputs: extend 2026.10.1 from 22 to 26 references.
evidence = canonical_json({
    "model": prepare.EXTENSION_EVIDENCE_MODEL, "version": EXTENDED,
    "previous_entry_sha256": sha(canonical_json(old)),
    "previous_capabilities": old["capabilities"],
    "added_capabilities": added,
    "review_artifacts_sha256": review[EXTENDED],
})
entry = {**deepcopy(old), "capabilities": deepcopy(old["capabilities"]) + deepcopy(added),
         "evidence_sha256": sha(evidence)}
prepare.require_extension_evidence(json.loads(evidence), old, entry)
prepare.write_new(args.out / f"inputs/evidence-extend-{EXTENDED}.json", evidence)
prepare.write_new(args.out / f"inputs/entry-extend-{EXTENDED}.json", canonical_json(entry) + b"\n")

candidate = prepare.prepare_candidate(entry=entry, evidence=evidence, previous=journal_raw,
                                      public_key=public, operation="extend-capabilities",
                                      now=GENERATED_AT)
candidate_raw = canonical_json(candidate) + b"\n"
prepare.write_new(args.out / f"candidates/candidate-1-extend-{EXTENDED}.json", candidate_raw)

manifest["inputs_sha256"] = {p.name: sha(p.read_bytes())
                             for p in sorted((args.out / "inputs").iterdir())
                             if p.name.endswith(f"{EXTENDED}.json")}
manifest["candidate_1"] = {"generated_at": candidate["envelope"]["generated_at"],
                           "expires_at": candidate["envelope"]["expires_at"],
                           "sequence": candidate["envelope"]["sequence"],
                           "sha256": sha(candidate_raw)}
prepare.write_new(args.out / "receipts/manifest.json",
                  (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode())
print(json.dumps({"candidate_1": manifest["candidate_1"], "inputs_sha256": manifest["inputs_sha256"]},
                 indent=1, sort_keys=True))
