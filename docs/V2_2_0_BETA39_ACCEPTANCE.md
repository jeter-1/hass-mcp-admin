# Engineering 2.2.0-beta.39 acceptance

Beta 39 is a corrective HAMCP-089 release. It replaces the helper-risk
planner's transport-specific template scanner with a bounded, whole-template
obligation ledger. This procedure authorizes neither publication nor live Home
Assistant access. Deployment and the household helper canary remain separate
decisions.

## Source and release gates

1. Resolve the feature base to current `main` after Beta 38 promotion. Require
   advertised Engineering `2.2.0-beta.38`, staged `2.2.0-beta.39`, stable
   `1.1.2`, approval authority 3, task schema 1, and fallback `none`.
2. Require exact staged-document resolution to this document. Public tool
   accounting remains 51: 25 canonical plus 26 Engineering-native tools.
3. Confirm the public helper operation still accepts only exact
   `input_boolean.<object_id>`, explicit `on|off`, and bounded expiration. No
   arbitrary service, data, target, physical domain, delegated write, or
   fallback may be reachable.
4. Confirm Jinja is parsed, never rendered. The checked-in semantic registry
   must bind Jinja 3.1.6 and the exact Home Assistant 2026.7.2, 2026.8.0, and
   2026.8.1 source contracts. Offline regeneration must be byte-identical, and
   generation must fail when a declared path/blob pair contradicts the
   captured official-source evidence or the installed pinned `Jinja2`
   distribution. The registry's semantics are reviewed against those releases;
   they are not bound to the connected instance's version, and nothing in this
   release reads the connected version.
5. Require focused obligation, target-projection, risk, actionability, drift,
   and F3 lock tests plus Full and Evidence gates. Require compilation,
   dependency consistency, strict vulnerability audit, YAML, PowerShell,
   secret, whitespace, metadata, stable-v1, promotion-candidate,
   exact-image/readmission, exact ha-mcp, and all three disposable Home
   Assistant lanes.

## Obligation-ledger invariant

Each complete Jinja template is parsed with shared binding and scope state. A
potential dependency source must terminate as exactly one of:

- `exact_dependency`;
- `proven_target_exclusion`;
- `proven_dependency_neutral`;
- `bounded_semantic_opaque`;
- `coverage_failure`.

No construct may disappear because exact provenance is lost. Unknown AST nodes,
call targets, receivers, filters, tests, imported content, parse failures, or
unsupported semantics must create explicit opaque or failure evidence. The
analyzer must enforce deterministic source-size, AST-node, depth, work,
binding, abstract-value, container, candidate, external-reference, and
obligation limits. It must never
load, render, compile, or execute a user template or call a Home Assistant
template helper.

The reviewed semantics must include the supported Home Assistant state helpers,
translated-state helpers, state/entity-set producers, dynamic filter/test
dispatch, ordinary methods with proven receiver provenance, local scopes and
macros, imports/includes/extends, and state-bearing `trigger`, `wait.trigger`,
and `this` contexts. Mapping, sequence, and namespace receivers must retain
their distinct Jinja lookup/iteration behavior, including finite positional
construction and method-name collisions. Arbitrary runtime context or helper
result strings may remain neutral when rendered, but must become exact or opaque
when later consumed as entity selectors. Static entity-bearing configuration roles must supply
bounded context where available. Comments, raw blocks, message formatting, and
proven ordinary values must remain low-friction.

Configuration context must follow Home Assistant execution semantics. Root
automation/script variables retain possible caller-supplied run-variable
overrides. A variables action renders its mapping in insertion order, makes each
completed assignment visible to the next value, skips `enabled: false`, joins a
dynamic `enabled` path, and cannot leak one parallel branch's bindings into a
sibling. Mapping order is material dependency evidence. Exact zone triggers seed
`trigger.zone`/`wait.trigger.zone`; Jinja loop and Home Assistant repeat values are
path-scoped; event/payload mappings and date/time values remain neutral when only
rendered and opaque when later used as entity selectors. Unused variable members
named `entity_id` are data, not dependencies merely by spelling.

