# HAMCP-371 S13: CI lanes move to the 9.2.0 candidate

S13 moves the admission lanes from ha-mcp 9.1.2 to 9.2.0, under the owner's S13
acceptance (plan, verbatim): "S13 is accepted: CI lanes move to 9.2.0, and
9.1.2 stops being exercised in CI on this branch. The 8.5.0 add-on lanes keep
running." It continues S10 to S12 under the architect's 2026-11-15 14:45 CST
instruction, relayed by Josh: "then S11 onward, stopping after SR."

This step edits source only. No lane was run, no workflow was dispatched, and no
Home Assistant instance was accessed. The lanes themselves run only in CI, on a
pushed head; S16 is the first push.

Paths: `P` = `hass_mcp_engineering_beta/ha_mcp_engineering`.

## Files

| Group | File | Change |
| --- | --- | --- |
| support window | `ci/support-window.yaml` | `admission.candidate_version` 9.1.2 → 9.2.0; `baseline_version` stays 8.5.0 |
| lane scripts | `scripts/exact_image_admission_91_runners.py` | `CANDIDATE_VERSION` 9.2.0 |
| | `scripts/exact_image_admission_91_governed.py` | `UPSTREAM_VERSION` 9.2.0; docstrings and comments |
| | `scripts/exact_image_admission_91_typed.py` | `UPSTREAM_VERSION` 9.2.0; refusal codes `peer_version_not_9_2_0`, `admission_candidate_not_9_2_0`; docstrings |
| acceptance script | `scripts/exact_image_read_gateway_acceptance.py` | 9.2.0 rows (below); the two history calls |
| tests of those scripts | `tests/test_hamcp319_91_governed_runner.py`, `tests/test_hamcp319_91_lane_runners.py`, `tests/test_hamcp319_91_typed_runner.py`, `tests/test_hamcp319_exact_image_admission_lanes.py` | candidate 9.1.2 → 9.2.0 (see "Test edits"; ruled S13-1 (a)) |
| lane selection (follow-up) | `tests/test_hamcp351_ha_mcp_912_admission.py` `LanePeerSelectionTests` | candidate 9.1.2 → 9.2.0 (S13-1 ruling) |
| used-tool admission (follow-up) | `tests/test_hamcp319_used_tool_exact_admission.py`, `tests/test_hamcp319_dashboard.py` | 9.2.0 appended; 9.2.0 dashboard peer row (SB used-tool ruling) |
| coverage matrix | `docs/read_coverage_matrix.json`, `docs/READ_COVERAGE_MATRIX.md` | regenerated with `tests/read_coverage_inventory.py --write` |
| Q8 check | `tests/test_hamcp371_q8_prepush_equality.py` (new) | the Q8 pre-SR equality check |

The three `91` scripts keep their file names; they are the lane scripts the
workflow calls, and renaming them would need a workflow edit.

## Each 9.2.0 row and its source

All rows are in `scripts/exact_image_read_gateway_acceptance.py`. Each was
written from the 9.2.0 capture or the 9.2.0 policy, never by analogy with the
9.1.2 row. Where a value equals 9.1.2's, the source was checked separately.

