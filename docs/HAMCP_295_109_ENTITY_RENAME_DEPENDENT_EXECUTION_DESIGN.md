# HAMCP-295 / HAMCP-109 design: governed entity ID rename and dependency-bound execution

Status: design for review (2.6.0 feature milestone, docs only). Nothing in this
document is implemented, signed, released, or deployed. It changes no runtime
behaviour, schema, tool, workflow, version, or release declaration.

- Register: HAMCP-295 (governed entity ID rename), HAMCP-109
  (dependency-bound multi-operation execution).
- Base: `origin/release/2.6.0` =
  `7246f2d21693f039504542574569f03b71476489` (2.4.2-beta.7 materialized).
  Every `file:line` below is at this base unless it is marked as upstream.
- Pinned Home Assistant Core from
  [`ci/support-window.yaml`](../ci/support-window.yaml): current `2026.10.1`,
  candidate `2026.10.3`, rollback `2026.10.0`. Pinned ha-mcp: current `8.5.0`,
  rollback `8.4.3`.
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
| **Josh decision (2026-11-07, design-complete scoping):** HAMCP-109 is "IN 3.0, designed and built around entity rename (HAMCP-295) as its concrete driver: dependent steps verified in order with rollback of earlier steps on later failure. Next: design with 295 in the 2.6.0 feature milestone." | HAMCP-109 register Next Action, quoted in the task prompt | Requires an execution mode in which a later failure causes the earlier verified steps to be reversed. This supersedes, for that mode only, the shipped "never roll back automatically" contract (section 2.1). |
| **Josh decision (2026-11-03):** HAMCP-295 is feature lane position 2 after HAMCP-253, 267 and 264. | HAMCP-295 register Next Action | Build order. HAMCP-264 (template reads) is ranked ahead of 295; see open question Q2. |
| Design a new, distinct dependency-bound mode; do not silently change existing multi-operation behaviour; stop and report if the decision cannot be met within existing safety constraints. | Task prompt | Section 3.2. Result: the decision **can** be met without weakening an existing safety constraint (section 3.2.6). It still needs a recorded ADR amendment, because the shipped ADR-018 text forbids exactly this behaviour. |
| Whether a rename also rewrites referencing automations, scripts and dashboards as dependent steps, or only discloses them, is not settled. | Task prompt | Open question Q1. Section 3 is written so either answer fits. |
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

### 2.4 Exact Home Assistant behaviour of a rename (upstream, Core 2026.10.x)

Each claim below is grounded in exact-tag source. URLs are given at
`2026.10.1`; the files are byte-identical at `2026.10.0` and `2026.10.3`.

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
| `reference_rewrites` | Present only if Josh chooses option B or C in Q1. A closed enum per holder type. No free-form payloads: rewrite payloads are generated, never supplied by the caller (section 3.3). |

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
  deleted-id caveat, and asynchronous re-add.

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

**Core commands** (all `@require_admin`, Core 2026.10.x):

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
  - A rewrite step (Q1 option B or C) also takes the exact lock the existing
    adapter for that holder already uses.
- **Narrow race.** Core offers no compare-and-swap on the registry. A write
  that lands between the final re-read and dispatch is reported as
  indeterminate, the same as today.

**At-most-once dispatch.** The rename child is one F3 child: durable intent
before send, one send, never redispatched. Recovery is readback only
(`recovery: read_only_no_redispatch`). V9 already-present no-dispatch applies:
if a readback shows that `<new>` is the same registry `id` and `<old>` is not
found, the outcome is `already_present`, with no dispatch.

**Authoritative readback.** The rename verifies only when all of these hold:

1. `get(<new>)` returns the same `id`, `unique_id`, `platform` and
   `config_entry_id`.
2. Every other field equals preflight, except `entity_id` and `modified_at`.
3. `get(<old>)` returns `ERR_NOT_FOUND`.

