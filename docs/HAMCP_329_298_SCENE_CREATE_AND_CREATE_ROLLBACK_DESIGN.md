# HAMCP-329 governed scene create and HAMCP-298 rollback for governed creates — design

Status: **2.6.0 design, docs only.** Written 2026-11-07 (CST). This document
does not authorize implementation outside its build plan, deployment, live
Home Assistant access, registry signing, a signed-profile change or a release.
Codex reviews it; the author does not review its own work.

Base: `origin/release/2.6.0` `7246f2d21693f039504542574569f03b71476489`
(Engineering `2.4.2-beta.7` materialized; the same commit as the
`release/2.5.0` tip). Every `file:line` below is at this base. Paths under
`hass_mcp_engineering_beta/ha_mcp_engineering/` are shortened to `ha_mcp_engineering/`.

Upstream authority (ADR-021): Home Assistant Core at exact tags.

- `2026.11.0`, commit `74cf5a49c63ed8d4d87717844bf14f79c28c9cea`. This is the
  HAMCP-280 exact stable target (`scripts/core_lane_bindings.py:59-63`).
- `2026.10.3`, commit `1f214b8461dd1fbbd66e10765c6905f49d3c1d86`. This is the
  newest Core release in the signed journal.

`homeassistant/components/config/scene.py`, `components/config/view.py` and
`helpers/collection.py` are byte-identical at the two tags. Upstream line
numbers are at `2026.11.0` unless a tag is named. `2026.10.1` (signed for the
delete profile) was not re-read for this design. Its v2 evidence belongs to
the signing step (§9).

## 1. Register outcomes and binding decisions

**HAMCP-329** (register, quoted):

> Engineering creates a new scene by exact config id with approval, verifies
> the stored configuration and registry identity exactly (loaded state
> disclosed as unverified), and undoes it by deleting exactly that scene; the
> existing core.configuration_delete_operation profile gains scenes in a new
> version, shared with HAMCP-298 rollback for governed creates; scene
> activation stays never-sent.

Dependency: "New profile versions signed in a sequence after 17." Source:
`docs/FEATURE_READINESS_MAP.md` row "HAMCP-071 Part 3" (`:59-80`, RCG-004..007).
The row notes require that `scene_loaded_state_not_verified_disclosed` carry
over from Part 2.

**HAMCP-298** (register, quoted):

> A plan that creates an automation, script or helper produces a rollback that
> deletes exactly that created object (bound to its identity and fingerprint,
> refused if it changed), instead of requiring a separately written delete
> plan. Generalizes the HAMCP-071 Part 3 idea beyond scenes, reusing governed
> deletion (HAMCP-155).

Dependencies: HAMCP-155, HAMCP-071.

**Binding decisions**

- **Josh decision, 2026-11-07** (recorded in the task prompt as design-complete
  scoping for the 2.6.0 feature milestone):
  - Extend the existing signed delete profile to scenes.
  - Scene create by exact config id, with approval.
  - Verify the stored configuration and the registry identity exactly.
  - Disclose loaded state as unverified.
  - Undo by deleting exactly that scene.
  - The delete profile gains scenes in a new version, shared with HAMCP-298.
  - Scene activation stays never-sent.
- **HAMCP-071 option C, Josh 2026-10-31** (`ha_core_readmission/profiles.py:806-809`).
  Verified means the stored configuration plus the registry identity. Loaded
  state is not verified.
- **HAMCP-155 decisions, Josh 2026-10-23**
  (`docs/HAMCP_155_GOVERNED_DELETION_DESIGN.md` §0, §2-§3):
  - delete semantics;
  - polled absence readback;
  - a recreate rollback;
  - protected-effect and consumer disclosure;
  - 1-8 children, ordered, non-atomic;
  - Ingress-only exact-hash approval.
- **ADR-018** (`docs/architecture/ADR-018-F3-GOVERNED-ROLLBACK.md`) states:
  "Create-through-delete is prohibited" and "No generalized … deletion … becomes
  reachable".
  - For scenes, the 329 decision above supersedes that prohibition.
  - For automations, scripts and helpers, the 298 register outcome supersedes
    it. The row carries no dated Josh decision, so confirmation is **Q5**
    (§10).
  - Under ADR-022 the superseding decision must be recorded. The ADR-018
    amendment is a build-plan item (§9), not part of this PR.

**Non-goals.** Adding any of these would be a scope change. This design
stops at that line.

- Public, general-purpose deletion of scenes or helpers through
  `create_configuration_delete_plan`. Both outcomes ask only for undo of an
  Engineering create.
- Rollback of label creates. Labels are not helpers. HAMCP-165 keeps
  `ROLLBACK_NOT_SUPPORTED`.
- Scene activation (`scene.turn_on`, `scene.apply`), dynamic scenes
  (`scene.create`) and `scene.reload`.
- Storage-helper types that Engineering cannot create today (readiness map
  future row 3).

## 2. Current state at the base

### 2.1 Signed Core profiles involved

All three profiles are built by `_contract()` (`ha_core_readmission/profiles.py:109-142`).
The fingerprints below were recomputed from base source and match the signed
journal entries in `upstream-trust/ha-core-release-registry.json`. The
journal tip is sequence 15 (checkpoint 1), carrying Core 2026.10.0,
2026.10.1 and 2026.10.3.

| Capability | Profile id / version | Adapter | Fingerprint | Signed for |
| --- | --- | --- | --- | --- |
| `core.configuration_delete_operation` | `core_configuration_delete_operation_v1` / 1 | `compiled-core-configuration-delete-operation-v1` | `sha256:5688af5d141b97ca33eadf251ecfbb8f3b1f833024b0ee1acea4973f558f887d` | 2026.10.1 (since seq 13), 2026.10.3 (since seq 14), carried in seq 15; not 2026.10.0 |
| `core.scene_configuration_operation` | `core_scene_configuration_operation_v1` / 1 | `compiled-core-scene-configuration-operation-v1` | `sha256:73bac9067f62b62b039ce03f7754d1312f9c0595d0fb16af49851a49c90ba6bb` | 2026.10.3 (seq 15) |
| `core.governed_configuration_operation` | `core_governed_configuration_operation_v1` / 1 | (signed probe profile) | `sha256:d517fa73878f8dafea857b5306db750304d3c65522481898800cf86fb091f441` | 2026.10.0, 2026.10.1, 2026.10.3 |

