# HAMCP-232 current-state recheck: governed edits to existing UI helpers (2.6.0)

Status: **recheck only (docs).** This document checks register row HAMCP-232
against current source at the 2.6.0 base. It adds no capability, changes no
runtime, profile, schema or version, and authorizes no signing, release,
deployment or live Home Assistant access. The status recommendation in §6 is
for Josh to decide; this document does not change the register status.

| Item | Value |
|---|---|
| Base | `origin/release/2.6.0` `7246f2d21693f039504542574569f03b71476489` (Engineering `2.4.2-beta.7`, `hass_mcp_engineering_beta/config.yaml:2`) |
| Register | HAMCP-232 (audit HAAC-E016). Read only: HAMCP-274 (input_select), HAMCP-276 (helper options dispatch authority) |
| Prior design | [`HAMCP_232_145_HELPER_OPTIONS_AND_SELECT_DESIGN.md`](HAMCP_232_145_HELPER_OPTIONS_AND_SELECT_DESIGN.md) (PR #314, shipped in 2.4.1-beta.41) |
| Signed journal | canonical `registry` branch, tip `e486028ed0b06b8a5a737824c829705b403b34d8` (sequence 17, published 2026-11-07 00:59 CST). The `main` copy `upstream-trust/ha-core-release-registry.json` is frozen at sequence 15 ([`CORE_RELEASE_REGISTRY.md`](CORE_RELEASE_REGISTRY.md), "Canonical source, frozen `main` copy and rollback") |
| Newest signed and bound Core | 2026.11.0, commit `74cf5a49c63ed8d4d87717844bf14f79c28c9cea` (tag `2026.11.0` resolves to this commit by `git ls-remote`). Binding: `scripts/core_lane_bindings.py:61-63`. Signed in sequences 16 and 17 |
| Paths | `E/` = `hass_mcp_engineering_beta/ha_mcp_engineering/`. Core paths are relative to `homeassistant/` at `74cf5a49` |
| Checked | 2026-11-07 07:30 CST |

## 1. Register outcome and binding decisions

**Outcome (quoted from the row):** "A governed plan family changes the options
of an existing UI-created helper via its config-entry options flow (exact
target, baseline fingerprint, impact disclosure, approval, one dispatch,
authoritative readback, rollback to captured options), built inactive and
admitted by owner signing."

**Task title (quoted):** "Governed edits to EXISTING UI (config-entry)
helpers: template state template, filter, statistics, utility_meter options
(audit HAAC-E016)".

Decisions that bind this recheck. Each is recorded as Josh's in the prior
design's §0 or in the HAMCP-232 register notes:

| Id | Decision (source) |
|---|---|
| OD-1 | The options flow is opened and aborted only inside `core.config_entry_helper_options_operation`, only for the plan's exact entries, and never as a general read (design §0, 2026-10-26) |
| OD-2 | Increment 1 covers template (sensor and binary_sensor only), filter and statistics, in one shared profile with a closed domain list. **utility_meter is DEFERRED** (design §0, 2026-10-26) |
| OD-3 | Template types with ActionSelector fields are refused. Engineering enforces `read_only` fields as pinned (design §0) |
| OD-6 | Rollback is a separately approved plan (design §0) |
| OD-7 | The residual concurrency window (no Core compare-and-swap) is accepted and disclosed (design §0) |
| Lost-open residual | Accepted 2026-10-27 (register note "OWNER DECISION (Josh, 2026-10-27, by sending the batch as written): ACCEPT the residual"). It is reported as "possibly left open; no configuration change; discarded at the next HA restart" |

## 2. What is governed today (base `7246f2d2`)

### 2.1 Helper types and option fields

The closed domain list is `DOMAINS = ("filter", "statistics", "template")`.
Template is limited to `TEMPLATE_TYPES = ("binary_sensor", "sensor")` and
entities to `ENTITY_DOMAINS = ("binary_sensor", "sensor")`
(`E/governance/helper_options.py:53-55`). The per-step field catalog is
`STEP_CATALOG` (`E/governance/helper_options.py:217-271`, with the shared filter and template fields at `:199-211`):

| Type (step) | Editable | Pinned (must equal baseline) |
|---|---|---|
| filter `lowpass` | `window_size`, `time_constant`, `precision` | `entity_id`, `filter` |
| filter `outlier` | `window_size`, `radius`, `precision` | `entity_id`, `filter` |
| filter `range` | `lower_bound`, `upper_bound`, `precision` | `entity_id`, `filter` |
| filter `time_simple_moving_average` | `window_size` (duration), `precision` | `type`, `entity_id`, `filter` |
| filter `throttle` | `window_size`, `precision` | `entity_id`, `filter` |
| filter `time_throttle` | `window_size` (duration), `precision` | `entity_id`, `filter` |
| statistics `init` | `sampling_size`, `max_age`, `keep_last_sample`, `percentile`, `precision` | `entity_id`, `state_characteristic` |
| template `sensor` | **`state`** (the state template), `unit_of_measurement`, `device_class`, `state_class`, `additional_options.availability` | `device_id` |
| template `binary_sensor` | **`state`**, `device_class`, `additional_options.{availability, delay_on, delay_off}` | `device_id` |

The public input schema publishes only the union of editable names
(`E/governance/helper_options.py:273-278`; `E/tools/governance.py:1462-1512`).
Entity ids must match `^(sensor|binary_sensor)\.[a-z0-9_]+$`. A plan holds
1-8 changes.

Refusal paths at the base:

- **Domain outside the list** (utility_meter included):
  `helper_options_domain_not_supported`
  (`E/governance/helper_options_planning.py:475-478`). Test:
  `tests/test_hamcp232_helper_options.py:720-721` sets the entry domain to
  `utility_meter` and asserts the refusal.
- **Template types other than sensor/binary_sensor**: their entities fall
  outside the schema's entity pattern and `ENTITY_DOMAINS`
  (`E/governance/helper_options_planning.py:121`). An unknown step id raises
  `options_step_not_supported` (`E/governance/helper_options.py:289`).
- **Field outside the step, or a no-op**: `helper_options_field_not_editable`
  (`E/governance/helper_options_planning.py:142`) and
  `helper_options_already_desired` (`:542`). Tests:
  `tests/test_hamcp232_helper_options.py:695-710`.

### 2.2 Coverage of the outcome items

| Outcome item | Base evidence | Status |
|---|---|---|
| template state template | `("template","sensor")` and `("template","binary_sensor")` with `state` `KIND_TEMPLATE` (`E/governance/helper_options.py:256-270`) | **Covered** |
| filter | six filter steps (`:218-246`) | **Covered** (the filter type itself is pinned) |
| statistics | `("statistics","init")` (`:247-255`) | **Covered** |
| utility_meter | not in `DOMAINS`; refused (§2.1) | **Not covered: deferred by OD-2** |
| Exact target, baseline fingerprint | entry resolution and refusals (`E/governance/helper_options_planning.py:465-500`); baseline read by open, read, abort; `modified_at` read after the open | Covered |
| Impact disclosure | consumer evidence model `config_entry_helper_options_consumer_evidence_v1` (`E/governance/helper_options.py:52`). The HAMCP-285 positive-evidence consequence label (`E/governance/helper_options_planning.py:236-252`) replaces the `safety_critical` over-labelling noted in HAMCP-274 on 2026-11-02 | Covered |
| Approval | `external_approval_required: True`, `elevated_acknowledgement_when_classified: True` (`E/capabilities.py:459-476`) | Covered |
| One dispatch | F3 identity `update_config_entry_helper_options_configuration` (`E/f3_runtime/registry.py:101-107`; `E/ha_core_readmission/routes.py:138-148`); contract `dispatch: at_most_once_options_flow_step_submit_per_config_entry` (`E/ha_core_readmission/profiles.py:665`) | Covered |
| Authoritative readback | contract `readback: options_flow_suggested_values_and_modified_at_exact` (`E/ha_core_readmission/profiles.py:675`) | Covered |
| Rollback to captured options | `_create_helper_options_rollback` (`E/f3_runtime/runtime.py:5535-5680`), which refuses with `helper_options_rollback_baseline_drift` (`:5659-5669`). Tests: `tests/test_hamcp232_helper_options.py:827`, `:1359`, `:1370`, `:1434` | Covered |
| Built inactive, admitted by owner signing | admission is checked before validation or any read (`E/governance/service.py:6275-6291`, refusal `pending_signed_registry_admission` at `:6336-6337`). Signed admission is in §3 | Covered and admitted |

Execution evidence for the outcome, as recorded (not re-run here):

- **Live** (HAMCP-276 notes, 2026-11-02, beta.57): a template sensor
  `additional_options.availability` edit gave task `47c22a79`
  `succeeded_verified` with 1 dispatch. The governed rollback gave task
  `12f97bdb` `succeeded_verified`. A no-op was refused with
  `helper_options_already_desired`. Only template sensor was exercised live.
- **Disposable Core 2026.11.0** (HAMCP-280, in the base):
  `docs/evidence/hamcp280/receipts/helper_options.json` records
  `result: PASS` against image
  `sha256:c6c1ca9931036bf3a2c4cc088d8db25dfea3b33935a5ecc83c40349462e2f69e`.
  It covers filter, statistics, template sensor and template binary_sensor
  edits with `succeeded_verified` and one dispatch each, plus the refusal
  scenarios `field_not_editable`, `validation_would_fail`, `stale_state`
  (0 dispatches) and `concurrent_flow` (the OD-7 overwrite observed and
  disclosed; rollback refused for baseline drift). Core 2026.10.1 and
  2026.10.3 are covered by
  [`HAMCP_269_SEQ15_CORE_EVIDENCE.md`](HAMCP_269_SEQ15_CORE_EVIDENCE.md) (row
  `helper_options`).

## 3. Signed admission: base versus sequences 15, 16 and 17

The compiled fingerprint at the base was recomputed in a local venv
(`profiles.CORE_RUNTIME_CAPABILITY_PROFILES`, 32 profiles):
`core.config_entry_helper_options_operation`, adapter
`compiled-core-config-entry-helper-options-operation-v1`,
**`sha256:ce2765dc5caf616ae199fdccf64ce6ec68e1386bb0949099d1896bcb50dd98d8`**
(contract `E/ha_core_readmission/profiles.py:640-681`). This is the value the
architect recorded at the merged beta.41 head (register note, 2026-10-27).

Signed journal, `registry` tip `e486028e` (decoded locally):

| Seq | Generated (UTC) / expires | Core entry: capability count | Helper options reference |
|---|---|---|---|
| 14 | 2026-10-25 / 2027-01-23 | 10.0: 22, 10.1: 26, 10.3: 26 | absent |
| 15 | 2026-11-01 / 2027-01-30 | 10.0: 22, 10.1: 26, **10.3: 31** | 10.3: `ce2765dc…` (added with input_select, script trace, dashboard metadata, scene configuration) |
| 16 | 2026-11-05 / 2027-02-03 | 10.0: 22, 10.1: 26, 10.3: 31, **11.0: 31** | 10.3 and 11.0: `ce2765dc…` |
| 17 | 2026-11-07 / 2027-02-05 | 10.0: 22, 10.1: 26, 10.3: 31, **11.0: 32** (adds `core.typed_helper_value_operation`) | 10.3 and 11.0: `ce2765dc…` |

**Result.** The compiled fingerprint at the base equals the signed reference
in sequences 15, 16 and 17. No profile drift is pending. Core 2026.10.1 is
deliberately not extended (OD-13), so a Core rollback to 10.1 withholds the
family and fails closed.

The row's blocker ("Signing seq 15 … not yet merged; capability shows
pending_signed_registry_admission on beta.43") and its Next Action ("After
seq 15 merges…") are **stale**. Sequence 15 is published, and the HAMCP-276
live acceptance ran with authority reported as `31/0, seq 15`.

Low drift in comments (no behavior impact; protected paths, not changed
here):

- `E/ha_core_readmission/profiles.py:633-638` still says "stays dormant
  until an owner-signed registry reference (planned sequence 15) selects it".
- `E/governance/helper_options_flows.py:11-14` cites only Core 2026.10.3 for
  the absence of an options-flow listing. §5 shows the same holds at
  2026.11.0.

## 4. What remains to meet the Outcome

**Nothing remains to meet the Outcome as written.** The Outcome names no
helper types. Every mechanism it lists exists at the base, is admitted by
owner-signed sequences 15-17 at the compiled fingerprint, and has live and
disposable execution evidence (§2.2, §3).

Two title-level items stay outside the shipped family. Both follow **Josh's
own decisions** and are not gaps against the Outcome:

1. **utility_meter (OD-2, deferred).** At 2026.11.0 the utility_meter options
   flow still offers only `source` (Required), `periodically_resetting`
   (Required) and `sensor_always_available` (Optional, default False)
   (`components/utility_meter/config_flow.py:63-76`, `OPTIONS_FLOW` `:122-124`,
   `options_flow_reloads = True` `:135`). Cycle, offset and tariffs are still
   not editable through options. OD-2's reason is unchanged.
   **For information only (not a design proposal)**, if Josh reverses OD-2:
   - **Catalog**: one `("utility_meter","init")` row with
     `periodically_resetting` and `sensor_always_available` editable and
     `source` pinned (the prior design §3.6 proposal).
   - **Domains**: `DOMAINS` gains `utility_meter`.
   - **Public schema**: unchanged, apart from the two new names in the
     `set`/`clear` enum.
   - **Profile**: the contract's `response_contract.domains` changes
     (`E/ha_core_readmission/profiles.py:657`), so the fingerprint changes.
     That needs a new owner-signed extension of every admitted Core entry,
     plus a new disposable evidence lane.

   This would add scope beyond the row as currently decided, so this recheck
   designs it no further. See §7.
2. **Template types with ActionSelector fields (OD-3, refused).** These are
   out of scope by decision.

## 5. Lost-open residual against the newest signed Core (2026.11.0)

The accepted residual: if the response to the options-flow open is lost,
Engineering never learns the flow id and cannot abort the flow. The ledger
records it as `possibly_open` and discloses it in health and task output
(`E/governance/helper_options_flows.py:1-17`, `:50`, `:62-66`). Tests:
`tests/test_hamcp232_options_flow_ledger.py:321`, `:336`, `:471` and
`tests/test_hamcp232_helper_options.py:1122`.

**Determination at Core 2026.11.0 (`74cf5a49`): there is still no
client-reachable enumeration of in-progress options flows.** The four
relevant files were fetched at the exact commit. Their sha256 values equal
the HAMCP-280 `release_sha256` values in
`docs/evidence/hamcp280/source-judgments.json`. The full source tarball at
the same commit was searched.

- The options views are `OptionManagerFlowIndexView` (POST start only) and
  `OptionManagerFlowResourceView` (GET, POST and DELETE by a known
  `flow_id`) (`components/config/config_entries.py:54-55`, `:277-317`). The
  base index view defines only `post`; there is no GET listing
  (`helpers/data_entry_flow.py:64-104`). Resource GET, POST and DELETE all
  need the flow id (`:106-142`).
- The WebSocket commands `config_entries/flow/progress` and
  `config_entries/flow/subscribe` iterate `hass.config_entries.flow` (config
  flows), not `hass.config_entries.options`
  (`components/config/config_entries.py:383-404`, `:406-485`). That matches
  the 2026.10.1 determination recorded in the register
  (`config_entries.py:384-404`/`:407-466`).
- `FlowManager.async_progress` (`data_entry_flow.py:281-285`) on
  `OptionsFlowManager` (`config_entries.py:4134`) is in-process Python only.
  No registered REST view or WebSocket command exposes it for options flows.
  The only `config_entries.options.async_get(...)` call sites (for example
  the template and statistics preview commands,
  `components/template/config_flow.py:1034`,
  `components/statistics/config_flow.py:212`) take a caller-supplied
  `flow_id`.

**Consequence.** The residual stands as accepted, with no change. It cannot
be closed with a Core read at the newest signed Core. Its severity is
unchanged: in memory only, no configuration change, discarded at the next
Home Assistant restart.

## 6. Recommendation for Josh (status not changed here)

**Recommend marking HAMCP-232 Completed.** The Outcome is met: built,
admitted by owner signing (seq 15 for 2026.10.3; seq 16/17 for 2026.11.0, all
at `ce2765dc…`), and live-accepted on beta.57. Also clear the stale seq-15
blocker. The decision is Josh's; this recheck leaves Status at Waiting.

Proposed Next Action text for the row: "Josh: decide Completed (recheck
docs/HAMCP_232_CURRENT_STATE_RECHECK.md: Outcome met; seq 15/16/17 admit
ce2765dc; lost-open residual unchanged at Core 2026.11.0). utility_meter stays
deferred per OD-2; file a separate row only if wanted."

