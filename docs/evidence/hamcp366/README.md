# HAMCP-366: device-configuration signing lane, review receipt

`device_configuration_entity.json` is one passing local run of the
`device_configuration_entity` lane for
`core.device_configuration_entity_operation` (design
[`HAMCP_297_DEVICE_CONFIGURATION_ENTITIES_DESIGN.md`](../../HAMCP_297_DEVICE_CONFIGURATION_ENTITIES_DESIGN.md)
section 9.2).

**This receipt is review evidence, not the signing receipt.** Every included
lane re-runs once on the signing commit, and the signing receipt will bind that
commit. Nothing here signs a registry entry, builds a candidate, or admits the
capability in production. The receipt is the lane's canonical output, copied
byte for byte; it is never edited by hand.

| Field | Value |
|---|---|
| Receipt source commit | `9d8a0014310b31e5894346176951bed55396ac6a` (clean committed checkout) |
| Receipt SHA-256 | `38a1d66ebdce8c5617fa244a1ee06530e36b3e01ef92e086b794afa90c5847ec` (34,760 bytes) |
| Result | `PASS`, `cleanup.clean: true` |
| Run | user unit `hamcp366-lane-run4`, scriptbox (amd64), 2026-11-11 07:07:44 to 07:12:24 Central (4 min 40 s), after one 10-minute wait in the lane's own resource admission |
| Core | 2026.11.0, `ghcr.io/home-assistant/home-assistant:2026.11.0@sha256:c6c1ca9931036bf3a2c4cc088d8db25dfea3b33935a5ecc83c40349462e2f69e` |
| ha-mcp child | 8.5.0, `ghcr.io/homeassistant-ai/ha-mcp:8.5.0@sha256:e1538bcdadb13a5467bbb8258fba3262a05518585e18115742da638adec7057c` |
| Predecessor journal | sequence 17, SHA-256 `3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d`, 32 Core references |

The commit that adds this receipt changes documentation only (this directory
and one note in the design). No lane script, acceptance module, fixture or test
changes after the receipt's source commit. A later change to the lane script
makes this receipt stale by the same rule; the lane then re-runs.

## Signing inputs the run asserts

The lane refuses before any container starts unless the compiled profile equals
the reference recorded in `scripts/core_device_configuration_local.py`, and
withholds the receipt unless the profile the running build loaded equals it too:

| Key | Value |
|---|---|
| `capability_id` | `core.device_configuration_entity_operation` |
| `profile_id` | `core_device_configuration_entity_operation_v1` |
| `profile_version` | `1` |
| `adapter_id` | `compiled-core-device-configuration-entity-operation-v1` |
| `contract_fingerprint` | `sha256:74a03a640f60c9adaf8508a0a7fcbca7745f29b3d09e1dc9a611ac361475293c` |

The receipt records both as `profile` (loaded) and `signing_profile`
(reference); they are equal. A profile change after this receipt makes the lane
refuse until the reference is deliberately updated, and makes this receipt
evidence for a different fingerprint.

## What the run executed

Real Core 2026.11.0 runs the real `matter` integration against a lane-owned,
test-only Matter Server stub that serves three node dumps taken from Core's own
test fixtures at commit `74cf5a49c63ed8d4d87717844bf14f79c28c9cea`. The stub
(`tests/fixtures/hamcp297_matter_server_stub/`, unmodified) and the dumps are
checked against their committed provenance and against the Core source archive
(SHA-256 `85f3c42c9f77aa45e01faff4471007beadf5cb50c4324f496a239139eec70ff1`)
before any container starts. The receipt's `stub` block carries the stub, lane
entry and dump hashes.

The signed three-triple build is exercised through the real Engineering
service, gateway and adapter, with no test seam:

- Authority: the journal's 32-reference entry refuses with
  `device_configuration_core_authority_unavailable`; the disposable 33-reference
  entry (signed by a fresh in-memory test key, never a publication key) admits.
- Each admitted kind, on one named entity, has a `succeeded_verified` forward
  change and a `succeeded_verified` undo with the recorded stub write and the
  readback fingerprint: `select.eve_energy_plug_power_on_behavior`
  (`startup_on_off`), `switch.eve_energy_20ecn4101_child_lock_top`
  (`child_lock`), `number.aqara_door_and_window_sensor_p2_sensitivity`
  (`sensitivity_level`).
- Stub write faults: a 5 s delay verifies; an errored write is held as
  `changed_unverified` and ends `manual_review_required`; a stalled write is
  held as `changed_unverified` and later ends `succeeded_verified`; a
  stored-value mismatch is held and ends `verification_failed` with
  `manual_review_required`.
