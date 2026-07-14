# Beta 24 release notes

Version: `2.0.0-beta.24`

Beta 24 is the final pre-RC safety and governance hardening release. It adds no
MCP tool or feature capability: the beta remains at 38 registered tools, 25
canonical tools, and zero planned capabilities. Production v1.1.2,
`hass_mcp_admin`, and port 8099 are unchanged.

## Corrections

- Top-level automation `id` is identity metadata, not behavioral
  configuration. Id-less governed proposals now verify against Home Assistant
  readback that contains the matching injected ID. A wrong proposed or returned
  ID remains an explicit `automation_id` verification failure.
- Automation normalization is now versioned. Proposed-config hashes, state
  fingerprints, and plan hashes use the Beta 24 rules. **Re-create any pending
  or approved automation change plans after upgrading to Beta 24.** Old terminal
  records remain readable; incompatible plans are never silently rehashed,
  rewritten, reapproved, or applied.
- `upsert_automation` remains registered with its existing schema for connector
  compatibility, but always fails closed with `provider_prohibited` and
  `governance_required` before provider or Home Assistant work. The only
  supported automation-write path is `create_change_plan` ->
  `approve_change_plan` -> `apply_change_plan`.
- A direct Home Assistant exception now requires a matching explicit read
  policy. Missing policy or access mismatch denies the operation.
- `cf-connecting-ip` is untrusted by default. It is accepted only when
  `trust_cf_connecting_ip` is enabled, the direct peer belongs to a configured
  `trusted_proxy_cidrs` network, and the header contains one valid IPv4 or IPv6
  address.
- Per-client and authentication-failure rate bucket stores now evict only the
  least-recently-used entry needed to stay within the bound; store pressure no
  longer resets every client's throttling state.
- A selected provider known to be unavailable before dispatch returns
  `provider_unavailable` with `upstream_attempted=false` without incrementing
  provider request, success, or failure counters.
- `get_audit_log` clamps the requested line count to 1 through 500. Zero and
  negative values cannot return the whole file.

Signed analytical cursors remain process-local, five-minute bounded snapshots.
They are not a general cache, must not be durable workflow state, and do not
survive add-on restart.

## Compatibility and safety

No public input schema changed. `call_service`, `delete_automation`, and
`reload_domain` fail closed on the Engineering server. Broader ordinary Home
Assistant execution belongs to the standard Home Assistant MCP integration
where an exact supported capability exists. Generated evidence,
recommendations, or handoffs are never authorization.

Unsupported inspection of scripts, scenes, groups, templates, and dashboards
remains explicit. System Log retention is bounded and unknown; correlation is
not proof of causation; generated handoffs are documentation, not approval.

## Post-deployment acceptance

Run these checks only against the beta connector after the user installs Beta
24. Do not restart Home Assistant, touch production, or execute a write without
separate explicit approval.

1. Call `server_info`, `list_capabilities`, and `get_server_health`; verify
   Beta 24, 38/25/0 catalog counts, HA connectivity, healthy governance storage,
   and capture provider and plan baselines.
2. Generate bounded system-status and focused handoffs. For the documented
   focused fixture, use `climate.mudroom_thermostat` and automation internal ID
   `1782920111688`; confirm the resolved entity is
   `automation.ha_critical_stale_data_monitor`. Verify one effective dependency
   row, partial usable coverage without synthetic failure, historical plans do
   not look active, and structured/Markdown scope and risk counts agree.
3. Follow two valid cursor pages and tamper with one cursor. Valid pages must
   preserve identity, timestamp, scope, totals, coverage, and provenance with
   zero HA/provider/index/evidence/regeneration work. The tampered cursor must
   return `invalid_cursor` and change only cursor-failure telemetry.
4. Submit a focusless focused handoff; verify field-level `invalid_request`, no
   provider/source-failure change, and one terminal recent-error increment.
5. If a safe read-only path selects the unavailable Standard provider, verify
   `provider_unavailable`, `upstream_attempted=false`, no provider counters, no
   fallback, and no HA call. Otherwise rely on automated coverage.
6. Test `upsert_automation` only with explicit user approval even though it must
   refuse. Use a harmless test identifier and confirm no read, write, provider
   dispatch, governance mutation, or payload disclosure.
7. Separately and explicitly approve any real governed description-only test.
   Preserve the original snapshot, omit top-level `id`, review and approve the
   exact immutable hash, apply, verify the matching HA-added ID and no
   `other:id`, repeat apply to prove `already_applied`, then separately request,
   approve, and verify rollback. Do not alter triggers, conditions, actions,
   enabled state, or physical behavior.
8. Inspect plans read-only. Pre-Beta-24 pending or approved records must not be
   silently migrated; recreate them. Historical terminal plans remain readable.
9. Confirm proxy trust is disabled and trusted ranges are empty by default. Do
   not perform denial-of-service or spoofing tests against the deployed add-on;
   automated tests are authoritative.
10. Call `get_audit_log` with default, 1, 500, and—where the connector allows—
    501, zero, and negative values. Verify clamping/rejection and redaction.
11. Reconcile final health and audit: only dispatched operations affect provider
    counters; validation, cursor, unavailable-pre-dispatch, and partial-coverage
    outcomes do not fabricate provider failures; no prohibited fallback or
    unauthorized mutation occurred.

The only permitted mutation in this procedure is the separately approved,
description-only governed apply and its separately approved rollback. Confirm
that production v1.1.2 was neither accessed nor modified.
