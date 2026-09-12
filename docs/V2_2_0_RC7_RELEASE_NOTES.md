# Engineering 2.2.0-rc.7 release notes

RC7 prepares the independently reviewed reporting correction
`943d05e4bf8e10fcf28da3be3aa6d650044cff3a` against published RC6
`c82e5a2598c204dbc09d5d9e275239faefe30f81`. Stable-v1 remains 1.1.2.
Materialized source must declare RC7 consistently and consume the temporary
staging file. These notes do not establish publication or live acceptance.

## Corrections

- Integrity pagination sizes the complete public response before snapshot
  retirement. Oversized pages can shrink while keeping the existing cursor,
  original snapshot, totals and coverage. All 31 synthetic findings were
  independently retrieved as 1 + 15 + 15 with one provider collection and no
  gaps/duplicates; Unicode and smaller-budget partial-coverage cases also pass.
  The original truncation-specific probe remains baseline evidence, not a
  required candidate output shape. Single-finding refusal at an inadequate
  budget remains explicit and does not extend snapshot lifetime.
- F3-backed plan verification now identifies the actual task/child summary,
  including in compact summaries. Stored legacy verification remains unchanged.
  Reconciliation reads do not mutate persisted authority or dispatch again.
- Held-read canaries record entered upstream transport exactly once and finish
  request timing before serialization, including cancellation. An attempt is
  not independently proof of dispatch. Pre-transport refusal is zero attempts;
  late callbacks cannot revive authority. Retained dispatched reads may finish
  after caller cancellation; no remote-abort guarantee is established.

No new schema, tool, route, provider contract, permission, persistence format,
workflow, TTL, response limit or governed restart capability is introduced.
Preserve RC6's recovery/approval, bounded-receipt and integrity-coalescing fixes
and all earlier compatibility/authority corrections. Engineering remains the
unified public Nabu Casa endpoint using exact reviewed internal ha-mcp 8.4.3.
The healthy pairing remains 51 static plus 25 delegated reads, 76 total, with
17 Core capabilities; operation status remains held. Compiled Core compatibility,
including 2026.9.0/2026.9.1, stays unchanged. Shared builds keep their 300-second
cooperative deadline and 600/3600-second evidence TTLs. Zero fallback remains
required.

## Evidence and acceptance

The correction's independent review found no actionable findings and ran 20 new
tests plus three traversal probes. Correction-head full Evidence ran 3,423 tests
with 20 skips and no test failures/errors; its sole failed step was unchanged
RC6 version metadata. Preserve that record. Final materialized RC7 must pass all
applicable Evidence steps against RC6, receive bounded release-delta review and,
when delivery is authorized, obtain exact-head CI evidence.

Completed RC6 acceptance remains evidence for RC6. RC7 requires separately
bound source/build, installed-image, fresh full public catalog and live evidence.
Follow [RC7 acceptance](V2_2_0_RC7_ACCEPTANCE.md) for useful reads, reporting
readback, natural dependency replacement after the initiating request returns,
settled authority and zero fallback. Do not manufacture live findings or actions
to reproduce disposable tests.

No current household state or unresolved restoration is asserted here. Any
necessary recovery/canary requires fresh state and consequences, exact scope,
its own authenticated plan approval, one apply and authoritative verification.
Do not reuse failed plans or blindly repeat uncertain execution. Preserve
completed restoration rather than toggling a helper to recreate an incident.
Dashboard, held-read, notification/navigation, backup, restart and Core-update
operations remain separately bounded. Published RC6 stays immutable; reverting
to it restores known reporting limitations and is not a complete recovery plan.
No stable promotion, deployment or live operation is authorized by these notes.
