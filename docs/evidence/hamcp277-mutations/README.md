# HAMCP-277 mutation evidence

`run_mutations.py` runs against a disposable `git clone --shared` copy of
this worktree at commit `1e41293e` (the fix and its tests), under the ignored
`.artifacts/mut277/`:

    python docs/evidence/hamcp277-mutations/run_mutations.py .artifacts/mut277 .artifacts/mut277/results.json

Each mutant is one or more exact-string edits, and every anchor must match
exactly once. N01 has two edits: it reverts the phase move. For each mutant
the runner runs `tests.test_hamcp277_dashboard_scan_budget` and
`tests.test_hamcp227_global_sources` in the copy (89 tests), then restores
the files. Classification is mechanical:
- **killed(assertion)**: all 89 tests ran and at least one test FAILED;
- **errors_only (hand check)**: all ran, no failure, at least one error;
- **not_a_kill(test count)**: fewer tests ran (an import or collection
  error);
- **timeout (not a kill)**: the run exceeded 300 s;
- **SURVIVED**: the suite passed.

An unmutated control runs before and after the mutants, and both must pass.

The run was on scriptbox on 2026-11-03, from 11:55 to 12:19 CST. It ran
under `systemd-run --user` (MemoryMax=3G, OOMPolicy=continue) and peaked
at 109 MB. Idle-host check at 11:55 CST:
- no pwsh, `check.ps1`, mutation or unittest process in any worktree;
- no other user job unit;
- load average 0.22, with 5 GB available.

## Result (`results.json`)

| Measure | Value |
|---|---|
| Mutants | 37 (N01–N37) |
| Killed by assertion | **35** |
| Errors-only, short test count or timeout | 0 |
| Survived | 2, both classified below |
| Controls (before and after) | 89 OK, 89 OK |

| Area | Mutants | Killed |
|---|---|---|
| Phase placement: revert to starting at build start, concurrent with the other sources | N01 (`provider.py`) | 1/1 |
| Per-read bound: no timeout, not capped by the phase, bound 6 s, cancelled-read result, no cancel of a timed-out read, timer clamp | N02–N07 | 5/6 |
| Sorted order | N08 | 1/1 |
| Budget attribution of a phase-capped read timeout | N09–N11 | 2/3 |
| Outcome mapping and accounting: too_large, timed_out and failed; read; exception; attempted; in-flight; not_attempted_budget, authority and failure; stale flag; `dashboard_not_read`; no read after the deadline | N12–N24 | 13/13 |
| Report: the `N of M` line and its first position, outcome lines, screening, the 10-path bound, "none shown", the plural, the others count, `outcome_counts`, the returned `outcomes` | N25–N36 | 12/12 |
| Completeness only when every dashboard is read with no counted gap | N37 | 1/1 |

## Survivors

- **N07, equivalent.** It drops the `max(0.0, …)` clamp on the per-read
  timer. `process()` computes `remaining = deadline - clock()` synchronously
  after the worker's `expired()` check returned false, with no await in
  between. `remaining` is therefore always positive, and the clamp never
  changes the value. In any case, `asyncio.sleep` with a negative delay
  returns at once, as checked. The clamp is defensive only.
- **N11, round 0: survived; round 1: reclassified and pinned.** It changes
  `remaining < DASHBOARD_READ_SECONDS` to `<=`. Round 0 called it
  boundary-equivalent. Codex round 1 (R1-L3) pointed out that the two
  behaviors are observably different: when the phase remainder equals the
  per-read bound exactly, the mutant also counts
  `dashboard_scan_budget_exhausted`, although the read got its full 3 s
  allowance and no budget was taken from it. The timeout policy is not
  changed. Round 1 pins the existing behavior with an assertion:
  `test_a_read_given_the_full_allowance_at_the_boundary_is_a_read_timeout`
  expects exactly `{"dashboard_read_timeout": 1}` and no terminal. Its
  round-1 analog R10 (the same `<` to `<=` in the current code) is killed;
  see below.

## Round 1 (Codex round-1 corrections R1-M1, R1-M2 and R1-L3)

