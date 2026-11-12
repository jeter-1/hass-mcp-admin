# HAMCP-377 rollback proof drivers

Two programs, each run by `scripts/rollback_proof_lane.py` in its own
interpreter with `-B -I`:

- `tip_writer.py SOURCE SCENARIO ...` puts `SOURCE` on `sys.path` and writes a
  store with that tree's own code and offline test harness. `SOURCE` is the
  release being rolled back from.
- `previous_reader.py SOURCE SCENARIO ...` puts `SOURCE` on `sys.path` and
  reads a store with that tree's own code. `SOURCE` is a `git archive` of the
  previous release's tagged commit.

Neither file imports the other, and neither imports the Engineering package
from the checkout it lives in. Each prints one JSON object on its last line of
standard output and reports the version and file path of the package it
actually imported; the lane fails a run whose package path is outside the
source tree it was given.

No committed store exists. Every store is written during the run and read
without being edited in between; the lane hashes each one after the writer
exits, before the read and after the read. The reader writes only to its own
work directory.

Both sides use synthetic providers from their own tree's tests. No Home
Assistant, container or network is involved. The signing key for the test
registry journal is generated in the writer process and never written to disk.
