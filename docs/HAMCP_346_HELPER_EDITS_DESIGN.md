# HAMCP-346: governed configuration edits for more storage-collection helpers — design

Status: **design only (2.11.0 Lane C, PR 1 of 4).** This document changes no
source, schema, profile, registry or workflow. It does not authorize a build,
a release, a signing session, deployment or live Home Assistant access. A
build needs Josh's decisions in §9 and his named OK for each protected file
in §7.

| Item | Value |
|---|---|
| Base | `release/2.11.0` at `54627e35dcfd8e972d468aec8745fd918db53ad3` (= `main` after #522), Engineering `2.4.2-beta.14` |
| Register | HAMCP-346 (HA admin E034); shares the write mechanism with HAMCP-328 (helper create, after 3.0) |
| Signed registry | `upstream-trust/ha-core-release-registry.json`, tip **sequence 19** (expires 2027-02-23): 2026.10.0 (22 capabilities), 2026.10.1 (26), 2026.10.3 (31), 2026.11.0 (38), 2026.11.3 (38) |
| Core source | Raw files from the GitHub tag `2026.11.3`, read in a scratch copy outside the repository. These are **not** the signed source archive. A build re-derives every Core fact below from the signed `2026.11.3` archive before it relies on it |
| Paths | Core paths are relative to `homeassistant/` at tag 2026.11.3. `E/` = `hass_mcp_engineering_beta/ha_mcp_engineering/` |
| Examples | Every entity id, name and value in this document is synthetic |

## 0. Scope and owner direction

**Owner direction recorded so far.** The architect's GO block for Lane C
(2026-11-27) reports that Josh approved the Lane C list, in which HAMCP-346
is a design-only item in 2.11.0, delivered as its own docs PR. No build decision
exists. Nothing below is a Josh decision unless §9 later records his answer.

**Register outcome (HAMCP-346).** Engineering can plan, apply, read back and
undo configuration edits (name, icon and type-specific options such as a
timer's duration or restore setting) for existing storage-collection helpers
beyond `input_boolean` and `input_number`: `timer`, `input_select`,
`input_text`, `input_datetime`, `input_button`, `counter`, `schedule`. Small
edits like an icon stay inside the governed approval record instead of being
done by hand.

**Why now.** HA admin E034: a planning attempt could not change a timer's icon
through Engineering, so two timer icons were fixed by hand, outside the
approval record. The owner-held F066 storage read (2026-11-26, not in the
repository) also found stored `initial` values that no supported path can
clear: 2 `input_select` and 1 `input_text`. Nine `input_boolean` helpers with
a stored `initial` are probably already clearable today (§2.1, inference to
prove offline in the build).

**Not in scope.**

| Family | Owner | Why it is separate |
|---|---|---|
| Helper **values** (`control_helper`) | HAMCP-311, delivered | Service calls on state, not configuration |
| `input_select` **current option** | HAMCP-145, `create_input_select_option_plan` | A state change through `input_select.select_option` |
| Config-entry helper **options** (template, filter, statistics) | HAMCP-232 | Options flow, not a storage collection |
| Helper **create** and **delete** | HAMCP-328, HAMCP-329/298 | 346 adds `update` only; §5.2 reserves the create identities |
| YAML-defined helpers | none | Core exposes only storage-collection items to `{type}/update`; a YAML helper is absent from `{type}/list` and the read fails closed as "not found" |
| Read-only settings view | HAMCP-519 (`get_helper_settings`, 2.11.0 Lane C PR 4) | A read tool; 346 is the write path |

## 1. Today's helper edit path (source facts)

- **Types.** `SUPPORTED_HELPER_TYPES = {"input_boolean", "input_number"}` in
  both `E/governance/resources.py:89` and `E/governance/service.py:483`.
- **Public schema.** `create_configuration_plan`
  (`E/tools/governance.py:428`) takes `ConfigurationOperation` items with
  `resource_type: Literal["automation", "script", "helper"]` and
  `helper_type: NotRequired[Literal["input_boolean", "input_number"]]`
  (`E/tools/governance.py:182-188`). The service error text is "helper_type
  must be input_boolean or input_number" (`E/governance/service.py:3766`).
- **Field sets and validators.** `_INPUT_BOOLEAN_FIELDS = {icon, initial,
  name}` (`resources.py:111`), `_INPUT_NUMBER_FIELDS = {icon, initial, max,
  min, mode, name, step, unit_of_measurement}` (`resources.py:112-123`).
  `_validate_input_number` (`resources.py:1523-1566`) **requires** `min`,
  `max`, `step` and `mode` even though Core gives `step` and `mode` defaults,
  so the approved proposal equals what Core stores. `_unknown_field_errors`
  (`resources.py:1613`) refuses any other key. Only `proposed_config` is
  validated (`validate_resource`, `resources.py:197-265`).
- **Identity.** `ConfigurationResourceGateway.get` splits the target
  `<type>.<object_id>` (`_split_helper_entity_id`, `resources.py:1419-1439`,
  domain must equal the helper type), reads `{type}/list`, and matches
  `id == object_id` (`resources.py:765-792`). No match returns `None`;
  duplicates and identity mismatches fail closed. `resource_identity_matches`
  (`resources.py:630-662`) compares the returned `id` with the object id.
- **Dispatch.** `_HelperConfigurationStrategy` (`E/f3_configuration/strategies.py:338-375`)
  sends `{"type": f"{type}/update", f"{type}_id": object_id,
  **proposed_config}` over the WebSocket. `InputBooleanConfigurationStrategy`
  and `InputNumberConfigurationStrategy` (`:377-395`) set the type and
  `id_field`; the strategy map is at `:1114-1117`.
- **Capability identities.** `CAPABILITY_IDENTITIES` (`strategies.py:48-79`)
  maps `(type, action)` to identities such as
  `update_input_number_configuration`; the F3 registrations are
  `CONFIGURATION_REGISTRATIONS` (`E/f3_runtime/registry.py:35`).
- **Signed profile.** The existing helper identities are admitted by
  `core.governed_configuration_operation`
  (`E/ha_core_readmission/profiles.py:388-408`, contract
  `compiled-core-governed-configuration-operation-v1`, dispatch
  `existing_typed_governed_provider_only`) through
  `_F3_GOVERNED_CONFIGURATION_OPERATIONS` (`E/ha_core_readmission/routes.py:35`,
  helper entries at `:44-56`).
- **Locks.** `resource_lock_key` puts `input_boolean`, `input_number` and
  `input_select` in the `helper` prefix (`E/f3_configuration/locks.py:44-46`);
  the reload-key map names `reload:input_boolean` and `reload:input_number`
  (`locks.py:212-213`); `_DEPENDENCY_HELPER_DOMAINS = {input_boolean,
  input_select}` (`locks.py:31`).
- **Other closed sets.** `E/f3_configuration/migration.py:40-55` lists the
  migratable resource types; `_P4_TARGET_TYPES = {scene_configuration,
  input_boolean, input_number}` (`E/f3_runtime/dispatch_facts.py:155`).
- **Readback.** `normalize_resource_config` (`resources.py:339-351`) drops
  `id` and coerces `input_number` numerics to float; verification compares
  normalized forms (`compare_resource_verification`).
- **Undo.** `rollback_change` → `service.rollback_change` →
  `f3_runtime.create_rollback_plan` → `_plan_fresh_rollback`
  (`E/f3_runtime/runtime.py:5770-5801`). It builds one reverse `update` per
  verified source operation with `proposed_config = deepcopy(current_config)`,
  chained by `depends_on`, and excludes an operation whose current
  fingerprint no longer equals its verified resulting fingerprint. Planning
  runs under `ROLLBACK_PLANNING_REQUIREMENTS = ("core.dependency_helper_planning",)`
  (`runtime.py:930`). The reverse plan is a new plan with its own approval.

## 2. Core authority (2026.11.3)

### 2.1 An update replaces the whole stored item, defaults included

- The WebSocket update command's schema is the type's storage fields **with
  their defaults** plus the item id (`helpers/collection.py:634-644`).
  `ws_update_item` (`:737`) removes the message envelope keys and calls
  `async_update_item(item_id, data)` (`:328-353`), which calls the type's
  `_update_data(current, updates)` (`:338`) and stores the result.
- Every helper type's `_update_data` returns `{id: item[id]} |
  validated_update`: input_boolean (`components/input_boolean/__init__.py:84-87`),
  input_select (`input_select/__init__.py:216-221`), input_text
  (`input_text/__init__.py:186-191`), input_number
  (`input_number/__init__.py:226-229`), input_datetime
  (`input_datetime/__init__.py:157-160`), input_button
  (`input_button/__init__.py:77-80`), timer (`timer/__init__.py:157-163`,
  which also reformats `duration`), counter (`counter/__init__.py:160-163`),
  schedule (`schedule/__init__.py:122-125`).
- **Consequence 1 (defaults).** A field the proposal omits that has a Core
  default is **reset to that default**, not kept. Omitting a timer's
  `restore` stores `restore: false`.
- **Consequence 2 (clearing).** A field the proposal omits that has **no**
  default is **removed**. This is the only way to clear a stored `initial`,
  `icon`, `pattern` or `unit_of_measurement`. Today's `input_boolean` path
  allows a proposal without `initial`, so it can probably already clear one
  (inference; the build proves it with an offline test).
- **No compare-and-swap.** Core takes no expected revision. The residual
  window between Engineering's pre-dispatch fingerprint check and Core's
  write is the same as for today's helper updates.

### 2.2 Storage fields per type

"Default" means Core fills the field when it is omitted. Field validation
runs in the WebSocket schema and again in `_update_data`.

| Type | Field | Core rule | Default |
|---|---|---|---|
| timer | `name` | required string | — |
| | `icon` | `cv.icon` | none |
| | `duration` | `cv.time_period`; stored as `H:MM:SS` by `_format_timedelta` (`timer/entity.py:36-40`), fractional seconds truncated, hours unbounded | `0` (`timer/const.py:17`) |
| | `restore` | boolean | `false` (`timer/const.py:18`) |
| input_select | `name` | required, non-empty | — |
| | `options` | required, non-empty list of unique strings | — |
| | `initial` | string; must be one of `options` (`_cv_input_select`) | none |
| | `icon` | `cv.icon` | none |
| input_text | `name` | required, non-empty | — |
| | `min` | int, 0..255 | `0` |
| | `max` | int, 1..255 | `100` |
| | `initial` | string; length within `min..max` (`_cv_input_text`) | none (the `""` in the schema is the error message, not a default) |
| | `icon`, `unit_of_measurement`, `pattern` | strings | none |
| | `mode` | `text` or `password` | `text` |
| input_datetime | `name` | required, non-empty | — |
| | `has_date`, `has_time` | booleans; at least one true (`has_date_or_time`) | `false` each |
| | `icon` | `cv.icon` | none |
| | `initial` | string; Core's format check `valid_initial` (`input_datetime/__init__.py:64`) runs for YAML only, not storage | none |
| input_button | `name` | required, non-empty | — |
| | `icon` | `cv.icon` | none |
| counter | `name` | required, non-empty | — |
| | `icon` | `cv.icon` | none |
| | `initial` | int | `0` (cannot be cleared) |
| | `minimum`, `maximum` | int or `null` | `null` each |
| | `restore` | boolean | `true` |
| | `step` | positive int | `1` |
| schedule | `name` | required, non-empty | — |
| | `icon` | `cv.icon` | none |
| | `monday` … `sunday` | lists of `{from, to, data?}`; `valid_schedule` sorts by `from`, refuses `from >= to` and overlaps; `to` may be `24:00:00`; stored times are strings; `data` maps strings to bool, string, int or float | `[]` each |
| input_number (existing) | `device_class` | `DEVICE_CLASSES_SCHEMA`; with a unit, `_cv_input_number` checks the unit fits the class | none |

### 2.3 What a configuration update does to the helper's current state

From each type's `entity.py` `async_update_config` at 2026.11.3. These are
consequences to disclose in the plan, not refusals.

| Type | Effect of an update |
|---|---|
| timer | An idle timer adopts the new duration; a running or paused timer keeps its running duration (`timer/entity.py:304-311`) |
| input_select | Options, name and icon change; the **current option is not changed**, so removing it leaves a state that is not among the options (`input_select/entity.py:122-127`) |
| input_text | Attributes change; the current value is kept even if it no longer fits `min`, `max` or `pattern` (`input_text/entity.py:44-53`, `:114-117`) |
| input_datetime | The stored value is kept; changing `has_date`/`has_time` changes how the state renders (`input_datetime/entity.py:271-274`) |
| input_button | Name and icon only (`input_button/entity.py:72-75`) |
| counter | The state is clamped into the new `minimum`/`maximum`, which can write a new state (`counter/entity.py:93-100`, `:157-161`) |
| schedule | The schedule is recomputed at once, so the on/off state can change immediately (`schedule/entity.py:76-82`) |
| input_number (existing) | The value is clamped into the new range (`input_number/entity.py:124-135`) |

A state change caused this way fires `state_changed` and can trigger
automations. The plan's consequence section names it (§5.8).

## 3. Design rule: the proposal is the stored item

Extend today's `input_number` rule to every new type: **every field Core
fills with a default must be present in the proposal.** Then the approved
`proposed_config` equals what Core stores, readback is an exact comparison,
and the approval hash covers the real result.

- Fields with **no** Core default stay optional. Omitting one clears it, and
  the plan's diff shows the removal against `current_config`.
- A plan that would change a field the caller did not mention is not
  possible, because the caller must state every defaulted field.
- A client needs the current item to copy it, change one field and propose
  the rest unchanged. HAMCP-519's `get_helper_settings` (2.11.0) provides it,
  with `input_text.initial` shown as presence and length only (§4.2).

## 4. Engineering validators (stricter than Core where Core is loose)

Each type gets a closed field set and a validator beside
`_validate_input_number`. Where Core coerces or accepts several input forms,
Engineering accepts only the form Core stores, so readback compares exactly.

| Type | Required | Optional (clearable) | Engineering rules beyond Core |
|---|---|---|---|
| timer | `name`, `duration`, `restore` | `icon` | `duration` must be the canonical stored string `^(0\|[1-9][0-9]{0,4}):[0-5][0-9]:[0-5][0-9]$`; no seconds number, no dict, no fractions, no negatives |
| input_select | `name`, `options` | `initial`, `icon` | 1..N unique non-empty strings (bound N in the build, at least Core's practical limit); `initial`, when present, is a member; exact case |
| input_text | `name`, `min`, `max`, `mode` | `initial`, `icon`, `unit_of_measurement`, `pattern` | ints, `0 <= min <= max <= 255`, `max >= 1`; `initial` length within range; `pattern` must compile under Python `re` (Core stores it unchecked) |
| input_datetime | `name`, `has_date`, `has_time` | `initial`, `icon` | at least one of `has_date`/`has_time`; `initial` must match the stored form for the chosen flags (`YYYY-MM-DD`, `HH:MM:SS` or `YYYY-MM-DD HH:MM:SS`), because Core does not check it for storage |
| input_button | `name` | `icon` | — |
| counter | `name`, `initial`, `minimum`, `maximum`, `restore`, `step` | `icon` | ints (no bools), `minimum`/`maximum` int or `null`; `minimum <= initial <= maximum` when bounded; `step >= 1` |
| schedule | `name`, all seven days | `icon` | the canonical stored form only: each day a list sorted by `from`, `HH:MM:SS` strings, `to` may be `24:00:00`, no overlaps, `from < to`; `data`, when present, is a bounded flat map of the scalar types Core accepts; bounded ranges per day |

All types keep the existing `name` and `icon` checks
(`_validate_required_name`, `_validate_optional_icon`) and the existing
persistence-safety checks on plan content.

### 4.1 input_number `device_class` gap (existing type)

Core stores an optional `device_class` for `input_number`
(`input_number/__init__.py:103-118`), but `_INPUT_NUMBER_FIELDS` lacks it
(`resources.py:112-123`), and governance has no `device_class` handling.
Inference from source, to prove with an offline test: an `input_number` that
has a `device_class` cannot be edited without losing it. A proposal that keeps
it is refused as an unsupported field, and a proposal without it removes it.
The reverse update in an undo plan carries the captured `device_class` and so
fails validation. **Proposal:** add `device_class` as an optional field, and
accept only the closed list of number device classes at the signed Core
version, with Core's unit check mirrored (§9 J-6).

Related Core fact, outside 346's scope: `input_number` drops any stored
`initial` when Core loads its storage (`input_number/__init__.py:208-223`),
so an `initial` written by an update lasts only until the next restart.

### 4.2 input_text `initial` display

`input_text` can be in `password` mode, and HAMCP-519's read view shows its
`initial` as presence and length only (Josh's 2.11.0 decision for 519, as the
architect's GO block reports it). A full-replace edit must resend the current
`initial` to keep it, but a client that sees only presence and length cannot
resend it. **Proposal:**

- `ConfigurationOperation` gains an optional `keep_current_fields` list, closed
  to `["initial"]` and accepted only for `input_text`. At plan time Engineering
  copies the exact current value into the persisted proposal, so the approval
  hash and readback bind it like any other field. The field must exist in the
  current item, or the plan is refused.
- Plan, approval and receipt views render `input_text.initial` as presence and
  length only. The exact value stays inside the persisted proposal.
- The existing persistence-safety checks still refuse known sensitive values.

This adds a second public-schema field (§9 J-5).

## 5. Wiring

### 5.1 Public surface

Widen `helper_type` in `ConfigurationOperation`
(`E/tools/governance.py:188`) to the seven new types, and widen
`SUPPORTED_HELPER_TYPES` and the error text to match. This keeps one plan
tool, one approval flow and one undo path for every helper configuration
edit. This is a **public-schema change** to a protected file (§7, §9 J-3).
The alternative, a separate tool, duplicates plan, approval and undo plumbing
for no safety gain.

The schema stays `update` only for the new types: a `create` operation with
a new helper type is refused with a stable error, so HAMCP-328 later decides
creation on its own.

### 5.2 F3 strategies and capability identities

- One strategy per type on `_HelperConfigurationStrategy`, with
  `id_field = f"{type}_id"` and its closed `configuration_fields`.
- New identities `update_<type>_configuration` for the seven types in
  `CAPABILITY_IDENTITIES` and `CONFIGURATION_REGISTRATIONS`. No
  `create_<type>_configuration` identity is registered; HAMCP-328 adds those.
- Each identity keeps the method-bound shape: exactly one WebSocket
  `{type}/update` command, the argument names `type`, `{type}_id` and the
  proposal's keys, which the closed field set bounds.

### 5.3 Signed profile: a new dormant runtime-only profile

The new identities must **never** map to `core.governed_configuration_operation`.
That profile's signed evidence covered only automation, script,
`input_boolean` and `input_number`, and the route comments say so for each newer
family ("Never governed_configuration_operation", `routes.py:1163-1183`).

**Proposal:** one new runtime-only profile, for example
`core.storage_collection_helper_configuration_operation` (contract id
`compiled-core-storage-collection-helper-configuration-operation-v1`), with:

- a target of one exact existing storage-collection item of a closed helper
  type list;
- dispatch of at most one `{type}/update` per operation, with no reload,
  service call or other command;
- readback that is an exact `{type}/list` match on the stored item;
- recovery that is read-only, with no redispatch.

It is listed in `F3_ENTRY_ADMISSION_PROFILES` and refuses
`pending_signed_registry_admission` until an owner-signed registry sequence
names it for the running Core version. HAMCP-328 helper creates would reuse
the same profile with a create-specific clause, so one signing step can admit
both. Whether to sign it for 2026.11.3 only, or for every 2026.11.x entry, is
Josh's decision (§9 J-4).

### 5.4 Identity and renamed entity ids

Today's helper identity is `<type>.<object_id>` with `object_id` equal to the
storage `id`. A helper whose entity id was renamed in the entity registry
therefore fails closed as "not found", because the new entity id's object id
no longer equals the storage id.

**Proposal for the first build:** keep that fail-closed behavior and state it
in the refusal ("target not found as a storage item; a renamed helper is not
supported yet"). Resolving through the entity registry's `unique_id` (as
`E/governance/created_object_identity.py:208-221` already does for created
objects) is a later, separate change (§9 J-7).

### 5.5 Locks, reload keys and closed type sets

- `resource_lock_key`: the seven types join the `helper` prefix, so a helper
  edit and a HAMCP-145 option set on the same `input_select` serialize.
- Reload keys: `reload:<type>` for each new type, following
  `reload:input_number`, though no reload is sent (the profile forbids it).
- `_DEPENDENCY_HELPER_DOMAINS` is unchanged.
- `migration.py`'s resource-type set and `dispatch_facts._P4_TARGET_TYPES`:
  each new type is added only where its behavior matches the existing
  helper types. The build checks each set and records why it adds or skips a
  type.

### 5.6 Readback

- Normalization drops `id` (as today) and otherwise compares the stored item
  exactly. No float coercion is needed: the new types store strings, ints and
  booleans, and `input_text.min`/`max` and counter fields are ints in Core.
- Verification is an exact match with the normalized proposal. Any
  difference, including a value Core coerced, fails verification and is
  reported. Engineering does not retry.

### 5.7 Undo

Undo reuses `rollback_change` unchanged in shape: a reverse `update` per
verified operation from its captured `current_config`, as a new plan with
its own approval, excluded when the helper changed again after the source
plan. Two points the build proves **before** extending it:

1. The captured `current_config` is the raw `{type}/list` item, which
   includes Core's `id` key (`resources.py:771-792`). The build must show
   whether today's reverse path strips `id` or refuses it, for the existing
   `input_boolean`/`input_number` types first. This design did not check it.
2. The reverse proposal must pass the new type's validator. Because the
   validators accept exactly the stored forms (§4), a captured stored item
   passes unless it holds a field Engineering does not know, such as an
   `input_number.device_class` (§4.1). Such an undo fails closed with the
   unsupported field named.

### 5.8 Risk and consequence disclosure

- Risk keeps the existing configuration-update classification. No new risk
  lowering is proposed.
- The plan states the §2.3 effect for its type, for example:
  - "removing the current option leaves `input_select.example_mode` on a
    value that is no longer an option";
  - "the counter will be clamped from 12 to 10";
  - "the schedule may switch on or off immediately".
- Where a predicted state change is computable from the current state (counter
  clamp, input_select option removal, input_number clamp), the plan states
  the predicted value. Otherwise it states that the state may change.
- Today's helper updates list no consumers of the helper's entity id (none
  found on the `create_configuration_plan` path, `service.py:5914`). The design
  adds none: an edit keeps the entity id. An automation that selects a removed
  `input_select` option is the one case where a consumer list would help; it
  is left for a later change.

## 6. Increments

| Increment | Types | Why |
|---|---|---|
| 1 | timer, input_select, input_text, input_datetime, input_button, counter, plus the `input_number.device_class` fix | Flat schemas. Covers E034 (timer icons) and the F066 `initial` clears |
| 2 | schedule | Nested day lists, canonical-form validation and an immediate state recompute. Larger test surface |

## 7. Files a build would touch

Protected (each needs Josh's named OK at build time):

- `E/governance/resources.py`: field sets, validators, identity,
  normalization.
- `E/governance/service.py`: `SUPPORTED_HELPER_TYPES`, error text,
  disclosures.
- `E/tools/governance.py`: the public `helper_type` Literal and, with J-5,
  `keep_current_fields` (public schema).
- `E/f3_configuration/strategies.py`: strategies, identities, strategy map.
- `E/f3_configuration/locks.py`: lock prefix and reload keys.
- `E/f3_configuration/migration.py` and `E/f3_runtime/dispatch_facts.py`:
  only if §5.5 shows a type belongs there.
- `E/f3_configuration/gateway.py`: only if update dispatch needs a per-type
  response check.
- `E/f3_runtime/registry.py`: `CONFIGURATION_REGISTRATIONS`.
- `E/f3_runtime/runtime.py`: only if §5.7 item 1 needs a fix.
- `E/ha_core_readmission/profiles.py`, `E/ha_core_readmission/routes.py`:
  the new dormant profile and its routes.
- `E/capabilities.py`: capability metadata for the new types.

Not protected: `tests/`, `docs/`.

No workflow, `.release/`, `config.yaml`, `Dockerfile` or signed-registry
change belongs to the build. Signing is a separate owner session.

## 8. Test plan (offline, synthetic fixtures only)

Success:

- Per type: plan → approve → apply → exact readback.
- An icon-only edit on a timer, the E034 case.
- Clearing `initial` on `input_select`, `input_text` and `input_boolean`. The
  last proves today's path (§2.1).
- Undo of each type restores the captured item exactly.

Refusal and negative reachability:

- A missing defaulted field is refused with the field named.
- Unknown fields, non-canonical `duration`, a non-member `initial`, an
  out-of-range `input_text.initial`, an invalid `input_datetime.initial` for
  its flags, a non-canonical, unsorted or overlapping schedule, and bool-typed
  ints are each refused.
- `create` with a new helper type is refused.
- A YAML-only or renamed helper fails closed as not found, with no dispatch.
- Duplicate or identity-mismatched list items fail closed.
- While the new profile is unadmitted, every new identity refuses
  `pending_signed_registry_admission` and reads nothing.
- No reload, service call or second command is ever sent. A fake transport
  asserts the exact one-command sequence.
- A readback mismatch (Core coerced a value) fails verification without a
  retry.
- An undo after a later outside edit excludes the operation. An undo with an
  unknown captured field fails closed.
- If J-5 is accepted: `input_text.initial` never appears in plan, approval or
  receipt text, only presence and length. `keep_current_fields` is refused for
  other types or fields, and when the current item lacks the field. A kept
  value equals the current value exactly.

Compatibility:

- `input_boolean` and `input_number` plans keep their exact current shapes,
  fingerprints and identities.
- `core.governed_configuration_operation` is byte-identical.
- Existing signed-profile fingerprints are unchanged.

## 9. Owner decisions requested (Josh) — open

Recommendations are the builder's and are not decisions.

| # | Question | Builder recommendation | Decision |
|---|---|---|---|
| J-1 | Build HAMCP-346, and in which release? | Build increment 1 in the release Josh picks, after 2.11.0 | [JOSH] pending |
| J-2 | Increments as in §6? | Yes: schedule in increment 2 | [JOSH] pending |
| J-3 | Widen `create_configuration_plan`'s `helper_type` (public schema) rather than add a tool? | Widen | [JOSH] pending |
| J-4 | New dormant runtime-only profile (§5.3), shared with HAMCP-328; which Core entries to sign it for, and when? | One profile; sign it in the same owner session as HAMCP-328's creates, unless edits are wanted sooner | [JOSH] pending |
| J-5 | `input_text.initial`: `keep_current_fields` (public schema) plus presence-and-length rendering in plan, approval and receipt views (§4.2)? | Yes, consistent with 519 | [JOSH] pending |
| J-6 | Fix the `input_number.device_class` gap (§4.1) in increment 1? | Yes: optional field, closed list of number device classes at the signed Core version | [JOSH] pending |
| J-7 | Renamed helpers: fail closed now, with `unique_id` resolution later? | Yes | [JOSH] pending |

## 10. Non-actions

- No source, schema, profile, registry, workflow or release change.
- No live Home Assistant or deployed MCP access. Core facts come from public
  source at tag 2026.11.3.
- No house identifiers. F066 is cited by counts only.
- No signing, Ready, merge, dispatch or deployment.
