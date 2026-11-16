# HAMCP-371 D6: a second exact reviewed `ha_search` variant for 9.2.0 (design report)

Ruling: architect ruling 2026-11-15 20:29 CST on P1 (`b82c51ab`), direction (a).
This is a read-only design report. No runtime code, policy, registry, scanner or
launcher changed, and no lane ran.

Evidence base:

- Engineering source at `b82c51ab`.
- ha-mcp 9.2.0 at `51e3aac788482a081807591cd5d58d07dc4e540b`, read with git
  plumbing and lazy fetch off.
- The S6 profile B receipts (`run-b-9.2.0-2/out/catalog/peer-9.2.0-1.json`,
  sha256 `c707f5f7…`, and `run-b-9.1.2-1/…/peer-9.1.2-1.json`, sha256 `2b824bbc…`).
- One offline in-process rerun of the accepted P1 harness to list names
  (loopback peer only, no files written, 0 upstream tool calls).

All line numbers refer to `hass_mcp_engineering_beta/ha_mcp_engineering/` unless
a path says otherwise.

## Summary

- **A precedent exists, bound to 8.6.0 only.** `providers/upstream_search_8_6.py`
  (commit `28378a52`, "Admit exact 8.6 component search descriptor pair") admits
  exactly two `ha_search` descriptors for 8.6.0: `reference` (legacy) and
  `component_unified`.
  - It hard-binds version 8.6.0, source `fc54437a`, the policy sha256 and the
    protocol (`_trusted_release`, lines 93-115).
  - Its own docstring says it is not a generic variant registry.
  - 9.2.0 needs its own exact pair. Everything that pair needs falls inside the
    HAMCP-371 lists.
- **Of the three legacy parameters, `config_time_budget` is the only one not
  honoured under unified search.** `search_types` and `include_config` are
  honoured on the component path. `config_time_budget` is ignored there and used
  only when ha-mcp falls back to its legacy path. Engineering cannot predict that
  fallback, so D6.5 applies: refuse it before dispatch under the unified variant.
  - This departs from the 8.6.0 precedent, which forwards it
    (`docs/HA_MCP_8_6_0_COMPATIBILITY.md`, "Exact search descriptor pair").
    Ruling R-D6-1 below asks the architect to confirm.
- **The advertised variant can change while the server runs, in both
  directions.** ha-mcp sends no `tools/list_changed` notification when it does.
  Engineering re-reads `tools/list` in the same session before every call and
  refuses a route whose descriptor changed, before dispatch. With a pair
  admitted, a later discovery admits the other exact variant.
- **On 9.2.0 under the D1 flags, Engineering exposes 27 tools today.** That is
  the 8.5.0 reference set of 27, minus `ha_search` (quarantined), plus
  `ha_config_get_yaml` (admitted as the optional reader). With the 9.2.0 pair it
  would expose 28.

## 1. Where the schema is compared, and whether more than one variant exists

### The comparison

- `providers/upstream_read_gateway.py:6243` `_compare_tool_contract`.
  - It fingerprints the observed `inputSchema` after
    `Draft202012Validator.check_schema` and compares it with
    `entry.input_schema_fingerprint` (lines ~6328-6343).
  - A difference gives `reason = "input_schema_mismatch"` (line 6385). The order
    of reasons is at lines 6380-6410.
- It is called by `_validate_catalog` (line 2933, call at 3136) for each entry of
  `selected_policy.tools`. That loop sets each read to matched or quarantined.
- Full admission (line ~1896) is
  `len(evaluation.matched) == classification_counts["automatic_read"]`.
- For 9.2.0, `upstream_tool_policy.py:95` (`USED_TOOL_ADMISSION_RELEASES`) means
  order is not gated, and unreviewed extra tools do not block admission.