Planned sequence 16 carries the 31 existing profiles forward to Core
2026.11.0 and adds none (`docs/FEATURE_READINESS_MAP.md`). Sequence 17 is
the next planned batch. This design's profile versions are signed after 17
(register dependency). `CORE_RUNTIME_CAPABILITY_PROFILES` has 32 entries
(`profiles.py:892-900`). The delete and scene profiles are runtime-only, not
members of `CORE_CAPABILITY_PROFILES`, so changing them moves no signed probe
fingerprint (`profiles.py:603-607`, `:632-637`).

**Delete v1 contract** (`profiles.py:608-631`):

- target `one_exact_existing_automation_or_script_configuration`;
- `resource_types: ["automation", "script"]`;
- dispatch `at_most_once_config_delete_no_body`;
- absent target `400_resource_not_found_no_write`;
- readback `config_404_registry_entry_absent_state_absent`;
- recovery `read_only_no_redispatch`.

**Scene v1 contract** (`profiles.py:812-890`):

- target `one_exact_existing_ui_managed_scene_by_config_id_with_homeassistant_registry_entry`;
- requests `SCENE_CONFIGURATION_REQUESTS` (`profiles.py:147-153`): config GET
  and POST, state GET, registry `get` and `list`;
- `never_sent` includes `DELETE /api/config/scene/config/{config_id}`,
  `scene.apply`, `scene.create`, `scene.delete`, `scene.reload` and
  `scene.turn_on`;
- required check `scene_loaded_state_not_verified_disclosed`;
- concurrency
  `no_core_compare_and_swap_upsert_concurrent_edit_overwritten_undetected_concurrent_delete_recreated_disclosed`.

### 2.2 Profile versioning: what the code allows

- `_contract()` hard-codes the version. It sets
  `profile_id = capability_id.replace(".", "_") + "_v1"` and writes
  `"profile_version": 1` into both the fingerprint material (model
  `ha-core-binary-capability-contract-v2`) and the profile
  (`profiles.py:119-141`). **No versioned-profile precedent exists.**
- The coordinator refuses to compile two profiles for one capability
  (`profile_capability_duplicate`), two with the same `(profile_id, version)`
  or two with the same adapter (`ha_core_readmission/coordinator.py:177-187`).
  **A runtime compiles exactly one version per capability.**
- Registry selection keys references by capability:
  `references = {p.capability_id: p for p in entry.capabilities}`. It admits a
  profile only on an exact match of profile_id, profile_version, adapter_id,
  contract_fingerprint and `auto_eligible`
  (`ha_core_readmission/registry.py:58-100`, match at `:70-78`). **A release
  entry references exactly one version per capability.**
- The registry is fetched from the `registry` branch and polled every six
  hours (`registry.py:14-18`; `docs/CORE_RELEASE_REGISTRY.md:117`, `:139`). It
  is not baked into the image. Runtime and registry therefore move
  independently. See §5.1.

### 2.3 Governed creates today

| Family | Resource types | Profile | Evidence |
| --- | --- | --- | --- |
| F3 configuration create/update | `automation`, `script`, `input_boolean`, `input_number` | `core.governed_configuration_operation` | adapters `f3_runtime/runtime.py:1956-1963`; routes `ha_core_readmission/routes.py:35-62`; helper types `governance/resources.py:89` |
| Label create/edit (HAMCP-165) | `label_registry_entry` | label profile | `runtime.py:1964-1968` |
| Scene | `scene_configuration`, **update only** | `core.scene_configuration_operation` | the gateway refuses create: `governance/resources.py:808` ("scene configuration is update-only"); `governance/scene_configuration.py:63-65` |
| Legacy contract-1 automation plan | `automation` (`CREATE_AUTOMATION`) | legacy writer | `governance/service.py:5523-5526`, `:14977-14978` |

Facts about helper create and absence (`governance/resources.py:791-915`):

- Helper create rechecks absence through the storage list and a
  `GET /api/states/<entity_id>` 404. It refuses with `target_already_exists`
  (`:849`) or `target_entity_id_reserved` (`:876`).
- It marks the send, then calls `{type}/create`.
- A returned id that differs from the predicted object id raises
  `generated_identity_mismatch` with `orphan_risk: True` (`:901-913`).
- The generic `write()` accepts only create and update.

### 2.4 Governed deletion today (HAMCP-155)

`ha_mcp_engineering/governance/configuration_delete.py` (401 lines):

- `RESOURCE_TYPES = ("automation", "script")` (`:31`).
- Identities `delete_automation_configuration` and
  `delete_script_configuration` (`:32-35`).
- Intent model `configuration_delete_intent_v1`, with fields (`:39-51`):
  - `model`
  - `resource_type`
  - `target_id`
  - `entity_id`
  - `registry_entry_id`
  - `pre_image_fingerprint`
  - `consumer_evidence_fingerprint`
  - `dependency_reasons`
  - `recreate_eligible`
- Absence polling: 6 attempts at 0.5 s (`:58-59`). A lingering registry entry
  or state reports `config_removed_runtime_unverified` (`:60`).
- `registry_entry` matches `platform == resource_type` and
  `unique_id == target_id`. A duplicate entry is refused (`:234-273`, match
  at `:256`).
- `delete()` refuses any other type before I/O (`:320-327`). It marks the send
  at `:347`. A 4xx response becomes `ConfigurationMutationNotDispatchedError`
  with reason `configuration_delete_rejected` (`:55`, `:352-363`).

Routing (`ha_core_readmission/routes.py`):

- Only the two delete identities map to `core.configuration_delete_operation`
  (`:107-116`, `:1019-1022`).
- The scene update identity maps only to the scene profile (`:161-173`,
  `:1029-1032`).
