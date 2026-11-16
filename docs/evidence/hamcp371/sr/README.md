# HAMCP-371 SR: harness, local Engineering image, 2.2.1 tree, catalog admission, Core seed, P3 and SR-b

Ruling: architect ruling 2026-11-15 19:05 CST (SR GO, part SR-a), under the SR
contract of 2026-11-15 17:50 CST. SR-a prepares the replay; it calls no read.
SR-b (the replay of the 27 admitted reads) ran twice: launch 1 failed after
start (section 7); launch 2 passed (section 11).

The architect accepted SR-a as reported (ruling 2026-11-15 20:17 CST) and
recorded two known differences from CI:

- **Build context.** SR builds from the committed tree, streamed as a tar
  (section 3). CI builds from its working directory.
- **Token.** SR gives Engineering `HA_TOKEN` only and refuses
  `SUPERVISOR_TOKEN` (section 2). This is a lane-versus-production
  difference, alongside N29(a): production Engineering runs as an add-on with
  a Supervisor token.

## 1. The 2.2.1 component tree

`check_component_tree.py` rebuilds the listing of
`custom_components/ha_mcp_tools` at ha-mcp commit
`fc54437a804858732e4bc927add98e202d879a09` (component 2.2.1). It uses git
plumbing only, with lazy fetch off.

| Store | Listing sha256 | Footer |
|---|---|---|
| `~/repos/ha-mcp` | `26524feadbbec8e5815bcd54c32a3b5d01d85896f889e8ca9ddc754e1f70bdf0` | `FILES 54 BYTES 3228447 MISSING 0 SHA1_MISMATCH 0` |
| cursor-research-2026-11-11-ij ha-mcp clone | `26524feadbbec8e5815bcd54c32a3b5d01d85896f889e8ca9ddc754e1f70bdf0` | the same |

Both rebuilt listings equal `../s7/component-2.2.1-tree.txt` byte for byte
(sha256 `26524fea…70bdf0`). Result: **PASS**: the tree is complete and every
blob matches its id.

## 2. The SR harness

- `scripts/hamcp371_live_replay_local.py` has two commands:
  - `build` makes the local Engineering image (section 3).
  - `run` runs SR-b: the S5 lane with Profile B, the D1 flags, disposable
    Core 2026.11.0 and the live socket. It runs two setups, 9.2.0 with
    component 9.2.0 and 8.5.0 with component 2.2.1. Engineering runs as a
    container from the recorded local image in front of a recording proxy
    and the ha-mcp peer.
- `scripts/hamcp371_live_replay_holder.py` runs inside the holder namespace.
  - `proxy` passes each exchange through unchanged. It stores the request,
    the raw response and a sha256 of the response, with authorization,
    cookie and proxy headers withheld.
  - `reads` opens an Engineering session and makes the 27 calls. Each call
    gets a receipt with the call, the upstream response sha256, the result
    class and the withheld keys and counts.
  - r12 is `ha_get_blueprint` in get mode on the default Core blueprint
    `homeassistant/motion_light.yaml`. If r12 does not succeed, the run stops.
  - r27 must come back as "not replayable in the local lane (no Supervisor)",
    and confirmed by its own response with no upstream success.
- Engineering's posture:
  - It runs with `--pull never` and a read-only root.
  - Capabilities are dropped, `no-new-privileges` is set, memory is 384m,
    and the pids limit is 256.
  - It gets `HA_TOKEN` only. The harness refuses any `SUPERVISOR_TOKEN`.
    CI gives Engineering both variables; SR gives only `HA_TOKEN` on purpose.
  - The image record is checked before any container is created: same tree,
    same image Id, same revision label.
- These interpretations are written into every SR-b receipt
  (`INTERPRETATIONS` in the lane script):
  - "Host network" means the holder's loopback-only namespace. Docker refuses
    `--add-host` with a container network, and the host's own network would
    give Core a route out.
  - The upstream URL Engineering receives names the recording proxy.
  - The 8.5.0 control has no `ha_mcp` package. Its three requirements come
    from the committed S5 inventory-3 rows.
  - "Withheld" counts every `*_withheld`, `*_dropped_by_policy` and
    `*_replaced` counter Engineering reports.
  - Whether the 8.5.0 catalog advertises `ha_config_get_yaml` is recorded,
    not required.
  - Whether Engineering admits each peer catalog under the D1 flags is shown
    by the result class of each read.

### Tests and mutation checks

- `tests/test_hamcp371_live_replay_local.py` had 40 offline tests at SR-a (47 after D6, section 6). They use
  loopback synthetic peers and a temporary git repository, with no Docker
  and no Core. Result: 40 OK.
- The SR module and the lane modules it reuses ran together (`core_read_replay_capture_local`,
  `upstream_catalog_capture_local`, `core_read_replay_calls`,
  `core_read_replay_seed`) in the worktree venv. Result: 221 OK.
- Fast tier, log `.artifacts/sr/fast/fast.txt` in the worktree: **passed**,
  191 tests.
  - Targets: the SR module plus `test_core_read_replay_capture_local` and
    `test_upstream_catalog_capture_local`.
  - Launch 1 refused in preflight: the 32 HAMCP-371 Engineering runtime paths
    already on the branch were not declared. No code ran.
  - Relaunch 1 used the same 32-path `-AuthorizedProtectedPath` declaration as
    the S13b Fast run. No protected path changed in SR-a.
- Mutation checks: `mutation-checks.txt`. 4 mutants, 4 killed: the r12 stop,
  the SUPERVISOR_TOKEN guard, authorization redaction and the r27
  confirmation.

## 3. The local Engineering image

`engineering-image.json` is the build record. The build follows
`ci.yml:1105-1146`:

