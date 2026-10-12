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
import tarfile

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
    assert ('{"script_dependency_contract": "PASS"' in text
            and '"core": "2026.10.1", "upstream": "8.5.0"' in text), job
    assert "##[group]Run python scripts/real_ha_contract_tests.py\n" in text, job

# R1: every upstream line the capability assessment cites is checked in the
# hash-bound Core archives (2026.10.0 by its signed entry, 2026.10.1 by the
# lane fixture). Each path pins its git blob id; each line pins its exact text.
CORE_ARCHIVES = {"2026.10.0": ("archives/core-2026.10.0-tar.gz", october["source_archive_sha256"]),
                 "2026.10.1": ("archives/core-2026.10.1-tar.gz", archive_sha)}
CITED = {
    ("2026.10.0", "homeassistant/components/automation/trace.py", "c440624b7f68882cdda480eb85be5cd669ae6aaf"): {
        67: "async_store_trace(hass, trace, trace_config[CONF_STORED_TRACES])"},
    ("2026.10.1", "homeassistant/components/automation/trace.py", "8e42028047feeb38b4f09047de71f2c21f9730f4"): {
        53: "def trace_automation(", 67: "if automation_id:",
        68: "async_store_trace(hass, trace, trace_config[CONF_STORED_TRACES])"},
    ("2026.10.0", "homeassistant/components/trace/models.py", "89b2d2d3ddd6ed5580081aacdaaa676f2cf1bef0"): {
        93: 'self.key = f"{self._domain}.{item_id}"', 96: "if trace_id_get():",
        97: "trace_set_child_id(self.key, self.run_id)"},
    ("2026.10.1", "homeassistant/components/trace/models.py", "d3a42ede803d8b88ddc6b75ceb007aa4dbaee4ef"): {
        93: 'self.key = f"{self._domain}.{item_id}"', 97: "if item_id and trace_id_get():",
        98: "trace_set_child_id(self.key, self.run_id)"},
    ("2026.10.1", "homeassistant/components/automation/__init__.py", "9cf80728504bf97fe38d6865ef1a28a7ebb7b4a0"): {
        403: "self._attr_unique_id = automation_id", 515: "self._attr_unique_id = automation_id",
        704: "with trace_automation(", 706: "self.unique_id,",
        926: "with trace_automation(", 928: "self.unique_id,"},
    ("2026.10.1", "homeassistant/components/trace/websocket_api.py", "7eddc5326601fbc3a3ea7e488c7cc165bc3d696b"): {
        59: "async def websocket_trace_get(",
        72: 'msg["id"], websocket_api.ERR_NOT_FOUND, "The trace could not be found"',
        92: "async def websocket_trace_list("},
    ("2026.10.1", "homeassistant/helpers/trace.py", "7255c562924dc882111ddef4a8b1d5015999ac82"): {
        51: "def set_child_id(self, child_key: str, child_run_id: str) -> None:",
        112: 'result["child_id"] = {',
        279: "def trace_set_child_id(child_key: str, child_run_id: str) -> None:"},
    ("2026.10.0", "homeassistant/helpers/template/extensions/devices.py", "8d79983628ddf0ac5c1ebb2a0954a1e8a3ad50e1"): {
        117: "def device_attr(self, device_or_entity_id: str, attr_name: str) -> Any:",
        130: "if device is None or not hasattr(device, attr_name):",
        132: "return getattr(device, attr_name)"},
    ("2026.10.1", "homeassistant/helpers/template/extensions/devices.py", "a10a33746371fe3745aff32086e19ac97754510a"): {
        33: "_DEPRECATED_DEVICE_ATTRS: dict[str, tuple[str, Callable[[dr.BaseDeviceEntry], Any]]] = {",
        144: "def device_attr(self, device_or_entity_id: str, attr_name: str) -> Any:",
        160: "if not device.is_composite_device and (",
        174: "return resolve(device)",
        180: "def is_device_attr("},
    ("2026.10.1", "homeassistant/helpers/device_registry.py", "52ab4273c2ddcb5447df4192ef0dda55739b7d86"): {
        384: "def _report_deprecated_config_entries_property(",
        394: "core_behavior=ReportBehavior.ERROR,",
        429: "def config_entries(self) -> set[str]:",
        442: "def config_entries_subentries(self) -> dict[str, set[str | None]]:",
        459: "def primary_config_entry(self) -> str:",
        473: "def is_composite_device(self) -> bool:"},
    ("2026.10.1", "homeassistant/helpers/frame.py", "3734871ec81872b1b30fa52685164238b230cec3"): {
        373: "def _report_usage_no_integration(", 386: "raise RuntimeError(msg) from err"},
    ("2026.10.1", "homeassistant/components/api/__init__.py", "a66f75403c21c1dd64771713d60704502de0ee81"): {
        497: "class APITemplateView(HomeAssistantView):",
        519: 'return tpl.async_render(variables=data.get("variables"), parse_result=False)  # type: ignore[no-any-return]'},
    ("2026.10.1", "homeassistant/helpers/template/__init__.py", "11b51a84021f7b140d65b48911b0a88ffdabcd6c"): {},
}
# Unchanged between the tags: the same blob must be at both.
SAME_BLOB = ["homeassistant/components/automation/__init__.py",
             "homeassistant/components/trace/websocket_api.py", "homeassistant/helpers/trace.py",
             "homeassistant/helpers/device_registry.py", "homeassistant/helpers/frame.py",
             "homeassistant/components/api/__init__.py", "homeassistant/helpers/template/__init__.py"]


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


