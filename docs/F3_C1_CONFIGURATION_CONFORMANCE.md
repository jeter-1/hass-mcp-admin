# F3-C1 configuration-adapter conformance

Status: runtime-inert Beta 18 foundation

Source contract: `f3-operation-adapter-v1` at
`77d8f19b3dc12ec94eef134375ddcbd5baeb2670`

This report describes repository behavior and isolated conformance components.
It is not authority to activate a route, dispatch a live operation, or access a
Home Assistant system.

## Source-derived facts

### Existing configuration architecture

`create_configuration_plan` creates one immutable contract-v2 plan containing
1–8 ordered operations. It resolves public helper operations to exact
`input_boolean` or `input_number` types, reads current configuration, validates,
normalizes, fingerprints, calculates risk, and binds the F2 policy decision.
Planning creates no execution task and invokes no mutating provider.

`_apply_configuration_plan` currently acquires sorted process-local target
locks, consumes approval, rereads the complete target set, and then executes in
caller order. Each step performs another stale reread, invokes the fixed gateway
once, reads back once, and stops on first failure. A lost response is read back
and is never blindly retried. Verified earlier operations remain applied when a
later operation fails. Configuration process-restart recovery currently lacks
an operation-specific readback reconciler.

The current gateway and resource contracts are:

| Resource | Canonical target | Current read | Fixed mutation | Normalization and verification | Current rollback | Current gap |
|---|---|---|---|---|---|---|
| automation | Bare internal ID `[A-Za-z0-9_-]{1,128}` | `GET /config/automation/config/<id>` | `POST` to the same exact path | Planning strips `id` and aliases plural fields; verification additionally accepts reviewed service/action aliases | Contract-v1 update only; no create or contract-v2 rollback | No restart reconstruction; no atomic compare-and-save |
| script | Bare lowercase storage key `[a-z0-9_]{1,128}`; reserved service names rejected | `GET /config/script/config/<id>` | `POST` to the same exact path | Strips `id`; script fields and modes are validated separately from automation | None | No restart reconstruction; no atomic compare-and-save |
| input_boolean | Exact `input_boolean.<object_id>` | `input_boolean/list` and exact ID match | `input_boolean/create` or `input_boolean/update`; update binds `input_boolean_id` | Strips `id`; exact name/icon/initial semantics; create name must deterministically produce target ID | None | Create must also exclude storage and entity-state collisions; no atomic compare-and-save |
| input_number | Exact `input_number.<object_id>` | `input_number/list` and exact ID match | `input_number/create` or `input_number/update`; update binds `input_number_id` | Strips `id`; finite numeric values normalize to floats; min/max/step/initial/mode rules are distinct | None | Same collision and concurrency limits as input boolean |

Every current-state fingerprint is the stable hash of the existing resource
normalization, including the explicit absent-state representation. Every
proposed hash is the stable hash of the normalized proposal. Risk evidence and
policy class remain the existing F2 values; F3-C1 does not recalculate or
change them.

The Home Assistant operations above expose no expected-hash write parameter,
generation check, atomic compare-and-save, or transaction receipt. The direct
gateway is the functional baseline; exact reviewed `ha-mcp` 7.14.2 and 8.0.0
configuration tool catalogs do not replace this boundary.

### Current failure categories

Before mutation, current behavior distinguishes unsupported input, invalid
identity/configuration, missing create/update state, stale fingerprint,
configuration-check failure, provider unavailability, approval/policy failure,
and target-lock conflict. After mutation may be possible, it distinguishes
confirmed non-dispatch only through authoritative gateway evidence, otherwise
treats lost or malformed responses as indeterminate and relies on readback.
Public errors remain the current governance categories; provider strings are
untrusted evidence and do not select an authoritative outcome.

## F3-C1 decisions

### Strategy and capability model

The isolated package has one `ConfigurationOperationAdapter` and four explicit
strategies. It accepts only `create` and `update`; scene, group, delete, rename,
enable/disable, service calls, arbitrary provider arguments, and unknown
capabilities fail closed.

The internal identities are:

- `create_automation_configuration`
- `update_automation_configuration`
- `create_script_configuration`
- `update_script_configuration`
- `create_input_boolean_configuration`
- `update_input_boolean_configuration`
- `create_input_number_configuration`
- `update_input_number_configuration`

Each descriptor binds the adapter model, resource/action, existing gateway,
transport and operation, full reviewed argument vocabulary, validation and
verification contracts, rollback declaration, and lock-set version. Each
prepared instance additionally binds the exact argument hash. Configuration is
canonical immutable JSON and is never evaluated in Engineering.

### Lock-set model

Each operation requests an exclusive resource lock and shared
`home_assistant:core` provider lock. A bounded ordered plan calculates the
complete union before its first dispatch. The union uses lower-case keys,
duplicate evidence union, exclusive dominance, bytewise order, and a hash-bound
sequence. Locks do not grant authorization.

F3-C1 calculates lock requests only. It does not acquire process-local locks,
persist locks, create fencing generations, or implement an executor. F3-A must
provide atomic acquisition of the complete set, exact owner/task/attempt
binding, fencing validation, and reverse terminal release.

### Preflight and validation

Preflight is designed for invocation after caller-layer approval consumption
and F3-A lock acquisition. It verifies exact prepared identity, plan expiry,
policy snapshot, consumed approval, provider admission, exact complete locks,
static resource validation, current existence/absence, exact current
fingerprint, and the full Home Assistant configuration check. Helper create
also repeats storage and entity-state collision checks through the existing
gateway bridge.

Dispatch performs a final authoritative reread immediately before the supplied
durable-intent callback. Stale or changed state fails before intent. It never
rebases a proposal, regenerates hashes, or treats validation as authorization.

### Durable intent and dispatch

F3-C1 does not persist an intent. It exposes the frozen `before_dispatch`
boundary that F3-A must bind to an atomic durable record containing task, plan,
operation, attempt, request, capability, resource/action, proposal and argument
hashes, provider descriptor, complete held locks, fencing generations,
timestamp, evidence deadline, `possibly_dispatched=true`, and
`dispatch_count=1`.

If that callback fails, the gateway is invoked zero times. After it succeeds,
the adapter invokes exactly one closed gateway write at most once. There is no
retry loop, fallback, provider choice derived from configuration, arbitrary
service data, or second mutating invocation. A received response remains
`observing`; it is not success evidence by itself.

### Observation, verification, and recovery

Observation reads the exact canonical target, compares resource identity and
the resource-specific normalized configuration, calculates only bounded hash
evidence and mismatch categories, and reruns the configuration check.

Exact identity, semantic configuration, valid normalization, and a valid
configuration check produce `succeeded_verified`. A different authoritative
result produces `verification_mismatch`. Unreadable evidence remains
`observing` until the fixed evidence deadline and then requires manual review.
Full configurations and arbitrary provider responses are absent from events and
public evidence.

Before intent, a reconstructed execution may reacquire the full lock set and
rerun preflight before committing a new valid intent. After intent, recovery
has no dispatch code path: it performs readback only, preserves the operation
lineage and one-dispatch bound, and never infers non-dispatch from a missing
response.

### Ordered plans, duplicates, cancellation, and rollback

The isolated sequence model preserves the 1–8 bound, caller order, unique IDs,
unique earlier-only dependencies, stop-on-first-failure, one attempt lineage
per resource operation, and complete pre-dispatch lock set. It is not an F4
graph executor and performs no parallel mutation or compensation.

- If operation 1 verifies and operation 2 fails pre-dispatch, the result is a
  partial application; operation 1 is retained and later work is undispatched.
- If operation 2 has intent and becomes indeterminate, reconstruction observes
  operation 2 and cannot redispatch it.
- Process loss between operations resumes at the exact first pending position;
  verified operations are never repeated.
- A dependency following a failed operation remains undispatched.
- An exact duplicate apply joins an active task or returns a terminal task;
  it creates no task, lock set, or dispatch. Mismatched task/plan identity fails
  closed.
- Cancellation is accepted only for pending, pre-intent operations. It cannot
  erase verified work, cancel possible dispatch, or imply rollback.

