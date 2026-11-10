# HAMCP-297: Device-Configuration Entities as Ordinary Typed Control (2.6.0 Design)

Status: **design only. Josh decisions of 2026-11-08 recorded (§15): the
change path is switch-like ordinary typed control (`control_device_configuration`),
not a plan. The Codex design review 2026-11-08 corrections are kept and
reshaped where item 25 required it (§14). Q8 (empty first-build safety
trigger) and Q9 (compiled `(domain, platform, translation_key)` allowlist) are
decided, and the first build is narrowed to three source-confirmed Matter
target kinds (B14–B17, §4.1). Q10 (breadth of the power-on triple) is
decided (B17); nothing is pending Josh (§13.2). The Cursor full review of
2026-11-09 at `84f0c5be` (Ready after corrections) is corrected in this
revision: F1–F3, L1 and L2, with no decision reopened (§16).** This
document changes no runtime code, schema, tool registration, policy, workflow,
signed registry, release declaration or version. It is the one new file in its
pull request.

- Register: HAMCP-297.
- Target release line: 2.6.0 (`release/2.6.0`).
- Base: `origin/release/2.6.0` `7246f2d21693f039504542574569f03b71476489`
  (HAMCP-331, Engineering `2.4.2-beta.7`). Every `E/` line number below is at
  this base unless it is marked `@1c4c32b3`. Lines marked `@1c4c32b3` are at
  the `release/2.6.0` head that carries HAMCP-327 (control_helper fail-to-ask)
  and HAMCP-311 (control_helper); the `E/fan/`, `E/power/`, `E/f3/`,
  `E/approval_web.py`, `E/tools/power.py` and `E/tools/fan.py` files are
  identical at both revisions (`git diff 7246f2d2 1c4c32b3` on those paths is
  empty).
- Pinned Home Assistant Core: `2026.11.0`, commit
  `74cf5a49c63ed8d4d87717844bf14f79c28c9cea` (tree
  `530241f5976abdfbea2fa1fa51e0ddc7a4d42560`, archive sha256
  `85f3c42c9f77aa45e01faff4471007beadf5cb50c4324f496a239139eec70ff1`). The
  binding is in `scripts/core_lane_bindings.py:62` and
  [`evidence/hamcp315/core-sources/2026.11.0.json`](evidence/hamcp315/core-sources/2026.11.0.json).
  The 2026-11-08 corrections re-read the cited Core files from that exact
  archive after recomputing its digest locally (ADR-021); they are local
  pinned-source reads, not a fresh remote observation. The item-25 rework
  cites no new upstream line.

Engineering paths are relative to `hass_mcp_engineering_beta/ha_mcp_engineering/`
(written `E/`). Upstream citations are read-only public source at the pinned
commit (ADR-021). In the text, `core:<path>:<lines>` means
`https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/<path>#L<lines>`.
§10 lists the full links. Upstream integration examples (Tuya) show that a
kind of entity exists at the pinned Core. They are not evidence about Josh's
devices. The Matter citations in §4.1 are different: they are the source of
the descriptors the architect's registry read found on the household
devices, and they fix each admitted `translation_key` and `entity_category`.
The `tapo_control` citations are at its latest release, 7.2.13 (tag commit
`6d2c8adb81f3551070ec62fd9590f26254df145a`), because the installed version
is unknown (§4.1).

Nothing here came from a live Home Assistant instance or a deployed MCP
endpoint through this design. No household device, entity or setting was
probed by it. The household targets in §4.1 come from the architect's
read-only inventory and read-only registry read, both recorded in the
register (HAMCP-297, 2026-11-08), and from Josh's decisions. The registry read
gave each target's platform and integration descriptor but not its
`translation_key` or `entity_category`; those come from integration source
at the pinned versions (§4.1).

## 1. Register outcome and binding decisions

Register Outcome (HAMCP-297, verbatim):

> Configuration-category entities on devices (for example power-on behavior and
> child lock on plugs) can be changed through a typed, bounded, governed path
> with exact targets, stale-state protection, readback and rollback, instead of
> by hand.

| # | Decision or constraint | Source | Status |
|---|---|---|---|
| B1 | Feature lane position 3, after HAMCP-253, 267 and 264 | Register Next Action (recorded there as the 2026-11-03 ranking) | Register record |
| B2 | The path is typed, bounded and governed, with exact targets, stale-state protection, readback and rollback | Register Outcome | Register record. "Governed" is met by ordinary typed-control governance (signed admission, closed input, entity locks, audit, exact readback, holds, identity-bound undo) per B8, not by a plan |
| B3 | Household use cases (build and acceptance targets): **power-on behavior after outages, child/physical lock, sensor tuning (sensitivity, timeouts). Not indicator LEDs.** | Register | **Josh direction (register 2026-11-07; confirmed in-session 2026-11-08)** |
| B4 | **Additive profiles only.** No existing signed profile is version-bumped or replaced; existing fingerprints stay byte-identical | Register | **Josh direction (register 2026-11-07; confirmed in-session 2026-11-08)** |
| B5 | New 2.6 profiles are signed for **Core 2026.11.0 only**, in **one combined 2.6 signing** (one `extend-capabilities` extension of the existing 2026.11.0 entry). No 10.x targets, no separate or phased 2.6 signing | Register | **Josh direction (register 2026-11-07; confirmed in-session 2026-11-08)** |
| B6 | Design document only, one new file, no runtime or protected change | Task prompt and 2.6.0 brief | Task constraint |
| B7 | Mechanism keys on `entity_category == "config"` entities of typed domains | Task prompt | Task constraint, refined in §3.1 and §4.1 |
| B8 | Device-configuration changes are **switch-like control, like `control_helper`**: a direct typed control with exact readback and undo, a chat confirmation only when safety-critical, **no approval panel and no plan**. `create_device_configuration_plan` is replaced. The signed profile stays additive | Josh, in session | **Josh decision (in-session 2026-11-08), item 25** |
| B9 | First-build targets (E1): power-on behavior selects (`on`/`off`/`toggle`/`previous`) on 5 plugs (garage camera smart plug, master bedroom TV Eve, kitchen Eve, garage beer fridge Eve, network cabinet Eve); child-lock switches on 4 Eve plugs; Aqara back-porch light sensor sensitivity (`select`, and `number` 1..3; one stale unavailable duplicate exists); Tapo camera digital sensitivity `number` 1..100. **Not in the first build:** Tapo camera detection selects (`high`/`normal`/`low`/`off`), deferred until there is a rule for when turning detection off needs a safety confirmation. Live acceptance uses the master bedroom TV plug or the kitchen plug, **never** the beer fridge or network cabinet. Every target must be confirmed from the entity registry (`entity_category == "config"`) before build and acceptance | Architect's read-only inventory (register HAMCP-297, 2026-11-08) and Josh | **Josh decision (in-session 2026-11-08), item 1**. Historical target list: narrowed by B15 and B16 (the Tapo digital-sensitivity `number` and the Aqara sensitivity `select` are dropped); the acceptance restriction is unchanged |
| B10 | One entity per change | Josh, in session | **Josh decision (in-session 2026-11-08), item 2** (Q5 option (a)) |
| B11 | Power-on behavior, including on critical loads, is **disclosure only**; it does not need a safety-critical acknowledgement | Josh, in session | **Josh decision (in-session 2026-11-08), item 3** (Q6 option (a)) |
| B12 | `control_power` keeps accepting config-category switches (for example child lock), serialized against this path by the shared entity lock | Josh, in session | **Josh decision (in-session 2026-11-08), item 4** (Q3 option (a)) |
| B13 | Profile name `core.device_configuration_entity_operation` is accepted. The tool name `create_device_configuration_plan` is superseded by B8 | Josh, in session | **Josh decision (in-session 2026-11-08), item 5** (Q7) |
| B14 | No safety confirmations in the first build. The `confirmation_required` mechanism ships with an **empty trigger**, and the build PR proves it end to end with a test-only trigger: `dispatch: no_dispatch`, `task_created: false`, no send; a later call with `acknowledge_safety_critical` dispatches; audit `safety_gate` / `safety_confirmation`; `external_approval: false` | Josh, in session | **Josh decision (in-session 2026-11-08)** (Q8 option (a)) |
| B15 | The first build admits targets through a compiled **`(domain, platform, translation_key)` allowlist inside the signed contract**. Each `translation_key` is confirmed from integration source at the pinned Core (2026.11.0, commit `74cf5a49`) and at the pinned integration version. Any target with no `translation_key` is **dropped** from the first build | Josh, in session | **Josh decision (in-session 2026-11-08)** (Q9 option (a)) |
| B16 | Follow-up after the source read: (i) **drop the Aqara sensitivity `select`**, because its triple `(select, matter, sensitivity_level)` is shared with the smoke/CO alarm smoke-sensitivity select; (ii) **accept the breadth** of the two generic triples kept, `(switch, matter, child_lock)` and `(number, matter, sensitivity_level)`, and disclose it (§4.1, §11) | Josh, in session | **Josh decision (in-session 2026-11-08)** (follow-up to Q9) |
| B17 | **Accept and disclose the breadth of `(select, matter, startup_on_off)`**, kept as is with no device-type filter. The triple admits the power-on behavior of any Matter endpoint that exposes `OnOff.StartUpOnOff` (`core:components/matter/select.py:426-450`, required attribute `:447`), for example Matter lights and wall switches, not only the 5 plugs. Disclosed in §4.1 and §11. It does not block the contract freeze, and the B15 three-triple contract is unchanged | Josh, in session | **Josh decision (in-session 2026-11-08)** (Q10 option (a)) |

Everything else in this document is a design proposal, not a recorded decision.
In particular the tool name `control_device_configuration` (§5.1) is this
design's proposal, chosen to match the `control_*` family; it is not a Josh
decision. Anything still unresolved is listed in §13.2 as pending Josh.

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
  `CORE_TYPED_OPERATION_PROFILES`, `E/ha_core_readmission/profiles.py:425-462@1c4c32b3`).
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
(`core:components/tuya/switch.py:100-104`) is, today, an ordinary
`control_power` target: exact readback, but no prior-value capture and no
undo. Its docstring says "a switch may power a critical load. Exact
owner-authorized actions remain supported despite those consequences" and
"Restoration is a separate owner-requested action after reconciliation"
(`E/tools/power.py:29-33`). `control_power` is signed under
**`core.typed_power_operation`** (generated as `"core.typed_" + family +
"_operation"` with family `power`, `E/ha_core_readmission/profiles.py:427-451`):
`light`/`switch`, `turn_on`/`turn_off`, provider boundary
`reviewed_ha_mcp_typed_power`, fingerprint
`sha256:9242b795a2d5ed3deeb3b8ea46fba13fc299c14fb274e3b29ed97a30b607001a`.
It stays as it is (B12, §5.10).

### 2.3 Execution locks `control_power` already takes

- `PowerAdapter` subclasses `FanAdapter` (`E/power/adapter.py:41`) and inherits
  `FanAdapter.lock_requests` (`E/fan/adapter.py:85-93`): an **exclusive
  `entity:<entity_id>`** resource lock, a shared `home_assistant:core` lock and
  a shared ha-mcp provider lock.
- `PowerService` subclasses `FanService` (`E/power/service.py:26-27`). Its
  records live under their own namespace, but its locks use
  `FanLockStore(root, ...)` on the **un-namespaced** root
  (`E/fan/service.py:105-118`, "Exact shared lock namespace"). The root is
  `settings.governance_path` (`E/power/service.py:54`).
- `DurableLockStore` keeps every lock under `<root>/f3-operation-locks-v1`
  (`E/f3/locks.py:41`, `:163-192`). The F3 runtime's `RuntimeLockStore` uses
  the same `storage_root` (`E/f3_runtime/runtime.py:1941-1946`), which is
  `settings.governance_path` (`E/governance/runtime.py:262`). Power, fan,
  helper and governed F3 plans therefore already share one durable lock store.
- Expiry never frees a key: "Expired leases remain conflicts until task-aware
  stale recovery explicitly releases, transfers, or holds them"; a conflict
  hold always blocks (`DurableLockStore.holder_blocks`, `E/f3/locks.py:329-342`).
- After an unresolved send, the power/fan path promotes its lock set to a
  selective conflict hold that retains only `entity:<entity_id>`
  (`E/fan/locks.py:14-21`, reason `manual_review_unresolved_dispatch` at
  `:23-53`; settled check `:55-65`).
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

### 2.5 The shape template: ordinary typed control (`control_helper`, `control_power`, `control_fan`)

Item 25 (B8) makes the shipped ordinary typed controls the template. Every
pattern below is reused; the right column says what this design adds or
changes. The source is read-only here (protected path).

| Pattern | Shipped source | Reuse in this design |
|---|---|---|
| Tool signature: exact `entity_id` pattern, closed `action` `Literal`, `operation_id`, optional `value`, `acknowledge_safety_critical: StrictBool = False` | `E/tools/helper.py:11-18@1c4c32b3`; `E/tools/power.py:11-52` | Same shape (§5.2). `value` is a string only, for numeric exactness (D3); `undo_task_id` added for undo (§5.11) |
| "Ordinary control, like a switch: no plan or panel approval" | `E/tools/helper.py:21@1c4c32b3` | Same (B8) |
| `operation_id` `^[0-9]{10}-[a-f0-9]{32}$`; five-minute freshness; `task_id` derived from it | `E/tools/helper.py:15@1c4c32b3`; `E/helper_value/contracts.py:151-152`, `:172-173@1c4c32b3` | Same |
| Same id with changed arguments refuses (`*_operation_id_rebound`); stale id refuses (`*_operation_id_expired`); receipt capacity refuses (`*_receipt_capacity_exhausted`); a repeated call only reconciles | `E/fan/service.py:171-203`, `:275-312` | Same; replaces plan hash, approval and expiry (§5.2) |
| Durable receipt `<governance_path>/<namespace>/<task_id>.json` (≤16384 bytes, ≤4096 records), atomic temp-file + fsync + `os.replace` + directory fsync, under the `.declarations.lock` flock; signed model `<ns>-core-bound-v2` with keys `model`, `request`, `baseline`, `prepared_hash`, `provider_contract`, `core_binding` | `E/fan/service.py:31`, `:120-169` | Same receipt bytes and checks, new namespace `ordinary-device-configuration-v1`; `save` is overridden to write the undo claim in the same single transaction (§5.11.2, §5.13) |
| Preparation before declaration, then a specialization hook (`before_declaration`) whose refusal leaves no task and proves no dispatch | `E/fan/service.py:253-258`, `:275-312`; `E/fan/adapter.py:70-83` | The confirmation gate (§5.6) uses this hook. The undo claim does **not**: the hook runs outside the `.declarations.lock` flock, so the claim is written in the `save` override instead (§5.11.2, review F3) |
| Shared lock store on the un-namespaced root; exclusive `entity:<entity_id>` | `E/fan/service.py:105-118`; `E/fan/adapter.py:85-93` | Same store and same exclusive key; different shared keys (§5.7) |
| Locked preflight re-prepares and compares the fingerprint (`*_state_changed` → `PREFLIGHT_REJECTED`); an already-reached state is `SUCCEEDED_VERIFIED` `desired_state_already_reached` with no dispatch | `E/fan/adapter.py:95-106` | Same; the fingerprint is the composite observation of §5.7 |
| Dispatch `once()`: re-read and compare with the prepared fingerprint, then the executor's `before_dispatch`; callback failure → `FAILED_PRE_DISPATCH` (not sent); provider `call_not_sent` → `DISPATCH_FAILED_CONFIRMED`; any other post-send exception → `DISPATCH_INDETERMINATE`; success → `OBSERVING` | `E/fan/adapter.py:108-158` | Same outcome mapping; `dispatch` is overridden so the `once()` re-read is the full §5.7 composite (state, registry entry, device), not the shipped state-only digest at `:115-119` (§5.7, §5.8, review F2) |
| Executor `before_dispatch`: revalidates the lock handle (lease and generation) and commits durable dispatch intent before the send; lease renewer | `E/f3/executor.py:619-665` (`validate_handle` at `:638`, `commit_dispatch_intent` at `:660`), `:697-702` | Same: this is the D1 lease/fencing check before send |
| Executor timing `LockTiming(30, 10, 0)`, `ExecutorTiming(180, 120, 6, 6)`, `final_read_at_deadline=True`; inline polls `(1, 1, 2, 2, 2, 2)` within 10 s | `E/fan/service.py:34-35`, `:77-82`, `:205-211` | Same |
| Observe / verify / recover: readback fingerprint is the digest of the observation; recover is read-only observe | `E/fan/adapter.py:160-179` | Same; observation is the composite projection (§5.9) |
| Receipt facts: `redispatch_prohibited: true`, `physical_feedback_verified: false`, consequence coverage `incomplete`, retained locks, dispatch phase | `E/fan/service.py:334-371` | Same |
| Selective `entity:` hold on unresolved dispatch; reconciliation to a terminal hold | `E/fan/locks.py:14-65` | Same (§5.12) |
| HAMCP-192 typed hold release: read-only review, then an owner release recorded in an additive `hold-releases/<task>.json` sidecar (`typed-hold-release-v1`, ≤4096 bytes) with audit and dispatch `none`; Ingress page kinds `TYPED_HOLD_KINDS = ("fan", "power")` | `E/fan/service.py:40`, `:504-700`, `:766-806`; `E/approval_web.py:72`, `:805-840` | Same release path; kind `device_configuration` added to `TYPED_HOLD_KINDS` and given its own branch in `_typed_service` (`E/approval_web.py:814-824`), which otherwise resolves every non-fan kind to the power service (§5.12, review L2). Observed: `helper` is not in `TYPED_HOLD_KINDS` at `1c4c32b3` |
| Signed-only Core authority (no legacy compiled fallback); preparation refuses when no core binding exists | `E/helper_value/service.py:41-60@1c4c32b3`; `E/helper_value/adapter.py:19-39@1c4c32b3`; `E/fan/authority.py:6-45` | Same (§5.3) |
| Chat confirmation: `acknowledge_safety_critical`, `CONFIRMATION = "model_conveyed_chat_confirmation"`, `confirmation_required` payload with `dispatch: no_dispatch`, `task_created: false`, no `task_id`; gates `acknowledged` / `acknowledged_evidence_unavailable` / `not_safety_critical`; audit `safety_gate`, `safety_confirmation`, `external_approval: false` | `E/helper_value/safety.py:27-56`, `:158-223@1c4c32b3`; `E/helper_value/service.py:68-123@1c4c32b3`, `:182-211@1c4c32b3`; `E/tools/helper.py:42-43`, `:55-59@1c4c32b3` | Mechanism and argument reused verbatim; the first-build trigger is empty (B14, §5.6) |
| Status vocabulary `succeeded_verified`, `no_change`, `changed_unverified`, `verification_failed`, `confirmation_required`, `refused`, `provider_unavailable`, `in_progress` | `E/helper_value/service.py:133-147@1c4c32b3` | Same |
| Policy: category `ordinary_runtime_action`, risk `physical_action`, `external_approval_required: False`, `direct_write_allowed: False`; Core independence entry; typed execution provider capability | `E/capabilities.py:223-239@1c4c32b3`; `E/ha_core_readmission/routes.py:184-186`, `:488-497@1c4c32b3`; `E/providers/routing.py:150-151@1c4c32b3` | Same class (§5.3, §7) |

