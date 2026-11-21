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
| `receipt-exclusions.json` | The declaration, committed before any lane ran: per lane, the committed receipt and its SHA-256, the leaves excluded from value equality, the leaves compared with a per-run suffix masked, and the value changes the task expects. The correction batch tightened it after the runs (see **Correction batch after review r1**). |
| `README.md` | This file. |
| `lane/device_configuration_entity.json` | The device lane's receipt on ha-mcp 9.2.0 (D7), copied byte for byte. |
| `lane/device_configuration_entity.comparison.json` | `compare_core_receipts.py` report for that receipt against hamcp366: equal. |
| `lane/helper_value.json` | The helper_value lane's first committed receipt, on ha-mcp 9.2.0 (D7), copied byte for byte. |
| `host/<lane>.json` | Each lane's receipt from the shared lane host (D2/D5), copied byte for byte. |
| `host/<lane>.comparison.json` | `compare_core_receipts.py` report for that receipt against the lane's committed receipt. |

Only receipt JSON and comparison reports are committed. Run directories, container
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

The D7 report as first committed (`5fbb8de1`) had `declaration_sha256`
`af4be4ce…`, the declaration as committed in `c94d66ce`, which held only the
device lane. Git history keeps that report. The correction batch regenerated
it over the same receipt, so all nine reports name the current declaration.

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
- A leaf that a suffix mask matches is not excluded. Both values must be
  strings ending in `_` and exactly eight lowercase hex digits, and everything
  before that ending must be equal. A value without that ending fails
  (`mask_shape`), and so does a different prefix (`masked_value`). Suffix-only
  differences are listed in the report under the mask's class.
- A declared expected change must hold exactly the declared committed value
  in the committed receipt and exactly the declared observed value in the new
  one. A stale baseline, an unchanged value or any other value fails.
- Every other leaf must be equal.

Exit 0 means equal, exit 1 means at least one difference. Under the brief, any
difference outside the declaration is a STOP.

A pattern is a list of segments. A string matches that dict key, a list of
strings matches any one of them, `*` matches exactly one segment (a key or a
list index) and `**` matches zero or more. A literal never matches a list
index. Every exclusion and mask pattern must match at least one leaf of the
committed receipt, so a pattern cannot silently go stale. An expected change
may not sit under an exclusion or a mask, a leaf may not be both masked and
excluded, and a class name may not be both.

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

Each lane is re-run on the shared lane host and compared with its
committed receipt (runs below). Every lane excludes `run_identity` (`source_commit`,
`started_utc`, `ended_utc`) and `host_admission` (the five admission
readings). Anything a lane adds is listed below; every other leaf is
compared, including all results, refusal codes, counts, send and dispatch
counts, image digests, profile fingerprints and the journal, manifest and
source-hash SHA-256s.

| Lane | Committed receipt (SHA-256) | Leaves | Excluded | Masked | Extra classes | Expected changes |
|---|---|---|---|---|---|---|
| `helper_value` | `hamcp443/lane/helper_value.json` (`ce3a227c…d814`), the D7 9.2.0 run on `c94d66ce` | 123 | 8 | 0 | none | none |
| `marketplace_read` | `hamcp290/lane/marketplace_read.json` (`a61da63c…1037`) | 100 | 10 | 0 | `public_store_snapshot` 2 | none |
| `security_metadata` | `hamcp134/lane/security_metadata.json` (`5e9e0439…706a`) | 104 | 8 | 0 | none | none |
| `scene_create_delete` | `hamcp364/lane/scene_create_delete.json` (`03fef43d…7b8e`) | 134 | 13 | 6 | `run_identity` +1, `derived_from_run_values` 4; mask `lane_suffix` 6 | `engineering_source.runtime_tree` |
| `created_helper_delete` | `hamcp364/lane/created_helper_delete.json` (`c54b261f…e1bd`) | 181 | 15 | 9 | `run_identity` +1, `derived_from_run_values` 6; mask `lane_suffix` 9 | `engineering_source.runtime_tree` |
| `thread_matter_topology` (A1) | `hamcp231/lane/thread_matter_topology.json` (`6cfb5217…7393`) | 74 | 8 | 0 | none | none |
| `thread_matter_topology_a2` | `hamcp231/lane/thread_matter_topology_a2.json` (`f36d8c1a…f798`) | 111 | 10 | 0 | `docker_assigned` 2 | none |
| `device_configuration_entity` | `hamcp366/device_configuration_entity.json` (`710a6aa1…f5dc`) | 933 | 189 | 0 | as above | the two D7 changes |

