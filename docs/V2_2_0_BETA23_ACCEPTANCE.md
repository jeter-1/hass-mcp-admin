# HA MCP Engineering Server 2.2.0-beta.23 compatibility-family acceptance

## Release boundary

- repository: `jeter-1/hass-mcp-admin`;
- exact implementation base: `ce0f3d9195fb552c67a858be6b4263d53296b7a5`;
- staged version: `2.2.0-beta.23`;
- advertised version before protected promotion: `2.2.0-beta.22`;
- stable version: `1.1.2`;
- protocol: `2025-03-26`; and
- merge, publication, deployment, and live Home Assistant changes: excluded.

## Exact 8.1.1 identity

| Evidence | Exact value |
| --- | --- |
| tag object | `46fa04345df4ae0a98b7e5bb9fbcebdb03018f3e` |
| source commit | `ae84694b50bfbd8d507042381fdee5e529bf73c5` |
| source tree | `f7cd88857a84fc4ad040ba01f62efc84211d9645` |
| source archive SHA-256 | `bc631f50d3efd22234430891bbf66f55bbcd1cdea775e2eb7ae1b41b5feabe79` |
| standalone OCI index | `sha256:e1d76a6ee54e26054a13a6c04089824b76d4d370246239591a02a2e4445ad2c9` |
| standalone amd64 manifest | `sha256:091b8dade6f11942a525fbe621e23b6770057c994e19538e09b4f36f3cd8bf87` |
| standalone arm64 manifest | `sha256:12b8f916024b6f5ec2e3f3454e804aaef88e8e0a2ea65e32c13eb5cf96087ada` |
| add-on amd64 index / manifest | `sha256:f5186360a6cdf66ce9a7f94f1096609ca966d4c159dedcca1b562fd0ccf7e429` / `sha256:9b051abf89667209dcc3f3d77614e0b914b69b4aa20350637569193eea23e7f2` |
| add-on arm64 index / manifest | `sha256:013ce6faff9b197634a346d5654854859d40aab1a1b1a9423f5e9e77ca38c176` / `sha256:8a14c856be38d621ee99807fde76406b7cabf99935fa2869686aaf205fed71fb` |
| OCI revision label | `a66c87b913c24f39a7cce771d0bd2d8452eb4f26` |
| exact registry entry | `ha-mcp-v8.1.1-e1d76a6e` |

The source package, standalone installed package, MCP initialize response,
add-on package/labels, and Supervisor installed inventory all report 8.1.1.
The tagged add-on `config.yaml` remains stale at 8.1.0 and is diagnostic only;
Supervisor inventory is the lifecycle authority.

## Contract and family decision

The two retained canonical captures from a healthy synthetic Home Assistant are
byte-identical at
`sha256:05532d94eedd3c39791763e06084eed073f8fd6df313dfe7322d880f8f99cf45`.
They identify `ha-mcp` 8.1.1, protocol `2025-03-26`, and 78 unique tools. The
raw catalog fingerprint and exact error-envelope shapes are identical to 8.1.0.
Complete deterministic comparison reports 78 unchanged tools, zero changed
schemas, annotations, descriptors, classifications, response envelopes,
provider contracts, or unknown drift.

The release-note item that names the 409 host sentence is confined to the
settings-UI locale documentation and tests for
`policies.pending.already_decided`. It does not change an MCP descriptor or
tool implementation, and no Engineering read, dashboard, backup, or lifecycle
provider consumes that browser-rendered sentence. The affected Engineering
tool/provider set is therefore empty; treating it as `documentation_only` does
not conceal a user-facing operational-contract change.

The family decision records only `immutable_identity_only`,
`documentation_only`, and `packaging_or_dependency_only`, producing
`admitted_automatic`. The exact policy keeps 24 automatic reads, two held, 13
mixed/wrapper-required, 33 persistent writes, four physical/high-risk actions,
one prohibited tool, and one unsupported tool. Accounting is 78 reviewed, zero
unreviewed, zero missing, zero additional, and zero fallback.

## Fail-closed and selective behavior

Permanent tests prove:

- no wildcard, latest, prerelease, semver-only, catalog-only, or self-advertised
  admission;
