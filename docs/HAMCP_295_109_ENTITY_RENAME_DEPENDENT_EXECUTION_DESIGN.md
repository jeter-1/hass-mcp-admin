# HAMCP-295 / HAMCP-109 design: governed entity ID rename and dependency-bound execution

Status: design for review (2.6.0 feature milestone, docs only). Nothing in this
document is implemented, signed, released, or deployed. It changes no runtime
behaviour, schema, tool, workflow, version, or release declaration.

Revision 2026-11-08: corrected after the Codex independent design review of
head `e025778d`. Section 11 lists each finding and where it is corrected.
Reference rewriting with verified undo is now mandatory scope (section 1.3);
the new rename profile is signed for Core 2026.11.0 only (Signing impact).

- Register: HAMCP-295 (governed entity ID rename), HAMCP-109
  (dependency-bound multi-operation execution).
- Base: `origin/release/2.6.0` =
  `7246f2d21693f039504542574569f03b71476489` (2.4.2-beta.7 materialized).
  Every `file:line` below is at this base unless it is marked as upstream.
- Pinned Home Assistant Core from
  [`ci/support-window.yaml`](../ci/support-window.yaml): current `2026.10.1`,
  candidate `2026.10.3`, rollback `2026.10.0`. Pinned ha-mcp: current `8.5.0`,
  rollback `8.4.3`. The new rename profile targets Core `2026.11.0` only
  (section 1.3). The 2026.10.x source analysis below is kept as background
  evidence; it is not a signing or admission target.
- Upstream authority (ADR-021): `home-assistant/core` at exact tags
  `2026.10.0` (`6a811d3359c7b2076dc9e1cf900843a129c044af`), `2026.10.1`
  (`abe1fdd7cd6b4de1715c87b6fd87d92699514ef9`) and `2026.10.3`
  (`1f214b8461dd1fbbd66e10765c6905f49d3c1d86`). The two Core files that carry
  the rename (`homeassistant/components/config/entity_registry.py`, sha256
  `b932ed29ee9a4d8ec0cf6f52b7862e809acb92deecfdbcfbe92ca231194e293e`, and
  `homeassistant/helpers/entity_registry.py`, sha256
  `f77b7ae946a0902136faadb3cfe45d17aca24fa639c43189295e101f5e47f425`) are
  byte-identical across all three tags. Upstream line numbers are quoted at
  `2026.10.1` and hold for all three tags.
- Upstream authority, Core `2026.11.0` (`74cf5a49c63ed8d4d87717844bf14f79c28c9cea`;
  the tag resolves to this commit, and the base already binds this version in
  `scripts/core_lane_bindings.py` L61-L63, HAMCP-280). At this tag the two
  rename files differ from 2026.10.x: `config/entity_registry.py` has sha256
  `269e4b5123f3613f46c81a04fa2057d8e5461a14bde065fc5318434db53bee9f` and
  `helpers/entity_registry.py` has sha256
  `a16d82bb68ceb680875c9fd8905d7c6f30d0cd39ed7ee7a12ded2c4a9d565088`. The
  rename path through `config/entity_registry/update` (with only
  `{entity_id, new_entity_id}`) and `config/entity_registry/get` is unchanged
  (section 2.4 gives the 2026.11.0 line numbers). The handler changes at this
  tag concern `disabled_by` only. The running-entity side
  (`helpers/entity.py`) changed: section 2.4 describes that difference.
- Signed admission: the canonical signed Core registry journal is the
  `registry` branch (tip `e486028ed0b06b8a5a737824c829705b403b34d8`, seq 17;
  seq 16 is `cf415e29`). Seq 16 and seq 17 both include Core 2026.11.0. The
  `upstream-trust/ha-core-release-registry.json` copy on the base is frozen at
  seq 15 and is not the authority for currently signed versions.

## 1. Register outcome and binding decisions

### 1.1 HAMCP-295 outcome (quoted from the register)

> One exact entity can be renamed (new_entity_id) through a governed plan that
> discloses every automation, script, scene, group, template and dashboard
> reference (HAMCP-227/270 evidence), states what will break, requires
> approval, applies once, reads back, and can be rolled back. HAMCP-069
> deliberately excluded renames until paired with change-impact analysis; that
> pairing now exists.

Row state at writing: Status `Backlog`; dependencies HAMCP-069, HAMCP-270;
Next Action "Feature lane position 2 after HAMCP-253, 267 and 264 (Josh's
ranking, 2026-11-03). Likely needs a signed Core profile change."

### 1.2 HAMCP-109 outcome (quoted from the register)

> Execute exact dependency-bound ordered operations with concurrency
> protection, authoritative verification, and explicit partial-failure handling
> while disclosing material consequences without turning consequence labels
> into a generalized policy program.

