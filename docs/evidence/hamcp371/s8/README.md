# HAMCP-371 S8: 9.2.0 error contract (N31) and the 9.2.0 rows

## N31: the review error probes on a Core with no seeded object

Ruling N31 (a), 2026-11-15 06:40 CST: send the review script's four error
probes (`scripts/review_upstream_read_release.py` `ERROR_PROBES`) to 9.1.2 and
9.2.0 before any object is seeded, and proceed with S8 only if every probe on
both versions reproduces the bound 9.1.2 shapes and the aggregate
`4ed5b1b4c0aaad0036b4625206e96739b40fc83ec1c944754fef1ed667a10437`.

### Run

- One profile A launch, `probes-1`. Harness commit `da6e6b70` (the `probes`
  lane command: the seed's `access` phase only, then the `review_probes`
  group, one peer at a time).
- Launched through the heavy gate as unit `hamcp371-s8-probes-1` at
  2026-11-15 07:05:48 CST. Admission: no other heavy job, 12.5 GB available,
  load 0.25, 60.4 GB root free. Gate receipt: `gate/probes-1.txt`.
- Ran 07:05:49 to 07:06:10 CST (21.3 s). Result `PASS`, cleanup proven.
  Launch log line: `launches.jsonl`.
- Core 2026.11.0, by digest `sha256:c6c1ca99…`. Peers ha-mcp 9.1.2
  (`sha256:381b1e01…`) and 9.2.0, by digest from the local cache, no pull.
  The full digests are in `probes-1/receipt.json`.
- D1 production flags on each peer, checked on the container environment and
  in the raw catalogue.
- Loopback only: no interface but `lo` and no IPv4 route, seen from Core and
  from the holder, before the seed and before each capture.
- Seed: the four access steps only (`onboard`, `ready`,
  `administrator_token`, `ordinary_user_token`), all `ok`. No registry object,
  helper, automation or other object was created, so Core held no automation
  when `missing_automation` was sent.
- 8.5.0 was left out: the lane's versions are 9.1.2 and 9.2.0, and 8.5.0
  would need code beyond the call set (ruling: do not extend the lane for it).

### Result

`compare_probes.py` reads only committed files and uses the review script's
own `error_evidence`. Its output is `comparison.txt` (exit 0).

| Probe | Code | Fingerprint | 9.1.2 | 9.2.0 |
|---|---|---|---|---|
| invalid_search | VALIDATION_FAILED | `fc0f1e8b…` | match | match |
| missing_automation | RESOURCE_NOT_FOUND | `8053f516…` | match | match |
| missing_registry_entity | SERVICE_CALL_FAILED | `3e1148ad…` | match | match |
| missing_state | ENTITY_NOT_FOUND | `8a705d92…` | match | match |
| aggregate | | `4ed5b1b4…` | match | match |

Each match is the whole bound record (`is_error`, `structured_code`,
`shape_fingerprint`), not only the fingerprint.

### missing_automation varies with Core content

`missing_automation`'s shape depends on what Core holds. Its payload carries
`available_automation_ids`, a list of the automations that exist. The shape
projection keeps list items, so one automation changes the fingerprint:

- `probes-1`, no automation: `available_automation_ids: []`, fingerprint
  `8053f516…`, the bound value.
- S6 `run-a-1` (both versions), `run-b-9.1.2-1` and `run-b-9.2.0-2`, seeded
  with the one automation `automation.hamcp371_automation`: fingerprint
  `25357453d3b95ffe379819f2be6eea23bce77a65dcc1d8b8bebc66eab4505126` on every
  version. `comparison.txt` shows these four from the committed S6 call files.

So an error-shape capture reproduces the bound aggregate only on a Core with
no automation. A review-time capture against a Core that has automations gives
a different `missing_automation` fingerprint and a different aggregate, and no
compiled adapter would match it. This affects review-time captures only;
Engineering does not send these probes at runtime (see the S8 report, Q6).

Q6, ruled 2026-11-15 07:40 CST: the error_contract recorded on failed live
calls (`upstream_read_gateway.py:6923-6943`, `:4744`) is logged only and not
compared. On a Core with automations it can show a `25357453`-class value,
with no effect on admission. Admission compares two registry values
(`ha_mcp_readmission/ha_mcp.py:872-879`, `upstream_tool_policy.py:2864-2866`,
`signed_registry/models.py:651-654`).

