# HAMCP-181 PR2 mutation evidence

`results.json` records every mutant of the HAMCP-181 PR2 (+HAMCP-214) campaign:
its concept, exact edit, site, the stage that killed it and the killing tests.

- 53 mutants over the not-sent proofs (M1, M6–M12), the shared projection on
  every surface (M2), HAMCP-214 plan verification and rollback availability
  (M3, M5), no-mutation claims (M4), health (HEALTH1–3) and the OD-6(ii) send
  time (SEND1–4).
- Campaign: 46 killed, 7 survived (M2e, M6b, M6d, M8b, M8c, M11c, SEND1).
  Every survivor was a real test gap, not an equivalent mutant. Each was closed
  by a new test in `tests/test_hamcp181_proven_no_call_producers.py` or
  `tests/test_hamcp181_terminal_outcome_t2.py` that passes at head and fails
  under the same mutant (`gap_closed_by`, `gap_closure`). Final: 53 of 53
  killed.
- Mutants were applied to disposable `git archive` copies of
  `4624c8947d4c378b246d139859645f01704c75f3`; later PR commits change no
  mutated runtime site. Runs were memory-capped systemd units (MemoryMax
  2500M), fast modules first, then the 16-module set one module per process.
- `harness/` holds the exact scripts: `mutants.py` (specs), `apply.py`
  (single-occurrence replacement), `driver.sh` and `run_one.sh` (campaign),
  `mk.sh` (gap-closure copies) and `build.py` (this `results.json`). Their
  absolute paths are the local scriptbox work directory; log names in
  `results.json` are relative to that run's log directory and are not
  committed.
- **Rerun on the final branch (2026-10-25).** After the main merge (#312)
  and the beta.36 rollback test, the same 53 specs ran with the same harness
  on a `git archive` copy of `66c18e9c2ab08476d539a0a612a65b32ca98a077`.
  Every edit applied as a single occurrence. Result: **53 of 53 killed**.
  - 50 were killed by their fast modules.
  - M3c was killed by `test_hamcp181_terminal_outcome_t2` and M5c by
    `test_f3_runtime_integration`, the same stages as the campaign.
  - SEND1, a campaign survivor, was killed by its gap-closure tests in
    `test_hamcp181_proven_no_call_producers`.

  `results.json` still records the campaign. The rerun logs are local only.
