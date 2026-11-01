# HAMCP-232 + HAMCP-145 (input_select): governed edits to existing UI helpers and setting an input_select value — design

> **2026-11-01 scope update (HAMCP-269):** Josh authorized disposable evidence
> for all five compiled profiles on both Core 2026.10.1 and 2026.10.3.
> [The evidence task](HAMCP_269_SEQ15_CORE_EVIDENCE.md) supersedes the historical
> batch-placement and dormant-wiring statements below. Runtime and production
> registry authority remain unchanged; execution results are recorded in its PR.

Status: **Part 0 (signed-coverage gate) and Part 1 (design) complete; Josh's
decisions recorded in §0 (2026-10-26); Part 2 builds all three families
INACTIVE.** Nothing here authorizes a release, deployment, live Home Assistant
access, registry preparation or signing, or a change to existing approval
behavior. Each family refuses `pending_signed_registry_admission` until an
owner-signed registry sequence names its exact profile.

| Item | Value |
|---|---|
| Base | `main` `06a28134f219244f258ab26307c1dfcb9e5d18cb` (fetched 2026-10-26), Engineering `2.4.1-beta.37`, 68 static tools |
| Register | HAMCP-232 (audit HAAC-E016), HAMCP-145 (audit HAAC-E018), context HAMCP-222; new finding HAMCP-233 (§1.3) |
| Signed registry | `upstream-trust/ha-core-release-registry.json`, tip **sequence 14** (expires 2027-01-23): 2026.10.0 (22 capabilities), 2026.10.1 (26), 2026.10.3 (26) |
| Core source | release archives for 2026.10.1 (`abe1fdd7`) and 2026.10.3 (`1f214b84`) under `/var/tmp/hamcp222-src/`; sha256 of each archive equals the signed `source_archive_sha256` (`a4305046…81c4`, `57a15d2d…e4e9`). Every Core file cited below is byte-identical in 2026.10.1 and 2026.10.3 (`diff -rq` over `components/{config,template,filter,statistics,utility_meter,input_select,select,websocket_api,diagnostics}`, `config_entries.py`, `data_entry_flow.py`, `helpers/{data_entry_flow,schema_config_entry_flow,selector,json}.py`) |
| Paths | Core paths are relative to `homeassistant/` in 2026.10.1. `E/` = `hass_mcp_engineering_beta/ha_mcp_engineering/`; `P` = `E/ha_core_readmission/profiles.py`; `R` = `E/ha_core_readmission/routes.py` |

## 0. Owner decisions (Josh, 2026-10-26)

Recorded verbatim in substance from Josh's reply on draft PR #314. Under
[`ADR-022`](architecture/ADR-022-OWNER-AUTHORITATIVE-PRODUCT-DIRECTION.md)
these are product decisions; they do not turn an unverified fact into
evidence (the probatio 0.13.0 `suggested_value` question, §2.3, stays open
until the evidence lane proves it).

| # | Decision |
|---|---|
| OD-1 | **Yes.** Baseline and readback open and abort the options flow, **only** inside `core.config_entry_helper_options_operation`, **only** for the plan's exact config entries, and never as a general read. **This supersedes HAMCP-132 decision (i)** ("no options-flow open and abort", `docs/HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md:437`, `:449`) **for this narrow use only**; HAMCP-132's read surfaces are unchanged and no general options read exists |
| OD-2 | Increment 1 = **template (sensor, binary_sensor only), filter, statistics**, in **one shared profile with a closed domain list**. **utility_meter is DEFERRED** (cycle/tariffs are not editable through the options flow, and the editable fields are not a current need). §2.4's utility_meter row and §3.6's utility_meter row remain as research only |
| OD-3 | As §3.6: template types with ActionSelector fields are refused; `read_only` fields are **enforced by Engineering as pinned** (must equal the baseline) |
| OD-4 | As §3.7: always governed with exact-hash approval; 069B consumer disclosure; incomplete evidence → protected → elevated |
| OD-5 | 1–8 entries per plan, one helper domain per plan, non-atomic, stop on first failure |
| OD-6 | Rollback is a separately approved plan (§3.5) |
| OD-7 | **Accept** the residual concurrency window (§3.3); it is disclosed in the plan **and** in the receipt |
| OD-8 | Injected defaults are disclosed as separate `normalization_changes`, not refused |
| OD-9 | input_select: governed plan |
| OD-10 | New tool `create_input_select_option_plan`; `create_helper_state_plan`'s schema stays byte-identical |
| OD-11 | The **new domain-parameterized reader** with per-option trigger disclosure (§4.3) |
| OD-12 | input_select rollback: separately approved (§4.4) |
| OD-13 | **seq 15 = extend 2026.10.3** (the batch's profiles; Josh wrote "+2" before the script-trace addition below, so 26 → 28 as written, 26 → 29 if the script-trace profile joins the same step — **to be confirmed by Josh when the signing package is prepared**); **seq 16 = add 2026.11.x with all profiles**; **2026.10.1 is NOT extended** (the rollback slot stays at 26, so a Core rollback to 10.1 withholds the new families, fail-closed) |
| OD-14 | **Authorized:** the protected `.github/workflows/ci.yml` wiring for the dormant lanes, gated like sequence 13, with **no change to existing steps** |
| OD-15 | HAMCP-233 is decided separately; it is not a dependency |
| Addition | Include a **script-trace READ profile** in this batch: `trace/list` + `trace/get` for `domain: script`, sanitized through the shared sanitizer exactly like the automation trace reads, read-only, built inactive, with its own dormant lane in the same set (§4b) |

**Consequences recorded.** Static tools 68 → 72 (`create_helper_options_plan`,
`create_input_select_option_plan`, `list_script_traces`, `get_script_trace`);
F3 identity pins 15 → 17 (one options-submit identity, one input_select
identity); three new runtime-only profiles (§4b lists all three fingerprints);
no signed profile, F3 identity or fingerprint changes. Part 2 is the next
free beta (§6.5).

**Owner direction (Josh, from the brief).**
- **E016 / HAMCP-232:** change EXISTING UI-created helpers (template state
  template; filter; statistics; utility_meter options) through their
  config-entry OPTIONS FLOW.
- **E018 / HAMCP-145:** set an `input_select`'s value (for example the
  Summer/Winter season helper; F066 recovery).
