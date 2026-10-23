# HAMCP-220: Incident-Log Entry Times

`get_incident_logs` (HAMCP-131, 2.4.1-beta.30) read the Supervisor verbose
log prefix as UTC. Newer Supervisor writes that prefix in Home Assistant's
local time with no offset. So every journal time was shifted by the zone
offset, and a recent window was reported as after the tail.

This document has three parts: the defect, the verification, and the fix.

## Defect (beta.30, 2026-10-22 about 12:34Z)

- `get_incident_logs` for `core` and `addon:self` reported
  `latest_available: 2026-10-22T07:34:41.230Z` and `window_after_tail`. It
  returned 0 entries for an 11:30Z-12:34Z window.
- `get_core_log_history`, at the same moment, showed
  `2026-10-22 07:34:40.940 INFO ...` with no offset, received at 12:34:48Z.
  So the text is America/Chicago local time (UTC-5 in October).
- `get_history_slices` is not affected. Delegated history times arrive
  UTC-offset (`HAMCP_131_INCIDENT_LOGS.md`, History continuation).

Only the incident-log time basis changes.

## Part 1: Verification

### Pins

| Component | Version | Notes |
|---|---|---|
| Home Assistant Supervisor | `2026.09.1` (40e3ee76), the HAMCP-131 pin; `2026.10.3` read for the change | |
| systemd `journal-gatewayd` and `sd-journal` | `v257.9` | |
| Home Assistant Core | `2026.10.1`; `dev` read for an unreleased change | |

The live system rendered local time, so it ran a Supervisor at or after
2026.10.x, not the 2026.09.1 pin.

### How Supervisor renders the time

- **2026.09.1.** `journal_verbose_formatter` renders `__REALTIME_TIMESTAMP`
  in UTC (`supervisor/utils/systemd_journal.py` L56-61:
  `datetime.fromtimestamp(int(...) / 1e6, UTC).isoformat(sep=" ",
  timespec="milliseconds")`, then the offset is cut).
- **2026.10.3.** #7372 (9493d3a7) changed this; #7365 (941bad7a) added the
  `tz` parameter. It now renders in a time zone passed in by the caller,
  as naive text:
  - `_format_timestamp` is at L49-55:
    `datetime.fromtimestamp(int(ts) / 1e6, tz).replace(tzinfo=None)
    .isoformat(sep=" ", timespec="milliseconds")`.
  - `journal_verbose_formatter` uses it at L76-80.
  - `api/host.py` L398-399 passes `tz=self.sys_timezone_tzinfo`, which is
    Home Assistant's configured zone.
- **Unreleased.** acf85382 (#7397) appends `+HH:MM`.

### Q1: Can the log endpoints return each entry's `__REALTIME_TIMESTAMP` without a permission change?

**Not per entry. The first entry of every page, yes.**

- The handler accepts only `text/plain`, `text/x-log` or `*/*`, and any
  other `Accept` returns 400 (`api/host.py` L317-327 at 2026.09.1). The
  journal JSON form (`LogFormat.JOURNAL`) is used only between Supervisor
  and gatewayd (L357-358). It is never returned to the caller.
- The `Range` header is forwarded unchanged (L352-353). The response header
  `X-First-Cursor` is the first returned entry's journal cursor (L369-370
  at 2026.09.1; L398-403 at 2026.10.3).
- systemd v257.9 `sd_journal_get_cursor` writes that cursor as
  `s=...;i=...;b=...;m=...;t=<realtime>;x=...` (`sd-journal.c` L1252-1258).
  The `t=` field is the entry's `__REALTIME_TIMESTAMP` in hex microseconds,
  UTC.

So every page carries one exact UTC realtime, for its first entry. That
needs no new permission, role, route or header.

Engineering's forward page already starts with the anchor entry, which is
re-read to check continuity. So that exact time is the anchor's, and it
calibrates the page.

A per-entry realtime would need one request per entry. That would exceed
the HAMCP-131 request budget, so it is not done.

### Q2: What are the exact text formats, and where does Home Assistant's zone come from?

**Journal prefix.** Each line starts
`YYYY-MM-DD HH:MM:SS.mmm <hostname> <identifier>[<pid>]: <message>`, with
the time as follows:

| Supervisor | Time zone | Offset | Precision |
|---|---|---|---|
| 2026.09.1 | UTC | none | milliseconds, truncated |
| 2026.10.3 | Home Assistant's configured zone | none | milliseconds; the float `/1e6` can round sub-millisecond values |
| unreleased #7397 | Home Assistant's configured zone | `+HH:MM` | milliseconds |

**Core message text.** Core's own message starts
`%(asctime)s.%(msecs)03d LEVEL (thread) [logger] message`
(`homeassistant/bootstrap.py` L587, `const.py` L1065 at 2026.10.1). The
time is in the Core container's `TZ`. Core `dev` 8ddc465d0f (#186660) adds
an offset. Engineering reads only the level from Core's text; the time comes
from the journal prefix.

**Zone source.**
- Supervisor's zone is `config.timezone`. When that is unset, it is the host
  zone, and when that is unknown, `"UTC"` (`coresys.py` L183-197 and
  `config.py` L78-99 at 2026.10.3).
- Core pushes its configured `time_zone` to Supervisor
  (`homeassistant/components/hassio/__init__.py` L482-503).

**Runtime changes.**
- The Home Assistant zone can change at runtime: a Core configuration update
  (`core_config.py` L723-729 and L745-771) pushes the new value. Supervisor
  then renders new log text in it at once.
- Supervisor sets each add-on container's `TZ` to `sys_timezone` when it
  creates the container (`docker/app.py` L237, `docker/const.py` L165 at
  2026.10.3).
- An add-on start always recreates the container:
  - `DockerInterface._run` stops the old container (L542-555);
  - that removes the container (L569-573);
  - `DockerApp.run` passes `environment` (L693-722).

  So the add-on's `TZ` is the value Supervisor held at the add-on's last
  start.

**Zone data.** The pinned base image (`python:3.12-slim`, digest in
`hass_mcp_engineering_beta/Dockerfile`) ships the IANA zone database, so
`zoneinfo` resolves zone names.

### Owner decision (review 1, H1): zone source

The first candidate read the zone from the add-on's `TZ`. Review 1 found
that wrong after a Home Assistant zone change: `TZ` keeps the zone Supervisor
held when the add-on last started (the Chicago/Panama reproduction). Josh
authorized reading Home Assistant's configured zone from Core's
`/api/config` (read-only), through the registered HAMCP-200 `/config` route,
including the protected route and registration files. This supersedes the
`TZ` decision.

- **The zone.** `incident_logs/zone.py` (new) makes a `GET /api/config`
  before the journal reads of each call that reads a journal, through
  `HomeAssistantRestClient`, and one more after them (review 2, below). It
  reads `time_zone`, which Core owns and may change at runtime.
- **Authority.** `get_incident_logs` acquires no gateway Core lease, so the
  route can be reached while Core is down. The zone reader therefore
  acquires, commits and revalidates its own `core.basic_rest_read` lease.
  It chains any prior dispatch authorizer and restores the caller's
  authorizer and error code afterwards. `routes.py` now classifies the tool
  as `CORE_INDEPENDENT_DOWNSTREAM_AUTHORITY`, not
  `CORE_INDEPENDENT_SUPERVISOR_ONLY`.
- **Failure.** If the lease is refused, or the read errors, times out
  (5 s) or returns a malformed payload, the zone is `unavailable`. Every
  readable source is then `unavailable` with `time_zone_unavailable`, zero
  journal requests, and a partial read. The add-on `TZ` and UTC are never
  substituted. **Consequence:** while Core is down, `get_incident_logs`
  reads no journal.
- **The add-on `TZ` is a cross-check only.** When it names a different zone
  whose UTC offset differs from Home Assistant's at any minute of the window,
  every source fails closed. Each gets `timestamp_basis_mismatch` and
  `zone_mismatch.reason: addon_time_zone_offset_differs`, with
  `offsets_first_differ_at`; zones that agree over the window
  (`same_offsets_in_window`) pass. A missing or invalid `TZ` is disclosed as
  `addon_time_zone_unavailable`, and the read is partial.