Every supported Jinja AST node requires a reviewed transfer rule or a
conservative fallback. Historical Beta 37 through Beta 39 alias, mapping,
method, bracket, grouping, conditional, and finite-transport cases must never
produce an empty ledger. Metamorphic wrapping may move evidence between exact
and conservative outcomes, but may not erase it.

## Target projection, risk, and actionability

Exact finite candidates that contain the planned helper are dependencies.
Exact candidates or domains that exclude it become explicit target exclusions.
Proven dependency-neutral obligations do not create false dependency risk.

Bounded semantic opacity is distinct from missing evidence. It may remain
execution-eligible only when automation inventory is complete and current,
each opaque source has a known bounded automation/configuration identity, its
downstream effect profile is present, the complete potential automation set is
bounded, and the required conservative locks can be acquired. The response
must disclose opacity and its reason. Proven-benign downstream effects may
remain low/standard; consequential or unknown effects remain elevated/high.

Coverage failure is never approval-actionable. It includes failed or stale
inventory, provider failure, identity-losing truncation, unbounded source or
candidate overflow, missing required action profiles, evidence-generation
failure, or an unbound conservative lock scope. Approval preparation never
creates a task or dispatch.

The authoritative obligation coverage is evaluated independently of the legacy
bounded dependency-graph projection. Automation inventory and blueprint-source
limits, event-selector limits, template/AST/value limits, and target-relevant
identity/profile limits must each fail explicitly. Compatibility projection
clipping alone must not erase or downgrade retained authoritative ledger evidence.

## Lock and drift contract

Risk and F3 must consume the same obligation ledger. Every helper execution
holds the exact helper-dependency key and the shared conservative introduction
guard through final preflight, dispatch, readback, and verification. Automation
configuration work derives exclusive keys from its current and proposed
content: exact dependencies take the matching exact key; bounded opacity takes
the conservative key; proven target exclusions and dependency-neutral content
take neither. Therefore:

- exact dependencies add every relevant automation resource lock;
- bounded opacity adds every known potentially relevant automation lock;
- target-excluded automation work remains concurrent because it requests no
  helper-dependency key;
- relevant automation reloads conflict; external-template opacity also binds a
  deterministic custom-template reload identity when represented by the lock
  model;
- raw `use_blueprint` configuration is a bounded external-source obligation
  until the exact blueprint source is read, resolved, and analyzed. F3
  configuration projection has no source body and therefore takes the
  conservative helper-dependency key for blueprint create, update, and removal.
  Provider-side discharge must bind the raw configuration/path fingerprint,
  resolved configuration fingerprint, and complete resolved obligation ledger;
  no caller assertion may suppress the raw obligation;
- unrelated automation work remains concurrent.

The approval fingerprint binds the semantic-registry identity, exact and
excluded candidates, opacity/failure reasons, external-template boundaries,
context provenance, potential automation set, action services/targets/data,
coverage and bounds, and lock projection. Final preflight repeats analysis
after locks are held. Any material change fails before dispatch and requires a
new plan and approval; display-only metadata must not cause false drift.

## Merge gate: opacity measurement

This measurement is a deliverable, not an optional diagnostic. The six
corrections can all be implemented perfectly and still leave the capability
unusable; this is the only number that distinguishes those outcomes before
deployment.

The analyzer was run offline over a sanitized snapshot of the operator's real
`automations.yaml`, taken from the private configuration repository. No live
Home Assistant or deployed MCP endpoint was contacted. Only aggregate counts
and reason codes are recorded here; no automation body, entity id, friendly
name, or selector value is reproduced.

| Measure | Corrected head | Reviewed head `2a7efb9` |
| --- | --- | --- |
| Automations analyzed | 89 | 89 |
| Automations unreadable | 0 | 0 |
| Classified exact | 67 (75.28%) | 63 (70.79%) |
| Classified opaque or conservative | 22 (24.72%) | 26 (29.21%) |
| Coverage failures | 0 | 0 |
| Obligations emitted | 2100 | 2101 |
| Exact-lock obligations | 1136 | 1136 |
| Conservative-lock obligations | 94 | 106 |

