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
| Receipt source commit | `cb1856cf570cfc2da23e3d5be90bbc70c1678f9a` (clean committed checkout) |
| Receipt SHA-256 | `7e7c85d1e00cacaf26e8ba25151dd3ffcd2a45f920bdfea3c94c2774bf68a97f` (35,799 bytes) |
| Result | `PASS`, `cleanup.clean: true` |
| Run | user unit `hamcp366-lane-run5`, scriptbox (amd64), 2026-11-11 08:04:00 to 08:08:22 Central (4 min 22 s) |
| Core | 2026.11.0, `ghcr.io/home-assistant/home-assistant:2026.11.0@sha256:c6c1ca9931036bf3a2c4cc088d8db25dfea3b33935a5ecc83c40349462e2f69e` |
| ha-mcp child | 8.5.0, `ghcr.io/homeassistant-ai/ha-mcp:8.5.0@sha256:e1538bcdadb13a5467bbb8258fba3262a05518585e18115742da638adec7057c` |
| Predecessor journal | sequence 17, SHA-256 `3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d`, 32 Core references |

The commit that adds this receipt changes documentation only (this directory
and one sentence in the design). No lane script, acceptance module, fixture,
test or runtime file changes after the receipt's source commit. A later change
to the lane script makes this receipt stale by the same rule; the lane then
re-runs.