The re-add in the state machine is asynchronous (section 2.4) and is reported
as a bounded **observation**, not a verification gate. A disabled entity never
re-adds. Recorder migration and helper config-entry follow-up cannot be
verified through any admitted read. They are disclosed as unverified Core
consequences and are never reported as verified.

**Disclosure ("states what will break").** The approval page and the plan
projection show the following:

- Every reference found through the `rename_entity` impact evidence:
  automations, scripts (loaded only, labelled partial), scenes, groups
  (loaded membership), dashboards (reviewed list, bounded), and templates
  (`unavailable` with the HAMCP-264 reason until that lands). Each reference
  carries its completeness label.
- A plain statement that each listed reference will point to a missing
  entity after the rename, unless it is rewritten by a dependent step that is
  part of this plan (Q1).
- Completeness: under `no_known_impacts_with_incomplete_coverage` the page
  states that unlisted references may exist.
- Core consequences:
  - recorder history migration, which can fail if the new id already has
    history;
  - helper config-entry follow and reload;
  - the deleted-entity-id caveat (an id held by a deleted registry entry
    counts as free);
  - asynchronous re-add.

Disclosure never downgrades risk and is not fingerprinted beyond the existing
HAMCP-270 rules. Consequence labels stay decision support (HAMCP-109 Notes).

**Policy class.** The F2 policy class of a rename step is undecided. It is
open question Q3 and is not set here. The mechanics support either
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
  planner produces it in 2.6/3.0. `create_configuration_plan` never accepts
  it as caller input. A caller-supplied `execution_mode` is refused with
  `execution_mode_not_caller_selectable`.

In the new mode, every operation carries one more persisted field,
`compensation`. It holds the exact inverse step, computed at **planning** time
from exact persisted prior state:

| Forward step | Compensation step | Expected pre-compensation state |
| --- | --- | --- |
| Rename `<old>` to `<new>` | Rename `<new>` to `<old>` (same command, same profile) | Registry entry at `<new>` equals the verified forward result; `<old>` free in registry and state machine |
| Automation/script update (Q1 option B/C) | Restore the exact prior config (HAMCP-135 inverse; existing governed configuration adapter) | Current config equals the forward step's verified post-apply fingerprint |
| Scene update (Q1 option B) | Restore the exact prior scene config (HAMCP-071 adapter) | Same rule |

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
is `succeeded_verified`.

#### 3.2.3 When compensation runs

Compensation runs only when all three conditions hold:

1. A forward step `k` ended in a **determinate** non-success:
   - refused before dispatch: stale target, admission refusal, lock or lease
     loss, or provider refusal with no send; or
   - dispatched, with authoritative readback proving it was **not** applied
     (verified failure).
2. Every step before `k` that was attempted is determinate:
   `succeeded_verified` or determinately not applied.
3. At least one earlier step is `succeeded_verified`.

When those conditions hold, the runtime records a durable decision
`compensation_decided {failed_step, reason}`. It then runs the compensation
steps of the verified steps in **reverse** order, one F3 child each. Each
compensation child gets its own intent, its own Core admission, at most one
dispatch, and exact readback.

#### 3.2.4 Indeterminate outcomes never compensate

If any attempted step is indeterminate (dispatch occurred and readback cannot
prove applied or not applied), the plan holds. No compensation runs, and the
current selective hold applies (ADR-017). The only path forward is readback-only
reconciliation:

- If reconciliation proves the step applied, it joins the verified set.
- If reconciliation proves it did not apply, it becomes determinate.

Either way, rule 3.2.3 is then evaluated again. If the outcome stays unknown,
the task ends in manual review with no compensation dispatch. This keeps the
F3 invariant: never act on an unknown outcome.

#### 3.2.5 Drift before compensation stops the sequence

