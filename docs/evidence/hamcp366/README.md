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
| Receipt source commit | `69111ecbfb85e68d6b827bf4dcf349c7398abc05` (clean committed checkout) |
| Receipt SHA-256 | `47db4e9bb47e93cf0d7f96170ad4052bbd716b49db1453ed2c97763164ce01a9` (22,189 bytes) |
| Result | `PASS`, `cleanup.clean: true` |
| Run | user unit `hamcp366-lane-run3`, scriptbox (amd64), 2026-11-11 00:14:57 to 00:19:19 Central (4 min 23 s) |
| Core | 2026.11.0, `ghcr.io/home-assistant/home-assistant:2026.11.0@sha256:c6c1ca9931036bf3a2c4cc088d8db25dfea3b33935a5ecc83c40349462e2f69e` |
| ha-mcp child | 8.5.0, `ghcr.io/homeassistant-ai/ha-mcp:8.5.0@sha256:e1538bcdadb13a5467bbb8258fba3262a05518585e18115742da638adec7057c` |
| Predecessor journal | sequence 17, SHA-256 `3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d`, 32 Core references |

Commits after the receipt's source commit on this branch add documentation only
(this directory). No lane script, acceptance module, fixture or test changes
after it.

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
| Lane setup REST | same Core constant | one state read shape, two config-flow posts, one entry reload | `core_url_not_pinned`, `core_rest_request_not_listed` |
| Stub control | `http://127.0.0.1:18299/_lane/control` | the section 9.2 write faults and one drift per admitted attribute | `stub_url_not_pinned`, `stub_request_not_listed` |
| ha-mcp child | `http://127.0.0.1:18298/beta23-real-ha-mcp` | (product provider) | `upstream_url_not_pinned` |

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
  same `id` and `created_at` (`registry_id_changed: false`,
  `created_at_changed: false`, `modified_at_changed: true`). Both paths still
  refused with zero sends: the pending change with
  `device_configuration_state_changed`, the undo with
  `device_configuration_undo_post_state_unproven`. The refusal code for the
  undo therefore differs from the design's R1 row. The lane requires the
  identity-change code when Core issues a new `id` or `created_at`, and the
  unproven-post-state code when Core restores them, and records which occurred.
- The dumps yield three more entities of admitted kinds than the lane
  exercises. They are listed under `observations.admitted_kind_entities_not_exercised`.
  The lane's closed command list names one entity per kind, so a product
  dispatch to any other entity is refused by the lane before Core.
- The stub has no drift mode and applies a stalled write afterwards; drift lives
  in the lane entry, and the stalled case is asserted as held, then verified.
- The lane is a local runner with its own evidence group
  (`ci/core-evidence-groups.json`). It is not wired into a workflow; workflow
  files are outside this task.

## Earlier runs on this branch (not evidence)

- `hamcp366-lane-run1` at `fd5b72ac`: WITHHELD after 19 s (Matter config flow
  `cannot_connect`, the name-resolution observation above); cleanup clean.
- Five short diagnostic runs established that cause. They are diagnosis only.
- `hamcp366-lane-run2` at `1655359c`: PASS, cleanup clean. Superseded by this
  receipt because one unreachable clause was removed from the lane script and
  tests were added afterwards.

## Not done here

No registry candidate, signature, signing or workflow dispatch, publication,
release, tag, version change, deployment, Ready action or merge. No protected
runtime, workflow or release path is changed. Mutation demonstration of the
guards by a separate implementer is pending.
