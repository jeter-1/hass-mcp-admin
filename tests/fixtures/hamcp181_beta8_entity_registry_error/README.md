# HAMCP-181 beta.8 entity-registry error-result record

One synthetic record set written by the shipped 2.4.1-beta.8 writer
(`7370ad25eb98e03347d73e152f38881895477d74`), per AGENTS.md "Persisted
Compatibility Fixtures". `tests/test_hamcp181_entity_registry_error_unproven.py`
reads it to prove that the current display-only projection reads a historical
entity-registry error-result record as (r-unproven), never as a proven
non-mutation, and that it never rewrites the record.

| Directory | Shape | beta.8 recipe |
| --- | --- | --- |
| `r_unproven_entity_registry_error_after_assignment` | entity-registry child, `dispatch_failed_confirmed`, `provider_rejected_mutation` without `provider_permission_denied` | the beta.8 entity-metadata harness and the real beta.8 WebSocket client send a rename and icon update to a loopback peer. The peer applies it to the synthetic registry and then answers Core's `unknown_error` result. |

beta.8 mapped every error result other than `unauthorized`/`forbidden` to
`entity_registry_update_rejected`, which it treated as a confirmed rejection.
The stored child therefore carries `provider_rejected_mutation` and no reason
code that would make the rejection proven. The synthetic registry did change:
the generator asserts that. The persisted record does not keep the
`entity_registry_update_rejected` reason string.

`generate.py` drives the unmodified beta.8 runtime. It never reads production
records, contacts Home Assistant or edits serialized JSON. The only network use
is the loopback peer on `127.0.0.1`. UUIDs and timestamps are left as the writer
produced them. `provenance.json` records:

- the source commit
- the generator SHA-256
- the beta.8 source-file hashes
- each committed file's SHA-256
- the shape summary, including the observed codes and the registry state after the call

Lock files and audit logs are not committed.

Regenerate only into a new directory, from a clean checkout of the exact
source commit, with the pinned Python 3.12 interpreter:

```
git worktree add --detach /var/tmp/beta8 7370ad25eb98e03347d73e152f38881895477d74
python tests/fixtures/hamcp181_beta8_entity_registry_error/generate.py /var/tmp/beta8 /var/tmp/new-output
```

Regeneration produces new identifiers and timestamps, so it never replaces the
committed set in place. Do not edit committed records or hashes.