## Signing impact

None. This recheck changes no Core profile. The compiled fingerprint at the
base equals the signed sequence 15/16/17 references (§3). Adding utility_meter
would change the profile, but it is not proposed here (§4, §7).

| Profile id | New or changed (from version X) | Core commands / contract added | Why | Fingerprint impact | Can it batch with other 2.6 profiles |
|---|---|---|---|---|---|
| None | n/a | n/a | No runtime or profile change | none | n/a |

## Negative reachability, acceptance, security

No new write, service path, tool, schema or provider route. Tool count and
registration are unchanged. The existing family's refusal and negative tests
are cited in §2.1, §2.2 and §5. The existing family's `never_sent` list stays
the security boundary (`E/governance/helper_options.py:71-78`;
`E/ha_core_readmission/profiles.py:666-674`). No live Home Assistant or
deployed endpoint was accessed. Upstream reads were limited to public Core
source at the exact commit `74cf5a49`.

2.5.0 overlap: none of the in-flight 2.5.0 PRs (#384-#387) touches a cited
Engineering file. #384 edits `docs/CORE_RELEASE_REGISTRY.md`; this document
cites it only by section heading, and the signed facts come from the
`registry` journal itself.

## 7. Open questions — pending Josh

1. **Status.** Accept the §6 recommendation to mark HAMCP-232 Completed?
2. **utility_meter.** Keep OD-2 (deferred), or open a separate row for a
   utility_meter increment? That increment needs a profile change and a new
   signing (§4).
