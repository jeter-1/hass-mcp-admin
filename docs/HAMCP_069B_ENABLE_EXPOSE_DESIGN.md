# HAMCP-069B governed entity enable/disable and voice-assistant exposure — design

Status: **owner decisions recorded 2026-10-22 (§0), including OD-2c (i)
(§0.3). Part 2 implements both resource types inactive.** This document does
not authorize deployment, live Home Assistant access, registry signing or a
release.

## 0. Owner decisions (Josh, 2026-10-22)

### 0.1 Decisions

These supersede the recommendations in §13 where they differ.

- **OD-1:** one new sibling tool, two resource types (availability;
  exposure).
- **OD-2:** (a) first verify against the signed sequence-12 registry
  contract whether `config/entity_registry/update` with `disabled_by` is
  already admitted under the existing reviewed Core profile. If yes,
  enable/disable ships active under sequence 12. If no, report exactly what is
  missing and stop before inventing a profile. (b) Exposure gets its new Core
  profile written and tested, inactive until the sequence-13 registry, which
  is signed together with the Core 2026.11 admission. No registry is created,
  signed or modified, and there is no unsigned local admission.
- **OD-3:** Google Assistant (through Home Assistant Cloud; primary) and Assist
  (`conversation`) only. **Alexa is out of scope.** For Google, the readback
  proves only the stored setting; a cloud YAML filter can override it, and
  this is disclosed.
- **OD-4:** protected objects are locks, alarm panels, covers of device class
  garage, gate or door, valves, sirens, and safety sensors (smoke, carbon
  monoxide, gas, moisture, safety). Unresolved is protected.
- **OD-5:** as proposed in §6.2, except as OD-9 states. Exposure changes are
  standard. Exposing a protected object, disabling a protected object or an
  entity a protected automation depends on, and enabling a protected entity
  that is still exposed (5c) are elevated.
- **OD-6:** enabling `integration`-disabled entities is allowed, with
  disclosure. `device`, `config_entry` and `hass` are refused with exact
  reasons.
- **OD-7:** at most 25 entities per plan; an enable plan covers one config
  entry (one reload); execution stops on the first failure. Amended to 8 per
  plan (§0.4).
- **OD-8:** (a) refuse an undo when there was no prior exposure setting to
  restore.