Distinct causes of opacity at the corrected head, by frequency:

| Reason code | Automations affected | Obligations |
| --- | --- | --- |
| `unknown_callable_binding` | 16 | 42 |
| `state_value_consumed_by_filter_target_opaque` | 2 | 10 |
| `unknown_attribute_receiver` | 6 | 7 |
| `states_entity_access_target_opaque` | 3 | 7 |
| `label_entities_entity_set_membership_unavailable` | 2 | 6 |
| `states_item_entity_access_target_opaque` | 1 | 4 |
| `blueprint_source_unavailable_to_local_analysis` | 3 | 3 |
| `state_collection_iterated_target_opaque` | 2 | 3 |
| `membership_iterates_state_value_target_opaque` | 2 | 3 |
| `state_object_last_changed_access_target_opaque` | 1 | 2 |
| `entity_name_registry_lookup_opaque` | 2 | 2 |
| `state_object_attributes_access_target_opaque` | 1 | 2 |
| `state_attr_entity_access_target_opaque` | 1 | 2 |
| `is_state_entity_access_target_opaque` | 1 | 1 |

Reading of the result. About three quarters of the operator's automations
classify exactly, so an ordinary helper flip shows one approval rather than an
elevated one. Correcting the standard Jinja vocabulary (B39-136-R6) removed
`unknown_filter_round` and `unknown_filter_max` entirely and moved four
automations from conservative to exact.

The dominant residual cause is `unknown_callable_binding`, and its content is
almost entirely Home Assistant's own template extensions rather than standard
Jinja - `as_timestamp` accounts for the large majority, with
`timedelta.total_seconds` behind it. Extending the reviewed vocabulary to
Home Assistant's documented template functions is the obvious next lever, and
it is a separate design decision rather than another correction round.

## Merge gate: per-helper tier measurement

The opacity measurement above describes **automations**. It does not answer the
question that decides whether the capability is usable, which is about
**helpers**: when the operator flips one of their actual `input_boolean`
helpers, what approval tier does the plan land in? This section answers that.

### Method and provenance

Run offline against the same sanitized configuration snapshot. No live Home
Assistant or deployed MCP endpoint was contacted. The automation-level
measurement above was produced by ad hoc scratchpad tooling that was never
committed; this measurement uses a separate offline harness that mirrors
`DirectHaDependencyProvider.scan` to build a real `DependencyIndex` snapshot,
then for each helper calls the production
`build_helper_dependency_risk_binding` and the production
`evaluate_change_policy` classifier over a real `ChangePlan`. The tier logic is
never reimplemented; only the counterfactual attribution below is computed
outside production code, and it is computed from the production binding's own
`downstream_profiles`.

Helper population: every `input_boolean` entity referenced anywhere in the
snapshot (automations, scripts, scenes, and `configuration.yaml`) - 16
entities. Helper definitions themselves live in `.storage`, which the operator's
configuration repository excludes, so a helper with no reference anywhere cannot
appear here. That omission does not soften the result: the two measured helpers
with zero proven relationships behave exactly as an unreferenced helper would,
and both are non-actionable.

Helper identifiers are redacted to stable indices. The index-to-entity mapping
was kept locally and is not committed.

### Per-helper rows

