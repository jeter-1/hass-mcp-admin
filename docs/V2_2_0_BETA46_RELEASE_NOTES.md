# Engineering 2.2.0-beta.46 release notes

Beta 46 stages a semantic-completeness correction for helper dependency risk.
Engineering 2.2.0-beta.45 remains advertised until protected promotion. Stable
v1.1.2 and the 51-tool Engineering registration remain unchanged.

## Typed trigger and time semantics

Complete finite state and zone triggers now retain their entity provenance
through `trigger.from_state`, `trigger.to_state`, `last_changed`,
`last_updated`, `now()`, `as_timestamp()`, datetime subtraction,
`total_seconds()`, scalar formatting and reviewed context identity access.
These value-consuming operations cannot turn a fixed unrelated person trigger
into a target-capable helper selector.

Typed provenance survives the ledger's bounded assignments, aliases, finite
containers, branches, loops, macros, filters and attributes. Using a typed
scalar or unknown value as a later entity selector still fails conservatively.
Unknown callables, external templates, dynamic labels, caller-supplied selectors
and unbounded state collections remain opaque or coverage-failed.

Consequential automation effects now attach only when an exact helper edge or
genuine target-capable opacity survives. Unrelated cover actions no longer
elevate a helper whose source relationships are all exact exclusions or
dependency-neutral. Independent exact edges in the same automation remain fully
consequential.

## Complete analysis with bounded presentation

Action profiles now distinguish complete traversal and semantic understanding
from bounded visible presentation. More than 32 action domains, services or
reason codes—and compacted effect targets, data or structure—can remain
analytically complete when every material value was examined and bound into
exact counts and deterministic full-set fingerprints. Changes beyond a visible
prefix still invalidate approval evidence.

Actual action-step or depth exhaustion, unavailable profiles or lock identities
and unsupported target-capable effects remain explicit analytical failures.
They stay non-actionable and conservatively locked. Large logical downstream
profiles continue to reconstruct through the existing authenticated pagination
and fragment contract; presentation fragmentation is not a coverage failure.

New helper plans use `helper-dependency-risk-v5`. Existing v3 and v4 plans stay
readable for historical review and recovery but require replanning and acquire
no current execution authority.

## Compatibility and non-actions

No public schema, tool registration, helper write authority, approval authority,
task schema, provider admission/routing, fallback, workflow, container,
deployment or stable-v1 surface changes. Exact helper dispatch, durable intent,
one-attempt semantics, authoritative readback, duplicate suppression,
readback-first uncertain-response handling, recovery and audit behavior remain
unchanged.

This staging change performs no merge, promotion, publication, deployment,
restart, live Home Assistant access or helper canary. Post-deployment read-only
verification and any governed write canary require separate authorization.
