# Engineering 2.4.1-beta.62 release notes

Engineering 2.4.1-beta.62 (HAMCP-277) lets the dependency index read the
household's dashboards within its existing 6 s dashboard budget, and names
the outcome of every dashboard it lists. It builds on 2.4.1-beta.61 (#353,
HAMCP-285). No tool, input schema, route, provider, upstream tool, admission
rule, signed profile, contract fingerprint, persisted format, add-on option
or Home Assistant write changes.

Acceptance: [`V2_4_1_BETA62_ACCEPTANCE.md`](V2_4_1_BETA62_ACCEPTANCE.md).

## Why

On beta.55 the architect's read-only dependency analysis reported "0 of 6
dashboards read; the 6 s DASHBOARD_SCAN_SECONDS budget was exhausted before
any read" (RCG-093). Dashboard-only references were therefore missing from
`entity_dependency_analysis`, `change_impact_analysis` and the HAMCP-270
plan disclosures.

**What the evidence shows.** Source analysis and synthetic experiments
reproduce an overlap/contention mechanism, and the change addresses it. They
do not establish that this mechanism alone caused the live "0 of 6": live
contention was not measured.

**Mechanism, from source.** The dashboard collector started its 6 s clock
when it began, but the provider started it as a task at the start of the
build, concurrently with every other dependency source. The dashboard
session, list and reads therefore overlapped the earlier sources, and the
dashboard clock ran during that overlap. The other sources use native REST
and WebSocket reads; the dashboard reads use delegated MCP sessions. The
source does not show a shared serialized transport between them; any shared
Home Assistant Core or host contention is modeled below, not measured.

**Mechanism, from synthetic experiments** (wall clock, synthetic household
at live scale: 121 automations, 1,606 states, 6 dashboards; list 0.4 s, read
0.8 s, an oversized dashboard 1.5 s and refused as too large):
- the dashboard phase alone reads 5 dashboards and reports the oversized
  one as too large in about 2.0 s;
- the same phase inside a live-scale build with no modeled contention gives
  the same result;
- with 4.0 s of modeled shared Core contention during the build, the same
  result; with 5.8 s, 1 read completed and the rest counted
  `dashboard_not_read` with the budget exhausted. About 5.6 s or more of
  modeled contention reproduces the live signature (live: the dashboard
  phase took 6,328 ms of a 7,640 ms build, and the automation source
  3,985 ms);
- in the modeled cases, a slow or oversized dashboard alone, and serialized
  reads alone, completed 2 to 5 of 6, never 0. HAMCP-273's oversized
  response alone occupies one of the 4 read slots. These controls do not
  exclude every live combination.

**Started versus completed reads.** The live "0 of 6" is zero *completed*
reads. Before this change a read cancelled in flight at the phase deadline
was also counted `dashboard_not_read`, so the live record does not show how
many reads started. This release counts a read cancelled in flight as
`timed_out` (`dashboard_read_timeout`), separately from one never started.

## The change

- **The dashboard phase runs after the other sources.** Its 6 s budget now
  starts when the phase starts, so earlier phases cannot spend it. The
  index's 300 s build deadline is unchanged; a build can take up to the
  dashboard budget longer than before.
- **Each read is bounded.** A read may take at most 3 s
  (`DASHBOARD_READ_SECONDS`), capped by what remains of the phase, so one
  slow dashboard cannot hold a read slot for the whole phase. A read that
  hits its own bound is `timed_out`; one capped by the phase deadline also
  counts the budget overrun.
- **No read starts after the phase deadline.** The deadline is checked, and
  the read's allowance computed, inside each scheduled read immediately
  before the reader is invoked. A read that waited behind other work until
  the deadline passed is not started and is `not_attempted_budget`.
- **A fixed order.** Dashboards are read in sorted `url_path` order, so the
  same household is read the same way on every build.
- **An exact outcome per dashboard.** Every listed dashboard gets one of
  `read`, `too_large`, `timed_out`, `failed`, `not_attempted_budget`,
  `not_attempted_authority` (dashboard authority retired during the phase),
  `not_attempted_failure` or `not_attempted_limit` (listed beyond the
  50-read limit; counted as `dashboard_not_read_inventory_limit`). A read in
  flight when the phase deadline cancels it is `timed_out` and counts
  `dashboard_read_timeout`.
- **Truthful partial reporting.** The dashboard coverage row's first
  warning is `Dashboards read: N of M listed`; this is the reason the
  HAMCP-270 disclosure shows. M counts every listed dashboard the build
  accounted (up to 51, the list request size), not only the 50 queued for
  reading; any beyond the limit are appended as
  `; K not attempted (50-read limit)`. When the upstream inventory itself
  was truncated, the line reads `Dashboards read: N of at least M
  (inventory truncated)` and the row is never complete. Each other outcome follows as
  `Dashboards <outcome>: <paths>`, with paths screened and at most 10 shown
  before a count of the rest. The row is complete only when every listed
  dashboard was read with no counted gap. The build profile adds
  `global_sources.dashboard.outcome_counts` (counts only, no paths).
- **Bounds unchanged.** `DASHBOARD_SCAN_SECONDS` stays 6 s: the measured
  fixture phase takes about 2.0 s, so no increase is justified. At most 50
  dashboards, 4 concurrent reads, the reviewed 2 MiB document cap and the
  10,000-finding bound are unchanged.

## Not changed

- The dashboard store stays diagnostic only: outside the snapshot
  fingerprint and the helper obligation ledger, and it never lowers risk.
  For every HAMCP-270 planner, plan bindings, bound disclosure, policy class,
  physical consequence, required acknowledgements and risk are identical
  whether the dashboard phase reads all, some or none of the dashboards.
- The upstream tools (`list_dashboards`, the reviewed analysis read), the
  provider route, admission, leases and audit events.
- Large-dashboard readback (HAMCP-273, RCG-045): an oversized dashboard is
  still refused, now reported exactly as `too_large`.
- No dashboard write path.

## Rollback

Reinstall 2.4.1-beta.61. The change is in the dependency index build only;
no persisted format, plan binding or fingerprint changed, so plans and
records are unaffected. On beta.61 the dashboard phase again runs
concurrently with the build and may read none of the dashboards.