| helper | policy_class | risk_delta | physical_consequence | evidence_complete | execution_eligible | semantic_precision | relevant | proven | opaque-only | primary reason | lock class |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `helper_01` | elevated_admin | high | indirect | no | no | coverage_failure | 43 | 21 | 22 | `action_profile_truncated` | `exact_helper_dependency`, `conservative_dependency` |
| `helper_02` | elevated_admin | high | indirect | no | no | coverage_failure | 25 | 3 | 22 | `action_profile_truncated` | `exact_helper_dependency`, `conservative_dependency` |
| `helper_03` | elevated_admin | high | indirect | no | no | coverage_failure | 28 | 6 | 22 | `action_profile_truncated` | `exact_helper_dependency`, `conservative_dependency` |
| `helper_04` | elevated_admin | high | indirect | no | no | coverage_failure | 30 | 8 | 22 | `action_profile_truncated` | `exact_helper_dependency`, `conservative_dependency` |
| `helper_05` | elevated_admin | high | indirect | no | no | coverage_failure | 33 | 15 | 18 | `action_profile_truncated` | `exact_helper_dependency`, `conservative_dependency` |
| `helper_06` | elevated_admin | high | indirect | no | no | coverage_failure | 22 | 1 | 21 | `action_profile_truncated` | `exact_helper_dependency`, `conservative_dependency` |
| `helper_07` | elevated_admin | high | indirect | no | no | coverage_failure | 24 | 2 | 22 | `action_profile_truncated` | `exact_helper_dependency`, `conservative_dependency` |
| `helper_08` | elevated_admin | high | indirect | no | no | coverage_failure | 23 | 2 | 21 | `action_profile_truncated` | `exact_helper_dependency`, `conservative_dependency` |
| `helper_09` | elevated_admin | high | indirect | no | no | coverage_failure | 23 | 5 | 18 | `action_profile_truncated` | `exact_helper_dependency`, `conservative_dependency` |
| `helper_10` | elevated_admin | high | indirect | no | no | coverage_failure | 22 | 2 | 20 | `action_profile_truncated` | `exact_helper_dependency`, `conservative_dependency` |
| `helper_11` | elevated_admin | high | indirect | no | no | coverage_failure | 23 | 1 | 22 | `action_profile_truncated` | `exact_helper_dependency`, `conservative_dependency` |
| `helper_12` | elevated_admin | high | indirect | no | no | coverage_failure | 22 | 1 | 21 | `action_profile_truncated` | `exact_helper_dependency`, `conservative_dependency` |
| `helper_13` | elevated_admin | high | indirect | no | no | coverage_failure | 26 | 4 | 22 | `action_profile_truncated` | `exact_helper_dependency`, `conservative_dependency` |
| `helper_14` | elevated_admin | high | indirect | no | no | coverage_failure | 23 | 1 | 22 | `action_profile_truncated` | `exact_helper_dependency`, `conservative_dependency` |
| `helper_15` | elevated_admin | high | indirect | no | no | coverage_failure | 22 | 0 | 22 | `action_profile_truncated` | `exact_helper_dependency`, `conservative_dependency` |
| `helper_16` | elevated_admin | high | indirect | no | no | coverage_failure | 22 | 0 | 22 | `action_profile_truncated` | `exact_helper_dependency`, `conservative_dependency` |

"relevant" is the count of automations the binding treats as potential
dependents. "proven" is the subset reached through an exact dependency on that
helper. "opaque-only" is the subset attached **solely** by bounded semantic
opacity, with no proven relationship to that helper.

### Aggregate

| Outcome | Count | Share |
| --- | --- | --- |
| Total `input_boolean` helpers analyzed | 16 | 100% |
| STANDARD_ADMIN | 0 | 0.00% |
| ELEVATED_ADMIN | 16 | 100.00% |
| PROHIBITED | 0 | 0.00% |
| Approval-actionable plans | 0 | 0.00% |

Every helper is additionally **non-actionable**: `execution_eligible` is false,
so `_helper_dependency_plan_is_actionable` returns false and no approval
challenge can be created. Verified by calling the production predicate directly
for all 16. Elevated is therefore the classification, not the outcome - the
outcome is that no helper flip can be approved at all.

Reason codes for the non-standard outcomes, all 16 helpers identically:

| Reason code | Helpers |
| --- | --- |
| `exact_input_boolean_state_elevated_policy` | 16 |
| `helper_dependency_coverage_failure` | 16 |
| `low_risk_not_established` | 16 |
| `action_profile_truncated` (coverage failure cause) | 16 |

### Pushed off standard solely by an unrelated opaque automation

This is the figure the measurement exists to produce, reported at both layers
the model applies.

