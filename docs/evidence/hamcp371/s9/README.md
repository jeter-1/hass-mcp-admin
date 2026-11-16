# HAMCP-371 S9: ha_get_history on ha-mcp 9.2.0

S9 replaces the S8 interim refusal with a 9.2.0 history module,
`hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_history_9_2.py`.
Its tests are in `tests/test_hamcp371_history_offsets.py`.
`tests/test_hamcp371_real_core_replay.py` (`RealCoreReplay920`) replays the
real-Core history receipts through the module.

This is a local candidate only. Nothing was signed, published, pushed or
deployed, and no Home Assistant instance was accessed.

## Why a 9.2.0 module

At `51e3aac7`, exact 9.2.0 `tools_history.py` resolves an ISO `start_time` or
`end_time` that has no offset in Home Assistant local time. 9.1 forced UTC.
The receipts show the change: `v16-history-offset-free` echoes `-06:00` on
9.2.0 and `+00:00` on 9.1.2. Engineering therefore does not forward a time
whose meaning changed between the releases (21:36 record, items 5 and 6; 22:59
record, item 7).

| Input | 9.2.0 result |
| --- | --- |
| Full ISO date and time with an explicit `Z` or `±HH:MM` offset (seconds and up to 6 fraction digits optional) | Forwarded unchanged |
| Relative form matched by the exact upstream pattern `^(\d+)(min|[hdwm])$` (applied, as upstream does, after `lower().strip()`) | Forwarded unchanged |
| Any other value `datetime.fromisoformat` parses: offset-free, date-only, `+0000`, a space separator, the basic format, 7 fraction digits | Refused before dispatch: `invalid_request` with `details.failure_category` `history_time_offset_required` and a fixed message |
| A value upstream cannot parse, or a non-string value | Refused before dispatch with the existing generic `invalid_request` (`argument_validation`) |

The refusal never echoes the value. When either time is unparseable, that
generic refusal is used. Each case has a test, including a positive "local"
test with `-06:00`. On 9.1.0 and 9.1.2 offset-free values are still forwarded
unchanged (tested).

## Gateway: the four R1 entries (22:59 record, item 1)

These are the only `upstream_read_gateway.py` changes:

1. `history_time_offset_required` is added to `_FAILURE_CATEGORIES`.
2. `_public_failure` maps it to `invalid_request`, not retryable.
3. A fixed `_UPSTREAM_DOMAIN_MESSAGES` entry is added for
   `("ha_get_history", "history_time_offset_required")`.
4. An `except HistoryTimeOffsetRequired` clause is added as the first handler
   after `read_arguments`. It raises `_GatewayFailure(..., dispatched=False)`.
   The same entry adds the import of the exception from the new module.

The 9.1 modules never raise `HistoryTimeOffsetRequired`, so 9.1.0 and 9.1.2
behave byte-identically. Their tests pass unmodified (see Validation).

## Result: closed key set, counts and warnings (items 7 to 12)

- The module runs before the unedited 9.1 projection
  (`upstream_history_9_1`). The 9.2.0 history and statistics shapes equal
  9.1's in every real-Core receipt.
- `ADMITTED`, `POLICY_DROPS` and `OPEN` are the S7 contract row 16. A test
  asserts that they equal `tests/fixtures/ha_mcp_9_2_0/output-key-contract.json`.
- A key outside the set is withheld, not refused. `fields_withheld` counts the
  distinct unreviewed names. `fields_dropped_by_policy` counts the distinct
  policy-drop names. Each count is capped at 999 and no names are disclosed.
- Integration-defined containers (`attributes`, `config`, `filter`,
  `selector`) pass whole and are not counted.
- The counts sit at the top level of the read's `data`, next to the history
  envelope's own `data` and `metadata` (N7a).
- An upstream warning passes only when it matches one of the 9.1 projection's
  closed templates, with the same conditions: the timezone failure, the
  `fields` typo, and for statistics the unit, reset and empty templates for
  requested entities. Any other warning, a non-string value, or a repeat of a
  single-use template is replaced by the fixed code `upstream_warning_replaced`
  in the history `data.warnings`. `upstream_warnings_replaced` counts these
  per occurrence.
- After the projection, each echoed query bound must carry an offset, or the
  read fails closed (`invalid_response`). The bounds checked are
  `data.period`, `data.time_range` and each history entity's `period`. This
  response-side check is new in S9.

## Disclosures