- The same comparison runs again at call time:
  - `upstream_read_gateway.py:3453` calls `search_catalog_view` and then
    `_validate_catalog` on the live catalog.
  - `upstream_read_gateway.py:3618-3660` compares the target descriptor with the
    admitted route's pinned contract.

### One fingerprint per stock tool

- A policy entry carries a single `input_schema_fingerprint`
  (`upstream_tool_policy.py:425`, `:585`).
- The registry's `tool_contracts` must equal the policy, name for name and
  fingerprint for fingerprint (`upstream_tool_policy.py:~2015-2040`, error
  `release_registry_tool_contract_policy_mismatch`).
- The registry's catalog count and fingerprint are checked at
  `upstream_tool_policy.py:1925-1930`.

### Existing mechanisms for more than one variant

There are two.

1. **The optional reviewed reader** (`providers/upstream_yaml_reader.py`, lines
   21-25, `OptionalToolBinding` at 2078, per-version captures at ~2100-2146).
   - This is how the HAMCP-351 filesystem-on and filesystem-off profiles are
     handled.
   - It covers tools that are absent from stock policy. It never replaces a
     stock read, so it does not fit `ha_search`.
2. **The exact search descriptor pair** (`providers/upstream_search_8_6.py`).
   - `VARIANTS` (lines 38-78) holds, per variant: the catalog fingerprint, the
     strict-full fingerprint, the input fingerprint, the description fingerprint,
     the runtime-contract fingerprint and the runtime field fingerprints.
   - `resolve_search_variant` (118-127) matches the full canonical descriptor,
     but only after `_trusted_release`.
   - `search_catalog_view` (143-164) builds an ephemeral release and policy view
     with the matched variant's fingerprints. Stored authority and raw
     observations are untouched.
   - `search_capability_contract` (130-140) gives the readmission profile one
     explicit contract for the pair.
   - Its call sites:
     - `upstream_read_gateway.py:1393` (discovery) and `:3453` (call time).
     - `upstream_tool_policy.py:971-972` (`validate_reviewed_release_catalog`).
     - `upstream_tool_policy.py:1377-1392` (the `policy_uncompiled_search_adapter`
       guard).
     - `ha_mcp_readmission/ha_mcp.py:48`, `:713-714` and `:770`.
   - `signed_registry/` has no search reference, so a binary-owned pair does not
     change the signed-registry models.
   - Every function returns the release unchanged unless it is the exact 8.6.0
     release. A 9.2.0 view chained after it is therefore independent of it.

### Can a 9.2.0 pair stay within the HAMCP-371 lists?

Yes, if the pair lives in `providers/upstream_reads_9_2.py`, which is on the
list. The files it would touch:

- `providers/upstream_reads_9_2.py`: the pair, its view, its resolver and the
  argument guard (section 5).
- `providers/upstream_read_gateway.py`: the two view call sites, and the refusal
  at dispatch.
- `upstream_tool_policy.py`: the catalog-validation view, and an uncompiled-adapter
  guard for 9.2.
- `ha_mcp_readmission/ha_mcp.py`: the pair's capability contract in the
  observation and in the profile.
- `upstream_tool_policy_9_2_0.json`: an adapter token on `ha_search`.
- `upstream_release_registry.json`: the new policy sha256 on the 9.2.0 row.
- `providers/contracts/upstream_dashboard_builtin_attestations.json`: only if its
  9.2.0 row binds the policy hash. The 8.6 commit had to change this file.

Files outside the lists:

- No runtime file.
- A separate `providers/upstream_search_9_2.py` would mirror the 8.6 layout more
  cleanly. It is a new file that is not on the list, so it needs authorization
  (ruling R-D6-2).
- `upstream_search_8_6.py` is not on the list and does not need to change.
- New tests, a capture script and evidence files are needed. They are not
  runtime paths.

## 2. How 9.2.0 handles the legacy parameters under unified search

The schema transform (`src/ha_mcp/transforms/component_search.py`):

