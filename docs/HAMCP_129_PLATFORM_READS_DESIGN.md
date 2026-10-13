# HAMCP-129 delegated platform health, installed apps and integration status — design

Status: **approved by Josh on 2026-10-12 (O1-O9 as recorded in §10) and
implemented in HAMCP-129 Part 2 as Engineering `2.4.1-beta.6`.** The text below
is the approved Part 1 design; implementation deviations are listed in §12.
Nothing here authorizes deployment, live Home Assistant or Engineering
access, registry signing, HOLD or support-window changes, or a release.

- Register: HAMCP-129.
- Base: `main` at `c3097c9d68c1608b35b22c136d64b84ffe2c8310` (Engineering
  `2.4.1-beta.4` plus the HAMCP-161 Core registry sequence 9).
- Support window (`ci/support-window.yaml`): ha-mcp current 8.5.0, rollback
  8.4.3, candidate 8.6.0 (production HOLD unchanged). Core current 2026.10.0,
  candidate 2026.10.1, rollback 2026.9.4.
- Upstream source was reviewed at the exact pinned commits, each checked
  against its tag: 8.4.3 `eac7a3aa7063432e9af17e7d7726040e909c7b8f`, 8.5.0
  `311d6dc273fb4e9a5b8cde0de15f69472a64fe44`, 8.6.0
  `fc54437a804858732e4bc927add98e202d879a09`. Line references are given as
  `TS` (`src/ha_mcp/tools/tools_system.py`), `TA` (`tools_addons.py`) and
  `TI` (`tools_integrations.py`) with the version.
- Core source reviewed: 2026.9.4 (local exact-core lane inputs), 2026.10.0
  (source archive `core-6a811d33…` from the 2026.10.0 compatibility
  artifacts), and the HAMCP-161 2026.10.0..2026.10.1 changed-file list and
  diffstat. The 2026.10.1 tree itself was not available locally (§5.3).

## 0. Summary

Three new delegated reads, each an Engineering-owned wrapper over one
`mixed_or_requires_wrapper` ha-mcp tool, following the
`providers/upstream_blueprint.py` pattern:

| Public tool | Upstream tool | Upstream call Engineering builds | Reaches upstream only with |
|---|---|---|---|
| `ha_get_system_health` | `ha_get_system_health` | baseline plus an enum-built `include` | `{}` or `{"include": "<sorted allowed sections>"}` (+ `include_dismissed_repairs`) |
| `ha_get_app` | `ha_get_app` | installed inventory | `{"source": "installed", "include_stats": <bool>}` |
| `ha_get_integration` | `ha_get_integration` | config-entry list mode | `{"limit": 200, "offset": 0, "exact_match": true, "include_options": false}` (+ `query`) |

The caller never supplies an upstream argument directly. Every caller argument
is validated by a closed, binary-owned schema and translated by Engineering.
Every result is projected through a positive field allowlist **before** the
existing sanitizer, then bounded. No write, flow, diagnostics download, config
check, upstream fallback or direct-HA fallback becomes reachable.

The verified source behaviour forces three departures from the brief's
expected shape (details in §9):

1. `ha_get_app` with a `slug` returns the raw Supervisor `/addons/{slug}/info`
   document, including the add-on's configured options. The wrapper therefore
   never sends `slug` upstream; filtering by slug happens in Engineering over
   the installed listing.
2. `ha_get_integration` with `domain` forces options reads, and on the legacy
   path that starts and aborts a real options flow per entry. With `entry_id`,
   detail mode can also start options flows. The wrapper therefore uses list
   mode only, never sends `domain` or `entry_id`, and filters in Engineering.
3. `ha_get_system_health` always runs Core's `system_health/info`, which starts
   outbound HTTPS reachability probes (cloud and Supervisor version checks).
   The open-world behaviour cannot be excluded through `include`; it is
   disclosed and the annotation stays `openWorldHint: true`.

Totals: 84 → **87** connector tools; delegated reads 25 → **28**.

## 1. Tool surface: arguments, rejections and value constraints

All three public schemas are closed (`additionalProperties: false`). Any
property not listed is rejected before dispatch with the existing
`invalid_arguments` refusal; there is no partial acceptance and no
pass-through. The schemas are identical for 8.4.3, 8.5.0 and 8.6.0, because
the upstream parameters Engineering uses are identical in all three (§1.4).

### 1.1 `ha_get_system_health`

Public input:

| Property | Type and constraint | Default |
|---|---|---|
| `sections` | array of unique strings, `enum` = `repairs`, `zha_network`, `zwave_network`, `thread_network`, `matter_network`, `dead_entities`, `themes`; `maxItems` 7 | `[]` (baseline only) |
| `include_dismissed_repairs` | boolean; accepted only when `sections` contains `repairs` (otherwise `invalid_arguments`) | `false` |

Engineering-built upstream arguments:

- `sections == []` → `{}`.
- otherwise → `{"include": ",".join(sorted(sections))}`, plus
  `"include_dismissed_repairs": true` only when requested.

Rejected (never sent upstream, never accepted publicly):

| Upstream parameter or section | Reason (verified) |
|---|---|
| `include=diagnostics`, `config_entry_id`, `device_id`, `diagnostics_fields`, `diagnostics_truncate_at_bytes`, `diagnostics_data_path`, `diagnostics_data_offset`, `diagnostics_data_limit` | Runs integration diagnostics code; output is unbounded and unredacted; ids are interpolated into the request path. |
| `include=config_check` | `POST /api/config/core/check_config`. A POST to a Core action endpoint; whether it can install requirements on a given Core is UNKNOWN. Not a read. |
| `include=zha_network_full` | Raw, unprojected ZHA device dump (upstream cap 25). |
| free-form `include` strings | Upstream comma-splits and lowercases `include`, and silently ignores unknown names with only a warning (TS 8.5.0 ~864-880). Engineering builds the string from the enum, so a caller cannot smuggle `zha_network_full` or `diagnostics` through it. |

Open-world sections: the **baseline** is the open-world part, not a selectable
section. `system_health/info` makes Core create every registered integration's
health-info coroutines as tasks at subscription time (Core 2026.10.0
`components/system_health/__init__.py:179-200`). That includes
`cloud` (`can_reach_cert_server`, `can_reach_cloud_auth`, `can_reach_cloud`;
`cloud/system_health.py:53-62`) and `hassio` (`version_api` reaches
`https://version.home-assistant.io/<channel>.json`;
`hassio/system_health.py:107-115`). The upstream tool reads only the
`initial` event, so these values arrive as `{"type": "pending"}`, but the
probes have already started. `thread_network` calls `otbr/info`, which in Core
reaches the OTBR REST API on the local network. Separately, upstream always
computes `ha_mcp_update`: in add-on mode it reads Supervisor
`/addons/self/info`; outside add-on mode it fetches PyPI over HTTPS unless
`HA_MCP_DISABLE_UPDATE_CHECK` is set (`update_check.py:52-53`). The deployed
ha-mcp mode is not provable from this repository (§9, F9).

Decision: the excluded sections above are excluded for being non-read or
unbounded. The baseline is kept, with its open-world behaviour disclosed in the
tool description and the annotation kept at `openWorldHint: true`. The
alternative, refusing the tool because the baseline cannot be made closed-world,
is owner decision **O2**.