- Every unknown identity maps to `core.unsupported_f3_operation`.

The public tool schema is
`CONFIGURATION_DELETE_INPUT_SCHEMA`: `resource_type` enum
`["automation", "script"]`, 1-8 deletions (`tools/governance.py:1236-1273`).

Path allowlist (`clients/paths.py:293-310`):

- The scene route is method-bound to `{GET, POST}`. The comment reads "a scene
  configuration DELETE is never reachable" (`:305-308`).
- The automation and script routes are not method-bound.

### 2.5 Rollback today

`create_rollback_plan` (`f3_runtime/runtime.py:5132-5269`) dispatches by plan
family:

- Label plans are refused with `ROLLBACK_NOT_SUPPORTED` (`:5156-5170`).
- Delete plans go to `_create_configuration_delete_rollback` (`:5414-5530`).
  This is the HAMCP-155 recreate, model
  `hamcp155-configuration-delete-recreate-v1` (`:5508`).
- Scene plans go to `_create_scene_configuration_rollback` (`:5840`).
- Everything else goes to `_plan_fresh_rollback` (`:5271-5413`, model
  `f3-governed-rollback-plan-v1`). It excludes any operation whose
  `action != "update"`.

Post-apply availability (`runtime.py:4322-4379`):

- A plan made only of creates gets **`unavailable_for_create`**
  (`:4369-4372`).
- A delete plan is available when `dispatched_once` and `_recreate_eligible`
  hold (`:4334-4342`, `:1414-1420`).

The proof that a create succeeded is already persisted.
`record.evidence["resulting_state_fingerprint"] == operation.proposed_config_hash`
is the equality that HAMCP-135 uses (`governance/configuration_rollback_eligibility.py:89`).

### 2.6 What is missing

1. A scene create path. This needs:
   - a planner action;
   - an absence check;
   - a create permit;
   - a create readback that polls for the new registry identity.
2. Deletion of scenes, `input_boolean` and `input_number`. There is no gateway
   path, profile contract or route method for it.
3. Rollback planning for creates. This needs:
   - an eligibility proof;
   - a delete-family rollback plan;
   - a change to availability marking;
   - a mixed create-and-update reverse order.
4. Versioned profiles (§2.2).

## 3. Proposed design

### 3.1 Answers to the scoping questions

- **Which helper domains does 298 cover today?** The helpers Engineering can
  create: `input_boolean` and `input_number` (`resources.py:89`;
  `runtime.py:1961`). **Both need the profile change.** Delete v1 signs no
  helper delete command and no `{type}/list` absence readback. The exact type
  set is **Q2**.
- **Does rollback-of-create for automations and scripts need a profile
  change?** **No.** Delete v1 already signs the exact
  `DELETE /api/config/{automation|script}/config/<id>`, the 400-absent rule
  and the config-registry-state absence readback.
  - A created automation or script, once verified, is an "existing
    automation or script configuration". The v1 target therefore fits.
  - The work is all on the Engineering side:
    - the eligibility proof;
    - the rollback planner;
    - availability marking;
    - the ADR-018 amendment.
  - One caveat. Once delete v2 replaces v1 (one version per capability,
    §2.2), automation and script rollback also run under v2. Phasing is
    **Q4**.
- **Does rollback-of-create for helpers need a profile change?** **Yes**:
  `input_boolean/delete`, `input_number/delete` and the `{type}/list` absence
  readback (§3.4).
- **Does scene undo need a profile change?** **Yes**: scene `DELETE` in delete
  v2 (Josh decision).
- **Does scene create need a profile change?** **Yes.** Its profile home is
  not decided, and is **Q1**.
  - POST to an absent id is outside the scene v1 target, which says
    "existing".
  - Delete v1 covers only DELETE.
  - The readiness map lists two candidates (`docs/FEATURE_READINESS_MAP.md:464`):
    "a new version of `core.scene_configuration_operation`, or a separate
    create/delete profile".

### 3.2 Scene create (HAMCP-329)

**Tool.** Extend the existing `create_scene_configuration_plan` with an
optional `action: "update" | "create"`. The default is `"update"`, so every
existing call keeps its exact meaning. No tool is added, and the
Engineering-native tool count stays **81** (§4).

When `action` is `create`, the inputs are:

| Field | Rule |
| --- | --- |
| `config_id` | required. `CONFIG_ID_PATTERN` `^[A-Za-z0-9_-]{1,64}$` (`scene_configuration.py:122`). It becomes the scene's registry `unique_id` (`homeassistant/scene.py:369-371`) |
| `name` | required. Part 2 name rule (non-empty, at most 255, exact, no trim) |
| `icon` | optional. Part 2 icon rule; `null` is refused for create |
| `entities` | required. 1..`MAX_ENTITIES`. Every value is a bounded snapshot with `state`; a `null` (merge-patch remove) is refused for create |
| `entity_id` | **refused** for create. Core derives it (below) |
| `expiration_minutes` | unchanged |

**Core facts (2026.11.0):**

- `POST /api/config/scene/config/<id>`, in `components/config/view.py:99-139`:
  - The body is validated with the scene `PLATFORM_SCHEMA` and the key with
    `cv.string` (`components/config/scene.py:42-51`). A failure returns 400
    and nothing is written.
  - Under `mutation_lock`, `EditSceneConfigView._write_value`
    (`config/scene.py:59-89`) builds `{id, name, entities, **body}`.
  - It **replaces every** entry with a matching id (the loop does not break)
    or appends one if none match.
  - It also writes a fresh `uuid4().hex` id into any stored entry that has no
    id (`:79-82`).
  - The file write is atomic (`view.py:252-257`).
  - The post-write hook runs `scene.reload` as a fire-and-forget task
    (`config/scene.py:25-28`; `view.py:134-137`).
  - The response is `{"result": "ok"}`.
- **Entity id.** Core assigns it when the reload adds the entity: the
  entity platform derives the object id from the name
  (`helpers/entity_platform.py:1105`; `_async_derive_object_ids` `:1384-1428`)
  and the registry generates a free id (`helpers/entity_registry.py:1450`,
  `:1518`).
