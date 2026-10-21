# HAMCP-213 PR001 test fixtures (anonymized copies)

These eight `op{1..4}_{before,after}.yaml` files are byte-identical **test
copies** of the anonymized design fixtures in `docs/evidence/hamcp213/pr001/`.
Those were committed by PR #287 (commit
`d4565648699798f485448f066180691670534141`, tree
`d2034331698d9f08b53b2ab1b1590dd0cbe72de5` for that directory on `main`).
`tests/test_hamcp213_pr001_fixtures.py` checks that each copy still matches
its source by sha256.

The provenance of the source files is recorded in
`docs/evidence/hamcp213/pr001/README.md`:

- They are anonymized structural transforms of the owner's real PR001 example
  (change plan `7b651ef4…`, four operations touching one garage cover). The
  owner supplied the configurations outside the repository.
- Every entity, notify service, trigger id, notification tag, action id and
  category is renamed. Free text is replaced with numbered neutral text.
  Structure, non-string values, step order and changed paths are unchanged.
- A privacy scan found 0 violations, and its positive control found 218.

The private source, the anonymizer and the real-to-anonymized mapping are not
in the repository, and no test reads them. These copies were not regenerated.
Nothing here is a persisted-format compatibility fixture: the files are
candidate Home Assistant configurations, not records written by shipped code.

| File | sha256 |
| --- | --- |
| `op1_after.yaml` | `73662fe925acb7bf7373010145ac7db9aa79a1f7e2c92310c9a404abbec0e8b8` |
| `op1_before.yaml` | `b2c55754933a07db08a3f4322b9d4df05ad637d4cafd00c2a5fcd3c91e40d7e0` |
| `op2_after.yaml` | `93d6328c207cc07c69db63fbf68edd3ed6dd989e286e4ce6c5e3a3eb35e56ba7` |
| `op2_before.yaml` | `c123a4359d1685642711c841d362fb260a2b0f5760c7e30e949ea97146d3d7dd` |
| `op3_after.yaml` | `b2a260e8babb35ede9928081b2234c8781f50bf736cd11895bc6110a2a04a4c7` |
| `op3_before.yaml` | `c831e8a0cbd1e1a557ff49354998f5c507b074077bf68ffeefa0e03b5542ebdf` |
| `op4_after.yaml` | `a31e82be228555fd9f0b71af5b155df659541d7f2847d4373ee2db6489f36738` |
| `op4_before.yaml` | `be5f7ab9b2260bff475073deab3a0c41b99157a81f5a7c9de10e475256af9516` |
