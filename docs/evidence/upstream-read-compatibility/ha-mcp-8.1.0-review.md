# Exact ha-mcp 8.1.0 compatibility review

## Decision

The reviewed result is **Outcome C**, limited to the exact immutable OCI
artifacts listed below. The automatic-read set remains 24 tools and the held
set remains exactly `ha_search` and `ha_get_operation_status`, but one consumed
read response changed: `ha_get_hacs_info` moved `success` from
`/data/success` to `/success`. Engineering therefore uses the exact
`ha-mcp-hacs-info-top-level-success-v1` projection only for ha-mcp 8.1.0,
protocol `2025-03-26`, and that tool.

The GitHub release executables and MCPB are explicitly **not trusted 8.1.0
artifacts**. They were built from a pre-release parent and report package
version 8.0.0. Engineering admits neither a version family nor all artifacts
attached to a release page; it admits one exact server identity, protocol,
catalog, and reviewed OCI identity.

## Source and release identity

| Evidence | Exact value |
| --- | --- |
| Repository | `homeassistant-ai/ha-mcp` |
| Annotated tag | `v8.1.0` |
| Tag object | `18651432226ee148c6d9d432b38acdca6cb8a3e3` |
| Tag target/source commit | `0683f5ff34e5c71f35bce08d1cedcdee3c0a60b2` |
| Source tree | `1dca1d1f7e7fd79f4545b60d0be84db322e6bbf6` |
| Source archive SHA-256 | `96e5a0b147a69553e4bb9a0e2d4803aca5465081a1fd5adfca7f8f8cb3eb5c20` |
| GitHub release | ID `365493077`, published `2026-08-05T12:01:01Z` |
| Previous reviewed source | `9dd3ac620e3149cd34ec3c990b6ee81e778191f2` |
| Source delta | 40 commits, 97 paths, +9,255/-4,122 |
| Protocol | `2025-03-26` |
| Custom component | `1.3.1` |

The tag and commit are unsigned. The annotated tag target, captured source
tree, exact runtime files, and immutable OCI manifests are the source/code
identity evidence. `target_commitish=master` and mutable tags are not authority.

## Trusted immutable artifact identities

Standalone `ghcr.io/homeassistant-ai/ha-mcp:8.1.0`:

- OCI index:
  `sha256:4c07e6259a42ed33958ac9d018aba7f4b03ea676388fd3264f8abde5ea767f76`;
- linux/amd64 manifest:
  `sha256:c1d7eb571a417c5b3765c1d4971cbedb7d2800725bb9bab1a510c876cbacb78c`;
- linux/arm64 manifest:
  `sha256:4bbb28a184e1a9a307bff2b55fe4423cb011e7ef7c0d4fade407c6460d6481b0`;
- no arm/v7 manifest.

Home Assistant add-on:

- amd64 index:
  `sha256:2744a11c90f7a66e61fabe8166d058191d236094393c50d976978407c039d45d`;
- amd64 image manifest:
  `sha256:f415b72351d79414a3133c227622633d9c190a3f4f6b849eed93ac524ac1c2d5`;
- arm64 index:
  `sha256:71bd08ac7ab4272bc226b91d299929949fa24b674e164121566bc1d84666e273`;
- arm64 image manifest:
  `sha256:2dad5c7f8afcfb8c5624d82a7d9c322fc70351d32d9697e07a162ec7015250b0`;
- no arm/v7 image.

The OCI revision label is
`6213fc8047171a2731af6299f9bcecd73e96fcad`, two commits after the tag.
The intervening tree changes are only add-on release metadata and changelog;
the packaged runtime source is byte-identical to the tag source. That revision
is useful diagnostic evidence but is not substituted for the tag target or
manifest digests.

## Tagged add-on metadata discrepancy

The exact tagged `homeassistant-addon/config.yaml` contains `version: 8.0.0`.
Post-release add-on repository metadata at `6213fc8` contains `version: 8.1.0`.
The published add-on image has `io.hass.version=8.1.0`, package metadata
`ha_mcp-8.1.0`, and `HA_MCP_BUILD_VERSION=8.1.0`; MCP initialize reports
server version 8.1.0.

For lifecycle binding, Supervisor's installed add-on inventory is the
authoritative installed-version source. It is bound to the configured add-on
endpoint hostname and must equal the exact MCP server version admitted from
initialize plus `tools/list`. Source-tree release metadata is build input, not
evidence of what Supervisor installed. A negative fixture pairs the 8.1.0
runtime with the tagged tree's stale 8.0.0 value and must fail binding; the
previous-version value can never become an admitted installed identity.

The focused current-main impact boundary is:

- upstream ha-mcp self-restart planning fails before plan persistence on the
  version disagreement;