- Dockerfile and context: `hass_mcp_engineering_beta/`.
- Build arguments: `BUILD_VERSION` comes from `config.yaml` with CI's regex.
  The others are `HAMCP_BUILD_SHA`, `HAMCP_BUILD_TIME` and
  `HAMCP_BUILD_DIRTY=false`.
- Command: `DOCKER_BUILDKIT=1 docker build`.

It differs from CI in one way: the context is the committed tree, streamed as
one deterministic tar (mtime 0, uid 0, modes from git, each blob checked
against its id). It is not the working directory. Docker applies the tree's
own `.dockerignore`.

| Field | Value |
|---|---|
| Tag | `hamcp371-sr-engineering:8b3cadfa06f5` (local only, never pushed) |
| Image Id | `sha256:61d897836200184ce59db4a2941e3d9c88f483a911c523b97506c82f42c6e655` |
| Revision | `8b3cadfa06f59c887e7e570d1cbeb7f91044c35c` |
| Tree (`hass_mcp_engineering_beta`) | `ec11f8784a9d4982ba11971b8b67047b941cc528` |
| Build-context sha256 | `45c511cd86a24873202842d0a147cd9babcae848a0a42195a9895cb68c0c4191` (412 files, 12,953,600 bytes) |
| Context listing sha256 | `195dc92044b47f6ae22db8116dddaf1c7f5f4233604b1b4fe2915fd081aaa73a` |
| Version label | `2.4.2-beta.8` (from the branch `config.yaml`, as on main; 2.4.2-beta.9 is set only at S16, ruling 2026-11-14 16:15; corrected per the ruling of 2026-11-15 20:17 CST) |
| Size | 316,347,886 bytes |

- The build ran as unit `hamcp371-sr-build-1` (MemoryMax=3G,
  RuntimeMaxSec=2400) from 20:09:33 to 20:10:06 CST on 2026-11-15, and
  succeeded in 31.7 s.
- Heavy gate at launch: 12,701 MB available, load 0.13, 60 GB root free,
  no other heavy job and no containers.
- Afterwards the build-context sha256 was recomputed from commit `8b3cadfa`.
  It is identical, and `engineering_image()` verifies the record against the
  local image.
- The `RepoDigests` entry is the local image store's own name for the Id. No
  registry push was made.

## 4. Not run

- SR-b: the replay of the reads, both setups. At first it was not started,
  because P1 found an admission blocker (section 5) and the ruling of
  2026-11-15 20:17 CST makes SR-b conditional on 9.2.0 being admitted exact.
  After D6 the P1 re-run passed (section 6). SR-b launch 1 then failed after
  start (section 7) and was not re-run.
- Full and Evidence tiers: Full ran at S14 and failed after start; Evidence
  was not launched (section 13).
- CI: there was no push.
- S15 and later.

## 5. P1: offline startup catalog admission (ruling 2026-11-15 20:17 CST)

`p1_catalog_admission.py` replays each saved D1-flag `tools/list` from S6
profile B, verbatim and in wire order, through the test suite's loopback peer.
It checks each file's sha256, its `serverInfo` and its single page first.
Engineering's own `UpstreamReadGateway.initialize()` then admits the catalog
against the built-in reviewed registry of head `291b2cb6` (registry sha256
`f3bff693…2039019`, signed registry off). It uses no Home Assistant, no Docker
and no network beyond 127.0.0.1, and it ran no heavy job. Output:
`p1-catalog-admission.json`, sha256
`5f828a7abed49e88782ad45585c557ba5b8f56031ad1fdafceb65754fa39a055`. The script
exited 1.

| Field | 9.2.0 (`run-b-9.2.0-2`, `c707f5f7…`) | 9.1.2 (`run-b-9.1.2-1`, `2b824bbc…`) |
|---|---|---|
| admission_status | **partially_admitted** | **partially_admitted** |
| version_status | reviewed_exact | reviewed_exact |
| upstream_advertised_tool_count | 80 | 80 |
| reviewed_policy_entry_count | 77 | 77 |
| catalog_comparison_status | partial | partial |
| exposed tools | 27 (no `ha_search`) | 27 (no `ha_search`) |
| peer tool calls | 0 | 0 |

**P2 result: admission blocker.** 9.2.0 is not admitted exact under the D1
flags. SR-b did not start, and no policy or registry row changed.

### Diagnosis

The diagnosis comes from a read-only inspection of the same health output and
of ha-mcp source. It is the same for both versions:

- One automatic read is quarantined: `ha_search`, reason
  `input_schema_mismatch`. The observed schema has no `search_types`,
  `include_config` or `config_time_budget`, and its tool and `query`
  descriptions differ. All three parameters are in the reviewed catalog.
- Reconciliation is degraded. `ha_get_operation_status` is held.
- The saved catalogs also advertise `ha_config_get_yaml`, `ha_list_files` and
  `ha_read_file`, which are not in the reviewed catalog. `ha_config_set_helper`
  (persistent_write) differs only in one description.
