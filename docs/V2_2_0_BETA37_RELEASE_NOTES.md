# Engineering 2.2.0-beta.37 release notes

Beta 37 adds the first HAMCP-089 governed exact runtime action. It can set one
virtual Home Assistant `input_boolean` to an explicit `on` or `off` state
through the existing external-approval and F3 execution lifecycle. Beta 36
remains the advertised release until the protected promotion workflow
separately publishes Beta 37.

## Exact helper-state proposal

`create_helper_state_plan` accepts only a lowercase exact
`input_boolean.<object_id>`, desired state `on` or `off`, and a bounded plan
expiration. Planning reads the exact current Home Assistant state first. An
already-desired helper returns a verified no-change result without creating a
plan or dispatching a service. A real change obtains bounded dependency and
action-consequence evidence for the exact helper before classifying risk.
Complete evidence with no consequential path remains
`standard_admin`/low/none. Consequential automation paths elevate the existing
governance policy proportionally. Incomplete, stale, failed, unsupported, or
truncated evidence remains reviewable but cannot be described as conclusively
low risk or dispatched.

The immutable plan binds the normalized dependency set, downstream automation
identities, consequence classification, completeness, and evidence
fingerprint without retaining unbounded automation bodies. F3 refreshes that
evidence before dispatch. A material normalized change invalidates the prior
approval as dependency-risk drift; generation-only or other irrelevant source
metadata changes do not create a false rejection.

This is an Engineering-native direct-provider contract. It does not delegate a
write to ha-mcp and has no fallback. The only reachable commands are exact
`input_boolean.turn_on` and `input_boolean.turn_off` calls with one exact entity
target. Toggle, physical-device domains, arbitrary service data, and generic
service forwarding remain unavailable.

## Execution, verification, and recovery

F3 re-reads the exact state immediately before dispatch. An unchanged baseline
permits one mutation. An already-reached desired state terminalizes as verified
success with zero dispatch and leaves approval unconsumed. Any other state or
state fingerprint drift fails before dispatch independently of dependency-risk
drift.

State, helper-configuration, and controlled-reload operations now share the
canonical `helper:input_boolean.<object_id>` identity and
`reload:input_boolean` lock relationship. Same-helper configuration and state
actions serialize in both directions, a controlled input-boolean reload cannot
overlap state preflight/dispatch/readback/verification, and unrelated helpers
remain concurrent under deterministic lock ordering.

The executor durably records intent before the sole mutation opportunity and
then performs an authoritative exact-state readback. Success requires the
desired state to be observed. A lost response is reconciled by readback only;
duplicate apply and startup recovery cannot redispatch. A mismatched readback
is a truthful post-dispatch verification failure.

Automatic rollback is not available. Reversal is a separately planned and
approved request for the opposite exact state. The complete contract is in
[`HAMCP_089_EXACT_HELPER_STATE.md`](HAMCP_089_EXACT_HELPER_STATE.md).

The existing disposable Home Assistant CI contract now exercises the production
helper-state gateway and F3 lifecycle on 2026.7.2, 2026.8.0, and 2026.8.1. It
proves off-to-on, duplicate suppression, separately approved on-to-off, one
actual WebSocket dispatch per plan, REST readback, durable intent ordering, no
fallback, and response-loss reconciliation without redispatch. This is
disposable CI acceptance only; Beta 37 does not claim household/live
acceptance.

## Catalog and preserved boundaries

- Static registration is 51 tools: 25 canonical and 26 Engineering-native.
  Exact reviewed totals are 77 for 7.14.2, 75 for 8.0.0 and 8.1.0, and 76 for
  8.1.1 and 8.2.0. Exact-image and readmission jobs derive their local count
  from the executable static registry declaration and preserve equality
  assertions.
- Stable v1.1.2 is unchanged.
- Existing public tool schemas, provider admission, upstream read routing,
  dashboard behavior, approval authority, audit redaction, and zero-fallback
  policy are unchanged except for the additive HAMCP-089 tool and exact F3
  operation.
- No generic service execution, physical device action, reload, restart,
  dashboard write, deployment, credential change, or live Home Assistant
  mutation is included in source validation.
- The known Android cold-start approval-notification body-navigation defect is
  not corrected or accepted by Beta 37. It remains separate future work.
