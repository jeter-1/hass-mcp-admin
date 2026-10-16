# Engineering 2.4.1-beta.14 release notes

Engineering 2.4.1-beta.14 closes three HAMCP-189 residuals from the HAMCP-164
triage (PR A). It builds on beta.13 (HAMCP-164).

No tool, input schema, description, provider route, signed registry, HOLD,
support window, persisted format, workflow or stable-v1 change is included. No
new write, dispatch, service-call or fallback path is reachable. The design
note is [`HAMCP_189_PR_A_DESIGN.md`](HAMCP_189_PR_A_DESIGN.md), and the
triage is [`HAMCP_189_TRIAGE.md`](HAMCP_189_TRIAGE.md).

**Core authority before "already satisfied" (R3/R3b).** Before beta.14, a
governed F3 step ran its preflight reads before acquiring a Core lease. Under
degraded Core, a step whose target was already in the desired state could
therefore report `succeeded_verified` with zero lease attempts. Now:
- the step acquires and consumes a Core lease before any preflight read;
- every preflight read runs under an authorizer that revalidates that lease;
- authority is revalidated after the reads.

Under degraded Core, the step is refused before any Home Assistant read:
- outcome `provider_unavailable_pre_dispatch`, with code
  `dispatch_authority_unavailable`;
- no verified outcome;
- the approval is not consumed.

The refusal is pre-dispatch, so a new plan can retry it. Core retirement
during the reads also refuses.

When Core is admitted, an already-reached target still returns
`succeeded_verified`, `desired_state_already_reached` and
`preflight_noop_verified`, exactly as on beta.13 (HAMCP-181).

The final dispatch call also runs under an authorizer. It opens only after
the pre-dispatch checks consume the dispatch authority, and only while that
authority revalidates.

Fan and power steps are unchanged: they already read through Core.

**Exact refusal for an unmapped F3 operation (R6).** An F3 operation with no
Core route is refused with `core_requirement_unmapped` instead of the generic
`preflight_failed`. The refusal happens before any read, lease or approval
consumption. Refusal codes are a closed set, and an unknown code is reported
as `dispatch_authority_unavailable`. Every registered F3 operation has a Core
route today, so no current operation reaches this path.

**Durable, provider-independent cancel (R2).** Before beta.14, cancelling a
governed operational task re-read the live ha-mcp provider identity, so an
ha-mcp outage or identity drift could fail the cancel before anything was
recorded. Now:
- The cancel is recorded durably before any provider contact. The child
  `execution_cancelled` event carries `operator_cancel_requested`.
- The task's stored provider identity is used, with no live read, so cancel
  succeeds during an ha-mcp outage.
- Every recovery pass checks for a recorded cancel before, and again after,
  each await that precedes execution. A cancelled task is never dispatched.
- A restart between the child cancel and the public event still projects
  `cancelled_pre_dispatch`.
- The cancel result adds `provider_contacted: false`. This is additive, and
  the cancel path never contacts a provider.

The other cancel rules are unchanged:
- cancel after a dispatch intent is still refused;
- apply still refuses `provider_lock_identity_changed`.

**Disclosed residuals (unchanged):**
- Cancelling an expired plan with no child record still refuses with
  `plan_expired`. No dispatch is possible in that case.
- The cancel telemetry residual remains for HAMCP-189 PR B.
- The configuration stale-state race (R3c) is inherent to the Home Assistant
  configuration API.

The healthy catalog remains **87 tools: 59 native and 28 delegated reads**.

Downgrade: beta.13 reads every beta.14 record, since no persisted format
changes. A downgraded server again reads before acquiring Core for "already
satisfied" steps, and again needs a live provider identity read to cancel.

Follow the [beta.14 acceptance contract](V2_4_1_BETA14_ACCEPTANCE.md).
