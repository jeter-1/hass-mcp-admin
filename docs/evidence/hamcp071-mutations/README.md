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
| `d4-results.json` | D4 full run: all 104 replayed plus 9 new D4 `H23`–`H31` mutants, after the D4 change | 258 OK | 113 | 112 |
| `r2-results.json` | R2 full run: all 113 replayed (7 re-anchored) plus 10 new `E1`–`E10` evidence-check removals, after the R2-H1 correction | 278 OK | 123 | 119 |
| `r2-rerun-results.json` | the 4 R2 survivors (A3 included as the equivalent control), after the new R2 isolation tests | 282 OK | 4 | 3 |
| `optc-results.json` | option C full run: 99 replayed (24 retired, H6 re-anchored) plus 11 new `O1`–`O11` check removals | 257 OK | 110 | 107 |
| `optc-rerun-results.json` | the 3 option C survivors (A3 included as the equivalent control), after the two new isolation tests | 259 OK | 3 | 2 |

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

## Codex R2 corrections (round 4)

*Historical: option C (round 5, below) removed the R2 evidence rule, and
retired every `E` mutant and the mutants re-anchored onto it.*

R2-H1: a loaded scene that matched the plan could be reported verified
from a reload before the save, or with no save at all. The correction
requires positive post-save evidence: in one poll the scene is re-added
(`last_changed` moves, state value unchanged) and a planned name, icon or
member-list change moves from its pre-POST loaded value to the saved
value. The strategy needs that evidence to intersect the plan's visible
changes; an already-stored configuration is refused before dispatch
through the new `already_reached_gap` hook.

T10, H8, H11, H12, H14, H15 and H16 were re-anchored onto the new code
(H12 now removes the whole state-and-registry baseline check; H14 drops the
intersection with the planned changes; H15 treats a missing check as full
evidence; H16 renames the evidence passthrough). The new `E` mutants each
remove one part of the evidence rule, and each is killed in
`r2-results.json`:

| Mutant | What it does | Killing tests (examples) |
|---|---|---|
| E1 | latches evidence without a planned difference (B equal to T) | the delayed-reload tests (exact evidence sets), the Codex R2 unrelated-reload tests, `test_a_genuine_reload_of_a_snapshot_only_edit_is_never_verified` |
| E2 | latches evidence without the saved value loaded | the Codex R2 unrelated-reload tests (name, icon, member set) |
| E3 | sends without a readable registry baseline | `test_an_unreadable_registry_baseline_sends_nothing` |
| E4 | the strategy ignores the planned changes | `test_strategy_without_reload_evidence_is_unverified` (wrong components) |
| E5 | counts unchanged components as planned | `test_strategy_without_reload_evidence_is_unverified` (current equal to proposed) |
| E6 | the adapter ignores `already_reached_gap` | the coincidental-match tests, `test_codex_r2_externally_stored_unloaded_snapshot_is_not_verified`, `test_guard_already_stored_needs_evidence` |
| E7 | the scene `already_reached_gap` is empty | the same tests |
| E8 | drops the gateway evidence bridge | the delayed-reload and positive tests (no edit verifies) |
| E9 | turns a loaded difference with evidence into "unverified" | `test_a_genuine_post_save_reload_with_a_different_loaded_scene_fails` |
| E10 | takes the loaded value from the latch, not the poll | the Codex R2 unrelated-reload tests |

Three earlier mutants survived the R2 full run, because the R2 change
removed the paths that used to kill them. Each now has an isolated test,
confirmed in `r2-rerun-results.json`:

| Survivor | What the mutant does | Why it survived | Killing test |
|---|---|---|---|
| G8 | drops scenes from the strict rollback-projection branch | it was killed only by the already-reached apply, which R2 now refuses before dispatch | `test_rollback_projection_needs_a_verified_task_and_one_dispatch`: a verified child under a manual-review task, or with no dispatch, projects no rollback |
| H10 | stops checking that `last_changed` moved | a visible transition implies a re-add in the suite's Core model | `test_a_member_attribute_rewrite_is_not_reload_evidence` (a state write keeps `last_changed`) and `test_a_registry_name_without_a_re_add_is_not_reload_evidence` |
| H13 | stops the poll on a loaded match without evidence | the old member-edit control matched before the reload; R2 member edits no longer do | `test_a_reload_between_one_polls_reads_verifies_at_the_next_poll`: the reload lands between one poll's state and registry reads |

