# HAMCP-364: scene and created-helper lanes, review receipts

Two receipts of `scripts/core_created_object_local.py`, one passing local run
per lane. Each is the lane's canonical output, copied byte for byte from the
run directory; neither is edited by hand.

**These receipts are review evidence, not signing receipts.** Every included
lane re-runs once on the signing commit, and the signing receipt will bind that
commit. Nothing here signs a registry entry, builds a candidate, or admits a
capability in production.

The two lanes had no committed receipt before these. Neither
`ci/core-evidence-groups.json` nor the lanes' design document
(`docs/HAMCP_329_298_SCENE_CREATE_AND_CREATE_ROLLBACK_DESIGN.md`) names a
directory for one, so they are kept here, under the change that last touched
their acceptance module (`scripts/created_object_contract_acceptance.py`) and
on whose head they ran.

| | `scene_create_delete.json` | `created_helper_delete.json` |
|---|---|---|
| Lane (`--lane`) | `scene_create_delete` | `created_helper_delete` |
| Capability | `core.scene_create_delete_operation` | `core.created_helper_delete_operation` |
| `contract_fingerprint` | `sha256:93378ae0385528f16deb977a985b778ba2dd2d39be6f5cb397ef74f2060a400d` | `sha256:3f2e681310a009a4fc853af6fa24f8eca1c671c7dd75bddbbc8b403fec9cdbf4` |
| Result | `PASS`, `cleanup.clean: true` | `PASS`, `cleanup.clean: true` |
| Receipt SHA-256 | `7320406f98b60c34499c2d10e71675981ffc3d8b0f080cdba53de131c7e57860` (5,402 bytes) | `5e4d641def9c6e248ca88a22fbf3836c5c836f9b429270116dffd1b547897df6` (6,906 bytes) |
| User unit (heavy-job gate, kind `lane`) | `hamcp364-scene-lane-5fc40f93` | `hamcp364-helper-lane-5fc40f93` |
| Run, 2026-11-12 Central (the receipt's `started_utc` and `ended_utc`) | 03:26:38 to 03:27:03 | 03:28:06 to 03:29:07 |
| Profile paths | 19 | 10 |
| Scenarios | `scene_create`, `scene_existing_id_refused`, `scene_recreate`, `scene_recreate_undo`, `scene_undo` | `helper_create_input_boolean`, `helper_create_input_number`, `helper_undo_input_boolean`, `helper_undo_input_number`, `mixed_plan_create`, `mixed_plan_undo` |

Both receipts record:

- source commit `5fc40f939756ee4ed5b004f88e53c00fd7d939c8` (clean committed
  checkout of the HAMCP-364 branch, which contains `release/2.6.0` at
  `aeb3b314dd180592874243c9ae370a63f0622c2b`);
- manifest SHA-256
  `48b8b87590a7c023f6ff9f44ff05abcab4fbb9aa3f597d57c2a131a149cfbbed`
  (`ci/core-contract-read-files.json`, 527 paths);
- Core 2026.11.0,
  `ghcr.io/home-assistant/home-assistant:2026.11.0@sha256:c6c1ca9931036bf3a2c4cc088d8db25dfea3b33935a5ecc83c40349462e2f69e`,
  with `--pull never`, Core at `2g` and the fixture container at `128m`;
- the sequence 17 predecessor journal, SHA-256
  `3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d`, and the
  authority transition from 32 to 33 references;
- the lane's own Docker bridge with host port 18123 bound to `127.0.0.1`, and
  `production_authority: false`.

All seven lanes ran on this source commit, one at a time and once each: no
launch was refused and none was repeated. Between lanes the launcher waited
until the previous lane's host port could be bound again. Before the first
launch, three ignored `__pycache__` directories that local test runs had left
in fixture directories (the Matter Server stub directory and two
device-configuration fixture directories) were moved out of them; no tracked
file changed. The receipts of the other five lanes are in the directories
that already held them:
[`../../hamcp134/lane/`](../../hamcp134/lane/README.md) (security metadata),
[`../../hamcp290/lane/`](../../hamcp290/lane/README.md) (Marketplace),
[`../../hamcp231/lane/`](../../hamcp231/lane/README.md) (Thread/Matter A1 and
A2) and [`../../hamcp366/`](../../hamcp366/README.md) (device configuration).

These two receipts replace the ones recorded on source commit `b6f81dcf`
(scene `ed8659bd548c4f29865b16d10e5ecbb399a292fee3623b95a4ffbd7cef208b30`,
created helper
`fdd6991386289849a66b89b085750712d532061b70c20a596a577bbee262fca7`; both are in
commit `5ea622ec`). After that commit the branch changed
`scripts/real_ha_contract_tests.py`, which `scripts/core_created_object_local.py`
and every other lane runner import (commit `4a72ee8a`: the config flow id that
harness accepts is the 26-character ULID Core issues), and
`scripts/device_configuration_contract_acceptance.py` (commits `58bdab1c` and
`5fc40f93`), so all seven lanes ran again. Apart from the source commit, the
times, the host admission snapshot and the entity ids and readback
fingerprints each run issues, every field of the two receipts equals the
earlier one.