core_files: dict[tuple[str, str], bytes] = {}
for version, (path, expected) in CORE_ARCHIVES.items():
    assert sha((raw / path).read_bytes()) == expected, path
    with tarfile.open(raw / path) as archive:
        members = {m.name.split("/", 1)[1]: m for m in archive.getmembers() if "/" in m.name}
        for name in {p for (_, p, _) in CITED} | set(SAME_BLOB):
            core_files[version, name] = archive.extractfile(members[name]).read()
for (version, path, blob_id), lines in CITED.items():
    data = core_files[version, path]
    assert git_blob_id(data) == blob_id, (version, path)
    text_lines = data.decode().splitlines()
    for number, expected in lines.items():
        assert text_lines[number - 1].strip() == expected, (version, path, number)
for path in SAME_BLOB:
    assert core_files["2026.10.0", path] == core_files["2026.10.1", path], path


def cite(version: str, path: str, *numbers: int) -> str:
    blob_id = next(b for (v, p, b) in CITED if v == version and p == path)
    assert all(n in CITED[version, path, blob_id] for n in numbers), (version, path, numbers)
    return f"core {version}:{path}@{blob_id[:12]}" + (":" + ",".join(map(str, numbers)) if numbers else "")


# Engineering lines cited by the assessment, at the deployed beta.3 build that consumes
# the journal (same bytes at the CI source by tree identity).
ENGINEERING_PACKAGE = "hass_mcp_engineering_beta/ha_mcp_engineering/"
ENGINEERING_CITED = {
    "ha_core_readmission/profiles.py": {260: '"core.automation_trace_read",', 297: '"core.template_semantics",',
                                        301: '"template_source_release_supported",'},
    "ha_core_readmission/source.py": {223: '"core.automation_trace_read",',
                                      251: 'add("core.template_semantics", websocket_ok, semantic=True)'},
    "ha_core_readmission/routes.py": {74: '"ha_eval_template": ("core.template_semantics",),',
                                      75: '"ha_get_automation_traces": ("core.automation_trace_read",),',
                                      105: '"render_template": ("core.template_semantics",),',
                                      106: '"list_automation_traces": ("core.automation_trace_read",),',
                                      107: '"get_automation_trace": ("core.automation_trace_read",),'},
    "trace_normalization.py": {67: '{"type": "trace/list", "domain": "automation", "item_id": automation_id}'},
    "tools/compatibility.py": {502: 'return await rest("POST", "/template", {"template": template}, raw=True)',
                               506: "async def list_automation_traces(automation_id: str) -> str:",
                               520: "async def get_automation_trace(automation_id: str, run_id: str) -> str:"},
    "reliability/provider.py": {123: '"GET", f"/config/automation/config/{automation_id}"',
                                589: "def _normalize_trace(summary: dict[str, Any], detail: Any) -> dict[str, Any]:"},
    "dependency/provider.py": {399: 'return state, None, "automation_id_missing"'},
    "incident/service.py": {44: 'AUTOMATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")'},
    "handoff/service.py": {38: 'AUTOMATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")'},
    "dependency/template_semantic_registry.json": {10: '"parse_only": true',
                                                   244: '"device_attr": "attribute_item_access",',
                                                   259: '"is_device_attr": "attribute_item_access",'},
    "dependency/obligation_ledger.py": {2466: 'reason=f"{name}_registry_lookup_opaque",'},
}
for path, lines in ENGINEERING_CITED.items():
    data = blob(DEPLOYED, ENGINEERING_PACKAGE + path)
    assert data == blob(CI_SOURCE, ENGINEERING_PACKAGE + path), path
    text_lines = data.decode().splitlines()
    for number, expected in lines.items():
        assert text_lines[number - 1].strip() == expected, (path, number)


