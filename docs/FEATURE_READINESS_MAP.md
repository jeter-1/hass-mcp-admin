# Feature Readiness Map

HAMCP-266. This map rates each candidate feature row against current source,
using the gaps (`RCG-NNN`) in [`READ_COVERAGE_MATRIX.md`](READ_COVERAGE_MATRIX.md)
(HAMCP-265). The guard test `tests/test_read_coverage_matrix.py` checks that
every gap id cited here exists in the matrix.

This map is decision support. Josh and the architect decide priority, scope and
order ([`ADR-022`](architecture/ADR-022-OWNER-AUTHORITATIVE-PRODUCT-DIRECTION.md)).
Scope comes from the register row of each task. Where a row's scope is unclear,
the map says so and does not fill it in. It grants no authority to sign,
admit, implement or deploy anything.

## Verdicts

| Verdict | Meaning |
|---|---|
| ready | Every read and write the row needs exists behind current admission; the work is Engineering code, tests and docs. |
| needs signing | Needs one or more new or revised compiled Core profiles, signed in a registry sequence, before it can run. |
| needs admission | Needs a reviewed `ha-mcp` policy or registry change that admits an upstream tool. |
| needs decision | Needs an owner decision (scope, privacy, design options) before design or implementation can start. |
| blocked | Waits on another task, an external party or an owner deferral. |

Effort uses the register's size where one is given (S, M, L). Otherwise it is
an estimate for the Engineering work only, excluding signing and live
acceptance.

## Common limits

These apply to every row and are not repeated:

- Deployed Core and `ha-mcp` versions cannot be determined from source. Each
  "exists" below means "compiled and admitted for the reviewed versions".
- The signed Core registry is disabled by default
  (`ha_core_release_registry_enabled: false`). Runtime-only profiles,
  including scene, label, entity-registry, helper-options and script-trace
  profiles, run only where it is enabled and a signed envelope covers the
  observed Core release. Sequence 15 covers Core 2026.10.0, 2026.10.1 and
  2026.10.3. All 31 profiles are covered only on 2026.10.3.
- `ha-mcp` 8.6.0 is on HOLD (HAMCP-156). The YAML reader (`ha_config_get_yaml`)
  is admitted on 8.4.3 and 8.5.0 only (RCG-017). The lifecycle and backup
  providers are admitted on 7.14.1 to 8.1.1 only (RCG-082, RCG-085,
  RCG-091).

## Rows

### HAMCP-071 Part 3: governed scene CREATE

- **Data it must read:** stored scene configuration by config id, scene states
  and the entity registry, for an absence check on the new id and for exact
  readback. These reads exist under `core.scene_configuration_operation`.
  Consumer disclosure has membership only (RCG-006).
- **Write commands:** create a new config id with
  `POST /api/config/scene/config/<id>` (the endpoint that edits use), and
  recovery by deleting exactly that scene with
  `DELETE /api/config/scene/config/<id>`. The scene profile covers edit
  only. `core.configuration_delete_operation` covers automation and script
  only (RCG-004, RCG-005). `scene.create`, `scene.apply` and activation stay
  never-sent.
- **Readback:** exact stored configuration plus registry identity. Loaded
  (runtime) state cannot be verified today (RCG-007). The disclosure
  `scene_loaded_state_not_verified_disclosed` must carry over, the lesson
  from Part 2's option C.
- **Blockers:** the register records "a signed delete-profile decision"
  pending for Josh.
- **Effort:** M.
- **Verdict:** **needs decision**, then **needs signing**.

### HAMCP-253: inspect, then acknowledge integrity-failed plans and manual-review tasks

