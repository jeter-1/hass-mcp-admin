# Beta 20 release notes

Version: `2.0.0-beta.20`

Beta 20 corrects the source-coverage contract used by the read-only
`incident_correlation` capability. A successfully built dependency index remains
usable evidence when known source types are unsupported. That condition is now
reported as partial coverage with `failed_items=0` and
`failure_category=null`, rather than as `provider_upstream_error`.

- Unsupported scripts, scenes, groups, templates, and dashboards remain explicit,
  bounded coverage limitations.
- Hypotheses distinguish actually missing or failed evidence from incomplete but
  usable evidence.
- Supporting dependency references remain attached when index coverage is partial.
- Actual provider errors, timeouts, and item-read failures retain distinct stable
  failure categories.
- Incident health `source_failures` and provider failure counters count actual
  failures only; partial unsupported coverage may still increment `partial_count`.
- Corrected coverage and hypothesis semantics are frozen into pagination snapshots;
  continuation still performs no upstream or index work.
- Signed analysis cursors now reject non-canonical Base64 encodings as tampering;
  valid cursor and pagination contracts are unchanged.
- No public MCP tool or schema changed. Registered tools remain 37 and canonical
  tools remain 25.
- No write capability was added. Production v1.1.2 remains unchanged, and
  `handoff_generation` remains planned.
