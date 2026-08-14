# Engineering 2.2.0-beta.38 release notes

Beta 38 is the smallest correction after Beta 37. It preserves the exact
governed `input_boolean` on/off capability and changes only its dependency
evidence precision and its provider attribution in `get_server_health`.
Beta 37 remains advertised until the protected promotion workflow separately
publishes Beta 38.

## Target-specific bounded dynamic references

The dependency extractor now recognizes a closed static-analysis grammar for
finite entity candidates. Supported evidence includes literal entity lists,
literal maps and dictionary fields, finite conditional unions, bounded nested
unions, and literal `label_entities('exact-label')` selectors combined with
explicit finite lists. The extractor also covers the reviewed Home Assistant
state-read function, filter, and test forms for `states`, `state_attr`,
`is_state`, `is_state_attr`, and `has_value`, plus `states.<domain>`
collections. Finite collections passed through `select`/`reject` with reviewed
state tests or `map` with reviewed state filters retain their bounded entity
candidates. Exact-domain collections also remain bounded through reviewed
`entity_id` `selectattr`/`rejectattr` tests. An exact
`map(attribute='entity_id')` projection may precede those tests without losing
the proven source domain. Negated `is not <reviewed-test>` forms retain the
same entity dependency as positive tests. Exact operands remain exact
dependencies; ambiguous operands, collection operators, and target-domain
collections remain non-conclusive. A proven non-target domain can still be
excluded for the exact helper. The resolver never executes Jinja or calls
arbitrary template functions.

The same reviewed collection pipelines are inspected within bounded
parentheses, containers, and conditional branches. Exact nested candidates
remain exact; ambiguous nested collections or operators and exceeded nesting
or scan bounds remain explicitly incomplete. A later outer filter cannot cause
an inner reviewed state-read pipeline to disappear from dependency evidence or
F3 lock projection. Malformed delimiter tails are scanned once under the shared
budget and produce explicit incomplete limit evidence instead of repeated
suffix rescans.

For the exact helper being planned, a complete candidate set that excludes the
helper is proven unrelated. A candidate set or exact label membership that
includes the helper is retained as a real causal dependency and the downstream
automation consequence is still classified proportionally. Direct/static
references are unchanged and are never suppressed by dynamic resolution.

Plans bind deterministic expression, collection-operator, explicit-candidate,
label-membership, and combined candidate-set fingerprints. Exact finite
candidates and literal label selectors are retained only within strict bounds.
A candidate-list, reviewed operator, label membership, relevant automation,
completeness, or freshness change alters the material dependency binding, so
final preflight rejects the old approval before dispatch.

Dynamic label names, arbitrary calls, unreviewed filters/tests, unrestricted
state iteration, unknown macros or functions, malformed entity values, failed
or partial label registry evidence, and any expression or membership overflow
remain non-conclusive. Reviewed direct, collection, or negated state-read syntax
that cannot be resolved emits bounded incomplete evidence rather than
disappearing from the index. Limits are explicit; evidence is never silently
discarded and no helper-specific allowlist or risk bypass exists.

The source regression fixture models
`input_boolean.mcp_f2_standard_admin_test_flag` without placing that identity in
production logic. When automation and blueprint coverage are complete, static
references are zero, and every unrelated dynamic reference is finitely proven,
the helper receives complete no-consequence evidence and remains eligible for
the existing low/standard path. Actual consequential dependencies still
elevate governance, and incomplete evidence remains non-dispatchable.

## Exact helper provider health attribution

`get_server_health` now reports the Engineering-native helper provider as:

- provider: `direct_home_assistant_state`;
- provider contract: `direct-ha-exact-input-boolean-v1`;
- fallback: `none`.

The provider appears in the operational provider section and the
`set_input_boolean_state` operation entry. Availability requires both a
successful REST `/config` probe for its exact read/readback path and a
successful read-only WebSocket `get_config` probe for its dispatch transport.
REST-only evidence does not report the provider healthy. Either unavailable
required transport reports unavailable/degraded, while a skipped check remains
configured/unprobed. These probes never call a service or claim that a state
action executed successfully, and the upstream lifecycle provider is never
substituted.

## Preserved boundaries

- Static registration remains 51 tools: 25 canonical and 26
  Engineering-native. Reviewed delegated-read and exact connector totals are
  unchanged from Beta 37.
- The public `create_helper_state_plan` schema, exact
  `input_boolean.turn_on`/`input_boolean.turn_off` dispatch mapping, approval
  authority 3, task schema 1, durable intent, authoritative reread, duplicate
  suppression, recovery, locks, and zero-fallback policy are unchanged.
- Stable v1.1.2, ha-mcp admission, dashboards, mobile approval navigation,
  workflows, container/deployment configuration, and all other HAMCP-089
  domains remain unchanged.
- Source and disposable-CI validation do not access or mutate the household
  Home Assistant environment.

The Beta 37 Gate 4 live helper canary remained blocked by overly broad dynamic
reference uncertainty. Beta 38 does not claim that live acceptance. After a
separately authorized deployment, the bounded canary in the Beta 38 acceptance
document must be rerun with separate operator approval for each transition.
