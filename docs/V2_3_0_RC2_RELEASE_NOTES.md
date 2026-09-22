# Engineering 2.3.0-rc.2 release notes

Engineering 2.3.0-rc.2 corrects historical plan readability before stable 2.3.0.
It restores supported reads of one reviewed expired-helper writer family and
two rejected automation-planning writer families. Original terminal status,
policy and immutable hashes remain intact; no approval or execution authority
is revived, and reads do not migrate or rewrite the records.

The [correction contract](TERMINAL_HISTORY_PROJECTION_CORRECTION.md) defines the
exact accepted shapes. Damaged hashes, unsupported lifecycles, task/dispatch
evidence and persistence-unsafe proposals remain refused. Malformed historical
values stay bounded read failures rather than breaking unrelated history reads.
Compatibility accounting adds three profile keys to the existing count map with
`authorization_effect=none_projection_only`; compatible history and genuine
projection failures remain separate. Rejected planning remains rejected planning.

Typed fan/light/switch operations, current configuration approval contracts,
at-most-once dispatch, authoritative readback and recovery retain their rc.1
behavior. The reviewed inventory remains 78 tools: 53 static and 25 delegated
reads. Public tool schemas, provider routes, signed Core journals and controlled
dependencies are unchanged. The acceptance pairing remains Core 2026.9.3 with
exact ha-mcp 8.5.0 and 19 admitted Core profiles under valid signed authority.
Core 2026.9.2 retains its original 17-reference authority and historical typed
route. Installing RC2 neither updates Core nor activates trust.

Update the existing **HA MCP Engineering Server Beta** installation after
authorized publication and deployment. The slug `hass_mcp_engineering_beta`,
image `ghcr.io/jeter-1/hass-mcp-engineering-beta`, linux/amd64 and linux/arm64,
options, ports, ingress, persistent paths and single Nabu Casa connector remain
unchanged. Frozen v1.1.2 is not an Engineering recovery target.

**Recovery:** this correction introduces no persisted format or read-time
migration. Removing it restores the prior historical read limitation; it does
not repair or roll back records. A binary recovery still requires a verified
compatible artifact and retention of current dispatch identities, receipts and
holds. Beta.3 cannot read beta.4's newer Core-bound records; binary downgrade
alone cannot recover them. Never restore older execution state that loses a
possible dispatch.

Dependency coverage remains partial, including unavailable reliable script,
scene, group, template and dashboard configuration coverage. Dashboard writes
remain non-atomic against external editors, and HA readback is not independent
physical feedback. Earlier catalog, supplied-evidence, navigation, outage-startup
and backup-verification qualifications retain their original scope.

The [RC2 acceptance contract](V2_3_0_RC2_ACCEPTANCE.md) separates source/CI and
independent review from publication, installed-image binding and readback of the
three original records. No installed fix or completed acceptance is claimed by
these notes. No household device cycle is required for this read-only correction.
Stable 2.3.0 remains a subsequent reviewed release transition.
