# HAMCP-227 mutation evidence

`run_mutations.py` runs against a disposable `git clone --shared` copy of
this worktree at `.artifacts/mut/tree/` (ignored). For each mutant it applies
one exact-string edit, which must match its anchor exactly once. It then
runs `tests.test_hamcp227_global_sources` in the copy and restores the
file. A mutant counts as killed only when the run exits non-zero from a
test failure or a test-level error. An import, syntax or collection error
is not a kill; I checked every `errors=` kill by hand (below). A timeout
(300 s) is never counted as a kill. An unmutated control runs before and
after the mutants, and both must pass.

The mutated files are:
- `dependency/global_sources.py` (M01–M31, M54, M55);
- `dashboard_analysis/dependency_reader.py` (M32–M46);
- `impact/provider.py` (M47, M48, M56);
- `dependency/service.py` (M49, M50);
- `dependency/index.py` (M51);
- `dependency/provider.py` (M52, M53).

Scratch (the copy, TMPDIR and logs) lives under the ignored
`.artifacts/mut/`.

## Rounds

| File | Run | Tests | Mutants | Killed |
|---|---|---|---|---|
| `round1-results.json` | first pass at c0f65cd1 | 51 OK | 55 | 48 |
| `round2-results.json` | all 55 replayed at 007ac661, after the isolation tests | 56 OK | 55 | **55** |
| `m56-results.json` | `run_m56.py` (reuses the `run_mutations.py` setup, saved as `run.py` in the scratch directory): reverts the impact-matrix attribution fix in 007ac661 | 56 OK | 1 | 1 |

No mutant timed out or failed to apply. Three kills are test-level errors
(`FAILED (errors=1)`), not import errors; in each, all 56 tests ran and the
error is a `KeyError` inside one test body:
- M19: `test_budget_overrun_is_partial_and_counts_unread_dashboards`;
- M41: `test_invalid_and_secret_shaped_paths_are_screened`;
- M53: `test_dashboards_cost_one_list_and_one_read_each`.

## Round-1 survivors

Before round 2, I confirmed in the copy that each new test kills its
target.

| Survivor | What the mutant does | Killing test |
|---|---|---|
| M22 | stops rechecking authority between dashboard reads | `test_retired_authority_after_the_list_stops_further_reads` |
| M32 | acquires the lease without a parent dispatch authorizer | `test_parent_without_a_dispatch_authorizer_acquires_nothing` |
| M40 | skips the pre-dispatch authority-identity check | `test_upstream_authority_drift_refuses_the_read_before_dispatch` |
| M43 | reports projection truncation twice, once as a structural gap | `test_projection_truncation_is_partial_with_a_terminal` (exact `errors` and empty `gaps`) |
| M46 | drops the parent caller id from child requests | `test_one_list_and_one_read_per_dashboard_with_attributed_audit` (upstream sees `synthetic-caller` three times) |
| M47 | turns an unavailable partition row that was read into `not_supported` | `test_impact_keeps_an_unavailable_dashboard_row_unavailable` |
| M52 | passes `registry_complete=True` regardless of the build's registry | `test_incomplete_build_registry_marks_scenes_and_groups_partial` |

M56 is killed by `test_impact_reports_deferred_template_coverage`, which
asserts the template row's provider `none` and capability `not_supported`.