- **The first-entry check stays as confirmation.** If the page's
  `X-First-Cursor` realtime disagrees with the text read in Home Assistant's
  zone (Supervisor still rendering an older zone, for example), the source is
  partial with `zone_mismatch.reason: first_entry_realtime_disagrees`.
  A single-entry resolution read that disagrees gives
  `entry_realtime_disagrees`.
- **Runtime zone change.** The zone is read again on every call, so a
  continuation after a change is read in the new zone. If the new zone and
  the add-on `TZ` disagree, the continuation is refused as above. A cursor
  whose anchor entry's text was rendered again in another zone fails as
  `journal_continuity_broken`, because its digest covers the raw text.

### Owner decision (review 2, H1 residual): zone bound across the call

Review 2 found a remaining race. If Home Assistant's zone changes after the
`/api/config` read and before the journal is read, Supervisor renders the
page in the new zone. When the page's first entry has the same UTC offset in
both zones, the first-entry check passes, the other entries are read in the
old zone, and an empty window can be reported complete (the reviewer's
`ZoneRaceProbe`: Chicago to Panama, the 08:10Z entry omitted as complete).
Josh decided to fix it, not accept it as a residual: the zone is bound
across the call by revalidation.

- **Second read.** After every journal page of the call has been read, and
  before any time, window or completeness result is published,
  `_bind_zone` reads `/api/config` again. It uses the same zone reader, the
  same registered route and its own `core.basic_rest_read` lease (acquired,
  committed, revalidated before dispatch and after the response, finished).
  A call that read no journal makes no second read.
- **Zone changed.** If the second zone differs from the first, every source
  that read the journal on this call is withheld: no entries, no
  `earliest_available` or `latest_available`, status `partial`, reason and
  `zone_unbound` `zone_changed_during_read`, and the omission
  `zone_changed_during_read:<source>`. Its request count is kept. A
  continued source resumes on the page where this call started it (its
  incoming cursor anchor); a fresh source locates again. No window or
  completeness claim is made, so an empty window is never complete.
- **Second read failed.** A refused lease, an error, a timeout or a
  malformed payload gives the same result with
  `zone_unverifiable_after_read`.
- **Disclosure.** `time_basis` reports `time_zone_reads: 2`,
  `time_zone_after_read` and `time_zone_after_read_status` (`not_read`,
  `agrees`, `zone_changed_during_read`, `zone_unverifiable_after_read`).
  `bounds` reports `max_time_zone_reads: 2` and
  `time_zone_read_deadline_seconds: 5.0`. `limitations` states the check.
- **Residual.** A change that is reverted between the two reads (A to B to
  A) is not seen by this check. The first-entry realtime check still
  applies to each page.
- **Reviewer's probe.** `ZoneRaceProbe` is committed as
  `tests/test_hamcp220_review2_zone_race_probe.py`. By Josh's decision, its
  only change is the lease-cycle assertion, from one cycle to two, for the
  required second read; every other line is byte-identical to the bundle's
  `zone_race_probe.py`. The unchanged probe fails only on that line, and its
  completeness assertion passes.

## Part 2: Fix

### Time basis