1. **Contract row 16 corrected (flagged for the architect).** The S7 row named
   only `data`, `metadata` and `warnings`. S9 lists every name the shipped 9.1
   projection reads or returns, cited to `upstream_history_9_1.py`. It also
   records the shipped 8.5-parity drops (`summary`, `statistics_metadata`,
   `unit_source`, `unit_reason`, `last_reset`) as policy drops. The generator
   note says "pending ruling". The names come from the reviewed 9.1 projection
   source, not from a 9.1.2 value copied by analogy.
2. **Flat-name limitation.** Counts are distinct names over the whole
   response. The 9.1 projection removes a statistics row's `end`, but the
   name stays admitted through `period` and `time_range`, so it is not counted
   as a policy drop.
3. **Structural novelty still fails closed.** A new admitted-name key in an
   unexpected place, for example inside an entity `period`, still makes the
   9.1 projection refuse (`invalid_response`). It is not withheld.
4. **A replaced unit or empty statistics warning can fail closed.** When that
   happens, the 9.1 cross-check between warnings and rows refuses the read.
5. **Warnings are counted per occurrence.** Key names are counted as
   distinct names.
6. **The reviewed warning list for history** is the shipped 9.1 closed
   template set. The contract's `reviewed_warnings` for row 16 is `[]`.
7. **An empty-string time is refused as unparseable.** Upstream treats an
   empty `end_time` as omitted, so Engineering is stricter here.
8. **Relative overflow.** The relative check repeats upstream's own overflow
   refusal (`now - timedelta(...)`). An overflowing value is refused as
   unparseable.
9. **Pre-existing metadata wording.** The shipped 8.5 metadata note says the
   query bounds are unchanged. In the 9.2.0 receipts, the echoed bounds carry
   Home Assistant's local offset (`-06:00`), and relative inputs echo
   `+00:00` with microseconds. The note is disclosed here, not changed.
10. **The replay host is renamed (test fixture interaction).**
    - The configured upstream URL's hostname is a known secret
      (`configuration.py`, the `secret_values` derivation), so any output
      text containing it is redacted.
    - The shared fixture `test_ha_mcp_production_readmission._settings` uses
      the bare host `upstream`. That turned Engineering's own key
      `upstream_warnings_replaced` into `[REDACTED:token]_warnings_replaced`.
    - The HAMCP-371 replay helper now sets a distinct synthetic URL
      (`REPLAY_URL`). The shared fixture is unchanged.
    - Inference, not checked live: a production upstream hostname is not a
      bare word, so it would not collide. A deployment whose upstream
      hostname is a substring of an Engineering key would see that key
      redacted. This is pre-existing behaviour and not changed here.
11. **Edits to HAMCP-371's own S8 tests.**
    - `tests/test_hamcp371_admission_core.py`: the two S8 interim history
      tests are replaced, one by a module-routing test and one by an
      offset-free gateway refusal with no upstream call.
    - `tests/test_hamcp371_real_core_replay.py`: `v16-history-offset-free`
      now expects no dispatch and `history_time_offset_required`
      (`REFUSED_BEFORE_DISPATCH_920`, 9.2.0 only); `_cases_920` excludes it;
      the history warnings are read from the inner `data`; the docstring and
      `REPLAY_URL` (item 10) are updated.
    - No 9.1 test changed.

## Validation (2026-11-15, local)

| Check | Result |
| --- | --- |
| `tests.test_hamcp371_history_offsets` | 28 tests OK |
| `tests.test_hamcp371_admission_core` | OK |
| `tests.test_hamcp371_real_core_replay` | 34 failures and 85 errors, all in `RealCoreReplay920` on non-history reads, by design until S10. The S8 baseline was 46 and 98; exactly the four history calls (`r16-history`, `v16-history-relative`, `v16-history-offset-free`, `v16-history-statistics`) dropped out, and no new call fails. Every other class passes. |
| `generate_output_key_contract.py --check` | up to date |
| `scripts/review_upstream_read_release.py validate` | passes |

| Unmodified regression modules, run one per module: every `test_hamcp319_*` module, `test_hamcp131_history_*`, `test_hamcp351_ha_mcp_912_admission`, `test_hamcp162_yaml_admission`, `test_hamcp371_error_classification`, `test_ha_mcp_production_readmission` and `test_upstream_8_5_compatibility` | 28 modules, 2,257 tests, all OK |
| Fast tier (`-BaseRef origin/main`, 18 declared protected paths, test targets `test_hamcp371_history_offsets`, `test_hamcp371_admission_core` and `test_hamcp319_history`) | passed |
| Repository scan (`docs/evidence/hamcp371/s6/comparison/scan.py`) and leak check, run on each changed file | 0 hits; each file PASS |

The Full and Evidence tiers were not run: they are not part of S9.