Before each compensation child, the runtime runs the HAMCP-135 full-bundle
state check (as `_rollback_bundle_check`, `runtime.py:5100`) across every
object the plan touched. The compensated object must equal its verified
forward result, and every not-yet-compensated object must equal its expected
state. If any check fails, the runtime stops with `compensation_drift`,
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
slice S0 records Josh's 2026-11-07 decision as a scoped amendment (new ADR,
next free number at build time). The amendment covers only
`dependency_bound_compensating_v1` and keeps ADR-018 unchanged for every
other plan. I do not treat this as a conflict to stop on: the decision
explicitly asks for the behaviour, and every execution-integrity rule still
holds.

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

#### 3.2.9 Terminal outcomes

These are new `terminal_outcome` codes on the existing task states. No new
`ExecutionTaskState` value is added, following the 069A
`ENTITY_METADATA_NOT_DISPATCHED_OUTCOME` precedent (`runtime.py:~4593`):

| Outcome | Task state | Meaning |
| --- | --- | --- |
| `succeeded_verified` | `SUCCEEDED_VERIFIED` | Every forward step verified |
| `already_present` | `SUCCEEDED_VERIFIED` | Rename already in effect (V9), no dispatch |
| `failed_no_writes` | failed, no dispatch | The first attempted step failed determinately; nothing was written |
| `failed_compensated_verified` | `FAILED_POST_DISPATCH` | A later step failed; every earlier verified step was reversed and verified, so touched objects equal their preflight state |
| `failed_compensation_incomplete` | `MANUAL_REVIEW_REQUIRED` | Compensation drift, a compensation failure, admission refusal of a compensation step, or lock loss. Each step's state is listed |
| `held_indeterminate` | `MANUAL_REVIEW_REQUIRED` | A step's outcome is unknown; no compensation ran |

`apply_change_plan`, `get_execution_task` and `get_change_plan` report each
forward step and each compensation step with:

- `provider_dispatch_occurred`;
- the verified outcome;
- the reason for any step that was not attempted.

`status: applied` appears only for `succeeded_verified` and `already_present`.

### 3.3 Dependent reference rewrites (only under Q1 option B or C)

These rules apply only if Josh chooses a rewrite option. Under option A they
are not built.

- **Generated, not supplied.** For each reference holder that has exact
  evidence and a reviewed governed write adapter, the planner:
  1. reads the holder's current config through the existing governed read;
  2. walks it with a structural rewriter limited to reviewed entity-reference
     positions (`entity_id` fields, `target.entity_id`, trigger and condition
     `entity_id`, scene `entities` keys);
  3. proposes the exact rewritten config as an ordinary update operation with
     `depends_on` set.

  The caller supplies no config.
- **Positions the rewriter cannot attribute** (the old id inside a template
  string, or any position not in the reviewed set) are never rewritten
  textually. The holder is listed as "not rewritten: will break". Whether the
  plan should be refused instead is sub-question Q1(b).
- **Eligibility per holder type at base:**

| Holder | Write adapter | Signed for | Eligible as a dependent step |
| --- | --- | --- | --- |
| Automation | Existing governed configuration adapter | `core_governed_configuration_operation_v1` on 2026.10.0/10.1/10.3 | Yes |
| Script | Same | Same | Yes |
| Scene | HAMCP-071 `scene_configuration` | `core_scene_configuration_operation_v1` on **2026.10.3 only** (registry seq 15) | Only on Core versions where signed; otherwise listed as not rewritten |
| Dashboard | Delegated ha-mcp dashboard setter under `ChangeOperation.UPDATE_DASHBOARD`, not a configuration-plan child | n/a | **Not without new scope.** Making it a child would put a delegated provider write inside a configuration plan. That is a new provider-dispatch path and a security architecture change (root AGENTS.md). Listed in Q1 as option B2 |
| Group | None | n/a | No: disclose only |
| Template | No read path (HAMCP-264) and no write adapter | n/a | No: disclose as `unavailable` |

- **Order (Q1(c)).** Either order fits the mechanics: the rename step can come
  first with rewrites depending on it, or rewrites can come first with the
  rename depending on all of them. Both orders leave a window in which
  references are broken. Rename first: references point to a missing old id
  until each rewrite verifies. Rewrite first: references point to a
  not-yet-existing new id until the rename verifies. The window is disclosed.