1. **The journal realtime UTC is preferred.**
   - Each page's first entry takes its exact time from the `X-First-Cursor`
     `t=` field (`ts_basis: journal_realtime_utc`).
   - That entry also calibrates the page. Each rendering hypothesis (Home
     Assistant's zone from `/api/config`, and UTC for Supervisor 2026.09.1)
     survives only if it maps the first entry's text to its realtime within
     1 ms.
   - If none survives, the page fails closed as `timestamp_basis_mismatch`.
2. **The other entries are read from their text, in the surviving
   rendering** (`text_local_tz`, with `time_zone`).
   - Supervisor's offset form is read exactly (`text_explicit_offset`) and
     is checked against the realtime too.
   - A page mixing the two forms fails as `timestamp_form_mixed`.
3. **Uncertain times are resolved, or disclosed (review 1, M1).**
   - A local time that occurs twice (DST fall-back), never occurs
     (spring-forward), or on which the surviving renderings disagree, is
     resolved exactly when the budget allows. One single-entry read at
     (page cursor, index) returns that entry as a page's first entry, with
     its journal realtime. The entry's digest and one of its candidates
     must match, or the source fails closed.
   - Resolution reads use the shared request budget. One forward page is
     kept in reserve for each open source that has not read a page yet.
     They are counted in `timestamp_basis.resolved_entries` and
     `unresolved_entries`. An uncertain tail entry is resolved too, so that
     "after the tail" is decided on its exact time.
   - An entry left unresolved gets `ts: null`, `ts_basis: uncertain` and
     `timestamp_uncertain: {reason, candidates}`.
   - **Ordering.** Entries are merged by their source's running maximum
     time, so each source keeps its journal order. An unresolved entry, an
     entry that precedes an earlier, later-timed entry of its source, and
     every row of any source up to the latest candidate of an emitted
     unresolved entry are all marked `ordering_uncertain`. That last bound
     is carried to the next page in the signed cursor (`o`), so later pages
     never claim an order around the span either. Any such row makes the
     read partial, with `ordering_uncertain:<source>`.
     Earliest-candidate ordering is never asserted, within a page or across
     pages. The running maximum is also each source's resume position in
     the cursor (`t`), so a later page never starts a source below a point
     it has already merged past, and every page advances.
   - "Before the window", "after the tail" and the retention edge are
     decided by the latest candidate. "End reached" is decided by the
     earliest.
4. **Window edges.**
   - An uncertain entry with a candidate inside the window is returned.
   - If not every candidate is inside, it is marked
     `window_membership_uncertain`, and the read is partial with
     `timestamp_uncertain_at_window_edge:<source>`.
5. **No realtime.** If the cursor has no `t=` field, the text is read in
   Home Assistant's zone only. The page counts as unverified and the read is
   partial (`timestamp_basis_unverified:<source>`).
6. **Zone failure.** See the owner decision above: an unreadable Home
   Assistant zone reads no journal and is never replaced by the add-on `TZ`
   or UTC. An unreadable zone or any zone disagreement never yields a
   complete read, including one that returns zero entries.

### Output

The time fields are additive. The schema id stays `incident-logs-v1`, but
`ts` is now nullable. That nullable `ts` is a review decision.

- **Entries** gain:
  - `ts_basis` and `time_zone`;
  - `timestamp_uncertain` when uncertain;
  - `window_membership_uncertain` when the entry is at a window edge;
  - `ordering_uncertain` when its order is not established.
- **Source rows** gain:
  - `timestamp_basis` (`verified_pages`, `unverified_pages`,
    `text_rendering`, `resolved_entries`, `unresolved_entries`);
  - `timestamp_uncertain_at_window_edge` and `ordering_uncertain` (counts);
  - `timestamp_uncertain_bounds` when an `earliest_available` or
    `latest_available` is uncertain (that bound is then `null`);
  - `zone_mismatch` (`reason`, `home_assistant_time_zone`,
    `addon_time_zone`, and `offsets_first_differ_at` for an add-on
    difference) when the source failed closed on a zone disagreement;
  - `zone_unbound` when the source was withheld by the second zone read.
- **Bounds** gain `max_time_zone_reads` and
  `time_zone_read_deadline_seconds`.
- **The envelope** gains `time_basis`:
  - `preferred`, plus `time_zone`, `time_zone_source`
    (`home_assistant_api_config_time_zone`), `time_zone_status`
    (`not_read`, `loaded`, `unavailable`) and `time_zone_reads`;
  - `time_zone_after_read` and `time_zone_after_read_status` (`not_read`,
    `agrees`, `zone_changed_during_read`, `zone_unverifiable_after_read`);
  - `addon_time_zone`, `addon_time_zone_source` and
    `addon_time_zone_status` (`not_read`, `agrees`,
    `same_offsets_in_window`, `differs`, `unavailable`);
  - `uncertain_entries` and `ordering_uncertain_entries`.

  It also gains the pin `supervisor_text_timestamps`.
- **New omissions:** `time_zone_unavailable`, `addon_time_zone_unavailable`,
  `zone_mismatch:<source>`, `ordering_uncertain:<source>`,
  `zone_changed_during_read:<source>` and
  `zone_unverifiable_after_read:<source>`.

### What changes and what does not

- **Changes (review 1):**
  - one Core `GET /api/config` before and one after the journal reads of a
    call that reads a journal, each under its own lease (review 2);
  - the tool's Core-independence basis;
  - the `o` field in the signed cursor payload;
  - resolution reads inside the existing request budget.
- **Unchanged:**
  - cursor signing and continuity digests;
  - the locate and bisection algorithm;
  - the request and byte bounds;
  - authority reporting;
  - sanitization at the single exit;
  - the Supervisor routes;
  - permissions and add-on configuration.

## Tests and mutations

- `tests/test_hamcp220_incident_log_timestamps.py` uses real-format text from
  the 2026.10.3 formatter, the 2026.09.1 formatter and the offset form, with
  real-shape cursors. It covers:
  - Chicago, UTC, Asia/Kolkata and Asia/Tokyo;
  - the live reproduction;
  - DST fall-back and spring-forward, both resolved and beyond the budget;
  - a multi-page fall-back with two sources, and a longer one through the
    whole repeated hour that drains every entry in journal order with
    every page advancing;
  - resolution reads keeping one request for the other source's first
    page;
  - ordering uncertainty carried to the next page;
  - window edges in local time and UTC;
  - Core and `addon:self` ordering;
  - zone read failure (refused, malformed, raising, unreadable);
  - the Home Assistant zone used when the add-on `TZ` is stale;
  - zones that agree except across a DST transition (Denver/Phoenix);
  - a Home Assistant zone change at runtime;
  - review 2: a zone change between pages, during a continuation (resumes
    its cursor) and across DST (an empty window never complete); the second
    read unreadable (absent, invalid, raising); one read when no journal
    is read; a failed source before a zone change;
  - a one-hour rendering error refused;
  - a cursor without realtime.

  `ZoneReaderTests` drive the real `HomeAssistantRestClient` against a
  loopback `/api/config`. They cover:
  - the reader's own committed lease;
  - no request when the lease is refused or uncommitted;
  - revalidation failure, errors and the bounded timeout;
  - authorizer chaining and restoration, and the preserved error code.

  `ZoneRebindReaderCases` drive both reads through the real client: two
  lease cycles when the zone is unchanged, and the second read refused by
  its lease or failing upstream (500, missing or invalid `time_zone`) as
  `zone_unverifiable_after_read`.
- `tests/test_hamcp220_review2_zone_race_probe.py` is the review 2
  reviewer's `ZoneRaceProbe`, with only its lease-cycle count amended (see
  the review 2 decision).
