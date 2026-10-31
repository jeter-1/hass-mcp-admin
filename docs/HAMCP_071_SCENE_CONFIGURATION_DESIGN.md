# HAMCP-071 governed scene configuration edit (built inactive)

`create_scene_configuration_plan` proposes one edit to one existing
UI-managed scene: its `name`, its `icon`, or the stored state of its member
entities. It is Engineering-native and built **inactive**. It refuses with
`pending_signed_registry_admission` until an owner-signed Core registry
sequence names its profile. The planned sequence is 16, the November
signing batch that adds 2026.11.x with all profiles (OD-13 in
[`HAMCP_232_145_HELPER_OPTIONS_AND_SELECT_DESIGN.md`](HAMCP_232_145_HELPER_OPTIONS_AND_SELECT_DESIGN.md)).
It is staged for 2.4.1-beta.52.

Saving a scene never activates it. The tool never sends `scene.turn_on`,
`scene.apply`, `scene.create`, `scene.reload` or `scene.delete`, and it never
deletes a scene. Creating a scene is not in this change; it is Part 3, a
follow-up (section 0).

## 0. Decisions

1. **Split: edit only.** The task allowed CREATE to ship separately if it
   needed materially more than the automation create pattern. It does, so
   this change delivers EDIT only and CREATE is Part 3. The reasons:
   - The automation create pattern has no delete-on-rollback.
     `f3_runtime/runtime.py` `_plan_fresh_rollback` only reverses `update`
     with a prior configuration. Undoing a created scene needs a governed
     delete.
   - Governed deletion is the separately signed profile
     `core.configuration_delete_operation` (HAMCP-155). Its contract names
     `resource_types` `["automation", "script"]`
     (`ha_core_readmission/profiles.py`). Adding scenes would change a
     signed fingerprint. The alternative is a scene DELETE in the new
     profile or a second new Core profile. Both are materially more work.
   - Rollback-delete must be bound to the id the plan created, which needs
     creation-provenance persistence that does not exist today.
   - Disclosing the resulting `entity_id` before the save needs a copy of
     Core's entity-id derivation (`helpers/entity_platform.py:1071`,
     `_async_derive_object_ids`, defined at `:1345`). The entity-registry
     collision handling behind it has not been audited.
2. **Route (c): Engineering-native, direct Core REST.** The plan is a new
   resource type in the governed F3 configuration-plan family, with a
   dedicated planner. The rejected options:
   - **(a) The generic `create_configuration_plan`.** Rejected. The signed
     evidence of `core.governed_configuration_operation` covers only
     automation, script, `input_boolean` and `input_number`
     (`ha_core_readmission/routes.py:35`,
     `_F3_GOVERNED_CONFIGURATION_OPERATIONS`). Widening it would change a
     signed fingerprint.
   - **(b) The delegated ha-mcp `ha_config_set_scene`.** Rejected. In ha-mcp
     8.5.0 it is a `persistent_write` with `destructiveHint`. It upserts (a
     missing id is created) and accepts a `python_transform`, and it is
     reviewed for classification only. Using it would need an ha-mcp
     contract change, which is a non-goal.
   - **(c) Chosen.** It binds the exact existing target and re-reads it
     before the one write. Engineering never asks Core to create a scene: a
     scene missing at planning or at the final check is refused. Core's save
     is still an upsert, so a deletion landing after the final check can make
     the one save recreate the scene. Josh accepted that race with an honest
     disclosure (D3, section 3).
5. **R1 decisions (Josh).**
   - **D1.** Any `switch` member, before or after the edit, makes the plan
     `elevated_admin` in addition to object protection. A switch's purpose
     cannot be resolved from its entity id, and an unresolved purpose never
     implies harmlessness.
   - **D2.** The inactive profile declares every Home Assistant request and
     state read. It is unsigned, so its fingerprint changed (section 6).
   - **D3.** The Core upsert race is accepted and disclosed. No mechanism
     closes it.
   - **D4 (Josh, 2026-10-31).** Any `input_boolean` or `input_select`
     member, before or after the edit, makes the plan `elevated_admin`, in
     addition to D1 and object protection. Mode helpers gate security
     automations: a camera alert that requires the away, guest and cleaner
     modes off is silently disabled by a scene that turns guest mode on.
     Other `input_*` helpers (`input_number`, `input_text`) are unchanged.
3. **Built inactive.** The new compiled Core profile
   `core.scene_configuration_operation` is runtime-only and sits in
   `CORE_NOVEMBER_BATCH_PROFILES`. No release in signed sequences 1–14 names
   it (`upstream-trust/ha-core-release-registry.json`), and it is not in the
   sequence-15 batch. No signed fingerprint changes. This follows the
   recommendation in section 3.4 of
   [`HAMCP_222_SIGNED_COVERAGE_AUDIT.md`](HAMCP_222_SIGNED_COVERAGE_AUDIT.md).
4. **Classification reuses object protection.** It uses `object_protection`
   from `governance/entity_availability_exposure_planning.py:189` over every
   member before *and* after the edit, plus the switch rule (D1) and the
   mode-helper rule (D4). Gaps in
   consumer evidence are disclosed but do not elevate the policy. Risk labels are decision
   support only (`AGENTS.md`).
