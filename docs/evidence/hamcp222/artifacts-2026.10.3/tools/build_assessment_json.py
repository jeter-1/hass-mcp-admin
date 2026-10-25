"""Build assessment.json for the HAMCP-222 Core 2026.10.3 registry add (prepare only).

Run after make_assessment.py: python3 -I build_assessment_json.py <add103-dir>
Re-checks OCI digest chains and GitHub metadata from raw/ and folds in the
mechanical assertion result. Exits non-zero on any mismatch.
"""

import hashlib
import json
import sys

OUT = sys.argv[1]
R = f"{OUT}/raw"
FAIL = []


def fail(m):
    FAIL.append(m)
    print("FAIL", m)


def sha(path):
    return "sha256:" + hashlib.sha256(open(path, "rb").read()).hexdigest()


def j(path):
    return json.load(open(path))


res = j(f"{OUT}/tools/assert-result.json")
if res["failures"]:
    fail("make_assessment failures present")

# --- GitHub metadata ---------------------------------------------------------
COMMIT = "1f214b8461dd1fbbd66e10765c6905f49d3c1d86"
TREE = "7c592b097111f15031bbfbc37edbbb42f8235320"
BASE_COMMIT = "abe1fdd7cd6b4de1715c87b6fd87d92699514ef9"
rel = j(f"{R}/release-2026.10.3.json")
rel2 = j(f"{R}/release-2026.10.2.json")
ref = j(f"{R}/ref-2026.10.3.json")
ref2 = j(f"{R}/ref-2026.10.2.json")
gc = j(f"{R}/gitcommit-2026.10.3.json")
cm = j(f"{R}/commit-2026.10.3.json")
cmp_ = j(f"{R}/compare-2026.10.1...2026.10.3.json")
checks = {
    "release tag": rel["tag_name"] == "2026.10.3",
    "release not draft/prerelease": rel["draft"] is False and rel["prerelease"] is False,
    "ref -> commit": ref["object"]["sha"] == COMMIT and ref["object"]["type"] == "commit",
    "git commit tree": gc["sha"] == COMMIT and gc["tree"]["sha"] == TREE,
    "commit verification": gc["verification"]["verified"] is True and gc["verification"]["reason"] == "valid",
    "commit api tree": cm["commit"]["tree"]["sha"] == TREE,
    "compare merge base == 2026.10.1": cmp_["merge_base_commit"]["sha"] == BASE_COMMIT,
    "compare ahead/behind": cmp_["status"] == "ahead" and cmp_["behind_by"] == 0,
    "compare files == local": len(cmp_["files"]) == res["changed_paths"],
    "10.2 tag commit in range": ref2["object"]["sha"] in {c["sha"] for c in cmp_["commits"]},
    "10.2 release not draft/prerelease": rel2["draft"] is False and rel2["prerelease"] is False,
}
cmp_files = {f["filename"]: f["status"][0].upper() for f in cmp_["files"]}
local = dict(l.rstrip("\n").split("\t")[::-1] for l in open(f"{OUT}/diff/changed-files-2026.10.1..2026.10.3.txt"))
checks["compare file statuses == local name-status"] = cmp_files == local
for k, v in checks.items():
    if not v:
        fail(f"github check {k}")

# --- OCI digest chain --------------------------------------------------------
O = f"{R}/oci"
INDEX = "sha256:407b89cd11a8cb67d388d21739a226c74acbbebe1ac59e3b83b11b43ce76b98f"
if sha(f"{O}/index-2026.10.3.json") != INDEX:
    fail("index digest")
idx = j(f"{O}/index-2026.10.3.json")
plat = {}
att = {}
for m in idx["manifests"]:
    p = m.get("platform", {})
    ann = m.get("annotations", {})
    if p.get("os") == "unknown":
        att[ann.get("vnd.docker.reference.digest")] = m["digest"]
    else:
        plat[f"{p['os']}/{p['architecture']}"] = m["digest"]
