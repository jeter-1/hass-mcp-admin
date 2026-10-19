# Exact Supervisor add-on store discovery fragments

These Apache-2.0 source excerpts are from Home Assistant Supervisor 2026.09.1,
commit `40e3ee7640a3c44abe67f1a1397f39c3cd949806`. See `LICENSE` and
`provenance.json` for the upstream repository, full-file hashes, source line
positions, extraction method and per-fragment hashes. No production data is
present. Only indentation is removed; executable behavior is not rewritten.

`tests/test_stable_v1_retirement.py` executes the fragments offline against a
disposable tree that mirrors this repository's tracked paths. It proves which
add-on configuration files a Supervisor clone of this repository discovers
(HAMCP-203: retired stable v1 must not be discoverable, Engineering must be),
and which keys `repository.yaml` can carry. It does not replace a booted
Supervisor or an installed-store observation.

To regenerate, obtain the exact source commit, parse each recorded source path
with Python `ast`, select the named node (for `StoreData._find_app_configs`,
the method inside the class) including decorators, slice its original lines,
and apply `textwrap.dedent`. Compare both full-file and fragment SHA-256
values. Never regenerate from a moving branch or hand-reconstruct an older
implementation.