6. **Option C (Josh, 2026-10-31, after Codex round 3).** "Verified" means
   that the stored scene configuration reads back exactly (type-exact,
   canonical JSON) and that its entity registry identity is unchanged.
   Engineering claims nothing about the scene Home Assistant has loaded.
   The R1/R2 loaded-state reload inference (pre-POST baseline, bounded poll,
   visible-transition and re-add evidence, and its gateway, F3 and runtime
   bridges) is removed. This supersedes R1-H1 and R2-H1 as written. Codex
   R3-H1 showed that the inference could not be made sound: if another
   writer reloads before this save, the old member attributes can stay
   loaded while every visible component looks right. D1–D4 and the D3
   disclosure are unchanged. This matches the automation family, which
   verifies only the stored configuration (`_F3_GOVERNED_CONFIGURATION_OPERATIONS`
   readback in `ha_core_readmission/routes.py`, and
   `compare_resource_verification` in `governance/resources.py`). Every
   surface carries the statement "Engineering verifies the saved scene and
   its registry identity; it does not verify the scene Home Assistant has
   loaded." (section 4).

## 1. Part 1 findings: the Core contract

The pinned sources are kept in the ignored `.artifacts/core-src/scene/`.
These six files are byte-identical at 2026.10.1 and 2026.10.3 (checked with
`cmp`):
- `components/config/scene.py`
- `components/config/view.py`
- `components/homeassistant/scene.py`
- `components/scene/__init__.py`
- `helpers/entity_platform.py`
- `helpers/entity_registry.py`

`helpers/state.py` was read at 2026.10.3 only. Line numbers below are at
2026.10.3. The route is `/api/config/scene/config/{config_id}` and requires
an admin.

- **GET** returns the stored entry, or 404 `"Resource not found"`
  (`config/view.py:86-96`).
- **POST is an upsert** (`config/view.py:99-139`).
  - A bad JSON body, a bad key or a body that fails the schema returns 400.
  - Core validates the body with the scene `PLATFORM_SCHEMA`, then stores
    the raw posted body.
  - `_write_value` (`config/scene.py:55-89`) replaces the whole entry with
    `{id: config_key, name, entities, **body}`. If no entry has that key, it
    appends one (`:88-89`). A body `id` would override the stored id, so
    the tool never sends `id`.
  - On every save, any stored entry without an `id` gets a new `uuid4` id
    (`:81-82`). The plan discloses this.
  - The post-write hook runs as a background task (`config/view.py:134-137`)
    and calls `scene.reload` for every non-delete action
    (`config/scene.py:25-29`). The POST returns `{"result": "ok"}` before the
    reload finishes (`config/view.py:139`), and a reload failure is only
    logged.
- **DELETE** returns 400 when the entry is absent. Its hook removes the
  `scene`/`homeassistant` registry entry and does not reload
  (`config/view.py:142-159`, `config/scene.py:31-40`). The tool can never
  reach it (section 5).
- **Schema** (`homeassistant/scene.py:87-106`). The keys are closed: `id`,
  `name` (required), `icon` (`cv.icon`), `entities` (required) and
  `metadata` (a mapping). Any other key is rejected. Member keys are checked
  only for `entity_id` format, not for existence.
- **States.** `_convert_states` (`homeassistant/scene.py:39`) takes `state`
  out of a mapping value and maps a bare boolean to `on`/`off`. A mapping
  without `state` is rejected, so the tool requires `state` in every member
  it writes.
- **Storage.** A YAML dump and an atomic write (`config/view.py:252-257`).
  GET returns the stored entry exactly, up to the YAML round trip.
- **Reload.** `reload_config` (`homeassistant/scene.py:182`) calls
  `platform.async_reset()` (`:202`). That removes every scene created at
  runtime with `scene.create`. The plan discloses this.
- **Loaded scene.** Its `unique_id` is the config id
  (`homeassistant/scene.py:356`), which is how the registry entry is bound.
  Member snapshots of the loaded scene are not observable (`:362-367`), and
  the tool does not read the loaded scene at all (decision 6).
- **Activation.** `async_reproduce_state` (`helpers/state.py:27`) reproduces
  every domain in parallel (`asyncio.gather`, `:66`). Stored member order is
  therefore not an activation order.

### 1.1 What is editable

A scene is editable only when both of these hold:
- the entity registry has an entry with domain `scene`, platform
  `homeassistant` and `unique_id` equal to the config id;
- GET by that config id returns the stored entry.

These are refused:
- integration scenes (platform other than `homeassistant`):
  `scene_configuration_not_ui_managed`;
- YAML scenes stored outside `scenes.yaml` (registry entry present, GET 404):
  `scene_configuration_not_ui_managed`;
- scenes without an id, which have no registry `unique_id` to bind.

### 1.2 Report-only finding 7

