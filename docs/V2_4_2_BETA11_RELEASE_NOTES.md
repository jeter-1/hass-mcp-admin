# Engineering 2.4.2-beta.11 release notes

**Status: in progress.** Engineering 2.4.2-beta.11 is the 2.9.0 build. It
builds on 2.4.2-beta.10 (HAMCP-401, #452). Its content lands through item pull
requests into the `release/2.9.0` integration branch, and the final release
pull request completes these notes.

The architect's task of 2026-11-18 08:2x CST names 2.4.2-beta.11 for the
2.9.0 early start (HAMCP-436).

Acceptance: [`V2_4_2_BETA11_ACCEPTANCE.md`](V2_4_2_BETA11_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## Current state

The version is materialized at the base of `release/2.9.0` (`main` at
6449c7b3). Nothing is published or deployed.

## Items

None has landed yet. The final release pull request describes each item's
behavior from the landed source.

- **HAMCP-436** (in its draft item pull request into `release/2.9.0`; not
  landed). The E022 lifecycle catalog: governed reload, app restart and Home
  Assistant restart on ha-mcp 9.2.0 with the `ha_mcp_tools` component. Exact
  contracts are required only for the tools each operation dispatches and
  the reviewed versioned add-on tools. The reviewed tool name set stays a
  whole-catalog gate. Descriptor drift on any other reviewed tool is
  recorded in provider health (`catalog_admission`), not rejected. The
  backup provider applies the same rule and stays held on 9.2.0
  (HAMCP-381).

## Evidence

**Pending.** The final release pull request binds each landed item's
evidence and the single Evidence tier log of the final release head.

## Tool catalog

**Pending.** The final release pull request states the expected connector
tool counts from the landed source.

## Rollback

**Pending.** The final release pull request states the rollback target and
whether rolling back is safe, from the storage, provider and option changes
that actually landed.
