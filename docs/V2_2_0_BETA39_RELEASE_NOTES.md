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
Mapping access now follows Jinja's attribute-first dot semantics and item-first
bracket semantics. The bounded analyzer recognizes the read-only dictionary
methods `get`, `items`, `keys`, and `values`: finite helper/candidate provenance
is retained, uncertain helper-capable method flows remain non-conclusive, and
proven ordinary method results remain low-friction. Same-named literal mapping
items are still reachable through bracket access. Method arguments remain part
of dependency analysis because Jinja evaluates them before the method call.
Bound `get`, `items`, `keys`, and `values` method aliases retain the same bounded
mapping provenance when invoked later or transported through another finite
mapping, so assignment does not erase an exact or conservative helper
dependency. Grouping and bounded finite list, tuple, mapping, or conditional
transport now preserve the same callable provenance through later invocation
or `states` subscription. Exact finite selections remain exact; dynamic, mixed,
malformed, or over-limit selections remain incomplete and conservatively
locked. Merely displaying or carrying a value proven ordinary remains
low-friction and does not create selector evidence. Conditional tests, lookup
keys, and other eager selector operands remain evidence rather than being
mistaken for transported values. This closes the prior gap where a closing
parenthesis or container boundary could terminate provenance before the
callable was consumed.
Bracket method-name access uses the literal item when present and
the reviewed mapping method when the item is absent. Dynamic bracket keys now
retain both bounded item possibilities and unshadowed read-method fallbacks.
Compatible fallback consumption that can return a reviewed entity helper is
conservative. Unreviewed attribute fallbacks such as `copy` remain incomplete
when consumed, and dynamic argument unpacking does not bypass that boundary. A
finite key proven to be `get` remains exact, and finite entirely ordinary
possibilities remain low-friction. Entity lookups used to compute a bracket key
are scanned eagerly. Fallback bases share one bounded provenance projection so
repeated local bindings retain near-linear scan cost. A bare reviewed helper
used as a bounded default retains value provenance; an actual helper call in a
method argument remains an eager dependency.
Conditional mapping alternatives retain bounded key-presence evidence, so a
default remains part of selector provenance whenever any branch can select it.
Incomplete bases cannot be promoted to complete by a method call. Consuming the
all-state collection inside a method argument, exceeding the bounded argument
depth, or retrieving a bound method through unsupported direct `attr` or
collection `map` projection remains conservative rather than disappearing from
evidence. Quoted display text is not treated as an operator, and the reviewed
exact `map(attribute='entity_id')` path remains target-specific.
Unsupported pipeline results that immediately feed a call, subscription, or
member access retain bounded helper-bearing input provenance; proven ordinary
inputs remain low-friction. Malformed projection scans stop after one bounded
limit result instead of rescanning an unmatched suffix.
Reviewed filter signatures bind only values that can enter pipeline output:
`default`, `map`, and `groupby` fallbacks plus `batch`/`slice` fill values.
Selection, ordering, size, and boolean-mode arguments remain ordinary when they
cannot introduce helper provenance. Invalid signatures or structural delimiter
mismatches remain bounded incomplete evidence with conservative locking.
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
