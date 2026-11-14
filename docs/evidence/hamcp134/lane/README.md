# HAMCP-134: security_metadata local exact-image lane

This directory holds the signing prerequisite for `core.security_metadata_read`,
which is proposed for signed Core sequence 18. It contains source review, a
committed local lane, and that lane's disposable receipt. It creates no seq-18
candidate, no owner signature and no production admission. Signing remains a
separate owner action.

The contract is `docs/HAMCP_134_SECURITY_METADATA_FEASIBILITY.md`.

## Exact authority and scope

The target Core is **2026.11.0**:

- source commit `74cf5a49c63ed8d4d87717844bf14f79c28c9cea`;
- archive SHA-256 `85f3c42c9f77aa45e01faff4471007beadf5cb50c4324f496a239139eec70ff1`;
- image index `sha256:c6c1ca9931036bf3a2c4cc088d8db25dfea3b33935a5ecc83c40349462e2f69e`,
  pinned by the existing `scripts/core_lane_bindings.py` binding.

No binding change is needed.

The predecessor journal is signed sequence 17. Its bytes must hash to
`3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d`. The lane
verifies it with the committed public key. It then requires exactly one
2026.11.0 entry with 32 references and no `core.security_metadata_read`.

Disposable authority is signed only by an in-memory ephemeral test key. The
two test authorities carry the 32 predecessor references, and then those same
references plus the compiled profile reference. This is not signing seq 18.

The lane proves exactly one profile reference (profile id
`core_security_metadata_read_v1`, adapter
`compiled-core-security-metadata-read-v1`), with contract fingerprint
`sha256:9aa788b50f6d9544808bd8b89fd50c00ffd32e7ebcdd940b9dddace18b2e9c55`.

- Before any container starts, `scripts/core_security_metadata_local.py`
  compares the compiled reference with that pinned fingerprint.
- The receipt binds both the reference and the fingerprint, and the run is
  withheld unless the acceptance result reports the same reference.
- A changed profile must be re-proven. The pinned fingerprint is never
  updated to fit.

## Source manifest

`ci/core-contract-read-files.json` grows from 456 to **460** paths. The 4 added
paths are `components/assist_pipeline/__init__.py`,
`components/assist_pipeline/pipeline.py`, `components/frontend/__init__.py` and
`core_config.py`. There are no removals, and every prior profile mapping is
retained.

The new `core.security_metadata_read` profile maps **33** files:

- `auth/__init__.py`, `auth/const.py`, `auth/models.py` and
  `auth/mfa_modules/__init__.py` (users, owner, MFA module ids);
- `components/auth/__init__.py` (`auth/current_user`) and
  `components/config/auth.py` (`config/auth/list`);
- `components/http/__init__.py`, `components/http/config.py` and
  `components/http/websocket_api.py` (`http/config`);
- `core_config.py` (`get_config` URL fields);
- `components/homeassistant/exposed_entities.py`
  (`homeassistant/expose_new_entities/get`);
- `components/assist_pipeline/__init__.py` and `pipeline.py`
  (`assist_pipeline/pipeline/list`);
- `components/conversation/agent_manager.py` and `http.py`
  (`conversation/agent/list`);
- `components/frontend/__init__.py` (`get_panels`);
- 15 `websocket_api` files that frame, authenticate, dispatch and bound commands;
- `helpers/collection.py` and `helpers/json.py`.

`core-security-metadata-source-hashes.json` lists the profile's 33 paths in
profile order, with their SHA-256 values from the exact archive. Before any
container starts, the lane reports one of these refusals:

- `security_metadata_manifest_missing`: the profile or the record is absent;
- `security_metadata_source_provenance_mismatch`: the record names another Core
  or archive;
- `security_metadata_manifest_mismatch`: the path lists differ;
- `security_metadata_source_hash_mismatch`: a staged archive member re-hashes to
  a different value.

The O3 union records were regenerated from the same exact archives.

- `docs/evidence/hamcp315/core-sources/2026.10.3.json` now holds 439 present
  hashes, and `2026.11.0.json` holds 459.
- `manifest-compatibility.json` was regenerated with them.
- All 4 new paths exist in both releases, so October still lacks only the
  original 21 shared absences and November still lacks only
  `homeassistant/helpers/condition.py`.
- Historical hashes are unchanged.

The generator is `generate_source_records.py` in this directory. It is offline,
reads tar members without extracting them, and omits absent paths instead of
inventing a digest. `--check` compares the committed bytes:

```
.artifacts/venv/bin/python -I docs/evidence/hamcp134/lane/generate_source_records.py \
  --archive 2026.10.3=.artifacts/inputs/core-2026.10.3.tar.gz \
  --archive 2026.11.0=.artifacts/inputs/core-2026.11.0.tar.gz [--check]
```

## Group mapping and CI

`ci/core-evidence-groups.json` appends
`"security_metadata": ["core.security_metadata_read"]`. The mapping never adds a
profile to inherited authority, because `passing_subset` still intersects with
the authenticated predecessor.

Scheduled CI evidence does not run this local lane, so a missing
`security_metadata` group receipt stays withheld. No workflow is changed.

`SIGNED_ONLY_DORMANT` gains `core.security_metadata_read` (runtime-only,
dormant until an owner-signed registry admits it).

## Lane

`scripts/core_security_metadata_local.py` runs the cached exact Core image on a
dedicated bridge, with an explicit `--memory` and the host port bound to
loopback 18123. The bridge is non-internal only because Docker cannot publish
ports from an internal network; the lane itself needs no egress. There is no
ha-mcp container, since the capability is Engineering-native.

`scripts/security_metadata_contract_acceptance.py` drives the real
`SecurityMetadataService` through route admission. It checks:

- the predecessor authority is unavailable, with zero reads;
- the extended authority's full report sends exactly the 10 planned commands
  (`auth/current_user`, `config/auth/list`, `http/config`, `get_config`,
  `homeassistant/expose_new_entities/get` for `cloud.alexa`,
  `cloud.google_assistant` and `conversation`, `assist_pipeline/pipeline/list`,
  `conversation/agent/list`, `get_panels`), equal to the read plan and to the
  audit `commands_sent`, with no fallback;
- projected counts and flags equal the raw Core reads (users, owners, MFA
  module ids, HTTP `active_config_type`, URL presence, expose-new defaults,
  pipeline, agent and panel counts);
- user ids, usernames, non-system user names, the access token and the
  synthetic secret never appear in the output;
- `sections=["mfa"]` sends only `auth/current_user`;
- anonymous callers are refused before any read;
- Core versions 2026.10.0, 2026.10.1 and 2026.10.3 give `identity_mismatch`
  before any command;
- five invalid argument sets are refused;
- no never-sent command or prefix is sent, and `fallback_count` is 0;
- before/after snapshots of auth, HTTP config, entity registry, pipelines and
  expose-new defaults are unchanged.

The lane is local-only. It refuses unless the checkout is clean and committed,
the run directory is new and under `.artifacts/`, and the container names and
ports are free. Failure is WITHHELD. Cleanup must read back absence.

```
CORE_EVIDENCE_HOST_MODE=local .artifacts/venv/bin/python scripts/core_security_metadata_local.py \
  --mode local --pull never --core-memory 2g --fixture-memory 128m \
  --journal <seq17-journal.json> --source-archive <core-2026.11.0.tar.gz> \
  --run-dir .artifacts/<new-run-dir>
```

Offline tests are in `tests/test_core_security_metadata_local.py`.

## Receipt

`security_metadata.json` is the receipt of the local run, copied byte-for-byte
from the run directory. It records the source commit, journal hash, profile
reference and fingerprint, source-hash digests, admission, container memory,
running image identity and cleanup. It is disposable evidence, not production
authority.

### Run 1: PASS (2026-11-09 23:31 CST)

- The lane ran under the host heavy-job gate as unit `hamcp134-lane` (kind `lane`)
  at source commit `bf3c98847f2fd6b72e80c43cafbc2dec2c907b89`.
- The Core container ran with `--memory 2g` and the cleanup fixture with `128m`.
- The receipt's SHA-256 is
  `799799a6844aa6769d2098a1fd09506c95f2d94ccfa5dc0f5f4d39f1c0d509a1`. It binds
  `contract_fingerprint`
  `sha256:9aa788b50f6d9544808bd8b89fd50c00ffd32e7ebcdd940b9dddace18b2e9c55`.
- Authority went from 32 to 33 references. The predecessor made zero reads and
  was refused with `authority_unavailable`.
- The full report sent exactly the 10 planned commands (equal to the read plan and
  the audit), with no fallback. `sections=["mfa"]` sent only `auth/current_user`.
- Coverage: `users`, `http_boundary`, `urls`, `assist` and `panels` observed; `mfa`
  `partial` with `connection_identity_not_supervisor` (connection identity
  `other_connection_user`, `is_admin` true). Completeness `partial` for that reason.
- Projected counts equal the raw Core reads: 2 users, 1 MFA module, 1 pipeline,
  1 agent, 17 panels; HTTP `active_config_type` `stable`. Output was 6,856 bytes.
  `redaction` is null: no Core value matched a redaction category, and the
  identifier, token and secret absence checks passed.