| Attribution | Helpers |
| --- | --- |
| Would be STANDARD_ADMIN if obligations with no proven relationship were absent | **2 of 16** |
| Would be execution-eligible (approvable at all) if those obligations were absent | **12 of 16** |
| Coverage failure caused **only** by opaque-only automations | **12 of 16** |
| Coverage failure with at least one genuinely proven cause | 4 of 16 |
| Helpers with zero proven relationships that are still non-actionable | 2 of 16 |
| `physical_consequence: safety_critical` inherited only from opaque-only automations | **13 of 16** |
| `physical_consequence: safety_critical` from a proven dependency | 3 of 16 |

Every helper is coupled to 18-22 opaque-only automations. That number tracks the
22 opaque automations in the measurement above: an automation that is opaque for
any reason becomes a potential dependent of **every** helper, because
`_obligation_targets_helper` projects an opaque obligation to
`bounded_semantic_opaque` for every target it cannot exclude.

Two consequences follow. First, the binding's `physical_consequence` is
`safety_critical` for all 16 helpers, while only 3 have a proven
safety-critical dependency. Second, and decisively, 5 of the 89 automations
carry a clipped effect projection; through the same coupling, 2 of them reach
every helper and raise `action_profile_truncated`, which is a coverage failure
rather than mere elevation. For 12 of 16 helpers that coverage failure has no
proven cause at all.

### Reading of the result

This is the third case in the merge-gate criteria: **most helpers land
non-standard because unrelated opaque automations are treated as potential
dependents.** Reporting and stopping is the required response; no correction was
attempted.

The result is stronger than the criterion anticipated. The expectation was that
over-coupling might push helpers from standard to elevated. What it actually
does is push every helper into coverage failure, which is not approvable at all.
On this configuration the capability cannot operate on any of the operator's 16
helpers.

Two qualifications, both stated so the finding is not read as larger than it is.
Even with helper-scoped opacity, most helpers would remain ELEVATED_ADMIN: 11 of
16 have a proven dependency whose effect projection is `unknown`, and 3 have a
proven safety-critical dependency. That part is correct behavior and reflects
the operator's helpers genuinely gating physical actions - 66 of 89 automations
project `unknown` and 12 project `safety_critical`. The change that helper
scoping would make is not mainly standard-versus-elevated; it is
approvable-versus-not-approvable, for 12 of 16 helpers.

Second, 3 automations use blueprints whose source the offline harness cannot
resolve, so they are opaque here where the live provider would resolve them.
They are not the cause of the coverage failure: none of the 5 automations with
clipped effect projections uses a blueprint, and the effect projection is
computed from configuration alone, so it is identical live.

The design question this raises - whether bounded opacity should be
helper-scoped rather than global, and whether a clipped effect projection on one
unrelated automation should be able to make every helper non-actionable - is
outside the scope of the correction round and is left for that conversation.

## Preserved operational acceptance

Retain exact off-to-on and on-to-off positive paths, durable intent before one
dispatch, authoritative reread, `succeeded_verified`, duplicate suppression,
readback-first response-loss handling, audit/provider attribution, exact
`direct_home_assistant_state` contract
`direct-ha-exact-input-boolean-v1`, and zero fallback. Disposable Home Assistant
acceptance is required on 2026.7.2, 2026.8.0, and 2026.8.1.

## Post-deployment canary (documented, not authorized here)

The candidate remains `input_boolean.mcp_f2_standard_admin_test_flag`. Re-read
its state and dependency evidence; do not assume either. Proceed only after a
separately authorized deployment and separate approval for each mutation.

Expected sequence when the pre-state is still `off`:

1. Require current bounded evidence, truthful risk/actionability, exact provider
   health, and fallback `none`.
2. Approve and verify one exact `off -> on` dispatch.
3. Reapply the completed plan and require zero additional dispatch.
4. Create and separately approve a fresh `on -> off` plan.
5. Verify exact cleanup and a final no-change request with no plan.

Stop on any source identity, effect, opacity, coverage, lock, state, provider,
version, or freshness mismatch.

## Non-goals

No custom-template retrieval, general Jinja interpreter, new tool, public
schema redesign, new write authority, generic service forwarding, provider
routing, fallback, dashboard, mobile-navigation, HAMCP-106, workflow,
container, deployment, stable-v1, merge, promotion, publication, or live-system
work belongs to Beta 39.
