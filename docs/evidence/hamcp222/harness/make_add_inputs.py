"""HAMCP-222: build the Core 2026.10.3 add inputs (sequence 14, prepared after sequence 13 is signed).

Deterministic and key-free. Every value comes from:
  --add103     the exact-release collection (raw/ public GitHub and GHCR artifacts with
               SHA256SUMS, diff/, assessment.json and tools/; method in its REPORT.md);
  --artifacts  the retained run 40148395528 attempt 2 artifacts and job logs (as for
               make_inputs.py);
  --repo       this checkout (read with git), including the seq-13 receipt manifest that
               make_inputs.py wrote and verified with the shipped offline verifiers.
Nothing here reads, requests or needs a private key, and no candidate is prepared: the
sequence-14 candidate depends on the signed sequence-13 journal bytes, so the owner
prepares it with the shipped CLI after signing step 1 (OWNER-SIGNING-RUNBOOK.md step 2).

Writes into --out (docs/evidence/hamcp222); every output path must be new:
  inputs/evidence-add-2026.10.3.json   canonical core-exact-release-review-evidence-v1 (no newline)
  inputs/entry-add-2026.10.3.json      the entry to add: 26 references, evidence bound by SHA256
  artifacts-2026.10.3/                 the small public artifacts the evidence binds by hash
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--repo", type=Path, required=True)
parser.add_argument("--add103", type=Path, required=True)
parser.add_argument("--artifacts", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()

REPO = args.repo.resolve()
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "hass_mcp_engineering_beta")]
import core_lane_bindings  # noqa: E402
import entity_metadata_contract_acceptance as entity_acceptance  # noqa: E402
import prepare_core_release_registry as prepare  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.probe_profiles import known_probe_profile  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_RUNTIME_CAPABILITY_PROFILES)
from ha_mcp_engineering.ha_core_readmission.registry_models import CoreReleaseEntry  # noqa: E402
from ha_mcp_engineering.signed_registry import canonical_json  # noqa: E402

VERSION = "2026.10.3"
ASSESSMENT_SHA256 = "a4315011f1730aaea5af50118e1c28115a6d0c25bf3c1b9c5944802a81f398ec"
ASSERT_RESULT_SHA256 = "ed4289a0a006cf08eb398b0b1da0c2456e61230a71806c7e98ba93f82db02d2e"
CAPABILITIES_SHA256 = "fa8bc43fa6a218b6883ad0eb7c2ce15543dadb92f122f10e53f6a7e7a569b01a"
RUNTIME_TREE = "70479fa2958aafd36d0e74e3082f98c50b11053c"  # v2.4.1-beta.36 ha_mcp_engineering
ENGINEERING_TREE = "1788d45fbeb3f5f9b8832cc735fbc34547dae926"  # v2.4.1-beta.36 hass_mcp_engineering_beta
RELEASE_TAG = "v2.4.1-beta.36"
MERGE_SHA = "8c9b44e845a0a9404e0f817fb7616b005c7a923a"  # run 40148395528 PR #306 merge ref
JOB = {"run_id": 40148395528, "run_attempt": 2, "job_id": 121050685050,
       "event": "pull_request", "pull_request": 306, "commit": MERGE_SHA,
       "job": "Real HA ha-2026-10-3 (candidate)"}
JOB_LOG = f"realha-{JOB['job_id']}.log"
HA_MCP = {"ha_mcp_source": "311d6dc273fb4e9a5b8cde0de15f69472a64fe44", "ha_mcp_version": "8.5.0"}
FIXTURE = "tests/fixtures/core_2026_10_3_lane_provenance.json"
JOURNAL = "upstream-trust/ha-core-release-registry.json"
REFERENCE_KEYS = entity_acceptance.REFERENCE_KEYS
# Public artifacts copied into the repository (archives, compare and commit API dumps and
# HTTP headers stay in the retained collection, bound by hash in review_artifacts_sha256).
COMMITTED = ["raw/gitcommit-2026.10.3.json", "raw/ref-2026.10.3.json", "raw/release-2026.10.3.json",
             "raw/release-2026.10.3-pr-numbers.txt", "raw/ref-2026.10.2.json",
             "raw/release-2026.10.2.json", "raw/release-2026.10.2-pr-numbers.txt",
             "raw/fetched-at.txt", "raw/SHA256SUMS",
             "raw/oci/index-2026.10.3.json", "raw/oci/manifest-amd64.json",
             "raw/oci/manifest-arm64.json", "raw/oci/manifest-att-amd64.json",
             "raw/oci/manifest-att-arm64.json", "raw/oci/provenance-amd64.json",
             "raw/oci/provenance-arm64.json", "raw/oci/config-amd64.json",
             "raw/oci/config-arm64.json",
             "diff/changed-files-2026.10.1..2026.10.3.txt", "diff/diffstat-2026.10.1..2026.10.3.txt",
             "diff/flagged-files.txt", "diff/flagged-diffs.patch",
             "assessment.json", "tools/assert-result.json", "tools/capabilities.json",
             "tools/make_assessment.py", "tools/build_assessment_json.py", "tools/ghcr_get.sh"]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*argv: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *argv], check=True, capture_output=True,
                          text=True).stdout.strip()


def ref(profile) -> dict:
    return {k: profile.to_mapping()[k] for k in REFERENCE_KEYS}


add103 = args.add103

# 1. The collection: every raw file by SHA256SUMS, the assessment and its inputs by pinned hash.
sums = {}
for line in (add103 / "raw/SHA256SUMS").read_text().splitlines():
    digest, name = line.split("  ", 1)
    assert sha((add103 / "raw" / name).read_bytes()) == digest, name
    sums[name] = digest
assert len(sums) == 33
assessment_raw = (add103 / "assessment.json").read_bytes()
assert sha(assessment_raw) == ASSESSMENT_SHA256
assert sha((add103 / "tools/assert-result.json").read_bytes()) == ASSERT_RESULT_SHA256
assert sha((add103 / "tools/capabilities.json").read_bytes()) == CAPABILITIES_SHA256
assessment = json.loads(assessment_raw)
assert assessment["failures"] == [] and assessment["version"] == VERSION
assert assessment["review_artifacts_sha256"] == {k: v for k, v in sums.items()}
for name, digest in assessment["diff_artifacts_sha256"].items():
    assert sha((add103 / "diff" / name).read_bytes()) == digest, name

# 2. Independent identity sources agree: lane fixture, lane binding, GitHub, GHCR.
fixture = json.loads(git("show", f"HEAD:{FIXTURE}"))
binding = core_lane_bindings.reviewed_lane_binding(VERSION)
assert binding.image_index_digest == fixture["image_index_digest"] == assessment["image_index_digest"]
assert binding.device_registry == core_lane_bindings.CHILD_DEVICE_NAME_PARTS
assert core_lane_bindings.reviewed_lane_binding("2026.10.1").device_registry == binding.device_registry
for key, value in (("source_commit", "core_source"), ("source_tree", "core_tree"),
                   ("source_archive_sha256", "source_archive_sha256"),
                   ("architecture_manifests", "architecture_manifests")):
    assert fixture[key] == assessment[value], key
assert "sha256:" + sums["oci/index-2026.10.3.json"] == fixture["image_index_digest"]
assert sums["archives/core-2026.10.3-tar.gz"] == fixture["source_archive_sha256"]
index = json.loads((add103 / "raw/oci/index-2026.10.3.json").read_bytes())
platforms = {f'{m["platform"]["os"]}/{m["platform"]["architecture"]}': m["digest"]
             for m in index["manifests"] if m.get("platform", {}).get("os") != "unknown"}
assert platforms == fixture["architecture_manifests"]
gitcommit = json.loads((add103 / "raw/gitcommit-2026.10.3.json").read_bytes())
assert gitcommit["sha"] == fixture["source_commit"] and gitcommit["tree"]["sha"] == fixture["source_tree"]
release = json.loads((add103 / "raw/release-2026.10.3.json").read_bytes())
assert release["tag_name"] == VERSION and not release["prerelease"] and not release["draft"]

# 3. Engineering: the checkout is the beta.36 runtime the receipts bind and the assessment read.
for rev in ("HEAD", RELEASE_TAG, MERGE_SHA):
    assert git("rev-parse", f"{rev}:hass_mcp_engineering_beta/ha_mcp_engineering") == RUNTIME_TREE, rev
    assert git("rev-parse", f"{rev}:hass_mcp_engineering_beta") == ENGINEERING_TREE, rev
assert assessment["engineering_checkout"]["runtime_package_tree"] == RUNTIME_TREE
assert assessment["engineering_checkout"]["hass_mcp_engineering_beta_tree"] == ENGINEERING_TREE
assert assessment["engineering_checkout"]["config_version"] == "2.4.1-beta.36"
# Outside docs/, the CI merge ref differs from this package only by the lane switch.
assert git("diff", "--name-only", MERGE_SHA, "HEAD", "--", ".", ":!docs").splitlines() \
    == [".github/workflows/ci.yml", "tests/test_alarmo_interval_observer.py"]
assert git("status", "--porcelain", "--", "hass_mcp_engineering_beta", "scripts", "tests",
           ".github", JOURNAL) == ""
compiled = [ref(p) for p in CORE_RUNTIME_CAPABILITY_PROFILES]
assert len(compiled) == 26 and all(p.auto_eligible for p in CORE_RUNTIME_CAPABILITY_PROFILES)
assert [{k: c[k] for k in a} for c, a in zip(compiled, assessment["capabilities"], strict=True)] \
    == assessment["capabilities"]
assert list(assessment["capability_status"]) == [c["capability_id"] for c in compiled]

# The probe profile is the one the signed 2026.10.1 entry uses: the same reviewed
# device-registry contract (lane binding above) and a compiled known profile.
journal = json.loads((REPO / JOURNAL).read_bytes())
signed_101 = next(e for e in journal["envelopes"][-1]["entries"] if e["version"] == "2026.10.1")
probe = (signed_101["probe_profile_id"], signed_101["probe_profile_sha256"])
assert known_probe_profile(*probe) is not None

# 4. Execution: the 2026.10.3 candidate job of the clean run, and the receipts make_inputs.py verified.
manifest = json.loads((REPO / "docs/evidence/hamcp222/receipts/manifest.json").read_bytes())
assert manifest["run"]["run_id"] == JOB["run_id"] and manifest["run"]["run_attempt"] == JOB["run_attempt"]
assert manifest["run"]["merge_sha"] == MERGE_SHA and manifest["runtime_tree"] == RUNTIME_TREE
core = manifest["cores"][VERSION]
assert core["slot"] == "candidate"
for name, digest in core["files_sha256"].items():
    assert sha((REPO / "docs/evidence/hamcp222/receipts" / VERSION / name).read_bytes()) == digest, name
summaries = core["verifier_summaries"]
assert all(s["result"] == "PASS" and s["core"] == VERSION and s["support_slot"] == "candidate"
           for s in summaries.values())
log_raw = (args.artifacts / JOB_LOG).read_bytes()
log = log_raw.decode()
for needle in (f"Real Home Assistant {VERSION} contract assertions passed.",
               "##[group]Run python scripts/real_ha_contract_tests.py",
               '"arm64_execution": false',
               f'"script_dependency_contract": "PASS"',
               f'"core": "{VERSION}", "upstream": "8.5.0"',
               '{"typed_fan_contract": "PASS"',
               '"TOTAL": 124',
               fixture["image_index_digest"]):
    assert needle in log, needle
for prefix, transition in (("Alarmo disposable inspection contract:", [19, 20]),
                           ("Entity metadata disposable contract:", [21, 22]),
                           ("Seq-13 label_registry disposable contract:", [22, 23]),
                           ("Seq-13 availability_exposure disposable contract:", [22, 24]),
                           ("Configuration delete disposable contract:", [22, 23])):
    line = next(x for x in log.splitlines() if prefix in x)
    printed = json.loads(line.split(prefix, 1)[1])
    assert printed["authority_transition"] == transition and printed["core"] == VERSION, prefix
    assert printed.get("result", "PASS") == "PASS", prefix
# The outer harness summary: every required lane passed and nothing it owned remains.
integration = json.loads(next(x for x in log.splitlines()
                              if x[29:].startswith('{"core": "2026.10.3", "discarded_windows"'))[29:])
assert integration["result"] == "PASS" and integration["support_slot"] == "candidate"
assert integration["owned_containers_remaining"] == 0 and integration["owned_network_remaining"] is False
assert integration["required_alarmo_interval"] == "PASS"
assert all(integration[k]["result"] == "PASS" for k in (
    "required_automation_baseline", "required_entity_metadata", "required_seq13_configuration_delete"))
assert integration["required_entity_metadata"]["receipt_sha256"] == core["files_sha256"]["entity-metadata-ha-2026-10-3.json"]
assert integration["required_seq13_configuration_delete"]["receipt_sha256"] \
    == core["files_sha256"]["seq13-configuration-delete-ha-2026-10-3.json"]
assert summaries["label_registry"]["authority_transition"] == [22, 23]
assert summaries["availability_exposure"]["authority_transition"] == [22, 24]
assert summaries["configuration_delete"]["authority_transition"] == [22, 23]
assert summaries["entity_metadata"]["authority_transition"] == [21, 22]

# 5. Evidence and entry.
carried = {k: assessment[k] for k in (
    "core_source", "core_tree", "source_archive_sha256", "comparison_base", "image_index_digest",
    "architecture_manifests", "oci", "upstream_release", "upstream_changes_from_2026.10.1",
    "capability_status", "selected_capability_assessment", "cited", "same_blob",
    "engineering_cited", "engineering_negative_grep", "engineering_checkout", "cross_checks",
    "diff_artifacts_sha256")}
evidence = {
    "model": "core-exact-release-review-evidence-v1",
    "version": VERSION,
    **carried,
    "probe_profile_id": probe[0],
    "probe_profile_sha256": probe[1],
    "capabilities": compiled,
    "engineering_source": MERGE_SHA,
    "engineering_tree": ENGINEERING_TREE,
    "engineering_version": "2.4.1-beta.36",
    "deployed_engineering_build": git("rev-parse", f"{RELEASE_TAG}^{{commit}}"),
    "executable_binding": (
        f"hass_mcp_engineering_beta tree {ENGINEERING_TREE} (runtime package tree {RUNTIME_TREE}) is "
        f"identical at {RELEASE_TAG} and at the CI merge ref {MERGE_SHA} of PR #306. Outside docs/, that "
        "merge ref differs from this signing package (PR #305 merged with main) only by enabling the "
        "dormant seq-13 lanes in .github/workflows/ci.yml and the test that pins that switch."),
    "reviewed_implementation": git("rev-parse", f"{RELEASE_TAG}^{{commit}}"),
    **HA_MCP,
    "executed_evidence": {
        "ci_runs": [JOB],
        "job_log_sha256": sha(log_raw),
        "support_slot": "candidate",
        "real_ha_contract": "PASS",
        "integration_summary": integration,
        "alarmo": "PASS",
        "alarmo_authority_transition": [19, 20],
        "baseline": "PASS",
        "baseline_comparison_total": 124,
        "entity_metadata": summaries["entity_metadata"],
        "label_registry": summaries["label_registry"],
        "availability_exposure": summaries["availability_exposure"],
        "configuration_delete": summaries["configuration_delete"],
        "script_dependency_contract": "PASS",
        "typed_fan_contract": "PASS",
        "receipts_sha256": core["files_sha256"],
        "artifact_zip_sha256": core["zip_sha256"],
        "profile_coverage": len(compiled),
        "arm64_execution": False,
    },
    "owner_decisions": [
        "2026-10-25 (Josh): accept that the real-Core delete lane proves only the elevated "
        "(protected, high-risk) delete path. The lane's dependency runtime has no script reader, so "
        "every plan carries dependency_evidence_incomplete:script_evidence_unavailable and the "
        "OD-4 incomplete-evidence rule marks it protected. The standard (medium) path, used in "
        "production when the ha-mcp script reader is admitted, is covered offline only. A lane "
        "script reader is a later follow-up.",
    ],
    "review_artifacts_sha256": {**{k: v for k, v in sums.items()},
                                **{f"diff/{k}": v for k, v in assessment["diff_artifacts_sha256"].items()},
                                "assessment.json": ASSESSMENT_SHA256,
                                "tools/assert-result.json": ASSERT_RESULT_SHA256,
                                "tools/capabilities.json": CAPABILITIES_SHA256,
                                f"ci/{JOB_LOG}": sha(log_raw)},
    "limits": [
        "Execution is one pull_request CI run (40148395528 attempt 2) of PR #306, which differs from this "
        "package outside docs/ only by enabling the dormant seq-13 lanes; binding to the beta.36 build is "
        "by identical Engineering package tree. The 2026.10.3 lane ran in the candidate slot.",
        "Arm64 provenance and manifest are bound, but arm64 execution is not established (amd64 only).",
        *assessment["limits"][1:4],
        assessment["limits"][4],
        assessment["limits"][5],
        "The real-Core delete lane proves only the elevated delete path (owner decision 2026-10-25); the "
        "standard path is covered offline only.",
        assessment["limits"][6],
    ],
}
assert evidence["limits"][2].startswith("OCI attestation")
assert not any("NOT included" in limit for limit in evidence["limits"])
evidence_bytes = canonical_json(evidence)  # canonical, no newline, like 2026.10.1
assert len(evidence_bytes) <= prepare.MAX_EVIDENCE_BYTES
entry = {
    "entry_id": f"ha-core-{VERSION}-reviewed-v1",
    "server_name": signed_101["server_name"],
    "version": VERSION,
    "source_commit": fixture["source_commit"],
    "source_tree": fixture["source_tree"],
    "source_archive_sha256": fixture["source_archive_sha256"],
    "image_index_digest": fixture["image_index_digest"],
    "architecture_manifests": fixture["architecture_manifests"],
    "evidence_sha256": sha(evidence_bytes),
    "probe_profile_id": probe[0],
    "probe_profile_sha256": probe[1],
    "capabilities": compiled,
}
parsed = CoreReleaseEntry.from_mapping(entry)
prepare.require_known_contracts(parsed)
assert parsed.to_mapping() == entry

(args.out / "inputs").mkdir(parents=True, exist_ok=True)
prepare.write_new(args.out / f"inputs/evidence-add-{VERSION}.json", evidence_bytes)
prepare.write_new(args.out / f"inputs/entry-add-{VERSION}.json", canonical_json(entry) + b"\n")
target = args.out / f"artifacts-{VERSION}"
target.mkdir()
for name in COMMITTED:
    destination = target / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(add103 / name, destination)
print(json.dumps({"evidence_sha256": entry["evidence_sha256"],
                  "entry_sha256": sha(canonical_json(entry) + b"\n"),
                  "references": len(entry["capabilities"]),
                  "job_log_sha256": sha(log_raw)}, indent=1, sort_keys=True))
