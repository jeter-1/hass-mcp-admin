# Engineering 2.4.2-beta.8 release notes

**Status: in progress.** Engineering 2.4.2-beta.8 is the 2.6.0 build. It
builds on 2.4.2-beta.7 (HAMCP-331, #383). Its content lands through item pull
requests into the `release/2.6.0` integration branch, and the final release
pull request completes these notes.

Josh chose the version 2.4.2-beta.8 on 2026-11-09 (HAMCP-354). The release
transition refuses `2.4.2-beta.7 -> 2.6.0-beta.1`.

Acceptance: [`V2_4_2_BETA8_ACCEPTANCE.md`](V2_4_2_BETA8_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## Current state

The version is materialized on `release/2.6.0` in the catch-up that merges
`main` through #413 (HAMCP-354). Nothing is published or deployed.

## Items

Landed in `release/2.6.0`, each through its own reviewed pull request. The
final release pull request describes each item's behavior from the landed
source.

- **HAMCP-149** (#396). Offline approval-completion fixture; tests and docs
  only.
- **HAMCP-104 Slice A** (#398). Precise stale-state pre-dispatch refusal
  reasons.
- **HAMCP-294** (#397). Bounded logbook window and entity/name filter.
- **HAMCP-267** (#400). Integrity, incident and handoff consume the
  scene/group/dashboard reference store.
- **HAMCP-296** (#401). Trace watch lifecycle.
- **HAMCP-229** (#402). Bounded automation reliability batch analysis.
- **HAMCP-143** (#412). Bounded refusal diagnostics for dashboard compiler
  limits.
- **HAMCP-337 / HAMCP-338** (#411). Entities-card rows classified by domain;
  E031 lock-tile risk fix.

The 2.5.0 build (2.4.2-beta.7) is included through #410 and HAMCP-354.

## Tool catalog

**Pending.** The final release pull request states the expected connector
tool counts from the landed source.

## Rollback

**Pending.** The final release pull request states whether rolling back to
2.4.2-beta.7 is safe, from the storage, provider and option changes that
actually landed.
