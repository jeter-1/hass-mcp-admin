# Engineering 2.4.2-beta.9 release notes

**Status: in progress.** Engineering 2.4.2-beta.9 is the 2.7.0 build. It
builds on 2.4.2-beta.8 (HAMCP-332, #438). Its content lands through item pull
requests into the `release/2.7.0` integration branch, and the final release
pull request completes these notes.

Josh decided on 2026-11-14 (relayed by the architect) that HAMCP-371 ships as
2.4.2-beta.9.

Acceptance: [`V2_4_2_BETA9_ACCEPTANCE.md`](V2_4_2_BETA9_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## Current state

The version is materialized at the base of `release/2.7.0` (`main` at
e741f2ed). Nothing is published or deployed.

## Items

None has landed yet. The final release pull request describes each item's
behavior from the landed source.

- **HAMCP-371** (in progress on its own branch). ha-mcp 9.2.0 admission, with
  the HAMCP-374 per-read real-output replay.

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
