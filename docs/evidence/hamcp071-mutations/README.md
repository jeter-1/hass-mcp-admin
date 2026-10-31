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
| `pre-r1-results.json` | final before the Codex R1 corrections: round 0 with the round-1 rows and the round-1 baseline | 219 OK | 82 | **81** |
| `results.json` | R1 full run: all 82 replayed plus 22 new `H*` guard removals, after the R1 corrections | 246 OK | 104 | 99 |
| `r1-rerun-results.json` | the 5 R1 survivors plus the H14 control, after the new R1 isolation tests | 247 OK | 6 | 5 |
| `d4-results.json` | D4 full run: all 104 replayed plus 9 new D4 `H23`–`H31` mutants, after the D4 change | 258 OK | 113 | **112** |

No mutant timed out and none had an import error in any round.

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

## Codex R1 corrections (round 2)

The R1 corrections (write permit, reload evidence, switch rule, declared
requests, cached-only consumers) re-anchored T10, P2, G1 and G3 and added
`H1`–`H22`. Every one of the 82 earlier mutants was replayed against the
corrected tree in `results.json`: 99 of 104 killed. The survivors were A3
(equivalent, below) and four that layered checks were masking. Each now
has an isolated test, confirmed in `r1-rerun-results.json`:

| Survivor | What the mutant does | Why it survived | Killing test |
|---|---|---|---|
| W6 | drops the closed-payload `config_id` match in `write()` | the F3 write permit behind it also refuses (`write_permit_required`), and the test asserted only `call_not_sent` | `test_closed_payload_violations_are_never_sent` now asserts the reason `closed_payload_violation` |
| H9 | takes an activation (state value moves with `last_changed`) as reload evidence | the activation test changed the state on every read, so the baseline already held it; and a name edit failed the loaded check anyway | `test_an_activation_is_not_reload_evidence`: one activation after the POST, on a member-only edit that the pre-reload loaded scene already matches |
| H11 | accepts reload evidence latched by any operation | each write replaces the baseline, so the operation id check is layered | `assert_verified_after_a_delayed_reload` asserts that another operation id is not evidence |
| H15 | verifies when the gateway has no reload check | every gateway in the suite has the check | `test_strategy_without_the_reload_check_is_unverified`: no check, a truthy non-`True` answer and `False` stay unverified; a `True` answer for this target and operation verifies |

`H14_unobserved_reload_verified` was killed in the full run; the rerun
includes it as a control (5 failures).

## D4 mode-helper rule (round 3)

Josh's decision D4 makes any `input_boolean` or `input_select` member,
before or after the edit, elevate the plan. The D1 and D4 rules now share
`_member_rule_reasons`, so H17–H19 were re-anchored onto it: H17/H18 drop
only `SWITCH_REASON` in the planner or the floor, and H19 matches the
switch domain as a substring. The runner gained `--out NAME.json`, and this
run wrote `d4-results.json` with every earlier mutant replayed in copied
trees. Each new mutant is killed:

| Mutant | What it does | Killing tests (examples) |
|---|---|---|
| H23 | removes `input_boolean` from the D4 domains | `test_an_input_boolean_member_elevates`, `test_a_helper_present_only_before_the_edit_elevates` |
| H24 | removes `input_select` from the D4 domains | `test_an_input_select_member_elevates`, `test_an_input_select_present_only_before_the_edit_elevates` |
| H25 | removes the mode-helper check | the elevation tests for both domains |
| H26 | drops the D4 reason in the planner only | the elevation tests (the policy floor then prohibits the plan) |
| H27 | drops the D4 reason from the policy floor only | `test_the_policy_floor_refuses_a_basis_without_the_helper_reason`, `test_the_id_floor_names_only_the_two_helper_domains` |
| H28 | matches a helper domain as a substring | `test_the_id_floor_names_only_the_two_helper_domains` (`light.input_boolean_room`) |
| H29 | widens D4 to every `input_*` domain | `test_input_number_and_input_text_members_are_unchanged` (the bounded control) |
| H30 | drops the D4 statement | the elevation tests |
| H31 | discloses only the members after the edit | the before-the-edit tests |

No mutant timed out and none had an import error.

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
- A3 survives again in both R1 runs and the D4 run, for the same reason.

## Usage

From the worktree root:

    systemd-run --user --unit hamcp071-mut --property=MemoryMax=3G \
        --property=OOMPolicy=continue --working-directory=$PWD \
        .artifacts/venv/bin/python docs/evidence/hamcp071-mutations/run_mutations.py [ids...]

With no ids, the runner runs the baseline and then every mutant, and writes
`results.json`. With ids, it runs only those mutants (pass `baseline` to
include it) and writes `results-partial.json`; the R1 rerun's copy is
`r1-rerun-results.json`. `--out NAME.json` overrides the output file (the
D4 run used `--out d4-results.json`). The R1 and D4 units also set
`TMPDIR=<worktree>/.artifacts/tmp`.
