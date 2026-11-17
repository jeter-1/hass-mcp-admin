# Engineering 2.4.2-beta.10 release notes

**Status: in progress.** Engineering 2.4.2-beta.10 is the 2.8.0 build. It
builds on 2.4.2-beta.9 (HAMCP-371, #444). Its content lands through item pull
requests into the `release/2.8.0` integration branch, and the final release
pull request completes these notes.

The architect's task of 2026-11-16 18:34 CST names 2.4.2-beta.10 for
HAMCP-401 Phase 1.

Acceptance: [`V2_4_2_BETA10_ACCEPTANCE.md`](V2_4_2_BETA10_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## Current state

The version is materialized at the base of `release/2.8.0` (`main` at
02b8f013). Nothing is published or deployed.

## Items

Every item has landed in `release/2.8.0` and is listed by its pull request.

- **HAMCP-401 Phase 1, with HAMCP-400** (#445, reviewed head ae9c637b,
  merged as 6587849d). Governed lifecycle on ha-mcp 9.2.0.
  - **Admitted on 9.2.0:** the governed reload of automations, scripts,
    `input_boolean` and `input_number`; the governed app (add-on) restart;
    and the governed Home Assistant restart. Each keeps the existing plan,
    exact-hash approval, readback and recovery gates, and dispatches at most
    once. Backup stays held on 9.2.0.
  - **Renamed upstream tools.** On 9.2.0 the add-on restart dispatches
    `ha_manage_app` and reads `ha_get_app`; other releases keep
    `ha_manage_addon` and `ha_get_addon`. Upstream's renamed-tool redirect is
    never relied on. A 9.2.0 catalog carrying only the old names is refused
    `required_tool_missing`.
  - **Home Assistant restart verification.** The restart is `verified` only
    when the dispatch is confirmed and Engineering's own Core probe observes
    the outage. A "restart scheduled" reply with no observed outage stays
    unverified and is never redispatched. `safe_mode` is never sent; reload
    never sends `entry_id`.
  - **HAMCP-400: `provider_held`.** A held provider disposition now fails
    before dispatch as `provider_held`, and health names the selected
    registry entry and the observed upstream version.
    `upstream_version_mismatch` now means only a version with no reviewed
    registry row. Public error codes are unchanged: lifecycle maps to
    `operational_contract_mismatch` and backup to
    `backup_provider_unavailable`.
  - No public schema or tool registration changes.

- **HAMCP-415 / HAMCP-416** (#448, reviewed head 5a39a919, merged as
  7aa5e0cf). Governed-change reliability.
  - **HAMCP-415 (E043): plans survive an identical Core restart.** A Core
    connection retirement still withdraws Core authority at once. When Core
    comes back with the same capability set and the same semantic
    fingerprints, approved plans stay valid. The Engineering log records
    `core_authority_restored` with `reason: core_authority_identical` and
    `plans_preserved: true`. A changed component, a version or registry
    retirement, an incompatible complete observation or outage-window
    overflow still voids plans, and the record names the reason
    (`core_authority_changed` with `changed_components`, or
    `core_plan_window_capacity`). A session-only refresh is not a
    retirement and is recorded as `core_authority_generation_refreshed`.
  - **HAMCP-416 (E044): rollback plans are replanned once terminal.**
    `rollback_change` returns an existing rollback plan only while it is
    live: approvable, or its task unsettled or held for manual review. A
    terminal one is replanned from fresh evidence. Every result carries
    `rollback_plan_resolution`: `existing_live_plan_returned` or
    `fresh_plan_created`. A replacement also carries
    `replaced_rollback_plan_id` and `replaced_rollback_plan_status`.
  - No persisted format, public input schema or tool registration changes.

- **HAMCP-410 (E042)** (#446, reviewed head fe93a18b, merged as
  bcf42e52). ha-mcp 9.2.0 key-set maps.
  - A bulk `ha_get_state` read now returns `states` keyed by entity ID.
    Before the fix the keys were withheld, so `states` came back `{}`
    while `metadata.completeness` read `complete`.
  - `ha_get_entity_exposure` list mode now returns `exposed_entities`
    keyed by entity ID, with each assistant flag.
  - If withholding empties every row of a non-empty collection, the read
    carries the `engineering_notes` entry `COLLECTION_WITHHELD` and is
    `partial`. An emptied collection no longer reads as complete.
  - No persisted format, public input schema or tool registration changes.

- **HAMCP-411 bundle** (#447, reviewed head d19aac9b, merged as 8f7e2a70).
  Truthfulness fixes.
  - **HAMCP-411 (E041): bounded `get_history`.** A single-entity history over
    the size budget returns a time-ordered prefix of whole rows, marked
    `partial`, with `truncated`, `has_more`, `returned_states` and
    `total_states`, a `cursor`, and a `get_history_slices` continuation.
    Before, the rows were cut and the result read complete. A size-bounded
    result is no longer reported `complete`.
  - **HAMCP-412 (E027): `control_device_configuration` error codes.** The
    public `error_code` names the refusal category, using existing codes
    only. Unlisted categories stay `invalid_request`.
  - **HAMCP-413 (E046): concurrent reads are retryable.** A concurrent
    `get_history_slices`, `get_incident_logs` or Core-log read returns
    `rate_limit_exceeded` with `retryable: true`.
  - **HAMCP-408 (E051): unpublished delegated reads.** A reviewed ha-mcp
    read that is not currently published is refused
    `upstream_not_admitted` (no delegated reads admitted) or
    `delegated_tool_not_admitted` (others admitted), both
    `provider_unavailable` and not retryable. Unreviewed names keep
    `CoreRequirementUnmapped`.
  - **HAMCP-409: stale upstream observation.** `upstream_read_gateway`
    health reports `upstream_observation_status` (`not_observed`, `current`
    or `stale`), the stale fields and since when.
  - **HAMCP-414:** `provider_routing.standard_ha_mcp_delegation` reads
    `available` while delegated ha-mcp reads are admitted.
  - No public input schema or tool registration changes.

- **HAMCP-417 (E049) and HAMCP-419 (E052)** (#450, reviewed head 40b1b5ef,
  merged as 3fb1dd83).
  - **E049: `created_identity` after readback.** `get_execution_task` shows,
    for each child, `created_identity_capture` and `created_identity`
    (`resource_type`, `resource_target`, `storage_object_id`, `entity_id`,
    `registry_entry_id`, `registry_unique_id`). `get_change_plan` lists
    them under `execution_task.created_identities`. Read-only: no Home
    Assistant read and no persisted record change.
  - **E052: automation `initial_state`.** Governed automation create and
    update plans accept a boolean `initial_state`, verified by exact
    read-back. Any other value is refused with `automation initial_state
    must be a boolean` before any Home Assistant call.

- **HAMCP-418 (E048)** (#451, reviewed head 20e69176, merged as fc552ab0).
  First page of an oversize list.
  - An oversize list response now returns whole leading rows in upstream
    order instead of zero rows. `response_completeness` carries
    `truncated: true`, `completeness: partial`, `projection:
    list_first_page`, `list_path`, `returned_count`, `omitted_count`, and a
    notice to narrow the query. `total_count` appears only when the
    response itself declares one.
  - Rows are never cut. Non-list responses, failures, governed receipts and
    `get_logbook` keep the existing projection.
  - No cursor, input schema or tool registration change. A list cursor is
    filed for 2.9.0 (HAMCP-425).

## Evidence

**Pending.** The final release pull request binds each landed item's
evidence and the single Evidence tier log of the final release head.

## Tool catalog

Only CI-observed values are listed. All are from run 44196696582, the I6
pull request run at 61d24d15. No CI run exists on the `release/2.8.0` branch
itself. 61d24d15 contains the release head fc552ab0 and differs from it only
in these two documents, so every runtime, script and test file it tested is
the release head's.

| Lane | Engineering | Delegated | Total | Upstream catalog | Source |
| --- | --- | --- | --- | --- | --- |
| ha-mcp 8.5.0 | 86 | 27 | 113 | 77 tools, fingerprint `0d7ecdb7f58b9b3f92fe05e0f3afbd213da6d5841263219d8c12c3dea1d77845` | job 134440196463 `exact-image-read-gateway`, artifact `exact-image-read-gateway-8.5.0`, `exact-image-read-gateway-result.json` |
| ha-mcp 9.2.0 | not recorded | 27 | not recorded | fingerprint `0f1a2c75a766a111a83053733c6803ca14d60357137f4ecc7bc7978061982147` | job 134440196627 `Exact-image admission 91_only amd64`, artifact `exact-image-admission-cases-91_only-amd64`, `91_parity_read_gateway_acceptance.json` and `91_filesystem_disabled_variant.json`; also job 134440196566 `Exact-image admission side_by_side arm64`, `sbs_stage_b2_91_strict_full_useful.json` |

- **Unchanged from beta.9.** Every value above equals the beta.9 record (run
  43960418854). No landed item states a tool registration change.
- **9.2.0 strict admission.** 82 advertised, 77 reviewed exact; no extras,
  missing or duplicates (job 134440196627,
  `91_strict_admission_positive.json`, Engineering 2.4.2-beta.10, selected
  entry `ha-mcp-v9.2.0-9de5bf6c`).
- **9.2.0 fingerprint and the optional YAML reader.** The delegated count
  and fingerprint are the same with `ha_config_get_yaml` exposed and
  without it.
- **CI did not record the 9.2.0 Engineering count or connector total;
  installed acceptance section 4 step 3 records them.**

Clients should refresh their tool list after deploying.

## Deploy

See [the acceptance contract](V2_4_2_BETA10_ACCEPTANCE.md), "Deploy". It
includes the ha-mcp 9.x step the beta.9 notes left out: on ha-mcp 9.x,
`upstream_dashboard_mcp_url` must end with exactly `/catalog/full`. Without
it Engineering refuses the 9.x server (`upstream_catalog_mode_unadmitted`)
and every delegated read is unavailable.

## Rollback

See [the acceptance contract](V2_4_2_BETA10_ACCEPTANCE.md), "Rollback". In
short:
- **ha-mcp pair.** Rolling the pair back to 8.5.0 also restores the
  `upstream_dashboard_mcp_url` value recorded before the 9.x change, without
  the `/catalog/full` suffix. With the suffix left in place, 8.5.0 answers
  404 at initialize and the delegated surface stays unavailable (fail-closed)
  until the suffix is removed.
- **Engineering.** Recovery is 2.4.2-beta.9 (tag `v2.4.2-beta.9`, commit
  02b8f013), by restoring the full Home Assistant backup taken right before
  deploy. Export `get_audit_log` before any restore. In-place downgrade from
  beta.10 to beta.9 is not supported and is unproven. CI's rollback image is
  2.4.2-beta.7 and is not the recovery target (HAMCP-405). No landed item
  states a persisted-format change.
- **Is rolling back safe?** Restoring the pre-deploy full backup is the
  supported, safe path: it returns Engineering, its storage and Home
  Assistant together to the backed-up state, and loses every change made
  since. Rolling Engineering back in place, keeping beta.10 storage, is not
  proven safe and is not supported.
