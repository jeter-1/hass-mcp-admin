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
6. **YAML 1.2 versus 1.1.** ha-mcp's reader parses YAML 1.2; Home Assistant
   loads YAML 1.1. Written values go through ports of Core's validators
   (`cv.boolean`, `Coerce(int)` with the schema minimum). So `auto_purge: off`,
   a string under 1.2, still projects as `false`. A value that fails them is
   reported as `configured` with `invalid: true`, `reason: fails_core_schema`
   and, for a scalar, `as_written`; it is never replaced by a default. A
   value whose 1.1 and 1.2 readings differ can still project differently from
   what Home Assistant loaded, for example a leading-zero or sexagesimal
   number. `coverage.yaml_parse` states this, and for entities Core's own
   answer stays authoritative.
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
