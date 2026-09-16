# HA MCP Engineering Server

Engineering **2.3.0-beta.1** adds typed ordinary fan control through the existing
authenticated connector. This prerelease preserves received Host/Origin enforcement
and secret-path authentication.
It provides one public MCP endpoint through the existing Nabu Casa
connector, with a unified catalog.
Engineering selects reviewed, admitted ha-mcp or native providers internally.

The installation retains its technical **Beta** identity. Choose **HA MCP
Engineering Server Beta**, directory and slug `hass_mcp_engineering_beta`.
No second installation or migration is required. Historical v1.1.2 in
`hass_mcp_admin/` is frozen and operationally retired; it is neither the
current installation target nor a supported Engineering rollback.

Read the [2.3.0-beta.1 release notes](docs/V2_3_0_BETA1_RELEASE_NOTES.md) and
[2.3.0-beta.1 acceptance contract](docs/V2_3_0_BETA1_ACCEPTANCE.md). A source version does
not prove publication, deployment or installed acceptance.

## Install or update Engineering

Read the [inbound policy and migration requirements](docs/INBOUND_SECURITY.md)
before an authorized 2.3.0-beta.1 deployment. MCP aliases beyond the strict loopback
authority defaults and browser Origins need exact configuration. Explicit host
lists replace the defaults. Retain independent management access because invalid
options refuse startup; historical acceptance does not establish this boundary.

1. Use this repository in the Home Assistant add-on store:
   `https://github.com/jeter-1/hass-mcp-admin`.
2. Select **HA MCP Engineering Server Beta**. Update an existing Engineering
   installation in place after publication and authorized deployment. Do not
   install the historical v1 package or move its data into Engineering.
3. Reconcile `mcp_allowed_hosts` and `mcp_allowed_origins` for intended paths
   through the supported add-on interface. Preserve other connection and trust
   settings, keeping access secrets in the operator's private interface.
4. Use the existing public Engineering Nabu Casa connector. MCP remains on port
   `8100`; authenticated, admin-only **HA MCP Approval** ingress uses internal
   port `8110`. The approval panel is not a second MCP endpoint.
5. Verify the final installed artifact, reconnect the client and complete the
   bounded [acceptance checks](docs/V2_3_0_BETA1_ACCEPTANCE.md).

The image repository remains
`ghcr.io/jeter-1/hass-mcp-engineering-beta`, supporting `linux/amd64`,
`linux/arm64` for 2.3.0-beta.1. Published 2.2.0 retains its historical
`linux/arm/v7` image. Home Assistant has retired 32-bit armv7 support; Engineering
requires a supported 64-bit installation. This source change does not migrate or
uninstall an existing system. Existing options, persistent paths, stored formats,
ports, ingress and technical Beta identities are preserved. The existing header-policy
options retain strict admission defaults; preserve the intended path configuration.

The controlled build introduced in 2.2.1 pins the base image and complete runtime
wheel closure.
See [controlled build inputs](docs/BUILD_INPUTS.md) for architecture checks,
dependency updates, publication verification and their evidence limits.

The [private observer contract](docs/INBOUND_TOPOLOGY_CAPTURE.md) requires
separate source-bound arming, bounded collection and private evidence handling.
Installation does not authorize or start a capture, and observation does not
implement Host/Origin enforcement.

## Capabilities and compatibility

The fully healthy reviewed **ha-mcp 8.4.3** pairing exposes **52 static tools
plus 25 delegated reads, 77 total**, with all **17 Core capabilities**.
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
referencing existing compiled contracts. Installing 2.3.0-beta.1 does not configure
trust or admit an uncompiled release. Existing compiled pairings retain their
behavior.

The [Core registry contract](docs/CORE_RELEASE_REGISTRY.md) governs exact-version
review, separate Core trust, signatures, expiry, retained denials and authority
retirement. Signed data can select existing compiled contracts; incompatible
semantics require review and possibly code changes. Future Core and ha-mcp
versions receive no blanket compatibility from this milestone.

## Ordinary fan requests

`control_fan` supports exact `turn_on`, `turn_off` and `set_percentage` requests
for one fan under ordinary authenticated connector authorization. The assistant
generates and reuses the operation ID; no configuration plan or panel approval
is added for this typed action. Generic service forwarding stays closed.

The fan contract is limited to Core 2026.9.2 / ha-mcp 8.4.3 under current authority.
Inspect authoritative task state and exact state/percentage readback. An uncertain
action is reconciled read-only, never blindly retried; unresolved outcomes retain
protection for the affected fan. Verification reflects HA state, not independent
physical feedback, and consumer coverage remains incomplete. Restoration requires
a separate exact owner request. See [typed fan control](docs/TYPED_FAN_CONTROL.md).

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

- [2.2.0 notes](docs/V2_2_0_RELEASE_NOTES.md) and
  [2.2.0 acceptance](docs/V2_2_0_ACCEPTANCE.md)
- [RC9 notes](docs/V2_2_0_RC9_RELEASE_NOTES.md) and
  [RC9 acceptance](docs/V2_2_0_RC9_ACCEPTANCE.md)
- [Architecture history](V2_BETA_ARCHITECTURE.md)
- [Historical beta deployment guidance](docs/BETA_DEPLOYMENT.md)
- [Published releases](https://github.com/jeter-1/hass-mcp-admin/releases)

Historical counts, pairings, installation and rollback guidance describe their
original release and do not replace the current 2.3.0-beta.1 contract.
