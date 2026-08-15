# Engineering 2.2.0-beta.39 release notes

Beta 39 is a smoke-test correction for the exact governed `input_boolean`
state capability. It changes only specialized helper-risk classification and
the approval projection for dependency-ineligible helper plans. Beta 38
remains advertised until the protected promotion workflow separately publishes
Beta 39.

## Selector-aware helper dependency risk

The shared dependency extractor now records whether one bounded dynamic
reference actually contains a Home Assistant entity selector. A reviewed local
`set` or `for` binding that shadows a global helper name is ordinary template
dataflow only when its bounded value provenance is proven non-entity. Known
callable aliases of reviewed Home Assistant entity helpers retain the canonical
helper's exact selector semantics; unproven callable aliases remain
non-conclusive. A uniquely proven `states` alias also retains canonical bracket,
dot, bare-collection, and iteration semantics. Mixed or unknown collection
aliases remain non-conclusive. Aliases crossing deliberately unreviewed macro or
`with` scopes retain bounded uncertainty instead of disappearing from evidence.
Reviewed helpers stored in finite literal mappings now retain the same semantics
when consumed directly through literal dot or string-key member access. Dynamic
keys that may select a helper or incomplete value, and mixed, missing,
malformed, or incomplete members, remain non-conclusive. A mapping whose every
possible selected value is proven non-helper remains low-friction non-selector
dataflow.
The record remains bounded and approval-bound with its reference kind,
selector-presence flag, expression fingerprint, source identity, and
`target_membership: not_applicable` classification.

The specialized helper-risk service excludes only those mechanically proven
non-selector records from target-membership uncertainty. It does not use
configuration paths such as `message` or `signature` as a safety decision.
Finite sensor-only selector candidates continue to exclude an exact
`input_boolean`; finite candidates containing the helper remain dependencies.
Unbounded `states(variable)`, computed selector inputs, unknown macros, dynamic
labels, unrestricted state iteration, failed evidence, truncation, and partial
coverage remain non-conclusive.

Template binding dataflow is deliberately small and static. Assignment
right-hand sides are analyzed before a new local binding takes effect. Bindings
inside conditional or otherwise uncertain scopes are not used to suppress a
possible global entity selector. Templates are never executed.

Non-selector evidence is deterministically bounded and hashed. A change from
ordinary formatting dataflow to a target-relevant entity selector changes the
dependency-risk fingerprint. Final execution preflight rejects the old plan and
approval before dispatch.

The Beta 38 live regression fixture contains nine ordinary formatting/state
summary records and two finite sensor-selector records. With complete
automation and blueprint coverage and zero real helper dependencies, Beta 39
classifies the fixture complete, no-consequence, low/standard, and
execution-eligible. No helper identity is hardcoded in production logic.

## Helper approval actionability

An exact helper-state plan whose bound dependency evidence is incomplete or
execution-ineligible is no longer presented as approval-actionable. It has no
`approve_change_plan` next operation, and approval preparation fails before a
challenge, task, provider call, or dispatch. The operator must resolve the
dependency evidence and create a fresh plan.

Complete harmless helpers remain approval-actionable at standard authority.
Complete consequential helpers remain approval-actionable with the existing
elevated acknowledgement. Prohibited plans remain non-actionable. This is a
helper-local projection and enforcement correction, not a global approval
lifecycle change.

## Preserved boundaries

- Public helper inputs remain `entity_id`, `desired_state`, and
  `expiration_minutes`; only exact `input_boolean.*` and `on|off` are accepted.
- Exact `input_boolean.turn_on`/`turn_off`, target binding, durable intent, one
  dispatch attempt, authoritative reread, duplicate suppression, response-loss
  reconciliation, locks, and zero fallback are unchanged.
- `get_server_health` still attributes the provider to
  `direct_home_assistant_state`, contract
  `direct-ha-exact-input-boolean-v1`, fallback `none`.
- Static registration remains 51 tools: 25 canonical and 26
  Engineering-native. Approval authority remains 3 and task schema remains 1.
- Stable v1.1.2, ha-mcp admission, dashboards, mobile navigation, workflows,
  container/deployment configuration, and all other HAMCP-089 domains remain
  unchanged.
- Source and disposable CI do not access or mutate household Home Assistant.

Live acceptance remains separate and requires a separately approved deployment
and separately approved `off -> on -> off` canary.
