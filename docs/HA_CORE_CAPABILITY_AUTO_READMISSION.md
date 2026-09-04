# Home Assistant Core capability-scoped readmission

Status: production runtime integration implemented locally on the Beta 57
baseline; Track 3 integration, release staging, and exact-image CI remain
deferred

This design implements the Home Assistant Core portion of ADR-020 alongside the
independent merged ha-mcp coordinator. It observes Core, publishes Core-owned
per-capability authority, gates existing routes, and updates bounded health and
catalog availability. It adds no tool, provider, generic forwarder, fallback,
public schema, release declaration, or stable-v1 change.

## Source authority

The compiled Core profiles are based on the exact Core releases already
declared by the repository:

| Core release | Source commit | Immutable CI image digest |
| --- | --- | --- |
| 2026.7.2 | `f9122fb28dd30d3833b3b313924befbc82157f97` | `sha256:1476924357b46e80735c13e94232ba5c853cac052e9df4bb28d50fa56348097b` |
| 2026.8.0 | `4a9dce13f61d03960ad5d2710e2af9fd2a78af54` | `sha256:a21689ef0510df9760ee11bab4d6b2fef3ed5c1a29ed9c3224271597a23729eb` |
| 2026.8.1 | `53998d7710b4ac280658511c24a2a3e2651f9873` | `sha256:6340a3de3917a9b19368e767310a96dd090f6a19aca8aeadf87fd1145cec9682` |
| 2026.9.0 | `dfb5a9e690daaf204b542896e4b595e61a11a401` | `sha256:372d991e58882a1d8c68c07e9aa3f3b509276e695355f73ccdb03baa70407293` |

Those releases receive compiled exact authority, but the 2026.9 entry contains
capability-specific deny-only and unavailable decisions where semantics are not
proven. Its source tree is
`47d4178cc071773032fd6446c569dc21caf08f1e`; its source archive SHA-256 is
`ca6ee91306eb48f9ef237c08863744636a416fa27fb4ae3010223b22f2af9793`.
The lightweight tag points to a commit with a verified signature. The reviewed
amd64 and arm64 manifests are recorded in
`tests/fixtures/ha_core_2026_9_authority.json`, together with observed SLSA v1
attestations whose subjects match those manifests.

A later Core release can use only a verified compatibility selection of a
binary-known profile and adapter. Live Core identity, version strings,
responses, or probe results cannot construct a profile or adapter and are
never authority by themselves. Registry verification remains outside the Core
source adapter; Core accepts only already-verified selections.

## Observation contract

One verification collects two consecutive, authenticated snapshots from the
configured Core authority. Each snapshot must contain bounded evidence from:

- REST `GET /api/config`;
- WebSocket `auth_ok`;
- WebSocket `get_config` in the authenticated session; and
- binary-owned probes for each capability under consideration.

The REST config version, WebSocket authentication version, and WebSocket config
version must agree. The identity and locally generated WebSocket session value
must remain stable between the two snapshots. Malformed, partial, disconnected,
unauthenticated, version-disagreeing, identity-changing, or session-changing
evidence fails closed.

The source adapter reports which binary-owned checks passed. It cannot supply a
structural contract digest. The Core component returns the compiled digest only
when the exact required check set passed. Semantic profiles additionally
require the exact binary-owned semantic fingerprint. Raw REST or WebSocket
payloads are discarded and do not appear in decisions or diagnostics.

## Capability profiles

Capabilities are evaluated independently:

1. basic REST reads;
2. basic WebSocket reads;
3. entity and service discovery;
4. area, floor, label, and entity-registry reads;
5. direct device-registry reads;
6. delegated device/effective-area semantics;
7. direct entity-state reads;
8. automation and dashboard configuration reads;
9. template and configuration-validation semantics;
10. dependency analysis and helper planning;
11. typed helper operations and F3 readback verification;
12. existing governed configuration operations; and
13. local governance observability.

Each profile binds one capability ID, class, version, compiled adapter, complete
contract fingerprint, required probe set, existing provider boundary, and
fallback `none`. A missing, duplicate, unstable, semantically unknown, or
changed capability is held without disabling compatible siblings. Unknown
capability and adapter identifiers fail closed.

The generic configuration-mutation family remains automatically prohibited.
Concrete existing governed adapters retain their separately reviewed
authority. Typed helper operations can be readmitted only through their exact
profile and must pass the same-session pre-dispatch boundary described below.
No generic service forwarding is present.

## Generations and publication

Core owns an independent decision lifecycle:

```text
observe material Core change
  -> retire the published Core generation
  -> invalidate unused Core leases
  -> enter verifying
  -> compare verified authority and binary-known profiles
  -> atomically publish one Core decision generation
```