In the existing read path, a not-found read reaches the caller as a
retryable, generic `upstream_error` instead of a not-found result. That is a
truthfulness gap. It is reported here and **not fixed** by HAMCP-071.

The new planner does not inherit the gap. An unknown config id refuses as
`scene_configuration_target_not_found`, not as Home Assistant being
unavailable. `PlanningRefusalTests.test_retryable_not_found_gap_is_not_masked`
pins this.

## 2. The tool

Input is a closed schema: `additionalProperties` false, `config_id`
required, and at least one field besides it (`minProperties` 2).

| Field | Rule |
|---|---|
| `config_id` | Required. `^[A-Za-z0-9_-]{1,64}$`. |
| `entity_id` | Optional cross-check. Must start with `scene.` and must equal the registry entry for the config id (`scene_configuration_target_mismatch`). |
| `name` | Non-empty string, at most 255 characters, stored exactly. Credential-shaped text is refused (`scene_configuration_text_withheld`). |
| `icon` | `^[a-z0-9-]{1,31}:[a-z0-9-]{1,63}$`, at most 64 characters, or `null` to remove it. |
| `entities` | A merge patch, with 1–64 keys: `{entity_id: snapshot}` sets or replaces a member and `{entity_id: null}` removes one. A snapshot is a mapping that contains `state` (a string of at most 255 characters), with at most 64 properties. Members in the `scene` domain are refused. |
| `expiration_minutes` | 5–1440, default 120. |

The tool passes the raw arguments on, so defaults are never materialized.
Its annotations are the same as the dashboard metadata tool's:
`readOnlyHint` false, `destructiveHint` false, `idempotentHint` false and
`openWorldHint` false. Only `config_id` and `entity_id` are identifier
inputs. Member keys are checked with `validate_entity_id`, and `scene.`
members are refused in `validate_request`.

**The edit.** The proposed configuration is the full stored configuration
with the requested changes applied. Unrequested keys, `metadata` included,
are kept exactly. The write body is that configuration without `id`.

Bounds on the stored configuration:
- at most 64 members and 64 attributes per member;
- keys of at most 255 characters and text of at most 2048 characters;
- lists of at most 128 items and nesting at most 4 deep;
- at most 65 536 bytes in total, and integers within 2^53.

Planning refuses before any dispatch, and creates no plan, when:
- the request is malformed (`scene_configuration_invalid_request`), names a
  field that is not supported (`scene_configuration_field_not_supported`),
  has a bad value (`scene_configuration_invalid_value`, with `field`), or
  requests no field (`scene_configuration_no_fields_requested`);
- the config id is unknown (`scene_configuration_target_not_found`);
- the scene is not UI-managed (`scene_configuration_not_ui_managed`), is
  ambiguous (`scene_configuration_target_ambiguous`: more than one registry
  entry matches), has configuration errors
  (`scene_configuration_target_unreviewable`) or is disabled
  (`scene_configuration_target_disabled`);
- a stored scene has no registry entry yet (`scene_configuration_not_loaded`).
  A stored scene whose loaded copy is stale is planned from storage; the
  loaded copy is never read;
- stored text is withheld by the screen (`scene_configuration_text_withheld`);
- the edit changes nothing. The result is a truthful no-change refusal
  (`scene_configuration_no_change`, `already_satisfied`), with no plan;
- a removal names a member that is not in the scene
  (`scene_configuration_entity_not_in_scene`), or the edit would leave no
  member (`scene_configuration_entities_empty`);
- an unrequested stored value cannot be sent through the closed POST
  (`scene_configuration_baseline_not_sendable`);
- the restore could never write the current configuration back
  (`scene_configuration_baseline_not_restorable`);
- an added or replaced member is in neither the entity registry nor the
  states (`scene_configuration_unknown_entities`, at most 16 named);
- the read was denied (`scene_configuration_permission_failure`) or failed
  (`scene_configuration_provider_unavailable`).

## 3. Fingerprint, disclosure and policy

- **Binding.** The governed state is `config_id`, `entity_id`,
  `registry_entry_id` and `config` (the exact GET body). Nothing about the
  loaded scene is part of the state.
- **Stable read.** The state read works as follows:
  1. GET the stored configuration.
  2. Read the registry entry (domain `scene`, platform `homeassistant`,
     `unique_id` equal to the config id, not disabled).
  3. GET the configuration again.

  If the configuration changed between the two GETs, the read is unstable
  and is refused as `scene_configuration_provider_unavailable`. Comparisons
  are type-exact, using canonical JSON (`exactly_equal`), so `1`, `1.0` and
  `true` are different values.
