# HAMCP-187: upstream ha-mcp 8.7.0 review

This is a read-only review report. It changes no runtime, policy, registry,
workflow, support-window or stable-v1 file, and it admits nothing.

**Status: 8.7.0 adoption is parked.** Josh decided on 2026-10-15 to stay on
ha-mcp 8.5.0 with HA-MCP Custom Component 2.2.1 (see "Decisions recorded" at
the end). The admission plan in section 8 and the update steps in sections 5
and 8.7 are kept as conditional designs for a future reconsideration. They are
not pending approval requests.

- Base: `main` at `690a00b2fcbffc4e90c97a1cad36ccf6168e8ec1` (Engineering
  `2.4.1-beta.9`, stable `1.1.2`).
- Production reference (owner-stated): ha-mcp 8.5.0 add-on, `admitted_exact`,
  with the HA-MCP Custom Component 2.2.1 ("File & YAML Tools" entry only) on
  Core 2026.10.1. The component version was stated by the owner on
  2026-10-15, after the capture. The capture in section 10 used 2.2.0 and
  2.2.2; it did not run 2.2.1.
- Review date: 2026-10-15.
- Amended 2026-10-15 with an owner-authorized disposable local capture
  (section 10). Where the capture contradicted an earlier source-only claim,
  the claim is corrected in place and marked **Corrected by capture**.

## Evidence method and limits

**Source.** I used a local clone of the upstream repository with tags
`v8.5.0`, `v8.6.0` and `v8.7.0`. Every source claim below cites a file and line
at a tag. Verified claims come from reading that source. Claims marked
*inferred* were not executed.

**Catalog captures.** I ran disposable local containers of the pinned
standalone images. Each had a read-only root filesystem, a synthetic
unreachable HA URL, a synthetic token and the settings UI disabled. A
stdlib-only client sent `initialize` (protocol `2025-03-26`) and then
`tools/list`, and the containers were removed afterwards.

**Method check.** The same method reproduced the compiled registry
fingerprints for 8.5.0 (`0d7ecdb7…`) and 8.6.0 (`47151934…`) exactly.

**Not done:**
- No live Home Assistant, live Engineering, deployed endpoint or Supervisor
  access.
- ~~No component-enabled capture.~~ Done in section 10: a disposable Core
  2026.10.1 with component 2.2.0 and 2.2.2, ha-mcp 8.5.0 and 8.7.0, under
  production-mirroring settings. Those captures are evidence only. They are
  not committed admission fixtures, and an admission PR must regenerate them
  with the admission scripts.
- No Supervisor runtime. The #2613 announcement itself could not fire
  (section 10, Q2); Core's side of it was exercised with a synthetic flow.
- No strict-contract or runtime-contract fingerprint computation. That needs
  the `mcp` package and the admission scripts.
- No arm64 execution.
- No cryptographic attestation verification.

## Pinned 8.7.0 identity

| Item | Value |
|---|---|
| Tag object | `da72390465b4e14244228478549d7eb362b00637` (tagged 2026-10-14 14:52:58 UTC) |
| Source commit | `a96752286be1a6511f32a62e90213641c8d18068` |
| Source tree | `00e38a7b4720aa6955fdcacc4040d942c2332c13` |
| Standalone image index | `sha256:600677bbd7574dfb59682e40d6945f73fcc8852d3684b3671506602473942448` |
| Standalone amd64 / arm64 | `sha256:a936dff6aa1be509c1c8aca2e3fc2071318958a7b49119bd07931c5ab683307a` / `sha256:b37c70761751be87d7bb8ed003bd7098d76700d66b5b659ad5ded15624c73bc2` |
| Add-on amd64 index / manifest | `sha256:d45f5498d77ae18fab1eaab1bb5bdc0c94b2e60491baab05582e4ae0167a1e6a` / `sha256:c6162daaed0bdc063cba8d1f1fec38c5c66b9ba40bfe1271346c9d36acbf58fc` |
| Add-on aarch64 index / manifest | `sha256:5affb15bedec8f12233c2e6da94db2501a27adeacbe0f6fb6c612a04ce8bd0dd` / `sha256:4a2dae9ba96029ec550543c2fc3ecef0689987c447edaa1698f23865a4a8d18c` |
| Image revision label | `051e45fab480967dc9bf79dd333106390b588537` |
| Proposed entry id | `ha-mcp-v8.7.0-600677bb` (loader rule `ha-mcp-v{version}-{digest[:8]}`) |
| Reference catalog | 77 tools, fingerprint `c6426571c1a7d38a6014f849a8f7f522b1bb22433e71a7455d34f0313b025e18` |

