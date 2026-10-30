# HAMCP-071 mutation evidence

`run_mutations.py` copies this worktree once per mutant, skipping `.git`,
`.artifacts` and `__pycache__`. In each copy it applies exact-string edits;
every edit must match its anchor exactly once. It then runs these five
modules:
- `tests.test_hamcp071_scene_configuration`;
- `tests.test_hamcp242_core_authority_itemization`;
- `tests.test_hamcp164_core_gate_fail_closed`;
- `tests.test_hamcp155_merge_seams`;
- `tests.test_f3_configuration_identity`.

A mutant counts as killed only when the run exits non-zero without an import,
syntax or indentation error. A timeout (900 s) is never counted as a kill.

Scratch lives under the ignored `.artifacts/hamcp071-mut/`: each copy, its
TMPDIR and HOME, and the logs. Each run was a `systemd-run --user` unit
(MemoryMax=3G, OOMPolicy=continue) with
`DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1`.

## Rounds

| File | Run | Tests in baseline | Mutants | Killed |
|---|---|---|---|---|
| `round0-results.json` | first pass, all mutants | 205 OK | 81 | 64 |
| `round1-results.json` | the 17 round-0 survivors plus the A3+A4 control, after the new tests | 219 tests per mutant run | 18 | 17 |
| `round1-baseline.json` | the unmutated tree after the new tests | 219 OK | — | — |
| `results.json` | final: round 0 with the round-1 rows and the round-1 baseline | 219 OK | 82 | **81** |

No mutant timed out and none had an import error in either round.

**Before round 1:** I added `MutationSurvivorTests` (14 tests) and
`ProfileShapeTests` to `tests/test_hamcp071_scene_configuration.py`. Before
round 1 ran, I confirmed in a disposable copy that each new test kills its
target mutant. The table lists each round-0 survivor and the test that now
kills it.

| Survivor | What the mutant does | Killing test |
|---|---|---|
| A5 | removes the rollback route's admission gate | `test_rollback_admission_is_checked_before_the_forward_state`: an unapplied plan under withdrawn admission refuses `pending_signed_registry_admission`, not `forward_not_verified` |
| T1 | allows a non-`homeassistant` registry entry | `test_integration_entry_for_a_stored_config_is_not_ui_managed`: a stored config whose only scene entry is integration-owned refuses `not_ui_managed`, not `not_loaded` |
| T10 | stops checking the loaded member list | `test_loaded_members_must_match_the_stored_members` |
| W10 | drops the send-boundary mark | `test_send_boundary_records_the_send_time`: the send-time summary and `dispatched_at` |
| D8 | marks every field as requested | `test_unrequested_field_is_disclosed_as_not_requested` |
| S4 | stops screening consumer evidence | `test_consumer_evidence_text_is_screened` |
| P1 | removes the protection-floor check | `test_bound_basis_must_carry_the_protection_floor`: a tampered standard-shaped marker that omits the lock floor is prohibited |
| P5 | allows multi-operation scene plans | `test_multi_operation_scene_plan_is_prohibited` (each child is non-prohibited on its own) |
| B3, B4 | treat an absent scene, or a contract error, as "binding holds" | `test_gateway_preflight_holds_only_for_the_bound_state` |
| B5 | skips the strategy preflight | `test_strategy_passes_the_gateway_verdict_through` |
| R2, R3, R4 | skip the rollback's child-record checks (outcome, dispatch count or intent, resulting fingerprint) | `test_rollback_checks_the_forward_child_record`: a verified task over an altered child record |
| R5 | stops mapping "no change" to drift | `test_rollback_after_external_restore_is_drift` |
| R7 | drops the rollback route's own lease recheck | `test_rollback_route_rechecks_its_lease_before_persisting`: admission is withdrawn after prepare and persistence is stubbed, so only the route's own `require_current()` can refuse |

Why these survived round 0:
- **Layered checks (B3–B5, R2–R4, R7).** The F3 fingerprint recheck already
  catches the full-path versions of B3–B5. The task-level state check already
  catches R2–R4. The final persistence check (`_before_persist`) already
  catches R7. Each new test isolates one layer, following the HAMCP-106
  R1-M3 isolated-control pattern.
- **A5.** On a verified forward, the planning lease also refuses withdrawn
  admission. The new test therefore uses the case where the gate must come
  first.

## Remaining survivor (1): equivalent

- **A3: the persistence-entry recheck is removed.** Equivalent.
  `_create_scene_configuration_plan` returns
  `await self._create_configuration_plan(...)`. That builder's first
  statement for a scene operation is `_require_dormant_profile_admission`,
  which performs the same `_scene_configuration_core()` check. Nothing
  between the two checks yields: no `await` runs first. So removing either
  check alone cannot let a plan through.
- **Isolated control.** `A3A4_both_persist_gates_removed` removes both
  checks and is killed in round 1 (2 failures).
- **A4 alone** (only the generic-builder check removed) was killed in
  round 0.

## Usage

From the worktree root:

    systemd-run --user --unit hamcp071-mut --property=MemoryMax=3G \
        --property=OOMPolicy=continue --working-directory=$PWD \
        .artifacts/venv/bin/python docs/evidence/hamcp071-mutations/run_mutations.py [ids...]

With no ids, the runner runs the baseline and then every mutant, and writes
`results.json`. With ids, it runs only those mutants and writes
`results-partial.json`.
