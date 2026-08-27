# Engineering 2.2.0-beta.47 release notes

Beta 47 stages the successor correction for two Beta 46 helper-risk defects.
Engineering 2.2.0-beta.46 remains advertised until protected promotion. Stable
v1.1.2 and the 51-tool Engineering registration remain unchanged.

## Complete exclusion and neutral provenance

Finite trigger-state, entity-domain, label-membership and collection provenance
now survives the reviewed attribute and filter transport used by Home Assistant
templates. Projecting `state`, `name`, timestamps or context identity from
fixed trigger state objects consumes the known trigger entity relationship and
returns a typed scalar. Projecting the same scalar attributes from a complete
`states.sensor` or `states.binary_sensor` collection preserves the proof that an
`input_boolean` target is excluded.

Complete finite candidates continue through literal lists and mappings,
branches, loops, subscriptions, `map(attribute=...)`, mapping values and
reviewed conversion/filter transport. Literal-label identity and membership
remain registry-generation and fingerprint bound. Unknown domains, arbitrary
selectors, incomplete label evidence, external templates, unknown callables and
over-limit inputs remain opaque or coverage-failed.

Collection-member lookup is accounted for before a filter's otherwise neutral
selection, ordering, formatting or aggregation semantics. This covers pinned
Jinja `map`, `selectattr`, `rejectattr`, `sort`, `join`, `sum`, `unique`, `min`,
`max` and `groupby` attribute arguments. An unknown or incomplete member that
could be a Home Assistant State remains bounded target-capable opacity; a known
ordinary value, exact State or complete non-target State domain retains its
proportional exact outcome.

Only an exact target relationship or genuinely target-capable opacity now
attaches a source's downstream consequence. Unrelated physical actions no
longer elevate a helper after every relationship in their source has been
authoritatively excluded or proven dependency-neutral. Independent exact
relationships remain consequential.

## Proportional action completeness

Exact, completely classified physical actions retain their consequence. Static
notification display and a bounded templated message with a proven non-control
literal prefix no longer make a separately exact safety-critical action
semantically incomplete. The implementation only parses Jinja; it never renders
or executes a template.

Dynamic-first notification values, Home Assistant Companion command and kiosk
namespaces, TTS, location refresh, channel removal, unreviewed extensions,
custom/dynamic services and unresolved script or scene effects remain
conservative and non-actionable. Display compaction and profile fragmentation
remain transport/presentation facts; actual traversal or semantic uncertainty
still fails closed.

New helper plans use `helper-dependency-risk-v6`. Persisted v3, v4 and v5 plans
remain readable but require a fresh plan and cannot authorize approval or
execution.

## Compatibility and non-actions

No public schema, tool registration, helper write authority, approval authority,
task schema, provider admission/routing, fallback, workflow, container,
deployment or stable-v1 surface changes. Existing exact dispatch, durable
intent, one-attempt semantics, authoritative reread, duplicate suppression,
readback-first uncertainty handling, recovery, audit and authenticated plan
pagination remain unchanged.

This staging change performs no merge, promotion, publication, deployment,
restart, live Home Assistant access or helper canary. Post-deployment read-only
verification and any governed write canary require separate authorization.
