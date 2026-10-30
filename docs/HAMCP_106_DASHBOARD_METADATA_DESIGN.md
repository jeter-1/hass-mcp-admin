# HAMCP-106 governed dashboard metadata (built inactive)

`create_dashboard_metadata_plan` proposes one change to the metadata of one
existing storage dashboard: `title`, `icon`, `show_in_sidebar` and
`require_admin`. It is Engineering-native and built **inactive**: it refuses
`pending_signed_registry_admission` until an owner-signed Core registry
sequence names its profile. The planned sequence is 15 (the November signing
batch). Released in 2.4.1-beta.50.

Dashboard content, creating or deleting dashboards, `url_path` and `mode`
changes, YAML dashboards, resources and themes are out of scope.

## 0. Owner decisions (Josh, 2026-10-30)

1. **Route.** Engineering-native, direct to Core. The one write is
   `lovelace/dashboards/update` by `dashboard_id`, carrying only the
   requested fields. The readback is `lovelace/dashboards/list`. It does not
   go through ha-mcp. `ha_config_set_dashboard` upserts (a missing
   `url_path` is created) and Engineering's F3 dashboard contract forbids
   metadata arguments, so that route would need a new upstream contract.
2. **Built inactive.** A new compiled Core profile,
   `core.dashboard_metadata_operation`, that no Core release selects under
   the signed sequence 14. The tool is withheld with
   `positive_authority_missing` and refuses before any read. No
   already-signed fingerprint changes. The profile joins the seq-15 batch.
3. **Disclosure is generic.** It does not count non-admin users.
   `config/auth/list` is not an admitted read, and reading it would conflict
   with HAMCP-205 minimization.
4. **Policy.** `standard_admin` for all four fields, physical consequence
   none. The fixed disclosure texts are:
   - `require_admin` true → false: "Non-admin users will be able to see and
     open this dashboard. This setting controls visibility only; it does not
     limit which devices non-admin users can control."
   - `require_admin` false → true: "Non-admin users will no longer see or
     open this dashboard."
   - `show_in_sidebar` → false: "Hidden from the sidebar; still reachable by
     URL."

## 1. Core contract (pinned source, identical at 2026.10.1 and 2026.10.3)

- **Write.** `lovelace/dashboards/update` takes `dashboard_id` plus any of
  `require_admin` (bool), `icon` (`cv.icon` or null), `title` (`cv.string`)
  and `show_in_sidebar` (bool). References:
  - `components/lovelace/const.py:71-76`;
  - `helpers/collection.py:634-645` and `:737-762`;
  - `components/lovelace/dashboard.py:314-322`.

  Core behavior that the profile relies on:
  - The update merges `{**item, **update}`, and an `icon` of null removes
    the icon.
  - An unknown id is `not_found`; there is no implicit create.
  - `url_path` and `mode` are not in the update schema, and an `id` update
    is rejected.
  - `cv.string` does not trim, so `"Map "` is stored exactly.
  - `cv.icon` only requires a `:`.
- **Readback.** `lovelace/dashboards/list` returns every dashboard's
  configuration, YAML dashboards included.
- **Overview.** Core 2026.10.x migrates the default Overview into a storage
  entry with `url_path` `"lovelace"`. The tool refuses `"lovelace"` by name.
- **Content** (the `lovelace.<id>` store) is not touched by the update.

## 2. The tool

Input (closed schema, at least one field besides `url_path`):

| Field | Rule |
|---|---|
| `url_path` | Required. An exact existing storage dashboard, not `"lovelace"`. |
| `title` | Non-empty string, at most 255 characters, stored exactly (no trim). Credential-shaped text is refused. |
| `icon` | `prefix:name`, at most 64 characters, or `null` to clear. |
| `show_in_sidebar` | Boolean. |
| `require_admin` | Boolean. |
| `expiration_minutes` | 5–1440, default 120. |

Only the fields present in the request are sent; an absent field is never
sent. `false` and `null` are values, not absence.

Planning refuses before any dispatch, and creates no plan, when:
- the `url_path` is unknown (`dashboard_metadata_target_not_found`);
- the dashboard is a YAML dashboard (`dashboard_metadata_yaml_dashboard`);
- the `url_path` is `"lovelace"` (`dashboard_metadata_default_dashboard`);
- the target is ambiguous (`dashboard_metadata_target_ambiguous`);
- a value is invalid (`dashboard_metadata_invalid_value`), a field is not
  supported, or no field is requested;
- the request changes nothing. The result is a truthful no-change refusal
  (`dashboard_metadata_no_change`, `already_satisfied`) with no plan.

## 3. Fingerprint, disclosure and policy

- **Binding.** The plan's current and proposed state is the metadata row
  (`id`, `url_path`, `mode`, `title`, `icon`, `show_in_sidebar`,
  `require_admin`) plus the dashboard content fingerprint. The content
  fingerprint is the `engineering_config_hash` from the existing admitted
  `get_dashboard_config` read (`force_reload`, with the returned `url_path`
  checked). If that read is unavailable, the request fails closed
  (`dashboard_metadata_content_fingerprint_unavailable`).
- **Stable read.** The state read is list → content fingerprint → list
  again. If the row changed between the two lists, the read is refused as
  `dashboard_metadata_read_unstable`.
- **Disclosure.** The plan shows each requested field before and after, plus
  the fixed texts in section 0. The plan-level disclosure records:
  - the provider `direct_home_assistant_dashboard_metadata`;
  - the execution `single_update_at_most_once`;
  - no fallback;
  - the content binding, the target count and the rollback route;
  - the residual concurrency window. Core has no compare-and-swap, so a
    concurrent edit between the final read and the write is possible; the
    readback detects it.
