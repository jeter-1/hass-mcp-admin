# HAMCP-132 recorder entity-filter truth table

`filter-truth-table.json` is Home Assistant Core 2026.10.1's own answer to
"is this entity recorded?" for 13 fixed filter configurations × 12 fixed
entity IDs. The configurations cover:
- cases 1, 2, 3, 4a, 4b and 6;
- 4a ignoring `exclude.domains`;
- the 4b `include.entities` override;
- glob precedence.

`tests/test_hamcp132_recorder_settings.py` checks the Engineering port
(`recorder_settings/entity_filter.py`) against every row.

**Provenance.** `generate_filter_truth_table.py` takes `homeassistant/helpers/entityfilter.py` from Core tag
`2026.10.1`, commit `abe1fdd7cd6b4de1715c87b6fd87d92699514ef9`. It verifies the file's SHA-256
(`0ce66e76729c568f4c23d90166f378524cf7a6ebd67b11110ab2c6a20a1136c2`) before
anything runs. It then extracts and compiles only `_convert_globs_to_pattern`,
`generate_filter` and `_generate_filter_from_sets_and_pattern_lists`, with
`split_entity_id`, `lru_cache` and `MAX_EXPECTED_ENTITY_IDS` stubbed, and
evaluates the fixed inputs. The output is not edited by hand.

Regenerate with:

```sh
python docs/evidence/hamcp132/generate_filter_truth_table.py \
    --core <core-2026.10.1>/homeassistant/helpers/entityfilter.py \
    --output docs/evidence/hamcp132/filter-truth-table.json
```

When the pinned source is present at the test's cache path, the suite
regenerates the table and compares it with the committed file; otherwise that
one test is skipped and the committed table is used.
