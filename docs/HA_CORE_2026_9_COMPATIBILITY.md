# Home Assistant Core 2026.9 compatibility assessment

Status: RC2 source candidate; immutable disposable-image CI is required before
release readiness. This document does not authorize a live Core upgrade.

## Immutable authority

| Release | Source commit | Tree | Source archive SHA-256 | OCI index |
| --- | --- | --- | --- | --- |
| Core `2026.9.0` | `dfb5a9e690daaf204b542896e4b595e61a11a401` | `47d4178cc071773032fd6446c569dc21caf08f1e` | `ca6ee91306eb48f9ef237c08863744636a416fa27fb4ae3010223b22f2af9793` | `sha256:372d991e58882a1d8c68c07e9aa3f3b509276e695355f73ccdb03baa70407293` |
| Core `2026.9.1` | `fc034572d0216a04ed40a07154394908a594dfed` | `4b2a1cd29e3d85c43d791e03eb82a17244956fec` | `27f657b4fbf76980f8451007116b37f09cc1226bb52c4ae6100cd46532120ff2` | `sha256:612d76760b544cb40b7ba01387fdac964c59a6a550a50a4d30b4773c822d2918` |
| ha-mcp `8.4.3` | `eac7a3aa7063432e9af17e7d7726040e909c7b8f` | `ffc545fa7e3ad683737454de0217e2b9f672589e` | `4504b0f086f20b21350269ed330886ed5398a96a671a30af97444ece412cf3cc` | `sha256:d5cea47a0115e5d161c2b319ee637b1b0a5bcfafe1597cb490299bbbc6329456` |

Both Core release commits have verified signatures. The reviewed architecture
manifest digests and source-file fingerprints are recorded in
`tests/fixtures/ha_core_2026_9_authority.json`. Core `2026.9.1` was the newest
stable `2026.9.x` patch found during this assessment. Its consumed contract is
unchanged from `2026.9.0`; the device-registry delta only cleans empty deleted
records.

The control remains Core `2026.8.1` at commit
`53998d7710b4ac280658511c24a2a3e2651f9873` and OCI index
`sha256:6340a3de3917a9b19368e767310a96dd090f6a19aca8aeadf87fd1145cec9682`.

## Corrected device assessment

Core 2026.9 returns ordinary devices and reduced child-device records in one
registry response. A child retains `parent_device_id`. Core's effective-area
semantics use a device's direct area first and otherwise inherit the regular
parent's area; an entity's direct area similarly takes precedence over its
device's effective area.

The prior 8.4.1 assessment remains true for that binary, but it is not authority
for 8.4.3. Exact ha-mcp 8.4.3 includes PR #2366. Its binary-owned component and
shared device resolvers retain child rows, preserve parent relationships
internally, and project effective area consistently through:

- `ha_get_device`;
- `ha_get_overview`;
- `ha_search`;
- `ha_get_entity`; and
- `ha_get_entity_exposure`.

RC2 therefore admits those five reads only when Core is an exact reviewed
2026.9 release and the selected binary adapter is exact ha-mcp 8.4.3. Pairing
Core 2026.9 with the legacy 8.4.1 adapter withholds only those five reads.
That cross-surface rule is checked both when publishing the catalog and again
immediately before a provider call.

## Candidate capability disposition

For exact Core `2026.9.0` or `2026.9.1` with exact ha-mcp `8.4.3`, the source
candidate admits the reviewed profiles for:

- REST and authenticated WebSocket reads;
- state, service, entity, label, area, floor, and device registries;
- direct and delegated child-device/effective-area semantics;
- template/Jinja behavior;
- Probatio-backed configuration validation;
- dependency analysis and helper planning;
- typed helper execution and authoritative reread;
- F3 mutation verification;
- automation configuration operations; and
- dashboard reads and governed dashboard updates.

The candidate retains 51 Engineering-native tools, all 25 reviewed delegated
reads, and 76 client-visible tools. `ha_get_operation_status` remains the only
held and unregistered read. No mixed, action, write, destructive, unknown, or
generic upstream capability becomes reachable. Fallback remains zero.

Evidence is layered rather than inferred from a version string:

1. exact source and immutable image provenance;
2. two stable, authenticated Core observations with REST/WebSocket agreement;
3. deterministic source and semantic fixtures;
4. focused gateway, policy, helper, F3, dashboard, and held-read tests; and
5. disposable exact-image CI against both reviewed 2026.9 patches.

Until step 5 passes on the final exact head, the disposable-runtime result is
pending rather than a local pass. Local Docker is unavailable on the authoring
machine.

## Failure and recovery boundaries

Unknown Core versions, identity disagreement, partial or malformed evidence,
catalog drift, adapter drift, generation/session movement, stale plans, and
lease reuse fail closed. Capability withdrawal is scoped to the affected
profile. Recovery after durable dispatch intent is observation-only and cannot
redispatch. Health and audit output contains only bounded identifiers, fixed
reason codes, counts, and fingerprints; it excludes endpoints, tokens, raw
registries, schemas, configurations, and household identities.

## Future live acceptance boundary

RC2 must first be deployed and tested while Core remains 2026.8.1. A fresh full
backup and its recovery material must then be verified. A Core update requires
Josh's separate authorization and must target the then-current reviewed stable
2026.9.x patch. Post-update smoke, restart recovery, and reversible helper and
dashboard canaries remain distinct authorized operations.

No live Home Assistant, deployed MCP server, production credential, backup,
upgrade, release publication, or deployment was used for this source assessment.
