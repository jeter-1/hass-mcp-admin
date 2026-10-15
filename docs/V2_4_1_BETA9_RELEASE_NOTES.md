# Engineering 2.4.1-beta.9 release notes

Engineering 2.4.1-beta.9 corrects the outcome of a governed entity-registry
metadata update that Home Assistant answers with an error result (HAMCP-183,
OD-15 of the [HAMCP-181 design](HAMCP_181_TRUTHFUL_OUTCOMES_DESIGN.md)). It
builds on beta.8.

**The defect.** After `config/entity_registry/update` was sent, every error
result other than the authorization refusal was recorded as confirmed
non-mutation:
- `dispatch_failed_confirmed`;
- `provider_rejected_mutation`;
- a zero provider mutation count;
- no registry readback.

That assumption is false. Core assigns the new registry entry before it
schedules the save and fires the update event, and its WebSocket dispatcher
turns a later exception into an `unknown_error` result. The HAMCP-181
revision-3 probe reproduced this on Core 2026.10.0 and 2026.10.1: the
registry changed, yet Engineering reported that nothing had changed. It
refused rollback (`forward_not_verified`) for a change that was really in
place.

**The correction.**
- An error result to a sent update is now recorded as uncertain:
  - outcome `dispatch_indeterminate`, with the response marked received;
  - diagnostics `provider_error_after_send` and
    `provider_response_indeterminate`;
  - no provider mutation count.
- The existing F3 registry readback then decides the outcome:

| Readback after the error result | Outcome | Rollback |
| --- | --- | --- |
| Exactly the planned state | `succeeded_verified` (the change is in place). The audit keeps `provider_error_after_send` next to `exact_readback_verified`. | Offered, as for any verified change |
| Exactly the prior state | `verification_mismatch`, so the task is `failed_post_dispatch` and the public error is `configuration_verification_failed` (verified not applied) | Refused (`forward_not_verified`) |
| Any other state | `manual_review_required` (`readback_matches_neither_plan_nor_prior`), with a conflict hold | Refused |
| Unavailable | `observing`, then `manual_review_required` when the evidence deadline or the attempts run out | Refused |

  None of these paths reports "confirmed nothing changed".
- Only the authorization refusal (`unauthorized` or `forbidden`, the P3
  class) stays confirmed non-mutation: `dispatch_failed_confirmed` with
  `provider_permission_denied` and no readback.
- Engineering's own pre-send refusals keep their current outcomes and never
  dispatch:
  - the closed payload;
  - stale or unknown label, area and category references;
  - the 2026.10 area and name rule.
- The task is never redispatched. The saved intent is preserved, and
  duplicate suppression is unchanged: replay of an unfinished task recovers
  by readback only, and replay of a finished one is refused or reused as
  before.
- Recovery after a restart applies the same mapping from the persisted
  dispatch diagnostics.
- A lost response or a closed connection keeps its earlier mapping: any
  complete readback other than the planned state is `verification_mismatch`.

The misleading gateway comment, which said every error result was a confirmed
non-application, has been corrected.

No tool, public schema or description, task state, terminal outcome string,
stored format, routing, signed registry, HOLD, support window, workflow or
stable-v1 change is included. The new diagnostic and reason codes use the
existing bounded-code format. The healthy catalog remains **87 tools: 59
native and 28 delegated reads**.

**HAMCP-182 gate.** This fix must be published before HAMCP-182 is published.

Downgrade: beta.8 reads every beta.9 record, and nothing new is persisted. A
downgraded server returns to the defect: an error result is again recorded as
confirmed non-mutation without readback.

Follow the [beta.9 acceptance contract](V2_4_1_BETA9_ACCEPTANCE.md).
