# HAMCP-222: signed-coverage audit and proposed seq-13 batch

**Status: read-only audit, docs only. STOPPED for Josh's decisions (§8).**
This document changes no code, profile, registry, version, workflow or
deployment. It accessed no live Home Assistant and no secret.

**Owner direction (Josh, 2026-10-23):** find out up front which planned features
need new signed Core profiles, so that **one** seq-13 signing covers everything
planned for the next few weeks. Today this is being discovered one feature at a
time: HAMCP-069B §0.2 and HAMCP-165 Part 0 each failed the signed-coverage gate.

## 0. Summary

| Result | Features |
|---|---|
| **Need new signed Core profiles** | 069B (2 profiles, already compiled), 165 (1 to 3), 155 (1), 106 (1, plus a provider decision), 071 (1 for scenes), 145 (1 per helper family), 134 (1, read-only) |
| **Fit an existing signed contract, subject to an owner interpretation** | 195 (same service, adapter and F3 identity as `core.typed_helper_operation`; see §3.11) |
| **Need no Core signing** (non-Core prerequisites or engineering-local only) | 149, 109, 181 PR2 + 214 + 193, journald option C (218) |
| **Outside a Core profile's reach / deferred** | 054 (HACS is not Core source), 151 (blocked on ha-mcp lifecycle admission; Core route only if re-scoped) |

- **Recommended seq-13 batch** (§6): 069B, 165, 155, 106, 071-scenes.
  That is 7 to 9 new profiles. 145, 134 and the 195 fallback profile are
  optional additions if Josh puts them in the next few weeks.
- **One signing is one owner session, not one sequence number.** The prep tool
  allows exactly one changed release entry per positive sequence step
  (`scripts/prepare_core_release_registry.py:165-166`, `:184-185`). Each Core
  version entry is therefore its own step. HAMCP-182 is the precedent: Josh
  signed sequences 10, 11 and 12 in one session
  (`docs/evidence/hamcp182/core-registry-hamcp182.md:4`, `:12-16`).
- **The evidence is the expensive part, not the ceremony.** Every new profile
  needs:
  - a disposable-container contract lane per support slot;
  - wiring for that lane in the protected `.github/workflows/ci.yml`;
  - receipts from one main full-tier CI run against the exact deployed runtime
    tree.

  To keep this to one evidence run and one signing, the batch must be frozen
  before it starts (§6.3).

## 1. Identity and method

| Item | Value |
|---|---|
| Base | `origin/main` `855da17832066b18913cc35541b9413238626d88` (fetched 2026-10-23) |
| Engineering version on main | `2.4.1-beta.32` (`hass_mcp_engineering_beta/config.yaml:2`) |
| Signed registry | `upstream-trust/ha-core-release-registry.json`, SHA256 `089247cafa29b3ab0c1687a63419d999ae9c894fa0a94248f73008eabf6785e6`; last envelope = sequence 12, expires 2027-01-13; entries 2026.10.0 and 2026.10.1, 22 capability references each |
| Support window | `ci/support-window.yaml`: current 2026.10.1, rollback 2026.10.0, candidate `null` |
| Core source | exact release archives for 2026.10.1 (commit `abe1fdd7cd6b4de1715c87b6fd87d92699514ef9`) and 2026.10.0 (`6a811d3359c7b2076dc9e1cf900843a129c044af`). Their SHA256 equals the signed `source_archive_sha256` of each entry. Core paths below are relative to `homeassistant/` in 2026.10.1 |
| 2026.10.0 vs 2026.10.1 | every Core file cited here is byte-identical in both. The only differences in the areas read are `auth/indieauth.py`, `auth/login_flow.py`, `automation/trace.py` and `frontend/manifest.json`, none of them relied on below |
| Fingerprints | recomputed with the hashed-lock environment (`requirements.lock` + `tests/requirements.lock`, `--require-hashes`) from `CORE_RUNTIME_CAPABILITY_PROFILES`: 24 compiled, 22 signed. All 22 signed references match the compiled fingerprint, profile id and adapter id for both entries |

**Repository paths.** `P` = `hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/profiles.py`,
`R` = `.../ha_core_readmission/routes.py`, and `E` = `hass_mcp_engineering_beta/ha_mcp_engineering/`.