`run_round1_mutations.py` mutates the round-1 code, using the same harness
and classification, in a disposable `git clone --shared` copy under the
ignored `.artifacts/mut277r1/`:

    python docs/evidence/hamcp277-mutations/run_round1_mutations.py .artifacts/mut277r1 .artifacts/mut277r1/results.json

The suites are the same two modules. The per-mutant bound is 1500 s, not
300 s: R07 (timer left running) makes every real-clock index build wait out
each read's 3 s timer, so it is slow rather than hung, and the bound must
let it finish so its assertion failures are counted. The controls take
about 36 s.

### First run (`round1_first_run_results.json`, commit `34722f24`)

The run was on scriptbox on 2026-11-03, from 14:51 to 15:10 CST, under
`systemd-run --user` (MemoryMax=3G, OOMPolicy=continue). Idle-host check
at 14:51 CST:
- no other user job unit;
- no pwsh, `check.ps1`, mutation or unittest process;
- load 0.74, with 5.7 GB available.

Controls: 100 OK and 100 OK. 19 of 21 were killed by assertion. Two were
not:
- **R07, timeout (not a kill).** It removes the per-read timer's
  cancellation. The run exceeded the 300 s bound because two tests awaited
  a simulated sleep with no real-time limit, and the real-clock builds ran
  slowly.
- **R11, survived; not equivalent.** It removes the worker's own deadline
  check, leaving only the in-read guard. The two differ when the deadline
  passes and the dashboard authority retires at the same moment. The
  mutant then reports the rest as `not_attempted_authority` and can omit
  the budget overrun. The code reports them as `not_attempted_budget` and
  counts the overrun.

Corrections in commit `deb1f2ca` (tests and runner only; no runtime change):
- `test_a_deadline_reached_together_with_retired_authority_is_reported_as_the_budget`
  pins the deadline-first precedence.
- `test_a_finished_read_cancels_its_timer` checks the timer directly,
  bounded in real time.
- The two unbounded tests (the cancellation test and the HAMCP-270
  disclosure test) now bound their waits in real time, so a timer left
  running fails an assertion.
- The runner's per-mutant bound was raised to 1500 s.

### Final run (`round1_results.json`, commit `deb1f2ca`)

The run was on scriptbox on 2026-11-03, from 15:33 to 15:52 CST, under
`systemd-run --user` (MemoryMax=3G, OOMPolicy=continue). Peak memory
observed during the run was about 90 MB. Idle-host check at 15:33 CST:
- no other user job unit;
- no pwsh, `check.ps1`, mutation or unittest process;
- load 0.05, with 5.8 GB available.

| Measure | Value |
|---|---|
| Mutants | 21 (R01–R21) |
| Killed by assertion | **21** |
| Errors-only, short test count, timeout or survived | 0 |
| Controls (before and after) | 102 OK, 102 OK |

| Area | Mutants | Killed |
|---|---|---|
| R1-M1 in-read deadline guard: guard removed, `< 0` instead of `<= 0`, allowance taken when queued (the round-0 shape), per-read cap dropped, no budget flag on an unstarted read, slot count released for an unstarted read, timer not cancelled, cancelled read not reported, timer does not cancel the read | R01–R09 | 9/9 |
| R1-L3 boundary (N11 on the round-1 code): `<` to `<=` | R10 | 1/1 |
| Worker pre-check removed (deadline-over-authority precedence) | R11 | 1/1 |
| R1-M2 inventory accounting: omitted outcome, omitted error key, accounted slice, bounded flag, more-than-requested clause, "at least" wording, the 50-read-limit suffix, queue not capped, limit condition, outcome vocabulary | R12–R21 | 10/10 |

R10 is killed by
`test_a_read_given_the_full_allowance_at_the_boundary_is_a_read_timeout`
(the R1-L3 pin) and by an existing hanging-read test. R11 is killed by the
precedence test. R07 fails 27 tests, including
`test_a_finished_read_cancels_its_timer`. R14 also raises 2 errors beside
its 3 failures; it counts as killed because it has assertion failures.