- `public_store_snapshot` (marketplace): `store_records` and `store_sha256`.
  The disposable Core fetches the live public HACS store, so both move when
  the public store changes. The installed theme's `install_fixture`,
  `installed_record`, `raw_installed` and `inventory.inventory_fingerprint`
  stay compared; a difference there means the public theme record changed
  and is a STOP. This class goes beyond the brief's three examples; the PR
  reports both values.
- `run_identity` +1 (created object): `engineering_source.commit` is the same
  commit as `source_commit`.
- `lane_suffix` (created object, suffix mask): `scenarios.*.readback.entity_id`,
  and `readback.registry_entity_id` in the scenarios that create the object
  (scene: `scene_create`, `scene_recreate`; helper:
  `helper_create_input_boolean`, `helper_create_input_number`,
  `mixed_plan_create`). Created object ids end in `_` plus a per-run
  `secrets.token_hex(4)`, so a rerun cannot collide with an earlier object.
  Only that final `_` and eight lowercase hex digits are masked: the domain and
  object name compare exactly, and an id without that ending fails. After an
  undo, `registry_entity_id` is `null` and compares exactly. The suffix
  appears in no other leaf.
- `derived_from_run_values` (created object): `scenarios.*.readback_fingerprint`,
  a SHA-256 over the read-back, which holds the suffixed ids.
- `engineering_source.runtime_tree` (created object, expected change): the
  committed receipts ran on `d84d8d9e` (tree `19d9d13f…`). The pinned
  observed value, `a2c1f448…`, is the runtime tree
  (`hass_mcp_engineering_beta/ha_mcp_engineering`) at the receipt commit
  `982e2bf3`, the same as `9af0d7dc`'s. This branch, after the `ad9881be`
  catch-up merge, has tree `d91f1358…`. The two trees differ in 5 of 400
  files (`application.py`, `capabilities.py`,
  `ha_core_readmission/profiles.py`, `ha_core_readmission/source.py`,
  `tools/marketplace_inventory.py`), comment and docstring changes only. A
  lane re-run at a later tree fails this pin by design, because this is a
  one-time proof of the refactor at `982e2bf3`.
- `docker_assigned` (A2): `lane_network.subnet` and `target_address`, the
  internal network's subnet and the Core's address in it. `lane_network.name`,
  `egress_probe.outcome` and every stub and topology count stay compared.

A self-check of each declaration against its own committed receipt passes:
no pattern is unused, and the only differences are the expected-change paths,
whose observed values a committed receipt cannot hold.

## Shared lane host runs

All runs below used `982e2bf3` (a clean checkout with the declaration, then `b60052b3…`, already committed) on scriptbox (amd64). Each ran
as a `systemd-run --user` unit with `MemoryMax=3G`, `OOMPolicy=continue` and `RuntimeMaxSec=3600`, one lane at a
time, with `--mode local --pull never` and the same staged inputs as the D7 runs. After marketplace run 1, each
lane on port 18123 started only once no socket remained on that port.

| Lane | Unit | Central time, 2026-11-20 | Harness admission (load1, available, other heavy jobs, containers) | Result | Receipt SHA-256 | Excluded leaves that differed |
|---|---|---|---|---|---|---|
| `device_configuration_entity` | `hamcp443-devcfg-host-982e2bf3` | 21:39:19 to 21:43:47 | load1 0.46, 11.69 GB, 0, 0 | `PASS`, clean; equal; both D7 changes applied | `eb3ce849…f55d` | `run_identity` 3, `host_admission` 3, `core_run_ids` 54, `engineering_task_ids` 20, `core_timestamps` 42, `derived_from_run_values` 36; `timing` 0 |
| `helper_value` | `hamcp443-helper-host-982e2bf3` | 21:45:44 to 21:47:11 | load1 1.34, 11.49 GB, 0, 0 | `PASS`, clean; equal | `ba345359…6413` | `run_identity` 3, `host_admission` 3 |
| `marketplace_read` run 1 | `hamcp443-marketplace-host-982e2bf3` | 21:47:46, 2.4 s | not reached | refused at preflight (`OSError`, see below) | none | none |
| `thread_matter_topology` (A1) | `hamcp443-a1-host-982e2bf3` | 21:52:01 to 21:52:17 | load1 3.46, 11.36 GB, 0, 0 | `PASS`, clean; equal | `c67841a5…80c1` | `run_identity` 3, `host_admission` 3 |
| `marketplace_read` run 2 | `hamcp443-marketplace-host2-982e2bf3` | 21:52:56 to 21:53:22 | load1 2.10, 11.44 GB, 0, 0 | `PASS`, clean; equal | `9ed0792e…1522` | `run_identity` 3, `host_admission` 3, `public_store_snapshot` 2 (`store_records` 6415 to 6903; `store_sha256` `5566c816…` to `802ca902…`) |
| `thread_matter_topology_a2` run 1 | `hamcp443-a2-host-982e2bf3` | 21:55:02, 0.9 s | not reached | refused at preflight (`a2_stub_file_set_mismatch`, see below) | none | none |
| `thread_matter_topology_a2` run 2 | `hamcp443-a2-host2-982e2bf3` | 21:57:38 to 21:58:00 | load1 1.38, 11.48 GB, 0, 0 | `PASS`, clean; equal | `7c145f6e…21de` | `run_identity` 3, `host_admission` 3; `docker_assigned` 0 |
| `security_metadata` | `hamcp443-security-host-982e2bf3` | 21:58:26 to 21:58:40 | load1 1.35, 11.36 GB, 0, 0 | `PASS`, clean; equal | `12939cc0…2bf7` | `run_identity` 3, `host_admission` 3 |
| `scene_create_delete` | `hamcp443-scene-host-982e2bf3` | 22:02:28 to 22:02:50 | load1 1.32, 11.45 GB, 0, 0 | `PASS`, clean; equal; `runtime_tree` change applied | `b7a57bd7…aa83` | `run_identity` 4, `host_admission` 3, `derived_from_run_values` 4; masked `lane_suffix` 6 (suffix only) |
| `created_helper_delete` | `hamcp443-createdhelper-host-982e2bf3` | 22:03:50 to 22:04:19 | load1 1.24, 11.42 GB, 0, 0 | `PASS`, clean; equal; `runtime_tree` change applied | `8904c234…01b3` | `run_identity` 4, `host_admission` 3, `derived_from_run_values` 6; masked `lane_suffix` 9 (suffix only) |