**Rule applied.** A command is **ADMITTED** only if it is named by a signed
seq-12 profile whose contract covers the intended use exactly. Each of the
following is **NOT ADMITTED** and needs a new profile:
- adding a command, service, argument or field to a signed profile;
- adding a new F3 identity or adapter under a signed profile (for example under
  `core.governed_configuration_operation`, signed as "existing governed
  adapters", `P:376-397`, identities closed at `R:35-65`);
- issuing a new version of a signed profile.

A changed profile also changes its fingerprint. That would un-admit the live
path that uses it until it is re-signed. Registry data cannot add a URL, tool,
provider, argument or code (`docs/CORE_RELEASE_REGISTRY.md`).

**How the citations were collected.** Three read-only research passes collected
them; nothing was taken from memory. These key citations were re-checked
directly:
- label create (`components/config/label_registry.py:70-82`);
- device update and its silent label filtering (`components/config/device_registry.py:150-183`);
- area update (`components/config/area_registry.py:109-122`);
- REST config delete (`components/config/view.py:141-159`);
- the 069B command vocabulary (`E/governance/entity_availability_exposure.py:96-118`);
- the approval-notification route (§7.2).

### 1.1 Signed seq-12 capabilities (recomputed)

| Capability | Fingerprint | Signed |
|---|---|---|
| core.basic_rest_read | `sha256:3c76c2789952f16340ec51c83122e43cceb1de9581b4dfdf15821d9fc8282cc8` | yes |
| core.basic_websocket_read | `sha256:e1c15f0d838c44f40c2bcf7ae9df408c9cd186cea7874ab7976945be405294cc` | yes |
| core.state_service_discovery | `sha256:f6647dfe70a6b477b97c7866d7b2f91f4ba00452547c9d16a8b8298a147c7225` | yes |
| core.non_device_registry_read | `sha256:4e29009423e7f2438a6b5e0a9cccfcc6add46de73fc05a1edd9fdde9be639615` | yes |
| core.direct_device_registry_read | `sha256:df4afeeea54fa4c59dbe5f8f5b63e63fab516ae69808d7d6ba4d0821b6818ee9` | yes |
| core.delegated_device_effective_area | `sha256:b1c2352fe0e87b59a4f9451113d650ac474a56d9135f715d17d2ca31d25645ad` | yes |
| core.direct_entity_state_read | `sha256:a42cec05d830e66ce830d07e413b8875dfcfd8d985ba2111b94419ef847cf9d1` | yes |
| core.automation_configuration_read | `sha256:ec4344e7dc0a0f4b1a84a2dfbbeae1b412432cf787bcdf41d58ea21a6edb45ad` | yes |
| core.automation_trace_read | `sha256:7f45584511fb1069561ba4ef7a43c4cd6e9a5f41cb9b2f2aac5bdadcd6511c9c` | yes |
| core.dashboard_configuration_read | `sha256:134fa8fa48bdd085a491d2f311ee69c48f53c844a651c334974899e8b79f6424` | yes |
| core.template_semantics | `sha256:0f6d682f1b4d02be622173bc56699ef6de208b5c45b52454cd6fc7a5a083cbe7` | yes |
| core.configuration_validation | `sha256:f998f871e47ded521c5ff713fd738b48c813b0363385237b19c472a1ab540b9a` | yes |
| core.dependency_helper_planning | `sha256:4e981b75c8bf81ef24c3fa123827786ca57762db0d2e09108e499a5e8312efe9` | yes |
| core.typed_helper_operation | `sha256:7ae55a050860e86089756ef8d9104977c630e7c6e593bee2dbd46c6bd2535936` | yes |
| core.f3_mutation_verification | `sha256:274c9beef6b4f6137715b7ab60a25cfcb93fbd1d1b2737ad776004dd3e47c873` | yes |
| core.governed_configuration_operation | `sha256:d517fa73878f8dafea857b5306db750304d3c65522481898800cf86fb091f441` | yes |
| core.governance_observability | `sha256:b4fa34c413a21ea643de830de00477c7c4953efb43916a1d83fa1e3cbd9eb3e2` | yes |
| core.typed_fan_operation | `sha256:25b3b6fdd25ca9c5ba8a7811d850f801fc7d3deefadd4a6bafbf1e9f6a6c042b` | yes |
| core.typed_power_operation | `sha256:9242b795a2d5ed3deeb3b8ea46fba13fc299c14fb274e3b29ed97a30b607001a` | yes |
| core.integration_inspection_metadata_read | `sha256:d10cb88811c65b4b45bed1941f1b8f920ac3eb217d05285f117d5ac23f09c36a` | yes |
| core.automation_baseline_metadata_read | `sha256:42ea1b1731aa0d73e01717aabfa99639c29af2755d8d341c39b4789256a3c540` | yes |
| core.entity_registry_metadata_operation | `sha256:cc09630efbc0e1e6d0a587696406f6502c62fe730a767bc0a8cfc96a9988f767` | yes |
| core.entity_registry_availability_operation | `sha256:065f825817cbdf78a367967a0344ca3dc506c4253c5086482a1da41a5dd3e055` | **no** (069B, compiled inactive) |
| core.entity_assistant_exposure_operation | `sha256:5517d5ef0c249eb45b728ab910a247949a28b0a5972f80f2e78312b464c64303` | **no** (069B, compiled inactive) |

Compiled `SUPPORTED_CORE_RELEASES` covers only 2026.7.2 to 2026.9.1. For
2026.10.x, capabilities are admitted **only** through the signed registry. So
every new profile below stays inactive until a signed sequence references it.

## 2. Per-feature coverage table

| Feature | Core commands needed (write ▲ / read ▽) | Coverage under seq 12 | New profile(s) | Non-Core prerequisites | Effort | Risk |
|---|---|---|---|---|---|---|
| **069B** enable/disable + exposure | ▲ `config/entity_registry/update` (`disabled_by`); ▲ `homeassistant/expose_entity`; ▽ entity_registry get/list, `config_entries/get_single`, `homeassistant/expose_new_entities/get` | NOT ADMITTED (both profiles unsigned) | 2, already compiled (§3.1) | none | S (sign + evidence) | Medium: disable can silence safety signals (design §6); material gap §3.1 |
| **165** labels | ▲ `config/label_registry/create`, ▲ `/update`; ▲ `config/device_registry/update` (labels); ▲ `config/area_registry/update` (labels); ▽ label/area/device registry list | Entity label assignment and all list reads ADMITTED. Create, update, device and area assignment NOT ADMITTED | 1 (labels only) to 3 (with devices and areas) (§3.2) | none | M | Medium: Core silently drops unknown label ids; the `labels` write replaces the whole set |
| **155** delete automation/script | ▲ REST `DELETE /api/config/{automation\|script}/config/{id}`; ▽ GET (404 absence), entity registry | Recreate (recovery) ADMITTED via existing create identities; delete NOT ADMITTED | 1 (§3.3) | none | M | High: destructive. Retires the legacy fail-closed `delete_automation` |
| **071** scenes (recommended) or groups | ▲ REST `POST /api/config/scene/config/{id}`; ▽ GET scene config | NOT ADMITTED. Delegated `ha_config_get_scene` is read-routed only | 1 for scenes (§3.4); groups L | none | M (scenes) / L (groups) | Medium: the post-write hook reloads the whole scene domain |
| **145** helper runtime actions | ▲ `call_service` input_number/input_select/input_text/input_datetime/input_button/timer | NOT ADMITTED (typed helper is input_boolean on/off only) | 1 per selected family (§3.5) | none | M per family | Medium: silent no-op semantics; physical effects via automations |
| **054** integration admin (hacs-govee) | ▲ config entry reload/disable, `update.install`, HACS `hacs/*`; ▽ `config_entries/get` and `manifest/get` for a non-alarmo domain | NOT ADMITTED; HACS commands are not Core source | several write profiles + an adapter pinned to HACS source (§3.6) | HACS source pinning; Core restart blocked like 151 | L | High: third-party code execution |
| **106** dashboard metadata + sidebar | ▲ `lovelace/dashboards/update` (title, icon, show_in_sidebar, require_admin); optional ▲ `frontend/update_panel` | Reads ADMITTED (`dashboard_configuration_read`); metadata write NOT ADMITTED | 1 (+1 optional panel override) (§3.7) | provider route decision: ha-mcp metadata contract vs Engineering-native | M | Medium: ha-mcp upserts (implicit create); `require_admin` hides dashboards |
| **134** security/permission reads | ▽ `config/auth/list`, `person/list`, `assist_pipeline/pipeline/list`, `homeassistant/expose_entity/list`, `http/config` | Only `get_config` ADMITTED | 1 read-only (§3.8) | privacy review first (register Next Action) | M | Medium: personal data; secret-bearing commands must stay forbidden |
| **181 PR2** (+214, +193) | none (outcome, persistence, labels) | n/a | none (§3.9) | owner authorization of PR2 protected files | L (PR2) / S (riders) | Medium: persisted-format compatibility |
| **109** multi-operation | none by itself; each step needs its own admitted identity | n/a | none (§3.9) | a concrete household use case | M | Medium: partial failure without rollback |
| **149** completion signaling | none; plan state is Engineering-local | n/a | none (§3.9) | client capability proof; possibly stateful MCP sessions | S-M | Low-Medium; see §7.2 |
| **151** add-on restart | current path: ha-mcp `ha_manage_addon` (identity already mapped); alternative ▲ `call_service hassio.app_restart` | Identity mapped to signed governed op, but ha-mcp lifecycle is **held** for ≥ 8.2.0; Core `hassio.*` NOT ADMITTED | none (current route) / 1 (Core route) (§3.10) | ha-mcp lifecycle re-admission (ha-mcp registry) | M-L | High: write path; owner-deferred |
| **195** input_boolean fast path | ▲ `call_service input_boolean.turn_on/turn_off` (same as today) | Same command, adapter and F3 identity as signed `typed_helper_operation`. Whether "without a plan" falls inside `provider_boundary: existing_governed_helper_adapter` is **an owner interpretation** | none (G1 accepted) / 1 (if not) (§3.11) | OD-A..OD-J on PR #299 | S / M | High: new reachability from connector authority |
| **218 option C** journald | none (local read of a read-only bind mount) | n/a | none (§3.12) | add-on option `journald: true` (protected `config.yaml`), a journal reader in the image (protected `Dockerfile`), exact HAOS verification | M | Medium: the whole host journal is exposed, so it needs an allow-list and redaction |

## 3. Per-feature detail

### 3.1 HAMCP-069B: enable/disable and voice-assistant exposure

PR #297 is **merged** (merge `b8e6c36`, head `f204e16`, shipped inactive as
`2.4.1-beta.31`). The register row (HAMCP-069) still describes it as a draft.