**Notes on the identity rows:**
- **Image revision.** `051e45fa` is the tag plus two commits ("publish
  version 8.7.0"). It differs from the tag only in
  `homeassistant-addon/CHANGELOG.md` and `homeassistant-addon/config.yaml`.
  8.5.0 shipped with the same pattern.
- **Add-on repository name.** The add-on arm64 repository is
  `ha-mcp-addon-aarch64`, not `-arm64`.
- **Reference catalog.** The fingerprint was deterministic across two
  captures, and the catalog arrived in one `tools/list` page.
- **8.5.0 cross-check.** The 8.5.0 standalone and add-on digests in the
  registry were re-resolved and match.

There are 235 upstream commits from `v8.6.0` to `v8.7.0`.

## 1. Protocol and MRTR

**Verdict: compatible. No Engineering protocol change is needed.**

**Negotiation is unchanged** (verified in source and by capture).
- 8.7.0's vendored SDK negotiates as
  `negotiated = requested if requested in HANDSHAKE_PROTOCOL_VERSIONS else LATEST_HANDSHAKE_VERSION`
  (`_vendor/mcp/server/runner.py:421-426`).
- Handshake versions are 2024-11-05, 2025-03-26, 2025-06-18 and 2025-11-25.
  The only "modern" version is 2026-07-28 (`_vendor/mcp_types/version.py:24-59`).
- A 2025-03-26 client receives `2025-03-26`, and every 8.5.0, 8.6.0 and 8.7.0
  capture shows exactly that.
- The vendored `mcp` / `mcp-types` 2.2.0 tree is byte-identical between
  `v8.5.0` and `v8.7.0`.
- The `uv.lock` bump of `mcp` from 1.28.1 to 1.30.0 is a dev dependency, not
  the server runtime.

**A legacy client cannot reach the modern stateless path.** HTTP routing sends
a request to the modern handler only when an `MCP-Protocol-Version` header is
present and is not a handshake version
(`_vendor/mcp/server/streamable_http_manager.py:194`). Engineering always
sends `2025-03-26`.

**MRTR (#2615, `b7092942`, new in 8.7.0) cannot affect Engineering under
production settings.**
- **Off by default.** It lives entirely inside `PolicyMiddleware`. That
  middleware is registered only when `ENABLE_TOOL_SECURITY_POLICIES` is true,
  and the default is `False` (`config_settings.py:504-506`;
  `server.py:816-817,861`).
- **Modern protocol only.** Even when policies are on,
  `supports_mrtr()` requires the request's protocol version to be in
  `MODERN_PROTOCOL_VERSIONS = ("2026-07-28",)` (`policy/mrtr.py:34-39`).
- **No `InputRequiredToolResult` for legacy clients.** It is produced only
  through `ApprovalContinuation`, which exists only when `supports_mrtr` is
  true (`mrtr.py:100,147-158`; `middleware.py:263-268,295-298`).
- **Policies on with a 2025-03-26 client.** The pre-existing blocking
  approval wait applies. The default is 60 s, with a range of 5–600. It then
  fails closed with `ToolError` / `isError` and code
  `USER_APPROVAL_REQUIRED` or `USER_DENIED` (`middleware.py:587-705`).
  There is no auto-allow.
- **No wire changes from the MRTR commit.** It changes nothing in
  `initialize` capabilities or in `tools/list` descriptors.

**What does change on the wire.**
- **`initialize` result.**
  - `serverInfo.icons` were removed when the binaries and `.mcpb` bundle were
    retired (#2646).
  - The `instructions` text now always ends with an issue-filing section
    (#2603; `server.py:347-361`).
  - Capabilities are unchanged: `logging`, `prompts` (listChanged),
    `resources` (listChanged, no subscribe), `tools` (listChanged).
- **`tools/list` descriptors** (#2613, `8938b750`, new in 8.7.0). Every tool
  now carries all four hints through `read_only_hints` / `write_hints`
  (`tools/tool_hints.py:17-38`):
  - Reads gain `destructiveHint: false`.
  - Writes gain an explicit `idempotentHint` (mostly `false`) and
    `readOnlyHint: false`.
  - Dashboard getter and setter `openWorldHint` changes from `false` to
    `true`.
  - This changes the annotations of 75 of the 80 production-profile tools.
    The other five (`ha_manage_app`, `ha_manage_blueprints`,
    `ha_manage_pipeline`, `ha_manage_theme`, `ha_set_integration`) already
    carried all four hints in 8.5.0 (captured; section 10, Q1). Every runtime
    fingerprint still changes, because `_meta` and descriptions differ.
- **#2613 also adds add-on MCP discovery.** See section 7.
- **`tools/call`.**
  - The error envelope is unchanged: `raise_tool_error` still raises
    `ToolError(json.dumps(error_response))` (`tools/helpers.py:69-72`).
  - Five tools no longer send `notifications/message` log lines to legacy
    clients (#2531). Progress notifications remain.
  - Sessions remain `stateless_http: True` (`__main__.py:320`).
  - The secret-path validation in `start.py:62-126` has no diff.
- **fastmcp-slim 4.0.3 → 4.0.10.** The changes are validation-error log
  redaction, cursor-offset validation, and preserved per-parameter strictness.
  None touches `initialize` capabilities, `isError`, or
  `structuredContent` / `outputSchema` behaviour.

**Effect on exact-schema admission.** The protocol allowlist
(`upstream_tool_policy.py:24`, `upstream_read_gateway.py:121-122`,
`upstream_dashboard.py:68`, `f3_dashboard/constants.py:24`,
`upstream_search_8_6.py:17`) needs no change. Admission does need a complete
new descriptor binding, because every descriptor differs (annotations on
75 of 80 tools, and `_meta` on all of them).

**Minimum Engineering change for protocol: none.**

## 2. Does 8.7.0 fix the #2695 HOLD defect?

**Yes, from source and tests.**

**Repository evidence.** The repo records the HOLD only as labels. There is
no committed defect write-up. The labels are:
- `ci/support-window.yaml:51-53`
- `docs/CODEX_WORKFLOW.md:392`
- `docs/ALARMO_INSPECTION_ACCEPTANCE.md:61`
- `tests/test_support_window.py:46`
- `tests/test_latest_stable_release.py:66`
- `tests/test_ha_mcp_860_candidate_lane.py:185`
- register HAMCP-173 ("Owner decision 2026-10-13: ha-mcp candidate slot empty
  (8.6.0 HOLD, upstream #2695)")

`#2695` is the upstream fix pull request, not an issue number. It was merged
2026-10-09 as `bd9cbc19`: "identify the helper behind an entity before
removing it".

**The defect.** `ha_remove_helpers_integrations` with a flow `helper_type`
plus an `entity_id` resolved the entity's `config_entry_id` and deleted that
config entry, without checking that the entity belonged to that helper's
integration. For example, `helper_type="group"` with a Hue light entity
removed the Hue config entry.
- v8.6.0 `src/ha_mcp/tools/tools_integrations.py:413-469`
  (`_get_entry_id_for_flow_helper`) checks only that `helper_type` is in
  `FLOW_HELPER_TYPES`. It reads `config/entity_registry/get` and returns
  `entry.config_entry_id` with no `platform` comparison.
- Any `success:false` reply is reported as `not_in_registry`.
- The deletion then happens in `_delete_resolved_flow_helper` →
  `_delete_flow_config_entry`.

**The fix in 8.7.0** (`src/ha_mcp/tools/flow_helper_lookup.py`, new file):
- `:193-196`: `if platform != helper_type: _raise_platform_mismatch(...)`. The
  message ends "Nothing was deleted." (`:204-230`).
- `require_helper_type` takes the helper set from Core's helper flows first.
- Registry read failures other than `not_found` now raise instead of being
  reported as absent (`:34-88`; closes upstream #2699).
- New entity-only removal resolves the type from the registry platform
  (`:349-375`).
- Tests:
  - `tests/src/unit/test_flow_helper_lookup.py`
  - `tests/src/unit/test_remove_helper_by_entity.py`
  - `tests/src/unit/test_ha_remove_helpers_component_routing.py`
  - e2e `TestRemoveHelperByEntity` in `test_config_entry_flow.py`

**Engineering reachability: none.**
- `ha_remove_helpers_integrations` is `persistent_write` in the 8.4.3, 8.5.0
  and 8.6.0 policies, with quarantine `policy:persistent_write` and no
  argument restrictions.
- No Engineering runtime, script or test file names it.
- The read gateway dispatches only read routes.
- The fan, power and dashboard providers refuse any tool outside their fixed
  required sets.

**This contradicts the stated premise.** See section 9, item C1: the same
defect is present in production 8.5.0 and in rollback 8.4.3.

## 3. Per-tool contract diffs vs 8.5.0

**Legend.**
- **Unchanged:** same structure and runtime behaviour.
- **Compatible change:** additive or text-only. The existing Engineering call
  and projection still work after rebinding.
- **Incompatible change:** a removed or retyped input, or a changed output or
  behaviour that the existing binding cannot carry without a new projection.

**Rebinding applies to every row.** No tool descriptor is byte-identical to
8.5.0:
- annotations changed on 75 of 80 tools (#2613; the other five already
  carried all four hints);
- 8.5.0's private `_meta.ha_mcp` is absent (since 8.6.0, #2487);
- most descriptions were rewritten.

Every row therefore needs a new per-tool contract binding, even when the
functional class is "unchanged".

**How to read the table.**
- The "8.6→8.7" column says whether 8.7.0 adds anything beyond the already
  reviewed 8.6.0 projection.
- Input fingerprints are `schema_fingerprint` of `inputSchema`, first 8 hex
  digits.

### 3a. The 28 delegated reads

| Tool | Input fp 8.5.0 / 8.6.0 / 8.7.0 | vs 8.5.0 | 8.6→8.7 | Evidence |
|---|---|---|---|---|
| ha_config_get_automation | bae748eb / bae748eb / bae748eb | Unchanged (descriptor drift only) | annotations only | Non-admin tokens are now refused locally (`admin_routes.py:25`, #2551); production uses an admin token *(inferred)* |
| ha_config_get_script | 941d5612 / 941d5612 / 941d5612 | Unchanged (descriptor drift only) | annotations only | Same as above |
| ha_config_get_scene | ed1f21b6 / e471799f / e471799f | Incompatible (8.6 change, already projected) | annotations only | `scene_id` is now optional; search/list inputs were added. The 8.6 projection still requires `scene_id` (`upstream_reads_8_6.py` `read_arguments`) |
| ha_config_get_calendar_events | text-only | Compatible (text) | annotations only | Body identical |
| ha_config_get_category | text-only | Compatible (text) | annotations only | Body identical |
| ha_config_get_label | text-only | Compatible (text) | annotations only | Body identical |
| ha_config_list_dashboard_resources | text-only | Compatible (text) | annotations only | Body identical |
| ha_config_list_groups | text-only | Compatible (text) | annotations only | Body identical |
| ha_config_list_helpers | c25b0b38 / 853fbabc / 7a122c14 | **Compatible change, new in 8.7** | **structural** | Adds `describe`, `helper_id`, `menu_choice`. `helper_type` changes from enum to `enum ∪ const "all" ∪ string`. Record shapers unchanged (`config_helpers/listing.py:69-137`). New `warnings` on degraded secret scrub, and `SERVICE_CALL_FAILED` when helper flows are degraded (#2648, #2697) |
| ha_eval_template | 5f3dce93 / 1cd46b20 / 1cd46b20 | Incompatible (8.6 change, already projected) | annotations only | #2524 (in 8.6.0): `template` became optional; `condition`, `variables`, `strict` added; `request_id` and `evaluation_time` removed from output. The 8.6 projection sends template-only with explicit defaults |
| ha_get_automation_traces | text-only | Compatible (text) | annotations only | `ctx.info` notification removed |
| ha_get_device | text-only | Unchanged | annotations only | Enrichment moved; AST-identical |
| ha_get_entity | text-only | Compatible (additive output) | annotations + output key | New `uses_device_name` output key (`tools_entities.py:93`, #2639) |
| ha_get_entity_exposure | text-only | Unchanged | annotations only | — |
| ha_get_hacs_info | text-only | Compatible | annotations only | Uses per-request credentials (`tools_hacs.py:130-141`, #2689). Engineering's `HACS_INFO_RESPONSE_ENVELOPE_MODEL_V1` binding needs an 8.7.0 key (`upstream_read_gateway.py:151-153`) |
| ha_get_history | bb827688 / 48bbc5e7 / 3d3e136a | **Incompatible output, new in 8.7** | **structural + output** | See note H |
| ha_get_overview | text-only | Unchanged | annotations only | Hint text only |
| ha_get_skill_guide | 23273e15 / 84790a9d / b9088e1e | Incompatible (8.6 change, already projected) | text-only input | #2556 (in 8.6.0): `skill` removed, `file` defaults to `SKILL.md`. **`clients/mcp.py:359-371` hard-codes the argument rewrite for `server_version == "8.6.0"` and must include 8.7.0.** Confirmed by capture (section 10, Q4): on 8.7.0 the unrewritten `{skill, file}` call fails, no key is returned, and the dashboard write is refused with `BPS_ACKNOWLEDGMENT_REQUIRED`. That failure is closed, not silent |
| ha_get_state | text-only | Unchanged (description changed) | description only | Body identical |
| ha_get_todo | text-only | Unchanged | annotations only | — |
| ha_get_zone | text-only | Unchanged | annotations only | Only the write path changed (#2643) |
| ha_list_floors_areas | text-only | Unchanged | annotations only | — |
| ha_list_services | text-only | Unchanged | annotations only | — |
| ha_search | text-only (reference) | Compatible; conditional descriptor | annotations only | With a component that advertises `search_unified`, the advertised schema drops `search_types`, `include_config` and `config_time_budget` (`transforms/component_search.py:22,61-96`, from 8.6.0 #2481). Passing `search_types` now always adds `warnings` (`search/response.py:630`). This is the existing 8.6 "search descriptor pair" problem; 8.7 needs its own pair |
| ha_manage_blueprints → `ha_get_blueprint` | 6a71b650 / 6a09a0f2 / 6a09a0f2 | Compatible (8.6 change, already projected) | description only | Annotations unchanged 8.6→8.7. Binding in `providers/upstream_blueprint.py:9-18` |
| ha_get_system_health (platform) | 0c51da5b / 569e6a4d / 569e6a4d | Compatible (8.6 change) | annotations only | 8.7.0 input fingerprint equals 8.6.0 |
| ha_get_app (platform) | c7667408 / 7c1fba17 / 7c1fba17 | Compatible | annotations only | PEM option values are redacted when `redact_secrets` is on (#2714). Extra suggestion text |
| ha_get_integration (platform) | 5dd76ac0 / 798a5110 / 798a5110 | Incompatible upstream (#2543, in 8.6.0); **not observable through Engineering** | annotations only | Component-path `options` are no longer flattened. Engineering's wrapper sends `include_options: False` (`upstream_platform_reads.py:292`) |

**Note H: ha_get_history (new in 8.7.0)** (#2684, #2685, #2693;
`tools_history.py`, `history_response.py`, `statistics_resets.py`).

*Input.* Adds `core_options`, a free-form object merged into the Core request,
and `include_schema`, which adds `data.core_contract`. Both are additive. An
8.7 projection must withhold `core_options`, as it does for the 8.6 template
extras.

*History output.*
- Rows are now Core's rows with compact keys expanded: `s`→`state`,
  `a`→`attributes`, `lu`→`last_updated`, `lc`→`last_changed`.
- Timestamps are localized to HA's timezone.
- Unknown keys are retained.
- `attributes` appears only when Core sends it.
- `last_changed` falls back to `last_updated`.
- 8.5.0 and 8.6.0 built fixed rows instead.
- `warnings` moved from `data.warnings` to the top level.

*Statistics output.*
- Raw Core rows, with new per-entity `unit_source`, `unit_reason` and
  `statistics_metadata`.
- `last_reset` is restored by a second recorder call, or omitted with a
  warning.

*Engineering impact.* The delegated-read path passes sanitized payloads
through generically, and no Engineering consumer parses upstream
`ha_get_history` output. A `grep` of the runtime finds the tool only in
`ha_core_readmission/routes.py:92`. So Engineering will not break, but
**clients see a different history shape**. The owner should treat this as a
client-visible output change to disclose in the release notes.

### 3b. Power and fan dependencies

| Tool | vs 8.5.0 | 8.6→8.7 | Evidence |
|---|---|---|---|
| ha_list_services | Unchanged | annotations only | Descriptions only |
| ha_get_state | Unchanged | description text | Body and `_do_states` identical |
| ha_call_service | Compatible change | `idempotentHint: false` added; description changed; input structure equal | Return, verify and wait logic unchanged (`tools_service.py:1813`). New refusal of snapshot-delete routes through `guard_snapshot_route` (`backup_access.py:225`, #2636), which does not apply to `fan`/`light`/`switch`. Snapshot-route deletes need no Engineering change. The `ha_get_operation_status` default timeout drops from 10 s to 8 s; that tool is not used by Engineering |

Required bindings: the `FAN_RELEASES` and power release maps
(`fan/contracts.py:16-22`, `power/contracts.py:9-18`), plus the
`upstream_fan.py` and `upstream_power.py` entry and source checks.

### 3c. Dashboard-write dependencies

| Tool | vs 8.5.0 | 8.6→8.7 | Evidence |
|---|---|---|---|
| ha_config_get_dashboard | 35ee7247 / 9d193def / 3d5eb7f0. **Compatible for Engineering's calls; incompatible upstream** | structural | Upstream adds `describe` and removes `mode` (search is now implied by `query`). Searches span all dashboards when no `url_path` is given, and results carry `truncated`. `openWorldHint` changes from false to true (#2700, #2671). Engineering calls only `list_only=true` and `url_path` (`upstream_dashboard.py:513,611,634`); its `"mode"` at `:1626` is an output metadata key, not this input. `describe=true` runs QuickJS inside HA, so it must stay withheld |
| ha_get_skill_guide | See 3a | — | Fixed internal read of `references/dashboard-guide.md`. `clients/mcp.py:359-371` needs 8.7.0 |
| ha_config_set_dashboard | Compatible change (behaviour narrowed) | `readOnlyHint: false`, `idempotentHint: false` and `openWorldHint` true added; input structure equal to 8.6 (`02cde8fb`) | Parameters, the `config_hash` optimistic lock and `compute_config_hash` are unchanged. New pre-write rejections of `""` and `{"item": …}` list slots. One extra read occurs before inner patches. With component 2.2.2, component-path saves add advisory card `warnings` after QuickJS checks, which can take up to about 20 s (`dashboard_edit.py:211-221`). **Measured by capture (section 10, Q3):** the first write after each Core start took 5.9–6.7 s (QuickJS start-up; the first write after a fresh container also installs `quickjs-ng`); later writes took 0.27–0.87 s with a custom card and about 0.012 s without. This holds with 8.5.0 as well as 8.7.0, because the component does the work. With component 2.2.0 every write took ≤ 0.019 s. Engineering's setter timeout must still be checked against the ~6 s first write and the 20 s upstream bound |

Required bindings:
- `upstream_dashboard_builtin_attestations.json`;
- `f3_dashboard/provider.py` `EXACT_CONTRACTS`;
- `f3_dashboard/atomicity.py:17-26,56-66`, a new source commit and Core
  pairing (currently 8.6.0 is paired with Core 2026.9.4, not 2026.10.x);
- `f3_dashboard/raw_evidence.py`;
- `f3_dashboard/constants.py`.

### 3d. Filesystem reads (HAMCP-162)

| Tool | vs 8.5.0 | Evidence |
|---|---|---|
| ha_config_get_yaml | Unchanged in the server (docstrings and annotations only) | Registered by the same gate as the file tools (`tools_yaml_read.py:519-528`) |
| ha_list_files | Compatible, narrowed | Component 2.2.2 omits `secrets.yaml`, `approval_pin.json` and `.storage` from listings (`custom_components/ha_mcp_tools/__init__.py:1335-1337`) |
| ha_read_file | Compatible, narrowed | `approval_pin.json` is now denied (`const.py:95`). The `secrets.yaml` masking exception now checks the resolved target, closing a symlink bypass (`__init__.py:681`). **No PEM masking of file content:** only canonical `secrets.yaml` is masked (`__init__.py:1244,2841`) |

**Registration and binding.**
- **Corrected by capture.** The earlier text said these tools register only
  when the flag is on *and* a component of at least `1.2.0` is detected. That
  is wrong. Registration depends only on `enable_filesystem_tools`
  (`tools_filesystem.py:415-424,949-959`; `tools_yaml_read.py:519-528` uses the
  same gate). That flag is a beta sub-flag: it is forced off unless
  `ENABLE_BETA_FEATURES` is also on. `MIN_COMPONENT_VERSION = "1.2.0"`
  (`:98`) is checked per call, not at registration. The earlier 77-tool
  standalone captures had `ENABLE_BETA_FEATURES` unset, which is why the
  three tools were absent. With beta on, both 8.5.0 and 8.7.0 advertise 80
  tools (section 10, Q1).
- Engineering has **no runtime binding** for these three tools today.
- The whole-catalog validator refuses an 80-tool catalog, while
  dependency-scoped paths work (`docs/V2_4_1_BETA6_ACCEPTANCE.md:56,90`;
  `docs/V2_4_1_BETA8_ACCEPTANCE.md:64-68`).
- An 8.7.0 admission does not change that unless HAMCP-162 classifies them.

## 4. New and removed tools; catalog count

- **Reference profile.** No tools were added or removed. 8.5.0, 8.6.0 and
  8.7.0 each advertise 77 tools with identical name sets, as captured.
- **Full source.** The registered tool-name sets at `v8.5.0` and `v8.7.0` are
  identical: 88 tools including every gated one.
- **Gates.** These gates are unchanged:
  - dev tools (`tools_dev.py:2391`)
  - screenshot (`tools_dashboard_screenshot.py:305`)
  - security-policy tool (`tools_security_policy.py:161`)
  - code mode (`tools_code.py:1280`)
  - YAML editing (`tools_yaml_config.py:523`)
  - filesystem tools
- **New defaults.** No new default exposes a tool. Snapshot controls are
  actions inside the existing `ha_manage_backup`. Helper writes through the
  component change the internal path of the existing `ha_config_set_helper`.
  New write actions inside existing write tools came in 8.6.0 (#2529, #2592)
  and 8.7.0 (#2654, area null-clears). None is delegated by Engineering.
- **`ENABLE_TOOL_SEARCH` (#2670).** The default is off, and the catalog is
  unaffected. When on, the catalog becomes pinned tools plus `ha_search_tools`
  and `ha_call_{read,write,delete}_tool`. Production must keep it off.
- **Production count (captured; section 10, Q1):**
  - **80** for both 8.5.0 and 8.7.0 under production-mirroring settings, with
    identical name sets: 88 minus `ha_config_set_yaml`, `ha_write_file`,
    `ha_delete_file`, two `ha_dev_*` tools, `ha_get_dashboard_screenshot`,
    `ha_manage_custom_tool` and `ha_manage_security_policy`.
  - The count does not depend on the component version. The 8.7.0 descriptors
    do (the `ha_search` variant).
  - That matches the owner-stated 8.5.0 count. The 79 in the HAMCP-162 note
    could not be reproduced. The owner has since reported that production
    shows 80, so the question is settled (item C3 in section 9).
  - Engineering must still expose its own count (87: 59 native, 28
    delegated; `docs/V2_4_1_BETA9_ACCEPTANCE.md:78`), with no filesystem tool.

## 5. Settings and options (#2637, #2669)

**What changed.** Each setting is now declared once, as a Pydantic field with
`Setting(...)` metadata in `src/ha_mcp/config_settings.py`.
`scripts/generate_app_options.py` generates the add-on `config.yaml` options
and schema plus `app_options.json`. `config.py` is now a re-export shim.
#2669 changes only i18n and help text.

**No renames, removals or default changes.** All environment aliases and
defaults match v8.5.0; only bounds moved into `Setting(range=)`. These are
preserved:
- `HAMCP_ENABLE_FILESYSTEM_TOOLS` (`config_settings.py:640-642`, default False,
  beta)
- `ENABLE_YAML_CONFIG_EDITING` (`:580-582`, default False, beta)
- `ENABLE_TOOL_SECURITY_POLICIES` (default False)
- `ENABLE_TOOL_SEARCH` (default False)
- `REDACT_SECRETS` (default False)
- the `DISABLED_TOOLS` / `PINNED_TOOLS` seeds (`:738-742`)

**New settings and options** (#2636). Exactly two Settings fields are new:
- `enable_snapshot_actions`, default True. It is the old behaviour; false
  blocks every snapshot action, including list.
- `backup_read_only`, default False. True blocks create, restore and delete.

Two related snapshot-delete controls are **retained, not new**: they already
exist in v8.5.0 (`src/ha_mcp/config.py:436-447`) with the same aliases and
defaults:
- `enable_snapshot_delete`, default False.
- `snapshot_delete_min_age_days`, default 7.

**Add-on config.yaml.** It gains `enable_snapshot_actions` and
`backup_read_only` (`bool?`), plus `discovery: [mcp]`. Options are reordered
and none are removed.

**What happens on update.** The override files keep their names and code
paths, so saved settings carry over without migration:
- `/data/feature_flags.json` (`config_registry.py:195`)
- `backup_settings.json` (`:270`)
- `tool_config.json` (`server.py:455-500`)

**Precedence.** Override precedence is unchanged
(`config_overrides.py:34,108,259,303`). `start.py` `export_app_options`
(`:258,518`) exports always-present keys with defaults. Beta keys, including
the filesystem and YAML flags, are exported only if present. On the stable
add-on they come from `feature_flags.json`.

**Post-update checklist.** *Conditional:* this applies only if adoption is
reopened (see "Decisions recorded"). It would be owner-run, after the
Engineering beta in section 8 is deployed.
1. Before updating, take a partial backup of the ha-mcp add-on, so
   `/data/feature_flags.json`, `backup_settings.json`, `tool_config.json` and
   `secret_path.txt` are captured.
2. After the update, open the add-on Configuration tab and set
   `enable_snapshot_actions` and `backup_read_only` deliberately.
   Defaults `true` / `false` reproduce 8.5.0 behaviour.
3. In the ha-mcp settings UI, confirm:
   - filesystem tools are on (origin `file`);
   - YAML config editing is off;
   - tool-security policies are off;
   - tool search is off;
   - `ha_write_file` and `ha_delete_file` are still disabled;
   - no mandatory-tool warning is shown.
4. Decide `redact_secrets`. It is off by default and is the HAMCP-162
   recommended prerequisite. #2714 PEM redaction applies only when it is on.
5. Under Settings → Devices & services, a discovered "Model Context Protocol"
   entry appears. Its URL contains the secret path. It is re-announced on
   every add-on start. **Corrected by capture (section 10, Q2):** the earlier
   advice was "Ignore it", but Ignore is not inert. The next re-announcement
   writes the URL into the ignored entry's data in
   `.storage/core.config_entries`, and from there into every backup.
   - **Configure** stores the URL on disk at once. Do not configure it unless
     HA Core should connect to ha-mcp.
   - **Leaving it unactioned** keeps the URL in memory only, in Core.
   - **Owner rule (2026-10-15):** if 8.7.0 is ever deployed, never act on the
     ha-mcp discovery notification: neither Configure nor Ignore.
6. Component: under the 2026-10-15 decision, production stays on component
   2.2.1, 2.2.2 is not taken, and HACS auto-update stays off. Taking 2.2.2
   (which brings #2671, section 10, Q3) would need a new owner decision. If
   one is ever made: update through HACS and restart Core. HACS only refreshes
   metadata; nothing installs automatically. The "File & YAML Tools" entry
   needs no reconfiguration.
7. Through Engineering, read `server_info` and confirm all of the following:
   - upstream identity `ha-mcp-v8.7.0-600677bb`;
   - an upstream catalog of 80 tools;
   - `ha_list_files`, `ha_read_file` and `ha_config_get_yaml` present;
   - `ha_write_file`, `ha_delete_file` and `ha_config_set_yaml` absent;
   - the Engineering tool count unchanged.
8. Expect `secrets.yaml` and `approval_pin.json` to disappear from
   `ha_list_files` output.
9. Run the acceptance canaries from section 8, step 4.

## 6. Component pairing (#2618, #2697, #2614)

**Versions.** Component `custom_components/ha_mcp_tools/manifest.json` is
2.2.0 at `v8.5.0`, 2.2.1 at `v8.6.0` and **2.2.2 at `v8.7.0`**. 2.2.2 adds
`after_dependencies` `energy` and `recorder`.

**No config-entry migration.** `config_flow.py:380` has `VERSION = 1` and
there is no `async_migrate_entry`. Entry types are `tools` and `server`; a
missing type means `tools`. The "File & YAML Tools" entry is unaffected.

**New capabilities in 2.2.2** (`websocket_api/constants.py:103-182`):
`helper_schemas`, `helper_item`, `helper_write` (#2618), `core_contract`,
`dashboard_cards`, `dashboards_docs` and `config_entries_subentry_data`.
`search_unified` and `template_diagnose` were already in 2.2.1.

**Handshake.**
- The server negotiates per capability and falls back to Core WebSocket
  commands when a capability is missing
  (`component_helper_collections.py:32`).
- Writes fall back only when the first attempt provably never ran.
- The only hard floor is `MIN_COMPONENT_VERSION = "1.2.0"`, which the file
  tools check per call. It does not decide whether they are registered
  (section 3d).
- *Captured (section 10, Q4):* all four pairings of 8.5.0 or 8.7.0 with 2.2.0
  or 2.2.2 work for searches and governed dashboard writes, so either update
  order works. Component 2.2.1, production's component, was not captured.

**Other component changes.**
- **#2697:** helper domains come from Core's helper flows, and component
  search scrubs secrets.
- **#2614:** component `call_service` / `bulk_call_service` carry the
  caller's Context, so writes are attributed to the calling user.

**Fast search.** When the component advertises `search`,
`device_registry_child_semantics` and `search_unified`, `ha_search` runs a
single component-side unified search (`tools/search/component.py:270-277`), and
the advertised `ha_search` descriptor changes as described in section 3a.
Engineering already models this as a two-variant descriptor pair for 8.6.0
(`providers/upstream_search_8_6.py`). 8.7.0 needs its own pair, captured with
component 2.2.2.

**Must the component and add-on update together?**
- Technically, no.
- For Engineering admission, the pairing matters because the exact 8.7.0
  descriptor Engineering must admit depends on the component through the
  `ha_search` variant. The filesystem tool set does **not** depend on the
  component: registration is flag-only (section 3d). The component matters to
  the file tools only through the per-call `1.2.0` floor.
- Admission evidence must therefore be captured for the exact pairing that
  would be deployed, and the rollback pairing must be shown to keep the
  existing 8.5.0 binding valid.
- **Verified by capture (section 10, Q1 and Q4):** 8.5.0's catalog is
  byte-identical with 2.2.0 and 2.2.2 (fingerprint `f112aa40…`). 8.7.0's
  catalog differs between the two (`95c69e8e…` with 2.2.0, `0906cbce…` with
  2.2.2) only in the `ha_search` reduced schema and one description.
- **Updating together is not required for #2671 either.** #2671 arrives with
  component 2.2.2, whichever server runs. 8.7.0 with 2.2.0 was captured
  working and runs no card code, because 2.2.0 contains none.
- **Component 2.2.1 (production's component; source only, not captured).**
  Upstream `v8.6.0` ships component 2.2.1. Its tree contains no `quickjs`,
  `linkedom` or npm code, so by source it avoids #2671 as 2.2.0 does. Unlike
  2.2.0, it already advertises `search_unified` and `template_diagnose`
  (`custom_components/ha_mcp_tools/websocket_api.py:374,440` at `v8.6.0`).
  It also advertises `search` and `device_registry_child_semantics`, so
  8.7.0 + 2.2.1 would take the `search_unified` `ha_search` variant
  *(inferred)*. That descriptor, its fingerprint and its dashboard behaviour
  were not captured. A future admission must capture 8.7.0 + 2.2.1 itself
  rather than reuse the 2.2.0 or 2.2.2 results.

## 7. Security deltas

| Change | Delta | Default | Engineering relevance |
|---|---|---|---|
| #2714 PEM redaction | Add-on option values matching `-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----` are redacted (`redaction.py:73,165,192`). Each PEM line is learned so `RedactSecretsMiddleware` (`:456`) scrubs log echoes | **Off** (`REDACT_SECRETS=False`, `config_settings.py:544-546`) | Affects `ha_get_app` output only when enabled. **Not applied to `ha_read_file` / `ha_config_get_yaml` content.** Engineering sanitization must remain the control for HAMCP-162 |
| #2613 MCP discovery | The add-on POSTs `{"service":"mcp","config":{"url":"http://<host>:9583<secret_path>"}}` to Supervisor `/discovery` on every start on Core ≥ 2026.10 (`app_discovery.py:50-90`; `config.yaml discovery: [mcp]`). No opt-out | **On**, unconditional | **New disclosure** of the secret-path URL into HA Core's discovery flow, visible to HA admins. It does not change the authentication model. **Quantified in section 10, Q2:** in memory only while the discovery is unactioned; written to `.storage/core.config_entries`, and so into backups, once the entry is configured, or once it is ignored and then re-announced. Not readable by non-admin users or by `ha_read_file`. **Owner decision (2026-10-15):** not accepted as-is; adoption parked; an upstream opt-out is to be requested. If 8.7.0 is ever deployed, never act on (Configure or Ignore) the discovery notification |
| #2671 card checking | Component 2.2.2 runs dashboard-resource JavaScript in a QuickJS sandbox inside the HA process. It installs `quickjs-ng` on demand and downloads `linkedom` from `registry.npmjs.org` (integrity-checked, cached in `.storage`) (`card_runtime.py:17,85-88`; `custom_cards.py:36-38,585-600`). Upstream `SECURITY.md:35-53`: contains broken cards, "not an adversarial one" | Triggered by component dashboard writes (`dashboard_edit`) and by the component's card describe path (`ha_config_get_dashboard(describe=true)`); exact conditions in section 10.4 | **Confirmed by capture (section 10, Q3):** Engineering-equivalent dashboard writes trigger it whenever component 2.2.2 is installed, with ha-mcp 8.5.0 as well as 8.7.0. Every component dashboard write, even with built-in cards only, loads the card definitions; if `quickjs-ng` is not installed, that first load pip-installs `quickjs-ng==0.17.0.1` (version-pinned, not hash-pinned). Only a write whose saved dashboard contains a `custom:` card (or a describe of all or of custom cards) loads custom cards. If the registered `module`/`js` resources then resolve to any `/local/` or `/hacsfiles/` `.js` path, even one whose file is missing, `linkedom` 0.18.13 is loaded: from the `.storage` cache **without re-verification** if present, otherwise downloaded from `registry.npmjs.org` and sha512-checked once. Card JavaScript from `www/` or `www/community/` then runs in-process. Components 2.2.0 and 2.2.1 contain none of this code (2.2.1 by source only). **Owner decision (2026-10-15):** stay on component 2.2.1, do not take 2.2.2, keep HACS auto-update off, and request upstream hash-pinning, cache re-verification and a card-JS opt-out |
| #2636 backup controls | `guard_snapshot_route` refuses snapshot delete through `ha_call_service` and WebSocket routes | On | Net tightening |
| #2614 context | Component service calls attributed to the caller | On | Better audit |
| Component deny list | `approval_pin.json` denied; listings hide `secrets.yaml`, `approval_pin.json` and `.storage`; symlink bypass of the `secrets.yaml` exception closed | On | Tightening for HAMCP-162 |
| #2689 HACS | Per-request credentials | On | Neutral |
| #2683, #2703 | Read-only webhook alias; Nabu Casa cloudhook relay with a 60 s buffered reply. OAuth-gated cloudhooks return 400 `OAUTH_UNAVAILABLE` instead of a 401 challenge | Webhook transport only | **Not used by Engineering's topology.** The 400 guidance suggests auth mode `none`, which must not be followed |
| #2551 (8.6.0) | Non-admin tokens are refused locally on admin-only routes | On | Neutral with an admin token |
| #2654 | `ha_set_area_or_floor` JSON `null` now **clears** a field (was a no-op) | On | Not delegated. Matters for any future area write |
| #2695 | Removal-by-entity platform check | On | Upstream-direct safety fix |

I found no newly default-enabled destructive tool, and no change to the HTTP
endpoint's secret-path validation.

## 8. Admission plan (design only)

**Parked (owner decision, 2026-10-15).** PRs A–E below are a conditional
design, kept for reconsideration; none is scheduled. HAMCP-184 proceeds
independently, because explicit fail-closed provider dispositions are needed
regardless of 8.7.0. Reconsideration triggers are listed in "Decisions
recorded". If reopened, the plan must be re-based on the then-current `main`
and the then-chosen component pairing.

**Ownership.** HAMCP-184 / F-05 is assumed to own the general fix that makes
`provider_disposition` stop defaulting to `admitted`
(`upstream_tool_policy.py:730-733`: `.get(surface, "admitted")`). Independently,
the 8.7.0 entry **must carry explicit dispositions for every provider**. Its
admission tests must fail if any surface is absent.

**Current registry state at `690a00b`.**
- Five entries carry no `provider_dispositions`, so every surface defaults to
  `admitted`: 7.14.1, 7.14.2, 8.0.0, 8.1.0 and 8.2.0.
- 8.1.1 explicitly admits all four surfaces, including `backup` and
  `lifecycle`.
- 8.4.1 through 8.6.0 hold `backup` and `lifecycle`.

PR A should decide whether those legacy entries keep their historical
meaning or are explicitly held.

### 8.1 Compiled registry entry (`upstream_release_registry.json`)

```json
{
  "version": "8.7.0",
  "release_tag": "v8.7.0",
  "entry_id": "ha-mcp-v8.7.0-600677bb",
  "server_name": "ha-mcp",
  "source_repository": "https://github.com/homeassistant-ai/ha-mcp",
  "source_commit": "a96752286be1a6511f32a62e90213641c8d18068",
  "source_tree": "00e38a7b4720aa6955fdcacc4040d942c2332c13",
  "image_index_digest": "sha256:600677bbd7574dfb59682e40d6945f73fcc8852d3684b3671506602473942448",
  "image_revision": "051e45fab480967dc9bf79dd333106390b588537",
  "architecture_image_digests": {
    "linux/amd64": "sha256:a936dff6aa1be509c1c8aca2e3fc2071318958a7b49119bd07931c5ab683307a",
    "linux/arm64": "sha256:b37c70761751be87d7bb8ed003bd7098d76700d66b5b659ad5ded15624c73bc2"
  },
  "addon_artifact_digests": {
    "linux/amd64": {
      "index_digest": "sha256:d45f5498d77ae18fab1eaab1bb5bdc0c94b2e60491baab05582e4ae0167a1e6a",
      "image_manifest_digest": "sha256:c6162daaed0bdc063cba8d1f1fec38c5c66b9ba40bfe1271346c9d36acbf58fc"
    },
    "linux/arm64": {
      "index_digest": "sha256:5affb15bedec8f12233c2e6da94db2501a27adeacbe0f6fb6c612a04ce8bd0dd",
      "image_manifest_digest": "sha256:4a2dae9ba96029ec550543c2fc3ecef0689987c447edaa1698f23865a4a8d18c"
    }
  },
  "allowed_protocol_versions": ["2025-03-26"],
  "advertised_tool_count": 77,
  "catalog_fingerprint": "c6426571c1a7d38a6014f849a8f7f522b1bb22433e71a7455d34f0313b025e18",
  "provider_dispositions": {
    "backup": "held",
    "dashboard": "admitted",
    "lifecycle": "held",
    "read_gateway": "admitted"
  },
  "approval_status": "reviewed",
  "review_date": "<admission PR date>",
  "review_provenance": ["<generated>"],
  "capture_format_version": 1,
  "capture_resource": "docs/evidence/upstream-read-compatibility/ha-mcp-8.7.0.json",
  "capture_sha256": "<generated>",
  "artifact_evidence_resource": "docs/evidence/upstream-read-compatibility/ha-mcp-8.7.0-contract-review.json",
  "artifact_evidence_sha256": "<generated>",
  "policy_resource": "upstream_tool_policy_8_7_0.json",
  "policy_sha256": "<generated>",
  "runtime_contract_fingerprint_model": "ha-mcp-operational-tool-descriptor-v2",
  "strict_full_contract_fingerprint_model": "ha-mcp-strict-full-contract-v1",
  "strict_full_contract_fingerprint": "<generated>",
  "tool_contracts": "<generated, 77 entries>",
  "error_contract_fingerprint": "<re-derived; see below>",
  "entity_lookup_missing_resource_status": "<re-derived from the 8.7.0 lookup path>",
  "dashboard_attestation": "<regenerated: attestation_fingerprint, compiled_constraints_fingerprint, entry_id, status>"
}
```

The key names and shapes were checked against the compiled 8.6.0 entry at
`690a00b`. Note that the registry keys architectures as `linux/amd64` and
`linux/arm64`; the add-on's `aarch64` repository maps to `linux/arm64`. Generate the entry with
`scripts/review_upstream_read_release.py candidate`; do not hand-write it.

**Dispositions.** Every surface is explicit:
- `read_gateway`: admitted.
- `dashboard`: admitted. This is conditional on re-validating setter latency
  and the QuickJS warnings (section 3c). It is also conditional on the
  component pairing: under the 2026-10-15 decision, production keeps
  component 2.2.1, which has no card runtime by source, so #2671 would not
  apply; taking 2.2.2 would need a separate owner decision (section 10, Q4).
- `backup`: held.
- `lifecycle`: held.

The unchanged `held` values match 8.5.0 and 8.6.0. #2636 does not justify
releasing backup.

**Error contract.** 8.6.0 reuses the 8.4.1 error contract `03000635`. 8.7.0's
envelope keys are unchanged, but suggestion ordering and new codes changed
(#2551 in 8.6.0; `ws_failure_code`). Re-derive the contract and map it in
`ha_mcp_readmission/ha_mcp.py:130-160`.

**Other registry-level bindings:**
- `EXACT_RUNTIME_TOOL_ORDER_FINGERPRINTS` (`upstream_tool_policy.py:71-76`)
  needs an 8.7.0 key. Without one, the order check is skipped
  (`:976-983`), which would be a silent weakening.
- `upstream_tool_policy.py:2414`
  (`if release.version != "8.6.0"`, which skips the add-on `_meta.ha_mcp`
  policy mutation) must also cover 8.7.0, because 8.7.0 has no `_meta.ha_mcp`
  either.
- Add an 8.7.0 key to `_REVIEWED_SUCCESS_ENVELOPE_MODELS`
  (`upstream_read_gateway.py:151-153`).

### 8.2 Policy JSON and per-version bindings

**Policy file.** Create `upstream_tool_policy_8_7_0.json` with 77 tools,
generated from the 8.6.0 policy with `--base-policy`.
- Classifications are unchanged, including:
  - the 24 `automatic_read` tools;
  - `ha_manage_blueprints` as `mixed_or_requires_wrapper`, exposed as
    `ha_get_blueprint`;
  - the three platform reads as `mixed_or_requires_wrapper` with adapters;
  - `ha_remove_helpers_integrations` as `persistent_write`;
  - everything else as before.
- Every `input_schema_fingerprint` and `reviewed_annotations` is regenerated
  from the 8.7.0 capture.

**New adapter modules.** Do not edit the 8.6 modules in place; they are
binary-owned per version.
- **`providers/upstream_reads_8_7.py`**, token `ha-mcp-8.7.0-public-reads-v1`.
  - Carry over the 8.6 public schemas and `read_arguments` rules: scene
    requires `scene_id`; template-only eval; skill `file`.
  - Add `ha_config_list_helpers`: keep the 8.6 public `helper_type` enum, and
    pin `describe=false`, `helper_id=None`, `menu_choice=None`.
  - Add `ha_get_history`: withhold `core_options` (send `None`) and
    `include_schema` (send `false`).
  - Take the 23 `INPUT_FINGERPRINTS` from the 8.7.0 capture.
- **`providers/upstream_search_8_7.py`**, token
  `ha-mcp-8.7.0-search-descriptor-pair-v1`. It needs a reference variant and a
  component-unified variant, captured with the component actually deployed
  (2.2.1 under the 2026-10-15 decision; the section 10 capture used 2.2.2).
  `_trusted_release` currently hard-codes 8.6.0.
- **`providers/upstream_blueprint.py`**: add 8.7.0, input `6a09a0f2…`, source
  `a9675228…`.
- **`providers/upstream_platform_reads.py`**: add 8.7.0 to `_SOURCES`, plus
  `_FINGERPRINTS` (health `569e6a4d…`, app `7c1fba17…`, integration
  `798a5110…`, all equal to 8.6.0), and `ADAPTER_BINDINGS`
  `ha-mcp-8.7.0-{health,app,integration}-v1`.
  - Update `capabilities.py:434` (the platform version tuple).
  - Update the version and commit gates at `upstream_tool_policy.py:1341-1369`.

**Fan, power and dashboard.**
- Fan and power: `fan/contracts.py` `FAN_RELEASES` and `power/contracts.py`.
  The required-tool sets are unchanged.
- Dashboard:
  - `upstream_dashboard_builtin_attestations.json`
  - `f3_dashboard/provider.py` `EXACT_CONTRACTS` (setter `02cde8fb…`, getter
    `3d5eb7f0…`)
  - `f3_dashboard/atomicity.py` (8.7.0 source plus a Core 2026.10.1 pairing)
  - `f3_dashboard/raw_evidence.py`
  - `clients/mcp.py:359-371` (8.7.0 skill-guide argument rewrite)

**Filesystem tools: out of scope.** They remain unbound, as they are today.
The 8.7.0 admission must **not** silently classify them. That is HAMCP-162.

### 8.3 Core 2026.10.1 child-device binding

**Today:**
- `ha_core_readmission/routes.py:203-216` admits 8.5.0 under every
  child-device profile through `delegated_device_adapters=("8.5.0",)`.
- The comparable rule for 8.6.0 is an explicit exact binding at
  `(8.6.0, 2026.10.0, CHILD_DEVICE_NAME_PARTS)`.
- 8.6.0 has no 2026.10.1 binding, so on 2026.10.1 it refuses with
  `ha_mcp_child_device_contract_unproven`.

**Proposal.** Add a binary-owned exact binding of the same form for
`adapter_version == "8.7.0"` with `core_version in {"2026.10.0", "2026.10.1"}`
and `CHILD_DEVICE_NAME_PARTS_PROBE_PROFILE`. 2026.10.0 is the Core rollback
slot.
- Do **not** add 8.7.0 to `delegated_device_adapters`; that would change the
  signed profile fingerprint.
- Evidence comes from the device-dependent tools
  (`DEVICE_DEPENDENT_DELEGATED_TOOLS`: `ha_get_device`, `ha_get_entity`,
  `ha_get_entity_exposure`, `ha_get_overview`, `ha_search`, …):
  - `ha_get_device` is AST-identical to 8.5.0.
  - `ha_get_entity` only adds `uses_device_name`.
  - Component search relies on the component's
    `device_registry_child_semantics` capability.
- Prove it with the existing 2026.10.1 lane-provenance fixture
  (`tests/fixtures/core_2026_10_1_lane_provenance.json`) and new
  device-registry read fixtures from the 8.7.0 capture.
- In `scripts/core_lane_bindings.py:42-55`, 2026.10.0 and 2026.10.1
  `CoreBinding` keep `"8.5.0"` as the child consumer until 8.7.0 becomes
  current. They change in the PR that rotates the support window.

### 8.4 CI support window

`ci/support-window.yaml`: `ha_mcp.candidate` changes from `null` to the 8.7.0
block:
- `version`
- `image_index_digest`
- source commit and archive SHA
- `addon_runtime` with `candidate_power` / `legacy` on amd64 and arm64,
  matching the 8.5.0 shape

The comment at lines 51-53 changes from "8.6.0 HOLD" to "8.7.0 candidate".
`current` stays 8.5.0 and `rollback` stays 8.4.3 until acceptance.

Matching edits:
- `scripts/support_window.py:93-98,247-280` (version-keyed checks)
- `tests/test_support_window.py:46`
- `tests/test_latest_stable_release.py:66`
- a new `tests/test_ha_mcp_870_candidate_lane.py` modelled on the 860 lane
  test

### 8.5 Exact-image and add-on lanes

These are protected workflow and script surfaces.
- **`.github/workflows/ci.yml`:**
  - the matrix at `:574-600`;
  - exact-image gateway lanes at `:603-882`, which compare against
    `docs/evidence/upstream-read-compatibility/ha-mcp-${v}.json`;
  - add-on runtime lanes at `:884-1190`, including the power candidate at
    `:1146-1164`.
- **Scripts:**
  - `scripts/ha_mcp_850_container_acceptance.py:75-86` (version codes and
    allowed Core pairs: add 870 + 2026.10.1)
  - `scripts/exact_image_read_gateway_acceptance.py:155-169`
  - `scripts/exact_image_readmission_acceptance.py:33-37,69-81`
  - `scripts/exact_addon_runtime_acceptance.py:111-115,206-217`
  - `scripts/fake_ha_read_gateway_contract_server.py:901,1177`
- **Evidence:**
  - `docs/evidence/upstream-read-compatibility/ha-mcp-8.7.0.json`
  - `-contract-review.json`
  - `ha-mcp-8.7.0-component-search.json`
  - `tests/fixtures/ha_mcp_8_7_0/{provenance,read-results,component-search-results}.json`
  - `tests/fixtures/ha_mcp_870_candidate.json`

### 8.6 Expected Engineering release

- **Release:** one Engineering beta, `2.4.1-beta.N` (N assigned later; the
  next after beta.9 if nothing intervenes). It admits **both 8.5.0 and
  8.7.0**.
- **Defaults kept:**
  - The advertised upstream reference stays 8.5.0.
  - The compiled registry `default_version` stays `7.14.1`, as it did for the
    8.6.0 beta.
  - 8.6.0 remains compiled.
- **Release notes must disclose:**
  - the client-visible `ha_get_history` output change;
  - the dashboard getter `openWorldHint` change;
  - the #2613 discovery disclosure and, only if a later owner decision takes
    component 2.2.2, the #2671 QuickJS card checks, as consequences the owner
    has then accepted.

### 8.7 Deploy order and rollback

1. Deploy Engineering `2.4.1-beta.N`, which admits 8.5.0 and 8.7.0. Accept it
   on 8.5.0 with no upstream change: `server_info` still shows `admitted_exact`
   on `ha-mcp-v8.5.0-e1538bcd`, the canaries pass, and no behaviour changes.
2. Owner step. Take a partial backup of the ha-mcp add-on. Then update the
   ha-mcp add-on to 8.7.0 and run the section 5 checklist. Under the
   2026-10-15 decision the component stays at **2.2.1** (2.2.2 not taken, HACS
   auto-update off), which avoids #2671 by source. Admission evidence must
   then include a captured 8.7.0 + 2.2.1 descriptor pair; the captured
   8.7.0 + 2.2.0 and 8.7.0 + 2.2.2 pairs do not substitute for it. *Earlier
   design, superseded:* update the component to 2.2.2 in the same window.
3. Acceptance on 8.7.0:
   - `server_info` shows `ha-mcp-v8.7.0-600677bb` and `admitted_exact`
     (or the documented 80-tool state);
   - all 28 reads;
   - a typed fan and light/switch canary with state verification;
   - one governed dashboard write, verified by readback and restored;
   - child-device reads on Core 2026.10.1;
   - a skill-guide fixed read;
   - a history read showing the new shape;
   - refusals for `describe`, `core_options` and the scene list.
4. **Rollback.** Restore the 8.5.0 add-on from the partial backup, or reinstall
   8.5.0 and restore `/data`.
   - The component needs no rollback: per-capability negotiation means no
     hard floor beyond 1.2.0, and 8.5.0 + 2.2.1 is today's production state.
     (If 2.2.2 were ever taken: the capture showed 8.5.0 + 2.2.2 working, with
     a catalog identical to 8.5.0 + 2.2.0 (section 10, Q4), and the #2671 card
     checks would keep running under 8.5.0.)
   - The discovery entry created by 8.7.0 stays in Core's discovery list.
     Do not act on it (section 5, item 5): ignoring it persists the URL at the
     next announcement.
   - Engineering needs no rollback, because it admits both versions.
   - Rolling back reintroduces the #2695 defect for direct ha-mcp clients
     (section 9, item C1).

### 8.8 8.6.0: retire from HOLD or keep?

**Recommendation: keep the compiled 8.6.0 entry unchanged in the 8.7.0
admission PR, and record 8.6.0 as "superseded, never to be deployed".**
- Removing a compiled entry is a runtime change with its own review.
- The entry is harmless when the add-on never runs it.
- Its tests pin the 8.6 projections that the 8.7 adapters reuse.

Retire it in a later cleanup, after 8.7.0 becomes `current`. In that cleanup,
remove `upstream_reads_8_6.py`, `upstream_search_8_6.py`, the 8.6.0 bindings,
fixtures and lanes together.

### 8.9 Size estimate and PR split

**Size reference.** The 8.6.0 admission was `e8c48f9`: 57 files, +19,462/−147,
mostly generated evidence. The PR #238 merge was 69 files, +21,528/−352. The
8.6.0 platform reads (`1b551c5`) were 53 files, +8,882.

**Estimate for 8.7.0:** about 60–80 files and +18,000–25,000 lines, of which
about 80% is generated evidence and fixtures. Hand-written runtime changes are
about 600–1,000 lines across about 20 files.

**Proposed split:**

| PR | Scope | Protected paths | Depends on |
|---|---|---|---|
| A (may already be HAMCP-184) | F-05: `provider_disposition` defaults to `held`; every registry entry gets explicit dispositions; a loader test rejects an absent surface | runtime | — |
| B | 8.7.0 compiled admission: registry entry; policy JSON; evidence; `upstream_reads_8_7` / `upstream_search_8_7`; blueprint, platform, fan, power and error-contract bindings; order fingerprint; `clients/mcp.py`; Core 2026.10.x child-device binding; tests | runtime, registry | A (or explicit dispositions in B regardless) |
| C | Dashboard family for 8.7.0: attestation, `EXACT_CONTRACTS`, atomicity with the 2026.10.1 pairing, raw evidence, setter latency and QuickJS-warning validation | runtime | B |
| D | CI candidate: `support-window.yaml` candidate; exact-image and add-on lanes in `ci.yml`; acceptance scripts; the 870 lane test; Core lane bindings for the 870 pair | workflow, support window | B (and C for dashboard lanes) |
| E | Materialize `2.4.1-beta.N` and add the acceptance document | release metadata | B, C, D |

B and C can merge into one PR if the owner prefers fewer reviews. C is
separate because the dashboard is the write family affected by the new
upstream latency and QuickJS card-runtime behaviour. It is not the only
admitted write family: B carries the power and fan bindings, whose typed
write canaries section 8.7 also requires.

HAMCP-162 (filesystem reads) stays a separate task, sequenced after E.

## 9. Findings that contradict the stated assumptions

**C1 (High, owner decision): the #2695 defect is not specific to 8.6.0.**
- The unchecked flow-helper removal exists in **production 8.5.0**
  (`tools_integrations.py:405-467`) and in **rollback 8.4.3**.
- It was introduced with `ha_delete_helpers_integrations` in `c45ba5a9`
  (2026-04-28, #1056), first released in **v7.4.0**.
- Holding 8.6.0 does not avoid the defect. Production has carried it all
  along, and 8.7.0 is the first release that fixes it.
- Engineering never dispatches the tool, but any direct client of the ha-mcp
  endpoint can. Under the one-connector topology that should be no one. The
  add-on still serves its secret-path URL on the host network, and from 8.7.0
  it announces it to Core discovery.
- **This strengthens the case for 8.7.0. It also weakens the premise that
  8.5.0 rollback is "safe" with respect to #2695.**

**C2 (Medium): the HOLD has no committed defect evidence.**
- The repo carries only "upstream defect #2695" labels.
- `#2695` is the upstream *fix PR*, not an issue.
- The owner decision record (HAMCP-173) should cite the defect mechanism and
  the affected versions (≥ 7.4.0, < 8.7.0).

**C3 (Medium): the production catalog count is disputed.**
- The brief states 80 tools.
- Register HAMCP-162's 2026-10-14 note records 79 (77 + 2) live, "so one of
  `ha_config_get_yaml` / `ha_list_files` / `ha_read_file` appears hidden".
- **Capture result (section 10, Q1):** under the stated settings, 8.5.0 and
  8.7.0 each advertise exactly 80 tools, with either component version. 79
  cannot be produced from the stated settings. A live 79 would mean
  production differs from them, for example an extra `DISABLED_TOOLS` entry
  in `tool_config.json`, or a different flag origin.
- **Settled (owner-reported, 2026-10-15):** production `server_info` and
  health show 80 upstream tools; the 79 was a transient settings state. This
  is the owner's report, not a reviewer observation; no further production
  check is requested.

**C4 (Medium): 8.7.0 is not "8.6.0 + fixes".**
- It is 235 commits beyond 8.6.0.
- It includes new **structural** input changes on Engineering-used tools:
  `ha_config_list_helpers`, `ha_get_history` and `ha_config_get_dashboard`.
- It includes a client-visible `ha_get_history` output change.
- It includes new annotations on 75 of the 80 production-profile tools.
- It includes two new security-relevant behaviours: Supervisor discovery of
  the secret-path URL, and in-HA QuickJS execution with an npm download
  through component 2.2.2.
- None of these is in the release-note list in the brief.
- **Refined by capture (section 10, Q4):** #2671 belongs to component 2.2.2,
  not to the ha-mcp 8.7.0 server. Component 2.2.2 runs card JavaScript under
  8.5.0 too, and 8.7.0 with 2.2.0 runs none. #2613 belongs to the 8.7.0
  add-on.

**C5 (Low): "the component and add-on must update together" is an
Engineering admission constraint, not an upstream requirement.** Upstream
negotiates per capability with fallback. Engineering, however, binds exact
descriptors that depend on the component through the `ha_search` variant. The
filesystem tool set does not depend on the component (registration is
flag-only, section 3d).

**C6 (Low): #2714 does not protect filesystem reads.** PEM redaction applies
only to add-on option values, and only with `redact_secrets` on. It does not
cover `ha_read_file` or `ha_config_get_yaml` content. HAMCP-162 must not
count on it.

**C7 (Low): 8.6.0 never had a Core 2026.10.1 child-device binding**
(confirmed, `routes.py:208-213`). Because 8.6.0 was also absent from
`core_lane_bindings.py`, the October Core rotation never needed one. 8.7.0
needs one explicitly (section 8.3).

## 10. Disposable capture (2026-10-15)

**Authority.** Owner decision 5 (Josh, 2026-10-15, recorded on HAMCP-187)
authorized a disposable local capture, for evidence only. Nothing here touched
live Home Assistant, the production ha-mcp add-on, Nabu Casa, Engineering, or
any real credential or endpoint path. The admin token was created inside the
throwaway config, and the MCP secret path was the synthetic
`/synthetic-review/mcp`. Everything was destroyed afterwards (section 10.9).

### 10.1 Method

| Item | Value |
|---|---|
| Core | `ghcr.io/home-assistant/home-assistant@sha256:130241f28d01fa80dfa3b44f7d89781c188d8642ecab9024f079f19f9f82baa0`, the 2026.10.1 pin in `ci/support-window.yaml:58-59`. Fresh empty config directory, `TZ=UTC` |
| ha-mcp 8.7.0 | `ghcr.io/homeassistant-ai/ha-mcp@sha256:600677bbd7574dfb59682e40d6945f73fcc8852d3684b3671506602473942448` (standalone, `serverInfo.version` 8.7.0) |
| ha-mcp 8.5.0 | `ghcr.io/homeassistant-ai/ha-mcp@sha256:e1538bcdadb13a5467bbb8258fba3262a05518585e18115742da638adec7057c` (standalone, `serverInfo.version` 8.5.0) |
| Component 2.2.0 | `custom_components/ha_mcp_tools` from upstream `v8.5.0` (`311d6dc273fb4e9a5b8cde0de15f69472a64fe44`) |
| Component 2.2.2 | `custom_components/ha_mcp_tools` from upstream `v8.7.0` (`a96752286be1a6511f32a62e90213641c8d18068`) |
| Component entry | One "File & YAML Tools" (`tools`) entry, created through the config flow. The in-process server entry was never created |
| ha-mcp settings | `ENABLE_BETA_FEATURES=true`, `HAMCP_ENABLE_FILESYSTEM_TOOLS=true`, `ENABLE_YAML_CONFIG_EDITING=false`, `DISABLED_TOOLS=ha_write_file,ha_delete_file`; settings UI disabled; read-only root filesystem with tmpfs scratch |
| Client | Stdlib MCP client, `initialize` at `2025-03-26`, then `tools/list` and `tools/call`. Fingerprints are `sha256` of the canonical sorted-key JSON of the tool list, the same method that reproduced the committed fingerprints |

**Why the beta flag is on.** `enable_filesystem_tools` is a beta sub-flag.
Upstream forces it off unless `ENABLE_BETA_FEATURES` is on, so "filesystem
tools ON" cannot be mirrored without the beta master flag. The production
add-on sets these through `feature_flags.json` rather than the environment;
the effective values are the same *(inferred from the override precedence in
section 5)*.

**Network.**
- `hamcp187-int` was a Docker `--internal` network with no route out. Core and
  ha-mcp sat on it, and ha-mcp never had any other network.
- Images were pulled by the host Docker daemon before any container started.
- For #2671, Core alone was then attached to a second, egress-capable network
  (phase C onward).
- A sniffer sidecar shared Core's network namespace and logged every DNS query
  and answer, every TCP SYN, the TLS SNI, plaintext HTTP request lines, and any
  other UDP to a non-internal destination. It wrote 263 events.
- **Positive control:** a TCP connect from Core to `example.com:443` at
  16:25:51Z was logged as `dns` and `syn` events.
- **Gaps.** The sniffer is restarted with each Core restart, which leaves a gap
  of about 7 s during Core start-up. HA's package log shows no installs in any
  gap. The first container recreate (R3) was not sniffed because the sniffer
  failed to start; it was repeated with the sniffer as R4, which is the run
  reported here.

**Dashboard writes.** The probe performs the same sequence that Engineering's
governed dashboard path sends, against a throwaway dashboard:
1. `ha_get_skill_guide` for `references/dashboard-guide.md` (the 8.6+ `file`
   form for 8.7.0, and `{skill, file}` for 8.5.0, matching
   `clients/mcp.py:359-371`), taking the best-practice key;
2. `ha_config_get_dashboard` with `url_path` for the `config_hash`;
3. `ha_config_set_dashboard` with the full config, the hash and the key;
4. a readback compared with the written config.

The variants were:
- `std`: built-in cards only;
- `schema`: a tile card with a deliberately wrong option type and an unknown
  card type;
- `custom`: adds the card `custom:hamcp187-card`, served from
  `www/hamcp187-card.js` and registered as a `/local/` module resource;
- `custombad`: the same card with an option its `setConfig` rejects.

The synthetic card records what globals exist when its top-level code runs,
and its `setConfig` throws a distinctive message, so running it inside HA is
visible in the write result.

**Timeline (UTC).**

| Time | Phase |
|---|---|
| 15:57 | A: Core started, internal network only |
| 15:58–16:03 | A2/B: discovery probe (section 10, Q2) |
| 16:04 | C: component 2.2.0 installed; egress network attached; `tools` entry created |
| 16:06–16:10 | 8.7.0 + 2.2.0: catalog, search and dashboard writes |
| 16:11 | E: component 2.2.2 replaces 2.2.0; Core restarted |
| 16:12–16:16 | 8.7.0 + 2.2.2: catalog, search and dashboard writes |
| 16:16–16:21 | R1 restart; R2 cache tamper; R3/R4 container recreate; B1 backup check |
| 16:22–16:24 | 8.5.0 + 2.2.2: catalog (with and without beta), search and writes |
| 16:24 | G: component 2.2.0 replaces 2.2.2 (downgrade); Core restarted |
| 16:25 | 8.5.0 + 2.2.0, then 8.7.0 + 2.2.0 card writes; sniffer positive control |
| 16:26 | I: `ha_read_file` deny check |

### 10.2 Q1: tool count

| Server | Component | Beta | Tools | Fingerprint |
|---|---|---|---|---|
| 8.5.0 | 2.2.2 | off | 77 | `0d7ecdb7f58b9b3f92fe05e0f3afbd213da6d5841263219d8c12c3dea1d77845` |
| 8.7.0 | 2.2.0 | off | 77 | `c6426571c1a7d38a6014f849a8f7f522b1bb22433e71a7455d34f0313b025e18` |
| 8.5.0 | 2.2.0 | on | **80** | `f112aa40…` |
| 8.5.0 | 2.2.2 | on | **80** | `f112aa40…` (identical to 2.2.0) |
| 8.7.0 | 2.2.0 | on | **80** | `95c69e8e1f4c9c7e1863572600f7a798b5fadab692a9471563e756beafd9ec80` |
| 8.7.0 | 2.2.2 | on | **80** | `0906cbcefee80254bdaa492a4b82401498c34cb4eb4e71dcb590f2ab2b5cb94e` |

**Method check.**
- The 8.5.0 beta-off row reproduces the committed reviewed 8.5.0 stock
  fingerprint (`upstream_tool_policy_8_5_0.json`, 77 tools) exactly.
- The 8.7.0 beta-off row reproduces this report's reference catalog exactly.
- So the capture method agrees with both committed and earlier evidence.

**Answers.**
- **Production-mirroring count: 80, for both 8.5.0 and 8.7.0.** The name sets
  are identical. The three tools beyond the 77 are `ha_config_get_yaml`,
  `ha_list_files` and `ha_read_file`. `ha_write_file`, `ha_delete_file` and
  `ha_config_set_yaml` are absent.
- **The 79-vs-80 dispute is not resolved in favour of 79.** No combination of
  the stated settings and either component version produced 79. The owner
  has since reported 80 in production (section 9, C3, settled).
- **The count does not depend on the component.** Registration is flag-only
  (section 3d, corrected).
- **The 8.7.0 descriptors do depend on the component.** With 2.2.2 (which
  advertises `search_unified`), `ha_search` drops `search_types`,
  `include_config` and `config_time_budget`, and `ha_config_set_helper` has a
  description-only change. 8.5.0's catalog is the same with either component.
- **8.5.0 vs 8.7.0 at 80 tools.** All 80 descriptors differ:
  - `_meta`: 80 (8.5.0's `_meta.ha_mcp` is gone);
  - description: 78;
  - annotations: 75;
  - `inputSchema`: 72;
  - `title`: 1.
- **Correction to section 1.** The five tools with unchanged annotations had
  all four hints already in 8.5.0.

### 10.3 Q2: #2613 discovery

**Does the announcement happen here?** No. The add-on announces itself by
POSTing to the Supervisor `/discovery` endpoint (`app_discovery.py:50-90`).
This capture had no Supervisor and ran the standalone image, so nothing was
announced. The Core side was therefore exercised directly, with a synthetic
URL and a probe integration that starts the same `hassio`-source flow for the
`mcp` handler that `components/hassio/discovery.py:125-130` starts.

**Where the URL goes in Core 2026.10.1** (source plus synthetic run):

| State | Where the URL is | Evidence |
|---|---|---|
| Discovery shown, not actioned | Only in the in-memory flow (`context`, flow data). The admin `config_entries/flow/progress` result and the REST flow GET did not contain it | Synthetic run |
| Same, non-admin user | Flow APIs return 401 or `unauthorized` | Synthetic run |
| **Configured** | Entry data `url` in `.storage/core.config_entries` | `mcp/config_flow.py:246` (source) |
| **Ignored**, before the next announcement | Nothing persisted. The ignored entry has no `data.url` | Synthetic run |
| **Ignored, then re-announced** (every add-on start) | `_abort_if_unique_id_configured(updates={CONF_URL: url})` writes the URL into the ignored entry's data in `.storage/core.config_entries`. A rotated URL overwrites it | `mcp/config_flow.py:212` (source); synthetic run |
| Persisted, then backed up | A local Core backup's `homeassistant.tar.gz` contains `data/.storage/core.config_entries` with the URL | Synthetic run |

**Who can read a persisted URL.**
- **HA admins** can, through `.storage`, a backup, or the entry's data.
- **Non-admin users** cannot. Non-admin `config_entries/get` and the REST entry
  list returned no entry data.
- **`ha_read_file` through the component cannot.** Every one of
  `.storage/core.config_entries`, `/config/.storage/core.config_entries` and
  `www/../.storage/core.config_entries` was denied with "Path not allowed".
  `.storage` is a non-overridable deny segment (`const.py:60,70-75` in both
  component versions). A control read of `www/` succeeded.
- **Logs:** `hassio/discovery.py` logs only the discovery uuid, at debug level
  (source), so `home-assistant.log`, which the file tools may read, does not
  receive the URL.
- **Flow events:** they carry only `handler`, `flow_id` and progress or
  refresh flags (`data_entry_flow.py:851-866`).

**Answer.**
- The secret-path URL is written to disk, and from there into Core backups,
  as soon as the discovered entry is **configured**, or once it is **ignored**
  and the add-on starts again.
- Leaving the discovery **unactioned** keeps it in memory only, in Core.
- Section 5 item 5 and section 8.7 are corrected accordingly.

**Not examined.** Supervisor keeps its own store of discovery messages, and
the Core hassio discovery proxy exists. Both need the Supervisor runtime or
its exact source, and neither was available. Whether a Supervisor or full
backup includes that store is unknown.

### 10.4 Q3: #2671 component JavaScript and npm

**Install and Core start-up.** No JavaScript runs and nothing is fetched from
npm.
- On a fresh container, HA pip-installs whichever manifest requirements Core
  does not already ship. Observed: `ruamel.yaml` and `voluptuous-openapi`.
  The manifest lists them as unpinned (`>=`). The list is identical in 2.2.0
  and 2.2.2 and pre-dates #2671.
- Egress at that point went to `pypi.org`, `files.pythonhosted.org` and
  `wheels.home-assistant.io`.

**When each step is triggered (v8.7.0 component source; the capture
exercised the `/local/` case).**
- **Card definitions and `quickjs-ng`.** After every successful component
  dashboard write (`dashboard_edit.py:211,221-227`), including writes with
  built-in cards only, the component computes advisory card warnings, which
  first loads the card definitions (`card_definitions.py:852-905`). The
  describe path loads them too (`:912-913`). The definitions are built once
  per Core process. The first build calls `async_ensure_runtime`
  (`card_runtime.py:74-100`), which pip-installs `quickjs-ng` only if it is
  not already installed. The package lives in the container's site-packages,
  so the install happens on the first write after a fresh container (first
  install, HA update or reinstall) and not again after a plain Core restart.
- **Custom-card loading and `linkedom`.** On the write path, custom cards are
  loaded only when the saved dashboard contains a `custom:` card
  (`card_definitions.py:893-903`); a built-in-only write never loads them. The
  describe path loads them when no card type is given or the type is
  `custom:` (`:914-919`). Loading resolves every registered `module` or `js`
  dashboard resource under **`/hacsfiles/`** (`www/community`) or **`/local/`**
  (`www`) to a `.js` path, without checking that the file exists
  (`custom_cards.py:54,188-197,631-650`). If that map is non-empty, the
  `linkedom` bundle is requested before any file is read (`:672-680`), so a
  stale registration of a missing file is enough. The bundle comes from the
  `.storage` cache if the cache file exists, unverified (`:579-600`).
  Otherwise it is downloaded from `registry.npmjs.org` and sha512-checked
  (`:200-204`). The cache is in the config directory, so it survives Core
  restarts and is downloaded again only when the cache file is missing.
- **Card JavaScript.** A card file's top-level code runs when the file is
  loaded into QuickJS: the first custom-card load in a Core process, and
  again whenever the file's mtime changes (`custom_cards.py:401-475`). Its
  `setConfig` check runs on every write, or describe, whose dashboard contains
  that card (`:338-360,519-523`). A missing file is skipped as "resource file
  is missing or unreadable", after `linkedom` has already been loaded.

**First dashboard write with 2.2.2 (as captured, `/local/` resource present).**
1. The component installs `quickjs-ng==0.17.0.1` through HA's requirements
   path (`card_runtime.py:15-100`).
   - Egress: `pypi.org`, `files.pythonhosted.org`, `wheels.home-assistant.io`.
   - The package is **version-pinned but not hash-pinned**.
   - HA's constraints apply. A different `quickjs` provider causes a refusal.
   - With `skip_pip`, card checks become unavailable and the write is not
     blocked.
2. It runs Home Assistant frontend card-editor schemas in QuickJS inside the
   HA process. The `schema` variant returned advisory warnings, for example
   ``vertical: Expected a value of type `boolean` ``, and the write still
   committed.
3. Because the written dashboard contained the `custom:` card and a `/local/`
   resource was registered, it downloaded the `linkedom` 0.18.13 tarball from
   **`registry.npmjs.org`** (the cache did not yet exist).
   - Observed at 16:13:26Z and, after a recreate, at 16:20:54Z.
   - The version is pinned and the tarball is checked against a pinned sha512
     at download.
   - The extracted bundle is cached at
     `.storage/ha_mcp_tools/linkedom-0.18.13.js` (413,058 bytes, sha256
     `12e8f1ad…`). A failed download is retried after 600 s.
4. It then executes the **third-party card JavaScript** from `www/` (top-level
   code plus `setConfig`) inside HA.
   - Proof: the `custombad` write returned the card's own exception text,
     `views[0].cards[1] (custom:hamcp187-card): hamcp187: label must not be
     'bad'`, as a warning. The write still committed and read back equal.
   - Sandbox observed by the card: no `std`, `os`, `require`,
     `XMLHttpRequest` or `WebSocket`; `fetch` is a stub that rejects
     (`custom_cards.py:60-180`).

**Later writes.**
- No egress.
- 0.27–0.87 s with the custom card; about 0.012 s with built-in cards only.

**After a Core restart (R1).**
- No egress, and the cache is reused.
- The first write takes 5.9 s (QuickJS start-up).

**After cache tampering (R2).**
- JavaScript appended to the cached `linkedom` file **ran after a restart**,
  with no re-verification and no re-download (`_async_dom`,
  `custom_cards.py:579-612`, reads the cache without a check).
- So the sha512 check protects only the download, not the file the component
  executes later.
- Anyone who can write `.storage` can already change HA, so this widens a
  boundary rather than creating a new one: code in a data file now executes
  as code.

**After a container recreate (R4, same image and config).**
- The `quickjs-ng` install repeats on the first write, because it lives in the
  container's site-packages. That means every HA update or reinstall.
- The `linkedom` download also repeated, but only because the R4 procedure
  deleted the cache file; the cache itself lives in the config directory and
  would otherwise have been reused. The new sha256 equals the original.
- First write: 6.7 s.

**Backups.** A local Core backup contains
`data/.storage/ha_mcp_tools/linkedom-0.18.13.js`. Restoring a backup restores
the cached bundle, which then runs unverified.

**Answer.**
- **Does 2.2.2 execute JavaScript inside HA?** Yes. It runs frontend schema
  code, linkedom, and dashboard-resource card code, on governed dashboard
  writes.
- **Does it download an npm package?** Yes, `linkedom` 0.18.13 from
  `registry.npmjs.org`. It is pinned and integrity-checked at download, but
  not when executed later.
- **When?** Nothing happens at install or Core start-up.
  - `quickjs-ng` pip install: the first component dashboard write (any cards)
    or describe after a fresh container, if the package is missing.
  - QuickJS definitions build (about 6 s): the first such call after every
    Core start; no egress.
  - `linkedom` download: the first custom-card load while the cache file is
    missing. A custom-card load needs a `custom:` card in the written
    dashboard (or a custom describe) and at least one registered
    `/local/` or `/hacsfiles/` `.js` resource, even a missing one. Otherwise
    the unverified cache is reused, across restarts.
  - Card JS: top-level code when a card file is first loaded in a Core
    process or has changed; `setConfig` on every write or describe whose
    dashboard contains that card.

### 10.5 Q4: component pairing

| Pairing | Catalog | Query search | Domain-only search | Governed dashboard write | Card JS in HA |
|---|---|---|---|---|---|
| 8.5.0 + 2.2.0 | 80, `f112aa40…` | component `ha_mcp_tools/search` | legacy | OK, readback equal; 0.011–0.016 s | **No** (no warning on `custombad`, no egress, no QuickJS log lines) |
| 8.5.0 + 2.2.2 | 80, `f112aa40…` | component `ha_mcp_tools/search` | legacy | OK, readback equal; 0.01–0.28 s | **Yes** (the `custombad` warning appeared) |
| 8.7.0 + 2.2.0 | 80, `95c69e8e…` | component `ha_mcp_tools/search` | legacy | OK, readback equal; 0.011–0.019 s | **No** |
| 8.7.0 + 2.2.2 | 80, `0906cbce…` | component `ha_mcp_tools/search` | component `search_unified` | OK, readback equal; up to 6.7 s for the first write after a start | **Yes** |

In every pairing, writes went through the component's `dashboard_edit`
WebSocket command, as observed in Core's WebSocket debug log.

**Answers.**
- **8.7.0 works with component 2.2.0.** Catalog, fast query search and
  governed dashboard writes all work.
- **A component without the card runtime avoids #2671.** The 2.2.0 tree
  contains no `quickjs`, `linkedom` or npm code at all (captured). Production
  runs 2.2.1, whose tree also contains none (source only; 2.2.1 was not
  captured).
- **#2671 exposure follows the component, not the server.** 8.5.0 with 2.2.2
  runs card JS too. Updating the component alone, without 8.7.0, would bring
  #2671 into today's production.
- **Fast search works in both pairings for queries.** Only the domain-only
  `search_unified` path needs 2.2.2.
- **Cost of not taking 2.2.2** (as captured for 2.2.0; the same list applies
  to 2.2.1 by source).
  - It forgoes the 2.2.2 fixes listed in section 6: #2697 search secret
    scrubbing, #2614 caller attribution, #2618 helper writes, and the
    `approval_pin.json` and listing denials for HAMCP-162.
  - Engineering must then bind the descriptor of the pairing actually
    deployed. For 8.7.0 + 2.2.0 that is `95c69e8e…` in this profile; 8.7.0 +
    2.2.1 was not captured.
- **Engineering's as-is guide call fails closed on 8.7.0.** 8.7.0 rejects
  `skill`, so no key is returned and the write is refused with
  `BPS_ACKNOWLEDGMENT_REQUIRED`. This confirms the `clients/mcp.py:359-371`
  change in section 8.2. 8.5.0 accepts `{skill, file}`.

### 10.6 Q5: report claims corrected by the capture

| Section | Earlier claim | Now |
|---|---|---|
| Evidence, "Not done" | No component-enabled capture | Done here |
| 1, 3, 9 C4 | Every tool's annotations changed | 75 of 80; all 80 descriptors still differ |
| 3a `ha_get_skill_guide` | `clients/mcp.py` must include 8.7.0 (source) | Confirmed; the unrewritten call fails closed |
| 3c setter | Latency up to about 20 s, *inferred* | Measured (Q3); first write after a start about 6 s |
| 3d | Filesystem tools need flag **and** component ≥ 1.2.0 at registration; 77 explained by that | **Wrong.** Flag-only registration; 77 is the beta master flag being off |
| 4 | 80 *inferred* | 80 captured for both versions |
| 5 item 5 | "Ignore it" | Ignore persists the URL at the next announcement |
| 6 | 8.5.0 + 2.2.2 *not verified*; update together for operational reasons | Verified; 8.7.0 + 2.2.0 is a working way to avoid #2671 |
| 7 #2671 | Engineering writes trigger it, *inferred* | Confirmed; also with 8.5.0; cache not re-verified |
| 8.7 | Ignore the discovery entry | See section 5 item 5 |
| 9 C3 | 80 vs 79 disputed | 80 captured; settled by the owner's report of 80 in production |

### 10.7 Probe artifacts (not findings)

- `ha_search` with `search_types: ["entity"]` errored identically in all four
  pairings. That is an invalid value chosen by the probe, not a defect.
- `home-assistant_v2.db` contained the synthetic URL. It came from the probe
  integration's own service call, which carried the URL in `service_data`.
  The real `hassio` discovery path fires no event that carries the URL
  (source).
- The first synthetic card file was empty, caused by a write slip. That run
  (8.7.0 + 2.2.2 `custom`, 16:13Z) still showed the `linkedom` download and a
  "not found in dashboard resources" warning. The card was rewritten and the
  runs repeated.

### 10.8 Limits

- No Supervisor, add-on runtime or `ha-mcp` add-on image; standalone images
  only.
- amd64 only.
- Settings mirrored the stated production settings, not production itself.
  No production file was read.
- Captures are evidence for this report, not admission fixtures. No strict or
  runtime contract fingerprints were computed.
- The raw logs and capture files were destroyed with the environment, as the
  brief required. Only the summaries above survive. The fingerprints in Q1 can
  be reproduced by repeating the method.

### 10.9 Cleanup

Cleanup ran on 2026-10-15 and was verified at 16:34:46Z.

**Removed:**
- Containers: `hamcp187-core`, `hamcp187-sniff` and `hamcp187-mcp870`, with
  `docker rm -f`. The 8.5.0 containers had already been removed after their
  phases.
- Networks: `hamcp187-int` and `hamcp187-egress`.
- The capture directory, with its throwaway config, `.storage`, admin token,
  backups, component trees, Core source extract, logs and tool lists.
  Root-owned files were removed through a `--network none` container, and the
  directory was then deleted.
- The capture helper scripts in the session scratchpad.

**Verified:**
- `docker ps -a --filter name=hamcp187`: 0 containers.
- `docker network ls --filter name=hamcp187`: 0 networks.
- `docker volume ls --filter dangling=true`: 0 volumes.
- The capture directory: "No such file or directory".

**Kept, deliberately.** The public, digest-pinned images listed in
section 10.1 remain in the local image cache. They hold no capture state. The
same host keeps other pinned Core and ha-mcp images for local lane work.

No live system, production credential or endpoint path was used, so none
needed revoking.

## Decisions recorded

**Owner decisions (Josh, 2026-10-15, recorded on HAMCP-187).** These
supersede the open decision requests in earlier revisions of this report.
They are owner direction; the production facts in them are owner-reported,
not reviewer observations.

1. **Stay on ha-mcp 8.5.0 with HA-MCP Custom Component 2.2.1.**
   - Component 2.2.1 shipped with ha-mcp 8.6.0 and contains no `quickjs`,
     `linkedom`, npm or custom-card runtime code; that code first appears in
     2.2.2 (source check; 2.2.1 was not captured, section 6).
   - Do not take component 2.2.2. Keep HACS auto-update off.
2. **8.7.0 admission work (PRs A–E, section 8) is parked.** HAMCP-184 still
   proceeds independently.
3. **Reconsideration triggers:**
   - HA Core 2026.11 compatibility (early November): if CI shows 8.5.0
     incompatible, reopen 8.7.0 (or later) admission;
   - a security fix relevant to Engineering's use of ha-mcp;
   - upstream opt-outs for #2613 and #2671.
4. **Upstream requests to file:**
   - #2613: an opt-out for the discovery announcement;
   - #2671: hash-pin `quickjs-ng`, re-verify the cached `linkedom` bundle on
     load, and an opt-out for card JavaScript execution.
5. **Operator rule if 8.7.0 is ever deployed:** never act on (Configure or
   Ignore) the ha-mcp discovery notification (section 10, Q2).
6. **79 vs 80 is settled.** Production `server_info` and health on 2026-10-15
   show 80 upstream tools; the 79 was a transient settings state. No
   production check is requested (section 9, C3).
7. **8.6.0 stays compiled, marked superseded and never to be deployed**
   (section 8.8).

**Earlier decisions (Josh, 2026-10-15), still standing or completed:**
- #2613 and #2671 were not accepted as-is, and their exposure was to be
  quantified first. Done in section 10.
- HAMCP-184 owns admission PR A (explicit provider dispositions).
- The disposable capture was authorized, and section 10 records it.

**Related owner item (from the HAMCP-187 record, outside this report's
scope):** direct-client #2695 exposure on 8.5.0 (section 9, C1) is to be
mitigated by the pending add-on port-mapping check and secret-path rotation
decision.
