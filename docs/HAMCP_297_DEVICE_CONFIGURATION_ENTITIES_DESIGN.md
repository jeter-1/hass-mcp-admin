# HAMCP-297: Governed Device-Configuration Entities (2.6.0 Design)

Status: **design only; Codex design review 2026-11-08 corrections applied
(§14); remaining owner choices in §13.** This document changes no runtime
code, schema, tool registration, policy, workflow, signed registry, release
declaration or version. It is the one new file in its pull request.

- Register: HAMCP-297.
- Target release line: 2.6.0 (`release/2.6.0`).
- Base: `origin/release/2.6.0` `7246f2d21693f039504542574569f03b71476489`
  (HAMCP-331, Engineering `2.4.2-beta.7`). Every `E/` line number below is at
  this base.
- Pinned Home Assistant Core: `2026.11.0`, commit
  `74cf5a49c63ed8d4d87717844bf14f79c28c9cea` (tree
  `530241f5976abdfbea2fa1fa51e0ddc7a4d42560`, archive sha256
  `85f3c42c9f77aa45e01faff4471007beadf5cb50c4324f496a239139eec70ff1`). The
  binding is in `scripts/core_lane_bindings.py:62` and
  [`evidence/hamcp315/core-sources/2026.11.0.json`](evidence/hamcp315/core-sources/2026.11.0.json).
  The 2026-11-08 corrections re-read the cited Core files from that exact
  archive after recomputing its digest locally (ADR-021); they are local
  pinned-source reads, not a fresh remote observation.

Engineering paths are relative to `hass_mcp_engineering_beta/ha_mcp_engineering/`
(written `E/`). Upstream citations are read-only public source at the pinned
commit (ADR-021). In the text, `core:<path>:<lines>` means
`https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/<path>#L<lines>`.
§10 lists the full links. Upstream integration examples (Tuya) show that a
kind of entity exists at the pinned Core. They are not evidence about Josh's
devices.

Nothing here came from a live Home Assistant instance or a deployed MCP
endpoint. No household device, entity or setting was probed. Which household
integrations and entities carry the three approved use cases is unobserved
evidence (§13 E1), not an open product question.

## 1. Register outcome and binding decisions

Register Outcome (HAMCP-297, verbatim):

> Configuration-category entities on devices (for example power-on behavior and
> child lock on plugs) can be changed through a typed, bounded, governed path
> with exact targets, stale-state protection, readback and rollback, instead of
> by hand.

| # | Decision or constraint | Source | Status |
|---|---|---|---|
| B1 | Feature lane position 3, after HAMCP-253, 267 and 264 | Register Next Action (recorded there as the 2026-11-03 ranking) | Register record |
| B2 | The path is typed, bounded and governed, with exact targets, stale-state protection, readback and rollback | Register Outcome | Register record |
| B3 | Household use cases (build and acceptance targets): **power-on behavior after outages, child/physical lock, sensor tuning (sensitivity, timeouts). Not indicator LEDs.** | Register | **Josh direction (register 2026-11-07; confirmed in-session 2026-11-08)** |
| B4 | **Additive profiles only.** No existing signed profile is version-bumped or replaced; existing fingerprints stay byte-identical | Register | **Josh direction (register 2026-11-07; confirmed in-session 2026-11-08)** |
| B5 | New 2.6 profiles are signed for **Core 2026.11.0 only**, in **one combined 2.6 signing** (one `extend-capabilities` extension of the existing 2026.11.0 entry). No 10.x targets, no separate or phased 2.6 signing | Register | **Josh direction (register 2026-11-07; confirmed in-session 2026-11-08)** |
| B6 | Design document only, one new file, no runtime or protected change | Task prompt and 2.6.0 brief | Task constraint |
| B7 | Mechanism keys on `entity_category == "config"` entities of typed domains | Task prompt | Task constraint, refined in §3.1 |

Everything else in this document is a design proposal, not a recorded decision.
Anything still unresolved is listed in §13 as pending Josh.

## 2. Current state at the base

### 2.1 No path exists for select, number or button configuration entities

- `call_service` is the only generic service path. It is refused:
  `StandardHaMcpGateway.available` is always `False`
  (`E/providers/standard_mcp.py:21-50`). The capability entry is
  `status: deprecated`, `operation_class`/`enforcement: provider_unavailable`,
  `fallback: none`, `direct_write_allowed: False` (`E/capabilities.py:65-70`).
  The router audits it as `refusal_reason: "provider_unavailable"`
  (`E/routing.py:1336-1339`), and it is in `PREVALIDATION_ENFORCEMENT_TOOLS`
  (`E/providers/routing.py:235-243`), so it is refused by name before argument
  coercion.
- `control_helper` (HAMCP-311) covers the helper domains only: counter,
  input_boolean, input_button, input_datetime, input_number, input_select,
  input_text and timer (the `helper_value` family in
  `CORE_TYPED_OPERATION_PROFILES`, `E/ha_core_readmission/profiles.py:427-462`).
  It does not cover `select`, `switch` or `number`.
- `create_input_select_option_plan` (HAMCP-145) is `input_select` only
  (`E/ha_core_readmission/routes.py:175-179`;
  `E/governance/input_select_option_planning.py:19`).

The result: a device's `select.*` or `number.*` configuration entity (for
example Tuya "Power-on behavior" or a presence-sensor "Sensitivity") cannot be
changed through Engineering at all today.

### 2.2 Configuration switches are reachable through `control_power` with no category awareness

- `control_power` accepts any `^(light|switch)\.[a-z0-9_]{1,120}$` entity
  (`E/power/contracts.py:20`, `E/tools/power.py:11-52`).
- `checked_state` checks only entity_id identity, an `on`/`off` state and a
  `last_updated` stamp (`E/power/contracts.py:59-67`). It reads no registry
  entry.
- The upstream adapter uses only ha-mcp `ha_get_state`, `ha_list_services` and
  `ha_call_service` (`E/providers/upstream_power.py`). It has no registry read.
- A repository-wide search for `entity_category` under `E/` finds one hit,
  `E/governance/entity_availability_exposure.py:180`, which is unrelated
  (exposure metadata).

So a configuration-category switch such as Tuya "Child lock"
(`core:components/tuya/switch.py:100-104`) is, today, a fast `control_power`
target. There is no plan, no external approval, no prior-value capture and no
rollback. `control_power` is signed under **`core.typed_power_operation`**
(generated as `"core.typed_" + family + "_operation"` with family `power`,
`E/ha_core_readmission/profiles.py:427-451`): `light`/`switch`,
`turn_on`/`turn_off`, provider boundary `reviewed_ha_mcp_typed_power`,
fingerprint
`sha256:9242b795a2d5ed3deeb3b8ea46fba13fc299c14fb274e3b29ed97a30b607001a`.
Its docstring already warns "a switch may power a critical load"
(`E/tools/power.py:29`). How this design treats that path is §5.10 and
§13 Q3.

### 2.3 Execution locks `control_power` already takes

- `PowerAdapter` subclasses `FanAdapter` (`E/power/adapter.py:41`) and inherits
  `FanAdapter.lock_requests` (`E/fan/adapter.py:85-92`): an **exclusive
  `entity:<entity_id>`** resource lock, a shared `home_assistant:core` lock and
  a shared ha-mcp provider lock.
- `PowerService` subclasses `FanService` (`E/power/service.py:26-27`). Its
  records live under their own namespace, but its locks use
  `FanLockStore(root, ...)` on the **un-namespaced** root
  (`E/fan/service.py:107-114`, "Exact shared lock namespace"). The root is
  `settings.governance_path` (`E/power/service.py:54`).
- `DurableLockStore` keeps every lock under `<root>/f3-operation-locks-v1`
  (`E/f3/locks.py:41`, `:163-192`). The F3 runtime's `RuntimeLockStore` uses
  the same `storage_root` (`E/f3_runtime/runtime.py:1941-1946`), which is
  `settings.governance_path` (`E/governance/runtime.py:262`). Power, fan and
  governed F3 plans therefore already share one durable lock store. A key
  requested by a governed plan conflicts with the same key held by
  `control_power`.
- After an unresolved send, the power/fan path promotes its lock set to a
  selective conflict hold that retains only `entity:<entity_id>`
  (`E/fan/locks.py:14-21`, reason `manual_review_unresolved_dispatch` at
  `:52-53`).
- Governed configuration keys come from `resource_lock_key`
  (`E/f3_configuration/locks.py:37-71`). HAMCP-145 `input_select` maps to the
  `helper:` prefix (`:44-46`); registry metadata, availability and exposure
  plans share `entity_registry:<entity_id>` (`:47-53`; the target id is the
  entity_id, `E/governance/entity_metadata_planning.py:410-411`). Neither
  prefix conflicts with `entity:`.

### 2.4 Counts at the base

- Static tools: 81 (`ENGINEERING_STATIC_TOOL_COUNT`,
  `E/tools/registry.py:239-240`, measured by importing the base).
- Compiled runtime Core profiles: 32 (`CORE_RUNTIME_CAPABILITY_PROFILES`,
  `E/ha_core_readmission/profiles.py` about `:893-900`).
