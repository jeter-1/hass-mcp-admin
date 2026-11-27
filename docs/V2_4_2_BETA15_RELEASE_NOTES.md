# Engineering 2.4.2-beta.15 release notes

**Status: in progress.** Engineering 2.4.2-beta.15 is the 2.11.0 build. It
builds on 2.4.2-beta.14 (the 2.10.0 build, HAMCP-500, #522). Its content lands
through item pull requests into the `release/2.11.0` integration branch, and
the final release pull request completes these notes.

Josh approved the 2.11.0 plan on 2026-11-25 and its kickoff on 2026-11-27:
big answers come back whole, with the native search follow-ups and
correctness fixes.

Acceptance: [`V2_4_2_BETA15_ACCEPTANCE.md`](V2_4_2_BETA15_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## Current state

The version is materialized on `release/2.11.0`, created from `main` at
54627e35 (2.4.2-beta.14). Nothing is published or deployed.

## Items

An item that has not landed is not part of this release, and these notes
make no claim about its behavior. The final release pull request describes
each landed item's behavior from the landed source.

Planned:

| Item | Scope | State |
| --- | --- | --- |
| HAMCP-425, HAMCP-432 | One paging contract for list replies: a list cursor, including lists inside a bare-object reply | not landed |
| HAMCP-492 | Integrity analysis compact pages within a 24000 byte budget | not landed |
| HAMCP-359 | The reconciliation audit record stays within its bound | not landed |
| HAMCP-511, HAMCP-527, HAMCP-513, HAMCP-517 | `get_history_slices` reads history natively, pages, handles concurrent reads, names its refusals consistently and reconciles its upstream request count | not landed |
| HAMCP-273, HAMCP-291 | Large dashboards can be read back | not landed |
| HAMCP-444 | Dashboard analysis rule profile for the current frontend | not landed |
| HAMCP-477 | Dashboard-write sensitive-data guard | not landed |
| HAMCP-522, HAMCP-523, HAMCP-525, HAMCP-520 | Native search area modes, closed codes for skip notices, `body_numeric_query` reporting and engine cleanup | not landed |
| HAMCP-447 | Native search dashboard and helper identities | not landed |
| HAMCP-296, HAMCP-431, HAMCP-389, HAMCP-524 | Trace watch renewal, one shared connection, eviction and `skipped_polls` | not landed |
| HAMCP-510 | Fixed gateway readers without a parent request | not landed |
| HAMCP-471 | JSON exponent overflow is refused as non-finite | not landed |
| HAMCP-321, HAMCP-495 | Reliability analysis condition-stop step and batch partial reasons | not landed |
| HAMCP-512 | `ha_eval_template` completeness | not landed |
| HAMCP-514, HAMCP-515 | Provider named in provider-unavailable failures; consistent collision reporting | not landed |
| HAMCP-518 | A `get_audit_log` per-tool summary | not landed |
| HAMCP-378 | Created-object rollback plan form | not landed |
| HAMCP-394, HAMCP-429, HAMCP-486 | Risk elevation only where incomplete dependency evidence matters | not landed |
| HAMCP-445 | Scene list rows | not landed |
| HAMCP-519 | Read-only view of input helper stored settings | not landed |

Not in this build: the ha-mcp visibility filter for native search
(HAMCP-499, replaced by a native visibility policy planned for 2.12.0), the
upstream fingerprint difference HAMCP-516 (deferred until its cause is
confirmed), governed restart, reload and backup, secret-safe native bodies,
native flow helpers and governed edits for more helper types (HAMCP-346 is
design only).

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