| Row | Value | Source |
| --- | --- | --- |
| `APP_TOOL_TRANSITION_VERSIONS` | adds 9.2.0 | the reviewed capture `docs/evidence/upstream-read-compatibility/ha-mcp-9.2.0.json` (registry `capture_sha256` `19c31e47…1281`, 77 tools) lists `ha_get_app` and `ha_manage_app` and no `ha_get_addon` |
| `EXPECTED_STOCK_COUNTS_BY_VERSION["9.2.0"]` | automatic_read 27, held_for_canary 1, mixed_or_requires_wrapper 10, persistent_write 32, physical_or_high_risk_action 4, prohibited 1, unsupported 2 | `classification_counts` of the 9.2.0 policy loaded through the release registry (`load_reviewed_upstream_release_registry().by_version["9.2.0"].policy`). That property counts the 4 wrapper read routes as `automatic_read`; the raw `P/upstream_tool_policy_9_2_0.json` per-tool classifications are 23 and 14, as `addon-upgrade-capability-loss.md` section 2 records. Both measures are equal on 9.1.2 and 9.2.0. |
| `DELEGATED_READ_CALL_OVERRIDES_BY_VERSION["9.2.0"]` | `ha_get_skill_guide` with `skill` `home-assistant-best-practices`, `file` `SKILL.md` | the 9.2.0 adapter serves only the file tier (`P/upstream_reads_9_2.py` `read_arguments`, row 18, S10) |
| `EXPECTED_ERROR_SHAPE_FINGERPRINTS["invalid_search"]["9.2.0"]` | `fc0f1e8b…f939` | the 9.2.0 capture's own `error_shapes.invalid_search.shape_fingerprint` (`VALIDATION_FAILED`). The four 9.2.0 `error_shapes` equal 9.1.2's, compared value by value. |
| `upstream_wire_arguments` | 9.2.0 releases use `upstream_reads_9_2.read_arguments`; 9.1 releases keep `upstream_reads_9_1`; others pass through | the S10 module split |
| `ha_search` `visible_scope_only` and partial-results gates | widened to `upstream_reads_9_2.RELEASES` | the S11 9.2.0 search projection (S11 README) |

## The two history calls

S13 plan: "one explicit-offset and one offset-free history call added". S9
ruling 1: on 9.2.0, an offset-free `start_time` is refused before dispatch.

- `HISTORY_EXPLICIT_OFFSET_ARGUMENTS`: `sun.sun`, `2026-11-01T00:00:00+00:00`,
  limit 5. It must not return `success` false, and must report provider
  `upstream_read_gateway` and the expected upstream version.
- `HISTORY_OFFSET_FREE_ARGUMENTS`: the same with no offset. It must return
  `success` false, `error_code` `invalid_request`, failure category
  `history_time_offset_required`, `upstream_dispatch_occurred` not true, and an
  unchanged fixture counter.

They run only when the expected upstream version is a 9.2.0 release and
`ha_get_history` is an automatic read. They run after every accounting check.
`UPSTREAM_ERROR_CALLS` assumes an upstream dispatch and feeds the
provider-routing and gateway failure counts, so adding them there would change
the existing 8.x and 9.1 expectations. The gateway maps the refusal at
`P/upstream_read_gateway.py` (`HistoryTimeOffsetRequired` →
`history_time_offset_required`, `dispatched=False`).

These calls are exercised only when the acceptance script runs against a real
9.2.0 image in CI. No local unit test runs the script end to end. The S9 unit
tests (`test_hamcp371_history_offsets.py`, `test_hamcp371_admission_core.py`
`test_920_offset_free_history_refuses_with_no_upstream_call`) pin the same
refusal in-process.

## Test edits (ruling item)

The S13 plan lists "The tests of those scripts" among the files edited. N11
reads: ""existing tests pass unmodified" covers 9.1 behaviour tests. Tests that
enumerate admitted versions may append 9.2.0, without removing or changing any
existing expectation."

These four modules test the lane configuration: which candidate the lanes
admit. They are not 9.1 behaviour tests (inference). With the candidate moved,
they fail unedited:

| Module | Unedited on S13 scripts | Edited |
| --- | --- | --- |
| `test_hamcp319_91_governed_runner` | 5 failures, 7 errors | 94 OK |
| `test_hamcp319_91_lane_runners` | 103 failures, 2 errors | 100 OK |
| `test_hamcp319_91_typed_runner` | 44 failures, 3 errors | 25 OK |
| `test_hamcp319_exact_image_admission_lanes` | failed | 73 OK |

The edits replace the candidate value 9.1.2 with 9.2.0, and the identifier
`9_1_2` with `9_2_0`, in the candidate's place only. One fixture source moves:
`OPTIONAL_91` in `test_hamcp319_91_lane_runners.py` reads the five optional
filesystem descriptors from the committed S8 standalone fs-on capture
(`docs/evidence/hamcp371/s8/profiles-2/out/standalone-9.2.0-fs-on/capture.json`)
instead of the HAMCP-351 9.1.2 response. No assertion is removed. The full diff
is `.artifacts/s13/lane-tests-trial.patch` (scratch) and the commit diff.

