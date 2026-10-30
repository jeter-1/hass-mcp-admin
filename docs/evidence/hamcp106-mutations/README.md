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
| former `results.json` (rounds 0–2) | superseded by round 3; the round 0–2 files are kept as history | 250 OK | 69 | 65 |
| `round3-results.json` | review round 1 corrections: every mutant replayed, H redefined for the columnar record, new G and M classes | 294 OK | 79 | 77 |
| `round4-results.json` | G6 and A3, after adding the runtime-composition test | 295 OK | 2 | 1 |
| `results.json` | final: round 3 with the round 4 rows, round 4 baseline | 295 OK | 79 | **78** |

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
- **Before round 3 (Codex review round 1):**
  - R1-M1 replaced the grouped HAMCP-255 record with a columnar one, so the
    H mutants were redefined for it (H1–H8).
  - R1-H1 added the generic resource-write gate (G1–G6) and R1-M2 the
    unrestorable-baseline refusal (M1–M2).
  - R1-M3 added isolated controls for A4, P3 and R4, and the runner now also
    runs `tests.test_hamcp165_review1_corrections`.
- **Before round 4:** G6 survived round 3. Every fixture wires the Core
  double into the gateway itself, so no test exercised the production
  composition. `RuntimeCompositionTests` now configures a real
  `GovernanceRuntime` with synthetic settings and asserts that the configured
  write boundary consults the given Core runtime.

## Remaining survivor (1): equivalent

- **A3: the gate re-check at persistence entry is removed.** Equivalent. The
  lowest plan builder repeats `_dashboard_metadata_core()` before storing a
  dashboard-metadata operation, and nothing between the two checks yields
  (no `await`). Removing either check alone cannot let a plan through. A5,
  which removes the lowest builder's check, is killed.

## Corrected round-2 survivor claims

Round 2 listed A4, P3 and R4 as redundant or equivalent. Review round 1
showed that each one is observable, and round 3 kills each one with an
isolated control:

- **A4** (final persistence-boundary re-check removed). The earlier claim
  that the lease refuses "with the same `pending_signed_registry_admission`"
  was wrong: the real planning lease re-checks on exit and refuses
  `core_capability_unavailable`, a different refusal. The new isolated
  control `test_final_persistence_check_refuses_without_the_lease_check`
  neutralizes the lease's own checks, withdraws admission after policy
  binding, and expects the full `pending_signed_registry_admission` refusal
  with nothing stored or written. The existing
  `test_lowest_builder_rechecks_admission_at_its_own_persist_boundary`
  (HAMCP-165) also kills A4.
- **P3** (generic builder admits the resource type). The earlier claim of
  "empty details" from another layer does not hold for complete valid input:
  a valid admitted operation would persist a plan.
  `test_generic_builder_refuses_a_complete_valid_admitted_operation` expects
  the exact `resource_type must be automation, script, or helper` validation
  error, with no reads, no commands and an empty plan repository.
- **R4** (rollback task-state check removed). Not equivalent: a verified
  child under an unverified parent task would create a restore.
  `test_verified_child_under_an_unverified_task_creates_no_restore` expects
  `forward_not_verified` with `plan_created` false and no new plans or
  writes.

## Reproduce

Run this from the worktree root, after creating `.artifacts/venv`:

    systemd-run --user --unit hamcp106-mut --property=MemoryMax=3G \
        --property=OOMPolicy=continue --working-directory=$PWD \
        $PWD/.artifacts/venv/bin/python docs/evidence/hamcp106-mutations/run_mutations.py [names...]

With no names, the runner writes `results.json`. With names, it writes
`results-partial.json`.