## Q7: the 9.2.0 App image reads and the revision gate

Ruling Q7 (a), 2026-11-15 07:40 CST: read
`ghcr.io/homeassistant-ai/ha-mcp-addon-{amd64,aarch64}:9.2.0` anonymously.
Require the `org.opencontainers.image.revision` label on both architectures to
equal `51e3aac788482a081807591cd5d58d07dc4e540b`. Stop if it differs or is
missing.

### Reads

- Ran 2026-11-15 07:38:34 to 07:38:35 CST (`raw/started.txt`,
  `raw/ended.txt`), with an empty `DOCKER_CONFIG`. No credentials were used,
  nothing was pulled and nothing was pushed.
- Commands, for each `$arch` in `amd64 aarch64`, prefixed with
  `env -i PATH=/usr/bin:/bin HOME=$HOME DOCKER_CONFIG=<empty dir> timeout 120`:
  - `docker buildx imagetools inspect ghcr.io/homeassistant-ai/ha-mcp-addon-$arch:9.2.0`
    (`inspect-tag-$arch.txt`), and the same with `--raw`
    (`index-$arch.raw.json`);
  - `docker buildx imagetools inspect ghcr.io/homeassistant-ai/ha-mcp-addon-$arch@<image manifest digest> --format '{{json .Image}}'`
    (`config-$arch.json`), and the same with `--raw`
    (`manifest-$arch.raw.json`);
  - `docker buildx imagetools inspect ghcr.io/homeassistant-ai/ha-mcp-addon-$arch@<index digest> --format '{{json .Provenance}}'`
    (`provenance-$arch.json`).
- Every raw file and its empty `.err` file is committed under `raw/` and listed
  in `SHA256SUMS`. `check_app_reads.py` reads only those files. Its output is
  `check-output.txt` (exit 1, the gate fails).
- Secret checks before the copy: the leak check passed on every file.
  `s6/comparison/scan.py` over the 20 read files gave 2 hits, one
  `long_base64url` in each `provenance-$arch.json`. Each is the base64
  Dockerfile that BuildKit embeds (`buildkit_metadata.source.infos[0].data`).
  Decoded, it is the public 59-line Dockerfile (git blob `119a01c0…`), with no
  credential.

### Result

| | amd64 | aarch64 |
|---|---|---|
| Index digest | `sha256:e0fd8903…94fe3ba` | `sha256:54459403…9c326a` |
| Image manifest | `sha256:928c55d6…ed9244` (linux/amd64) | `sha256:de36201e…9010dc` (linux/arm64) |
| `org.opencontainers.image.revision` | absent | absent |
| Labels present | `io.hass.{arch,description,name,type,version}` only; version `9.2.0` | the same, arch `aarch64` |
| Cmd | `["python3","/start.py"]` | the same |
| SLSA `vcs:revision` | `51e3aac7…` | `51e3aac7…` |
| SLSA `vcs:source` | `https://github.com/homeassistant-ai/ha-mcp` | the same |
| Dockerfile in provenance, git blob | `119a01c0…` | `119a01c0…` |

The full digests are in `check-output.txt`. Each digest is the sha256 of the
raw bytes the registry served.

**Q7 revision gate: FAIL. The label is absent on both architectures.** Per
the ruling, the pull and the App catalogue capture were not done.

Ruled 2026-11-15 08:05 CST: the label gate was an architect error and is
withdrawn. HAMCP-351 records the 9.1.2 standalone label as `b35a3d22…`, not
the source commit `bab615df…`: upstream's label is the workflow commit, so
the gate could not pass on either image type. The reads above are accepted.
`check_app_reads.py` and `check-output.txt` stay as the record of the
withdrawn gate. The provenance gate below replaces it.

### Q7-b: the provenance gate

Ruling Q7-b (a), 2026-11-15 08:05 CST. `app-reads/provenance_gate.py` reads
the same committed raw files, after checking them against `SHA256SUMS`. Its
output is `app-reads/provenance-gate.txt` (exit 0). On both architectures it
requires:

