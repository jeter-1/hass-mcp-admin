# Engineering 2.1 and 2.2 foundation roadmap

Status: 2.1A and F2 accepted; Beta 11 reliability and exact-upstream correction in `2.2.0-beta.11`

The required milestone order is:

```text
2.1A Operational Administration
→ 2.1B Broader governed administration
→ 2.1C Upstream lifecycle automation
```

The signed compatibility registry may move ahead of 2.1B only if upstream
release churn becomes the higher operational risk.

## 2.1A staged delivery

1. Beta 1 established versioned operational plans, reusable
   configuration-check evidence, externally approved full-backup creation,
   independent backup verification, indeterminate recovery, and operational
   health/audit data.
2. Beta 2 completes the remaining family coherently: controlled reload with
   planning/apply validation, exact add-on restart, Home Assistant restart,
   operation-specific verification, expected-disruption handling, and durable
   background/startup reconciliation without blind redispatch.

2.1A completed its source and separately authorized deployed acceptance.

## 2.2.0-beta.1 F1 foundation

F1 separates immutable plans, existing exact-hash approvals, and mutable
durable execution tasks. It provides task lookup, bounded listing,
pre-dispatch cancellation, exact task idempotency, append-only lifecycle
events, startup rehydration, a 24-hour post-dispatch manual-review deadline,
and legacy plan projections. Existing operation-specific verification remains
authoritative.

F1 does not implement elevated/risk-aware approval UX (F2), shared locks (F3),
compensation (F4), generalized verification (F5), comprehensive historical
terminal migration (F6), later protocol/lifecycle work (F7), or MCP-native
Tasks. Those remain future milestones and receive no implied authority from
the task record.

## 2.2.0-beta.2 F1 corrective release

Beta 2 keeps the F1 architecture and public tool surface unchanged. It
separates original provider-response receipt from later readback verification
and reconciles operation counters with durable duplicate-apply task events.
It does not begin F2 or integrate C1, E1, K1, or another development lane.

## 2.2.0-beta.3 C1 foundation

Beta 3 defines the strict signed compatibility-registry format, canonical
serialization, content digest, Ed25519 verification, trust-anchor selection,
sequence and replay rules, expiry, previous-digest chaining, revocations, and
typed fail-closed validation. It does not load or retrieve a registry and does
not alter compiled admission, tools, health, providers, execution authority,
writes, or fallback behavior.

## 2.2.0-beta.4 K1 foundation

Beta 4 defines strict local knowledge manifests, provenance-only trust classes,
independent version relevance, bounded path/format/hash validation, expiration,
duplicate and version-conflict handling, deterministic ordering, and exact
citation identity. Knowledge content remains instruction-inert data.

K1 does not load at startup, retrieve remote content, register an MCP tool,
authorize recommendations or plans, access Home Assistant, integrate with the
signed compatibility registry, execute a write, or add fallback behavior.

## 2.2.0-beta.5 E1 foundation

Beta 5 defines immutable update and recovery evidence, explicit per-target
policy, deterministic findings, and the exact
`ready_for_governed_planning`, `blocked`, `manual_review_required`, and
`unsupported` advisory verdicts. Blockers, warnings, and unknowns remain
separate, and absent decision-critical evidence never becomes ready.

E1 is a pure evaluator. It does not collect evidence, load at startup, access
Home Assistant or the network, register a tool, create a plan, approval, or
task, call a provider, perform an update, backup, restart, restore, downgrade,
or safe-mode action, integrate C1 or K1 at runtime, or add fallback behavior.

## 2.2.0-beta.6 F2 policy foundation

Beta 6 derives deterministic risk delta, physical consequence, and
`standard_admin`, `elevated_admin`, or `prohibited` policy from each normalized
governed plan. Authority version 3 binds policy to the plan and requires one
administrator action for standard plans or separate plan approval and
elevated-risk acknowledgement from the same administrator for elevated plans.

F2 does not add a resource, MCP tool, provider, arbitrary service call,
update/recovery execution, shared F3 lock semantics, F4 transaction graphs or
compensation, F5 generalized verification, write fallback, or C1/K1/E1 runtime
authority. F1 task schema 1 and no-blind-redispatch behavior remain unchanged.

## 2.2.0-beta.7 F2 acceptance corrections

Beta 7 records affirmative configuration-provider response evidence before
readback and projects prohibited plans as terminal/non-actionable across legacy
compatibility fields, pending health counters, Ingress, and handoff evidence.
It does not change policy mapping, approval authority, task schema, resource or
provider reachability, update/recovery behavior, or fallback.

## 2.2.0-beta.8 persisted prohibited-plan compatibility

Beta 8 restores detail, listing, health, Ingress, startup, and handoff reads for
the exact validated Beta 6 prohibited-plan shape created by same-target
supersession. Compatibility is structural and read-only; no record is migrated
or rewritten, and any authority or execution contradiction fails closed.

Beta 8's manually reconstructed compatibility fixture omitted
`contract_version`, defaulted to contract v1, and did not model the contract-v2
records actually written by Beta 6. The deployed correction therefore did not
recognize those real records.

## 2.2.0-beta.9 real persisted prohibited-plan compatibility

Beta 9 generates neutral compatibility fixtures through the exact shipped Beta
6 writer, recognizes only its contract-v2 prohibited/superseded representation,
validates prepared operation evidence, retains every contradiction refusal, and
keeps reads byte-preserving. It also contains bounded per-record projection
failures in plan inventory and reconciles every loaded plan in health without
swallowing systemic storage errors.

Beta 10 corrects the separate legacy contract-v1 expired-automation form and
moves delta-aware safety-reducing policy beyond Beta 11. F3 begins only after
that separate milestone is accepted. Beta 9 changes no policy
classification, approval sequence, task ownership, provider, tool, resource,
or fallback boundary.

## 2.2.0-beta.10 legacy expired-automation compatibility

Beta 10 generates neutral fixtures through Beta 6's exact legacy `create_plan`
writer and expiration lifecycle. It recognizes only the source-established
contract-v1 prohibited/expired automation profile and its two complete event
sequences. It retains Beta 9 contract-v2 compatibility, partial listing, health
reconciliation, and byte-preserving reads. CI regenerates both historical
fixture families from the exact Beta 6 commit.

Delta-aware safety-reducing policy remains deferred beyond Beta 11. Beta 10 adds no
policy classification, approval sequence, task ownership, provider, tool,
resource, execution, recovery, or fallback authority.

## 2.2.0-beta.11 bounded recovery and exact upstream compatibility

Beta 11 bounds stale restart reconciliation without adding execution authority,
then adds one independently reviewed exact 8.0.0 upstream profile alongside
7.14.2. It does not begin delta-aware policy or F3. The two new 8.0.0 reads are
held for a subsequent production-canary decision and unknown 8.x releases
remain fail-closed.

Each operation must remain operation-specific. This roadmap does not authorize
a generic administrator, arbitrary Supervisor command, service-call shortcut,
fallback, restore, or deletion.
