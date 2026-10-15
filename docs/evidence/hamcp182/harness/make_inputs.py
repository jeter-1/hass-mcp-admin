"""HAMCP-182: build the owner-signing inputs from the main CI entity-metadata receipts.

Run from a checkout of the exact commit the receipts were produced at (main
690a00b, deployed Engineering 2.4.1-beta.9). Nothing here reads, requests or
needs a private key; the production journal is verified with the PUBLIC key.

  --receipts DIR  the two downloaded CI artifacts, unzipped as DIR/current and
                  DIR/rollback, with DIR/artifact-<id>.zip next to them.

Writes into --out (docs/evidence/hamcp182):
  receipts/<slot>/{entity-metadata,alarmo-cleanup}-<lane>.json  byte-identical copies
  receipts/manifest.json                    run, artifacts, digests, verifier summaries
  inputs/evidence-extend-<core>.json        canonical core-capability-extension-evidence-v1
  inputs/entry-extend-<core>.json           the existing entry + the entity reference
  inputs/evidence-revoke-2026.9.4.json      canonical withdrawal evidence
  inputs/entry-revoke-2026.9.4.json         the exact signed 2026.9.4 entry, rebound
"""

from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--repo", type=Path, required=True)
parser.add_argument("--receipts", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()

REPO = args.repo.resolve()
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "hass_mcp_engineering_beta")]
import entity_metadata_contract_acceptance as acceptance  # noqa: E402
import prepare_core_release_registry as prepare  # noqa: E402
import support_window  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_RUNTIME_CAPABILITY_PROFILES)
from ha_mcp_engineering.signed_registry import canonical_json  # noqa: E402

RUN = {"run_id": 38681524688, "run_number": 1100, "run_attempt": 1, "event": "workflow_dispatch",
       "head_branch": "main", "head_sha": "690a00b2fcbffc4e90c97a1cad36ccf6168e8ec1",
       "workflow": ".github/workflows/ci.yml", "conclusion": "success"}
ARTIFACTS = {
    "current": {"core": "2026.10.1", "lane": "ha-2026-10-1", "artifact_id": 11948098495,
                "name": "entity-metadata-core-2026.10.1-current-amd64",
                "zip_sha256": "124950f12d3eff942a513c370cfb8dece8953ade6fd9632581561c8d3dca2114"},
    "rollback": {"core": "2026.10.0", "lane": "ha-2026-10-0", "artifact_id": 11948661527,
                 "name": "entity-metadata-core-2026.10.0-rollback-amd64",
                 "zip_sha256": "8dc5a32bb87abf2b81450d3736651522079d2a95f6ce7221995ff840e995c9e4"},
}
JOURNAL = "upstream-trust/ha-core-release-registry.json"
JOURNAL_SHA256 = "ecaccd79ff79582f633e1406546a844ee4b38920703e4192e6463162dd4a4402"
PUBLIC_KEY = REPO / "docs/evidence/hamcp161/core-public-key.txt"
PUBLIC_KEY_SHA256 = "7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac"
REASON = "outside the CI support window; retired by owner direction (HAMCP-182)"
REFERENCE_KEYS = acceptance.REFERENCE_KEYS
ENTITY = next(p for p in CORE_RUNTIME_CAPABILITY_PROFILES
              if p.capability_id == "core.entity_registry_metadata_operation")
ENTITY_REF = {k: ENTITY.to_mapping()[k] for k in REFERENCE_KEYS}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*argv: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *argv], check=True, capture_output=True,
                          text=True).stdout.strip()


head = git("rev-parse", "HEAD")
assert head == RUN["head_sha"], f"run from the producing commit, not {head}"
assert git("status", "--porcelain", "--", "hass_mcp_engineering_beta", "scripts", JOURNAL) == ""

key_raw = base64.b64decode(PUBLIC_KEY.read_bytes().strip(), validate=True)
assert sha(key_raw) == PUBLIC_KEY_SHA256
journal_raw = (REPO / JOURNAL).read_bytes()
assert sha(journal_raw) == JOURNAL_SHA256, "production journal is not the reviewed sequence 9"
tip = prepare.parse_journal(journal_raw, Ed25519PublicKey.from_public_bytes(key_raw)).accepted
assert tip.sequence == 9
entries = {e.version: e.to_mapping() for e in tip.entries}
assert sorted(entries) == ["2026.10.0", "2026.10.1", "2026.9.4"]
assert sorted(r.version for r in tip.revocations) == ["2026.9.2", "2026.9.3"]

compiled = [{k: p.to_mapping()[k] for k in REFERENCE_KEYS} for p in CORE_RUNTIME_CAPABILITY_PROFILES]
predecessor = [r for r in compiled if r["capability_id"] != ENTITY_REF["capability_id"]]

for sub in ("inputs", "receipts"):
    (args.out / sub).mkdir(parents=True, exist_ok=True)
manifest = {"model": "hamcp182-receipt-manifest-v1", "run": RUN,
            "verified_at_commit": head,
            "runtime_tree": git("rev-parse", "HEAD:hass_mcp_engineering_beta/ha_mcp_engineering"),
            "slots": {}}

