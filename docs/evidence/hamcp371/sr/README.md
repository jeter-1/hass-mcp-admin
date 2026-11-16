# HAMCP-371 SR-a, P1 and D6: harness, local Engineering image, 2.2.1 tree, catalog admission

Ruling: architect ruling 2026-11-15 19:05 CST (SR GO, part SR-a), under the SR
contract of 2026-11-15 17:50 CST. SR-a prepares the replay; it calls no read.
SR-b (the replay of the 27 admitted reads) has not run.

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
  After D6 the P1 re-run passed (section 6). SR-b is recorded separately.
- Full and Evidence tiers.
- CI: there was no push.
- S14 and later.

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

