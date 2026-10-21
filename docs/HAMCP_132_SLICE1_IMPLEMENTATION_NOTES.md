# HAMCP-132 slice 1 implementation notes

Slice 1 implements design sections 3A and 3C as two read-only
Engineering-native tools, `get_recorder_settings` and
`get_unregistered_entities`. The design and Part 1 source verification against
Home Assistant Core 2026.10.1 are in
[`HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md`](HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md)
(draft PR #291, commit `26c1eb0`). Josh's owner decisions (i)–(vii), recorded
on 2026-10-20 in design section 8, apply unchanged:
- no options flow;
- slice 1 is A + C;
- the `ha_mcp_tools` commands are deferred;
- at most 50 entities per `recorder/entity_options/get` call, through the batch;
- `unknown`, not a default, when packages or `!include` are present;
- two tools;
- `template <label>:` keys stay unaddressable (slice 2).

This file records where the implementation departs from, narrows or adds to
the design, so review can check each one against source. Every item below has
tests in `tests/test_hamcp132_recorder_settings.py` or
`tests/test_hamcp132_unregistered_entities.py`.

## Deviations and additions

1. **The `homeassistant` node is read, narrowly.** When
   `homeassistant.packages` is not found, the reader cannot say whether
   `homeassistant` is missing or `packages` is missing. A third fixed read of
   `homeassistant` classifies this. Only two facts are used: the node's type
   and whether it has a `packages` key. The node is deleted immediately and
   nothing from it is returned. `YAML_CONFIG_PATHS` in the gateway is exactly
   `recorder`, `homeassistant.packages` and `homeassistant`. The design named
   only the first two.
2. **`recorder/info` runs in its own batch.** `recorder/info` is not
   admin-only; `recorder/entity_options/get` is. Each runs in a separate
   pipelined batch, so losing admin authority on the entity options does not
   discard the runtime status. The design described one batch.
3. **`search/related` is extended to `item_type: entity`.** The batch
   allowlist already admitted `search/related` for automations and scripts
   (HAMCP-209 and HAMCP-212). Slice 1 adds the `entity` item type with the
   strict entity-ID pattern. It is sent only for listed entities that have an
   `entity/source` platform.
4. **Positive-allowlist projection.** The recorder projection copies only
   these recognized keys:
   - the six scalar settings;
   - the two deprecated settings, only when written;
   - `include` and `exclude`.

   `db_url` is reduced to `value_withheld` plus the runtime `default_location`.
   A `!secret` or `!env_var` reference is withheld together with its name
   (`reason: secret_reference`). Unrecognized keys are only counted
   (`unrecognized_key_count`). This is stricter than the design's "withhold
   `db_url` and connection-shaped keys".
5. **Shared `_bounded_route_current`.** The route-currency check in
   `script_dependency_reader` (HAMCP-212) is extracted unchanged into
   `UpstreamReadGateway._bounded_route_current`. The new `yaml_config_reader`
   reuses it. The script reader's behaviour and its tests are unchanged.
6. **YAML 1.2 versus 1.1 (corrected after review of `de1433f`, M1).**
   ha-mcp's reader parses YAML 1.2; Home Assistant loads YAML 1.1. The reader's
   value cannot show how a value was spelled: unquoted `off` and quoted
   `"off"` both reach it as the string `off`. Validating the reader's value
   is therefore kept apart from Core-schema validity.

   `projection.yaml11_readings` ports the PyYAML 6.0 implicit resolver and
   constructors. A test checks it against the installed PyYAML. It lists every
   other value YAML 1.1 could have produced for the same written text:
   - **A string the reader returned** may have been unquoted. Covered forms:
     - the boolean family (`yes`/`no`/`true`/`false`/`on`/`off` in
       their three cases);
     - the spec booleans `y`/`Y`/`n`/`N`;
     - YAML 1.1 integers: underscores, `0b`, base 8 with a leading `0`,
       `0x` and sexagesimal `1:20`;
     - YAML 1.1 floats, including sexagesimal floats, `.inf` and `.nan`.
   - **An integer** may have been written with a leading zero. YAML 1.1
     reads `010` as 8 and `08` as a string.
   - **A finite float** may have been written without a decimal point. YAML
     1.1 reads `1e3` as a string.

   Each reading goes through the ports of Core's validators: `cv.boolean`,
   and `Coerce(int)` with the schema minimum. The outcomes decide the report:
   - **All readings agree:** the value stays definite. `auto_purge: off`
     projects as `false` either way.
   - **Readings differ:** the setting is reported `validity: parser_dependent`
     with `reason: yaml_1_1_reading_differs` and `value: null`. It also
     carries the reader's value as `as_read`, plus each reading's spelling,
     validity and coerced value, and the omission
     `setting_parser_dependent:<key>`. This covers `commit_interval: off` and
     `purge_keep_days: yes` (1.1: 0 and 1, valid), `commit_interval: 1:20`
     (1.1: 80) and `commit_interval: 010` (1.1: 8).
   - **Every reading fails:** only then is the value reported
     `invalid: true`, `reason: fails_core_schema`, with `as_written` for a
     scalar. For example, `commit_interval: never` fails under any parser.
     The value is never replaced by a default.

   Filter lists use the same rule: an item whose readings change Core's
   acceptance makes the filter `origin: unknown`, `computable: false`,
   `reason: parser_dependent`. `fails_core_schema` is used only when an item
   fails under every reading. For entities, Core's own
   `recorder/entity_options/get` answer stays authoritative.

   Conservative choices, for the architect:
   - **Leading zeros.** Any integer of 8 or more whose digits are all 0–7
     could have been written with a leading zero and read in base 8. So
     common values such as `purge_keep_days: 10`, `14`, `30` or `365` become
     `parser_dependent`. Integers containing an 8 or a 9 stay definite,
     because `Coerce(int)` turns the 1.1 string `"09"` back into 9. The review called the leading-zero
     difference already disclosed. The alternative is to keep these definite
     and rely on that disclosure.
   - **Spec booleans `y`/`n`.** These are kept as possible 1.1 booleans because
     the annotatedyaml 1.0.2 loader could not be inspected (PyPI was
     unreachable). PyPI's resolver does not treat them as booleans.
   - **Quoted numeric strings.** A quoted `"010"` is also `parser_dependent`.
     The reader's resolver is not pinned, so a string is never assumed to have
     been quoted.

   One case is still unobservable: a `0o`-prefixed integer is a number under
   YAML 1.2 but a string under YAML 1.1. `coverage.yaml_numeric_spelling`
   discloses it.
7. **No candidate definitions.** The design allowed optional
   `candidate_definition` notes for registry-less entities. Slice 1 offers
   none: `definition_location` is always `not_determinable`, and
   `coverage.candidate_definitions` says so.
8. **Truncated reconciliation is partial.** A `limit` or response-byte
   truncation sets `truncated: true` and adds `entities_not_listed_limit` or
   `entities_not_listed_response_bytes`. The report is then partial, not
   complete.
9. **More than one config entry is `ambiguous`.** If `search/related` returns
   more than one config entry for an entity, `config_entry_id` is `null` with
   `config_entry_status: ambiguous`. One is never chosen.
10. **`entity_reconciliation` imports `rest_path`.** The states read uses the
    REST client's existing `GET /states` route through the shared
    `rest_path` helper. The path is a string literal, as the HAMCP-200
    path-safety scan requires. No `ROUTE_TEMPLATES` entry is added.
11. **Tuple and string argument hardening.** The service entry points
    normalize only a tuple to a list. A string, mapping, set or `None` goes to
    validation unchanged and is refused before any dispatch. A string is never
    split into characters. The first test run found this defect: `"light"`
    became five single-letter domains. A regression test covers it.
12. **One sanitized exit point (correction after review of `de1433f`, H1).**
    Both tools used to append identifier-bearing omissions after the strict
    sanitizer had run. In `get_recorder_settings` these were `filter_drift:`
    entity IDs; in `get_unregistered_entities` they were `config_entry_unknown:`
    entity IDs. A configured secret could therefore reach the response.

    Each service now builds the complete envelope first: report, omissions,
    warnings, limitations and every identifier. Its `_finish` method then runs
    `sanitize_untrusted_data` once, with the known secrets, on that final
    envelope. Nothing is added after that call.

    Two events change the omissions, and each causes the envelope to be
    rebuilt and sanitized again:
    - **Redaction or truncation** adds `redacted_by_policy`. Per HAMCP-191 the
      report is then partial.
    - **The response-byte bound** collapses filter lists in the recorder tool
      and trims entity rows in the reconciliation tool. Either adds its
      omission.

    The returned value is always the output of the last sanitizer call.
    `config_entry_unknown:` omissions are capped at 50
    (`MAX_OMITTED_ENTITY_IDS`), so the omission list stays bounded. The tests
    plant a known secret in every field that carries an identifier: entity
    IDs, platforms, config-entry IDs, filter items, omissions and the scope.
    They assert that the serialized response never contains it. A control
    without the known secret shows that each planted path reaches the
    response.

## Filter port

`recorder_settings/entity_filter.py` ports Core 2026.10.1
`helpers/entityfilter.py` (cases 1, 2, 3, 4a, 4b and 6). It is a cross-check
only: Core's own per-entity answer from `recorder/entity_options/get` is
authoritative, and a disagreement is `filter_drift`, never resolved silently.

`docs/evidence/hamcp132/generate_filter_truth_table.py` runs Core's three
filter functions on 13 configurations × 12 entity IDs:
- the functions are extracted by AST from the pinned source;
- that source is checked by SHA-256 first (`0ce66e76…`, commit `abe1fdd7`).

It writes `filter-truth-table.json`. The port must match every row. When the
pinned source is cached locally, a test regenerates the table and compares it
with the committed file.

## Mutations

`docs/evidence/hamcp132-mutations/run_mutations.py` applies each mutation to
a disposable copy outside the repository and runs both suites. The results
are in `results.json` beside it; every row of design section 7 has at least
one mutation, and the totals are in the acceptance document.

The correction batch for the review of `de1433f` adds 13 mutations. One is the
reviewer's own: the entity-options result association reversed. The other 12
cover items 6 and 12. They include omissions appended after sanitization, and
a parser-dependent value reported as `fails_core_schema`. All must be killed.
Three earlier fragments now target the final-envelope variable. The
response-byte mutation P9 returns the collapsed lists without their omission.