- 32 refusals, each with zero service sends and zero stub writes: nine
  refusal-fixture entities (kinds outside the three triples, non-config and
  diagnostic categories, disabled, no device, unavailable, unit-converted
  number), four `matter` entities outside the admitted kinds, number range and
  step, an option not offered, parse-time field and action refusals, lock
  conflicts in both directions between `control_power` and this capability,
  state drift before preflight and before dispatch, and undo refusals after
  drift, after re-registration and for an unverified task.

Totals in the receipt: 13 operations, 14 service sends, 15 stub writes, 0 lane
client refusals, 0 fallbacks, `production_authority: false`.

## Guards

Every network client the acceptance module
(`scripts/device_configuration_contract_acceptance.py`) opens compares the
configured URL with a loopback constant and refuses when they differ, dials the
constant, sends only exact shapes on a closed list, and refuses with a fixed
reason code before any session exists:

| Client | Dials | Closed list | Refusals |
|---|---|---|---|
| Product gateway WebSocket | `ws://127.0.0.1:18297/api/websocket` | two registry reads, one service payload per named lane entity | `core_url_not_pinned`, `core_command_not_listed` |
| Product gateway REST | `http://127.0.0.1:18297/api` | `GET /api/states/<entity>` | `core_url_not_pinned`, `core_rest_request_not_listed` |
| Lane setup WebSocket | same Core constant | registry and config-entry reads, one fixture registry update, one remove of the admitted select (the re-registration case) | `core_url_not_pinned`, `core_command_not_listed` |
| Lane setup REST (raw session) | `http://127.0.0.1:18297` plus a listed path | exactly three config-entry routes: flow start, flow continue, entry reload. A state path is refused | `core_url_not_pinned`, `core_rest_request_not_listed` |
| Lane state readback | `http://127.0.0.1:18297/api` through the runtime REST client | `GET` of one entity state on the bound runtime route (`rest_path("/states/{}", ...)`) | `core_url_not_pinned`, `core_rest_request_not_listed` |
| Stub control | `http://127.0.0.1:18299/_lane/control` | the section 9.2 write faults and one drift per admitted attribute | `stub_url_not_pinned`, `stub_request_not_listed` |
| ha-mcp child | `http://127.0.0.1:18298/beta23-real-ha-mcp` | (product provider) | `upstream_url_not_pinned` |

The raw session's one computed URL (`CORE_URL + path`) is the single
`HARNESS_EXEMPTIONS` entry this lane has in
`tests/test_hamcp200_path_safety.py`: the three config-entry routes have no
bound runtime route, and the sender refuses any other method, path or body
before a session exists (`LaneRawRouteTests` in
`tests/test_core_device_configuration_local.py`).

The lane clients set `trust_env=False` and do not follow redirects. The stub's
lane entry (`tests/fixtures/hamcp366_device_configuration_lane/stub_entry.py`)
replaces the stub's open control route with one closed route and refuses any
other body with `lane_control_request_not_listed`.

## Network posture

The three containers join one dedicated, lane-owned Docker bridge. The bridge
is not internal: this is the posture already recorded for local lanes in
[`../hamcp315/README.md`](../hamcp315/README.md) (HAMCP-326), so container
egress is allowed and `--pull never` still requires cached images. Host ports
18297 (Core), 18298 (ha-mcp child) and 18299 (stub control) bind `127.0.0.1`
only. The stub's Matter WebSocket port is not published to the host; Core
reaches it on the lane network. The runner checks network ownership, driver and
mode, then each container's actual port bindings and sole network membership
immediately after it starts and before its first readiness request. Before the
stub is used or any product call is made it also checks each running
container's image identity and memory limit against the pinned values.

Observed on this host: on the non-internal bridge the running Core did not
resolve Docker's embedded-DNS names (resolver timeout; the cause was not
established). The lane therefore starts the stub first and gives Core the stub's
name as a hosts entry. `stub_address()` accepts only one IPv4 address, assigned
by the lane's own network to the lane's own stub, inside that network's single
private subnet, and refuses otherwise with `stub_address_not_on_lane_network`.
The receipt records the entry as `stub_host_entry`.

No household device, live Home Assistant, deployed MCP endpoint, production
credential or secret is used. The onboarding token of the disposable Core
exists only in memory and in the temporary upstream environment file.

## Execution

Use a clean committed checkout, the task Python environment, cached exact
images, and a **new** run directory under the worktree's ignored `.artifacts/`.
Run it as a heavy job under the host's admission rule, never directly:

