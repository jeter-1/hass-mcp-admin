# 2.2.0-beta.7 acceptance contract

Version: `2.2.0-beta.7`

Source baseline:
`5c7eebf962837f85f2309b1b5099401fb075cd6e`

This is the source and later operator-controlled acceptance contract for the
Beta 7 response-evidence and prohibited-projection corrections. Source
implementation and validation must not access deployed Home Assistant, merge,
publish, deploy, create a tag or release, or trigger an operational action.

## Immutable boundaries

- Provider response receipt comes only from affirmative transport/provider
  evidence, never from later readback.
- Empty successful HTTP responses and received HTTP or WebSocket error frames
  count as responses; timeouts and connection failures without a response do
  not.
- Response bodies, headers, credentials, full configurations, and approval
  principals are not persisted.
- Historical task records are not migrated or backfilled.
- A prohibited F2 plan is terminal, visible, and non-actionable. It creates no
  challenge, task, provider call, or fallback and is excluded from pending
  approval views and counters.
- Standard and elevated pending plans retain their truthful pending counters.
- F2 policy mapping, authority version 3, task schema version 1, one-task
  ownership, same-administrator enforcement, stale-state checks, verification,
  approval-consumption ordering, and no-blind-redispatch behavior are unchanged.
- The source/local/configured contract remains 25/23/48 plus 26/74, zero
  planned tools, stable v1.1.2, and zero fallback.

## Source response-evidence acceptance

Require focused tests for each configuration-provider transport outcome:

1. REST success with a body records one received response and timestamp while
   retaining no body.
2. REST success with an empty body records the same receipt evidence.
3. A received REST error records receipt while operation failure remains
   separate and bounded.
4. A timeout or connection failure before response leaves receipt false and no
   response timestamp.
5. WebSocket success and error frames each record receipt while retaining their
   distinct operation outcome.
6. A successful write followed by verified readback completes
   `succeeded_verified` with true provider-response evidence.
7. A successful write followed by a semantic mismatch remains
   `failed_post_dispatch` with true provider-response evidence.
8. Each operation in a multi-operation plan retains its own receipt evidence.
9. Startup reads old tasks without changing persisted response fields.

No test may infer response receipt solely because later readback sees desired
state.

## Source prohibited-projection acceptance

Use the reviewed safety-critical fixture with `lock.unlock` and a nonexistent
device target. Require:

- policy class and lifecycle `prohibited`, no acknowledgements, and no
  challenge;
- public detail, list, and handoff projections that are non-actionable and do
  not claim awaiting/required approval;
- approval and apply both return `prohibited_change`;
- no execution task, provider attempt, mutation, or fallback;
- unchanged awaiting/required/pending/external-approval counters and an
  incremented prohibited-policy counter;
- no ordinary rejection counter increment unless separately caused by a user
  rejection;
- no Ingress approval row or action; and
- the same projection after restart, expiry-window passage, and creation of a
  later same-target plan.

Authority-v1/v2 history remains readable under its existing schema and is not
silently upgraded.

## Disposable and exact-image acceptance

The pinned disposable Home Assistant contract must retain all F2 scenarios:

- standard: one approval, one task, one dispatch, verified readback, and
  duplicate task reuse without redispatch;
- elevated: ordered plan approval and same-administrator risk acknowledgement,
  different-administrator refusal, one provider response with timestamp,
  semantic readback verification, `succeeded_verified`, one task/dispatch, no
  trigger or physical actuation, and duplicate reuse; and
- prohibited: entity and nonexistent-device safety-critical targets produce no
  approval action, task, provider mutation, or fallback and do not change
  pending approval counters.

Exact-image lanes for `ha-mcp` 7.14.1 and 7.14.2 must each report 78 advertised
tools, 26 exact-admitted delegated reads, and zero missing reviewed reads,
schema mismatches, unreviewed tools, or fallback attempts. Stable packaging
must remain 1.1.2. The Engineering image must report `2.2.0-beta.7`, the exact
head SHA, and `dirty=false` for amd64, arm64, and arm/v7.

## Later live retest sequence

Live acceptance requires separate authorization. When authorized:

1. Capture the dedicated fixture baseline and governance/task counters.
2. Rerun live Test 3.
3. Confirm one provider attempt, `response_received=true`, a populated
   `response_recorded_at`, `succeeded_verified`, matching normalized
   fingerprints, and duplicate apply with no redispatch.
4. Leave the elevated configuration installed.
5. Run Test 5 immediately using a separate explicit baseline update plan.
6. Do not use rollback between Tests 3 and 5.
7. Verify Test 5 uses a separate immutable plan, the approval count returned by
   its policy, one task, one dispatch, verified baseline readback, helper still
   off, and unchanged trace count.
8. Rerun or inspect the prohibited plan and confirm it is not awaiting or
   requiring approval and has no challenge, task, provider, or fallback.
9. Confirm there are no nonterminal or manual-review tasks.

Test 1 does not require full live repetition unless shared code changes beyond
this narrow correction. Deployment and live-system access remain separate
operator decisions.

## Required validation

Run focused response-evidence, prohibited-policy, approval, task, transport,
Ingress, handoff, and disposable contract tests twice. Run two buffered and one
verbose full unittest discovery, Full and exact-head Evidence tiers, compilation,
metadata, dependency consistency, strict dependency audit, compatibility
registry validation and deterministic regeneration, YAML, secret, PowerShell,
protected-path, and whitespace gates. Run Docker-backed disposable, exact-image,
stable/Beta packaging, and architecture lanes locally only when the normal
Docker boundary is available; otherwise require exact-head CI.