- `_LEGACY_PARAMETERS = {search_types, include_config, config_time_budget}` is at
  line 22. The required capabilities are at line 21: `search`,
  `device_registry_child_semantics` and `search_unified`.
- `_rewrite` (lines 71-86) removes those parameters from the advertised schema
  only, and replaces the descriptions.
- The class docstring says the callable still validates against the function, so
  all three are still accepted.
- `list_tools` and `get_tool` (lines 88-105) apply the rewrite on every call. The
  transform is registered at `src/ha_mcp/server.py:221-223`.

The routing (`src/ha_mcp/tools/tools_search.py`):

- `ha_search` is at line 68, and `config_time_budget` is a parameter at line 252.
- It resolves the request into `_ResolvedSearch` (lines ~383-399).
- If `_component_serves_search_types` is false, it takes the legacy path.
- `mode_supported` is at line 422.
- Then it takes the component path (`_ha_search_via_component`) or the legacy
  path (`_legacy_ha_search`, component.py:794).

| Parameter | Component path | Verdict |
|---|---|---|
| `search_types` | Forwarded. `_build_component_search_request` sends `"search_types"` (`src/ha_mcp/tools/search/component.py:200-211`). `dashboard` is served by a separate leg and merged (component.py:154-185, 241, 734). Any other surface the component lacks sends the whole call to the legacy path, which also honours it. | **Honoured** |
| `include_config` | Sent as `"include_config": req.include_config` (component.py:214). Config records are normalized at component.py:400 and 519. | **Honoured** |
| `config_time_budget` | Absent from the component request and from component.py entirely. It is used only by `search/legacy.py:99` (passed to `_ha_deep_search`) and by `smart_search/_deep.py` and `_scenes.py`. | **Ignored when the component serves the call.** Honoured only on a legacy fallback, which can happen silently (component.py:755, the unserviceable dashboard split, the visibility gating at 673-720, or an unsupported search type). |

Conclusion for D6.5: refuse `config_time_budget` before dispatch under the
unified variant. Forward `search_types` and `include_config` unchanged.

## 3. Whether the variant can change while the server runs

On the upstream side (`src/ha_mcp/tools/component_api.py`), it can, in both
directions:

- **A component that appears late is adopted.** Negative capability entries
  expire after 300 s for a definitive absence (`_NEGATIVE_CACHE_TTL_S`, line
  91), or after 30 s for an inconclusive failure
  (`_TRANSIENT_NEGATIVE_CACHE_TTL_S`, line 98). After that, the next `tools/list`
  can advertise the unified schema.
- **A Core restart re-probes.** A positive entry holds only while the WebSocket
  it was probed over is the live one in the pool (`_PROBED_OVER`; lines 134-146
  and 240-258). The restart may have loaded another component version.
- **A downgraded component is dropped.** An `unknown_command` from the component
  calls `invalidate_caps` (line 548). It has about ten callers, for example
  `component_config_reads.py:109`.
- **A failed probe goes back to legacy.** `_unified_search_available` returns
  False on any exception (`component_search.py:60-68`). A transient WebSocket
  failure during `tools/list` therefore advertises the legacy schema.
- The transform sends no `tools/list_changed`. The only senders are in vendored
  fastmcp visibility code, which this transform does not use.

On Engineering's side:

- Before every call, the client opens a fresh `initialize` and `tools/list` in
  the same session and runs the gateway's catalog validator before `tools/call`
  (`clients/upstream_read.py:533-554`; validator at
  `upstream_read_gateway.py:3374`, passed at 3776).
- The validator recomputes the view and the evaluation (3453-3487) and compares
  the target with the route's pinned contract (3618-3660).
- A change raises `schema_mismatch` with disposition `quarantine` before dispatch,
  and advances the live-observation epoch (5547).
