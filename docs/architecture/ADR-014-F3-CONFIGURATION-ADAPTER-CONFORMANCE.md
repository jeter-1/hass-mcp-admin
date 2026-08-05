# ADR-014: F3 configuration-adapter conformance

Status: Accepted for the runtime-inert F3-C1 foundation

Date: 2026-08-04

Merged contract and executor base:
`1815f7aabeb09eefeb86bbca1108c5cea537da5d` (`2.2.0-beta.17`)

## Context

The legacy configuration implementation plans, validates, classifies, applies,
and reads back automation, script, `input_boolean`, and `input_number`
resources through fixed direct Home Assistant gateways. Those routes remain
authoritative until F3-D.

Beta 17 ships the canonical `ha_mcp_engineering.f3.contracts` package, the
`SharedOperationExecutor`, and durable locks and execution records. One F3
record represents one exact prepared operation. It cannot represent several
independent provider intents for a contract-v2 plan under one public task ID.

## Decision

Add one closed `ConfigurationOperationAdapter`, four explicit resource
strategies, and eight create/update capability identities. Every shipped C1
module consumes canonical objects from `ha_mcp_engineering.f3.contracts`; the
repository-root `f3_contracts` facade is not a runtime dependency.

Each prepared operation binds canonical immutable configuration JSON, plan and
operation identity, current and proposed hashes, risk and policy evidence,
approval-bundle hash, a closed provider descriptor and argument hash, exact
verification contract, and rollback unavailable.

For one operation, the accepted lifecycle is:

1. the caller supplies an existing immutable planned operation;
2. the merged executor acquires the complete durable lock set;
3. adapter preflight validates immutable authority, admission, static and full
   configuration validity, existence/absence, and the final authoritative
   current-state fingerprint;
4. the adapter calls the executor's irreversible `before_dispatch` callback;
5. that callback idempotently consumes approval and commits durable intent;
6. callback success is followed immediately by the one fixed gateway write;
7. observation and verification use exact authoritative readback;
8. every post-intent reconstruction is readback-only.

Preflight neither requires nor claims consumed approval. It validates only
immutable authorization evidence already bound into the prepared operation.
There is no provider probe, mutable decision, stale reread, or unrelated await
between successful `before_dispatch` return and the gateway write.

## Identity, authority, and locks

Canonical target forms remain the current forms:

- automation: bare internal ID; no `automation.` alias;
- script: bare lowercase storage key; no `script.` prefix;
- input boolean: exact `input_boolean.<object_id>` entity ID;
- input number: exact `input_number.<object_id>` entity ID.

Whitespace variants, case aliases, invalid domains, friendly names, and fuzzy
identities fail closed. Fixed provider arguments retain the exact REST path or
WebSocket command in their hash; `provider_operation` is a closed canonical
identifier accepted by the shared executor.

Every operation requests three resource-scope locks:

- one exclusive exact resource lock: `automation:<id>`, `script:<id>`, or
  `helper:<entity_id>`;
- one matching shared reload lock: `reload:automation`, `reload:script`,
  `reload:input_boolean`, or `reload:input_number`;
- shared `home_assistant:core`.

Matching reload and later Home Assistant restart operations can request those
same keys exclusively. Different exact resources remain compatible, and
unrelated domains do not acquire each other's reload locks. C1 uses a direct
Home Assistant gateway and acquires no `addon:ha_mcp` lock.

## Ordered plan boundary

The pure sequence model validates 1–8 immutable operations, declared order,
earlier-only dependencies, unique operation IDs, unique exact targets, the
complete normalized lock union, and deterministic child descriptors. Every
descriptor retains the one public task ID and binds plan ID, operation ID, and
a distinct deterministic attempt ID.

C1 does not persist a child execution, dispatch a provider from the sequence
model, manufacture public tasks, or pass several writes through one F3 record.
Restart-position evidence describes only `not_started`, the current operation,
later undispatched work, or a prior blocking outcome; it never authorizes
redispatch.

The remaining prerequisite is a shared durable child-execution and execution-
ownership namespace:

`one public task -> one ordered operation list -> one F3 child identity per operation`

F3-D or a separately accepted prerequisite owns that namespace and the route
switch.

## Cancellation and rollback

Task-wide cancellation is accepted only while every child is `not_started` and
no durable intent exists. After any child has intent or may have dispatched,
cancellation is rejected. Verified work stays represented, later work may stay
undispatched, and no partial-cancellation state, rollback, or compensation is
invented.

All eight forward capability descriptors report `rollback_supported=false` and
all prepared operations report `rollback_available=false`. Rollback preparation
returns no operation and reaches no gateway write. Historical contract-v1
automation rollback remains unchanged on the legacy route. Contract-v2 F3
rollback requires a separately governed operation and an F3-D decision about
migration, a reviewed lock bridge, or approved temporary disablement.

## External-writer limitation

The reviewed Home Assistant REST and storage-helper operations expose no
atomic compare-and-save, expected-hash enforcement, generation guard, or
transaction receipt. The final preflight read rejects changes visible before
approval and intent. An external writer can still race after preflight and
before the provider save. Exact readback detects a different final value but
cannot prove an intermediate edit was not overwritten. This is an existing
provider limitation, not compare-and-swap.

## Consequences

- No public tool, runtime route, provider fallback, schema, task state, policy
  decision, startup listener, coordinator, or persistence repository is added.
- Provider success is nonterminal until exact readback verification.
- Approval or intent failure invokes the gateway zero times.
- Each operation attempt invokes the gateway at most once.
- Once intent exists, recovery cannot redispatch that attempt.
- Metrics and events contain only closed labels, bounded categories, and
  identity/evidence hashes.
- Current configuration, rollback, and cancellation routes remain unchanged.

## Deferred integration

F3-D must add or consume the accepted child-execution ownership namespace,
connect one public task to durable per-operation F3 records, coordinate the
complete plan lock set, preserve duplicate-apply ownership, decide the legacy
rollback bridge, integrate bounded health evidence, and activate routing only
after all F3 tracks are accepted. It must not hide several writes behind one
intent or create several public tasks.