### 1.2 `ha_get_app`

Public input:

| Property | Type and constraint | Default |
|---|---|---|
| `slug` | string or null; `^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}$`, not `.` or `..` | `null` |
| `include_stats` | boolean | `false` |

Engineering-built upstream arguments, always:
`{"source": "installed", "include_stats": <bool>}`. **`slug` is never sent
upstream.** When `slug` is given, Engineering selects the matching item from
the projected installed list and returns `resource_not_found` if none matches.

Rejected:

| Upstream parameter | Reason (verified) |
|---|---|
| `slug` (upstream) | `get_addon` checks `if slug:` before `source` (TA 8.5.0:2469-2479), so `source="installed"` does not constrain it. A slug returns raw `/addons/{slug}/info` with no field filtering: `options` (add-on configuration, often credentials), `schema`, `network`, `ip_address`, `hostname`, `ingress_*`, `webui`, `devices`, and API-permission flags. Upstream `redact_secrets` defaults to off (`config.py`) and only covers password-format options. |
| `source="available"`, `query`, `repository` | Store discovery; `query` and `repository` are used only for `/store`, so they do nothing for installed. Out of scope. |

`include_stats`: upstream calls `/addons/{slug}/stats` for **every started
add-on** through an unbounded `asyncio.gather` (TA 8.5.0:1130-1151). These are
Supervisor-local GETs, but Engineering cannot bound the concurrency of the
upstream call. Proposal: when `include_stats` is true, Engineering first makes
the stats-free listing call; if more than 32 add-ons are started it refuses with
`response_too_large` instead of making the stats call. That is two upstream
dispatches for one public call. The alternative, rejecting `include_stats` in
the first release, is owner decision **O3**.

The pre-check is a sampled bound, not a hard cap on the upstream fan-out.
Engineering never dispatches the stats call when its own stats-free snapshot
shows more than 32 started add-ons. Upstream `list_addons` then re-lists before
it starts the stats requests, so an add-on started between the two calls is
still fanned out upstream (33 or more requests) before Engineering refuses the
over-cap response with `response_too_large`. No tool in the reviewed ha-mcp
releases accepts a per-slug stats request or a slug set, so a hard bound at the
fan-out boundary would need a new, separately reviewed provider contract (see
§12, D11).

### 1.3 `ha_get_integration`

Public input:

| Property | Type and constraint | Default |
|---|---|---|
| `domain` | string or null; `^[a-z0-9_]{1,64}$` | `null` |
| `entry_id` | string or null; `^[A-Za-z0-9]{1,64}$` | `null` |
| `query` | string or null; 1–64 printable characters, no control characters | `null` |
| `state` | string or null; `enum` = `loaded`, `setup_error`, `migration_error`, `setup_retry`, `not_loaded`, `failed_unload`, `setup_in_progress`, `unload_in_progress` | `null` |
| `limit` | integer 1–100 | `50` |
| `offset` | integer 0–200 | `0` |

`entry_id` is exclusive with `domain`, `query`, `state`, `limit` and `offset`
(`invalid_arguments` otherwise).

Engineering-built upstream arguments, always list mode, one dispatch:

```
{"limit": 200, "offset": 0, "exact_match": true, "include_options": false}
+ {"query": <domain>}   when domain is set (superset prefilter)
+ {"query": <query>}    when only query is set
```

Engineering then applies, in order: exact `domain` equality, case-insensitive
substring `query` on `domain`/`title` (when both are set), `state` equality,
`entry_id` equality, then `offset`/`limit` pagination over the filtered set.
`state_summary` is recomputed over the filtered set. When upstream reports
`has_more: true` (more than 200 matching entries) the result is
`completeness: "partial"` with reason `upstream_window_exceeded`, and an
`entry_id` that is not found inside the window is reported as
`resource_not_found` with the same partial reason, not as definitive absence.

Rejected:

| Upstream parameter | Reason (verified) |
|---|---|
| `domain` (upstream) | `if domain is not None: include_opts = True` (TI 8.5.0:830-832; 8.4.3:812-813). On the legacy path this starts and aborts an options flow for every `supports_options` entry through an unbounded gather (TI 8.5.0 ~1568-1585). |
| `entry_id` (upstream detail mode) | The legacy path always probes the options flow when `supports_options` (TI 8.6.0:1077-1078); the component path probes when `redaction_enabled()` (TI 8.6.0:1154). The `redact_secrets` setting is UNKNOWN, so detail mode is not provably flow-free. |
| `include_options` | Options are configuration and can carry secrets; also forces the probes above. Forced `false`. |
| `include_schema` | Detail-only; returns the options-flow schema by starting an options flow when `supports_options` (TI 8.5.0:1064, 1200; 8.4.3:1045, 1181; 8.6.0:1031, 1163). |
| `include_subentries` | Ignored in list mode (TI 8.5.0:986-987); detail-only. |
| `subentry_type`, `subentry_id` | Used only to start the subentry-schema flow. |
| `show_advanced_options` | Only affects flow schemas; does nothing on HA 2026.6+. |
| `exact_match=false` | Fuzzy matching (ratio ≥ 70) makes results depend on an upstream heuristic. Forced `true`. |
| `device_id`, `diagnostics_fields`, `diagnostics_truncate_at_bytes`, `diagnostics_data_path`, `diagnostics_data_offset`, `diagnostics_data_limit` | Diagnostics path; not in the brief's list, found in source. |
| upstream `limit`, `offset` | Engineering owns pagination (`limit ≤ 200`, `offset ≥ 0` with no maximum upstream). |

List mode with `domain` unset and `include_options` false starts no flow on
either the legacy or the component path (component guard
`if include_opts and redaction_enabled()`, TI 8.6.0:1608). It reads config
entries (REST `GET /api/config/config_entries/entry` on the legacy path, or the
custom-component WebSocket command `ha_mcp_tools/config_entries`) and
`logger/log_info`.

### 1.4 Version verification

| Item | 8.4.3 | 8.5.0 | 8.6.0 |
|---|---|---|---|
| `ha_get_system_health` input-schema fingerprint (policy) | `0c51da5b…` | `0c51da5b…` (same) | `569e6a4d…` (Annotated field descriptions only) |
| System-health sections and parameters | 10 sections, 9 parameters | same | same; changed diagnostics 401/403 messages only |
| `ha_get_app` input-schema fingerprint | `71bbe1fb…` | `c76674086…` | `7c1fba17…` |
| `ha_get_app` installed list fields | unchanged | `version_latest`/`version_installed` added to the **store** list only | text-only changes |
| `ha_get_integration` input-schema fingerprint | `5dd76ac0…` | `5dd76ac0…` (same) | `798a5110…` |
| `domain` forces options | yes (TI:812-813) | yes (TI:830-832) | yes |
| Component list path (`_fetch_entries_via_component`) present | yes | yes | yes |
| Option-section flattening on the component path | present | present | removed (irrelevant: options are never requested) |
| Custom-component `system_snapshot` used by repairs/zwave/matter/dead_entities | yes | yes | yes |

The wrapper's argument construction is the same for all three versions. Each
version still gets its own adapter binding (§4) because the input-schema
fingerprints differ.

