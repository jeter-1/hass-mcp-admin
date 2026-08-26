# Engineering 2.2.0-beta.46 acceptance

Beta 46 corrects two live Beta 45 HAMCP-089 defects. Fixed state and zone
trigger provenance could remain falsely target-capable after time and context
operations, allowing unrelated consequential automations to contaminate helper
risk. Separately, bounded downstream-profile presentation was treated as if
required action analysis had stopped. Engineering 2.2.0-beta.45 remains
advertised until a separately authorized protected promotion. Stable v1.1.2
and the 51-tool Engineering registration remain unchanged.

## Typed trigger and value provenance

The whole-template obligation ledger keeps distinct bounded types for finite
entity selectors, state objects with finite entity provenance, datetimes,
timedeltas, scalars and reviewed dependency-neutral context values. A complete
state or zone trigger seeds `trigger.entity_id`, `trigger.from_state` and
`trigger.to_state` from its configured finite entity set. State attributes
retain that entity provenance; consuming `last_changed` or `last_updated` as a
datetime does not create another selector.

Reviewed `now()`, `as_timestamp()`, datetime subtraction, `total_seconds()`,
formatting, scalar comparison and state-context identity access produce typed
non-selector values. Assignments, finite collections, branches, loops, macros,
filters and attribute access preserve the typed provenance supported by the
ledger. Passing any such scalar or context value back into a state-selector
call is not treated as harmless: unless an exact finite selector can be proved,
the result remains target-capable opacity.

Every state-bearing obligation still terminates as exact dependency, complete
target exclusion, dependency-neutral, bounded semantic opacity or coverage
failure. Unknown callables and attributes, caller-supplied entity values,
unbounded state collections, dynamic labels without complete membership,
external templates and malformed or over-limit constructs remain conservative.
No automation, helper, configuration-path or household-specific exception is
used.

Only a source retaining an exact target edge or genuine target-capable opacity
may contribute a downstream consequence. A source whose relationships are all
finite target exclusions or dependency-neutral contributes no consequence,
approval elevation or target-specific downstream lock. Independent exact
relationships in the same source remain causal and retain their consequence.

## Analytical completeness and presentation bounds

Action profiles distinguish:

- `analysis_complete`: every required action/effect was visited within the
  reviewed structural bounds;
- `semantic_complete`: every visited effect has sufficiently reviewed meaning;
- `presentation_truncated`: bounded visible lists or detail projections were
  compacted;
- `processing_limit_exceeded`: traversal stopped before required material was
  examined; and
- downstream pagination/fragmentation, which is a transport representation and
  does not alter analytical truth.

Display compaction alone does not make `complete` false. Action domains,
services, reason codes, effect targets and effect data retain exact total counts
and deterministic full-set fingerprints while returning bounded visible
prefixes. Action-structure fingerprints bind ordering and all material omitted
detail. Material changes beyond a visible prefix must change the action-profile
and helper-risk fingerprints and fail final preflight until a fresh plan is
created and approved. Display-only aliases remain nonmaterial.

The existing opaque-cursor downstream-profile pager remains the complete-detail
path. All logical profiles, including a record spanning four or more fragments,
must reconstruct deterministically with stable count, ordering and full-set
fingerprint and with no provider, refresh, lock, approval, dispatch, fallback or
persistence side effect.

True step or depth exhaustion, missing profiles, unavailable lock identities,
unsupported target-capable action semantics and other stopped analysis remain
coverage failures. They carry bounded reasons, observed limits and drift-bound
overflow evidence where available and remain non-actionable with conservative
locking. Action traversal is fenced at 512 steps and depth 16; material effect
data is fenced at 4,096 nodes and depth 16. The fences are evaluated without
recursive call-stack dependence, so deeply nested malformed material terminates
as explicit processing failure rather than an absent profile or runtime error.

## Risk model, actionability and locks

New plans use `helper-dependency-risk-v5`. Persisted v3 and v4 plans remain
readable with their original hashes and historical classifications, but require
replanning and cannot authorize approval or dispatch. They are never
reinterpreted under v5 semantics.

Require these v5 outcomes:

- unrelated fixed-trigger time/context evidence: complete, no surviving target
  dependency or opacity, no downstream consequence, low/standard and
  approval-actionable;
- exact safety-critical helper evidence: complete, safety-critical,
  high/elevated and approval-actionable through elevated approval, even when
  bounded presentation or profile fragments are required;
- genuine target-capable opacity: incomplete, high/elevated, non-actionable and
  conservatively locked; and
- genuine processing or coverage failure: incomplete, non-actionable and
  conservatively locked.

Risk, approval binding, final preflight and F3 locking consume the same evidence
generation. Exact dependencies acquire exact locks; excluded/neutral sources add
no target-specific downstream lock; opaque or coverage-failed evidence acquires
the conservative dependency guard. Service, target, selector, material action
data, full-set evidence and lock changes reject stale approval before dispatch.

The public helper input, approval authority 3, task schema 1, exact
`input_boolean.turn_on`/`turn_off` provider contract, durable intent, one
dispatch attempt, authoritative reread, duplicate suppression, readback-first
uncertain response, recovery, audit attribution and zero fallback remain
unchanged.

## Validation and release boundaries

Require the Beta 45 baseline falsifications, focused Beta 46 semantic/action/F3
tests, Beta 37-45 helper-risk regressions, obligation-ledger adversarial and
resource tests, plan observability/pagination, complete unit discovery,
protected Fast, Full and clean-head Evidence gates, compilation, dependency,
YAML, PowerShell, secret, whitespace and strict vulnerability checks, exact
tool accounting, stable-v1 comparison, isolated Beta 46 promotion-candidate
validation and exact-head CI across supported Home Assistant, exact ha-mcp,
image/readmission, packaging and architecture lanes.

## Post-deployment acceptance — separate authorization

Perform no live acceptance from the implementation branch. After separate
review, merge, promotion and deployment authorization, rebuild dependency
evidence and run read-only controls against the deployed configuration.

For the standard helper, if configuration is unchanged, require zero exact
dependencies, zero target-relevant opacity, zero downstream profiles, complete
evidence, no physical consequence, low/standard policy and approval-actionable
status.

For `guest_mode`, if configuration is unchanged, require the exact dependencies
and all downstream profiles to remain present and traversable, no
presentation-only coverage failure, safety-critical/high/elevated policy and
approval-actionable status through elevated approval. Any newly introduced
genuine selector remains conservative. If configuration changed, derive the
expected counts from fresh evidence rather than forcing historical values.

The exact off-to-on-to-off mutation canary remains separately authorized and
must not run until both read-only controls pass. This feature task authorizes no
merge, promotion, publication, deployment, restart, live Home Assistant access,
approval, apply or dispatch.
