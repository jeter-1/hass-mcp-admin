# HAMCP-297: Governed Device-Configuration Entities (2.6.0 Design)

Status: **design only, awaiting owner decisions.** This document changes no
runtime code, schema, tool registration, policy, workflow, signed registry,
release declaration or version. It is the one new file in its pull request.

- Register: HAMCP-297 (Backlog when this design started).
- Target release line: 2.6.0 (`release/2.6.0`).
- Base: `origin/release/2.6.0` `7246f2d21693f039504542574569f03b71476489`
  (HAMCP-331, Engineering `2.4.2-beta.7`).
- Pinned Home Assistant Core: `2026.11.0`, commit
  `74cf5a49c63ed8d4d87717844bf14f79c28c9cea` (tree
  `530241f5976abdfbea2fa1fa51e0ddc7a4d42560`, archive sha256
  `85f3c42c9f77aa45e01faff4471007beadf5cb50c4324f496a239139eec70ff1`). The
  binding is in `scripts/core_lane_bindings.py:62` and
  [`evidence/hamcp315/core-sources/2026.11.0.json`](evidence/hamcp315/core-sources/2026.11.0.json).

Engineering paths are relative to `hass_mcp_engineering_beta/ha_mcp_engineering/`
(written `E/`), with line numbers at the base commit. Upstream citations are
read-only public source at the pinned commit (ADR-021). In the text,
`core:<path>:<lines>` means
`https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/<path>#L<lines>`.
§10 lists the full links.

Nothing here came from a live Home Assistant instance or a deployed MCP
endpoint. No household device, entity or setting was probed. Every household
fact this design needs is an open question for Josh (§13).

## 1. Register outcome and binding decisions

Register Outcome (HAMCP-297, verbatim):

> Configuration-category entities on devices (for example power-on behavior and
> child lock on plugs) can be changed through a typed, bounded, governed path
> with exact targets, stale-state protection, readback and rollback, instead of
> by hand.

Register Next Action at the start of this design: "Feature lane position 3
after HAMCP-253, 267 and 264 (Josh's ranking, 2026-11-03). Needs a household
use-case list; likely a signed Core profile." Notes were empty.

| # | Decision or constraint | Source | Status |
|---|---|---|---|
| B1 | Feature lane position 3, after HAMCP-253, 267 and 264 | Register Next Action | **Josh decision** (2026-11-03 ranking) |
| B2 | The path is typed, bounded and governed, with exact targets, stale-state protection, readback and rollback | Register Outcome | **Josh decision** (register Outcome) |
| B3 | Examples in scope: power-on behavior and child lock on plugs | Register Outcome | **Josh decision** (examples, not a closed list) |
| B4 | A household use-case list is needed before build | Register Next Action | Open question — pending Josh (§13 Q1) |
| B5 | "Likely a signed Core profile" | Register Next Action | Design proposal confirms it: one new profile (§6) |
| B6 | Design document only, one new file, no runtime or protected change | Task prompt and 2.6.0 brief | Task constraint |
| B7 | Mechanism keys on `entity_category == "config"` entities of typed domains | Task prompt | Task constraint, refined in §3.1 |

Everything else in this document is a design proposal, not a recorded decision.

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
  `CORE_TYPED_OPERATION_PROFILES`, `E/ha_core_readmission/profiles.py:426-460`).
  It does not cover `select`, `switch` or `number`.
- `create_input_select_option_plan` (HAMCP-145) is `input_select` only
  (`E/ha_core_readmission/routes.py:175-179`;
  `E/governance/input_select_option_planning.py:19`).

The result: a device's `select.*` configuration entity (for example Tuya
"Power-on behavior") cannot be changed through Engineering at all today.

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
rollback. `control_power` is signed under `core.typed_operation` family
`power` (`light`/`switch`, `turn_on`/`turn_off`, provider boundary
`reviewed_ha_mcp_typed_power`, fingerprint
`sha256:9242b795a2d5ed3deeb3b8ea46fba13fc299c14fb274e3b29ed97a30b607001a`).
Its docstring already warns "a switch may power a critical load"
(`E/tools/power.py:29`). How this design treats that path is §5.10 and
§13 Q3.