| Command | R/W | Core | Runtime | Coverage |
|---|---|---|---|---|
| `config/entity_registry/get` | R | `components/config/entity_registry.py:98-124` | `E/governance/entity_availability_exposure.py:489` | named in both unsigned 069B profiles |
| `config/entity_registry/update` with `disabled_by` ∈ {user, null} | W | `entity_registry.py:154-305` (`disabled_by` :179-186; device-disabled refusal :247-258) | 069B adapter | **NOT ADMITTED**: `core.entity_registry_availability_operation` (`P:505`, `065f8258…e055`) is unsigned |
| `homeassistant/expose_entity` (one assistant, one entity) | W | `components/homeassistant/exposed_entities.py:396-415` → `:144-168` | 069B adapter | **NOT ADMITTED**: `core.entity_assistant_exposure_operation` (`P:527`, `5517d5ef…4303`) is unsigned |
| `config_entries/get_single` | R | `components/config/config_entries.py:507-527` | `entity_availability_exposure.py:507` | named in the unsigned availability profile |
| `config/entity_registry/list` | R | `entity_registry.py:39` | `entity_availability_exposure.py:100`, `:542` | named by signed `non_device_registry_read` (`P:184`), **not** by the 069B profiles |
| `homeassistant/expose_new_entities/get` | R | `exposed_entities.py:446-460` | `entity_availability_exposure.py:102`, `:567` | **named in no profile** |

- **New profiles:** the two compiled profiles, unchanged, unless Josh accepts the
  material fix in OD-5.
- **Material gap, found here.** The 069B gateway's closed vocabulary
  (`entity_availability_exposure.py:96-108`) sends two reads that neither 069B
  profile names in its `required_checks`:
  - `config/entity_registry/list`, which is covered elsewhere by
    `non_device_registry_read`;
  - `homeassistant/expose_new_entities/get`, which is covered nowhere.

  This is not a runtime bypass: the commands are closed in code, and the tool
  is refused until a signed sequence exists. But it is a precision gap in the
  contract that will be signed. Adding them now is free, because the profiles
  are unsigned. It is a code change, so it would be its own small scoped PR,
  and it changes both fingerprints. **Josh decides (OD-5).**
- **Evidence needed:**
  - a 069B container lane (none exists yet) proving enable, disable, expose and
    unexpose on a disposable Core in each slot, with readback and cleanup;
  - receipts from the main full-tier CI run.
- **Effort and risk:** S beyond the shared evidence work. Live canaries are
  deferred until after the signing (`docs/V2_4_1_BETA31_ACCEPTANCE.md:185-188`).

### 3.2 HAMCP-165: label create/update and device/area assignment