- 8.1.2, 8.2.0, and unrelated versions reject without an exact entry;
- schema, output, annotation, lifecycle, dashboard, security, tool-set, and
  unknown drift cannot use the automatic path;
- an automatic read can be held without disabling unrelated exact reads;
- changed write/mixed tools remain nondelegated;
- provider-specific drift holds only the dependent provider;
- descriptor normalization requires an explicit tool, field, and reviewed rule;
- decision or evidence tampering fails closed; and
- release-specific revocation removes only that exact entry from runtime while
  preserving historical audit access.

## Packaging and runtime evidence

The exact source pins private `websockets==17.0.1`, records every vendored file
in `MANIFEST.sha256`, declares no production dependency on shared `websockets`,
contains no first-party shared import, and configures the embedded HTTP listener
with `ws="none"`. CI downloads the exact source archive by digest and repeats
these checks in both disposable Home Assistant lanes. Each lane imports the
private tree and proves the shared distribution version and import state remain
unchanged.

## Exact Home Assistant compatibility matrix

| Lane | Immutable Core image |
| --- | --- |
| preserved baseline | `ghcr.io/home-assistant/home-assistant:2026.7.2@sha256:1476924357b46e80735c13e94232ba5c853cac052e9df4bb28d50fa56348097b` |
| new compatibility target | `ghcr.io/home-assistant/home-assistant:2026.8.0@sha256:a21689ef0510df9760ee11bab4d6b2fef3ed5c1a29ed9c3224271597a23729eb` |

Both lanes begin with the same state written through exact Home Assistant
2026.7.2, not a hand-edited registry. Two normal config entries and two real
sensor entities share one physical-device identifier, producing one historical
multi-entry device. The writer also persists an automation containing the old
device ID as a direct service target and an exact entity reference. It installs
the exact 8.1.1 `ha_mcp_tools` component through its supported config flow.

The 2026.7.2 lane proves the historical device remains one enumerable device
owned by both config entries. The 2026.8.0 lane proves Core migrates it to two
single-owner devices, remaps each entity to its owning split, removes the old ID
from enumeration, and reports the exact old-to-new mapping through
`config/device_registry/list_composite_splits`. In both lanes the direct old
device target still resolves, `ha_mcp_tools/device_get` expands both entities,
and public `ha_get_device(device_id=<old id>)` succeeds through exact ha-mcp
8.1.1 with the original ID and both entities.

The same run builds the production Engineering dependency index from live Core
state, registry and automation configuration, proves the exact entity reference
is indexed, and runs change-impact analysis. Impact evidence must retain the
entity's current device relationship—historical composite in 2026.7.2 and the
owning split in 2026.8.0—and report the direct automation, device-registry, and
disable-availability findings.

The writer uses an explicit default-port `http:` YAML block. The baseline lane
proves no 2026.8 storage contract is backported. The 2026.8.0 lane proves the
same reachable HTTP endpoint is migrated once to storage version 2 with
`yaml_migration_done=true`, no pending trial, stable port 8123, and no recorded
error. Every pre-existing REST, WebSocket, configuration write/readback,
validation, trace, and cleanup contract runs unchanged in both matrix lanes;
neither lane is allowed to continue on error.

The exact-image matrix derives the 8.1.1 standalone image from the committed
registry and verifies immutable index/platform identities, labels, initialize,
the complete catalog twice, automatic reads, held tools, and zero fallback. The
exact add-on matrix independently verifies both architecture indexes/manifests,
package/build/Supervisor identity, complete catalog, dashboard, backup and
lifecycle planning, bounded large add-on detail, zero dispatch, and graceful
shutdown.

## Preservation

Beta 23 preserves exact 8.1.0 policy and behavior, Beta 22 complete approval
review projections, Beta 20 F3 execution/recovery, prohibited-plan compatibility,
task schema 1, approval authority 3, stable 1.1.2, public tool schemas/counts,
secure dependency pins, and no-fallback semantics.

The protected promotion preflight accepts only the exact Beta 22 to Beta 23
sequence and rejects skipped versions, same-version staging, channel changes,
and core-version changes.

Local focused, Full, Evidence, security, architecture, exact-image, add-on, and
exact-head CI results are recorded in the draft PR after execution. Production
canary and deployment remain separately authorized work.