- Signed registry (canonical on the `registry` branch): seq 17, generated
  2026-11-07T06:16:34Z. Profile counts per release: 2026.10.0 has 22, 2026.10.1
  has 26, 2026.10.3 has 31, 2026.11.0 has 32. Seq 17 added
  `core.typed_helper_value_operation` (`sha256:d45ea23b…228d`) to 2026.11.0.
  The `main` copy is frozen at seq 15.

These are base-local numbers. They are not 2.6.0 release totals (§7).

### 2.5 The shape template

The closest shipped precedent is the HAMCP-145 governed input_select option
plan. It has:

- one exact entity, closed input validated before any read
  (`E/governance/input_select_option_planning.py:46-81`);
- a direct REST state read with a strict response check
  (`E/governance/input_select_option.py`, `InputSelectGateway.read_state`);
- a pre-dispatch callback, then `mark_provider_send()` at the exact send
  boundary, then one WebSocket `call_service`. Errors after send are
  `provider_rejected` or `dispatch_indeterminate`, never "unchanged"
  (`InputSelectGateway.select_option`);
- its own signed profile `core.typed_input_select_option_operation`
  (`E/ha_core_readmission/profiles.py:683-711`, adapter
  `compiled-core-typed-input-select-option-operation-v1`, provider boundary
  `direct_ha_api`, a never-sent list, fingerprint `sha256:e2240555…e38d`);
- the house refusal `pending_signed_registry_admission` before any HA I/O until
  the profile is signed.

Two HAMCP-145 elements are **not** copied, because they are too weak for this
family: its `helper:` lock identity (§5.7), and its rollback rule, which
compares only the current state with the applied option
(`E/governance/service.py:4790-4801`). §5.11 binds identity and the exact
post-apply observation instead.

[`HAMCP_232_145_HELPER_OPTIONS_AND_SELECT_DESIGN.md`](HAMCP_232_145_HELPER_OPTIONS_AND_SELECT_DESIGN.md)
is the house-style design precedent.

## 3. Upstream facts at the pinned Core

### 3.1 What `entity_category == "config"` is, and who sets it