## 2. Secret and sensitive fields; output allowlists

The existing sanitizer (`sanitization.py:14-75,152-173`) is an exact
normalized-key **denylist** (`password`, `passwd`, `passphrase`, tokens,
cookies, webhooks, auth-flow and Matter codes). It does not match compound or
network keys such as `mqtt_password`, `apikey`, `username`, `host`, `ssid`,
`nameservers` or `ws_server_url`. HAMCP-129 therefore adds a **positive
allowlist projection** per tool. The projection runs on the normalized upstream
payload before the existing sanitizer, which still runs afterwards. A key not
listed below is dropped, not passed through. A listed key with the wrong JSON
type fails the call with `invalid_response`.

### 2.1 `ha_get_system_health`

Top level:
`success`, `baseline_available`, `platform`, `sections`, `warnings`,
`completeness`. Dropped: `health_info` (replaced by `platform`),
`component_count` (upstream computes `len(event)`, which counts the `type`/`data`
envelope keys and is always 2), `message`, `ha_mcp_update`.

`platform` (from `health_info.data`):

| Domain | Allowed keys | Dropped (examples) |
|---|---|---|
| `homeassistant` | `version`, `installation_type`, `dev`, `hassio`, `docker`, `virtualenv`, `python_version`, `os_name`, `os_version`, `arch` | `timezone`, `user`, `config_dir`, any other |
| `hassio` | `host_os`, `update_channel`, `supervisor_version`, `agent_version`, `docker_version`, `disk_total`, `disk_used`, `disk_life_time`, `healthy`, `supported`, `virtualization`, `board`, `ntp_synchronized`, `host_connectivity`, `supervisor_connectivity`, `supervisor_api`, `version_api` | `nameservers`, `installed_addons` (use `ha_get_app`), any other |
| `cloud` | `logged_in`, `relayer_connected`, `remote_enabled`, `remote_connected`, `alexa_enabled`, `google_enabled`, `certificate_status`, `iot_state`, `can_reach_cert_server`, `can_reach_cloud_auth`, `can_reach_cloud` | `instance_id`, `remote_server`, `relayer_region`, `certificate_fingerprint`, `certificate_alternative_names`, `certificate_expire_date`, `subscription_expiration`, `iot_last_disconnect_reason`, any other |
| any other domain | none; the domain name is listed in `platform.other_domains` (sorted, max 64) | all values |

Allowed values are scalars. Any value in Core's typed shape
(`{"type": "pending"}`, `{"type": "failed", "error": …}`, used for the
reachability checks and also for `healthy` and `supported` when Supervisor
reports unhealthy or unsupported; Core 2026.10.0
`hassio/system_health.py:56-70`) is reduced to one of `ok`, `pending` or
`failed`. The error text is dropped.

Sections:

| Section | Allowed fields |
|---|---|
| `repairs` | `count`, `dismissed_count`, `issues[]` → `domain`, `issue_domain`, `issue_id`, `severity`, `is_fixable`, `translation_key`, `breaks_in_ha_version`, `created`, `ignored`, `dismissed_version`. Dropped: `translation_placeholders` (can carry hosts, paths and URLs), `learn_more_url`, `data`. Cap 50 issues. |
| `zha_network` | `count`, `devices[]` → `ieee`, `name`, `manufacturer`, `model`, `lqi`, `rssi`, `available`. Cap 50 (upstream cap). |
| `zwave_network` | `count`, `total_count`, `controller` → `home_id`, `type`, `own_node_id`, `is_primary`, `is_suc`, `sdk_version`, `firmware_version`, `status`, `inclusion_state`, `rf_region`, `is_rebuilding_routes`, `supports_long_range`; `nodes[]` → `node_id`, `status`, `is_routing`, `is_secure`, `zwave_plus_version`, `is_controller_node`. Dropped: `controller.nodes` (upstream copies the **raw, uncapped** node list into `controller`), any other controller key. Cap 50 nodes. |
| `thread_network` | `count`, `border_routers[]` → `extended_address`, `channel`, `extended_pan_id`, `border_agent_id`. Never `active_dataset_tlvs` (the Thread network key) or `url`; upstream already projects them out, and Engineering's allowlist keeps them out if upstream changes. Cap 16. |
| `matter_network` | `config_entry_id`, `state`, `title`. |
| `dead_entities` | `summary` → `candidate_total`, `registry_total`; `config_entry_orphans` and `stale_restored` → `count`, `total_count`, `truncated`, `items[]` → `entity_id`, `platform`, `config_entry_id`, `disabled_by`, `has_state`. Cap 50 per bucket (upstream cap). |
| `themes` | `count`, `themes[]` (names, cap 64), `default_theme`, `default_dark_theme`. |
| any section | an `error` string is replaced with the fixed code `section_unavailable`. Upstream error text is dropped. |

### 2.2 `ha_get_app`

`apps[]` → `slug`, `name`, `version`, `state`, `installed`,
`update_available`, `repository`, `description` (max 200 characters), and,
when requested, `stats` → `cpu_percent`, `memory_percent`, `memory_usage`,
`memory_limit` (or `null` when upstream reports a per-add-on stats failure).
A started add-on whose requested stats are `null` or absent makes the result
`partial` with the `stats_unavailable` reason, and its slug is identified in
`warnings`; stopped add-ons carry no stats and do not affect completeness.
`summary` → `total_installed`, `running`, `stopped`, `updates_available`.
`warnings` are reduced to fixed codes (`stats_unavailable:<slug>`); upstream
text is dropped. Nothing from `/addons/{slug}/info` is ever requested, so
`options`, `schema`, `network`, `ip_address`, `hostname`, `ingress_*`,
`webui`, `devices`, `homeassistant_api`, `hassio_api`, `auth_api` and
`host_network` cannot appear. The allowlist would drop them anyway.

### 2.3 `ha_get_integration`

`entries[]` → `entry_id`, `domain`, `title` (max 120 characters), `state`,
`source`, `disabled_by`, `supports_options`, `supports_unload`,
`supports_reconfigure`, `log_level`, `pref_disable_new_entities`,
`pref_disable_polling`. Dropped: `options` (should never be present, and is
removed if it is), `log_level_raw`, any other key. Upstream list rows carry no
`data` and no `unique_id`.

Pagination: `total_count`, `offset`, `limit`, `count`, `has_more`,
`next_offset`, all recomputed by Engineering over the filtered set.
`state_summary` is recomputed. Upstream `query`, `domain_filter` and
`warnings` are dropped and replaced by Engineering's own echo of the validated
arguments.

Disclosed residual: `title` is set by the integration or the user and can
contain an account e-mail address or a host name. It is kept because entries
cannot be identified without it (owner decision **O4**).

## 3. Output bounding

Bounding happens in this order, in Engineering:

1. **Item caps** in the projection (§2): repairs 50, ZHA 50, Z-Wave nodes 50,
   border routers 16, dead entities 50 per bucket, themes 64, other platform
   domains 64, apps 100, integration page `limit ≤ 100` over an upstream window
   of 200. Each capped list reports `count`, `total_count` and `truncated`.
2. **String caps:** every allowed string field is cut to 200 characters
   (`title` 120). Truncated fields are recorded.
