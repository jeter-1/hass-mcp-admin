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

None has landed yet. The final release pull request describes each item's
behavior from the landed source.

- **HAMCP-401 Phase 1, with HAMCP-400** (in progress on its own branch).
  Governed reload, app restart and Home Assistant restart on ha-mcp 9.2.0,
  and a held provider refused as `provider_held`.

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