oci = {"index": INDEX, "platforms": plat}
for arch, short in (("linux/amd64", "amd64"), ("linux/arm64", "arm64")):
    d = plat[arch]
    if sha(f"{O}/manifest-{short}.json") != d:
        fail(f"manifest {short}")
    a = att.get(d)
    if a is None or sha(f"{O}/manifest-att-{short}.json") != a:
        fail(f"attestation manifest {short}")
    am = j(f"{O}/manifest-att-{short}.json")
    prov = [l["digest"] for l in am["layers"]
            if l.get("annotations", {}).get("in-toto.io/predicate-type", "").startswith("https://slsa.dev/provenance")]
    if len(prov) != 1 or sha(f"{O}/provenance-{short}.json") != prov[0]:
        fail(f"provenance {short}")
    mf = j(f"{O}/manifest-{short}.json")
    if sha(f"{O}/config-{short}.json") != mf["config"]["digest"]:
        fail(f"config {short}")
    pv = j(f"{O}/provenance-{short}.json")
    pred = pv["predicate"]
    rev = pred["buildDefinition"]["externalParameters"]["request"]["args"]
    vcs = json.dumps(pred).count(COMMIT)
    subj = {s["name"]: "sha256:" + s["digest"]["sha256"] for s in pv["subject"]}
    if d not in subj.values():
        fail(f"provenance subject {short}")
    if vcs == 0:
        fail(f"provenance revision {short}")
    base = [r for r in pred["buildDefinition"]["resolvedDependencies"] if "homeassistant-base@" in r.get("uri", "")]
    cfg = j(f"{O}/config-{short}.json")
    oci[short] = {
        "manifest": d, "attestation_manifest": a, "provenance": prov[0],
        "config": mf["config"]["digest"],
        "provenance_subjects": subj,
        "provenance_base": base[0]["uri"].split("?")[0] + "@sha256:" + base[0]["digest"]["sha256"],
        "label_io.hass.base.version": cfg["config"]["Labels"]["io.hass.base.version"],
        "label_io.hass.version": cfg["config"]["Labels"]["io.hass.version"],
        "builder_id": pred["runDetails"]["builder"]["id"],
        "vcs_revision": pred["runDetails"]["metadata"]["buildkit_metadata"]["vcs"]["revision"],
        "vcs_source": pred["runDetails"]["metadata"]["buildkit_metadata"]["vcs"]["source"],
        "started_finished": [pred["runDetails"]["metadata"]["startedOn"], pred["runDetails"]["metadata"]["finishedOn"]],
    }
    if oci[short]["vcs_revision"] != COMMIT or oci[short]["vcs_source"] != "https://github.com/home-assistant/core":
        fail(f"provenance vcs {short}")
    if not oci[short]["builder_id"].startswith("https://github.com/home-assistant/core/actions/runs/40006558230/"):
        fail(f"provenance builder {short}")
    if oci[short]["label_io.hass.version"] != "2026.10.3":
        fail(f"label version {short}")
    if not oci[short]["provenance_base"].split("@")[1] == oci[short]["label_io.hass.base.version"]:
        fail(f"base label/provenance differ {short}")