Equivalent observations and effective Core authority are idempotent. Authority
for ha-mcp or configured transport is not part of the Core material
fingerprint, so it cannot churn Core generations. Likewise, the Core component
has no method that can retire ha-mcp authority.

The local Core allocator generates identifiers only. Core, ha-mcp, and
transport authority remain independently invalidated. A late Core verification
ticket cannot replace a newer pending Core generation, and a Core retirement
cannot retire an ha-mcp lease or churn configured transport state.

## Pre-dispatch boundary

An admitted capability may issue a bounded registered single-use route lease.
The lease binds:

- surface `home_assistant_core`;
- capability, profile, and compiled adapter;
- Core generation;
- exact observation and authenticated-session fingerprints; and
- an exact target fingerprint for mutation-capable profiles.

Immediately before an existing typed provider call, the caller validates and
atomically consumes that exact lease. F3 mutation dispatch first performs a new
two-snapshot Core reconciliation after final adapter preflight, acquires the
complete Core authority set, and consumes it inside the irreversible callback
after approval consumption and before durable F3 intent.
Identity, capability, observation, session, target, adapter, profile, or
generation drift prevents the commit. Retirement invalidates unused leases;
sequential and concurrent duplicate commits fail. A committed call may be
finished once but cannot republish authority.

Core authority contains no provider invocation, setter, service call, generic
forwarder, or fallback. Runtime integration only gates existing typed provider
boundaries. F3 recovery remains readback-only. F3 observation and verification
also re-observe Core and require current readback authority; an unavailable
readback profile yields bounded manual review rather than success or redispatch.

## Core 2026.9 disposition

Exact source review found that Core 2026.9 returns regular device records and
reduced child-device records in one registry list. Effective areas can be
inherited from a parent device. Exact ha-mcp 8.4.1 contains neither
`parent_device_id` nor effective-area handling in its relevant source and its
flat direct-area projection cannot preserve that contract.

Accordingly, basic REST and WebSocket reads, state/service discovery,
non-device registries, direct entity state, automation configuration,
dashboard configuration, and local governance observability are admitted after
their exact probes. Direct device-registry reads, the five affected delegated
reads, template semantics, configuration validation, and F3 readback are held
or quarantined. Dependency/helper planning, typed helper execution, and
governed configuration operations are unavailable. This is a partial decision,
not a global Core rejection.

The five quarantined delegated reads are `ha_get_device`, `ha_get_overview`,
`ha_search`, `ha_get_entity`, and `ha_get_entity_exposure`. `ha_eval_template`
is separately held. Exact ha-mcp 8.4.1 admission remains 25 reads; Core routing
withholds six of those from a fresh catalog on Core 2026.9. The native catalog
remains 51 tools, and `ha_get_operation_status` remains independently held by
the ha-mcp policy.

## Bounded projections

The update assessment exposes only:

- observed Core version;
- previous and current Core generations;
- identity-agreement status;
- compatible and held counts;
- bounded capability IDs and reason codes;
- whether code or reviewed compatibility data is required;
- transport, schema, semantic, or provider change categories;
- bounded lease, commit, retirement, and capacity counts; and
- fallback count, always zero.

It excludes raw configuration, entity state, endpoints, credentials, tokens,
WebSocket payloads, sessions, schemas, response bodies, and exception text.
Material and wholly withheld reconciliation outcomes are additionally written
through the existing sanitized audit sink using only this bounded authority
vocabulary; an audit-write failure grants no authority and is counted in
health.

## Remaining integration points

1. Receive and independently review Track 3's final rebased two-commit head,
   then cherry-pick it and resolve any overlap once on this branch.
2. Run the immutable Core 2026.9 disposable lane described by
   `tests/fixtures/ha_core_2026_9_disposable_lane.json`. Workflow changes are
   intentionally absent because this local task did not authorize protected
   workflow edits.
3. Stage the next release only after Beta 57 publication, combined validation,
   and confirmation that no other main change claimed the next beta.
4. When an independently reviewed ha-mcp release implements parent/effective
   area semantics, supply exact cross-surface semantic probe evidence and a
   verified selection for only the delegated device profile. The direct device
   profile must remain held until its own adapter is corrected.

## Local acceptance

The local round proves stable and malformed observation handling, exact release
authority, capability-local 2026.9 decisions, child-device/effective-area
validation, deterministic retirement, stale-plan refusal, atomic complete
lease sets, target binding, catalog withdrawal/restoration, F3 pre-dispatch
reprobe, readback withholding, bounded health, production runtime composition,
and all applicable ADR-020 vectors.

The local Docker socket was unavailable, so no disposable image claim is made.
Exact-image execution remains a release-readiness requirement in CI. Release
staging, publication, deployment, live access, and the Core upgrade remain
outside this branch.