3. **Completeness:** `complete` only when nothing was capped, no section
   returned `section_unavailable`, `baseline_available` is true (system
   health), and upstream `has_more` is false (integration). Otherwise
   `partial`, with the reasons listed.
4. The existing gateway path is unchanged: `sanitize_untrusted_data` with
   `max_string=max(2000, min(limit//2, 20000))`, then the fail-closed size check
   `encoded_size + 8_000 > response_limit` → `response_too_large`. With
   `response_limit_bytes` 60000 and the caps above, the projected worst case
   stays well under the limit, so `response_too_large` should become a
   defensive backstop rather than an expected outcome. Part 2 asserts this with
   worst-case fixtures at every cap.

The upstream response itself is still bounded only by the existing transport
limits. The projection operates on the payload that the gateway has already
accepted and normalized.

## 4. Per-version admission

### 4.1 Adapter module

New module `providers/upstream_platform_reads.py`, modelled on
`upstream_blueprint.py`:

- `ADAPTER_BINDINGS`: nine entries, one per (tool, version). Each binds the
  adapter token (for example `ha-mcp-8.5.0-platform-health-v1`), the version,
  the exact source commit and the exact policy input-schema fingerprint from
  §1.4.
- `is_platform_adapter(entry)`: true only when the upstream name, exposed name
  and `mixed_or_requires_wrapper` classification match, there is exactly one
  argument restriction that is a known token, the binding's version and
  input-schema fingerprint match the entry, and the reviewed annotations are
  `readOnlyHint: true`, `destructiveHint: false`.
- `PUBLIC_SCHEMAS`: the three closed schemas (§1).
- `read_arguments(tool, arguments)`: validates and builds the exact upstream
  argument dict (§1). Raises a fixed `ValueError` code otherwise.
- `validate_result(tool, payload, arguments)`: checks `success is True`, the
  top-level shape and the absence of any excluded section key (`diagnostics`,
  `config_check`, `zha_network_full`, `options`). Fails with `invalid_response`.
- `project_result(tool, payload, arguments)`: the §2 allowlists, §3 caps and
  Engineering-side filtering (§1.2, §1.3).
- `completeness(tool, projected)`.

### 4.2 Policy JSON changes (`upstream_tool_policy_8_4_3/8_5_0/8_6_0.json`)

For each of the three entries in each of the three files:

- `classification` stays `mixed_or_requires_wrapper`. The aggregate
  classification counts in the policies and in the contract reviews therefore
  do not change.
- `argument_restrictions`: `[]` → `["<version adapter token>"]`.
- `description`: replaced with an Engineering-authored bounded description
  of the wrapper's arguments and the §1.1/§2 disclosures. The published
  description comes from the policy entry (`upstream_read_gateway.py:368-372`),
  so the upstream description, which advertises slugs, diagnostics and
  options, never becomes model-facing.
- `input_schema_fingerprint`, `reviewed_annotations`, `response_limit_bytes`
  (60000) and `timeout_seconds` (60) are unchanged. The reviewed annotations
  already equal upstream's (system health and app `openWorldHint: true`,
  integration `false`), so unlike the blueprint wrapper no
  `read_annotation_fingerprint` special case is needed.
- Top-level `reviewed_runtime_annotation_fingerprints`,
  `reviewed_runtime_description_fingerprints` and
  `reviewed_runtime_output_schema_fingerprints` must exactly equal the read-route
  set (`load_upstream_tool_policy`, `upstream_tool_policy.py:1216-1364`), so each
  gains three entries. These are derived from the existing committed
  `tools/list` captures (`docs/evidence/upstream-read-compatibility/ha-mcp-<v>.json`);
  no live capture is needed.

The compiled ha-mcp release registry
(`ha_mcp_engineering/upstream_release_registry.json`) binds each release's
`policy_sha256`, so the three `policy_sha256` values change. That is the same
mechanical change made by the 8.5.0 blueprint commit `7cbc490` and the 8.6.0
search commit `28378a5`. It is **not** the signed Core registry, the signed
ha-mcp registry (not published; `ha_mcp_release_registry_enabled: false`), a
HOLD or a support-window entry. Its `tool_contracts` already contain all three
tools, so they do not change. See F7 and owner decision **O5**.

### 4.3 Code changes for admission

- `upstream_tool_policy.py`: `is_read_route` becomes
  `automatic_read or is_blueprint_adapter(self) or is_platform_adapter(self)`
  (`:418-419`). The adapter-binding version check at `:1262-1273` is extended to
  the new bindings.
- `upstream_read_gateway.py`: the same hook points the blueprint adapter uses:
  public schema (`:322`), title (`:362`), dispatch arguments (`:2693`), result
  validation (`:3264`), plus the projection call before sanitization (`~:3333`),
  adapter id (`:5111`) and completeness (`:6028`).
- `ha_mcp_readmission/ha_mcp.py`: `_profile_for_release` already iterates
  `entry.is_read_route` and includes `argument_restrictions` in the contract
  fingerprint, so the three tools join the ha-mcp readmission profile with no
  code change expected. Part 2 verifies this with a test.

### 4.4 Drift quarantine (affected tool only)

Existing per-tool quarantine applies unchanged. If the observed input schema,
description, annotations or output schema of one of the three tools drifts from
its reviewed fingerprint, only that tool is quarantined. The other 27 delegated
reads stay admitted. If a binding's fingerprint does not match its policy entry,
`is_platform_adapter` is false: the entry stays `mixed_or_requires_wrapper`
with no read route, so the tool is simply not exposed. It is never exposed with
the upstream schema. Part 2 adds a negative test for each case.

`MAX_QUARANTINE_RECORDS = 26` (`upstream_read_gateway.py:113`) is a display cap.
`quarantined_tools` and `missing_tools` report truncation, but
`core_withheld_tools` (`:1739`) does not. With 28 read routes, a whole-Core
withhold would list 26 of 28 tools without saying so. Proposal: raise the cap
to 32 and add `core_withheld_truncated` (F8).

### 4.5 8.6.0 HOLD

Unchanged. The 8.6.0 bindings and policy entries are added so the candidate
stays reviewable, exactly as the blueprint wrapper did. The production HOLD in
`ci/support-window.yaml` and every merge gate stay as they are.

## 5. Core readmission

### 5.1 Route mapping

`DELEGATED_CORE_REQUIREMENTS` (`ha_core_readmission/routes.py:74-102`) must
gain all three tools. An unmapped delegated tool silently skips the Core gate
(F6 from 069A). The static 069A test
(`tests/test_hamcp069a_entity_metadata_readmission.py:490-525`) does not cover
these tools; their mapping is covered by the updated Core catalog test in
`tests/test_ha_core_2026_9_integration.py` and the route tests in
`tests/test_hamcp129_platform_reads.py` (`CatalogMetadataTests`,
`CoreRouteTests`).