- **Disclosure** (model `scene_configuration_disclosure_v1`). For each
  plan, the disclosure shows:
  - `name` and `icon` before and after;
  - each added, removed or changed member with its before and after
    snapshot, and the unchanged member count;
  - the member counts before and after;
  - the consumers: automations and scripts that reference the scene, from
    the 069B dependency index. Planning reads only the snapshot that is
    already cached; it never builds or refreshes the index, so it sends no
    Home Assistant request of its own. When the index is missing, `status` is
    `unavailable` with `evidence_gaps` `["dependency_index_unavailable"]`;
    when the cached snapshot is not current, the gap is
    `dependency_index_not_current`. Neither is ever "no consumers".
  - the switch members before and after the edit (entity ids only);
  - the `input_boolean` and `input_select` members before and after the
    edit (`mode_helper_members`, entity ids only).

  It also carries these fixed texts:
  - "Saving this scene does not activate it: no device changes state when
    the plan is applied. The stored states take effect only when the scene
    is activated later (by a person, an automation or a script)."
  - "Home Assistant reloads every UI and YAML scene after the save. Scenes
    created at runtime with scene.create are removed by that reload."
  - "Home Assistant rewrites scenes.yaml on save; any stored scene entry
    without an id is given a new random id."
  - "Members are saved in entity_id order. Core reproduces scene states
    with every domain in parallel, so stored order is not an activation
    ordering guarantee before or after this save."
  - The metadata text: metadata is never changed.
  - The verification-scope text (`verification_scope_disclosure`): the
    statement "Engineering verifies the saved scene and its registry
    identity; it does not verify the scene Home Assistant has loaded.",
    what a verified result means, that the background reload is not
    observed so the loaded scene can still differ, and that Engineering
    never reloads, activates or resends.
  - The rollback, untrusted-text and consumer texts.

  The disclosure also carries the scope fields `verification_scope`
  (`stored_configuration_and_registry_identity`), `loaded_state`
  (`not_verified`) and `verification_scope_statement`.

  The plan-level disclosure records:
  - target count 1;
  - execution `single_update_at_most_once`;
  - provider `direct_home_assistant_scene_configuration`;
  - fallback `none`;
  - the never-sent list (section 6);
  - the residual concurrency window (D3): "Core has no compare-and-swap for
    scene configuration, and its save is an upsert. ... between that final
    check and the save: a concurrent edit to this scene can be overwritten
    without being detected (the readback sees only this plan's result), and
    a concurrent deletion can make the save recreate the scene.
    Verification proves this plan's stored result, not that no other change
    happened in that window." No readback claims to detect this race.
- **Policy.** Object protection is evaluated over the members before and
  after the edit. Device class is read for `cover` and `binary_sensor`. A
  device class that cannot be resolved counts as protected
  (`device_class_unresolved`).
  - Any protected member: `elevated_admin`, risk `HIGH`, physical
    consequence `indirect`. The reason codes are sorted:
    `scene_configuration_protected_member` plus
    `scene_protected_member_<reason>`. The statement is "This scene stores
    states for protected devices; activating it later can change them.
    Saving it does not."
  - Any `switch` member before or after the edit (D1): `elevated_admin`,
    with `scene_protected_member_switch_domain`. A second statement names
    the switch entity ids: "Elevated because the scene has switch members
    before or after this edit; a switch's purpose cannot be resolved from
    Home Assistant, so it is treated as protected: ...". The rule is on the
    domain only, so
    `light.switch_room` is not a switch.
  - Any `input_boolean` or `input_select` member before or after the edit
    (D4): `elevated_admin`, with `scene_protected_member_mode_helper_domain`.
    A statement names the helper entity ids (never their states or
    options): "Elevated because the scene has input_boolean or input_select
    members before or after this edit; mode/helper members can change how
    automations behave: ...". The rule is on the domain only, so
    `light.input_boolean_room` is not a helper, and `input_number` and
    `input_text` members do not elevate. An elevated plan stays actionable
    after elevated approval.
  - Otherwise: `standard_admin`, risk `LOW`, physical consequence `none`.
    The reason codes are `scene_configuration_standard_policy` and
    `supported_configuration_change`.
  - The protection floor derived from the entity ids is recomputed at
    policy time and must be contained in the bound basis.
  - An incomplete operation, or a multi-operation plan that contains a
    scene operation, is `PROHIBITED`
    (`scene_configuration_execution_contract_incomplete`).
- **Untrusted text.** Planning screens the scene name. The plan title is
  `Update scene configuration <config_id>` (or `Restore ...`) and never
  repeats the name.

## 4. Apply, verification and recovery

The F3 runtime is reused: durable intent, an exact-target lock and
`mark_provider_send()` at the send boundary. Its only scene addition is the
additive scope projection (`scene_scope_fields`). The locks are an exclusive
`scene_configuration` identity per config id plus the shared `reload:scene`
identity.

- **Write permit (R1-H2).** The lowest write layer
  (`scene_configuration.write`) sends only under an F3 write permit bound to
  the plan, the config id, the command's arguments hash and the dispatch
  intent, and each permit carries one send. The permit is installed only by
  the adapter's `dispatch_scope()` around the one send. A planning lease, an
  authorizing Core call window, or a wrong target, command or intent is
  refused as `scene_configuration_write_permit_required`, with no request.
  Every direct read refuses with `pending_signed_registry_admission`, and 0
  calls, while admission is withdrawn.
- **Before dispatch.** `scene_configuration_preflight_evidence` re-reads the
  scene immediately before the POST. A missing scene or any type-exact drift
  is refused as `stale_target_state`, with no dispatch. It never creates a
  scene. Create and delete commands are refused as update-only.
- **Dispatch.** One `POST /api/config/scene/config/{config_id}` carrying the
  full proposed configuration without `id`. It is sent at most once, never
  redispatched, with no fallback. A metadata change
  (`scene_configuration_metadata_changed`) or a no-change command
  (`scene_configuration_no_change`) is refused before sending.
- **Write outcomes.**
  - 401 or 403, or an authorization error: not dispatched.
  - 400 (`scene_configuration_write_rejected`) and other 4xx: not
    dispatched (`dispatch_failed_confirmed`).
  - 5xx: an error result after the send. It is indeterminate
    (`failed_post_dispatch`), and the readback decides it.
  - A lost response is never redispatched.
  - `{"result": "ok"}` is not success by itself; the readback decides it.
- **Readback (option C).** After the POST the runtime reads the governed
  state again: the stored configuration (GET) and the registry entry. The
  task is `succeeded_verified` exactly when the stored configuration equals
  the proposed configuration type-exactly (canonical JSON, so `99` and
  `99.0` differ, and an unrequested change is a difference) and the
  registry identity (`config_id`, `entity_id`, `registry_entry_id`) is
  unchanged. Nothing else is claimed:
  - The loaded scene is never read and never verified. A reload by another
    writer before the save, a failed reload or a late reload does not change
    the outcome (Codex R3-H1 probe: the save verifies, the loaded attributes
    stay old, and no surface claims the loaded scene).
  - A stored mismatch (nothing stored, a type-inexact value, an
    unrequested change) is a verification failure: `failed_post_dispatch`,
    `verification_mismatch`, `exact_readback_not_verified`.
  - A replaced registry entry or a renamed scene entity is a verification
    failure in the same way.
  - A removed or disabled registry entry, or a failed read, cannot be
    observed: the task stays `observing` (then manual review) and is never
    `succeeded_verified`.
  - Recovery is read-only, and the send is never repeated.
- **Scope fields.** Every scene apply result, its `execution_task`,
  `get_execution_task` and a retained apply result carry
  `verification_scope: "stored_configuration_and_registry_identity"`,
  `loaded_state: "not_verified"` and `verification_scope_statement`:
  "Engineering verifies the saved scene and its registry identity; it does
  not verify the scene Home Assistant has loaded." The fields are additive
  and appear only on tasks whose children are all scene operations. The
  plan disclosure (section 3), the tool description and the capability
  catalog carry the same scope. `VerificationScopeTests` holds a guard that
  no surface claims loaded-state verification.
- **Already stored.** When preflight finds the desired stored configuration
  and registry identity already present, nothing is sent and the task is
  `succeeded_verified` with `desired_state_already_reached`, the same
  stored-only scope. Rollback is unavailable, because it needs one
  dispatch.
- **Every verified path.** In the F3 configuration adapter only two
  functions produce `succeeded_verified`: `verify` (the exact readback) and
  `_preflight_already_reached`. An AST check in `VerificationScopeTests`
  pins that producer set. A rollback plan is itself a scene edit with the
  same readback, and it requires the forward edit to be verified and
  dispatched once.
- **Rollback.** `rollback_change` creates a separate governed plan
  (`hamcp071-scene-configuration-restore-v1`). It restores the exact
  captured prior configuration, needs its own approval and does not activate
  the scene. Its result carries `restored_fields` and
  `approval_required: true`. It refuses in these cases:
  - the forward change was not verified, or not dispatched;
  - the prior state is missing;
  - the current state no longer equals the forward result, compared
    type-exactly (`scene_configuration_rollback_baseline_drift`). This also
    covers a restore that would change nothing.

  Other restore-preparation refusals pass through with their own codes.
  Planning has already refused any edit whose restore could not be sent
  (section 2).

## 5. Inactive mechanism

- The profile is in `CORE_NOVEMBER_BATCH_PROFILES` for sequence 16. Signed
  sequence 14 does not name it, and neither does the sequence-15 batch, so
  no Core release selects it.
- The tool, the generic `create_configuration_plan` (for the raw resource
  type) and every persistence path refuse
  `pending_signed_registry_admission` first, before any read, validation
  read or persistence. The text is: "Scene configuration edits are
  implemented but inactive: pending signed registry admission (planned: the
  November Core signing batch, sequence 16). No plan is created and nothing
  is dispatched."
- The service refusal is `PROVIDER_UNAVAILABLE` with these details:
  - `reason` and `reason_code`: `pending_signed_registry_admission`;
  - `resource_type`: `scene_configuration`;
  - `core_capability`: `core.scene_configuration_operation`;
  - `plan_created` false;
  - `provider_dispatch_occurred` false.
- Generic routes that do not carry the scene planning context refuse the
  resource type.
- The send boundary is the gateway write. It refuses a complete, valid
  command before sending it, unless:
  - Core currently admits the profile (otherwise
    `pending_signed_registry_admission`); and
  - the call runs inside the F3 dispatch window that installs the request's
    Core dispatch authority (otherwise `active_f3_execution_required`).

  This applies the HAMCP-106 R1-H1 rule from the start.
- A legacy apply without the F3 runtime refuses
  `core_authority_unavailable`. `scene_configuration` is in
  `DORMANT_PROFILE_RESOURCE_TYPES`.
- F3 dispatch admission is checked again (`UNSIGNED_PROFILE_REQUIREMENTS`).
- The route allowlist (`clients/paths.py`) admits `/config/scene/config/{}`
  for GET and POST only. DELETE is unreachable. The only websocket commands
  are `config/entity_registry/list` and `config/entity_registry/get`.
- `STATIC_TOOL_CORE_REQUIREMENTS` maps the tool only to
  `core.scene_configuration_operation`. It needs no separate read profile.
  `f3_requirements` maps the identity only to the scene profile, never to
  `core.governed_configuration_operation`.
- Health under sequence 14 itemizes 26 compatible and 5 withheld profiles.
  The withheld entries are:
  - `core.config_entry_helper_options_operation`;
  - `core.dashboard_metadata_operation`;
  - `core.scene_configuration_operation`;
  - `core.script_trace_read`;
  - `core.typed_input_select_option_operation`.

  Each is `unavailable` with `positive_authority_missing`. The scene
  entry's `affected_tools` is `["create_scene_configuration_plan"]`. After
  sequence 15 the count is 30 compatible and 1 withheld (this profile).
  After sequence 16 it is 31 compatible and 0 withheld.

## 6. Profile shape for signing (seq 16)

| Field | Value |
|---|---|
| `capability_id` | `core.scene_configuration_operation` |
| `capability_class` | `scene_configuration_operation` |
| `profile_id` | `core_scene_configuration_operation_v1` |
| `profile_version` | `1` |
| `adapter_id` | `compiled-core-scene-configuration-operation-v1` |
| `contract_fingerprint` | `sha256:73bac9067f62b62b039ce03f7754d1312f9c0595d0fb16af49851a49c90ba6bb` |
| `auto_eligible` | `true` (the default; every November-batch profile) |

- **Required checks:** `rest_scene_config_get_by_config_id`,
  `rest_scene_config_post_by_config_id`, `rest_state_get_by_entity_id`,
  `websocket_entity_registry_list_and_get`,
  `scene_config_and_registry_identity_readback_exact` and
  `scene_loaded_state_not_verified_disclosed`.
- **Provider boundary:** `direct_ha_api`.
- **Predispatch:** `core_identity`, `capability_contract`,
  `target_fingerprint`, `session_fingerprint` and `generation`.

The response contract has these values:
- target: `one_exact_existing_ui_managed_scene_by_config_id_with_homeassistant_registry_entry`;
- requests (D2, `SCENE_CONFIGURATION_REQUESTS`): every Home Assistant
  request the provider can send:
  - `GET /api/config/scene/config/{config_id}`;
  - `GET /api/states/{entity_id}`;
  - `POST /api/config/scene/config/{config_id}`;
  - websocket `config/entity_registry/get`;
  - websocket `config/entity_registry/list`.

  `ReachableRequestSetTests` proves that create, apply and restore reach
  exactly this set;
- routes: read `GET /api/config/scene/config/{config_id}`, write
  `POST /api/config/scene/config/{config_id}` and state
  `GET /api/states/{entity_id}`; commands read
  `config/entity_registry/get` and `config/entity_registry/list`;
- state reads (the `GET /api/states/{entity_id}` route is for members
  only; the scene's own state is never read):
  `member_device_class_when_the_registry_has_none` and
  `member_existence_when_unregistered`;
- consumer disclosure:
  `current_cached_dependency_index_snapshot_only_never_built_or_refreshed_gap_disclosed_when_not_current`;
- arguments:
  - fields `entities`, `icon` and `name`;
  - name: `non_empty_string_max_255_exact_no_trim`;
  - icon: `prefix_colon_name_max_64_or_null_removes`;
  - entities: `merge_patch_of_existing_entity_ids_to_bounded_snapshots_with_state`;
  - body: `full_proposed_configuration_without_id`;
  - unrequested fields: `sent_exactly_as_stored`;
- dispatch: `at_most_once_scene_config_post_per_scene_under_one_f3_write_permit_bound_to_plan_target_command_and_dispatch_intent`;
- never sent: `DELETE /api/config/scene/config/{config_id}`, `scene.apply`,
  `scene.create`, `scene.delete`, `scene.reload` and `scene.turn_on`;
- side effect: `core_post_write_hook_reloads_all_ui_scenes`;
- readback: `stored_config_exact_type_exact_plus_registry_identity_unchanged`;
- verification scope: `stored_configuration_and_registry_identity`;
- loaded state: `not_verified`;
- concurrency: `no_core_compare_and_swap_upsert_concurrent_edit_overwritten_undetected_concurrent_delete_recreated_disclosed`;
- recovery: `read_only_no_redispatch`.

The fingerprint above is computed from the source at this head
(`CORE_RUNTIME_CAPABILITY_PROFILES`). R1 (D2) changed it from
`sha256:8fa78120…` to `sha256:d461fa2a…`, and option C changed it to the
value above (required checks, state reads, readback and scope; the routes
and request set are unchanged). The profile is unsigned, so no signed
fingerprint changed.
`ProfileShapeTests.test_seq16_profile_shape_is_the_documented_shape` pins the whole
profile mapping, including this fingerprint, so a change to the shape
breaks a test before signing. The signed-registry entry itself belongs with
the seq-16 lane (section 7). The profile is also in the November dormant
probe evidence (`ha_core_readmission/source.py`).

## 7. Evidence lane and what remains for November

There is **no scene evidence lane yet**.
`scripts/seq15_contract_acceptance.py` records the profile as
`SEQ16_DORMANT`. The dormant sets in the seq-13, alarmo, automation-baseline,
configuration-delete and entity-metadata acceptance scripts exclude it.
`ci.yml` is unchanged.

Remaining for the November sequence-16 batch (not authorized here):
1. Define a seq-16 scene lane against the disposable Core, with a
   fingerprint pin. Wiring it into `ci.yml` is a protected change.
2. Sign sequence 16 with the profile in section 6.
3. Run the lane and confirm these points, which are unverified offline:
   - the GET/POST round trip and the stored-configuration readback;
   - the registry identity across the save's background reload;
   - id assignment for entries without an id.
4. Part 3: CREATE, with the work listed in section 0, item 1.

## 8. Offline proof

`tests/test_hamcp071_scene_configuration.py` has 152 tests and uses the
synthetic fixtures in `tests/hamcp071_fixtures.py`:
- `scene.synthetic_lounge_dim`, config id `1700000000001`;
- `scene.synthetic_leaving`, config id `1700000000002`;
- `scene.synthetic_presence_simulation`, an integration scene;
- `scene.synthetic_yaml_scene`, config id `1700000000009`, a YAML scene.

The tests use the real service, the F3 runtime and the scene gateway. They
cover:
- positive edits, disclosure and redaction;
- every planning refusal;
- inactive refusal at every entry point;
- generic routes that cannot carry the type;
- inactive and admitted resource writes;
- runtime composition and the apply boundary;
- truthful failure outcomes;
- rollback and its refusals;
- the contract boundaries;
- R1: the F3 write permit and withdrawn-admission reads
  (`AdmittedResourceWriteTests`), the switch rule (`SwitchMemberTests`),
  the declared request set
  (`ReachableRequestSetTests`) and the upsert-race disclosure
  (`DisclosureTests.test_the_upsert_race_is_disclosed_and_never_claimed_detected`);
- D4: the mode-helper rule (`ModeHelperMemberTests`): an `input_boolean`
  or `input_select` member elevates, before or after the edit or unchanged;
  a light-only scene and `input_number`/`input_text` members stay
  standard; the policy floor refuses a basis without the D4 reason; an
  elevated plan applies after elevated approval and never activates;
- option C (`VerificationScopeTests`): snapshot, name, icon, icon removal,
  member add and member removal each reach `succeeded_verified` with the
  scope fields on the result and the task; the Codex R3-H1 probe (another
  writer's reload before the save, then a failed reload) verifies the
  stored scene, leaves the loaded attributes old, reads no scene state and
  claims nothing loaded; a failed reload; stored mismatches (nothing
  stored, type-exact `99.0`, an unrequested name) and registry drift (a
  replaced entry, a renamed entity) are verification failures; a removed or
  disabled entry is never verified; a guard that no surface (apply result,
  execution task, plan disclosure, tool description, catalog, profile)
  claims loaded-state verification, with a control for the guard pattern;
  a check that the reload inference is removed while `dispatch_scope` and
  `preflight_evidence` stay; the AST check of the verified producers; and
  the strict rollback projection; and two isolated controls for option C
  mutants, `test_scope_fields_are_stamped_only_on_scene_tasks` (O11) and
  `MutationSurvivorTests.test_response_identity_is_the_exact_config_id`
  (O4). The R1 and R2 reload-evidence tests were removed with the code
  they tested.

The new tests found and fixed **two runtime defects**:
1. **Invalid policy reason codes.** The policy emitted
   `protected_object:<reason>` (the colon is invalid, and the codes were
   unsorted). Every elevated scene plan therefore failed with
   `ValueError("invalid policy reason codes")`. The codes are now
   `scene_protected_member_<reason>`, sorted. Regression test:
   `DisclosureTests.test_lock_or_garage_cover_elevates_before_or_after`.
2. **Type-loose comparisons.** `already_reached` in the strategy, the
   preflight check and the rollback drift check compared normalized state
   with Python `==`, where `1 == 1.0 == True`. They now use
   `exactly_equal` (canonical JSON). The readback verification was already
   fingerprint-based and type-exact. Regression tests:
   - `IsolatedControlTests.test_strategy_readback_alone_rejects_a_mismatch`;
   - `ContractBoundaryTests.test_preflight_holds_only_for_the_bound_state`;
   - `RollbackTests.test_rollback_drift_is_type_exact`.

Mutation evidence is in `docs/evidence/hamcp071-mutations/`; see
`README.md` and `results.json` there. The runner,
`run_mutations.py`, runs each mutant in a copied tree. Results:
- **Round 0:** 64 of 81 mutants killed.
- **Between rounds:** I added `MutationSurvivorTests` and
  `ProfileShapeTests`. Most of the new tests isolate one layered check,
  such as a gateway or strategy preflight, a rollback child-record check or
  the rollback lease recheck. Each of those is redundant on the full path.
- **Before R1:** 81 of 82 mutants killed, against a baseline of 219 tests
  (`pre-r1-results.json`).
- **R1 full run:** the 82 replayed plus 22 new `H*` guard removals (permit,
  read admission, reload latch and baseline, poll exit, strategy evidence,
  runtime passthrough, switch rule, cached-only consumers): 99 of 104
  killed, against a baseline of 246 tests (`results.json`).
- **R1 isolation:** the four masked survivors W6, H9, H11 and H15 each got
  an isolated test; the rerun kills all four, plus the H14 control, against
  a baseline of 247 tests (`r1-rerun-results.json`).
- **D4 full run:** all 104 replayed plus 9 new D4 mutants `H23`–`H31`
  (each helper domain removed, the rule removed in the planner or the
  floor, substring and every-`input_*` matches, the statement, and the
  before-edit members): 112 of 113 killed, against a baseline of 258 tests
  (`d4-results.json`). H17–H19 were re-anchored onto the shared member
  rule.
- **R2 full run:** all 113 replayed (T10, H8, H11, H12, H14, H15 and H16
  re-anchored onto the evidence rule) plus 10 new `E1`–`E10` evidence-check
  removals: 119 of 123 killed, against a baseline of 278 tests
  (`r2-results.json`).
- **R2 isolation:** G8, H10 and H13 lost their old killing paths with the
  R2 change and each got an isolated test; the rerun kills all three
  against a baseline of 282 tests (`r2-rerun-results.json`). So 122 of
  123 were killed. (The R1 and R2 reload-evidence mutants are historical:
  option C removed the code they mutated.)
- **Option C full run:** 24 mutants were retired with the removed code
  (T5, T9, T10, B9, B10, H8–H16 and E1–E10), H6 was re-anchored to the
  kept permit consumption, and the other 99 were replayed. Eleven new
  `O1`–`O11` mutants remove one option C check each: the registry
  identity and `entity_id` in the projection, the stored-`config`
  comparison, the response-identity check, the scope fields on the apply
  result, the execution task, the retained result, the plan disclosure
  and the catalog, `loaded_state: "verified"`, and the scope stamped on
  every family. 107 of 110 killed, against a baseline of 257 tests
  (`optc-results.json`).
- **Option C isolation:** O4 and O11 each got an isolated test
  (`test_response_identity_is_the_exact_config_id` and
  `test_scope_fields_are_stamped_only_on_scene_tasks`); the rerun kills
  both against a baseline of 259 tests (`optc-rerun-results.json`). So
  **109 of 110** are killed, and every mutant that removes a stored
  equality, registry identity or scope-field check is killed.
- **Survivor:** A3, which removes the persistence-entry recheck. It is
  equivalent: the generic builder repeats the same gate as its first
  statement, and nothing yields between the two checks. The combined
  control A3A4, which removes both checks, is killed.

## 9. Non-goals and known limitations

Not in this change:
- creating a scene (Part 3) and deleting a scene;
- activating a scene or reproducing its states;
- `metadata` edits;
- integration scenes, YAML scenes outside `scenes.yaml` and scenes without
  an id;
- `scene.create` runtime scenes;
- the delegated `ha_config_set_scene`;
- any ha-mcp contract change;
- any signed registry, signed profile or existing fingerprint change.

Known limitations:
- **Residual concurrency window (D3, accepted).** It runs between the
  final check and the POST (section 3). A concurrent edit in that window can
  be overwritten without being detected, and a concurrent deletion can make
  the save recreate the scene, because Core's save is an upsert. The
  readback proves only this plan's stored result. The send is never
  repeated.
- **The loaded scene is not verified (option C).** Engineering verifies
  the saved scene and its registry identity; it does not verify the scene
  Home Assistant has loaded. A failed, late or concurrent reload can leave
  the loaded scene different from the verified stored configuration. This
  is disclosed on every surface; the remedy is to reload scenes in Home
  Assistant, which Engineering never does.
- **Consumers are cached.** The consumer disclosure uses only the cached
  dependency index and discloses a gap when it is missing or not current.
- **Global reload.** The save reloads every UI and YAML scene and removes
  `scene.create` runtime scenes. This is disclosed, not prevented.
- **Untrusted store values.** A stored configuration that the closed POST
  cannot carry (`scene_configuration_baseline_not_sendable`) or restore
  (`scene_configuration_baseline_not_restorable`) cannot be edited by this
  tool.
- **Member order.** Members are saved in `entity_id` order. Order does not
  affect activation (section 1).
- **Finding 7** is unfixed (section 1.2).