Two runs were refused at preflight. Neither created a run directory, claimed a name or started a container, and
neither produced a receipt:

- **`marketplace_read` run 1** (`hamcp443-marketplace-host-982e2bf3`, 21:47:46) raised `OSError` after 2.4 s:
  `require_free_ports` could not bind 127.0.0.1:18123. The helper lane's teardown 39 s earlier had left
  server-side `TIME_WAIT` sockets on that port. The bind sets no `SO_REUSEADDR`, which is the same as the base
  harness on `9af0d7dc`. The mechanism was confirmed afterwards:
  - after A1, 17 sockets remained in `TIME_WAIT` on 18124, and a test bind failed with `EADDRINUSE`;
  - after each later 18123 lane, 19 to 61 such sockets remained.

  Run 2 started once the port was clear and passed.
- **`thread_matter_topology_a2` run 1** (`hamcp443-a2-host-982e2bf3`, 21:55:02) refused
  `a2_stub_file_set_mismatch` after 0.9 s. The committed stub directory held an extra, git-ignored
  `__pycache__/stub.cpython-312.pyc`. Earlier test runs that import `stub.py` had written it. `verify_stub` is
  unchanged from base, and it binds the exact file set. The ignored cache was removed, and run 2 passed against
  the committed directory's 7 files.

Both refusals' diagnostics label the lane `"helper_value"`. That is the pre-existing fallback label for a failure
outside a lane context (`lane_failure_diagnostics.py`, unchanged).

The orchestrator's `hamcp437-i2d-fast3` unit (a Fast check, started 21:43:04) ran alongside every run from the
device lane's last 43 s onward. Under the host-wide policy that is one other heavy job. The harness's own admission
reported `other_heavy_jobs: 0` because it counts only Evidence `pwsh` processes and lane containers. Both counts were
within limits: the policy allows at most 4 heavy jobs host-wide, and the harness refuses above 1.

The last column gives the regenerated reports (correction batch, below). In the created-object lanes, the
`registry_entity_id` read-backs after an undo are `null` in both receipts, because the undo removed the object. Since
the correction batch they compare exactly (scene 2 leaves, helper 3) instead of being excluded.

## Correction batch after review r1

Independent review r1 of PR #469 (Cursor) found that the created-object id leaves were excluded whole, so any id
would compare equal (R1-01), and that the two `runtime_tree` reasons described the pin inexactly (R1-02). The
architect's ruling set one correction batch. Editing the declaration after the runs is acceptable here only because
the edits tighten it (R1-01) or change prose (R1-02).

- **Comparator.** `compare_core_receipts.py` gained a separate `suffix_masked` class, described under **How the
  comparison works**. It does not widen `excluded`. The report model is now
  `hamcp443-receipt-comparison-report-v2`, which adds `masked_leaves` and `masked_differences`.
- **Declaration.** Its SHA-256 went from `b60052b3…1f03` (as at `982e2bf3`, when the lanes ran) to
  `5d008939…c792`. The diff is exactly two kinds of entry, both in the two created-object lanes:
  - the id entries, which moved from `excluded` to `suffix_masked`, with `registry_entity_id` now masked only in the
    create scenarios;
  - the two `runtime_tree` reasons.

  No other entry changed.
