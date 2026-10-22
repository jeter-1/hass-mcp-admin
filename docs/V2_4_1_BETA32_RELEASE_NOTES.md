# Engineering 2.4.1-beta.32 release notes

Engineering 2.4.1-beta.32 corrects `get_incident_logs` entry times
(HAMCP-220, break-fix). It follows 2.4.1-beta.31 (HAMCP-069B) and includes it.

Supervisor 2026.10.3 renders the log line time in Home Assistant's local
time, with no offset. beta.30 and beta.31 read that time as UTC. In a zone
west of UTC, a recent window was therefore reported as after the journal
tail and returned nothing.

Findings, the owner's zone-source decision and the design are in
[`HAMCP_220_INCIDENT_LOG_TIMESTAMPS.md`](HAMCP_220_INCIDENT_LOG_TIMESTAMPS.md).
Acceptance: [`V2_4_1_BETA32_ACCEPTANCE.md`](V2_4_1_BETA32_ACCEPTANCE.md).

## `get_incident_logs` time basis

- **Home Assistant's zone, from Core.**
  - Each call that reads a journal reads Home Assistant's configured
    `time_zone` once, from Core's `GET /api/config` (the registered HAMCP-200
    `/config` route). It does so under its own `core.basic_rest_read` lease.
  - If that read is refused or fails, no journal is read. The sources are
    `unavailable` with `time_zone_unavailable`. The add-on `TZ` and UTC are
    never used instead.
  - **While Core is down, `get_incident_logs` therefore returns no journal
    entries.**
- **Fail closed on any zone disagreement.**
  - The add-on `TZ` is compared with Home Assistant's zone. If it names a
    zone whose offset differs at any minute of the window, every source is
    partial with `zone_mismatch` (`addon_time_zone_offset_differs`). A
    `TZ` that has the same offsets throughout the window passes.
  - Each page's first entry takes its exact UTC `__REALTIME_TIMESTAMP` from
    the `X-First-Cursor` `t=` field. That entry confirms the page's text
    was rendered in Home Assistant's zone (or in UTC, for Supervisor
    2026.09.1). If it was not, the source is partial with `zone_mismatch`
    (`first_entry_realtime_disagrees`).
  - None of these reads is ever reported complete, even with zero entries.
- **Text in Home Assistant's zone.** The other entries are read from their
  text in the rendering that matched, with DST rules from `zoneinfo`.
  Supervisor's offset form, `+HH:MM`, is read exactly.
- **DST resolved, or disclosed.**
  - A local time that occurs twice (DST fall-back) or never
    (spring-forward) is resolved to its exact journal time when the request
    budget allows. One single-entry read returns it as a page's first
    entry.
  - When the budget does not allow it, the entry keeps `ts: null` and
    `ts_basis: uncertain`, with `timestamp_uncertain` listing every
    candidate.
  - Around such an entry, each source keeps its journal order, and the
    affected rows are marked `ordering_uncertain`. This covers rows from
    every source up to the entry's latest candidate, on later pages too.
    The read is then partial (`ordering_uncertain:<source>`).
  - The tool never claims an order between the candidates.
- **Stated basis.**
  - Every entry has `ts_basis` and `time_zone`.
  - Every source has `timestamp_basis`, which includes `resolved_entries`
    and `unresolved_entries`.
  - The response has `time_basis`: Home Assistant's zone and its status, the
    add-on `TZ` and its status, and the uncertain and ordering-uncertain
    counts.
  - A page without a realtime is read in the zone and makes the read
    partial (`timestamp_basis_unverified`).

## Compatibility

- Existing fields keep their names, and new fields are additive. The output
  schema id stays `incident-logs-v1`.
- `ts` may now be `null`, for unresolved entries only.
- The signed cursor payload gains the ordering bound `o`. Cursors are
  process-keyed and do not survive a restart.
- A cursor whose anchor text Supervisor has rendered again in another zone
  fails as `journal_continuity_broken`.
- Resolution reads come out of the existing request budget.
- Budgets, authority reporting and the sanitizer exit are unchanged.
- `get_history_slices` is unchanged.

**Protected runtime files.** Josh authorized the Core read and the route and
registration files it needs:
- `incident_logs/zone.py` (new);
- `incident_logs/service.py`, `contracts.py` and `timebasis.py`;
- `application.py` (wiring);
- `ha_core_readmission/routes.py`. `get_incident_logs` is now
  `CORE_INDEPENDENT_DOWNSTREAM_AUTHORITY`.

No tool, input schema, description, permission, Supervisor route, add-on
configuration, workflow, signed registry entry, HOLD, support window,
dependency or stable-v1 file changes. The catalog stays at **93** tools (94
with the optional YAML reader).

## Rollback

Reinstall the 2.4.1-beta.31 artifact. It restores the UTC misreading
described above. No stored format changes.
