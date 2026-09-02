# Home Assistant Core capability-scoped readmission

Status: local implementation complete; final shared-coordinator and runtime
integration deferred

This design implements the Home Assistant Core portion of ADR-020 without
copying the concurrent ha-mcp coordinator or activating new runtime routes. It
does not change startup, MCP tool registration, provider routing, fallback,
public schemas, release metadata, or stable v1.

## Source authority

The compiled Core profiles are based on the exact Core releases already
declared by the repository:

| Core release | Source commit | Immutable CI image digest |
| --- | --- | --- |
| 2026.7.2 | `f9122fb28dd30d3833b3b313924befbc82157f97` | `sha256:1476924357b46e80735c13e94232ba5c853cac052e9df4bb28d50fa56348097b` |
| 2026.8.0 | `4a9dce13f61d03960ad5d2710e2af9fd2a78af54` | `sha256:a21689ef0510df9760ee11bab4d6b2fef3ed5c1a29ed9c3224271597a23729eb` |
| 2026.8.1 | `53998d7710b4ac280658511c24a2a3e2651f9873` | `sha256:6340a3de3917a9b19368e767310a96dd090f6a19aca8aeadf87fd1145cec9682` |

Those releases receive compiled exact authority. A later Core release can use
only a verified compatibility selection supplied by the future shared
coordinator. Live Core identity, version strings, responses, or probe results
cannot construct a profile or adapter and are never authority by themselves.
The Core package performs no registry fetch or signature verification; that
boundary remains owned by the shared coordinator and signed-registry path.

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
4. registry reads;
5. dashboard and configuration reads;
6. template semantics;
7. existing typed helper operations; and
8. configuration mutation and operational actions.

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

`CoreGenerationAllocator` is the only planned shared-coordinator interface. The
local implementation uses a Core-local monotonic allocator. Final integration
may inject the shared monotonic allocator without moving Core decisions,
profiles, or observation logic into a second coordinator. A late verification
ticket cannot replace a newer pending Core generation.

## Pre-dispatch boundary

An admitted capability may issue a bounded registered single-use route lease.
The lease binds:

- surface `home_assistant_core`;
- capability, profile, and compiled adapter;
- Core generation;
- exact observation and authenticated-session fingerprints; and
- an exact target fingerprint for mutation-capable profiles.

Immediately before an existing typed provider call, the caller must collect and
reload current evidence, then validate and atomically consume that exact lease.
Identity, capability, observation, session, target, adapter, profile, or
generation drift prevents the commit. Retirement invalidates unused leases;
sequential and concurrent duplicate commits fail. A committed call may be
finished once but cannot republish authority.

This package contains no provider invocation, setter, service call, generic
forwarder, fallback, route publication, or application wiring. Final integration
must pass the successful commit to the existing typed provider boundary and
preserve its existing authoritative readback and verification.

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

## Deferred integration points

After the ha-mcp operational branch lands, the integration change must:

1. implement the existing-client adapter that obtains REST config and one
   same-session WebSocket `auth_ok` plus `get_config` observation;
2. implement binary-owned capability probes using the current direct-Core and
   typed-provider contracts;
3. translate only cryptographically verified signed-registry or compiled exact
   authority into `CoreAuthoritySelection` values;
4. inject the shared generation allocator;
5. publish Core routing and health atomically with the shared coordinator;
6. require a fresh observation and lease commit immediately before an existing
   typed mutation provider;
7. keep Core and ha-mcp generation retirement independent; and
8. stage the next available Engineering beta after rebase.

The integration must not add a second coordinator, broaden public tools, enable
fallback, change approval policy, or admit generic Core actions.

## Local acceptance

The local round proves stable and malformed observation handling, exact release
authority, compatible patch readmission, per-capability partial decisions,
semantic isolation, generation retirement, stale verification refusal,
single-use and concurrent lease behavior, exact typed-helper pre-dispatch
binding, bounded sanitized reports, no runtime import, and all ADR-020 vectors
containing Core behavior through a production Core adapter.

Disposable exact-Core execution and application startup/routing validation are
deferred to final integration because this branch deliberately has no runtime
call site. Release staging, publication, deployment, and live acceptance are
outside this round.
