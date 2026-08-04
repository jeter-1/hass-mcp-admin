# HA MCP Engineering Server 2.2.0-beta.18 release notes

## Release boundary

Beta 18 adds internal, runtime-inert F3-C1 configuration adapters conforming to
`f3-operation-adapter-v1`. Covered operations are create and update for
automation, script, `input_boolean`, and `input_number` resources.

The branch is based on the accepted F3-0 contract head
`77d8f19b3dc12ec94eef134375ddcbd5baeb2670`. F3-A was remotely available but
had not explicitly declared its executor/lock API stable when this track began,
so exact F3-A binding is deliberately pending. F3-C1 creates no executor, lock
manager, task persistence, or compatibility shim. The reserved Beta 17 F3-B
delivery remains earlier in the fixed merge order.

## Configuration conformance

One shared configuration adapter uses explicit strategies for all four resource
types. Eight closed capability identities bind exact existing gateway
operations and arguments, resource-specific identity and validation rules,
immutable proposed configuration, current fingerprints, proposed hashes, F2
risk and policy evidence, exact readback, rollback declaration, and lock-set
version.

Each operation requests an exclusive canonical resource lock and shared
`home_assistant:core`. Bounded ordered plans calculate and bind the complete
1–8 operation lock union before dispatch. F3-A will later own atomic durable
acquisition, task/attempt ownership, fencing generations, renewal, and release.

Preflight preserves existing validation and performs a late authoritative
stale-state reread. A supplied durable-intent callback must complete before the
adapter's sole fixed gateway write. Intent failure invokes the provider zero
times. A provider response alone is not success: exact normalized resource
identity, configuration readback, and full configuration validity are required.

After durable intent, recovery has no mutating path. Timeout, disconnect,
malformed response, response loss, and process loss reconstruct through exact
readback only. Deterministic fixtures prove a maximum adapter dispatch count of
one and maximum simulated mutation count of one per operation attempt.

The source honestly retains the existing external-writer limitation: the
reviewed Home Assistant operations have no atomic compare-and-save or expected
generation. Late preread rejects stale changes observed before intent, and
post-write verification detects a different final result, but cannot prove that
an intermediate external edit was not overwritten.

## Runtime and compatibility preservation

Current application startup, `ChangeGovernanceService`, apply and rollback
routes, provider routing, central health, and public tool registration do not
import, instantiate, or invoke F3-C1. Final executor integration and activation
remain F3-D responsibilities.

Beta 18 does not change:

- public MCP tools or the 48 Engineering-local tool contract;
- execution-task schema version 1, plan schema, F2 policy decisions, approval
  authority, or automatic-read accounting;
- current automation/configuration or rollback routes;
- Dashboard v3 reads, backup, controlled reload, add-on restart, or Home
  Assistant restart;
- exact `ha-mcp` 7.14.2 accounting: 78 advertised, 26 delegated, zero held,
  48 local, and 74 total;
- exact `ha-mcp` 8.0.0 accounting: 78 advertised, 24 delegated, exactly two
  held, 48 local, and 72 total;
- held tools `ha_search` and `ha_get_operation_status`;
- protocol `2025-03-26`, dependency pins `aiohttp==3.14.3` and
  `cryptography==50.0.0`, stable v1.1.2, or zero fallback.

Nothing in this release authorizes deployment, publication, tagging, merge,
production access, or runtime adapter activation.
