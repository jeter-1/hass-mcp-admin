# Beta 22 release notes

Version: `2.0.0-beta.22`

Beta 22 stabilizes the read-only `handoff_generation` capability without adding
or changing a public MCP tool.

- Corrected duplicate and contradictory dependency-index coverage in focused and
  incident handoffs.
- Shared dependency evidence now produces one normalized logical-source row.
- Successful partial dependency coverage no longer creates a synthetic provider
  failure; real distinct provider failures remain explicit and counted once.
- Expired, superseded, rolled-back, and terminal validation-only plans are
  retained history rather than active pending work, blockers, risks, or current
  authorization requirements.
- Active approval, apply, verification, rollback, and unresolved failure states
  retain their current lifecycle behavior.
- Resolved automation entity IDs now appear consistently in structured scope,
  Markdown scope, evidence context, and frozen cursor pages.
- `risk_count` now equals the number of handoff items in the `risks` section;
  severity totals remain separately available in `items_by_severity`.

No public tool was added. Registered tool count remains 38, canonical count
remains 25, existing schemas are unchanged, and the planned capability list
remains empty. No write capability was added. Production v1.1.2, slug
`hass_mcp_admin`, and port 8099 are unchanged.
