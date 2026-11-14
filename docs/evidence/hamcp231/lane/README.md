# HAMCP-231: thread_matter_topology local exact-image lane

This directory holds the signing prerequisite evidence for
`core.thread_matter_topology_read`. It contains source review, a committed
local lane, and that lane's disposable receipt. It creates no registry
candidate, no owner signature and no production admission. Signing remains a
separate owner action.

**Scope of this evidence.** A1 (`thread/list_datasets`) is proven end to end
against exact Core. A2 (`matter/network_topology` with `refresh: false`) is
proven for its fail-closed case (Matter not set up gives `matter_not_loaded`)
and, against the HAMCP-297 Matter Server stub, for success and two upstream
faults. **The A1 and A2 receipts in this directory are the R8 re-runs on
the signing commit `d84d8d9e`; nothing has been signed** (sections "R8 run
on the signing commit" below; signing plan §5A.8 step 2). Preparation lists
their SHA-256 values in `review_artifacts_sha256` (signing plan §5A.8 step
4). Before R8, A2 success lane run 7 below (PASS, on source commit
`5fc40f93`, the HAMCP-364 branch after its merge with `release/2.6.0` at
`aeb3b314`) produced the committed `thread_matter_topology_a2.json`
(history). The run 7 receipt records, as
`lane_network`, the internal network the run created and inspected again
before it handed Core to the harness. Runs 2, 3, 4, 5 and 6 were earlier PASS
runs whose receipts it replaced.
The A2 lane uses the HAMCP-297 Matter Server stub. PR #428 merged that stub
into `release/2.6.0`, and run 7 uses the committed copy.

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

`ci/core-contract-read-files.json` holds **527** paths on this branch. After
the merge with `release/2.6.0` at `29339009` that is the release's 513 paths
(460, plus the 53 of the merged Marketplace profile, HAMCP-290) and the 14 that
only this profile adds. The file's bytes did not change in that merge. It only
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
`docs/evidence/hamcp315/core-sources/2026.10.3.json` now holds 455 hashes and
`2026.11.0.json` holds 526 (441 and 512 on the release). October still lacks
72 manifest paths and November still lacks only
`homeassistant/helpers/condition.py`. Historical hashes are
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

`ci/core-evidence-groups.json` adds
`"thread_matter_topology": ["core.thread_matter_topology_read"]` directly after
`marketplace_read` and before `security_metadata`, which the HAMCP-134 test
pins as the last group. The mapping
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

`thread_matter_topology.json` recorded this run until run 2 replaced it (the
run 1 receipt is in commit `df447a65`).

### Run 2: PASS (2026-11-11 08:49 CST)

Run 2 repeats the lane after the merge with `release/2.6.0` at `29339009`,
under the host heavy-job gate as unit `hamcp231-a1-lane2`.
`thread_matter_topology.json` recorded this run until run 3 replaced it (the
run 2 receipt is in commit `47ccef4c`):

- source commit `4087df22f2430ba82a06701a60366922fe06cf8c`;
- manifest SHA-256
  `913f8a3229fec3e85516183fd5fbfc2539ec90e3921b1d56419d545ab0f5bb7e` (527
  paths). Run 1 bound the manifest as it was before the catch-up merges;
- the same image, memory limits, profile fingerprint and 38 profile paths as
  run 1;
- receipt SHA-256
  `1d42b9a867aa79e420aa0ba7c77bc3b6d546d3834e3e14216a6d0ef523a13b9c`.

Apart from the source commit, the manifest hash, the times and the host
admission snapshot, every receipt field equals run 1: the authority
transition, the seeded datasets, the commands sent, the refusals, the eight
canaries with no leak, `fallback_count` 0 and clean cleanup.

### Run 3: PASS (2026-11-11 12:11 CST)

Run 3 repeats the lane after the merge with `release/2.6.0` at `ccaed9ef`
(HAMCP-329/298), under the host heavy-job gate as unit `hamcp231-a1-lane3`.
That merge changed `scripts/alarmo_inspection_contract_acceptance.py`, which
the acceptance script imports, the Engineering runtime package and the
manifest. `thread_matter_topology.json` recorded this run until run 4 replaced
it (the run 3 receipt is in commit `4d68e4d8`):

- source commit `69e1dfff48bcca26e3adeece90a6efddd60cefc0`;
- manifest SHA-256
  `48b8b87590a7c023f6ff9f44ff05abcab4fbb9aa3f597d57c2a131a149cfbbed`. The
  manifest keeps its 527 paths and gains the two created-object profile
  entries (38 profile entries, up from 36);
- the same image, memory limits and profile fingerprint as run 2;
- receipt SHA-256
  `71ef49469217c6fd775c49b762715868eefbc09061b51aa495f4eb75dd1bda09`.

Apart from the source commit, the manifest hash, the times and the host
admission snapshot, every receipt field equals run 2, including the authority
transition from 32 to 33 references.

### Run 4: PASS (2026-11-11 21:22 CST)

Run 4 repeats the lane on the HAMCP-364 branch, under the host heavy-job gate
as unit `hamcp364-a1-lane-b6f81dcf`. HAMCP-364 changed
`scripts/thread_matter_topology_contract_acceptance.py`: its raw WebSocket
client dials only the constant `RAW_CORE_URL`
(`ws://127.0.0.1:18124/api/websocket`), sends only the shapes listed in
`RAW_COMMANDS`, and opens its session through `scripts/lane_session.py`, which
refuses a redirect. `thread_matter_topology.json` recorded this run until run
5 replaced it (the run 4 receipt is in commit `5ea622ec`):

- source commit `b6f81dcf4c0edd9cb1819cf511a540194ab1c3e5`;
- the same manifest SHA-256 as run 3 (the manifest's bytes did not change);
- the same image, memory limits and profile fingerprint as run 3;
- receipt SHA-256
  `cc216e400529e785a5dc79b2a029acc5583134464834559255d5120e17058800`.

Apart from the source commit, the times and the host admission snapshot, every
receipt field equals run 3.

### Run 5: PASS (2026-11-12 03:32 CST)

Run 5 repeats the lane on the HAMCP-364 branch, under the host heavy-job gate
as unit `hamcp364-a1-lane-5fc40f93`. After run 4 the branch changed
`scripts/real_ha_contract_tests.py`, which the lane runner
`scripts/core_thread_matter_topology_local.py` imports: the config flow id
that harness accepts is now the 26-character ULID Core issues (commit
`4a72ee8a`). `scripts/thread_matter_topology_contract_acceptance.py` did not
change. `thread_matter_topology.json` recorded this run until the R8 re-run
below replaced it:

- source commit `5fc40f939756ee4ed5b004f88e53c00fd7d939c8`;
- the same manifest SHA-256, image, memory limits and profile fingerprint as
  run 4;
- receipt SHA-256
  `6bafb4b6eb49079435e9305f0e7f2a62a324b5579bcad69cc4a974561da2e2c2`.

Apart from the source commit, the times and the host admission snapshot, every
receipt field equals run 4.

### R8 run on the signing commit: PASS (2026-11-13 21:57 CST)

The committed `thread_matter_topology.json` is the R8 re-run on the signing commit
(signing plan §5A.8 step 2; architect ruling 2026-11-13, about 22:15 CST). It supersedes
run 5's receipt. Preparation lists its SHA-256 in
`review_artifacts_sha256` (signing plan §5A.8 step 4). Nothing has been
signed.

- user unit `hamcp332-a1-lane-d84d8d9e` (heavy-job gate, kind
  `lane`), 2026-11-13 21:57:02 to 21:57:13 Central (the receipt's
  `started_utc` and `ended_utc`);
- source commit `d84d8d9e69c4fff8481b018182a6b43f088aac7d`;
- result `PASS`, `cleanup.clean: true`;
- manifest SHA-256
  `073fa5ad6adc954940a5678d5b064795d2a45b2728e76998fc4b79309e8cd9a1`
  (`ci/core-contract-read-files.json`, 535 paths);
- the sequence 17 predecessor journal, SHA-256
  `3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d`;
- receipt SHA-256
  `6cfb52178627332beab4dc07a9ab34a641d12e93aec523a27fadb055cc2b7393`
  (3,019 bytes).

These runs cover A2 only for its fail-closed case. The A2 success lane below
covers success.

## A2 success lane

`scripts/core_thread_matter_topology_a2_local.py` and
`scripts/thread_matter_topology_a2_acceptance.py` are the A2 success lane. It
runs exact Core 2026.11.0
(`sha256:c6c1ca9931036bf3a2c4cc088d8db25dfea3b33935a5ecc83c40349462e2f69e`)
with Matter configured through the config flow to the HAMCP-297 Matter Server
stub at `ws://matter-stub:5580/ws`. The stub runs in the same Core image, as
`python3 -I`, from a read-only mount.

**Network.** The lane uses its own Docker bridge created with `--internal`,
and the lane refuses unless Docker reports `Internal: true`. No port is
published. The host reaches Core by its address on that bridge. Before
dispatch, an egress probe from the Core container must read `blocked`. Memory
is explicit: Core 2g, stub 256m, fixture 128m. Cleanup must read back as
clean: no containers, no network, no configuration.

**Stub pinning.** HAMCP-297 owns the stub, so this lane never copies or edits
it. `a2-stub-expectations.json` pins its owner (HAMCP-297, PR #428), its
commit `aa25c56498194c493b468db1faa842018e04acd2`, its committed directory
`tests/fixtures/hamcp297_matter_server_stub/` and the SHA-256 of all seven
files. `--stub-dir` accepts only two locations: that committed directory, or a
copy under `.artifacts/` extracted from the pinned commit with `git archive`.
The lane refuses a missing file, an extra file, a hash mismatch or a symlink.
A test checks that no file in `docs/`, `scripts/` or `tests/` has a pinned stub
hash.

The lane checks:

- the default read and `sections: ["topology"]` each read the topology once
  and return the same projection;
- every `get_network_topology` the stub recorded carries exactly
  `{"refresh": false}`, and the stub's own state agrees;
- after the mark, the only commands are `get_network_topology` plus Core's own
  background traffic;
- the projection:
  - status `ok`, not truncated, with totals equal to the raw reply;
  - unique `g<k>` references, and every kept edge resolved;
  - the ghost edge to `thread_FFFF00000000DEAD` dropped and counted;
  - Matter nodes resolved to their HA device ids, and non-HA kinds marked
    `not_an_ha_device`;
  - the Wi-Fi station's and AP's network names withheld;
  - the border router's untrusted `vendor_name` withheld;
- the kept network-level values (section 5.1.2): `network_name`, `rloc16` and
  `ext_pan_id` are kept, never made opaque. Hex values come out in the
  module's normalized form: lowercase, separators removed. Each is also
  checked against Core's raw reply;
- no canary leaks, and `fallback_count` is 0. 16 canaries are checked: 14
  stub literals (the network-key-shaped string, the extended addresses and
  graph ids, the BSSID in both forms, the SSID, the host name and the fault
  message) plus the lane's HA token and access secret;
- fault `topology_error` (Matter Server error_code 7): one dispatch, section
  `upstream_command_failed`, read `partial`;
- fault `server_info_schema_version: 12` with a Matter reload: Core refuses
  before calling the server, giving section `matter_server_schema_below_13`.

### Run 2: PASS (2026-11-10 02:14 CST)

`thread_matter_topology_a2.json` recorded this run until run 3 replaced it
(the run 2 receipt is in commit `bc07881e`):

- source commit `f1f824c91b1a93a556ebbffe57d821e0ae457a5b`;
- profile `core_thread_matter_topology_read_v1`, fingerprint
  `sha256:d03f005cfc0dbaaa03aad3e78d9147f83321bb3dec8015aa69edb93e358ebb6c`;
- stub at `extracted_pinned_commit`, with 7 files verified;
- egress probe `blocked`.

Results:

- 6 nodes, 4 kept connections, 1 dropped (the ghost), and 3 Matter nodes
  resolved (61, 91, 199);
- the stub recorded 4 `get_network_topology` commands, all with
  `refresh: false`. One was the harness's raw baseline read and three were
  Engineering's. Under the schema-12 fault, Core forwarded nothing;
- `topology_error` gave `upstream_command_failed`, and schema 12 gave
  `matter_server_schema_below_13`;
- 0 canary leaks, `fallback_count` 0, and cleanup clean.

Run 1 (2026-11-10 02:11 CST) was withheld. The acceptance compared
`ext_pan_id` with the stub's upper-case form, but the module emits it
lowercased by design. The fix compares in normalized form. Run 1 also led to a
module fix: the stub's diagnostic mesh ids (`thread_…`, `meshrloc_…`) are now
screened as graph ids on nodes and edge endpoints.

### Run 3: PASS (2026-11-10 17:16 CST)

Run 3 repeats the lane on the catch-up merge with `release/2.6.0`
(`fcd6973f`), under the host heavy-job gate as unit `hamcp231-a2-lane3b`.
`thread_matter_topology_a2.json` recorded this run until run 4 replaced it
(the run 3 receipt is in commit `df447a65`):

- source commit `6339dbe7aebe7fcb4f2b11cbdd262ed348abda52`;
- the same profile and fingerprint as run 2;
- manifest SHA-256
  `913f8a3229fec3e85516183fd5fbfc2539ec90e3921b1d56419d545ab0f5bb7e` (527
  paths);
- stub at `committed`, with 7 files verified: the release copy in
  `tests/fixtures/hamcp297_matter_server_stub/`, which matches the pinned
  hashes of commit `aa25c56498194c493b468db1faa842018e04acd2`;
- egress probe `blocked`;
- receipt SHA-256
  `dd23cca6e9d357e4660846a2910ee35a30fa6909d4d7f7c7d5d0fac59fedd8e0`.

The results equal run 2: 6 nodes, 4 kept connections, 1 dropped, 3 Matter
nodes resolved, 4 recorded topology commands all with `refresh: false`, both
faults as above, 0 of 16 canaries leaked, `fallback_count` 0 and cleanup clean.

The first launch, one minute earlier as unit `hamcp231-a2-lane3`, was refused
before any container started (`a2_stub_file_set_mismatch`). A local test run
had left an ignored `__pycache__` directory in the stub directory, and the lane
refuses any extra file there. Removing it was the only change.

The run 3 receipt's `stub_dependency` label still read "draft PR #428". That
text came from the lane script, which run 3 left unchanged; the PR had merged.

### Run 4: PASS (2026-11-11 08:48 CST)

Run 4 repeats the lane after the merge with `release/2.6.0` at `29339009`,
under the host heavy-job gate as unit `hamcp231-a2-lane4`. That merge changed
`scripts/lane_failure_diagnostics.py`, which the lane imports.
`thread_matter_topology_a2.json` recorded this run until run 5 replaced it
(the run 4 receipt is in commit `47ccef4c`):

- source commit `4087df22f2430ba82a06701a60366922fe06cf8c`;
- the same profile, fingerprint and manifest SHA-256 as run 3 (the manifest's
  bytes did not change);
- stub at `committed`, with the same 7 files verified;
- `stub_dependency` now reads "HAMCP-297 PR #428 at
  `aa25c56498194c493b468db1faa842018e04acd2`". The label, two docstrings, the
  `--stub-dir` help text and the note in `a2-stub-expectations.json` were
  corrected in the source commit, so `expectations_sha256` changed with the
  note. No pin, check or refusal changed;
- egress probe `blocked`;
- receipt SHA-256
  `a84433b9382d4f06d183e4fee8c8d1274f4768f0b6e599cb6d54e8273283febf`.

Apart from the source commit, `expectations_sha256`, the label, the times and
the host admission snapshot, every receipt field equals run 3: the topology,
the recorded stub commands, both faults, 0 of 16 canaries leaked,
`fallback_count` 0 and clean cleanup.

### Run 5: PASS (2026-11-11 12:10 CST)

Run 5 repeats the lane after the merge with `release/2.6.0` at `ccaed9ef`
(HAMCP-329/298), under the host heavy-job gate as unit `hamcp231-a2-lane5`.
That merge changed `scripts/alarmo_inspection_contract_acceptance.py`, which
the acceptance script imports, the Engineering runtime package and the
manifest. `thread_matter_topology_a2.json` recorded this run until run 6
replaced it (the run 5 receipt is in commit `4d68e4d8`):

- source commit `69e1dfff48bcca26e3adeece90a6efddd60cefc0`;
- manifest SHA-256
  `48b8b87590a7c023f6ff9f44ff05abcab4fbb9aa3f597d57c2a131a149cfbbed` (527
  paths, 38 profile entries);
- the same profile and fingerprint as run 4;
- stub at `committed`, with the same 7 files verified;
- egress probe `blocked`;
- receipt SHA-256
  `45023964921e876b9edbfd578da17c4b70593eca45adfeacf9ac5e80fa50605b`.

Apart from the source commit, the manifest hash, the times and the host
admission snapshot, every receipt field equals run 4.

### Run 6: PASS (2026-11-11 21:24 CST)

Run 6 repeats the lane on the HAMCP-364 branch, under the host heavy-job gate
as unit `hamcp364-a2-lane-b6f81dcf-r2`. HAMCP-364 changed the lane script and
`scripts/thread_matter_topology_a2_acceptance.py`. Before the harness learns
Core's address, the runner inspects the network again by the id
`docker network create` printed in this run (`hand_over_core`), and refuses
unless Docker still reports it under the lane's name and this run's label as
an internal bridge with one subnet. The harness accepts only a bare
`http://<IPv4>:8123` origin inside that subnet (`use_internal_lane_core`) and
holds every later request to it. The harness socket sends only the two shapes
in `A2_RAW_COMMANDS`, and every session is opened through
`scripts/lane_session.py`, which refuses a redirect.
`thread_matter_topology_a2.json` recorded this run until run 7 replaced it
(the run 6 receipt is in commit `5ea622ec`):

- source commit `b6f81dcf4c0edd9cb1819cf511a540194ab1c3e5`;
- the same profile, fingerprint and manifest SHA-256 as run 5;
- stub at `committed`, with the same 7 files verified;
- egress probe `blocked`;
- a new `lane_network` block: name
  `beta-rc2-contract-ha-2026-11-0-thread-matter-a2`, subnet `172.18.0.0/16`,
  target address `172.18.0.3` (Core's address on that network);
- receipt SHA-256
  `7bd5993acd2a5403b5e9ca224f6a81047c9eda43010ad6fa98c5cb1ac822920e`.

Apart from `lane_network`, the source commit, the times and the host admission
snapshot, every receipt field equals run 5.

The first launch, one minute earlier as unit `hamcp364-a2-lane-b6f81dcf`, was
refused before any container started (`a2_stub_file_set_mismatch`), for the
cause recorded under run 3: a local test run had left an ignored `__pycache__`
directory in the stub directory. Removing it was the only change.

### Run 7: PASS (2026-11-12 03:32 CST)

Run 7 repeats the lane on the HAMCP-364 branch, under the host heavy-job gate
as unit `hamcp364-a2-lane-5fc40f93`. After run 6 the branch changed
`scripts/real_ha_contract_tests.py`, which the lane runner
`scripts/core_thread_matter_topology_a2_local.py` imports: the config flow id
that harness accepts is now the 26-character ULID Core issues (commit
`4a72ee8a`). Neither the lane script nor
`scripts/thread_matter_topology_a2_acceptance.py` changed.
`thread_matter_topology_a2.json` recorded this run until the R8 re-run below
replaced it:

- source commit `5fc40f939756ee4ed5b004f88e53c00fd7d939c8`;
- the same profile, fingerprint and manifest SHA-256 as run 6;
- stub at `committed`, with the same 7 files verified;
- egress probe `blocked`;
- the same `lane_network` values as run 6;
- receipt SHA-256
  `1b4506263090a3f56cba85841ce64cee66e1c323f51729364cadd3868f3826da`.

Apart from the source commit, the times and the host admission snapshot, every
receipt field equals run 6.

### R8 run on the signing commit: PASS (2026-11-13 21:57 CST)

The committed `thread_matter_topology_a2.json` is the R8 re-run on the signing commit
(signing plan §5A.8 step 2; architect ruling 2026-11-13, about 22:15 CST). It supersedes
run 7's receipt. Preparation lists its SHA-256 in
`review_artifacts_sha256` (signing plan §5A.8 step 4). Nothing has been
signed.

- user unit `hamcp332-a2-lane-d84d8d9e` (heavy-job gate, kind
  `lane`), 2026-11-13 21:57:25 to 21:57:45 Central (the receipt's
  `started_utc` and `ended_utc`);
- source commit `d84d8d9e69c4fff8481b018182a6b43f088aac7d`;
- result `PASS`, `cleanup.clean: true`;
- manifest SHA-256
  `073fa5ad6adc954940a5678d5b064795d2a45b2728e76998fc4b79309e8cd9a1`
  (`ci/core-contract-read-files.json`, 535 paths);
- the sequence 17 predecessor journal, SHA-256
  `3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d`;
- receipt SHA-256
  `f36d8c1a82e657de1dd82373eace0372f3ae31c2a1ee7e6fc50330f92913f798`
  (4,772 bytes).

The lane was launched once. Before the first lane of this batch was launched,
the ignored `__pycache__` directory that a local test run had again left in
the stub directory (the cause of the refused launches recorded under runs 3
and 6) was moved out of it. No tracked file changed.

**Residuals.**

- The topology is static and synthetic: the stub serves one fixed graph, not
  a real Thread or Wi-Fi mesh.
- The stub applies writes to its own cache. The only writes it saw were Core's
  own `set_default_fabric_label` and `start_listening` during Matter setup and
  reload. Engineering sent none.
- The stub is the committed release copy, merged by PR #428. A later change
  to any of its seven files makes the lane refuse until the pins are reviewed.

Offline tests are in `tests/test_core_thread_matter_topology_a2_local.py`.
They use canary values generated at test time, never the stub's literals, and
include an upper-case `ext_pan_id`.
