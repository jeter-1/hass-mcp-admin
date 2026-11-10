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

`ci/core-contract-read-files.json` grows from 456 to **509** paths. The 53 added
paths include no removals, and every prior profile mapping is retained.

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

- `docs/evidence/hamcp315/core-sources/2026.10.3.json` now holds 437 present
  hashes, and `2026.11.0.json` holds 508.
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
public store. There is no ha-mcp container, since the capability is
Engineering-native.

`scripts/marketplace_read_contract_acceptance.py` drives the real
`MarketplaceInventoryService` through route admission. It checks:

- the predecessor authority is unavailable, with zero reads;
- the extended authority returns a routed page with `backend: core_marketplace`,
  exactly one `marketplace/repositories/list` command admitted and sent, no
  fallback, and installed totals equal to the raw Core list;
- anonymous callers are refused before any read;
- a version identity mismatch is refused before any command;
- four invalid argument sets are refused;
- after the disposable Marketplace entry is disabled, the result is
  `marketplace_not_loaded`, with one list command, no HACS fallback and
  `fallback_count` 0.

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
