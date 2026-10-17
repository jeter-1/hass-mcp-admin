# Engineering 2.4.1-beta.15 acceptance

This contract covers HAMCP-189 PR B and PR C, delivered in one PR (O6
amended):
- R1: rollback planning holds its own Core lease;
- R2-telemetry: an observed read counts an upstream attempt only after the
  Core authorizer passes;
- O9: a truthful, bounded terminal outcome for an expired pre-intent task;
- R8 and L1: the gateway audit keeps the refusal reason;
- L2: the beta.13 release-notes correction.

It builds on 2.4.1-beta.14 (HAMCP-189 PR A).

Design and final protected-file list: `docs/HAMCP_189_PR_B_DESIGN.md`.
Authority: `docs/HAMCP_189_TRIAGE.md` and Josh's 2026-10-16 decisions O1–O9
on register row HAMCP-189. Release notes:
[`V2_4_1_BETA15_RELEASE_NOTES.md`](V2_4_1_BETA15_RELEASE_NOTES.md).

Release target: the next Engineering beta, first ready, first served together
with HAMCP-162. `2.4.1-beta.15` was staged in `.release/next-version` and then
materialized in this PR by one owner-authorized run of
`scripts/promote_next_release.py --apply`, which consumed the declaration. If
HAMCP-162 merges first and takes this version, this PR merges `main`,
restages and rematerializes the next beta, and renames this document and the
release notes to match.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- the design note;
- `2.4.1-beta.15` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Full and Evidence tier logs named in the PR.

### Offline proof: rollback under degraded Core refuses before any read (R1)

`tests/test_hamcp189_rollback_core_lease.py` uses a real signed Core runtime
with synthetic configuration and registry fakes.

- `test_degraded_core_refuses_fresh_rollback_before_any_read`,
  `test_garage_inverse_refuses_before_any_read_under_degraded_core` and
  `test_degraded_core_refuses_before_any_registry_read`: under degraded Core,
  fresh rollback planning (configuration, garage inverse and entity metadata)
  is refused as `provider_unavailable` / `core_capability_unavailable` with
  zero configuration or registry reads and no rollback plan.
- `test_unconsumable_lease_is_released_and_refused_before_any_read`: an
  acquired lease that cannot be consumed is released and refused.
- `test_admitted_core_creates_the_plan_inside_one_current_lease`,
  `test_garage_inverse_plans_inside_the_lease_and_returns_existing` and
  `test_admitted_core_plans_every_registry_read_inside_the_lease`: under
  admitted Core, every planning read runs with an installed authorizer that
  returns true, the plan is created and the lease finishes once. The
  entity-metadata lease binds the exact registry target.
- `test_core_retired_during_planning_creates_no_plan` and
  `test_core_retired_during_registry_planning_creates_no_plan`: a retirement
  before a planning read refuses that read and creates no plan.
- `test_garage_retirement_during_final_builder_read_saves_nothing`: Core
  retires after the garage builder's last awaited read returns. The refusal is
  `provider_unavailable` / `core_capability_unavailable`, no rollback plan or
  source rollback pointer is saved, nothing is written, the lease finishes
  once, and a clean retry reads the same resources and creates the plan.
- `test_ordinary_retirement_during_final_builder_read_saves_nothing`: the
  same proof for an ordinary configuration rollback (one builder read). The
  check runs through the private `_before_persist` hook in
  `governance/service.py` (O8 amendment) before the plan is recorded or any
  prior plan is superseded.
- `test_existing_rollback_plan_is_returned_without_core`: the idempotent local
  return still works while Core is degraded.
- `test_runtime_without_core_keeps_current_behavior`: compositions with no
  Core runtime are unchanged.

### Offline proof: cancel telemetry (R2-telemetry)

`tests/test_hamcp189_observed_read_telemetry.py`: a refused read reports zero
upstream attempts; a retirement counts only the admitted read; admitted reads
count every dispatch; a failure before dispatch is not an attempt; a late
retained-session dispatch after the caller returned is not started.