| Tool | Core surfaces actually used | Proposed requirement |
|---|---|---|
| `ha_get_system_health` | WS `system_health/info`, `repairs/list_issues`, `zha/devices`, `zwave_js/network_status`, `otbr/info`, `config_entries/get`, `frontend/get_themes`, entity registry; REST `/api/states`; custom-component `ha_mcp_tools/system_snapshot` | `("core.basic_websocket_read", "core.basic_rest_read")` |
| `ha_get_app` | Supervisor `GET /addons` and `/addons/{slug}/stats`. In add-on mode ha-mcp calls Supervisor directly with its own token; otherwise through Core WS `supervisor/api` (TA 8.5.0:437-500) | `("core.basic_websocket_read",)` |
| `ha_get_integration` | REST `GET /api/config/config_entries/entry` (legacy) or WS `ha_mcp_tools/config_entries` (component); WS `logger/log_info` | `("core.basic_websocket_read", "core.basic_rest_read")` |

This follows the existing delegated-read precedent: `ha_get_blueprint` and
`ha_manage_blueprints` map to `core.basic_websocket_read`, and
`ha_get_hacs_info` maps to `core.basic_rest_read`, although neither profile
attests those specific commands. The basic profiles attest only
`rest_config_mapping`, `rest_states_list` and `rest_state_entity_shape`, and
`websocket_auth_ok_version`, `websocket_get_config_mapping` and
`websocket_result_envelope` (`profiles.py:~145-200`). Per-command shape drift is
caught by Engineering's `validate_result` and projection, which fail closed with
`invalid_response`. **Supervisor is not attested by any Core profile.** That gap
is disclosed in the `ha_get_app` description and in `list_capabilities`.

### 5.2 Is a signed-registry change needed?

**No**, under the proposed mapping. Signed Core registry sequence 9
(`upstream-trust/ha-core-release-registry.json`, last envelope) lists
`core.basic_websocket_read`, `core.basic_rest_read` and
`core.non_device_registry_read` for 2026.9.4, 2026.10.0 and 2026.10.1, and
revokes 2026.9.2 and 2026.9.3. The tools are therefore available on all three
supported Cores as soon as the release ships, with no new signed envelope and
no dormancy.

The alternative is a new profile (for example `core.platform_health_read`)
that attests `system_health/info`, `repairs/list_issues`, `otbr/info` and the
config-entries list. It would require a new `CORE_CAPABILITY_PROFILES` entry, a
container evidence run and an owner-signed registry sequence 10. The tools would
ship **dormant**, failing closed with `CoreCapabilityUnavailable` on every Core
until that PR merges, as 069A did. That is owner decision **O1**. The
recommendation is the basic-profile mapping, because these are reads and the
precedent reads use it.

### 5.3 Behaviour on the supported Cores

| Core | Evidence | Expected behaviour |
|---|---|---|
| 2026.9.4 | local source | All read paths present. `system_health/info` subscription with `initial`/`update`/`finish` events; cloud and hassio reachability probes; `otbr/info`; `repairs/list_issues`. |
| 2026.10.0 | source archive diff against 2026.9.4 | `system_health/__init__.py`, `otbr/websocket_api.py` and `logger/websocket_api.py`: only the voluptuous→probatio schema-library change. `repairs/websocket_api.py`: `ignore_issue` gains an unknown-issue error (a write path that is not used); `list_issues` unchanged. `config/config_entries.py`: flow-result typing only. `hassio/websocket_api.py`: decorator additions. `hassio`/`cloud` `system_health.py`: identical. No change to a path the wrappers use. |
| 2026.10.1 | HAMCP-161 changed-file list and diffstat only | Of the relevant components, only `zha/websocket_api.py` (+1 line) and the `zha` manifest (library bump) changed, plus `matter` entity modules that `matter_network` does not use. The ZHA one-line change was **not inspected** (no local 2026.10.1 tree). `zha/devices` shape drift would fail closed through the projection type checks. Part 2 must inspect it from exact source before claiming parity (F11). |

## 6. Catalog and metadata

- **Tool count:** current total is **84**, not 83 (F1): 59 Engineering static
  (`tools/registry.py:170-171`, `ENGINEERING_STATIC_TOOL_COUNT = 59`) plus 25
  delegated reads. After HAMCP-129: **87** (59 + 28). The static count does not
  change.
- **Delegated read count:** 25 → 28 on 8.4.3, 8.5.0 and 8.6.0 alike, because
  all three versions get bindings. 8.4.3 currently has 25 automatic reads and no
  blueprint adapter (the blueprint bindings cover 8.5.0 and 8.6.0 only; F10), so
  its delegated surface differs from 8.5.0's by tool identity even though the
  counts match.
- **Tests and fixtures asserting 84 or 25** (Part 2 updates them):
  `tests/test_beta56…:324`, `test_beta58…:235`,
  `test_core_update_continuity.py:554`,
  `test_exact_8_1_runtime_acceptance.py:938/974/998`,
  `test_ha_core_2026_9_integration.py:2930,2991` (it also asserts
  `len(DELEGATED_CORE_REQUIREMENTS) == 27`, which becomes 30),
  `test_rc3…`, `test_rc4…`, `test_ha_mcp_production_readmission.py`,
  `tests/fixtures/dashboard_analysis/native-c82-descriptors.json`,
  `tests/fixtures/integration_inspection/beta10_catalog_baseline.json`,
  `promotion/promotion_regression_manifest.yaml`. Also `scripts/codex-context.py`
  and `tests/test_codex_workflow.py` if they carry counts, as they did in `7cbc490`.
- **`list_capabilities` (HAMCP-157 provider matrix):** three new
  `CAPABILITY_PROVIDER_MATRIX` rows in `capabilities.py`, one per tool, with
  `standard_ha_mcp_coverage: "reviewed_argument_constrained"`,
  `direct_ha_coverage: "not_used"`, `selected_provider: "upstream_platform_reads"`,
  `fallback_policy: "none"`, `trust_mode: "reviewed_argument_constrained"`,
  `trust_profile` = the adapter tokens, a `completeness` value of
  `projected_allowlist_partial_disclosed`, and a `security_justification` naming
  the forced arguments and the disclosures. `core_applicability` is
  `basic_websocket/rest_profiles_supervisor_unattested` for `ha_get_app`. The
  dynamic upstream capability rows come from gateway admission unchanged.
- **Documents:** `TOOL_AUDIT.md:12` and ADR-024 / `integration_inspection/`
  currently say `ha_get_integration` "remains unexposed" because it can
  initialize an options flow. Part 2 amends both: the **wrapper** is exposed in
  list-only form; the upstream tool, `domain` and detail mode remain unexposed.
  `docs/2_1_ROADMAP.md:74-77` (HAMCP-129 entry) is marked as delivered by the
  wrappers.

## 7. Tests and mutation check plan

### 7.1 New tests (`tests/test_hamcp129_platform_reads.py`)

Each group runs for 8.4.3, 8.5.0 and 8.6.0:

1. **Schema binding:** the published schema equals `PUBLIC_SCHEMAS[tool]`
   exactly, `additionalProperties` is false, and the upstream schema is never
   published.
2. **Argument construction:** a table of public inputs and the exact upstream
   dicts (§1), including the sorted `include`, the forced `include_options:
   false` and `exact_match: true`, the `domain` → `query` prefilter, and the
   absence of `slug`, `domain` and `entry_id` from every upstream dict.