```bash
.artifacts/venv/bin/python scripts/core_device_configuration_local.py \
  --mode local --pull never \
  --journal .artifacts/inputs/seq17-journal.json \
  --source-archive .artifacts/inputs/core-2026.11.0.tar.gz \
  --run-dir "$PWD/.artifacts/device-configuration-run-1"
```

Memory limits: Core `2g`, ha-mcp child `768m`, stub `256m`, fixture staging and
cleanup `128m` (override only with the four explicit arguments). The runner
also applies the shared local resource admission
(`scripts/core_local_resources.py`) and records the admitted sample as
`host_admission`. The lane is
local-only: a CI context, an optimised interpreter (`-O`), a dirty checkout, an
existing container or network of the lane's names, an occupied port, a missing
cached image, or an input whose bytes differ from the pinned hash refuses before
the lane starts.

A group failure or a cleanup failure is **WITHHELD**. Do not rerun a substitute
or count offline tests as Core evidence. Failure diagnostics follow the
HAMCP-322 rules described in `../hamcp315/README.md`: a failed receipt names
the stage and check only, and raw logs stay in private local files outside the
repository.

## Observations that differ from the design text

- Re-registration (design section 8, N12 variant F2 and R1): the design expects
  the registry `id` and `created_at` to change and the undo to refuse with
  `device_configuration_undo_identity_changed`. In this run, removing the
  admitted select and reloading the `matter` entry re-registered it with the
  same `id` and `created_at`. Both paths still refused with zero sends: the
  pending change with `device_configuration_state_changed`, the undo with
  `device_configuration_undo_post_state_unproven`. The refusal code for the
  undo therefore differs from the design's R1 row. On this receipt's source
  commit the lane still requires the identity-change code when Core issues a
  new `id` or `created_at`, and the unproven-post-state code when Core restores
  them, and records which occurred. Restating R1 and asserting one exact code
  is a pending follow-up; see the next section.
- The dumps yield three more entities of admitted kinds than the lane
  exercises. They are listed under `observations.admitted_kind_entities_not_exercised`.
  The lane's closed command list names one entity per kind, so a product
  dispatch to any other entity is refused by the lane before Core. See
  "Coverage by operation shape" below.
- The stub has no drift mode and applies a stalled write afterwards; drift lives
  in the lane entry, and the stalled case is asserted as held, then verified.
- The lane is a local runner with its own evidence group
  (`ci/core-evidence-groups.json`). It is not wired into
  `scripts/core_evidence_lanes.py` (`run_specialized`) or a workflow; that
  wiring is a separate 2.7 row (HAMCP-368). The design's section 9.2 carries
  the same note.

## Re-registration observation (Core 2026.11.0)

The receipt records, under `observations.reregistration_fingerprint`, what a
remove and same-`unique_id` re-registration did to the observation the undo
decision is made of. It is an observation: the lane asserts nothing about its
content.

How the fingerprint is computed: the lane calls the runtime's own code, not a
copy. `composite()` in `scripts/device_configuration_contract_acceptance.py`
runs `DeviceConfigurationAdapter._read`
(`hass_mcp_engineering_beta/ha_mcp_engineering/device_configuration/adapter.py:148-160`)
under the adapter's Core-authority read (`.../device_configuration/service.py:80-103`).
The fingerprint is `digest()` (`.../fan/contracts.py:41-43`) of that
observation (`.../device_configuration/contracts.py:451-457`). The undo
compares `identity()` first (`adapter.py:169-170`,
`device_configuration_undo_identity_changed`) and the fingerprint second
(`adapter.py:171-172`, `device_configuration_undo_post_state_unproven`).
"Core registry answer" rows are Core's own reply to
`config/entity_registry/get`, read by the lane's setup WebSocket.

The `matter` config entry was kept and stayed `loaded`
(`matter_config_entry_kept: true`). Two paths ran, both between the pending
change's locked preflight and `once()`:

| Path | Steps | Targets | Re-registers the entity |
|---|---|---|---|
| `remove_then_reload` | WebSocket `config/entity_registry/remove`, then `POST /api/config/config_entries/entry/{entry_id}/reload` | `select.eve_energy_plug_power_on_behavior` | yes |
| `reload_only` | `POST /api/config/config_entries/entry/{entry_id}/reload` alone (the same reload; no remove is sent for these) | `switch.eve_energy_20ecn4101_child_lock_top`, `number.aqara_door_and_window_sensor_p2_sensitivity` | no |

A Core restart was not tried. The lane's closed WebSocket list names the remove
for the select only, so a remove of the config switch or the number was not
sent; the `reload_only` rows are what the lane has for those two kinds.

