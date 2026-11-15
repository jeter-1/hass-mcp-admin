# HAMCP-371 S10: closed 9.2.0 key sets, zone and services, the dashboard getter, the D5 platform gate

S10 closes the key sets of the compiled 9.2.0 public reads (21:36 record,
items 7 to 12), adds the zone and services handling (items 13 to 15, research
G 2b and 2e), keeps the 9.2.0-only modes unreachable (2f, item 16) and puts
the dashboard getter's counts and warning rule behind one 9.2.0 gate (N30).

The architect's rulings of 2026-11-15 13:25 CST, relayed by Josh, accepted S9
(`31f35229`) and the first S10 commit (`265168bf`) with evidence conditions
E1 to E3, ruled D5 (question S10-1) and ruled S10-2. The second S10 commit
records E1 to E3 below and adds the D5 platform gate and the S10-2 value
types. **D5 for the blueprint read is not done: see question D5-B.**

This is a local candidate only. Nothing was signed, published, pushed or
deployed, and no Home Assistant instance was accessed.

## Rulings record

- **S10-1** is answered by D5 (Josh's ruling by relaying the 13:25 block).
  Platform reads: done (section D5 below). Blueprint: blocked (question D5-B).
- **S10-2** is ruled: `integration` (in `target.entity[]` filters) is admitted
  as a string and `collapsed` (on field sections) as a boolean, as Core keys
  copied unchanged. Any other value type is withheld and counted. Done
  (section S10-2 below).
- **New register row**: HAMCP-402 (on `main`, `upstream_blueprint.validate_result`
  validates but does not project, so unknown top-level keys under `data` pass
  through on every admitted version, including live 8.5.0). It was filed with
  Status Proposed; Josh sets the priority (the architect proposes Medium).

## Rulings questions for Josh (S10 stops here; S11 waits on D5-B)

**Question D5-B (blueprint gate cannot learn the version).** D5 grants "one
exact-9.2.0 gate and one call in each of `upstream_platform_reads.project_result`
and `upstream_blueprint.validate_result`. No other change in either file." The
platform gate is possible because `project_result(entry, payload, arguments)`
receives the policy entry, whose binding names the version. The blueprint
function does not:

- `upstream_blueprint.validate_result(payload, arguments)`
  (`upstream_blueprint.py:81-100`) takes only the upstream payload and the
  public arguments. Neither carries the ha-mcp version.
- The gateway calls it as `validate_blueprint_result(payload, arguments)` at
  `upstream_read_gateway.py:4253-4257`, after `is_blueprint_adapter(policy_entry)`,
  and does not pass `policy_entry`.
- No request-scoped value carries the version: the only context variables in
  the Engineering package are request telemetry, provider-send observers,
  dependency, F3, helper-value and device-configuration state (searched with
  `grep -rn ContextVar`).

So a 9.2.0 gate inside `validate_result` cannot be written within the grant.
The options, none of which is authorized now:

- (a) One gateway edit at `:4255` that passes `policy_entry` (or its version)
  to the blueprint validator, plus the matching signature in
  `upstream_blueprint.py`. This is a fifth gateway change, which the R1 rule
  makes a stop and report, and a second change in `upstream_blueprint.py`
  beyond the one gate and call.
- (b) A version-selecting change in `upstream_blueprint.py` only, for example
  a new function that `is_blueprint_adapter` or `validate_result` reaches
  through the entry. The gateway passes no entry to `validate_result`, so this
  also needs the gateway, and "No other change" forbids it.
- (c) Leave 9.2.0 blueprint on its shipped behaviour in HAMCP-371 (unknown
  top-level keys under `data` pass through, as on every admitted version
  today) and fix all versions together in HAMCP-402, which has the same
  version-input problem.

Recommendation (inference): (a), ruled as one bounded gateway exception
naming the line, because (c) admits 9.2.0 with the pass-through D5 meant to
close, and HAMCP-402 will need the same version input for 8.x and 9.1.x.

Facts for the ruling:

- Only list mode is captured on 9.2.0 (`r12-blueprints`, `{"domain": ...}`).
  There is no 9.2.0 get-mode receipt, so the "reviewed 9.2.0 set ... captured
  with and without the component" exists for list mode only. Get mode would
  rest on the source review of `tools_blueprints.py` at `51e3aac7` (the file
  is unchanged from 9.1.2), not on a capture.
- D5's refusal (`ValueError`, then `invalid_response`) produces no count
  fields. HAMCP-371's replay test still expects `fields_withheld` on r12
  (2 errors in `test_admitted_keys_and_withheld_counts`) and injects its key
  at the top level of the payload, outside `data` (1 failure in
  `test_an_injected_key_is_withheld_and_counted_once_per_name`). Under (a)
  those expectations change to a refusal test for a key under `data` and an
  unchanged top-level case; the change is to HAMCP-371's own test.

Until D5-B is ruled the 3 r12 subtests stay failing, not marked expected.

**Question T129 (six shipped tests have failed since S8).** Six unmodified
shipped test modules fail on this branch. Each passes at `origin/main`
`e741f2ed`, and fails at S8 `eb6c6b32` and on the current tree. Five were
also run at `9e91000a`, the parent of S8, and pass there
(`test_upstream_release_registry` was not run there). In every case the cause is the S8 9.2.0 registry row
(list A row 8): each test pins the reviewed version set, a per-version table,
or the list of policy files it copies into a temporary tree. HAMCP-351 edited
the same files for 9.1.2 (`bcdff105`). The six were outside the S9 regression
set of 28 modules, so S8 and S9 did not report them; the S10 sweep found them.

| Module | Failing tests | Cause (file:line) |
| --- | --- | --- |
| `test_hamcp129_platform_reads` | `test_bindings_cover_exactly_three_tools_at_each_version` (`18 != 15`) | `BINDING_VERSIONS` `:52` and the local `SOURCES` table have no 9.2.0 |
| `test_beta11_upstream_8_compatibility` | `test_exact_release_evidence_and_policy_accounting` | supported-version tuple `:63` ends at 9.1.2 |
| `test_ha_core_2026_9_integration` | `test_repository_context_reports_current_exact_catalog` | `:3117` expects `reviewed_upstream_version` 9.1.2; the context now reports 9.2.0. The other counts are unchanged (77 stock, 27 delegated reads, 113 connector total) |
| `test_hamcp184_provider_dispositions_fail_closed` | 1 failure, 2 errors | version list `:102`; `EFFECTIVE_AFTER` (`:88`) has no 9.2.0 row (`:115`, `KeyError`); the fixture copy lacks `upstream_tool_policy_9_2_0.json` (`test_fixture_baseline_loads`) |
| `test_ha_mcp_850_candidate_lane` | `test_matrix_installs_locked_contract_dependencies_before_loading_registry` | version set `:460` |
| `test_upstream_release_registry` | 3 failures, 1 error | version tuple `:264`; `expected_automatic_reads` has no 9.2.0 (`:958`, `KeyError`); two temporary-tree tests do not copy `upstream_tool_policy_9_2_0.json` |

Two kinds of edit would fix them:

- version lists and copied-file lists: add `"9.2.0"` or the 9.2.0 policy
  file, as HAMCP-351 did; and
- per-version expectations about 9.2.0 (`SOURCES`, `EFFECTIVE_AFTER`,
  `expected_automatic_reads`, `reviewed_upstream_version`). Under the owner
  condition, each would be written from the S8 capture or a recorded review,
  not copied from 9.1.2. Each row would cite its source.

D5 says "the shipped tests for both files pass unmodified", and the owner
conditions say the 9.1 tests pass unmodified. Choose one:

- (a) Authorize these edits in the six named modules: list additions plus
  9.2.0 rows written from the S8 capture. No 8.x or 9.1.x expectation would
  change. Each edit would be a named E2-style row in the S15 Cursor request.
- (b) Leave them failing until SB or S16, disclosed.

Recommendation (inference): (a). Every other test in the six modules passes,
and no 8.x or 9.1.x expectation would change. The full sweep is in the
Validation section.

**Question E1-R (re-admission and the S7 keys).** E1 withdrew the S10
additions with no receipt (section E1). Two follow-ups:

- May a withdrawn key be re-admitted on a recorded 9.2.0 source review (as
  `response` was), or does it need a receipt?
- The S7 contract still admits 28 keys whose only source is the shipped 9.1
  code ("interpretation, pending ruling" in the row; listed in section E1).
  E1 covers the S10 edits only, so these are reported and kept unchanged. Are
  they to be withdrawn as well?

## E1: the S10 contract edits, key by key

The contract diffs are committed:

- `contract-31f35229-to-265168bf.diff`: S9 to the first S10 commit;
- `contract-265168bf-to-s10c.diff`: the first S10 commit to this one (E1
  withdrawals, S10-2 types, D5 platform rows).

`e1_receipts.py` walks all 168 S6 receipts (every set, both versions, both
call directories) and prints up to three JSON paths per key, keyed by the
receipt's `upstream_tool`. Its output is `e1-receipts.txt`.

| Read | Key | Change at `265168bf` | Receipt (file : JSON path) | Follows | Now |
| --- | --- | --- | --- | --- | --- |
| `ha_config_list_groups` | `all` | admitted, cited to `upstream_reads_9_1_config.py:29,34,285-301` | none | shipped 9.1 code only | withdrawn: withheld and counted |
| `ha_config_list_groups` | `unavailable_fields` | admitted, same citation | none | shipped 9.1 code only | withdrawn from `admitted`; `recomputed_by_engineering` (the 9.1 projection writes it) |
| `ha_eval_template` | `unavailable_fields` | admitted, `upstream_reads_9_1_config.py:333-345` | none | shipped 9.1 code only | withdrawn; `recomputed_by_engineering` |
| `ha_get_entity`, `ha_get_entity_exposure`, `ha_get_todo`, `ha_get_zone`, `ha_list_floors_areas` | `warnings`, `partial` | admitted, cited to `upstream_reads_9_1_entities.py` | none | shipped 9.1 code only | withdrawn: withheld and counted |
| `ha_get_overview`, `ha_get_state` | `warnings` | admitted (state also had the visibility prefix as a reviewed warning) | none | shipped 9.1 code only | withdrawn: withheld and counted; prefix removed |
| `ha_list_services` | `partial` | admitted | none | shipped 9.1 code only | withdrawn |
| `ha_list_services` | `warnings` | admitted | none | research G ruling 2b | kept (a ruling, not the code) |
| `ha_list_services` | `response` | admitted on a recorded review | `run-a-1/out/9.2.0/calls/v24-services-response.json` : `/exchange/response/result/structuredContent/services/calendar.get_events/response`; also `t-large-services.json` : `.../services/recorder.get_statistics/response` (sets A and B) | the receipts and the source (`tools_services.py:581-585` at `51e3aac7`) | kept |
| `ha_list_services` | `optional` | admitted on the same review | the same files : `.../response/optional` | the receipts | kept |
| (all) | `engineering_notes` | added to `engineering_added` | not an upstream key | Engineering output (items 14, 15) | kept |

Consequences of the withdrawals (behaviour on 9.2.0 only):

- An upstream `warnings` or `partial` on the eight entity-envelope rows (services
  keeps `warnings`) is withheld and counted in `fields_withheld`. It no longer
  passes, and no longer marks the read partial.
- The `ha_get_state` visibility completeness rule of the 9.1 projection cannot
  fire on 9.2.0, because `warnings` is removed before it runs.
- Groups always reports `all` in Engineering's recomputed `unavailable_fields`,
  because an upstream `all` is withheld. An upstream `unavailable_fields` on
  groups or template is withheld and counted rather than refused.
- `partial` on overview and state remains admitted from S7 (two of the 28
  keys of question E1-R).

The 28 S7 keys whose only source is the shipped 9.1 code (unchanged, reported):
`ha_config_get_category.category`, `ha_config_get_label.label`,
`ha_config_get_scene.{category, warnings}`, `ha_config_get_script.warnings`,
`ha_config_list_dashboard_resources.{content_truncated, content_truncation_note, migration_hint, note}`,
`ha_config_list_helpers.warnings`, `ha_eval_template.warnings`,
`ha_get_automation_traces.diagnostics`, `ha_get_entity.{entity_entries, errors}`,
`ha_get_entity_exposure.exposed_entities`,
`ha_get_history.{attributes, max, mean, min, warnings}`, `ha_get_overview.partial`,
`ha_get_state.{count, errors, partial, states, success}`,
`ha_search.{dashboards, warnings}`.

## E2: edits to HAMCP-371's own test expectations

No shipped (9.1 or earlier) test was edited.

**Device, N26** (`tests/test_hamcp371_real_core_replay.py`). The ruling,
verbatim: "N26: keep the shipped per-occurrence withheld-key count on the
device read for 9.2.0. Record the difference from ruling 10 on the row; do not
gate the shared file for this." Its text is held in
`~/handoffs/hass-mcp-admin-plan-hamcp-371-lane.md` (line 430, and the rulings
table at line 2737) and in `~/handoffs/hass-mcp-admin-agent-release-2.6.0-build.md`
(line 3711). Both are local handoff files, not repository files.

| Test | Old expectation (`31f35229`) | New expectation |
| --- | --- | --- |
| `test_admitted_keys_and_withheld_counts` | `data['fields_withheld'] >= len(withheld)`, and `fields_dropped_by_policy` equal to the policy-drop names, for device too | `data.get('fields_withheld', 0) >= len(withheld)`; no `fields_dropped_by_policy` or `upstream_warnings_replaced` key on device |
| `test_an_injected_key_is_withheld_and_counted_once_per_name` | base count read as `data['fields_withheld']` | base count read with `.get(..., 0)` (absent when zero) |
| `test_an_unlisted_upstream_warning_is_replaced_and_counted` | `upstream_warning_replaced` in the warnings | device: the shipped closed code (`enrichment_warning_unrecognized` on the single read, `unknown_fields_withheld` on the list read), and no replaced count |

Evidence: the shipped device projection (`upstream_device_projection.py`,
unedited by HAMCP-371 except its version row) and contract row 13 `notes`,
which records the difference from ruling 10 as N26 requires.

**History offsets** (`tests/test_hamcp371_history_offsets.py:233`). Old:
`set(ADDED) == set(real.ADDED)`. New: `set(ADDED) == set(real.ADDED) - {"engineering_notes"}`.
Evidence: S10 adds `engineering_notes` to the contract's `engineering_added`
for the zone and services notes (items 14 and 15); the history module does not
emit it, so the S9 equality narrowed by that one name.

**Unreachable modes** (`tests/test_hamcp371_unreachable_modes.py:121,135`).
The module is new in S10. Its uncommitted draft expected `fields_withheld == 3`
(the three describe keys); the committed test expects 4. Evidence: the
contract walk counts names nested inside a withheld value (disclosure 3), and
the describe-shaped row carries `later_forms: [{"step": ...}]`, so `step` is
the fourth name. The value 3 was never committed.

**This commit** (E1 and S10-2 consequences, `tests/test_hamcp371_key_sets.py`
and `tests/test_hamcp371_real_core_replay.py`):

| Test | Old | New | Reason |
| --- | --- | --- | --- |
| `test_prefix_rules_are_the_three_reviewed_families` | renamed `..._two_...`; `{automation, state, services}` | `{automation, services}` | E1 withdrew the state prefix |
| `test_reviewed_prefixes` | the state visibility prefix passes; a near miss is replaced | the services language prefix's near miss is replaced | E1 |
| the replaced-warning test | on `ha_get_overview` | on `ha_list_services` | E1 withdrew overview `warnings` |
| `test_state_warnings_are_withheld_not_reviewed` | (new) | state `warnings` withheld, counted 1, not replaced | E1 |
| `test_typed_keys_are_the_s10_2_ruling`, `test_typed_service_keys`, `test_services_core_keys_are_admitted_on_the_s10_2_ruling` | (new) | the S10-2 types, mismatches withheld and counted, contract citations | S10-2 |
| `test_unreviewed_9_2_0_keys_stay_withheld` | services pending `collapsed, filter, integration` | `filter` | S10-2 |
| closed client check | `client <= admitted` | `client <= admitted | recomputed_by_engineering` | E1 moved `unavailable_fields` to recomputed |
| services replay | `collapsed`, `filter`, `integration` never in the client | `collapsed` and `integration` in the client exactly when upstream has them; `filter` never | S10-2 |

## E3: named review items

E1 and E2 are named review items in the S15 Cursor request. The plan's S15
row (`~/handoffs/hass-mcp-admin-plan-hamcp-371-lane.md`, line 2267) now reads,
in part: "E3 (13:25 ruling): the Cursor request names two review items: E1,
the S10 contract edits, key by key (the E1 table, the two contract diffs and
e1_receipts.py in docs/evidence/hamcp371/s10/); E2, the HAMCP-371
test-expectation edits (the E2 tables, including N26)". This work launches no
review.

## D5: the platform reads on 9.2.0

`P/providers/upstream_platform_reads.py` gets one import line and one gate
with one call in `project_result`, before the per-tool dispatch (6 added
lines: the import, a blank line, a two-line comment, the gate and the call;
no other change in the file):

```python
from . import upstream_reads_9_2_platform as _platform_9_2
...
    # HAMCP-371 D5: on ha-mcp 9.2.0 only, the default-withhold counts and the
    # replaced-warning rule (upstream_reads_9_2_platform).
    if binding(entry)[1] in _platform_9_2.RELEASES:
        return _platform_9_2.project(tool, payload, values)
```

New `P/providers/upstream_reads_9_2_platform.py`:

- It calls the shipped rebuild (`_project_health`, `_project_apps`,
  `_project_integrations`) unchanged, then adds the S10 engine's counts.
- `fields_withheld` counts the distinct upstream names outside `KNOWN`, which
  is the contract's `admitted | consumed_structure | recomputed_by_engineering`
  (a test asserts equality). `fields_dropped_by_policy` is 0. Both are capped
  at 999.
- Health `info`: no silent drop. `info` is not open in the contract or the
  code. Each key of `health_info.data.<domain>.info` is named `<domain>.<key>`;
  one the rebuild does not return is withheld and counted. On r20 that is 18
  names on both sets (the contract's `withheld_by_rebuild`), tested.
- Every upstream warning is replaced: the shipped code
  `upstream_warnings_withheld` becomes `upstream_warning_replaced`,
  `upstream_warnings_replaced` counts per occurrence, and the read is partial
  with reason `upstream_warnings_replaced`. Engineering's own codes are kept.
- r27 (`ha_get_app`) has no successful receipt; it stays "not replayable in
  the local lane (no Supervisor)" per the 05:35 ruling. Its 9.2.0 projection is
  tested with a synthetic payload only.

Contract: the three platform rows are `closed` rebuilds with no open
containers and no policy drops. Their admitted keys are cited to the shipped
rebuild (`upstream_platform_reads.py:422-610`, `:611-673`, `:674-744`), which
constructs them; they are the rebuild's output keys, not receipt keys. The
health row has `qualified_containers` for the `info` rule.

Disclosures:

- **Flat names.** A `KNOWN` name is uncounted wherever it appears in the
  upstream payload, as in the S10 engine (disclosure 1).
- **Integration `state_summary`.** The rebuild recomputes `state_summary`; the
  upstream inner keys `loaded` and `setup_error` are walked and counted as
  withheld, which matches the contract's `withheld_by_rebuild`.
- **Health domains.** A domain outside `homeassistant`, `hassio` and `cloud` is
  listed by name in `platform.other_domains` (shipped behaviour); its `info`
  keys are withheld and counted.
- **Count unit for `info`** (`<domain>.<key>`, distinct) is an interpretation
  recorded on the row's `notes`, pending ruling.

Tests (`tests/test_hamcp371_platform_9_2.py`, 13 tests): the contract binding
and the walk against the generator on r20 and r26; r20 counts and equality
with the shipped output apart from the counts; injected `info` keys; a new
health domain; r26 counts; synthetic integration and app payloads; warnings
replaced on all three; caps; the shipped refusals; and on 8.5.0 and 9.1.2 the
module is never called (a spy) and the output equals the shipped output.

## S10-2: service value types

`ha_list_services` admits `integration` (Core 2026.11.0 `74cf5a49`
`helpers/selector.py:178`, also `:212`: entity filter `integration: str`) and
`collapsed` (`helpers/service.py:996` and `:1004`: section `collapsed: bool`,
default `False`). The citations are on the contract row's `new_in_9_2_0`
sources and its new `value_types`. The engine's `KeySet.types` withholds and
counts a value of any other type (an exact type check, `type(value) is bool` or `str`). On the
receipts, `integration` appears at `t-large-services.json`
`.../services/scene.delete/target/entity/0/integration` and `collapsed` at
`.../services/light.turn_on/fields/additional_fields/collapsed`
(`e1-receipts.txt`).

## What S10 adds

### Closed key sets (`P/providers/upstream_reads_9_2_keys.py`)

- `ROWS` holds 21 reads. A test asserts that it equals the S7 output-key
  contract (`tests/fixtures/ha_mcp_9_2_0/output-key-contract.json`).
- On 9.2.0, `upstream_reads_9_2._projection` runs the key set first, then the
  unedited compiled 9.1 projection, or the 9.2.0 zone or services override.
- A key outside the set is withheld and counted, not refused (item 7).
  `fields_withheld` counts distinct withheld names and
  `fields_dropped_by_policy` counts distinct policy-drop names (item 10).
  Both are capped at 999 and sit at the top level of `data` (D1). No names
  are disclosed.
- `attributes`, `config`, `filter` and `selector`, plus each row's own open
  containers, pass whole and are not counted (item 11). Map containers
  (service names, domains) treat their dict keys as data.
- An upstream warning passes only when it equals a reviewed string or starts
  with a reviewed prefix. Any other warning is removed and counted per
  occurrence in `upstream_warnings_replaced`. The fixed code
  `upstream_warning_replaced` is appended once, and the read becomes partial
  with a fixed sentence (item 12).
- `KeySet.types` (S10-2) admits a key with one value type only; any other
  type is withheld and counted.
- The 9.1 groups and template completeness texts are re-worded to name 9.2.0
  (`TEXTS`).

### Zone and services (`P/providers/upstream_reads_9_2_entities.py`)

- `ha_get_zone`: 9.2.0 moved `friendly_name` to `name` on home and YAML zone
  rows (`tools_zones.py:380-392,436` at `51e3aac7`), so Engineering restores
  `friendly_name` from `name` on a row that lacks it (item 15). The location
  note and the icon meaning go in the payload as `engineering_notes`
  (item 14).
- `ha_list_services`: 9.2.0 no longer reports a field `type`. Engineering does
  not synthesize one, and a note says so (item 15). `response` is admitted on
  a recorded review (item 13).
- `engineering_notes` is added to the contract's `engineering_added`.

### Unreachable modes (`tests/test_hamcp371_unreachable_modes.py`, item 16 F1)

- The helpers `describe` mode is pinned off. A public call that names
  `describe` is refused before dispatch.
- Integration `include_schema` and `include_subentry_schema` are never sent,
  and naming either is refused before dispatch.
- A describe-shaped result has `read_only`, `secret` and `later_forms`
  withheld and counted. The integration rebuild never returns them.
- An oversized helpers result fails with `response_too_large`.

### Dashboard getter (N30, list B row 8)

- New `P/providers/upstream_reads_9_2_dashboard.py`. Its get-mode key set and
  the four reviewed warning templates are cited to `_get_dashboard_get_mode`,
  `_finalize_get_result` (`tools_config_dashboards.py`) and
  `dashboard_render_paths` (`dashboard_screenshot/paths.py:175-233`) at
  `51e3aac7`. Both blobs are the same at 9.1.2 (`bab615df`), so the rows
  equal 9.1.2's because the source is the same, not by analogy.
- `P/providers/upstream_dashboard.py` gets exactly one import line and one
  gate with one call (7 added lines), placed after the redaction summary and
  before the 20-entry warning bound:

  ```python
  if exchange.handshake.server_version in _dashboard_9_2.RELEASES:
      warnings, completeness = _dashboard_9_2.apply(
          payload, data, metadata, warnings, completeness
      )
  ```

- 9.1 behaviour unchanged: on 8.5.0, 9.1.0 and 9.1.2 the module is never
  called (a spy that raises proves it), and the injected payload returns no
  count keys and the shipped warnings. `test_hamcp319_dashboard` passes
  unmodified (33 tests).

### Device (N26)

The device read is not gated. Its shipped behaviour is kept on 9.2.0 and the
difference from ruling 10 is recorded on contract row 13 (`notes`):
`fields_withheld` counts per occurrence and is absent when zero; there is no
`fields_dropped_by_policy` or `upstream_warnings_replaced`; an unlisted
warning becomes a shipped closed code (`enrichment_warning_unrecognized` on
the single read; `unknown_fields_withheld` on the list read, which has no
`warnings` key and withholds an upstream `warnings` value as an unknown
key), not `upstream_warning_replaced`.

## Disclosures

1. **Flat-name model.** Counts and admission use names over the whole
   response, not paths. For example `optional` is admitted wherever it
   appears in a services row.
2. **Policy drops are removed before the inner 9.1 projection**, so that
   projection never sees them.
3. **Names nested inside a withheld value are counted.** A withheld
   `later_forms: [{"step": ...}]` counts 4 names in the unreachable-modes
   test, not 3.
4. **9.2.0 zone warnings are unreviewed.** The texts at `tools_zones.py:322-331`
   and `:425` are not on the reviewed list, so a zone read that carries them
   has them replaced and becomes partial.
5. **Dashboard, tested end-to-end only with `RELEASES` patched to 9.1.2.** The
   real 9.2.0 getter is not reachable before SB: the dashboard attestation is
   quarantined (S8) and `EXACT_GETTER_RELEASES` has no 9.2.0. The wiring test
   proves the call site, not 9.2.0 admission.
6. **Dashboard counting details.**
   - `fields_dropped_by_policy` is always 0: the getter has no policy drops.
   - Reviewed keys that the provider's rebuild discards (for example
     `render_paths`) are admitted, not counted.
   - Values beyond the provider's 20-entry bound, and non-string values, are
     counted as replaced.
   - A reviewed warning that the sanitizer altered no longer matches and is
     replaced (conservative).
   - An upstream value equal to Engineering's own partial sentence is
     replaced, not trusted.
   - The reviewed large-config hint names upstream write tools
     (`ha_config_set_dashboard(python_transform=...)`). It is passed through
     as on 9.1; Engineering does not expose those tools.
7. **Edits to HAMCP-371's own tests**: see E2. No shipped test was edited.
8. **Authorizations.** `_platform.py` was created under D5. `_blueprint.py`
   was not created (question D5-B). `_config.py` was not needed (the config
   reads run through `ROWS`).
9. **Scratch.** An early scratch copy of the upstream dashboard file was
   written to `/tmp`; it was moved to the research area outside the worktree.
10. **Six shipped test modules have failed since S8** (question T129): see
    the T129 table. They are not edited and not marked expected.

## Validation (2026-11-15, local, uncommitted tree on `265168bf`)

| Check | Result |
| --- | --- |
| `tests.test_hamcp371_key_sets` | 16 tests OK |
| `tests.test_hamcp371_platform_9_2` (new, D5 platform reads; r27 synthetic) | 13 tests OK |
| `tests.test_hamcp371_unreachable_modes` | 9 tests OK |
| `tests.test_hamcp371_dashboard_getter` | 9 tests OK |
| `tests.test_hamcp371_history_offsets` | 28 tests OK |
| `tests.test_hamcp371_admission_core` | 30 tests OK |
| `tests.test_hamcp371_real_core_replay` | 27 tests; 1 failure and 2 errors, all r12 in `RealCoreReplay920` (question D5-B): `test_admitted_keys_and_withheld_counts` sets A and B (`KeyError: 'fields_withheld'`), and `test_an_injected_key_is_withheld_and_counted_once_per_name` for `ha_get_blueprint` (the key passes through). The r20 and r26 subtests pass after D5. |
| Unmodified S9 regression set, one module per run (every `test_hamcp319_*` module, `test_hamcp131_history_*`, `test_hamcp351_ha_mcp_912_admission`, `test_hamcp162_yaml_admission`, `test_hamcp371_error_classification`, `test_ha_mcp_production_readmission`, `test_upstream_8_5_compatibility`) | 28 modules, 2,257 tests, all OK. This includes `test_hamcp319_dashboard` (33) unmodified. |
| Further unmodified modules: `test_hamcp231_thread_matter_topology`, `test_upstream_8_6_compatibility`, `test_beta_v2` | 3 modules, 252 tests, all OK |
| `tests.test_hamcp129_platform_reads` (unmodified) | 39 tests, 1 failure (question T129) |
| Sweep of the other shipped tests that pin version sets (`.artifacts`-local list of 53 files; modules already above skipped) | 33 modules, 915 tests: 28 OK, 5 FAILED (`test_beta11_upstream_8_compatibility`, `test_ha_core_2026_9_integration`, `test_hamcp184_provider_dispositions_fail_closed`, `test_ha_mcp_850_candidate_lane`, `test_upstream_release_registry`; question T129) |
| The 6 failing shipped modules on `git clone --shared` copies | all 6 pass at `origin/main` `e741f2ed`; 5 pass at `9e91000a` (`test_upstream_release_registry` was not run there); all 6 fail at S8 `eb6c6b32` with the same failures as the current tree |
| `generate_output_key_contract.py --check` | up to date |
| `scripts/review_upstream_read_release.py validate` | passes, 13 releases including 9.2.0 |
| Fast tier (`-BaseRef origin/main`, 23 declared protected paths: the 22 of S10 plus `providers/upstream_reads_9_2_platform.py`; test targets `test_hamcp371_platform_9_2`, `test_hamcp371_key_sets`, `test_hamcp371_dashboard_getter`, `test_hamcp371_unreachable_modes`, `test_hamcp319_dashboard`) | passed, 80 tests |
| Repository scan (`docs/evidence/hamcp371/s6/comparison/scan.py`) and leak check, run on each changed file including this README | 14 files, 0 hits; each file PASS |

The Full and Evidence tiers were not run: they are not part of S10.
