# HAMCP-371 S10: closed 9.2.0 key sets, zone and services, the dashboard getter

S10 closes the key sets of the compiled 9.2.0 public reads (21:36 record,
items 7 to 12), adds the zone and services handling (items 13 to 15, research
G 2b and 2e), keeps the 9.2.0-only modes unreachable (2f, item 16) and puts
the dashboard getter's counts and warning rule behind one 9.2.0 gate (N30).
The counts on the platform reads (2d) are **not done**: see the rulings
question below.

This is a local candidate only. Nothing was signed, published, pushed or
deployed, and no Home Assistant instance was accessed.

## Rulings question for Josh (S10 stops here)

**Question S10-1.** Rows 12 (`ha_get_blueprint`), 20 (`ha_get_system_health`),
26 (`ha_get_integration`) and 27 (`ha_get_app`) do not route through the
9.2.0 read module (`upstream_reads_9_2._projection`). The gateway calls
`validate_blueprint_result` for blueprint and `project_platform_result` for
the three platform reads directly (`upstream_read_gateway.py:4253-4264`).
`upstream_platform_reads.project_result` (`:772-783`) chooses its projection
by tool name, not by version, and the 9.2.0 rows in its `ADAPTER_BINDINGS`
and in `upstream_blueprint.ADAPTER_BINDINGS` hold only metadata (version,
commit, fingerprint). So the planned `upstream_reads_9_2_platform.py` and
`upstream_reads_9_2_blueprint.py` cannot be reached unless one of the
following is authorized:

- list A row 8 (`upstream_platform_reads.py`) and row 9 (`upstream_blueprint.py`)
  allow 9.2.0 rows only, with projection code unchanged, so a gate there is
  not allowed; or
- a gateway change, but all four R1 entries are used (S9), and a fifth
  gateway change is a stop and report.

Evidence from the 9.2.0 real-Core replay (`tests/test_hamcp371_real_core_replay.py`,
`RealCoreReplay920`):

- Blueprint has shape-only validation. An injected unknown top-level key
  passes through to the client, which conflicts with item 7 (withhold and
  count).
- System health and integration already withhold unknown keys through their
  closed rebuilds, but add no `fields_withheld`, `fields_dropped_by_policy`
  or `upstream_warnings_replaced` counts.
- Integration replaces unlisted upstream warnings with its own shipped code
  `upstream_warnings_withheld`, not `upstream_warning_replaced`.
- The contract marks system health's `info` (`health_info.data.homeassistant.info`)
  as an open container, but the shipped health rebuild keeps only its fixed
  `platform.homeassistant` fields, so a key injected inside `info` is dropped
  rather than passed whole (item 11; the open-container test fails on r20).
  Under (a) the contract row or the module must settle which applies.
- App has no successful receipt (r27-apps errors on both versions; SR records
  it).

Options:

- (a) Authorize, like N30, exactly one 9.2.0 gate and one call in
  `upstream_platform_reads.py` (in `project_result`) and in
  `upstream_blueprint.py` (in `validate_result`), each calling a new 9.2.0
  module; 9.1 behaviour unchanged, with negative tests that the module is
  never called on 8.5.0, 9.1.0 and 9.1.2.
- (b) Authorize one more gateway change that sends these four rows through a
  new 9.2.0 module.