# --- capabilities: relied-on Core paths -> touched? -------------------------
C = "homeassistant/components/"
H = "homeassistant/helpers/"
RELIED = {
    "core.basic_rest_read": [C + "api/", C + "http/", "homeassistant/core.py"],
    "core.basic_websocket_read": [C + "websocket_api/", C + "http/", "homeassistant/auth/"],
    "core.state_service_discovery": [C + "api/", C + "websocket_api/", H + "service.py", "homeassistant/core.py"],
    "core.non_device_registry_read": [C + "config/area_registry.py", C + "config/floor_registry.py",
                                      C + "config/label_registry.py", C + "config/entity_registry.py",
                                      H + "area_registry.py", H + "floor_registry.py", H + "label_registry.py",
                                      H + "entity_registry.py"],
    "core.direct_device_registry_read": [C + "config/device_registry.py", H + "device_registry.py"],
    "core.delegated_device_effective_area": [C + "config/device_registry.py", H + "device_registry.py",
                                             C + "config/entity_registry.py", H + "entity_registry.py", C + "search/"],
    "core.direct_entity_state_read": [C + "api/", "homeassistant/core.py"],
    "core.automation_configuration_read": [C + "config/automation.py", C + "config/view.py", C + "automation/"],
    "core.automation_trace_read": [C + "trace/", C + "automation/", C + "websocket_api/"],
    "core.dashboard_configuration_read": [C + "lovelace/", C + "websocket_api/"],
    "core.template_semantics": [H + "template/", C + "api/", C + "websocket_api/"],
    "core.configuration_validation": [C + "config/core.py", H + "check_config.py", H + "config_validation.py",
                                      "homeassistant/config.py"],
    "core.dependency_helper_planning": [H + "device_registry.py", H + "entity_registry.py", H + "area_registry.py",
                                        H + "template/", C + "config/automation.py", C + "config/script.py", C + "search/"],
    "core.typed_helper_operation": [C + "input_boolean/", C + "input_number/", H + "collection.py",
                                    H + "service.py", "homeassistant/core.py"],
    "core.f3_mutation_verification": [C + "config/", C + "api/", C + "websocket_api/", C + "lovelace/"],
    "core.governed_configuration_operation": [C + "config/automation.py", C + "config/script.py", C + "config/view.py",
                                              C + "automation/config.py", C + "script/config.py",
                                              H + "config_validation.py", H + "condition.py"],
    "core.governance_observability": [],
    "core.typed_fan_operation": [C + "fan/", H + "service.py"],
    "core.typed_power_operation": [C + "switch/", C + "light/", H + "service.py"],
    "core.integration_inspection_metadata_read": [C + "config/config_entries.py", "homeassistant/config_entries.py",
                                                  "homeassistant/loader.py"],
    "core.automation_baseline_metadata_read": [C + "automation/", C + "config/automation.py", C + "auth/",
                                               H + "entity_registry.py", H + "device_registry.py"],
    "core.entity_registry_metadata_operation": [C + "config/entity_registry.py", H + "entity_registry.py"],
    "core.entity_registry_availability_operation": [C + "config/entity_registry.py", H + "entity_registry.py",
                                                    C + "homeassistant/exposed_entities.py",
                                                    C + "cloud/alexa_config.py"],
    "core.entity_assistant_exposure_operation": [C + "homeassistant/exposed_entities.py", C + "cloud/google_config.py",
                                                 C + "cloud/alexa_config.py", C + "conversation/"],
    "core.label_registry_operation": [C + "config/label_registry.py", H + "label_registry.py"],
    "core.configuration_delete_operation": [C + "config/automation.py", C + "config/script.py", C + "config/view.py",
                                            C + "config/entity_registry.py", H + "entity_registry.py", C + "api/"],
}
changed = list(local)
cap_status = {}
for cap, paths in RELIED.items():
    hit = sorted({c for c in changed for p in paths if (c.startswith(p) if p.endswith("/") else c == p)})
    cap_status[cap] = {"relied_on_core_paths": paths, "changed_relied_on_paths": hit}

fp = j(f"{OUT}/tools/capabilities.json")
if [c["capability_id"] for c in fp] != list(RELIED):
    fail("capability list order/ids differ from compiled profiles")

ENG = "hass_mcp_engineering_beta/ha_mcp_engineering/"
CV10 = "core 2026.10.1:homeassistant/helpers/config_validation.py@" + next(c["blob"][:12] for c in res["cited"] if c["version"] == "2026.10.1" and c["path"].endswith("config_validation.py"))
CV13 = "core 2026.10.3:homeassistant/helpers/config_validation.py@" + next(c["blob"][:12] for c in res["cited"] if c["version"] == "2026.10.3" and c["path"].endswith("config_validation.py"))


def cb(v, p):
    return f"core {v}:{p}@" + next(c["blob"][:12] for c in res["cited"] if c["version"] == v and c["path"] == p)