- ordinary other-add-on restart planning also fails because target
  classification first establishes the exact upstream add-on binding;
- Engineering's own add-on self-restart uses its separate authoritative
  Supervisor-self identity and does not consume the upstream binding;
- controlled reload and Home Assistant restart planning do not consume add-on
  inventory and are unaffected by this particular disagreement.

This is the tested boundary; no wider lifecycle-impact claim is made.

## GitHub release asset exclusion

The published release assets matched GitHub's recorded digests but were
mis-versioned:

| Asset | SHA-256 | Finding |
| --- | --- | --- |
| Linux executable | `232cbb5a8c6f03a72a4b618b6549123f0268de91bbda05b626fd27a66c09a05a` | prints `ha-mcp 8.0.0` |
| macOS arm64 | `d55e801871a6c3fca506e99f1fcabf1c28076573f950826df20c7968a334d15a` | contains `ha_mcp-8.0.0.dist-info` |
| Windows | `fb970fac7b9066d95b46df2168f8c3039ab20d614ebc90d7710ea9127643b2dc` | contains `ha_mcp-8.0.0.dist-info` |
| MCPB | `57af4c6514d24e97b33f489ab63e3150031703bf0f96640f51a8be647e663915` | manifest says 8.1.0 but embeds stale-version binaries |

The binary/MCPB reusable workflow checked out the pre-release event SHA
`04c6e4ffbd55930317e49508b8df2fbc13cab986` instead of the exact tag. The OCI
workflows used exact-tag checkout and do not share the defect. A mis-versioned
binary advertises 8.0.0 with changed descriptors and must fail the exact 8.0.0
contract gate. It is not covered by the 8.1.0 admission.

## Complete source-delta classification

Every changed path is retained in the private 97-path classification. Counts
by primary category are:

| Category | Paths | Engineering impact |
| --- | ---: | --- |
| translations/documentation only | 23 | no wire authority |
| CI/release machinery | 19 | exposed the release-asset identity defect |
| settings sidecar | 12 | exact lifecycle runtime evidence required |
| flow/config-entry handling | 10 | changed successive selection semantics |
| tool catalog or schema | 7 | exact descriptor regeneration required |
| HACS behavior | 6 | read response projection and write reclassification |
| authentication/security | 3 | documentation/access-gate review; no new per-user authorization claim |
| custom component | 3 | exact component/version review |
| Home Assistant add-on | 3 | metadata and immutable image review |
| website/setup wizard | 3 | no Engineering runtime contract |
| embedded worker behavior | 2 | shutdown cancellation evidence |
| dependency/runtime packaging | 2 | exact image and audit regression |
| startup/shutdown lifecycle | 2 | cleanup/readmission evidence |
| MCP transport | 2 | disconnect/reconnect acceptance |

No Critical or High source-security regression was found. The excluded binary
identity defect is Medium under the immutable-OCI-only boundary and blocking
if a claim were made for every official 8.1.0 distribution.

## Runtime catalog and catalog-count reconciliation

The controlling exact `tools/list` catalog advertises 78 unique tools in both
standalone and add-on deployments. It was captured twice with byte-identical
canonical evidence. Every advertised tool is classified; unknown and
unreviewed counts are zero.

The source contains 87 explicit `@tool` declarations plus dynamically
registered `ha_get_skill_guide`. Ten explicit declarations are gated off in
the reviewed default production/add-on configuration:

`ha_config_get_yaml`, `ha_config_set_yaml`, `ha_delete_file`,
`ha_dev_manage_server`, `ha_dev_manage_settings`,
`ha_get_dashboard_screenshot`, `ha_list_files`, `ha_manage_custom_tool`,
`ha_read_file`, and `ha_write_file`.

Four search proxy tools—`ha_search_tools`, `ha_call_read_tool`,
`ha_call_write_tool`, and `ha_call_delete_tool`—exist only in optional
tool-search mode, where they replace/hide the ordinary catalog. They are not
additional default tools. No disciplined source extraction yields the
previously reported Claude count of 93; it requires an undocumented
false-positive selection. Source-only, conditional, hidden, and nonadvertised
names therefore remain separate diagnostic evidence and cannot influence
admission.

## Exact fingerprints