- **Prior registry metadata.** If a registry entry for
  `(scene, homeassistant, <config_id>)` was deleted within 30 days
  (`ORPHANED_ENTITY_KEEP_SECONDS`, `entity_registry.py:97`), `async_get_or_create`
  restores it (`entity_registry.py:1685-1700`):
  - entity_id, if it is still free;
  - area, labels, categories and aliases;
  - `disabled_by` and `hidden_by`;
  - options, including assistant exposure.

  Deleted registry entries are not visible through `config/entity_registry/list`,
  so Engineering **cannot predict the entity_id**. It is disclosed as "assigned
  by Home Assistant" and reported after readback.

**Planning reads.** All are existing scene-profile requests, refused before
I/O while unadmitted:

1. Config GET must return 404. Otherwise refuse `scene_configuration_target_exists`.
2. The registry list must have **no** `scene.*` entry with
   `unique_id == config_id` on any platform. Otherwise refuse
   `scene_configuration_config_id_registered`. This covers a YAML-only
   `homeassistant` scene, an integration scene and a not-yet-purged leftover.
3. Member validation and the protected-member policy follow Part 2 unchanged:
   existence, device-class reads and safety-critical members. Saving never
   activates the scene. The risk disclosure still names members whose stored
   snapshot would set a protected state when the scene is later activated.
4. Consumer disclosure: a new scene has no consumers by identity. The plan
   states that a dangling reference to the eventual entity_id would bind to it.
   Coverage gaps are disclosed as in Part 2.

The plan binds:

- the absence fingerprint `resource_fingerprint("scene_configuration", None)`;
- the exact proposed stored body;
- no entity_id.

**Locks, preflight, dispatch.**

- Locks: the existing per-resource lock on `scene_configuration:<config_id>`
  and the scene reload-domain lock that Part 2 uses.
- Final preflight under the locks repeats reads 1 and 2. Any change is a
  stale-state refusal with zero writes.
- Then come the durable dispatch intent, a single-use **create permit** (the
  Part 2 R1-H2 write permit, extended with `action=create`), `mark_provider_send()`
  and **one** POST. There is no retry. Recovery is read-only.

**Concurrency, disclosed and not prevented.** Core has no compare-and-swap.
A concurrent create of the same id between the preflight GET and the POST is
**overwritten undetected**. This is the Part 2 upsert race, now on the create
side. The `uuid4` repair of id-less entries is a Core side effect on other
stored entries, disclosed as in Part 2.

**Readback.** Polled within a bound because the reload is fire-and-forget: 6
attempts at 0.5 s, the 155 bound. Each attempt checks:

- config GET equals the exact expected stored body (type-exact canonical
  JSON);
- the registry has exactly one `scene.*` entry with `unique_id == config_id`
  and `platform == "homeassistant"`.

On success, the record evidence stores the **created identity**:
`{config_id, entity_id, registry_entry_id, disabled_by}`.

| Observation | Outcome |
| --- | --- |
| Config exact + registry identity present | `succeeded_verified`, with `verification_scope: stored_configuration_and_registry_identity`, `loaded_state: not_verified` and the `scene_loaded_state_not_verified_disclosed` statement. Restored `disabled_by`, `hidden_by`, area or exposure are reported, not treated as failures |
| Config exact, no registry entry after the bound | `scene_config_stored_registry_unverified`: not success and not rollback-eligible (**Q7**) |
| Config differs (for example, a concurrent writer) | readback mismatch: not success |
| Config 404 | not applied, or unresolved if the send was uncertain |

**Never sent, unchanged:** `scene.turn_on`, `scene.apply`, `scene.create`,
`scene.delete` and `scene.reload` (Core sends the reload itself). The scene
gateway itself never sends `DELETE`. Scene `DELETE` is sent only by the
deletion gateway (§3.4).

### 3.3 Rollback for governed creates (HAMCP-298, and HAMCP-329 undo)

`rollback_change(plan_id, plan_hash)` on a plan whose children include
verified creates goes to a new `_create_created_object_delete_rollback`. That
function plans a **new, separately approved** deletion plan in the HAMCP-155
family (`target_type: configuration_deletion`). It dispatches nothing.

- Rollback model: `hamcp298-created-object-delete-rollback-v1`.
- The plan carries `source_plan_id`, `source_plan_hash`, the source task id,
  and for each child the source `operation_id` and record hash.
- Excluded children are listed with their reasons.

**Eligibility.** A created child is included only if every check passes:

1. The source operation has `action == "create"` and a type in
   {`automation`, `script`, `input_boolean`, `input_number`,
   `scene_configuration`}.
2. The record shows:
   - `terminal`;
   - `normalized_outcome == "succeeded_verified"`;
   - `dispatch_intent` present;
   - `dispatch_count == 1`;
   - `evidence.resulting_state_fingerprint == operation.proposed_config_hash`.

   This is the created-state fingerprint, using the HAMCP-135 equality
   (`configuration_rollback_eligibility.py:89`).
3. Current readback fingerprint equals the created fingerprint. A difference
   is refused `created_object_changed`. Absence is excluded as
   `created_object_already_absent`.
4. Registry identity:
   - There is exactly one entry for `(domain, platform, unique_id=target_id)`,
     where the platform is the domain for automation, script and helpers, and
     `homeassistant` for scenes.
   - For scenes, its `registry_entry_id` must equal the created identity
     recorded at verification. A difference is refused
     `created_object_identity_changed`. A scene record without a created
     identity is refused, never inferred.
   - For automation and script, the registry entry appears only after Core's
     post-write reload. It cannot be captured at create verification, so the
     entry observed at rollback planning is bound, and the 155 preflight
     rechecks it. Disclosed.
   - For helpers, the created identity is captured when the create readback
     observes it. Otherwise the automation and script rule applies.
5. The delete profile admits the delete identity for that type
   (`core.route_status`). If not, the response is
   `pending_signed_registry_admission`, with zero Home Assistant reads and
   nothing stored.