- **Cause.** The cause is in ha-mcp source, not in a D1 flag. At ha-mcp
  9.2.0 (`51e3aac7`), `src/ha_mcp/transforms/component_search.py`
  (`ComponentSearchSchemaTransform`, registered unconditionally at
  `src/ha_mcp/server.py:221-223`) rewrites the advertised `ha_search`. It
  removes exactly those three parameters and replaces both descriptions, but
  only when the connected component reports the capabilities `search`,
  `device_registry_child_semantics` and `search_unified`. The S6 profile B
  component reported all three, for 9.2.0 and for 9.1.2
  (`out/profile-b/component-after-setup.json`). The reviewed `ha_search`
  entry carries the legacy schema. That is what the transform leaves in
  place when those capabilities are absent (an inference from the source;
  the reviewed capture's component state was not rechecked here). The 9.1.2
  source was not checked.
- **Effect on SR-b.** `r25-search` (`ha_search`) is one of the SR reads, so it
  would not be admitted in the 9.2.0 setup.

Not checked: whether the 8.5.0 control (component 2.2.1) passes its
admitted_exact gate under the D1 flags.

## 6. D6: the 9.2.0 ha_search pair, the P1 re-run and the SR health gate

Rulings: architect rulings 2026-11-15 22:26 CST (D6 accepted, R-D6-1 to
R-D6-3). They replace the refusal of `config_time_budget` proposed in
`D6-search-variant-design.md`: the budget is forwarded unchanged with a warning.

### The pair (commit `7338723e`)

- `providers/upstream_reads_9_2.py` holds exactly two reviewed `ha_search`
  descriptors for 9.2.0:
  - `legacy` is the reviewed registry descriptor;
  - `unified` is the descriptor ha-mcp 9.2.0 advertised in S6 profile B.
- The pair is bound to version 9.2.0, source `51e3aac7`, the policy sha256
  and protocol `2025-03-26`. It is chained after the 8.6 search checks at the
  same call sites, so 8.6.0 and its tests are unchanged. No runtime file was
  added.
- `d6_search_pair.py` derives every fingerprint from committed files, using
  Engineering's own functions. Its output is `d6-search-pair-9.2.0.json`
  (sha256 `2481ce28…63af71`, pair sha256 `25fcab57…53838`).
  - `legacy` reproduces the registry row: its contract, catalog fingerprint
    and strict full-contract fingerprint.
  - `unified` comes from receipt `c707f5f7…`. It differs from `legacy` only in
    `inputSchema` (`config_time_budget`, `include_config` and `search_types`
    removed, the `query` description replaced) and `description`.
  - Correction: the unified description fingerprint is `462a9d45…`, computed
    by the script. An earlier hand computation, `4fbce174…`, was wrong.
- Arguments, R-D6-1 and R-D6-3:
  - Engineering forwards all arguments unchanged under both variants,
    including `search_types`, `include_config` and `config_time_budget`.
  - Under `unified`, a caller-supplied `config_time_budget` adds the fixed
    warning `search_budget_legacy_path_only`. There is no refusal. The warning
    appears only under `unified` and only when the caller supplied the budget.
  - The public schema is unchanged.
- A call-time flip keeps the 8.6 behaviour, as ruled:
  - the route pins its variant at discovery;
  - a call whose observed descriptor no longer matches is refused before
    dispatch, and `ha_search` is quarantined;
  - the old route keeps refusing after a re-initialize, and the next
    discovery exposes the new variant.
- Attribution. Each `ha_search` result carries
  `metadata.search_descriptor_variant`. It names the descriptor variant that
  was pinned and matched. It does not prove which upstream code path ran.
- The `policy_uncompiled_search_adapter` guard
  (`upstream_tool_policy.py:1377-1392`) needed no chaining. It refuses a
  policy entry that carries the 8.6 search adapter token outside 8.6.0. The
  9.2.0 pair is binary-owned and carries no policy token.
- 9.1.2 is still `partially_admitted`, with `ha_search` quarantined
  (`input_schema_mismatch`). It is outside this ruling.

### Tests

| Check | Result |
|---|---|
| `tests/test_hamcp371_search_pair.py` | 18 OK |
| Regression run in the worktree venv | 611 OK, 2 skipped |
| Fast tier, `.artifacts/d6impl/fast/fast.txt` (32-path declaration) | passed, 197 tests |
| Mutation checks, `d6-mutation-checks.txt` | 6 mutants, 6 killed |

The 18 tests cover:

- both variants admitted;
- a third descriptor refused and quarantined;
- a flip;
- the budget forwarded, with the warning only under `unified`;
- `search_types` and `include_config` unchanged;
- the 8.6 pair unchanged.

The mutants cover the variant gate, the warning condition, attribution and
the trust binding.

### P1 re-run

`p1_catalog_admission.py` now gates on the D6 re-run criteria. It ran at head
`7338723e` against registry sha256 `f3bff693…2039019`. The output is
`p1-catalog-admission.json`, sha256 `dd6c811f…35ffb0df`. The script exited 0.

| Field | 9.2.0 | 9.1.2 |
|---|---|---|
| admission_status | **admitted_exact** | partially_admitted |
| version_status | reviewed_exact | reviewed_exact |
| upstream_advertised_tool_count | 80 | 80 |
| reviewed_policy_entry_count | 77 | 77 |
| catalog_comparison_status | exact | partial |
| exposed tools | 28 | 27 (no `ha_search`) |
| held | `ha_get_operation_status` | `ha_get_operation_status` |
| quarantined | none | `ha_search` |
| peer tool calls | 0 | 0 |

The gate checks:

- 9.2.0 is admitted exact;
- 28 tools are exposed, which are the 27 Engineering exposes on the reviewed
  8.5.0 catalog (`ha_search` among them) plus `ha_config_get_yaml`;
- `ha_list_files` and `ha_read_file` are withheld (`prohibited_withheld`);
- `ha_get_operation_status` is held.

**All passed: SR-b may start.** Section 5 records the earlier blocker.

### SR harness: the health step and the 8.5.0 control gate

The 20:17 and 22:26 rulings read the control gate from Engineering
`get_server_health`. The SR harness did not call it, so `reads` now does,
before any read, in each setup:

- It records, from `data.upstream_read_gateway`:
  - `upstream_advertised_tool_count`;
  - `reviewed_policy_entry_count`;
  - `version_status`;
  - `admission_status`;
  - `catalog_comparison_status`;
  - `upstream_server_version`;
  - `reviewed_upstream_version`.
- It records the scalar fields of
  `data.governance.operational_administration.lifecycle_provider`.
- It records the exchange sha256, the result class, whether
  `response_completeness.truncated` was set, and the upstream traffic caused by
  the call.
- In the 8.5.0 setup it checks 80, 77, `reviewed_exact` and `admitted_exact`.
  - Any difference stops the run before the reads, with
    `control_gate_failed`, and cleanup runs. A refused or truncated health
    response counts as a difference.
  - The 9.2.0 values are recorded and do not gate.
- Each `ha_search` receipt records `search_descriptor_variant` and
  `warning_codes` from the result metadata, or none.

Tests: `tests/test_hamcp371_live_replay_local.py`, 47 tests, together with the
two lane modules it reuses: 198 OK.
- Fast tier, log `.artifacts/d6impl/fast2/fast.txt`, unit
  `hamcp371-sr-fast-2b`, 23:04 to 23:06 CST: **passed**, 395 tests, rc 0.
  - Targets: the four D6 modules plus the SR module and the two lane
    modules. The 32-path declaration is the same as the earlier Fast runs.
  - Launch 1 (`hamcp371-sr-fast-2`) failed before any check ran: the user
    unit's PATH did not include `~/.local/bin`, so `pwsh` was not found
    (rc 127). Relaunch 1 set PATH explicitly. Its log is kept as
    `fast-launch1-pwsh-not-on-path.txt`.
- Scan: one hit, `token_value`, at `tests/test_hamcp371_live_replay_local.py:498`.
  It is the synthetic `SUPERVISOR_TOKEN` value of the guard test, committed
  at `8b3cadfa`. This change does not touch it. Leak check: PASS.

## 7. SR-b launch 1: failed after start, not re-run

Rulings: architect rulings 2026-11-15 22:26 CST (SR-b GO if P1 passes; a
failure after start is reported, never re-run).

- Unit `hamcp371-sr-b-1`, head `a461dfc7`, image `hamcp371-sr-engineering:a518934a54e0`
  (`engineering-image.json` as committed at `a461dfc7`; section 9). Heavy gate at start: 12683 MB
  available, load 0.27, 58G root free, no other heavy jobs.
- Started 23:09:42 CST, ended 23:10:14 CST (31.5 s). Run id
  `0947e551778a497ea19e163a7b9541d2`.
- Result **WITHHELD**, failure `blueprint_get_refused`, type `Refusal`.
  Cleanup clean: containers and volumes absent, no Docker network created.
- Evidence: `run-b-1/` (37 files, `SHA256SUMS` verified; `receipt.json`
  sha256 `ccb0d19f…88d93c7d`) and `launches.jsonl`. The receipt's secret
  scan covered 35 files and removed none; the leak check passed and the
  scan of the 38 committed files had 0 hits.

### What ran

Only the 9.2.0 setup. The run stopped at r12 of that setup, so the 8.5.0
control setup did not start.

- The component phase passed: `ha_mcp_tools` loaded, the check exited 0.
- Engineering started. The recording proxy saw `GET /` (404), `initialize`
  (200), `notifications/initialized` (202) and `tools/list` (200). No tool
  call reached the peer.
- `get_server_health` returned `CoreCapabilityUnavailable`
  (`error:provider_unavailable`) with no `upstream_read_gateway` or
  lifecycle provider fields. Not truncated. As the candidate setup, it did
  not gate.
- r01 to r11 each returned `CoreRequirementUnmapped`
  (`error:unsupported_operation`, reason `core_requirement_unmapped`), with no
  upstream traffic.
- r12 `ha_get_blueprint` returned the same refusal, and the harness stops on
  a refused blueprint get: `blueprint_get_refused`.

### Engineering's log

- `core_authority_verification_failed`: reason `core_authority_incompatible`,
  detail `unavailable`, recovery state `incompatible`, 0 of 2 confirmations,
  1 consecutive failure.
- `core_catalog_tools_withdrawn`: 28 tools.

### Cause (from source, not tested)

- The lane's Core is `ghcr.io/home-assistant/home-assistant@sha256:c6c1ca99…`,
  label `io.hass.version` 2026.11.0.
- `ha_core_readmission/runtime.py:931-943`: `core_authority_incompatible` is
  set when Engineering observed the exact Core completely, with identity
  agreeing, but no reviewed contract admits it; the detail is the generation
  disposition, here `unavailable`.
- `ha_core_readmission/profiles.py:80-106` (`SUPPORTED_CORE_RELEASES`) and
  `compiled_exact_authority` (`profiles.py:1364-1372`, no release row, no authority): the built-in compiled
  Core authority covers 2026.7.2, 2026.8.0, 2026.8.1, 2026.9.0 and 2026.9.1
  only. The Core 2026.11.0 profiles are signed-only: dormant until an
  owner-signed registry sequence names them.
- The lane runs Engineering with `upstream_trust_registry_enabled: false`
  (ci.yml's synthetic options) and no signed registry. Under that posture
  Core 2026.11.0 has no authority, so the 28 Core-backed catalog tools are
  withdrawn. A withdrawn tool is no longer a published delegated read, so
  `_gateway_core_route` falls back to the static route table
  (`routing.py:79-87`), which has no mapping for it, and the call is refused
  with `CoreRequirementUnmapped` before Core acquisition or any provider call
  (`routing.py:1073-1092`).
- The same Core image serves the 8.5.0 control setup, so its control gate
  could not have passed either. This is an inference from the same source.
  It was not run.
- P1 (sections 5 and 6) did not show this: it runs the gateway against the
  saved catalogs offline, without the Core runtime.

### Not exercised

The 8.5.0 control setup and its gate, the 9.2.0 gateway and lifecycle
provider values, the r13 and later reads, including the `ha_search` variant
record.

### Options for a ruling (none taken)

- Run the lane's Core at a release the built-in authority admits. Of those,
  only 2026.7.2 (`sha256:14769243…48097b`, the `SUPPORTED_CORE_RELEASES`
  digest) is cached locally. Whether component 2.2.1 and 9.2.0 load on it
  is not known.
- Give the lane a signed registry sequence that names Core 2026.11.0. This
  touches the signed-registry configuration and needs its own ruling.
- Any re-run is a new launch under a new ruling.

## 8. Core registry seed: seeding (a)

Ruling: architect ruling 2026-11-16 06:05 CST, seeding (a), after the
step-2 stop at `49ace557`. D7 (the committed key
`docs/evidence/hamcp222/core-public-key.txt`, sha256 `9b27fb7f…cc9f42`) and
the registry blob verification were accepted.

`core_registry_seed.py` (commit `49555f68`) runs Engineering's own
`CoreReleaseRegistry.refresh()` once, offline, in a temporary directory
whose `data/` stands for the lane's `/data`:

- The fetcher is local and in-process. It accepts only Engineering's Core
  registry URL and returns the exact blob
  `upstream-trust/ha-core-release-registry.json` at `917bfa2a`
  (`git cat-file`; blob `6d91c9b2…`, sha256 `e792670d…db27b092`,
  408,173 bytes). It was called once.
- The key is the committed public key above.
- `refresh()` returned True and wrote the cache and its `.lifecycle`
  witness under the lane's names. Both were copied unchanged into
  `core-registry-seed/`; neither was written or edited by hand.

| File (lane path) | sha256 | Bytes |
|---|---|---|
| `/data/ha-core-release-registry-cache.json` | `622e29e0f285d3bd45ab488f70f492642365b319d5ba76a70dc43a68826082df` | 408,213 |
| `/data/ha-core-release-registry-cache.json.lifecycle` | `f8c0918fb763fed3d5e62bae7ca82d6bec2e1e772a6eea64ab90793753577b88` | 141 |
| `seed-record.json` (the record) | `ab0d8daae9cb50f5b80313dbb3d6b7b98bf3b3fe8164211a4496355912eb4c83` | |

- Authority after the refresh: sequence 18, content digest
  `sha256:5a68d9a2…6482`, expires 2027-02-12T12:09:02Z, cache valid,
  freshness current, not surface-denied.
- The witness holds only `schema_version`, `state` and
  `authority_journal_sha256`. It binds no path or host value.
- The harness delivers both files into Engineering's `/data` with the
  options file, in one `docker cp` tar, as uid 0, mode 0600.
- **Known difference from production:** same verification and write path,
  local transport instead of GitHub.

Tests: `tests/test_hamcp371_core_registry_seed.py` (6, OK) and the harness
tests (50, OK). The Fast tier ran at `49555f68`.

## 9. The local Engineering image for SR-b launch 2

`engineering-image.json` now records the image rebuilt from `49555f68`, the
same way as `291b2cb6` (section 3): committed-tree tar context, heavy gate,
local tag only, never pushed. Commit `13f8587b`.

| Field | Value |
|---|---|
| Tag | `hamcp371-sr-engineering:49555f68caff` (local only, never pushed) |
| Image Id | `sha256:2397b9da0b1334d7945b37b9daecdd35c5bd6b4b3b814c2b74ac7ee8962cb532` |
| Revision | `49555f68caff54ecb44491b34c9dcb129ed7fc1b` |
| Tree (`hass_mcp_engineering_beta`) | `060d5d9997fd8b67461217c585574449830637e9` |
| Build-context sha256 | `ac7497f2a5211f7255f6ba9230546eafd44c909a7c263f8e138885d27855bc01` (412 files, 12,963,840 bytes) |
| Version label | `2.4.2-beta.8` |
| Size | 316,364,485 bytes |

- The build ran as unit `hamcp371-sr-build-3` from 06:11:34 to 06:11:47 CST
  on 2026-11-16 (12.1 s). Heavy gate: 12,765 MB available, load 0.03, 58 GB
  root free, no other heavy job.
- The context includes the D6 `ha_search` pair commit `7338723e`: the
  Engineering tree has been `060d5d99` since that commit. That is also why
  the context sha256 equals the launch-1 image's. Only the revision and
  build-time arguments differ.

Image history:

| Use | Tag | Image Id | Record commit |
|---|---|---|---|
| SR-a (section 3) | `hamcp371-sr-engineering:8b3cadfa06f5` | `sha256:61d89783…c6e655` | `291b2cb6` |
| SR-b launch 1 (section 7) | `hamcp371-sr-engineering:a518934a54e0` | `sha256:b95ab1f23253f94e1209eaf39799fa1afa62ca2d540fb2b9b70e45a0fef96d13` | `a461dfc7` |
| SR-b launch 2 (section 11) | `hamcp371-sr-engineering:49555f68caff` | `sha256:2397b9da0b1334d7945b37b9daecdd35c5bd6b4b3b814c2b74ac7ee8962cb532` | `13f8587b` |

## 10. P3 (amended): Core 2026.11.0 authority from the seed — PASS

Ruling: architect ruling 2026-11-16 06:05 CST, P3 (amended). Script
`p3_core_authority.py`; result `p3-core-authority.json` (commit
`6b542391`, evaluated 2026-11-16T12:15:13Z at head `13f8587b`, tree
`060d5d99`).

- The seed files were delivered as the lane delivers them: the harness's
  `options_archive()` tar, extracted into `data/` under the final names,
  with nothing renamed. The witness survived the copy.
- Engineering's `CoreReleaseRegistry` at the branch head, the committed key,
  and a fetcher that always fails (`aiohttp.ClientConnectionError`).
- At load and again after one failed `refresh()`: sequence 18, content
  digest `sha256:5a68d9a2…6482`, expires 2027-02-12T12:09:02Z, cache valid,
  positive authority current, not surface-denied, not revoked.
- Entry `ha-core-2026.11.0-reviewed-v1`: image index
  `sha256:c6c1ca9931036bf3a2c4cc088d8db25dfea3b33935a5ecc83c40349462e2f69e`,
  source commit `74cf5a49c63ed8d4d87717844bf14f79c28c9cea`, probe profile
  `core-probes-child-device-name-parts-v1`, 38 capabilities. It equals the
  blob's sequence-18 entry.
- 38 selections, all `verified_compatibility` / `positive`.
- `refresh()` returned False after one fetch call, reason
  `registry_unavailable`. The files were unchanged (cache `622e29e0…`,
  witness `f8c0918f…`, both 0600).

Tests: `tests/test_hamcp371_p3_core_authority.py` (3, OK). These cover the
PASS case, a seed without its witness (FAIL: invalid cache,
surface-denied), and the fetcher.

## 11. SR-b launch 2: PASS

Ruling: architect ruling 2026-11-16 06:05 CST, step 5 (authorized once).

- Unit `hamcp371-sr-b-2`, harness `6b542391`, image
  `hamcp371-sr-engineering:49555f68caff` (section 9), Core seed per
  section 8. Heavy gate at start: 12,704 MB available, load 0.09, 58 GB
  root free, no other heavy job.
- Started 06:16:35 CST, ended 06:17:48 CST on 2026-11-16 (72.6 s). Run id
  `7dbf310e45d745ccbc8ce761aca4fcb9`.
- Result **PASS**, no failure. Cleanup clean for both setups.
- Core image `ghcr.io/home-assistant/home-assistant@sha256:c6c1ca99…`
  (2026.11.0) for both setups.
- Evidence: `run-b-2/` (768 files, `SHA256SUMS` verified;
  `receipt.json` sha256
  `cb01661a395d9da36eb041dc23457ecc513e5f3f5888c0983bc6cc97d56269f0`,
  `SHA256SUMS` sha256 `9b891e2c…a7382`) and `launches.jsonl` (5 lines).
  The receipt's secret scan covered 766 files with 8 needles and removed
  none. The leak check passed.
- The pattern scan of the 768 committed files reported 12
  `long_base64url` hits, 6 in each setup's `out/reads/_health.json`. All
  12 are the same 86-character value of `historical_integrity`:
  the concatenation of `"validated_at_startup_or_explicit_deep_audit_and_when_"`
  and `"record_becomes_authority_relevant"`,
  a string constant in
  `hass_mcp_engineering_beta/ha_mcp_engineering/governance/service.py:18066-18067`.
  None is a secret. The scanner was not changed.

| | 8.5.0 control | 9.2.0 candidate |
|---|---|---|
| Control gate | passed: advertised 80, reviewed 77, `reviewed_exact`, `admitted_exact`; differ `[]` | not gated (candidate) |
| Upstream read gateway | `admitted_exact`, catalog `exact`, reviewed 77, advertised 80, upstream 8.5.0, `reviewed_exact` | `admitted_exact`, catalog `exact`, reviewed 77, advertised 80, upstream 9.2.0, `reviewed_exact` |
| Reads | 27: 26 success, 1 `error:provider_error` | 27: 26 success, 1 `error:provider_error` |
| r12 `ha_get_blueprint` | success | success |
| r25 `ha_search` | success | success, `search_descriptor_variant` `unified` |
| r27 `ha_get_app` | `error:provider_error`; note "not replayable in the local lane (no Supervisor)", confirmed by the response | same |
| 9.2.0 fields | not applicable | recorded for 24 reads: `fields_withheld` 18 (r20) and 13 (r26); `fields_dropped_by_policy` 1 (r16, r17, r18); all others 0 |
| Peer catalog | 80 tools; `ha_config_get_yaml` present (not required) | 80 tools; `ha_config_get_yaml` present (required) |
| Lifecycle provider | `upstream_operational_lifecycle`, configured, fallback policy `none`, fallback 0, operational status `unknown` | same |

Core authority in both health records:

- Disposition `admitted_compatible`, compatible 38, held 0, quarantined
  0, fallback 0, observed Core 2026.11.0, identity agreement true.
- Release registry: enabled, registry id accepted, sequence 18, cache valid,
  freshness current, refresh failed, last failure `registry_unavailable`
  (no egress, as expected), not surface-denied, 3 retained revocations.

### D8 entries for run-b-2

`d8_gitattributes.py run-b-2 --base e741f2ed --write` appended 172
exact-path `-text -whitespace` entries to `.gitattributes`. They sit under
a comment naming `run-b-2` and its `SHA256SUMS`. Each file is listed in
`run-b-2/SHA256SUMS`, matches its listed sha256, and fails
`git diff --check`. No globs.

- 170 recorded proxy responses, 85 in each of `8.5.0/out/proxy/` and
  `9.2.0/out/proxy/`. Each directory has 115 `.response` files; the 30 not
  flagged are `000001`, `000003`, `000006`, and `000009` to `000113` in
  steps of 4 (the same numbers in both setups). The flagged ones are
  server-sent-event bodies whose lines end in CRLF
  (`event: message\r\n`). The exact list is the run-b-2 block in
  `.gitattributes`.
- `8.5.0/peer.log` and `9.2.0/peer.log`.

All staged blobs equal the files' bytes; the base `.gitattributes` has no
line-ending normalization that applies to them.

## 12. P4: offline parity of SR-b launch 2 (ruling 2026-11-16 06:35 CST)

`p4_parity.py` builds `p4-parity.json` and `p4-parity.md` from the committed
`run-b-2` files only; nothing was launched. Each read file, recorded
upstream response and `peer.json` is checked against `run-b-2/SHA256SUMS`
and the receipt before use. Tests: `tests/test_hamcp371_p4_parity.py`,
8 OK. Result: **all_explained**.

- Health record (9.2.0): the three exposed-count fields are all 28, as the
  P1 re-run required. The record carries no names (`exposed_tools` is `[]`,
  the projection is bounded). The names were recovered by running
  Engineering's gateway in-process on the committed `peer.json`. That step
  runs Engineering code and is not receipts-only. On both setups the 28
  names equal P1's (including `ha_config_get_yaml`), the fingerprint
  matches the lane, and no peer tool was called.
- Five reads are flagged. Each cites its ruling, and each upstream
  condition the citation depends on is checked against the recorded proxy
  responses:
  - r08-groups: `data.groups[].all` (not sent by 9.2.0).
  - r10-template: `data.evaluation_time` and `data.request_id` (not sent
    by 9.2.0).
  - r18-skill-guide: `data.skill_content_hint` (not sent by 9.2.0). The
    9.2.0 `content` is 23,227 characters upstream and 20,014 in the public
    result, with the "safely bounded" warning. Size is not a P4 flag
    criterion; it is disclosed.
  - r20-system-health: 18 withheld on 9.2.0 (D5).
  - r26-integrations: 13 withheld on 9.2.0 (D5 and the S7 `_ENTRY`
    allowlist).
  - The withheld counts equal Engineering's own withheld names.
- No read is unexplained.

## 13. S14: Full tier failed after start; Evidence not launched

The Full tier ran once on head `9e6f8063`.

- Command: `check.ps1 -Tier Full -Parallel 4 -BaseRef origin/main`, with
  the 32-path `-AuthorizedProtectedPath` declaration. A script checked the
  declaration against `git diff --name-only origin/main...HEAD` before the
  launch; it is the same as the D6 Fast run's.
- Base: `origin/main` at `e741f2ed`, re-fetched before the launch and
  unchanged.
- Unit: `hamcp371-s14-full` (MemoryMax=3G, OOMPolicy=continue,
  RuntimeMaxSec=5400), from 06:48:26 to 07:49:41 CST on 2026-11-16, rc 1.
  Memory peak 2.5G.
- Heavy gate at launch: 12,800 MB available, load 0.18, 58 GB root free,
  no other heavy job and no containers.
- Logs: `.artifacts/s14/full.txt` and
  `.artifacts/unit-test-shards/latest/` in the worktree. Committed here:
  `s14/full-unit-shards-summary.json` (the sharded summary) and
  `s14/full-failures.txt` (the failure blocks and the steps after the
  suite). Worktree paths are shortened to `<W>/`.

| Step | Result |
|---|---|
| Base reference, protected-path scope, scratch Git discovery, compile | passed |
| Unit suite, 11 shards (4 workers; shard 10 serial) | **failed**: shards 3, 5, 7 and 9 |
| Add-on metadata | **failed**: "Beta version was not bumped above the deployed/base version" |
| YAML (133 files), pip check, secret-pattern scan, PowerShell syntax, whitespace (four checks) | passed |

Unit-suite failures. All are in this branch's tests or in tests that this
branch's changes trip; Fast ran only targeted modules and did not reach
them.

1. Shard 3: `test_rc2dev9_upstream_admission` ContractFamilyTests
   `test_all_reviewed_versions_have_exact_builtin_attestations`. The
   branch's builtin attestations include 9.2.0, but the test's fixed set
   of reviewed versions does not.
2. Shard 9: `test_hamcp319_endpoint_selector` Step0CorpusTests
   `test_step0_corpus_has_no_strict_value_outside_hamcp319_tests`. The
   strict `/mcp/catalog/full` URL literal at
   `tests/test_hamcp371_live_replay_local.py:486` is outside the hamcp319
   tests.
3. Shards 7 and 9: `ProcessStateLeak` at tearDownModule in
   `test_hamcp371_blueprint_9_2` (0 to 27 entries),
   `test_hamcp371_p4_parity` (0 to 28 entries) and
   `test_hamcp371_overview_search_9_2` (0 to 27 entries). The leaked
   state is `ha_mcp_engineering.capabilities._DYNAMIC_UPSTREAM_CAPABILITIES`
   and `_UPSTREAM_READ_GATEWAY_SUMMARY`. Class or module fixtures registered
   the gateway's dynamic capabilities and did not restore them. The
   isolation harness restored them for later modules. For P4 the fixture
   is `setUpClass`, which calls `build()`, and `build()` runs the gateway.
4. Shard 5: all 1,429 tests passed, but the shard exited 1 on 28 scratch
   leaks under `.artifacts/tmp`, all created by
   `tests/test_hamcp371_live_replay_local.py`: `run_reads` 16, `setUp` 3,
   `catalog` 3, `repository` 3, and one each from three tests.

Add-on metadata: `hass_mcp_engineering_beta/config.yaml` stays at
`2.4.2-beta.8`, as on main, until S16 (ruling 2026-11-14 16:15).
`validate_addon_metadata.py` runs unconditionally in both Full and Evidence
(`scripts/check.ps1:1108`). So neither tier can pass on this branch before
the version is set.

Evidence was not launched. It runs the same unit suite and the same
metadata step on the same head, so its result is already known. Launching
it would spend the one authorized Evidence launch without producing a
record of a review candidate. The ruling's rule for a failure after start
is followed: the Full result is reported and not re-run. Fixes, a new
head, and new Full and Evidence runs need a ruling.

The tiers ran at `9e6f8063`. The commit that records them changes only
`docs/evidence/hamcp371/sr/` (this README and `s14/`).

## 14. T1: the four S14 test fixes (ruling 2026-11-16 08:18 CST)

All four are in `tests/` and are made in one commit. None changes an
existing expectation or weakens an assertion. The guard
`tests/test_hamcp319_endpoint_selector.py` is not edited.

1. `test_rc2dev9_upstream_admission`: 9.2.0 is added, under the T129 rules,
   to the expected set of builtin attestations and to `tool_for`'s version
   list. Its tool now comes from the S8 capture
   `docs/evidence/upstream-read-compatibility/ha-mcp-9.2.0.json` (`349d58e9`).
   The attestation was reviewed in SB (`e6474aad`; source tag `v9.2.0`,
   `51e3aac7`). Each edit carries a comment that names its source.
   **The earlier T129 sweep missed this module.** That sweep ran at
   `34abf281` (s10 README, section T129). The builtin attestation file had no
   9.2.0 entry at that commit; SB added it later, in `e6474aad`. So the module
   still passed in the sweep and first failed in S14.
2. `test_hamcp371_live_replay_local` `test_upstream_url_forms`: the
   expectation now ends in `sr.STRICT_SUFFIX`, which it imports from the
   harness (`scripts/hamcp371_live_replay_local.py`, the module that owns
   the suffix) instead of spelling the strict literal. The rest of the
   expected URL, `http://127.0.0.1:18086/synthetic-upstream-secret/mcp`, is
   spelled out as before; it is the 8.5.0 legacy form.
3. `test_hamcp371_blueprint_9_2`, `test_hamcp371_overview_search_9_2` and
   `test_hamcp371_p4_parity`:
   - every `setUpClass` that builds a gateway first calls the suite's own
     `restore_after_class` (`tests/support/process_isolation.py`);
   - every test that builds one in its body restores the state with
     `addCleanup`;
   - the proof: `setUpModule` captures the isolation guard's probes, and
     `tearDownModule` fails if they differ when the module ends. The probes
     cover the capability catalog, the gateway summary and the loggers that
     P4 also changed.
4. `test_hamcp371_live_replay_local` scratch:
   - each scratch directory now comes from `scratch(self)`, which registers
     `shutil.rmtree` with `addCleanup`, so it is removed whether the test
     passes or fails;
   - the old `exchange` cleanup is replaced; it skipped `rmdir()` when the
     directory was empty;
   - the proof: `setUpModule` gives the module a private default temp parent,
     and `tearDownModule` fails unless the parent is empty, then removes it.

Both proofs were shown to fail on a leak, by in-process patches run outside
the committed tests (scripts under `.artifacts/t1/`, not committed):
- with `restore_after_class` made a no-op, each of the three modules fails
  at `tearDownModule` with its "left process-wide state" message;
- with `scratch` made to skip its cleanup, `test_hamcp371_live_replay_local`
  fails at `tearDownModule`, with 31 directories left.

## 15. V1 and P4-B (ruling 2026-11-16 08:18 CST)

### V1: version declaration

- Local `release/2.7.0` is at `b7c7cd75`, one commit on main `e741f2ed`. It
  declares Engineering `2.4.2-beta.9` (the 2.7.0 build), with the same file
  set as `5a053a5c`. The rollback target is unchanged; S16 decides it.
- Fast on `release/2.7.0`, with `-BaseRef origin/main`, passed: 229 tests
  OK, and "Metadata validation passed."
- `release/2.7.0` is merged into this branch at `1cc158cc`, as an exact
  union. The only file changed on both sides is
  `tests/test_hamcp319_91_lane_runners.py`. The release side changes line
  63, `LANE_VERSION`; this branch changes lines 99-826. The merged file
  differs from each parent by exactly the other side's change. After the
  merge the module passed, 100 tests OK.
- From here, every tier on this branch uses `-BaseRef release/2.7.0`.
- V1 corrects the rule of 2026-11-14 16:15 that the version is set only at
  S16. The `2.4.2-beta.8` labels in sections 3, 9 and 13 record the version
  of the runs made at those heads; they are not changed.

### P4-B: the withheld names on r20 and r26

The table is in `p4b-withheld-names.md` (and `.json`). It was generated by
`p4b_withheld_names.py` from the committed `run-b-2` receipts. Run with no
arguments, the script compares a fresh build with the committed files and
exits 0 when they match.

- The table covers 18 names for r20 and 13 for r26. Each count equals
  P4's `outside_rebuild_contract` count and Engineering's `fields_withheld`.
- No value is printed. The leak check passed (`RESULT=PASS`) and the scan
  found 0 hits.
- Marked as able to carry a credential, a URL with userinfo, a token, a
  hostname, an IP address or a file path:
  - r20: `homeassistant.config_dir`, `manage_url` (3 paths), `message` and
    `network.error`;
  - r26: `error_reason_translation_placeholders`, `reason` and `unique_id`.
- The 8.5.0 public result showed only two of the names: r26's `loaded` and
  `setup_error`, at `$.data.state_summary.*`. The 9.2.0 public result shows
  them too.
  - Those keys are counts that Engineering computes itself from the entries'
    states (`upstream_platform_reads.py`, `state_summary`). The upstream
    values are still withheld.
- The table goes beyond the ruling in two ways: it adds the generator script
  and a column for the 9.2.0 public result.
- *Observed* is only a pattern match over the lane's values, from a
  disposable Core. It is no bound on what a household install sends.
  - `manage_url` is observed as `file_path` because Core's system health
    gives relative URLs.
- `homeassistant.user` is an OS account name. That is not one of the listed
  categories, so the name is noted but not marked.
- Admission of these names is for the architect to rule.