The source commit includes the one runtime change on this branch: the text of
the undo `rule` field (see "Runtime text change" below). The profile
fingerprint is the same with and without it.

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
- One observed undo that is not in the refusal count: the undo of a config
  switch change after a `matter` entry reload alone (see "Re-registration
  observation").

Totals in the receipt: 14 operations, 15 service sends, 16 stub writes, 0 lane
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

## Observations and the design text

- Re-registration (design sections 5.11.4 and 8: R1, and the N12 variant F2).
  Removing the admitted select and reloading the `matter` entry re-registered
  it with the same registry `id`, `created_at` and `unique_id`. The design
  states this as the restored case. The lane requires it
  (`restored_reregistration`,
  `scripts/device_configuration_contract_acceptance.py:558-567`) and asserts
  one exact undo refusal, `device_configuration_undo_post_state_unproven`
  (`REREGISTRATION_UNDO_REFUSAL`, `:126`, asserted at `:1029-1032`), with zero
  sends. A changed registry `id`, `created_at` or `unique_id` on that path
  fails the lane; no other refusal code is accepted in its place. The pending
  change that the same re-registration catches is refused with
  `device_configuration_state_changed`.
- A replaced device (design R2) is not exercised in the lane. It would need a
  stub control action that removes a node and commissions a replacement with
  the same serial under a new node id. The lane's closed stub list
  (`STUB_POSTS`) has no such action and none was added. R2 is covered at unit
  level; see "Unit-level coverage" below.
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
decision is made of. The per-field tables are an observation: the lane asserts
nothing about their content. What the lane does assert on this path is above:
the restored registry `id`, `created_at` and `unique_id`, a rewritten
`modified_at`, and the one exact undo refusal.

How the fingerprint is computed: the lane calls the runtime's own code, not a
copy. `composite()` in `scripts/device_configuration_contract_acceptance.py`
(`:493`) runs `DeviceConfigurationAdapter._read`
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
for the select only and is not widened (product-owner ruling of 2026-11-11,
recorded with its reason in design section 5.11.4): a remove of the config
switch or the number was not sent, and the `reload_only` rows are what the lane
has for those two kinds. Their restored case is covered at unit level.

### `remove_then_reload`: `select.eve_energy_plug_power_on_behavior`

Fingerprint `b9acee4a508a5b2bbeac518dcce3f553848822c7413c98427be5cd6e7ef75d3b`
before, `74de8adc10fd7f18b4686ed2117d8ec527089b26b3efadfea7421500381df2ee`
after: **changed**. The "before" fingerprint equals the one the forward task
recorded (`before_is_recorded_post_fingerprint: true`). `identity()` is
unchanged (`identity_restored: true`). The undo refused with
`device_configuration_undo_post_state_unproven`, raised before any task, with
zero sends and zero stub writes (`refusals.undo_after_reregistration`).

| Level | Field | Before | After | Changed |
|---|---|---|---|---|
| Core registry answer | `id` | `1ad3e36015c7ed385084ab7eb4e205bb` | same | no |
| Core registry answer | `created_at` | `1794405854.881606` | same | no |
| Core registry answer | `modified_at` | `1794405854.88174` | `1794405871.632502` | yes |
| Core registry answer | `device_id` | `c5fe5574eb555bf58e25a0b0f104682f` | same | no |
| Core registry answer | `entity_id` | `select.eve_energy_plug_power_on_behavior` | same | no |
| Core registry answer | `unique_id` | `123456789ABCDEF0-000000000000003D-MatterNodeDevice-1-MatterStartUpOnOff-6-16387` | same | no |
| Core registry answer | `platform`, `config_entry_id` | `matter`, `01M75ERVK792ZVZVX3YTXGEV01` | same | no |
| composite observation | `registry.modified_at` | `1794405854.88174` | `1794405871.632502` | yes |
| composite observation | `context_id` | `01M75ESB1P7P9KXH5C4GZB406K` | `01M75ESC0GHMQ1Y32T7AC6BWFC` | yes |
| composite observation | `last_changed` | `2026-11-11T14:04:30.648098+00:00` | `2026-11-11T14:04:31.632606+00:00` | yes |
| composite observation | `last_updated` | `2026-11-11T14:04:30.648098+00:00` | `2026-11-11T14:04:31.632606+00:00` | yes |
| composite observation | `state` | `on` | same | no |
| composite observation | `assumed_state` | `false` | same | no |
| composite observation | `attributes.options` | sha256 `f157c574849d11b9...` | same | no |
| composite observation | `entity_id` | `select.eve_energy_plug_power_on_behavior` | same | no |
| composite observation | `registry.id`, `registry.created_at`, `registry.device_id`, `registry.unique_id`, `registry.platform`, `registry.config_entry_id` | as the registry rows above | same | no |
| composite observation | `registry.entity_category`, `registry.translation_key` | `config`, `startup_on_off` | same | no |
| composite observation | `registry.disabled_by`, `registry.options_number`, `registry.original_device_class` | `null` | same | no |
| composite observation | `device.id`, `device.created_at`, `device.primary_config_entry` | `c5fe5574eb555bf58e25a0b0f104682f`, `1794405854.876173`, `01M75ERVK792ZVZVX3YTXGEV01` | same | no |
| composite observation | `device.identifiers`, `device.disabled_by` | sha256 `f968b0b055b7e324...`, `null` | same | no |

Of the four changed fingerprint fields, only `registry.modified_at` comes from
the re-registration itself. The other three also change on a reload alone, as
the next tables show.

### `reload_only`: the config switch and the number

Neither entity was re-registered: every registry-level field, `modified_at`
included, is unchanged. The fingerprint still changed, through the state's
`context_id`, `last_changed` and `last_updated` only.

| Target | Level | Field | Before | After | Changed |
|---|---|---|---|---|---|
| `switch.eve_energy_20ecn4101_child_lock_top` | fingerprint | | `c20f7c19941e96d1a639a711b058f98d99a565fdb022b46dbb76c32de6883389` | `a6ee2d9e37ce1352faf44a24c41b6be39f7434d8b54948858b690d21012e9883` | yes |
| | Core registry answer | `id` | `37578c2e407733a9b520e399f29e7920` | same | no |
| | Core registry answer | `created_at` | `1794405854.873494` | same | no |
| | Core registry answer | `modified_at` | `1794405854.873638` | same | no |
| | Core registry answer | `device_id` | `389e014c5f2686254fc1e7b74d79b550` | same | no |
| | Core registry answer | `entity_id`, `unique_id`, `platform`, `config_entry_id` | (in the receipt) | same | no |
| | composite observation | `context_id` | `01M75ESACSNKE9R1824YY1SK72` | `01M75ESC0927DDKYVC15GP5MZQ` | yes |
| | composite observation | `last_changed`, `last_updated` | `2026-11-11T14:04:29.978696+00:00` | `2026-11-11T14:04:31.625209+00:00` | yes |
| | composite observation | every other field (`state` `off`, `attributes` empty, all `registry.*`, all `device.*`) | (in the receipt) | same | no |
| `number.aqara_door_and_window_sensor_p2_sensitivity` | fingerprint | | `85aacc016bf2f6128fed2e3f4cd1e163b6ce209e4934bec976370ef2eea7a11d` | `724db58012bd17731eea78600aa994b615d0618b8ae1b8c6129894eb4bda1b57` | yes |
| | Core registry answer | `id` | `966ae8cff0cff751787673f977a5bf89` | same | no |
| | Core registry answer | `created_at` | `1794405854.890566` | same | no |
| | Core registry answer | `modified_at` | `1794405854.890712` | same | no |
| | Core registry answer | `device_id` | `ca9096c92913c0eddfa78824a5b501d5` | same | no |
| | Core registry answer | `entity_id`, `unique_id`, `platform`, `config_entry_id` | (in the receipt) | same | no |
| | composite observation | `context_id` | `01M75ES1E17S9S6PT753KHMZ30` | `01M75ESC0PBVFSZW4VSQEC9J3P` | yes |
| | composite observation | `last_changed`, `last_updated` | `2026-11-11T14:04:20.803502+00:00` | `2026-11-11T14:04:31.638937+00:00` | yes |
| | composite observation | every other field (`state` `3`, `attributes.min/max/step/mode`, all `registry.*`, all `device.*`) | (in the receipt) | same | no |

Every field of every target, with both values, is in the receipt; lists and
objects appear there as a SHA-256 of the runtime's canonical form.

### Undo after a reload alone (observation)

The lane changes the config switch (`operations.switch_before_reload`,
`succeeded_verified`) before the reload, and after it asks for the undo of
that change. The request is one the lane's closed lists already carry; nothing
was added to them. The lane records the result and asserts no particular code
(`observed_undo`, `scripts/device_configuration_contract_acceptance.py:707`).
Observed (`observations.reregistration_fingerprint.reload_only.undo_after_reload`):
`device_configuration_undo_post_state_unproven`, raised before any task, zero
service sends and zero stub writes. The fingerprint before the reload equals
the one the forward task recorded.

So on Core 2026.11.0 a `matter` entry reload by itself ended undo eligibility
for a verified change. Recovery is a compensating change (design section
5.11.3). The undo of the number after a reload alone was not attempted.

Not established by this run: what a remove and re-registration does to the
config switch or the number in a running Core (the lane's closed list has no
such command; the unit tests below cover the runtime's decision for that
shape), a Core restart path, a replaced device, and any path on a Core version
other than 2026.11.0.

## Unit-level coverage of the cases the lane does not run

In `tests/test_hamcp297_device_configuration.py`, against the runtime's own
undo decision with an offline fake Core:

| Test | Shape | Expected |
|---|---|---|
| `test_r1_restored_config_switch_refuses_post_state_unproven_with_zero_sends` | the config switch comes back with the same registry `id`, `created_at` and every other recorded identity field, and a changed fingerprint. Three shapes: registry `modified_at` only; state timestamps and context id only (a reload alone); both (removed, then reloaded) | `device_configuration_undo_post_state_unproven`; one write in total (the forward change); restoring the recorded observation lets the same undo verify |
| `test_r1_restored_sensitivity_number_refuses_post_state_unproven_with_zero_sends` | the same three shapes for the sensitivity number | the same |
| `test_r2_replaced_device_is_caught_by_registry_id_and_unique_id_alone` | a replaced device: new registry `id` and `unique_id` (together, and each alone), with the device `id`, the entity_id and the value unchanged; and the realistic shape, where the device keeps its `id` and gets new identifiers | `device_configuration_undo_identity_changed`; one write in total; restoring the recorded entity lets the same undo verify |

## Runtime text change

One protected runtime file changes on this branch, under a product-owner
authorization of 2026-11-11 for that file and that string only:
`hass_mcp_engineering_beta/ha_mcp_engineering/device_configuration/service.py`
(3 lines added, 1 removed). The `rule` text of an available undo was

> only while the post-change observation is still exact

and is now

> only while the post-change observation is still exact; a later change or an integration reload ends eligibility, and a Home Assistant restart is expected to as well; after that, make a compensating change

It is a plain result field. No undo or refusal behaviour, reachability, input
or output schema, tool description or profile material changes:
`contract_fingerprint` is
`sha256:74a03a640f60c9adaf8508a0a7fcbca7745f29b3d09e1dc9a611ac361475293c`
before and after. `test_undo_rule_text_is_exact_in_the_receipt_and_through_the_real_tool`
pins the text.

## Coverage by operation shape

The node dumps create three entities of admitted kinds that the lane does not
dispatch to (`observations.admitted_kind_entities_not_exercised`; each is one
for which the lane's prediction of the runtime's refusal is "none",
`scripts/device_configuration_contract_acceptance.py:935-950`). The runtime
allowlist has one triple per domain, so the domain fixes the triple. Coverage
is by operation shape:

| Entity not dispatched to | Operation shape | Lane operations that exercise the shape (script line; receipt key under `operations`) | Exercised |
|---|---|---|---|
| `select.eve_energy_20ecn4101_power_on_behavior_bottom` | select option change, `(select, matter, startup_on_off)` | on `select.eve_energy_plug_power_on_behavior`: `select_forward` (L850), `select_undo` (L851), `select_delayed` (L876), `select_before_reregistration` (L1006) | yes |
| `select.eve_energy_20ecn4101_power_on_behavior_top` | select option change, `(select, matter, startup_on_off)` | the same four | yes |
| `switch.eve_energy_plug_child_lock` | config-switch toggle, `(switch, matter, child_lock)` | on `switch.eve_energy_20ecn4101_child_lock_top`: `switch_forward_on` and `switch_forward_off` (L857, L858), `switch_undo` (L860), `switch_before_drift` (L984), `switch_before_reload` (L999) | yes |

All nine receipt entries are `succeeded_verified`. The lane never sends to the
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
- `hamcp366-lane-run4` at `9d8a0014`: PASS, cleanup clean (receipt SHA-256
  `38a1d66ebdce8c5617fa244a1ee06530e36b3e01ef92e086b794afa90c5847ec`). Superseded
  by this receipt because the acceptance module changed afterwards: on the
  re-registration path it accepted either undo refusal, according to whether
  Core restored the registry id. It now asserts the one exact code, and the
  undo after a reload alone was added as an observation.

## Not done here

No registry candidate, signature, signing or workflow dispatch, publication,
release, tag, version change, deployment, Ready action or merge. No protected
workflow or release path is changed, and no protected runtime path other than
the one text above. Mutation demonstration by a separate implementer of the
guards added after `db91bb35` is pending.
