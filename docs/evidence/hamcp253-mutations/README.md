# HAMCP-253 mutation evidence

`run_mutations.py` copies the tracked tree into the ignored `.artifacts/` and
applies one mutant at a time. Before anything runs, it checks that every
fragment matches exactly once. It then runs
`tests/test_hamcp253_record_acknowledgement.py` and
`tests/test_hamcp253_record_acknowledgement_web.py` without fail-fast.

**Kill rule.** Only an assertion failure (a `FAIL:` line) counts as a kill.
A mutant that only raises (`ERROR:` lines) is reported as `error_only` and is
not a kill. A timeout is not a kill either.

Each round ran as a `systemd-run --user` unit with `MemoryMax=3G`,
`MemorySwapMax=0` and `OOMPolicy=continue`, on a host idle-checked first
(no other Evidence, mutation or full-suite run). Times are CST, 2026-11-03.

| Round | File | Mutants | Killed | Not killed |
|---|---|---|---|---|
| 1 (05:17–05:38) | `results-round1.json` | 57 | 46 | A8, Q3, Q4, Q5, W4 error-only; I8, I9, L1, L2 survived; A21, W2 equivalent |
| 2 (05:39–06:04) | `results-round2.json` | 57 | 52 | Q3, Q4, Q5 error-only; A21, W2 equivalent |
| 3 (06:04–06:06) | `results-round3.json` | 3 (`--only` Q3, Q4, Q5) | 3 | none |

## Final result

The final result is 57 mutants: 55 killed and 2 equivalent, with no
unexpected survivors.

**Tests added or hardened after round 1:**

- **A8:** `test_malformed_fingerprint_is_refused_before_the_record_is_read`.
- **Q3, Q4 and Q5:** the per-read file and quarantine assertions in the two
  `test_repeated_inspection_never_moves_a_corrupt_*` tests. Round 2 showed the
  moved file still raised `FileNotFoundError`, so each read now asserts the
  file exists first.
- **I8:** `test_task_outside_manual_review_is_not_acknowledgeable`.
- **I9:** `test_free_text_event_values_are_reported_as_unrecognized`.
- **L1:** `test_broken_ledger_chain_is_never_trusted`.
- **L2:** `test_edited_ledger_entry_is_never_trusted`.
- **W4:** the 503 test now fails with an assertion if the route raises.

Round 2 reran all 57 mutants against these tests. The only change after
round 2 was one `assertTrue(path.exists())` in each of the two corrupt-record
tests, which only strengthens them. Round 3 reran the three mutants it
affects.

## Equivalent mutants

- **A21 (health memo not cleared after a write).** The memo key includes the
  ledger token `(inode, size, mtime_ns)`. Every append replaces the file, so
  the token changes and the memo misses. The explicit clear is defence in
  depth.
- **W2 (stored principal not compared).** The CSRF binding is keyed by
  `(principal, kind, record_id)`, so a pop by a different administrator never
  finds another administrator's binding. W1 shows that dropping the principal
  from the key is killed.

## Not mutated

- **Removing `LOCK_NB` from the ledger lock.** A blocking lock would
  deadlock the harness. A held-lock refusal test covers the non-blocking
  behaviour instead.
- **The Ingress content-type, body-size and field-count fragments.** These
  fragments are shared with the existing approval routes, so they do not
  match uniquely. The HAMCP-253 web tests cover each limit directly.

## Identifier rename after generation

The repository's offline credential scan (`Test-SecretPatterns` in
`scripts/check.ps1`) matches `sk-` followed by 20 or more identifier
characters. Two mutant IDs matched it by accident, through the substring
`task-`. After the rounds ran, both IDs were renamed by an exact literal
substitution in `run_mutations.py` and in all three results files. The old
spellings are not reproduced here, because quoting them would trip the same
scan. The change was `task` → `tasks` in these IDs:

- `H3-tasks-consistency-guard-removed`
- `Q5-tasks-inspection-read-quarantines`

Nothing else in the results changed: the verdicts, `FAIL:` lines, test counts
and timings are as generated. The per-mutant log paths recorded in the results
point into the ignored `.artifacts/`, where the logs keep their original names.

## Round-1 corrections (R1-M1, R1-M2, R1-M3)

`run_round1_mutations.py` reuses the `run_mutations.py` harness, kill rule and
copied-tree layout. It mutates the code that the round-1 corrections added or
changed, and runs three modules without fail-fast:
`tests/test_hamcp253_record_acknowledgement.py`,
`tests/test_hamcp253_record_acknowledgement_web.py` and
`tests/test_hamcp253_round1_corrections.py`.

The rounds above ran against the code before the corrections. Ten of their
fragments no longer match it: A14, A15, A16, A17, A20, A21 (which now matches
twice), Q5, I6, I8 and I9. The round-1 runner ports A14–A17, A20, A21, I6, I8
and I9 to the current code as `P-*` mutants. It does not port Q5, because
R1Q7 supersedes it: task inspection no longer quarantines at all.

The mutant groups are:

| Group | Area | Mutants |
|---|---|---|
| R1Q | R1-M1, inspection never mutates storage | 9 |
| R1S | R1-M2, stored metadata is never echoed | 15 |
| R1A | R1-M3, audit coordination in the service | 17 |
| R1L | R1-M3, ledger decision and completion validation | 12 |
| P | ported from the earlier rounds | 9 |