- **Receipts.** No receipt was rerun or edited. The SHA-256 of every receipt file, recorded before and after the
  regeneration, is unchanged:

  | Receipt | SHA-256 |
  |---|---|
  | `hamcp443/host/created_helper_delete.json` | `8904c23418745571b721e9ad21d9ba998b8b1f9824535481401f1c4ac00f01b3` |
  | `hamcp443/host/device_configuration_entity.json` | `eb3ce849675439be07475d6a0a6c1746f5ff62303f4c6dfe6d8c5819b9f3f55d` |
  | `hamcp443/host/helper_value.json` | `ba345359934fb286c46178e9cadd474bac29992f1d73c61aecc2286935426413` |
  | `hamcp443/host/marketplace_read.json` | `9ed0792e9649d85a4bbaf09024bebb1f0f95aa52878a264a0da667aa05361522` |
  | `hamcp443/host/scene_create_delete.json` | `b7a57bd78080abcda502b60aa5a4820e1621afe801a5a865fcf1fa145defaa83` |
  | `hamcp443/host/security_metadata.json` | `12939cc08c036b98b63afd3d589492ffa6d1064a385e621709235fb091962bf7` |
  | `hamcp443/host/thread_matter_topology.json` | `c67841a5f6573981616b1104655282fc7c045184593392bd0100a631a07780c1` |
  | `hamcp443/host/thread_matter_topology_a2.json` | `7c145f6e828d55ab699d3a133a606e5eed12f347f6779c84f0f5e4dc6ed221de` |
  | `hamcp443/lane/device_configuration_entity.json` | `8a82f59203371cab270bff52d6be2be4973a553eb5d77d78c62be81a64450ac2` |
  | `hamcp443/lane/helper_value.json` | `ce3a227cf42d66f3843615df9993a77e57aab4f53af859cb0cc79b374adcd814` |
  | `hamcp134/lane/security_metadata.json` | `5e9e0439a40699d5d5e7a52368cf82790cde56dd0787b298b3dfde1810cb706a` |
  | `hamcp231/lane/thread_matter_topology.json` | `6cfb52178627332beab4dc07a9ab34a641d12e93aec523a27fadb055cc2b7393` |
  | `hamcp231/lane/thread_matter_topology_a2.json` | `f36d8c1a82e657de1dd82373eace0372f3ae31c2a1ee7e6fc50330f92913f798` |
  | `hamcp290/lane/marketplace_read.json` | `a61da63ccedc9989a8c1a02c3cf4880f3323b3a539898c9aca080b937a781037` |
  | `hamcp364/lane/created_helper_delete.json` | `c54b261f7dd46baa80ee7805c9ec6983e22d5c5ee9202c5a7befc8577cade1bd` |
  | `hamcp364/lane/scene_create_delete.json` | `03fef43d9c787762def3786955733b801a5eadf5e428a8bfaab25e52c89b7b8e` |
  | `hamcp366/device_configuration_entity.json` | `710a6aa172ded0dd168eef8b9784456081db0f3f888985ba17a742bc1071f5dc` |

- **Reports.** First, the previous comparator and declaration were run again over the committed host receipts. They
  reproduced all eight committed host reports byte for byte. Then the new comparator, over the same receipts,
  regenerated all nine reports. Every one is `equal: true` with no differences, and every one names `5d008939…`:

  | Report | Excluded | Masked (suffix differed) | Change from the previous report |
  |---|---|---|---|
  | `host/device_configuration_entity` | 189 | 0 | model and declaration hash only |
  | `host/helper_value` | 8 | 0 | model and declaration hash only |
  | `host/marketplace_read` | 10 | 0 | model and declaration hash only |
  | `host/security_metadata` | 8 | 0 | model and declaration hash only |
  | `host/scene_create_delete` | 13 | 6 (6) | the 6 `lane_suffix` differences move from excluded to masked; 21 to 13 excluded |
  | `host/created_helper_delete` | 15 | 9 (9) | the 9 `lane_suffix` differences move from excluded to masked; 27 to 15 excluded |
  | `host/thread_matter_topology` | 8 | 0 | model and declaration hash only |
  | `host/thread_matter_topology_a2` | 10 | 0 | model and declaration hash only |
  | `lane/device_configuration_entity` (D7) | 189 | 0 | model and declaration hash only (`af4be4ce…` before) |

  The D7 report's device lane entry is identical in `af4be4ce…`, `b60052b3…` and `5d008939…`. Its excluded
  differences, including the one `timing` leaf, are unchanged.
