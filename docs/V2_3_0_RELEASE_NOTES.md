# Engineering 2.3.0 release notes

Engineering 2.3.0 is the stable release of the accepted RC2 implementation.
This transition changes release identifiers and documentation only. It adds no
runtime behavior, public tool/schema, provider route, dependency, configuration
option or persisted format.

The Engineering server remains the single client-facing Home Assistant MCP
endpoint through the existing Nabu Casa connector. Reviewed ha-mcp capabilities
are internal providers; native Engineering capabilities supply deeper inspection,
analysis, governed implementation, verification and recovery.

## Preserved capabilities

- Exact fan, light and switch operations retain authenticated connector authority,
  exact targets, durable operation identities, at-most-once dispatch and HA state
  readback. Configuration operations retain their existing approval contracts.
- Reviewed Core 2026.9.3 signed applicability covers 19 capability profiles with
  exact ha-mcp 8.5.0 admission. Core 2026.9.2 retains its immutable 17-reference
  entry and historical typed route. Installation does not update Core, activate
  trust or admit future versions.
- The healthy reviewed catalog remains 78 tools: 53 static and 25 delegated reads.
  Provider attribution, held-tool exclusions and zero unreviewed fallback remain.
- RC2's historical projection correction preserves the original terminal status,
  policy and immutable hashes of reviewed expired-helper and failed automation
  planning records. It neither rewrites records nor revives approval or dispatch
  authority. Damaged or unsupported history remains refused.
- Protected automatic publication retains the original owner Ready decision,
  exact-head merge and verified publication handoff. Deployment remains separate.

## Same installation and recovery

Update the existing **HA MCP Engineering Server Beta** after publication and
authorized deployment. The technical name, slug `hass_mcp_engineering_beta`, image
`ghcr.io/jeter-1/hass-mcp-engineering-beta`, linux/amd64 and linux/arm64 packaging,
ports, ingress, options, Host/Origin policy, connector and persistent paths remain
unchanged. No second installation or data move is required. Frozen historical
v1.1.2 remains retired and is not an Engineering rollback target.

This release adds no storage migration. Preserve current execution IDs, dispatch
receipts, locks, holds and uncertain work. Binary recovery requires a verified
compatible artifact; it does not authorize restoration of older execution state.
Beta.3 cannot read beta.4's newer Core-bound records. Never erase knowledge of a
possible dispatch to recover an earlier binary.

## Acceptance and limits

The accepted RC2 source is
`271fa972d6f7c8e360b4fe8593379dbfdbef3376`. Its installed acceptance closed on
2026-09-22, with evidence attribution and prior limitations retained as described
in [2.3.0 acceptance](V2_3_0_ACCEPTANCE.md). RC2 observations remain bound to that
artifact and time; they do not establish publication or installation of 2.3.0.
The final stable candidate requires fresh validation, CI and independent review,
then separate publication verification and bounded installed acceptance.

Dependency coverage remains partial, including unavailable reliable script,
scene, group, template and dashboard configuration coverage. Dashboard writes
remain non-atomic against external editors, and HA readback is not independent
physical feedback. Earlier raw-catalog and supplied-evidence qualifications,
the unknown historical upgrade-authority exception cause, incomplete Android
navigation, cache-only outage startup and independent backup-content/restore
verification remain explicit. Stable status does not expand those claims.
