# Engineering 2.2.0-beta.49 acceptance

Beta 49 stages the producer-side target-scope correction for the deployed
Beta 48 HAMCP-089 planning failure. Beta 48 remained fail-closed: neither the
standard-helper nor consequential-helper plan was actionable, and no approval,
apply, provider dispatch or Home Assistant mutation occurred.

Engineering 2.2.0-beta.48 remains advertised until a separately authorized
protected promotion. Stable v1.1.2 and the 51-tool Engineering registration are
unchanged.

## Falsification and root cause

The Beta 48 aggregation layer already excluded obligations classified as
complete target exclusions or dependency-neutral. The deployed failure showed
that 59 unrelated obligations reached aggregation as target-capable instead.
No raw household records are retained in this repository. A sanitized
production-path fixture reproduces those 59 terminals through the shipped
`DirectHaDependencyProvider`:

| Retained Beta 48 reason | Count | Lost proof |
|---|---:|---|
| `state_value_consumed_by_filter_target_opaque` | 25 | finite label or literal selector transported through a state-value filter |
| `state_value_rendered_or_iterated_target_opaque` | 17 | the same finite selector after scalar rendering/iteration |
| `filter_member_attribute_receiver_opaque` | 9 | member attribute lookup after a reviewed finite `expand()` |
| `expand_expanded_membership_opaque` | 8 | complete literal-label selector identity discarded by `expand()` |

The producer discarded selector identity after reviewed `expand()` and
`map('states')` transforms. Later member projection consequently saw an unknown
State collection and emitted new target-capable opacity. The same snapshot was
used by the test index; the falsification did not require an aggregation-layer
broadening or an index-generation substitution.

## Corrected target-scope contract

Reviewed selector transforms preserve bounded producer identity until the
provider resolves the immutable registry-backed snapshot. Complete finite
literal candidates, complete literal-label membership and complete closed
domains can therefore terminate as exact inclusion or complete target
exclusion before any downstream profile is selected. A reviewed state-value
filter consumes the selector once and returns a typed scalar; rendering that
scalar does not invent a second entity relationship. Supplying it later to an
entity selector remains conservative unless finite entity identity is proven.

Literal-label expansion is closed only after the provider resolves the same
bounded state and entity-source snapshot used for the index. The resolver
matches Home Assistant's reviewed `expand()` contract: it recursively follows
`group.*`, entities whose recorded source integration is `group`, and `zone.*`
person membership. Registry source authority is admitted only from an already
canonical, lowercase ASCII Home Assistant domain of at most 64 characters;
malformed values are never normalized. It binds the source kind, complete
membership, membership count and membership fingerprint for every visited
entity. Missing state or source evidence, malformed or partial membership,
cycles, dynamic selectors, unknown callables, overflow and processing failure
remain target-capable or coverage-failed. Retained candidate or membership
prefixes never prove exclusion.

Only an exact target inclusion, genuine target-capable opacity or
target-relevant coverage failure may attach a downstream action profile.
Risk, consequence, approval actionability, final-preflight drift, persisted
observability and operational locks consume that identical terminal set.

Planning persists and exposes the exact dependency-index generation,
fingerprint and source epoch used for classification. These are operator-audit
provenance. The target-scoped evidence fingerprint remains approval authority,
so a semantically identical fenced refresh may advance the generation without
making every plan permanently stale. Material candidate, label, domain,
selector, source, profile-effect or completeness drift changes the bound
evidence and is rejected before dispatch.

## Risk-model compatibility

New plans use `helper-dependency-risk-v8`. Persisted v3 through v7 evidence
remains readable with its historical hashes and classification, but requires a
fresh v8 plan and cannot authorize approval, current lock projection or
dispatch.

## Required source outcomes

The complete production-path regression runs synthetic Home Assistant
configuration through provider scan, dependency-index snapshot, public entity
analysis, helper plan creation, persisted plan evidence, policy and F3 lock
projection. Require:

- the standard target has zero exact obligations, zero target-relevant opacity,
  zero downstream profiles, complete evidence, no physical consequence, low
  risk, `standard_admin`, actionable approval and no downstream automation
  lock;
- the exact consequential target retains seven synthetic safety-critical
  automations, complete evidence, high risk, `elevated_admin`, actionable
  approval and all seven exact automation locks;
- an arbitrary selector remains opaque, incomplete, non-actionable and
  conservatively locked;
- generic groups, domain-specific groups and zones resolve recursively from
  complete snapshot evidence, while cycles and missing, malformed, partial or
  over-limit evidence stay conservative;
- obligation and downstream-profile traversal is deterministic across two
  complete reads and ends without provider dispatch.

Zero findings from public `entity_dependency_analysis` are observation only and
are never treated as a safety proof.

## Validation and release boundary

Require focused Beta 49 coverage; Beta 37-49 dependency, governance and F3
regressions; obligation-ledger adversarial/resource tests; HAMCP-089
observability and pagination; complete discovery; protected Fast, Full and
clean-head Evidence gates; isolated Beta 49 promotion-candidate validation;
compilation; YAML and PowerShell validation; dependency consistency; secret and
whitespace scans; strict pinned-dependency audit; stable-v1 comparison; exact
tool, task-schema and approval-authority accounting; and exact-head CI across
Home Assistant, exact ha-mcp, exact-image/readmission, packaging and architecture
lanes.

This feature task performs no merge, promotion, publication, deployment,
restart, live Home Assistant access, approval, apply or dispatch.

## Post-deployment acceptance — separate authorization

After independent approval, protected merge/promotion and separately authorized
deployment, rebuild the dependency index and rerun the complete Beta 48
read-only matrix. Traverse all obligations and profiles twice. Require the
standard helper to be complete, unrelated, low/standard and actionable, and the
consequential helper to retain every genuine safety-critical relationship while
remaining elevated and actionable. Confirm index identities and fingerprints
agree with the persisted plans and that no approval, apply, dispatch, fallback,
lock or Home Assistant state change occurred.

Only a full read-only pass may unlock separate HAMCP-089 canary authorization.
The native automation `invalid_request` / `ValueError` apply failure remains a
separate canary and is not corrected or accepted by Beta 49.
