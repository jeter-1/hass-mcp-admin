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
- **N11, boundary-equivalent.** It changes `remaining < DASHBOARD_READ_SECONDS`
  to `<=`. The two differ only when the phase remainder equals the per-read
  bound exactly, to the last bit. Then the read's own bound and the phase
  deadline are the same instant, so either label is truthful: "timed out on
  its own bound" or "capped by the phase". The per-dashboard outcome
  (`timed_out`), the counted `dashboard_read_timeout` and the partial
  completeness are identical. Only the extra budget counter would differ.
  Any dashboard still queued at that instant is stopped by `expired()`,
  which counts the overrun anyway. No test pins this zero-width boundary,
  because neither behavior is more correct.