def ecite(path: str, *numbers: int) -> str:
    assert all(n in ENGINEERING_CITED[path] for n in numbers), (path, numbers)
    return f"engineering {ENGINEERING_PACKAGE}{path}:" + ",".join(map(str, numbers))


CONTRACT_SCRIPT = "scripts/real_ha_contract_tests.py"
contract_lines = blob(CI_SOURCE, CONTRACT_SCRIPT).decode().splitlines()
assert blob(CI_SOURCE, CONTRACT_SCRIPT) == blob(PREPARED_ON, CONTRACT_SCRIPT)
for number, expected in {140: '"automation": "dev14_real_contract_automation",',
                         3196: "async def _run_script_dependency_contract(configured, core_runtime, read_gateway):",
                         3215: 'bodies[keys[4]]["sequence"].append({"condition": "template", "value_template":',
                         3966: 'phase = "trace_generation"',
                         3976: "normalized = await fetch_normalized_trace_list(",
                         3990: '"type": "trace/get",', 3992: '"item_id": RESOURCE_IDS["automation"],',
                         3998: 'assert isinstance(detail.get("config"), dict)'}.items():
    assert contract_lines[number - 1].strip() == expected, (CONTRACT_SCRIPT, number)

REAL_HA_STEP = ("CI step 'Run real Home Assistant contracts' (python scripts/real_ha_contract_tests.py, "
                "Core 2026.10.1 candidate slot, ha-mcp 8.5.0) in every job of executed_evidence.ci_runs")

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
            "automation/trace.py (#185161): traces stored only for automations with an id. Selected reference core.automation_trace_read: assessed in selected_capability_assessment.",
            "trace/models.py (#185207): no child link to an id-less automation run. Selected reference core.automation_trace_read: assessed in selected_capability_assessment.",
            "helpers/template/extensions/devices.py (#185361): deprecated device template attributes restored. Selected reference core.template_semantics: assessed in selected_capability_assessment.",
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
    "selected_capability_assessment": {
        "citation_form": ("core <tag>:<path>@<git blob id prefix>:<lines>, verified in the hash-bound Core "
                          "archives; engineering <path>:<lines> at the deployed build (byte-identical at the CI "
                          "source). harness/make_inputs.py asserts every cited line's text."),
        "core.automation_trace_read": {
            "contract": [
                "Bounded trace/list headers and trace/get for an automation's config id; a missing trace is not_found: "
                + ecite("ha_core_readmission/profiles.py", 260) + "; admission probe " + ecite("ha_core_readmission/source.py", 223) + ".",
                "Consumers: list_automation_traces, get_automation_trace (" + ecite("ha_core_readmission/routes.py", 106, 107)
                + "; " + ecite("tools/compatibility.py", 506, 520) + ", documented to take the automation's config 'id'); "
                "delegated ha_get_automation_traces (" + ecite("ha_core_readmission/routes.py", 75) + "); shared list transport "
                + ecite("trace_normalization.py", 67) + ".",
                "automation_reliability_analysis and incident_correlation fetch /config/automation/config/{id} before any trace read ("
                + ecite("reliability/provider.py", 123) + "), and both incident and handoff accept only an id pattern ("
                + ecite("incident/service.py", 44) + "; " + ecite("handoff/service.py", 38) + "). change_impact_analysis reads "
                "traces only for an automation with an 'id' attribute; without one it records automation_id_missing ("
                + ecite("dependency/provider.py", 399) + ").",
            ],
            "upstream_change": [
                "2026.10.0 stored every run (" + cite("2026.10.0", "homeassistant/components/automation/trace.py", 67)
                + "). An automation without an id has unique_id None (" + cite("2026.10.1", "homeassistant/components/automation/__init__.py", 403, 515)
                + ", passed at 704-706 and 926-928), so its runs were stored under the key 'automation.None' ("
                + cite("2026.10.0", "homeassistant/components/trace/models.py", 93) + ").",
                "2026.10.1 stores a run only when the automation has an id (" + cite("2026.10.1", "homeassistant/components/automation/trace.py", 67, 68)
                + ") and links a parent run to a child only when the child has an id ("
                + cite("2026.10.1", "homeassistant/components/trace/models.py", 97, 98) + "; 2026.10.0: "
                + cite("2026.10.0", "homeassistant/components/trace/models.py", 96, 97) + ").",
                "Unchanged: trace/get, trace/list and not_found (" + cite("2026.10.1", "homeassistant/components/trace/websocket_api.py", 59, 72, 92)
                + "), child_id serialization (" + cite("2026.10.1", "homeassistant/helpers/trace.py", 51, 112, 279) + ").",
            ],
            "assessment": [
                "Id-bearing automations: unchanged. Same store call, key, child link and WebSocket handlers.",
                "Id-less automations: runs are no longer stored, so trace/list for item_id 'None' returns [] and trace/get returns "
                "not_found. Both are inside the contract (bounded list; missing trace is not_found). No Engineering consumer "
                "addresses that bucket by design: each starts from an automation config id. ha-mcp 8.5.0 resolves the item_id "
                "from the entity registry unique_id, which an id-less automation does not have.",
                "Child links: a parent run no longer carries child_id for an id-less child, whose run is no longer stored. "
                "Id-bearing child links are unchanged. Engineering's normalized traces do not read child_id ("
                + ecite("reliability/provider.py", 589) + "); get_automation_trace returns trace/get as Core sends it.",
                "Edge: an automation whose configured id is the literal string 'None' shared its trace bucket with id-less "
                "runs on 2026.10.0; on 2026.10.1 the bucket holds only its own runs (narrower, not wider).",
                "Conclusion: contract satisfied on 2026.10.1. Reference retained.",
            ],
            "executed_evidence": [
                REAL_HA_STEP + ": phase trace_generation (" + CONTRACT_SCRIPT + ":3966-3998 at the CI source) reloads "
                "automations, fires the fixture event, requires non-empty trace/list headers with run_id and started_at "
                "through fetch_normalized_trace_list for the id-bearing fixture automation 'dev14_real_contract_automation' "
                "(line 140), and requires trace/get to return trace and config objects. The step prints 'Real Home Assistant "
                "2026.10.1 contract assertions passed.' only after every phase passes.",
                "Same step: the admission probe (bounded trace/list plus a missing trace/get that must be not_found) admits "
                "core.automation_trace_read within the lane's 19 compatible references.",
            ],
            "untested_on_2026_10_1": [
                "Runs of an automation without an id (their absence from storage).",
                "child_id links from a parent run to a child automation or script run.",
                "The delegated ha_get_automation_traces path (ha-mcp 8.5.0).",
                "automation_reliability_analysis, incident_correlation, handoff_generation and change_impact_analysis trace "
                "reads end to end (covered offline by the unit suite only).",
            ],
            "test_added": "None. The changed behavior is inside Core and outside what any Engineering consumer depends on; "
                          "executing it needs a real 2026.10.1 Core (new Real HA lane work) and would not change the conclusion.",
        },
        "core.template_semantics": {
            "contract": [
                "Analysis: offline, binary-owned semantic registry (parse only, "
                + ecite("dependency/template_semantic_registry.json", 10) + "). Rendering: the existing bounded direct HA "
                "template provider. Profile checks include template_source_release_supported ("
                + ecite("ha_core_readmission/profiles.py", 297, 301) + "); projection " + ecite("ha_core_readmission/source.py", 251) + ".",
                "Consumers: render_template forwards the template to Core's /api/template and returns Core's text ("
                + ecite("tools/compatibility.py", 502) + "; " + ecite("ha_core_readmission/routes.py", 105)
                + "); delegated ha_eval_template (" + ecite("ha_core_readmission/routes.py", 74) + "); dependency and "
                "helper-planning analysis through the semantic registry.",
                "device_attr and is_device_attr are attribute_item_access (" + ecite("dependency/template_semantic_registry.json", 244, 259)
                + "); every such lookup except entity_name is bounded_semantic_opaque with a conservative lock ("
                + ecite("dependency/obligation_ledger.py", 2466) + ").",
            ],
            "upstream_change": [
                "2026.10.0: device_attr returns getattr(device, attr) (" + cite("2026.10.0", "homeassistant/helpers/template/extensions/devices.py", 117, 130, 132)
                + "). For a non-composite device the config_entries, config_entries_subentries and primary_config_entry "
                "properties report a deprecated use with core_behavior ERROR (" + cite("2026.10.1", "homeassistant/helpers/device_registry.py", 384, 394, 429, 442, 459, 473)
                + ", same blob at 2026.10.0), which raises RuntimeError (" + cite("2026.10.1", "homeassistant/helpers/frame.py", 373, 386)
                + ", same blob at 2026.10.0). hasattr does not catch it, so the render fails.",
                "2026.10.1: for those three names on a non-composite device, device_attr returns a value derived from "
                "config_entry_id and config_subentry_id and logs a one-time warning (" + cite("2026.10.1", "homeassistant/helpers/template/extensions/devices.py", 33, 144, 160, 174, 180)
                + "). is_device_attr compares that value. Composite devices and every other attribute behave as on 2026.10.0.",
                "Unchanged: the template engine (" + cite("2026.10.1", "homeassistant/helpers/template/__init__.py")
                + ") and the /api/template view (" + cite("2026.10.1", "homeassistant/components/api/__init__.py", 497, 519)
                + "). The global names and signatures of device_attr and is_device_attr are unchanged.",
            ],
            "assessment": [
                "Analysis: unaffected. The registry classifies the lookup by function name, not attribute, and returns an "
                "opaque value; a different rendered value cannot change the dependency result. The added offline test "
                "executes this for the three restored names.",
                "Rendering: a template that read these attributes on a non-composite device failed on 2026.10.0 and returns "
                "a value on 2026.10.1, matching releases before 2026.10.0. Engineering forwards Core's output and relies on "
                "no rendered value. Error handling for other failures is unchanged.",
                "The device-registry probe reads config_entries_subentries and primary_config_entry from the WebSocket "
                "registry serialization, not from templates; that path is unchanged.",
                "Conclusion: contract satisfied on 2026.10.1. Reference retained.",
            ],
            "executed_evidence": [
                REAL_HA_STEP + ": script dependency contract printed {\"script_dependency_contract\": \"PASS\", ..., \"core\": "
                "\"2026.10.1\", \"upstream\": \"8.5.0\"}; its fixtures include a template condition (" + CONTRACT_SCRIPT
                + ":3196, 3215 at the CI source) analyzed through the semantic registry.",
                "Real 2026.10.0 renders through delegated ha_eval_template (arithmetic, undefined, error and refusal cases; "
                "scripts/ha_mcp_850_container_acceptance.py:853-858) in run 38279374013, jobs 114912771142 and 114912771210 "
                "(ha-mcp 8.6.0 candidate, Core 2026.10.0, step 'Run python scripts/ha_mcp_850_container_acceptance.py'). "
                "The render path is byte-identical in 2026.10.1 except devices.py.",
                "Offline: tests/test_hamcp161_device_template_attribute_analysis.py (sha256 "
                + sha((args.repo / "tests/test_hamcp161_device_template_attribute_analysis.py").read_bytes())
                + ") executes the analysis of device_attr and is_device_attr for the three restored names and proves it "
                "equals an ordinary attribute's and cannot hide a dependent state read.",
            ],
            "untested_on_2026_10_1": [
                "Any template render on a real 2026.10.1 Core, through render_template or ha_eval_template.",
                "Rendering of the three restored attributes on any Core.",
                "Native render_template against any real Core: no script in this repository's real-HA lanes calls it; only the "
                "delegated ha_eval_template is executed (on 2026.10.0).",
            ],
            "test_added": "tests/test_hamcp161_device_template_attribute_analysis.py (offline analysis only). A real-Core "
                          "render on 2026.10.1 would need new Real HA lane work; it is disclosed above, not added.",
        },
    },
    "review_artifacts_sha256": {name: sha((raw / path).read_bytes()) for name, path in ARTIFACTS.items()},
    "unchanged_implementation_files": {path: sha(blob(DEPLOYED, path)) for path in UNCHANGED},
    "limits": [
        "Execution is the full-tier CI candidate-slot lane on main 5fe5ddd and a3aa4d1; no run executed 2026.10.1 on the deployed build commit itself. Binding is by identical Engineering package tree.",
        "Arm64 provenance and manifest are bound, but arm64 execution is not established (amd64 only).",
        "OCI attestation and SLSA provenance signatures were not cryptographically verified; digests and declared source revision are recorded as observed.",
        "The image label io.hass.base.version (2026.06.1) differs from the provenance base (2026.07.0); recorded as an observation, not relied on.",
        "The baseline printed summary omits authority_transition; [20, 21] is asserted by scripts/automation_baseline_contract_acceptance.py at the CI source.",
        "Upstream 2026.10.1 changes trace storage and child links for automations without an id, and restores deprecated device template attributes. Both touch selected references (core.automation_trace_read, core.template_semantics); selected_capability_assessment finds both contracts satisfied and lists what is untested on 2026.10.1. IndieAuth redirect handling is not an Engineering surface.",
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