| Gate | amd64 | aarch64 |
|---|---|---|
| Index and image manifest digests, as recorded above; the index's attestation manifest refers to that image manifest | PASS | PASS |
| SLSA `vcs:revision` `51e3aac788482a081807591cd5d58d07dc4e540b` | PASS | PASS |
| Repository `homeassistant-ai/ha-mcp` (`vcs:source`, `github_repository`, repository id `1056618941`, owner id `232192432`, the event payload's repository) | PASS | PASS |
| Builder id is run `43056483558` attempt 1 in that repository; runner environment `github-hosted` | PASS | PASS |
| `github_ref` `refs/heads/master`, the default branch named in the event payload; ref type `branch`; `github_ref_protected` `true` | PASS | PASS |
| `targetPlatform` `linux/amd64` / `linux/arm64`; `vcs:localdir:dockerfile` `homeassistant-addon` | PASS | PASS |
| Embedded Dockerfile git blob `119a01c01755c3b681afeb415c4ebb0ebb7fbb09`, the v9.2.0 tree entry | PASS | PASS |
| Same run, attempt and workflow commit on both | PASS | |

**Q7-b provenance gate: PASS.**

Disclosed differences, not gated:

- The workflow file commit is `e664ece999585b64e936a887d1be474d8fa9988a`
  (`semver-release.yml`, job `build-addon`, `workflow_dispatch`), not the
  source commit `51e3aac7…`.
- The provenance is unsigned: a BuildKit in-toto statement read anonymously
  from the registry. This is the HAMCP-351 evidence class, which relies on
  upstream's registry write control. The default branch and the protected
  flag come from the same unsigned statement.

Negative check (not committed, run in `.artifacts/` on a copy with
`SHA256SUMS` rewritten for the changed file): changing the aarch64
`vcs:revision`, runner environment, protected flag, ref, event repository,
embedded Dockerfile or run id each made the gate exit 1 with that field
named.

Observations recorded with the withdrawn gate:

- The provenance on both architectures is an unsigned BuildKit in-toto
  statement. It names run `43056483558`, workflow
  `.github/workflows/semver-release.yml@refs/heads/master` at workflow commit
  `e664ece9…` (job `build-addon`, `workflow_dispatch`, a GitHub-hosted
  runner, protected ref). Its build arguments carry the `io.hass.*` labels and
  no `org.opencontainers.image.*` label.
- The Dockerfile embedded in that provenance has git blob id `119a01c0…`, the
  same as `homeassistant-addon/Dockerfile` in the tree of `51e3aac7`, read
  from the upstream clone's tree with lazy fetch off. The blob itself is
  absent from the partial clone, so the match is by object id only.
- The standalone 9.2.0 image (S4 `provenance-9.2.0.json`) was built by a
  different run, `43058645294`, of `release-publish.yml` at workflow commit
  `4d714830…`, from the same source commit `51e3aac7`. Its
  `org.opencontainers.image.revision` label is `4d714830…`, the workflow
  commit, not `51e3aac7`. That binding records `label_evidence` as
  `informational_only`.

### Q7-b: the App pull and the filesystem profiles

Harness commit `809e0540` (`pull --image app` and the `profiles` command of
`scripts/upstream_catalog_capture_local.py`).

Pull, 2026-11-15 08:23:07 to 08:23:11 CST, unit `hamcp371-s8-pull-app`
(admission: no other heavy job, 12.6 GB available, load 0.01, 60.4 GB root
free; `gate/pull-app-9.2.0.txt`). Result `PASS` (`pull-app-9.2.0/receipt.json`):
`docker pull --quiet --platform linux/amd64
ghcr.io/homeassistant-ai/ha-mcp-addon-amd64@sha256:928c55d6…9244`, with an
empty `DOCKER_CONFIG`, after the committed reads verified and the provenance
gate passed. Nothing was pulled by tag. The local image Id is the image
manifest digest (containerd image store); the manifest's config digest is
`sha256:9f7fe819…6867`. Root free space 60.36 GB before, 60.17 GB after.
`ha-mcp-web` is at `/app/.venv/bin/ha-mcp-web`, seen in the listing of a
created, never started, `--network none` container, which was then removed.

Profiles run `profiles-1`, 2026-11-15 08:23:44 to 08:24:24 CST, unit
`hamcp371-s8-profiles-1` (admission: no other heavy job, 12.3 GB available,
load 0.18, 60.2 GB root free; `gate/profiles-1.txt`). One peer at a time,
two captures per profile, against the repository's synthetic fixture.
Result **`WITHHELD`**, failure `fs_on_control_differs_from_hamcp351`,
cleanup proven. `profiles-1/check_profiles.py` reads only committed files;
its output is `profiles-1/check-output.txt`.

| Profile | Tools | Capture sha256 | Two captures identical |
|---|---|---|---|
| `app-9.2.0-fs-off` | 77 | `ed3d6354…`, the S4 standalone capture | yes |
| `app-9.2.0-fs-on` | 77 | `ed3d6354…` | yes |
| `standalone-9.2.0-fs-on` | 77 | `ed3d6354…` | yes |
| `control-standalone-9.1.2-fs-on` | 77 | `ce522211…` | yes |

**The filesystem flag had no effect in any profile.** The 9.1.2 control,
run with `HAMCP_ENABLE_FILESYSTEM_TOOLS=true`, returned the HAMCP-351
filesystem-off `tools/list` result exactly (77 tools), not the filesystem-on
one (82 tools). The three 9.2.0 "fs-on" profiles also returned the
filesystem-off catalogue.

Cause (read from the upstream source with lazy fetch off, at both
`bab615df` (v9.1.2) and `51e3aac7` (v9.2.0)): `HAMCP_ENABLE_FILESYSTEM_TOOLS`
is a beta sub-flag. `src/ha_mcp/config_registry.py` ("Beta sub-flags gated by
`enable_beta_features`") applies it only when `ENABLE_BETA_FEATURES` is true.
The harness's fs-on profiles set the filesystem flag without
`ENABLE_BETA_FEATURES=true`, so they did not use the HAMCP-351 profile
definition. This is a harness defect in `809e0540`. The control caught it.

What this run does establish: the amd64 App 9.2.0 image's catalogue with the
filesystem tools off is byte-identical to the standalone S4 capture
(`ed3d6354…`, two captures each). What it does not establish: any
filesystem-on catalogue, App or standalone. Per the standing relaunch rule
(a failure after start is reported, never re-run), nothing was relaunched.

HAMCP-351 does not record its peer environment in committed evidence. The
repository's 9.1 lane contract
(`scripts/exact_image_admission_91_runners.py:1803-1806`) sets
`ENABLE_TOOL_SEARCH=true`, `ENABLE_BETA_FEATURES=true`,
`HAMCP_ENABLE_FILESYSTEM_TOOLS` (the one toggle) and `READ_ONLY_MODE=false`
for the candidate. That this is what HAMCP-351 used is an inference.

Committed: `profiles-1/receipt.json`, `profiles-1/SHA256SUMS` (every file of
the run directory, computed after the run), the control's
`observation.json`, the pull receipt, both gate receipts and the launch log.
Not committed: the other captures, the observations, and the peer and
fixture logs. They stay in the ignored `.artifacts/hamcp371-s8/` and are
listed in `profiles-1/SHA256SUMS`. Secret checks before the copy: the leak
check passed on the run directory, the pull directory, both gate receipts,
both logs and the launch log; `s6/comparison/scan.py` over 25 files gave 0
hits.

### What is committed and what is not

Committed, each byte-identical to the run directory (checked against the run's
own `SHA256SUMS`): `receipt.json`, `SHA256SUMS`, the seed report, and for each
version the four probe call files, `_index.json` and `_initialize.json`. Also
the gate receipt, the launch log, `compare_probes.py` and `comparison.txt`.

Not committed: the two peer catalogues, the Core log, the two peer logs and
the empty holder log. They stay in the ignored `.artifacts/hamcp371-s8/` and
are listed with their hashes in `probes-1/SHA256SUMS`.

Secret checks before the copy:

1. The lane, while the tokens existed: exact bytes of each token and of its
   signature segment, 4 needles, 19 files, none removed (`secret_scan` in the
   receipt).
2. The leak check passed on every file before it was copied.
3. `s6/comparison/scan.py` over the whole run directory, the gate receipt and
   the launch log: 23 files, 0 hits.
