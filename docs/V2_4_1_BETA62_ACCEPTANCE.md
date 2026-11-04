# Engineering 2.4.1-beta.62 acceptance

This contract covers HAMCP-277: the dependency index's dashboard phase reads
the household's dashboards within its bounded budget, or reads as many as
truthfully fit and names the rest. It follows 2.4.1-beta.61 and includes it.

See also the release notes:
[`V2_4_1_BETA62_RELEASE_NOTES.md`](V2_4_1_BETA62_RELEASE_NOTES.md).

**Release target.** `2.4.1-beta.62`, following 2.4.1-beta.61 (#353,
HAMCP-285). The release is materialized in all three version authorities
with no staged declaration left. Nothing is published or deployed.
HAMCP-253 (#354) also expects beta.62 but had not materialized it when this
was staged, and the release transition accepts only beta.61 → beta.62.
Whichever merges first keeps beta.62; the other restages to the next free
beta with full Evidence. That collision is expected and is not a finding.

**Deploy freeze.** Nothing deploys before the HAMCP-278 cutover. Stage 4
waits for that cutover and for Josh.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- the objective: at live scale the dashboard source reads the household's
  dashboards within its bounded budget, or reads as many as truthfully fit
  and names the rest, so dashboard-only references reach
  `entity_dependency_analysis`, `change_impact_analysis` and the HAMCP-270
  disclosures;
- the non-goals: HAMCP-273 large-dashboard readback, any dashboard write
  path, a new upstream tool or provider route, and any admission or
  signed-profile change;
- the bounds: `DASHBOARD_SCAN_SECONDS` stays 6 s; the 300 s build deadline,
  50-dashboard, 4-concurrent, 2 MiB document and 10,000-finding bounds are
  unchanged; each read is bounded by `DASHBOARD_READ_SECONDS` = 3 s, capped
  by what remains of the phase;
- the store stays diagnostic: outside the snapshot fingerprint and the
  helper obligation ledger, never lowering risk (HAMCP-227, HAMCP-270).

Offline evidence (`tests/test_hamcp277_dashboard_scan_budget.py`, the
updated `tests/test_hamcp227_global_sources.py`):
- **Positive.** Six dashboards at realistic latencies all read within the
  budget, with every dashboard's reference in dependency analysis and a
  HAMCP-270 disclosure item.
- **Oversized.** One dashboard refused as too large is reported exactly as
  `too_large`; the other five are read, and the row is partial.
- **Slow reads.** Hanging reads time out per read (`timed_out`) and the
  rest are still read. When the budget truly runs out, as many as fit are
  read and the rest are named `not_attempted_budget`; the row is partial,
  never complete, and the budget overrun is counted.
- **Phase clock.** A slow earlier phase holding a modeled shared Core longer
  than the whole dashboard budget does not spend it: all six dashboards are
  read after it releases.
- **No late start.** Under deferred scheduling and clock advancement, every
  read enters the reader strictly before the phase deadline; a read queued
  before the deadline that would run at or after it is never started and is
  `not_attempted_budget`, and a read's allowance is the remainder when it
  runs. Cancellation while reads are queued or running leaves no task.
- **Inventory accounting.** With 50 listed, all 50 are read; with 51, 50 are
  read and the 51st is `not_attempted_limit`, and the first line is
  `Dashboards read: 50 of 51 listed; 1 not attempted (50-read limit)`; an
  upstream-truncated inventory reads `of at least M (inventory truncated)`
  and is never complete. Read counts, outcomes, omitted counts and the
  HAMCP-270 first-line reason reconcile.
- **Exact outcomes.** Every listed dashboard has exactly one known outcome;
  the first warning is `Dashboards read: N of M listed` (or its bounded
  form above); named paths are screened and bounded.
- **Non-interaction with HAMCP-270.** For all five planners, plan bindings,
  bound disclosure, policy class, physical consequence, required
  acknowledgements and risk are identical with no dashboard reader, with all
  dashboards read, with one too large and with the budget exhausted; only
  the disclosure's reasons differ, truthfully.
- **Mutation testing** in a copied tree, with every kill classified
  (`docs/evidence/hamcp277-mutations/`).
- The complete repository suite in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes, including the
beta.61 → beta.62 release transition.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow.

## 4. Installed acceptance (separately authorized)

After the HAMCP-278 cutover, Josh deploys beta.62 and refreshes the
connector. The architect then runs read-only checks only. None of them
creates, approves or applies a plan.

1. **Read-only.** Run `entity_dependency_analysis` on an entity that a
   dashboard references. PASS if the dashboard coverage row reports at
   least the 5 normal dashboards read (`Dashboards read: N of M listed`
   with N ≥ 5) and `home-admin-dashboard` either read or reported exactly
   as `too_large` (`Dashboards too_large: home-admin-dashboard`), and the
   dashboard reference appears in the result.
2. **Read-only.** Run `change_impact_analysis` for the same entity. PASS
   if the dashboard reference appears with the same coverage reason.
3. **Read-only.** In the step 1 result, the dashboard coverage row's first
   warning is the line the HAMCP-270 plan disclosure shows as its dashboard
   reason; offline tests prove that mapping. PASS if that first warning is
   exactly `Dashboards read: N of M listed`, its N and M agree with the
   per-outcome lines that follow and with
   `global_sources.dashboard.outcome_counts` from `get_server_health`, and M
   is the number of dashboards `list_dashboards` reports.

Seeing the disclosure on a real plan requires creating one:
`create_entity_metadata_plan` and the other HAMCP-270 planners persist a
governed plan even though they dispatch no Home Assistant change. That is a
separate planning action, outside this read-only acceptance, and needs
Josh's explicit authorization; if authorized, the plan is never approved.

If a step fails, keep the responses, the build profile from
`get_server_health` (including `global_sources.dashboard.outcome_counts`)
and the audit records, and record them in HAMCP-277. Whether to roll back
to beta.61 is Josh's decision.