- `tests/test_hamcp220_review1_probes.py` and
  `tests/test_hamcp220_review1_tool_local_probe.py` are the review 1
  reviewer's probes, unchanged.
- `tests/test_hamcp131_incident_logs.py` runs every HAMCP-131 test twice: once
  with the 2026.09.1 UTC rendering, and once with 2026.10.3 local rendering
  in America/Chicago (`IncidentLogLocalRenderTests`). It also checks:
  - the zone reader is wired in;
  - a refused Core lease reads no zone and no journal.
- `docs/evidence/hamcp220-mutations/` holds the mutation evidence:
  - text parsed as UTC;
  - an ambiguous or nonexistent time resolved silently;
  - zone fallback to UTC;
  - calibration bypassed;
  - Home Assistant's zone bypassed (`H1*`), including the add-on `TZ` used
    when it differs and the reviewer's widened tolerance;
  - ambiguous-span ordering asserted (`M1*`), including earliest-candidate
    ordering across ambiguous pages. Within one merge, an earliest-candidate
    key yields the same order as the running maximum, because a source's
    entries are taken in journal order. Across pages, the cursor then resumes
    a source below its merge position, and the read stops advancing: pages
    return no entries but keep a cursor. The long fall-back test fails on
    that;
  - the zone not bound across the call (`Z*`, review 2): the post-read
    revalidation removed (`Z1`), a changed or unreadable second zone
    accepted, a withheld source still publishing, a continued source losing
    its cursor, only sources with entries withheld, the omission or the
    partial status dropped;
  - the HAMCP-131 set, with the fragments this change moved retargeted.