- `EntityCategory` is a `StrEnum` with `CONFIG = "config"` ("allows changing
  the configuration of a device") and `DIAGNOSTIC = "diagnostic"`
  (`core:const.py:1026-1039`). Its docstring says category entities are not
  exposed to cloud, Alexa or Google Assistant, and are not included in
  indirect service calls to devices or areas.
- The integration sets it: `Entity.entity_category` / `_attr_entity_category`
  (`core:helpers/entity.py:292`, `:621`, `:970-975`). The platform passes it into
  the registry on registration
  (`core:helpers/entity_platform.py:1141-1163`), and re-registration updates it
  (`core:helpers/entity_registry.py:1610-1690`).
- Users and API clients cannot change it. The `config/entity_registry/update`
  schema has no `entity_category` key
  (`core:components/config/entity_registry.py:153-196`). It is integration-owned.
  (An MQTT discovery payload can set it, because that payload is the
  integration's input.)
- The registry entry carries `entity_category` (`core:helpers/entity_registry.py:282`).
  `as_partial_dict` serializes it with `config_entry_id`, `created_at`,
  `device_id`, `disabled_by`, `id`, `modified_at`, `options`, `platform`,
  `translation_key` and `unique_id`; `extended_dict` adds `capabilities`,
  `device_class` and `original_device_class`
  (`core:helpers/entity_registry.py:393-440`).
- Indirect targeting skips category entities: `primary_entities_only` excludes
  them from device, area and floor expansion. Direct label-to-entity expansion
  is not affected (`core:helpers/target.py:226-229`, `:369-372`).
- `sensor` refuses the config category outright
  (`core:components/sensor/entity.py:234-238`).
- `update` defaults to config and sets it when INSTALL is supported
  (`core:components/update/entity.py:33`, `:128-140`). Its services are firmware
  install, skip and clear-skipped.

Consequence for this design: "config category" is an integration-owned,
registry-readable fact. It cannot be forged by a registry update through the
API. It covers more than settings (for example firmware updates). So the
domain and service allowlist, not the category alone, bounds the slice. The
category also says nothing about **where** the setting is stored (§5.6).

### 3.2 Registry reads

- `config/entity_registry/get` takes one exact `entity_id`. It returns
  `extended_dict`, or the error `not_found` "Entity not found"
  (`core:components/config/entity_registry.py:95-121`).
- `config/device_registry/list` returns every device's cached JSON, including
  `id`, `created_at`, `identifiers`, `config_entries`, `primary_config_entry`,
  `disabled_by` and `entry_type`
  (`core:components/config/device_registry.py:74-100`;
  `core:helpers/device_registry.py:755-795`).

### 3.3 Service calls per domain

WebSocket `call_service` (`core:components/websocket_api/commands.py:316-377`)
takes `domain`, `service`, `target`, `service_data` and `return_response`, and
calls `hass.services.async_call(..., blocking=True)`. Errors map to
`not_found` (ServiceNotFound), `invalid_format` (schema) and
ServiceValidationError (about `:373`).

| Domain | Services registered | Data | Readback | Source |
|---|---|---|---|---|
| `select` | `select_first`, `select_last`, `select_next`, `select_option`, `select_previous` | `select_option`: required `option` (`cv.string`); next/previous: optional `cycle` | `state` is `current_option`, or `None` (unknown) if not in `options`. `options` is a capability attribute. An invalid option raises ServiceValidationError `not_valid_option` | `core:components/select/services.py:16-44`; `select/const.py:61-86`; `select/entity.py:38-54`, `:85-106`, `:116-172` |
| `switch` | `turn_off`, `turn_on`, `toggle` | none | `state` is `on`/`off` (ToggleEntity) | `core:components/switch/services.py:13-19`; `switch/const.py:85-90` |
| `number` | `set_value` | required `value` (`Coerce(float)`) | `state` is the value in the display unit (§3.5) | `core:components/number/services.py:16-49`; `number/entity.py:145-163`, `:184-252`, `:265-272`, `:362-447` |
| `button` | `press` | none | `state` is the last-pressed timestamp, written **before** `async_press` runs | `core:components/button/services.py:11-15`; `button/entity.py:54-74`; `button/const.py:21-26` |
| `update` | `install`, `skip`, `clear_skipped` | install: version, backup | firmware state | `core:components/update/services.py`; `update/entity.py:33`, `:128-140` |

### 3.4 In-tree examples of the three approved use cases

Upstream examples only. They show the entity kind each use case takes at the
pinned Core; they do not show which integration or device the household uses.

| Use case (B3) | Entity kind at Core 2026.11.0 | Upstream example |
|---|---|---|
| Power-on behavior after outages | `select` | Tuya "Power-on behavior": `translation_key="relay_status"`, `entity_category=EntityCategory.CONFIG` (`core:components/tuya/select.py:83-86`, `:228-231`, `:259-262`). Options include `last`/`memory` ("Previous state"), `on`/`power_on` and `off`/`power_off` (Tuya `strings.json`) |
| Child/physical lock | `switch` | Tuya "Child lock": `translation_key="child_lock"`, `entity_category=EntityCategory.CONFIG` (`core:components/tuya/switch.py:100-104`, `:149-153`, `:202-205`) |
| Sensor tuning: numeric sensitivity | `number` | Tuya presence sensor (`DeviceCategory.HPS`) "Sensitivity": `translation_key="sensitivity"`, `entity_category=EntityCategory.CONFIG`, no device class (`core:components/tuya/number.py:161-166`); also `DeviceCategory.ZD` (`:503-508`) |
| Sensor tuning: enumerated sensitivity | `select` | Tuya "Motion sensitivity": `translation_key="motion_sensitivity"`, `entity_category=EntityCategory.CONFIG` (`core:components/tuya/select.py:119-122`, `:361-364`) |
| Sensor tuning: timeouts and delays | `number` | Tuya alarm (`DeviceCategory.MAL`) "Arm delay" and "Alarm delay": `device_class=NumberDeviceClass.DURATION`, `entity_category=EntityCategory.CONFIG` (`core:components/tuya/number.py:227-240`) |

Tuya number entities take `native_min_value`, `native_max_value` and
`native_step` from the device definition (`core:components/tuya/number.py:577-579`).
ZHA takes the category from the `zha` library
(`core:components/zha/select.py:47-65`). MQTT takes it from the discovery
configuration.

Consequence: the three approved use cases need **`select`, `switch` and
`number`**. `number` is therefore in the first slice (§4), with the numeric
contract in §5.5.1 inside the one new profile, before its first signature.

### 3.5 `number` semantics that decide exactness

- Capability attributes are `min`, `max`, `step` and `mode`
  (`core:components/number/const.py:101-107`;
  `core:components/number/entity.py:145-163`). `min` is the native minimum
  converted to the display unit with `floor_decimal`, `max` with
  `ceil_decimal` (`:184-222`).
- `step` is `native_step` when the integration sets it; otherwise a default
  of 1.0 divided by 10 until it is smaller than the range
  (`core:components/number/entity.py:224-252`; `DEFAULT_STEP = 1.0`,
  `number/const.py:93-95`).
- `state` is `value`: the native value converted to the display unit and
  rounded with `round` to the native value's printed precision
  (`core:components/number/entity.py:265-272`, `:362-372`, `:392-424`).
- The service rejects values outside the **display** `min`/`max`
  (`out_of_range`), converts the value to the native unit, **clamps** to the
  native range, and does **not** enforce `step`
  (`core:components/number/services.py:16-39`).
- Conversion is the identity unless the device class is in
  `UNIT_CONVERTERS` (`core:components/number/const.py:651` onward; it
  includes `duration` and `temperature`) **and** the display unit differs from
  the native unit (`core:components/number/entity.py:392-447`). The display
  unit differs when the registry entry has a user-set
  `options["number"]["unit_of_measurement"]` (`:449-466`), when a
  `temperature` entity's native unit differs from the system unit
  (`:326-340`), or when a translated unit applies (`:342-356`).
- NumberEntity takes its device class from the integration
  (`_attr_device_class` or the entity description,
  `core:components/number/entity.py:174-182`), which the registry records as
  `original_device_class`. The registry's user `device_class` override is not
  read by `NumberEntity.device_class`.

So a numeric setting is exact only when Engineering proves the conversion is
the identity, checks bounds and step itself, and compares the readback
numerically. §5.5.1 makes that the contract.

## 4. Bounded first slice

| Domain / service | First slice? | Why (from source) |
|---|---|---|
| `select.select_option` | **Yes** | Absolute, idempotent target value; options are enumerated in state; readback is exact (`state == option`); the prior option, if still offered, is a valid rollback target. Covers power-on behavior and enumerated sensitivity |
| `select.select_first/last/next/previous` | **No, never sent** | Relative moves; the outcome depends on the current state and on option order |
| `switch.turn_on` / `switch.turn_off` | **Yes** | Absolute, idempotent; readback is exact (`on`/`off`); the prior state is the rollback target. Covers child/physical lock |
| `switch.toggle` | **No, never sent** | Relative; the outcome depends on the current state |
| `number.set_value` | **Yes, under the exactness contract in §5.5.1** | Required for numeric sensitivity, timeouts and delays (§3.4). Core clamps, may convert units and does not enforce `step` (§3.5), so Engineering proves identity conversion, enforces bounds and step before sending, and compares readback numerically |
| `button.press` | **No** | An action, not a setting. State is written before the press, so readback cannot prove the effect, and there is no prior value to roll back to (`core:components/button/entity.py:54-74`). `identify`/`restart`/`update` device classes are actions (`button/const.py:21-26`) |
| `update.*` | **No, never sent** | Firmware install. Config category by default but not a setting |
| `light` (indicator LEDs and similar) | **No** | Indicator LEDs are excluded by B3 |
| `text`, `time`, others | **No** | Not one of the three approved use cases. A later increment needs its own readback analysis and its own additive profile (B4) |

The first slice is **`select.select_option`, `switch.turn_on`/`switch.turn_off`
and `number.set_value` on registry-proven config-category device entities.**
It covers all three approved use cases at the entity-kind level. Whether each
household entity satisfies the contract (for example a timeout whose
conversion cannot be proven identity is refused) depends on household
evidence (§13 E1).

## 5. Proposed design

### 5.1 Tool and plan family

- New governed plan tool: `create_device_configuration_plan` (provider
  capability `OPERATIONAL_ADMINISTRATION`, alongside
  `create_input_select_option_plan` at `E/providers/routing.py:209`).
- New F3 identity: `("set_exact_device_configuration_value",
  "set_device_configuration_value")`, in a new routes table
  `_F3_DEVICE_CONFIGURATION_OPERATIONS` with `DEVICE_CONFIGURATION_PROFILE` and
  `DEVICE_CONFIGURATION_TOOL` constants, modeled on
  `E/ha_core_readmission/routes.py:175-179`. It is **not** added to
  `_F3_TYPED_HELPER_OPERATIONS` (`:31-33`) or to
  `_F3_GOVERNED_CONFIGURATION_OPERATIONS` (`:34-63`).
- New configuration resource type `device_configuration_entity` (§5.7 lock
  identity).
- Apply and rollback use the existing governance tools
  (`approve_change_plan`, `apply_change_plan`, `rollback_change`). No new
  apply tool.
- The fast typed controls (`control_power`, `control_fan`, `control_helper`)
  are unchanged by this design. §5.10 covers the `control_power` interaction.

### 5.2 Inputs and outputs

Closed input, validated before any Home Assistant read (refusal
`device_configuration_invalid_value` or `device_configuration_field_not_supported`):

| Field | Rule |
|---|---|
| `entity_id` | Required. `^(select|switch|number)\.[a-z0-9_]{1,120}$` |
| `value` | Required string. For `select`: 1–255 characters, no control characters, compared exactly (case-sensitive, never stripped), as in `input_select_option_planning.py:62-70`. For `switch`: exactly `on` or `off`. For `number`: a canonical decimal string matching `^-?(0|[1-9][0-9]{0,8})(\.[0-9]{1,6})?$` (no exponent, sign only on non-zero values, no `NaN`/`Infinity`), which must round-trip exactly through an IEEE-754 double (§5.5.1) |
| `reason` | Required, 1–500 characters after strip |
| `expiration_minutes` | Optional integer 5–1440, default 120 |

One entity per plan in the first slice. Multi-entity plans are an open
question (§13 Q5). No other field is accepted: no `service`, `domain`,
`service_data`, `target` beyond the entity, `cycle`, `option` alias or unit.

Plan output adds, beyond the standard plan envelope:

- `target`: entity_id, domain, the target identity of §5.11.1 (registry `id`,
  `created_at`, `platform`, `unique_id`, `config_entry_id`, `device_id`,
  device `id`/`created_at`/`identifiers`/`primary_config_entry`), device name
  (bounded), `entity_category: "config"`;
- `baseline`: prior value (`state`), `last_changed`, `last_updated`, context
  id, registry `modified_at`; for `select` the bounded `options` list; for
  `number` `min`, `max`, `step`, `mode`, `unit_of_measurement`,
  `original_device_class` and the identity-conversion proof (§5.5.1);
- `requested_value` and the exact service it maps to (`select.select_option`
  with `{"option": value}`, `switch.turn_on`/`switch.turn_off` with no data,
  or `number.set_value` with `{"value": <number>}`);
- `consequences`: the disclosure in §5.6;
- `rollback`: "separately approved plan restoring `<prior value>`, available
  only after a verified apply whose post-apply observation is still exact
  (§5.11)".

### 5.3 Routing and provider boundary

- Provider: direct Home Assistant API under a new provider contract
  `direct-ha-exact-device-configuration-v1`, matching the HAMCP-145
  `direct-ha-exact-input-select-option-v1` pattern. No ha-mcp delegation, no
  `call_service` reuse and no fallback. If the direct gateway is unavailable,
  the plan fails closed.
- Admission: the route requires the new signed profile (§6). Until the running
  Core release's signed registry entry covers it, create and apply refuse with
  `pending_signed_registry_admission` before any HA I/O, as HAMCP-145 does.
  Only Core 2026.11.0 will be extended (B5); every other Core release keeps
  refusing.
- Core independence: none. This is a Core-dependent F3 route, not a
  `CoreIndependence` entry like `control_power` (`routes.py:478-492`).

### 5.4 Core commands

The complete set of Core commands the new gateway may send:

| Step | Command | Upstream |
|---|---|---|
| Target proof | WS `config/entity_registry/get` `{entity_id}` | `core:components/config/entity_registry.py:95-121` |
| Device proof | WS `config/device_registry/list` | `core:components/config/device_registry.py:74-100` |
| State read and readback | REST `GET /api/states/{entity_id}` | as used by `InputSelectGateway.read_state` |
| Dispatch (select) | WS `call_service` `{domain: "select", service: "select_option", service_data: {option}, target: {entity_id}}` | `core:components/select/services.py:34-38`; `websocket_api/commands.py:316-377` |
| Dispatch (switch) | WS `call_service` `{domain: "switch", service: "turn_on"|"turn_off", target: {entity_id}}`, no `service_data` | `core:components/switch/services.py:13-18` |
| Dispatch (number) | WS `call_service` `{domain: "number", service: "set_value", service_data: {value: <JSON number>}, target: {entity_id}}` | `core:components/number/services.py:42-49` |

Never sent (the profile's never-sent list): `select.select_first`,
`select.select_last`, `select.select_next`, `select.select_previous`,
`switch.toggle`, any `number` service data key other than `value`,
`button.press`, `update.*`, `light.*`, `config/entity_registry/update`,
`config/device_registry/update`, any `return_response: true`, and any
`call_service` whose target is not exactly the one planned `entity_id` (no
`device_id`, `area_id`, `floor_id` or `label_id` targets).

### 5.5 Target proof

At plan creation, and again inside the apply authority window while the
execution locks of §5.7 are held, the gateway requires all of the following.
Any failure refuses before dispatch.

1. `config/entity_registry/get` returns an entry whose `entity_id` equals the
   request. `not_found` refuses `device_configuration_entity_not_registered`.
2. `entity_category == "config"`. `diagnostic` or `null` refuses
   `device_configuration_not_config_category`. A `null` category switch is the
   `control_power` domain. A diagnostic entity is read-only by intent.
3. `device_id` is a non-empty string, and that id is present in
   `config/device_registry/list`. Otherwise refuse
   `device_configuration_no_device`. This excludes helpers, template entities
   and other device-less entities.
4. Entity `disabled_by` is `null` and the device's `disabled_by` is `null`.
   Otherwise refuse `device_configuration_target_disabled`.
5. The entity domain is exactly `select`, `switch` or `number`, matching the
   input pattern. The registry `platform` and `translation_key` are recorded.
   Platform is not allow-listed in the first slice (§13 E1 may justify
   narrowing it).
6. The state read returns the same `entity_id` and a **valid, restorable
   baseline**. Absence of `unavailable`/`unknown` is not enough:
   - `switch`: state is exactly `on` or `off`;
   - `select`: a bounded, duplicate-free `options` list (1–256 strings of at
     most 255 characters) and a state that is a member of that list;
   - `number`: §5.5.1 rules 1–3 hold and the state is a finite decimal that
     is within `min`/`max` and aligned to `step` (§5.5.1 rule 4).
   `unavailable`/`unknown` refuses `device_configuration_entity_unavailable`.
   Any other non-restorable baseline (malformed state, a `select` state not in
   its options, a `number` state out of bounds or off-step) refuses
   `device_configuration_baseline_not_restorable`, because B2 requires
   rollback and there would be no valid value to restore.
7. The requested value is valid for the domain: a `select` value in
   `options` (else `device_configuration_value_not_offered`); a `number`
   value passing §5.5.1 rules 4–5 (else the refusal named there).
8. The requested value differs from the current state (numerically for
   `number`). Equal values refuse `device_configuration_no_change`, so no plan
   is created for a no-op.

The plan records the target identity of §5.11.1 and the baseline fingerprint
of §5.7. A later rename of the entity_id, a re-registration under a different
registry id, or a replaced device therefore breaks the binding and is caught
as stale.

#### 5.5.1 Numeric exactness contract (`number` only)

These rules are part of the one new profile's response contract (§6), fixed
before its first signature.

1. **Bounds attributes.** The state's `min`, `max` and `step` are finite JSON
   numbers with `min < max` and `step > 0`. Each is read as
   `Decimal(repr(float))` (shortest round-trip form). The registry entry's
   `capabilities` must carry the same `min`, `max` and `step`; a disagreement
   refuses `device_configuration_number_bounds_inconsistent`.
2. **Identity-conversion proof.** Let `dc` be the registry
   `original_device_class` (§3.5). The plan refuses
   `device_configuration_number_unit_conversion` when `dc` is in the pinned
   Core 2026.11.0 `UNIT_CONVERTERS` set (`core:components/number/const.py:651`
   onward, embedded verbatim in the profile) **and** either the registry
   `options` carries `number.unit_of_measurement`, or `dc` is `temperature`.
   Otherwise Core's conversion in both directions is the identity
   (`core:components/number/entity.py:392-447`). The state's
   `unit_of_measurement` (or its absence), `dc` and the registry `number`
   options are recorded in the baseline fingerprint. Residual: an integration
   that supplies a translated display unit instead of a native unit
   (`core:components/number/entity.py:342-356`) is not distinguishable from
   the registry or state reads. The lane fixture (§9.2) must include a
   converter-class number with a translated unit; if 2026.11.0 does not
   behave as identity for it, the build must refuse converter-class numbers
   it cannot prove before the profile is frozen.
3. **No ambiguous unit.** A `number` that reports `assumed_state: true` is
   allowed only with the disclosure of §5.6; it is not refused.
4. **Range and step.** With `v = Decimal(value)`: `min <= v <= max`, else
   `device_configuration_number_out_of_range`; `(v - min) / step` is an exact
   integer in Decimal arithmetic (no tolerance), else
   `device_configuration_number_step_misaligned`. Engineering never relies on
   Core's `out_of_range` check and never sends a value Core would clamp.
5. **Representation.** `Decimal(repr(float(v))) == v`, else
   `device_configuration_invalid_value`. The `value` service field is the JSON
   number `float(v)`.
6. **Readback.** The state must parse as a finite decimal; `verified` only
   when `Decimal(state) == v` and the recorded unit, `min`, `max` and `step`
   are unchanged. Any integration rounding, quantization, Core clamping or
   unit change shows up as `verification_mismatch`, never `verified`.
7. **Rounding and clamping are never silent.** Engineering never rounds,
   snaps or clamps the requested value. A value that needs rounding to fit is
   refused at plan creation (rules 4–5).
8. **Undo.** The prior value is restorable only if it passes rules 4–5
   against the attributes current at rollback creation and dispatch.

### 5.6 Approval and risk disclosure

Every plan needs exact-hash external approval. This family has no fast path
and no model-conveyed confirmation.

The plan's `consequences` field always includes:

- **Persistence (qualified):** a configuration-category entity usually
  represents a setting held by the device or its integration, so it is often
  not undone by a Home Assistant restart and may survive the device leaving
  Home Assistant. The category alone does not prove this (§3.1). Whether a
  given setting is stored on the hardware, in the integration's cloud, or only
  in Home Assistant depends on the integration and the device. The plan says
  "may persist outside Home Assistant" unless household or integration
  evidence for that exact platform establishes otherwise.
- **Outage and restart recovery** (power-on behavior and similar): this setting
  decides what the device does when power returns after an outage, a breaker
  trip or a device reboot. For example, a plug feeding a freezer, refrigerator,
  sump pump or medical load set to "off" stays off after an outage, and a plug
  set to "on" or "previous state" may switch a load on unattended.
- **Physical access** (child lock and similar): the setting changes whether the
  device's own buttons work. Locking can block manual control in an emergency.
  Unlocking can allow unintended manual control.
- **Sensing behavior** (sensitivity, timeouts and delays): the setting changes
  when the sensor reports motion, presence or alarm conditions, and therefore
  what automations, lights or alarms do. Lower sensitivity or longer delays can
  miss events; higher sensitivity can cause false triggers. The plan shows the
  value, unit, `min`, `max` and `step` it relies on.
- **Feedback limits:** Home Assistant state is the integration's report, not
  physical observation. If the entity reports `assumed_state`
  (`core:helpers/entity.py:918`), readback proves only Home Assistant's
  optimistic state, and the plan says so.
- **Integration semantics:** option labels and numeric scales are
  integration-defined. The plan shows the raw option string or number the
  device will receive, plus the friendly name when one is available.

These are disclosures, not refusals. Consequence evidence does not by itself
block an exact, owner-approved plan (AGENTS.md product direction; ADR-022).
Whether some settings should also need the existing safety-critical
acknowledgement on the approval page (`E/approval_web.py:1986`, the
`safety_critical_acknowledgement` second action) is §13 Q6. The first slice
does not invent a classifier.

### 5.7 Stale-state protection and concurrency

**Baseline fingerprint.** sha256 over canonical JSON of: entity_id, `state`,
`last_changed`, `last_updated`, context id, `assumed_state`, the `select`
`options` list in order, the `number` `min`/`max`/`step`/`mode`/
`unit_of_measurement`, and registry `id`, `created_at`, `entity_category`,
`device_id`, `platform`, `unique_id`, `config_entry_id`, `modified_at`,
`original_device_class` and `options["number"]`. `last_reported` is excluded
(it changes without a state change). This extends the HAMCP-145
`baseline_fingerprint(state, options)` shape.

**Execution lock set (apply and rollback alike).** The new operation's
prepared lock requests are, in one set:

| Key | Mode | Why |
|---|---|---|
| `entity:<entity_id>` | **exclusive** | The same key `control_power` (and `control_fan`) takes (`E/fan/adapter.py:85-92`). It is produced by mapping the new resource type `device_configuration_entity` to the prefix `entity` in `resource_lock_key` (`E/f3_configuration/locks.py:37-71`). It must **not** use the HAMCP-145 `helper:` prefix or a new `device_configuration:` prefix, either of which would let `control_power` run concurrently |
| `entity_registry:<entity_id>` | shared | Conflicts with the exclusive key Engineering registry metadata, availability and exposure plans take (`E/f3_configuration/locks.py:47-53`), so a governed rename or disable cannot interleave with a send |
| `reload:device_configuration_entity` | shared | Keeps the uniform configuration lock graph, as HAMCP-069A-1 and HAMCP-106 did (`E/f3_configuration/locks.py:204-258`) |
| `home_assistant:core` | shared | Home Assistant availability dependency, as both paths already take |

There is no ha-mcp provider lock: the provider is the direct HA API.
`control_power`'s lock set, contract and signed profile are unchanged.

**Ownership timeline.**

1. The whole set is acquired in one `DurableLockStore` transaction
   (`acquire_once`, `E/f3/locks.py:344`; bounded wait in `acquire`, `:553`) on
   the shared store under `settings.governance_path` (§2.3). Acquisition is
   all-or-nothing, so the order in which this operation and `control_power`
   arrive cannot deadlock or leave a partial set. Contention ends as
   `lock_conflict` with no dispatch.
2. Only after acquisition does the gateway re-run §5.5 and recompute the
   baseline fingerprint. Any difference refuses `stale_target_state` with no
   dispatch. A plan created concurrently with another plan for the same
   entity therefore fails here once the other has applied.
3. Immediately before `mark_provider_send()` the handle's lease and
   generation are revalidated against the store. If the lease expired or was
   fenced, the command is not sent (`before_dispatch_failed`,
   `call_not_sent`).
4. The set is held through the single send and the whole readback window
   (§5.9).
5. Release follows the outcome (§5.12): released after `verified`, and after a
   `provider_rejected` whose Core error is a pre-entity validation code
   (`not_valid_option`, `out_of_range`, `invalid_format`, `not_found`) and
   whose readback equals the baseline fingerprint. Every other post-send
   outcome (`dispatch_indeterminate`, `verification_mismatch`, any other
   rejection) is `manual_review_required`: the set is promoted to a selective
   conflict hold that retains only `entity:<entity_id>`, declared through the
   child's `selective_hold_keys` (`E/f3_runtime/runtime.py:593-601`;
   `E/f3_runtime/repository.py:102-129`). The hold is durable, is
   reconstructed after a restart (`E/f3_runtime/runtime.py:2260`), and is
   released only by `reconcile_child` `release_hold_after_verified_resolution`
   after a read-only reconciliation (`E/f3_runtime/runtime.py:6621`,
   `:6706-6740`). While it is held, `control_power`, another
   device-configuration apply and any rollback on that entity refuse
   `lock_conflict` and send nothing.

**Core admission lease is not execution serialization.** Plan creation and
rollback-plan creation hold the Core planning lease
(`_rollback_planning_lease`, `E/f3_runtime/runtime.py:1116`; used by HAMCP-145
at `E/governance/service.py:4863-4876`). That lease proves Core authority for
the read; it does not serialize writers. Serialization against other
Engineering writers is only the lock set above.

**What locks can and cannot prevent.**

- Preventable, and prevented: concurrent Engineering dispatch on the same
  entity by `control_power`, by another device-configuration apply or
  rollback, or a governed registry plan on the same entity.
- Not preventable by Engineering: changes made in the Home Assistant UI, by
  automations or scripts, by other integrations, by the device's own buttons,
  or by the device itself. These are detected, not prevented: the in-window
  recheck refuses a change that landed before the send; a change that lands
  between send and readback is a `verification_mismatch` and holds the
  entity (step 5), never a redispatch; and rollback requires the exact
  post-apply observation (§5.11).

### 5.8 At-most-once dispatch

As in `InputSelectGateway.select_option`:

1. Run the pre-dispatch callback (lock currentness, stale recheck, authority).
   If it raises, the command is never sent (`before_dispatch_failed`,
   `call_not_sent`).
2. `mark_provider_send()` at the exact send boundary.
3. Send exactly one WebSocket `call_service`. There is no retry, ever.
4. A Core rejection with a received response (for example ServiceValidationError
   `not_valid_option` because the option was removed in between) is
   `provider_rejected`. A timeout or disconnect after send is
   `dispatch_indeterminate`. Neither is treated as proof that the state is
   unchanged. Readback decides (§5.9).

### 5.9 Readback

- Authoritative readback is REST `GET /api/states/{entity_id}`, while the lock
  set is held.
- Device integrations often report the new state asynchronously after
  `blocking=True` returns (for example a Zigbee attribute report). Readback
  therefore polls with the shipped fan schedule, `INLINE_POLL_DELAYS = (1, 1,
  2, 2, 2, 2)` within `INLINE_POLL_BUDGET_SECONDS = 10`
  (`E/fan/service.py:34-35`).
- `verified`: state equals the requested value (exact string for `select`;
  `on`/`off` for `switch`; numeric equality with unchanged unit and bounds for
  `number`, §5.5.1 rule 6), and the target identity of §5.11.1 is unchanged.
  The plan then records the **post-apply observation** of §5.11.1.
- `verification_mismatch`: the budget ends with a different state, with
  `unavailable`/`unknown`, or with an identity change. No redispatch. The
  result reports the last observed state and states that the device may or may
  not have applied the setting. The entity is held (§5.7 step 5).
- After `dispatch_indeterminate` or `provider_rejected`, the same readback runs.
  The outcome is reported as observed and never upgraded to "unchanged".

### 5.10 Interaction with existing governance

- **`call_service`:** unchanged and still refused (`provider_unavailable`, no
  fallback). This design adds no generic service path and no forwarding. The
  new gateway builds its single payload from the closed input.
- **`control_helper`:** unaffected. No domain overlap (helper domains versus
  `select`/`switch`/`number`). 2.5.0 PR #386 (HAMCP-327, control_helper
  fail-to-ask) touches the helper path. This design shares no file with it.
- **`control_power`:** today it reaches config-category switches (§2.2). The
  concurrency correction in §5.7 is required whichever way this is decided:
  the new operation takes the same exclusive `entity:<entity_id>` key, so the
  two paths never dispatch concurrently on one switch, and `control_power`
  itself is unchanged. Whether the fast path should **keep accepting**
  config-category switches is §13 Q3 (pending Josh):
  - **(a) Leave it** (design default). Config switches stay reachable through
    both the fast path and the new governed plan, serialized by the shared
    lock.
  - **(b) Redirect:** refuse config-category entities in `control_power` and
    point to `create_device_configuration_plan`.
  - **(c) Disclose only:** `control_power` adds a consequence line when the
    target is config-category.

  Options (b) and (c) need a registry read that the signed
  `core.typed_power_operation` contract does not name. Under B4 that contract
  cannot be changed, so (b) or (c) would need its own separately scoped,
  additive profile and review; it is not part of this design, and it could
  join the one combined 2026.11.0 signing (B5) only if scoped and reviewed
  before that signing.
- **`reload_domain`, `delete_automation`, `upsert_automation`:** unaffected.

### 5.11 Rollback

#### 5.11.1 What a verified apply records

At `verified` (§5.9) the plan's verification record durably stores:

- **Target identity:** registry `id`, `created_at`, `unique_id`, `platform`,
  `config_entry_id`, `device_id` and `entity_category`; and the device's `id`,
  `created_at`, sorted `identifiers` and `primary_config_entry`. User-editable
  names, areas and labels are not part of identity.
- **Post-apply observation:** `state`, `last_changed`, `last_updated`, context
  id, `assumed_state`, the domain attributes (`select` `options` in order;
  `number` `min`/`max`/`step`/`unit_of_measurement`) and registry
  `options["number"]`; excluding `last_reported`.
- **Post-apply observation fingerprint:** sha256 over the canonical JSON of
  the two items above.
- **Restorable prior value:** the baseline value validated at plan creation
  (§5.5 item 6).

#### 5.11.2 Undo (rollback of a verified apply)

- Rollback is a **separately approved** plan, created by `rollback_change` on
  a `verified` source plan, that restores the restorable prior value through
  the same tool, F3 identity, target proof, lock set, dispatch and readback.
- **At rollback creation** the gateway re-reads the registry entry, device list
  and state and requires (a) the target identity equal to the recorded one,
  else `rollback_target_identity_changed`; and (b) the current observation
  fingerprint equal to the recorded post-apply observation fingerprint, else
  `rollback_post_state_unproven`. Equal value alone is not enough: a replaced
  registry entity at the same entity_id and value has a different registry
  `id`/`created_at`; a later write that returns to the same value has a
  different `last_changed`/`last_updated`/context id.
- The rollback plan's baseline **is** the recorded post-apply observation. The
  prior value is revalidated against current attributes: `switch` `on`/`off`;
  `select` prior option still in the current `options`; `number` §5.5.1 rule 8.
  Otherwise `device_configuration_baseline_not_restorable`, reporting the
  prior value for manual handling.
- **At rollback dispatch**, inside the lock set, identity and the post-apply
  observation fingerprint are checked again; any difference refuses with the
  same codes and sends nothing.
- A verified rollback is reported as **"original change undone (verified)"**.
  It is the only outcome that may claim the original action was undone.
- Rollback is consumed once; the source plan cannot be rolled back twice.
- There is no automatic rollback, matching the house rule for governed plans.

#### 5.11.3 When undo is not provable: hold, reconciliation, compensating change

- After `dispatch_indeterminate`, `verification_mismatch` or a non-validation
  `provider_rejected`, Engineering cannot prove which value is its own.
  `rollback_change` refuses `rollback_post_state_unproven`. The entity stays
  held (§5.7 step 5) until `reconcile_child` releases it after a read-only
  reconciliation.
- After a verified apply whose post-state has since drifted (for example a
  third-party change, or a Home Assistant restart that reset `last_changed`),
  undo is likewise refused.
- In both cases the recovery is a **compensating change**: an ordinary new
  `create_device_configuration_plan` toward the desired value, built from a
  freshly observed baseline with full §5.5 proof, separately approved, and
  carrying a `compensates_plan_id` reference to the original plan. Its result
  is reported as "compensating change applied (verified)", **never** as "the
  original change was undone". It overwrites whatever is present at its own
  fresh baseline, which the approver sees.

#### Superseded (historical)

The c22af225 draft allowed rollback "while the live state still equals the
verified readback value" and, after an indeterminate or mismatched apply, a
rollback plan "from the observed state". Both are superseded by §5.11.2 and
§5.11.3 (Codex D2): value equality cannot prove identity or ownership.

### 5.12 Partial failure

With one entity and one command per plan, the only partial outcomes are:

| Outcome | Dispatched? | Lock after | Reported as |
|---|---|---|---|
| Refused at target proof or stale recheck | No | released | refusal reason, plan unapplied |
| `lock_conflict` | No | not acquired | blocking holder, plan unapplied |
| `before_dispatch_failed` (including lease lost before send) | No | released | `call_not_sent` |
| `provider_rejected`, pre-entity validation code, readback equals baseline | Yes, response received | released | Core error code, unchanged state observed |
| `provider_rejected`, any other | Yes, response received | `entity:` hold | `manual_review_required`, readback outcome |
| `dispatch_indeterminate` | Unknown | `entity:` hold | `manual_review_required`, readback outcome |
| `verification_mismatch` | Yes | `entity:` hold | `manual_review_required`, last observed state |
| `verified` | Yes | released | post-apply observation fingerprint, undo available (§5.11.2) |

If Josh chooses multi-entity plans (§13 Q5), they follow the house rule:
non-atomic, stop on the first failure, no automatic rollback, and per-step
results, with each step's lock set and hold as above.

## 6. Signing impact

Recorded constraints: additive profiles only (B4); one combined 2.6 signing
for Core 2026.11.0 only (B5).

No existing signed profile covers this use (HAMCP-222 rule: a command,
service, argument, F3 identity or adapter not named by a signed contract is not
admitted):

- `core.typed_power_operation` (`sha256:9242b795…001a`) names `light`/`switch`
  `turn_on`/`turn_off` through `reviewed_ha_mcp_typed_power`. It has no
  registry reads, no `select` or `number`, no F3 plan identity and no
  rollback. Reusing it would put a new adapter and provider boundary under a
  signed contract that does not name them, and B4 forbids changing it.
- `core.typed_input_select_option_operation` (`sha256:e2240555…e38d`) names
  `input_select.select_option` only.
- `core.typed_helper_value_operation` (`sha256:d45ea23b…228d`) names helper
  domains only (including `input_number.set_value`, not `number.set_value`).
- `core.entity_registry_metadata_operation` (`profiles.py:495-520`) names
  `config/entity_registry/get` for metadata plans, not this adapter.

| Profile id | New or changed | Core commands / contract added | Why | Fingerprint impact | Signing |
|---|---|---|---|---|---|
| `core.device_configuration_entity_operation` (proposed name, §13 Q7) | **New, additive** (no predecessor; first signed for Core 2026.11.0) | Adapter `compiled-core-device-configuration-entity-operation-v1`; provider boundary `direct_ha_api`; REST `GET /api/states/{entity_id}`; WS `config/entity_registry/get`; WS `config/device_registry/list`; WS `call_service` `select.select_option` (`option` only), `switch.turn_on`, `switch.turn_off` (no data), `number.set_value` (`value` only); single exact `entity_id` target; F3 identity `set_exact_device_configuration_value`/`set_device_configuration_value`; lock identity `entity:<entity_id>` exclusive (§5.7); the numeric exactness contract of §5.5.1 including the embedded 2026.11.0 `UNIT_CONVERTERS` set; restorable-baseline and identity-bound undo rules (§5.5, §5.11); checks such as `entity_registry_config_category_device_proof`, `device_configuration_state_readback_exact`, `device_configuration_number_identity_conversion`; never-sent list per §5.4 | No signed contract names this adapter, these services on config entities, or this F3 identity (above) | Adds one new fingerprint, computed by `_contract()` (`profiles.py:109-145`) in the build PR; unknown until then. **No existing profile fingerprint changes** | Appended to the **one combined 2.6 `extend-capabilities` extension of the existing 2026.11.0 entry** (B5), with its Core 2026.11.0 lane receipt (§9.2) among that extension's `review_artifacts_sha256` (`scripts/prepare_core_release_registry.py:108-126`). No other release is extended, so 2026.10.x keeps refusing `pending_signed_registry_admission` (fail-closed) |

All numeric semantics are in this profile before its first signature. There is
no later "number" profile and no change to `core.typed_power_operation`. A
future power-path change that needed an existing contract changed would
conflict with B4 and cannot be silently included (§5.10).

**Proving check (build PR and signing preparation):** every preexisting signed
contract id and fingerprint is byte-identical to the base; the 2026.11.0
extension's `previous_capabilities` equals the current entry's list and its
`added_capabilities` contains only reviewed new 2.6 profiles, including this
one exactly once.

## 7. Public schema and tool-count impact

- One new static tool: `create_device_configuration_plan`.
- One new input schema (§5.2), additive. No existing tool schema changes.
- One new F3 identity, one new provider contract, one new configuration
  resource type, one new compiled runtime profile.
- `call_service`, `control_power`, `control_helper`, `control_fan` and
  `create_input_select_option_plan` schemas are unchanged.
- The capabilities catalog (`E/capabilities.py`) gains one entry (category
  governance, `direct_write_allowed: False`, writes only through approved
  plans).

Counts: at the design base this is +1 static tool (81 to 82) and +1 compiled
profile (32 to 33). Those are base-local deltas. The 2.6.0 release totals,
fingerprints and the 2026.11.0 profile count must be recomputed from the
integrated 2.6.0 build, after the other 2.6 profiles land.

## 8. Negative reachability and refusals

Each row is a test the build PR must add. These are required future
implementation evidence; none has been run. Refusals happen before any HA I/O
unless the row says otherwise.

| # | Attempt | Expected | HA I/O before refusal |
|---|---|---|---|
| N1 | Profile not signed for the running Core | `pending_signed_registry_admission` on create and apply | none |
| N2 | `entity_id` outside `select.`/`switch.`/`number.` (for example `button.`, `update.`, `light.`, `input_select.`) | `device_configuration_invalid_value` | none |
| N3 | Extra field (`service`, `domain`, `service_data`, `target`, `cycle`, unit) | `device_configuration_field_not_supported` | none |
| N4 | Switch `value` other than `on`/`off` (for example `toggle`); `number` value with exponent, `NaN`, leading zeros or not round-tripping | `device_configuration_invalid_value` | none |
| N5 | Registry `not_found` | `device_configuration_entity_not_registered` | registry get only |
| N6 | `entity_category` `null` or `diagnostic` | `device_configuration_not_config_category` | registry get only |
| N7 | No `device_id`, or device absent from the list | `device_configuration_no_device` | registry reads only |
| N8 | Entity or device disabled | `device_configuration_target_disabled` | registry reads only |
| N9 | State `unavailable`/`unknown` | `device_configuration_entity_unavailable` | reads only |
| N10 | `select` value not in `options` | `device_configuration_value_not_offered` | reads only |
| N11 | Value equals the current state (numerically for `number`, for example `30` vs state `30.0`) | `device_configuration_no_change` | reads only |
| N12 | Any baseline field changes before dispatch (state, `last_changed`, `last_updated`, context, options, `min`/`max`/`step`/unit, registry `modified_at`, `device_id`, category, `options["number"]`) | `stale_target_state`, no `call_service` sent | reads only |
| N13 | Pre-dispatch callback raises | `before_dispatch_failed`, `call_not_sent` | reads only |
| N14 | Gateway is asked for any never-sent service (§5.4) | Unreachable: payload builder has no branch for it. Test asserts the exact payloads and that no other is constructible | none |
| N15 | Timeout after send | `dispatch_indeterminate`, one send only, readback runs, `entity:` hold retained | one send |
| N16 | Readback budget ends with a different state | `verification_mismatch`, no redispatch, `entity:` hold retained | one send |
| N17 | Rollback while the current observation differs from the post-apply observation (state, timestamps or context) | `rollback_post_state_unproven` | reads only |
| N18 | Direct gateway unavailable | fail closed, no ha-mcp or `call_service` fallback | none |
| N19 | `call_service` with a config entity | still `provider_unavailable` (regression) | none |
| N20 | Approval hash mismatch or expired plan | standard F3 refusal, no dispatch | none |
| N21 | Malformed baseline: `switch` state not `on`/`off`; `select` state not in its options; `number` state non-numeric, out of bounds or off-step | `device_configuration_baseline_not_restorable` | reads only |
| N22 | `number` `min`/`max`/`step` missing, non-finite, `min >= max`, `step <= 0`, or disagreeing with registry `capabilities` | `device_configuration_number_bounds_inconsistent` | reads only |
| N23 | `number` with a converter device class and a registry `number.unit_of_measurement` option; `temperature` number | `device_configuration_number_unit_conversion` | reads only |
| N24 | `number` value below `min` or above `max` (including a value Core would clamp) | `device_configuration_number_out_of_range`, never sent | reads only |
| N25 | `number` value off-step (for example `7` with `min` 0, `step` 5; `0.15` with `step` 0.1) | `device_configuration_number_step_misaligned`, never sent | reads only |
| N26 | `number` readback rounded, quantized, clamped, or with a changed unit or bounds | `verification_mismatch`, never `verified` | one send |

**Concurrency tests (Codex D1).** The competing Engineering write never
dispatches in any of these:

| # | Scenario | Expected |
|---|---|---|
| C1 | `control_power` holds `entity:<switch>`; device-configuration apply starts | apply ends `lock_conflict` (or waits, then re-runs §5.5 and refuses stale if power changed the state); no device-configuration send while power holds the lock |
| C2 | Device-configuration apply holds the set; `control_power` on the same switch starts | power ends `lock_conflict`; no power send; power's own contract and lock set unchanged |
| C3 | Two plans for the same entity, both approved, applied concurrently | exactly one sends; the other gets `lock_conflict`, or after acquisition `stale_target_state`; never two sends |
| C4 | Lock lease expires or is fenced between the in-window recheck and `mark_provider_send()` | `call_not_sent`, zero sends |
| C5 | Unresolved post-send ownership (`dispatch_indeterminate`, then process restart) | `entity:` hold reconstructed after restart; `control_power`, another apply and rollback all refuse until `release_hold_after_verified_resolution` |
| C6 | Governed registry metadata or availability plan on the same entity while the set is held | that plan ends `lock_conflict`; no registry write during the send |
| C7 | Lock-key regression | the new resource type maps to exactly `entity:<entity_id>`; `helper:` and `device_configuration:` keys are never produced for it |

**Rollback identity tests (Codex D2).**

| # | Scenario | Expected |
|---|---|---|
| R1 | Registry entity deleted and re-registered at the same entity_id with the same name and value | `rollback_target_identity_changed` at creation and at dispatch |
| R2 | Device replaced (new device `id`) with the same entity_id and value | `rollback_target_identity_changed` |
| R3 | ABA: after apply, a third party sets another value and then the applied value again | `rollback_post_state_unproven` (timestamps/context differ) |
| R4 | Identity or observation changes between rollback creation and rollback dispatch | refused at dispatch with the same codes, zero sends |
| R5 | `select` prior option removed from `options` before rollback | `device_configuration_baseline_not_restorable`, prior value reported |
| R6 | `number` bounds or step change so that the prior value no longer passes §5.5.1 rule 4 | `device_configuration_baseline_not_restorable` |
| R7 | Third-party change after an indeterminate send | `rollback_change` refuses `rollback_post_state_unproven`; a compensating change is offered and is reported as compensating, not undo |
| R8 | Rollback of a rollback, or a second rollback of one plan | refused (consumed once) |

**Success tests (required alongside the refusals).** `select` power-on
behavior change verified and undone (verified); `select` enumerated
sensitivity verified; `switch` child lock on to off verified and undone;
`number` sensitivity set and undone (verified); `number` duration timeout set
and undone (verified) with no unit option present; an asynchronous readback
that succeeds on a later poll; a compensating change after drift that is
verified and labelled as compensating; an exact owner-approved plan whose
disclosed consequence is high (critical load, physical lock) still applies
(tests/AGENTS.md).

## 9. Acceptance

### 9.1 Source acceptance (offline, in the build PR)

- All N, C and R tests and the success tests pass.
- Profile compilation: the new profile compiles, its fingerprint is recorded,
  and every existing fingerprint is byte-identical to the base.
- Tool count and schema snapshots change only by the one new tool.
- The never-sent list is asserted against the gateway's payload builder.
- The embedded `UNIT_CONVERTERS` device-class set equals the pinned Core
  2026.11.0 source.

### 9.2 Core 2026.11.0 disposable-lane acceptance (prerequisite for signing)

ADR-021 requires the assembled registry proofs, dispatch, asynchronous state
and numeric conversion behavior to be proven in the matching exact-version
disposable lane. No household access is needed for this.

- **Lane:** the existing Core 2026.11.0 disposable lane binding
  (`scripts/core_lane_bindings.py:62`, image index
  `sha256:c6c1ca9931036bf3a2c4cc088d8db25dfea3b33935a5ecc83c40349462e2f69e`).
- **Evidence group:** a new group `device_configuration_entity` in
  `ci/core-evidence-groups.json` naming exactly
  `core.device_configuration_entity_operation`, run by a new
  `run_disposable` acceptance module wired into `scripts/core_evidence_lanes.py`
  like the existing groups (`run_specialized`, `:27-58`).
- **Fixture:** a repo-owned, test-only custom component in the style of
  `tests/fixtures/real_ha_device_migration/custom_components/beta23_device_fixture`
  that registers one device with config-category `select`, `switch` and
  `number` entities (a plain number with integration-set step, and a
  `duration` number), plus a non-config switch, a diagnostic select, a
  disabled config entity and a config entity without a device. It can delay
  its state report, reject a send, stall past the send timeout, quantize a
  numeric write, and change its options. Lane setup may use
  `config/entity_registry/update` to set a number unit option on the fixture;
  that is fixture setup, never the product path. Installing it in the lane
  needs a workflow overlay step (a protected `.github/workflows/` path that the
  build task must name).
- **Lane tests (Codex D4):** registered config entities across `select`,
  `switch` and `number` verified; wrong-domain, disabled, diagnostic,
  non-config and device-less entities refused with no `call_service`; delayed
  state reporting verified on a later poll; rejected send and indeterminate
  send each followed by readback, exactly one send each, and the `entity:`
  hold present; numeric bounds, step, unit-conversion and quantization cases
  per N22–N26; verified undo for each domain; identity-replacement rollback
  refusal (R1); and C1/C2 against the lane's `control_power` on the fixture
  config switch.
- **Binding to signing:** the group's PASS receipt is recorded against the
  exact final profile fingerprint and lane image. Its sha256 is one of the
  `review_artifacts_sha256` of the single combined 2.6 2026.11.0
  `core-capability-extension-evidence-v1` (B5;
  `scripts/prepare_core_release_registry.py:108-126`). Any profile change
  after the run invalidates the receipt and requires a rerun. No signing is
  prepared without it.

### 9.3 Deployed acceptance (after a separately authorized release, signing and deployment; not part of this design)

On household-chosen entities identified per §13 E1:

- One power-on-behavior `select`: create, approve and apply; readback
  verified; then create, approve and apply the undo; readback verified.
- One config `switch` (child/physical lock): the same.
- One sensor-tuning entity (`number` sensitivity or timeout, or `select`
  sensitivity, whichever the household has): the same.
- Josh confirms the physical device behaves as reported. Home Assistant state is
  not physical proof.
- Negative live checks: a non-config `switch` and a diagnostic entity are
  refused, with no dispatch in the audit log.

## 10. Upstream citations (Core 2026.11.0, commit `74cf5a49`)

Base URL: `https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/`

- [`const.py` L1026-L1039](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/const.py#L1026-L1039): `EntityCategory`
- [`helpers/entity.py` L970-L975](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/entity.py#L970-L975): `entity_category` property; [L918](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/entity.py#L918): `assumed_state`
- [`helpers/entity_platform.py` L1141-L1163](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/entity_platform.py#L1141-L1163): registration passes the category
- [`helpers/entity_registry.py` L265-L300](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/entity_registry.py#L265-L300), [L393-L440](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/entity_registry.py#L393-L440), [L1610-L1690](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/entity_registry.py#L1610-L1690)
- [`helpers/device_registry.py` L755-L795](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/device_registry.py#L755-L795): device JSON (`id`, `created_at`, `identifiers`, `primary_config_entry`)
- [`helpers/target.py` L226-L229](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/target.py#L226-L229): indirect expansion skips category entities
- [`components/config/entity_registry.py` L95-L121](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/config/entity_registry.py#L95-L121) (get), [L153-L196](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/config/entity_registry.py#L153-L196) (update schema, no `entity_category`)
- [`components/config/device_registry.py` L74-L100](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/config/device_registry.py#L74-L100): device list
- [`components/websocket_api/commands.py` L316-L377](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/websocket_api/commands.py#L316-L377): `call_service`
- [`components/select/services.py` L16-L44](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/select/services.py#L16-L44); [`select/entity.py` L38-L106](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/select/entity.py#L38-L106)
- [`components/switch/services.py` L13-L19](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/switch/services.py#L13-L19)
- [`components/number/services.py` L16-L49](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/number/services.py#L16-L49): range check, conversion, clamp, no step enforcement
- [`components/number/entity.py` L145-L272](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/number/entity.py#L145-L272) (capability attributes, min/max/step); [L362-L466](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/number/entity.py#L362-L466) (value, conversion and rounding, registry unit option)
- [`components/number/const.py` L93-L107](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/number/const.py#L93-L107) (defaults, capability attributes); [L651](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/number/const.py#L651) (`UNIT_CONVERTERS`)
- [`components/button/entity.py` L54-L74](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/button/entity.py#L54-L74)
- [`components/update/entity.py` L128-L140](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/update/entity.py#L128-L140)
- [`components/sensor/entity.py` L234-L238](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/sensor/entity.py#L234-L238)
- Upstream examples only: [`components/tuya/switch.py` L100-L104](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/tuya/switch.py#L100-L104) (child lock); [`components/tuya/select.py` L83-L86](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/tuya/select.py#L83-L86) (relay status / power-on behavior), [L119-L122](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/tuya/select.py#L119-L122) (motion sensitivity); [`components/tuya/number.py` L161-L166](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/tuya/number.py#L161-L166) (sensitivity), [L227-L240](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/tuya/number.py#L227-L240) (arm/alarm delay, duration), [L577-L579](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/tuya/number.py#L577-L579) (bounds from device definition)

## 11. Security section

- **New write reachability:** yes, deliberately. Four exact services
  (`select.select_option`, `switch.turn_on`, `switch.turn_off`,
  `number.set_value`) on registry-proven config-category device entities, only
  through an exact-hash-approved plan. This is a security architecture change
  that needs the explicit scope (this design plus the recorded direction),
  negative tests (§8) and focused review named in AGENTS.md.
- **No generic forwarding:** the payload is built from closed input. No caller
  string reaches `domain`, `service` or the target type. `call_service` stays
  refused.
- **No fallback:** direct HA gateway only. There is no ha-mcp delegation and no
  route to `call_service`.
- **Provider admission:** signed profile required, for Core 2026.11.0 only.
  Until signed, refusal happens before HA I/O.
- **Concurrency:** the new operation shares `control_power`'s exclusive
  `entity:<entity_id>` key in the one durable lock store, so two Engineering
  paths never dispatch concurrently to one entity; unresolved sends hold the
  entity until governed reconciliation (§5.7).
- **Rollback authority:** undo is bound to registry and device identity and
  the exact post-apply observation; compensating changes are separately
  approved and never claim undo (§5.11).
- **Numeric exactness:** Engineering refuses rather than rounds, clamps or
  converts; readback compares numerically and never upgrades a changed value
  (§5.5.1).
- **Category forgery:** `entity_category` cannot be set through
  `config/entity_registry/update` (§3.1). An integration (including MQTT
  discovery) can still label an entity config. The domain and service
  allowlist, not the category, bounds what is sent.
- **Indirect targets:** only one exact `entity_id`. Device, area, floor and label
  targets are never sent.
- **Existing contracts:** `core.typed_power_operation` and every other signed
  profile are unchanged (B4).
- **Audit:** create, approve, apply, readback, hold, reconciliation, rollback
  and compensating change are audited with the plan hash, target identity,
  prior value, requested value, lock keys and outcome. Option strings are
  bounded (255) and sanitized as in HAMCP-145.
- **Bounded output:** `options` list at most 256 entries of at most 255
  characters. Device name is bounded.
- **Secrets:** none read or added.

## 12. Build plan (after the remaining owner choices)

1. Build PR on the 2.6.0 line (protected Engineering runtime path, named and
   authorized in that task):
   `E/governance/device_configuration.py` (gateway, payload builder, baseline,
   numeric contract), `E/governance/device_configuration_planning.py` (closed
   input), the routes table and constants in `E/ha_core_readmission/routes.py`,
   the new profile in `E/ha_core_readmission/profiles.py`, the
   `device_configuration_entity` mapping in `E/f3_configuration/locks.py`, the
   tool in `E/tools/`, the routing entry in `E/providers/routing.py`, and the
   capabilities entry. Tests per §8.
2. Compute and record the new fingerprint. Assert that every existing
   fingerprint is unchanged.
3. Core 2026.11.0 lane acceptance per §9.2: fixture component, acceptance
   module, evidence group, and the workflow overlay step (protected
   `.github/workflows/` path, named in that task). PASS receipt bound to the
   final fingerprint.
4. Signing: the one combined 2.6 `extend-capabilities` for 2026.11.0 (B5),
   including this profile and its lane receipt. This is a separate,
   owner-authorized action.
5. Release and deployment: separately authorized.
6. Deployed acceptance per §9.3 on household entities identified per §13 E1.

**2.5.0 overlap:** the build will touch `E/ha_core_readmission/routes.py`,
`profiles.py`, `E/f3_configuration/locks.py`, `E/providers/routing.py`,
`E/capabilities.py` and `E/tools/registry.py`. In-flight 2.5.0 PRs (#386
HAMCP-327, #387 HAMCP-288, #385 HAMCP-319, HAMCP-290, #384 HAMCP-309 Part 3)
may touch some of these. 2.5.0 wins shared files. The build catches up by
merging `main` (never a rebase) after 2.5.0 lands. This design PR itself shares
no file with them.

## 13. Open items — pending Josh

Closed (not reopened): **Q1** use-case intent is answered (B3). **Q2**
(`number` in the first slice) is closed as a design consequence of B3: sensor
tuning needs `number` (§3.4, §5.5.1). **Q4** signing placement is answered
(B5).

| # | Item | Why it matters | Design default if unanswered | Blocks |
|---|---|---|---|---|
| Q3 | `control_power` and config-category switches: (a) leave, (b) separately scoped redirect to the new plan, or (c) separately scoped disclosure (§5.10) | Today a child lock is one fast call away with no rollback. (b) and (c) need an additive profile of their own under B4. The §5.7 lock correction applies either way | (a) leave unchanged | Nothing in this build. (b)/(c) would be a separate scope and must be reviewed before the combined signing to join it |
| E1 | **Household evidence (not an intent question):** which integrations and entities carry power-on behavior, child/physical lock and sensor tuning in the household, with their domain, platform, `min`/`max`/`step`, unit and device class | Decides deployed acceptance targets (§9.3) and whether any household numeric entity falls outside §5.5.1 (for example a temperature or user-unit timeout, which would be refused) | Lane-only acceptance until obtained | Deployed acceptance; possibly a contract adjustment before the profile is frozen |
| Q5 | Per-plan bounds: one entity per plan, or up to N (for example 8) entities of one domain on one device | Multi-entity plans are non-atomic; locks, readback, holds and undo per step | One entity per plan | Contract freeze (input schema and profile) |
| Q6 | Should some settings (for example power-on behavior on named critical loads) need the existing safety-critical acknowledgement on the approval page (`E/approval_web.py:1986`)? | Adds a second confirmation for high-consequence settings | Disclosure only (§5.6), no extra acknowledgement | Contract freeze if a per-target rule enters the profile |
| Q7 | Final names for the tool (`create_device_configuration_plan`) and profile (`core.device_configuration_entity_operation`) | Names enter the signed contract and the public schema | Proposed names (a reversible implementation default until the build PR freezes them) | Contract freeze only if Josh objects |

Proposed ways to obtain E1 (no live access is taken by this design):
(a) Josh lists the devices and entities; (b) Josh authorizes a separately
scoped, read-only inventory through the deployed Engineering read tools,
limited to `select`/`switch`/`number` registry entries with
`entity_category == "config"` and their state attributes; or (c) proceed on
lane evidence and choose household targets at deployed acceptance.

## 14. Codex design review 2026-11-08 — disposition

Review of head `c22af225` against base `7246f2d2`. All five findings are
corrected in this revision; no finding is deferred.

| ID | Severity | Finding (short) | Correction |
|---|---|---|---|
| D1 | High | Concurrency exception let `control_power` and the new apply dispatch concurrently | §2.3 (base lock facts); §5.7 rewritten: exclusive `entity:<entity_id>` shared with `control_power` through the `device_configuration_entity` → `entity` mapping, full lock set, atomic acquisition, ownership through dispatch, readback and selective hold until `reconcile_child`; Core admission lease separated from execution serialization; external versus preventable races; `control_power` contract unchanged. Tests C1–C7 |
| D2 | High | Rollback did not bind target identity or the post-apply observation | §5.5 item 6 (valid restorable baseline); §5.11 rewritten: recorded target identity and post-apply observation fingerprint, checked at rollback creation and dispatch; refusal codes; hold and reconciliation; compensating change distinct from undo; old text under "Superseded (historical)". Tests R1–R8, N17, N21 |
| D3 | Medium | Slice and acceptance omitted the answered sensor-tuning target | B3 recorded (§1); §3.4 maps the three use cases to `select`, `switch` and `number`; §3.5 numeric source facts; §4 puts `number.set_value` in the slice; §5.5.1 numeric contract (bounds, step, identity conversion, no rounding or clamping, numeric readback, undo) inside the one new profile before first signing (§6); Q1/Q2 closed. Tests N22–N26 and numeric success tests |
| D4 | Medium | Acceptance lacked the exact-Core disposable lane | §9.2 Core 2026.11.0 lane acceptance with evidence group, fixture, lane tests, and its receipt bound to the final fingerprint and the combined 2026.11.0 extension evidence; §12 step 3 |
| D5 | Low | Wrong power profile id and obsolete signing alternatives | `core.typed_power_operation` used throughout (§2.2, §5.10, §6, §11); B4/B5 recorded as decided; Q4 removed; the "power fingerprint may change" text removed; proving check in §6 |
| Note | — | Config-category persistence presented as universal | §5.6 persistence disclosure qualified; §3.1 consequence |

Counts in §7 are restated as base-local deltas, as the review asked.