| Model/surface | Standalone | Add-on |
| --- | --- | --- |
| raw operational catalog | `d8ac6e0736f7bfdc58d3ec8a31f512d8ab70be13336753f4388d7619019a53a2` | `6b5cd123cc60ff6668c2ff4dd1f9cedbe6a7a21fe43fe00471cd46611d4406d7` |
| strict full contract `ha-mcp-strict-full-contract-v1` | `34596f05fc48787260487ba4dea1177df6b28a540245a9d4b7feee1a3a01616e` | `d24f3fb3170a91e386975ab6bd82bbb270c40a4be31b0b1d08ec4f9a07f20729` |
| normalized catalog `ha-mcp-reviewed-normalized-catalog-v1` | `5ec7b1f4a4c2ffabb2acc14c73a230f08a5f94908b6f27e57cb6739d662f03d7` | same |
| Dashboard normalized runtime | `fb7f3789c8c020d8636a96b85a207635e94eefe9e0944c8814de59aba17e532e` | same |

The 234 standalone/add-on leaf differences are exactly three reviewed dynamic
policy values on each of 78 tools: deployment, enabled, and live. Rules remains
zero. No name, description, input schema, output contract, annotation, tag,
pinned, LLM-exposure, or policy-shape difference exists. Raw hashes remain
diagnostic; semantic model-aware validation is authoritative.

The captured runtime registration order is retained to make the strict raw
catalog fingerprints reproducible. Order is diagnostic evidence only: exact
name-set equality and the sorted per-tool normalized aggregate remain the
authoritative admission checks.

The offline runtime capture initially produced candidate aggregate
`d1ceeb40e8c54c3e2df474209a53d1071492e581d53448ea7efc496cd02aa9b9`
using the copied 8.0.0 policy classifications. The required source review then
reclassified all of `ha_manage_hacs` from mixed/wrapper-required to persistent
write. Classification is deliberately one aggregate component, so the final
reviewed policy deterministically produces `5ec7b1f4...`; the earlier candidate
is retained only as pre-review evidence and is not an admission identity.

## Complete policy accounting

| Classification | Count |
| --- | ---: |
| automatic read | 24 |
| held for canary | 2 |
| mixed or wrapper-required | 13 |
| persistent write | 33 |
| physical/high-risk action | 4 |
| prohibited | 1 |
| unsupported | 1 |
| **total** | **78** |

The delegated set is:

`ha_config_get_automation`, `ha_config_get_calendar_events`,
`ha_config_get_category`, `ha_config_get_label`, `ha_config_get_scene`,
`ha_config_get_script`, `ha_config_list_dashboard_resources`,
`ha_config_list_groups`, `ha_config_list_helpers`, `ha_eval_template`,
`ha_get_automation_traces`, `ha_get_blueprint`, `ha_get_device`,
`ha_get_entity`, `ha_get_entity_exposure`, `ha_get_hacs_info`,
`ha_get_history`, `ha_get_overview`, `ha_get_skill_guide`, `ha_get_state`,
`ha_get_todo`, `ha_get_zone`, `ha_list_floors_areas`, and
`ha_list_services`.

The held set remains exactly `ha_search` and `ha_get_operation_status`.

## Descriptor changes and HACS remove

There are no default runtime tool-name additions or removals. Three advertised
descriptors changed:

- `ha_config_set_helper`: description only;
- `ha_manage_hacs`: description and input schema; `remove` is added to the
  action enum and action/repository descriptions change; annotations and
  output schema are byte-equivalent;
- `ha_set_integration`: the `config` input description now documents
  successive selection lists.

The source-only `ha_dev_manage_server` descriptor also changed but is not
advertised in the reviewed runtime.

The exact `ha_manage_hacs` component fingerprints are:

| Component | 8.0.0 | 8.1.0 | Result |
| --- | --- | --- | --- |
| description | `c0aeb6d0fbfbb370a893e7fcd78cb55abe5c43ae91385d8251596454396721c2` | `71ec0768c413b8a664b95dcf90e39ad9251528608d1f58a5dd4b7345e0b6acb8` | changed |
| input schema | `0f90beb27c269c103c5d09e133c7afaf7e8065f647f8abac3380eced856ee8fd` | `1d272b827475b39e26bcc478b84409fa00da6920a7dc7980c3f8d657a7a1e5cb` | changed |
| annotations | `ab8e804cf5b3a85ee08f741f9ebca37d8bcddab844bf1c76a63b62a4d45df0e5` | same | unchanged |
| output schema | `82ef96cebaf5fbe16269fd18b0240d78f5b9b90a4155a17eb797115b09148ecf` | same | unchanged |

Because `ha_manage_hacs` now contains a first-class destructive remove action,
the entire 8.1.0 tool is reclassified from mixed/wrapper-required to
`persistent_write`. It is absent from the automatic gateway, all special
provider allowlists, and every Engineering public registration path. Calls to
`ha_manage_hacs(action="remove", ...)` are unreachable unless a later review
adds a dedicated governed wrapper. Both the old and new HACS read success
envelopes are tested; this write reclassification does not create a route.

## Provider and response contracts