- (c) Leave the four rows on their shipped behaviour on 9.2.0, uncounted, as a
  disclosed gap (blueprint's pass-through of unknown top-level keys included),
  and adjust HAMCP-371's own replay test to expect it. The `_platform.py` and
  `_blueprint.py` authorizations are then dropped (condition c).

Recommendation (inference): (a). It matches N30's pattern, keeps the gateway
at its four R1 entries, and closes the blueprint pass-through, which (c)
would leave open.

Until this is ruled, `RealCoreReplay920` keeps 3 failures and 8 errors, all
on rows 12, 20 and 26 (listed under Validation). They are left failing, not
marked as expected.

**Question S10-2 (smaller).** On 9.2.0 `ha_list_services`, the keys
`integration` (inside `target.entity[]`, Core's entity filter copied unchanged
at `tools_services.py:576-578`) and `collapsed` (a field-group flag, `:627`)
are not in the 9.1.2 baseline and have no recorded review, so they are
withheld and counted. Recommendation: review both as Core's service-description
keys copied unchanged and admit them on the 9.2.0 row. Until then a services
read that carries them reports `fields_withheld` above 0.

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
7. **Edits to HAMCP-371's own tests** (no 9.1 test changed):
   - `tests/test_hamcp371_history_offsets.py`: the S9 equality between the
     history module's `ADDED` and the contract's `engineering_added` now
     excludes `engineering_notes`, which S10 adds and history does not emit.
   - `tests/test_hamcp371_real_core_replay.py`: device expectations follow
     N26 (`DEVICE`, `DEVICE_UNRECOGNIZED`, `DEVICE_UNKNOWN`): the count is
     read with `get(..., 0)`, no policy-drop or replaced count is expected,
     and the closed device codes are expected instead of
     `upstream_warning_replaced`.
8. **Authorizations not used yet.** `_config.py`, `_platform.py` and
   `_blueprint.py` were not created. `_config.py` was not needed (the config
   reads run through `ROWS`). `_platform.py` and `_blueprint.py` wait on
   question S10-1.
9. **Scratch.** An early scratch copy of the upstream dashboard file was
   written to `/tmp`; it was moved to the research area outside the worktree.

## Validation (2026-11-15, local)

| Check | Result |
| --- | --- |
| `tests.test_hamcp371_key_sets` | 13 tests OK |
| `tests.test_hamcp371_unreachable_modes` | 9 tests OK |
| `tests.test_hamcp371_dashboard_getter` | 9 tests OK |
| `tests.test_hamcp371_history_offsets` | 28 tests OK |
| `tests.test_hamcp371_admission_core` | 30 tests OK |
| `tests.test_hamcp371_real_core_replay` | 26 tests; 3 failures and 8 errors, all in `RealCoreReplay920` on r12, r20 and r26 (question S10-1). The S9 state was 34 failures and 85 errors. Every other class passes. |
| `generate_output_key_contract.py --check` | up to date |
| `scripts/review_upstream_read_release.py validate` | passes |
| Unmodified regression modules, one per module (the S9 set: every `test_hamcp319_*` module, `test_hamcp131_history_*`, `test_hamcp351_ha_mcp_912_admission`, `test_hamcp162_yaml_admission`, `test_hamcp371_error_classification`, `test_ha_mcp_production_readmission`, `test_upstream_8_5_compatibility`) | 28 modules, 2,257 tests, all OK |
| Fast tier (`-BaseRef origin/main`, 22 declared protected paths, test targets `test_hamcp371_dashboard_getter`, `test_hamcp371_key_sets`, `test_hamcp371_unreachable_modes`, `test_hamcp319_dashboard`) | passed, 64 tests |
| Repository scan (`docs/evidence/hamcp371/s6/comparison/scan.py`) and leak check, run on each changed file | 14 files, 0 hits; each file PASS |

The remaining `RealCoreReplay920` failures:

- `test_admitted_keys_and_withheld_counts`: r12, r20 and r26 in sets A and B
  (6 errors, `KeyError: 'fields_withheld'`);
- `test_an_injected_key_is_withheld_and_counted_once_per_name`: integration
  and system health (2 errors) and blueprint (1 failure: the key passes
  through);
- `test_a_key_inside_an_open_container_passes_uncounted`: r20 (1 failure: the
  key inside `info` is dropped);
- `test_an_unlisted_upstream_warning_is_replaced_and_counted`: r26 (1 failure,
  `upstream_warnings_withheld` instead of `upstream_warning_replaced`).

The Full and Evidence tiers were not run: they are not part of S10.