- **Data it must read:** Engineering's own plan and execution-task stores
  (`get_change_plan`, `list_execution_tasks`; see "Tools that read no Home
  Assistant data source directly" in the matrix). There is no Home Assistant
  read.
- **Write commands:** an acknowledgement record in the Engineering store
  only, with no Home Assistant write. It needs no Core profile or upstream
  admission.
- **Readback:** exact readback from the Engineering store. Loaded-state
  questions do not apply.
- **Blockers:** priority, and its place relative to 071, are Josh's to set.
  The integrity-preserving acknowledgement design (no rewrite of hash-bearing
  records) is a persistence boundary and needs a security section.
- **Effort:** M (estimate; the register gives none).
- **Verdict:** **ready**.

### HAMCP-231: Thread/Matter topology visibility

- **Data it must read:** border routers, Thread datasets and next-hop or path
  data, "where Home Assistant/OTBR/Matter Server expose it". No matrix source
  covers this. The nearest path is the radio-network section of the
  `ha_get_system_health` adapter, which is curated and partial (see the
  `system_health` source).
- **Write commands:** none (read-only outcome).
- **Readback:** not applicable.
- **Blockers:** scope is unclear. The register calls for "Investigation, then
  read PR". Which Core WebSocket commands, OTBR endpoints or Matter Server
  APIs exist, and whether each needs a signed Core profile or a new provider,
  is not established. Any non-Core provider (Matter Server) would be a new
  provider boundary.
- **Effort:** unknown until the investigation.
- **Verdict:** **needs decision** (investigation first).

### HAMCP-261: read-only query of the observability stack

- **Data it must read:** HAMCP-260's log and metrics stores, not Home
  Assistant. No matrix source applies. The Home Assistant log limits that
  this row would complement are RCG-065 to RCG-072.
- **Write commands:** none. It uses its own read-only credential.
- **Readback:** not applicable. Completeness and truncation must be reported.
- **Blockers:** HAMCP-260 does not exist yet. A new external provider is a
  security architecture change that needs a focused review.
- **Effort:** M.
- **Verdict:** **blocked** (HAMCP-260).

### HAMCP-262: optional loaded-state verification after scene and automation saves

- **Data it must read:** a reliable post-save reload signal correlated to the
  save. Engineering has no such read today (RCG-007).
- **Write commands:** none. If a signal exists, reading it probably needs an
  event subscription or equivalent Core command, which no profile covers.
  That would need a new signed read profile.
- **Readback:** this row is the readback question. Per the register, the
  signal must never be satisfiable by an activation, an earlier reload or an
  unrelated reload. Whether pinned Core offers such a signal is unproven,
  so loaded-state verification is not yet shown to be possible.
- **Blockers:** a feasibility finding (the register cites Codex round 3's
  informational note). It is sequenced after 071.
- **Effort:** M.
- **Verdict:** **needs decision** (feasibility). If feasible, it also
  **needs signing**.

### HAMCP-264: YAML-reader purpose for templates, packages and scripts

- **Data it must read:** YAML template entities, packages, `!include`
  targets and package scripts (RCG-001, RCG-009, RCG-013, RCG-014,
  RCG-016). These run through `ha_config_get_yaml`, which is admitted on
  ha-mcp 8.4.3 and 8.5.0 and held on 8.6.0 (RCG-017).
- **Write commands:** none.
- **Readback:** not applicable. Completeness must be truthful per source.
  UI template helpers stay unavailable (RCG-034), as the register states.
- **Blockers:** depends on 227, which has merged. A new reader purpose is a
  security-sensitive surface: it needs its own audit event, bounded paths
  and sizes, and `!secret` values never resolved.
- **Effort:** M.
- **Verdict:** **ready** on ha-mcp 8.4.3 or 8.5.0. It **needs admission**
  wherever 8.6.0 is observed (HAMCP-156).

### HAMCP-267: integrity, incident and handoff analyses use the reference store

- **Data it must read:** the HAMCP-227 diagnostic scene, group and dashboard
  reference store, which already exists. Its inputs are partial for large
  dashboards (RCG-045) and for scene membership (RCG-006).
- **Write commands:** none.
- **Readback:** not applicable. Partial sources must be stated.
- **Blockers:** none (227 has merged).
- **Effort:** S.
- **Verdict:** **ready**.

### HAMCP-270: governed plans disclose scene, group and dashboard references

- **Data it must read:** the same HAMCP-227 store. The same partial inputs
  apply (RCG-045, RCG-006).
- **Write commands:** none new. It changes disclosure only: no fingerprint
  change, and never a risk downgrade.
- **Readback:** not applicable.
- **Blockers:** none (227 has merged).
- **Effort:** S.
- **Verdict:** **ready**.

### HAMCP-149: pane-approval completion signaling and client continuation

- **Data it must read:** Engineering plan state only (`get_change_plan`).
  There is no Home Assistant read.
- **Write commands:** none.
- **Readback:** bounded authoritative plan-state checks.
- **Blockers:** whether the client supports completion notifications and
  continuation is unproven (register blocker). The next step is a
  non-executing compatibility fixture.
- **Effort:** S for the fixture. Implementation is unknown until the fixture
  reports.
- **Verdict:** **ready** for the fixture. Implementation **needs decision**
  after it.

### HAMCP-109: dependency-bound multi-operation execution

- **Data it must read:** it depends on the operation set chosen. Scope is
  unclear: the register says no multi-operation task has been selected.
- **Write commands:** unknown until a task is selected. Each operation would
  reuse its own existing or new profile.
- **Readback:** per-stage verification. The limits are those of each step's
  source.
- **Blockers:** owner selection of a concrete task and a reframed proposal.
- **Effort:** L.
- **Verdict:** **needs decision**.

### HAMCP-134: bounded security and effective-permission metadata reads

- **Data it must read:** HA user roles, MFA and session policy flags, voice and
  tablet authority, and ingress, proxy or TLS boundaries. No matrix source
  covers these. Only exposure flags are readable today (the `entity_registry`
  source).
- **Write commands:** none.
- **Readback:** not applicable.
- **Blockers:** a feasibility and privacy review comes first (register).
  The data may need a new signed read profile or may be impossible through
  the HA API; this is not established.
- **Effort:** unknown (feasibility first).
- **Verdict:** **needs decision**.

### HAMCP-195: typed helper-toggle fast path

- **Data it must read:** input_boolean state and dependency analysis of the
  helper's consumers. Both exist; consumer completeness is limited by
  RCG-013 (template references) and RCG-002 (script inventory).
- **Write commands:** `input_boolean.turn_on` and `turn_off` under connector
  authority. The governed plan path exists (`create_helper_state_plan`, F3
  verification under `core.typed_helper_operation`). A connector-authority
  typed path is a new execution route, which is a security architecture
  change.
- **Readback:** exact state readback.
- **Blockers:** owner decisions OD-A to OD-J on the PR #299 design.
  The row is Deferred.
- **Effort:** M.
- **Verdict:** **needs decision**.

### HAMCP-054: governed integration administration

- **Data it must read:** the installed custom integration's config entry and
  update state. The config-entry inspection reader covers one adapter only
  (RCG-061), and update inventory is unavailable (RCG-089).
- **Write commands:** integration update, repair or replacement. No write path
  exists (RCG-090).
- **Readback:** not established.
- **Blockers:** Deferred. The concrete defect is upstream, and no corrected
  package is selected (register).
- **Effort:** L.
- **Verdict:** **blocked**.

### HAMCP-151: governed add-on restart provider contract

- **Data it must read:** the add-on inventory (`ha_get_app`, 8.4.1 and
  later) and its own add-on identity. Both exist.
- **Write commands:** add-on restart through the lifecycle provider. It is
  admitted on ha-mcp 7.14.1 to 8.1.1 and held on 8.2.0 and later; the
  provider hard-codes pre-8.4.1 tool names (RCG-082). This is a Supervisor
  matter; it needs no Core signing.
- **Readback:** add-on state after restart.
- **Blockers:** Deferred by owner; reopened only by an explicit owner
  decision.
- **Effort:** M.
- **Verdict:** **blocked** (owner deferral). If reopened, it **needs
  admission**.

### HAMCP-156: capability-based compatibility policy for routine updates

- **Data it must read:** none new. It changes how upstream tools are
  admitted. That affects every held path: RCG-017, RCG-052, RCG-082,
  RCG-085 and RCG-091.
- **Write commands:** none new.
- **Readback:** not applicable.
- **Blockers:** design discussion only. Josh has not authorized implementation
  or lifting the HOLD.
- **Effort:** L.
- **Verdict:** **needs decision**.

### HAMCP-104: precise stale-state preflight diagnostics

- **Data it must read:** none new. It uses the existing stale-state checks.
- **Write commands:** none.
- **Readback:** not applicable. It changes refusal diagnostics only, with
  zero-attempt behavior preserved.
- **Blockers:** owner selection. Current behavior must first be revalidated
  (register).
- **Effort:** S.
- **Verdict:** **ready** (if selected).

### Candidate (no register row): governed helper CREATE beyond input_boolean and input_number

This row is a candidate, not approved scope. It is listed because the
matrix's largest helper gap is here.

- **Data it must read:** absence checks and exact readback of the helper
  definition. Storage helpers are listed internally through `{type}/list`,
  which is already used for input_boolean and input_number. UI template,
  filter and statistics helpers can be read only through an options flow
  (RCG-034).
- **Write commands:**
  - storage collections: `input_select/create|update`,
    `input_text/create|update`, `input_datetime/...`, `input_button/...`,
    `counter/...`, `timer/...`, `schedule/...` (RCG-025, RCG-030);
  - config-flow create for template, filter and statistics (RCG-036), and
    for the other config-entry helpers (RCG-042).
  - Recovery of a create is a delete: `{type}/delete` for storage
    collections (RCG-023, RCG-029, RCG-033), or config-entry removal
    (RCG-038). This is the same signed delete-profile decision as 071
    Part 3.
- **Readback:** exact stored-definition readback is possible for storage
  collections. For config-flow helpers, readback runs through the options
  flow, which OD-1 restricts.
- **Blockers:** owner scope (which types). The HAMCP-232 OD-1 position on
  options reads. HAMCP-276 should land first, so that the existing options
  apply path is proven (RCG-035).
- **Effort:** M for the storage-collection types; L for config-flow types.
- **Verdict:** **needs decision**, then **needs signing**.

## Related fix-lane items

These are not feature rows. They change the readiness of the rows above:

- **HAMCP-276** (Ready, Small, High): options apply is refused at
  `dispatch_authority` (RCG-035). Until it lands, the existing helper-options
  profile is admitted but cannot dispatch.
- **HAMCP-274** (Ready, Medium): input_select option plans are prohibited
  even for unreferenced helpers (RCG-026).
- **HAMCP-273** (Backlog, Small, depends on 263): large-dashboard readback
  (RCG-045, RCG-048). It limits the completeness of 267 and 270.

## Signing batch: Core 2026.11 (registry sequence 16)

Sequence 16 must carry forward **all 31** compiled runtime profiles listed in
the matrix's "Compiled Core profile index". Each one must be re-verified
against exact Core 2026.11 source. If a profile is omitted, every tool that
depends on it becomes unavailable on 2026.11.

These are the new or revised profiles the rows above would need. A profile is
listed only where a row's scope names its commands. Each still needs owner
approval of that scope before it is compiled and signed.

| # | Profile (proposed) | For | Commands | Constraints | Gate before signing |
|---|---|---|---|---|---|
| 1 | Scene create and delete: a new version of `core.scene_configuration_operation`, or a separate create/delete profile | 071 Part 3 | `GET` and `POST /api/config/scene/config/<id>` for a new id; `DELETE /api/config/scene/config/<id>` for recovery; entity registry list and get; state get | absence check before create; delete only of exactly the created id; `scene.create`, `scene.apply` and `scene.turn_on` never sent; stored and registry readback exact; loaded state not verified, and disclosed | Josh's delete-profile decision (register 071) |
| 2 | Helper delete: a new version of `core.configuration_delete_operation` | helper-create recovery; 071 Part 3, if it shares the profile | `{type}/delete` for each approved storage helper type; `{type}/list` absence readback | pre-image capture; consumer disclosure; recreate rollback | owner scope (types) |
| 3 | Storage-helper create and update: a new version of `core.governed_configuration_operation` | helper-create candidate | `{type}/create` and `{type}/update` for each approved type (input_select, input_text, input_datetime, input_button, counter, timer, schedule) | exact definition readback through `{type}/list`; no state writes; input_select `set_options` stays never-sent | owner scope (types) |
| 4 | Config-flow helper create | helper-create candidate (template, filter, statistics) | `config_entries/flow` start and single-step submit for the approved helper domains; config-entry removal for recovery | single exact domain per plan; no options reads beyond OD-1 | owner scope; OD-1 position; HAMCP-276 landed |
| 5 | Read-only options-flow read | 264 (UI template helpers); helper readability (RCG-034, RCG-040, RCG-044) | `config_entries/options/flow` start and abort for one exact entry; no submit reachable | separate audit event; bounded redaction; negative tests proving submit is unreachable | reversal of HAMCP-232 OD-1 (owner decision) |
| 6 | Options catalog extension: a new version of `core.config_entry_helper_options_operation` | template types other than sensor and binary_sensor (RCG-037); other config-entry helpers (RCG-040) | the existing options-flow commands, with new per-type step catalogs | per-type Core flow evidence | owner scope; HAMCP-276 landed |

Not in the batch, because scope is unclear or a feasibility step comes first:

- **262** (an event or reload signal profile): only if feasibility proves a
  correlated signal.
- **134** (security metadata reads): only after the privacy review.
- **231** (Thread and Matter topology): only after the investigation names
  the exact commands.

Rows that need no Core signing: 253, 264, 267, 270, 149, 104, 109 (until
scoped), 261 (external provider), 151 and 156 (ha-mcp admission), 195 (owner
decisions first) and 054 (deferred).

## Recommended feature-lane order

This is a recommendation for the architect and Josh, not a decision.

1. **HAMCP-270, then HAMCP-267** (S, ready). They reuse the merged HAMCP-227
   store, need no signing or admission, and make every later governed plan
   disclose more. 270 comes first because it improves pre-approval disclosure
   directly. Neither depends on the deployed versions.
2. **HAMCP-264** (M, ready on 8.4.3 and 8.5.0). It closes the largest group of
   read gaps (RCG-001, RCG-009, RCG-013, RCG-014, RCG-016). It also improves
   the consumer completeness that 195 and the helper-create candidate rely
   on. Confirm first that the observed ha-mcp version admits the YAML reader.
3. **HAMCP-253** (M, ready). It is Engineering-local, with no upstream
   dependency, and it removes manual triage of refused records. Its
   persistence boundary needs a security section.
4. **Signing-batch decisions for Core 2026.11.** Josh decides on the
   delete-profile question (071 Part 3) and the helper-create scope, so that
   profiles 1 to 3 can be compiled and reviewed before sequence 16 is
   signed. Sequence 16 is needed regardless, to carry the 31 existing
   profiles to 2026.11.
5. **HAMCP-071 Part 3**, then the **helper-create candidate**. They run once
   their profiles are signed, and they share the delete profile.
6. **HAMCP-149** fixture (S), whenever a slot is free. It is
   non-executing.
7. Hold for owner decisions or feasibility: 262, 195, 109, 134, 231 and 156.
   Blocked: 261 (on 260), 151 and 054 (deferred).

The fix lane (276, then 274) should land before the helper-create candidate
and before profile 6.
