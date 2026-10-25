"""HAMCP-222 Core 2026.10.3 registry-add assessment inputs (prepare only, never signs).

Run: python3 -I make_assessment.py <add103-dir> <engineering-checkout>
Reads the hash-bound Core archives as data (tarfile, no extraction, no import).
Asserts every CITED line, SAME_BLOB identity, unchanged directories, the
changed-path set against diff/changed-files, and every Engineering cite at the
pinned checkout HEAD. Writes tools/assert-result.json. Exits non-zero on any mismatch.
"""

import hashlib
import json
import subprocess
import sys
import tarfile

OUT, REPO = sys.argv[1], sys.argv[2]
ARCH = {
    "2026.10.1": ("abe1fdd7cd6b4de1715c87b6fd87d92699514ef9",
                  "a4305046e34fb6bad4669915c64dfaec0f2b22ff56b6baefca9e8890cabd81c4"),
    "2026.10.3": ("1f214b8461dd1fbbd66e10765c6905f49d3c1d86",
                  None),  # from the committed lane fixture below
}
ENG_HEAD = "c1c0b02376075023a75ee741624854d5485ec83f"
ENG_TREE = "1788d45fbeb3f5f9b8832cc735fbc34547dae926"
ENG_RUNTIME_TREE = "70479fa2958aafd36d0e74e3082f98c50b11053c"
E = "hass_mcp_engineering_beta/ha_mcp_engineering/"
FAIL = []


def fail(msg):
    FAIL.append(msg)
    print("FAIL", msg)


