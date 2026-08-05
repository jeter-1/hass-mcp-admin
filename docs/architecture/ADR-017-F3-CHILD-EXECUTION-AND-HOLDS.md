# ADR-017: F3 child execution and selective holds

## Status

Accepted for Beta 20 implementation and independent review.

## Context

One configuration plan can contain up to eight ordered provider mutations,
while an F3 execution record authorizes at most one provider attempt. A public
task therefore cannot itself be the authoritative record for every child
intent. Indeterminate operations also need to block their exact mutated target
without unnecessarily retaining provider and availability dependencies.

## Decision

Use a separate, versioned `f3-child-execution-v1` repository beneath the
existing Engineering storage root. Child identity is deterministic from public
task, plan, immutable operation identity, and ordinal. The manifest binds the
plan and prepared hashes, dependency vector, adapter/capability, target,
complete-lock hash, approval bundle, provider identity, attempt/request IDs,
and selective-hold keys. Runtime evidence stores bounded intent, response,
observation, verification, scheduling, and reconciliation facts.

Canonical serialization, atomic replacement, directory fsync, restrictive
permissions, a cross-process repository lock, and an initialization journal
make authority reconstruction deterministic. Unknown versions, mismatched
identity or hashes, contradictory child vectors, and corrupt records fail
closed. JSONL audit is never execution authority.

Only the first dependency-satisfied child can become dispatch eligible. Earlier
verified children remain historical fact. An unresolved or failed child blocks
later children. Public projection is deterministic and never reports success
unless every child is `succeeded_verified`.

When an indeterminate child enters manual review, promotion atomically retains
only its declared target lock and releases dependency locks without changing
the retained fencing generation. The selective keys are:

- configuration or rollback: exact automation, script, or helper target;
- backup: `backup:local_full_backup`;
- reload: exact `reload:<domain>`;
- add-on restart: exact `addon:<slug>`; and
- Home Assistant restart: `home_assistant:core`.

Holds never expire. An evidence deadline changes lifecycle outcome but cannot
release a hold. Failed promotion leaves the complete acquired handle intact.
A durable release journal makes process loss during authorized release
reconstructable without provider mutation.

Release belongs only to verified coordinator reconciliation or the existing
authenticated private Ingress workflow. It binds session/CSRF, child and record
generation, prepared hash, hold identities, and fencing generations. Stale and
replayed actions fail. No public MCP hold-release tool is added.

## Consequences

- One public task can truthfully project an ordered sequence without changing
  task schema 1.
- One immutable operation has at most one child attempt and one dispatch.
- Manual-review ambiguity blocks the affected resource, not unrelated work.
- Time, restart, or lease expiry cannot silently remove a conflict hold.
- Health and audit expose only bounded identifiers, status, counts, reason
  codes, and evidence hashes—never raw configurations or provider payloads.
