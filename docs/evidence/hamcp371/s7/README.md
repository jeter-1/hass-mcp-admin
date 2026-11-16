# HAMCP-371 S7: promoted receipts and the output-key contract

## Component 2.2.1 tree list

`component-2.2.1-tree.txt` lists the `ha_mcp_tools` component tree at upstream
tag `v8.6.0`, commit `fc54437a`, as C4 requires for the SR 8.5.0 control. It
has 54 files. It was read from the upstream store by git plumbing, with lazy
fetch off.

- sha256 `26524feadbbec8e5815bcd54c32a3b5d01d85896f889e8ca9ddc754e1f70bdf0`.

The leak check passed on it before the copy, and `s6/comparison/scan.py`
found 0 hits. Before SR, the component tree built for the control is checked
against this list (the upstream store gap).

## Output-key contract

`generate_output_key_contract.py` reads the committed S6 receipts and writes
three files under `tests/fixtures/ha_mcp_9_2_0/`:

- `output-key-contract.json`, the reviewed key table (ruling 2).
- `provenance.json`, with one class C entry per successful receipt (path,
  sha256, pointer `/exchange/response/result`) and the sha256 of each source
  file the table cites.
- `SHA256SUMS.json`, the sha256 of the 203 receipts and call indexes it reads.
  This includes the two YAML calls made as the ordinary user.

Each receipt is checked against its run's `SHA256SUMS` and its index entry
before it is used. Receipts are never edited. Regenerate with:

    python docs/evidence/hamcp371/s7/generate_output_key_contract.py

Add `--check` to compare without writing. The output is the same each time.

### How the table is built, per read

- **Observed names.** These are the distinct key names on 9.1.2 and on 9.2.0,
  over both profiles and all of the read's calls.
  - The walk does not go into open containers. Four are ruled: `config`,
    `attributes`, `selector` and `filter`. Further open containers are
    proposed below.
  - It does not go into policy drops.
  - For map containers, it skips the data keys and reads only the row values.
- **Closed reads.** A name seen in a 9.1.2 receipt is admitted, with that
  receipt as its source. A name the shipped 9.1 projection requires
  is admitted with its `file:line` source. This is marked as an interpretation
  pending ruling.
- **Rebuild reads** (13, 18, 20, 26 and 27). The shipped allowlist is the
  admitted set. `withheld_by_rebuild` lists the observed names that the
  shipped rebuild does not carry. S10 counts those names.
- **New in 9.2.0.** Each key new in 9.2.0 is `admitted` only with a recorded
  review. That is a research G ruling, or for a closed read, a shipped source.
  Any other new key is `withheld_pending_review`. Names are flat per read, so
  a ruled name already seen on 9.1.2 is listed too, with the profiles it was
  seen on.

### What the receipts show (9.1.2 to 9.2.0)

| Read | Change |
|---|---|
| 24 `ha_list_services` | `services` is a one-level map keyed `domain.service`. `fields` is a one-level map that can nest. New: `collapsed`, `filter`, `integration`, `optional` and `response`, all withheld pending review. Removed: `type` |
| 22 `ha_get_zone` | `friendly_name` is removed. `persons` and `device_trackers` were already in profile A 9.1.2 and are new only on profile B. All three ruled names are admitted (2e) |
| 25 `ha_search`, 17 `ha_get_overview` | `name` is admitted by ruling (2c, 2d). As a flat name it was already present on 9.1.2 |
| 9, 26 | `read_only`, `secret` and `later_forms` are withheld pending review. Those modes are not exercised by the receipts (2f) |
| 26 `ha_get_integration` | The rebuild withholds 13 entry names, for example `created_at`, `unique_id` and `setup_error` |
| 20 `ha_get_system_health` | The rebuild withholds `component_count`, `manage_url` and `message`. The unit for the count inside `info` is pending ruling |
| 1 `ha_config_get_automation` | One reviewed upstream warning (profile A, both versions) |
| 13 `ha_get_device` | The shipped per-occurrence count is kept on 9.2.0, as the contract note records (22:59 record, item 2) |
| 27 `ha_get_app` | No successful receipt. r27-apps fails on both versions, and SR records the body and the error category |

### Agent proposals pending ruling

These are listed in `proposals_pending_ruling` in the contract:

- the map containers;
- the extra open containers: `capabilities`, `options`, `result`, `info`,
  `example` and `default`;
- admitting unobserved names that the shipped projection names;
- using as reviewed warnings exactly the strings observed in the 9.1.2
  receipts.

## Replay tests

`tests/test_hamcp371_real_core_replay.py` checks four things:

- **Receipt integrity.** Every bound file matches its hash, the cited sources
  are current, and the generator reproduces the fixture byte for byte. A
  changed hash is refused.
- **The contract.** It covers all 27 reads. No Engineering-added name matches
  an upstream key in any receipt. Policy drops and unreviewed keys are never
  admitted. The ruled names are admitted.
- **The 9.1.2 replay.** Each profile A and B receipt is replayed through the
  real gateway on the shipped 9.1.2 route. Each one sends the recorded wire
  arguments and succeeds. The exceptions are r27-apps and the three p4 absent
  reads, which fail closed with a fixed category and no upstream message or
  code.
- **The 9.2.0 replay and N29.** These cover the withheld counts, injection,
  open containers, replaced warnings, history, zone and services, and the
  administrator and ordinary-user YAML reads. They fail by design until S8
  creates the 9.2.0 capture and admission and S10 adds the closed key sets
  (step table S7). Today all 9 stop on the missing
  `docs/evidence/upstream-read-compatibility/ha-mcp-9.2.0.json`.

S8 changes `upstream_device_projection.py` (`REVIEWED_VERSIONS`), so the
contract is regenerated there. Its `source_files` sha256 values move with
that change.
