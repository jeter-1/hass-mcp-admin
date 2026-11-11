# HAMCP-290: marketplace_read local exact-image lane

This directory holds the signing prerequisite for `core.marketplace_read`, which
is proposed for signed Core sequence 18. It contains source review, a committed
local lane, and that lane's disposable receipt. It creates no seq-18 candidate,
no owner signature and no production admission. Signing remains a separate
owner action.

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
2026.11.0 entry with 32 references and no `core.marketplace_read`.

Disposable authority is signed only by an in-memory ephemeral test key. The
two test authorities carry the 32 predecessor references, and then those same
references plus the compiled profile reference. This is not signing seq 18.

The lane proves exactly one profile reference, with contract fingerprint
`sha256:9393b59801d97786e3ac8cf2b44c1aec121198568bfb017cabaa5691acc0cfeb`.

- Before any container starts, `scripts/core_marketplace_read_local.py`
  compares the compiled reference with that pinned fingerprint.
- The receipt binds both the reference and the fingerprint, and the run is
  withheld unless the acceptance result reports the same reference.
- A changed profile must be re-proven. The pinned fingerprint is never
  updated to fit.

## Source manifest

This item adds 53 paths to `ci/core-contract-read-files.json`. With the 4 paths
HAMCP-134 added, the manifest grows from 456 to **513** paths. There are no
removals, and every prior profile mapping is retained.

The new `core.marketplace_read` profile maps **75** files:

- all 51 regular files of `homeassistant/components/marketplace/`, which is new in
  2026.11.0;
- 15 `websocket_api` files that frame, authenticate, dispatch and bound commands;
- `bootstrap.py`, `config_entries.py`, `const.py`, `core.py`, `data_entry_flow.py`,
  `loader.py`, and `helpers/discovery_flow.py`, `helpers/json.py` and
  `helpers/storage.py`, which cover integration loading, the entry
  disable/not_loaded path, store persistence and JSON output.

`core-marketplace-source-hashes.json` lists the profile's 75 paths in profile order,
with their SHA-256 values from the exact archive. Before any container starts,
the lane reports one of these refusals:

- `marketplace_manifest_missing`: the profile or the record is absent;
- `marketplace_source_provenance_mismatch`: the record names another Core or
  archive;
- `marketplace_manifest_mismatch`: the path lists differ;
- `marketplace_source_hash_mismatch`: a staged archive member re-hashes to a
  different value.

The O3 union records were regenerated from the same exact archives.

- `docs/evidence/hamcp315/core-sources/2026.10.3.json` now holds 441 present
  hashes, and `2026.11.0.json` holds 512.
- `manifest-compatibility.json` was regenerated with them.
- October lacks 72 manifest paths: the original 21 shared absences plus the 51
  Marketplace files.
- November still lacks only `homeassistant/helpers/condition.py`.
- Historical hashes are unchanged.

The generator is `generate_source_records.py` in this directory. It is offline,
reads tar members without extracting them, and omits absent paths instead of
inventing a digest. `--check` compares the committed bytes:

```
.artifacts/venv/bin/python -I docs/evidence/hamcp290/lane/generate_source_records.py \
  --archive 2026.10.3=.artifacts/inputs/core-2026.10.3.tar.gz \
  --archive 2026.11.0=.artifacts/inputs/core-2026.11.0.tar.gz [--check]
```

## Group mapping and CI

`ci/core-evidence-groups.json` appends `"marketplace_read": ["core.marketplace_read"]`.
The mapping never adds a profile to inherited authority, because
`passing_subset` still intersects with the authenticated predecessor.

Scheduled CI evidence does not run this local lane, so a missing
`marketplace_read` group receipt stays withheld. This supersedes the
§9 note in `docs/HAMCP_290_MARKETPLACE_READ_DESIGN.md` that a group needs a
workflow lane. No workflow is changed. Automating future Marketplace
inheritance in CI is outside this prerequisite.

`SIGNED_ONLY_DORMANT` is unchanged.

## Lane

`scripts/core_marketplace_read_local.py` runs the cached exact Core image on a
dedicated non-internal bridge, with an explicit `--memory` and the host port
bound to loopback 18123. It needs egress because Core's Marketplace fetches its
public store and, for the install fixture below, downloads one public theme
anonymously. There is no ha-mcp container, since the capability is
Engineering-native.

### Install fixture

