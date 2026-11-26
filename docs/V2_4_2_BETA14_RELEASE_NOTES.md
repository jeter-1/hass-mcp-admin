# Engineering 2.4.2-beta.14 release notes

**Status: in progress.** Engineering 2.4.2-beta.14 is the 2.10.0 build. It
builds on 2.4.2-beta.13 (the 2.9.1 build, HAMCP-455, #500). Its content lands
through item pull requests into the `release/2.10.0` integration branch, and
the final release pull request completes these notes.

Josh approved the 2.10.0 plan on 2026-11-25: native search is the anchor,
with must-carry and live-correctness fixes; object lifecycle moves to the
next release.

Acceptance: [`V2_4_2_BETA14_ACCEPTANCE.md`](V2_4_2_BETA14_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## Current state

The version is materialized on `release/2.10.0`, created from `main` at
f48c6fb9 (2.4.2-beta.13). Nothing is published or deployed.

## Items

An item that has not landed is not part of this release, and these notes
make no claim about its behavior. The final release pull request describes
each landed item's behavior from the landed source.

| Item | Scope | State |
| --- | --- | --- |
| HAMCP-500 | The 2.4.2-beta.14 declaration: the version materialized on `release/2.10.0`, with these in-progress notes, the acceptance contract and the CHANGELOG entry | landed (#504) |
| HAMCP-502 | `get_history` returned only the first 24 hours of a longer window and marked it complete; the incident activity reads had the same gap. Both now send an end time, so a window up to 168 hours is returned in full. `get_history` with `minimal=False` now returns attribute-only changes; the incident reliability row reads partial, with its limitations, when the reliability evidence is partial; `get_error_log` marks a full system log (50 entries) as possibly missing older entries | landed (#505) |
| HAMCP-462 | Core mirror pins refreshed to 2026.11.3, the signed trust-root seq 19 copied into `main`, and the support window rotated (2026.11.3 current, 2026.11.0 rollback) | landed (#508) |
| HAMCP-490, HAMCP-491 | `configuration_integrity_analysis`: dynamic references at one path that differ in evidence now get distinct `finding_id`s, so compact rows no longer repeat; `unresolved_dynamic_reference_count` stays unfiltered by `severities`, and `counter_semantics` and the guide now say so | landed (#510) |
| HAMCP-493 | `automation_reliability_analysis` and the batch: summary root causes now carry `affected_step` (from the trace evidence for a concurrency rejection), and a disabled automation's group reports `current_state` `disabled` | landed (#511) |
| HAMCP-503 | Core trace retention: `automation_reliability_analysis` reports a trace source whose Core ring may have evicted runs inside the lookback as partial (`trace.stored_traces`, default 5); trace watch rows count polls that may have missed evicted runs (in process, assumes 5) | landed (#509) |
| HAMCP-497, HAMCP-498 | `get_server_health` reports `server.build_sha` and `build_dirty`, as `server_info` does, and `release_registry` reports `last_refresh_at` (UTC start of the last refresh attempt) and `refresh_interval_seconds`. A Core-bound call refused while Engineering re-verifies Core after a restart or update now returns `CoreCapabilityUnavailable` with `retryable` true and `reason_code` `core_authority_recovering`, naming the recovery state and next attempt; an unsupported Core version or a missing capability stays non-retryable | landed (#512) |
| HAMCP-508 | Bounded reads say so: `list_automations`, `list_devices` and `list_entity_registry` add `returned`, `total` and `truncated` (default page unchanged); incident correlation marks history, logbook, trace-service and reliability-finding bounds partial with `items_withheld`, counts malformed rows, and counts omitted warnings and limitations; `entity_dependency_analysis` reports a capped indirect walk (`indirect_dependency_limit_exceeded`, `total_complete` false) and the full dynamic reference count with the withheld ones. Those keys are additive. **Breaking (SI-5):** `list_automation_traces` returns `{traces, returned, source_truncated, retention}` instead of a bare list; the headers are unchanged and `retention` says the list is Core's stored ring (default 5), so older runs are not listed | landed (#515) |
| HAMCP-505 | The Core-evidence workflow takes its pull request acceptance target from the support window's current Core (2026.11.3) instead of a fixed 2026.11.0. CI scripts and tests only; no runtime change | landed (#514) |
| HAMCP-481 W1 (built as HAMCP-501) | Native search (entities, automations and scripts; filters; `result_fields`; correct partial semantics): the scoring port, landed inert, so no tool imports it | landed (#506) |
| HAMCP-481 W2 | Native search: the fixed Core reads, landed inert | landed (#507) |
| HAMCP-481 W3 | Native search: the engine, landed inert | landed (#513) |
| HAMCP-481 W4 | Native search: the page readers | [fill: #516 state] |
| HAMCP-481 W6 | Native search: the `ha_search` cutover; the tool keeps its name | [fill: PR and state] |
| HAMCP-481 W7 | Native search: the design doc update (these release rows are HAMCP-509's) | [fill: PR and state] |

Not in this build:
- object lifecycle, response compaction, history paging, plan withdraw, list
  paging, trace-watch renewal and the dashboard items, planned for 2.11;
- the ha-mcp visibility filter for native search (HAMCP-499);
- the HAMCP-293 risk lowering (HAMCP-486);
- the HAMCP-474 D1 relabel (HAMCP-487);
- [fill: anything W4 to W7 defer].

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