3. **Refusal:** each rejected upstream parameter supplied publicly →
   `invalid_arguments` with **no dispatch** (fake transport records zero
   calls). Covers `diagnostics`, `config_check`, `zha_network_full`, unknown
   section strings, comma injection in `sections`, `include_options`,
   `subentry_*`, `device_id`, every `diagnostics_*`, `source="available"`,
   `query` for apps, `include_schema`, `exact_match: false`, out-of-range `limit` and `offset`,
   bad patterns, and `entry_id` combined with other filters.
4. **Projection canaries:** fixture payloads seeded with `options`, `schema`,
   `network`, `ip_address`, `hostname`, `nameservers`, `instance_id`,
   `remote_server`, `certificate_fingerprint`, `translation_placeholders`,
   `learn_more_url`, `active_dataset_tlvs`, `url`, `controller.nodes`,
   `ws_server_url`, `log_level_raw`, `ha_mcp_update` and an unknown key in each
   object. Assert none survive, and that every allowed field does.
5. **Shape refusal:** a wrong type for an allowed field, `success: false`, or an
   excluded section key present → `invalid_response`, dispatched=true, no
   partial result.
6. **Bounding:** worst-case fixtures at every cap. Assert `truncated`,
   `total_count`, `completeness: "partial"` with reasons, and an encoded size
   below the response limit.
7. **Integration filtering:** domain exactness (`zha` does not match
   `zha_toolkit`), query substring, state filter, local pagination,
   `has_more` → partial, `entry_id` outside the window → `resource_not_found`
   with partial reason.
8. **App slug filtering** and the stats cap (more than 32 started add-ons in
   the stats-free snapshot → `response_too_large` with only that call
   dispatched; exactly 32 → the stats call; growth between the two calls and
   concurrent calls → the documented sampled-bound race, counted at the
   synthetic upstream fan-out), and stats completeness (a started add-on
   without stats → `partial`/`stats_unavailable`), if O3 keeps
   `include_stats`.
9. **Admission and quarantine:** a binding fingerprint mismatch → the tool is
   not exposed and the other 27 reads remain. Description, annotation and output
   drift → only that tool is quarantined. An adapter token on the wrong version
   is refused at policy load.
10. **Core mapping:** all three tools are in `DELEGATED_CORE_REQUIREMENTS`
    (covered by the updated Core catalog test and the new route tests; the
    static 069A F6 test is not extended). A missing or withheld basic
    profile → `CoreCapabilityUnavailable` with no dispatch. Supported on
    2026.9.4, 2026.10.0 and 2026.10.1 through registry sequence 9; revoked
    2026.9.2/2026.9.3 → withheld.
11. **ha-mcp readmission profile:** the three tools appear as ordinary-read
    capabilities with contract fingerprints that include the adapter tokens.
12. **No HOLD or support-window drift:** `ci/support-window.yaml` and the 8.6.0
    disposition are unchanged (existing guards).

### 7.2 Fixtures

Upstream result fixtures are generated by running the exact pinned ha-mcp tool
functions (for each version) against a fake Home Assistant/Supervisor client in
a harness, with a `provenance.json` naming the source commit and the generator.
This follows the pattern of `tests/fixtures/ha_mcp_8_5_0/` and `fan_beta1/`.
They are not hand-written and no live system is touched.

### 7.3 Mutation checks

Each mutation is applied by a small script to a scratch copy, the focused test
file is run, and the failing test is recorded in the PR evidence. Each mutation
must make at least one test fail:

| # | Mutation | Expected failing group |
|---|---|---|
| M1 | Stop forcing `include_options: false` | 2, 4 |
| M2 | Send `domain` upstream | 2 |
| M3 | Send `slug` upstream | 2, 4 |
| M4 | Add `diagnostics` (or `config_check`, `zha_network_full`) to the section enum | 1, 3 |
| M5 | Build `include` from raw caller text | 3 |
| M6 | Replace the projection with pass-through | 4 |
| M7 | Drop one allowlist type check | 5 |
| M8 | Remove an item cap or `truncated` flag | 6 |
| M9 | Allow `exact_match: false` | 2, 3 |
| M10 | Skip `validate_result` | 5 |
| M11 | Remove a version check from `is_platform_adapter` | 9 |
| M12 | Remove one tool from `DELEGATED_CORE_REQUIREMENTS` | 10 |
| M13 | Set `additionalProperties: true` | 1, 3 |
| M14 | Remove the stats-cap pre-check (if O3 keeps stats) | 8 |

The executed author mutation set is in the PR evidence: 23 mutations in the
first batch (M1-M10, A1-A7, E1-E4, B1-B2) plus the correction-batch mutations
for D8, the stats cap and stats completeness.

Local gate: the focused tests, then `scripts/check.ps1 -Tier Full` and
`-Tier Evidence` where available (on Linux the script is not verified; the
equivalent pytest and validation commands are run and reported). No manual or
full-tier CI dispatch.

## 8. File-by-file change list, PR fit and release metadata

All paths under `hass_mcp_engineering_beta/ha_mcp_engineering/` are protected
and need Josh's explicit authorization for Part 2 (owner decision **O6**).

| File | Change | Estimate |
|---|---|---|
| `providers/upstream_platform_reads.py` (new) | bindings, schemas, argument builders, validation, projection, completeness | 400–500 lines |
| `providers/upstream_read_gateway.py` | hook points (§4.3); `MAX_QUARANTINE_RECORDS` 32; `core_withheld_truncated` | 50–70 |
| `upstream_tool_policy.py` | `is_read_route`; binding check | 20–30 |
| `upstream_tool_policy_8_4_3.json`, `_8_5_0.json`, `_8_6_0.json` | 3 entries each plus three fingerprint maps | 3 × single-line JSON |
| `upstream_release_registry.json` | 3 × `policy_sha256` | 1 line (single-line JSON) |
| `ha_core_readmission/routes.py` | 3 mappings | +3 |
| `capabilities.py` | 3 provider-matrix rows | 40–60 |
| `ha_mcp_readmission/ha_mcp.py` | expected none; test-verified | 0–5 |
| `docs/evidence/upstream-read-compatibility/ha-mcp-<v>-contract-review.json` | only if they enumerate adapter tokens (verify); counts unchanged | 0–3 lines |
| `tests/test_hamcp129_platform_reads.py` (new) | §7.1 | 700–900 |
| `tests/fixtures/hamcp129/` (new) | generator, provenance, results for 3 tools × 3 versions | 200 + data |
| count-asserting tests and fixtures (§6) | 84 → 87, 25 → 28, 27 → 30 | ~12 files, 1–5 lines each |
| `TOOL_AUDIT.md`, ADR-024, `docs/2_1_ROADMAP.md`, `docs/HA_MCP_8_*_COMPATIBILITY.md` | §6 amendments | 30–60 |
| `scripts/codex-context.py`, `tests/test_codex_workflow.py` | only if counts are carried | 0–30 |
| release metadata (`config.yaml`, `.release/`, changelog) | next beta after beta.5 | see below |

Total: about 1,600–2,200 changed lines across roughly 30 files, comparable to
the 8.5.0 blueprint commit `7cbc490` (30 files, about 1,270 lines).

**PR fit:** one coherent PR is recommended. The three wrappers share one
adapter module, one set of policy, registry and count edits, and one review. If
the review budget favors two PRs, the cleanest split is PR-A
(`ha_get_integration` + `ha_get_app`: simpler projections, closes the
inventory half of F001) and PR-B (`ha_get_system_health`: the open-world
disclosure and the largest projection). The split repeats the policy, registry
and count churn (total 86, then 87). Owner decision **O7**.

