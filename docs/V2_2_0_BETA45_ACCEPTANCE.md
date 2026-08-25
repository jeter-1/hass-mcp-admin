# Engineering 2.2.0-beta.45 acceptance

Beta 45 corrects B44-HR1, the helper-risk projection defect that retained
unrelated template relationships as target-capable opacity after bounded
analysis had already proved exact exclusion or dependency neutrality.
Engineering 2.2.0-beta.44 remains advertised until a separately authorized
protected promotion. Stable v1.1.2 and the 51-tool Engineering registration
remain unchanged.

## Authoritative provenance model

The obligation ledger distinguishes runtime-value precision from the
completeness of an entity-candidate universe. A filter may leave the selected
runtime subset unknown while still proving that every possible entity comes
from one complete finite set. Such evidence terminates as an exact dependency
whose candidates can support target-specific inclusion or exclusion. Empty
sets are neutral only when their completeness is proven. Partial, truncated,
overflowed, malformed, externally opaque, or otherwise unsupported values
remain opaque or become coverage failures.

Candidate IDs and possible domains retained on an opaque obligation are
diagnostic hints only. The helper-risk layer must never reconstruct exclusion
from them. Exact target inclusion, target exclusion, neutrality, opacity and
coverage failure are authoritative ledger outcomes consumed consistently by
risk, approval binding, final preflight and F3 locking.

The finite rules cover reviewed assignment and alias chains, complete literal
lists, tuples and mappings, subscriptions, finite branches and loops,
provenance-preserving filters, and complete non-helper domains such as
`states.sensor` and `states.binary_sensor`. Reviewed time and duration
operations remain neutral after the exact source state object has emitted its
dependency. An unresolved branch or selector prevents exclusion unless it is
independently proven unable to carry entity identity.

## Literal label membership

Literal `label_entities(...)` selectors are resolved while the dependency
index is built, using the same bounded entity and label registry generation as
the scan. Lookup follows Home Assistant's reviewed semantics: an exact label ID
wins; otherwise the selector is matched to a label name after Unicode case
folding and removal of ordinary spaces. An ID match is never unioned with a
different label whose normalized name collides with that ID.

Exact membership, lookup model and mode, resolved label identity,
completeness, complete composite candidate set and the membership fingerprint
are bound into ledger evidence. Label membership is unioned with independent
finite producers from lists, mappings, branches and loops; resolving one label
must never replace an already-proven candidate. Complete membership containing
the target remains an exact dependency; complete membership excluding it is an
authoritative exclusion; a complete empty membership is neutral only when the
entire composite candidate universe is proven complete.

Dynamic labels, failed registry reads, incomplete registries, malformed
memberships, selector overflow and membership truncation remain opaque. No
later live reconciliation is used. A membership change must alter the
approval-bound fingerprint and fail final preflight until a new plan is
created and approved. A partial set cannot prove exclusion, while an exact
inclusion from another component remains target-relevant even when additional
composite provenance is unresolved.

## Causality, consequence and actionability

A source contributes a helper consequence only when at least one authoritative
target-relevant causal edge survives. Excluding one template does not suppress
an exact structured trigger, action reference, blueprint relationship or
another exact template dependency in the same source. Conversely, unrelated
physical actions do not elevate the helper merely because their automation
also contains a proven-unrelated template.

Require these outcomes:

- complete exclusion or neutrality with no surviving helper edge is complete,
  low/standard, execution-eligible evidence;
- an exact benign helper edge remains proportionate;
- an exact consequential helper edge remains elevated;
- genuine target-capable opacity remains incomplete, elevated/high,
  execution-ineligible and non-actionable; and
- inventory, freshness, bounds, registry or evidence-generation failure stays
  fail-closed.

Disabled state, current runtime state, zero legacy-reference counts, an empty
partial set and any production entity identity are not exclusion proofs.

## Locking, drift and compatibility

Exact helper dependencies receive the exact helper/dependency lock. Proven
exclusion and neutral sources receive no unnecessary helper dependency lock.
Unresolved target-capable evidence receives the conservative dependency lock.
Create/update races and final preflight must bind changes to candidate and label
membership, label lookup mode and resolved identity, possible domains, branch
provenance, source configuration, evidence completeness, semantic-registry
identity and lock projection. Display-only metadata remains nonmaterial unless
it changes Home Assistant's normalized lookup identity.

The public MCP schemas, helper inputs, approval authority, task schema,
provider routing, exact input-boolean dispatch, durable intent, one-attempt and
readback-first behavior, duplicate suppression, recovery, audit attribution and
zero fallback remain unchanged. No template is rendered or executed, and no
new provider or write authority is introduced.

## Validation and release boundaries

Require Beta 44 baseline falsification, focused obligation-ledger/helper-risk
and F3 tests, Beta 37-44 regressions, complete unit discovery, protected Fast,
Full and Evidence gates, compilation, YAML and PowerShell validation,
dependency and strict vulnerability checks, secrets, whitespace, stable-v1
comparison, isolated Beta 45 promotion-candidate validation and exact-head CI.

Post-deployment acceptance is separate and read-only: rebuild the deployed
dependency index, create a fresh planning-only proposal for the test helper and
verify exclusion, neutrality, completeness, consequence, actionability and
lock projections. Beta 44 approval evidence must not be reused. The exact
off-to-on-to-off helper canary requires separate authorization after the
read-only gate.

This feature task authorizes no merge, promotion, publication, deployment,
restart, live Home Assistant access, approval, apply, dispatch or household
canary.
