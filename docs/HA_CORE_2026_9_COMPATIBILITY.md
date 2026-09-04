# Home Assistant Core 2026.9 compatibility assessment

Status: implemented locally on merged Beta 57; exact disposable-image CI and
release staging pending

This report separates source facts, implemented decisions, unverified
semantics, and future work. It does not authorize a live Core upgrade.

## Immutable authority

| Item | Exact authority |
| --- | --- |
| Release | Home Assistant Core `2026.9.0` |
| Tag | lightweight `refs/tags/2026.9.0` |
| Commit | `dfb5a9e690daaf204b542896e4b595e61a11a401` |
| Commit signature | verified |
| Tree | `47d4178cc071773032fd6446c569dc21caf08f1e` |
| Source archive SHA-256 | `ca6ee91306eb48f9ef237c08863744636a416fa27fb4ae3010223b22f2af9793` |
| OCI index | `sha256:372d991e58882a1d8c68c07e9aa3f3b509276e695355f73ccdb03baa70407293` |
| linux/amd64 | `sha256:bd39459e8d84fbbbd9a40fdccf7ab029f20d92340263b93db00887c3d5dcdaf6` |
| linux/arm64 | `sha256:2a7eb678f984c9983d36435c12e124f406c4eedd70fb5780d8488b1c777f0d95` |
| Image version labels | `2026.9.0` on both reviewed manifests |
| Provenance | SLSA v1 attestations observed with subjects matching both manifests |

The control authority remains exact Core `2026.8.1`, commit
`53998d7710b4ac280658511c24a2a3e2651f9873`, image index
`sha256:6340a3de3917a9b19368e767310a96dd090f6a19aca8aeadf87fd1145cec9682`.
The complete machine-readable candidate and ha-mcp evidence is in
`tests/fixtures/ha_core_2026_9_authority.json`.

Official references:

- [Home Assistant 2026.9 release notes](https://www.home-assistant.io/blog/2026/09/02/release-20269/)
- [Home Assistant Core 2026.9 full changelog](https://www.home-assistant.io/changelogs/core-2026.9)
- [Exact Core source commit](https://github.com/home-assistant/core/tree/dfb5a9e690daaf204b542896e4b595e61a11a401)

## Source comparison with 2026.8.1

Engineering reviewed only consumed interfaces rather than treating an entire
Core release as one contract.

- REST `/api/config`, state discovery, service discovery, authenticated
  WebSocket `auth_ok`, and WebSocket `get_config` retain the bounded shapes
  needed by the Core observer.
- Area, floor, label, and entity-registry list commands remain independently
  probeable and are not coupled to the changed device model.
- Device-registry listing now concatenates regular devices and reduced
  `ChildDeviceEntry` records. Regular records explicitly expose
  `parent_device_id: null`; child records carry a non-null parent and omit
  regular-only metadata.
- Core's effective-area helper returns a device's direct area or its regular
  parent's area for a child. Entity effective-area resolution uses the
  entity's direct area first, then the attached device's effective area.
- Exact ha-mcp 8.4.1 source commit
  `701a7c26ac0e2309c7883a627d31873ab1510077` contains no
  `parent_device_id` or effective-area implementation in the relevant resolver,
  overview, search, registry, or voice-assistant paths. Its direct-area maps
  cannot represent the complete Core 2026.9 contract.
- Update install, skip, and clear actions now require administrator context.
  This branch admits no new action or write and does not use those actions.
- Persistent-notification `update_type=updated` is not an Engineering Core
  authority input and grants no capability.
- The removed deprecated vacuum battery property is not consumed by an
  admitted provider contract.
- Template/Jinja changes are not inferred compatible from successful identity
  probes. Template-dependent capability remains held.
- Configuration validation after the Probatio change remains held until exact
  disposable behavior is proven.

Transport failures, application error envelopes, timeouts, malformed evidence,
and REST/WebSocket version disagreement remain bounded fail-closed outcomes.
No response, version string, source annotation, or catalog advertisement can
create compatibility authority.

## Initial capability disposition

| Capability | Core 2026.9 decision | Reason |
| --- | --- | --- |
| Basic REST reads | admitted exact after probes | exact identity and bounded state/config shapes |
| Basic authenticated WebSocket reads | admitted exact after probes | one authenticated session and matching `get_config` |
| State and service discovery | admitted exact after probes | bounded unique state/domain evidence |
| Area/floor/label/entity registries | admitted exact after probes | independent non-device registry contract |
| Direct helper/entity-state reads | admitted exact after probes | exact state response contract |
| Automation configuration reads | admitted exact after probes | bounded automation configuration command |
| Dashboard configuration reads | admitted exact after probes | dashboard list plus exact configuration read |
| Governance/persisted evidence | admitted exact | local-only; zero Core/provider calls |
| Direct device-registry read | held | current native projection drops child/effective-area semantics |
| Five device-dependent ha-mcp reads | quarantined at catalog route | exact 8.4.1 projection lacks parent/effective-area semantics |
| Template semantics / `ha_eval_template` | held | 2026.9 semantic profile not proven |
| Configuration validation | held | Probatio behavior not proven |
| Dependency analysis and helper planning | unavailable | dependent semantic authority incomplete |
| Typed helper execution/readback | unavailable | complete mutation authority incomplete |
| F3 mutation verification | held | exact 2026.9 readback semantics not proven |
| Governed configuration operations | unavailable | complete mutation authority incomplete |

The five device-dependent delegated reads are `ha_get_device`,
`ha_get_overview`, `ha_search`, `ha_get_entity`, and
`ha_get_entity_exposure`. The Core gate classifies those route withdrawals as
quarantined while the direct device capability remains separately held. A
future corrected ha-mcp profile can restore only the delegated profile after
exact semantic probes and verified compatibility selection; it cannot
implicitly restore the native direct-device projection.

## Runtime behavior

Startup obtains two stable snapshots. REST and WebSocket versions must agree.
A material Core observation retires only Core authority, invalidates unused
Core leases, and atomically publishes the new per-capability decision set.
ha-mcp and transport generations are not retired.

Static routes and dynamic delegated reads acquire their complete Core lease set
and consume it once immediately before their existing provider boundary. A
fresh upstream catalog omits Core-withheld delegated reads while retaining
siblings. Reconciliation requests a new catalog listing rather than advertising
an unreviewed `tools/list_changed` capability.

F3 performs another two-snapshot Core reconciliation after final adapter
preflight and before approval consumption. It then consumes the exact Core
authority inside the existing irreversible callback before durable intent.
Core 2026.9 has no admitted mutation-verification profile, so provider dispatch
is refused. Post-intent recovery never dispatches; observation is also withheld
when current readback authority is absent and produces bounded manual-review
evidence.

Health exposes only the observed version, agreement, generation, profile IDs,
dispositions, reason codes, bounded lifecycle counters, and fallback count
zero. Material or wholly withheld reconciliations also use the existing
sanitized audit sink with the same bounded decision vocabulary. Raw responses,
registry rows, entity states, endpoints, credentials, headers, sessions,
schemas, and signed-registry contents are excluded from both projections.

## Disposable lane and remaining evidence

`tests/fixtures/ha_core_2026_9_disposable_lane.json` fixes the exact Core and
ha-mcp images and enumerates the required positive and refusal scenarios. It is
non-production and allows no Core mutation or fallback. The local Docker socket
was permission-denied, so the lane was not represented as a local pass.

This task did not authorize protected workflow edits. The immutable lane must
be wired and pass on the combined exact head in CI before release readiness.
Template dictionary/State behavior, area/device/location helpers, Probatio
validation, and exact F3 readback must remain held until that evidence supports
a separately reviewed profile.

Track 3 has not supplied its final reported rebased head. Its current local
branch is not treated as integration authority and was not cherry-picked. The
next integration session must review the reported two commits, cherry-pick that
exact head, resolve overlap once, and rerun combined validation.

## Non-actions

No live Home Assistant, deployed Engineering server, household configuration,
production credential, backup, release declaration, workflow, public tool,
provider fallback, deployment, image publication, restart, or Core upgrade was
used or changed.