6. The source has no cached rollback. The existing `rollback.request_id`
   caching applies (`runtime.py:5172-5175`).

**Rollback plan contents.**

- One HAMCP-155 delete child per eligible created object, in **reverse**
  forward order. The count is at most 8: `MAX_CONFIGURATION_OPERATIONS = 8`
  (`governance/service.py:445`) equals the 155 bound.
- Each child's intent is a standard `configuration_delete_intent_v1`:
  - `pre_image_fingerprint` is the created fingerprint;
  - `consumer_evidence_fingerprint` comes from the fenced dependency index
    (consumers added since the create are disclosed and raise the plan as in
    155 §3.2);
  - `recreate_eligible: true`, so the rollback of this rollback is the 155
    recreate.
- **Mixed forward plans** (creates and verified updates): a single reverse
  plan. Updates get the existing restore child. Creates get a delete child.
  The order is strictly the reverse of the forward order. For example, if an
  automation was updated to reference a newly created helper, the automation
  is restored before the helper is deleted.

  Two separate plans would lose that ordering. If the F3 child sequence cannot
  carry delete and configuration adapters in one plan, the mixed case ships
  later (§9 slice 5), and until then mixed plans keep today's update-only
  rollback with the creates listed as excluded. This is a build risk, not a
  product decision.
- **Policy** (unchanged HAMCP-155/213): delete is in `physical_effects` as
  `configuration_deleted`; protected effects in the created configuration
  become elevated with an acknowledgement; dependency gaps become elevated.
- **Approval:** exact-hash, through Ingress only, with no self-approval. The
  forward approval is never reused.

**Availability marking** (`runtime.py:4322-4379`):

- `unavailable_for_create` becomes `available_pending_current_state_check`
  when at least one created child meets checks 1-2.
- Plans made only of labels keep `ROLLBACK_NOT_SUPPORTED`.
- Legacy contract-1 `CREATE_AUTOMATION` plans keep `unavailable_for_create`
  (**Q6**).

### 3.4 Delete v2: the deletion gateway for scenes and helpers

`ConfigurationDeleteGateway` gains per-type commands. The automation and
script behaviour is byte-for-byte unchanged.

| Type | Write (once) | Absent target | Registry match | Absence readback (polled 6 × 0.5 s) |
| --- | --- | --- | --- | --- |
| `automation` / `script` | `DELETE /api/config/<type>/config/<id>`, no body (unchanged) | 400 "Resource not found", no write | `(type, type, id)` | config 404 + registry absent + state absent (unchanged) |
| `scene_configuration` | `DELETE /api/config/scene/config/<config_id>`, no body | 400 "Resource not found", no write (`view.py:142-151`) | `(scene, homeassistant, config_id)` | config GET 404 + registry absent + `GET /api/states/<entity_id>` 404 |
| `input_boolean` / `input_number` | WS `input_boolean/delete` `{input_boolean_id}` / `input_number/delete` `{input_number_id}` | WS error `not_found`, then a stray result frame (below) | `(domain, domain, id)` | `{type}/list` lacks id + registry absent + state 404 |

**Core facts:**

- **Scene delete.**
  - `view.py:142-159` writes atomically.
  - `EditIdBasedConfigView._delete_value` pops only the **first** matching id
    (`view.py:233-241`).
  - The hook does **not** reload. It resolves
    `ent_reg.async_get_entity_id(SCENE_DOMAIN, HOMEASSISTANT_DOMAIN, key)` and
    calls `async_remove`, which removes the entity and its state, as a
    fire-and-forget task (`config/scene.py:30-40`; `view.py:157`).
  - A duplicate id leaves a 200 GET after the DELETE, which is a readback
    mismatch (155 rule).
  - Core keeps a `DeletedRegistryEntry` for 30 days.
- **Helper delete.**
  - `ws_delete_item` awaits `async_delete_item` (`helpers/collection.py:764-777`).
  - That pops the item, schedules the storage save after `SAVE_DELAY = 10` s
    (`:31`, `:356-369`) and awaits `notify_changes`. The listener
    `_remove_entity` removes the registry entry, or force-removes the entity
    (`:479-490`), **before** the reply.
  - The collection serves only storage items. YAML helpers live in a separate
    collection that shares the `IDManager`
    (`input_boolean/__init__.py:105-131`; `input_number/__init__.py:160-184`;
    the same wiring at 2026.10.3, `input_boolean/__init__.py:95-119`,
    `input_number/__init__.py:116-140`). A YAML helper is therefore never
    deleted.
- **Upstream quirk.** On `ItemNotFound`, `ws_delete_item` sends
  `ERR_NOT_FOUND` and then falls through to `send_result` for the same message
  id (`collection.py:771-777`).
  - The client must treat the **first** frame as authoritative and drop the
    second.
  - A not-found error is a confirmed non-mutation: `ItemNotFound` is raised
    before the pop.
  - A regression test pins this (§6).
- **Persistence window, disclosed.** Helper absence is verified in the
  runtime and registry. The storage file is written up to 10 s later, so a
  Core stop inside that window can bring the helper back. Helper create has
  the same window today.

**Route and permits.**

- `clients/paths.py` `_route_methods` for `/config/scene/config/{}` becomes
  `{GET, POST, DELETE}`.
- `DELETE` additionally requires a **single-use delete permit**, mirroring the
  Part 2 R1-H2 write permit. It is bound to plan, task, operation, exact
  target, exact command and the recorded dispatch intent. Only the deletion
  gateway installs it, and any other `DELETE` is refused before a session
  exists.
- Helper delete goes through a closed command builder. Only those two
  commands are possible, and every other `{type}/delete` is refused.

**Identities and routing.**

- New F3 identities `delete_scene_configuration_configuration`,
  `delete_input_boolean_configuration` and `delete_input_number_configuration`
  map only to `core.configuration_delete_operation`.
- `create_scene_configuration_configuration` maps only to the Q1 profile.
- `f3_requirements()` keeps sending every unknown identity to
  `core.unsupported_f3_operation`.

**Reachability.**

- The public `create_configuration_delete_plan` enum stays
  `["automation", "script"]`.