What the shipped controls do **not** have, and this design must add:

- **Undo.** `control_power`, `control_fan` and `control_helper` have no undo;
  `control_power` says restoration is a separate owner-requested action. B8
  requires undo, so §5.11 specifies a new identity-bound undo built from the
  receipt writer and the HAMCP-192 sidecar pattern.
- **Registry identity.** None of them reads the entity or device registry.
  This family must (§5.5), which is why the provider is the direct Home
  Assistant API rather than the reviewed ha-mcp typed provider (§5.3).

HAMCP-145 (`create_input_select_option_plan`) is no longer the template. Its
`helper:` lock identity and its value-only rollback rule
(`E/governance/service.py:4790-4801`) stay rejected, as the Codex D1 and D2
corrections already said.

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
| `select.select_option` | **Yes** | Absolute, idempotent target value; options are enumerated in state; readback is exact (`state == option`); the prior option, if still offered, is a valid undo target. Covers power-on behavior (and, in a later kind, enumerated sensitivity; none is admitted in the first build, §4.1) |
| `select.select_first/last/next/previous` | **No, never sent** | Relative moves; the outcome depends on the current state and on option order |
| `switch.turn_on` / `switch.turn_off` | **Yes** | Absolute, idempotent; readback is exact (`on`/`off`); the prior state is the undo target. Covers child/physical lock |
| `switch.toggle` | **No, never sent** | Relative; the outcome depends on the current state |
| `number.set_value` | **Yes, under the exactness contract in §5.5.1** | Required for numeric sensitivity, timeouts and delays (§3.4). Core clamps, may convert units and does not enforce `step` (§3.5), so Engineering proves identity conversion, enforces bounds and step before sending, and compares readback numerically |
| `button.press` | **No** | An action, not a setting. State is written before the press, so readback cannot prove the effect, and there is no prior value to restore (`core:components/button/entity.py:54-74`). `identify`/`restart`/`update` device classes are actions (`button/const.py:21-26`) |
| `update.*` | **No, never sent** | Firmware install. Config category by default but not a setting |
| `light` (indicator LEDs and similar) | **No** | Indicator LEDs are excluded by B3 |
| `text`, `time`, others | **No** | Not one of the three approved use cases. A later increment needs its own readback analysis and its own additive profile (B4) |

The first slice is **`select.select_option`, `switch.turn_on`/`switch.turn_off`
and `number.set_value` on registry-proven config-category device entities
whose target kind is admitted for the first build (§4.1).**

### 4.1 First-build targets (B9, B15, B16, B17) and target-kind admission

Josh's item-1 decision (B9) named the first-build targets from the
keyword-based inventory. That list is kept below as history. Q9 (B15) and its
follow-up (B16) narrow it to three source-confirmed Matter target kinds, and
B17 accepts the breadth of the power-on triple.

#### Historical (B9, item 1): the inventory list

| Use case (B3) | Domain | Household targets (from the keyword-based inventory) | Values seen in the inventory | Status after B15/B16 |
|---|---|---|---|---|
| Power-on behavior after outages | `select` | 5 plugs: garage camera smart plug, master bedroom TV Eve, kitchen Eve, garage beer fridge Eve, network cabinet Eve | `on`, `off`, `toggle`, `previous` | Admitted |
| Child/physical lock | `switch` | 4 Eve plugs | `on`/`off` | Admitted |
| Sensor tuning, enumerated | `select` | Aqara back-porch light sensor sensitivity | (not recorded) | **Dropped** (B16 (i)) |
| Sensor tuning, numeric | `number` | Aqara back-porch light sensor sensitivity (1..3); Tapo camera digital sensitivity (1..100) | integer ranges | Aqara admitted; Tapo **dropped** (B15) |
| **Excluded from the first build** | `select` | Tapo camera detection selects | `high`, `normal`, `low`, `off` | Still excluded |

#### Registry confirmation (done 2026-11-08, read-only, by the architect)

The architect's read-only registry read (register HAMCP-297, 2026-11-08)
found the plugs and the Aqara sensor on platform **`matter`**, with the
integration descriptors `MatterStartUpOnOff` (power-on selects), `EveChildLock`
(child-lock switches), `AqaraOccupancySensorBooleanStateConfigurationCurrentSensitivityLevel`
(the Aqara select) and `BooleanStateConfigurationCurrentSensitivityLevel` (the
Aqara number). It found the camera numbers on platform **`tapo_control`** (a
custom integration). That read did not expose `translation_key` or
`entity_category`, so both come from integration source (B15):

- Matter is in-tree, so its integration version is Core 2026.11.0 itself
  (`core:components/matter/manifest.json`, requirement
  `matter-python-client==1.5.5`). The keys below are read at commit
  `74cf5a49`.
- The installed `tapo_control` version is **unknown**: the registry read did
  not expose it. The source read is at its latest release, 7.2.13 (tag commit
  `6d2c8adb81f3551070ec62fd9590f26254df145a`, 2026-10-23). Nothing from it is
  admitted, so the unknown version changes no admitted kind.

#### Compiled allowlist (B15, B16, B17): exactly three admitted triples

| Triple `(domain, platform, translation_key)` | Household targets | Pinned source | Contract facts from source | Breadth beyond the household targets (disclosed) |
|---|---|---|---|---|
| `(select, matter, startup_on_off)` | Power-on behavior on the 5 plugs | `core:components/matter/select.py:426-450` (`MatterStartUpOnOff`); name "Power-on behavior" (`matter/strings.json:394-395`) | `entity_category=CONFIG`; options `on`/`off`/`toggle`/`previous` (a device `None` reads as `previous`); the only Matter select with this key | Not vendor- or device-type-gated: the one required attribute is `OnOff.StartUpOnOff` (`:447`), so any Matter endpoint that exposes it is admitted (for example Matter lights and wall switches as well as plugs). Accepted and disclosed, with no device-type filter (B17, Q10 option (a)); power-on behavior stays disclosure only on every admitted entity (B11) |
| `(switch, matter, child_lock)` | Child lock on the 4 Eve plugs | `core:components/matter/switch.py:536-545` (`EveChildLock`); name "Child lock" (`matter/strings.json:737-738`) | `entity_category=CONFIG`; `MatterNumericSwitch` | Also admits `EveTrvChildLock` (`switch.py:472-490`: Eve vendor 4874, thermostat `KeypadLockout`), the Eve thermostat valve's keypad lock. Accepted and disclosed (B16 (ii)) |
| `(number, matter, sensitivity_level)` | Aqara back-porch sensor sensitivity | `core:components/matter/number.py:539-563` (`BooleanStateConfigurationCurrentSensitivityLevel`); name "Sensitivity" (`matter/strings.json:283-284`) | `entity_category=CONFIG`; `MatterRangeNumber`, `native_min_value=1`, `native_step=1`, maximum from the device's `SupportedSensitivityLevels` attribute, `device_to_ha` is x+1, slider mode, no device class; the only Matter number with this key | Any Matter `BooleanStateConfiguration` device with the sensitivity-level feature (`featuremap_contains`, `:559-561`), for example water-leak, rain and contact sensors, not only Aqara. Accepted and disclosed (B16 (ii)). The range (1..3 in the inventory) is device-supplied, so it is confirmed from state and registry capabilities at runtime (§5.5.1), never compiled |

#### Dropped from the first build

Each refuses `device_configuration_target_kind_not_admitted` after the
registry read, before any state read or send.

| Target | Kind | Reason | Pinned source |
|---|---|---|---|
| Aqara back-porch sensitivity `select` | `(select, matter, sensitivity_level)` | B16 (i). The triple is shared by `SmokeCOSmokeSensitivityLevel`, the smoke/CO alarm smoke-sensitivity select (`select.py:451-471`, not vendor-gated, enabled by default), and by three legacy vendor selects: the Aqara contact sensor (`:620-648`, vendor 4447 product 8194), the Aqara occupancy sensor (`:649-678`, vendor 4447 products 8197 and 8195, options `low`/`standard`/`high`) and Heiman (`:679-705`). Admitting the triple would make smoke-alarm sensitivity reachable. The vendor selects are disabled by default (`entity_registry_enabled_default=False`) and are kept "until HA 2026.11.0" for migration to the generic number (`:617-619`) | `core:components/matter/select.py` |
| Tapo camera digital-sensitivity `number` (1..100) | `(number, tapo_control, null)` | B15: no `translation_key`. `TapoMotionDetectionDigitalSensitivity` sets none; the only `translation_key` in the whole integration is `light.py:99` (`nightlight`) | `tapo_control` 7.2.13 `number.py:290-345` |
| Tapo camera detection selects | `(select, tapo_control, null)` | Excluded by B9 (no rule yet for when turning detection off needs a safety confirmation), and also no `translation_key` (B15) | `tapo_control` 7.2.13 |

Adding Tapo later needs an upstream `translation_key`, or a new admission rule
decided by Josh, and a new additive profile (B4).

**Rejected alternative: descriptor-key admission.** Matter builds `unique_id`
as `device_id-endpoint-<descriptor key>-cluster-attribute`
(`core:components/matter/helpers.py:123-134`), so admission by descriptor key
could separate `SmokeCOSmokeSensitivityLevel` from the Aqara select, or
`EveChildLock` from `EveTrvChildLock`. It is not used: it admits on a parse of
`unique_id` instead of the `translation_key` field B15 names, so it would
change the Q9 contract. Only Josh can adopt it.

Consequences for the contract:

1. **Registry confirmation at runtime and before acceptance.** Platform and
   descriptor were confirmed from the registry, and `translation_key` and
   `entity_category` from source, on 2026-11-08 (above). Every call still
   proves `entity_category == "config"`, `platform` and `translation_key` from
   `config/entity_registry/get` (§5.5 items 2 and 5). A household entity whose
   registry entry disagrees with the cited source (for example a different
   key under a later integration version) refuses and is not a target. For
   `number` the `min`/`max`/`step`, unit and `original_device_class` are
   runtime facts checked by §5.5.1. A target that is not `config` category is
   not a target of this family (it may still be a `control_power` target,
   B12).
2. **Dropped kinds are unreachable, not merely unlisted.** The allowlist is
   embedded in the profile's response contract (§6) as exactly the three
   triples above, so smoke/CO smoke sensitivity, the legacy vendor sensitivity
   selects, every `tapo_control` entity and every other config-category kind
   refuse. The build checks the compiled list against the cited source at the
   pinned versions (§9.1); if they disagree, the build stops and reports
   instead of guessing a discriminator. Adding a kind is a new additive
   profile (B4).
3. **The stale duplicate.** The Aqara sensor has one stale `unavailable`
   duplicate. If it is a `number` of the admitted kind, §5.5 item 6 refuses it
   (`device_configuration_entity_unavailable`, reads only, no send); if it is
   the dropped `select`, item 5 refuses it first. Test T3 (§8).
4. **Power-on `toggle` is a value, not the switch service.** On the
   power-on selects, `toggle` is an option string sent through
   `select.select_option`; `switch.toggle` stays never-sent (§5.4).
5. **Live acceptance targets (B9).** Only the master bedroom TV plug or the
   kitchen plug carries deployed acceptance; the garage beer fridge and network
   cabinet plugs are never used for acceptance (§9.3). This restricts
   acceptance, not the product: an owner-authorized change on those plugs is
   ordinary control, with the outage disclosure of §5.6.

## 5. Proposed design

### 5.1 Tool and family

- New ordinary typed control tool: **`control_device_configuration`** (this
  design's proposed name, consistent with `control_power`, `control_fan` and
  `control_helper`; B13 superseded `create_device_configuration_plan`).
- Service: `DeviceConfigurationService(FanService)`, namespace
  `ordinary-device-configuration-v1`, the same lifecycle as `PowerService`
  and `HelperService` (§2.5). Adapter: `DeviceConfigurationAdapter(FanAdapter)`.
  Runtime: a `DeviceConfigurationRuntime` configured like
  `HelperRuntime.configure` (`E/helper_value/service.py:288-291@1c4c32b3`).
- **No plan, no F3 identity, no approval.** There is no
  `set_exact_device_configuration_value` identity, no
  `_F3_DEVICE_CONFIGURATION_OPERATIONS` table, no
  `device_configuration_entity` configuration resource type, and no use of
  `approve_change_plan`, `apply_change_plan` or `rollback_change`. Undo is an
  action of the same tool (§5.11).
- Reconciliation and recovery use the existing `get_execution_task` path, as
  the other `control_*` tools do (`E/fan/service.py:260-273`).
- `control_power`, `control_fan` and `control_helper` are unchanged.

### 5.2 Inputs and outputs

Closed input, validated before any Home Assistant read (refusal
`device_configuration_invalid_value`, `device_configuration_action_not_supported`
or `device_configuration_field_not_supported`):

