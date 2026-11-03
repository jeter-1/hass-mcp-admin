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