Every mutant that removes an R2 evidence check maps to one verified path:
- `verify` through `observation_gap`: E1, E2, E4, E5, E8, E9, E10, H8, H10,
  H11, H13, H14, H15, H16 and T10;
- the reload baseline before the POST: E3 and H12;
- `_preflight_already_reached`: E6 and E7;
- the rollback projection: G8.

All are killed. No mutant timed out and none had an import error.

## Option C (round 5)

Codex R3-H1: another writer's reload before this plan's save could still
produce `succeeded_verified` while old member attributes stayed loaded.
Josh's option C (2026-10-31) redefines "verified": the stored scene
configuration reads back exactly (type-exact canonical) and the scene's
entity registry identity is unchanged. Engineering claims nothing about
the scene Home Assistant has loaded, and the reload-inference machinery
was removed. Results carry `verification_scope:
"stored_configuration_and_registry_identity"` and `loaded_state:
"not_verified"`.

**Retired (24), because the code they mutated was removed:** T5, T9 and
T10 (loaded-scene reads), B9 (the projection's loaded key, now
equivalent), B10, H8–H16 (reload latch, baseline, poll exit, evidence
passthrough) and E1–E10 (the R2 evidence rule). **H6** was re-anchored to
the kept write-permit consumption. The other 99 earlier mutants were
replayed against the option C tree.

The new `O` mutants each remove one option C check:

| Mutant | What it does | Killed in |
|---|---|---|
| O1 | the projection drops `registry_entry_id` | `optc-results.json` (8 failures, including the registry-drift tests) |
| O2 | the projection drops `entity_id` | `optc-results.json` (7 failures, including the renamed-entity drift test) |
| O3 | verification compares identity only, not the stored `config` | `optc-results.json` (10 failures, including the stored-mismatch tests) |
| O4 | the scene response-identity check always matches | `optc-rerun-results.json`: `test_response_identity_is_the_exact_config_id` |
| O5 | the scope fields are dropped from the apply result | `optc-results.json` (8 failures) |
| O6 | the scope fields are dropped from the execution task | `optc-results.json` (2 failures) |
| O7 | the scope fields are dropped from the retained apply result | `optc-results.json` (1 failure) |
| O8 | the scope fields are dropped from the plan disclosure | `optc-results.json` (1 failure) |
| O9 | `loaded_state` is dropped from the catalog | `optc-results.json` (1 failure) |
| O10 | `loaded_state` claims `"verified"` | `optc-results.json` (8 failures, including the loaded-claim guard) |
| O11 | the scope fields are stamped on every family's task | `optc-rerun-results.json`: `test_scope_fields_are_stamped_only_on_scene_tasks` |

Two option C mutants survived the full run, and each now has an isolated
test:
- **O4.** The scene read (`read_state`) builds `config_id` from its own
  argument, and the generic callers of `resource_identity_matches` (retry
  bundles, the garage inverse, deletion) never carry a scene, so no full
  path can reach the branch with another identity. The new test pins the
  check itself.
- **O11.** No other family runs through these modules. The new test pins
  the scene-only rule on the runtime projection: no declarations, another
  family and a mixed task all get no scope fields.

O8–O11 ran with 258 tests: the O4 test was added while the full run was in
progress, and the remaining mutant copies included it. It is a stricter
suite, and each row records its test count. The rerun used the final
259-test tree.

So 109 of 110 are killed. No mutant timed out and none had an import error.

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
- A3 survives again in both R1 runs, the D4 run, both R2 runs and both
  option C runs, for the same reason.

## Usage

From the worktree root:

    systemd-run --user --unit hamcp071-mut --property=MemoryMax=3G \
        --property=OOMPolicy=continue --working-directory=$PWD \
        .artifacts/venv/bin/python docs/evidence/hamcp071-mutations/run_mutations.py [ids...]

With no ids, the runner runs the baseline and then every mutant, and writes
`results.json`. With ids, it runs only those mutants (pass `baseline` to
include it) and writes `results-partial.json`; the R1 rerun's copy is
`r1-rerun-results.json`. `--out NAME.json` overrides the output file (the
D4 run used `--out d4-results.json`; the R2 runs used `--out r2-results.json`
and `--out r2-rerun-results.json`; the option C runs used
`--out optc-results.json` and `--out optc-rerun-results.json`). The R1, D4,
R2 and option C units also set `TMPDIR=<worktree>/.artifacts/tmp`.