### Offline proof: expired pre-intent deadline (O9)

`tests/test_hamcp189_expired_pre_intent_deadline.py` uses the real exact
helper-state F3 path. Approval consumption is refused at the Core seam, so the
child records `pre_intent_retry_required` with no dispatch intent.

- `test_expired_task_terminates_after_bounded_retries`: recovery at every
  eligible instant after expiry terminates the child after at most 7 retries,
  at or after the deadline, as `preflight_rejected` / `plan_expired`. The task
  is `failed_pre_dispatch` and the plan `FAILED`. Zero dispatch, locks and
  leases settled, readiness restored, later sweeps change nothing.
- `test_retries_continue_until_the_deadline`: one second before the deadline
  the child still retries and readiness is still false.
- `test_unexpired_failure_keeps_its_bounded_retry`: a failure while the plan
  is valid keeps `bounded_retry`; after preparation recovers, the same task
  dispatches exactly once.
- `test_deadline_survives_a_restart`.
- `test_service_reconciler_composition_never_dispatches`.
- `ExpiredClaimTerminalPrimitiveTests`: the claimless primitive refuses a live
  claim, a changed generation, an already-terminal child and a dispatched
  child (`BlindRedispatchProhibited`), leaving each record unchanged.

### Offline proof: expired work with no child record (O9, review M2)

`tests/test_hamcp189_expired_unclaimed_declaration.py`, with the service
reconciler absent:
- operational (`OperationalUnclaimedTests`): restart between saving the
  declaration and the first child, then expiry with the provider down, ends
  `failed_pre_dispatch` / `plan_expired` with zero planning, dispatch and
  verification calls and readiness restored; the terminalization step itself
  contacts no provider; in-process expiry; before the deadline the bounded
  retry continues; an operator cancel and a cancel recorded inside the
  projection window keep precedence; a first claim that wins is never
  overwritten, and one that loses finds the terminal record;
- operational, M2 residual (review of `1189020`): the restart case asserts
  the full provider-contact tuple unchanged, identity reads included
  (`test_restart_then_expiry_rejects_without_provider_contact`);
  `test_recovery_sweep_never_invokes_the_identity_reader`;
  `test_slow_identity_does_not_delay_local_termination` (Codex's probe: the
  identity response is held and the task still ends within 0.25 s, the
  reader never entered); `test_expired_claimed_child_ends_before_provider_contact`
  (a first claim that won, then expired, ends with zero contact and the
  provider down); `test_before_deadline_recovery_still_prepares`
  (preservation);
- exact helper state (`HelperStateUnclaimedTests`): restart then expiry
  settles with the helper unchanged; before the deadline it keeps retrying;
- dashboard (`DashboardUnclaimedTests`): restart then expiry is rejected by
  the existing preflight without the new path, with zero writes;
  `test_restart_past_deadline_ends_locally_without_preread`: past the
  deadline it ends locally with zero pre-reads and zero writes.

### Offline proof: gateway audit reason (R8, L1)

`tests/test_hamcp189_gateway_audit_reason.py`:
- `test_empty_name_audit_and_metric_carry_the_unmapped_reason` (L1);
- `test_nonempty_unmapped_name_keeps_one_record_and_one_count`;
- `test_list_and_dict_names_write_exactly_one_generic_record` (R8);
- `test_every_non_string_name_writes_one_record_and_one_count`: `None`,
  `False`, `0`, `[]`, `{}` and truthy non-string names each write one generic
  record and one error count, with no raw value;
- `test_successful_string_name_keeps_one_tool_record_and_no_count`;
- L1 residual (review of `1189020`), `RawNameLoggingTests`: Codex's probe
  through the production `configure_logging` with a debug root capture;
  `None`, `False`, `0`, `[]`, `{}` and list/dict names carrying a marker log
  only `non_string_tool_name:<type>`, the SDK warning keeps only its bounded
  summary, and the marker appears in no app log, SDK log or audit record;
  a string name is still logged;
