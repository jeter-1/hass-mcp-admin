# HA MCP Engineering Server

Engineering **2.2.0** is the stable Engineering milestone. It provides one public
MCP endpoint through the existing Nabu Casa connector, with a unified catalog.
Engineering selects reviewed, admitted ha-mcp or native providers internally.

The installation retains its technical **Beta** identity. Choose **HA MCP
Engineering Server Beta**, directory and slug `hass_mcp_engineering_beta`.
No second installation or migration is required. Historical v1.1.2 in
`hass_mcp_admin/` is frozen and operationally retired; it is neither the
current installation target nor a supported Engineering rollback.

Read the [2.2.0 release notes](docs/V2_2_0_RELEASE_NOTES.md) and
[2.2.0 acceptance contract](docs/V2_2_0_ACCEPTANCE.md). A source version does
not prove publication, deployment or installed acceptance.

## Install or update Engineering

1. Use this repository in the Home Assistant add-on store:
   `https://github.com/jeter-1/hass-mcp-admin`.
2. Select **HA MCP Engineering Server Beta**. Update an existing Engineering
   installation in place after publication and authorized deployment. Do not
   install the historical v1 package or move its data into Engineering.
3. Retain existing connection and trust configuration. Configure a new
   installation through the supported add-on interface, keeping access secrets
   in the operator's private interface.
4. Use the existing public Engineering Nabu Casa connector. MCP remains on port
   `8100`; authenticated, admin-only **HA MCP Approval** ingress uses internal
   port `8110`. The approval panel is not a second MCP endpoint.
5. Verify the final installed artifact, reconnect the client and complete the
   bounded [acceptance checks](docs/V2_2_0_ACCEPTANCE.md).

The image repository remains
`ghcr.io/jeter-1/hass-mcp-engineering-beta`, supporting `linux/amd64`,
`linux/arm64` and `linux/arm/v7`. Options/defaults, persistent paths, stored
formats, ports, ingress and technical Beta identities are unchanged by 2.2.0.

## Capabilities and compatibility

The fully healthy reviewed **ha-mcp 8.4.3** pairing exposes **51 static tools
plus 25 delegated reads, 76 total**, with all **17 Core capabilities**.
Actual authority and provider admission determine availability. Fresh protocol
enumeration and descriptor comparison establish the catalog; a health count or
cached client inventory alone does not.
`ha_get_operation_status` remains held and absent from ordinary registration.

Engineering supports bounded inspection, dependency/reliability/integrity
analysis, reviewed reads and governed operations. Native capabilities supplement
upstream functionality where deeper Engineering semantics are required.
Provider unavailability grants no fallback authority or arbitrary forwarding.

Accepted RC9 evidence includes **Core 2026.9.2 / ha-mcp 8.4.3**.
Core 2026.9.2 requires separately configured, valid signed Core authority
referencing existing compiled contracts. Installing 2.2.0 does not configure
trust or admit an uncompiled release. Existing compiled pairings retain their
behavior.

The [Core registry contract](docs/CORE_RELEASE_REGISTRY.md) governs exact-version
review, separate Core trust, signatures, expiry, retained denials and authority
retirement. Signed data can select existing compiled contracts; incompatible
semantics require review and possibly code changes. Future Core and ha-mcp
versions receive no blanket compatibility from this milestone.

## Governed changes and evidence

Inspect, explain evidence and effects, prepare an exact plan, obtain its
authenticated approval, apply once, inspect execution, read back and verify.
`approve_change_plan` requests approval; it does not grant it. Approval
remains bound to current evidence and execution conditions.

After a timeout or incomplete response, reconcile the persisted task and
authoritative target state. Attempt count alone does not prove dispatch.
Do not blindly repeat a mutation, reuse a failed approval or change providers.
Dashboard saves remain non-atomic against external editors.

- [Change governance](docs/CHANGE_GOVERNANCE.md)
- [External approval](docs/EXTERNAL_APPROVAL.md)
- [Execution recovery](docs/GOVERNED_EXECUTION_RECOVERY.md)
- [Bounded response guidance](docs/TOKEN_EFFICIENCY.md)
- [Integrity analysis](docs/CONFIGURATION_INTEGRITY_ANALYSIS.md)
- [Automation reliability](docs/AUTOMATION_RELIABILITY_ANALYSIS.md)
- [Audit records](docs/AUDIT_LOG.md)

## Development and release

Follow the [Engineering README](hass_mcp_engineering_beta/README.md),
[docs/CODEX_WORKFLOW.md](docs/CODEX_WORKFLOW.md) and applicable instructions.
Resolve the exact release context before work:

```sh
python scripts/codex-context.py --format json
```

Materialize a release in its original PR through
`scripts/promote_next_release.py`; the temporary declaration is consumed.
Final clean-head Evidence and candidate CI remain separate from publication
and installed acceptance. A version-changing main merge can automatically
publish; the owner decision must cover that consequence. Deployment is separate.

## Historical references

Published artifacts and historical evidence remain immutable. RC9's accepted
runtime is preserved by 2.2.0; its observations retain their RC9 pairing/time.

- [RC9 notes](docs/V2_2_0_RC9_RELEASE_NOTES.md) and
  [RC9 acceptance](docs/V2_2_0_RC9_ACCEPTANCE.md)
- [Architecture history](V2_BETA_ARCHITECTURE.md)
- [Historical beta deployment guidance](docs/BETA_DEPLOYMENT.md)
- [Published releases](https://github.com/jeter-1/hass-mcp-admin/releases)

Historical counts, pairings, installation and rollback guidance describe their
original release and do not replace the current 2.2.0 contract.
