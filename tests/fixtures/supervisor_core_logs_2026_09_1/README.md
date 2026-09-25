# Exact Supervisor Core-log contract fragments

These Apache-2.0 source excerpts are from Home Assistant Supervisor 2026.09.1,
commit `40e3ee7640a3c44abe67f1a1397f39c3cd949806`. See `LICENSE` and
`provenance.json` for the upstream repository, full-file hashes, source line
positions, extraction method and per-fragment hashes. No production data is
present. Only indentation is removed; executable behavior is not rewritten.

The offline test executes the fragments with synthetic process, installed-app
and journal dependencies. This isolates security, Range forwarding, streaming
and ambiguous EOF behavior. It does not replace the separate assembled API
probe in `scripts/core_log_supervisor_probe.py` or installed acceptance.

To regenerate, obtain the exact source commit, parse each recorded source path
with Python `ast`, select the named node including decorators, slice its original
lines, and apply `textwrap.dedent`. Preserve the source fragment's terminal
newline. Compare both full-file and fragment SHA-256 values. Never regenerate
from a moving branch or hand-reconstruct an older implementation.