### 2.3 Counts at the base

- Static tools: 81 (`ENGINEERING_STATIC_TOOL_COUNT`,
  `E/tools/registry.py:239-240`, measured by importing the base).
- Compiled runtime Core profiles: 32 (`CORE_RUNTIME_CAPABILITY_PROFILES`,
  `E/ha_core_readmission/profiles.py` about `:893-900`).
- Signed registry (canonical on the `registry` branch): seq 17, generated
  2026-11-07T06:16:34Z. Profile counts per release: 2026.10.0 has 22, 2026.10.1
  has 26, 2026.10.3 has 31, 2026.11.0 has 32. Seq 17 added
  `core.typed_helper_value_operation` (`sha256:d45ea23b…228d`) to 2026.11.0.
  The `main` copy is frozen at seq 15.

### 2.4 The shape template

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

[`HAMCP_232_145_HELPER_OPTIONS_AND_SELECT_DESIGN.md`](HAMCP_232_145_HELPER_OPTIONS_AND_SELECT_DESIGN.md)
is the house-style design precedent. This design reuses its shape.

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
- The registry entry carries `entity_category` (`core:helpers/entity_registry.py:282`),
  and `as_partial_dict`/`extended_dict` serialize it with `device_id`,
  `disabled_by`, `id`, `platform`, `unique_id`, `config_entry_id` and
  `modified_at` (`:393-438`).
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
domain and service allowlist, not the category alone, bounds the slice.

### 3.2 Registry reads

- `config/entity_registry/get` takes one exact `entity_id`. It returns
  `extended_dict`, or the error `not_found` "Entity not found"
  (`core:components/config/entity_registry.py:95-121`).
- `config/device_registry/list` returns every device's cached JSON, including
  `id`, `disabled_by` and `entry_type`
  (`core:components/config/device_registry.py:74-100`;
  `core:helpers/device_registry.py:763-775`).

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
| `number` | `set_value` | required `value` (`Coerce(float)`) | `state` is the value in the display unit. The service raises `out_of_range` outside min/max, converts to the native unit, clamps, and does not enforce `step` | `core:components/number/services.py:16-49`; `number/entity.py:145-163`, `:241-249`, `:265-272`, `:366-372`, `:400-445` |
| `button` | `press` | none | `state` is the last-pressed timestamp, written **before** `async_press` runs | `core:components/button/services.py:11-15`; `button/entity.py:54-74`; `button/const.py:21-26` |
| `update` | `install`, `skip`, `clear_skipped` | install: version, backup | firmware state | `core:components/update/services.py`; `update/entity.py:33`, `:128-140` |

### 3.4 In-tree examples of the register's two named settings

- Tuya "Child lock": `switch`, `translation_key="child_lock"`,
  `entity_category=EntityCategory.CONFIG`
  (`core:components/tuya/switch.py:100-104`, `:149-153`, `:202-205`).
