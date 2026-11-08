# HAMCP-329 governed scene create and HAMCP-298 rollback for governed creates — design

Status: **2.6.0 design, docs only.** Written 2026-11-07 (CST); corrected
2026-11-08 (CST) after the Codex design review of PR #393 (§0), then
updated the same day to record Josh's in-session decisions on items 12-15
(§0.1). This
document does not authorize implementation outside its build plan,
deployment, live Home Assistant access, registry signing, a signed-profile
change or a release. Codex reviews it; the author does not review its own
work.

Base: `origin/release/2.6.0` `7246f2d21693f039504542574569f03b71476489`
(Engineering `2.4.2-beta.7` materialized; the same commit as the
`release/2.5.0` tip). Every Engineering `file:line` below is at this base.
Paths under `hass_mcp_engineering_beta/ha_mcp_engineering/` are shortened to
`ha_mcp_engineering/`.

Current comparison points (read 2026-11-08, used only where named):

- integration `origin/release/2.6.0` `1c4c32b3c2da152bd42f25178b077c62c13af7d4`;
- signed registry, `registry` branch `e486028ed0b06b8a5a737824c829705b403b34d8`,
  envelope sequence 17, which carries Core 2026.10.0, 2026.10.1, 2026.10.3
  and 2026.11.0.

Upstream authority (ADR-021): Home Assistant Core **2026.11.0**, commit
`74cf5a49c63ed8d4d87717844bf14f79c28c9cea`, archive SHA-256
`85f3c42c9f77aa45e01faff4471007beadf5cb50c4324f496a239139eec70ff1`
(digest re-verified 2026-11-08 before use; the registry's 2026.11.0 entry
names the same archive). This is the HAMCP-280 exact stable target
(`scripts/core_lane_bindings.py:59-63`) and the **only** Core release the new
profiles in this design are signed for. Upstream line numbers are at
2026.11.0. Upstream paths are under `homeassistant/`.

> Superseded (historical): the 2026-11-07 text also read Core 2026.10.3 and
> claimed `config/scene.py`, `config/view.py` and `helpers/collection.py` were
> byte-identical there, so that a delete v2 could be re-referenced on every
> carried release. That claim is not relied on: the new profiles target
> 2026.11.0 only, and the existing profiles are not changed on any release.

## 0. Codex design review 2026-11-08 — disposition

Review: one top-level comment on PR #393 at head `a306b4d8`, verdict "not
ready". Every finding is corrected in this revision.

| ID | Severity | Finding (short) | Corrected in |
| --- | --- | --- | --- |
| H1 | High | Replace the version-switch architecture with additive profiles | §1 (binding direction), §3.1 (architecture and routing), §5 (profile table, proving tests), §5.1 (old analysis labelled superseded), §9 (build order and implementation rollback), §10 (Q1-Q5 withdrawn). Existing `core.configuration_delete_operation` and `core.scene_configuration_operation` stay byte-identical. Scene create/delete gets its own new profile. Helper delete gets a second new profile (§3.1; first proposed by the correction author, now Josh decision (in-session 2026-11-08), item 12, see §0.1). Automation and script create rollback uses the already-signed delete contract unchanged. Both new profiles are signed for Core 2026.11.0 only, in the one combined 2.6 signing |
| H2 | High | Ownership proof is not established by rollback-time identity, and Core restores the registry entry id | §3.2 (creation-time identity capture; what Core restores; the remaining observable-identity limitation), §3.4 (eligibility needs creation-time identity; outcome table for the four replacement cases), §8 (the categorical "never deletes an object Engineering did not create" claim is removed and replaced by an accurate limitation), §10 Q-A (strictness was left to Josh; now decided as option E, Josh decision (in-session 2026-11-08), item 13, see §0.1) |
| M3 | Medium | Helper target identity must be converted, hash-bound and correspondence-checked | §3.2.2 (identity tuple, correspondence rules), §3.5 (exact delete argument, state readback by actual entity id), §6 (tests for both domains, mismatch and cross-domain refusal, rename, exact argument) |
| M4 | Medium | Mixed-plan rollback cannot be deferred as an implementation choice | §3.4.4 (one reverse plan is the acceptance outcome; prerequisite rule for excluded or failed children), §7 (acceptance), §9 (no deferral slice; escalation only for a specific proven-unsupported combination) |
| — | (review text) | Admission before reads; helper save delay is not disk durability; persistence/downgrade gate; actual adapter id; seq 15/16 as frozen-base history | §3.4.1 (numbered execution order, admission first), §3.5 (10 s delayed save and the final-write flush), §8 (persistence and downgrade gate with exact-historical-writer fixtures), §2.1 (adapter id `compiled-core-governed-configuration-operation-v1`; base-history label) |

## 0.1 Josh decisions 2026-11-08 — disposition

Josh answered the open choices for this design in-session on 2026-11-08
(items 12-15 of the 2.6.0 choices list). He accepted each recommendation as
written. The §0 Codex corrections stand; only the two §0 rows named below
change. Each one moves a choice that was open into a recorded decision.

| Item | Decision (attribution: Josh decision (in-session 2026-11-08), item N) | Sections changed |
| --- | --- | --- |
| 12 | (a) **Two** additive 2026.11.0 profiles: `core.scene_create_delete_operation` and `core.created_helper_delete_operation`. The 2026-11-08 Codex correction had recorded the split as the correction author's own decision. It is now Josh's decision, and the earlier framing is withdrawn | §0 H1 row, §3.1, §10 (no change to the §5 profile table, which already lists both) |
| 13 | Q-A option **E**. If only `modified_at` moved, the child is included with the disclosure and an elevated acknowledgement. A different registry `id`, `created_at` or body is still refused. E replaces the earlier "if unanswered, S is the fail-closed default"; options S and O are no longer live | §0 H2 row, §3.2.1, §3.4.2 (new "Option E contract"), §3.4.3, §3.5, §4, §6 (negative and positive E tests), §6 mutations, §7, §8, §9 slice 2, §10 |
| 14 | Q6 (a): legacy contract-1 `CREATE_AUTOMATION` plans keep `unavailable_for_create` | §3.4.4 availability marking, §6, §10 |
| 15 | Q7 (a): a created scene that is stored but never registered within the bound is non-success and not rollback-eligible | §3.3 readback table, §6, §10 (§7 already reports it as not success) |

No item conflicts with the stated architecture or safety constraints.
Nothing else in this document is attributed to Josh beyond these items and
the earlier direction in §1.

## 1. Register outcomes and binding direction

**HAMCP-329** (register outcome, quoted):

> Engineering creates a new scene by exact config id with approval, verifies
> the stored configuration and registry identity exactly (loaded state
> disclosed as unverified), and undoes it by deleting exactly that scene; the
> existing core.configuration_delete_operation profile gains scenes in a new
> version, shared with HAMCP-298 rollback for governed creates; scene
> activation stays never-sent.

The clause "the existing core.configuration_delete_operation profile gains
scenes in a new version" is **superseded** by the same row's Next Action
(below). Everything else in the outcome stands.

**HAMCP-298** (register outcome, quoted):

> A plan that creates an automation, script or helper produces a rollback that
> deletes exactly that created object (bound to its identity and fingerprint,
> refused if it changed), instead of requiring a separately written delete
> plan. Generalizes the HAMCP-071 Part 3 idea beyond scenes, reusing governed
> deletion (HAMCP-155).

Dependencies: HAMCP-155, HAMCP-071. Source of the 329 scope:
`docs/FEATURE_READINESS_MAP.md` row "HAMCP-071 Part 3" (`:59-80`,
RCG-004..007); the row requires that `scene_loaded_state_not_verified_disclosed`
carry over from Part 2.

**Binding direction.** Josh direction (register 2026-11-07; confirmed
in-session 2026-11-08):

- Additive profiles only. An existing signed profile is never version-bumped
  or replaced. Existing fingerprints stay byte-identical.
- Scene create/delete gets a **separate, new** scene profile. The rule applies
  to HAMCP-298 too.
- New 2.6 profiles are signed for **Core 2026.11.0 only**, in **one combined
  2.6 signing**: one `extend-capabilities` extension of the existing 2026.11.0
  registry entry. No 2026.10.x targets and no separate or phased 2.6 signing.

**Other recorded decisions this design relies on** (cited from their
records, unchanged):

- HAMCP-071 option C, recorded at `ha_core_readmission/profiles.py:806-809`:
  verified means the stored configuration plus the registry identity; loaded
  state is not verified.
- HAMCP-155 decisions, recorded in `docs/HAMCP_155_GOVERNED_DELETION_DESIGN.md`
  §0, §2-§3: delete semantics; polled absence readback; a recreate rollback;
  protected-effect and consumer disclosure; 1-8 children, ordered,
  non-atomic; Ingress-only exact-hash approval.
- ADR-018 (`docs/architecture/ADR-018-F3-GOVERNED-ROLLBACK.md`) says
  "Create-through-delete is prohibited" and "No generalized … deletion …
  becomes reachable". The approved HAMCP-329 and HAMCP-298 register outcomes
  ask for exactly the undo of an Engineering create, so for these five types
  they supersede that prohibition. Under ADR-022 the superseding direction is
  recorded in an ADR-018 amendment (build slice 1, §9). The amendment must not
  use the old prohibition to narrow the requested create-rollback outcome.
  Bounded deletion still holds: no general-purpose scene or helper deletion
  becomes reachable (non-goals below).

> Superseded (historical): the 2026-11-07 revision recorded a same-day
> "extend the existing signed delete profile to scenes … in a new version"
> decision from the task prompt. The HAMCP-329 Next Action records that
> Josh replaced it the same day with the separate additive scene profile
> above.

