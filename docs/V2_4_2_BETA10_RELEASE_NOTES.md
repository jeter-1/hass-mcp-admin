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

Each item is listed when its pull request lands in `release/2.8.0`. An item
that has not landed is not part of this release.

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

Not yet landed (no claim is made about their behavior):
- HAMCP-401 Phase 1, with HAMCP-400 (#445);
- HAMCP-410 (#446);
- HAMCP-411, HAMCP-412, HAMCP-413, HAMCP-408, HAMCP-409 and HAMCP-414
  (#447);
- the optional HAMCP-417 and HAMCP-419.

## Evidence

**Pending.** The final release pull request binds each landed item's
evidence and the single Evidence tier log of the final release head.

## Tool catalog

**Pending.** The final release pull request states the expected connector
tool counts from the landed source.

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
- **Engineering.** **Pending** until every item has landed. The final
  release pull request states the target and whether in-place downgrade is
  safe.