def sb(p):
    return f"core 2026.10.1=2026.10.3:{p}@{res['same_blob'][p][:12]}"


COND = sb("homeassistant/helpers/condition.py")
AUTH10, AUTH13 = cb("2026.10.1", "homeassistant/auth/__init__.py"), cb("2026.10.3", "homeassistant/auth/__init__.py")
ALX10 = cb("2026.10.1", "homeassistant/components/cloud/alexa_config.py")
ALX13 = cb("2026.10.3", "homeassistant/components/cloud/alexa_config.py")
APH10 = cb("2026.10.1", "homeassistant/components/config/auth_provider_homeassistant.py")
APH13 = cb("2026.10.3", "homeassistant/components/config/auth_provider_homeassistant.py")
CORE_VIEW = cb("2026.10.3", "homeassistant/components/config/core.py")
VIEW = cb("2026.10.3", "homeassistant/components/config/view.py")

assessment = {
    "citation_form": "core <tag>:<path>@<git blob id prefix>:<lines>, verified in the hash-bound Core archives "
                     "(core 2026.10.1=2026.10.3:<path>@<blob> means the same blob in both); engineering <path>:<lines> "
                     "at checkout c1c0b02 (hass_mcp_engineering_beta tree 1788d45f, runtime tree 70479fa2). "
                     "tools/make_assessment.py asserts every cited line's text, blob identity and unchanged directory.",
    "core.configuration_validation": {
        "contract": [
            f"POST /api/config/core/check_config returns exactly {{result, errors, warnings}} with result valid|invalid: "
            f"engineering {ENG}ha_core_readmission/profiles.py:312,318; probe engineering {ENG}ha_core_readmission/source.py:78,81,88,435.",
            f"Consumers: check_config, create_reload_plan, create_home_assistant_restart_plan "
            f"(engineering {ENG}ha_core_readmission/routes.py:198,241-242,246,248).",
        ],
        "upstream_change": [
            f"2026.10.1 rejected a state condition with 'for' and a multi-element state list ({CV10}:1563-1565).",
            f"2026.10.3 accepts it; it still rejects 'for' with an attribute and with any listed state that names an entity "
            f"({CV13}:1557-1563,1565). The only difference in the file is this hunk (tools/assert-result.json config_validation_hunks_10_1_lines).",
            f"The endpoint, its admin gate and result taxonomy are unchanged ({CORE_VIEW}:29,32,36,38,42-44; same blob, and "
            f"{sb('homeassistant/helpers/check_config.py')}). The evaluator is unchanged ({COND}:1870,1907,1923,1927,1938-1939): "
            "any listed state matches, then 'for' is measured from entity.last_changed.",
        ],
        "assessment": [
            "Response shape and taxonomy: unchanged; Engineering's probe predicate accepts both outcomes as before.",
            "Verdict: a configuration whose only error on 2026.10.1 was a multi-state 'for' condition validates as valid on "
            "2026.10.3. This is a Core semantic widening inside the contract (Engineering forwards Core's verdict and does not "
            "re-derive it; no Engineering code mirrors the removed rule).",
            "probatio_semantics_exact: probatio==0.13.0 is unchanged (pyproject.toml:81 in both archives); the change is a Core "
            "schema rule, not a probatio semantic change.",
            "Conclusion: contract satisfied on 2026.10.3. Reference retainable.",
        ],
    },
    "core.governed_configuration_operation": {
        "contract": [
            f"Selected for governed automation/script writes after F3 classification (engineering {ENG}ha_core_readmission/routes.py:735; "
            f"profile {ENG}ha_core_readmission/profiles.py:377; projection {ENG}ha_core_readmission/source.py:277).",
            f"Native eligibility grammar for generated conditions allows only state in ('on','off','open','not_home',['off']) "
            f"(engineering {ENG}governance/configuration_eligibility.py:208,215,217).",
        ],
        "upstream_change": [
            f"Writes go through config/view.py data_validator (automation: async_validate_config_item), and a schema error returns "
            f"400 'Message malformed' ({VIEW}:117,121,123; {cb('2026.10.3','homeassistant/components/config/automation.py')}:49; "
            f"{cb('2026.10.3','homeassistant/components/automation/config.py')}:237). Both files are unchanged. STATE_CONDITION_SCHEMA "
            f"is the 'state' entry of the condition dispatch in both versions ({CV10}:1755; {CV13}:1751).",
        ],
        "assessment": [
            "Write path: Core now accepts a body with a multi-state 'for' condition that 2026.10.1 rejected with 400. Engineering's "
            "own generated grammar never emits a multi-element list (the only list is ['off']), so its owned writes are unaffected.",
            "Owner-supplied bodies: such a body now succeeds instead of failing at Core; readback compares Core's stored config "
            "exactly and its code is unchanged. Wider acceptance, not a new write path or target.",
            "Conclusion: contract satisfied on 2026.10.3. Reference retainable.",
        ],
    },
    "core.dependency_helper_planning": {
        "contract": [f"engineering {ENG}ha_core_readmission/profiles.py:324; consumers routes.py:217,218."],
        "assessment": [
            f"No relied-on Core file changed ({len(RELIED['core.dependency_helper_planning'])} paths, see capability_status). "
            f"Entity extraction is key-based (engineering {ENG}dependency/extraction.py:39), and both a single state and a state "
            "list were already valid without 'for' on 2026.10.1, so the newly valid shape adds no new key or reference form.",
            "Conclusion: untouched; reference retainable.",
        ],
    },
    "core.configuration_delete_operation": {
        "contract": [f"engineering {ENG}ha_core_readmission/profiles.py:589,594; routes.py:107."],
        "assessment": [
            f"DELETE handler, readback endpoints and entity registry unchanged ({VIEW}:142; same blobs for config/automation.py, "
            "config/script.py, config/entity_registry.py, helpers/entity_registry.py, components/api). Stored configs that were "
            "invalid on 2026.10.1 because of a multi-state 'for' condition may now load on 2026.10.3; deletion and absence "
            "readback do not depend on validity.",
            "Conclusion: untouched; reference retainable.",
        ],
    },
    "core.basic_websocket_read": {
        "upstream_change": [
            f"async_remove_user now revokes each refresh token of the removed user before removing it ({AUTH13}:345,347,357-359,361; "
            f"2026.10.1 {AUTH10}:354,357). Revocation runs callbacks registered by the WebSocket auth phase, which close the "
            f"connection ({AUTH13}:536,540,542; {sb('homeassistant/components/websocket_api/auth.py')}:116-117).",
        ],
        "assessment": [
            "Only effect: a WebSocket session whose token belongs to a user being deleted is now closed instead of staying open. "
            "Engineering never deletes users (no auth_provider, config/auth/delete or async_remove_user reference in the runtime "
            "package: tools/assert-result.json engineering_negative_grep). For Engineering this is fail-closed (reconnect fails auth).",
            "Auth phase, commands and connection code are unchanged (websocket_api/ directory unchanged).",
            "Conclusion: contract satisfied on 2026.10.3. Reference retainable.",
        ],
    },
    "core.entity_registry_availability_operation / core.entity_assistant_exposure_operation": {
        "upstream_change": [
            f"Alexa's entity-registry listener now also suppresses RequireRelink around its cloud sync ({ALX10}:562 -> {ALX13}:562).",
        ],
        "assessment": [
            f"Engineering tolerates one side effect of that listener: a newly persisted cloud.alexa key "
            f"(engineering {ENG}governance/entity_availability_exposure.py:66-67,363). That key is written by should_expose "
            f"({ALX13}:320,545), before and independent of the changed line 562; exposed_entities.py and google_config.py are "
            "unchanged. Alexa remains not writable (entity_availability_exposure.py:83).",
            "Effect: an unhandled RequireRelink in a background listener is now swallowed; no change to what Engineering writes or reads back.",
            "Both capabilities are currently dormant (pending signed admission); conclusion: untouched in contract; retainable.",
        ],
    },
    "core.f3_mutation_verification": {
        "assessment": [
            "Matched as touched only through its broad relied-on prefix homeassistant/components/config/. The one changed file there, "
            "config/auth_provider_homeassistant.py, is not an F3 readback endpoint and is not called by Engineering "
            "(engineering_negative_grep: auth_provider absent). Every config endpoint F3 reads back is the same blob "
            "(config/automation.py, script.py, view.py, entity/area/device/floor/label registries, config_entries.py); components/api, "
            "websocket_api and lovelace are unchanged directories.",
            "Conclusion: untouched in contract; reference retainable.",
        ],
    },
    "components/config (endpoints Engineering calls)": {
        "assessment": [
            f"Only config/auth_provider_homeassistant.py changed ({APH10}:54 -> {APH13}:14,61-62,66,70,75,80): owner guard, "
            "credentials_exist refusal and a lock for config/auth_provider/homeassistant/create. Engineering does not call it.",
            "Every other file under components/config is the same blob (unchanged_directories), including core.py (check_config), "
            "view.py, automation.py, script.py, auth.py (config/auth/list used by engineering approval_identity.py:42), "
            "entity_registry.py, device/area/floor/label registries and config_entries.py.",
        ],
    },
    "untouched_capabilities": sorted(c for c, s in cap_status.items() if not s["changed_relied_on_paths"]),
    "touched_capabilities": {c: s["changed_relied_on_paths"] for c, s in cap_status.items() if s["changed_relied_on_paths"]},
    "governance_observability_note": f"No Core call (engineering {ENG}ha_core_readmission/profiles.py:403; source.py:239).",
    "household_observations_not_capabilities": [
        "group/sensor.py:478: an empty sensor group now yields None instead of crashing (no-member groups). Engineering cites "
        "only group/sensor.py:367-369 (default name), identical in both (engineering configuration_definitions/projection.py:666, schemas.py:117).",
        "mqtt/entity.py: discovery teardown registered on reload dispatchers (device triggers/tags no longer fire twice after reload).",
        "Other changed integrations (broadlink, ecovacs, fritz, melcloud_home, ...) are integration-specific.",
    ],
}