- Scene and helper delete children are constructed **only** by the
  rollback planner from a verified Engineering create. The service enforces
  this with a provenance field on the plan, `source: created_object_rollback`,
  bound into the plan hash. The executor refuses scene or helper delete
  children without it.
- The same rule is part of the v2 signed contract (`reachability`, §5).

### 3.5 Partial failure and recovery

- Forward create plans keep today's semantics: ordered, non-atomic, stop on
  the first failure.
- Rollback plans: ordered, non-atomic, stop on the first failure; unattempted
  children are marked; no automatic compensation.
- An uncertain send is resolved by readback only, and a delete is never
  redispatched (155 §1). Absent means verified. Present means not applied or
  unresolved.
- A child that read back `config_removed_runtime_unverified` is reported as
  such and is not recreate-eligible.

## 4. Public schema and tool-registration impact

- **Tool count: unchanged.** Engineering-native tools: 81 at the base
  (`len(CAPABILITIES) + len(BETA_NATIVE_CAPABILITIES)` = 25 + 56, computed from
  base source; the formula is `application.py:278-280`). No tool is added or
  removed. The delegated count is dynamic and untouched.
- **`create_scene_configuration_plan`** (`tools/governance.py:1730-1778`) gains
  the additive optional `action` enum `["update", "create"]`, default
  `"update"`. With `create`:
  - `name` and `entities` are required;
  - `entity_id` and `null` member values are refused.

  Under `update`, every existing request validates and behaves identically.
  The security-sensitive surface is a public input schema change. A
  regression test pins that update requests are identical.
- **`create_configuration_delete_plan`**: schema unchanged
  (`tools/governance.py:1236-1273`).
- **`rollback_change`**: schema unchanged. New response values:
  - `rollback_model: hamcp298-created-object-delete-rollback-v1`;
  - exclusion reasons `created_object_changed`,
    `created_object_already_absent`, `created_object_identity_changed` and
    `created_object_not_verified`.
- **Plan and record surfaces:**
  - availability status `available_pending_current_state_check` for create
    plans;
  - scene create evidence `created_identity`;
  - scene create disclosure (`scene_loaded_state_not_verified_disclosed`,
    "entity_id assigned by Home Assistant", the prior-metadata restore and the
    upsert race).
- **Catalog and health:**
  - `capabilities.py:431` lists `resource_types: [automation, script]` for the
    public delete tool, which is unchanged;
  - `capabilities.py:528` lists the scene tool, which gains a `create`
    description;
  - `core_admission` reports both profiles.

## 5. Signing impact

| Profile id | New or changed (from version X) | Core commands / contract added | Why | Fingerprint impact | Can it batch with other 2.6 profiles |
| --- | --- | --- | --- | --- | --- |
| `core.configuration_delete_operation` | **Changed, from version 1** (`core_configuration_delete_operation_v1`, adapter `compiled-core-configuration-delete-operation-v1`, `sha256:5688af5d141b97ca33eadf251ecfbb8f3b1f833024b0ee1acea4973f558f887d`; signed for 2026.10.1 since seq 13 and 2026.10.3 since seq 14, carried in seq 15) **to version 2** (`core_configuration_delete_operation_v2`, adapter `compiled-core-configuration-delete-operation-v2`) | REST `DELETE /api/config/scene/config/{config_id}` (no body; domain `scene`, registry platform `homeassistant`); WS `input_boolean/delete` `{input_boolean_id}` and `input_number/delete` `{input_number_id}`; absence-readback reads `GET /api/config/scene/config/{config_id}` (404), WS `input_boolean/list` and `input_number/list`, `config/entity_registry/list`, `GET /api/states/{entity_id}`. `resource_types` grows from `[automation, script]` to `[automation, script, scene, input_boolean, input_number]` (helper set: Q2). Contract adds a per-type `absent_target` (REST 400 no write; WS `not_found` no write, duplicate result frame ignored), per-type readback, `reachability` (automation and script through the public delete tool or created-object rollback; scene and helpers through created-object rollback only), and `never_sent` (`scene.apply`, `scene.create`, `scene.delete`, `scene.reload`, `scene.turn_on`, `input_boolean/create`, `input_boolean/update`, `input_number/create`, `input_number/update`). Automation and script commands unchanged | Josh decision 2026-11-07 (scenes, new version shared with 298); 298 outcome (helpers). Undo of a scene or helper create needs a signed delete command, and v1 has none | **New fingerprint. Value unknown until computed from merged code**; it cannot be derived before the contract text merges. Version 2 needs `_contract()` to take a `profile_version` (§2.2). All existing v1 fingerprints must stay byte-identical (regression test). Every carried Core release entry (2026.10.1, 2026.10.3, 2026.11.0 and any later carried release) must switch its reference from v1 to v2 **in the same sequence**, each with exact v2 contract evidence for that release. A release left on v1 loses all deletion on a v2 runtime (§5.1). Probe fingerprints are unaffected (runtime-only profile) | **Yes**: one sequence after 17, together with the scene-create profile row below and any other 2.6 profiles. It must be the same sequence that re-references every carried release |
| `core.scene_configuration_operation` | **Pending Q1.** If option A: **changed, from version 1** (`core_scene_configuration_operation_v1`, adapter `compiled-core-scene-configuration-operation-v1`, `sha256:73bac9067f62b62b039ce03f7754d1312f9c0595d0fb16af49851a49c90ba6bb`; signed for 2026.10.3 in seq 15, carried to 2026.11.0 in planned seq 16) **to version 2** (`core_scene_configuration_operation_v2`, adapter `…-v2`). If option B or C: unchanged here | Option A adds a create target: `one_exact_absent_config_id_with_no_scene_registry_entry`, using the same `POST /api/config/scene/config/{config_id}` already in `SCENE_CONFIGURATION_REQUESTS`; create arguments (name required, every member a snapshot, no null, no entity_id); absence check (config 404 plus no `scene.*` registry entry with that unique_id); polled readback of the created registry identity; concurrency `upsert_concurrent_create_of_same_id_overwritten_undetected_disclosed`. No new route or command. `never_sent` unchanged, so scene `DELETE` stays never-sent for this profile | 329 outcome: create a new scene. Scene v1's target is "existing" only, and delete v1 or v2 is a delete contract | Option A: **new fingerprint, unknown until merged**. Every carried release (2026.10.3, 2026.11.0) moves v1→v2 in one sequence. Scene edit (Part 2) is withdrawn on any runtime and registry pairing where the versions differ (§5.1) | **Yes**: same sequence as the delete v2 row |
| `core.governed_configuration_operation` | **Unchanged** (version 1, `sha256:d517fa73878f8dafea857b5306db750304d3c65522481898800cf86fb091f441`) | None | Creates of automation, script, `input_boolean` and `input_number` are already signed. Rollback of those creates sends a delete under the delete profile, never under this one | None | Not applicable |

