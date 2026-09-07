# Home Assistant Core capability-scoped readmission

Status: integrated RC2 source candidate; final exact-head CI remains required.

This is the Home Assistant Core implementation of ADR-020. It observes Core,
publishes Core-owned per-capability authority, gates existing static, delegated,
planning, and F3 routes, and exposes bounded health and audit evidence. It adds
no public tool, schema, generic forwarder, fallback, or stable-v1 change.

## Authority and observation

Compiled exact profiles cover Core 2026.7.2, 2026.8.0, 2026.8.1, 2026.9.0,
and 2026.9.1. Exact commits and immutable OCI identities are recorded in the
release evidence, not derived from live descriptions. A future compatible
release may select only an existing binary-known profile and adapter through
reviewed compatibility authority. Live version, catalog, and response evidence
cannot authorize themselves.

One reconciliation captures two consecutive authenticated snapshots containing:

- REST `GET /api/config`, states, and services;
- WebSocket `auth_ok` and `get_config` from one session;
- bounded registry/configuration probes owned by each profile; and
- semantic fingerprints for template, configuration, dependency, helper,
  dashboard, and F3 behavior where required.

REST and WebSocket versions, identity, connection, and observer session must
agree across both snapshots. Missing, duplicate, malformed, partial, oversized,
contradictory, or unstable evidence fails closed. Raw responses are discarded
after bounded projection and never appear in decisions, health, or audit.

## Independent capability profiles

Profiles separately govern:

1. basic REST and WebSocket reads;
2. state and service discovery;
3. area, floor, label, entity, and device registries;
4. delegated device/effective-area semantics;
5. direct state and configuration reads;
6. template and Probatio configuration semantics;
7. dependency and helper planning;
8. exact typed-helper execution and F3 readback;
9. governed automation and dashboard configuration operations; and
10. local governance observability.

One missing profile withholds only its consumers. Unknown profile, adapter, or
capability identifiers fail closed. Generic mutation and service-forwarding
families remain prohibited.

Core 2026.9 device reads have an additional cross-surface invariant. The five
device-dependent delegated reads require the exact binary-owned ha-mcp 8.4.3
adapter containing the reviewed child-device/effective-area correction. An
8.4.1 adapter on Core 2026.9 withholds those reads while retaining compatible
siblings. The same check runs at catalog publication and final route acquisition.

## Generations, catalog publication, and leases

A material Core identity, version, connection, session, observation, or
authority change:

1. retires the current Core generation;
2. invalidates its unused Core leases;
3. gathers and compares two fresh observations;
4. evaluates only binary-owned profiles under trusted authority; and
5. atomically publishes one new decision and route generation.

Core, ha-mcp, and transport generations remain independent. Repeated equivalent
observations are idempotent. A Core change cannot retire ha-mcp authority, and
a connector reconnect cannot invent provider authority.

Each route lease binds surface, capability, profile, adapter, generation,
session, observation, and—where applicable—the exact mutation target. It is
registered, bounded, and single-use. Same-session validation and the
cross-surface adapter check occur immediately before the existing provider
boundary. Sequential and concurrent reuse fail. A committed call may finish
once but cannot publish or extend authority.

F3 refreshes the two-snapshot evidence under the complete lock fence before
approval consumption and durable intent. Target, plan, identity, generation,
or evidence drift refuses before dispatch. Recovery after durable intent uses
authoritative readback only and never blindly redispatches.

## Exact 2026.9 result

Exact Core 2026.9.0 and 2026.9.1 paired with exact ha-mcp 8.4.3 admit all
reviewed Core profiles and all 25 delegated reads. The client catalog remains
51 Engineering-native plus 25 delegated tools, for 76 total.
`ha_get_operation_status` remains the sole held and unregistered read. Dashboard
authority stays separately bound to its exact getter/setter attestation.
Fallback remains zero.

The disposable CI matrix keeps older Core lanes paired with exact ha-mcp 8.2.0
and adds both immutable Core 2026.9 patches paired with exact ha-mcp 8.4.3. The
2026.9 lanes use a dedicated non-internal Docker bridge, synthetic credentials
and fixtures, loopback-only harness ports, bounded waits, and unconditional
cleanup.
The combined exact-image and Core lanes exercise all five device consumers
through the production gateway plus template, Probatio, dependency/helper, F3,
dashboard, and restoration contracts.

Local Docker is unavailable, so those lanes are CI-only evidence and must pass
on the final exact head before release readiness.

## Bounded projections and non-actions

Health and audit expose only version disposition, generations, profile and
adapter IDs, fixed reason codes, bounded counts/fingerprints, lease lifecycle,
and fallback count. They exclude raw identities, sessions, tokens, endpoints,
catalogs, schemas, registries, configuration, state, and exceptions.

This source integration does not publish or deploy RC2, update Core, take or
restore a backup, access a live system, create a provider, add a write route,
or enable fallback. Deployment, backup verification, Core upgrade, and live
acceptance remain separate owner-authorized stages.