limits = [
    "Prepared from GitHub API metadata, codeload archives and GHCR artifacts only. No CI execution of 2026.10.3 is collected here; "
    "executed_evidence must come from the lane runs and is not asserted by this package.",
    "OCI attestation and SLSA provenance signatures were not cryptographically verified; digests and declared source revision "
    "are recorded as observed. GitHub commit verification is the API's 'valid' verdict (no local GPG check).",
    "The image base changes from homeassistant-base 2026.07.0 (2026.10.1 image) to 2026.10.1 (builder.yml BASE_IMAGE_VERSION; "
    "provenance base digests recorded). Base-image contents, including the exact Python patch version, were not reviewed (unknown; "
    "release notes claim Python 3.14.8 in 2026.10.2 and .python-version is 3.14.9).",
    "Dependency pins changed (aiohttp 3.14.4->3.14.5, home-assistant-frontend 20260930.3->.5, uv, new pycares==5.0.1, bitstring<5, "
    "pypasser<1.0.0). Package contents are outside the Core archive and were not reviewed. Backend lovelace is unchanged; "
    "dashboard defaults stay gated on Core 2026.9.4 (engineering dashboard_analysis/rules.py:12,44).",
    "2026.10.3 accepts multi-state 'for' state conditions that 2026.10.1 rejected; core.configuration_validation and "
    "core.governed_configuration_operation are assessed as satisfied; the newly valid shape is not executed against a real 2026.10.3.",
    "User removal now closes that user's WebSocket connections; Engineering never removes users.",
    "No household physical effects, Supervisor lifecycle, add-on continuity, all-custom-integration coverage or ha-mcp claim.",
]

