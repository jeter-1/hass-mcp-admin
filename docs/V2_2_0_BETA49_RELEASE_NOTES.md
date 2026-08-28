# Engineering 2.2.0-beta.49 release notes

Beta 49 stages a producer-side target-scope correction for helper dependency
risk. Engineering 2.2.0-beta.48 remains advertised until protected promotion.
Stable v1.1.2 and the 51-tool Engineering catalog are unchanged.

## Preserve scope through reviewed selector transforms

Complete finite entity candidates, literal-label membership and closed domains
now keep their bounded producer identity through reviewed `expand()`, State
attribute projection and state-value filter transport. This allows an unrelated
helper to be excluded before consequence, completeness, policy, actionability,
fingerprint or lock aggregation.

Recursive expansion now follows complete, immutable snapshot evidence instead
of an entity-ID prefix: generic groups, entities sourced by Home Assistant's
group integration, and zone person membership are resolved recursively and
fingerprint-bound. Source-integration authority requires an already canonical,
lowercase ASCII Home Assistant domain of at most 64 characters; malformed
values are not normalized. Missing source/state evidence, cycles, partial or
malformed membership, overflow, dynamic selectors and unknown
entity-producing callables remain conservative. A scalar result becomes
target-capable again if it is later consumed as an entity selector without
finite identity proof.

Plans now expose the exact dependency-index generation, fingerprint and source
epoch used for classification. New plans use `helper-dependency-risk-v8`;
persisted v3-v7 plans stay readable but require replanning and cannot authorize
approval, lock projection or dispatch.

## Compatibility and non-actions

No public MCP schema, tool registration, provider admission or routing,
fallback, helper write authority, approval authority, task schema, workflow,
container, deployment or stable-v1 surface changes. Existing durable intent,
one-attempt dispatch, authoritative reread, duplicate suppression,
readback-first recovery, audit and authenticated plan pagination remain
unchanged.

This staging task performs no merge, promotion, publication, deployment,
restart, live Home Assistant access or governed helper mutation. Post-deployment
read-only planning acceptance and any helper canary require separate authority.
