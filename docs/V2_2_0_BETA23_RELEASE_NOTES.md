# HA MCP Engineering 2.2.0-beta.23 compatibility-family release notes

Beta 23 stages an evidence-bound compatibility-family admission model and uses
exact `ha-mcp` 8.1.1 as its first accepted patch release.

The family model reduces repetitive review for releases whose runtime and
provider contracts remain exact. It never adds wildcard or semver runtime
authority. Every accepted patch still receives its own source tag object,
commit, tree and archive digest; OCI indexes and manifests; runtime capture;
complete per-tool policy; dashboard attestation; provider dispositions;
deterministic decision; and exact registry entry. Unknown 8.1.x patches,
8.2.x, prereleases, and unrelated servers remain unavailable.

For 8.1.1, two exact standalone captures are byte-identical and all 78 tool
descriptors match 8.1.0. The admission retains 24 automatic reads, holds
`ha_search` and `ha_get_operation_status`, leaves writes and mixed tools
nondelegated, and admits the read, dashboard, backup, and lifecycle providers.
Exact standalone and add-on CI uses immutable indexes and per-architecture
manifests and captures each deployment catalog twice. A disposable pinned Home
Assistant probe verifies that vendored
`websockets==17.0.1` loads from the private tree without importing, replacing,
or declaring the shared Home Assistant package.

The disposable Home Assistant gate is now an independent two-lane matrix
(`fail-fast: false`): the preserved exact 2026.7.2 image and exact 2026.8.0.
Exact 2026.7.2 first writes a genuine multi-config-entry device for each lane.
The target boot then
proves the baseline remains composite or, on 2026.8.0, splits into two owned
devices while its old ID continues to resolve for direct switch-service targets
with state readback,
Core's composite mapping, exact `ha_mcp_tools/device_get`, and public
`ha_get_device`. Live dependency indexing and impact analysis verify the
migrated entity/device relationships and persisted automation reference. The
same matrix also checks the 2026.8 one-time `http:` YAML storage migration and
runs every existing REST, WebSocket, configuration, validation, trace, and
cleanup client contract in both lanes.

Registry format 3 adds exact family-decision binding, provider dispositions,
source tag/tree/archive identity, and per-release revocation. Revoked entries remain
auditable but cannot authorize runtime use. Historical entries retain their
existing behavior, and exact 8.1.0 remains unchanged.

Staged-release validation now requires the exact next member of the active
development sequence. This replaces the obsolete pre-RC3 ceiling while still
rejecting skipped sequence numbers, channel changes, and core-version changes.

Beta 23 does not change public Engineering tools, task schema 1, approval
authority 3, Beta 20 F3 behavior, Beta 22 review projections, stable 1.1.2, or
the zero-fallback boundary. Publication, deployment, and live Home Assistant
changes remain outside this feature PR.