Normal recovery never invokes rollback. Contract-v2 rollback remains
unavailable. Existing separately approved, stale-safe legacy automation-update
rollback remains on the current runtime route and is not activated through
F3-C1.

### Outcome projection

| F3 outcome | Existing task state | Dispatch status | Permitted recovery |
|---|---|---|---|
| `preflight_rejected`, `lock_conflict`, `provider_unavailable_pre_dispatch`, `failed_pre_dispatch` | `failed_pre_dispatch` | impossible | none or a separately authorized new attempt |
| `dispatch_failed_confirmed` | `failed_post_dispatch` | authoritatively attempted without effect | a separately governed decision |
| `cancelled_pre_dispatch` | `cancelled_pre_dispatch` | impossible | none |
| `dispatch_indeterminate`, `observing` | `observing` | possible | readback only |
| `verification_mismatch`, `failed_post_dispatch` | `failed_post_dispatch` | possible | governed reconciliation or rollback decision |
| `succeeded_verified` | `succeeded_verified` | occurred or exact result observed | none |
| `manual_review_required` | `manual_review_required` | possible/unknown | governed manual reconciliation |

Persisted task-state names and task schema version 1 are unchanged.

### Observability

Closed counters cover preparation, preflight, stale and existence rejection,
validation and lock failures, intent success/failure, dispatch, response
receipt/loss, readback, verification, recovery, manual review, duplicate and
blind-redispatch prevention, and cancellation. Events contain only phase,
closed capability/resource/action/outcome categories, a target identity hash,
and bounded diagnostic codes. They contain no configuration, script sequence,
secret, credential, token, or arbitrary response.

## Tested conclusions

Deterministic offline fixtures count reads, validations, adapter dispatches,
simulated mutations, observations, verifications, recoveries, and helper
absence checks. They cover absence, stale state, provider and validation
failure, response loss before/after effect, malformed response, unreadable
readback, exact/mismatched readback, process loss, external writers, duplicate
apply, lock conflict, cancellation, and manual-review deadline.

The migration-equivalence suite consumes current contract-v2 operations and
proves all eight resource/action paths retain canonical target, normalized
proposal, current fingerprint, proposed hash, risk and F2 policy evidence,
validation result, fixed provider operation and arguments, expected final
configuration, rollback declaration, and closed error behavior. Intent
durability and readback-only reconstruction are intentional F3 additions.

The external-writer matrix proves changes visible before intent are rejected,
same-resource Engineering operations share one exclusive identity, different
resources share only a compatible provider lock, and post-save changes produce
a verification mismatch. Exact final readback cannot prove an intermediate
external update was not overwritten.

## Inferences

Binding the final preread, intent commit, and provider save in one atomic Home
Assistant transaction would be the only way to eliminate the remaining
external-writer race. No such reviewed primitive is present, so F3-C1 preserves
and documents the existing limit rather than claiming compare-and-swap.

Because the declaration-only F3 contract is outside the current add-on package,
F3-D will need an accepted packaging/import decision when it activates the
runtime adapter. That integration must not duplicate or silently fork the F3-A
API.

## Unresolved integration requirements

1. F3-A must explicitly declare the exact executor/lock API commit stable.
2. F3-C1 must then integrate and test actual durable lock records, intent
   records, fencing, lock release, duplicate tasks, and reconstruction; no
   compatibility shim is retained.
3. F3-D must activate the current configuration route only after all F3 tracks
   are accepted, while preserving plan/task schemas, F2 policy, approval
   authority, automatic-read accounting, fallback zero, and public tools.
4. Central health aggregation remains an F3-D responsibility.
5. The reserved Beta 18 branch must not merge ahead of the reserved Beta 17
   F3-B delivery unless the operator revises the release order.

## Runtime-inert boundary

Current application startup does not import this package. Current
`ChangeGovernanceService`, apply and rollback routes, provider routing,
capability catalog, and public tool registration do not reference or instantiate
it. Engineering-local tool count remains 48. No provider invocation is routed
through F3-C1 and no live system was used to produce its evidence.