### 5.1 The v1→v2 transition window (one version per capability)

A runtime compiles one version per capability (§2.2). A registry release entry
references one version per capability. The registry is fetched independently
of the image. So:

- A **v2 runtime with a registry that still references v1** withdraws every
  deletion: automation and script deletes, the HAMCP-155 recreate's source,
  and 298 rollbacks. Under option A it also withdraws scene edit. The status
  is `pending_signed_registry_admission`, fail-closed.
- A **v1 runtime with a registry that references v2** loses the same
  capabilities on the currently deployed Engineering.

No ordering avoids both cases. The window is the time between signing and
deploying. Whether to accept the window, and which side of it, is **Q3**.
Supporting two versions per capability is a registry-model change outside
this scope. Josh's decision is a new version, so the design does not propose
a sibling capability id to avoid the window.

## 6. Negative reachability, refusals and tests

| Path | Refusal | Test (new unless noted) |
| --- | --- | --- |
| Scene create while the Q1 profile is unadmitted | `pending_signed_registry_admission`, zero HA reads, nothing stored | admission-first test, with a counted fake transport |
| Scene create for an existing id or a registered unique_id | `scene_configuration_target_exists` / `scene_configuration_config_id_registered`, no write | planning-refusal tests; preflight-stale test (id appears after planning) |
| Scene create with `entity_id`, a `null` member, a missing name or entities | schema or planner refusal before reads | schema tests; update-request identity regression |
| Scene `DELETE` without the delete permit, or from the scene gateway | refused before transport (`paths.py`) | permit-missing, permit-reuse and wrong-target tests; scene gateway `NEVER_SENT` unchanged (existing) |
| Scene services | `scene.turn_on`, `scene.apply`, `scene.create`, `scene.delete` and `scene.reload` never sent | existing never-sent tests extended to the create and delete paths |
| Public delete tool with scene or helper types | schema enum refusal; planner `unsupported_resource_type` before reads | schema-unchanged test; direct-service-call refusal |
| Scene or helper delete child without `source: created_object_rollback` | executor refusal before intent | forged-plan test |
| Helper `{type}/delete` for any other type | closed builder refusal | WS allowlist test |
| `ERR_NOT_FOUND` followed by a duplicate result frame | first frame authoritative → `sent_rejected_no_mutation`; second frame dropped without mis-correlation | fake-WS quirk test |
| Rollback of an unverified, uncertain or undispatched create | excluded (`created_object_not_verified`) | eligibility matrix |
| Rollback after the created object changed, or its identity changed | `created_object_changed` / `created_object_identity_changed`, zero writes | stale tests at planning and preflight |
| Rollback when already absent | excluded, nothing sent | test |
| Label-create rollback | `ROLLBACK_NOT_SUPPORTED` unchanged (existing) | existing test |
| Generic `create_configuration_plan` with `delete` | refused (existing) | existing test |
| v1/v2 profile mismatch | `pending_signed_registry_admission` for every delete identity, scene identities under option A | coordinator and selection tests with test-signed registries referencing v1 and v2 |
| Existing profile fingerprints | byte-identical after the `_contract()` change | regression over all 32 profiles |
| Uncertain delete send | readback only, never redispatched (existing `BlindRedispatchProhibited`) | recovery tests per new type |

Mutations, run under the heavy-job rules in the build PRs and not here:

1. drop the scene create absence recheck;
2. drop the created-fingerprint check;
3. drop the scene `registry_entry_id` binding;
4. accept a delete without the permit;
5. treat the second WS frame as the reply;
6. skip the polled create readback;
7. expose scene in the public delete enum.

## 7. Acceptance

**Useful authorized success** (test-signed registry, fake Core, end to end):

1. Scene create: plan → approve → one POST → verified (config exact, registry
   identity captured) with the loaded-state disclosure. Then `rollback_change`
   → separate approval → one scene `DELETE` → absence verified. The 155
   recreate restores the same config id.
2. Automation create, then a rollback that deletes it, under **delete v1**
   (Q4 phasing) and again under v2.
3. `input_boolean` and `input_number` create, then a rollback that deletes
   them.
4. A mixed plan (create a helper, update an automation to reference it). The
   rollback restores the automation first, then deletes the helper.

**Refusal:** every row of §6.

**Failure preservation:**

- A 400 on DELETE, or a WS not-found, gives `sent_rejected_no_mutation`.
- An uncertain send gives a readback decision.
- `config_removed_runtime_unverified` is reported as not success.
- `scene_config_stored_registry_unverified` is reported as not success.
- A partial rollback stops at the first failure, with the remaining children
  marked.

**Verification:** each exact readback in §3.2 and §3.4, polled within a
bound.

**Recovery:**

- A process loss after the intent resolves by readback only.
- The rollback of the rollback (the recreate) is offered only for
  `succeeded_verified` delete children.

**Post-signing canary** (a separate, Josh-authorized step, not part of this
design):

1. Create one test scene, verify it, roll it back and verify absence.
2. Do the same for one test automation and one test helper.

## 8. Security