- Refusals: anonymous gave `authority_unavailable`, Core 2026.10.0/.1/.3 gave
  `identity_mismatch`, five invalid argument sets were refused.
- No never-sent command was sent, `fallback_count` was 0, the 7 before/after
  snapshots were unchanged, and cleanup read back as clean.

### Run 2: PASS (2026-11-11 21:21 CST)

Run 2 repeats the lane on the HAMCP-364 branch, under the host heavy-job gate
as unit `hamcp364-security-lane-b6f81dcf`. HAMCP-364 changed
`scripts/security_metadata_contract_acceptance.py`: its raw WebSocket client
dials only the constant `RAW_CORE_URL` (`ws://127.0.0.1:18123/api/websocket`),
sends only the shapes listed in `RAW_COMMANDS`, and opens its session through
`scripts/lane_session.py`, which refuses a redirect. `security_metadata.json`
recorded this run until run 3 replaced it (the run 1 receipt is in commit
`32beeb20`, the run 2 receipt in commit `5ea622ec`):

- source commit `b6f81dcf4c0edd9cb1819cf511a540194ab1c3e5`;
- manifest SHA-256
  `48b8b87590a7c023f6ff9f44ff05abcab4fbb9aa3f597d57c2a131a149cfbbed` (527
  paths, 33 in the profile). Run 1 bound the manifest as it was at its source
  commit,
  `07bc9034ebb66881bd91fe710174db7dfba67e43c598a53d960f4f81587b1f4d`;
- the same image, memory limits and `contract_fingerprint` as run 1;
- receipt SHA-256
  `88a3dd1c41871bfb4814a6c6769d969148549f9d7eae075f10a2e7dd30182b8b`.

Apart from the source commit, the manifest hash, the times and the host
admission snapshot, every receipt field equals run 1.

### Run 3: PASS (2026-11-12 03:30 CST)

Run 3 repeats the lane on the HAMCP-364 branch, under the host heavy-job gate
as unit `hamcp364-security-lane-5fc40f93`. After run 2 the branch changed
`scripts/real_ha_contract_tests.py`, which the lane runner
`scripts/core_security_metadata_local.py` imports: the config flow id that
harness accepts is now the 26-character ULID Core issues (commit `4a72ee8a`).
`scripts/security_metadata_contract_acceptance.py` did not change.
`security_metadata.json` recorded this run until the R8 re-run below replaced it:

- source commit `5fc40f939756ee4ed5b004f88e53c00fd7d939c8`;
- the same manifest SHA-256, image, memory limits and `contract_fingerprint`
  as run 2;
- receipt SHA-256
  `a7f0697af9d350b69a122d540e5b5af29e5090a0779edebb3264c7a66e8d4a47`.

Apart from the source commit, the times and the host admission snapshot, every
receipt field equals run 2. The limitation below applies to all three runs.

### R8 run on the signing commit: PASS (2026-11-13 21:55 CST)

The committed `security_metadata.json` is the R8 re-run on the signing commit
(signing plan §5A.8 step 2; architect ruling 2026-11-13, about 22:15 CST). It supersedes
run 3's receipt. Preparation lists its SHA-256 in
`review_artifacts_sha256` (signing plan §5A.8 step 4). Nothing has been
signed.

- user unit `hamcp332-security-lane-d84d8d9e` (heavy-job gate, kind
  `lane`), 2026-11-13 21:55:16 to 21:55:27 Central (the receipt's
  `started_utc` and `ended_utc`);
- source commit `d84d8d9e69c4fff8481b018182a6b43f088aac7d`;
- result `PASS`, `cleanup.clean: true`;
- manifest SHA-256
  `073fa5ad6adc954940a5678d5b064795d2a45b2728e76998fc4b79309e8cd9a1`
  (`ci/core-contract-read-files.json`, 535 paths);
- the sequence 17 predecessor journal, SHA-256
  `3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d`;
- receipt SHA-256
  `5e9e0439a40699d5d5e7a52368cf82790cde56dd0787b298b3dfde1810cb706a`
  (3,707 bytes).

Limitation: the disposable Core has one owner (the onboarding user) and no
non-admin user, no pending or trial HTTP configuration and no Supervisor
identity, and creating them would add writes to the lane. The lane connects
with a long-lived token (`ha_url_env`/`ha_token`), so the MFA section is
`partial` with `connection_identity_not_supervisor`. Those projections are
proven offline in `tests/test_hamcp134_security_metadata.py`.
