# Engineering 2.2.0-beta.39 acceptance

This procedure applies only after independent source review, protected merge,
promotion, publication, separately authorized deployment, and separately
authorized live mutation. Development must not access the deployed MCP server
or household Home Assistant.

## Source and release gates

1. Confirm the feature base is current `main` after Beta 38 promotion,
   advertised Engineering source is `2.2.0-beta.38`, stable is `1.1.2`, and
   `.release/next-version` stages only `2.2.0-beta.39`.
2. Confirm static registration remains exactly 51 tools: 25 canonical and 26
   Engineering-native. Approval authority must remain 3, task schema 1, and
   fallback `none`.
3. Confirm the public helper schema still accepts only exact
   `input_boolean.<object_id>`, explicit `on|off`, and bounded expiration. No
   toggle, arbitrary service/data, physical domain, delegated write, or fallback
   may be reachable.
4. Require focused tests for selector/non-selector classification, finite target
   inclusion/exclusion, local binding dataflow, uncertain scopes, unknown
   macros, dynamic labels, static dependencies, consequential dependencies,
   incomplete coverage, evidence bounds, drift, actionability, challenge
   refusal, locks, provider health, duplicate apply, response loss, and recovery.
5. Require Full and Evidence gates, unit discovery, compilation, dependency and
   vulnerability checks, YAML, PowerShell, secret and whitespace checks,
   stable-v1 comparison, exact-image/readmission, exact ha-mcp, and disposable
   Home Assistant 2026.7.2, 2026.8.0, and 2026.8.1 jobs.

## Specialized helper-risk contract

For one exact helper, only a dynamic reference containing a supported entity
selection or lookup can affect target-membership completeness. A reviewed local
binding that shadows a Home Assistant helper name is classified as ordinary
template dataflow only when bounded static evidence proves both the binding and
its value are non-entity dataflow. A known callable alias of `states`,
`state_attr`, `is_state`, `is_state_attr`, `has_value`, `expand`, or another
reviewed entity helper retains that helper's selector semantics. An unproven
callable binding remains non-conclusive. A uniquely proven `states` alias also
retains bracket, dot, bare-collection, and iteration semantics. Exact bracket or
dot targets remain exact dependencies; bare state collections remain
non-conclusive. Mixed, unknown, or incomplete collection provenance must never
be represented as ordinary formatting. A reviewed entity helper stored in a
finite literal mapping retains its canonical semantics when consumed directly
through a literal dot or string-key member path; copying the member to a second
variable is not required. Dynamic keys that can select a helper or incomplete
value, and mixed, missing, malformed, or incomplete member provenance, remain
non-conclusive. A mapping whose every possible selected value is proven
non-helper remains ordinary template dataflow.
Configuration path names are diagnostic and never decide selector semantics.

Each excluded non-selector record binds bounded source identity, reference kind,
selector presence, normalized expression fingerprint, and
`target_membership: not_applicable`. Non-selector evidence and overflow evidence
must remain deterministic and bounded.

The following remain non-conclusive:

- unbounded `states(variable)` and `states[dynamic_value]`;
- unknown macros or functions that may produce entity IDs;
- callable bindings whose provenance cannot be proven non-entity;
- mixed or unknown aliases used with call, bracket, dot, or collection
  iteration syntax;
- dynamic mapping keys that may select a helper or incomplete value, or mapping
  members with mixed, missing, malformed, or incomplete helper provenance;
- entity-helper aliases crossing macro, `with`, or another deliberately
  unreviewed Jinja scope unless the bounded grammar proves the exact selector;
- dynamic/computed label and registry selectors;
- unrestricted iteration over Home Assistant states;
- conditionally shadowed or otherwise uncertain helper names;
- failed, partial, stale, malformed, truncated, or over-limit evidence.

Finite exact sensor-only candidates exclude an `input_boolean`. Finite candidates
containing the helper remain relevant. Static dependencies remain relevant, and
consequential downstream actions retain proportional elevated governance.

The specialized regression fixture models
`input_boolean.mcp_f2_standard_admin_test_flag` with complete automation and
blueprint coverage, zero static dependencies, nine ordinary message/signature/
formatting records, and two finite geolocation sensor expressions. It must
produce complete evidence, zero relevant downstream automations, no consequence,
low/standard classification, and execution eligibility without a production
helper allowlist.

A change from ordinary formatting dataflow to a possible entity selector must
change the material evidence fingerprint. Final helper preflight must reject the
old plan before dispatch and require a fresh plan and approval.

## Approval actionability contract

- A complete harmless helper is approval-actionable at standard authority.
- A complete consequential but reviewable helper is approval-actionable at the
  existing proportional authority level.
- An evidence-incomplete or execution-ineligible helper plan is not
  approval-actionable, has no `approve_change_plan` next operation, and cannot
  create, view, or decide an approval challenge.
- A prohibited plan is never approval-actionable.
- Approval preparation never creates an execution task or provider dispatch.

This is local to exact helper-state plans and does not redefine the global
approval lifecycle.

## Provider and execution preservation

`get_server_health` must continue to report provider
`direct_home_assistant_state`, contract
`direct-ha-exact-input-boolean-v1`, and fallback `none`, with both REST and
read-only WebSocket evidence required for healthy/available status.

Execution must preserve external approval binding, durable intent, one exact
`input_boolean.turn_on` or `input_boolean.turn_off` dispatch, exact entity target,
authoritative readback, duplicate suppression, response-loss readback first, no
blind redispatch, common helper/configuration/reload locks, and zero fallback.

## Post-deployment read-only gate

Before mutation, confirm exact version/build identity, tool catalog and schemas,
provider attribution and health, Home Assistant connectivity, approval authority,
task schema, governance storage, dependency-index freshness, complete automation
and blueprint coverage, and zero failed automation reads. Stop on any mismatch.

## Separately authorized live canary

Candidate: `input_boolean.mcp_f2_standard_admin_test_flag`.

Re-read the pre-state; the expected smoke-test state was `off`, but it must not be
assumed. Proceed only when the exact pre-state remains `off` and dependency
evidence is complete, zero-dependency, no-consequence, low/standard, and the
exact helper provider is healthy with fallback zero.

1. Create the exact `off -> on` plan and obtain Josh's separate approval.
2. Require durable intent, exactly one dispatch, authoritative `on` reread, and
   terminal verified success.
3. Reapply the completed plan and require duplicate suppression with zero second
   dispatch.
4. Create a fresh `on -> off` plan and obtain separate approval.
5. Require exactly one dispatch, authoritative `off` reread, verified cleanup,
   and fallback zero.
6. Request `off` again and require a verified no-change response with no plan.

Any dependency, selector classification, coverage, freshness, provider, or state
drift stops acceptance before dispatch. Source validation does not authorize this
live mutation.

## Non-goals

No dashboard, HAMCP-106, Android/iOS, Supervisor identity, historical projection,
scene, group, script, generic service, new helper family, physical action,
provider-route, credential, deployment, or fallback work belongs to Beta 39.
