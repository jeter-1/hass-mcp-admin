# Engineering 2.2.0-beta.50 acceptance

Beta 50 stages the production obligation-producer correction for the deployed
Beta 49 HAMCP-089 planning failure. Beta 49 remained fail-closed: neither the
standard-helper nor consequential-helper plan was approval-actionable, and no
approval, apply, provider dispatch or Home Assistant mutation occurred.

Engineering 2.2.0-beta.49 remains advertised until a separately authorized
protected promotion. Stable v1.1.2 and the 51-tool Engineering registration are
unchanged.

## Read-only deployed evidence

The persisted standard plan `4f8c780072d744c2895b97f4834d41a7`
was traversed twice before implementation. Both complete reads returned the
same 59 logical obligations in four pages and the same six logical downstream
profiles in two pages. The obligation full-set fingerprint was
`d8153205b2998fbcd9317a4748004091819bdf8b69f14eb03ec095da838c2b1b`;
the profile fingerprint was
`9a42cf3e29870cf1107ed3c7084e896a695ecbab653719c66c98366d255ec981`.
Both terminal cursors were null, and the read path reported zero Home Assistant
and upstream requests.

The bounded sanitized capture is committed as
`tests/fixtures/beta50_deployed_beta49_residual_matrix_v1.json`. It retains the
reason, obligation kind, source/path class, selector scope, candidate/domain
shape, provenance completeness and lock projection for all 59 records as
deterministic counts. Source, selector and entity identities are pseudonymized;
no raw household configuration or entity inventory is retained.

The deployed residual reason classes were:

| Reason | Count |
|---|---:|
| `state_value_consumed_by_filter_target_opaque` | 13 |
| `states_domain_object_entity_access_target_opaque` | 8 |
| `states_entity_access_target_opaque` | 7 |
| `label_entities_entity_set_membership_unavailable` | 6 |
| `states_item_entity_access_target_opaque` | 4 |
| `membership_iterates_state_value_target_opaque` | 3 |
| `state_collection_iterated_target_opaque` | 3 |
| `state_attr_entity_access_target_opaque` | 2 |
| `state_bearing_context_rendered_target_opaque` | 2 |
| `state_object_last_changed_access_target_opaque` | 2 |
| `trigger_context_entity_opaque` | 2 |
| `unknown_context_attribute` | 2 |
| `entity_name_registry_lookup_opaque` | 2 |
| `is_state_entity_access_target_opaque` | 1 |
| `unknown_attribute_receiver` | 1 |
| `unknown_callable_binding` | 1 |

## Producer correction

The helper aggregation layer already ignored obligations terminally classified
as complete target exclusions or dependency-neutral. Beta 49 instead lost
proof before that boundary:

- fixed `numeric_state` trigger contracts did not seed the finite State-object
  provenance supplied by their configured entity set;
- unresolved `states[entity]` transport discarded complete literal-label
  producer and selector provenance before provider-side membership resolution;
- reviewed collection-member attribute projection consumed an exact State
  relationship and then reattached the uncertain collection receiver to its
  scalar result; and
- admitted collection filters with an omitted or explicit-null optional
  `attribute` argument could consume State members without emitting the input
  relationship; and
- `entity_name(entity)` emitted a new unbound registry opacity instead of
  consuming its bounded entity input and returning a tainted scalar.

Beta 50 preserves those proofs at the producer. Exact State, label, finite
candidate and closed-domain relationships terminate before downstream-profile
selection. Reviewed State attributes, trigger context identity, datetime/time
arithmetic, formatting and registry display-name results are dependency-neutral
after their input relationship is bound. A resulting scalar remains tainted: if
it is later supplied to `states()`, `state_attr()` or another entity selector
without finite identity, it becomes target-capable opacity.

Unknown or caller-supplied selectors, dynamic labels without complete registry
evidence, mixed/incomplete provenance, imported templates, unknown callables,
malformed or cyclic expansion, overflow and coverage failure remain
conservative. An exact inclusion is never erased by an exclusion hint, and
coverage failure retains known exact locks plus the unconstrained guard.

Risk, consequence, approval actionability, downstream-profile selection,
evidence fingerprints, final-preflight drift and F3 locking consume the same
immutable terminal set. Candidate, label, domain, source, selector or
completeness drift changes the approval-bound evidence and rejects before
dispatch. Exact and completely excluded evidence uses target-specific locks;
the unconstrained helper-dependency guard is retained only for surviving
opacity or coverage failure.

## Risk-model and snapshot identity

New plans use `helper-dependency-risk-v9`. Persisted v3 through v8 evidence
remains readable with its historical hashes and classification, but requires a
fresh v9 plan and cannot authorize approval, current lock projection or
dispatch.

Each plan performs a fresh bounded dependency assessment and persists the exact
generation, fingerprint and source epoch used for its classification. Two
sequential plans may legitimately use different generations; each plan's
observability must agree exactly with its own bound identity.

## Required source outcomes

The production-path regression runs sanitized synthetic Home Assistant
configuration through `DirectHaDependencyProvider`, dependency-index snapshot,
target analysis, obligation resolution, helper planning, persisted plan
observability and operational lock projection. Require:

- the standard target has zero exact obligations, zero target-relevant opacity,
  zero downstream profiles, complete evidence, no physical consequence, low
  risk, `standard_admin`, actionable approval and no downstream automation or
  unconstrained helper-dependency lock;
- the exact consequential target retains seven synthetic safety-critical
  automations, complete evidence, high risk, `elevated_admin`, actionable
  approval and all seven exact automation locks;
- an arbitrary selector remains opaque, incomplete, non-actionable and
  conservatively locked;
- a State collection consumed by an optional-attribute filter without an
  `attribute` argument remains exact, excluded or conservative according to
  its input scope and can never disappear as neutral output;
- coverage failure remains monotonic and fail-closed;
- two complete obligation/profile traversals are deterministic, terminate with
  a null cursor and perform no provider dispatch.

## Validation and release boundary

Require focused Beta 50 coverage; Beta 37-50 dependency, governance and F3
regressions; obligation-ledger adversarial/resource tests; HAMCP-089
observability and pagination; complete discovery; protected Fast, Full and
clean-head Evidence gates; isolated Beta 50 promotion-candidate validation;
compilation; YAML and PowerShell validation; dependency consistency; secret and
whitespace scans; strict pinned-dependency audit; stable-v1 comparison; exact
tool, task-schema and approval-authority accounting; and exact-head CI across
Home Assistant, exact ha-mcp, exact-image/readmission, packaging and architecture
lanes.

This feature task performs no merge, promotion, publication, deployment,
restart, approval, apply, dispatch or Home Assistant mutation. The bounded
persisted-plan traversal was the only deployed-system access.

## Post-deployment acceptance — separate authorization

After independent approval, protected merge/promotion and separately authorized
deployment, rebuild the dependency index and create fresh planning-only
standard-helper and consequential-helper controls. Traverse all obligations and
profiles twice. Require the standard helper to be complete, unrelated,
low/standard and actionable, and the consequential helper to retain every
genuine safety-critical relationship while remaining elevated and actionable.
Confirm each plan's index identity and evidence fingerprint and confirm that no
approval, apply, dispatch, fallback, lock or Home Assistant state change
occurred.

Only a full read-only pass may unlock separate HAMCP-089 canary authorization.
The configuration-integrity parser and held-read taxonomy remain separate work.
