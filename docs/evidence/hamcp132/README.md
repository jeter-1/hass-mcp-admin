# HAMCP-132 recorder entity-filter truth table

`filter-truth-table.json` is Home Assistant Core 2026.11.3's own answer to
"is this entity recorded?" for 14 fixed filter configurations × 12 fixed
entity IDs. The configurations cover:
- cases 1, 2, 3, 4a, 4b and 6;
- 4a ignoring `exclude.domains`;
- the 4b `include.entities` override, both inside and outside an
  excluded domain or glob (an entity in both entity lists is recorded from
  2026.11.3);
- glob precedence.

`tests/test_hamcp132_recorder_settings.py` checks the Engineering port
(`recorder_settings/entity_filter.py`) against every row.

**Provenance.** `generate_filter_truth_table.py` takes `homeassistant/helpers/entityfilter.py` from Core tag
`2026.11.3`, commit `92da9f154079459b9e547126f9bd9fce726de16a`. It verifies the file's SHA-256
(`0611e7ee2949264518363e1611f23e7da8e0134ffc1bae9280c89f46767fb4c0`) before
anything runs. It then extracts and compiles only `_convert_globs_to_pattern`,
`generate_filter` and `_generate_filter_from_sets_and_pattern_lists`, with
`split_entity_id`, `lru_cache` and `MAX_EXPECTED_ENTITY_IDS` stubbed, and
evaluates the fixed inputs. The output is not edited by hand.

Regenerate with:

```sh
python docs/evidence/hamcp132/generate_filter_truth_table.py \
    --core <core-2026.11.3>/homeassistant/helpers/entityfilter.py \
    --output docs/evidence/hamcp132/filter-truth-table.json
```

When the pinned source is present at the test's cache path, the suite
regenerates the table and compares it with the committed file; otherwise that
one test is skipped and the committed table is used.