| Field | Rule |
|---|---|
| `entity_id` | Required. `^(select|switch|number)\.[a-z0-9_]{1,120}$` |
| `action` | Required. `Literal["select_option", "turn_on", "turn_off", "set_value", "undo"]`. `select_option` only for `select`; `turn_on`/`turn_off` only for `switch`; `set_value` only for `number`; `undo` for any of the three. A mismatch refuses `device_configuration_action_not_supported` |
| `operation_id` | Required. `^[0-9]{10}-[a-f0-9]{32}$`, the shipped semantics: generated once, five-minute freshness, same id and arguments only reconcile, changed arguments refuse, never a fresh id to retry uncertain work (`E/tools/helper.py:15`, `:24-27@1c4c32b3`) |
| `value` | `StrictStr`, required for `select_option` and `set_value`, forbidden otherwise. `select`: 1–255 characters, no control characters, compared exactly (case-sensitive, never stripped), as in `input_select_option_planning.py:62-70`. `number`: a canonical decimal string matching `^-?(0|[1-9][0-9]{0,8})(\.[0-9]{1,6})?$` (no exponent, sign only on non-zero values, no `NaN`/`Infinity`), which must round-trip exactly through an IEEE-754 double (§5.5.1). Unlike `control_helper`, which takes `StrictStr | StrictInt | StrictFloat`, numbers are strings here so that exactness (D3) is checked on the caller's literal, not on a float the client already rounded |
| `undo_task_id` | `^[a-f0-9]{32}$`, required when `action == "undo"`, forbidden otherwise. Names the original task to undo. `value` is forbidden with `undo`: the restore value comes from the original's recorded baseline (§5.11) |
| `acknowledge_safety_critical` | `StrictBool`, default `False`. Same meaning and rule as `control_helper`: set it to `true` only after the user confirms in chat in response to a `confirmation_required` result; never pre-emptively (§5.6) |

One entity per call (B10). No other field is accepted: no `reason`,
`expiration_minutes`, plan or approval fields, `service`, `domain`,
`service_data`, `target`, `cycle`, `option` alias or unit.

Output is the shipped ordinary-control receipt (§2.5) plus:

- `status`: one of `succeeded_verified`, `no_change`, `changed_unverified`,
  `verification_failed`, `confirmation_required`, `refused`,
  `provider_unavailable`, `in_progress` (the `control_helper` vocabulary);
- `target`: entity_id, domain, the target identity of §5.11.1, device name
  (bounded), `entity_category: "config"`, target kind;
- `baseline`: prior value and, for `select`, the bounded `options`; for
  `number`, `min`, `max`, `step`, `mode`, `unit_of_measurement`,
  `original_device_class` and the identity-conversion proof (§5.5.1);
- `requested_value` and the exact service it maps to (`select.select_option`
  with `{"option": value}`, `switch.turn_on`/`switch.turn_off` with no data,
  or `number.set_value` with `{"value": <number>}`);
- `disclosures`: §5.6, on every result including `no_change`;
- `undo`: after `succeeded_verified` of a forward change,
  `{"available": true, "undo_task_id": <task_id>, "restores": <prior value>}`
  with the rule that undo works only while the post-change observation is
  still exact (§5.11); otherwise `{"available": false, "reason": ...}`. An
  undo result says `"undone": true` only when it is `succeeded_verified`.

### 5.3 Routing, provider and admission

- **Provider:** the direct Home Assistant API under a new provider contract
  `direct-ha-exact-device-configuration-v1`. No ha-mcp delegation, no
  `call_service` reuse and no fallback; if the direct gateway is unavailable,
  the call refuses `provider_unavailable` before any send. This differs from
  `control_power`/`control_helper` (reviewed ha-mcp typed providers) for one
  reason: the target proof needs `config/entity_registry/get` and
  `config/device_registry/list` (§5.5), and the shipped typed providers expose
  neither. A direct-HA provider under a closed payload builder is the
  HAMCP-145 precedent (`E/capabilities.py:566-590@1c4c32b3`, provider
  `direct_home_assistant_state`, `direct_write_allowed: False`). The
  one-connector topology is unchanged: the client still sees only the
  Engineering endpoint.
- **Provider capability:** a new `ProviderCapability` typed-execution member
  beside `TYPED_HELPER_VALUE_EXECUTION` (`E/providers/routing.py:150-151@1c4c32b3`).
- **Admission:** a `CoreIndependence` entry like `control_helper`'s
  (`E/ha_core_readmission/routes.py:488-497@1c4c32b3`) bound to the new signed
  profile, and signed-only Core authority (no legacy compiled fallback), as
  `HelperCoreAuthority` (`E/helper_value/service.py:41-60@1c4c32b3`). The new
  profile name does not fit the `"core.typed_" + family + "_operation"`
  generator (`E/ha_core_readmission/profiles.py:425-462@1c4c32b3`), so the
  build adds an explicit `_contract(...)` for it and an explicit profile
  lookup in the authority instead of `operation_profile(self.family)`
  (`E/fan/authority.py:6-45`). Until the running Core release's signed entry
  covers it, every call refuses `device_configuration_core_authority_unavailable`
  before any HA I/O (replacing the plan-era `pending_signed_registry_admission`).
  Only Core 2026.11.0 is extended (B5); every other Core release keeps
  refusing.
- **Policy class:** category `ordinary_runtime_action`, risk
  `physical_action`, `external_approval_required: False`,
  `direct_write_allowed: False` (writes only through this typed contract), as
  the `control_power`/`control_helper` entries (`E/capabilities.py:223-239@1c4c32b3`).
  The exact catalog strings are fixed by the build PR.

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
`call_service` whose target is not exactly the one requested `entity_id` (no
`device_id`, `area_id`, `floor_id` or `label_id` targets).

### 5.5 Target proof

At preparation (before the declaration), again in the locked preflight, and
for the state part again in `once()` immediately before the send (§5.7), the
gateway requires all of the following. Any failure refuses before dispatch.

1. `config/entity_registry/get` returns an entry whose `entity_id` equals the
   request. `not_found` refuses `device_configuration_entity_not_registered`.
2. `entity_category == "config"`. `diagnostic` or `null` refuses
   `device_configuration_not_config_category`. A `null` category switch is the
   `control_power` domain (B12). A diagnostic entity is read-only by intent.
3. `device_id` is a non-empty string, and that id is present in
   `config/device_registry/list`. Otherwise refuse
   `device_configuration_no_device`. This excludes helpers, template entities
   and other device-less entities.
4. Entity `disabled_by` is `null` and the device's `disabled_by` is `null`.
   Otherwise refuse `device_configuration_target_disabled`.
5. The entity domain is exactly `select`, `switch` or `number`, matching the
   input pattern, and the registry target kind `(domain, platform,
   translation_key)` is exactly one of the three compiled triples
   `(select, matter, startup_on_off)`, `(switch, matter, child_lock)` or
   `(number, matter, sensitivity_level)` (B15, B16, §4.1). A `null`
   `translation_key` never matches. Otherwise refuse
   `device_configuration_target_kind_not_admitted`.
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
   `device_configuration_baseline_not_restorable`, because B2 and B8 require
   undo and there would be no valid value to restore.
7. The requested value is valid for the domain: a `select` value in
   `options` (else `device_configuration_value_not_offered`); a `number`
   value passing §5.5.1 rules 4–5 (else the refusal named there).
8. If the requested value equals the current state (numerically for
   `number`), the call is a **no-op**: it is declared, the locked preflight
   returns the shipped `desired_state_already_reached` with no dispatch, and
   the status is `no_change` (`E/fan/adapter.py:95-106`;
   `E/tools/helper.py:37@1c4c32b3`). A `no_change` task has nothing to undo.

Preparation records the target identity of §5.11.1 and the composite
observation fingerprint of §5.7 in the receipt baseline. A later rename of the
entity_id, a re-registration under a different registry id, or a replaced
device therefore breaks the binding and is caught as stale.

#### 5.5.1 Numeric exactness contract (`number` only)

These rules are part of the one new profile's response contract (§6), fixed
before its first signature.

1. **Bounds attributes.** The state's `min`, `max` and `step` are finite JSON
   numbers with `min < max` and `step > 0`. Each is read as
   `Decimal(repr(float))` (shortest round-trip form). The registry entry's
   `capabilities` must carry the same `min`, `max` and `step`; a disagreement
   refuses `device_configuration_number_bounds_inconsistent`.
2. **Identity-conversion proof.** Let `dc` be the registry
   `original_device_class` (§3.5). The call refuses
   `device_configuration_number_unit_conversion` when `dc` is in the pinned
   Core 2026.11.0 `UNIT_CONVERTERS` set (`core:components/number/const.py:651`
   onward, embedded verbatim in the profile) **and** either the registry
   `options` carries `number.unit_of_measurement`, or `dc` is `temperature`.
   Otherwise Core's conversion in both directions is the identity
   (`core:components/number/entity.py:392-447`). The state's
   `unit_of_measurement` (or its absence), `dc` and the registry `number`
   options are recorded in the observation fingerprint. Residual: an
   integration that supplies a translated display unit instead of a native
   unit (`core:components/number/entity.py:342-356`) is not distinguishable
   from the registry or state reads. No admitted first-build kind is converter-class (the Matter
   `sensitivity_level` number has no device class). The §9.2 refusal fixture
   includes a converter-class number with a translated unit, which must be
   refused with zero sends; a later kind that is converter-class needs its
   own exact-version lane proof before it is admitted.
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
6. **Readback.** The state must parse as a finite decimal;
   `succeeded_verified` only when `Decimal(state) == v` and the recorded unit,
   `min`, `max` and `step` are unchanged. Any integration rounding,
   quantization, Core clamping or unit change shows up as a mismatch, never
   `succeeded_verified`.
7. **Rounding and clamping are never silent.** Engineering never rounds,
   snaps or clamps the requested value. A value that needs rounding to fit is
   refused at preparation (rules 4–5).
8. **Undo.** The prior value is restorable only if it passes rules 4–5
   against the attributes current at undo preparation, preflight and send.

E1 note (updated for B15): the only admitted numeric kind is the Matter
`(number, matter, sensitivity_level)` integer range 1..N, the Aqara
back-porch sensor in the household (1..3 in the inventory). Its source sets
`native_min_value=1` and `native_step=1`, takes the maximum from the device's
`SupportedSensitivityLevels` and sets no device class
(`core:components/matter/number.py:539-563`), so rule 2 is the identity case.
`max` is device-supplied: rules 1 and 4 read it from state and registry
capabilities on every call; nothing about the range is compiled. The Tapo
1..100 number is dropped (no `translation_key`, §4.1).

### 5.6 Safety confirmation and risk disclosure

**No approval, no panel (B8).** A forward change or an undo dispatches on the
call itself, after the shipped checks, exactly like `control_helper` and
`control_power`. There is no plan hash, no external approval and no
approval-page acknowledgement.

**Chat confirmation, only when safety-critical (B8).** The mechanism and
argument are `control_helper`'s, reused verbatim (§2.5):

- after preparation and before the declaration (`before_declaration`), a
  safety gate evaluates the prepared change; a no-op is not evaluated
  (`E/helper_value/service.py:182-211@1c4c32b3`);
- if the gate reports a reason and `acknowledge_safety_critical` is `false`,
  the call returns `status: confirmation_required` with the reason,
  `dispatch: no_dispatch`, `task_created: false` and no `task_id`; nothing is
  declared, so the same `operation_id` (or a new one if it has expired) is
  used for the confirmed call (`E/helper_value/service.py:93-123@1c4c32b3`);
- the model must tell the user the reason and ask in chat, and call again
  with `acknowledge_safety_critical=true` only after the user confirms; never
  pre-emptively (`E/tools/helper.py:39-43@1c4c32b3`);
- the receipt and audit record `safety_gate` (`acknowledged`,
  `acknowledged_evidence_unavailable` or `not_safety_critical`),
  `safety_confirmation: "model_conveyed_chat_confirmation"` and
  `external_approval: false` (`E/helper_value/service.py:68-91@1c4c32b3`).

**What is safety-critical in the first build.** Defined concretely:

- **Power-on behavior is not safety-critical** (B11, item 3): disclosure only,
  on every target, including the garage beer fridge and network cabinet plugs.
- **Child lock and sensor sensitivity:** no classifier exists for them.
  `control_power` reaches child-lock switches today with no confirmation (B12),
  and `control_helper`'s trigger (`E/governance/helper_dependency.py`,
  "Bounded dependency-risk evidence for exact input-boolean actions") is
  helper-specific and not reviewed for `select`/`switch`/`number` device
  entities.
- **Tapo detection selects** (the one kind whose `off` value raised a safety
  question) are not in the first build (B9), and no `tapo_control` kind is
  admitted (B15).
- **Smoke/CO alarm smoke sensitivity** is unreachable: its triple
  `(select, matter, sensitivity_level)` is not admitted (B16 (i), §4.1), so it
  needs no first-build safety rule.

**Decided: the first-build trigger is empty (B14, Josh decision (in-session
2026-11-08), Q8 option (a)).** The mechanism and argument ship verbatim; every
first-build result is `safety_gate: not_safety_critical`, and an unneeded
`acknowledge_safety_critical=true` is accepted and recorded, as in
`control_helper`. The build PR must prove the mechanism end to end with a
test-only trigger that is unreachable in a production build (§8 S1, S2, S7):
the first call returns `confirmation_required` with `dispatch: no_dispatch`,
`task_created: false` and no send; a later call with
`acknowledge_safety_critical=true` dispatches; the receipt and audit carry
`safety_gate` and `safety_confirmation`, with `external_approval: false`. A
real trigger is added only with the kind that needs it (for example Tapo
detection), in a later additive profile.

**Disclosures on every result** (unchanged content; now in the control
result instead of a plan):

- **Persistence (qualified):** a configuration-category entity usually
  represents a setting held by the device or its integration, so it is often
  not undone by a Home Assistant restart and may survive the device leaving
  Home Assistant. The category alone does not prove this (§3.1). Whether a
  given setting is stored on the hardware, in the integration's cloud, or only
  in Home Assistant depends on the integration and the device. The result says
  "may persist outside Home Assistant" unless household or integration
  evidence for that exact platform establishes otherwise.
- **Outage and restart recovery** (power-on behavior and similar): this setting
  decides what the device does when power returns after an outage, a breaker
  trip or a device reboot. For example, a plug feeding a freezer, refrigerator,
  sump pump, network equipment or medical load set to "off" stays off after an
  outage, and a plug set to "on" or "previous state" may switch a load on
  unattended.
- **Physical access** (child lock and similar): the setting changes whether the
  device's own buttons work. Locking can block manual control in an emergency.
  Unlocking can allow unintended manual control.
- **Sensing behavior** (sensitivity, timeouts and delays): the setting changes
  when the sensor reports motion, presence, light or alarm conditions, and
  therefore what automations, lights or alarms do. Lower sensitivity or longer
  delays can miss events; higher sensitivity can cause false triggers. The
  result shows the value, unit, `min`, `max` and `step` it relies on. The
  admitted Matter sensitivity kind is not Aqara-specific: on a water-leak,
  rain or contact sensor it changes when that sensor reports (§4.1).
- **Feedback limits:** Home Assistant state is the integration's report, not
  physical observation (`physical_feedback_verified: false`). If the entity
  reports `assumed_state` (`core:helpers/entity.py:918`), readback proves only
  Home Assistant's optimistic state, and the result says so.
- **Integration semantics:** option labels and numeric scales are
  integration-defined. The result shows the raw option string or number the
  device received, plus the friendly name when one is available.

These are disclosures, not refusals. Consequence evidence does not by itself
block an exact, owner-authorized change (AGENTS.md product direction; ADR-022).

#### Superseded (historical): Q8 as an open question

The bda3ace0 revision left the trigger as §13 Q8, pending Josh, with
options (a) empty trigger, (b) extend the `control_helper` dependency-evidence
builder, (c) an owner-named set of exact targets. Superseded by B14 (option
(a)).

### 5.7 Stale-state protection and concurrency