def blob_id(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def load(version):
    commit, want = ARCH[version]
    path = f"{OUT}/raw/archives/core-{version}-tar.gz"
    raw = open(path, "rb").read()
    got = hashlib.sha256(raw).hexdigest()
    files = {}
    with tarfile.open(path, "r:gz") as tar:
        if tar.pax_headers.get("comment") != commit:
            fail(f"{version} pax comment {tar.pax_headers.get('comment')} != {commit}")
        prefix = f"core-{commit}/"
        for m in tar.getmembers():
            if m.name == prefix[:-1] and m.isdir():
                continue
            if not m.name.startswith(prefix):
                fail(f"{version} member outside prefix {m.name}")
                continue
            rel = m.name[len(prefix):]
            if m.isfile():
                mode = "100755" if m.mode & 0o111 else "100644"
                files[rel] = (mode, tar.extractfile(m).read())
            elif m.issym():
                files[rel] = ("120000", m.linkname.encode())
    return got, files


# Expected 10.3 archive hash comes from the committed lane fixture, not memory.
fixture = json.loads(subprocess.run(
    ["git", "-C", REPO, "show", f"{ENG_HEAD}:tests/fixtures/core_2026_10_3_lane_provenance.json"],
    check=True, capture_output=True).stdout)


def find(d, key):
    if isinstance(d, dict):
        for k, v in d.items():
            if k == key:
                return v
            r = find(v, key)
            if r is not None:
                return r
    if isinstance(d, list):
        for v in d:
            r = find(v, key)
            if r is not None:
                return r
    return None


fx_sha = find(fixture, "source_archive_sha256")
ARCH["2026.10.3"] = (ARCH["2026.10.3"][0], fx_sha.split(":")[-1])
if find(fixture, "source_commit") != ARCH["2026.10.3"][0]:
    fail("fixture source_commit mismatch")

sha = {}
trees = {}
for v in ARCH:
    sha[v], trees[v] = load(v)
    if sha[v] != ARCH[v][1]:
        fail(f"archive {v} sha256 {sha[v]} != {ARCH[v][1]}")
A, B = trees["2026.10.1"], trees["2026.10.3"]
BLOB = {v: {p: blob_id(d) for p, (m, d) in t.items()} for v, t in trees.items()}

# --- changed-path set must equal diff/changed-files (git name-status) -------
changed = {}
for p in sorted(set(A) | set(B)):
    if p not in A:
        changed[p] = "A"
    elif p not in B:
        changed[p] = "D"
    elif A[p] != B[p]:
        changed[p] = "M"
cf = {}
for line in open(f"{OUT}/diff/changed-files-2026.10.1..2026.10.3.txt"):
    s, p = line.rstrip("\n").split("\t")
    cf[p] = s
if cf != changed:
    fail(f"changed-files mismatch: {set(cf.items()) ^ set(changed.items())}")
ha_changed = sorted(p for p in changed if p.startswith("homeassistant/"))

# --- CITED: (version, path) -> {line: exact stripped text} ------------------
CITED = {
    ("2026.10.1", "homeassistant/helpers/config_validation.py"): {
        1542: "def STATE_CONDITION_SCHEMA(value: Any) -> dict[str, Any]:",
        1554: "if CONF_FOR in validated:",
        1558: "if CONF_ATTRIBUTE in validated:",
        1559: "raise probatio.Invalid(\"Cannot use 'for' with an attribute\")",
        1563: "if isinstance(state, list):",
        1564: "if len(state) != 1:",
        1565: "raise probatio.Invalid(\"Cannot use 'for' with a list of states\")",
        1567: "if INPUT_ENTITY_ID.match(state):",
        1569: "\"Cannot use 'for' with a state referencing an entity\"",
        1755: "\"state\": STATE_CONDITION_SCHEMA,",
    },
    ("2026.10.3", "homeassistant/helpers/config_validation.py"): {
        1542: "def STATE_CONDITION_SCHEMA(value: Any) -> dict[str, Any]:",
        1554: "if CONF_FOR in validated:",
        1557: "# states, the entity must stay in one of them for the whole duration.",
        1558: "if CONF_ATTRIBUTE in validated:",
        1559: "raise probatio.Invalid(\"Cannot use 'for' with an attribute\")",
        1560: "states = validated[CONF_STATE]",
        1561: "if not isinstance(states, list):",
        1562: "states = [states]",
        1563: "if any(INPUT_ENTITY_ID.match(state) for state in states):",
        1565: "\"Cannot use 'for' with a state referencing an entity\"",
        1751: "\"state\": STATE_CONDITION_SCHEMA,",
    },
    ("2026.10.3", "homeassistant/helpers/condition.py"): {
        1870: "def state(",
        1874: "for_period: timedelta | None = None,",
        1907: "if not isinstance(req_state, list):",
        1923: "is_state = value == state_value",
        1927: "if for_period is None or not is_state:",
        1938: "duration = dt_util.utcnow() - for_period",
        1939: "duration_ok = duration > entity.last_changed",
    },
    ("2026.10.3", "homeassistant/components/config/core.py"): {
        29: "url = \"/api/config/core/check_config\"",
        32: "@require_admin",
        36: "res = await check_config.async_check_ha_config_file(request.app[KEY_HASS])",
        38: "state = \"invalid\" if res.errors else \"valid\"",
        42: "\"result\": state,",
        43: "\"errors\": res.error_str or None,",
        44: "\"warnings\": res.warning_str or None,",
    },
    ("2026.10.3", "homeassistant/components/config/view.py"): {
        117: "await self.data_validator(hass, config_key, data)",
        121: "except (probatio.Invalid, HomeAssistantError) as err:",
        123: "f\"Message malformed: {err}\", HTTPStatus.BAD_REQUEST",
        142: "async def delete(self, request: web.Request, config_key: str) -> web.Response:",
    },
    ("2026.10.3", "homeassistant/components/config/automation.py"): {
        49: "data_validator=async_validate_config_item,",
    },
    ("2026.10.3", "homeassistant/components/automation/config.py"): {
        237: "automation_config[CONF_CONDITIONS] = await async_validate_conditions_config(",
    },
    ("2026.10.1", "homeassistant/auth/__init__.py"): {
        345: "async def async_remove_user(self, user: models.User) -> None:",
        354: "if tasks:",
        357: "await self._store.async_remove_user(user)",
    },
    ("2026.10.3", "homeassistant/auth/__init__.py"): {
        345: "async def async_remove_user(self, user: models.User) -> None:",
        347: "if user.is_owner:",
        357: "# Revoking runs the callbacks, which close connections using the tokens.",
        358: "for refresh_token in list(user.refresh_tokens.values()):",
        359: "self.async_remove_refresh_token(refresh_token)",
        361: "await self._store.async_remove_user(user)",
        536: "def async_remove_refresh_token(self, refresh_token: models.RefreshToken) -> None:",
        540: "callbacks = self._revoke_callbacks.pop(refresh_token.id, ())",
        542: "revoke_callback()",
    },
    ("2026.10.3", "homeassistant/components/websocket_api/auth.py"): {
        116: "self._hass.auth.async_register_revoke_token_callback(",
        117: "refresh_token.id, self._cancel_ws",
    },
    ("2026.10.1", "homeassistant/components/config/auth_provider_homeassistant.py"): {
        54: "await provider.async_add_auth(msg[\"username\"], msg[\"password\"])",
    },
    ("2026.10.3", "homeassistant/components/config/auth_provider_homeassistant.py"): {
        14: "CREATE_LOCK: HassKey[asyncio.Lock] = HassKey(",
        61: "if user.is_owner and not connection.user.is_owner:",
        62: "raise Unauthorized(context=connection.context(msg))",
        66: "async with hass.data[CREATE_LOCK]:",
        70: "credential.auth_provider_type == provider.type",
        75: "\"credentials_exist\",",
        80: "await provider.async_add_auth(msg[\"username\"], msg[\"password\"])",
    },
    ("2026.10.1", "homeassistant/components/cloud/alexa_config.py"): {
        562: "with suppress(alexa_errors.NoTokenAvailable):",
    },
    ("2026.10.3", "homeassistant/components/cloud/alexa_config.py"): {
        320: "return async_should_expose(self.hass, CLOUD_ALEXA, entity_id)",
        536: "async def _handle_entity_registry_updated(",
        545: "if not self.should_expose(entity_id):",
        562: "with suppress(alexa_errors.NoTokenAvailable, alexa_errors.RequireRelink):",
    },
    ("2026.10.1", "homeassistant/components/group/sensor.py"): {
        367: "self._attr_name = name",
        368: "if name == DEFAULT_NAME:",
        369: "self._attr_name = f\"{DEFAULT_NAME} {sensor_type}\".capitalize()",
        478: "if not valid_state or not valid_state_numeric:",
    },
    ("2026.10.3", "homeassistant/components/group/sensor.py"): {
        367: "self._attr_name = name",
        368: "if name == DEFAULT_NAME:",
        369: "self._attr_name = f\"{DEFAULT_NAME} {sensor_type}\".capitalize()",
        478: "if not sensor_values or not valid_state or not valid_state_numeric:",
    },
    ("2026.10.3", "homeassistant/const.py"): {26: "PATCH_VERSION: Final = \"3\"",
                                               49: "DEVICE_DEFAULT_NAME: Final = \"Unnamed Device\""},
    ("2026.10.1", "homeassistant/const.py"): {26: "PATCH_VERSION: Final = \"1\"",
                                               49: "DEVICE_DEFAULT_NAME: Final = \"Unnamed Device\""},
    ("2026.10.3", "homeassistant/package_constraints.txt"): {
        9: "aiohttp==3.14.5",
        63: "python-slugify==9.1.2",
        43: "home-assistant-frontend==20260930.5",
    },
    ("2026.10.1", "homeassistant/package_constraints.txt"): {
        9: "aiohttp==3.14.4",
        63: "python-slugify==9.1.2",
        43: "home-assistant-frontend==20260930.3",
    },
    ("2026.10.3", ".github/workflows/builder.yml"): {18: "BASE_IMAGE_VERSION: \"2026.10.1\""},
    ("2026.10.1", ".github/workflows/builder.yml"): {17: "BASE_IMAGE_VERSION: \"2026.07.0\""},
    ("2026.10.3", "pyproject.toml"): {81: "\"probatio==0.13.0\","},
    ("2026.10.1", "pyproject.toml"): {81: "\"probatio==0.13.0\","},
}

# config_validation.py: the only difference is one hunk inside STATE_CONDITION_SCHEMA
import difflib
_a = A["homeassistant/helpers/config_validation.py"][1].decode().split("\n")
_b = B["homeassistant/helpers/config_validation.py"][1].decode().split("\n")
CV_HUNKS = [(t, i1 + 1, i2, j1 + 1, j2) for t, i1, i2, j1, j2 in
            difflib.SequenceMatcher(None, _a, _b, autojunk=False).get_opcodes() if t != "equal"]
if not CV_HUNKS or min(h[1] for h in CV_HUNKS) < 1554 or max(h[2] for h in CV_HUNKS) > 1570:
    fail(f"config_validation differs outside STATE_CONDITION_SCHEMA for-branch: {CV_HUNKS}")

cited_out = []
for (v, p), lines in CITED.items():
    t = trees[v]
    if p not in t:
        fail(f"cited missing {v}:{p}")
        continue
    text = t[p][1].decode("utf-8").split("\n")
    b = BLOB[v][p]
    for n, want in sorted(lines.items()):
        got = text[n - 1].strip() if n <= len(text) else None
        if got != want:
            fail(f"cite {v}:{p}:{n} got {got!r} want {want!r}")
    cited_out.append({"version": v, "path": p, "blob": b,
                      "cite": f"core {v}:{p}@{b[:12]}",
                      "lines": {str(n): s for n, s in sorted(lines.items())}})

# --- SAME_BLOB: relied-on files identical in both versions ------------------
SAME_BLOB = [
    "homeassistant/helpers/condition.py",
    "homeassistant/helpers/check_config.py",
    "homeassistant/helpers/entity_registry.py",
    "homeassistant/helpers/device_registry.py",
    "homeassistant/helpers/area_registry.py",
    "homeassistant/helpers/floor_registry.py",
    "homeassistant/helpers/label_registry.py",
    "homeassistant/helpers/service.py",
    "homeassistant/helpers/collection.py",
    "homeassistant/components/config/core.py",
    "homeassistant/components/config/view.py",
    "homeassistant/components/config/automation.py",
    "homeassistant/components/config/script.py",
    "homeassistant/components/config/auth.py",
    "homeassistant/components/config/entity_registry.py",
    "homeassistant/components/config/device_registry.py",
    "homeassistant/components/config/area_registry.py",
    "homeassistant/components/config/floor_registry.py",
    "homeassistant/components/config/label_registry.py",
    "homeassistant/components/config/config_entries.py",
    "homeassistant/components/automation/config.py",
    "homeassistant/components/script/config.py",
    "homeassistant/components/websocket_api/auth.py",
    "homeassistant/components/websocket_api/commands.py",
    "homeassistant/components/websocket_api/connection.py",
    "homeassistant/components/websocket_api/http.py",
    "homeassistant/components/auth/__init__.py",
    "homeassistant/components/api/__init__.py",
    "homeassistant/components/homeassistant/exposed_entities.py",
    "homeassistant/components/cloud/google_config.py",
    "homeassistant/auth/models.py",
    "homeassistant/auth/auth_store.py",
    "homeassistant/core.py",
    "homeassistant/config.py",
    "homeassistant/config_entries.py",
    "homeassistant/loader.py",
]
same_out = {}
for p in SAME_BLOB:
    if p not in A or p not in B:
        fail(f"same_blob missing {p}")
    elif BLOB["2026.10.1"][p] != BLOB["2026.10.3"][p] or A[p][0] != B[p][0]:
        fail(f"same_blob differs {p}")
    else:
        same_out[p] = BLOB["2026.10.3"][p]

# --- unchanged directories (every file identical, listed exceptions only) ---
DIRS = {
    "homeassistant/components/config/": {"homeassistant/components/config/auth_provider_homeassistant.py"},
    "homeassistant/components/cloud/": {"homeassistant/components/cloud/alexa_config.py"},
    "homeassistant/components/frontend/": {"homeassistant/components/frontend/manifest.json"},
    "homeassistant/helpers/": {"homeassistant/helpers/config_validation.py"},
    "homeassistant/auth/": {"homeassistant/auth/__init__.py"},
}
for d in ("websocket_api", "api", "http", "lovelace", "automation", "script", "trace",
          "input_boolean", "input_number", "fan", "switch", "light", "homeassistant",
          "search", "auth", "template", "conversation", "google_assistant", "history",
          "logbook", "recorder", "system_health", "hassio", "backup", "person", "zone",
          "device_automation", "blueprint", "onboarding"):
    DIRS[f"homeassistant/components/{d}/"] = set()
DIRS["homeassistant/util/"] = set()
dirs_out = {}
for d, allowed in DIRS.items():
    n = sum(1 for p in B if p.startswith(d))
    if n == 0:
        fail(f"dir empty {d}")
    diff = {p for p in changed if p.startswith(d)}
    if diff != allowed:
        fail(f"dir {d} changed {sorted(diff)} expected {sorted(allowed)}")
    dirs_out[d] = {"files": n, "changed": sorted(diff)}


# --- Engineering cites at the pinned checkout -------------------------------
def git(*args):
    return subprocess.run(["git", "-C", REPO, *args], check=True, capture_output=True).stdout


if git("rev-parse", "HEAD").decode().strip() != ENG_HEAD:
    fail("engineering HEAD mismatch")
if git("rev-parse", f"{ENG_HEAD}:hass_mcp_engineering_beta").decode().strip() != ENG_TREE:
    fail("engineering tree mismatch")
if git("rev-parse", f"{ENG_HEAD}:hass_mcp_engineering_beta/ha_mcp_engineering").decode().strip() != ENG_RUNTIME_TREE:
    fail("runtime tree mismatch")

ENGINEERING_CITED = {
    E + "ha_core_readmission/profiles.py": {
        147: "\"core.basic_rest_read\",", 158: "\"core.basic_websocket_read\",",
        173: "\"core.state_service_discovery\",", 184: "\"core.non_device_registry_read\",",
        202: "\"core.direct_device_registry_read\",", 219: "\"core.delegated_device_effective_area\",",
        236: "\"core.direct_entity_state_read\",", 248: "\"core.automation_configuration_read\",",
        260: "\"core.automation_trace_read\",", 282: "\"core.dashboard_configuration_read\",",
        297: "\"core.template_semantics\",", 312: "\"core.configuration_validation\",",
        318: "\"probatio_semantics_exact\",", 403: "\"persisted_evidence_no_core_call\",",
        594: "\"rest_config_automation_script_delete_no_body\",", 324: "\"core.dependency_helper_planning\",",
        336: "\"core.typed_helper_operation\",", 377: "\"core.governed_configuration_operation\",",
        399: "\"core.governance_observability\",", 448: "\"core.integration_inspection_metadata_read\",",
        460: "\"core.automation_baseline_metadata_read\",", 474: "\"core.entity_registry_metadata_operation\",",
        509: "\"core.entity_registry_availability_operation\",", 532: "\"core.entity_assistant_exposure_operation\",",
        559: "\"core.label_registry_operation\",", 589: "\"core.configuration_delete_operation\",",
    },
    E + "ha_core_readmission/source.py": {
        78: "def _configuration_validation_response(value: Any) -> bool:",
        81: "or set(value) != {\"result\", \"errors\", \"warnings\"}",
        88: "if result not in {\"valid\", \"invalid\"}:",
        255: "\"core.configuration_validation\",",
        239: "add(\"core.governance_observability\", True)",
        277: "\"core.governed_configuration_operation\",",
        435: "request_url(self._settings.api_url, rest_path(\"/config/core/check_config\")),",
    },
    E + "ha_core_readmission/routes.py": {
        198: "\"check_config\": (\"core.configuration_validation\",),",
        242: "\"core.configuration_validation\",",
        248: "\"core.configuration_validation\",",
        107: "CONFIGURATION_DELETE_PROFILE = \"core.configuration_delete_operation\"",
        217: "\"create_change_plan\": (\"core.dependency_helper_planning\",),",
        218: "\"create_configuration_plan\": (\"core.dependency_helper_planning\",),",
        241: "\"create_reload_plan\": (",
        246: "\"create_home_assistant_restart_plan\": (",
        735: "values.add(\"core.governed_configuration_operation\")",
    },
    E + "governance/configuration_eligibility.py": {
        208: "def native_policy(conditions: list) -> None:",
        215: "require(set(condition) <= {\"condition\", \"entity_id\", \"state\", \"for\"})",
        217: "require(condition[\"state\"] in (\"on\", \"off\", \"open\", \"not_home\", [\"off\"]))",
    },
    E + "reliability/lifecycle.py": {
        231: "# Other conditions need runtime inputs or semantics outside this slice.",
        232: "self.gap(\"condition_unresolved\", path)",
    },
    E + "configuration_definitions/projection.py": {
        666: "# group/sensor.py:367-369 replaces the default name.",
    },
    E + "configuration_definitions/schemas.py": {
        117: "# group/sensor.py:367-369 replaces this default with \"Sensor group <type>\".",
    },
    E + "recorder_settings/entity_filter.py": {
        8: "``abe1fdd7cd6b4de1715c87b6fd87d92699514ef9``.",
    },
    E + "configuration_definitions/naming.py": {
        18: "SLUGIFY_PIN = \"python-slugify 9.1.2 legacy pipeline (Core 2026.10.1 package_constraints.txt)\"",
    },
    E + "dependency/extraction.py": {
        39: "ENTITY_BEARING_KEYS = frozenset({\"entity_id\"})",
    },
    E + "approval_identity.py": {42: "AUTH_LIST_COMMAND = MappingProxyType({\"type\": \"config/auth/list\"})"},
    E + "audit_baseline/capture_contracts.py": {20: "\"principal\": {\"type\": \"auth/current_user\"},"},
    E + "governance/entity_availability_exposure.py": {
        66: "# Only Alexa's handler writes a computed default as a side effect.",
        67: "TOLERATED_NEW_KEY_ASSISTANTS = (\"cloud.alexa\",)",
        83: "WRITABLE_ASSISTANTS = (\"cloud.google_assistant\", \"conversation\")",
        363: "Core's Alexa registry-update handler persists a computed default the first",
    },
    E + "dashboard_analysis/rules.py": {
        11: "FRONTEND_COMMIT = \"380e9b5a81ada29a1d187b4123c54fb3d6fbcc89\"",
        12: "CORE_VERSION = \"2026.9.4\"",
        44: "self.defaults = core_version == CORE_VERSION",
    },
}
eng_out = []
for p, lines in ENGINEERING_CITED.items():
    text = git("show", f"{ENG_HEAD}:{p}").decode().split("\n")
    for n, want in sorted(lines.items()):
        got = text[n - 1].strip() if n <= len(text) else None
        if got != want:
            fail(f"ecite {p}:{n} got {got!r} want {want!r}")
    eng_out.append({"path": p, "cite": f"engineering {p}",
                    "lines": {str(n): s for n, s in sorted(lines.items())}})

# Negative reachability in the runtime package (git grep exit 1 == no match).
NEG = ["auth_provider", "config/auth/delete", "config/auth/create", "async_remove_user",
       "cloud/alexa/sync"]
neg_out = {}
for pat in NEG:
    r = subprocess.run(["git", "-C", REPO, "grep", "-n", "-F", pat, ENG_HEAD, "--",
                        "hass_mcp_engineering_beta/ha_mcp_engineering"], capture_output=True)
    neg_out[pat] = "absent" if r.returncode == 1 else r.stdout.decode()[:400]
    if r.returncode != 1:
        fail(f"negative grep {pat} matched")

summary = {
    "archives_sha256": sha,
    "changed_paths": len(changed),
    "changed_homeassistant_paths": len(ha_changed),
    "changed_status_counts": {s: sum(1 for x in changed.values() if x == s) for s in "AMD"},
    "cited": cited_out,
    "same_blob": same_out,
    "unchanged_directories": dirs_out,
    "engineering_cited": eng_out,
    "engineering_negative_grep": neg_out,
    "config_validation_hunks_10_1_lines": CV_HUNKS,
    "failures": FAIL,
}
json.dump(summary, open(f"{OUT}/tools/assert-result.json", "w"), indent=1, sort_keys=True)
print(json.dumps({k: summary[k] for k in ("archives_sha256", "changed_paths",
                  "changed_homeassistant_paths", "changed_status_counts")}, indent=1))
print("cited files", len(cited_out), "cited lines", sum(len(c["lines"]) for c in cited_out),
      "same_blob", len(same_out), "dirs", len(dirs_out), "eng files", len(eng_out),
      "eng lines", sum(len(c["lines"]) for c in eng_out))
print("FAILURES", len(FAIL))
sys.exit(1 if FAIL else 0)
