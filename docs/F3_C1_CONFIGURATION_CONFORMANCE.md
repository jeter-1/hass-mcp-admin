# F3-C1 configuration-adapter conformance

Status: runtime-inert Beta 18 foundation

Direct base: merged Beta 17 main
`1815f7aabeb09eefeb86bbca1108c5cea537da5d`

Canonical contract: `ha_mcp_engineering.f3.contracts`, model
`f3-operation-adapter-v1`

This report separates current source facts, Beta 18 decisions, known limits,
and future integration requirements. It does not authorize a live operation or
activate a runtime route.

## Current source facts

### Legacy planning and execution

`create_configuration_plan` produces one immutable contract-v2 plan with 1–8
ordered operations. It resolves helpers to exact `input_boolean` or
`input_number` types, reads current configuration, validates, normalizes,
fingerprints, classifies risk, and binds the F2 policy decision. Planning
creates no task and performs no write.

The current `_apply_configuration_plan` path remains authoritative. It uses its
existing approval, process-local locking, stale reread, fixed gateway,
stop-on-first-failure, readback, partial-application, duplicate apply, and task
behavior. Beta 18 does not route that path through F3 or alter legacy rollback.

### Resource contracts

| Resource | Canonical target | Exact read/write | Normalization and verification | Legacy rollback | Provider limit |
|---|---|---|---|---|---|
| automation | bare internal ID | `GET`/`POST /config/automation/config/<id>` | automation-specific trigger/condition/action and alias rules | contract-v1 update only | no compare-and-save |
| script | bare lowercase storage key | `GET`/`POST /config/script/config/<id>` | script sequence and mode rules; no automation-only alias equivalence | none | no compare-and-save |
| input_boolean | exact `input_boolean.<id>` | exact list/read plus `input_boolean/create` or `/update` | exact name/icon/initial and helper identity rules | none | no generation guard |
| input_number | exact `input_number.<id>` | exact list/read plus `input_number/create` or `/update` | exact numeric min/max/step/initial/mode normalization | none | no generation guard |

Current fingerprints hash the normalized current resource, including absence.
Proposed hashes cover the normalized immutable proposal. F2 risk and policy
evidence are source-derived and are not recalculated by C1.

The direct Home Assistant configuration gateway is the reviewed baseline. C1
does not route through `ha-mcp`, arbitrary WebSocket or REST forwarding, caller-
selected gateways, dynamic imports, services, URLs, or physical-device actions.

## Beta 18 decisions

### Canonical API and closed capabilities

All shipped C1 modules import canonical declarations from
`ha_mcp_engineering.f3.contracts`. The repository-root `f3_contracts` package
remains a compatibility/test facade and is absent from the Engineering image.
C1 extends or contains canonical frozen objects; it does not redefine them.

One `ConfigurationOperationAdapter` and four explicit strategies expose only:

- `create_automation_configuration`
- `update_automation_configuration`
- `create_script_configuration`
- `update_script_configuration`
- `create_input_boolean_configuration`
- `update_input_boolean_configuration`
- `create_input_number_configuration`
- `update_input_number_configuration`

Unknown identities and resource/action combinations fail closed. Delete,
rename, enable/disable, arbitrary service calls/data, arbitrary provider
operations, dynamic loading, fallback, dashboard setters, and physical-device
domains are unreachable.

Every prepared operation retains one canonical target, immutable current and
proposed JSON, plan and operation identity, plan/current/proposed/risk/policy/
approval hashes, exact expected effect, closed provider operation, exact fixed
argument hash, validation and verification models, and rollback unavailable.

### Identity rules

- Automation accepts only the current bare internal ID. Case aliases,
  whitespace, `automation.` prefixes, friendly names, and malformed IDs fail.
- Script accepts only a bare lowercase storage key. `script.` prefixes,
  whitespace, case aliases, reserved service names, and malformed IDs fail.
- Input boolean and input number require the exact full entity ID in the
  matching domain. Cross-domain aliases and bare/friendly/fuzzy names fail.
- Create and update cannot change target identity, and readback must resolve to
  the same canonical identity.

Case cannot create an alternate lock identity; target acceptance itself is not
broadened.

### Exact lock graph

Every lock is resource scope. Each capability requests:

| Resource | Exclusive exact resource | Shared reload | Shared restart dependency |
|---|---|---|---|
| automation | `automation:<id>` | `reload:automation` | `home_assistant:core` |
| script | `script:<id>` | `reload:script` | `home_assistant:core` |
| input_boolean | `helper:<entity_id>` | `reload:input_boolean` | `home_assistant:core` |
| input_number | `helper:<entity_id>` | `reload:input_number` | `home_assistant:core` |

Duplicate evidence unions, exclusive dominates shared, and acquisition input is
canonical byte order. Matching reload or restart locks can later request the
same key exclusively. Different exact resources remain compatible and
different domains acquire no unrelated reload key. No global configuration
lock and no `addon:ha_mcp` provider lock are introduced.

For one operation, merged F3-A atomically acquires and reverse-releases the
complete set with durable owner/task/operation/attempt binding and fencing
generations. The pure sequence model also calculates the deterministic union
for 1–8 operations, but C1 does not activate multi-operation execution.