**Non-goals.** Adding any of these is a scope change; this design stops at
that line.

- Public, general-purpose deletion of scenes or helpers through
  `create_configuration_delete_plan`. Both outcomes ask only for undo of an
  Engineering create.
- Rollback of label creates. Labels are not helpers. HAMCP-165 keeps
  `ROLLBACK_NOT_SUPPORTED`.
- Scene activation (`scene.turn_on`, `scene.apply`), dynamic scenes
  (`scene.create`) and `scene.reload`.
- Helper types other than the existing governed creates, `input_boolean` and
  `input_number` (`governance/resources.py:89`). A future storage-helper type
  joins only with its own create and its own reviewed permission.
- Any change to an existing signed profile.

## 2. Current state at the base

### 2.1 Signed Core profiles involved

All three profiles are built by `_contract()` (`ha_core_readmission/profiles.py:109-142`).
The references below were read from the signed registry at `e486028e`
(sequence 17), 2026.11.0 entry, and equal the fingerprints recomputed from
base source.

| Capability | Profile id / version | Adapter | Fingerprint | Signed for (seq 17) |
| --- | --- | --- | --- | --- |
| `core.configuration_delete_operation` | `core_configuration_delete_operation_v1` / 1 | `compiled-core-configuration-delete-operation-v1` | `sha256:5688af5d141b97ca33eadf251ecfbb8f3b1f833024b0ee1acea4973f558f887d` | 2026.10.1, 2026.10.3, 2026.11.0; not 2026.10.0 |
| `core.scene_configuration_operation` | `core_scene_configuration_operation_v1` / 1 | `compiled-core-scene-configuration-operation-v1` | `sha256:73bac9067f62b62b039ce03f7754d1312f9c0595d0fb16af49851a49c90ba6bb` | 2026.10.3, 2026.11.0 |
| `core.governed_configuration_operation` | `core_governed_configuration_operation_v1` / 1 | `compiled-core-governed-configuration-operation-v1` | `sha256:d517fa73878f8dafea857b5306db750304d3c65522481898800cf86fb091f441` | 2026.10.0, 2026.10.1, 2026.10.3, 2026.11.0 |

**This design changes none of the three.** The 2026.11.0 entry at sequence
17 references 32 capabilities; `CORE_RUNTIME_CAPABILITY_PROFILES` has 32
entries at the base (`profiles.py:892-900`). The delete and scene profiles
are runtime-only, not members of `CORE_CAPABILITY_PROFILES`, so they move no
signed probe fingerprint (`profiles.py:603-607`, `:632-637`). The new
profiles in §5 are runtime-only in the same way.

> Frozen-base history (not current signing readiness): at the base, the
> repository's committed copy of `upstream-trust/ha-core-release-registry.json`
> has tip sequence 15 (2026.10.0, 2026.10.1, 2026.10.3), and the readiness map
> described a "planned sequence 16" carrying the existing profiles to
> 2026.11.0. The signed registry has since reached sequence 17 with
> 2026.11.0 present. The next sequence number, the new profile IDs and
> fingerprints, and the aggregate capability count remain unknown until the
> implementation is materialized and signed.

**Delete v1 contract** (`profiles.py:608-631`), unchanged:

- target `one_exact_existing_automation_or_script_configuration`;
- `resource_types: ["automation", "script"]`;
- requests `rest_config_automation_script_get`,
  `rest_config_automation_script_delete_no_body`,
  `websocket_entity_registry_list`, `rest_entity_state_get`,
  `config_registry_state_absence_readback`;
- dispatch `at_most_once_config_delete_no_body`;
- absent target `400_resource_not_found_no_write`;
- readback `config_404_registry_entry_absent_state_absent`;
- recovery `read_only_no_redispatch`.

**Scene v1 contract** (`profiles.py:812-890`), unchanged:

- target `one_exact_existing_ui_managed_scene_by_config_id_with_homeassistant_registry_entry`;
- requests `SCENE_CONFIGURATION_REQUESTS` (`profiles.py:147-153`): config GET
  and POST, state GET, registry `get` and `list`;
- `never_sent` includes `DELETE /api/config/scene/config/{config_id}`,
  `scene.apply`, `scene.create`, `scene.delete`, `scene.reload` and
  `scene.turn_on`;
- required check `scene_loaded_state_not_verified_disclosed`;
- concurrency
  `no_core_compare_and_swap_upsert_concurrent_edit_overwritten_undetected_concurrent_delete_recreated_disclosed`.

### 2.2 Why additive profiles (code facts)

These facts explain why a version switch is not the plan. They are kept as
background; §5.1 holds the superseded analysis.

- `_contract()` hard-codes version 1: `profile_id = capability_id.replace(".", "_") + "_v1"`
  and `"profile_version": 1` in both the fingerprint material (model
  `ha-core-binary-capability-contract-v2`) and the profile
  (`profiles.py:119-141`). New profiles in this design are version 1 of new
  capabilities, so `_contract()` needs **no change**.
- The coordinator refuses two profiles for one capability
  (`profile_capability_duplicate`), two with the same `(profile_id, version)`
  or two with the same adapter (`ha_core_readmission/coordinator.py:177-187`).
- Registry selection keys references by capability and admits a profile only
  on an exact match of profile_id, profile_version, adapter_id,
  contract_fingerprint and `auto_eligible`
  (`ha_core_readmission/registry.py:58-100`, match at `:70-78`).
- The registry is fetched from the `registry` branch and polled every six
  hours (`registry.py:14-18`; `docs/CORE_RELEASE_REGISTRY.md:117`, `:139`).
  Runtime and registry move independently. With new capability ids, each side
  only ever lacks the *new* capabilities; nothing existing is withdrawn
  (proving tests in §5).

### 2.3 Governed creates today

| Family | Resource types | Profile | Evidence |
| --- | --- | --- | --- |
| F3 configuration create/update | `automation`, `script`, `input_boolean`, `input_number` | `core.governed_configuration_operation` | adapters `f3_runtime/runtime.py:1956-1963`; routes `ha_core_readmission/routes.py:35-62`; helper types `governance/resources.py:89` |
| Label create/edit (HAMCP-165) | `label_registry_entry` | label profile | `runtime.py:1964-1968` |
| Scene | `scene_configuration`, **update only** | `core.scene_configuration_operation` | the gateway refuses create: `governance/resources.py:808` ("scene configuration is update-only"); `governance/scene_configuration.py:63-65` |
| Legacy contract-1 automation plan | `automation` (`CREATE_AUTOMATION`) | legacy writer | `governance/service.py:5523-5526`, `:14977-14978` |

Helper create today (`governance/resources.py:791-915`):

- The governance resource id of a helper is its **full entity id**, for
  example `input_boolean.example`. `_split_helper_entity_id`
  (`resources.py:1409-1430`) checks the domain equals the helper type and
  splits off the object id `example`.
- Create rechecks absence through the storage list and a
  `GET /api/states/input_boolean.example` 404, refusing with
  `target_already_exists` (`:849`) or `target_entity_id_reserved` (`:876`).
- It marks the send (`:880`), then sends `{type}/create`.
- A returned id that differs from the object id raises
  `generated_identity_mismatch` with `orphan_risk: True` (`:887-913`).
- No registry read happens on the create path. No creation identity is
  recorded today for any type.

### 2.4 Governed deletion today (HAMCP-155)

`ha_mcp_engineering/governance/configuration_delete.py` (401 lines):

- `RESOURCE_TYPES = ("automation", "script")` (`:31`); identities
  `delete_automation_configuration` and `delete_script_configuration`
  (`:32-35`).
- Intent model `configuration_delete_intent_v1` (`:39-51`, built at
  `:85-110`): `model`, `resource_type`, `target_id`, `entity_id`,
  `registry_entry_id`, `pre_image_fingerprint`,
  `consumer_evidence_fingerprint`, `dependency_reasons`, `recreate_eligible`.
- Absence polling: 6 attempts at 0.5 s (`:58-59`). A lingering registry entry
  or state reports `config_removed_runtime_unverified` (`:60`).
- `registry_entry` (`:234-273`) reads `config/entity_registry/list`, matches
  `platform == resource_type` and `unique_id == target_id`, refuses a
  duplicate, and takes the **actual** entity id from the entry
  (`entity_id_for`, `:73-82`), so a renamed automation is handled.
- `delete()` refuses any other type before I/O (`:320-327`), marks the send at
  `:347`, and turns a 4xx into `ConfigurationMutationNotDispatchedError`
  reason `configuration_delete_rejected` (`:55`, `:352-363`).

Routing (`ha_core_readmission/routes.py`): only the two delete identities map
to `core.configuration_delete_operation` (`:107-116`, `:1019-1022`); the scene
update identity maps only to the scene profile (`:161-173`, `:1029-1032`);
every unknown identity maps to `core.unsupported_f3_operation`.

Public schema `CONFIGURATION_DELETE_INPUT_SCHEMA`: `resource_type` enum
`["automation", "script"]`, 1-8 deletions (`tools/governance.py:1236-1273`).

Path allowlist (`clients/paths.py:293-310`): the scene route is method-bound
to `{GET, POST}` ("a scene configuration DELETE is never reachable",
`:305-308`); the automation and script routes are not method-bound.

### 2.5 Rollback today

`create_rollback_plan` (`f3_runtime/runtime.py:5132-5269`) dispatches by plan
family:

- label plans: `ROLLBACK_NOT_SUPPORTED` (`:5156-5170`);
- delete plans: `_create_configuration_delete_rollback` (`:5414-5530`), the
  HAMCP-155 recreate, model `hamcp155-configuration-delete-recreate-v1`
  (`:5508`);