**Release metadata:** HAMCP-168 / PR #246 takes `2.4.1-beta.5`. HAMCP-129
implementation follows it and would take the next beta (`2.4.1-beta.6`, or
whatever follows the last merged runtime PR), materialized in its own PR with
`scripts/promote_next_release.py --apply` before Ready. That touches the
protected `config.yaml` and `.release/` paths and needs explicit authorization
(part of **O6**). Part 1 makes no release-metadata change.

## 9. Findings that contradict the brief's assumptions

| # | Finding | Evidence | Effect on design |
|---|---|---|---|
| F1 | The connector total is **84**, not 83. After HAMCP-129 it is 87, not 86. | `tools/registry.py:170-171` (static 59) + 25 delegated; tests assert 84 (§6) | Counts in §6. |
| F2 | `source="installed"` does not constrain `slug`; a slug returns raw `/addons/{slug}/info`, including add-on options. `query` does nothing for installed apps. | TA 8.5.0:2469-2479 | `slug` is never sent upstream; Engineering filters (§1.2). |
| F3 | Open-world behaviour cannot be removed with an `include` allowlist; the baseline always runs Core `system_health/info`, which starts outbound reachability probes, and upstream always computes `ha_mcp_update`. | Core 2026.10.0 `system_health/__init__.py:179-200`; `cloud/system_health.py:53-62`; `hassio/system_health.py:107-115`; `update_check.py:52-53` | Disclosed; `openWorldHint` stays true (O2). |
| F4 | `domain` forces options reads, which start real options flows on the legacy path; `entry_id` detail mode can also start options flows. | TI 8.5.0:830-832, ~1568; TI 8.6.0:1077-1078, 1154, 1608 | List mode only; filtering in Engineering (§1.3). |
| F5 | There are upstream parameters beyond the brief's list (`include_schema`, `device_id` and six `diagnostics_*` on integration; `device_id` and the `diagnostics_*` set on system health); `include_subentries` is detail-only; unknown `include` names are silently ignored. | TS/TI at all three versions | All rejected (§1). |
| F6 | The existing sanitizer is a key denylist and misses compound and network keys. | `sanitization.py:14-75,152-173` | New positive allowlist projection before sanitization (§2). |
| F7 | Part 2 cannot avoid editing the compiled ha-mcp release registry's `policy_sha256` for 8.4.3/8.5.0/8.6.0. This is not the signed registry, HOLD or support window. | `upstream_release_registry.json`; precedents `7cbc490`, `28378a5` | Flagged for confirmation (O5). |
| F8 | `MAX_QUARANTINE_RECORDS = 26` is below the new read-route count (28), and `core_withheld_tools` has no truncation flag. | `upstream_read_gateway.py:113, 1739` | Raise to 32 and add the flag (§4.4). |
| F9 | Whether the deployed ha-mcp runs in add-on mode (direct Supervisor and Supervisor-based `ha_mcp_update`) or not (Core `supervisor/api` and PyPI update check) is not provable from this repository. The `redact_secrets` setting is UNKNOWN. | TA 8.5.0:488-500; `_version.py:77-90` | Design does not depend on either; architect to confirm mode live if wanted. |
| F10 | The blueprint wrapper binds only 8.5.0 and 8.6.0; 8.4.3 needs its own bindings for the new tools. | `upstream_blueprint.py:13-19` | Nine bindings (§4.1). |
| F11 | The 2026.10.1 Core change to `zha/websocket_api.py` (+1 line) was not inspected, because the 2026.10.1 tree is not available locally. | HAMCP-161 diffstat | Part 2 inspects it from exact source; drift fails closed. |
| F12 | Basic Core profiles do not attest the commands these tools use, and no Core profile attests Supervisor. | `profiles.py:~145-200` | Precedent mapping plus disclosure, or a dormant signed profile (O1). |
| F13 | The compiled registry records `advertised_tool_count` 77 for 8.5.0, while the architect observed 80 tools live. The difference is probably the custom component's tools (the File & YAML entry is installed), but this was not verified. | `upstream_release_registry.json` | Not a blocker for this design; flagged for the architect. |
| F14 | The `thread_network` and `matter_network` sections give border-router radio parameters and Matter config-entry state only, with no per-node Thread or Matter health. For HAHE-F021, the useful evidence is the combination: OTBR and Matter Server app state and version (`ha_get_app`), the `thread`, `otbr` and `matter` config-entry states (`ha_get_integration`), and border routers plus repairs (`ha_get_system_health`). | TS 8.5.0 thread/matter sections | Disclosed in the descriptions; per-node health is out of scope. |
| F15 | Upstream `component_count` counts the event envelope (always 2), not components. | TS 8.5.0:1216-1217 with Core `initial` event | Dropped from the projection. |
| F16 | The architect's 2026-10-11 register proposal included `config_check` in the system-health sections and integration **detail** mode. Source review shows `config_check` is a POST to Core's check-config action and detail mode can start options flows (F4), so this design excludes both. | Register HAMCP-129 notes; §1.1, §1.3 | Narrower than proposed; recorded for Josh rather than silently dropped. |
| F17 | The register outcome asks for CPU, RAM and HA-disk measurements. These three tools provide host disk (`disk_total`, `disk_used`, `disk_life_time`, as Supervisor-formatted strings) and per-add-on CPU/memory (`include_stats`), but **no host or Core CPU/RAM**. The upstream values also carry no observation timestamp; Engineering stamps the response time only. | Core 2026.10.0 `hassio/system_health.py:82-103`; TA 8.5.0:1130-1151 | Host CPU/RAM remains a gap after HAMCP-129; a separate source (for example Supervisor `/host/info` or `/core/stats`) would need its own review. |

## 10. Owner decisions requested

| # | Decision | Recommendation |
|---|---|---|
| O1 | Core mapping: basic profiles now (available on all three Cores, Supervisor gap disclosed), or a new dormant signed profile (needs registry sequence 10 before use) | Basic profiles (§5) |
| O2 | Accept the system-health baseline's disclosed open-world behaviour, or refuse the tool | Accept with disclosure |
| O3 | `include_stats` on `ha_get_app`: allow with the two-call pre-check and the 32-add-on cap, or reject in the first release | Allow with the cap |
| O4 | Keep integration `title` (may contain account e-mail or host) | Keep, disclosed, capped at 120 characters |
| O5 | Confirm that the compiled `upstream_release_registry.json` `policy_sha256` edits are in scope for Part 2 (not the "registry" the limits exclude) | Confirm |
| O6 | Authorize the protected paths for Part 2 (§8), including release metadata for the beta after beta.5 | Authorize after #246 merges |
| O7 | One PR or the A/B split | One PR |
| O8 | Repairs: drop `translation_placeholders` entirely, or keep them with a key allowlist | Drop in the first release |
| O9 | Accept the narrowing against the 2026-10-11 proposal (no `config_check`, no integration detail mode; F16) and the remaining host CPU/RAM gap (F17) | Accept; track host CPU/RAM separately |

## 11. Non-actions in Part 1