- **New write reachability** (an architecture change under root
  `AGENTS.md`):
  - scene `DELETE`;
  - `input_boolean/delete` and `input_number/delete`;
  - scene create through POST to an absent id.

  Each is:
  - bound to an exact identity and a signed profile;
  - admission-checked before any read;
  - sent at most once after a durable intent;
  - verified by exact readback;
  - negative-tested (§6).
- **Bounded deletion.** Scene and helper deletion is reachable only as the
  undo of a verified Engineering create. The proof is the fingerprint
  equality plus the registry identity. Deletion is never offered as a general
  tool, so Engineering cannot delete a scene or helper it did not create.
- **No provider bypass.**
  - The provider boundary stays `direct_ha_api`.
  - There is no ha-mcp delegation, fallback or direct-HA path outside the two
    gateways.
  - Upstream `ha_config_remove_*` stay unrouted.
  - Legacy `delete_automation` stays prohibited (155 §4).
- **Persistence.**
  - New record evidence `created_identity` (scene creates), which is additive.
  - New plan provenance `source: created_object_rollback` and rollback model,
    both bound into the plan hash.
  - Records that lack the new keys are refused, never inferred.
  - No historical persisted format is rewritten, and no compatibility fixture
    is needed. Older plans are read as before.
- **Signing.**
  - Two runtime-only profile versions.
  - No probe fingerprint moves.
  - Fingerprints come from merged code only.
  - The transition window fails closed (§5.1).
- **Disclosure, not prevention.** The following are stated in every relevant
  plan:
  - the upsert race;
  - the `uuid4` repair of id-less entries;
  - the prior registry metadata restore;
  - the 10 s helper storage window;
  - the asynchronous registry creation for automation, script and scene;
  - loaded state not verified.
- **Secrets.** None are read or added. No live Home Assistant access is used
  for this design.

## 9. Build plan

Every implementation PR targets `release/2.6.0`. Each is a draft with
independent review. 2.5.0 lands first and **wins shared files**.

- #387 (HAMCP-288) touches `application.py`, `models/responses.py`,
  `tests/test_f3_runtime_integration.py`, `tests/test_beta_v2.py` and
  `tests/test_f3_readiness.py`.
- #384 (HAMCP-309 Part 3) touches `docs/CORE_RELEASE_REGISTRY.md` and
  `scripts/core_evidence_workflow.py`, which the signing step uses.
- #386 touches helper value control only, with no overlap.

Catch up with `release/2.5.0` by merge, never by rebase.

| Slice | Content | Profile or signing effect | Depends on |
| --- | --- | --- | --- |
| 1 | `_contract(profile_version=1)` support (`_v{n}` id, versioned material); ADR-018 amendment recording the superseding decisions (after Q5) | none (regression: 32 fingerprints unchanged) | 2.5.0 merged |
| 2 | 298 for automation and script under **delete v1**: eligibility proof, `_create_created_object_delete_rollback`, availability marking, create-only plans | none; runs on the signed v1 | slice 1; Q4 |
| 3 | Delete v2: scene and helper gateway commands, the delete permit, the route method, identities, `reachability` enforcement, the WS quirk; 298 for helpers and the scene undo | **compiles v2 in place of v1.** Merging it into a released image starts the §5.1 window, so it ships only in an image whose deploy follows the signing (Q3) | slice 2; Q2, Q3 |
| 4 | Scene create (`action: create`, absence check, create permit, polled readback, created identity) under the Q1 profile | Q1 profile version (option A: scene v2) | slice 3; Q1 |
| 5 | Mixed create-and-update reverse plans, if the F3 sequence needs work | none | slice 2 |
| 6 | Signing (Josh only): one sequence **after 17** re-referencing every carried release to delete v2 (and to scene v2 under option A), with per-release contract evidence | new fingerprints | slices 3-4 merged; built image provenance |

**Rollback of the implementation.**

- Slices 1, 2 and 5: revert.
- Slices 3 and 4: reverting to a v1 runtime while the registry references v2
  withdraws deletion (§5.1). It needs a re-signed sequence referencing v1, or
  an accepted withdrawal. Josh decides.

## 10. Open questions — pending Josh

- **Q1. Profile home for scene create.** Not covered by the 329 decision, which
  places only the undo in the delete profile. The options:
  - **(A)** `core.scene_configuration_operation` v2 adds create. This keeps
    create next to the scene readback contract. Cost: scene edit enters the
    v1→v2 window.
  - **(B)** Delete v2 also carries the scene create POST. Only one profile
    changes. Cost: a contract named "delete" authorizes a create.
  - **(C)** A new sibling profile for scene create. There is no window for
    scene edit. Cost: a new capability id, and readiness map row 1 named it
    only as an alternative.

  The author's analysis favours A. That is not a decision.
- **Q2. Helper types in delete v2.** The proposal is `input_boolean` and
  `input_number`, which are today's governed creates. Readiness map row 2 gates
  helper delete on "owner scope (types)". Future storage-helper types would
  join through a later version once their create exists.
- **Q3. The v1→v2 transition window** (§5.1). Accept the fail-closed
  withdrawal of all deletion (and scene edit under option A) between signing
  and deploying, and choose the order: sign first or deploy first.
- **Q4. Phasing.** May 298 for automation and script ship under the signed
  delete v1 (slice 2) before v2 is signed? Or must 298 ship as one unit with
  helpers and scenes?
- **Q5. ADR-018 supersession.** ADR-018 prohibits create-through-delete. The
  329 decision covers scenes. Confirm that the 298 register outcome is Josh's
  superseding direction for automations, scripts and helpers, so the amendment
  can record it as such.
- **Q6. Legacy contract-1 `CREATE_AUTOMATION` plans**
  (`governance/service.py:5523-5526`). Are they in 298 scope, or do they keep
  `unavailable_for_create`?
- **Q7. A created scene that is stored but never registered.** The registry
  entry may not appear within the bound, for example if the reload fails. The
  config then exists with no registry identity. The 155 rule refuses a delete
  without a registry entry, so no governed undo exists. The options:
  - keep that rule (fail-closed, the author's default);
  - allow a config-only scene delete with a weaker readback (config 404 only),
    which deviates from HAMCP-155.