- scene plans: `_create_scene_configuration_rollback` (`:5840`);
- everything else: `_plan_fresh_rollback` (`:5271-5413`, model
  `f3-governed-rollback-plan-v1`), which excludes any operation whose
  `action != "update"`.

Post-apply availability (`runtime.py:4322-4379`): a create-only plan gets
`unavailable_for_create` (`:4369-4372`); a delete plan is available when
`dispatched_once` and `_recreate_eligible` hold (`:4334-4342`, `:1414-1420`).

The proof that a create succeeded is persisted:
`record.evidence["resulting_state_fingerprint"] == operation.proposed_config_hash`
(HAMCP-135 equality, `governance/configuration_rollback_eligibility.py:89`).
That proves the *content* written, not object continuity (§3.2).

### 2.6 What is missing

1. Scene create: planner action, absence check, create permit, polled create
   readback with creation identity capture.
2. Creation-time identity capture for automation, script and helper creates.
3. Deletion of Engineering-created scenes, `input_boolean` and `input_number`:
   gateway commands, permits, route method, new profiles.
4. Rollback planning for creates: eligibility, ownership checks, a
   created-object rollback plan, availability marking, mixed reverse order.

## 3. Proposed design

### 3.1 Architecture: additive profiles and routing

Two new runtime-only profiles are added. Proposed names (final IDs,
adapter ids and fingerprints are unknown until built):

| New capability (proposed) | Covers | Why it is its own profile |
| --- | --- | --- |
| `core.scene_create_delete_operation` | scene create by absent config id (POST); creation identity capture for scenes; scene DELETE of an Engineering-created scene, reachable only from created-object rollback; the scene recreate that undoes that delete | Josh direction: a separate, new scene create/delete profile. The existing scene update profile keeps `DELETE` never-sent and its "existing" target |
| `core.created_helper_delete_operation` | creation identity capture for `input_boolean` and `input_number` creates; `input_boolean/delete` and `input_number/delete` of an Engineering-created helper, reachable only from created-object rollback; their absence readback | Helper delete is a newly required permission. It is not a scene permission (below) |

**Decided: helper delete lives in its own profile, not in the scene
profile.** Josh decision (in-session 2026-11-08), item 12: two additive
2026.11.0 profiles, `core.scene_create_delete_operation` and
`core.created_helper_delete_operation`. The reasons given when the choice was
put to Josh:

- Josh's direction names a *scene* create/delete profile. A contract with that
  name and target that also authorized `input_boolean/delete` would repeat
  the defect of the withdrawn "delete contract authorizes a create" option:
  its name and target would not describe what it admits.
- Separate profiles are admitted and withdrawn independently. A scene
  readback problem found at signing does not take helper rollback with it,
  and the reverse also holds.
- Both go in the same one combined 2.6 signing. A second profile therefore
  costs one more reference in the same `extend-capabilities` extension, not a
  second signing.

> Superseded (historical): the 2026-11-08 Codex correction presented this
> split as the correction author's own decision and invited Codex to
> challenge it. It is now Josh's decision. The one-profile alternative is not
> a live option.

**Automation and script create rollback uses the existing signed delete
contract unchanged.** A verified created automation or script is an
"existing automation or script configuration" (v1 target). The delete, the
400-absent rule and the absence readback are all already signed. Creation
identity capture for these two types (§3.2) uses only reads that v1 already
signs for that exact target class: `rest_config_automation_script_get`,
`websocket_entity_registry_list` and `rest_entity_state_get`. Its route
identities therefore map to `core.configuration_delete_operation`. No new
permission is needed for automation or script rollback. The alternative —
putting automation/script capture in a new profile — would make
automation/script rollback depend on a new signing for reads already signed;
it is not proposed. Codex should confirm this reading of the v1 contract.

**Routing** (all new identities are proposed names; every unknown identity
still maps to `core.unsupported_f3_operation`):

| Identity | Capability | Profile status |
| --- | --- | --- |
| `update_scene_configuration_configuration` (existing) | `core.scene_configuration_operation` | unchanged |
| `delete_automation_configuration`, `delete_script_configuration` (existing) | `core.configuration_delete_operation` | unchanged; also used by automation/script created-object rollback children |
| `capture_created_automation_identity`, `capture_created_script_identity` (new, read-only) | `core.configuration_delete_operation` | unchanged profile, new runtime route |
| `create_scene_configuration_configuration`, `capture_created_scene_identity`, `delete_created_scene_configuration` (new) | `core.scene_create_delete_operation` | new |
| `capture_created_input_boolean_identity`, `capture_created_input_number_identity`, `delete_created_input_boolean`, `delete_created_input_number` (new) | `core.created_helper_delete_operation` | new |
| `create_*` / `update_*` for automation, script, helpers (existing) | `core.governed_configuration_operation` | unchanged |

Consequences:

- Every existing capability keeps its exact profile id, version, adapter and
  fingerprint on every Core release.
- On Core 2026.10.x the new capabilities are never admitted; scene create and
  scene/helper create rollback report `pending_signed_registry_admission`
  there. Automation and script create rollback works wherever delete v1 is
  signed (2026.10.1, 2026.10.3, 2026.11.0).
- No withdrawal window, no carried-release switch and no re-sign-to-v1
  recovery exist in this design.

### 3.2 Created-object identity (creation-time evidence)

**Rule.** Rollback eligibility requires **creation-time identity evidence**:
identity observed by the forward task, under its locks, after the create
verified. Rollback-time observation is never substituted for missing
creation-time evidence.

#### 3.2.1 What Core 2026.11.0 lets Engineering observe

- `config/entity_registry/list` returns each entry's `as_partial_dict`
  (`components/config/entity_registry.py:42-60`; `helpers/entity_registry.py:393-424`,
  `:443-447`), which includes `id`, `entity_id`, `platform`, `unique_id`,
  `created_at` and `modified_at`.
- Registry removal keeps a `DeletedRegistryEntry` with the entry's `id`,
  `created_at`, `entity_id` and metadata (`entity_registry.py:1812-1846`),
  for 30 days when no config entry owns it (`ORPHANED_ENTITY_KEEP_SECONDS`,
  `:97`; purge at `:2736-2748`).
- Re-registration of the same `(domain, platform, unique_id)` pops that entry
  and **restores its `id`** (`entity_registry_id = deleted_entity.id`,
  `:1685-1699`), its `created_at` (`:1692`), entity_id if free, area, labels,
  categories, aliases, `disabled_by`, `hidden_by` and options. A stored
  registry id, or `created_at`, is therefore **not** an incarnation proof.
- The restored entry is constructed without `modified_at` (`:1775-1797`), so
  it takes the `RegistryEntry` default `utcnow()` (`:264`, `:290`). Any later
  registry update also sets `modified_at = utcnow()` (`:2163`). An entry
  loaded from storage keeps its stored `modified_at` (`:2515`).

So Engineering can detect, through `modified_at`, that a registry entry was
removed and re-registered (or edited) after the creation capture. It cannot
distinguish that from a benign metadata edit (rename, area, labels, exposure,
a Core migration), and it cannot detect a replacement that never removed the
registry entry: for example an identical-body scene POST or a `scenes.yaml`
edit, or a helper storage file edited while Core was stopped. **Uninterrupted
object incarnation is not observable through Core's public surfaces.** A
changed `modified_at` is handled by **option E** (Josh decision (in-session
2026-11-08), item 13; §3.4.2): the child is included with a disclosure and an
elevated acknowledgement, and is never reported as proven ownership.

#### 3.2.2 The creation identity record

At create verification, inside the forward task and under its locks, a
bounded poll (6 attempts at 0.5 s, the HAMCP-155 bound) captures and stores
in the record evidence a closed `created_identity` object, hash-bound into
the record:

| Field | automation / script | `input_boolean` / `input_number` | scene |
| --- | --- | --- | --- |
| `resource_type` | `automation` / `script` | `input_boolean` / `input_number` | `scene_configuration` |
| `resource_target` (governance resource id) | config id, e.g. `kitchen_motion` | **full entity id**, e.g. `input_boolean.example` (`resources.py:1409-1430`) | config id |
| `storage_object_id` | config id | object id `example` = create response `id` (`resources.py:887`) | config id |
| `registry_domain` / `registry_platform` | `automation`/`automation`, `script`/`script` | `input_boolean`/`input_boolean`, `input_number`/`input_number` | `scene`/`homeassistant` |
| `registry_unique_id` | config id | object id (`input_boolean/entity.py:26`, `input_number/entity.py:51`: `_attr_unique_id = config[CONF_ID]`) | config id (`components/homeassistant/scene.py:368-371`) |
| `registry_entry_id` | entry `id` | entry `id` | entry `id` |
| `entity_id` (actual) | registry entry `entity_id` | registry entry `entity_id`; initially `<domain>.<object id>` (`input_boolean/entity.py:41`, `input_number/entity.py:68`) but the registry entry wins (rename, or a restored entry's old entity id) | registry entry `entity_id` (assigned by Core) |
| `registry_created_at`, `registry_modified_at` | as listed | as listed | as listed |
| `delete_argument` | `DELETE /api/config/<type>/config/<config id>` | `{"type": "input_boolean/delete", "input_boolean_id": "<object id>"}` / same for `input_number` (`helpers/collection.py:585-586` `item_id_key = f"{model_name}_id"`; model name = domain, `input_boolean/__init__.py:129-131`) | `DELETE /api/config/scene/config/<config id>` |
| `capture_fingerprint` | fingerprint of the above | fingerprint of the above | fingerprint of the above |

**Correspondence checks at capture** (any failure: no identity is stored and
the record carries `created_identity_capture: identity_mismatch`):

- `resource_type` equals the registry domain, and for automation, script and
  helpers equals the registry platform; scene platform is `homeassistant`.
- For helpers: the domain of `resource_target` equals `resource_type`
  (cross-domain refusal); the object id of `resource_target` equals
  `storage_object_id`, `registry_unique_id` and the delete argument's
  `<domain>_id`; the `{type}/list` item with that id is present.
- Exactly one registry entry matches `(domain, platform, unique_id)`.
- `entity_id` starts with `<registry domain>.`. It may differ from
  `<domain>.<object id>`; a difference is recorded and disclosed, not refused.
  State reads use this actual entity id, never `resource_target`.
- The config body at capture still equals the created fingerprint.

**Capture outcomes.** Capture never changes the create's own outcome:
`succeeded_verified` stays exactly as today. The record additionally carries
`created_identity_capture`, one of `captured`, `registry_entry_not_observed_within_bound`,
`identity_mismatch`, `capability_unadmitted` or `read_failed`. Only
`captured` makes the child rollback-eligible. For scenes, the registry entry
is also part of the create's verification itself (§3.3), so a scene create
without it is not `succeeded_verified`.

**Admission.** Capture reads run only if the capture identity's capability
is admitted (§3.1), checked before the first capture read. If it is not
admitted, no read is sent and the record says `capability_unadmitted`.

**Asynchrony.** Automation, script and scene registry entries appear after
Core's fire-and-forget post-write reload; helper entries appear before the
create reply (collection listener). The bounded poll covers both. An entry
that does not appear within the bound is `registry_entry_not_observed_within_bound`:
the create is reported as today, rollback is not offered, and the plan says
so. Automation and script can then still be deleted through the existing
public `create_configuration_delete_plan`; scenes and helpers have no public
delete (non-goal), and this is disclosed.

### 3.3 Scene create (HAMCP-329)

**Tool.** Extend `create_scene_configuration_plan` with an optional
`action: "update" | "create"`, default `"update"`, so every existing call
keeps its exact meaning. No tool is added.

When `action` is `create`:

| Field | Rule |
| --- | --- |
| `config_id` | required; `CONFIG_ID_PATTERN` `^[A-Za-z0-9_-]{1,64}$` (`scene_configuration.py:122`); becomes the registry `unique_id` (`components/homeassistant/scene.py:368-371`) |
| `name` | required; Part 2 name rule (non-empty, at most 255, exact, no trim) |
| `icon` | optional; Part 2 icon rule; `null` refused for create |
| `entities` | required; 1..`MAX_ENTITIES`; every value a bounded snapshot with `state`; `null` (merge-patch remove) refused |
| `entity_id` | **refused** for create; Core assigns it |
| `expiration_minutes` | unchanged |

**Core facts (2026.11.0):**

- `POST /api/config/scene/config/<id>` (`components/config/view.py:99-139`):
  - body validated with the scene `PLATFORM_SCHEMA`, key with `cv.string`
    (`components/config/scene.py:42-51`); failure is 400, nothing written;
  - under `mutation_lock`, `EditSceneConfigView._write_value`
    (`config/scene.py:59-89`) builds `{id, name, entities, **body}`, replaces
    **every** entry with a matching id (no break) or appends one;
  - writes a fresh `uuid4().hex` id into any stored entry that has no id
    (`:79-82`);
  - atomic file write (`view.py:252-257`);
  - post-write hook runs `scene.reload` fire-and-forget
    (`config/scene.py:25-28`; `view.py:134-137`); response `{"result": "ok"}`.
- Entity id: assigned when the reload adds the entity
  (`helpers/entity_platform.py:1105`, `:1384-1428`;
  `helpers/entity_registry.py:1450`, `:1518`).
- Prior registry entry: if `(scene, homeassistant, <config_id>)` was deleted
  within 30 days, re-registration restores its `id`, `created_at`, entity_id
  (if free), area, labels, categories, aliases, `disabled_by`, `hidden_by`
  and options including exposure (§3.2.1). Deleted entries are not visible in
  `config/entity_registry/list`, so Engineering **cannot predict the entity
  id or registry id**; both are disclosed as assigned by Home Assistant and
  reported after readback.

**Execution order (planning).** Admission first:

1. Validate the request (schema, rules above). No I/O.
2. Admission: `create_scene_configuration_configuration` must be admitted
   for the connected Core release. If not: `pending_signed_registry_admission`,
   zero Home Assistant reads, nothing stored.
3. Config GET must be 404, else `scene_configuration_target_exists`.
4. Registry list must have **no** `scene.*` entry with `unique_id == config_id`
   on any platform, else `scene_configuration_config_id_registered` (covers a
   YAML-only `homeassistant` scene, an integration scene, a leftover).
5. Member validation and protected-member policy as in Part 2 (existence,
   device-class reads, safety-critical members). Saving never activates the
   scene; the disclosure names members whose snapshot would set a protected
   state when the scene is later activated.
6. Consumer disclosure: a new scene has no consumers by identity; a dangling
   reference to the eventual entity id would bind to it. Coverage gaps are
   disclosed as in Part 2.
7. Store the plan, binding the absence fingerprint
   `resource_fingerprint("scene_configuration", None)`, the exact proposed
   stored body, and no entity id.

**Apply.** Locks: the per-resource lock on `scene_configuration:<config_id>`
and the scene reload-domain lock used by Part 2. Under the locks: admission
recheck, then reads 3 and 4 repeated (any change is a stale-state refusal,
zero writes), then the durable dispatch intent, a single-use **create
permit** (the Part 2 R1-H2 permit with `action=create`, bound to plan, task,
operation, exact config id and exact body), `mark_provider_send()` and **one**
POST. No retry; recovery is read-only.

**Concurrency, disclosed and not prevented.** Core has no compare-and-swap.
A concurrent create of the same id between the preflight GET and the POST is
overwritten undetected (the Part 2 upsert race on the create side). The
`uuid4` repair of id-less entries is a Core side effect on other entries,
disclosed as in Part 2.

**Readback** (polled, 6 × 0.5 s, because the reload is fire-and-forget).
Each attempt: config GET equals the exact expected body (type-exact
canonical JSON), and the registry has exactly one `scene.*` entry with
`unique_id == config_id` and `platform == "homeassistant"`. On success the
creation identity (§3.2.2) is stored.

| Observation | Outcome |
| --- | --- |
| Config exact + registry identity captured | `succeeded_verified`, `verification_scope: stored_configuration_and_registry_identity`, `loaded_state: not_verified`, the `scene_loaded_state_not_verified_disclosed` statement; restored `disabled_by`, `hidden_by`, area or exposure are reported, not failures |
| Config exact, no registry entry within the bound | `scene_config_stored_registry_unverified`: not success, not rollback-eligible, no creation identity stored, and no rollback-time identity adopted later (Josh decision (in-session 2026-11-08), item 15; §10). The plan result discloses that the stored scene configuration exists with no governed undo |
| Config differs (e.g. a concurrent writer) | readback mismatch: not success |
| Config 404 | not applied, or unresolved if the send was uncertain |

**Never sent:** `scene.turn_on`, `scene.apply`, `scene.create`,
`scene.delete`, `scene.reload` (Core sends its own reload). The scene
*update* gateway never sends `DELETE`; scene `DELETE` is sent only by the
created-object deletion gateway (§3.5).

### 3.4 Rollback for governed creates (HAMCP-298, and the HAMCP-329 undo)

`rollback_change(plan_id, plan_hash)` on a plan whose children include
verified creates goes to a new `_create_created_object_rollback`. It plans a
**new, separately approved** plan and dispatches nothing.

- Rollback model (proposed): `hamcp298-created-object-rollback-v1`.
- The plan carries `source_plan_id`, `source_plan_hash`, the source task id,
  and per child the source `operation_id`, record hash and
  `created_identity.capture_fingerprint`; plus the provenance
  `source: created_object_rollback`. All are bound into the plan hash.
- Excluded children are listed with their reasons.

#### 3.4.1 Execution order (planning)

1. **Local checks, no Home Assistant I/O.** Validate `plan_id`/`plan_hash`;
   return a cached rollback for the same `rollback.request_id` if one exists
   (`runtime.py:5172-5175`); load the source plan and records from local
   persistence and verify their hashes.
2. **Local eligibility per child**, no I/O. Included only if all hold:
   - source operation `action == "create"`, type in {`automation`, `script`,
     `input_boolean`, `input_number`, `scene_configuration`};
   - record `terminal`, `normalized_outcome == "succeeded_verified"`,
     `dispatch_intent` present, `dispatch_count == 1`;
   - `evidence.resulting_state_fingerprint == operation.proposed_config_hash`
     (HAMCP-135 equality, `configuration_rollback_eligibility.py:89`);
   - `created_identity_capture == "captured"` and the stored
     `created_identity` passes its fingerprint and the §3.2.2 correspondence
     checks.

   Otherwise the child is excluded: `created_object_not_verified` or
   `created_object_identity_not_captured`. Excluded children never cause an
   identity to be read at rollback time and adopted.
3. **Admission, before any Home Assistant read.** Each remaining child's
   delete identity (automation/script → `core.configuration_delete_operation`;
   helpers → `core.created_helper_delete_operation`; scene →
   `core.scene_create_delete_operation`), and each update child's existing
   restore capability, must be admitted. An unadmitted child is excluded with
   `pending_signed_registry_admission`, and the prerequisite rule (§3.4.4)
   applies. If no child remains, the response is
   `pending_signed_registry_admission` with **zero** Home Assistant reads and
   nothing stored.
4. **Current-state reads** (only for admitted children): config or storage
   item, registry entries, state by actual entity id, consumer evidence.
5. **Ownership checks** (§3.4.2) and the prerequisite rule (§3.4.4).
6. Store the plan.

The executor repeats steps 3-5 under the locks at preflight (§3.4.3).

#### 3.4.2 Ownership checks and outcomes

At planning and again at preflight, for each included child, against its
creation-time identity:

- current body fingerprint equals the created fingerprint, else
  `created_object_changed`;
- exactly one registry entry for `(registry_domain, registry_platform,
  registry_unique_id)`, else `created_object_identity_changed` (none:
  see absence below; more than one: refused);
- that entry's `id` and `created_at` equal the captured values, else
  `created_object_identity_changed`;
- if that entry's `modified_at` differs from the captured value, and every
  other check in this list passes, the child is **included under option E**
  (Josh decision (in-session 2026-11-08), item 13; contract below). It is
  never refused for this reason alone, and never included without the E
  disclosure and the elevated acknowledgement;
- for helpers, the §3.2.2 correspondence checks hold for the current
  storage item and registry entry; the delete argument is rebuilt from the
  captured object id and must equal the captured `delete_argument`;
- absence of the config/storage item: excluded `created_object_already_absent`,
  nothing sent.

Owner-consistent outcome for each replacement case (never silently adopted
as proven ownership):

| Case | What Engineering observes | Outcome |
| --- | --- | --- |
| Replacement with a different registry id (external delete/recreate after the 30-day purge, or an entry from another path) | `id` differs (and `created_at` usually differs) | Refused `created_object_identity_changed`, zero writes, even when `modified_at` also moved |
| Same registry id but a different `created_at`, or a different body | `created_at` or body fingerprint differs (with or without a newer `modified_at`) | Refused `created_object_identity_changed` / `created_object_changed`, zero writes. Option E never applies |
| Benign registry edit after creation (rename, area, labels, exposure, a Core migration) | `id`, `created_at`, body equal; `modified_at` newer | Option E: included with the E disclosure and an elevated acknowledgement. Never reported as proven ownership |
| Replacement that restores the same registry id with an identical body (external delete/recreate within 30 days) | `id`, `created_at`, body equal; `modified_at` newer, so indistinguishable from the previous row | Option E, exactly as the previous row. This is the residual risk E accepts, and the E disclosure names it |
| Replacement that never removed the registry entry, with an identical body (identical-body scene POST, YAML or storage-file edit) | nothing distinguishable | Included as the created object; this residual limitation is disclosed in every created-object rollback plan (§8) |
| Missing creation identity (older record, capture not admitted, not observed within bound, mismatch, read failure) | — | Excluded `created_object_identity_not_captured`; no rollback-time identity is captured or adopted. Automation/script may still be deleted through the public delete tool |
| Replacement between planning and preflight | preflight identity, body or `modified_at` differs from the plan-bound values | Stale-state refusal at preflight, zero writes; a fresh rollback plan must be requested |
| Replacement between preflight and the single send | not observable (no compare-and-swap) | Residual race under the locks, disclosed as in HAMCP-155; the send deletes by the bound id |

**Option E contract** (Josh decision (in-session 2026-11-08), item 13).
E applies only when the registry entry's `id` and `created_at`, the body
fingerprint and (for helpers) every §3.2.2 correspondence check all equal
the creation capture, and the only difference is a newer `modified_at`.
Any other difference is refused as in the table above. For such a child:

- **Disclosure.** The plan carries this statement for the child: "the
  registry entry changed after Engineering created it; it may have been
  edited, or deleted and recreated outside Engineering, which Home Assistant
  does not distinguish". It also names the captured and observed
  `modified_at` and, if the entity id changed, both entity ids. The plan and
  result never say "proven ownership" or an equivalent.
- **Elevation.** The child gets the new elevation basis (proposed)
  `created_object_registry_modified_since_create`. It sits beside the
  HAMCP-155 bases (`configuration_delete_planning.py:45-47`, computed in
  `expected_basis` `:193-205`), so `classify` (`:286-291`) makes the child's
  policy `elevated` (operation `policy_class` `elevated_admin`). The plan then
  requires `elevated_risk_acknowledgement`. Approval without it is refused, as
  the runtime already enforces (`f3_runtime/runtime.py:4634-4645`). This basis
  is an ownership-evidence elevation, not a physical consequence. On its own
  it must not set `physical_consequence: safety_critical`. The child's
  physical classification still comes only from the HAMCP-155 bases. That
  separation is required implementation behaviour.
- **Binding.** The plan hash binds the captured `modified_at`, the
  `modified_at` and entity id observed at planning, and the E basis. At
  preflight, under the locks, the observed `modified_at`, `id`, `created_at`,
  body and entity id must equal the plan-bound values. Any change is a
  stale-state refusal with zero writes, and a fresh rollback plan must be
  requested. The acknowledgement applies to that exact plan hash and is never
  carried to another plan.
- **Entity id.** The delete is addressed by the captured config id or object
  id. The state readback uses the entity id observed at planning (the actual
  registry entity id, for example a renamed one). It never uses the captured
  entity id or `resource_target`.
- **Automation and script.** E adds the basis in the 298 layer on top of the
  unchanged HAMCP-155 delete child. The delete v1 contract, its commands and
  its readback do not change.

#### 3.4.3 Rollback plan contents and approval

- One child per eligible forward child, in **strict reverse** forward order,
  at most 8 (`MAX_CONFIGURATION_OPERATIONS = 8`, `governance/service.py:445`,
  equal to the HAMCP-155 bound).
- Automation/script delete children: standard `configuration_delete_intent_v1`
  (unchanged model) with `target_id` = config id, `entity_id` and
  `registry_entry_id` from the creation identity, `pre_image_fingerprint` =
  created fingerprint; the 155 preflight runs unchanged, and the 298 layer
  additionally rechecks `created_at`/`modified_at` and the capture
  fingerprint.
- Helper and scene delete children: a new closed intent (proposed
  `created_object_delete_intent_v1`) carrying the full §3.2.2 tuple
  (`resource_target`, `storage_object_id`, `registry_unique_id`,
  `registry_entry_id`, actual `entity_id`, `delete_argument`) plus
  `pre_image_fingerprint`, `consumer_evidence_fingerprint`,
  `dependency_reasons`, `recreate_eligible`.
- Update children (mixed plans): the existing restore child, unchanged.
- `consumer_evidence_fingerprint` from the fenced dependency index; consumers
  added since the create are disclosed and raise the plan as in 155 §3.2.
- `recreate_eligible: true`. The rollback of a created-object delete is a
  recreate: automation/script through the HAMCP-155 recreate; helpers through
  the existing governed create (its `generated_identity_mismatch` check
  applies); scenes through the new scene profile's create.
- Policy (unchanged HAMCP-155/213): `configuration_deleted` in
  `physical_effects`; protected effects in the created configuration become
  elevated with an acknowledgement; dependency gaps become elevated. In
  addition, an option E child (§3.4.2) is elevated with the
  `created_object_registry_modified_since_create` basis. One plan-level
  `elevated_risk_acknowledgement` covers every elevated child in the plan, as
  today, and the plan lists every basis per child.
- Approval: exact-hash, Ingress only, no self-approval; the forward approval
  is never reused.

#### 3.4.4 Mixed forward plans (create and update)

**Acceptance outcome:** one reverse plan. Updates get the existing restore
child; creates get a delete child; the order is strictly the reverse of the
forward order. Example: forward "create helper H, then update automation A to
reference H" rolls back as "restore A, then delete H".

**Prerequisite rule.** In rollback order, every child that precedes a delete
child D(X) is a prerequisite of D(X) (conservatively positional: Engineering
does not try to prove which later forward child references X).

- At planning: if any prerequisite of D(X) is excluded (stale, changed,
  unverified, unadmitted, identity not captured), D(X) is excluded with
  `created_object_rollback_prerequisite_excluded`, naming the prerequisite.
- At execution: the plan is ordered and stops on the first failure (§3.6), so
  a failed or uncertain restore of A leaves D(H) unattempted and marked.
- A later delete is never attempted while an earlier restore is unresolved.

This is not deferred. If implementation proves that a **specific**
combination cannot be carried in one F3 child sequence (for example a named
adapter pair), the build stops and that exact combination is escalated to
Josh as a bounded scope question; nothing is pre-approved here.

**Availability marking** (`runtime.py:4322-4379`):

- `unavailable_for_create` becomes `available_pending_current_state_check`
  when at least one created child passes the local checks of §3.4.1 step 2.
- Plans made only of labels keep `ROLLBACK_NOT_SUPPORTED`.
- Legacy contract-1 `CREATE_AUTOMATION` plans keep `unavailable_for_create`
  (`governance/service.py:5523-5526`). Josh decision (in-session 2026-11-08),
  item 14. They carry no creation identity, and the legacy writer is not
  changed. The public `create_configuration_delete_plan` can still delete
  such an automation.

### 3.5 Deletion gateway for Engineering-created scenes and helpers

A created-object deletion gateway (beside `ConfigurationDeleteGateway`, whose
automation/script code is unchanged) sends exactly these commands:

| Type | Write (once) | Absent target | Registry match | Absence readback (polled 6 × 0.5 s) |
| --- | --- | --- | --- | --- |
| `scene_configuration` | `DELETE /api/config/scene/config/<config_id>`, no body | 400 "Resource not found", no write (`view.py:142-151`) | `(scene, homeassistant, config_id)` | config GET 404 + registry absent + `GET /api/states/<actual entity_id>` 404 |
| `input_boolean` | WS `{"type": "input_boolean/delete", "input_boolean_id": "<storage_object_id>"}` | WS `not_found`, then a stray result frame (below) | `(input_boolean, input_boolean, <storage_object_id>)` | `input_boolean/list` lacks the object id + registry absent + `GET /api/states/<actual entity_id>` 404 |
| `input_number` | WS `{"type": "input_number/delete", "input_number_id": "<storage_object_id>"}` | as above | `(input_number, input_number, <storage_object_id>)` | `input_number/list` lacks the object id + registry absent + state 404 by actual entity id |

The helper delete argument is the **object id**, never the full entity id;
the state readback uses the **actual registry entity id**, never the
resource target. A renamed helper therefore deletes by object id and reads
back the renamed entity id. A rename moves `modified_at`, so the child is
included only under option E (§3.4.2), with its disclosure and elevated
acknowledgement.

**Core facts:**

- Scene delete: `view.py:142-159` writes atomically;
  `EditIdBasedConfigView._delete_value` pops only the **first** matching id
  (`view.py:233-241`); the hook does not reload, it resolves
  `ent_reg.async_get_entity_id(SCENE_DOMAIN, HOMEASSISTANT_DOMAIN, key)` and
  calls `async_remove` fire-and-forget (`config/scene.py:30-40`;
  `view.py:157`); a duplicate id leaves a 200 GET after the DELETE, which is a
  readback mismatch (155 rule); Core keeps a `DeletedRegistryEntry` for 30
  days (§3.2.1).
- Helper delete: `ws_delete_item` awaits `async_delete_item`
  (`helpers/collection.py:764-777`), which pops the item, schedules the
  storage save with `SAVE_DELAY = 10` s (`:31`, `:356-369`), and awaits
  `notify_changes`; the listener `_remove_entity` removes the registry entry
  or force-removes the entity (`:479-490`) **before** the reply. The
  collection serves only storage items; YAML helpers are a separate
  collection sharing the `IDManager` (`input_boolean/__init__.py:107-131`;
  `input_number/__init__.py:160-184`), so a YAML helper is never deleted.
- Upstream quirk: on `ItemNotFound`, `ws_delete_item` sends `ERR_NOT_FOUND`
  and then falls through to `send_result` for the same message id
  (`collection.py:771-777`). The client treats the **first** frame as
  authoritative and drops the second; not-found is a confirmed non-mutation
  (`ItemNotFound` is raised before the pop).
- **Durability, disclosed.** Helper absence is verified in the runtime and
  registry, not on disk. The storage save is delayed 10 s. A clean Core stop
  flushes pending delayed saves on `EVENT_HOMEASSISTANT_FINAL_WRITE`
  (`helpers/storage.py:586-592`, `:609-620`); a crash or power loss inside
  the window can lose the save, so the helper can reappear after restart.
  Helper create has the same window today. Runtime absence is reported as
  runtime absence, never as disk durability.

**Route and permits.**

- `clients/paths.py` `_route_methods` for `/config/scene/config/{}` becomes
  `{GET, POST, DELETE}`, and `DELETE` additionally requires a **single-use
  delete permit** bound to plan, task, operation, exact config id, exact
  command and the recorded dispatch intent. Only the created-object deletion
  gateway installs it; any other scene `DELETE` (including from the scene
  update gateway) is refused before a session exists. The scene update
  profile's `never_sent` stays as it is.
- Helper delete goes through a closed command builder that can produce only
  the two commands above, from a validated object id; every other
  `{type}/delete` is refused.

**Reachability.**

- The public `create_configuration_delete_plan` enum stays
  `["automation", "script"]`.
- Scene and helper delete children are constructed **only** by the
  created-object rollback planner from a record with captured creation
  identity, with the plan provenance `source: created_object_rollback` bound
  into the plan hash. The executor refuses scene or helper delete children
  without it, and refuses a provenance whose source plan/record hashes do
  not verify.
- The same rule is part of both new signed contracts (`reachability`, §5).

### 3.6 Partial failure and recovery

- Forward create plans keep today's semantics: ordered, non-atomic, stop on
  the first failure. Capture failure never changes a create's outcome.
- Rollback plans: ordered, non-atomic, stop on the first failure;
  unattempted children are marked; no automatic compensation.
- An uncertain send is resolved by readback only; a delete is never
  redispatched (155 §1, `BlindRedispatchProhibited`). Absent means verified;
  present means not applied or unresolved.
- `config_removed_runtime_unverified` is reported as such and is not
  recreate-eligible.

## 4. Public schema and tool-registration impact

- **Tool count: no tool added or removed.** Engineering-native tools at the
  base: 81 (`len(CAPABILITIES) + len(BETA_NATIVE_CAPABILITIES)` = 25 + 56;
  formula `application.py:278-280`). The 2.6.0 aggregate depends on other
  2.6 work and is not asserted here. The delegated count is dynamic and
  untouched.
- **`create_scene_configuration_plan`** (`tools/governance.py:1730-1778`)
  gains the additive optional `action` enum `["update", "create"]`, default
  `"update"`. With `create`: `name` and `entities` required; `entity_id` and
  `null` member values refused. Under `update`, every existing request
  validates and behaves identically (regression test). This is a
  security-sensitive public input schema change.
- **`create_configuration_delete_plan`**: schema unchanged
  (`tools/governance.py:1236-1273`).
- **`rollback_change`**: schema unchanged. New response values:
  `rollback_model: hamcp298-created-object-rollback-v1`; exclusion reasons
  `created_object_not_verified`, `created_object_identity_not_captured`,
  `created_object_changed`, `created_object_identity_changed`,
  `created_object_already_absent`, `created_object_rollback_prerequisite_excluded`,
  `pending_signed_registry_admission`. New elevation basis (option E, not an
  exclusion reason): `created_object_registry_modified_since_create`, with
  the E disclosure, the captured and observed `modified_at` and, when
  changed, both entity ids.
- **Plan and record surfaces:** availability
  `available_pending_current_state_check` for create plans; record evidence
  `created_identity` and `created_identity_capture` on automation, script,
  helper and scene creates; scene create disclosures
  (`scene_loaded_state_not_verified_disclosed`, "entity id and registry id
  assigned by Home Assistant", the prior-metadata restore, the upsert race).
- **Catalog and health:** `capabilities.py:431` (public delete tool
  `resource_types: [automation, script]`) unchanged; `capabilities.py:528`
  (scene tool) gains a `create` description; `core_admission` reports the two
  new capabilities.

## 5. Signing impact

| Profile | New or changed | Core commands / contract | Why | Fingerprint impact | Batch |
| --- | --- | --- | --- | --- | --- |
| `core.configuration_delete_operation` | **Unchanged** (`core_configuration_delete_operation_v1`, `compiled-core-configuration-delete-operation-v1`, `sha256:5688af5d…887d`) | None. Also serves automation/script created-object rollback and their creation identity capture (reads already in the contract) | §3.1 | None | Not applicable |
| `core.scene_configuration_operation` | **Unchanged** (`core_scene_configuration_operation_v1`, `compiled-core-scene-configuration-operation-v1`, `sha256:73bac906…a6bb`) | None; `DELETE` stays never-sent | Josh direction: separate scene profile | None | Not applicable |
| `core.governed_configuration_operation` | **Unchanged** (`core_governed_configuration_operation_v1`, `compiled-core-governed-configuration-operation-v1`, `sha256:d517fa73…f441`) | None. Automation, script and helper creates (and the helper recreate) stay under it | Already signed | None | Not applicable |
| `core.scene_create_delete_operation` (proposed) | **New**, version 1 (`core_scene_create_delete_operation_v1`, adapter `compiled-core-scene-create-delete-operation-v1`, proposed) | REST `POST /api/config/scene/config/{config_id}` to an absent id; `GET /api/config/scene/config/{config_id}`; `DELETE /api/config/scene/config/{config_id}` (no body); WS `config/entity_registry/list`; `GET /api/states/{entity_id}`. Contract: create target `one_exact_absent_ui_scene_config_id_with_no_scene_registry_entry`; delete target `one_exact_engineering_created_scene_with_creation_identity`; `resource_types: ["scene"]`, platform `homeassistant`; create arguments (name required, every member a snapshot, no null, no entity_id); create readback `config_exact_and_registry_identity_polled_loaded_state_not_verified`; delete `absent_target` `400_resource_not_found_no_write`; delete readback `config_404_registry_absent_state_absent_polled`; `reachability` (create through `create_scene_configuration_plan` `action: create`; delete only through created-object rollback); required check `scene_loaded_state_not_verified_disclosed`; concurrency `upsert_concurrent_create_of_same_id_overwritten_undetected_disclosed`; `never_sent` `scene.apply`, `scene.create`, `scene.delete`, `scene.reload`, `scene.turn_on`; recovery `read_only_no_redispatch` | HAMCP-329 outcome; Josh direction | New fingerprint, **unknown until computed from merged code** | One combined 2.6 signing: an `extend-capabilities` extension of the existing 2026.11.0 entry only |
| `core.created_helper_delete_operation` (proposed) | **New**, version 1 (`core_created_helper_delete_operation_v1`, adapter `compiled-core-created-helper-delete-operation-v1`, proposed) | WS `input_boolean/list`, `input_number/list`, `input_boolean/delete {input_boolean_id}`, `input_number/delete {input_number_id}`, `config/entity_registry/list`; REST `GET /api/states/{entity_id}`. Contract: target `one_exact_engineering_created_storage_helper_with_creation_identity`; `resource_types: ["input_boolean", "input_number"]`; identity correspondence (resource target domain = type; object id = storage id = registry unique_id = delete argument; state by actual registry entity id); creation identity capture read-only; dispatch `at_most_once_ws_helper_delete_by_object_id`; `absent_target` `ws_not_found_no_write_duplicate_result_frame_ignored`; readback `storage_list_absent_registry_absent_state_absent_polled_runtime_not_disk_durable`; `reachability` created-object rollback only; `never_sent` `input_boolean/create`, `input_boolean/update`, `input_number/create`, `input_number/update` and every other `{type}/delete`; recovery `read_only_no_redispatch` | HAMCP-298 outcome (helpers); helper delete is a newly required permission | New fingerprint, **unknown until computed from merged code** | Same one combined 2.6 signing, 2026.11.0 only |

No other new permission is required. The probe profile and its fingerprints
do not move (both new profiles are runtime-only). The signing tool checks
retained references against the compiled exact contracts, so the one
combined signing appends the two new references to the 2026.11.0 entry and
retains every existing reference byte-for-byte (cross-check with #395).

**Proving tests (required implementation evidence; none has been run):**

1. Every existing runtime profile (32 at the base) keeps its exact
   `profile_id`, `profile_version`, `adapter_id` and `contract_fingerprint`,
   compared to the signed references at sequence 17 for every carried Core
   release.
2. **Old runtime + extended registry:** a base runtime with a test-signed
   registry whose 2026.11.0 entry also references the two new capabilities
   keeps admission of every existing capability, and the unknown references
   do not break selection.
3. **New runtime + old registry** (sequence-17 references only): every
   existing capability is admitted exactly as before (scene update,
   automation/script delete, automation/script create rollback); only the new
   identities (scene create, scene/helper capture, scene/helper delete)
   report `pending_signed_registry_admission`, with zero Home Assistant
   reads.
4. **Older Core releases:** on 2026.10.0, 2026.10.1 and 2026.10.3 the new
   capabilities are never admitted, even with the extended registry, and
   existing admission on those releases is unchanged.
5. Probe fingerprints unchanged.

### 5.1 Superseded (historical): the v1→v2 version-switch analysis

The 2026-11-07 revision proposed `core.configuration_delete_operation` v2
(and, under its option A, `core.scene_configuration_operation` v2), moving
every carried Core release from v1 to v2 in one sequence, accepting a
fail-closed withdrawal window between signing and deploying (old Q3), and
recovering an implementation revert by re-signing references back to v1.
Josh's direction (register 2026-11-07; confirmed in-session 2026-11-08)
replaced that architecture with additive profiles. The analysis is retained
only as history; it is not an actionable plan, no version-replacement tooling
is to be built for it, and nothing in §3, §5, §7 or §9 depends on it.

## 6. Negative reachability, refusals and tests

All rows are required implementation evidence; none has been run.

| Path | Refusal | Test (new unless noted) |
| --- | --- | --- |
| Scene create or scene/helper rollback while its new profile is unadmitted | `pending_signed_registry_admission`, zero Home Assistant reads, nothing stored | admission-first tests with a counted fake transport (planning and preflight) |
| Creation identity capture while its capability is unadmitted | no read sent; record `capability_unadmitted`; create outcome unchanged | counted-transport test |
| Scene create for an existing id or a registered unique_id | `scene_configuration_target_exists` / `scene_configuration_config_id_registered`, no write | planning tests; preflight-stale test (id appears after planning) |
| Scene create with `entity_id`, a `null` member, a missing name or entities | schema or planner refusal before reads | schema tests; update-request identity regression |
| Scene `DELETE` without the permit, reused permit, wrong target, or from the scene update gateway | refused before transport (`paths.py`) | permit tests; scene update `never_sent` unchanged (existing) |
| Scene services | never sent | existing never-sent tests extended to create and delete |
| Public delete tool with scene or helper types | schema enum refusal; planner `unsupported_resource_type` before reads | schema-unchanged test; direct-service-call refusal |
| Scene or helper delete child without `source: created_object_rollback`, or with forged/mismatched source hashes | executor refusal before intent | forged-plan and missing-provenance tests |
| Helper `{type}/delete` for any other type | closed builder refusal | WS allowlist test |
| Helper identity mismatch: resource target domain ≠ type (cross-domain), object id ≠ storage id / unique_id / delete argument, registry entry of the other helper domain | capture `identity_mismatch`; rollback excluded or refused, zero writes | tests for **both** `input_boolean` and `input_number` with distinct full ids and object ids |
| Helper exact delete argument | sends `{type: "<domain>/delete", "<domain>_id": "<object id>"}`, never the full entity id | fake-WS argument assertion for both domains |
| Renamed helper or automation (registry entity id ≠ `<domain>.<object id>`) | option E: included with the E basis; delete by object id / config id; state read by the entity id observed at planning | rename tests for automation, script, both helper domains and scene; state readback against the renamed id, never the captured id or `resource_target` |
| `ERR_NOT_FOUND` followed by a duplicate result frame | first frame authoritative → `sent_rejected_no_mutation`; second dropped without mis-correlation | fake-WS quirk test |
| Rollback of an unverified, uncertain or undispatched create | excluded `created_object_not_verified` | eligibility matrix |
| Record without creation identity (older record, capture failed/timed out/unadmitted/mismatch) | excluded `created_object_identity_not_captured`; no rollback-time identity read is adopted | missing-identity test with a counted transport proving no adoption |
| Replacement with a different registry id before rollback | `created_object_identity_changed`, zero writes | test at planning and at preflight |
| Replacement restoring the same registry id and identical body | option E: elevated acknowledgement + disclosure, never reported as proven ownership | delete/re-register fake-registry test with restored `id`/`created_at` and new `modified_at` |
| **E negative:** `modified_at` moved **and** a different registry `id` | `created_object_identity_changed`, zero writes; E not applied | test per type, at planning and at preflight, with an acknowledgement supplied, proving it does not override the refusal |
| **E negative:** `modified_at` moved **and** the same `id` but a different `created_at` | `created_object_identity_changed`, zero writes | as above |
| **E negative:** `modified_at` moved **and** a different body fingerprint | `created_object_changed`, zero writes | as above |
| **E negative:** `modified_at` moved **and** a helper correspondence failure (cross-domain, object id ≠ unique_id or delete argument) | refused, zero writes | both helper domains |
| **E negative:** E child approved without `elevated_risk_acknowledgement` | approval refused `elevated_risk_acknowledgement_required`; nothing dispatched | approval test; counted transport shows zero writes |
| **E negative:** acknowledgement from a different plan hash, or reused after a new rollback plan | refused; acknowledgement not carried over | approval-binding test |
| **E negative:** `modified_at`, entity id, `id`, `created_at` or body changes again between planning and preflight | stale-state refusal at preflight, zero writes | preflight-stale test per type |
| **E negative:** an unchanged `modified_at` | no E basis, no E disclosure; the child's policy is unchanged from HAMCP-155 | regression test showing E does not elevate unchanged children |
| **E negative:** the E basis alone | `physical_consequence` not set to `safety_critical` by E alone | classification test |
| **E positive:** created automation, script, `input_boolean`, `input_number` and scene, each then renamed or given an area (only `modified_at` moves) | rollback plan includes the child with the E basis, `elevated_admin`, the E disclosure and both `modified_at` values; with the acknowledgement it approves, sends exactly one delete and verifies absence | end-to-end test per type with a fake registry |
| **E positive:** mixed plan with one E child and one unchanged child | one plan-level acknowledgement covers it; only the E child carries the E basis; strict reverse order holds | mixed-plan test |
| Legacy contract-1 `CREATE_AUTOMATION` plan (item 14) | rollback availability stays `unavailable_for_create`; no identity read or adoption | legacy-plan test with a counted transport |
| Scene stored but not registered within the bound (item 15) | `scene_config_stored_registry_unverified`, not success, not rollback-eligible; a later `rollback_change` excludes it and reads no identity | scene readback-timeout test, then a rollback request with a counted transport |
| Replacement between planning and preflight | stale-state refusal, zero writes | preflight-stale test for each type |
| Created object changed or already absent | `created_object_changed` / `created_object_already_absent`, nothing sent | tests |
| Mixed plan, prerequisite restore excluded at planning | dependent delete excluded `created_object_rollback_prerequisite_excluded` | create helper → update consumer, consumer stale |
| Mixed plan, prerequisite restore fails or is uncertain at execution | stop; helper delete unattempted and marked | create helper → update consumer, restore fails |
| Label-create rollback | `ROLLBACK_NOT_SUPPORTED` (existing) | existing test |
| Generic `create_configuration_plan` with `delete` | refused (existing) | existing test |
| Existing profile references and fingerprints | byte-identical | §5 proving tests 1-5 |
| Uncertain delete send | readback only, never redispatched (`BlindRedispatchProhibited`) | recovery tests per new type |

Mutations, run under the heavy-job rules in the build PRs and not here:

1. drop the scene create absence recheck;
2. drop the created-fingerprint check;
3. drop the registry `id` comparison;
4. drop the `modified_at` comparison (removes the E basis and disclosure);
   also: apply E when `id`, `created_at` or the body differs; and drop the E
   acknowledgement requirement;
5. accept a rollback-time identity when creation identity is missing;
6. send the helper full entity id as the delete argument;
7. accept a delete without the permit;
8. treat the second WS frame as the reply;
9. skip the polled create readback;
10. drop the mixed-plan prerequisite rule;
11. expose scene in the public delete enum.

## 7. Acceptance

**Useful authorized success** (test-signed registry, fake Core, end to end):

1. Scene create: plan → approve → one POST → verified (config exact,
   creation identity captured) with the loaded-state disclosure; then
   `rollback_change` → separate approval → one scene `DELETE` → absence
   verified; the recreate restores the same config id.
2. Automation and script create → creation identity captured under the
   unchanged delete contract → rollback deletes it under **unchanged delete
   v1**, on a Core release where only sequence-17 references exist.
3. `input_boolean` and `input_number` create → identity captured → rollback
   sends the exact object-id delete argument → absence verified by object id
   and actual entity id.
4. Mixed plan: create a helper, update an automation to reference it;
   rollback restores the automation first, then deletes the helper.
5. Mixed plan with the restore excluded at planning, and separately with the
   restore failing at execution: the helper delete is not attempted in
   either case and the result says why.
6. Option E (item 13): a created helper and a created scene are each renamed
   after creation. The rollback plan includes each with the E disclosure and
   the elevated acknowledgement. Approval with the acknowledgement deletes
   exactly that object and verifies absence by the renamed entity id. The
   same plan with a different registry `id`, `created_at` or body is refused.

**Refusal:** every row of §6.

**Failure preservation:**

- A 400 on DELETE, or a WS not-found, gives `sent_rejected_no_mutation`.
- An uncertain send gives a readback decision.
- `config_removed_runtime_unverified` and `scene_config_stored_registry_unverified`
  are reported as not success.
- A partial rollback stops at the first failure, remaining children marked.
- Capture failure leaves the forward create outcome unchanged.

**Verification:** each exact readback in §3.3 and §3.5, polled within a
bound; helper absence reported as runtime absence, not disk durability.

**Recovery:** process loss after the intent resolves by readback only; the
rollback of the rollback (the recreate) is offered only for
`succeeded_verified` delete children; restart around intent and readback
tested (§8 persistence gate).

**Post-signing canary** (a separate, Josh-authorized step, not part of this
design): create one test scene, verify, roll back, verify absence; the same
for one test automation and one test helper.

## 8. Security

- **New write reachability** (an architecture change under root
  `AGENTS.md`): scene create by POST to an absent id; scene `DELETE`;
  `input_boolean/delete` and `input_number/delete`. Each is bound to an exact
  identity and a new signed profile, admission-checked before any read, sent
  at most once after a durable intent, verified by exact readback, and
  negative-tested (§6). Automation/script deletion reachability is unchanged
  (already reachable through the public delete tool under delete v1).
- **Bounded deletion and its limit.** Scene and helper deletion is reachable
  only as the separately approved undo of a verified Engineering create
  whose identity was captured at creation, through a closed command builder
  and a single-use permit. Deletion is never offered as a general tool for
  these types. Engineering refuses whenever the observable identity (registry
  `id`, `created_at`, body fingerprint, helper correspondence) differs from
  the creation capture. A changed `modified_at` alone is elevated under
  option E (Josh decision (in-session 2026-11-08), item 13). **Limitation:**
  Home Assistant does not expose object incarnation. A replacement that
  keeps every observable value equal cannot be detected, in particular one
  that never removed the registry entry and has an identical body, and the
  rollback would delete it. Under option E, a delete/recreate that restored
  the same registry id is also included, after disclosure and an elevated
  acknowledgement. The design does not claim that Engineering can never
  delete an object it did not create.
- **No provider bypass.** The provider boundary stays `direct_ha_api`; no
  ha-mcp delegation, fallback or direct-HA path outside the gateways;
  upstream `ha_config_remove_*` stay unrouted; legacy `delete_automation`
  stays prohibited (155 §4).
- **Persistence and downgrade — implementation acceptance gate.**
  - New record evidence `created_identity` / `created_identity_capture` on
    create records, new plan provenance `source: created_object_rollback`,
    the new rollback model and the new delete intent are additive and bound
    into their hashes.
  - Old plans and records (without the new keys) must load, verify their
    historical hashes and behave exactly as before; they are never inferred
    to be rollback-eligible.
  - An older runtime must reject the new rollback plan forms and delete
    intents as unsupported, never execute them, and must still load records
    that carry the new evidence keys.
  - Restart tests around the dispatch intent and the readback for every new
    path.
  - If implementation changes the serializer or hashed material of any
    already-shipped plan or record format, executable old-record
    compatibility evidence is required, and any committed historical fixture
    must be generated by the exact historical writer (root `AGENTS.md`
    "Persisted Compatibility Fixtures"). No fixture is reconstructed by hand.
    This gate is not waived by this design.
- **Signing.** Two new runtime-only profiles, Core 2026.11.0 only, one
  combined 2.6 signing; existing references unchanged; no probe fingerprint
  moves; fingerprints come from merged code only.
- **Disclosure, not prevention:** the upsert race; the `uuid4` repair of
  id-less entries; the prior registry metadata and id restore; the residual
  observable-identity limitation; the 10 s helper storage window and crash
  durability; asynchronous registry creation for automation, script and
  scene; loaded state not verified.
- **Secrets.** None are read or added. No live Home Assistant access was used
  for this design.

## 9. Build plan

Every implementation PR targets `release/2.6.0`, is a draft with independent
review, and catches up by merge, never rebase. 2.5.0 lands first and **wins
shared files**: #387 (HAMCP-288) touches `application.py`,
`models/responses.py`, `tests/test_f3_runtime_integration.py`,
`tests/test_beta_v2.py`, `tests/test_f3_readiness.py`; #384 (HAMCP-309 Part
3) touches `docs/CORE_RELEASE_REGISTRY.md` and
`scripts/core_evidence_workflow.py`, which signing uses; #386 touches helper
value control only.

All slices ship in 2.6.0. Slice order is build sequencing, not phased
signing.

| Slice | Content | Profile or signing effect | Depends on |
| --- | --- | --- | --- |
| 1 | ADR-018 amendment recording the HAMCP-329/298 direction; creation identity capture framework and `created_identity` evidence; persistence/downgrade tests | none; §5 proving test 1 | 2.5.0 merged |
| 2 | 298 for automation and script: capture under the unchanged delete contract, eligibility and ownership checks, `_create_created_object_rollback`, availability marking, mixed reverse plans with the prerequisite rule | none (runs on signed delete v1) | slice 1 (the `modified_at` rule is decided: option E, item 13) |
| 3 | New `core.created_helper_delete_operation`: closed helper builder, identity tuple, WS quirk, permits, routes; 298 for helpers including mixed plans | new runtime-only profile, dormant until signed | slice 2 |
| 4 | New `core.scene_create_delete_operation`: scene `action: create`, absence check, create permit, polled readback, scene delete, route method + delete permit, scene undo and recreate | new runtime-only profile, dormant until signed | slice 2 |
| 5 | Signing (Josh only): the one combined 2.6 `extend-capabilities` extension of the 2026.11.0 entry, adding these two references with the other approved 2.6 additions | two new references; existing references unchanged | slices 3-4 merged; built image provenance |

**Rollback of the implementation.** Every slice reverts cleanly: the
existing capabilities never change, so a revert only removes new identities.
If a release with the new profiles is reverted to an older runtime while the
registry carries the new references, the older runtime ignores them (§5
proving test 2) and must reject any stored new-form rollback plans (§8 gate);
no re-signing is needed.

## 10. Decided questions and remaining open questions

Withdrawn per Josh direction (register 2026-11-07; confirmed in-session
2026-11-08) and the Codex review: old Q1 (scene profile home: separate new
scene profile), Q2 (helper types: the existing governed creates
`input_boolean`, `input_number`), Q3 (v1→v2 window: obsolete), Q4 (phasing:
all in 2.6.0, one signing), Q5 (ADR-018: the approved HAMCP-298 outcome is
recorded in the amendment).

**Decided** (Josh decision (in-session 2026-11-08); recommendations accepted
as written):

- **Item 12. One new scene profile, or two?** Decided (a): two additive
  2026.11.0 profiles, `core.scene_create_delete_operation` and
  `core.created_helper_delete_operation`, both in the one combined 2.6
  signing (§3.1, §5).
- **Item 13 (Q-A). Ownership strictness when the registry entry changed after
  creation.** Decided option **E**. If only `modified_at` moved, the child is
  included with the disclosure and an elevated acknowledgement. A different
  registry `id`, `created_at` or body is still refused, and missing creation
  identity still excludes. The contract is in §3.4.2 ("Option E contract")
  and the tests are in §6. The earlier rule "if unanswered at freeze, S is the
  fail-closed default" is withdrawn.
- **Item 14 (Q6). Legacy contract-1 `CREATE_AUTOMATION` plans**
  (`governance/service.py:5523-5526`). Decided (a): they keep
  `unavailable_for_create`. The legacy writer is not changed, and the public
  delete tool still deletes such an automation (§3.4.4).
- **Item 15 (Q7). A created scene that is stored but never registered within
  the bound.** Decided (a): non-success and not rollback-eligible. There is
  no config-only cleanup (§3.3).

> Superseded (historical): before 2026-11-08 the Q-A options were S (strict
> refuse), E (elevated, the author's recommendation) and O (disclosure only).
> S was the fail-closed default if Josh had not answered. Q6 option (b)
> would have changed the legacy writer. Q7 option (b) proposed a separate
> config-only cleanup. None of these is a live option.

**Pending Josh:** none for this design.

Conditional, not a current question: a mixed-plan deferral needs Josh only
if implementation proves a **specific** combination cannot meet §3.4.4; it
is not pre-approved.
