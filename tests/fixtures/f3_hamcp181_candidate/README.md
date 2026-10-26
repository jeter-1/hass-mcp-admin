# HAMCP-181 PR2 candidate-writer fixtures

These synthetic records were written by the HAMCP-181 PR2 candidate writer at
commit `82510f78b6a40c6e21fbc0c48a371131bbed6f66`, following AGENTS.md
"Persisted Compatibility Fixtures" and design section 4.3 items 3 and 4.
Two tests read them, each in two ways.

- `tests/test_hamcp181_beta36_downgrade.py` reads them under the shipped
  2.4.1-beta.36 reader (`16b724ec0b1d0fa8aae9705df569704f27ad783e`, tag
  `v2.4.1-beta.36`). This is the governed-plan rollback target.
- `tests/test_hamcp181_beta8_downgrade.py` reads them under the shipped
  2.4.1-beta.8 reader (`7370ad25eb98e03347d73e152f38881895477d74`, tag
  `v2.4.1-beta.8`). This is the fan/power/task-only floor.
- Both tests also read them under the current reader. That run checks the T2
  projections and that nothing is rewritten.

Both downgrade tests apply section 4.3 item 3.

`generate.py` drives the unmodified candidate runtime through the shipped test
harnesses with synthetic fakes. Before it writes anything, it requires clean
tracked files and a clean `git status --short hass_mcp_engineering_beta/`.

The generator never edits serialized JSON. It reads no production records and
contacts no Home Assistant instance, provider, or external network. One shape
uses the disposable loopback WebSocket peer from HAMCP-183. UUIDs and
timestamps are kept exactly as the writer produced them. Lock files and audit
logs are not committed.

`pr2/provenance.json` records:

- the candidate commit;
- the clean status outputs;
- the generator command and the generator's SHA-256;
- the Python version;
- the SHA-256 of each runtime source file;
- each shape's identifiers and the writer's own summary;
- the SHA-256 of every committed file.

Every record is terminal.

| Directory | Shape | Task state / terminal_outcome / dispatch_phase | Recipe |
| --- | --- | --- | --- |
| `pr2/a_helper_already_present` | (a) child, T2 (a) task | succeeded_verified / desired_state_already_present_not_dispatched / not_dispatched_already_present | helper-state plan, desired state already observed |
| `pr2/b_configuration_callback_not_sent` | (b) child, T2 (b) task | failed_post_dispatch / failed_after_intent_not_dispatched / intent_recorded_not_sent | automation update, adapter callback fails after the intent |
| `pr2/b_hvac_helper_appeared_not_sent` | T2 (b) task, chain | failed_post_dispatch / failed_after_intent_not_dispatched / intent_recorded_not_sent | HVAC plan, shipped create preflight refuses after the intent; two later children are never claimed |
| `pr2/b_dashboard_setter_not_sent` | dashboard (b) child | failed_post_dispatch / failed_after_intent_not_dispatched / intent_recorded_not_sent | Core authority retires during the guide read; setter never called |
| `pr2/r_proven_configuration_4xx` | (r-proven) configuration child | failed_post_dispatch / failed_post_dispatch / sent_rejected_no_mutation | automation write answered with REST 400 |
| `pr2/r_unproven_operational_rejection` | (r-unproven) operational child | failed_post_dispatch / failed_post_dispatch / sent_failed_mutation_unproven | reload provider rejection after the send |
| `pr2/r_unproven_entity_registry_error` | entity-metadata registry-error child (see note) | failed_post_dispatch / failed_post_dispatch / intent_recorded_send_uncertain | registry `unknown_error` result, registry unchanged |
| `pr2/c_response_lost_verified` | (c) child verified by readback | succeeded_verified / succeeded_verified / intent_recorded_send_uncertain | automation write lands, response lost |
| `pr2/sent_verified_configuration` | sent-and-verified child | succeeded_verified / succeeded_verified / sent_response_received | automation write and exact readback |
| `pr2/v9_entity_metadata_already_present` | V9 task | succeeded_verified / desired_state_already_present_not_dispatched / not_dispatched_already_present | entity rename already present before apply |
| `pr2/mixed_three_child_parent` | three-child mixed parent | manual_review_required / manual_review_required / sent_response_received | HVAC plan: two verified children, automation answered with REST 400 |
| `pr2/fan_sent_verified` | fan record and prepared request declaration | succeeded_verified / succeeded_verified | typed fan `turn_on` 50% |
| `pr2/power_sent_verified` | power record and prepared request declaration | succeeded_verified / succeeded_verified | typed power `turn_on` |

The candidate writer cannot produce an r-unproven entity-registry child whose
codes are `provider_rejected_mutation` without `provider_permission_denied`.
For a registry error result, HAMCP-183 records `provider_error_after_send` and
`provider_response_indeterminate`, with the outcome `verification_mismatch`.
That variant is what the directory holds, and no other shape was substituted
for it.

## Regenerating

Regenerate only into a new, empty directory, from a clean checkout of the
candidate commit, with the pinned Python 3.12 interpreter:

```
python tests/fixtures/f3_hamcp181_candidate/generate.py NEW_OUTPUT_DIRECTORY
```

Each regeneration produces new identifiers and timestamps. It therefore never
replaces `pr2/` in place, and the tests do not depend on identifier or
timestamp values. Do not edit committed records or hashes.

## Shipped reader driver

`shipped_reader.py` is not a fixture. Each downgrade test runs it in a separate
`-I` interpreter over a copy of `pr2/` and one of two sources: a `git archive`
extraction of the shipped commit, or the current source.

For each shape, the driver builds the reader's own service and runtime with
that source's own test fakes. It then runs:

- the startup recovery pass;
- the reads;
- a duplicate apply or duplicate control;
- a restart with a second recovery pass.

It counts every gateway and transport method call. It records each file the
reader adds or modifies and, for each modified task file, the events it appends.
It prints its observations as JSON. It does not assert anything; the test does.

If the interpreter's `mcp` differs from the reader's pin, the driver replaces
only the reader's pinned-SDK import gate and records that it did. beta.8 pins
`mcp==1.28.1`. To run beta.8 without the shim, set `HAMCP181_BETA8_PYTHON` to
an interpreter that has beta.8's exact `requirements.txt` installed. beta.36
pins `mcp==1.30.0`, the current pin, so it normally needs no shim.
`HAMCP181_BETA36_PYTHON` selects its interpreter in the same way.
