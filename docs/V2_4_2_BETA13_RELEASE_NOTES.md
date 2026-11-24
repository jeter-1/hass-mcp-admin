# Engineering 2.4.2-beta.13 release notes

**Status: in progress.** Engineering 2.4.2-beta.13 is the 2.9.1 build. It
builds on 2.4.2-beta.12 (the 2.9.0.1 hotfix, HAMCP-470, #488). Its content
lands through item pull requests into the `release/2.9.1` integration branch,
and the final release pull request completes these notes.

The architect's block of 2026-11-23 names 2.4.2-beta.13 for the 2.9.1 build.
The same day, the 2.9.1 scope was re-scoped to audit-driven read fixes taken
from tool usage and a house audit. No other 2.9.0 hotfix is planned.

Acceptance: [`V2_4_2_BETA13_ACCEPTANCE.md`](V2_4_2_BETA13_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## Current state

The version is materialized on `release/2.9.1` at its catch-up with `main`
(b4dd3da3, 2.4.2-beta.12). Nothing is published or deployed.

## Items

An item that has not landed is not part of this release, and these notes
make no claim about its behavior. The final release pull request describes
each landed item's behavior from the landed source.

Landed, tooling and tests only (no runtime change):

- **Version-gate lint** (HAMCP-456, #487). `tests/test_version_gate_lint.py`
  fails on an exact Core or ha-mcp version literal in the Engineering runtime
  that is outside `ci/support-window.yaml` and not listed in
  `ci/version-gate-allowlist.json`.
- **Release tooling** (HAMCP-458, #486). The release scripts refuse a
  guessed base and check the test venv, and a readiness sweep is added.

Planned, in this order:

| Item | Scope | State |
| --- | --- | --- |
| HAMCP-472 | `ha_search` with a domain filter returns no entities where `search_entities` finds them, and every search reports `partial` with one replaced warning. A read-only diagnosis comes first; a fix follows if the cause is Engineering's | not landed |
| Native search | Design only in this build (entities, automations and scripts; filters; `result_fields`; correct partial semantics). The build is planned for the next release | not landed |
| HAMCP-473 | `configuration_integrity_analysis` paging and filters | not landed |
| HAMCP-474 | Analysis coverage gaps: large automations, scripts and templates, the default dashboard, and the system-log partial reason | not landed |
| HAMCP-475 | `get_audit_log` smaller pages with a cursor | not landed |
| HAMCP-476 | Per-automation run count over a time window (a new native read) | not landed |
| HAMCP-293 | Automation plan risk classified from the change, not the automation's existing actions | not landed |
| HAMCP-292 | Sensitive-data false positive on plan description wording | not landed |

Not in this build: native lifecycle and identity (Phase 1), the restart
verification fixes (HAMCP-479, HAMCP-480), the dashboard rule profile
(HAMCP-444), the protocol ADR (HAMCP-384) and the Core mirror refresh
(HAMCP-462). No lifecycle work is planned for this build.

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