out = {
    "model": "core-exact-release-review-evidence-v1 (inputs only; not signed)",
    "version": "2026.10.3",
    "core_source": COMMIT,
    "core_tree": TREE,
    "source_archive_sha256": res["archives_sha256"]["2026.10.3"],
    "comparison_base": {"version": "2026.10.1", "core_source": BASE_COMMIT,
                        "core_tree": "f84f3a161b96beb66d91cc2f08909ea5e8ae0cd8",
                        "source_archive_sha256": res["archives_sha256"]["2026.10.1"]},
    "image_index_digest": INDEX,
    "architecture_manifests": plat,
    "oci": oci,
    "upstream_release": {
        "tag": rel["tag_name"], "published_at": rel["published_at"], "prerelease": rel["prerelease"],
        "draft": rel["draft"], "archive_form": "codeload tar.gz of the tag commit",
        "github_commit_verification": "verified (GitHub API; no local GPG check)",
        "commit_parents": [p["sha"] for p in gc["parents"]],
        "intermediate_release": {"tag": rel2["tag_name"], "commit": ref2["object"]["sha"],
                                 "published_at": rel2["published_at"]},
    },
    "upstream_changes_from_2026.10.1": {
        "commits": len(cmp_["commits"]), "ahead_by": cmp_["ahead_by"],
        "changed_paths": res["changed_paths"], "changed_homeassistant_paths": res["changed_homeassistant_paths"],
        "status_counts": res["changed_status_counts"],
        "diffstat": open(f"{OUT}/diff/diffstat-2026.10.1..2026.10.3.txt").read().strip().split("\n")[-1].strip(),
        "flagged": [
            "helpers/config_validation.py (#186165, 2026.10.2): multi-state 'for' state conditions allowed. "
            "core.configuration_validation and core.governed_configuration_operation: assessed.",
            "auth/__init__.py (#186558, 2026.10.2): removing a user revokes its refresh tokens and closes connections. "
            "core.basic_websocket_read: assessed.",
            "config/auth_provider_homeassistant.py (#186398, 2026.10.2): owner guard and credentials_exist on create. Not an Engineering surface.",
            "cloud/alexa_config.py (#185894, 2026.10.2): RequireRelink suppressed in the registry listener. Exposure/availability: assessed.",
        ],
        "release_note_attribution": "PR numbers and release placement from raw/release-2026.10.{2,3}-notes.md (observation).",
        "unchanged_relied_on": sorted(res["same_blob"]),
        "unchanged_directories": res["unchanged_directories"],
    },
    "capabilities": fp,
    "capability_status": cap_status,
    "selected_capability_assessment": assessment,
    "cited": res["cited"],
    "same_blob": res["same_blob"],
    "engineering_cited": res["engineering_cited"],
    "engineering_negative_grep": res["engineering_negative_grep"],
    "engineering_checkout": {"commit": "c1c0b02376075023a75ee741624854d5485ec83f",
                             "hass_mcp_engineering_beta_tree": "1788d45fbeb3f5f9b8832cc735fbc34547dae926",
                             "runtime_package_tree": "70479fa2958aafd36d0e74e3082f98c50b11053c",
                             "config_version": "2.4.1-beta.36"},
    "cross_checks": checks,
    "review_artifacts_sha256": {l.split()[1]: l.split()[0] for l in open(f"{R}/SHA256SUMS")},
    "diff_artifacts_sha256": {n: hashlib.sha256(open(f"{OUT}/diff/{n}", "rb").read()).hexdigest() for n in (
        "changed-files-2026.10.1..2026.10.3.txt", "diffstat-2026.10.1..2026.10.3.txt", "flagged-files.txt", "flagged-diffs.patch")},
    "limits": limits,
    "failures": FAIL,
}
json.dump(out, open(f"{OUT}/assessment.json", "w"), indent=1, sort_keys=False)
print("touched", json.dumps(out["selected_capability_assessment"]["touched_capabilities"], indent=1))
print("untouched", len(out["selected_capability_assessment"]["untouched_capabilities"]))
print(json.dumps(oci, indent=1)[:3000])
print("FAILURES", len(FAIL))
sys.exit(1 if FAIL else 0)
