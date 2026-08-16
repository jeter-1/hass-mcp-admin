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
   2026.8.1 source contracts. Offline regeneration must be byte-identical.
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
- unrelated automation work remains concurrent.

The approval fingerprint binds the semantic-registry identity, exact and
excluded candidates, opacity/failure reasons, external-template boundaries,
context provenance, potential automation set, action services/targets/data,
coverage and bounds, and lock projection. Final preflight repeats analysis
after locks are held. Any material change fails before dispatch and requires a
new plan and approval; display-only metadata must not cause false drift.

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