- **Limit.** The rename plus its rewrites must fit within
  `MAX_CONFIGURATION_OPERATIONS = 8` (`governance/service.py:445`). That
  allows the rename and up to 7 rewrites. Above that, the planner refuses
  with `entity_rename_dependent_steps_exceed_limit` and the count. It never
  splits a rename into several plans silently, because splitting would lose
  the compensation boundary. Whether to raise the limit is Q5.

### 3.4 Rollback after success

This covers the HAMCP-295 requirement that a rename "can be rolled back".
After `succeeded_verified`, owner rollback stays ADR-018 `rollback_change`:
the planner builds a reverse plan from persisted prior state, with its **own**
approval. It is refused unless the current state equals the forward result.

The design adds `_create_entity_rename_rollback`, alongside
`_create_entity_metadata_rollback` (`runtime.py:5976`). It reverses the rename,
plus the rewrites in reverse order under option B or C, using the HAMCP-135
exact-inverse model. If the old id is now occupied, it refuses with
`rollback_not_available` and reason `entity_rename_rollback_destination_occupied`.
Pre-approved compensation (3.2) covers only failure during the forward
sequence. It never runs after a successful plan.

### 3.5 Partial failure summary

| Situation | Writes left in place | Result |
| --- | --- | --- |
| Stale or occupied before first write | None | Refused, approval not consumed |
| Rename refused by Core (`invalid_info`) | None | `failed_no_writes` |
| Rename verified, rewrite `k` determinately fails | Compensations reverse rewrites `< k` and the rename | `failed_compensated_verified` |
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
| Static tools | **81 → 82**: new `create_entity_rename_plan` | Computed from `tools/registry.py:240` at base. The delegated ha-mcp tool count is unaffected, so the advertised total rises by 1 on any admitted ha-mcp set |
| `create_entity_metadata_plan` | Unchanged | `new_entity_id` stays refused |
| `create_configuration_plan` input | Unchanged | `execution_mode` is not accepted (refused if supplied) |
| `apply_change_plan`, `get_change_plan`, `get_execution_task`, `list_execution_tasks` output | Additive: `execution_mode`, per-step `compensation` projection, new `terminal_outcome` codes | Security-sensitive compatibility surface. Existing plans project `ordered_stop_on_failure` and no compensation |
| `rollback_change` | Accepts verified rename plans | New refusal reasons listed in section 6 |
| Server instructions (`mcp_server.py:15-16`), tool descriptions (`tools/governance.py:1060/1201/1312`), approval page (`approval_web.py:1974`) | Wording: the non-atomic, no-automatic-rollback rule stays for every plan **except** dependency-bound rename plans, which state their pre-approved compensation | These are protected runtime paths. They change only in the build PRs that name them |
| F3 identities | +1 `rename_entity_registry_entry` | The routes table counts F3 identities, so that pin changes |
| Persisted records | `ConfigurationOperation.compensation`, plan `execution_mode`, compensation child records, `compensation_decided` record | Storage format change. Section 8 gives the downgrade requirement |
| Provider registry | +1 contract `direct-ha-exact-entity-registry-rename-v1`, fallback none | No ha-mcp change |

## Signing impact

