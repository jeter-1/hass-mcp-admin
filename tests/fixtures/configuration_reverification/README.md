# Exact beta.6 writer fixture

Synthetic records generated through shipped source
`6e1c9f1412f97b1171bc38aafe6815ca8abe54eb`, before the trigger-condition verifier
correction. No production records, human approval claim, or device execution.

`beta6-history-provenance.json` binds all twelve original record files, eight
writer source files and the original generator. `generate-beta6-history.py` is
retained byte-for-byte with its original local writer/archive path. To regenerate
in another environment, prepare that exact Git revision and map the generator's
WRITER path to it in a disposable copy; do not execute against a live installation.
Random plan/task/challenge identities and timings are writer outputs; regeneration
proves the format/behavior, not identical random IDs. Never edit a hash-bearing
record. The generated helper verifies, the automation is saved once but reports
verification_mismatch; the parent remains manual_review_required.

The regression reads these exact bytes, presents synthetic saved configurations,
and proves supplementary verification without rewriting original history or
issuing another configuration write. Publication of the fixture does not imply
that the real household task has been read, changed, or resolved.