- **Policy.** `standard_admin`, physical consequence none, reason codes
  `dashboard_metadata_standard_policy` and
  `supported_configuration_change`. The policy classifies a plan as
  `PROHIBITED` (`dashboard_metadata_execution_contract_incomplete`) when its
  execution contract is incomplete or when it is a multi-operation plan
  that contains a dashboard-metadata operation.
- **Title text** is untrusted. Planning screens it. The plan description
  names fields only, never the title.

## 4. Apply, verification and rollback

The F3 runtime is reused unchanged: durable intent, an exact-target lock,
and `mark_provider_send()` at the send boundary.

- **Before dispatch.** The fingerprint is re-read immediately before the
  write. Drift is refused as `stale_target_state`, with no dispatch.
- **Dispatch.** One `lovelace/dashboards/update` by the `dashboard_id` of
  the one listed row, at most once, never redispatched, with no fallback.
  The `dashboard_id` comes from that row; Core's slug is never assumed.
- **Errors after the send.** The outcome uses the existing vocabulary:
  - Core `not_found` (404), or `unauthorized`, is not dispatched
    (`dispatch_failed_confirmed`).
  - Any other Core error, a malformed response, or a response for a
    different id is `provider_response_indeterminate` /
    `possibly_dispatched`. The exact readback decides the outcome.
- **Readback.** `lovelace/dashboards/list`. The requested fields must equal
  the request exactly, the unrequested fields must be unchanged, and the
  content fingerprint must be unchanged. Any mismatch is a verification
  failure. Recovery is read-only.
- **Rollback.** `rollback_change` creates a separate governed plan that
  restores the prior values of the changed fields
  (`hamcp106-dashboard-metadata-restore-v1`). It refuses in these cases:
  - the forward change was not verified, or not dispatched;
  - the prior state is missing;
  - the current state no longer equals the forward result
    (`dashboard_metadata_rollback_baseline_drift`).

## 5. Inactive mechanism

- The profile is in `CORE_NOVEMBER_BATCH_PROFILES` and is not in signed
  sequence 14, so no Core release selects it.
- The tool, `create_configuration_plan` and the resource-write routes
  refuse `pending_signed_registry_admission` first, before any read,
  validation read or persistence.
- F3 dispatch admission is also checked (`UNSIGNED_PROFILE_REQUIREMENTS`).
- The service refusal is `PROVIDER_UNAVAILABLE` with details:
  - `reason` and `reason_code`: `pending_signed_registry_admission`;
  - `resource_type`: `dashboard_metadata`;
  - `core_capability`: `core.dashboard_metadata_operation`;
  - `plan_created` false;
  - `provider_dispatch_occurred` false.
- Health under sequence 14 itemizes 26 compatible and 4 withheld profiles,
  and the withheld list includes `core.dashboard_metadata_operation` with
  `positive_authority_missing`. After seq 15 it is 30 compatible.

## 6. Profile shape for signing (seq 15)

| Field | Value |
|---|---|
| `capability_id` | `core.dashboard_metadata_operation` |
| `profile_id` | `core_dashboard_metadata_operation_v1` |
| `profile_version` | `1` |
| `adapter_id` | `compiled-core-dashboard-metadata-operation-v1` |
| `contract_fingerprint` | `sha256:62cd6c1453b0bc0b52c6e5f9775d1897294f31b62792dd9674a61b9e8a54cfaa` |

Required checks: `websocket_lovelace_dashboards_list`,
`websocket_lovelace_dashboards_update_by_id`,
`dashboards_list_metadata_readback_exact`. Provider boundary
`direct_ha_api`. The fingerprint is pinned by
`tests/test_seq15_contract_acceptance.py` (`FINGERPRINTS`). The never-sent
list in the contract names `lovelace/config/save` and `lovelace/config/delete`,
dashboard create and delete, the resource commands, `frontend/update_panel`
and `frontend/set_user_data`.

## 7. Evidence lane and what remains for November

`scripts/seq15_contract_acceptance.py` defines the `dashboard_metadata` lane
(scenarios on `engineering-acceptance`). It is dormant: `ci.yml` is
protected and is not changed by HAMCP-106. The lane is listed in
`WORKFLOW_PENDING_LANES`. Until `ci.yml` gives it an evidence path, its
`output_env` is unset and an enabled run fails closed.
`tests/test_alarmo_interval_observer.py` pins that the wired lanes match
`ci.yml` exactly and that the pending lane's variable is absent from it.

Remaining for the November seq-15 batch (protected; not authorized here):
1. `ci.yml` stage step (the seq-15 `printf` block): add
   `printf 'REAL_HA_DASHBOARD_METADATA_RESULT=%s\n' "$RUNNER_TEMP/seq15-dashboard-metadata-$HA_CONTRACT_LANE.json"`.
2. `ci.yml` upload step path list: add
   `${{ runner.temp }}/seq15-dashboard-metadata-${{ matrix.lane }}.json`.
3. Remove `"dashboard_metadata"` from `WORKFLOW_PENDING_LANES`. Update the
   expected `printf` list and upload paths in
   `tests/test_alarmo_interval_observer.py`.
4. Sign sequence 15 with the profile in section 6. Then run the lane
   against the disposable Core and verify the following, which are
   unverified offline:
   - Core's slug ids;
   - trailing-space storage;
   - icon clearing;
   - the lane's content-read stand-in (`DashboardContentRead`) against the
     real `lovelace/config` lease authorization.

## 8. Known limitations

- A dashboard whose configuration exceeds the response size limit has no
  content fingerprint, so its metadata cannot be changed (fails closed).
- Generic disclosure only; affected users are not counted (decision 3).
- Residual concurrency window between the final read and the write
  (section 3). The readback detects it, and it is never redispatched.
