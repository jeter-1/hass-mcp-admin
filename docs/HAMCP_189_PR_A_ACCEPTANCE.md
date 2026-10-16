# HAMCP-189 PR A acceptance

This contract covers HAMCP-189 PR A:
- R3/R3b: Core authority before "already satisfied";
- R6: the exact refusal for an unmapped F3 operation;
- R2: a durable, provider-independent cancel.

Design and final protected-file list: `docs/HAMCP_189_PR_A_DESIGN.md`.
Authority: `docs/HAMCP_189_TRIAGE.md` and Josh's 2026-10-16 decisions O1–O9
on register row HAMCP-189.

Release target: the next Engineering beta at materialization time. It is the
first to be ready and is served together with HAMCP-162. The version string is
`unknown` until `scripts/promote_next_release.py` materializes it. This
document does not name or claim a version. PR C (R8, L1, L2) is a separate PR
in the same release (O6).

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- the design note;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Full and Evidence tier logs named in the PR.

### Offline proof: degraded Core refuses before any read (O1)

`tests/test_hamcp189_authority_cancel.py`,
`CoreAuthorityBeforeAlreadySatisfiedTests`. It uses a real signed Core runtime
with a synthetic registry and a fake helper gateway.

- `test_degraded_core_already_reached_refuses_before_any_read` makes the
  triage probe permanent. The registry entry expires and the helper is already
  in the desired state. Expected after apply:
  - `failed_pre_dispatch`, with child outcome `provider_unavailable_pre_dispatch`
    and code `dispatch_authority_unavailable`;
  - no `preflight_noop_verified`;
  - zero helper reads and zero network calls;
  - approval still `approved`;
  - a refused lease attempt is counted (the probe's "zero lease attempts" is
    gone);
  - leases and commits settled.
- `test_admitted_core_already_reached_verifies_under_current_authority`:
  under admitted Core, the HAMCP-181 outcome stays unchanged
  (`succeeded_verified`, `desired_state_already_reached`,
  `preflight_noop_verified`). Every preflight read runs with an installed
  authorizer that returns true.
- `test_authority_retired_during_preflight_read_cannot_verify`: if Core is
  retired during the read, the outcome is refused, never verified.
- `test_dispatch_window_opens_only_after_before_dispatch` and
  `test_retirement_after_before_dispatch_closes_window`: the dispatch-window
  authorizer.
- `test_expiry_at_dispatch_guard_reconcile_has_zero_dispatch`: expiry at the
  final pre-dispatch await.

### Offline proof: R6

`UnmappedOperationRefusalTests` covers four behaviors:
- An unmapped operation is refused with `core_requirement_unmapped`, with zero
  preflight reads, zero lease attempts and zero approval consumption. This
  holds both through the preflight wrapper and through the dispatch guard alone.
- An unknown code collapses to `dispatch_authority_unavailable`.
- Every registered F3 operation has a Core route. The registry and route sets
  are equal.

### Offline proof: durable cancel (O3)

`DurableCancelTests` uses a governed operational reload with a switchable
provider identity reader and a lifecycle fake that fails every call during an
outage.

- `test_cancel_during_outage_is_durable_and_recovery_never_dispatches`. This
  is the full fail-then-recover sequence:
  1. Cancel during a simulated ha-mcp outage. Expected:
     - `cancelled_pre_dispatch`, with `provider_contacted: false`;
     - zero provider contacts;
     - a child `execution_cancelled` event carrying `operator_cancel_requested`.
  2. The provider returns.
  3. Run two recovery passes, then repeat apply.
  4. Expected: zero dispatch, and the task stays cancelled.
- `test_cancel_after_identity_drift_binds_stored_identity`: apply still
  refuses `provider_lock_identity_changed`, and cancel succeeds from the
  stored identity without a live read.
- `test_cancel_after_intent_is_still_refused`: unchanged refusal after intent,
  even during an outage.
- `test_restart_between_child_cancel_and_public_event`: a process loss after
  the durable child cancel. A fresh runtime then projects
  `cancelled_pre_dispatch` (not `preflight_failed`), the plan status is
  unchanged, and there is zero dispatch.
- `test_cancel_recorded_during_recovery_await_prevents_dispatch`: a cancel
  recorded during recovery's prepare await prevents execution.
- `CoreAuthorityBeforeAlreadySatisfiedTests.test_cancel_racing_dispatch_wins_without_dispatch`:
  a cross-process cancel during the final pre-dispatch await. Expected: zero
  dispatch, no intent, and approval still `approved`.

### Mutations (each must fail)

| Mutation | Failing tests |
| --- | --- |
| Remove the `_CoreVerificationAdapter.preflight` lease wrapper | degraded-Core refusal, retirement-during-read, guard-await expiry, R6 wrapper, race |
| `_cancel_recorded` returns false and the projection branch is removed | restart-between-cancel-and-event, recovery-await cancel |
| Cancel performs the live provider identity read before recording | outage cancel, drift cancel, restart, recovery-await cancel |

## 2. Release CI and publication

These stages follow the release's own acceptance document once it is
materialized. Publication needs Josh's Ready authorization and the protected
merge handoff. No new persisted format is introduced:
- the child cancel event is the existing `execution_cancelled` event, with one
  more bounded diagnostic code;
- the previous beta reads every record this change writes.

## 3. Installed Engineering

All installed checks below are read-only unless a step names Josh's explicit
authorization.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the materialized version tag;
   - the protected `main` merge commit that contains this PR.

   A source version alone does not prove the installed image.
2. **Catalog unchanged.** PR A changes no tool registration or input schema.
   The cancel result gains only the additive field `provider_contacted`.
   Compare the public descriptors with the previous beta. Also compare:
   - Core signed selection;
   - storage and audit health;
   - F3 readiness;
   - zero fallback.
3. **Degraded-Core refusal and durable cancel.** These are proven offline only
   (§1). No live degradation, outage, cancel or apply test is run to accept
   this change.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
