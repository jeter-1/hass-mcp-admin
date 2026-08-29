# Engineering 2.2.0-beta.51 acceptance

Beta 51 stages the HAMCP-089 production-path agreement correction reviewed at
source head `3aa1d8833369220216ac3c0fd2e55c7cd35133c6`, based on
`66448fc8ece110a73900909ba113eab504db20f6`. Engineering 2.2.0-beta.50
remains advertised until a separately authorized protected promotion. Stable
v1.1.2 and the 51-tool Engineering registration remain unchanged.

Beta 50 remained fail-closed: the affected standard-helper plan was incomplete
and non-actionable, and no approval, apply, provider dispatch or Home Assistant
mutation occurred. This document is source acceptance for a staged candidate;
it is not deployed acceptance; authorization for HAMCP-089 execution is outside
this task.

## Sanitized production replay

The regression uses
`tests/fixtures/dependency/hamcp089_beta50_standard_helper_replay_v1.json`, a
sanitized derivative of the bounded Beta 50 capture. The source capture SHA-256
is `7e92cd703d97cfb32651da278c48bff6a8b3c24df44ee9e723185cdd4952d1de`
and its self-fingerprint is
`b1989ed5e8fdbf22ce29b611ccb83f35ae5ca69a9ba0d2c7d8f48f5a52473817`.
Sanitization occurred before the derivative's hash-bearing material was
computed. The committed fixture contains five pseudonymized configurations and
bounded label/group membership evidence while preserving template syntax,
control flow, dataflow, ordering and scalar types.

The capture explicitly does not prove that the current configuration bodies
were cryptographically identical to the original planning snapshot: the
configuration read did not expose a fingerprint comparable to the persisted
plan fingerprints. The fixture records that limitation and does not manufacture
missing evidence. It is sufficient to replay the captured semantic forms
through the shipped provider, dependency index and helper-risk service; later
deployed acceptance must rebuild evidence from the then-current configuration.

At the exact Beta 50 base, the replay produced zero exact target dependencies,
28 target-capable opaque obligations, five downstream profiles, incomplete
evidence and a non-actionable plan. At the reviewed correction head it produces:

- zero exact target dependencies;
- zero target-relevant opaque obligations;
- zero downstream profiles;
- complete evidence and no physical consequence;
- low risk with `standard_admin` policy; and
- execution eligibility and approval actionability.

The consequential control retains every genuine exact dependency and seven
safety-critical downstream profiles. It has complete evidence, a high risk
level, the `elevated_admin` policy, and approval actionability. The
arbitrary-selector control remains incomplete, non-actionable and conservatively
locked.

## Corrected production-path contract

Entity-set helpers such as `label_entities()` retain finite entity-ID-string
provenance, while real State collections retain State-object provenance. Exact
`states[key]` access preserves bounded candidate, domain and label evidence.
Fixed trigger State objects preserve finite `trigger.from_state` and
`trigger.to_state` relationships; reviewed datetime, nullable, scalar and
context transformations cannot invent helper selectors.

Finite expression-only variables and complete conditional branches retain
their candidate union across later actions. Statement-prefixed or malformed
templates, incomplete branches, caller-supplied selectors, arbitrary filter or
call output, imports and over-limit evidence remain conservative. No Jinja
template is rendered or executed, and the analyzer is not a general interpreter.

Rendering one State through concatenation accounts for its dependency
relationship exactly once and returns scalar-tainted provenance rather than
State shape. Independent State reads remain independent relationships. If a
rendered scalar is later used as an entity selector without separately proven
finite entity-ID provenance, it remains opaque, incomplete and non-actionable.

The standard control retains its exact helper lock and the shared
`helper_dependency:input_boolean_dynamic` stability fence. The consequential
control additionally retains all seven exact downstream automation locks and
the same shared fence. The fence serializes final refresh, evidence validation,
durable intent, dispatch and readback; it does not classify clean evidence as
opaque. Genuine uncertainty retains the conservative dependency guard.

Risk, consequence, actionability, fingerprints, final-preflight drift,
observability and F3 locking consume the identical immutable terminal evidence.
Each plan binds the exact dependency-index generation, fingerprint and source
epoch it used. A current non-invalidated snapshot may be reused by immediately
following plans, while stale, invalidated, missing or hard-fenced evidence
forces a refresh and a racing freshness change is caught by the post-check.

New plans use `helper-dependency-risk-v10`. Persisted v3 through v9 plans remain
readable with their historical hashes and classifications, but require
replanning and cannot authorize current approval, locking or dispatch.

## Source validation requirements

Require the direct State-concatenation regressions, captured replay, Beta 47-50
helper-risk regressions, dependency extraction and obligation-ledger suites,
governance helper-risk, F3 configuration/operational locks, plan observability,
complete discovery, protected Fast/Full/Evidence gates, isolated Beta 51
promotion-candidate validation, compilation, JSON/YAML/PowerShell validation,
dependency consistency, strict vulnerability audit, secret/whitespace checks,
stable-v1 comparison and exact tool/task-schema/approval-authority accounting.

There is no public-schema, provider admission, routing, fallback, workflow,
container, deployment, add-on metadata or stable-v1 change. This staging task
performs no merge, promotion, publication, deployment, live access, approval,
apply, dispatch or Home Assistant mutation.

## Post-deployment acceptance — separate authorization

### Stage 1: artifact and runtime identity

Before planning, require agreement among the Beta 51 release, annotated tag,
source commit, immutable image and deployed digest, including `dirty=false`.
Confirm stable v1.1.2, 51 Engineering tools, task schema 1, approval authority 3,
fallback none, healthy Home Assistant REST/WebSocket connectivity, expected
delegated-read admission, healthy storage and F3 readiness. No active lock,
hold, challenge, nonterminal execution or recovery failure may exist.

### Stage 2: read-only and planning acceptance

The operator must perform this sequence exactly:

1. Capture governance/provider counters and both helpers' authoritative states.
2. Explicitly refresh the dependency index once.
3. Record its generation, fingerprint, source epoch and freshness.
4. Create exactly two helper-state plans: the standard test helper and
   `input_boolean.guest_mode`.
5. Create no reload, dashboard, generic-change or additional plan.
6. Leave both plans at `approval_not_requested`.
7. Traverse every obligation and downstream profile twice.
8. Verify deterministic ordering, counts, full-set fingerprints, terminal null
   cursors, exact fragment reconstruction and provider-free pagination.
9. Verify both plans bind the intended current snapshot identity.
10. Verify historical v3-v9 plans remain readable but non-authoritative.
11. Reconcile final counters and authoritative helper states with the baseline.

The standard-helper plan must use v10 and report exact 0, opaque 0, profiles 0,
complete evidence, consequence none, low/standard policy and actionability. It
must have no downstream automation lock while retaining the exact helper lock
and shared stability fence.

The `guest_mode` plan must retain every genuine exact dependency and seven
safety-critical consequential profiles, remain complete, high/elevated and
actionable, and retain its exact downstream locks plus the shared stability
fence. If the live configuration has changed, recompute the complete expected
set from the fresh snapshot rather than forcing historical counts; any genuine
uncertainty must remain fail-closed. Any failure blocks the mutation canary.

### Stage 3: separately authorized mutation canary

The reversible helper off -> on -> off canary is separate work requiring
explicit authorization after both read-only controls pass. It is not performed
or requested by this staging task.