### `remove_then_reload`: `select.eve_energy_plug_power_on_behavior`

Fingerprint `8f097432704e448d1abc399d46af5b1f4d91d6905798c1eea96e05a6d9ba6372`
before, `168b329b4d2598f4233d0a991f6ea98f012ff50932f9f2e1261674e39d6504ef`
after: **changed**. The "before" fingerprint equals the one the forward task
recorded (`before_is_recorded_post_fingerprint: true`). `identity()` is
unchanged (`identity_restored: true`). The undo refused with
`device_configuration_undo_post_state_unproven` and zero sends.

| Level | Field | Before | After | Changed |
|---|---|---|---|---|
| Core registry answer | `id` | `ef9b63b1a00b192f8f02d0dbe26b3918` | same | no |
| Core registry answer | `created_at` | `1794402486.613693` | same | no |
| Core registry answer | `modified_at` | `1794402486.613887` | `1794402507.663438` | yes |
| Core registry answer | `device_id` | `5dfc409851d655934d8133d7c0d788d5` | same | no |
| Core registry answer | `entity_id` | `select.eve_energy_plug_power_on_behavior` | same | no |
| Core registry answer | `unique_id` | `123456789ABCDEF0-000000000000003D-MatterNodeDevice-1-MatterStartUpOnOff-6-16387` | same | no |
| Core registry answer | `platform`, `config_entry_id` | `matter`, `01M75BJ28GSV1JYVGRYFT2QPHS` | same | no |
| composite observation | `registry.modified_at` | `1794402486.613887` | `1794402507.663438` | yes |
| composite observation | `context_id` | `01M75BJNGZQND3MY161PTR3AWT` | `01M75BJPWFW2G3706JEH7SXBZ5` | yes |
| composite observation | `last_changed` | `2026-11-11T13:08:26.273122+00:00` | `2026-11-11T13:08:27.663615+00:00` | yes |
| composite observation | `last_updated` | `2026-11-11T13:08:26.273122+00:00` | `2026-11-11T13:08:27.663615+00:00` | yes |
| composite observation | `state` | `on` | same | no |
| composite observation | `assumed_state` | `false` | same | no |
| composite observation | `attributes.options` | sha256 `f157c574849d11b9...` | same | no |
| composite observation | `entity_id` | `select.eve_energy_plug_power_on_behavior` | same | no |
| composite observation | `registry.id`, `registry.created_at`, `registry.device_id`, `registry.unique_id`, `registry.platform`, `registry.config_entry_id` | as the registry rows above | same | no |
| composite observation | `registry.entity_category`, `registry.translation_key` | `config`, `startup_on_off` | same | no |
| composite observation | `registry.disabled_by`, `registry.options_number`, `registry.original_device_class` | `null` | same | no |
| composite observation | `device.id`, `device.created_at`, `device.primary_config_entry` | `5dfc409851d655934d8133d7c0d788d5`, `1794402486.607803`, `01M75BJ28GSV1JYVGRYFT2QPHS` | same | no |
| composite observation | `device.identifiers`, `device.disabled_by` | sha256 `f968b0b055b7e324...`, `null` | same | no |

Of the four changed fingerprint fields, only `registry.modified_at` comes from
the re-registration itself. The other three also change on a reload alone, as
the next tables show.

### `reload_only`: the config switch and the number

Neither entity was re-registered: every registry-level field, `modified_at`
included, is unchanged. The fingerprint still changed, through the state's
`context_id`, `last_changed` and `last_updated` only. No undo was attempted
against these two after the reload.

