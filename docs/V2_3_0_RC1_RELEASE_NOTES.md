# Engineering 2.3.0-rc.1 release notes

Engineering 2.3.0-rc.1 is the feature-frozen candidate for stable 2.3.0. It carries
the existing typed fan, light and switch operations, signed Core applicability
and reviewed internal ha-mcp providers through the same single Engineering
connector. Compared with beta.4, no runtime behavior, public tool/schema,
configuration option, dependency or persisted format changes.

The release includes the reviewed production Core 2026.9.3 registry data and the
protected automatic-publication handoff and event-context corrections merged
after beta.4. Existing Core .3 applicability covers all 19 semantic profiles;
Core .2 retains its original immutable 17-reference authority and historical
typed route. Installed authority still requires valid configured signatures and
matching observed Core/provider identity. Installing this release does not
activate trust or update Core. Future releases receive no blanket admission.

The supported reviewed inventory remains 78 tools: 53 static and 25 delegated
reads. Provider selection remains internal, attributed and limited to admitted
contracts. Ordinary typed fan/power requests retain exact targets, authenticated
connector authority, at-most-once dispatch and authoritative readback. Existing
configuration operations retain their own approval contracts. Uncertain results
are reconciled using their original IDs; they never permit blind redispatch or
provider substitution.

Installation remains **HA MCP Engineering Server Beta**, slug
`hass_mcp_engineering_beta`, image
`ghcr.io/jeter-1/hass-mcp-engineering-beta`, linux/amd64 and linux/arm64. Ports,
ingress, persistent paths, Host/Origin policy and connector configuration remain
unchanged. Frozen historical v1.1.2 is not an Engineering recovery target.

Dependency coverage remains partial, including unavailable reliable script,
scene, group, template and dashboard configuration coverage. Dashboard updates
remain non-atomic against external editors; HA state readback does not establish
independent physical effects. Prior raw-catalog, supplied-evidence, navigation,
outage-startup and backup-verification qualifications retain their original scope.

**Recovery:** beta.3 cannot read beta.4's new Core-bound records. Binary downgrade
alone is insufficient. Preserve dispatch identities, current receipts, holds and
uncertain work; use a verified compatible reader and separately reviewed recovery
procedure. Never restore older execution state that loses a possible dispatch.

The [rc.1 acceptance contract](V2_3_0_RC1_ACCEPTANCE.md) separates candidate
Evidence/CI, first actual automatic publication, immutable artifact verification
and separately authorized installed acceptance. No passing deployment or household
result is asserted by these release notes. Existing functional evidence is carried
with provenance; no new device cycle is implied. Stable 2.3.0 follows RC acceptance
through its own reviewed version transition.