- Tuya "Power-on behavior": `select`, `translation_key="relay_status"`,
  `entity_category=EntityCategory.CONFIG` (`core:components/tuya/select.py:83-86`,
  `:228-231`, `:259-262`). Its options include `last`/`memory` ("Previous
  state"), `on`/`power_on` and `off`/`power_off` (Tuya `strings.json`).
- ZHA takes the category from the `zha` library
  (`core:components/zha/select.py:47-65`). MQTT takes it from the discovery
  configuration.

These confirm that the register's two examples are a `select` and a `switch`
at the pinned Core, at least for one in-tree integration. Which integrations
the household actually uses is unknown (§13 Q1).

## 4. Bounded first slice

| Domain / service | First slice? | Why (from source) |
|---|---|---|
| `select.select_option` | **Yes** | Absolute, idempotent target value; options are enumerated in state; readback is exact (`state == option`); the prior option is a valid rollback target |
| `select.select_first/last/next/previous` | **No, never sent** | Relative moves; the outcome depends on the current state and on option order |
| `switch.turn_on` / `switch.turn_off` | **Yes** | Absolute, idempotent; readback is exact (`on`/`off`); the prior state is the rollback target |
| `switch.toggle` | **No, never sent** | Relative; the outcome depends on the current state |
| `number.set_value` | **Deferred** (§13 Q2) | Readback is not exact by construction: unit conversion and rounding, server-side clamping, and `step` is not enforced (`number/services.py:16-49`, `number/entity.py:400-445`) |
| `button.press` | **No** | An action, not a setting. State is written before the press, so readback cannot prove the effect, and there is no prior value to roll back to (`button/entity.py:54-74`). `identify`/`restart`/`update` device classes are actions (`button/const.py:21-26`) |
| `update.*` | **No, never sent** | Firmware install. Config category by default but not a setting |
| `text`, `time`, `light`, others | **Deferred** | No register example; no household use case recorded. A later increment needs its own readback analysis |

The first slice is therefore **`select.select_option` and
`switch.turn_on`/`switch.turn_off` on registry-proven config-category device
entities.** It covers both register examples. `number` is the most likely second
increment, because many devices expose numeric configuration (for example
LED brightness or auto-off timers). Including it in the first slice is an open
question with a concrete exactness rule proposed in §13 Q2.

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
| `entity_id` | Required. `^(select|switch)\.[a-z0-9_]{1,120}$` |
| `value` | Required string. For `select`: 1–255 characters, no control characters, compared exactly (case-sensitive, never stripped), as in `input_select_option_planning.py:62-70`. For `switch`: exactly `on` or `off` |
| `reason` | Required, 1–500 characters after strip |
| `expiration_minutes` | Optional integer 5–1440, default 120 |

One entity per plan in the first slice. Multi-entity plans are an open
question (§13 Q5). No other field is accepted: no `service`, `domain`,
`service_data`, `target` beyond the entity, `cycle` or `option` alias.

Plan output adds, beyond the standard plan envelope:

- `target`: entity_id, domain, registry `id`, `platform`, `unique_id`,
  `device_id`, device name (bounded), `entity_category: "config"`;
- `baseline`: prior value (`state`), for `select` the bounded `options` list,
  `last_updated`, registry `modified_at`;
- `requested_value` and the exact service it maps to (`select.select_option`
  with `{"option": value}`, or `switch.turn_on`/`switch.turn_off` with no data);
- `consequences`: the disclosure in §5.6;
- `rollback`: "separately approved plan restoring `<prior value>`".

### 5.3 Routing and provider boundary

- Provider: direct Home Assistant API under a new provider contract
  `direct-ha-exact-device-configuration-v1`, matching the HAMCP-145
  `direct-ha-exact-input-select-option-v1` pattern. No ha-mcp delegation, no
  `call_service` reuse and no fallback. If the direct gateway is unavailable,
  the plan fails closed.
- Admission: the route requires the new signed profile (§6). Until the running
  Core release's signed registry entry covers it, create and apply refuse with
  `pending_signed_registry_admission` before any HA I/O, as HAMCP-145 does.
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

Never sent (the profile's never-sent list): `select.select_first`,
`select.select_last`, `select.select_next`, `select.select_previous`,
`switch.toggle`, `number.*`, `button.press`, `update.*`,
`config/entity_registry/update`, `config/device_registry/update`, any
`return_response: true`, and any `call_service` whose target is not exactly the
one planned `entity_id` (no `device_id`, `area_id`, `floor_id` or `label_id`
targets).

### 5.5 Target proof

At plan creation, and again inside the apply authority window, the gateway
requires all of the following. Any failure refuses before dispatch.

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
5. The entity domain is exactly `select` or `switch`, matching the input
   pattern. The registry `platform` is recorded. It is not allow-listed in the
   first slice (§13 Q1 may narrow it to named integrations).
6. The state read returns the same `entity_id`, a state that is not
   `unavailable` or `unknown` (refuse `device_configuration_entity_unavailable`),
   and for `select` a bounded, duplicate-free `options` list (1–256 strings)
   containing `value`, or refuse `device_configuration_value_not_offered`.
7. The requested value differs from the current state. Equal values refuse
   `device_configuration_no_change`, so no plan is created for a no-op.

The plan records registry `id`, `unique_id`, `platform`, `device_id` and
`modified_at`. A later rename of the entity_id, or a re-registration under a
different id, therefore breaks the binding and is caught as stale.

### 5.6 Approval and risk disclosure

Every plan needs exact-hash external approval. This family has no fast path
and no model-conveyed confirmation.

The plan's `consequences` field always includes:

- **Persistence:** the setting is stored on the device. It is not undone by a
  Home Assistant restart, and it can survive the device leaving Home Assistant.
- **Outage and restart recovery** (power-on behavior and similar): this setting
  decides what the device does when power returns after an outage, a breaker
  trip or a device reboot. For example, a plug feeding a freezer, refrigerator,
  sump pump or medical load set to "off" stays off after an outage, and a plug
  set to "on" or "previous state" may switch a load on unattended.
- **Physical access** (child lock and similar): the setting changes whether the
  device's own buttons work. Locking can block manual control in an emergency.
  Unlocking can allow unintended manual control.
- **Feedback limits:** Home Assistant state is the integration's report, not
  physical observation. If the entity reports `assumed_state`
  (`core:helpers/entity.py:918`), readback proves only Home Assistant's
  optimistic state, and the plan says so.
- **Integration semantics:** option labels are integration-defined. The plan
  shows the raw option string the device will receive, plus the friendly name
  when one is available.

These are disclosures, not refusals. Consequence evidence does not by itself
block an exact, owner-approved plan (AGENTS.md product direction; ADR-022).
Whether some settings (for example power-on behavior on named critical loads)
should also need the existing safety-critical acknowledgement on the approval
page (`E/approval_web.py:1986`, the `safety_critical_acknowledgement` second
action) is an open question (§13 Q6). The first slice does not invent a
classifier.

### 5.7 Stale-state protection and concurrency

- Baseline fingerprint (sha256 over canonical JSON) covers: entity_id, `state`,
  `last_updated`, the `select` `options` list in order, and registry `id`,
  `entity_category`, `device_id`, `platform`, `unique_id` and `modified_at`.
  This extends the HAMCP-145 `baseline_fingerprint(state, options)` shape with
  the registry fields.
- Inside the F3 authority window and immediately before dispatch, the gateway
  re-runs §5.5 and recomputes the fingerprint. Any difference refuses
  `stale_target_state` with no dispatch. The plan must then be recreated and
  re-approved.
- One lease per `entity_id` (the plan's `lease_target_id`), as HAMCP-145 does.
  A concurrent plan or apply on the same entity is refused, not queued.
- A concurrent `control_power` call on the same switch is not serialized by
  this lease, because `control_power` holds its own authority. The fingerprint
  recheck catches a change that lands before dispatch. A change that lands
  between dispatch and readback shows up as a readback mismatch, never a
  redispatch.

### 5.8 At-most-once dispatch

As in `InputSelectGateway.select_option`:

1. Run the pre-dispatch callback (stale recheck, lease, authority). If it
   raises, the command is never sent (`before_dispatch_failed`,
   `call_not_sent`).
2. `mark_provider_send()` at the exact send boundary.
3. Send exactly one WebSocket `call_service`. There is no retry, ever.
4. A Core rejection with a received response (for example ServiceValidationError
   `not_valid_option` because the option was removed in between) is
   `provider_rejected`. A timeout or disconnect after send is
   `dispatch_indeterminate`. Neither is treated as proof that the state is
   unchanged. Readback decides (§5.9).

### 5.9 Readback

- Authoritative readback is REST `GET /api/states/{entity_id}`.
- Device integrations often report the new state asynchronously after
  `blocking=True` returns (for example a Zigbee attribute report). Readback
  therefore polls with the shipped fan schedule, `INLINE_POLL_DELAYS = (1, 1,
  2, 2, 2, 2)` within `INLINE_POLL_BUDGET_SECONDS = 10`
  (`E/fan/service.py:34-35`).
- `verified`: state equals the requested value (exact string for `select`;
  `on`/`off` for `switch`). The plan records the readback fingerprint.
- `verification_mismatch`: the budget ends with a different state, or with
  `unavailable`/`unknown`. No redispatch. The result reports the last observed
  state and states that the device may or may not have applied the setting.
- After `dispatch_indeterminate` or `provider_rejected`, the same readback runs.
  The outcome is reported as observed and never upgraded to "unchanged".

### 5.10 Interaction with existing governance

- **`call_service`:** unchanged and still refused (`provider_unavailable`, no
  fallback). This design adds no generic service path and no forwarding. The
  new gateway builds its single payload from the closed input.
- **`control_helper`:** unaffected. No domain overlap (helper domains versus
  `select`/`switch`). 2.5.0 PR #386 (HAMCP-327, control_helper fail-to-ask)
  touches the helper path. This design shares no file with it.
- **`control_power`:** today it reaches config-category switches (§2.2). The
  options are:
  - **(a) Leave it.** Config switches stay reachable through both the fast
    path and the new governed plan. Simplest, but the outage and access
    consequences of a child lock or similar remain one fast call away with no
    rollback.
  - **(b) Refuse config-category entities in `control_power`** with a refusal
    that points to `create_device_configuration_plan`. This needs a registry
    read that the ha-mcp-delegated power path does not perform today. It is a
    runtime change to a shipped, signed typed-power path, and arguably changes
    the `core.typed_operation` power family's contract.
  - **(c) Disclose only.** `control_power` adds a consequence line when the
    target is config-category. This still needs the registry read.

  Changing `control_power` is a scope change beyond the register Outcome, so
  this design does not choose. It stops at **§13 Q3, pending Josh.** The build
  plan in §12 assumes (a) until Josh decides.
- **`reload_domain`, `delete_automation`, `upsert_automation`:** unaffected.

### 5.11 Rollback

- Rollback is a **separately approved** plan that restores the captured prior
  value through the same tool, F3 identity, target proof, stale recheck,
  dispatch and readback.
- It can be built only while the live state still equals the verified
  readback value. Otherwise it refuses `stale_target_state`. Rollback never
  overwrites a value someone else set after the apply.
- For `select`, the prior option must still be in the current `options` list.
  Otherwise it refuses `device_configuration_value_not_offered` and reports the
  prior value for manual handling.
- There is no automatic rollback, matching the house rule for governed plans.
- A plan whose apply ended in `verification_mismatch` or
  `dispatch_indeterminate` can still produce a rollback plan from the
  **observed** state, if that state differs from the prior value. The rollback
  plan's baseline is the observed state, not the requested one.

### 5.12 Partial failure

With one entity and one command per plan, the only partial outcomes are:

| Outcome | Dispatched? | Reported as |
|---|---|---|
| Refused at target proof or stale recheck | No | refusal reason, plan unapplied |
| `before_dispatch_failed` | No | `call_not_sent` |
| `provider_rejected` | Yes, response received | readback outcome, plus the Core error code |
| `dispatch_indeterminate` | Unknown | readback outcome |
| `verification_mismatch` | Yes | last observed state, rollback eligibility |
| `verified` | Yes | readback fingerprint, rollback plan available |

If Josh chooses multi-entity plans (§13 Q5), they follow the house rule:
non-atomic, stop on the first failure, no automatic rollback, and per-step
results.

## 6. Signing impact

No existing signed profile covers this use (HAMCP-222 rule: a command,
service, argument, F3 identity or adapter not named by a signed contract is not
admitted):

- `core.typed_operation` family `power` (`sha256:9242b795…001a`) names `light`/
  `switch` `turn_on`/`turn_off` through `reviewed_ha_mcp_typed_power`. It has no
  registry reads, no `select`, no F3 plan identity and no rollback. Reusing it
  would put a new adapter and provider boundary under a signed contract that
  does not name them.
- `core.typed_input_select_option_operation` (`sha256:e2240555…e38d`) names
  `input_select.select_option` only.
- `core.typed_helper_value_operation` (`sha256:d45ea23b…228d`) names helper
  domains only.
- `core.entity_registry_metadata_operation` (`profiles.py:495-520`) names
  `config/entity_registry/get` for metadata plans, not this adapter.

| Profile id | New or changed (from version X) | Core commands / contract added | Why | Fingerprint impact | Can it batch with other 2.6 profiles |
|---|---|---|---|---|---|
| `core.device_configuration_entity_operation` (proposed name) | **New** (no predecessor; first signed for Core 2026.11.0) | Adapter `compiled-core-device-configuration-entity-operation-v1`; provider boundary `direct_ha_api`; REST `GET /api/states/{entity_id}`; WS `config/entity_registry/get`; WS `config/device_registry/list`; WS `call_service` `select.select_option` (`option` only), `switch.turn_on`, `switch.turn_off` (no data), single exact `entity_id` target; F3 identity `set_exact_device_configuration_value`/`set_device_configuration_value`; checks such as `entity_registry_config_category_device_proof`, `device_configuration_state_readback_exact`; never-sent list per §5.4 | No signed contract names this adapter, these services on config entities, or this F3 identity (above) | Adds one new fingerprint (computed by `_contract()`, `profiles.py:109-145`, in the build PR; unknown until then). **No existing profile fingerprint changes.** 2026.11.0 goes from 32 to 33 profiles | **Yes.** It can share one `extend-capabilities` sequence step for 2026.11.0 with other 2.6 profiles (one changed release entry per step, `scripts/prepare_core_release_registry.py:165-166`, `:184-185`). Earlier releases (2026.10.x) are not extended, so they keep refusing `pending_signed_registry_admission` (fail-closed) |

If `number` joins the slice (§13 Q2), it adds `number.set_value` to this same
new profile before first signing, not as a separate profile. If Josh chooses
`control_power` option (b) or (c) (§13 Q3), the `core.typed_operation`
fingerprint may change. That would be a second, separate signing impact,
assessed when decided.

## 7. Public schema and tool-count impact

- One new static tool: `create_device_configuration_plan`. Static tools go
  from 81 to 82 (`E/tools/registry.py:239-240`).
- One new input schema (§5.2), additive. No existing tool schema changes.
- One new F3 identity, one new provider contract, one new compiled runtime
  profile (32 to 33).
- `call_service`, `control_power`, `control_helper`, `control_fan` and
  `create_input_select_option_plan` schemas are unchanged.
- The capabilities catalog (`E/capabilities.py`) gains one entry (category
  governance, `direct_write_allowed: False`, writes only through approved
  plans).

## 8. Negative reachability and refusals

Each row is a test the build PR must add. Refusals happen before any HA I/O
unless the row says otherwise.

| # | Attempt | Expected | HA I/O before refusal |
|---|---|---|---|
| N1 | Profile not signed for the running Core | `pending_signed_registry_admission` on create and apply | none |
| N2 | `entity_id` outside `select.`/`switch.` (for example `number.`, `button.`, `update.`, `light.`, `input_select.`) | `device_configuration_invalid_value` | none |
| N3 | Extra field (`service`, `domain`, `service_data`, `target`, `cycle`) | `device_configuration_field_not_supported` | none |
| N4 | Switch `value` other than `on`/`off` (for example `toggle`) | `device_configuration_invalid_value` | none |
| N5 | Registry `not_found` | `device_configuration_entity_not_registered` | registry get only |
| N6 | `entity_category` `null` or `diagnostic` | `device_configuration_not_config_category` | registry get only |
| N7 | No `device_id`, or device absent from the list | `device_configuration_no_device` | registry reads only |
| N8 | Entity or device disabled | `device_configuration_target_disabled` | registry reads only |
| N9 | State `unavailable`/`unknown` | `device_configuration_entity_unavailable` | reads only |
| N10 | `select` value not in `options` | `device_configuration_value_not_offered` | reads only |
| N11 | Value equals the current state | `device_configuration_no_change` | reads only |
| N12 | Any baseline field changes before dispatch (state, `last_updated`, options, registry `modified_at`, `device_id`, category) | `stale_target_state`, no `call_service` sent | reads only |
| N13 | Pre-dispatch callback raises | `before_dispatch_failed`, `call_not_sent` | reads only |
| N14 | Gateway is asked for any never-sent service (§5.4) | Unreachable: payload builder has no branch for it. Test asserts the exact payloads and that no other is constructible | none |
| N15 | Timeout after send | `dispatch_indeterminate`, one send only, readback runs | one send |
| N16 | Readback budget ends with a different state | `verification_mismatch`, no redispatch | one send |
| N17 | Rollback while live state differs from verified readback | `stale_target_state` | reads only |
| N18 | Direct gateway unavailable | fail closed, no ha-mcp or `call_service` fallback | none |
| N19 | `call_service` with a config entity | still `provider_unavailable` (regression) | none |
| N20 | Approval hash mismatch or expired plan | standard F3 refusal, no dispatch | none |

Success tests are also required: `select` verified, `switch` on to off verified,
rollback verified, and an asynchronous readback that succeeds on a later poll.

## 9. Acceptance

Source acceptance (offline, in the build PR):

- All N1–N20 tests and the success tests pass.
- Profile compilation: the new profile compiles, its fingerprint is recorded,
  and all 32 existing fingerprints are byte-identical to the base.
- Tool count is 82. Tool and schema snapshots change only by the one new tool.
- The never-sent list is asserted against the gateway's payload builder.

Deployed acceptance (after a separately authorized release, signing and
deployment; not part of this design):

- On one household-chosen device (§13 Q1): create, approve and apply a
  power-on-behavior `select` change. Readback is verified. Then create, approve
  and apply the rollback. Readback is verified.
- The same for one config `switch` (for example a child lock).
- Josh confirms the physical device behaves as reported. Home Assistant state is
  not physical proof.
- Negative live checks: a non-config `switch` and a diagnostic entity are
  refused, with no dispatch in the audit log.

## 10. Upstream citations (Core 2026.11.0, commit `74cf5a49`)

Base URL: `https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/`

- [`const.py` L1026-L1039](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/const.py#L1026-L1039): `EntityCategory`
- [`helpers/entity.py` L970-L975](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/entity.py#L970-L975): `entity_category` property; [L918](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/entity.py#L918): `assumed_state`
- [`helpers/entity_platform.py` L1141-L1163](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/entity_platform.py#L1141-L1163): registration passes the category
- [`helpers/entity_registry.py` L265-L300](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/entity_registry.py#L265-L300), [L393-L438](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/entity_registry.py#L393-L438), [L1610-L1690](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/entity_registry.py#L1610-L1690)
- [`helpers/target.py` L226-L229](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/target.py#L226-L229): indirect expansion skips category entities
- [`components/config/entity_registry.py` L95-L121](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/config/entity_registry.py#L95-L121) (get), [L153-L196](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/config/entity_registry.py#L153-L196) (update schema, no `entity_category`)
- [`components/config/device_registry.py` L74-L100](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/config/device_registry.py#L74-L100): device list
- [`components/websocket_api/commands.py` L316-L377](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/websocket_api/commands.py#L316-L377): `call_service`
- [`components/select/services.py` L16-L44](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/select/services.py#L16-L44); [`select/entity.py` L38-L106](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/select/entity.py#L38-L106)
- [`components/switch/services.py` L13-L19](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/switch/services.py#L13-L19)
- [`components/number/services.py` L16-L49](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/number/services.py#L16-L49); [`number/entity.py` L400-L445](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/number/entity.py#L400-L445)
- [`components/button/entity.py` L54-L74](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/button/entity.py#L54-L74)
- [`components/update/entity.py` L128-L140](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/update/entity.py#L128-L140)
- [`components/sensor/entity.py` L234-L238](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/sensor/entity.py#L234-L238)
- [`components/tuya/switch.py` L100-L104](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/tuya/switch.py#L100-L104) (child lock); [`components/tuya/select.py` L83-L86](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/tuya/select.py#L83-L86) (relay status / power-on behavior)

## 11. Security section

- **New write reachability:** yes, deliberately. Three exact services
  (`select.select_option`, `switch.turn_on`, `switch.turn_off`) on
  registry-proven config-category device entities, only through an
  exact-hash-approved plan. This is a security architecture change that needs
  the explicit scope (this design plus Josh's decisions), negative tests (§8)
  and focused review named in AGENTS.md.
- **No generic forwarding:** the payload is built from closed input. No caller
  string reaches `domain`, `service` or the target type. `call_service` stays
  refused.
- **No fallback:** direct HA gateway only. There is no ha-mcp delegation and no
  route to `call_service`.
- **Provider admission:** signed profile required. Until signed, refusal
  happens before HA I/O.
- **Category forgery:** `entity_category` cannot be set through
  `config/entity_registry/update` (§3.1). An integration (including MQTT
  discovery) can still label an entity config. The domain and service
  allowlist, not the category, bounds what is sent.
- **Indirect targets:** only one exact `entity_id`. Device, area, floor and label
  targets are never sent.
- **Audit:** create, approve, apply, readback and rollback are audited with the
  plan hash, registry `id`, `device_id`, prior value, requested value and
  outcome. Option strings are bounded (255) and sanitized as in HAMCP-145.
- **Bounded output:** `options` list at most 256 entries of at most 255
  characters. Device name is bounded.
- **Secrets:** none read or added.

## 12. Build plan (after Josh's decisions)

1. Build PR on the 2.6.0 line (protected Engineering runtime path, named and
   authorized in that task):
   `E/governance/device_configuration.py` (gateway, payload builder, baseline),
   `E/governance/device_configuration_planning.py` (closed input), the routes
   table and constants in `E/ha_core_readmission/routes.py`, the new profile in
   `E/ha_core_readmission/profiles.py`, the tool in `E/tools/`, the routing
   entry in `E/providers/routing.py`, and the capabilities entry. Tests per §8.
2. Compute and record the new fingerprint. Assert that the 32 existing
   fingerprints are unchanged.
3. Signing: Josh's one-tap `extend-capabilities` for 2026.11.0, batched with
   other 2.6 profiles in one sequence step (§6). This is a separate,
   owner-authorized action.
4. Release and deployment: separately authorized.
5. Deployed acceptance per §9 on household-chosen devices.

**2.5.0 overlap:** the build will touch `E/ha_core_readmission/routes.py`,
`profiles.py`, `E/providers/routing.py`, `E/capabilities.py` and
`E/tools/registry.py`. In-flight 2.5.0 PRs (#386 HAMCP-327, #387 HAMCP-288,
#385 HAMCP-319, HAMCP-290, #384 HAMCP-309 Part 3) may touch some of these. 2.5.0
wins shared files. The build catches up by merging `main` (never a rebase)
after 2.5.0 lands. This design PR itself shares no file with them.

## 13. Open questions — pending Josh

| # | Question | Why it matters | Design default if unanswered |
|---|---|---|---|
| Q1 | **Household use-case list.** Which devices and which configuration settings (for example power-on behavior on which plugs, child lock on which devices), on which integrations? | The register says it is needed. It decides whether the first slice should be narrowed to named platforms, and which devices to use for deployed acceptance | Open question — pending Josh. Build does not start without it |
| Q2 | Is `number.set_value` in the first slice? Proposed exactness rule if yes: the value must be within min/max, aligned to `step` from `min`, expressed in the entity's state unit, and readback must equal it exactly; otherwise refuse or `verification_mismatch` | Many config settings are numeric, but Core clamps, converts units and does not enforce `step` | Deferred to a second increment |
| Q3 | `control_power` and config-category switches: (a) leave, (b) refuse and route to the new plan, or (c) disclose only (§5.10) | Today a child lock is one fast call away with no rollback. (b) and (c) change a shipped signed path | (a) leave unchanged |
| Q4 | Signing placement and timing: batch the new profile with the other 2.6 profiles in one 2026.11.0 `extend-capabilities` step, or sign on its own | Fewer signing taps versus independent timing | Batch with the other 2.6 profiles |
| Q5 | Per-plan bounds: one entity per plan, or up to N (for example 8) entities of one domain on one device | Multi-entity plans are non-atomic. Readback and rollback per step | One entity per plan |
| Q6 | Should some settings (for example power-on behavior on named critical loads) need the existing safety-critical acknowledgement on the approval page (`E/approval_web.py:1986`)? | Adds a second confirmation for high-consequence settings | Disclosure only (§5.6), no extra acknowledgement |
| Q7 | Final names for the tool (`create_device_configuration_plan`) and profile (`core.device_configuration_entity_operation`) | Names enter the signed contract and the public schema | Proposed names |

Stop condition recorded: Q3 options (b) and (c) are scope changes to a shipped
signed path. This design does not pursue them without Josh's decision.
