# HAMCP-371 S10: closed 9.2.0 key sets, zone and services, the dashboard getter, the D5 platform gate

S10 closes the key sets of the compiled 9.2.0 public reads (21:36 record,
items 7 to 12), adds the zone and services handling (items 13 to 15, research
G 2b and 2e), keeps the 9.2.0-only modes unreachable (2f, item 16) and puts
the dashboard getter's counts and warning rule behind one 9.2.0 gate (N30).

The architect's rulings of 2026-11-15 13:25 CST, relayed by Josh, accepted S9
(`31f35229`) and the first S10 commit (`265168bf`) with evidence conditions
E1 to E3, ruled D5 (question S10-1) and ruled S10-2. The second S10 commit
(`34abf281`) recorded E1 to E3 and added the D5 platform gate and the S10-2
value types. The architect's rulings of 2026-11-15 14:45 CST, relayed by Josh,
accepted `34abf281` and ruled the three questions it raised: D5-B, T129 and
E1-R. This third S10 commit carries out those rulings.

This is a local candidate only. Nothing was signed, published, pushed or
deployed, and no Home Assistant instance was accessed.

## Rulings record

- **S10-1** is answered by D5 (Josh's ruling by relaying the 13:25 block).
  Platform reads: done (section D5). Blueprint: done under D5-B (section
  D5-B).
- **S10-2** is ruled: `integration` (in `target.entity[]` filters) is admitted
  as a string and `collapsed` (on field sections) as a boolean, as Core keys
  copied unchanged. Any other value type is withheld and counted. Done
  (section S10-2).
- **New register row**: HAMCP-402 (on `main`, `upstream_blueprint.validate_result`
  validates but does not project, so unknown top-level keys under `data` pass
  through on every admitted version, including live 8.5.0). It was filed with
  Status Proposed; Josh sets the priority (the architect proposes Medium).
  D5-B closes the keys under `data` on 9.2.0 only; HAMCP-402 still owns 8.x,
  9.1.x and the payload top level.
- **14:45 correction** (architect, verbatim): "D5's blueprint part granted a
  version gate inside validate_result without checking that its signature
  carries no version. As ruled, it could not be built. The stop was correct."
- **D5-B** is ruled (a), as one bounded exception: done (section D5-B).
- **T129** is ruled (a), with the list made complete first: done (section
  T129).
- **E1-R** is ruled: done (section E1-R).

The 14:45 rulings are held verbatim in
`~/handoffs/hass-mcp-admin-agent-release-2.7.0.md` (section "Architect
rulings 2026-11-15 14:45 CST on 34abf281"), a local handoff file.

## D5-B: the blueprint read on 9.2.0

The ruling, in part (verbatim): "Gateway: change only the call
validate_blueprint_result(payload, arguments) (main :4225; cite the branch
line) to also pass policy_entry. No other gateway change." and
"upstream_blueprint.py: validate_result takes policy_entry, and its body adds
one exact-9.2.0 gate and one call to a 9.2 validator in a new module."

Runtime changes (all under `P` = `hass_mcp_engineering_beta/ha_mcp_engineering/`):

| File | Change |
| --- | --- |
| `P/providers/upstream_read_gateway.py:4255` (main `:4225`) | `validate_blueprint_result(payload, arguments)` becomes `validate_blueprint_result(payload, arguments, policy_entry)`. The only gateway change; the fifth and last authorized gateway edit. |
| `P/providers/upstream_blueprint.py:9` | one import: `from . import upstream_reads_9_2_blueprint as _blueprint_9_2` |
| `P/providers/upstream_blueprint.py:89` | signature: `def validate_result(payload, arguments: dict, policy_entry) -> None:` |
| `P/providers/upstream_blueprint.py:112-114` | at the end of the body, after every shipped check: a one-line comment, the gate `if ADAPTER_BINDINGS[policy_entry.argument_restrictions[0]][0] in _blueprint_9_2.RELEASES:` and the call `_blueprint_9_2.validate(payload, expected["action"])` |
| `P/providers/upstream_reads_9_2_blueprint.py` (new) | `RELEASES = {"9.2.0"}`, `LIST_KEYS`, `GET_KEYS` and `validate`, which raises `ValueError("blueprint_read_response_key_unreviewed")` for any key directly under `data` outside the set for the mode. It never changes the payload. |

Behaviour:

- On 9.2.0, list and get mode, with and without the component: a key directly
  under `data` outside the reviewed set is refused through the existing path
  (`ValueError`, which the gateway reports as `invalid_response`, never
  counted). `config` and `yaml` stay open as user content; nested keys are not
  closed.
- 8.x and 9.1.x: unchanged. The gate reads the version from the entry's
  adapter binding; no other binding reaches the new module (test
  `SharedGate.test_every_other_binding_never_calls_the_module`).
- A key at the payload top level, outside `data`, is not covered by the
  ruling and passes as shipped on every version, 9.2.0 included. HAMCP-402
  owns it.

Key sets:

- `LIST_KEYS = {domain, count, blueprints}` is the 9.2.0 capture: every
  `r12-blueprints` receipt (profile A without the component, profile B with
  component 9.2.0) has exactly these keys under `data`. It matches
  `_format_blueprint_list` (`src/ha_mcp/tools/tools_blueprints.py:123-127`
  at `51e3aac7`).
- `GET_KEYS = {path, domain, name, metadata, inputs, used_by, config, yaml,
  yaml_source}` is a **source-review key set**, not a capture: the lane has no
  9.2.0 get-mode receipt. Source: `_get_blueprint` and
  `_merge_blueprint_body` in `src/ha_mcp/tools/tools_blueprints.py` at
  `51e3aac7` (byte-identical to 9.1.2 `bab615df`): `path`, `domain`, `name`
  (:449-455), `metadata` (:461), `inputs` (:470), `used_by` (:482), `config`
  (:696), `yaml` and `yaml_source` (:698-699).
- **SR requirement (ruled):** SR includes `ha_get_blueprint` get mode on a
  default Core blueprint, with the component. If that read is refused, SR
  stops and reports.

Tests:

- The shipped blueprint and gateway tests pass unmodified (Validation).
- `tests/test_hamcp371_blueprint_9_2.py` (new, 11 tests): the module's sets,
  refusal and non-mutation; the shared gate on every binding; and the real
  gateway replayed on the stock registry capture of 9.2.0, 9.1.2 and 8.5.0
  with the committed `r12-blueprints` receipts and a synthetic get answer per
  profile. A key injected under `data` is refused on 9.2.0 in list and get
  mode and passes as before on 9.1.2 and 8.5.0. The top-level case gives the
  same result on all three. The get answers are synthetic, shaped from the
  source review.
- Mutation check (not committed): with `RELEASES` emptied, 7 tests fail across
  the two modules (the four 9.2.0 refusal subtests, the set and gate tests,
  and the r12 replay subtest), so the refusal tests detect a disabled gate.
- The 3 r12 subtests of `tests/test_hamcp371_real_core_replay.py` became the
  refusal tests, as proposed: the key under `data` is refused on 9.2.0 and the
  top-level key passes.

Disclosures:

- The new import makes `upstream_blueprint.py` import a 9.2 module. The
  ruling names the gate and the call; the import is the line the call needs.
- The contract row 12 `named` citation `upstream_blueprint.py:81-105` is
  relative to `main`; on this branch the function is at `:89-114`.
- **S15 Cursor review item (ruled):** the gateway line `:4255`, the
  `upstream_blueprint.py` gate and call (`:113-114`) and the signature change
  (`:89`).

## T129: shipped tests that pin the reviewed versions

Ruling T129 (2026-11-15 14:45): (a), with the list made complete first.

**The complete list.** Every `tests/test_*.py` module (499 on the branch, 486 on `main`) runs as its own
`unittest` process, two at a time, on a `git clone --shared` copy at branch
HEAD `34abf281` and on one at `main` `e741f2ed`. `34abf281` is the committed
tree before the T129, D5-B and E1-R edits, so a module that fails there only
because of a 9.2.0 version, row or file list shows up. Outputs:
`.artifacts/s10e/allmods-34abf281.txt` and `allmods-e741f2ed.txt` (not
committed). A module that passes on `main` and fails on the branch is rerun
alone on both trees to rule out a timing flake.

**Result.** Both sweeps finished: 499 modules ran on `34abf281` and 486 on
`main`; the 13 branch-only modules were added by HAMCP-371 (the
`test_hamcp371_*` modules and the replay and capture-tool tests). Seven modules pass
on `main` and fail on `34abf281`; all seven are below. Six have the 9.2.0
version, row or file-list cause and were edited; `test_read_coverage_matrix`
is outside those kinds and stopped. No module fails on `main` and passes on
the branch.

Also recorded, outside the edit set:

- Fail on both trees, with the same summary on each (outside T129, since they
  do not pass on `main`; cause not investigated here):
  `test_hamcp189_expired_pre_intent_deadline` (errors=1),
  `test_hamcp189_expired_unclaimed_declaration` (errors=1),
  `test_pr314_delta_cleanup` (failures=2).
- `test_hamcp371_real_core_replay` fails on `34abf281` (failures=1, errors=2)
  and is not on `main`. Its failing subtests are the pre-D5-B r12 subtests that
  D5-B turned into the refusal tests; at the D5-B commit it passes (27 OK).
- `test_hamcp319_used_tool_exact_admission` passes on both sweep trees and
  fails after SB, which adds 9.2.0 to `EXACT_GETTER_RELEASES`. It is an SB
  ruling item, recorded in `sb/README.md`, and is not edited.

| Module | Cause | Kind | Disposition |
| --- | --- | --- | --- |
| `test_hamcp129_platform_reads` | `BINDING_VERSIONS` and `SOURCES` have no 9.2.0 | version list, row | edited |
| `test_beta11_upstream_8_compatibility` | supported-version tuple ends at 9.1.2 | version list | edited |
| `test_ha_core_2026_9_integration` | `reviewed_upstream_version` is 9.1.2; the context reports 9.2.0 | row | edited |
| `test_hamcp184_provider_dispositions_fail_closed` | `REVIEWED_AFTER_HAMCP184` has no 9.2.0 row | row | edited |
| `test_ha_mcp_850_candidate_lane` | version set ends at 9.1.2 | version list | edited |
| `test_upstream_release_registry` | version tuple, tool-count and v2-model sets, `expected_automatic_reads` and the copied policy and evidence files have no 9.2.0 | version list, row, file list | edited |
| `test_read_coverage_matrix` | `docs/read_coverage_matrix.json` is stale: the 9.2.0 adapters and tools are not in it, and four `provider_matrix` entries changed (`ha_config_get_yaml`, `ha_get_app`, `ha_get_integration`, `ha_get_system_health`) | generated inventory, outside the ruled kinds | **stopped; not edited.** The approved plan's S13 (authorization item 5) regenerates the matrix with `tests/read_coverage_inventory.py --write`. It fails until then. |

**The edits, module by module.** Each 9.2.0 value comes from the S8 capture
(the 9.2.0 registry row, `upstream_tool_policy_9_2_0.json` and
`ha-mcp-9.2.0-contract-review.json`). None is copied from 9.1.2. Each
edit carries a comment that names its source. No 8.x or 9.1.x expectation
changed, and no assertion was removed or weakened.

| Module | Edit (branch line) | 9.2.0 source |
| --- | --- | --- |
| `test_beta11_upstream_8_compatibility` | `"9.2.0"` added to the supported tuple (`:80-81`) | S8 registry row |
| `test_ha_core_2026_9_integration` | `reviewed_upstream_version` `"9.1.2"` → `"9.2.0"` (`:3117-3121`) | S8 registry row is the newest reviewed row; its `advertised_tool_count` is 77 and the 9.2.0 policy marks `ha_get_hacs_info` unsupported, so the test's other counts hold as written |
| `test_ha_mcp_850_candidate_lane` | `"9.2.0"` added to the version set (`:467-468`) | S8 registry row |
| `test_hamcp129_platform_reads` | `BINDING_VERSIONS` gains 9.2.0 (`:52-54`); `SOURCES["9.2.0"] = ("v9.2.0", "51e3aac7…")` (`:64-65`) | `release_tag` and `source_commit` of the S8 row; the bindings are `upstream_platform_reads.py` `ADAPTER_BINDINGS` from the S8 capture |
| `test_hamcp184_provider_dispositions_fail_closed` | `REVIEWED_AFTER_HAMCP184["9.2.0"] = OPERATIONAL_HELD` (`:84-87`) | S8 row `provider_dispositions`: read gateway and dashboard admitted, backup and lifecycle held |
| `test_upstream_release_registry` | `POLICY_9200` and `ARTIFACT_EVIDENCE_9200` (`:56-57`, `:80`); 9.2.0 added to the `captured_tools` set (`:104`), the fixture policy list (`:150`), the fixture version list (`:175`), the evidence copy (`:206`), the supported tuple (`:285-286`), the tool-count-77 set (`:295`), and the v2-model set (`:333-334`); `expected_automatic_reads["9.2.0"] = 27` (`:311-312`, `:967-968`) | S8 row `advertised_tool_count` 77 and `contract_model`; 27 automatic reads counted in `upstream_tool_policy_9_2_0.json` |

Disclosures:

- `test_ha_core_2026_9_integration` replaces a value instead of adding a row:
  the test asserts the newest reviewed version, which is now 9.2.0. The ruling
  names `reviewed_upstream_version` among the allowed rows. 9.1.2 stays
  admitted in the registry and in every other test.
- **S15 Cursor review item (ruled):** the diff of each of the six modules.

## E1-R: re-admission and the 28 S7 keys

Ruling E1-R (2026-11-15 14:45).

**Item 1 (re-admission of an E1-withdrawn key).** No key is re-admitted.
For every read with an E1 withdrawal (groups, template, the entity-envelope
rows, overview, state, services), the lane reaches the read: the S6 receipt
sets hold calls to each. The withdrawals therefore stay; a re-admission needs a
receipt. The branch-level question, whether the lane can reach the exact
branch that writes `warnings` or `partial` (for example a failed services
fetch inside the overview), was not argued key by key. Keeping a withdrawal
is the fail-closed default; this is an agent choice and is disclosed.

**Item 2 (the 28 keys named only by the shipped 9.1 code).** Each key now has
a 9.2.0 source citation: the line in ha-mcp `51e3aac7` that writes it, on the
named tool's own call path (the chain is in the last column). All 28 stay,
recorded in the contract as source-review keys
(`E1-R 9.2.0 source review, ha-mcp 51e3aac7 src/ha_mcp/tools/<file:line>`).
None is withdrawn. The generator stops if a named key has neither a 9.1.2
receipt nor an E1-R citation, so a future one cannot be admitted silently.
Paths are under `src/ha_mcp/tools/`. Read with `GIT_NO_LAZY_FETCH=1` and git
plumbing only.

| # | Read | Key | 9.2.0 source (writes it) | Call path at `51e3aac7` |
| --- | --- | --- | --- | --- |
| 3 | `ha_config_get_category` | `category` | `tools_categories.py:124` | in `ha_config_get_category` (:45) |
| 4 | `ha_config_get_label` | `label` | `tools_labels.py:647` | in `ha_config_get_label` (:592) |
| 5 | `ha_config_get_scene` | `category` | `tools_config_scenes.py:455` | `_legacy_get_scene` (:418) <- `ha_config_get_scene` :383 |
| 5 | `ha_config_get_scene` | `warnings` | `tools_config_scenes.py:457` | same |
| 6 | `ha_config_get_script` | `warnings` | `tools_config_scripts.py:255` | `_legacy_get_script` (:198) <- `ha_config_get_script` :183 |
| 7 | `ha_config_list_dashboard_resources` | `content_truncated` | `tools_resources.py:265` | in `ha_config_list_dashboard_resources` (:175) |
| 7 | `ha_config_list_dashboard_resources` | `content_truncation_note` | `tools_resources.py:266` | same |
| 7 | `ha_config_list_dashboard_resources` | `migration_hint` | `tools_resources.py:274` | same |
| 7 | `ha_config_list_dashboard_resources` | `note` | `tools_resources.py:279` | same |
| 9 | `ha_config_list_helpers` | `warnings` | `config_helpers/listing.py:231` (also :182) | `_shape_component_helpers_response` <- `tools_config_helpers.py:377` (`_list_helpers_via_component`) <- `ha_config_list_helpers` :229; :182 is `_paginate_helpers_response` <- :207 |
| 10 | `ha_eval_template` | `warnings` | `tools_utility.py:163` | `_render_template` (:135) <- `eval_template` :104 <- `ha_eval_template` :415 |
| 11 | `ha_get_automation_traces` | `diagnostics` | `tools_traces.py:596` | `_format_trace_list` (:528) <- `_fetch_trace_list` :304 <- `ha_get_automation_traces` :199 |
| 14 | `ha_get_entity` | `entity_entries` | `tools_entities.py:1997` | `_get_by_entity_id` <- `ha_get_entity` :1908 |
| 14 | `ha_get_entity` | `errors` | `tools_entities.py:2000` | same |
| 15 | `ha_get_entity_exposure` | `exposed_entities` | `tools_voice_assistant.py:191` | `_list_exposures` <- `ha_get_entity_exposure` :282 |
| 16 | `ha_get_history` | `attributes` | `history_response.py:10,36` | Core's compact `a` renamed; `tools_history.py:35` imports it |
| 16 | `ha_get_history` | `max`, `mean`, `min` | `statistics_helpers.py:74,84` | Core's statistics rows passed unchanged (`"statistics": page`); the names are Core's `StatisticsRow` fields, Core 2026.11.0 `74cf5a49` `homeassistant/components/recorder/statistics.py:342-344` |
| 16 | `ha_get_history` | `warnings` | `tools_history.py:984` (history), `:1099` (statistics) | `_fetch_history`, `_fetch_statistics` <- `ha_get_history` |
| 17 | `ha_get_overview` | `partial` | `smart_search/_overview.py:556` | `_assemble_overview_response` <- `get_system_overview` :201 <- `search/overview.py:538` (`_assemble_overview`) <- `_collect_overview` :450 <- `tools_search.py:655` (`ha_get_overview`) |
| 19 | `ha_get_state` | `success`, `count`, `states` | `search/state.py:89,90,91` | `_build_bulk_states_response` <- `_get_bulk_entity_states` :206 <- `tools_search.py:848` (`ha_get_state`) |
| 19 | `ha_get_state` | `errors` | `search/state.py:102` | same |
| 19 | `ha_get_state` | `partial` | `search/state.py:109` | same |
| 25 | `ha_search` | `dashboards` | `search/component.py:523` | `_component_config_payload` <- `_shape_component_search_response` :633 <- `_ha_search_via_component` :751 <- `tools_search.py:442` (`ha_search`) |
| 25 | `ha_search` | `warnings` | `search/modes.py:688` | `_search_regular` <- `search/legacy.py:386` <- `_legacy_ha_search` :88 <- `tools_search.py:410` |

Notes:

- A citation shows the 9.2.0 code can write the key on that read. It is not an
  observation. SR or live acceptance observes it, or it stays unobserved; the
  lane receipts did not carry any of the 28.
- Row 16 `max`, `mean`, `min`: ha-mcp writes no literal for these names; it
  passes Core's statistics rows unchanged. The Core citation was read from the
  GitHub contents API at the exact commit (copy in `.artifacts/s10e/core/`,
  not committed).
- Several citations sit in the no-component (`_legacy_*`) path of a read that
  also has a component path. Either path is a 9.2.0 path the read can take.
- No runtime change: `ROWS` in `upstream_reads_9_2_keys.py` holds names only,
  and the names are unchanged. Only the contract's source strings change.

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
- `partial` on overview and state remains admitted from S7; under E1-R both
  are source-review keys (section E1-R).

The 28 S7 keys whose only source was the shipped 9.1 code are tabled, with
their 9.2.0 citations, in section E1-R.

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
7. **Edits to HAMCP-371's own tests**: see E2. Shipped test modules were
   edited only under T129 (version, row and file lists; see that section).
8. **Authorizations.** `_platform.py` was created under D5 and `_blueprint.py`
   under D5-B, with the one D5-B gateway call change and the
   `validate_result` signature change. `_config.py` was not needed (the config
   reads run through `ROWS`).
9. **Scratch.** An early scratch copy of the upstream dashboard file was
   written to `/tmp`; it was moved to the research area outside the worktree.
10. **Shipped test modules that failed since S8** are edited under T129 only
    within the ruled kinds: see the T129 section for the full list and diffs.

## Validation (2026-11-15, local, uncommitted tree on `34abf281`)

The S10 final run: one `unittest` process per module, in sequence, on the
working tree that this commit records (`.artifacts/s10e/s10-final.txt`).

| Check | Result |
| --- | --- |
| Every `tests/test_hamcp371_*` module (9, including the new `test_hamcp371_blueprint_9_2`, 11 tests) | all OK. `test_hamcp371_real_core_replay` 27 tests OK, including the r12 refusal tests (D5-B) |
| Shipped modules, unmodified: `test_readonly_upstream_gateway`, `test_upstream_8_5_compatibility`, `test_upstream_8_6_compatibility`, `test_hamcp319_parity_entities`, `test_hamcp319_used_tool_exact_admission`, `test_hamcp319_side_by_side`, `test_hamcp319_no_redirect_useful`, `test_core_read_replay_calls`, `test_rc2dev9_upstream_admission`, `test_codex_workflow` (53 skipped), `test_ha_mcp_production_readmission` | all OK. These include the shipped blueprint and gateway tests (D5-B: they pass unmodified) |
| The six T129 modules, edited | all OK: `test_hamcp129_platform_reads` 39, `test_beta11_upstream_8_compatibility` 6, `test_ha_core_2026_9_integration` 72, `test_hamcp184_provider_dispositions_fail_closed` 16, `test_ha_mcp_850_candidate_lane` 47, `test_upstream_release_registry` 17 |
| `test_read_coverage_matrix` (unmodified) | **FAILED (4 failures), stopped under T129.** The generated inventory is stale; S13 regenerates it (see T129) |
| Totals | 27 modules: 26 OK with 1,006 tests, 1 FAILED |
| Full-module sweeps (T129) | still running at this commit; see T129 |
| `generate_output_key_contract.py --check` | exit 0, up to date |
| `scripts/review_upstream_read_release.py validate` | passes, 13 releases including 9.2.0 |
| Fast tier (`-BaseRef origin/main`, 24 declared protected paths: the 23 of S10 plus `providers/upstream_reads_9_2_blueprint.py`; test targets `test_hamcp371_blueprint_9_2`, `test_hamcp371_key_sets`, `test_hamcp371_platform_9_2`, `test_hamcp371_unreachable_modes`, `test_beta11_upstream_8_compatibility`, `test_hamcp184_provider_dispositions_fail_closed`, `test_ha_mcp_850_candidate_lane`) | passed, 118 tests |
| Repository scan (`docs/evidence/hamcp371/s6/comparison/scan.py`) on each changed file | 15 files, 4 hits, all pre-existing on `main`: `test_ha_core_2026_9_integration.py` (token_value 1, long_base64url 1) and `test_ha_mcp_850_candidate_lane.py` (token_value 2) give the same 4 hits at `e741f2ed`; the lines this change adds to those two files give 0. Every other file gives 0 |
| Leak check on each changed file, including this README | 15 files, each PASS |

The Full and Evidence tiers were not run: they are not part of S10.
