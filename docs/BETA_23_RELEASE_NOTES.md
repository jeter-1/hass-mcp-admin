# Beta 23 release notes

Version: `2.0.0-beta.23`

Beta 23 corrects global provider-routing failure accounting. The selected route
is now distinct from an attempted provider operation, and the shared metrics API
requires explicit dispatch provenance.

- Request validation no longer increments provider requests or failures.
- Cursor validation no longer increments provider requests or failures.
- Authentication and rate-limit rejection before dispatch do not affect provider
  counters.
- Local sanitized-snapshot continuation performs no provider work.
- Actual provider failures, invalid required responses, and timeouts remain
  attributable and count once per attempted operation.
- Successful partial provider coverage remains a successful, non-failing attempt;
  `partial_results` records the partial provider operation separately.
- Source failures remain distinct from provider failures.
- All Beta 22 handoff coverage, lifecycle, scope, risk, open-item, authorization,
  pagination, audit, and read-only corrections remain intact.
- No public MCP tool or schema changed. Registered tools remain 38, canonical
  tools remain 25, and the planned capability count remains zero.
- No write capability was added. Production v1.1.2 is unchanged.

## Accounting contract

`requests_by_provider` changes only when dispatch begins.
`successful_requests_by_provider` changes only when a dispatched operation
returns complete or usable partial evidence. `failures_by_provider` changes only
when a dispatched operation has an attributable failure. Selected provider
metadata, tool terminal status, assessment incompleteness, unsupported coverage,
dynamic uncertainty, and local cursor failures are insufficient.

Tool failures remain visible through the relevant tool counters,
`recent_error_counts`, cursor counters, transport status, and bounded audit.
`source_failures` counts actual evidence-source failures. These are separate from
the cumulative process-level provider counters.

## Deployed read-only acceptance

1. Call `server_info`, `list_capabilities`, and `get_server_health`. Verify
   `2.0.0-beta.23`, 38 registered/25 canonical tools, an empty planned list,
   connected HA, and unchanged handoff metadata. Capture provider, handoff,
   governance, retry, timeout, and audit baselines.
2. Generate a bounded `system_status` handoff with dependency and integrity
   enabled. Verify one usable partial dependency row, zero source failures, no
   synthetic provider failure, retained historical-plan semantics, and no
   mutation.
3. Generate a focused handoff for entity `climate.mudroom_thermostat` and internal
   automation ID `1782920111688`. Verify resolution to
   `automation.ha_critical_stale_data_monitor`, one dependency row, and provider
   deltas only for operations actually dispatched.
4. With a low limit, follow two signed cursor pages. Verify the same handoff ID,
   scope, totals, coverage, and provenance, with zero HA/provider/governance/index
   work and no provider-counter change.
5. Submit a focusless `focused_review`. Require `invalid_request`, zero HA and
   upstream work, unchanged Engineering request/success/failure counters, zero
   source failures, and request-validation tool/recent-error accounting.
6. Submit a tampered cursor and a cursor with `refresh_index=true`. Require
   fail-closed cursor errors, cursor-counter changes only, zero upstream work,
   unchanged provider/source failures, and no snapshot creation.
7. Run another invalid Engineering-native analysis request and one malformed
   transitional direct read. Both must leave their provider counters unchanged.
8. Do not disrupt HA or credentials to manufacture a provider failure. If no
   natural read-only failure exists, record live coverage unavailable and rely on
   automated real-failure and timeout tests.
9. Reconcile health: validation appears in recent errors, cursor failures remain
   cursor events, provider failures remain unchanged absent real attempted
   failures, terminal handoff aggregates count once, pages do not duplicate them,
   no prohibited fallback occurred, and governance plan count is unchanged.
10. Inspect bounded audit. Validation entries have no HA endpoint categories,
    raw cursor, Markdown, configuration, traces, secrets, tokens, or authenticated
    URLs.
11. Confirm no service, trigger, entity/configuration write, governance mutation,
    reload, restart, production access, or production modification occurred.

Connector recreation is not normally required because Beta 23 changes no public
tool or schema. Refresh the add-on repository and update only
`hass_mcp_engineering_beta` on port 8100.