Row state at writing: Status `Planned`. Notes include: "Consequence
classification remains disclosure; it does not justify a broad governance
project" and "HAMCP-222 audit (PR #302): no Core signing by itself; each
sequenced operation must be admitted individually."

### 1.3 Decisions that bind this design

| Decision | Source | Effect on this design |
| --- | --- | --- |
| **Recorded owner decision (HAMCP-109 register Next Action, 2026-11-07, design-complete scoping):** HAMCP-109 is "IN 3.0, designed and built around entity rename (HAMCP-295) as its concrete driver: dependent steps verified in order with rollback of earlier steps on later failure. Next: design with 295 in the 2.6.0 feature milestone." | HAMCP-109 register Next Action | Requires an execution mode in which a later failure causes the earlier verified steps to be reversed. This supersedes, for that mode only, the shipped "never roll back automatically" contract (section 2.1). The recorded "build IN 3.0" target stands; the delivery schedule is Remaining Josh choice J3 (section 10). |
| **Josh direction (register 2026-11-07; confirmed in-session 2026-11-08):** the HAMCP-295 rename **rewrites references with verified undo**. Disclose-only is not a deliverable. | HAMCP-295 / HAMCP-109 register rows; 2.6.0 correction brief | Reference rewriting (section 3.3) and verified undo (sections 3.2 and 3.4) are mandatory acceptance (section 7). A rename-only implementation does **not** complete HAMCP-295. This supersedes the earlier open question Q1 (section 10, Superseded). |
| **Josh direction (register 2026-11-07; confirmed in-session 2026-11-08):** additive profiles only; new 2.6 profiles are signed for **Core 2026.11.0 only**, in **one combined 2.6 signing** (one `extend-capabilities` extension of the existing 2026.11.0 entry). No 10.x targets for new profiles. | HAMCP-329 register row; 2.6.0 correction brief | One new additive rename profile on 2026.11.0 only (Signing impact). No existing profile or fingerprint changes. |
| **Recorded owner decision (2026-11-03):** HAMCP-295 is feature lane position 2 after HAMCP-253, 267 and 264. | HAMCP-295 register Next Action | Build order. HAMCP-264 (template reads) is ranked ahead of 295; template coverage is part of Remaining Josh choice J1. |
| Design a new, distinct dependency-bound mode; do not silently change existing multi-operation behaviour; stop and report if the decision cannot be met within existing safety constraints. | Task prompt | Section 3.2. Result: the decision **can** be met without weakening an existing safety constraint (section 3.2.6). It still needs a recorded ADR amendment, because the shipped ADR-018 text forbids exactly this behaviour. |
| HAMCP-069 excluded renames until change-impact analysis existed. | HAMCP-295 outcome | `change_impact_analysis` already models `rename_entity` (section 2.4). |
| Each sequenced operation must be admitted individually (HAMCP-222). | HAMCP-109 Notes | Every forward step and every compensation step passes its own Core admission at dispatch time (section 3.6). |

ADR-022 requires a superseding owner decision to be recorded with its
consequences rather than silently preserved or silently overridden. Section 9
puts that record (a new ADR plus the matching governance text) into the first
build slice. This document does not edit ADR-018 or any other existing file.

## 2. Current state at the base

### 2.1 The shipped multi-operation contract that conflicts with the decision

The current product contract states the opposite of "rollback of earlier steps
on later failure" in five places:

- [`docs/CHANGE_GOVERNANCE.md:186-187`](CHANGE_GOVERNANCE.md): "The review
  explicitly states that execution is non-atomic, stops on the first failure,
  and has no automatic or batch rollback." Line 534: "No automatic rollback
  occurs." Lines 221-224 list rename among things Dev14 does not add, and say
  batch rollback is unavailable.
- `hass_mcp_engineering_beta/ha_mcp_engineering/mcp_server.py:15-16` (server
  instructions sent to every client): "5. Multi-operation plans are
  non-atomic, stop on the first failure, and never roll back automatically."
- `hass_mcp_engineering_beta/ha_mcp_engineering/approval_web.py:1974`: the
  approval page warns "This plan is non-atomic."
- `hass_mcp_engineering_beta/ha_mcp_engineering/tools/governance.py:1060`,
  `:1201`, `:1312`: tool descriptions say children run "in order,
  non-atomically, stopping on the first failure".
- [`ADR-018`](architecture/ADR-018-F3-GOVERNED-ROLLBACK.md) lines 35-36:
  "Rollback does not execute automatically, does not compensate a sequence
  implicitly, and cannot bypass the same exact ... approval". Line 55
  (Consequences): "No generalized compensation, deletion, operational
  rollback, or automatic rollback becomes reachable."

The runtime enforces this. In the F3 apply loop
(`f3_runtime/runtime.py:4524-4545`), each child runs in declaration order, the
loop `break`s on the first child whose terminal outcome is not
`succeeded_verified`, and no reverse dispatch follows. The retry bundle check
(`f3_runtime/runtime.py:~3507-3530`) refuses with
`retry_bundle_prefix_unverified` unless every earlier child is
`succeeded_verified`.

### 2.2 What already exists that this design reuses

| Capability | Evidence at base | Reuse |
| --- | --- | --- |
| Contract v2 configuration plans: 1-8 ordered operations with `depends_on` that points only at earlier operations | `governance/service.py:445` (`MAX_CONFIGURATION_OPERATIONS = 8`); `governance/models.py:332-360` (`ConfigurationOperation`); prepared-ops dependency resolution at `f3_runtime/runtime.py:2908-2931` | The dependency-bound mode is a configuration plan with a new plan-level mode (section 3.2). |
| F3 child execution (ADR-017): one child is at most one dispatch, with a durable intent, selective holds, and readback-only recovery | [`ADR-017`](architecture/ADR-017-F3-CHILD-EXECUTION-AND-HOLDS.md); `_execute_child` at `f3_runtime/runtime.py:3543` | Forward and compensation steps are ordinary F3 children. |
| Governed rollback planner (ADR-018) and HAMCP-135 exact inverse | `rollback_change`; `_plan_fresh_rollback` at `f3_runtime/runtime.py:5271`; reverse selection at `:5074`; `_rollback_bundle_check` at `:5100`; `governance/configuration_rollback_eligibility.py:18` (`MODEL = "hamcp135-exact-inverse-v1"`); [`HAMCP135_GOVERNED_ROLLBACK.md`](HAMCP135_GOVERNED_ROLLBACK.md) | Owner rollback after success stays this mechanism. Compensation borrows its inverse construction, its reverse ordering, and its full-bundle state check. |
| Lock union kept across a phase change | `f3/locks.py:476` `transfer_complete(...)`, which atomically fences an exact complete owner set into its successor | The lock set is held from the first forward write to the end of compensation. |
| Entity registry metadata update (HAMCP-069A) | Resource `entity_registry_metadata`; F3 identity `update_entity_registry_metadata_configuration` (`ha_core_readmission/routes.py:66-72`); profile `core.entity_registry_metadata_operation` (`ha_core_readmission/profiles.py:490-520`); single-operation rule at `governance/service.py:7037-7046`; rollback planner `_create_entity_metadata_rollback` at `f3_runtime/runtime.py:5976` | The pattern for a direct-HA registry write: exact readback, V9 already-present no-dispatch, a static tool target lease, and an exact registry lock. |
| Explicit refusal of `new_entity_id` today | `governance/entity_metadata.py:10` (docstring) and `:125` (refused field list) give `entity_metadata_field_not_supported` | Stays unchanged. Rename is a separate resource and does not widen the metadata allowlist (section 3.1). |
| Reference evidence (HAMCP-227) | [`ENTITY_DEPENDENCY_ANALYSIS.md`](ENTITY_DEPENDENCY_ANALYSIS.md) (global source partition) | The source of disclosure for scenes, groups and dashboards. |
| Plan-level reference disclosure (HAMCP-270) | `governance/global_reference_disclosure.py:25-50`: key `global_source_consumers`, model `global_source_consumers_v1`, source types `scene, group, dashboard, template`, completeness `complete / partial / unavailable`, bounded to 8 references | Reused unchanged on the rename plan. |
| Change impact for renames | [`CHANGE_IMPACT_ANALYSIS.md`](CHANGE_IMPACT_ANALYSIS.md); `impact/service.py:836` (`operation == "rename_entity"` requires `replacement_entity_id`); rules `rename_destination_conflict` (`impact/rules.py:271-274`, `impact/provider.py:340-344`) and `rename_reference_migration_required` (`impact/rules.py:95`, `:460`) | The rename planner calls the same evidence path and binds its assessment into the plan. |
| Static tool catalog | `tools/registry.py:239-240`; `ENGINEERING_STATIC_TOOL_COUNT` evaluates to **81** at base (computed by importing the registry in a scratch venv) | +1 (section 4). |

### 2.3 What is missing

1. There is no governed resource or F3 identity that sends `new_entity_id`.
   It is refused on purpose.
2. There is no Core capability profile that admits
   `config/entity_registry/update` carrying `new_entity_id`. The signed
   metadata profile's response contract lists only `aliases, area_id,
   categories, hidden_by, icon, labels, name` (`profiles.py:490-520`).
3. There is no execution mode in which a later failure reverses earlier
   verified steps. Every rule in section 2.1 forbids it.
4. Templates have no reference read path: HAMCP-264 is in Backlog, and UI
   template helpers have no read path. Script reference enumeration covers
   loaded scripts only. Group membership is the loaded membership from the
   `entity_id` state attribute (HAMCP-227).
5. No governed write adapter exists for groups or templates. Dashboard writes
   exist, but as a separate `ChangeOperation.UPDATE_DASHBOARD`
   (`governance/models.py:40`). They go through the delegated ha-mcp
   dashboard setter ([`F3_DASHBOARD_WRITE_CONTRACT.md`](F3_DASHBOARD_WRITE_CONTRACT.md)),
   not through a configuration-plan child.

### 2.4 Exact Home Assistant behaviour of a rename (upstream, Core 2026.10.x and 2026.11.0)

Each claim below is grounded in exact-tag source. URLs are given at
`2026.10.1`; the files are byte-identical at `2026.10.0` and `2026.10.3`.
Core `2026.11.0` (`74cf5a49`) is covered by the note at the end of this list.
Only the 2026.11.0 evidence supports the new profile's admission (section 5);
the 10.x evidence is background and is not a signing target.

- **Command.** `config/entity_registry/update` is `@require_admin`
  ([config/entity_registry.py#L153-L197](https://github.com/home-assistant/core/blob/2026.10.1/homeassistant/components/config/entity_registry.py#L153-L197)).
  The schema accepts `Optional("new_entity_id"): str` (L178). The handler
  collects `new_entity_id` into `changes` (L218-229) and calls
  `registry.async_update_entity(entity_id, **changes)`. A `ValueError` becomes
  error `invalid_info` (L269-276). If the entity is unknown, the handler
  returns `ERR_NOT_FOUND` (L214). After the update, the handler switches to the
  new id (L281-282) and responds with `{"entity_entry": entity_entry.extended_dict}`
  (L295).
- **Validation.** `_async_update_entity`
  ([helpers/entity_registry.py#L2051-L2062](https://github.com/home-assistant/core/blob/2026.10.1/homeassistant/helpers/entity_registry.py#L2051-L2062))
  raises "Entity with this ID is already registered" when
  `_entity_id_available(new)` is false, "Invalid entity ID" when the id is
  invalid, and "New entity ID should be same domain" on a domain change.
  `_entity_id_available`
  ([L1355-L1367](https://github.com/home-assistant/core/blob/2026.10.1/homeassistant/helpers/entity_registry.py#L1355-L1367))
  is true when the id is not registered **and** is available in the state
  machine. An id that belongs to a **deleted** registry entry counts as
  available.
- **Effect.** The registry entry keeps its `id` and `unique_id`. The entities
  map pops the old key and inserts the new one (L2116). An
  `EVENT_ENTITY_REGISTRY_UPDATED` event fires with `action: update`,
  `changes` holding the old values (including `entity_id`), and
  `old_entity_id` (L2124-2132).
- **Running entity.** `Entity._async_registry_updated`
  ([helpers/entity.py#L1617-L1680](https://github.com/home-assistant/core/blob/2026.10.1/homeassistant/helpers/entity.py#L1617-L1680))
  force-removes the old entity (L1671) and re-adds it under the new id through
  `platform.async_add_entities` (L1678). This happens asynchronously after the
  websocket result. A disabled entity is only removed.
- **Recorder.** `components/recorder/entity_registry.py` (L28-35) listens for
  the event and migrates statistics and states metadata from the old id to the
  new one. If the new id is already present in `states_meta`, it logs "Cannot
  migrate history ... because the new entity_id is already in use" (L72). The
  recorder itself decides this, and Engineering cannot read it.
- **Helper config entries.** `helpers/helper_integration.py`
  (`async_handle_source_entity_changes`, L14-40 and L84-93) updates helper
  entries that reference the source by entity id, and reloads entries that
  reference it by registry UUID.
- **Registry-entry state follows the rename:** aliases, labels, area,
  categories, options, and assistant exposure for registered entities
  (`exposed_entities.py` reads registry `options`, L252-256).
- **References are not rewritten.** In the cited files, Core does not rewrite
  any reference held in an automation, script, scene, group, template or
  dashboard. Engineering's `change_impact_analysis` already "never assumes
  Home Assistant rewrites a reference"
  ([`CHANGE_IMPACT_ANALYSIS.md`](CHANGE_IMPACT_ANALYSIS.md)). After a rename,
  every such reference points to an id that no longer exists until it is
  changed.
- **Automatic path.** `config/entity_registry/...` also offers
  `async_regenerate_entity_id` (L362). That is an automatic id path and is
  out of scope.
- **Core 2026.11.0 (`74cf5a49`).** Checked against exact-tag source only.
  - `config/entity_registry/get`
    ([L99-L122](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/components/config/entity_registry.py#L99-L122))
    is unchanged: it returns `ERR_NOT_FOUND` for an unknown id (L117) and
    `entry.extended_dict` otherwise (L122).
  - `config/entity_registry/update`
    ([L153-L283](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/components/config/entity_registry.py#L153-L283))
    keeps `@require_admin` (L153), `Optional("new_entity_id"): str` (L177),
    `ERR_NOT_FOUND` (L213), `new_entity_id` in `changes` (L226), `invalid_info`
    on `ValueError` (L265), the switch to the new id (L269-L270), and the
    `{"entity_entry": entity_entry.extended_dict}` response (L283). The only
    handler changes at this tag concern `disabled_by`: the "Device is disabled"
    refusal on enable is removed, and the reload hint now keys on a
    disabled-to-enabled transition. A rename-only request does not reach
    either change.
  - Validation in `_async_update_entity` (L2022) is identical to 2026.10.1
    ("already registered" L2136, "Invalid entity ID" L2139, "same domain"
    L2142). `_entity_id_available` (L1435-L1447) is identical, so the
    deleted-id caveat still holds. The entities map pops the old key at L2218,
    and the event carries `old_entity_id` at L2233. The event's `changes` now
    also drops `compat_name` (a new registry field). Other registry changes at
    this tag (`compat_name`, minor storage version 24, the derived
    `disabled_by` path, and the area or child-device checks) are not triggered
    by a rename-only update. Readback compares preflight with post-apply on the
    same Core, so a different field set on 2026.11.0 does not affect the
    "every other field equals preflight" check (section 3.1).
  - Recorder `entity_registry.py` changed only its imports. The migration
    listener (L24-L44) and the "already in use" warning (L73) are unchanged.
    `helpers/helper_integration.py` is byte-identical
    (`eedfca79e0ac213fcf45cd4bcab6fa82ddd0a511e3723485388ab55657fb8972`).
  - **Running entity changed.** In `helpers/entity.py` at 2026.11.0
    ([L1712-L1810](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/helpers/entity.py#L1712-L1810))
    an entity of a Core integration is renamed **in place**. Core removes the
    old state, moves the entity's registrations to the new id, and writes the
    new state (L1773-L1788) without removing and re-adding the entity. Only
    custom-integration classes that define add or remove hooks without
    `async_entity_id_changed` still take the force-remove and re-add path
    (`_entity_class_requires_readd`, L115; `_async_readd_on_entity_id_change`,
    L1790-L1810; removal planned for Core 2027.11). A disabled entity is still
    only removed. This changes nothing in the design. The state-machine
    outcome stays a bounded observation and is not a verification gate
    (section 3.1). Engineering does not rely on its timing relative to the
    websocket result. The disclosure names the outcome as "re-add or in-place
    move", depending on Core version and integration.

## 3. Proposed design

### 3.1 Rename step (HAMCP-295)

**New resource and identity.** The design adds resource type
`entity_registry_rename` with action `update`, and F3 identity
`rename_entity_registry_entry`. It is separate from `entity_registry_metadata`
for three reasons:

1. The metadata allowlist and its `entity_metadata_field_not_supported`
   refusal stay exactly as shipped.
2. The signed metadata profile fingerprint
   (`sha256:cc09630efbc0e1e6d0a587696406f6502c62fe730a767bc0a8cfc96a9988f767`)
   does not change. Changing it would de-admit the shipped metadata tool on
   every Core version until Josh re-signs.
3. A rename has different consequences: it breaks references and migrates
   identity, where metadata changes a label.

The plan family stays the contract v2 configuration plan
(`ChangeOperation.CONFIGURATION_PLAN`). No new `ChangeOperation` enum value is
added. The 069A design chose the configuration family for the same reason (a
new enum value breaks downgrade; [`HAMCP_069A_ENTITY_METADATA_DESIGN.md`](HAMCP_069A_ENTITY_METADATA_DESIGN.md) §3.1).

**Tool.** New static tool `create_entity_rename_plan`. It is planning only: it
writes nothing to Home Assistant, and only `apply_change_plan` dispatches.

Input (closed schema, `additionalProperties: false`):

| Field | Rule |
| --- | --- |
| `entity_id` | Required. Canonical, registered, exact (no pattern or glob). |
| `new_entity_id` | Required. Canonical, valid (`valid_entity_id` rules), same domain as `entity_id`, different from it, and not registered or present in the state machine at planning time. |
| `title`, `description` | Bounded plan text, with the same secret screening as `create_configuration_plan` (`governance/service.py` persistence safety check). |

There is no caller field for rewrites. The planner always generates the
dependent rewrite steps from evidence (section 3.3); the caller cannot add,
remove, or supply a rewrite payload. Unknown fields are refused by the closed
schema.

Output: the standard plan projection (plan id, plan hash, approval URL, state)
plus the following:

- `rename`: old id, new id, registry `id` and `unique_id`, platform,
  config_entry_id, and disabled/hidden state.
- `change_impact`: the `rename_entity` assessment, bound into the plan hash.
- `global_source_consumers`: the HAMCP-270 block, informational.
- `execution_mode`.
- `steps`, each with its compensation (section 3.2).
- `breaks`: the statement of what will break.
- `consequences`: recorder migration, helper config-entry follow/reload,
  deleted-id caveat, and asynchronous re-add or in-place move (section 2.4).

**Routing and provider.** The step is Engineering-native, direct Home
Assistant, following the 069A pattern:

- Provider `direct_home_assistant_entity_registry`, with a new provider
  contract `direct-ha-exact-entity-registry-rename-v1` and `fallback: none`.
- No ha-mcp tool is used. The ha-mcp registry tools are not delegated for
  this, and no upstream dispatch becomes reachable.
- Route key: tool `create_entity_rename_plan` maps to
  (`core.dependency_helper_planning`, `core.entity_registry_rename_operation`),
  mirroring `ha_core_readmission/routes.py:276`. The F3 identity
  `rename_entity_registry_entry` maps only to the new profile. Unknown
  identities keep mapping to `core.unsupported_f3_operation`
  (`routes.py:~1020-1045`).
- The static tool target lease (069A V11, `routes.py:803`) returns
  `{"target_type": "entity_registry_rename", "target_id": <old entity_id>}`.

**Core commands** (admitted on Core 2026.11.0 only; `config/entity_registry/update` is `@require_admin`, while `config/entity_registry/get` carries no admin decorator; on any other Core version the rename profile is unsigned and the tool refuses before plan creation):

| Phase | Command | Purpose |
| --- | --- | --- |
| Preflight and readback | `config/entity_registry/get` with `entity_id: <old>` | Exact entry, identity (`id`, `unique_id`), all fields |
| Preflight and readback | `config/entity_registry/get` with `entity_id: <new>` | Must be `ERR_NOT_FOUND` before the forward step; after it, must return the same `id`/`unique_id` |
| Preflight | State presence read for `<new>` through the already-signed direct entity state read | The new id must be absent from the state machine |
| Dispatch | `config/entity_registry/update` with exactly `{entity_id: <old>, new_entity_id: <new>}` | The only write. No other key is ever sent in the same message |

**Stale-state and concurrency.**

- **Planning fingerprint.** The plan binds:
  - the exact registry entry of `<old>`, minus `modified_at` and
    `next_name_part` (069A V2);
  - registry `id` and `unique_id`;
  - the absence of `<new>` from the registry and the state machine;
  - the impact assessment hash.
- **Apply (before the first write in the plan).**
  - Acquire every lock listed below.
  - Re-read all targets. Any mismatch means zero writes and no approval
    consumption (the current rule, [`CHANGE_GOVERNANCE.md`](CHANGE_GOVERNANCE.md)
    apply section).
  - If `<new>` became occupied, refuse with
    `entity_rename_destination_occupied`.
  - If `<old>` is gone or its `id` changed, refuse with
    `entity_rename_source_changed`.
- **Locks.**
  - Exclusive `entity_registry:<old>` and exclusive `entity_registry:<new>`.
    These are the same key family as the metadata, availability and exposure
    plans, so a concurrent metadata plan on either id conflicts.
  - Shared `core:home_assistant`.
  - Exclusive `reload:entity_registry_rename`, so two renames never
    interleave registry events.
  - Each rewrite step (section 3.3) also takes the exact resource, reload
    domain and core lock set the existing adapter for that holder already
    uses. The plan's lock union is acquired before the first write.
- **Narrow race.** Core offers no compare-and-swap on the registry. A write
  that lands between the final re-read and dispatch is reported as
  indeterminate, the same as today.

**At-most-once dispatch.** The rename child is one F3 child: durable intent
before send, one send, never redispatched. Recovery is readback only
(`recovery: read_only_no_redispatch`).

**Already present (V9) versus source changed.** At the rename child's final
preflight (after locks, before intent) exactly one of three cases holds:

| Case | Exact condition | Child result |
| --- | --- | --- |
| Dispatch | `get(<old>)` equals the planning fingerprint and `<new>` is absent from the registry and state machine | Durable intent, one send, readback |
| Already present (no-op) | `get(<old>)` is `ERR_NOT_FOUND` **and** `get(<new>)` returns the planned registry `id`, `unique_id`, `platform` and `config_entry_id` **and** every other field equals the planning fingerprint (except `entity_id`, `modified_at`) | No intent, `dispatch_count == 0`, event `preflight_noop_verified`. Section 3.2.2a defines what this means for dependants, compensation and rollback |
| Source changed / destination occupied | Anything else | Determinate pre-dispatch refusal (`entity_rename_source_changed` or `entity_rename_destination_occupied`), zero writes |

The already-present case is checked only against the planned identity. It is
never inferred from a partial match.

**Authoritative readback.** The rename verifies only when all of these hold:

1. `get(<new>)` returns the same `id`, `unique_id`, `platform` and
   `config_entry_id`.
2. Every other field equals preflight, except `entity_id` and `modified_at`.
3. `get(<old>)` returns `ERR_NOT_FOUND`.

The state-machine outcome (an asynchronous re-add on 2026.10.x, or on
2026.11.0 an in-place move for Core integrations and a re-add for unmigrated
custom integrations; section 2.4) is reported as a bounded **observation**,
not a verification gate. A disabled entity never re-adds. Recorder migration and helper config-entry follow-up cannot be
verified through any admitted read. They are disclosed as unverified Core
consequences and are never reported as verified.

**Disclosure ("states what will break").** The approval page and the plan
projection show the following:

- Every reference found through the `rename_entity` impact evidence:
  automations, scripts (loaded only, labelled partial), scenes, groups
  (loaded membership), dashboards (reviewed list, bounded), and templates
  (`unavailable` with the HAMCP-264 reason until that lands). Each reference
  carries its completeness label.
- For each reference, whether a dependent step of this plan rewrites it
  (section 3.3) or it is **not rewritten** with its exact reason. A plain
  statement that each not-rewritten reference will point to a missing entity
  after the rename.
- Completeness: under `no_known_impacts_with_incomplete_coverage` the page
  states that unlisted references may exist.
- Core consequences:
  - recorder history migration, which can fail if the new id already has
    history;
  - helper config-entry follow and reload;
  - the deleted-entity-id caveat (an id held by a deleted registry entry
    counts as free);
  - asynchronous re-add, or in-place move on Core 2026.11.0 (section 2.4).

Disclosure never downgrades risk and is not fingerprinted beyond the existing
HAMCP-270 rules. Consequence labels stay decision support (HAMCP-109 Notes).

**Policy class.** The F2 policy class of a rename step is undecided. It is
Remaining Josh choice J2 and is not set here. The mechanics support either
`standard_admin` (one approval action) or `elevated_admin` (plan approval plus
`elevated_risk_acknowledgement`).

### 3.2 Dependency-bound execution mode (HAMCP-109)

#### 3.2.1 Shape

A configuration plan gains a plan-level field `execution_mode`, bound into the
plan hash:

- **`ordered_stop_on_failure`.** The implied value for every existing plan,
  and the value every existing planner keeps producing. Behaviour does not
  change at all: the section 2.1 contract still applies word for word to
  these plans.
- **`dependency_bound_compensating_v1`.** The new mode. Only the rename
  planner produces it, in the release that ships HAMCP-295 complete (J3).
  `create_configuration_plan` never accepts
  it as caller input. A caller-supplied `execution_mode` is refused with
  `execution_mode_not_caller_selectable`.

In the new mode, every operation carries one more persisted field,
`compensation`. It holds the exact inverse step, computed at **planning** time
from exact persisted prior state:

| Forward step | Compensation step | Expected pre-compensation state |
| --- | --- | --- |
| Rename `<old>` to `<new>` | Rename `<new>` to `<old>` (same command, same profile) | Registry entry at `<new>` equals the verified forward result; `<old>` free in registry and state machine |
| Automation/script reference rewrite (section 3.3) | Restore the exact prior config (HAMCP-135 inverse; existing governed configuration adapter) | Current config equals the forward step's verified post-apply fingerprint |
| Scene reference rewrite (section 3.3) | Restore the exact prior scene config (HAMCP-071 adapter) | Same rule |

A compensation step is a precomputed **candidate**. Whether it ever runs is
decided only by the ownership rules in section 3.2.2a, from durable child
records, never from the plan alone.

The approval page shows each forward step **and** its compensation, with
exact payloads. Both are covered by the one plan hash, so the owner approves
the compensation together with the forward steps. No write that the owner did
not approve can occur. Approval semantics are unchanged: one hash-bound
external approval, plus the elevated acknowledgement if the policy class
requires it.

#### 3.2.2 Forward execution

Forward execution is identical to the current loop for dispatch, holds and
verification. Each step is one F3 child: intent, admission, dispatch at most
once, exact readback. A step runs only after every step in its `depends_on`
is in a dependency-satisfying class (section 3.2.2a), and the dependent
child's own final preflight re-reads the predecessor's postcondition (for a
rename predecessor: `get(<new>)` returns the planned registry `id`, and
`get(<old>)` is `ERR_NOT_FOUND`). A dependency is a state predicate checked at
the dependant's preflight; it is never a claim of write ownership.

#### 3.2.2a Per-child dispatch ownership, no-op semantics and accounting

Every child in this mode, forward or compensation, ends in exactly one class.
The class is derived only from the child's durable F3 record and declaration,
never from the parent task state, the parent `terminal_outcome`, a projection
field such as `completed_operation_ids`, or full-bundle state equality. This
mirrors the HAMCP-135 rule that "a no-op at a proposed body is not evidence of
our forward write" (`governance/configuration_rollback_eligibility.py:87-94`).

| Class | Durable record proof (all required) | Satisfies dependants | Compensation / owner-rollback candidate | Accounting |
| --- | --- | --- | --- | --- |
| `owned_verified` | Declaration bound to this plan (`plan_id`, `plan_hash`, `public_task_id`, `operation_id`, `operation_ordinal`, `operation_dependency_ids`, target, `capability_id`, `request_id`, `approval_bundle_hash`, `prepared_operation_hash`); record identity matches the declaration (`plan_id`, child `task_id`, `request_id`, `attempt_id`); `dispatch_intent` present with `provider_arguments_hash` equal to the prepared forward payload; `dispatch_count == 1` (reserved at intent commit, `f3/persistence.py:577-590`); `normalized_outcome == "succeeded_verified"`; evidence `resulting_state_fingerprint` equals the planned forward result; record terminal and `validate()` passes | Yes | **Yes**, and only this class | `owned_operation_ids` |
| `already_present_verified` (no-op) | `dispatch_intent is None`, `dispatch_count == 0`, event `preflight_noop_verified`, current state equals the planned forward result | Yes (its postcondition holds and is re-read by each dependant) | **Never.** Engineering did not make this change, so no inverse is sent | `noop_operation_ids`; per-step `changed_by_engineering: false` |
| `not_applied_determinate` | Refused before intent (`dispatch_count == 0`, no intent), **or** intent present and authoritative readback proves the forward payload was not applied | No | No | `failed_operation_ids` |
| `indeterminate` | Intent present and readback cannot prove applied or not applied | No | No; holds the plan (3.2.4) | `unresolved_operation_ids` |
| `not_attempted` | No record | No | No | `not_attempted_operation_ids` |

Further rules:

- A child recovered after a restart keeps the class its durable record
  proves. A recovered child with no intent whose readback shows the forward
  result is `already_present_verified`, never `owned_verified`: recovery
  readback never creates write ownership.
- A record whose evidence claims success but lacks an intent, carries a
  `provider_arguments_hash` or `prepared_operation_hash` that does not match
  the declaration, belongs to another plan, task, attempt or ordinal, or has
  `dispatch_count` other than 1, has no compensation or rollback authority.
  Such a record is treated as `indeterminate` for this plan (hold and manual
  review), never as `owned_verified`.
- Before any compensation intent, the runtime reloads the exact source plan,
  task, manifest (`sequence_hash`) and every child declaration and record, and
  re-derives every class (as HAMCP-135 `source_snapshot` does). The plan's
  precomputed compensation payload is used only for children re-derived as
  `owned_verified`.
- `completed_operation_ids` keeps its shipped meaning for compatibility. The
  three new keys (`owned_operation_ids`, `noop_operation_ids`,
  `compensated_operation_ids`) are additive projections. No projection key is
  an authority input.

#### 3.2.3 When compensation runs

Compensation runs only when all three conditions hold:

1. A forward step `k` ended `not_applied_determinate`:
   - refused before dispatch: stale target, admission refusal, lock or lease
     loss, or provider refusal with no send; or
   - dispatched, with authoritative readback proving it was **not** applied
     (verified failure).
2. Every step before `k` that was attempted is determinate
   (`owned_verified`, `already_present_verified` or
   `not_applied_determinate`).
3. At least one earlier step is `owned_verified`.

If conditions 1 and 2 hold but 3 does not (every earlier step is a no-op or
none was attempted), no compensation runs and the outcome is
`failed_no_writes`: this plan wrote nothing, and no-op steps are left as found.

When all three hold, the runtime persists a durable `compensation_decided`
record (section 3.2.8) **before** the first compensation intent. It then runs
the compensation steps of the `owned_verified` steps only, in **reverse**
declaration order, one F3 child each. `already_present_verified` steps are
skipped and reported as `excluded_from_compensation: not_dispatched_by_this_plan`.
Each compensation child gets its own intent, its own Core admission, at most
one dispatch, and exact readback, and is classified by the same table.

#### 3.2.4 Indeterminate outcomes never compensate

If any attempted step is indeterminate (dispatch occurred and readback cannot
prove applied or not applied), the plan holds. No compensation runs, and the
current selective hold applies (ADR-017). The only path forward is readback-only
reconciliation:

- If reconciliation proves the step applied **and** its durable record meets
  every `owned_verified` proof in section 3.2.2a, it becomes `owned_verified`.
  If the forward result is present but the record has no intent, it becomes
  `already_present_verified` and has no compensation authority.
- If reconciliation proves it did not apply, it becomes
  `not_applied_determinate`.
- Reconciliation only establishes state. It never itself creates a
  compensation decision or any dispatch (section 3.2.8).

Either way, rule 3.2.3 is then evaluated again. If the outcome stays unknown,
the task ends in manual review with no compensation dispatch. This keeps the
F3 invariant: never act on an unknown outcome.

#### 3.2.5 Drift before compensation stops the sequence

Before each compensation child, the runtime runs the HAMCP-135 full-bundle
state check (as `_rollback_bundle_check`, `runtime.py:5100`) across every
object the plan touched. The compensated object must equal its verified
forward result, every not-yet-compensated object must equal its expected
state, and every `already_present_verified` object must still equal the
planned forward result (it is checked, never written). If any check fails, the runtime stops with `compensation_drift`,
dispatches nothing more, lists each step's state, and ends in manual review.
It never overwrites a change it did not make. Each compensation step gets one
deterministic attempt (as HAMCP-135). A failed compensation is not retried.

#### 3.2.6 Why this stays within existing safety constraints

| Safety constraint | Preserved because |
| --- | --- |
| No unapproved write | Every compensation payload is exact, precomputed, shown, and hash-bound before approval. |
| At-most-once dispatch | Compensation children are F3 children with durable intents. Each forward and compensation child dispatches at most once. |
| Fail closed on unknown state | Indeterminate outcomes hold and never trigger compensation (3.2.4). Drift stops the sequence (3.2.5). |
| Exact target and authority | Same targets, same locks, same Core admission per child (HAMCP-222). |
| No overwrite of foreign changes | The full-bundle check runs before every compensation child. |
| No silent change to existing plans | Existing plans keep `ordered_stop_on_failure`. The mode is not caller-selectable. |
| Consequence labels are not policy | No new policy class or risk program. Compensation is triggered by execution outcome, not by a consequence label. |

The one rule that does change is ADR-018 "does not compensate a sequence
implicitly". In the new mode, compensation is **explicit**: it is shown and
approved up front, not implicit. It is still automatic in timing, and the
ADR-018 Consequences line forbids automatic rollback. That is why section 9
slice S0 records the 2026-11-07 HAMCP-109 decision as a scoped amendment (new
ADR, next free number at build time). The amendment covers only
`dependency_bound_compensating_v1` and keeps ADR-018 unchanged for every
other plan. It must amend three ADR-018 statements, each for this mode only:

1. "does not compensate a sequence implicitly": compensation is explicit,
   pre-approved and hash-bound;
2. "No ... automatic rollback becomes reachable": compensation of the owned
   verified prefix is automatic in timing after a determinate failure;
3. "The forward approval is never reused": compensation children execute
   under the forward plan's single consumed approval, because their exact
   payloads were part of that approval's hash. Owner rollback after success
   (`rollback_change`, section 3.4) is unchanged and still needs its own new
   approval.

I do not treat this as a conflict to stop on: the decision explicitly asks
for the behaviour, and every execution-integrity rule still holds.

#### 3.2.7 Lock retention

The lock union of all forward targets is acquired before the first write, as
today. It is held through compensation completion and is not released per
step. At the forward-to-compensation phase change, the runtime transfers the
complete owner set with `f3/locks.py:476` `transfer_complete`, the same
mechanism HAMCP-135 uses. If the transfer fails (a lease expired, or a record
mismatches), the sequence stops before any compensation dispatch, with
`compensation_locks_lost`, and ends in manual review.

#### 3.2.8 Restart

If the Engineering add-on restarts during a sequence, recovery follows ADR-017:

- An in-flight child is reconciled by readback only.
- Compensation continues only when the owner or client re-enters through
  `apply_change_plan` with the same `expected_plan_hash` caller-intent guard
  (`runtime.py:4508-4511`). Before continuing, the runtime reacquires the
  complete lock set and runs a full bundle check.
- No background or autonomous dispatch is added.

Build gates for crash consistency (required, not optional):

- **Authority records.** `compensation_decided`, the phase
  (`forward` / `compensating` / `terminal`), and each compensation child's
  identity are authoritative durable records in the F3 task and child store,
  written crash-consistently (atomic write, as child records are today). The
  decision binds `plan_id`, `plan_hash`, `task_id`, `sequence_hash`, the
  failed step's `operation_id` and child id, the reason, and the exact
  ordered compensation set (forward `operation_id`, forward child id, forward
  record hash, compensation `prepared_operation_hash`). The audit event of the
  same name is a mirror for attribution only. The audit log is never read as
  authority for phase, ownership or the compensation set.
- **Idempotent re-entry.** A persisted decision is never recomputed with a
  different set. On re-entry the runtime re-derives every class (3.2.2a) and
  refuses (`compensation_decision_mismatch`, manual review) if the result
  differs from the persisted decision.
- **Reconciliation does not dispatch.** Recovery readback may resolve an
  indeterminate child, but resumed forward or compensation work starts only
  from an owner or client `apply_change_plan` re-entry with the matching
  `expected_plan_hash`, after the full lock reacquisition and full-bundle
  check.
- **Crash points to test:** before and after `compensation_decided`
  persistence; before and after each compensation intent; lease expiry and
  hold transfer during the phase change; duplicate callers during
  compensation; an indeterminate compensation child; re-entry when part of
  the prefix is already compensated (already-compensated children are
  read back and never re-sent).

#### 3.2.9 Terminal outcomes

These are new `terminal_outcome` codes on the existing task states. No new
`ExecutionTaskState` value is added, following the 069A
`ENTITY_METADATA_NOT_DISPATCHED_OUTCOME` precedent (`runtime.py:~4593`):

| Outcome | Task state | Meaning |
| --- | --- | --- |
| `succeeded_verified` | `SUCCEEDED_VERIFIED` | Every forward step is `owned_verified` or `already_present_verified`, and at least one is `owned_verified`. No-op steps are listed in `noop_operation_ids` with `changed_by_engineering: false` |
| `desired_state_already_present_not_dispatched` (status `already_present`) | `SUCCEEDED_VERIFIED` | Every forward step is `already_present_verified`; nothing was dispatched (V9; same code as `ENTITY_METADATA_NOT_DISPATCHED_OUTCOME`, `governance/entity_metadata.py:45`) |
| `failed_no_writes` | failed, no dispatch by this plan | A step failed determinately and no earlier step is `owned_verified` (each earlier step is a no-op or was not attempted). No compensation; no-op steps are left as found |
| `failed_compensated_verified` | `FAILED_POST_DISPATCH` | A later step failed; every `owned_verified` step was reversed and verified, so every object Engineering wrote equals its preflight state. `already_present_verified` steps were not reversed and are listed as `excluded_from_compensation` |
| `failed_compensation_incomplete` | `MANUAL_REVIEW_REQUIRED` | Compensation drift, a compensation failure, admission refusal of a compensation step, or lock loss. Each step's state is listed |
| `held_indeterminate` | `MANUAL_REVIEW_REQUIRED` | A step's outcome is unknown; no compensation ran |

`apply_change_plan`, `get_execution_task` and `get_change_plan` report each
forward step and each compensation step with:

- `provider_dispatch_occurred`;
- its section 3.2.2a class and `changed_by_engineering`;
- the verified outcome;
- the reason for any step that was not attempted or was excluded from
  compensation.

`status: applied` appears only for `succeeded_verified`; the all-no-op case
reports `status: already_present`.

### 3.3 Dependent reference rewrites (mandatory)

Reference rewriting is mandatory scope (Josh direction, section 1.3). Every
rename plan generates its rewrite steps; a plan never consists of a rename
alone when a rewritable reference exists. A rename-only implementation does
not complete HAMCP-295.

**Generated, not supplied.** For each reference holder that has exact
evidence and a reviewed, admitted governed write adapter, the planner:

1. reads the holder's current config through the existing governed read;
2. rewrites only the occurrences inside the exact boundary below;
3. proposes the exact rewritten config as an ordinary update operation with
   `depends_on: [<rename operation_id>]`, plus its exact compensation
   (section 3.2.1).

The caller supplies no config and cannot select holders.

**Exact rewrite boundary.** An occurrence of `<old>` is rewritten to `<new>`
only when **all** of the following hold:

| # | Condition |
| --- | --- |
| B1 | The holder is an automation or script config (`update_automation_configuration` / `update_script_configuration`, existing governed configuration adapter), or a scene config (HAMCP-071 `scene_configuration` adapter). No other holder type is written. |
| B2 | The HAMCP-227 extractor (`dependency/extraction.py`, `add_structured`) reports the occurrence for this holder at planning time as an obligation with `obligation_kind == "structured_entity_reference"`, `outcome == "exact_dependency"` and finding `match_type == "structured_exact"`, with `target_entity_id == <old>`. |
| B3 | For automation and script holders, the structural position is the value of an `entity_id` key (`ENTITY_BEARING_KEYS`, `extraction.py:41`) at the reported `config_path`, and the leaf is either (a) a string exactly equal to `<old>`, or (b) a direct element of a list value of that key, exactly equal to `<old>`. Only that leaf string is replaced. |
| B4 | For scene holders, the position is the mapping key `<old>` of `$.entities` (relation `scene_entity`, `extraction.py:950-958`). The key is replaced by `<new>`; its value and the key order are preserved. |
| B5 | The number of leaves the rewriter replaces in the holder equals the number of B2 occurrences for `<old>` in that holder, and the paths match one to one. |
| B6 | Re-running the same extractor on the proposed config yields no B2 occurrence of `<old>`, the same paths now naming `<new>`, and every other finding and obligation unchanged. The normalized proposed config differs from the normalized current config only at the replaced leaves. |

**Never rewritten (disclosed).** Each such occurrence is listed per holder as
"not rewritten: will break" with its exact reason code:

| Occurrence | Reason code |
| --- | --- |
| `<old>` inside any template string (Jinja), in any field | `template_occurrence_not_rewritten` (no textual Jinja replacement) |
| Blueprint input value (`blueprint_input`, `match_type == "blueprint_resolved"`) | `blueprint_input_not_rewritten` |
| `state_changed` or `call_service` event-trigger data (`state_changed_event_trigger`, `call_service_event_trigger` obligations) | `event_data_occurrence_not_rewritten` |
| An `entity_id` value in any other shape: comma-separated string, nested mapping, value deeper than a direct list element | `unsupported_value_shape` |
| Whole-configuration, coverage-failure or external-blueprint obligations for the holder | `holder_coverage_incomplete` |
| B5 or B6 fails for the holder | `rewrite_evidence_mismatch` (the whole holder is not rewritten) |
| Holder type without an admitted governed adapter: dashboards, groups, templates, UI helpers | `no_governed_writer` |
| Holder adapter not admitted on the running Core at planning time | `holder_adapter_not_admitted` |

A holder can be partly rewritten: its B-boundary occurrences are rewritten and
its other occurrences are listed as not rewritten. Free-text fields (`alias`,
`description` and similar `FREE_TEXT_KEYS`) are neither rewritten nor counted
as breakage. Whether a plan with any not-rewritten occurrence proceeds with
disclosure or is refused is Remaining Josh choice J1. Until J1 is answered the
build contract defaults to **proceed with per-occurrence disclosure and a
count on the approval page**, and the plan never claims full coverage.

This design adds no dashboard delegation, no group or template writer, and no
textual Jinja replacement. Each would be new provider or write scope with its
own review and admission; none can hide inside the rename profile.

**Execution order (fixed).**

1. Ordinal 0: the rename step (`rename_entity_registry_entry`).
2. Ordinals 1..n: rewrite steps, ordered by holder type (automation, script,
   scene) and then by holder id (code-point order). Each has
   `depends_on: [<rename operation_id>]` and runs sequentially in declaration
   order.

Reasons: the commonest determinate failures (destination occupied, Core
`invalid_info`, stale source) then end with zero writes and no compensation;
each rewrite is written only after its new target id verifiably exists (the
dependency postcondition re-read in section 3.2.2); and compensation unwinds
rewrites in reverse before the rename. The broken window (references name the
missing old id until each rewrite verifies) is disclosed. If the rename child
is `already_present_verified`, rewrites still run (its postcondition holds),
and a later rewrite failure compensates only the owned rewrites; the rename is
never reversed by this plan.

**Eligibility per holder type (Core 2026.11.0, where the rename is admitted):**

| Holder | Write adapter | Admission on 2026.11.0 | Dependent step |
| --- | --- | --- | --- |
| Automation | Existing governed configuration adapter | `core_governed_configuration_operation_v1` | Yes (B1-B6) |
| Script | Same | Same | Yes (B1-B6) |
| Scene | HAMCP-071 `scene_configuration` | `core_scene_configuration_operation_v1` (canonical registry seq 16 and 17) | Yes (B1, B2, B4-B6) |
| Dashboard | Delegated ha-mcp setter under `ChangeOperation.UPDATE_DASHBOARD`, not a configuration-plan child | n/a | No: `no_governed_writer` (disclosed) |
| Group | None | n/a | No: `no_governed_writer` (disclosed) |
| Template | No read path (HAMCP-264) and no write adapter | n/a | No: disclosed as `unavailable` |

Each rewrite child is still admitted individually at dispatch (section 3.6).

**Limit.** The rename plus its rewrites must fit within the existing
`MAX_CONFIGURATION_OPERATIONS = 8` (`governance/service.py:445`): the rename
and up to 7 rewrite steps. Above that, the planner refuses with
`entity_rename_dependent_steps_exceed_limit` and the count. It never splits a
rename into several plans, because splitting would lose the compensation
boundary. The bound is kept; raising it is not proposed.

### 3.4 Rollback after success (owner-requested verified undo)

This covers the HAMCP-295 requirement that a rename "can be rolled back".
Verified undo has two mandatory forms: pre-approved compensation during a
failed forward sequence (section 3.2), and owner-requested rollback after
success, described here. Both are acceptance requirements (section 7).

After `succeeded_verified`, owner rollback stays ADR-018 `rollback_change`:
the planner builds a reverse plan from persisted prior state, with its **own**
new approval (the forward approval is not reused here). It is refused unless
the current state equals the forward result.

The design adds `_create_entity_rename_rollback`, alongside
`_create_entity_metadata_rollback` (`runtime.py:5976`), using the HAMCP-135
exact-inverse model:

- **Source authority.** The planner reloads the exact source plan, task,
  manifest and every child declaration and record, and re-derives each
  child's class (section 3.2.2a). It selects only `owned_verified` forward
  steps. It never infers ownership from the task's `SUCCEEDED_VERIFIED` state,
  its `terminal_outcome`, a projection, or the current state equalling the
  forward result.
- **Selection.** The owned rewrite steps in reverse declaration order, then
  the rename (if owned) last. `already_present_verified` steps are reported as
  `excluded: not_dispatched_by_this_plan` and receive no inverse. If no step is
  owned (all no-op), the planner refuses with `rollback_not_available` and
  reason `forward_not_dispatched` (the shipped V9 reason).
- **Refusals.** If the old id is now occupied: `rollback_not_available`,
  reason `entity_rename_rollback_destination_occupied`. If any selected
  object no longer equals its forward result: `rollback_not_available`,
  reason `forward_state_drifted`. A task ending `held_indeterminate` or
  `failed_compensation_incomplete` is refused with reason
  `dependency_bound_manual_review`; recovery there is a new, manually
  reviewed plan. `failed_compensated_verified` has nothing left to reverse.
- **Execution.** The reverse plan is an ordinary `ordered_stop_on_failure`
  plan (ADR-018 unchanged): one F3 child per step, per-child admission,
  exact restored-state verification, no automatic compensation.

Pre-approved compensation (3.2) covers only failure during the forward
sequence. It never runs after a successful plan.

### 3.5 Partial failure summary

| Situation | Writes left in place | Result |
| --- | --- | --- |
| Stale or occupied before first write | None | Refused, approval not consumed |
| Rename refused by Core (`invalid_info`) | None | `failed_no_writes` |
| Rename `owned_verified`, rewrite `k` determinately fails | Compensations reverse owned rewrites `< k` and then the rename | `failed_compensated_verified` |
| Rename `already_present_verified`, rewrite `k` determinately fails, no earlier owned rewrite | None by this plan; the rename stays as found | `failed_no_writes`; **no inverse rename** |
| Rename `already_present_verified`, owned rewrites `< k`, rewrite `k` determinately fails | Compensations reverse only the owned rewrites `< k`; the rename stays as found | `failed_compensated_verified`, rename listed as `excluded_from_compensation` |
| Rewrite `k` indeterminate | Rename and rewrites `< k` stay until reconciled | `held_indeterminate` (manual review) |
| Compensation drift or failure | Whatever is listed per step | `failed_compensation_incomplete` (manual review) |
| Core restarts or identity changes mid-sequence | Admission refuses the next child before dispatch; compensation is also refused if Core identity no longer matches | `failed_compensation_incomplete` with exact per-step state |

### 3.6 Core admission per step

Each forward child and each compensation child is admitted individually at its
own dispatch through the existing predispatch checks: `core_identity`,
`capability_contract`, `target_fingerprint`, `session_fingerprint`,
`generation`. Rename and rename compensation use
`core.entity_registry_rename_operation`. Automation and script steps use
`core.governed_configuration_operation`. Scene steps use
`core.scene_configuration_operation`. The dependency-bound mode adds no
profile of its own. This follows HAMCP-222 ("no Core signing by itself").

## 4. Public schema and registration impact

| Surface | Change | Notes |
| --- | --- | --- |
| Static tools | **+1 against the design base** (81 → 82 at `7246f2d2`): new `create_entity_rename_plan` | Computed from `tools/registry.py:240` at base. This is not a frozen release total: the build derives the total at the integrated implementation revision. The delegated ha-mcp tool count is unaffected |
| `create_entity_metadata_plan` | Unchanged | `new_entity_id` stays refused |
| `create_configuration_plan` input | Unchanged | `execution_mode` is not accepted (refused if supplied) |
| `apply_change_plan`, `get_change_plan`, `get_execution_task`, `list_execution_tasks` output | Additive: `execution_mode`, per-step `compensation` projection, new `terminal_outcome` codes | Security-sensitive compatibility surface. Existing plans project `ordered_stop_on_failure` and no compensation |
| `rollback_change` | Accepts verified rename plans | New refusal reasons listed in section 6 |
| Server instructions (`mcp_server.py:15-16`), tool descriptions (`tools/governance.py:1060/1201/1312`), approval page (`approval_web.py:1974`) | Wording: the non-atomic, no-automatic-rollback rule stays for every plan **except** dependency-bound rename plans, which state their pre-approved compensation | These are protected runtime paths. They change only in the build PRs that name them |
| F3 identities | +1 `rename_entity_registry_entry` | The routes table counts F3 identities, so that pin changes |
| Persisted records | `ConfigurationOperation.compensation`, plan `execution_mode`, compensation child records, `compensation_decided` record | Storage format change. Section 8 gives the downgrade requirement |
| Provider registry | +1 contract `direct-ha-exact-entity-registry-rename-v1`, fallback none | No ha-mcp change |

## 5. Signing impact

Exactly one new additive profile. Naming follows `ha_core_readmission/profiles.py`
`_contract` (L109-L137): the dotted `capability_id` is
`core.entity_registry_rename_operation`, and the derived `profile_id` is
`capability_id.replace(".", "_") + "_v1"` =
`core_entity_registry_rename_operation_v1` (profile_version 1).

| Profile id | New or changed (from version X) | Core commands / contract added | Why | Fingerprint impact | Can it batch with other 2.6 profiles |
| --- | --- | --- | --- | --- | --- |
| `core_entity_registry_rename_operation_v1` (capability id `core.entity_registry_rename_operation`) | **New, additive** (runtime-only, in `CORE_RUNTIME_CAPABILITY_PROFILES`, not in `CORE_CAPABILITY_PROFILES`) | `config/entity_registry/update` with exactly `{entity_id, new_entity_id}`; `config/entity_registry/get` for old and new ids; new-id state absence read. Contract: dispatch `at_most_once_entity_registry_rename`, readback `authoritative_exact_entity_registry_entry_renamed`, recovery `read_only_no_redispatch`, predispatch `core_identity, capability_contract, target_fingerprint, session_fingerprint, generation` | Rename is outside the signed metadata closed-field contract, so it needs its own owner-signed admission (HAMCP-222). It also serves the rename compensation and rename rollback steps | New contract fingerprint, **unknown** until the build computes it from the compiled contract. Signed probe fingerprints unchanged (runtime-only). No other profile's fingerprint changes | **Yes, and only there:** signed for **Core 2026.11.0 only**, inside the one combined 2.6 signing (one `extend-capabilities` extension of the existing 2026.11.0 entry), once the compiled contract and its evidence are final. No 10.0, 10.1 or 10.3 entry. No separate or later 2.6 ceremony. If the contract is not final in time for that signing, it is not signed in 2.6 (J3). Unsigned, the profile stays dormant and the tool refuses |
| `core_entity_registry_metadata_operation_v1` (`core.entity_registry_metadata_operation`) | Unchanged | None | `new_entity_id` is deliberately **not** added to the metadata closed fields | None: stays `sha256:cc09630efbc0e1e6d0a587696406f6502c62fe730a767bc0a8cfc96a9988f767` | n/a |
| `core_governed_configuration_operation_v1` (`core.governed_configuration_operation`) | Unchanged | None | Automation and script rewrite, compensation and rollback steps use the existing adapter and its existing 2026.11.0 admission | None | n/a |
| `core_scene_configuration_operation_v1` (`core.scene_configuration_operation`) | Unchanged; existing signed coverage stays as it is | None | Scene rewrite, compensation and rollback steps use the existing adapter and its existing 2026.11.0 admission. No historic-version expansion is proposed | None | n/a |

The dependency-bound execution mode has no profile of its own. Any future
rewrite provider contract (for example a dashboard, group or template writer)
would need its own reviewed scope and admission and cannot be folded into the
rename profile.

Required future signing-scope tests (not run in this design):

- exact admission of `core_entity_registry_rename_operation_v1` on Core
  2026.11.0 from an owner-signed reference;
- zero dispatch (no recorded provider call) and pre-plan refusal for the
  unsigned rename profile on Core 2026.10.0, 2026.10.1 and 2026.10.3;
- the metadata profile fingerprint stays
  `sha256:cc09630efbc0e1e6d0a587696406f6502c62fe730a767bc0a8cfc96a9988f767`;
- `new_entity_id` through `create_entity_metadata_plan` still refuses with
  `entity_metadata_field_not_supported` (N1).

No signing happens in this PR or in any agent PR. Signing is Josh's owner
action, outside the repository workflow.

## 6. Negative reachability and refusal

Each row is a required test in the build slice named in section 9. Following
`tests/AGENTS.md`, each test proves zero dispatch (no recorded provider call)
as well as the refusal code.

| # | Attempt | Required result | Test |
| --- | --- | --- | --- |
| N1 | `create_entity_metadata_plan` with `new_entity_id` | `entity_metadata_field_not_supported`, unchanged | Existing 069A test stays green |
| N2 | `create_configuration_plan` with resource `entity_registry_rename` or with `execution_mode` | Refused (`unsupported_resource_type` / `execution_mode_not_caller_selectable`); no plan persisted | New |
| N3 | Rename across domains, invalid id, same id, or glob/pattern | Refused at planning; no plan | New |
| N4 | `new_entity_id` registered, present in states, or occupied between plan and apply | Planning refusal / apply `entity_rename_destination_occupied` with zero writes and approval not consumed | New |
| N5 | `<old>` gone or registry `id` changed between plan and apply | `entity_rename_source_changed`, zero writes | New |
| N6 | Dispatch message containing any key besides `entity_id`, `new_entity_id` | Impossible by construction; adapter unit test asserts the exact payload | New |
| N7 | Rename profile unsigned or dormant for the running Core | Tool refuses before plan creation; F3 identity maps nowhere else; no fallback to the metadata profile or to ha-mcp | New |
| N8 | Unknown F3 identity or forged plan with the rename identity under another profile | `core.unsupported_f3_operation` / admission refusal | New |
| N9 | Later step indeterminate | No compensation dispatch; `held_indeterminate` | New |
| N10 | Compensation preflight drift (old id taken, entry changed, config changed) | `compensation_drift`, no further dispatch, manual review | New |
| N11 | Lock transfer failure at phase change | `compensation_locks_lost`, zero compensation dispatch | New |
| N12 | Retry or re-apply after `failed_compensated_verified` | Terminal result returned; no redispatch of any forward or compensation child | New |
| N13 | Second apply while running (duplicate) | `duplicate_apply_prevented`; no second dispatch | Existing path, new fixture |
| N14 | Existing multi-operation plan fails at child 2 | Stops; child 1 **not** reversed; outcome unchanged from base | New regression guard on the old mode |
| N15 | Restart mid-compensation, then apply without the matching `expected_plan_hash` | `APPROVAL_HASH_MISMATCH`, no dispatch | New |
| N16 | `rollback_change` on a rename when old id is occupied or state drifted | `rollback_not_available` with reason; no plan | New |
| N17 | Rewrite holder with the old id only inside a template string | Holder not rewritten; listed `template_occurrence_not_rewritten`; plan proceeds or refuses per J1 | New |
| N18 | More than 7 dependent rewrites | `entity_rename_dependent_steps_exceed_limit` | New |
| N19 | Dashboard reference | Never becomes a dispatching child; disclosed `no_governed_writer` | New |
| N20 | Exact shipped older Engineering reader loads a plan or task written by the new writer (downgrade) | Refused or held, never executed as `ordered_stop_on_failure` | Writer-provenance downgrade test (section 8) |
| N21 | Rename child `already_present_verified`, then a rewrite fails determinately with no earlier owned rewrite | No inverse rename is sent (zero rename-compensation intents); `failed_no_writes`; rename listed `excluded_from_compensation` | New |
| N22 | Rename `owned_verified`, owned rewrites 1-2, rewrite 3 fails determinately | Compensation intents exist only for rewrites 2, 1 and the rename, in that order; each verified; `failed_compensated_verified` | New |
| N23 | Rename `already_present_verified`, owned rewrite 1, rewrite 2 fails determinately | Only rewrite 1 is compensated; no inverse rename | New |
| N24 | Forged or mismatched receipt: a child record claiming `succeeded_verified` without an intent, with a `provider_arguments_hash` / `prepared_operation_hash` not matching the declaration, from another plan, task, attempt or ordinal, or with `dispatch_count != 1` | Never `owned_verified`; no compensation or rollback intent; hold and manual review | New |
| N25 | Child recovered after restart with no intent whose readback shows the forward result (recovered no-op) | `already_present_verified`; never acquires compensation or rollback authority | New |
| N26 | Parent task `SUCCEEDED_VERIFIED` and full-bundle state equal to the forward result, but a child is a no-op | `rollback_change` excludes that child (`not_dispatched_by_this_plan`); with all children no-op it refuses `forward_not_dispatched` | New |
| N27 | Re-entry whose re-derived compensation set differs from the persisted `compensation_decided` record | `compensation_decision_mismatch`, zero dispatch, manual review | New |
| N28 | Crash before/after `compensation_decided` persistence and before/after each compensation intent; re-entry with an already-compensated prefix | No duplicate send; already-compensated children are read back only; recovery readback alone never dispatches | New |
| N29 | Rewrite holder with occurrences outside B1-B6 (template string, blueprint input, event data, comma-separated string, nested value), or a dashboard or group holder | No mutation of those positions or holders; each listed with its reason code; B5/B6 mismatch leaves the holder unwritten | New |
| N30 | Rename profile unsigned on Core 2026.10.0, 2026.10.1, 2026.10.3 | Tool refuses before plan creation; zero dispatch | New |
| N31 | Metadata profile fingerprint after the build | Unchanged (`sha256:cc09630efbc0e1e6d0a587696406f6502c62fe730a767bc0a8cfc96a9988f767`) | New pin |

## 7. Acceptance

**Completion rule.** HAMCP-295 is complete only when A1-A3 and A5 pass
together: rename **with** generated reference rewrites, pre-approved
compensation, and separately approved owner rollback, all verified. A
rename-only implementation, or one whose rewrites or verified undo are absent
or dormant, does not complete HAMCP-295 and is not a release deliverable.

**Useful authorized success (mandatory).** The point of the change is that
Josh can rename an entity without hand-editing:

- A1. An owner-approved rename of an actuator entity with known references
  applies once, verifies by exact readback, and reports the re-add or
  in-place move observation. This includes the 069A E1-style case where the
  policy class is elevated: an exact owner-authorized operation stays
  actionable despite a high consequence label
  (`hass_mcp_engineering_beta/AGENTS.md`).
- A2. The same rename plan carries generated rewrites of at least two
  automations and one script (and a scene where one references the entity),
  including a holder with more than one exact B-boundary occurrence. Every
  step is `owned_verified`, each rewritten config equals the generated
  proposal, and B6 re-extraction finds no remaining B-boundary occurrence of
  the old id.
- A3. After success, `rollback_change` produces a reverse plan with its own
  approval. Applying it restores the old configs (reverse order) and then the
  old id, all verified.

**Refusal and failure preservation.**

- A4. Every row in section 6 and every signing-scope test passes.
- A5. A forced verified failure of the third step leads to compensation of
  steps 2 and 1 in that order, both verified, with outcome
  `failed_compensated_verified`. The registry and configs equal preflight.
- A6. A forced indeterminate failure of the second step means no compensation
  and `held_indeterminate`. Readback reconciliation then resolves it through
  the 3.2.4 rules, and no dispatch occurs until a hash-matched re-entry.
- A7. Forced drift before compensation of step 1 gives
  `failed_compensation_incomplete` with exact per-step state, and nothing is
  overwritten.
- A8. No-op ownership: N21-N26 pass; an `already_present_verified` child never
  receives a compensation or rollback intent.
- A9. Unsupported positions: N29 passes; a holder with a template occurrence
  and a dashboard holder are listed as not rewritten with exact reason codes,
  and their configs are byte-identical after the plan.

**Verification and disclosure.**

- A10. The approval page shows the following for each holder:
  - every forward step and every compensation payload;
  - the references, each marked rewritten or not rewritten with its reason
    code, with completeness labels and the not-rewritten count;
  - the "will break" statement;
  - the recorder, helper and deleted-id consequences.

  The plan hash changes if any forward or compensation payload changes.

**Live acceptance.** Live acceptance on a real Home Assistant is a separate,
later, owner-authorized activity. It is not part of this design or of any
agent PR. It needs a disposable test entity, chosen by Josh.

## 8. Security section

- **New write reachability.** Exactly one new Home Assistant write:
  `config/entity_registry/update` with `{entity_id, new_entity_id}`. It goes
  through a direct-HA provider contract with `fallback: none`, behind an
  owner-signed runtime-only profile, a hash-bound external approval, and F3
  at-most-once dispatch. The compensation write uses the same command and
  profile, with a payload pre-approved in the same plan hash. Rewrite steps
  reuse the existing reviewed automation/script and scene adapters, only at
  the B1-B6 positions (section 3.3). No new provider, no
  ha-mcp delegation, no upstream dispatch, no direct-HA fallback, and no
  arbitrary forwarding is added.
- **New automatic behaviour.** Compensation dispatch without a second human
  action is new. It is bounded in four ways:
  1. exact pre-approved payloads;
  2. only after a determinate failure;
  3. only over `owned_verified` steps, proven from durable child records
     (section 3.2.2a); never over a no-op, recovered no-op, forged or
     unattributable record, and never inferred from parent success;
  4. full-bundle drift check and per-child admission before each send.

  This is the security architecture change the root AGENTS.md lists ("newly
  reachable write ... physical action"). It needs explicit scope (the
  recorded 2026-11-07 HAMCP-109 decision and the Josh direction in section
  1.3), negative tests (section 6), and focused review.
- **Physical consequence.** Renaming an actuator entity breaks every
  reference the plan does not rewrite (dashboards, groups, templates, and
  not-rewritten occurrences, section 3.3) until it is fixed by hand.
  This is disclosed, not used as a refusal reason (ADR-022). Recorder
  migration failure loses the history link and is disclosed, not verifiable.
- **Persistence and downgrade.** New persisted fields and records. Frozen
  rules for the build (contract freeze, not open):
  - *Discriminator.* The plan key `execution_mode` and the operation key
    `compensation` are persisted **only** for
    `dependency_bound_compensating_v1` plans. Absence means
    `ordered_stop_on_failure`. Existing planners never write either key, so
    their persisted bytes are unchanged.
  - *Hash rule.* Plan hash material includes `execution_mode` and each
    `compensation` only when present. No default value is inserted into the
    hash material of an existing plan, so every existing plan hash stays
    byte-identical.
  - *New writer to exact old reader.* With plan and task records written by
    the new writer, the exact shipped reader of the preceding release (2.5.x
    at writing; derive the exact version at build time) must refuse or hold
    them and never execute them as `ordered_stop_on_failure` (N20). The
    expected mechanism is that shipped dataclass readers reject unknown
    constructor fields; that is an expectation to prove, not a claim.
  - *Exact shipped writer to new reader.* Records written by the exact
    shipped code load in the new reader with identical plan hashes and
    unchanged `ordered_stop_on_failure` behaviour.
  - *Provenance.* Both directions use writer-provenance fixtures generated by
    the exact shipped code (root `AGENTS.md`, Persisted Compatibility
    Fixtures), with the source commit and generation process recorded. No
    hand-authored fixture substitutes. The unknown resource type
    `entity_registry_rename` alone is not sufficient evidence, because the
    rename is ordinal 0 and task recovery reads child records independently.
  - If old-reader refusal cannot be proven, a versioned plan contract is
    required before release. This remains an open build risk.
- **Bounded output and sanitization.** Reference lists use the HAMCP-270
  bounds (`MAX_REFERENCES = 8`). Rewrite diffs on the approval page are
  bounded and pass the existing secret screening. Plan text is screened
  before persistence.
- **Audit.** Every forward, compensation and decision record carries the plan
  hash, step id, child id and actor. `compensation_decided` is mirrored as an
  audit event for attribution. The audit log is never authority for phase,
  ownership or the compensation set; the crash-consistent F3 records are
  (section 3.2.8).
- **No live access.** This design used only repository source and public Core
  source at exact tags. No live Home Assistant, deployed endpoint, credential
  or secret was accessed.

## 9. Build plan

The recorded HAMCP-109 target is "build IN 3.0" (section 1.3). The delivery
schedule is Remaining Josh choice J3; the slice order below is the same under
every J3 option. HAMCP-295 and HAMCP-109 ship together in one release: no
release ships S1 without S2 and S3, because a rename-only or disclose-only
release is not a deliverable.

| Slice | Content | Depends on | Protected paths named |
| --- | --- | --- | --- |
| S0 | New ADR (next free number): scoped dependency-bound compensating execution, recording the 2026-11-07 HAMCP-109 decision and the Josh direction in section 1.3; amends the three ADR-018 statements in section 3.2.6 for this mode only; matching [`CHANGE_GOVERNANCE.md`](CHANGE_GOVERNANCE.md) text | J1, J2 answered (contract freeze) | None (docs) |
| S1 | Rename resource, planner, tool `create_entity_rename_plan` (+1 static tool against the base; derive the release total at the integrated revision), F3 identity, dormant runtime-only profile, provider contract, preflight, readback, V9 three-case rule, `rollback_change` support for the rename step | S0; 2.5.0 landed | `hass_mcp_engineering_beta/ha_mcp_engineering/` |
| S2 | Generic `dependency_bound_compensating_v1` in F3: persisted compensation candidates, section 3.2.2a ownership classes and accounting, lock-union transfer, crash-consistent decision records, compensation phase, outcomes, restart rules, frozen discriminator/hash rules and the two-direction downgrade proof | S1 | `hass_mcp_engineering_beta/ha_mcp_engineering/` |
| S3 | **Mandatory** dependent rewrite steps (section 3.3): B1-B6 rewriter, re-extraction check, fixed order, per-occurrence disclosure, owner rollback of rewrites | S2 | `hass_mcp_engineering_beta/ha_mcp_engineering/` |
| S4 | Full/Evidence validation and acceptance A1-A10 on the integrated head; compiled rename-contract fingerprint frozen | S3 | None |
| S5 | Owner signing: `core_entity_registry_rename_operation_v1` for Core **2026.11.0 only**, inside the one combined signing of the release that ships HAMCP-295 (the combined 2.6 signing if J3 places it in 2.6.0) | S4 compiled contract fingerprint; Josh action | `upstream-trust/` (owner only) |
| S6 | Version transition and release documents | Josh's release decision | `.release/`, `config.yaml` |

**Overlap with in-flight 2.5.0 work.** 2.5.0 wins shared files, and S1-S3 will
merge `main` after 2.5.0 lands (never rebase):

- PR #387 (HAMCP-288) changes `tests/test_f3_runtime_integration.py`,
  `tests/test_catalog_readiness.py`, `tests/test_beta25_external_approval.py`,
  `tests/test_f3_readiness.py` and `models/responses.py`. S1 and S2 will touch
  the same tests (tool count, F3 identities, outcome projection).
- PR #384 (HAMCP-309 Part 3) changes [`CORE_RELEASE_REGISTRY.md`](CORE_RELEASE_REGISTRY.md),
  which S5 documentation touches.
- No overlap with #386 (HAMCP-327) or #385 (HAMCP-319 design).
- HAMCP-290 and the other new 2.6 profiles share the one combined 2.6
  signing; there is no separate envelope for this profile.

**Rollback of the feature.**

- Before signing, the profile is dormant and the tool refuses. That is the
  default state, with no rollback needed.
- After release, rolling back to the previous version is safe only once the
  section 8 downgrade proof shows that persisted dependency-bound plans are
  refused or held by the older build.
- Removing the tool alone leaves the metadata, configuration and rollback
  paths unchanged.

## 10. Remaining Josh choices — pending Josh

Settled and not asked again: references are rewritten with verified undo
(section 1.3); additive profile, Core 2026.11.0 only, one combined 2.6 signing;
rewrite order (section 3.3, author proposal: rename first); the existing
eight-operation bound is kept (rename plus up to 7 rewrites).

**J1. Rewrite coverage and acceptable incomplete coverage.** The rewrite
boundary is B1-B6 (automation, script, scene; structural `entity_id` leaves
and scene keys). Template occurrences, blueprint inputs, event data,
dashboards and groups are not rewritten and are disclosed.

- (a) Proceed with per-occurrence disclosure and a not-rewritten count on the
  approval page (build default until answered).
- (b) Proceed, but require an explicit plan-level acknowledgement whenever
  any occurrence is not rewritten.
- (c) Refuse the whole plan when any known occurrence is not rewritten.
- Sub-choice: wait for HAMCP-264 (template read coverage) before HAMCP-295
  ships, or ship with truthful `unavailable` template disclosure.

**J2. Rename policy class and acknowledgement.** `standard_admin` (one
approval action); `elevated_admin` for actuator domains (069A E1 pattern);
or `elevated_admin` for every rename plan. Classification is decision support
only and never makes an exact owner-authorized rename non-actionable.

**J3. Delivery schedule versus HAMCP-109's recorded 3.0 target.** Concrete
order under every option: S0 → S1 → S2 → S3 → S4 → S5 → S6, shipped together.

- (a) Keep the recorded 3.0 target for both HAMCP-109 and HAMCP-295; the rename
  profile is then not in the 2.6 signing.
- (b) Move both into 2.6.0; the rename profile joins the combined 2.6
  signing, which waits for S4.
- (c) Target 2.6.0 with a hard cutoff: if S4 has not frozen the compiled
  contract when the combined 2.6 signing is assembled, the whole of
  HAMCP-295/109 moves to 3.0, with no rename-only release and no second 2.6
  signing.

### Superseded (historical)

The revision at head `e025778d` listed Q1-Q5. Q1 (disclose-only option A
versus rewrites B1/B2/C) and its sub-question Q1(c) (order) are superseded by
the Josh direction in section 1.3 and the fixed order in section 3.3; option A
is not a deliverable, and option B2 (dashboard delegation) is not in scope.
Q1(b) and Q2 are folded into J1, Q3 is J2, Q4 is J3. Q5 (raise the plan size)
is withdrawn: the eight-operation bound is kept.

## 11. Codex design review 2026-11-08 — disposition

Review of head `e025778d13bd9f7bd54804832a3dc87e1d21df24` (verdict: Ready
after corrections).

| Finding | Severity | Correction |
| --- | --- | --- |
| 1. No-dispatch predecessor and compensation contract | Medium | Section 3.1 (V9 three-case rule); new 3.2.2a (ownership classes, `already_present_verified` satisfies dependants but never compensates, separate `owned_operation_ids` / `noop_operation_ids` accounting, durable-record proof mirroring HAMCP-135 `configuration_rollback_eligibility.py`); 3.2.2, 3.2.3, 3.2.4, 3.2.5; 3.2.9 outcomes; 3.4 owner rollback selects owned steps only; 3.5 rows; tests N21-N28; acceptance A8 |
| 2. Materialize the settled rewrite requirement | Medium | Section 1.3 records the Josh direction; 3.1 removes the caller rewrite field; 3.3 rewritten as mandatory with the exact B1-B6 boundary, never-rewritten reason codes, fixed order (rename first), no dashboard/group/template writer and no textual Jinja replacement; 3.4 verified undo; section 7 completion rule (rename-only does not complete HAMCP-295), A2, A9; section 9 S3 mandatory; section 10 option A superseded; test N29 |
| 3. Signing scope contradicts 11.0-only | Medium | Header, section 3.1 Core commands, Signing impact (one additive `core_entity_registry_rename_operation_v1`, capability id `core.entity_registry_rename_operation`, Core 2026.11.0 only, one combined 2.6 signing; 10.x targets and historic scene expansion removed); signing-scope tests; N30, N31; section 9 S5 |
| Non-finding gates | n/a | 3.2.8 crash-consistent authority records (audit is not authority), reconciliation never dispatches, crash-point tests; 3.2.6 ADR-018 amendment includes forward-approval nonreuse for this mode only; section 8 frozen discriminator/hash rules and two-direction writer-provenance downgrade proof |
| Remaining Josh decisions | n/a | Section 10 J1-J3; order proposed; eight-operation bound kept |
