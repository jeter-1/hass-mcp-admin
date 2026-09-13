# Engineering 2.2.0 release notes

Engineering 2.2.0 is the stable Engineering milestone, preserving the accepted
RC9 implementation on the existing technical Beta installation path. This is a
release/documentation transition, not a runtime migration or new feature release.

Preparation base: `34e5600963666cbd4c5cc33de2138e2d9bd6294a`.
Accepted RC9 runtime/publication source:
`e96cf9337fa7033dce3f1935c055a22a06184def`, tree
`d22d0f19df60777babb292895f969cc824cec8aa`.
The intervening base change published signed Core-registry data and its bound
review evidence. Both files remain unchanged.

## Stable milestone, same installation

Use **HA MCP Engineering Server Beta**, directory/slug
`hass_mcp_engineering_beta`, image repository
`ghcr.io/jeter-1/hass-mcp-engineering-beta`. MCP port 8100 and the admin-only
approval ingress on internal port 8110 retain their identities. Options/defaults,
connections, persistent paths, stored formats and internal Beta identities do
not change. Update the same add-on after authorized publication/deployment;
no second installation or data move is required.

Historical stable-v1 remains **1.1.2**, frozen under `hass_mcp_admin/`.
It is operationally retired and is neither the current installation target nor
an Engineering rollback. Stable Engineering does not move into the v1 package.

## Preserved capability and boundaries

Engineering remains the unified public Nabu Casa endpoint, selecting admitted
ha-mcp or native providers internally. The healthy reviewed **ha-mcp 8.4.3**
pairing requires **17 Core capabilities** and exposes **51 static plus
25 delegated tools, 76 total**. `ha_get_operation_status` remains held and
absent from ordinary registration. Provider unavailability grants no fallback.

RC9's semantic continuity, manager-owned dependency builds, registry/device
validation, bounded reconciliation responses, integrity deduplication and
execution recovery are preserved. Public schemas, registration, routing,
admission, permissions, dependencies, trust configuration/data, persistence
formats and workflows are unchanged. Dependency soft/hard TTLs remain
600/3600 seconds with a 300-second cooperative deadline, not a remote-abort
or exactly-once remote execution guarantee.

Current targets/evidence, exact plans/hashes, authenticated approvals, authority,
ownership, durable intent and authoritative verification remain required.
Attempt counts alone do not prove dispatch. Uncertain execution must be
reconciled rather than blindly repeated. Dashboard saves remain non-atomic
against external editors.

## Exact compatibility

Accepted RC9 evidence includes **Core 2026.9.2 / ha-mcp 8.4.3**.
This uncompiled Core release requires separately configured, valid signed Core
authority selecting existing reviewed contracts. Installing 2.2.0 alone does
not establish that trust. Existing compiled pairings retain their behavior.

The [Core registry contract](CORE_RELEASE_REGISTRY.md) preserves separate Core
trust, signatures, identity checks, expiry, retained denials and retirement.
Reviewed signed data selects existing compiled contracts for an exact release;
unknown or incompatible contracts require review and possibly code changes.
Future Core or ha-mcp compatibility is not implied. No ha-mcp upgrade is included.

## Evidence and remaining verification

Inspected retained RC9 evidence covers source/CI/publication, installed-image
binding and complete public catalogs, signed-registry activation, Core 2026.9.2
reads and semantic authority, natural dependency replacement, read regressions,
governed helper/dashboard execution with exact restoration, and bounded restart
recovery/settlement. These remain observations of RC9 source
`e96cf9337fa7033dce3f1935c055a22a06184def` with their original pairing/time,
not tests of a published or installed 2.2.0 artifact.

Notification clearing was operator-reported. Complete Android approval navigation,
cache-only startup during an outage and independent backup-content verification
were not established. Preserve incomplete consumer coverage and historical
reporting/failure evidence rather than inferring an untested guarantee.

The final 2.2.0 candidate requires clean-head Evidence, exact-head CI including
architecture builds and the actual pinned Core 2026.9.2/ha-mcp 8.4.3 lane, and
bounded independent release-delta review. Record actual commands, counts, skips
and tested identities. Historical RC9 CI cannot stand in for candidate CI.

[2.2.0 acceptance](V2_2_0_ACCEPTANCE.md) separately requires final publication,
installed-image binding, fresh complete catalog, useful reads, natural refresh
and resource settlement. These notes do not assert publication, deployment or
installed acceptance has occurred for 2.2.0.

## Authorization and recovery

Keep the PR OPEN/DRAFT without auto-merge until the owner decision. The existing
main-push workflow can publish a version-changing merge on the unchanged image
path, with a stable release flag for 2.2.0. The later owner decision must cover
merge and that automatic publication consequence. Deployment, trust changes,
Core updates and canaries remain separately scoped.

Local recovery reverts release-preparation commits while retaining accepted
runtime and evidence. Installed recovery must account for compatible Core,
configuration/database state, existing execution, usable backups and independent
recovery access. Never disable a denial, edit registry lifecycle files or reuse
a failed plan to regain authority.