| Target | Level | Field | Before | After | Changed |
|---|---|---|---|---|---|
| `switch.eve_energy_20ecn4101_child_lock_top` | fingerprint | | `7c2057615a0d5d770715195e867c4fe351994697cd834ab2edf2a25cd9136039` | `5e86c7a85d7b606d0726b0752ebc0804ce3860c4c4a6ae1ff356b2f1a3fcfe34` | yes |
| | Core registry answer | `id` | `5d674a99a27b1407827b3fc5f65cfca4` | same | no |
| | Core registry answer | `created_at` | `1794402486.604929` | same | no |
| | Core registry answer | `modified_at` | `1794402486.605126` | same | no |
| | Core registry answer | `device_id` | `1422f276b7fbf45ba612e4240bb30bdd` | same | no |
| | Core registry answer | `entity_id`, `unique_id`, `platform`, `config_entry_id` | (in the receipt) | same | no |
| | composite observation | `context_id` | `01M75BJM40ESD339ZK43FFFNHE` | `01M75BJPW7ZK7FYQV3PTGT1W2Z` | yes |
| | composite observation | `last_changed`, `last_updated` | `2026-11-11T13:08:25.520444+00:00` | `2026-11-11T13:08:27.655283+00:00` | yes |
| | composite observation | every other field (`state` `on`, `attributes` empty, all `registry.*`, all `device.*`) | (in the receipt) | same | no |
| `number.aqara_door_and_window_sensor_p2_sensitivity` | fingerprint | | `705eb9de957d128094336d202174dd5c62315706da0afb32bc6f281c90b8923e` | `cf26743fc27f6a9f0a37414dd36b2a1eea0998711cba6bf0885ddb867c13bfd9` | yes |
| | Core registry answer | `id` | `be1c0b7caaa4499cae155816e7e60436` | same | no |
| | Core registry answer | `created_at` | `1794402486.630994` | same | no |
| | Core registry answer | `modified_at` | `1794402486.63125` | same | no |
| | Core registry answer | `device_id` | `3f30c1d4eadc2a255372946482b19e93` | same | no |
| | Core registry answer | `entity_id`, `unique_id`, `platform`, `config_entry_id` | (in the receipt) | same | no |
| | composite observation | `context_id` | `01M75BJAA1YTEWBPMRNCEXZKDD` | `01M75BJPWR6RXHA1M9MDV8ST1D` | yes |
| | composite observation | `last_changed`, `last_updated` | `2026-11-11T13:08:14.787800+00:00` | `2026-11-11T13:08:27.672469+00:00` | yes |
| | composite observation | every other field (`state` `3`, `attributes.min/max/step/mode`, all `registry.*`, all `device.*`) | (in the receipt) | same | no |

Every field of every target, with both values, is in the receipt; lists and
objects appear there as a SHA-256 of the runtime's canonical form.

Not established by this run: what a remove and re-registration does to the
config switch or the number (it needs a WebSocket command the lane's closed
list does not have), a Core restart path, and any path on a Core version other
than 2026.11.0.

## Coverage by operation shape

The node dumps create three entities of admitted kinds that the lane does not
dispatch to (`observations.admitted_kind_entities_not_exercised`; each is one
for which the lane's prediction of the runtime's refusal is "none",
`scripts/device_configuration_contract_acceptance.py:902-911`). The runtime
allowlist has one triple per domain, so the domain fixes the triple. Coverage
is by operation shape:

| Entity not dispatched to | Operation shape | Lane operations that exercise the shape (script line; receipt key under `operations`) | Exercised |
|---|---|---|---|
| `select.eve_energy_20ecn4101_power_on_behavior_bottom` | select option change, `(select, matter, startup_on_off)` | on `select.eve_energy_plug_power_on_behavior`: `select_forward` (L811), `select_undo` (L812), `select_delayed` (L837), `select_before_reregistration` (L962) | yes |
| `select.eve_energy_20ecn4101_power_on_behavior_top` | select option change, `(select, matter, startup_on_off)` | the same four | yes |
| `switch.eve_energy_plug_child_lock` | config-switch toggle, `(switch, matter, child_lock)` | on `switch.eve_energy_20ecn4101_child_lock_top`: `switch_forward_on` and `switch_forward_off` (L818, L820), `switch_undo` (L821), `switch_before_drift` (L945) | yes |

All eight receipt entries are `succeeded_verified`. The lane never sends to the
three entities in the first column: its closed command list names one entity
per kind.

## Earlier runs on this branch (not evidence)

- `hamcp366-lane-run1` at `fd5b72ac`: WITHHELD after 19 s (Matter config flow
  `cannot_connect`, the name-resolution observation above); cleanup clean.
- Five short diagnostic runs established that cause. They are diagnosis only.
- `hamcp366-lane-run2` at `1655359c`: PASS, cleanup clean. Superseded because
  one unreachable clause was removed from the lane script and tests were added
  afterwards.
- `hamcp366-lane-run3` at `69111ecb`: PASS, cleanup clean (receipt SHA-256
  `47db4e9bb47e93cf0d7f96170ad4052bbd716b49db1453ed2c97763164ce01a9`). Superseded
  by this receipt because the acceptance module changed afterwards: the raw
  REST session was closed to three config-entry routes, the state read moved to
  the bound runtime route, and the re-registration observation was added.

## Not done here

No registry candidate, signature, signing or workflow dispatch, publication,
release, tag, version change, deployment, Ready action or merge. No protected
runtime, workflow or release path is changed. Mutation demonstration by a
separate implementer of the guards added after `db91bb35` is pending.