for slot, art in ARTIFACTS.items():
    source = args.receipts / slot
    zip_raw = (args.receipts / f"artifact-{art['artifact_id']}.zip").read_bytes()
    assert sha(zip_raw) == art["zip_sha256"], f"{slot}: artifact zip differs from the GitHub digest"
    names = (f"entity-metadata-{art['lane']}.json", f"alarmo-cleanup-{art['lane']}.json")
    assert sorted(p.name for p in source.iterdir()) == sorted(names)
    summary = acceptance.verify_receipt_files(
        str(source / names[0]), str(source / names[1]),
        core_version=art["core"], support_slot=slot)
    receipt = acceptance.load_receipt((source / names[0]).read_bytes())
    assert receipt["engineering_source"] == {"commit": head, "runtime_tree": manifest["runtime_tree"]}
    assert receipt["added_capability"] == ENTITY_REF
    old = entries[art["core"]]
    # The CI predecessor (21 compiled references) is the production entry's exact list.
    assert old["capabilities"] == predecessor
    assert receipt["predecessor_capabilities_sha256"] == sha(canonical_json(old["capabilities"]))
    assert receipt["image_index_digest"] == old["image_index_digest"]
    target = args.out / "receipts" / slot
    target.mkdir(exist_ok=True)
    hashes = {}
    for name in names:
        shutil.copyfile(source / name, target / name)
        hashes[name] = sha((target / name).read_bytes())
    entity_sha, cleanup_sha = hashes[names[0]], hashes[names[1]]
    assert summary["receipt_sha256"] == entity_sha
    manifest["slots"][slot] = {**art, "files_sha256": hashes, "verifier_summary": summary}

    evidence = canonical_json({
        "model": prepare.EXTENSION_EVIDENCE_MODEL, "version": art["core"],
        "previous_entry_sha256": sha(canonical_json(old)),
        "previous_capabilities": old["capabilities"],
        "added_capabilities": [ENTITY_REF],
        "review_artifacts_sha256": [entity_sha, cleanup_sha, art["zip_sha256"]],
    })
    entry = {**deepcopy(old), "capabilities": deepcopy(old["capabilities"]) + [ENTITY_REF],
             "evidence_sha256": sha(evidence)}
    prepare.require_extension_evidence(json.loads(evidence), old, entry)
    (args.out / f"inputs/evidence-extend-{art['core']}.json").write_bytes(evidence)
    (args.out / f"inputs/entry-extend-{art['core']}.json").write_bytes(canonical_json(entry) + b"\n")

window_raw = (REPO / support_window.WINDOW).read_bytes()
window = support_window.load(REPO)
slots = {s: (None if window["core"][s] is None else window["core"][s]["version"])
         for s in ("candidate", "current", "rollback")}
assert slots == {"candidate": None, "current": "2026.10.1", "rollback": "2026.10.0"}
withdrawn = entries["2026.9.4"]
evidence = canonical_json({
    "model": "core-release-withdrawal-evidence-v1", "operation": "revoke", "task": "HAMCP-182",
    "version": "2026.9.4", "reason": REASON,
    "owner_direction": {"date": "2026-10-14", "owner": "Josh",
                        "decision": "revoke Core 2026.9.4 (outside the support window)"},
    "retained_releases": {"current": "2026.10.1", "rollback": "2026.10.0"},
    "support_window": {"path": support_window.WINDOW, "source_commit": head,
                       "git_blob": git("rev-parse", f"HEAD:{support_window.WINDOW}"),
                       "sha256": sha(window_raw), "core_slots": slots},
    "withdrawn_from": {"journal_path": JOURNAL, "journal_sha256": JOURNAL_SHA256,
                       "registry_sequence": tip.sequence},
    "withdrawn_entry": withdrawn, "withdrawn_entry_sha256": sha(canonical_json(withdrawn)),
    "dependency_review": [
        "compiled_exact_authority('2026.9.4') is empty and no fan/power legacy receipt pins "
        "2026.9.4 in deployed beta.9 (690a00b) or rollback beta.8 (7370ad25); the legacy pin is 2026.9.2.",
        "dashboard_analysis/rules.py CORE_VERSION 2026.9.4 only selects default dashboard-analysis "
        "rules when the observed Core is 2026.9.4; it grants no authority.",
        "f3_dashboard/atomicity.py names 2026.9.4 as static ha-mcp 8.6.0 source evidence; it grants "
        "no authority.",
    ],
    "production_safety_gate": "docs/evidence/hamcp182/core-registry-hamcp182.md (Part 0: deployed "
                              "beta.9 690a00b and rollback beta.8 7370ad25)",
})
(args.out / "inputs/evidence-revoke-2026.9.4.json").write_bytes(evidence)
(args.out / "inputs/entry-revoke-2026.9.4.json").write_bytes(
    canonical_json({**withdrawn, "evidence_sha256": sha(evidence)}) + b"\n")

manifest["inputs_sha256"] = {p.name: sha(p.read_bytes())
                             for p in sorted((args.out / "inputs").iterdir())}
(args.out / "receipts/manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
print(json.dumps(manifest, indent=1, sort_keys=True))