**Composite observation and its fingerprint.** One projection, used for the
prepared baseline, the locked preflight, the `once()` re-read, the readback
and undo: entity_id, `state`, `last_changed`, `last_updated`, context id,
`assumed_state`, the `select` `options` list in order, the `number`
`min`/`max`/`step`/`mode`/`unit_of_measurement`, and registry `id`,
`created_at`, `entity_category`, `device_id`, `platform`, `translation_key`,
`unique_id`, `config_entry_id`, `modified_at`, `original_device_class` and
`options["number"]`, plus the device's `id`, `created_at`, sorted
`identifiers`, `primary_config_entry` and `disabled_by`. `last_reported` and
user-editable names, areas and labels are excluded. The fingerprint is the
sha256 of its canonical JSON. Because readback uses the same projection, the
verified readback fingerprint **is** the post-change observation fingerprint
that undo requires (§5.11).

**Stale-state chain (shipped pattern, §2.5; composite at every step).**
Preparation fingerprint → locked preflight re-prepares and compares
(`*_state_changed` refuses, no dispatch; `E/fan/adapter.py:95-106`) → `once()`
re-reads and compares immediately before the executor's `before_dispatch`.
Any difference refuses with no send.

**The final pre-send check is the full composite, not the state digest
(review F2).** The shipped `FanAdapter.dispatch` `once()` re-reads REST state
only and compares `digest(state)` with `prepared.current_state_fingerprint`
(`E/fan/adapter.py:111-125`, read and compare at `:115-119`). Inherited unchanged, that check would either
always refuse (a state digest never equals the composite fingerprint) or, if
the fingerprint were narrowed to match it, miss a registry or device identity
replacement that leaves state unchanged in the window between the locked
preflight and the send. So `DeviceConfigurationAdapter` **overrides
`dispatch`**. Its `once()`:

1. raises `device_configuration_duplicate_dispatch` if it already ran (the
   shipped guard, `E/fan/adapter.py:113-114`);
2. reads the full §5.7 projection through the same reader as preparation and
   the preflight: REST state, `config/entity_registry/get` for the entity_id,
   and the `config/device_registry/list` entry for the registry `device_id`;
3. recomputes the composite fingerprint and compares it with
   `prepared.current_state_fingerprint`. Every field of the projection is
   compared, including the registry `id`, `created_at`, `unique_id`,
   `device_id`, `platform` and `translation_key` and the device `id`,
   `created_at` and `identifiers`. A difference, or a registry or device read
   that fails or returns no entry, raises the refusal
   `device_configuration_state_changed` before `before_dispatch`, so nothing
   is sent and no intent is committed;
4. re-checks the compiled allowlist against the re-read `(domain, platform,
   translation_key)` and `entity_category == "config"` (a pure function of
   the re-read, no new I/O);
5. only then runs the executor's `before_dispatch` and marks the call as
   dispatched.

The outcome mapping after `once()` is the shipped one, unchanged
(`E/fan/adapter.py:126-158`): a `before_dispatch` failure is
`FAILED_PRE_DISPATCH` (`:133-143`); not dispatched without a callback failure
is the refusal (`:144`), which is how a composite mismatch ends; a provider `call_not_sent` is `DISPATCH_FAILED_CONFIRMED`; any other
exception is `DISPATCH_INDETERMINATE`. The undo path (§5.11) uses the same
override, so an undo's last pre-send check also covers the full recorded
identity. The three reads add three round trips inside the held lock set and
before the send; they add no write and no new endpoint (all three are already
used by preparation, §5.5).

**Lock set (forward change and undo alike).** `DeviceConfigurationAdapter`
overrides `lock_requests`:

| Key | Mode | Why |
|---|---|---|
| `entity:<entity_id>` | **exclusive** | The exact key `control_power` and `control_fan` take (`E/fan/adapter.py:85-93`), in the same store (`E/fan/service.py:105-118`), so the two paths never dispatch concurrently on one switch (B12) |
| `entity_registry:<entity_id>` | shared | Conflicts with the exclusive key Engineering registry metadata, availability and exposure plans take (`E/f3_configuration/locks.py:47-53`), so a governed rename or disable cannot interleave with a send |
| `home_assistant:core` | shared | Home Assistant availability dependency, as both paths already take |

There is no ha-mcp provider lock (the provider is the direct HA API) and no
`reload:` key (there is no configuration resource type any more).
`control_power`'s lock set, contract and signed profile are unchanged.

**Ownership timeline (the shipped executor, `E/f3/executor.py`).**

1. The set is acquired atomically on the shared store with the shipped
   bounded wait (`FAN_LOCK_TIMING`, `E/fan/service.py:205-211`). Acquisition
   is all-or-nothing, so arrival order with `control_power` cannot deadlock or
   leave a partial set. Contention ends as `lock_conflict` with no dispatch.
2. Only after acquisition does the locked preflight re-run §5.5 and compare
   the fingerprint.
3. In `before_dispatch` the handle's lease and generation are revalidated
   (`validate_handle`, `E/f3/executor.py:638`) and durable dispatch intent is
   committed (`:660`) before the send. If the lease expired or was fenced, the
   command is not sent (`FAILED_PRE_DISPATCH`). The lease is renewed during the
   operation (`:697-702`).
4. The set is held through the single send and the readback window (§5.9).
5. Release follows the outcome (§5.12): released after `succeeded_verified`,
   `no_change` or a proven non-send. **Every dispatched or possibly dispatched
   outcome that is not verified** is promoted to a selective conflict hold
   that retains only `entity:<entity_id>` (`E/fan/locks.py:14-53`). The hold is
   durable, survives restart (expiry never frees it, `E/f3/locks.py:329-342`),
   and is released only by the HAMCP-192 typed hold release after a read-only
   review (§5.12). While it is held, `control_power`, another device-config
   change and any undo on that entity refuse `lock_conflict` and send nothing.

**What locks can and cannot prevent.**

- Preventable, and prevented: concurrent Engineering dispatch on the same
  entity by `control_power`, by another device-configuration change or undo,
  or a governed registry plan on the same entity.
- Not preventable by Engineering: changes made in the Home Assistant UI, by
  automations or scripts, by other integrations, by the device's own buttons,
  or by the device itself. These are detected, not prevented: the preflight
  and `once()` checks refuse a change that landed before the send; a change
  that lands between send and readback is a mismatch that holds the entity,
  never a redispatch; and undo requires the exact post-change observation
  (§5.11).

### 5.8 At-most-once dispatch

The shipped `FanAdapter.dispatch` contract (`E/fan/adapter.py:108-158`), with
the `once()` re-read replaced by the composite (§5.7, review F2):

1. `once()` re-reads the full composite observation (state, registry entry
   and device) and compares its fingerprint, then runs the executor's
   `before_dispatch` (lock currentness, durable intent). If either fails, the
   command is never sent (`FAILED_PRE_DISPATCH`).
2. Send exactly one WebSocket `call_service`. There is no retry, ever
   (`redispatch_prohibited: true`).
3. A provider result that proves the call was not sent is
   `DISPATCH_FAILED_CONFIRMED`. **Any other exception after the send,
   including a Core rejection with a received response** (for example
   ServiceValidationError `not_valid_option` because the option was removed in
   between), is `DISPATCH_INDETERMINATE`. Neither is treated as proof that the
   state is unchanged; readback decides (§5.9), and the entity is held
   (§5.12).

### 5.9 Readback

- Authoritative readback is the composite observation of §5.7 (REST state
  plus the registry reads), taken while the lock set is held.
- Device integrations often report the new state asynchronously after
  `blocking=True` returns (for example a Zigbee attribute report). Readback
  therefore polls with the shipped schedule, `INLINE_POLL_DELAYS = (1, 1, 2,
  2, 2, 2)` within `INLINE_POLL_BUDGET_SECONDS = 10`
  (`E/fan/service.py:34-35`), and the executor takes a final read at its
  deadline (`final_read_at_deadline=True`, `:205-211`).
- `succeeded_verified`: state equals the requested value (exact string for
  `select`; `on`/`off` for `switch`; numeric equality with unchanged unit and
  bounds for `number`, §5.5.1 rule 6), and the target identity of §5.11.1 is
  unchanged. The service then records the verified observation (§5.11.1).
- `changed_unverified` / `verification_failed`: the window ends with a
  different state, with `unavailable`/`unknown`, or with an identity change.
  No redispatch. The result reports the last observed state and states that
  the device may or may not have applied the setting. The entity is held.
- After `DISPATCH_INDETERMINATE` the same readback runs. The outcome is
  reported as observed and never upgraded to "unchanged".

### 5.10 Interaction with existing governance

- **`call_service`:** unchanged and still refused (`provider_unavailable`, no
  fallback). This design adds no generic service path and no forwarding. The
  new gateway builds its single payload from the closed input.
- **`control_helper`:** unaffected. No domain overlap (helper domains versus
  `select`/`switch`/`number`). This design reuses its confirmation mechanism
  by pattern and shares no file with HAMCP-327.
- **`control_power` (decided, B12):** it keeps accepting config-category
  switches such as child lock. Its contract, schema, lock set and signed
  profile `core.typed_power_operation` are unchanged. The two paths are
  serialized by the shared exclusive `entity:<entity_id>` key (§5.7). The
  difference the caller sees: `control_device_configuration` adds registry
  proof, prior-value capture, disclosures and undo; `control_power` does not.
  Neither is an approval path.
- **`create_input_select_option_plan` and the F3 plan family:** unaffected;
  this design adds nothing to them.
- **`reload_domain`, `delete_automation`, `upsert_automation`:** unaffected.

#### Superseded (historical): §5.10 Q3 options

Options (b) redirect and (c) disclose-only for `control_power` were offered as
Q3. They are superseded by B12 (option (a)).

### 5.11 Undo

#### 5.11.1 What a verified change records

When a forward change ends `succeeded_verified` (§5.9), the service writes one
additive sidecar, in the HAMCP-192 style (atomic write, bounded, one file per
task, older code ignores the directory; `E/fan/service.py:504-556`):

`<governance_path>/ordinary-device-configuration-v1/verified-observations/<task_id>.json`,
model `device-configuration-verified-observation-v1`, at most 4096 bytes:

- `model`, `task_id`, `prepared_hash` (binding it to the exact receipt);
- **target identity:** registry `id`, `created_at`, `unique_id`, `platform`,
  `translation_key`, `config_entry_id`, `device_id` and `entity_category`;
  and the device's `id`, `created_at`, sorted `identifiers` and
  `primary_config_entry`;
- **post-change observation fingerprint:** the verified readback fingerprint
  of §5.7;
- **restorable value:** the baseline value validated at preparation (§5.5
  item 6), copied from the receipt baseline;
- `recorded_at`.

The build must not rely on the execution record carrying the readback
fingerprint: that is not established at this base. If the process stops
after verification and before the sidecar is durable, undo for that task
refuses `device_configuration_undo_unavailable` (fail closed; the recovery is a
compensating change, §5.11.3). `no_change`, refused and non-verified tasks
have no sidecar.

#### 5.11.2 Undo of a verified change

`control_device_configuration(entity_id, action="undo", undo_task_id=<original>,
operation_id=<new>)`:

- **Eligibility.** The original receipt exists in this namespace, its
  `request.entity_id` equals `entity_id`, it is a forward change (not an
  undo), it ended `succeeded_verified`, and its verified-observation sidecar
  exists and matches its `prepared_hash`. Otherwise refuse
  `device_configuration_undo_unavailable`. An undo of an undo refuses
  `device_configuration_undo_not_eligible`: re-applying the earlier value is an
  ordinary forward change.
- **Identity and exact post-state at preparation.** The undo prepares like a
  forward change (§5.5) and additionally requires (a) the target identity
  equal to the recorded one, else `device_configuration_undo_identity_changed`;
  and (b) the current composite observation fingerprint equal to the recorded
  post-change fingerprint, else `device_configuration_undo_post_state_unproven`.
  Equal value alone is not enough: a replaced registry entity at the same
  entity_id and value has a different registry `id`/`created_at`; a later
  write that returns to the same value has a different
  `last_changed`/`last_updated`/context id.
- **The undo's baseline is the recorded post-change observation.** Because the
  undo's prepared fingerprint equals it, the shipped locked preflight and
  `once()` comparisons (§5.7) re-check the same exact post-state inside the
  locks and immediately before the send; any difference refuses with no send.
- **The restore value is revalidated** against current attributes: `switch`
  `on`/`off`; `select` prior option still in the current `options`; `number`
  §5.5.1 rule 8. Otherwise `device_configuration_baseline_not_restorable`,
  reporting the prior value for manual handling.
- **Consumed once: the claim is written inside the receipt write (review
  F3).** The claim record is `undo-claims/<original_task_id>.json`, model
  `device-configuration-undo-claim-v1` (≤4096 bytes): `original_task_id`,
  `original_prepared_hash`, `undo_task_id`, `undo_prepared_hash`,
  `claimed_at`. It is **not** written in `before_declaration`. That hook runs
  after preparation and before `save`, outside any lock
  (`E/fan/service.py:253-258`, called at `:292`, `save` at `:295`), so a claim
  written there could race a second undo and could not be decided together
  with the receipt.

  Instead `DeviceConfigurationService` overrides `save`
  (`E/fan/service.py:171-203`). The override enters `self.transaction()` (the
  exclusive `.declarations.lock` flock, `E/fan/service.py:120-127`) **exactly
  once** and does all of the following inside that one block, in this order:

  1. the shipped checks, unchanged in order and diagnostics: load the prior
     receipt (`:174-178`; a matching retry returns it with no claim work, a
     different request refuses rebound), freshness (`:179-180`) and capacity
     (`:181-182`, counted over `*.json` in the namespace root, which the
     `undo-claims/` subdirectory does not affect);
  2. for an `undo` request only: read `undo-claims/<original_task_id>.json`.
     If it exists and names a different undo task, refuse
     `device_configuration_undo_already_claimed` unless the supersede rule
     below holds. If it names this undo task and prepared hash (a crash
     retry), keep it;
  3. write the claim atomically: mkstemp in `undo-claims/`, write, flush,
     fsync, `os.replace`, fsync of `undo-claims/` (the shipped receipt write
     sequence, `:189-202`);
  4. write the receipt bytes exactly as the shipped `save` builds them
     (`:183-202`), then return.

  It does not call `super().save()` and does not call `self.transaction()`
  again. `fcntl.flock` locks belong to the open file description, so a second
  `open()` of `.declarations.lock` inside the block would get a new
  description and block on the first one in the same process: a nested
  `transaction()` deadlocks. A build may move the shipped body of steps 1
  and 4 into a shared helper that expects the lock to be held already, and
  have `FanService.save` call it under its own single `transaction()`, but
  only if the fan and power receipt bytes and refusal order stay identical
  (P9, §5.13).

  **Supersede rule, decided under the same lock.** A claim naming another
  undo task may be replaced only when that task has no receipt (possible only
  after a crash or an exception between steps 3 and 4), or its receipt's
  execution record is terminal with zero dispatch proven (refused at the
  locked preflight or at `once()`, `FAILED_PRE_DISPATCH`, or
  `DISPATCH_FAILED_CONFIRMED`). The records are read, not written, while the
  lock is held; the build must show that this read takes no lock that a
  holder of `.declarations.lock` could wait on in reverse order. A
  dispatched, possibly dispatched or still running undo keeps the claim for
  good. An undo refused before `save` (input, admission, identity, post-state,
  confirmation gate) never writes a claim at all.

  **Why this makes the claim atomic.** Every writer of an undo claim and every
  writer of an undo receipt in this namespace is this one `save`, and it holds
  the exclusive flock from the claim check through the receipt write. Two
  concurrent undos of one original therefore serialize: the second enters
  after the first has written both its claim and its receipt, sees the claim,
  finds a receipt whose task is not proven zero-dispatch, and refuses. No
  interleaving lets both pass the check. A claim without a receipt can exist
  only if the process stopped or raised inside the block, and since execution
  starts only after `save` returns (`E/fan/service.py:295` onward), that state
  proves the claimed undo never started and may be superseded. The check, the
  supersede decision and both writes form one critical section; the earlier
  hook-based wording did not provide that.
- **Confirmation.** The undo passes the same safety gate as a forward change
  (§5.6).
- **Reporting.** A `succeeded_verified` undo is reported as **"original change
  undone (verified)"**. It is the only outcome that may claim the original
  change was undone. Any other undo outcome follows §5.12 and holds the entity
  like a forward change.