- The background reconciler runs a reprobe on that event, or every 900 s
  (`COMPATIBILITY_REPROBE_INTERVAL_SECONDS`, line 150; `_wait_for_reprobe` at
  2788). At most one immediate retry is armed (`_stale_reprobe_retry_armed`,
  1943-1975 and 2557-2569), and after that it falls back to the slow cadence.

What a flip does:

- **Today** (one 9.2.0 variant, the legacy one):
  - Legacy to unified: the next call is refused before dispatch, and the next
    discovery quarantines `ha_search` (`input_schema_mismatch`), as in P1.
  - Unified to legacy: the next discovery admits it again.
  - Sibling reads are not affected.
- **With the pair:**
  - Either flip refuses only the stale `ha_search` call, before dispatch.
  - The next discovery admits the other exact variant. The 8.6 document
    describes the same behaviour: "Switching variants refuses a stale search
    before dispatch."
  - Under unified, the `config_time_budget` refusal follows the variant pinned
    on the admitted route, so it never reaches upstream.

## 4. What 9.2.0 exposes under the D1 flags

These lists come from the offline P1 harness: the D1 catalog `c707f5f7…`,
against the reviewed 9.2.0 catalog and the reviewed 8.5.0 fixture catalog.

**Exposed on 9.2.0 under D1 today (27):**

- `ha_config_get_automation`, `ha_config_get_calendar_events`,
  `ha_config_get_category`, `ha_config_get_label`, `ha_config_get_scene`,
  `ha_config_get_script`
- `ha_config_get_yaml`
- `ha_config_list_dashboard_resources`, `ha_config_list_groups`,
  `ha_config_list_helpers`
- `ha_eval_template`
- `ha_get_app`, `ha_get_automation_traces`, `ha_get_blueprint`, `ha_get_device`,
  `ha_get_entity`, `ha_get_entity_exposure`, `ha_get_history`,
  `ha_get_integration`, `ha_get_overview`, `ha_get_skill_guide`, `ha_get_state`,
  `ha_get_system_health`, `ha_get_todo`, `ha_get_zone`
- `ha_list_floors_areas`, `ha_list_services`

Compared with the 8.5.0 set:

- The reviewed 8.5.0 catalog and the reviewed (component-off) 9.2.0 catalog each
  expose the same 27, both `admitted_exact`. That set includes `ha_search` and
  excludes `ha_config_get_yaml`.
- The 8.5.0 figure is offline. The live 8.5.0 surface was not observed (no Home
  Assistant access).
- 9.2.0 under D1 is missing `ha_search` and adds `ha_config_get_yaml`.
- With the 9.2.0 pair it would expose 28 and, by inference, be `admitted_exact`:
  - All 23 of its automatic reads would match.
  - The remaining differences do not gate used-tool admission: the extra tools
    and the description of the persistent write `ha_config_set_helper`.

**Held or quarantined:**

| Name | 9.2.0 under D1 | How |
|---|---|---|
| `ha_search` | quarantined | `input_schema_mismatch`. Expected runtime contract `36f5a903…`, observed `215f7b9a…`. |
| `ha_get_operation_status` | held | `held_for_canary` (also held on 8.5.0) |
| `ha_config_get_yaml` | **exposed**, optional state `admitted` | The HAMCP-162 optional reader: an exact runtime binding to the 9.2.0 filesystem-on capture `s8/profiles-2/out/standalone-9.2.0-fs-on/capture.json` (`upstream_yaml_reader.py:~2100-2121`), plus a Core requirements check. It never joins stock policy or accounting, and is revalidated on each call at `upstream_read_gateway.py:3579-3613`. |
| `ha_list_files`, `ha_read_file` | withheld, `prohibited_withheld` | Prohibited siblings with pinned runtime fingerprints (`upstream_yaml_reader.py:76-80, 2122-2146`). They never route. |
| `ha_delete_file`, `ha_write_file` | `optional_absent` | Not advertised under D1. |

Blocked by classification (the same on all three runs, except `unsupported`):

