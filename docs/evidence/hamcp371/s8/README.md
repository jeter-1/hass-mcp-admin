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

Observations, not admission evidence:

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