- **Out of scope:** creating new helpers; integration administration
  (HAMCP-054).
- **Target:** built INACTIVE, ready for the next signing session together
  with the Core 2026.11 admission (expected 2026-11-04).

## 1. Part 0 — signed-coverage gate

### 1.1 Result

**Neither use is admitted.** Both need new sibling profiles. The one
unexpected finding (§1.3) is read-side reachability through a delegated tool.
It is not an admission of either use, so Part 1 continued.

| Command needed | Covered by signed seq 14? | Evidence |
|---|---|---|
| REST `POST /api/config/config_entries/options/flow` (start) | **No** | No signed profile names any options-flow route. `E/clients/paths.py:52-67` `ROUTE_TEMPLATES` is closed and has no `config_entries` route |
| REST `POST`/`DELETE /api/config/config_entries/options/flow/{flow_id}` (submit / abort) | **No** | Same |
| A read returning config-entry `options` | **No, and none exists in Core** | `config_entries.py:676-703` `as_json_fragment` has neither `data` nor `options`; there is no WS options-flow command (`components/config/config_entries.py:64-75`) |
| WS `config_entries/get_single` | Named only by the **unsigned-on-10.0, signed-on-10.1/10.3** availability profile's reads (`P:515`), for its own use | A new use under another profile is not admission (HAMCP-222 §1 rule) |
| WS `call_service` `input_select.select_option` | **No** | `core.typed_helper_operation` (`P:336`) is bound to one F3 identity, `("set_exact_input_boolean_state", "set_input_boolean_state")` (`R:32-34`); the only native `call_service` literal is `input_boolean` turn_on/turn_off (`E/governance/helper_state.py:19-25`, `:151-186`); generic `call_service` routes to the unavailable standard gateway (`E/providers/routing.py:165`) |
| `core.governed_configuration_operation` identities | **No** input_select or config-entry identity | closed list `R:35-65`; unknown identities map to `core.unsupported_f3_operation` (`R:710-751`), which no profile defines |

### 1.2 Fingerprints recomputed (stdlib sha256 over the canonical contract, `P:111-145`)

- 26 profiles compiled (`P:612-619`). 2026.10.1 and 2026.10.3: **26/26** signed
  references match compiled id, adapter and fingerprint. 2026.10.0: 22/22
  (the four seq-13 profiles are not signed for 10.0). No profile is signed but
  not compiled.
- Relevant: `core.typed_helper_operation` `sha256:7ae55a050860e86089756ef8d9104977c630e7c6e593bee2dbd46c6bd2535936`;
  `core.governed_configuration_operation` `sha256:d517fa73878f8dafea857b5306db750304d3c65522481898800cf86fb091f441`;
  `core.integration_inspection_metadata_read` `sha256:d10cb88811c65b4b45bed1941f1b8f920ac3eb217d05285f117d5ac23f09c36a`.
  Neither new family may change any of these.

### 1.3 Unexpected finding: delegated reads already open options flows (HAMCP-233)

Not an admission of HAMCP-232, but it bears directly on owner decision (i) of
HAMCP-132 ("no options-flow open and abort",
`docs/HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md:437`, `:449`).

- **`ha_search`** is routed to `core.delegated_device_effective_area`
  (`R:170`) with no argument restriction. In ha-mcp **8.5.0 and 8.6.0** its
  helper deep search calls `fetch_entry_options_with_status`
  (`src/ha_mcp/tools/smart_search/_deep.py:1695` in 8.5.0, `:1702` in 8.6.0).
  For each flow helper whose title does not already match, that function POSTs
  `/api/config/config_entries/options/flow`, reads the form's
  `suggested_value`s, and DELETEs the flow
  (`tools_integrations.py:205-275` in 8.5.0; `rest_client.py:1520-1582`). The
  8.4.3 path was reported by the research pass and not re-read here.
- **`ha_config_list_helpers`** (`R:147`, `core.basic_websocket_read`) can
  return `ConfigEntry.options` through the `ha_mcp_tools` custom component's
  helper list when that component is installed (research pass; not re-read).
- **No submit is reachable.** Neither tool can send
  `POST /options/flow/{flow_id}`. `ha_get_integration` is already constrained
  (`E/providers/upstream_platform_reads.py:269-298`, `:677-679`).
- **Treatment:** recorded as HAMCP-233 for a separate owner decision. This
  design does **not** depend on it and does not widen or reuse it.

### 1.4 New profiles needed

