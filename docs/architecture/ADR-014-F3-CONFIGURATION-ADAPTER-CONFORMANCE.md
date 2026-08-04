# ADR-014: F3 configuration-adapter conformance

Status: Accepted for the runtime-inert F3-C1 foundation

Date: 2026-08-04

Contract base: `77d8f19b3dc12ec94eef134375ddcbd5baeb2670`

## Context

The current configuration implementation already plans, validates, classifies,
applies, and reads back automation, script, `input_boolean`, and `input_number`
resources. It uses fixed resource operations, but it is not expressed through
the frozen `f3-operation-adapter-v1` lifecycle. In particular, configuration
restart recovery cannot reconstruct a possibly dispatched operation through
readback alone.

F3-A owns the durable executor and lock records. Its remote branch was
available while F3-C1 began, but its API had not been explicitly declared
stable. F3-C1 therefore must implement the frozen adapter protocol without
creating a competing executor, lock manager, task record, or persistence layer.

## Decision

Add one shared `ConfigurationOperationAdapter` with four explicit resource
strategies and eight closed capability identities. The package is a protected
runtime module, but current application, governance, provider-routing, and tool
registration modules do not import or instantiate it.

Each prepared operation retains canonical JSON for the current and proposed
configuration, exact planning hashes, risk and policy evidence, approval-bundle
hash, exact provider descriptor, complete lock requirements, and exact
verification contract. Configuration content is never evaluated by
Engineering and is passed only to the existing reviewed
`ConfigurationResourceGateway` contract.

The lifecycle is:

1. preparation consumes an existing immutable planned operation;
2. preflight checks identity, policy snapshot, consumed approval, provider
   admission, complete locks, current state, static validation, and the full
   Home Assistant configuration check;
3. dispatch rereads current state, invokes a supplied durable-intent callback,
   and then calls the fixed gateway at most once;
4. observation performs exact resource readback and the configuration check;
5. verification compares normalized authoritative readback, identity, and the
   proposed hash contract;
6. recovery can only repeat observation after durable intent.

Contract-v2 rollback remains unavailable. The historical contract-v1
automation-update capability is declared, but the adapter cannot manufacture a
rollback task or approval from the frozen protocol. Current rollback routing is
unchanged; F3-D must bind a separately governed rollback operation later.

## Identity and locks

Canonical target forms remain the current forms:

- automation: bare internal ID; no `automation.` entity alias;
- script: bare lowercase storage key; no `script.` prefix;
- input boolean: full `input_boolean.<object_id>` entity ID;
- input number: full `input_number.<object_id>` entity ID.

Leading or trailing whitespace and invalid domains fail closed. Lock identity
is case-insensitive without broadening public target acceptance. Resource locks
are exclusive and use `automation:<id>`, `script:<id>`, or
`helper:<entity_id>`. Every operation also requests shared
`home_assistant:core`, so a later Home Assistant restart can request the same
key exclusively.

A 1–8 operation sequence calculates and hash-binds the complete normalized
lock union before dispatch. Duplicates union evidence, exclusive mode
dominates, acquisition input is canonical bytewise order, and duplicate
resource targets fail before acquisition. F3-A remains responsible for atomic
durable acquisition, owner/attempt binding, fencing generations, renewal, and
reverse release.

## External-writer limitation

The reviewed Home Assistant REST and storage-helper operations expose no
atomic compare-and-save, expected-hash enforcement, generation guard, or
transaction receipt. The late authoritative preread rejects changes visible
before durable intent and does not widen the existing stale-state window.
An external writer can still race after that preread and before the provider
save. Exact post-write readback detects a different final value but cannot
prove that an intermediate external edit was not overwritten. This is an
existing provider limitation, not compare-and-swap.

## Consequences

- There is no new public tool, route, provider fallback, schema, task state, or
  policy decision.
- A provider success response is nonterminal until exact readback verifies.
- Intent persistence failure causes zero gateway writes.
- Once intent exists, timeout, disconnect, malformed response, or process loss
  is possibly dispatched; reconstruction cannot redispatch that attempt.
- Ordered plans stop on first failure, preserve verified earlier steps, leave
  later steps undispatched, and perform no automatic compensation.
- Metrics and events contain only closed labels, bounded diagnostic categories,
  and identity/evidence hashes.

## Deferred integration

F3-D must integrate the exact stable F3-A executor API, replace no test-only
authority with local equivalents, atomically acquire the complete lock set,
persist the full intent record, validate fencing, reconstruct sequence
position, bind duplicate apply and cancellation to durable tasks, and connect
isolated observability to central health. It must also arrange packaging of the
frozen contract or its accepted runtime successor. Route activation remains
forbidden until F3-A, F3-B, F3-C1, and F3-C2 are accepted in the reserved release
order.