- Dashboard v3 descriptor and normalized policy projection are unchanged and
  receive a new exact 8.1.0 attestation.
- Backup, reload, add-on action, add-on inventory, and Home Assistant restart
  descriptors are exact-release reviewed and remain argument constrained.
- Exact 8.0.0 and 8.1.0 `ha_get_addon` inventory/detail responses are
  byte-identical against the same source-derived fixture. Both use
  `ha-mcp-lifecycle-addon-structured-content-v1` with
  `mcp-direct-structured-content-v1`; no new response model is justified.
- The detail response remains one text item of 71,986 bytes plus direct
  structured content.
- `ha_get_hacs_info` 8.0.0 uses
  `{data:{success:true,...},metadata:{...}}`; 8.1.0 uses
  `{success:true,data:{...},metadata:{...}}`. The exact 8.1 model restores the
  stable reviewed nested-success representation and rejects missing/extra
  fields, wrong types, inner collisions, duplicate JSON members, non-finite
  values, and divergent text/structured payloads.

## Runtime lifecycle evidence

Exact-image runtime probes, plus a SHA-256-bound execution of the custom
component teardown functions from source commit
`0683f5ff34e5c71f35bce08d1cedcdee3c0a60b2`, establish:

- normal and repeated startup preserve one stable settings-sidecar URL;
- corrupt persisted `ui.state` regenerates a new valid identity;
- the sidecar listener is bound only to `127.0.0.1`;
- sidecar state/discovery files are mode 0600;
- shutdown removes serving files while retaining the stable state file;
- no disabled sentinel is created during ordinary replacement;
- pending embedded worker reader/watcher tasks are cancelled, the async
  generator finalizes, the loop closes, and zero loop errors remain;
- cleanup observes bounded cancellation/timeout behavior; and
- exact-image CI forces an Engineering disconnect, requires a truthful
  no-fallback failure, restarts the same immutable 8.1.0 image, and requires
  exact readmission to `ha-mcp-v8.1.0-4c07e625` under protocol `2025-03-26`,
  with 78 upstream tools, 24 delegated reads, two held reads, 48 local tools,
  72 total tools, and every mismatch, quarantine, missing, unreviewed, and
  fallback counter at zero.

The custom-component file is not shipped in the standalone or add-on OCI
images. Its worker cancellation/loop-closure claim therefore comes from the
separate exact-source probe, not from the packaged-image sidecar probe. CI
verifies the reviewed file SHA-256 before extracting and executing only
`_cancel_pending_tasks` and `_teardown_worker_loop`; no downloaded source is
executed before that digest check.

## Add-on and custom-component disposition

Apart from the tagged/post-release version discrepancy, the reviewed add-on
configuration is unchanged: `aarch64` and `amd64` only, application startup,
manual boot, ingress on internal port 9583, manager-scoped Supervisor API,
Home Assistant API, host networking, the same bounded options/schema, and the
same immutable architecture-specific image naming. Neither source nor OCI
publishes arm/v7. The settings UI and ingress access gate are not represented
as per-user authorization.

The custom component advances from 1.3.0 to 1.3.1. Its service declarations,
events, webhook route/auth gate, config-entry types, dependencies, and public
capability names do not change. The runtime changes are bounded to embedded
server teardown/resource cleanup, its worker-loop pending-task sweep, one
development-server `pip_spec="clear"` normalization path, and Simplified
Chinese translations. The disposable Home Assistant fixture therefore needs
no new service, event, webhook, or config-entry authority. Component capability
negotiation, not the version string, remains the gate for its WebSocket reads.

Upstream runtime dependencies move FastMCP 3.4.4 to 3.4.5, websockets 16.1.1
to 17.0, and the cryptography upper bound from below 50 to below 51. Exact OCI
startup, transport, disconnect/readmission, strict Engineering dependency
audit, and disposable Home Assistant contracts are required to detect runtime
impact; these upstream changes do not alter Engineering's own secure pins.

## Security disposition and canary boundary

Unknown 8.1.1/8.2.0 releases, wrong protocol, unsupported runtime models,
extra/missing/duplicate tools, any per-tool component drift, malformed HACS
responses, stale installed-version identity, and any catalog or provider
argument drift fail before dispatch. Held tools remain unregistered. Planning
performs zero provider mutation and fallback remains zero.

Admission evidence is offline and disposable. A later controlled canary must
separately verify the deployed immutable artifact, exact 78/24/2/48/72
accounting, HACS read normalization, Dashboard reads, backup/reload/restart
planning only, lifecycle identity, zero dispatch, zero mutation, and zero
fallback. This review does not authorize an upstream update, deployment,
production access, provider apply, tag, publication, or merge.