| Proposed profile | For | Names (writes ▲, reads ▽) |
|---|---|---|
| `core.config_entry_helper_options_operation` (one shared profile with a closed domain list; alternative: one per domain, OD-2) | HAMCP-232 | ▲ REST `POST /api/config/config_entries/options/flow/{flow_id}` (submit). ▽/flow-lifecycle: REST `POST /api/config/config_entries/options/flow` (open), `DELETE …/{flow_id}` (abort). ▽ WS `config_entries/get_single`, the entity registry reads that resolve `config_entry_id` ↔ entities, `GET /api/states/{entity_id}` |
| `core.typed_input_select_option_operation` | HAMCP-145 | ▲ WS `call_service` `input_select.select_option` `{option}`. ▽ `GET /api/states/{entity_id}` (state + `options` attribute) for baseline, pre-dispatch check and readback. Never sent: `set_options`, `select_next`, `select_previous`, `select_first`, `select_last`, `reload`, any other domain |

Both are NEW siblings. No signed profile, F3 identity table or fingerprint
changes. Shape template: `core.label_registry_operation` (`P:553-579`).

## 2. Core authority: how options flows work (2026.10.1 = 2026.10.3)

### 2.1 Transport and lifecycle

- **REST only, admin only.**
  - Start: `POST /api/config/config_entries/options/flow`, body
    `{"handler": "<entry_id>"}` (`components/config/config_entries.py:284-287`;
    `helpers/data_entry_flow.py:62-98`). UnknownHandler → 404.
  - Step: `POST …/options/flow/{flow_id}` (`components/config/config_entries.py:304-313`).
    Schema failure → **400** `{"errors": …}`, flow stays at the same step
    (`helpers/data_entry_flow.py:115-125`; `data_entry_flow.py:355-379`).
  - Abort: `DELETE …/options/flow/{flow_id}` (`helpers/data_entry_flow.py:131-138`).
    **Not admin-gated** (the resource view does not override `delete`), so any
    authenticated user can abort a flow whose id they know.
- **No WebSocket transport** for options flows. `config_entries/flow/progress`
  and `flow/subscribe` cover config flows only (`components/config/config_entries.py:383-485`).
- **In memory only.** `flow_id` is a random uuid (`data_entry_flow.py:314`);
  `_progress` is a dict (`:191`). **No timeout, no persistence.** A flow lives
  until finished, aborted, or Core restarts.