- There is no automatic undo.

#### 5.11.3 When undo is not provable: hold, review, compensating change

- After a dispatched non-verified outcome, Engineering cannot prove which
  value is its own; there is no sidecar, so undo refuses
  `device_configuration_undo_unavailable`, and the entity stays held (§5.12).
- After a verified change whose post-state has since drifted (a third-party
  change, or a Home Assistant restart that reset `last_changed`), undo refuses
  `device_configuration_undo_post_state_unproven`.
- In both cases the recovery is a **compensating change**: an ordinary new
  `control_device_configuration` forward call toward the desired value, built
  from a freshly observed baseline with full §5.5 proof (after any hold is
  released). Its result is reported as an ordinary change; when the caller
  says it is compensating, the audit records the original `task_id` as
  informational context. It is **never** reported as "the original change was
  undone".

#### Superseded (historical)

- The c22af225 draft allowed rollback "while the live state still equals the
  verified readback value" and, after an indeterminate or mismatched apply, a
  rollback plan "from the observed state". Superseded by the Codex D2
  correction: value equality cannot prove identity or ownership.
- The d020118a revision made undo a separately approved `rollback_change`
  plan, and compensating changes separately approved plans carrying
  `compensates_plan_id`. Superseded by B8 (no plan): undo is the `undo` action
  of the same tool, with the same identity and exact post-state binding and a
  durable once-only claim.

### 5.12 Outcomes, holds and release

One entity and one command per call (B10), so the outcomes are:

| Outcome | Dispatched? | Lock after | Status |
|---|---|---|---|
| Refused at input, admission, target proof or confirmation gate | No, no task | not acquired | `refused` or `confirmation_required` (no `task_id`) |
| `lock_conflict` | No | not acquired | `refused`, blocking holder reported |
| Locked preflight or `once()` stale | No | released | `refused` (`*_state_changed`) |
| Already at the requested value | No | released | `no_change` |
| `FAILED_PRE_DISPATCH` (including lease lost before send) | No | released | `refused`, `call_not_sent` |
| `DISPATCH_FAILED_CONFIRMED` (provider proves not sent) | No | released | `provider_unavailable` or `refused` |
| `DISPATCH_INDETERMINATE` (timeout, disconnect, or any post-send rejection) | Unknown or yes | `entity:` hold | `changed_unverified` / `verification_failed` per readback |
| Readback mismatch at the deadline | Yes | `entity:` hold | `changed_unverified` / `verification_failed` |
| `succeeded_verified` | Yes | released | `succeeded_verified`, undo available (forward change) |

**Hold release (shipped HAMCP-192 path).** A held device-configuration task is
released only through the existing typed hold release: a read-only review of
the task and current observation, then an owner release recorded in the
additive `hold-releases/<task>.json` sidecar with audit and dispatch `none`
(`E/fan/service.py:504-700`, `:766-806`). The build adds the kind
`device_configuration` to the Ingress page's `TYPED_HOLD_KINDS`
(`E/approval_web.py:72`; a protected path the build task must name) **and a
dedicated branch in `ApprovalWebApp._typed_service`** (review L2). Today that
resolver returns `FAN_OPERATIONS.service` for `fan` and falls through to
`POWER_OPERATIONS.service` for every other admitted kind
(`E/approval_web.py:814-824`), and both the inbox (`:826` onward) and the
release handler (`typed_release`, `:945` onward; the service is resolved at
`:975-977`) use it (`:833`, `:860`, `:975`). Adding the kind to the tuple alone would therefore show
and release device-configuration holds against the power service. The build
changes the resolver to:

- `fan` → `FAN_OPERATIONS.service` (unchanged);
- `device_configuration` → `DEVICE_CONFIGURATION_OPERATIONS.service`;
- `power` → `POWER_OPERATIONS.service`, now as an explicit `kind == "power"`
  branch;
- anything else → `None`, so the existing `service is None` refusal
  (`:976-977`) fails closed instead of reaching the power service.

The injected `_typed_services` mapping (`:817-819`, used by tests) gets the
same key. The release page text, CSRF binding and `Dispatch: none` outcome
(`:1016`) are unchanged. That page is the **recovery** surface `control_power` and `control_fan`
already use for unresolved sends. It approves no change, dispatches nothing,
and is not part of the normal change path (B8). Releasing a hold never makes
undo available; the recovery is a compensating change (§5.11.3).

#### Superseded (historical): partial-failure table

The d020118a table released the lock after a `provider_rejected` with a
pre-entity validation code and baseline readback, and released holds through
`reconcile_child` `release_hold_after_verified_resolution`. The shipped
ordinary-control adapter cannot distinguish a validation rejection from an
uncertain send (`E/fan/adapter.py:108-158`), so that early release is dropped
(more conservative), and the F3 `reconcile_child` path is replaced by the
typed hold release above.

### 5.13 Persisted records and downgrade evidence

This answers the signing plan's persisted-state gate (#395
`docs/HAMCP_332_2_6_0_SIGNING_PLAN.md` §7.3, row N4 "Verification records and
undo plans", which recorded "Not in #389 at its head"). It was a real gap in
the d020118a revision. There are no plans in this family any more; the
persisted records are:

| Record | Location | Format | Writer |
|---|---|---|---|
| Receipt (declaration) | `<governance_path>/ordinary-device-configuration-v1/<task_id>.json` | shipped receipt format, model `ordinary-device-configuration-v1-core-bound-v2` (§2.5) | `DeviceConfigurationService.save` override: shipped checks and receipt bytes of `FanService.save` unchanged, plus the undo claim in the same transaction (§5.11.2) |
| Execution record | the service's executions repository under the namespace root | shipped execution format | shipped executor, unchanged |
| Locks and holds | `<governance_path>/f3-operation-locks-v1` | shipped lock format; new owner/task ids only | shipped `DurableLockStore` / `FanLockStore` |
| Hold release | `.../ordinary-device-configuration-v1/hold-releases/<task>.json` | shipped `typed-hold-release-v1` | shipped HAMCP-192 writer |
| Verified observation | `.../verified-observations/<task_id>.json` | **new** `device-configuration-verified-observation-v1` | new (§5.11.1) |
| Undo claim | `.../undo-claims/<original_task_id>.json` | **new** `device-configuration-undo-claim-v1` | new; written only by the `DeviceConfigurationService.save` override, in the same single `.declarations.lock` transaction as the undo receipt (§5.11.2) |

No existing record format changes. There is no legacy (`<ns>`, unsigned)
receipt model for this namespace: it is signed-only from the first build, so
the legacy-hash recovery path is not used.

**Expected behavior across versions (each needs the proof below):**

- **Older runtime reading a newer state directory (downgrade).** The preceding
  shipped Engineering image has no `ordinary-device-configuration-v1`
  service, so it never reads, resumes or redispatches those tasks. Their
  locks are owned by a task id it does not know. `holder_blocks` keeps any
  such lock blocking (`E/f3/locks.py:329-342`), so the older `control_power`
  on a held or in-flight switch refuses `lock_conflict` until roll-forward,
  and the older Ingress page cannot release a `device_configuration` hold.
  This is a fail-closed residual, disclosed in release notes at build time.
  What is **unknown at this head** and must be proven against the exact
  preceding shipped image: that its F3, fan and power stale recovery never
  releases, transfers or redispatches a lock whose owner it does not
  recognize.
- **Newer runtime reading older state (forward).** Existing power, fan and
  helper receipts, execution records, locks and hold-release sidecars load
  with identical hashes and behavior; the new service ignores other
  namespaces.
- **Signed registry reverted or the profile withdrawn.** New calls refuse
  `device_configuration_core_authority_unavailable` before HA I/O. Recovery of
  an in-flight task uses its recorded `core_binding`; if authority is
  unavailable, recovery cannot read, the task stays non-terminal and held, no
  redispatch occurs, and undo refuses.

**Required proof (persisted-state gate; required future implementation
evidence, none run):** fixtures generated by the new writer (no hand-built
records, per AGENTS.md "Persisted Compatibility Fixtures"), loaded by the
exact preceding shipped image, plus restart tests at each boundary (§8 P1–P9).

### 5.14 Audit

Every call is audited through the shipped ordinary-control audit path with a
`request_summary` whitelist in the style of `E/helper_value/service.py:68-91@1c4c32b3`:
task_id, operation_id, operation hash, entity_id, target kind, target identity
digest, action, prior value, requested value, `undo_task_id`, lock keys,
dispatch facts (`dispatch_phase`, `provider_dispatch_occurred`, attempt
counts), readback outcome, `safety_gate`, `safety_confirmation`,
`external_approval: false`, and status. Holds and hold releases use the shipped
HAMCP-192 audit (`E/fan/service.py:766-806`). Option strings are bounded (255)
and sanitized as in HAMCP-145.

## 6. Signing impact

Recorded constraints: additive profiles only (B4); one combined 2.6 signing
for Core 2026.11.0 only (B5); profile name accepted (B13).

No existing signed profile covers this use (HAMCP-222 rule: a command,
service, argument or adapter not named by a signed contract is not admitted):

- `core.typed_power_operation` (`sha256:9242b795…001a`) names `light`/`switch`
  `turn_on`/`turn_off` through `reviewed_ha_mcp_typed_power`. It has no
  registry reads, no `select` or `number`, and no undo. Reusing it would put a
  new adapter and provider boundary under a signed contract that does not name
  them, and B4 forbids changing it.
- `core.typed_input_select_option_operation` (`sha256:e2240555…e38d`) names
  `input_select.select_option` only.
- `core.typed_helper_value_operation` (`sha256:d45ea23b…228d`) names helper
  domains only (including `input_number.set_value`, not `number.set_value`).
- `core.entity_registry_metadata_operation` (`profiles.py:495-520`) names
  `config/entity_registry/get` for metadata plans, not this adapter.