| Command | R/W | Core | Runtime | Coverage |
|---|---|---|---|---|
| `config/label_registry/list` | R | `components/config/label_registry.py:53-57` | `E/governance/entity_metadata.py:487` | ADMITTED: `non_device_registry_read` `P:184-198` (`4e290094…9615`); also a check of the 069A profile `P:480` |
| `config/label_registry/create` {name, color, description, icon} | W | `label_registry.py:70-96` (admin :81) | none | **NOT ADMITTED**. `P:468-471`: label creation "deliberately absent until its adapter exists" |
| `config/label_registry/update` {label_id; name, color, description, icon} | W | `label_registry.py:123-133`; `helpers/label_registry.py:183-224` | none | **NOT ADMITTED** |
| `config/entity_registry/update` (`labels`) | W | `components/config/entity_registry.py:157`, `:176`; unknown ids dropped `:243-245` | `entity_metadata.py:325-338` | **ADMITTED**: `core.entity_registry_metadata_operation` `P:472-499` (`cc09630e…f767`), fields `P:488-491` |
| `config/device_registry/list` | R | `components/config/device_registry.py:77` | `E/dependency/provider.py:1556` | ADMITTED: `direct_device_registry_read` `P:201-216` |
| `config/device_registry/update` (`labels`) | W | `device_registry.py:150-210`. The schema also accepts `area_id`, `disabled_by`, `name_by_user`; unknown label ids are **silently dropped** (`:180-182`); composite device → not allowed (`:186-196`) | none | **NOT ADMITTED** |
| `config/area_registry/list` | R | `components/config/area_registry.py:24` | `entity_metadata.py:505` | ADMITTED: `non_device_registry_read` |
| `config/area_registry/update` (`labels`) | W | `area_registry.py:109-152`. The schema also accepts aliases, floor_id, icon, name, picture and sensor ids; unknown labels **silently dropped** (`:143-145`) | none | **NOT ADMITTED** |

**Label identity facts for the Part 1 design:**
- `label_id` = slugify(name), with `_2`, `_3`… on collision
  (`helpers/normalized_name_base_registry.py:70-77`).
- A duplicate normalized name is refused (`helpers/label_registry.py:146-149`).
- A rename keeps the `label_id`.
- `slugify("")` returns `""` (`util/__init__.py:39-44`), so the design must
  refuse an empty name.

**New profiles:**
- (a) `core.label_registry_operation`
  - Writes: `config/label_registry/create` or `config/label_registry/update`.
  - Reads: `label_registry/list` before and after, with exact readback by `label_id`.
  - Fields: name, color, icon, description, closed.
  - No delete.
- (b) `core.device_registry_label_operation`
  - Write: `config/device_registry/update` with only `{device_id, labels}`.
  - Reads: device list and label list, with exact readback.
  - Refuses composite devices.
- (c) `core.area_registry_label_operation`
  - Write: `config/area_registry/update` with only `{area_id, labels}`.

(b) and (c) may be one profile with two adapters. They must not attach to the
069A profile (§1 rule).

- **Delegated:** `ha_config_set_label`, `ha_set_device`, `ha_set_area_or_floor`
  and `ha_set_entity` are `persistent_write` and not routed.
  `ha_set_device` and `ha_set_entity` are also in `PROHIBITED_UPSTREAM_TOOLS`
  (`E/providers/upstream_dashboard.py:106-118`).
- **Evidence needed:** a container lane per profile per slot, covering:
  - create, rename, duplicate-name refusal;
  - device and area assignment with exact readback;
  - unknown-label refusal before dispatch;
  - cleanup.

### 3.3 HAMCP-155: governed deletion of one automation or script

| Command | R/W | Core | Runtime | Coverage |
|---|---|---|---|---|
| REST `DELETE /api/config/automation/config/{id}` | W | `components/config/view.py:141-159` (a missing key returns **400**, not 404); `components/config/automation.py:23-51` (hook removes the registry entry, fire-and-forget) | none. Legacy `delete_automation` fails closed (`E/tools/compatibility.py:650-656`) | **NOT ADMITTED**: no delete identity at `R:35-65`; `E/f3_configuration/strategies.py:42` supports `create` and `update` only |
| REST `DELETE /api/config/script/config/{id}` | W | `view.py:141-159`; `components/config/script.py:22-48` | none | **NOT ADMITTED** |
| REST `GET /api/config/{automation\|script}/config/{id}` → 404 | R | `view.py:85-96` | `E/governance/resources.py:629` | Route ADMITTED for exact readback (`P:376-397`), but proving absence needs a new check. Reusing the exact-match readback for absence would be a widening |
| `config/entity_registry/get` / `list` (entry removed) | R | `entity_registry.py:100`, `:40` | existing | ADMITTED (`non_device_registry_read`) |
| REST `POST …/config/{id}` (recovery re-create) | W | `view.py:98-139` | `resources.py:996-1044` | ADMITTED via `create_automation_configuration` / `create_script_configuration` (`R:35-65`) |

- **New profile:** `core.configuration_delete_operation`
  - Write: DELETE for `automation` or `script` by id; no body.
  - Reads: exact pre-image (the recovery payload, bound into the plan hash),
    the registry entry, then absence by 404 plus a bounded poll for the
    registry entry.
  - The REST client needs an explicit method binding: `E/clients/rest.py:59-98`
    validates only route templates.
  - Recovery is a separate plan under the already-admitted create identity.
- **Entity restore:** re-creating an automation or script with the same
  unique_id within 30 days restores its registry metadata
  (`helpers/entity_registry.py:94`, `:1601-1631`).
- **Effort and risk:** M; high risk, because deletion is destructive.

### 3.4 HAMCP-071: scenes (recommended) or groups

