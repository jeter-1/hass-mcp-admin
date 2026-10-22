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

### Decision for review: zone source

The task asks for Home Assistant's zone. The fix reads it from the add-on's
`TZ`, which Supervisor sets, and not from Core `/api/config`. There are two
reasons:
- `get_incident_logs` is declared Core-independent (`routes.py` L227, a
  protected file).
- A new Supervisor or Core route would need the protected HAMCP-200 route
  registry (`paths.py`).

The cost: after a Home Assistant zone change, until this add-on restarts,
the text no longer matches the add-on zone. Calibration then fails that page
closed as `timestamp_basis_mismatch`; it never reads it in the wrong zone.
If Josh prefers `/api/config` or `/info`, that is a separately scoped change
to the protected files.

## Part 2: Fix

### Time basis

1. **The journal realtime UTC is preferred.**
   - Each page's first entry takes its exact time from the `X-First-Cursor`
     `t=` field (`ts_basis: journal_realtime_utc`).
   - That entry also calibrates the page. Each rendering hypothesis (Home
     Assistant's zone, and UTC for Supervisor 2026.09.1) survives only if it
     maps the first entry's text to its realtime within 1 ms.
   - If none survives, the page fails closed as `timestamp_basis_mismatch`.
2. **The other entries are read from their text, in the surviving
   rendering** (`text_local_tz`, with `time_zone`).
   - Supervisor's offset form is read exactly (`text_explicit_offset`) and
     is checked against the realtime too.
   - A page mixing the two forms fails as `timestamp_form_mixed`.
3. **Uncertain times are never guessed.**
   - A local time that occurs twice (DST fall-back), never occurs
     (spring-forward), or on which the surviving renderings disagree, gets
     `ts: null` and `ts_basis: uncertain`.
   - It also gets `timestamp_uncertain: {reason, candidates}`, keeping every
     candidate instant.
   - Ordering uses the earliest candidate.
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
6. **Zone failure.** A missing or invalid `TZ` makes every readable source
   `unavailable` with `time_zone_unavailable`, zero requests, and a partial
   read. UTC is never assumed.

### Output

The time fields are additive. The schema id stays `incident-logs-v1`, but
`ts` is now nullable. That nullable `ts` is a review decision.

- **Entries** gain:
  - `ts_basis` and `time_zone`;
  - `timestamp_uncertain` when uncertain;
  - `window_membership_uncertain` when the entry is at a window edge.
- **Source rows** gain:
  - `timestamp_basis` (`verified_pages`, `unverified_pages`,
    `text_rendering`);
  - `timestamp_uncertain_at_window_edge`;
  - `timestamp_uncertain_bounds` when an `earliest_available` or
    `latest_available` is uncertain (that bound is then `null`).
- **The envelope** gains `time_basis` (`preferred`, `time_zone`,
  `time_zone_source`, `time_zone_status`, `uncertain_entries`) and the pin
  `supervisor_text_timestamps`.

### What does not change

Cursor format and signing, continuity digests, the locate and bisection
algorithm, request and byte budgets, authority reporting, sanitization at the
single exit, the routes, permissions and add-on configuration.

## Tests and mutations

- `tests/test_hamcp220_incident_log_timestamps.py` uses real-format text from
  the 2026.10.3 formatter, the 2026.09.1 formatter and the offset form, with
  real-shape cursors. It covers:
  - Chicago, UTC, Asia/Kolkata and Asia/Tokyo;
  - the live reproduction;
  - DST fall-back and spring-forward;
  - window edges in local time and UTC;
  - Core and `addon:self` ordering;
  - zone read failure;
  - a stale zone;
  - a cursor without realtime.
- `tests/test_hamcp131_incident_logs.py` runs every HAMCP-131 test twice: once
  with the 2026.09.1 UTC rendering, and once with 2026.10.3 local rendering
  in America/Chicago (`IncidentLogLocalRenderTests`).
- `docs/evidence/hamcp220-mutations/` holds the mutation evidence:
  - text parsed as UTC;
  - an ambiguous or nonexistent time resolved silently;
  - zone fallback to UTC;
  - calibration bypassed;
  - the HAMCP-131 set, with the five fragments this change moved retargeted.