- **OD-9 (supersedes §6.3):** protected cases are classified `elevated_admin`
  under the **existing** elevated approval mechanism. There is no change to the
  approval core and no HAMCP-213 per-object acknowledgement. The approval page
  carries a plain-language consequence statement (for example, "This exposes
  lock.front_door to Google Assistant: anyone who can speak to it may unlock
  the door without a PIN"; "Disabling this breaks automation X").
- **OD-10:** exposure of entities not in the registry is refused.
- **OD-11:** Google `disable_2fa` and the "expose new entities" setting are
  excluded.
- **OD-12:** `hidden_by` stays in 069A-1.

### 0.2 OD-2(a) verification result: not admitted

Verified at `main` `816de8db02f224fb6610c6b6aaad43a5d20b2b38` against
`upstream-trust/ha-core-release-registry.json`, envelope sequence 12 (entries
2026.10.0 and 2026.10.1). The answer is **no**. Enable/disable cannot ship
active under sequence 12.

What sequence 12 admits:

- 22 capabilities per entry. The only entity-registry write among them is
  `core.entity_registry_metadata_operation`, profile
  `core_entity_registry_metadata_operation_v1` version 1, adapter
  `compiled-core-entity-registry-metadata-operation-v1`, contract fingerprint
  `sha256:cc09630efbc0e1e6d0a587696406f6502c62fe730a767bc0a8cfc96a9988f767`.
- That fingerprint is recomputed from the compiled profile
  (`ha_core_readmission/profiles.py:472-499`) and matches exactly. The
  existing test `tests/test_hamcp069a_entity_metadata_readmission.py` pins the
  same equality (20 tests passed locally).

What that signed contract covers, and why `disabled_by` is outside it:

- Its `response_contract.fields` is the closed list `aliases`, `area_id`,
  `categories`, `hidden_by`, `icon`, `labels`, `name`
  (`profiles.py:488-491`). `disabled_by` is not in it.
- Its required check is `websocket_entity_registry_update_closed_fields`,
  which is the same closed list.
- The reviewed provider declares `disabled_by` in `never_sent`
  (`governance/entity_metadata.py:118-127`), and its gateway refuses any key
  outside `{type, entity_id, *MANAGED_FIELDS}` with `closed_payload_violation`
  (`entity_metadata.py:582-596`).
- The route binding admits the metadata route only
  (`ha_core_readmission/routes.py:66-73`, :155-158).
- The other admitted write profiles do not cover entity-registry writes:
  `core.governed_configuration_operation` is limited to "existing typed
  governed provider only", `core.typed_helper_operation` to typed helpers, and
  `core.f3_mutation_verification` is verification only. Routing a
  `disabled_by` write through any of them would be an unsigned widening of a
  signed contract, which OD-2 forbids.

What is missing for enable/disable to be active:

1. A Core capability profile whose signed contract covers
   `config/entity_registry/update` limited to `disabled_by ∈ {"user", null}`,
   with exact readback. Either a new profile (the §4.2
   `core.entity_registry_availability_operation`) or a changed 069A profile.
   A changed 069A profile changes fingerprint `cc09630e…` and un-admits the
   live `create_entity_metadata_plan` until it is re-signed (§4.1).
2. A signed registry sequence that pins that profile's fingerprint for the
   supported Core releases. Sequence 12 does not, and no later sequence exists.

Per OD-2(a), implementation stopped here. No profile, schema, runtime, test,
registry or version change was made at that point. Josh then decided OD-2c (§0.3).

### 0.3 OD-2c decision: (i), both resource types inactive

Josh decided OD-2c = (i) on 2026-10-22:

- Enable/disable and exposure are both implemented now. Each has its own new
  Core profile (`core.entity_registry_availability_operation` and
  `core.entity_assistant_exposure_operation`). The signed 069A profile is not
  modified, so `create_entity_metadata_plan` stays live.
- Both stay **inactive**. A request is refused with the reason
  `pending_signed_registry_admission`, and `list_capabilities` and the health
  snapshot report the same status.
- They activate only with a future signed registry sequence (planned:
  sequence 13, with the Core 2026.11 admission). No registry is created, signed
  or modified, and there is no unsigned local admission.
- The release therefore activates nothing new. The release notes and the
  acceptance document say so, and both live canaries (enable/disable on one
  harmless entity; expose/unexpose one harmless entity to Assist) are
  documented as to-do after sequence 13.
- Tests prove both resource types are refused while unadmitted, and that a
  profile admitted by a test registry fixture behaves correctly end to end, so
  activation later is only the signing.

Effects on the body of this design:

- Alexa (`cloud.alexa`) is out of scope (OD-3). Every mention of three
  assistants below now reads two: `conversation` and `cloud.google_assistant`.
  The baseline fingerprint (§4.4) still records the stored `cloud.alexa` value,
  so an Alexa-side change is detected, but 069B never writes it.
- §6.3 is superseded by OD-9. Protected cases are `elevated_admin` under the
  existing elevated approval, with no safety-critical acknowledgement binding
  and no change to approval machinery. The plan carries plain-language
  consequence statements that the approval page shows.

### 0.4 OD-7 amendment: at most 8 entities per plan

Implementation found that the limit of 8 operations is enforced well beyond
the generic configuration route:

- the approval page (`approval_web.py` `MAX_REVIEW_OPERATIONS = 8`);
- persisted review validation and approval/dispatch contract checks
  (`service.py` `MAX_CONFIGURATION_OPERATIONS`);
- F3 sequencing and locks (`f3_configuration/sequence.py`, `locks.py`);
- persisted F3 repository validators (`f3_runtime/repository.py`);
- configuration reverification (`MAX_OPERATIONS = 8`).

A plan of 9-25 entities could be created but never approved, and raising the
bound would change the approval core, which OD-9 excludes. Josh decided on
2026-10-22 to cap the family at **8 entities per plan**, the existing shared
bound. No approval, F3 or persistence bound changes. Raising the limit to 25 is
a separately reviewed follow-up. Every "25" in §7.1 and elsewhere now reads 8;
the single-config-entry rule for enable and stop-on-first-failure are unchanged.

- Register: HAMCP-069 (this increment is recorded as 069B; the row stays
  Active).
- Base: `main` at `816de8db02f224fb6610c6b6aaad43a5d20b2b38`, Engineering
  `2.4.1-beta.29`. HAMCP-131 (beta.30) and HAMCP-132 slice 2 (beta.31) are
  pending. The implementation takes the next free beta when it materializes.
- Static tools at base: 62 (25 canonical and 37 Engineering-native). Connector
  total: 90 (91 with the optional YAML reader). See
  `tools/registry.py:178-179` and `docs/V2_4_1_BETA29_ACCEPTANCE.md:60,205`.
- `.release/` contains only `README.md`. No next version is declared.
- Pinned Core (`ci/support-window.yaml`):
  - current is 2026.10.1 (`abe1fdd7cd6b4de1715c87b6fd87d92699514ef9`);
  - rollback is 2026.10.0 (`6a811d3359c7b2076dc9e1cf900843a129c044af`);
  - there is no candidate.
- Every Core citation below was checked at 2026.10.1. All cited files are
  identical at 2026.10.0. 2026.10.2 (`8d7fd8b6`) is outside the window. Its only
  change to a cited file is at `components/cloud/alexa_config.py:562`, which now
  also suppresses `RequireRelink`. That change does not affect this design.
- Source authority follows ADR-021: exact-tag Core source only. No live
  observation was made, and none is used.
- Templates: the 069A design (`HAMCP_069A_ENTITY_METADATA_DESIGN.md`) and the
  HAMCP-213 safety-critical design (`HAMCP_213_SAFETY_CRITICAL_APPROVAL_DESIGN.md`).

Core paths are relative to `homeassistant/`. Engineering paths are relative to
`hass_mcp_engineering_beta/ha_mcp_engineering/`.

## 1. Scope

This design covers:

- **Enable and disable** of entities in the entity registry, by setting
  `disabled_by` to `user` or `None`.
- **Voice-assistant exposure** of entities in the entity registry, by setting
  `should_expose` to true or false for one named assistant.

Out of scope, as the brief limits:

- device enable/disable;
- config-entry and integration enable/disable;
- the `expose_new_entities` setting (read only, for disclosure; §3.3);
- Google `disable_2fa`;
- direct `.storage` edits;
- new add-on permissions;
- workflow, signed-registry, support-window and stable-v1 changes.

`hidden_by` already ships in 069A-1 as the `hidden` field (§12, A2; OD-12).

## 2. Core contract: enable and disable

### 2.1 The only command

`config/entity_registry/update` is defined in
`components/config/entity_registry.py`:

- It requires an admin user (`@require_admin`, :154).
- `disabled_by` accepts only `None` or `"user"` (:179-186). `hidden_by` has the
  same restriction (:187-194).
- One message can carry several fields. `options_domain` and `options` are
  applied in a second, separate write (:274, :286), so a combined message is
  not atomic.

069B therefore sends exactly one shape per child:

```json
{"type": "config/entity_registry/update", "entity_id": "<exact id>", "disabled_by": "user"}
{"type": "config/entity_registry/update", "entity_id": "<exact id>", "disabled_by": null}
```

No other key is ever sent. In particular, 069B never sends `options`,
`options_domain`, `hidden_by`, `name`, `new_entity_id` or `area_id`.

### 2.2 Response

The response is `{"entity_entry": <extended dict>}` (:295-305). It may also
contain the following keys, but **only when the request sets
`disabled_by: None`**:

- `require_restart: true`, when the entity has no config entry, or its entry
  does not support unload;
- otherwise `reload_delay: 30`.

These keys depend only on the request. Core returns them even if the entity was
already enabled. They are reported as disclosed consequences. They never prove
that anything happened.

### 2.3 Side effects (corrects the brief)

The handler is `EntityRegistryDisabledHandler`, in `config_entries.py:4221`
(registered at :2251).

- **Disabling never triggers a reload.** The handler returns early when the
  entity is now disabled or has no config entry (:4250-4259). Disabling removes
  the entity's state at once (`helpers/entity.py:1458-1510`, 1661-1662).
  Consumers then see an entity that does not exist, not one that is
  `unavailable`.
- **Enabling schedules a reload of the whole config entry.** This applies only
  when:
  - the entity has a config entry;
  - that entry supports unload (:4265); and
  - the previous `disabled_by` was not `config_entry` (:4311-4324).

  The delay is `RELOAD_AFTER_UPDATE_DELAY = 30` s (:245). One global timer is
  re-armed on every qualifying event (:4271-4281), so all enables across all
  entries are coalesced into one batch, 30 s after the last change. The reload
  is `async_reload` of the entry (:2596-2631): every entity of that entry
  becomes unavailable and is set up again, not just the target.
- **Some enables need a restart.** If the entry does not support unload, or
  there is no entry, the entity stays absent until Core restarts. 069B never
  restarts Core. The plan says so in advance.
- Enabling an entity whose previous `disabled_by` was `config_entry` returns
  `reload_delay`, but nothing reloads, and the entity stays effectively off
  while its entry is disabled (§2.4).
- Disabling does not change exposure options. Exposure survives a disable and
  takes effect again on enable (§6.4).

### 2.4 Refusals

Core enforces these:

| Condition | Core behaviour | Citation |
|---|---|---|
| Entity not in the registry | `not_found` ("Entity not found") | :212-216 |
| Enabling while the device is disabled | `invalid_info` "Device is disabled" | :247-258 |
| Other `ValueError` | `invalid_info` | :275-277, :289-291 |

069B adds these Engineering refusals at plan time, before any dispatch. Each
reason is an exact, stable string:

| Reason | Condition |
|---|---|
| `entity_not_in_registry` | No registry entry; a state-only entity has no unique_id and cannot be disabled. |
| `entity_already_disabled` | Disable requested, but `disabled_by` is already non-null. Overwriting `integration` or `config_entry` with `user` would lose the original reason, and rollback could not restore it. |
| `entity_already_enabled` | Enable requested, but `disabled_by` is already null. This is the 069A no-op rule (§3.4 there). |
| `entity_disabled_by_device` | Enable requested while `disabled_by` is `device`. Core would refuse; the device must be enabled first, which is out of scope. |
| `entity_disabled_by_config_entry` | Enable requested while `disabled_by` is `config_entry`. It would have no effect (§2.3); the entry is out of scope. |
| `entity_disabled_by_hass` | Enable requested while `disabled_by` is `hass`. This is a Core-internal reason. |
| `entity_disabled_by_integration` | Only if Josh rejects OD-6. |
| `entity_not_disableable_engineering_dependency` | The target is an entity that Engineering's own approval or health path reads, if any exists. The implementation must derive this from source; today the inventory is empty. |
| `entity_locked_by_inflight_plan` | Lock `entity_registry:<entity_id>` is held, for example by a 069A plan (§7.4). |

There is no Core list of entities that cannot be disabled. Within the registry,
Core accepts a disable for any entity.

### 2.5 Exact readback

The readback uses `config/entity_registry/get`, the same read 069A uses
(`components/config/entity_registry.py:98-124`). The extended dict includes
`disabled_by`, `hidden_by`, `options`, `config_entry_id`, `device_id` and
`modified_at`.

`list_for_display` is **not** acceptable as proof, because it omits disabled
entities.

Verification requires all of the following:

- `disabled_by` equals the requested value exactly.
- Every other field in the registry guard (069A `normalize_entry`, extended
  below) is unchanged.
- Exposure keys that existed before are unchanged.

New `options[<assistant>]` keys are **tolerated and reported**, not treated as
a mismatch. Alexa's registry-update handler calls `async_should_expose`, which
persists a computed default the first time it sees an entity (§3.4).
Verification does not rely on state presence: an enabled entity appears only
after the reload or a restart.

## 3. Core contract: voice-assistant exposure

### 3.1 Assistants

`KNOWN_ASSISTANTS = ("cloud.alexa", "cloud.google_assistant", "conversation")`
is defined at `components/homeassistant/exposed_entities.py:25`.

- No command lists which assistants are configured.
- Both cloud ids are accepted even when Home Assistant Cloud is not set up.
- Which assistants exist in Josh's install is **unknown** from source (OD-3).

### 3.2 The only write

`homeassistant/expose_entity` (:395-414):

- requires an admin user;
- `assistants` is a list of `In(KNOWN_ASSISTANTS)`;
- `entity_ids` is a list of strings;
- `should_expose` is a bool;
- the result is empty.

**It performs no check on existence, state or domain.**

- For a registered entity, the value is stored in the registry at
  `options[<assistant>]["should_expose"]` (:151-167). This fires a registry
  update event.
- For any other string, the value goes to the separate `exposed_entities`
  store, saved with a 10 s delay (:30, :169-192).

069B sends exactly one entity and one assistant per child:

```json
{"type": "homeassistant/expose_entity", "assistants": ["<one assistant>"], "entity_ids": ["<exact registered id>"], "should_expose": true}
```

Unregistered ids are refused (`entity_not_in_registry`). This keeps every write
in the registry, where it can be read back. Bulk lists are never sent.

### 3.3 Defaults and "expose new entities"

`async_should_expose` and `_is_default_exposed` (:246-312) compute the default:

- Only `conversation` defaults to exposed (`DEFAULT_EXPOSED_ASSISTANT`). The
  cloud assistants default to not exposed.
- The default applies only when `expose_new_entities` is on for that assistant.
- Entities with an `entity_category` or a non-null `hidden_by` are never
  exposed by default (:308-312).
- Default-exposed domains: climate, cover, fan, humidifier, light,
  media_player, scene, switch, todo, vacuum, water_heater. **lock,
  alarm_control_panel, valve and siren are not on this list.**
- binary_sensor device classes exposed by default: door, garage_door, lock,
  motion, opening, presence, window.
- sensor device classes exposed by default: aqi, co, co2, humidity, pm10,
  pm25, temperature, voc.

`homeassistant/expose_new_entities/get` returns `{"expose_new": bool}`
(:445-477). It is a pure read and is shown as disclosure. 069B never sends
`expose_new_entities/set`.

### 3.4 Reads that write (security-relevant)

`async_should_expose` **persists the computed default** the first time it
evaluates an entity (:246-271). Any read that goes through it is therefore a
write. 069B must never call it, directly or through a Core command that wraps
it.

Two reads are safe:

- `config/entity_registry/get`, reading `options[<assistant>].should_expose`.
  This is the authoritative read. A missing key means "never evaluated, Core
  default applies".
- `homeassistant/expose_entity/list` (:417-442), which uses the read-only
  `async_get_entity_settings` (:226-244). It returns only `true` values, so
  `false` and "never evaluated" look the same. 069B does not use it.

`cloud/status` **must never be called**. Its prefs include
`google_secure_devices_pin` (`components/cloud/http_api.py:1156`,
`prefs.py:257`).

The upstream `ha_get_entity_exposure` connector tool
(`providers/upstream_reads_8_6.py:364-382`) remains a delegated read. 069B does
not depend on it.

### 3.5 Effective exposure versus stored preference

- **conversation (Assist):** the stored preference is the effective exposure.
  The default agent clears its slot lists when exposure changes
  (`components/conversation/default_agent.py:357`, 1319-1330).
- **Cloud assistants:** a YAML `filter` in the cloud configuration overrides
  `should_expose` completely (`google_config.py:288-295`,
  `alexa_config.py:312-320`). It cannot be observed without reading cloud
  configuration, and that read is out of bounds here. The readback therefore
  proves the **stored preference** for cloud assistants, not the effective
  exposure. The plan states this (OD-3).
- Cloud syncs happen after a delay: Google is debounced 15 s, and Alexa uses
  1 s and is based on diffs. These syncs are not verified.

### 3.6 What exposure enables (consequence disclosure)

- **Assist has no PIN.** If a lock is exposed to Assist, a voice "unlock" works
  without a PIN. The intent `OnOffIntentHandler` maps `HassTurnOff` on a lock
  to `lock.unlock` (`components/intent/__init__.py:180-251`).
  Covers (open/close) and valves are handled the same way.
- **Google** requires a PIN challenge for lock unlock, alarm arm/disarm and
  covers of device class door, garage and gate, unless `disable_2fa` is set
  (`components/google_assistant/trait.py:1771`, 1888-1904, 2455-2459,
  2596-2601, 2759-2770). 069B never sets `disable_2fa`. The secure-devices PIN
  itself is not observable, because `cloud/status` is forbidden.
- **Alexa:** Home Assistant adds no PIN for unlock. Any Alexa-side voice code
  is outside Home Assistant and cannot be observed.

These facts are the main reason exposure of a protected object is escalated
(§6).

### 3.7 Readback

The readback uses `config/entity_registry/get`. Verification requires:

- `options[<assistant>].should_expose` equals the requested bool exactly;
- the other two assistants' keys that existed before are unchanged; and
- `disabled_by` and the registry guard are unchanged.

The command's empty result is never treated as proof.

## 4. Plan family (sibling, not an extension)

### 4.1 Why a sibling

Extending 069A-1 would change the contract fingerprint of
`core.entity_registry_metadata_operation`. Its fingerprint inputs are defined
by `_contract` in `ha_core_readmission/profiles.py:109-142`. They include the
checks, the `response_contract` fields and the predispatch rules.

Envelope 12 of `upstream-trust/ha-core-release-registry.json` (HAMCP-182)
pins that exact fingerprint for 2026.10.0 and 2026.10.1. Admission
(`registry.py` selections; `models.py:309-324`) requires exact equality.

So any edit would **immediately un-admit the shipped
`create_entity_metadata_plan`** until a new signed sequence existed. 069A-1
also excludes these operations in eight separate places:

- the closed schema;
- the planner's unknown-field refusal (`entity_metadata_planning.py:142-147`);
- provider `never_sent` (`entity_metadata.py:121-127`);
- the registry guard hash;
- the gateway allowlist (:582-595);
- the resources `MANAGED_FIELDS` subset (`resources.py:757-787`);
- rollback restoring only `MANAGED_FIELDS` (`runtime.py:4091-4096`);
- the signed profile.

A sibling family leaves all of these untouched.

### 4.2 Proposed shape (OD-1)

- **One tool**, `create_entity_availability_exposure_plan`. This makes 63
  static tools and a connector total of 91 (92 with the YAML reader).
- The input is closed (`additionalProperties: false`):

  ```json
  {
    "operation": "disable | enable | expose | unexpose",
    "entity_ids": ["<1..8 exact registry ids>"],
    "assistant": "conversation | cloud.google_assistant",
    "reason": "<bounded text>"
  }
  ```

  `assistant` is required for expose and unexpose, and refused for enable and
  disable. There is no "all assistants" value. There are no free-form fields.
  Unknown keys are refused, as in 069A F8.
- **Two resource types:**
  - `entity_registry_availability`, with operation
    `set_entity_disabled_by`;
  - `entity_assistant_exposure`, with operation
    `set_entity_assistant_exposure`.
- **Two new Core capability profiles:**
  - `core.entity_registry_availability_operation`, with checks for
    `entity_registry/get`, an `update` limited to `disabled_by`, and exact
    readback;
  - `core.entity_assistant_exposure_operation`, with checks for
    `entity_registry/get`, an `expose_entity` limited to one assistant and one
    entity, and exact readback of `options`.

  Each profile has its own adapter id. Neither is added to
  `CORE_CAPABILITY_PROFILES`, so the probe fingerprints do not change. They go
  in the runtime profiles, as 069A did.
- **Dormancy:** every supported Core fails closed with
  `CoreCapabilityUnavailable` until a separate owner-signed Core registry PR
  (sequence 13) pins both new fingerprints. Signing is out of scope here
  (OD-2). Besides the MCP tool gate and the F3 gates, the governance service
  checks admission first, before any Home Assistant read or plan storage
  (review 1, M1). It does this for every entry point: the planning service,
  the internal plan builder and the F3 inverse. It fails closed when no Core
  runtime is composed, and it holds an exact-target Core lease from the first
  read to persistence.

### 4.3 Places the new types must be registered (fail-closed checklist)

An unknown `resource_type` is **not** prohibited by the generic policy
(`policy.py` `configuration_operation_policy` 618+ falls through to 805-826).
It fails closed only later, at these points:

- strategy (`strategies.py:512-514`);
- migration (`migration.py:44-51`);
- locks (`locks.py:40`, 181-182);
- resources (`resources.py:51-59`, 991-994);
- F3 registry `target_type` (`f3_runtime/registry.py:277-281`);
- routes (`f3_requirements`).

069B therefore adds an explicit policy branch for each type. It mirrors
`_entity_metadata_operation_policy` (`policy.py:506-589`, 634-635): exactly
one marker is required, and anything else is `PROHIBITED`.

The new types must also be registered in:

- the service route and its operation guard;
- `SUPPORTED_RESOURCE_TYPES` and the gateway;
- the F3 strategy, the migration allowlist and the adapter registration;
- the F3 registry entry. This changes `registry_sha256`, as 069A did, and is
  not signed;
- the lock prefix;
- the rollback builder;
- the capability entry;
- the Core class, profile and route requirement;
- the health snapshot.

Downgrade: a beta.29 binary that loads a persisted 069B plan would classify it
through the generic policy, but it fails closed at apply. A test proves this
from the real beta.29 load path, as 069A F12 did.

### 4.4 Plan content and stale-state refusal

The immutable plan content holds, for each target:

- the exact entity_id;
- the registry baseline fingerprint, computed over:
  - `disabled_by`, `hidden_by`, `config_entry_id`, `device_id`,
    `entity_category`, `device_class` and `original_device_class`;
  - the three `options[<assistant>].should_expose` values, where "absent" is a
    distinct value;
- the config entry's `supports_unload`, from a read-only config-entries read;
- the protected-object classification and its reason (§6.1);
- the consumer-evidence fingerprint (§5);
- the requested value.

`modified_at` is deliberately excluded. Alexa's default-persistence (§3.4) can
change it with no meaningful change to the entity.

At preflight, immediately before each dispatch, the planner re-reads
`entity_registry/get` and recomputes the fingerprint.

- Any difference is refused as `entity_registry_baseline_changed`. Nothing is
  dispatched.
- The dependency index is re-fenced, and a changed consumer fingerprint is
  refused as `dependency_evidence_changed`. This reuses `resources.py:835-872`.

## 5. Impact analysis (dependency index)

Reverse lookup filters `DependencyIndexSnapshot.findings` on
`target_entity_id`. It uses:

- `DependencyIndex.get` and `open_source_fence`
  (`dependency/index.py:230-339`);
- `select_dependency_findings` (`dependency/service.py:317-345`);
- relations from `_relation_for` (`extraction.py:3233-3258`).

The existing 069A disclosure helpers are reused:

- `read_runtime_dependency_snapshot`;
- `consumer_evidence_fingerprint`;
- `physical_consumer_consequence`.

These live in `entity_metadata_disclosure.py:95-285`. A **new builder keyed on
`target_entity_id`** is needed, because 069A matches label and area
membership, not entity ids.

Coverage is disclosed as it is. Missing coverage is never shown as "no
consumers".

| Consumer kind | Coverage | Source |
|---|---|---|
| Automations, including blueprint instances | authoritative | `provider.py:1674-1797` |
| Scripts | partial, diagnostic partition only | `script_sources.py:486-497` |
| Scenes, groups, templates | `unavailable` | hard-coded in the provider |
| Dashboards | `unavailable` (per-dashboard analysis only; no reverse index) | — |
| Voice assistants | not a dependency; covered by §3.6 consequence text | — |

What the disclosure says for each operation:

- **Disable:** each consumer with its relation. For each automation that names
  the target in a trigger or condition, the plan says that "the trigger will
  no longer fire" or "the condition sees a missing entity".
- **Enable:** the config entry, the reload consequence (§2.3), and the number
  of other entities in that entry. That count comes from a read-only
  `entity_registry/list` filtered by `config_entry_id`.
- **Expose:** the §3.6 voice-actuation text for the domain and device class.
  For cloud assistants, it adds the YAML-filter caveat and the current
  `expose_new` value.

`change_impact_analysis` already has a read-only `disable_entity` operation
(`impact/models.py:13`, `impact/rules.py:130-148`). 069B reuses its rule text,
so the two stay consistent.

## 6. Protected objects and HAMCP-213 mapping

### 6.1 Classification (a union; OD-4)

A target is **protected** if any of the following applies:

1. The shipped floor applies: `protected_object_reason(entity_id)`
   (`governance/physical_effects.py:250-261`) gives:
   - `sensitive_domain_lock`, `sensitive_domain_alarm_control_panel`,
     `sensitive_domain_valve`;
   - `garage_cover` (a `cover.*` id containing "garage");
   - `water_control` (id terms water, shutoff, shut_off, main_valve).
2. The domain is `cover`, and the registry `device_class`, or else
   `original_device_class`, is `garage`, `gate` or `door`. This is the
   HAMCP-213 D5/OD-7 rule. Josh decided it, but **it has not shipped**.
   Applying it here is local to 069B (reason `cover_device_class_<class>`).
3. The domain is `siren` (D5/OD-7; not shipped; reason `siren_domain`).
4. The entity is a **safety sensor feeding a protected automation**: a
   trigger or condition source of an automation whose
   `AutomationActionRiskProfile` has a physical consequence on an object
   covered by rules 1-3.
   - This reuses the causal-source join in `governance/helper_dependency.py`
     (`_causal_automation_sources` :346-379, which excludes action-data and
     target relations; `build_helper_dependency_risk_binding` :1275).
   - Reason: `feeds_protected_automation:<automation id>`.
   - For scripts, coverage is partial. A target that a script references, where
     that script's physical-effect comparison
     (`compare_physical_effects("script","create",None,cfg)`) shows a
     protected effect, also counts. Partial coverage is disclosed.
5. Optional (OD-4b): a `binary_sensor` of class `smoke`, `carbon_monoxide`,
   `gas`, `moisture` or `safety`, whether or not it feeds an automation.
   Disabling one silences a life-safety signal that may be used outside Home
   Assistant automations (notifications, the alarm panel).

If the index is unavailable, stale or out of bounds, the classification is
`unresolved`. Unresolved is treated as protected, the same fail-closed
direction as physical-effect `unresolved`.

One shared predicate decides "evidence complete" for every disable
classification path: planning, the policy recompute, the preflight consumer
fingerprint and the F3 inverse (review 1, H1). It is
`dependency_evidence_gaps` in `entity_availability_exposure_planning.py`.
Each gap becomes the reason
`dependency_evidence_incomplete:<gap>`, and the consequence statement says
"impact could not be fully determined". The gaps are:

- the index unavailable;
- the Home Assistant version unsupported or unavailable;
- an unsupported obligation-ledger model;
- automation or blueprint coverage that is not complete or has failed items;
- the bounded index payload exceeded;
- automation read failures;
- an obligation-ledger or dynamic-reference overflow;
- an automation obligation that is a coverage failure or a target-capable
  opaque consumer;
- script evidence unavailable, with read failures, or bounded.

Staleness cannot arise, because each read awaits a fresh build (fenced at
preflight). Two things are disclosed but are not gaps: the structural
`partial` coverage of a working script reader, and the permanently
`unavailable` scenes, groups, templates and dashboards (§5).

### 6.2 Proposed policy (OD-5)

| Operation | Ordinary target | Protected target (§6.1) |
|---|---|---|
| expose | standard | **elevated + safety-critical acknowledgement** |
| unexpose | standard | standard (it reduces voice reach) |
| disable | standard, with impact disclosure | **elevated + safety-critical acknowledgement** |
| enable | standard, with the reload/restart warning | standard. OD-5c: escalate if any assistant's stored `should_expose` is true, because enabling then restores voice actuation |

How F2 represents this:

- A standard plan requires `plan_approval` only.
- An escalated plan is `elevated_admin` with `safety_critical_acknowledgement`.
  `policy.py:1320-1343` already maps this to `plan_approval` plus
  `safety_critical_acknowledgement`, with no exactness shortcut.

A plan's class is the highest class among its children. The acknowledgement
lists every protected child as a separate checkbox.

### 6.3 Required change to the HAMCP-213 binding (security-sensitive)

The safety-critical flag is set **only** by `_safety_critical_comparison_policy`
(`policy.py:224-300`). It returns `None` unless the resource type is automation
or script (:237-238). The binding is built by `plan_acknowledgement_binding`
and `compare_operation` (`physical_effects.py:1440-1528`).

For any other family, today's code would raise
`safety_critical_acknowledgement_unbindable`.

069B therefore needs a closed, per-resource-type binding provider for its two
types.

- Each object row is `(operation_id, entity_id)`.
- The projection hash covers:
  - the operation;
  - the requested value;
  - the assistant;
  - the protection reason;
  - the baseline fingerprint.
- The dispatch gate at `service.py` ~12997-13030 recomputes the binding before
  each dispatch, unchanged. A mismatch raises
  `SAFETY_CRITICAL_ACKNOWLEDGEMENT_MISMATCH`.
- The approver, same-principal, Ingress-only and HAMCP-205 verifier rules stay
  exactly as they are.

`MAX_ACKNOWLEDGED_OBJECTS = 32` (:1531) bounds this, and the 8-entity limit
(§7.1) stays below it. This is a change to approval machinery, so it needs the
review's security section.

### 6.4 Exposure survives disable

Disabling a protected entity leaves its exposure options as they are (§2.3).
The plan reports the current stored exposure for all three assistants. That
way, a later enable does not silently restore voice actuation (OD-5c).

## 7. Execution

### 7.1 Bulk and grouping (OD-7)

- A plan has one operation kind and 1-8 entities (§0.4; OD-7 said 25). For expose and unexpose,
  one assistant.
- **Enable plans are limited to a single config entry.** The approval then
  covers exactly one reload blast radius (or one "restart required" set).
  Core's reload timer is global (§2.3), so enables from two different plans
  within 30 s are still coalesced. The plan discloses this and does not try to
  prevent it.
- Disable, expose and unexpose plans may span config entries. Their disclosure
  is grouped by config entry.
- Children are ordered by config entry, then entity_id. Execution is
  non-atomic and stops on the first failure, following ADR-017 semantics:
  - only the first dependency-satisfied child dispatches;
  - an unresolved child blocks the rest;
  - remaining children report `LATER_OPERATIONS_UNDISPATCHED`.
- The generic configuration route's limit of 8 operations
  (`MAX_CONFIGURATION_OPERATIONS`, `service.py:379`) and 069A's single-child
  rule (`policy.py:1032-1044`) are **not** changed. The family's own guard
  enforces the same limit of 8 (§0.4).

### 7.2 At-most-once and outcomes

Each child gets exactly one dispatch, then one authoritative readback. Errors
are classified as in 069A:

- `unauthorized` means not dispatched;
- any other error, or a lost connection, means **uncertain**. Recovery is by
  readback only, and there is never a second dispatch.

The no-op rule:

- At plan time, a target already in the desired state is refused.
- If it reaches that state between preflight and dispatch, the child reports
  V9 `desired_state_already_present_not_dispatched`.
- For exposure, "absent" never counts as the desired state. The effective
  default is not read (§3.4), so an explicit write is always made.

Outcomes per child:

- `succeeded_verified`;
- `verification_failed` (readback differs);
- `uncertain_not_retried`;
- `refused_<reason>`;
- `not_dispatched_after_prior_failure`.

`reload_delay` and `require_restart` are recorded with the child, as
disclosed consequences.

### 7.3 Audit

Each child writes one audit record. It contains:

- the plan hash;
- the child identity;
- the target;
- the requested value;
- the baseline and readback fingerprints;
- the outcome;
- the Core response keys from §2.2.

Outputs pass through the shared strict sanitizer (HAMCP-191). No cloud
preference, PIN or status field is ever read, so none can be echoed.

### 7.4 Locks

Both types take `entity_registry:<entity_id>`, the same prefix 069A uses
(`locks.py:37-38`). This serializes 069A and 069B on the same entity.

The Core-side reload after an enable happens asynchronously, 30 s later.
Engineering does not hold a reload lock for it, and the plan says so.

## 8. Rollback

Rollback is a governed reverse plan, never automatic, and it needs its own
approval:

- **Disable to enable:** sets `disabled_by: null`. The rollback carries the
  reload warning and is itself a standard enable.
- **Enable to disable:** sets `disabled_by: "user"`.
  - If the forward enable was over `integration` (OD-6), rollback writes
    `user`, not `integration`. Core accepts only `user` and `None`. This is
    disclosed at plan time.
- **Expose or unexpose with an explicit prior value:** writes the prior bool
  back.
- **Expose or unexpose with an absent prior key** ("never evaluated"): Core
  has no command that restores "absent". OD-8 has two options:
  - (a) Refuse rollback with `prior_exposure_preference_unset_not_restorable`
    (recommended).
  - (b) Write the opposite of the forward value.
- Any later edit to the entity refuses rollback, as 069A F11 does.

## 9. Core readmission

There are two new runtime profiles (§4.2), and each has a `static_tool_target`
using the shared `validate_entity_id` (069A V11/R1).

Until signed sequence 13 exists, every supported Core fails closed. Note that
the compiled `SUPPORTED_CORE_RELEASES` covers only 2026.7.2-2026.9.1; 2026.10.x
is admitted only through the signed registry.

HAMCP-165 review1 H1 audit: before that review, an F3 runtime composed
without a Core runtime ran a retained, approved 069B plan in the legacy no-Core
mode, with no dispatch-time admission check. The dormant-profile F3 gate now
covers both 069B resource types. It applies on first apply, on a retained plan,
and before every recovery, readback or private read-only reconciliation
observation (a held observation's verification stays held). With no Core the child
refuses `core_authority_unavailable`; with an unadmitted profile it refuses
`pending_signed_registry_admission`; either way no Home Assistant command is
sent. Generic `create_configuration_plan` routing refuses these types first
too, before any read or store. An unadmitted first apply refuses earlier
still, at apply entry, before any plan, task, child, manifest or lock record
is written and before any Home Assistant request; without an F3 runtime the
service refuses the same way instead of reading the registry and storing a
task (HAMCP-165, following the HAMCP-155 review finding).

Consequence: **the release acceptance's live canaries cannot run until a
separate signed-registry PR lands** (OD-2). The implementation PR body carries
the evidence package for that PR, as 069A did under D2.

## 10. Tests and mutations (Part 2 plan)

These come from the brief and are listed here so their scope can be agreed.

**End-to-end tests** (service path and F3 path), for each of disable, enable,
expose and unexpose.

**Refusal and policy tests:**

- each §2.4 refusal, with its exact reason;
- impact disclosure correctness, including the `unavailable` coverage rows;
- protected escalation, for each §6.1 rule including `unresolved`;
- stale baseline and changed dependency evidence refused;
- the reload/restart warning text for supports-unload true, false and none;
- grouping, the single-entry rule for enable, and the 8 and 9 limits (§0.4);
- a readback mismatch leading to `verification_failed`;
- a tolerated new Alexa options key;
- the cloud YAML-filter disclosure.

**Negative tests:**

- no unplanned writes: the gateway refuses any other key or command;
- `should_expose`, `expose_entity/list` and `cloud/status` are never sent;
- `expose_new_entities/set` is never sent;
- `disable_2fa` is never sent;
- the assistant cannot self-approve: approvals come only from Ingress, there is
  no tool-path approval, and the same-principal rule holds;
- the downgrade load path;
- the 069A schema and its signed fingerprint are byte-identical to base.

**Mutations:**

- skip readback;
- treat a protected target as ordinary;
- ignore stale state;
- write exposure for the wrong assistant;
- also: drop the single-entry enable rule, and accept an unregistered id.

## 11. Release

- The next free beta is set via `scripts/promote_next_release.py --apply`.
- Release docs: BETA notes, acceptance, CHANGELOG, README (also fixing the
  stale "87 tools" at `README.md:241`), TOOL_AUDIT and SECURITY.
- The acceptance doc includes two live canaries. Josh approves each one, and
  each is gated on signed sequence 13:
  - expose, then unexpose, one harmless entity to Assist;
  - disable, then re-enable, one harmless entity, chosen so that its entry's
    reload is harmless.

## 12. Findings against the brief's assumptions

| # | Assumption | Finding |
|---|---|---|
| A1 | Disabling needs the config-entry reload warning | Disabling never reloads (§2.3). **Enabling** reloads the whole entry after a coalesced 30 s, or needs a restart. The warning moves to enable. |
| A2 | `hidden_by` may be in scope | It already ships in 069A-1 as the `hidden` field, with the D6 refusal. Recommend leaving it there (OD-12). |
| A3 | Exposure can be read back from the exposure API | `async_should_expose` writes a default the first time it runs (§3.4). Only `entity_registry/get` options is safe and authoritative. |
| A4 | Which assistants are present can be determined | No command lists them, and `cloud/status` exposes the Google PIN, so it is forbidden. Presence is Josh's input (OD-3). |
| A5 | The dependency index covers automations, scripts, scenes and dashboards | Automations and blueprints are authoritative. Scripts are partial. Scenes, groups, templates and dashboards are `unavailable` (§5). |
| A6 | HAMCP-213 protected objects include garage covers by class, and sirens | Josh decided this (D5/OD-7), but it has not shipped. The live floor is the id-substring rule. 069B applies D5 locally (§6.1). |
| A7 | The safety-critical acknowledgement can attach to any family | It is hard-wired to automation and script. A closed binding provider is needed (§6.3). |
| A8 | The entity-metadata family can be extended | That would un-admit the signed 069A-1 profile (§4.1). A sibling family is needed. |
| A9 | 25 per plan fits existing limits | 069A is single-child and generic plans allow up to 8. A family-local limit of 25 fits within the 32-object acknowledgement bound. |
| A10 | `expose_entity` validates its target | It does not. It accepts any string and writes to a separate store. Unregistered ids are refused (§3.2). |
| A11 | Release canaries can run at release | Only after a separate signed-registry PR (§9). |
| A12 | Exposure to Assist of a lock is low-risk | Voice unlock works with no PIN (§3.6). |

## 13. Owner decisions requested

| # | Decision | Recommendation |
|---|---|---|
| OD-1 | A sibling family: one tool, two resource types, two Core profiles (§4.2). Or two tools (64 static). | One tool |
| OD-2 | Accept dormancy until a separate owner-signed sequence-13 PR; live canaries are gated on it. | Accept |
| OD-3 | Assistants in scope. Which are present in your install? | All three accepted. Cloud readback claims "stored preference" only, with the YAML-filter caveat. Or conversation-only if cloud is not used. |
| OD-4 | Protected-object union (§6.1 rules 1-4, with unresolved treated as protected). OD-4b: add the safety binary_sensor classes (rule 5). | Rules 1-4: yes. 4b: yes |
| OD-5 | Policy (§6.2): (a) exposure is standard, and expose of a protected target needs elevated + safety-critical acknowledgement; (b) disable is standard with disclosure, and a protected or feeding target needs elevated + acknowledgement; (c) enable is standard. **OD-5c:** escalate an enable of a protected target with stored exposure true. | (a), (b), (c) as proposed. 5c: yes |
| OD-6 | Enable over `disabled_by: integration` (for example, disabled-by-default diagnostic sensors). | Allow, with disclosure. Refuse `device`, `config_entry` and `hass`. |
| OD-7 | Bulk: 25 per plan, one operation kind; enable limited to one config entry; ordered stop-on-first-failure (§7.1). | As proposed |
| OD-8 | Rollback of an exposure write over an absent prior key. | (a) Refuse |
| OD-9 | Generalize the HAMCP-213 binding with a closed provider for the two new types (§6.3). This is an approval-machinery change, so the review needs a security section. | Yes |
| OD-10 | Refuse unregistered entity ids for exposure (§3.2). | Yes |
| OD-11 | Exclude Google `disable_2fa` and `expose_new_entities/set`; show `expose_new` read-only. | Yes |
| OD-12 | `hidden_by`: keep in 069A-1 (`hidden`) and leave it out of 069B. | Keep in 069A-1 |

## 14. Non-actions in Part 1

These were not done:

- no runtime, schema, test, workflow, signed-registry, support-window,
  stable-v1, release or version change;
- no live Home Assistant access, and no deployed MCP access;
- no credentials or secrets read;
- no Ready, merge, release or deploy.