| Profile id | New or changed (from version X) | Core commands / contract added | Why | Fingerprint impact | Can it batch with other 2.6 profiles |
| --- | --- | --- | --- | --- | --- |
| `core.entity_registry_rename_operation` (profile_id `core_entity_registry_rename_operation_v1`) | **New** (runtime-only, in `CORE_RUNTIME_CAPABILITY_PROFILES`, not in `CORE_CAPABILITY_PROFILES`) | `config/entity_registry/update` with exactly `{entity_id, new_entity_id}`; `config/entity_registry/get` for old and new ids; new-id state absence read. Contract: dispatch `at_most_once_entity_registry_rename`, readback `authoritative_exact_entity_registry_entry_renamed`, recovery `read_only_no_redispatch`, predispatch `core_identity, capability_contract, target_fingerprint, session_fingerprint, generation` | Rename is outside the signed metadata closed-field contract, so it needs its own owner-signed admission (HAMCP-222). It also serves the compensation step | New contract fingerprint, **unknown** until the build computes it from the compiled contract. Signed probe fingerprints unchanged (runtime-only). No other profile's fingerprint changes | **Yes.** Needs owner-signed registry entries for every supported Core version (2026.10.0, 2026.10.1, 2026.10.3, and 2026.11.0 if registry seq 16 is signed first). These fit in one envelope with the other 2.6 profiles. The profile stays dormant (the tool refuses) until signed |
| `core.entity_registry_metadata_operation` (`core_entity_registry_metadata_operation_v1`) | Unchanged | None | `new_entity_id` is deliberately **not** added to the metadata closed fields | None: stays `sha256:cc09630efbc0e1e6d0a587696406f6502c62fe730a767bc0a8cfc96a9988f767` | n/a |
| `core.governed_configuration_operation` (`core_governed_configuration_operation_v1`) | Unchanged (reused only under Q1 option B or C) | None | Automation and script rewrite and compensation steps use the existing adapters | None | n/a |
| `core.scene_configuration_operation` (`core_scene_configuration_operation_v1`) | Unchanged (reused only under Q1 option B); signed today for 2026.10.3 only | None | Scene rewrite steps are admitted only where this profile is signed | None. Extending to other Core versions would add signed entries, not a contract change | Yes, if Josh wants scene rewrites on more Core versions |

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
| N17 | Rewrite target with the old id only inside a template string (option B/C) | Holder not rewritten; listed as will break, or plan refused per Q1(b) | New |
| N18 | More than 7 dependent rewrites | `entity_rename_dependent_steps_exceed_limit` | New |
| N19 | Dashboard reference under option B1 or C | Never becomes a dispatching child; disclosed only | New |
| N20 | Older Engineering build loads a persisted dependency-bound plan or task (downgrade) | Refused or held, never executed as `ordered_stop_on_failure` | Downgrade test from exact shipped code (section 8) |

## 7. Acceptance

**Useful authorized success.** The point of the change is that Josh can rename
an entity without hand-editing:

- A1. An owner-approved rename of an actuator entity with known references
  applies once, verifies by exact readback, and reports the async re-add
  observation. This includes the 069A E1-style case where the policy class is
  elevated: an exact owner-authorized operation stays actionable despite a
  high consequence label (`hass_mcp_engineering_beta/AGENTS.md`).
- A2. Under option B or C: rename plus rewrites of two automations and one
  script all verify, and each rewritten config equals the generated proposal.
- A3. After success, `rollback_change` produces a reverse plan with its own
  approval. Applying it restores the old id (and the old configs under B or C),
  verified.

**Refusal and failure preservation.**

- A4. Every row in section 6 passes.
- A5. A forced verified failure of the third step leads to compensation of
  steps 2 and 1 in that order, both verified, with outcome
  `failed_compensated_verified`. The registry and configs equal preflight.
- A6. A forced indeterminate failure of the second step means no compensation
  and `held_indeterminate`. Readback reconciliation then resolves it through
  the 3.2.4 rules.
- A7. Forced drift before compensation of step 1 gives
  `failed_compensation_incomplete` with exact per-step state, and nothing is
  overwritten.

**Verification and disclosure.**

- A8. The approval page shows the following for each holder:
  - every forward step and every compensation payload;
  - the references, with completeness labels;
  - the "will break" statement;
  - the recorder, helper and deleted-id consequences.

  The plan hash changes if any compensation payload changes.

**Live acceptance.** Live acceptance on a real Home Assistant is a separate,
later, owner-authorized activity. It is not part of this design or of any
agent PR. It needs a disposable test entity, chosen by Josh.