| Command | R/W | Core | Coverage |
|---|---|---|---|
| REST `GET /api/config/scene/config/{id}` | R | `view.py:85-96`; `components/config/scene.py:42-51` | Not directly. `E/clients/paths.py:51-66` has no scene route. Delegated `ha_config_get_scene` is routed under `basic_websocket_read` (`R:109`), whose checks do not attest this route |
| REST `POST /api/config/scene/config/{id}` | W | `view.py:98-139`; `scene.py:25-89`. The schema allows extra keys (`components/homeassistant/scene.py:87-106`). The hook reloads the **whole scene domain** | **NOT ADMITTED** |
| `scene.turn_on` / `scene.apply`, DELETE | W | `components/scene/services.py:15-23`; `view.py:141-159` | **NOT ADMITTED**; recommend excluding both |
| Group helper config/options flow | W | `components/config/config_entries.py:179-304`; `components/group/config_flow.py:169-182` (12 types), `:363-380` (`hide_members` writes member `hidden_by`) | **NOT ADMITTED**; no exact options readback (`config_entries/get_single` omits options) |

- **Why scenes first:** a scene is one REST view with the same lock, atomic
  write and exact GET readback as the governed automation and script writes.
  Groups are a multi-step flow with side effects on member entities and no exact
  readback.
- **New profile:** `core.scene_configuration_operation`
  - Write: POST scene config.
  - Reads: a new direct scene GET route (pre-image, 404 for create), exact
    readback, and existence of every entity in `entities`.
  - Fields closed in Engineering: id, name, icon, entities, metadata.
  - Activation and delete are excluded.

### 3.5 HAMCP-145: additional helper runtime actions

`core.typed_helper_operation` (`P:336`, `7ae55a05…5936`) is bound to one F3
identity, `set_exact_input_boolean_state` (`R:32-34`), and to the
`direct-ha-exact-input-boolean-v1` provider contract: `input_boolean`
turn_on/turn_off only, through WS `call_service`
(`components/websocket_api/commands.py:266-300`).

None of the following services is admitted:

| Service | Core |
|---|---|
| `input_number.set_value` / `increment` / `decrement` | `components/input_number/__init__.py:157-165` |
| `input_select.select_option` (+ relative moves, `set_options`) | `components/input_select/__init__.py:183-221` |
| `input_text.set_value` (length failures only log a warning, `:257-268`) | `components/input_text/__init__.py:162-164` |
| `input_datetime.set_datetime` | `components/input_datetime/__init__.py:178-193` |
| `input_button.press` | `components/input_button/__init__.py:124` |
| `timer.start` / `pause` / `cancel` / `finish` / `change` | `components/timer/__init__.py:157-169` |

- **New profiles:** one per family, following the typed fan/power pattern
  (`P:415-439`): closed domain and service list, closed arguments, dispatch at
  most once, exact readback, no redispatch. Candidate ids:
  - `core.typed_input_number_value_operation`
  - `core.typed_input_select_option_operation`
  - `core.typed_input_text_value_operation`
  - `core.typed_input_datetime_value_operation`
  - `core.typed_input_button_press_operation`
  - `core.typed_timer_control_operation`
- **Do not widen `typed_helper_operation`.** A new version un-admits today's
  input_boolean path.
- **Evidence gap:** the signed 2026.10.1 evidence lists `unchanged_relied_on`
  only for input_boolean and input_number among these domains.
- **Status:** the register keeps 145 as Backlog, "when a real need appears", so
  it belongs in the batch only for a family Josh names (OD-4).

### 3.6 HAMCP-054: integration administration (LaggAt/hacs-govee)

