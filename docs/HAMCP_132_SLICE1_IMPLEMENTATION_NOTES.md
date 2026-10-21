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
6. **YAML 1.2 versus 1.1 (corrected after review of `de1433f`, M1; owner
   decisions of 2026-10-20 applied).**
   ha-mcp's reader parses YAML 1.2; Home Assistant loads YAML 1.1. The reader's
   value cannot show how a value was spelled: unquoted `off` and quoted
   `"off"` both reach it as the string `off`. Validating the reader's value
   is therefore kept apart from Core-schema validity.

   `projection.yaml11_readings` ports the PyYAML 6.0 implicit resolver and
   constructors. A test checks it against the installed PyYAML 6.0.2. It
   lists every other value YAML 1.1 could have produced for the same text:
   - **A string the reader returned** may have been unquoted only if the
     reader's own YAML 1.2 resolver keeps its plain spelling a string.
     `projection._YAML12_NOT_STRING` ports the ruamel.yaml 0.19.1 YAML 1.2
     implicit resolvers (`resolver.py`, the `(1, 2)` bool, float, int and null
     entries). 0.19.1 is the version in the reviewed component dependency
     contract (`tests/fixtures/ha_mcp_860_component_dependencies.json`). The
     component's manifest requires only `ruamel.yaml>=0.18.0`, so the installed
     version is not observed, and `coverage.yaml_parse` says so.
     - **Quoted (owner decision 3).** ruamel reads plain `010`, `10`, `0x1F`,
       `0b101`, `1_000`, `0o10`, `1.5`, `1e3`, `.inf`, `true`, `null` and the
       empty scalar as other types. A returned string with that text was
       quoted, and a quoted scalar is a string under YAML 1.1 too. It is
       validated as a definite string: `commit_interval: "010"` is 10,
       `domains: ["true"]` stays `true`.
     - **Possibly unquoted.** The remaining YAML 1.1 forms are checked: the
       boolean family (`yes`/`no`/`on`/`off` in their three cases) and
       sexagesimal integers and floats (`1:20`, `1:20.5`). `true`/`false`
       are booleans in both versions, so they never reach here as plain text.
     - **`y`/`n` (owner decision 2).** PyYAML 6.0.2's boolean resolver is
       `^(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF)$`
       (`yaml/resolver.py`, registered for the first characters
       `yYnNtTfFoO`). It does not include `y`, `Y`, `n` or `N`. Pinned Core
       2026.10.1 constrains `annotatedyaml==1.0.2` and `PyYAML==6.0.3`
       (`homeassistant/package_constraints.txt`). annotatedyaml 1.0.2's
       `loader.py` subclasses `yaml.CSafeLoader` (or `yaml.SafeLoader`) and
       registers constructors only, never an implicit resolver. Its scalar
       constructor wraps strings that are already resolved. PyYAML 6.0.3's
       `resolver.py` and `constructor.py` are byte-identical to the installed
       6.0.2. The wheels were inspected, not installed: annotatedyaml 1.0.2
       cp313 manylinux sha256
       `75927ec682f188efe25309e259c115e3976b702900ce1be93a971b328c87a10a`, and
       PyYAML 6.0.3 cp313 manylinux sha256
       `0f29edc409a6392443abf94b9cf89ce99889a1dd5376d94316ae5145dfedd5d6`.
       So these are plain strings under both versions and stay definite. A
       test asserts this against the installed resolver.
   - **An integer (owner decision 1)** is reported definite, as parsed. Its
     written spelling is not observed. `coverage.yaml_numeric_spelling`
     carries the standing disclosure: an integer written with a leading zero
     (for example `010`) would be read by Home Assistant's YAML 1.1 loader as
     octal (`08` and `09` as strings). So `purge_keep_days: 10`, `14`, `30`
     and `365` are definite. Integers are never marked `parser_dependent`.
   - **A finite float** may have been written without a decimal point. YAML
     1.1 reads `1e3` as a string. This is unchanged: the owner decisions name
     integers only, so floats whose readings change Core's verdict stay
     `parser_dependent`. They are flagged for the architect below.

   Each reading goes through the ports of Core's validators: `cv.boolean`,
   and `Coerce(int)` with the schema minimum. The outcomes decide the report:
   - **All readings agree:** the value stays definite. `auto_purge: off`
     projects as `false` either way.
   - **Readings differ:** the setting is reported `validity: parser_dependent`
     with `reason: yaml_1_1_reading_differs` and `value: null`. It also
     carries the reader's value as `as_read`, plus each reading's spelling,
     validity and coerced value, and the omission
     `setting_parser_dependent:<key>`. This covers `commit_interval: off` and
     `purge_keep_days: yes` (1.1: 0 and 1, valid) and `commit_interval: 1:20`
     (1.1: 80).
   - **Every reading fails:** only then is the value reported
     `invalid: true`, `reason: fails_core_schema`, with `as_written` for a
     scalar. For example, `commit_interval: never` fails under any parser.
     The value is never replaced by a default.

   Filter lists use the same rule: an item whose readings change Core's
   acceptance makes the filter `origin: unknown`, `computable: false`,
   `reason: parser_dependent`. `fails_core_schema` is used only when an item
   fails under every reading. For entities, Core's own
   `recorder/entity_options/get` answer stays authoritative.

   Two integer spellings are undetectable and disclosed in
   `coverage.yaml_numeric_spelling` (owner decision 4): a leading zero, and a
   `0o`-prefixed integer, which is a number under YAML 1.2 but a string under
   YAML 1.1.

   For the architect:
   - **Floats.** A float is still `parser_dependent` when its exponent
     spelling would change Core's verdict. Decision 1's rationale (the
     spelling is not observed) would make it definite with a disclosure. That
     extension was not made without an owner decision.
   - **`%YAML 1.1` directive.** ruamel honours a `%YAML 1.1` directive. In
     that case the reader already parses YAML 1.1, and a string treated as
     possibly unquoted was in fact quoted. The report is conservative
     (`parser_dependent`), not wrong.
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

The owner decisions of 2026-10-20 replace M6 and add five mutations, all
of which must be killed:
- M6 reports a plain integer as spelling-dependent;
- M9 treats a quoted string as unquoted;
- M10 reads `y`/`n` as YAML 1.1 booleans;
- M11 drops the numeric-spelling disclosure from coverage;
- M12 and M13 lose the YAML 1.2 quoting signal for booleans and floats.