## 8. Security section

- **New write reachability.** Exactly one new Home Assistant write:
  `config/entity_registry/update` with `{entity_id, new_entity_id}`. It goes
  through a direct-HA provider contract with `fallback: none`, behind an
  owner-signed runtime-only profile, a hash-bound external approval, and F3
  at-most-once dispatch. The compensation write uses the same command and
  profile, with a payload pre-approved in the same plan hash. Under option B
  or C, rewrite steps reuse existing reviewed adapters. No new provider, no
  ha-mcp delegation, no upstream dispatch, no direct-HA fallback, and no
  arbitrary forwarding is added.
- **New automatic behaviour.** Compensation dispatch without a second human
  action is new. It is bounded in four ways:
  1. exact pre-approved payloads;
  2. only after a determinate failure;
  3. only over verified steps;
  4. full-bundle drift check and per-child admission before each send.

  This is the security architecture change the root AGENTS.md lists ("newly
  reachable write ... physical action"). It needs explicit scope (the Josh
  2026-11-07 decision), negative tests (section 6), and focused review.
- **Physical consequence.** Renaming an actuator entity breaks automations,
  scripts and dashboards that control it until their references are fixed.
  This is disclosed, not used as a refusal reason (ADR-022). Recorder
  migration failure loses the history link and is disclosed, not verifiable.
- **Persistence and downgrade.** New persisted fields and records. The build
  must prove, with the exact shipped 2.5.x writer and reader (per the
  persisted-fixture rules in root AGENTS.md), that an older build:
  - refuses or holds a dependency-bound plan; and
  - never runs it as an ordinary stop-on-failure plan without its
    compensation.

  The unknown resource type `entity_registry_rename` is expected to make an
  older build refuse. That is an expectation to verify, not a claim made here.
  If the proof fails, the plan-level mode must be made unreadable to older
  builds (for example a versioned plan contract) before release. This is an
  open build risk.
- **Bounded output and sanitization.** Reference lists use the HAMCP-270
  bounds (`MAX_REFERENCES = 8`). Rewrite diffs on the approval page are
  bounded and pass the existing secret screening. Plan text is screened
  before persistence.
- **Audit.** Every forward, compensation and decision record carries the plan
  hash, step id, child id and actor. `compensation_decided` is an audit event.
- **No live access.** This design used only repository source and public Core
  source at exact tags. No live Home Assistant, deployed endpoint, credential
  or secret was accessed.

## 9. Build plan

The build target release depends on Q4. HAMCP-109 is recorded as "IN 3.0".

| Slice | Content | Depends on | Protected paths named |
| --- | --- | --- | --- |
| S0 | New ADR (next free number): scoped dependency-bound compensating execution, recording Josh's 2026-11-07 decision as superseding ADR-018 for this mode only; matching [`CHANGE_GOVERNANCE.md`](CHANGE_GOVERNANCE.md) text | Answers to Q1, Q4 | None (docs) |
| S1 | Rename resource, planner, tool `create_entity_rename_plan` (81 → 82), F3 identity, dormant runtime-only profile, provider contract, preflight, readback, V9, `rollback_change` support; single-step plans only (`ordered_stop_on_failure` is enough for one step) | S0; 2.5.0 landed | `hass_mcp_engineering_beta/ha_mcp_engineering/` |
| S2 | Generic `dependency_bound_compensating_v1` in F3: persisted compensation, lock-union transfer, compensation phase, outcomes, restart rules, downgrade proof; tests use the rename step as the forward/compensation pair | S1 | `hass_mcp_engineering_beta/ha_mcp_engineering/` |
| S3 | Dependent rewrite steps per Q1 (skipped under option A) | S2; Q1(b)/(c); for scenes, signing coverage | `hass_mcp_engineering_beta/ha_mcp_engineering/` |
| S4 | Owner signing: registry envelope adding `core_entity_registry_rename_operation_v1` for each supported Core version, batched with other 2.6 profiles | S1 compiled contract fingerprint; Josh action | `upstream-trust/` (owner only) |
| S5 | Version transition and release documents | Josh's release decision | `.release/`, `config.yaml` |