| Pass | Unit | File | Mutants | Killed | Not killed |
|---|---|---|---|---|---|
| First (12:20–12:49) | `hamcp253-r1-mutations` | `results-round1-corrections-initial.json` | 52 | 37 | R1Q1, R1L7 error-only; R1S4, S7, S8, S14, S15, R1A8, A10, A15, A16, R1L3, L6, L8, L9 survived |
| Final (13:30–14:10) | `hamcp253-r1-mutations-final` | `results-round1-corrections-final.json` | 62 | 58 | R1A8, R1A16, P-A21, R1L3 equivalent |

The first pass started after the HAMCP-277 mutation unit ended at 12:19. The
final pass started after the HAMCP-277 Evidence unit ended at 13:29. Before
each start, no other Evidence, mutation or full-suite unit was running. The
final pass added the nine `P-*` mutants and R1A17, which removes both
enabled checks together. Its baseline ran 107 tests with return code 0.

**Tests added or hardened after the first pass:**

- **R1Q1:** inspecting an already-quarantined plan now fails with an
  assertion, rather than an error, when it raises `GovernanceError`.
- **R1S4:** `test_task_file_under_another_identifier_reports_the_requested_one`.
- **R1S7:** `test_configured_value_in_a_stored_reason_code_is_screened`.
- **R1S8:** `test_closed_fields_show_only_their_vocabulary`.
- **R1S14:** `test_identifier_shaped_configured_value_is_never_listed`.
- **R1S15:** the Ingress page test asserts the `Stored identifier` row.
- **R1A10:** the unreconcilable-decision test also tries a revoke.
- **R1A15:** `test_transient_sink_failure_still_audits_the_refusal`.
- **R1L6:** `test_completion_naming_a_completion` gives the completion a
  matching audit ID, so the audit-ID check no longer masks the target check.
- **R1L7:** `assert_rejected` fails with an assertion on any other exception
  type and on success.
- **R1L8:** `test_a_settled_decision_is_never_completed_again`.
- **R1L9:** `test_decision_with_an_unknown_entry_type_is_invalid`.

**Final result.** 62 mutants: 58 killed and 4 equivalent, with no unexpected
survivors.

**Equivalent mutants.** Each reason names the reachable states:

- **R1A8 (decision-audit writer skips its own enabled check).** Every call of
  the writer follows an enabled check in the same call. Acknowledge refuses
  `audit_unavailable` before the ledger opens, and start reconciliation
  returns before its transaction. `GovernanceService.audit` and
  `AuditLogger.enabled` and `path` are assigned only at construction, so the
  writer never sees a disabled logger. R1A17 shows that the pair of checks is
  covered.
- **R1A16 (start reconciliation runs without audit).** With a disabled logger,
  the writer's own enabled check returns `False`. Start reconciliation then
  raises `acknowledgement_audit_pending` and catches it, so nothing is
  written. The tests assert that the ledger and audit bytes are unchanged.
  R1A17 removes both checks and is killed.
- **P-A21 (health memo not cleared after a write).** As in the earlier rounds:
  the memo key includes the ledger token (inode, size, `mtime_ns`), and every
  append replaces the file, so the memo misses after any write. The clear is
  defence in depth.
- **R1L3 (no room reserved for the completion).** Each completion names a
  distinct earlier decision. So when no decision is pending, the ledger has an
  even length. `MAX_EVENTS` is 2048, and for an even length, `len+1 > 2048`
  and `len+2 > 2048` give the same answer. An odd length means a decision is
  pending, and append refuses that first.

## Round-2 corrections (R2-M1, R2-M2, R2-M3)

`run_round2_mutations.py` reuses the same harness, kill rule and copied-tree
layout. It mutates only the code the round-2 corrections added or changed, and
runs all four HAMCP-253 modules without fail-fast. The fourth module is
`tests/test_hamcp253_round2_corrections.py`.

One round-1 fragment no longer matches the current code: R1S13, which removed
the record-ID pattern check from the health projection-failure rows. Round 2
replaced that check with the shared screen. R2S3 (the raw stored identifier)
and R2S2 (the screen without configured values) supersede it, so it is not
ported.

The mutant groups are:

| Group | Area | Mutants |
|---|---|---|
| R2S | R2-M1, projection-failure rows in health and `list_change_plans` | 6 |
| R2D | R2-M2, the audit destination check before the ledger | 8 |
| R2U | R2-M3, a write failing after the ledger replacement | 6 |

| Pass | Unit | File | Mutants | Killed | Not killed |
|---|---|---|---|---|---|
| Single (17:13–17:28 CST, 2026-11-03) | `hamcp253-r2-mutations` | `results-round2-corrections.json` | 20 | 20 | none |

The pass started after another session's HAMCP-277 Evidence unit had ended
and the one-minute load average had dropped below 2. At the start, no other
Evidence, mutation or full-suite unit was running. The baseline ran 119 tests
in 43 s with return code 0, on Python 3.12.14.

**Test added before the pass.** R2D2 removes the regular-file check. A
directory is refused without that check, because opening it for append fails.
So `AuditDestinationTests` adds a `character device` destination: the audit
path is a symlink to `/dev/null`. It opens and takes writes, but keeps nothing,
so only the regular-file check refuses it. As a negative control, disabling
the check produced 5 assertion failures before the pass.

**Final result.** 20 mutants, all 20 killed by an assertion failure. There are
no equivalent mutants and no unexpected survivors.

Two notes on what kills what:

- **R2D7 (the probe opens the log with `wb`).** The probe would truncate the
  audit log. `test_reclassify_revoke_and_repeat` kills it, because the earlier
  decision events are gone.
- **R2-M3 completion writes.** The completion-failure tests also pass without
  the R2-M3 code, because a completion failure was already reported as
  pending. They are regression tests. The decision-write test kills R2U1 and
  R2U3–R2U6. `test_persistence_failure_records_nothing` (before the
  replacement) kills R2U2.