### Approval, preflight, and dispatch ordering

The accepted order is:

`durable locks -> final preflight -> idempotent approval consumption -> durable intent -> one gateway mutation`

Preflight validates exact prepared identity, plan/task/operation/target,
expiration, policy and approval hashes, risk evidence, provider admission,
complete locks, static schema, full Home Assistant configuration validity,
create absence or update existence, and the exact current fingerprint. The
last mutable-state decision is the authoritative read at the end of preflight.
Stale state is rejected; the proposal is never rebased.

Preflight does not require consumed approval. The caller-owned idempotent
approval callback remains inside `SharedOperationExecutor`. The adapter invokes
the executor's irreversible `before_dispatch` callback and, after successful
return, immediately awaits the exact gateway write. It performs no probe,
stale reread, mutable decision, or unrelated await in between.

Approval failure and intent persistence failure call the gateway zero times.
Successful intent permits at most one gateway invocation for that resource
attempt. Every timeout, disconnect, crash, malformed response, or lost response
after intent is possibly dispatched. There is no mutation retry or fallback.

### Observation, verification, and recovery

Provider response is not success. Observation rereads the exact target and
compares canonical identity, existence, resource-specific normalized content,
expected proposed hash, and post-write configuration validity. Evidence retains
only hashes and bounded mismatch categories.

Exact readback produces `succeeded_verified`. A different authoritative result
produces `verification_mismatch`. Unreadable evidence remains `observing` until
the unchanged deadline, then becomes `manual_review_required`.

Before intent, reconstruction may reacquire locks and rerun preflight. After
intent, recovery calls readback only, retains `dispatch_count=1`, never changes
the deadline, never invokes a gateway mutation, and never infers that a missing
response means no write occurred.

### Ordered sequence boundary

The pure sequence model validates 1–8 immutable operations, caller order,
unique operation IDs, earlier-only unique dependencies, unique exact targets,
complete lock union, and deterministic child descriptors. Every descriptor
retains the same public task ID and binds plan ID, operation ID, and a distinct
attempt ID.

It does not persist F3 children, call providers, create tasks, acquire plan
execution ownership, or execute several writes through one F3 record. A multi-
operation sequence cannot be passed to F3-A as one provider operation.

Restart evidence describes the first `not_started` or current operation,
verified historical operations, later undispatched work, and prior blocking
outcomes. It never authorizes dispatch. An indeterminate current operation
blocks later work and permits only observation. No compensation or automatic
rollback occurs.

Required future ownership is:

`one public task -> one ordered operation list -> one durable F3 child identity per operation`

### Duplicate apply, cancellation, and rollback

The isolated duplicate model reuses the exact active or terminal public task
and never authorizes another task, lock acquisition, or dispatch. Corrupt or
unrelated task/plan identity fails closed. Current public duplicate routing is
unchanged.

Task-wide cancellation is accepted only while every child is `not_started` and
no intent exists. After any child has intent or may have dispatched, it is
rejected. Verified work is retained and later work may remain undispatched;
there is no partial-cancellation state and no rollback implication.

All eight forward descriptors report `rollback_supported=false`; every
prepared operation reports `rollback_available=false`; rollback preparation
returns no operation and reaches no write. Historical contract-v1 automation
rollback remains on the unchanged legacy route. Contract-v2 F3 rollback needs
a separately governed operation and later integration decision.

### Migration equivalence

For all eight paths, tests compare legacy immutable plan projection with the C1
prepared operation for plan/operation identity, resource/action/target,
normalized current/proposed configuration, fingerprints and hashes, risk and
policy evidence, approval hash, expected effects, fixed provider operation and
argument hash, validation and verification models, rollback unavailable, and
lock-set hash. A nonmember operation or incomplete historical policy projection
fails closed and requires a new plan.

## Known external-writer limitation

Home Assistant exposes no atomic compare-and-save primitive for these writes.
Changes observed by final preflight are rejected before approval and intent.
An external writer can still race after preflight and before save. Post-write
verification detects a different final result but cannot prove that an
intermediate edit was not overwritten. C1 does not claim compare-and-swap.

## Runtime-inert and compatibility boundary

Current application startup, capabilities, governance service and recovery,
provider routing, and tool registration do not import or instantiate C1. The
current create/apply/rollback/cancel routes remain unchanged. There is no new
listener, coordinator, repository, public tool, dashboard execution, fallback,
or active configuration dispatch through C1.

Preserved values are 48 Engineering-local tools, task schema 1, configuration
plan contract 2, operational plan contract 3, protocol `2025-03-26`, stable
v1.1.2, `aiohttp==3.14.3`, and `cryptography==50.0.0`.

## Inference and remaining F3-D dependency

The current F3-A record key cannot truthfully store multiple independent
provider intents for one public task. F3-D or a separately accepted prerequisite
must provide the durable child-execution and execution-ownership namespace,
coordinate the complete sequence lock set, retain duplicate-apply ownership,
decide the legacy rollback bridge, and then switch the route. C1 deliberately
does not implement that architecture or activate any route.
