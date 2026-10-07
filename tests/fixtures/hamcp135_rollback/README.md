# Exact beta.16 persisted compatibility fixture

`beta16/` was written by commit
`fce97c66848796d68d989f12efcf53fa43ddf32d` (protected Engineering beta.16).
`generate_beta16.py` imports that commit's test harness and production writer,
creates the full synthetic forward plan, obtains external fixture approval,
executes five fake configuration writes, and asks the shipped rollback path for
its prohibited inverse. It copies the resulting store without editing records.

The source was extracted with `git archive` of that exact local commit. Run the
existing read-only Python 3.12 environment with bytecode disabled and arguments
`generate_beta16.py SOURCE NEW_OUTPUT`. No production input is used; the shipped
committed `hamcp135_configuration/caller_owned_retry.json` is synthetic before
hashes are computed. `PROVENANCE.json` identifies writer, generator and input
hashes; `SHA256SUMS` seals every returned file, including empty lock files.

The archive identity, exact command and stdout are in the ignored review packet.
Independent review should verify the archive commit and writer, reproduce its
shape, and verify these hashes. Do not regenerate old records with current code,
reconstruct hashed serializations or edit the fixture to fit an assertion.