| Command | R/W | Core | Coverage |
|---|---|---|---|
| `config_entries/get`, `manifest/get` for the Govee domain | R | `components/config/config_entries.py:657-662`; `components/websocket_api/commands.py:699` | ADMITTED only for `alarmo` (`core.integration_inspection_metadata_read`, `P:446-455`); another domain is a widening |
| REST `POST /api/config/config_entries/entry/{id}/reload` | W | `config_entries.py:132-153` | **NOT ADMITTED** |
| `config_entries/disable` | W | `config_entries.py:576-583` | **NOT ADMITTED** |
| `repairs/list_issues` (R), `repairs/ignore_issue` and fix flows (W) | R/W | `components/repairs/websocket_api.py:40-90`, `:136`, `:181` | **NOT ADMITTED** |
| `update.install` | W | `components/update/services.py:97-103` | **NOT ADMITTED**; the install body is HACS code |
| HACS `hacs/*` | R/W | not in Core source | **outside any Core profile**; would need an adapter pinned to exact HACS source (precedent: Alarmo's `alarmo-inspection-v1` adapter) |

- **Assessment:** the register keeps this Deferred, with no reviewed package to
  administer. **Exclude it from seq 13.**

### 3.7 HAMCP-106: dashboard metadata and sidebar

| Command | R/W | Core | Coverage |
|---|---|---|---|
| `lovelace/dashboards/list`, `lovelace/config` | R | `components/lovelace/__init__.py:303-309`; `components/lovelace/websocket.py:130-132` | ADMITTED: `dashboard_configuration_read` (`P:282`, `134fa8fa…6424`) |
| `lovelace/config/save` | W | `websocket.py:149-152` | existing F3 dashboard identity (`R:29-31`), through ha-mcp `ha_config_set_dashboard` (config only) |
| `lovelace/dashboards/update` {title, icon, show_in_sidebar, require_admin} | W | `helpers/collection.py:634`; `components/lovelace/dashboard.py:272-322`; `components/lovelace/const.py:71-89` | **NOT ADMITTED** |
| `lovelace/dashboards/create`, `/delete` | W | `collection.py:611`, `:649` | **NOT ADMITTED**; recommend excluding (delete is a prohibited upstream tool) |
| `get_panels` (R), `frontend/update_panel` (W, system-wide panel override) | R/W | `components/frontend/__init__.py:969-989`, `:1084-1124` | **NOT ADMITTED** |
| `frontend/set_user_data` | W | `components/frontend/storage.py:196-215` | **NOT ADMITTED**, and unsuitable: it is per connection user, so it would change the Engineering service user's sidebar, not Josh's |

- **New profile:** `core.dashboard_metadata_operation`
  - Write: `lovelace/dashboards/update` on an existing storage dashboard by id.
  - Fields: title, icon, show_in_sidebar, require_admin.
  - Excluded: create, delete and url_path changes.
- **Optional profile:** `core.frontend_panel_override_operation`.
- **Provider decision (OD-6):** ha-mcp 8.6.0 `ha_config_set_dashboard` supports
  a metadata-only call, but it upserts: a missing dashboard is created. Either
  route needs this new Core profile, because a new F3 identity cannot be mapped
  onto signed profiles. The two routes are:
  - (a) a new ha-mcp metadata contract that blocks the implicit create;
  - (b) an Engineering-native direct write.

### 3.8 HAMCP-134: security and effective-permission metadata reads

| Command | Core | Admin | Coverage | Notes |
|---|---|---|---|---|
| `config/auth/list` | `components/config/auth.py:22-33`, `:165-187` | yes | **NOT ADMITTED** as a tool read (used internally for the approver check, §7.2) | no secrets; project the fields |
| `auth/current_user` | `components/auth/__init__.py:559-595` | no | ADMITTED (`automation_baseline_metadata_read`, `P:458`) | describes only the Engineering service user |
| `person/list` | `components/person/__init__.py:326-341` | no | **NOT ADMITTED** | personal data |
| `assist_pipeline/pipeline/list` | `components/assist_pipeline/pipeline.py:556-567` | no | **NOT ADMITTED** | |
| `homeassistant/expose_entity/list` | `components/homeassistant/exposed_entities.py:417-442` | yes | **NOT ADMITTED** natively | |
| `http/config` | `components/http/websocket_api.py:52-82` | yes | **NOT ADMITTED** | holds TLS file **paths**; project booleans only |
| `get_config` | `components/websocket_api/commands.py:658-667` | no | ADMITTED (`basic_websocket_read`) | |

- **Must never be read or sent:**
  - `cloud/status`: carries `google_secure_devices_pin` (`components/cloud/http_api.py:1111-1162`).
  - `auth/refresh_tokens`: only the service user's tokens, and includes IPs.
  - `auth/long_lived_access_token` and `auth/sign_path`: both mint credentials.
  - The MFA setup flows.
  - `http/config/configure`: restarts Home Assistant.
- **MFA:** state for other users is not available over WebSocket.
- **New profile:** `core.security_posture_metadata_read` (read-only, projected
  fields only).
- **Status:** the register asks for a feasibility and privacy review first, so
  this is optional for seq 13 (OD-4).

### 3.9 No Core signing needed: 181 PR2 (+214, +193), 109, 149

- **181 PR2.** Design at `docs/HAMCP_181_TRUTHFUL_OUTCOMES_DESIGN.md:1309-1403`.
  It changes outcome, persistence and label classification only. The design's
  non-actions say: "No runtime… registry, schema… change" (`:1531-1535`).
  - It touches protected runtime files (`:1324-1342`), which need Josh's
    authorization.
  - Its release references name "beta.10" (`:1393`, `:1401`) and need
    restaging.
  - **193:** confirmed at `E/f3_dashboard/adapter.py:641-652`; it is a test
    rider.
  - **214:** a plan/task agreement inside PR2.
- **109.** Sequencing is executor-local:
  - ordered `depends_on` (`E/governance/configuration_eligibility.py:333-337`);
  - iteration (`E/f3_runtime/runtime.py:2648`);
  - stop on first failure (`E/governance/service.py:8005-8007`).

  Each step takes its own Core lease (`E/ha_core_readmission/runtime.py:1576-1586`),
  and unknown identities are refused (`core.unsupported_f3_operation`). No new
  Core command. Every operation it sequences must be admitted on its own.
- **149.** Plan and approval state is Engineering-local (`R:284-317`). The MCP
  notification APIs exist in the pinned SDK (`mcp==1.30.0`) but are unused.
  The transport is stateless only while the release registry is disabled
  (`stateless_http=not settings.ha_mcp_release_registry_enabled`,
  `E/mcp_server.py:31-35`). Even with a stateful session, a server push needs
  an open client stream. No Core command; see §7.2 for one adjacent
  observation.

### 3.10 HAMCP-151: add-on restart plans

- **Current route.** The restart goes through ha-mcp `ha_manage_addon`
  restart (`E/providers/operational_lifecycle.py:334-348`). Its identity
  `restart_installed_home_assistant_addon` / `restart_addon` (`R:62`) is mapped
  to signed `governed_configuration_operation` + `f3_mutation_verification`
  (`R:605-612`).
- **Blocker.** It is not the Core registry. ha-mcp lifecycle admission is
  `held` for ha-mcp ≥ 8.2.0, and newer catalogs rename the tool to
  `ha_manage_app` (`docs/HAMCP_218_SUPERVISOR_MANAGER_ROLE.md:495-502`;
  `docs/HA_MCP_8_6_0_COMPATIBILITY.md:60-63`).
- **Widening.** Re-admitting the identity under a renamed tool, or adding a
  Core adapter, is a widening.
- **Alternative route.** WS `call_service hassio.app_restart`
  (`components/hassio/services.py:229-303`) would need a new Core profile.
- **Recommendation:** owner-deferred; **exclude it from seq 13.**

### 3.11 HAMCP-195: typed input_boolean fast path

The design (draft PR #299, head `12670c8`) reuses `HelperStateGateway.set_state`
(WS `call_service input_boolean.turn_on|turn_off`) and the **same** F3 identity.
At the Core-contract level that is the signed `typed_helper_operation`:
- same command;
- same adapter;
- no new argument.

The open point is the profile's `provider_boundary:
existing_governed_helper_adapter`. Today it is reached only after an approved
plan. If Josh reads the boundary as covering a gated call without a plan, no
signing is needed. If he reads it as "plan-approved only", seq 13 needs a new
`core.gated_input_boolean_operation` (OD-3). This audit does not decide that
interpretation.

Dependency analysis reads (`script/config`, `search/related`) run under the
existing `dependency_helper_planning` route. See §7.1.

### 3.12 HAMCP-218 option C: journald

Option C sets the add-on option `journald: true`. Supervisor then bind-mounts
the host journal read-only, and a typed, allow-listed, redacted local reader
reads it. Sources: `docs/HAMCP_218_SUPERVISOR_MANAGER_ROLE.md:363-374`,
`:519-521`, `:536-541`.

- **Supervisor API role:** none. `hassio_role` stays `homeassistant`
  (`hass_mcp_engineering_beta/config.yaml:11-13`).
- **Core command:** none.
- **Prerequisites, all non-Core:**
  - authorization for protected `config.yaml` and `Dockerfile`;
  - a journal parser (the `python:3.12-slim` base has no `journalctl`), which
    needs supply-chain review;
  - verification of journal contents at the exact deployed HAOS version;
  - an identifier allow-list and redaction review.
- **Design point:** `CORE_INDEPENDENT_STATIC_TOOLS` would need a basis such as
  the existing `mounted_read_only_source` rather than `supervisor_api_only`.

## 4. Deduplicated list of NEW profiles

| # | Proposed capability id | Single write (or read set) | For | In recommended batch |
|---|---|---|---|---|
| 1 | `core.entity_registry_availability_operation` (compiled, `065f8258…e055`) | `config/entity_registry/update` (`disabled_by`) | 069B | yes |
| 2 | `core.entity_assistant_exposure_operation` (compiled, `5517d5ef…4303`) | `homeassistant/expose_entity` | 069B | yes |
| 3 | `core.label_registry_operation` | `config/label_registry/create` \| `update` | 165 | yes |
| 4 | `core.device_registry_label_operation` | `config/device_registry/update` {device_id, labels} | 165 | if OD-2 includes devices |
| 5 | `core.area_registry_label_operation` | `config/area_registry/update` {area_id, labels} | 165 | if OD-2 includes areas |
| 6 | `core.configuration_delete_operation` | REST DELETE automation/script config | 155 | yes |
| 7 | `core.dashboard_metadata_operation` | `lovelace/dashboards/update` | 106 | yes |
| 8 | `core.scene_configuration_operation` | REST POST scene config | 071 | yes (if scenes) |
| 9 | `core.gated_input_boolean_operation` | `call_service input_boolean.turn_on/off` without a plan | 195 | only if OD-3 says the boundary excludes it |
| 10 | `core.typed_<helper family>_operation` (up to 6) | one family's services | 145 | only for families named in OD-4 |
| 11 | `core.security_posture_metadata_read` | read set (§3.8) | 134 | optional (OD-4) |
| 12 | `core.frontend_panel_override_operation` | `frontend/update_panel` | 106 | optional |
| - | Core `hassio.app_restart`, config-entry/repairs/update writes, HACS adapter | - | 151, 054 | no (deferred) |

Profiles 1, 2 and 3 are the minimum. The recommended batch is 1-3, 6, 7 and 8,
plus 4 and 5 if Josh wants devices and areas: 6 to 8 profiles.

## 5. What can ship now with no signing

These do not wait for seq 13. Each still needs its own scope and protected-path
authorization.

- **181 PR2 + 214 + 193.** Needs owner authorization of the PR2 protected
  runtime files.
- **149.** The client-compatibility fixture first, as the register says.
- **109.** Once a concrete household multi-operation task is chosen. Its
  operations must already be admitted.
- **218 option C.** Needs authorization of `config.yaml` and `Dockerfile`, plus
  exact-HAOS verification.
- **195 under G1.** Only if Josh rules that the signed `typed_helper_operation`
  boundary covers the gated fast path (OD-3), and records OD-A..OD-J.
- **165 entity label assignment.** Assigning existing labels to entities already
  works under seq 12 via `create_entity_metadata_plan`.
- **Inactive builds** of every batch feature. They ship refused, with reason
  `pending_signed_registry_admission`, as 069B did in beta.31.

## 6. Proposed seq-13 batch

### 6.1 Batch content

| Feature | Profiles | Evidence each needs |
|---|---|---|
| 069B | 1, 2 (already compiled; OD-5 may revise the material first) | container lane: disable/enable + expose/unexpose of a disposable entity, exact readback and cleanup, per slot |
| 165 | 3 (+4, 5) | container lane: create, rename, duplicate refusal, device/area assignment with exact readback, unknown-label refusal before dispatch, cleanup |
| 155 | 6 | container lane: delete a disposable automation and script, 404 + registry absence, recovery re-create, missing-key (400) classification |
| 106 | 7 | container lane: metadata update on a disposable storage dashboard, exact readback, no implicit create |
| 071 | 8 | container lane: create/update a disposable scene, exact readback, entity-existence refusal, no activation |

### 6.2 What one signing looks like

The prep tool allows one changed release entry per positive sequence step. So
"one signing" is one owner session that signs consecutive sequences. There are
two timing options; both are a single session.

- **T-a. With the Core 2026.11 admission** (069B OD-2c plan, recommended).
  - seq 13 = `add` of a 2026.11.x entry listing the 22 existing references plus
    all new ones;
  - seq 14 = `extend-capabilities` of 2026.10.1 (which becomes rollback) with
    the same new references;
  - optionally seq 15 = `revoke` of 2026.10.0 once it leaves the window,
    following the 182 precedent for 2026.9.4.
  - This first needs:
    - a reviewed 2026.11 lane binding in `scripts/core_lane_bindings.py`;
    - a lane provenance fixture;
    - a support-window rotation.
- **T-b. Earlier, on 10.x only.**
  - seq 13 = extend 2026.10.1;
  - seq 14 = extend 2026.10.0.
  - Core 2026.11 would then be added later with all capabilities in its `add`
    entry, which is a separate signing session.

Prep-tool limits that shape the batch:
- **Extension evidence** is closed. `added_capabilities` must equal the newly
  appended references, and several are allowed. `review_artifacts_sha256` holds
  **1 to 32** hashes (`scripts/prepare_core_release_registry.py:110-127`).
  HAMCP-182 used 3 per capability (entity receipt, cleanup receipt, artifact
  zip). With separate lanes, about 10 profiles reach the cap. A consolidated
  lane (one zip and one cleanup per slot, one receipt per profile) keeps a 6-9
  profile batch well inside it.
- **Every referenced profile must already be compiled** with
  `auto_eligible=True` in the signer's checkout (`require_known_contracts`,
  `:88-98`).
- **The receipts bind** `engineering_source = {commit, runtime_tree}` of the
  deployed build (`docs/evidence/hamcp182/core-registry-hamcp182.md:68-72`,
  `:501`).

### 6.3 Recommended order

1. **Josh decides** OD-1..OD-8 (§8).
2. **Material fixes before the freeze** (small, separate PR): the 069B
   required-checks precision fix if OD-5 says so.
3. **Build each batch feature inactive.** Use separate PRs in the owner's
   feature order: 165, 155, 106, then 071-scenes.
   - Each new profile is compiled and refused with
     `pending_signed_registry_admission`.
   - End-to-end tests use a test-registry fixture, as 069B did.
   - No live canary until signed.
4. **One consolidated container-evidence lane PR.** It wires all batch profiles
   into the main full-tier CI. **This touches protected `.github/workflows/ci.yml`**
   (precedent: `e362b9b`, HAMCP-179), so Josh must authorize it.
5. **Freeze and release.** One Engineering beta containing every batch profile.
   Josh deploys it.
   - After the freeze, any profile change alters its fingerprint and invalidates
     the evidence.
   - A feature that misses the freeze slips to a later sequence.
6. **Evidence run.**
   - One main full-tier CI run (both slots) at the deployed head.
   - Offline receipt verification.
   - A Part 0 rehearsal with an ephemeral key on the deployed and rollback builds
     (HAMCP-182 §5 pattern).
   - Candidate preparation with the shipped prep tool.
7. **Owner signing** in one session, with `OWNER-SIGNING-RUNBOOK.md`
   (seq 13 [+14, +15]).
8. **Publication PR**, then each feature's live canary.

### 6.4 Rough effort

| Phase | Effort |
|---|---|
| Inactive build per feature | M each (069B done) |
| Consolidated lane | M-L |
| Evidence + signing PR | M (precedent HAMCP-182) |

Each extra profile adds one lane scenario set and one receipt.

## 7. Observations outside the batch (disclosed, not changed)

### 7.1 Reads named in no signed `required_checks` (pre-existing)

These reads are closed in code and run under admitted tool routes, but no
signed profile names the command itself:

- `script/config` and `search/related`, in dependency analysis
  (`E/dependency/provider.py:982-1007`), under the `dependency_helper_planning`
  route;
- `config/auth/list`, used for the approver identity check;
- delegated `ha_get_entity_exposure`, routed under `delegated_device_effective_area`
  (`R:123`).

They are worth a precision decision at the same freeze (OD-8). Leaving them is
the existing reviewed posture.

### 7.2 Approval mobile notifications

The approval notification worker sends REST
`POST /api/services/notify/mobile_app_*`:
- `E/governance/approval_notifications.py:31-33`;
- route template `E/clients/paths.py:62`.

It runs "outside this request and outside the gateway Core lease" (`R:311-317`).
`E/clients/rest.py:81-89` applies the Core dispatch gate only when request
telemetry exists. No signed profile names this service call.

This is a pre-existing, reviewed path, and this audit does not call it a defect.
But **if HAMCP-149 extends push notifications** to completion, rejection or
expiry, that widens a Core write that no profile names. Scope it explicitly.
Whether telemetry is absent in the worker context is **unverified**.

## 8. Open decisions for Josh

- **OD-1. Batch membership.** Recommended: 069B, 165, 155, 106, 071-scenes.
  Add or remove features.
- **OD-2. HAMCP-165 devices/areas scope.** The options:
  - (a) labels plus entity assignment only, which is 1 new profile; entity
    assignment works today;
  - (b) add devices and areas as profiles 4 and 5 in seq 13 (recommended if
    devices and areas are needed in the next few weeks: they cost only lane
    scenarios once the batch exists);
  - (c) devices and areas later.

  Also confirm the HAMCP-165 build option (i) "build inactive until seq 13".
- **OD-3. HAMCP-195 boundary.** Does signed `typed_helper_operation`
  (`provider_boundary: existing_governed_helper_adapter`) cover a gated call
  without a plan?
  - Yes: no signing is needed.
  - No: profile 9 joins the batch.
- **OD-4. Optional profiles.** Name any HAMCP-145 helper families, HAMCP-134
  security reads (after its privacy review) or the 106 panel override to
  include. The default is none.
- **OD-5. 069B material fix.** Add `config/entity_registry/list` and
  `homeassistant/expose_new_entities/get` to the 069B required checks before
  the freeze? This changes both unsigned fingerprints.
- **OD-6. HAMCP-106 provider route.** Either:
  - (a) an ha-mcp metadata-only contract that blocks the implicit create; or
  - (b) an Engineering-native direct write.

  Both need profile 7.
- **OD-7. Timing.** Either:
  - T-a, with the Core 2026.11 admission (recommended; matches 069B OD-2c); or
  - T-b, earlier on 10.x, followed by a second session for 2026.11.

  Also: the freeze date, and authorization for the consolidated lane's
  `ci.yml` change.
- **OD-8. Pre-existing unnamed reads (§7.1).** Name them in new profile versions
  at this freeze, or keep the current posture.

## 9. Non-actions

- No code, profile, registry, version, workflow, support-window or stable-v1
  change.
- No signing input, candidate or key material was created or handled.
- No live Home Assistant or deployed MCP access. No secrets.
- The Core archives were treated as untrusted data and read with grep, sed and
  cat only.
- Not verified:
  - the seq-12 Ed25519 signature itself (it is not needed for coverage);
  - how ha-mcp performs a restart internally;
  - the hacs-govee integration domain;
  - frontend sidebar key names;
  - whether `http/config` stores any secret value;
  - the HAOS journal contents;
  - the exact key layout of 2026.11 `add` evidence for new capabilities.