- `SdkDiagnosticFilterTests`: the filter rewrites the three SDK validation
  messages, leaves every other record untouched and is installed once.

### Mutations (each must fail)

| Mutation | Failing tests |
| --- | --- |
| Rollback planning without the lease (both call sites) | 11 of 13 R1 tests; the existing-plan and no-Core tests pass by design |
| Unbounded retry: the expired pre-intent deadline removed | the three O9 deadline tests (bounded, deadline, restart); the composition test passes by design, because the service reconciler still ends the task |
| The audit loses the empty-name reason (L1) | `test_empty_name_audit_and_metric_carry_the_unmapped_reason` |
| The audit keeps the unhashable lookup (R8) | the list and dict name test (two subtest errors) |
| The attempt starts before the authorizer (R2-telemetry) | four telemetry tests |
| No lease check at the garage save boundary (review M1) | `test_garage_retirement_during_final_builder_read_saves_nothing` |
| The no-record expired declaration is left stuck (review M2) | four no-record tests |
| The primitive overwrites a first claim (review M2) | `test_first_claim_that_wins_is_never_overwritten` |
| Cancel not rechecked inside the projection transaction (review M2) | `test_cancel_recorded_inside_the_projection_window_wins` |
| A falsey name is not classified (review L1) | five audit tests |
| A falsey name is not counted (review L1) | five audit tests |
| The save hook's call removed in `governance/service.py` (O8 amendment) | `test_ordinary_retirement_during_final_builder_read_saves_nothing` |
| The ordinary rollback omits the save hook (O8 amendment) | `test_ordinary_retirement_during_final_builder_read_saves_nothing` |
| Provider contact before local termination: the local-first check removed (review M2 residual) | the five provider-contact tests (`test_restart_then_expiry_rejects_without_provider_contact`, `test_recovery_sweep_never_invokes_the_identity_reader`, `test_slow_identity_does_not_delay_local_termination`, `test_expired_claimed_child_ends_before_provider_contact`, `test_restart_past_deadline_ends_locally_without_preread`) |
| Local-first only for the no-record case (review M2 residual) | `test_expired_claimed_child_ends_before_provider_contact` |
| Log contexts carry the raw name (review L1 residual) | `test_malformed_names_log_only_a_bounded_type` |
| The SDK diagnostic filter not installed (review L1 residual) | `test_malformed_names_log_only_a_bounded_type`, `test_gateway_installs_the_filter_once` |
| The filter keeps the raw SDK message (review L1 residual) | `test_malformed_names_log_only_a_bounded_type`, `test_sdk_validation_messages_keep_only_a_bounded_summary` |

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence. Publication needs Josh's Ready authorization and the
protected merge handoff. Verify the merged source, v2.4.1-beta.15, the
immutable version and commit images, provenance and SBOM with the shipped
verifiers. Never invent future image identities. Retain the beta.14 artifacts
and the known-good earlier artifacts for recovery.

No new persisted format is introduced:
- the O9 terminal uses the existing `preflight_rejected` outcome,
  `pre_dispatch_terminal` event, `plan_expired` diagnostic,
  `transition_processed` runtime result and `failed_pre_dispatch` task state;
- beta.14 reads every record this change writes.

## 3. Installed Engineering

All installed checks below are read-only unless a step names Josh's explicit
authorization.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.15 tag;
   - the protected `main` merge commit that contains this PR.

   A source version alone does not prove the installed image.
2. **Catalog unchanged.** This PR changes no tool registration, input schema
   or result field. Compare the public descriptors with the previous beta.
   Also compare:
   - Core signed selection;
   - storage and audit health;
   - F3 readiness;
   - zero fallback.
3. **Degraded-Core rollback refusal, telemetry, expired-task termination and
   audit reasons.** These are proven offline only (§1). No live degradation,
   expiry, rollback, cancel or malformed request is run to accept this change.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
