# HAMCP-181 beta.8 dispatch-phase fixtures

These are synthetic records written by the shipped 2.4.1-beta.8 writer
(`7370ad25eb98e03347d73e152f38881895477d74`), per AGENTS.md "Persisted
Compatibility Fixtures" and design section 4.3 item 4. The HAMCP-181 PR1
projection tests read them to prove that the new display-only projection
interprets historical records correctly and never rewrites them.

`generate.py` drives the unmodified beta.8 runtime through beta.8's own test
harnesses and synthetic fakes. It never reads production records, contacts
Home Assistant, opens a network connection or edits serialized JSON. UUIDs and
timestamps are left as the writer produced them. `provenance.json` records the
source commit, the generator SHA-256, the beta.8 source-file hashes, each
committed file's SHA-256 and the identifiers of every shape. Lock files and
audit logs are not committed.

| Directory | Shape (design section 2.1) | beta.8 recipe |
| --- | --- | --- |
| `beta8/a_helper_already_present` | (a) preflight no-op child | helper-state plan, desired state already observed |
| `beta8/b_dashboard_setter_not_sent` | dashboard (b) child | Core authority retires during the guide read; setter never called |
| `beta8/r_proven_configuration_4xx` | (r-proven) configuration child (P1) | automation write answered with REST 400 |
| `beta8/r_unproven_operational_rejection` | (r-unproven) operational child | reload provider rejection after the send |
| `beta8/c_response_lost_verified` | (c) child verified by readback | automation write lands, response lost |
| `beta8/sent_verified_configuration` | sent-and-verified child | automation write and exact readback |
| `beta8/v9_entity_metadata_already_present` | V9 task | entity rename already present before apply |
| `beta8/mixed_three_child_parent` | three-child mixed parent | HVAC plan: two verified children, automation answered with REST 400 |
| `beta8/fan_sent_verified` | fan record and prepared request declaration | typed fan `turn_on` 50% |
| `beta8/power_sent_verified` | power record and prepared request declaration | typed power `turn_on` |

Regenerate only into a new directory, from a clean checkout of the exact
source commit, with the pinned Python 3.12 interpreter:

```
git worktree add --detach /tmp/beta8 7370ad25eb98e03347d73e152f38881895477d74
python tests/fixtures/f3_beta8_dispatch_phase/generate.py /tmp/beta8 /tmp/new-beta8-output
```

Regeneration produces new identifiers and timestamps, so it never replaces
the committed set in place. Do not edit committed records or hashes.