- `held_for_canary` 1
- `mixed_or_requires_wrapper` 10
- `persistent_write` 32
- `physical_or_high_risk_action` 4
- `prohibited` 1
- `unsupported`: 2 on 9.2.0 (`ha_get_camera_image`, `ha_get_hacs_info`), 1 on
  8.5.0, where `ha_get_hacs_info` is an automatic read in the 8.5.0 policy.

## 5. Proposed smallest change

The change mirrors `28378a52` for 9.2.0, inside the HAMCP-371 lists.

### a. The pair

In `providers/upstream_reads_9_2.py`:

- Add `SEARCH_ADAPTER = "ha-mcp-9.2.0-search-descriptor-pair-v1"`.
- Add `SEARCH_VARIANTS`, with `reference` and `component_unified`. Each carries
  the same fields as `upstream_search_8_6.SearchVariant`.
- Add `_trusted_search_release`. It must require all of:
  - 9.2.0, source `51e3aac7`, not revoked, and the policy resource and sha256;
  - the reference entry's fingerprint and the reference contract fingerprint;
  - the drift-checked `PUBLIC_SCHEMAS["ha_search"]`.
- Add `resolve_search_variant_9_2`, `search_catalog_view_9_2` and
  `search_capability_contract_9_2`, with the same rules as the 8.6 versions.
  Each returns its input unchanged for any other release.

### b. Wiring

Chain the 9.2 view after the 8.6 view at the same call sites:

- `upstream_read_gateway.py:1393` and `:3453`;
- `upstream_tool_policy.py:972`;
- `ha_mcp_readmission/ha_mcp.py:713-714` and `:770`.

Also:

- Extend `adapter_release` and `is_adapter` in `upstream_reads_9_2.py` so the
  compiled entry is recognized with either input fingerprint.
- Add a 9.2 `policy_uncompiled_search_adapter` guard next to the guard at
  `upstream_tool_policy.py:1388`.

### c. Refusing the legacy argument

- In `read_arguments` (`upstream_reads_9_2.py:135`), or at the call-time point
  where the gateway knows the route's pinned variant, refuse when:
  - the pinned variant is `component_unified`; and
  - the call carries `config_time_budget`.
- The refusal code is distinct, `search_argument_not_honoured_by_variant`. The
  name is open to ruling.
- The refusal happens before any `tools/call`, and the result names the
  argument and the variant.
- `search_types` and `include_config` are forwarded unchanged.
- The public schema does not change, so there is no public-schema change and no
  tool-count change.

### d. Policy and registry

- `upstream_tool_policy_9_2_0.json`: append `SEARCH_ADAPTER` to the
  `argument_restrictions` of `ha_search`.
- `upstream_release_registry.json`: the 9.2.0 row's `policy_sha256` changes, and
  so its readmission profile changes. Old 9.2.0 authority cannot silently acquire
  the alternate contract.
- The attestations file changes only if its 9.2.0 row binds the policy hash.

### e. Receipt-bound capture, not by hand

- A committed script, `docs/evidence/hamcp371/sr/d6_search_capture.py`, would:
  - check the sha256 of `c707f5f7…` and its `serverInfo` (ha-mcp 9.2.0);
  - check its single page and wire order (the P1 checks);
  - check that `out/profile-b/component-after-setup.json` reports all three
    required capabilities;
  - extract the `ha_search` descriptor, and compute every `component_unified`
    field with Engineering's own `schema_fingerprint`.
- The catalog fingerprint is computed over the reviewed 9.2.0 catalog with only
  `ha_search` replaced. The `reference` fields come from the existing reviewed
  9.2.0 contract.
- It writes `ha-mcp-9.2.0-component-search.json`, which records the sources and
  their digests.
- A test recomputes that file and the `SEARCH_VARIANTS` literals from the
  receipt, and fails on any difference.
- Values from the script at `b82c51ab` (read-only):