| Capability id (the signing plan #395 calls its profile id `core_device_configuration_entity_operation_v1`; the build derives the exact id) | New or changed | Core commands / contract added | Why | Fingerprint impact | Signing |
|---|---|---|---|---|---|
| `core.device_configuration_entity_operation` (B13) | **New, additive** (no predecessor; first signed for Core 2026.11.0) | Operation class: typed ordinary operation (as `TYPED_ORDINARY_OPERATION`, `profiles.py:425-462@1c4c32b3`), with an explicit `_contract` because the name does not fit the generator; adapter `compiled-core-device-configuration-entity-operation-v1`; provider boundary `direct_ha_api`, provider contract `direct-ha-exact-device-configuration-v1`; REST `GET /api/states/{entity_id}`; WS `config/entity_registry/get`; WS `config/device_registry/list`; WS `call_service` `select.select_option` (`option` only), `switch.turn_on`, `switch.turn_off` (no data), `number.set_value` (`value` only); single exact `entity_id` target; actions including `undo`; the compiled target-kind allowlist of exactly three triples, `(select, matter, startup_on_off)`, `(switch, matter, child_lock)` and `(number, matter, sensitivity_level)`, with `null` `translation_key` never admitted (B15, B16, §4.1); dispatch at most once, recovery read-only with no redispatch; lock identity `entity:<entity_id>` exclusive (§5.7); the numeric exactness contract of §5.5.1 including the embedded 2026.11.0 `UNIT_CONVERTERS` set; restorable-baseline and identity-bound undo rules (§5.5, §5.11); predispatch checks such as `entity_registry_config_category_device_proof`, `device_configuration_target_kind_admitted`, `device_configuration_state_readback_exact`, `device_configuration_number_identity_conversion`; never-sent list per §5.4. **No F3 plan identity** | No signed contract names this adapter, these services on config entities, or registry-bound undo | Adds one new fingerprint, computed by `_contract()` (`profiles.py:109-145`) in the build PR; unknown until then. **No existing profile fingerprint changes** | Appended to the **one combined 2.6 `extend-capabilities` extension of the existing 2026.11.0 entry** (B5), with its Core 2026.11.0 lane receipt (§9.2) among that extension's `review_artifacts_sha256` (`scripts/prepare_core_release_registry.py:108-126`). No other release is extended, so 2026.10.x keeps refusing `device_configuration_core_authority_unavailable` (fail-closed) |

All numeric semantics and the three-triple allowlist are in this profile
before its first signature; a change to the allowlist changes the
fingerprint. There is no later "number" profile and no change to
`core.typed_power_operation`.

**Proving check (build PR and signing preparation):** every preexisting signed
contract id and fingerprint is byte-identical to the base; the 2026.11.0
extension's `previous_capabilities` equals the current entry's list and its
`added_capabilities` contains only reviewed new 2.6 profiles, including this
one exactly once. Signing-plan gates: G4 (lane evidence bound to the final
fingerprint, §9.2: the run executes the signed three-triple build, and every
admitted kind is dispatched and verified through Core's real `matter`
integration; a kind the signed allowlist refuses cannot count, review F1) and G5 (the evidence-group mapping in
`ci/core-evidence-groups.json` and the read-file mapping in
`ci/core-contract-read-files.json` before signing).

## 7. Public schema and tool-count impact

- One new static tool: `control_device_configuration` (this design's proposed
  name).
- One new input schema (§5.2), additive. No existing tool schema changes.
- One new provider contract, one new `ProviderCapability` member, one new
  `CoreIndependence` entry, one new compiled runtime profile, one new ordinary
  service namespace and two new sidecar models (§5.13). **No** new F3
  identity, configuration resource type or plan tool.
- `call_service`, `control_power`, `control_helper`, `control_fan` and
  `create_input_select_option_plan` schemas are unchanged.
- The capabilities catalog (`E/capabilities.py`) gains one entry: category
  `ordinary_runtime_action`, risk `physical_action`,
  `external_approval_required: False`, `direct_write_allowed: False` (§5.3),
  plus one provider-contract entry beside `E/capabilities.py:978-998@1c4c32b3`.
- The Ingress typed hold release gains the kind `device_configuration`
  (§5.12).

Counts: at the design base this is +1 static tool (81 to 82) and +1 compiled
profile (32 to 33), unchanged by item 25 (one plan tool became one control
tool). Those are base-local deltas. The 2.6.0 release totals, fingerprints and
the 2026.11.0 profile count must be recomputed from the integrated 2.6.0
build, after the other 2.6 profiles land.

## 8. Negative reachability and refusals

Each row is a test the build PR must add. These are required future
implementation evidence; none has been run. Refusals happen before any HA I/O
unless the row says otherwise.

| # | Attempt | Expected | HA I/O before refusal |
|---|---|---|---|
| N1 | Profile not signed for the running Core | `device_configuration_core_authority_unavailable`, on forward change and undo | none |
| N2 | `entity_id` outside `select.`/`switch.`/`number.` (for example `button.`, `update.`, `light.`, `input_select.`) | `device_configuration_invalid_value` | none |
| N3 | Extra field (`service`, `domain`, `service_data`, `target`, `cycle`, unit, `reason`, `expiration_minutes`) | `device_configuration_field_not_supported` (schema refusal) | none |
| N4 | `number` value with exponent, `NaN`, leading zeros, a JSON number instead of a string, or not round-tripping; `value` with `turn_on`/`turn_off`/`undo` | `device_configuration_invalid_value` | none |
| N4a | Action not valid for the domain (`turn_on` on `select.`, `select_option` on `number.`, `toggle` anywhere) | `device_configuration_action_not_supported` | none |
| N5 | Registry `not_found` | `device_configuration_entity_not_registered` | registry get only |
| N6 | `entity_category` `null` or `diagnostic` | `device_configuration_not_config_category` | registry get only |
| N7 | No `device_id`, or device absent from the list | `device_configuration_no_device` | registry reads only |
| N8 | Entity or device disabled | `device_configuration_target_disabled` | registry reads only |
| N9 | State `unavailable`/`unknown` | `device_configuration_entity_unavailable` | reads only |
| N10 | `select` value not in `options` | `device_configuration_value_not_offered` | reads only |
| N11 | Value equals the current state (numerically for `number`, for example `"30"` vs state `30.0`) | `no_change`, declared, zero sends, no undo sidecar | reads only |
| N12 | Any observation field changes between preparation and the locked preflight, or between the preflight and `once()` (state, `last_changed`, `last_updated`, context, options, `min`/`max`/`step`/unit, registry `modified_at`, `device_id`, category, `options["number"]`, device identity). **Variant (F2):** between the locked preflight and `once()`, the registry entry is deleted and re-registered at the same entity_id so the registry `id` and `created_at` change while state, value and every state attribute stay identical | refused stale at `once()` by the composite comparison (`device_configuration_state_changed`), no `before_dispatch`, no intent, no `call_service` sent | reads only |
| N13 | `before_dispatch` raises (including a lost or fenced lease) | `FAILED_PRE_DISPATCH`, `call_not_sent` | reads only |
| N14 | Gateway is asked for any never-sent service (§5.4) | Unreachable: payload builder has no branch for it. Test asserts the exact payloads and that no other is constructible | none |
| N15 | Timeout after send | `DISPATCH_INDETERMINATE`, one send only, readback runs, `entity:` hold retained | one send |
| N15a | Core rejection with a received response after send (for example `not_valid_option`) | `DISPATCH_INDETERMINATE`, one send, `entity:` hold retained (never released early) | one send |
| N16 | Readback window ends with a different state | `changed_unverified`/`verification_failed`, no redispatch, `entity:` hold retained | one send |
| N17 | Undo while the current observation differs from the recorded post-change observation (state, timestamps or context) | `device_configuration_undo_post_state_unproven` | reads only |
| N18 | Direct gateway unavailable | `provider_unavailable`, no ha-mcp or `call_service` fallback | none |
| N19 | `call_service` with a config entity | still `provider_unavailable` (regression) | none |
| N20 | Same `operation_id` with changed arguments; expired `operation_id`; receipt capacity exhausted | `*_operation_id_rebound` / `*_operation_id_expired` / `*_receipt_capacity_exhausted`, no dispatch | none |
| N21 | Malformed baseline: `switch` state not `on`/`off`; `select` state not in its options; `number` state non-numeric, out of bounds or off-step | `device_configuration_baseline_not_restorable` | reads only |
| N22 | `number` `min`/`max`/`step` missing, non-finite, `min >= max`, `step <= 0`, or disagreeing with registry `capabilities` | `device_configuration_number_bounds_inconsistent` | reads only |
| N23 | `number` with a converter device class and a registry `number.unit_of_measurement` option; `temperature` number | `device_configuration_number_unit_conversion` | reads only |
| N24 | `number` value below `min` or above `max` (including a value Core would clamp) | `device_configuration_number_out_of_range`, never sent | reads only |
| N25 | `number` value off-step (for example `7` with `min` 0, `step` 5; `0.15` with `step` 0.1) | `device_configuration_number_step_misaligned`, never sent | reads only |
| N26 | `number` readback rounded, quantized, clamped, or with a changed unit or bounds | mismatch, never `succeeded_verified`, held | one send |
| N27 | No plan or approval reachability: the tool never creates a change plan, never calls `approve_change_plan`/`apply_change_plan`/`rollback_change`, and never requires an approval page | asserted by a registration and call-graph test | none |

**Confirmation tests (B8, B11, §5.6).**

| # | Scenario | Expected |
|---|---|---|
| S1 | Test-only trigger reports a reason; `acknowledge_safety_critical=false` | `confirmation_required`, `dispatch: no_dispatch`, `task_created: false`, no `task_id`, no receipt, no lock taken |
| S2 | Same `operation_id` with `acknowledge_safety_critical=true` after S1 | declared and dispatched once; receipt and audit `safety_gate: acknowledged`, `safety_confirmation: model_conveyed_chat_confirmation`, `external_approval: false` |
| S3 | Power-on behavior change on a critical-load plug fixture with the production trigger | no `confirmation_required`; disclosure present; `safety_gate: not_safety_critical` (B11) |
| S4 | `acknowledge_safety_critical=true` with no reason | accepted, recorded, dispatches normally (`control_helper` behavior) |
| S5 | No-op with the test-only trigger | `no_change`; gate not evaluated |
| S6 | Undo with the test-only trigger | the same gate as a forward change |
| S7 | End-to-end mechanism proof (B14): test-only trigger, first call with `acknowledge_safety_critical=false`, then the confirmed call | first call: `confirmation_required`, `dispatch: no_dispatch`, `task_created: false`, zero `call_service` sends; confirmed call: exactly one send, `succeeded_verified`; audit of both calls carries `safety_gate` and `safety_confirmation`, and `external_approval: false` |
| S8 | Production build, every admitted kind (§4.1) | empty trigger: no `confirmation_required`, `safety_gate: not_safety_critical`; the test-only trigger is not constructible in a production build (asserted) |

**Target admission tests (B9, §4.1).**

| # | Scenario | Expected |
|---|---|---|
| T1 | Config-category `select` of the excluded Tapo detection kind `(select, tapo_control, null)`; a Matter smoke/CO smoke-sensitivity select and the legacy Aqara sensitivity select, both `(select, matter, sensitivity_level)` | `device_configuration_target_kind_not_admitted`, registry reads only, no state read, no send |
| T1a | Config-category `tapo_control` `number` with a `null` `translation_key` (the Tapo digital-sensitivity kind) | `device_configuration_target_kind_not_admitted`, registry reads only, no send |
| T2 | Config-category `select`/`switch`/`number` of any kind not in the allowlist: an admitted key on another platform (for example `(switch, tuya, child_lock)`), an admitted platform with another key, and a `matter` entity with a `null` key | `device_configuration_target_kind_not_admitted` |
| T3 | The stale `unavailable` Aqara duplicate | `device_configuration_entity_unavailable` if it is the admitted `number` kind, reads only; `device_configuration_target_kind_not_admitted` if it is the dropped `select` |
| T4 | Each of the three admitted kinds with fixture values matching the cited source: `(select, matter, startup_on_off)` options `on`/`off`/`toggle`/`previous`; `(switch, matter, child_lock)`; `(number, matter, sensitivity_level)` with `min` 1, `step` 1 and a device-supplied `max` (3) | admitted and verified; also admitted, proving the disclosed breadth is real: an Eve TRV `child_lock` switch and a non-Aqara `sensitivity_level` number (for example a water-leak sensor) |
| T5 | Power-on select value `toggle` | sent as `select.select_option` `{"option": "toggle"}`; `switch.toggle` never constructed |
| T6 | Allowlist regression | the compiled list is exactly the three triples of §4.1, no more, and the build's source check against the pinned Core 2026.11.0 Matter files passes |

**Concurrency tests (Codex D1).** The competing Engineering write never
dispatches in any of these:

| # | Scenario | Expected |
|---|---|---|
| C1 | `control_power` holds `entity:<switch>`; a device-configuration change starts | `lock_conflict` (or waits, then the locked preflight refuses stale if power changed the state); no send while power holds the lock |
| C2 | A device-configuration change holds the set; `control_power` on the same switch starts | power ends `lock_conflict`; no power send; power's own contract and lock set unchanged |
| C3 | Two device-configuration calls (different `operation_id`s) for the same entity, concurrently | exactly one sends; the other gets `lock_conflict`, or after acquisition a stale refusal; never two sends |
| C4 | Lease expires or is fenced between the locked preflight and the send | `FAILED_PRE_DISPATCH`, zero sends |
| C5 | Unresolved send, then process restart | `entity:` hold present after restart; `control_power`, another change and undo all refuse until the HAMCP-192 typed hold release for kind `device_configuration` |
| C6 | Governed registry metadata or availability plan on the same entity while the set is held | that plan ends `lock_conflict`; no registry write during the send |
| C7 | Lock-key regression | the lock set is exactly `entity:<entity_id>` exclusive, `entity_registry:<entity_id>` shared, `home_assistant:core` shared; no `helper:`, `device_configuration:`, `reload:` or ha-mcp provider key |
| C8 | Hold release reachability | the Ingress inbox lists and the release resolves `DeviceConfigurationService` for kind `device_configuration` (never `PowerService`; a device-configuration task id is never looked up on the power service), records `typed-hold-release-v1`, dispatches nothing, and does not make undo available; an unknown kind resolves to `None` and is refused (L2) |

**Undo identity tests (Codex D2).**

| # | Scenario | Expected |
|---|---|---|
| R1 | Registry entity deleted and re-registered at the same entity_id with the same name and value | `device_configuration_undo_identity_changed` at preparation; also at preflight/`once()` if it happens later |
| R2 | Device replaced (new device `id`) with the same entity_id and value | `device_configuration_undo_identity_changed` |
| R3 | ABA: after the change, a third party sets another value and then the changed value again | `device_configuration_undo_post_state_unproven` (timestamps/context differ) |
| R4 | Identity or observation changes between undo preparation and the send | refused at preflight or `once()`, zero sends |
| R5 | `select` prior option removed from `options` before undo | `device_configuration_baseline_not_restorable`, prior value reported |
| R6 | `number` bounds or step change so that the prior value no longer passes §5.5.1 rule 4 | `device_configuration_baseline_not_restorable` |
| R7 | Undo after an indeterminate or mismatched send | `device_configuration_undo_unavailable`; a compensating forward change is possible after release and is never reported as undo |
| R8 | Undo of an undo | `device_configuration_undo_not_eligible` |
| R9 | Second undo of one original after a dispatched or unknown first undo | `device_configuration_undo_already_claimed` |
| R10 | Second undo after the first undo was declared and then refused before intent (locked preflight or `once()` stale, zero dispatch proven) | claim superseded under the `.declarations.lock`; the second undo proceeds. An undo refused before `save` wrote no claim at all |
| R11 | Verified-observation sidecar missing (crash after verification) | `device_configuration_undo_unavailable` |
| R12 | `undo_task_id` names a task of another entity, of another namespace, a `no_change` task, or does not exist | `device_configuration_undo_unavailable`, no HA I/O |
| R13 | Two undos of one original, different `operation_id`s, released together so both pass preparation and the confirmation gate (F3) | exactly one claim and one undo receipt; the other refuses `device_configuration_undo_already_claimed` inside `save`; exactly one `call_service` across both |
| R14 | Exception or kill after the claim is written and before the undo receipt is written (F3) | no receipt, no execution; a later undo supersedes the claim under the lock and proceeds; never two sends |
| R15 | Deadlock guard (F3) | the undo `save` path enters `transaction()` exactly once (counted by a test double) and never calls `super().save()`; a test with a bounded timeout fails if the path blocks on `.declarations.lock` |

**Persistence and downgrade tests (§5.13; #395 §7.3 N4).** Fixtures come from
the new writer only.

| # | Scenario | Expected |
|---|---|---|
| P1 | Exact preceding shipped image starts on a state directory holding a device-configuration receipt, execution record, `entity:` hold and both sidecars | it reads none of the new namespace, redispatches nothing, and its stale recovery does not release, transfer or redispatch the unknown-owner lock; its `control_power` on that switch refuses `lock_conflict` |
| P2 | Roll forward after P1 | the new runtime resumes the task read-only and the hold is still present |
| P3 | Restart after the declaration, before dispatch intent | task resumes or ends with zero dispatch; never two sends |
| P4 | Restart after dispatch intent, before the send | treated as possibly sent; no redispatch; held |
| P5 | Restart after the send, before readback | recovery is read-only observe; no redispatch; outcome from readback; held unless verified |
| P6 | Restart after verification, before the verified-observation sidecar | undo refuses `device_configuration_undo_unavailable` |
| P7 | Restart during an undo `save`, after the claim and before the undo receipt (the only way a claim exists without a receipt, §5.11.2) | claim superseded only because no undo receipt exists; no double undo |
| P8 | Registry reverted (profile absent) with an in-flight task | new calls refuse before HA I/O; the in-flight task stays held with no redispatch; undo refuses |
| P9 | New runtime loads existing power, fan and helper records and locks; new fan and power receipts are written | hashes and behavior identical; fan and power receipt bytes and refusal order identical to the base, including if the build extracts a shared locked helper from `FanService.save` (§5.11.2) |

**Success tests (required alongside the refusals).** `select` power-on
behavior change verified and undone (verified); `switch` child lock on to off
verified and undone; `number` sensitivity (the Matter 1..N integer range) set
and undone (verified), each also in the §9.2 lane on the signed build; as
offline unit tests only, through the unit-test hook that is absent from the
signed build (§9.2; no such kind is admitted in the first build and these
results never count toward G4), a `select` enumerated sensitivity verified
and a `number` duration timeout set and undone (verified) with no unit option
present, proving the generic contract; an
asynchronous readback that succeeds on a later poll; a compensating forward
change after drift that is verified and not labelled as undo; an exact
owner-authorized change whose disclosed consequence is high (critical-load
power-on, physical lock) still dispatches without confirmation or approval
(B11; tests/AGENTS.md).

## 9. Acceptance

### 9.1 Source acceptance (offline, in the build PR)

- All N, S, T, C, R and P tests and the success tests pass.
- Profile compilation: the new profile compiles, its fingerprint is recorded,
  and every existing fingerprint is byte-identical to the base.
- Tool count and schema snapshots change only by the one new tool.
- The never-sent list is asserted against the gateway's payload builder.
- The embedded `UNIT_CONVERTERS` device-class set equals the pinned Core
  2026.11.0 source.
- The compiled target-kind allowlist is exactly the three triples of §4.1
  (B15, B16), and each `translation_key` and `entity_category` matches the
  cited Matter source at Core 2026.11.0 commit `74cf5a49` (T6).

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
- **The signed build only (review F1).** The G4 run executes the exact
  build whose compiled profile carries the final three-triple allowlist (B15,
  §4.1) and the final fingerprint. It contains no test seam, no extra triple
  and no test-only confirmation trigger; a check before the run asserts that
  the profile fingerprint the lane loads equals the fingerprint recorded for
  signing, and the run fails if it does not. A fixture kind that the signed
  allowlist refuses can only appear in the run as a refusal case; it can
  never contribute a verified dispatch to G4.
- **Admitted kinds, through Core's real `matter` integration.** Each of the
  three admitted kinds must exist in the lane as a real Core 2026.11.0
  `matter` entity, so the registry `platform` is `matter` and the
  `translation_key` comes from Core's own discovery schema, not from fixture
  code. Core's Matter integration talks to a Matter Server over WebSocket
  (`MatterClient(entry.data[CONF_URL], ...)`,
  `core:components/matter/__init__.py:106`; `start_listening` at `:362`;
  requirement `matter-python-client==1.5.5`, `manifest.json`). The lane
  therefore runs a **lane-owned, test-only Matter Server stub** next to the
  Core container and creates the `matter` config entry with the stub's URL:
  - The stub implements only the server side of the WebSocket protocol subset
    that `matter-python-client==1.5.5` uses for connect, `start_listening`,
    node delivery, `write_attribute` and attribute-updated events. That
    subset is derived from the pinned client source at build time and is
    **unknown at this head**; if the client needs a command the stub cannot
    answer faithfully, the build stops and reports it rather than patching
    Core or the client.
  - It serves node dumps taken unchanged from Core's own test fixtures at
    `74cf5a49`, identified by sha256:
    `tests/components/matter/fixtures/nodes/eve_energy_20ecn4101.json`
    (`48a7a21131eee56e47a0db856d21134fd6bc983295f633ea9e3aef6d122de58d`),
    whose Core snapshot entities include
    `switch.eve_energy_20ecn4101_child_lock_top`
    (`tests/components/matter/snapshots/test_switch.ambr:570`) and the
    `power_on_behavior` selects (`test_select.ambr:1212`, `:1275`);
    `eve_energy_plug.json`
    (`1ed129e955c909f119e40303b234bdc2b83dfadd8347f26570e59e0ef71fa14d`;
    `select.eve_energy_plug_power_on_behavior`, `test_select.ambr:1338`); and
    `aqara_door_window_p2.json`
    (`5fdde93c56c824795be29ccbb2dd9b919f4ff5a4b17df0f9220d82c675e0836f`;
    `number.aqara_door_and_window_sensor_p2_sensitivity`,
    `test_number.ambr:21`). Those snapshots are Core's own record that the
    dumps produce the three admitted `(domain, platform, translation_key)`
    kinds with `entity_category: config`.
  - All three admitted kinds write through `write_attribute`
    (`core:components/matter/select.py:121-123` for
    `MatterAttributeSelectEntity`; `switch.py:274` for `MatterNumericSwitch`,
    the `EveChildLock` class at `:536-545`; `number.py:143-145` for
    `MatterRangeNumber`, whose `sensitivity_level` description has no
    `command`, `:540-563`; client call at `entity.py:459-477`). The stub
    records each write (node, endpoint, attribute path, value), applies it to
    its node model and pushes the attribute-updated event, so Core's state
    changes through its normal update path.
  - Fault modes live in the stub, so they apply to the admitted kinds: delay
    the attribute update past the first readback poll, return an error for a
    write, stall a write past the send timeout, store a different value than
    written, and report the attribute update without the write (drift).
  - The stub, its node-dump copies and their hashes, and the lane step that
    starts it are protected-path changes the build task must name (a
    `.github/workflows/` overlay plus a test-only lane asset); the stub's
    source sha256 and the three dump hashes are recorded in the PASS receipt.
  - Residual: the stub stands in for the Matter Server and the device. It
    proves Core 2026.11.0's entity creation, registry fields, service
    handling, write path and state update for the admitted kinds, not a real
    Matter Server's or a real device's timing. Real devices are proven only at
    deployed acceptance (§9.3).
- **Refusal fixture (not counted as dispatch evidence).** The repo-owned,
  test-only custom component in the style of
  `tests/fixtures/real_ha_device_migration/custom_components/beta23_device_fixture`
  stays, for refusals only: config-category `select`, `switch` and `number`
  entities on a non-`matter` platform (T2), a non-config switch, a diagnostic
  select, a disabled config entity, a config entity without a device, an
  unavailable duplicate, and a converter-class number with a translated unit
  (§5.5.1 rule 2). Every case must end with zero `call_service` sends. Lane
  setup may use `config/entity_registry/update` to set a number unit option
  or disable an entity on the fixture; that is fixture setup, never the
  product path. Installing it needs the same workflow overlay.
- **Former Q9 test seam (superseded by F1).** The seam that added the
  fixture kind to the allowlist is removed from the lane. It may remain only
  as a unit-test hook for the generic `select`/`number` mechanism (the generic
  success tests in §8), it is not compiled into the signed build (asserted by
  a test that loads the signed profile and finds exactly three triples and no
  hook), and no result obtained through it counts toward G4.
- **Lane tests (Codex D4), all on the signed build:** for each admitted kind
  (the `startup_on_off` select, the `child_lock` switch in both directions,
  the `sensitivity_level` number) a verified forward change and a verified
  undo, each with exactly one recorded stub write; delayed state reporting
  verified on a later poll; rejected and stalled writes each followed by
  readback, exactly one write each, and the `entity:` hold present; a
  stored-value mismatch held as `verification_failed`; numeric bounds and
  step per N22–N26 on the sensitivity number (1..N from
  `SupportedSensitivityLevels`); the composite-identity checks at `once()`
  (N12 including the registry-id variant, R1, R4); wrong-domain, disabled,
  diagnostic, non-config, device-less and non-admitted-kind entities refused
  with zero sends; S8 (no `confirmation_required` for any admitted kind); and
  C1/C2 against the lane's `control_power` on the Matter child-lock switch.
  The confirmation mechanism with its test-only trigger (S1–S7, B14) is proven
  offline in the build PR, outside the G4 run, because that trigger is not
  constructible in the signed build.
- **Receipt contents required for G4:** the lane image, the profile
  fingerprint loaded (equal to the signing fingerprint), the stub and dump
  hashes, and for every admitted kind at least one `succeeded_verified`
  forward change and one `succeeded_verified` undo with the recorded stub
  write and the readback fingerprint. A receipt missing any admitted kind
  does not satisfy G4.
- **Binding to signing:** the group's PASS receipt is recorded against the
  exact final profile fingerprint and lane image. Its sha256 is one of the
  `review_artifacts_sha256` of the single combined 2.6 2026.11.0
  `core-capability-extension-evidence-v1` (B5;
  `scripts/prepare_core_release_registry.py:108-126`). Any profile change
  after the run invalidates the receipt and requires a rerun. No signing is
  prepared without it.

### 9.3 Deployed acceptance (after a separately authorized release, signing and deployment; not part of this design)

Preconditions: each acceptance entity below is checked from the entity
registry: `entity_category == "config"` and a `(domain, platform,
translation_key)` equal to one of the three admitted triples (B9, B15, §4.1).
The 2026-11-08 read confirmed platform and descriptor only, so this check
reads `translation_key` and `entity_category` from the registry itself.

- **Power-on behavior `select` on the master bedroom TV Eve plug or the
  kitchen Eve plug only** (B9; never the garage beer fridge or network cabinet
  plug): change, readback `succeeded_verified`; then `undo`, readback
  `succeeded_verified` and "original change undone (verified)".
- **Child-lock `switch` on the same TV or kitchen Eve plug:** the same.
- **Sensor-tuning `number`:** the Aqara back-porch sensor's Matter
  `sensitivity_level` number (the only admitted sensor-tuning kind): the same.
- Josh confirms the physical device behaves as reported. Home Assistant state is
  not physical proof.
- Negative live checks: a non-config `switch`, a diagnostic entity and a Tapo
  detection `select` are refused, with no dispatch in the audit log. The
  dropped kinds are refused the same way: the Tapo digital-sensitivity
  `number` (`device_configuration_target_kind_not_admitted`) and the Aqara
  sensitivity `select` (`device_configuration_target_kind_not_admitted`, or
  `device_configuration_target_disabled` if it is disabled, as the legacy
  Matter vendor selects are by default).

#### Superseded (historical): Tapo and Aqara-select acceptance

The bda3ace0 revision allowed the sensor-tuning acceptance on the Aqara
`select` or a Tapo digital-sensitivity `number`. Both kinds are dropped (B15,
B16), so neither is an acceptance target.

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
- Household target kinds, Matter (in-tree, so pinned at Core 2026.11.0; B15, §4.1):
  [`components/matter/manifest.json`](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/matter/manifest.json) (`matter-python-client==1.5.5`);
  [`components/matter/select.py` L426-L450](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/matter/select.py#L426-L450) (`MatterStartUpOnOff`, `startup_on_off`, admitted),
  [L451-L471](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/matter/select.py#L451-L471) (`SmokeCOSmokeSensitivityLevel`, `sensitivity_level`),
  [L617-L619](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/matter/select.py#L617-L619) (legacy vendor selects kept until 2026.11.0),
  [L620-L648](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/matter/select.py#L620-L648) (Aqara contact legacy select),
  [L649-L678](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/matter/select.py#L649-L678) (Aqara occupancy legacy select),
  [L679-L705](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/matter/select.py#L679-L705) (Heiman legacy select);
  [`components/matter/switch.py` L536-L545](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/matter/switch.py#L536-L545) (`EveChildLock`, `child_lock`, admitted),
  [L472-L490](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/matter/switch.py#L472-L490) (`EveTrvChildLock`, same key);
  [`components/matter/number.py` L539-L563](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/matter/number.py#L539-L563) (`BooleanStateConfigurationCurrentSensitivityLevel`, `sensitivity_level`, admitted);
  [`components/matter/strings.json` L283-L285](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/matter/strings.json#L283-L285) ("Sensitivity"),
  [L394-L395](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/matter/strings.json#L394-L395) ("Power-on behavior"),
  [L737-L739](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/matter/strings.json#L737-L739) ("Child lock");
  [`components/matter/helpers.py` L123-L134](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/matter/helpers.py#L123-L134) (`unique_id` format; rejected descriptor-key alternative)
- Dropped kinds, `tapo_control` custom integration (not Core), latest release 7.2.13, tag commit `6d2c8adb81f3551070ec62fd9590f26254df145a` (the installed version is unknown, §4.1):
  [`custom_components/tapo_control/number.py` L290-L345](https://github.com/JurajNyiri/HomeAssistant-Tapo-Control/blob/6d2c8adb81f3551070ec62fd9590f26254df145a/custom_components/tapo_control/number.py#L290-L345) (`TapoMotionDetectionDigitalSensitivity`, no `translation_key`);
  [`custom_components/tapo_control/light.py` L99](https://github.com/JurajNyiri/HomeAssistant-Tapo-Control/blob/6d2c8adb81f3551070ec62fd9590f26254df145a/custom_components/tapo_control/light.py#L99) (`nightlight`, the integration's only `translation_key`)

## 11. Security section

- **New write reachability:** yes, deliberately. Four exact services
  (`select.select_option`, `switch.turn_on`, `switch.turn_off`,
  `number.set_value`) on registry-proven config-category device entities of
  admitted target kinds, through one ordinary typed control tool with no plan
  and no approval panel (B8). This is a security architecture change that
  needs the explicit scope (this design plus the recorded decisions), negative
  tests (§8) and focused review named in AGENTS.md.
- **No approval is not "no governance":** the shipped ordinary-control
  guarantees apply unchanged: signed Core admission, closed input, durable
  declaration before execution, operation-id freshness and rebinding refusal,
  stale-state chain, atomic locks with lease validation before intent and
  send, at-most-once dispatch, independent readback, durable hold for
  unresolved sends, and audit (§2.5, §5.7–§5.9, §5.12). Item 25 removes the
  external approval step and the plan, not these.
- **Chat confirmation:** the `control_helper` mechanism, reused verbatim. It
  is model-conveyed, recorded as such (`external_approval: false`), and is
  not an authorization boundary against a misbehaving client; it is a
  disclosure step. The first-build trigger is **empty** (B14): no admitted
  target asks for confirmation. The mechanism is still built and proven end
  to end with a test-only trigger that a production build cannot construct
  (§8 S7, S8), so a later trigger adds a rule, not new machinery.
- **No generic forwarding:** the payload is built from closed input. No caller
  string reaches `domain`, `service` or the target type. `call_service` stays
  refused.
- **No fallback:** direct HA gateway only. There is no ha-mcp delegation and no
  route to `call_service`.
- **Provider admission:** signed profile required, for Core 2026.11.0 only.
  Until signed, refusal happens before HA I/O.
- **Target-kind admission:** a compiled allowlist of exactly three triples in
  the signed contract (B15, B16, §4.1): `(select, matter, startup_on_off)`,
  `(switch, matter, child_lock)` and `(number, matter, sensitivity_level)`.
  Everything else is unreachable, including smoke/CO alarm smoke sensitivity
  and the legacy vendor sensitivity selects (their shared triple
  `(select, matter, sensitivity_level)` is not admitted), every `tapo_control`
  entity (no `translation_key`), and any `null`-key entity.
- **Disclosed breadth (B16 (ii), B17):** a triple admits every entity that carries
  it, not only the household targets. `(switch, matter, child_lock)` also
  admits the Eve thermostat valve keypad lock (`EveTrvChildLock`).
  `(number, matter, sensitivity_level)` admits any Matter
  `BooleanStateConfiguration` device with the sensitivity feature, such as
  water-leak, rain and contact sensors; lowering a leak sensor's sensitivity
  can make it miss a leak (§5.6 disclosure). `(select, matter,
  startup_on_off)` admits the power-on behavior of any Matter endpoint exposing
  `OnOff.StartUpOnOff`, lights and wall switches included, not only the 5
  plugs; that breadth is accepted and disclosed with no device-type filter
  (B17, Josh decision (in-session 2026-11-08), Q10 option (a)), and power-on
  behavior is disclosure only on every admitted entity (B11). Every
  admitted entity still needs the full §5.5 proof on every call.
- **Concurrency:** the new tool shares `control_power`'s exclusive
  `entity:<entity_id>` key in the one durable lock store, so two Engineering
  paths never dispatch concurrently to one entity; unresolved sends hold the
  entity until the typed hold release (§5.7, §5.12).
- **Recovery surface (flag for review):** the build adds the kind
  `device_configuration` to the Ingress typed hold release
  (`E/approval_web.py:72`) and a dedicated `device_configuration` branch in
  `_typed_service` (`:814-824`), whose last branch becomes an explicit
  `power` case with `None` for anything else, so a device-configuration hold
  is never reviewed or released against the power service (§5.12, review
  L2). That page releases a durable hold after a
  read-only review and dispatches nothing. It is a recovery surface, not a
  change approval, and it never makes undo available. Codex should confirm
  that this addition does not reintroduce an approval step into the change
  path.
- **Undo authority:** undo is bound to registry and device identity and the
  exact post-change observation, consumed at most once through a durable claim
  that the `save` override checks and writes in the same single
  `.declarations.lock` transaction as the undo receipt, without nesting that
  lock; two concurrent undos serialize on it (§5.11.2, review F3).
  Compensating changes are ordinary forward changes and never claim undo
  (§5.11).
- **Final pre-send identity check:** the `once()` re-read immediately before
  the send compares the full composite observation, registry `id` and device
  identity included, not only the state (§5.7, review F2).
- **Numeric exactness:** Engineering refuses rather than rounds, clamps or
  converts; readback compares numerically and never upgrades a changed value
  (§5.5.1).
- **Category forgery:** `entity_category` cannot be set through
  `config/entity_registry/update` (§3.1). An integration (including MQTT
  discovery) can still label an entity config. The domain and service
  allowlist and the target-kind list, not the category, bound what is sent.
- **Indirect targets:** only one exact `entity_id`. Device, area, floor and label
  targets are never sent.
- **Persisted state:** two new additive sidecar models; no existing format
  changes; downgrade behavior and its unproven part are stated in §5.13.
- **Existing contracts:** `core.typed_power_operation` and every other signed
  profile are unchanged (B4).
- **Audit:** every forward change, undo, confirmation-required result, hold
  and hold release is audited with target identity, prior value, requested
  value, lock keys, safety gate, `external_approval: false` and outcome
  (§5.14). Option strings are bounded (255) and sanitized as in HAMCP-145.
- **Bounded output:** `options` list at most 256 entries of at most 255
  characters. Device name is bounded. Sidecars at most 4096 bytes.
- **Secrets:** none read or added.

## 12. Build plan

1. **Prerequisites (done 2026-11-08):** the read-only registry read
   confirmed platform and descriptor for the §4.1 targets, `translation_key`
   and `entity_category` were confirmed from pinned source, Q8 is decided
   (B14, empty trigger), Q9 is decided (B15, B16, three triples) and Q10 is
   decided (B17, power-on triple breadth accepted; the allowlist is unchanged).
2. Build PR on the 2.6.0 line (protected Engineering runtime path, named and
   authorized in that task), in the `control_helper` file layout:
   `E/device_configuration/contracts.py` (closed input, statuses, refusals),
   `E/device_configuration/adapter.py` (`DeviceConfigurationAdapter`, lock set,
   payload builder, composite observation, numeric contract),
   `E/device_configuration/service.py` (`DeviceConfigurationService`, signed-only
   authority, confirmation gate in `before_declaration`, sidecars, the `save`
   override that writes the undo claim and receipt under one transaction
   (§5.11.2), request summary), with the adapter's `dispatch` override for
   the composite `once()` re-read (§5.7),
   `E/tools/device_configuration.py` (the tool), `E/tools/registry.py`, the
   `CoreIndependence` entry and constants in `E/ha_core_readmission/routes.py`,
   the explicit `_contract` in `E/ha_core_readmission/profiles.py`, the
   `ProviderCapability` member in `E/providers/routing.py`, the capabilities
   and provider-contract entries in `E/capabilities.py`, and the
   `TYPED_HOLD_KINDS` entry and the dedicated `_typed_service` branch in
   `E/approval_web.py` (§5.12). **No change** to
   `E/f3_configuration/locks.py`, the F3 plan tools or `control_power`.
   Tests per §8, including the three-triple allowlist (T1–T6), the
   test-only-trigger confirmation proof (S7, S8), the composite `once()`
   variant (N12), the undo-claim concurrency and deadlock tests (R13–R15)
   and the hold-release resolver (C8).
3. Compute and record the new fingerprint. Assert that every existing
   fingerprint is unchanged.
4. Persisted-state evidence per §5.13 (P1–P9), including the run against the
   exact preceding shipped image with writer-generated fixtures.
5. Core 2026.11.0 lane acceptance per §9.2 on the signed three-triple build:
   the test-only Matter Server stub with the pinned upstream node dumps, the
   refusal fixture component, acceptance module, evidence group, and the
   workflow overlay step (protected `.github/workflows/` path and lane asset,
   named in that task). PASS receipt bound to the final fingerprint, with a
   verified forward change and undo for every admitted kind (review F1).
6. Signing: the one combined 2.6 `extend-capabilities` for 2026.11.0 (B5),
   including this profile and its lane receipt. This is a separate,
   owner-authorized action.
7. Release and deployment: separately authorized.
8. Deployed acceptance per §9.3 on the TV or kitchen plug and the Aqara
   back-porch Matter sensitivity `number` (B9, B15).

**2.5.0 overlap:** the build will touch `E/ha_core_readmission/routes.py`,
`profiles.py`, `E/providers/routing.py`, `E/capabilities.py`,
`E/tools/registry.py` and `E/approval_web.py`. In-flight 2.5.0 PRs (#386
HAMCP-327 `control_helper`, #387 HAMCP-288, #385 HAMCP-319, HAMCP-290, #384
HAMCP-309 Part 3) may touch some of these. The citations marked `@1c4c32b3`
in this design come from HAMCP-327 work that is not at the design base. 2.5.0
wins shared files. The build catches up by merging `main` (never a rebase)
after 2.5.0 lands, and re-verifies the cited `control_helper` patterns at
that point. This design PR itself shares no file with them.

## 13. Open items

### 13.1 Decided

| # | Item | Decision | Attribution |
|---|---|---|---|
| Q1 | Use-case intent | Power-on behavior, child/physical lock, sensor tuning (B3) | Josh direction (register 2026-11-07; confirmed in-session 2026-11-08) |
| Q2 | `number` in the first slice | Yes, as a design consequence of B3 (§3.4, §5.5.1) | design consequence of B3, not a separate Josh decision |
| Q3 | `control_power` and config-category switches | (a) `control_power` keeps them; the paths are serialized by the shared lock (B12) | Josh decision (in-session 2026-11-08), item 4 |
| Q4 | Signing placement | One combined 2.6 signing, Core 2026.11.0 only (B5) | Josh direction (register 2026-11-07; confirmed in-session 2026-11-08) |
| Q5 | Per-change bounds | Single entity per change (B10) | Josh decision (in-session 2026-11-08), item 2 |
| Q6 | Extra confirmation for high-consequence settings | Disclosure only for power-on behavior (B11) | Josh decision (in-session 2026-11-08), item 3 |
| Q7 | Names | Profile `core.device_configuration_entity_operation` accepted; the tool name `create_device_configuration_plan` is superseded by item 25 (B13). `control_device_configuration` is this design's proposal | Josh decision (in-session 2026-11-08), items 5 and 25 |
| E1 | First-build targets | §4.1 (B9), with registry confirmation before build and acceptance, Tapo detection selects excluded, acceptance on the TV or kitchen plug only. Narrowed by B15 and B16 (below) | Josh decision (in-session 2026-11-08), item 1 |
| Q8 | First-build safety trigger | (a) none: the mechanism ships with an empty trigger, and the build PR proves it end to end with a test-only trigger (`dispatch: no_dispatch`, `task_created: false`, no send; the acknowledged call dispatches; audit `safety_gate` / `safety_confirmation`; `external_approval: false`) (B14, §5.6, §8 S7–S8) | Josh decision (in-session 2026-11-08) |
| Q9 | Target admission | (a) a compiled `(domain, platform, translation_key)` allowlist inside the signed contract, each key confirmed from integration source at the pinned Core and integration version; a target with no `translation_key` is dropped (B15, §4.1) | Josh decision (in-session 2026-11-08) |
| Q9 follow-up | Shared and generic triples | (i) drop the Aqara sensitivity `select` (its triple is shared with the smoke/CO smoke-sensitivity select); (ii) accept and disclose the breadth of `(switch, matter, child_lock)` and `(number, matter, sensitivity_level)` (B16, §4.1, §11) | Josh decision (in-session 2026-11-08) |
| Q10 | Breadth of `(select, matter, startup_on_off)` | (a) accept and disclose: keep the triple as is with no device-type filter; it admits the power-on behavior of any Matter endpoint exposing `OnOff.StartUpOnOff` (lights, wall switches and so on), not only the 5 plugs. Does not block the contract freeze (B17, §4.1, §11) | Josh decision (in-session 2026-11-08) |
| — | Registry confirmation | Done 2026-11-08 by the architect's read-only registry read (register HAMCP-297): platform `matter` with descriptors `MatterStartUpOnOff`, `EveChildLock`, the Aqara select and number descriptors; platform `tapo_control` for the camera numbers. `translation_key` and `entity_category` were not exposed and are confirmed from source (§4.1). The resulting first build is Matter only, exactly three triples | Architect's register record (HAMCP-297, 2026-11-08); source reads in this design |
| — | Shape | Switch-like ordinary typed control with exact readback and undo, chat confirmation only when safety-critical, no approval panel and no plan (B8) | Josh decision (in-session 2026-11-08), item 25 |

### 13.2 Pending Josh

None. Every owner question raised by this design is decided (§13.1).

#### Superseded (historical): Q8, Q9 and Q10 as pending questions

The bda3ace0 revision listed Q8 (first-build safety trigger; options (a)
empty trigger, (b) extend the `control_helper` dependency-evidence builder,
(c) owner-named exact targets) and Q9 (target admission; options (a) compiled
triple allowlist, (b) admit every config-category kind and deny only Tapo
detection, (c) owner-configured exact entity ids) as pending Josh, with the
registry confirmation as the next human action. All three are now resolved
(§13.1). The cb38e75c revision then listed Q10 (breadth of
`(select, matter, startup_on_off)`; options (a) accept and disclose, (b) narrow
it with an admission rule beyond the triple, such as a Matter device-type
filter; recommended (a)) as pending Josh. It is resolved as (a) (B17, §13.1).

**Next human action:** none is pending Josh on this design. The build
itself, signing, release, deployment and deployed acceptance
remain separately authorized actions (§12).

## 14. Codex design review 2026-11-08 — disposition

Review of head `c22af225` against base `7246f2d2`. All five findings were
corrected in `d020118a`; no finding is deferred. Item 25 (B8) changed the
shape of several corrections; the safety property each finding asked for is
kept. The "Shape after item 25" column states what changed.

| ID | Severity | Finding (short) | Correction (d020118a) | Shape after item 25 (this revision) |
|---|---|---|---|---|
| D1 | High | Concurrency exception let `control_power` and the new apply dispatch concurrently | §2.3; §5.7: exclusive `entity:<entity_id>` shared with `control_power` through a `device_configuration_entity` → `entity` mapping, full lock set, atomic acquisition, ownership through dispatch and readback, selective hold until `reconcile_child`. Tests C1–C7 | Kept: the same exclusive `entity:` key in the same store, now taken directly by the ordinary adapter (`E/fan/adapter.py:85-93`), so no resource-type mapping and no `reload:` key. Hold release is the HAMCP-192 typed hold release instead of `reconcile_child`. The early lock release after a pre-entity validation rejection is dropped (every post-send exception holds; more conservative). Tests C1–C8 |
| D2 | High | Rollback did not bind target identity or the post-apply observation | §5.5 item 6; §5.11: recorded identity and post-apply fingerprint, checked at rollback creation and dispatch; compensating change distinct from undo. Tests R1–R8, N17, N21 | Kept: the same identity and exact post-state binding, now checked at undo preparation, preflight and `once()`. Undo is the `undo` action of the same tool, not an approved `rollback_change` plan. New: the verified-observation sidecar and a once-only undo claim (§5.11). Compensating change is an ordinary forward change, no `compensates_plan_id`. Tests R1–R12, N17, N21 |
| D3 | Medium | Slice and acceptance omitted the answered sensor-tuning target | §3.4, §3.5, §4, §5.5.1 numeric contract in the one new profile. Tests N22–N26 | Unchanged in substance; `value` is a canonical decimal string so exactness is checked on the caller's literal (§5.2). E1 numeric targets noted (§5.5.1) |
| D4 | Medium | Acceptance lacked the exact-Core disposable lane | §9.2 lane acceptance bound to the final fingerprint and the combined 2026.11.0 extension evidence | Kept. Lane tests adapted to direct control and undo (no approve or apply step); confirmation and non-admitted-kind cases added; Q9 test-seam residual stated |
| D5 | Low | Wrong power profile id and obsolete signing alternatives | `core.typed_power_operation` throughout; B4/B5; proving check in §6 | Unchanged. §6 drops the F3 identity; the operation class is the typed ordinary operation with an explicit `_contract` |
| Note | — | Config-category persistence presented as universal | §5.6 qualified | Unchanged; now in the control result |

Also changed by item 25: N1's refusal code is now
`device_configuration_core_authority_unavailable` (was
`pending_signed_registry_admission`); N11 is a declared `no_change` (was a
plan-creation refusal); N20 is the operation-id rebind/expiry refusal (was
approval hash mismatch). Counts in §7 remain base-local deltas.

## 15. Josh decisions 2026-11-08 — disposition

| Item | Decision (attribution: Josh decision (in-session 2026-11-08), item N) | Sections changed |
|---|---|---|
| 25 | Device-configuration changes are switch-like control like `control_helper`: direct typed control, exact readback and undo, chat confirmation only when safety-critical, no approval panel and no plan; the signed profile stays additive | Title; §1 B8; §2.5 (new template); §5 rewritten (§5.1–§5.14); §6 (no F3 identity); §7; §8 (N1, N11, N20, N27, S, C5, C7, C8, R9–R12, P); §9; §11; §12; §13; §14 shape column |
| 1 | E1 first-build targets: power-on selects on 5 plugs, child-lock switches on 4 Eve plugs, Aqara light-sensor sensitivity (select and number 1..3), Tapo digital sensitivity numbers 1..100; Tapo detection selects excluded; live acceptance on the TV or kitchen plug only; registry confirmation required | §1 B9; §4.1 (new); §5.5 item 5; §5.5.1 E1 note; §8 T1–T5; §9.1; §9.2 fixture; §9.3; §13 (E1 decided, Q9 new) |
| 2 | Q5: single entity per change | §1 B10; §5.2; §5.12; §13 |
| 3 | Q6: disclosure only for power-on behavior | §1 B11; §5.6; §8 S3; §13 (Q8 new for the remaining targets) |
| 4 | Q3: `control_power` keeps config-category switches, serialized by the shared lock | §1 B12; §5.7; §5.10 (old options superseded); §8 C1–C2; §13 |
| 5 | Q7: profile name accepted; tool name superseded by item 25 | §1 B13; §5.1; §6; §7; §13 |
| Q8 | (a) no safety confirmations in the first build: the mechanism ships with an empty trigger and is proven end to end with a test-only trigger | Header; §1 B14; §2.5 (confirmation row); §5.6 (decided; historical Q8 note); §8 S7, S8; §11 (chat confirmation); §12 steps 1–2; §13 (Q8 decided, historical note) |
| Q9 | (a) compiled `(domain, platform, translation_key)` allowlist in the signed contract, keys confirmed from source at the pinned Core and integration version; targets without a `translation_key` dropped (the Tapo digital-sensitivity `number`) | Header and provenance note; §1 B15 (B9 status note); §4 select row; §4.1 (allowlist, dropped list, registry confirmation, rejected descriptor-key alternative); §5.5 item 5; §5.5.1 E1 note; §6 signing row; §8 T1–T6 and success tests; §9.1; §9.2 residual; §9.3; §10 (Matter and `tapo_control` citations); §11; §12; §13 |
| Q9 follow-up | (i) drop the Aqara sensitivity `select` (triple shared with the smoke/CO smoke-sensitivity select); (ii) accept and disclose the breadth of the `child_lock` switch and `sensitivity_level` number triples | §1 B16; §4.1; §5.6 (smoke unreachable; sensing disclosure); §8 T1, T3, T4; §9.3 (Aqara select acceptance removed, historical note); §11 (disclosed breadth); §13 (Q9 follow-up decided; Q10 new) |
| Q10 | (a) accept and disclose the breadth of `(select, matter, startup_on_off)`: kept as is with no device-type filter; it admits power-on behavior on any Matter endpoint with `OnOff.StartUpOnOff` (lights, wall switches and so on), not only the 5 plugs; does not block the contract freeze | Header; §1 B17; §4.1 (heading, intro, allowlist breadth column); §11 (disclosed breadth); §12 step 1; §13 (Q10 decided; §13.2 empty; historical note) |

The registry confirmation of the §4.1 targets (platform and descriptor) is
the architect's read-only register record of 2026-11-08, not a Josh decision;
`translation_key` and `entity_category` come from the pinned source cited in
§4.1 and §10. The one question the source read raised, Q10 (breadth of the
power-on triple), is decided (B17); nothing is pending Josh.

No decision conflicted with the stated architecture or safety constraints.
Undo is newly specified, because the shipped `control_*` tools have none
(§2.5); B2 and B8 require it.

## 16. Cursor full review 2026-11-09 — disposition

Independent review (Cursor) of head `84f0c5be` against base `7246f2d2`,
posted 2026-11-09 5:48 PM CST on PR #389, verdict "Ready after corrections".
No Critical or High finding. All five findings are corrected in this
revision; none is deferred. Josh's recorded decisions (B9–B17, §15) are
unchanged. No source code changes.

| ID | Severity | Finding (short) | Correction (sections) | Proving tests |
|---|---|---|---|---|
| F1 | Medium | The lane seam admits a fixture kind the signed allowlist refuses, so its PASS is evidence of a different fingerprint and cannot satisfy G4 | §9.2 rewritten: the G4 run executes only the signed three-triple build (fingerprint asserted equal to the signing fingerprint); each admitted kind is a real Core 2026.11.0 `matter` entity created from Core's pinned test node dumps served by a lane-owned test-only Matter Server stub; all three write through `write_attribute`; fault modes move into the stub; the custom component remains for refusals only; the seam survives only as a unit-test hook absent from the signed build and never counts toward G4. Also §5.5.1 rule 2, §6 (G4 sentence), §8 success tests, §12 step 5 | §9.2 lane tests and receipt contents (a verified forward change and undo per admitted kind); T2; the signed-profile test that finds exactly three triples and no hook |
| F2 | Medium | The cited `once()` re-reads state only (`E/fan/adapter.py:115-119`), so inherited it always refuses or misses an identity replacement before the send | §5.7: `DeviceConfigurationAdapter` overrides `dispatch`; `once()` re-reads the full composite (state, registry get, device list), compares it with the prepared fingerprint, re-checks the allowlist, then runs `before_dispatch`; shipped outcome mapping (`:126-158`) kept; undo uses the same override. Also §5.8 item 1, §2.5 `once()` row, §11 | N12 registry-id variant; R1; R4 |
| F3 | Medium | The undo claim was placed in `before_declaration`, outside the `.declarations.lock`, so two undos could both pass the check | §5.11.2: the claim is checked, superseded if allowed, and written by a `DeviceConfigurationService.save` override inside the one existing `transaction()` (`E/fan/service.py:120-127`), before the receipt bytes; no `super().save()` and no second `transaction()` (a nested flock deadlocks); the atomicity argument is stated. Also §2.5 rows, §5.13 table, §11, §12 step 2 | R10 (reworded), R13 (concurrent undos), R14 (claim without receipt), R15 (single transaction, deadlock guard), P7, P9 |
| L1 | Low | §5.13 cited §8 P1–P8; the table ends at P9 | §5.13 now cites P1–P9 | — |
| L2 | Low | `_typed_service` resolves every non-fan kind to the power service (`E/approval_web.py:814-824`), so adding the kind to `TYPED_HOLD_KINDS` alone would release against power | §5.12: a dedicated `device_configuration` branch, an explicit `power` branch and `None` (refused) for anything else; injected test mapping gets the key. Also §2.5 hold-release row, §11 recovery surface, §12 step 2 | C8 (resolves `DeviceConfigurationService`, never power; unknown kind refused) |
