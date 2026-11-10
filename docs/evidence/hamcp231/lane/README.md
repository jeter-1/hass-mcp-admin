# HAMCP-231: thread_matter_topology local exact-image lane

This directory holds the signing prerequisite evidence for
`core.thread_matter_topology_read`. It contains source review, a committed
local lane, and that lane's disposable receipt. It creates no registry
candidate, no owner signature and no production admission. Signing remains a
separate owner action.

**Scope of this evidence.** A1 (`thread/list_datasets`) is proven end to end
against exact Core. A2 (`matter/network_topology` with `refresh: false`) is
proven only for its fail-closed case: with Matter not set up, Core answers
`unknown_command` and the read reports `matter_not_loaded`. The A2 success lane
needs the HAMCP-297 Matter Server stub and has not run. **A2 signing evidence is
incomplete until the A2 success lane runs.**

## Exact authority and scope

The target Core is **2026.11.0**:

- source commit `74cf5a49c63ed8d4d87717844bf14f79c28c9cea`;
- archive SHA-256 `85f3c42c9f77aa45e01faff4471007beadf5cb50c4324f496a239139eec70ff1`;
- image pinned by the existing `scripts/core_lane_bindings.py` binding.

No binding change is needed.

The predecessor journal is signed sequence 17. Its bytes must hash to
`3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d`. The lane
verifies it with the committed public key. It then requires exactly one
2026.11.0 entry with 32 references and no `core.thread_matter_topology_read`.

Disposable authority is signed only by an in-memory ephemeral test key. The two
test authorities carry the 32 predecessor references, and then those same
references plus the compiled profile reference.

The lane proves exactly one profile reference:

- profile `core_thread_matter_topology_read_v1`, adapter
  `compiled-core-thread-matter-topology-read-v1`;
- contract fingerprint
  `sha256:d03f005cfc0dbaaa03aad3e78d9147f83321bb3dec8015aa69edb93e358ebb6c`.

Before any container starts, `scripts/core_thread_matter_topology_local.py`
compares the compiled reference with that pinned fingerprint. The receipt binds
both, and the run is withheld unless the acceptance result reports the same
reference. A changed profile must be re-proven.

## Source manifest

`ci/core-contract-read-files.json` grows from 509 to **523** paths. It only
expands; every prior profile mapping is retained.

The new `core.thread_matter_topology_read` profile maps **38** files:

- the `thread` integration files that register and answer `thread/list_datasets`
  and own the dataset store (`__init__`, `config_flow`, `const`, `dataset_store`,
  `discovery`, `manifest.json`, `websocket_api`);
- the `matter` files on the `matter/network_topology` path (`__init__`, `adapter`,
  `api`, `const`, `helpers`, `manifest.json`);
- the same 15 `websocket_api` files as the Marketplace profile;
- `bootstrap.py`, `config_entries.py`, `const.py`, `core.py`, `data_entry_flow.py`,
  `loader.py`, `helpers/json.py`, `helpers/storage.py`,
  `helpers/device_registry.py` and `util/ulid.py`.

`core-thread-matter-source-hashes.json` lists those paths in profile order, with
their SHA-256 values from the exact archive. Before any container starts, the
lane reports one of `thread_matter_manifest_missing`,
`thread_matter_source_provenance_mismatch`, `thread_matter_manifest_mismatch`
or `thread_matter_source_hash_mismatch`.

The O3 union records were regenerated from the same exact archives:
`docs/evidence/hamcp315/core-sources/2026.10.3.json` now holds 451 hashes and
`2026.11.0.json` holds 522. October still lacks 72 manifest paths and November
still lacks only `homeassistant/helpers/condition.py`. Historical hashes are
unchanged.

`generate_source_records.py` in this directory reuses the HAMCP-290 generator
unchanged and adds only the topology record. `--check` compares the committed
bytes:

```
.artifacts/venv/bin/python -I docs/evidence/hamcp231/lane/generate_source_records.py \
  --archive 2026.10.3=.artifacts/inputs/core-2026.10.3.tar.gz \
  --archive 2026.11.0=.artifacts/inputs/core-2026.11.0.tar.gz [--check]
```

## Group mapping and CI

`ci/core-evidence-groups.json` appends
`"thread_matter_topology": ["core.thread_matter_topology_read"]`. The mapping
never adds a profile to inherited authority, because `passing_subset` still
intersects with the authenticated predecessor. Scheduled CI does not run this
local lane, so a missing group receipt stays withheld. No workflow is changed.

## Lane

`scripts/core_thread_matter_topology_local.py` runs the cached exact Core image
with an explicit `--memory`, the host port bound to loopback **18124** (distinct
from the HAMCP-290 lane), and a configuration that loads `thread:` but never
sets up Matter. It refuses unless the running Core reports `thread` loaded and
`matter` not loaded. There is no ha-mcp container and no Matter Server.

The bridge is non-internal only because Docker cannot publish a port from an
internal network. The capability itself needs no egress.

`scripts/thread_matter_topology_contract_acceptance.py` uses the real
`ThreadMatterTopologyService`, `TopologyReader` and `CoreRuntime`.

Harness-only setup, on the disposable Core and never through Engineering:

- it waits for `thread/list_datasets` to register and requires an empty store;
- it seeds two synthetic datasets with `thread/add_dataset_tlv`. Each TLV is
  built at run time with a random extended PAN ID, PAN ID, network key and
  PSKc. These are canaries, not credentials, and never leave memory;
- the second dataset's free-text `source` carries its own network key in hex;
- it confirms that raw `matter/network_topology` with `refresh: false` answers
  `unknown_command`.

Checks through route admission:

- the predecessor authority is unavailable, with zero reads;
- with the extended authority, the default read sends exactly
  `thread/list_datasets` then `matter/network_topology`, admitted and sent in that
  order;
- both datasets match the raw store: dataset id, extended PAN ID, PAN ID,
  channel and network name. PAN IDs kept by design (§5.1.2);
- the secret-bearing `source` is withheld (`None`, one withheld field), and no
  network key, PSKc, TLV, HA token or access secret appears in any result, in
  either case;
- the topology section is `matter_not_loaded`, completeness is `partial` with
  `section_unavailable` and `sanitized_text`;
- `sections: ["datasets"]` and `sections: ["topology"]` each send only their
  own command;
- the raw store is unchanged after the reads;
- anonymous callers are refused before any read, a Core identity mismatch is
  refused before any command, and seven invalid argument sets (including any
  `refresh`) are refused;
- no never-sent command (including `thread/add_dataset_tlv`) is sent by
  Engineering, and `fallback_count` is 0.

The lane is local-only. It refuses unless the checkout is clean and committed,
the run directory is new and under `.artifacts/`, and the container names and
port are free. Failure is WITHHELD. Cleanup must read back absence.

```
CORE_EVIDENCE_HOST_MODE=local .artifacts/venv/bin/python scripts/core_thread_matter_topology_local.py \
  --mode local --pull never --core-memory 2g --fixture-memory 128m \
  --journal <seq17-journal.json> --source-archive <core-2026.11.0.tar.gz> \
  --run-dir .artifacts/<new-run-dir>
```

Offline tests are in `tests/test_core_thread_matter_topology_local.py`.

## Receipt

`thread_matter_topology.json` is the receipt of the local run, copied
byte-for-byte from the run directory. It records:

- the source commit, journal hash, profile reference and fingerprint;
- the source-hash digests and admission;
- container memory, running image identity and cleanup.

It is disposable evidence, not production authority.

### Run 1: PASS (2026-11-09 23:30 CST)

- The lane ran under the host heavy-job gate as unit `hamcp231-lane` (kind
  `lane`) at source commit `d7b7f9366c33bcf1a151fb32c276225468b62640`.