This changes existing expectations, which N11 does not cover. `b8e42139` kept
the edits, disclosed, pending a ruling. A fifth module failed for the same
cause and was left unedited there:
`tests/test_hamcp351_ha_mcp_912_admission.py` `LanePeerSelectionTests`
(5 failing and 2 erroring subtests, in 2 of the module's 23 tests). It asserted
that `typed_lane.select_peer_image` selects the pinned 9.1.2 row and refuses
others with `peer_version_not_9_1_2`.

### S13-1 ruling, applied in the follow-up commit

Architect ruling 2026-11-15 17:50 CST, relayed by Josh (verbatim): "S13-1
RULING: (a). N11's append-only rule governs version lists. The single CI
candidate slot moves, as bcdff105 did. Accept the four module edits. Edit
test_hamcp351_ha_mcp_912_admission LanePeerSelectionTests the same way: the
typed lane selects the 9.2.0 row and refuses 9.1.2 as candidate. That is lane
selection only; 9.1.2 admission is unchanged. No assertion removed. Record in
the S13 README and, at S16, in the release notes: after this release no CI
admission lane exercises 9.1.2; it stays admitted in the registry, source
admitted, never replayed, never live. S15 review item."

`LanePeerSelectionTests` now builds its row from the reviewed 9.2.0 release.
- `test_pinned_920_row_selects_each_architecture` asserts the 9.2.0 index and
  platform digests, `INDEX_920` and `PLATFORMS_920`. They are written from
  `docs/evidence/hamcp371/s4` (`index-9.2.0.raw.json`,
  `inspect-digest-9.2.0.txt`).
- `test_912_911_and_unpinned_digests_are_refused` keeps every earlier case
  (9.1.1, 9.1.0, tag only, other index, other platform), now with
  `peer_version_not_9_2_0`. It adds two cases: the full pinned 9.1.2 row, and
  a 9.1.2 version label on the 9.2.0 digests. Both are refused as candidate.
- The module's 9.1.2 registry, policy and adapter tests are unchanged.

**For the S16 release notes (ruled):** after this release no CI admission lane
exercises ha-mcp 9.1.2. 9.1.2 stays admitted in the registry, source
admitted, never replayed, never live.

### SB used-tool ruling, applied in the same commit

Architect ruling 2026-11-15 17:50 CST (verbatim): "SB USED-TOOL RULING: (a).
Apply trial3 (.artifacts/sb/ut/trial3.patch) and add 9.2.0 rows to
tests/test_hamcp319_dashboard.py, additive only, as bcdff105 did for 9.1.2.
The 9.2.0 peer shapes come from the 9.2.0 capture (profiles-2 standalone
filesystem-on), with the source cited, not from the 9.1 shapes by analogy.
The 33 original test_hamcp319_dashboard tests must still pass with their
expectations unchanged. S15 review item."

- `tests/test_hamcp319_used_tool_exact_admission.py`: trial3 as written (the SB
  README ruling item): `'9.2.0'` appended to `NINE_ONE`, and `WIRE_920` is
  the profiles-2 standalone fs-on `tools/list`. One comment line added.
- `tests/test_hamcp319_dashboard.py`, additive only. `NINE_TWO = ('9.2.0',)`
  is appended to `NINE_ONE`, and `URLS['9.2.0']` is added. `catalog()` is
  unchanged: `catalog('9.2.0')` keeps the reviewed 9.2.0 capture's runtime
  order (77 tools), which `test_hamcp371_admission_core` relies on. The
  module's `VERSIONS` stays `('9.1.0', '8.5.0')`, so its 33 tests run
  unchanged. The sources of the 9.2.0 peer shapes:
  - Descriptors: the used-tool module serves the profiles-2 standalone fs-on
    `tools/list` named above (82 tools, wire order). Its getter, guide and
    setter descriptors equal those in the reviewed 9.2.0 capture
    (`ha-mcp-9.2.0.json`), compared value by value. The extra five are the
    filesystem and YAML tools.
  - First attempt, withdrawn: `catalog('9.2.0')` also returned the fs-on
    list. The affected-module rerun then failed
    `test_hamcp371_admission_core` `test_920_exposed_surface_equals_912`
    (`ha_config_get_yaml` exposed on 9.2.0 only, since 9.1.2's catalog has
    no YAML tool). Removing that override restored the module (33 OK).
  - Reply envelopes: the capture is a `tools/list`, so it holds no reply
    bodies. The peer keeps the envelopes in
    `tests/fixtures/ha_mcp_9_1_0/dashboard/envelopes.json`, which were
    transcribed from source. Source review: the seven source files listed in
    that fixture's `provenance.json` have the same sha256 at v9.1.0
    (`d2e3f4b0`), v9.1.2 (`bab615df`) and v9.2.0 (`51e3aac7`). They were read
    with `GIT_NO_LAZY_FETCH=1 git show <commit>:<path> | sha256sum` from the
    disposable ha-mcp clone. So the envelopes describe 9.2.0 by source
    identity, not by analogy.
  - The guide: `docs/evidence/hamcp371/s4/ha-mcp-9.2.0/image-resources.json`
    records the 9.2.0 image's `dashboard-guide.md` with the fixture's sha256
    `b4d42e93…d293`.
  - `KEY_91` is synthetic, as before. The provider reads it from the guide
    acknowledgement line, whose source (`strict_bps.py`) is one of the seven
    identical files.
- `test_hamcp371_dashboard_getter` still patches `NINE_ONE` and `URLS` for
  9.2.0. Those patches are now redundant but harmless (the module passes).

## Stale workflow labels, left unchanged

Workflow edits are not authorized, so two step names stay stale:

- `.github/workflows/ci.yml:1039` "Start synthetic read-only fixture for the
  9.1.0 lanes" (the plan's own note).
- `.github/workflows/ci.yml:1483` "Case side-by-side previous release does not
  review 9.1.2 on the strict-full value". Three literals in
  `test_hamcp319_91_lane_runners.py` pin this step name, so they keep "9.1.2".

No lane needed a workflow edit, and no test or lane needed a signed 9.2.0
artifact. So neither S13 stop condition was reached.

## Coverage matrix

`tests/read_coverage_inventory.py --write`, the same step HAMCP-351 took for
9.1.2 (`ddf9d83f`). It adds the 9.2.0 policy and adapter items to
`tool_catalog`, records the changed `provider_registry` items (`control_fan`,
`control_helper`, `control_power`, `ha_config_get_yaml`, `ha_get_app`,
`ha_get_integration`, `ha_get_system_health`), and refreshes cited line numbers.
The Markdown diff is line numbers and the inventory count only. No coverage
mapping changes: `test_read_coverage_matrix` passes (17 tests) with no new
entry. The `ha_config_get_yaml` row prose ("ha-mcp 8.4.3, 8.5.0 and 9.1.0
admitted") already omits 9.1.2 on main and was not edited.

## Q8 check

`tests/test_hamcp371_q8_prepush_equality.py` checks the committed JSON directly:
- the 9.2.0 registry row's `provider_dispositions` and
  `dashboard_attestation.status` equal 9.1.2's, and the status is `reviewed`;
- the built-in attestation entry is present and not revoked, and its contract
  fingerprints equal 9.1.2's;
- no 9.2.0 value is `quarantined`.

The four negative cases run the same check on edited copies: a quarantined
status, a changed disposition, a revoked or changed attestation, and a missing
row. At S13 the committed state passes.

## Validation

Each affected module was run on its own: every module that imports the edited
scripts, reads the support window or the coverage matrix, plus every
`test_hamcp371_*` module and `test_upstream_release_registry`. That is 45
modules and 1,435 tests. 44 pass. `test_hamcp351_ha_mcp_912_admission` fails as
described above. The Fast tier result is in the S13 commit message.

After the 17:50 rulings, the follow-up commit reruns the same 45 modules and
the Fast tier; results are in that commit message.
