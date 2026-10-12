# HAMCP-069A governed entity metadata administration — design

Status: **design proposal for owner review. No runtime code is changed by this
document.** Part 2 (implementation) begins only after Josh approves this design
in writing, with any amendments. Nothing here authorizes deployment, live Home
Assistant access, registry signing, or a release.

- Register: HAMCP-069 (this increment recorded as 069A).
- Base: `main` at `77a547cef032cf26ba7bb3631e19a3b0ece97d62`.
- Engineering version at base: `2.4.1-beta.3`; connector total 83
  (25 canonical + 33 Engineering-native + 25 delegated reads).
- Support window at base (`ci/support-window.yaml:38-44`): Core current
  `2026.10.0`, candidate `2026.10.1`, rollback `2026.9.4`.
- Owner decisions applied: Josh, 2026-10-02 and 2026-10-11, as restated in the
  069A task on 2026-10-12. The 2026-10-12 provider decision (a direct HA
  websocket registry write, not a wrapper over upstream `ha_set_entity`)
  supersedes the register's 2026-10-11 note that said "wrapper over upstream
  ha_set_entity". That change is recorded in the register.

Owner decisions this design needs are collected in
[§13](#13-owner-decisions-requested). Findings that contradict the task's
assumptions are in [§12](#12-findings-against-the-stated-assumptions).

## 1. Tool surface

There is one new Engineering-native proposal tool, `create_entity_metadata_plan`.
Approval and execution use the existing `approve_change_plan` (an external Home
Assistant administrator challenge) and `apply_change_plan`. Reversal uses the
existing `rollback_change` (§8). No other public tool changes shape.

### 1.1 Input schema (closed)

| Field | Type and constraint | Meaning |
|---|---|---|
| `entity_id` | required; one string matching `^[a-z0-9_]+\.[a-z0-9_]+$`, at most 255 characters | The single target. No lists, globs, area, device or label targets. |
| `name` | optional; string of 1–100 characters, or `null` | The final registry `name`. `null` reverts to the integration default. |
| `icon` | optional; string matching `^[a-z0-9-]+:[a-z0-9-]+$` (at most 100 characters), or `null` | The final registry `icon`. `null` clears it. |
| `aliases` | optional; list of 0–16 items. Each item is a string of 1–100 characters with no leading or trailing whitespace, case-insensitively unique, or `null` (at most one `null`, meaning HA's computed-name alias placeholder) | The **complete** final alias list, in order. |
| `area_id` | optional; an existing area id, or `null` | The final entity-level `area_id`. `null` clears the override, so the entity follows its device area. |
| `hidden` | optional; boolean | `true` sets `hidden_by: "user"`. `false` sets `hidden_by: null` (refused if the entity is integration-hidden; see §2.3). |
| `add_labels` | optional; 0–16 existing label ids or labels created by this plan | Labels to add. |
| `remove_labels` | optional; 0–16 label ids currently on the entity | Labels to remove. |
| `categories` | optional; map of `scope` (slug) to `category_id` or `null`, with 1–8 entries | Per-scope category assignment (`null` removes the scope). Scopes not listed are untouched. |
| `create_labels` | optional; 0–4 objects of the form `{name, color?, icon?}` | Labels to create (create-only). A created label is used only when it also appears in `add_labels`, referenced by its predicted id (§6). |
| `expiration_minutes` | optional; integer 5–1440, default 120 | The same as the existing plan tools. |

The planner normalizes `add_labels`/`remove_labels` into one full final label
set. The plan records only that **final set**, not the deltas (§3).

At least one metadata field must be supplied. `add_labels` and `remove_labels`
must not overlap. Adding a label the entity already has, or removing one it
does not have, is reported as already satisfied rather than as an error. A plan
whose final state equals the current state for every supplied field is a no-op
(§3.4).

### 1.2 Explicitly rejected (no partial acceptance)

These names are refused with a dedicated reason code,
`entity_metadata_field_not_supported`, before any Home Assistant read:

- `new_entity_id` and any other rename;
- `disabled_by`, `enabled` (deferred to 069B);
- `expose_to`, `should_expose`, `options`, `options_domain` (deferred to 069B,
  or excluded entirely);
- `device_class`, `device_id`, or any other device-registry field;
- `entity_ids`, `targets`, or any list of entities, wildcard, area, device or
  label target;
- any other unknown key.

**Mechanism.** By default, native tools silently ignore unknown arguments.
FastMCP's argument model inherits pydantic's `extra="ignore"`. The tool will
therefore use the existing strict-tool pattern:

- a `Tool` subclass that checks the raw argument key set before SDK coercion, as
  `ConfigurationReverificationTool` does (`tools/governance.py:505-532`);
- `tool.parameters["additionalProperties"] = False`;
- `ConfigDict(extra="forbid")` for the nested `create_labels` and `categories`
  item types.

Independently of input validation, the provider builds the outgoing websocket
payload from a closed field allowlist (§2.1). An unknown key therefore cannot
reach Home Assistant even if validation regressed.

### 1.3 Output

`create_entity_metadata_plan` returns the same envelope as other plan tools:
`status`, `plan_id`, `plan_hash`, `approval_required`, `policy` and the
approval URL. It adds a bounded `entity_metadata` projection containing:

- `current` and `final` for every touched field;
- `already_satisfied` fields;
- `labels_to_create`, with each predicted `label_id`;
- `consumer_disclosure` (§4);
- `rollback_disclosure` (§8).

`get_change_plan` gains the same projection. This is an **additive** output
change; no existing field changes.

## 2. Home Assistant contract

Every claim below was verified against exact-tag Home Assistant source
(`github.com/home-assistant/core`) at tags `2026.9.4`, `2026.10.0` and
`2026.10.1`, as ADR-021 requires. `2026.10.0` and `2026.10.1` are byte-identical
for every file read. Line numbers below are for `2026.10.0`; `2026.9.4` is about
two lines lower. The schema library moved from voluptuous (9.4) to `probatio`
(10.0), and the schema dictionaries are textually equivalent. Probatio's own
semantics were not independently audited, so the implementation will add
per-version contract tests (§11).

### 2.1 Websocket messages (the only shapes the provider may send)

**Read: one registry entry.** This is not admin-only and returns
`extended_dict`, or the error `not_found` (`components/config/entity_registry.py:98-124`).

```json
{"type": "config/entity_registry/get", "entity_id": "<entity_id>"}
```

**Read: referenced registries.** These are not admin-only.
`category_registry/list` **requires** `scope`.

```json
{"type": "config/label_registry/list"}
{"type": "config/area_registry/list"}
{"type": "config/category_registry/list", "scope": "<scope>"}
```

**Write: the entity update.** This is `@require_admin`
(`entity_registry.py:154-198`). The payload is built from this closed allowlist
only, and only touched keys are sent:

```json
{"type": "config/entity_registry/update",
 "entity_id": "<entity_id>",
 "name": "<str>|null", "icon": "<str>|null",
 "aliases": ["<str>"|null, ...], "area_id": "<str>|null",
 "hidden_by": "user"|null, "labels": ["<label_id>", ...],
 "categories": {"<scope>": "<category_id>"|null}}
```

Never sent: `new_entity_id`, `disabled_by`, `device_class`, `options_domain`
or `options`. Excluding `options_domain` also removes HA's partial-write window
(`entity_registry.py:284-293`), where the main update has already been stored
when options validation fails.

**Write: label creation.** This is `@require_admin` (`label_registry.py:70-99`).
`description` is never sent; it is out of scope.

```json
{"type": "config/label_registry/create", "name": "<str>",
 "color": "<str>", "icon": "<mdi:...>"}
```

**Authorization.** HA requires only an admin websocket connection; there are no
other gates. A non-admin connection receives `unauthorized`.

### 2.2 Field semantics

These were verified in `entity_registry.py:218-305` and helpers
`entity_registry.py:1948-2135`.

| Field | HA semantics | Design consequence |
|---|---|---|
| `labels` | Full replacement, converted to a **set**: order and duplicates are lost. **Unknown label ids are silently dropped**, with no error (`label_registry.async_get_missing_label_ids`). | The planner requires every final label id to exist (or to be created earlier in the same plan). The final set's fingerprint is bound. Readback compares sorted sets; a missing id is a verification mismatch, never success. |
| `aliases` | Full **ordered** list replacement. Items are stripped, empty strings dropped, duplicates **kept**. `null` is stored as the `COMPUTED_NAME` placeholder (present in 9.4, 10.0 and 10.1) and read back as `null`. | Input must already be normalized (no surrounding whitespace, no empty strings, no case-insensitive duplicates), so readback equals the request exactly. At most one `null` is allowed. |
| `categories` | **Per-scope merge**. `scope: id` sets or moves a category; `scope: null` removes it; unmentioned scopes are untouched. Scope keys must be slugs. **Category existence is not validated.** | The planner validates each category through `category_registry/list` for that scope and binds the definition fingerprint. The recorded final state is the full resulting categories map, and readback compares the full map. |
| `hidden_by` | API accepts only `null` or `"user"`. `null` also clears `integration` hiding. | `hidden: false` on an `integration`-hidden entity is refused (§2.3). |
| `area_id` | `null` clears it. **Area existence is not validated** on update. | The planner validates through `area_registry/list` and binds the area definition fingerprint. |
| `name` | `null` reverts to the default. Any string is accepted, including `""`. | `""` is refused, which avoids an unnamed entity. Use `null` to revert. |
| `icon` | `null` clears it. There is no `cv.icon` check on entity update. | The `prefix:name` pattern is enforced locally. |
| No-op | Every field is compared. If nothing changed, HA returns the old entry with no save and no event. | A no-op is refused at planning time. If it is found at final preflight, the plan finishes as verified success with zero dispatch (§7). |
| Response | `{"entity_entry": extended_dict}`. `require_restart`/`reload_delay` appear **only** for `disabled_by` changes, which are excluded here. | The response is evidence, not verification. Authoritative verification is a separate `entity_registry/get` readback (§2.4). No restart or reload is required. |

### 2.3 Refusals derived from the HA contract

- **Not registry-managed.** An entity with no `unique_id` is not in the entity
  registry; `get` and `update` return `not_found`. The planner refuses with
  `entity_not_registry_managed`. If the entity also has no state, it refuses
  with `entity_not_found`. A state-only entity is never "updated" through any
  other path.
- **Area or name on a device entity with no name of its own.** This rule is new
  in `2026.10.0` and `2026.10.1` and absent in `2026.9.4` (helpers
  `entity_registry.py:1289-1296, 2106-2113`). HA rejects (`invalid_info`) an
  update touching `area_id` or `name` when the resulting entry has a
  `device_id`, its own `area_id`, and an empty unprefixed name. The planner
  refuses this **resulting state on every Core**
  (`entity_metadata_area_requires_own_name`). Refusing it on 9.4 too means
  rolling Core forward never makes a plan created under 9.4 invalid. HA's
  pre-store check means nothing is partially written if the rule is reached
  anyway.
- **Clearing integration hiding.** `hidden: false` when `hidden_by` is
  `integration` would clear integration-owned hiding. This is refused
  (`entity_metadata_integration_hidden`) unless Josh amends it (D6).
- **Disabled entities.** Metadata of a disabled entity may be edited; HA
  allows it, and it does not re-enable the entity. This is disclosed.

### 2.4 Exact readback

After dispatch, the provider issues `config/entity_registry/get` for the exact
`entity_id` and compares every touched field against the plan's recorded final
state:

| Field | Comparison |
|---|---|
| `name`, `icon`, `area_id` | Exact equality, including `null`. |
| `aliases` | Exact ordered list equality, with `null` for the placeholder. |
| `labels` | Exact set equality with the final label set. |
| `categories` | Exact equality with the full final categories map. |
| `hidden_by` | `"user"` or `null`, exactly as planned. |
| **Untouched fields** | Every other `extended_dict` field, excluding `modified_at`, must equal the preflight entry. This detects a concurrent change that landed between preflight and dispatch. |

A label creation is verified by `config/label_registry/list`. Exactly one label
with the predicted `label_id` must exist, and its name, color and icon must
equal the request.

## 3. Plan semantics

### 3.1 Plan family

**Recommendation (D1): model this as a governed *configuration* plan**
(`CONFIGURATION_PLAN_CONTRACT_VERSION = 2`, `ChangeOperation.CONFIGURATION_PLAN`)
with two new fixed resource types:

- `entity_registry_metadata`, with `update` only;
- `label`, with `create` only.

Each becomes one F3 configuration child. The provider follows the
`governance/helper_state.py` direct-provider pattern: a code-owned constant
provider identity, exact argument constraints, no arbitrary payload, no
fallback, and authoritative readback.

The alternative is the operational family (contract v3, like helper state). It
is rejected for three reasons:

1. `rollback_change` refuses every contract-v3 plan
   (`f3_runtime/runtime.py:3173`). ADR-018 states that operational rollback
   remains unavailable, so the requested `rollback_change` reversal would need a
   runtime change and an ADR-018 amendment.
2. Operational plans are single-dispatch (`task_models.SINGLE_DISPATCH_OPERATIONS`),
   so a plan that creates a label and then applies it would need a new
   multi-step operational model.
3. A new `ChangeOperation` enum value is persisted. On a downgrade to beta.3,
   `ChangePlan.from_dict` would raise and the store would quarantine the record
   as corrupt (`governance/storage.py:600-604, 668-685`). Configuration
   operations carry `resource_type` as a plain string (`governance/models.py:312,321`).
   An older build therefore loads them and then refuses to act on the unknown
   resource type.

The configuration family already supports:

- ordered children with per-child durable intent, at-most-once dispatch,
  readback-only recovery and truthful partial-sequence results (ADR-017);
- reverse update plans built from exact persisted prior state through
  `rollback_change`, with creates excluded (ADR-018).

It also already contains websocket-written fixed resource adapters with exact
readback, for `input_boolean` and `input_number` configuration
(`governance/resources.py`).

Provider identity: `direct_home_assistant_entity_registry`. Contract:
`direct-ha-exact-entity-registry-metadata-v1`. `fallback: none`.

### 3.2 Immutable plan content

The plan records the **exact final state** of every touched field, not deltas:

- **Target.** The `entity_id` plus the registry `id` (UUID) and `unique_id`
  from the planning read. A different registry `id` at preflight is stale; it
  catches the case where an entity id was deleted and recreated.
- **Per touched field.** The `current` value and the `final` value. `labels`
  holds the complete final set. `categories` holds the complete final map.
  `aliases` holds the complete ordered list.
- **Label creation children.** For each, the `name`, `color`, `icon`,
  predicted `label_id`, and the normalized-name availability evidence.
- **Fingerprints.** These are bound into `baseline` and therefore into
  `plan_hash`. `baseline` is hashed and is the field that carries freshness
  evidence (`service.py:713-750`).
  - **Registry entry:** `stable_hash` of the full `extended_dict`, including
    `modified_at`.
  - **Referenced labels:** each label definition the final set adds or removes
    (`label_id`, `name`).
  - **Label namespace (only when creating):** a fingerprint of the
    `(label_id, normalized name)` set.
  - **Areas:** the current and final area definitions (`area_id`, `name`,
    `floor_id`).
  - **Categories:** each touched scope's current and final category definition.
  - **Consumer disclosure:** the evidence fingerprint plus the dependency-index
    identity (§4).
- **Policy and provider.** The policy snapshot (F2), and the provider and
  contract identities.

### 3.3 Stale-state refusal before dispatch

F3 re-reads the entry and every referenced definition immediately before the
sole dispatch opportunity of each child, after the complete lock set is held:

| Preflight result | Outcome |
|---|---|
| Entry fingerprint unchanged | Dispatch is permitted (once). |
| Every touched field already equals its final value (someone else made the same change) | Verified success with zero dispatch; the approval is not consumed. |
| Any other change to the entry, or to a referenced label, area or category definition, or to the label namespace for a create | `stale_baseline`. Refused before dispatch. A new plan is required. |
| Material change in the consumer-disclosure evidence fingerprint | Approval invalidated (`dependency_evidence_changed`). Refused before dispatch. |

### 3.4 No-op refusal

If every supplied field already equals its requested final value and no label
needs creating, planning returns `status: no_change`,
`outcome: already_in_desired_state`, `plan_created: false`, `verified: true`.
No plan is stored, as for helper state (`service.py:3402-3416`). Supplied
fields that are already satisfied are listed in the plan, but they are not
touched and not sent.

### 3.5 Locks

These follow the conventions in `f3_configuration/locks.py`:

- **Exclusive** `entity_registry:<entity_id>`.
- **Exclusive** `label_registry:create`, only for label-creation children. This
  serializes label creation across concurrent plans so that a predicted
  `label_id` cannot be taken by another plan.
- **Shared** `core:home_assistant`, which conflicts with Core restart plans.

## 4. Consumer disclosure

The disclosure uses the existing dependency index only; no new index is built.
The planner obtains a fenced snapshot the way the helper planner does
(`DependencyIndex.get(refresh=True)`, `dependency/index.py:264`). It binds the
snapshot identity and an evidence fingerprint into `baseline`, and reports:

| Change | Evidence source (existing) | Reported as |
|---|---|---|
| Label added or removed | Ledger obligations with `context_provenance` `entity_set_producer:label_entities` whose `literal_selectors` contain the label id or name. These are resolved by `extraction.resolve_literal_label_obligations` (`extraction.py:1800`). Also automation action `effect_targets` tokens `…|label_id|<id>` (`governance/risk.py:536-551`). | The automations and blueprints that reference the label, each with an exact or opaque outcome and, where known, the action's service and physical consequence. |
| Area changed (old and new area) | Obligations from `entity_set_producer:area_entities` with literal selectors, plus `effect_targets` tokens `…|area_id|<id>`. | The same, for both the old and the new area. |
| Aliases or name changed | **No existing model.** Assist alias resolution is not indexed. | Coverage `unavailable` for Assist and voice. HA refreshes the default conversation agent's cache when `aliases`, `area_id` or `name` change (`conversation/default_agent.py:92-99`). This is disclosed. |
| Any of the above | `SourceCoverageItem.completeness` per source type. | Scripts, scenes, groups, template entities and dashboards are reported as `unavailable` (`dependency/provider.py:893-902`). Trigger and condition `label_id`/`area_id` selectors, `data.area_id`, non-literal `label_entities`/`area_entities` arguments, `effect_projection_clipped` and `processing_limit_exceeded` are reported as **incomplete coverage**. They are never reported as "no consumers". |

Disclosure is decision support (ADR-022): it is shown on the approval page and
in the plan projection. Only its fingerprint and the policy-relevant facts
(§5) are execution-binding.

Known gaps are disclosed and are not fixed in 069A. Each would be a separately
scoped index change:

- structured `label_id`/`area_id` selectors in triggers and conditions are not
  indexed;
- `area_entities` is never resolved to members;
- dashboards, scripts and templates are not scanned by the index;
- Assist aliases are unmodeled.

## 5. Policy (existing F2 classes only)

No new F2 class, risk value, consequence value, or acknowledgement type is
added. `governance/policy.py` gains one classification branch for the new
resource types; today an unknown operation falls through to `prohibited`
(`policy.py:779-786`). `POLICY_VERSION` stays `f2-v2` because existing
classifications are unchanged. Reviewers should confirm this.

**Default: `standard_admin`**, with risk `low` and physical consequence `none`,
and one `plan_approval`. This applies to:

- changes limited to `name`, `icon`, `aliases`, `categories` and `hidden`, in
  any domain;
- label and area changes on entities whose domain is **not** an actuator.

The evidence for the second case: HA resolves `label_id`/`area_id` service
targets per service domain, so membership of a non-actuating entity cannot
widen a service call's physical reach. Downstream template consumers stay
disclosed (§4) as incomplete coverage.

**Elevated: `elevated_admin`** (`plan_approval` plus
`elevated_risk_acknowledgement`, with the existing owner-authoritative
mechanics). It applies in two cases, with evidence:

1. **E1 — membership change on an actuator.** An `add_labels`/`remove_labels`
   or `area_id` change on an entity whose domain is in the existing reviewed
   direct-effect set `_HELPER_DIRECT_EFFECT_DOMAINS` (`governance/risk.py:63-75`:
   alarm_control_panel, climate, cover, fan, light, lock, switch, valve,
   water_heater) or in the transitive set (automation, scene, script).
   - **Why:** label- and area-targeted service calls in uncovered sources
     (scripts, dashboards, Assist "turn off the kitchen") gain or lose this
     actuator, and the index cannot see them (§4).
   - **Classification:** risk `high`. Physical consequence `indirect`, or
     `safety_critical` for `_HELPER_SAFETY_CRITICAL_DOMAINS` (alarm_control_panel,
     lock, valve).
2. **E2 — indexed evidence of a physical consumer.** Any indexed automation
   action `effect_targets` token, or a literal `label_entities`/`area_entities`
   obligation, that references an added or removed label or the old or new area
   and feeds an action classified as direct or safety-critical by the existing
   risk model.
   - **Classification:** risk `high`, and physical consequence equal to the
     strongest consequence found.

**Not elevated, but disclosed.** Name and alias changes on actuators change
Assist addressing, not membership. Coverage is `unavailable` and visibly
disclosed. Josh may move these to E1 (D4).

**Prohibited (`prohibited`) only for execution-integrity failures:**

- a missing or incomplete execution contract;
- a missing dependency snapshot;
- a provider-contract mismatch.

It is never used solely because of a consequence (ADR-022; ADR-012 amendment).
Ambiguous targets and arguments are refused before a plan exists.

**Approval.** There is one external Home Assistant administrator approval,
bound to the exact plan hash. The MCP caller cannot self-approve.

**Owner choice (D3).** The proposed rule (E1/E2 elevated, everything else
standard with disclosed incomplete coverage) is narrower than the helper-state
precedent. That precedent elevates every case of incomplete consequence
evidence. The conservative alternative is to elevate **every** label or area
change. That would make the primary household use case (label editing) always
require the second acknowledgement.

## 6. Label creation

**Ordering.** All `label/create` children (at most 4) run first, in request
order. The single `entity_registry_metadata/update` child runs last and depends
on every create child.

**Exact id at plan time.** HA derives `label_id = slugify(name)` and appends
`_2`, `_3`… on a collision. A duplicate *normalized name* (casefolded, spaces
removed) is rejected with `invalid_info` (helpers
`normalized_name_base_registry.py:28-30, 70-77`). To keep the plan's final label
set exact:

- `create_labels[].name` is restricted to `^[A-Za-z0-9][A-Za-z0-9 _-]{0,49}$`.
  For that character set, HA's `slugify` result is mechanically derivable
  (lowercase, with runs of non-alphanumeric characters becoming `_` and trimmed).
  The implementation will pin this mapping with per-version contract tests.
- **Planning refusals.**
  - The normalized name matches an existing label: `label_already_exists`, with
    the existing `label_id` returned so the caller can use `add_labels`.
  - The predicted id collides with an existing `label_id`:
    `label_id_collision`.
  - Two `create_labels` entries in the same plan normalize to the same name, or
    predict the same id: `duplicate_label_request`.
- **Binding and preflight.** The predicted `label_id` and the label-namespace
  fingerprint are bound into the plan, and the namespace is re-checked at
  preflight under the `label_registry:create` lock.
- **Response check.** If the `create` response's `label_id` differs from the
  prediction, the child is a verification mismatch. The sequence stops before
  the entity update, and the returned id is never substituted.

**Allowed values.** `color` matches `^#[0-9a-fA-F]{6}$` or a lowercase theme
token `^[a-z][a-z0-9-]{0,31}$`; HA validates the token, and an HA refusal is a
confirmed pre-change failure. `icon` matches the `prefix:name` pattern.
`description` is not supported.

**Non-atomicity.** A created label persists even if the later entity update is
stale, refused or fails. This is disclosed before approval and in the result:
`labels_created: [...]`, `entity_update: not_dispatched|failed|…`. A retry plan
then sees the label as existing and uses `add_labels`.

**Rollback.** Created labels are **never deleted automatically**. ADR-018
prohibits create-through-delete. Label deletion is not a reviewed write path in
069A, and the delete would remove the label from every entity that acquired it
afterwards. The rollback plan restores the entity's prior label set, which
removes the created label from this entity. It then reports each created label
as `retained_unused_label` for manual cleanup.

## 7. F3 execution

### 7.1 Operation identities

These are new `CONFIGURATION_REGISTRATIONS` (`f3_runtime/registry.py:34-43`):

- `("update_entity_registry_metadata", "entity_registry_metadata", "update")`
- `("create_label_registry_entry", "label", "create")`

They are declared as `code_owned_closed_contract`, with
`exact_registry_readback` verification. The reverse model is
`governed_reverse_update_plan` for the update and `none` for the create. The
F3 registry completeness validation (`registry.py:195-225`) must accept both.

### 7.2 At-most-once dispatch

This reuses the existing configuration child execution (ADR-017):

1. Hold the complete lock set (§3.5).
2. Run the final preflight (§3.3).
3. Commit durable intent.
4. Send one websocket command.
5. Run the authoritative readback (§2.4).

Duplicate `apply_change_plan`, process restart and startup reconciliation
never create a second dispatch opportunity.

**Uncertain outcome.** If the response is lost (a timeout or disconnect after
send), the child is indeterminate. Recovery runs **readback only**:

- if the final state is observed, the child is `succeeded_verified`;
- if the pre-state is still observed, the child is `failed_post_dispatch`
  (`not applied, dispatch uncertain`), still with **no redispatch**;
- anything else is `manual_review_required`.

The existing 24-hour post-dispatch deadline applies.

### 7.3 Distinct outcome reporting

These use the existing F3 outcome and task-state vocabulary
(`f3/contracts.py:36-50`, `f3/models.py:36-83`).

| Situation | F3 outcome / task state | Dispatch? |
|---|---|---|
| Verified final state | `SUCCEEDED_VERIFIED` | 1 |
| Final state already present at preflight | `SUCCEEDED_VERIFIED`, with `dispatch_count: 0` | 0 |
| Stale entry, referenced definition or label namespace | `PREFLIGHT_REJECTED` / `stale_baseline` | 0 |
| Consumer evidence drift | `PREFLIGHT_REJECTED` / `dependency_evidence_changed` | 0 |
| Provider (websocket) unavailable before dispatch | `PROVIDER_UNAVAILABLE_PRE_DISPATCH` | 0 |
| Core capability not admitted | `CoreCapabilityUnavailable` at the gateway (§9) | 0 |
| Policy denial, or approval missing, expired or mismatched | Refused at approve/apply (`prohibited`, `APPROVAL_*`) | 0 |
| HA refused the command (`invalid_info`, `unauthorized`, `not_found`) | `DISPATCH_FAILED_CONFIRMED` (confirmed not applied) | 1 |
| Response lost | `DISPATCH_INDETERMINATE`, then readback-only reconciliation | 1 |
| Response received, but readback differs (including a silently dropped label id, a concurrent edit to an untouched field, or a label id differing from the prediction) | `failed_post_dispatch` / `verification_mismatch` | 1 |

For a label-then-update sequence, results are reported per child. A failure in
child *n* stops the sequence. Later children are reported as `not_dispatched`,
and earlier verified creates stay reported as applied.

## 8. Rollback

`rollback_change(plan_id, expected_plan_hash)` stays a **planner**: it never
mutates. It creates a new configuration plan under the ADR-018 rules
unchanged:

- **Allowed only when** the forward `entity_registry_metadata/update` child was
  authoritatively verified, its exact prior field values are persisted, and the
  current readback still matches the forward result fingerprint. Otherwise it
  returns `rollback_not_available`, with reason `forward_not_verified`,
  `prior_state_missing` or `entry_changed_since_apply`.
- **Content.** One `update` child whose final state is the **prior value of
  every field the forward plan touched**: name, icon, the full alias list, the
  full label set, area_id, hidden_by, and the full prior categories map
  restricted to the touched scopes (`scope: null` where a scope was previously
  absent).
- **Labels.** A forward label that has since been deleted from the label
  registry makes the rollback refuse with `rollback_reference_missing`. HA would
  otherwise silently drop it, and verification would fail. A forward area or
  category that has since been deleted is handled the same way.
- **Creates.** Created labels are excluded and reported (§6).
- **Execution.** The rollback plan gets its own F2 classification (the same
  §5 rules applied to the reverse delta), its own external approval (the
  forward approval is never reused), its own preflight against the
  then-current entry, the same lock set, one dispatch, and exact readback.
- **Disclosure.** The approval page states that rollback writes the forward
  plan's *prior* values for every touched field, replacing whatever those
  fields hold at that moment. Two freshness checks bound this. Rollback planning
  is refused if the entry changed after the forward apply. The rollback plan's
  own preflight refuses any change made after the rollback plan was created. A
  later edit is therefore refused as stale rather than silently overwritten.
  The disclosure says this plainly: *"Restores the values captured before plan
  X, replacing the current values of these fields. If the entity was edited
  since, this plan is refused as stale; it never merges."* See F11.

## 9. Core readmission

Today every static tool and every F3 apply identity is mapped to Core
requirements in `ha_core_readmission/routes.py`. No profile covers entity or
label registry **writes**. `core.non_device_registry_read` covers only the list
endpoints for areas, floors, labels and entities, not
`entity_registry/get` or `category_registry/list` (`profiles.py:183-198`).

### Proposed requirement

There is one new mutation-capable profile, `core.entity_registry_metadata_operation`.
It gets a new `CoreCapabilityClass` member and is appended as a **separate
tuple** to `CORE_RUNTIME_CAPABILITY_PROFILES`, never to
`CORE_CAPABILITY_PROFILES`. That tuple is hashed into every signed probe
fingerprint (`probe_profiles.py:26`).

- `adapter_id`: `compiled-core-entity-registry-metadata-operation-v1`
- `provider_boundary`: `direct_ha_api`
- Required checks:
  - `websocket_entity_registry_get`
  - `websocket_entity_registry_update_closed_fields`
  - `websocket_label_registry_list`
  - `websocket_label_registry_create`
  - `websocket_area_registry_list`
  - `websocket_category_registry_list_scoped`
  - `entity_registry_readback_exact`
- `predispatch`: `core_identity`, `capability_contract`, `target_fingerprint`,
  `session_fingerprint`, `generation`.
- Probe readiness goes in `ha_core_readmission/source.py`. It is read-only and
  uses the existing pattern at `source.py:201-262`.

### Mapping

- The planner `create_entity_metadata_plan` maps to
  `core.entity_registry_metadata_operation` plus
  `core.dependency_helper_planning`.
- The F3 identities `update_entity_registry_metadata` and
  `create_label_registry_entry` map to `core.entity_registry_metadata_operation`
  plus `core.f3_mutation_verification`.
- They do not map to `core.governed_configuration_operation`. That profile's
  signed contract attests `existing_governed_configuration_adapters`, and
  reusing it would admit a new write adapter under evidence that never covered
  it.

### Evidence and fail-closed behaviour

**This is a finding (§12, F4).** Compiled exact authority covers only Cores
`2026.7.2`–`2026.9.1` (`profiles.py:80-106`), which is outside the current
window. On `2026.10.0` (current) and `2026.9.4` (rollback), a profile is
admitted **only** if Josh's **owner-signed** Core release registry
(`upstream-trust/ha-core-release-registry.json`, `registry.py:144-186`) lists
its exact `profile_id`, `adapter_id` and `contract_fingerprint`.

This task forbids registry changes, and the registry is owner-signed. 069A
therefore ships the profile and mappings, and the tool **fails closed** with
`CoreCapabilityUnavailable` on every supported Core. That covers planning and
apply alike, before any provider call (`routing.py:707-745`). The capability
stays dormant until a **separate owner-signed registry sequence PR** adds the
reference for `2026.9.4`, `2026.10.0` and `2026.10.1`. This is the same split
as `capture_automation_baseline`: tool PR #228, then signed-registry PR #230 /
`dab2c99`.

The evidence package for that signing has three parts:

1. The exact-tag source citations in §2.
2. The disposable-container contract run (`scripts/real_ha_contract_tests.py`)
   on each window Core: update, get, label create, readback, `not_found`, the
   2026.10 area/name refusal, silently dropped label ids and response loss.
3. A `docs/evidence/` record.

The container run belongs to the separately authorized full/evidence tier, not
to this branch's PR CI.

An unsupported or unknown Core admits nothing, so the tool and its apply
identities remain unavailable. The F3 route for an unknown identity already
resolves to `core.unsupported_f3_operation`, which has no profile.

**Related gap (§12, F6).** A static tool with no mapping skips the Core gate
entirely (`routes.py:215`, `routing.py:711`), and no test enforces completeness.
069A adds the mapping explicitly. It also proposes a static-tool completeness
test, which is test-only.

## 10. Capability and catalog metadata

- **Count.** There is one new tool: static registered 58 → 59, Engineering-native
  33 → 34, connector total **83 → 84**. These counts are derived in
  `tools/registry.py:170-171` and `capabilities.py`; `scripts/codex-context.py`
  needs no edit. Hard-coded assertions in about 33 test modules, the catalog
  fixtures (`fixtures/dashboard_analysis/native-c82-descriptors.json`,
  `fixtures/integration_inspection/beta10_catalog_baseline.json`),
  `promotion/promotion_regression_manifest.yaml`, `README.md` and the acceptance
  documents are updated together.
- **`list_capabilities`.** One `BETA_NATIVE_CAPABILITIES` entry, modeled on
  `create_helper_state_plan` (`capabilities.py:203-224`):
  - status `beta_native`, `operation_class: proposal`, `routing: engineering_native`;
  - `provider: direct_home_assistant_entity_registry`, `fallback: none`;
  - `external_approval_required: true`, `direct_write_allowed: false`;
  - `apply_tool: apply_change_plan`, `rollback: governed_reverse_update_plan`;
  - `upstream_delegation: none`;
  - `excluded_fields` listing §1.2.
- **Provider matrix.** Plan tools have no `CAPABILITY_PROVIDER_MATRIX` row, so
  there is no change (`test_beta_v2.py:734` keeps 18 rows). The prose matrix in
  ADR-002 gains one line.
- **Routing.** `providers/routing.py` gets `create_entity_metadata_plan →
  OPERATIONAL_ADMINISTRATION` (Engineering-native), as for the helper planner.
  `apply_change_plan` and `rollback_change` remain the only direct-required
  routes.
- **Health snapshot.** `get_server_health` gains an
  `entity_metadata_provider` block, mirroring `helper_state_provider`
  (`service.py:12697-12790`). It contains provider identity, contract,
  `provider_contract_status: code_owned_exact`, `fallback: none`, Core
  admission status, and the last outcome counts. There are no new counts.
- **Upstream `ha_set_entity` stays unexposed.** It remains classified
  `persistent_write` in `upstream_tool_policy_8_6_0.json`, remains in
  `PROHIBITED_UPSTREAM_TOOLS` (`providers/upstream_dashboard.py:94-106`), and
  remains absent from registration. The same holds for `ha_config_set_label`
  and `ha_config_set_category`.
  - **Existing tests that continue to prove it:**
    - `test_readonly_upstream_gateway.py:4693-4717`
    - `test_upstream_release_registry.py:1155-1169`
  - **New tests:**
    - the entity-metadata provider has no upstream client dependency;
    - a monkeypatched upstream `ha_set_entity` is never invoked by plan, apply
      or rollback.

## 11. Change list, test plan, and PR fit

### 11.1 File-by-file change list (estimates)

Runtime files are under `hass_mcp_engineering_beta/ha_mcp_engineering/`, which
is protected. The task names this surface, so implementation is authorized by
Josh's design approval.

| File | Change | Est. lines |
|---|---|---|
| `governance/entity_metadata.py` (new) | Provider: closed payload builders, read/update/create/readback, provider evidence constants. | ~350 |
| `governance/entity_metadata_planning.py` (new) | Input normalization, final-state computation, refusals, fingerprints, label-id prediction. | ~450 |
| `governance/entity_metadata_disclosure.py` (new) | Consumer disclosure projection from the dependency snapshot (§4). | ~250 |
| `governance/service.py` | `create_entity_metadata_plan`, normalization check (`_require_current_normalization` branch), review summary projection, health block. | +~350 |
| `governance/resources.py` | Two resource types, normalization and fingerprint hooks. | +~80 |
| `governance/policy.py` | Classification branch (§5). | +~120 |
| `f3_configuration/{strategies,locks,adapter,models}.py` | Strategies for the two identities, lock keys, preflight, readback dispatch. | +~250 |
| `f3_runtime/{registry,runtime}.py` | Registrations, rollback planner support for the update child. | +~120 |
| `tools/governance.py`, `tools/registry.py` | Strict tool class, schema, `GOVERNANCE_TOOLS`, `_PROPOSAL_TOOLS`. | +~150 |
| `capabilities.py`, `providers/routing.py` | Catalog entry, route. | +~40 |
| `ha_core_readmission/{models,profiles,routes,source}.py` | Profile, class, mappings, probe readiness. | +~90 |
| `governance/runtime.py`, `f3_runtime/runtime.py` audit wrapper | Provider construction and audited gateway (as `_AuditedHelperStateGateway`). | +~70 |
| **Runtime subtotal** | | **~2,300** |
| `tests/test_hamcp069a_entity_metadata_*.py` (3–4 new modules) | §11.2 | ~2,500 |
| About 33 existing test modules, plus catalog fixtures and the promotion manifest | Count bumps 58→59, 33→34, 83→84; catalog snapshot entries. | ~60 one-line edits |
| `docs/HAMCP_069A_ENTITY_METADATA.md`, ADR-002 matrix line, `docs/CHANGE_GOVERNANCE.md` | Feature doc and acceptance. | ~250 |
| Release materialization: `config.yaml`, `version.py`, `scripts/validate_addon_metadata.py`, `CHANGELOG.md`, `README.md`, `docs/V2_4_1_BETA4_{ACCEPTANCE,RELEASE_NOTES}.md` | Required; see F7. | ~200 |

### 11.2 Test plan

All tests use deterministic offline fixtures with a fake websocket peer, as
`tests/AGENTS.md` requires.

- **Positive.**
  - Each field alone, then all fields together.
  - Labels: add, remove, and add plus remove. The final set is recorded and
    sent as a full list.
  - Aliases with the `null` placeholder.
  - Category set, move and remove.
  - Area set and clear.
  - Hide and unhide by the user.
  - Create one label and apply it, then create four.
  - The exact payload equals the closed allowlist for each case.
- **Refusal.**
  - Each §1.2 field, refused with zero HA reads.
  - Unknown keys; bulk or list targets; malformed ids, icons, colors and names.
  - Overlapping add and remove.
  - Unknown label, area or category.
  - An entity with no `unique_id` (`not_found`).
  - The 2026.10 area/name rule, refused on every simulated Core.
  - Clearing integration hiding.
  - Duplicate or colliding label creation.
  - No-op (no plan stored).
  - Policy `prohibited` paths.
- **Stale.**
  - The entry changed between plan and preflight.
  - A referenced label, area or category was deleted or renamed.
  - Another label was created that takes the predicted id.
  - Consumer-evidence drift.
  - An irrelevant index generation change does **not** invalidate the plan.
- **Uncertainty.**
  - Response lost, then readback shows the final state: success with
    `dispatch_count == 1`.
  - Response lost, then readback shows the pre-state: not applied, no
    redispatch.
  - Response lost and the provider is unavailable during recovery: readback
    only.
  - Duplicate apply.
  - Restart reconciliation.
- **Verification failure.**
  - A silently dropped label id.
  - A concurrent edit to an untouched field.
  - A created label id that differs from the prediction (the sequence stops
    before the update).
  - An HA `invalid_info` refusal.
- **Rollback.**
  - A verified forward plan produces a reverse plan holding the prior values of
    all touched fields, which needs its own approval and readback.
  - The entry changed since apply: `rollback_not_available`.
  - A forward label deleted since: refused.
  - Created labels are retained and reported.
  - An unverified forward plan: unavailable.
- **Consumer disclosure.**
  - A literal `label_entities` reference.
  - An area effect target.
  - Dynamic or opaque references reported as incomplete.
  - Dashboards and scripts reported as `unavailable`.
  - The disclosure fingerprint is bound into `plan_hash`.
- **Policy.**
  - Standard: name only on a light; labels on a sensor.
  - E1: labels on a light, or area on a lock (`safety_critical`); these remain
    owner-actionable under elevated policy and are never `prohibited`.
  - E2: an indexed physical consumer.
  - Input order cannot change the decision.
- **Catalog.**
  - The 84 total; the `list_capabilities` entry; schema
    `additionalProperties: false`; annotations.
  - The health block.
  - `ha_set_entity` is still prohibited and never invoked.
- **Readmission.**
  - The planner and both apply identities map to the new profile.
  - An unadmitted profile gives `CoreCapabilityUnavailable` with zero transport.
  - A Core outside the window admits nothing.
  - The profile is not in `CORE_CAPABILITY_PROFILES`, so existing probe
    fingerprints are unchanged.
  - The static-tool mapping completeness test.

**CI wall time.** The latest PR run took 8.0 minutes, and the slowest shards
took 7.0–7.2 minutes. The new modules are fast unit tests, estimated at under
30 seconds in total. Unmeasured modules default to 5 seconds in
`ci/unit-test-shards.json`, and that file is not changed. The ≤12-minute limit
is not at risk. Actual shard times will be reported.

### 11.3 Does it fit one coherent PR?

**Yes, as one coherent PR.** That means about 2,300 runtime lines, about 2,500
test lines, and about 60–70 files once count bumps and release materialization
are included. That is comparable to the `capture_automation_baseline` precedent
(`ed51a55`, 60 files).

If Josh or the reviewer judges it too large for the one-review budget, the
clean fallback is:

- **069A-1:** the entity update, policy, disclosure, rollback, catalog and
  readmission, with no `create_labels`.
- **069A-2:** label creation.

Creation is separable because 069A-1 already accepts existing labels.

## 12. Findings against the stated assumptions

- **F1 — Labels are a full-list replacement: true, but it is a set, and unknown
  ids are silently dropped.** Order and duplicates are lost, and a nonexistent
  label id is discarded without error. *Handling:* pre-validate existence, bind
  definition fingerprints, and treat a readback set mismatch as a verification
  failure.
- **F2 — Aliases: a full ordered list, but sanitized.** Items are stripped,
  empty strings dropped and duplicates kept, and `null` is a valid
  computed-name placeholder. *Handling:* require normalized input, so readback
  equals the request exactly.
- **F3 — Categories, area ids, and label ids on update are not validated by
  HA.** Categories also merge per scope rather than being replaced.
  *Handling:* validate every referenced id at plan time and preflight; record
  the full resulting map.
- **F4 — Registry writes need an owner-signed Core registry entry to be usable
  on any supported Core.** This is the significant finding. The task forbids
  registry changes, so 069A as designed merges **dormant**: it fails closed on
  2026.9.4, 2026.10.0 and 2026.10.1 until a separate owner-signed registry PR.
  The only way to avoid this would be to map the new adapter onto the
  already-signed `core.governed_configuration_operation`, which this design
  rejects as admitting an unattested write adapter. **Owner decision D2.**
- **F5 — Version-dependent HA behaviour inside the window.** 2026.10.x rejects
  area or name updates that leave a device-bound entity with an own area and no
  own name; 2026.9.4 accepts them. *Handling:* refuse uniformly.
- **F6 — An unmapped static tool bypasses the Core gate.**
  `static_tool_requirements()` returns `()` for unknown tools, and the gateway
  then skips `acquire` (`routes.py:215`, `routing.py:711`). This is pre-existing
  and not caused by 069A. A test-only completeness check is proposed. Changing
  the gateway to fail closed on an empty mapping is **not** proposed here,
  because it would be unrelated runtime work.
- **F7 — Any runtime change requires a beta version bump in the same PR.**
  `scripts/validate_addon_metadata.py:463-495` enforces this. 069A therefore
  needs `2.4.1-beta.4` materialization, which touches the protected
  `hass_mcp_engineering_beta/config.yaml`. Alternatively, `.release/next-version`
  (also protected) can be staged. **Owner decision D5:** authorize the protected
  release metadata path. PR #242 (HAMCP-159) may also claim beta.4, so the
  version must be coordinated at merge order.
- **F8 — Native tools ignore unknown arguments by default.** The explicit
  rejection required by §1.2 needs the existing strict-tool pattern, which is
  already used by four tools.
- **F9 — The task named the helper-state operational pattern, but the
  configuration family fits better.** This does not contradict the provider
  decision (direct websocket, exact constraints, no fallback, readback). It is
  a choice of plan family, made so that `rollback_change` and the label-then-
  update ordering work under existing ADR-017 and ADR-018, without amendment.
  **Owner decision D1.**
- **F10 — `hidden` does not change voice exposure that has already been
  decided.** HA uses `hidden_by` only for *default* exposure, on first
  evaluation (`homeassistant/exposed_entities.py:247-269, 304-312`). Hiding an
  entity does not un-expose it from Assist; that is 069B's `expose_to`. This is
  disclosed in the tool description.
- **F11 — Rollback cannot overwrite later edits.** The task asked for a
  disclosure that rollback overwrites later edits. Under ADR-018, rollback is
  available only while current readback still matches the forward result, and
  the reverse plan's own preflight refuses any change. A later edit therefore
  makes rollback unavailable or stale; it is never overwritten. The disclosure
  states both the replace semantics and the refusal (§8). Recovering after a
  later edit requires a new forward plan.
- **F12 — Persisted-record downgrade behaviour is only partly verified.**
  Configuration operations carry `resource_type` as a plain string, so beta.3
  should load 069A plans and refuse to act on them (§3.1). Whether every beta.3
  load path tolerates the new resource types without quarantining the record
  as corrupt is **unverified**. Part 2 will add a test that loads a 069A record
  through the beta.3 parsing path, or will report the result.

## 13. Owner decisions requested

| # | Decision | Recommendation |
|---|---|---|
| D1 | Plan family: configuration (contract v2) or operational (contract v3). | Configuration (§3.1, F9). |
| D2 | Readmission: a new profile that stays dormant until a separate signed-registry PR, or reuse of the signed `governed_configuration_operation`. | A new profile, and accept dormancy (§9, F4). |
| D3 | Policy: E1/E2 elevated with other changes standard, or every label/area change elevated. | E1/E2 (§5). |
| D4 | Name or alias changes on actuators: standard, or elevated. | Standard, with disclosure. |
| D5 | Authorize the protected release-metadata path for beta.4 materialization (F7). | Authorize; coordinate with #242. |
| D6 | `hidden: false` on integration-hidden entities: refuse, or allow with disclosure. | Refuse in 069A. |
| D7 | PR shape: one PR, or the 069A-1/069A-2 fallback. | One PR. |

## 14. Non-actions in Part 1

This part made no runtime, test, workflow, schema, registry, support-window,
admission, HOLD, merge-gate or shard-runner changes. There was no live Home
Assistant access, no manual or full-tier dispatch, and no release, tag or
deployment. Upstream `ha_set_entity` was not touched. PR #242 and its branch
were not touched.
