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
| Receipt SHA-256 | `ed8659bd548c4f29865b16d10e5ecbb399a292fee3623b95a4ffbd7cef208b30` (5,403 bytes) | `fdd6991386289849a66b89b085750712d532061b70c20a596a577bbee262fca7` (6,907 bytes) |
| User unit (heavy-job gate, kind `lane`) | `hamcp364-scene-lane-b6f81dcf` | `hamcp364-helper-lane-b6f81dcf-r2` |
| Run, 2026-11-11 Central (the receipt's `started_utc` and `ended_utc`) | 21:17:54 to 21:18:14 | 21:19:31 to 21:19:57 |
| Profile paths | 19 | 10 |
| Scenarios | `scene_create`, `scene_existing_id_refused`, `scene_recreate`, `scene_recreate_undo`, `scene_undo` | `helper_create_input_boolean`, `helper_create_input_number`, `helper_undo_input_boolean`, `helper_undo_input_number`, `mixed_plan_create`, `mixed_plan_undo` |

Both receipts record:

- source commit `b6f81dcf4c0edd9cb1819cf511a540194ab1c3e5` (clean committed
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

The first launch of the created-helper lane, as unit
`hamcp364-helper-lane-b6f81dcf` at 21:18:20, was refused at the runner's port
check before any container or run directory existed: the scene lane had ended
on the same host port five seconds earlier and the port could not yet be
bound. The second launch, a minute later, changed nothing else.

The other five lanes ran once each on the same source commit. Their receipts
are in the directories that already held them:
[`../../hamcp134/lane/`](../../hamcp134/lane/README.md) (security metadata),
[`../../hamcp290/lane/`](../../hamcp290/lane/README.md) (Marketplace),
[`../../hamcp231/lane/`](../../hamcp231/lane/README.md) (Thread/Matter A1 and
A2) and [`../../hamcp366/`](../../hamcp366/README.md) (device configuration).
