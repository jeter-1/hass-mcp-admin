# HAMCP-371 SR-a: harness, local Engineering image, 2.2.1 tree

Ruling: architect ruling 2026-11-15 19:05 CST (SR GO, part SR-a), under the SR
contract of 2026-11-15 17:50 CST. SR-a prepares the replay; it calls no read.
SR-b (the replay of the 27 admitted reads) has not run.

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

- `tests/test_hamcp371_live_replay_local.py` has 40 offline tests. They use
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
| Version label | `2.4.2-beta.8` (from the branch `config.yaml`, as on main; 2.4.2-beta.9 is set only at S14) |
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

- SR-b: the replay of the 27 reads, both setups. It waits for the
  architect's go and the budget rule.
- Full and Evidence tiers.
- CI: there was no push.
- S14 and later.
