# HAMCP-443 (A): receipt comparisons for the local Core lanes

HAMCP-443 (A) changes the device lane's upstream URL check (D7) and factors
the seven local Core harnesses onto a shared lane host. Neither change may
alter what a lane proves. The proof is a re-run of each lane whose new receipt
is compared field by field with the lane's committed receipt
(design `~/handoffs/architect/designs/2026-11-20-hamcp-443-evidence-lanes-v3.md`,
review finding F7: byte-identical receipts are unattainable).

## What is here

| File | Purpose |
|---|---|
| `receipt-exclusions.json` | The declaration, committed before any lane runs: per lane, the committed receipt and its SHA-256, the leaves excluded from value equality, and the value changes the task expects. |
| `README.md` | This file. |
| `lane/device_configuration_entity.json` | The device lane's receipt on ha-mcp 9.2.0 (D7), copied byte for byte. |
| `lane/device_configuration_entity.comparison.json` | `compare_core_receipts.py` report for that receipt against hamcp366: equal. |
| `lane/helper_value.json` | The helper_value lane's first committed receipt, on ha-mcp 9.2.0 (D7), copied byte for byte. |
| `host/<lane>.json` | Each lane's receipt from the shared lane host (D2/D5), copied byte for byte. |
| `host/<lane>.comparison.json` | `compare_core_receipts.py` report for that receipt against the lane's committed receipt. |

New receipts and comparison reports are added here as the lanes run. Only
receipt JSON and comparison reports are committed. Run directories, container
logs and host files never are: a container log can hold the disposable Core's
admin token.

## D7 runs on ha-mcp 9.2.0

Both ran from `c94d66ce` (clean checkout, declaration already committed), on
scriptbox (amd64), as `systemd-run --user` units with `MemoryMax=3G`,
`OOMPolicy=continue` and `RuntimeMaxSec=3600`, one at a time, with
`--mode local --pull never` and the staged Core 2026.11.0 archive
(`85f3c42c…0ff1`). Child image
`ghcr.io/homeassistant-ai/ha-mcp:9.2.0@sha256:9de5bf6c4216142fdffe862900abe72dac8dfe9621dd0b2b050d132372b676ba`.

| Lane | Unit | Central time | Admission | Journal | Result | Receipt SHA-256 |
|---|---|---|---|---|---|---|
| `device_configuration_entity` | `hamcp443-devcfg-lane-c94d66ce` | 2026-11-20 18:22:20 to 18:26:44 | load1 0.16, 11.90 GB available, 0 other heavy jobs, 0 containers | seq 17 `3f79fce7…6a5d` | `PASS`, clean | `8a82f592…0ac2` |
| `helper_value` | `hamcp443-helper-lane-c94d66ce` | 2026-11-20 18:27:25 to 18:28:47 | load1 0.37, 11.92 GB available, 0 other heavy jobs, 0 containers | seq 16 `8df3665c…b82c` | `PASS`, clean | `ce3a227c…d814` |

Both journals came from the registry branch's history and match the harness
pins: seq 16 from registry commit `cf415e29`, seq 17 from `e486028e`.

The D7 report's `declaration_sha256` (`af4be4ce…`) names the declaration as
committed in `c94d66ce`, which held only the device lane. Step 3 added the
other lanes afterwards, so the current file hashes differently.

Device comparison: `equal: true`. Both D7 changes were applied. No leaf
outside the declaration differed. Of the 189 excluded leaves, these differed:
`run_identity` 3, `host_admission` 3 (`load1`, `mem_available_bytes`, `utc`),
`core_run_ids` 54, `engineering_task_ids` 20, `core_timestamps` 42 and
`derived_from_run_values` 36. One `timing` leaf differed:
`operations/number_stored_value_mismatch/seconds` went from 9.2 to 9.3. Every
`state_reads_after_send` and `final.observation_attempts` value was equal, and
so was `stub_host_entry.address`.

## How the comparison works

`scripts/compare_core_receipts.py --lane <lane> --observed <receipt>` reads the
declaration, the committed receipt and the new receipt, and nothing else. It
refuses (exit 2) when the committed receipt's SHA-256 differs from the
declaration, when the lane's harness is missing, when a JSON input has a
duplicate key or a non-finite number, or when the declaration is malformed.

Everywhere in the receipt it compares dict key sets, list lengths and leaf
types (`true` is not `1`, `1` is not `1.0`). Then:

- A leaf that an exclusion pattern matches skips value equality only. Its
  differences are listed in the report under its class.
- A declared expected change must hold exactly the declared committed value
  in the committed receipt and exactly the declared observed value in the new
  one. A stale baseline, an unchanged value or any other value fails.
- Every other leaf must be equal.

Exit 0 means equal, exit 1 means at least one difference. Under the brief, any
difference outside the declaration is a STOP.

A pattern is a list of segments. A string matches that dict key, a list of
strings matches any one of them, `*` matches exactly one segment (a key or a
list index) and `**` matches zero or more. A literal never matches a list
index. Every pattern must match at least one leaf of the committed receipt, so
a pattern cannot silently go stale. An expected change may not sit under an
exclusion.

## Device lane (`device_configuration_entity`)

Committed receipt: `docs/evidence/hamcp366/device_configuration_entity.json`
(SHA-256 `710a6aa172ded0dd168eef8b9784456081db0f3f888985ba17a742bc1071f5dc`,
the R8 run on `d84d8d9e`). Of its leaves, 189 are excluded:

| Class | Leaves | Why the value changes every run |
|---|---|---|
| `run_identity` | 3 | `source_commit`, `started_utc`, `ended_utc`. |
| `host_admission` | 5 | The host's load, memory, container and heavy-job counts when the lane was admitted. |
| `docker_assigned` | 1 | `stub_host_entry.address`: Docker picks the lane network's subnet. `stub_host_entry.name` stays compared. |
| `core_run_ids` | 54 | Context, config entry, device and registry ids the disposable Core mints. |
| `engineering_task_ids` | 20 | Task ids Engineering mints, and `undo_of`, which names one. |
| `core_timestamps` | 42 | Registry `created_at`/`modified_at` and state `last_changed`/`last_updated`. |
| `derived_from_run_values` | 36 | Fingerprints and evidence hashes over observations that contain the ids and times above. |
| `timing` | 28 | `seconds`, `state_reads_after_send` and the held tasks' `final.observation_attempts`, which count polls before a wall-clock deadline. |

The first three classes are the brief's examples (source commit, timestamps,
run ids). `timing` goes beyond them; the PR lists every excluded value that
differed. The top-level `operations/*/observation_attempts` stay compared, as
do every status, result, refusal code and send count, the `changed` booleans
and `changed_fields`, the attribute and identifier SHA-256s, the Core and stub
image digests, the profile fingerprint, the journal and manifest hashes and
`authority_transition`. `tests/test_compare_core_receipts.py` pins a sample of
these as compared.

Expected changes (D7): the upstream image and its `config_digest` move from
ha-mcp 8.5.0 to the 9.2.0 child that `REVIEWED_CORE_BINDINGS["2026.11.0"]`
names since HAMCP-437 I2b (`ci/support-window.yaml`).

## Shared lane host lanes (step 3)

Each lane is re-run once on the shared lane host and compared with its
committed receipt. Every lane excludes `run_identity` (`source_commit`,
`started_utc`, `ended_utc`) and `host_admission` (the five admission
readings). Anything a lane adds is listed below; every other leaf is
compared, including all results, refusal codes, counts, send and dispatch
counts, image digests, profile fingerprints and the journal, manifest and
source-hash SHA-256s.

| Lane | Committed receipt (SHA-256) | Leaves | Excluded | Extra classes | Expected changes |
|---|---|---|---|---|---|
| `helper_value` | `hamcp443/lane/helper_value.json` (`ce3a227c…d814`), the D7 9.2.0 run on `c94d66ce` | 123 | 8 | none | none |
| `marketplace_read` | `hamcp290/lane/marketplace_read.json` (`a61da63c…1037`) | 100 | 10 | `public_store_snapshot` 2 | none |
| `security_metadata` | `hamcp134/lane/security_metadata.json` (`5e9e0439…706a`) | 104 | 8 | none | none |
| `scene_create_delete` | `hamcp364/lane/scene_create_delete.json` (`03fef43d…7b8e`) | 134 | 21 | `run_identity` +1, `lane_suffix` 8, `derived_from_run_values` 4 | `engineering_source.runtime_tree` |
| `created_helper_delete` | `hamcp364/lane/created_helper_delete.json` (`c54b261f…e1bd`) | 181 | 27 | `run_identity` +1, `lane_suffix` 12, `derived_from_run_values` 6 | `engineering_source.runtime_tree` |
| `thread_matter_topology` (A1) | `hamcp231/lane/thread_matter_topology.json` (`6cfb5217…7393`) | 74 | 8 | none | none |
| `thread_matter_topology_a2` | `hamcp231/lane/thread_matter_topology_a2.json` (`f36d8c1a…f798`) | 111 | 10 | `docker_assigned` 2 | none |
| `device_configuration_entity` | `hamcp366/device_configuration_entity.json` (`710a6aa1…f5dc`) | 933 | 189 | as above | the two D7 changes |

- `public_store_snapshot` (marketplace): `store_records` and `store_sha256`.
  The disposable Core fetches the live public HACS store, so both move when
  the public store changes. The installed theme's `install_fixture`,
  `installed_record`, `raw_installed` and `inventory.inventory_fingerprint`
  stay compared; a difference there means the public theme record changed
  and is a STOP. This class goes beyond the brief's three examples; the PR
  reports both values.
- `run_identity` +1 (created object): `engineering_source.commit` is the same
  commit as `source_commit`.
- `lane_suffix` (created object): `scenarios.*.readback.entity_id` and
  `registry_entity_id`. Created object ids end in a per-run
  `secrets.token_hex(4)` so a rerun cannot collide with an earlier object.
  The suffix appears in no other leaf.
- `derived_from_run_values` (created object): `scenarios.*.readback_fingerprint`,
  a SHA-256 over the read-back, which holds the suffixed ids.
- `engineering_source.runtime_tree` (created object, expected change): the
  committed receipts ran on `d84d8d9e` (tree `19d9d13f…`). `release/2.9.0`
  at `9af0d7dc` carries later runtime changes (tree `a2c1f448…`). 443-A
  changes no runtime file, so the branch's tree equals the base's.
- `docker_assigned` (A2): `lane_network.subnet` and `target_address`, the
  internal network's subnet and the Core's address in it. `lane_network.name`,
  `egress_probe.outcome` and every stub and topology count stay compared.

A self-check of each declaration against its own committed receipt passes:
no pattern is unused, and the only differences are the expected-change paths,
whose observed values a committed receipt cannot hold.