- No production code, schema, policy, registry, HOLD, support-window,
  merge-gate, shard or workflow change.
- No live Home Assistant, Supervisor or Engineering access; no credentials read.
- No manual or full-tier CI dispatch.
- No change to PR #246 or its files (`governance/service.py`, `f3_runtime/*`).
- Draft PR only; not marked Ready, not merged, not released or deployed.

## 12. Part 2 implementation record and deviations

Implemented in `providers/upstream_platform_reads.py` with gateway, policy,
Core-route and capability hooks, and tested in
`tests/test_hamcp129_platform_reads.py`. Exact upstream outputs were generated
offline by running the pinned ha-mcp releases against synthetic Core and
Supervisor fakes (`tests/fixtures/hamcp129/`, see `provenance.json`); the
replay tests project them through the shipped gateway.

None of the following changes an approved decision:

- **D1 (F11 resolved).** Core 2026.10.1 (`abe1fdd7cd6b4de1715c87b6fd87d92699514ef9`)
  adds one `connection.send_result(msg[ID])` to `zha/devices/reconfigure`
  (`zha/websocket_api.py:516`). The health section uses only `zha/devices`; no
  effect.
- **D2 (evidence identities, O5).** The reviewed contract-review evidence
  `classification_counts` change (automatic reads 25 to 28, wrapper-required
  13 to 10 at each version), so the compiled registry's
  `artifact_evidence_sha256` and the derived dashboard
  `attestation_fingerprint` are regenerated, together with the constants that
  pin them (`upstream_dashboard_builtin_attestations.json`,
  `tests/fixtures/ha_core_2026_9_authority.json`,
  `scripts/exact_8_4_1_dashboard_quarantine_acceptance.py`,
  `tests/test_exact_8_1_runtime_acceptance.py`). Precedent `9a143b2`. The
  owner-signed Core registry is unchanged.
- **D3 (binding mismatch).** A policy entry whose adapter token does not match
  its exact tool, version, source commit and schema fingerprint refuses the
  whole policy load (the blueprint-adapter precedent), while runtime upstream
  drift still quarantines only the affected tool.
- **D4 (size backstop).** Adversarial maximum-length strings in every capped
  list can exceed the response limit; such results fail closed as
  `response_too_large` instead of being silently truncated further.
- **D5 (fail-closed shapes).** A config entry carrying `options` (impossible
  with `include_options=false`) refuses the result. Section failures are
  reported with the fixed `section_unavailable` code; upstream error text and
  warnings are replaced with fixed codes (`baseline_unavailable`,
  `upstream_warnings_withheld`, `stats_unavailable:<slug>`). A started add-on
  with requested stats `null` or absent is `partial` (`stats_unavailable`);
  this replaces the first batch's `invalid_response` for an absent `stats`
  key and its `complete` result for `null` stats (review R3).
- **D6 (output additions).** Lists carry `total_count` and `truncated`; the
  app summary is recomputed from the projected listing; not-found refusals
  carry completeness details; the Matter title is masked like integration
  titles; `query` is trimmed and lower-cased before matching.
- **D7 (test harness).** The configured upstream endpoint host and path are
  redaction secrets in the sanitizer, so the test endpoint uses names that do
  not collide with the fixed reason codes.
- **D8 (signed future-release reuse; owner decision 2026-10-13).** The
  platform adapters are bound to exact ha-mcp source. Josh decided that
  signed-only reuse must not be narrowed: a future ha-mcp release admitted
  only through an owner-signed entry (no compiled exact release) keeps exactly
  the base behaviour for the existing 25 delegated reads. Only the three
  platform wrappers are withheld, because their per-version bindings are
  absent. `HaMcpAuthoritySelector._select_signed_profile` derives a signed-reuse
  policy view (`withhold_unbound_platform_reads`) in which the unbound wrappers
  revert to their unwrapped `mixed_or_requires_wrapper` upstream entries, as
  on base, so they are blocked and never exposed; every other entry is reused
  unchanged, and the existing `7cbc490` rule is evaluated on that view (the
  8.5.0 and 8.6.0 blueprint adapters still make those policies non-reusable,
  as on base). The withholding is explicit: the selection carries
  `withheld_capabilities`, and gateway health, the `automatic_readmission`
  projection and the readmission audit report `readmission_withheld_read_count`,
  `readmission_withheld_reason_counts` and `readmission_withheld_tools` with
  the reason `platform_read_binding_absent`. Nothing is quarantined and the 25
  reads are not removed. A compiled exact release withholds nothing and
  exposes all 28. `tests/test_beta58_ha_mcp_8_4_3_continuity.py` asserts the
  25 signed-only reads, the base classification accounting, the 24-read
  blueprint transition and the 28 exact reads. It also dispatches an ordinary
  read through the signed authority, shows that drift in one old read removes
  only that read while the three wrappers stay withheld, and shows that an
  expired signed registry still exposes nothing.
  The policy hash change approved under O5 still stops any published signed
  ha-mcp entry that binds the old 8.4.3, 8.5.0 or 8.6.0 policy hash from
  matching; whether any such entry is published cannot be determined from
  source (`unknown`). The owner-run
  `scripts/prepare_ha_mcp_release_registry_update.py` is unchanged. For a
  version other than the bound profile its `matched_reads` evidence can list
  the three wrappers that the runtime then withholds with
  `platform_read_binding_absent`; its exact-version evidence reports
  `matched_read_count` 28 for 8.4.3.
- **D9 (§6 file list).** `tests/test_ha_mcp_production_readmission.py`
  (pinned to 8.2.0) and `promotion_regression_manifest.yaml` (beta.39) did not
  need changes. The count assertions for the current versions changed in the
  test modules listed in the PR instead.
- **D10 (CI fixture).** CI on `1b551c5` showed that the synthetic
  `scripts/fake_ha_read_gateway_contract_server.py` used by the exact-image
  read-gateway lane lacked the config-entry listing, `logger/log_info` and the
  `system_health/info` stream. `d563f0c` adds synthetic read-only responses
  for them. This is fixture-only and changes no runtime.
- **D11 (stats bound is sampled; open for owner decision).** The independent
  review asked for the 32-started-add-on cap to be enforced before any stats
  request is dispatched, from one consistent snapshot, with no excess
  dispatch. Engineering enforces it on its own side: when its stats-free
  snapshot shows more than 32 started add-ons, it never dispatches the stats
  call, so the stats requests it causes come from at most one upstream call.
  It cannot enforce the cap at the fan-out boundary. Upstream `list_addons`
  re-lists before its `asyncio.gather` over `/addons/{slug}/stats` (TA
  8.5.0:1130-1151), and no reviewed ha-mcp release offers a per-slug or
  slug-set stats tool. An add-on started between the two calls is therefore
  still fanned out upstream before Engineering refuses the over-cap response
  with `response_too_large`. `test_stats_cap_snapshot_and_documented_race`
  records this window, including concurrent calls. A hard bound needs one of:
  a separately reviewed provider contract, an Engineering-native stats path
  (new direct-HA reachability), or withdrawing `include_stats`. Each needs
  explicit scope, so this correction batch only states the limit accurately
  and leaves the choice to Josh. The public schema description ("at most 32
  started add-ons") describes the Engineering-side refusal and is unchanged.