The service projects installed repositories only, so a fresh disposable Core
lists none. Before the routed read, the harness installs exactly one public
theme into the disposable Core, through its own raw WebSocket and never through
Engineering:

- It refuses unless the disposable Marketplace has no GitHub account connected
  (`github_connected` is `false`). No token is used or supplied, and no secret
  is read.
- It refuses unless the Core is fresh, with no installed record.
- It installs only a named candidate: `JuanMTech/google_light_theme`, or
  `catppuccin/home-assistant` when the first is not in the store. No other
  repository can be installed. If neither is in the store, the fixture fails
  with `install_candidate_missing` before any install command and the lane
  withholds its receipt. It makes one install attempt, with
  `marketplace/warning/accept` then `marketplace/repository/install`.
- The receipt records the installed repository and its installed version
  (`install_fixture.repository` and `install_fixture.installed_version`).
- A theme is YAML only, so Core imports no downloaded code.
- Core downloads it anonymously from public GitHub hosts over the lane's existing
  bridge. The lane's network configuration is unchanged.
- The record must read back `installed` with a version, and it must be the only
  installed record. Otherwise the lane fails with `installed_record_missing` or
  `more_than_one_installed`.
- The files land in the disposable `/config`, which cleanup removes and reads
  back as absent.

`scripts/marketplace_read_contract_acceptance.py` drives the real
`MarketplaceInventoryService` through route admission. It checks:

- the predecessor authority is unavailable, with zero reads;
- the extended authority returns a routed page with `backend: core_marketplace`,
  exactly one `marketplace/repositories/list` command admitted and sent, no
  fallback, and installed totals equal to the raw Core list;
- anonymous callers are refused before any read;
- a version identity mismatch is refused before any command;
- four invalid argument sets are refused;
- the installed record is on the routed page exactly once. It has exactly the
  10 projected fields and no raw Core fields such as `local_path`. Its identity,
  flags and version match the raw record. Each text field is equal to the raw
  value, or it is bounded and the page discloses `sanitized_text`. No known
  secret appears on the page;
- a redaction canary: the same real record, read through a second service that
  treats one of the record's own strings as a secret. The page must omit that
  string and report `partial` with `sanitized_text` and a redaction summary;
- after the disposable Marketplace entry is disabled, the result is
  `marketplace_not_loaded`, with one list command, no HACS fallback and a
  health snapshot `fallback_count` of 0;
- the receipt's `fallback_count` is counted from the commands the lane's four
  readers admitted or wrote: every command other than
  `marketplace/repositories/list` counts, including one that was admitted but
  never written. The lane fails unless that count is 0.

The lane refuses under GitHub Actions or with `CORE_EVIDENCE_CONTEXT` set,
before any command. The harness's raw Core socket is opened with
`trust_env=False`, so proxy variables in the environment are not used.

The lane is local-only. It refuses unless the checkout is clean and committed,
the run directory is new and under `.artifacts/`, and the container names and
ports are free. Failure is WITHHELD. Cleanup must read back absence.

```
CORE_EVIDENCE_HOST_MODE=local .artifacts/venv/bin/python scripts/core_marketplace_read_local.py \
  --mode local --pull never --core-memory 2g --fixture-memory 128m \
  --journal <seq17-journal.json> --source-archive <core-2026.11.0.tar.gz> \
  --run-dir .artifacts/<new-run-dir>
```

Offline tests are in `tests/test_core_marketplace_read_local.py`.

## Receipt

`marketplace_read.json` is the receipt of the local run, copied byte-for-byte
from the run directory. It records the source commit, journal hash, profile
reference and fingerprint, source-hash digests, admission, container memory,
running image identity and cleanup. It is disposable evidence, not production
authority.

### Run 1: PASS (2026-11-09 22:35 CST)

- The lane ran under the host heavy-job gate as unit `hamcp290-lane` (kind `lane`)
  at source commit `83a14e003ed7ccad847a85676c6bdd9259fabcac`.
- The Core container ran with `--memory 2g` and the fixture writer with `128m`.
- The receipt's SHA-256 is
  `2cf03764f3f51c5de8bac048a5493b0e7fb680b91f8f7a436a4bdce624723e0c`. It binds
  `contract_fingerprint`
  `sha256:9393b59801d97786e3ac8cf2b44c1aec121198568bfb017cabaa5691acc0cfeb`.
- Authority went from 32 to 33 references. The predecessor made zero reads and
  was refused with `authority_unavailable`.
