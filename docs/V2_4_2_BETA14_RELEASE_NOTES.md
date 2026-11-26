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
| HAMCP-481 W4 | Native search: the sub-call page readers, landed inert; W6 fixed their page check, which refused every page under the live unified descriptor | landed (#516) |
| HAMCP-481 W6 | Native search: the `ha_search` cutover; the tool keeps its name and arguments, plus one appended `search_types` sentence. The delegated 9.2.0 `ha_search` is internal-only (no `ha_mcp__ha_search`) and serves only the fixed sub-call pages under its own Core requirement. Automation, script and scene body matches come from ha-mcp's scrubbed sub-call (S1); a numeric-like query or an unproven scrub withholds them. Pages step down 150, 30, 6, 1 rows when too large | landed (#517) |
| HAMCP-481 W6b | Native search: component-route parity. Entity rows carry the composed device and entity `name`; the scored texts add the name, aliases, labels, device texts, area and floor; the component tier scorer (100, 80, ratio of at least 70) replaces BM25 and the typo fallback; the state filter runs before paging. Supersedes #518 (synthetic test fixtures) | landed (#519) |
| HAMCP-481 W7 | Native search: the design doc update. Revision 5 of `docs/HAMCP_481_NATIVE_SEARCH_DESIGN.md` marks native search implemented in 2.10.0 and adds the implementation record (§10). Docs only; these release rows are HAMCP-509's | landed (#520) |

Not in this build:
- object lifecycle, response compaction, history paging, plan withdraw, list
  paging, trace-watch renewal and the dashboard items, planned for 2.11;
- the ha-mcp visibility filter for native search (HAMCP-499);
- the HAMCP-293 risk lowering (HAMCP-486);
- the HAMCP-474 D1 relabel (HAMCP-487);
- native search: dashboard and helper identity (HAMCP-447);
- a fail-closed refusal for the other fixed readers with no parent request
  (HAMCP-510);
- an engine cleanup after W6b (HAMCP-520);
- the disclosed body-match residuals, for 2.11: non-scalar and bool secrets,
  YAML-mode dashboards, and no inf/nan arm in the numeric trigger;
- the helper sub-call still opens and aborts options flows upstream, as
  before (HAMCP-233);
- the later items the design doc keeps: native helper reads that replace
  the helper sub-call (HAMCP-484), an `entity` value for `search_types`
  (HAMCP-485), and `include_config` bodies, withheld until a redaction
  design exists (HAMCP-454). W7 (#520) is docs only and adds no deferral.

### Native search parity

Copied verbatim from the W6 pull request (#517), measured against the live
delegated component route (unified descriptor c1ca76bd). W6b (#519) then
closes X12; its parity list follows the block.

```
Kept:
- Full 9.2.0 schema (per_domain_limit, group_by_domain, fields, result_fields, config_time_budget, include_hidden default true), plus one appended sentence.
- X1 eligibility: domain/area/state intent skips config search unless search_types is explicit.
- X5 union area match; X6 unknown area gives success with 0.
- X10 count is the sum; limit is per surface. X15 ties sort by entity_id.
- X3/X4 Row 25 projection for dashboard and helper rows.
- Automation, script and scene body matching (match_in_config) is the delegated route's own (S1 b+), scrubbed upstream, except under the F1 trigger or an unproven scrub (see Changed).
- Audit status: a partial reply is audited partial, as on the delegated route (restored in the correction batch).
- result_fields enrichment (area, floor, labels, aliases, is_group, member_entity_ids).

Improved:
- X9: closed Engineering warnings; upstream_warnings_replaced is 0.
- X1 now warns config_skipped.
- X11: offset and limit are always present.
- X7: completeness reflects real partial_reasons; a partial or failed sub-call affects only its own bucket.
- Full description (no truncation).
- Combined next_offset is the minimum of the surfaces.
- match_in_references comes from Core search/related (entity_id-shaped query); the component route hard-codes it false. A graph failure gives not_searched graph_unavailable.
- A long-named row no longer fails the whole type: the page-size ladder re-reads only its span at a smaller size.

Changed (with reason):
- X7: no VISIBLE_SCOPE caveat; HAMCP-499's visibility filter is not ported; hidden only via registry hidden_by.
- X12: the composed device+entity name is not served (warning); W6b adds it.
- An unusable floor/area registry gives source_unavailable.
- The invalid-argument envelope is the native SearchError.
- Arbitrary delegated projection is unreachable.
- config_time_budget is validated but not forwarded to pages; each sub-call scan has its own 60 s deadline (S1, as decided).
- Automation, script and scene matches need ha-mcp admitted; without it the bucket is source_unavailable subcall_not_admitted (S1 b+: bodies only from the scrubbed route).
- An upstream partial page (legacy: a secret-bearing file gives per-id 500s) is not_searched upstream_partial for that bucket only; the upstream partial_reason text and hint are not carried.
- A row larger than the reply bound even at page size 1, or a bucket past 300 page calls, is <type>_subcall_incomplete for that bucket.
- F1 trigger (Codex r1 F1; Josh's A'+ as relayed): for a numeric-like query (exact: only 0-9 . + - e : t and spaces; fuzzy: that, or any query token with a digit), automation, script, scene and helper sub-call rows keep only a name rescore and graph references. match_in_config is false, rows with no name match are dropped, and the bucket is not_searched body_numeric_query. A legitimate numeric body match (for example 10:00 or 75) is lost; the name match and graph references remain.
- F1 scrub check: when a one-row helper probe before or after the scans does not show a clean scrub, every body type in the call is treated the same way (body_scrub_degraded or body_scrub_unverified).
- The audit status of the native invalid-argument path is failure / validation_failure.
```

W6b (#519), a summary of #519's P1-P9 table, measured against the same live
component route (ha-mcp 9.2.0 `ha_mcp_tools` search). Each item is kept:
native now does what live does.
- P1: every entity row carries `name`, the composed device and entity name
  (Core 2026.11.3's rule); the composed-name warning is removed (X12).
- P2: exact mode matches the entity ID, friendly name, composed name,
  aliases, labels, device texts, area and floor.
- P3: the fuzzy scorer is the component's tier scorer (100, 80, or a ratio
  of at least 70). BM25 and the typo fallback are retired.
- P4: `match_type` comes from the composed and friendly names, with
  `alias_match` and no `typo_fallback`.
- P5: the domain, hidden and state filters run before paging in every mode;
  the page-only state warning is removed.
- P6: an area-filtered query scores like any query.
- P7: device texts add the main device's manufacturer and model.
- P8: storage-helper names and bodies score on the component scorer;
  automation, script and scene bodies stay on W6's sub-call. P9 (exact
  configuration match) is unchanged.
- Every entity search now reads the device registry, and every query also
  reads the area, floor and label registries; exact mode reads aliases. No
  new source is read.

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
