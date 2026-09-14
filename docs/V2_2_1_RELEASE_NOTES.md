# Engineering 2.2.1 release notes

Engineering 2.2.1 prepares controlled build inputs and retires new arm/v7 images.
It retains the accepted Engineering behavior and technical Beta installation
identity. This maintenance release changes platform availability: **2.2.1
supports only `linux/amd64` and `linux/arm64`**. Published 2.2.0 keeps its
original three-platform images and verification contract.

Preparation base: `284baa90f5c056c383f1b663ed0f9e68cfdbe026`.
Independently reviewed implementation:
`d47d77a4e55f7606088d3d98d8545b5d81e62dd7`, tree
`a648aac66a981c86ab0e3bdcf37550943024ea39`.
Final release preparation, candidate CI and installed acceptance have distinct
identities and evidence; these notes do not assert their completion.

## Platform and installation contract

Use the existing **HA MCP Engineering Server Beta** add-on, directory/slug
`hass_mcp_engineering_beta` and image repository
`ghcr.io/jeter-1/hass-mcp-engineering-beta`. Ports, ingress, configuration
options/defaults, persistent paths and stored formats remain unchanged.

An arm/v7 installation cannot use this release. It needs a separately planned
migration to supported 64-bit hardware/OS; this change performs no migration,
uninstallation or data move. It does not change any published 2.2.0 artifact.
Historical stable-v1 remains 1.1.2, frozen and operationally retired; it is not
the current installation target or an Engineering rollback.

## Controlled build inputs

Both Docker stages pin the reviewed Python 3.12.14 multi-platform base digest.
The complete 39-package runtime wheel closure is pinned and hash-checked for
both supported architectures. The eight direct requirements and runtime
versions recorded for the accepted 2.2.0 build are preserved. Base pip remains
25.0.1. Source distributions, mutable apt resolution and compiler installation
are removed from this build path.

The test lock includes the same runtime pins and admitted hashes plus its
test/audit tools. CI builds and loads both architecture images, checks their
exact installed inventories and runs bounded dependency smoke without network
access. The final Docker build also runs dependency smoke without networking.
This does not start Engineering or contact Home Assistant.

Publication verifies the final digest's reported Python SBOM inventory and
resolved base provenance against committed input declarations before assigning
release tags. Platform requirements come from the exact release source;
historical three-platform releases and older sources without build-input
declarations keep their original verification requirements.

See [build inputs](BUILD_INPUTS.md) for locked inputs, reviewed updates and
evidence limits. BuildKit provenance and hashes provide traceability, not
independent signer authentication or byte-for-byte reproducibility. Final-digest
inspection is distinct from independently executing a published digest. CI host,
scanner, timestamp and attestation differences remain relevant. Future security
updates require deliberate review of pinned dependencies and base images.

## Preserved behavior and compatibility

Engineering remains one public Nabu Casa endpoint with internal provider
selection. The healthy reviewed ha-mcp 8.4.3 pairing requires 17 Core
capabilities and exposes 51 static plus 25 delegated tools, 76 total.
`ha_get_operation_status` remains held. Provider unavailability grants no
fallback or alternative dispatch authority.

Core 2026.9.2 still requires separately configured, valid signed Core authority
referencing existing reviewed contracts. Installing 2.2.1 does not activate
trust. Compiled pairings, provider contracts, schemas, approvals, ownership,
verification, cleanup, persistence and dependency TTLs are unchanged. Future
Core or ha-mcp versions are not implicitly supported.

## Validation, acceptance and recovery

The approved release lifecycle now permits an immediately following stable patch
without a beta/RC cycle. Existing beta/RC sequences, refusal of skipped stable
patches or direct stable minor/major jumps, materialization, complete validation,
review and owner-authorized merge/publication remain required. The policy delta
requires its own bounded review alongside these release files.

The implementation review reported 138 passing focused tests with no skips,
host-only smoke and input/historical-verification checks. Its unchanged-version
Evidence failure remains historical; it is not passing release-candidate
evidence. Final materialized 2.2.1 must pass clean-head Evidence and every
required CI family, including both actual architecture builds/smoke and the
pinned Core 2026.9.2 / ha-mcp 8.4.3 runtime lane.

The [2.2.1 acceptance contract](V2_2_1_ACCEPTANCE.md) requires separate final
publication, installed-image binding, complete fresh catalog, useful reads,
natural dependency refresh and settled resources. Prior 2.2.0 acceptance stays
bound to its original source and observations. Complete Android navigation,
cache-only startup during an outage and independent backup-content verification
remain unestablished; no new live evidence is claimed here.

Leave delivery draft for independent release-delta review and the owner's Ready
decision. Controlled merge/publication and deployment retain separate authority.
Local recovery reverts release preparation while retaining reviewed feature
commits; reverting build changes is a separate reviewed action. Installed
recovery must reconcile compatible Core, configuration/database state, current
execution, backups and independent access. A rollback does not restore upstream
arm/v7 support or authorize trust changes, cache edits or blind mutation retries.