- Marketplace reached stage `running` with 6,417 store records, and every record
  passed whole-envelope validation.
- The routed read returned `core_marketplace` with `complete` completeness, one
  command and no fallback. `installed_total` was 0, which matches the raw list.
- Refusals: anonymous gave `authority_unavailable`, wrong Core version gave
  `identity_mismatch`, four invalid argument sets were refused, and the disabled
  entry gave `marketplace_not_loaded`.
- No HACS command was sent, and `fallback_count` was 0. Cleanup read back as
  clean.

Run 1 limitation, superseded by run 2: a fresh disposable Core has no
installed repositories, so run 1 projected no real installed record. Run 2
adds the install fixture above, which installs one theme into the disposable
Core only.

### Run 2: PASS with the install fixture (2026-11-10 06:07 CST)

This run's receipt superseded run 1's. Run 3's receipt supersedes it.

- The lane ran under the host heavy-job gate as unit `hamcp290-lane2` (kind
  `lane`) at source commit `cf0857e7cba0f43c0681d23e723fd2375b0fc089`, with Core
  at `--memory 2g` and the fixture writer at `128m`.
- The receipt's SHA-256 is
  `a7e02385e5d5d4587bccd78ecc4eba4780c2f417aee82a25db84b92bd5ecee28`. It binds
  the same `contract_fingerprint`
  `sha256:9393b59801d97786e3ac8cf2b44c1aec121198568bfb017cabaa5691acc0cfeb`.
- Install fixture: the fresh Core had 0 installed records and no GitHub account
  connected. The pinned candidate `JuanMTech/google_light_theme` (id
  `234032927`) installed anonymously at `v1.13`. No token was used and the
  lane's network configuration did not change.
- The routed read returned `core_marketplace`, `complete`, one command and no
  fallback, with `installed_total` 1 and `count` 1, matching the raw list. The
  record carried exactly the 10 projected fields, and every text field equalled
  the raw value.
- Redaction canary: with the record's description treated as a secret, the page
  omitted it and reported `partial` with `sanitized_text`.
- Authority, refusals, HACS and cleanup results are the same as run 1: 32 to 33
  references, zero predecessor reads, 6,417 store records, no HACS command,
  `fallback_count` 0, and cleanup read back as clean.

### Run 3: PASS on the corrected lane (2026-11-10 17:07 CST)

The committed `marketplace_read.json` is now this run's receipt. It supersedes
run 2's receipt.

- The lane ran under the host heavy-job gate as unit `hamcp290-lane3` (kind
  `lane`) at source commit `398ca8888b7abe324027b55668a1baa60606ac68`, with Core
  at `--memory 2g` and the fixture writer at `128m`. That commit carries the
  release/2.6.0 catch-up and the correction batch: the derived
  `fallback_count`, the named-candidates-only install fixture and the guard
  tests.
- The receipt's SHA-256 is
  `da7a40ac435a5347c73615fea27bfe081bd6b0d26e42a019f60bb0019c302b93`. It binds
  the same `contract_fingerprint`
  `sha256:9393b59801d97786e3ac8cf2b44c1aec121198568bfb017cabaa5691acc0cfeb` and
  the manifest SHA-256
  `b058369d2a9587593eefdae7a8eae3f69e480753735d37e552b5e71b603521c7` (513 paths,
  75 in the profile).
- Install fixture: the fresh Core had 0 installed records and no GitHub account
  connected. The named candidate `JuanMTech/google_light_theme` (id `234032927`)
  installed anonymously at `v1.13`. The receipt's `install_fixture` records the
  repository, id, category and installed version. It no longer has a
  `selection` field, because only a named candidate can be installed.
- `fallback_count` is 0, counted from the commands the four readers admitted or
  wrote. The health snapshot `fallback_count` was also 0, and no HACS command
  was sent.
- The routed read returned `core_marketplace`, `complete`, one command and no
  fallback, with `installed_total` 1 and `count` 1, matching the raw list. The
  record carried exactly the 10 projected fields, and every text field equalled
  the raw value.
- Redaction canary: with the record's description treated as a secret, the page
  omitted it and reported `partial` with `sanitized_text`.
- Authority, refusals and cleanup results are the same as run 2: 32 to 33
  references, zero predecessor reads, 6,417 store records, and cleanup read
  back as clean.
- The receipt names source commit `398ca888`, the commit the lane ran on. The
  commit that adds this receipt and this record changes only these two files.
