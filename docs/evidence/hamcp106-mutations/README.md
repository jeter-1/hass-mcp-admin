# HAMCP-106 / HAMCP-255 mutation evidence

`run_mutations.py` copies this worktree (skipping `.git`, `.artifacts` and
`__pycache__`) once per mutant. In each copy it applies exact-string edits;
every edit must match its anchor exactly once. It then runs the HAMCP-106 and
HAMCP-255 test modules. A mutant counts as killed only when the run exits
non-zero without an import or indentation error. A timeout is never counted
as a kill.

## Rounds

| File | Run | Tests in baseline | Mutants | Killed |
|---|---|---|---|---|
| `round0-results.json` | first pass, all mutants | 236 OK | 69 | 41 |
| `round1-results.json` | the 28 round-0 survivors, after the new tests | 250 OK | 28 | 22 |
| `round2-results.json` | P2 and H2, after correcting their tests | 250 OK (round 1 baseline) | 2 | 2 |
| `results.json` | final: the latest row for each mutant, with the round 1 baseline | 250 OK | 69 | **65** |

Between rounds:
- **Before round 1:** I added contract-boundary, preflight-layer and
  plan-truth tests to `tests/test_hamcp106_dashboard_metadata.py`, and an
  order-independence test to `tests/test_hamcp255_reconciliation_audit.py`.
  I also fixed two mutant definitions:
  - R5 was not viable (an `IndentationError`). The runner now also detects
    that error.
  - H5 was a no-op, because no disposition starts with `withheld`. It now
    keeps only the admitted groups.
- **Before round 2:**
  - **P2's test** had renamed the second operation. That broke the
    per-operation marker check, so the per-operation rule prohibited the plan
    by itself. The test now asserts that each child is `standard_admin` on
    its own.
  - **H2's test** compared dicts, and dict equality ignores order. The test
    now checks key order for a reversed decision order.

## Remaining survivors (4): equivalent or redundant

- **A3: the gate re-check at persistence entry is removed.** Equivalent. The
  lowest plan builder repeats `_dashboard_metadata_core()` before storing a
  dashboard-metadata operation, and nothing between the two checks yields
  (no `await`). Removing either check alone cannot let a plan through. A5,
  which removes the lowest builder's check, is killed.
- **A4: the final persistence-boundary re-check is removed.** Redundant with
  the lease's `_before_persist` admission check on the same path, which
  refuses with the same `pending_signed_registry_admission`.
- **P3: the generic builder admits the resource type.** Redundant. Even with
  the type on the generic allow-list, another validation layer refuses the
  operation with the same `CONFIGURATION_VALIDATION_FAILED` and empty
  details, so no observable output distinguishes the mutant. The generic
  route's inactive-gate refusal is separately covered by the killed mutants
  A1 and A2.
- **R4: the task-state check in rollback is removed.** Equivalent for a
  single-update plan. The child-record checks that follow (dispatched once,
  `succeeded_verified`) produce the same `forward_not_dispatched` and
  `forward_not_verified` refusals, which the rollback tests assert by
  `reason_code`.

## Reproduce

Run this from the worktree root, after creating `.artifacts/venv`:

    systemd-run --user --unit hamcp106-mut --property=MemoryMax=3G \
        --property=OOMPolicy=continue --working-directory=$PWD \
        $PWD/.artifacts/venv/bin/python docs/evidence/hamcp106-mutations/run_mutations.py [names...]

With no names, the runner writes `results.json`. With names, it writes
`results-partial.json`.
