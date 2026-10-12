"""HAMCP-161: build the canonical 2026.10.1 evidence and the add/revoke entry inputs.

Deterministic: every value comes from the retained public artifacts (--raw), the
repository at the named commits (--repo, read with `git show`) and the current
production journal (--journal, verified with the PUBLIC key only). No key material.

Writes into --out (must not exist):
  evidence-2026.10.1.json, entry-2026.10.1.json,
  evidence-revoke-2026.9.2.json, entry-revoke-2026.9.2.json (copied from --revoke-inputs),
  evidence-revoke-2026.9.3.json, entry-revoke-2026.9.3.json,
  artifacts/ (small public OCI/upstream-diff artifacts referenced by hash)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--repo", type=Path, required=True)
parser.add_argument("--raw", type=Path, required=True)
parser.add_argument("--journal", type=Path, required=True)
parser.add_argument("--public-key", type=Path, required=True)
parser.add_argument("--revoke-inputs", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()

sys.path[:0] = [str(args.repo / "scripts"), str(args.repo / "hass_mcp_engineering_beta")]
import prepare_core_release_registry as prepare  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: E402
from ha_mcp_engineering.signed_registry import canonical_json  # noqa: E402

DEPLOYED = "872685b884bbc6e2b16a48d99516d390dbbd87bd"      # beta.3 build
CI_SOURCE = "5fe5ddd82d4d8363dd9643fdfaee471b13117400"     # main runs on 2026.10.1
PREPARED_ON = "a3aa4d17900895a6d22dbea75346dad1c674f613"   # main, beta.4 source


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*argv: str) -> str:
    return subprocess.run(["git", "-C", str(args.repo), *argv], check=True,
                          capture_output=True, text=True).stdout.strip()


def blob(commit: str, path: str) -> bytes:
    return subprocess.run(["git", "-C", str(args.repo), "show", f"{commit}:{path}"],
                          check=True, capture_output=True).stdout


def tree(commit: str, path: str = "") -> str:
    return git("rev-parse", f"{commit}:{path}" if path else f"{commit}^{{tree}}")


public = Ed25519PublicKey.from_public_bytes(base64.b64decode(
    args.public_key.read_bytes().strip(), validate=True))
journal_raw = args.journal.read_bytes()
tip = prepare.parse_journal(journal_raw, public).accepted
october = {e.version: e.to_mapping() for e in tip.entries}["2026.10.0"]

raw = args.raw
fixture = json.loads(blob(CI_SOURCE, "tests/fixtures/core_2026_10_1_lane_provenance.json"))
index = json.loads((raw / "oci/index-2026.10.1.json").read_bytes())
gitcommit = json.loads((raw / "gitcommit-2026.10.1.json").read_bytes())
release = json.loads((raw / "release-2026.10.1.json").read_bytes())
archive_line = next(line for line in (raw / "archive-hashes.txt").read_text().splitlines()
                    if line.startswith("2026.10.1 tar.gz/"))
archive_sha = archive_line.rsplit("sha256=", 1)[1]
platforms = {f'{m["platform"]["os"]}/{m["platform"]["architecture"]}': m["digest"]
             for m in index["manifests"] if m.get("platform", {}).get("os") != "unknown"}

# Cross-checks between independent sources; any mismatch stops the build.
assert gitcommit["sha"] == fixture["source_commit"] and gitcommit["tree"]["sha"] == fixture["source_tree"]
assert archive_sha == fixture["source_archive_sha256"]
assert platforms == fixture["architecture_manifests"], platforms
assert "sha256:" + sha((raw / "oci/index-2026.10.1.json").read_bytes()) == fixture["image_index_digest"]
assert release["tag_name"] == "2026.10.1" and not release["prerelease"] and not release["draft"]
assert tree(DEPLOYED, "hass_mcp_engineering_beta") == tree(CI_SOURCE, "hass_mcp_engineering_beta")
binding = blob(CI_SOURCE, "scripts/core_lane_bindings.py").decode()
assert '"2026.10.1": CoreBinding("' + fixture["image_index_digest"] in binding
assert "2.4.1-beta.3" in blob(DEPLOYED, "hass_mcp_engineering_beta/config.yaml").decode()

LOGS = {"114912771115": ("38279374013", "schedule", CI_SOURCE),
        "114822698417": ("38252614026", "workflow_dispatch", CI_SOURCE),
        "114926024490": ("38283397548", "workflow_dispatch", PREPARED_ON),
        "114924143102": ("38282769077", "workflow_call (publication gate)", PREPARED_ON)}
for job in LOGS:
    text = (raw / f"ci/logs/job-{job}.log").read_text()
    assert "Real Home Assistant 2026.10.1 contract assertions passed." in text, job
    assert '"authority_transition": [19, 20]' in text, job
    assert "Native automation baseline disposable contract:" in text and '"TOTAL": 124' in text, job
    assert fixture["image_index_digest"] in text and '"arm64_execution": false' in text, job

ARTIFACTS = {
    "oci-index": "oci/index-2026.10.1.json",
    "oci-manifest-amd64": "oci/manifest-amd64.json",
    "oci-manifest-arm64": "oci/manifest-arm64.json",
    "oci-attestation-manifest-amd64": "oci/manifest-att-amd64.json",
    "oci-attestation-manifest-arm64": "oci/manifest-att-arm64.json",
    "oci-slsa-provenance-amd64": "oci/provenance-amd64.json",
    "oci-slsa-provenance-arm64": "oci/provenance-arm64.json",
    "upstream-release": "release-2026.10.1.json",
    "upstream-commit": "gitcommit-2026.10.1.json",
    "upstream-changed-files-2026.10.0..2026.10.1": "changed-files-2026.10.0..2026.10.1.txt",
    "upstream-diffstat-2026.10.0..2026.10.1": "diffstat-2026.10.0..2026.10.1.txt",
    "upstream-flagged-files": "flagged-files.txt",
    "upstream-flagged-diffs": "flagged-diffs.patch",
    **{f"ci-job-log-{job}": f"ci/logs/job-{job}.log" for job in LOGS},
}
COMMITTED = [name for name in ARTIFACTS if not name.startswith("ci-job-log-")]

UNCHANGED = ["hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/device_registry.py",
             "hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/probe_profiles.py",
             "hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/registry.py",
             "hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/source.py",
             "hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/profiles.py"]
for path in UNCHANGED:
    assert blob(DEPLOYED, path) == blob(CI_SOURCE, path), path

changed = (raw / "changed-files-2026.10.0..2026.10.1.txt").read_text().split("\n")
diffstat_tail = (raw / "diffstat-2026.10.0..2026.10.1.txt").read_text().strip().splitlines()[-1].strip()

evidence = {
    "model": "core-exact-release-review-evidence-v1",
    "version": "2026.10.1",
    "core_source": fixture["source_commit"],
    "core_tree": fixture["source_tree"],
    "source_archive_sha256": archive_sha,
    "image_index_digest": fixture["image_index_digest"],
    "architecture_manifests": fixture["architecture_manifests"],
    "upstream_release": {"tag": release["tag_name"], "published_at": release["published_at"],
                         "prerelease": False, "github_commit_verification": "verified (GitHub API; no local GPG check)",
                         "archive_form": "codeload tar.gz of the tag commit"},
    "probe_profile_id": october["probe_profile_id"],
    "probe_profile_sha256": october["probe_profile_sha256"],
    "capabilities": october["capabilities"],
    "engineering_source": CI_SOURCE,
    "engineering_tree": tree(CI_SOURCE, "hass_mcp_engineering_beta"),
    "engineering_version": "2.4.1-beta.3",
    "deployed_engineering_build": DEPLOYED,
    "executable_binding": (
        "hass_mcp_engineering_beta tree " + tree(DEPLOYED, "hass_mcp_engineering_beta")
        + " (runtime package tree " + tree(DEPLOYED, "hass_mcp_engineering_beta/ha_mcp_engineering")
        + ") is identical at the deployed beta.3 build and at the CI source; the commits between "
        "them change only tests, scripts, docs, ci, .github and AGENTS.md."),
    "reviewed_implementation": DEPLOYED,
    "prepared_on": PREPARED_ON,
    "ha_mcp_source": "311d6dc273fb4e9a5b8cde0de15f69472a64fe44",
    "ha_mcp_version": "8.5.0",
    "executed_evidence": {
        "ci_runs": [{"run_id": r, "job_id": j, "event": e, "commit": c}
                    for j, (r, e, c) in LOGS.items()],
        "support_slot": "candidate",
        "exact_image_record": "PASS",
        "real_ha_contract": "PASS",
        "lane_test_authority_admitted": 19,
        "alarmo": "PASS",
        "alarmo_authority_transition": [19, 20],
        "alarmo_observer_negative_control": "PASS",
        "baseline": "PASS",
        "baseline_authority_transition": [20, 21],
        "baseline_comparison_total": 124,
        "script_dependency_contract": "PASS",
        "typed_fan_contract": "PASS",
        "profile_coverage": len(october["capabilities"]),
        "arm64_execution": False,
    },
    "upstream_changes_from_2026.10.0": {
        "commits": 71, "diffstat": diffstat_tail,
        "changed_paths": len([line for line in changed if line.strip()]),
        "flagged": [
            "auth indieauth/login_flow (#185544): loopback redirect port relaxation; PKCE S256 only. Not an Engineering read or write surface.",
            "automation/trace.py (#185161): traces stored only for automations with an id. Affects trace reads of id-less automations; no signed capability depends on it.",
            "trace/models.py (#185207): trace model change; same scope as above.",
            "helpers/template/extensions/devices.py (#185361): deprecated device template attributes restored. Template rendering only.",
        ],
        "unchanged_relied_on": ["helpers/device_registry.py", "helpers/entity_registry.py",
                                "helpers/area_registry.py", "helpers/floor_registry.py",
                                "helpers/label_registry.py", "helpers/config_validation.py",
                                "helpers/service.py", "components/websocket_api",
                                "components/config", "components/lovelace", "components/script",
                                "components/input_boolean", "components/input_number",
                                "components/fan", "components/switch", "components/light",
                                "components/api", "components/homeassistant", "components/search",
                                "components/http", "core.py", "loader.py"],
    },
    "review_artifacts_sha256": {name: sha((raw / path).read_bytes()) for name, path in ARTIFACTS.items()},
    "unchanged_implementation_files": {path: sha(blob(DEPLOYED, path)) for path in UNCHANGED},
    "limits": [
        "Execution is the full-tier CI candidate-slot lane on main 5fe5ddd and a3aa4d1; no run executed 2026.10.1 on the deployed build commit itself. Binding is by identical Engineering package tree.",
        "Arm64 provenance and manifest are bound, but arm64 execution is not established (amd64 only).",
        "OCI attestation and SLSA provenance signatures were not cryptographically verified; digests and declared source revision are recorded as observed.",
        "The image label io.hass.base.version (2026.06.1) differs from the provenance base (2026.07.0); recorded as an observation, not relied on.",
        "The baseline printed summary omits authority_transition; [20, 21] is asserted by scripts/automation_baseline_contract_acceptance.py at the CI source.",
        "Upstream 2026.10.1 changes automation tracing for id-less automations and IndieAuth redirect handling; neither is a signed capability surface.",
        "No household physical effects, Supervisor lifecycle, installed add-on continuity on 2026.10.1, all-custom-integration coverage or ha-mcp 8.6.x claim.",
        "core.entity_registry_metadata_operation is NOT included: no disposable container contract for it exists (HAMCP-161 item (c) stopped).",
    ],
}

entry = {
    "entry_id": "ha-core-2026.10.1-reviewed-v1",
    "server_name": october["server_name"],
    "version": "2026.10.1",
    "source_commit": fixture["source_commit"],
    "source_tree": fixture["source_tree"],
    "source_archive_sha256": archive_sha,
    "image_index_digest": fixture["image_index_digest"],
    "architecture_manifests": fixture["architecture_manifests"],
    "evidence_sha256": None,
    "probe_profile_id": october["probe_profile_id"],
    "probe_profile_sha256": october["probe_profile_sha256"],
    "capabilities": october["capabilities"],
}
evidence_bytes = canonical_json(evidence)  # canonical, no newline, like the 2026.10.0 evidence
entry["evidence_sha256"] = sha(evidence_bytes)

args.out.mkdir(parents=True, exist_ok=False)
(args.out / "evidence-2026.10.1.json").write_bytes(evidence_bytes)
(args.out / "entry-2026.10.1.json").write_bytes(canonical_json(entry) + b"\n")
for version in ("2026.9.2", "2026.9.3"):
    for kind in ("evidence", "entry"):
        name = f"{kind}-revoke-{version}.json"
        shutil.copyfile(args.revoke_inputs / name, args.out / name)
    entry_v = json.loads((args.out / f"entry-revoke-{version}.json").read_bytes())
    assert entry_v["evidence_sha256"] == sha((args.out / f"evidence-revoke-{version}.json").read_bytes())
(args.out / "artifacts").mkdir()
for name in COMMITTED:
    path = ARTIFACTS[name]
    shutil.copyfile(raw / path, args.out / "artifacts" / Path(path).name)
print(json.dumps({"evidence_sha256": entry["evidence_sha256"],
                  "entry_sha256": sha(canonical_json(entry) + b"\n"),
                  "capabilities": len(entry["capabilities"])}, indent=1))
