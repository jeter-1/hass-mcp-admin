# Recover governance and task persistence

Status: future integration runbook; depends on the merged durable-task baseline

## Current source-derived facts

Existing Engineering operational administration persists its own governed
operation records. E1 does not change governance storage, task storage,
application startup, capabilities, observability, routing, or version code.
It creates no task records and performs no startup reconciliation.

## Future integration dependency

Update-task persistence must be designed against the merged 2.2 durable-task
baseline. This E1 branch intentionally does not guess its schemas, namespaces,
transaction boundaries, task states, quarantine behavior, or migration rules.

## Proposed recovery invariants

A future update or recovery implementation should preserve:

- immutable target, candidate, policy, preflight fingerprint, and approval
  identity;
- durable intent and attempt number before any disruptive provider dispatch;
- exact distinction between not dispatched, dispatched, indeterminate,
  verification pending, verified, and failed;
- approval consumption and audit attribution across process termination;
- evidence accumulated before and after expected disruption;
- at-most-once dispatch, including after Engineering restarts;
- readback-only startup reconciliation for already-dispatched work;
- bounded quarantine of genuinely corrupt records without rewriting evidence;
  and
- stable-v1 isolation and explicit downgrade/readability behavior.

## Proposed recovery procedure

1. Start without invoking an update, restart, restore, or safe-mode provider.
2. Validate storage identity, schema version, record hashes, and namespace
   ownership.
3. Enumerate a bounded set of nonterminal update/recovery tasks.
4. Reject or quarantine malformed records under the future task contract.
5. For records with durable dispatch evidence, perform only operation-specific
   readback and post-update verification.
6. For records without durable dispatch evidence, leave them undispatched and
   require the normal approval/apply path.
7. Restore audit continuity and expose incomplete evidence without inventing
   success, failure, or version facts.
8. Escalate ambiguous task/storage identity for manual review.

## Disaster-recovery evidence

A later persistence recovery test should demonstrate records surviving an
Engineering process replacement, expected Home Assistant disruption, retained
data downgrade where supported, re-upgrade, and partial readback failure. It
must also prove zero provider dispatch during startup reconciliation and no
cross-namespace mutation of stable or legacy records.

## Non-actions

This runbook does not define the future task schema, edit current governance
files, reconcile a live process, migrate records, access `/data`, or invoke a
provider. Integration documentation must be reconciled after the durable-task
baseline merges.
