# Engineering 2.4.1-beta.31 release notes

Engineering 2.4.1-beta.31 corrects `get_incident_logs` entry times
(HAMCP-220, break-fix). It follows 2.4.1-beta.30 (HAMCP-131) and includes it.

Supervisor 2026.10.3 renders the log line time in Home Assistant's local
time, with no offset. beta.30 read that time as UTC, so in a zone west of UTC
a recent window was reported as after the journal tail and returned nothing.

Findings and design:
[`HAMCP_220_INCIDENT_LOG_TIMESTAMPS.md`](HAMCP_220_INCIDENT_LOG_TIMESTAMPS.md).
Acceptance: [`V2_4_1_BETA31_ACCEPTANCE.md`](V2_4_1_BETA31_ACCEPTANCE.md).

## `get_incident_logs` time basis

- **Journal realtime first.** Each page's first entry takes its exact UTC
  `__REALTIME_TIMESTAMP` from the `X-First-Cursor` `t=` field. That entry
  also checks how the page's text was rendered, in Home Assistant's zone or
  in UTC (Supervisor 2026.09.1). If neither matches, the page fails closed
  as `timestamp_basis_mismatch`.
- **Text in Home Assistant's zone.** The other entries are read from their
  text in the rendering that matched, with DST rules from `zoneinfo`.
  Supervisor's offset form, `+HH:MM`, is read exactly.
- **Uncertain, not guessed.**
  - A local time that occurs twice (DST fall-back, for example 2026-11-01
    01:30 in America/Chicago) or never (spring-forward) gets `ts: null` and
    `ts_basis: uncertain`, with `timestamp_uncertain` listing every
    candidate.
  - If such an entry may fall on either side of a window edge, it is
    returned with `window_membership_uncertain` and the read is partial.
- **Zone failure.** The zone is the add-on's `TZ`, which Supervisor sets
  from Home Assistant's configuration at add-on start. If it is missing or
  invalid, the Core and `addon:self` sources become `unavailable`
  (`time_zone_unavailable`) with zero requests. UTC is never assumed.
- **Stated basis.**
  - Every entry has `ts_basis` and `time_zone`.
  - Every source has `timestamp_basis`.
  - The response has `time_basis`.
  - A page without a realtime is read in the zone and makes the read
    partial (`timestamp_basis_unverified`).

## Compatibility

- Existing fields keep their names, and new fields are additive. The output
  schema id stays `incident-logs-v1`.
- `ts` may now be `null` (uncertain entries only).
- Cursor format, budgets, authority reporting and the sanitizer exit are
  unchanged.
- `get_history_slices` is unchanged.

No tool, input schema, description, permission, route, add-on configuration,
workflow, signed registry entry, HOLD, support window, dependency or stable-v1
file changes. The catalog stays at **92** tools (93 with the optional YAML
reader).

## Known limitation

A Home Assistant time-zone change takes effect for this add-on at its next
restart. Until then, a mismatching page fails closed as
`timestamp_basis_mismatch`.

## Rollback

Reinstall the 2.4.1-beta.30 artifact; it restores the UTC misreading
described above. No stored format changes. Cursors are process-keyed and do
not survive a restart.
