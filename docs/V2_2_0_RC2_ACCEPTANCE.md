# Engineering 2.2.0-rc.2 acceptance

Engineering 2.2.0-rc.2 is the materialized source candidate for Home Assistant
Core capability-scoped readmission. The advertised Engineering source is
2.2.0-rc.2, `.release/next-version` has been consumed, and stable remains
1.1.2. Materialization does not merge, publish, deploy, restart anything,
access Home Assistant, take a backup, or update Core.

## Exact release authority

The reviewed Home Assistant targets are exact Core 2026.9.0 and the newer
stable patch 2026.9.1:

| Release | Source commit | OCI index |
| --- | --- | --- |
| Core `2026.9.0` | `dfb5a9e690daaf204b542896e4b595e61a11a401` | `sha256:372d991e58882a1d8c68c07e9aa3f3b509276e695355f73ccdb03baa70407293` |
| Core `2026.9.1` | `fc034572d0216a04ed40a07154394908a594dfed` | `sha256:612d76760b544cb40b7ba01387fdac964c59a6a550a50a4d30b4773c822d2918` |

Both commits have verified signatures. Core 2026.9.1 changes no consumed
contract relative to 2026.9.0. Exact ha-mcp 8.4.3 authority is source commit
`eac7a3aa7063432e9af17e7d7726040e909c7b8f`, source tree
`ffc545fa7e3ad683737454de0217e2b9f672589e`, and OCI index
`sha256:d5cea47a0115e5d161c2b319ee637b1b0a5bcfafe1597cb490299bbbc6329456`.
Its exact tag contains PR #2366's Core 2026.9 child-device/effective-area
correction.

The complete source, archive, architecture-manifest, and reviewed-file evidence
is in `tests/fixtures/ha_core_2026_9_authority.json`.
The dependency analyzer separately binds the exact Core template sources,
including `homeassistant/helpers/template/states.py`, through semantic-registry
fingerprint `857e344b0f14ee3088e641ff3b1fd10657b9d1266dda54a1707025de18d5dcdc`.
Those source blobs were captured from the official immutable 2026.9.0 and
2026.9.1 tags and locally reverified before deterministic registry generation.
The reviewed automation-trace contract is separately bound to Core's exact
`homeassistant/components/trace/websocket_api.py` implementation: `trace/list`
must return a bounded mapping sequence and `trace/get` must retain the exact
missing-trace `not_found` envelope across every supported Core source commit.

## Functional acceptance

Source and exact-head validation must prove:

- two stable bounded Core observations precede authority publication;
- REST configuration, WebSocket authentication, and `get_config` identity
  agree;
- Core, ha-mcp, and transport generations remain independent;
- exact Core 2026.8.1 plus ha-mcp 8.4.3 remains unchanged;
- exact Core 2026.9.0 and 2026.9.1 plus ha-mcp 8.4.3 admit all 25 reviewed
  delegated reads;
- child-device records, parent relationships, and Core-defined effective
  area/floor semantics are preserved through `ha_get_device`,
  `ha_get_overview`, `ha_search`, `ha_get_entity`, and
  `ha_get_entity_exposure`;
- pairing Core 2026.9 with the older 8.4.1 adapter withholds only those five
  reads;
- template dictionary, State-object, and area/device/location helpers behave
  under exact 2026.9;
- Probatio configuration validation distinguishes success, warnings, errors,
  and malformed evidence;
- standard and consequential helper planning remain exact and deterministic;
- automation trace list/get routes require their independent reviewed trace
  contract; automation-configuration evidence cannot authorize them;
- a missing or malformed service inventory withholds only service discovery
  after the configuration and state probes remain valid;
- a missing, malformed, oversized, or timed-out state inventory withholds only
  state-dependent capabilities while independent WebSocket registry,
  automation, trace, and dashboard evidence remains eligible;
- backup, reload, add-on restart, and Home Assistant restart planning cannot
  enter their provider-backed read paths without the exact compiled Core
  requirements for those routes;
- background dependency prewarming acquires and consumes current
  dependency-planning Core authority before its first HA read, revalidates that
  authority before every later read, and makes its cache unusable when the Core
  generation changes;
- consumed F3 mutation authority is revalidated after durable intent and before
  the adapter can invoke its provider, while F3 readback revalidates the same
  current authority before every provider observation;
- add-on lifecycle identity reads consume and revalidate the planner's current
  Core route before each upstream `ha_get_addon` call;
- typed-helper execution performs one dispatch, authoritative reread,
  duplicate suppression, and exact disposable restoration;
- dashboard reads and governed reversible dashboard updates retain their exact
  Beta 57/58 authority and verification;
- held-read `RESOURCE_NOT_FOUND` remains `resource_not_found` with
  `retryable=false`; and
- `ha_get_operation_status` remains held and unregistered.

Immediately before every provider interaction, including a later interaction
inside one multi-call tool request, the current route must
retain the exact Core observation, generation, session, capability, profile,
adapter, and target. Stale authority, identity drift, adapter drift, malformed
or incomplete evidence, lock failure, or lease reuse must stop before dispatch.
Post-intent recovery is authoritative-readback only and never redispatches.

## Preserved boundaries

The required catalog remains:

- stable: 1.1.2;
- Engineering-native tools: 51;
- delegated ha-mcp reads: 25;
- client-visible total: 76;
- held reads: exactly `ha_get_operation_status`;
- task schema: 1;
- approval authority: 3; and
- fallback: zero.

No public schema, stable-v1 file, provider, generic forwarder, action/write
surface, publication authority, deployment metadata, or production trust
configuration changes. Health and audit remain bounded and sanitized.

## Exact disposable CI

The immutable matrix retains Core 2026.7.2, 2026.8.0, and 2026.8.1 controls
with exact ha-mcp 8.2.0, then adds exact Core 2026.9.0 and 2026.9.1 with exact
ha-mcp 8.4.3. The 2026.9 lanes use synthetic credentials and fixtures on a
dedicated non-internal Docker bridge with loopback-only harness ports, bounded
timeouts, and unconditional cleanup. They may mutate only disposable fixtures
and must restore each reversible helper, area, and configuration change.

Local Docker is unavailable, so disposable Core/image execution is CI-only
evidence. Every exact-head Core, ha-mcp, exact-image, packaging, and architecture
lane must pass before merge authorization is considered.

## Separate future live stages

1. Separately authorize publication and deployment, then smoke-test RC2 while
   Core remains 2026.8.1.
2. Take and verify a fresh full Home Assistant backup and the recovery material.
3. Obtain Josh's separate authorization for a Core upgrade.
4. Upgrade only to the then-current reviewed stable 2026.9.x patch.
5. Run post-upgrade smoke, restart-recovery, and separately authorized
   reversible helper and dashboard canaries.

No source validation in this PR substitutes for those operational gates.

## Rollback

Before merge, close the draft PR. After a future merge but before deployment,
revert the RC2 merge. After a future deployment, redeploy the immutable RC1
artifact from commit `6340f2024d2c667251ea4b635565a0d5020141bb`, then verify
version/build identity, storage integrity, F3 readiness, exact provider
admission, catalog counts, and zero fallback before any Core upgrade.

Merge, publication, deployment, backup, Core upgrade, and live acceptance are
separate owner-authorized actions.