- The Core container ran with `--memory 2g` and the fixture writer with
  `128m`. The image was
  `ghcr.io/home-assistant/home-assistant:2026.11.0@sha256:c6c1ca9931036bf3a2c4cc088d8db25dfea3b33935a5ecc83c40349462e2f69e`.
- The receipt's SHA-256 is
  `6c976d24bcd44a111306c5da797d6c0b5606c4e8e426b4f72c0182765aacc15d`. It binds
  `contract_fingerprint`
  `sha256:d03f005cfc0dbaaa03aad3e78d9147f83321bb3dec8015aa69edb93e358ebb6c`
  and 38 profile paths.
- Authority went from 32 to 33 references. The predecessor made zero reads and
  was refused with `authority_unavailable`.
- Two synthetic datasets were seeded. Raw `matter/network_topology` with
  `refresh: false` answered `unknown_command`.
- Datasets read:
  - The routed default read sent exactly `thread/list_datasets` then
    `matter/network_topology`.
  - Both datasets were `ok`, with one withheld identifier field (the
    secret-bearing `source`).
  - PAN IDs kept by design (§5.1.2).
- Topology and completeness:
  - Topology was `matter_not_loaded`.
  - Completeness was `partial`, with `section_unavailable` and
    `sanitized_text`.
  - Across the two routed reads, Engineering sent
    `[list_datasets, network_topology, list_datasets, network_topology]`
    (the default read, then the datasets-only and topology-only reads).
- Leak check: eight canaries were checked and none leaked.
- Refusals:
  - anonymous gave `authority_unavailable`;
  - wrong Core version gave `identity_mismatch`;
  - seven invalid argument sets were refused.
- `fallback_count` was 0, and cleanup read back as clean.

Limitation: this run does not prove A2 success. The Matter Server stub
(HAMCP-297) is required for that, and **A2 signing evidence is incomplete until
the A2 success lane runs.**

## A2 success lane (harness built; run pending HAMCP-297)

`scripts/core_thread_matter_topology_a2_local.py` and
`scripts/thread_matter_topology_a2_acceptance.py` are the A2 success lane. It
uses loopback port **18125** and its own container and network names. It runs
exact Core 2026.11.0 with Matter configured through the config flow to the
HAMCP-297 Matter Server stub at `ws://matter-stub:5580/ws`. The stub runs in the
same Core image, read-only, with no published port.

The stub is consumed by committed path and SHA-256 only. This lane never copies
or edits it. `a2-stub-expectations.json` records the stub path and hash, the
node-fixture path and hash, and the canary literals. Its field names come from
matter-python-client 1.5.5's exact-version source, as HAMCP-297 reports them.
Its status is `pending_hamcp297`, so the lane refuses `a2_stub_pending` before
any input, image or container is touched.

Once ready, the lane checks:

- with the extended authority (32 predecessor references plus the compiled
  profile), the default read and `sections: ["topology"]` each read the
  topology once and return the same projection. Predecessor refusal is
  already proven by the A1 lane;
- the stub's command record shows that every `get_network_topology` carried
  exactly `{"refresh": false}`. The recorded refresh must be the literal
  `false`; `true`, `0`, `null`, a missing key or an extra argument fails;
- after the mark, Engineering sends only `get_network_topology`, never a
  commissioning, removal, ping or interview command;
- the projection: status `ok`, not truncated, totals equal to the raw reply,
  unique `g<k>` references, every edge resolved, at least one ghost edge
  dropped and counted, and the required kinds present;
- each Matter node's `ha_device_id` equals the device-registry id derived from
  `deviceid_<fabric>-<node>-MatterNodeDevice`. Non-HA kinds are
  `not_an_ha_device`, and the Wi-Fi AP name is withheld;
- `rloc16` and the network type match the raw reply;
- no canary leaks (extended address, BSSID, SSID, host name, key), and
  `fallback_count` is 0.

Offline tests are in `tests/test_core_thread_matter_topology_a2_local.py`.
They use canary values generated at test time, never the stub's literals.

**A2 signing evidence is incomplete until the A2 success lane runs.**