| Field | Reference (reviewed) | Component-unified (receipt `c707f5f7…`) |
|---|---|---|
| Wire input fingerprint | `2258e36c…` | `c1ca76bd2f762cceb1697bec9e0cee25e0d3806cc2815e62dd06f5132a19a83b` |
| Full descriptor | `36f5a903…` | `215f7b9adf2ae9fb35a3c76d195c36e3aa8987f2db12ffe2c92700a410378abb` |
| Description | (reviewed) | `4fbce174623e9ea49bda4446101e54e118fad779c5ec51c661c0a2b662b58a41` |
| Advertised properties | 15 | 12 (no `search_types`, `include_config` or `config_time_budget`) |

- The 9.1.2 unified input fingerprint is `cd5b40c5…`. It equals the 8.6.0
  `component_unified` input fingerprint. The 9.2.0 one differs, so the 8.6 pair
  cannot be reused. 9.1.2 is out of scope here.

### f. Tests

These would extend `tests/test_hamcp371_admission_core.py` or go in a new
`tests/test_hamcp371_search_pair_9_2.py`:

1. **Admission:**
   - The D1 receipt catalog is `admitted_exact` and exposes 28.
   - The reviewed catalog is still `admitted_exact` with 27, and its `ha_search`
     stays on `reference`.
2. **Dispatch:**
   - Under `component_unified`, `ha_search` with `search_types` and
     `include_config` dispatches unchanged, and the peer receives the same
     arguments.
   - With `config_time_budget`, it is refused with the distinct code and 0 peer
     calls.
   - Under `reference`, `config_time_budget` is forwarded.
3. **Negative cases, each giving quarantine or refusal with 0 peer calls:**
   - one byte changed in the unified descriptor;
   - unified descriptor fields mixed with reference fields;
   - a pair on 9.2.1, or on 9.1.2 with the 9.2 fingerprints;
   - a revoked release, a changed policy sha, or a drifted public schema;
   - the adapter token on a non-9.2 policy (`policy_uncompiled_search_adapter`);
   - signed denial and retained revocation still in control.
4. **Variant change:** admit `reference`, serve `component_unified` at call time.
   - The stale call is refused before dispatch, the epoch advances, and sibling
     reads still succeed.
   - The reprobe admits `component_unified`. The reverse flip behaves the same.
5. **8.6.0 is unchanged:** the existing `test_upstream_8_6_compatibility.py` still
   passes, and the 8.6 view returns 9.2.0 releases unchanged.
6. **Capture:** the test from point e.
7. **Mutation checks:**
   - remove the refusal;
   - accept any variant by input fingerprint alone;
   - drop the version gate;
   - skip the call-time view.

### Gates

- These changes touch protected runtime paths already on the HAMCP-371 list.
- They need the Fast tier, then Full or Evidence before independent review.
- SR-b would then run with `r25-search` admitted.
  `scripts/core_read_replay_calls.py` uses `search_types` (lines 135, 138 and
  156). It has no `include_config` or `config_time_budget` case, so the refusal
  would not change any existing SR read.

## Rulings needed

- **R-D6-1.** Refuse `config_time_budget` under `component_unified`, as D6.5
  directs and as recommended here? This departs from the 8.6.0 precedent, which
  forwards it. Keep 8.6.0 as it is?
- **R-D6-2.** Put the pair in `upstream_reads_9_2.py`, which is on the list
  (recommended)? Or authorize a new `providers/upstream_search_9_2.py`?
- **R-D6-3.** The refusal code name (proposed:
  `search_argument_not_honoured_by_variant`). Should the result also carry the
  pinned variant name?

## Not done

- No code, policy, registry, scanner or launcher change.
- No lane, SR-b, Fast, Full or Evidence run.
- No push, and no access to Home Assistant or Engineering MCP.
- The 9.1.2 source was not checked. The live 8.5.0 surface was not observed.
- The 8.5.0 control has no offline check, as ruled.