- **No exclusivity.** Several options flows for one entry may coexist (a UI
  user's and ours) (`config_entries.py:4067-4080`). Each deep-copies the entry
  options **when it starts** (`helpers/schema_config_entry_flow.py:449`).
- **Finishing replaces options wholesale with no compare-and-swap.**
  `async_finish_flow` → `async_update_entry(entry, options=result["data"])`
  (`config_entries.py:4100-4116`, `:2781-2796`). If nothing changed it returns
  False and nothing reloads. If changed: `modified_at` updates, the entry is
  saved, `config_entries/subscribe` notifies, and — all four helpers use
  `options_flow_reloads = True` (`filter/config_flow.py:251`,
  `statistics/config_flow.py:168`, `template/config_flow.py:813`,
  `utility_meter/config_flow.py:135`) — a reload is **scheduled in the
  background** (`config_entries.py:4117-4119`). None of the four registers an
  update listener, so the `ValueError` guard at `:4107-4111` does not trigger.
- **The final response does not contain the options.** The view strips `data`
  (`helpers/data_entry_flow.py:37-43`). A finished or aborted `flow_id` is
  removed (`data_entry_flow.py:571`); re-submitting it is 404.

### 2.2 Merge semantics (the central safety fact)

`_update_and_remove_omitted_optional_keys`
(`helpers/schema_config_entry_flow.py:204-224`):

1. `values = deepcopy(entry.options at flow start)`; `values.update(user_input)`.
2. Every `Optional` key of the step schema **absent** from `user_input` is
   **deleted**, unless its selector has `read_only`.
3. An omitted `Optional(…, default=X)` is **reset to X** (the schema injects
   the default before the merge).
4. Keys outside the step schema survive from the snapshot (`name`,
   `template_type`, utility_meter `cycle`, …).
5. `read_only` is a selector flag only; Core does not stop an API client from
   **changing** a "read-only" field it submits (filter/statistics `entity_id`,
   `filter`, `state_characteristic`).

Consequences: a blind partial submit deletes or resets fields; the last
finisher wins over a snapshot taken when its flow started; read-only must be
enforced by Engineering.

### 2.3 Reading the current options

Core has **no read of `options`**. The available mechanisms:

| Mechanism | Covers | Status |
|---|---|---|
| (a) Open the options flow, read each field's `description.suggested_value`, abort | All four types; only fields in the current step schema | Suggested values default to the full stored options (`helpers/schema_config_entry_flow.py:263-268`, `data_entry_flow.py:668-703`). That the REST field list carries `suggested_value` was checked in probatio **0.11.4**; Core pins **0.13.0** (`requirements.txt:41`), which is not available locally. **Unverified for the pinned serializer** — the evidence lane must prove it (§6.4). ha-mcp relies on the same behavior in production (`tools_integrations.py` docstring, issue #1575), which is supporting observation, not proof |
| (b) `ha_mcp_tools` component command | All | Owner decision 2026-10-21: the component is not used, now or later (`E/configuration_definitions/contracts.py:19-22`) |
| (c) Diagnostics `GET /api/diagnostics/config_entry/{id}` | **utility_meter only** (`components/utility_meter/diagnostics.py:33-35`) | One type of four |
| (d) Entity state/attributes only | Indirect | Cannot prove options |

**Recommendation: (a), scoped** (OD-1): only inside the new write profile,
only for the plan's exact entries, never exposed as a general read tool. This
**supersedes HAMCP-132 decision (i) for this narrow use** and must be recorded
as such (ADR-022). The 132 side-effect notes are addressed: no requirement
installs for these four built-ins (`utility_meter` needs only `cronsim`,
already loaded with the integration); the template preview WS command is
registered once per process and is UI-only; no SQL `db_url` logging applies.

### 2.4 Per helper type

| Type | Options steps | Editable in options | Not editable / pinned | Validation and aborts | After finish |
|---|---|---|---|---|---|
| **template** | `init` (no schema) → the stored `template_type` step; 18 types, no menu (`template/config_flow.py:492-494`, `:692-781`) | sensor: `state` (Required), `unit_of_measurement`, `device_class`, `state_class`; binary_sensor: `state`, `device_class`; all: `device_id`, section `additional_options` {`availability`, + binary_sensor `delay_on`/`delay_off`} (`:162-164`, `:199-212`, `:375-397`, `:462-473`) | `name`, `template_type`; many other types carry **ActionSelector** fields (switch `turn_on`/`turn_off`, number `set_value`, alarm actions, …) — editing them changes what a later command **executes** | sensor unit and state-class checks raise a bare `probatio.Invalid` → **400 with no errors body** (`:517`, `:542`; `helpers/http.py:95-96`); no abort reasons | entry reload; entities recreated, several restore state |
| **filter** | `init` (no schema) → the stored `filter` step: lowpass, outlier, range, time_simple_moving_average, throttle, time_throttle (`filter/config_flow.py:63-65`, `:220-242`) | `precision`; per type `window_size`, `radius`, `time_constant`, `lower_bound`, `upper_bound`, `type` (time SMA) (`:105-192`) | `name`; `entity_id` and `filter` are read_only (not enforced) | int coercion; abort `already_configured` if another entry matches every key (`:68-86`) | reload; history re-read from the recorder (`filter/sensor.py:334-368`) |
| **statistics** | single `init` (`statistics/config_flow.py:152-157`) | `sampling_size`, `max_age`, `keep_last_sample`, `percentile`, `precision` (`:107-136`) | `name`; `entity_id`, `state_characteristic` read_only (not enforced) | errors `missing_max_age_or_sampling_size`, `missing_keep_last_sample`; abort `already_configured` (`:78-96`) | reload; buffer reloaded from the recorder (`statistics/sensor.py:773-774`) |
| **utility_meter** | single `init` (`utility_meter/config_flow.py:122-124`) | `source` (Required), `periodically_resetting` (Required), `sensor_always_available` (`:63-76`) | **cycle (`meter_type`), offset, tariffs, net_consumption, delta_values, name are NOT editable via options** (`:78-116`) | none in the options flow | reload; value restored (RestoreSensor); **changing `source` keeps the accumulated value** while switching source |

**Finding for OD-2:** if the owner's utility_meter need is the reset cycle or
tariffs, the options flow cannot do it. Only source/reset-detection/
availability are editable.

## 3. HAMCP-232 design: governed options edit

### 3.1 Tool and input

New sibling tool `create_helper_options_plan` (static tools 68 → 69 with
§4's tool, 70). Input:

```
{
  "changes": [                         # 1..8 (OD-5)
    {
      "entity_id": "sensor.living_room_filtered",   # a helper entity the user knows
      "set": {"window_size": 10},                   # only fields in the closed per-type allowlist (§3.6)
      "clear": []                                   # optional keys to remove, closed allowlist
    }
  ],
  "reason": "…"
}
```

- The entity resolves through the entity registry to exactly one
  `config_entry_id`; `config_entries/get_single` must report a domain in the
  closed set, `supports_options: true`, `state: loaded`, not disabled.
  Anything else refuses before any flow is opened.
- One change per config entry; duplicate entries refuse.
- The plan never accepts raw `user_input`; Engineering builds the full step
  dict.

### 3.2 Plan creation: exact baseline and exact proposal

Inside the service-layer admission + planning lease (§6.1):

1. `get_single` → `entry_id`, `domain`, `title`, `state`, `modified_at`.
2. Open the options flow (`POST …/options/flow`). Require `type: form`, the
   expected `step_id` (template: the stored template type; filter: the stored
   filter type; statistics/utility_meter: `init`), and a field list whose
   names equal the compiled per-type schema **exactly** (an unknown or missing
   field = schema drift → refuse `options_schema_unrecognized`).
3. Read every field's `suggested_value` → **visible baseline**. A field with no
   suggested value is recorded as absent.
4. **Always abort** (`DELETE`) in a `finally`. A failed abort is recorded as an
   orphan flow (in memory only; disappears at the next Core restart) and
   disclosed; it does not make the plan succeed.
5. Compute the **exact proposed step dict**: baseline ⊕ `set` ⊖ `clear`, then
   apply the compiled schema's defaults exactly as Core will
   (§2.2 rule 3). Every resulting difference from the baseline that the user
   did not request (for example a default injected where the key was absent)
   is listed separately as `normalization_changes` and disclosed.
6. Refuse a **no-op** (`already_desired`), any change to a pinned field
   (§3.6), any type with an ActionSelector field outside increment 1, and any
   value outside the compiled field type (numbers, durations, booleans,
   strings ≤ bounded length; template strings parsed by Engineering's template
   analyzer — an unparseable template refuses).
7. The plan stores: `entry_id`, `domain`, `step_id`, `modified_at`, the
   visible baseline, the full proposed step dict, `normalization_changes`, the
   resolved entity ids, the consumer disclosure (§3.7), and their fingerprints.
   Plan hash covers all of them.

Hidden keys (outside the step schema) are neither visible nor submitted; Core
carries them from the flow's snapshot. The baseline fingerprint therefore
binds **visible fields + `modified_at`** (any change to the entry, visible or
not, moves `modified_at`, `config_entries.py:2793`).

### 3.3 Dispatch: one submit, guarded

At apply, each child runs, with every read inside its pre-dispatch authority
window (beta.36 `_predispatch_read_window`):

1. **Admission** re-check (dispatch gate, §6.1).
2. **Open** a fresh flow (snapshot taken now).
3. **Drift checks, all must hold, else abort the flow and refuse
   `stale_target_state`:** `step_id` and field names unchanged; every
   `suggested_value` equals the plan's visible baseline; then `get_single`
   reports the same `entry_id`/`domain`, `state: loaded`, and
   **`modified_at` equal to the plan's**. Because `modified_at` is read
   *after* the flow opened, an unchanged value proves the flow's snapshot is
   the planned baseline.
4. **Guard commit**, then **submit exactly once**: `POST …/{flow_id}` with the
   full proposed step dict (pinned read-only fields included at their baseline
   values). This POST is the single irreversible dispatch.
5. Outcomes:
   - `type: create_entry` → proceed to readback.
   - `type: form` with `errors` (validation, for example statistics
     `missing_max_age_or_sampling_size`), or 400 → **not applied**; abort the
     flow; readback still runs to prove options equal the baseline.
   - `type: abort` (`already_configured`) → not applied; readback confirms.
   - 404 on submit → the flow no longer existed (aborted by someone else, or
     Core restarted) → not applied; readback confirms.
   - Timeout / connection loss after the send → **uncertain**; no redispatch;
     readback decides.

Residual race (disclosed, not closable with Core's API): a concurrent update
that lands between the step-3 `get_single` and the step-4 submit (one round
trip) is overwritten; a UI user's flow opened **before** ours and finished
**after** ours overwrites ours with their older snapshot. Core offers no
compare-and-swap.

### 3.4 Readback

1. Poll `get_single` (bounded, for example 30 s) until `modified_at` has moved
   past the plan's value and `state` is `loaded` again (the reload is
   asynchronous). `setup_error`/`setup_retry`/`not_loaded` after the deadline
   → `applied_entry_unhealthy` (manual review; rollback offered).
2. Open a new flow, read the suggested values, abort. They must equal the
   proposed step dict exactly → `succeeded_verified`. Equal to the baseline →
   `not_applied`. Anything else → `verification_mismatch` (manual review, no
   redispatch).
3. Entities: every entity of the entry still exists in the registry. Their
   state is reported (for example a template sensor's new value) but
   `unavailable` is not by itself a failure (a filter's source may be
   unavailable). For template sensors, the receipt includes Engineering's
   render of the new template at plan time (`core.template_semantics`, already
   signed) as disclosure only.

### 3.5 Rollback

A **separately approved** rollback plan (`rollback_change`, the 069B pattern
`E/f3_runtime/runtime.py:4600-4660`, `:5068+`):

- available only for a child that was dispatched once and exactly verified;
- built only while the live visible options still equal the verified
  readback and `modified_at` still equals the post-apply value (otherwise
  refuse `rollback_baseline_drift`);
- re-applies the captured visible baseline step dict through the same
  open/check/submit/readback sequence;
- carries the same risk evaluation as a forward plan. Hidden keys are
  untouched in both directions. Accumulated state that a reload re-derives
  (statistics buffer, filter history, utility_meter value under a changed
  source) is not "rolled back" and is disclosed.

### 3.6 Increment-1 scope (proposal, OD-2/OD-3)

| Type | Editable | Pinned (must equal baseline) | Refused entirely |
|---|---|---|---|
| template **sensor** | `state`, `unit_of_measurement`, `device_class`, `state_class`, `additional_options.availability` | `device_id` | — |
| template **binary_sensor** | `state`, `device_class`, `additional_options.{availability, delay_on, delay_off}` | `device_id` | — |
| template, any other type | — | — | every type with ActionSelector fields (switch, button, number, select, light, fan, cover, lock, alarm_control_panel, climate, vacuum, update, …) and image/weather/event/device_tracker: later increments |
| **filter** | `precision` and the stored type's parameters | `entity_id`, `filter` | changing the filter type |
| **statistics** | `sampling_size`, `max_age`, `keep_last_sample`, `percentile`, `precision` | `entity_id`, `state_characteristic` | — |
| **utility_meter** | `periodically_resetting`, `sensor_always_available` | `source` (proposal: pinned in increment 1 because the accumulated value carries over to a new source) | cycle/tariffs/offset: not possible via options at all |

Template `state`/`availability` values are templates. Templates cannot call
services, so they add no write reachability; ActionSelector fields would, and
are therefore refused.

### 3.7 Risk policy

- **Always a governed plan** with exact-hash external approval
  (`E/governance/service.py:7769`, checks `:7839`, `:8637`); no fast path.
- **Consumer disclosure** for every entity of the entry, reusing the HAMCP-069B
  entity-level disclosure (`E/governance/entity_metadata_disclosure.py:95`,
  `E/governance/entity_availability_exposure_planning.py:69-73`,
  `MAX_DISCLOSED_CONSUMERS = 16`): automations and scripts that reference the
  entity, with `UNAVAILABLE_COVERAGE = ("dashboards", "groups", "scenes",
  "templates")` disclosed as unavailable, never as "no consumers".
- **OD-4 (HAMCP-069B):** `dependency_evidence_gaps` (`:225`) is the single
  completeness predicate; incomplete evidence → **protected** → elevated
  approval. The dependency index has no config-entry helper source
  (`E/dependency/provider.py:1788-1797`), so template/helper-chain coverage is
  always unavailable. **Expected in practice: every options edit is elevated**,
  as production deletes and disables already are (HAMCP-222 canaries). That is
  a disclosure, not a refusal: an exact owner-approved edit stays actionable.
- **Protected objects:** the existing `object_protection` union (`:189`) and
  `E/governance/risk.py:37-51` apply to the helper's entities and to disclosed
  consumers; a protected consumer → elevated.
- **Template-specific disclosure:** entities newly referenced by the proposed
  `state`/`availability` template and those no longer referenced (from
  Engineering's template analyzer), plus the plan-time render.
- **Bounds:** 1–8 entries per plan, one helper domain per plan (proposal),
  non-atomic, stop on first failure, no automatic rollback (the existing
  multi-operation rule).

### 3.8 Lifecycle safety summary

| Situation | Behavior |
|---|---|
| Flow opened, then any refusal before the guard | abort in `finally`; nothing written; orphan disclosed if abort fails (no persistence, gone at restart) |
| Validation error / abort on submit | not applied; flow aborted; readback confirms baseline |
| Core restart mid-flow | flow lost; submit 404 → not applied; readback confirms |
| Response lost after submit | uncertain; never redispatched; readback decides |
| Human edits in the UI between plan and apply | `modified_at`/suggested-value drift → `stale_target_state`, no submit |
| Human UI flow concurrent with ours | residual (§3.3); disclosed in the plan and receipt |
| Same plan applied twice / resumed / recovered | F3 one-dispatch guard; recovery is read-only, no redispatch |
| Entry fails to reload after apply | `applied_entry_unhealthy`; rollback plan offered |

## 4. HAMCP-145 design: set an input_select value

### 4.1 Shape (OD-9, OD-10)

- **Governed plan, not fast control.** The HAMCP-195 fast path is deferred and
  its gate (complete consumer evidence) would rarely pass (§6 there).
- New sibling tool `create_input_select_option_plan`
  `{entity_id, option, reason}`. `create_helper_state_plan`'s public schema,
  `helper_state_provider_evidence`, the operational rows and the
  `helper-dependency-risk-v13` output stay **byte-identical**
  (`E/tools/governance.py:272-312`; `E/governance/helper_dependency.py:25`).
- New provider contract `direct-ha-exact-input-select-option-v1`, operation
  `set_exact_input_select_option`, new F3 identity
  `("set_exact_input_select_option", "set_input_select_option")` in a new `R`
  table mapped to `core.typed_input_select_option_operation` +
  `core.f3_mutation_verification`. Do **not** add it to
  `_F3_TYPED_HELPER_OPERATIONS` (that would admit it under signed authority).
- Entity pattern `^input_select\.[a-z0-9_]{1,114}$`; both UI and YAML
  input_selects (same class and service, `components/input_select/__init__.py:276-289`).

### 4.2 Plan, dispatch, readback

- **Plan:** `GET /api/states/{entity_id}` → state and `options` attribute.
  Refuse if the entity is missing/unavailable, if `option` is not **exactly**
  (case-sensitive) in `options` (`option_not_available`), or if it equals the
  current state (`already_desired`: Core would write an identical state with no
  `state_changed` event). Baseline = current option + options list
  (fingerprinted).
- **Dispatch:** pre-dispatch re-read inside the authority window; refuse
  `stale_target_state` if the current option or the options list changed.
  Then exactly one `call_service input_select.select_option {option}`.
  An option removed in between makes Core raise `HomeAssistantError`
  (`home_assistant_error`, `:310-319`; `components/websocket_api/commands.py:335-347`),
  which is treated as "readback decides", never as proof of failure.
- **Readback:** `GET /api/states/{entity_id}` state == `option` →
  `succeeded_verified`. An automation that changes the helper again
  immediately produces `verification_mismatch` (disclosed, no redispatch).
- **Persistence disclosure:** a helper with an `initial` value reverts to it
  at the next restart (`:268`, `:291-302`); Engineering cannot read `initial`
  for YAML helpers, so this is disclosed as unknown.

### 4.3 Consumer impact (OD-11)

The season helper drives automations, so changing it is a causal trigger.

- **Proposal:** a new domain-parameterized reader beside
  `build_helper_dependency_risk_binding` (`E/governance/helper_dependency.py:1275`)
  for `input_select`, emitting its own model id (for example
  `helper-dependency-risk-input-select-v1`), leaving v13 untouched. It reuses
  the consequence profiles (`E/governance/risk.py:1531`): consumer automations
  whose triggers/conditions/templates read the helper, with — where
  statically resolvable — the option values they trigger on (`to:`/`from:`),
  so the plan can say "selecting Winter will trigger automation X".
- Incomplete evidence (scripts not followed, templates/dashboards
  unavailable, opaque templates) → protected → elevated (OD-4). Protected or
  safety-critical consumers → elevated. Never a refusal on consequence alone.
- **Alternative (cheaper):** the 069B entity-level disclosure only (who
  references the helper; no per-option or consequence profile).

### 4.4 Rollback (OD-12)

Proposal: a separately approved rollback plan selecting the captured prior
option, only while the live state equals the verified readback and the prior
option is still in the list. This is what the "flip and back" canary uses.

## 4b. Script-trace read profile (owner addition, 2026-10-26)

**Purpose.** Read the run traces of one exact script, the same way the
automation trace reads already work, so a client can debug a script without a
second connector.

**Core authority** (`homeassistant/components/trace/websocket_api.py`,
identical in 2026.10.0/.1/.3): `trace/list {domain, item_id?}` returns a list
of trace-header mappings; `trace/get {domain, item_id, run_id}` returns one
trace mapping or the error `{code: "not_found", message: "The trace could not
be found"}`. A script's `item_id` is its configuration key
(`homeassistant/components/script/__init__.py`, `unique_id = key`).

**Profile** `core.script_trace_read` (runtime-only, read-only, no
pre-dispatch requirements), adapter `compiled-core-script-trace-read-v1`,
checks `script_trace_list_command_contract`,
`script_trace_list_bounded_mapping_sequence`,
`script_trace_get_command_contract`, `script_trace_get_not_found_contract`.

**Tools.** `list_script_traces(script_id)` and
`get_script_trace(script_id, run_id)`, both requiring
`core.script_trace_read`.

- `item_id` is always the exact, validated script key; the list-all form
  (no `item_id`) is never sent.
- Every result passes through the shared untrusted-data sanitizer (known
  secrets redacted, bounded strings) **before** output, exactly as the
  automation trace reads in `trace_normalization.py` do; both call sites are
  registered in the HAMCP-191 sanitizer-site inventory.
- Closed vocabulary: only `trace/list` and `trace/get` with
  `domain: "script"`. Never sent: `trace/contexts`, every `trace/debug/*`
  command (breakpoints, continue, step, stop).
- Not-found is reported as not-found (no fallback to delegated trace tools).
- Until a signed sequence names the profile, both tools refuse
  `pending_signed_registry_admission` before any Home Assistant request.

**Fingerprints (runtime-only profiles, computed from this branch's
`profiles.py`; final values are re-verified at the review head):**

| Profile | sha256 |
|---|---|
| `core.config_entry_helper_options_operation` | `ce2765dc5caf616ae199fdccf64ce6ec68e1386bb0949099d1896bcb50dd98d8` |
| `core.typed_input_select_option_operation` | `e2240555fb35042e47e7ee7afb7f5552977d019a27058fb2c2c5802674fee38d` |
| `core.script_trace_read` | `749904fe40b13db4289301d24b958836fc72c810829611729d8c26080f06ca03` |

## 5. Signing batch shape (November)

- Three new profiles (two write, one read; §4b). Each changed release entry
  is its own sequence step (`scripts/prepare_core_release_registry.py:165-185`).
- **Decided (OD-13):** seq 15 extends **2026.10.3** with the batch's profiles
  (26 → 29 including the script-trace read; the exact set is confirmed by Josh
  when the signing package is prepared); seq 16 adds **2026.11.x** with all
  profiles after its evidence run. **2026.10.1 is not extended** and stays at
  26, so a Core rollback to 10.1 withholds these families (fail-closed, like
  10.0 today).
- 071/106 (HAMCP-222 deferrals) can join only if built and frozen before the
  evidence run (HAMCP-222 §6.3: freeze before the run).

## 6. Part 2 preview: build inactive (after decisions)

### 6.1 Lessons built in from the start (069B, 165, 155, beta.36)

1. **Admission first at the service layer** (before any read, validation read
   or persistence) on every entry point, including the lowest builder and
   generic `create_configuration_plan` routing; re-checked at the persist
   boundary through `_before_persist` (`E/governance/service.py:5603-5639`,
   `:6562`). Refusal `pending_signed_registry_admission`,
   `plan_created=False`, zero provider reads, nothing stored
   (`E/routing.py:393-425`).
2. **Dispatch admission on every F3 path** — first apply, retained approved
   plan, resume, recovery, read-only reconciliation; held verification stays
   held; no Core → `core_authority_unavailable`
   (`E/f3_runtime/runtime.py:824-955`). input_select is an **operational**
   plan (single target), which `_unsigned_profiles` does not cover today
   (it keys on `plan.operations` resource types): Part 2 adds the operational
   admission seam, with a test proving input_boolean is unchanged.
3. **F3 entry refusal before `_initialize`** (`runtime.py:3862-3917`); stores
   byte-identical after refusal.
4. **Drift refused at dispatch** with `stale_target_state` (§3.3, §4.2).
5. **Pre-dispatch reads inside their own authority window**
   (`_predispatch_read_window`, `PREDISPATCH_READS_HOME_ASSISTANT = True`);
   the options flow open/abort counts as a pre-dispatch read.
6. **Authority-enforcing fakes only** (`tests/core_authority_fakes.py`
   `require_core_authority`); the fake options-flow server implements §2.2's
   merge, defaults, read_only non-enforcement, 400/404, in-memory flows and
   asynchronous reload, so tests cannot pass against a kinder Core.
7. **Fixed vocabularies:** each gateway has a closed command list plus a
   never-sent list, refuses anything else before I/O, and calls
   `mark_provider_send()` at the exact send boundary; an error result is never
   proof of failure.
8. **All pins updated in the same PR:** static tool count (68 → 72), catalog,
   capability metadata, F3 identity pins (15 → 17), profile pins, allowlists,
   `ROUTE_TEMPLATES` (three new options-flow templates, method-bound).
9. **Mutation evidence** for each new gateway and policy rule, memory-capped.

### 6.2 New routes (method-bound)

`POST /config/config_entries/options/flow`,
`POST /config/config_entries/options/flow/{}`,
`DELETE /config/config_entries/options/flow/{}` — reachable **only** from the
new gateway, with the entry id from the plan and the flow id from Core's own
response (validated as 32 lowercase hex).

### 6.3 Test plan (offline)

Positive: each increment-1 type edits and verifies; rollback restores;
elevated plans with incomplete evidence stay actionable; input_select flip and
back. Negative: pending admission at every entry and every F3 path (zero HA
requests, stores unchanged); unknown domain/type/field; ActionSelector type;
pinned field change; no-op; option not in list; drift (suggested value,
`modified_at`, options list, current option); validation error / abort / 404 /
timeout on submit (no redispatch); orphan abort; reload never completes;
readback mismatch; input_boolean path byte-identical; no other domain, no
`set_options`/relative moves; no fallback to delegated tools.

### 6.4 Dormant seq-15 evidence lanes

Behind the existing dormant switch pattern (`.github/workflows/ci.yml:363-365`,
`scripts/real_ha_contract_tests.py:3657-3680`; the `ci.yml` wiring is a
protected path; **authorized by OD-14**, dormant gating like seq 13, no change
to existing steps):

- **helper options lane:** the harness (under `scripts/`, own sender) creates
  disposable filter, statistics, template-sensor and template-binary_sensor
  helpers (utility_meter deferred, OD-2) via
  their config flows; then through Engineering: edit + verify + rollback per
  type; refusals recorded (not aborted): drift injected between plan and apply,
  pinned-field change, validation error, concurrent flow; **proves the
  pinned probatio 0.13.0 serializer carries `suggested_value`** (§2.3), else
  the family stays unsigned; asserts every reviewed read and every never-sent
  command; predecessor registry (seq 14) refuses with zero HA requests.
- **input_select lane:** flip + verify + rollback; option not in list refused
  pre-dispatch; options changed between plan and apply refused; predecessor
  refusal with zero HA requests.
- **script-trace lane:** the harness creates and runs a disposable script,
  then through Engineering lists and gets its traces; asserts the sanitized
  shape, the not-found contract for an unknown run id, that only
  `trace/list`/`trace/get` with `domain: script` were sent, and predecessor
  refusal with zero HA requests.

### 6.5 Release

Next free beta **2.4.1-beta.38** (staged `.release/next-version` +
`docs/V2_4_1_BETA38_{ACCEPTANCE,RELEASE_NOTES}.md`), declared as
"activates nothing new": all three families refuse
`pending_signed_registry_admission` until a signed sequence names them.

### 6.6 Post-signing canaries (defined now, run only after signing)

1. Edit a harmless filter helper's `window_size`, verify, then roll back to
   the captured options; verify.
2. Select a different option on a harmless input_select, verify, then roll
   back; verify.

## 7. What stays unchanged

All signed profiles and fingerprints; `create_helper_state_plan` and the
input_boolean path; HAMCP-132's read surfaces (no general options read);
delegated tool routing (HAMCP-233 is separate); stable v1.

## 8. Owner decisions requested (Josh) — answered 2026-10-26, see §0

Kept as the record of what was asked.

| # | Decision | Recommendation |
|---|---|---|
| OD-1 | Read baseline/readback by opening and aborting the options flow, scoped to the plan's entries inside the new write profile (supersedes HAMCP-132 decision (i) for this use only) | **Yes.** Without it there is no exact baseline or readback (§2.3), and the family cannot meet its contract |
| OD-2 | Helper types in increment 1; one shared profile or one per domain | template (sensor, binary_sensor only), filter, statistics; utility_meter only if `periodically_resetting`/`sensor_always_available` (or `source`) is a real need — **cycle/tariffs are not editable via options**. One shared profile with a closed domain list |
| OD-3 | Per-type editable/pinned fields (§3.6), incl. utility_meter `source` | As §3.6: ActionSelector template types refused; `source` pinned in increment 1 |
| OD-4 | Risk policy: always governed + exact-hash approval; 069B consumer disclosure; incomplete → protected → elevated (expected for nearly every edit) | As §3.7 |
| OD-5 | Bounds | 1–8 entries per plan, one domain per plan, non-atomic |
| OD-6 | Rollback for 232 | Separately approved rollback plan (§3.5) |
| OD-7 | Accept the residual concurrency window Core cannot close (§3.3) | Accept, disclosed in plan and receipt |
| OD-8 | Normalization (defaults Core injects) shown as separate disclosed changes rather than refused | Disclose |
| OD-9 | input_select: governed plan vs fast control | Governed plan |
| OD-10 | New sibling tool vs extending `create_helper_state_plan` | New tool `create_input_select_option_plan` (keeps the existing public schema byte-identical) |
| OD-11 | input_select consumer analysis | New domain-parameterized reader with per-option trigger disclosure (§4.3); alternative: entity-level disclosure only |
| OD-12 | input_select rollback | Yes, separately approved (§4.4) |
| OD-13 | Which release entries get the new profiles | seq 15 = 2026.10.3 (+2), seq 16 = 2026.11.x; 2026.10.1 optional |
| OD-14 | Authorize the protected `ci.yml` wiring for the two dormant lanes | Needed for the evidence run |
| OD-15 | HAMCP-233 (delegated `ha_search` options-flow probing) | Decide separately; not a dependency |

## 9. Non-actions in Parts 0–1

No runtime, profile, registry, workflow, version or test change; no live Home
Assistant or deployed endpoint accessed; no secret read; no registry prepared
or signed; no release, tag or deployment. Validation run: registry parse,
archive sha256 checks, fingerprint recompute (26/26 on 10.1 and 10.3, 22/22
on 10.0), and static source reads. No test suite was run (docs only).