**Overlap with in-flight 2.5.0 work.** 2.5.0 wins shared files, and S1-S3 will
merge `main` after 2.5.0 lands (never rebase):

- PR #387 (HAMCP-288) changes `tests/test_f3_runtime_integration.py`,
  `tests/test_catalog_readiness.py`, `tests/test_beta25_external_approval.py`,
  `tests/test_f3_readiness.py` and `models/responses.py`. S1 and S2 will touch
  the same tests (tool count, F3 identities, outcome projection).
- PR #384 (HAMCP-309 Part 3) changes [`CORE_RELEASE_REGISTRY.md`](CORE_RELEASE_REGISTRY.md),
  which S4 documentation touches.
- No overlap with #386 (HAMCP-327) or #385 (HAMCP-319 design).
- HAMCP-290 adds a new Core contract and therefore its own signed entries. S4
  can share its envelope only if the timing lines up.

**Rollback of the feature.**

- Before signing, the profile is dormant and the tool refuses. That is the
  default state, with no rollback needed.
- After release, rolling back to the previous version is safe only once the
  section 8 downgrade proof shows that persisted dependency-bound plans are
  refused or held by the older build.
- Removing the tool alone leaves the metadata, configuration and rollback
  paths unchanged.

## 10. Open questions — pending Josh

None of these is answered here. The mechanics in section 3 fit every option
listed.

**Q1. Rename only, or rename plus dependent rewrites?** The rows do not
settle this.

- **A. Disclose only.** One rename step. All references are disclosed as
  will break. This is the smallest write surface. Consequence: HAMCP-109 has
  no multi-step driver from HAMCP-295 (a single step needs no compensation).
  The 2026-11-07 decision names rename as 109's concrete driver, so 109 would
  need a different driver or re-scoping by Josh.
- **B1. Rename plus rewrites of automations, scripts and scenes.** Uses
  existing reviewed adapters; scenes only where signed. Dashboards, groups
  and templates are disclosed as will break. This exercises 109 fully with
  no new provider path.
- **B2. B1 plus dashboards.** Requires making the delegated ha-mcp dashboard
  setter a configuration-plan child. That is a new provider-dispatch path and
  new scope beyond both rows.
- **C. Rename plus rewrites of automations and scripts only.** Same mechanics
  as B1, with a smaller adapter set.

Sub-questions:

- (b) Under B or C, when a holder has an old-id occurrence the structural
  rewriter cannot attribute (for example inside a template string), should
  the plan proceed and list that holder as will break, or refuse the whole
  plan?
- (c) Under B or C, should the rename run first or the rewrites run first?
  Both leave a disclosed broken window.

**Q2. Template coverage.** Template references are `unavailable` until
HAMCP-264. HAMCP-264 is ranked ahead of 295 in the lane order, but its row is
Backlog. Does HAMCP-295 wait for HAMCP-264, or ship with truthful
`unavailable` disclosure?

**Q3. Policy class of a rename step.** `standard_admin`, or `elevated_admin`
for actuator domains following the 069A E1 pattern, or elevated for every
dependency-bound plan? The classification is decision support only and does
not make an owner-authorized rename non-actionable.

**Q4. Release target.** HAMCP-109 is "IN 3.0". HAMCP-295 has no recorded
target. Should a single-step rename (S1, option A semantics, owner rollback
via `rollback_change`) ship in a 2.6.x release ahead of 109? Or should 295
ship only together with 109 in 3.0?

**Q5. Plan size.** Should a rename with more than 7 dependent rewrites be
refused, as designed here under the existing `MAX_CONFIGURATION_OPERATIONS = 8`,
or should the limit be raised for this mode?
